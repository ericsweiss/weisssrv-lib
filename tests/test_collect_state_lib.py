"""Unit tests for scripts/collect-state-lib.sh: the secret-redaction guard, the
tri-state health classifiers, the alert and event policy and the section helpers.
Each test sources the library in bash and drives one helper with synthetic input.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent / "scripts" / "collect-state-lib.sh"


def _run(
    func_call: str, stdin: str = "", env: dict | None = None
) -> subprocess.CompletedProcess:
    """Source the library and run a function call, returning the completed proc.

    An `env` replaces the environment, so a PATH of stubs shadows the real tools.
    """
    return subprocess.run(
        ["bash", "-c", f". {LIB}\n{func_call}\n"],
        input=stdin, capture_output=True, text=True, env=env,
    )


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


def _redact(text: str, tmp_path: Path) -> str:
    """Run redact_file over `text` and return the redacted output."""
    infile = tmp_path / "in.txt"
    outfile = tmp_path / "out.txt"
    infile.write_text(text)
    res = _run(f"redact_file {infile} {outfile}")
    assert res.returncode == 0, res.stderr
    return outfile.read_text()


# redact_file: secrets must come out redacted

# Every key a REDACT_PATTERNS entry names, in the spelling a config or API prints.
KEYED = (
    "password", "token", "access_token", "refresh_token", "id_token", "secret",
    "api_key", "apiKey", "application_key", "b2_application_key", "key_id",
    "b2_key_id", "openvpn-user", "openvpn-password", "api-token", "oidc_client_id",
    "oidc_client_secret", "client_id", "client_secret",
)


def _sed(pattern: str, text: str) -> str:
    """Apply one redaction pattern alone, as redact_file applies each in turn."""
    res = subprocess.run(["sed", "-E", "-e", pattern], input=text, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return res.stdout

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
            ("aws AKIA" + "A1B2C3D4E5F6G7H8", "AKIA" + "A1B2C3D4E5F6G7H8"),
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
            ('"token":"abc123def"', "abc123def"),
            ('password: "hunter two"', "two"),
            ("password: 'correct horse battery'", "battery"),
            ('{"password":"ab\\"cd zq9ef"}', "zq9ef"),
        ],
    )
    def test_secret_shapes_redacted(self, tmp_path, line, leak):
        out = _redact(line + "\n", tmp_path)
        assert leak not in out, f"leaked through redaction: {out!r}"

    def test_every_key_pattern_redacts_json_keys_and_quoted_values_on_its_own(self):
        """Another pattern catching the same line must not mask a dead arm."""
        plain = "".join(f"{key}: zq7leak\n" for key in KEYED)
        forms = {
            "JSON-quoted key": "".join(f'{{"{key}": "zq7leak"}}\n' for key in KEYED),
            "quoted value": "".join(f'{key}: "two zq7leak"\n' for key in KEYED),
            "single-quoted value": "".join(f"{key}: 'two zq7leak'\n" for key in KEYED),
            "escaped quote": "".join(f'{{"{key}": "a\\" zq7leak"}}\n' for key in KEYED),
        }
        res = _run('printf "%s\\n" "${REDACT_PATTERNS[@]}"')
        assert res.returncode == 0, res.stderr
        keyed, weak = 0, []
        for pattern in res.stdout.splitlines():
            if _sed(pattern, plain) == plain:
                continue
            keyed += 1
            weak += [
                f"{form}: {pattern}" for form, text in forms.items()
                if _sed(pattern, text).count("zq7leak") == text.count("zq7leak")
            ]
        assert keyed >= 16, f"{keyed} key-named patterns, not the 16 or more the lib carries"
        assert not weak, weak

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


GREEN = (6, "true", 9, 9, 19, 19, 100, 50, 0, 0)


@pytest.mark.parametrize("args, kwargs, verdict", [
    (GREEN, {}, "OK"),
    # probe_flux_not_ready folds spec.suspend in, so a frozen cluster degrades too.
    ((6, "true", 9, 9, 19, 19, 100, 50, 1, 0), {}, "PARTIAL"),
    ((6, "true", 9, 9, 19, 19, 100, 50, 0, 1), {}, "PARTIAL"),
    ((5, "true", 9, 9, 18, 19, 94, 50, 0, 0), {}, "PARTIAL"),
    ((0, "true", 9, 9, 3, 19, 15, 50, 0, 0), {}, "FAILED"),
    ((6, "true", 0, 9, 19, 19, 100, 50, 0, 0), {}, "FAILED"),
    # A collector-side kubeconfig problem (probe defaults 0/0) never reads OK.
    ((6, "false", 0, 0, 19, 19, 100, 50, 0, 0), {}, "PARTIAL"),
    ((6, "true", 9, 9, 9, 19, 47, 50, 0, 0), {}, "FAILED"),
    # Guests count toward HOSTS_TOTAL; 3 of 22 down stays above the floor.
    ((6, "true", 9, 9, 19, 22, 86, 50, 0, 0), {}, "PARTIAL"),
    # A missing specialised collector leaves a whole block out of the artifact.
    (GREEN, {"sections_ok": 21, "sections_total": 22}, "PARTIAL"),
    (GREEN, {"sections_ok": 22, "sections_total": 22}, "OK"),
    # collect-state.sh coalesces an unaskable Alertmanager to 1, not 0.
    (GREEN, {"alerts": 1}, "PARTIAL"),
    (GREEN, {"alerts": 99}, "PARTIAL"),
], ids=[
    "all-green-is-ok", "flux-not-ready-or-suspended-degrades", "zfs-degraded-degrades",
    "one-host-down-degrades", "no-pve-host-is-failed", "api-ok-zero-ready-is-failed",
    "kubectl-unreachable-is-partial", "coverage-below-floor-is-failed",
    "some-guests-down-degrades", "failed-specialised-section-degrades",
    "all-sections-collected-stays-ok", "firing-or-unknown-alert-degrades",
    "alerts-never-fail",
])
def test_classify_regular(args, kwargs, verdict):
    assert _regular(*args, **kwargs) == verdict


JSON_GREEN = (6, 6, "true", 9, 9, 0, 0)


@pytest.mark.parametrize("args, kwargs, verdict", [
    (JSON_GREEN, {}, "healthy"),
    ((6, 6, "true", 9, 9, 1, 0), {}, "degraded"),
    ((6, 6, "true", 9, 9, 0, 1), {}, "degraded"),
    ((5, 6, "true", 9, 9, 0, 0), {}, "degraded"),
    ((0, 6, "true", 9, 9, 0, 0), {}, "catastrophic"),
    ((6, 6, "true", 0, 9, 0, 0), {}, "catastrophic"),
    ((6, 6, "false", 0, 0, 0, 0), {}, "degraded"),
    (JSON_GREEN, {"alerts": 1}, "degraded"),
    (JSON_GREEN, {"alerts": 99}, "degraded"),
], ids=[
    "all-green-is-healthy", "flux-not-ready-degrades", "zfs-degraded-degrades",
    "one-host-down-degrades", "no-pve-host-is-catastrophic",
    "api-ok-zero-ready-is-catastrophic", "kubectl-unreachable-is-degraded",
    "firing-or-unknown-alert-degrades", "alerts-never-catastrophic",
])
def test_classify_json(args, kwargs, verdict):
    assert _json(*args, **kwargs) == verdict


class TestClassifyJson:
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
# Same arguments as classify_regular; empty output means every OK predicate holds.

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


@pytest.fixture
def require_jq() -> None:
    """Fail rather than skip: a silent skip is how the jq-based helpers lose
    their only coverage in a job that stopped installing jq."""
    assert shutil.which("jq"), (
        "jq is not installed, so the jq-based helpers are unverified. "
        "Install jq in the job that runs this suite."
    )


@pytest.mark.usefixtures("require_jq")
class TestWarningEventsFilter:
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

    def test_the_consumer_filter_excludes_what_it_matches(self):
        exclude = '.reason == "FailedScheduling" and .metadata.namespace == "ci"'
        res = _run(
            f"warning_events_filter '{CUTOFF}' {shlex.quote(exclude)}",
            stdin=json.dumps({"items": [
                _event("FailedScheduling", "ci", SCHEDULING_FAILURE),
                _event("FailedScheduling", "apps", SCHEDULING_FAILURE),
            ]}),
        )
        assert res.stdout.strip() == "1", res.stderr

    def test_a_filter_with_a_backslash_regex_and_an_ampersand_is_kept_verbatim(self):
        exclude = ('((.message // "") | test("Insufficient\\\\s+cpu"))'
                   ' and .metadata.namespace == "ci&co"')
        res = _run(
            f"warning_events_filter '{CUTOFF}' {shlex.quote(exclude)}",
            stdin=json.dumps({"items": [
                _event("FailedScheduling", "ci&co", "0/3 nodes: Insufficient   cpu."),
                _event("FailedScheduling", "ci&co", "0/3 nodes: Insufficient memory."),
                _event("FailedScheduling", "apps", "0/3 nodes: Insufficient cpu."),
            ]}),
        )
        assert res.stdout.strip() == "2", res.stderr

    def test_a_broken_consumer_filter_reads_as_unknown(self):
        res = _run(f"warning_events_filter '{CUTOFF}' '.reason =='",
                   stdin=json.dumps({"items": [_event()]}))
        assert res.stdout.strip() == "unknown"
        assert res.stderr.startswith("warning_events_filter: jq failed: "), res.stderr
        assert res.stderr.strip() != "warning_events_filter: jq failed:"



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


# cs_head_tail: a time-sorted listing keeps both its oldest and newest lines.

def _head_tail(head: int, tail: int, stdin: str) -> list[str]:
    res = _run(f"cs_head_tail {head} {tail} none", stdin)
    assert res.returncode == 0, res.stderr
    return res.stdout.splitlines()


class TestCsHeadTail:
    def test_empty_input_prints_the_fallback(self):
        assert _head_tail(3, 2, "") == ["none"]

    def test_short_input_passes_through_without_a_marker(self):
        assert _head_tail(3, 2, "a\nb\nc\nd\n") == ["a", "b", "c", "d"]

    def test_long_input_keeps_both_ends_and_counts_the_gap(self):
        lines = "".join(f"{i}\n" for i in range(1, 21))
        assert _head_tail(3, 2, lines) == [
            "1", "2", "3", "  ... (15 lines omitted)", "19", "20",
        ]

    def test_an_unterminated_last_line_is_kept(self):
        assert _head_tail(1, 1, "a\nb\nc")[-1] == "c"


# cs_smart_ata_attrs: the attributes that predict a failing disk, raw values.

SMART_A = """\
  1 Raw_Read_Error_Rate     0x000f   083   063   044    Pre-fail  Always       -       200000000
  5 Reallocated_Sector_Ct   0x0033   100   100   010    Pre-fail  Always       -       0
