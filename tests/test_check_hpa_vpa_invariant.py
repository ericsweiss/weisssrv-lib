"""Tests for scripts/check-hpa-vpa-invariant.py."""
from __future__ import annotations

import io
from pathlib import Path

import pytest
import yaml
from script_loader import load_script

mod = load_script("check-hpa-vpa-invariant.py")


# The chart-native target list is consumer data (--policy-config), so the suite
# declares its own and feeds it to the gate.
TARGETS = [
    {"namespace": "traefik", "kind": "Deployment", "name": "traefik",
     "source": "traefik chart autoscaling.enabled"},
    {"namespace": "apps", "kind": "Deployment", "name": "web", "source": "chart autoscaling"},
]


@pytest.fixture()
def policy_file(tmp_path) -> Path:
    p = tmp_path / "policy.yaml"
    p.write_text(yaml.safe_dump({"chart_native_hpa_targets": TARGETS}))
    return p


def _run(stdin_text: str, monkeypatch, argv: list[str] | None = None) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    try:
        return mod.main(argv or [])
    except SystemExit as exc:  # the shared corpus loader exits 2 directly
        return int(exc.code)


CPU_HPA = """
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata: {name: foo, namespace: ns}
spec:
  scaleTargetRef: {apiVersion: apps/v1, kind: Deployment, name: foo}
  metrics:
    - type: Resource
      resource: {name: cpu, target: {type: Utilization, averageUtilization: 80}}
"""


def _vpa(controlled: str, name: str = "foo") -> str:
    return f"""
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata: {{name: {name}, namespace: ns}}
spec:
  targetRef: {{apiVersion: apps/v1, kind: Deployment, name: foo}}
  resourcePolicy:
    containerPolicies:
      - containerName: "*"
        controlledResources: [{controlled}]
"""


def test_memory_only_vpa_with_cpu_hpa_passes(monkeypatch, policy_file):
    assert _run(CPU_HPA + "---" + _vpa("memory"), monkeypatch) == 0


def test_cpu_vpa_with_cpu_hpa_fails(monkeypatch):
    assert _run(CPU_HPA + "---" + _vpa("cpu, memory"), monkeypatch) == 1


def test_vpa_default_controlled_resources_fails(monkeypatch):
    """No controlledResources means cpu+memory — clashes with a CPU HPA."""
    vpa = """
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata: {name: foo, namespace: ns}
spec:
  targetRef: {apiVersion: apps/v1, kind: Deployment, name: foo}
  resourcePolicy:
    containerPolicies:
      - containerName: "*"
        minAllowed: {memory: 32Mi}
"""
    assert _run(CPU_HPA + "---" + vpa, monkeypatch) == 1


def test_hpa_without_matching_vpa_passes(monkeypatch):
    assert _run(CPU_HPA, monkeypatch) == 0


def test_different_namespace_does_not_clash(monkeypatch):
    other_ns_vpa = _vpa("cpu, memory").replace("namespace: ns", "namespace: other")
    assert _run(CPU_HPA + "---" + other_ns_vpa, monkeypatch) == 0


def test_memory_hpa_vs_memory_vpa_fails(monkeypatch):
    mem_hpa = CPU_HPA.replace("name: cpu", "name: memory")
    assert _run(mem_hpa + "---" + _vpa("memory"), monkeypatch) == 1


EXTERNAL_HPA = """
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata: {name: foo, namespace: ns}
spec:
  scaleTargetRef: {apiVersion: apps/v1, kind: Deployment, name: foo}
  metrics:
    - type: External
      external:
        metric: {name: queue_depth}
        target: {type: AverageValue, averageValue: "10"}
"""


def test_external_only_hpa_with_cpu_vpa_passes(monkeypatch):
    """metrics present but purely External — no Resource metric, so no clash."""
    assert _run(EXTERNAL_HPA + "---" + _vpa("cpu, memory"), monkeypatch) == 0


def test_two_vpas_one_cpu_one_memory_fails(monkeypatch):
    """Two VPAs on one target must be unioned: the cpu one must not be masked."""
    mem_vpa = _vpa("memory", name="foo-mem")
    cpu_vpa = _vpa("cpu", name="foo-cpu")
    assert _run(CPU_HPA + "---" + mem_vpa + "---" + cpu_vpa, monkeypatch) == 1


def test_update_mode_off_vpa_does_not_clash(monkeypatch):
    """A recommend-only (Off) VPA never mutates pods — coredns pattern."""
    off_vpa = """
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata: {name: foo, namespace: ns}
spec:
  targetRef: {apiVersion: apps/v1, kind: Deployment, name: foo}
  updatePolicy: {updateMode: "Off"}
  resourcePolicy:
    containerPolicies:
      - containerName: "*"
        controlledResources: [cpu, memory]
"""
    assert _run(CPU_HPA + "---" + off_vpa, monkeypatch) == 0


CONTAINER_RESOURCE_HPA = """
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata: {name: foo, namespace: ns}
spec:
  scaleTargetRef: {apiVersion: apps/v1, kind: Deployment, name: foo}
  metrics:
    - type: ContainerResource
      containerResource:
        name: cpu
        container: app
        target: {type: Utilization, averageUtilization: 80}
"""


def test_container_resource_hpa_with_cpu_vpa_fails(monkeypatch):
    """A per-container (ContainerResource) CPU HPA still clashes with a cpu VPA."""
    assert _run(CONTAINER_RESOURCE_HPA + "---" + _vpa("cpu, memory"), monkeypatch) == 1


def test_container_policy_off_does_not_clash(monkeypatch):
    """A per-container Off policy is recommend-only and must not count as mutating."""
    off_container_vpa = """
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata: {name: foo, namespace: ns}
spec:
  targetRef: {apiVersion: apps/v1, kind: Deployment, name: foo}
  resourcePolicy:
    containerPolicies:
      - containerName: "*"
        mode: "Off"
        controlledResources: [cpu, memory]
"""
    assert _run(CPU_HPA + "---" + off_container_vpa, monkeypatch) == 0


