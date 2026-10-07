#!/usr/bin/env python3
"""Unit tests for check-ci-include-job-names.py.

Each test breaks the link between an optional need and the job an include
creates, and asserts the gate fails: GitLab itself never reports it.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from script_loader import load_script  # noqa: E402

GATE = load_script("check-ci-include-job-names.py")

CONSUMER_CI = """\
include:
  - project: org/weisssrv-lib
    ref: v1.0.0
    file: ci/lint/python-lint.yml

validation-gate:
  stage: validate
  script:
    - echo ok
  needs:
    - job: python-lint
      optional: true
"""

LIB_TEMPLATE = """\
spec:
  inputs:
    job_name:
      default: python-lint
    stage:
      default: lint
---
"$[[ inputs.job_name ]]":
  stage: $[[ inputs.stage ]]
  script:
    - ruff check .
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


@pytest.fixture
def world(tmp_path: Path):
    consumer = tmp_path / "consumer"
    lib = tmp_path / "lib"
    _write(consumer / ".gitlab-ci.yml", CONSUMER_CI)
    _write(lib / "ci" / "lint" / "python-lint.yml", LIB_TEMPLATE)
    return consumer, lib


def _run(world, extra: list[str] | None = None) -> int:
    consumer, lib = world
    return GATE.main(
        ["--repo-root", str(consumer), "--lib-path", str(lib), *(extra or [])]
    )


def test_resolvable_optional_need_passes(world, capsys):
    assert _run(world) == 0
    assert "Optional needs OK" in capsys.readouterr().out


def test_library_rename_fails(world, capsys):
    """The library renames its job key: the consumer's need now names nothing."""
    consumer, lib = world
    path = lib / "ci" / "lint" / "python-lint.yml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "default: python-lint", "default: ruff-lint"
        ),
        encoding="utf-8",
    )
    assert _run(world) == 1
    err = capsys.readouterr().err
    assert "'python-lint' is an optional need" in err
    assert "validation-gate" in err


def test_non_default_job_name_input_is_honoured(world):
    """A consumer renaming the job through `inputs:` must update the need too;
    the gate credits the supplied value, not the spec default."""
    consumer, lib = world
    ci = consumer / ".gitlab-ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            "    file: ci/lint/python-lint.yml\n",
            "    file: ci/lint/python-lint.yml\n"
            "    inputs:\n      job_name: repo-python-lint\n",
        ),
        encoding="utf-8",
    )
    assert _run(world) == 1
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            "    - job: python-lint", "    - job: repo-python-lint"
        ),
        encoding="utf-8",
    )
    assert _run(world) == 0


def test_job_defined_in_the_consumer_file_resolves(world):
    consumer, _lib = world
    ci = consumer / ".gitlab-ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8") + "\nlocal-gate:\n  script:\n    - echo ok\n",
        encoding="utf-8",
    )
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            "    - job: python-lint\n      optional: true\n",
            "    - job: python-lint\n      optional: true\n"
            "    - job: local-gate\n      optional: true\n",
        ),
        encoding="utf-8",
    )
    assert _run(world) == 0


def test_local_include_is_resolved(world):
    consumer, _lib = world
    _write(
        consumer / ".gitlab" / "ci" / "extra.yml",
        """\
        child-gate:
          script:
            - echo ok
        """,
    )
    ci = consumer / ".gitlab-ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8")
        .replace("include:\n", "include:\n  - local: .gitlab/ci/extra.yml\n")
        .replace(
            "    - job: python-lint\n      optional: true\n",
            "    - job: python-lint\n      optional: true\n"
            "    - job: child-gate\n      optional: true\n",
        ),
        encoding="utf-8",
    )
    assert _run(world) == 0


def test_hidden_template_key_does_not_create_a_job(world, capsys):
    """A `.hidden` anchor is not a job, so a need on it never runs."""
    consumer, lib = world
    path = lib / "ci" / "lint" / "python-lint.yml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            '"$[[ inputs.job_name ]]":', '".$[[ inputs.job_name ]]":'
        ),
        encoding="utf-8",
    )
    assert _run(world) == 1
    assert "'python-lint' is an optional need" in capsys.readouterr().err


def test_unreadable_include_is_named_in_the_failure(world, capsys):
    consumer, lib = world
    ci = consumer / ".gitlab-ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            "    - job: python-lint\n      optional: true\n",
            "    - job: remote-gate\n      optional: true\n",
        ),
        encoding="utf-8",
    )
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            "include:\n", "include:\n  - remote: https://example.invalid/x.yml\n"
        ),
        encoding="utf-8",
    )
    assert _run(world) == 1
    err = capsys.readouterr().err
    assert "not a file in either checkout" in err


