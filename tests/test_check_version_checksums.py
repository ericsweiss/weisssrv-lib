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
PINNED = f"k3s_version: 'v1.36.4+k3s1'\nk3s_install_script_checksum: 'sha256:{INSTALLER_SHA}'\n"
NO_VAR_NAME = {k: v for k, v in ENTRY.items() if k != "var_name"}


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

    def test_missing_vars_key_is_an_operator_error(self):
        with pytest.raises(gate.ConfigError):
            gate.violations([ENTRY], {"k3s_version": "v1.36.4+k3s1"}, fetcher)

    def test_empty_version_is_an_operator_error(self):
        with pytest.raises(gate.ConfigError):
            gate.violations([ENTRY], {"k3s_version": "", "k3s_install_script_checksum": "x"}, fetcher)


class TestChecksumEntries:
    def test_entries_without_a_checksum_pin_are_ignored(self):
        assert gate.checksum_entries({"services": [{"name": "x", "var_name": "x_version"}]}) == []


def write_registry(tmp_path: Path, entry: dict) -> Path:
    config = tmp_path / "version-registry.py"
    config.write_text(f"CONFIG = {{'vars_file': 'vars.yml', 'services': [{entry!r}]}}\n")
    return config


class TestMain:
    def test_clean_run_exits_zero(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gate, "fetch", fetcher)
        (tmp_path / "vars.yml").write_text(PINNED)
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

    def test_the_config_env_var_selects_the_registry(self, tmp_path, monkeypatch, capsys):
        config = write_registry(tmp_path, {"name": "x", "var_name": "x_version"})
        monkeypatch.setenv("CHECK_VERSIONS_CONFIG", str(config))
        assert gate.main(["--repo-root", str(tmp_path), "--allow-empty"]) == 0
        assert "No checksum pins" in capsys.readouterr().out


# Exit 1 means "recompute the pin", so no unusable input may reach it.
@pytest.mark.parametrize("entry,vars_text,needle", [
    pytest.param(None, PINNED, "nope.py not found", id="missing_registry"),
    pytest.param(ENTRY, None, "vars.yml not found", id="missing_vars_file"),
    pytest.param(ENTRY, b"a: [1,\n", "vars.yml", id="unparseable_vars_file"),
    pytest.param(ENTRY, b"\xff\xfe\x00binary", "vars.yml", id="binary_vars_file"),
    pytest.param(ENTRY, b"- a\n- b\n", "top-level is not a mapping", id="vars_file_is_a_list"),
    pytest.param(NO_VAR_NAME, PINNED, "no var_name", id="pin_without_a_var_name"),
    pytest.param({"name": "x", "var_name": "x_version", "checksum_var": "x_sha"}, PINNED,
                 "declares only one of", id="half_a_pin"),
    pytest.param(dict(ENTRY, version_file="ci"), PINNED, "version_file", id="version_file_pin"),
    pytest.param("k3s", PINNED, "not a mapping", id="non_mapping_service_entry"),
    pytest.param(dict(ENTRY, checksum_url="https://example.invalid/{release}/install.sh"),
                 PINNED, "not a usable format string", id="unknown_url_placeholder"),
    pytest.param(dict(ENTRY, checksum_url="https://example.invalid/{version/install.sh"),
                 PINNED, "not a usable format string", id="malformed_url_format_string"),
    pytest.param(dict(ENTRY, checksum_url="http://example.invalid/{version}/install.sh"),
                 PINNED, "refusing a non-https", id="plaintext_http_url"),
    pytest.param(dict(ENTRY, checksum_url="file:///etc/passwd"), PINNED,
                 "refusing a non-https", id="file_url"),
    pytest.param(ENTRY, PINNED, "failed to download", id="failed_download"),
])
def test_an_unusable_input_exits_two(tmp_path, monkeypatch, capsys, entry, vars_text, needle):
    def unreachable(*_args, **_kwargs):
        raise gate.urllib.error.URLError("unreachable")

    monkeypatch.setattr(gate.urllib.request, "urlopen", unreachable)
    config = tmp_path / "nope.py" if entry is None else write_registry(tmp_path, entry)
    if vars_text is not None:
        vars_file = tmp_path / "vars.yml"
        vars_file.write_bytes(vars_text.encode() if isinstance(vars_text, str) else vars_text)
    assert gate.main(["--config", str(config), "--repo-root", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert needle in err
    assert "Traceback" not in err
    assert "Stale checksum pins" not in err
