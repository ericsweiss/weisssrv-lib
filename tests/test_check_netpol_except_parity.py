"""Tests for scripts/check-netpol-except-parity.py.

Canonical suite: a consumer that vendors the script vendors this file too.
"""
import io
import json
from pathlib import Path

import pytest
import yaml
from script_loader import load_script

mod = load_script("check-netpol-except-parity.py")


def _egress_policy(egress_rules, name="allow-egress-thing", namespace=None,
                   types=("Egress",)):
    meta = {"name": name}
    if namespace:
        meta["namespace"] = namespace
    spec = {"podSelector": {}, "egress": egress_rules}
    if types is not None:
        spec["policyTypes"] = list(types)
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": meta,
        "spec": spec,
    }


def _block(cidr, except_list=None):
    block = {"cidr": cidr}
    if except_list is not None:
        block["except"] = list(except_list)
    return {"ipBlock": block}


def _policy(except_list):
    return _egress_policy([{"to": [_block("0.0.0.0/0", except_list)]}])


def _ingress_policy(name, rules):
    spec = {"podSelector": {}, "policyTypes": ["Ingress"]}
    if rules is not None:
        spec["ingress"] = rules
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": name},
        "spec": spec,
    }


def _write(tmp_path, doc):
    path = tmp_path / "networkpolicy.yaml"
    path.write_text(yaml.safe_dump(doc))
    return path


LOWER_HALF, UPPER_HALF = _block("0.0.0.0/1"), _block("128.0.0.0/1")
TRAEFIK_NS = {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "traefik"}}}

