#!/usr/bin/env python3
"""Unit tests for scripts/generate-molecule-pipeline.py.

Covers the affected-set computation (direct role selection, transitive role
dependencies, integration-test selection, global triggers, inventory/playbook
handling), the coverage-bug failure modes, and the child-pipeline rendering
(including the empty -> no-op child).

Everything runs against a synthetic fixture repo built in a tmpdir (roles
alpha/beta/gamma/leaf with meta + include_role edges, two integration stacks), a
collection-layout variant (bare and FQCN role references), or through the pure
compute_affected(), so the suite is consumer-tree independent.

Fixture dependency graph (consumer -> providers):
    beta  -> {alpha}          (include_role in tasks/)
    gamma -> {beta, alpha}    (meta dependency + include_role)
    leaf  -> {}
so a change to `alpha` fans out to alpha, beta, gamma.

Run with pytest:
    python3 -m pytest tests/test_generate_molecule_pipeline.py -v
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

_script_path = Path(__file__).resolve().parent.parent / "scripts" / "generate-molecule-pipeline.py"
_spec = importlib.util.spec_from_file_location("generate_molecule_pipeline", _script_path)
gmp = importlib.util.module_from_spec(_spec)
sys.modules["generate_molecule_pipeline"] = gmp
_spec.loader.exec_module(gmp)

FIXTURE_CI = textwrap.dedent(
    """\
    stages:
      - test

    molecule-tests:
      stage: test
      parallel:
        matrix:
          - ROLE: alpha
            SCENARIO: default
          - ROLE: beta
            SCENARIO: default
          - ROLE: beta
            SCENARIO: extra
          - ROLE: gamma
            SCENARIO: default
          - ROLE: leaf
            SCENARIO: default

    integration-tests:
      stage: test
      parallel:
        matrix:
          - TEST:
              - stack-a
              - stack-b
    """
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _build_repo(root: Path) -> Path:
    _write(root / ".gitlab-ci.yml", FIXTURE_CI)
    _write(root / "ansible" / "roles" / "README.md", "not a role\n")

    _write(root / "ansible/roles/alpha/tasks/main.yml", "---\n- name: noop\n  ansible.builtin.debug: {}\n")
    # beta wraps alpha via include_role in a PRODUCTION dir.
    _write(
        root / "ansible/roles/beta/tasks/main.yml",
        "---\n- name: wrap alpha\n  ansible.builtin.include_role:\n    name: alpha\n",
    )
    # gamma declares beta as a meta dependency and include_roles alpha directly.
    _write(root / "ansible/roles/gamma/meta/main.yml", "---\ndependencies:\n  - role: beta\n")
    _write(
        root / "ansible/roles/gamma/tasks/main.yml",
        "---\n- name: wrap alpha too\n  ansible.builtin.import_role:\n    name: alpha\n",
    )
    _write(root / "ansible/roles/leaf/tasks/main.yml", "---\n- name: noop\n  ansible.builtin.debug: {}\n")

    for role, scenarios in {
        "alpha": ["default"], "beta": ["default", "extra"],
        "gamma": ["default"], "leaf": ["default"],
    }.items():
        for scenario in scenarios:
            d = root / "ansible/roles" / role / "molecule" / scenario
            _write(d / "molecule.yml", "driver:\n  name: docker\n")
            # A molecule/ include_role must NOT create a graph edge: leaf is
            # referenced only from alpha's scenario, so alpha must not depend on it.
            _write(
                d / "converge.yml",
                "---\n- hosts: all\n  tasks:\n    - ansible.builtin.include_role:\n"
                f"        name: {'leaf' if role == 'alpha' else role}\n",
            )

    _write(root / "ansible/integration-tests/_shared/prepare.yml", "---\n- hosts: all\n")
    # stack-a exercises gamma and loads the shared inventory vars file.
    _write(
        root / "ansible/integration-tests/stack-a/molecule/default/molecule.yml",
        "driver:\n  name: docker\n",
    )
    _write(
        root / "ansible/integration-tests/stack-a/molecule/default/converge.yml",
        "---\n- hosts: all\n  vars_files:\n"
        "    - ../../../../inventories/prod/group_vars/all.yml\n"
        "  tasks:\n    - ansible.builtin.include_role:\n        name: gamma\n",
    )
    _write(
        root / "ansible/integration-tests/stack-b/molecule/default/molecule.yml",
        "driver:\n  name: docker\n",
    )
    _write(
        root / "ansible/integration-tests/stack-b/molecule/default/converge.yml",
        "---\n- hosts: all\n  tasks:\n    - ansible.builtin.include_role:\n        name: leaf\n",
    )

    _write(root / "ansible/inventories/prod/group_vars/all.yml", "---\nfoo: bar\n")
    _write(root / "ansible/inventories/prod/hosts.yml", "---\nall: {}\n")
    _write(root / "ansible/playbooks/site.yml", "---\n- hosts: all\n")
    return root


@pytest.fixture(scope="module")
def repo(tmp_path_factory) -> Path:
    return _build_repo(tmp_path_factory.mktemp("molecule-repo"))


@pytest.fixture()
def sel(repo):
    def _sel(changed):
        return gmp.select(changed, repo=repo)

    return _sel


class TestMatrixParsing:
    def test_role_scenarios_present(self, repo):
        matrix, integration = gmp.parse_molecule_matrix(repo / ".gitlab-ci.yml")
        assert matrix["beta"] == ["default", "extra"]
        assert set(matrix) == {"alpha", "beta", "gamma", "leaf"}
        assert integration == ["stack-a", "stack-b"]

    def test_unparseable_matrix_raises(self, tmp_path):
        (tmp_path / ".gitlab-ci.yml").write_text("molecule-tests:\n  stage: test\n")
        with pytest.raises(RuntimeError):
            gmp.parse_molecule_matrix(tmp_path / ".gitlab-ci.yml")


class TestDerivedGraph:
    def test_include_role_and_meta_edges(self, repo):
        known = {"alpha", "beta", "gamma", "leaf"}
        graph = gmp.build_role_graph(repo / "ansible/roles", known_roles=known)
        assert graph["beta"] == {"alpha"}
        assert graph["gamma"] == {"alpha", "beta"}

    def test_molecule_dirs_excluded_from_graph(self, repo):
        """A role's own scenario include_role must not become a production edge."""
        known = {"alpha", "beta", "gamma", "leaf"}
        graph = gmp.build_role_graph(repo / "ansible/roles", known_roles=known)
        assert "alpha" not in graph, "alpha's molecule converge must not create an edge"

    def test_no_self_edges_and_only_known_roles(self, repo):
        graph = gmp.build_role_graph(repo / "ansible/roles", known_roles={"alpha", "beta"})
        for consumer, providers in graph.items():
            assert consumer not in providers
            assert providers <= {"alpha", "beta"}

    def test_integration_role_map(self, repo):
        known = {"alpha", "beta", "gamma", "leaf"}
        mapping = gmp.build_integration_map(repo / "ansible/integration-tests", known_roles=known)
        assert mapping == {"stack-a": {"gamma"}, "stack-b": {"leaf"}}

    def test_inventory_consumers(self, repo):
        inv = gmp.build_inventory_consumers(repo)
        assert inv["ansible/inventories/prod/group_vars/all.yml"] == {("integration", "stack-a")}


