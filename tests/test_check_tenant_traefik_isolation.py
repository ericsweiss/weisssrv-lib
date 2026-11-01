"""Tests that check-tenant-traefik-isolation.py fails when a tenant lands without the full Deny-bound policies."""
from __future__ import annotations

import posixpath
from pathlib import Path

import pytest
import yaml
from script_loader import load_script

gate = load_script("check-tenant-traefik-isolation.py")
TENANTS_KUSTOMIZATION = "kubernetes/clusters/demo/tenants/kustomization.yaml"
DEMO = ["tenant-crd-editor.yaml", "demo-tenant.yaml"]
# The policy shape the gate pins whole; each cluster repo's file must match it,
# so a gate spec that drifts from it fails here.
PINNED_POLICY = (
    Path(__file__).resolve().parent / "fixtures" / "tenant-traefik" / "tenant-traefik-policy.yaml"
).read_text()


def routing_binding(ns: str, policy: str = "tenant-traefik-hostnames") -> dict:
    """A per-tenant binding of `policy` the platform writes into the wiring file."""
    return {
        "apiVersion": "admissionregistration.k8s.io/v1",
        "kind": "ValidatingAdmissionPolicyBinding",
        "metadata": {"name": f"{policy}-{ns}"},
        "spec": {
            "policyName": policy,
            "validationActions": ["Deny"],
            "paramRef": {
                "name": f"tenant-routing-{ns}",
                "namespace": "flux-system",
                "parameterNotFoundAction": "Deny",
            },
            "matchResources": {
                "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": ns}}
            },
        },
    }


def wiring(ns: str) -> list[dict]:
    """A tenant file's routing part: its Namespace, hostname grant and the
    hostname and certificate bindings that read it."""
    return [
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": ns, "labels": {"fluxcd.io/tenant": ns}},
        },
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": f"tenant-routing-{ns}", "namespace": "flux-system"},
            "data": {"hosts": "demo.example.com\n"},
        },
        routing_binding(ns),
        routing_binding(ns, "tenant-certificates"),
    ]


def write_tenant(root: Path, resource: str, docs: list[dict]) -> None:
    path = (root / TENANTS_KUSTOMIZATION).parent / resource
    path.write_text(yaml.safe_dump_all(docs, sort_keys=False))


def build(tmp_path: Path, resources: list[str]) -> Path:
    """The tenants kustomization and each listed tenant file wired for routing
    (namespace = file stem)."""
    kustomization = tmp_path / TENANTS_KUSTOMIZATION
    kustomization.parent.mkdir(parents=True, exist_ok=True)
    kustomization.write_text(
        "resources:\n" + "".join(f"  - {r}\n" for r in resources)
    )
    for resource in resources:
        if posixpath.normpath(resource) not in gate.SHARED:
            write_tenant(tmp_path, resource, wiring(Path(resource).stem))
    return tmp_path


def policy(action: str = "Deny") -> str:
    """The pinned policy file, its first binding's action set to `action`."""
    return PINNED_POLICY.replace("validationActions: [Deny]", f"validationActions: [{action}]", 1)


def write_policy(root: Path, body: str, listed: bool = True) -> None:
    """The policy file, plus the configs kustomization that applies it."""
    path = root / gate.TENANT_POLICY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    resources = ["traefik-middlewares.yaml"] + (["tenant-traefik-policy.yaml"] if listed else [])
    (path.parent / "kustomization.yaml").write_text(
        "resources:\n" + "".join(f"  - {r}\n" for r in resources)
    )


def checked(tmp_path: Path, body: str | None = None, resources: list[str] = DEMO) -> list[str]:
    """The gate's findings for `resources` under policy file `body`, or none written."""
    root = build(tmp_path, resources)
    if body is not None:
        write_policy(root, body)
    return gate.check(root)


def _mutated(name: str, mutate, kind: str = "ValidatingAdmissionPolicy") -> str:
    """The pinned policy file with the `kind` named `name` passed through `mutate`."""
    docs = [d for d in yaml.safe_load_all(PINNED_POLICY) if d]
    target = [d for d in docs if d["kind"] == kind and d["metadata"]["name"] == name]
    assert len(target) == 1
    mutate(target[0]["spec"])
    return yaml.safe_dump_all(docs, sort_keys=False)


def _ops(name: str) -> dict:
    """Each resource policy `name` matches, with the operations it matches on."""
    rules = gate.REQUIRED_POLICIES[name]["resourceRules"]
    return {r: tuple(rule["operations"]) for rule in rules for r in rule["resources"]}


@pytest.mark.parametrize("resources", [["tenant-crd-editor.yaml"], DEMO])
def test_the_pinned_policies_pass(tmp_path, resources):
    assert checked(tmp_path, policy(), resources) == []


