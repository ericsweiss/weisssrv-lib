#!/usr/bin/env python3
"""Repo invariant: docs/SCRIPTS.md's two promises stay true as scripts are added.

Without it a script ships documented but ungated, or gated but undocumented.
"""

import os
import re
from pathlib import Path
from typing import Dict, List

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
TESTS = Path(__file__).resolve().parent
DOC = REPO / "docs" / "SCRIPTS.md"

# Scripts that ship without a suite on purpose: path relative to scripts/ -> reason.
# An explicit list, not a filter, so an exemption is a visible edit a reviewer sees.
EXEMPT: Dict[str, str] = {}


def collect(root: Path) -> List[Path]:
    """Every file under `root` this gate covers: executable, or .py/.sh (some
    vendored scripts ship mode 644). Recursive; __pycache__ excluded."""
    found = []
    for path in sorted(root.rglob("*")):
        # Generated, not shipped.
        if "__pycache__" in path.parts or not path.is_file():
            continue
        if path.relative_to(root).as_posix() in EXEMPT:
            continue
        if os.access(path, os.X_OK) or path.suffix in (".py", ".sh"):
            found.append(path)
    return found


# Executable code ships from two roots: scripts/, and the reaper modules a
# CronJob mounts beside its app script.
REAPERS = REPO / "kubernetes" / "reapers"
SHIPPED_ROOTS = ((SCRIPTS, SCRIPTS), (REAPERS, REAPERS))
SHIPPED = [path for root, _ in SHIPPED_ROOTS for path in collect(root)]

# Role `files/` scripts run on a real host (as root, in several cases), so they
# need a suite too. Molecule fixture scripts and the installed collection copy
# under .ansible/ are not shipped code.
COLLECTIONS = REPO / "ansible_collections"
ROLE_SCRIPT_EXEMPT: Dict[str, str] = {
    "node_exporter_host/files/vzdump-metrics-hook.sh":
        "run and asserted on by node_exporter_host/molecule/default/verify.yml",
    "smtp_relay/files/postfix-queue-collector.sh":
        "run and asserted on by smtp_relay/molecule/default/verify.yml",
}


def exemption_is_backed(key: str, reason: str, root: Path = COLLECTIONS) -> bool:
    """A reason naming a molecule verify.yml holds only while some verify.yml of
    that role still names the script. Reasons of other shapes are unverifiable
    here and pass."""
    if "verify.yml" not in reason:
        return True
    role, name = key.split("/", 1)[0], Path(key).name
    return any(
        name in verify.read_text(encoding="utf-8")
        for verify in sorted(root.glob("*/*/roles/%s/molecule/*/verify.yml" % role))
    )


def collect_role_scripts(root: Path) -> List[Path]:
    """Every `<role>/files/**.{py,sh}` a role ships to a host."""
    found = []
    for path in sorted(root.glob("*/*/roles/*/files/**/*")):
        if not path.is_file() or path.suffix not in (".py", ".sh"):
            continue
        parts = path.relative_to(root).parts
        if ".ansible" in parts or "molecule" in parts:
            continue
        rel = path.relative_to(path.parents[2]).as_posix()
        if rel in ROLE_SCRIPT_EXEMPT:
            continue
        found.append(path)
    return found


ROLE_SCRIPTS = collect_role_scripts(COLLECTIONS)


def _role_suite_for(script: Path) -> Path:
    """tests/test_<script stem>.py, with `-` and `.` as `_`."""
    return TESTS / ("test_%s.py" % re.sub(r"[-./]", "_", script.stem))


def _suite_for(script: Path) -> Path:
    """tests/test_<path under scripts/>.py, with `-`, `.` and `/` as `_`.

    The directory is part of the key, so `scripts/lib/helper.sh` does not share
    a suite with `scripts/helper.sh`. The `.py`/`.sh` suffix is dropped.
    """
    root = REAPERS if REAPERS in script.parents else SCRIPTS
    rel = script.relative_to(root).as_posix()
    for suffix in (".py", ".sh"):
        rel = rel.removesuffix(suffix)
    return TESTS / ("test_%s.py" % re.sub(r"[-./]", "_", rel))


