"""Tests for scripts/check-alertmanager-behaviour.py.

The `amtool config routes test` arm runs against a stub amtool on a controlled
PATH; one arm additionally drives the real binary when it is installed.
"""
from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from _helpers import require_tool
from script_loader import load_script

REPO = Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "examples" / "alertmanager-behaviour.example.yaml"

mod = load_script("check-alertmanager-behaviour.py")


AMTOOL_STUB = f"""\
#!{sys.executable}
import os
import sys

print(os.environ.get("AMTOOL_OUTPUT", ""), file=sys.stderr if os.environ.get(
    "AMTOOL_ON_STDERR") else sys.stdout)
sys.exit(int(os.environ.get("AMTOOL_RC", "0")))
"""


@pytest.fixture()
def stub_amtool(tmp_path, monkeypatch):
    """Put a scripted `amtool` first on PATH and return a setter for what it says."""
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    stub = bin_dir / "amtool"
    stub.write_text(AMTOOL_STUB)
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    def _say(output="", rc=0, on_stderr=False):
        monkeypatch.setenv("AMTOOL_OUTPUT", output)
        monkeypatch.setenv("AMTOOL_RC", str(rc))
        if on_stderr:
            monkeypatch.setenv("AMTOOL_ON_STDERR", "1")

    _say()
    return _say


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

    def test_a_negated_alertname_naming_no_rule_is_reported(self):
        doc = self._am(
            [
                {
                    "source_matchers": ['alertname="A"'],
                    "target_matchers": ['severity="warning"', 'alertname!~"B|Typoed"'],
                }
            ],
        )
        problems = mod.check_inhibits(doc, {"A", "B"}, set())
        assert any("exempts alertname(s) ['Typoed']" in p for p in problems)

    def test_a_well_formed_negated_alertname_is_clean(self):
        doc = self._am(
            [
                {
                    "source_matchers": ['alertname="A"'],
                    "target_matchers": ['severity="warning"', 'alertname!~"B|C"'],
                }
            ],
        )
        assert mod.check_inhibits(doc, {"A", "B", "C"}, set()) == []

    def test_a_non_alternation_negated_regex_is_reported_not_skipped(self):
        doc = self._am(
            [
                {
                    "source_matchers": ['alertname="A"'],
                    "target_matchers": ['severity="warning"', 'alertname!~"^Kube.*"'],
                }
            ],
        )
        problems = mod.check_inhibits(doc, {"A"}, set())
        assert any("plain alternation" in p for p in problems)

    def test_a_declared_upstream_alert_satisfies_the_matcher(self):
        doc = self._am(
            [{"source_matchers": ['alertname="A"'], "target_matchers": ['alertname="InfoInhibitor"']}],
        )
        assert mod.check_inhibits(doc, {"A"}, {"InfoInhibitor"}) == []


