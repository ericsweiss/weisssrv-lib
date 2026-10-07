"""House style for every scenario's junit declarations: the tag a converge-driven
negative case carries, and the ` ::<n>` count its guard records in one run.
"""
from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import yaml
from script_loader import load_script

REPO = Path(__file__).resolve().parent.parent
ROLES = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles"
SHARED_BASE = ROLES.parent / "molecule-shared" / "base.yml"

# hosts x cases rests on this tag: it keeps the idempotence replay from
# re-firing a converge-driven guard, which would record the case twice.
IDEMPOTENCE_SKIP = "molecule-idempotence-notest"

# The consuming script owns the line grammar, including the ` ::<n>` count form.
_SANITIZE = load_script("sanitize-junit-expected-failures.py")


class RunOnce(NamedTuple):
    """A guard reached under `run_once: true`: its count is the cases alone."""

    cases: int


# scenario -> guard pattern -> distinct cases driving that guard in one run.
# Converge and verify cases count the same: once per host per case, unless the
# guard is wrapped in RunOnce.
CASES = {
    "adguard_sync/default": {
        "Validate every entry in adguard_sync_replicas carries a URL": 1,
    },
    "alloy_host/default": {
        "Assert Loki push credentials are present": 1,
    },
    "apt_signed_repo/default": {
        # The wrong fingerprint, then the bundled second key.
        "Verify downloaded signing key fingerprint": 2,
        "Verify existing signing keyring fingerprint": 1,
        "Assert a component set was requested": 1,
        "Fail when the deb822 sources file is absent": 1,
        "Assert the sources file carries a Components line to rewrite": 1,
    },
    "base/default": {
        "Assert every kernel cmdline argument is a single bare token": 1,
        # Verify-driven: the systemd-boot command line.
        "Assert the host takes its kernel command line from GRUB": 1,
    },
    "compose_app/default": {
        # The broken candidate, then the partial sites-enabled merge.
        "Validate the candidate against the merged nginx configuration": 2,
    },
    "docker_engine/default": {
        "Validate the pinned Docker versions are supplied": 1,
    },
    "gitlab/default": {
        "Validate the real-IP trust list resolved": 1,
        "Validate the SMTP credentials": 1,
        # Verify-driven: the stubbed-iptables read.
        "Count SSH redirect rules in": 1,
    },
    "immich/default": {
        "Validate the real-IP trust list resolved": 1,
        "Validate the Postgres exporter image pin": 1,
    },
    "k3s/default": {
        "Assert the kube-vip interface exists on this server": 1,
        "Validate required k3s variables": 1,
        "Assert the off-node snapshot NFS target is configured": 1,
        # Verify-driven, on both server platforms: the bootstrap-intent cases.
        "Assert a greenfield bootstrap was asked for before cluster-init": 1,
        "Assert the server group resolved before deciding the bootstrap mode": 1,
    },
    "k3s/agent": {
        "Validate required k3s variables": 1,
    },
    "node_exporter_host/default": {
        "Assert the slabinfo collector inputs are consistent": 1,
    },
    "prometheus_exporter/default": {
        "Assert the caller supplied every required prometheus_exporter parameter": 1,
        "Assert the caller supplied a health-check port": 1,
    },
    "proxmox_backup/default": {
        # The drifted NFS server, then the zfspool pool moved to the pool root.
        "Assert create-fixed storage properties have not drifted": 2,
    },
    "proxmox_firewall/default": {
        # A mapping `sources`, then an entry with no sources key.
        "Assert the sg-dns admin-port entries carry a port and a list of sources": 2,
        # A scalar `sources`, then a valueless `port`.
        "Assert the sg-metrics scrape-port entries carry a port and a list of sources": 2,
        # A scalar list, two empty lists, a null and an empty-string element,
        # a punctuated name and a name under the ipset grammar's minimum.
        "Assert the client-scope source lists are non-empty lists of set names": 7,
        "Assert the smtp-relay client scope is a list of set names": 1,
        # The relay egress entry with no port, then the host egress entry with
        # no proto: one guard covers both inputs.
        "Assert the extra egress ports carry a port and a protocol": 2,
        "Assert every IPSet special entry carries an address": 1,
        # A name the role already renders, an over-long one, a leading digit.
        "Assert every per-application security group name is usable and unowned": 3,
        "Assert the per-application security group names are unique": 1,
        "Assert the per-host extra security group references are group names": 1,
        # main.yml reaches assert_cluster_scope.yml under run_once and verify
        # includes it directly without, so the file-level read cannot settle
        # these two; both driving plays select one host either way.
        "Assert no cluster-scope firewall input is scoped to the Proxmox group": 1,
        "Assert a cluster with a relay guest names the sg-smtp-relay client scope": 1,
        "Assert a reachable Proxmox node was found for the cluster-scope tasks": RunOnce(1),
        # The role's own publish task, driven against a pve-firewall stub that
        # prints a parse error and exits 0; no run_once on it.
        "Validate the compiled ruleset after publishing": 1,
        # Converge-driven; guest.yml carries no run_once.
        "Assert a relay guest has a client scope for sg-smtp-relay": 1,
    },
    "proxmox_ha/default": {
        # run_once on the assert itself; main.yml includes rules.yml under it.
        "Assert a reachable Proxmox node was found for the reconcile": RunOnce(1),
        "Assert every HA rule is a supported type": RunOnce(1),
        "Ensure we're running on a Proxmox VE cluster": 1,
        "Get current storage replication jobs": 1,
    },
    "proxmox_lxc/default": {
        "Validate resource pool is in standard list": 1,
        "Fail if required storage pool does not exist": 1,
        # An empty gateway, then a disk size that is not a GiB value.
        "Assert the required LXC create inputs are set": 2,
    },
    "proxmox_vm/default": {
        "Validate resource pool is in standard list": 1,
        "Fail if required storage pool does not exist": 1,
        "Attach additional disks to VM": 1,
        # A duplicate slot, a missing slot and the boot-disk slot.
        "Assert every additional disk pins a unique, explicit scsi_slot": 3,
        "Assert the additional-disk backend is implemented": 1,
        "Fail when no Proxmox host answered the cluster-resource query": 1,
        "Ensure the guest NIC has the firewall flag": 1,
        "Reconcile VM memory allocation": 1,
    },
    "restic_offsite/default": {
        "Refuse secret values the env file and restic would read differently": 1,
        "Assert swap-clean is interlocked against the offsite run": 1,
    },
    "tailscale/default": {
        "Fail when a Tailscale join is required but TAILSCALE_AUTH_KEY is unset": 1,
    },
    "vfio_passthrough/default": {
        "Assert the kernel-cmdline method is implemented": 1,
        "Assert the managed-file marker is usable": 1,
    },
    "zvol_mount/default": {
        "Assert a blank-looking disk carries no partition table either": 1,
    },
}

