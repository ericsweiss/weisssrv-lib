"""Tests for scripts/check-kustomization.py, both directions plus the vacuity arms.

Every failure arm is driven from a fixture tree, so a mutation that stops the
gate reading one of the content keys turns a test red.
"""
from __future__ import annotations

import textwrap

import pytest
from script_loader import load_script

gate = load_script("check-kustomization.py")


def write(base, name: str, body: str) -> None:
    path = base / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body))


CONFIGMAP = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: app
"""


def test_a_listed_manifest_that_exists_passes(tmp_path, capsys):
    write(tmp_path, "kustomization.yaml", "resources:\n  - cm.yaml\n")
    write(tmp_path, "cm.yaml", CONFIGMAP)
    assert gate.main([str(tmp_path)]) == 0
    assert "lists 1 resources" in capsys.readouterr().out


def test_a_listed_manifest_that_does_not_exist_is_a_violation(tmp_path, capsys):
    write(tmp_path, "kustomization.yaml", "resources:\n  - cm.yaml\n  - gone.yaml\n")
    write(tmp_path, "cm.yaml", CONFIGMAP)
    assert gate.main([str(tmp_path)]) == 1
    assert "names paths that do not exist" in capsys.readouterr().err


def test_a_manifest_present_but_unlisted_is_a_violation(tmp_path, capsys):
    write(tmp_path, "kustomization.yaml", "resources:\n  - cm.yaml\n")
    write(tmp_path, "cm.yaml", CONFIGMAP)
    write(tmp_path, "orphan.yaml", CONFIGMAP)
    assert gate.main([str(tmp_path)]) == 1
    assert "present but not listed" in capsys.readouterr().err


def test_an_emptied_resources_list_is_a_violation(tmp_path, capsys):
    """The arm that matters: an empty render plus `prune: true` deletes the app."""
    write(tmp_path, "kustomization.yaml", "resources: []\n")
    assert gate.main([str(tmp_path)]) == 1
    assert "lists no resources" in capsys.readouterr().err


def test_a_listed_manifest_carrying_no_object_is_a_violation(tmp_path, capsys):
    write(tmp_path, "kustomization.yaml", "resources:\n  - cm.yaml\n")
    write(tmp_path, "cm.yaml", "# commented out\n")
    assert gate.main([str(tmp_path)]) == 1
    assert "carry no object" in capsys.readouterr().err


@pytest.mark.parametrize(
    "remote",
    [
        "https://github.invalid/org/repo//dir?ref=v1.0.0",
        "github.invalid/org/repo//dir?ref=v1.0.0",
        "git@github.invalid:org/repo.git//dir?ref=v1",
    ],
)
def test_a_pinned_remote_base_is_not_looked_for_on_disk(tmp_path, remote):
    write(tmp_path, "kustomization.yaml", f"resources:\n  - cm.yaml\n  - {remote}\n")
    write(tmp_path, "cm.yaml", CONFIGMAP)
    assert gate.main([str(tmp_path)]) == 0


def test_a_local_sibling_beside_a_remote_base_is_still_checked(tmp_path, capsys):
    """The remote guard must not widen into skipping the local paths with it."""
    write(
        tmp_path,
        "kustomization.yaml",
        """\
        resources:
          - cm.yaml
          - ghost.yaml
          - github.invalid/org/repo//dir?ref=v1.0.0
        """,
    )
    write(tmp_path, "cm.yaml", CONFIGMAP)
    assert gate.main([str(tmp_path)]) == 1
    assert "ghost.yaml" in capsys.readouterr().err


def test_a_remote_base_is_not_looked_for_on_disk(tmp_path):
    write(
        tmp_path,
        "kustomization.yaml",
        """\
        resources:
          - cm.yaml
          - https://example.invalid/base.yaml
          - github.com/org/repo//overlays/prod?ref=v1
        """,
    )
    write(tmp_path, "cm.yaml", CONFIGMAP)
    assert gate.main([str(tmp_path)]) == 0


@pytest.mark.parametrize("spelling", ["cm.yaml", "./cm.yaml"])
def test_a_dot_slash_prefix_is_the_same_path(tmp_path, spelling):
    write(tmp_path, "kustomization.yaml", f"resources:\n  - {spelling}\n")
    write(tmp_path, "cm.yaml", CONFIGMAP)
    assert gate.main([str(tmp_path)]) == 0


def test_a_listed_directory_covers_the_manifests_under_it(tmp_path):
    write(tmp_path, "kustomization.yaml", "resources:\n  - sub\n")
    write(tmp_path / "sub", "kustomization.yaml", "resources:\n  - cm.yaml\n")
    write(tmp_path / "sub", "cm.yaml", CONFIGMAP)
    assert gate.main([str(tmp_path)]) == 0


def test_a_listed_directory_with_no_kustomization_is_a_violation(tmp_path, capsys):
    write(tmp_path, "kustomization.yaml", "resources:\n  - sub\n")
    write(tmp_path / "sub", "cm.yaml", CONFIGMAP)
    assert gate.main([str(tmp_path)]) == 1
    assert "kustomize builds nothing here" in capsys.readouterr().err


def test_a_child_directorys_own_drift_is_reported(tmp_path, capsys):
    write(tmp_path, "kustomization.yaml", "resources:\n  - sub\n")
    write(tmp_path / "sub", "kustomization.yaml", "resources:\n  - cm.yaml\n")
    write(tmp_path / "sub", "cm.yaml", CONFIGMAP)
    write(tmp_path / "sub", "orphan.yaml", CONFIGMAP)
    assert gate.main([str(tmp_path)]) == 1
    assert "orphan.yaml" in capsys.readouterr().err


def test_a_cycle_terminates(tmp_path):
    """Two directories listing each other must not recurse forever."""
    write(tmp_path, "kustomization.yaml", "resources:\n  - sub\n")
    write(tmp_path / "sub", "kustomization.yaml", "resources:\n  - ..\n")
    assert gate.main([str(tmp_path)]) in (0, 1)


@pytest.mark.parametrize(
    ("key", "body"),
    [
        ("components", "components:\n  - comp\n"),
        ("patches", "patches:\n  - path: patch.yaml\n"),
        ("patchesStrategicMerge", "patchesStrategicMerge:\n  - patch.yaml\n"),
        ("configMapGenerator", "configMapGenerator:\n  - name: c\n    files:\n      - patch.yaml\n"),
        ("configMapGenerator env", "configMapGenerator:\n  - name: c\n    env: patch.yaml\n"),
        ("helmCharts", "helmCharts:\n  - name: c\n    valuesFile: patch.yaml\n"),
    ],
)
def test_a_file_named_through_another_content_key_counts_as_listed(tmp_path, key, body):
    """A manifest reached through any of these keys is built, so it is listed."""
    write(tmp_path, "kustomization.yaml", "resources:\n  - cm.yaml\n" + body)
    write(tmp_path, "cm.yaml", CONFIGMAP)
    if key == "components":
        write(
            tmp_path / "comp",
            "kustomization.yaml",
            "apiVersion: kustomize.config.k8s.io/v1alpha1\n"
            "kind: Component\n"
            "patches:\n  - path: p.yaml\n",
        )
        write(tmp_path / "comp", "p.yaml", CONFIGMAP)
    else:
        write(tmp_path, "patch.yaml", CONFIGMAP)
    assert gate.main([str(tmp_path)]) == 0


class TestInlineStrategicMergePatch:
    """`patchesStrategicMerge` takes a path or a whole patch document inline."""

    INLINE = (
        "resources:\n  - cm.yaml\n"
        "patchesStrategicMerge:\n"
        "  - |-\n"
        "    apiVersion: v1\n"
        "    kind: ConfigMap\n"
        "    metadata:\n"
        "      name: app\n"
        "    data:\n"
        "      key: value\n"
    )

    def test_an_inline_patch_is_not_a_path(self, tmp_path):
        write(tmp_path, "kustomization.yaml", self.INLINE)
        write(tmp_path, "cm.yaml", CONFIGMAP)
        assert gate.main([str(tmp_path)]) == 0

    def test_a_patch_path_that_does_not_exist_is_a_violation(self, tmp_path, capsys):
        write(
            tmp_path,
            "kustomization.yaml",
            "resources:\n  - cm.yaml\npatchesStrategicMerge:\n  - gone.yaml\n",
        )
        write(tmp_path, "cm.yaml", CONFIGMAP)
        assert gate.main([str(tmp_path)]) == 1
        assert "gone.yaml" in capsys.readouterr().err


def test_an_inert_component_is_a_violation(tmp_path, capsys):
    write(tmp_path, "kustomization.yaml", "resources:\n  - comp\n")
    write(
        tmp_path / "comp",
        "kustomization.yaml",
        "apiVersion: kustomize.config.k8s.io/v1alpha1\nkind: Component\n",
    )
    assert gate.main([str(tmp_path)]) == 1
    assert "contributes nothing" in capsys.readouterr().err


class TestTheGateRefusesToBeVacuous:
    """A run that inspected no kustomization.yaml must not report green."""

    def test_a_directory_with_no_kustomization_is_an_operator_error(self, tmp_path, capsys):
        write(tmp_path, "cm.yaml", CONFIGMAP)
        assert gate.main([str(tmp_path)]) == 2
        assert "kustomize builds nothing here" in capsys.readouterr().err

    def test_a_nonexistent_directory_is_an_operator_error(self, tmp_path, capsys):
        assert gate.main([str(tmp_path / "gone")]) == 2
        assert "is not a directory" in capsys.readouterr().err

    def test_an_unparseable_kustomization_is_an_operator_error(self, tmp_path, capsys):
        write(tmp_path, "kustomization.yaml", "resources: [oops\n")
        assert gate.main([str(tmp_path)]) == 2
        err = capsys.readouterr().err
        assert "unparseable YAML" in err and "<unicode string>" not in err

    def test_a_non_mapping_kustomization_is_an_operator_error(self, tmp_path, capsys):
        write(tmp_path, "kustomization.yaml", "- resources\n")
        assert gate.main([str(tmp_path)]) == 2
        assert "is not a mapping" in capsys.readouterr().err


def test_a_yml_spelled_kustomization_is_read(tmp_path):
    write(tmp_path, "kustomization.yml", "resources:\n  - cm.yaml\n")
    write(tmp_path, "cm.yaml", CONFIGMAP)
    assert gate.main([str(tmp_path)]) == 0


def test_a_remote_reference_is_classified_as_remote():
    """The is_local() shapes, spelled here rather than read from the gate."""
    for name in (
        "https://example.invalid/base.yaml",
        "git@example.invalid:org/repo.git",
        "github.com/org/repo//overlays/prod",
        "ssh://git@example.invalid/org/repo",
        "org/repo.git/overlays",
        "./base?ref=v1",
    ):
        assert not gate.is_local(name), name
    for name in ("cm.yaml", "./cm.yaml", "sub/cm.yaml", "../sibling"):
        assert gate.is_local(name), name


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