def test_baseline_only_without_the_policies_fails(tmp_path):
    """A torn-down tenant's Namespace stays live with the tenant label, so the
    label-bound policies are required with no tenant wired as well."""
    problems = checked(tmp_path, resources=["tenant-crd-editor.yaml"])
    assert len(problems) == 1
    assert "wires no tenant" in problems[0]


# Plain-Ingress annotations, forwardAuth addresses and endpoint resolution do
# not depend on allowCrossNamespace, so the gate never reads that setting.
def test_a_tenant_without_the_policies_fails(tmp_path):
    problems = checked(tmp_path, resources=["tenant-crd-editor.yaml", "hermes-tenant.yaml"])
    assert len(problems) == 1
    assert "hermes-tenant.yaml" in problems[0]
    assert "even when providers.kubernetesCRD.allowCrossNamespace is false" in problems[0]


def test_an_empty_tenants_kustomization_is_reported(tmp_path):
    assert "lists no resources" in checked(tmp_path, resources=[])[0]


# An unparseable input means the gate could not inspect its subject: exit 2
# through OperatorError, never a traceback and never an isolation finding.
def test_an_unparseable_input_is_vacuous_not_a_finding(tmp_path):
    root = build(tmp_path, ["tenant-crd-editor.yaml"])
    (root / TENANTS_KUSTOMIZATION).write_text("a: [1,\n")
    with pytest.raises(gate.OperatorError):
        gate.check(root)
    assert gate.main(["--repo", str(root)]) == 2


def test_a_malformed_configs_kustomization_is_vacuous(tmp_path, capsys):
    root = build(tmp_path, DEMO)
    write_policy(root, PINNED_POLICY)
    (root / gate.CONFIGS_KUSTOMIZATION).write_text("resources: x\n")
    assert gate.main(["--repo-root", str(root)]) == 2
    assert gate.CONFIGS_KUSTOMIZATION in capsys.readouterr().err


def _without(kind: str, name: str):
    """The pinned file less the one `kind` document naming `name`."""
    docs = PINNED_POLICY.split("---\n")
    kept = [d for d in docs if not (f"kind: {kind}\n" in d and f"name: {name}\n" in d)]
    assert len(kept) == len(docs) - 1
    return "---\n".join(kept)


# Each removal leaves a tenant namespace unenforced: a dropped or unbound policy,
# a cross-namespace Ingress, an ExternalName or selectorless Service, a raw
# CertificateRequest, an nfs volume or a claim on another class.
REMOVALS = {
    "warn-binding": policy("Warn"),
    "selector-key": policy().replace("key: fluxcd.io/tenant\n", "key: fluxcd.io/not-tenant\n", 1),
    "selector-operator": policy().replace("operator: Exists", "operator: DoesNotExist", 1),
    "policy-undefined": policy().replace(
        "kind: ValidatingAdmissionPolicy\n", "kind: ConfigMap\n", 1
    ),
    "last-binding-dropped": policy().rsplit("---\n", 1)[0],
    **{
        f"{kind}-{name}": _without(kind, name)
        for kind in ("ValidatingAdmissionPolicy", "ValidatingAdmissionPolicyBinding")
        for name in (
            "tenant-traefik-ingress-refs",
            "tenant-no-externalname-services",
            "tenant-no-manual-endpoints",
            "tenant-no-tcp-udp-routes",
            "tenant-traefik-tls-defaults",
            "tenant-certificaterequests",
            "tenant-pod-volumes",
            "tenant-pvc-storageclasses",
        )
    },
    # The per-tenant bindings name these; without them they enforce nothing.
    **{
        f"grant-policy-{name}": _without("ValidatingAdmissionPolicy", name)
        for name in gate.GRANT_POLICIES
    },
}


@pytest.mark.parametrize("removal", sorted(REMOVALS))
def test_a_policy_file_missing_a_piece_keeps_the_gate_closed(tmp_path, removal):
    assert len(checked(tmp_path, REMOVALS[removal])) == 1


def test_the_gate_pins_every_shipped_policy():
    """The pinned set and the fixture name the same policies, and only the
    per-tenant grant policies lack a tenant-label binding: their paramRef is
    per tenant, so the bindings that read it live in the wiring files."""
    docs = [d for d in yaml.safe_load_all(PINNED_POLICY) if d]
    shipped = {d["metadata"]["name"] for d in docs if d["kind"] == "ValidatingAdmissionPolicy"}
    bound = {
        d["spec"]["policyName"] for d in docs if d["kind"] == "ValidatingAdmissionPolicyBinding"
    }
    assert set(gate.REQUIRED_POLICIES) == shipped
    assert gate.GRANT_POLICIES == ("tenant-traefik-hostnames", "tenant-certificates")
    assert bound == shipped - set(gate.GRANT_POLICIES)


