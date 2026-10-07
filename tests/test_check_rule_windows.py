"""scripts/check-rule-windows.py: an alert hold must stay inside its lookback."""
from __future__ import annotations

from pathlib import Path

import pytest

from script_loader import load_script

gate = load_script("check-rule-windows.py")

RULES = """groups:
  - name: bursts
    rules:
      - alert: BlockWriteFailed
        expr: sum(count_over_time({job="gw"} |= "ipset[ips] add failed" [%(window)s])) > 20
        for: %(hold)s
        labels: {severity: warning}
"""

PROMETHEUS_RULE = """apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: app
spec:
  groups:
    - name: app
      rules:
        - alert: NotificationsDropping
          expr: sum by (integration) (increase(notifications_failed_total[%(window)s])) > 0
          for: %(hold)s
"""


def _write(tmp_path: Path, body: str, **fmt) -> Path:
    path = tmp_path / "rules.yaml"
    path.write_text(body % fmt)
    return path


class TestToSeconds:
    @pytest.mark.parametrize(
        "text,want",
        [("15m", 900), ("1h", 3600), ("1h30m", 5400), ("1d", 86400), ("500ms", 0.5)],
    )
    def test_a_duration_resolves(self, text, want):
        assert gate.to_seconds(text) == want

    @pytest.mark.parametrize("text", ["", "15", "m", "1 hour", "0s"])
    def test_a_bad_duration_is_an_operator_error(self, text):
        with pytest.raises(gate.OperatorError):
            gate.to_seconds(text)


class TestAccumulatingWindows:
    def test_a_loki_line_filter_bracket_is_not_a_selector(self):
        """`|= "ipset[ips] add failed"` is a string, not a range selector."""
        expr = 'sum(count_over_time({job="gw"} |= "ipset[ips] add failed" [1h]))'
        assert gate.accumulating_windows(expr) == ["1h"]

    def test_a_sustained_idiom_is_not_read(self):
        assert gate.accumulating_windows("rate(errors[5m]) > 0.1") == []
        assert gate.accumulating_windows("max_over_time(up[24h]) == 0") == []

    def test_a_subquery_yields_its_range_not_its_step(self):
        assert gate.accumulating_windows("increase(x[1h:5m])") == ["1h"]

    def test_several_calls_each_contribute(self):
        expr = "increase(a[1h]) > 0 and increase(b[15m]) > 0"
        assert gate.accumulating_windows(expr) == ["1h", "15m"]


class TestCheck:
    def test_a_hold_inside_the_window_passes(self, tmp_path):
        findings, examined = gate.check([_write(tmp_path, RULES, window="1h", hold="15m")])
        assert findings == []
        assert examined == 1

    def test_a_hold_equal_to_the_window_is_a_finding(self, tmp_path):
        """Mutation: flip the window to the hold and the gate must go red."""
        findings, _ = gate.check([_write(tmp_path, RULES, window="1h", hold="1h")])
        assert len(findings) == 1
        assert "BlockWriteFailed" in findings[0]
        assert "[1h]" in findings[0]

    def test_a_hold_past_the_window_is_a_finding(self, tmp_path):
        findings, _ = gate.check([_write(tmp_path, RULES, window="15m", hold="1h")])
        assert len(findings) == 1

    def test_the_tightest_window_bounds_the_hold(self, tmp_path):
        path = tmp_path / "rules.yaml"
        path.write_text(
            "groups:\n  - name: g\n    rules:\n      - alert: A\n"
            "        expr: increase(a[6h]) > 0 and increase(b[30m]) > 0\n"
            "        for: 45m\n"
        )
        findings, _ = gate.check([path])
        assert len(findings) == 1 and "[30m]" in findings[0]

    def test_a_prometheusrule_cr_is_read(self, tmp_path):
        findings, examined = gate.check(
            [_write(tmp_path, PROMETHEUS_RULE, window="6h", hold="6h")]
        )
        assert examined == 1 and len(findings) == 1

    def test_a_rule_without_a_hold_is_skipped(self, tmp_path):
        path = tmp_path / "rules.yaml"
        path.write_text(
            "groups:\n  - name: g\n    rules:\n"
            "      - alert: A\n        expr: increase(a[1h]) > 0\n"
        )
        assert gate.check([path]) == ([], 0)

    def test_an_exemption_needs_a_reason(self):
        with pytest.raises(gate.OperatorError):
            gate.parse_allow(["BlockWriteFailed"])

    def test_an_exempt_alert_is_not_a_finding(self, tmp_path):
        findings, _ = gate.check(
            [_write(tmp_path, RULES, window="1h", hold="1h")],
            {"BlockWriteFailed": "hold is deliberate"},
        )
        assert findings == []

    def test_a_directory_is_walked(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "r.yaml").write_text(RULES % {"window": "1h", "hold": "1h"})
        findings, _ = gate.check([tmp_path])
        assert len(findings) == 1

    def test_an_unparseable_file_is_an_operator_error(self, tmp_path):
        path = tmp_path / "rules.yaml"
        path.write_text("groups: [\n")
        with pytest.raises(gate.OperatorError):
            gate.check([path])


class TestMain:
    def test_a_clean_corpus_exits_zero(self, tmp_path, capsys):
        rc = gate.main([str(_write(tmp_path, RULES, window="1h", hold="15m"))])
        assert rc == 0
        assert "Rule windows OK" in capsys.readouterr().out

    def test_a_finding_exits_one(self, tmp_path, capsys):
        rc = gate.main([str(_write(tmp_path, RULES, window="1h", hold="1h"))])
        assert rc == 1
        assert "BlockWriteFailed" in capsys.readouterr().err

    def test_a_corpus_with_nothing_to_examine_exits_two(self, tmp_path, capsys):
        path = tmp_path / "rules.yaml"
        path.write_text("groups:\n  - name: g\n    rules:\n      - record: a\n        expr: up\n")
        assert gate.main([str(path)]) == 2
        assert "examined" in capsys.readouterr().err

    def test_a_missing_path_exits_two(self, tmp_path, capsys):
        assert gate.main([str(tmp_path / "nope.yaml")]) == 2
        assert "no such file" in capsys.readouterr().err