# (id, policy, violation count, needles in the first violation, needles absent).
SHAPES = [
    *(
        (f"each_canonical_set_passes-{label}", _policy(mod.CANONICAL[label]), 0, (), ())
        for label in sorted(mod.CANONICAL)
    ),
    ("a_dropped_cidr_is_a_violation", _policy(mod.RESERVED_FULL[:-1]), 1,
     ("non-canonical except-list",), ()),
    ("reordering_is_a_violation", _policy(reversed(mod.LAN_FENCE)), 1, (), ()),
    # The edit that most directly re-opens the LAN: drop the key entirely.
    ("a_deleted_except_list_is_a_violation",
     _egress_policy([{"to": [_block("0.0.0.0/0")]}]), 1, ("no except-list",), ()),
    ("an_emptied_except_list_is_a_violation", _policy([]), 1, ("no except-list",), ()),
    # `/00` is a zero-length prefix, judged numerically like `/0`.
    ("a_zero_prefix_written_as_double_zero_still_needs_a_fence",
     _egress_policy([{"to": [_block("0.0.0.0/00")]}]), 1, ("no except-list",), ()),
    ("a_narrow_egress_ipblock_needs_no_except",
     _egress_policy([{"to": [_block("1.1.1.1/32")]}]), 0, (), ()),
    ("a_narrow_egress_except_list_is_not_held_to_the_canon",
     _egress_policy([{"to": [_block("10.0.10.0/24", ["10.0.10.5/32"])]}]), 0, (), ()),
    # Ingress is exempt whatever it excludes: the lists fence egress only.
    ("an_ingress_except_list_is_not_held_to_the_canon",
     _ingress_policy("allow-wan-ingress",
                     [{"from": [_block("0.0.0.0/0", ["10.0.0.0/8"])]}]), 0, (), ()),
    # wg-easy's WAN endpoint: ingress from anywhere is the intended shape.
    ("an_unfenced_ingress_default_route_is_allowed",
     _ingress_policy("allow-wg-easy-ingress", [{"from": [_block("0.0.0.0/0")]}]), 0, (), ()),
    ("an_ingress_only_policy_is_not_read_as_egress",
     _ingress_policy("default-deny-ingress", None), 0, (), ()),
    # `egress: [- {}]` allows egress everywhere and leaves no ipBlock behind.
    ("an_empty_egress_rule_is_a_violation", _egress_policy([{}]), 1,
     ("no `to:` peers",), ()),
    ("a_ports_only_egress_rule_is_a_violation",
     _egress_policy([{"ports": [{"protocol": "TCP", "port": 443}]}]), 1,
     ("no `to:` peers",), ()),
    # The API derives policyTypes from the rules present; an empty list is omitempty.
    ("omitted_policy_types_still_counts_as_egress",
     _egress_policy([{}], types=None), 1, ("no `to:` peers",), ()),
    ("an_empty_policytypes_list_takes_the_inferred_default",
     _egress_policy([{}], types=()), 1, ("no `to:` peers",), ()),
    # 0.0.0.0/1 + 128.0.0.0/1 is 0.0.0.0/0 written so no peer ends in `/0`.
    ("a_zero_route_split_into_halves_is_a_violation",
     _egress_policy([{"to": [LOWER_HALF, UPPER_HALF]}]), 1,
     ("reaches all of", "192.168.0.0/16"), ()),
    # One /1 is not the whole space, but every fence in its half is reached,
    # and the fences in the other half are not claimed.
    ("a_lone_half_route_is_a_violation", _egress_policy([{"to": [LOWER_HALF]}]), 1,
     ("10.0.0.0/8", "100.64.0.0/10"), ("192.168.0.0/16", "172.16.0.0/12")),
    ("a_lone_block_covering_the_whole_lan_is_a_violation",
     _egress_policy([{"to": [_block("192.168.0.0/16")],
                      "ports": [{"protocol": "TCP", "port": 22}]}]), 1,
     ("reaches all of 192.168.0.0/16",), ()),
    # The canonically fenced /0 satisfies the per-peer arm; the half re-opens the LAN.
    ("a_fenced_zero_route_plus_a_bare_half_is_a_violation",
     _egress_policy([{"to": [_block("0.0.0.0/0", mod.LAN_FENCE), UPPER_HALF]}]), 1,
     ("reaches all of",), ()),
    # The per-peer arm owns the plain shape; the coverage arm must not double-report.
    ("a_fenced_zero_route_is_reported_once",
     _egress_policy([{"to": [_block("0.0.0.0/0")]}]), 1, (), ()),
    ("narrow_lan_egress_rules_are_untouched",
     _egress_policy([{"to": [_block("192.168.0.102/32")], "ports": [{"port": 2049}]}]),
     0, (), ()),
    ("selector_only_egress_rules_are_untouched",
     _egress_policy([{"to": [TRAEFIK_NS]}]), 0, (), ()),
    # A non-empty peer list carrying no constraint reads as every destination.
    ("an_empty_to_peer_allows_every_destination", _egress_policy([{"to": [{}]}]), 1,
     ("EMPTY `to:` peer", "EVERY destination"), ()),
    # `ports:` lives on the rule, so a peer spelling it carries no constraint.
    ("a_ports_only_to_peer_allows_every_destination",
     _egress_policy([{"to": [{"ports": [{"port": 443}]}]}]), 1, ("EMPTY `to:` peer",), ()),
    # `podSelector: {}` selects every pod in the namespace: a real peer.
    ("a_selector_only_to_peer_is_not_the_empty_peer",
     _egress_policy([{"to": [{"podSelector": {}}]}]), 0, (), ()),
    ("an_empty_peer_beside_a_fenced_block_is_still_reported",
     _egress_policy([{"to": [_block("0.0.0.0/0", mod.LAN_FENCE), {}]}]), 1,
     ("EMPTY `to:` peer",), ()),
]


@pytest.mark.parametrize(
    "doc,count,needles,absent", [row[1:] for row in SHAPES], ids=[row[0] for row in SHAPES]
)
def test_policy_shape(tmp_path, doc, count, needles, absent):
    violations = mod.check_paths([_write(tmp_path, doc)])
    assert len(violations) == count, violations
    for needle in needles:
        assert needle in violations[0]
    for needle in absent:
        assert needle not in violations[0]


