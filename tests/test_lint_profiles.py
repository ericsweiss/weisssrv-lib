"""The root lint dotfiles stay in step with the lint/ copies consumers vendor."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml
from _helpers import template_input_default

REPO = Path(__file__).resolve().parent.parent
SHIPPED_PRE_COMMIT = REPO / "lint" / "pre-commit-config.yaml"
ROOT_PRE_COMMIT = REPO / ".pre-commit-config.yaml"
SHIPPED_EDITORCONFIG = REPO / "lint" / "editorconfig"
ROOT_EDITORCONFIG = REPO / ".editorconfig"
SHIPPED_GITLEAKS = REPO / "lint" / "gitleaks.toml"
ROOT_GITLEAKS = REPO / ".gitleaks.toml"
SHIPPED_GITATTRIBUTES = REPO / "lint" / "gitattributes"
ROOT_GITATTRIBUTES = REPO / ".gitattributes"
YAML_LINT_TEMPLATE = REPO / "ci" / "lint" / "yaml-lint.yml"
YAMLLINT_PROFILE = REPO / "lint" / "yamllint-relaxed.yml"
YAMLLINT_SELF_PROFILE = REPO / "lint" / "yamllint-self.yml"
YAMLLINT_REPO = "https://github.com/adrienverge/yamllint"
LIB_CI = REPO / ".gitlab-ci.yml"
# Root-level YAML this repo ships. A directory target cannot reach them, so
# each needs its own entry in the yaml-lint job's targets and changes.
ROOT_YAML_FILES = (".gitlab-ci.yml", ".pre-commit-config.yaml")
# The only names ansible-lint auto-loads a yamllint config from.
ANSIBLE_LINT_DISCOVERS = (".yamllint", ".yamllint.yaml", ".yamllint.yml")
# Every setting ansible-lint's load_yamllint_config() checks a config it loads
# against. One mismatch prints "Found incompatible custom yamllint
# configuration" and turns its fix mode off for the whole run.
ANSIBLE_LINT_REQUIRES = {
    "comments.min-spaces-from-content": 1,
    "comments-indentation": False,
    "braces.min-spaces-inside": 0,
    "braces.max-spaces-inside": 1,
    "octal-values.forbid-implicit-octal": True,
    "octal-values.forbid-explicit-octal": True,
}
# Provider checkouts a local `terraform init` leaves in a consumer tree.
IGNORED_PATHS = (
    ".terraform/modules/zone/main.yml",
    "terraform/cloudflare/.terraform/modules/zone/main.yml",
)


def pinned_revs(text: str) -> dict[str, str]:
    """Every `repo: rev` pair of a pre-commit config; `local` hooks carry none."""
    repos = yaml.safe_load(text)["repos"]
    return {r["repo"]: r["rev"] for r in repos if r.get("repo") != "local"}


def revs_agree(shipped_text: str, fork_text: str) -> bool:
    """The fork may add repos, but every shared one is pinned to the same rev."""
    shipped, fork = pinned_revs(shipped_text), pinned_revs(fork_text)
    return set(shipped) <= set(fork) and all(fork[k] == v for k, v in shipped.items())


def extends_default_rules(text: str) -> bool:
    """A gitleaks config's `[extend] useDefault`. Absent or false, gitleaks
    loads no rules at all and every scan passes clean."""
    return tomllib.loads(text).get("extend", {}).get("useDefault") is True

def discovered_configs(root: Path) -> list[str]:
    """The names in `root` that ansible-lint would auto-load a yamllint config from."""
    return [name for name in ANSIBLE_LINT_DISCOVERS if (root / name).is_file()]


def yamllint_hook_args(path: Path) -> list[str]:
    """The `args:` of a pre-commit config's yamllint hook."""
    for repo in yaml.safe_load(path.read_text())["repos"]:
        if repo.get("repo") == YAMLLINT_REPO:
            return [str(arg) for arg in repo["hooks"][0]["args"]]
    raise AssertionError(f"{path} has no {YAMLLINT_REPO} hook")


