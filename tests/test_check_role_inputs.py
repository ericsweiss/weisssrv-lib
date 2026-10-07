"""scripts/check-role-inputs.py — both arms find their defect and can be vacuous."""
from __future__ import annotations

from pathlib import Path

import pytest

from script_loader import load_script

gate = load_script("check-role-inputs.py")


def _tree(tmp_path: Path, *, role_defaults: str, role_tasks: str = "---\n[]\n",
          playbook: str, inventory: str) -> dict:
    roles = tmp_path / "roles" / "widget"
    (roles / "defaults").mkdir(parents=True)
    (roles / "tasks").mkdir(parents=True)
    (roles / "defaults" / "main.yml").write_text(role_defaults, encoding="utf-8")
    (roles / "tasks" / "main.yml").write_text(role_tasks, encoding="utf-8")

    playbooks = tmp_path / "playbooks"
    playbooks.mkdir()
    (playbooks / "site.yml").write_text(playbook, encoding="utf-8")

    prod = tmp_path / "inventories" / "prod" / "group_vars"
    prod.mkdir(parents=True)
    (prod / "all.yml").write_text(inventory, encoding="utf-8")
    return {
        "roles_dir": tmp_path / "roles",
        "inventory": tmp_path / "inventories" / "prod",
        "playbooks": playbooks,
    }


OPT_IN_DEFAULTS = "---\nwidget_enabled: false\n"
PLAY_UNCONDITIONAL = "---\n- hosts: all\n  roles:\n    - weisssrv.infra.widget\n"


def test_an_unconditional_opt_in_role_with_no_flag_fails(tmp_path):
    tree = _tree(tmp_path, role_defaults=OPT_IN_DEFAULTS,
                 playbook=PLAY_UNCONDITIONAL, inventory="---\nother_var: 1\n")
    problems = gate.check_opt_ins(tree["roles_dir"], tree["inventory"], tree["playbooks"])
    assert problems and "widget_enabled is set nowhere" in problems[0]


def test_setting_the_flag_in_the_inventory_passes(tmp_path):
    tree = _tree(tmp_path, role_defaults=OPT_IN_DEFAULTS,
                 playbook=PLAY_UNCONDITIONAL, inventory="---\nwidget_enabled: true\n")
    assert gate.check_opt_ins(*tree.values()) == []


def test_guarding_the_invocation_with_the_flag_passes(tmp_path):
    play = (
        "---\n- hosts: all\n  roles:\n    - role: weisssrv.infra.widget\n"
        "      when: widget_enabled | bool\n"
    )
    tree = _tree(tmp_path, role_defaults=OPT_IN_DEFAULTS, playbook=play,
                 inventory="---\nother_var: 1\n")
    assert gate.check_opt_ins(*tree.values()) == []


def test_an_include_role_task_is_seen_too(tmp_path):
    play = (
        "---\n- hosts: all\n  tasks:\n    - ansible.builtin.include_role:\n"
        "        name: weisssrv.infra.widget\n"
    )
    tree = _tree(tmp_path, role_defaults=OPT_IN_DEFAULTS, playbook=play,
                 inventory="---\nother_var: 1\n")
    assert gate.check_opt_ins(*tree.values())


def test_a_yaml_suffixed_playbook_is_scanned(tmp_path):
    """The invocation scan globs both suffixes, as the inventory scan does."""
    tree = _tree(tmp_path, role_defaults=OPT_IN_DEFAULTS,
                 playbook=PLAY_UNCONDITIONAL, inventory="---\nother_var: 1\n")
    (tree["playbooks"] / "site.yml").rename(tree["playbooks"] / "site.yaml")
    problems = gate.check_opt_ins(*tree.values())
    assert problems and "widget_enabled is set nowhere" in problems[0]


def test_an_exempt_role_is_not_reported(tmp_path):
    """A role that reconciles its disabled state is composed unconditionally."""
    tree = _tree(tmp_path, role_defaults=OPT_IN_DEFAULTS,
                 playbook=PLAY_UNCONDITIONAL, inventory="---\nother_var: 1\n")
    assert gate.check_opt_ins(
        *tree.values(), allow_disabled={"widget": "reconciles both ways"}
    ) == []


