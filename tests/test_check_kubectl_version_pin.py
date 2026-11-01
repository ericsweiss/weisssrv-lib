"""scripts/check-kubectl-version-pin.py flags kubectl/k3s minor-version skew."""
from __future__ import annotations

import pytest

from script_loader import load_script

ckp = load_script("check-kubectl-version-pin.py")


def _ci(major: int, minor: int) -> str:
    return f'    KUBECTL_URL="https://dl.k8s.io/release/v{major}.{minor}.4/bin/linux/amd64/kubectl"\n'


def _cm(major: int, minor: int) -> str:
    return f"data:\n  k3s_version: v{major}.{minor}.1+k3s1\n"


class TestCheck:
    @pytest.mark.parametrize("ci_minor", [33, 32, 34], ids=["equal", "one-below", "one-above"])
    def test_within_one_minor_passes(self, ci_minor):
        code, msg = ckp.check(_ci(1, ci_minor), _cm(1, 33))
        assert code == 0
        assert "within the supported" in msg

    @pytest.mark.parametrize("major,minor", [(1, 31), (2, 33)], ids=["two-minor", "major"])
    def test_skew_fails(self, major, minor):
        code, msg = ckp.check(_ci(major, minor), _cm(1, 33))
        assert code == 1
        assert "outside Kubernetes' supported" in msg

    def test_missing_kubectl_pin_is_an_operator_error(self, tmp_path):
        """No pin in the file given is a wrong path, not skew: exit 2, path named."""
        ci = tmp_path / "workflow.yml"
        code, msg = ckp.check("no pin here\n", _cm(1, 33), ci, tmp_path / "cm.yaml")
        assert code == 2
        assert str(ci) in msg

    def test_missing_k3s_version_is_an_operator_error(self, tmp_path):
        cm = tmp_path / "cm.yaml"
        code, msg = ckp.check(_ci(1, 33), "data:\n  other: 1\n", tmp_path / "ci.yml", cm)
        assert code == 2
        assert str(cm) in msg

    def test_skew_message_names_the_ci_file_it_read(self, tmp_path):
        ci = tmp_path / "workflow.yml"
        code, msg = ckp.check(_ci(1, 31), _cm(1, 33), ci, tmp_path / "cm.yaml")
        assert code == 1
        assert str(ci) in msg


class TestCli:
    def test_explicit_paths_are_read(self, tmp_path, capsys):
        ci = tmp_path / "ci.yml"
        ci.write_text(_ci(1, 33))
        cm = tmp_path / "cm.yaml"
        cm.write_text(_cm(1, 33))
        assert ckp.main(["prog", str(ci), str(cm)]) == 0
        assert "within the supported" in capsys.readouterr().out

    def test_main_names_the_paths_it_was_given(self, tmp_path, capsys):
        ci = tmp_path / "workflow.yml"
        ci.write_text("nothing to see\n")
        cm = tmp_path / "cm.yaml"
        cm.write_text(_cm(1, 33))
        assert ckp.main(["prog", str(ci), str(cm)]) == 2
        assert str(ci) in capsys.readouterr().out

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
    """An unreadable input is an operator error: exit 2 on one stderr line."""

    @pytest.mark.parametrize("which", ["missing-ci", "missing-configmap", "directory", "undecodable"])
    def test_unreadable_input_exits_two_on_one_line(self, tmp_path, capsys, which):
        ci = tmp_path / "ci.yml"
        ci.write_text(_ci(1, 33))
        cm = tmp_path / "cm.yaml"
        cm.write_text(_cm(1, 33))
        bad = tmp_path / "nope.yml"
        args = {
            "missing-ci": [bad, cm],
            "missing-configmap": [ci, bad],
            "directory": [tmp_path, cm],
            "undecodable": [ci, cm],
        }[which]
        if which == "undecodable":
            ci.write_bytes(b"\xff\xfe\x00binary")
        assert ckp.main(["prog", *map(str, args)]) == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.count("\n") == 1
        assert captured.err.startswith("ERROR: could not read input file:")
        if which.startswith("missing"):
            assert str(bad) in captured.err


class TestFormat:
    def test_the_extraction_is_format_agnostic(self):
        """The pin is regexed out of text, so an Actions workflow parses fine."""
        workflow = (
            "jobs:\n  lint:\n    steps:\n"
            "      - run: curl -sSLO https://dl.k8s.io/release/v1.33.4/bin/linux/amd64/kubectl\n"
        )
        code, msg = ckp.check(workflow, _cm(1, 33))
        assert code == 0
        assert "within the supported" in msg


def _ci_include_input(major: int, minor: int) -> str:
    """A consumer that includes ci/deploy/kubectl-setup.yml carries no URL."""
    return (
        "include:\n"
        "  - project: eric/weisssrv-lib\n"
        "    file: ci/deploy/kubectl-setup.yml\n"
        "    inputs:\n"
        f'      kubectl_version: "v{major}.{minor}.2"\n'
    )


class TestIncludeInputForm:
    def test_the_include_input_pin_is_found(self):
        code, msg = ckp.check(_ci_include_input(1, 35), _cm(1, 35))
        assert code == 0
        assert "within the supported" in msg

    def test_a_two_minor_skew_in_the_include_form_still_fails(self):
        """Without this the new branch could pass vacuously."""
        code, msg = ckp.check(_ci_include_input(1, 37), _cm(1, 35))
        assert code == 1
        assert "outside Kubernetes' supported" in msg

    def test_both_forms_are_evaluated_when_both_are_present(self):
        """A second pin in another job must not ride in behind the first."""
        both = _ci(1, 35) + _ci_include_input(1, 37)
        code, msg = ckp.check(both, _cm(1, 35))
        assert code == 1
        assert "v1.37" in msg

    def test_two_in_skew_pins_both_pass(self):
        code, msg = ckp.check(_ci(1, 35) + _ci_include_input(1, 34), _cm(1, 35))
        assert code == 0
        assert "v1.34.x" in msg and "v1.35.x" in msg

    def test_a_second_download_pin_is_checked_too(self):
        code, msg = ckp.check(_ci(1, 35) + _ci(1, 30), _cm(1, 35))
        assert code == 1
        assert "v1.30" in msg

    def test_neither_form_is_an_operator_error(self):
        code, msg = ckp.check("jobs: {}\n", _cm(1, 35))
        assert code == 2
        assert "kubectl_version: input" in msg
