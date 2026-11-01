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


def _cfg(tmp_path, **extra):
    """A loaded config: one route case plus `extra` keys."""
    doc = {"route_cases": [{"receiver": "r", "labels": ["alertname=X"]}], **extra}
    return mod.load_config(_config(tmp_path, doc))


def _rule(source, target, equal=()) -> dict:
    return {
        "source_matchers": list(source),
        "target_matchers": list(target),
        "equal": list(equal),
    }


def _inhibit(source, target, equal=()) -> dict:
    """An Alertmanager config holding one inhibit rule."""
    return {"inhibit_rules": [_rule(source, target, equal)]}


def _rules(*alerts) -> dict:
    """A rules corpus; each alert is (name, severity) or (name, labels)."""
    return {
        "groups": [
            {
                "name": "g",
                "rules": [
                    {
                        "alert": name,
                        "expr": "x",
                        "labels": labels if isinstance(labels, dict) else {"severity": labels},
                    }
                    for name, labels in alerts
                ],
            }
        ]
    }


def _parse(*matchers):
    problems: list[str] = []
    parsed = mod._parse_matchers(list(matchers), 0, "target", problems)
    assert problems == []
    return parsed


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
        with pytest.raises(ValueError, match="one of receiver / receivers"):
            mod.load_config(_config(tmp_path, {"route_cases": [{"labels": ["alertname=X"]}]}))

    @pytest.mark.parametrize("receivers", [
        {"receiver": "a", "receivers": ["a", "b"]},
        {"receivers": []},
    ])
    def test_a_case_needs_exactly_one_receiver_key(self, tmp_path, receivers):
        case = {**receivers, "labels": ["alertname=X"]}
        with pytest.raises(ValueError, match="one of receiver / receivers"):
            mod.load_config(_config(tmp_path, {"route_cases": [case]}))

    def test_a_case_may_list_every_receiver_a_continue_route_reaches(self, tmp_path):
        case = {"receivers": ["audit", "page"], "labels": ["alertname=X"]}
        config = mod.load_config(_config(tmp_path, {"route_cases": [case]}))
        assert config.route_cases == [(("audit", "page"), ["alertname=X"])]

    def test_optional_sets_default_to_empty(self, tmp_path):
        config = _cfg(tmp_path)
        assert config.synthetic_route_alerts == set()
        assert config.upstream_alerts == set()

    @pytest.mark.parametrize("key,value", [
        ("escalation_warning_markers", "Warning"),
        ("rule_corpora", "loki"),
        ("matcher_parity_labels", "instance"),
    ])
    def test_a_list_key_given_a_scalar_is_rejected(self, tmp_path, key, value):
        with pytest.raises(ValueError, match=key):
            _cfg(tmp_path, **{key: value})

    def test_the_config_carries_the_matcher_parity_labels(self, tmp_path):
        assert _cfg(tmp_path, matcher_parity_labels=["instance"]).matcher_parity_labels == (
            "instance",
        )

    def test_a_malformed_pair_entry_is_an_operator_error(self, tmp_path):
        with pytest.raises(ValueError, match="critical \\+ warning"):
            _cfg(tmp_path, escalation_pairs=[{"warning": "A"}])


class TestRouteCaseAlertnames:
    def _config_obj(self, tmp_path, **extra):
        cases = [{"receiver": "r", "labels": ["alertname=Gone", "severity=warning"]}]
        return _cfg(tmp_path, route_cases=cases, **extra)

    def test_a_case_naming_no_rule_is_reported(self, tmp_path):
        problems = mod.check_route_case_alertnames({"Alive"}, self._config_obj(tmp_path))
        assert problems and "asserting nothing" in problems[0]

    def test_a_declared_upstream_alert_is_accepted(self, tmp_path):
        config = self._config_obj(tmp_path, upstream_alerts=["Gone"])
        assert mod.check_route_case_alertnames({"Alive"}, config) == []

    def test_a_synthetic_alert_is_skipped(self, tmp_path):
        config = self._config_obj(tmp_path, synthetic_route_alerts=["Gone"])
        assert mod.check_route_case_alertnames({"Alive"}, config) == []


