"""Tests for scripts/check-netpol-except-parity.py.

CANONICAL SUITE. A consumer that vendors the script vendors this file too and
adds only its own smoke test — that its manifest corpus is clean, and that every
allowlist entry in its config still names a live policy.
"""
import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check-netpol-except-parity.py"
spec = importlib.util.spec_from_file_location("check_netpol_except_parity", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _policy(except_list):
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "allow-egress-thing"},
        "spec": {
            "podSelector": {},
            "policyTypes": ["Egress"],
            "egress": [
                {"to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": except_list}}]}
            ],
        },
    }


def _write(tmp_path, doc):
    import yaml

    path = tmp_path / "networkpolicy.yaml"
    path.write_text(yaml.safe_dump(doc))
    return path


@pytest.mark.parametrize("label", sorted(mod.CANONICAL))
def test_each_canonical_set_passes(tmp_path, label):
    path = _write(tmp_path, _policy(list(mod.CANONICAL[label])))
    assert mod.check_paths([path]) == []


def test_a_dropped_cidr_is_a_violation(tmp_path):
    shortened = mod.RESERVED_FULL[:-1]
    path = _write(tmp_path, _policy(shortened))
    violations = mod.check_paths([path])
    assert len(violations) == 1
    assert "non-canonical except-list" in violations[0]


def test_reordering_is_a_violation(tmp_path):
    reordered = list(reversed(mod.LAN_FENCE))
    path = _write(tmp_path, _policy(reordered))
    assert mod.check_paths([path])


def test_a_deleted_except_list_is_a_violation(tmp_path):
    """The edit that most directly re-opens the LAN: drop the key entirely."""
    doc = _policy(mod.LAN_FENCE)
    doc["spec"]["egress"][0]["to"][0]["ipBlock"].pop("except")
    path = _write(tmp_path, doc)
    violations = mod.check_paths([path])
    assert len(violations) == 1
    assert "no except-list" in violations[0]


def test_an_emptied_except_list_is_a_violation(tmp_path):
    path = _write(tmp_path, _policy([]))
    violations = mod.check_paths([path])
    assert len(violations) == 1
    assert "no except-list" in violations[0]


def test_a_narrow_egress_ipblock_needs_no_except(tmp_path):
    doc = _policy(mod.LAN_FENCE)
    peer = doc["spec"]["egress"][0]["to"][0]["ipBlock"]
    peer.pop("except")
    peer["cidr"] = "1.1.1.1/32"
    path = _write(tmp_path, doc)
    assert mod.check_paths([path]) == []


def test_an_unfenced_ingress_default_route_is_allowed(tmp_path):
    """wg-easy's WAN endpoint: ingress from anywhere is the intended shape."""
    doc = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "allow-wg-easy-ingress"},
        "spec": {
            "podSelector": {},
            "policyTypes": ["Ingress"],
            "ingress": [{"from": [{"ipBlock": {"cidr": "0.0.0.0/0"}}]}],
        },
    }
    path = _write(tmp_path, doc)
    assert mod.check_paths([path]) == []


def test_yml_files_are_scanned(tmp_path):
    import yaml

    (tmp_path / "netpol.yml").write_text(yaml.safe_dump(_policy(["10.0.0.0/8"])))
    assert mod.check_paths([tmp_path])


# --- The bypasses the peer-shaped arms could not see -------------------------
# All three of these were reproduced against the pre-hardening gate and printed
# "except-lists match a canonical set", rc=0.


def _egress_policy(egress_rules, name="allow-egress-thing", namespace=None):
    meta = {"name": name}
    if namespace:
        meta["namespace"] = namespace
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": meta,
        "spec": {"podSelector": {}, "policyTypes": ["Egress"], "egress": egress_rules},
    }


def test_an_empty_egress_rule_is_a_violation(tmp_path):
    """`egress: [- {}]` allows egress everywhere and leaves no ipBlock behind."""
    path = _write(tmp_path, _egress_policy([{}]))
    violations = mod.check_paths([path])
    assert len(violations) == 1
    assert "no `to:` peers" in violations[0]