def _assert_suite_exercises(script: Path, suite: Path) -> None:
    """The suite names the script and defines at least one test."""
    if not suite.exists():
        pytest.fail("%s has no suite at all — see the existence check." % script.name)
    body = suite.read_text(encoding="utf-8")
    assert script.name in body, (
        "%s exists but never names %s — a placeholder suite satisfies the "
        "existence check while proving nothing." % (suite.name, script.name)
    )
    assert re.search(r"^\s*def test_", body, re.M), "%s defines no tests" % suite.name


def test_there_are_scripts_to_check():
    """Guard the guard: a bad walk would make every assertion below vacuous."""
    assert len(SHIPPED) > 20


def test_there_are_reaper_scripts_to_check():
    """Guard the guard: the reapers root ships code and must be walked."""
    assert len(collect(REAPERS)) >= 1


def test_no_two_scripts_share_a_suite():
    """Two scripts mapping to one suite would let the second ship untested."""
    seen: Dict[Path, Path] = {}
    for script in SHIPPED:
        suite = _suite_for(script)
        assert suite not in seen, (
            "%s and %s both map to %s" % (seen.get(suite), script, suite.name)
        )
        seen[suite] = script


def test_no_two_role_scripts_share_a_suite():
    """The role key is the bare stem, so two roles shipping the same basename
    would map to one suite and the second would ship untested."""
    seen: Dict[Path, Path] = {}
    for script in ROLE_SCRIPTS:
        suite = _role_suite_for(script)
        assert suite not in seen, (
            "%s and %s both map to %s" % (seen.get(suite), script, suite.name)
        )
        seen[suite] = script


def test_the_collector_sees_every_kind_of_script_it_claims_to_cover(tmp_path):
    """The collector sees extensionless executables, nested helpers and
    non-.py/.sh executables."""
    (tmp_path / "lib").mkdir()
    (tmp_path / "__pycache__").mkdir()
    for name, mode in (
        ("tool.py", 0o644),  # vendored, not run in place
        ("lib/helper.sh", 0o755),
        ("bare-tool", 0o755),  # shebang, no extension
        ("hook.pl", 0o755),
        ("README.md", 0o644),  # not a script
        ("__pycache__/tool.cpython-313.pyc", 0o755),  # generated
    ):
        target = tmp_path / name
        target.write_text("")
        target.chmod(mode)

    assert {p.name for p in collect(tmp_path)} == {
        "tool.py",
        "helper.sh",
        "bare-tool",
        "hook.pl",
    }


def test_an_exemption_is_an_explicit_edit(tmp_path):
    """Opting a file out is possible, but only by naming it (with a reason)."""
    script = tmp_path / "unloved.sh"
    script.write_text("")
    assert [p.name for p in collect(tmp_path)] == ["unloved.sh"]
    EXEMPT["unloved.sh"] = "test fixture"
    try:
        assert collect(tmp_path) == []
    finally:
        del EXEMPT["unloved.sh"]


@pytest.mark.parametrize(
    "script", SHIPPED, ids=lambda p: p.relative_to(REPO).as_posix()
)
def test_every_shipped_script_has_a_suite(script: Path):
    suite = _suite_for(script)
    assert suite.exists(), (
        "%s has no tests — add %s, or drop the script. docs/SCRIPTS.md tells "
        "consumers every script here is covered." % (script.name, suite.name)
    )


@pytest.mark.parametrize(
    "script", SHIPPED, ids=lambda p: p.relative_to(REPO).as_posix()
)
def test_every_suite_actually_exercises_its_script(script: Path):
    """Existence certifies a filename, not coverage: `def test_placeholder: pass`
    in a correctly-named file satisfies the check above and proves nothing. Make
    the suite show it at least reaches for the script it is named after."""
    _assert_suite_exercises(script, _suite_for(script))


@pytest.mark.parametrize(
    "script", SHIPPED, ids=lambda p: p.relative_to(REPO).as_posix()
)
def test_every_shipped_script_is_documented(script: Path):
    assert script.name in DOC.read_text(encoding="utf-8"), (
        "%s is not mentioned in docs/SCRIPTS.md" % script.name
    )


