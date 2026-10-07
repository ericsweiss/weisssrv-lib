"""Tests for scripts/unifi-settings-drift.py, the console-owned UniFi posture gate.

Covers diff_settings AND main()'s wiring: an inverted drift test or swapped
diff_settings arguments must not stay green.
"""
from __future__ import annotations

import json
import ssl
from pathlib import Path

import pytest
from script_loader import load_script

REPO = Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "examples" / "unifi-settings.example.json"

gate = load_script("unifi-settings-drift.py")


def _config(tmp_path: Path, desired: dict, **extra) -> Path:
    path = tmp_path / "unifi-settings.json"
    path.write_text(json.dumps({"site": "default", "desired": desired, **extra}))
    return path


@pytest.fixture()
def api(monkeypatch):
    """Credentials in the environment plus a scripted fetch_section."""
    monkeypatch.setenv("UNIFI_API_URL", "https://unifi.invalid")
    monkeypatch.setenv("UNIFI_API_KEY", "token")
    monkeypatch.delenv("UNIFI_ALLOW_INSECURE", raising=False)
    calls: list[str] = []

    def _serve(live):
        def _fetch(url, api_key, verify=True):
            calls.append(url)
            return live

        monkeypatch.setattr(gate, "fetch_section", _fetch)
        return calls

    return _serve


class TestShippedExample:
    def test_the_example_config_loads(self):
        cfg = gate.load_config(EXAMPLE)
        assert cfg["site"] and cfg["section"] == "ips"
        assert cfg["desired"]["ips_mode"] == "ips"


class TestLoadConfig:
    def test_a_missing_key_is_rejected(self, tmp_path):
        path = tmp_path / "c.json"
        path.write_text(json.dumps({"site": "default"}))
        with pytest.raises(ValueError, match="missing"):
            gate.load_config(path)

    def test_an_empty_desired_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="non-empty"):
            gate.load_config(_config(tmp_path, {}))

    def test_the_section_defaults_to_ips(self, tmp_path):
        assert gate.load_config(_config(tmp_path, {"a": 1}))["section"] == "ips"

    def test_a_section_with_a_slash_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="single path segment"):
            gate.load_config(_config(tmp_path, {"a": 1}, section="ips/../rest"))


class TestSettingsUrl:
    def test_it_addresses_the_section_not_rest_setting(self):
        url = gate.settings_url("https://unifi.invalid/", "default", "ips")
        assert url == "https://unifi.invalid/proxy/network/api/s/default/get/setting/ips"
        assert "/rest/setting" not in url


class TestDiffSettings:
    def test_an_identical_section_has_no_drift(self):
        assert gate.diff_settings({"ips_mode": "ips"}, {"ips_mode": "ips"}) == []

    def test_a_changed_value_is_reported(self):
        drift = gate.diff_settings({"ips_mode": "ids"}, {"ips_mode": "ips"})
        assert len(drift) == 1 and "ips_mode" in drift[0]

    def test_an_absent_key_is_reported(self):
        drift = gate.diff_settings({}, {"ips_mode": "ips"})
        assert drift and "absent from the live section" in drift[0]

    def test_a_list_compares_order_insensitively(self):
        assert gate.diff_settings({"c": ["b", "a"]}, {"c": ["a", "b"]}) == []
        assert gate.diff_settings({"c": ["a"]}, {"c": ["a", "b"]})

    def test_an_undeclared_live_key_is_not_drift(self):
        assert gate.diff_settings({"ips_mode": "ips", "utm_token": "x"}, {"ips_mode": "ips"}) == []

    def test_a_long_value_is_truncated(self):
        drift = gate.diff_settings({"c": ["x" * 200]}, {"c": ["y"]})
        assert drift and drift[0].endswith("…") is False
        assert "…" in drift[0]


