"""Tests for scripts/check-scrape-netpol.py."""
from __future__ import annotations

import io

from script_loader import load_script

mod = load_script("check-scrape-netpol.py")


def _run(stdin_text: str, monkeypatch, argv: list[str] | None = None) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    try:
        return mod.main(argv or [])
    except SystemExit as exc:  # the shared corpus loader exits 2 directly
        return int(exc.code)


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

# Monitor rendered by the chart: only the HelmRelease values reveal it.
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
    """A chart-native monitor enabled through HelmRelease values, with no allow."""
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


def test_an_allow_declared_egress_only_earns_no_ingress_credit(monkeypatch):
    """CRITICAL: an `ingress:` rule under `policyTypes: [Egress]` admits nothing
    at runtime. Crediting it would report a scrape-blocked namespace clean."""
    mislabelled = OBS_ALLOW.replace("policyTypes: [Ingress]", "policyTypes: [Egress]")
    assert _run(DEFAULT_DENY + mislabelled + SERVICE_MONITOR, monkeypatch) == 1


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
    """A corpus with zero scrape targets is an operator error: a gate that
    checks nothing is not a gate."""
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


def test_a_selector_with_extra_requirements_is_not_credited(monkeypatch):
    """Only the metadata.name label is guaranteed on a namespace, so a selector
    adding a requirement the corpus cannot evaluate is not credited."""
    allow_extra_label = OBS_ALLOW.replace(
        "matchLabels: {kubernetes.io/metadata.name: observability}",
        "matchLabels: {kubernetes.io/metadata.name: observability, team: platform}",
    )
    assert "team: platform" in allow_extra_label
    assert _run(DEFAULT_DENY + allow_extra_label + SERVICE_MONITOR, monkeypatch) == 1


def test_an_empty_matchlabels_default_deny_restricts_the_namespace(monkeypatch):
    """`podSelector: {matchLabels: {}}` selects every pod, so a default-deny
    spelled that way restricts the namespace exactly like `{}`."""
    spelled = DEFAULT_DENY.replace("podSelector: {}", "podSelector: {matchLabels: {}}")
    assert _run(spelled + SERVICE_MONITOR, monkeypatch) == 1
    # App-scoped allow, so the restriction can only come from the
    # empty-matchLabels policy.
    scoped_allow = OBS_ALLOW.replace(
        "podSelector: {}", "podSelector: {matchLabels: {app: app}}"
    )
    assert _run(spelled + scoped_allow + SERVICE_MONITOR, monkeypatch) == 0


def test_an_empty_namespaceselector_peer_is_credited(monkeypatch):
    """`namespaceSelector: {}` matches every namespace — observability included —
    with no corpus knowledge required, so it counts as an allow rather than
    falling through the label walk to a false-block."""
    allow_all_ns = """
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: allow-any-namespace, namespace: ns}
spec:
  podSelector: {}
  policyTypes: [Ingress]
  ingress:
    - from:
        - namespaceSelector: {}
      ports: [{protocol: TCP, port: 9090}]
"""
    assert _run(DEFAULT_DENY + allow_all_ns + SERVICE_MONITOR, monkeypatch) == 0


def test_an_empty_namespaceselector_anded_with_a_pod_restriction_is_not_credited(monkeypatch):
    """`namespaceSelector: {}` ANDed with a restrictive podSelector is not credited."""
    cross_ns_allow = """
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: allow-other-app, namespace: ns}
spec:
  podSelector: {}
  policyTypes: [Ingress]
  ingress:
    - from:
        - namespaceSelector: {}
          podSelector: {matchLabels: {app: other}}
      ports: [{protocol: TCP, port: 9090}]
"""
    assert _run(DEFAULT_DENY + cross_ns_allow + SERVICE_MONITOR, monkeypatch) == 1


