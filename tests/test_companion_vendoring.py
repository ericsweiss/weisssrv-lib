"""Every gate with a mandatory companion file fails in contract without it.

Vendoring the gate alone must give exit 2 and a message naming the companion,
not a ModuleNotFoundError traceback. gate_common: test_gate_common_vendoring.py.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
# The deploy-coverage gate prefers the pipeline's base SHA over its argument;
# the throwaway repo under test must not inherit the runner's.
_env = {k: v for k, v in os.environ.items()
        if k not in ("CI_MERGE_REQUEST_DIFF_BASE_SHA", "CI_COMMIT_BEFORE_SHA")}
SCRIPTS = REPO / "scripts"

PAIRS = (
    ("check-ci-include-job-names.py", "ci_yaml.py"),
    ("check-cluster-invariants.py", "inventory_tree.py"),
    ("check-deploy-coverage.sh", "ci_yaml.py"),
    ("check-guest-endpoint-parity.py", "inventory_tree.py"),
    ("check-deploy-preflight.py", "ci_playbook_invocations.py"),
    ("check-deploy-preflight.py", "ci_yaml.py"),
    ("check-molecule-image-pin.py", "ci_yaml.py"),
    ("check-molecule-matrix-coverage.sh", "ci_yaml.py"),
    ("check-version-checksums.py", "check-versions.py"),
    ("check-helm-repo-parity.py", "check-versions.py"),
    ("check-include-contract.py", "ci_yaml.py"),
    ("check-lib-pins.py", "ci_yaml.py"),
    ("generate-hosts-env.py", "inventory_tree.py"),
    ("validate-helm-values.py", "check-hpa-vpa-invariant.py"),
)

# The two shapes a gate loads a companion by: a sibling module goes on sys.path
# and is imported, every other companion is read as a sibling file.
_SIBLING_IMPORT = re.compile(
    r"^\s*(?:from ([A-Za-z0-9_]+) import|import ([A-Za-z0-9_]+)\b)", re.M
)
_SIBLING_LOAD = re.compile(r'parent\s*/\s*"([A-Za-z0-9_.-]+\.py)"')
# gate_common.py has its own suite, which drives the corpus gates end to end.
ELSEWHERE = {"gate_common.py"}

VENDORABLE = set(
    yaml.safe_load((SCRIPTS / "vendorable-paths.yml").read_text(encoding="utf-8"))[
        "vendorable"
    ]
)


def _derived_pairs() -> tuple:
    found = set()
    for path in sorted(SCRIPTS.iterdir()):
        if path.suffix not in (".py", ".sh"):
            continue
        if "scripts/%s" % path.name not in VENDORABLE:
            continue
        text = path.read_text(encoding="utf-8")
        for first, second in _SIBLING_IMPORT.findall(text):
            module = first or second
            companion = module + ".py"
            if (
                "scripts/%s" % companion in VENDORABLE
                and companion != path.name
                and companion not in ELSEWHERE
            ):
                found.add((path.name, companion))
        for companion in _SIBLING_LOAD.findall(text):
            if companion != path.name and companion not in ELSEWHERE:
                found.add((path.name, companion))
    return tuple(sorted(found))


def test_the_pair_list_is_complete():
    """A vendorable gate that grows a companion must grow a pair here too."""
    assert _derived_pairs() == tuple(sorted(PAIRS))


def _marked_gates(text: str | None = None) -> set:
    """Gates the offer list marks: named in a comment, or one sitting above them."""
    if text is None:
        text = (SCRIPTS / "vendorable-paths.yml").read_text(encoding="utf-8")
    lines = text.splitlines()
    comments = [line for line in lines if line.lstrip().startswith("#")]
    marked = set()
    for index, line in enumerate(lines):
        entry = line.strip()
        if not entry.startswith("- scripts/"):
            continue
        name = entry[len("- scripts/"):]
        if any(name in comment for comment in comments):
            marked.add(name)
        elif index and lines[index - 1].lstrip().startswith("#"):
            marked.add(name)
    return marked


def test_every_pair_is_marked_in_the_offer_list():
    """A consumer re-vendoring one gate must read there that it needs the companion."""
    assert sorted({gate for gate, _ in PAIRS} - _marked_gates()) == []


def test_an_unmarked_entry_is_not_credited_as_marked():
    """The marking rule, against an offer list that says nothing about the gate."""
    assert _marked_gates("vendorable:\n  - scripts/ci_yaml.py\n  - scripts/x.py\n") == set()


def _prepare(gate: str, cwd: Path) -> list:
    """Per-gate setup; returns the argv tail the gate needs to reach its import."""
    if gate != "check-deploy-coverage.sh":
        return []
    env = {**os.environ, "GIT_CONFIG_GLOBAL": str(cwd / "gitconfig"),
           "GIT_CONFIG_SYSTEM": str(cwd / "gitconfig")}
    run = lambda *a: subprocess.run(  # noqa: E731
        ["git", "-C", str(cwd), *a], check=True, capture_output=True, env=env
    )
    run("init", "-q", "-b", "main")
    run("config", "user.email", "gate@example.invalid")
    run("config", "user.name", "gate")
    (cwd / ".gitlab-ci.yml").write_text(
        "deploy-x:\n  stage: deploy\n  rules:\n    - changes:\n"
        "        - ansible/roles/example/**/*\n",
        encoding="utf-8",
    )
    run("add", "-A")
    run("commit", "-qm", "base")
    role = cwd / "ansible" / "roles" / "example" / "tasks"
    role.mkdir(parents=True)
    (role / "main.yml").write_text("---\n", encoding="utf-8")
    run("add", "-A")
    run("commit", "-qm", "change")
    return ["HEAD~1"]


def _run(script: Path, cwd: Path, argv_tail: list = ()) -> subprocess.CompletedProcess:
    argv = ["bash", str(script)] if script.suffix == ".sh" else [
        sys.executable, str(script)
    ]
    return subprocess.run(
        [*argv, *argv_tail], input="", capture_output=True, text=True, cwd=str(cwd),
        env={**_env, "PYTHONPATH": "", "SCRIPT_DIR": str(cwd)},
    )


@pytest.mark.parametrize("gate,companion", PAIRS)
def test_the_gate_exits_two_when_its_companion_is_not_vendored(gate, companion, tmp_path):
    shutil.copy(SCRIPTS / gate, tmp_path / gate)
    # Only the companion under test is absent: a gate with two names whichever
    # one it misses, so leaving both out would assert the other's message.
    for other in {c for g, c in PAIRS if g == gate} - {companion}:
        shutil.copy(SCRIPTS / other, tmp_path / other)
    argv_tail = _prepare(gate, tmp_path)
    proc = _run(tmp_path / gate, tmp_path, argv_tail)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert companion in proc.stderr
    assert "Traceback" not in proc.stderr


@pytest.mark.parametrize("gate,companion", PAIRS)
def test_the_gate_gets_past_the_import_when_its_companion_is_vendored(
    gate, companion, tmp_path
):
    shutil.copy(SCRIPTS / gate, tmp_path / gate)
    shutil.copy(SCRIPTS / companion, tmp_path / companion)
    argv_tail = _prepare(gate, tmp_path)
    proc = _run(tmp_path / gate, tmp_path, argv_tail)
    assert companion not in proc.stderr or "must sit next to" not in proc.stderr
    assert "ModuleNotFoundError" not in proc.stderr


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