def test_an_exemption_without_a_reason_is_an_operator_error():
    with pytest.raises(gate.OperatorError):
        gate.parse_allow_disabled(["widget"])


def test_an_exemption_with_a_reason_parses():
    assert gate.parse_allow_disabled(["widget=reconciles both ways"]) == {
        "widget": "reconciles both ways"
    }


def test_a_collection_with_no_opt_in_role_is_an_operator_error(tmp_path):
    tree = _tree(tmp_path, role_defaults="---\nwidget_enabled: true\n",
                 playbook=PLAY_UNCONDITIONAL, inventory="---\n")
    with pytest.raises(gate.OperatorError):
        gate.check_opt_ins(*tree.values())


def test_playbooks_that_invoke_no_opt_in_role_is_an_operator_error(tmp_path):
    tree = _tree(tmp_path, role_defaults=OPT_IN_DEFAULTS,
                 playbook="---\n- hosts: all\n  roles:\n    - other.thing\n",
                 inventory="---\n")
    with pytest.raises(gate.OperatorError):
        gate.check_opt_ins(*tree.values())


ASSERT_TASK = """---
- name: Assert inputs
  ansible.builtin.assert:
    that:
      - widget_token | default('') | length > 0
"""


def test_an_asserted_input_with_no_default_and_no_assignment_fails(tmp_path):
    tree = _tree(tmp_path, role_defaults="---\nwidget_other: 1\n",
                 role_tasks=ASSERT_TASK, playbook=PLAY_UNCONDITIONAL,
                 inventory="---\nunrelated: 1\n")
    problems = gate.check_required_inputs(*tree.values())
    assert problems and "asserts widget_token" in problems[0]


def test_assigning_the_input_in_the_inventory_passes(tmp_path):
    tree = _tree(tmp_path, role_defaults="---\nwidget_other: 1\n",
                 role_tasks=ASSERT_TASK, playbook=PLAY_UNCONDITIONAL,
                 inventory="---\nwidget_token: abc\n")
    assert gate.check_required_inputs(*tree.values()) == []


def test_a_default_that_renders_empty_still_counts_as_missing(tmp_path):
    """The gap a raw-string test cannot see."""
    tree = _tree(tmp_path,
                 role_defaults="---\nwidget_token: \"{{ site_token | default('') }}\"\n",
                 role_tasks=ASSERT_TASK, playbook=PLAY_UNCONDITIONAL,
                 inventory="---\nunrelated: 1\n")
    problems = gate.check_required_inputs(*tree.values())
    assert problems and "renders EMPTY" in problems[0]


def test_an_assert_gated_off_by_default_is_not_required(tmp_path):
    tasks = """---
- name: Optional feature
  when: widget_feature | bool
  block:
    - name: Assert inputs
      ansible.builtin.assert:
        that:
          - widget_token | default('') | length > 0
"""
    tree = _tree(tmp_path, role_defaults="---\nwidget_feature: false\nwidget_x: 1\n",
                 role_tasks=tasks, playbook=PLAY_UNCONDITIONAL,
                 inventory="---\nunrelated: 1\n")
    with pytest.raises(gate.OperatorError):
        gate.check_required_inputs(*tree.values())


@pytest.mark.parametrize("flag_default", ["auto", "n"])
def test_a_flag_ansible_reads_false_gates_the_assert_off(tmp_path, flag_default):
    """`| bool` is Ansible's, so an unrecognised default is off here too."""
    tasks = """---
- name: Optional feature
  when: widget_feature | bool
  block:
    - name: Assert inputs
      ansible.builtin.assert:
        that:
          - widget_token | default('') | length > 0
"""
    tree = _tree(tmp_path,
                 role_defaults="---\nwidget_feature: %s\nwidget_x: 1\n" % flag_default,
                 role_tasks=tasks, playbook=PLAY_UNCONDITIONAL,
                 inventory="---\nunrelated: 1\n")
    with pytest.raises(gate.OperatorError):
        gate.check_required_inputs(*tree.values())
    assert gate.check_required_inputs(*tree.values(), allow_empty=True) == []


