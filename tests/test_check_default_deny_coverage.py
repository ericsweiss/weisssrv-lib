"""Tests for scripts/check-default-deny-coverage.py, proved against fixture
corpora so every failure arm is exercised."""
from __future__ import annotations

import io
import textwrap

import pytest
from script_loader import load_script

gate = load_script("check-default-deny-coverage.py")


FENCED = """\
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-ingress
  namespace: {ns}
spec:
  podSelector: {{}}
  policyTypes: [Ingress]
"""

DEPLOY = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app
  namespace: {ns}
"""

# The API defaults an omitted namespace to `default`, so both of these belong to
# a real namespace the mandate covers. Spelled without a `namespace:` key rather
# than with `namespace: default`, because the point is the defaulting itself.
NS_LESS_DEPLOY = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app
"""

NS_LESS_FENCE = """\
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-ingress
spec:
  podSelector: {}
  policyTypes: [Ingress]
"""

# The workload kinds the gate promises, spelled out here rather than read from
# the gate: a parametrization over `gate.WORKLOAD_KINDS` would shrink with the
# set it pins.
WORKLOAD_MANIFESTS = {
    "Deployment": "apiVersion: apps/v1\nkind: Deployment",
    "StatefulSet": "apiVersion: apps/v1\nkind: StatefulSet",
    "DaemonSet": "apiVersion: apps/v1\nkind: DaemonSet",
    "ReplicaSet": "apiVersion: apps/v1\nkind: ReplicaSet",
    "Job": "apiVersion: batch/v1\nkind: Job",
    "CronJob": "apiVersion: batch/v1\nkind: CronJob",
    "Pod": "apiVersion: v1\nkind: Pod",
}


def _workload(kind: str, ns: str) -> str:
    return f"{WORKLOAD_MANIFESTS[kind]}\nmetadata:\n  name: app\n  namespace: {ns}\n"


def _ns_wide_policy(ingress: str, ns: str = "apps") -> str:
    """A namespace-wide (`podSelector: {}`) Ingress policy carrying `ingress`.

    `ingress` is flow-style YAML so each rule shape reads as one line in the
    tables below — the shape under test is the whole point of each case.
    """
    return (
        "---\n"
        "apiVersion: networking.k8s.io/v1\n"
        "kind: NetworkPolicy\n"
        "metadata:\n"
        "  name: ns-wide\n"
        f"  namespace: {ns}\n"
        "spec:\n"
        "  podSelector: {}\n"
        "  policyTypes: [Ingress]\n"
        f"  ingress: {ingress}\n"
    )


# Rule shapes that admit every peer on every port. Counting any of them as a
# fence is fail-open. The trap is the empty label selector: `{}` matches every
# object in its scope.
WIDE_OPEN_RULES = {
    "neither from nor ports": "[{}]",
    "a bare {} peer": "[{from: [{}]}]",
    "an empty namespaceSelector (every namespace)": "[{from: [{namespaceSelector: {}}]}]",
    # The explicit namespaceSelector is what widens this shape: the same rule
    # without it is scoped to the policy's own namespace and fences.
    "both selectors empty": "[{from: [{namespaceSelector: {}, podSelector: {}}]}]",
    # Peers within one rule are OR'd: a rule is only as closed as its most
    # open peer.
    "a wide peer beside a narrow one": (
        "[{from: [{namespaceSelector: {matchLabels: "
        "{kubernetes.io/metadata.name: observability}}}, {namespaceSelector: {}}]}]"
    ),
    # An empty selector TERM is the same trap one level down: `matchLabels: {}`
    # requires nothing, so the selector matches every namespace.
    "an empty matchLabels namespaceSelector": (
        "[{from: [{namespaceSelector: {matchLabels: {}}}]}]"
    ),
    # An ipBlock usually narrows, but the whole address space with no except
    # list admits every peer there is.
    "an unexcepted 0.0.0.0/0 ipBlock": "[{from: [{ipBlock: {cidr: 0.0.0.0/0}}]}]",
    "an unexcepted ::/0 ipBlock": "[{from: [{ipBlock: {cidr: '::/0'}}]}]",
    # The prefix length is what admits everything — any /0 spelling counts,
    # not just the two canonical ones.
    "a long-form IPv6 /0 ipBlock": "[{from: [{ipBlock: {cidr: '0:0:0:0:0:0:0:0/0'}}]}]",
    "a /0 with a nonzero address half": "[{from: [{ipBlock: {cidr: 10.0.0.0/0}}]}]",
    "a zero-padded /00 prefix": "[{from: [{ipBlock: {cidr: 0.0.0.0/00}}]}]",
    # Excepts that leave ANY address admitted narrow nothing this gate can
    # prove: which space pods occupy is the cluster's business, so partial
    # excepts — private-range or public-range alike — stay wide open.
    "a /0 with partial private excepts": (
        "[{from: [{ipBlock: {cidr: 0.0.0.0/0, "
        "except: [10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16]}}]}]"
    ),
    "a /0 excepting only public space": (
        "[{from: [{ipBlock: {cidr: 0.0.0.0/0, except: [203.0.113.0/24]}}]}]"
    ),
    # An except equal to its cidr is rejected by the API, so the policy never
    # admits and must not be read as a fence.
    "a /0 excepted by itself": (
        "[{from: [{ipBlock: {cidr: 0.0.0.0/0, except: [0.0.0.0/0]}}]}]"
    ),
}