class TestEscalationInhibits:
    """A warning with a critical sibling and no inhibit rule pages twice."""

    RULES = {
        "groups": [
            {
                "name": "disk",
                "rules": [
                    {"alert": "DiskUsageWarning", "expr": "x", "labels": {"severity": "warning"}},
                    {
                        "alert": "DiskUsageWarningProlonged",
                        "expr": "x",
                        "labels": {"severity": "critical"},
                    },
                ],
            }
        ]
    }

    def _config(self, tmp_path, **extra):
        doc = {"route_cases": [{"receiver": "r", "labels": ["alertname=X"]}]}
        doc.update(extra)
        return mod.load_config(_config(tmp_path, doc))

    def test_the_pair_is_derived_from_the_rules_corpus(self, tmp_path):
        config = self._config(tmp_path)
        assert mod.derive_escalation_pairs(self.RULES, config.escalation_suffixes) == [
            ("DiskUsageWarningProlonged", "DiskUsageWarning")
        ]

    def test_a_pair_with_no_inhibit_rule_is_reported(self, tmp_path):
        problems = mod.check_escalation_inhibits(
            {"inhibit_rules": []}, self.RULES, self._config(tmp_path)
        )
        assert problems and "pages twice" in problems[0]
        assert "DiskUsageWarningProlonged -> DiskUsageWarning" in problems[0]

    def test_an_inhibited_pair_is_clean(self, tmp_path):
        am = {
            "inhibit_rules": [
                {
                    "source_matchers": ['alertname="DiskUsageWarningProlonged"'],
                    "target_matchers": ['alertname="DiskUsageWarning"'],
                    "equal": ["instance"],
                }
            ]
        }
        assert mod.check_escalation_inhibits(am, self.RULES, self._config(tmp_path)) == []

    def test_a_reversed_inhibit_rule_does_not_satisfy_the_pair(self, tmp_path):
        """Inhibiting the critical with the warning silences the page, not the noise."""
        am = {
            "inhibit_rules": [
                {
                    "source_matchers": ['alertname="DiskUsageWarning"'],
                    "target_matchers": ['alertname="DiskUsageWarningProlonged"'],
                }
            ]
        }
        problems = mod.check_escalation_inhibits(am, self.RULES, self._config(tmp_path))
        assert problems and "pages twice" in problems[0]

    def test_a_missing_shared_equal_label_is_reported(self, tmp_path):
        am = {
            "inhibit_rules": [
                {
                    "source_matchers": ['alertname="DiskUsageWarningProlonged"'],
                    "target_matchers": ['alertname="DiskUsageWarning"'],
                }
            ]
        }
        config = self._config(tmp_path, escalation_equal=["instance"])
        problems = mod.check_escalation_inhibits(am, self.RULES, config)
        assert problems and "equal:['instance']" in problems[0]

    def test_equal_labels_split_across_two_rules_is_reported(self, tmp_path):
        """Two rules each missing a different label each silence unrelated instances."""
        am = {
            "inhibit_rules": [
                {
                    "source_matchers": ['alertname="DiskUsageWarningProlonged"'],
                    "target_matchers": ['alertname="DiskUsageWarning"'],
                    "equal": ["instance"],
                },
                {
                    "source_matchers": ['alertname="DiskUsageWarningProlonged"'],
                    "target_matchers": ['alertname="DiskUsageWarning"'],
                    "equal": ["device"],
                },
            ]
        }
        config = self._config(tmp_path, escalation_equal=["instance", "device"])
        problems = mod.check_escalation_inhibits(am, self.RULES, config)
        assert problems and "equal:['device']" in problems[0]
        assert "Rule 0 comes closest with equal:['instance']" in problems[0]

    def test_one_rule_carrying_every_equal_label_is_clean(self, tmp_path):
        am = {
            "inhibit_rules": [
                {
                    "source_matchers": ['alertname="DiskUsageWarningProlonged"'],
                    "target_matchers": ['alertname="DiskUsageWarning"'],
                    "equal": ["device"],
                },
                {
                    "source_matchers": ['alertname="DiskUsageWarningProlonged"'],
                    "target_matchers": ['alertname="DiskUsageWarning"'],
                    "equal": ["instance", "device"],
                },
            ]
        }
        config = self._config(tmp_path, escalation_equal=["instance", "device"])
        assert mod.check_escalation_inhibits(am, self.RULES, config) == []

    def test_a_declared_exception_is_skipped(self, tmp_path):
        config = self._config(
            tmp_path,
            escalation_exceptions=[
                {"critical": "DiskUsageWarningProlonged", "warning": "DiskUsageWarning"}
            ],
        )
        assert mod.check_escalation_inhibits({"inhibit_rules": []}, self.RULES, config) == []

    def test_an_explicitly_declared_pair_is_checked(self, tmp_path):
        """A pair whose names share no stem is invisible to the suffix walk."""
        config = self._config(
            tmp_path,
            escalation_pairs=[{"critical": "EtcdQuorumLost", "warning": "EtcdQuorumAtRisk"}],
        )
        problems = mod.check_escalation_inhibits({"inhibit_rules": []}, self.RULES, config)
        assert any("EtcdQuorumLost -> EtcdQuorumAtRisk" in p for p in problems)

    def test_a_warning_with_no_critical_sibling_is_not_a_pair(self, tmp_path):
        rules = {
            "groups": [
                {
                    "name": "disk",
                    "rules": [
                        {"alert": "Lonely", "expr": "x", "labels": {"severity": "warning"}}
                    ],
                }
            ]
        }
        assert mod.check_escalation_inhibits(
            {"inhibit_rules": []}, rules, self._config(tmp_path)
        ) == []

    def test_a_malformed_pair_entry_is_an_operator_error(self, tmp_path):
        with pytest.raises(ValueError, match="critical \\+ warning"):
            self._config(tmp_path, escalation_pairs=[{"warning": "A"}])


