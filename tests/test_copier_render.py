"""tests/copier_render.py — the shared copier render harness."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import copier_render  # noqa: E402


def _template(tmp_path: Path) -> Path:
    root = tmp_path / "template-repo"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "x.pyc").write_text("", encoding="utf-8")
    (root / "template").mkdir()
    (root / "template" / "README.md.jinja").write_text("{{ name }}\n", encoding="utf-8")
    (root / "copier.yml").write_text("name:\n  type: str\n", encoding="utf-8")
    return root


def test_the_working_tree_is_copied_without_git(tmp_path):
    """.git present would make copier render the last COMMIT, not the tree."""
    src = copier_render.copy_source(_template(tmp_path), tmp_path / "scratch")
    assert (src / "copier.yml").is_file()
    assert not (src / ".git").exists()
    assert not (src / "__pycache__").exists()


def test_an_extra_ignore_pattern_is_honoured(tmp_path):
    root = _template(tmp_path)
    (root / ".bin").mkdir()
    (root / ".bin" / "tool").write_text("", encoding="utf-8")
    src = copier_render.copy_source(root, tmp_path / "scratch", extra_ignore=(".bin",))
    assert not (src / ".bin").exists()


def test_the_copier_command_carries_the_answers_file(tmp_path):
    argv = copier_render.copier_argv(
        tmp_path / "src", tmp_path / "dest", tmp_path / "answers.yml"
    )
    assert argv[1:5] == ["-m", "copier", "copy", "--defaults"]
    assert "--data-file" in argv and str(tmp_path / "answers.yml") in argv
    assert argv[-2:] == [str(tmp_path / "src"), str(tmp_path / "dest")]


def test_data_overrides_become_data_flags(tmp_path):
    argv = copier_render.copier_argv(
        tmp_path / "src", tmp_path / "dest", tmp_path / "a.yml",
        data={"ci_shape": "github"},
    )
    assert "--data" in argv and "ci_shape=github" in argv


def test_render_invokes_copier_and_returns_the_destination(tmp_path):
    calls = []
    dest = copier_render.render(
        _template(tmp_path), tmp_path / "scratch", tmp_path / "answers.yml",
        runner=lambda argv, check: calls.append(argv),
    )
    assert dest == tmp_path / "scratch" / "render"
    assert calls and calls[0][2] == "copier"


def test_a_library_without_the_engine_fails_rather_than_skipping(tmp_path):
    problems = copier_render.check_registered_copies(tmp_path / "no-lib", tmp_path)
    assert problems and "must not silently skip" in problems[0]


def test_a_missing_manifest_fails_rather_than_skipping(tmp_path):
    lib = tmp_path / "lib" / "scripts"
    lib.mkdir(parents=True)
    (lib / "check-vendored-copies.py").write_text("", encoding="utf-8")
    problems = copier_render.check_registered_copies(tmp_path / "lib", tmp_path)
    assert problems and "must not silently skip" in problems[0]


def test_a_passing_engine_reports_no_problem(tmp_path):
    lib = tmp_path / "lib" / "scripts"
    lib.mkdir(parents=True)
    (lib / "check-vendored-copies.py").write_text(
        "import sys\nsys.exit(0)\n", encoding="utf-8"
    )
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "vendored-manifest.yml").write_text(
        "vendored: []\n", encoding="utf-8"
    )
    assert copier_render.check_registered_copies(tmp_path / "lib", tmp_path) == []


def test_a_failing_engine_is_reported(tmp_path):
    lib = tmp_path / "lib" / "scripts"
    lib.mkdir(parents=True)
    (lib / "check-vendored-copies.py").write_text(
        "import sys\nsys.exit(1)\n", encoding="utf-8"
    )
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "vendored-manifest.yml").write_text(
        "vendored: []\n", encoding="utf-8"
    )
    assert copier_render.check_registered_copies(tmp_path / "lib", tmp_path) == [
        "registered copies"
    ]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
