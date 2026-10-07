"""The nextcloud role's readiness gate.

The inline Jinja `until:` expression is read out of the role and evaluated
against representative `occ status --output=json` payloads, never restated here.
"""

import json
import re
from pathlib import Path

import pytest
import yaml
from _helpers import ansible_env

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "nextcloud"
MAIN = ROLE / "tasks" / "main.yml"

GATE_PREFIX = "Wait for Nextcloud to finish"


def _gate_task() -> dict:
    """The readiness-gate task, located by name prefix rather than index."""
    tasks = yaml.safe_load(MAIN.read_text())
    matches = [
        t for t in tasks if str(t.get("name", "")).startswith(GATE_PREFIX)
    ]
    assert len(matches) == 1, (
        f"expected exactly one task named {GATE_PREFIX!r}* in {MAIN}, "
        f"found {len(matches)}"
    )
    return matches[0]


def _evaluate(status_payload, rc=0):
    """Evaluate the role's real `until:` expression against an occ payload."""
    env = ansible_env()
    expr = _gate_task()["until"]
    rendered = env.from_string("{{ (" + expr + ") | bool }}").render(
        nextcloud_status={"rc": rc, "stdout": json.dumps(status_payload)}
    )
    return rendered == "True"


def _status(**overrides):
    """A fully-ready occ status payload, with overrides applied."""
    payload = {
        "installed": True,
        "version": "34.0.3.2",
        "versionstring": "34.0.3",
        "edition": "",
        "maintenance": False,
        "needsDbUpgrade": False,
        "productname": "Nextcloud",
        "extendedSupport": False,
    }
    payload.update(overrides)
    return payload


def test_gate_passes_when_fully_ready():
    assert _evaluate(_status()) is True


def test_gate_blocks_while_db_upgrade_pending():
    """A pending DB migration keeps the gate closed."""
    assert _evaluate(_status(needsDbUpgrade=True)) is False


def test_gate_blocks_while_in_maintenance_mode():
    assert _evaluate(_status(maintenance=True)) is False


def test_gate_blocks_before_install_completes():
    assert _evaluate(_status(installed=False)) is False


def test_gate_blocks_on_nonzero_rc():
    assert _evaluate(_status(), rc=1) is False


@pytest.mark.parametrize("missing", ["needsDbUpgrade", "maintenance"])
def test_gate_fails_safe_when_a_field_is_absent(missing):
    """An occ that does not report a field must keep us waiting, not proceed."""
    payload = _status()
    del payload[missing]
    assert _evaluate(payload) is False


def test_gate_retries_long_enough_to_outlast_a_migration():
    """A stricter gate is only an improvement if it is allowed to wait."""
    task = _gate_task()
    defaults = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())
    assert task["retries"] == "{{ nextcloud_install_wait_retries }}"
    assert task["delay"] == "{{ nextcloud_install_wait_delay }}"
    budget = (
        defaults["nextcloud_install_wait_retries"]
        * defaults["nextcloud_install_wait_delay"]
    )
    assert budget >= 300, f"retry budget {budget}s is too short for a migration"


SSRF_PREFIX = "Converge server-side requests to local addresses"


def _ssrf_expression() -> str:
    """The `--value={{ ... }}` half of the SSRF-guard occ command."""
    tasks = yaml.safe_load(MAIN.read_text())
    matches = [t for t in tasks if str(t.get("name", "")) == SSRF_PREFIX]
    assert len(matches) == 1, f"expected one task named {SSRF_PREFIX!r} in {MAIN}"
    cmd = matches[0]["ansible.builtin.command"]["cmd"]
    found = re.search(r"--value=(\{\{.*?\}\})", cmd, re.S)
    assert found, cmd
    return found.group(1)


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        ({"nextcloud_oidc_enabled": True,
          "nextcloud_oidc_allow_local_remote_servers": True}, "true"),
        ({"nextcloud_oidc_enabled": True,
          "nextcloud_oidc_allow_local_remote_servers": False}, "false"),
        ({"nextcloud_oidc_enabled": True}, "false"),
        ({"nextcloud_oidc_allow_local_remote_servers": True}, "false"),
        ({}, "false"),
        # String spellings, as an inventory or an `-e` override supplies them.
        ({"nextcloud_oidc_enabled": "true",
          "nextcloud_oidc_allow_local_remote_servers": "true"}, "true"),
        ({"nextcloud_oidc_enabled": "yes",
          "nextcloud_oidc_allow_local_remote_servers": "1"}, "true"),
        ({"nextcloud_oidc_enabled": "true",
          "nextcloud_oidc_allow_local_remote_servers": "false"}, "false"),
        ({"nextcloud_oidc_enabled": "true",
          "nextcloud_oidc_allow_local_remote_servers": "no"}, "false"),
        ({"nextcloud_oidc_enabled": "true",
          "nextcloud_oidc_allow_local_remote_servers": "0"}, "false"),
        ({"nextcloud_oidc_enabled": "true",
          "nextcloud_oidc_allow_local_remote_servers": ""}, "false"),
    ],
)
def test_the_ssrf_guard_falls_back_closed(context, expected):
    """An unset allow-flag must render `false`, matching defaults/main.yml."""
    env = ansible_env()
    assert env.from_string(_ssrf_expression()).render(**context) == expected


def test_the_role_default_stays_closed():
    defaults = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())
    assert defaults["nextcloud_oidc_allow_local_remote_servers"] is False


NGINX_TEMPLATE = ROLE / "templates" / "nginx-nextcloud.conf.j2"
HSTS_HEADER = 'add_header Strict-Transport-Security'


def _render_nginx(**overrides) -> str:
    """The nginx vhost rendered against defaults/main.yml plus overrides."""
    defaults = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())
    context = dict(defaults)
    context.update(overrides)
    env = ansible_env()
    return env.from_string(NGINX_TEMPLATE.read_text()).render(**context)


def test_the_default_renders_the_hsts_header():
    rendered = _render_nginx()
    assert (
        'add_header Strict-Transport-Security '
        '"max-age=31536000; includeSubDomains" always;'
    ) in rendered


def test_a_boolean_false_drops_the_hsts_header():
    assert HSTS_HEADER not in _render_nginx(nextcloud_nginx_hsts_enabled=False)


@pytest.mark.parametrize("spelling", ["false", "no", "0", ""])
def test_a_string_false_drops_the_hsts_header(spelling):
    """An inventory or `-e` override supplies a string; plain Jinja truthiness
    would pin a year-long includeSubDomains nobody asked for."""
    assert HSTS_HEADER not in _render_nginx(nextcloud_nginx_hsts_enabled=spelling)