187 Reported_Uncorrect      0x0032   099   099   000    Old_age   Always       -       1
188 Command_Timeout         0x0032   100   099   000    Old_age   Always       -       15 15 15
194 Temperature_Celsius     0x0022   047   055   000    Old_age   Always       -       47 (Min/Max 20/55)
197 Current_Pending_Sector  0x0012   100   100   000    Old_age   Always       -       0
198 Offline_Uncorrectable   0x0010   100   100   000    Old_age   Offline      -       0
"""


class TestSmartAtaAttrs:
    def test_every_predictor_is_reported_with_its_raw_value(self):
        out = _run("cs_smart_ata_attrs", SMART_A).stdout.split()
        assert out == [
            "Reallocated_Sector_Ct=0", "Reported_Uncorrect=1", "Command_Timeout=15",
            "Temperature_Celsius=47", "Current_Pending_Sector=0", "Offline_Uncorrectable=0",
        ]

    def test_a_noisy_vendor_counter_is_not_reported(self):
        assert "Raw_Read_Error_Rate" not in _run("cs_smart_ata_attrs", SMART_A).stdout


# cs_zpool_leaf_paths: the pool each leaf device belongs to.

ZPOOL_P = """\
  pool: tank
 state: ONLINE
config:

\tNAME                                    STATE     READ WRITE CKSUM
\ttank                                    ONLINE       0     0     0
\t  raidz1-0                              ONLINE       0     0     0
\t    /dev/disk/by-id/ata-DISK_A-part1    ONLINE       0     0     0
\t    /dev/disk/by-id/ata-DISK_B-part1    ONLINE       0     0     0