def test_there_are_role_scripts_to_check():
    """Guard the guard: a bad glob would make the role-script check vacuous."""
    assert len(ROLE_SCRIPTS) + len(ROLE_SCRIPT_EXEMPT) >= 8


def test_the_role_collector_skips_fixtures_and_the_installed_copy(tmp_path):
    """Molecule fixtures and .ansible/ copies are not shipped code."""
    base = tmp_path / "ns" / "coll" / "roles"
    for rel in (
        "alpha/files/real.sh",
        "alpha/molecule/default/files/fixture.sh",
        "alpha/files/notes.md",
    ):
        target = base / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("")
    installed = tmp_path / "ns" / ".ansible" / "roles" / "alpha" / "files"
    installed.mkdir(parents=True)
    (installed / "installed.sh").write_text("")
    names = [p.name for p in collect_role_scripts(tmp_path)]
    assert names == ["real.sh"]
    assert "installed.sh" not in names


def test_a_role_script_exemption_is_an_explicit_edit(tmp_path):
    """Opting one out is possible, but only by naming it with a reason."""
    target = tmp_path / "ns" / "coll" / "roles" / "alpha" / "files" / "loose.sh"
    target.parent.mkdir(parents=True)
    target.write_text("")
    assert [p.name for p in collect_role_scripts(tmp_path)] == ["loose.sh"]
    ROLE_SCRIPT_EXEMPT["alpha/files/loose.sh"] = "test fixture"
    try:
        assert collect_role_scripts(tmp_path) == []
    finally:
        del ROLE_SCRIPT_EXEMPT["alpha/files/loose.sh"]


@pytest.mark.parametrize("key", sorted(ROLE_SCRIPT_EXEMPT))
def test_every_role_script_exemption_claim_still_holds(key: str):
    """An exemption that points at a molecule verify.yml is only as good as that
    file: once it stops naming the script, the exemption hides an untested
    script that runs as root on a host."""
    assert exemption_is_backed(key, ROLE_SCRIPT_EXEMPT[key]), (
        "%s is exempt because %r, but no molecule verify.yml of that role names "
        "it any more — write the suite or correct the reason."
        % (key, ROLE_SCRIPT_EXEMPT[key])
    )


@pytest.mark.parametrize("key", sorted(ROLE_SCRIPT_EXEMPT))
def test_no_exempted_role_script_already_has_a_suite(key: str):
    """A suite under the exempted script's name means the exemption is stale:
    drop it so the suite is held to the checks below."""
    suite = _role_suite_for(Path(key))
    assert not suite.exists(), (
        "%s is exempt yet %s exists — drop the ROLE_SCRIPT_EXEMPT entry."
        % (key, suite.name)
    )


def test_a_stale_exemption_claim_is_caught(tmp_path):
    """Mutation proof for the claim check: a reason naming a verify.yml that
    does not mention the script must not pass."""
    verify = tmp_path / "ns" / "coll" / "roles" / "alpha" / "molecule" / "default"
    verify.mkdir(parents=True)
    (verify / "verify.yml").write_text("- command: /usr/local/bin/real.sh\n")
    reason = "run and asserted on by alpha/molecule/default/verify.yml"
    assert exemption_is_backed("alpha/files/real.sh", reason, tmp_path)
    assert not exemption_is_backed("alpha/files/gone.sh", reason, tmp_path)


@pytest.mark.parametrize(
    "script", ROLE_SCRIPTS, ids=lambda p: p.relative_to(COLLECTIONS).as_posix()
)
def test_every_role_script_has_a_suite(script: Path):
    suite = _role_suite_for(script)
    assert suite.exists(), (
        "%s runs on a real host with no suite — add %s, or name it in "
        "ROLE_SCRIPT_EXEMPT with a reason." % (script.name, suite.name)
    )


@pytest.mark.parametrize(
    "script", ROLE_SCRIPTS, ids=lambda p: p.relative_to(COLLECTIONS).as_posix()
)
def test_every_role_suite_actually_exercises_its_script(script: Path):
    """A role script runs on a real host as root, so a placeholder suite is the
    worst place for one."""
    _assert_suite_exercises(script, _role_suite_for(script))


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
