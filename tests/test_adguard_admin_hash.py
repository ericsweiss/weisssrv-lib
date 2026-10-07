"""Behavioural tests for adguard_home/files/adguard-admin-hash.py.

Covers what molecule cannot reach: several users, a duplicate name, a missing
password key, a multi-line scalar, the post-write verify. bcrypt is passlib or a stub.
"""

from __future__ import annotations

import os
import sys
import types

import pytest
import yaml

from script_loader import REPO, load_path

SCRIPT = (
    REPO
    / "ansible_collections"
    / "weisssrv"
    / "infra"
    / "roles"
    / "adguard_home"
    / "files"
    / "adguard-admin-hash.py"
)


def _load_script():
    return load_path(SCRIPT)


class _StubBcrypt:
    """Deterministic stand-in for passlib.hash.bcrypt (`$2b$` prefixed)."""

    @staticmethod
    def hash(password):
        return "$2b$stub$" + password

    @staticmethod
    def verify(password, hashed):
        if not hashed.startswith("$2b$stub$"):
            raise ValueError("malformed stub hash")
        return hashed == "$2b$stub$" + password

    @classmethod
    def using(cls, **_kwargs):
        return cls


def _install_stub_passlib():
    passlib = types.ModuleType("passlib")
    hash_module = types.ModuleType("passlib.hash")
    hash_module.bcrypt = _StubBcrypt
    passlib.hash = hash_module
    sys.modules.setdefault("passlib", passlib)
    sys.modules.setdefault("passlib.hash", hash_module)
    return _StubBcrypt


@pytest.fixture(scope="module")
def bcrypt():
    try:
        from passlib.hash import bcrypt as real

        real.using(rounds=4).hash("probe")
    except Exception:
        return _install_stub_passlib()
    return real


@pytest.fixture
def script():
    return _load_script()


def write_config(tmp_path, users, extra=""):
    lines = ["bind_host: 0.0.0.0\n", "users:\n"]
    for name, password in users:
        lines.append(f"  - name: {name}\n")
        if password is not None:
            lines.append(f"    password: {password}\n")
    text = "".join(lines) + extra
    path = tmp_path / "AdGuardHome.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def call(script, config, user, action, password=None, monkeypatch=None):
    """Run the script's main() in-process; returns the SystemExit code or 0."""
    argv = ["adguard-admin-hash.py", "--config", str(config), "--user", user, action]
    monkeypatch.setattr(sys, "argv", argv)
    if password is not None:
        monkeypatch.setattr("sys.stdin", _Stdin(password + "\n"))
    try:
        script.main()
    except SystemExit as exc:
        return exc.code or 0
    return 0


class _Stdin:
    def __init__(self, text):
        self._text = text

    def read(self):
        return self._text


def test_read_targets_the_named_user_not_the_last_one(script, tmp_path, monkeypatch, capsys):
    config = write_config(tmp_path, [("admin", "$2b$aaa"), ("other", "$2b$zzz")])
    assert call(script, config, "admin", "read", monkeypatch=monkeypatch) == 0
    assert capsys.readouterr().out.strip() == "$2b$aaa"


def test_reconcile_rewrites_the_named_user_and_leaves_the_others_alone(
    script, tmp_path, monkeypatch, capsys, bcrypt
):
    config = write_config(tmp_path, [("admin", "$2b$stale"), ("other", "$2b$keepme")])
    assert call(script, config, "admin", "reconcile", "secret", monkeypatch) == 0
    assert capsys.readouterr().out.strip() == "CHANGED"

    parsed = yaml.safe_load(config.read_text(encoding="utf-8"))
    users = {u["name"]: u["password"] for u in parsed["users"]}
    assert bcrypt.verify("secret", users["admin"])
    assert users["other"] == "$2b$keepme"
    assert parsed["bind_host"] == "0.0.0.0"


def test_reconcile_is_idempotent_when_the_stored_hash_verifies(
    script, tmp_path, monkeypatch, capsys, bcrypt
):
    config = write_config(tmp_path, [("admin", bcrypt.using(rounds=4).hash("secret"))])
    before = config.read_bytes()
    assert call(script, config, "admin", "reconcile", "secret", monkeypatch) == 0
    assert capsys.readouterr().out.strip() == "UNCHANGED"
    assert config.read_bytes() == before


def test_reconcile_preserves_mode_and_ownership(script, tmp_path, monkeypatch, bcrypt):
    config = write_config(tmp_path, [("admin", "$2b$stale")])
    os.chmod(config, 0o600)
    before = os.stat(config)
    assert call(script, config, "admin", "reconcile", "secret", monkeypatch) == 0
    after = os.stat(config)
    assert (after.st_mode, after.st_uid, after.st_gid) == (
        before.st_mode,
        before.st_uid,
        before.st_gid,
    )


def test_duplicate_user_names_fail_without_writing(
    script, tmp_path, monkeypatch, capsys, bcrypt
):
    config = write_config(tmp_path, [("admin", "$2b$one"), ("admin", "$2b$two")])
    before = config.read_bytes()
    assert call(script, config, "admin", "reconcile", "secret", monkeypatch) == 1
    assert "2 users named" in capsys.readouterr().err
    assert config.read_bytes() == before


def test_unknown_user_fails(script, tmp_path, monkeypatch):
    config = write_config(tmp_path, [("admin", "$2b$one")])
    assert call(script, config, "nobody", "read", monkeypatch=monkeypatch) == 1


def test_missing_users_key_fails(script, tmp_path, monkeypatch):
    config = tmp_path / "AdGuardHome.yaml"
    config.write_text("bind_host: 0.0.0.0\n", encoding="utf-8")
    assert call(script, config, "admin", "read", monkeypatch=monkeypatch) == 1


def test_user_without_password_key_reads_empty_and_refuses_reconcile(
    script, tmp_path, monkeypatch, capsys, bcrypt
):
    config = write_config(tmp_path, [("admin", None)])
    assert call(script, config, "admin", "read", monkeypatch=monkeypatch) == 0
    assert capsys.readouterr().out.strip() == ""

    before = config.read_bytes()
    assert call(script, config, "admin", "reconcile", "secret", monkeypatch) == 1
    assert config.read_bytes() == before


def test_multi_line_password_scalar_is_refused(script, tmp_path, monkeypatch, bcrypt):
    config = tmp_path / "AdGuardHome.yaml"
    config.write_text(
        "users:\n  - name: admin\n    password: >-\n      $2b$sta\n      le\n",
        encoding="utf-8",
    )
    before = config.read_bytes()
    assert call(script, config, "admin", "reconcile", "secret", monkeypatch) == 1
    assert config.read_bytes() == before


def test_post_write_verification_failure_leaves_the_config_untouched(
    script, tmp_path, monkeypatch, bcrypt
):
    config = write_config(tmp_path, [("admin", "$2b$stale")])
    before = config.read_bytes()
    monkeypatch.setattr(script.yaml, "safe_load", lambda handle: {"users": []})
    assert call(script, config, "admin", "reconcile", "secret", monkeypatch) == 1
    assert config.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["AdGuardHome.yaml"]


def test_empty_password_on_stdin_fails(script, tmp_path, monkeypatch):
    config = write_config(tmp_path, [("admin", "$2b$stale")])
    before = config.read_bytes()
    assert call(script, config, "admin", "reconcile", "", monkeypatch) == 1
    assert config.read_bytes() == before


def test_missing_config_fails(script, tmp_path, monkeypatch):
    assert call(script, tmp_path / "absent.yaml", "admin", "read", monkeypatch=monkeypatch) == 1