def test_a_group_vars_directory_layout_is_read(tmp_path):
    """group_vars/<group>/<file>.yml and .yaml carry the site's answer too."""
    tasks = """---
- name: Optional feature
  when: widget_feature | bool
  block:
    - name: Assert inputs
      ansible.builtin.assert:
        that:
          - widget_token | default('') | length > 0
"""
    tree = _tree(tmp_path, role_defaults="---\nwidget_feature: true\nwidget_x: 1\n",
                 role_tasks=tasks, playbook=PLAY_UNCONDITIONAL,
                 inventory="---\nunrelated: 1\n")
    flags = tree["inventory"] / "group_vars" / "all"
    flags.mkdir()
    (flags / "flags.yml").write_text("---\nwidget_feature: false\n", encoding="utf-8")
    with pytest.raises(gate.OperatorError):
        gate.check_required_inputs(*tree.values())


def test_a_yaml_suffixed_group_vars_file_is_read(tmp_path):
    tree = _tree(tmp_path, role_defaults="---\nwidget_other: 1\n",
                 role_tasks=ASSERT_TASK, playbook=PLAY_UNCONDITIONAL,
                 inventory="---\nunrelated: 1\n")
    (tree["inventory"] / "group_vars" / "extra.yaml").write_text(
        "---\nwidget_token: abc\n", encoding="utf-8")
    assert gate.check_required_inputs(*tree.values()) == []


def test_malformed_yaml_is_an_operator_error(tmp_path):
    tree = _tree(tmp_path, role_defaults="---\nwidget_enabled: false\n  bad: [\n",
                 playbook=PLAY_UNCONDITIONAL, inventory="---\n")
    with pytest.raises(gate.OperatorError):
        gate.check_opt_ins(*tree.values())


def test_a_vault_tagged_value_still_parses(tmp_path):
    path = tmp_path / "vaulted.yml"
    path.write_text("---\nplain: 1\nsecret: !vault |\n  $ANSIBLE_VAULT;1.1\n",
                    encoding="utf-8")
    assert gate._load(path)["plain"] == 1


def test_allow_empty_downgrades_a_vacuous_arm(tmp_path, capsys):
    tree = _tree(tmp_path, role_defaults=OPT_IN_DEFAULTS,
                 playbook="---\n- hosts: all\n  roles:\n    - other.thing\n",
                 inventory="---\n")
    assert gate.check_opt_ins(*tree.values(), allow_empty=True) == []
    assert "no opt-in role invocation was examined" in capsys.readouterr().out


def test_allow_empty_downgrades_the_required_inputs_arm(tmp_path):
    tasks = """---
- name: Optional feature
  when: widget_feature | bool
  block:
    - name: Assert inputs
      ansible.builtin.assert:
        that:
          - widget_token | default('') | length > 0
"""
    tree = _tree(tmp_path, role_defaults="---\nwidget_feature: false\nwidget_x: 1\n",
                 role_tasks=tasks, playbook=PLAY_UNCONDITIONAL,
                 inventory="---\nunrelated: 1\n")
    assert gate.check_required_inputs(*tree.values(), allow_empty=True) == []


def test_a_default_referencing_a_host_var_is_not_a_gap(tmp_path, capsys):
    """The one branch that SUPPRESSES a finding: the default renders empty only
    because the evaluator's context carries no host_vars."""
    tasks = """---
- name: Assert inputs
  ansible.builtin.assert:
    that:
      - widget_token | default('') | length > 0
"""
    tree = _tree(
        tmp_path,
        role_defaults="---\nwidget_token: \"{{ site_token | default('') }}\"\n",
        role_tasks=tasks, playbook=PLAY_UNCONDITIONAL, inventory="---\nunrelated: 1\n",
    )
    host_vars = tmp_path / "inventories" / "prod" / "host_vars"
    host_vars.mkdir()
    (host_vars / "node.yml").write_text("---\nsite_token: abc\n", encoding="utf-8")

    with pytest.raises(gate.OperatorError):
        gate.check_required_inputs(*tree.values())
    assert gate.check_required_inputs(*tree.values(), allow_empty=True) == []