class TestDirectRoleSelection:
    def test_leaf_selects_only_itself(self, sel):
        s = sel(["ansible/roles/leaf/tasks/main.yml"])
        assert {r for r, _ in s.scenarios} == {"leaf"}

    def test_multi_scenario_role_selects_all_scenarios(self, sel):
        s = sel(["ansible/roles/beta/tasks/main.yml"])
        assert {sc for r, sc in s.scenarios if r == "beta"} == {"default", "extra"}

    def test_roles_readme_is_not_a_role_change(self, sel):
        s = sel(["ansible/roles/README.md"])
        assert s.empty


class TestTransitiveRoleDeps:
    def test_provider_fans_out(self, sel):
        s = sel(["ansible/roles/alpha/tasks/main.yml"])
        assert {r for r, _ in s.scenarios} == {"alpha", "beta", "gamma"}

    def test_consumer_does_not_pull_providers(self, sel):
        s = sel(["ansible/roles/gamma/tasks/main.yml"])
        assert {r for r, _ in s.scenarios} == {"gamma"}


class TestIntegrationSelection:
    def test_role_in_stack_selects_stack(self, sel):
        assert sel(["ansible/roles/leaf/tasks/main.yml"]).integration == {"stack-b"}

    def test_transitive_provider_selects_stack(self, sel):
        # stack-a exercises gamma, which transitively consumes alpha.
        assert "stack-a" in sel(["ansible/roles/alpha/tasks/main.yml"]).integration

    def test_integration_dir_change_selects_only_that_stack(self, sel):
        s = sel(["ansible/integration-tests/stack-b/molecule/default/converge.yml"])
        assert s.integration == {"stack-b"}
        assert s.scenarios == set()

    def test_shared_prepare_selects_all_stacks(self, sel):
        s = sel(["ansible/integration-tests/_shared/prepare.yml"])
        assert s.integration == {"stack-a", "stack-b"}

    def test_unknown_stack_dir_raises(self, sel):
        with pytest.raises(gmp.CoverageError):
            sel(["ansible/integration-tests/ghost/molecule/default/converge.yml"])