# The Pod Security restricted volume set, each admitted by has() on its source.
RESTRICTED_VOLUME_SOURCES = (
    "has(v.configMap) || has(v.csi) || has(v.downwardAPI) || has(v.emptyDir) || "
    "has(v.ephemeral) || has(v.persistentVolumeClaim) || has(v.projected) || has(v.secret)"
)


def test_the_gate_pins_the_restricted_volume_set_on_pods_and_workloads():
    """Baseline Pod Security admits nfs, iscsi and the other node-mounted volume
    types, and the NAS exports trust node addresses, so the gate holds tenant
    pods and every pod template to the restricted set."""
    required = gate.REQUIRED_POLICIES["tenant-pod-volumes"]
    assert required["paramKind"] is None
    assert required["variables"] == [(
        "volumes",
        "request.resource.resource == 'pods' ? (has(object.spec) && "
        "has(object.spec.volumes) ? object.spec.volumes : []) : "
        "request.resource.resource == 'cronjobs' ? (has(object.spec) && "
        "has(object.spec.jobTemplate) && has(object.spec.jobTemplate.spec) && "
        "has(object.spec.jobTemplate.spec.template) && "
        "has(object.spec.jobTemplate.spec.template.spec) && "
        "has(object.spec.jobTemplate.spec.template.spec.volumes) ? "
        "object.spec.jobTemplate.spec.template.spec.volumes : []) : "
        "(has(object.spec) && has(object.spec.template) && "
        "has(object.spec.template.spec) && has(object.spec.template.spec.volumes) ? "
        "object.spec.template.spec.volumes : [])",
    )]
    assert required["validations"] == [
        f"variables.volumes.all(v, {RESTRICTED_VOLUME_SOURCES})"
    ]


def test_the_gate_matches_update_only_where_the_object_stays_mutable():
    """Specs frozen after creation match CREATE only, so finalizer-only updates on
    objects admitted before the policy still pass. A claim's volumeType annotation
    stays mutable until binding, so claims match UPDATE too."""
    both = ("CREATE", "UPDATE")
    assert _ops("tenant-certificaterequests") == {
        "certificaterequests": ("CREATE",), "challenges": ("CREATE",), "orders": both,
    }
    assert _ops("tenant-pod-volumes") == {
        "pods": ("CREATE",), "jobs": ("CREATE",),
        "deployments": both, "statefulsets": both, "daemonsets": both,
        "replicasets": both, "cronjobs": both,
    }
    assert _ops("tenant-pvc-storageclasses") == {"persistentvolumeclaims": both}


@pytest.mark.parametrize(
    ("name", "needle"),
    [
        ("tenant-no-externalname-services", "!k.startsWith('tailscale.com/')"),
        ("tenant-traefik-hostnames", "object.spec.ingressClassName == 'traefik'"),
        ("tenant-traefik-hostnames", "!k.startsWith('tailscale.com/')"),
        ("tenant-traefik-hostnames", "k != 'kubernetes.io/ingress.class'"),
        ("tenant-traefik-object-refs", "!has(object.spec.failover)"),
        ("tenant-traefik-object-refs",
         "object.spec.highestRandomWeight.services.all(s, (!has(s.namespace)"),
        ("tenant-traefik-object-refs", "object.spec.errors.service.middlewares.all(m,"),
        ("tenant-traefik-object-refs", "object.spec.mirroring.middlewares.all(m,"),
        ("tenant-traefik-object-refs",
         "object.spec.mirroring.mirrors.all(s, !has(s.middlewares)"),
        ("tenant-traefik-route-refs", "r.services.all(s, !has(s.middlewares)"),
        ("tenant-traefik-route-refs", "!has(object.spec.parentRefs)"),
    ],
    ids=["service-tailscale", "ingress-class", "ingress-tailscale", "ingress-legacy-class",
         "traefikservice-failover", "highest-random-weight", "errors-service-middlewares",
         "mirroring-middlewares", "mirror-middlewares", "route-service-middlewares",
         "route-parent-refs"],
)
def test_the_gate_pins_the_other_operator_and_failover_refusals(name, needle):
    """Another ingress operator (Tailscale's), a TraefikService failover or an
    unpinned service reference would route around the other pins, so the gate
    carries each refusal."""
    assert [v for v in gate.REQUIRED_POLICIES[name]["validations"] if needle in v]


def test_the_gate_pins_the_internal_ingress_and_route_allowlists():
    """Both VIPs serve the same entrypoints, so an internal host must carry an allowlist."""
    ingress = gate.REQUIRED_POLICIES["tenant-traefik-ingress-refs"]
    assert gate.INTERNAL_INGRESS_ALLOWLIST in ingress["validations"]
    assert {"internalDomain", "internalAllowlistRefs", "routerMiddlewares"} <= {
        name for name, _ in ingress["variables"]
    }
    route = gate.REQUIRED_POLICIES["tenant-traefik-route-refs"]
    assert ("internalHostEnds", gate.INTERNAL_HOST_ENDS) in route["variables"]
    assert "internalHostEnds.exists" in gate.INTERNAL_ROUTE_ALLOWLIST


