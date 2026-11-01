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


def test_memory_only_vpa_with_cpu_hpa_passes(monkeypatch):
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
    """A recommend-only (Off) VPA never mutates pods, so it pairs with any HPA."""
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

def _chart_native_vpa(
    controlled: str = "memory", update_mode: str = "Auto", container_mode: str | None = None
) -> str:
    """A VPA for every declared chart-native workload (memory-only by default)."""
    mode = f'\n        mode: "{container_mode}"' if container_mode else ""
    out = []
    for t in TARGETS:
        ns, name = t["namespace"], t["name"]
        out.append(f"""
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata: {{name: {name}, namespace: {ns}}}
spec:
  targetRef: {{apiVersion: apps/v1, kind: Deployment, name: {name}}}
  updatePolicy: {{updateMode: "{update_mode}"}}
  resourcePolicy:
    containerPolicies:
      - containerName: "*"{mode}
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


@pytest.mark.parametrize(
    "stream",
    [
        # A VPA that also controls cpu conflicts with the chart's HPA.
        _chart_native_vpa("cpu, memory"),
        UNRELATED,
        # An Off (recommend-only) VPA, or one whose every containerPolicy is Off,
        # right-sizes nothing.
        _chart_native_vpa(update_mode="Off"),
        _chart_native_vpa(container_mode="Off"),
    ],
    ids=["cpu-vpa", "missing-vpa", "off-vpa", "per-container-off-vpa"],
)
def test_chart_native_requirement_unmet_fails(monkeypatch, policy_file, stream):
    assert _run_flag(stream, monkeypatch, policy_file) == 1


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


@pytest.mark.parametrize(
    "stream, allowlist, flagged",
    [
        (DEPLOY_WITH_CPU_LIMIT, None, True),
        (DEPLOY_NO_CPU_LIMIT, None, False),
        (HR_WITH_CPU_LIMIT, None, True),
        (HR_NO_CPU_LIMIT, None, False),
        # `cpu: null` clears a chart default rather than setting a limit.
        (DEPLOY_WITH_CPU_LIMIT.replace("cpu: 500m, ", "cpu: null, "), None, False),
        (HR_WITH_CPU_LIMIT.replace("cpu: 200m, ", "cpu: null, "), None, False),
        (CRONJOB_WITH_CPU_LIMIT, None, True),
        (DEPLOY_WITH_CPU_LIMIT, {"ns/Deployment/app"}, False),
    ],
    ids=["pod-spec", "pod-spec-memory-only", "helmrelease", "helmrelease-memory-only",
         "pod-spec-null", "helmrelease-null", "cronjob", "allowlisted"],
)
def test_cpu_limit_violations(stream, allowlist, flagged):
    assert bool(mod.cpu_limit_violations(_docs(stream), allowlist)) is flagged


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

def _workload(
    request: str,
    limit: str | None,
    cap: str | None = None,
    *,
    resource: str = "memory",
    controlled_values: str | None = None,
    controlled_resources: str | None = None,
    update_mode: str = "Auto",
    container_mode: str | None = None,
    container_name: str = "*",
    with_vpa: bool = True,
) -> str:
    """A one-container Deployment plus a VPA over it.

    A `limit` of None leaves requests and no limits; a `cap` of None leaves the
    policy without maxAllowed.
    """
    limits = "" if limit is None else f"\n            limits: {{{resource}: {limit}}}"
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
            requests: {{{resource}: {request}}}{limits}
"""
    if not with_vpa:
        return deployment
    lines = [f'      - containerName: "{container_name}"']
    if controlled_values:
        lines.append(f"        controlledValues: {controlled_values}")
    if controlled_resources:
        lines.append(f"        controlledResources: [{controlled_resources}]")
    if container_mode:
        lines.append(f'        mode: "{container_mode}"')
    if cap is not None:
        lines.append(f"        maxAllowed: {{{resource}: {cap}}}")
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


@pytest.mark.parametrize(
    "cap, kwargs",
    [
        # Above the limit is wrong in every shape: the kubelet would reject it.
        ("2Gi", {}),
        ("2Gi", {"controlled_values": "RequestsOnly"}),
        # controlledValues unset defaults to RequestsAndLimits: the updater moves
        # the limit with the request, so a cap at the limit bounds nothing.
        ("1Gi", {}),
        ("1Gi", {"controlled_values": "RequestsAndLimits"}),
        # Compared on bytes, not on the spelling.
        ("1024Mi", {}),
    ],
    ids=["above", "above-requests-only", "equal", "equal-requests-and-limits", "equal-in-mi"],
)
def test_a_cap_at_or_above_the_limit_is_flagged(cap, kwargs):
    assert mod.vpa_cap_violations(_docs(_workload("1Gi", "1Gi", cap, **kwargs)))


@pytest.mark.parametrize(
    "cap, kwargs, allowlist",
    [
        # An Off VPA's recommendation is never applied, so the kubelet can never
        # reject the cap: the finding would be unfixable.
        ("2Gi", {"update_mode": "Off"}, None),
        ("2Gi", {"container_mode": "Off"}, None),
        # RequestsOnly hand-sets the limit and caps the request at it.
        ("1Gi", {"controlled_values": "RequestsOnly"}, None),
        ("1Gi", {"update_mode": "Off"}, None),
        ("1Gi", {"container_mode": "Off"}, None),
        # The 0.8x shape the limit-controlling tier is supposed to use.
        ("819Mi", {}, None),
        ("1023Mi", {}, None),
        # A policy naming another container says nothing about this one's cap.
        ("2Gi", {"container_name": "sidecar"}, None),
        # A quantity this cannot read must not be invented into a failure.
        ("notaquantity", {}, None),
        ("2Gi", {}, {"ns/VerticalPodAutoscaler/app"}),
    ],
    ids=["above-vpa-off", "above-policy-off", "equal-requests-only", "equal-vpa-off",
         "equal-policy-off", "below", "below-in-mi", "other-container", "unparseable",
         "allowlisted"],
)
def test_a_cap_the_limit_bounds_passes(cap, kwargs, allowlist):
    assert mod.vpa_cap_violations(_docs(_workload("1Gi", "1Gi", cap, **kwargs)), allowlist) == []


def test_cap_skipped_when_target_absent_from_corpus():
    """Chart-rendered workloads have no limit here — skipped, never guessed."""
    stream = _workload("1Gi", "1Gi", "2Gi").split("---", 1)[1]
    assert mod.vpa_cap_violations(_docs(stream)) == []


def test_cap_skipped_when_container_has_no_memory_limit():
    stream = _workload("1Gi", "1Gi", "2Gi").replace("limits: {memory: 1Gi}", "limits: {cpu: null}")
    assert mod.vpa_cap_violations(_docs(stream)) == []


def test_cap_violation_names_the_allowlist_key():
    """The message has to carry the exact key an operator would paste back."""
    (violation,) = mod.vpa_cap_violations(_docs(_workload("1Gi", "1Gi", "2Gi")))
    assert "ns/VerticalPodAutoscaler/app" in violation


def test_cap_integrated_fails_with_flag(monkeypatch, policy_file):
    stream = _chart_native_vpa("memory") + "\n---\n" + _workload("1Gi", "1Gi", "2Gi")
    assert _run_flag(stream, monkeypatch, policy_file) == 1


def test_cap_integrated_passes_with_flag(monkeypatch, policy_file):
    stream = _chart_native_vpa("memory") + "\n---\n" + _workload("256Mi", "1Gi", "204Mi")
    assert _run_flag(stream, monkeypatch, policy_file) == 0


def test_cap_integrated_allowlisted_passes(monkeypatch, tmp_path):
    """A consumer with documented, not-yet-re-derived caps stays green."""
    p = tmp_path / "policy.yaml"
    p.write_text(
        yaml.safe_dump(
            {"vpa_cap_allowlist": {"ns/VerticalPodAutoscaler/app": "cap re-derivation pending"}}
        )
    )
    assert _run_flag(_workload("256Mi", "1Gi", "2Gi"), monkeypatch, p) == 0


def test_cap_not_checked_without_flag(monkeypatch):
    """Same opt-in as the CPU-limit policy: only meaningful on the full corpus."""
    assert _run(_workload("1Gi", "1Gi", "2Gi"), monkeypatch) == 0


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


@pytest.mark.parametrize(
    "body",
    ["chart_native_hpa_targets: [ns/Deployment/app]\n",
     "chart_native_hpa_targets: {ns: app}\n"],
)
def test_policy_config_misshaped_targets_raise(tmp_path, body):
    p = tmp_path / "policy.yaml"
    p.write_text(body)
    with pytest.raises(ValueError, match="chart"):
        mod.load_policy(p)


def test_a_misshaped_target_list_is_an_operator_error(tmp_path, monkeypatch, capsys):
    p = tmp_path / "policy.yaml"
    p.write_text("chart_native_hpa_targets: [ns/Deployment/app]\n")
    assert _run_flag(UNRELATED, monkeypatch, p) == 2
    assert "--policy-config" in capsys.readouterr().err


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


def test_a_named_off_policy_overrides_the_wildcard():
    """A named `mode: Off` policy shields its container from the wildcard's cap."""
    off = _PRECEDENCE_DOCS.replace("controlledValues: RequestsOnly", 'mode: "Off"')
    assert 'mode: "Off"' in off
    assert mod.vpa_cap_violations(_docs(off)) == []


def test_a_named_off_policy_overrides_the_wildcard_in_the_ratio_arm():
    docs = _docs(_workload("512Mi", "1Gi", "819Mi"))
    assert mod.vpa_cap_violations(docs)
    docs[1]["spec"]["resourcePolicy"]["containerPolicies"].insert(
        0, {"containerName": "app", "mode": "Off"}
    )
    assert mod.vpa_cap_violations(docs) == []


def test_wildcard_still_judged_for_uncovered_containers():
    docs = _PRECEDENCE_DOCS.replace('containerName: "app"', 'containerName: "other"')
    assert mod.vpa_cap_violations(_docs(docs))


# --- caps scaled through the request:limit ratio ------------------------------


class TestRatioScaledCap:
    """The admission controller keeps request:limit, so the limit reaches cap x limit / request."""

    @pytest.mark.parametrize(
        ("request_", "limit", "cap", "resource", "phrases"),
        [
            ("512Mi", "1Gi", "819Mi", "memory",
             ("to 1638Mi", "0.8 x limit x (request / limit) = 409Mi", "RequestsOnly")),
            ("100m", "500m", "200m", "cpu", ("maxAllowed.cpu 200m", "to 1000m", "= 80m")),
        ],
    )
    def test_a_cap_scaled_past_the_limit_is_flagged(self, request_, limit, cap, resource, phrases):
        (found,) = mod.vpa_cap_violations(_docs(_workload(request_, limit, cap, resource=resource)))
        assert "ns/VerticalPodAutoscaler/app" in found
        assert all(phrase in found for phrase in phrases)

    @pytest.mark.parametrize(
        ("args", "kwargs"),
        [
            (("512Mi", "1Gi", "409Mi"), {}),
            (("512Mi", "1Gi", "512Mi"), {}),
            (("100m", "500m", "80m"), {"resource": "cpu"}),
            (("500m", "500m", "1"), {"resource": "cpu"}),
            (("512Mi", "1Gi", "819Mi"), {"controlled_values": "RequestsOnly"}),
            (("512Mi", "1Gi", "819Mi"), {"controlled_resources": "cpu"}),
            (("512Mi", "1Gi", "819Mi"), {"update_mode": "Off"}),
            (("512Mi", "1Gi", "819Mi"), {"container_mode": "Off"}),
        ],
        ids=["general-form", "cap-at-request", "cpu-general-form", "cpu-one-to-one",
             "requests-only", "uncontrolled-resource", "vpa-off", "policy-off"],
    )
    def test_a_cap_the_ratio_keeps_within_the_limit_passes(self, args, kwargs):
        assert mod.vpa_cap_violations(_docs(_workload(*args, **kwargs))) == []

    @pytest.mark.parametrize("request_", ["512Mi", "1Gi"])
    def test_a_cap_above_the_limit_is_reported_once(self, request_):
        (found,) = mod.vpa_cap_violations(_docs(_workload(request_, "1Gi", "2Gi")))
        assert "is above the 1Gi limit" in found

    def test_a_cap_equal_to_the_limit_is_reported_once(self):
        (found,) = mod.vpa_cap_violations(_docs(_workload("512Mi", "1Gi", "1Gi")))
        assert "equals the limit" in found

    @pytest.mark.parametrize("resource", ["memory", "cpu"])
    def test_a_container_with_requests_and_no_limits_is_not_judged(self, resource):
        request_, cap = ("512Mi", "819Mi") if resource == "memory" else ("100m", "200m")
        docs = _docs(_workload(request_, None, cap, resource=resource))
        assert mod.vpa_cap_violations(docs) == []

    @pytest.mark.parametrize(
        ("zero", "limit", "cap", "resource"),
        [("0", "1Gi", "819Mi", "memory"), ("0m", "500m", "200m", "cpu")],
    )
    def test_a_zero_request_is_not_judged(self, zero, limit, cap, resource):
        """No ratio exists to scale through, so the arm skips rather than divide by zero."""
        assert mod.vpa_cap_violations(_docs(_workload(zero, limit, cap, resource=resource))) == []

    def test_an_allowlisted_vpa_is_exempt(self):
        docs = _docs(_workload("512Mi", "1Gi", "819Mi"))
        assert mod.vpa_cap_violations(docs, {"ns/VerticalPodAutoscaler/app"}) == []

    def test_the_gate_fails_and_names_the_general_form(self, monkeypatch, policy_file, capsys):
        stream = _workload("512Mi", "1Gi", "819Mi")
        assert _run_flag(_chart_native_vpa("memory") + "\n---\n" + stream,
                         monkeypatch, policy_file) == 1
        assert "cap at most 0.8 x limit x (request / limit)" in capsys.readouterr().err


# --- memory requests == limits under a limit-rewriting VPA -------------------


def test_equal_memory_under_a_limit_rewriting_vpa_is_flagged():
    found = mod.memory_ratio_violations(_docs(_workload("512Mi", "512Mi")))
    assert len(found) == 1
    assert "ns/Deployment/app" in found[0] and "'app'" in found[0]


@pytest.mark.parametrize(
    "request_, limit, kwargs, allowlist, flagged",
    [
        # Compared on bytes, not on the spelling.
        ("1Gi", "1024Mi", {}, None, True),
        # RequestsOnly leaves the limit hand-set, so the ratio never moves.
        ("512Mi", "512Mi", {"controlled_values": "RequestsOnly"}, None, False),
        ("512Mi", "512Mi", {"controlled_resources": "cpu"}, None, False),
        ("512Mi", "512Mi", {"update_mode": "Off"}, None, False),
        ("512Mi", "512Mi", {"container_mode": "Off"}, None, False),
        ("512Mi", "512Mi", {"with_vpa": False}, None, False),
        ("256Mi", "512Mi", {}, None, False),
        ("512Mi", "512Mi", {}, {"ns/Deployment/app"}, False),
    ],
    ids=["equal-in-mi", "requests-only", "cpu-only-vpa", "vpa-off", "policy-off",
         "no-vpa", "unequal", "allowlisted"],
)
def test_memory_ratio_violations(request_, limit, kwargs, allowlist, flagged):
    docs = _docs(_workload(request_, limit, **kwargs))
    assert bool(mod.memory_ratio_violations(docs, allowlist)) is flagged


def test_memory_ratio_integrated_fails_with_flag(monkeypatch, policy_file, capsys):
    assert _run_flag(_chart_native_vpa("memory") + "\n---\n" + _workload("512Mi", "512Mi"),
                     monkeypatch, policy_file) == 1
    assert "requests.memory == limits.memory" in capsys.readouterr().err


def test_memory_ratio_not_checked_without_flag(monkeypatch):
    """Same opt-in as the CPU-limit and cap policies."""
    assert _run(_workload("512Mi", "512Mi"), monkeypatch) == 0


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


def test_a_namespaceless_workload_is_keyed_default(monkeypatch, tmp_path):
    """The API defaults an omitted namespace to `default`, so the allowlist key
    a consumer would write is the one that matches."""
    namespaceless = DEPLOY_WITH_CPU_LIMIT.replace(", namespace: ns", "")
    assert mod.cpu_limit_violations(_docs(namespaceless))[0].startswith(
        "  default/Deployment/app"
    )
    assert mod.cpu_limit_violations(_docs(namespaceless), {"default/Deployment/app"}) == []


def test_an_empty_corpus_is_an_operator_error(monkeypatch, capsys):
    """A gate that passes on nothing is not a gate."""
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
    """A cap with no limit to compare against is named, never skipped in silence."""
    (unjudged,) = mod.vpa_cap_unjudged(_docs(UNRENDERED_CAP))
    assert "observability/VerticalPodAutoscaler/alloy" in unjudged
    assert "observability/DaemonSet/alloy" in unjudged
    assert mod.vpa_cap_violations(_docs(UNRENDERED_CAP)) == []


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("  resourcePolicy:", '  updatePolicy: {updateMode: "Off"}\n  resourcePolicy:'),
        ('containerName: "*"', 'containerName: "*"\n        mode: "Off"'),
    ],
    ids=["vpa-off", "policy-off"],
)
def test_an_off_cap_on_an_unrendered_target_is_not_reported_as_not_judged(old, new):
    """An Off VPA or policy never applies its cap, so there is nothing left to judge."""
    assert mod.vpa_cap_unjudged(_docs(UNRENDERED_CAP.replace(old, new))) == []