class TestGlobalTriggers:
    def test_each_global_trigger(self, sel, repo):
        matrix, integration = gmp.parse_molecule_matrix(repo / ".gitlab-ci.yml")
        all_scenarios = {(r, s) for r, scen in matrix.items() for s in scen}
        for path in sorted(gmp.GLOBAL_TRIGGER_FILES):
            s = sel([path])
            assert s.full, f"{path} must trigger the full matrix"
            assert s.scenarios == all_scenarios
            assert s.integration == set(integration)
        for prefix in gmp.GLOBAL_TRIGGER_PREFIXES:
            assert sel([prefix + "x.yml"]).full, f"{prefix} must trigger the full matrix"

    def test_global_trigger_wins_over_unknown_role(self, sel):
        """A global trigger short-circuits before the unknown-role coverage check."""
        s = sel([".gitlab-ci.yml", "ansible/roles/ghost/tasks/main.yml"])
        assert s.full

    def test_env_extra_triggers_are_honored(self, monkeypatch):
        monkeypatch.setattr(gmp, "GLOBAL_TRIGGER_FILES", gmp.GLOBAL_TRIGGER_FILES | {"Taskfile.yml"})
        assert gmp.is_global_trigger("Taskfile.yml")


class TestInventoryAndPlaybookPaths:
    def test_group_vars_all_selects_integration_only(self, sel):
        s = sel(["ansible/inventories/prod/group_vars/all.yml"])
        assert s.integration == {"stack-a"}
        assert s.scenarios == set()

    def test_playbook_only_is_empty(self, sel):
        assert sel(["ansible/playbooks/site.yml"]).empty

    def test_hosts_and_non_ansible_paths_are_empty(self, sel):
        assert sel(["ansible/inventories/prod/hosts.yml"]).empty
        assert sel(["docs/01-overview.md", "kubernetes/apps/foo/release.yaml"]).empty