def yaml_lint_inputs() -> dict:
    """The inputs the library's own pipeline passes to the yaml-lint job."""
    for entry in yaml.safe_load(LIB_CI.read_text())["include"]:
        if isinstance(entry, dict) and entry.get("local") == "/ci/lint/yaml-lint.yml":
            return dict(entry["inputs"])
    raise AssertionError(f"{LIB_CI} does not include /ci/lint/yaml-lint.yml")


def yaml_lint_config_input() -> str:
    """The `config` the library's own pipeline passes to the yaml-lint job."""
    return str(yaml_lint_inputs()["config"])


def uncovered_root_yaml(targets: str, changes: list[str]) -> list[str]:
    """Root-level YAML files missing from the yaml-lint job's targets or changes.
    The job lints each target path, so a root file absent from both ships
    unlinted and no MR touching it even runs the job."""
    target_names = set(targets.split())
    return sorted(
        name
        for name in ROOT_YAML_FILES
        if name not in target_names or name not in set(changes)
    )


def effective_rules(text: str):
    """A profile's rules after `extends: relaxed` is resolved."""
    config = pytest.importorskip("yamllint.config")
    return config.YamlLintConfig(content=text).rules


def incompatible_settings(rules) -> list[str]:
    """The ANSIBLE_LINT_REQUIRES settings `rules` does not satisfy."""
    missing = []
    for setting, expected in ANSIBLE_LINT_REQUIRES.items():
        value = rules
        for key in setting.split("."):
            if not isinstance(value, dict) or key not in value:
                break
            value = value[key]
        if value != expected:
            missing.append(setting)
    return missing


def local_hook(path: Path, hook_id: str) -> dict:
    """One hook of a pre-commit config's `repo: local` block."""
    for repo in yaml.safe_load(path.read_text())["repos"]:
        if repo.get("repo") == "local":
            for hook in repo["hooks"]:
                if hook["id"] == hook_id:
                    return hook
    raise AssertionError(f"{path} has no local `{hook_id}` hook")


