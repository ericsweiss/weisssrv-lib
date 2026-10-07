"""Tests for scripts/check-issuer-refs.py."""
from __future__ import annotations

import io

import pytest

from script_loader import load_script

mod = load_script("check-issuer-refs.py")


def _run(stdin_text: str, monkeypatch, *argv: str) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    try:
        return mod.main(list(argv))
    except SystemExit as exc:  # the shared corpus loader exits 2 directly
        return int(exc.code)


CLUSTER_ISSUER = """
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata: {name: letsencrypt-prod}
spec: {acme: {server: https://example.invalid/directory}}
"""

CERT = """
apiVersion: cert-manager.io/v1
kind: Certificate
metadata: {name: wildcard, namespace: traefik}
spec:
  secretName: wildcard-tls
  issuerRef: {kind: ClusterIssuer, name: letsencrypt-prod}
"""

CERT_TYPO = CERT.replace("letsencrypt-prod", "letsencrypt-production")

INGRESS = """
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: app
  namespace: apps
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
spec: {rules: []}
"""

NAMESPACED_ISSUER = """
apiVersion: cert-manager.io/v1
kind: Issuer
metadata: {name: selfsigned, namespace: traefik}
spec: {selfSigned: {}}
"""

CERT_NAMESPACED = """
apiVersion: cert-manager.io/v1
kind: Certificate
metadata: {name: internal, namespace: traefik}
spec:
  secretName: internal-tls
  issuerRef: {name: selfsigned}
"""

EXTERNAL_GROUP_CERT = """
apiVersion: cert-manager.io/v1
kind: Certificate
metadata: {name: pca, namespace: apps}
spec:
  secretName: pca-tls
  issuerRef: {kind: AWSPCAClusterIssuer, name: pca, group: awspca.cert-manager.io}
"""


def test_a_resolving_cluster_issuer_reference_passes(monkeypatch):
    assert _run(CLUSTER_ISSUER + "---" + CERT + "---" + INGRESS, monkeypatch) == 0


def test_a_certificate_naming_an_absent_cluster_issuer_fails(monkeypatch, capsys):
    """The mutation: one typo in the issuer name leaves the cert Pending."""
    assert _run(CLUSTER_ISSUER + "---" + CERT_TYPO, monkeypatch) == 1
    err = capsys.readouterr().err
    assert "letsencrypt-production" in err
    assert "does not ship" in err


def test_an_ingress_annotation_naming_an_absent_cluster_issuer_fails(monkeypatch, capsys):
    corpus = CLUSTER_ISSUER + "---" + INGRESS.replace("letsencrypt-prod", "staging")
    assert _run(corpus, monkeypatch) == 1
    assert "cert-manager.io/cluster-issuer" in capsys.readouterr().err


def test_an_omitted_kind_resolves_against_the_namespaced_issuers(monkeypatch):
    """cert-manager defaults issuerRef.kind to Issuer, not ClusterIssuer."""
    assert _run(NAMESPACED_ISSUER + "---" + CERT_NAMESPACED, monkeypatch) == 0


def test_a_namespaced_issuer_in_another_namespace_does_not_resolve(monkeypatch, capsys):
    corpus = NAMESPACED_ISSUER.replace("namespace: traefik", "namespace: other")
    assert _run(corpus + "---" + CERT_NAMESPACED, monkeypatch) == 1
    assert "own namespace" in capsys.readouterr().err


def test_a_cluster_issuer_name_does_not_satisfy_a_namespaced_reference(monkeypatch):
    """Kinds are distinct objects; a ClusterIssuer is not an Issuer."""
    corpus = CLUSTER_ISSUER + "---" + CERT_NAMESPACED.replace("selfsigned",
                                                              "letsencrypt-prod")
    assert _run(corpus, monkeypatch) == 1


def test_another_issuer_implementation_is_not_inspected(monkeypatch):
    """A non-cert-manager group names an object this corpus would never hold."""
    assert _run(CLUSTER_ISSUER + "---" + CERT + "---" + EXTERNAL_GROUP_CERT,
                monkeypatch) == 0


def test_an_externally_installed_issuer_is_exempt_with_a_reason(monkeypatch):
    assert _run(CERT, monkeypatch,
                "--allow-external", "letsencrypt-prod=shipped by the chart") == 0


def test_an_exemption_without_a_reason_is_an_operator_error(monkeypatch):
    assert _run(CERT, monkeypatch, "--allow-external", "letsencrypt-prod") == 2


def test_a_corpus_with_no_issuer_reference_is_an_operator_error(monkeypatch, capsys):
    """A gate that inspected nothing must not report a pass."""
    assert _run(CLUSTER_ISSUER, monkeypatch) == 2
    assert "0 issuer references" in capsys.readouterr().err


def test_an_empty_corpus_is_an_operator_error(monkeypatch):
    assert _run("", monkeypatch) == 2


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
