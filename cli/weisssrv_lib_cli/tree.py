"""Shared model of the weisssrv tenant scaffold + low-level file helpers.

The CLI operates on a project generated from weisssrv-project-template: a
`kubernetes/flux/` directory of manifests plus a `kustomization.yaml` that lists
them, with placeholder tokens `changeme-app` / `changeme-group` across the
tracked tree.

Two editing strategies are used deliberately:
  * ruamel.yaml round-trip for DATA edits inside comment-heavy manifests
    (removing the deployment secret env block, dropping `replicas`, making the
    VPA memory-only) — configured (sequence=4, offset=2, explicit_start) to
    preserve the template's `---` starts, 2-space list offset, and comments.
  * line-based edits for the kustomization resource list and for UNCOMMENTING
    opt-in blocks — those touch comments, which are not data ruamel can move, so
    text surgery is both simpler and loss-free here.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

from ruamel.yaml import YAML

FLUX_DIR = "kubernetes/flux"
KUSTOMIZATION = "kustomization.yaml"
DEPLOYMENT = "deployment.yaml"

# Manifests present in the scaffold but NOT listed in kustomization resources by
# default (opt-in). `verify` must not flag these as orphaned.
OPT_IN_MANIFESTS = frozenset({"hpa.yaml"})

APP_TOKEN = "changeme-app"
GROUP_TOKEN = "changeme-group"

# DNS-label app slug and GitLab namespace path, matching scripts/rename.sh.
_SLUG_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_GROUP_RE = re.compile(
    r"^[a-z0-9]([a-z0-9._-]*[a-z0-9])?(/[a-z0-9]([a-z0-9._-]*[a-z0-9])?)*$"
)

# Directories never scanned/modified.
_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", ".terraform", "node_modules"}


def valid_slug(slug: str) -> bool:
    return bool(_SLUG_RE.match(slug))


def valid_group(group: str) -> bool:
    return bool(_GROUP_RE.match(group))


def rt_yaml() -> YAML:
    """A round-trip YAML configured to preserve the template's formatting."""
    y = YAML()
    y.preserve_quotes = True
    y.explicit_start = True
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 4096  # never fold long lines
    return y


def load_yaml(path: Path):
    y = rt_yaml()
    with path.open(encoding="utf-8") as fh:
        return y.load(fh)


def dump_yaml(data, path: Path) -> None:
    y = rt_yaml()
    buf = io.StringIO()
    y.dump(data, buf)
    path.write_text(buf.getvalue(), encoding="utf-8")


def flux_dir(root: Path) -> Path:
    return root / FLUX_DIR


def flux_file(root: Path, name: str) -> Path:
    return root / FLUX_DIR / name


def is_binary(path: Path) -> bool:
    """Cheap binary sniff (a NUL byte in the first 8 KiB), mirroring `grep -I`."""
    try:
        chunk = path.read_bytes()[:8192]
    except OSError:
        return True
    return b"\x00" in chunk


def tracked_files(root: Path) -> list[Path]:
    """Text files under `root`, skipping VCS/build dirs and binaries.

    Prefers `git ls-files` when `root` is a git work tree (matches rename.sh's
    tracked-only scope); otherwise walks the tree.
    """
    git_files = _git_tracked(root)
    if git_files is not None:
        return [p for p in git_files if p.is_file() and not is_binary(p)]
    out: list[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part in _SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        if is_binary(p):
            continue
        out.append(p)
    return out


def _git_tracked(root: Path) -> list[Path] | None:
    if not (root / ".git").exists():
        return None
    import subprocess

    try:
        res = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    names = [n for n in res.stdout.decode("utf-8", "replace").split("\0") if n]
    return [root / n for n in names]


def read_document_names(path: Path) -> list[str]:
    """metadata.name of every YAML document in a (possibly multi-doc) file."""
    import yaml

    names: list[str] = []
    with path.open(encoding="utf-8") as fh:
        for doc in yaml.safe_load_all(fh):
            if isinstance(doc, dict):
                names.append((doc.get("metadata") or {}).get("name", ""))
    return names


def remove_documents(text: str, predicate) -> tuple[str, int]:
    """Drop every `---`-delimited document for which predicate(doc) is True.

    Preserves every other document (and its comments) verbatim. Splits on lines
    that are exactly `---` so block scalars containing `---` are unaffected.
    Commented-out blocks yaml-parse to None and are always kept. Returns
    (new_text, num_removed).
    """
    import yaml

    lines = text.splitlines(keepends=True)
    chunks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip() == "---":
            if current:
                chunks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        chunks.append(current)

    kept: list[str] = []
    removed = 0
    for chunk in chunks:
        body = "".join(chunk)
        try:
            doc = yaml.safe_load(body)
        except yaml.YAMLError:
            doc = None
        if isinstance(doc, dict) and predicate(doc):
            removed += 1
            continue
        kept.append(body)
    return "".join(kept), removed


def _doc_name(doc: dict) -> str:
    return (doc.get("metadata") or {}).get("name", "")


def remove_document_by_name(text: str, name: str) -> tuple[str, int]:
    return remove_documents(text, lambda d: _doc_name(d) == name)


def remove_documents_by_kind(text: str, kind: str) -> tuple[str, int]:
    return remove_documents(text, lambda d: d.get("kind") == kind)