class TestFailureModes:
    def test_unknown_role_raises(self, sel):
        with pytest.raises(gmp.CoverageError):
            sel(["ansible/roles/ghost/tasks/main.yml"])

    def test_empty_diff_is_empty_selection(self, sel):
        assert sel([]).empty

    def test_selected_role_without_scenarios_raises(self):
        with pytest.raises(gmp.CoverageError):
            gmp.compute_affected(
                ["ansible/roles/a/tasks/main.yml"],
                matrix={"a": ["default"], "b": []},
                integration_tests=[],
                role_deps={"b": {"a"}},
                integration_map={},
                inventory_consumers={},
            )


class TestComputeAffectedSynthetic:
    """Pure compute_affected() over synthetic graphs (transitivity + direction)."""

    MATRIX = {"a": ["default"], "b": ["default"], "c": ["default"], "leaf": ["default"]}
    # b consumes a; c consumes b  => a change fans out a->b->c.
    DEPS = {"b": {"a"}, "c": {"b"}}

    def _compute(self, changed, integration_map=None):
        return gmp.compute_affected(
            changed,
            matrix=self.MATRIX,
            integration_tests=list(integration_map or []),
            role_deps=self.DEPS,
            integration_map=integration_map or {},
            inventory_consumers={},
        )

    def test_provider_change_fans_out_to_consumers(self):
        sel = self._compute(["ansible/roles/a/tasks/main.yml"])
        assert {r for r, _ in sel.scenarios} == {"a", "b", "c"}

    def test_consumer_change_does_not_pull_providers(self):
        sel = self._compute(["ansible/roles/c/tasks/main.yml"])
        assert {r for r, _ in sel.scenarios} == {"c"}

    def test_integration_provider_coupling(self):
        sel = self._compute(["ansible/roles/a/tasks/main.yml"], integration_map={"stack": {"c"}})
        assert "stack" in sel.integration


class TestRendering:
    def test_noop_child_when_empty(self):
        doc = yaml.safe_load(gmp.render_child_pipeline(gmp.Selection(set(), set())))
        assert gmp.NOOP_JOB_NAME in doc
        assert doc[gmp.NOOP_JOB_NAME]["rules"] == [{"when": "always"}]

    def test_child_reuses_template_and_matrix(self):
        sel = gmp.Selection({("alpha", "default"), ("beta", "extra")}, {"stack-a"})
        doc = yaml.safe_load(gmp.render_child_pipeline(sel))
        assert doc["include"] == [{"local": gmp.MOLECULE_JOBS_INCLUDE}]
        assert doc["molecule-tests"]["extends"] == gmp.MOLECULE_JOB_EXTENDS
        assert doc["integration-tests"]["extends"] == gmp.INTEGRATION_JOB_EXTENDS
        assert sorted(
            (e["ROLE"], e["SCENARIO"]) for e in doc["molecule-tests"]["parallel"]["matrix"]
        ) == [("alpha", "default"), ("beta", "extra")]
        assert doc["integration-tests"]["parallel"]["matrix"] == [{"TEST": ["stack-a"]}]

    def test_render_is_deterministic(self):
        sel = gmp.Selection({("alpha", "default"), ("beta", "extra")}, {"stack-a"})
        assert gmp.render_child_pipeline(sel) == gmp.render_child_pipeline(sel)

    def test_molecule_only_selection_omits_integration_job(self):
        doc = yaml.safe_load(gmp.render_child_pipeline(gmp.Selection({("alpha", "default")}, set())))
        assert "integration-tests" not in doc

    def test_integration_only_selection_omits_molecule_job(self):
        doc = yaml.safe_load(gmp.render_child_pipeline(gmp.Selection(set(), {"stack-a"})))
        assert "molecule-tests" not in doc


COLLECTION = "ansible_collections/weisssrv/infra"

COLLECTION_CI = textwrap.dedent(
    """\
    stages:
      - test

    molecule-tests:
      stage: test
      parallel:
        matrix:
          - ROLE: alpha
            SCENARIO: default
          - ROLE: beta
            SCENARIO: default
    """
)


