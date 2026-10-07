"""A checksum that no longer matches the artefact its version pin names exits 1."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from script_loader import load_script

gate = load_script("check-version-checksums.py")

INSTALLER = b"#!/bin/sh\necho install\n"
INSTALLER_SHA = hashlib.sha256(INSTALLER).hexdigest()

ENTRY = {
    "name": "k3s",
    "var_name": "k3s_version",
    "checksum_var": "k3s_install_script_checksum",
    "checksum_url": "https://example.invalid/k3s/{version}/install.sh",
}


def fetcher(_url: str) -> bytes:
    return INSTALLER


def vars_data(checksum: str) -> dict:
    return {"k3s_version": "v1.36.4+k3s1", "k3s_install_script_checksum": checksum}


class TestViolations:
    def test_matching_checksum_is_clean(self):
        assert gate.violations([ENTRY], vars_data(f"sha256:{INSTALLER_SHA}"), fetcher) == []

    def test_bare_hex_pin_is_accepted(self):
        assert gate.violations([ENTRY], vars_data(INSTALLER_SHA), fetcher) == []

    def test_stale_checksum_is_reported(self):
        found = gate.violations([ENTRY], vars_data("sha256:" + "0" * 64), fetcher)
        assert len(found) == 1
        assert "k3s_install_script_checksum" in found[0]

    def test_the_url_is_rendered_at_the_pinned_version(self):
        seen = []

        def recording(url: str) -> bytes:
            seen.append(url)
            return INSTALLER

        gate.violations([ENTRY], vars_data(INSTALLER_SHA), recording)
        assert seen == ["https://example.invalid/k3s/v1.36.4+k3s1/install.sh"]

    def test_an_unknown_url_placeholder_is_an_operator_error(self):
        entry = dict(ENTRY, checksum_url="https://example.invalid/{release}/install.sh")
        with pytest.raises(gate.ConfigError):
            gate.violations([entry], vars_data(INSTALLER_SHA), fetcher)

    def test_missing_vars_key_is_an_operator_error(self):
        with pytest.raises(gate.ConfigError):
            gate.violations([ENTRY], {"k3s_version": "v1.36.4+k3s1"}, fetcher)

    def test_empty_version_is_an_operator_error(self):
        with pytest.raises(gate.ConfigError):
            gate.violations([ENTRY], {"k3s_version": "", "k3s_install_script_checksum": "x"}, fetcher)


class TestChecksumEntries:
    def test_entries_without_a_checksum_pin_are_ignored(self):
        assert gate.checksum_entries({"services": [{"name": "x", "var_name": "x_version"}]}) == []

    def test_half_a_pin_is_rejected(self):
        with pytest.raises(gate.ConfigError):
            gate.checksum_entries(
                {"services": [{"name": "x", "var_name": "x_version", "checksum_var": "x_sha"}]}
            )

    def test_a_version_file_pin_is_rejected(self):
        entry = dict(ENTRY, version_file="ci")
        with pytest.raises(gate.ConfigError):
            gate.checksum_entries({"services": [entry]})

    def test_a_pin_without_a_var_name_is_rejected(self):
        entry = {k: v for k, v in ENTRY.items() if k != "var_name"}
        with pytest.raises(gate.ConfigError):
            gate.checksum_entries({"services": [entry]})


def write_registry(tmp_path: Path, entry: dict) -> Path:
    config = tmp_path / "version-registry.py"
    config.write_text(f"CONFIG = {{'vars_file': 'vars.yml', 'services': [{entry!r}]}}\n")
    return config


class TestMain:
    def test_clean_run_exits_zero(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gate, "fetch", fetcher)
        (tmp_path / "vars.yml").write_text(
            f"k3s_version: 'v1.36.4+k3s1'\nk3s_install_script_checksum: 'sha256:{INSTALLER_SHA}'\n"
        )
        assert gate.main(
            ["--config", str(write_registry(tmp_path, ENTRY)), "--repo-root", str(tmp_path)]
        ) == 0

    def test_stale_pin_exits_one(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(gate, "fetch", fetcher)
        (tmp_path / "vars.yml").write_text(
            "k3s_version: 'v1.36.4+k3s1'\nk3s_install_script_checksum: 'sha256:%s'\n" % ("0" * 64)
        )
        assert gate.main(
            ["--config", str(write_registry(tmp_path, ENTRY)), "--repo-root", str(tmp_path)]
        ) == 1
        assert "Stale checksum pins" in capsys.readouterr().err

    def test_missing_registry_exits_two(self, tmp_path):
        assert gate.main(["--config", str(tmp_path / "nope.py"), "--repo-root", str(tmp_path)]) == 2

    def test_missing_vars_file_exits_two(self, tmp_path):
        assert gate.main(
            ["--config", str(write_registry(tmp_path, ENTRY)), "--repo-root", str(tmp_path)]
        ) == 2

    def test_an_unparseable_vars_file_exits_two(self, tmp_path, capsys):
        """Exit 1 is the stale-pin code, so an unreadable input must not use it."""
        (tmp_path / "vars.yml").write_text("a: [1,\n")
        assert gate.main(
            ["--config", str(write_registry(tmp_path, ENTRY)), "--repo-root", str(tmp_path)]
        ) == 2
        err = capsys.readouterr().err
        assert "vars.yml" in err
        assert "Traceback" not in err

    def test_a_binary_vars_file_exits_two(self, tmp_path, capsys):
        (tmp_path / "vars.yml").write_bytes(b"\xff\xfe\x00binary")
        assert gate.main(
            ["--config", str(write_registry(tmp_path, ENTRY)), "--repo-root", str(tmp_path)]
        ) == 2
        assert "Traceback" not in capsys.readouterr().err

    def test_a_pin_without_a_var_name_exits_two(self, tmp_path):
        entry = {k: v for k, v in ENTRY.items() if k != "var_name"}
        (tmp_path / "vars.yml").write_text("k3s_version: 'v1'\n")
        assert gate.main(
            ["--config", str(write_registry(tmp_path, entry)), "--repo-root", str(tmp_path)]
        ) == 2

    def test_an_unknown_url_placeholder_exits_two(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gate, "fetch", fetcher)
        entry = dict(ENTRY, checksum_url="https://example.invalid/{release}/install.sh")
        (tmp_path / "vars.yml").write_text(
            f"k3s_version: 'v1'\nk3s_install_script_checksum: 'sha256:{INSTALLER_SHA}'\n"
        )
        assert gate.main(
            ["--config", str(write_registry(tmp_path, entry)), "--repo-root", str(tmp_path)]
        ) == 2

    def test_registry_without_checksum_pins_exits_two(self, tmp_path, capsys):
        config = write_registry(tmp_path, {"name": "x", "var_name": "x_version"})
        assert gate.main(["--config", str(config), "--repo-root", str(tmp_path)]) == 2
        assert "declares no checksum pin" in capsys.readouterr().err

    def test_registry_without_checksum_pins_passes_with_allow_empty(self, tmp_path, capsys):
        config = write_registry(tmp_path, {"name": "x", "var_name": "x_version"})
        assert gate.main(
            ["--config", str(config), "--repo-root", str(tmp_path), "--allow-empty"]
        ) == 0
        assert "No checksum pins" in capsys.readouterr().out

    def test_a_malformed_url_format_string_exits_two(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gate, "fetch", fetcher)
        entry = dict(ENTRY, checksum_url="https://example.invalid/{version/install.sh")
        (tmp_path / "vars.yml").write_text(
            f"k3s_version: 'v1'\nk3s_install_script_checksum: 'sha256:{INSTALLER_SHA}'\n"
        )
        assert gate.main(
            ["--config", str(write_registry(tmp_path, entry)), "--repo-root", str(tmp_path)]
        ) == 2

    def test_a_plaintext_http_checksum_url_exits_two(self, tmp_path):
        entry = dict(ENTRY, checksum_url="http://example.invalid/{version}/install.sh")
        (tmp_path / "vars.yml").write_text(
            f"k3s_version: 'v1'\nk3s_install_script_checksum: 'sha256:{INSTALLER_SHA}'\n"
        )
        assert gate.main(
            ["--config", str(write_registry(tmp_path, entry)), "--repo-root", str(tmp_path)]
        ) == 2

    def test_the_config_env_var_selects_the_registry(self, tmp_path, monkeypatch, capsys):
        config = write_registry(tmp_path, {"name": "x", "var_name": "x_version"})
        monkeypatch.setenv("CHECK_VERSIONS_CONFIG", str(config))
        assert gate.main(["--repo-root", str(tmp_path), "--allow-empty"]) == 0
        assert "No checksum pins" in capsys.readouterr().out


class TestFetchFailures:
    """A download failure is an operator error: exit 1 means "recompute the pin"."""

    @staticmethod
    def _unreachable(monkeypatch):
        def raiser(*_args, **_kwargs):
            raise gate.urllib.error.URLError("unreachable")

        monkeypatch.setattr(gate.urllib.request, "urlopen", raiser)

    def test_fetch_maps_a_network_failure_to_a_config_error(self, monkeypatch):
        self._unreachable(monkeypatch)
        with pytest.raises(gate.ConfigError, match="failed to download"):
            gate.fetch("https://example.invalid/install.sh")

    def test_a_failed_download_exits_two_not_one(self, tmp_path, monkeypatch, capsys):
        self._unreachable(monkeypatch)
        (tmp_path / "vars.yml").write_text(
            f"k3s_version: 'v1.36.4+k3s1'\nk3s_install_script_checksum: 'sha256:{INSTALLER_SHA}'\n"
        )
        assert gate.main(
            ["--config", str(write_registry(tmp_path, ENTRY)), "--repo-root", str(tmp_path)]
        ) == 2
        err = capsys.readouterr().err
        assert "failed to download" in err
        assert "Stale checksum pins" not in err


def test_a_non_mapping_service_entry_is_rejected():
    with pytest.raises(gate.ConfigError, match="not a mapping"):
        gate.checksum_entries({"services": ["k3s"]})


def test_a_vars_file_that_is_a_list_exits_two(tmp_path, capsys):
    (tmp_path / "vars.yml").write_text("- a\n- b\n")
    assert gate.main(
        ["--config", str(write_registry(tmp_path, ENTRY)), "--repo-root", str(tmp_path)]
    ) == 2
    assert "vars.yml" in capsys.readouterr().err


def test_fetch_rejects_a_non_https_url():
    with pytest.raises(gate.ConfigError):
        gate.fetch("file:///etc/passwd")
    with pytest.raises(gate.ConfigError):
        gate.fetch("http://example.invalid/install.sh")
