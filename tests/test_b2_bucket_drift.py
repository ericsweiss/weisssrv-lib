"""Tests for scripts/b2-bucket-drift.py (diff + config loading, no network)."""
from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "b2_bucket_drift", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "b2-bucket-drift.py"
)
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)


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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