# The other direction: rules that genuinely narrow, and must keep counting as a
# fence. Over-reading these as wide open would fail repos whose policies are
# correct — including the common "same-namespace clients, one port" allow.
FENCING_RULES = {
    "a scoped namespaceSelector": (
        "[{from: [{namespaceSelector: {matchLabels: "
        "{kubernetes.io/metadata.name: observability}}}]}]"
    ),
    "a scoped podSelector": "[{from: [{podSelector: {matchLabels: {app: one}}}]}]",
    "a matchExpressions selector": (
        "[{from: [{podSelector: {matchExpressions: [{key: app, operator: Exists}]}}]}]"
    ),
    "an ipBlock peer": "[{from: [{ipBlock: {cidr: 10.42.0.0/16}}]}]",
    # Coverage is exact subtraction: excepts that reconstruct the entire
    # family leave nothing admitted, whatever ranges the cluster's pods use.
    "a /0 fully excepted away": (
        "[{from: [{ipBlock: {cidr: 0.0.0.0/0, "
        "except: [0.0.0.0/1, 128.0.0.0/1]}}]}]"
    ),
    "an empty selector narrowed by ports": (
        "[{from: [{podSelector: {}}], ports: [{protocol: TCP, port: 8080}]}]"
    ),
    # An omitted namespaceSelector scopes the peer to the policy's own
    # namespace, so every pod in it is still a fence against other namespaces.
    "a port-less empty podSelector": "[{from: [{podSelector: {}}]}]",
    "a port-less empty matchLabels podSelector": (
        "[{from: [{podSelector: {matchLabels: {}}}]}]"
    ),
    "ports with no from at all": "[{ports: [{protocol: TCP, port: 8080}]}]",
}


def run(monkeypatch, corpus: str, argv: list[str] | None = None) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(textwrap.dedent(corpus)))
    try:
        return gate.main(argv or [])
    except SystemExit as exc:  # the shared corpus loader exits 2 directly
        return int(exc.code)


def test_fenced_namespace_passes(monkeypatch, capsys) -> None:
    corpus = DEPLOY.format(ns="apps") + "---\n" + FENCED.format(ns="apps")
    assert run(monkeypatch, corpus) == 0
    assert "Ingress default-deny OK" in capsys.readouterr().out


def test_unfenced_namespace_fails(monkeypatch, capsys) -> None:
    assert run(monkeypatch, DEPLOY.format(ns="apps")) == 1
    assert "apps: owns workloads" in capsys.readouterr().err


def test_the_gate_recognises_exactly_the_documented_workload_kinds() -> None:
    """The set is the gate's scope: a kind outside it makes its namespace
    invisible, and the gate then reports OK on a namespace nothing fences."""
    assert set(gate.WORKLOAD_KINDS) == set(WORKLOAD_MANIFESTS)