def test_extra_job_declares_an_unreadable_source(world):
    consumer, lib = world
    ci = consumer / ".gitlab-ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            "    - job: python-lint\n      optional: true\n",
            "    - job: python-lint\n      optional: true\n"
            "    - job: remote-gate\n      optional: true\n",
        ),
        encoding="utf-8",
    )
    assert _run(world, ["--extra-job", "remote-gate=ships from a CI component"]) == 0


def test_stale_extra_job_fails(world, capsys):
    assert _run(world, ["--extra-job", "ghost=gone"]) == 1
    assert "drop the stale entry" in capsys.readouterr().err


def test_extra_job_without_a_reason_exits_2(world, capsys):
    assert _run(world, ["--extra-job", "remote-gate"]) == 2
    assert "JOB=REASON" in capsys.readouterr().err


def test_missing_lib_checkout_is_an_unreadable_include(world, capsys):
    consumer, _lib = world
    assert GATE.main(["--repo-root", str(consumer)]) == 1
    assert "needs a library checkout" in capsys.readouterr().err


def test_input_with_no_default_and_no_value_is_reported(world, capsys):
    consumer, lib = world
    path = lib / "ci" / "lint" / "python-lint.yml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "    job_name:\n      default: python-lint\n", "    job_name: {}\n"
        ),
        encoding="utf-8",
    )
    assert _run(world) == 1
    assert "neither passes nor defaults" in capsys.readouterr().err


def test_non_optional_need_is_left_alone(world):
    """GitLab already fails pipeline creation for a hard need naming nothing."""
    consumer, lib = world
    ci = consumer / ".gitlab-ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            "    - job: python-lint\n      optional: true\n",
            "    - job: python-lint\n      optional: true\n"
            "    - job: hard-gate\n",
        ),
        encoding="utf-8",
    )
    assert _run(world) == 0


def test_missing_ci_file_exits_2(world, capsys):
    assert _run(world, ["--ci-file", ".gitlab-ci.yaml"]) == 2
    assert "does not exist" in capsys.readouterr().err


def test_pipeline_with_no_optional_need_can_be_required(world, capsys):
    consumer, lib = world
    ci = consumer / ".gitlab-ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            "  needs:\n    - job: python-lint\n      optional: true\n", ""
        ),
        encoding="utf-8",
    )
    assert _run(world) == 0
    assert _run(world, ["--require-optional-needs"]) == 2
    assert "inspected nothing" in capsys.readouterr().err


def test_optional_need_declared_inside_a_local_include_is_inspected(world, capsys):
    """A stale need inside an included template is the case GitLab hides."""
    consumer, _lib = world
    _write(
        consumer / ".gitlab" / "ci" / "extra.yml",
        """\
        child-gate:
          script:
            - echo ok
          needs:
            - job: ghost-gate
              optional: true
        """,
    )
    ci = consumer / ".gitlab-ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            "include:\n", "include:\n  - local: .gitlab/ci/extra.yml\n"
        ),
        encoding="utf-8",
    )
    assert _run(world) == 1
    err = capsys.readouterr().err
    assert "'ghost-gate' is an optional need" in err
    assert ".gitlab/ci/extra.yml:child-gate" in err


def test_optional_need_inside_a_local_include_resolves(world, capsys):
    consumer, _lib = world
    _write(
        consumer / ".gitlab" / "ci" / "extra.yml",
        """\
        child-gate:
          script:
            - echo ok
          needs:
            - job: python-lint
              optional: true
        """,
    )
    ci = consumer / ".gitlab-ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            "include:\n", "include:\n  - local: .gitlab/ci/extra.yml\n"
        ),
        encoding="utf-8",
    )
    assert _run(world) == 0
    assert "Optional needs OK" in capsys.readouterr().out


def test_optional_need_inside_a_nested_include_is_inspected(world, capsys):
    """Nesting is where a rename hides best: the need is two files deep."""
    consumer, _lib = world
    _write(
        consumer / ".gitlab" / "ci" / "extra.yml",
        """\
        include:
          - local: .gitlab/ci/deep.yml
        """,
    )
    _write(
        consumer / ".gitlab" / "ci" / "deep.yml",
        """\
        deep-gate:
          script:
            - echo ok
          needs:
            - job: ghost-gate
              optional: true
        """,
    )
    ci = consumer / ".gitlab-ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            "include:\n", "include:\n  - local: .gitlab/ci/extra.yml\n"
        ),
        encoding="utf-8",
    )
    assert _run(world) == 1
    err = capsys.readouterr().err
    assert "'ghost-gate' is an optional need" in err
    assert ".gitlab/ci/deep.yml:deep-gate" in err


