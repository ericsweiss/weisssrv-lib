#!/usr/bin/env python3
"""Unit tests for check-helm-values-coverage.py.

Each test mutates a clean tmp tree so the registry and the manifests disagree,
and asserts the gate fails: a registry nobody checks is inert data.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from script_loader import load_script  # noqa: E402

GATE = load_script("check-helm-values-coverage.py")

RELEASE = """\
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: {name}
  namespace: {name}
spec:
  chart:
    spec:
      chart: {chart}
      version: "1.0.0"
      sourceRef:
        kind: HelmRepository
        name: {repo}
"""


def _release(root: Path, rel: str, name: str, chart: str, repo: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(RELEASE.format(name=name, chart=chart, repo=repo), encoding="utf-8")


def _registry(root: Path, doc: dict) -> Path:
    path = root / "scripts" / "helm-values-releases.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _release(
        tmp_path, "kubernetes/apps/alpha/release.yaml", "alpha", "alpha", "alpha-charts"
    )
    _registry(
        tmp_path,
        {
            "releases": [
                {
                    "name": "alpha",
                    "manifest": "kubernetes/apps/alpha/release.yaml",
                    "chart": "alpha",
                    "repo_name": "alpha-charts",
                }
            ]
        },
    )
    return tmp_path


def _run(repo: Path) -> int:
    return GATE.main(["--repo-root", str(repo)])


def test_registry_covering_every_release_passes(repo: Path, capsys):
    assert _run(repo) == 0
    assert "coverage OK" in capsys.readouterr().out


def test_unlisted_helmrelease_fails(repo: Path, capsys):
    _release(
        repo, "kubernetes/apps/beta/release.yaml", "beta", "beta", "beta-charts"
    )
    assert _run(repo) == 1
    err = capsys.readouterr().err
    assert "kubernetes/apps/beta/release.yaml" in err
    assert "rendered by nothing" in err


def test_excluded_helmrelease_passes(repo: Path):
    _release(
        repo, "kubernetes/apps/beta/release.yaml", "beta", "beta", "beta-charts"
    )
    doc = yaml.safe_load(
        (repo / "scripts" / "helm-values-releases.yaml").read_text(encoding="utf-8")
    )
    doc["excluded"] = {
        "kubernetes/apps/beta/release.yaml": "chart comes from a Kustomize component"
    }
    _registry(repo, doc)
    assert _run(repo) == 0


def test_stale_exclusion_fails(repo: Path, capsys):
    doc = yaml.safe_load(
        (repo / "scripts" / "helm-values-releases.yaml").read_text(encoding="utf-8")
    )
    doc["excluded"] = {"kubernetes/apps/ghost/release.yaml": "gone"}
    _registry(repo, doc)
    assert _run(repo) == 1
    assert "drop the stale exclusion" in capsys.readouterr().err


def test_exclusion_without_a_reason_fails(repo: Path, capsys):
    _release(
        repo, "kubernetes/apps/beta/release.yaml", "beta", "beta", "beta-charts"
    )
    doc = yaml.safe_load(
        (repo / "scripts" / "helm-values-releases.yaml").read_text(encoding="utf-8")
    )
    doc["excluded"] = {"kubernetes/apps/beta/release.yaml": ""}
    _registry(repo, doc)
    assert _run(repo) == 1
    assert "carries no reason" in capsys.readouterr().err


def test_listed_and_excluded_fails(repo: Path, capsys):
    doc = yaml.safe_load(
        (repo / "scripts" / "helm-values-releases.yaml").read_text(encoding="utf-8")
    )
    doc["excluded"] = {"kubernetes/apps/alpha/release.yaml": "unparseable output"}
    _registry(repo, doc)
    assert _run(repo) == 1
    assert "the exclusion is dead" in capsys.readouterr().err


def test_entry_naming_a_missing_manifest_fails(repo: Path, capsys):
    (repo / "kubernetes" / "apps" / "alpha" / "release.yaml").unlink()
    assert _run(repo) == 2
    assert "not a gate" in capsys.readouterr().err


def test_entry_with_a_wrong_chart_fails(repo: Path, capsys):
    doc = yaml.safe_load(
        (repo / "scripts" / "helm-values-releases.yaml").read_text(encoding="utf-8")
    )
    doc["releases"][0]["chart"] = "alpha-oss"
    _registry(repo, doc)
    assert _run(repo) == 1
    assert "the cluster does not run" in capsys.readouterr().err


def test_entry_with_a_wrong_repo_name_fails(repo: Path, capsys):
    doc = yaml.safe_load(
        (repo / "scripts" / "helm-values-releases.yaml").read_text(encoding="utf-8")
    )
    doc["releases"][0]["repo_name"] = "other-charts"
    _registry(repo, doc)
    assert _run(repo) == 1
    assert "wrong HelmRepository" in capsys.readouterr().err


def test_entry_missing_a_required_key_fails(repo: Path, capsys):
    doc = yaml.safe_load(
        (repo / "scripts" / "helm-values-releases.yaml").read_text(encoding="utf-8")
    )
    del doc["releases"][0]["chart"]
    _registry(repo, doc)
    assert _run(repo) == 1
    assert "is missing chart" in capsys.readouterr().err


def test_duplicate_manifest_entry_fails(repo: Path, capsys):
    doc = yaml.safe_load(
        (repo / "scripts" / "helm-values-releases.yaml").read_text(encoding="utf-8")
    )
    doc["releases"].append({**doc["releases"][0], "name": "alpha-again"})
    _registry(repo, doc)
    assert _run(repo) == 1
    assert "is listed twice" in capsys.readouterr().err


def test_duplicate_release_name_fails(repo: Path, capsys):
    _release(
        repo, "kubernetes/apps/beta/release.yaml", "beta", "beta", "beta-charts"
    )
    doc = yaml.safe_load(
        (repo / "scripts" / "helm-values-releases.yaml").read_text(encoding="utf-8")
    )
    doc["releases"].append(
        {
            "name": "alpha",
            "manifest": "kubernetes/apps/beta/release.yaml",
            "chart": "beta",
            "repo_name": "beta-charts",
        }
    )
    _registry(repo, doc)
    assert _run(repo) == 1
    assert "are named 'alpha'" in capsys.readouterr().err


def test_empty_corpus_exits_2(tmp_path: Path, capsys):
    (tmp_path / "kubernetes").mkdir()
    _registry(
        tmp_path,
        {"releases": [{"name": "a", "manifest": "kubernetes/a.yaml", "chart": "a"}]},
    )
    assert _run(tmp_path) == 2
    assert "not a gate" in capsys.readouterr().err


def test_empty_registry_exits_2(repo: Path, capsys):
    _registry(repo, {"releases": []})
    assert _run(repo) == 2
    assert "non-empty `releases:` list" in capsys.readouterr().err


def test_missing_manifest_dir_exits_2(repo: Path, capsys):
    assert GATE.main(["--repo-root", str(repo), "--manifest-dir", "k8s"]) == 2
    assert "does not exist" in capsys.readouterr().err


def test_unparseable_manifest_holding_a_helmrelease_is_not_counted(
    repo: Path, capsys
):
    """A file PyYAML refuses contributes no HelmRelease, so it cannot be
    silently counted as covered."""
    path = repo / "kubernetes" / "apps" / "broken" / "release.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("kind: HelmRelease\nvalues: [unclosed\n", encoding="utf-8")
    assert _run(repo) == 0
    doc = yaml.safe_load(
        (repo / "scripts" / "helm-values-releases.yaml").read_text(encoding="utf-8")
    )
    doc["excluded"] = {"kubernetes/apps/broken/release.yaml": "unparseable output"}
    _registry(repo, doc)
    assert _run(repo) == 1
    assert "drop the stale exclusion" in capsys.readouterr().err


def test_release_textwrap_fixture_is_valid_yaml():
    """Guards the fixture itself: a malformed template would make every
    coverage assertion vacuous."""
    doc = yaml.safe_load(
        textwrap.dedent(RELEASE.format(name="x", chart="x", repo="x"))
    )
    assert doc["kind"] == "HelmRelease"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