def test_a_dual_family_slash_zero_rule_is_credited(monkeypatch):
    """`0.0.0.0/0` beside `::/0` is credited; one family alone is not, because
    the corpus cannot establish the scraper's family."""
    both = """
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: allow-any-address, namespace: ns}
spec:
  podSelector: {}
  policyTypes: [Ingress]
  ingress:
    - from:
        - ipBlock: {cidr: 0.0.0.0/0}
        - ipBlock: {cidr: '::/0'}
      ports: [{protocol: TCP, port: 9090}]
"""
    assert _run(DEFAULT_DENY + both + SERVICE_MONITOR, monkeypatch) == 0
    v4_only = both.replace("        - ipBlock: {cidr: '::/0'}\n", "")
    assert _run(DEFAULT_DENY + v4_only + SERVICE_MONITOR, monkeypatch) == 1
    excepted = both.replace(
        "ipBlock: {cidr: 0.0.0.0/0}",
        "ipBlock: {cidr: 0.0.0.0/0, except: [10.42.0.0/16]}",
    )
    # An except list on either family breaks the whole-address-space proof.
    assert _run(DEFAULT_DENY + excepted + SERVICE_MONITOR, monkeypatch) == 1


def test_an_invalid_cidr_never_counts_toward_the_family_credit(monkeypatch):
    """`garbage/0` must not pass as IPv4 — crediting is the fail-open
    direction, so only a cidr the API itself would accept may count."""
    bogus_pair = """
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: allow-any-address, namespace: ns}
spec:
  podSelector: {}
  policyTypes: [Ingress]
  ingress:
    - from:
        - ipBlock: {cidr: garbage/0}
        - ipBlock: {cidr: '::/0'}
      ports: [{protocol: TCP, port: 9090}]
"""
    assert _run(DEFAULT_DENY + bogus_pair + SERVICE_MONITOR, monkeypatch) == 1


def test_wrong_typed_or_combined_shapes_never_credit(monkeypatch):
    """API-invalid shapes must not prove the scrape is admitted: wrong-typed
    selector terms, an ipBlock peer carrying a selector, and a falsey
    non-list except are all rejected by the API and stay findings."""
    combined = """
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: allow-combined, namespace: ns}
spec:
  podSelector: {}
  policyTypes: [Ingress]
  ingress:
    - from:
        - {ipBlock: {cidr: 0.0.0.0/0}, namespaceSelector: {}}
        - ipBlock: {cidr: '::/0', except: {}}
      ports: [{protocol: TCP, port: 9090}]
"""
    assert _run(DEFAULT_DENY + combined + SERVICE_MONITOR, monkeypatch) == 1
    wrong_typed = OBS_ALLOW.replace(
        "matchLabels: {kubernetes.io/metadata.name: observability}",
        "matchLabels: [kubernetes.io/metadata.name]",
    )
    assert _run(DEFAULT_DENY + wrong_typed + SERVICE_MONITOR, monkeypatch) == 1


def test_unknown_selector_or_ipblock_keys_never_credit(monkeypatch):
    """A `matchLables:`/`exept:` typo empties the recognised terms — riding
    the empty-selector or unexcepted-/0 shortcut would credit a policy
    server-side apply rejects."""
    typo_selector = OBS_ALLOW.replace(
        "matchLabels: {kubernetes.io/metadata.name: observability}",
        "matchLables: {kubernetes.io/metadata.name: observability}",
    )
    assert _run(DEFAULT_DENY + typo_selector + SERVICE_MONITOR, monkeypatch) == 1
    typo_except = """
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: allow-any-address, namespace: ns}
spec:
  podSelector: {}
  policyTypes: [Ingress]
  ingress:
    - from:
        - ipBlock: {cidr: 0.0.0.0/0, exept: [10.42.0.0/16]}
        - ipBlock: {cidr: '::/0'}
      ports: [{protocol: TCP, port: 9090}]
"""
    assert _run(DEFAULT_DENY + typo_except + SERVICE_MONITOR, monkeypatch) == 1


