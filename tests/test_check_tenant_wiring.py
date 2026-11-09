"""Coverage for check-tenant-wiring.py.

Every arm runs against a fixture tenant file, and each mutation case must FAIL.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from script_loader import load_script

gate = load_script("check-tenant-wiring.py")
from tenant_common import discover_tenants_dir  # noqa: E402  (on sys.path once the gate loads)
TENANTS_DIR = "kubernetes/clusters/demo/tenants"
TENANT_LOCAL = ("--tenant-storage-class",)

TENANT = textwrap.dedent(
    """\
    ---
    apiVersion: v1
    kind: Namespace
    metadata:
      name: demo-app
      annotations:
        kustomize.toolkit.fluxcd.io/prune: disabled
      labels:
        fluxcd.io/tenant: demo-app
        pod-security.kubernetes.io/enforce: baseline
        pod-security.kubernetes.io/warn: restricted
        pod-security.kubernetes.io/audit: restricted
    ---
    apiVersion: v1
    kind: ResourceQuota
    metadata:
      name: demo-app-quota
      namespace: demo-app
      annotations:
        kustomize.toolkit.fluxcd.io/prune: disabled
    spec:
      hard:
        pods: "10"
        requests.ephemeral-storage: 4Gi
        limits.ephemeral-storage: 8Gi
        persistentvolumeclaims: "4"
        tenant-local.storageclass.storage.k8s.io/requests.storage: 40Gi
    ---
    apiVersion: v1
    kind: LimitRange
    metadata:
      name: demo-app-limits
      namespace: demo-app
      annotations:
        kustomize.toolkit.fluxcd.io/prune: disabled
    spec:
      limits:
        - type: Container
          default:
            memory: 512Mi
            ephemeral-storage: 512Mi
          defaultRequest:
            ephemeral-storage: 128Mi
          max:
            ephemeral-storage: 2Gi
    ---
    apiVersion: v1
    kind: ServiceAccount
    metadata:
      name: demo-app-flux
      namespace: flux-system
    ---
    apiVersion: rbac.authorization.k8s.io/v1
    kind: RoleBinding
    metadata:
      name: demo-app-flux-admin
      namespace: demo-app
    subjects:
      - kind: ServiceAccount
        name: demo-app-flux
        namespace: flux-system
    roleRef:
      kind: ClusterRole
      name: admin
      apiGroup: rbac.authorization.k8s.io
    ---
    apiVersion: rbac.authorization.k8s.io/v1
    kind: RoleBinding
    metadata:
      name: demo-app-flux-crd-editor
      namespace: demo-app
    subjects:
      - kind: ServiceAccount
        name: demo-app-flux
        namespace: flux-system
    roleRef:
      kind: ClusterRole
      name: tenant-crd-editor
      apiGroup: rbac.authorization.k8s.io
    ---
    apiVersion: source.toolkit.fluxcd.io/v1
    kind: GitRepository
    metadata:
      name: demo-app
      namespace: flux-system
    spec:
      interval: 1m
      url: https://example.invalid/demo/demo-app
      ref:
        branch: main
    ---
    apiVersion: kustomize.toolkit.fluxcd.io/v1
    kind: Kustomization
    metadata:
      name: demo-app
      namespace: flux-system
    spec:
      dependsOn:
        - name: infrastructure-configs
      serviceAccountName: demo-app-flux
      sourceRef:
        kind: GitRepository
        name: demo-app
      path: ./kubernetes/flux
      prune: true
      targetNamespace: demo-app
      postBuild:
        substituteFrom:
          - kind: ConfigMap
            name: cluster-versions
            optional: false
          - kind: ConfigMap
            name: cluster-config
            optional: false
    """
)

STORE = textwrap.dedent(
    """\
    ---
    apiVersion: external-secrets.io/v1
    kind: ClusterSecretStore
    metadata:
      name: onepassword-demo-app
    spec:
      conditions:
        - namespaces:
            - demo-app
      provider:
        onepassword:
          connectHost: http://onepassword-connect.external-secrets.svc.cluster.local:8080
          vaults:
            demo-app: 1
          auth:
            secretRef:
              connectTokenSecretRef:
                name: onepassword-connect-token
                namespace: demo-app
                key: token
    """
)

AGGREGATOR = textwrap.dedent(
    """\
    apiVersion: kustomize.config.k8s.io/v1beta1
    kind: Kustomization
    resources:
      - tenant-crd-editor.yaml
      - demo-app.yaml
    """
)

SECOND = textwrap.dedent(
    """\
    ---
    apiVersion: kustomize.toolkit.fluxcd.io/v1
    kind: Kustomization
    metadata:
      name: demo-app-db
      namespace: flux-system
    spec:
      dependsOn:
        - name: infrastructure-configs
      sourceRef:
        kind: GitRepository
        name: demo-app
      path: ./kubernetes/db
      prune: true
      targetNamespace: demo-app
      postBuild:
        substituteFrom:
          - kind: ConfigMap
            name: cluster-config
            optional: false
    """
)
CONFINED_SECOND = SECOND.replace(
    "  dependsOn:\n", "  serviceAccountName: demo-app-flux\n  dependsOn:\n"
).replace("  prune: true\n", "  prune: true\n  deletionPolicy: Orphan\n")

EXTRA_NAMESPACE = textwrap.dedent(
    """\
    ---
    apiVersion: v1
    kind: Namespace
    metadata:
      name: demo-app-jobs
      annotations:
        kustomize.toolkit.fluxcd.io/prune: disabled
      labels:
        fluxcd.io/tenant: demo-app
        pod-security.kubernetes.io/enforce: baseline
        pod-security.kubernetes.io/warn: restricted
        pod-security.kubernetes.io/audit: restricted
    """
)
JOBS_CONFIGMAP = textwrap.dedent(
    """\
    ---
    apiVersion: v1
    kind: ConfigMap
    metadata:
      name: jobs-settings
      namespace: demo-app-jobs
    """
)

ROUTING = textwrap.dedent(
    """\
    ---
    apiVersion: v1
    kind: ConfigMap
    metadata:
      name: tenant-routing-demo-app
      namespace: flux-system
      annotations:
        kustomize.toolkit.fluxcd.io/prune: disabled
    data:
      hosts: |
        demo-app.example.com
    ---
    apiVersion: admissionregistration.k8s.io/v1
    kind: ValidatingAdmissionPolicyBinding
    metadata:
      name: tenant-traefik-hostnames-demo-app
      annotations:
        kustomize.toolkit.fluxcd.io/prune: disabled
    spec:
      policyName: tenant-traefik-hostnames
      validationActions: [Deny]
      paramRef:
        name: tenant-routing-demo-app
        namespace: flux-system
        parameterNotFoundAction: Deny
      matchResources:
        namespaceSelector:
          matchLabels:
            kubernetes.io/metadata.name: demo-app
    ---
    apiVersion: admissionregistration.k8s.io/v1
    kind: ValidatingAdmissionPolicyBinding
    metadata:
      name: tenant-certificates-demo-app
      annotations:
        kustomize.toolkit.fluxcd.io/prune: disabled
    spec:
      policyName: tenant-certificates
      validationActions: [Deny]
      paramRef:
        name: tenant-routing-demo-app
        namespace: flux-system
        parameterNotFoundAction: Deny
      matchResources:
        namespaceSelector:
          matchLabels:
            kubernetes.io/metadata.name: demo-app
    """
)

LOOKALIKE = "---\napiVersion: example.com/v1\nkind: ClusterRole\nmetadata:\n  name: demo-app-lookalike\n"
GUARD = "  annotations:\n    kustomize.toolkit.fluxcd.io/prune: disabled\n"
SUBSTITUTE_VERSIONS = (
    "      - kind: ConfigMap\n        name: cluster-versions\n        optional: false\n"
)
POST_BUILD = TENANT[TENANT.index("  postBuild:\n"):]
SOURCE_REF = "  sourceRef:\n    kind: GitRepository\n    name: demo-app\n"
CONDITIONS = "    - namespaces:\n        - demo-app"
# The fixture's Container ephemeral-storage lines, by the LimitRange key they sit under.
EPHEMERAL_LIMITS = {
    "default": "        ephemeral-storage: 512Mi\n",
    "defaultRequest": "        ephemeral-storage: 128Mi\n",
    "max": "        ephemeral-storage: 2Gi\n",
}
CLAIM_CAPS = (
    "persistentvolumeclaims",
    "tenant-local.storageclass.storage.k8s.io/requests.storage",
)
ONEPASSWORD = STORE[STORE.index("    onepassword:\n"):]
SDK_STORE = STORE.replace(
    ONEPASSWORD,
    "    onepasswordSDK:\n"
    "      vault: VAULT\n"
    "      auth:\n"
    "        serviceAccountSecretRef:\n"
    "          name: onepassword-sa-token\n"
    "          namespace: demo-app\n"
    "          key: token\n",
)

GITLAB_STORE = STORE.replace(
    ONEPASSWORD,
    "    gitlab:\n"
    "      url: https://gitlab.example.com\n"
    "      projectID: \"7\"\n"
    "      auth:\n"
    "        SecretRef:\n"
    "          accessToken:\n"
    "            name: gitlab-api-token\n"
    "            namespace: demo-app\n"
    "            key: token\n",
)


def write(root: Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    write(tmp_path, f"{TENANTS_DIR}/{gate.AGGREGATOR}", AGGREGATOR)
    write(tmp_path, f"{TENANTS_DIR}/tenant-crd-editor.yaml", "kind: ClusterRole\n")
    write(tmp_path, f"{TENANTS_DIR}/demo-app.yaml", TENANT)
    return tmp_path


def check(root: Path, storage_classes: tuple[str, ...] = ()) -> list[str]:
    """Every wiring file in the one tenants directory under `root`."""
    tenants_dir = discover_tenants_dir(root)
    return gate.check_directory(gate.Targets(root, tenants_dir=tenants_dir), storage_classes)


def wire(root: Path, body: str, storage_classes: tuple[str, ...] = ()) -> list[str]:
    """Findings for the demo tenant file replaced by `body`."""
    write(root, f"{TENANTS_DIR}/demo-app.yaml", body)
    return check(root, storage_classes)


def run(root: Path, *flags: str) -> int:
    return gate.main([*(flags or TENANT_LOCAL), "--repo-root", str(root)])


def drop(body: str, needle: str) -> str:
    return "\n".join(line for line in body.splitlines() if needle not in line) + "\n"


def without_doc(body: str, marker: str) -> str:
    """`body` with the one document containing `marker` removed."""
    docs = body.split("\n---\n")
    hit = [i for i, d in enumerate(docs) if marker in d]
    assert len(hit) == 1, hit
    del docs[hit[0]]
    return "\n---\n".join(docs)


def guard(body: str, marker: str, value: str | None) -> str:
    """`body` with the prune guard of the one document holding `marker` removed
    (None) or set to `value`."""
    docs = body.split("\n---\n")
    hit = [i for i, d in enumerate(docs) if marker in d]
    assert len(hit) == 1 and GUARD in docs[hit[0]], hit
    docs[hit[0]] = docs[hit[0]].replace(
        GUARD, "" if value is None else GUARD.replace("disabled", value)
    )
    return "\n---\n".join(docs)


def test_a_complete_tenant_file_passes(repo: Path, capsys) -> None:
    assert check(repo) == []
    assert run(repo) == 0
    assert "1 tenant file(s) in" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("body", "classes"),
    [
        pytest.param(TENANT.replace(SUBSTITUTE_VERSIONS, ""), (), id="cluster-config-only"),
        pytest.param(
            drop(TENANT, "    tenant-local.storageclass") + "---\napiVersion: v1\n"
            "kind: ResourceQuota\nmetadata:\n  name: demo-app-storage\n  namespace: demo-app\n"
            + GUARD + "spec:\n  hard:\n    " + CLAIM_CAPS[1] + ": 40Gi\n",
            ("tenant-local",), id="claim-caps-on-a-second-quota",
        ),
        pytest.param(
            TENANT.replace(EPHEMERAL_LIMITS["defaultRequest"],
                           "        cpu: 50m\n" + EPHEMERAL_LIMITS["defaultRequest"]),
            (), id="cpu-default-request",
        ),
        pytest.param(TENANT + LOOKALIKE.replace("example.com/v1", "rbac.authorization.k8s.io/v1"),
                     (), id="real-cluster-role"),
        pytest.param(TENANT + STORE, (), id="store-scoped-to-the-tenant"),
        pytest.param(TENANT + CONFINED_SECOND, (), id="confined-db-kustomization"),
        pytest.param(TENANT + EXTRA_NAMESPACE + JOBS_CONFIGMAP, (), id="second-namespace"),
        pytest.param(
            TENANT + EXTRA_NAMESPACE
            + STORE.replace("        - demo-app\n", "        - demo-app\n        - demo-app-jobs\n"),
            (), id="store-admits-both-namespaces",
        ),
        pytest.param(
            (TENANT + EXTRA_NAMESPACE)
            .replace("  targetNamespace: demo-app\n", "  targetNamespace: demo-app-jobs\n")
            .replace("  namespace: demo-app\nsubjects:", "  namespace: demo-app-jobs\nsubjects:"),
            (), id="target-and-bindings-in-the-second-namespace",
        ),
        pytest.param(TENANT + ROUTING, (), id="cluster-scoped-grant-bindings"),
        pytest.param(
            TENANT + ROUTING.replace(GUARD, "")
            .replace("tenant-routing-demo-app", "demo-app-settings")
            .replace("policyName: tenant-traefik-hostnames", "policyName: demo-app-other")
            .replace("policyName: tenant-certificates", "policyName: demo-app-another"),
            (), id="unrelated-configmap-and-binding-need-no-guard",
        ),
        pytest.param(
            TENANT + EXTRA_NAMESPACE
            + STORE.replace("        - demo-app\n", "        - demo-app\n        - demo-app-jobs\n")
            .replace("        demo-app: 1\n", "        demo-app-jobs: 1\n"),
            (), id="vault-named-after-the-second-namespace",
        ),
        pytest.param(
            TENANT + "---\napiVersion: rbac.authorization.k8s.io/v1\nkind: Role\n"
            "metadata:\n  name: admin\n  namespace: demo-app\nrules: []\n",
            (), id="local-role-named-admin-beside-the-cluster-role-binding",
        ),
        pytest.param(TENANT + SDK_STORE.replace("VAULT", "demo-app"), (), id="sdk-store"),
        pytest.param(TENANT + GITLAB_STORE, (), id="gitlab-store-with-the-tenant-token"),
    ],
)
def test_a_confined_variant_passes(repo: Path, body: str, classes: tuple[str, ...]) -> None:
    assert wire(repo, body, classes) == []


def _cases():
    """(body, expected) pairs: each mutation of the tenant file is a finding."""
    t = TENANT
    with_store = t + STORE
    yield from (
        pytest.param(drop(t, needle), expected, id=f"drop-{needle}")
        for needle, expected in [
            ("serviceAccountName", "serviceAccountName"),
            ("prune: true", "prune: true"),
            ("targetNamespace", "targetNamespace"),
            ("infrastructure-configs", "dependOn"),
            ("pod-security.kubernetes.io/enforce", "pod-security.kubernetes.io/enforce"),
            ("fluxcd.io/tenant", "fluxcd.io/tenant"),
            ("name: cluster-config", "does not substituteFrom ConfigMap cluster-config"),
            ("  path: ./kubernetes/flux", "no spec.path"),
        ]
    )
    yield from (
        pytest.param(body, expected, id=name)
        for name, body, expected in [
            ("rolebinding-admin",
             t.replace("  name: admin\n  apiGroup:", "  name: view\n  apiGroup:"), "grants admin"),
            ("rolebinding-crd-editor",
             t.replace("  name: tenant-crd-editor\n  apiGroup:", "  name: view\n  apiGroup:"),
             "grants tenant-crd-editor"),
            ("substitute-optional", t.replace("        optional: false", "        optional: true"),
             "is not optional: false"),
            ("no-postbuild", t.split("  postBuild:")[0],
             "does not substituteFrom ConfigMap cluster-config"),
            ("substitute-secret", t.replace(SUBSTITUTE_VERSIONS, SUBSTITUTE_VERSIONS.replace(
                "kind: ConfigMap", "kind: Secret")), "is not a ConfigMap"),
            ("substitute-no-kind", t.replace(SUBSTITUTE_VERSIONS, SUBSTITUTE_VERSIONS.replace(
                "      - kind: ConfigMap\n", "      -\n")), "is not a ConfigMap"),
            ("substitute-unlisted", t.replace(SUBSTITUTE_VERSIONS, SUBSTITUTE_VERSIONS.replace(
                "cluster-versions", "other-config")), "ConfigMap other-config is not one of"),
            ("postbuild-bool", t.replace(POST_BUILD, "  postBuild: true\n"),
             "postBuild is not a mapping"),
            ("postbuild-list", t.replace(POST_BUILD, "  postBuild:\n    - cluster-config\n"),
             "postBuild is not a mapping"),
            ("substitute-string", t.replace(
                POST_BUILD, "  postBuild:\n    substituteFrom: cluster-config\n"),
             "substituteFrom is not a list"),
            ("substitute-mapping", t.replace(
                POST_BUILD, "  postBuild:\n    substituteFrom:\n      name: cluster-config\n"),
             "substituteFrom is not a list"),
            ("no-source", t.replace(SOURCE_REF, ""), "no sourceRef.name"),
            ("source-undeclared", t.replace("    name: demo-app\n  path:", "    name: other\n  path:"),
             "no GitRepository other in flux-system"),
            ("source-kind", t.replace("    kind: GitRepository\n", "    kind: OCIRepository\n"),
             "sourceRef.kind"),
            ("source-namespace", t.replace("  sourceRef:\n", "  sourceRef:\n    namespace: demo-app\n"),
             "sourceRef.namespace"),
            ("path-in-this-repo", t.replace("  path: ./kubernetes/flux\n", "  path: ./kubernetes\n"),
             "directory of this"),
            ("path-climbs", t.replace("  path: ./kubernetes/flux\n",
                                      "  path: ../other-repo/kubernetes\n"),
             "climbs above the tenant repository root"),
            ("no-resourcequota", without_doc(t, "kind: ResourceQuota\n"), "declares no ResourceQuota"),
            ("no-limitrange", without_doc(t, "kind: LimitRange\n"), "declares no LimitRange"),
            *(
                (f"quota-{key}", drop(t, f"    {key}:"),
                 f"does not cap ephemeral storage; missing spec.hard keys {key}")
                for key in gate.EPHEMERAL_QUOTA_KEYS
            ),
            ("limitrange-no-container", t.replace("    - type: Container\n", "    - type: Pod\n"),
             "LimitRange demo-app-limits has no Container entry"),
            ("cap-in-another-namespace", t.replace(
                "  name: demo-app-quota\n  namespace: demo-app",
                "  name: demo-app-quota\n  namespace: other-app"),
             "neither the tenant namespace demo-app"),
            ("document-without-namespace", t.replace(
                "  name: demo-app-limits\n  namespace: demo-app", "  name: demo-app-limits"),
             "names no metadata.namespace"),
            ("cluster-kind-in-another-group", t + LOOKALIKE,
             "ClusterRole demo-app-lookalike names no metadata.namespace"),
            ("service-account-in-tenant-namespace", t.replace(
                "kind: ServiceAccount\nmetadata:\n  name: demo-app-flux\n  namespace: flux-system",
                "kind: ServiceAccount\nmetadata:\n  name: demo-app-flux\n  namespace: demo-app"),
             "kustomize-controller impersonates"),
            ("no-namespace", without_doc(t, "kind: Namespace\n"),
             f"{TENANTS_DIR}/demo-app.yaml: declares no Namespace"),
            ("no-flux-kustomization",
             without_doc(t, "apiVersion: kustomize.toolkit.fluxcd.io/v1\nkind: Kustomization\n"),
             "declares no Flux Kustomization"),
            ("kustomize-config-kustomization", t.replace(
                "apiVersion: kustomize.toolkit.fluxcd.io/v1",
                "apiVersion: kustomize.config.k8s.io/v1beta1"), "declares no Flux Kustomization"),
            ("kustomization-in-a-lookalike-group", t.replace(
                "apiVersion: kustomize.toolkit.fluxcd.io/v1",
                "apiVersion: example.com/kustomize.toolkit.fluxcd.io/v1"),
             "declares no Flux Kustomization"),
            ("second-kustomization-unconfined", t + SECOND, "[demo-app-db]: Kustomization has no "
             "serviceAccountName"),
            ("target-undeclared-namespace", (t + EXTRA_NAMESPACE).replace(
                "  targetNamespace: demo-app\n", "  targetNamespace: elsewhere\n"),
             "targetNamespace 'elsewhere'"),
            ("target-second-namespace-unbound", (t + EXTRA_NAMESPACE).replace(
                "  targetNamespace: demo-app\n", "  targetNamespace: demo-app-jobs\n"),
             "RoleBinding is not in the tenant namespace demo-app-jobs"),
            ("store-no-conditions", t + STORE.replace(f"  conditions:\n{CONDITIONS}\n", ""),
             "has no spec.conditions"),
            ("store-other-namespace", t + STORE.replace("        - demo-app", "        - other-app"),
             "do not admit the tenant namespace"),
            ("store-second-namespace", t + STORE.replace(
                "        - demo-app", "        - demo-app\n        - other-app"),
             "reaches past the tenant namespace"),
            *(
                (f"store-namespaces-{bad}", t + STORE.replace(CONDITIONS, f"    - namespaces: {bad}"),
                 "do not admit the tenant namespace")
                for bad in ("demo-app-x", "7", "demo")
            ),
            *(
                (f"store-{key}", t + STORE.replace(CONDITIONS, f"{CONDITIONS}\n    - {key}: {value}"),
                 "reaches past")
                for key, value in [
                    ("namespaceRegexes", "[demo-.*]"),
                    ("namespaceSelector", "{matchLabels: {tenant: 'true'}}"),
                    ("namespaceSelector", "{}"),
                    ("namespaceRegexes", "[]"),
                ]
            ),
            *(
                (f"vault-{name}", t + STORE.replace("        demo-app: 1\n", vaults),
                 "not the tenant's own vault alone (demo-app)")
                for name, vaults in [
                    ("own-then-platform", "        demo-app: 1\n        Homelab: 2\n"),
                    ("own-then-another-tenant", "        demo-app: 1\n        other-app: 2\n"),
                    ("another-tenant", "        other-app: 1\n"),
                ]
            ),
            ("vault-empty", t + STORE.replace("      vaults:\n        demo-app: 1\n",
                                              "      vaults: {}\n"), "names no 1Password vault"),
            ("vault-absent", t + STORE.replace("      vaults:\n        demo-app: 1\n", ""),
             "names no 1Password vault"),
            ("sdk-vault-absent", t + SDK_STORE.replace("      vault: VAULT\n", ""),
             "names no 1Password vault"),
            ("provider-none", t + STORE.replace("  provider:\n" + ONEPASSWORD, "  provider: {}\n"),
             "uses provider none"),
            ("provider-two", t + STORE.replace(
                "  provider:\n", "  provider:\n    onepasswordSDK:\n      vault: demo-app\n"),
             "uses provider onepassword, onepasswordSDK"),
            ("token-ref-sdk", t + SDK_STORE.replace("VAULT", "demo-app").replace(
                "namespace: demo-app", "namespace: external-secrets"),
             "credentials from namespace external-secrets"),
        ]
    )
    # A malformed entry is a finding naming what it breaks, never a crash.
    yield from (
        pytest.param(with_store.replace(old, new, 1), expected, id=f"malformed-{name}")
        for name, old, new, expected in [
            ("condition-string", CONDITIONS, "    - demo-app", "has no spec.conditions"),
            ("conditions-string", f"  conditions:\n{CONDITIONS}", "  conditions: demo-app",
             "has no spec.conditions"),
            ("condition-mixed", CONDITIONS, "    - namespaces: [demo-app]\n    - demo-app",
             "spec.conditions entry that is not a mapping"),
            ("condition-null", CONDITIONS, "    - null", "has no spec.conditions"),
            ("condition-null-mixed", CONDITIONS, f"{CONDITIONS}\n    - null",
             "spec.conditions entry that is not a mapping"),
            ("condition-namespaces-null", CONDITIONS, "    - namespaces: null",
             "do not admit the tenant namespace"),
            ("subject-string",
             "  - kind: ServiceAccount\n    name: demo-app-flux\n    namespace: flux-system\n",
             "  - demo-app-flux\n", "no RoleBinding grants admin"),
            ("roleref-string",
             "roleRef:\n  kind: ClusterRole\n  name: admin\n  apiGroup: rbac.authorization.k8s.io\n",
             "roleRef: admin\n", "no RoleBinding grants admin"),
            ("dependson-string", "    - name: infrastructure-configs\n", "    - infrastructure-configs\n",
             "does not dependOn infrastructure-configs"),
            ("sourceref-string", SOURCE_REF, "  sourceRef: demo-app\n", "has no sourceRef.name"),
            ("metadata-list", "kind: Namespace\nmetadata:\n  name: demo-app\n",
             "kind: Namespace\nmetadata: [demo-app]\nunread:\n",
             "namespace None has no fluxcd.io/tenant label"),
            ("namespace-list", "  name: demo-app-quota\n  namespace: demo-app",
             "  name: demo-app-quota\n  namespace: [demo-app]",
             "malformed manifest shape — ResourceQuota demo-app-quota metadata.namespace is a list"),
            ("name-list", "  name: demo-app-flux\n  namespace: flux-system\n---",
             "  name: [demo-app-flux]\n  namespace: flux-system\n---",
             "malformed manifest shape — ServiceAccount ? metadata.name is a list"),
            ("account-mapping", "serviceAccountName: demo-app-flux", "serviceAccountName: {a: b}",
             "spec.serviceAccountName is a dict"),
            ("source-name-list", "    kind: GitRepository\n    name: demo-app",
             "    kind: GitRepository\n    name: [demo-app]", "spec.sourceRef.name is a list"),
        ]
    )


@pytest.mark.parametrize(("body", "expected"), list(_cases()))
def test_a_mutation_is_a_finding(repo: Path, body: str, expected: str) -> None:
    problems = wire(repo, body)
    assert any(expected in p for p in problems), problems
    assert run(repo) == 1


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(
            TENANT.replace(SUBSTITUTE_VERSIONS, SUBSTITUTE_VERSIONS * 2),
            "substituteFrom lists ConfigMap cluster-versions more than once",
            id="repeated-substitute-source",
        ),
        *(
            pytest.param(
                TENANT.replace(EPHEMERAL_LIMITS[key], '        cpu: "1"\n' + EPHEMERAL_LIMITS[key]),
                f"LimitRange demo-app-limits sets cpu under {key}", id=f"cpu-under-{key}",
            )
            for key in ("default", "max")
        ),
        pytest.param(
            TENANT + STORE.replace(CONDITIONS, "    - namespaceSelector:\n        matchLabels:\n"
                                   "          kubernetes.io/metadata.name: demo-app"),
            "reaches past", id="name-label-selector-admits-the-namespace",
        ),
        pytest.param(TENANT + SDK_STORE.replace("VAULT", "Homelab"), "reads vault(s) Homelab",
                     id="sdk-vault-another"),
        *(
            pytest.param(TENANT + STORE.replace(ONEPASSWORD, provider),
                         "its reach cannot be checked", id=f"provider-{name}")
            for name, provider in [("fake", "    fake:\n      data: []\n"),
                                   ("vault", "    vault:\n      server: https://vault.example\n")]
        ),
        pytest.param(TENANT + STORE.replace("namespace: demo-app", "namespace: external-secrets"),
                     "credentials from namespace external-secrets", id="token-ref"),
        pytest.param(TENANT + GITLAB_STORE.replace("namespace: demo-app", "namespace: gitlab"),
                     "credentials from namespace gitlab", id="gitlab-token-ref"),
        pytest.param(TENANT + STORE.replace("        demo-app: 1\n", "        Homelab: 1\n"),
                     "not the tenant's own vault alone (demo-app)", id="vault-platform"),
        pytest.param(TENANT + EXTRA_NAMESPACE + STORE,
                     "do not admit the tenant namespace demo-app-jobs", id="store-misses-a-namespace"),
        *(
            pytest.param(TENANT + drop(EXTRA_NAMESPACE, f"{label}:"),
                         f"namespace demo-app-jobs has no {label} label", id=f"second-ns-{label}")
            for label in gate.REQUIRED_NS_LABELS
        ),
    ],
)
def test_a_mutation_is_the_only_finding(repo: Path, body: str, expected: str) -> None:
    """These arms report one finding and nothing collateral."""
    problems = wire(repo, body)
    assert len(problems) == 1 and expected in problems[0], problems


@pytest.mark.parametrize(
    "missing",
    [CLAIM_CAPS[:1], CLAIM_CAPS[1:], CLAIM_CAPS],
    ids=["persistentvolumeclaims", "requests.storage", "both"],
)
def test_a_quota_that_does_not_cap_the_named_class_fails(
    repo: Path, missing: tuple[str, ...]
) -> None:
    """The class enforces no claim size, so the quota is the only bound on how
    many claims, and how much storage, a tenant may declare."""
    body = TENANT
    for key in missing:
        body = drop(body, f"    {key}:")
    problems = wire(repo, body, ("tenant-local",))
    expected = (
        "ResourceQuota does not cap persistent volume claims; missing spec.hard keys "
        + ", ".join(missing)
    )
    assert any(expected in p for p in problems), problems


def test_no_storage_class_still_caps_the_claim_count(repo: Path) -> None:
    body = drop(TENANT, "    tenant-local.storageclass")
    write(repo, f"{TENANTS_DIR}/demo-app.yaml", body)
    assert run(repo, "--no-tenant-storage-class") == 0
    problems = wire(repo, drop(body, "    persistentvolumeclaims:"))
    assert any("missing spec.hard keys persistentvolumeclaims" in p for p in problems), problems


def test_the_storage_flag_is_required(repo: Path) -> None:
    """An omitted flag would drop the size caps, so the gate refuses to guess."""
    with pytest.raises(SystemExit) as exc:
        gate.main(["--repo-root", str(repo)])
    assert exc.value.code == 2


@pytest.mark.parametrize(
    "unset",
    [("default",), ("defaultRequest",), ("max",), ("default", "defaultRequest", "max")],
    ids=["default", "defaultRequest", "max", "all"],
)
def test_a_limit_range_without_an_ephemeral_storage_limit_fails(
    repo: Path, unset: tuple[str, ...]
) -> None:
    body = TENANT
    for key in unset:
        body = body.replace(EPHEMERAL_LIMITS[key], "")
    problems = wire(repo, body)
    expected = f"LimitRange demo-app-limits sets no ephemeral-storage under {', '.join(unset)}"
    assert len(problems) == 1 and expected in problems[0], problems


@pytest.mark.parametrize(
    ("body", "marker", "value"),
    [
        *((TENANT, f"kind: {kind}\n", value)
          for kind in ("Namespace", "ResourceQuota", "LimitRange") for value in (None, "enabled")),
        (TENANT + EXTRA_NAMESPACE, "name: demo-app-jobs", None),
        *((TENANT + ROUTING, f"metadata:\n  name: {name}\n", None)
          for name in ("tenant-routing-demo-app", "tenant-traefik-hostnames-demo-app",
                       "tenant-certificates-demo-app")),
    ],
)
def test_an_object_without_the_prune_guard_fails(
    repo: Path, body: str, marker: str, value: str | None
) -> None:
    """Every Namespace, its caps, the hostname grant and both bindings outlive
    the wiring: a pruned Namespace cascades through every object and PVC in it."""
    problems = wire(repo, guard(body, marker, value))
    assert len(problems) == 1, problems
    assert "lacks kustomize.toolkit.fluxcd.io/prune: disabled" in problems[0]


def test_an_unguarded_namespace_beside_a_database_names_the_database(repo: Path) -> None:
    problems = wire(repo, guard(TENANT, "kind: Namespace\n", None) + CONFINED_SECOND)
    assert len(problems) == 1 and "cascade-delete the database" in problems[0], problems


@pytest.mark.parametrize("policy", [None, "Delete"], ids=["absent", "delete"])
@pytest.mark.parametrize("by", ["name", "path", "nested-path"])
def test_a_database_kustomization_without_orphan_fails(
    repo: Path, policy: str | None, by: str
) -> None:
    """Without `deletionPolicy: Orphan`, deleting the db Kustomization garbage
    collects the database Cluster and its PVCs with it."""
    replacement = "" if policy is None else f"  deletionPolicy: {policy}\n"
    second = CONFINED_SECOND.replace("  deletionPolicy: Orphan\n", replacement)
    if by == "name":
        second = second.replace("path: ./kubernetes/db\n", "path: ./kubernetes/database\n")
    else:
        second = second.replace("name: demo-app-db\n", "name: demo-app-data\n")
    if by == "nested-path":
        second = second.replace("path: ./kubernetes/db\n", "path: ./kubernetes/db/overlays/prod\n")
    problems = wire(repo, TENANT + second)
    assert len(problems) == 1 and "deletionPolicy: Orphan" in problems[0], problems


def test_a_malformed_standalone_file_is_a_finding(repo: Path) -> None:
    path = repo / TENANTS_DIR / "demo-app.yaml"
    path.write_text(TENANT.replace("  namespace: demo-app\n  annotations", "  namespace: [x]\n  annotations", 1))
    problems = gate.check_files(gate.Targets(repo, files=[path]))
    assert any("malformed manifest shape" in p for p in problems), problems


def test_an_unreadable_tenant_file_is_a_finding(repo: Path) -> None:
    (repo / TENANTS_DIR / "demo-app.yaml").write_bytes(b"kind: \xff\xfe\n")
    assert any("demo-app.yaml: unreadable:" in p for p in check(repo))
    problems = wire(repo, TENANT + "kind: [unclosed\n")
    assert any("unparseable YAML:" in p and "line" in p for p in problems), problems
    assert run(repo) == 1


def test_a_gate_bug_is_a_traceback_not_a_manifest_finding(repo: Path, monkeypatch) -> None:
    """A fault in the gate itself surfaces as an error, never as a red tenant file."""

    def broken(*_args, **_kwargs):
        raise TypeError("gate bug")

    monkeypatch.setattr(gate, "_caps_problems", broken)
    with pytest.raises(TypeError, match="gate bug"):
        check(repo)


def _aggregate(repo: Path, entry: str) -> None:
    write(repo, f"{TENANTS_DIR}/{gate.AGGREGATOR}", AGGREGATOR.replace("  - demo-app.yaml\n", entry))


def test_an_unlisted_tenant_file_fails(repo: Path) -> None:
    _aggregate(repo, "")
    assert any("not listed" in p for p in check(repo))


def test_a_listed_but_absent_file_fails(repo: Path) -> None:
    (repo / TENANTS_DIR / "demo-app.yaml").unlink()
    assert any("does not exist" in p for p in check(repo))


@pytest.mark.parametrize(
    ("entry", "where"),
    [("  - ./demo-app.yaml\n", "demo-app.yaml"), ("  - demo-app.yml\n", "demo-app.yml"),
     ("  - ./teams/demo-app.yaml\n", "teams/demo-app.yaml")],
    ids=["dot-prefixed", "yml-suffix", "subdirectory"],
)
def test_a_listed_tenant_file_is_inspected(repo: Path, entry: str, where: str) -> None:
    """kustomize reads `./x` as `x` and applies any suffix or subdirectory, so
    each listed file is checked and none is called unlisted."""
    (repo / TENANTS_DIR / "demo-app.yaml").unlink()
    write(repo, f"{TENANTS_DIR}/{where}", drop(TENANT, "pod-security.kubernetes.io/enforce"))
    _aggregate(repo, entry)
    problems = check(repo)
    assert problems == [f"{TENANTS_DIR}/{where}: namespace demo-app has no "
                        "pod-security.kubernetes.io/enforce label"], problems


def test_an_unlisted_yml_tenant_on_disk_is_read(repo: Path) -> None:
    write(repo, f"{TENANTS_DIR}/other-app.yml", TENANT.replace("demo-app", "other-app"))
    assert any("other-app.yml: not listed" in p for p in check(repo))


def test_a_listed_directory_is_a_finding(repo: Path) -> None:
    """Kustomize applies a listed directory, so the gate cannot drop it unread."""
    (repo / TENANTS_DIR / "other-app").mkdir()
    write(repo, f"{TENANTS_DIR}/{gate.AGGREGATOR}", AGGREGATOR + "  - other-app\n")
    assert check(repo) == [
        f"{TENANTS_DIR}/{gate.AGGREGATOR} lists other-app, which is not a regular "
        "file, so no tenant wiring in it is checked"
    ]


@pytest.mark.parametrize(
    ("aggregator", "code"),
    [("resources:\n  - tenant-crd-editor.yaml\n", 0), ("resources: []\n", 0), (AGGREGATOR, 2)],
    ids=["agrees", "empty", "lists-an-absent-tenant"],
)
def test_an_empty_tree_passes_only_when_the_aggregator_agrees(
    tmp_path: Path, aggregator: str, code: int
) -> None:
    write(tmp_path, f"{TENANTS_DIR}/{gate.AGGREGATOR}", aggregator)
    write(tmp_path, f"{TENANTS_DIR}/tenant-crd-editor.yaml", "kind: ClusterRole\n")
    assert run(tmp_path) == code


@pytest.mark.parametrize(
    "body",
    [b"resources: [\n", b"- tenant-crd-editor.yaml\n", b"resources:\n  - \xff\xfe.yaml\n",
     b"resources: demo-app.yaml\n", b"resources:\n  - ../other/tenant.yaml\n"],
    ids=["unparseable", "not-a-mapping", "non-utf8", "resources-string", "outside"],
)
def test_an_unreadable_aggregator_exits_2(repo: Path, body: bytes, capsys) -> None:
    (repo / TENANTS_DIR / gate.AGGREGATOR).write_bytes(body)
    assert run(repo) == 2
    assert gate.AGGREGATOR in capsys.readouterr().err


class TestTargets:
    """The gate reads a tenants directory, found or named, or wiring files alone."""

    def test_a_single_wiring_file_is_checked_without_an_aggregator(
        self, tmp_path: Path, capsys
    ) -> None:
        write(tmp_path, "rendered/demo-app.yaml", TENANT)
        assert gate.main([*TENANT_LOCAL, str(tmp_path / "rendered" / "demo-app.yaml")]) == 0
        assert "1 tenant file(s) given" in capsys.readouterr().out
        write(tmp_path, "rendered/demo-app.yaml", drop(TENANT, "serviceAccountName"))
        assert gate.main([*TENANT_LOCAL, str(tmp_path / "rendered" / "demo-app.yaml")]) == 1
        assert "serviceAccountName" in capsys.readouterr().err

    def test_a_single_wiring_file_skips_the_cluster_tree_path_arm(
        self, tmp_path: Path
    ) -> None:
        """A tenant repo has its own kubernetes/flux, which is no cluster tree."""
        write(tmp_path, "kubernetes/flux/kustomization.yaml", "resources: []\n")
        write(tmp_path, "rendered/demo-app.yaml", TENANT)
        assert gate.main(
            [*TENANT_LOCAL, "--repo-root", str(tmp_path), "rendered/demo-app.yaml"]
        ) == 0

    def test_an_operator_error_exits_2(self, repo: Path) -> None:
        assert gate.main([*TENANT_LOCAL, "--repo-root", str(repo), "--tenants-dir", "nope"]) == 2


PLATFORM_NAMESPACE = "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: metallb-system\n"


@pytest.mark.parametrize(
    ("name", "platform_file", "expected"),
    [
        ("metallb-system", "kubernetes/infrastructure/metallb/namespace.yaml",
         "namespace metallb-system is already declared by "
         "kubernetes/infrastructure/metallb/namespace.yaml"),
        ("kube-system", None, "namespace kube-system is already declared by the apiserver"),
    ],
    ids=["platform-manifest", "built-in"],
)
def test_a_tenant_namespace_the_platform_declares_fails(
    repo: Path, name: str, platform_file: str | None, expected: str
) -> None:
    if platform_file:
        write(repo, platform_file, "---\n" + PLATFORM_NAMESPACE)
    body = TENANT.replace("name: demo-app\n  annotations", f"name: {name}\n  annotations", 1)
    problems = wire(repo, body)
    assert any(expected in p for p in problems), problems
    assert run(repo) == 1


@pytest.mark.parametrize(
    ("rel", "body"),
    [
        (f"{TENANTS_DIR}/README.yaml", PLATFORM_NAMESPACE.replace("metallb-system", "demo-app")),
        ("kubernetes/apps/broken.yaml", "key: [unclosed\n"),
        ("kubernetes/apps/lookalike.yaml",
         PLATFORM_NAMESPACE.replace("v1", "example.com/v1").replace("metallb-system", "demo-app")),
    ],
    ids=["inside-the-tenants-tree", "unparseable-platform-file", "namespace-kind-of-another-group"],
)
def test_only_a_platform_namespace_manifest_collides(repo: Path, rel: str, body: str) -> None:
    write(repo, rel, body)
    assert not any("already declared" in p for p in check(repo))


def test_an_unparseable_platform_file_is_a_finding(repo: Path) -> None:
    write(repo, "kubernetes/apps/broken.yaml", "key: [unclosed\n")
    assert ("kubernetes/apps/broken.yaml: does not parse, so a tenant could claim a "
            "namespace it declares") in check(repo)
    assert run(repo) == 1


def test_a_missing_platform_tree_exits_2(tmp_path: Path, capsys) -> None:
    write(tmp_path, f"tenants/{gate.AGGREGATOR}", AGGREGATOR)
    write(tmp_path, "tenants/tenant-crd-editor.yaml", "kind: ClusterRole\n")
    write(tmp_path, "tenants/demo-app.yaml", TENANT)
    argv = [*TENANT_LOCAL, "--repo-root", str(tmp_path), "--tenants-dir", "tenants"]
    assert gate.main(argv) == 2
    assert "kubernetes is not a directory" in capsys.readouterr().err


def test_a_standalone_file_cannot_claim_a_built_in_namespace(repo: Path) -> None:
    path = repo / TENANTS_DIR / "demo-app.yaml"
    path.write_text(TENANT.replace("name: demo-app\n  annotations", "name: kube-system\n  annotations", 1))
    problems = gate.check_files(gate.Targets(repo, files=[path]))
    assert any("namespace kube-system is already declared by the apiserver" in p
               for p in problems), problems


BORROWED_LABEL = TENANT.replace("fluxcd.io/tenant: demo-app", "fluxcd.io/tenant: victim")


def test_a_label_cannot_borrow_another_tenants_vault(repo: Path) -> None:
    """The label is the tenant's identity, never a vault grant."""
    problems = wire(repo, BORROWED_LABEL + STORE.replace("        demo-app: 1\n", "        victim: 1\n"))
    assert len(problems) == 1, problems
    assert "reads vault(s) victim, not the tenant's own vault alone (demo-app)" in problems[0]
    assert run(repo) == 1


