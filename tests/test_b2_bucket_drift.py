"""Tests for scripts/b2-bucket-drift.py (diff + config loading, no network)."""
from __future__ import annotations

import json
import pathlib

import pytest

from script_loader import load_script

mod = load_script("b2-bucket-drift.py")


EXAMPLE_CONFIG = (
    pathlib.Path(__file__).resolve().parent.parent / "examples" / "b2-bucket.example.json"
)
DESIRED = json.loads(EXAMPLE_CONFIG.read_text())["desired"]


def clean_bucket() -> dict:
    return {
        "bucketName": "REPLACE-WITH-BUCKET-NAME",
        "bucketType": "allPrivate",
        "defaultServerSideEncryption": {
            "isClientAuthorizedToRead": True,
            "value": {"mode": "SSE-B2", "algorithm": "AES256"},
        },
        "lifecycleRules": [
            {
                "fileNamePrefix": "",
                "daysFromHidingToDeleting": 30,
                "daysFromUploadingToHiding": None,
            }
        ],
        "fileLockConfiguration": {
            "isClientAuthorizedToRead": True,
            "value": {
                "isFileLockEnabled": False,
                "defaultRetention": {"mode": None, "period": None},
            },
        },
    }


class TestDiffBucket:
    def test_clean_bucket_has_no_drift(self):
        assert mod.diff_bucket(clean_bucket(), DESIRED) == []

    def test_wrong_bucket_type_drifts(self):
        b = clean_bucket()
        b["bucketType"] = "allPublic"
        assert any("bucketType" in d for d in mod.diff_bucket(b, DESIRED))

    def test_missing_sse_drifts(self):
        b = clean_bucket()
        b["defaultServerSideEncryption"] = {
            "isClientAuthorizedToRead": True,
            "value": {"mode": None, "algorithm": None},
        }
        assert any("SSE" in d for d in mod.diff_bucket(b, DESIRED))

    def test_missing_lifecycle_rule_drifts(self):
        b = clean_bucket()
        b["lifecycleRules"] = []
        assert any("lifecycleRules" in d for d in mod.diff_bucket(b, DESIRED))

    def test_extra_lifecycle_rule_drifts(self):
        b = clean_bucket()
        b["lifecycleRules"].append(
            {"fileNamePrefix": "restic/", "daysFromUploadingToHiding": 1}
        )
        assert any("lifecycleRules" in d for d in mod.diff_bucket(b, DESIRED))

    def test_default_retention_set_drifts(self):
        b = clean_bucket()
        b["fileLockConfiguration"]["value"]["defaultRetention"] = {
            "mode": "governance",
            "period": {"duration": 7, "unit": "days"},
        }
        assert any("defaultRetention" in d for d in mod.diff_bucket(b, DESIRED))

    def test_unreadable_sections_surface_as_drift(self):
        # An unreadable section must NAME the capability gap, not read as
        # "no drift".
        b = clean_bucket()
        b["fileLockConfiguration"] = {"isClientAuthorizedToRead": False, "value": None}
        b["defaultServerSideEncryption"] = {
            "isClientAuthorizedToRead": False,
            "value": None,
        }
        drift = mod.diff_bucket(b, DESIRED)
        assert any("fileLock" in d and "capabilities" in d for d in drift)
        assert any("SSE" in d and "capabilities" in d for d in drift)


class TestLoadConfig:
    """The bucket identity is consumer data; a malformed config must fail loudly
    rather than silently checking the wrong (or no) bucket."""

    @staticmethod
    def _write(tmp_path: pathlib.Path, payload) -> pathlib.Path:
        p = tmp_path / "b2.json"
        p.write_text(json.dumps(payload))
        return p

    def test_example_config_is_loadable(self):
        cfg = mod.load_config(EXAMPLE_CONFIG)
        assert cfg["desired"]["bucketType"] == "allPrivate"

    def test_missing_top_level_key_raises(self, tmp_path):
        with pytest.raises(ValueError):
            mod.load_config(self._write(tmp_path, {"bucket_id": "x"}))

    def test_missing_desired_key_raises(self, tmp_path):
        payload = json.loads(EXAMPLE_CONFIG.read_text())
        del payload["desired"]["lifecycleRules"]
        with pytest.raises(ValueError):
            mod.load_config(self._write(tmp_path, payload))

    def test_non_object_raises(self, tmp_path):
        with pytest.raises(ValueError):
            mod.load_config(self._write(tmp_path, ["not", "an", "object"]))


