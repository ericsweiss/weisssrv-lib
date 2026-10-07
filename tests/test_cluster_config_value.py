"""scripts/cluster-config-value.sh — a value is read, an absent key is loud."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "cluster-config-value.sh"
# The script shells out to `python3`: resolve the interpreter running pytest
# (which has pyyaml) ahead of a system python, with the HOME its user site
# packages live under.
ENV = {
    "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin:/usr/sbin:/sbin",
    **{k: v for k, v in os.environ.items() if k in ("HOME", "PYTHONUSERBASE")},
}

CONFIG = """---
apiVersion: v1
kind: ConfigMap
metadata:
  name: cluster-config
data:
  cluster_internal_domain: internal.example.test
  cluster_api_vip: "10.0.10.161"
  cluster_metallb_public_vip: 10.0.10.100
"""


def _run(tmp_path: Path, *keys: str, config: str = CONFIG):
    path = tmp_path / "cluster-config.yaml"
    path.write_text(config, encoding="utf-8")
    return subprocess.run(
        ["bash", str(SCRIPT), *keys],
        env={**ENV, "CLUSTER_CONFIG": str(path)},
        capture_output=True, text=True,
    )


def test_a_quoted_value_loses_its_quotes(tmp_path):
    assert _run(tmp_path, "cluster_api_vip").stdout.strip() == "10.0.10.161"


def test_an_unquoted_value_reads_the_same(tmp_path):
    assert _run(tmp_path, "cluster_metallb_public_vip").stdout.strip() == "10.0.10.100"


def test_several_keys_print_space_separated(tmp_path):
    proc = _run(tmp_path, "cluster_api_vip", "cluster_internal_domain")
    assert proc.stdout.strip() == "10.0.10.161 internal.example.test"


def test_an_absent_key_fails_rather_than_printing_nothing(tmp_path):
    proc = _run(tmp_path, "cluster_nope")
    assert proc.returncode == 1
    assert "is not set" in proc.stderr


def test_a_metadata_key_is_not_a_data_value(tmp_path):
    """Only `data:` scalars are values; `metadata.name` is not one."""
    proc = _run(tmp_path, "name")
    assert proc.returncode == 1
    assert "is not set" in proc.stderr


def test_a_key_in_both_sections_reads_the_data_value(tmp_path):
    config = CONFIG.replace("  name: cluster-config\n",
                            "  name: cluster-config\n  cluster_api_vip: metadata\n")
    assert _run(tmp_path, "cluster_api_vip", config=config).stdout.strip() == "10.0.10.161"


def test_no_key_at_all_is_a_usage_error(tmp_path):
    assert _run(tmp_path).returncode == 2


def test_a_missing_config_file_is_an_operator_error(tmp_path):
    proc = subprocess.run(
        ["bash", str(SCRIPT), "cluster_api_vip"],
        env={**ENV, "CLUSTER_CONFIG": str(tmp_path / "absent.yaml")},
        capture_output=True, text=True,
    )
    assert proc.returncode == 2
    assert "not found" in proc.stderr


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