def test_named_container_memory_policy_still_clashes(monkeypatch):
    """A memory-only policy naming ONE container leaves the pod's other
    containers under default (cpu+memory) VPA control — the cpu clash with the
    HPA must not be hidden (fail closed without a '*' catch-all)."""
    named_vpa = """
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata: {name: foo, namespace: ns}
spec:
  targetRef: {apiVersion: apps/v1, kind: Deployment, name: foo}
  resourcePolicy:
    containerPolicies:
      - containerName: app
        controlledResources: [memory]
"""
    assert _run(CPU_HPA + "---" + named_vpa, monkeypatch) == 1


def test_named_container_off_policy_still_clashes(monkeypatch):
    """A mode:Off policy naming ONE container does not turn off the VPA for
    unmatched containers, which keep default cpu+memory control."""
    named_off_vpa = """
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata: {name: foo, namespace: ns}
spec:
  targetRef: {apiVersion: apps/v1, kind: Deployment, name: foo}
  resourcePolicy:
    containerPolicies:
      - containerName: app
        mode: "Off"
"""
    assert _run(CPU_HPA + "---" + named_off_vpa, monkeypatch) == 1


def test_named_policy_plus_catchall_memory_passes(monkeypatch):
    """A named policy alongside a '*' memory-only catch-all covers every
    container, so no default cpu control remains and there is no clash."""
    combo_vpa = """
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata: {name: foo, namespace: ns}
spec:
  targetRef: {apiVersion: apps/v1, kind: Deployment, name: foo}
  resourcePolicy:
    containerPolicies:
      - containerName: sidecar
        mode: "Off"
      - containerName: "*"
        controlledResources: [memory]
"""
    assert _run(CPU_HPA + "---" + combo_vpa, monkeypatch) == 0


LIST_DOC = """
apiVersion: v1
kind: List
items:
  - apiVersion: autoscaling/v2
    kind: HorizontalPodAutoscaler
    metadata: {name: foo, namespace: ns}
    spec:
      scaleTargetRef: {apiVersion: apps/v1, kind: Deployment, name: foo}
      metrics:
        - type: Resource
          resource: {name: cpu, target: {type: Utilization, averageUtilization: 80}}
  - apiVersion: autoscaling.k8s.io/v1
    kind: VerticalPodAutoscaler
    metadata: {name: foo, namespace: ns}
    spec:
      targetRef: {apiVersion: apps/v1, kind: Deployment, name: foo}
      resourcePolicy:
        containerPolicies:
          - containerName: "*"
            controlledResources: [cpu, memory]
"""


def test_list_wrapped_resources_are_expanded(monkeypatch):
    """An HPA + clashing VPA inside a kind: List must still be detected."""
    assert _run(LIST_DOC, monkeypatch) == 1


def test_malformed_yaml_is_an_operator_error(monkeypatch, capsys):
    """Exit 1 means the policy is violated, so an unreadable corpus exits 2."""
    assert _run("foo: [unterminated\n", monkeypatch) == 2
    assert "failed to parse YAML input" in capsys.readouterr().err


