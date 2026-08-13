"""Tests for scripts/check-scrape-netpol.py."""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "check_scrape_netpol",
    Path(__file__).resolve().parent.parent / "scripts" / "check-scrape-netpol.py",
)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def _run(stdin_text: str, monkeypatch, argv: list[str] | None = None) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    return mod.main(argv or [])


DEFAULT_DENY = """
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: default-deny-ingress, namespace: ns}
spec:
  podSelector: {}
  policyTypes: [Ingress]
"""

OBS_ALLOW = """
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: allow-metrics-ingress, namespace: ns}
spec:
  podSelector: {}
  policyTypes: [Ingress]
  ingress:
    - from:
        - namespaceSelector:
            matchLabels: {kubernetes.io/metadata.name: observability}
      ports: [{protocol: TCP, port: 9090}]
"""

SERVICE_MONITOR = """
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata: {name: app, namespace: ns}
spec:
  selector: {matchLabels: {app: app}}
"""

# The !180 shape: the monitor is rendered by the chart, so only the HelmRelease
# values reveal it.
CHART_MONITOR = """
---
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata: {name: app, namespace: ns}
spec:
  values:
    app:
      podMonitor:
        enabled: true
"""


# An unrestricted namespace with a monitor: a scrape target the gate always
# passes, so a corpus prefixed with it is non-vacuous without changing what the
# test under it proves.
DECOY_SCRAPE = SERVICE_MONITOR.replace("namespace: ns}", "namespace: other}")


def test_scraped_namespace_without_observability_allow_fails(monkeypatch):
    assert _run(DEFAULT_DENY + SERVICE_MONITOR, monkeypatch) == 1


def test_scraped_namespace_with_observability_allow_passes(monkeypatch):
    assert _run(DEFAULT_DENY + OBS_ALLOW + SERVICE_MONITOR, monkeypatch) == 0


def test_chart_native_podmonitor_without_allow_fails(monkeypatch):
    """The regression that shipped in !180: monitor enabled via HelmRelease values."""
    assert _run(DEFAULT_DENY + CHART_MONITOR, monkeypatch) == 1


def test_chart_native_podmonitor_with_allow_passes(monkeypatch):
    assert _run(DEFAULT_DENY + OBS_ALLOW + CHART_MONITOR, monkeypatch) == 0


def test_lowercase_servicemonitor_spelling_is_detected(monkeypatch):
    """cert-manager spells it `prometheus.servicemonitor.enabled`."""
    lowercase = """
---
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata: {name: app, namespace: ns}
spec:
  values:
    prometheus:
      servicemonitor:
        enabled: true
"""
    assert _run(DEFAULT_DENY + lowercase, monkeypatch) == 1
    assert _run(DEFAULT_DENY + OBS_ALLOW + lowercase, monkeypatch) == 0


def test_disabled_chart_monitor_is_not_scraped(monkeypatch):
    disabled = CHART_MONITOR.replace("enabled: true", "enabled: false")
    # DECOY carries the corpus past the zero-scrape-targets guard, so this
    # asserts "ns is not scraped" rather than "nothing was scraped anywhere".
    assert _run(DECOY_SCRAPE + DEFAULT_DENY + disabled, monkeypatch) == 0


def test_unrestricted_namespace_needs_no_allow(monkeypatch):
    """No Ingress policy at all means nothing is denied — no allow required."""
    assert _run(SERVICE_MONITOR, monkeypatch) == 0


def test_egress_only_policy_does_not_restrict_ingress(monkeypatch):
    egress_only = """
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: allow-egress, namespace: ns}
spec:
  podSelector: {}
  policyTypes: [Egress]
  egress: [{}]
"""
    assert _run(egress_only + SERVICE_MONITOR, monkeypatch) == 0


def test_policy_without_policytypes_defaults_to_ingress(monkeypatch):
    """policyTypes is optional; a rules-only policy still denies by default."""
    implicit = """
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: allow-web, namespace: ns}
spec:
  podSelector: {}
  ingress:
    - from:
        - namespaceSelector:
            matchLabels: {kubernetes.io/metadata.name: traefik}
"""
    assert _run(implicit + SERVICE_MONITOR, monkeypatch) == 1


def test_matchexpressions_allow_is_accepted(monkeypatch):
    expr_allow = """
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: allow-metrics-ingress, namespace: ns}
spec:
  podSelector: {}
  policyTypes: [Ingress]
  ingress:
    - from:
        - namespaceSelector:
            matchExpressions:
              - key: kubernetes.io/metadata.name
                operator: In
                values: [observability, traefik]
"""
    assert _run(DEFAULT_DENY + expr_allow + SERVICE_MONITOR, monkeypatch) == 0


def test_allow_from_anywhere_satisfies_the_scrape(monkeypatch):
    allow_all = """
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: allow-web-ingress, namespace: ns}
spec:
  podSelector: {}
  policyTypes: [Ingress]
  ingress:
    - ports: [{protocol: TCP, port: 443}]
"""
    assert _run(DEFAULT_DENY + allow_all + SERVICE_MONITOR, monkeypatch) == 0