@pytest.mark.parametrize("am,known,upstream,expected", [
    pytest.param({"route": {"receiver": "r"}}, set(), set(),
                 ["no inhibit_rules found in the Alertmanager config"],
                 id="no_inhibit_rules_is_reported"),
    pytest.param(_inhibit(['alertname="A"', 'namespace="ns"'],
                          ['alertname="B"', 'namespace="ns"'], ["namespace"]),
                 {"A", "B"}, set(), "dedups nothing", id="a_redundant_equal_label_is_reported"),
    pytest.param(_inhibit(['alertname="A"'], ['alertname=~"B|Typoed|C"']),
                 {"A", "B", "C"}, set(), "Typoed", id="every_alternation_member_is_checked"),
    pytest.param(_inhibit(['alertname="A"'], ['alertname=~"^Kube.*"']),
                 {"A"}, set(), "plain alternation",
                 id="a_non_alternation_regex_is_reported_not_skipped"),
    pytest.param(_inhibit(['alertname="A"'], ['severity="warning"', 'alertname!~"B|Typoed"']),
                 {"A", "B"}, set(), "exempts alertname(s) ['Typoed']",
                 id="a_negated_alertname_naming_no_rule_is_reported"),
    pytest.param(_inhibit(['alertname="A"'], ['severity="warning"', 'alertname!~"B|C"']),
                 {"A", "B", "C"}, set(), [], id="a_well_formed_negated_alertname_is_clean"),
    pytest.param(_inhibit(['alertname="A"'], ['severity="warning"', 'alertname!~"^Kube.*"']),
                 {"A"}, set(), "plain alternation",
                 id="a_non_alternation_negated_regex_is_reported_not_skipped"),
    # Every negated alertname matcher is checked, not only the last one on the label.
    pytest.param(_inhibit(['alertname="A"'],
                          ['severity="warning"', 'alertname!~"Typoed"', 'alertname!="B"']),
                 {"A", "B"}, set(), "exempts alertname(s) ['Typoed']",
                 id="a_dead_name_in_a_first_negated_matcher_is_reported"),
    pytest.param(_inhibit(['alertname="A"'], ['alertname="B"', 'alertname="C"']),
                 {"A", "B", "C"}, set(), "target alertname matchers are contradictory",
                 id="contradictory_positive_alertname_matchers_are_reported"),
    pytest.param(_inhibit(['alertname="A"', 'namespace="ns"'],
                          ['alertname="B"', 'namespace="ns"', 'namespace!~"other"'],
                          ["namespace"]),
                 {"A", "B"}, set(), "dedups nothing",
                 id="a_redundant_equal_label_is_reported_beside_a_narrowing_matcher"),
    pytest.param(_inhibit(['alertname="A"'], ['alertname="InfoInhibitor"']),
                 {"A"}, {"InfoInhibitor"}, [],
                 id="a_declared_upstream_alert_satisfies_the_matcher"),
])
def test_check_inhibits(am, known, upstream, expected):
    """`expected` is the exact problem list, or a needle one problem carries."""
    problems = mod.check_inhibits(am, known, upstream)
    if isinstance(expected, list):
        assert problems == expected
    else:
        assert any(expected in p for p in problems), problems


class TestRepeatedMatchers:
    """A label can carry several matchers and Alertmanager ANDs every one."""

    def test_a_positive_and_a_negative_alertname_are_both_read(self):
        parsed = _parse('alertname=~"A|B"', 'alertname!~"B"')
        assert mod._exact_alertnames(parsed) == (["A", "B"], None)
        assert mod._negated_alertnames(parsed) == (["B"], None)

    def test_an_unparseable_entry_is_a_problem_not_a_matcher(self):
        problems: list[str] = []
        assert mod._parse_matchers(['"alertname"="A"'], 3, "source", problems) == []
        assert problems == ["rule 3: unparseable source matcher '\"alertname\"=\"A\"'"]

    def test_a_negated_member_subtracts_from_the_covered_set(self):
        parsed = _parse('alertname=~"A|B"', 'alertname!="A"')
        assert mod._covered_alertnames(parsed) == (["B"], None)

    def test_a_negation_outside_the_positive_set_subtracts_nothing(self):
        parsed = _parse('alertname=~"A|B"', 'alertname!="C"')
        assert mod._covered_alertnames(parsed) == (["A", "B"], None)

    def test_an_unreadable_negation_leaves_the_covered_set_unknowable(self):
        parsed = _parse('alertname=~"A|B"', 'alertname!~"^Kube.*"')
        names, unreadable = mod._covered_alertnames(parsed)
        assert names == []
        assert "plain alternation" in unreadable

    def test_two_positive_alertname_matchers_intersect(self):
        parsed = _parse('alertname=~"A|B"', 'alertname=~"B|C"')
        assert mod._exact_alertnames(parsed)[0] == ["B"]

    def test_every_negated_matcher_contributes_an_exemption(self):
        parsed = _parse('alertname!~"A|B"', 'alertname!="Typoed"')
        assert mod._negated_alertnames(parsed)[0] == ["A", "B", "Typoed"]

    def test_duplicate_identical_matchers_are_harmless(self):
        parsed = _parse('alertname="A"', 'alertname="A"', 'severity="warning"')
        assert mod._exact_alertnames(parsed) == (["A"], None)
        assert mod._matches_labels({"labels": {"severity": "warning"}}, parsed) is True



DISK = _rules(("DiskUsageWarning", "warning"), ("DiskUsageWarningProlonged", "critical"))
DISK_DUAL = _rules(
    ("DiskUsageWarning", "warning"),
    ("DiskUsageWarningProlonged", "critical"),
    ("DiskUsageWarningProlonged", "warning"),
)
CPU = _rules(
    ("CPUTempWarning", {"severity": "warning", "component": "cpu"}),
    ("CPUTempCritical", {"severity": "critical", "component": "cpu"}),
)
DISK_CRIT, DISK_WARN = ['alertname="DiskUsageWarningProlonged"'], ['alertname="DiskUsageWarning"']
SEV_CRIT, SEV_WARN = ['severity="critical"'], ['severity="warning"']
NONE = {"inhibit_rules": []}
PAGES_TWICE = ("pages twice",)