def _build_collection_repo(root: Path) -> Path:
    """Collection layout with NO integration suite (the lib shape)."""
    _write(root / ".gitlab-ci.yml", COLLECTION_CI)
    _write(root / COLLECTION / "requirements.yml", "---\ncollections: []\n")
    _write(root / COLLECTION / "roles/alpha/tasks/main.yml", "---\n- name: noop\n  ansible.builtin.debug: {}\n")
    _write(
        root / COLLECTION / "roles/beta/tasks/main.yml",
        "---\n- name: wrap alpha\n  ansible.builtin.include_role:\n    name: alpha\n",
    )
    for role in ("alpha", "beta"):
        _write(root / COLLECTION / "roles" / role / "molecule/default/molecule.yml", "driver:\n  name: docker\n")
    return root


FQCN_NS = "weisssrv.infra."

FQCN_CI = textwrap.dedent(
    """\
    stages:
      - test

    molecule-tests:
      stage: test
      parallel:
        matrix:
          - ROLE: alpha
            SCENARIO: default
          - ROLE: beta
            SCENARIO: default
          - ROLE: gamma
            SCENARIO: default
          - ROLE: delta
            SCENARIO: default
          - ROLE: foreign
            SCENARIO: default

    integration-tests:
      stage: test
      parallel:
        matrix:
          - TEST:
              - stack-fqcn
    """
)


def _build_fqcn_collection_repo(root: Path) -> Path:
    """Collection layout whose roles reference each other by FQCN.

    Edges (consumer -> providers): beta -> {alpha} via a FQCN meta dependency,
    gamma -> {alpha} via a FQCN include_role, delta -> {alpha, beta} via a mixed
    bare + FQCN pair. `foreign` references community.general.alpha and must get
    no edge at all.
    """
    _write(root / ".gitlab-ci.yml", FQCN_CI)
    roles = root / COLLECTION / "roles"
    _write(roles / "alpha/tasks/main.yml", "---\n- name: noop\n  ansible.builtin.debug: {}\n")
    _write(roles / "beta/meta/main.yml", f"---\ndependencies:\n  - role: {FQCN_NS}alpha\n")
    _write(roles / "beta/tasks/main.yml", "---\n- name: noop\n  ansible.builtin.debug: {}\n")
    _write(
        roles / "gamma/tasks/main.yml",
        f"---\n- name: wrap alpha\n  ansible.builtin.include_role:\n    name: {FQCN_NS}alpha\n",
    )
    _write(
        roles / "delta/tasks/main.yml",
        "---\n- name: wrap alpha bare\n  ansible.builtin.include_role:\n    name: alpha\n"
        f"- name: wrap beta fqcn\n  ansible.builtin.import_role:\n    name: {FQCN_NS}beta\n",
    )
    _write(
        roles / "foreign/tasks/main.yml",
        "---\n- name: wrap another collection\n  ansible.builtin.include_role:\n"
        "    name: community.general.alpha\n",
    )
    for role in ("alpha", "beta", "gamma", "delta", "foreign"):
        _write(roles / role / "molecule/default/molecule.yml", "driver:\n  name: docker\n")
    _write(
        root / COLLECTION / "integration-tests/stack-fqcn/molecule/default/converge.yml",
        "---\n- hosts: all\n  tasks:\n    - ansible.builtin.include_role:\n"
        f"        name: {FQCN_NS}gamma\n",
    )
    return root


