"""scripts/check-deploy-preflight.py — argv parsing and the failure paths."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from script_loader import SCRIPTS, load_script

SCRIPT = SCRIPTS / "check-deploy-preflight.py"

preflight = load_script("check-deploy-preflight.py")


# --- end to end ----------------------------------------------------------

PIPELINE = """
.deploy-base:
  stage: deploy
deploy-base-role:
  extends: .deploy-base
  script:
    - ansible-playbook -i inventories/prod site.yml --tags %s
"""


def _repo(tmp_path: Path, pipeline: str, playbook: str = "site.yml") -> Path:
    (tmp_path / "ansible").mkdir()
    if playbook:
        (tmp_path / "ansible" / playbook).write_text("---\n", encoding="utf-8")
    (tmp_path / ".gitlab-ci.yml").write_text(pipeline, encoding="utf-8")
    return tmp_path


HOSTS_BANNER = "      play #1 (all): all\n        hosts (2):"


def _fake_ansible(tmp_path: Path, stdout: str, rc: int = 0) -> dict:
    """A stub ansible-playbook on PATH; the real one needs a full collection."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "ansible-playbook"
    stub.write_text(
        "#!/bin/sh\ncat <<'OUT'\n%s\nOUT\nexit %d\n" % (stdout, rc), encoding="utf-8"
    )
    stub.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = "%s%s%s" % (bin_dir, os.pathsep, env["PATH"])
    return env


def _run(cwd: Path, env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd, env=env, capture_output=True, text=True,
    )


def test_a_tag_that_selects_a_task_passes(tmp_path):
    repo = _repo(tmp_path, PIPELINE % "base")
    env = _fake_ansible(tmp_path, HOSTS_BANNER + "\n        TAGS: [base, ssh]")
    proc = _run(repo, env)
    assert proc.returncode == 0, proc.stderr
    assert "1 playbook(s), 1 tag selection(s)" in proc.stdout


def test_a_skip_tag_matching_no_task_leaves_the_selection_intact(tmp_path):
    repo = _repo(tmp_path, PIPELINE % "base --skip-tags reboot")
    env = _fake_ansible(tmp_path, HOSTS_BANNER + "\n        TAGS: [base, ssh]")
    proc = _run(repo, env)
    assert proc.returncode == 0, proc.stderr
    assert "1 tag selection(s) checked" in proc.stdout


def test_the_jobs_skip_tags_reach_the_list_tasks_run(tmp_path):
    """Without them, a tag whose every task the job skips scores as selected."""
    repo = _repo(tmp_path, PIPELINE % "base --skip-tags reboot")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_log = tmp_path / "argv.log"
    stub = bin_dir / "ansible-playbook"
    # Prints a selected task only when the job's skip list was NOT passed on,
    # so the gate goes red exactly when the skip reaches ansible-playbook.
    stub.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> {argv_log}\n'
        f'echo "{HOSTS_BANNER}"\n'
        'case "$*" in *--skip-tags*) ;; *) echo "        TAGS: [base, ssh]" ;; esac\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = "%s%s%s" % (bin_dir, os.pathsep, env["PATH"])
    proc = _run(repo, env)
    assert proc.returncode == 1, proc.stdout
    assert "selects NO task" in proc.stderr
    assert "--skip-tags reboot" in argv_log.read_text()


def test_a_tag_that_selects_no_task_fails(tmp_path):
    repo = _repo(tmp_path, PIPELINE % "base")
    env = _fake_ansible(tmp_path, HOSTS_BANNER + "\n        TAGS: [always]")
    proc = _run(repo, env)
    assert proc.returncode == 1
    assert "selects NO task" in proc.stderr


def test_a_playbook_that_does_not_exist_fails(tmp_path):
    repo = _repo(tmp_path, PIPELINE % "base", playbook="")
    env = _fake_ansible(tmp_path, "")
    proc = _run(repo, env)
    assert proc.returncode == 1
    assert "does not exist" in proc.stderr


def test_a_pipeline_with_no_deploy_job_fails_rather_than_passing_vacuously(tmp_path):
    repo = _repo(tmp_path, "lint:\n  script:\n    - echo hi\n")
    env = _fake_ansible(tmp_path, "")
    proc = _run(repo, env)
    assert proc.returncode == 1
    assert "resolved 0 playbooks" in proc.stderr


