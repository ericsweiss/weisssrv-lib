"""scripts/check-helm-repo-parity.py — both copies are held to the CRs."""
from __future__ import annotations

from pathlib import Path

import pytest

from script_loader import load_script

gate = load_script("check-helm-repo-parity.py")

SOURCE = """---
apiVersion: source.toolkit.fluxcd.io/v1
kind: HelmRepository
metadata:
  name: traefik
  namespace: flux-system
spec:
  url: https://traefik.github.io/charts
"""

RELEASES = """---
releases:
  - name: traefik
    manifest: kubernetes/infrastructure/controllers/traefik/release.yaml
    chart: traefik
    repo_name: traefik
    repo_url: https://traefik.github.io/charts
"""

REGISTRY = '''CONFIG = {
    "services": [
        {"name": "traefik", "helm_repo": "https://traefik.github.io/charts"},
    ],
}
'''


def _tree(tmp_path: Path, source=SOURCE, releases=RELEASES, registry=REGISTRY):
    sources = tmp_path / "sources"
    sources.mkdir(exist_ok=True)
    (sources / "traefik.yaml").write_text(source, encoding="utf-8")
    args = ["--sources-dir", str(sources)]
    if releases is not None:
        (tmp_path / "releases.yaml").write_text(releases, encoding="utf-8")
        args += ["--releases", str(tmp_path / "releases.yaml")]
    else:
        args += ["--releases", str(tmp_path / "absent.yaml")]
    if registry is not None:
        (tmp_path / "registry.py").write_text(registry, encoding="utf-8")
        args += ["--registry", str(tmp_path / "registry.py")]
    else:
        args += ["--registry", str(tmp_path / "absent.py")]
    return args


def test_three_agreeing_copies_pass(tmp_path):
    assert gate.main(_tree(tmp_path)) == 0


def test_a_release_url_that_drifted_fails(tmp_path, capsys):
    releases = RELEASES.replace("traefik.github.io/charts", "example.test/charts")
    assert gate.main(_tree(tmp_path, releases=releases)) == 1
    assert "pulls from" in capsys.readouterr().err


def test_a_release_naming_no_helmrepository_fails(tmp_path, capsys):
    releases = RELEASES.replace("repo_name: traefik", "repo_name: traefik-oci")
    assert gate.main(_tree(tmp_path, releases=releases)) == 1
    assert "names no HelmRepository" in capsys.readouterr().err


def test_a_registry_url_that_drifted_fails(tmp_path, capsys):
    registry = REGISTRY.replace("traefik.github.io/charts", "example.test/charts")
    assert gate.main(_tree(tmp_path, registry=registry)) == 1
    assert "matches no HelmRepository url" in capsys.readouterr().err


def test_a_trailing_slash_is_not_a_mismatch(tmp_path):
    releases = RELEASES.replace(
        "repo_url: https://traefik.github.io/charts",
        "repo_url: https://traefik.github.io/charts/",
    )
    assert gate.main(_tree(tmp_path, releases=releases)) == 0


def test_an_entry_with_no_repo_fields_is_left_to_the_manifest(tmp_path):
    releases = RELEASES.replace("    repo_name: traefik\n", "").replace(
        "    repo_url: https://traefik.github.io/charts\n", ""
    )
    assert gate.main(_tree(tmp_path, releases=releases)) == 0


def test_a_sources_directory_with_no_helmrepository_is_an_operator_error(tmp_path):
    assert gate.main(_tree(tmp_path, source="---\nkind: ConfigMap\n")) == 2


def test_a_missing_sources_directory_is_an_operator_error(tmp_path):
    assert gate.main(["--sources-dir", str(tmp_path / "nope")]) == 2


def test_nothing_to_compare_is_an_operator_error(tmp_path):
    assert gate.main(_tree(tmp_path, releases=None, registry=None)) == 2


def test_a_registry_without_a_config_mapping_is_an_operator_error(tmp_path):
    assert gate.main(_tree(tmp_path, registry="OTHER = {}\n")) == 2


SERVICE_REGISTRY_FORM = '''SERVICE_REGISTRY = [
    {"name": "traefik", "helm_repo": "https://traefik.github.io/charts"},
]
'''


