#!/usr/bin/env python3
"""Unit tests for check-ci-include-job-names.py.

Each test breaks a link between an optional need and the job an include makes,
or hides an include, and asserts the gate refuses to pass. GitLab reports neither.
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


NEED = "    - job: python-lint\n      optional: true\n"
REMOTE = "remote: https://example.invalid/x.yml"
ALLOW_REMOTE = ["--allow-unreadable-include", "example.invalid=vendor template, not ours"]


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new), encoding="utf-8")


def _ci(world) -> Path:
    return world[0] / ".gitlab-ci.yml"


def _template(world) -> Path:
    return world[1] / "ci" / "lint" / "python-lint.yml"


def _add_include(world, line: str) -> None:
    """Add one entry to the consumer's `include:` list."""
    _edit(_ci(world), "include:\n", f"include:\n  - {line}\n")


def _add_need(world, job: str) -> None:
    """Add an optional need on `job` to validation-gate."""
    _edit(_ci(world), NEED, NEED + f"    - job: {job}\n      optional: true\n")


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
    _edit(_template(world), "default: python-lint", "default: ruff-lint")
    assert _run(world) == 1
    err = capsys.readouterr().err
    assert "'python-lint' is an optional need" in err
    assert "validation-gate" in err


def test_non_default_job_name_input_is_honoured(world):
    """A consumer renaming the job through `inputs:` must update the need too;
    the gate credits the supplied value, not the spec default."""
    _edit(_ci(world), "    file: ci/lint/python-lint.yml\n",
          "    file: ci/lint/python-lint.yml\n    inputs:\n      job_name: repo-python-lint\n")
    assert _run(world) == 1
    _edit(_ci(world), "    - job: python-lint", "    - job: repo-python-lint")
    assert _run(world) == 0


def test_job_defined_in_the_consumer_file_resolves(world):
    ci = _ci(world)
    ci.write_text(ci.read_text(encoding="utf-8") + "\nlocal-gate:\n  script:\n    - echo ok\n",
                  encoding="utf-8")
    _add_need(world, "local-gate")
    assert _run(world) == 0


def test_hidden_template_key_does_not_create_a_job(world, capsys):
    """A `.hidden` anchor is not a job, so a need on it never runs."""
    _edit(_template(world), '"$[[ inputs.job_name ]]":', '".$[[ inputs.job_name ]]":')
    assert _run(world) == 1
    assert "'python-lint' is an optional need" in capsys.readouterr().err


@pytest.mark.parametrize(
    "include, needle",
    [
        (REMOTE, "https://example.invalid/x.yml"),
        ("component: gitlab.example.com/org/comp/gate@1.0.0",
         "gitlab.example.com/org/comp/gate@1.0.0"),
    ],
    ids=["remote", "component"],
)
def test_undeclared_unreadable_include_exits_2_though_every_need_resolves(
    world, capsys, include, needle
):
    """Such an include hides its own needs, so a clean run proves nothing."""
    _add_include(world, include)
    assert _run(world) == 2
    err = capsys.readouterr().err
    assert "outside both checkouts" in err
    assert needle in err


def test_undeclared_remote_include_outranks_a_dangling_need(world, capsys):
    """The dangling need might be that include's job, so the verdict is exit 2."""
    _edit(_ci(world), NEED, "    - job: remote-gate\n      optional: true\n")
    _add_include(world, REMOTE)
    assert _run(world) == 2
    assert "outside both checkouts" in capsys.readouterr().err


def test_a_declared_unreadable_include_is_printed_and_passes(world, capsys):
    """The blind spot is accepted with a reason and named in the output."""
    _add_include(world, REMOTE)
    assert _run(world, ALLOW_REMOTE) == 0
    out = capsys.readouterr().out
    assert "not inspected: .gitlab-ci.yml: remote:" in out
    assert "vendor template, not ours" in out


def test_a_job_from_a_declared_unreadable_include_needs_extra_job(world, capsys):
    """Declaring the include keeps its jobs invisible: `--extra-job` names them."""
    _add_include(world, REMOTE)
    _add_need(world, "remote-gate")
    assert _run(world, ALLOW_REMOTE) == 1
    assert "'remote-gate' is an optional need" in capsys.readouterr().err
    assert _run(world, [*ALLOW_REMOTE, "--extra-job", "remote-gate=that remote file"]) == 0


def test_a_stale_include_allowance_fails(world, capsys):
    assert _run(world, ["--allow-unreadable-include", "gone.invalid=stale"]) == 1
    err = capsys.readouterr().err
    assert "--allow-unreadable-include names gone.invalid" in err
    assert "drop the stale entry" in err


def test_include_allowance_without_a_reason_exits_2(world, capsys):
    _add_include(world, REMOTE)
    assert _run(world, ["--allow-unreadable-include", "example.invalid"]) == 2
    assert "SUBSTRING=REASON" in capsys.readouterr().err


def test_included_file_that_is_absent_exits_2(world, capsys):
    _add_include(world, "local: .gitlab/ci/absent.yml")
    assert _run(world) == 2
    assert ".gitlab/ci/absent.yml is not in" in capsys.readouterr().err