# (rules, Alertmanager config, config extras, needles; none means no problem).
ESCALATION_CASES = {
    "a_pair_with_no_inhibit_rule_is_reported": (
        DISK, NONE, {}, ("pages twice", "DiskUsageWarningProlonged -> DiskUsageWarning")),
    "an_inhibited_pair_is_clean": (DISK, _inhibit(DISK_CRIT, DISK_WARN, ["instance"]), {}, ()),
    # The critical declaration alone satisfies a severity-scoped source side.
    "a_name_declared_at_two_severities_is_still_inhibited": (
        DISK_DUAL, _inhibit(SEV_CRIT, SEV_WARN, ["instance"]), {}, ()),
    # A rule excluding either side can never match it, so the pair is open.
    "a_negated_source_alertname_does_not_satisfy_the_pair": (
        DISK,
        _inhibit(['alertname=~"DiskUsageWarningProlonged|OtherCritical"',
                  'alertname!="DiskUsageWarningProlonged"'], DISK_WARN, ["instance"]),
        {}, PAGES_TWICE),
    "a_negated_target_alertname_does_not_satisfy_the_pair": (
        DISK,
        _inhibit(DISK_CRIT, ['alertname=~"DiskUsageWarning|OtherWarning"',
                             'alertname!="DiskUsageWarning"'], ["instance"]),
        {}, PAGES_TWICE),
    "a_negation_naming_another_alert_leaves_the_pair_inhibited": (
        DISK,
        _inhibit(['alertname=~"DiskUsageWarningProlonged|OtherCritical"',
                  'alertname!="OtherCritical"'], DISK_WARN, ["instance"]),
        {}, ()),
    # The names such a rule spares are unknown, so the pair is unproven.
    "an_unreadable_negation_does_not_satisfy_the_pair": (
        DISK,
        _inhibit([*DISK_CRIT, 'alertname!~"^Disk.*"'], DISK_WARN, ["instance"]),
        {}, PAGES_TWICE),
    # Inhibiting the critical with the warning silences the page, not the noise.
    "a_reversed_inhibit_rule_does_not_satisfy_the_pair": (
        DISK, _inhibit(DISK_WARN, DISK_CRIT), {}, PAGES_TWICE),
    "a_missing_shared_equal_label_is_reported": (
        DISK, _inhibit(DISK_CRIT, DISK_WARN), {"escalation_equal": ["instance"]},
        ("equal:['instance']",)),
    # Two rules each missing a different label each silence unrelated instances.
    "equal_labels_split_across_two_rules_is_reported": (
        DISK,
        {"inhibit_rules": [_rule(DISK_CRIT, DISK_WARN, ["instance"]),
                           _rule(DISK_CRIT, DISK_WARN, ["device"])]},
        {"escalation_equal": ["instance", "device"]},
        ("equal:['device']", "Rule 0 comes closest with equal:['instance']")),
    "one_rule_carrying_every_equal_label_is_clean": (
        DISK,
        {"inhibit_rules": [_rule(DISK_CRIT, DISK_WARN, ["device"]),
                           _rule(DISK_CRIT, DISK_WARN, ["instance", "device"])]},
        {"escalation_equal": ["instance", "device"]}, ()),
    "a_declared_exception_is_skipped": (
        DISK, NONE,
        {"escalation_exceptions": [
            {"critical": "DiskUsageWarningProlonged", "warning": "DiskUsageWarning"}]},
        ()),
    # A pair whose names share no stem is invisible to the suffix walk.
    "an_explicitly_declared_pair_is_checked": (
        DISK, NONE,
        {"escalation_pairs": [{"critical": "EtcdQuorumLost", "warning": "EtcdQuorumAtRisk"}]},
        ("EtcdQuorumLost -> EtcdQuorumAtRisk",)),
    "a_warning_with_no_critical_sibling_is_not_a_pair": (
        _rules(("Lonely", "warning")), NONE, {}, ()),
    "a_replacement_pair_with_no_inhibit_rule_is_reported": (
        _rules(("DiskUsageWarning", "warning"), ("DiskUsageCritical", "critical")), NONE, {},
        ("DiskUsageCritical -> DiskUsageWarning",)),
    # The pair's own labels satisfy a rule that pins no alertname at all.
    "a_severity_scoped_rule_inhibits_the_pair": (
        CPU,
        _inhibit([*SEV_CRIT, 'component=~"cpu|gpu"'], [*SEV_WARN, 'component=~"cpu|gpu"'],
                 ("instance", "component")),
        {}, ()),
    # `equal: [alertname]` pairs same-named alerts, so these two never meet.
    "an_alertname_equal_rule_does_not_inhibit_a_renamed_pair": (
        CPU, _inhibit(SEV_CRIT, SEV_WARN, ("alertname", "namespace")), {}, PAGES_TWICE),
    "a_scope_the_pair_falls_outside_does_not_inhibit_it": (
        CPU,
        _inhibit([*SEV_CRIT, 'component=~"gpu|network"'],
                 [*SEV_WARN, 'component=~"gpu|network"'], ("instance",)),
        {}, PAGES_TWICE),
    "a_scope_exempting_the_warning_does_not_inhibit_it": (
        CPU,
        _inhibit([*SEV_CRIT, 'component="cpu"'], [*SEV_WARN, 'alertname!="CPUTempWarning"'],
                 ("instance",)),
        {}, PAGES_TWICE),
    "a_scoped_rule_still_has_to_carry_every_equal_label": (
        CPU,
        _inhibit([*SEV_CRIT, 'component="cpu"'], [*SEV_WARN, 'component="cpu"'], ("instance",)),
        {"escalation_equal": ["instance", "chip"]}, ("equal:['chip']",)),
    # A namespaced pair opts out of a global key written for host series.
    "naming_a_derived_pair_replaces_its_equal_requirement": (
        CPU,
        _inhibit(['alertname="CPUTempCritical"'], ['alertname="CPUTempWarning"'], ("chip",)),
        {"escalation_equal": ["instance"],
         "escalation_pairs": [
             {"critical": "CPUTempCritical", "warning": "CPUTempWarning", "equal": ["chip"]}]},
        ()),
    # A declared pair naming upstream alerts has no labels to test a scope on.
    "a_pair_the_corpus_does_not_declare_is_not_covered_by_a_scope": (
        CPU, _inhibit(SEV_CRIT, SEV_WARN, ("instance",)),
        {"escalation_pairs": [{"critical": "KubeNodeUnreachable", "warning": "KubeNodeNotReady"}]},
        ("KubeNodeUnreachable -> KubeNodeNotReady",)),
}