def test_typoed_peer_or_rule_keys_and_absent_podselector_never_credit(monkeypatch):
    """Typoed peer or rule keys and an absent spec.podSelector never credit a policy."""
    typo_peer = """
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: allow-typo-peer, namespace: ns}
spec:
  podSelector: {}
  policyTypes: [Ingress]
  ingress:
    - from:
        - {namespaceSelector: {}, podSelecter: {matchLabels: {app: other}}}
      ports: [{protocol: TCP, port: 9090}]
"""
    assert _run(DEFAULT_DENY + typo_peer + SERVICE_MONITOR, monkeypatch) == 1
    typo_rule = """
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: allow-typo-rule, namespace: ns}
spec:
  podSelector: {}
  policyTypes: [Ingress]
  ingress:
    - form:
        - namespaceSelector: {matchLabels: {kubernetes.io/metadata.name: observability}}
"""
    assert _run(DEFAULT_DENY + typo_rule + SERVICE_MONITOR, monkeypatch) == 1
    no_selector_deny = """
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: invalid-deny, namespace: ns}
spec:
  policyTypes: [Ingress]
"""
    # The invalid deny does not restrict, so the monitor-only namespace passes.
    assert _run(no_selector_deny + SERVICE_MONITOR, monkeypatch) == 0


def test_one_invalid_peer_poisons_the_whole_rule(monkeypatch):
    """The API rejects a policy with any invalid peer — the valid /0 pair
    beside it must not credit a rule that never applies. A string `values`
    (substring membership) must not credit either."""
    poisoned = """
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: allow-poisoned, namespace: ns}
spec:
  podSelector: {}
  policyTypes: [Ingress]
  ingress:
    - from:
        - ipBlock: {cidr: 0.0.0.0/0}
        - ipBlock: {cidr: '::/0'}
        - {podSelecter: {}}
      ports: [{protocol: TCP, port: 9090}]
"""
    assert _run(DEFAULT_DENY + poisoned + SERVICE_MONITOR, monkeypatch) == 1
    string_values = OBS_ALLOW.replace(
        "        - namespaceSelector:\n            matchLabels: {kubernetes.io/metadata.name: observability}",
        "        - namespaceSelector:\n            matchExpressions: [{key: kubernetes.io/metadata.name, operator: In, values: observability}]",
    )
    assert _run(DEFAULT_DENY + string_values + SERVICE_MONITOR, monkeypatch) == 1


def test_an_invalid_ipblock_shape_poisons_the_whole_rule(monkeypatch):
    """Invalid shapes ANYWHERE in the rule reject the whole policy at the
    API, so a valid dual-family pair beside them must not credit; a pure
    selector peer or a valid narrowing block only skips."""
    base = """
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: allow-any-address, namespace: ns}
spec:
  podSelector: {}
  policyTypes: [Ingress]
  ingress:
    - from:
        - ipBlock: {cidr: 0.0.0.0/0}
        - ipBlock: {cidr: '::/0'}
        - PLACEHOLDER
      ports: [{protocol: TCP, port: 9090}]
"""
    for bad in (
        "ipBlock: {cidr: garbage/0}",
        "ipBlock: {cidr: 10.0.0.0/8, exept: [10.1.0.0/16]}",
        "ipBlock: {cidr: 10.0.0.0/8, except: {}}",
        "{ipBlock: {cidr: 10.0.0.0/8}, podSelector: {}}",
    ):
        corpus = base.replace("PLACEHOLDER", bad)
        assert _run(DEFAULT_DENY + corpus + SERVICE_MONITOR, monkeypatch) == 1, bad
    for benign in (
        "namespaceSelector: {matchLabels: {kubernetes.io/metadata.name: other}}",
        "ipBlock: {cidr: 10.0.0.0/8, except: [10.1.0.0/16]}",
        "ipBlock: {cidr: 192.168.0.0/16}",
    ):
        corpus = base.replace("PLACEHOLDER", benign)
        assert _run(DEFAULT_DENY + corpus + SERVICE_MONITOR, monkeypatch) == 0, benign


