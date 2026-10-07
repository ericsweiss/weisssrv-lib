"""The zfs_exporter unit binds where zfs_exporter_listen_address says.

The default binds every interface; a narrowed bind is what a site sets when the
host firewall is not the only access control it wants.
"""
from __future__ import annotations

from pathlib import Path

import jinja2
import pytest
import yaml
from _helpers import ansible_env

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "zfs_exporter"
TEMPLATE = ROLE / "templates" / "zfs-exporter.service.j2"


def render(listen_address: str) -> str:
    env = ansible_env(undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
    template = env.from_string(TEMPLATE.read_text(encoding="utf-8"))
    return template.render(
        ansible_managed="managed",
        zfs_exporter_port=9134,
        zfs_exporter_listen_address=listen_address,
    )


def test_the_empty_default_binds_every_interface():
    assert "ExecStart=/usr/local/bin/zfs_exporter --web.listen-address=:9134" in render("")


def test_a_bind_address_reaches_the_unit():
    unit = render("127.0.0.1")
    assert "--web.listen-address=127.0.0.1:9134" in unit
    # A misplaced colon would render `=:127.0.0.1:9134` and bind everything.
    assert "--web.listen-address=:" not in unit


def test_an_ipv6_bind_is_bracketed():
    """Go's net.SplitHostPort rejects a bare `fd00::5:9134`, so the exporter
    would fail to start on the very address the variable exists to set."""
    assert "--web.listen-address=[fd00::5]:9134" in render("fd00::5")


def test_the_role_default_is_the_open_bind():
    defaults = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())
    assert defaults["zfs_exporter_listen_address"] == ""


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