def test_a_tagless_deploy_job_passes_by_default_and_fails_when_required(tmp_path):
    pipeline = """
.deploy-base:
  stage: deploy
deploy-all:
  extends: .deploy-base
  script:
    - ansible-playbook -i inventories/prod site.yml
"""
    repo = _repo(tmp_path, pipeline)
    env = _fake_ansible(tmp_path, HOSTS_BANNER)
    assert _run(repo, env).returncode == 0
    proc = _run(repo, env, "--require-tag-selections")
    assert proc.returncode == 1
    assert "0 tag selections" in proc.stderr


def test_an_unparseable_job_fails_instead_of_shrinking_the_check(tmp_path, monkeypatch):
    """A written invocation the parser cannot read must be loud."""
    monkeypatch.setattr(preflight, "parse_invocations", lambda text: [])
    failures = preflight.check(
        _repo(tmp_path, PIPELINE % "base") / ".gitlab-ci.yml",
        tmp_path / "ansible", [".deploy-base"], False,
    )
    assert any("parsed 0 of 1" in f for f in failures)


def test_a_missing_pipeline_file_is_an_operator_error(tmp_path):
    assert preflight.main(["--ci-file", str(tmp_path / "nope.yml")]) == 2


# --- the --ansible-dir seam ----------------------------------------------

RETARGET_PIPELINE = """
.deploy-base:
  stage: deploy
deploy-acme:
  extends: .deploy-base
  script:
    - ansible-playbook -i inventories/prod playbooks/acme.yml --tags acme_certs
"""


def _retargeted(tmp_path: Path, with_playbook: bool) -> tuple[Path, Path]:
    """A pipeline and an ansible root under neither default name, so only
    --ci-file plus --ansible-dir can reach them."""
    pipeline = tmp_path / "pipeline.yml"
    pipeline.write_text(RETARGET_PIPELINE, encoding="utf-8")
    ansible = tmp_path / "elsewhere"
    (ansible / "playbooks").mkdir(parents=True)
    if with_playbook:
        (ansible / "playbooks" / "acme.yml").write_text("---\n", encoding="utf-8")
    return pipeline, ansible


def test_a_playbook_absent_under_the_given_ansible_dir_fails(tmp_path, monkeypatch):
    """--ansible-dir is the only way a consumer retargets the gate; untested, it
    could resolve against the default root and report any tree clean."""
    pipeline, ansible = _retargeted(tmp_path, with_playbook=False)
    env = _fake_ansible(tmp_path, HOSTS_BANNER + "\n        TAGS: [acme_certs]")
    monkeypatch.setenv("PATH", env["PATH"])
    assert preflight.main(
        ["--ci-file", str(pipeline), "--ansible-dir", str(ansible)]
    ) == 1


def test_a_playbook_and_tag_resolvable_under_the_given_ansible_dir_passes(
    tmp_path, monkeypatch
):
    pipeline, ansible = _retargeted(tmp_path, with_playbook=True)
    env = _fake_ansible(tmp_path, HOSTS_BANNER + "\n        TAGS: [acme_certs]")
    monkeypatch.setenv("PATH", env["PATH"])
    assert preflight.main(
        ["--ci-file", str(pipeline), "--ansible-dir", str(ansible)]
    ) == 0


def test_a_missing_ansible_dir_is_an_operator_error(tmp_path):
    pipeline, _ = _retargeted(tmp_path, with_playbook=False)
    assert preflight.main(
        ["--ci-file", str(pipeline), "--ansible-dir", str(tmp_path / "nope")]
    ) == 2


def test_a_job_extending_several_parents_is_still_inspected(tmp_path):
    pipeline = """
.deploy-base:
  stage: deploy
.retryable:
  retry: 1
deploy-all:
  extends: [.retryable, .deploy-base]
  script:
    - ansible-playbook -i inventories/prod site.yml --tags base
"""
    repo = _repo(tmp_path, pipeline)
    env = _fake_ansible(tmp_path, HOSTS_BANNER + "\n        TAGS: [base]")
    assert _run(repo, env).returncode == 0


LIMIT_PIPELINE = """
.deploy-base:
  stage: deploy
deploy-proxmox:
  extends: .deploy-base
  script:
    - ansible-playbook -i inventories/prod site.yml --limit proxmox
"""


def test_a_limit_that_intersects_every_play_to_zero_hosts_fails(tmp_path):
    repo = _repo(tmp_path, LIMIT_PIPELINE)
    env = _fake_ansible(tmp_path, "      play #1 (dns): dns\n        hosts (0):")
    proc = _run(repo, env)
    assert proc.returncode == 1
    assert "matches NO host" in proc.stderr


