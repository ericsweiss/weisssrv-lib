"""Tests for scripts/gate_common.py.

The helpers the corpus gates share: every one of them decides a verdict, so each
case below is written as the mistake it must not make.
"""
from __future__ import annotations

import io
import ipaddress
import os
import subprocess
import sys

import pytest
from script_loader import SCRIPTS, load_script

mod = load_script("gate_common.py")


# --- load_docs ---------------------------------------------------------------


def test_plain_documents_are_returned():
    docs = mod.load_docs(io.StringIO("kind: A\n---\nkind: B\n"))
    assert [d["kind"] for d in docs] == ["A", "B"]


def test_a_kind_list_wrapper_is_flattened():
    stream = io.StringIO("kind: List\nitems:\n  - kind: A\n  - kind: B\n")
    assert [d["kind"] for d in mod.load_docs(stream)] == ["A", "B"]


def test_a_bare_top_level_list_is_flattened():
    assert [d["kind"] for d in mod.load_docs(io.StringIO("- kind: A\n- kind: B\n"))] == ["A", "B"]


def test_non_mapping_entries_are_dropped():
    assert mod.load_docs(io.StringIO("- 1\n- kind: A\n")) == [{"kind": "A"}]


def test_unparseable_input_raises_an_operator_error():
    """Never a traceback: every gate maps this to exit 2, not exit 1."""
    with pytest.raises(mod.OperatorError, match="failed to parse YAML input"):
        mod.load_docs(io.StringIO("a: [1\n"))


# --- namespaces and keys -----------------------------------------------------


def test_an_omitted_namespace_is_default():
    """The API's own defaulting. Reading it as "nowhere" would exempt a whole
    namespace from every gate."""
    assert mod.doc_namespace({"metadata": {"name": "a"}}) == "default"
    assert mod.doc_namespace({}) == "default"
    assert mod.doc_namespace({"metadata": {"namespace": "ns"}}) == "ns"


def test_doc_key_is_the_allowlist_spelling():
    assert mod.doc_key({"kind": "Deployment", "metadata": {"name": "app"}}) == (
        "default/Deployment/app"
    )
    assert mod.doc_key(
        {"kind": "Deployment", "metadata": {"name": "app", "namespace": "ns"}}
    ) == "ns/Deployment/app"


# --- policy_types ------------------------------------------------------------


def test_absent_policy_types_is_inferred_from_the_rules():
    assert mod.policy_types({}) == {"Ingress"}
    assert mod.policy_types({"egress": [{}]}) == {"Ingress", "Egress"}


def test_an_empty_policy_types_takes_the_inferred_default():
    """`policyTypes` is omitempty, so an empty list reaches the API exactly as
    an absent field does and every CNI applies the same inferred default."""
    assert mod.policy_types({"policyTypes": [], "egress": [{}]}) == {"Ingress", "Egress"}
    assert mod.policy_types({"policyTypes": []}) == {"Ingress"}


def test_a_non_list_policy_types_falls_back_to_the_inferred_default():
    assert mod.policy_types({"policyTypes": "Ingress"}) == {"Ingress"}


# --- selects_all_pods --------------------------------------------------------


@pytest.mark.parametrize(
    "selector", [None, {}, {"matchLabels": {}}, {"matchExpressions": []}]
)
def test_every_empty_spelling_selects_everything(selector):
    assert mod.selects_all_pods(selector) is True


@pytest.mark.parametrize(
    "selector",
    [
        {"matchLabels": {"app": "x"}},
        {"matchLables": {}},  # a typo leaves the recognised terms empty
        {"matchLabels": []},  # API-invalid type
        {"matchExpressions": {}},  # API-invalid type
        "not-a-mapping",
    ],
)
def test_narrowing_and_invalid_selectors_do_not_select_everything(selector):
    assert mod.selects_all_pods(selector) is False


# --- CIDR arithmetic ---------------------------------------------------------


@pytest.mark.parametrize("cidr", ["0.0.0.0/0", " 0.0.0.0/0 ", "0.0.0.0/00", "::/0", "10.0.0.0/0"])
def test_zero_prefix_is_judged_numerically(cidr):
    assert mod.zero_prefix(cidr) is True


@pytest.mark.parametrize("cidr", ["10.0.0.0/8", "0.0.0.0/1", "garbage", None, "10.0.0.0/x"])
def test_a_non_zero_prefix_is_not_wide_open(cidr):
    assert mod.zero_prefix(cidr) is False