def test_a_substitution_placeholder_cidr_is_not_evaluated(tmp_path):
    """A template spells site CIDRs as `${name}`; they are neither bad nor fenced."""
    doc = _policy(mod.LAN_FENCE)
    doc["spec"]["egress"].append(
        {"to": [{"ipBlock": {"cidr": "${cluster_lan_cidr}"}}], "ports": [{"port": 443}]}
    )
    doc["spec"]["egress"].append(
        {"to": [{"ipBlock": {"cidr": "${cluster_metallb_internal_vip}/32"}}]}
    )
    path = _write(tmp_path, doc)
    violations, scanned, errors = mod.scan_paths([path])
    assert violations == [] and errors == [] and scanned == 1


def test_yml_files_are_scanned(tmp_path):
    (tmp_path / "netpol.yml").write_text(yaml.safe_dump(_policy(["10.0.0.0/8"])))
    assert mod.check_paths([tmp_path])


def test_split_halves_carrying_the_fence_between_them_clear_the_coverage_arm():
    """The coverage arm asks what is reachable, not how it is spelled: halves
    that between them fence the whole LAN reach none of it.
    """
    lower = [c for c in mod.LAN_FENCE if c.startswith(("10.", "100."))]
    upper = [c for c in mod.LAN_FENCE if c.startswith(("172.", "192.", "169."))]
    assert sorted(lower + upper) == sorted(mod.LAN_FENCE), "the split must be total"
    assert mod.unfenced_reach([("0.0.0.0/1", lower), ("128.0.0.0/1", upper)]) == []


def test_the_coverage_arm_keeps_each_except_with_its_own_peer():
    """A NetworkPolicy `except:` narrows only the ipBlock it is written in.
    Pooling the excepts across a rule would report this leak as fenced."""
    leaky = [("0.0.0.0/1", list(mod.LAN_FENCE[:2])), ("128.0.0.0/1", [])]
    assert "192.168.0.0/16" in mod.unfenced_reach(leaky)


def _config(tmp_path, doc) -> Path:
    path = tmp_path / "netpol-except.yaml"
    path.write_text(yaml.safe_dump(doc))
    return path


def test_an_exemption_is_keyed_on_namespace_and_name(tmp_path):
    """An allowlisted peer-less rule passes, and only under its own key."""
    key = "ci/job-egress"
    ns, name = key.split("/")
    policy = mod.load_config(
        _config(tmp_path, {"unrestricted_egress_ok": {key: "job pods deploy"}})
    )
    ok = _write(tmp_path, _egress_policy([{}], name=name, namespace=ns))
    assert mod.check_paths([ok], policy) == []
    other = tmp_path / "other"
    other.mkdir()
    moved = _write(other, _egress_policy([{}], name=name, namespace="downloads"))
    assert mod.check_paths([moved], policy)


def test_a_namespaceless_policy_is_allowlisted_as_default(tmp_path):
    """An omitted namespace reads as `default`, matching every sibling gate."""
    policy = mod.load_config(
        _config(
            tmp_path,
            {"unrestricted_egress_ok": {"default/job-egress": "job pods deploy"}},
        )
    )
    ok = _write(tmp_path, _egress_policy([{}], name="job-egress"))
    assert mod.check_paths([ok], policy) == []
    other = tmp_path / "other"
    other.mkdir()
    fenced = _write(other, _egress_policy([{}], name="job-egress", namespace="ci"))
    assert mod.check_paths([fenced], policy)


def test_the_allowlist_is_empty_without_a_config(tmp_path):
    """Fail-closed: an undeclared peer-less egress rule is a violation."""
    path = _write(tmp_path, _egress_policy([{}], name="job-egress", namespace="ci"))
    assert mod.check_paths([path])


def test_a_config_replaces_the_canonical_sets(tmp_path):
    """The Policy is a value: loading one config leaves the default untouched."""
    policy = mod.load_config(
        _config(tmp_path, {"canonical_except_lists": {"site": ["10.0.0.0/8"]}})
    )
    assert policy.canonical == {"site": ["10.0.0.0/8"]}
    assert mod.classify(["10.0.0.0/8"], policy) == "site"
    assert mod.classify(mod.RESERVED_FULL, policy) is None
    assert mod.Policy().canonical == mod.CANONICAL