@pytest.mark.parametrize(
    "rules,am,extra,needles", list(ESCALATION_CASES.values()), ids=list(ESCALATION_CASES)
)
def test_escalation_inhibits(tmp_path, rules, am, extra, needles):
    """A warning with a critical sibling and no inhibit rule pages twice."""
    problems = mod.check_escalation_inhibits(am, rules, _cfg(tmp_path, **extra))
    if not needles:
        assert problems == []
    joined = "\n".join(problems)
    for needle in needles:
        assert needle in joined, problems


def test_the_escalation_pair_is_derived_with_the_default_suffixes(tmp_path):
    assert mod.derive_escalation_pairs(DISK, _cfg(tmp_path).escalation_suffixes) == [
        ("DiskUsageWarningProlonged", "DiskUsageWarning")
    ]


# (alerts, suffixes, warning markers or None for the default, expected pairs).
REPLACEMENT_PAIRS = {
    "a_trailing_warning_is_replaced_by_the_suffix": (
        [("DiskUsageWarning", "warning"), ("DiskUsageCritical", "critical")], ["Critical"],
        None, [("DiskUsageCritical", "DiskUsageWarning")]),
    "a_trailing_low_pairs_with_the_suffix_in_front_of_the_marker": (
        [("HostMemLow", "warning"), ("HostMemCriticalLow", "critical")], ["Critical"],
        None, [("HostMemCriticalLow", "HostMemLow")]),
    "a_trailing_low_also_pairs_with_a_plain_critical": (
        [("HostMemAvailableLow", "warning"), ("HostMemAvailableCritical", "critical")],
        ["Critical"], None, [("HostMemAvailableCritical", "HostMemAvailableLow")]),
    "a_trailing_high_is_a_marker_too": (
        [("NVMeTempHigh", "warning"), ("NVMeTempCritical", "critical")], ["Critical"],
        None, [("NVMeTempCritical", "NVMeTempHigh")]),
    "the_appended_spelling_still_pairs": (
        [("ArchiveBackupFailed", "warning"), ("ArchiveBackupFailedProlonged", "critical")],
        ["Prolonged"], None, [("ArchiveBackupFailedProlonged", "ArchiveBackupFailed")]),
    "a_marker_with_no_critical_sibling_is_not_a_pair": (
        [("SATASsdTempWarning", "warning"), ("Unrelated", "critical")], ["Critical"],
        None, []),
    "no_markers_leaves_only_the_appended_spelling": (
        [("DiskUsageWarning", "warning"), ("DiskUsageCritical", "critical")], ["Critical"],
        (), []),
    "a_marker_listed_twice_yields_one_pair": (
        [("DiskUsageWarning", "warning"), ("DiskUsageCritical", "critical")], ["Critical"],
        ("Warning", "Warning"), [("DiskUsageCritical", "DiskUsageWarning")]),
    # Stripping the whole name would pair `Warning` with every critical.
    "a_name_that_is_nothing_but_the_marker_keeps_its_stem": (
        [("Warning", "warning"), ("Critical", "critical")], ["Critical"], None, []),
    "an_alert_at_both_severities_is_not_its_own_pair": (
        [("FooWarning", "warning"), ("FooWarning", "critical")], ["Warning"], None, []),
}


