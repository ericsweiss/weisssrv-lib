"""Tests for scripts/check-scrape-wiring.py: the scraped PORT, not just the namespace.

Each arm is driven from a fixture tree so a mutation that stops the gate
resolving a Service hop, a PodMonitor endpoint or a port spelling turns red.
"""
from __future__ import annotations

import io
import pathlib
import textwrap

import pytest
from script_loader import load_script

gate = load_script("check-scrape-wiring.py")

DEPLOYMENT = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app
spec:
  template:
    metadata:
      labels:
        app.kubernetes.io/name: app
    spec:
      containers:
        - name: app
          ports:
            - name: metrics
              containerPort: 9100
"""

SERVICE = """\
apiVersion: v1
kind: Service
metadata:
  name: app
  labels:
    app.kubernetes.io/name: app
spec:
  selector:
    app.kubernetes.io/name: app
  ports:
    - name: metrics
      port: 9100
      targetPort: metrics
"""

SERVICE_MONITOR = """\
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: app
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: app
  endpoints:
    - port: metrics
"""

POD_MONITOR = """\
apiVersion: monitoring.coreos.com/v1
kind: PodMonitor
metadata:
  name: app
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: app
  podMetricsEndpoints:
    - port: metrics
"""


def policy(port: object = 9100, ports_key: str = "ports") -> str:
    rule = f"      {ports_key}:\n        - protocol: TCP\n          port: {port}\n"
    if port is None:
        rule = f"      {ports_key}: []\n"
    return (
        "apiVersion: networking.k8s.io/v1\n"
        "kind: NetworkPolicy\n"
        "metadata:\n  name: allow-scrape\n"
        "spec:\n"
        "  podSelector:\n    matchLabels:\n      app.kubernetes.io/name: app\n"
        "  policyTypes: [Ingress]\n"
        "  ingress:\n"
        "    - from:\n"
        "        - namespaceSelector:\n"
        "            matchLabels:\n"
        "              kubernetes.io/metadata.name: observability\n"
        + rule
    )


def tree(tmp_path, *documents: str):
    (tmp_path / "manifests.yaml").write_text(
        "\n---\n".join(textwrap.dedent(d) for d in documents)
    )
    return [str(tmp_path)]


def test_a_monitor_whose_port_is_admitted_passes(tmp_path, capsys):
    argv = tree(tmp_path, DEPLOYMENT, SERVICE, SERVICE_MONITOR, policy())
    assert gate.main(argv) == 0
    assert "every scraped port is admitted from observability" in capsys.readouterr().out


def test_a_policy_admitting_the_wrong_port_is_a_violation(tmp_path, capsys):
    """The defect the namespace-granularity sibling cannot see."""
    argv = tree(tmp_path, DEPLOYMENT, SERVICE, SERVICE_MONITOR, policy(9101))
    assert gate.main(argv) == 1
    assert "no NetworkPolicy admits namespace observability on port 9100" in (
        capsys.readouterr().err
    )


def test_a_policy_naming_the_port_by_name_is_credited(tmp_path):
    argv = tree(tmp_path, DEPLOYMENT, SERVICE, SERVICE_MONITOR, policy("metrics"))
    assert gate.main(argv) == 0


def test_no_policy_at_all_is_a_violation(tmp_path):
    argv = tree(tmp_path, DEPLOYMENT, SERVICE, SERVICE_MONITOR)
    assert gate.main(argv) == 1


def test_an_empty_ports_list_admits_every_port(tmp_path):
    """`ports: []` round-trips as absent in the API, so it matches every port."""
    argv = tree(tmp_path, DEPLOYMENT, SERVICE, SERVICE_MONITOR, policy(None))
    assert gate.main(argv) == 0


def test_an_absent_ports_key_admits_every_port(tmp_path):
    bare = policy().split("      ports:")[0]
    argv = tree(tmp_path, DEPLOYMENT, SERVICE, SERVICE_MONITOR, bare)
    assert gate.main(argv) == 0


def test_a_non_dict_ports_entry_does_not_raise(tmp_path):
    broken = policy().replace("        - protocol: TCP\n          port: 9100\n", "        - 9100\n")
    argv = tree(tmp_path, DEPLOYMENT, SERVICE, SERVICE_MONITOR, broken)
    assert gate.main(argv) == 1


def test_an_endport_range_covering_the_port_is_credited(tmp_path):
    ranged = policy(9000).replace("          port: 9000\n", "          port: 9000\n          endPort: 9200\n")
    argv = tree(tmp_path, DEPLOYMENT, SERVICE, SERVICE_MONITOR, ranged)
    assert gate.main(argv) == 0


def test_an_endport_range_excluding_the_port_is_a_violation(tmp_path, capsys):
    """A range is credited by coverage, not by carrying an `endPort` at all."""
    ranged = policy(9200).replace(
        "          port: 9200\n", "          port: 9200\n          endPort: 9300\n"
    )
    argv = tree(tmp_path, DEPLOYMENT, SERVICE, SERVICE_MONITOR, ranged)
    assert gate.main(argv) == 1
    assert "on port 9100" in capsys.readouterr().err


def test_an_entry_with_no_port_key_admits_every_port_for_its_protocol(tmp_path):
    """`{protocol: TCP}` is a legal NetworkPolicyPort meaning every TCP port."""
    protocol_only = policy().replace("          port: 9100\n", "")
    argv = tree(tmp_path, DEPLOYMENT, SERVICE, SERVICE_MONITOR, protocol_only)
    assert gate.main(argv) == 0


def test_a_udp_only_entry_does_not_admit_the_scrape(tmp_path):
    """The protocol-only arm must stay scoped to the entry's own protocol."""
    udp_only = policy().replace(
        "        - protocol: TCP\n          port: 9100\n", "        - protocol: UDP\n"
    )
    argv = tree(tmp_path, DEPLOYMENT, SERVICE, SERVICE_MONITOR, udp_only)
    assert gate.main(argv) == 1


def test_every_selected_service_must_be_admitted(tmp_path, capsys):
    """prometheus-operator scrapes EVERY Service the selector matches."""
    second_service = SERVICE.replace("name: app\n", "name: app-canary\n", 1).replace(
        "    app.kubernetes.io/name: app\n  ports:",
        "    app.kubernetes.io/name: canary\n  ports:",
    ).replace("      port: 9100", "      port: 9101")
    canary = DEPLOYMENT.replace("name: app\n", "name: canary\n", 1).replace(
        "        app.kubernetes.io/name: app", "        app.kubernetes.io/name: canary"
    ).replace("containerPort: 9100", "containerPort: 9101")
    argv = tree(
        tmp_path, DEPLOYMENT, canary, SERVICE, second_service, SERVICE_MONITOR, policy()
    )
    assert gate.main(argv) == 1
    assert "Service app-canary" in capsys.readouterr().err