class TestMainWiring:
    """A clean run must exit 0 and a drifted one 1, so an inverted check or a
    swapped diff_settings argument order cannot stay green."""

    def test_a_clean_section_exits_zero(self, tmp_path, api, capsys):
        api({"ips_mode": "ips"})
        rc = gate.main(["--config", str(_config(tmp_path, {"ips_mode": "ips"}))])
        out = capsys.readouterr().out
        assert rc == 0
        assert "OK:" in out

    def test_a_drifted_section_exits_one_and_names_the_key(self, tmp_path, api, capsys):
        api({"ips_mode": "ids"})
        rc = gate.main(["--config", str(_config(tmp_path, {"ips_mode": "ips"}))])
        out = capsys.readouterr().out
        assert rc == 1
        assert "DRIFT:" in out
        assert "ips_mode" in out

    def test_the_live_value_is_reported_as_live_not_as_desired(self, tmp_path, api, capsys):
        """Swapped diff_settings(desired, live) would print the two the other
        way round and send the operator at the wrong side."""
        api({"ips_mode": "ids"})
        gate.main(["--config", str(_config(tmp_path, {"ips_mode": "ips"}))])
        assert "'ids' != 'ips'" in capsys.readouterr().out

    def test_the_configured_section_reaches_the_url(self, tmp_path, api):
        calls = api({"a": 1})
        gate.main(["--config", str(_config(tmp_path, {"a": 1}, section="usg"))])
        assert calls and calls[0].endswith("/get/setting/usg")

    def test_missing_credentials_exit_two(self, tmp_path, monkeypatch, capsys):
        monkeypatch.delenv("UNIFI_API_URL", raising=False)
        monkeypatch.delenv("UNIFI_API_KEY", raising=False)
        rc = gate.main(["--config", str(_config(tmp_path, {"a": 1}))])
        assert rc == 2
        assert "UNIFI_API_URL" in capsys.readouterr().out

    def test_a_missing_config_exits_two(self, tmp_path, api, capsys):
        api({"a": 1})
        assert gate.main(["--config", str(tmp_path / "nope.json")]) == 2
        assert "ERROR:" in capsys.readouterr().out

    def test_a_failed_read_exits_two_without_quoting_the_body(
        self, tmp_path, api, monkeypatch, capsys
    ):
        """A controller error page can quote the request, credentials included."""
        api({"a": 1})

        def _boom(url, api_key, verify=True):
            raise RuntimeError("x-api-key: supersecret")

        monkeypatch.setattr(gate, "fetch_section", _boom)
        rc = gate.main(["--config", str(_config(tmp_path, {"a": 1}))])
        out = capsys.readouterr().out
        assert rc == 2
        assert "RuntimeError" in out
        assert "supersecret" not in out


class _FakeResponse:
    """Minimal urlopen return value: json.load() reads it, `with` closes it."""

    def __init__(self, payload):
        self._body = json.dumps(payload)

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


@pytest.fixture()
def urlopen_spy(monkeypatch):
    """Capture the ssl context fetch_section builds and serve a canned body."""
    seen: dict = {}

    def fake(_req, timeout=None, context=None):
        seen["context"] = context
        return _FakeResponse(seen.get("payload", {"data": [{"ips_mode": "ips"}]}))

    monkeypatch.setattr(gate.urllib.request, "urlopen", fake)
    return seen


def test_tls_verification_is_on_unless_opted_out(tmp_path, monkeypatch, urlopen_spy):
    """The insecure path is an explicit opt-in, never the default."""
    config = str(_config(tmp_path, {"ips_mode": "ips"}))
    monkeypatch.setenv("UNIFI_API_URL", "https://unifi.invalid")
    monkeypatch.setenv("UNIFI_API_KEY", "k")
    monkeypatch.delenv("UNIFI_ALLOW_INSECURE", raising=False)
    gate.main(["--config", config])
    assert urlopen_spy["context"].verify_mode == ssl.CERT_REQUIRED
    assert urlopen_spy["context"].check_hostname is True

    monkeypatch.setenv("UNIFI_ALLOW_INSECURE", "1")
    gate.main(["--config", config])
    assert urlopen_spy["context"].verify_mode == ssl.CERT_NONE
    assert urlopen_spy["context"].check_hostname is False


class TestFetchSection:
    """The shape guard: several sections in one body must not be compared."""

    def test_a_well_formed_body_returns_the_section(self, urlopen_spy):
        urlopen_spy["payload"] = {"data": [{"ips_mode": "ips"}]}
        assert gate.fetch_section("https://unifi.invalid", "k") == {"ips_mode": "ips"}

    @pytest.mark.parametrize(
        "payload",
        [
            {"data": [{"ips_mode": "ips"}, {"ips_mode": "ids"}]},
            {"data": []},
            {"data": {"ips_mode": "ips"}},
            {},
        ],
    )
    def test_an_unexpected_shape_is_refused(self, urlopen_spy, payload):
        urlopen_spy["payload"] = payload
        with pytest.raises(RuntimeError, match="unexpected response shape"):
            gate.fetch_section("https://unifi.invalid", "k")