@pytest.mark.parametrize(
    "alerts,suffixes,markers,expected", list(REPLACEMENT_PAIRS.values()),
    ids=list(REPLACEMENT_PAIRS),
)
def test_derive_escalation_pairs(alerts, suffixes, markers, expected):
    """A critical sibling that REPLACES the warning marker is still a pair."""
    args = (_rules(*alerts), suffixes) + (() if markers is None else (markers,))
    assert mod.derive_escalation_pairs(*args) == expected


class TestRuleCorpora:
    """Rule trees the Prometheus extractor cannot see still have to resolve."""

    LOKI = textwrap.dedent(
        """\
        groups:
          - name: host-log-staleness
            interval: 1m
            rules:
              - alert: HostLogShippingStale
                expr: absent_over_time({job="journal"}[45m])
                labels: {severity: warning}
        """
    )

    def _corpus(self, tmp_path, body=None) -> Path:
        tree = tmp_path / "loki"
        tree.mkdir(exist_ok=True)
        (tree / "host-log-staleness.yaml").write_text(self.LOKI if body is None else body)
        return tree

    def test_a_directory_of_plain_groups_documents_is_read(self, tmp_path):
        self._corpus(tmp_path)
        doc = mod.load_rule_corpora(["loki"], tmp_path)
        assert mod._known_alertnames(doc) == {"HostLogShippingStale"}

    def test_a_single_file_entry_is_read(self, tmp_path):
        tree = self._corpus(tmp_path)
        doc = mod.load_rule_corpora([str(tree / "host-log-staleness.yaml")], tmp_path)
        assert mod._known_alertnames(doc) == {"HostLogShippingStale"}

    def test_a_sibling_manifest_without_groups_is_ignored(self, tmp_path):
        tree = self._corpus(tmp_path)
        (tree / "release.yaml").write_text("apiVersion: helm.toolkit.fluxcd.io/v2\nkind: HelmRelease\n")
        doc = mod.load_rule_corpora(["loki"], tmp_path)
        assert mod._known_alertnames(doc) == {"HostLogShippingStale"}

    def test_a_missing_entry_is_an_operator_error(self, tmp_path):
        with pytest.raises(ValueError, match="does not exist"):
            mod.load_rule_corpora(["loki"], tmp_path)

    def test_an_entry_declaring_no_alert_is_an_operator_error(self, tmp_path):
        """An emptied tree would otherwise leave every name it owns unknown."""
        self._corpus(tmp_path, body="groups: []\n")
        with pytest.raises(ValueError, match="declares no alerting rule"):
            mod.load_rule_corpora(["loki"], tmp_path)

    def test_a_route_case_naming_a_corpus_alert_only_resolves_once_declared(
        self, tmp_path, stub_amtool
    ):
        """Without the corpus the case names no rule, so it asserts nothing."""
        self._corpus(tmp_path)
        rules_body = tmp_path / "rules-body.yaml"
        rules_body.write_text("groups:\n- name: g\n  rules:\n  - alert: A\n    expr: x\n")
        am_body = tmp_path / "am-body.yaml"
        am_body.write_text(
            yaml.safe_dump(
                {"inhibit_rules": [{"source_matchers": ['alertname="A"'],
                                    "target_matchers": ['alertname="A"']}]}
            )
        )
        extract = tmp_path / "extract.py"
        extract.write_text(
            "import pathlib, sys\n"
            f"src = {str(rules_body)!r} if sys.argv[1] == 'rules' else {str(am_body)!r}\n"
            "pathlib.Path(sys.argv[2]).write_text(pathlib.Path(src).read_text())\n"
        )
        stub_amtool(output="discord")
        doc = {"route_cases": [{"receiver": "discord", "labels": ["alertname=HostLogShippingStale"]}]}
        without = tmp_path / "without.yaml"
        without.write_text(yaml.safe_dump(doc))
        argv = ["--repo-root", str(tmp_path), "--extract-script", str(extract)]
        assert mod.main(["--config", str(without), *argv]) == 1
        doc["rule_corpora"] = ["loki"]
        declared = tmp_path / "declared.yaml"
        declared.write_text(yaml.safe_dump(doc))
        assert mod.main(["--config", str(declared), *argv]) == 0

    def test_a_missing_entry_exits_two_from_main(self, tmp_path, capsys, stub_amtool):
        extract = tmp_path / "extract.py"
        extract.write_text("import sys; sys.exit(0)\n")
        path = tmp_path / "config.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "route_cases": [{"receiver": "r", "labels": ["alertname=X"]}],
                    "rule_corpora": ["loki"],
                }
            )
        )
        rc = mod.main(
            ["--config", str(path), "--repo-root", str(tmp_path), "--extract-script", str(extract)]
        )
        assert rc == 2
        assert "rule_corpora" in capsys.readouterr().err


class TestExtractedBodyIsParsedOnce:
    """A malformed extracted body is an operator error, not a routing finding."""

    @pytest.mark.parametrize("body,match", [
        ("route: {receiver: [oops\n", "not parseable YAML"),
        ("just a string\n", "not a YAML mapping"),
        ("", "is empty"),
    ], ids=["unparseable", "scalar", "empty"])
    def test_a_malformed_body_is_an_operator_error(self, tmp_path, body, match):
        path = tmp_path / "alertmanager.yaml"
        path.write_text(body)
        with pytest.raises(mod.ExtractionError, match=match):
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


