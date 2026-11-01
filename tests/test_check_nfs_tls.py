"""scripts/check-nfs-tls.py — the TLS and hostname arms, and the vacuity guard."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "check-nfs-tls.py"

GOOD = """
apiVersion: v1
kind: PersistentVolume
metadata:
  name: media
spec:
  mountOptions:
    - nfsvers=4.2
    - xprtsec=tls
  nfs:
    server: nas.example.test
    path: /export/media
"""


def _run(corpus: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        input=corpus, capture_output=True, text=True,
    )


def test_a_tls_hostname_volume_passes():
    proc = _run(GOOD)
    assert proc.returncode == 0, proc.stderr
    assert "1 NFS PersistentVolume(s)" in proc.stdout


def test_a_plaintext_volume_fails():
    proc = _run(GOOD.replace("    - xprtsec=tls\n", ""))
    assert proc.returncode == 1
    assert "lack xprtsec=tls" in proc.stderr


def test_a_server_named_by_ip_fails():
    proc = _run(GOOD.replace("nas.example.test", "10.0.10.102"), "--cert-domain",
                "*.example.test")
    assert proc.returncode == 1
    assert "has no IP SAN" in proc.stderr
    assert "*.example.test certificate" in proc.stderr


def test_an_empty_server_fails():
    proc = _run(GOOD.replace("server: nas.example.test", 'server: ""'))
    assert proc.returncode == 1
    assert "spec.nfs.server is empty" in proc.stderr


def test_a_corpus_with_no_nfs_volume_is_an_operator_error():
    corpus = "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: x\n"
    assert _run(corpus).returncode == 2


def test_allow_empty_turns_that_into_a_pass():
    corpus = "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: x\n"
    assert _run(corpus, "--allow-empty").returncode == 0


COMMA_JOINED = GOOD.replace(
    "    - nfsvers=4.2\n    - xprtsec=tls\n", "    - nfsvers=4.2,xprtsec=tls\n"
)


def test_a_comma_joined_mount_option_element_passes():
    """Kubernetes comma-joins mountOptions, so one element may carry both."""
    proc = _run(COMMA_JOINED)
    assert proc.returncode == 0, proc.stderr
    assert "1 NFS PersistentVolume(s)" in proc.stdout


def test_a_comma_joined_element_without_the_option_still_fails():
    proc = _run(COMMA_JOINED.replace(",xprtsec=tls", ",sec=sys"))
    assert proc.returncode == 1
    assert "lack xprtsec=tls" in proc.stderr


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
