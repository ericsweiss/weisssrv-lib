"""Tests for scripts/extract-prometheus-config.py.

Drives the extraction/rendering against fixture manifests (tests/fixtures/
prometheus/) shaped like a kube-prometheus-stack HelmRelease + an ExternalSecret
Alertmanager template, so a structural regression fails here before the
promtool/amtool CI job downloads its binaries.

Run via `python3 -m pytest tests`.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures" / "prometheus"
RELEASE = FIXTURES / "release.yaml"
AM_CONFIG = FIXTURES / "alertmanager-config.yaml"

_SPEC = importlib.util.spec_from_file_location(
    "extract_prometheus_config",
    REPO / "scripts" / "extract-prometheus-config.py",
)
ext = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ext)  # type: ignore[union-attr]


class TestExtractRules:
    def test_produces_promtool_shaped_groups(self, tmp_path: Path):
        out = tmp_path / "rules.yaml"
        assert ext.extract_rules(out, RELEASE) == 0
        doc = yaml.safe_load(out.read_text())
        assert "groups" in doc and isinstance(doc["groups"], list)
        assert len(doc["groups"]) == 2, "both rule-map entries must be merged"
        for group in doc["groups"]:
            assert "name" in group
            assert isinstance(group.get("rules"), list)

    def test_every_rule_has_alert_and_expr(self, tmp_path: Path):
        out = tmp_path / "rules.yaml"
        ext.extract_rules(out, RELEASE)
        doc = yaml.safe_load(out.read_text())
        for group in doc["groups"]:
            for rule in group["rules"]:
                # recording rules use `record`; alerts use `alert` — either way
                # an `expr` is mandatory (promtool would reject a missing one).
                assert "expr" in rule
                assert "alert" in rule or "record" in rule

    def test_missing_rules_map_fails(self, tmp_path: Path):
        empty = tmp_path / "release.yaml"
        empty.write_text("kind: HelmRelease\nspec:\n  values: {}\n")
        assert ext.extract_rules(tmp_path / "out.yaml", empty) == 1


class TestExtractAlertmanager:
    def test_no_unrendered_template_remains(self, tmp_path: Path):
        out = tmp_path / "am.yaml"
        assert ext.extract_alertmanager(out, AM_CONFIG) == 0
        rendered = out.read_text()
        assert "{{" not in rendered and "}}" not in rendered

    def test_url_placeholders_render_as_urls(self, tmp_path: Path):
        out = tmp_path / "am.yaml"
        ext.extract_alertmanager(out, AM_CONFIG)
        doc = yaml.safe_load(out.read_text())
        assert doc["receivers"][0]["webhook_configs"][0]["url"].startswith("https://")
        assert doc["global"]["smtp_auth_password"] == "dummy"

    def test_dummy_override_wins(self, tmp_path: Path):
        out = tmp_path / "am.yaml"
        ext.extract_alertmanager(
            out, AM_CONFIG, {"chatWebhookUrl": "https://chat.invalid/hook"}
        )
        doc = yaml.safe_load(out.read_text())
        assert doc["receivers"][0]["webhook_configs"][0]["url"] == "https://chat.invalid/hook"

    def test_missing_template_fails(self, tmp_path: Path):
        empty = tmp_path / "am.yaml"
        empty.write_text("kind: ExternalSecret\nspec: {}\n")
        assert ext.extract_alertmanager(tmp_path / "out.yaml", empty) == 1

    def test_placeholder_regex_maps_known_vars(self):
        m = ext._PLACEHOLDER_RE.match("{{ .chatWebhookUrl | quote }}")
        assert m and m.group(1) == "chatWebhookUrl"
        assert ext.dummy_for("chatWebhookUrl").startswith("https://")
        assert ext.dummy_for("smtpPassword") == "dummy"


class TestCli:
    def test_bad_subcommand_exits_2(self):
        with pytest.raises(SystemExit) as exc:
            ext.main(["prog", "bogus", "/tmp/x"])
        assert exc.value.code == 2

    def test_missing_args_exits_2(self):
        with pytest.raises(SystemExit) as exc:
            ext.main(["prog", "rules"])
        assert exc.value.code == 2

    def test_rules_via_cli(self, tmp_path: Path):
        out = tmp_path / "rules.yaml"
        assert ext.main(["prog", "rules", str(out), "--release", str(RELEASE)]) == 0
        assert yaml.safe_load(out.read_text())["groups"]

    def test_malformed_dummy_pair_rejected(self, tmp_path: Path):
        with pytest.raises(SystemExit):
            ext.main(
                [
                    "prog", "alertmanager", str(tmp_path / "o.yaml"),
                    "--am-config", str(AM_CONFIG), "--dummy", "noequals",
                ]
            )