# Converge-driven negative cases CASES does not describe yet; an explicit list,
# so the completeness check names the gap rather than ignoring it.
PENDING_SCENARIOS: set[str] = set()


def scenario_dir(scenario: str) -> Path:
    role, name = scenario.split("/")
    return ROLES / role / "molecule" / name


def every_scenario() -> list[str]:
    """Every molecule scenario in the collection, as `role/name`."""
    return [
        f"{path.parents[2].name}/{path.parent.name}"
        for path in sorted(ROLES.glob("*/molecule/*/converge.yml"))
    ]


def molecule_config(scenario: str) -> dict:
    return yaml.safe_load((scenario_dir(scenario) / "molecule.yml").read_text())


def inventory_hosts(config: dict) -> dict[str, set[str]]:
    """host -> its groups, over the platforms and the inventory stand-ins.

    A stand-in is an ordinary inventory host: the junit callback records a
    testcase for it like any other.
    """
    found: dict[str, set[str]] = {}
    for platform in config["platforms"]:
        found[platform["name"]] = {"all", *(platform.get("groups") or [])}
    stand_ins = (config.get("provisioner", {}).get("inventory") or {}).get("hosts") or {}
    for group, members in stand_ins.items():
        if isinstance(members, dict) and "hosts" in members:
            members = members["hosts"]
        for name in members or {}:
            found.setdefault(name, {"all"}).add(group)
    assert found, "the scenario declares no hosts at all"
    return found