def test_a_malformed_policy_config_is_an_operator_error(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "policy.yaml"
    bad.write_text("- a\n- b\n")
    assert _run_flag("", monkeypatch, bad) == 2
    assert "--policy-config" in capsys.readouterr().err


def test_an_unreadable_policy_config_is_an_operator_error(tmp_path, monkeypatch, capsys):
    assert _run_flag("", monkeypatch, tmp_path / "gone.yaml") == 2
    assert "--policy-config" in capsys.readouterr().err


# --- chart-native HPA static assertion (--require-chart-native-vpas) -----------

def _chart_native_vpa(controlled: str = "memory") -> str:
    """A VPA for every declared chart-native workload (memory-only by default)."""
    out = []
    for t in TARGETS:
        ns, name = t["namespace"], t["name"]
        out.append(f"""
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata: {{name: {name}, namespace: {ns}}}
spec:
  targetRef: {{apiVersion: apps/v1, kind: Deployment, name: {name}}}
  updatePolicy: {{updateMode: Auto}}
  resourcePolicy:
    containerPolicies:
      - containerName: "*"
        controlledResources: [{controlled}]
""")
    return "\n---\n".join(out)


# A corpus with nothing the gate scores; the gate refuses a literally empty one.
UNRELATED = """
apiVersion: v1
kind: ConfigMap
metadata: {name: unrelated, namespace: ns}
"""


def _run_flag(stdin_text: str, monkeypatch, policy_file) -> int:
    return _run(
        stdin_text, monkeypatch,
        ["--require-chart-native-vpas", "--policy-config", str(policy_file)],
    )


def test_chart_native_all_memory_only_passes(monkeypatch, policy_file):
    """All chart-native workloads have a memory-only VPA -> OK."""
    assert _run_flag(_chart_native_vpa("memory"), monkeypatch, policy_file) == 0


def test_chart_native_cpu_vpa_fails(monkeypatch, policy_file):
    """A chart-native workload whose VPA also controls cpu conflicts with its HPA."""
    assert _run_flag(_chart_native_vpa("cpu, memory"), monkeypatch, policy_file) == 1


def test_chart_native_missing_vpa_fails(monkeypatch, policy_file):
    """A chart-native workload with no VPA in the corpus is flagged when required."""
    assert _run_flag(UNRELATED, monkeypatch, policy_file) == 1


def test_chart_native_off_mode_vpa_does_not_satisfy(monkeypatch, policy_file):
    """An Off (recommend-only) VPA never right-sizes, so it must NOT satisfy the
    chart-native requirement — the gate should still fail."""
    off = []
    for t in TARGETS:
        ns, name = t["namespace"], t["name"]
        off.append(f"""
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata: {{name: {name}, namespace: {ns}}}
spec:
  targetRef: {{apiVersion: apps/v1, kind: Deployment, name: {name}}}
  updatePolicy: {{updateMode: "Off"}}
  resourcePolicy:
    containerPolicies:
      - containerName: "*"
        controlledResources: [memory]
""")
    assert _run_flag("\n---\n".join(off), monkeypatch, policy_file) == 1


def test_chart_native_per_container_off_vpa_does_not_satisfy(monkeypatch, policy_file):
    """A mutating (Auto) VPA whose every containerPolicy is mode:Off right-sizes
    nothing (empty controlled set) and must NOT satisfy the chart-native gate."""
    off = []
    for t in TARGETS:
        ns, name = t["namespace"], t["name"]
        off.append(f"""
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata: {{name: {name}, namespace: {ns}}}
spec:
  targetRef: {{apiVersion: apps/v1, kind: Deployment, name: {name}}}
  updatePolicy: {{updateMode: Auto}}
  resourcePolicy:
    containerPolicies:
      - containerName: "*"
        mode: "Off"
        controlledResources: [memory]
""")
    assert _run_flag("\n---\n".join(off), monkeypatch, policy_file) == 1


def test_chart_native_check_is_opt_in(monkeypatch):
    """Without the flag, missing chart-native VPAs do not fail (generic-join only)."""
    assert _run(UNRELATED, monkeypatch) == 0


# --- "no CPU limits" policy (--require-chart-native-vpas) ----------------------


def _docs(text: str) -> list:
    return [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]


DEPLOY_WITH_CPU_LIMIT = """
apiVersion: apps/v1
kind: Deployment
metadata: {name: app, namespace: ns}
spec:
  template:
    spec:
      containers:
        - name: app
          resources:
            requests: {cpu: 50m, memory: 64Mi}
            limits: {cpu: 500m, memory: 128Mi}
"""

DEPLOY_NO_CPU_LIMIT = DEPLOY_WITH_CPU_LIMIT.replace("cpu: 500m, ", "")

HR_WITH_CPU_LIMIT = """
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata: {name: thing, namespace: ns}
spec:
  values:
    controller:
      resources:
        requests: {cpu: 10m}
        limits: {cpu: 200m, memory: 64Mi}
"""

HR_NO_CPU_LIMIT = HR_WITH_CPU_LIMIT.replace("cpu: 200m, ", "")

# `cpu: null` clears a chart default rather than setting a limit — not a violation.
DEPLOY_NULL_CPU_LIMIT = DEPLOY_WITH_CPU_LIMIT.replace("cpu: 500m, ", "cpu: null, ")
HR_NULL_CPU_LIMIT = HR_WITH_CPU_LIMIT.replace("cpu: 200m, ", "cpu: null, ")

CRONJOB_WITH_CPU_LIMIT = """
apiVersion: batch/v1
kind: CronJob
metadata: {name: job, namespace: ns}
spec:
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: c
              resources:
                limits: {cpu: 250m, memory: 64Mi}
"""


def test_cpu_limit_pod_spec_flagged():
    assert mod.cpu_limit_violations(_docs(DEPLOY_WITH_CPU_LIMIT))


def test_cpu_limit_pod_spec_memory_only_ok():
    assert mod.cpu_limit_violations(_docs(DEPLOY_NO_CPU_LIMIT)) == []


def test_cpu_limit_helmrelease_flagged():
    assert mod.cpu_limit_violations(_docs(HR_WITH_CPU_LIMIT))


def test_cpu_limit_helmrelease_memory_only_ok():
    assert mod.cpu_limit_violations(_docs(HR_NO_CPU_LIMIT)) == []


def test_cpu_limit_pod_spec_null_ok():
    """limits.cpu: null clears the default — not an effective CPU limit."""
    assert mod.cpu_limit_violations(_docs(DEPLOY_NULL_CPU_LIMIT)) == []


def test_cpu_limit_helmrelease_null_ok():
    """A HelmRelease clearing limits.cpu with null must not be flagged."""
    assert mod.cpu_limit_violations(_docs(HR_NULL_CPU_LIMIT)) == []


def test_cpu_limit_cronjob_flagged():
    assert mod.cpu_limit_violations(_docs(CRONJOB_WITH_CPU_LIMIT))


def test_cpu_limit_allowlist_exempts():
    assert mod.cpu_limit_violations(_docs(DEPLOY_WITH_CPU_LIMIT), {"ns/Deployment/app"}) == []


def test_cpu_limit_integrated_fails_with_flag(monkeypatch, policy_file):
    """Full-corpus mode (flag set) fails when a workload sets a CPU limit."""
    stream = _chart_native_vpa("memory") + "\n---\n" + DEPLOY_WITH_CPU_LIMIT
    assert _run_flag(stream, monkeypatch, policy_file) == 1


def test_cpu_limit_integrated_passes_with_flag(monkeypatch, policy_file):
    """Full-corpus mode passes when CPU limits are absent (memory-only limits)."""
    stream = _chart_native_vpa("memory") + "\n---\n" + DEPLOY_NO_CPU_LIMIT
    assert _run_flag(stream, monkeypatch, policy_file) == 0


def test_cpu_limit_not_checked_without_flag(monkeypatch):
    """The generic join (no flag) does not enforce the CPU-limit policy."""
    assert _run(DEPLOY_WITH_CPU_LIMIT, monkeypatch) == 0


# --- VPA memory-cap rule (--require-chart-native-vpas) ------------------------

def _capped_vpa(
    cap: str,
    limit: str = "1Gi",
    controlled_values: str | None = None,
    update_mode: str = "Auto",
    container_mode: str | None = None,
    container_name: str = "*",
) -> str:
    """A Deployment with a memory limit plus a VPA capping it."""
    policy_lines = [f'      - containerName: "{container_name}"']
    if controlled_values:
        policy_lines.append(f"        controlledValues: {controlled_values}")
    if container_mode:
        policy_lines.append(f'        mode: "{container_mode}"')
    policy_lines.append(f"        maxAllowed: {{memory: {cap}}}")
    policy = "\n".join(policy_lines)
    return f"""
apiVersion: apps/v1
kind: Deployment
metadata: {{name: app, namespace: ns}}
spec:
  template:
    spec:
      containers:
        - name: app
          resources:
            requests: {{memory: 256Mi}}
            limits: {{memory: {limit}}}
---
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata: {{name: app, namespace: ns}}
spec:
  targetRef: {{apiVersion: apps/v1, kind: Deployment, name: app}}
  updatePolicy: {{updateMode: "{update_mode}"}}
  resourcePolicy:
    containerPolicies:
{policy}
"""


def test_cap_above_limit_flagged():
    assert mod.vpa_cap_violations(_docs(_capped_vpa("2Gi", limit="1Gi")))


def test_cap_above_limit_flagged_even_for_requests_only():
    """Above the limit is wrong in every shape — the kubelet would reject it."""
    assert mod.vpa_cap_violations(
        _docs(_capped_vpa("2Gi", limit="1Gi", controlled_values="RequestsOnly"))
    )


def test_cap_above_limit_is_not_flagged_when_the_vpa_is_off():
    """An Off VPA's recommendation is never applied, so the kubelet can never
    reject the cap: the finding would be unfixable and force an allowlist entry."""
    docs = _docs(_capped_vpa("2Gi", limit="1Gi", update_mode="Off"))
    assert mod.vpa_cap_violations(docs) == []
    assert mod.vpa_cap_unjudged(docs) == []


def test_cap_above_limit_is_not_flagged_when_the_container_policy_is_off():
    """The per-container half of the same exemption."""
    docs = _docs(_capped_vpa("2Gi", limit="1Gi", container_mode="Off"))
    assert mod.vpa_cap_violations(docs) == []


def test_cap_above_limit_is_flagged_again_once_the_vpa_is_on():
    """The positive case the exemption must not swallow."""
    assert mod.vpa_cap_violations(
        _docs(_capped_vpa("2Gi", limit="1Gi", update_mode="Auto"))
    )


def test_cap_equal_to_limit_flagged_when_policy_controls_limits():
    """controlledValues unset defaults to RequestsAndLimits: the updater moves
    the limit with the request, so a cap at the limit bounds nothing."""
    assert mod.vpa_cap_violations(_docs(_capped_vpa("1Gi", limit="1Gi")))


def test_cap_equal_to_limit_flagged_for_explicit_requests_and_limits():
    assert mod.vpa_cap_violations(
        _docs(_capped_vpa("1Gi", limit="1Gi", controlled_values="RequestsAndLimits"))
    )


def test_cap_equal_to_limit_ok_for_requests_only():
    """RequestsOnly hand-sets the limit and caps the request at it — correct."""
    assert mod.vpa_cap_violations(
        _docs(_capped_vpa("1Gi", limit="1Gi", controlled_values="RequestsOnly"))
    ) == []


def test_cap_equal_to_limit_ok_when_update_mode_off():
    """Off recommends and never applies, so the == shape records growth."""
    assert mod.vpa_cap_violations(_docs(_capped_vpa("1Gi", limit="1Gi", update_mode="Off"))) == []


def test_cap_equal_to_limit_ok_when_container_mode_off():
    assert mod.vpa_cap_violations(
        _docs(_capped_vpa("1Gi", limit="1Gi", container_mode="Off"))
    ) == []


def test_cap_below_limit_passes():
    """The 0.8x shape the limit-controlling tier is supposed to use."""
    assert mod.vpa_cap_violations(_docs(_capped_vpa("819Mi", limit="1Gi"))) == []


def test_cap_compared_across_units():
    """1024Mi == 1Gi: the comparison is on bytes, not on the spelling."""
    assert mod.vpa_cap_violations(_docs(_capped_vpa("1024Mi", limit="1Gi")))
    assert mod.vpa_cap_violations(_docs(_capped_vpa("1023Mi", limit="1Gi"))) == []


def test_cap_ignored_when_container_not_named_by_policy():
    """A policy naming another container says nothing about this one's cap."""
    assert mod.vpa_cap_violations(
        _docs(_capped_vpa("2Gi", limit="1Gi", container_name="sidecar"))
    ) == []


def test_cap_skipped_when_target_absent_from_corpus():
    """Chart-rendered workloads have no limit here — skipped, never guessed."""
    stream = _capped_vpa("2Gi", limit="1Gi").split("---", 1)[1]
    assert mod.vpa_cap_violations(_docs(stream)) == []


def test_cap_skipped_when_container_has_no_memory_limit():
    stream = _capped_vpa("2Gi", limit="1Gi").replace("limits: {memory: 1Gi}", "limits: {cpu: null}")
    assert mod.vpa_cap_violations(_docs(stream)) == []


def test_cap_allowlist_exempts():
    assert mod.vpa_cap_violations(
        _docs(_capped_vpa("2Gi", limit="1Gi")), {"ns/VerticalPodAutoscaler/app"}
    ) == []


def test_cap_violation_names_the_allowlist_key():
    """The message has to carry the exact key an operator would paste back."""
    (violation,) = mod.vpa_cap_violations(_docs(_capped_vpa("2Gi", limit="1Gi")))
    assert "ns/VerticalPodAutoscaler/app" in violation


def test_unparseable_cap_is_not_a_violation():
    """A quantity this cannot read must not be invented into a failure."""
    assert mod.vpa_cap_violations(_docs(_capped_vpa("notaquantity", limit="1Gi"))) == []


def test_cap_integrated_fails_with_flag(monkeypatch, policy_file):
    stream = _chart_native_vpa("memory") + "\n---\n" + _capped_vpa("2Gi", limit="1Gi")
    assert _run_flag(stream, monkeypatch, policy_file) == 1


def test_cap_integrated_passes_with_flag(monkeypatch, policy_file):
    stream = _chart_native_vpa("memory") + "\n---\n" + _capped_vpa("819Mi", limit="1Gi")
    assert _run_flag(stream, monkeypatch, policy_file) == 0


def test_cap_integrated_allowlisted_passes(monkeypatch, tmp_path):
    """A consumer with documented, not-yet-re-derived caps stays green."""
    p = tmp_path / "policy.yaml"
    p.write_text(
        yaml.safe_dump(
            {"vpa_cap_allowlist": {"ns/VerticalPodAutoscaler/app": "cap re-derivation pending"}}
        )
    )
    assert _run_flag(_capped_vpa("2Gi", limit="1Gi"), monkeypatch, p) == 0


def test_cap_not_checked_without_flag(monkeypatch):
    """Same opt-in as the CPU-limit policy: only meaningful on the full corpus."""
    assert _run(_capped_vpa("2Gi", limit="1Gi"), monkeypatch) == 0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1Gi", 2**30),
        ("1024Mi", 2**30),
        ("512Ki", 512 * 2**10),
        ("1M", 1e6),
        ("1e3", 1000.0),
        ("1E3", 1000.0),
        ("2", 2.0),
        (2048, 2048.0),
        ("", None),
        (None, None),
        ("garbage", None),
        ("12Xi", None),
    ],
)
def test_parse_quantity(text, expected):
    assert mod.parse_quantity(text) == expected