def test_a_label_unlike_the_namespace_still_passes(repo: Path) -> None:
    assert wire(repo, BORROWED_LABEL + STORE) == []


@pytest.mark.parametrize(
    ("role", "ref"),
    [
        ("admin", "  kind: Role\n  name: admin\n  apiGroup: rbac.authorization.k8s.io\n"),
        ("admin", "  kind: ClusterRole\n  name: admin\n  apiGroup: example.com\n"),
        ("admin", "  kind: ClusterRole\n  name: admin\n"),
        ("tenant-crd-editor",
         "  kind: Role\n  name: tenant-crd-editor\n  apiGroup: rbac.authorization.k8s.io\n"),
    ],
    ids=["admin-role", "admin-other-group", "admin-no-group", "crd-editor-role"],
)
def test_a_binding_to_anything_but_the_rbac_cluster_role_fails(
    repo: Path, role: str, ref: str
) -> None:
    expected = f"  kind: ClusterRole\n  name: {role}\n  apiGroup: rbac.authorization.k8s.io\n"
    assert TENANT.count(expected) == 1
    problems = wire(repo, TENANT.replace(expected, ref))
    assert problems == [
        f"{TENANTS_DIR}/demo-app.yaml [demo-app]: the {role} RoleBinding for flux-system/demo-app-flux "
        f"does not reference ClusterRole {role} in rbac.authorization.k8s.io — a "
        "tenant-local Role of that name grants whatever the tenant writes into it"
    ], problems


