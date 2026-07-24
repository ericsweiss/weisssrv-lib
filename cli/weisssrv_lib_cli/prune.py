"""`prune` — structurally drop scaffold components a project does not need.

Each feature deletes the relevant manifest(s), removes their kustomization
entry, and cleans up any cross-references (the deployment secret env block, the
observability-scrape NetworkPolicy). Idempotent: pruning an already-pruned
feature is a no-op.
"""
from __future__ import annotations

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
    public ones); this prune then drops only the public documents.
    """
    def is_public(doc: dict) -> bool:
        if doc.get("kind") not in ("IngressRoute", "Certificate"):
            return False
        return not tree._doc_name(doc).endswith("-internal")

    for fname in ("ingressroute.yaml", "certificate.yaml"):
        path = tree.flux_file(root, fname)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        new, removed = tree.remove_documents(text, is_public)
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
    """Apply each named prune feature. Returns the files changed/removed."""
    changed: list[Path] = []
    for feat in features:
        if feat.startswith("manifest:"):
            name = feat.split(":", 1)[1]
            if not name.endswith(".yaml"):
                name += ".yaml"
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