def test_unparseable_included_file_exits_2(world, capsys):
    _write(world[0] / ".gitlab" / "ci" / "broken.yml", "gate: [unclosed\n")
    _add_include(world, "local: .gitlab/ci/broken.yml")
    assert _run(world) == 2
    assert "is not parseable" in capsys.readouterr().err


def test_extra_job_declares_a_job_from_a_source_the_gate_never_reads(world):
    _add_need(world, "child-gate")
    assert _run(
        world, ["--extra-job", "child-gate=emitted by the generated child pipeline"]
    ) == 0


def test_stale_extra_job_fails(world, capsys):
    assert _run(world, ["--extra-job", "ghost=gone"]) == 1
    assert "drop the stale entry" in capsys.readouterr().err


def test_extra_job_without_a_reason_exits_2(world, capsys):
    assert _run(world, ["--extra-job", "child-gate"]) == 2
    assert "JOB=REASON" in capsys.readouterr().err


def test_missing_lib_checkout_exits_2(world, capsys):
    assert GATE.main(["--repo-root", str(world[0])]) == 2
    assert "needs a library checkout" in capsys.readouterr().err


def test_input_with_no_default_and_no_value_is_reported(world, capsys):
    _edit(_template(world), "    job_name:\n      default: python-lint\n", "    job_name: {}\n")
    assert _run(world) == 1
    assert "neither passes nor defaults" in capsys.readouterr().err


def test_an_unresolvable_need_target_fails_on_its_own(world, capsys):
    """The only evidence is the key itself: every other need here resolves."""
    _edit(_template(world), "    stage:\n      default: lint\n",
          "    stage:\n      default: lint\n    gate: {}\n")
    _edit(_template(world), "  script:\n    - ruff check .\n",
          "  script:\n    - ruff check .\n  needs:\n"
          "    - job: $[[ inputs.gate ]]\n      optional: true\n")
    assert _run(world) == 1
    err = capsys.readouterr().err
    assert "the job set is incomplete" in err
    assert "optional need '$[[ inputs.gate ]]'" in err


def test_non_optional_need_is_left_alone(world):
    """GitLab already fails pipeline creation for a hard need naming nothing."""
    _edit(_ci(world), NEED, NEED + "    - job: hard-gate\n")
    assert _run(world) == 0


def test_missing_ci_file_exits_2(world, capsys):
    assert _run(world, ["--ci-file", ".gitlab-ci.yaml"]) == 2
    assert "does not exist" in capsys.readouterr().err


def test_pipeline_with_no_optional_need_can_be_required(world, capsys):
    _edit(_ci(world), "  needs:\n" + NEED, "")
    assert _run(world) == 0
    assert _run(world, ["--require-optional-needs"]) == 2
    assert "inspected nothing" in capsys.readouterr().err


def _child_gate(world, need: str) -> None:
    """A local include whose `child-gate` holds one optional need."""
    _write(
        world[0] / ".gitlab" / "ci" / "extra.yml",
        f"""\
        child-gate:
          script:
            - echo ok
          needs:
            - job: {need}
              optional: true
        """,
    )
    _add_include(world, "local: .gitlab/ci/extra.yml")


def test_optional_need_declared_inside_a_local_include_is_inspected(world, capsys):
    """A stale need inside an included template is the case GitLab hides."""
    _child_gate(world, "ghost-gate")
    assert _run(world) == 1
    err = capsys.readouterr().err
    assert "'ghost-gate' is an optional need" in err
    assert ".gitlab/ci/extra.yml:child-gate" in err


def test_a_local_include_and_its_own_need_resolve(world, capsys):
    _child_gate(world, "python-lint")
    _add_need(world, "child-gate")
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
    _add_include(world, "local: .gitlab/ci/extra.yml")
    assert _run(world) == 1
    err = capsys.readouterr().err
    assert "'ghost-gate' is an optional need" in err
    assert ".gitlab/ci/deep.yml:deep-gate" in err