def test_a_commented_out_assignment_is_not_an_assignment(tmp_path):
    """A commented flag must not satisfy the opt-in arm."""
    tree = _tree(tmp_path, role_defaults=OPT_IN_DEFAULTS,
                 playbook=PLAY_UNCONDITIONAL,
                 inventory="---\n# widget_enabled: true\nother_var: 1\n")
    problems = gate.check_opt_ins(*tree.values())
    assert problems and "widget_enabled is set nowhere" in problems[0]


def test_main_reports_an_unreadable_directory_as_an_operator_error(tmp_path):
    assert gate.main([
        "--roles-dir", str(tmp_path / "nope"),
        "--inventory", str(tmp_path), "--playbooks", str(tmp_path),
    ]) == 2


def test_main_returns_one_on_a_finding(tmp_path):
    tree = _tree(tmp_path, role_defaults=OPT_IN_DEFAULTS,
                 playbook=PLAY_UNCONDITIONAL, inventory="---\nother: 1\n")
    assert gate.main([
        "--roles-dir", str(tree["roles_dir"]),
        "--inventory", str(tree["inventory"]),
        "--playbooks", str(tree["playbooks"]),
        "--skip", "required-inputs",
        "--skip", "unknown-inputs",
    ]) == 1


BOTH_ARMS_DEFAULTS = "---\nwidget_enabled: false\n"


def _both_arms_tree(tmp_path: Path) -> dict:
    """A tree with one finding in each arm."""
    return _tree(tmp_path, role_defaults=BOTH_ARMS_DEFAULTS, role_tasks=ASSERT_TASK,
                 playbook=PLAY_UNCONDITIONAL, inventory="---\nother: 1\n")


def _main(tree: dict, *extra: str) -> int:
    return gate.main([
        "--roles-dir", str(tree["roles_dir"]),
        "--inventory", str(tree["inventory"]),
        "--playbooks", str(tree["playbooks"]),
        *extra,
    ])


def test_skip_opt_ins_still_reports_a_required_input(tmp_path, capsys):
    tree = _both_arms_tree(tmp_path)
    assert _main(tree, "--skip", "opt-ins", "--skip", "unknown-inputs") == 1
    err = capsys.readouterr().err
    assert "asserts widget_token" in err
    assert "widget_enabled is set nowhere" not in err


def test_skip_required_inputs_still_reports_an_opt_in(tmp_path, capsys):
    tree = _both_arms_tree(tmp_path)
    assert _main(tree, "--skip", "required-inputs", "--skip", "unknown-inputs") == 1
    err = capsys.readouterr().err
    assert "widget_enabled is set nowhere" in err
    assert "asserts widget_token" not in err


def test_both_arms_report_when_nothing_is_skipped(tmp_path, capsys):
    tree = _both_arms_tree(tmp_path)
    assert _main(tree, "--skip", "unknown-inputs") == 1
    err = capsys.readouterr().err
    assert "widget_enabled is set nowhere" in err and "asserts widget_token" in err


def test_skipping_every_arm_is_an_operator_error(tmp_path, capsys):
    tree = _both_arms_tree(tmp_path)
    assert _main(tree, "--skip", "opt-ins", "--skip", "required-inputs",
                 "--skip", "unknown-inputs") == 2
    assert "every arm skipped" in capsys.readouterr().err


HEALTHY_DEFAULTS = "---\nwidget_enabled: false\nwidget_token: seeded\n"

# Jinja cannot parse a dotted filter name, so this stands in for any default the
# evaluator cannot model.
UNMODELLED = "{{ x | ansible.builtin.mandatory }}"


def test_main_returns_zero_on_a_healthy_tree(tmp_path, capsys):
    """The non-finding half: the flag is answered and the input has a default."""
    tree = _tree(tmp_path, role_defaults=HEALTHY_DEFAULTS, role_tasks=ASSERT_TASK,
                 playbook=PLAY_UNCONDITIONAL, inventory="---\nwidget_enabled: true\n")
    assert _main(tree, "--allow-empty") == 0
    assert "FAIL" not in capsys.readouterr().err


