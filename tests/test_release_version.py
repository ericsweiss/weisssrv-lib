"""The release literal is held equal across every runnable pin snippet.

README.md's **Current release** line is the authority for the tag. Most pin
examples in the docs are written as `<CURRENT_TAG>` so a bump touches nothing,
but a handful must stay copy-paste runnable and therefore carry the literal:
cli/README.md's pipx specs, docker/README.md's `ref:`, and the `?ref=` in each
Terraform module README. Those are swept by hand at release time, so they are
gated here against cli/pyproject.toml's version — the one machine-readable
copy.

Scope is deliberately narrow: only these files, and only pin-example literals.
Historical prose elsewhere legitimately names older tags (the migration notes
in docs/INCLUDE-CONTRACT.md and MIGRATING.md), and asserting over those would
make the gate a nuisance rather than a guard.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PYPROJECT = REPO / "cli" / "pyproject.toml"

# Files whose pin snippets must be runnable at the current tag, with the regex
# that finds every literal in each. Each pattern captures the vX.Y.Z it matched.
TAG = r"v\d+\.\d+\.\d+"
LITERAL_SITES: dict[str, re.Pattern[str]] = {
    "cli/README.md": re.compile(rf"weisssrv-lib\.git@({TAG})"),
    "docker/README.md": re.compile(rf"^\s*ref:\s*({TAG})\s*$", re.MULTILINE),
    "terraform/modules/cloudflare-zone/README.md": re.compile(rf"\?ref=({TAG})"),
    "terraform/modules/tailscale-acl/README.md": re.compile(rf"\?ref=({TAG})"),
    "terraform/modules/authentik-sso/README.md": re.compile(rf"\?ref=({TAG})"),
    "terraform/modules/unifi-network/README.md": re.compile(rf"\?ref=({TAG})"),
}


def release_version() -> str:
    """The distribution version, as `vX.Y.Z`."""
    with PYPROJECT.open("rb") as handle:
        return "v" + tomllib.load(handle)["project"]["version"]


def test_readme_current_release_matches_the_distribution_version() -> None:
    """README.md's Current release line names the version cli/pyproject.toml ships."""
    readme = (REPO / "README.md").read_text()
    heading = readme.index("## Current release")
    body = readme[heading:]
    match = re.search(rf"\*\*({TAG})\.\*\*", body)
    assert match, "README.md '## Current release' has no bolded **vX.Y.Z.** literal"
    assert match.group(1) == release_version()


@pytest.mark.parametrize("relative_path", sorted(LITERAL_SITES))
def test_pin_snippets_name_the_current_release(relative_path: str) -> None:
    """Every literal tag in a runnable pin snippet equals the release version."""
    path = REPO / relative_path
    assert path.is_file(), f"{relative_path} is missing"
    found = LITERAL_SITES[relative_path].findall(path.read_text())
    assert found, (
        f"{relative_path} carries no literal pin — it is registered here because "
        "its snippet must stay copy-paste runnable, so either restore the pin or "
        "drop the file from LITERAL_SITES"
    )
    stale = sorted({tag for tag in found if tag != release_version()})
    assert not stale, (
        f"{relative_path} pins {', '.join(stale)}; "
        f"cli/pyproject.toml ships {release_version()}"
    )
