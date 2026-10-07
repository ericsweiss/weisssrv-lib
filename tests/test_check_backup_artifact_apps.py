"""Tests for scripts/check-backup-artifact-apps.py, both drift directions.

Canonical suite: a consumer that vendors the script vendors this file too and
adds only its own smoke test over its committed host_vars and rules.
"""
from __future__ import annotations

from pathlib import Path

from script_loader import load_script

mod = load_script("check-backup-artifact-apps.py")


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


ARMLESS_RULES = """
              - alert: BackupArtifactStale
                expr: >-
                  (time() - backup_artifact_last_mtime_seconds > 180000)
                for: 1h
                labels:
                  severity: warning
"""


def test_an_empty_pairing_is_an_operator_error(tmp_path, capsys):
    """Nothing declared on either side certifies a contract never inspected."""
    assert mod.main(_files(tmp_path, "nas_storage_other: []\n", ARMLESS_RULES)) == 2
    assert "paired nothing" in capsys.readouterr().err


def test_an_empty_pairing_passes_when_the_flag_allows_it(tmp_path, capsys):
    argv = _files(tmp_path, "nas_storage_other: []\n", ARMLESS_RULES)
    assert mod.main(argv + ["--allow-empty"]) == 0
    assert "--allow-empty" in capsys.readouterr().out


UNRELATED_RULES = """
              - alert: Unrelated
                expr: up == 0
"""


def test_no_alert_at_all_passes_with_the_flag(tmp_path, capsys):
    """The state --allow-empty exists for: no artefacts, so no alert either."""
    argv = _files(tmp_path, "nas_storage_other: []\n", UNRELATED_RULES)
    assert mod.main(argv + ["--allow-empty"]) == 0
    assert "--allow-empty" in capsys.readouterr().out


def test_no_alert_at_all_without_the_flag_is_an_operator_error(tmp_path):
    assert mod.main(_files(tmp_path, "nas_storage_other: []\n", UNRELATED_RULES)) == 2


def test_no_alert_but_declared_apps_still_fails(tmp_path):
    host_vars = HOST_VARS + '  - name: pve-cluster\n    pattern: "etc-pve-*.tar.gz"\n'
    assert mod.main(_files(tmp_path, host_vars, UNRELATED_RULES) + ["--allow-empty"]) == 1


def test_a_missing_file_is_an_operator_error(tmp_path):
    argv = ["--host-vars", str(tmp_path / "absent.yml"), "--rules", str(tmp_path / "absent.yaml")]
    assert mod.main(argv) == 2


def test_a_rules_file_without_the_alert_is_an_operator_error(tmp_path, capsys):
    """A missing BackupArtifactStale rule is a broken gate (2), not drift (1)."""
    rules = RULES.replace("BackupArtifactStale", "SomethingElse")
    assert mod.main(_files(tmp_path, HOST_VARS, rules)) == 2
    assert "BackupArtifactStale" in capsys.readouterr().err


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
    assert "can never fire" in problems[0]


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


def test_a_for_less_alert_does_not_absorb_the_next_rules_arms():
    """`for:` is optional: when the guarded alert omits it, the scan must stop
    at the next alert declaration instead of counting its arms."""
    rules = """
        - alert: BackupArtifactStale
          expr: >-
            absent(backup_artifact_last_mtime_seconds{app="gitlab"})
        - alert: SomeOtherAlert
          expr: >-
            absent(backup_artifact_last_mtime_seconds{app="not-ours"})
          for: 30m
"""
    assert mod.alert_arm_apps(rules) == {"gitlab"}