class TestServicePortDefaults:
    """A Service port with no `targetPort` serves its own port number."""

    SERVICE_NO_TARGET = SERVICE.replace("      targetPort: metrics\n", "")

    def test_the_port_number_is_the_pod_port_when_targetport_is_absent(self, tmp_path):
        argv = tree(
            tmp_path, DEPLOYMENT, self.SERVICE_NO_TARGET, SERVICE_MONITOR, policy()
        )
        assert gate.main(argv) == 0

    def test_a_policy_on_another_port_is_still_a_violation(self, tmp_path, capsys):
        argv = tree(
            tmp_path, DEPLOYMENT, self.SERVICE_NO_TARGET, SERVICE_MONITOR, policy(9101)
        )
        assert gate.main(argv) == 1
        assert "on port 9100" in capsys.readouterr().err


class TestANumericallySelectedPort:
    """A numeric `targetPort` needs no containerPort for the Service to be
    valid, so the scrape is credited where a selected workload declares it."""

    SERVICE_NUMERIC = SERVICE.replace("      targetPort: metrics\n", "")
    OTHER_PORT = DEPLOYMENT.replace("name: app\n", "name: app-sidecar\n", 1).replace(
        "            - name: metrics\n              containerPort: 9100\n",
        "            - name: health\n              containerPort: 8080\n",
    )

    def test_a_selected_workload_declaring_another_port_is_no_target(self, tmp_path):
        """The sidecar serves nothing on 9100, so a policy naming the port for
        the workload that does declare it admits the whole scrape."""
        argv = tree(
            tmp_path,
            DEPLOYMENT,
            self.OTHER_PORT,
            self.SERVICE_NUMERIC,
            SERVICE_MONITOR,
            policy("metrics"),
        )
        assert gate.main(argv) == 0

    def test_a_number_no_selected_workload_declares_is_a_violation(self, tmp_path, capsys):
        argv = tree(
            tmp_path, self.OTHER_PORT, self.SERVICE_NUMERIC, SERVICE_MONITOR, policy()
        )
        assert gate.main(argv) == 1
        assert "declares a port 9100" in capsys.readouterr().err


def test_a_service_the_monitor_does_not_select_is_not_judged(tmp_path):
    """Only the Services carrying the monitor's labels are scrape targets."""
    other = (
        SERVICE.replace("name: app\n", "name: other\n", 1)
        .replace(
            "  labels:\n    app.kubernetes.io/name: app\nspec:",
            "  labels:\n    app.kubernetes.io/name: other\nspec:",
        )
        .replace("targetPort: metrics", "targetPort: nothing-declares-this")
    )
    argv = tree(tmp_path, DEPLOYMENT, SERVICE, other, SERVICE_MONITOR, policy())
    assert gate.main(argv) == 0


def test_a_label_only_one_selected_workload_carries_does_not_admit_the_scrape(
    tmp_path, capsys
):
    """The admitted pod labels are the intersection: last-wins greens a tree
    where one selected workload's pods are not admitted."""
    primary = DEPLOYMENT.replace(
        "        app.kubernetes.io/name: app",
        "        app.kubernetes.io/name: app\n        component: primary",
    )
    canary = DEPLOYMENT.replace("name: app\n", "name: canary\n", 1).replace(
        "        app.kubernetes.io/name: app",
        "        app.kubernetes.io/name: app\n        component: canary",
    )
    narrowed = policy().replace(
        "      app.kubernetes.io/name: app\n  policyTypes",
        "      app.kubernetes.io/name: app\n      component: canary\n  policyTypes",
    )
    argv = tree(tmp_path, primary, canary, SERVICE, SERVICE_MONITOR, narrowed)
    assert gate.main(argv) == 1
    assert "no NetworkPolicy admits namespace observability" in capsys.readouterr().err


class TestPodMonitor:
    """The kind the gate advertises; `spec.endpoints` alone would pass it blind."""

    def test_a_pod_monitor_port_that_is_admitted_passes(self, tmp_path):
        argv = tree(tmp_path, DEPLOYMENT, POD_MONITOR, policy())
        assert gate.main(argv) == 0

    def test_a_pod_monitor_port_that_is_not_admitted_is_a_violation(self, tmp_path, capsys):
        argv = tree(tmp_path, DEPLOYMENT, POD_MONITOR, policy(9101))
        assert gate.main(argv) == 1
        assert "on port 9100" in capsys.readouterr().err

    def test_a_pod_monitor_needs_no_service(self, tmp_path):
        """A PodMonitor resolves straight against containerPorts: no Service hop."""
        argv = tree(tmp_path, DEPLOYMENT, POD_MONITOR, policy())
        assert gate.main(argv) == 0

    def test_a_pod_monitor_port_no_container_declares_is_a_violation(self, tmp_path, capsys):
        monitor = POD_MONITOR.replace("port: metrics", "port: telemetry")
        argv = tree(tmp_path, DEPLOYMENT, monitor, policy())
        assert gate.main(argv) == 1
        assert "declares a port named 'telemetry'" in capsys.readouterr().err

    def test_a_pod_monitor_with_no_endpoint_is_an_operator_error(self, tmp_path, capsys):
        monitor = POD_MONITOR.split("  podMetricsEndpoints:")[0]
        argv = tree(tmp_path, DEPLOYMENT, monitor, policy())
        assert gate.main(argv) == 2
        assert "declares no endpoints" in capsys.readouterr().err

    def test_the_endpoint_key_is_selected_by_kind(self):
        assert gate.ENDPOINT_KEYS == {
            "PodMonitor": "podMetricsEndpoints",
            "ServiceMonitor": "endpoints",
        }


def test_a_service_monitor_with_no_service_is_a_violation(tmp_path, capsys):
    argv = tree(tmp_path, DEPLOYMENT, SERVICE_MONITOR, policy())
    assert gate.main(argv) == 1
    assert "no Service carries the monitor's labels" in capsys.readouterr().err


def test_a_monitor_selector_with_matchexpressions_is_an_operator_error(tmp_path, capsys):
    """Reading only matchLabels judges objects the monitor does not select and
    leaves the ones it does select unresolved."""
    monitor = SERVICE_MONITOR.replace(
        "    matchLabels:\n      app.kubernetes.io/name: app\n",
        "    matchLabels:\n      app.kubernetes.io/name: app\n"
        "    matchExpressions:\n      - {key: shard, operator: Exists}\n",
    )
    argv = tree(tmp_path, DEPLOYMENT, SERVICE, monitor, policy())
    assert gate.main(argv) == 2
    assert "spec.selector carries matchExpressions" in capsys.readouterr().err


def test_a_matchexpressions_pod_selector_is_not_credited(tmp_path, capsys):
    unmodelled = policy().replace(
        "  podSelector:\n    matchLabels:\n      app.kubernetes.io/name: app\n",
        "  podSelector:\n    matchExpressions:\n      - {key: a, operator: Exists}\n",
    )
    argv = tree(tmp_path, DEPLOYMENT, SERVICE, SERVICE_MONITOR, unmodelled)
    assert gate.main(argv) == 1
    assert "matchExpressions, which this gate does not model" in capsys.readouterr().err


