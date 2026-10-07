"""Unit tests for scripts/collect-state-lib.sh: the secret-redaction guard over
CLUSTER_STATUS.txt and the tri-state health classifiers. Each test sources the
library in a bash subprocess and drives one helper with synthetic input.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent / "scripts" / "collect-state-lib.sh"


def _run(func_call: str, stdin: str = "") -> subprocess.CompletedProcess:
    """Source the library and run a function call, returning the completed proc."""
    script = f". {LIB}\n{func_call}\n"
    return subprocess.run(
        ["bash", "-c", script],
        input=stdin,
        capture_output=True,
        text=True,
    )


def _redact(text: str, tmp_path: Path) -> str:
    """Run redact_file over `text` and return the redacted output."""
    infile = tmp_path / "in.txt"
    outfile = tmp_path / "out.txt"
    infile.write_text(text)
    res = _run(f"redact_file {infile} {outfile}")
    assert res.returncode == 0, res.stderr
    return outfile.read_text()


# redact_file: secrets must come out redacted

class TestRedactSecrets:
    @pytest.mark.parametrize(
        "line,leak",
        [
            ("password: hunter2", "hunter2"),
            ("Password= Sup3rSecret!", "Sup3rSecret"),
            ("token: glpat-abc123def456", "glpat-abc123def456"),
            ("access_token: ya29.a0Af", "ya29.a0Af"),
            ("secret: s3cr3tvalue", "s3cr3tvalue"),
            ("client_secret: oidc-secret-value", "oidc-secret-value"),
            ("api_key: 32aa4d3c9ff04a1", "32aa4d3c9ff04a1"),
            ("apikey=32aa4d3c9ff04a1", "32aa4d3c9ff04a1"),
            ("API_KEY: 32AA4D3C9FF04A1", "32AA4D3C9FF04A1"),
            ("Authorization: Bearer eyFOOBARtoken123", "eyFOOBARtoken123"),
            ("Authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA"),
            ("CF_Token=cf-token-value", "cf-token-value"),
            ("OPENVPN_PASSWORD=vpnpass", "vpnpass"),
            ("WIREGUARD_PRIVATE_KEY=wOOfoo42=", "wOOfoo42"),
            ("WIREGUARD_PRESHARED_KEY=pskvalue42=", "pskvalue42"),
            ("runner token glrt-AbC_123-xyz", "glrt-AbC_123-xyz"),
            ("pat glpat-" + "aB1" * 8, "glpat-" + "aB1" * 8),
            ("deploy gldt-" + "cD2" * 8, "gldt-" + "cD2" * 8),
            ("build glcbt-" + "eF3" * 8, "glcbt-" + "eF3" * 8),
            ("openai sk-" + "a1B2" * 8, "sk-" + "a1B2" * 8),
            ("anthropic sk-ant-" + "a1B2" * 8, "sk-ant-" + "a1B2" * 8),
            ("openai sk-proj-" + "a1B2" * 8, "sk-proj-" + "a1B2" * 8),
            ("aws AKIA" + "A1B2C3D4E5F6G7H8"[:16], "AKIA" + "A1B2C3D4E5F6G7H8"[:16]),
            ("b2_application_key=fakekey_K005LongB2Value", "fakekey_K005LongB2Value"),
            ("b2_key_id: 0051a2b3c4d5e6f0000000001", "0051a2b3c4d5e6f0000000001"),
            ("gh token ghp_" + "a1" * 20, "ghp_" + "a1" * 20),
            ("op sa ops_" + "b2" * 25, "ops_" + "b2" * 25),
            (
                "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig-part_here",
                "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig-part_here",
            ),
            (
                "hook https://discord.com/api/webhooks/1234567890/secret_hook-token",
                "secret_hook-token",
            ),
        ],
    )
    def test_secret_shapes_redacted(self, tmp_path, line, leak):
        out = _redact(line + "\n", tmp_path)
        assert leak not in out, f"leaked through redaction: {out!r}"

    def test_pem_private_key_block_collapsed(self, tmp_path):
        pem = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEpAIBAAKCAQEA7the8keymaterial\n"
            "morekeymaterial==\n"
            "-----END RSA PRIVATE KEY-----\n"
        )
        out = _redact(pem, tmp_path)
        assert "keymaterial" not in out
        assert "<PRIVATE_KEY_REDACTED>" in out


# redact_file: benign text must come out untouched

class TestRedactBenign:
    @pytest.mark.parametrize(
        "line",
        [
            "K3s nodes ready: 9/9",
            "Basic configuration options are documented in docs/01",
            "tokens: 3 loaded identities",
            "unit tokenizer.service loaded active",
            "eyeball the output before shipping",
            "the secretary approved the change",
        ],
    )
    def test_benign_lines_untouched(self, tmp_path, line):
        out = _redact(line + "\n", tmp_path)
        assert out == line + "\n"


# classifiers. The helpers below name each positional argument of
# classify_regular and classify_json.

def _regular(pve, api, ready, total, hosts_ok, hosts_total, pct, floor, flux, zfs, sections_ok=1, sections_total=1, alerts=0) -> str:
    res = _run(
        f"classify_regular {pve} {api} {ready} {total} {hosts_ok} "
        f"{hosts_total} {pct} {floor} {flux} {zfs} "
        f"{sections_ok} {sections_total} {alerts}"
    )
    assert res.returncode == 0, res.stderr
    return res.stdout.strip()


def _json(pve_up, pve_total, api, ready, total, flux, zfs, alerts=0) -> str:
    res = _run(
        f"classify_json {pve_up} {pve_total} {api} {ready} {total} "
        f"{flux} {zfs} {alerts}"
    )
    assert res.returncode == 0, res.stderr
    return res.stdout.strip()


class TestClassifyRegular:
    def test_all_green_is_ok(self):
        assert _regular(6, "true", 9, 9, 19, 19, 100, 50, 0, 0) == "OK"

    def test_flux_not_ready_degrades(self):
        assert _regular(6, "true", 9, 9, 19, 19, 100, 50, 1, 0) == "PARTIAL"

    def test_zfs_degraded_degrades(self):
        assert _regular(6, "true", 9, 9, 19, 19, 100, 50, 0, 1) == "PARTIAL"

    def test_one_host_down_degrades(self):
        assert _regular(5, "true", 9, 9, 18, 19, 94, 50, 0, 0) == "PARTIAL"

    def test_no_pve_host_is_failed(self):
        assert _regular(0, "true", 9, 9, 3, 19, 15, 50, 0, 0) == "FAILED"

    def test_api_ok_zero_ready_is_failed(self):
        # API answered and reported zero Ready nodes: catastrophic.
        assert _regular(6, "true", 0, 9, 19, 19, 100, 50, 0, 0) == "FAILED"

    def test_kubectl_unreachable_is_partial_not_failed(self):
        # Collector-side kubeconfig problem (probe defaults 0/0) must degrade,
        # not read as a catastrophic cluster failure — and never promote to OK.
        assert _regular(6, "false", 0, 0, 19, 19, 100, 50, 0, 0) == "PARTIAL"

    def test_coverage_below_floor_is_failed(self):
        assert _regular(6, "true", 9, 9, 9, 19, 47, 50, 0, 0) == "FAILED"

    def test_some_guests_down_degrades_not_failed(self):
        # Guests count toward HOSTS_TOTAL; 3 of 22 unreachable stays above the
        # coverage floor with core infra up, so the verdict is PARTIAL.
        assert _regular(6, "true", 9, 9, 19, 22, 86, 50, 0, 0) == "PARTIAL"

    def test_failed_specialised_section_degrades(self):
        # Every host SSH succeeded but one specialised collector (Proxmox /
        # DNS / mail / k3s) did not: the artifact is missing a whole block
        # (ZFS health, firewall, HA state) and must not read OK.
        assert _regular(6, "true", 9, 9, 19, 19, 100, 50, 0, 0, sections_ok=21, sections_total=22) == "PARTIAL"

    def test_all_sections_collected_stays_ok(self):
        assert _regular(6, "true", 9, 9, 19, 19, 100, 50, 0, 0, sections_ok=22, sections_total=22) == "OK"

    def test_firing_alert_degrades(self):
        # A firing non-Watchdog alert must degrade the verdict.
        assert _regular(6, "true", 9, 9, 19, 19, 100, 50, 0, 0, alerts=1) == "PARTIAL"

    def test_firing_alerts_never_cause_failed(self):
        # Alert noise degrades but must never suppress the artifact.
        assert _regular(6, "true", 9, 9, 19, 19, 100, 50, 0, 0, alerts=99) == "PARTIAL"

    def test_unknown_alerts_demote(self):
        # collect-state.sh coalesces an unaskable Alertmanager to 1, not 0, so
        # "could not ask" arrives here as the degrading value.
        assert _regular(6, "true", 9, 9, 19, 19, 100, 50, 0, 0, alerts=1) == "PARTIAL"
        assert _regular(6, "true", 9, 9, 19, 19, 100, 50, 0, 0, alerts=0) == "OK"

    def test_suspended_flux_counts_as_not_ready(self):
        # probe_flux_not_ready folds spec.suspend into its count, so a frozen
        # cluster arrives here as flux_not_ready>0 and degrades.
        assert _regular(6, "true", 9, 9, 19, 19, 100, 50, 1, 0) == "PARTIAL"


class TestClassifyJson:
    def test_all_green_is_healthy(self):
        assert _json(6, 6, "true", 9, 9, 0, 0) == "healthy"

    def test_flux_not_ready_degrades(self):
        assert _json(6, 6, "true", 9, 9, 1, 0) == "degraded"

    def test_zfs_degraded_degrades(self):
        assert _json(6, 6, "true", 9, 9, 0, 1) == "degraded"

    def test_one_host_down_degrades(self):
        assert _json(5, 6, "true", 9, 9, 0, 0) == "degraded"

    def test_no_pve_host_is_catastrophic(self):
        assert _json(0, 6, "true", 9, 9, 0, 0) == "catastrophic"

    def test_api_ok_zero_ready_is_catastrophic(self):
        assert _json(6, 6, "true", 0, 9, 0, 0) == "catastrophic"

    def test_kubectl_unreachable_is_degraded(self):
        # API unreachable (collector-side): degraded, not catastrophic.
        assert _json(6, 6, "false", 0, 0, 0, 0) == "degraded"

    def test_firing_alert_degrades(self):
        # The signal regular mode already gates on: --json must not answer
        # healthy while a non-Watchdog alert fires.
        assert _json(6, 6, "true", 9, 9, 0, 0, alerts=1) == "degraded"

    def test_firing_alerts_never_catastrophic(self):
        # Alert noise degrades; it never claims the cluster is down.
        assert _json(6, 6, "true", 9, 9, 0, 0, alerts=99) == "degraded"

    def test_unknown_alerts_demote(self):
        # collect-state.sh coalesces an unaskable Alertmanager to 1, not 0, so
        # "could not ask" arrives here as the degrading value.
        assert _json(6, 6, "true", 9, 9, 0, 0, alerts=1) == "degraded"
        assert _json(6, 6, "true", 9, 9, 0, 0, alerts=0) == "healthy"

    @pytest.mark.parametrize("position", ["flux", "zfs"])
    def test_an_unaskable_probe_coerced_through_coerce_int_never_reads_healthy(
        self, position
    ):
        """The contract between a probe and this verdict: unknown is not zero.

        A probe that could not answer reaches the classifier as 1, so a cluster
        nobody could inspect cannot report healthy.
        """
        flux = 'flux_arg="$(coerce_int unknown 1)"' if position == "flux" else 'flux_arg=0'
        zfs = 'zfs_arg="$(coerce_int unknown 1)"' if position == "zfs" else 'zfs_arg=0'
        res = _run(
            f'{flux}; {zfs}; classify_json 6 6 true 9 9 "$flux_arg" "$zfs_arg" 0'
        )
        assert res.returncode == 0, res.stderr
        assert res.stdout.strip() == "degraded"


class TestClassifierParity:
    """Regular and --json verdicts must agree on shared signals: OK <=> healthy,
    PARTIAL <=> degraded, FAILED <=> catastrophic (with full host coverage, the
    regular-only difference documented in the collect-state.sh header)."""

    PARITY = {"OK": "healthy", "PARTIAL": "degraded", "FAILED": "catastrophic"}

    @pytest.mark.parametrize(
        "pve,api,ready,total,flux,zfs,alerts",
        [
            (6, "true", 9, 9, 0, 0, 0),   # green
            (6, "true", 9, 9, 1, 0, 0),   # flux stuck
            (6, "true", 9, 9, 0, 2, 0),   # zfs degraded
            (5, "true", 9, 9, 0, 0, 0),   # one pve host down
            (6, "true", 8, 9, 0, 0, 0),   # one k3s node not ready
            (6, "false", 0, 0, 0, 0, 0),  # kubectl unreachable
            (0, "true", 0, 0, 0, 0, 0),   # nothing reachable
            (6, "true", 0, 9, 0, 0, 0),   # api ok, zero nodes ready
            (6, "true", 9, 9, 0, 0, 3),   # alerts firing
        ],
    )
    def test_same_signals_map_to_paired_verdicts(self, pve, api, ready, total,
                                                 flux, zfs, alerts):
        # Full host coverage so the regular-only coverage gate is neutral;
        # hosts_ok tracks pve reachability for the one-host-down case.
        hosts_total = 19
        hosts_ok = hosts_total if pve == 6 else (0 if pve == 0 else 18)
        pct = hosts_ok * 100 // hosts_total
        reg = _regular(pve, api, ready, total, hosts_ok, hosts_total, pct, 50, flux, zfs, alerts=alerts)
        js = _json(pve, 6, api, ready, total, flux, zfs, alerts=alerts)
        assert self.PARITY[reg] == js, (
            f"verdict mismatch: regular={reg} json={js} for "
            f"pve={pve} api={api} ready={ready}/{total} flux={flux} "
            f"zfs={zfs} alerts={alerts}"
        )


# regular_failing_predicates: the PARTIAL/FAILED verdict must name its cause
# Same 13 args as classify_regular; empty output means every OK predicate holds.

def _failing(pve, api, ready, total, hosts_ok, hosts_total, pct, floor, flux, zfs, sections_ok=1, sections_total=1, alerts=0) -> str:
    res = _run(
        f"regular_failing_predicates {pve} {api} {ready} {total} {hosts_ok} "
        f"{hosts_total} {pct} {floor} {flux} {zfs} "
        f"{sections_ok} {sections_total} {alerts}"
    )
    assert res.returncode == 0, res.stderr
    return res.stdout.strip()


class TestRegularFailingPredicates:
    ALL_GREEN = dict(pve=6, api="true", ready=9, total=9, hosts_ok=19,
                     hosts_total=19, pct=100, floor=50, flux=0, zfs=0,
                     sections_ok=5, sections_total=5, alerts=0)

    def test_all_green_names_nothing(self):
        assert _failing(**self.ALL_GREEN) == ""

    @pytest.mark.parametrize(
        "override,expected",
        [
            ({"alerts": 4}, "alerts_firing=4"),
            ({"flux": 2}, "flux_not_ready=2"),
            ({"zfs": 1}, "zfs_degraded=1"),
            ({"hosts_ok": 18}, "hosts=18/19"),
            ({"sections_ok": 3}, "sections=3/5"),
            ({"ready": 8}, "k3s_nodes=8/9"),
            ({"api": "false"}, "k3s_api=unreachable"),
            ({"pve": 0}, "pve_reachable=0"),
            ({"pct": 15}, "coverage=15%<50%"),
        ],
    )
    def test_each_signal_names_itself(self, override, expected):
        args = {**self.ALL_GREEN, **override}
        assert _failing(**args) == expected

    def test_every_failing_signal_is_listed(self):
        out = _failing(pve=0, api="false", ready=0, total=0, hosts_ok=3, hosts_total=19, pct=15, floor=50, flux=2, zfs=1, sections_ok=3, sections_total=5, alerts=4)
        for token in ("pve_reachable=0", "coverage=15%<50%", "hosts=3/19",
                      "sections=3/5", "k3s_api=unreachable", "k3s_nodes=0/0",
                      "flux_not_ready=2", "zfs_degraded=1",
                      "alerts_firing=4"):
            assert token in out, f"{token} missing from {out!r}"

    @pytest.mark.parametrize(
        "override",
        [
            {},
            {"alerts": 4},
            {"flux": 2},
            {"zfs": 1},
            {"hosts_ok": 18},
            {"sections_ok": 3},
            {"ready": 8},
            {"api": "false"},
            {"pve": 0},
            {"pct": 15},
        ],
    )
    def test_agrees_with_classify_regular(self, override):
        # classify_regular's OK arm IS this function, so empty output must mean
        # OK and non-empty must mean not-OK on every signal it names.
        args = {**self.ALL_GREEN, **override}
        verdict = _regular(**args)
        failing = _failing(**args)
        assert (verdict == "OK") == (failing == ""), (verdict, failing)


# compose_active_sections echoes the comma-joined optional sections that
# render. "-" drops a section and `metrics` is nested under `backup`.

def _sections(health_url, nginx_cert, backup_timer, backup_prom) -> str:
    res = _run(
        f"compose_active_sections '{health_url}' '{nginx_cert}' "
        f"'{backup_timer}' '{backup_prom}'"
    )
    assert res.returncode == 0, res.stderr
    return res.stdout.strip()


class TestComposeActiveSections:
    def test_full_app_renders_every_section(self):
        assert _sections(
            "http://x/health", "/etc/ssl/x/fullchain.pem", "x-backup.timer",
            "/var/lib/node_exporter/x.prom",
        ) == "health,nginx,backup,metrics"

    def test_all_skip_renders_no_optional_sections(self):
        # Every optional sentinel "-": only the always-on compose block renders.
        assert _sections("-", "-", "-", "-") == ""

    def test_health_only(self):
        # An internal service with a health endpoint and nothing else.
        assert _sections("http://127.0.0.1:3003/ping", "-", "-", "-") == "health"

    def test_backup_without_metrics(self):
        assert _sections("-", "-", "x-backup.timer", "-") == "backup"

    def test_metrics_is_nested_under_backup(self):
        # backup_prom set but backup_timer "-": metrics must not render alone.
        assert _sections("-", "-", "-", "/var/lib/node_exporter/x.prom") == ""

    def test_section_order_is_stable(self):
        assert _sections("-", "/etc/ssl/x.pem", "x.timer", "/v/x.prom") == (
            "nginx,backup,metrics"
        )


# firewall_guest_fw_list: per-guest .fw enumeration
# Reads candidate *.fw paths on stdin, drops cluster.fw, emits the rest sorted.

def _fw_list(stdin: str) -> list[str]:
    res = _run("firewall_guest_fw_list", stdin=stdin)
    return res.stdout.splitlines()


class TestFirewallGuestFwList:
    def test_excludes_cluster_fw(self):
        out = _fw_list(
            "/etc/pve/firewall/cluster.fw\n"
            "/etc/pve/firewall/100.fw\n"
            "/etc/pve/firewall/156.fw\n"
        )
        assert "/etc/pve/firewall/cluster.fw" not in out
        assert out == ["/etc/pve/firewall/100.fw", "/etc/pve/firewall/156.fw"]

    def test_output_is_sorted(self):
        out = _fw_list(
            "/etc/pve/firewall/222.fw\n"
            "/etc/pve/firewall/100.fw\n"
            "/etc/pve/firewall/156.fw\n"
        )
        assert out == sorted(out)
        assert out == [
            "/etc/pve/firewall/100.fw",
            "/etc/pve/firewall/156.fw",
            "/etc/pve/firewall/222.fw",
        ]

    def test_only_cluster_fw_yields_nothing(self):
        assert _fw_list("/etc/pve/firewall/cluster.fw\n") == []

    def test_empty_input_yields_nothing(self):
        assert _fw_list("") == []


# cs_capped / cs_emit: remote section emitters
# They fall back on empty input and mark a capped section, which
# `producer | head -N || echo msg` cannot do (see TestNoDeadPipelineFallbacks).

def _capped(cap: int, fallback: str, stdin: str) -> list[str]:
    res = _run(f"cs_capped {cap} '{fallback}'", stdin=stdin)
    assert res.returncode == 0, res.stderr
    return res.stdout.splitlines()


def _emit(fallback: str, stdin: str) -> list[str]:
    res = _run(f"cs_emit '{fallback}'", stdin=stdin)
    assert res.returncode == 0, res.stderr
    return res.stdout.splitlines()


class TestCsEmit:
    def test_empty_producer_prints_fallback(self):
        # A failed producer must render the fallback, not an empty section.
        assert _emit("none", "") == ["none"]

    def test_output_passes_through_unchanged(self):
        assert _emit("none", "a\nb\nc\n") == ["a", "b", "c"]

    def test_uncapped_keeps_every_line(self):
        big = "".join(f"row{i}\n" for i in range(500))
        assert len(_emit("No ZFS", big)) == 500

    def test_unterminated_final_line_is_kept(self):
        # kubectl -o jsonpath emits no trailing newline.
        assert _emit("none", "only-line") == ["only-line"]

    def test_blank_line_is_output_not_absence(self):
        assert _emit("none", "\n") == [""]


class TestCsCapped:
    def test_empty_producer_prints_fallback(self):
        assert _capped(30, "none", "") == ["none"]

    def test_under_cap_has_no_truncation_marker(self):
        out = _capped(5, "none", "a\nb\n")
        assert out == ["a", "b"]

    def test_exactly_at_cap_has_no_marker(self):
        out = _capped(3, "none", "a\nb\nc\n")
        assert out == ["a", "b", "c"]

    def test_over_cap_truncates_and_says_so(self):
        out = _capped(3, "none", "a\nb\nc\nd\ne\n")
        assert out[:3] == ["a", "b", "c"]
        assert len(out) == 4
        assert "truncated" in out[3]
        # The marker must report the real total, not just the cap — a reader
        # needs to know how much is missing.
        assert "3 of 5" in out[3]

    def test_cap_zero_means_uncapped(self):
        out = _capped(0, "none", "a\nb\nc\nd\n")
        assert out == ["a", "b", "c", "d"]


# warning_events_filter: counts Warning events at or after the cutoff from stdin.
# The count is advisory and excludes nothing; exclusions go in WARNING_EVENTS_JQ.

CUTOFF = "2026-01-01T00:00:00Z"

SCHEDULING_FAILURE = (
    "0/9 nodes are available: 3 Insufficient cpu, 6 node(s) had untolerated taint"
)


def _event(reason="BackOff", namespace="default", message="",
           ts="2026-01-01T12:00:00Z", key="lastTimestamp") -> dict:
    return {
        "reason": reason,
        "message": message,
        "metadata": {"namespace": namespace},
        key: ts,
    }


def _warning_events(items, cutoff=CUTOFF) -> str:
    res = _run(f"warning_events_filter '{cutoff}'",
               stdin=json.dumps({"items": items}))
    assert res.returncode == 0, res.stderr
    return res.stdout.strip()


def require_jq() -> None:
    """Fail rather than skip: a silent skip is how this filter loses its only
    coverage in a job that stopped installing jq."""
    assert shutil.which("jq"), (
        "jq is not installed, so warning_events_filter is unverified. "
        "Install jq in the job that runs this suite."
    )


class TestWarningEventsFilter:
    @pytest.fixture(autouse=True)
    def _jq_present(self):
        require_jq()

    def test_scheduling_failure_counts(self):
        assert _warning_events([
            _event("FailedScheduling", "apps", SCHEDULING_FAILURE),
        ]) == "1"

    def test_every_warning_in_window_counts(self):
        assert _warning_events([
            _event("FailedScheduling", "apps", SCHEDULING_FAILURE),
            _event("BackOff", "apps", "Back-off restarting container"),
        ]) == "2"

    def test_event_time_only_inside_the_cutoff_counts(self):
        # Events-API events carry eventTime with lastTimestamp absent.
        assert _warning_events([
            _event("BackOff", "default", "", key="eventTime"),
        ]) == "1"

    def test_event_before_the_cutoff_is_dropped(self):
        assert _warning_events([
            _event("BackOff", "default", "", ts="2025-12-31T23:00:00Z"),
        ]) == "0"

    def test_unparseable_input_reads_as_unknown(self):
        # A failed kubectl must never render as "no warnings".
        res = _run(f"warning_events_filter '{CUTOFF}'", stdin="not json")
        assert res.stdout.strip() == "unknown"


# coerce_int: a non-numeric probe result must take the caller's fallback.


class TestCoerceInt:
    @pytest.mark.parametrize(
        "value,fallback,expected",
        [
            ("0", "1", "0"),
            ("3", "1", "3"),
            ("0", "null", "0"),
            ("3", "null", "3"),
            ("", "1", "1"),
            ("unknown", "1", "1"),
            ("", "null", "null"),
            ("unknown", "null", "null"),
            ("1.5", "1", "1"),
        ],
    )
    def test_fallback_replaces_a_non_numeric_result(self, value, fallback, expected):
        res = _run(f"coerce_int {shlex.quote(value)} {fallback}")
        assert res.returncode == 0, res.stderr
        assert res.stdout == expected

    def test_an_unknown_count_never_reads_as_zero(self):
        # A false 0 is what turns an unaskable query into a clean verdict.
        assert _run("coerce_int unknown 1").stdout != "0"