EXTRACT_OK, EXTRACT_FAILS = "import sys; sys.exit(0)\n", "import sys; sys.exit(1)\n"


@pytest.mark.parametrize("argv,body,needle", [
    pytest.param(lambda t, x: ["--config", str(EXAMPLE), "--extract-script", str(t / "nope.py")],
                 None, "not found", id="a_missing_extract_script"),
    pytest.param(lambda t, x: ["--config", str(EXAMPLE), "--repo-root", str(t / "does-not-exist"),
                               "--extract-script", str(x)],
                 EXTRACT_OK, "is not a directory", id="a_nonexistent_repo_root"),
    pytest.param(lambda t, x: ["--config", str(EXAMPLE), "--extract-script", str(x)],
                 EXTRACT_FAILS, "extraction failed", id="a_failing_extraction"),
    pytest.param(lambda t, x: ["--config", str(t / "bad.yaml"), "--extract-script", str(x)],
                 EXTRACT_OK, "route_cases", id="a_bad_config"),
])
def test_an_operator_error_exits_two(argv, body, needle, tmp_path, capsys, stub_amtool):
    extract = tmp_path / "extract.py"
    if body is not None:
        extract.write_text(body)
    (tmp_path / "bad.yaml").write_text("route_cases: []\n")
    assert mod.main(argv(tmp_path, extract)) == 2
    assert needle in capsys.readouterr().err


class TestOperatorErrors:
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


class TestRoutes:
    """check_routes compares amtool's comma-joined receiver line exactly and
    fails loud on a broken amtool; a scripted stub covers both."""

    CONFIG = "route:\n  receiver: default\n"

    def _am(self, tmp_path) -> Path:
        path = tmp_path / "alertmanager.yaml"
        path.write_text(self.CONFIG)
        return path

    @pytest.mark.parametrize("output,expected,needle", [
        pytest.param("heartbeat", ("heartbeat",), None, id="the_resolved_receiver_matches"),
        pytest.param("default", ("heartbeat",), "heartbeat",
                     id="a_different_receiver_is_reported"),
        pytest.param("audit,critical-page", ("audit", "critical-page"), None,
                     id="a_continue_route_resolves_to_every_receiver_it_reaches"),
        pytest.param("audit,critical-page", ("critical-page",), "'audit,critical-page'",
                     id="naming_only_one_receiver_of_a_continue_route_is_reported"),
        # A prefix comparison passes exactly the misroute this gate exists to catch.
        pytest.param("critical-page", ("critical",), "'critical-page'",
                     id="a_receiver_that_merely_shares_a_prefix_is_not_a_match"),
    ])
    def test_the_receiver_line_is_compared_exactly(
        self, output, expected, needle, tmp_path, stub_amtool
    ):
        stub_amtool(output=output)
        problems = mod.check_routes(self._am(tmp_path), [(expected, ['alertname="X"'])])
        if needle is None:
            assert problems == []
        else:
            assert problems and needle in problems[0], problems

    def test_a_failing_amtool_raises_rather_than_reporting_a_misroute(
        self, tmp_path, stub_amtool
    ):
        stub_amtool(output="amtool: error: unknown flag --config.file", rc=1, on_stderr=True)
        with pytest.raises(mod.ExtractionError, match="amtool"):
            mod.check_routes(self._am(tmp_path), [(("heartbeat",), ['alertname="W"'])])

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
        assert mod.check_routes(config, [(("heartbeat",), ['alertname="Watchdog"'])]) == []
        assert mod.check_routes(config, [(("default",), ['alertname="Watchdog"'])])

    def test_a_continue_route_resolves_to_every_receiver(self, tmp_path):
        config = tmp_path / "alertmanager.yaml"
        config.write_text(
            textwrap.dedent(
                """
                route:
                  receiver: default
                  routes:
                    - receiver: audit
                      matchers: ['severity="critical"']
                      continue: true
                    - receiver: page
                      matchers: ['severity="critical"']
                receivers:
                  - name: default
                  - name: audit
                  - name: page
                """
            )
        )
        labels = ['severity="critical"']
        assert mod.check_routes(config, [(("audit", "page"), labels)]) == []
        assert mod.check_routes(config, [(("audit",), labels)])
        assert mod.check_routes(config, [(("page", "audit"), labels)])


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


def _info_inhibit(*target: str, exempt: str | None = None, equal=("namespace",)) -> dict:
    target = list(target or ['severity="info"'])
    if exempt:
        target.append('alertname!~"%s"' % exempt)
    return _inhibit(['alertname="InfoInhibitor"'], target, equal)