def test_excepts_cover_a_cidr_only_when_nothing_is_left():
    assert mod.excepts_cover_cidr(["0.0.0.0/1", "128.0.0.0/1"], "0.0.0.0/0") is True
    assert mod.excepts_cover_cidr(["0.0.0.0/1"], "0.0.0.0/0") is False


def test_an_unparseable_except_never_counts_toward_coverage():
    """Guessing in its favour would certify a fence the API rejects."""
    assert mod.excepts_cover_cidr(["0.0.0.0/1", "garbage"], "0.0.0.0/0") is False


def test_an_except_that_is_not_a_strict_subnet_is_ignored():
    assert mod.excepts_cover_cidr(["0.0.0.0/0"], "0.0.0.0/0") is False
    assert mod.excepts_cover_cidr(["::/0"], "0.0.0.0/0") is False


def test_exclude_nets_passes_a_net_of_another_family_through():
    """A cut can only cover addresses of its own family."""
    net = ipaddress.ip_network("10.0.0.0/8")
    cut = ipaddress.ip_network("::/0")
    assert mod.exclude_nets([net], [cut]) == [net]


def test_exclude_nets_splits_and_empties():
    net = ipaddress.ip_network("10.0.0.0/8")
    half = ipaddress.ip_network("10.0.0.0/9")
    assert mod.exclude_nets([net], [half]) == [ipaddress.ip_network("10.128.0.0/9")]
    assert mod.exclude_nets([net], [net]) == []


# --- peer_selects_everything -------------------------------------------------


@pytest.mark.parametrize(
    "peer",
    [
        {},
        {"namespaceSelector": {}},
        {"ipBlock": {"cidr": "0.0.0.0/0"}},
        {"ipBlock": {"cidr": "0.0.0.0/0", "except": ["0.0.0.0/1"]}},
    ],
)
def test_a_wide_open_peer_is_recognised(peer):
    assert mod.peer_selects_everything(peer) is True


@pytest.mark.parametrize(
    "peer",
    [
        {"namespaceSelector": {"matchLabels": {"a": "b"}}},
        # A podSelector with no namespaceSelector is scoped to the policy's own
        # namespace, so it narrows.
        {"podSelector": {}},
        {"podSelector": {"matchLabels": {}}},
        {"ipBlock": {"cidr": "10.0.0.0/8"}},
        {"ipBlock": {"cidr": "0.0.0.0/0", "except": ["0.0.0.0/1", "128.0.0.0/1"]}},
        {"ipBlock": "not-a-mapping"},
        "not-a-mapping",
    ],
)
def test_a_narrowing_or_invalid_peer_is_not_wide_open(peer):
    assert mod.peer_selects_everything(peer) is False


# --- parse_exempt ------------------------------------------------------------


def test_exemptions_parse_into_namespace_and_reason():
    assert mod.parse_exempt(["ns=because"]) == {"ns": "because"}
    assert mod.parse_exempt([]) == {}


@pytest.mark.parametrize("raw", ["ns", "ns=", "=reason", "  =  "])
def test_an_exemption_without_a_reason_is_refused(raw):
    """An unexplained exemption is a hole."""
    with pytest.raises(mod.OperatorError, match="NS=REASON"):
        mod.parse_exempt([raw])


