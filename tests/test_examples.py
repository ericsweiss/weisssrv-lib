"""The shipped consumer config templates in examples/ stay loadable.

These are the files docs/SCRIPTS.md tells a consumer to copy, so a renamed
config key in the owning script must break here rather than on first use. Three
examples are already exercised by their owning script's suite
(b2-bucket, deploy-coverage, hosts-env-map); this module covers the rest and
enumerates the directory so a new example cannot arrive unowned or undocumented.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "examples"
TESTS = REPO / "tests"
SCRIPTS_DOC = REPO / "docs" / "SCRIPTS.md"


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), REPO / "scripts" / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def example_files() -> list[Path]:
    return sorted(p for p in EXAMPLES.glob("*.example.*") if p.is_file())


class TestEnumeration:
    """Every shipped example is documented and owned by a test."""

    def test_directory_is_not_empty(self):
        assert example_files(), "examples/ has no *.example.* files"

    @pytest.mark.parametrize("example", example_files(), ids=lambda p: p.name)
    def test_named_in_examples_readme(self, example):
        # check-doc-links.py resolves relative .md targets only, so a link to a
        # .py/.yaml/.json/.conf example is unchecked without this.
        assert example.name in (EXAMPLES / "README.md").read_text()

    @pytest.mark.parametrize("example", example_files(), ids=lambda p: p.name)
    def test_named_in_scripts_doc(self, example):
        assert example.name in SCRIPTS_DOC.read_text()

    @pytest.mark.parametrize("example", example_files(), ids=lambda p: p.name)
    def test_loaded_by_some_test(self, example):
        owners = [t.name for t in TESTS.glob("test_*.py") if example.name in t.read_text()]
        assert owners, (
            f"{example.name} is loaded by no test — add a smoke load to the owning "
            "script's suite or to this module."
        )


class TestVersionRegistryExample:
    """examples/version-registry.example.py -> check-versions.py --config."""

    def test_loads_through_check_versions(self, tmp_path):
        cv = _load_script("check-versions.py")
        cfg = cv.load_config(EXAMPLES / "version-registry.example.py", repo_root=tmp_path)
        assert cfg["services"], "example registry declares no services"
        assert cfg["vars_file"] and cfg["default_deploy_command"]

    def test_every_entry_uses_a_supported_category(self):
        cv = _load_script("check-versions.py")
        supported = {"github", "dockerhub", "ghcr", "lsio", "helm", "apt_repo", "manual"}
        cfg = cv.load_config(EXAMPLES / "version-registry.example.py", repo_root=REPO)
        for svc in cfg["services"]:
            assert svc["category"] in supported, f"{svc['name']}: {svc['category']}"

    def test_the_report_title_is_consumer_named(self):
        cv = _load_script("check-versions.py")
        cfg = cv.load_config(EXAMPLES / "version-registry.example.py", repo_root=REPO)
        assert cfg["report_title"] and cv.REPORT_TITLE == cfg["report_title"]

    def test_version_file_entries_name_a_declared_alias(self):
        cv = _load_script("check-versions.py")
        cfg = cv.load_config(EXAMPLES / "version-registry.example.py", repo_root=REPO)
        aliases = set(cfg.get("version_file_aliases") or {})
        for svc in cfg["services"]:
            ref = svc.get("version_file")
            if isinstance(ref, str) and "/" not in ref:
                assert ref in aliases, f"{svc['name']} names undeclared alias {ref!r}"


class TestAutoscalingPolicyExample:
    """examples/autoscaling-policy.example.yaml -> check-hpa-vpa-invariant.py."""

    def test_loads_through_load_policy(self):
        hpa = _load_script("check-hpa-vpa-invariant.py")
        policy = hpa.load_policy(EXAMPLES / "autoscaling-policy.example.yaml")
        assert ("traefik", "Deployment", "traefik") in policy.chart_native_hpa_targets
        assert policy.cpu_limit_allowlist == set()
        assert policy.vpa_cap_allowlist == set()

    def test_only_documented_top_level_keys(self):
        doc = yaml.safe_load((EXAMPLES / "autoscaling-policy.example.yaml").read_text())
        assert set(doc) <= {
            "chart_native_hpa_targets",
            "cpu_limit_allowlist",
            "vpa_cap_allowlist",
        }


class TestHelmValuesReleasesExample:
    """examples/helm-values-releases.example.yaml -> validate-helm-values.py.

    Parse/validate only: the render path is a network round-trip per release.
    """

    def test_loads_through_load_releases(self):
        vhv = _load_script("validate-helm-values.py")
        releases = vhv.load_releases(str(EXAMPLES / "helm-values-releases.example.yaml"))
        assert len(releases) >= 1
        for rel in releases:
            for key in vhv.REQUIRED_RELEASE_KEYS:
                assert rel.get(key), f"{rel} is missing {key}"

    def test_manifest_paths_are_repo_relative(self):
        vhv = _load_script("validate-helm-values.py")
        for rel in vhv.load_releases(str(EXAMPLES / "helm-values-releases.example.yaml")):
            assert not rel["manifest"].startswith("/")


class TestNetpolExceptExample:
    """examples/netpol-except.example.yaml -> check-netpol-except-parity.py."""

    def test_loads_through_load_config(self, monkeypatch):
        netpol = _load_script("check-netpol-except-parity.py")
        monkeypatch.setattr(netpol, "CANONICAL", dict(netpol.CANONICAL))
        monkeypatch.setattr(netpol, "FENCE_NETS", list(netpol.FENCE_NETS))
        builtin = dict(netpol.CANONICAL)
        netpol.load_config(EXAMPLES / "netpol-except.example.yaml")
        assert netpol.UNRESTRICTED_EGRESS_OK
        assert all(netpol.UNRESTRICTED_EGRESS_OK.values()), "every exemption carries a reason"
        # `canonical_except_lists` REPLACES the built-ins, so an example that
        # ships only one of them silently retires the other for anyone who
        # copies it as their config.
        assert netpol.CANONICAL == builtin, (
            "the example's canonical_except_lists must reproduce every built-in "
            "set exactly — copying it has to be additive, not subtractive"
        )


class TestB2BucketExample:
    """Shape guard for the one example carrying placeholder identifiers."""

    def test_placeholders_are_obvious(self):
        raw = (EXAMPLES / "b2-bucket.example.json").read_text()
        json.loads(raw)
        assert "REPLACE-WITH-" in raw