class TestAPeerNarrowingTheScrapeNamespace:
    """Prometheus is chart-installed, so a podSelector on the observability
    peer resolves only against the labels the caller declares; every other
    narrowing fails closed and names the peer."""

    NARROWED = policy().replace(
        "              kubernetes.io/metadata.name: observability\n",
        "              kubernetes.io/metadata.name: observability\n"
        "          podSelector:\n"
        "            matchLabels:\n"
        "              app.kubernetes.io/name: prometheus\n",
    )
    DECLARED = ["--prometheus-pod-label", "app.kubernetes.io/name=prometheus"]

    @pytest.fixture(autouse=True)
    def _fresh_scraper_labels(self):
        gate.SCRAPER_LABELS.clear()
        yield
        gate.SCRAPER_LABELS.clear()

    def test_a_podselector_with_no_declared_labels_is_not_credited(self, tmp_path, capsys):
        argv = tree(tmp_path, DEPLOYMENT, SERVICE, SERVICE_MONITOR, self.NARROWED)
        assert gate.main(argv) == 1
        err = capsys.readouterr().err
        assert "no NetworkPolicy admits namespace observability" in err
        assert "allow-scrape: its peer for namespace observability selects pods by" in err
        assert "none declared" in err

    def test_a_podselector_the_declared_labels_satisfy_is_credited(self, tmp_path):
        argv = tree(tmp_path, DEPLOYMENT, SERVICE, SERVICE_MONITOR, self.NARROWED)
        assert gate.main(argv + self.DECLARED) == 0

    def test_a_podselector_asking_for_an_undeclared_label_is_not_credited(
        self, tmp_path, capsys
    ):
        other = self.NARROWED.replace(
            "app.kubernetes.io/name: prometheus\n", "app.kubernetes.io/name: alertmanager\n"
        )
        argv = tree(tmp_path, DEPLOYMENT, SERVICE, SERVICE_MONITOR, other)
        assert gate.main(argv + self.DECLARED) == 1
        assert "selects pods by {'app.kubernetes.io/name': 'alertmanager'}" in (
            capsys.readouterr().err
        )

    def test_a_podselector_asking_for_more_labels_than_declared_is_not_credited(
        self, tmp_path
    ):
        more = self.NARROWED.replace(
            "              app.kubernetes.io/name: prometheus\n",
            "              app.kubernetes.io/name: prometheus\n"
            "              prometheus: kube-prometheus-stack-prometheus\n",
        )
        argv = tree(tmp_path, DEPLOYMENT, SERVICE, SERVICE_MONITOR, more)
        assert gate.main(argv + self.DECLARED) == 1
        assert gate.main(argv + self.DECLARED + [
            "--prometheus-pod-label", "prometheus=kube-prometheus-stack-prometheus"
        ]) == 0

    def test_a_malformed_pod_label_is_a_usage_error(self, tmp_path):
        argv = tree(tmp_path, DEPLOYMENT, SERVICE, SERVICE_MONITOR, self.NARROWED)
        with pytest.raises(SystemExit) as caught:
            gate.main(argv + ["--prometheus-pod-label", "prometheus"])
        assert caught.value.code == 2

    def test_an_empty_podselector_selects_every_pod_and_keeps_the_credit(self, tmp_path):
        """`podSelector: {}` narrows nothing, so the peer admits every pod."""
        every = self.NARROWED.replace(
            "          podSelector:\n"
            "            matchLabels:\n"
            "              app.kubernetes.io/name: prometheus\n",
            "          podSelector: {}\n",
        )
        argv = tree(tmp_path, DEPLOYMENT, SERVICE, SERVICE_MONITOR, every)
        assert gate.main(argv) == 0

    def test_matchexpressions_beside_the_namespace_name_is_not_credited(
        self, tmp_path, capsys
    ):
        """A peer naming the namespace in matchLabels AND narrowing it with
        matchExpressions is an unmodelled shape, not a credited one."""
        narrowed = policy().replace(
            "              kubernetes.io/metadata.name: observability\n",
            "              kubernetes.io/metadata.name: observability\n"
            "            matchExpressions:\n"
            "              - {key: tier, operator: Exists}\n",
        )
        argv = tree(tmp_path, DEPLOYMENT, SERVICE, SERVICE_MONITOR, narrowed)
        assert gate.main(argv) == 1
        assert "narrows it with namespaceSelector.matchExpressions" in capsys.readouterr().err

    def test_a_podselector_with_matchexpressions_is_not_credited_even_when_declared(
        self, tmp_path, capsys
    ):
        expr = self.NARROWED.replace(
            "            matchLabels:\n"
            "              app.kubernetes.io/name: prometheus\n",
            "            matchExpressions:\n"
            "              - {key: app.kubernetes.io/name, operator: Exists}\n",
        )
        argv = tree(tmp_path, DEPLOYMENT, SERVICE, SERVICE_MONITOR, expr)
        assert gate.main(argv + self.DECLARED) == 1
        assert "narrows it with podSelector" in capsys.readouterr().err

    def test_an_extra_namespace_label_narrows_the_peer(self, tmp_path, capsys):
        """A second matchLabels entry beside the name asks for a namespace label
        nothing here can resolve, so the peer is not credited."""
        extra = self.NARROWED.replace(
            "              kubernetes.io/metadata.name: observability\n",
            "              kubernetes.io/metadata.name: observability\n"
            "              tier: platform\n",
        )
        argv = tree(tmp_path, DEPLOYMENT, SERVICE, SERVICE_MONITOR, extra)
        assert gate.main(argv + self.DECLARED) == 1
        assert "narrows it with namespaceSelector.matchLabels.tier" in capsys.readouterr().err

    def test_a_peer_naming_another_namespace_is_not_credited(self, tmp_path):
        elsewhere = self.NARROWED.replace(
            "kubernetes.io/metadata.name: observability", "kubernetes.io/metadata.name: other"
        )
        argv = tree(tmp_path, DEPLOYMENT, SERVICE, SERVICE_MONITOR, elsewhere)
        assert gate.main(argv + self.DECLARED) == 1


def test_an_egress_only_policy_does_not_admit_the_scrape(tmp_path):
    egress = policy().replace("policyTypes: [Ingress]", "policyTypes: [Egress]")
    argv = tree(tmp_path, DEPLOYMENT, SERVICE, SERVICE_MONITOR, egress)
    assert gate.main(argv) == 1


def test_a_matchnames_entry_is_refused_without_the_namespace_option(tmp_path, capsys):
    scoped = SERVICE_MONITOR.replace(
        "spec:\n", "spec:\n  namespaceSelector:\n    matchNames: [app]\n", 1
    )
    argv = tree(tmp_path, DEPLOYMENT, SERVICE, scoped, policy())
    assert gate.main(argv) == 2
    assert "without --namespace" in capsys.readouterr().err


def test_a_matchnames_entry_equal_to_the_namespace_passes(tmp_path):
    scoped = SERVICE_MONITOR.replace(
        "spec:\n", "spec:\n  namespaceSelector:\n    matchNames: [app]\n", 1
    )
    argv = tree(tmp_path, DEPLOYMENT, SERVICE, scoped, policy())
    assert gate.main([*argv, "--namespace", "app"]) == 0