def test_a_config_replaces_the_fence_networks(tmp_path):
    policy = mod.load_config(_config(tmp_path, {"fence_networks": ["172.16.0.0/12"]}))
    assert mod.unfenced_reach([("192.168.0.0/16", [])], policy) == []
    assert mod.unfenced_reach([("172.16.0.0/12", [])], policy) == ["172.16.0.0/12"]


def test_containment_math():
    """A block inside a fence range stays silent; one that contains a fence
    range is reported, whatever its prefix length.
    """
    assert mod.unfenced_reach([("0.0.0.0/0", [])]) == sorted(
        str(f) for f in mod.FENCE_NETS if f.version == 4
    )
    assert mod.unfenced_reach([("192.168.0.0/16", [])]) == ["192.168.0.0/16"]
    assert mod.unfenced_reach([("192.168.0.0/15", [])]) == ["192.168.0.0/16"]
    assert mod.unfenced_reach([("192.168.0.0/24", [])]) == []
    assert mod.unfenced_reach([("192.168.0.102/32", [])]) == []
    assert mod.unfenced_reach([("1.1.1.1/32", [])]) == []
    assert mod.unfenced_reach([]) == []
    # v6 is fenced by its own analogues, and the two families never mix.
    assert mod.unfenced_reach([("::/0", [])]) == ["fc00::/7", "fe80::/10"]


class TestTheGateRefusesToBeVacuous:
    """A run that inspects nothing must not report green."""

    def test_a_directory_with_no_policies_exits_two(self, tmp_path, capsys):
        (tmp_path / "notes.yaml").write_text("kind: ConfigMap\n")
        assert mod.main([str(tmp_path)]) == 2
        assert "0 NetworkPolicy manifests" in capsys.readouterr().err

    def test_an_empty_directory_exits_two(self, tmp_path, capsys):
        """A renamed manifest subtree leaves the gate pointed at nothing."""
        assert mod.main([str(tmp_path)]) == 2
        assert "is not a gate" in capsys.readouterr().err

    def test_a_nonexistent_path_is_an_operator_error_not_a_traceback(self, tmp_path, capsys):
        assert mod.main([str(tmp_path / "gone")]) == 2
        assert "no such file or directory" in capsys.readouterr().err

    def test_a_scanned_corpus_reports_its_count(self, tmp_path, capsys):
        _write(tmp_path, _policy(list(mod.LAN_FENCE)))
        assert mod.main([str(tmp_path)]) == 0
        assert "across 1 policy/policies" in capsys.readouterr().out

    def test_an_unparseable_manifest_is_an_operator_error_not_a_drift_finding(
        self, tmp_path, capsys
    ):
        """Exit 1 means the fence drifted; a file that does not parse says
        nothing about any except-list, and the headline must not send the reader
        looking for a CIDR."""
        _write(tmp_path, _policy(list(mod.LAN_FENCE)))
        (tmp_path / "broken.yaml").write_text("egress: [oops\n")
        rc = mod.main([str(tmp_path)])
        err = capsys.readouterr().err
        assert rc == 2
        assert "unparseable YAML" in err
        assert "drifted from the canonical sets" not in err
        # PyYAML's mark names the file, not "<unicode string>".
        assert "broken.yaml" in err and "<unicode string>" not in err


def test_the_violation_message_names_the_config_key(tmp_path):
    """It must point at `unrestricted_egress_ok` in --config, not at a module
    constant — the script is a vendored copy, so editing it is what the
    vendored-copy gate fails on."""
    path = _write(tmp_path, _egress_policy([{}], name="job-egress", namespace="ci"))
    violations = mod.check_paths([path])
    assert violations
    assert "unrestricted_egress_ok" in violations[0]
    assert "UNRESTRICTED_EGRESS_OK" not in violations[0]