# Every policy that matches UPDATE on a kind with a spec, bar tenant-certificaterequests
# (its Order rule admits the garbage collector); the CREATE-only policies never
# see a finalizer update.
FINALIZING_POLICIES = (
    "tenant-traefik-route-refs", "tenant-traefik-object-refs", "tenant-traefik-ingress-refs",
    "tenant-no-externalname-services", "tenant-no-tcp-udp-routes", "tenant-traefik-tls-defaults",
    "tenant-traefik-hostnames", "tenant-certificates", "tenant-pod-volumes",
    "tenant-pvc-storageclasses",
)
FINALIZING_EXPRESSION = (
    "request.operation != 'UPDATE' || !has(object.metadata.deletionTimestamp) || "
    "object.spec != oldObject.spec || (has(object.metadata.annotations) ? "
    "object.metadata.annotations : {}) != (has(oldObject.metadata.annotations) ? "
    "oldObject.metadata.annotations : {})"
)


def test_exactly_the_finalizing_policies_skip_a_finalizer_only_update():
    """A terminating legacy object sheds its finalizer by an UPDATE that changes
    neither spec nor annotations; exactly FINALIZING_POLICIES and the endpoints
    policy skip it, in the gate's pins and in the pinned file alike."""
    pinned = {
        name: required.get("matchConditions", [])
        for name, required in gate.REQUIRED_POLICIES.items()
    }
    in_file = {
        d["metadata"]["name"]: [
            (c["name"], gate._cel(c["expression"]))
            for c in d["spec"].get("matchConditions") or []
        ]
        for d in yaml.safe_load_all(PINNED_POLICY)
        if d and d["kind"] == "ValidatingAdmissionPolicy"
    }
    expected = {
        name: [("enforce-unless-finalizing-unchanged", FINALIZING_EXPRESSION)]
        if name in FINALIZING_POLICIES else []
        for name in gate.REQUIRED_POLICIES
    }
    # No spec to compare: the endpoints policy keys the same exemption on the
    # routing data, labels and annotations instead.
    expected["tenant-no-manual-endpoints"] = [
        ("enforce-unless-finalizing-unchanged", gate._cel(gate.FINALIZING_UNCHANGED_ENDPOINTS[0][1]))
    ]
    assert pinned == expected
    assert in_file == expected


def _ignore_annotations(spec):
    condition = spec["matchConditions"][0]
    condition["expression"] = condition["expression"].split(
        "|| (has(object.metadata.annotations)"
    )[0]
    assert "annotations" not in condition["expression"]


# Without the annotations clause a tenant could hold its own Service in
# Terminating and rewrite the annotations these policies validate.
FINALIZER_WEAKENINGS = {
    "dropped": lambda spec: spec.pop("matchConditions"),
    "false": lambda spec: spec["matchConditions"][0].__setitem__("expression", "false"),
    "annotations-ignored": _ignore_annotations,
}


@pytest.mark.parametrize("weakening", sorted(FINALIZER_WEAKENINGS))
@pytest.mark.parametrize("name", FINALIZING_POLICIES + ("tenant-no-manual-endpoints",))
def test_a_widened_finalizer_exemption_keeps_the_gate_closed(tmp_path, name, weakening):
    assert len(checked(tmp_path, _mutated(name, FINALIZER_WEAKENINGS[weakening]))) == 1


# Each mutation narrows the route binding so it no longer covers every traefik.io
# object in every tenant namespace, while its tenant-label Exists still reads true.
@pytest.mark.parametrize(
    "narrowing",
    [
        "    objectSelector:\n      matchLabels: {protected: \"true\"}\n",
        "    resourceRules:\n      - apiGroups: [\"traefik.io\"]\n"
        "        apiVersions: [\"*\"]\n        operations: [\"CREATE\"]\n"
        "        resources: [\"ingressroutetcps\"]\n",
        "    excludeResourceRules:\n      - apiGroups: [\"traefik.io\"]\n"
        "        apiVersions: [\"*\"]\n        operations: [\"*\"]\n"
        "        resources: [\"ingressroutes\"]\n",
        "    namespaceSelector:\n      matchExpressions:\n"
        "        - key: fluxcd.io/tenant\n          operator: Exists\n"
        "        - key: kubernetes.io/metadata.name\n          operator: In\n"
        "          values: [nobody]\n",
        "    namespaceSelector:\n      matchLabels: {protected: \"true\"}\n"
        "      matchExpressions:\n"
        "        - key: fluxcd.io/tenant\n          operator: Exists\n",
        "    namespaceSelector:\n      matchExpressions:\n"
        "        - key: fluxcd.io/tenant\n          operator: Exists\n"
        "          values: [nobody]\n",
    ],
    ids=[
        "objectSelector",
        "resourceRules",
        "excludeResourceRules",
        "second-expression",
        "matchLabels",
        "expression-values",
    ],
)
def test_a_binding_that_narrows_its_match_keeps_the_gate_closed(tmp_path, narrowing):
    body = policy()
    selector = (
        "  matchResources:\n    namespaceSelector:\n      matchExpressions:\n"
        "        - key: fluxcd.io/tenant\n          operator: Exists\n"
    )
    assert selector in body
    narrowed = "  matchResources:\n" + (
        narrowing if "namespaceSelector" in narrowing else selector.split("\n", 1)[1] + narrowing
    )
    assert len(checked(tmp_path, body.replace(selector, narrowed, 1))) == 1


