"""scripts/flux-env.sh — the multi-ConfigMap substitution wrapper.

Functional coverage runs the real script against the real sibling
flux-render.sh; FLUX_EXTRA_CONFIGMAPS is pinned to "" so fixtures control the
whole input list.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "flux-env.sh"


def write_cm(path: Path, data: dict[str, str]) -> Path:
    path.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": path.stem},
                "data": data,
            }
        )
    )
    return path


def run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=cwd,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "FLUX_EXTRA_CONFIGMAPS": ""},
        capture_output=True,
        text=True,
    )


def test_bash_syntax_is_valid() -> None:
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not on PATH")
def test_shellcheck_clean() -> None:
    subprocess.run(
        ["shellcheck", "--severity=warning", "--exclude=SC1091", str(SCRIPT)], check=True
    )


def test_export_versions_merges_files_with_later_file_winning(tmp_path: Path) -> None:
    a = write_cm(tmp_path / "a.yaml", {"foo_version": "1", "shared": "old"})
    b = write_cm(tmp_path / "b.yaml", {"bar_vip": "10.0.0.1", "shared": "new"})
    proc = run(["export-versions", f"{a.name} {b.name}"], cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "export foo_version=1" in proc.stdout
    # Both files' lines are emitted; precedence is eval order, so b's must come last.
    assert proc.stdout.index("export shared=old") < proc.stdout.index("export shared=new")
    allowlists = [line for line in proc.stdout.splitlines() if "FLUX_ENVSUBST_VARS" in line]
    assert len(allowlists) == 1, "one merged allowlist, not one per file"
    assert "${foo_version}" in allowlists[0] and "${bar_vip}" in allowlists[0]


def test_a_file_named_twice_is_read_once(tmp_path: Path) -> None:
    a = write_cm(tmp_path / "a.yaml", {"foo_version": "1"})
    proc = run(["export-versions", f"{a.name} {a.name}"], cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.count("export foo_version=") == 1


def test_merged_configmap_unions_data_sorted(tmp_path: Path) -> None:
    a = write_cm(tmp_path / "a.yaml", {"zz": "1", "shared": "old"})
    b = write_cm(tmp_path / "b.yaml", {"aa": "2", "shared": "new"})
    proc = run(["merged-configmap", f"{a.name} {b.name}"], cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    doc = yaml.safe_load(proc.stdout)
    assert doc["data"] == {"aa": "2", "zz": "1", "shared": "new"}


def test_a_missing_configmap_is_a_loud_error(tmp_path: Path) -> None:
    proc = run(["merged-configmap", "absent.yaml"], cwd=tmp_path)
    assert proc.returncode != 0
    assert "ConfigMap not found" in proc.stderr


def test_an_unknown_subcommand_dies_with_the_menu(tmp_path: Path) -> None:
    proc = run(["frobnicate"], cwd=tmp_path)
    assert proc.returncode != 0
    assert "unknown subcommand" in proc.stderr