@pytest.mark.parametrize("kind", sorted(WORKLOAD_MANIFESTS))
def test_every_workload_kind_puts_its_namespace_in_scope(monkeypatch, capsys, kind) -> None:
    """Exit 1, not 2: 2 would mean the corpus held no workload at all, which is
    precisely what dropping the kind from WORKLOAD_KINDS would produce."""
    assert run(monkeypatch, _workload(kind, "apps")) == 1
    assert f"{kind}/app" in capsys.readouterr().err


@pytest.mark.parametrize("kind", sorted(WORKLOAD_MANIFESTS))
def test_every_workload_kind_is_satisfied_by_a_default_deny(monkeypatch, kind) -> None:
    """The other half: the kind must also be FENCEABLE, so the gate is a
    condition on the namespace rather than a permanent failure for that kind."""
    assert run(monkeypatch, _workload(kind, "apps") + "---\n" + FENCED.format(ns="apps")) == 0


def test_the_ok_summary_counts_fenced_and_exempt_namespaces(monkeypatch, capsys) -> None:
    """The clean-run summary counts workload namespaces, not fenced ones."""
    corpus = "---\n".join(
        [
            DEPLOY.format(ns="apps"),
            FENCED.format(ns="apps"),
            DEPLOY.format(ns="media"),
            FENCED.format(ns="media"),
            DEPLOY.format(ns="flux-system"),
            DEPLOY.format(ns="downloads"),
            FENCED.format(ns="idle"),
        ]
    )
    assert run(monkeypatch, corpus, ["--exempt", "downloads=its own policy covers egress too"]) == 0
    out = capsys.readouterr().out
    assert (
        "Ingress default-deny OK (4 workload namespaces, 2 fenced, 2 exempt) "
        "in 7 document(s)" in out
    )
    assert "not exercised" not in out, "both exemptions are exercised by this corpus"


def test_the_violation_names_the_flag_rather_than_a_local_edit(monkeypatch, capsys) -> None:
    """The script is vendored, so an exemption edited into it would be reverted."""
    assert run(monkeypatch, DEPLOY.format(ns="apps")) == 1
    assert "--exempt apps=REASON" in capsys.readouterr().err


def test_a_helmrelease_puts_its_target_namespace_in_scope(monkeypatch) -> None:
    """A chart's workloads never appear in a kustomize corpus."""
    corpus = """\
        apiVersion: helm.toolkit.fluxcd.io/v2
        kind: HelmRelease
        metadata:
          name: thing
          namespace: flux-system
        spec:
          targetNamespace: charted
        """
    assert run(monkeypatch, corpus) == 1


def test_an_app_scoped_policy_does_not_fence_the_namespace(monkeypatch, capsys) -> None:
    corpus = DEPLOY.format(ns="apps") + textwrap.dedent(
        """\
        ---
        apiVersion: networking.k8s.io/v1
        kind: NetworkPolicy
        metadata:
          name: allow-one-app
          namespace: apps
        spec:
          podSelector:
            matchLabels: {app: one}
          policyTypes: [Ingress]
        """
    )
    assert run(monkeypatch, corpus) == 1
    assert "apps: owns workloads" in capsys.readouterr().err


def test_a_namespace_wide_allow_all_policy_does_not_fence(monkeypatch, capsys) -> None:
    """The false fence: it satisfies "has an Ingress policyType" while fencing nothing."""
    corpus = DEPLOY.format(ns="apps") + textwrap.dedent(
        """\
        ---
        apiVersion: networking.k8s.io/v1
        kind: NetworkPolicy
        metadata:
          name: allow-all-ingress
          namespace: apps
        spec:
          podSelector: {}
          policyTypes: [Ingress]
          ingress: [{}]
        """
    )
    assert run(monkeypatch, corpus) == 1
    assert "apps: owns workloads" in capsys.readouterr().err


def test_an_allow_all_policy_defeats_a_sibling_default_deny(monkeypatch) -> None:
    """NetworkPolicies are additive — the open one wins, so the namespace is unfenced."""
    corpus = (
        DEPLOY.format(ns="apps")
        + "---\n"
        + FENCED.format(ns="apps")
        + textwrap.dedent(
            """\
            ---
            apiVersion: networking.k8s.io/v1
            kind: NetworkPolicy
            metadata:
              name: allow-all-ingress
              namespace: apps
            spec:
              podSelector: {}
              policyTypes: [Ingress]
              ingress: [{}]
            """
        )
    )
    assert run(monkeypatch, corpus) == 1