def test_a_local_include_inside_a_library_file_resolves_in_the_library(world, capsys):
    """The nested `local:` belongs to the library checkout, not the consumer."""
    _consumer, lib = world
    path = lib / "ci" / "lint" / "python-lint.yml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "---\n", "---\ninclude:\n  - local: ci/lint/shared.yml\n"
        ),
        encoding="utf-8",
    )
    _write(
        lib / "ci" / "lint" / "shared.yml",
        """\
        shared-gate:
          script:
            - echo ok
          needs:
            - job: ghost-gate
              optional: true
        """,
    )
    assert _run(world) == 1
    err = capsys.readouterr().err
    assert "'ghost-gate' is an optional need" in err
    assert "ci/lint/shared.yml:shared-gate" in err


def test_an_include_cycle_does_not_recurse_forever(world, capsys):
    consumer, _lib = world
    _write(
        consumer / ".gitlab" / "ci" / "a.yml",
        """\
        include:
          - local: .gitlab/ci/b.yml

        a-gate:
          script:
            - echo ok
        """,
    )
    _write(
        consumer / ".gitlab" / "ci" / "b.yml",
        """\
        include:
          - local: .gitlab/ci/a.yml

        b-gate:
          script:
            - echo ok
          needs:
            - job: a-gate
              optional: true
        """,
    )
    ci = consumer / ".gitlab-ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            "include:\n", "include:\n  - local: .gitlab/ci/a.yml\n"
        ),
        encoding="utf-8",
    )
    assert _run(world) == 0
    assert "Optional needs OK" in capsys.readouterr().out


def test_needs_passed_as_an_array_input_are_inspected(world, capsys):
    """`needs: $[[ inputs.needs ]]` takes the dependency from the include."""
    consumer, lib = world
    path = lib / "ci" / "lint" / "python-lint.yml"
    path.write_text(
        path.read_text(encoding="utf-8")
        .replace(
            "    stage:\n      default: lint\n",
            "    stage:\n      default: lint\n"
            "    needs:\n      type: array\n      default: []\n",
        )
        .replace("  stage: $[[ inputs.stage ]]\n",
                 "  stage: $[[ inputs.stage ]]\n  needs: $[[ inputs.needs ]]\n"),
        encoding="utf-8",
    )
    ci = consumer / ".gitlab-ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            "    file: ci/lint/python-lint.yml\n",
            "    file: ci/lint/python-lint.yml\n"
            "    inputs:\n      needs:\n"
            "        - job: ghost-gate\n          optional: true\n",
        ),
        encoding="utf-8",
    )
    assert _run(world) == 1
    err = capsys.readouterr().err
    assert "'ghost-gate' is an optional need" in err
    assert "ci/lint/python-lint.yml:python-lint" in err
    ci.write_text(
        ci.read_text(encoding="utf-8").replace("job: ghost-gate", "job: validation-gate"),
        encoding="utf-8",
    )
    assert _run(world) == 0


def test_a_nested_include_resolves_its_inputs_against_the_parent(world, capsys):
    """The library file forwards its own input to the file it includes."""
    _consumer, lib = world
    path = lib / "ci" / "lint" / "python-lint.yml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "---\n",
            "---\ninclude:\n  - local: ci/lint/shared.yml\n"
            "    inputs:\n      gate_name: $[[ inputs.job_name ]]-shared\n",
        ),
        encoding="utf-8",
    )
    _write(
        lib / "ci" / "lint" / "shared.yml",
        """\
        spec:
          inputs:
            gate_name:
              default: shared-gate
        ---
        "$[[ inputs.gate_name ]]":
          script:
            - echo ok
        """,
    )
    consumer_ci = _consumer / ".gitlab-ci.yml"
    consumer_ci.write_text(
        consumer_ci.read_text(encoding="utf-8").replace(
            "    - job: python-lint\n      optional: true\n",
            "    - job: python-lint\n      optional: true\n"
            "    - job: python-lint-shared\n      optional: true\n",
        ),
        encoding="utf-8",
    )
    assert _run(world) == 0
    assert "Optional needs OK" in capsys.readouterr().out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
