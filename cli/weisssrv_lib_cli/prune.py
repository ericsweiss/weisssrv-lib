"""`prune` — structurally drop scaffold components a project does not need.

Each feature deletes the relevant manifest(s), removes their kustomization
entry, and cleans up any cross-references (the deployment secret env block, the
observability-scrape NetworkPolicy). Idempotent: pruning an already-pruned
feature is a no-op.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from . import tree
from . import kustomization as kz

FEATURES = (
    "secrets",
    "metrics",
    "pdb",
    "single-replica",
    "hpa",
    "external-ingress",
    "image-build",
)


class PruneError(ValueError):
    pass


def _is_public_ingress(doc: dict) -> bool:
    """An IngressRoute/Certificate whose name is NOT an `-internal` variant."""
    if doc.get("kind") not in ("IngressRoute", "Certificate"):
        return False
    return not tree._doc_name(doc).endswith("-internal")


def _has_active_content(text: str) -> bool:
    """True if any line is an actual YAML node (not blank / `---` / a comment)."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped == "---" or stripped.startswith("#"):
            continue
        return True
    return False


def _external_ingress_would_empty(root: Path) -> list[str]:
    """Files `prune external-ingress` would truncate to no active document.

    Happens on an un-wired scaffold: the only active documents are the public
    IngressRoute/Certificate, and the internal variants are still commented out,
    so dropping the public ones leaves an empty file that is still referenced by
    the kustomization. Returns the offending filenames (empty = safe).
    """
    offenders: list[str] = []
    for fname in ("ingressroute.yaml", "certificate.yaml"):
        path = tree.flux_file(root, fname)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        new, removed = tree.remove_documents(text, _is_public_ingress)
        if removed and not _has_active_content(new):
            offenders.append(fname)
    return offenders


def _safe_manifest_name(root: Path, raw: str) -> str:
    """Validate a `manifest:<file>` argument and return its `.yaml` filename.

    The `manifest:` feature deletes `kubernetes/flux/<file>`, so an untrusted
    `<file>` must never be able to escape that directory. Reject absolute paths,
    `..`, and any embedded path separator (the Flux dir is flat — a plain
    filename is the only legitimate form), then resolve the final path and
    assert containment as a belt-and-suspenders guard.
    """
    if not raw:
        raise PruneError(
            "manifest: requires a file name (e.g. manifest:servicemonitor)"
        )
    if os.path.isabs(raw):
        raise PruneError(
            f"manifest: file name must be a plain filename, not an absolute "
            f"path: '{raw}'"
        )
    seps = {"/", os.sep}
    if os.altsep:
        seps.add(os.altsep)
    if raw in (".", "..") or any(sep in raw for sep in seps):
        raise PruneError(
            f"manifest: file name must be a plain filename with no path "
            f"separators or '..': '{raw}'"
        )
    fname = raw if raw.endswith(".yaml") else raw + ".yaml"
    flux_real = os.path.realpath(tree.flux_dir(root))
    target_real = os.path.realpath(tree.flux_file(root, fname))
    if os.path.commonpath([flux_real, target_real]) != flux_real:
        raise PruneError(
            f"manifest: refusing to operate outside {tree.FLUX_DIR}: '{raw}'"
        )
    return fname


def _validate_features(root: Path, features: list[str]) -> None:
    """Reject the whole request BEFORE mutating anything, so a bad feature name
    (or an external-ingress prune that would empty a file) never leaves a
    half-mutated repo."""
    for feat in features:
        if feat.startswith("manifest:"):
            # Validates the file name (incl. path-traversal rejection); raises
            # PruneError up front so a bad `manifest:` never deletes anything.
            _safe_manifest_name(root, feat.split(":", 1)[1])
            continue
        if feat not in FEATURES:
            raise PruneError(
                f"unknown prune feature '{feat}' "
                f"(known: {', '.join(FEATURES)}, or manifest:<file>)"
            )
    if "external-ingress" in features:
        offenders = _external_ingress_would_empty(root)
        if offenders:
            raise PruneError(
                "prune external-ingress would empty "
                + " and ".join(offenders)
                + " (no internal variant is active) while it stays listed in the "
                "kustomization. Run `wire internal-ingress` first, then prune — "
                "or use `prune manifest:<file>` to delete the file and its "
                "kustomization entry."
            )


def _delete_manifest(root: Path, name: str, changed: list[Path]) -> None:
    """Delete kubernetes/flux/<name> and drop it from the kustomization."""
    path = tree.flux_file(root, name)
    if path.exists():
        path.unlink()
        changed.append(path)
    _drop_resource(root, name, changed)