def test_a_ports_only_egress_rule_is_a_violation(tmp_path):
    """Same hole, dressed as a port restriction: `to:` is still absent, so the
    rule allows :443 to every destination including the LAN."""
    doc = _egress_policy([{"ports": [{"protocol": "TCP", "port": 443}]}])
    path = _write(tmp_path, doc)
    violations = mod.check_paths([path])
    assert len(violations) == 1
    assert "no `to:` peers" in violations[0]


def test_a_zero_route_split_into_halves_is_a_violation(tmp_path):
    """0.0.0.0/1 + 128.0.0.0/1 is 0.0.0.0/0 written so no peer ends in `/0`."""
    doc = _egress_policy(
        [
            {
                "to": [
                    {"ipBlock": {"cidr": "0.0.0.0/1"}},
                    {"ipBlock": {"cidr": "128.0.0.0/1"}},
                ]
            }
        ]
    )
    path = _write(tmp_path, doc)
    violations = mod.check_paths([path])
    assert len(violations) == 1
    assert "reaches all of" in violations[0]
    assert "192.168.0.0/16" in violations[0]


def test_a_lone_half_route_is_a_violation(tmp_path):
    """The case an exact-coverage test cannot see: one `0.0.0.0/1` peer is not
    the whole address space, but every fenced range fits inside it, so the pod
    still has the LAN, Tailscale and the metadata address."""
    doc = _egress_policy([{"to": [{"ipBlock": {"cidr": "0.0.0.0/1"}}]}])
    path = _write(tmp_path, doc)
    violations = mod.check_paths([path])
    assert len(violations) == 1
    assert "10.0.0.0/8" in violations[0]
    assert "100.64.0.0/10" in violations[0]
    # ...and the fences that live in the OTHER half are not claimed to be
    # reached: this peer stops at 127.255.255.255.
    assert "192.168.0.0/16" not in violations[0]
    assert "172.16.0.0/12" not in violations[0]


def test_a_lone_block_covering_the_whole_lan_is_a_violation(tmp_path):
    """The narrowest spelling of the same escape: a single `192.168.0.0/16`
    peer never approaches a /0 and hands the pod the entire LAN."""
    doc = _egress_policy(
        [
            {
                "to": [{"ipBlock": {"cidr": "192.168.0.0/16"}}],
                "ports": [{"protocol": "TCP", "port": 22}],
            }
        ]
    )
    path = _write(tmp_path, doc)
    violations = mod.check_paths([path])
    assert len(violations) == 1
    assert "reaches all of 192.168.0.0/16" in violations[0]


def test_split_halves_carrying_the_fence_between_them_clear_the_coverage_arm():
    """The coverage arm asks what is REACHABLE, not how it is spelled: halves
    that between them fence the whole LAN reach none of it.

    (Such a policy still fails the check overall, on the older per-peer rule
    that every except-list must equal a canonical set verbatim — that strictness
    is deliberate and is what the `_a_dropped_cidr_` tests pin. This asserts the
    coverage arm alone, so the two cannot be confused for each other.)
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


def test_a_fenced_zero_route_plus_a_bare_half_is_a_violation(tmp_path):
    """The mixed shape neither arm owns alone: the /0 peer is canonically
    fenced (so the per-peer arm is happy) and the extra half re-opens the LAN
    the /0's except-list just closed."""
    doc = _egress_policy(
        [
            {
                "to": [
                    {"ipBlock": {"cidr": "0.0.0.0/0", "except": list(mod.LAN_FENCE)}},
                    {"ipBlock": {"cidr": "128.0.0.0/1"}},
                ]
            }
        ]
    )
    path = _write(tmp_path, doc)
    violations = mod.check_paths([path])
    assert len(violations) == 1
    assert "reaches all of" in violations[0]


def test_a_fenced_zero_route_is_reported_once(tmp_path):
    """The per-peer arm owns the plain shape; the coverage arm must not
    double-report the same edit."""
    doc = _egress_policy([{"to": [{"ipBlock": {"cidr": "0.0.0.0/0"}}]}])
    path = _write(tmp_path, doc)
    assert len(mod.check_paths([path])) == 1