@pytest.mark.parametrize("inhibit,reported", [
    pytest.param(_info_inhibit(), ["NotificationsDropping"],
                 id="an_alert_dropping_the_only_equal_label_is_reported"),
    pytest.param(_info_inhibit('severity="info"', 'alertname="PodInfo"',
                               'alertname="NotificationsDropping"'), [],
                 id="a_contradictory_target_matches_nothing_to_scope"),
    # PodInfo keeps the label, so it is never reported either.
    pytest.param(_info_inhibit(exempt="NotificationsDropping"), [],
                 id="naming_it_in_the_exemption_matcher_clears_it"),
    pytest.param(_info_inhibit(equal=("alertname", "namespace")), [],
                 id="an_equal_set_naming_alertname_always_scopes"),
    pytest.param(_info_inhibit(equal=("namespace", "integration")), [],
                 id="one_surviving_equal_label_still_scopes_the_pair"),
    pytest.param(_info_inhibit('severity="warning"'), [],
                 id="a_target_matcher_the_alert_fails_is_not_examined"),
    # The spared set is unknown, so no alert is proven unconditionally muted.
    pytest.param(_info_inhibit(exempt="^Notifications.*"), [],
                 id="an_unreadable_exemption_regex_reports_nothing"),
    pytest.param(_info_inhibit('alertname="NotificationsDropping"'), [],
                 id="a_target_pinned_to_an_exact_alertname_is_a_pair_not_a_scope"),
])
def test_equal_label_scope(inhibit, reported):
    """An alert dropping every `equal:` label is muted whenever the source fires."""
    problems = mod.check_equal_label_scope(inhibit, INFO_RULES)
    assert len(problems) == len(reported), problems
    for problem, alert in zip(problems, reported):
        assert alert in problem


ENDPOINT = {"EndpointDown": [{"alert": "EndpointDown", "labels": {"severity": "warning"}}]}
DISK_BOTH = {
    "DiskUsage": [
        {"alert": "DiskUsage", "labels": {"severity": "warning"}},
        {"alert": "DiskUsage", "labels": {"severity": "critical"}},
    ]
}


@pytest.mark.parametrize("matchers,alert,rules,admits", [
    pytest.param(('alertname="Upstream"',), "Upstream", {}, True,
                 id="a_pin_alone_admits_an_undeclared_alert"),
    pytest.param(('alertname="EndpointDown"', 'severity="warning"'), "EndpointDown", ENDPOINT,
                 True, id="a_pin_with_a_severity_the_alert_carries_admits"),
    pytest.param(('alertname="EndpointDown"', 'severity="critical"'), "EndpointDown", ENDPOINT,
                 False, id="a_pin_with_a_severity_the_alert_lacks_does_not"),
    pytest.param(('alertname="Upstream"', 'component="storage"'), "Upstream", {}, False,
                 id="a_pin_with_another_matcher_needs_a_declaration"),
    pytest.param(('alertname="EndpointDown"', 'alertname="DiskUsage"'), "EndpointDown",
                 ENDPOINT, False, id="contradictory_pins_admit_no_alert-EndpointDown"),
    pytest.param(('alertname="EndpointDown"', 'alertname="DiskUsage"'), "DiskUsage",
                 ENDPOINT, False, id="contradictory_pins_admit_no_alert-DiskUsage"),
    # The critical declaration produces the instance the side pins.
    pytest.param(('alertname="DiskUsage"', 'severity="critical"'), "DiskUsage", DISK_BOTH, True,
                 id="one_of_two_declared_severities_admits"),
    pytest.param(('alertname="DiskUsage"', 'severity="info"'), "DiskUsage", DISK_BOTH, False,
                 id="a_severity_neither_declaration_carries_does_not"),
    pytest.param(('severity="critical"',), "DiskUsage", DISK_BOTH, True,
                 id="a_scope_with_no_pin_reads_the_declarations_too"),
])
def test_side_admits(matchers, alert, rules, admits):
    """A pinned side still has to satisfy its other matchers."""
    assert bool(mod._side_admits(_parse(*matchers), alert, rules)) is admits


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
ENDPOINT_PIN, ALL_HTTPS = 'alertname="EndpointDown"', 'instance=~"https://.*"'
NOT_GIT_AUTH = 'instance!~"https://(git|auth)"'


def _scope_inhibit(*target: str, source=('alertname="AggregateDown"',)) -> dict:
    return _inhibit(source, target)


