"""Tests for scripts/check-include-contract.py, the include input-contract gate.

Every arm has a case that makes the gate FAIL: an undeclared input, an omitted
REQUIRED input, a stage no pipeline declares, and each operator error.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from script_loader import load_script

mod = load_script("check-include-contract.py", register=True)
SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check-include-contract.py"


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


@pytest.fixture()
def world(tmp_path):
    """A consumer pipeline including one library template, both legal."""
    lib = tmp_path / "lib"
    write(
        lib,
        "ci/lint/gate.yml",
        """\
        spec:
          inputs:
            stage:
              default: lint
            job_name:
              default: run-gate
        ---
        "$[[ inputs.job_name ]]":
          stage: $[[ inputs.stage ]]
          script: ["true"]
        """,
    )
    consumer = tmp_path / "consumer"
    write(
        consumer,
        ".gitlab-ci.yml",
        """\
        stages: [lint, deploy]
        include:
          - project: group/weisssrv-lib
            ref: v1.0.0
            file: /ci/lint/gate.yml
            inputs:
              stage: lint
        """,
    )
    return lib, consumer


def run(world, *extra: str) -> int:
    lib, consumer = world
    return mod.main(["--repo-root", str(consumer), "--lib-path", str(lib), *extra])


def pipeline(consumer: Path) -> Path:
    return consumer / ".gitlab-ci.yml"


def consumer_pipeline(stages, inputs=None) -> str:
    """A pipeline including /ci/lint/gate.yml; `stages=None` omits the key."""
    lines = [] if stages is None else ["stages: [%s]" % ", ".join(stages)]
    lines += ["include:", "  - project: group/weisssrv-lib", "    file: /ci/lint/gate.yml"]
    if inputs:
        lines += ["    inputs:"] + ["      %s: %s" % item for item in inputs.items()]
    return "\n".join(lines) + "\n"


def _job(body: str, spec: str = "") -> str:
    return (spec + "---\n" if spec else "") + "run-gate:\n" + body + '  script: ["true"]\n'


REQUIRES_LIB_REF = (
    'spec:\n  inputs:\n    stage:\n      default: lint\n    lib_ref: {}\n---\n'
    'run-gate:\n  stage: $[[ inputs.stage ]]\n  script: ["echo $[[ inputs.lib_ref ]]"]\n'
)
DEFAULTS_TO_VALIDATE = _job(
    "  stage: $[[ inputs.stage ]]\n", "spec:\n  inputs:\n    stage:\n      default: validate\n"
)
STAGELESS = _job("")

CASES = [
    # (id, template or None for the fixture's, stages, inputs, rc, needles)
    ("legal", None, ["lint", "deploy"], {"stage": "lint"}, 0, ["1 included file(s) checked"]),
    ("no-spec-header", 'plain-job:\n  stage: lint\n  script: ["true"]\n', ["lint"], None, 0, []),
    ("undeclared-input", None, ["lint"], {"stagee": "lint"}, 1, ["declares no input 'stagee'"]),
    ("required-input-omitted", REQUIRES_LIB_REF, ["lint"], None, 1,
     ["requires input 'lib_ref'"]),
    ("required-input-passed",
     'spec:\n  inputs:\n    lib_ref: {}\n---\n'
     'run-gate:\n  stage: lint\n  script: ["echo $[[ inputs.lib_ref ]]"]\n',
     ["lint"], {"lib_ref": "v1.0.0"}, 0, []),
    # `lib_ref:` with no body parses as None, which carries no default.
    ("required-input-null",
     _job("  stage: lint\n", "spec:\n  inputs:\n    lib_ref:\n"), ["lint"], None, 1,
     ["requires input 'lib_ref'"]),
    ("undeclared-stage", None, ["deploy"], None, 1, ["resolves to stage 'lint'", "'run-gate'"]),
    # The include passes no stage, so the template default is the subject.
    ("default-resolves-the-stage", DEFAULTS_TO_VALIDATE, ["lint"], None, 1,
     ["resolves to stage 'validate'"]),
    ("passed-input-overrides", DEFAULTS_TO_VALIDATE, ["lint"], {"stage": "lint"}, 0, []),
    ("stage-less-is-test", STAGELESS, ["lint"], None, 1, ["resolves to stage 'test'"]),
    ("no-stages-key-gets-defaults", STAGELESS, None, None, 0, []),
    ("pre-stage", _job("  stage: .pre\n"), ["lint"], None, 0, []),
    # `.base` creates no job, but something must still have been inspected.
    ("hidden-template", '.base:\n  stage: nowhere\n  script: ["true"]\n', ["lint"], None, 0, []),
    ("extends-is-unchecked", _job("  extends: .base\n"), ["lint"], None, 0, []),
    ("pages-is-no-test-job", 'pages:\n  script: ["true"]\n', ["lint"], None, 0, []),
]


@pytest.mark.parametrize(
    "template, stages, inputs, rc, needles", [c[1:] for c in CASES], ids=[c[0] for c in CASES]
)
def test_include_contract(world, capsys, template, stages, inputs, rc, needles):
    lib, consumer = world
    if template is not None:
        (lib / "ci" / "lint" / "gate.yml").write_text(template, encoding="utf-8")
    pipeline(consumer).write_text(consumer_pipeline(stages, inputs), encoding="utf-8")
    assert run(world) == rc
    out, err = capsys.readouterr()
    for needle in needles:
        assert needle in (err if rc else out)


def test_a_local_include_is_checked_too(world):
    _lib, consumer = world
    write(consumer, "ci/local.yml", 'own-job:\n  stage: deploy\n  script: ["true"]\n')
    write(
        consumer,
        ".gitlab-ci.yml",
        """\
        stages: [lint, deploy]
        include:
          - local: /ci/local.yml
        """,
    )
    assert run(world) == 0


class TestUnreadableIncludes:
    def test_a_remote_include_is_named_not_silently_dropped(self, world, capsys):
        _lib, consumer = world
        text = pipeline(consumer).read_text()
        pipeline(consumer).write_text(
            text + "  - component: $CI_SERVER_FQDN/g/c/job@1.0\n"
        )
        assert run(world) == 0
        assert "not contract-checked" in capsys.readouterr().out

    def test_only_unreadable_includes_is_an_operator_error(self, world, capsys):
        _lib, consumer = world
        write(
            consumer,
            ".gitlab-ci.yml",
            """\
            stages: [lint]
            include:
              - remote: https://example.invalid/ci.yml
            """,
        )
        assert run(world) == 2
        assert "inspected nothing" in capsys.readouterr().err


class TestOperatorErrors:
    def test_a_project_include_with_no_lib_checkout_exits_2(self, world, capsys, monkeypatch):
        _lib, consumer = world
        monkeypatch.delenv("WEISSSRV_LIB_PATH", raising=False)
        assert mod.main(["--repo-root", str(consumer)]) == 2
        assert "needs a library checkout" in capsys.readouterr().err

    def test_a_missing_pipeline_exits_2(self, world, capsys):
        _lib, consumer = world
        pipeline(consumer).unlink()
        assert run(world) == 2
        assert "is not a file" in capsys.readouterr().err

    def test_an_included_file_missing_from_the_checkout_exits_2(self, world, capsys):
        lib, _consumer = world
        (lib / "ci" / "lint" / "gate.yml").unlink()
        assert run(world) == 2
        assert "is not in" in capsys.readouterr().err

    def test_an_unparseable_include_exits_2(self, world, capsys):
        lib, _consumer = world
        write(lib, "ci/lint/gate.yml", "job:\n  script: [\n")
        assert run(world) == 2
        assert "not parseable" in capsys.readouterr().err

    def test_a_lib_path_that_is_not_a_directory_exits_2(self, world, capsys):
        _lib, consumer = world
        assert mod.main(
            ["--repo-root", str(consumer), "--lib-path", str(pipeline(consumer))]
        ) == 2
        assert "is not a directory" in capsys.readouterr().err

    def test_a_pipeline_with_no_includes_exits_2(self, world, capsys):
        _lib, consumer = world
        write(consumer, ".gitlab-ci.yml", 'stages: [lint]\njob:\n  script: ["true"]\n')
        assert run(world) == 2
        assert "inspected nothing" in capsys.readouterr().err


class TestInvocation:
    def test_the_repository_s_own_pipeline_passes(self):
        """A repo whose includes are all local obeys its own specs. A pipeline
        that pins the library needs the checkout, so it is skipped instead."""
        repo = SCRIPT.parent.parent
        lib = os.environ.get("WEISSSRV_LIB_PATH")
        argv = ["--repo-root", str(repo)] + (["--lib-path", lib] if lib else [])
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), *argv], capture_output=True, text=True
        )
        if proc.returncode == 2 and "needs a library checkout" in proc.stderr:
            pytest.skip("this pipeline pins the library; set WEISSSRV_LIB_PATH")
        assert proc.returncode == 0, proc.stderr

    def test_a_missing_ci_yaml_companion_is_a_clean_exit_2(self, tmp_path):
        """Vendored alone, the gate names ci_yaml.py instead of tracebacking."""
        solo = tmp_path / "check-include-contract.py"
        solo.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(solo)], capture_output=True, text=True
        )
        assert proc.returncode == 2
        assert "ci_yaml.py must sit next to this script" in proc.stderr