# --- --policy-config loading --------------------------------------------------

def test_policy_config_loads_both_keys(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "chart_native_hpa_targets": [
                    {"namespace": "ns", "kind": "Deployment", "name": "app", "source": "chart"}
                ],
                "cpu_limit_allowlist": {"ns/Deployment/app": "third-party chart default"},
            }
        )
    )
    policy = mod.load_policy(p)
    assert policy.chart_native_hpa_targets[("ns", "Deployment", "app")] == "chart"
    assert policy.cpu_limit_allowlist == {"ns/Deployment/app"}


def test_policy_config_incomplete_target_raises(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("chart_native_hpa_targets:\n  - {namespace: ns, kind: Deployment}\n")
    with pytest.raises(ValueError, match="missing"):
        mod.load_policy(p)


def test_policy_config_non_mapping_raises(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("- a\n- b\n")
    with pytest.raises(ValueError, match="mapping"):
        mod.load_policy(p)


def test_empty_policy_config_is_fine(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("")
    policy = mod.load_policy(p)
    assert policy.chart_native_hpa_targets == {}
    assert policy.cpu_limit_allowlist == set()


def test_load_policy_does_not_accumulate_across_calls(tmp_path, policy_file):
    """Two loads must not merge: validate-helm-values.py imports this module and
    loads the same file."""
    other = tmp_path / "other.yaml"
    other.write_text(
        yaml.safe_dump(
            {
                "chart_native_hpa_targets": [
                    {"namespace": "other", "kind": "Deployment", "name": "b", "source": "chart"}
                ],
                "cpu_limit_allowlist": {"other/Deployment/b": "third-party chart default"},
            }
        )
    )
    first = mod.load_policy(policy_file)
    second = mod.load_policy(other)
    assert set(second.chart_native_hpa_targets) == {("other", "Deployment", "b")}
    assert second.cpu_limit_allowlist == {"other/Deployment/b"}
    # The first result is untouched by the second load.
    assert len(first.chart_native_hpa_targets) == len(TARGETS)
    assert first.cpu_limit_allowlist == set()
    assert not hasattr(mod, "CHART_NATIVE_HPA_TARGETS")
    assert not hasattr(mod, "CPU_LIMIT_ALLOWLIST")


_PRECEDENCE_DOCS = """
apiVersion: apps/v1
kind: Deployment
metadata: {name: app, namespace: ns}
spec:
  template:
    spec:
      containers:
        - name: app
          resources:
            requests: {memory: 256Mi}
            limits: {memory: 1Gi}
---
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata: {name: app, namespace: ns}
spec:
  targetRef: {apiVersion: apps/v1, kind: Deployment, name: app}
  updatePolicy: {updateMode: "Auto"}
  resourcePolicy:
    containerPolicies:
      - containerName: "app"
        controlledValues: RequestsOnly
        maxAllowed: {memory: 1Gi}
      - containerName: "*"
        maxAllowed: {memory: 1Gi}
"""


def test_exact_container_policy_overrides_the_wildcard():
    """An exact-named RequestsOnly policy takes VPA precedence over "*" for that
    container, so the limit-controlling wildcard must not be judged against it."""
    assert mod.vpa_cap_violations(_docs(_PRECEDENCE_DOCS)) == []


def test_wildcard_still_judged_for_uncovered_containers():
    docs = _PRECEDENCE_DOCS.replace('containerName: "app"', 'containerName: "other"')
    assert mod.vpa_cap_violations(_docs(docs))


# --- memory requests == limits under a limit-rewriting VPA -------------------


def _ratio_workload(
    request: str = "512Mi",
    limit: str = "512Mi",
    controlled_values: str | None = None,
    controlled_resources: str | None = None,
    update_mode: str = "Auto",
    container_mode: str | None = None,
    with_vpa: bool = True,
) -> str:
    deployment = f"""
apiVersion: apps/v1
kind: Deployment
metadata: {{name: app, namespace: ns}}
spec:
  template:
    spec:
      containers:
        - name: app
          resources:
            requests: {{memory: {request}}}
            limits: {{memory: {limit}}}
"""
    if not with_vpa:
        return deployment
    lines = ['      - containerName: "*"']
    if controlled_values:
        lines.append(f"        controlledValues: {controlled_values}")
    if controlled_resources:
        lines.append(f"        controlledResources: [{controlled_resources}]")
    if container_mode:
        lines.append(f'        mode: "{container_mode}"')
    policy = "\n".join(lines)
    return deployment + f"""---
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata: {{name: app, namespace: ns}}
spec:
  targetRef: {{apiVersion: apps/v1, kind: Deployment, name: app}}
  updatePolicy: {{updateMode: "{update_mode}"}}
  resourcePolicy:
    containerPolicies:
{policy}
"""


def test_equal_memory_under_a_limit_rewriting_vpa_is_flagged():
    found = mod.memory_ratio_violations(_docs(_ratio_workload()))
    assert len(found) == 1
    assert "ns/Deployment/app" in found[0] and "'app'" in found[0]


def test_equal_memory_compared_across_units():
    """1Gi == 1024Mi: compared on bytes, not on the spelling."""
    assert mod.memory_ratio_violations(_docs(_ratio_workload("1Gi", "1024Mi")))


def test_equal_memory_under_requests_only_is_fine():
    """RequestsOnly leaves the limit hand-set, so the ratio never moves."""
    assert mod.memory_ratio_violations(
        _docs(_ratio_workload(controlled_values="RequestsOnly"))
    ) == []


def test_equal_memory_under_a_cpu_only_vpa_is_fine():
    assert mod.memory_ratio_violations(
        _docs(_ratio_workload(controlled_resources="cpu"))
    ) == []


def test_equal_memory_under_an_off_vpa_is_fine():
    assert mod.memory_ratio_violations(_docs(_ratio_workload(update_mode="Off"))) == []
    assert mod.memory_ratio_violations(_docs(_ratio_workload(container_mode="Off"))) == []


def test_equal_memory_without_a_vpa_is_fine():
    assert mod.memory_ratio_violations(_docs(_ratio_workload(with_vpa=False))) == []


def test_unequal_memory_is_fine():
    assert mod.memory_ratio_violations(_docs(_ratio_workload("256Mi", "512Mi"))) == []


def test_equal_memory_is_allowlistable():
    assert mod.memory_ratio_violations(
        _docs(_ratio_workload()), {"ns/Deployment/app"}
    ) == []


def test_memory_ratio_integrated_fails_with_flag(monkeypatch, policy_file, capsys):
    assert _run_flag(_chart_native_vpa("memory") + "\n---\n" + _ratio_workload(),
                     monkeypatch, policy_file) == 1
    assert "requests.memory == limits.memory" in capsys.readouterr().err


def test_memory_ratio_not_checked_without_flag(monkeypatch):
    """Same opt-in as the CPU-limit and cap policies."""
    assert _run(_ratio_workload(), monkeypatch) == 0


# --- allowlist entries carry a rationale, structurally -----------------------


def _policy(tmp_path, body: str) -> Path:
    p = tmp_path / "policy.yaml"
    p.write_text(body)
    return p


@pytest.mark.parametrize(
    "key", ["cpu_limit_allowlist", "vpa_cap_allowlist", "memory_ratio_allowlist"]
)
def test_a_bare_string_allowlist_entry_is_rejected(tmp_path, key):
    """An unexplained exemption is a hole, and a YAML comment survives no
    refactor of the file."""
    with pytest.raises(ValueError, match="must be a mapping"):
        mod.load_policy(_policy(tmp_path, f"{key}: [ns/Deployment/app]\n"))


def test_a_blank_reason_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="has no reason"):
        mod.load_policy(_policy(tmp_path, 'cpu_limit_allowlist:\n  ns/Deployment/app: "  "\n'))


def test_a_mapping_allowlist_loads(tmp_path):
    policy = mod.load_policy(
        _policy(tmp_path, "cpu_limit_allowlist:\n  ns/Deployment/app: chart default\n")
    )
    assert policy.cpu_limit_allowlist == {"ns/Deployment/app"}


def test_a_list_allowlist_is_rejected(tmp_path):
    """One accepted shape only: two would drift apart as the file grows."""
    with pytest.raises(ValueError, match="must be a mapping"):
        mod.load_policy(
            _policy(
                tmp_path,
                "memory_ratio_allowlist:\n"
                "  - target: ns/Deployment/app\n    reason: pinned by the vendor\n",
            )
        )


@pytest.mark.parametrize(
    "key", ["cpu_limit_allowlist", "vpa_cap_allowlist", "memory_ratio_allowlist"]
)
@pytest.mark.parametrize("target", ["ns/app", "ns/Deployment/app/extra", "ns//app"])
def test_a_misshaped_allowlist_key_is_rejected(tmp_path, key, target):
    """A key becomes a set member verbatim, so one missing its Kind segment
    exempts nothing and the exemption silently does not apply."""
    with pytest.raises(ValueError, match="is not of the form"):
        mod.load_policy(_policy(tmp_path, f"{key}:\n  {target}: chart default\n"))


def test_the_vpa_cap_allowlist_refuses_a_workload_key(tmp_path):
    """The cap rule keys on the VPA, not on its target: a Deployment key here
    never matches the object the gate reports."""
    with pytest.raises(ValueError, match="exempts VerticalPodAutoscaler"):
        mod.load_policy(
            _policy(tmp_path, "vpa_cap_allowlist:\n  ns/Deployment/app: pending\n")
        )


def test_the_vpa_cap_allowlist_accepts_its_own_kind(tmp_path):
    policy = mod.load_policy(
        _policy(tmp_path, "vpa_cap_allowlist:\n  ns/VerticalPodAutoscaler/app: pending\n")
    )
    assert policy.vpa_cap_allowlist == {"ns/VerticalPodAutoscaler/app"}


def test_a_namespaceless_workload_is_keyed_default(monkeypatch, tmp_path):
    """The API defaults an omitted namespace to `default`, so the allowlist key
    a consumer would write is the one that matches."""
    namespaceless = DEPLOY_WITH_CPU_LIMIT.replace(", namespace: ns", "")
    assert mod.cpu_limit_violations(_docs(namespaceless))[0].startswith(
        "  default/Deployment/app"
    )
    assert mod.cpu_limit_violations(_docs(namespaceless), {"default/Deployment/app"}) == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))


