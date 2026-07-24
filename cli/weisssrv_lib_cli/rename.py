"""`rename` — replace the scaffold's placeholder tokens.

Supersedes scripts/rename.sh: validates the app slug (a DNS label — it becomes
the namespace and Flux Kustomization name) and the GitLab group (a namespace
path, possibly nested), then substitutes `changeme-app` / `changeme-group`
across every tracked text file.
"""
from __future__ import annotations

from pathlib import Path

from . import tree


class RenameError(ValueError):
    """Raised for an invalid slug or group."""


def rename(root: Path, app: str, group: str) -> list[Path]:
    """Substitute the placeholder tokens under `root`.

    Returns the list of files changed. Raises RenameError on invalid inputs.
    """
    if not tree.valid_slug(app):
        raise RenameError(
            f"app slug '{app}' must be a valid DNS label "
            "(lowercase letters, digits, hyphens)"
        )
    if not tree.valid_group(group):
        raise RenameError(
            f"group '{group}' must be a GitLab namespace path "
            "(lowercase alphanumerics, '.', '_', '-', '/'-separated)"
        )

    changed: list[Path] = []
    for path in tree.tracked_files(root):
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        if tree.APP_TOKEN not in text and tree.GROUP_TOKEN not in text:
            continue
        # Replace the group token first so an app slug that happens to contain
        # the group token can't be re-substituted.
        new = text.replace(tree.GROUP_TOKEN, group).replace(tree.APP_TOKEN, app)
        if new != text:
            path.write_text(new, encoding="utf-8", errors="surrogateescape")
            changed.append(path)
    return changed
