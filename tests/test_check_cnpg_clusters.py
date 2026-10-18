"""Tests for scripts/check-cnpg-clusters.py."""
from __future__ import annotations

import io

import pytest

from script_loader import load_script

mod = load_script("check-cnpg-clusters.py")

CLUSTER = """
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: app-db
  namespace: app
  annotations:
    kustomize.toolkit.fluxcd.io/prune: disabled
spec:
  instances: 1
  enablePDB: false
  plugins:
    - name: barman-cloud.cloudnative-pg.io
      isWALArchiver: true
      parameters:
        barmanObjectName: app-store
"""

STORE = """
apiVersion: barmancloud.cnpg.io/v1
kind: ObjectStore
metadata:
  name: app-store
  namespace: app
  annotations:
    kustomize.toolkit.fluxcd.io/prune: disabled
spec:
  retentionPolicy: "30d"
"""

SCHEDULE = """
apiVersion: postgresql.cnpg.io/v1
kind: ScheduledBackup
metadata: {name: app-db-nightly, namespace: app}
spec:
  cluster: {name: app-db}
  method: plugin
  pluginConfiguration: {name: barman-cloud.cloudnative-pg.io}
"""

IN_TREE = CLUSTER.replace(
    "  plugins:\n    - name: barman-cloud.cloudnative-pg.io\n      isWALArchiver: true\n"
    "      parameters:\n        barmanObjectName: app-store\n",
    "  backup:\n    retentionPolicy: \"30d\"\n    barmanObjectStore:\n"
    "      destinationPath: s3://bucket/\n",
)

SCHEDULE_IN_TREE = SCHEDULE.replace("  method: plugin\n", "").replace(
    "  pluginConfiguration: {name: barman-cloud.cloudnative-pg.io}\n", "")


def _corpus(*docs: str) -> str:
    return "---\n".join(docs)


def _run(stdin_text: str, monkeypatch, argv: list[str] | None = None) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    try:
        return mod.main(argv or [])
    except SystemExit as exc:
        return int(exc.code)


def test_a_complete_cluster_passes(monkeypatch, capsys):
    assert _run(_corpus(CLUSTER, STORE, SCHEDULE), monkeypatch) == 0
    assert "1 cluster(s)" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda c: c.replace("      isWALArchiver: true\n", ""), "no WAL archiver"),
        (lambda c: c.replace("  enablePDB: false\n", ""), "enablePDB: false"),
        (lambda c: c.replace("    kustomize.toolkit.fluxcd.io/prune: disabled\n", "    a: b\n"),
         "prune: disabled"),
    ],
)
def test_a_cluster_missing_a_setting_fails(monkeypatch, capsys, mutate, expected):
    assert _run(_corpus(mutate(CLUSTER), STORE, SCHEDULE), monkeypatch) == 1
    assert expected in capsys.readouterr().err


def test_a_disabled_barman_plugin_is_no_archiver(monkeypatch, capsys):
    cluster = CLUSTER.replace("      isWALArchiver: true\n", "      enabled: false\n      isWALArchiver: true\n")
    assert _run(_corpus(cluster, STORE, SCHEDULE), monkeypatch) == 1
    assert "no WAL archiver, so point-in-time recovery is impossible" in capsys.readouterr().err


def test_another_plugin_marked_wal_archiver_is_not_barman(monkeypatch, capsys):
    cluster = CLUSTER.replace("- name: barman-cloud.cloudnative-pg.io", "- name: other.example.com")
    assert _run(_corpus(cluster, STORE, SCHEDULE), monkeypatch) == 1
    assert "no WAL archiver" in capsys.readouterr().err


def test_a_missing_object_store_fails(monkeypatch, capsys):
    assert _run(_corpus(CLUSTER, SCHEDULE), monkeypatch) == 1
    assert "does not ship" in capsys.readouterr().err


def test_an_object_store_in_another_namespace_does_not_count(monkeypatch, capsys):
    store = STORE.replace("  namespace: app\n", "  namespace: other\n")
    assert _run(_corpus(CLUSTER, store, SCHEDULE), monkeypatch) == 1
    assert "does not ship" in capsys.readouterr().err


def test_short_retention_fails(monkeypatch, capsys):
    store = STORE.replace('"30d"', '"2w"')
    assert _run(_corpus(CLUSTER, store, SCHEDULE), monkeypatch) == 1
    assert "under the 30-day floor" in capsys.readouterr().err


def test_the_floor_is_a_flag(monkeypatch):
    assert _run(_corpus(CLUSTER, STORE, SCHEDULE), monkeypatch,
                ["--min-retention-days", "45"]) == 1


@pytest.mark.parametrize("days", ["0", "-5"])
def test_a_floor_under_one_day_is_an_operator_error(monkeypatch, capsys, days):
    assert _run(_corpus(CLUSTER, STORE, SCHEDULE), monkeypatch, ["--min-retention-days", days]) == 2
    assert "--min-retention-days must be at least 1" in capsys.readouterr().err


def test_a_cluster_without_a_scheduled_backup_fails(monkeypatch, capsys):
    assert _run(_corpus(CLUSTER, STORE), monkeypatch) == 1
    assert "no ScheduledBackup" in capsys.readouterr().err


def test_a_schedule_in_another_namespace_does_not_count(monkeypatch, capsys):
    schedule = SCHEDULE.replace("namespace: app}", "namespace: other}")
    assert _run(_corpus(CLUSTER, STORE, schedule), monkeypatch) == 1
    assert "no ScheduledBackup targets this cluster" in capsys.readouterr().err


