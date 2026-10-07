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


class TestHappyPath:
    def test_a_legal_pipeline_passes(self, world, capsys):
        assert run(world) == 0
        assert "1 included file(s) checked" in capsys.readouterr().out

    def test_a_template_with_no_spec_header_passes(self, world):
        lib, consumer = world
        write(lib, "ci/lint/gate.yml", 'plain-job:\n  stage: lint\n  script: ["true"]\n')
        write(
            consumer,
            ".gitlab-ci.yml",
            """\
            stages: [lint]
            include:
              - project: group/weisssrv-lib
                file: /ci/lint/gate.yml
            """,
        )
        assert run(world) == 0

    def test_a_local_include_is_checked_too(self, world):
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


class TestUndeclaredInput:
    def test_an_input_the_template_does_not_declare_fails(self, world, capsys):
        _lib, consumer = world
        write(
            consumer,
            ".gitlab-ci.yml",
            """\
            stages: [lint]
            include:
              - project: group/weisssrv-lib
                file: /ci/lint/gate.yml
                inputs:
                  stagee: lint
            """,
        )
        assert run(world) == 1
        assert "declares no input 'stagee'" in capsys.readouterr().err


class TestRequiredInput:
    def test_an_omitted_default_less_input_fails(self, world, capsys):
        lib, _consumer = world
        write(
            lib,
            "ci/lint/gate.yml",
            """\
            spec:
              inputs:
                stage:
                  default: lint
                lib_ref: {}
            ---
            run-gate:
              stage: $[[ inputs.stage ]]
              script: ["echo $[[ inputs.lib_ref ]]"]
            """,
        )
        assert run(world) == 1
        assert "requires input 'lib_ref'" in capsys.readouterr().err

    def test_passing_it_clears_the_finding(self, world):
        lib, consumer = world
        write(
            lib,
            "ci/lint/gate.yml",
            """\
            spec:
              inputs:
                lib_ref: {}
            ---
            run-gate:
              stage: lint
              script: ["echo $[[ inputs.lib_ref ]]"]
            """,
        )
        write(
            consumer,
            ".gitlab-ci.yml",
            """\
            stages: [lint]
            include:
              - project: group/weisssrv-lib
                file: /ci/lint/gate.yml
                inputs:
                  lib_ref: v1.0.0
            """,
        )
        assert run(world) == 0

    def test_a_null_declaration_is_still_required(self, world, capsys):
        """`lib_ref:` with no body parses as None, which carries no default."""
        lib, _consumer = world
        write(
            lib,
            "ci/lint/gate.yml",
            'spec:\n  inputs:\n    lib_ref:\n---\nrun-gate:\n'
            '  stage: lint\n  script: ["true"]\n',
        )
        assert run(world) == 1
        assert "requires input 'lib_ref'" in capsys.readouterr().err


class TestStages:
    def test_a_stage_the_pipeline_does_not_declare_fails(self, world, capsys):
        _lib, consumer = world
        write(
            consumer,
            ".gitlab-ci.yml",
            """\
            stages: [deploy]
            include:
              - project: group/weisssrv-lib
                file: /ci/lint/gate.yml
            """,
        )
        assert run(world) == 1
        err = capsys.readouterr().err
        assert "resolves to stage 'lint'" in err
        assert "'run-gate'" in err

    def test_the_input_default_is_what_resolves_the_stage(self, world, capsys):
        """The include passes no stage, so the template default is the subject."""
        lib, consumer = world
        write(
            lib,
            "ci/lint/gate.yml",
            'spec:\n  inputs:\n    stage:\n      default: validate\n---\n'
            'run-gate:\n  stage: $[[ inputs.stage ]]\n  script: ["true"]\n',
        )
        write(
            consumer,
            ".gitlab-ci.yml",
            """\
            stages: [lint]
            include:
              - project: group/weisssrv-lib
                file: /ci/lint/gate.yml
            """,
        )
        assert run(world) == 1
        assert "resolves to stage 'validate'" in capsys.readouterr().err

    def test_a_passed_input_overrides_the_default(self, world):
        lib, consumer = world
        write(
            lib,
            "ci/lint/gate.yml",
            'spec:\n  inputs:\n    stage:\n      default: validate\n---\n'
            'run-gate:\n  stage: $[[ inputs.stage ]]\n  script: ["true"]\n',
        )
        write(
            consumer,
            ".gitlab-ci.yml",
            """\
            stages: [lint]
            include:
              - project: group/weisssrv-lib
                file: /ci/lint/gate.yml
                inputs:
                  stage: lint
            """,
        )
        assert run(world) == 0

    def test_a_stage_less_job_resolves_to_test(self, world, capsys):
        lib, consumer = world
        write(lib, "ci/lint/gate.yml", 'run-gate:\n  script: ["true"]\n')
        write(
            consumer,
            ".gitlab-ci.yml",
            """\
            stages: [lint]
            include:
              - project: group/weisssrv-lib
                file: /ci/lint/gate.yml
            """,
        )
        assert run(world) == 1
        assert "resolves to stage 'test'" in capsys.readouterr().err

    def test_a_pipeline_with_no_stages_key_gets_gitlab_s_defaults(self, world):
        lib, consumer = world
        write(lib, "ci/lint/gate.yml", 'run-gate:\n  script: ["true"]\n')
        write(
            consumer,
            ".gitlab-ci.yml",
            'include:\n  - project: group/weisssrv-lib\n    file: /ci/lint/gate.yml\n',
        )
        assert run(world) == 0

    def test_the_always_present_stages_are_accepted(self, world):
        lib, consumer = world
        write(lib, "ci/lint/gate.yml", 'run-gate:\n  stage: .pre\n  script: ["true"]\n')
        write(
            consumer,
            ".gitlab-ci.yml",
            """\
            stages: [lint]
            include:
              - project: group/weisssrv-lib
                file: /ci/lint/gate.yml
            """,
        )
        assert run(world) == 0

    def test_a_hidden_template_is_not_a_job(self, world):
        lib, consumer = world
        write(lib, "ci/lint/gate.yml", '.base:\n  stage: nowhere\n  script: ["true"]\n')
        write(
            consumer,
            ".gitlab-ci.yml",
            """\
            stages: [lint]
            include:
              - project: group/weisssrv-lib
                file: /ci/lint/gate.yml
            """,
        )
        # `.base` creates no job, but something must still have been inspected.
        assert run(world) == 0

    def test_an_extending_job_is_left_unchecked(self, world):
        lib, consumer = world
        write(
            lib,
            "ci/lint/gate.yml",
            'run-gate:\n  extends: .base\n  script: ["true"]\n',
        )
        write(
            consumer,
            ".gitlab-ci.yml",
            """\
            stages: [lint]
            include:
              - project: group/weisssrv-lib
                file: /ci/lint/gate.yml
            """,
        )
        assert run(world) == 0

    def test_a_pages_job_is_not_read_as_an_implicit_test_job(self, world):
        lib, consumer = world
        write(lib, "ci/lint/gate.yml", 'pages:\n  script: ["true"]\n')
        write(
            consumer,
            ".gitlab-ci.yml",
            """\
            stages: [lint]
            include:
              - project: group/weisssrv-lib
                file: /ci/lint/gate.yml
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