def _narrow_resources(spec):
    rules = spec["matchConstraints"]["resourceRules"]
    rules[0]["resources"] = rules[0]["resources"][:-1]


def _narrow_operations(spec):
    rules = [r for r in spec["matchConstraints"]["resourceRules"] if "UPDATE" in r["operations"]]
    if not rules:
        pytest.skip("no rule matches UPDATE")
    rules[0]["operations"] = ["CREATE"]


def _expression_true(index: int):
    def mutate(spec):
        spec["validations"][index]["expression"] = "true"
    return mutate


# Each mutation weakens one policy while its name and Deny binding still read
# true: errors fail open, a validation is dropped or made vacuous, or the match
# narrows.
WEAKENINGS = {
    "first-expression-true": _expression_true(0),
    "last-expression-true": _expression_true(-1),
    "failurePolicy-Ignore": lambda spec: spec.__setitem__("failurePolicy", "Ignore"),
    "failurePolicy-absent": lambda spec: spec.pop("failurePolicy"),
    "validation-dropped": lambda spec: spec["validations"].pop(),
    "validations-empty": lambda spec: spec.__setitem__("validations", []),
    "resources-narrowed": _narrow_resources,
    "operations-narrowed": _narrow_operations,
    "namespaceSelector-added": lambda spec: spec["matchConstraints"].__setitem__(
        "namespaceSelector", {"matchLabels": {"protected": "true"}}
    ),
    "matchConditions-added": lambda spec: spec.__setitem__(
        "matchConditions", [{"name": "never", "expression": "false"}]
    ),
    "paramKind-secret": lambda spec: spec.__setitem__(
        "paramKind", {"apiVersion": "v1", "kind": "Secret"}
    ),
}


@pytest.mark.parametrize("weakening", sorted(WEAKENINGS))
@pytest.mark.parametrize("name", sorted(gate.REQUIRED_POLICIES))
def test_a_weakened_policy_keeps_the_gate_closed(tmp_path, name, weakening):
    assert len(checked(tmp_path, _mutated(name, WEAKENINGS[weakening]))) == 1


@pytest.mark.parametrize("name", gate.GRANT_POLICIES)
def test_a_grant_policy_without_its_param_kind_keeps_the_gate_closed(tmp_path, name):
    """Without paramKind the per-tenant paramRef is never read."""
    assert len(checked(tmp_path, _mutated(name, lambda spec: spec.pop("paramKind")))) == 1


@pytest.mark.parametrize("name", sorted(gate.REQUIRED_POLICIES))
def test_a_second_weakened_copy_of_a_policy_keeps_the_gate_closed(tmp_path, name):
    """Only the first copy is pinned, so a duplicate name must close the gate."""
    weakened = next(
        d for d in yaml.safe_load_all(_mutated(name, WEAKENINGS["validations-empty"]))
        if d and d["kind"] == "ValidatingAdmissionPolicy" and d["metadata"]["name"] == name
    )
    problems = checked(tmp_path, PINNED_POLICY + "---\n" + yaml.safe_dump(weakened, sort_keys=False))
    assert len(problems) == 1
    assert "complete Deny-bound tenant policies" in problems[0]


SHIPPED_VARIABLES = sorted(
    (d["metadata"]["name"], v["name"])
    for d in yaml.safe_load_all(PINNED_POLICY)
    if d and d["kind"] == "ValidatingAdmissionPolicy"
    for v in d["spec"].get("variables") or []
)


# A widened variable vacates every validation that reads it while each
# expression string stays the same: an allowlist that admits anything, or a
# pattern that matches any address.
@pytest.mark.parametrize(("name", "variable"), SHIPPED_VARIABLES)
def test_a_widened_variable_keeps_the_gate_closed(tmp_path, name, variable):
    def widen(spec):
        for v in spec["variables"]:
            if v["name"] == variable:
                v["expression"] = "'.*'"

    assert len(checked(tmp_path, _mutated(name, widen))) == 1


