"""The copier wrapper behind `new-cluster` and `new-app`.

A thin wrapper: validate the source and destination up front (copier's own
failure modes are late and messy), then hand off to `copier.run_copy`. copier is
an OPTIONAL dependency, imported at render time, so argument validation and
`--help` work without it installed.

The two subcommands differ only in which published template they name; rendering
is template-agnostic, so any copier template works as a source.
"""
from __future__ import annotations

import re
from pathlib import Path

CLUSTER_TEMPLATE_URL = "https://git.ericsweiss.com/eric/weisssrv-cluster-template.git"
APP_TEMPLATE_URL = "https://git.ericsweiss.com/eric/weisssrv-app-template.git"

_COPIER_CONF = ("copier.yml", "copier.yaml")
_VCS_URL_RE = re.compile(r"^(?:[a-z][a-z0-9+.-]*://|git@|(?:gh|gl):)")

_INSTALL_HINT = (
    "copier is not installed — it ships as the CLI's `cluster` extra: "
    "pip install 'weisssrv-lib-cli[cluster]'"
)


class TemplateError(ValueError):
    """Bad arguments or an unusable source/destination (exit 2)."""


class MissingCopierError(TemplateError):
    """The `cluster` extra is not installed (exit 3).

    A subclass of TemplateError so a caller that only knows the two original
    classes keeps catching it; the distinct exit code separates "fix your
    environment" from "fix your arguments".
    """


class RenderError(RuntimeError):
    """copier itself failed to render the template (exit 1)."""


def parse_data(pairs: list[str]) -> dict[str, str]:
    """`KEY=VALUE` strings into copier `data`. Values may contain `=`."""
    data: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise TemplateError(f"--data expects KEY=VALUE, got '{pair}'")
        data[key.strip()] = value
    return data


def is_vcs_source(source: str) -> bool:
    return bool(_VCS_URL_RE.match(source)) or source.endswith(".git")


def resolve_source(source: str, vcs_ref: str | None = None, published: str | None = None) -> str:
    """Validate the template source; returns the value to hand copier.

    `published` is the URL named in the "not a template" message — the one this
    subcommand exists to render.
    """
    if is_vcs_source(source):
        return source
    path = Path(source).expanduser()
    if not path.is_dir():
        raise TemplateError(
            f"template source '{source}' is neither a directory nor a VCS URL "
            f"(the published template lives at {published or CLUSTER_TEMPLATE_URL})"
        )
    if not any((path / name).exists() for name in _COPIER_CONF):
        raise TemplateError(f"'{source}' has no copier.yml — not a copier template")
    if vcs_ref and not (path / ".git").exists():
        raise TemplateError(
            f"--vcs-ref needs a git checkout, and '{source}' is not one"
        )
    return str(path.resolve())


def _resolve_destination(destination: Path) -> Path:
    dest = Path(destination).expanduser()
    if dest.exists():
        if not dest.is_dir():
            raise TemplateError(f"destination '{dest}' exists and is not a directory")
        if any(dest.iterdir()):
            raise TemplateError(f"destination '{dest}' exists and is not empty")
    return dest


def _copier():
    try:
        import copier
    except ImportError as exc:
        raise MissingCopierError(_INSTALL_HINT) from exc
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
    published: str | None = None,
) -> Path:
    """Render `source` into `destination`. Returns the destination path."""
    # Argument validation precedes the import so a typo fails the same way
    # whether or not the optional dependency is installed.
    dest = _resolve_destination(destination)
    src = resolve_source(source, vcs_ref, published)
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