def test_a_malformed_selector_peer_poisons_the_family_credit(monkeypatch):
    """Atomicity closed structurally: a selector peer only SKIPS when the
    whole selector is API-valid — scalar selectors, non-string label values,
    and bad matchExpressions all poison the credit."""
    base = """
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: allow-any-address, namespace: ns}
spec:
  podSelector: {}
  policyTypes: [Ingress]
  ingress:
    - from:
        - ipBlock: {cidr: 0.0.0.0/0}
        - ipBlock: {cidr: '::/0'}
        - PLACEHOLDER
      ports: [{protocol: TCP, port: 9090}]
"""
    for bad in (
        "namespaceSelector: not-a-mapping",
        "podSelector: [a, list]",
        "namespaceSelector: {matchLabels: {app: [1, 2]}}",
        "namespaceSelector: {matchExpressions: [{key: app, operator: Sometimes}]}",
        "podSelector: {matchExpressions: [{key: app, operator: In, values: observability}]}",
    ):
        corpus = base.replace("PLACEHOLDER", bad)
        assert _run(DEFAULT_DENY + corpus + SERVICE_MONITOR, monkeypatch) == 1, bad
    ok = base.replace(
        "PLACEHOLDER",
        "namespaceSelector: {matchExpressions: [{key: app, operator: Exists}]}",
    )
    assert _run(DEFAULT_DENY + ok + SERVICE_MONITOR, monkeypatch) == 0


def test_label_syntax_and_operator_cardinality_poison_the_credit(monkeypatch):
    """The validator applies the apiserver's own rules: bad label syntax,
    In without values, and Exists with values all poison the credit; a
    syntactically valid selector still merely skips."""
    base = """
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: allow-any-address, namespace: ns}
spec:
  podSelector: {}
  policyTypes: [Ingress]
  ingress:
    - from:
        - ipBlock: {cidr: 0.0.0.0/0}
        - ipBlock: {cidr: '::/0'}
        - PLACEHOLDER
      ports: [{protocol: TCP, port: 9090}]
"""
    for bad in (
        "namespaceSelector: {matchLabels: {'bad key!': x}}",
        "namespaceSelector: {matchLabels: {app: 'bad value!'}}",
        "namespaceSelector: {matchLabels: {\"app\\n\": x}}",
        "namespaceSelector: {matchLabels: {app: \"x\\n\"}}",
        "namespaceSelector: {matchExpressions: [{key: app, operator: In}]}",
        "namespaceSelector: {matchExpressions: [{key: app, operator: In, values: []}]}",
        "namespaceSelector: {matchExpressions: [{key: app, operator: Exists, values: [x]}]}",
    ):
        corpus = base.replace("PLACEHOLDER", bad)
        assert _run(DEFAULT_DENY + corpus + SERVICE_MONITOR, monkeypatch) == 1, bad
    ok = base.replace(
        "PLACEHOLDER",
        "namespaceSelector: {matchLabels: {app.kubernetes.io/name: other-thing}}",
    )
    assert _run(DEFAULT_DENY + ok + SERVICE_MONITOR, monkeypatch) == 0


# --- shapes the gate read wrongly before -------------------------------------


def test_an_empty_policytypes_list_still_restricts_the_namespace(monkeypatch):
    """`policyTypes` is omitempty: an empty list round-trips as an absent field,
    so the CNI applies the ingress default and the scrape is denied."""
    no_types = DEFAULT_DENY.replace("policyTypes: [Ingress]", "policyTypes: []")
    assert _run(DECOY_SCRAPE + no_types + SERVICE_MONITOR, monkeypatch) == 1


