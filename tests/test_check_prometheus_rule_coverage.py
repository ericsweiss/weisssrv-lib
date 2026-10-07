#!/usr/bin/env python3
"""Unit tests for check-prometheus-rule-coverage.py.

Each test adds or renames an alert in a clean tmp tree and asserts the gate
fails, so a shipped alert cannot slip past the coverage accounting.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from script_loader import load_script  # noqa: E402

GATE = load_script("check-prometheus-rule-coverage.py")

RELEASE_PATH = "kubernetes/infrastructure/observability/kps/release.yaml"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _write(
        tmp_path / RELEASE_PATH,
        """\
        apiVersion: helm.toolkit.fluxcd.io/v2
        kind: HelmRelease
        metadata:
          name: kps
        spec:
          values:
            additionalPrometheusRulesMap:
              inline:
                groups:
                  - name: inline
                    rules:
                      - alert: InlineAlertFires
                        expr: up == 0
        """,
    )
    _write(
        tmp_path / "kubernetes/infrastructure/observability/rules/nodes.yaml",
        """\
        apiVersion: monitoring.coreos.com/v1
        kind: PrometheusRule
        metadata:
          name: nodes
        spec:
          groups:
            - name: nodes
              rules:
                - alert: NodeDown
                  expr: up == 0
        """,
    )
    _write(
        tmp_path / "tests/prometheus-rules/nodes.test.yaml",
        """\
        rule_files:
          - rules.yaml
        tests:
          - alert_rule_test:
              - eval_time: 5m
                alertname: NodeDown
                exp_alerts: []
              - eval_time: 5m
                alertname: InlineAlertFires
                exp_alerts: []
        """,
    )
    return tmp_path


def _run(repo: Path, extra: list[str] | None = None) -> int:
    return GATE.main(
        [
            "--repo-root", str(repo),
            "--tests-dir", "tests/prometheus-rules",
            "--release", RELEASE_PATH,
            *(extra or []),
        ]
    )


def test_fully_covered_corpus_passes(repo: Path, capsys):
    assert _run(repo) == 0
    assert "Alert coverage OK" in capsys.readouterr().out


def test_untested_alert_fails(repo: Path, capsys):
    _write(
        repo / "kubernetes/infrastructure/observability/rules/dns.yaml",
        """\
        apiVersion: monitoring.coreos.com/v1
        kind: PrometheusRule
        metadata:
          name: dns
        spec:
          groups:
            - name: dns
              rules:
                - alert: ResolverDown
                  expr: up == 0
        """,
    )
    assert _run(repo) == 1
    err = capsys.readouterr().err
    assert "ResolverDown: shipped but no" in err


def test_declared_untested_alert_passes(repo: Path):
    _write(
        repo / "kubernetes/infrastructure/observability/rules/dns.yaml",
        """\
        apiVersion: monitoring.coreos.com/v1
        kind: PrometheusRule
        metadata:
          name: dns
        spec:
          groups:
            - name: dns
              rules:
                - alert: ResolverDown
                  expr: up == 0
        """,
    )
    assert _run(repo, ["--untested", "ResolverDown=needs a live resolver"]) == 0


def test_stale_untested_entry_for_a_tested_alert_fails(repo: Path, capsys):
    assert _run(repo, ["--untested", "NodeDown=stale"]) == 1
    assert "drop the --untested entry" in capsys.readouterr().err


def test_untested_entry_for_a_nonexistent_alert_fails(repo: Path, capsys):
    assert _run(repo, ["--untested", "GhostAlert=gone"]) == 1
    assert "no rule declares it" in capsys.readouterr().err


def test_untested_without_a_reason_exits_2(repo: Path, capsys):
    assert _run(repo, ["--untested", "NodeDown"]) == 2
    assert "ALERTNAME=REASON" in capsys.readouterr().err


def test_test_naming_a_renamed_alert_fails(repo: Path, capsys):
    """A test left behind by a renamed alert passes against nothing."""
    path = repo / "kubernetes/infrastructure/observability/rules/nodes.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("NodeDown", "NodeNotReady"),
        encoding="utf-8",
    )
    assert _run(repo, ["--untested", "NodeNotReady=new name"]) == 1
    assert "the test passes against nothing" in capsys.readouterr().err


def test_inline_release_rules_are_in_the_corpus(repo: Path, capsys):
    """Dropping the inline alert's test must fail: the HelmRelease's
    additionalPrometheusRulesMap is part of the shipped corpus."""
    path = repo / "tests/prometheus-rules/nodes.test.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "      - eval_time: 5m\n"
            "        alertname: InlineAlertFires\n"
            "        exp_alerts: []\n",
            "",
        ),
        encoding="utf-8",
    )
    assert _run(repo) == 1
    assert "InlineAlertFires: shipped but no" in capsys.readouterr().err


def test_loki_ruler_alerts_are_in_the_corpus(repo: Path, capsys):
    _write(
        repo / "loki-rules/app.yaml",
        """\
        groups:
          - name: logs
            rules:
              - alert: LogErrorBurst
                expr: 'sum(rate({app="x"} |= "error" [5m])) > 1'
        """,
    )
    assert _run(repo, ["--loki-rules-dir", "loki-rules"]) == 1
    assert "LogErrorBurst: shipped but no" in capsys.readouterr().err


def test_template_test_file_counts_as_coverage(repo: Path):
    """A consumer that renders a per-answer test ships it as `.test.yaml.jinja`."""
    nodes = repo / "tests/prometheus-rules/nodes.test.yaml"
    nodes.rename(repo / "tests/prometheus-rules/nodes.test.yaml.jinja")
    assert _run(repo) == 0


def test_empty_corpus_exits_2(tmp_path: Path, capsys):
    (tmp_path / "kubernetes").mkdir()
    (tmp_path / "tests" / "prometheus-rules").mkdir(parents=True)
    assert GATE.main(
        [
            "--repo-root", str(tmp_path),
            "--tests-dir", "tests/prometheus-rules",
            "--no-release",
        ]
    ) == 2
    assert "not a gate" in capsys.readouterr().err


def test_missing_tests_dir_exits_2(repo: Path, capsys):
    assert GATE.main(
        [
            "--repo-root", str(repo),
            "--tests-dir", "tests/rules",
            "--release", RELEASE_PATH,
        ]
    ) == 2
    assert "is not a directory" in capsys.readouterr().err


def test_missing_release_exits_2(repo: Path, capsys):
    (repo / RELEASE_PATH).unlink()
    assert _run(repo) == 2
    assert "does not exist" in capsys.readouterr().err


def test_neither_release_nor_no_release_exits_2(repo: Path, capsys):
    assert GATE.main(
        ["--repo-root", str(repo), "--tests-dir", "tests/prometheus-rules"]
    ) == 2
    assert "exactly one of" in capsys.readouterr().err


def test_both_release_and_no_release_exits_2(repo: Path, capsys):
    assert _run(repo, ["--no-release"]) == 2
    assert "exactly one of" in capsys.readouterr().err


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