def test_a_matchnames_entry_for_another_namespace_is_refused(tmp_path, capsys):
    scoped = SERVICE_MONITOR.replace(
        "spec:\n", "spec:\n  namespaceSelector:\n    matchNames: [elsewhere]\n", 1
    )
    argv = tree(tmp_path, DEPLOYMENT, SERVICE, scoped, policy())
    assert gate.main([*argv, "--namespace", "app"]) == 2
    assert "against --namespace app" in capsys.readouterr().err


def test_the_scrape_namespace_is_an_option(tmp_path):
    renamed = policy().replace(
        "kubernetes.io/metadata.name: observability",
        "kubernetes.io/metadata.name: monitoring",
    )
    argv = tree(tmp_path, DEPLOYMENT, SERVICE, SERVICE_MONITOR, renamed)
    assert gate.main(argv) == 1
    assert gate.main([*argv, "--observability-namespace", "monitoring"]) == 0


class TestTheGateRefusesToBeVacuous:
    """A run that inspected no monitor must not report green while a policy
    declares that something here is scraped."""

    def test_a_scrape_allow_with_no_monitor_is_an_operator_error(self, tmp_path, capsys):
        argv = tree(tmp_path, DEPLOYMENT, SERVICE, policy())
        assert gate.main(argv) == 2
        assert "a gate that checks nothing is not a gate" in capsys.readouterr().err

    def test_a_corpus_with_no_kinded_document_is_an_operator_error(self, tmp_path, capsys):
        (tmp_path / "notes.yaml").write_text("# nothing here\n")
        assert gate.main([str(tmp_path)]) == 2
        assert "a gate that checks nothing is not a gate" in capsys.readouterr().err

    def test_a_nonexistent_directory_is_an_operator_error(self, tmp_path, capsys):
        assert gate.main([str(tmp_path / "gone")]) == 2
        assert "is not a directory" in capsys.readouterr().err

    def test_an_unparseable_manifest_is_an_operator_error(self, tmp_path, capsys):
        tree(tmp_path, DEPLOYMENT, SERVICE, SERVICE_MONITOR, policy())
        (tmp_path / "broken.yaml").write_text("ingress: [oops\n")
        assert gate.main([str(tmp_path)]) == 2
        err = capsys.readouterr().err
        assert "unparseable YAML" in err and "<unicode string>" not in err

    def test_a_monitor_selecting_every_pod_is_an_operator_error(self, tmp_path, capsys):
        monitor = SERVICE_MONITOR.replace(
            "  selector:\n    matchLabels:\n      app.kubernetes.io/name: app\n",
            "  selector: {}\n",
        )
        argv = tree(tmp_path, DEPLOYMENT, SERVICE, monitor, policy())
        assert gate.main(argv) == 2
        assert "selects every pod" in capsys.readouterr().err


def test_unmodelled_notes_do_not_leak_between_runs(tmp_path, capsys):
    unmodelled = policy().replace(
        "  podSelector:\n    matchLabels:\n      app.kubernetes.io/name: app\n",
        "  podSelector:\n    matchExpressions:\n      - {key: a, operator: Exists}\n",
    )
    assert gate.main(tree(tmp_path, DEPLOYMENT, SERVICE, SERVICE_MONITOR, unmodelled)) == 1
    capsys.readouterr()
    assert gate.main(tree(tmp_path, DEPLOYMENT, SERVICE, SERVICE_MONITOR, policy())) == 0
    assert "matchExpressions" not in capsys.readouterr().out


NAMESPACE = """\
apiVersion: v1
kind: Namespace
metadata:
  name: app
"""

SCOPED_MONITOR = SERVICE_MONITOR.replace(
    "spec:\n", "spec:\n  namespaceSelector:\n    matchNames: [app]\n", 1
)


class TestNamespaceFromTree:
    """`--namespace-from-tree` is for a byte-identical pipeline, which cannot
    carry a per-tenant `--namespace` value."""

    def test_the_tree_namespace_verifies_a_matchnames_entry(self, tmp_path):
        argv = tree(tmp_path, NAMESPACE, DEPLOYMENT, SERVICE, SCOPED_MONITOR, policy())
        assert gate.main([*argv, "--namespace-from-tree"]) == 0

    def test_a_matchnames_entry_for_another_namespace_is_refused(self, tmp_path, capsys):
        elsewhere = SERVICE_MONITOR.replace(
            "spec:\n", "spec:\n  namespaceSelector:\n    matchNames: [elsewhere]\n", 1
        )
        argv = tree(tmp_path, NAMESPACE, DEPLOYMENT, SERVICE, elsewhere, policy())
        assert gate.main([*argv, "--namespace-from-tree"]) == 2
        assert "against --namespace app" in capsys.readouterr().err

    def test_a_tree_with_no_namespace_manifest_still_passes_an_unscoped_monitor(
        self, tmp_path
    ):
        """The shipped tenant layout: the operator owns the Namespace, and the
        monitor names none, so the derivation is never consulted."""
        argv = tree(tmp_path, DEPLOYMENT, SERVICE, SERVICE_MONITOR, policy())
        assert gate.main([*argv, "--namespace-from-tree"]) == 0

    def test_a_tree_with_no_namespace_manifest_refuses_a_matchnames_entry(
        self, tmp_path, capsys
    ):
        argv = tree(tmp_path, DEPLOYMENT, SERVICE, SCOPED_MONITOR, policy())
        assert gate.main([*argv, "--namespace-from-tree"]) == 2
        assert "found no named Namespace manifest" in capsys.readouterr().err

    def test_two_namespace_manifests_are_refused(self, tmp_path, capsys):
        second = NAMESPACE.replace("name: app", "name: other")
        argv = tree(
            tmp_path, NAMESPACE, second, DEPLOYMENT, SERVICE, SCOPED_MONITOR, policy()
        )
        assert gate.main([*argv, "--namespace-from-tree"]) == 2
        assert "found 2 of them here (app, other)" in capsys.readouterr().err

    def test_the_same_namespace_twice_is_one_namespace(self, tmp_path):
        argv = tree(
            tmp_path, NAMESPACE, NAMESPACE, DEPLOYMENT, SERVICE, SCOPED_MONITOR,
            policy(),
        )
        assert gate.main([*argv, "--namespace-from-tree"]) == 0

    def test_the_two_namespace_options_are_mutually_exclusive(self, tmp_path):
        argv = tree(tmp_path, NAMESPACE, DEPLOYMENT, SERVICE, SCOPED_MONITOR, policy())
        with pytest.raises(SystemExit):
            gate.main([*argv, "--namespace-from-tree", "--namespace", "app"])


# --- shapes a vacuous pass turned on -----------------------------------------