def test_a_namespaceless_document_is_read_as_default(monkeypatch):
    """The API defaults an omitted namespace to `default`, so a monitor and a
    default-deny written without one belong to the same real namespace."""
    monitor = SERVICE_MONITOR.replace(", namespace: ns", "")
    deny = DEFAULT_DENY.replace(", namespace: ns", "")
    assert _run(deny + monitor, monkeypatch) == 1
    allow = OBS_ALLOW.replace(", namespace: ns", "")
    assert _run(deny + allow + monitor, monkeypatch) == 0


def test_an_unused_exempt_is_reported_not_fatal(monkeypatch, capsys):
    """Same accounting as the sibling default-deny gate: an exemption the corpus
    no longer exercises is visible, never a failure."""
    argv = ["--exempt", "gone=namespace retired"]
    assert _run(DEFAULT_DENY + OBS_ALLOW + SERVICE_MONITOR, monkeypatch, argv) == 0
    assert "not exercised by this corpus: gone" in capsys.readouterr().out


SERVICE_WITH_METRICS_PORT = """
---
apiVersion: v1
kind: Service
metadata:
  name: app
  namespace: ns
  labels: {app: app}
spec:
  selector: {app: app}
  ports:
    - name: metrics
      port: 9090
"""

MONITOR_NAMING_METRICS = """
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata: {name: app, namespace: ns}
spec:
  selector: {matchLabels: {app: app}}
  endpoints:
    - port: metrics
"""


class TestMonitorPortResolution:
    def test_a_resolvable_endpoint_port_is_clean(self, monkeypatch):
        corpus = (
            DEFAULT_DENY + OBS_ALLOW + SERVICE_WITH_METRICS_PORT + MONITOR_NAMING_METRICS
        )
        assert _run(corpus, monkeypatch) == 0

    def test_a_renamed_service_port_is_reported(self, monkeypatch, capsys):
        """The mutation: prometheus-operator then emits zero targets, so no `up`
        series appears and an `up == 0` alert arm can never fire."""
        corpus = (
            DEFAULT_DENY
            + OBS_ALLOW
            + SERVICE_WITH_METRICS_PORT.replace("name: metrics", "name: http-metrics")
            + MONITOR_NAMING_METRICS
        )
        assert _run(corpus, monkeypatch) == 1
        err = capsys.readouterr().err
        assert "port 'metrics' is declared by no Service" in err
        assert "zero targets" in err

    def test_a_service_with_no_named_port_is_reported(self, monkeypatch, capsys):
        corpus = (
            DEFAULT_DENY
            + OBS_ALLOW
            + SERVICE_WITH_METRICS_PORT.replace("    - name: metrics\n", "    - ")
            + MONITOR_NAMING_METRICS
        )
        assert _run(corpus, monkeypatch) == 1
        assert "no named port" in capsys.readouterr().err

    def test_a_monitor_whose_service_the_chart_renders_is_not_a_finding(self, monkeypatch):
        """No matched Service in the corpus proves nothing about the port."""
        corpus = DEFAULT_DENY + OBS_ALLOW + MONITOR_NAMING_METRICS
        assert _run(corpus, monkeypatch) == 0

    def test_a_podmonitor_resolves_against_container_ports(self, monkeypatch, capsys):
        workload = """
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: app, namespace: ns}
spec:
  template:
    metadata:
      labels: {app: app}
    spec:
      containers:
        - name: app
          ports:
            - name: http-metrics
              containerPort: 9090
"""
        monitor = """
---
apiVersion: monitoring.coreos.com/v1
kind: PodMonitor
metadata: {name: app, namespace: ns}
spec:
  selector: {matchLabels: {app: app}}
  podMetricsEndpoints:
    - port: metrics
"""
        assert _run(DEFAULT_DENY + OBS_ALLOW + workload + monitor, monkeypatch) == 1
        assert "declared by no workload" in capsys.readouterr().err

    def test_a_matchexpressions_selector_is_not_credited_or_failed(self, monkeypatch):
        monitor = MONITOR_NAMING_METRICS.replace(
            "selector: {matchLabels: {app: app}}",
            "selector: {matchExpressions: [{key: app, operator: Exists}]}",
        )
        corpus = (
            DEFAULT_DENY
            + OBS_ALLOW
            + SERVICE_WITH_METRICS_PORT.replace("name: metrics", "name: other")
            + monitor
        )
        assert _run(corpus, monkeypatch) == 0