def test_a_live_policy_beside_an_off_one_is_still_reported_as_not_judged():
    mixed = UNRENDERED_CAP + """      - containerName: sidecar
        mode: "Off"
        maxAllowed: {memory: 2Gi}
"""
    assert len(mod.vpa_cap_unjudged(_docs(mixed))) == 1


def test_a_capless_vpa_on_an_unrendered_target_is_not_reported():
    """Nothing to judge: the arm only cares about a cap it could not compare."""
    docs = _docs(UNRENDERED_CAP.replace("        maxAllowed: {memory: 1Gi}\n", ""))
    assert mod.vpa_cap_unjudged(docs) == []


def test_an_allowlisted_vpa_is_not_reported_as_not_judged():
    allowed = {"observability/VerticalPodAutoscaler/alloy"}
    assert mod.vpa_cap_unjudged(_docs(UNRENDERED_CAP), allowed) == []


UNRENDERED_REQUESTS_ONLY = UNRENDERED_CAP.replace(
    'containerName: "*"', 'containerName: "*"\n        controlledValues: RequestsOnly'
)


def test_a_declared_limit_makes_the_cap_judgeable_and_catches_it():
    declared = {"observability/DaemonSet/alloy": {"alloy": "512Mi"}}
    docs = _docs(UNRENDERED_REQUESTS_ONLY)
    assert mod.vpa_cap_unjudged(docs, None, declared) == []
    (violation,) = mod.vpa_cap_violations(docs, None, declared)
    assert "1Gi is above the 512Mi limit" in violation