def test_a_selectorless_service_is_not_credited(tmp_path, capsys):
    """Manual endpoints: the Service points at no workload here, and an empty
    selector widened to the monitor's labels, certifying a policy against none."""
    manual = SERVICE.replace("  selector:\n    app.kubernetes.io/name: app\n", "")
    argv = tree(tmp_path, DEPLOYMENT, manual, SERVICE_MONITOR, policy())
    assert gate.main(argv) == 1
    assert "no spec.selector" in capsys.readouterr().err


def test_an_empty_service_selector_is_not_credited(tmp_path, capsys):
    empty = SERVICE.replace("  selector:\n    app.kubernetes.io/name: app\n", "  selector: {}\n")
    argv = tree(tmp_path, DEPLOYMENT, empty, SERVICE_MONITOR, policy())
    assert gate.main(argv) == 1
    assert "no spec.selector" in capsys.readouterr().err


class TestOnePortNameAtTwoNumbers:
    """`targetPort: metrics` resolves per pod, so two selected workloads may
    serve the one Service port at different numbers; every number is a target."""

    SHARD = DEPLOYMENT.replace("name: app\n", "name: app-shard\n", 1).replace(
        "containerPort: 9100", "containerPort: 9200"
    )

    def test_a_policy_admitting_every_number_passes(self, tmp_path):
        both = policy().replace(
            "          port: 9100\n",
            "          port: 9100\n        - protocol: TCP\n          port: 9200\n",
        )
        argv = tree(tmp_path, DEPLOYMENT, self.SHARD, SERVICE, SERVICE_MONITOR, both)
        assert gate.main(argv) == 0

    def test_the_number_left_out_is_a_violation(self, tmp_path, capsys):
        argv = tree(tmp_path, DEPLOYMENT, self.SHARD, SERVICE, SERVICE_MONITOR, policy())
        assert gate.main(argv) == 1
        err = capsys.readouterr().err
        assert "on port 9200" in err
        assert "Deployment app-shard" in err

    def test_a_policy_naming_the_port_covers_both(self, tmp_path):
        """A named policy port is resolved against each pod's own ports, so one
        name admits both numbers."""
        argv = tree(
            tmp_path, DEPLOYMENT, self.SHARD, SERVICE, SERVICE_MONITOR, policy("metrics")
        )
        assert gate.main(argv) == 0

    def test_each_workload_is_judged_against_the_policies_matching_it(self, tmp_path):
        """One policy per shard, each naming only that shard's number."""
        shard = self.SHARD.replace(
            "        app.kubernetes.io/name: app",
            "        app.kubernetes.io/name: app\n        shard: two",
        )
        narrowed = policy(9200).replace(
            "      app.kubernetes.io/name: app\n  policyTypes",
            "      app.kubernetes.io/name: app\n      shard: two\n  policyTypes",
        ).replace("name: allow-scrape", "name: allow-scrape-two")
        argv = tree(
            tmp_path, DEPLOYMENT, shard, SERVICE, SERVICE_MONITOR, policy(), narrowed
        )
        assert gate.main(argv) == 0


def test_the_same_port_name_at_one_number_is_fine(tmp_path):
    """Two replicas of the same shape are not an ambiguity."""
    second = DEPLOYMENT.replace("name: app\n", "name: app-shard\n", 1)
    argv = tree(tmp_path, DEPLOYMENT, second, SERVICE, SERVICE_MONITOR, policy())
    assert gate.main(argv) == 0


def test_each_namespace_is_judged_against_its_own_policies(tmp_path, capsys):
    """A two-namespace tree: the policy in `a` must not admit the scrape in `b`."""

    def namespaced(document: str, namespace: str) -> str:
        return document.replace("metadata:\n", f"metadata:\n  namespace: {namespace}\n", 1)

    argv = tree(
        tmp_path,
        *(namespaced(d, "a") for d in (DEPLOYMENT, SERVICE, SERVICE_MONITOR, policy())),
        *(namespaced(d, "b") for d in (DEPLOYMENT, SERVICE, SERVICE_MONITOR)),
    )
    assert gate.main(argv) == 1
    assert "no NetworkPolicy admits namespace observability" in capsys.readouterr().err


def test_both_namespaces_wired_passes_and_counts_every_monitor(tmp_path, capsys):
    def namespaced(document: str, namespace: str) -> str:
        return document.replace("metadata:\n", f"metadata:\n  namespace: {namespace}\n", 1)

    documents = (DEPLOYMENT, SERVICE, SERVICE_MONITOR, policy())
    argv = tree(
        tmp_path,
        *(namespaced(d, "a") for d in documents),
        *(namespaced(d, "b") for d in documents),
    )
    assert gate.main(argv) == 0
    assert "2 monitor(s)" in capsys.readouterr().out


def test_a_tree_disagreeing_with_the_namespace_option_is_refused(tmp_path, capsys):
    stated = SERVICE_MONITOR.replace("metadata:\n", "metadata:\n  namespace: elsewhere\n", 1)
    argv = tree(tmp_path, DEPLOYMENT, SERVICE, stated, policy())
    assert gate.main([*argv, "--namespace", "app"]) == 2
    assert "disagrees with the namespaces the tree states" in capsys.readouterr().err


def test_one_stated_namespace_reaches_the_documents_naming_none(tmp_path):
    """The Kustomization's own behaviour: a lone stated namespace is the tree's."""
    stated = SERVICE_MONITOR.replace("metadata:\n", "metadata:\n  namespace: app\n", 1)
    argv = tree(tmp_path, DEPLOYMENT, SERVICE, stated, policy())
    assert gate.main(argv) == 0


def test_an_unassignable_document_in_a_multi_namespace_tree_is_refused(
    tmp_path, capsys
):
    def namespaced(document: str, namespace: str) -> str:
        return document.replace("metadata:\n", f"metadata:\n  namespace: {namespace}\n", 1)

    argv = tree(
        tmp_path,
        *(namespaced(d, "a") for d in (DEPLOYMENT, SERVICE, SERVICE_MONITOR, policy())),
        *(namespaced(d, "b") for d in (DEPLOYMENT, SERVICE, SERVICE_MONITOR)),
        policy(),
    )
    assert gate.main(argv) == 2
    assert "names none for NetworkPolicy allow-scrape" in capsys.readouterr().err


CLUSTER_SCOPED = (
    "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: a\n",
    "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: b\n",
    "apiVersion: apiextensions.k8s.io/v1\nkind: CustomResourceDefinition\n"
    "metadata:\n  name: widgets.example.com\n",
    "apiVersion: rbac.authorization.k8s.io/v1\nkind: ClusterRole\n"
    "metadata:\n  name: app-reader\n",
    "apiVersion: scheduling.k8s.io/v1\nkind: PriorityClass\n"
    "metadata:\n  name: app-high\nvalue: 1000\n",
)


def _namespaced(document: str, namespace: str) -> str:
    return document.replace("metadata:\n", f"metadata:\n  namespace: {namespace}\n", 1)


def _two_namespaces() -> list[str]:
    """A wired monitor, Service, workload and policy in each of `a` and `b`."""
    documents = (DEPLOYMENT, SERVICE, SERVICE_MONITOR, policy())
    return [_namespaced(d, ns) for ns in ("a", "b") for d in documents]


