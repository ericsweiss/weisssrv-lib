"""weisssrv-new-project: render a weisssrv copier template into a new repo.

Commands (see cli.main): new-cluster, new-app. copier does the rendering and is
an optional extra, so the package itself carries no runtime dependency.
"""
from importlib.metadata import PackageNotFoundError, version as _version

try:
    # Installed: report the distribution version, so `--version` answers "which
    # library tag is this CLI from?" instead of a hand-copied literal.
    __version__ = _version("weisssrv-lib-cli")
except PackageNotFoundError:
    # Running off a checkout (PYTHONPATH=cli): a version-free marker, so it
    # cannot go stale against the release tag.
    __version__ = "0+source"
