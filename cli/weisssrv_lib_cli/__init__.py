"""weisssrv-new-project: render a weisssrv copier template into a new repo.

Commands (see cli.main): new-cluster, new-app. copier does the rendering and is
an optional extra, so the package itself carries no runtime dependency.
"""
from importlib.metadata import PackageNotFoundError, version as _version

try:
    __version__ = _version("weisssrv-lib-cli")
except PackageNotFoundError:
    # A checkout, not an install: a version-free marker cannot go stale against
    # the release tag.
    __version__ = "0+source"
