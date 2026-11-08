#!/usr/bin/env python3
"""Keep a CRD-owning HelmRelease from deleting its CRDs on uninstall.

Reads the rendered corpus on stdin with consumer data from --policy-config;
exits 0 clean, 1 on a violation, 2 on an error. Contract: docs/SCRIPTS.md.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    from gate_common import doc_key, load_corpus  # noqa: E402
except ImportError:
    print(
        "ERROR: gate_common.py must be vendored beside this gate "
        "(see scripts/vendorable-paths.yml)", file=sys.stderr,
    )
    raise SystemExit(2) from None

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(2) from None

HELM_RELEASE = "HelmRelease"
KEEP_ANNOTATION = "helm.sh/resource-policy"
KEEP_VALUE = "keep"
RETRY_STRATEGY = "RetryOnFailure"


def _values_keep_crds(doc: dict) -> bool:
    """`spec.values.crds.annotations` — the key a chart renders onto its CRDs."""
    values = (doc.get("spec") or {}).get("values") or {}
    annotations = (values.get("crds") or {}).get("annotations") or {}
    return annotations.get(KEEP_ANNOTATION) == KEEP_VALUE


def _post_renderer_keeps_crds(doc: dict) -> bool:
    """A kustomize patch whose target is the CRDs, not some other kind."""
    for renderer in (doc.get("spec") or {}).get("postRenderers") or []:
        for entry in (renderer.get("kustomize") or {}).get("patches") or []:
            if (entry.get("target") or {}).get("kind") != "CustomResourceDefinition":
                continue
            try:
                patch = yaml.safe_load(entry.get("patch") or "")
            except yaml.YAMLError:
                continue
            if not isinstance(patch, dict):
                continue
            annotations = ((patch.get("metadata") or {}).get("annotations") or {})
            if annotations.get(KEEP_ANNOTATION) == KEEP_VALUE:
                return True
    return False


def _keep_flag_keeps_crds(doc: dict) -> bool:
    """`spec.values.crds.keep` — the boolean some charts turn into the annotation."""
    values = (doc.get("spec") or {}).get("values") or {}
    return (values.get("crds") or {}).get("keep") is True


# A shape names the path the chart actually reads, so a values key the chart
# ignores fails instead of passing on the string alone. The `keep` shape also
# survives an uninstall by itself, so it needs no retry strategy.
KEEP_SHAPES = {
    "values": _values_keep_crds,
    "postRenderer": _post_renderer_keeps_crds,
    "keep": _keep_flag_keeps_crds,
}
SHAPES_NEEDING_RETRY = {"values", "postRenderer"}


def install_strategy(doc: dict) -> str | None:
    """`spec.install.strategy.name` — the only path the HelmRelease CRD defines."""
    strategy = ((doc.get("spec") or {}).get("install") or {}).get("strategy")
    return strategy.get("name") if isinstance(strategy, dict) else None


def extra_args(doc: dict) -> list[str]:
    """`spec.values.extraArgs`, the list most controller charts append to argv."""
    args = ((doc.get("spec") or {}).get("values") or {}).get("extraArgs")
    return [str(a) for a in args] if isinstance(args, list) else []


class Policy:
    """Consumer data from --policy-config. A value, not module state."""

    def __init__(self, crd_keepers=None, no_crds=None, required_extra_args=None) -> None:
        # "namespace/HelmRelease/name" -> the KEEP_SHAPES key that keeps its CRDs.
        self.crd_keepers: dict[str, str] = dict(crd_keepers or {})
        # "namespace/HelmRelease/name" -> why an uninstall takes no CR with it.
        self.no_crds: dict[str, str] = dict(no_crds or {})
        # "namespace/HelmRelease/name" -> argv entries its values must carry.
        self.required_extra_args: dict[str, list[str]] = dict(required_extra_args or {})


def load_policy(path) -> Policy:
    """Read a --policy-config file. Every entry carries a shape or a reason."""
    with open(path) as handle:
        doc = yaml.safe_load(handle) or {}
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: top-level must be a mapping")
    keepers = doc.get("crd_keepers") or {}
    no_crds = doc.get("no_crds") or {}
    required = doc.get("required_extra_args") or {}
    for name, value in (("crd_keepers", keepers), ("no_crds", no_crds),
                        ("required_extra_args", required)):
        if not isinstance(value, dict):
            raise ValueError(f'{path}: {name} must be a mapping of "namespace/Kind/name": …')
    for key, shape in keepers.items():
        if shape not in KEEP_SHAPES:
            raise ValueError(
                f"{path}: crd_keepers[{key}] shape {shape!r} is not one of "
                f"{sorted(KEEP_SHAPES)}"
            )
    for key, reason in no_crds.items():
        if not str(reason or "").strip():
            raise ValueError(f"{path}: no_crds[{key}] has no reason")
    overlap = sorted(set(keepers) & set(no_crds))
    if overlap:
        raise ValueError(f"{path}: declared in both crd_keepers and no_crds: {overlap}")
    for key, args in required.items():
        if not isinstance(args, list) or not args:
            raise ValueError(f"{path}: required_extra_args[{key}] must be a non-empty list")
    if not keepers and not no_crds:
        raise ValueError(f"{path}: declares no HelmRelease — the gate would check nothing")
    return Policy(keepers, no_crds, {k: [str(a) for a in v] for k, v in required.items()})


def violations(docs: list[dict], policy: Policy) -> list[str]:
    """Every release whose CRD safety, or required argv, is not what it declares."""
    releases = {doc_key(d): d for d in docs if d.get("kind") == HELM_RELEASE}
    found: list[str] = []

    declared = set(policy.crd_keepers) | set(policy.no_crds) | set(policy.required_extra_args)
    for key in sorted(declared - set(releases)):
        found.append(
            f"  {key}: declared in --policy-config but absent from the corpus — the "
            f"guard checked nothing; correct the key or drop the entry"
        )
    for key in sorted(set(releases) - set(policy.crd_keepers) - set(policy.no_crds)):
        found.append(
            f"  {key}: in neither crd_keepers nor no_crds — add it with the shape that "
            f"keeps its CRDs, or with the reason an uninstall takes no CR with it"
        )

    for key, shape in sorted(policy.crd_keepers.items()):
        doc = releases.get(key)
        if doc is None:
            continue
        if not KEEP_SHAPES[shape](doc):
            found.append(
                f"  {key}: no {KEEP_ANNOTATION}: {KEEP_VALUE} on the CRDs its chart owns, "
                f"at the {shape} path the chart reads — the next uninstall or chart-version "
                f"change takes every CR with them"
            )
        if shape in SHAPES_NEEDING_RETRY and install_strategy(doc) != RETRY_STRATEGY:
            found.append(
                f"  {key}: spec.install.strategy.name is not {RETRY_STRATEGY} — install "
                f"remediation uninstalls the release, which deletes the chart's CRDs and "
                f"every CR"
            )

    for key, required in sorted(policy.required_extra_args.items()):
        doc = releases.get(key)
        if doc is None:
            continue
        present = extra_args(doc)
        for arg in required:
            if arg not in present:
                found.append(
                    f"  {key}: spec.values.extraArgs is missing {arg!r} — the controller "
                    f"falls back to its own default, which the manifests do not match"
                )
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HelmRelease CRD-safety gate.")
    parser.add_argument(
        "--policy-config",
        required=True,
        help="YAML declaring crd_keepers, no_crds and required_extra_args",
    )
    args = parser.parse_args(argv)
    try:
        policy = load_policy(args.policy_config)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"ERROR: --policy-config {args.policy_config}: {exc}", file=sys.stderr)
        return 2

    docs = load_corpus()
    if not any(d.get("kind") == HELM_RELEASE for d in docs):
        print(
            "ERROR: no HelmRelease in the corpus — check the `kustomize build` paths "
            "feeding this gate", file=sys.stderr,
        )
        return 2

    found = violations(docs, policy)
    if found:
        print("HelmRelease CRD safety violated:", file=sys.stderr)
        print("\n".join(found), file=sys.stderr)
        return 1
    print(
        f"CRD safety OK ({len(policy.crd_keepers)} keeper(s), "
        f"{len(policy.no_crds)} release(s) owning no CRD)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
