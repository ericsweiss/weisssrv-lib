"""Tests for scripts/check-pvc-storageclass.py."""
from __future__ import annotations

import io

import pytest

from script_loader import load_script

mod = load_script("check-pvc-storageclass.py")


def _run(stdin_text: str, monkeypatch) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    try:
        return mod.main()
    except SystemExit as exc:  # the shared corpus loader exits 2 directly
        return int(exc.code)


PVC_STATIC = """
apiVersion: v1
kind: PersistentVolumeClaim
metadata: {name: loki-data, namespace: observability}
spec:
  storageClassName: ""
  accessModes: [ReadWriteOnce]
  resources: {requests: {storage: 75Gi}}
"""

# No storageClassName: the DefaultStorageClass admission plugin rewrites it to
# local-path at create time.
PVC_UNPINNED = """
apiVersion: v1
kind: PersistentVolumeClaim
metadata: {name: loki-data, namespace: observability}
spec:
  accessModes: [ReadWriteOnce]
  resources: {requests: {storage: 75Gi}}
"""

STS_PINNED = """
apiVersion: apps/v1
kind: StatefulSet
metadata: {name: loki, namespace: observability}
spec:
  volumeClaimTemplates:
    - metadata: {name: storage}
      spec:
        storageClassName: ""
        resources: {requests: {storage: 75Gi}}
"""

STS_UNPINNED = STS_PINNED.replace('        storageClassName: ""\n', "")

HR_PINNED = """
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata: {name: loki, namespace: observability}
spec:
  values:
    singleBinary:
      persistence:
        enabled: true
        size: 75Gi
        storageClass: "-"
"""

HR_UNPINNED = HR_PINNED.replace('        storageClass: "-"\n', "")


# The template pattern: the cluster's tenant StorageClass and a size.
CNPG_PINNED = """
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata: {name: app-db, namespace: app}
spec:
  instances: 1
  storage:
    storageClass: tenant-local
    size: 20Gi
"""

# `size` alone: CNPG renders a claim with no class, which the default
# StorageClass captures exactly like an unpinned PVC.
CNPG_UNPINNED = """
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata: {name: app-db, namespace: app}
spec:
  instances: 1
  storage:
    size: 20Gi
"""


PVC_VOLUME_BOUND = """
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: pg-dump
  namespace: app
  annotations:
    kustomize.toolkit.fluxcd.io/prune: disabled
spec:
  storageClassName: ""
  volumeName: pg-dump-pv
  accessModes: [ReadWriteOnce]
  resources: {requests: {storage: 10Gi}}
"""

PVC_VOLUME_BOUND_PRUNABLE = PVC_VOLUME_BOUND.replace(
    "  annotations:\n    kustomize.toolkit.fluxcd.io/prune: disabled\n", ""
)


PVC_NULL_CLASS = PVC_UNPINNED.replace(
    "spec:\n", "spec:\n  storageClassName: null\n"
)

HR_VALUES_WITHOUT_PERSISTENCE = """
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata: {name: thing, namespace: ns}
spec:
  values:
    resources: {limits: {memory: 128Mi}}
"""


def _with_static(doc: str) -> str:
    """PVC_STATIC carries the corpus past the zero-claims guard."""
    return PVC_STATIC + "---\n" + doc