def test_a_declared_limit_above_the_cap_passes():
    declared = {"observability/DaemonSet/alloy": {"alloy": "2Gi"}}
    docs = _docs(UNRENDERED_REQUESTS_ONLY)
    assert mod.vpa_cap_violations(docs, None, declared) == []
    assert mod.vpa_cap_unjudged(docs, None, declared) == []


def test_a_declared_limit_leaves_a_limit_controlling_ratio_unjudged():
    """A declared limit carries no request, so the ratio rule still has nothing to read."""
    declared = {"observability/DaemonSet/alloy": {"alloy": "2Gi"}}
    docs = _docs(UNRENDERED_CAP)
    assert mod.vpa_cap_violations(docs, None, declared) == []
    (note,) = mod.vpa_cap_unjudged(docs, None, declared)
    assert "maxAllowed.memory controls limits" in note
    assert "ratio rule was not applied" in note


def test_an_unrendered_cpu_cap_that_controls_limits_is_reported_as_not_judged():
    docs = _docs(UNRENDERED_CAP.replace("maxAllowed: {memory: 1Gi}", "maxAllowed: {cpu: 500m}"))
    (note,) = mod.vpa_cap_unjudged(docs)
    assert "maxAllowed.cpu controls limits" in note
    assert "observability/DaemonSet/alloy" in note