class TestExtractedBodyIsParsedOnce:
    """A malformed extracted body is an operator error, not a routing finding."""

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

    def test_main_exits_two_when_amtool_is_absent(self, tmp_path, capsys, monkeypatch):
        """amtool is the whole route check, so its absence is an operator error."""
        monkeypatch.setattr(mod.shutil, "which", lambda _name: None)
        extract = tmp_path / "extract.py"
        extract.write_text("")
        rc = mod.main(
            ["--config", str(EXAMPLE), "--repo-root", str(tmp_path), "--extract-script", str(extract)]
        )
        assert rc == 2
        assert "amtool not found on PATH" in capsys.readouterr().err

    def test_main_exits_two_on_a_malformed_body(self, tmp_path, capsys, monkeypatch):
        """A malformed body exits 2 end to end."""
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
        """`--repo-root` is the extractor's cwd, not the caller's."""
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

    def test_an_extract_arg_reaches_the_child(self, tmp_path):
        """Without the seam a consumer cannot tell the extractor its rule shape."""
        extract = tmp_path / "extract.py"
        extract.write_text(
            "import pathlib, sys\n"
            "assert '--require-rules-dir' in sys.argv, sys.argv\n"
            "pathlib.Path(sys.argv[2]).write_text('groups: []\\n')\n"
        )
        am, rules = mod._extract(
            tmp_path, extract, tmp_path, ["--require-rules-dir"]
        )
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

    def test_a_failing_extraction_exits_two(self, tmp_path, capsys, stub_amtool):
        extract = tmp_path / "extract.py"
        extract.write_text("import sys; sys.exit(1)\n")
        rc = mod.main(["--config", str(EXAMPLE), "--extract-script", str(extract)])
        assert rc == 2
        assert "extraction failed" in capsys.readouterr().err

    def test_a_bad_config_exits_two(self, tmp_path, capsys, stub_amtool):
        extract = tmp_path / "extract.py"
        extract.write_text("import sys; sys.exit(0)\n")
        bad = tmp_path / "bad.yaml"
        bad.write_text("route_cases: []\n")
        assert mod.main(["--config", str(bad), "--extract-script", str(extract)]) == 2
        assert "route_cases" in capsys.readouterr().err


class TestRoutes:
    """check_routes' own contribution is the exact first-token comparison and the
    fail-loud handling of a broken amtool; a scripted stub covers both."""

    CONFIG = "route:\n  receiver: default\n"

    def _am(self, tmp_path) -> Path:
        path = tmp_path / "alertmanager.yaml"
        path.write_text(self.CONFIG)
        return path

    def test_the_resolved_receiver_matches(self, tmp_path, stub_amtool):
        stub_amtool(output="heartbeat")
        assert mod.check_routes(self._am(tmp_path), [("heartbeat", ['alertname="W"'])]) == []

    def test_a_different_receiver_is_reported(self, tmp_path, stub_amtool):
        stub_amtool(output="default")
        problems = mod.check_routes(self._am(tmp_path), [("heartbeat", ['alertname="W"'])])
        assert problems and "heartbeat" in problems[0]

    def test_only_the_first_token_of_a_tree_is_the_resolution(self, tmp_path, stub_amtool):
        stub_amtool(output="critical-page\ncritical")
        assert mod.check_routes(self._am(tmp_path), [("critical-page", ['alertname="X"'])]) == []

    def test_a_receiver_that_merely_shares_a_prefix_is_not_a_match(self, tmp_path, stub_amtool):
        """`critical-page` must not satisfy an expected `critical` — a prefix
        comparison passes exactly the misroute this gate exists to catch."""
        stub_amtool(output="critical-page")
        assert mod.check_routes(self._am(tmp_path), [("critical", ['alertname="X"'])])

    def test_a_failing_amtool_raises_rather_than_reporting_a_misroute(
        self, tmp_path, stub_amtool
    ):
        stub_amtool(output="amtool: error: unknown flag --config.file", rc=1, on_stderr=True)
        with pytest.raises(mod.ExtractionError, match="amtool"):
            mod.check_routes(self._am(tmp_path), [("heartbeat", ['alertname="W"'])])

    def test_a_failing_amtool_exits_two_from_main(self, tmp_path, capsys, stub_amtool):
        stub_amtool(output="amtool: error: cannot load config", rc=1, on_stderr=True)
        extract = tmp_path / "extract.py"
        extract.write_text(
            "import pathlib, sys\n"
            "pathlib.Path(sys.argv[2]).write_text("
            "'groups: []\\n' if sys.argv[1] == 'rules' else 'inhibit_rules: []\\n')\n"
        )
        rc = mod.main(["--config", str(EXAMPLE), "--extract-script", str(extract)])
        err = capsys.readouterr().err
        assert rc == 2, err
        assert "amtool" in err
        assert "does not match the expected receivers" not in err


