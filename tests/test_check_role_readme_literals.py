#!/usr/bin/env python3
"""The role-README literal gate fails on a seeded literal and passes without one."""

import os
from pathlib import Path

import pytest

from script_loader import REPO, load_script

ROLES = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles"

mod = load_script("check-role-readme-literals.py")


def _roles(tmp_path: Path, body: str, name: str = "demo") -> Path:
    role = tmp_path / "roles" / name
    role.mkdir(parents=True)
    (role / "README.md").write_text(body, encoding="utf-8")
    return tmp_path / "roles"


def test_a_clean_readme_passes(tmp_path):
    roles = _roles(tmp_path, "# demo\n\nPoint it at `resolver.example.com`.\n")
    assert mod.main(["--roles-dir", str(roles), "--no-site-domains", "--no-site-addresses"]) == 0


@pytest.mark.parametrize(
    "literal", ["192.168.1.10", "192.168.0.161", "`192.168.30.5`"]
)
def test_a_seeded_home_range_address_fails(tmp_path, literal):
    roles = _roles(tmp_path, f"# demo\n\nThe resolver is {literal}.\n")
    assert mod.main(["--roles-dir", str(roles), "--no-site-domains", "--no-site-addresses"]) == 1


def test_a_seeded_site_domain_fails(tmp_path):
    roles = _roles(tmp_path, "# demo\n\nCerts come from acme.esweiss.com.\n")
    assert mod.main([
        "--roles-dir", str(roles), "--site-domain", "esweiss.com",
        "--no-site-addresses",
    ]) == 1


def test_an_undeclared_domain_passes(tmp_path):
    """Only the domains the caller names are site data; nothing is guessed."""
    roles = _roles(tmp_path, "# demo\n\nCerts come from acme.esweiss.com.\n")
    assert mod.main(["--roles-dir", str(roles), "--no-site-domains", "--no-site-addresses"]) == 0


def test_a_longer_address_is_not_a_substring_match(tmp_path):
    roles = _roles(tmp_path, "# demo\n\nPrefix 2192.168.1.10.5 is not an address.\n")
    assert mod.main(["--roles-dir", str(roles), "--no-site-domains", "--no-site-addresses"]) == 0


def test_a_missing_roles_dir_is_an_operator_error(tmp_path):
    assert mod.main(["--roles-dir", str(tmp_path / "nope"), "--no-site-domains", "--no-site-addresses"]) == 2


def test_a_roles_dir_with_no_readme_is_an_operator_error(tmp_path):
    (tmp_path / "roles" / "demo").mkdir(parents=True)
    assert mod.main(["--roles-dir", str(tmp_path / "roles"), "--no-site-domains", "--no-site-addresses"]) == 2


def test_every_finding_names_its_role_and_line(tmp_path, capsys):
    roles = _roles(tmp_path, "# demo\n\nline two\nThe resolver is 192.168.1.10.\n")
    assert mod.main(["--roles-dir", str(roles), "--no-site-domains", "--no-site-addresses"]) == 1
    assert "demo/README.md:4: 192.168.1.10" in capsys.readouterr().out


def test_the_collections_own_readmes_pass():
    assert mod.main([
        "--roles-dir", str(ROLES),
        "--site-domain", "esweiss.com",
        "--site-domain", "ericsweiss.com",
        "--site-literal", r"(?<![\w.])10\.0\.(10|20)\.\d{1,3}(?!\.?\d)",
    ]) == 0


def test_no_domain_flag_at_all_is_an_operator_error(tmp_path, capsys):
    roles = _roles(tmp_path, "# demo\n\nNothing site-specific here.\n")
    assert mod.main(["--roles-dir", str(roles)]) == 2
    assert "--no-site-domains" in capsys.readouterr().err


def test_no_address_flag_at_all_is_an_operator_error(tmp_path, capsys):
    """A 10.x or 172.16.x site would otherwise pass an address arm that only
    covers 192.168/16, with nothing saying so."""
    roles = _roles(tmp_path, "# demo\n\nThe resolver is 10.0.10.150.\n")
    assert mod.main(["--roles-dir", str(roles), "--no-site-domains"]) == 2
    err = capsys.readouterr().err
    assert "--no-site-addresses" in err
    assert "192.168.x.y" in err


def test_the_address_opt_out_runs_the_builtin_arm_only(tmp_path, capsys):
    roles = _roles(tmp_path, "# demo\n\nThe resolver is 10.0.10.150.\n")
    assert mod.main([
        "--roles-dir", str(roles), "--no-site-domains", "--no-site-addresses",
    ]) == 0


def test_a_supplied_site_range_is_caught_and_named(tmp_path, capsys):
    pattern = r"(?<![\w.])10\.0\.(10|20)\.\d{1,3}(?!\.?\d)"
    roles = _roles(tmp_path, "# demo\n\nThe resolver is 10.0.10.150.\n")
    assert mod.main([
        "--roles-dir", str(roles), "--no-site-domains", "--site-literal", pattern,
    ]) == 1
    assert "10.0.10.150" in capsys.readouterr().out
    clean = _roles(tmp_path / "clean", "# demo\n\nUse `resolver.example.com`.\n")
    assert mod.main([
        "--roles-dir", str(clean), "--no-site-domains", "--site-literal", pattern,
    ]) == 0
    assert pattern in capsys.readouterr().out


def test_a_seeded_site_hostname_fails(tmp_path, capsys):
    roles = _roles(tmp_path, "# demo\n\n    proxmox_host: pve-nas-01\n")
    assert mod.main(["--roles-dir", str(roles), "--no-site-domains", "--no-site-addresses"]) == 1
    assert "pve-nas-01" in capsys.readouterr().out


def test_a_product_spelling_is_not_a_site_hostname(tmp_path):
    roles = _roles(tmp_path, "# demo\n\nRun `pve-firewall compile` afterwards.\n")
    assert mod.main(["--roles-dir", str(roles), "--no-site-domains", "--no-site-addresses"]) == 0


def test_an_extra_site_literal_regex_is_honoured(tmp_path, capsys):
    roles = _roles(tmp_path, "# demo\n\nThe box is called basement-nas.\n")
    assert mod.main([
        "--roles-dir", str(roles), "--no-site-domains",
        "--site-literal", r"\bbasement-[a-z]+\b",
    ]) == 1
    assert "basement-nas" in capsys.readouterr().out


def test_a_malformed_site_literal_regex_is_an_operator_error(tmp_path, capsys):
    roles = _roles(tmp_path, "# demo\n\nNothing site-specific here.\n")
    assert mod.main([
        "--roles-dir", str(roles), "--no-site-domains", "--site-literal", "(",
    ]) == 2
    assert "bad --site-literal regex" in capsys.readouterr().err


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a 0000 file anyway")
def test_an_unreadable_readme_is_an_operator_error(tmp_path, capsys):
    roles = _roles(tmp_path, "# demo\n\nNothing site-specific here.\n")
    (roles / "demo" / "README.md").chmod(0o000)
    try:
        assert mod.main([
            "--roles-dir", str(roles), "--no-site-domains", "--no-site-addresses",
        ]) == 2
    finally:
        (roles / "demo" / "README.md").chmod(0o644)
    assert "Traceback" not in capsys.readouterr().err


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