WORKLOAD = """
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: app, namespace: ns}
spec:
  template:
    metadata:
      labels: {app: app}
    spec:
      containers:
        - name: app
          ports:
            - name: metrics
              containerPort: 9090
"""

POD_MONITOR_BY_NUMBER = """
---
apiVersion: monitoring.coreos.com/v1
kind: PodMonitor
metadata: {name: app, namespace: ns}
spec:
  selector: {matchLabels: {app: app}}
  podMetricsEndpoints:
    - portNumber: 9090
"""


class TestPodPortSpellings:
    """`portNumber` and `targetPort` resolve against the container ports too."""

    def test_a_declared_portnumber_is_clean(self, monkeypatch):
        corpus = DEFAULT_DENY + OBS_ALLOW + WORKLOAD + POD_MONITOR_BY_NUMBER
        assert _run(corpus, monkeypatch) == 0

    def test_an_undeclared_portnumber_is_reported(self, monkeypatch, capsys):
        """prometheus-operator discovers declared container ports only, so a
        number no container declares resolves zero targets."""
        corpus = (
            DEFAULT_DENY
            + OBS_ALLOW
            + WORKLOAD
            + POD_MONITOR_BY_NUMBER.replace("portNumber: 9090", "portNumber: 9187")
        )
        assert _run(corpus, monkeypatch) == 1
        err = capsys.readouterr().err
        assert "portNumber 9187 is declared by no workload" in err

    def test_an_undeclared_podmonitor_targetport_is_reported(self, monkeypatch, capsys):
        corpus = (
            DEFAULT_DENY
            + OBS_ALLOW
            + WORKLOAD
            + POD_MONITOR_BY_NUMBER.replace("portNumber: 9090", "targetPort: http")
        )
        assert _run(corpus, monkeypatch) == 1
        assert "targetPort 'http' is declared by no workload" in capsys.readouterr().err

    def test_an_endpoint_naming_no_port_is_not_a_finding(self, monkeypatch):
        corpus = (
            DEFAULT_DENY
            + OBS_ALLOW
            + WORKLOAD
            + POD_MONITOR_BY_NUMBER.replace("    - portNumber: 9090", "    - path: /metrics")
        )
        assert _run(corpus, monkeypatch) == 0


SERVICE_HOP = """
---
apiVersion: v1
kind: Service
metadata:
  name: app
  namespace: ns
  labels: {app: app}
spec:
  selector: {app: app}
  ports:
    - name: metrics
      port: 9090
      targetPort: metrics
"""


