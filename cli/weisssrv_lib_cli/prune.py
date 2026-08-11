"""`prune` — structurally drop scaffold components a project does not need.

Each feature deletes the relevant manifest(s), removes their kustomization
entry, and cleans up any cross-references (the deployment secret env block, the
observability-scrape NetworkPolicy). Idempotent: pruning an already-pruned
feature is a no-op.

Two prefixed selectors take an argument instead of naming a fixed feature:
`manifest:<file>` (any kubernetes/flux manifest) and `ci:<shape>` (keep one of
the template's three CI shapes, delete the others' files). Both sanitise their
argument up front — by containment and by allowlist respectively — so a crafted
value can never reach a path the caller did not intend.
"""
from __future__ import annotations

import os
import shutil
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

# The ONLY paths `ci:<shape>` may delete, keyed by the shape that is KEPT.
#
# SECURITY: these paths live outside kubernetes/flux/, so `_safe_manifest_name`
# cannot guard them. Instead the shape name is never joined into a path — it is
# an exact dict key only, and the deleted paths are fixed `tree` constants, so a
# crafted shape misses the mapping and `_safe_ci_shape` refuses it.
_CI_SHAPE_DROPS = {
    "gitlab": (tree.GITHUB_WORKFLOWS,),
    "github": (tree.GITLAB_CI, *tree.GITLAB_CI_EXTRA),
    "none": (tree.GITLAB_CI, *tree.GITLAB_CI_EXTRA, tree.GITHUB_WORKFLOWS),
}
CI_SHAPES = tuple(_CI_SHAPE_DROPS)


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


def _internal_ingress_active(root: Path) -> bool:
    """Whether the internal route AND its certificate are both really deploying.

    The internal variants are opt-in manifests of their own now, so "active"
    means their `optional/…` resource line is uncommented — `wire
    internal-ingress` is what does that.

    EVERY manifest must be active AND present on disk, not just one: this gates
    the destructive `prune external-ingress`, and a half-wired tree (only the
    route enabled, or a line enabled for a file someone deleted) would otherwise
    authorise deleting the public route + cert and leave the workload with no
    usable TLS route at all.
    """
    kpath = tree.flux_file(root, tree.KUSTOMIZATION)
    if not kpath.exists():
        return False
    text = kpath.read_text(encoding="utf-8")
    return all(
        kz.has_resource(text, resource)
        and tree.flux_file(root, resource).is_file()
        for resource in tree.INTERNAL_INGRESS_MANIFESTS
    )


def _external_ingress_would_empty(root: Path) -> list[str]:
    """Files `prune external-ingress` would leave with no active document, on a
    tree where that outcome has nothing to replace it.

    The public IngressRoute/Certificate are the only documents in these two
    files, so pruning always empties them. That is fine once `wire
    internal-ingress` has enabled the internal variants — the emptied files are
    then deleted outright — and a dead end before that: the tenant would be left
    with no route at all. Returns the offending filenames (empty = safe).
    """
    if _internal_ingress_active(root):
        return []
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
    `..`, and any embedded path separator — a plain filename is the only
    legitimate form, and the opt-in manifests one level down in `optional/` are
    reached through their own named features, not through this selector — then
    resolve the final path and assert containment as a belt-and-suspenders guard.
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


def _safe_ci_shape(raw: str) -> str:
    """Validate a `ci:<shape>` argument against the fixed allowlist.

    The returned value is a KEY of `_CI_SHAPE_DROPS`, never a path: the caller
    looks the key up to get the hardcoded `tree` constants it deletes. Because
    the shape is never concatenated onto `root`, there is no path to traverse —
    an unknown value has nothing to select and is refused here, up front.
    """
    if raw not in _CI_SHAPE_DROPS:
        raise PruneError(
            f"unknown CI shape '{raw}' (known: {', '.join(CI_SHAPES)}) — "
            "e.g. ci:gitlab keeps .gitlab-ci.yml, ci:github keeps "
            ".github/workflows/, ci:none keeps neither"
        )
    return raw