OTHER_TENANT = TENANT.replace("demo-app", "other-app")


def _second_tenant(repo: Path, body: str) -> None:
    write(repo, f"{TENANTS_DIR}/other-app.yaml", body)
    write(repo, f"{TENANTS_DIR}/{gate.AGGREGATOR}", AGGREGATOR + "  - other-app.yaml\n")


def test_two_tenant_files_with_distinct_namespaces_pass(repo: Path) -> None:
    _second_tenant(repo, OTHER_TENANT)
    assert check(repo) == []


def test_a_namespace_two_tenant_files_declare_names_both(repo: Path) -> None:
    _second_tenant(repo, OTHER_TENANT + EXTRA_NAMESPACE.replace("demo-app-jobs", "demo-app")
                   .replace("fluxcd.io/tenant: demo-app", "fluxcd.io/tenant: other-app"))
    problems = check(repo)
    assert problems == [
        f"{TENANTS_DIR}/other-app.yaml: namespace demo-app is also declared by "
        f"{TENANTS_DIR}/demo-app.yaml — both tenants' service accounts would hold admin in it"
    ], problems
    assert run(repo) == 1


def test_standalone_files_sharing_a_namespace_fail(repo: Path) -> None:
    first, second = repo / "first.yaml", repo / "second.yaml"
    first.write_text(TENANT)
    second.write_text(TENANT)
    problems = gate.check_files(gate.Targets(repo, files=[first, second]))
    assert problems == [
        "second.yaml: namespace demo-app is also declared by first.yaml — both "
        "tenants' service accounts would hold admin in it"
    ], problems


