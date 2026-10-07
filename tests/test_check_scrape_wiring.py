"""Tests for scripts/check-scrape-wiring.py: the scraped PORT, not just the namespace.

Each arm is driven from a fixture tree so a mutation that stops the gate
resolving a Service hop, a PodMonitor endpoint or a port spelling turns red.
"""
from __future__ import annotations

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


def test_a_matchexpressions_pod_selector_is_not_credited(tmp_path, capsys):
    unmodelled = policy().replace(
        "  podSelector:\n    matchLabels:\n      app.kubernetes.io/name: app\n",
        "  podSelector:\n    matchExpressions:\n      - {key: a, operator: Exists}\n",
    )
    argv = tree(tmp_path, DEPLOYMENT, SERVICE, SERVICE_MONITOR, unmodelled)
    assert gate.main(argv) == 1
    assert "matchExpressions, which this gate does not model" in capsys.readouterr().err


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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