def test_a_cluster_scoped_document_does_not_make_a_tree_ambiguous(tmp_path, capsys):
    """A Namespace or CRD carries no namespace because it has none, so counting
    it as unassigned refused a two-namespace tree that is wired correctly."""
    argv = tree(tmp_path, *_two_namespaces(), *CLUSTER_SCOPED)
    assert gate.main(argv) == 0
    assert "2 monitor(s)" in capsys.readouterr().out


def test_a_namespaced_document_naming_none_is_still_refused_beside_them(
    tmp_path, capsys
):
    """The guard narrows to namespaced kinds; it does not go away."""
    argv = tree(tmp_path, *_two_namespaces(), *CLUSTER_SCOPED, policy())
    assert gate.main(argv) == 2
    err = capsys.readouterr().err
    assert "names none for NetworkPolicy allow-scrape:" in err
    assert "Namespace" not in err


def test_the_tree_namespace_is_still_read_from_the_ungrouped_namespace(tmp_path):
    """Skipping the Namespace for grouping must not drop it from the tree the
    `--namespace-from-tree` derivation reads."""
    scoped = SERVICE_MONITOR.replace(
        "spec:\n", "spec:\n  namespaceSelector:\n    matchNames: [a]\n", 1
    )
    argv = tree(tmp_path, DEPLOYMENT, SERVICE, scoped, policy(), CLUSTER_SCOPED[0])
    assert gate.main([*argv, "--namespace-from-tree"]) == 0


def test_a_tree_of_only_cluster_scoped_documents_checks_nothing(tmp_path, capsys):
    argv = tree(tmp_path, *CLUSTER_SCOPED)
    assert gate.main(argv) == 0
    assert "no ServiceMonitor or PodMonitor" in capsys.readouterr().out


def test_a_kustomization_file_does_not_make_a_tree_ambiguous(tmp_path, capsys):
    """kustomize's own documents are build inputs, so they hold no namespace."""
    kustomization = (
        "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\n"
        "resources:\n  - manifests.yaml\n"
    )
    component = (
        "apiVersion: kustomize.config.k8s.io/v1alpha1\nkind: Component\n"
        "resources:\n  - manifests.yaml\n"
    )
    argv = tree(tmp_path, *_two_namespaces(), kustomization, component)
    assert gate.main(argv) == 0
    assert "2 monitor(s)" in capsys.readouterr().out


def test_a_flux_kustomization_naming_no_namespace_is_still_refused(tmp_path, capsys):
    """Flux's CR shares the kind name but is namespaced, so the carve-out is by
    API group and this one still counts toward the ambiguity."""
    flux = (
        "apiVersion: kustomize.toolkit.fluxcd.io/v1\nkind: Kustomization\n"
        "metadata:\n  name: apps\n"
    )
    argv = tree(tmp_path, *_two_namespaces(), flux)
    assert gate.main(argv) == 2
    assert "names none for Kustomization apps:" in capsys.readouterr().err


# --- hand-written endpoints ---------------------------------------------------


ENDPOINT_SLICE = """\
apiVersion: discovery.k8s.io/v1
kind: EndpointSlice
metadata:
  name: app
  labels:
    kubernetes.io/service-name: app
addressType: IPv4
ports:
  - name: metrics
    port: 9100
    protocol: TCP
endpoints:
  - addresses: ["10.0.10.153"]
"""

SELECTORLESS = SERVICE.replace("  selector:\n    app.kubernetes.io/name: app\n", "")


class TestHandWrittenEndpoints:
    """A selectorless Service whose addresses the repo declares itself: the
    target is not a pod here, so no NetworkPolicy governs the scrape."""

    def test_an_endpointslice_makes_the_target_a_non_pod_one(self, tmp_path, capsys):
        argv = tree(tmp_path, DEPLOYMENT, SELECTORLESS, SERVICE_MONITOR, ENDPOINT_SLICE)
        assert gate.main(argv) == 0
        assert "1 target(s) have hand-written endpoints" in capsys.readouterr().out

    def test_an_endpoints_object_counts_too(self, tmp_path):
        endpoints = (
            "apiVersion: v1\nkind: Endpoints\nmetadata:\n  name: app\n"
            "subsets:\n  - addresses: [{ip: 10.0.10.153}]\n"
            "    ports: [{name: metrics, port: 9100}]\n"
        )
        argv = tree(tmp_path, DEPLOYMENT, SELECTORLESS, SERVICE_MONITOR, endpoints)
        assert gate.main(argv) == 0

    def test_a_slice_with_no_address_is_no_target(self, tmp_path, capsys):
        """The object exists, so only its contents tell the scrape from nothing."""
        empty = ENDPOINT_SLICE.replace('endpoints:\n  - addresses: ["10.0.10.153"]\n', "")
        argv = tree(tmp_path, DEPLOYMENT, SELECTORLESS, SERVICE_MONITOR, empty)
        assert gate.main(argv) == 1
        assert "hold no address on 'metrics'" in capsys.readouterr().err

    def test_a_slice_naming_another_port_is_no_target(self, tmp_path, capsys):
        """Prometheus keeps the target by the endpoint port's name."""
        other = ENDPOINT_SLICE.replace("name: metrics", "name: health")
        argv = tree(tmp_path, DEPLOYMENT, SELECTORLESS, SERVICE_MONITOR, other)
        assert gate.main(argv) == 1
        assert "hold no address on 'metrics'" in capsys.readouterr().err

    def test_a_numberless_entry_under_another_name_is_no_target(self, tmp_path, capsys):
        """The name is what Prometheus keeps, so an unnumbered `health` entry
        does not stand in for `metrics`."""
        other = ENDPOINT_SLICE.replace("name: metrics\n    port: 9100\n", "name: health\n")
        argv = tree(tmp_path, DEPLOYMENT, SELECTORLESS, SERVICE_MONITOR, other)
        assert gate.main(argv) == 1
        assert "hold no address on 'metrics'" in capsys.readouterr().err

    def test_an_endpoints_subset_pairs_its_addresses_with_its_own_ports(
        self, tmp_path, capsys
    ):
        """One subset holds the addresses, another the port: neither serves."""
        endpoints = (
            "apiVersion: v1\nkind: Endpoints\nmetadata:\n  name: app\n"
            "subsets:\n  - addresses: [{ip: 10.0.10.153}]\n    ports: []\n"
            "  - addresses: []\n    ports: [{name: metrics, port: 9100}]\n"
        )
        argv = tree(tmp_path, DEPLOYMENT, SELECTORLESS, SERVICE_MONITOR, endpoints)
        assert gate.main(argv) == 1
        assert "hold no address on 'metrics'" in capsys.readouterr().err

    def test_addresses_a_substitution_fills_in_are_credited(self, tmp_path):
        """A `${...}` placeholder is a scalar here: its addresses are unknowable."""
        deferred = ENDPOINT_SLICE.replace(
            'endpoints:\n  - addresses: ["10.0.10.153"]\n',
            "endpoints: ${cluster_exporter_addresses}\n",
        )
        argv = tree(tmp_path, DEPLOYMENT, SELECTORLESS, SERVICE_MONITOR, deferred)
        assert gate.main(argv) == 0

    def test_a_targetport_monitor_matches_the_endpoint_number(self, tmp_path):
        """`targetPort` addresses the port directly, so the number is the match."""
        monitor = SERVICE_MONITOR.replace("    - port: metrics\n", "    - targetPort: 9100\n")
        unnamed = ENDPOINT_SLICE.replace(
            "  - name: metrics\n    port: 9100\n", "  - port: 9100\n"
        )
        argv = tree(tmp_path, DEPLOYMENT, SELECTORLESS, monitor, unnamed)
        assert gate.main(argv) == 0

    def test_a_scalar_only_carrying_a_placeholder_is_an_operator_error(self, tmp_path, capsys):
        """Substitution leaves `none-...` a scalar still, so only a scalar that IS
        the placeholder is credited."""
        partial = ENDPOINT_SLICE.replace(
            'endpoints:\n  - addresses: ["10.0.10.153"]\n',
            "endpoints: none-${cluster_exporter_addresses}\n",
        )
        argv = tree(tmp_path, DEPLOYMENT, SELECTORLESS, SERVICE_MONITOR, partial)
        assert gate.main(argv) == 2
        assert "neither a list nor a ${...} substitution" in capsys.readouterr().err

    def test_a_scalar_that_is_no_substitution_is_an_operator_error(self, tmp_path, capsys):
        """Only a `${...}` placeholder legally stands where a list belongs; any
        other scalar is a shape nothing can read."""
        broken = ENDPOINT_SLICE.replace(
            'endpoints:\n  - addresses: ["10.0.10.153"]\n', "endpoints: none\n"
        )
        argv = tree(tmp_path, DEPLOYMENT, SELECTORLESS, SERVICE_MONITOR, broken)
        assert gate.main(argv) == 2
        assert "neither a list nor a ${...} substitution" in capsys.readouterr().err

    def test_a_number_where_the_ports_list_belongs_is_an_operator_error(
        self, tmp_path, capsys
    ):
        broken = ENDPOINT_SLICE.replace(
            "ports:\n  - name: metrics\n    port: 9100\n    protocol: TCP\n", "ports: 9100\n"
        )
        argv = tree(tmp_path, DEPLOYMENT, SELECTORLESS, SERVICE_MONITOR, broken)
        assert gate.main(argv) == 2
        assert "ports is 9100" in capsys.readouterr().err

    def test_an_endpointslice_for_another_service_does_not_count(self, tmp_path, capsys):
        other = ENDPOINT_SLICE.replace("kubernetes.io/service-name: app", "service-name: other")
        argv = tree(tmp_path, DEPLOYMENT, SELECTORLESS, SERVICE_MONITOR, other)
        assert gate.main(argv) == 1
        assert "no spec.selector" in capsys.readouterr().err


