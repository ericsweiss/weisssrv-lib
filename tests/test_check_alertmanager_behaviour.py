"""Tests for scripts/check-alertmanager-behaviour.py.

The `amtool config routes test` arm needs amtool, so it is exercised only when
amtool is on PATH; everything else — config loading, the alertname arms and the
operator-error paths — is offline.
"""
from __future__ import annotations

import importlib.util
import shutil
import textwrap
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "examples" / "alertmanager-behaviour.example.yaml"

_SPEC = importlib.util.spec_from_file_location(
    "check_alertmanager_behaviour", REPO / "scripts" / "check-alertmanager-behaviour.py"
)
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)  # type: ignore[union-attr]


def _config(tmp_path, doc) -> Path:
    path = tmp_path / "am.yaml"
    path.write_text(yaml.safe_dump(doc))
    return path


class TestLoadConfig:
    def test_the_shipped_example_loads(self):
        config = mod.load_config(EXAMPLE)
        assert config.route_cases
        assert config.synthetic_route_alerts
        assert "Watchdog" in config.upstream_alerts

    def test_route_cases_are_required(self, tmp_path):
        with pytest.raises(ValueError, match="route_cases"):
            mod.load_config(_config(tmp_path, {"upstream_alerts": ["X"]}))

    def test_a_case_without_a_receiver_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="receiver \\+ labels"):
            mod.load_config(_config(tmp_path, {"route_cases": [{"labels": ["alertname=X"]}]}))

    def test_optional_sets_default_to_empty(self, tmp_path):
        config = mod.load_config(
            _config(tmp_path, {"route_cases": [{"receiver": "r", "labels": ["alertname=X"]}]})
        )
        assert config.synthetic_route_alerts == set()
        assert config.upstream_alerts == set()


class TestRouteCaseAlertnames:
    def _config_obj(self, tmp_path, **extra):
        doc = {"route_cases": [{"receiver": "r", "labels": ["alertname=Gone", "severity=warning"]}]}
        doc.update(extra)
        return mod.load_config(_config(tmp_path, doc))

    def test_a_case_naming_no_rule_is_reported(self, tmp_path):
        problems = mod.check_route_case_alertnames({"Alive"}, self._config_obj(tmp_path))
        assert problems and "asserting nothing" in problems[0]

    def test_a_declared_upstream_alert_is_accepted(self, tmp_path):
        config = self._config_obj(tmp_path, upstream_alerts=["Gone"])
        assert mod.check_route_case_alertnames({"Alive"}, config) == []

    def test_a_synthetic_alert_is_skipped(self, tmp_path):
        config = self._config_obj(tmp_path, synthetic_route_alerts=["Gone"])
        assert mod.check_route_case_alertnames({"Alive"}, config) == []


class TestInhibits:
    def _am(self, inhibits) -> dict:
        return {"inhibit_rules": inhibits}

    def test_no_inhibit_rules_is_reported(self):
        assert mod.check_inhibits({"route": {"receiver": "r"}}, set(), set()) == [
            "no inhibit_rules found in the Alertmanager config"
        ]

    def test_a_redundant_equal_label_is_reported(self):
        doc = self._am(
            [
                {
                    "source_matchers": ['alertname="A"', 'namespace="ns"'],
                    "target_matchers": ['alertname="B"', 'namespace="ns"'],
                    "equal": ["namespace"],
                }
            ],
        )
        problems = mod.check_inhibits(doc, {"A", "B"}, set())
        assert any("dedups nothing" in p for p in problems)

    def test_every_alternation_member_is_checked(self):
        doc = self._am(
            [
                {
                    "source_matchers": ['alertname="A"'],
                    "target_matchers": ['alertname=~"B|Typoed|C"'],
                }
            ],
        )
        problems = mod.check_inhibits(doc, {"A", "B", "C"}, set())
        assert any("Typoed" in p for p in problems)

    def test_a_non_alternation_regex_is_reported_not_skipped(self):
        doc = self._am(
            [{"source_matchers": ['alertname="A"'], "target_matchers": ['alertname=~"^Kube.*"']}],
        )
        problems = mod.check_inhibits(doc, {"A"}, set())
        assert any("plain alternation" in p for p in problems)

    def test_a_declared_upstream_alert_satisfies_the_matcher(self):
        doc = self._am(
            [{"source_matchers": ['alertname="A"'], "target_matchers": ['alertname="InfoInhibitor"']}],
        )
        assert mod.check_inhibits(doc, {"A"}, {"InfoInhibitor"}) == []


