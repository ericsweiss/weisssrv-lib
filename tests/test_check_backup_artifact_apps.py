"""Tests for scripts/check-backup-artifact-apps.py.

The collector's app list (NAS host_vars) and BackupArtifactStale's
per-app absent() arms (Flux-reconciled PrometheusRule) are edited on separate
lifecycles; this gate is what keeps them paired. Exercises both drift
directions.

CANONICAL SUITE. A consumer that vendors the script vendors this file too and
adds only its own smoke test — that its committed host_vars and rules agree.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_SCRIPT = REPO / "scripts" / "check-backup-artifact-apps.py"

_spec = importlib.util.spec_from_file_location("check_backup_artifact_apps", _SCRIPT)
assert _spec and _spec.loader
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


HOST_VARS = """
nas_storage_backup_artifact_apps:
  - name: authentik
    pattern: "authentik-*.sql.gz"
  - name: gitlab
    pattern: "*_gitlab_backup.tar"
"""

RULES = """
              - alert: BackupArtifactStale
                expr: >-
                  (time() - backup_artifact_last_mtime_seconds > 180000)
                  or absent(backup_artifact_last_mtime_seconds{app="authentik"})
                  or absent(backup_artifact_last_mtime_seconds{app="gitlab"})
                for: 1h
                labels:
                  severity: warning
              - alert: SomethingElse
                expr: absent(backup_artifact_last_mtime_seconds{app="not-an-arm"})
                for: 1h
"""


def test_collector_apps_reads_the_name_key():
    assert mod.collector_apps(HOST_VARS) == {"authentik", "gitlab"}


def test_alert_arms_are_scoped_to_the_alert_block():
    """A later rule mentioning the same metric must not be read as an arm."""
    assert mod.alert_arm_apps(RULES) == {"authentik", "gitlab"}


def test_app_without_an_arm_is_drift():
    host_vars = HOST_VARS + '  - name: pve-cluster\n    pattern: "etc-pve-*.tar.gz"\n'
    assert mod.collector_apps(host_vars) - mod.alert_arm_apps(RULES) == {"pve-cluster"}


def test_arm_without_an_app_is_drift():
    rules = RULES.replace(
        '                  for: 1h', '                  for: 1h', 1
    ).replace(
        '                  or absent(backup_artifact_last_mtime_seconds{app="gitlab"})',
        '                  or absent(backup_artifact_last_mtime_seconds{app="gitlab"})\n'
        '                  or absent(backup_artifact_last_mtime_seconds{app="retired"})',
    )
    assert mod.alert_arm_apps(rules) - mod.collector_apps(HOST_VARS) == {"retired"}


def _files(tmp_path, host_vars: str, rules: str):
    (tmp_path / "host_vars.yml").write_text(host_vars)
    (tmp_path / "rules.yaml").write_text(rules)
    return ["--host-vars", str(tmp_path / "host_vars.yml"), "--rules", str(tmp_path / "rules.yaml")]


def test_matching_sets_exit_zero(tmp_path):
    assert mod.main(_files(tmp_path, HOST_VARS, RULES)) == 0


def test_a_missing_arm_exits_one(tmp_path):
    host_vars = HOST_VARS + '  - name: pve-cluster\n    pattern: "etc-pve-*.tar.gz"\n'
    assert mod.main(_files(tmp_path, host_vars, RULES)) == 1


def test_a_missing_file_is_an_operator_error(tmp_path):
    argv = ["--host-vars", str(tmp_path / "absent.yml"), "--rules", str(tmp_path / "absent.yaml")]
    assert mod.main(argv) == 2


COMPANION_RULE = """
              - alert: BackupArtifactCompanionMissing
                expr: >-
                  backup_artifact_companion_present == 0
                  or backup_artifact_companion_size_bytes == 0
                for: 1h
"""


def test_companions_are_read_only_when_declared():
    """An app with no `companions:` key is a claim of self-containment, not an
    empty companion set — it must not appear in the map at all."""
    host_vars = HOST_VARS + '    companions: ["gitlab-secrets.json", "gitlab.rb"]\n'
    assert mod.collector_companions(host_vars) == {
        "gitlab": ["gitlab-secrets.json", "gitlab.rb"]
    }
    assert mod.collector_companions(HOST_VARS) == {}


def test_a_companion_rule_with_no_declared_companions_is_a_violation():
    """The alert keys on a metric family the collector emits only per declared
    companion, so shipping it with none declared is a rule that can never fire."""
    problems = mod.check_companions(HOST_VARS, COMPANION_RULE, Path("host_vars.yml"), Path("rules.yaml"))
    assert len(problems) == 1
    assert "can NEVER" in problems[0]


def test_declared_companions_with_the_rule_present_are_clean():
    host_vars = HOST_VARS + '    companions: ["gitlab-secrets.json"]\n'
    assert mod.check_companions(host_vars, COMPANION_RULE, Path("host_vars.yml"), Path("rules.yaml")) == []


def test_declared_companions_with_no_rule_are_a_violation():
    """The other direction: series emitted with nothing alerting on them."""
    host_vars = HOST_VARS + '    companions: ["gitlab-secrets.json"]\n'
    problems = mod.check_companions(host_vars, "              - alert: Unrelated\n", Path("h.yml"), Path("r.yaml"))
    assert len(problems) == 1
    assert "does not exist" in problems[0]


def test_neither_declared_nor_alerted_is_clean():
    """Companions are opt-in: a cluster using none is a valid state."""
    assert mod.check_companions(HOST_VARS, "              - alert: Unrelated\n", Path("h.yml"), Path("r.yaml")) == []


def test_a_commented_out_alert_does_not_satisfy_the_gate():
    assert not mod.alert_exists("          # - alert: BackupArtifactCompanionMissing\n", "BackupArtifactCompanionMissing")


def test_a_prefix_matching_alert_name_does_not_satisfy_the_gate():
    assert not mod.alert_exists("          - alert: BackupArtifactCompanionMissingLegacy\n", "BackupArtifactCompanionMissing")


def test_the_exact_active_alert_satisfies_the_gate_quoted_or_bare():
    assert mod.alert_exists('          - alert: "BackupArtifactCompanionMissing"\n', "BackupArtifactCompanionMissing")
    assert mod.alert_exists("          - alert: BackupArtifactCompanionMissing\n", "BackupArtifactCompanionMissing")
