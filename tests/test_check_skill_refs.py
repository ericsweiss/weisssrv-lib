"""Coverage for check-skill-refs.py.

Each arm runs against a fixture repo: broken pointers exit 1, vacuous scans
exit 2, and only --foreign paths stay unchecked.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from script_loader import SCRIPTS

SCRIPT = SCRIPTS / "check-skill-refs.py"
SKILL = Path(".claude/skills/demo-development")
FLOOR = 40


def run(repo_root: Path, *extra: str, skill_dir: Path = SKILL) -> subprocess.CompletedProcess:
    cmd = [
        sys.executable, str(SCRIPT), "--repo-root", str(repo_root),
        "--skill-dir", str(skill_dir), *extra,
    ]
    return subprocess.run(cmd, check=False, capture_output=True, text=True)


def make_tree(tmp_path: Path, skill_text: str) -> Path:
    """A miniature repo: one docs file, one script and a skill citing them."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "RUNBOOKS.md").write_text("# Runbooks\n")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "check-lib-pins.py").write_text("#\n")
    skill = tmp_path / SKILL
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text(skill_text)
    return skill


def body(*paths: str) -> str:
    """A skill page citing each path, padded past the vacuity floor."""
    lines = [f"- see `{p}`" for p in paths]
    lines += [f"- see `docs/RUNBOOKS.md` ({i})" for i in range(FLOOR)]
    return "\n".join(lines) + "\n"


def test_a_resolving_citation_passes(tmp_path):
    make_tree(tmp_path, body("scripts/check-lib-pins.py"))
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "41 cited paths resolve" in result.stdout


def test_a_renamed_file_fails(tmp_path):
    make_tree(tmp_path, body("scripts/check-lib-pins-renamed.py"))
    result = run(tmp_path)
    assert result.returncode == 1
    assert "check-lib-pins-renamed.py" in result.stderr


def test_an_absolute_skill_dir_is_accepted(tmp_path):
    skill = make_tree(tmp_path, body("scripts/check-lib-pins.py"))
    assert run(tmp_path, skill_dir=skill).returncode == 0


@pytest.mark.parametrize(
    ("page", "token", "target"),
    [
        ("SKILL.md", "references/debugging.md", "references/debugging.md"),
        ("references/a.md", "references/b.md", "references/b.md"),
        ("references/a.md", "extra/x.md", "references/extra/x.md"),
    ],
    ids=["skill-to-reference", "reference-to-reference", "page-sibling"],
)
def test_a_skill_relative_citation_is_checked(tmp_path, page, token, target):
    """`references/` resolves against the skill dir, any other segment against the page dir."""
    skill = make_tree(tmp_path, body())
    (skill / page).write_text(f"see `{token}`\n" + body())
    (skill / target).parent.mkdir(parents=True, exist_ok=True)
    result = run(tmp_path)
    assert result.returncode == 1
    assert token in result.stderr
    (skill / target).write_text("# page\n")
    assert run(tmp_path).returncode == 0


def test_a_broken_repo_citation_on_a_references_page_fails(tmp_path):
    """Pages under references/ are scanned, not only SKILL.md."""
    skill = make_tree(tmp_path, body())
    (skill / "references" / "a.md").write_text("see `scripts/gone.py`\n")
    result = run(tmp_path)
    assert result.returncode == 1
    assert "scripts/gone.py" in result.stderr


def test_a_multi_suffix_markdown_page_is_scanned(tmp_path):
    skill = make_tree(tmp_path, body())
    (skill / "references" / "page.md.tmpl").write_text("see `scripts/gone.py`\n")
    result = run(tmp_path)
    assert result.returncode == 1
    assert "references/page.md.tmpl: scripts/gone.py" in result.stderr


SHORTHAND = ("--shorthand-root", "ansible/inventories/prod")


def test_a_shorthand_citation_is_checked_under_a_declared_root(tmp_path):
    make_tree(tmp_path, body("group_vars/all.yml", "group_vars/dns.yml"))
    group_vars = tmp_path / "ansible" / "inventories" / "prod" / "group_vars"
    group_vars.mkdir(parents=True)
    (group_vars / "all.yml").write_text("---\n")
    result = run(tmp_path, *SHORTHAND)
    assert result.returncode == 1
    assert "group_vars/dns.yml" in result.stderr
    assert "group_vars/all.yml" not in result.stderr


@pytest.mark.parametrize("token", ["group_vars/all.yml", "scirpts/check-lib-pins.py"])
def test_a_citation_no_base_resolves_fails(tmp_path, token):
    """An undeclared shorthand root, a typo or a deleted directory all fail."""
    make_tree(tmp_path, body(token))
    result = run(tmp_path)
    assert result.returncode == 1
    assert token in result.stderr


def test_a_shorthand_root_that_is_not_a_directory_exits_two(tmp_path):
    make_tree(tmp_path, body("scripts/check-lib-pins.py"))
    result = run(tmp_path, *SHORTHAND)
    assert result.returncode == 2
    assert "--shorthand-root" in result.stderr


@pytest.mark.parametrize("token", ["docs/<topic>.md", "scripts/check-*.py", "hosts.yml"])
def test_a_placeholder_glob_or_basename_is_not_a_token(tmp_path, token):
    make_tree(tmp_path, body(token))
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "40 cited paths resolve" in result.stdout


def test_a_hujson_citation_is_checked(tmp_path):
    make_tree(tmp_path, body("scripts/policy.hujson"))
    result = run(tmp_path)
    assert result.returncode == 1
    assert "scripts/policy.hujson" in result.stderr
    (tmp_path / "scripts" / "policy.hujson").write_text("{}\n")
    assert run(tmp_path).returncode == 0


def test_a_foreign_path_is_not_checked(tmp_path):
    make_tree(tmp_path, body("docs/INCLUDE-CONTRACT.md"))
    assert run(tmp_path).returncode == 1
    assert run(tmp_path, "--foreign", "docs/INCLUDE-CONTRACT.md").returncode == 0


def test_a_scan_that_matches_nothing_exits_two(tmp_path):
    make_tree(tmp_path, "No backticked paths here at all.\n")
    result = run(tmp_path)
    assert result.returncode == 2
    assert "not seeing" in result.stderr


def test_the_floor_is_an_argument(tmp_path):
    make_tree(tmp_path, body())
    assert run(tmp_path, "--min-tokens", str(FLOOR + 1)).returncode == 2
    assert run(tmp_path, "--min-tokens", str(FLOOR)).returncode == 0


def test_a_skill_dir_without_markdown_exits_two(tmp_path):
    assert run(tmp_path, skill_dir=tmp_path / "nowhere").returncode == 2
    (tmp_path / SKILL).mkdir(parents=True)
    (tmp_path / SKILL / "notes.txt").write_text("`docs/RUNBOOKS.md`\n")
    assert run(tmp_path).returncode == 2


def test_an_undecodable_skill_page_exits_two(tmp_path):
    skill = make_tree(tmp_path, body("scripts/check-lib-pins.py"))
    (skill / "references" / "legacy.md").write_bytes("café `docs/RUNBOOKS.md`\n".encode("latin-1"))
    result = run(tmp_path)
    assert result.returncode == 2
    assert "legacy.md" in result.stderr
    assert "Traceback" not in result.stderr
