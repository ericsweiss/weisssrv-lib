"""Behavioural tests for the nextcloud role's readiness gate.

The gate is an inline Jinja `until:` expression, so the only way to test it
without standing up a real Nextcloud is to pull the expression out of the task
file and evaluate it against representative `occ status --output=json`
payloads. That is what these tests do: the expression under test is READ FROM
THE ROLE, never restated here, so a regression in the role fails the assertions
rather than quietly diverging from a copy.

Regression context: gating on `installed` alone let a version bump through. On
a 34.0.2 -> 34.0.3 upgrade the container was recreated, `occ status` answered
`installed: true` while the migration was still pending, the gate passed in
~0.5s, and the next occ call died on "only a limited number of commands are
available" — taking `Configure the OIDC provider` down with it.
"""

import json
from pathlib import Path

import jinja2
import pytest
import yaml

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
    env = jinja2.Environment(undefined=jinja2.ChainableUndefined)
    # `from_json` and `bool` are Ansible filters, not stock Jinja ones. The
    # payloads here only ever carry real JSON booleans, so Python's `bool` is a
    # faithful stand-in for Ansible's string-aware one.
    env.filters["from_json"] = json.loads
    env.filters["bool"] = bool
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
    """The exact state that broke deploy-nextcloud on the 34.0.3 bump."""
    assert _evaluate(_status(needsDbUpgrade=True)) is False


def test_gate_blocks_while_in_maintenance_mode():
    assert _evaluate(_status(maintenance=True)) is False


def test_gate_blocks_before_install_completes():
    assert _evaluate(_status(installed=False)) is False


def test_gate_blocks_on_nonzero_rc():
    assert _evaluate(_status(), rc=1) is False


@pytest.mark.parametrize("missing", ["needsDbUpgrade", "maintenance"])
def test_gate_fails_safe_when_a_field_is_absent(missing):
    """An occ that does not report a field must keep us waiting, not proceed.

    `default(true)` on the two upgrade fields points the failure in the safe
    direction: waiting costs a retry budget that already spans minutes, while
    proceeding costs a failed deploy against a half-migrated instance.
    """
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
