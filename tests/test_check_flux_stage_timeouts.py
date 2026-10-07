"""scripts/check-flux-stage-timeouts.py: a waiting stage must outlast its releases."""
from __future__ import annotations

import pytest

from script_loader import load_script

gate = load_script("check-flux-stage-timeouts.py")

STAGE = """apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: %(name)s
  namespace: flux-system
spec:
  interval: 10m
  path: ./%(path)s
  prune: true
  wait: %(wait)s
%(timeout)s"""

RELEASE = """apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: %(name)s
spec:
  timeout: %(timeout)s
  chart:
    spec:
      chart: %(name)s
"""


def _tree(tmp_path, *, stage_timeout="20m", wait="true", release_timeout="15m", path="apps"):
    clusters = tmp_path / "kubernetes/clusters/prod"
    clusters.mkdir(parents=True)
    (clusters / "apps.yaml").write_text(
        STAGE
        % {
            "name": "apps",
            "path": path,
            "wait": wait,
            "timeout": "  timeout: %s\n" % stage_timeout if stage_timeout else "",
        },
        encoding="utf-8",
    )
    if release_timeout:
        apps = tmp_path / "apps/demo"
        apps.mkdir(parents=True)
        (apps / "release.yaml").write_text(
            RELEASE % {"name": "demo", "timeout": release_timeout}, encoding="utf-8"
        )
    return tmp_path, tmp_path / "kubernetes/clusters"


def test_a_stage_with_headroom_passes(tmp_path):
    code, report = gate.check(*_tree(tmp_path))
    assert code == 0
    assert "outlast" in "\n".join(report)


def test_a_stage_that_does_not_outlast_a_release_fails(tmp_path):
    code, report = gate.check(*_tree(tmp_path, stage_timeout="10m"))
    assert code == 1
    assert "does not exceed" in "\n".join(report)


def test_an_equal_timeout_is_too_tight(tmp_path):
    """No headroom at all: the stage expires in the same instant."""
    code, _ = gate.check(*_tree(tmp_path, stage_timeout="15m"))
    assert code == 1


def test_a_waiting_stage_without_an_explicit_timeout_fails(tmp_path):
    """The first hole the per-repo copies had: it dropped out of the walk."""
    code, report = gate.check(*_tree(tmp_path, stage_timeout=""))
    assert code == 1
    assert "no explicit spec.timeout" in "\n".join(report)


def test_a_stale_stage_path_is_an_operator_error(tmp_path):
    """The second hole: a path that no longer exists must not read as a pass."""
    code, report = gate.check(*_tree(tmp_path, path="moved-away"))
    assert code == 2
    assert "does not resolve" in "\n".join(report)


def test_a_corpus_with_no_waiting_stage_is_an_operator_error(tmp_path):
    code, report = gate.check(*_tree(tmp_path, wait="false"))
    assert code == 2
    assert "no wait:true" in "\n".join(report)


def test_a_corpus_with_no_release_timeout_is_an_operator_error(tmp_path):
    """Nothing to compare against is not agreement."""
    repo, clusters = _tree(tmp_path)
    (repo / "apps/demo/release.yaml").write_text(
        "apiVersion: helm.toolkit.fluxcd.io/v2\nkind: HelmRelease\n"
        "metadata:\n  name: demo\nspec:\n  chart:\n    spec:\n      chart: demo\n",
        encoding="utf-8",
    )
    code, report = gate.check(repo, clusters)
    assert code == 2
    assert "examined nothing" in "\n".join(report)


def test_an_unparseable_manifest_is_an_operator_error(tmp_path):
    repo, clusters = _tree(tmp_path)
    (clusters / "prod" / "broken.yaml").write_text("a: [unclosed\n", encoding="utf-8")
    code, report = gate.check(repo, clusters)
    assert code == 2
    assert "broken.yaml" in "\n".join(report)


def test_the_phase_timeouts_count_too(tmp_path):
    """`install.timeout` and `upgrade.timeout` override spec.timeout per phase."""
    repo, clusters = _tree(tmp_path, release_timeout="5m")
    (repo / "apps/demo/release.yaml").write_text(
        "apiVersion: helm.toolkit.fluxcd.io/v2\n"
        "kind: HelmRelease\n"
        "metadata:\n  name: demo\n"
        "spec:\n  timeout: 5m\n  upgrade:\n    timeout: 30m\n",
        encoding="utf-8",
    )
    code, report = gate.check(repo, clusters)
    assert code == 1
    assert "1800s" in "\n".join(report)


@pytest.mark.parametrize(
    "value,seconds", [("90s", 90), ("15m", 900), ("1h30m", 5400), ("2h", 7200)]
)
def test_to_seconds_parses_the_flux_spellings(value, seconds):
    assert gate.to_seconds(value) == seconds


def test_an_unparseable_duration_is_an_operator_error():
    with pytest.raises(gate.Vacuous):
        gate.to_seconds("soon")


def test_too_tight_compares_rather_than_trusting_the_corpus():
    """Mutation case on the comparison itself."""
    releases = {"release.yaml:app": 900}
    assert gate.too_tight(900, releases) == ["release.yaml:app's 900s"]
    assert gate.too_tight(600, releases)
    assert gate.too_tight(901, releases) == []


def test_main_reports_a_missing_cluster_dir(tmp_path, capsys):
    code = gate.main(["--repo-root", str(tmp_path), "--cluster-dir", "nope"])
    assert code == 2
    assert "does not exist" in capsys.readouterr().err