class TestRoutesWithRealAmtool:
    """One arm against the real binary, so the stub's output shape cannot drift
    away from what amtool actually prints."""

    @pytest.fixture(autouse=True)
    def _real_amtool(self):
        require_tool(
            "amtool",
            "alertmanager-behaviour",
            "Install the alertmanager release tarball's amtool onto PATH in the job.",
        )

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


INFO_RULES = {
    "groups": [
        {
            "name": "info",
            "rules": [
                {
                    "alert": "NotificationsDropping",
                    "expr": "sum by (integration) (increase(failed_total[6h])) > 0",
                    "labels": {"severity": "info"},
                },
                {
                    "alert": "PodInfo",
                    "expr": "sum by (namespace, pod) (kube_pod_info) > 0",
                    "labels": {"severity": "info"},
                },
            ],
        }
    ]
}


def _info_inhibit(exempt: str | None = None, equal=("namespace",)) -> dict:
    target = ['severity="info"']
    if exempt:
        target.append('alertname!~"%s"' % exempt)
    return {
        "inhibit_rules": [
            {
                "source_matchers": ['alertname="InfoInhibitor"'],
                "target_matchers": target,
                "equal": list(equal),
            }
        ]
    }


class TestEqualLabelScope:
    """An alert dropping every `equal:` label is muted whenever the source fires."""

    def test_an_alert_dropping_the_only_equal_label_is_reported(self):
        problems = mod.check_equal_label_scope(_info_inhibit(), INFO_RULES)
        assert len(problems) == 1
        assert "NotificationsDropping" in problems[0]

    def test_naming_it_in_the_exemption_matcher_clears_it(self):
        inhibit = _info_inhibit(exempt="NotificationsDropping")
        assert mod.check_equal_label_scope(inhibit, INFO_RULES) == []

    def test_an_alert_that_keeps_the_label_is_not_reported(self):
        inhibit = _info_inhibit(exempt="NotificationsDropping")
        assert "PodInfo" not in " ".join(
            mod.check_equal_label_scope(inhibit, INFO_RULES)
        )

    def test_an_equal_set_naming_alertname_always_scopes(self):
        inhibit = _info_inhibit(equal=("alertname", "namespace"))
        assert mod.check_equal_label_scope(inhibit, INFO_RULES) == []

    def test_one_surviving_equal_label_still_scopes_the_pair(self):
        inhibit = _info_inhibit(equal=("namespace", "integration"))
        assert mod.check_equal_label_scope(inhibit, INFO_RULES) == []

    def test_a_target_matcher_the_alert_fails_is_not_examined(self):
        inhibit = _info_inhibit()
        inhibit["inhibit_rules"][0]["target_matchers"] = ['severity="warning"']
        assert mod.check_equal_label_scope(inhibit, INFO_RULES) == []

    def test_a_target_pinned_to_an_exact_alertname_is_a_pair_not_a_scope(self):
        inhibit = _info_inhibit()
        inhibit["inhibit_rules"][0]["target_matchers"] = ['alertname="NotificationsDropping"']
        assert mod.check_equal_label_scope(inhibit, INFO_RULES) == []


SCOPE_RULES = {
    "groups": [
        {
            "name": "probes",
            "rules": [
                {
                    "alert": "AggregateDown",
                    "expr": (
                        'count(max by (instance) (probe_success{instance=~"https://.*",'
                        'instance!~"https://(git|auth)"}) == 0) >= 3'
                    ),
                    "labels": {"severity": "critical"},
                },
                {"alert": "EndpointDown", "expr": "probe_success == 0"},
            ],
        }
    ]
}


def _scope_inhibit(*target: str) -> dict:
    return {
        "inhibit_rules": [
            {
                "source_matchers": ['alertname="AggregateDown"'],
                "target_matchers": list(target),
            }
        ]
    }


class TestInhibitTargetScope:
    """A target must not admit series the source's own selector excludes."""

    def test_a_positive_only_target_is_reported(self):
        inhibit = _scope_inhibit('alertname="EndpointDown"', 'instance=~"https://.*"')
        problems = mod.check_inhibit_target_scope(inhibit, SCOPE_RULES)
        assert len(problems) == 1
        assert "AggregateDown" in problems[0] and "no lookahead" in problems[0]

    def test_a_second_negative_matcher_clears_it(self):
        inhibit = _scope_inhibit(
            'alertname="EndpointDown"',
            'instance=~"https://.*"',
            'instance!~"https://(git|auth)"',
        )
        assert mod.check_inhibit_target_scope(inhibit, SCOPE_RULES) == []

    def test_a_target_not_scoping_the_label_is_left_alone(self):
        inhibit = _scope_inhibit('alertname="EndpointDown"')
        assert mod.check_inhibit_target_scope(inhibit, SCOPE_RULES) == []

    def test_a_source_outside_the_corpus_is_left_alone(self):
        inhibit = _scope_inhibit('alertname="EndpointDown"', 'instance=~"https://.*"')
        inhibit["inhibit_rules"][0]["source_matchers"] = ['alertname="Upstream"']
        assert mod.check_inhibit_target_scope(inhibit, SCOPE_RULES) == []


