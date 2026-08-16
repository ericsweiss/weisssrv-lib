"""Tests for scripts/check-hpa-vpa-invariant.py."""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "check_hpa_vpa_invariant",
    Path(__file__).resolve().parent.parent / "scripts" / "check-hpa-vpa-invariant.py",
)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


# The chart-native target list is consumer data (--policy-config), so the suite
# declares its own and feeds it to the gate.
TARGETS = [
    {"namespace": "traefik", "kind": "Deployment", "name": "traefik",
     "source": "traefik chart autoscaling.enabled"},
    {"namespace": "apps", "kind": "Deployment", "name": "web", "source": "chart autoscaling"},
]


@pytest.fixture()
def policy_file(tmp_path) -> Path:
    import yaml

    p = tmp_path / "policy.yaml"
    p.write_text(yaml.safe_dump({"chart_native_hpa_targets": TARGETS}))
    return p


def _run(stdin_text: str, monkeypatch, argv: list[str] | None = None) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    return mod.main(argv or [])


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


def test_malformed_yaml_exits_cleanly(monkeypatch):
    """Bad YAML exits with a message instead of an uncaught traceback."""
    with pytest.raises(SystemExit):
        _run("foo: [unterminated\n", monkeypatch)


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
    assert _run_flag("", monkeypatch, policy_file) == 1


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


def test_chart_native_check_is_opt_in(monkeypatch, policy_file):
    """Without the flag, missing chart-native VPAs do not fail (generic-join only)."""
    monkeypatch.setattr("sys.argv", ["check"])
    assert _run("", monkeypatch) == 0


# --- "no CPU limits" policy (--require-chart-native-vpas) ----------------------

import yaml as _yaml  # noqa: E402


def _docs(text: str) -> list:
    return [d for d in _yaml.safe_load_all(text) if isinstance(d, dict)]


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


def test_cap_above_limit_flagged_even_when_off():
    assert mod.vpa_cap_violations(_docs(_capped_vpa("2Gi", limit="1Gi", update_mode="Off")))


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
    import yaml

    p = tmp_path / "policy.yaml"
    p.write_text(
        yaml.safe_dump({"vpa_cap_allowlist": ["ns/VerticalPodAutoscaler/app"]})
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
    import yaml

    p = tmp_path / "policy.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "chart_native_hpa_targets": [
                    {"namespace": "ns", "kind": "Deployment", "name": "app", "source": "chart"}
                ],
                "cpu_limit_allowlist": ["ns/Deployment/app"],
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
    loads the same file, so accumulating globals leaked one caller into the other."""
    import yaml

    other = tmp_path / "other.yaml"
    other.write_text(
        yaml.safe_dump(
            {
                "chart_native_hpa_targets": [
                    {"namespace": "other", "kind": "Deployment", "name": "b", "source": "chart"}
                ],
                "cpu_limit_allowlist": ["other/Deployment/b"],
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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