CASES = [
    ("static-pvc", PVC_STATIC, 0, ()),
    ("unpinned-pvc", PVC_UNPINNED, 1, ()),
    ("named-class", PVC_STATIC.replace('storageClassName: ""', "storageClassName: nfs-downloads"),
     0, ()),
    ("null-class", PVC_NULL_CLASS, 1, ("no storageClassName",)),
    ("sts-pinned", STS_PINNED, 0, ()),
    # volumeClaimTemplates are immutable, so this must be caught before apply.
    ("sts-unpinned", STS_UNPINNED, 1, ()),
    ("hr-pinned", HR_PINNED, 0, ()),
    # The chart renders the PVC server-side, so only the values can be linted.
    ("hr-unpinned", HR_UNPINNED, 1, ()),
    ("hr-null-class", HR_PINNED.replace('storageClass: "-"', "storageClass: null"),
     1, ("but no storageClass ",)),
    ("hr-existing-claim", HR_PINNED.replace('storageClass: "-"', "existingClaim: app-data"),
     0, ()),
    ("hr-empty-existing-claim", HR_PINNED.replace('storageClass: "-"', 'existingClaim: ""'),
     1, ()),
    ("hr-disabled-persistence", _with_static(HR_UNPINNED.replace("enabled: true", "enabled: false")),
     0, ()),
    ("hr-no-persistence-block", _with_static(HR_VALUES_WITHOUT_PERSISTENCE), 0, ()),
    ("cnpg-pinned", CNPG_PINNED, 0, ()),
    ("cnpg-size-only", CNPG_UNPINNED, 1,
     ("app/Cluster/app-db spec.storage", "name the provisioner's class")),
    ("cnpg-static-pvc-template", CNPG_UNPINNED.replace(
        "    size: 20Gi", '    size: 20Gi\n    pvcTemplate:\n      storageClassName: ""'), 0, ()),
    ("cnpg-class-wins-over-template", CNPG_PINNED.replace(
        "    size: 20Gi", "    size: 20Gi\n    pvcTemplate:\n      storageClassName: null"), 0, ()),
    ("cnpg-wal", CNPG_PINNED + "  walStorage:\n    size: 5Gi\n", 1, ("spec.walStorage",)),
    ("cnpg-tablespace",
     CNPG_PINNED + "  tablespaces:\n    - name: hot\n      storage:\n        size: 1Gi\n",
     1, ("app/Cluster/app-db tablespace 'hot'",)),
    ("cluster-of-another-group",
     _with_static(CNPG_UNPINNED.replace("postgresql.cnpg.io/v1", "example.com/v1")), 0, ()),
    ("volume-bound-prune-disabled", PVC_VOLUME_BOUND, 0, ()),
    # Pruning releases the PV, and a Released volume never rebinds unaided.
    ("volume-bound-prunable", PVC_VOLUME_BOUND_PRUNABLE, 1,
     ("pg-dump-pv", "kustomize.toolkit.fluxcd.io/prune")),
    *[(f"volume-bound-prune-{value}",
       PVC_VOLUME_BOUND.replace("prune: disabled", f"prune: {value!r}"), 1, ())
      for value in ("enabled", "true", "Disabled", "false")],
    *[(f"volume-name-{volume}", PVC_VOLUME_BOUND_PRUNABLE.replace(
        "volumeName: pg-dump-pv", f"volumeName: {volume}"), 0, ())
      for volume in ('""', "null")],
    ("prune-and-class-both-reported",
     PVC_VOLUME_BOUND_PRUNABLE.replace('  storageClassName: ""\n', ""), 1,
     ("no storageClassName", "pg-dump-pv")),
]


@pytest.mark.parametrize("corpus, code, needles", [c[1:] for c in CASES],
                         ids=[c[0] for c in CASES])
def test_claim_shape(monkeypatch, capsys, corpus, code, needles):
    assert _run(corpus, monkeypatch) == code
    err = capsys.readouterr().err
    for needle in needles:
        assert needle in err


def test_empty_corpus_is_an_operator_error(monkeypatch, capsys):
    """This gate takes no arguments, so a mis-piped invocation has no other
    symptom — and its two sibling gates on the same corpus hold the same
    contract."""
    assert _run("", monkeypatch) == 2
    assert "empty corpus" in capsys.readouterr().err


def test_a_corpus_with_no_claims_is_an_operator_error(monkeypatch, capsys):
    """The render loop produced documents but never reached a stage declaring
    storage — the same zero-subjects arm check-secretstore-scope.py carries for
    a store-less corpus."""
    unrelated = """
apiVersion: v1
kind: ConfigMap
metadata: {name: settings, namespace: ns}
"""
    assert _run(unrelated, monkeypatch) == 2
    err = capsys.readouterr().err
    assert "0 claims in 1 document(s)" in err
    assert "a gate that checks nothing is not a gate" in err


def test_the_success_line_reports_the_claims_it_checked(monkeypatch, capsys):
    """The count is what distinguishes a real pass from a vacuous one."""
    assert _run(PVC_STATIC + "---\n" + HR_PINNED, monkeypatch) == 0
    assert "2 claim(s) across 2 document(s)" in capsys.readouterr().out
