"""Tests for scripts/check-role-defaults-documented.py, the role-README gate.

Proves the gate catches an undocumented default, tolerates the legitimate
shapes, and separates a finding (1) from a broken run (2).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from script_loader import load_script

crdd = load_script("check-role-defaults-documented.py")
SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "check-role-defaults-documented.py"
)


def role(root: Path, name: str, defaults: str, readme: str) -> Path:
    path = root / name
    (path / "defaults").mkdir(parents=True)
    (path / "defaults" / "main.yml").write_text(defaults)
    (path / "README.md").write_text(readme)
    return path


def run(roles_dir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--roles-dir", str(roles_dir), *args],
        capture_output=True,
        text=True,
    )


def test_a_documented_default_passes(tmp_path):
    role(tmp_path, "alpha", "alpha_flag: true\n", "# alpha\n\n| `alpha_flag` | x |\n")
    proc = run(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_an_undocumented_default_fails(tmp_path):
    role(tmp_path, "alpha", "alpha_flag: true\nalpha_port: 80\n", "# alpha\n\n`alpha_flag`\n")
    proc = run(tmp_path)
    assert proc.returncode == 1
    assert "alpha_port" in proc.stdout
    assert "alpha_flag" not in proc.stdout


def test_table_only_reports_a_key_named_only_in_prose(tmp_path):
    """A default dropped from the table but left in a note reads as documented."""
    role(tmp_path, "alpha", "alpha_flag: true\n", "# alpha\n\nSet `alpha_flag`.\n")
    proc = run(tmp_path, "--table-only")
    assert proc.returncode == 1
    assert "alpha_flag" in proc.stdout


def test_table_only_accepts_a_variables_table_row(tmp_path):
    role(tmp_path, "alpha", "alpha_flag: true\n",
         "# alpha\n\n| Variable | Default |\n|---|---|\n| `alpha_flag` | true |\n")
    proc = run(tmp_path, "--table-only")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_prose_passes_without_table_only(tmp_path):
    """The default standard is a mention anywhere in the README."""
    role(tmp_path, "alpha", "alpha_flag: true\n", "# alpha\n\nSet `alpha_flag`.\n")
    assert run(tmp_path).returncode == 0


def test_a_role_without_defaults_is_not_a_finding(tmp_path):
    (tmp_path / "beta").mkdir()
    (tmp_path / "beta" / "README.md").write_text("# beta\n")
    role(tmp_path, "alpha", "alpha_flag: true\n", "# alpha\n`alpha_flag`\n")
    assert run(tmp_path).returncode == 0


def test_an_empty_defaults_file_is_not_a_finding(tmp_path):
    role(tmp_path, "alpha", "---\n", "# alpha\n")
    role(tmp_path, "beta", "beta_flag: true\n", "# beta\n`beta_flag`\n")
    proc = run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "OK: 1 role(s)" in proc.stdout


def test_a_roles_dir_whose_defaults_are_all_comment_only_is_an_operator_error(tmp_path):
    """Nothing was compared, so a clean run would certify an unread tree."""
    role(tmp_path, "alpha", "# only a comment\n", "# alpha\n")
    proc = run(tmp_path)
    assert proc.returncode == 2
    assert "with any variable in it" in proc.stderr


def test_a_defaults_file_that_is_not_a_mapping_is_an_operator_error(tmp_path):
    """A top-level list reads as zero keys, which would pass the role clean."""
    role(tmp_path, "alpha", "- alpha_flag\n", "# alpha\n")
    proc = run(tmp_path)
    assert proc.returncode == 2
    assert "not a mapping" in proc.stderr


def test_defaults_without_a_readme_is_an_operator_error(tmp_path):
    path = tmp_path / "alpha" / "defaults"
    path.mkdir(parents=True)
    (path / "main.yml").write_text("alpha_flag: true\n")
    proc = run(tmp_path)
    assert proc.returncode == 2
    assert "no README.md" in proc.stderr


def test_unparseable_defaults_is_an_operator_error(tmp_path):
    role(tmp_path, "alpha", "a: [\n", "# alpha\n")
    proc = run(tmp_path)
    assert proc.returncode == 2


def test_a_missing_roles_dir_is_an_operator_error(tmp_path):
    proc = run(tmp_path / "absent")
    assert proc.returncode == 2
    assert "roles dir not found" in proc.stderr


def test_an_empty_roles_dir_is_an_operator_error(tmp_path):
    proc = run(tmp_path)
    assert proc.returncode == 2
    assert "no role under" in proc.stderr


def test_a_roles_dir_whose_roles_have_no_defaults_is_an_operator_error(tmp_path):
    """Counting directories rather than defaults files passed a wrong --roles-dir."""
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    proc = run(tmp_path)
    assert proc.returncode == 2
    assert "declares a defaults/main.yml" in proc.stderr
    assert str(tmp_path) in proc.stderr


def test_the_count_names_only_the_roles_whose_defaults_were_read(tmp_path):
    role(tmp_path, "alpha", "alpha_flag: true\n", "# alpha\n`alpha_flag`\n")
    (tmp_path / "beta").mkdir()
    proc = run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "OK: 1 role(s)" in proc.stdout


def test_declared_keys_reads_only_top_level_keys(tmp_path):
    defaults = tmp_path / "main.yml"
    defaults.write_text("a: 1\nb:\n  nested: 2\n")
    assert crdd.declared_keys(defaults) == ["a", "b"]


def test_the_collection_documents_every_default_it_ships():
    """The real tree, so the gate cannot be green only against fixtures."""
    proc = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_a_longer_documented_name_does_not_cover_a_shorter_default(tmp_path):
    role(
        tmp_path,
        "alpha",
        "alpha_source: a\nalpha_sources: [b]\n",
        "# alpha\n\n| `alpha_sources` | x |\n",
    )
    proc = run(tmp_path)
    assert proc.returncode == 1
    assert "alpha_source," in proc.stdout or "alpha_source\n" in proc.stdout