# --- the rendered-corpus arm --------------------------------------------------


SCRAPING_MONITOR = SERVICE_MONITOR.replace(
    "metadata:\n", "metadata:\n  namespace: observability\n", 1
).replace("spec:\n", "spec:\n  namespaceSelector:\n    matchNames: [app]\n", 1)


def corpus_documents(*extra: str) -> list[str]:
    """A monitor in `observability` scraping the workload in `app`."""
    return [
        _namespaced(DEPLOYMENT, "app"),
        _namespaced(SERVICE, "app"),
        SCRAPING_MONITOR,
        *extra,
    ]


def corpus(tmp_path, *documents: str) -> str:
    """One multi-document stream, the shape `kustomize build` writes."""
    path = tmp_path / "render-all.yaml"
    path.write_text("\n---\n".join(textwrap.dedent(d) for d in documents))
    return str(path)


class TestRenderedCorpus:
    """`--corpus`: a whole rendered cluster, so a monitor's matchNames resolves
    against the namespace its pods and their policies live in."""

    def test_a_cross_namespace_monitor_wired_right_passes(self, tmp_path, capsys):
        stream = corpus(tmp_path, *corpus_documents(_namespaced(policy(), "app")))
        assert gate.main(["--corpus", stream]) == 0
        assert "1 monitor(s)" in capsys.readouterr().out

    def test_the_same_monitor_is_refused_as_a_tree(self, tmp_path, capsys):
        """The tree arm covers one namespace, so it cannot verify this scope."""
        argv = tree(tmp_path, *corpus_documents(_namespaced(policy(), "app")))
        assert gate.main(argv) == 2
        assert "cannot be verified" in capsys.readouterr().err

    def test_a_policy_on_the_wrong_port_is_a_violation(self, tmp_path, capsys):
        stream = corpus(tmp_path, *corpus_documents(_namespaced(policy(9101), "app")))
        assert gate.main(["--corpus", stream]) == 1
        err = capsys.readouterr().err
        assert "namespace app" in err
        assert "on port 9100" in err

    def test_no_policy_in_the_target_namespace_is_a_violation(self, tmp_path):
        stream = corpus(tmp_path, *corpus_documents())
        assert gate.main(["--corpus", stream]) == 1

    def test_a_policy_beside_the_monitor_does_not_admit_it(self, tmp_path):
        """The policy must live where the pods are, not where the monitor is."""
        stream = corpus(
            tmp_path, *corpus_documents(_namespaced(policy(), "observability"))
        )
        assert gate.main(["--corpus", stream]) == 1

    def test_a_directory_of_rendered_manifests_is_a_corpus_too(self, tmp_path):
        corpus(tmp_path, *corpus_documents(_namespaced(policy(), "app")))
        assert gate.main(["--corpus", str(tmp_path)]) == 0

    def test_the_corpus_reads_from_stdin(self, tmp_path, monkeypatch):
        stream = pathlib.Path(
            corpus(tmp_path, *corpus_documents(_namespaced(policy(), "app")))
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(stream.read_text()))
        assert gate.main(["--corpus", "-"]) == 0

    def test_a_matchnames_namespace_absent_from_the_corpus_is_an_operator_error(
        self, tmp_path, capsys
    ):
        elsewhere = SCRAPING_MONITOR.replace("matchNames: [app]", "matchNames: [gone]")
        stream = corpus(
            tmp_path,
            _namespaced(DEPLOYMENT, "app"),
            _namespaced(SERVICE, "app"),
            _namespaced(policy(), "app"),
            elsewhere,
        )
        assert gate.main(["--corpus", stream]) == 2
        assert "holds no object in" in capsys.readouterr().err

    def test_a_monitor_naming_no_namespace_scrapes_its_own(self, tmp_path):
        documents = [
            _namespaced(d, "app") for d in (DEPLOYMENT, SERVICE, SERVICE_MONITOR, policy())
        ]
        assert gate.main(["--corpus", corpus(tmp_path, *documents)]) == 0

    def test_an_untargeted_scrape_allow_is_not_an_operator_error(self, tmp_path):
        """A chart renders monitors a corpus does not carry, so an allow here
        cannot be paired with one the way a tenant tree's is."""
        stream = corpus(
            tmp_path,
            *corpus_documents(_namespaced(policy(), "app")),
            _namespaced(policy(), "chart-owned"),
        )
        assert gate.main(["--corpus", stream]) == 0

    def test_a_corpus_with_no_monitor_at_all_is_still_refused(self, tmp_path, capsys):
        stream = corpus(
            tmp_path,
            _namespaced(DEPLOYMENT, "app"),
            _namespaced(SERVICE, "app"),
            _namespaced(policy(), "app"),
        )
        assert gate.main(["--corpus", stream]) == 2
        assert "a gate that checks nothing is not a gate" in capsys.readouterr().err

    def test_a_hand_written_endpoint_target_needs_no_policy(self, tmp_path, capsys):
        """The shape a cluster uses to scrape a host outside it."""
        stream = corpus(
            tmp_path,
            _namespaced(SELECTORLESS, "observability"),
            _namespaced(ENDPOINT_SLICE, "observability"),
            SERVICE_MONITOR.replace(
                "metadata:\n", "metadata:\n  namespace: observability\n", 1
            ),
        )
        assert gate.main(["--corpus", stream]) == 0
        assert "1 target(s) have hand-written endpoints" in capsys.readouterr().out

    def test_the_corpus_and_a_directory_are_not_both_read(self, tmp_path, capsys):
        stream = corpus(tmp_path, *corpus_documents(_namespaced(policy(), "app")))
        assert gate.main(["--corpus", stream, str(tmp_path)]) == 2
        assert "--corpus replaces the manifest directory" in capsys.readouterr().err

    def test_the_corpus_and_the_namespace_options_are_mutually_exclusive(self, tmp_path):
        stream = corpus(tmp_path, *corpus_documents(_namespaced(policy(), "app")))
        with pytest.raises(SystemExit):
            gate.main(["--corpus", stream, "--namespace", "app"])
        with pytest.raises(SystemExit):
            gate.main(["--corpus", stream, "--namespace-from-tree"])

    def test_an_unreadable_corpus_is_an_operator_error(self, tmp_path, capsys):
        assert gate.main(["--corpus", str(tmp_path / "gone.yaml")]) == 2
        assert "unreadable" in capsys.readouterr().err


