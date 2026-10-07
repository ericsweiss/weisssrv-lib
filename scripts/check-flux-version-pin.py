#!/usr/bin/env python3
"""Hold the Flux version to one value across CI, the versions ConfigMap and gotk.

The component set must also stay the one the cluster was bootstrapped with.
Usage and exit codes: docs/SCRIPTS.md.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_CI_FILE = ".gitlab-ci.yml"
DEFAULT_VERSIONS_CONFIGMAP = "kubernetes/infrastructure/sources/versions-configmap.yaml"
DEFAULT_GOTK_GLOB = "kubernetes/clusters/*/flux-system/gotk-components.yaml"

# The default set `flux bootstrap` installs. A cluster bootstrapped with
# --components-extra passes its own --components; any other set is a
# distribution change, not a version bump.
DEFAULT_COMPONENTS = (
    "source-controller,kustomize-controller,helm-controller,notification-controller"
)

PIN_RE = re.compile(r'^\s*FLUX_VERSION[=:]\s*"?([0-9][0-9.]*)"?\s*$', re.MULTILINE)
CONFIGMAP_RE = re.compile(r'^\s*flux_version:\s*"?v?([0-9][0-9.]*)"?\s*$', re.MULTILINE)
GOTK_VERSION_RE = re.compile(r"^# Flux Version: v([0-9][0-9.]*)\s*$", re.MULTILINE)
GOTK_COMPONENTS_RE = re.compile(r"^# Components: (.+)$", re.MULTILINE)


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def check(
    root: Path,
    ci_file: str = DEFAULT_CI_FILE,
    versions_configmap: str = DEFAULT_VERSIONS_CONFIGMAP,
    gotk_glob: str = DEFAULT_GOTK_GLOB,
    components: str = DEFAULT_COMPONENTS,
    runbook: str | None = None,
) -> tuple[int, list[str]]:
    """(exit code, report lines). 0 agreement, 1 disagreement, 2 nothing inspected."""
    ci_text = _read(root / ci_file)
    if ci_text is None:
        return 2, [f"ERROR: {ci_file} is unreadable — the gate inspected nothing"]
    configmap_text = _read(root / versions_configmap)
    if configmap_text is None:
        return 2, [
            f"ERROR: {versions_configmap} is unreadable — the gate inspected nothing"
        ]

    pin = PIN_RE.search(ci_text)
    if not pin:
        return 2, [f"ERROR: no FLUX_VERSION pin in {ci_file}"]
    declared = CONFIGMAP_RE.search(configmap_text)
    if not declared:
        return 2, [f"ERROR: no flux_version key in {versions_configmap}"]

    pinned, in_configmap = pin.group(1), declared.group(1)
    report: list[str] = []
    failed = False
    if pinned != in_configmap:
        report += [
            f"FLUX_VERSION pin ({pinned}) and {versions_configmap} ({in_configmap}) disagree.",
            "Bump the version source the ConfigMap is generated from, regenerate it, and commit.",
        ]
        failed = True

    manifests = sorted(root.glob(gotk_glob))
    if not manifests:
        # Written by `flux bootstrap`, so a pre-bootstrap repository has none.
        report.append(f"No {gotk_glob} yet — checked the pin against the ConfigMap only.")
        return (1 if failed else 0), report

    for manifest in manifests:
        relative = manifest.relative_to(root)
        text = _read(manifest) or ""
        header = GOTK_VERSION_RE.search(text)
        if not header:
            report.append(f"{relative} has no '# Flux Version:' header.")
            failed = True
        elif header.group(1) != pinned:
            report += [
                f"FLUX_VERSION pin ({pinned}) and {relative} ({header.group(1)}) disagree.",
                f"Re-run `flux install --export > {relative}` with a matching CLI."
                + (f" (see {runbook})" if runbook else ""),
            ]
            failed = True
        declared_components = GOTK_COMPONENTS_RE.search(text)
        if not declared_components or declared_components.group(1).strip() != components:
            got = (
                declared_components.group(1).strip()
                if declared_components
                else "<no '# Components:' header>"
            )
            report += [
                f"{relative} lists {got}, not {components}.",
                "A cluster bootstrapped with --components-extra passes the matching --components.",
            ]
            failed = True

    if not failed:
        report.append(
            f"Flux pinned at {pinned} in {ci_file}, {versions_configmap} and "
            f"{len(manifests)} gotk-components.yaml."
        )
    return (1 if failed else 0), report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Flux version-pin agreement gate.")
    parser.add_argument("--repo-root", default=".", help="repository root (default: .)")
    parser.add_argument("--ci-file", default=DEFAULT_CI_FILE, help="file holding the FLUX_VERSION pin")
    parser.add_argument(
        "--versions-configmap",
        default=DEFAULT_VERSIONS_CONFIGMAP,
        help="ConfigMap manifest holding the flux_version key",
    )
    parser.add_argument(
        "--gotk-glob",
        default=DEFAULT_GOTK_GLOB,
        help="glob for the committed gotk-components.yaml manifests",
    )
    parser.add_argument(
        "--components",
        default=DEFAULT_COMPONENTS,
        help="expected gotk component set (extend for a --components-extra bootstrap)",
    )
    parser.add_argument(
        "--runbook",
        default=None,
        metavar="PATH",
        help="consumer doc named in the re-export remediation line",
    )
    args = parser.parse_args(argv)

    code, report = check(
        Path(args.repo_root),
        args.ci_file,
        args.versions_configmap,
        args.gotk_glob,
        args.components,
        args.runbook,
    )
    print("\n".join(report), file=sys.stderr if code == 2 else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