READER = textwrap.dedent(
    """\
    ---
    apiVersion: v1
    kind: ServiceAccount
    metadata:
      name: demo-app-reader
      namespace: demo-app
    ---
    apiVersion: rbac.authorization.k8s.io/v1
    kind: Role
    metadata:
      name: demo-app-reader
      namespace: flux-system
    rules:
      - apiGroups: ["kustomize.toolkit.fluxcd.io"]
        resources: ["kustomizations"]
        resourceNames: ["demo-app"]
        verbs: ["get", "list", "watch"]
      - apiGroups: ["source.toolkit.fluxcd.io"]
        resources: ["gitrepositories"]
        resourceNames: ["demo-app"]
        verbs: ["get", "list", "watch"]
    ---
    apiVersion: rbac.authorization.k8s.io/v1
    kind: RoleBinding
    metadata:
      name: demo-app-reader
      namespace: flux-system
    subjects:
      - kind: ServiceAccount
        name: demo-app-reader
        namespace: demo-app
    roleRef:
      kind: Role
      name: demo-app-reader
      apiGroup: rbac.authorization.k8s.io
    """
)
ROLE_REF = "  kind: Role\n  name: demo-app-reader\n  apiGroup: rbac.authorization.k8s.io\n"
KUSTOMIZE_RULE = (
    '  - apiGroups: ["kustomize.toolkit.fluxcd.io"]\n    resources: ["kustomizations"]\n'
    '    resourceNames: ["demo-app"]\n    verbs: ["get", "list", "watch"]\n'
)
READER_ROLE = f"{TENANTS_DIR}/demo-app.yaml: Role flux-system/demo-app-reader"
READER_BINDING = f"{TENANTS_DIR}/demo-app.yaml: RoleBinding flux-system/demo-app-reader"