def _drop_resource(root: Path, name: str, changed: list[Path]) -> None:
    kpath = tree.flux_file(root, tree.KUSTOMIZATION)
    if not kpath.exists():
        return
    text = kpath.read_text(encoding="utf-8")
    new, did = kz.remove_resource(text, name)
    if did:
        kpath.write_text(new, encoding="utf-8")
        if kpath not in changed:
            changed.append(kpath)


def _prune_secrets(root: Path, changed: list[Path]) -> None:
    _delete_manifest(root, "externalsecret.yaml", changed)
    # Drop the secret env block from the first container.
    dep = tree.flux_file(root, tree.DEPLOYMENT)
    if dep.exists():
        data = tree.load_yaml(dep)
        try:
            container = data["spec"]["template"]["spec"]["containers"][0]
        except (KeyError, IndexError, TypeError):
            container = None
        if container is not None and "env" in container:
            del container["env"]
            tree.dump_yaml(data, dep)
            if dep not in changed:
                changed.append(dep)
        elif container is None:
            # externalsecret.yaml is already gone; flag the orphaned env ref
            # rather than reporting silent success.
            print(
                f"warning: could not locate the container env block in "
                f"{tree.DEPLOYMENT}; review it for a stale secret env reference",
                file=sys.stderr,
            )


def _prune_metrics(root: Path, changed: list[Path]) -> None:
    _delete_manifest(root, "servicemonitor.yaml", changed)
    # Remove the observability-scrape ingress policy (paired with metrics).
    npath = tree.flux_file(root, "networkpolicy.yaml")
    if npath.exists():
        text = npath.read_text(encoding="utf-8")
        new, removed = tree.remove_document_by_name(text, "allow-scrape-from-observability")
        if removed:
            npath.write_text(new, encoding="utf-8")
            if npath not in changed:
                changed.append(npath)


def _set_replicas(root: Path, count: int, changed: list[Path]) -> None:
    dep = tree.flux_file(root, tree.DEPLOYMENT)
    if not dep.exists():
        return
    data = tree.load_yaml(dep)
    if isinstance(data.get("spec"), dict) and data["spec"].get("replicas") != count:
        data["spec"]["replicas"] = count
        tree.dump_yaml(data, dep)
        if dep not in changed:
            changed.append(dep)


def _prune_external_ingress(root: Path, changed: list[Path]) -> None:
    """Remove the public IngressRoute + Certificate, leaving the internal
    variants (whose metadata.name ends in `-internal`).

    For a clean internal-only result, run `wire internal-ingress` FIRST so the
    internal variants are active documents (a real `---` separates them from the
    public ones); this prune then drops only the public documents. `prune`
    refuses up front (see `_validate_features`) if this would empty a file.
    """
    for fname in ("ingressroute.yaml", "certificate.yaml"):
        path = tree.flux_file(root, fname)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        new, removed = tree.remove_documents(text, _is_public_ingress)
        if removed:
            path.write_text(new, encoding="utf-8")
            if path not in changed:
                changed.append(path)


def _prune_image_build(root: Path, changed: list[Path]) -> None:
    for fname in ("Dockerfile", ".dockerignore"):
        path = root / fname
        if path.exists():
            path.unlink()
            changed.append(path)


def prune(root: Path, features: list[str]) -> list[Path]:
    """Apply each named prune feature. Returns the files changed/removed.

    Every requested feature is validated up front (`_validate_features`); an
    invalid request raises before any file is touched.
    """
    _validate_features(root, features)
    changed: list[Path] = []
    for feat in features:
        if feat.startswith("manifest:"):
            # Re-validate (defence in depth) and normalise to a safe .yaml name.
            name = _safe_manifest_name(root, feat.split(":", 1)[1])
            _delete_manifest(root, name, changed)
            continue
        if feat == "secrets":
            _prune_secrets(root, changed)
        elif feat == "metrics":
            _prune_metrics(root, changed)
        elif feat == "pdb":
            _delete_manifest(root, "pdb.yaml", changed)
        elif feat == "single-replica":
            _delete_manifest(root, "pdb.yaml", changed)
            _set_replicas(root, 1, changed)
        elif feat == "hpa":
            _delete_manifest(root, "hpa.yaml", changed)
        elif feat == "external-ingress":
            _prune_external_ingress(root, changed)
        elif feat == "image-build":
            _prune_image_build(root, changed)
        else:
            raise PruneError(
                f"unknown prune feature '{feat}' "
                f"(known: {', '.join(FEATURES)}, or manifest:<file>)"
            )
    return changed
