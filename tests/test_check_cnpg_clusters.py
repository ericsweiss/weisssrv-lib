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
metadata: {name: app-store, namespace: app}
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
"""


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


def test_a_missing_object_store_fails(monkeypatch, capsys):
    assert _run(_corpus(CLUSTER, SCHEDULE), monkeypatch) == 1
    assert "does not ship" in capsys.readouterr().err


def test_short_retention_fails(monkeypatch, capsys):
    store = STORE.replace('"30d"', '"2w"')
    assert _run(_corpus(CLUSTER, store, SCHEDULE), monkeypatch) == 1
    assert "under the 30-day floor" in capsys.readouterr().err


def test_the_floor_is_a_flag(monkeypatch):
    assert _run(_corpus(CLUSTER, STORE, SCHEDULE), monkeypatch,
                ["--min-retention-days", "45"]) == 1


def test_a_cluster_without_a_scheduled_backup_fails(monkeypatch, capsys):
    assert _run(_corpus(CLUSTER, STORE), monkeypatch) == 1
    assert "no ScheduledBackup" in capsys.readouterr().err


def test_a_schedule_in_another_namespace_does_not_count(monkeypatch):
    schedule = SCHEDULE.replace("namespace: app}", "namespace: other}")
    assert _run(_corpus(CLUSTER, STORE, schedule), monkeypatch) == 1


def test_an_in_tree_archiver_with_retention_passes(monkeypatch):
    cluster = CLUSTER.replace(
        "  plugins:\n    - name: barman-cloud.cloudnative-pg.io\n      isWALArchiver: true\n"
        "      parameters:\n        barmanObjectName: app-store\n",
        "  backup:\n    retentionPolicy: \"30d\"\n    barmanObjectStore:\n"
        "      destinationPath: s3://bucket/\n",
    )
    assert _run(_corpus(cluster, SCHEDULE), monkeypatch) == 0


def test_more_than_one_instance_may_keep_its_pdb(monkeypatch):
    cluster = CLUSTER.replace("  instances: 1\n  enablePDB: false\n", "  instances: 3\n")
    assert _run(_corpus(cluster, STORE, SCHEDULE), monkeypatch) == 0


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