errors: No known data errors

  pool: ssd
config:
\tssd                ONLINE
\t  mirror-0         ONLINE
\t    /dev/nvme0n1p1 ONLINE
"""


class TestZpoolLeafPaths:
    def test_each_leaf_is_paired_with_its_pool(self):
        assert _run("cs_zpool_leaf_paths", ZPOOL_P).stdout.splitlines() == [
            "tank\t/dev/disk/by-id/ata-DISK_A-part1",
            "tank\t/dev/disk/by-id/ata-DISK_B-part1",
            "ssd\t/dev/nvme0n1p1",
        ]


# cs_disk_pool: a failed pool lookup never reads as "in no pool".

POOL_MAP = "/dev/sda\ttank\n/dev/nvme0n1\tssd\n"


class TestDiskPool:
    @pytest.mark.parametrize("dev,rc,want", [
        ("/dev/sda", "0", "tank"),
        ("/dev/nvme0n1", "0", "ssd"),
        ("/dev/sdz", "0", "-"),
        ("/dev/sda", "1", "unknown"),
        ("/dev/sdz", "1", "unknown"),
    ])
    def test_pool_or_marker(self, dev, rc, want):
        assert _run(f"cs_disk_pool {dev} {rc}", POOL_MAP).stdout == want + "\n"

    def test_an_empty_map_from_a_failed_lookup_is_unknown(self):
        assert _run("cs_disk_pool /dev/sda 1", "").stdout == "unknown\n"


# cs_pool_map: leaf paths resolve to whole disks, and an unresolved leaf fails
# the map so no SMART line reads "in no pool" for a pool member.

LEAVES = "tank\t/dev/disk/by-id/ata-A-part1\nssd\t/dev/nvme0n1\n"


def _pool_map(tmp_path: Path, leaves: str, lsblk_fails: str = "") -> subprocess.CompletedProcess:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir, "readlink", 'case "$2" in *ata-A-part1) echo /dev/sda1 ;; '
                               '*mapper/crypt*) echo /dev/dm-0 ;; *loop*) echo /dev/dm-9 ;; '
                               '*missing*) exit 1 ;; *) echo "$2" ;; esac')
    _stub(bin_dir, "lsblk", f'case "$3" in {lsblk_fails or "/nomatch"}) exit 32 ;; '
                            "/dev/sda1) echo sda ;; /dev/dm-0) echo sdb2 ;; "
                            "/dev/sdb2) echo sdb ;; /dev/dm-9) echo dm-9 ;; "
                            "*) printf '\\nchild\\n' ;; esac")
    return _run("cs_pool_map", leaves, env={"PATH": f"{bin_dir}:/usr/bin:/bin"})


class TestPoolMap:
    def test_partitions_and_whole_disks_map_to_their_disk(self, tmp_path):
        res = _pool_map(tmp_path, LEAVES)
        assert res.returncode == 0, res.stderr
        assert res.stdout.splitlines() == ["/dev/sda\ttank", "/dev/nvme0n1\tssd"]

    def test_a_stacked_leaf_maps_to_its_whole_disk(self, tmp_path):
        res = _pool_map(tmp_path, "tank\t/dev/mapper/crypt-a\n")
        assert res.returncode == 0, res.stderr
        assert res.stdout.splitlines() == ["/dev/sdb\ttank"]

    def test_a_parent_chain_that_never_ends_fails_the_map(self, tmp_path):
        res = _pool_map(tmp_path, "tank\t/dev/mapper/loop-a\n")
        assert res.returncode == 1
        assert res.stdout == ""
        assert "cannot resolve zpool leaf /dev/mapper/loop-a" in res.stderr

    def test_an_unresolvable_link_fails_the_map(self, tmp_path):
        res = _pool_map(tmp_path, LEAVES + "tank\t/dev/disk/by-id/missing\n")
        assert res.returncode == 1
        assert "cannot resolve zpool leaf /dev/disk/by-id/missing" in res.stderr
        assert "/dev/sda\ttank" in res.stdout

    def test_a_failed_lsblk_fails_the_map(self, tmp_path):
        res = _pool_map(tmp_path, LEAVES, lsblk_fails="/dev/sda1")
        assert res.returncode == 1
        assert "/dev/disk/by-id/ata-A-part1" in res.stderr

    def test_a_partial_map_labels_every_disk_unknown(self, tmp_path):
        """The member the failed leaf names is absent from the map, so a map
        status of 0 would label it "-", in no pool."""
        partial = _pool_map(tmp_path, LEAVES, lsblk_fails="/dev/sda1")
        rc = partial.returncode
        assert _run(f"cs_disk_pool /dev/sda {rc}", partial.stdout).stdout == "unknown\n"
        assert _run("cs_disk_pool /dev/sda 0", partial.stdout).stdout == "-\n"


# cs_snapshot_report: recency, count and pinned space, plus never-pruned snapshots.

SNAPSHOTS = (
    "D\ttank/share\t1.2T\ttrue\n"
    "D\tssd/appdata/metrics\t300M\tfalse\n"
    "D\ttank/empty\t0B\ttrue\n"
    "S\tssd/appdata/metrics@zfs-auto-snap_daily-2026-10-01\td1\n"
    "S\ttank/share@sync-20260930\td2\n"
    "S\tssd/appdata/metrics@sync-20260930\td3\n"
    "S\ttank/share@sync-20261031\td4\n"
    "S\ttank/share@zfs-auto-snap_hourly-2026-10-31\td5\n"
)


class TestSnapshotReport:
    def _report(self) -> list[str]:
        res = _run("cs_snapshot_report", SNAPSHOTS)
        assert res.returncode == 0, res.stderr
        return res.stdout.splitlines()

    def test_each_dataset_shows_newest_count_and_space(self):
        assert "  tank/share: newest d5, 3 snapshots, usedbysnapshots 1.2T" in self._report()

    def test_auto_snapshots_on_a_disabled_dataset_are_flagged(self):
        line = next(ln for ln in self._report() if "metrics" in ln)
        assert "auto-snapshot=false yet 1 zfs-auto-snap kept" in line

    def test_auto_snapshots_on_an_enabled_dataset_are_not_flagged(self):
        line = next(ln for ln in self._report() if "tank/share" in ln)
        assert "never pruned" not in line

    def test_other_snapshots_on_a_disabled_dataset_are_not_flagged(self):
        report = _run("cs_snapshot_report", SNAPSHOTS.replace("zfs-auto-snap_daily", "manual")).stdout
        assert "never pruned" not in report

    def test_a_dataset_without_snapshots_is_not_listed(self):
        assert not any("tank/empty" in ln for ln in self._report())


# cs_retry_ssh: one retry on ssh's own failure code, none on a remote failure.

class TestRetrySsh:
    def _calls(self, rcs: list[int]) -> tuple[int, int]:
        script = (
            f"rcs=({' '.join(map(str, rcs))}); n=0\n"
            'fake() { local rc=${rcs[$n]}; n=$((n + 1)); return "$rc"; }\n'
            "sleep() { :; }\n"
            'cs_retry_ssh fake; rc=$?; echo "$rc $n"'
        )
        rc, calls = _run(script).stdout.split()
        return int(rc), int(calls)

    def test_an_ssh_failure_is_retried_once(self):
        assert self._calls([255, 0]) == (0, 2)

    def test_a_second_ssh_failure_is_returned(self):
        assert self._calls([255, 255, 0]) == (255, 2)

    def test_a_remote_command_failure_is_not_retried(self):
        assert self._calls([1, 0]) == (1, 1)

    def test_success_runs_once(self):
        assert self._calls([0]) == (0, 1)


# cs_journal_error_sources: the error-emitting sources and their counts.

def test_journal_error_sources_name_the_container_and_count():
    entries = [
        {"CONTAINER_NAME": "app_server", "SYSLOG_IDENTIFIER": "3f2a9c1d"},
        {"CONTAINER_NAME": "app_server", "SYSLOG_IDENTIFIER": "3f2a9c1d"},
        {"SYSLOG_IDENTIFIER": "kernel"},
        {"_COMM": "pvedaemon"},
    ]
    stdin = "".join(json.dumps(e) + "\n" for e in entries) + "not json\n\n"
    res = _run("cs_journal_error_sources", stdin)
    assert res.returncode == 0, res.stderr
    assert res.stdout.split() == [
        "2", "app_server", "1", "kernel", "1", "pvedaemon", "1", "(unparseable)",
    ]


def test_journal_error_sources_fail_when_no_line_parses():
    """Text instead of JSON must reach the caller's UNKNOWN branch, not read as none."""
    res = _run("cs_journal_error_sources", "-- No entries --\n[1, 2]\n")
    assert res.returncode == 1
    assert res.stdout.split() == ["2", "(unparseable)"]