def test_a_schedule_for_another_cluster_does_not_count(monkeypatch, capsys):
    schedule = SCHEDULE.replace("cluster: {name: app-db}", "cluster: {name: other-db}")
    assert _run(_corpus(CLUSTER, STORE, schedule), monkeypatch) == 1
    assert "no ScheduledBackup targets this cluster" in capsys.readouterr().err


def test_a_suspended_schedule_does_not_count(monkeypatch, capsys):
    schedule = SCHEDULE + "  suspend: true\n"
    assert _run(_corpus(CLUSTER, STORE, schedule), monkeypatch) == 1
    assert "no ScheduledBackup" in capsys.readouterr().err


def test_a_schedule_without_the_plugin_method_fails(monkeypatch, capsys):
    schedule = SCHEDULE.replace("  method: plugin\n", "")
    assert _run(_corpus(CLUSTER, STORE, schedule), monkeypatch) == 1
    assert "method: plugin" in capsys.readouterr().err


def test_a_schedule_naming_another_plugin_fails(monkeypatch, capsys):
    schedule = SCHEDULE.replace("{name: barman-cloud.cloudnative-pg.io}", "{name: other.example.com}")
    assert _run(_corpus(CLUSTER, STORE, schedule), monkeypatch) == 1
    assert "method: plugin" in capsys.readouterr().err


def test_a_prunable_object_store_fails(monkeypatch, capsys):
    store = STORE.replace("    kustomize.toolkit.fluxcd.io/prune: disabled\n", "    a: b\n")
    assert _run(_corpus(CLUSTER, store, SCHEDULE), monkeypatch) == 1
    err = capsys.readouterr().err
    assert "ObjectStore app-store" in err
    assert "prune: disabled" in err


def test_a_plugin_entry_naming_no_store_says_so(monkeypatch, capsys):
    cluster = CLUSTER.replace("      parameters:\n        barmanObjectName: app-store\n", "")
    assert _run(_corpus(cluster, STORE, SCHEDULE), monkeypatch) == 1
    err = capsys.readouterr().err
    assert "names no barmanObjectName" in err
    assert "'None'" not in err


def test_a_store_without_retention_names_no_window(monkeypatch, capsys):
    store = STORE.replace('  retentionPolicy: "30d"\n', "  configuration: {}\n")
    assert _run(_corpus(CLUSTER, store, SCHEDULE), monkeypatch) == 1
    err = capsys.readouterr().err
    assert "keeps no recovery window" in err
    assert "None" not in err


@pytest.mark.parametrize("instances", ['"${DB_INSTANCES}"', "null", "three", '"1"', "1.5", "true"])
def test_a_non_integer_instance_count_is_a_finding(monkeypatch, capsys, instances):
    cluster = CLUSTER.replace("  instances: 1\n", f"  instances: {instances}\n")
    assert _run(_corpus(cluster, STORE, SCHEDULE), monkeypatch) == 1
    err = capsys.readouterr().err
    assert "is not an integer (pipe the post-substitution build)" in err
    assert "Traceback" not in err


def test_an_in_tree_archiver_with_retention_passes(monkeypatch):
    assert _run(_corpus(IN_TREE, SCHEDULE_IN_TREE), monkeypatch) == 0


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        ('    retentionPolicy: "7d"\n', "backup.retentionPolicy keeps 7 day(s), under the 30-day floor"),
        ("", "backup.retentionPolicy keeps no recovery window"),
    ],
)
def test_an_in_tree_archiver_under_the_floor_fails(monkeypatch, capsys, policy, expected):
    cluster = IN_TREE.replace('    retentionPolicy: "30d"\n', policy)
    assert _run(_corpus(cluster, SCHEDULE_IN_TREE), monkeypatch) == 1
    assert expected in capsys.readouterr().err


def test_an_in_tree_archiver_needs_a_default_method_schedule(monkeypatch, capsys):
    assert _run(_corpus(IN_TREE, SCHEDULE), monkeypatch) == 1
    assert "no ScheduledBackup with method barmanObjectStore" in capsys.readouterr().err


def test_more_than_one_instance_may_keep_its_pdb(monkeypatch):
    cluster = CLUSTER.replace("  instances: 1\n  enablePDB: false\n", "  instances: 3\n")
    assert _run(_corpus(cluster, STORE, SCHEDULE), monkeypatch) == 0


@pytest.mark.parametrize("pdb", ["  enablePDB: true\n", ""])
def test_an_unset_instance_count_is_one_instance(monkeypatch, capsys, pdb):
    cluster = CLUSTER.replace("  instances: 1\n  enablePDB: false\n", pdb)
    assert _run(_corpus(cluster, STORE, SCHEDULE), monkeypatch) == 1
    assert "set enablePDB: false" in capsys.readouterr().err


def test_a_corpus_with_no_cluster_is_an_operator_error(monkeypatch, capsys):
    assert _run(STORE, monkeypatch) == 2
    assert "0 CloudNativePG Clusters" in capsys.readouterr().err


def test_an_empty_corpus_is_an_operator_error(monkeypatch):
    assert _run("", monkeypatch) == 2


@pytest.mark.parametrize(
    ("policy", "days"),
    [("30d", 30), ("5w", 35), ("1m", 30), ("thirty", None), ("", None), (None, None)],
)
def test_retention_days_reads_barman_recovery_windows(policy, days):
    assert mod.retention_days(policy) == days
