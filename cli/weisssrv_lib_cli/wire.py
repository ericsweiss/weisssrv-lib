"""`wire` — enable opt-in scaffold components.

These operations uncomment blocks the template ships commented out (the HPA
resource, the internal IngressRoute/Certificate, the Authentik SSO middleware)
and make the paired data edits (drop `replicas` when the HPA owns scaling, make
the VPA memory-only). Uncommenting is line-based text surgery — the content is
comments, not data ruamel can move — while the paired data edits use ruamel
round-trip. Idempotent where practical.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import tree
from . import kustomization as kz

FEATURES = ("hpa", "internal-ingress", "sso")


class WireError(ValueError):
    pass


_COMMENT_LINE_RE = re.compile(r"^\s*#")
# Strip exactly one leading "# " (or "#") after the indentation, preserving the
# indentation AND the YAML content's own indentation (which follows the "# ").
_STRIP_RE = re.compile(r"^(\s*)#\s?(.*)$")


def _strip_comment(line: str) -> str:
    body = line.rstrip("\n")
    m = _STRIP_RE.match(body)
    if not m:
        return line
    return f"{m.group(1)}{m.group(2)}\n"


def _uncomment_block_from_marker(text: str, marker: str = "# ---") -> tuple[str, bool]:
    """Uncomment from the first line equal to `marker` (a commented doc-start)
    through end of file, stripping the leading `# ` from each comment line and
    leaving blank lines untouched. Returns (text, changed)."""
    lines = text.splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        if line.strip() == marker:
            start = i
            break
    if start is None:
        return text, False
    for j in range(start, len(lines)):
        if _COMMENT_LINE_RE.match(lines[j]):
            lines[j] = _strip_comment(lines[j])
    return "".join(lines), True


def _wire_internal_ingress(root: Path, changed: list[Path]) -> None:
    for fname in ("ingressroute.yaml", "certificate.yaml"):
        path = tree.flux_file(root, fname)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        new, did = _uncomment_block_from_marker(text)
        if did:
            path.write_text(new, encoding="utf-8")
            changed.append(path)


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
    # 1. Add hpa.yaml to the kustomization (uncomments the opt-in line).
    kpath = tree.flux_file(root, tree.KUSTOMIZATION)
    if kpath.exists():
        text = kpath.read_text(encoding="utf-8")
        new, did = kz.add_resource(text, "hpa.yaml")
        if did:
            kpath.write_text(new, encoding="utf-8")
            changed.append(kpath)
    # 2. Uncomment the HPA resource in hpa.yaml.
    hpath = tree.flux_file(root, "hpa.yaml")
    if hpath.exists():
        text = hpath.read_text(encoding="utf-8")
        new, did = _uncomment_block_from_marker(text)
        if did:
            hpath.write_text(new, encoding="utf-8")
            changed.append(hpath)
    # 3. Drop `replicas` from the deployment so Flux SSA doesn't fight the HPA.
    dep = tree.flux_file(root, tree.DEPLOYMENT)
    if dep.exists():
        data = tree.load_yaml(dep)
        if isinstance(data.get("spec"), dict) and "replicas" in data["spec"]:
            del data["spec"]["replicas"]
            tree.dump_yaml(data, dep)
            changed.append(dep)
    # 4. Make the VPA memory-only so HPA (CPU) and VPA don't fight.
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


def wire(root: Path, features: list[str]) -> list[Path]:
    """Apply each named wire feature. Returns the files changed."""
    changed: list[Path] = []
    for feat in features:
        if feat == "hpa":
            _wire_hpa(root, changed)
        elif feat == "internal-ingress":
            _wire_internal_ingress(root, changed)
        elif feat == "sso":
            _wire_sso(root, changed)
        else:
            raise WireError(
                f"unknown wire feature '{feat}' (known: {', '.join(FEATURES)})"
            )
    return changed
