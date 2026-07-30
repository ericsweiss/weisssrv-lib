"""`verify` — sanity-check a generated project.

Checks (all offline, no cluster access):
  * no `changeme-` placeholder tokens remain anywhere;
  * exactly one CI shape survives, with no leftovers from the others
    (the template ships all three — see docs/CI-SHAPES.md);
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

from . import prune, tree
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


def _ci_shapes_present(root: Path) -> set[str]:
    """Which CI shapes this project still carries.

    A `.github/workflows/` that exists but holds no file runs nothing, so it is
    not a shape — it is a leftover, reported separately.
    """
    present = set()
    # `is_file()` follows symlinks, and prune now REFUSES to keep a symlinked
    # .gitlab-ci.yml. Without the same test here the two disagree: verify reports
    # the gitlab shape as selected while prune says the tree does not have it.
    gitlab_ci = root / tree.GITLAB_CI
    if gitlab_ci.is_file() and not gitlab_ci.is_symlink():
        present.add("gitlab")
    workflows = root / tree.GITHUB_WORKFLOWS
    # A REGULAR .yml/.yaml, not merely "some file": GitHub runs nothing else, so
    # a leftover .gitkeep or README would otherwise report the github shape as
    # selected and let verify pass a project with no CI at all. Symlinks are
    # excluded because Actions does not follow them out of the workspace.
    if (
        workflows.is_dir()
        and not workflows.is_symlink()
        and any(
            p.is_file() and not p.is_symlink() and p.suffix in (".yml", ".yaml")
            for p in workflows.iterdir()
        )
    ):
        present.add("github")
    return present


def _ci_problems(root: Path) -> list[str]:
    """Flag an unselected project, and a half-applied selection either way.

    The template ships all three shapes; a generated project must keep exactly
    one (`prune ci:<shape>`, or `rename --ci <shape>`). Leaving two means a
    GitHub mirror with Actions enabled runs a second, duplicate set of gates.
    """
    problems: list[str] = []
    present = _ci_shapes_present(root)

    if len(present) > 1:
        problems.append(
            f"both CI shapes are present ({tree.GITLAB_CI} and "
            f"{tree.GITHUB_WORKFLOWS}/) — this project has not selected one; run "
            f"`weisssrv-new-project prune ci:<{'|'.join(prune.CI_SHAPES)}>` "
            "(see docs/CI-SHAPES.md)"
        )

    # A .gitlab-ci.yml that exists but is not a regular file (a symlink, a
    # directory) is not the gitlab shape — _ci_shapes_present rejects it, to stay
    # aligned with what prune will keep. Say so explicitly, or the leftover
    # branch below reports ".gitlab-ci.yml is gone" about a file that is sitting
    # right there.
    gitlab_ci = root / tree.GITLAB_CI
    if (gitlab_ci.exists() or gitlab_ci.is_symlink()) and "gitlab" not in present:
        problems.append(
            f"{tree.GITLAB_CI} exists but is not a regular file — GitLab will "
            "not run it; restore the file or remove it"
        )

    # The surviving shape must be complete, and the dropped shapes must leave
    # nothing behind. `.gitlab/secret-detection-ruleset.toml` is what makes
    # GitLab's Secret-Detection analyzer read .gitleaks.toml, so it lives and
    # dies with .gitlab-ci.yml.
    for extra in tree.GITLAB_CI_EXTRA:
        exists = (root / extra).exists()
        if "gitlab" in present and not exists:
            problems.append(
                f"the gitlab CI shape is selected but {extra} is missing "
                f"({tree.GITLAB_CI} needs it to load .gitleaks.toml)"
            )
        elif "gitlab" not in present and exists:
            problems.append(
                f"{extra} is left over from the gitlab CI shape but "
                f"{tree.GITLAB_CI} is gone — re-run "
                f"`weisssrv-new-project prune ci:<{'|'.join(prune.CI_SHAPES)}>`"
            )

    workflows = root / tree.GITHUB_WORKFLOWS
    # Reached when the dir exists but holds no REGULAR .yml/.yaml — empty, or
    # left with a .gitkeep/README, or a symlink. GitHub runs none of those, so
    # the shape is not selected and the directory is a leftover either way.
    if "github" not in present and (workflows.is_dir() or workflows.is_symlink()):
        problems.append(
            f"{tree.GITHUB_WORKFLOWS}/ exists but holds no runnable workflow "
            "(a regular .yml/.yaml) — it is left over from the github CI shape; "
            "delete it, or restore the workflow if this project meant to keep it"
        )
    return problems


def verify(root: Path, run_kustomize: bool = True) -> tuple[bool, list[str]]:
    """Return (ok, problems). ok is True when problems is empty."""
    problems: list[str] = []

    # 1. Leftover placeholder tokens.
    token_hits = _remaining_tokens(root)
    for p in token_hits:
        problems.append(f"placeholder token remains in {p.relative_to(root)}")

    # 2. Exactly one CI shape, cleanly applied.
    problems.extend(_ci_problems(root))

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

    # 3. Listed resources exist on disk.
    for name in resources:
        if not (fdir / name).exists():
            problems.append(f"kustomization lists '{name}' but it is missing on disk")

    # 4. Non-opt-in manifests on disk are referenced.
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

    # 5. Optional kustomize build.
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