@pytest.mark.parametrize(
    "extra",
    ["controlledValues: RequestsOnly", "controlledResources: [memory]", 'mode: "Off"'],
)
def test_an_unrendered_cpu_cap_that_cannot_move_a_limit_is_not_reported(extra):
    docs = _docs(
        UNRENDERED_CAP.replace("maxAllowed: {memory: 1Gi}", f"{extra}\n        maxAllowed: {{cpu: 500m}}")
    )
    assert mod.vpa_cap_unjudged(docs) == []


def test_a_rendered_limit_wins_over_a_declared_one():
    """The corpus is the truth; a stale declared limit must not hide a violation."""
    declared = {"ns/Deployment/app": {"app": "8Gi"}}
    assert mod.vpa_cap_violations(_docs(_workload("1Gi", "1Gi", "2Gi")), None, declared)


def test_the_gate_fails_on_an_unjudged_cap_unless_acknowledged(monkeypatch, capsys):
    argv = ["--require-chart-native-vpas"]
    assert _run(UNRENDERED_CAP, monkeypatch, argv) == 1
    assert "controlledValues: RequestsOnly" in capsys.readouterr().err
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

    def test_a_match_expressions_budget_is_matched(self, monkeypatch):
        budget = BLOCKING_PDB.replace(
            "matchLabels: {app: web}",
            "matchExpressions: [{key: app, operator: In, values: [web]}]",
        )
        assert _run(FLOOR_HPA + FLOOR_WORKLOAD + budget, monkeypatch) == 1

    def test_an_empty_selector_budget_covers_every_pod(self, monkeypatch):
        budget = BLOCKING_PDB.replace("  selector:\n    matchLabels: {app: web}", "  selector: {}")
        assert _run(FLOOR_HPA + FLOOR_WORKLOAD + budget, monkeypatch) == 1

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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