def test_namespaceselector_matchnames_attributes_to_the_target(monkeypatch):
    """A monitor in observability targeting `ns` requires the allow in `ns`."""
    remote = """
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata: {name: app, namespace: observability}
spec:
  namespaceSelector: {matchNames: [ns]}
  selector: {matchLabels: {app: app}}
"""
    assert _run(DEFAULT_DENY + remote, monkeypatch) == 1
    assert _run(DEFAULT_DENY + OBS_ALLOW + remote, monkeypatch) == 0


def test_any_namespaceselector_is_unattributable(monkeypatch):
    any_ns = """
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata: {name: app, namespace: observability}
spec:
  namespaceSelector: {any: true}
  selector: {matchLabels: {app: app}}
"""
    assert _run(DECOY_SCRAPE + DEFAULT_DENY + any_ns, monkeypatch) == 0


def test_pod_scoped_ingress_policy_does_not_mark_namespace_restricted(monkeypatch):
    """A podSelector-scoped policy denies only those pods, not the namespace."""
    scoped = """
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: allow-one-pod, namespace: ns}
spec:
  podSelector: {matchLabels: {app: other}}
  policyTypes: [Ingress]
  ingress: [{}]
"""
    assert _run(scoped + SERVICE_MONITOR, monkeypatch) == 0


def test_exempt_namespace_is_skipped(monkeypatch):
    argv = ["--exempt", "ns=external Endpoints only"]
    assert _run(DEFAULT_DENY + SERVICE_MONITOR, monkeypatch, argv) == 0


def test_exempt_without_a_reason_is_rejected(monkeypatch, capsys):
    """An operator error exits 2 — exit 1 means "a scrape is blocked"."""
    assert _run(DEFAULT_DENY + SERVICE_MONITOR, monkeypatch, ["--exempt", "ns"]) == 2
    assert "NS=REASON" in capsys.readouterr().err


def test_observability_namespace_is_configurable(monkeypatch):
    corpus = (DEFAULT_DENY + SERVICE_MONITOR + OBS_ALLOW).replace("observability", "metrics")
    assert _run(corpus, monkeypatch) == 1
    assert _run(corpus, monkeypatch, ["--observability-namespace", "metrics"]) == 0


def test_targetnamespace_overrides_helmrelease_namespace(monkeypatch):
    release = CHART_MONITOR.replace(
        "spec:\n  values:", "spec:\n  targetNamespace: ns\n  values:"
    ).replace("namespace: ns}", "namespace: flux-system}")
    assert _run(DEFAULT_DENY + release, monkeypatch) == 1
    assert _run(DEFAULT_DENY + OBS_ALLOW + release, monkeypatch) == 0


def test_empty_corpus_is_an_operator_error(monkeypatch, capsys):
    """Same contract as the two sibling gates fed by the same accumulated
    corpus: a broken pipe or a wrong kustomize path must not read as a pass."""
    assert _run("", monkeypatch) == 2
    assert "empty corpus" in capsys.readouterr().err


def test_unparseable_corpus_is_an_operator_error(monkeypatch, capsys):
    assert _run("a: [1\n", monkeypatch) == 2
    assert "failed to parse YAML input" in capsys.readouterr().err


def test_a_corpus_with_no_scrape_targets_is_an_operator_error(monkeypatch, capsys):
    """The render loop produced documents but never reached the stage defining
    the monitors, so every namespace went unexamined — the same zero-subjects
    arm check-secretstore-scope.py carries for a store-less corpus."""
    unrelated = """
---
apiVersion: v1
kind: ConfigMap
metadata: {name: settings, namespace: ns}
"""
    assert _run(unrelated, monkeypatch) == 2
    err = capsys.readouterr().err
    assert "0 scrape targets in 1 document(s)" in err
    assert "a gate that checks nothing is not a gate" in err


def test_scrape_targets_with_none_ingress_restricted_still_pass(monkeypatch, capsys):
    """Default-deny is a per-namespace choice, so `checked` may legitimately be
    0 while the corpus is genuinely non-vacuous. Both counts are reported."""
    assert _run(SERVICE_MONITOR, monkeypatch) == 0
    out = capsys.readouterr().out
    assert "0 scraped ingress-restricted" in out
    assert "1 scraped namespaces seen" in out


def test_an_empty_from_list_is_an_allow_all(monkeypatch):
    """`from: []` and an omitted `from` both match every source in the API;
    the empty-list spelling must not read as a blocked scrape."""
    allow_all_empty_from = """
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: allow-all, namespace: ns}
spec:
  podSelector: {}
  policyTypes: [Ingress]
  ingress:
    - from: []
"""
    assert _run(allow_all_empty_from + SERVICE_MONITOR, monkeypatch) == 0
