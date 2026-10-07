"""scripts/flux-child-kustomizations.py — dependsOn order and the empty cases."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from script_loader import load_script

tool = load_script("flux-child-kustomizations.py")

KUSTOMIZATION = """---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: %s
spec:
  path: ./kubernetes/%s
%s
"""


def _ordered(directory: Path):
    """The composition main() uses: (ordered names, names in a cycle)."""
    return tool._order(tool._kustomizations(directory)[0])


def _cluster(tmp_path: Path, stages) -> Path:
    directory = tmp_path / "kubernetes" / "clusters" / "demo"
    directory.mkdir(parents=True)
    for name, deps in stages:
        block = ""
        if deps:
            block = "  dependsOn:\n" + "".join("    - name: %s\n" % d for d in deps)
        (directory / ("%s.yaml" % name)).write_text(
            KUSTOMIZATION % (name, name, block), encoding="utf-8"
        )
    return directory


def test_dependencies_come_before_their_dependants(tmp_path):
    directory = _cluster(tmp_path, [
        ("apps", ["configs"]),
        ("configs", ["controllers"]),
        ("controllers", []),
    ])
    assert _ordered(directory)[0] == ["controllers", "configs", "apps"]


def test_independent_stages_are_sorted_so_the_output_is_stable(tmp_path):
    directory = _cluster(tmp_path, [("zebra", []), ("alpha", []), ("middle", [])])
    assert _ordered(directory)[0] == ["alpha", "middle", "zebra"]


def test_a_dependson_cycle_is_reported_and_still_deterministic(tmp_path):
    directory = _cluster(tmp_path, [("one", ["two"]), ("two", ["one"])])
    ordered, cycled = _ordered(directory)
    assert ordered == ["one", "two"]
    assert cycled == ["one", "two"]


def test_a_dependson_cycle_is_an_operator_error(tmp_path, capsys):
    """The printed order cannot satisfy the declared dependencies."""
    directory = _cluster(tmp_path, [("one", ["two"]), ("two", ["one"])])
    assert tool.main(["--dir", str(directory)]) == 2
    out = capsys.readouterr()
    assert out.out.split() == ["one", "two"]
    assert "cycle among one, two" in out.err


def test_a_non_flux_kustomization_is_ignored(tmp_path):
    directory = _cluster(tmp_path, [("apps", [])])
    (directory / "kustomize.yaml").write_text(
        "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\n"
        "resources: []\n", encoding="utf-8",
    )
    assert _ordered(directory)[0] == ["apps"]


def test_paths_mode_prints_the_name_and_the_spec_path(tmp_path, capsys):
    """`name<TAB>path`: a caller labels each stage, and `cut -f2` is the path."""
    directory = _cluster(tmp_path, [("apps", ["configs"]), ("configs", [])])
    assert tool.main(["--dir", str(directory), "--paths"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "configs\t./kubernetes/configs", "apps\t./kubernetes/apps",
    ]


def test_child_kustomization_paths_is_the_importable_form(tmp_path):
    directory = _cluster(tmp_path, [("apps", ["configs"]), ("configs", [])])
    assert tool.child_kustomization_paths(directory) == [
        ("configs", "./kubernetes/configs"), ("apps", "./kubernetes/apps"),
    ]


PATHLESS_KUSTOMIZATION = """---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: %s
spec:
  sourceRef:
    kind: GitRepository
    name: flux-system
