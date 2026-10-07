"""The zvol source table renders the fields restic-offsitectl reads back.

The clone is mounted with whatever `mount_opts` renders, and molecule declares
no zvol sources, so the read-only default has no other cover."""

from __future__ import annotations

from pathlib import Path

import pytest
from _helpers import ansible_env

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "restic_offsite"
TEMPLATE = ROLE / "templates" / "restic-offsitectl.sh.j2"


def _render(*sources: dict) -> str:
    env = ansible_env(keep_trailing_newline=True)
    template = env.from_string(TEMPLATE.read_text(encoding="utf-8"))
    return template.render(
        ansible_managed="managed",
        restic_offsite_sources=[],
        restic_offsite_zvol_sources=list(sources),
    )


def _zvol_rows(rendered: str) -> list[str]:
    body = rendered.split("ZVOL_SOURCES=(", 1)[1].split("\n)", 1)[0]
    return [line for line in body.splitlines() if line.strip()]


def test_the_omitted_options_default_to_an_ext4_read_only_mount() -> None:
    rendered = _render({"name": "data", "zvol": "tank/data"})
    assert _zvol_rows(rendered) == ['  "data tank/data ext4 ro"']


def test_declared_options_pass_through() -> None:
    rendered = _render(
        {"name": "vm", "zvol": "tank/vm", "fstype": "xfs", "mount_opts": "ro,noload"}
    )
    assert _zvol_rows(rendered) == ['  "vm tank/vm xfs ro,noload"']


def test_a_writable_mount_is_the_site_asking_for_it() -> None:
    """`rw` renders only when declared, proving `ro` is the default and not a
    hardcoded value: a writable clone is mutated during the backup walk."""
    rendered = _render({"name": "data", "zvol": "tank/data", "mount_opts": "rw"})
    assert _zvol_rows(rendered) == ['  "data tank/data ext4 rw"']


def test_the_consumer_reads_the_fields_in_the_rendered_order() -> None:
    assert "read -r name zvol fstype mopts" in _render()


def test_no_zvol_source_renders_an_empty_array() -> None:
    assert _zvol_rows(_render()) == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
