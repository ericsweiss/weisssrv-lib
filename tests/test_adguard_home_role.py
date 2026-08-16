"""Behavioural tests for adguard_home's staged-archive verification chain.

`adguard_home_archive_cache_dir` lets an operator pre-stage the release tarball
so an air-gapped or rate-limited host does not fetch it from GitHub. That path
BYPASSES `get_url`'s `checksum:`, so without a verification step the cache
directory is an unauthenticated way to put arbitrary bytes on the resolver and
run them as root.

Two gates guard it, and they are tested separately because they answer different
questions:

* The **trust boundary** — the cache directory and the files staged in it must
  be root-owned and closed to group/other writers. This runs FIRST, because a
  digest only proves the bytes match a value stored in the same directory: a
  writer who can swap the archive can swap the `checksums.txt` beside it, and
  the comparison would still pass. It also gates the explicit-pin path, whose
  target a directory writer can still redirect.
* The **verification** — the archive's sha256 must match `adguard_home_archive_sha256`
  or a line in the staged `checksums.txt`.

Both are `assert`s whose conditions are inline Jinja, so the only cheap way to
test them is the way tests/test_nextcloud_role.py tests its gate: pull the real
expressions out of the role and evaluate them against representative
`stat`/`slurp` results. The expressions are READ FROM THE ROLE, never restated,
so a regression fails these assertions rather than diverging from a copy.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

import jinja2
import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "adguard_home"
MAIN = ROLE / "tasks" / "main.yml"

CACHE_STAT = "Look for the staged-archive cache directory"
STAGED_STAT = "Look for a locally staged AdGuard Home archive"
STAGED_CHECKSUMS_STAT = "Look for a staged checksums.txt"
TRUST = "Assert the staged-archive cache is a root-owned trust boundary"
STAGED_CHECKSUMS_READ = "Read the staged checksums.txt"
VERIFY = "Verify the staged AdGuard Home archive"
DOWNLOAD = "Download AdGuard Home"
EXTRACT = "Extract AdGuard Home"

ARCH = "amd64"
GOOD = "a" * 64
OTHER = "b" * 64

# The role's condition embeds a regex (`\s+\S*...\.tar\.gz`) in a Jinja string
# literal, and Jinja's lexer unicode-escape-decodes those. `\s` is not a valid
# Python escape, so the decode warns — under Ansible too. It is the production
# behaviour, not a defect in these tests, so the noise is filtered rather than
# the regex rewritten.
pytestmark = pytest.mark.filterwarnings(
    "ignore:invalid escape sequence:DeprecationWarning"
)


def _flatten(tasks) -> list[dict]:
    """Every task in file order, descending into block/rescue/always."""
    flat: list[dict] = []
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        flat.append(task)
        for key in ("block", "rescue", "always"):
            flat += _flatten(task.get(key))
    return flat


TASKS = _flatten(yaml.safe_load(MAIN.read_text()))
NAMES = [str(t.get("name", "")) for t in TASKS]


def _task(name: str) -> dict:
    matches = [t for t in TASKS if str(t.get("name", "")) == name]
    assert len(matches) == 1, f"expected exactly one task named {name!r} in {MAIN}"
    return matches[0]


def _when(task: dict) -> list[str]:
    when = task.get("when", [])
    return [str(when)] if isinstance(when, str) else [str(c) for c in when]


def _env() -> jinja2.Environment:
    env = jinja2.Environment(undefined=jinja2.ChainableUndefined)
    env.filters["b64decode"] = lambda v: base64.b64decode(v).decode()
    # `bool` is an Ansible filter; the conditions only ever yield a real boolean
    # here, so Python's is a faithful stand-in for Ansible's string-aware one.
    env.filters["bool"] = bool
    # `search` is an Ansible test, not a stock Jinja one.
    env.tests["search"] = lambda value, pattern: re.search(pattern, str(value)) is not None
    return env


def _evaluate(*, pin: str = "", checksums: str | None = None, staged: str = GOOD) -> bool:
    """Evaluate the role's real assert condition against a staged-archive state.

    `checksums` is the plaintext of a staged checksums.txt, or None when the
    slurp never ran (the task is skipped when the pin is set, or when no
    checksums.txt is beside the archive).
    """
    env = _env()
    conditions = _task(VERIFY)["ansible.builtin.assert"]["that"]
    assert len(conditions) == 1, "the verification collapsed into several conditions"
    content = (
        jinja2.ChainableUndefined(name="content")
        if checksums is None
        else base64.b64encode(checksums.encode()).decode()
    )
    rendered = env.from_string("{{ (" + conditions[0] + ") | bool }}").render(
        adguard_home_archive_sha256=pin,
        adguard_home_arch=ARCH,
        adguard_home_staged_archive={
            "stat": {"exists": True, "checksum": staged, "path": "/srv/cache/x.tar.gz"}
        },
        adguard_home_staged_checksums_content={"content": content},
    )
    return rendered == "True"


TRUSTED_DIR = {"exists": True, "isdir": True, "uid": 0, "wgrp": False, "woth": False, "mode": "0755"}
TRUSTED_FILE = {"exists": True, "uid": 0, "wgrp": False, "woth": False, "mode": "0644"}

# A register left behind by a skipped task: `.stat` resolves to undefined, which
# is what every `| default(...)` in the trust conditions has to fall back on.
SKIPPED = {"skipped": True}


def _evaluate_trust(*, cache=None, archive=None, checksums=None) -> bool:
    """Evaluate the role's real trust-boundary conditions against a stat state.

    Each argument overrides fields on an otherwise-trusted `stat` result, or is
    the sentinel SKIPPED for a register whose task never ran. `checksums=None`
    means no checksums.txt was consulted (an explicit pin is set, or none is
    staged) — the state the role reaches on its pin path.

    `assert`'s `that` is an AND over the list, which is what `all` reproduces.
    """
    env = _env()

    def stat(override, base):
        if override is SKIPPED:
            return dict(SKIPPED)
        return {"stat": {**base, **(override or {})}}

    context = {
        "adguard_home_archive_cache_dir": "/srv/cache",
        "adguard_home_archive_cache_stat": stat(cache, TRUSTED_DIR),
        "adguard_home_staged_archive": stat(archive, TRUSTED_FILE),
        "adguard_home_staged_checksums": (
            {"stat": {"exists": False}}
            if checksums is None
            else stat(checksums, TRUSTED_FILE)
        ),
    }
    conditions = _task(TRUST)["ansible.builtin.assert"]["that"]
    return all(
        env.from_string("{{ (" + str(condition) + ") | bool }}").render(**context) == "True"
        for condition in conditions
    )


# --------------------------------------------------------------------------
# The chain is wired: each link exists and is reachable from the one before it
# --------------------------------------------------------------------------


def test_the_staged_stat_takes_a_sha256_of_the_archive() -> None:
    """`stat` computes no checksum by default, and the verification has nothing
    to compare without one."""
    stat = _task(STAGED_STAT)["ansible.builtin.stat"]
    assert stat["checksum_algorithm"] == "sha256"
    assert _task(STAGED_STAT)["register"] == "adguard_home_staged_archive"


def test_the_checksums_file_is_only_consulted_without_an_explicit_pin() -> None:
    """The pin is the stronger statement; reading checksums.txt beside it would
    let a staged file decide which digest counts."""
    when = _when(_task(STAGED_CHECKSUMS_STAT))
    assert any("adguard_home_staged_archive.stat.exists" in c for c in when)
    assert any("adguard_home_archive_sha256 | length == 0" in c for c in when)


def test_the_checksums_file_is_read_only_when_it_exists() -> None:
    read = _task(STAGED_CHECKSUMS_READ)
    assert read["register"] == "adguard_home_staged_checksums_content"
    assert "adguard_home_staged_checksums.stat.exists" in " ".join(_when(read))


def test_the_verification_runs_exactly_when_an_archive_is_staged() -> None:
    """Gated on the staged archive, not on the cache directory: a configured
    cache with nothing in it must fall through to the upstream download rather
    than fail the play."""
    assert "adguard_home_staged_archive.stat.exists" in " ".join(_when(_task(VERIFY)))


def test_the_verification_precedes_the_extract() -> None:
    """Ordering is the whole point — verifying after unpacking verifies nothing."""
    assert NAMES.index(STAGED_STAT) < NAMES.index(VERIFY) < NAMES.index(EXTRACT)


# --------------------------------------------------------------------------
# The trust boundary, which the verification's digest comparison rests on
# --------------------------------------------------------------------------


def test_the_cache_directory_is_stat_ed_for_the_trust_check() -> None:
    """The directory's own ownership and mode are half the boundary: a
    group-writable directory lets a writer replace both the archive and the
    checksums.txt that blesses it, whatever the files themselves look like."""
    task = _task(CACHE_STAT)
    assert task["ansible.builtin.stat"]["path"] == "{{ adguard_home_archive_cache_dir }}"
    assert task["register"] == "adguard_home_archive_cache_stat"
    assert "adguard_home_archive_cache_dir | length > 0" in " ".join(_when(task))


def test_the_trust_check_runs_exactly_when_an_archive_is_staged() -> None:
    """Same gate as the verification: a configured-but-empty cache must fall
    through to the upstream download rather than fail the play."""
    assert "adguard_home_staged_archive.stat.exists" in " ".join(_when(_task(TRUST)))


def test_the_trust_check_precedes_every_use_of_the_staged_bytes() -> None:
    """It gates BOTH paths, so it has to sit ahead of the slurp (an untrusted
    checksums.txt is never even read) and ahead of the digest comparison that
    the explicit pin takes."""
    assert (
        NAMES.index(CACHE_STAT)
        < NAMES.index(STAGED_STAT)
        < NAMES.index(STAGED_CHECKSUMS_STAT)
        < NAMES.index(TRUST)
        < NAMES.index(STAGED_CHECKSUMS_READ)
        < NAMES.index(VERIFY)
        < NAMES.index(EXTRACT)
    )


def test_the_trust_failure_message_names_the_requirement() -> None:
    """An operator hitting this staged the files themselves; the message has to
    state the requirement, not just that something is wrong."""
    fail_msg = _task(TRUST)["ansible.builtin.assert"]["fail_msg"]
    assert "root (uid 0)" in fail_msg
    assert "not writable by group or other" in fail_msg
    assert "adguard_home_archive_cache_dir" in fail_msg


def test_a_root_owned_cache_is_trusted() -> None:
    assert _evaluate_trust() is True
    assert _evaluate_trust(checksums={}) is True


@pytest.mark.parametrize("field", ["wgrp", "woth"])
def test_a_writable_cache_directory_is_not_trusted(field: str) -> None:
    assert _evaluate_trust(cache={field: True}) is False


@pytest.mark.parametrize("field", ["wgrp", "woth"])
def test_a_writable_staged_archive_is_not_trusted(field: str) -> None:
    assert _evaluate_trust(archive={field: True}) is False


@pytest.mark.parametrize("field", ["wgrp", "woth"])
def test_a_writable_checksums_file_is_not_trusted(field: str) -> None:
    """The checksums.txt decides which digest counts, so it is exactly as
    sensitive as the archive it blesses."""
    assert _evaluate_trust(checksums={field: True}) is False


@pytest.mark.parametrize("uid", [1000, 65534])
def test_a_non_root_owner_is_not_trusted(uid: int) -> None:
    assert _evaluate_trust(cache={"uid": uid}) is False
    assert _evaluate_trust(archive={"uid": uid}) is False
    assert _evaluate_trust(checksums={"uid": uid}) is False


def test_a_cache_path_that_is_not_a_directory_is_not_trusted() -> None:
    """`adguard_home_archive_cache_dir` pointed at a file or a dangling symlink
    is a misconfiguration, and the stat fields below it mean nothing."""
    assert _evaluate_trust(cache={"isdir": False}) is False


@pytest.mark.parametrize("name", [CACHE_STAT, STAGED_STAT, STAGED_CHECKSUMS_STAT])
def test_the_trust_stats_do_not_follow_symlinks(name: str) -> None:
    """`stat` defaults to follow: false, and that default is load-bearing here:
    following reports the TARGET's ownership while the symlink — the part an
    attacker repoints — goes unexamined. Setting follow: true would open the
    boundary, so it must stay unset."""
    assert _task(name)["ansible.builtin.stat"].get("follow", False) is False


def test_a_symlinked_cache_directory_is_not_trusted() -> None:
    """Unfollowed, a symlink stats as lrwxrwxrwx and not a directory, so the
    classic redirect-the-staging-path attack is denied by the same conditions
    rather than needing a rule of its own."""
    symlink = {"isdir": False, "mode": "0777", "wgrp": True, "woth": True}
    assert _evaluate_trust(cache=symlink) is False
    assert _evaluate_trust(archive=symlink) is False
    assert _evaluate_trust(checksums=symlink) is False


def test_an_unconsulted_checksums_file_does_not_block_the_pin_path() -> None:
    """With adguard_home_archive_sha256 set, the checksums stat is skipped and
    nothing is trusted from that file — so its absence must not fail the play."""
    assert _evaluate_trust(checksums=None) is True
    assert _evaluate_trust(checksums=SKIPPED) is True


@pytest.mark.parametrize("register", ["cache", "archive"])
def test_a_missing_stat_fails_closed(register: str) -> None:
    """The regression this guards: a reordered or renamed stat leaves the
    condition reading an undefined, which must deny rather than default open."""
    assert _evaluate_trust(**{register: SKIPPED}) is False


def test_the_upstream_download_is_skipped_only_when_a_verified_archive_is_staged() -> None:
    """The two paths are mutually exclusive, and the staged one is the branch
    the verification guards; the other carries get_url's own checksum."""
    assert "not (adguard_home_staged_archive.stat.exists" in " ".join(_when(_task(DOWNLOAD)))
    assert "checksum" in _task(DOWNLOAD)["ansible.builtin.get_url"]