def test_narrow_lan_egress_rules_are_untouched(tmp_path):
    """A rule allowing one LAN /32 never covers the space, so the coverage arm
    must not look at it — this is the shape most policies in the repo use."""
    doc = _egress_policy(
        [{"to": [{"ipBlock": {"cidr": "192.168.0.102/32"}}], "ports": [{"port": 2049}]}]
    )
    path = _write(tmp_path, doc)
    assert mod.check_paths([path]) == []


def test_selector_only_egress_rules_are_untouched(tmp_path):
    """In-cluster peers are not the LAN-escape shape this gate owns."""
    doc = _egress_policy(
        [{"to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "traefik"}}}]}]
    )
    path = _write(tmp_path, doc)
    assert mod.check_paths([path]) == []


def test_an_ingress_only_policy_is_not_read_as_egress(tmp_path):
    doc = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "default-deny-ingress"},
        "spec": {"podSelector": {}, "policyTypes": ["Ingress"]},
    }
    path = _write(tmp_path, doc)
    assert mod.check_paths([path]) == []


def test_omitted_policy_types_still_counts_as_egress(tmp_path):
    """The API derives policyTypes from the rules present, so a peer-less
    egress rule opens just as much without the literal declaration."""
    doc = _egress_policy([{}])
    doc["spec"].pop("policyTypes")
    path = _write(tmp_path, doc)
    assert mod.check_paths([path])


def _config(tmp_path, doc) -> Path:
    import yaml

    path = tmp_path / "netpol-except.yaml"
    path.write_text(yaml.safe_dump(doc))
    return path


def test_an_exemption_is_keyed_on_namespace_and_name(tmp_path, monkeypatch):
    """An allowlisted peer-less rule passes, and only under its own key."""
    key = "ci/job-egress"
    ns, name = key.split("/")
    monkeypatch.setattr(mod, "UNRESTRICTED_EGRESS_OK", dict(mod.UNRESTRICTED_EGRESS_OK))
    mod.load_config(_config(tmp_path, {"unrestricted_egress_ok": {key: "job pods deploy"}}))
    ok = _write(tmp_path, _egress_policy([{}], name=name, namespace=ns))
    assert mod.check_paths([ok]) == []
    other = tmp_path / "other"
    other.mkdir()
    moved = _write(other, _egress_policy([{}], name=name, namespace="downloads"))
    assert mod.check_paths([moved])


def test_the_allowlist_is_empty_without_a_config(tmp_path, monkeypatch):
    """Fail-closed: an undeclared peer-less egress rule is a violation."""
    monkeypatch.setattr(mod, "UNRESTRICTED_EGRESS_OK", {})
    path = _write(tmp_path, _egress_policy([{}], name="job-egress", namespace="ci"))
    assert mod.check_paths([path])


def test_a_reasonless_exemption_is_rejected(tmp_path):
    with pytest.raises(mod.ConfigError, match="has no reason"):
        mod.load_config(_config(tmp_path, {"unrestricted_egress_ok": {"ci/x": ""}}))


def test_a_config_replaces_the_canonical_sets(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "CANONICAL", dict(mod.CANONICAL))
    mod.load_config(_config(tmp_path, {"canonical_except_lists": {"site": ["10.0.0.0/8"]}}))
    assert mod.CANONICAL == {"site": ["10.0.0.0/8"]}
    assert mod.classify(["10.0.0.0/8"]) == "site"
    assert mod.classify(mod.RESERVED_FULL) is None


def test_a_config_replaces_the_fence_networks(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "FENCE_NETS", list(mod.FENCE_NETS))
    mod.load_config(_config(tmp_path, {"fence_networks": ["172.16.0.0/12"]}))
    assert mod.unfenced_reach([("192.168.0.0/16", [])]) == []
    assert mod.unfenced_reach([("172.16.0.0/12", [])]) == ["172.16.0.0/12"]