class TestCollectionLayout:
    """ROLES_DIR/INTEGRATION_DIR/CI_FILE retargeting (weisssrv.infra collection)."""

    @pytest.fixture(scope="class")
    def repo(self, tmp_path_factory) -> Path:
        return _build_collection_repo(tmp_path_factory.mktemp("collection-repo"))

    def _sel(self, repo, changed):
        return gmp.select(
            changed,
            repo=repo,
            roles_prefix=f"{COLLECTION}/roles",
            integration_prefix=f"{COLLECTION}/integration-tests",
        )

    def test_provider_fans_out_under_collection_prefix(self, repo):
        s = self._sel(repo, [f"{COLLECTION}/roles/alpha/tasks/main.yml"])
        assert {r for r, _ in s.scenarios} == {"alpha", "beta"}

    def test_absent_integration_job_yields_no_integration(self, repo):
        assert self._sel(repo, [f"{COLLECTION}/roles/beta/tasks/main.yml"]).integration == set()

    def test_default_prefix_paths_no_longer_match(self, repo):
        assert self._sel(repo, ["ansible/roles/alpha/tasks/main.yml"]).empty

    def test_unknown_role_names_the_configured_prefix(self, repo):
        with pytest.raises(gmp.CoverageError, match=f"{COLLECTION}/roles/ghost/"):
            self._sel(repo, [f"{COLLECTION}/roles/ghost/tasks/main.yml"])

    def test_missing_roles_dir_raises(self, repo):
        with pytest.raises(RuntimeError, match="not found"):
            gmp.select([], repo=repo, roles_prefix="ansible/roles")

    def test_present_but_empty_integration_matrix_still_raises(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(COLLECTION_CI + "\nintegration-tests:\n  stage: test\n")
        with pytest.raises(RuntimeError):
            gmp.parse_molecule_matrix(ci)


class TestCollectionFqcnReferences:
    """A collection's roles reference each other by FQCN while the on-disk dirs
    are bare, so the graph must normalize the own-collection prefix away."""

    @pytest.fixture(scope="class")
    def repo(self, tmp_path_factory) -> Path:
        return _build_fqcn_collection_repo(tmp_path_factory.mktemp("fqcn-repo"))

    @pytest.fixture(scope="class")
    def graph(self, repo):
        return gmp.build_role_graph(repo / COLLECTION / "roles")

    def _sel(self, repo, changed):
        return gmp.select(
            changed,
            repo=repo,
            roles_prefix=f"{COLLECTION}/roles",
            integration_prefix=f"{COLLECTION}/integration-tests",
        )

    def test_prefix_derived_from_the_roles_dir_layout(self, repo):
        assert gmp.collection_role_prefix(repo / COLLECTION / "roles") == FQCN_NS

    def test_classic_layout_has_no_prefix(self, repo):
        assert gmp.collection_role_prefix(repo / "ansible/roles") == ""

    def test_fqcn_meta_dependency_edge(self, graph):
        assert graph["beta"] == {"alpha"}

    def test_fqcn_include_role_edge(self, graph):
        assert graph["gamma"] == {"alpha"}

    def test_mixed_bare_and_fqcn_edges(self, graph):
        assert graph["delta"] == {"alpha", "beta"}

    def test_foreign_namespace_does_not_match(self, graph):
        assert "foreign" not in graph, "community.general.alpha must not alias onto alpha"

    def test_provider_fans_out_transitively(self, repo):
        s = self._sel(repo, [f"{COLLECTION}/roles/alpha/tasks/main.yml"])
        assert {r for r, _ in s.scenarios} == {"alpha", "beta", "gamma", "delta"}

    def test_fqcn_integration_map(self, repo):
        mapping = gmp.build_integration_map(
            repo / COLLECTION / "integration-tests",
            known_roles={"alpha", "beta", "gamma", "delta", "foreign"},
            collection_prefix=FQCN_NS,
        )
        assert mapping == {"stack-fqcn": {"gamma"}}

    def test_fqcn_integration_stack_selected_via_provider(self, repo):
        s = self._sel(repo, [f"{COLLECTION}/roles/alpha/tasks/main.yml"])
        assert s.integration == {"stack-fqcn"}


class TestEnvironmentOverrides:
    """The env contract ci/internal/molecule-matrix.gitlab-ci.yml relies on.

    The path constants resolve at import time, so this drives the real script in
    a subprocess rather than reloading the module in-process.
    """

    def _run(self, repo: Path, tmp_path: Path, changed: str, env: dict) -> dict:
        changed_file = tmp_path / "changed.txt"
        changed_file.write_text(changed + "\n")
        out = tmp_path / "child.yml"
        proc = subprocess.run(
            [sys.executable, str(_script_path), "--changed-files-from", str(changed_file),
             "-o", str(out), "--repo", str(repo)],
            capture_output=True, text=True,
            env={**os.environ, **env},
        )
        assert proc.returncode == 0, proc.stderr
        return yaml.safe_load(out.read_text())

    @pytest.fixture(scope="class")
    def repo(self, tmp_path_factory) -> Path:
        return _build_collection_repo(tmp_path_factory.mktemp("collection-env-repo"))

    ENV = {
        "ROLES_DIR": f"{COLLECTION}/roles",
        "INTEGRATION_DIR": f"{COLLECTION}/integration-tests",
    }

    def test_roles_dir_env_narrows_to_the_changed_role(self, repo, tmp_path):
        doc = self._run(repo, tmp_path, f"{COLLECTION}/roles/beta/tasks/main.yml", self.ENV)
        assert doc["molecule-tests"]["parallel"]["matrix"] == [{"ROLE": "beta", "SCENARIO": "default"}]

    def test_galaxy_requirements_trigger_follows_roles_dir(self, repo, tmp_path):
        doc = self._run(repo, tmp_path, f"{COLLECTION}/requirements.yml", self.ENV)
        assert sorted(e["ROLE"] for e in doc["molecule-tests"]["parallel"]["matrix"]) == ["alpha", "beta"]

    def test_jobs_include_env_retargets_the_child_include(self, repo, tmp_path):
        env = {**self.ENV, "MOLECULE_JOBS_INCLUDE": ".gitlab/ci/collection-molecule-jobs.yml"}
        doc = self._run(repo, tmp_path, f"{COLLECTION}/roles/beta/tasks/main.yml", env)
        assert doc["include"] == [{"local": ".gitlab/ci/collection-molecule-jobs.yml"}]

    def test_jobs_include_env_is_also_a_global_trigger(self, repo, tmp_path):
        env = {**self.ENV, "MOLECULE_JOBS_INCLUDE": ".gitlab/ci/collection-molecule-jobs.yml"}
        doc = self._run(repo, tmp_path, ".gitlab/ci/collection-molecule-jobs.yml", env)
        assert sorted(e["ROLE"] for e in doc["molecule-tests"]["parallel"]["matrix"]) == ["alpha", "beta"]

    @pytest.mark.parametrize(
        "changed",
        [
            f"{COLLECTION}/galaxy.yml",
            f"{COLLECTION}/meta/runtime.yml",
            f"{COLLECTION}/plugins/filter/x.py",
            f"{COLLECTION}/molecule-shared/base.yml",
        ],
    )
    def test_collection_root_paths_run_the_full_matrix(self, repo, tmp_path, changed):
        """These are neither role paths nor scenario paths: without a derived
        trigger they select NOTHING and the MR goes green having run no scenario."""
        doc = self._run(repo, tmp_path, changed, self.ENV)
        assert sorted(e["ROLE"] for e in doc["molecule-tests"]["parallel"]["matrix"]) == ["alpha", "beta"]

    def test_collection_root_triggers_follow_roles_dir(self, repo, tmp_path):
        """The derivation is from $ROLES_DIR's parent, so another collection's
        galaxy.yml is NOT a trigger (it selects nothing, no full matrix)."""
        doc = self._run(repo, tmp_path, "ansible_collections/other/coll/galaxy.yml", self.ENV)
        assert "molecule-tests" not in doc
        assert gmp.NOOP_JOB_NAME in doc


class TestCli:
    def test_changed_files_from_writes_output(self, repo, tmp_path):
        changed = tmp_path / "changed.txt"
        changed.write_text("ansible/roles/leaf/tasks/main.yml\n")
        out = tmp_path / "child.yml"
        rc = gmp.main(
            ["--changed-files-from", str(changed), "-o", str(out), "--repo", str(repo)]
        )
        assert rc == 0
        doc = yaml.safe_load(out.read_text())
        assert doc["molecule-tests"]["parallel"]["matrix"] == [
            {"ROLE": "leaf", "SCENARIO": "default"}
        ]

    def test_unknown_role_exits_2(self, repo, tmp_path):
        changed = tmp_path / "changed.txt"
        changed.write_text("ansible/roles/ghost/tasks/main.yml\n")
        assert gmp.main(["--changed-files-from", str(changed), "--repo", str(repo)]) == 2

    def test_print_graph(self, repo, capsys):
        assert gmp.main(["--print-graph", "--repo", str(repo)]) == 0
        out = capsys.readouterr().out
        assert "molecule matrix" in out and "stack-a" in out

    @pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
    def test_diff_base_reads_git(self, tmp_path):
        r = _build_repo(tmp_path / "gitrepo")
        env = {
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
        }

        def git(*args):
            subprocess.run(["git", "-C", str(r), *args], check=True,
                           capture_output=True, env={**os.environ, **env})

        git("init", "-q", "-b", "main")
        git("add", "-A")
        git("commit", "-q", "-m", "base")
        base = subprocess.run(["git", "-C", str(r), "rev-parse", "HEAD"],
                              check=True, capture_output=True, text=True).stdout.strip()
        _write(r / "ansible/roles/leaf/tasks/main.yml", "---\n- name: changed\n  ansible.builtin.debug: {}\n")
        git("commit", "-q", "-am", "edit leaf")

        out = tmp_path / "child.yml"
        assert gmp.main([base, "-o", str(out), "--repo", str(r)]) == 0
        doc = yaml.safe_load(out.read_text())
        assert doc["molecule-tests"]["parallel"]["matrix"] == [
            {"ROLE": "leaf", "SCENARIO": "default"}
        ]


class TestRealCollection:
    """The synthetic fixtures prove the algorithm; this proves it against the
    layout that actually ships, so a rename or a mistyped FQCN reference in
    ansible_collections/ fails here instead of silently pruning an edge."""

    REAL_ROLES = Path(__file__).resolve().parent.parent / COLLECTION / "roles"

    @pytest.fixture(scope="class")
    def graph(self):
        return gmp.build_role_graph(self.REAL_ROLES)

    def test_prefix_is_the_shipped_collection(self):
        assert gmp.collection_role_prefix(self.REAL_ROLES) == FQCN_NS

    def test_known_meta_dependency_edges(self, graph):
        assert "base" in graph["qol"]
        assert "base" in graph["nas_storage"]

    def test_known_include_role_edges(self, graph):
        assert "apt_signed_repo" in graph["docker_engine"]
        assert "prometheus_exporter" in graph["zfs_exporter"]

    def test_no_own_collection_reference_is_pruned_as_unknown(self):
        """build_role_graph drops providers that aren't on disk, so a typo'd
        `weisssrv.infra.apt_signed_repos` would vanish with no error."""
        known = {p.name for p in self.REAL_ROLES.iterdir() if p.is_dir()}
        unknown = {}
        for role_dir in sorted(p for p in self.REAL_ROLES.iterdir() if p.is_dir()):
            referenced: set[str] = set()
            meta = role_dir / "meta" / "main.yml"
            if meta.is_file():
                referenced |= gmp._meta_dependencies(meta)
            for yml in gmp._yaml_files(role_dir):
                if "/molecule/" in yml.as_posix():
                    continue
                gmp._collect_include_role_names(gmp._load_yaml(yml), referenced)
            missing = sorted(
                name[len(FQCN_NS):]
                for name in referenced
                if name.startswith(FQCN_NS) and name[len(FQCN_NS):] not in known
            )
            if missing:
                unknown[role_dir.name] = missing
        assert unknown == {}
