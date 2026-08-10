"""Tests for the `new-cluster` copier wrapper.

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

from weisssrv_lib_cli import cluster
from weisssrv_lib_cli.cli import main

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
        assert cluster.parse_data(["a=1", "b=x=y"]) == {"a": "1", "b": "x=y"}

    def test_empty_value_allowed(self):
        assert cluster.parse_data(["a="]) == {"a": ""}

    @pytest.mark.parametrize("bad", ["nope", "=1", " =1"])
    def test_rejects_malformed(self, bad):
        with pytest.raises(cluster.ClusterError):
            cluster.parse_data([bad])


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
        assert cluster.is_vcs_source(url)
        assert cluster.resolve_source(url, "v1.2.3") == url

    def test_local_template_resolves_absolute(self, tmp_path):
        assert cluster.resolve_source(str(TEMPLATE)) == str(TEMPLATE.resolve())

    def test_missing_directory_rejected(self, tmp_path):
        with pytest.raises(cluster.ClusterError, match="neither a directory"):
            cluster.resolve_source(str(tmp_path / "nope"))

    def test_directory_without_copier_yml_rejected(self, tmp_path):
        with pytest.raises(cluster.ClusterError, match="no copier.yml"):
            cluster.resolve_source(str(tmp_path))

    def test_vcs_ref_on_non_git_local_path_rejected(self):
        with pytest.raises(cluster.ClusterError, match="--vcs-ref needs a git checkout"):
            cluster.resolve_source(str(TEMPLATE), "v1")


class TestDestinationGuards:
    def test_non_empty_destination_rejected(self, tmp_path):
        (tmp_path / "keep.txt").write_text("x", encoding="utf-8")
        with pytest.raises(cluster.ClusterError, match="not empty"):
            cluster.render(str(TEMPLATE), tmp_path, defaults=True)

    def test_file_destination_rejected(self, tmp_path):
        dest = tmp_path / "afile"
        dest.write_text("x", encoding="utf-8")
        with pytest.raises(cluster.ClusterError, match="not a directory"):
            cluster.render(str(TEMPLATE), dest, defaults=True)

    def test_missing_copier_reports_the_extra(self, tmp_path, monkeypatch):
        # `None` in sys.modules makes `import copier` raise ImportError.
        monkeypatch.setitem(sys.modules, "copier", None)
        with pytest.raises(cluster.ClusterError, match=r"\[cluster\]"):
            cluster.render(str(TEMPLATE), tmp_path / "out", defaults=True)


@copier_required
class TestRender:
    def test_renders_with_defaults(self, tmp_path):
        dest = cluster.render(str(TEMPLATE), tmp_path / "out", defaults=True)
        assert (dest / "README.md").read_text() == "# demo-cluster\n\nBase domain: example.test\n"
        rendered = yaml.safe_load((dest / "clusters" / "demo-cluster" / "cluster.yaml").read_text())
        assert rendered == {"name": "demo-cluster", "domain": "example.test"}

    def test_data_overrides_answers(self, tmp_path):
        dest = cluster.render(
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
        cluster.render(str(TEMPLATE), dest, defaults=True, pretend=True)
        assert not dest.exists() or not any(dest.iterdir())

    def test_vcs_ref_selects_the_tagged_revision(self, tmp_path):
        repo = _git_template(tmp_path)
        old = cluster.render(str(repo), tmp_path / "v1out", vcs_ref="v1", defaults=True)
        assert old.joinpath("README.md").read_text().startswith("# demo-cluster\n")
        new = cluster.render(str(repo), tmp_path / "v2out", vcs_ref="v2", defaults=True)
        assert new.joinpath("README.md").read_text() == "# demo-cluster (v2)\n"
        # No ref on a git source = copier's default, the LATEST TAG (not HEAD).
        latest = cluster.render(str(repo), tmp_path / "outdefault", defaults=True)
        assert latest.joinpath("README.md").read_text() == "# demo-cluster (v2)\n"

    def test_render_error_is_wrapped(self, tmp_path):
        bad = tmp_path / "bad-template"
        bad.mkdir()
        (bad / "copier.yml").write_text("_min_copier_version: '999.0.0'\n", encoding="utf-8")
        with pytest.raises(cluster.RenderError):
            cluster.render(str(bad), tmp_path / "out", defaults=True)


class TestNewClusterCommand:
    def test_help_names_the_published_template(self, capsys):
        with pytest.raises(SystemExit):
            main(["new-cluster", "--help"])
        assert cluster.CLUSTER_TEMPLATE_URL in capsys.readouterr().out

    def test_bad_data_returns_2(self, tmp_path, capsys):
        rc = main(["new-cluster", str(TEMPLATE), str(tmp_path / "out"), "--data", "nope"])
        assert rc == 2
        assert "KEY=VALUE" in capsys.readouterr().err

    @copier_required
    def test_end_to_end(self, tmp_path, capsys):
        dest = tmp_path / "out"
        rc = main(
            [
                "new-cluster",
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