def selected_hosts(config: dict, pattern: str) -> set[str]:
    """The hosts a play's `hosts:` selects: `all`, a group, or one host."""
    known = inventory_hosts(config)
    members = {name for name, groups in known.items() if pattern in groups}
    if members:
        return members
    assert pattern in known, f"play pattern {pattern!r} selects nothing"
    return {pattern}


def driving_host_counts(scenario: str) -> dict[str, int]:
    """`hosts:` pattern -> hosts it selects, per play driving a negative case."""
    config = molecule_config(scenario)
    counts: dict[str, int] = {}
    for name in ("converge.yml", "verify.yml"):
        path = scenario_dir(scenario) / name
        if not path.is_file():
            continue
        for play in yaml.safe_load(path.read_text()):
            if rescued_in(play):
                counts[play["hosts"]] = len(selected_hosts(config, play["hosts"]))
    assert counts, f"{scenario} drives no negative case from any play"
    return counts


def hosts(scenario: str) -> int:
    """Hosts every negative case runs on: the junit callback writes one each."""
    counts = set(driving_host_counts(scenario).values())
    assert len(counts) == 1, f"{scenario} mixes host counts: {driving_host_counts(scenario)}"
    return counts.pop()


def converge_runs(scenario: str) -> int:
    """Converge executions per molecule run: the step plus every replay of it."""
    own = molecule_config(scenario).get("scenario", {}).get("test_sequence")
    shared = yaml.safe_load(SHARED_BASE.read_text())["scenario"]["test_sequence"]
    sequence = own or shared
    return sequence.count("converge") + sequence.count("idempotence")


def rescued_in(node) -> list[dict]:
    """Every block that rescues a failure: one negative case each."""
    found: list[dict] = []

    def walk(current) -> None:
        if isinstance(current, dict):
            if "block" in current and "rescue" in current:
                found.append(current)
            for value in current.values():
                walk(value)
        elif isinstance(current, list):
            for value in current:
                walk(value)

    walk(node)
    return found


def rescued_blocks(playbook: Path) -> list[dict]:
    return rescued_in(yaml.safe_load(playbook.read_text()))


def untagged(playbook: Path) -> list[str]:
    """Converge-driven negative cases the idempotence replay would re-fire."""
    return [
        str(block.get("name"))
        for block in rescued_blocks(playbook)
        if IDEMPOTENCE_SKIP not in (block.get("tags") or [])
    ]


PLAYBOOKS = ("converge.yml", "verify.yml", "prepare.yml", "side_effect.yml")
INCLUDE_KEYS = ("include_tasks", "import_tasks",
                "ansible.builtin.include_tasks", "ansible.builtin.import_tasks")
ROLE_KEYS = ("include_role", "import_role",
             "ansible.builtin.include_role", "ansible.builtin.import_role")


def walk_tasks(node, inherited: bool = False) -> list[tuple[str | None, bool, dict]]:
    """(name, run_once, task) for every task, run_once inherited from blocks."""
    found: list[tuple[str | None, bool, dict]] = []
    if isinstance(node, list):
        for item in node:
            found += walk_tasks(item, inherited)
        return found
    if not isinstance(node, dict):
        return found
    effective = inherited or bool(node.get("run_once"))
    if "block" in node:
        for key in ("block", "rescue", "always"):
            found += walk_tasks(node.get(key) or [], effective)
        return found
    found.append((node.get("name") if isinstance(node.get("name"), str) else None,
                  effective, node))
    return found


