"""node_exporter_host binds and probes the address the site asks for.

An IPv6 literal must be bracketed in the unit, in the probe URL and in
node-exporter-healthcheck.sh, or the exporter never starts or is restarted."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import jinja2
import pytest
import yaml
from _helpers import ansible_env

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "node_exporter_host"
VARS = ROLE / "vars" / "main.yml"
UNIT = ROLE / "templates" / "node-exporter-host.service.j2"
TASKS = ROLE / "tasks" / "main.yml"
SCRIPT = ROLE / "files" / "node-exporter-healthcheck.sh"
PROBE_TASK = "Verify node_exporter is listening on port {{ node_exporter_host_port }}"
PORT = 9101


def _derived(bind: str) -> dict:
    """vars/main.yml rendered in file order, as Ansible resolves it lazily."""
    env = ansible_env(undefined=jinja2.StrictUndefined)
    context = {"node_exporter_host_bind_address": bind}
    for name, expression in yaml.safe_load(VARS.read_text(encoding="utf-8")).items():
        context[name] = env.from_string(expression).render(**context).strip()
    return context


def _unit(bind: str) -> str:
    env = ansible_env(undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
    return env.from_string(UNIT.read_text(encoding="utf-8")).render(
        ansible_managed="managed",
        node_exporter_host_port=PORT,
        node_exporter_host_systemd_collector=False,
        node_exporter_host_processes_collector=False,
        node_exporter_host_textfile_dir="/var/lib/node_exporter",
        **_derived(bind),
    )


def _probe_url(bind: str) -> str:
    tasks = yaml.safe_load(TASKS.read_text(encoding="utf-8"))
    url = next(t["ansible.builtin.uri"]["url"] for t in tasks if t.get("name") == PROBE_TASK)
    env = ansible_env(undefined=jinja2.StrictUndefined)
    return env.from_string(url).render(node_exporter_host_port=PORT, **_derived(bind))


@pytest.mark.parametrize(
    "bind,listen",
    [("", ":9101"), ("127.0.0.1", "127.0.0.1:9101"), ("fd00::5", "[fd00::5]:9101")],
)
def test_the_unit_binds_where_the_site_asked(bind, listen) -> None:
    """Go's net.SplitHostPort rejects a bare `fd00::5:9101`, so an unbracketed
    IPv6 bind fails to start on the very address the variable exists to set."""
    assert "--web.listen-address=%s " % listen in _unit(bind)


@pytest.mark.parametrize(
    "bind,host",
    [("", "127.0.0.1"), ("127.0.0.1", "127.0.0.1"), ("fd00::5", "[fd00::5]")],
)
def test_the_play_probes_the_bound_address(bind, host) -> None:
    assert _probe_url(bind) == "http://%s:9101/metrics" % host


def _probe_with_stub(tmp_path: Path, host: str) -> str:
    """Run the healthcheck's probe with a curl stub that records its argv."""
    recorded = tmp_path / "argv"
    stub = tmp_path / "curl"
    stub.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@" >> "$RECORDED"\nexit 0\n')
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    env = {
        **os.environ,
        "PATH": "%s:%s" % (tmp_path, os.environ["PATH"]),
        "RECORDED": str(recorded),
        "NODE_EXPORTER_PROBE_HOST": host,
    }
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--probe-only", str(PORT)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return recorded.read_text(encoding="utf-8")


def test_the_healthcheck_probes_loopback_by_default(tmp_path) -> None:
    argv = _probe_with_stub(tmp_path, "127.0.0.1")
    assert "http://127.0.0.1:9101/metrics" in argv


def test_the_healthcheck_brackets_an_ipv6_probe_host(tmp_path) -> None:
    """An unbracketed host makes curl fail every interval, and the timer then
    restarts a healthy exporter forever."""
    argv = _probe_with_stub(tmp_path, "fd00::5")
    assert "http://[fd00::5]:9101/metrics" in argv


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