class TestLoadCorpus:
    """The shared preamble the corpus gates share: both arms exit 2, not 1."""

    def test_unparseable_input_exits_two(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            mod.load_corpus(io.StringIO("a: [1,\nb: {"))
        assert excinfo.value.code == 2
        assert "failed to parse YAML input" in capsys.readouterr().err

    def test_an_empty_corpus_exits_two(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            mod.load_corpus(io.StringIO(""))
        assert excinfo.value.code == 2
        assert "empty corpus" in capsys.readouterr().err

    def test_a_populated_corpus_is_returned(self):
        docs = mod.load_corpus(io.StringIO("kind: Pod\n"))
        assert docs == [{"kind": "Pod"}]


class TestLoadLiveItems:
    """A valid but empty kubectl payload must not read as a clean cluster."""

    def test_an_empty_item_list_exits_two(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            mod.load_live_items(io.StringIO('{"items": []}'), what="pods")
        assert excinfo.value.code == 2
        assert "no pods on stdin" in capsys.readouterr().err

    def test_a_payload_of_only_non_mappings_exits_two(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            mod.load_live_items(io.StringIO('{"items": [null, 7]}'), what="pods")
        assert excinfo.value.code == 2
        assert "no pods on stdin" in capsys.readouterr().err

    def test_a_non_list_payload_exits_two(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            mod.load_live_items(io.StringIO('{"items": {}}'), what="pods")
        assert excinfo.value.code == 2
        assert "is not a pods list" in capsys.readouterr().err

    def test_unparseable_json_exits_two(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            mod.load_live_items(io.StringIO("{nope"), what="pods")
        assert excinfo.value.code == 2
        assert "failed to parse" in capsys.readouterr().err

    def test_a_bare_top_level_list_is_accepted(self):
        assert mod.load_live_items(io.StringIO('[{"kind": "Pod"}]')) == [{"kind": "Pod"}]

    def test_a_populated_item_list_is_returned(self):
        payload = '{"items": [{"kind": "Pod"}]}'
        assert mod.load_live_items(io.StringIO(payload), what="pods") == [{"kind": "Pod"}]


# --- load_cluster_config / substitute ----------------------------------------


CLUSTER_CONFIG_DOC = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: cluster-config
data:
  cluster_internal_domain: internal.example
  cluster_lan_cidr: 10.9.0.0/24
  cluster_api_vip: 10.9.0.61
"""


def _write_config(root, text=CLUSTER_CONFIG_DOC, path=None):
    target = root / (path or mod.CLUSTER_CONFIG)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def test_the_cluster_config_data_map_is_returned_as_strings(tmp_path):
    _write_config(tmp_path)
    config = mod.load_cluster_config(tmp_path)
    assert config["cluster_internal_domain"] == "internal.example"
    assert config["cluster_api_vip"] == "10.9.0.61"
    assert all(isinstance(v, str) for v in config.values())


def test_a_non_string_scalar_is_coerced_rather_than_handed_on_as_a_bool(tmp_path):
    """A YAML `true`/`8080` reaching a caller untyped breaks its string compare."""
    _write_config(tmp_path, CLUSTER_CONFIG_DOC + "  cluster_scrape_port: 8080\n")
    assert mod.load_cluster_config(tmp_path)["cluster_scrape_port"] == "8080"


def test_a_missing_cluster_config_raises_rather_than_returning_an_empty_map(tmp_path):
    with pytest.raises(mod.OperatorError) as excinfo:
        mod.load_cluster_config(tmp_path)
    assert mod.CLUSTER_CONFIG in str(excinfo.value)


def test_an_empty_data_block_raises_rather_than_checking_nothing(tmp_path):
    _write_config(tmp_path, "kind: ConfigMap\ndata: {}\n")
    with pytest.raises(mod.OperatorError) as excinfo:
        mod.load_cluster_config(tmp_path)
    assert "no data keys" in str(excinfo.value)


def test_unparseable_yaml_raises_an_operator_error_not_a_yaml_error(tmp_path):
    _write_config(tmp_path, "data: {\n  broken\n")
    with pytest.raises(mod.OperatorError):
        mod.load_cluster_config(tmp_path)


def test_a_non_configmap_document_ahead_of_the_configmap_is_skipped(tmp_path):
    _write_config(tmp_path, "kind: Namespace\ndata:\n  decoy: x\n---\n" + CLUSTER_CONFIG_DOC)
    assert "decoy" not in mod.load_cluster_config(tmp_path)


def test_a_whole_value_placeholder_resolves():
    config = {"cluster_api_vip": "10.9.0.61"}
    assert mod.substitute("${cluster_api_vip}", config) == "10.9.0.61"
    assert mod.substitute("  ${cluster_api_vip}  ", config) == "10.9.0.61"


def test_an_unknown_placeholder_is_returned_untouched_to_fail_the_callers_parse():
    assert mod.substitute("${cluster_nope}", {}) == "${cluster_nope}"


def test_a_partial_placeholder_is_not_resolved():
    """Flux substitutes inside a string; a gate comparing whole values must not,
    or `api-${cluster_api_vip}` would read as a bare address."""
    config = {"cluster_api_vip": "10.9.0.61"}
    assert mod.substitute("api-${cluster_api_vip}", config) == "api-${cluster_api_vip}"


def test_a_non_string_value_is_stringified_rather_than_raising():
    assert mod.substitute(8080, {}) == "8080"
    assert mod.substitute(None, {}) == ""


class TestMissingPyYaml:
    """A job image without PyYAML is an operator error, never a policy finding."""

    @pytest.mark.parametrize("script", ["gate_common.py", "ci_yaml.py"])
    def test_an_absent_pyyaml_exits_two(self, tmp_path, script):
        (tmp_path / "yaml.py").write_text('raise ImportError("blocked")\n')
        env = {**os.environ, "PYTHONPATH": str(tmp_path)}
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / script)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert proc.returncode == 2, proc.stderr
        assert "PyYAML required" in proc.stderr
        assert "Traceback" not in proc.stderr