def test_the_failure_message_names_both_ways_out() -> None:
    """An operator hitting this is holding an archive they staged themselves;
    the message has to say how to bless it and how to abandon it."""
    fail_msg = _task(VERIFY)["ansible.builtin.assert"]["fail_msg"]
    assert "adguard_home_archive_sha256" in fail_msg
    assert "checksums.txt" in fail_msg
    assert "Remove the archive" in fail_msg


# --------------------------------------------------------------------------
# The condition itself
# --------------------------------------------------------------------------


def test_an_explicit_pin_matching_the_staged_archive_passes() -> None:
    assert _evaluate(pin=GOOD) is True


def test_an_explicit_pin_that_does_not_match_fails() -> None:
    """The regression this guards: an archive swapped under a stale pin."""
    assert _evaluate(pin=OTHER) is False


def test_a_staged_checksums_file_naming_the_digest_passes() -> None:
    assert _evaluate(checksums=f"{GOOD}  AdGuardHome_linux_{ARCH}.tar.gz\n") is True


def test_a_checksums_line_carrying_a_path_prefix_still_passes() -> None:
    """Upstream's checksums.txt has shipped both bare and `./`-prefixed names."""
    assert _evaluate(checksums=f"{GOOD}  ./AdGuardHome_linux_{ARCH}.tar.gz\n") is True


def test_a_checksums_file_that_does_not_name_the_digest_fails() -> None:
    assert _evaluate(checksums=f"{OTHER}  AdGuardHome_linux_{ARCH}.tar.gz\n") is False


def test_a_digest_listed_against_another_architecture_fails() -> None:
    """checksums.txt lists every artefact; matching the digest alone would let
    an arm64 tarball authenticate an amd64 install."""
    assert _evaluate(checksums=f"{GOOD}  AdGuardHome_linux_arm64.tar.gz\n") is False


def test_a_digest_appearing_as_bare_prose_fails() -> None:
    """The filename term is what makes this a checksum line rather than any
    occurrence of 64 hex characters in the file."""
    assert _evaluate(checksums=f"see also {GOOD}\n") is False


def test_neither_a_pin_nor_a_checksums_file_fails() -> None:
    """The whole point: unverified bytes must not be installed."""
    assert _evaluate() is False


@pytest.mark.parametrize("empty", ["", "\n"])
def test_an_empty_checksums_file_fails(empty: str) -> None:
    assert _evaluate(checksums=empty) is False