def _validate_features(root: Path, features: list[str]) -> None:
    """Reject the whole request BEFORE mutating anything, so a bad feature name
    (or an external-ingress prune that would empty a file) never leaves a
    half-mutated repo."""
    # At most one CI shape per invocation: the features are applied in
    # sequence, so two shapes would drop each other's files and leave no CI at
    # all. Repeats of the SAME shape are idempotent and allowed.
    shapes = {
        _safe_ci_shape(f.split(":", 1)[1]) for f in features if f.startswith("ci:")
    }
    if len(shapes) > 1:
        raise PruneError(
            "conflicting CI shapes requested ("
            + ", ".join(sorted(shapes))
            + "); a project keeps exactly one"
        )
    for feat in features:
        if feat.startswith("manifest:"):
            # Validates the file name (incl. path-traversal rejection); raises
            # PruneError up front so a bad `manifest:` never deletes anything.
            _safe_manifest_name(root, feat.split(":", 1)[1])
            continue
        if feat.startswith("ci:"):
            # Allowlist membership (see _safe_ci_shape) AND a symlink preflight,
            # so a refusal happens before anything is deleted rather than
            # halfway through the shape.
            shape = _safe_ci_shape(feat.split(":", 1)[1])
            for rel in _CI_SHAPE_DROPS[shape]:
                _safe_ci_target(root, rel)
            # The shape being KEPT must exist before the others are deleted.
            # Selecting gitlab in a tree whose .gitlab-ci.yml is already gone
            # would delete .github/workflows/ and report success, leaving the
            # project with no pipeline at all — the destructive outcome, from a
            # request whose intent was to keep one.
            missing = _ci_shape_missing(root, shape)
            if missing:
                raise PruneError(
                    f"ci:{shape} keeps a shape this tree does not have — "
                    + ", ".join(missing)
                    + ". Deleting the other shape would leave no pipeline; "
                    "restore the files, or select the shape you actually have"
                )
            continue
        if feat not in FEATURES:
            raise PruneError(
                f"unknown prune feature '{feat}' (known: {', '.join(FEATURES)}, "
                f"manifest:<file>, or ci:<{'|'.join(CI_SHAPES)}>)"
            )
    if "external-ingress" in features:
        offenders = _external_ingress_would_empty(root)
        if offenders:
            raise PruneError(
                "prune external-ingress would empty "
                + " and ".join(offenders)
                + " (the internal route and certificate are not BOTH active and "
                "present) while it stays listed in the kustomization. Run `wire "
                "internal-ingress` first, then prune — or use `prune "
                "manifest:<file>` to delete the file and its kustomization entry."
            )


def _ci_shape_missing(root: Path, shape: str) -> list[str]:
    """What the named shape needs but this tree lacks. Empty means keepable.

    `none` keeps nothing, so it can never be unsatisfiable.
    """
    # Both the DROP and the KEPT paths go through _safe_ci_target, so a
    # symlinked ancestor cannot resolve outside the repo. A leaf symlink is
    # rejected too: git tracks the link, not a runnable file at that path.
    if shape == "gitlab":
        missing = []
        for rel in (tree.GITLAB_CI, *tree.GITLAB_CI_EXTRA):
            path = _safe_ci_target(root, rel)
            if path.is_symlink() or not path.is_file():
                missing.append(rel)
        return missing
    if shape == "github":
        workflows = _safe_ci_target(root, tree.GITHUB_WORKFLOWS)
        runnable = (
            workflows.is_dir()
            and not workflows.is_symlink()
            and any(
                p.is_file() and not p.is_symlink() and p.suffix in (".yml", ".yaml")
                for p in workflows.iterdir()
            )
        )
        # Same predicate verify uses: GitHub runs regular .yml/.yaml only.
        return [] if runnable else [f"{tree.GITHUB_WORKFLOWS}/ (no runnable workflow)"]
    return []


def _safe_ci_target(root: Path, rel: str) -> Path:
    """Resolve a CI drop target, refusing to traverse a symlinked ancestor.

    The names in _CI_SHAPE_DROPS are hardcoded, so the LEAF is safe and the
    delete path already unlinks a symlinked leaf rather than following it. An
    ANCESTOR is the hole: with `.github` a symlink, `root / ".github/workflows"`
    resolves outside the project and rmtree() would delete whatever is there.
    """
    ancestor = root
    for part in Path(rel).parts[:-1]:
        ancestor /= part
        if ancestor.is_symlink():
            raise PruneError(
                f"ci: refusing to traverse symlinked directory '{ancestor}' — "
                "delete the CI files by hand, or replace the symlink with a "
                "real directory"
            )
    return root / rel


