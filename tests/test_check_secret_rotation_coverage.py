#!/usr/bin/env python3
"""Unit tests for check-secret-rotation-coverage.py.

Each test builds a throwaway manifest tree plus a rotation document and asserts
the exit code, so an undocumented credential cannot pass as covered.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from script_loader import load_script  # noqa: E402

GATE = load_script("check-secret-rotation-coverage.py")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _write(
        tmp_path / "kubernetes" / "apps" / "alpha" / "externalsecret.yaml",
        """\
        apiVersion: external-secrets.io/v1
        kind: ExternalSecret
        metadata:
          name: alpha-secret
          namespace: alpha
        spec:
          data:
            - secretKey: token
              remoteRef:
                key: Alpha App
                property: token
        """,
    )
    _write(
        tmp_path / "docs" / "ROTATION.md",
        """\
        # Rotation

        | Secret | Item |
        |---|---|
        | alpha/alpha-secret | Alpha App |
        """,
    )
    return tmp_path


def _run(repo: Path, extra: list[str] | None = None) -> int:
    return GATE.main(
        ["--repo-root", str(repo), "--doc", "docs/ROTATION.md", *(extra or [])]
    )


def test_documented_secret_passes(repo: Path, capsys):
    assert _run(repo) == 0
    assert "Rotation coverage OK" in capsys.readouterr().out


def test_undocumented_external_secret_fails(repo: Path, capsys):
    _write(
        repo / "kubernetes" / "apps" / "beta" / "externalsecret.yaml",
        """\
        apiVersion: external-secrets.io/v1
        kind: ExternalSecret
        metadata:
          name: beta-secret
          namespace: beta
        spec:
          data:
            - secretKey: token
              remoteRef:
                key: Alpha App
        """,
    )
    assert _run(repo) == 1
    assert "beta/beta-secret" in capsys.readouterr().err


def test_undocumented_remote_ref_key_fails(repo: Path, capsys):
    text = (repo / "docs" / "ROTATION.md").read_text(encoding="utf-8")
    (repo / "docs" / "ROTATION.md").write_text(
        text.replace("Alpha App", "Gamma App"), encoding="utf-8"
    )
    assert _run(repo) == 1
    assert "'Alpha App'" in capsys.readouterr().err


def test_cluster_external_secret_is_covered_by_its_fanned_out_name(repo: Path, capsys):
    """The write-capable token a ClusterExternalSecret fans out needs a rotation
    path too: the gate keys it by spec.externalSecretName."""
    _write(
        repo / "kubernetes" / "infrastructure" / "configs" / "dns-token.yaml",
        """\
        apiVersion: external-secrets.io/v1
        kind: ClusterExternalSecret
        metadata:
          name: dns-token-ces
        spec:
          externalSecretName: dns-api-token
          externalSecretSpec:
            data:
              - secretKey: token
                remoteRef:
                  key: Alpha App
        """,
    )
    assert _run(repo) == 1
    err = capsys.readouterr().err
    assert "dns-api-token" in err

    doc = repo / "docs" / "ROTATION.md"
    doc.write_text(
        doc.read_text(encoding="utf-8") + "\n| dns-api-token | Alpha App |\n",
        encoding="utf-8",
    )
    assert _run(repo) == 0


def test_namespaceless_external_secret_is_reported(repo: Path, capsys):
    _write(
        repo / "kubernetes" / "components" / "shared" / "externalsecret.yaml",
        """\
        apiVersion: external-secrets.io/v1
        kind: ExternalSecret
        metadata:
          name: shared-secret
        spec:
          data:
            - secretKey: token
              remoteRef:
                key: Alpha App
        """,
    )
    assert _run(repo) == 1
    assert "declares no metadata.namespace" in capsys.readouterr().err


def test_declared_manual_covers_a_secret(repo: Path):
    _write(
        repo / "kubernetes" / "apps" / "beta" / "externalsecret.yaml",
        """\
        apiVersion: external-secrets.io/v1
        kind: ExternalSecret
        metadata:
          name: beta-secret
          namespace: beta
        spec:
          data:
            - secretKey: token
              remoteRef:
                key: Alpha App
        """,
    )
    assert _run(repo, ["--declared-manual", "beta/beta-secret=bootstrap only"]) == 0


def test_stale_declared_manual_fails(repo: Path, capsys):
    assert _run(repo, ["--declared-manual", "ghost/ghost-secret=gone"]) == 1
    assert "drop the stale entry" in capsys.readouterr().err


def test_declared_manual_without_a_reason_exits_2(repo: Path, capsys):
    assert _run(repo, ["--declared-manual", "beta/beta-secret"]) == 2
    assert "NAME=REASON" in capsys.readouterr().err


def test_empty_corpus_exits_2(tmp_path: Path, capsys):
    (tmp_path / "kubernetes").mkdir()
    _write(tmp_path / "docs" / "ROTATION.md", "# Rotation\n")
    assert _run(tmp_path) == 2
    assert "not a gate" in capsys.readouterr().err


def test_missing_manifest_dir_exits_2(repo: Path, capsys):
    assert _run(repo, ["--manifest-dir", "k8s"]) == 2
    assert "does not exist" in capsys.readouterr().err


def test_unreadable_doc_exits_2(repo: Path, capsys):
    (repo / "docs" / "ROTATION.md").unlink()
    assert _run(repo) == 2
    assert "could not be read" in capsys.readouterr().err


def test_longer_name_does_not_cover_a_shorter_one(repo: Path, capsys):
    """`alpha-secret-v2` in the document must not satisfy `alpha-secret`."""
    doc = repo / "docs" / "ROTATION.md"
    doc.write_text(
        doc.read_text(encoding="utf-8").replace(
            "alpha/alpha-secret", "alpha/alpha-secret-v2"
        ),
        encoding="utf-8",
    )
    assert _run(repo) == 1
    assert "alpha/alpha-secret is reached by no rotation path" in capsys.readouterr().err


def test_unparseable_manifest_is_reported(repo: Path, capsys):
    _write(repo / "kubernetes" / "apps" / "broken.yaml", "a: [unclosed\n")
    assert _run(repo) == 1
    assert "unparseable" in capsys.readouterr().err


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