"""


def _add_pathless(directory: Path, name: str) -> None:
    (directory / ("%s.yaml" % name)).write_text(
        PATHLESS_KUSTOMIZATION % name, encoding="utf-8"
    )


def test_a_pathless_kustomization_fails_paths_by_default(tmp_path, capsys):
    """A render corpus built from a silently short list under-covers the cluster."""
    directory = _cluster(tmp_path, [("apps", [])])
    _add_pathless(directory, "observability")
    assert tool.main(["--dir", str(directory), "--paths"]) == 1
    assert "observability" in capsys.readouterr().err


def test_allow_missing_paths_prints_the_paths_there_are(tmp_path, capsys):
    directory = _cluster(tmp_path, [("apps", [])])
    _add_pathless(directory, "observability")
    assert tool.main(
        ["--dir", str(directory), "--paths", "--allow-missing-paths"]
    ) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines() == ["apps\t./kubernetes/apps"]
    assert "observability" in captured.err


def test_require_paths_is_accepted_as_a_no_op_alias(tmp_path, capsys):
    directory = _cluster(tmp_path, [("apps", [])])
    _add_pathless(directory, "observability")
    assert tool.main(["--dir", str(directory), "--paths", "--require-paths"]) == 1
    assert "observability" in capsys.readouterr().err


def test_paths_passes_when_every_stage_declares_one(tmp_path):
    directory = _cluster(tmp_path, [("apps", []), ("configs", [])])
    assert tool.main(["--dir", str(directory), "--paths", "--require-paths"]) == 0
    assert tool.main(["--dir", str(directory), "--paths"]) == 0


def test_a_tree_where_no_kustomization_declares_a_path_is_a_finding(tmp_path, capsys):
    """An empty child-pipeline path list would pass every render job on nothing."""
    directory = _cluster(tmp_path, [])
    _add_pathless(directory, "apps")
    _add_pathless(directory, "observability")
    assert tool.main(["--dir", str(directory), "--paths", "--allow-missing-paths"]) == 1
    captured = capsys.readouterr()
    assert "declares a spec.path" in captured.err
    assert captured.out.strip() == ""
    assert tool.main(["--dir", str(directory), "--paths"]) == 1


def test_another_kind_in_the_cluster_directory_is_ignored(tmp_path):
    directory = _cluster(tmp_path, [("apps", [])])
    (directory / "release.yaml").write_text(
        "apiVersion: helm.toolkit.fluxcd.io/v2\nkind: HelmRelease\n"
        "metadata:\n  name: thing\n", encoding="utf-8",
    )
    assert _ordered(directory)[0] == ["apps"]


def test_a_malformed_cluster_manifest_is_an_operator_error(tmp_path, capsys):
    """A parse failure must not read as `no Kustomizations found`."""
    directory = _cluster(tmp_path, [("apps", [])])
    bad = directory / "broken.yaml"
    bad.write_text("a: [unclosed\n", encoding="utf-8")
    assert tool.main(["--dir", str(directory)]) == 2
    err = capsys.readouterr().err
    assert str(bad) in err
    assert "Traceback" not in err


def test_a_directory_with_no_kustomization_fails(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert tool.main(["--dir", str(empty)]) == 1


def test_a_missing_directory_is_an_operator_error(tmp_path):
    assert tool.main(["--dir", str(tmp_path / "nope")]) == 2


def test_the_default_directory_is_the_single_cluster(tmp_path, monkeypatch):
    _cluster(tmp_path, [("apps", [])])
    monkeypatch.chdir(tmp_path)
    assert tool.main([]) == 0


def test_two_cluster_directories_require_an_explicit_choice(tmp_path, monkeypatch):
    _cluster(tmp_path, [("apps", [])])
    os.mkdir(tmp_path / "kubernetes" / "clusters" / "other")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        tool.main([])
    assert exc.value.code == 2


def test_no_clusters_directory_is_an_operator_error(tmp_path, monkeypatch, capsys):
    """exit 1 would read as a policy finding, not a wrong working directory."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        tool.main([])
    assert exc.value.code == 2
    assert "pass --dir" in capsys.readouterr().err


def test_a_root_level_flux_system_is_excluded(tmp_path, capsys):
    """Its spec.path would build the Flux controllers into the render corpus."""
    directory = _cluster(tmp_path, [("apps", []), ("flux-system", [])])
    assert tool.main(["--dir", str(directory)]) == 0
    assert capsys.readouterr().out.split() == ["apps"]
    assert tool.main(["--dir", str(directory), "--paths"]) == 0
    assert capsys.readouterr().out.splitlines() == ["apps\t./kubernetes/apps"]


def test_an_extra_exclusion_keeps_flux_system_excluded(tmp_path, capsys):
    directory = _cluster(tmp_path, [("apps", []), ("flux-system", []), ("extra", [])])
    assert tool.main(["--dir", str(directory), "--exclude", "extra"]) == 0
    assert capsys.readouterr().out.split() == ["apps"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))


def test_a_yml_stage_is_enumerated_like_a_yaml_one(tmp_path):
    """Flux reconciles either suffix; globbing one drops a stage from the order
    and its spec.path never reaches the render gates."""
    directory = _cluster(tmp_path, [("apps", ["configs"])])
    (directory / "configs.yml").write_text(
        KUSTOMIZATION % ("configs", "configs", ""), encoding="utf-8"
    )
    assert _ordered(directory)[0] == ["configs", "apps"]


def test_a_yml_stage_contributes_its_path(tmp_path, capsys):
    directory = _cluster(tmp_path, [("apps", [])])
    (directory / "observability.yml").write_text(
        KUSTOMIZATION % ("observability", "observability", ""), encoding="utf-8"
    )
    assert tool.main(["--dir", str(directory), "--paths"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "apps\t./kubernetes/apps", "observability\t./kubernetes/observability",
    ]