def test_a_missing_config_file_is_an_operator_error(tmp_path, capsys):
    """Same rule as a missing scan path — reported, not a traceback."""
    assert mod.main(["--config", str(tmp_path / "gone.yaml"), str(tmp_path)]) == 2
    assert "--config" in capsys.readouterr().err


@pytest.mark.parametrize(
    "text, needle",
    [
        ("- a\n- b\n", "must be a mapping"),
        ('canonical_except_lists: "oops"\n', "canonical_except_lists"),
        ("fence_networks: [not-a-cidr]\n", "fence_networks"),
        ("unrestricted_egress_ok:\n  ci/x: ''\n", "has no reason"),
        ("a: [1\n", "--config"),
    ],
    ids=["non-mapping", "canonical-list", "fence-cidr", "reasonless-exemption", "yaml"],
)
def test_a_malformed_config_is_an_operator_error(tmp_path, capsys, text, needle):
    """Every broken-config shape exits 2; exit 1 means the LAN fence drifted."""
    config = tmp_path / "netpol.yaml"
    config.write_text(text)
    assert mod.main(["--config", str(config), str(tmp_path)]) == 2
    assert needle in capsys.readouterr().err


def test_a_fence_assembled_from_smaller_peers_is_still_reached():
    """Two /17s covering a fenced /16 must not slip past a per-block subnet
    test — reachability is judged on the collapsed union."""
    reached = mod.unfenced_reach(
        [("192.168.0.0/17", []), ("192.168.128.0/17", [])]
    )
    assert "192.168.0.0/16" in reached


def test_a_deficient_configured_canonical_list_still_trips_the_fence(tmp_path):
    """canonical_except_lists is site data: a configured list that omits a
    fence network satisfies the per-peer equality arm, so containment must
    run on literal /0 rules too."""
    deficient = [c for c in mod.LAN_FENCE if not c.startswith("192.168.")]
    policy = mod.Policy(canonical={"lan-fence": deficient})
    doc = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "egress-deficient", "namespace": "ns"},
        "spec": {
            "podSelector": {},
            "policyTypes": ["Egress"],
            "egress": [
                {"to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": deficient}}]}
            ],
        },
    }
    path = _write(tmp_path, doc)
    violations = mod.check_paths([path], policy)
    assert any("192.168.0.0/16" in v for v in violations), violations


# --- malformed manifests are operator errors, never fence findings -----------


