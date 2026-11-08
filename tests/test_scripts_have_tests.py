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

def collect(root: Path) -> List[Path]:
    """Every file under `root` this gate covers: executable, or .py/.sh (some
    vendored scripts ship mode 644). Recursive; __pycache__ excluded."""
    found = []
    for path in sorted(root.rglob("*")):
        # Generated, not shipped.
        if "__pycache__" in path.parts or not path.is_file():
            continue
        if os.access(path, os.X_OK) or path.suffix in (".py", ".sh"):
            found.append(path)
    return found


# Executable code ships from scripts/ and from the CronJob programs under
# kubernetes/ a consumer mounts into its cluster.
REAPERS = REPO / "kubernetes" / "reapers"
DDNS = REPO / "kubernetes" / "ddns"
SHIPPED_ROOTS = ((SCRIPTS, SCRIPTS), (REAPERS, REAPERS), (DDNS, DDNS))
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
    root = next(r for r, _ in SHIPPED_ROOTS if r in script.parents)
    rel = script.relative_to(root).as_posix()
    for suffix in (".py", ".sh"):
        rel = rel.removesuffix(suffix)
    return TESTS / ("test_%s.py" % re.sub(r"[-./]", "_", rel))


def _assert_suite_exercises(script: Path, suite: Path, remedy: str) -> None:
    """The suite exists, names the script and defines at least one test."""
    if not suite.exists():
        pytest.fail("%s has no tests — add %s, or %s." % (script.name, suite.name, remedy))
    body = suite.read_text(encoding="utf-8")
    assert script.name in body, (
        "%s exists but never names %s — a placeholder suite proves nothing."
        % (suite.name, script.name)
    )
    assert re.search(r"^\s*def test_", body, re.M), "%s defines no tests" % suite.name


def test_there_are_scripts_to_check():
    """Guard the guard: a bad walk would make every assertion below vacuous."""
    assert len(SHIPPED) > 20


@pytest.mark.parametrize("root", [REAPERS, DDNS], ids=["reapers", "ddns"])
def test_there_are_cronjob_programs_to_check(root: Path):
    """Guard the guard: each CronJob program root ships code and must be walked."""
    assert len(collect(root)) >= 1


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


@pytest.mark.parametrize(
    "script", SHIPPED, ids=lambda p: p.relative_to(REPO).as_posix()
)
def test_every_suite_actually_exercises_its_script(script: Path):
    """docs/SCRIPTS.md tells consumers every script here is covered, and a
    correctly-named placeholder file proves nothing, so the suite must reach
    for the script it is named after."""
    _assert_suite_exercises(script, _suite_for(script), "drop the script")


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
def test_every_role_suite_actually_exercises_its_script(script: Path):
    """A role script runs on a real host as root, so a placeholder suite is the
    worst place for one."""
    _assert_suite_exercises(
        script, _role_suite_for(script), "name it in ROLE_SCRIPT_EXEMPT with a reason"
    )



# Sourced or imported, never run: these keep their shebang for editors and
# shellcheck and stay non-executable. Every other shebang script is a program.
LIBRARIES = frozenset({
    "ci-run-check.sh", "collect-state-lib.sh", "deploy-verify-lib.sh",
    "shell-lib.sh", "smoke-lib.sh", "ci_playbook_invocations.py", "ci_yaml.py",
    "gate_common.py", "inventory_tree.py", "tenant_common.py",
})


def test_a_script_is_executable_exactly_when_it_is_a_program_with_a_shebang():
    scripts = sorted([*SCRIPTS.glob("*.py"), *SCRIPTS.glob("*.sh")])
    names = {path.name for path in scripts}
    assert LIBRARIES <= names, sorted(LIBRARIES - names)
    wrong = []
    for path in scripts:
        shebang = path.read_bytes()[:2] == b"#!"
        executable = os.access(path, os.X_OK)
        if path.name in LIBRARIES:
            if executable:
                wrong.append(f"{path.name}: a library, so not executable")
        elif shebang and not executable:
            wrong.append(f"{path.name}: has a shebang but is not executable")
        elif executable and not shebang:
            wrong.append(f"{path.name}: is executable but has no shebang")
    assert not wrong, wrong

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
