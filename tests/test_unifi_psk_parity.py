"""Tests for scripts/unifi-psk-parity.py, the write-only WLAN key parity gate.

Covers compare() AND main()'s wiring, and that no key ever reaches stdout.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from script_loader import load_script

REPO = Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "examples" / "unifi-psk.example.json"

gate = load_script("unifi-psk-parity.py")


def _config(tmp_path: Path, wlans: dict, site: str = "default") -> Path:
    path = tmp_path / "unifi-psk.json"
    path.write_text(json.dumps({"site": site, "wlans": wlans}))
    return path


@pytest.fixture()
def api(monkeypatch):
    monkeypatch.setenv("UNIFI_API_URL", "https://unifi.invalid")
    monkeypatch.setenv("UNIFI_API_KEY", "token")
    monkeypatch.delenv("UNIFI_ALLOW_INSECURE", raising=False)

    def _serve(live: dict[str, str]):
        monkeypatch.setattr(gate, "fetch_wlans", lambda url, api_key, verify=True: live)

    return _serve


def test_example_config_loads():
    cfg = gate.load_config(EXAMPLE)
    assert cfg["site"] == "default" and set(cfg["wlans"]) == {"example-home", "example-iot"}


def test_compare_reports_each_verdict():
    live = {"a": "k1", "b": "k2"}
    wanted = {"a": "k1", "b": "other", "c": "k3"}
    assert gate.compare(live, wanted) == [("a", "match"), ("b", "mismatch"), ("c", "absent")]


def test_parity_exits_zero(api, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WLAN_PSK_HOME", "correct horse")
    api({"home": "correct horse"})
    assert gate.main(["--config", str(_config(tmp_path, {"home": "WLAN_PSK_HOME"}))]) == 0
    out = capsys.readouterr().out
    assert "home: match" in out and "correct horse" not in out


def test_mismatch_exits_one_without_printing_either_key(api, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WLAN_PSK_HOME", "store value")
    api({"home": "console value"})
    assert gate.main(["--config", str(_config(tmp_path, {"home": "WLAN_PSK_HOME"}))]) == 1
    out = capsys.readouterr().out
    assert "home: mismatch" in out
    assert "store value" not in out and "console value" not in out


def test_absent_wlan_exits_one(api, tmp_path, monkeypatch):
    monkeypatch.setenv("WLAN_PSK_HOME", "x" * 8)
    api({})
    assert gate.main(["--config", str(_config(tmp_path, {"home": "WLAN_PSK_HOME"}))]) == 1


def test_missing_secret_is_an_operator_error(api, tmp_path, monkeypatch):
    monkeypatch.delenv("WLAN_PSK_HOME", raising=False)
    api({"home": "k"})
    assert gate.main(["--config", str(_config(tmp_path, {"home": "WLAN_PSK_HOME"}))]) == 2


def test_missing_credentials_are_an_operator_error(tmp_path, monkeypatch):
    monkeypatch.delenv("UNIFI_API_URL", raising=False)
    monkeypatch.delenv("UNIFI_API_KEY", raising=False)
    assert gate.main(["--config", str(_config(tmp_path, {"home": "WLAN_PSK_HOME"}))]) == 2


def test_api_failure_names_the_type_not_the_body(api, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WLAN_PSK_HOME", "x" * 8)

    def _boom(url, api_key, verify=True):
        raise RuntimeError("body with x_passphrase inside")

    monkeypatch.setattr(gate, "fetch_wlans", _boom)
    assert gate.main(["--config", str(_config(tmp_path, {"home": "WLAN_PSK_HOME"}))]) == 2
    out = capsys.readouterr().out
    assert "RuntimeError" in out and "x_passphrase inside" not in out


def test_rejects_a_config_without_wlans(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"site": "default", "wlans": {}}))
    with pytest.raises(ValueError):
        gate.load_config(path)
