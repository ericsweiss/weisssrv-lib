"""Tests for scripts/check-molecule-image-pin.py.

Every arm runs against a fixture tree: the gate's value is that it FAILS on a
stale tag, so each rule has a case that makes it fail.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from script_loader import load_script

gate = load_script("check-molecule-image-pin.py")

REGISTRY = "registry.example.com/eric/weisssrv-lib/molecule-test"


def molecule_yml(tag: str) -> str:
    return textwrap.dedent(
        """\
        driver:
          name: docker
        platforms:
          - name: instance
            image: ${MOLECULE_TEST_IMAGE:-%s:%s}
        """
        % (REGISTRY, tag)
    )


def build_repo(tmp_path: Path, ref: str, scenarios: dict) -> Path:
    (tmp_path / ".gitlab-ci.yml").write_text(
        "variables:\n  WEISSSRV_LIB_REF: %s\n" % ref, encoding="utf-8"
    )
    for scenario, tag in scenarios.items():
        suite, name = scenario.split("/", 1)
        path = tmp_path / "ansible/integration-tests" / suite / "molecule" / name
        path.mkdir(parents=True)
        (path / "molecule.yml").write_text(molecule_yml(tag), encoding="utf-8")
    return tmp_path


def test_a_matching_pin_passes(tmp_path):
    repo = build_repo(tmp_path, "v1.2.3", {"dns-stack/default": "v1.2.3"})
    assert gate.check("v1.2.3", repo) == ([], 1)


def test_a_stale_tag_fails(tmp_path):
    repo = build_repo(tmp_path, "v1.2.3", {"dns-stack/default": "v1.0.0"})
    problems, seen = gate.check("v1.2.3", repo)
    assert seen == 1
    assert len(problems) == 1
    assert "'v1.0.0'" in problems[0]


def test_a_non_default_scenario_name_is_covered(tmp_path):
    repo = build_repo(tmp_path, "v1.2.3", {"dns-stack/upgrade": "v1.0.0"})
    problems, _ = gate.check("v1.2.3", repo)
    assert len(problems) == 1
    assert "upgrade/molecule.yml" in problems[0]


def test_no_literal_is_an_operator_error_not_a_pass(tmp_path):
    repo = build_repo(tmp_path, "v1.2.3", {})
    assert gate.check("v1.2.3", repo) == ([], 0)
    assert gate.main(["--ci-file", str(repo / ".gitlab-ci.yml")]) == 2


def test_a_local_sentinel_is_not_read_as_a_stale_pin(tmp_path):
    """`:local` carries no `v` tag, so it is not a pin the gate may rewrite."""
    repo = build_repo(tmp_path, "v1.2.3", {})
    scenario = repo / "ansible/integration-tests/dns/molecule/default"
    scenario.mkdir(parents=True)
    (scenario / "molecule.yml").write_text(
        "platforms:\n  - image: %s:local\n" % REGISTRY, encoding="utf-8"
    )
    assert gate.check("v1.2.3", repo) == ([], 0)


def test_an_unrelated_tag_on_another_image_is_untouched(tmp_path):
    """The regex is anchored on the image path, so a sibling pin is not rewritten."""
    repo = build_repo(tmp_path, "v1.2.3", {"dns/default": "v1.2.3"})
    other = repo / "ansible/TESTING.md"
    other.write_text("ghcr.io/other/app:v9.9.9\n", encoding="utf-8")
    assert gate.check("v1.2.3", repo) == ([], 1)
    assert gate.fix("v1.2.3", repo) == 0
    assert "v9.9.9" in other.read_text(encoding="utf-8")


def test_the_project_segment_is_a_parameter(tmp_path):
    """A consumer of a differently named library passes --project."""
    repo = build_repo(tmp_path, "v1.2.3", {})
    scenario = repo / "ansible/integration-tests/dns/molecule/default"
    scenario.mkdir(parents=True)
    (scenario / "molecule.yml").write_text(
        "platforms:\n  - image: reg.example/acme/infra-lib/molecule-test:v1.0.0\n",
        encoding="utf-8",
    )
    assert gate.check("v1.2.3", repo) == ([], 0)
    problems, seen = gate.check("v1.2.3", repo, project="acme/infra-lib")
    assert seen == 1 and len(problems) == 1


def test_fix_rewrites_every_source(tmp_path):
    repo = build_repo(
        tmp_path, "v1.2.3", {"dns/default": "v1.0.0", "mail/upgrade": "v1.1.0"}
    )
    assert gate.fix("v1.2.3", repo) == 2
    assert gate.check("v1.2.3", repo) == ([], 2)


def test_ci_file_selects_the_tree_that_is_read_and_rewritten(tmp_path):
    repo = build_repo(tmp_path, "v1.2.3", {"dns/default": "v1.0.0"})
    ci = str(repo / ".gitlab-ci.yml")
    assert gate.main(["--ci-file", ci]) == 1
    assert gate.main(["--ci-file", ci, "--fix"]) == 0
    rendered = repo / "ansible/integration-tests/dns/molecule/default/molecule.yml"
    assert "v1.2.3" in rendered.read_text(encoding="utf-8")


def test_a_non_release_ref_is_an_operator_error(tmp_path):
    repo = build_repo(tmp_path, "main", {"dns/default": "v1.2.3"})
    with pytest.raises(gate.OperatorError):
        gate.declared_ref(repo / ".gitlab-ci.yml")
    assert gate.main(["--ci-file", str(repo / ".gitlab-ci.yml")]) == 2


def test_a_missing_ref_variable_is_an_operator_error(tmp_path):
    (tmp_path / ".gitlab-ci.yml").write_text("stages: [lint]\n", encoding="utf-8")
    assert gate.main(["--ci-file", str(tmp_path / ".gitlab-ci.yml")]) == 2


def test_a_reference_tag_in_the_pipeline_file_still_parses(tmp_path):
    """GitLab's `!reference` would crash a plain SafeLoader before the ref is read."""
    (tmp_path / ".gitlab-ci.yml").write_text(
        "variables:\n  WEISSSRV_LIB_REF: v1.2.3\n"
        "job:\n  script: !reference [.base, script]\n",
        encoding="utf-8",
    )
    assert gate.declared_ref(tmp_path / ".gitlab-ci.yml") == "v1.2.3"


def test_a_ref_var_override_is_honoured(tmp_path):
    (tmp_path / ".gitlab-ci.yml").write_text(
        "variables:\n  OTHER_LIB_REF: v2.0.0\n", encoding="utf-8"
    )
    assert gate.declared_ref(tmp_path / ".gitlab-ci.yml", "OTHER_LIB_REF") == "v2.0.0"
