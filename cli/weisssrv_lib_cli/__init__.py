"""weisssrv-new-project: scaffold a weisssrv cluster tenant repo.

Commands (see cli.main): rename, prune, wire, verify, new-cluster. Offline and
dependency-light — the only runtime dependency is ruamel.yaml (round-trip YAML
editing that preserves the scaffold's comments); copier is an optional extra
that only new-cluster needs.
"""
from importlib.metadata import PackageNotFoundError, version as _version

try:
    # Installed: report the distribution version, so `--version` answers "which
    # library tag is this CLI from?" instead of a hand-copied literal.
    __version__ = _version("weisssrv-lib-cli")
except PackageNotFoundError:
    __version__ = "0.2.0+source"  # running off a checkout (PYTHONPATH=cli)