@pytest.mark.parametrize("inhibit,count,needles", [
    pytest.param(_scope_inhibit(ENDPOINT_PIN, ALL_HTTPS), 1, ("AggregateDown", "no lookahead"),
                 id="a_positive_only_target_is_reported"),
    pytest.param(_scope_inhibit(ENDPOINT_PIN, ALL_HTTPS, NOT_GIT_AUTH), 0, (),
                 id="a_second_negative_matcher_clears_it"),
    # The remediation the finding asks for must be seen in either order.
    pytest.param(_scope_inhibit(ENDPOINT_PIN, NOT_GIT_AUTH, ALL_HTTPS), 0, (),
                 id="the_negative_matcher_clears_it_written_first"),
    pytest.param(_scope_inhibit(ENDPOINT_PIN, NOT_GIT_AUTH), 0, (),
                 id="a_negative_only_target_is_left_alone"),
    pytest.param(_scope_inhibit(ENDPOINT_PIN, ALL_HTTPS, ALL_HTTPS), 1, (),
                 id="a_duplicated_positive_matcher_is_reported_once"),
    pytest.param(_scope_inhibit(ENDPOINT_PIN), 0, (),
                 id="a_target_not_scoping_the_label_is_left_alone"),
    # The rule covers one source alert, so that selector bounds the target.
    pytest.param(_scope_inhibit(ENDPOINT_PIN, ALL_HTTPS,
                                source=('alertname=~"AggregateDown|Spare"',
                                        'alertname!="Spare"')),
                 1, ("AggregateDown",), id="a_negated_source_member_leaves_one_covered_alert"),
    pytest.param(_scope_inhibit(ENDPOINT_PIN, ALL_HTTPS, source=('alertname="Upstream"',)),
                 0, (), id="a_source_outside_the_corpus_is_left_alone"),
])
def test_inhibit_target_scope(inhibit, count, needles):
    """A target must not admit series the source's own selector excludes."""
    problems = mod.check_inhibit_target_scope(inhibit, SCOPE_RULES)
    assert len(problems) == count, problems
    for needle in needles:
        assert needle in problems[0]


@pytest.mark.parametrize("expr,dropped", [
    ("sum by (integration) (x)", True),
    ("sum by (namespace, pod) (x)", False),
    ("up == 0", False),
], ids=["a_by_clause_omitting_the_label_drops_it", "a_by_clause_keeping_the_label_does_not",
        "an_expression_with_no_aggregation_is_never_reported"])
def test_aggregates_away(expr, dropped):
    assert mod.aggregates_away(expr, "namespace") is dropped


def _ingress_rules(expr: str) -> dict:
    return {"groups": [{"name": "ingress", "rules": [{"alert": "ExternalIngressDown", "expr": expr}]}]}


INGRESS = _ingress_rules('probe_success{instance=~"a.example|b.example"} == 0')
INGRESS_PIN, INGRESS_BOTH = 'alertname="ExternalIngressDown"', 'instance=~"a.example|b.example"'
INGRESS_OR_OTHER = 'alertname=~"ExternalIngressDown|Other"'


@pytest.mark.parametrize("target,rules,labels,count,needles", [
    pytest.param([INGRESS_PIN, INGRESS_BOTH], INGRESS, ("instance",), 0, (),
                 id="a_matcher_present_in_the_expr_passes"),
    # Dropping one alternation member leaves the inhibit silently wider.
    pytest.param([INGRESS_PIN, 'instance=~"a.example"'], INGRESS, ("instance",), 1,
                 ("is not a selector the expr of ExternalIngressDown declares",
                  "a.example|b.example"),
                 id="a_matcher_that_drifted_from_the_expr_fails"),
    pytest.param([INGRESS_PIN, 'instance!~"c.example"'], INGRESS, ("instance",), 1, (),
                 id="a_negated_matcher_is_checked_too"),
    # `=` pins one instance; only a mirrored regex claims to reproduce a set.
    pytest.param([INGRESS_PIN, 'instance="a.example"'], INGRESS, ("instance",), 0, (),
                 id="an_exact_matcher_is_not_held_to_the_expr"),
    pytest.param([INGRESS_PIN, INGRESS_BOTH, 'instance!~"drifted"'], INGRESS, ("instance",), 1,
                 ('instance!~"drifted"',), id="a_second_regex_matcher_on_the_label_is_checked_too"),
    pytest.param([INGRESS_PIN, 'instance=~"nowhere"'], INGRESS, (), 0, (),
                 id="the_arm_is_off_when_no_label_is_declared"),
    pytest.param([INGRESS_PIN, 'instance=~"nowhere"'], INGRESS, ("namespace",), 0, (),
                 id="an_unlisted_label_is_not_checked"),
    # A rule excluding the alert cannot drift from a selector it never matches.
    pytest.param([INGRESS_OR_OTHER, 'alertname!="ExternalIngressDown"', 'instance=~"a.example"'],
                 INGRESS, ("instance",), 0, (), id="a_negated_alertname_is_not_held_to_its_expr"),
    pytest.param([INGRESS_OR_OTHER, 'instance=~"a.example"'], INGRESS, ("instance",), 1, (),
                 id="a_covered_alternation_member_is_held_to_its_expr"),
    pytest.param(['alertname="Upstream"', 'instance=~"x"'], INGRESS, ("instance",), 0, (),
                 id="an_alertname_with_no_rule_is_skipped"),
    pytest.param([INGRESS_PIN, INGRESS_BOTH],
                 _ingress_rules('probe_success{instance=~"a.example|b.example"}\n  == 0'),
                 ("instance",), 0, (), id="whitespace_in_the_expr_does_not_hide_a_match"),
])
def test_matcher_value_parity(target, rules, labels, count, needles):
    """An inhibit matcher written to mirror a rule's own selector must match it."""
    am = _inhibit(['alertname="ClusterDown"'], target)
    problems = mod.check_matcher_value_parity(am, rules, labels)
    assert len(problems) == count, problems
    for needle in needles:
        assert needle in problems[0]