class TestServiceHopResolution:
    """A Service port resolves to a pod port, and a NAMED one must exist."""

    def test_a_resolvable_named_targetport_is_clean(self, monkeypatch):
        corpus = DEFAULT_DENY + OBS_ALLOW + SERVICE_HOP + WORKLOAD + MONITOR_NAMING_METRICS
        assert _run(corpus, monkeypatch) == 0

    def test_a_named_targetport_no_container_declares_is_reported(
        self, monkeypatch, capsys
    ):
        """The common edit: /metrics moved to its own port, the container
        `ports:` entry left behind. Namespace-level checking passes it."""
        corpus = (
            DEFAULT_DENY
            + OBS_ALLOW
            + SERVICE_HOP
            + WORKLOAD.replace("name: metrics", "name: http")
            + MONITOR_NAMING_METRICS
        )
        assert _run(corpus, monkeypatch) == 1
        err = capsys.readouterr().err
        assert "Service app targetPort" in err
        assert "declared by no workload" in err

    def test_a_numeric_targetport_needs_no_container_port(self, monkeypatch):
        """A numeric targetPort is routed whatever the container declares."""
        corpus = (
            DEFAULT_DENY
            + OBS_ALLOW
            + SERVICE_HOP.replace("targetPort: metrics", "targetPort: 9090")
            + WORKLOAD.replace("name: metrics", "name: http")
            + MONITOR_NAMING_METRICS
        )
        assert _run(corpus, monkeypatch) == 0

    def test_a_selectorless_service_has_no_pods_to_check(self, monkeypatch):
        """A Service with hand-written endpoints selects no pod in the corpus."""
        corpus = (
            DEFAULT_DENY
            + OBS_ALLOW
            + SERVICE_HOP.replace("  selector: {app: app}\n", "")
            + WORKLOAD.replace("name: metrics", "name: http")
            + MONITOR_NAMING_METRICS
        )
        assert _run(corpus, monkeypatch) == 0

    def test_an_endpoint_targetport_resolves_through_the_service_pods(
        self, monkeypatch, capsys
    ):
        monitor = MONITOR_NAMING_METRICS.replace("- port: metrics", "- targetPort: http")
        corpus = DEFAULT_DENY + OBS_ALLOW + SERVICE_HOP + WORKLOAD + monitor
        assert _run(corpus, monkeypatch) == 1
        assert "targetPort 'http' is declared by no workload" in capsys.readouterr().err


NARROWED_OBS_ALLOW = """
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
          podSelector:
            matchLabels: {app.kubernetes.io/name: prometheus}
      ports: [{protocol: TCP, port: 9090}]
"""
SCRAPER = ["--prometheus-pod-label", "app.kubernetes.io/name=prometheus"]


class TestScraperPodLabels:
    """A peer narrowing the observability namespace is judged only against
    labels declared with --prometheus-pod-label."""

    def test_a_narrowed_peer_is_credited_when_no_labels_are_declared(self, monkeypatch):
        corpus = DEFAULT_DENY + NARROWED_OBS_ALLOW + SERVICE_MONITOR
        assert _run(corpus, monkeypatch) == 0

    def test_a_narrowed_peer_that_selects_the_scraper_is_credited(self, monkeypatch):
        corpus = DEFAULT_DENY + NARROWED_OBS_ALLOW + SERVICE_MONITOR
        assert _run(corpus, monkeypatch, SCRAPER) == 0

    def test_a_narrowed_peer_that_misses_the_scraper_is_not_credited(
        self, monkeypatch, capsys
    ):
        corpus = DEFAULT_DENY + NARROWED_OBS_ALLOW.replace(
            "name: prometheus", "name: redis-exporter"
        ) + SERVICE_MONITOR
        assert _run(corpus, monkeypatch, SCRAPER) == 1
        err = capsys.readouterr().err
        assert "ns: scraped" in err and "ingress-restricted" in err

    def test_a_matchexpressions_peer_is_evaluated_against_the_scraper(self, monkeypatch):
        peer = NARROWED_OBS_ALLOW.replace(
            "matchLabels: {app.kubernetes.io/name: prometheus}",
            "matchExpressions: [{key: app.kubernetes.io/name, operator: In, "
            "values: [prometheus]}]",
        )
        assert _run(DEFAULT_DENY + peer + SERVICE_MONITOR, monkeypatch, SCRAPER) == 0

    def test_a_label_pair_without_an_equals_sign_is_an_operator_error(
        self, monkeypatch, capsys
    ):
        corpus = DEFAULT_DENY + OBS_ALLOW + SERVICE_MONITOR
        argv = ["--prometheus-pod-label", "prometheus"]
        assert _run(corpus, monkeypatch, argv) == 2
        assert "must be KEY=VALUE" in capsys.readouterr().err


