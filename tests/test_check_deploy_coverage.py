"""Unit tests for check-deploy-coverage.sh, the MR gate that fails when an
Ansible role/playbook/inventory change is picked up by no deploy-* CI job.
Driven via subprocess inside a throwaway git repo.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check-deploy-coverage.sh"

# One deploy-stage job maps the base role, one playbook and one inventory file.
# Everything else is unmapped unless the test's deploy-coverage.conf lists it.
FIXTURE_CI = textwrap.dedent(
    """\
    stages:
      - lint
      - deploy

    # A lint-stage job that ALSO mentions a role path — the script must NOT
    # credit this as deploy coverage (it filters on stage: deploy).
    deploy-coverage-check:
      stage: lint
      script:
        - bash scripts/check-deploy-coverage.sh
      rules:
        - changes:
            - ansible/roles/widget/**/*

    deploy-ansible-base:
      stage: deploy
      script:
        - echo deploy
      rules:
        - if: '$CI_COMMIT_BRANCH == "main"'
          changes:
            - ansible/roles/base/**/*
            - ansible/playbooks/base.yml
            - ansible/inventories/prod/group_vars/all.yml
    """
)


def _git(args, cwd):
    env = dict(os.environ)
    # Deterministic, no-config-dependent commits.
    env.update(
        {
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        }
    )
    res = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, env=env
    )
    assert res.returncode == 0, f"git {args} failed: {res.stderr}"
    return res


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repo with the fixture .gitlab-ci.yml and a copy of the script,
    committed as the base. Tests add a feature commit and diff against base."""
    r = tmp_path / "repo"
    r.mkdir()
    _git(["init", "-q", "-b", "main"], r)
    # Copy the real script under test into scripts/ so the path the fixture CI
    # references and the script's own self-reference both resolve.
    (r / "scripts").mkdir()
    shutil.copy(SCRIPT, r / "scripts" / "check-deploy-coverage.sh")
    shutil.copy(SCRIPT.parent / "ci_yaml.py", r / "scripts" / "ci_yaml.py")
    (r / ".gitlab-ci.yml").write_text(FIXTURE_CI)
    # Seed every literal the fixture CI names, so none of them reads as stale.
    _commit(r, "base", dict.fromkeys(
        ["ansible/roles/base/tasks/main.yml", "ansible/playbooks/base.yml",
         "ansible/inventories/prod/group_vars/all.yml"], "x\n"))
    return r


def _base_sha(repo: Path) -> str:
    return _git(["rev-parse", "HEAD"], repo).stdout.strip()