def test_an_empty_corpus_is_an_operator_error(monkeypatch, capsys):
    """A gate that passes on nothing is not a gate."""
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert _run("", monkeypatch) == 2
    assert "empty corpus" in capsys.readouterr().err


# --- caps whose target the corpus does not render ----------------------------

UNRENDERED_CAP = """
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata: {name: alloy, namespace: observability}
spec:
  targetRef: {apiVersion: apps/v1, kind: DaemonSet, name: alloy}
  resourcePolicy:
    containerPolicies:
      - containerName: "*"
        maxAllowed: {memory: 1Gi}
"""


def test_an_unrendered_target_is_reported_as_not_judged():
    """The silent skip is how a cap at double the real limit passed review."""
    (unjudged,) = mod.vpa_cap_unjudged(_docs(UNRENDERED_CAP))
    assert "observability/VerticalPodAutoscaler/alloy" in unjudged
    assert "observability/DaemonSet/alloy" in unjudged
    assert mod.vpa_cap_violations(_docs(UNRENDERED_CAP)) == []


def test_a_capless_vpa_on_an_unrendered_target_is_not_reported():
    """Nothing to judge: the arm only cares about a cap it could not compare."""
    docs = _docs(UNRENDERED_CAP.replace("        maxAllowed: {memory: 1Gi}\n", ""))
    assert mod.vpa_cap_unjudged(docs) == []