class TestUnmodelledNotes:
    """A declined shape must be named, or the failure reads as an absent allow."""

    def test_an_extra_label_requirement_is_named_as_unmodelled(self, monkeypatch, capsys):
        allow = OBS_ALLOW.replace(
            "matchLabels: {kubernetes.io/metadata.name: observability}",
            "matchLabels: {kubernetes.io/metadata.name: observability, team: platform}",
        )
        assert _run(DEFAULT_DENY + allow + SERVICE_MONITOR, monkeypatch) == 1
        err = capsys.readouterr().err
        assert "not modelled: NetworkPolicy ns/allow-metrics-ingress" in err
        assert "requirements beyond kubernetes.io/metadata.name" in err

    def test_an_ipblock_peer_is_named_as_unmodelled(self, monkeypatch, capsys):
        allow = OBS_ALLOW.replace(
            "        - namespaceSelector:\n"
            "            matchLabels: {kubernetes.io/metadata.name: observability}\n",
            "        - ipBlock: {cidr: 10.42.0.0/16}\n",
        )
        assert _run(DEFAULT_DENY + allow + SERVICE_MONITOR, monkeypatch) == 1
        err = capsys.readouterr().err
        assert "not modelled: NetworkPolicy ns/allow-metrics-ingress" in err
        assert "is an ipBlock" in err

    def test_a_podselector_scoped_peer_is_named_as_unmodelled(self, monkeypatch, capsys):
        allow = OBS_ALLOW.replace(
            "            matchLabels: {kubernetes.io/metadata.name: observability}\n",
            "            matchLabels: {kubernetes.io/metadata.name: observability}\n"
            "          podSelector: {matchLabels: {app.kubernetes.io/name: prometheus}}\n",
        )
        argv = ["--prometheus-pod-label", "app.kubernetes.io/name=alloy"]
        assert _run(DEFAULT_DENY + allow + SERVICE_MONITOR, monkeypatch, argv) == 1
        assert "scopes the observability namespace with a podSelector" in capsys.readouterr().err

    def test_a_peer_naming_another_namespace_earns_no_note(self, monkeypatch, capsys):
        """A read peer that names a different namespace is not an unmodelled shape."""
        allow = OBS_ALLOW.replace("observability}", "traefik}")
        assert _run(DEFAULT_DENY + allow + SERVICE_MONITOR, monkeypatch) == 1
        assert "not modelled" not in capsys.readouterr().err

    def test_notes_do_not_leak_between_runs(self, monkeypatch, capsys):
        allow = OBS_ALLOW.replace(
            "matchLabels: {kubernetes.io/metadata.name: observability}",
            "matchLabels: {kubernetes.io/metadata.name: observability, team: platform}",
        )
        assert _run(DEFAULT_DENY + allow + SERVICE_MONITOR, monkeypatch) == 1
        capsys.readouterr()
        assert _run(DEFAULT_DENY + SERVICE_MONITOR, monkeypatch) == 1
        assert "not modelled" not in capsys.readouterr().err

    def test_a_note_from_a_passing_namespace_is_not_printed(self, monkeypatch, capsys):
        """Notes are per namespace, so a credited namespace's declined peer stays quiet."""
        other_allow = OBS_ALLOW.replace("namespace: ns}", "namespace: other}").replace(
            "    - from:\n",
            "    - from:\n        - ipBlock: {cidr: 10.42.0.0/16}\n",
        )
        other_deny = DEFAULT_DENY.replace("namespace: ns}", "namespace: other}")
        other_monitor = SERVICE_MONITOR.replace("namespace: ns}", "namespace: other}")
        corpus = (
            DEFAULT_DENY + SERVICE_MONITOR + other_deny + other_allow + other_monitor
        )
        assert _run(corpus, monkeypatch) == 1
        err = capsys.readouterr().err
        assert "  ns: scraped" in err
        assert "not modelled" not in err
