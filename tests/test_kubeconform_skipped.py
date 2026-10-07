"""Tests for scripts/kubeconform-skipped.py (flux-lint's unvalidated-kind tracker).
"""
from __future__ import annotations

import io
import json

from script_loader import load_script

ks = load_script("kubeconform-skipped.py")


def test_lists_distinct_skipped_kinds():
    payload = {
        "resources": [
            {"version": "v1", "kind": "ConfigMap", "status": "statusValid"},
            {"version": "helm.toolkit.fluxcd.io/v2", "kind": "HelmRelease", "status": "statusSkipped"},
            {"version": "helm.toolkit.fluxcd.io/v2", "kind": "HelmRelease", "status": "statusSkipped"},
            {"version": "example.com/v1", "kind": "Widget", "status": "statusSkipped"},
        ]
    }
    assert ks.skipped_kinds(payload) == [
        "example.com/v1/Widget",
        "helm.toolkit.fluxcd.io/v2/HelmRelease",
    ]


def test_no_skips_returns_empty():
    payload = {"resources": [{"version": "v1", "kind": "Service", "status": "statusValid"}]}
    assert ks.skipped_kinds(payload) == []


def test_missing_resources_key_is_safe():
    assert ks.skipped_kinds({}) == []


def test_unknown_fields_fall_back_to_placeholder():
    payload = {"resources": [{"status": "statusSkipped"}]}
    assert ks.skipped_kinds(payload) == ["?/?"]


def _run(monkeypatch, payload_text: str, argv: list[str]) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(payload_text))
    return ks.main(argv)


_SKIPPED_PAYLOAD = json.dumps(
    {
        "resources": [
            {"version": "helm.toolkit.fluxcd.io/v2", "kind": "HelmRelease",
             "status": "statusSkipped"},
            {"version": "v1", "kind": "ConfigMap", "status": "statusValid"},
        ]
    }
)


def test_reads_baseline_ignoring_comments_and_blanks(tmp_path):
    baseline = tmp_path / "expected.txt"
    baseline.write_text("# comment\n\n  a/B  \nc/D\n")
    assert ks.read_baseline(str(baseline)) == {"a/B", "c/D"}


def test_without_baseline_a_skip_is_informational(monkeypatch):
    assert _run(monkeypatch, _SKIPPED_PAYLOAD, []) == 0


def test_skip_inside_the_baseline_passes(monkeypatch, tmp_path):
    baseline = tmp_path / "expected.txt"
    baseline.write_text("helm.toolkit.fluxcd.io/v2/HelmRelease\n")
    assert _run(monkeypatch, _SKIPPED_PAYLOAD, [str(baseline)]) == 0


def test_skip_outside_the_baseline_fails(monkeypatch, tmp_path):
    """The gate can FAIL: an unlisted skipped kind is the whole point."""
    baseline = tmp_path / "expected.txt"
    baseline.write_text("some.other/v1/Thing\n")
    assert _run(monkeypatch, _SKIPPED_PAYLOAD, [str(baseline)]) == 1


def test_empty_baseline_file_fails_on_any_skip(monkeypatch, tmp_path):
    baseline = tmp_path / "expected.txt"
    baseline.write_text("")
    assert _run(monkeypatch, _SKIPPED_PAYLOAD, [str(baseline)]) == 1


def test_missing_baseline_file_fails(monkeypatch, tmp_path):
    assert _run(monkeypatch, _SKIPPED_PAYLOAD, [str(tmp_path / "nope.txt")]) == 1


def test_unparseable_input_fails(monkeypatch):
    """A parse failure must fail the gate, not pass silently and hide the tracker."""
    assert _run(monkeypatch, "not json", []) == 1


def test_non_object_payload_fails(monkeypatch):
    assert _run(monkeypatch, "[]", []) == 1


def test_an_empty_resource_list_is_an_operator_error(monkeypatch, capsys):
    """A broken pipe or an empty render corpus must not read as a clean pass."""
    assert _run(monkeypatch, '{"resources": []}', []) == 2
    assert "zero resources" in capsys.readouterr().err


def test_an_empty_resource_list_with_a_baseline_is_an_operator_error(
        monkeypatch, tmp_path):
    baseline = tmp_path / "expected.txt"
    baseline.write_text("")
    assert _run(monkeypatch, '{"resources": []}', [str(baseline)]) == 2