def test_an_allowlisted_vpa_is_not_reported_as_not_judged():
    allowed = {"observability/VerticalPodAutoscaler/alloy"}
    assert mod.vpa_cap_unjudged(_docs(UNRENDERED_CAP), allowed) == []


def test_a_declared_limit_makes_the_cap_judgeable_and_catches_it():
    declared = {"observability/DaemonSet/alloy": {"alloy": "512Mi"}}
    docs = _docs(UNRENDERED_CAP)
    assert mod.vpa_cap_unjudged(docs, None, declared) == []
    (violation,) = mod.vpa_cap_violations(docs, None, declared)
    assert "1Gi is above the 512Mi limit" in violation


def test_a_declared_limit_above_the_cap_passes():
    declared = {"observability/DaemonSet/alloy": {"alloy": "2Gi"}}
    docs = _docs(UNRENDERED_CAP)
    assert mod.vpa_cap_violations(docs, None, declared) == []
    assert mod.vpa_cap_unjudged(docs, None, declared) == []


def test_a_rendered_limit_wins_over_a_declared_one():
    """The corpus is the truth; a stale declared limit must not hide a violation."""
    declared = {"ns/Deployment/app": {"app": "8Gi"}}
    assert mod.vpa_cap_violations(_docs(_capped_vpa("2Gi", limit="1Gi")), None, declared)