@pytest.mark.parametrize(
    "egress, needle",
    [
        # A `- ` with nothing after it deserializes to None.
        ("  egress:\n    -\n", "not a mapping"),
        ("  egress:\n    - to: oops\n", "not a list"),
    ],
    ids=["null-rule", "non-list-peer-key"],
)
def test_a_malformed_egress_rule_is_an_operator_error(tmp_path, capsys, egress, needle):
    """Exit 1 means the fence drifted, so a shape the API rejects exits 2 and
    names the file."""
    (tmp_path / "netpol.yaml").write_text(
        "apiVersion: networking.k8s.io/v1\n"
        "kind: NetworkPolicy\n"
        "metadata: {name: broken, namespace: ns}\n"
        "spec:\n  podSelector: {}\n  policyTypes: [Egress]\n" + egress
    )
    assert mod.main([str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "netpol.yaml" in err
    assert needle in err


def test_an_unparseable_cidr_is_an_operator_error(tmp_path, capsys):
    """A typo'd cidr contributes nothing to the reachability analysis, so it
    must be reported rather than silently dropped."""
    path = _write(tmp_path, _policy(["10.0.0/8"] + mod.LAN_FENCE[1:]))
    assert mod.main([str(path)]) == 2
    assert "unparseable CIDR" in capsys.readouterr().err


# --- the corpora the gate judges, and the peer with no constraint ------------


def test_an_unparseable_json_manifest_is_an_operator_error(tmp_path, capsys):
    (tmp_path / "netpol.json").write_text(
        '{"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",\n'
    )
    assert mod.main([str(tmp_path)]) == 2
    assert "unparseable JSON" in capsys.readouterr().err


def test_an_unparseable_json_file_named_outright_is_still_an_error(tmp_path, capsys):
    """Whatever it holds, a file the gate was pointed at is its subject."""
    path = tmp_path / "dashboard.json"
    path.write_text("{\n")
    assert mod.main([str(path)]) == 2
    assert "unparseable JSON" in capsys.readouterr().err


@pytest.mark.parametrize("wrap", [lambda doc: doc, lambda doc: [doc]], ids=["object", "list"])
def test_json_manifests_are_scanned(tmp_path, wrap):
    """A JSON manifest is a manifest, and a dashboard beside it is not one."""
    (tmp_path / "dashboard.json").write_text(
        json.dumps({"title": "Cluster", "panels": [{"type": "timeseries"}]})
    )
    (tmp_path / "netpol.json").write_text(json.dumps(wrap(_policy(["10.0.0.0/8"]))))
    violations, scanned, errors = mod.scan_paths([tmp_path])
    assert scanned == 1 and errors == [] and len(violations) == 1


def test_a_broken_dashboard_does_not_red_the_gate(tmp_path):
    """A dashboard is hand-edited; a stray comma in one must not read as the
    NetworkPolicy corpus being unreadable."""
    (tmp_path / "dashboard.json").write_text('{"title": "Cluster",,}')
    (tmp_path / "netpol.yaml").write_text(yaml.safe_dump(_policy(["10.0.0.0/8"])))
    violations, scanned, errors = mod.scan_paths([tmp_path])
    assert errors == [] and scanned == 1 and len(violations) == 1


def test_a_placeholder_tree_passes_but_its_render_is_judged(tmp_path, capsys):
    """The LAN-escape arm examines nothing while the CIDR is a placeholder; the
    rendered corpus is where the escape becomes visible."""
    doc = _egress_policy([{"to": [{"ipBlock": {"cidr": "${cluster_lan_cidr}"}}]}])
    path = _write(tmp_path, doc)
    assert mod.check_paths([path]) == []

    corpus = tmp_path / "rendered.yaml"
    corpus.write_text(yaml.safe_dump(doc).replace("${cluster_lan_cidr}", "10.0.0.0/8"))
    assert mod.main(["--corpus", str(corpus)]) == 1
    assert "reaches all of 10.0.0.0/8" in capsys.readouterr().out


def test_a_leftover_placeholder_in_a_corpus_is_an_operator_error(tmp_path, capsys):
    """In a rendered stream a `${...}` CIDR is a substitution the render missed."""
    doc = _egress_policy([{"to": [{"ipBlock": {"cidr": "${cluster_lan_cidr}"}}]}])
    corpus = tmp_path / "rendered.yaml"
    corpus.write_text(yaml.safe_dump(doc))
    assert mod.main(["--corpus", str(corpus)]) == 2
    assert "unparseable CIDR" in capsys.readouterr().err


def test_a_corpus_on_stdin_is_scanned(monkeypatch, capsys):
    doc = _egress_policy([{"to": [{"ipBlock": {"cidr": "0.0.0.0/0"}}]}])
    monkeypatch.setattr("sys.stdin", io.StringIO(yaml.safe_dump(doc)))
    assert mod.main(["--corpus", "-"]) == 1
    assert "<stdin>" in capsys.readouterr().out


def test_a_policy_less_corpus_is_an_operator_error(tmp_path, capsys):
    """A render that never reached the policies must not read as a pass."""
    corpus = tmp_path / "rendered.yaml"
    corpus.write_text("apiVersion: v1\nkind: ConfigMap\nmetadata: {name: settings}\n")
    assert mod.main(["--corpus", str(corpus)]) == 2
    assert "scanned 0 NetworkPolicy manifests" in capsys.readouterr().err


def test_a_missing_corpus_file_is_an_operator_error(tmp_path, capsys):
    assert mod.main(["--corpus", str(tmp_path / "gone.yaml")]) == 2
    assert "--corpus" in capsys.readouterr().err