class TestSupervisedApply:
    """The apply path is the destructive one: it must carry the revision it read
    and refuse to write over a concurrent change."""

    @staticmethod
    def _drifted_bucket() -> dict:
        bucket = clean_bucket()
        bucket["bucketType"] = "allPublic"
        bucket["revision"] = 7
        return bucket

    def _patch(self, monkeypatch, tmp_path, calls, update_result):
        config = tmp_path / "b2.json"
        config.write_text(EXAMPLE_CONFIG.read_text())

        def fake_api(url, token=None, body=None, basic=None):
            calls.append((url, body))
            if url.endswith("b2_authorize_account"):
                return {
                    "apiInfo": {"storageApi": {"apiUrl": "https://api.invalid"}},
                    "authorizationToken": "tok",
                }
            if url.endswith("b2_list_buckets"):
                return {"buckets": [self._bucket_now()]}
            return update_result()

        monkeypatch.setattr(mod, "_api", fake_api)
        monkeypatch.setenv("B2_APPLICATION_KEY_ID", "id")
        monkeypatch.setenv("B2_APPLICATION_KEY", "key")
        monkeypatch.setattr(mod.sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda _prompt: "yes")
        return config

    def test_apply_sends_the_revision_it_read(self, monkeypatch, tmp_path):
        calls: list = []
        self._bucket_now = lambda: (
            self._drifted_bucket() if len(calls) < 3 else clean_bucket()
        )
        config = self._patch(monkeypatch, tmp_path, calls, lambda: {})
        assert mod.main(["--config", str(config), "--apply"]) == 0
        update = [body for url, body in calls if url.endswith("b2_update_bucket")]
        assert update and update[0]["ifRevisionIs"] == 7

    def test_a_concurrent_change_aborts_instead_of_clobbering(
        self, monkeypatch, tmp_path, capsys
    ):
        calls: list = []
        self._bucket_now = self._drifted_bucket

        def conflict():
            raise mod.urllib.error.HTTPError(
                "https://api.invalid", 409, "conflict", {}, None
            )

        config = self._patch(monkeypatch, tmp_path, calls, conflict)
        assert mod.main(["--config", str(config), "--apply"]) == 2
        assert "changed since it was read" in capsys.readouterr().out

    def test_a_non_tty_refuses_to_apply(self, monkeypatch, tmp_path, capsys):
        """An unattended run must never mutate the offsite bucket."""
        calls: list = []
        self._bucket_now = self._drifted_bucket
        config = self._patch(monkeypatch, tmp_path, calls, lambda: {})
        monkeypatch.setattr(mod.sys.stdin, "isatty", lambda: False)
        assert mod.main(["--config", str(config), "--apply"]) == 2
        assert "requires an interactive terminal" in capsys.readouterr().out
        assert [b for u, b in calls if u.endswith("b2_update_bucket")] == []

    def test_a_declined_confirmation_aborts(self, monkeypatch, tmp_path, capsys):
        calls: list = []
        self._bucket_now = self._drifted_bucket
        config = self._patch(monkeypatch, tmp_path, calls, lambda: {})
        monkeypatch.setattr("builtins.input", lambda _prompt: "no")
        assert mod.main(["--config", str(config), "--apply"]) == 1
        assert "ABORTED" in capsys.readouterr().out
        assert [b for u, b in calls if u.endswith("b2_update_bucket")] == []

    def test_a_bucket_named_something_else_is_refused(self, monkeypatch, tmp_path, capsys):
        """Wrong bucket id in the config: the gate must not reconcile a stranger."""
        calls: list = []

        def other():
            bucket = self._drifted_bucket()
            bucket["bucketName"] = "someone-elses-bucket"
            return bucket

        self._bucket_now = other
        config = self._patch(monkeypatch, tmp_path, calls, lambda: {})
        assert mod.main(["--config", str(config), "--apply"]) == 2
        assert "refusing to touch it" in capsys.readouterr().out
        assert [b for u, b in calls if u.endswith("b2_update_bucket")] == []

    def test_drift_without_apply_reports_and_changes_nothing(
        self, monkeypatch, tmp_path, capsys
    ):
        calls: list = []
        self._bucket_now = self._drifted_bucket
        config = self._patch(monkeypatch, tmp_path, calls, lambda: {})
        assert mod.main(["--config", str(config)]) == 1
        assert "Re-run with --apply" in capsys.readouterr().out
        assert [b for u, b in calls if u.endswith("b2_update_bucket")] == []

    def test_an_apply_the_api_ignored_is_not_success(self, monkeypatch, tmp_path, capsys):
        """B2 can accept a write and change nothing; the re-read is what catches it."""
        calls: list = []
        self._bucket_now = self._drifted_bucket
        config = self._patch(monkeypatch, tmp_path, calls, lambda: {})
        assert mod.main(["--config", str(config), "--apply"]) == 1
        out = capsys.readouterr().out
        assert "drift remains after apply" in out
        assert "bucketType" in out

    def test_a_clean_bucket_is_exit_zero(self, monkeypatch, tmp_path, capsys):
        calls: list = []
        self._bucket_now = clean_bucket
        config = self._patch(monkeypatch, tmp_path, calls, lambda: {})
        assert mod.main(["--config", str(config)]) == 0
        assert "OK:" in capsys.readouterr().out

    def test_apply_without_a_revision_omits_the_field(self):
        cfg = json.loads(EXAMPLE_CONFIG.read_text())
        sent: list = []
        original = mod._api
        try:
            mod._api = lambda url, token=None, body=None, basic=None: sent.append(body)
            mod.apply_bucket("https://api.invalid", "tok", cfg)
        finally:
            mod._api = original
        assert "ifRevisionIs" not in sent[0]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