def test_whitespace_and_the_yaml_round_trip_do_not_close_the_gate(tmp_path):
    """The pin compares whitespace-normalized CEL, so reflowing a folded
    expression is not a weakening, and the round trip the mutations use
    changes nothing by itself."""
    def reflow(spec):
        for v in spec["validations"]:
            v["expression"] = v["expression"].replace(" ", "\n  ", 1)

    assert checked(tmp_path, _mutated("tenant-traefik-object-refs", reflow)) == []
    noop = _mutated("tenant-no-manual-endpoints", lambda spec: None,
                    "ValidatingAdmissionPolicyBinding")
    assert checked(tmp_path, noop) == []


def _drop_validation(spec):
    kept = [v for v in spec["validations"] if "routerMiddlewares" not in v["expression"]]
    assert len(kept) == len(spec["validations"]) - 1
    spec["validations"] = kept


def _substring_match(spec):
    spec["variables"] = [v for v in spec["variables"] if v["name"] != "internalHostEnds"]
    for v in spec["validations"]:
        v["expression"] = v["expression"].replace(
            "!variables.internalHostEnds.exists(e, r.match.lowerAscii().contains(e))",
            "!r.match.lowerAscii().contains(variables.internalDomain)",
        )


def _unlowered(spec):
    for v in spec["variables"]:
        if v["name"] == "internalDomain":
            v["expression"] = "'${cluster_internal_domain}'"


def _unanchored(spec):
    for v in spec["validations"]:
        v["expression"] = v["expression"].replace(
            "exists(k, k.startsWith('traefik.ingress.kubernetes.io/') && ", "exists(k, ")


# An internal host without its allowlist, a host merely containing the internal
# domain, a mixed-case domain the lowered match never meets, or an allowlist in a
# key Traefik ignores for its case: each is named.
@pytest.mark.parametrize(
    ("name", "mutate", "named"),
    [
        ("tenant-traefik-ingress-refs", _drop_validation, "validations"),
        ("tenant-traefik-route-refs", _substring_match, "variables, validations"),
        ("tenant-traefik-route-refs", _unlowered, "variables"),
        ("tenant-traefik-ingress-refs", _unlowered, "variables"),
        ("tenant-traefik-ingress-refs", _unanchored, "validations"),
    ],
    ids=["ingress-no-allowlist", "route-substring", "route-unlowered", "ingress-unlowered",
         "ingress-case-folded-prefix"],
)
def test_a_weakened_internal_allowlist_is_named(tmp_path, name, mutate, named):
    problems = checked(tmp_path, _mutated(name, mutate))
    assert len(problems) == 1
    assert f"{name}: {named} differ" in problems[0]


def _gaps(root: Path) -> list[str]:
    return gate.policy_gaps(root / gate.TENANT_POLICY, root)


def test_each_policy_gap_is_named(tmp_path):
    """The finding lists what is wrong, not just that something is."""
    assert _gaps(tmp_path) == [f"{gate.TENANT_POLICY} is missing"]
    write_policy(tmp_path, PINNED_POLICY)
    assert _gaps(tmp_path) == []
    write_policy(tmp_path, PINNED_POLICY, listed=False)
    assert _gaps(tmp_path) == [
        f"{gate.CONFIGS_KUSTOMIZATION} does not list tenant-traefik-policy.yaml"
    ]
    write_policy(tmp_path, policy("Warn"))
    assert len(_gaps(tmp_path)) == 1
    assert "no binding whose whole spec is Deny" in _gaps(tmp_path)[0]
    duplicate = next(
        d for d in yaml.safe_load_all(PINNED_POLICY)
        if d and d["kind"] == "ValidatingAdmissionPolicy"
        and d["metadata"]["name"] == "tenant-no-tcp-udp-routes"
    )
    write_policy(tmp_path, PINNED_POLICY + "---\n" + yaml.safe_dump(duplicate))
    assert _gaps(tmp_path) == ["tenant-no-tcp-udp-routes: defined 2 times, not once"]
    (tmp_path / gate.CONFIGS_KUSTOMIZATION).unlink()
    assert _gaps(tmp_path) == [
        f"{gate.CONFIGS_KUSTOMIZATION} is missing, so nothing applies the policies"
    ]


def test_the_gaps_ride_under_the_finding(tmp_path):
    root = build(tmp_path, DEMO)
    write_policy(root, PINNED_POLICY, listed=False)
    problems = gate.check(root)
    assert len(problems) == 1
    assert problems[0].endswith(
        f"\n    - {gate.CONFIGS_KUSTOMIZATION} does not list tenant-traefik-policy.yaml"
    )


