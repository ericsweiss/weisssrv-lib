"""Every file the nas_storage deploy tasks write is removed when the component
is switched off, so a disabled component leaves no timer or frozen metric.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "nas_storage"
TASKS = ROLE / "tasks"

# Deploy file -> the units and scripts it writes that the opt-out must remove.
# textfile_collector renders the units for a collector, so those paths are
# named by the include's textfile_collector_name rather than a dest:.
DEPLOY_FILES = (
    "media_mover.yml",
    "archive_backup.yml",
    "swap_clean.yml",
    "pve_cluster_backup.yml",
    "backup_metrics.yml",
    "mergerfs.yml",
)
_DEST = re.compile(r"^\s*dest:\s*(/(?:etc/systemd/system|usr/local/sbin)/\S+)\s*$", re.M)
_COLLECTOR = re.compile(r"^\s*textfile_collector_name:\s*(\S+)\s*$", re.M)
# The shared metrics library is deployed by the compose_app include, so its path
# is an include var rather than a dest:. Both sides name the same variable.
_LIB = re.compile(r"^\s*compose_app_backup_lib_dest:\s*(.+?)\s*$", re.M)

# Task file -> why it writes a unit yet is not an opt-out component.
NOT_A_COMPONENT = {
    "nfs.yml": "NFS is unconditional; the drop-in is not an opt-out component",
}


def derived_files() -> set:
    """Every task file that renders a unit or script, so a new one cannot slip out."""
    found = set()
    for path in TASKS.glob("*.yml"):
        if path.name == "main.yml":
            continue
        text = path.read_text(encoding="utf-8")
        if _DEST.search(text) or _COLLECTOR.search(text):
            found.add(path.name)
    return found


def deployed_paths() -> set:
    found = set()
    for name in DEPLOY_FILES:
        text = (TASKS / name).read_text(encoding="utf-8")
        found |= set(_DEST.findall(text))
        found |= {path.strip('"\'') for path in _LIB.findall(text)}
        for collector in _COLLECTOR.findall(text):
            found.add("/etc/systemd/system/%s.service" % collector)
            found.add("/etc/systemd/system/%s.timer" % collector)
    return found


# deprovision_units.yml derives each unit's file from its name, so a call site
# lists only the component's other files.
UNIT_KEYS = (
    "nas_storage_deprovision_timers",
    "nas_storage_deprovision_services",
    "nas_storage_deprovision_static_services",
)


def deprovisioned_paths() -> set:
    tasks = yaml.safe_load((TASKS / "main.yml").read_text(encoding="utf-8"))
    found = set()
    for task in tasks:
        if task.get("ansible.builtin.include_tasks") != "deprovision_units.yml":
            continue
        variables = task.get("vars", {})
        found |= set(variables.get("nas_storage_deprovision_paths", []))
        for key in UNIT_KEYS:
            found |= {"/etc/systemd/system/%s" % unit for unit in variables.get(key, [])}
    return found


def test_the_derived_list_matches_the_expectation():
    """A new component task file must be added to DEPLOY_FILES or exempted."""
    unlisted = sorted(derived_files() - set(NOT_A_COMPONENT) - set(DEPLOY_FILES))
    assert unlisted == [], (
        "these task files write a unit or script but are compared against "
        "nothing — add them to DEPLOY_FILES, or to NOT_A_COMPONENT with a "
        "reason: " + ", ".join(unlisted)
    )


def test_every_exemption_carries_a_reason():
    for name, reason in NOT_A_COMPONENT.items():
        assert reason.strip(), "%s is exempted with no reason" % name
        assert (TASKS / name).exists(), "%s no longer exists; drop the exemption" % name


def test_the_scan_finds_paths_to_compare():
    """A regex that matched nothing would make the comparison vacuous."""
    assert len(deployed_paths()) >= 10
    assert len(deprovisioned_paths()) >= 10


def test_every_deployed_unit_and_script_is_deprovisioned():
    missing = sorted(deployed_paths() - deprovisioned_paths())
    assert missing == [], (
        "these are written by a deploy task but never removed when the "
        "component is disabled: " + ", ".join(missing)
    )


def test_no_deprovisioned_path_is_orphaned():
    """A path no deploy task writes is a rename left behind on one side."""
    orphans = sorted(deprovisioned_paths() - deployed_paths())
    assert orphans == [], (
        "these are removed on opt-out but written by no deploy task: "
        + ", ".join(orphans)
    )


def _unit_template(unit: str) -> Path:
    return ROLE / "templates" / ("%s.j2" % unit)


def deprovision_service_keys() -> dict:
    """Service name -> the key its caller lists it under."""
    tasks = yaml.safe_load((TASKS / "main.yml").read_text(encoding="utf-8"))
    keys = {}
    for task in tasks:
        if task.get("ansible.builtin.include_tasks") != "deprovision_units.yml":
            continue
        variables = task.get("vars", {})
        for key in ("nas_storage_deprovision_services",
                    "nas_storage_deprovision_static_services"):
            for unit in variables.get(key, []):
                keys[unit] = key
    return keys


def misfiled_services(keys: dict) -> list:
    """Units listed under the wrong key for whether they have an [Install]."""
    wrong = []
    for unit, key in sorted(keys.items()):
        template = _unit_template(unit)
        if not template.exists():
            continue
        installable = "[Install]" in template.read_text(encoding="utf-8")
        expected = ("nas_storage_deprovision_services" if installable
                    else "nas_storage_deprovision_static_services")
        if key != expected:
            wrong.append("%s: listed under %s, expected %s" % (unit, key, expected))
    return wrong


def test_the_service_key_scan_finds_units_to_compare():
    assert len(deprovision_service_keys()) >= 5


def test_every_deprovisioned_service_is_under_the_right_key():
    wrong = misfiled_services(deprovision_service_keys())
    assert wrong == [], (
        "a unit with no [Install] section is stop-only: `enabled: false` "
        "reports changed and disables nothing. " + "; ".join(wrong)
    )


def test_a_static_unit_under_the_disable_key_is_reported():
    """Mutation: the comparison must report a static unit moved to the wrong key."""
    keys = dict(deprovision_service_keys())
    static = [unit for unit, key in keys.items()
              if key == "nas_storage_deprovision_static_services"
              and _unit_template(unit).exists()]
    assert static, "no static service to mutate"
    keys[static[0]] = "nas_storage_deprovision_services"
    assert any(static[0] in row for row in misfiled_services(keys))


def test_a_renamed_unit_on_one_side_is_reported():
    """Mutation: the comparison must not pass when the two sides disagree."""
    deployed = deployed_paths()
    renamed = {p for p in deployed if not p.endswith("media-mover.timer")}
    renamed.add("/etc/systemd/system/media-mover-v2.timer")
    assert renamed - deprovisioned_paths()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