class TestPodsAChartRenders:
    """A HelmRelease's workloads reach no manifest, so the namespace a corpus
    carries for them holds policies and nothing to match them against."""

    def _corpus(self, tmp_path, *extra: str) -> str:
        return corpus(
            tmp_path,
            _namespaced("apiVersion: v1\nkind: Namespace\nmetadata:\n  name: app\n", "app"),
            _namespaced(policy(), "app"),
            SCRAPING_MONITOR,
            *extra,
        )

    def test_a_namespace_rendering_no_workload_is_counted(self, tmp_path, capsys):
        assert gate.main(["--corpus", self._corpus(tmp_path)]) == 0
        assert "1 endpoint(s) scrape pods a chart renders" in capsys.readouterr().out

    def test_a_service_the_namespace_carries_is_judged_as_written(self, tmp_path, capsys):
        """No workload, but a selectorless Service for the monitor whose
        hand-written endpoints carry no address: that is broken wiring, not
        a chart's."""
        empty_slice = ENDPOINT_SLICE.replace(
            'endpoints:\n  - addresses: ["10.0.10.153"]\n', "endpoints: []\n"
        )
        stream = self._corpus(
            tmp_path, _namespaced(SELECTORLESS, "app"), _namespaced(empty_slice, "app")
        )
        assert gate.main(["--corpus", stream]) == 1
        assert "a chart renders" not in capsys.readouterr().out

    def test_the_same_namespace_is_judged_once_it_renders_one(self, tmp_path, capsys):
        wrong_port = DEPLOYMENT.replace("name: metrics", "name: http")
        stream = self._corpus(
            tmp_path, _namespaced(wrong_port, "app"), _namespaced(SERVICE, "app")
        )
        assert gate.main(["--corpus", stream]) == 1
        assert "declares a port named 'metrics'" in capsys.readouterr().err

    def test_the_tree_arm_still_reports_it(self, tmp_path, capsys):
        """Outside a corpus a missing workload is a tree that cannot be right,
        not a chart's."""
        argv = tree(tmp_path, SERVICE, SERVICE_MONITOR, policy())
        assert gate.main(argv) == 1
        assert "declares a port named 'metrics'" in capsys.readouterr().err


class TestCorpusWideScope:
    """`any: true` reaches every namespace the corpus carries, so the monitor is
    judged on their union."""

    ANY = SERVICE_MONITOR.replace(
        "metadata:\n", "metadata:\n  namespace: observability\n", 1
    ).replace("spec:\n", "spec:\n  namespaceSelector:\n    any: true\n", 1)

    def test_a_namespace_with_no_matching_service_contributes_nothing(self, tmp_path):
        stream = corpus(
            tmp_path,
            _namespaced(DEPLOYMENT, "app"),
            _namespaced(SERVICE, "app"),
            _namespaced(policy(), "app"),
            _namespaced(DEPLOYMENT, "quiet"),
            self.ANY,
        )
        assert gate.main(["--corpus", stream]) == 0

    def test_a_second_namespace_serving_the_scrape_needs_its_own_policy(
        self, tmp_path, capsys
    ):
        stream = corpus(
            tmp_path,
            *(_namespaced(d, "app") for d in (DEPLOYMENT, SERVICE, policy())),
            *(_namespaced(d, "other") for d in (DEPLOYMENT, SERVICE)),
            self.ANY,
        )
        assert gate.main(["--corpus", stream]) == 1
        assert "namespace other" in capsys.readouterr().err

    def test_no_namespace_serving_the_scrape_is_a_violation(self, tmp_path, capsys):
        stream = corpus(tmp_path, _namespaced(DEPLOYMENT, "app"), self.ANY)
        assert gate.main(["--corpus", stream]) == 1
        assert "no Service carries the monitor's labels" in capsys.readouterr().err

    def test_a_selected_but_misconfigured_service_elsewhere_is_still_a_finding(
        self, tmp_path, capsys
    ):
        """A wired target in one namespace does not answer for a Service the
        monitor selects in another."""
        broken = SERVICE.replace("      targetPort: metrics\n", "      targetPort: absent\n")
        stream = corpus(
            tmp_path,
            *(_namespaced(d, "app") for d in (DEPLOYMENT, SERVICE, policy())),
            *(_namespaced(d, "other") for d in (DEPLOYMENT, broken, policy())),
            self.ANY,
        )
        assert gate.main(["--corpus", stream]) == 1
        err = capsys.readouterr().err
        assert "namespace other" in err
        assert "declares a port named 'absent'" in err

    def test_the_tree_arm_still_refuses_it(self, tmp_path, capsys):
        argv = tree(tmp_path, DEPLOYMENT, SERVICE, self.ANY, policy())
        assert gate.main(argv) == 2
        assert "scopes the scrape outside this directory" in capsys.readouterr().err


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