def test_a_read_only_flux_system_role_and_binding_pass(repo: Path) -> None:
    assert wire(repo, TENANT + READER) == []


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(READER.replace(ROLE_REF, ROLE_REF.replace("Role", "ClusterRole", 1)
                                    .replace("demo-app-reader", "cluster-admin")),
                     f"{READER_BINDING} binds ClusterRole cluster-admin, not a Role this file declares",
                     id="binding-to-cluster-admin"),
        pytest.param(READER.replace(KUSTOMIZE_RULE, '  - apiGroups: [""]\n    resources: '
                                    '["secrets"]\n    resourceNames: ["demo-app"]\n'
                                    '    verbs: ["get"]\n'),
                     f"{READER_ROLE} reaches core secrets, not this tenant's own Flux objects",
                     id="secret-reading-role"),
        pytest.param(READER.replace('verbs: ["get", "list", "watch"]', 'verbs: ["get", "patch"]', 1),
                     f"{READER_ROLE} grants patch, not read alone", id="patch-verb"),
        pytest.param(READER.replace('resourceNames: ["demo-app"]', 'resourceNames: ["other"]', 1),
                     f"{READER_ROLE} names other, which this file does not declare",
                     id="foreign-resource-name"),
        pytest.param(READER.replace('    resourceNames: ["demo-app"]\n', "", 1),
                     f"{READER_ROLE} is not one apiGroup", id="unnamed-resources"),
        pytest.param(READER.replace("  - kind: ServiceAccount\n    name: demo-app-reader\n",
                                    "  - kind: Group\n    name: demo-app-reader\n"),
                     f"{READER_BINDING} binds a subject other than a ServiceAccount this file",
                     id="group-subject"),
        pytest.param(READER + "---\napiVersion: rbac.authorization.k8s.io/v1\n"
                     "kind: ClusterRoleBinding\nmetadata:\n  name: demo-app-root\n"
                     "subjects: []\nroleRef:\n  kind: ClusterRole\n  name: cluster-admin\n"
                     "  apiGroup: rbac.authorization.k8s.io\n",
                     f"{TENANTS_DIR}/demo-app.yaml: ClusterRoleBinding demo-app-root grants "
                     "across every namespace", id="cluster-role-binding"),
    ],
)
def test_flux_system_rbac_beyond_reading_its_own_objects_fails(
    repo: Path, body: str, expected: str
) -> None:
    problems = wire(repo, TENANT + body)
    assert len(problems) == 1 and expected in problems[0], problems
    assert run(repo) == 1


CREDENTIAL_NAMESPACE = "                namespace: demo-app\n"


@pytest.mark.parametrize(
    "store",
    [STORE, SDK_STORE.replace("VAULT", "demo-app"), GITLAB_STORE],
    ids=["onepassword", "onepassword-sdk", "gitlab"],
)
def test_a_credential_ref_without_a_namespace_fails(repo: Path, store: str) -> None:
    lines = store.splitlines(keepends=True)
    kept = [line for line in lines if line.strip() != "namespace: demo-app"
            or line.startswith("        - ")]
    assert len(kept) == len(lines) - 1, "the fixture names one credential namespace"
    problems = wire(repo, TENANT + "".join(kept))
    assert len(problems) == 1, problems
    assert ("names no namespace on its credential secretRef — a cluster-scoped store "
            "must read its token from the tenant namespace explicitly") in problems[0]


def test_an_unreadable_platform_file_inspects_nothing(repo: Path, capsys) -> None:
    path = repo / "kubernetes/apps/binary.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfe\x00")
    assert run(repo) == 2
    err = capsys.readouterr().err
    assert "kubernetes/apps/binary.yaml: unreadable" in err
    assert "does not parse" not in err