def test_journal_error_sources_with_no_input_print_nothing():
    res = _run("cs_journal_error_sources", "")
    assert res.returncode == 0 and res.stdout == ""


# cs_tailscale_status: a failed probe never reads as a benign "no peers".

class TestCsTailscaleStatus:
    @staticmethod
    def _bin(tmp_path: Path, status: str, rc: int = 0, sudo: str = 'exec "$@"') -> Path:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _stub(bin_dir, "sudo", f'[ "$1" = -n ] || {{ echo prompt >&2; exit 1; }}; shift\n{sudo}')
        _stub(bin_dir, "tailscale", f"printf '%s' {shlex.quote(status)}; exit {rc}")
        return bin_dir

    def _status(self, bin_dir: Path) -> subprocess.CompletedProcess:
        return _run("cs_tailscale_status", env={"PATH": f"{bin_dir}:/usr/bin:/bin"})

    def test_no_binary_says_not_installed(self, tmp_path: Path) -> None:
        res = _run(f"PATH={shlex.quote(str(tmp_path))} cs_tailscale_status")
        assert res.stdout.strip() == "Tailscale not installed", res.stderr

    def test_empty_output_says_no_peers(self, tmp_path: Path) -> None:
        res = self._status(self._bin(tmp_path, ""))
        assert res.stdout.strip() == "Tailscale connected, no peers", res.stderr

    def test_a_failed_tailscale_call_is_reported_as_failed(self, tmp_path: Path) -> None:
        bin_dir = self._bin(
            tmp_path, "failed to connect to local tailscaled\nsecond line\n", rc=1
        )
        assert self._status(bin_dir).stdout.strip() == (
            "Tailscale status FAILED: failed to connect to local tailscaled"
        )

    def test_a_refused_sudo_is_reported_as_failed(self, tmp_path: Path) -> None:
        bin_dir = self._bin(
            tmp_path, "peer0\n", sudo='echo "sudo: a password is required" >&2; exit 1'
        )
        assert self._status(bin_dir).stdout.strip() == (
            "Tailscale status FAILED: sudo: a password is required"
        )

    def test_the_first_five_status_lines_are_printed(self, tmp_path: Path) -> None:
        bin_dir = self._bin(tmp_path, "".join(f"peer{i}\n" for i in range(8)))
        res = self._status(bin_dir)
        assert res.stdout.splitlines() == [f"peer{i}" for i in range(5)], res.stderr