def test_a_namespace_wide_policy_with_real_rules_still_fences(monkeypatch) -> None:
    """Only a rule with neither `from` nor `ports` is wide open; a narrowed one counts."""
    corpus = DEPLOY.format(ns="apps") + textwrap.dedent(
        """\
        ---
        apiVersion: networking.k8s.io/v1
        kind: NetworkPolicy
        metadata:
          name: allow-scrape
          namespace: apps
        spec:
          podSelector: {}
          policyTypes: [Ingress]
          ingress:
            - from:
                - namespaceSelector:
                    matchLabels: {kubernetes.io/metadata.name: observability}
        """
    )
    assert run(monkeypatch, corpus) == 0


@pytest.mark.parametrize("shape", sorted(WIDE_OPEN_RULES))
def test_a_wide_open_rule_shape_does_not_fence_the_namespace(monkeypatch, capsys, shape) -> None:
    corpus = DEPLOY.format(ns="apps") + _ns_wide_policy(WIDE_OPEN_RULES[shape])
    assert run(monkeypatch, corpus) == 1
    assert "apps: owns workloads" in capsys.readouterr().err


@pytest.mark.parametrize("shape", sorted(WIDE_OPEN_RULES))
def test_a_wide_open_rule_shape_defeats_a_sibling_default_deny(monkeypatch, shape) -> None:
    """Additive policies: the open one wins wherever it is declared."""
    corpus = (
        DEPLOY.format(ns="apps")
        + "---\n"
        + FENCED.format(ns="apps")
        + _ns_wide_policy(WIDE_OPEN_RULES[shape])
    )
    assert run(monkeypatch, corpus) == 1


@pytest.mark.parametrize("shape", sorted(FENCING_RULES))
def test_a_narrowed_rule_shape_still_fences_the_namespace(monkeypatch, shape) -> None:
    corpus = DEPLOY.format(ns="apps") + _ns_wide_policy(FENCING_RULES[shape])
    assert run(monkeypatch, corpus) == 0


def test_a_namespace_less_workload_owns_the_default_namespace(monkeypatch, capsys) -> None:
    """`default` is a real namespace: the API puts a namespace-less workload
    there, so it must be fenced like any other. Reading the absent key as "no
    namespace" would drop the document and exempt the namespace entirely."""
    assert run(monkeypatch, NS_LESS_DEPLOY) == 1
    assert "default: owns workloads (Deployment/app)" in capsys.readouterr().err


def test_the_default_namespace_is_fenceable_like_any_other(monkeypatch) -> None:
    assert run(monkeypatch, NS_LESS_DEPLOY + "---\n" + FENCED.format(ns="default")) == 0


def test_a_namespace_less_policy_fences_the_default_namespace(monkeypatch) -> None:
    """Both sides default alike — a deny the API would place in `default` must
    not be read as belonging to nowhere, or it would fence nothing."""
    assert run(monkeypatch, NS_LESS_DEPLOY + "---\n" + NS_LESS_FENCE) == 0


def test_a_namespace_less_helmrelease_targets_the_default_namespace(monkeypatch, capsys) -> None:
    corpus = """\
        apiVersion: helm.toolkit.fluxcd.io/v2
        kind: HelmRelease
        metadata:
          name: thing
        """
    assert run(monkeypatch, corpus) == 1
    assert "default: owns workloads (HelmRelease/thing)" in capsys.readouterr().err


def test_a_helmrelease_without_a_target_falls_back_to_its_own_namespace(
    monkeypatch, capsys
) -> None:
    """targetNamespace wins where set; absent, the release deploys beside itself."""
    corpus = """\
        apiVersion: helm.toolkit.fluxcd.io/v2
        kind: HelmRelease
        metadata:
          name: thing
          namespace: charted
        """
    assert run(monkeypatch, corpus) == 1
    assert "charted: owns workloads (HelmRelease/thing)" in capsys.readouterr().err


def test_an_egress_only_policy_does_not_fence_the_namespace(monkeypatch) -> None:
    corpus = DEPLOY.format(ns="apps") + textwrap.dedent(
        """\
        ---
        apiVersion: networking.k8s.io/v1
        kind: NetworkPolicy
        metadata:
          name: allow-egress-dns
          namespace: apps
        spec:
          podSelector: {}
          policyTypes: [Egress]
          egress: [{}]
        """
    )
    assert run(monkeypatch, corpus) == 1