class TestExtractedBodyIsParsedOnce:
    """A malformed extracted body is an OPERATOR error, not a routing finding.

    The extractor copies the alertmanager.yaml block scalar out of the
    ExternalSecret verbatim, so a typo inside it leaves the outer manifest valid
    and only shows up here.
    """

    def test_an_unparseable_body_is_an_operator_error(self, tmp_path):
        path = tmp_path / "alertmanager.yaml"
        path.write_text("route: {receiver: [oops\n")
        with pytest.raises(mod.ExtractionError, match="not parseable YAML"):
            mod._load_extracted(path, "Alertmanager config")

    def test_a_scalar_body_is_an_operator_error(self, tmp_path):
        path = tmp_path / "alertmanager.yaml"
        path.write_text("just a string\n")
        with pytest.raises(mod.ExtractionError, match="not a YAML mapping"):
            mod._load_extracted(path, "Alertmanager config")

    def test_an_empty_body_is_an_operator_error(self, tmp_path):
        path = tmp_path / "alertmanager.yaml"
        path.write_text("")
        with pytest.raises(mod.ExtractionError, match="is empty"):
            mod._load_extracted(path, "Alertmanager config")

    def test_main_exits_two_on_a_malformed_body(self, tmp_path, capsys, monkeypatch):
        """End to end: exit 2, not the traceback-on-1 the old code produced."""
        monkeypatch.setattr(mod.shutil, "which", lambda _name: "/usr/bin/amtool")
        extract = tmp_path / "extract.py"
        extract.write_text(
            "import pathlib, sys\n"
            "kind = sys.argv[1]\n"
            "body = 'groups: []\\n' if kind == 'rules' else 'route: {receiver: [oops\\n'\n"
            "pathlib.Path(sys.argv[2]).write_text(body)\n"
        )
        rc = mod.main(
            ["--config", str(EXAMPLE), "--repo-root", str(tmp_path), "--extract-script", str(extract)]
        )
        assert rc == 2
        assert "not parseable YAML" in capsys.readouterr().err


class TestExtraction:
    def test_the_extractor_runs_from_repo_root_not_the_caller_cwd(self, tmp_path, monkeypatch):
        """`--repo-root` must be the extractor's cwd too.

        The extractor resolves its manifest defaults against the PROCESS cwd, so
        locating the script alone leaves the seam broken from anywhere but the
        consumer root.
        """
        repo = tmp_path / "repo"
        (repo / "kubernetes").mkdir(parents=True)
        (repo / "kubernetes" / "marker.yaml").write_text("ok\n")
        extract = tmp_path / "extract.py"
        extract.write_text(
            "import pathlib, sys\n"
            "pathlib.Path('kubernetes/marker.yaml').read_text()\n"
            "pathlib.Path(sys.argv[2]).write_text('groups: []\\n')\n"
        )
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        am, rules = mod._extract(tmp_path, extract, repo)
        assert am.is_file() and rules.is_file()

    def test_an_extraction_failure_is_an_operator_error(self, tmp_path):
        extract = tmp_path / "extract.py"
        extract.write_text("import sys; sys.exit(1)\n")
        with pytest.raises(mod.ExtractionError):
            mod._extract(tmp_path, extract, tmp_path)