# firing_alerts_counts: what gates the verdict, and what is tenant advisory.

def _alert(name: str, severity: str = "warning", namespace: str | None = None,
           state: str | None = None, silenced_by: list | None = None) -> dict:
    labels = {"alertname": name, "severity": severity}
    if namespace is not None:
        labels["namespace"] = namespace
    alert: dict = {"labels": labels, "startsAt": "2026-01-01T00:00:00Z"}
    if state is not None:
        alert["status"] = {"state": state, "silencedBy": silenced_by or []}
    return alert


def _counts(alerts, tenants: str = "tenant-a") -> str:
    payload = alerts if isinstance(alerts, str) else json.dumps(alerts)
    return _run(f"firing_alerts_counts {shlex.quote(tenants)}", payload).stdout.strip()


@pytest.mark.usefixtures("require_jq")
class TestFiringAlertsCounts:
    def test_a_platform_alert_gates(self):
        assert _counts([_alert("TargetDown", namespace="observability")]) == "1 0"

    def test_null_routed_severity_none_does_not_count(self):
        assert _counts([_alert("DeployInProgress", "none", "observability")]) == "0 0"

    def test_watchdog_and_infoinhibitor_do_not_count(self):
        assert _counts([_alert("Watchdog", "none"), _alert("InfoInhibitor", "info")]) == "0 0"

    def test_a_tenant_alert_is_counted_apart(self):
        assert _counts([
            _alert("JobLate", namespace="tenant-a"),
            _alert("TargetDown", namespace="observability"),
        ]) == "1 1"

    def test_every_listed_tenant_counts_apart(self):
        assert _counts([
            _alert("JobLate", namespace="tenant-a"),
            _alert("JobLate", namespace="tenant-b"),
        ], tenants="tenant-a tenant-b") == "0 2"

    def test_an_alert_with_no_namespace_is_platform(self):
        assert _counts([_alert("HostDown")], tenants="") == "1 0"

    def test_null_payload_is_zero(self):
        assert _counts("null") == "0 0"

    def test_unparseable_payload_is_unknown(self):
        assert _counts("not json") == "unknown"

    @pytest.mark.parametrize("payload", ["{}", '{"error": "upstream down"}', '"down"'])
    def test_anything_but_an_alert_list_is_unknown(self, payload):
        assert _counts(payload) == "unknown"