# Each mutation keeps the Deny action and the tenant match, so only a check of
# the complete binding spec refuses it: a paramRef that admits on a missing
# parameter fails open, and any other added field is unreviewed.
BINDING_WEAKENINGS = {
    "paramRef-allow": lambda spec: spec.__setitem__(
        "paramRef", {"name": "absent", "parameterNotFoundAction": "Allow"}
    ),
    "paramRef-selector": lambda spec: spec.__setitem__(
        "paramRef", {"selector": {}, "parameterNotFoundAction": "Allow"}
    ),
    "extra-action": lambda spec: spec.__setitem__("validationActions", ["Deny", "Audit"]),
}


@pytest.mark.parametrize("weakening", sorted(BINDING_WEAKENINGS))
@pytest.mark.parametrize(
    "name", sorted(set(gate.REQUIRED_POLICIES) - set(gate.GRANT_POLICIES))
)
def test_a_binding_with_more_than_the_required_spec_keeps_the_gate_closed(
    tmp_path, name, weakening
):
    body = _mutated(name, BINDING_WEAKENINGS[weakening], "ValidatingAdmissionPolicyBinding")
    assert len(checked(tmp_path, body)) == 1


def _binding(mutate, index: int = 2) -> list[dict]:
    docs = wiring("demo")
    mutate(docs[index]["spec"])
    return docs


def _configmap(mutate) -> list[dict]:
    docs = wiring("demo")
    mutate(docs[1])
    return docs


def _priority(value: str) -> list[dict]:
    return _configmap(lambda d: d["data"].__setitem__("maxPriority", value))


# Each mutation leaves the tenant `demo` without a Deny binding that reads its
# own platform-owned hostname grant, or without a usable grant. Both bindings
# go through one whole-spec comparison, so the hostname one carries the cases.
ROUTING_GAPS = {
    "binding-missing": lambda: [
        d for d in wiring("demo")
        if d.get("spec", {}).get("policyName") != "tenant-traefik-hostnames"
    ],
    "certificates-binding-missing": lambda: [
        d for d in wiring("demo")
        if d.get("spec", {}).get("policyName") != "tenant-certificates"
    ],
    "certificates-paramRef-allow": lambda: _binding(
        lambda s: s["paramRef"].__setitem__("parameterNotFoundAction", "Allow"), 3
    ),
    "binding-warn": lambda: _binding(lambda s: s.__setitem__("validationActions", ["Warn"])),
    "binding-extra-action": lambda: _binding(
        lambda s: s.__setitem__("validationActions", ["Deny", "Audit"])
    ),
    "binding-other-policy": lambda: _binding(
        lambda s: s.__setitem__("policyName", "tenant-traefik-route-refs")
    ),
    "binding-other-namespace": lambda: _binding(
        lambda s: s["matchResources"]["namespaceSelector"]["matchLabels"].__setitem__(
            "kubernetes.io/metadata.name", "other"
        )
    ),
    "binding-label-selector": lambda: _binding(
        lambda s: s["matchResources"].__setitem__(
            "namespaceSelector",
            {"matchExpressions": [{"key": "fluxcd.io/tenant", "operator": "Exists"}]},
        )
    ),
    "binding-object-selector": lambda: _binding(
        lambda s: s["matchResources"].__setitem__(
            "objectSelector", {"matchLabels": {"protected": "true"}}
        )
    ),
    "paramRef-other-name": lambda: _binding(
        lambda s: s["paramRef"].__setitem__("name", "tenant-routing-other")
    ),
    "paramRef-tenant-namespace": lambda: _binding(
        lambda s: s["paramRef"].__setitem__("namespace", "demo")
    ),
    "paramRef-no-namespace": lambda: _binding(lambda s: s["paramRef"].pop("namespace")),
    "paramRef-allow": lambda: _binding(
        lambda s: s["paramRef"].__setitem__("parameterNotFoundAction", "Allow")
    ),
    "paramRef-selector": lambda: _binding(
        lambda s: s.__setitem__(
            "paramRef",
            {"selector": {}, "namespace": "flux-system", "parameterNotFoundAction": "Deny"},
        )
    ),
    "paramRef-missing": lambda: _binding(lambda s: s.pop("paramRef")),
    "configmap-missing": lambda: [d for d in wiring("demo") if d["kind"] != "ConfigMap"],
    "configmap-tenant-namespace": lambda: _configmap(
        lambda d: d["metadata"].__setitem__("namespace", "demo")
    ),
    "configmap-other-name": lambda: _configmap(
        lambda d: d["metadata"].__setitem__("name", "tenant-routing-other")
    ),
    "hosts-empty": lambda: _configmap(lambda d: d["data"].__setitem__("hosts", "")),
    "hosts-blank-lines": lambda: _configmap(lambda d: d["data"].__setitem__("hosts", "\n  \n")),
    "data-missing": lambda: _configmap(lambda d: d.pop("data")),
    "hosts-wildcard": lambda: _configmap(
        lambda d: d["data"].__setitem__("hosts", "*.example.com\n")
    ),
    "hosts-wildcard-beside-exact": lambda: _configmap(
        lambda d: d["data"].__setitem__("hosts", d["data"]["hosts"] + "*.demo.example.com\n")
    ),
    "maxPriority-not-a-number": lambda: _priority("high"),
    # str.isdigit() admits this; CEL int() does not, and under failurePolicy Fail
    # the policy would then refuse every route that sets a priority.
    "maxPriority-non-ascii-digits": lambda: _priority("١٠٠"),
    "maxPriority-overflow": lambda: _priority("2147483648"),
    "maxPriority-negative": lambda: _priority("-1"),
    "maxPriority-padded": lambda: _priority(" 100\n"),
    "namespace-missing": lambda: [d for d in wiring("demo") if d["kind"] != "Namespace"],
}