class TestOperatorErrors:
    def test_a_missing_extract_script_exits_two(self, tmp_path, capsys):
        rc = mod.main(["--config", str(EXAMPLE), "--extract-script", str(tmp_path / "nope.py")])
        assert rc == 2
        assert "not found" in capsys.readouterr().err

    def test_a_relative_repo_root_resolves_against_the_caller_cwd(
        self, tmp_path, monkeypatch, capsys
    ):
        """The extractor runs with `cwd=repo_root`, so a relative `--repo-root`
        handed through unresolved is re-resolved by the CHILD and doubles its
        own prefix."""
        repo = tmp_path / "repo"
        (repo / "scripts").mkdir(parents=True)
        (repo / "scripts" / "extract-prometheus-config.py").write_text(
            "import pathlib, sys\n"
            "pathlib.Path(sys.argv[2]).write_text("
            "'groups: []\\n' if sys.argv[1] == 'rules' else 'inhibit_rules: []\\n')\n"
        )
        monkeypatch.setattr(mod.shutil, "which", lambda _name: "/usr/bin/amtool")
        monkeypatch.setattr(mod, "check_routes", lambda *_a, **_k: [])
        monkeypatch.chdir(tmp_path)

        rc = mod.main(["--config", str(EXAMPLE), "--repo-root", "repo"])
        assert "extraction failed" not in capsys.readouterr().err
        assert rc != 2

    def test_a_nonexistent_repo_root_exits_two(self, tmp_path, capsys):
        extract = tmp_path / "extract.py"
        extract.write_text("import sys; sys.exit(0)\n")
        rc = mod.main(
            [
                "--config",
                str(EXAMPLE),
                "--repo-root",
                str(tmp_path / "does-not-exist"),
                "--extract-script",
                str(extract),
            ]
        )
        assert rc == 2
        assert "is not a directory" in capsys.readouterr().err

    @pytest.mark.skipif(shutil.which("amtool") is None, reason="amtool not on PATH")
    def test_a_failing_extraction_exits_two(self, tmp_path, capsys):
        extract = tmp_path / "extract.py"
        extract.write_text("import sys; sys.exit(1)\n")
        rc = mod.main(["--config", str(EXAMPLE), "--extract-script", str(extract)])
        assert rc == 2
        assert "extraction failed" in capsys.readouterr().err

    @pytest.mark.skipif(shutil.which("amtool") is None, reason="amtool not on PATH")
    def test_a_bad_config_exits_two(self, tmp_path, capsys):
        extract = tmp_path / "extract.py"
        extract.write_text("import sys; sys.exit(0)\n")
        bad = tmp_path / "bad.yaml"
        bad.write_text("route_cases: []\n")
        assert mod.main(["--config", str(bad), "--extract-script", str(extract)]) == 2
        assert "route_cases" in capsys.readouterr().err


@pytest.mark.skipif(shutil.which("amtool") is None, reason="amtool not on PATH")
class TestRoutesWithAmtool:
    def test_a_reordered_route_is_caught(self, tmp_path):
        config = tmp_path / "alertmanager.yaml"
        config.write_text(
            textwrap.dedent(
                """
                route:
                  receiver: default
                  routes:
                    - receiver: heartbeat
                      matchers: ['alertname="Watchdog"']
                receivers:
                  - name: default
                  - name: heartbeat
                """
            )
        )
        assert mod.check_routes(config, [("heartbeat", ['alertname="Watchdog"'])]) == []
        assert mod.check_routes(config, [("default", ['alertname="Watchdog"'])])

    def test_a_receiver_that_merely_shares_a_prefix_is_not_a_match(self, tmp_path):
        """`critical-page` must not satisfy an expected `critical` — a prefix
        comparison passes exactly the misroute this gate exists to catch."""
        config = tmp_path / "alertmanager.yaml"
        config.write_text(
            textwrap.dedent(
                """
                route:
                  receiver: critical-page
                receivers:
                  - name: critical
                  - name: critical-page
                """
            )
        )
        assert mod.check_routes(config, [("critical", ['alertname="X"'])])
        assert mod.check_routes(config, [("critical-page", ['alertname="X"'])]) == []
