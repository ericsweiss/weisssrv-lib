"""Every field the AdGuard dns_config POST sends is also compared for drift.

AdGuard counts the per-client rate limit over a /24, so a per-host whitelist
stays inert until the subnet lengths are sent and compared too."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from _helpers import ansible_env

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "adguard_home"
BASE_CONFIG = ROLE / "tasks" / "api_base_config.yml"
DEFAULTS = ROLE / "defaults" / "main.yml"
TASK = "Update DNS configuration"

# Each dns_config field and the role variable that drives it. The body and the
# drift expression are both checked against this, so a new field has to be
# added to all three.
FIELDS = {
    "protection_enabled": "adguard_home_protection_enabled",
    "upstream_dns": "adguard_home_upstream_dns",
    "upstream_mode": "adguard_home_upstream_mode",
    "fallback_dns": "adguard_home_fallback_dns",
    "dnssec_enabled": "adguard_home_enable_dnssec",
    "resolve_clients": "adguard_home_resolve_clients",
    "use_private_ptr_resolvers": "adguard_home_use_private_ptr_resolvers",
    "disable_ipv6": "adguard_home_disable_ipv6",
    "ratelimit": "adguard_home_ratelimit",
    "ratelimit_whitelist": "adguard_home_ratelimit_whitelist",
    "ratelimit_subnet_len_ipv4": "adguard_home_ratelimit_subnet_len_ipv4",
    "ratelimit_subnet_len_ipv6": "adguard_home_ratelimit_subnet_len_ipv6",
    "cache_enabled": "adguard_home_cache_enabled",
    "cache_size": "adguard_home_cache_size",
    "cache_ttl_min": "adguard_home_cache_ttl_min",
    "cache_ttl_max": "adguard_home_cache_ttl_max",
    "cache_optimistic": "adguard_home_cache_optimistic",
}


def _flatten(tasks) -> list[dict]:
    """Every task in file order, descending into block/rescue/always."""
    flat: list[dict] = []
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        flat.append(task)
        for key in ("block", "rescue", "always"):
            flat += _flatten(task.get(key))
    return flat


TASKS = _flatten(yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8")))
WANTED = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))


def _task() -> dict:
    matches = [t for t in TASKS if str(t.get("name", "")) == TASK]
    assert len(matches) == 1, f"expected exactly one task named {TASK!r}"
    return matches[0]


def _body() -> dict:
    return _task()["ansible.builtin.uri"]["body"]


def _drift_expression() -> str:
    """The `when:` entry that compares the live configuration with the desired."""
    conditions = [str(c) for c in _task()["when"]]
    matches = [c for c in conditions if "adguard_home_current_dns_config" in c]
    assert len(matches) == 1, "the drift comparison is no longer a single condition"
    return matches[0]


def _other(value):
    """A value of the same type that is not `value`."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, list):
        return value + ["203.0.113.1"]
    return "parallel" if value != "parallel" else "fastest_addr"


def _drifts(live: dict, **wanted) -> bool:
    """Evaluate the role's own drift expression against one live reading."""
    context = {var: WANTED[var] for var in FIELDS.values()}
    context.update(wanted)
    context["adguard_home_current_dns_config"] = {"json": live}
    rendered = ansible_env().from_string(
        "{{ (" + _drift_expression() + ") | bool }}"
    ).render(**context)
    return rendered == "True"


def test_the_body_sends_exactly_the_fields_under_comparison() -> None:
    assert set(_body()) == set(FIELDS)


@pytest.mark.parametrize("field", sorted(FIELDS))
def test_each_field_is_bound_to_its_role_variable(field: str) -> None:
    """A bare reference, so the uri module keeps the JSON type AdGuard expects."""
    assert _body()[field] == "{{ %s }}" % FIELDS[field]


def test_an_instance_that_already_matches_is_not_reposted() -> None:
    live = {field: WANTED[var] for field, var in FIELDS.items()}
    assert _drifts(live) is False


@pytest.mark.parametrize("field", sorted(FIELDS))
def test_a_drift_in_any_single_field_triggers_the_post(field: str) -> None:
    live = {f: WANTED[var] for f, var in FIELDS.items()}
    live[field] = _other(live[field])
    assert _drifts(live) is True, f"{field} is sent but never compared"


@pytest.mark.parametrize(
    "field", ["ratelimit_subnet_len_ipv4", "ratelimit_subnet_len_ipv6"]
)
def test_an_instance_not_reporting_the_subnet_lengths_does_not_churn(field: str) -> None:
    """A build old enough to omit the keys reads as AdGuard's own default, so the
    role leaves it alone instead of reposting on every run."""
    live = {f: WANTED[var] for f, var in FIELDS.items()}
    del live[field]
    assert _drifts(live) is False


@pytest.mark.parametrize(
    "field,var",
    [
        ("ratelimit_subnet_len_ipv4", "adguard_home_ratelimit_subnet_len_ipv4"),
        ("ratelimit_subnet_len_ipv6", "adguard_home_ratelimit_subnet_len_ipv6"),
    ],
)
def test_a_site_that_narrowed_the_lengths_does_not_churn_either(
    field: str, var: str
) -> None:
    """The never-converging run: a build that omits the key read as 24, so a
    site asking for 32 reposted and reported changed on every run forever."""
    live = {f: WANTED[v] for f, v in FIELDS.items()}
    del live[field]
    assert _drifts(live, **{var: 32}) is False


@pytest.mark.parametrize(
    "field,var",
    [
        ("ratelimit_subnet_len_ipv4", "adguard_home_ratelimit_subnet_len_ipv4"),
        ("ratelimit_subnet_len_ipv6", "adguard_home_ratelimit_subnet_len_ipv6"),
    ],
)
def test_a_length_written_as_a_string_is_not_drift(field: str, var: str) -> None:
    """Inventory can spell a number either way, and an untyped comparison made
    the task permanently changed for the one that is a string."""
    live = {f: WANTED[v] for f, v in FIELDS.items()}
    live[field] = 32
    assert _drifts(live, **{var: "32"}) is False
    live[field] = 24
    assert _drifts(live, **{var: "32"}) is True


def test_an_instance_omitting_the_lengths_is_reported_not_passed() -> None:
    """A build that cannot honour the fields must not read as agreement with no
    one saying so: the drift arm is silent there, so an assert is not."""
    matches = [
        t for t in TASKS
        if str(t.get("name", "")) == "Assert AdGuard reports the rate-limiter subnet lengths"
    ]
    assert len(matches) == 1
    conditions = [str(c) for c in matches[0]["ansible.builtin.assert"]["that"]]
    assert any("'ratelimit_subnet_len_ipv4' in" in c for c in conditions)
    assert any("'ratelimit_subnet_len_ipv6' in" in c for c in conditions)
    assert "0.107.4x" in str(matches[0]["ansible.builtin.assert"]["fail_msg"])


@pytest.mark.parametrize(
    "var,expected",
    [
        ("adguard_home_ratelimit_subnet_len_ipv4", 24),
        ("adguard_home_ratelimit_subnet_len_ipv6", 56),
    ],
)
def test_the_subnet_lengths_default_to_todays_behaviour(var: str, expected: int) -> None:
    """AdGuard's own defaults, so adopting the keys changes nothing until a
    site narrows them."""
    assert WANTED[var] == expected