def file_tasks(path: Path) -> list[tuple[str | None, bool, dict]]:
    """Tasks in a role task file, or in every play of a scenario playbook."""
    document = yaml.safe_load(path.read_text()) or []
    if path.name not in PLAYBOOKS:
        return walk_tasks(document)
    found: list[tuple[str | None, bool, dict]] = []
    for play in document:
        for key in ("pre_tasks", "tasks", "post_tasks"):
            found += walk_tasks(play.get(key) or [])
    return found


def task_sources(scenario: str) -> list[Path]:
    """Files whose tasks this scenario can reach: the role's, and its own."""
    role = ROLES / scenario.split("/")[0]
    candidates = sorted((role / "tasks").glob("*.yml"))
    candidates += [scenario_dir(scenario) / name for name in PLAYBOOKS]
    return [path for path in candidates if path.is_file()]


def included_file(task: dict) -> str | None:
    """The task file an include reaches, by basename."""
    for key in INCLUDE_KEYS:
        spec = task.get(key)
        if isinstance(spec, str):
            return Path(spec).name
        if isinstance(spec, dict) and isinstance(spec.get("file"), str):
            return Path(spec["file"]).name
    for key in ROLE_KEYS:
        spec = task.get(key)
        if isinstance(spec, dict):
            entry = spec.get("tasks_from")
            if not isinstance(entry, str):
                return "main.yml"
            return entry if entry.endswith(".yml") else f"{entry}.yml"
    return None


def reaching_run_once(scenario: str) -> dict[str, set[bool]]:
    """Task file -> the run_once values of every include that reaches it."""
    found: dict[str, set[bool]] = {}
    for path in task_sources(scenario):
        for _name, run_once, task in file_tasks(path):
            target = included_file(task)
            if target:
                found.setdefault(target, set()).add(run_once)
    return found


def run_once_for(scenario: str, pattern: str) -> bool | None:
    """Whether the guard records one testcase per play; None when the task
    cannot be resolved soundly (no match, several, or an ambiguous include)."""
    hits = [
        (path, run_once)
        for path in task_sources(scenario)
        for name, run_once, _task in file_tasks(path)
        if name and pattern in name
    ]
    if len(hits) != 1:
        return None
    path, run_once = hits[0]
    if run_once:
        return True
    if path.name in PLAYBOOKS:
        return False
    reaching = reaching_run_once(scenario).get(path.name)
    if not reaching or len(reaching) != 1:
        return None
    return reaching.pop()


# A case driven under `ignore_errors` is recorded as PASSED, so the sanitizer
# never observes it and it is never declared. Drive a negative case with
# block/rescue instead.
def declared(scenario: str) -> dict[str, int]:
    path = scenario_dir(scenario) / "expected-junit-failures.txt"
    expectations = _SANITIZE.load_expectations(path)
    assert expectations, f"{path} declares nothing — the gate is vacuous"
    return dict(expectations)