def test_the_gate_fails_on_an_unjudged_cap_unless_acknowledged(monkeypatch):
    argv = ["--require-chart-native-vpas"]
    assert _run(UNRENDERED_CAP, monkeypatch, argv) == 1
    assert _run(UNRENDERED_CAP, monkeypatch, argv + ["--allow-unjudged-vpa-caps"]) == 0


def test_a_declared_limits_entry_with_no_container_map_is_rejected(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump({"vpa_cap_declared_limits": {"ns/DaemonSet/x": "512Mi"}}))
    with pytest.raises(ValueError, match="non-empty mapping"):
        mod.load_policy(path)


def test_a_declared_limit_that_is_not_a_quantity_is_rejected(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text(
        yaml.safe_dump({"vpa_cap_declared_limits": {"ns/DaemonSet/x": {"c": "lots"}}})
    )
    with pytest.raises(ValueError, match="not a memory quantity"):
        mod.load_policy(path)


def test_declared_limits_round_trip_through_the_policy_file(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text(
        yaml.safe_dump({"vpa_cap_declared_limits": {"ns/DaemonSet/x": {"c": "512Mi"}}})
    )
    assert mod.load_policy(path).vpa_cap_declared_limits == {"ns/DaemonSet/x": {"c": "512Mi"}}


FLOOR_HPA = """
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata: {name: web, namespace: apps}
spec:
  minReplicas: 1
  maxReplicas: 4
  scaleTargetRef: {apiVersion: apps/v1, kind: Deployment, name: web}
"""

FLOOR_WORKLOAD = """
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: web, namespace: apps}
spec:
  template:
    metadata:
      labels: {app: web}
    spec:
      containers: [{name: web, image: web}]
"""

BLOCKING_PDB = """
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata: {name: web, namespace: apps}
spec:
  minAvailable: 1
  selector:
    matchLabels: {app: web}
"""


class TestPdbFloor:
    """An HPA floor of one replica beside a budget allowing no disruption."""

    def test_the_undrainable_pairing_fails(self, monkeypatch, capsys):
        corpus = FLOOR_HPA + FLOOR_WORKLOAD + BLOCKING_PDB
        assert _run(corpus, monkeypatch) == 1
        err = capsys.readouterr().err
        assert "minReplicas 1 while PodDisruptionBudget apps/web" in err

    def test_a_floor_of_two_drains(self, monkeypatch):
        corpus = (
            FLOOR_HPA.replace("minReplicas: 1", "minReplicas: 2")
            + FLOOR_WORKLOAD
            + BLOCKING_PDB
        )
        assert _run(corpus, monkeypatch) == 0

    def test_maxunavailable_one_drains(self, monkeypatch):
        corpus = (
            FLOOR_HPA
            + FLOOR_WORKLOAD
            + BLOCKING_PDB.replace("minAvailable: 1", "maxUnavailable: 1")
        )
        assert _run(corpus, monkeypatch) == 0

    def test_maxunavailable_zero_blocks(self, monkeypatch):
        corpus = (
            FLOOR_HPA
            + FLOOR_WORKLOAD
            + BLOCKING_PDB.replace("minAvailable: 1", "maxUnavailable: 0")
        )
        assert _run(corpus, monkeypatch) == 1

    def test_a_percentage_budget_rounds_the_api_way(self, monkeypatch):
        """minAvailable 100% pins the only pod; maxUnavailable 100% frees it."""
        pinned = BLOCKING_PDB.replace("minAvailable: 1", 'minAvailable: "100%"')
        assert _run(FLOOR_HPA + FLOOR_WORKLOAD + pinned, monkeypatch) == 1
        freed = BLOCKING_PDB.replace("minAvailable: 1", 'maxUnavailable: "100%"')
        assert _run(FLOOR_HPA + FLOOR_WORKLOAD + freed, monkeypatch) == 0

    def test_an_absent_minreplicas_defaults_to_one(self, monkeypatch):
        corpus = (
            FLOOR_HPA.replace("  minReplicas: 1\n", "")
            + FLOOR_WORKLOAD
            + BLOCKING_PDB
        )
        assert _run(corpus, monkeypatch) == 1

    def test_a_budget_selecting_other_pods_is_not_the_pairing(self, monkeypatch):
        corpus = (
            FLOOR_HPA
            + FLOOR_WORKLOAD
            + BLOCKING_PDB.replace("{app: web}", "{app: other}")
        )
        assert _run(corpus, monkeypatch) == 0

    def test_a_chart_rendered_target_is_not_judged(self, monkeypatch):
        """Without the pod template the budget cannot be matched to the HPA."""
        assert _run(FLOOR_HPA + BLOCKING_PDB, monkeypatch) == 0


class TestRenderedTargets:
    """--require-rendered-targets fails an autoscaler pointing at nothing."""

    def test_a_dangling_scaletargetref_fails(self, monkeypatch, capsys):
        corpus = FLOOR_HPA.replace("name: web}", "name: wbe}") + FLOOR_WORKLOAD
        assert _run(corpus, monkeypatch, ["--require-rendered-targets"]) == 1
        assert "scaleTargetRef names Deployment/wbe" in capsys.readouterr().err

    def test_a_dangling_vpa_targetref_fails(self, monkeypatch, capsys):
        vpa = """
---
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata: {name: web, namespace: apps}
spec:
  targetRef: {apiVersion: apps/v1, kind: Deployment, name: gone}
"""
        assert _run(vpa + FLOOR_WORKLOAD, monkeypatch, ["--require-rendered-targets"]) == 1
        assert "targetRef names Deployment/gone" in capsys.readouterr().err

    def test_a_resolvable_ref_passes(self, monkeypatch):
        corpus = FLOOR_HPA.replace("minReplicas: 1", "minReplicas: 2") + FLOOR_WORKLOAD
        assert _run(corpus, monkeypatch, ["--require-rendered-targets"]) == 0

    def test_a_declared_chart_native_target_is_exempt(self, monkeypatch, policy_file):
        corpus = FLOOR_HPA.replace("minReplicas: 1", "minReplicas: 2")
        argv = ["--require-rendered-targets", "--policy-config", str(policy_file)]
        assert _run(corpus, monkeypatch, argv) == 0

    def test_the_arm_is_off_by_default(self, monkeypatch):
        corpus = FLOOR_HPA.replace("name: web}", "name: wbe}") + FLOOR_WORKLOAD
        assert _run(corpus, monkeypatch) == 0

    def test_a_crd_backed_target_is_never_judged(self, monkeypatch):
        """A VPA may scale a Prometheus CR, which is no workload document."""
        vpa = """
---
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata: {name: prom, namespace: apps}
spec:
  targetRef: {apiVersion: monitoring.coreos.com/v1, kind: Prometheus, name: prom}
"""
        assert _run(vpa + FLOOR_WORKLOAD, monkeypatch, ["--require-rendered-targets"]) == 0


SIZED_WORKLOAD = """
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: web, namespace: apps}
spec:
  template:
    metadata:
      labels: {app: web}
    spec:
      containers:
        - name: web
          image: web
          resources:
            requests: {memory: 256Mi, cpu: 100m}
            limits: {memory: 512Mi}
"""


class TestRequestAboveLimit:
    """A request above its own limit: rejected by the API server, not by a render."""

    def test_a_request_under_its_limit_passes(self, monkeypatch):
        assert _run(SIZED_WORKLOAD, monkeypatch) == 0

    def test_a_request_above_its_limit_fails(self, monkeypatch, capsys):
        corpus = SIZED_WORKLOAD.replace("memory: 512Mi}", "memory: 128Mi}")
        assert _run(corpus, monkeypatch) == 1
        err = capsys.readouterr().err
        assert "requests memory 256Mi above its own limit 128Mi" in err

    def test_the_units_are_compared_not_the_text(self, monkeypatch):
        """512Mi of request under a 1Gi limit is legal however it is spelled."""
        corpus = SIZED_WORKLOAD.replace("memory: 256Mi,", "memory: 512Mi,").replace(
            "memory: 512Mi}", "memory: 1Gi}"
        )
        assert _run(corpus, monkeypatch) == 0

    def test_a_cpu_request_above_its_limit_fails(self, monkeypatch, capsys):
        corpus = SIZED_WORKLOAD.replace("limits: {memory: 512Mi}", "limits: {cpu: 50m}")
        assert _run(corpus, monkeypatch) == 1
        assert "requests cpu 100m above its own limit 50m" in capsys.readouterr().err

    def test_a_resource_limited_but_not_requested_is_not_compared(self, monkeypatch):
        corpus = SIZED_WORKLOAD.replace("requests: {memory: 256Mi, cpu: 100m}", "requests: {}")
        assert _run(corpus, monkeypatch) == 0

    def test_an_unreadable_quantity_is_not_a_violation(self, monkeypatch):
        """An unparseable value proves nothing, so it must not invent a finding."""
        corpus = SIZED_WORKLOAD.replace("memory: 512Mi}", "memory: lots}")
        assert _run(corpus, monkeypatch) == 0

    def test_an_init_container_is_judged_too(self, monkeypatch, capsys):
        corpus = SIZED_WORKLOAD.replace(
            "      containers:", "      initContainers:"
        ).replace("memory: 512Mi}", "memory: 128Mi}")
        assert _run(corpus, monkeypatch) == 1
        assert "container 'web'" in capsys.readouterr().err