def body_lines(text: str) -> list[str]:
    """Settings only, so the two files' different headers are allowed to differ."""
    return [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


class TestRootForksMatchTheVendoredCopies:
    def test_the_root_pre_commit_config_adds_hooks_and_changes_no_rev(self):
        assert revs_agree(SHIPPED_PRE_COMMIT.read_text(), ROOT_PRE_COMMIT.read_text())

    def test_a_rev_bumped_in_one_copy_alone_is_caught(self):
        drifted = ROOT_PRE_COMMIT.read_text().replace("rev: v", "rev: v0.", 1)
        assert not revs_agree(SHIPPED_PRE_COMMIT.read_text(), drifted)

    def test_the_root_editorconfig_settings_are_identical(self):
        assert body_lines(ROOT_EDITORCONFIG.read_text()) == body_lines(
            SHIPPED_EDITORCONFIG.read_text()
        )

    def test_an_editorconfig_setting_changed_in_one_copy_is_caught(self):
        drifted = ROOT_EDITORCONFIG.read_text().replace("indent_size = 2", "indent_size = 3", 1)
        assert body_lines(drifted) != body_lines(SHIPPED_EDITORCONFIG.read_text())

    def test_the_root_gitattributes_is_the_vendored_copy(self):
        """These two share their header too, so byte identity is the contract."""
        assert ROOT_GITATTRIBUTES.read_text() == SHIPPED_GITATTRIBUTES.read_text()

    def test_a_rule_changed_in_one_gitattributes_copy_is_caught(self):
        drifted = ROOT_GITATTRIBUTES.read_text().replace("eol=lf", "eol=crlf", 1)
        assert drifted != SHIPPED_GITATTRIBUTES.read_text()


class TestYamllintRevMatchesTheLintPin:
    """Both pre-commit copies pin the yamllint the yaml-lint template installs."""

    @pytest.mark.parametrize(
        "path", [SHIPPED_PRE_COMMIT, ROOT_PRE_COMMIT], ids=lambda p: p.name
    )
    def test_rev_matches_the_template_default(self, path):
        rev = pinned_revs(path.read_text())[YAMLLINT_REPO]
        assert rev.lstrip("v") == template_input_default(
            YAML_LINT_TEMPLATE, "yamllint_version"
        )


class TestShippedYamllintProfile:
    def test_it_loads_through_yamllints_own_config_loader(self):
        """An unknown rule name or level fails here rather than in a consumer."""
        config = pytest.importorskip("yamllint.config")
        config.YamlLintConfig(file=str(YAMLLINT_PROFILE))


class TestNoYamllintConfigOnAnsibleLintsDiscoveryPath:
    """ansible-lint lints ansible_collections/ from the repo root. A yamllint
    config it discovers there replaces its own yaml[*] rules, dropping
    octal-values and comments, and turns fix mode off."""

    def test_the_repo_root_carries_none(self):
        assert discovered_configs(REPO) == []

    def test_a_profile_dropped_at_the_root_is_caught(self, tmp_path):
        (tmp_path / ".yamllint").write_text("extends: relaxed\n")
        assert discovered_configs(tmp_path) == [".yamllint"]

    def test_the_self_profile_lives_in_lint_and_loads(self):
        assert YAMLLINT_SELF_PROFILE.is_file()
        config = pytest.importorskip("yamllint.config")
        config.YamlLintConfig(file=str(YAMLLINT_SELF_PROFILE))

    def test_the_pipeline_names_the_self_profile_explicitly(self):
        assert yaml_lint_config_input() == "-c lint/yamllint-self.yml"


class TestSelfPipelineLintsItsRootYaml:
    def test_every_root_yaml_file_exists(self):
        """Guard the guard: a renamed file must not silently empty the check."""
        for name in ROOT_YAML_FILES:
            assert (REPO / name).is_file(), name

    def test_each_is_a_target_and_a_changes_entry(self):
        inputs = yaml_lint_inputs()
        assert uncovered_root_yaml(inputs["targets"], inputs["changes"]) == []

    def test_a_root_file_left_out_is_caught(self):
        inputs = yaml_lint_inputs()
        dropped = " ".join(
            t for t in inputs["targets"].split() if t != ".pre-commit-config.yaml"
        )
        assert uncovered_root_yaml(dropped, inputs["changes"]) == [
            ".pre-commit-config.yaml"
        ]
        kept = [c for c in inputs["changes"] if c != ".pre-commit-config.yaml"]
        assert uncovered_root_yaml(inputs["targets"], kept) == [
            ".pre-commit-config.yaml"
        ]

    @pytest.mark.parametrize(
        "path", [SHIPPED_PRE_COMMIT, ROOT_PRE_COMMIT], ids=lambda p: p.name
    )
    def test_each_pre_commit_hook_passes_an_undiscovered_path(self, path):
        args = yamllint_hook_args(path)
        assert args[:1] == ["-c"], args
        assert args[1] not in ANSIBLE_LINT_DISCOVERS, args


class TestGitleaksExtendsTheDefaultRules:
    """A gitleaks profile carries only an allowlist. Without the extend block it
    silently disarms the scan instead of narrowing it."""

    def test_the_shipped_profile_exists(self):
        """Guard the guard: the parametrize below is empty without it."""
        assert SHIPPED_GITLEAKS.is_file()

    @pytest.mark.parametrize(
        "path",
        [p for p in (SHIPPED_GITLEAKS, ROOT_GITLEAKS) if p.is_file()],
        ids=lambda p: p.name,
    )
    def test_each_shipped_profile_extends_them(self, path: Path):
        assert extends_default_rules(path.read_text()), (
            f"{path} does not set [extend] useDefault = true, so gitleaks would "
            "load no rules and report every repo clean"
        )

    def test_a_profile_that_drops_the_extend_block_is_caught(self):
        assert not extends_default_rules('title = "allowlist only"\n')
        assert not extends_default_rules("[extend]\nuseDefault = false\n")


class TestProfilesStayAnsibleLintCompatible:
    """ansible-lint merges a config it discovers over its own yaml[*] rules and
    refuses one that drops these settings, with fix mode off for the run."""

    @pytest.mark.parametrize(
        "path", [YAMLLINT_PROFILE, YAMLLINT_SELF_PROFILE], ids=lambda p: p.name
    )
    def test_every_required_setting_holds(self, path):
        assert incompatible_settings(effective_rules(path.read_text())) == []

    def test_a_bare_relaxed_profile_is_caught(self):
        """What the profiles would be without the explicit rule blocks."""
        bare = "extends: relaxed\nrules:\n  line-length: disable\n"
        assert sorted(incompatible_settings(effective_rules(bare))) == [
            "comments.min-spaces-from-content",
            "octal-values.forbid-explicit-octal",
            "octal-values.forbid-implicit-octal",
        ]

    @pytest.mark.parametrize(
        "path", [YAMLLINT_PROFILE, YAMLLINT_SELF_PROFILE], ids=lambda p: p.name
    )
    @pytest.mark.parametrize("rule", ["comments", "octal-values", "empty-lines"])
    def test_the_added_rules_only_warn(self, path, rule):
        """A re-vendor must not red a consumer over files it already has."""
        assert effective_rules(path.read_text())[rule]["level"] == "warning"


class TestProviderCheckoutsAreIgnored:
    """One target list has to work in CI and in a tree where terraform ran."""

    @pytest.mark.parametrize(
        "path", [YAMLLINT_PROFILE, YAMLLINT_SELF_PROFILE], ids=lambda p: p.name
    )
    @pytest.mark.parametrize("ignored", IGNORED_PATHS)
    def test_a_dot_terraform_path_is_skipped(self, path, ignored):
        config = pytest.importorskip("yamllint.config")
        assert config.YamlLintConfig(content=path.read_text()).is_file_ignored(ignored)

    @pytest.mark.parametrize(
        "path", [YAMLLINT_PROFILE, YAMLLINT_SELF_PROFILE], ids=lambda p: p.name
    )
    def test_the_repos_own_terraform_yaml_is_not_skipped(self, path):
        config = pytest.importorskip("yamllint.config")
        assert not config.YamlLintConfig(content=path.read_text()).is_file_ignored(
            "terraform/cloudflare/main.yml"
        )

    def test_a_profile_without_the_ignore_block_is_caught(self):
        config = pytest.importorskip("yamllint.config")
        bare = config.YamlLintConfig(content="extends: relaxed\n")
        assert not bare.is_file_ignored(IGNORED_PATHS[0])


class TestTheSelfForkOnlyChangesDocumentStart:
    """The self fork exists for the `spec:`-first CI templates. Any other rule
    difference is drift between the baseline and the copy this repo runs."""

    def test_the_rule_sets_differ_in_document_start_alone(self):
        shipped = effective_rules(YAMLLINT_PROFILE.read_text())
        own = effective_rules(YAMLLINT_SELF_PROFILE.read_text())
        differing = {k for k in shipped | own if shipped.get(k) != own.get(k)}
        assert differing == {"document-start"}

    def test_a_rule_tightened_in_one_copy_alone_is_caught(self):
        shipped = effective_rules(
            YAMLLINT_PROFILE.read_text().replace("    max: 1", "    max: 0")
        )
        own = effective_rules(YAMLLINT_SELF_PROFILE.read_text())
        differing = {k for k in shipped | own if shipped.get(k) != own.get(k)}
        assert differing == {"document-start", "empty-lines"}


class TestTheSharedCheckTaskfileHookFollowsIncludes:
    """A Taskfile is an `includes:` tree, so a commit can touch only an
    included file. A hook keyed on `Taskfile.yml` alone never runs then."""

    TRIGGERS = ("Taskfile.yml", "taskfiles/lint.yml", "taskfiles/flux.yaml")

    @pytest.mark.parametrize("changed", TRIGGERS)
    def test_the_shipped_hook_triggers_on_every_taskfile(self, changed):
        pattern = local_hook(SHIPPED_PRE_COMMIT, "check-taskfile")["files"]
        assert re.search(pattern, changed), (pattern, changed)

    def test_a_root_only_pattern_misses_an_included_taskfile(self):
        assert not re.search(r"^Taskfile\.yml$", "taskfiles/lint.yml")

    def test_it_reads_the_whole_tree_rather_than_the_changed_files(self):
        """check-taskfile.sh walks `includes:` itself and takes no arguments."""
        hook = local_hook(SHIPPED_PRE_COMMIT, "check-taskfile")
        assert hook["pass_filenames"] is False
        assert (REPO / "scripts" / "check-taskfile.sh").is_file()