def test_flux_system_is_the_one_built_in_exemption(monkeypatch) -> None:
    """Universal to a Flux cluster; every other exemption is a consumer's flag."""
    assert set(gate.EXEMPT_NAMESPACES) == {"flux-system"}
    assert run(monkeypatch, DEPLOY.format(ns="flux-system")) == 0


def test_a_cli_exemption_needs_a_reason(monkeypatch) -> None:
    assert run(monkeypatch, DEPLOY.format(ns="apps"), ["--exempt", "apps"]) == 2


def test_a_cli_exemption_with_a_reason_is_honoured(monkeypatch) -> None:
    assert run(monkeypatch, DEPLOY.format(ns="apps"), ["--exempt", "apps=because"]) == 0


def test_cli_exemptions_add_to_the_built_in_one(monkeypatch) -> None:
    """A consumer's flags must not replace the flux-system default."""
    corpus = DEPLOY.format(ns="flux-system") + "---\n" + DEPLOY.format(ns="apps")
    assert run(monkeypatch, corpus, ["--exempt", "apps=because"]) == 0


def test_an_unused_exemption_is_reported_not_fatal(monkeypatch, capsys) -> None:
    corpus = DEPLOY.format(ns="apps") + "---\n" + FENCED.format(ns="apps")
    assert run(monkeypatch, corpus) == 0
    assert "not exercised by this corpus: flux-system" in capsys.readouterr().out


def test_an_empty_corpus_is_an_operator_error(monkeypatch) -> None:
    assert run(monkeypatch, "") == 2


def test_unparseable_input_is_an_operator_error(monkeypatch) -> None:
    assert run(monkeypatch, "kind: [unclosed\n") == 2


def test_a_corpus_without_workloads_is_an_operator_error(monkeypatch, capsys) -> None:
    """The shape a render loop that never reached an app stage produces."""
    assert run(monkeypatch, FENCED.format(ns="apps")) == 2
    assert "0 workload namespaces" in capsys.readouterr().err


@pytest.mark.parametrize("ns", sorted(gate.EXEMPT_NAMESPACES))
def test_every_exemption_carries_a_reason(ns: str) -> None:
    reason = gate.EXEMPT_NAMESPACES[ns]
    assert reason.strip()
    assert len(reason.split()) >= 8, (
        f"{ns}: the exemption reason must be a sentence, got {reason!r}"
    )


def test_an_empty_policytypes_list_still_fences(monkeypatch) -> None:
    """`policyTypes` is omitempty: an empty list round-trips as an absent field,
    so the CNI applies the ingress default and the namespace is fenced."""
    no_types = FENCED.format(ns="apps").replace("policyTypes: [Ingress]", "policyTypes: []")
    assert run(monkeypatch, DEPLOY.format(ns="apps") + "---\n" + no_types) == 0


def test_an_empty_matchlabels_default_deny_still_fences(monkeypatch) -> None:
    """`podSelector: {matchLabels: {}}` fences the namespace exactly like `{}`."""
    corpus = DEPLOY.format(ns="apps") + textwrap.dedent(
        """\
        ---
        apiVersion: networking.k8s.io/v1
        kind: NetworkPolicy
        metadata:
          name: default-deny-ingress
          namespace: apps
        spec:
          podSelector:
            matchLabels: {}
          policyTypes: [Ingress]
        """
    )
    assert run(monkeypatch, corpus) == 0


def test_an_empty_matchlabels_allow_all_defeats_a_sibling_fence(monkeypatch) -> None:
    """The same spelling on a wide-open ALLOW is namespace-wide too — additive
    policies mean it re-opens the namespace past a real default-deny."""
    corpus = (
        DEPLOY.format(ns="apps")
        + "---\n"
        + FENCED.format(ns="apps")
        + textwrap.dedent(
            """\
            ---
            apiVersion: networking.k8s.io/v1
            kind: NetworkPolicy
            metadata:
              name: allow-everything
              namespace: apps
            spec:
              podSelector:
                matchLabels: {}
              policyTypes: [Ingress]
              ingress: [{}]
            """
        )
    )
    assert run(monkeypatch, corpus) == 1


