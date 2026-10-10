"""Every corpus gate that imports gate_common fails in contract without it.

A consumer that vendors the gate but not gate_common.py must get the gates' own
operator-error exit 2, not a ModuleNotFoundError traceback exiting 1.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"

IMPORTERS = (
    "check-default-deny-coverage.py",
    "check-ephemeral-storage-cap.py",
    "check-guest-endpoint-parity.py",
    "check-helmrelease-crd-safety.py",
    "check-hpa-vpa-invariant.py",
    "check-ingressroute-backends.py",
    "check-issuer-refs.py",
    "check-live-cpu-limits.py",
    "check-netpol-except-parity.py",
    "check-nfs-tls.py",
    "check-pvc-storageclass.py",
    "check-scrape-netpol.py",
    "check-secretstore-scope.py",
)

# Every shipped script, not just check-*.py: a differently named gate that
# imports gate_common must not escape the contract below.
DERIVED = tuple(sorted(
    path.name for path in SCRIPTS.iterdir()
    if path.suffix in (".py", ".sh")
    and path.name != "gate_common.py"
    and "gate_common" in path.read_text(encoding="utf-8")
))


def test_the_importer_list_is_complete():
    assert DERIVED == IMPORTERS


@pytest.mark.parametrize("name", IMPORTERS)
def test_the_gate_exits_two_when_gate_common_is_not_vendored(name, tmp_path):
    copy = tmp_path / name
    shutil.copy(SCRIPTS / name, copy)
    proc = subprocess.run(
        [sys.executable, str(copy)],
        input="", capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": ""},
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "gate_common.py" in proc.stderr


@pytest.mark.parametrize("name", IMPORTERS)
def test_the_gate_imports_gate_common_when_it_is_vendored(name, tmp_path):
    copy = tmp_path / name
    shutil.copy(SCRIPTS / name, copy)
    shutil.copy(SCRIPTS / "gate_common.py", tmp_path / "gate_common.py")
    proc = subprocess.run(
        [sys.executable, str(copy)],
        input="", capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": ""},
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "ModuleNotFoundError" not in proc.stderr
    assert "gate_common.py must be vendored" not in proc.stderr
