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


def test_a_server_named_by_ip_passes_when_the_cert_carries_an_ip_san():
    """The TLS-option arm must stay on for a consumer that mounts by IP."""
    corpus = GOOD.replace("nas.example.test", "10.0.10.102")
    proc = _run(corpus, "--allow-ip-server")
    assert proc.returncode == 0, proc.stderr
    proc = _run(corpus.replace("    - xprtsec=tls\n", ""), "--allow-ip-server")
    assert proc.returncode == 1
    assert "lack xprtsec=tls" in proc.stderr


def test_an_empty_server_fails():
    proc = _run(GOOD.replace("server: nas.example.test", 'server: ""'))
    assert proc.returncode == 1
    assert "spec.nfs.server is empty" in proc.stderr


def test_a_custom_required_option_is_honoured():
    assert _run(GOOD, "--required-option", "sec=krb5p").returncode == 1


def test_an_empty_corpus_is_an_operator_error():
    assert _run("").returncode == 2


def test_a_corpus_with_no_nfs_volume_is_an_operator_error():
    corpus = "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: x\n"
    assert _run(corpus).returncode == 2


def test_allow_empty_turns_that_into_a_pass():
    corpus = "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: x\n"
    assert _run(corpus, "--allow-empty").returncode == 0


def test_unparseable_input_is_an_operator_error():
    assert _run("kind: [unbalanced\n").returncode == 2


LIST_WRAPPED = """
apiVersion: v1
kind: List
items:
  - apiVersion: v1
    kind: PersistentVolume
    metadata:
      name: media
    spec:
      mountOptions:
        - nfsvers=4.2
      nfs:
        server: nas.example.test
        path: /export/media
"""


def test_a_volume_inside_a_kind_list_is_inspected():
    proc = _run(LIST_WRAPPED)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "xprtsec=tls" in proc.stderr


def test_a_compliant_volume_inside_a_kind_list_counts_as_inspected():
    proc = _run(LIST_WRAPPED.replace("        - nfsvers=4.2",
                                     "        - nfsvers=4.2\n        - xprtsec=tls"))
    assert proc.returncode == 0, proc.stderr
    assert "1 NFS PersistentVolume(s)" in proc.stdout


def test_the_gate_exits_two_without_gate_common(tmp_path):
    import os
    import shutil

    copy = tmp_path / "check-nfs-tls.py"
    shutil.copy(SCRIPT, copy)
    proc = subprocess.run(
        [sys.executable, str(copy)],
        input=GOOD, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": ""},
    )
    assert proc.returncode == 2
    assert "gate_common.py" in proc.stderr


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