def _run(repo: Path, *args: str, **overrides: str):
    """Invoke the script with the CI_* base overrides scrubbed, then `overrides`."""
    env = dict(os.environ)
    for k in ("CI_MERGE_REQUEST_DIFF_BASE_SHA", "CI_COMMIT_BEFORE_SHA", "DEPLOY_COVERAGE_CONFIG"):
        env.pop(k, None)
    env.update(overrides)
    return subprocess.run(
        ["bash", "scripts/check-deploy-coverage.sh", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )


def _commit(repo: Path, message: str, files: dict) -> None:
    """Write `files` (repo path -> content) and commit them."""
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", message], repo)


def test_mapped_role_passes(repo: Path):
    """A change to a role a deploy job lists passes."""
    base = _base_sha(repo)
    _commit(repo, "edit base", {"ansible/roles/base/tasks/main.yml": "changed\n"})
    res = _run(repo, base)
    assert res.returncode == 0, f"expected pass, got: {res.stdout}\n{res.stderr}"
    assert "covered by at least one deploy-* job" in res.stdout


def test_unmapped_role_fails(repo: Path):
    """A change to a role no deploy job lists fails and names the role."""
    base = _base_sha(repo)
    _commit(repo, "add widget", {"ansible/roles/widget/tasks/main.yml": "new role\n"})
    res = _run(repo, base)
    assert res.returncode == 1, f"expected failure, got rc={res.returncode}"
    # widget is mentioned in the LINT job's rules but not a deploy job — the
    # stage-scoped parse must NOT credit that as coverage.
    assert "not mapped to any CI deploy job" in res.stderr
    assert "ansible/roles/widget/" in res.stderr


def test_intentionally_unmapped_role_honored(repo: Path):
    """A role in the config's [roles] section passes with no deploy mapping."""
    _commit(repo, "allowlist widget",
            {"scripts/deploy-coverage.conf": "[roles]\nwidget  # manual only\n"})

    base = _base_sha(repo)
    _commit(repo, "add widget", {"ansible/roles/widget/tasks/main.yml": "new role\n"})
    res = _run(repo, base)
    assert res.returncode == 0, (
        f"allowlisted role should pass, got rc={res.returncode}: "
        f"{res.stdout}\n{res.stderr}"
    )


def test_deleted_role_not_flagged(repo: Path):
    """Deleting an unmapped role is not flagged: nothing is left to roll out."""
    # First add an unmapped role and commit it as part of the base so the
    # deletion is a pure removal in the measured diff.
    _commit(repo, "add widget", {"ansible/roles/widget/tasks/main.yml": "doomed\n"})
    base = _base_sha(repo)

    # Now delete the whole role and diff base...HEAD: the only change is a
    # deletion, which the gate must ignore.
    shutil.rmtree(repo / "ansible/roles/widget")
    _commit(repo, "remove widget", {})
    res = _run(repo, base)
    assert res.returncode == 0, (
        f"deleted role must not be flagged, got rc={res.returncode}: "
        f"{res.stdout}\n{res.stderr}"
    )
    assert "widget" not in res.stderr


def test_changes_paths_dict_form_credited(repo: Path):
    """GitLab's `changes: {paths: [...]}` mapping form must confer the same
    coverage credit as the plain list form."""
    ci = (repo / ".gitlab-ci.yml").read_text() + textwrap.dedent(
        """\

        deploy-ansible-gadget:
          stage: deploy
          script:
            - echo deploy
          rules:
            - if: '$CI_COMMIT_BRANCH == "main"'
              changes:
                paths:
                  - ansible/roles/gadget/**/*
        """
    )
    _commit(repo, "map gadget via dict changes", {".gitlab-ci.yml": ci})

    base = _base_sha(repo)
    _commit(repo, "add gadget", {"ansible/roles/gadget/tasks/main.yml": "new role\n"})
    res = _run(repo, base)
    assert res.returncode == 0, (
        f"dict-form changes: should credit coverage, got rc={res.returncode}: "
        f"{res.stdout}\n{res.stderr}"
    )


def test_deleted_plus_added_unmapped_role_still_flags_addition(repo: Path):
    """A deleted role plus a different added unmapped role still flags the addition."""
    _commit(repo, "add widget", {"ansible/roles/widget/tasks/main.yml": "doomed\n"})
    base = _base_sha(repo)

    shutil.rmtree(repo / "ansible/roles/widget")
    _commit(repo, "swap widget for gadget",
            {"ansible/roles/gadget/tasks/main.yml": "new unmapped\n"})
    res = _run(repo, base)
    assert res.returncode == 1
    assert "ansible/roles/gadget/" in res.stderr
    assert "ansible/roles/widget/" not in res.stderr


def test_config_entry_without_rationale_is_rejected(repo: Path):
    """An entry with no trailing `# rationale` is a config error (exit 2), not a
    silent exemption — the rule the header states is machine-enforced."""
    _commit(repo, "bad config", {"scripts/deploy-coverage.conf": "[roles]\nwidget\n"})
    base = _base_sha(repo)
    _commit(repo, "add widget", {"ansible/roles/widget/tasks/main.yml": "new role\n"})
    res = _run(repo, base)
    assert res.returncode == 2, res.stdout + res.stderr
    assert "no '# rationale' comment" in res.stderr


def test_unknown_section_is_rejected(repo: Path):
    _commit(repo, "bad section", {"scripts/deploy-coverage.conf": "[bogus]\nx  # why\n"})
    base = _base_sha(repo)
    _commit(repo, "edit base", {"ansible/roles/base/tasks/main.yml": "changed\n"})
    res = _run(repo, base)
    assert res.returncode == 2
    assert "unknown section" in res.stderr


def test_settings_relocate_the_scanned_dirs(repo: Path):
    """A consumer with a different layout points the gate at its own dirs."""
    ci = (repo / ".gitlab-ci.yml").read_text().replace(
        "ansible/roles/base/**/*", "infra/roles/base/**/*"
    )
    _commit(repo, "relocate roles", {
        "scripts/deploy-coverage.conf": "[settings]\nroles_dir = infra/roles\n",
        ".gitlab-ci.yml": ci,
    })
    base = _base_sha(repo)

    _commit(repo, "edit relocated base", {"infra/roles/base/tasks/main.yml": "mapped\n"})
    assert _run(repo, base).returncode == 0

    base = _base_sha(repo)
    _commit(repo, "add relocated widget", {"infra/roles/widget/tasks/main.yml": "unmapped\n"})
    res = _run(repo, base)
    assert res.returncode == 1
    assert "infra/roles/widget/" in res.stderr


def test_shipped_example_config_parses(repo: Path):
    """The example config must load cleanly (rationales present, sections known)."""
    example = SCRIPT.parent.parent / "examples" / "deploy-coverage.example.conf"
    _commit(repo, "adopt example config", {"scripts/deploy-coverage.conf": example.read_text()})
    base = _base_sha(repo)
    _commit(repo, "edit base", {"ansible/roles/base/tasks/main.yml": "changed\n"})
    res = _run(repo, base)
    assert res.returncode == 0, res.stdout + res.stderr


def test_playbook_path_with_space_stays_one_entry(repo: Path):
    """A changed path containing whitespace must be reported as one unmapped
    entry, not split into two bogus ones."""
    base = _base_sha(repo)
    _commit(repo, "add spaced playbook", {"ansible/playbooks/my play.yml": "unmapped\n"})
    res = _run(repo, base)
    assert res.returncode == 1
    assert "ansible/playbooks/my play.yml" in res.stderr


def test_stale_literal_changes_path_fails(repo: Path):
    """A literal changes: entry that no longer exists stops triggering its job."""
    ci = (repo / ".gitlab-ci.yml").read_text() + textwrap.dedent(
        """\

        deploy-ansible-gadget:
          stage: deploy
          script:
            - echo deploy
          rules:
            - changes:
                - ansible/playbooks/renamed-away.yml
        """
    )
    _commit(repo, "list a path that does not exist", {".gitlab-ci.yml": ci})
    base = _base_sha(repo)
    _commit(repo, "edit base", {"ansible/roles/base/tasks/main.yml": "changed\n"})
    res = _run(repo, base)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "deploy-ansible-gadget" in res.stderr
    assert "ansible/playbooks/renamed-away.yml" in res.stderr


def test_stale_path_is_reported_with_no_ansible_changes(repo: Path):
    """The stale arm does not depend on the diff touching Ansible at all."""
    ci = (repo / ".gitlab-ci.yml").read_text() + textwrap.dedent(
        """\

        deploy-ansible-gadget:
          stage: deploy
          script:
            - echo deploy
          rules:
            - changes:
                - ansible/playbooks/renamed-away.yml
        """
    )
    _commit(repo, "list a path that does not exist", {".gitlab-ci.yml": ci})
    base = _base_sha(repo)
    _commit(repo, "unrelated change", {"README.md": "unrelated\n"})
    res = _run(repo, base)
    assert res.returncode == 1
    assert "ansible/playbooks/renamed-away.yml" in res.stderr


def test_a_glob_changes_path_is_not_read_as_stale(repo: Path):
    """`**/*` and friends match a tree, not a file: only literals are checked."""
    base = _base_sha(repo)
    _commit(repo, "unrelated change", {"README.md": "unrelated\n"})
    res = _run(repo, base)
    assert res.returncode == 0, res.stdout + res.stderr



# --- playbook and inventory arms ---------------------------------------------


@pytest.mark.parametrize(
    ("path", "summary"),
    [
        ("ansible/playbooks/base.yml", "Changed playbooks:       base.yml"),
        ("ansible/inventories/prod/group_vars/all.yml", "Changed inventory paths: group_vars/all.yml"),
    ],
)
def test_a_mapped_playbook_or_inventory_file_passes(repo: Path, path, summary):
    base = _base_sha(repo)
    _commit(repo, "edit", {path: "changed\n"})
    res = _run(repo, base)
    assert res.returncode == 0, res.stdout + res.stderr
    assert summary in res.stdout


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("ansible/playbooks/widget.yml", "changed playbooks are not mapped"),
        # An inventory file is unmapped at any depth.
        ("ansible/inventories/prod/host_vars/node-1/main.yml",
         "changed inventory paths are not mapped"),
    ],
)
def test_an_unmapped_playbook_or_inventory_file_fails(repo: Path, path, message):
    base = _base_sha(repo)
    _commit(repo, "add", {path: "new\n"})
    res = _run(repo, base)
    assert res.returncode == 1
    assert message in res.stderr
    assert path in res.stderr