def test_a_defaulted_input_is_not_reported():
    assert gate._default_gap({"widget_token": "seeded"}, "widget_token", {}, set()) is None
    plain = gate._default_gap({"widget_token": ""}, "widget_token", {}, set())
    assert "which is empty" in plain
    assert "which is empty" in gate._default_gap(
        {"widget_token": None}, "widget_token", {}, set())
    templated = gate._default_gap(
        {"widget_token": "{{ site_token | default('') }}"}, "widget_token", {}, set())
    assert "renders EMPTY against this inventory" in templated


def test_an_unmodelled_default_renders_as_unmodelled():
    assert gate._render_default(UNMODELLED, {}) is gate._UNMODELLED


def test_an_unmodelled_condition_is_not_reachability():
    assert gate._reachable_by_default(("x | ansible.builtin.mandatory",), {}) is None


def test_an_unmodelled_default_references_no_names():
    assert gate._referenced_names(UNMODELLED) == set()


def test_an_unmodelled_default_is_not_a_finding(tmp_path, capsys):
    """The fail-open arms stay silent rather than report what they cannot read."""
    tree = _tree(tmp_path,
                 role_defaults="---\nwidget_enabled: false\nwidget_token: \"%s\"\n"
                               % UNMODELLED,
                 role_tasks=ASSERT_TASK, playbook=PLAY_UNCONDITIONAL,
                 inventory="---\nwidget_enabled: true\n")
    assert _main(tree, "--allow-empty") == 0
    assert "FAIL" not in capsys.readouterr().err


def test_an_unmodelled_default_is_named_in_the_summary(tmp_path, capsys):
    """Fail-open is only safe if the dropped input is visible."""
    tree = _tree(tmp_path,
                 role_defaults="---\nwidget_enabled: false\nwidget_token: \"%s\"\n"
                               % UNMODELLED,
                 role_tasks=ASSERT_TASK, playbook=PLAY_UNCONDITIONAL,
                 inventory="---\nwidget_enabled: true\n")
    assert _main(tree, "--allow-empty") == 0
    out = capsys.readouterr().out
    assert "UNMODELLED widget: widget_token default expression not modelled" in out


def test_an_unmodelled_when_expression_is_named_in_the_summary(tmp_path, capsys):
    tasks = (
        "---\n- name: Assert inputs\n  ansible.builtin.assert:\n"
        "    that:\n      - widget_token | default('') | length > 0\n"
        "  when: widget_mode | ansible.builtin.mandatory\n"
    )
    tree = _tree(tmp_path, role_defaults="---\nwidget_other: 1\n",
                 role_tasks=tasks, playbook=PLAY_UNCONDITIONAL,
                 inventory="---\nunrelated: 1\n")
    assert _main(tree, "--allow-empty", "--skip", "opt-ins") == 0
    assert "UNMODELLED" in capsys.readouterr().out


def test_the_ansible_regex_tests_are_modelled():
    """`is match` / `is search` in a when: must not read as unmodelled."""
    assert gate._reachable_by_default(("widget_mode is search('ab')",),
                                      {"widget_mode": "xaby"}) is True
    assert gate._reachable_by_default(("widget_mode is match('ab')",),
                                      {"widget_mode": "xaby"}) is False
    assert gate._reachable_by_default(("widget_mode is match('AB', true)",),
                                      {"widget_mode": "abc"}) is True


def test_main_maps_an_operator_error_to_exit_two(tmp_path, capsys):
    tree = _both_arms_tree(tmp_path)
    assert _main(tree, "--allow-disabled", "widget") == 2
    assert "ROLE=REASON" in capsys.readouterr().err


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))


CONSUMING_TASK = (
    "---\n"
    "- name: write the widget config\n"
    "  ansible.builtin.copy:\n"
    "    content: \"{{ widget_token }}\"\n"
    "    dest: /etc/widget.conf\n"
)


