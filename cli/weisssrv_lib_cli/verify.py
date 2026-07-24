"""`verify` — sanity-check a generated project.

Checks (all offline, no cluster access):
  * no `changeme-` placeholder tokens remain anywhere;
  * every resource listed in kustomization.yaml exists on disk;
  * every non-opt-in manifest on disk is referenced by the kustomization
    (an orphaned manifest would silently never deploy);
  * optionally, `kustomize build kubernetes/flux` succeeds (skipped with a note
    if the kustomize binary is not on PATH).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import tree
from . import kustomization as kz


def _remaining_tokens(root: Path) -> list[Path]:
    """Files still containing a placeholder token.

    Checks the two EXACT tokens (`changeme-app` / `changeme-group`) rather than
    the bare `changeme-` prefix, so pedagogical doc mentions like
    `grep -rn changeme- .` (instructions, not placeholders) don't false-positive.
    """
    hits = []
    for path in tree.tracked_files(root):
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        if tree.APP_TOKEN in text or tree.GROUP_TOKEN in text:
            hits.append(path)
    return hits


def verify(root: Path, run_kustomize: bool = True) -> tuple[bool, list[str]]:
    """Return (ok, problems). ok is True when problems is empty."""
    problems: list[str] = []

    # 1. Leftover placeholder tokens.
    token_hits = _remaining_tokens(root)
    for p in token_hits:
        problems.append(f"placeholder token remains in {p.relative_to(root)}")

    fdir = tree.flux_dir(root)
    kpath = tree.flux_file(root, tree.KUSTOMIZATION)
    if not fdir.is_dir():
        problems.append(f"missing directory {tree.FLUX_DIR}")
        return (not problems, problems)
    if not kpath.exists():
        problems.append(f"missing {tree.FLUX_DIR}/{tree.KUSTOMIZATION}")
        return (not problems, problems)

    ktext = kpath.read_text(encoding="utf-8")
    resources = kz.list_resources(ktext)

    # 2. Listed resources exist on disk.
    for name in resources:
        if not (fdir / name).exists():
            problems.append(f"kustomization lists '{name}' but it is missing on disk")

    # 3. Non-opt-in manifests on disk are referenced.
    on_disk = {
        p.name
        for p in fdir.glob("*.yaml")
        if p.name != tree.KUSTOMIZATION
    }
    referenced = set(resources)
    for name in sorted(on_disk - referenced):
        if name in tree.OPT_IN_MANIFESTS:
            continue
        problems.append(f"manifest '{name}' is on disk but not referenced by the kustomization")

    # 4. Optional kustomize build.
    if run_kustomize:
        if shutil.which("kustomize"):
            res = subprocess.run(
                ["kustomize", "build", str(fdir)],
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                problems.append(f"kustomize build failed:\n{res.stderr.strip()}")
        else:
            problems.append("NOTE: kustomize not on PATH — skipped the build check")

    # The kustomize-not-found note is advisory, not a failure.
    hard = [p for p in problems if not p.startswith("NOTE:")]
    return (not hard, problems)
