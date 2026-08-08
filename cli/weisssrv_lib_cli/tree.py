"""Shared model of the weisssrv tenant scaffold + low-level file helpers.

The CLI operates on a project generated from weisssrv-app-template: a
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

from ruamel.yaml import YAML, YAMLError

FLUX_DIR = "kubernetes/flux"
KUSTOMIZATION = "kustomization.yaml"
DEPLOYMENT = "deployment.yaml"

# Manifests present in the scaffold but NOT listed in kustomization resources by
# default (opt-in). `verify` must not flag these as orphaned.
OPT_IN_MANIFESTS = frozenset({"hpa.yaml"})

# --- CI shapes -------------------------------------------------------------
# The template ships all three CI shapes (docs/CI-SHAPES.md) and a project keeps
# exactly one; `prune ci:<shape>` drops the others. These paths are repo-root
# relative and sit OUTSIDE FLUX_DIR — see prune._CI_SHAPE_DROPS, whose fixed
# allowlist is the only thing allowed to turn a shape name into a deletion.
# Nothing under kubernetes/flux/ is CI-shape specific: Flux deploys the tenant
# in all three shapes, so the manifests are identical whichever is chosen.
GITLAB_CI = ".gitlab-ci.yml"
GITHUB_WORKFLOWS = ".github/workflows"
# GitLab CI companions that die with .gitlab-ci.yml. `.gitlab/issue_templates/`
# and `.gitlab/merge_request_templates/` are deliberately absent: they are
# GitLab HOST metadata, not CI, and stay useful on a repo that runs no pipeline.
GITLAB_CI_EXTRA = (".gitlab/secret-detection-ruleset.toml",)
# Parents a CI-shape drop can empty. Removed only when empty, so a project that
# keeps e.g. .gitlab/merge_request_templates/ keeps the directory.
CI_PARENT_DIRS = (".github", ".gitlab")

APP_TOKEN = "changeme-app"
GROUP_TOKEN = "changeme-group"

# DNS-label app slug and GitLab namespace path, matching scripts/rename.sh.
_SLUG_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
# The slug becomes a namespace / Flux Kustomization name and feeds DNS-facing
# names, so it must fit a single DNS label (RFC 1035 max 63 octets).
_SLUG_MAX_LEN = 63
_GROUP_RE = re.compile(
    r"^[a-z0-9]([a-z0-9._-]*[a-z0-9])?(/[a-z0-9]([a-z0-9._-]*[a-z0-9])?)*$"
)

# Directories never scanned/modified.
_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", ".terraform", "node_modules"}


def valid_slug(slug: str) -> bool:
    return bool(_SLUG_RE.match(slug)) and len(slug) <= _SLUG_MAX_LEN


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


def _safe_load(text: str):
    """Plain-Python load of a single YAML document (ruamel's safe loader).

    Kept on ruamel — the CLI's only runtime dependency — so no PyYAML is needed
    at runtime. Returns None for empty/comment-only input.
    """
    return YAML(typ="safe", pure=True).load(io.StringIO(text))


def _safe_load_all(text: str) -> list:
    """Plain-Python load of every document in a multi-doc YAML string."""
    return list(YAML(typ="safe", pure=True).load_all(io.StringIO(text)))


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
    names: list[str] = []
    for doc in _safe_load_all(path.read_text(encoding="utf-8")):
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
            doc = _safe_load(body)
        except YAMLError:
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