@pytest.mark.parametrize(
    ("config", "path"),
    [
        ("[playbooks]\nmaint/widget.yml  # operator-run\n", "ansible/playbooks/maint/widget.yml"),
        ("[inventory]\nhosts.yml  # targets every deploy\n", "ansible/inventories/prod/hosts.yml"),
    ],
)
def test_an_allowlisted_playbook_or_inventory_file_is_honored(repo: Path, config, path):
    _commit(repo, "config", {"scripts/deploy-coverage.conf": config})
    base = _base_sha(repo)
    _commit(repo, "add", {path: "new\n"})
    res = _run(repo, base)
    assert res.returncode == 0, res.stdout + res.stderr


def test_a_non_yaml_inventory_file_is_not_an_inventory_change(repo: Path):
    base = _base_sha(repo)
    _commit(repo, "readme", {"ansible/inventories/prod/README.md": "x\n"})
    res = _run(repo, base)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "coverage arm is skipped" in res.stdout


# --- config parsing ----------------------------------------------------------


def test_every_setting_relocates_the_gate(repo: Path):
    """playbooks_dir, inventory_dir, ci_file, job_prefix and job_stage."""
    _commit(repo, "relocate", {
        "scripts/deploy-coverage.conf": (
            "[settings]\n"
            "playbooks_dir = plays\n"
            "inventory_dir = inv\n"
            "ci_file = ci/pipeline.yml\n"
            "job_prefix = rollout-\n"
            "job_stage = ship\n"
        ),
        "plays/site.yml": "x\n",
        "inv/vars.yml": "x\n",
        "ci/pipeline.yml": (
            "rollout-site:\n"
            "  stage: ship\n"
            "  script: [echo]\n"
            "  rules:\n"
            "    - changes:\n"
            "        - plays/site.yml\n"
            "        - inv/vars.yml\n"
        ),
    })
    base = _base_sha(repo)
    _commit(repo, "edit", {"plays/site.yml": "changed\n", "inv/vars.yml": "changed\n"})
    res = _run(repo, base)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "rollout-* job rule" in res.stdout

    base = _base_sha(repo)
    _commit(repo, "add", {"plays/other.yml": "x\n", "inv/other.yml": "x\n"})
    res = _run(repo, base)
    assert res.returncode == 1
    assert "plays/other.yml" in res.stderr
    assert "inv/other.yml" in res.stderr
    assert "ci/pipeline.yml" in res.stderr


