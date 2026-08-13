"""Tests for scripts/check-pvc-storageclass.py."""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "check_pvc_storageclass",
    Path(__file__).resolve().parent.parent / "scripts" / "check-pvc-storageclass.py",
)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def _run(stdin_text: str, monkeypatch) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    return mod.main()


PVC_STATIC = """
apiVersion: v1
kind: PersistentVolumeClaim
metadata: {name: loki-data, namespace: observability}
spec:
  storageClassName: ""
  accessModes: [ReadWriteOnce]
  resources: {requests: {storage: 75Gi}}
"""

# The 2026-07 incident shape: no storageClassName, so the DefaultStorageClass
# admission plugin rewrites it to local-path at create time.
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


def test_static_pvc_passes(monkeypatch):
    assert _run(PVC_STATIC, monkeypatch) == 0


def test_unpinned_pvc_fails(monkeypatch):
    assert _run(PVC_UNPINNED, monkeypatch) == 1


def test_named_class_passes(monkeypatch):
    """An explicit named class is a deliberate choice, not a fall-through."""
    doc = PVC_STATIC.replace('storageClassName: ""', "storageClassName: nfs-downloads")
    assert _run(doc, monkeypatch) == 0


def test_pinned_volume_claim_template_passes(monkeypatch):
    assert _run(STS_PINNED, monkeypatch) == 0


def test_unpinned_volume_claim_template_fails(monkeypatch):
    """volumeClaimTemplates are immutable — this must be caught before apply."""
    assert _run(STS_UNPINNED, monkeypatch) == 1


def test_helmrelease_persistence_with_class_passes(monkeypatch):
    assert _run(HR_PINNED, monkeypatch) == 0


def test_helmrelease_persistence_without_class_fails(monkeypatch):
    """The chart renders the PVC server-side, so only the values can be linted."""
    assert _run(HR_UNPINNED, monkeypatch) == 1


def test_helmrelease_existing_claim_passes(monkeypatch):
    """existingClaim binds a PVC this repo already pins (authentik postgres)."""
    doc = HR_UNPINNED.replace("        size: 75Gi", "        size: 75Gi\n        existingClaim: loki-data")
    assert _run(doc, monkeypatch) == 0


def test_disabled_persistence_block_is_ignored(monkeypatch):
    doc = HR_UNPINNED.replace("enabled: true", "enabled: false")
    # PVC_STATIC carries the corpus past the zero-claims guard, so this asserts
    # "the disabled block is not a claim", not "no claim was found anywhere".
    assert _run(PVC_STATIC + "---\n" + doc, monkeypatch) == 0


def test_unrelated_size_key_is_not_a_persistence_block(monkeypatch):
    """A `size` under a non-persistence key still names no class — but the
    block is only flagged when it is not explicitly disabled, and a values tree
    with neither `size` nor `enabled` must never trip it."""
    doc = """
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata: {name: thing, namespace: ns}
spec:
  values:
    resources: {limits: {memory: 128Mi}}
"""
    assert _run(PVC_STATIC + "---\n" + doc, monkeypatch) == 0


def test_list_wrapped_resources_are_expanded(monkeypatch):
    doc = """
apiVersion: v1
kind: List
items:
  - apiVersion: v1
    kind: PersistentVolumeClaim
    metadata: {name: a, namespace: ns}
    spec:
      resources: {requests: {storage: 1Gi}}
"""
    assert _run(doc, monkeypatch) == 1


def test_malformed_yaml_is_an_operator_error(monkeypatch, capsys):
    """Exit 2, not 1 — exit 1 means "a claim is unpinned"."""
    monkeypatch.setattr("sys.stdin", io.StringIO("a: [1,\nb: {"))
    assert mod.main() == 2
    assert "failed to parse YAML input" in capsys.readouterr().err


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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))


def test_an_explicit_null_storageclass_is_unpinned(monkeypatch, capsys):
    """`storageClassName: null` deserializes as unset, so the default
    StorageClass captures the claim exactly like a missing key."""
    pvc_null = """
apiVersion: v1
kind: PersistentVolumeClaim
metadata: {name: cache, namespace: ns}
spec:
  storageClassName: null
  accessModes: [ReadWriteOnce]
  resources: {requests: {storage: 1Gi}}
"""
    assert _run(pvc_null, monkeypatch) == 1
    assert "no storageClassName" in capsys.readouterr().err
