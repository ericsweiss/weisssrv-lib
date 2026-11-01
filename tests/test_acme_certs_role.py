"""acme_certs shared report expressions and the re-seed guard."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from _helpers import ansible_env

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "acme_certs"


def _tasks(name: str) -> list[dict]:
    return yaml.safe_load((ROLE / "tasks" / name).read_text())


def _render(expr: str, **context):
    env = ansible_env()
    env.filters["regex_replace"] = lambda value, pattern, repl: re.sub(pattern, repl, value)
    return env.from_string(expr.strip()).render(**context)


ROLE_VARS = yaml.safe_load((ROLE / "vars" / "main.yml").read_text())
DEFAULTS = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())
TARGETS = [
    {"host": "a"},
    {"host": "b", "ssh_no_sudo": True},
    {"host": "c", "ssh_no_sudo": False},
]


def test_no_sudo_hosts_selects_only_the_flagged_targets():
    rendered = _render(ROLE_VARS["_acme_certs_no_sudo_hosts"], acme_certs_distribution_targets=TARGETS)
    assert rendered == "['b']"


def test_reseed_hosts_defaults_to_every_sudo_target():
    no_sudo = ["b"]
    rendered = _render(
        DEFAULTS["acme_certs_reseed_hosts"],
        acme_certs_distribution_targets=TARGETS,
        _acme_certs_no_sudo_hosts=no_sudo,
    )
    assert rendered == "['a', 'c']"


REASONS = {"a": "did not answer the probe", "b": "ssh_no_sudo"}


def test_reasons_report_reads_host_then_reason():
    rendered = _render(ROLE_VARS["_acme_certs_reasons_report"], _acme_certs_reasons=REASONS)
    assert rendered == "a (did not answer the probe), b (ssh_no_sudo)"
    assert _render(ROLE_VARS["_acme_certs_reasons_report"]) == ""


def test_the_report_names_why_nothing_was_pinned():
    report = _tasks("reseed.yml")[-1]
    assert report["name"] == "Report the targets the re-seed left unpinned"
    expr = report["vars"]["_acme_certs_reseed_miss_msg"]
    disabled = _render(expr, acme_certs_enabled=False, inventory_hostname="ca")
    assert disabled == "acme_certs_enabled is false on ca; the reseed entry point re-pins nothing."
    missed = _render(expr, acme_certs_enabled=True, acme_certs_reseed_report="x (did not answer the probe)")
    assert missed.startswith("Not re-seeded: x (did not answer the probe). Their next cert push fails")