def test_the_service_registry_form_is_accepted(tmp_path):
    assert gate.main(_tree(tmp_path, registry=SERVICE_REGISTRY_FORM)) == 0


def test_a_service_registry_url_that_drifted_fails(tmp_path, capsys):
    registry = SERVICE_REGISTRY_FORM.replace(
        "traefik.github.io/charts", "example.test/charts")
    assert gate.main(_tree(tmp_path, registry=registry)) == 1
    assert "matches no HelmRepository url" in capsys.readouterr().err


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))


NO_REPO_RELEASES = """---
releases:
  - name: traefik
    manifest: kubernetes/infrastructure/controllers/traefik/release.yaml
    chart: traefik
"""

NO_REPO_REGISTRY = '''CONFIG = {"services": [{"name": "traefik"}]}
'''


def test_a_corpus_with_no_declared_repo_is_an_operator_error(tmp_path, capsys):
    args = _tree(tmp_path, releases=NO_REPO_RELEASES, registry=NO_REPO_REGISTRY)
    assert gate.main(args) == 2
    assert "nothing was compared" in capsys.readouterr().err


def test_allow_empty_passes_a_corpus_with_no_declared_repo(tmp_path, capsys):
    args = _tree(tmp_path, releases=NO_REPO_RELEASES, registry=NO_REPO_REGISTRY)
    assert gate.main(args + ["--allow-empty"]) == 0
    assert "0 comparison(s)" in capsys.readouterr().out


def test_the_success_line_counts_real_comparisons(tmp_path, capsys):
    assert gate.main(_tree(tmp_path)) == 0
    assert "2 comparison(s)" in capsys.readouterr().out


def test_a_yaml_registry_is_an_operator_error(tmp_path, capsys):
    registry = tmp_path / "registry.yaml"
    registry.write_text("services: []\n", encoding="utf-8")
    args = _tree(tmp_path, registry=None)
    args[args.index("--registry") + 1] = str(registry)
    assert gate.main(args) == 2
    assert "unsupported config format" in capsys.readouterr().err


def test_a_registry_that_is_not_python_is_an_operator_error(tmp_path, capsys):
    registry = tmp_path / "registry.py"
    registry.write_text("services: [not python]\n", encoding="utf-8")
    args = _tree(tmp_path, registry=None)
    args[args.index("--registry") + 1] = str(registry)
    assert gate.main(args) == 2
    assert "could not read a repo list" in capsys.readouterr().err


def test_a_helmrepository_in_a_yml_file_is_seen(tmp_path):
    """A `.yml` source is as real to Flux as a `.yaml` one."""
    args = _tree(tmp_path)
    sources = tmp_path / "sources"
    (sources / "traefik.yaml").rename(sources / "traefik.yml")
    assert gate.main(args) == 0


def test_a_trailing_slash_in_the_helmrepository_url_still_matches(tmp_path):
    source = SOURCE.replace("charts\n", "charts/\n")
    assert gate.main(_tree(tmp_path, source=source)) == 0


def test_an_unparseable_sources_file_is_an_operator_error(tmp_path, capsys):
    """An unreadable corpus is not `the helm repo URLs drifted`."""
    args = _tree(tmp_path)
    bad = tmp_path / "sources" / "bad.yaml"
    bad.write_text("a: [unclosed\n", encoding="utf-8")
    assert gate.main(args) == 2
    err = capsys.readouterr().err
    assert str(bad) in err
    assert "Traceback" not in err


def test_a_helmrepository_without_a_url_names_itself(tmp_path, capsys):
    """Dropping the CR would blame the releases entry that names it."""
    source = SOURCE.replace("spec:\n  url: https://traefik.github.io/charts\n", "spec: {}\n")
    assert gate.main(_tree(tmp_path, source=source)) == 1
    assert "HelmRepository/traefik declares no spec.url" in capsys.readouterr().err


def test_an_unparseable_releases_list_is_an_operator_error(tmp_path, capsys):
    """A malformed release list is not `the helm repo URLs drifted`."""
    args = _tree(tmp_path, releases="releases: [unclosed\n")
    assert gate.main(args) == 2
    err = capsys.readouterr().err
    assert str(tmp_path / "releases.yaml") in err
    assert "Traceback" not in err
