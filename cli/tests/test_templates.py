"""Tests for the `new-cluster` / `new-app` copier wrapper.

Rendering runs against tests/fixtures/copier-template (a miniature local copier
template) so the suite stays offline. Everything that does not need copier
installed is tested unconditionally.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from weisssrv_lib_cli import __version__, templates
from weisssrv_lib_cli.cli import build_parser, main

TEMPLATE = Path(__file__).resolve().parent / "fixtures" / "copier-template"

copier_required = pytest.mark.skipif(
    importlib.util.find_spec("copier") is None,
    reason="copier not installed (pip install 'weisssrv-lib-cli[cluster]')",
)


def _git_template(tmp_path: Path) -> Path:
    """The fixture template as a git repo with two tagged revisions, v1 and v2."""
    repo = tmp_path / "template-repo"
    shutil.copytree(TEMPLATE, repo)
    env = dict(os.environ)
    env.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.test",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.test",
    )

    def run(*argv: str) -> None:
        subprocess.run(argv, cwd=repo, check=True, env=env, capture_output=True)

    run("git", "init", "-q", "-b", "main")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "v1")
    run("git", "tag", "-a", "v1", "-m", "v1")
    (repo / "README.md.jinja").write_text("# {{ cluster_name }} (v2)\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "v2")
    run("git", "tag", "-a", "v2", "-m", "v2")
    return repo


class TestParseData:
    def test_pairs(self):
        assert templates.parse_data(["a=1", "b=x=y"]) == {"a": "1", "b": "x=y"}

    def test_empty_value_allowed(self):
        assert templates.parse_data(["a="]) == {"a": ""}

    @pytest.mark.parametrize("bad", ["nope", "=1", " =1"])
    def test_rejects_malformed(self, bad):
        with pytest.raises(templates.TemplateError):
            templates.parse_data([bad])


class TestSourceResolution:
    @pytest.mark.parametrize(
        "url",
        [
            "https://git.ericsweiss.com/eric/weisssrv-cluster-template.git",
            "git@git.ericsweiss.com:eric/weisssrv-cluster-template.git",
            "gh:ericsweiss/weisssrv-cluster-template",
            "file:///srv/templates/cluster",
        ],
    )
    def test_vcs_sources_pass_through(self, url):
        assert templates.is_vcs_source(url)
        assert templates.resolve_source(url, "v1.2.3") == url

    def test_local_template_resolves_absolute(self, tmp_path):
        assert templates.resolve_source(str(TEMPLATE)) == str(TEMPLATE.resolve())

    def test_missing_directory_rejected(self, tmp_path):
        with pytest.raises(templates.TemplateError, match="neither a directory"):
            templates.resolve_source(str(tmp_path / "nope"))

    def test_directory_without_copier_yml_rejected(self, tmp_path):
        with pytest.raises(templates.TemplateError, match="no copier.yml"):
            templates.resolve_source(str(tmp_path))

    def test_vcs_ref_on_non_git_local_path_rejected(self):
        with pytest.raises(templates.TemplateError, match="--vcs-ref needs a git checkout"):
            templates.resolve_source(str(TEMPLATE), "v1")


class TestDestinationGuards:
    def test_non_empty_destination_rejected(self, tmp_path):
        (tmp_path / "keep.txt").write_text("x", encoding="utf-8")
        with pytest.raises(templates.TemplateError, match="not empty"):
            templates.render(str(TEMPLATE), tmp_path, defaults=True)

    def test_file_destination_rejected(self, tmp_path):
        dest = tmp_path / "afile"
        dest.write_text("x", encoding="utf-8")
        with pytest.raises(templates.TemplateError, match="not a directory"):
            templates.render(str(TEMPLATE), dest, defaults=True)

    def test_missing_copier_reports_the_extra(self, tmp_path, monkeypatch):
        # `None` in sys.modules makes `import copier` raise ImportError.
        monkeypatch.setitem(sys.modules, "copier", None)
        with pytest.raises(templates.MissingCopierError, match=r"\[cluster\]"):
            templates.render(str(TEMPLATE), tmp_path / "out", defaults=True)


class _FakeCopier:
    """Records the kwargs `run_copy` is called with; renders nothing."""

    class errors:  # noqa: N801 - mirrors copier.errors
        class CopierError(Exception):
            pass

    def __init__(self):
        self.kwargs = None

    def run_copy(self, src, dest, **kwargs):
        self.kwargs = kwargs


class TestCopierKwargs:
    """`--trust` is the one flag that lets a template execute arbitrary code."""

    @pytest.fixture
    def fake(self, monkeypatch):
        fake = _FakeCopier()
        monkeypatch.setattr(templates, "_copier", lambda: fake)
        return fake

    def test_unsafe_is_off_by_default(self, fake, tmp_path):
        templates.render(str(TEMPLATE), tmp_path / "out", defaults=True)
        assert fake.kwargs["unsafe"] is False

    def test_trust_maps_to_unsafe(self, fake, tmp_path):
        templates.render(str(TEMPLATE), tmp_path / "out", defaults=True, trust=True)
        assert fake.kwargs["unsafe"] is True

    def test_cli_does_not_trust_without_the_flag(self, fake, tmp_path):
        assert main(["new-cluster", str(TEMPLATE), str(tmp_path / "out"), "--defaults"]) == 0
        assert fake.kwargs["unsafe"] is False

    def test_cli_trust_flag_reaches_copier(self, fake, tmp_path):
        rc = main(
            ["new-cluster", str(TEMPLATE), str(tmp_path / "out"), "--defaults", "--trust"]
        )
        assert rc == 0
        assert fake.kwargs["unsafe"] is True


@copier_required
class TestRender:
    def test_renders_with_defaults(self, tmp_path):
        dest = templates.render(str(TEMPLATE), tmp_path / "out", defaults=True)
        assert (dest / "README.md").read_text() == "# demo-cluster\n\nBase domain: example.test\n"
        rendered = yaml.safe_load((dest / "clusters" / "demo-cluster" / "cluster.yaml").read_text())
        assert rendered == {"name": "demo-cluster", "domain": "example.test"}

    def test_data_overrides_answers(self, tmp_path):
        dest = templates.render(
            str(TEMPLATE),
            tmp_path / "out",
            data={"cluster_name": "weisssrv", "base_domain": "esweiss.com"},
            defaults=True,
        )
        assert (dest / "clusters" / "weisssrv" / "cluster.yaml").exists()
        answers = yaml.safe_load((dest / ".copier-answers.yml").read_text())
        assert answers["cluster_name"] == "weisssrv"
        assert answers["base_domain"] == "esweiss.com"

    def test_pretend_writes_nothing(self, tmp_path):
        dest = tmp_path / "out"
        templates.render(str(TEMPLATE), dest, defaults=True, pretend=True)
        assert not dest.exists() or not any(dest.iterdir())

    def test_vcs_ref_selects_the_tagged_revision(self, tmp_path):
        repo = _git_template(tmp_path)
        old = templates.render(str(repo), tmp_path / "v1out", vcs_ref="v1", defaults=True)
        assert old.joinpath("README.md").read_text().startswith("# demo-cluster\n")
        new = templates.render(str(repo), tmp_path / "v2out", vcs_ref="v2", defaults=True)
        assert new.joinpath("README.md").read_text() == "# demo-cluster (v2)\n"
        # No ref on a git source = copier's default, the LATEST TAG (not HEAD).
        latest = templates.render(str(repo), tmp_path / "outdefault", defaults=True)
        assert latest.joinpath("README.md").read_text() == "# demo-cluster (v2)\n"

    def test_render_error_is_wrapped(self, tmp_path):
        bad = tmp_path / "bad-template"
        bad.mkdir()
        (bad / "copier.yml").write_text("_min_copier_version: '999.0.0'\n", encoding="utf-8")
        with pytest.raises(templates.RenderError):
            templates.render(str(bad), tmp_path / "out", defaults=True)


_PUBLISHED = {
    "new-cluster": templates.CLUSTER_TEMPLATE_URL,
    "new-app": templates.APP_TEMPLATE_URL,
}


class TestRenderCommands:
    """`new-cluster` and `new-app` are the same wrapper over two templates."""

    def test_version_reports_the_package_version(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["--version"])
        assert exc.value.code == 0
        assert __version__ in capsys.readouterr().out

    @pytest.mark.parametrize("command", sorted(_PUBLISHED))
    def test_help_names_its_own_published_template(self, command, capsys):
        with pytest.raises(SystemExit):
            main([command, "--help"])
        out = capsys.readouterr().out
        assert _PUBLISHED[command] in out
        # Each subcommand names ONE template; crossing them would send an
        # operator at the wrong repo.
        other = next(url for name, url in _PUBLISHED.items() if name != command)
        assert other not in out

    @pytest.mark.parametrize("command", sorted(_PUBLISHED))
    def test_bad_data_returns_2(self, command, tmp_path, capsys):
        rc = main([command, str(TEMPLATE), str(tmp_path / "out"), "--data", "nope"])
        assert rc == 2
        assert "KEY=VALUE" in capsys.readouterr().err

    @pytest.mark.parametrize("command", sorted(_PUBLISHED))
    def test_a_missing_source_names_that_commands_template(self, command, tmp_path, capsys):
        rc = main([command, str(tmp_path / "nope"), str(tmp_path / "out")])
        assert rc == 2
        assert _PUBLISHED[command] in capsys.readouterr().err

    @pytest.mark.parametrize("command", sorted(_PUBLISHED))
    def test_source_defaults_to_that_commands_published_template(self, command, tmp_path):
        args = build_parser().parse_args([command, str(tmp_path / "out")])
        assert args.source == _PUBLISHED[command]
        assert args.destination == tmp_path / "out"

    @pytest.mark.parametrize("command", sorted(_PUBLISHED))
    def test_missing_copier_returns_3(self, command, tmp_path, capsys, monkeypatch):
        monkeypatch.setitem(sys.modules, "copier", None)
        rc = main([command, str(TEMPLATE), str(tmp_path / "out"), "--defaults"])
        # Not 2: an uninstalled extra is an environment problem, not a typo.
        assert rc == 3
        assert "[cluster]" in capsys.readouterr().err

    @copier_required
    @pytest.mark.parametrize("command", sorted(_PUBLISHED))
    def test_a_render_failure_returns_1(self, command, tmp_path, capsys):
        bad = tmp_path / "bad-template"
        bad.mkdir()
        (bad / "copier.yml").write_text("_min_copier_version: '999.0.0'\n", encoding="utf-8")
        rc = main([command, str(bad), str(tmp_path / "out"), "--defaults"])
        assert rc == 1
        assert "copier failed" in capsys.readouterr().err

    @copier_required
    @pytest.mark.parametrize("command", sorted(_PUBLISHED))
    def test_end_to_end(self, command, tmp_path, capsys):
        dest = tmp_path / "out"
        rc = main(
            [
                command,
                str(TEMPLATE),
                str(dest),
                "--data",
                "cluster_name=lab",
                "--defaults",
            ]
        )
        assert rc == 0
        out, _err = capsys.readouterr()
        assert "rendered" in out
        assert (dest / "clusters" / "lab" / "cluster.yaml").exists()