def test_a_limit_that_matches_a_play_passes(tmp_path):
    repo = _repo(tmp_path, LIMIT_PIPELINE)
    env = _fake_ansible(
        tmp_path, "      play #1 (proxmox): proxmox\n        hosts (2):"
    )
    assert _run(repo, env).returncode == 0


def test_a_limit_whose_list_hosts_output_has_no_banner_fails(tmp_path):
    """A banner shape PLAY_HOSTS cannot read must not read as a clean check."""
    repo = _repo(tmp_path, LIMIT_PIPELINE)
    env = _fake_ansible(tmp_path, "some other banner shape")
    proc = _run(repo, env)
    assert proc.returncode == 1
    assert "no `hosts (N):` line" in proc.stderr


REFERENCE_PIPELINE = """
.deploy-base:
  stage: deploy
deploy-proxmox:
  extends: .deploy-base
  script:
    - !reference [.absent-template, script]
"""


def test_a_script_reference_into_an_included_file_fails(tmp_path):
    """The job's real `ansible-playbook` call lives where the parse cannot see
    it, so reporting the job clean would check nothing."""
    repo = _repo(tmp_path, REFERENCE_PIPELINE)
    env = _fake_ansible(tmp_path, "")
    proc = _run(repo, env)
    assert proc.returncode == 1
    assert "resolved to nothing" in proc.stderr


LIMITLESS_PIPELINE = """
.deploy-base:
  stage: deploy
deploy-dns:
  extends: .deploy-base
  script:
    - ansible-playbook -i inventories/prod dns.yml
"""


def test_a_limitless_call_whose_plays_resolve_to_zero_hosts_fails(tmp_path):
    """The majority shape: a play naming a group the inventory no longer has."""
    repo = _repo(tmp_path, LIMITLESS_PIPELINE, playbook="dns.yml")
    env = _fake_ansible(tmp_path, "      play #1 (dns): dns\n        hosts (0):")
    proc = _run(repo, env)
    assert proc.returncode == 1
    assert "its own hosts: patterns" in proc.stderr
    assert "matches NO host" in proc.stderr


def test_a_limitless_call_whose_plays_resolve_hosts_passes(tmp_path):
    repo = _repo(tmp_path, LIMITLESS_PIPELINE, playbook="dns.yml")
    env = _fake_ansible(tmp_path, "      play #1 (dns): dns\n        hosts (2):")
    assert _run(repo, env).returncode == 0


def test_a_missing_ansible_playbook_is_an_operator_error(tmp_path):
    """An image without ansible-playbook must not read as a silent no-op."""
    repo = _repo(tmp_path, PIPELINE % "base")
    env = dict(os.environ)
    env["PATH"] = str(tmp_path / "nonexistent")
    proc = _run(repo, env)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "ansible-playbook" in proc.stderr
    assert "PATH" in proc.stderr


def test_a_limit_matching_no_inventory_host_fails(tmp_path):
    repo = _repo(tmp_path, LIMIT_PIPELINE)
    env = _fake_ansible(tmp_path, "ERROR! Specified --limit does not match", rc=1)
    proc = _run(repo, env)
    assert proc.returncode == 1
    assert "--list-hosts failed" in proc.stderr


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))


INHERITED_SCRIPT_PIPELINE = """
.deploy-base:
  stage: deploy
  script:
    - ansible-playbook -i inventories/prod site.yml --tags base
deploy-inherited:
  extends: .deploy-base
"""

INVISIBLE_SCRIPT_PIPELINE = """
deploy-inherited:
  extends: .deploy-base
"""


def test_a_job_inheriting_its_invocation_from_a_parent_is_still_inspected(tmp_path):
    """The parent is in this file, so the chain resolves and the job's real
    invocation is checked rather than read as an empty script."""
    repo = _repo(tmp_path, INHERITED_SCRIPT_PIPELINE)
    env = _fake_ansible(tmp_path, HOSTS_BANNER + "\n        TAGS: [base]")
    assert _run(repo, env).returncode == 0


def test_a_job_whose_parent_is_in_an_included_file_fails(tmp_path):
    """With nothing to parse the job would be silently exempt from every
    playbook, tag and limit check."""
    repo = _repo(tmp_path, INVISIBLE_SCRIPT_PIPELINE)
    env = _fake_ansible(tmp_path, "")
    proc = _run(repo, env)
    assert proc.returncode == 1
    assert "no script to inspect" in proc.stderr
    assert ".deploy-base" in proc.stderr
