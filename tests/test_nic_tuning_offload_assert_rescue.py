"""The offload-drift negative cases in nic_tuning's scenario must stay negative.

A looped assert reports only "One or more items failed" at task level, so the
rescue has to read the per-item fail_msg out of ansible_failed_result.results."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from ansible.plugins.test import core as ansible_tests
from jinja2.nativetypes import NativeEnvironment

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "nic_tuning"
ASSERT_TASKS = ROLE / "tasks" / "assert-offloads.yml"
VERIFY = ROLE / "molecule" / "default" / "verify.yml"
DEFAULTS = ROLE / "defaults" / "main.yml"
ASSERT_NAME = "Assert every requested NIC offload override is live"
ENV = NativeEnvironment()
# The role's assert uses ansible.builtin tests (`match`), absent from Jinja.
ENV.tests.update(ansible_tests.TestModule().tests())


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _assert_task() -> dict:
    task = next(t for t in _load(ASSERT_TASKS) if t.get("name") == ASSERT_NAME)
    return task


def _render(template: str, context: dict):
    return ENV.from_string(template).render(**context)


def _role_failure(override: dict, option: dict, state: dict) -> tuple[bool, str]:
    """Run the role's own assert body against a fixture: (passed, fail_msg)."""
    task = _assert_task()
    context = {
        "item": [override, option],
        "nic_tuning_overrides": [override],
        "nic_tuning_offload_state": state,
        "nic_tuning_feature_names": _load(DEFAULTS)["nic_tuning_feature_names"],
        "ansible_check_mode": False,
    }
    for name, expr in task["vars"].items():
        context[name] = _render(expr, context)
    passed = all(
        bool(_render("{{ %s }}" % clause, context))
        for clause in task["ansible.builtin.assert"]["that"]
    )
    return passed, str(_render(task["ansible.builtin.assert"]["fail_msg"], context))


def _play() -> dict:
    return _load(VERIFY)[0]


def _rescue_expr(fact: str) -> str:
    """The rescue set_fact expression that records fact, found by its key."""
    for task in _play()["tasks"]:
        for entry in task.get("rescue", []):
            facts = entry.get("ansible.builtin.set_fact", {})
            if fact in facts:
                return facts[fact]
    raise AssertionError("no rescue sets %s" % fact)


def _looped_result(messages: list[str]) -> dict:
    """What ansible_failed_result looks like when a looped assert fails."""
    return {
        "changed": False,
        "failed": True,
        "msg": "One or more items failed",
        "results": [{"failed": True, "msg": m} for m in messages],
    }


def _evaluate(fact: str, failed_result: dict) -> bool:
    context = {"ansible_failed_result": failed_result}
    helper = _play()["vars"]["nic_tuning_failed_item_msgs"]
    context["nic_tuning_failed_item_msgs"] = _render(helper, context)
    return bool(_render(_rescue_expr(fact), context))


DRIFTED = (
    {"interface": "nic0", "options": [{"feature": "gro", "value": "off"}]},
    {"feature": "gro", "value": "off"},
    {
        "results": [
            {
                "item": {"interface": "nic0"},
                "rc": 0,
                "stderr": "",
                "stdout_lines": ["Features for nic0:", "generic-receive-offload: on"],
            }
        ]
    },
)
UNREADABLE = (
    {"interface": "nic9", "options": [{"feature": "gro", "value": "off"}]},
    {"feature": "gro", "value": "off"},
    {
        "results": [
            {
                "item": {"interface": "nic9"},
                "rc": 71,
                "stderr": "Cannot get device feature names: No such device",
                "stdout_lines": [],
            }
        ]
    },
)


@pytest.mark.parametrize(
    ("fixture", "fact"),
    [(DRIFTED, "nic_tuning_drift_rejected"),
     (UNREADABLE, "nic_tuning_ethtool_error_rejected")],
)
def test_the_scenarios_negative_fixtures_still_fail_the_role_assert(fixture, fact):
    passed, _ = _role_failure(*fixture)
    assert not passed, "the %s fixture no longer drives the assert to fail" % fact


@pytest.mark.parametrize(
    ("fixture", "fact"),
    [(DRIFTED, "nic_tuning_drift_rejected"),
     (UNREADABLE, "nic_tuning_ethtool_error_rejected")],
)
def test_the_rescue_reads_the_per_item_fail_msg(fixture, fact):
    _, fail_msg = _role_failure(*fixture)
    assert _evaluate(fact, _looped_result([fail_msg])), (
        "the rescue did not recognise the role's own fail_msg: %s" % fail_msg
    )


@pytest.mark.parametrize(
    "fact", ["nic_tuning_drift_rejected", "nic_tuning_ethtool_error_rejected"]
)
def test_the_task_level_loop_message_alone_is_not_accepted(fact):
    assert not _evaluate(fact, _looped_result([]))


@pytest.mark.parametrize(
    ("fixture", "fact"),
    [(DRIFTED, "nic_tuning_ethtool_error_rejected"),
     (UNREADABLE, "nic_tuning_drift_rejected")],
)
def test_another_interfaces_failure_is_not_accepted(fixture, fact):
    _, fail_msg = _role_failure(*fixture)
    assert not _evaluate(fact, _looped_result([fail_msg]))
