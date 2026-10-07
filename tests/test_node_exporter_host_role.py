"""node_exporter_host's rendered defaults files, and the scenario proving them.

`ansible_managed` resolves only inside the template module from ansible-core
2.21 on, so a `copy: content:` carrying it fails the play.
"""

from __future__ import annotations

from pathlib import Path

import jinja2
import yaml
from _helpers import ansible_env

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "node_exporter_host"
TEMPLATES = ROLE / "templates"
SCENARIO = ROLE / "molecule" / "default"
VERIFY = SCENARIO / "verify.yml"
CONVERGE = SCENARIO / "converge.yml"
DECLARATION = SCENARIO / "expected-junit-failures.txt"

MANAGED_HEADER = "# {{ ansible_managed }}"

# dest -> (task file, template, the assignment the file exists to carry)
RENDERED_DEFAULTS = {
    "/etc/default/slabinfo-collector": (
        "main.yml",
        "slabinfo-collector.defaults.j2",
        'SLAB_CACHES="skbuff_ext_cache dentry"',
    ),
    "/etc/default/vzdump-metrics-hook": (
        "vzdump_hook.yml",
        "vzdump-metrics-hook.defaults.j2",
        'TEXTFILE_DIR="/var/lib/node_exporter"',
    ),
}

RENDER_VARS = {
    "ansible_managed": "Ansible managed",
    "node_exporter_host_slab_caches": ["skbuff_ext_cache", "dentry"],
    "node_exporter_host_textfile_dir": "/var/lib/node_exporter",
}


# A playbook nests its tasks one level deeper than a role task file.
NESTS = ("block", "rescue", "always", "pre_tasks", "tasks", "post_tasks")


def _tasks(path: Path) -> list:
    """Every task in one file, flattened through blocks and play sections."""
    out = []
    pending = [t for t in (yaml.safe_load(path.read_text()) or []) if isinstance(t, dict)]
    while pending:
        task = pending.pop()
        out.append(task)
        for key in NESTS:
            pending.extend(t for t in task.get(key) or [] if isinstance(t, dict))
    return out


def _all_role_tasks() -> list:
    out = []
    for path in sorted((ROLE / "tasks").glob("*.yml")):
        out.extend((path, task) for task in _tasks(path))
    return out


def _task_for_dest(path: Path, dest: str) -> dict:
    matches = [
        task
        for task in _tasks(path)
        for args in (task.get("ansible.builtin.template") or task.get("ansible.builtin.copy") or {},)
        if args.get("dest") == dest
    ]
    assert len(matches) == 1, f"expected exactly one task writing {dest} in {path}"
    return matches[0]


def test_no_task_hands_ansible_managed_to_a_non_template_module() -> None:
    """The core-2.21 trap: only the template module defines ansible_managed, so
    a copy/lineinfile/set_fact referencing it fails the play outright."""
    offenders = []
    for path, task in _all_role_tasks():
        for module, args in task.items():
            if module == "ansible.builtin.template" or not isinstance(args, dict):
                continue
            if "ansible_managed" in yaml.safe_dump(args):
                offenders.append(f"{path.name}: {task.get('name')} ({module})")
    assert offenders == [], f"ansible_managed outside the template module: {offenders}"


def test_each_defaults_file_is_rendered_from_a_template() -> None:
    for dest, (task_file, template, _) in RENDERED_DEFAULTS.items():
        task = _task_for_dest(ROLE / "tasks" / task_file, dest)
        assert "ansible.builtin.template" in task, f"{dest} is not written by template"
        assert task["ansible.builtin.template"]["src"] == template


def test_each_template_opens_with_the_managed_header() -> None:
    for _, template, _ in RENDERED_DEFAULTS.values():
        first = (TEMPLATES / template).read_text().splitlines()[0]
        assert first == MANAGED_HEADER, f"{template} does not open with {MANAGED_HEADER}"


def test_each_template_renders_its_header_and_its_assignment() -> None:
    """Rendered with a StrictUndefined environment: a template reading a
    variable the role does not define would raise here rather than in CI."""
    env = ansible_env(undefined=jinja2.StrictUndefined)
    for _, template, assignment in RENDERED_DEFAULTS.values():
        rendered = env.from_string((TEMPLATES / template).read_text()).render(**RENDER_VARS)
        assert rendered.splitlines()[0] == "# Ansible managed"
        assert assignment in rendered


def test_the_scenario_asserts_both_rendered_managed_headers() -> None:
    """Without these the header could silently render empty again."""
    names = [str(task.get("name", "")) for task in _tasks(VERIFY)]
    for subject in ("slabinfo allowlist", "hookscript defaults file"):
        assert any(
            f"Assert the {subject} carries a rendered managed header" == name for name in names
        ), f"verify.yml no longer asserts the {subject} managed header"


def test_the_scenario_never_re_renders_ansible_managed_itself() -> None:
    """verify.yml runs outside any role, so it has no template context either."""
    assert "{{ ansible_managed" not in VERIFY.read_text()


def test_the_scenario_pins_the_textfile_dir_it_asserts() -> None:
    """verify.yml loads no role defaults, so the variable its assert reads has
    to come from the scenario's group_vars."""
    group_vars = yaml.safe_load((SCENARIO / "molecule.yml").read_text())
    pinned = group_vars["provisioner"]["inventory"]["group_vars"]["all"]
    assert pinned["node_exporter_host_textfile_dir"] == RENDER_VARS[
        "node_exporter_host_textfile_dir"
    ]
    assert "node_exporter_host_textfile_dir" in VERIFY.read_text()


# The role assert converge's negative case drives to failure.
SLAB_INPUT_GUARD = "Assert the slabinfo collector inputs are consistent"
NEGATIVE_CASE = "Re-run with an empty slab cache allowlist"


def test_the_converge_negative_failure_is_declared_for_junit() -> None:
    """Undeclared, the rescued guard leaves a red junit report on a green job;
    the fix is what first lets converge reach the negative case at all."""
    declared = [
        line.strip()
        for line in DECLARATION.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert declared == [SLAB_INPUT_GUARD]
    assert any(task.get("name") == SLAB_INPUT_GUARD for _, task in _all_role_tasks())


def test_the_negative_case_is_skipped_on_the_idempotence_run() -> None:
    """Molecule replays converge for the idempotence step, so an untagged block
    fires its guard twice and records two junit failures where the declaration
    allows one — a green molecule run, a red job."""
    scenario = yaml.safe_load((SCENARIO / "molecule.yml").read_text())
    assert "idempotence" in scenario["scenario"]["test_sequence"]
    block = next(
        task for task in _tasks(CONVERGE)
        if str(task.get("name", "")).startswith(NEGATIVE_CASE)
    )
    assert "molecule-idempotence-notest" in (block.get("tags") or [])
    assert block.get("rescue"), f"{NEGATIVE_CASE} is no longer rescued"