def test_a_wrong_typed_selector_term_neither_fences_nor_defeats(monkeypatch, capsys) -> None:
    """`matchLabels: []` / `matchExpressions: {}` are API-invalid: the policy
    never applies, so it must not count as the namespace's fence — and the
    same shape on an allow must not defeat a real one."""
    invalid_fence = FENCED.format(ns="apps").replace(
        "podSelector: {}", "podSelector: {matchLabels: []}"
    )
    assert run(monkeypatch, DEPLOY.format(ns="apps") + "---\n" + invalid_fence) == 1
    invalid_allow = _ns_wide_policy("[{}]").replace(
        "podSelector: {}", "podSelector: {matchExpressions: {}}"
    )
    corpus = DEPLOY.format(ns="apps") + "---\n" + FENCED.format(ns="apps") + invalid_allow
    assert run(monkeypatch, corpus) == 0


def test_an_unknown_selector_key_neither_fences_nor_defeats(monkeypatch) -> None:
    """A `matchLables:` typo empties the recognised terms; reading it as
    select-all would certify a fence server-side apply rejects."""
    typo_fence = FENCED.format(ns="apps").replace(
        "podSelector: {}", "podSelector: {matchLables: {app: x}}"
    )
    assert run(monkeypatch, DEPLOY.format(ns="apps") + "---\n" + typo_fence) == 1


def test_an_absent_spec_podselector_never_fences(monkeypatch) -> None:
    """spec.podSelector is REQUIRED — absent is not the all-pods default but a
    policy the API rejects, and it must not read as the namespace's fence."""
    no_selector = (
        "---\n"
        "apiVersion: networking.k8s.io/v1\n"
        "kind: NetworkPolicy\n"
        "metadata:\n  name: default-deny-ingress\n  namespace: apps\n"
        "spec:\n  policyTypes: [Ingress]\n"
    )
    assert run(monkeypatch, DEPLOY.format(ns="apps") + no_selector) == 1


BOTH_WAYS = """\
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny
  namespace: {ns}
spec:
  podSelector: {{}}
  policyTypes: [Ingress, Egress]
"""


class TestRequireEgress:
    """--require-egress is opt-in per namespace; the ingress arm is unchanged."""

    def test_a_namespace_fenced_both_ways_passes(self, monkeypatch, capsys) -> None:
        corpus = DEPLOY.format(ns="apps") + "---\n" + BOTH_WAYS.format(ns="apps")
        assert run(monkeypatch, corpus, ["--require-egress", "apps"]) == 0
        assert "1 egress-fenced" in capsys.readouterr().out

    def test_dropping_the_egress_policytype_fails(self, monkeypatch, capsys) -> None:
        """The mutation: only `Egress` leaves the fence, and nothing else reds."""
        corpus = (
            DEPLOY.format(ns="apps")
            + "---\n"
            + BOTH_WAYS.format(ns="apps").replace("[Ingress, Egress]", "[Ingress]")
        )
        assert run(monkeypatch, corpus, ["--require-egress", "apps"]) == 1
        assert "denies egress by default" in capsys.readouterr().err

    def test_an_egress_allow_all_rule_re_opens_the_namespace(self, monkeypatch) -> None:
        corpus = (
            DEPLOY.format(ns="apps")
            + "---\n"
            + BOTH_WAYS.format(ns="apps")
            + "  egress: [{}]\n"
        )
        assert run(monkeypatch, corpus, ["--require-egress", "apps"]) == 1

    def test_an_egress_rule_naming_ports_still_fences(self, monkeypatch) -> None:
        corpus = (
            DEPLOY.format(ns="apps")
            + "---\n"
            + BOTH_WAYS.format(ns="apps")
            + "  egress:\n    - ports: [{protocol: UDP, port: 53}]\n"
        )
        assert run(monkeypatch, corpus, ["--require-egress", "apps"]) == 0

    def test_without_the_flag_an_ingress_only_fence_passes(self, monkeypatch) -> None:
        corpus = DEPLOY.format(ns="apps") + "---\n" + FENCED.format(ns="apps")
        assert run(monkeypatch, corpus) == 0