def declared_lines(scenario: str) -> list[str]:
    """The declaration file's lines as written, comments and blanks dropped."""
    path = scenario_dir(scenario) / "expected-junit-failures.txt"
    return [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_the_table_describes_scenarios_to_check():
    """Guard the guard: an empty table makes every assertion below vacuous."""
    assert len(CASES) > 15
    assert all(guards for guards in CASES.values())


def test_only_a_scenarios_own_sequence_can_drop_the_replay():
    """The shared sequence replays converge, so a scenario that does not must
    say so in its own test_sequence rather than by losing the shared one."""
    for scenario in CASES:
        own = molecule_config(scenario).get("scenario", {}).get("test_sequence")
        if converge_runs(scenario) == 1:
            assert own, f"{scenario} runs converge once without declaring why"
        else:
            assert converge_runs(scenario) == 2, scenario


def test_a_play_pattern_resolves_to_the_hosts_it_selects():
    """Soundness: a scenario may add inventory stand-ins, and a play naming a
    group gets a testcase per member, not one per platform."""
    config = {
        "platforms": [{"name": "pf-test", "groups": ["proxmox"]}],
        "provisioner": {"inventory": {"hosts": {
            "proxmox": {"hosts": {"pve-a": {}, "pve-b": {}}},
            "dns": {"hosts": {"dns-01": {}}},
        }}},
    }
    assert selected_hosts(config, "all") == {"pf-test", "pve-a", "pve-b", "dns-01"}
    assert selected_hosts(config, "proxmox") == {"pf-test", "pve-a", "pve-b"}
    assert selected_hosts(config, "dns") == {"dns-01"}
    assert selected_hosts(config, "pf-test") == {"pf-test"}


def test_each_scenario_drives_its_negative_cases_on_one_host_count():
    """`hosts()` is one number per scenario. A scenario driving one case from a
    play that selects stand-ins and another from a platform-scoped play would
    need a host count per guard instead."""
    mixed = [
        f"{scenario}: {driving_host_counts(scenario)}"
        for scenario in CASES
        if len(set(driving_host_counts(scenario).values())) != 1
    ]
    assert not mixed, "\n  ".join(
        ["negative cases run on differing host counts:", *mixed]
    )


def test_every_converge_driven_negative_case_is_tagged():
    """An untagged block fires its guard again on the replay, so the run records
    hosts x cases x converge runs and --strict rejects the honest count."""
    offenders = [
        f"{scenario}: {name}"
        for scenario in every_scenario()
        for name in untagged(scenario_dir(scenario) / "converge.yml")
    ]
    assert not offenders, "\n  ".join(
        [f"negative cases missing the {IDEMPOTENCE_SKIP} tag:", *offenders]
    )


def test_every_scenario_with_a_converge_driven_case_is_described():
    """A new negative case must land in the table, not only in the scenario."""
    missing = [
        scenario
        for scenario in every_scenario()
        if rescued_blocks(scenario_dir(scenario) / "converge.yml")
        and scenario not in CASES
        and scenario not in PENDING_SCENARIOS
    ]
    assert not missing, (
        "converge drives a negative case these scenarios do not describe:\n  "
        + "\n  ".join(missing)
    )


def expected_count(scenario: str, cases: int | RunOnce) -> int:
    """The testcases one run records for a guard: cases, x hosts unless run_once."""
    if isinstance(cases, RunOnce):
        return cases.cases
    return hosts(scenario) * cases


def reason(scenario: str, cases: int | RunOnce) -> str:
    if isinstance(cases, RunOnce):
        return f"{cases.cases} case(s), run_once"
    return f"{hosts(scenario)} host(s) x {cases} case(s)"


def wrong_counts(
    scenario: str,
    counts: dict[str, int],
    guards: dict[str, int | RunOnce] | None = None,
) -> list[str]:
    """Declared counts that are not what one run records for their guard."""
    return [
        f"{scenario}: {pattern!r} declares {counts.get(pattern)}, "
        f"expected {reason(scenario, cases)}"
        for pattern, cases in (CASES[scenario] if guards is None else guards).items()
        if counts.get(pattern) != expected_count(scenario, cases)
    ]


def test_every_declaration_counts_hosts_times_cases():
    wrong = [line for scenario in CASES
             for line in wrong_counts(scenario, declared(scenario))]
    assert not wrong, "\n  ".join(["declared count is not hosts x cases:", *wrong])


def test_a_single_testcase_is_declared_as_a_plain_line():
    """A ` ::1` suffix reads as a count the file did not need to state."""
    redundant = [
        f"{scenario}: {line!r}"
        for scenario in CASES
        for line in declared_lines(scenario)
        if line.endswith(" ::1")
    ]
    assert not redundant, "\n  ".join(
        ["one testcase is the default, so the count is noise:", *redundant]
    )


def test_every_run_once_marker_matches_the_task_it_names():
    """The marker is a claim about the role, so read it back off the task — or
    the include that reaches it — rather than trusting the table."""
    disagree: list[str] = []
    readable = 0
    for scenario, guards in CASES.items():
        for pattern, cases in guards.items():
            derived = run_once_for(scenario, pattern)
            if derived is None:
                continue
            readable += 1
            if derived != isinstance(cases, RunOnce):
                disagree.append(
                    f"{scenario}: {pattern!r} runs run_once={derived}, "
                    f"the table says {isinstance(cases, RunOnce)}"
                )
    assert not disagree, "\n  ".join(["run_once disagrees with the task:", *disagree])
    assert readable > 40, f"only {readable} guards resolved — the read is vacuous"


def test_every_run_once_marker_is_confirmed_not_merely_unrefuted():
    """An unresolvable guard stays on the default rule: a marker the read cannot
    confirm would be an unchecked claim about the count."""
    unconfirmed = [
        f"{scenario}: {pattern!r}"
        for scenario, guards in CASES.items()
        for pattern, cases in guards.items()
        if isinstance(cases, RunOnce) and run_once_for(scenario, pattern) is not True
    ]
    assert not unconfirmed, "\n  ".join(
        ["RunOnce marker the task does not confirm:", *unconfirmed]
    )
    assert any(
        isinstance(cases, RunOnce)
        for guards in CASES.values()
        for cases in guards.values()
    ), "no guard is marked run_once, so the check proves nothing"


def test_the_declarations_cover_exactly_the_guards_named_here():
    """A new negative case must land in CASES, not only in the scenario."""
    for scenario, guards in CASES.items():
        assert set(declared(scenario)) == set(guards), scenario


def test_a_declaration_carrying_the_replay_factor_is_reported():
    """Mutation: the count the tag retires — hosts x cases x converge runs."""
    checked = 0
    for scenario, guards in CASES.items():
        if converge_runs(scenario) == 1:
            continue
        replayed = {
            pattern: expected_count(scenario, cases) * converge_runs(scenario)
            for pattern, cases in guards.items()
        }
        assert wrong_counts(scenario, replayed), scenario
        checked += 1
    assert checked, "no scenario replays converge, so the mutation proves nothing"


def test_a_run_once_guard_declared_per_host_is_reported():
    """Mutation: run_once records one testcase however many hosts the play
    selects, and the sanitizer reads a declaration as a cap — so a hosts x cases
    count passes --strict while swallowing a second, genuine failure."""
    scenario = "k3s/default"
    assert hosts(scenario) == 2, "the mutation needs a scenario with two hosts"
    guards: dict[str, int | RunOnce] = {"Assert something once": RunOnce(1)}
    assert wrong_counts(scenario, {"Assert something once": 2}, guards)
    assert not wrong_counts(scenario, {"Assert something once": 1}, guards)


def test_an_untagged_block_is_reported(tmp_path):
    """Mutation: the tag is what the count rule rests on."""
    playbook = tmp_path / "converge.yml"
    playbook.write_text(
        "- name: Converge\n"
        "  hosts: all\n"
        "  tasks:\n"
        "    - name: Drive the guard\n"
        "      block:\n"
        "        - name: Include the role with a bad input\n"
        "          ansible.builtin.debug: {}\n"
        "      rescue:\n"
        "        - name: Record that the guard fired\n"
        "          ansible.builtin.debug: {}\n"
    )
    assert untagged(playbook) == ["Drive the guard"]
    playbook.write_text(
        playbook.read_text().replace(
            "      block:\n", f"      tags: [{IDEMPOTENCE_SKIP}]\n      block:\n"
        )
    )
    assert untagged(playbook) == []