def test_a_local_include_inside_a_library_file_resolves_in_the_library(world, capsys):
    """The nested `local:` belongs to the library checkout, not the consumer."""
    _edit(_template(world), "---\n", "---\ninclude:\n  - local: ci/lint/shared.yml\n")
    _write(
        world[1] / "ci" / "lint" / "shared.yml",
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
    _add_include(world, "local: .gitlab/ci/a.yml")
    assert _run(world) == 0
    assert "Optional needs OK" in capsys.readouterr().out


ARRAY_NEEDS_INPUT = (
    "    stage:\n      default: lint\n",
    "    stage:\n      default: lint\n    needs:\n      type: array\n      default: []\n",
)
GHOST_NEEDS_PASSED = (
    "    file: ci/lint/python-lint.yml\n",
    "    file: ci/lint/python-lint.yml\n"
    "    inputs:\n      needs:\n        - job: ghost-gate\n          optional: true\n",
)


def test_needs_passed_as_an_array_input_are_inspected(world, capsys):
    """`needs: $[[ inputs.needs ]]` takes the dependency from the include."""
    _edit(_template(world), *ARRAY_NEEDS_INPUT)
    _edit(_template(world), "  stage: $[[ inputs.stage ]]\n",
          "  stage: $[[ inputs.stage ]]\n  needs: $[[ inputs.needs ]]\n")
    _edit(_ci(world), *GHOST_NEEDS_PASSED)
    assert _run(world) == 1
    err = capsys.readouterr().err
    assert "'ghost-gate' is an optional need" in err
    assert "ci/lint/python-lint.yml:python-lint" in err
    _edit(_ci(world), "job: ghost-gate", "job: validation-gate")
    assert _run(world) == 0


def test_an_array_input_passed_on_to_a_nested_include_keeps_its_type(world, capsys):
    """Forwarding `$[[ inputs.needs ]]` hands the child the list, not its text."""
    _edit(_template(world), *ARRAY_NEEDS_INPUT)
    _edit(_template(world), "---\n",
          "---\ninclude:\n  - local: ci/lint/shared.yml\n"
          "    inputs:\n      needs: $[[ inputs.needs ]]\n")
    _write(
        world[1] / "ci" / "lint" / "shared.yml",
        """\
        spec:
          inputs:
            needs:
              type: array
              default: []
        ---
        shared-gate:
          script:
            - echo ok
          needs: $[[ inputs.needs ]]
        """,
    )
    _edit(_ci(world), *GHOST_NEEDS_PASSED)
    assert _run(world) == 1
    err = capsys.readouterr().err
    assert "'ghost-gate' is an optional need" in err
    assert "ci/lint/shared.yml:shared-gate" in err
    _edit(_ci(world), "job: ghost-gate", "job: validation-gate")
    assert _run(world) == 0


def test_needs_taken_from_a_reference_node_are_inspected(world, capsys):
    """`needs: !reference [...]` points at a hidden template's dependency list."""
    _edit(_ci(world), "  needs:\n" + NEED, "  needs: !reference [.gate-needs, needs]\n")
    ci = _ci(world)
    ci.write_text(
        ci.read_text(encoding="utf-8")
        + "\n.gate-needs:\n  needs:\n    - job: ghost-gate\n      optional: true\n",
        encoding="utf-8",
    )
    assert _run(world) == 1
    err = capsys.readouterr().err
    assert "'ghost-gate' is an optional need" in err
    assert ".gitlab-ci.yml:validation-gate" in err


def test_a_nested_include_resolves_its_inputs_against_the_parent(world, capsys):
    """The library file forwards its own input to the file it includes."""
    _edit(_template(world), "---\n",
          "---\ninclude:\n  - local: ci/lint/shared.yml\n"
          "    inputs:\n      gate_name: $[[ inputs.job_name ]]-shared\n")
    _write(
        world[1] / "ci" / "lint" / "shared.yml",
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
    _add_need(world, "python-lint-shared")
    assert _run(world) == 0
    assert "Optional needs OK" in capsys.readouterr().out


def _escaping_target(world) -> Path:
    """A readable CI file outside both checkouts, creating `escaped-gate`."""
    outside = world[0].parent / "outside" / "evil.yml"
    _write(
        outside,
        """\
        escaped-gate:
          script:
            - echo ok
        """,
    )
    _add_need(world, "escaped-gate")
    return outside


def test_local_include_climbing_out_of_the_repo_exits_2(world, capsys):
    """A `..` path leaves the project, so GitLab never creates its jobs."""
    _escaping_target(world)
    _add_include(world, "local: ../outside/evil.yml")
    assert _run(world) == 2
    err = capsys.readouterr().err
    assert "'../outside/evil.yml'" in err
    assert "outside the checkout" in err


def test_project_include_symlinked_out_of_the_library_exits_2(world, capsys):
    """The path stays inside the checkout; the link it names does not."""
    outside = _escaping_target(world)
    (world[1] / "ci" / "lint" / "linked.yml").symlink_to(outside)
    _edit(_ci(world), "    file: ci/lint/python-lint.yml\n",
          "    file: ci/lint/python-lint.yml\n"
          "  - project: org/weisssrv-lib\n    ref: v1.0.0\n    file: ci/lint/linked.yml\n")
    assert _run(world) == 2
    err = capsys.readouterr().err
    assert "'ci/lint/linked.yml'" in err
    assert "outside the checkout" in err


def test_a_symlinked_include_inside_the_checkout_resolves(world, capsys):
    """Containment is the test, not the link: this one stays in the repo."""
    ci_dir = world[0] / ".gitlab" / "ci"
    _write(
        ci_dir / "real.yml",
        """\
        child-gate:
          script:
            - echo ok
        """,
    )
    (ci_dir / "link.yml").symlink_to(ci_dir / "real.yml")
    _add_include(world, "local: .gitlab/ci/link.yml")
    _add_need(world, "child-gate")
    assert _run(world) == 0
    assert "Optional needs OK" in capsys.readouterr().out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