@pytest.mark.usefixtures("require_jq")
class TestAlertsRender:
    def _render(self, alerts) -> subprocess.CompletedProcess:
        return _run("alerts_render", json.dumps(alerts))

    def test_an_empty_set_renders_nothing(self):
        res = self._render([])
        assert res.returncode == 0 and res.stdout == ""

    def test_active_alerts_come_first_and_name_their_scope(self):
        res = self._render([
            _alert("Quiet", namespace="apps", state="suppressed", silenced_by=["s1"]),
            _alert("HostDown", "critical", state="active"),
        ])
        assert res.stdout.splitlines() == [
            "  HostDown [critical] - since 2026-01-01T00:00:00Z",
            "  Quiet [warning] apps since 2026-01-01T00:00:00Z (suppressed: s1)",
        ], res.stderr

    def test_an_alert_with_no_state_sorts_as_firing(self):
        stateless = _alert("NodeDown", "critical")
        res = self._render([
            _alert("Quiet", namespace="apps", state="suppressed", silenced_by=["s1"]),
            stateless,
        ])
        assert res.stdout.splitlines() == [
            "  NodeDown [critical] - since 2026-01-01T00:00:00Z",
            "  Quiet [warning] apps since 2026-01-01T00:00:00Z (suppressed: s1)",
        ], res.stderr

    def test_unparseable_input_fails(self):
        assert _run("alerts_render", "not json").returncode != 0

    def test_null_renders_nothing(self):
        res = _run("alerts_render", "null")
        assert res.returncode == 0 and res.stdout == ""

    @pytest.mark.parametrize("payload", ["{}", '{"error": "upstream down"}'])
    def test_an_object_fails(self, payload):
        res = _run("alerts_render", payload)
        assert res.returncode != 0 and res.stdout == ""