def _routed(tmp_path: Path, docs: list[dict]) -> list[str]:
    """The gate's findings for one tenant `demo` whose file holds `docs`."""
    root = build(tmp_path, ["tenant-crd-editor.yaml", "demo.yaml"])
    write_policy(root, policy())
    write_tenant(root, "demo.yaml", docs)
    return gate.check(root)


@pytest.mark.parametrize("gap", sorted(ROUTING_GAPS))
def test_a_tenant_without_its_hostname_grant_fails(tmp_path, gap):
    problems = _routed(tmp_path, ROUTING_GAPS[gap]())
    assert len(problems) == 1
    assert "demo" in problems[0]


@pytest.mark.parametrize("ceiling", ["0", "100", "2147483647"])
def test_a_max_priority_number_is_accepted(tmp_path, ceiling):
    assert _routed(tmp_path, _priority(ceiling)) == []


def test_another_tenants_binding_does_not_cover_a_tenant(tmp_path):
    """Each tenant namespace needs its own binding and grant."""
    root = build(tmp_path, ["tenant-crd-editor.yaml", "alpha.yaml", "beta.yaml"])
    write_policy(root, policy())
    beta = wiring("beta")
    beta[2] = routing_binding("alpha")
    beta[2]["metadata"]["name"] = "tenant-traefik-hostnames-beta"
    beta[3] = routing_binding("alpha", "tenant-certificates")
    beta[3]["metadata"]["name"] = "tenant-certificates-beta"
    write_tenant(root, "beta.yaml", beta)
    problems = gate.check(root)
    assert len(problems) == 2
    assert all("beta" in p for p in problems)


def test_a_dot_slash_shared_resource_is_not_a_tenant(tmp_path):
    """kustomize reads `./tenant-crd-editor.yaml` as the shared RBAC file, and
    so does the wiring gate, so it is no tenant here either."""
    assert checked(tmp_path, policy(), ["./tenant-crd-editor.yaml", "./demo.yaml"]) == []


def test_a_listed_tenant_file_that_is_missing_fails(tmp_path):
    root = build(tmp_path, ["tenant-crd-editor.yaml", "demo.yaml"])
    write_policy(root, policy())
    ((root / TENANTS_KUSTOMIZATION).parent / "demo.yaml").unlink()
    problems = gate.check(root)
    assert len(problems) == 1
    assert "demo.yaml" in problems[0]


def test_the_cli_finds_the_one_tenants_tree(tmp_path):
    root = build(tmp_path, DEMO)
    write_policy(root, policy())
    assert gate.main(["--repo-root", str(root)]) == 0


def test_a_wiring_file_alone_is_checked_for_its_routing(tmp_path, capsys):
    """A tenant repo has no platform files; its rendered wiring still must
    carry the grant and both bindings."""
    path = tmp_path / "demo.yaml"
    path.write_text(yaml.safe_dump_all(wiring("demo"), sort_keys=False))
    assert gate.main([str(path)]) == 0
    path.write_text(yaml.safe_dump_all(wiring("demo")[:2], sort_keys=False))
    assert gate.main([str(path)]) == 1
    assert "tenant-traefik-hostnames" in capsys.readouterr().err


def test_a_nameless_namespace_is_never_reported_as_none():
    docs = {"demo.yaml": [{"kind": "Namespace", "metadata": {"labels": {}}}]}
    problems = gate.routing_problems_for(docs)
    assert problems and all("None" not in p for p in problems), problems
    assert "declares no Namespace" in problems[0]
    named = {"demo.yaml": wiring("demo") + [{"kind": "Namespace", "metadata": "x"}]}
    assert gate.routing_problems_for(named) == []