def test_a_job_outside_the_deploy_stage_confers_no_coverage(repo: Path):
    """Only jobs in job_stage count, so moving it strips deploy-ansible-base."""
    _commit(repo, "config", {"scripts/deploy-coverage.conf": "[settings]\njob_stage = ship\n"})
    base = _base_sha(repo)
    _commit(repo, "edit base", {"ansible/roles/base/tasks/main.yml": "changed\n"})
    res = _run(repo, base)
    assert res.returncode == 1
    assert "ansible/roles/base/" in res.stderr


def test_an_unknown_setting_is_rejected(repo: Path):
    _commit(repo, "config", {"scripts/deploy-coverage.conf": "[settings]\nrole_dir = x\n"})
    res = _run(repo, _base_sha(repo))
    assert res.returncode == 2
    assert "unknown setting 'role_dir'" in res.stderr


def test_an_entry_before_any_section_is_rejected(repo: Path):
    _commit(repo, "config", {"scripts/deploy-coverage.conf": "widget  # orphaned\n"})
    res = _run(repo, _base_sha(repo))
    assert res.returncode == 2
    assert "entry before any [section]" in res.stderr


def test_the_config_path_comes_from_the_environment(repo: Path):
    _commit(repo, "config", {"ci/coverage.conf": "[roles]\nwidget  # manual only\n"})
    base = _base_sha(repo)
    _commit(repo, "add widget", {"ansible/roles/widget/tasks/main.yml": "x\n"})
    assert _run(repo, base).returncode == 1
    res = _run(repo, base, DEPLOY_COVERAGE_CONFIG="ci/coverage.conf")
    assert res.returncode == 0, res.stdout + res.stderr