def _unknown_tree(tmp_path: Path, inventory: str) -> dict:
    return _tree(tmp_path, role_defaults="---\nwidget_enabled: true\n",
                 role_tasks=CONSUMING_TASK, playbook=PLAY_UNCONDITIONAL,
                 inventory=inventory)


def test_a_consumed_role_variable_is_not_reported(tmp_path):
    tree = _unknown_tree(tmp_path, "---\nwidget_enabled: true\nwidget_token: abc\n")
    assert gate.check_unknown_inputs(*tree.values()) == []


def test_a_misspelled_role_variable_is_reported(tmp_path):
    """Mutation: one typo'd key and the arm must go red, since the role's
    `| default(...)` guard makes the real variable take its default instead."""
    tree = _unknown_tree(tmp_path, "---\nwidget_enabled: true\nwidget_tokne: abc\n")
    problems = gate.check_unknown_inputs(*tree.values())
    assert len(problems) == 1
    assert "widget_tokne" in problems[0] and "widget" in problems[0]


def test_a_variable_read_only_by_a_playbook_is_not_reported(tmp_path):
    tree = _tree(
        tmp_path, role_defaults="---\nwidget_enabled: true\n",
        playbook="---\n- hosts: all\n  roles:\n    - weisssrv.infra.widget\n"
                 "  vars:\n    inner: \"{{ widget_extra }}\"\n",
        inventory="---\nwidget_enabled: true\nwidget_extra: 1\n",
    )
    assert gate.check_unknown_inputs(*tree.values()) == []


def test_a_name_consumed_only_in_a_molecule_scenario_is_reported(tmp_path):
    tree = _unknown_tree(tmp_path, "---\nwidget_enabled: true\nwidget_fixture: 1\n")
    scenario = tree["roles_dir"] / "widget" / "molecule" / "default"
    scenario.mkdir(parents=True)
    (scenario / "converge.yml").write_text(
        "---\n- hosts: all\n  vars:\n    widget_fixture: 1\n", encoding="utf-8")
    problems = gate.check_unknown_inputs(*tree.values())
    assert len(problems) == 1 and "widget_fixture" in problems[0]


def test_a_variable_matching_no_role_prefix_is_left_alone(tmp_path):
    tree = _unknown_tree(tmp_path, "---\nwidget_enabled: true\nsite_domain: x\n")
    assert gate.check_unknown_inputs(*tree.values()) == []


def test_an_inventory_with_no_role_prefixed_variable_is_an_operator_error(tmp_path):
    tree = _unknown_tree(tmp_path, "---\nsite_domain: x\n")
    with pytest.raises(gate.OperatorError):
        gate.check_unknown_inputs(*tree.values())
    assert gate.check_unknown_inputs(*tree.values(), allow_empty=True) == []


def test_an_exempt_unknown_variable_is_not_reported(tmp_path):
    tree = _unknown_tree(tmp_path, "---\nwidget_enabled: true\nwidget_tokne: abc\n")
    assert gate.check_unknown_inputs(
        *tree.values(), allow_unknown={"widget_tokne": "read by an external tool"}
    ) == []


def test_the_longest_role_prefix_owns_the_variable(tmp_path):
    tree = _unknown_tree(tmp_path, "---\nwidget_enabled: true\nwidget_proxy_mode: on\n")
    (tree["roles_dir"] / "widget_proxy" / "defaults").mkdir(parents=True)
    (tree["roles_dir"] / "widget_proxy" / "defaults" / "main.yml").write_text(
        "---\nwidget_proxy_enabled: true\n", encoding="utf-8")
    problems = gate.check_unknown_inputs(*tree.values())
    assert len(problems) == 1
    assert "the widget_proxy role" in problems[0]


def test_main_returns_one_on_an_unknown_input(tmp_path, capsys):
    tree = _unknown_tree(tmp_path, "---\nwidget_enabled: true\nwidget_tokne: abc\n")
    assert _main(tree, "--skip", "opt-ins", "--skip", "required-inputs") == 1
    assert "widget_tokne" in capsys.readouterr().err
