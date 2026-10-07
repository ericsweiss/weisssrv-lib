"""Tests for scripts/check-ingressroute-backends.py."""
from __future__ import annotations

import io

from script_loader import load_script

mod = load_script("check-ingressroute-backends.py")


def _run(stdin_text: str, monkeypatch, *argv: str) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    try:
        return mod.main(list(argv))
    except SystemExit as exc:  # the shared corpus loader exits 2 directly
        return int(exc.code)


SERVICE = """
---
apiVersion: v1
kind: Service
metadata:
  name: demo
  namespace: apps
  labels: {app.kubernetes.io/name: demo}
spec:
  ports:
    - name: http
      port: 8080
"""

ROUTE = """
---
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata: {name: demo, namespace: apps}
spec:
  routes:
    - match: Host(`demo.example.com`)
      services:
        - name: demo
          port: 8080
"""

MONITOR = """
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata: {name: demo, namespace: apps}
spec:
  selector:
    matchLabels: {app.kubernetes.io/name: demo}
  endpoints:
    - port: http
      path: /metrics
"""


def test_a_resolving_corpus_passes(monkeypatch, capsys):
    assert _run(SERVICE + ROUTE + MONITOR, monkeypatch) == 0
    assert "references OK" in capsys.readouterr().out


def test_a_renamed_service_fails(monkeypatch, capsys):
    corpus = SERVICE + ROUTE.replace("        - name: demo", "        - name: demo-web")
    assert _run(corpus, monkeypatch) == 1
    assert "names no Service in the corpus" in capsys.readouterr().err


def test_a_backend_port_the_service_does_not_declare_fails(monkeypatch, capsys):
    corpus = SERVICE + ROUTE.replace("port: 8080", "port: 8081")
    assert _run(corpus, monkeypatch) == 1
    err = capsys.readouterr().err
    assert "neither a port nor a port name" in err
    assert "8080" in err


def test_a_backend_may_name_the_port_instead_of_the_number(monkeypatch):
    corpus = SERVICE + ROUTE.replace("port: 8080", "port: http")
    assert _run(corpus, monkeypatch) == 0


def test_a_servicemonitor_port_that_is_no_port_name_fails(monkeypatch, capsys):
    """`endpoints[].port` is a port NAME, so the Service number does not satisfy it."""
    corpus = SERVICE + ROUTE + MONITOR.replace("- port: http", "- port: metrics")
    assert _run(corpus, monkeypatch) == 1
    assert "no port NAME" in capsys.readouterr().err


def test_an_unnamed_service_port_cannot_satisfy_a_monitor(monkeypatch):
    corpus = (
        SERVICE.replace("    - name: http\n", "") + ROUTE + MONITOR
    )
    assert _run(corpus, monkeypatch) == 1


def test_a_monitor_selecting_nothing_in_the_corpus_is_skipped(monkeypatch):
    """A monitor for another repo's Service is not this corpus's finding."""
    corpus = SERVICE + ROUTE + MONITOR.replace("demo}", "other}")
    assert _run(corpus, monkeypatch) == 0


def test_a_backend_outside_the_rendered_namespaces_is_not_a_finding(monkeypatch):
    """Out of scope, so nothing resolves and the vacuity guard speaks instead."""
    corpus = SERVICE + ROUTE.replace(
        "        - name: demo\n", "        - name: traefik\n          namespace: kube-system\n"
    )
    assert _run(corpus, monkeypatch) == 2


def test_a_cross_namespace_backend_is_resolved_when_the_corpus_has_it(
    monkeypatch, capsys
):
    other = SERVICE.replace("namespace: apps", "namespace: edge").replace(
        "name: demo", "name: edge-svc"
    )
    corpus = SERVICE + other + ROUTE.replace(
        "        - name: demo\n", "        - name: missing\n          namespace: edge\n"
    )
    assert _run(corpus, monkeypatch) == 1
    assert "edge/missing" in capsys.readouterr().err


def test_a_traefikservice_backend_is_not_a_service_reference(monkeypatch):
    corpus = SERVICE + MONITOR + ROUTE.replace(
        "        - name: demo\n          port: 8080\n",
        "        - name: weighted\n          kind: TraefikService\n",
    )
    assert _run(corpus, monkeypatch) == 0


def test_an_ingressroutetcp_backend_is_covered(monkeypatch, capsys):
    corpus = SERVICE + ROUTE.replace("kind: IngressRoute", "kind: IngressRouteTCP").replace(
        "port: 8080", "port: 9999"
    )
    assert _run(corpus, monkeypatch) == 1
    assert "IngressRouteTCP" in capsys.readouterr().err


def test_a_targetport_endpoint_is_not_a_name_reference(monkeypatch):
    corpus = SERVICE + ROUTE + MONITOR.replace("- port: http", "- targetPort: 9100")
    assert _run(corpus, monkeypatch) == 0


def test_a_monitor_matchnames_namespace_is_honoured(monkeypatch, capsys):
    other = SERVICE.replace("namespace: apps", "namespace: edge")
    corpus = other + ROUTE.replace("port: 8080", "port: http") + MONITOR.replace(
        "  selector:",
        "  namespaceSelector:\n    matchNames: [edge]\n  selector:",
    ).replace("- port: http", "- port: metrics")
    assert _run(corpus, monkeypatch) == 1
    assert "edge/demo" in capsys.readouterr().err


def test_a_corpus_with_no_reference_is_an_operator_error(monkeypatch, capsys):
    assert _run(SERVICE, monkeypatch) == 2
    assert "resolved 0 references" in capsys.readouterr().err


def test_an_empty_corpus_is_an_operator_error(monkeypatch):
    assert _run("", monkeypatch) == 2


CHART_ROUTE = ROUTE.replace("        - name: demo", "        - name: demo-chart")


def test_an_allowed_backend_passes(monkeypatch, capsys):
    """A credited backend still counts, so the zero-references guard stays honest."""
    corpus = SERVICE + CHART_ROUTE
    assert _run(
        corpus, monkeypatch, "--allow-backend", "apps/demo-chart=rendered by its chart"
    ) == 0
    out = capsys.readouterr().out
    assert "apps/demo-chart" in out
    assert "rendered by its chart" in out


def test_an_unlisted_missing_backend_still_fails(monkeypatch, capsys):
    corpus = SERVICE + MONITOR + CHART_ROUTE
    assert _run(
        corpus, monkeypatch, "--allow-backend", "apps/other=rendered by its chart"
    ) == 1
    assert "apps/demo-chart names no Service" in capsys.readouterr().err


def test_an_allowed_backend_without_a_reason_is_an_operator_error(monkeypatch, capsys):
    corpus = SERVICE + MONITOR + CHART_ROUTE
    assert _run(corpus, monkeypatch, "--allow-backend", "apps/demo-chart=") == 2
    assert "NAMESPACE/NAME=REASON" in capsys.readouterr().err


def test_an_allowed_backend_without_a_namespace_is_an_operator_error(
    monkeypatch, capsys
):
    corpus = SERVICE + MONITOR + CHART_ROUTE
    assert _run(corpus, monkeypatch, "--allow-backend", "demo-chart=why") == 2
    assert "NAMESPACE/NAME=REASON" in capsys.readouterr().err
