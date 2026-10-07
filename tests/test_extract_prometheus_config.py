"""Tests for scripts/extract-prometheus-config.py.

Drives extraction and rendering against the fixture manifests in
tests/fixtures/prometheus/, so a structural regression fails before the CI job.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from script_loader import load_script

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures" / "prometheus"
RELEASE = FIXTURES / "release.yaml"
AM_CONFIG = FIXTURES / "alertmanager-config.yaml"

ext = load_script("extract-prometheus-config.py")


PROMETHEUS_RULE = """\
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: standalone
spec:
  groups:
    - name: standalone.rules
      rules:
        - alert: StandaloneAlert
          expr: up == 0
"""


class TestExtractRules:
    def test_produces_promtool_shaped_groups(self, tmp_path: Path):
        out = tmp_path / "rules.yaml"
        assert ext.extract_rules(out, RELEASE, tmp_path / "absent") == 0
        doc = yaml.safe_load(out.read_text())
        assert "groups" in doc and isinstance(doc["groups"], list)
        assert len(doc["groups"]) == 2, "both rule-map entries must be merged"
        for group in doc["groups"]:
            assert "name" in group
            assert isinstance(group.get("rules"), list)

    def test_every_rule_has_alert_and_expr(self, tmp_path: Path):
        out = tmp_path / "rules.yaml"
        ext.extract_rules(out, RELEASE, tmp_path / "absent")
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
        assert ext.extract_rules(tmp_path / "out.yaml", empty, tmp_path / "absent") == 1

    def test_standalone_prometheusrules_are_unioned_in(self, tmp_path: Path):
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        (rules_dir / "standalone.yaml").write_text(PROMETHEUS_RULE)
        out = tmp_path / "rules.yaml"
        assert ext.extract_rules(out, RELEASE, rules_dir) == 0
        names = [g["name"] for g in yaml.safe_load(out.read_text())["groups"]]
        assert "standalone.rules" in names
        assert len(names) == 3

    def test_a_rule_in_a_subdirectory_or_named_yml_is_extracted(self, tmp_path: Path):
        """promtool must see every PrometheusRule the cluster ships, not only
        the top-level `.yaml` ones."""
        rules_dir = tmp_path / "rules"
        (rules_dir / "nested").mkdir(parents=True)
        (rules_dir / "nested" / "deep.yaml").write_text(PROMETHEUS_RULE)
        (rules_dir / "short.yml").write_text(
            PROMETHEUS_RULE.replace("standalone.rules", "short.rules")
        )
        out = tmp_path / "rules.yaml"
        assert ext.extract_rules(out, RELEASE, rules_dir) == 0
        names = [g["name"] for g in yaml.safe_load(out.read_text())["groups"]]
        assert "standalone.rules" in names
        assert "short.rules" in names

    def test_a_non_prometheusrule_in_a_subdirectory_is_ignored(self, tmp_path: Path):
        rules_dir = tmp_path / "rules"
        (rules_dir / "nested").mkdir(parents=True)
        (rules_dir / "nested" / "kustomization.yaml").write_text(
            "resources:\n  - deep.yaml\n"
        )
        out = tmp_path / "rules.yaml"
        assert ext.extract_rules(out, RELEASE, rules_dir) == 0
        names = [g["name"] for g in yaml.safe_load(out.read_text())["groups"]]
        assert "standalone.rules" not in names

    def test_a_mistyped_rules_map_fails_under_require_release_rules(
        self, tmp_path: Path, capsys
    ):
        """The chart ignores an unknown values key, so the cluster loses the
        alerts too — the gate must not go green on the same typo."""
        release = tmp_path / "release.yaml"
        release.write_text(
            RELEASE.read_text().replace(
                "additionalPrometheusRulesMap", "additionalPrometheusRulesMapp"
            )
        )
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        (rules_dir / "standalone.yaml").write_text(PROMETHEUS_RULE)
        assert ext.extract_rules(
            tmp_path / "out.yaml", release, rules_dir, require_release_rules=True
        ) == 1
        assert str(release) in capsys.readouterr().err

    def test_a_mistyped_rules_map_is_tolerated_by_default(self, tmp_path: Path):
        """A dir-only consumer declares no inline groups at all."""
        release = tmp_path / "release.yaml"
        release.write_text(
            RELEASE.read_text().replace(
                "additionalPrometheusRulesMap", "additionalPrometheusRulesMapp"
            )
        )
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        (rules_dir / "standalone.yaml").write_text(PROMETHEUS_RULE)
        assert ext.extract_rules(tmp_path / "out.yaml", release, rules_dir) == 0

    def test_absent_rules_dir_yields_the_helmrelease_alone(self, tmp_path: Path):
        out = tmp_path / "rules.yaml"
        assert ext.extract_rules(out, RELEASE, tmp_path / "nope") == 0
        assert len(yaml.safe_load(out.read_text())["groups"]) == 2

    def test_a_rules_map_whose_entries_hold_no_groups_fails(self, tmp_path: Path, capsys):
        """A populated additionalPrometheusRulesMap can still yield nothing;
        promtool would be handed an empty file."""
        release = tmp_path / "release.yaml"
        release.write_text(
            "kind: HelmRelease\n"
            "spec:\n"
            "  values:\n"
            "    additionalPrometheusRulesMap:\n"
            "      empty:\n"
            "        groups: []\n"
        )
        assert ext.extract_rules(tmp_path / "out.yaml", release, tmp_path / "absent") == 1
        assert "no rule groups" in capsys.readouterr().err

    def test_empty_release_reports_the_error_not_a_traceback(self, tmp_path: Path, capsys):
        empty = tmp_path / "release.yaml"
        empty.write_text("# only a comment\n")
        assert ext.extract_rules(tmp_path / "out.yaml", empty, tmp_path / "absent") == 1
        assert "no rule groups" in capsys.readouterr().err

    def test_neither_source_holds_a_group_fails(self, tmp_path: Path, capsys):
        """Union semantics: an empty result is the failure, from either source."""
        empty = tmp_path / "release.yaml"
        empty.write_text("# only a comment\n")
        assert ext.extract_rules(
            tmp_path / "out.yaml", empty, tmp_path / "absent"
        ) == 1
        assert "no rule groups found" in capsys.readouterr().err


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

    def test_flux_substitution_tokens_pass_through(self, tmp_path: Path):
        """`${...}` is Flux's, not the extractor's: amtool must see it verbatim
        so the fixture matches the shape every real consumer manifest has."""
        out = tmp_path / "am.yaml"
        assert ext.extract_alertmanager(out, AM_CONFIG) == 0
        assert "${cluster_internal_domain}" in out.read_text()

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

    def test_explicitly_null_spec_key_reports_the_error(self, tmp_path: Path, capsys):
        nulled = tmp_path / "am.yaml"
        nulled.write_text("kind: ExternalSecret\nspec:\n  target:\n")
        assert ext.extract_alertmanager(tmp_path / "out.yaml", nulled) == 1
        assert "not found in" in capsys.readouterr().err

    def test_empty_am_config_reports_the_error_not_a_traceback(self, tmp_path: Path, capsys):
        empty = tmp_path / "am.yaml"
        empty.write_text("")
        assert ext.extract_alertmanager(tmp_path / "out.yaml", empty) == 1
        assert "not found in" in capsys.readouterr().err

    def test_an_unsupported_template_action_fails(self, tmp_path: Path, capsys):
        """The guard that stops a `{{` leaking into the file amtool is handed:
        an action _PLACEHOLDER_RE cannot match must fail, not render."""
        src = tmp_path / "am.yaml"
        src.write_text(
            "kind: ExternalSecret\n"
            "spec:\n"
            "  target:\n"
            "    template:\n"
            "      data:\n"
            "        alertmanager.yaml: |\n"
            "          route:\n"
            "            receiver: {{ if .enabled }}a{{ else }}b{{ end }}\n"
        )
        assert ext.extract_alertmanager(tmp_path / "out.yaml", src) == 1
        assert "unrendered template expression remains" in capsys.readouterr().err

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
        assert ext.main([
            "prog", "rules", str(out), "--release", str(RELEASE),
        ]) == 0
        assert yaml.safe_load(out.read_text())["groups"]

    def test_an_explicit_missing_rules_dir_is_an_error(self, tmp_path: Path, capsys):
        """An unlinted standalone-rules tree is a silently dead alert."""
        out = tmp_path / "rules.yaml"
        assert ext.main([
            "prog", "rules", str(out), "--release", str(RELEASE),
            "--rules-dir", str(tmp_path / "nope"),
        ]) == 2
        assert "does not exist" in capsys.readouterr().err

    def test_an_absent_default_rules_dir_is_an_error_under_the_flag(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        """A consumer that says it keeps rules in a tree must have the tree."""
        monkeypatch.chdir(tmp_path)
        out = tmp_path / "rules.yaml"
        assert ext.main([
            "prog", "rules", str(out), "--release", str(RELEASE),
            "--require-rules-dir",
        ]) == 2
        err = capsys.readouterr().err
        assert str(ext.DEFAULT_RULES_DIR) in err
        assert "--require-rules-dir" in err

    def test_a_release_only_tree_needs_no_flag(self, tmp_path: Path, monkeypatch):
        """The inline-rules shape: no rules directory anywhere."""
        monkeypatch.chdir(tmp_path)
        out = tmp_path / "rules.yaml"
        assert ext.main([
            "prog", "rules", str(out), "--release", str(RELEASE),
        ]) == 0
        assert len(yaml.safe_load(out.read_text())["groups"]) == 2

    def test_a_rules_dir_only_tree_needs_no_flag(self, tmp_path: Path):
        """The standalone-PrometheusRule shape: no inline groups anywhere."""
        release = tmp_path / "release.yaml"
        release.write_text("kind: HelmRelease\nspec:\n  values: {}\n")
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        (rules_dir / "standalone.yaml").write_text(PROMETHEUS_RULE, encoding="utf-8")
        out = tmp_path / "rules.yaml"
        assert ext.main([
            "prog", "rules", str(out), "--release", str(release),
            "--rules-dir", str(rules_dir),
        ]) == 0
        names = [g["name"] for g in yaml.safe_load(out.read_text())["groups"]]
        assert "standalone.rules" in names

    def test_an_explicit_rules_dir_is_included(self, tmp_path: Path):
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        (rules_dir / "standalone.yaml").write_text(PROMETHEUS_RULE, encoding="utf-8")
        out = tmp_path / "rules.yaml"
        assert ext.main([
            "prog", "rules", str(out), "--release", str(RELEASE),
            "--rules-dir", str(rules_dir),
        ]) == 0
        names = [g["name"] for g in yaml.safe_load(out.read_text())["groups"]]
        assert "standalone.rules" in names

    def test_malformed_dummy_pair_rejected(self, tmp_path: Path):
        with pytest.raises(SystemExit):
            ext.main(
                [
                    "prog", "alertmanager", str(tmp_path / "o.yaml"),
                    "--am-config", str(AM_CONFIG), "--dummy", "noequals",
                ]
            )


class TestRepeatableRulesDir:
    def test_two_rules_dirs_are_both_extracted(self, tmp_path: Path):
        """A per-app PrometheusRule tree beside the shared one must reach
        promtool; a single --rules-dir leaves it untested."""
        shared = tmp_path / "rules"
        shared.mkdir()
        (shared / "standalone.yaml").write_text(PROMETHEUS_RULE, encoding="utf-8")
        apps = tmp_path / "apps"
        (apps / "sonarr").mkdir(parents=True)
        (apps / "sonarr" / "prometheusrule.yaml").write_text(
            PROMETHEUS_RULE.replace("standalone.rules", "sonarr.rules"), encoding="utf-8"
        )
        out = tmp_path / "rules.yaml"
        assert ext.main([
            "prog", "rules", str(out), "--release", str(RELEASE),
            "--rules-dir", str(shared), "--rules-dir", str(apps),
        ]) == 0
        names = [g["name"] for g in yaml.safe_load(out.read_text())["groups"]]
        assert "standalone.rules" in names
        assert "sonarr.rules" in names

    def test_one_absent_dir_of_several_is_an_error(self, tmp_path: Path, capsys):
        shared = tmp_path / "rules"
        shared.mkdir()
        (shared / "standalone.yaml").write_text(PROMETHEUS_RULE, encoding="utf-8")
        assert ext.main([
            "prog", "rules", str(tmp_path / "o.yaml"), "--release", str(RELEASE),
            "--rules-dir", str(shared), "--rules-dir", str(tmp_path / "nope"),
        ]) == 2
        err = capsys.readouterr().err
        assert "nope does not exist" in err
        assert str(shared) not in err

    def test_a_list_of_dirs_passed_directly_is_accepted(self, tmp_path: Path):
        shared = tmp_path / "rules"
        shared.mkdir()
        (shared / "standalone.yaml").write_text(PROMETHEUS_RULE, encoding="utf-8")
        out = tmp_path / "rules.yaml"
        assert ext.extract_rules(out, RELEASE, [shared, tmp_path / "absent"]) == 0
        names = [g["name"] for g in yaml.safe_load(out.read_text())["groups"]]
        assert "standalone.rules" in names