def _delete_manifest(root: Path, name: str, changed: list[Path]) -> None:
    """Delete kubernetes/flux/<name> and drop it from the kustomization."""
    path = tree.flux_file(root, name)
    if path.exists():
        path.unlink()
        changed.append(path)
    _drop_resource(root, name, changed)


def _delete_optional_manifest(root: Path, resource: str, changed: list[Path]) -> None:
    """Delete an opt-in manifest (a FLUX_DIR-relative `optional/<file>` path)
    and every reference that would outlive it.

    Three places name it: the file itself, the COMMENTED enable line in the live
    kustomization (an active one too, if the feature was wired), and
    optional/kustomization.yaml — which CI builds, so a stale entry there fails
    the lint job for a file the tenant deliberately removed.
    """
    fname = resource.rsplit("/", 1)[-1]
    path = tree.flux_file(root, resource)
    if path.exists():
        path.unlink()
        changed.append(path)
    _drop_resource(root, resource, changed, drop_commented=True)

    okpath = tree.optional_dir(root) / tree.KUSTOMIZATION
    if not okpath.is_file():
        return
    new, did = kz.remove_resource(okpath.read_text(encoding="utf-8"), fname)
    if did:
        okpath.write_text(new, encoding="utf-8")
        if okpath not in changed:
            changed.append(okpath)


def _drop_resource(
    root: Path, name: str, changed: list[Path], drop_commented: bool = False
) -> None:
    kpath = tree.flux_file(root, tree.KUSTOMIZATION)
    if not kpath.exists():
        return
    text = kpath.read_text(encoding="utf-8")
    new, did = kz.remove_resource(text, name)
    if drop_commented:
        new, did_comment = kz.remove_commented_resource(new, name)
        did = did or did_comment
    if did:
        kpath.write_text(new, encoding="utf-8")
        if kpath not in changed:
            changed.append(kpath)


def _externalsecret_target_name(root: Path) -> str | None:
    """The target Secret name the scaffold's ExternalSecret produces.

    Reads `spec.target.name` from the (active) ExternalSecret document, falling
    back to `metadata.name` (ESO's default when `target.name` is omitted).
    Returns None when the manifest is absent or unparseable — the caller then
    prunes conservatively (any secretKeyRef env, never plain-value env).
    """
    es = tree.flux_file(root, "externalsecret.yaml")
    if not es.exists():
        return None
    try:
        docs = tree._safe_load_all(es.read_text(encoding="utf-8"))
    except tree.YAMLError:
        return None
    for doc in docs:
        if isinstance(doc, dict) and doc.get("kind") == "ExternalSecret":
            spec = doc.get("spec") or {}
            target = spec.get("target") or {}
            name = target.get("name") or (doc.get("metadata") or {}).get("name")
            return name or None
    return None


def _is_secret_env(entry, secret_name: str | None) -> bool:
    """Whether a deployment env entry references the pruned Secret.

    When `secret_name` is known, only entries whose `valueFrom.secretKeyRef.name`
    equals it are pruned. When it is None (target name unresolvable), any entry
    carrying a `secretKeyRef` is pruned. Plain-value env vars (and configMapKeyRef
    / fieldRef entries) are never touched.
    """
    if not isinstance(entry, dict):
        return False
    value_from = entry.get("valueFrom")
    if not isinstance(value_from, dict):
        return False
    skr = value_from.get("secretKeyRef")
    if not isinstance(skr, dict):
        return False
    if secret_name is None:
        return True
    return skr.get("name") == secret_name