def test_containment_math():
    """The arm asks 'does a whole fence range fit inside what this reaches?'.

    A block INSIDE a fence range is the deliberate shape (the LAN /32s every
    NFS/DNS policy in the repo uses) and must stay silent; a block that CONTAINS
    one is the escape, whatever its prefix length.
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


def test_classify_returns_the_matching_label():
    assert mod.classify(mod.RESERVED_FULL) == "reserved-full"
    assert mod.classify(mod.LAN_FENCE) == "lan-fence"
    assert mod.classify(["10.0.0.0/8"]) is None


class TestTheGateRefusesToBeVacuous:
    """A run that inspects nothing must not report green."""

    def test_a_directory_with_no_policies_exits_two(self, tmp_path, capsys):
        (tmp_path / "notes.yaml").write_text("kind: ConfigMap\n")
        assert mod.main([str(tmp_path)]) == 2
        assert "0 NetworkPolicy manifests" in capsys.readouterr().err

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


def test_the_violation_message_names_the_config_key(tmp_path, monkeypatch):
    """It must point at `unrestricted_egress_ok` in --config, not at a module
    constant — the script is a vendored copy, so editing it is what the
    vendored-copy gate fails on."""
    monkeypatch.setattr(mod, "UNRESTRICTED_EGRESS_OK", {})
    path = _write(tmp_path, _egress_policy([{}], name="job-egress", namespace="ci"))
    violations = mod.check_paths([path])
    assert violations
    assert "unrestricted_egress_ok" in violations[0]
    assert "UNRESTRICTED_EGRESS_OK" not in violations[0]


def test_a_missing_config_file_is_an_operator_error(tmp_path, capsys):
    """Same rule as a missing scan path — reported, not a traceback."""
    assert mod.main(["--config", str(tmp_path / "gone.yaml"), str(tmp_path)]) == 2
    assert "--config" in capsys.readouterr().err


class TestAMalformedConfigIsAnOperatorError:
    """Exit 2 for every broken-config shape.

    Exit 1 is "the LAN fence drifted" and sends the reader into kubernetes/; a
    traceback is worse. Both were reachable while these arms raised
    `SystemExit(str)` (which exits 1) or nothing at all.
    """

    def _run(self, tmp_path, text, capsys):
        config = tmp_path / "netpol.yaml"
        config.write_text(text)
        rc = mod.main(["--config", str(config), str(tmp_path)])
        return rc, capsys.readouterr().err

    def test_a_non_mapping_top_level(self, tmp_path, capsys):
        rc, err = self._run(tmp_path, "- a\n- b\n", capsys)
        assert rc == 2
        assert "must be a mapping" in err

    def test_a_malformed_canonical_list(self, tmp_path, capsys):
        rc, err = self._run(tmp_path, 'canonical_except_lists: "oops"\n', capsys)
        assert rc == 2
        assert "canonical_except_lists" in err

    def test_an_unparseable_cidr_in_fence_networks(self, tmp_path, capsys):
        rc, err = self._run(tmp_path, "fence_networks: [not-a-cidr]\n", capsys)
        assert rc == 2
        assert "fence_networks" in err

    def test_a_reasonless_exemption(self, tmp_path, capsys):
        rc, err = self._run(tmp_path, "unrestricted_egress_ok:\n  ci/x: ''\n", capsys)
        assert rc == 2
        assert "has no reason" in err

    def test_unparseable_yaml(self, tmp_path, capsys):
        rc, err = self._run(tmp_path, "a: [1\n", capsys)
        assert rc == 2
        assert "--config" in err


def test_a_fence_assembled_from_smaller_peers_is_still_reached():
    """Two /17s covering a fenced /16 must not slip past a per-block subnet
    test — reachability is judged on the collapsed union."""
    reached = mod.unfenced_reach(
        [("192.168.0.0/17", []), ("192.168.128.0/17", [])]
    )
    assert "192.168.0.0/16" in reached


def test_a_deficient_configured_canonical_list_still_trips_the_fence(tmp_path, monkeypatch):
    """canonical_except_lists is site data: a configured list that omits a
    fence network satisfies the per-peer equality arm, so containment must
    run on literal /0 rules too."""
    deficient = [c for c in mod.LAN_FENCE if not c.startswith("192.168.")]
    monkeypatch.setattr(mod, "CANONICAL", {"lan-fence": deficient})
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
    violations = mod.check_paths([path])
    assert any("192.168.0.0/16" in v for v in violations), violations
