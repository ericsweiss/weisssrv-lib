"""`new-cluster` — render a weisssrv cluster template with copier.

EXPERIMENTAL: weisssrv-cluster-template is not published yet, so the wrapper is
exercised against local copier templates only.

Unlike the fork-and-rename scaffold the other commands operate on, the cluster
template is a copier template, so this is a thin wrapper: validate the source
and destination up front (copier's own failure modes are late and messy), then
hand off to `copier.run_copy`. copier is an OPTIONAL dependency — it is imported
only here, so `rename`/`prune`/`wire`/`verify` stay one-dependency and offline.
"""
from __future__ import annotations

import re
from pathlib import Path

CLUSTER_TEMPLATE_URL = "https://git.ericsweiss.com/eric/weisssrv-cluster-template.git"

_COPIER_CONF = ("copier.yml", "copier.yaml")
_VCS_URL_RE = re.compile(r"^(?:[a-z][a-z0-9+.-]*://|git@|(?:gh|gl):)")

_INSTALL_HINT = (
    "copier is not installed — it ships as the CLI's `cluster` extra: "
    "pip install 'weisssrv-lib-cli[cluster]'"
)


class ClusterError(ValueError):
    """Bad arguments or an unusable source/destination (exit 2)."""


class RenderError(RuntimeError):
    """copier itself failed to render the template (exit 1)."""


def parse_data(pairs: list[str]) -> dict[str, str]:
    """`KEY=VALUE` strings into copier `data`. Values may contain `=`."""
    data: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise ClusterError(f"--data expects KEY=VALUE, got '{pair}'")
        data[key.strip()] = value
    return data


def is_vcs_source(source: str) -> bool:
    return bool(_VCS_URL_RE.match(source)) or source.endswith(".git")


def resolve_source(source: str, vcs_ref: str | None = None) -> str:
    """Validate the template source; returns the value to hand copier."""
    if is_vcs_source(source):
        return source
    path = Path(source).expanduser()
    if not path.is_dir():
        raise ClusterError(
            f"template source '{source}' is neither a directory nor a VCS URL "
            f"(the cluster template lives at {CLUSTER_TEMPLATE_URL})"
        )
    if not any((path / name).exists() for name in _COPIER_CONF):
        raise ClusterError(f"'{source}' has no copier.yml — not a copier template")
    if vcs_ref and not (path / ".git").exists():
        raise ClusterError(
            f"--vcs-ref needs a git checkout, and '{source}' is not one"
        )
    return str(path.resolve())


def _resolve_destination(destination: Path) -> Path:
    dest = Path(destination).expanduser()
    if dest.exists():
        if not dest.is_dir():
            raise ClusterError(f"destination '{dest}' exists and is not a directory")
        if any(dest.iterdir()):
            raise ClusterError(f"destination '{dest}' exists and is not empty")
    return dest


def _copier():
    try:
        import copier
    except ImportError as exc:
        raise ClusterError(_INSTALL_HINT) from exc
    return copier


def render(
    source: str,
    destination: Path,
    *,
    vcs_ref: str | None = None,
    data: dict[str, str] | None = None,
    defaults: bool = False,
    pretend: bool = False,
    trust: bool = False,
) -> Path:
    """Render `source` into `destination`. Returns the destination path."""
    # Argument validation precedes the import so a typo fails the same way
    # whether or not the optional dependency is installed.
    dest = _resolve_destination(destination)
    src = resolve_source(source, vcs_ref)
    copier = _copier()
    try:
        copier.run_copy(
            src,
            dest,
            data=data or {},
            vcs_ref=vcs_ref,
            defaults=defaults,
            pretend=pretend,
            unsafe=trust,
        )
    except copier.errors.CopierError as exc:
        raise RenderError(f"{type(exc).__name__}: {exc}") from exc
    return dest