def _prune_secrets(root: Path, changed: list[Path]) -> None:
    # Resolve the target Secret name BEFORE deleting the manifest, so we remove
    # only the deployment env entries bound to THIS secret and leave any
    # user-added env vars (plain values or other secretKeyRefs) intact.
    secret_name = _externalsecret_target_name(root)
    _delete_manifest(root, "externalsecret.yaml", changed)
    dep = tree.flux_file(root, tree.DEPLOYMENT)
    if not dep.exists():
        return
    data = tree.load_yaml(dep)
    try:
        container = data["spec"]["template"]["spec"]["containers"][0]
    except (KeyError, IndexError, TypeError):
        container = None
    if container is None:
        # Could not locate the workload container; flag the orphaned env ref
        # rather than reporting silent success.
        print(
            f"warning: could not locate the container env block in "
            f"{tree.DEPLOYMENT}; review it for a stale secret env reference",
            file=sys.stderr,
        )
        return
    env = container.get("env")
    if not isinstance(env, list):
        return
    # Remove matching entries in place (reverse index) so ruamel keeps the
    # comments/formatting of the surviving env vars.
    removed_any = False
    for i in range(len(env) - 1, -1, -1):
        if _is_secret_env(env[i], secret_name):
            del env[i]
            removed_any = True
    if not removed_any:
        return
    if len(env) == 0:
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
    variants (whose metadata.name ends in `-internal`) to serve the workload.

    Run `wire internal-ingress` FIRST — it enables the optional/ internal
    manifests, and `prune` refuses up front (see `_validate_features`) until it
    has. A file left with no active document is DELETED, kustomization entry
    included: the scaffold's public route and cert are one document each, and an
    empty file that stays listed fails `kustomize build`. A file that still has
    content (a tenant who kept both variants in one file) is rewritten instead.
    """
    for fname in ("ingressroute.yaml", "certificate.yaml"):
        path = tree.flux_file(root, fname)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        new, removed = tree.remove_documents(text, _is_public_ingress)
        if not removed:
            continue
        if _has_active_content(new):
            path.write_text(new, encoding="utf-8")
            if path not in changed:
                changed.append(path)
        else:
            _delete_manifest(root, fname, changed)


def _prune_ci(root: Path, shape: str, changed: list[Path]) -> None:
    """Keep one CI shape, delete the others' files (docs/CI-SHAPES.md).

    Mirrors the template's scripts/select-ci.sh: drop the losing shapes' paths,
    then remove `.github` / `.gitlab` if — and only if — the drop left them
    empty. Nothing under kubernetes/flux/ is touched; the manifests are
    CI-agnostic because Flux deploys the tenant in all three shapes.

    `shape` MUST already have passed `_safe_ci_shape` — it is used solely as a
    lookup key, so every path deleted here is a hardcoded `tree` constant.
    """
    for rel in _CI_SHAPE_DROPS[shape]:
        # Re-checked here, not just in _validate_features: this is the call that
        # deletes, and it must not depend on a caller having preflighted.
        target = _safe_ci_target(root, rel)
        # A symlink is unlinked, never followed: rmtree() refuses symlinks
        # anyway, and following one would delete whatever it points at.
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
        else:
            continue  # already applied
        changed.append(target)
    for parent in tree.CI_PARENT_DIRS:
        path = root / parent
        if path.is_dir() and not path.is_symlink() and not any(path.iterdir()):
            path.rmdir()
            changed.append(path)


def _prune_image_build(root: Path, changed: list[Path]) -> None:
    for fname in ("Dockerfile", ".dockerignore"):
        path = root / fname
        if path.exists():
            path.unlink()
            changed.append(path)


def validate(root: Path, features: list[str]) -> None:
    """Raise PruneError if `prune(root, features)` would refuse.

    Public so a caller that mutates the tree BEFORE pruning (cli `rename --ci`)
    can fail before its first write instead of leaving the tree half-applied.
    """
    _validate_features(root, features)


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
        if feat.startswith("ci:"):
            # Re-validate (defence in depth): only an allowlisted shape name can
            # reach _prune_ci, which deletes fixed paths and nothing else.
            _prune_ci(root, _safe_ci_shape(feat.split(":", 1)[1]), changed)
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
            _delete_optional_manifest(root, tree.HPA_MANIFEST, changed)
        elif feat == "external-ingress":
            _prune_external_ingress(root, changed)
        elif feat == "image-build":
            _prune_image_build(root, changed)
        else:
            raise PruneError(
                f"unknown prune feature '{feat}' (known: {', '.join(FEATURES)}, "
                f"manifest:<file>, or ci:<{'|'.join(CI_SHAPES)}>)"
            )
    return changed