class TestAggregatesAway:
    def test_a_by_clause_omitting_the_label_drops_it(self):
        assert mod.aggregates_away("sum by (integration) (x)", "namespace") is True

    def test_a_by_clause_keeping_the_label_does_not(self):
        assert mod.aggregates_away("sum by (namespace, pod) (x)", "namespace") is False

    def test_an_expression_with_no_aggregation_is_never_reported(self):
        assert mod.aggregates_away("up == 0", "namespace") is False


class TestMatcherValueParity:
    """An inhibit matcher written to mirror a rule's own selector must match it."""

    RULES = {
        "groups": [
            {
                "name": "ingress",
                "rules": [
                    {
                        "alert": "ExternalIngressDown",
                        "expr": (
                            'probe_success{instance=~"a.example|b.example"} == 0'
                        ),
                    }
                ],
            }
        ]
    }

    def _am(self, matcher: str) -> dict:
        return {
            "inhibit_rules": [
                {
                    "source_matchers": ['alertname="ClusterDown"'],
                    "target_matchers": ['alertname="ExternalIngressDown"', matcher],
                }
            ]
        }

    def test_a_matcher_present_in_the_expr_passes(self):
        am = self._am('instance=~"a.example|b.example"')
        assert mod.check_matcher_value_parity(am, self.RULES, ("instance",)) == []

    def test_a_matcher_that_drifted_from_the_expr_fails(self):
        """Dropping one alternation member leaves the inhibit silently wider."""
        am = self._am('instance=~"a.example"')
        (problem,) = mod.check_matcher_value_parity(am, self.RULES, ("instance",))
        assert "is not a selector the expr of ExternalIngressDown declares" in problem
        assert "a.example|b.example" in problem

    def test_a_negated_matcher_is_checked_too(self):
        am = self._am('instance!~"c.example"')
        assert mod.check_matcher_value_parity(am, self.RULES, ("instance",))

    def test_an_exact_matcher_is_not_held_to_the_expr(self):
        """`=` pins one instance; only a mirrored regex claims to reproduce a set."""
        am = self._am('instance="a.example"')
        assert mod.check_matcher_value_parity(am, self.RULES, ("instance",)) == []

    def test_the_arm_is_off_when_no_label_is_declared(self):
        am = self._am('instance=~"nowhere"')
        assert mod.check_matcher_value_parity(am, self.RULES, ()) == []

    def test_an_unlisted_label_is_not_checked(self):
        am = self._am('instance=~"nowhere"')
        assert mod.check_matcher_value_parity(am, self.RULES, ("namespace",)) == []

    def test_an_alertname_with_no_rule_is_skipped(self):
        am = {
            "inhibit_rules": [
                {
                    "source_matchers": ['alertname="ClusterDown"'],
                    "target_matchers": ['alertname="Upstream"', 'instance=~"x"'],
                }
            ]
        }
        assert mod.check_matcher_value_parity(am, self.RULES, ("instance",)) == []

    def test_whitespace_in_the_expr_does_not_hide_a_match(self):
        rules = {
            "groups": [
                {
                    "name": "ingress",
                    "rules": [
                        {
                            "alert": "ExternalIngressDown",
                            "expr": 'probe_success{instance=~"a.example|b.example"}\n  == 0',
                        }
                    ],
                }
            ]
        }
        am = self._am('instance=~"a.example|b.example"')
        assert mod.check_matcher_value_parity(am, rules, ("instance",)) == []

    def test_the_config_carries_the_label_list(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "route_cases": [{"receiver": "default", "labels": ["alertname=X"]}],
                    "matcher_parity_labels": ["instance"],
                }
            )
        )
        assert mod.load_config(path).matcher_parity_labels == ("instance",)

    def test_a_non_list_label_declaration_is_rejected(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "route_cases": [{"receiver": "default", "labels": ["alertname=X"]}],
                    "matcher_parity_labels": "instance",
                }
            )
        )
        with pytest.raises(ValueError, match="matcher_parity_labels"):
            mod.load_config(path)
