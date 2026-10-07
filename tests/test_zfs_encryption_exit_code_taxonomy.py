"""zfs-load-key.sh documents exactly the exit codes it can produce.

zfs-load-key@.service names codes by number in RestartPreventExitStatus, so a
renumbered `exit`, or a header line lost to a reword, changes retry behaviour.
"""
from __future__ import annotations

import re
from pathlib import Path

import jinja2
import pytest
from _helpers import ansible_env

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "zfs_encryption"
SCRIPT = ROLE / "templates" / "zfs-load-key.sh.j2"
UNIT = ROLE / "templates" / "zfs-load-key@.service.j2"

# Header taxonomy lines read `#   <code> — <meaning>`.
DOCUMENTED = re.compile(r"(?m)^#\s+(\d+) ")
REACHABLE = re.compile(r"\bexit (\d+)\b")
NO_RETRY = re.compile(r"(?m)^RestartPreventExitStatus=(.*)$")

CONNECT_CODES = ["0", "1", "2", "3", "4", "5", "7"]
# The key-command branch has no Connect call, so no 401/403 (7).
KEYCMD_CODES = ["0", "1", "2", "3", "4", "5"]


def render(key_command: str) -> str:
    env = ansible_env(undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
    template = env.from_string(SCRIPT.read_text(encoding="utf-8"))
    return template.render(
        ansible_managed="managed",
        zfs_encryption_key_command=key_command,
        zfs_encryption_token_path="/etc/onepassword-connect/token",
        zfs_encryption_connect_url="https://connect.test.local",
        zfs_encryption_connect_vault="Homelab",
        zfs_encryption_fetch_timeout_seconds=120,
        zfs_encryption_fetch_retry_seconds=5,
    )


def documented(script: str) -> list[str]:
    return sorted(set(DOCUMENTED.findall(script)))


def reachable(script: str) -> list[str]:
    """Codes the shell can exit with; comment lines are dropped so prose
    naming a code ("an instant exit 5") is not read as a code path."""
    code = "\n".join(
        line for line in script.splitlines() if not line.lstrip().startswith("#")
    )
    return sorted(set(REACHABLE.findall(code)))


@pytest.mark.parametrize(
    ("key_command", "expected"),
    [("", CONNECT_CODES), ("printf '%s' secret", KEYCMD_CODES)],
    ids=["connect", "key-command"],
)
def test_each_render_documents_exactly_what_it_exits_with(key_command, expected):
    script = render(key_command)
    assert documented(script) == expected
    assert reachable(script) == expected


def test_the_units_no_retry_codes_are_documented_and_reachable():
    script = render("")
    match = NO_RETRY.search(UNIT.read_text(encoding="utf-8"))
    assert match, "zfs-load-key@.service.j2 declares no RestartPreventExitStatus"
    codes = match.group(1).split()
    assert codes, "RestartPreventExitStatus is empty — nothing stops the retries"
    assert set(codes) <= set(documented(script))
    assert set(codes) <= set(reachable(script))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s.replace("#   7 — Connect", "#   8 — Connect", 1),
        lambda s: s.replace("        exit 7\n", "        exit 8\n", 1),
    ],
    ids=["header-renumbered", "exit-renumbered"],
)
def test_a_renumbered_code_is_caught(mutate):
    """The negative arm: either half moving alone must break the comparison."""
    script = mutate(render(""))
    assert script != render(""), "mutation needle no longer present"
    assert documented(script) != reachable(script)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
