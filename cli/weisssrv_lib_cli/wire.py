"""`wire` — enable opt-in scaffold components.

Enabling an opt-in MANIFEST (the HPA, the internal IngressRoute/Certificate) is
uncommenting its `# - optional/<file>` line in the live kustomization: the file
itself is already a real, schema-validated manifest under
`kubernetes/flux/optional/`. The SSO middleware is the one thing still shipped
as a commented block, inside the public route. Both are line-based text surgery
— the content is comments, not data ruamel can move — while the paired data
edits (drop `replicas` when the HPA owns scaling, make the VPA memory-only) use
ruamel round-trip. Idempotent where practical.

A missing enable line is reported, never invented: writing a resource line for a
file the tree does not have produces a kustomization that cannot build.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from . import tree
from . import kustomization as kz

FEATURES = ("hpa", "internal-ingress", "sso")


class WireError(ValueError):
    pass


# Strip exactly one leading "# " (or "#") after the indentation, preserving the
# indentation AND the YAML content's own indentation (which follows the "# ").
_STRIP_RE = re.compile(r"^(\s*)#\s?(.*)$")


def _strip_comment(line: str) -> str:
    body = line.rstrip("\n")
    m = _STRIP_RE.match(body)
    if not m:
        return line
    return f"{m.group(1)}{m.group(2)}\n"


def _enable_optional(root: Path, resource: str, changed: list[Path]) -> bool:
    """Uncomment the `# - <resource>` line in the live kustomization.

    `resource` is a FLUX_DIR-relative `optional/<file>` path. Both halves are
    checked first — the manifest on disk and the commented line — because
    `kz.uncomment_resource` can only act on a line that is already there, and a
    silent no-op would read as "already enabled". Returns whether the resource
    is active afterwards, so a caller can hold back paired edits that only make
    sense once the manifest is really deploying.
    """
    kpath = tree.flux_file(root, tree.KUSTOMIZATION)
    if not kpath.exists():
        return False
    text = kpath.read_text(encoding="utf-8")
    if kz.has_resource(text, resource):
        return True  # already enabled
    if not tree.flux_file(root, resource).exists():
        print(
            f"warning: {tree.FLUX_DIR}/{resource} is not in this tree — "
            "nothing to enable",
            file=sys.stderr,
        )
        return False
    new, did = kz.uncomment_resource(text, resource)
    if not did:
        print(
            f"warning: no `# - {resource}` line in {tree.FLUX_DIR}/"
            f"{tree.KUSTOMIZATION} — add the resource by hand",
            file=sys.stderr,
        )
        return False
    kpath.write_text(new, encoding="utf-8")
    if kpath not in changed:
        changed.append(kpath)
    return True


def _wire_internal_ingress(root: Path, changed: list[Path]) -> None:
    """Enable the internal route AND its certificate: the route serves TLS from
    the secret the certificate issues, so neither works alone."""
    for resource in tree.INTERNAL_INGRESS_MANIFESTS:
        _enable_optional(root, resource, changed)


def _wire_sso(root: Path, changed: list[Path]) -> None:
    """Uncomment the Authentik forward-auth middleware inside the public route."""
    path = tree.flux_file(root, "ingressroute.yaml")
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    name_re = re.compile(r"^(\s*)#\s*-\s*name:\s*authentik-auth\s*$")
    ns_re = re.compile(r"^(\s*)#\s*namespace:\s*authentik\s*$")
    did = False
    for i, line in enumerate(lines):
        if name_re.match(line.rstrip("\n")):
            lines[i] = _strip_comment(line)
            did = True
            # Uncomment the paired namespace line immediately following.
            if i + 1 < len(lines) and ns_re.match(lines[i + 1].rstrip("\n")):
                lines[i + 1] = _strip_comment(lines[i + 1])
    if did:
        path.write_text("".join(lines), encoding="utf-8")
        changed.append(path)


def _wire_hpa(root: Path, changed: list[Path]) -> None:
    """Enable optional/hpa.yaml and make the two paired edits its header asks
    for: drop `replicas` from the deployment (Flux server-side apply would
    otherwise fight the HPA over the replica count) and make the VPA
    memory-only (so the VPA and the HPA do not both drive CPU). Each is
    announced, because both silently change how the workload scales — and
    neither is applied unless the HPA is really enabled, or the deployment would
    be left with no replica count and nothing to set one."""
    # 1. Uncomment the opt-in line in the kustomization.
    if not _enable_optional(root, tree.HPA_MANIFEST, changed):
        return
    # 2. Drop `replicas` from the deployment so Flux SSA doesn't fight the HPA.
    dep = tree.flux_file(root, tree.DEPLOYMENT)
    if dep.exists():
        data = tree.load_yaml(dep)
        if isinstance(data.get("spec"), dict) and "replicas" in data["spec"]:
            del data["spec"]["replicas"]
            tree.dump_yaml(data, dep)
            changed.append(dep)
            print(
                f"note: removed spec.replicas from {tree.DEPLOYMENT} — the HPA "
                "owns the replica count now"
            )
    # 3. Make the VPA memory-only so HPA (CPU) and VPA don't fight.
    vpath = tree.flux_file(root, "vpa.yaml")
    if vpath.exists():
        data = tree.load_yaml(vpath)
        try:
            cps = data["spec"]["resourcePolicy"]["containerPolicies"]
        except (KeyError, TypeError):
            cps = None
        if cps:
            changed_vpa = False
            for cp in cps:
                if list(cp.get("controlledResources", [])) != ["memory"]:
                    cp["controlledResources"] = ["memory"]
                    changed_vpa = True
            if changed_vpa:
                tree.dump_yaml(data, vpath)
                changed.append(vpath)
                print(
                    "note: set vpa.yaml controlledResources to [memory] — the "
                    "HPA owns CPU now"
                )


def _validate_features(features: list[str]) -> None:
    """Reject the whole request BEFORE mutating anything, so a bad feature name
    never leaves a half-wired repo."""
    for feat in features:
        if feat not in FEATURES:
            raise WireError(
                f"unknown wire feature '{feat}' (known: {', '.join(FEATURES)})"
            )


def wire(root: Path, features: list[str]) -> list[Path]:
    """Apply each named wire feature. Returns the files changed.

    Every requested feature is validated up front; an invalid request raises
    before any file is touched.
    """
    _validate_features(features)
    changed: list[Path] = []
    for feat in features:
        if feat == "hpa":
            _wire_hpa(root, changed)
        elif feat == "internal-ingress":
            _wire_internal_ingress(root, changed)
        elif feat == "sso":
            _wire_sso(root, changed)
    return changed
