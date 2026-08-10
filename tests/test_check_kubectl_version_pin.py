"""Tests for scripts/check-kubectl-version-pin.py (the kubectl/k3s skew gate).

Exercises the version extraction, the +/-1 minor skew classification, the CLI
path resolution against throwaway files, and the argparse surface: the two
positional paths are optional (defaults apply), a bad argument is rejected
rather than treated as a filename, and an unreadable input exits 2 with a
one-line error instead of a traceback.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_SCRIPT = REPO / "scripts" / "check-kubectl-version-pin.py"

# Import the hyphenated-name module the same way test_check_doc_links.py does.
_spec = importlib.util.spec_from_file_location("check_kubectl_version_pin", _SCRIPT)
assert _spec and _spec.loader
ckp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ckp)


def _ci(major: int, minor: int) -> str:
    return f'    KUBECTL_URL="https://dl.k8s.io/release/v{major}.{minor}.4/bin/linux/amd64/kubectl"\n'


def _cm(major: int, minor: int) -> str:
    return f"data:\n  k3s_version: v{major}.{minor}.1+k3s1\n"


class TestCheck:
    def test_equal_minor_passes(self):
        code, msg = ckp.check(_ci(1, 33), _cm(1, 33))
        assert code == 0
        assert "within the supported" in msg

    def test_one_minor_below_passes(self):
        code, _ = ckp.check(_ci(1, 32), _cm(1, 33))
        assert code == 0

    def test_one_minor_above_passes(self):
        code, _ = ckp.check(_ci(1, 34), _cm(1, 33))
        assert code == 0

    def test_two_minor_skew_fails(self):
        code, msg = ckp.check(_ci(1, 31), _cm(1, 33))
        assert code == 1
        assert "outside Kubernetes' supported" in msg

    def test_major_mismatch_fails(self):
        code, msg = ckp.check(_ci(2, 33), _cm(1, 33))
        assert code == 1
        assert "outside Kubernetes' supported" in msg

    def test_missing_kubectl_pin_fails(self):
        code, msg = ckp.check("no pin here\n", _cm(1, 33))
        assert code == 1
        assert "kubectl pin" in msg

    def test_missing_k3s_version_fails(self):
        code, msg = ckp.check(_ci(1, 33), "data:\n  other: 1\n")
        assert code == 1
        assert "k3s_version" in msg


class TestCli:
    def test_explicit_paths_are_read(self, tmp_path, capsys):
        ci = tmp_path / "ci.yml"
        ci.write_text(_ci(1, 33))
        cm = tmp_path / "cm.yaml"
        cm.write_text(_cm(1, 33))
        assert ckp.main(["prog", str(ci), str(cm)]) == 0
        assert "within the supported" in capsys.readouterr().out

    def test_skew_exits_nonzero(self, tmp_path):
        ci = tmp_path / "ci.yml"
        ci.write_text(_ci(1, 30))
        cm = tmp_path / "cm.yaml"
        cm.write_text(_cm(1, 33))
        assert ckp.main(["prog", str(ci), str(cm)]) == 1

    def test_defaults_apply_when_paths_omitted(self, tmp_path, capsys, monkeypatch):
        """Both positionals are optional — with none given the module defaults
        (the conventional repo layout) are read."""
        ci = tmp_path / "ci.yml"
        ci.write_text(_ci(1, 33))
        cm = tmp_path / "cm.yaml"
        cm.write_text(_cm(1, 33))
        monkeypatch.setattr(ckp, "CI_YAML", ci)
        monkeypatch.setattr(ckp, "VERSIONS_CM", cm)
        assert ckp.main(["prog"]) == 0
        assert "within the supported" in capsys.readouterr().out

    def test_ci_path_only_uses_default_configmap(self, tmp_path, monkeypatch):
        ci = tmp_path / "ci.yml"
        ci.write_text(_ci(1, 33))
        cm = tmp_path / "cm.yaml"
        cm.write_text(_cm(1, 33))
        monkeypatch.setattr(ckp, "VERSIONS_CM", cm)
        assert ckp.main(["prog", str(ci)]) == 0


class TestCliErrors:
    """Bad input is reported on one line with a non-zero exit, never a
    traceback, and a flag-shaped argument is not taken for a filename."""

    def test_missing_file_exits_two_without_traceback(self, tmp_path, capsys):
        missing = tmp_path / "nope.yml"
        cm = tmp_path / "cm.yaml"
        cm.write_text(_cm(1, 33))
        assert ckp.main(["prog", str(missing), str(cm)]) == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.count("\n") == 1
        assert captured.err.startswith("ERROR: could not read input file:")
        assert str(missing) in captured.err

    def test_missing_configmap_exits_two(self, tmp_path, capsys):
        ci = tmp_path / "ci.yml"
        ci.write_text(_ci(1, 33))
        assert ckp.main(["prog", str(ci), str(tmp_path / "nope.yaml")]) == 2
        assert "ERROR: could not read input file:" in capsys.readouterr().err

    def test_directory_argument_exits_two(self, tmp_path, capsys):
        cm = tmp_path / "cm.yaml"
        cm.write_text(_cm(1, 33))
        assert ckp.main(["prog", str(tmp_path), str(cm)]) == 2
        assert "ERROR: could not read input file:" in capsys.readouterr().err

    def test_undecodable_file_exits_two(self, tmp_path, capsys):
        ci = tmp_path / "ci.yml"
        ci.write_bytes(b"\xff\xfe\x00binary")
        cm = tmp_path / "cm.yaml"
        cm.write_text(_cm(1, 33))
        assert ckp.main(["prog", str(ci), str(cm)]) == 2
        assert "ERROR: could not read input file:" in capsys.readouterr().err

    def test_unknown_flag_is_rejected(self):
        """Previously any argv[1] was treated as a filename; argparse now
        rejects a flag-shaped argument (SystemExit 2 from the parser)."""
        with pytest.raises(SystemExit) as exc:
            ckp.main(["prog", "--bogus"])
        assert exc.value.code == 2

    def test_extra_positional_is_rejected(self):
        """A third path was silently ignored before argparse."""
        with pytest.raises(SystemExit) as exc:
            ckp.main(["prog", "a.yml", "b.yaml", "c.yaml"])
        assert exc.value.code == 2

    def test_help_exits_zero(self, capsys):
        with pytest.raises(SystemExit) as exc:
            ckp.main(["prog", "--help"])
        assert exc.value.code == 0
        assert "ci_yaml" in capsys.readouterr().out