# --- diff base ---------------------------------------------------------------

ZEROS = "0" * 40


@pytest.fixture
def unmapped_after_mapped(repo: Path):
    """Two commits: a mapped edit, then an unmapped role. Returns the base before
    each, so the chosen base decides whether the gate sees the unmapped role."""
    first = _base_sha(repo)
    _commit(repo, "edit base", {"ansible/roles/base/tasks/main.yml": "changed\n"})
    second = _base_sha(repo)
    _commit(repo, "add widget", {"ansible/roles/widget/tasks/main.yml": "x\n"})
    head = _base_sha(repo)
    return first, second, head


def test_the_merge_request_base_wins_over_the_argument(repo: Path, unmapped_after_mapped):
    first, _, head = unmapped_after_mapped
    res = _run(repo, head, CI_MERGE_REQUEST_DIFF_BASE_SHA=first)
    assert res.returncode == 1
    assert "ansible/roles/widget/" in res.stderr


def test_the_argument_wins_over_the_before_sha(repo: Path, unmapped_after_mapped):
    first, _, head = unmapped_after_mapped
    res = _run(repo, head, CI_COMMIT_BEFORE_SHA=first)
    assert res.returncode == 0, res.stdout + res.stderr


def test_the_before_sha_is_used_without_an_argument(repo: Path, unmapped_after_mapped):
    _, second, _ = unmapped_after_mapped
    res = _run(repo, CI_COMMIT_BEFORE_SHA=second)
    assert res.returncode == 1
    assert "ansible/roles/widget/" in res.stderr


def test_an_all_zeros_before_sha_falls_back_to_origin_main(repo: Path, unmapped_after_mapped):
    first, _, _ = unmapped_after_mapped
    _git(["update-ref", "refs/remotes/origin/main", first], repo)
    res = _run(repo, CI_COMMIT_BEFORE_SHA=ZEROS)
    assert res.returncode == 1
    assert "ansible/roles/widget/" in res.stderr


def test_an_unresolvable_base_is_an_error_not_an_empty_diff(repo: Path):
    res = _run(repo, "no-such-ref")
    assert res.returncode == 2
    assert "'no-such-ref' is not a valid git ref" in res.stderr


def test_a_missing_origin_main_is_an_error(repo: Path):
    res = _run(repo, CI_COMMIT_BEFORE_SHA=ZEROS)
    assert res.returncode == 2
    assert "'origin/main'" in res.stderr


def test_a_base_with_no_common_ancestor_is_an_error(repo: Path):
    _git(["checkout", "-q", "--orphan", "unrelated"], repo)
    _commit(repo, "unrelated root", {"other.txt": "x\n"})
    orphan = _base_sha(repo)
    _git(["checkout", "-q", "main"], repo)
    res = _run(repo, orphan)
    assert res.returncode == 2
    assert "shares no common ancestor" in res.stderr
