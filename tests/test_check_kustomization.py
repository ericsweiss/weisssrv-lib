"""Tests for scripts/check-kustomization.py: the descent, the walk, the exemptions.

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


def test_a_flux_bootstrap_directory_may_name_its_gotk_files_before_they_exist(tmp_path):
    write(tmp_path, "kustomization.yaml",
          "resources:\n  - gotk-components.yaml\n  - ./gotk-sync.yaml\n  - cm.yaml\n")
    write(tmp_path, "cm.yaml", CONFIGMAP)
    assert gate.main([str(tmp_path)]) == 0


def test_a_bootstrap_directory_still_fails_any_other_missing_path(tmp_path, capsys):
    write(tmp_path, "kustomization.yaml",
          "resources:\n  - gotk-components.yaml\n  - gotk-sync.yaml\n  - gone.yaml\n")
    assert gate.main([str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "gone.yaml" in err and "gotk-" not in err


def test_a_lone_missing_gotk_file_is_a_violation(tmp_path, capsys):
    """Only a kustomization naming both files is a bootstrap directory."""
    write(tmp_path, "kustomization.yaml",
          "resources:\n  - gotk-components.yaml\n  - cm.yaml\n")
    write(tmp_path, "cm.yaml", CONFIGMAP)
    assert gate.main([str(tmp_path)]) == 1
    assert "gotk-components.yaml" in capsys.readouterr().err


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
        "https://example.invalid/base.yaml",
    ],
)
def test_a_remote_base_is_not_looked_for_on_disk(tmp_path, remote):
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
    assert gate.main([str(tmp_path)]) == 0


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
        "github.com/org/repo/overlays/prod",
        "ssh://git@example.invalid/org/repo",
        "org/repo.git/overlays",
        "./base?ref=v1",
    ):
        assert not gate.is_local(name), name
    for name in ("cm.yaml", "./cm.yaml", "sub/cm.yaml", "../sibling"):
        assert gate.is_local(name), name


# One kustomization body per resource key beyond resources and bases, with the
# files it names; each one alone puts objects into the render.
RESOURCE_KEY_CASES = {
    "components": ("components:\n  - comp\n", {
        "comp/kustomization.yaml": "apiVersion: kustomize.config.k8s.io/v1alpha1\n"
                                   "kind: Component\nresources:\n  - cm.yaml\n",
        "comp/cm.yaml": CONFIGMAP,
    }),
    "helmCharts": (
        "helmCharts:\n  - name: x\n    repo: https://x.invalid\n    version: 1.0.0\n", {},
    ),
    "generators": ("generators:\n  - gen.yaml\n", {"gen.yaml": CONFIGMAP}),
    "configMapGenerator": (
        "configMapGenerator:\n  - name: dash\n    files:\n      - board=board.json\n",
        {"board.json": "{}\n"},
    ),
    "secretGenerator": ("secretGenerator:\n  - name: s\n    literals:\n      - a=b\n", {}),
}


def test_every_resource_key_has_a_render_case():
    assert set(RESOURCE_KEY_CASES) == set(gate.RESOURCE_KEYS) - {"resources", "bases"}


@pytest.mark.parametrize("key", sorted(RESOURCE_KEY_CASES))
def test_a_kustomization_holding_one_resource_key_renders(tmp_path, key):
    """components, helmCharts, generators and both generator kinds each put
    objects into the render on their own."""
    body, files = RESOURCE_KEY_CASES[key]
    write(tmp_path, "kustomization.yaml", body)
    for name, content in files.items():
        write(tmp_path, name, content)
    assert gate.main([str(tmp_path)]) == 0


def test_a_transformer_only_kustomization_is_a_violation(tmp_path, capsys):
    write(tmp_path, "kustomization.yaml", "images:\n  - name: demo\n    newTag: v1\n")
    assert gate.main([str(tmp_path)]) == 1
    assert "lists only transformers" in capsys.readouterr().err


def test_an_unlisted_json_manifest_is_a_violation(tmp_path, capsys):
    write(tmp_path, "kustomization.yaml", "resources:\n  - cm.yaml\n")
    write(tmp_path, "cm.yaml", CONFIGMAP)
    write(tmp_path, "board.json", "{}\n")
    assert gate.main([str(tmp_path)]) == 1
    assert "board.json" in capsys.readouterr().err


def test_a_bases_only_kustomization_references_its_target(tmp_path):
    write(tmp_path, "kustomization.yaml", "bases:\n  - cm.yaml\n")
    write(tmp_path, "cm.yaml", CONFIGMAP)
    assert gate.main([str(tmp_path)]) == 0


def test_a_non_utf8_manifest_is_an_operator_error(tmp_path, capsys):
    write(tmp_path, "kustomization.yaml", "resources:\n  - cm.yaml\n")
    (tmp_path / "cm.yaml").write_bytes(b"kind: \xff\xfe\n")
    assert gate.main([str(tmp_path)]) == 2
    assert "cm.yaml" in capsys.readouterr().err


def _app(tree, name: str, extra: str = "", files: dict | None = None):
    """One walked directory: a kustomization listing cm.yaml, plus `files`."""
    write(tree / name, "kustomization.yaml", "resources:\n  - cm.yaml\n" + extra)
    write(tree / name, "cm.yaml", CONFIGMAP)
    for rel, body in (files or {}).items():
        write(tree / name, rel, body)


# One body per key that reshapes a render without adding to it, with the files
# it names. Each alone makes a Component useful and a kustomization empty.
TRANSFORMER_KEY_CASES = {
    "patches": ("patches:\n  - path: p.yaml\n", {"p.yaml": CONFIGMAP}),
    "patchesStrategicMerge": ("patchesStrategicMerge:\n  - p.yaml\n", {"p.yaml": CONFIGMAP}),
    "patchesJson6902": (
        "patchesJson6902:\n  - path: p.yaml\n    target:\n      kind: ConfigMap\n",
        {"p.yaml": "- op: add\n  path: /data\n  value: {}\n"},
    ),
    "replacements": ("replacements:\n  - path: r.yaml\n", {"r.yaml": "source:\n  kind: ConfigMap\n"}),
    "transformers": ("transformers:\n  - t.yaml\n", {"t.yaml": CONFIGMAP}),
    "images": ("images:\n  - name: demo\n    newTag: v1\n", {}),
    "labels": ("labels:\n  - pairs:\n      app: demo\n", {}),
    "commonLabels": ("commonLabels:\n  app: demo\n", {}),
    "commonAnnotations": ("commonAnnotations:\n  owner: demo\n", {}),
    "replicas": ("replicas:\n  - name: demo\n    count: 2\n", {}),
    "openapi": ("openapi:\n  path: schema.json\n", {"schema.json": "{}\n"}),
    "crds": ("crds:\n  - crd.json\n", {"crd.json": "{}\n"}),
    "configurations": ("configurations:\n  - c.yaml\n", {"c.yaml": "nameReference: []\n"}),
}


class TestWalk:
    """--walk checks every kustomization in a tree against its own siblings."""

    def test_a_fully_listed_tree_passes(self, tmp_path, capsys):
        for name in ("a", "b", "c"):
            _app(tmp_path / "kubernetes", name)
        assert gate.main(["--walk", str(tmp_path / "kubernetes")]) == 0
        assert "3 kustomizations" in capsys.readouterr().out

    def test_an_unlisted_sibling_is_a_violation(self, tmp_path, capsys):
        _app(tmp_path / "kubernetes", "a", files={"netpol.yaml": CONFIGMAP})
        assert gate.main(["--walk", str(tmp_path / "kubernetes")]) == 1
        assert "netpol.yaml" in capsys.readouterr().err

    def test_a_yml_spelled_kustomization_is_walked(self, tmp_path, capsys):
        tree = tmp_path / "kubernetes"
        write(tree / "a", "kustomization.yml", "resources:\n  - cm.yaml\n")
        write(tree / "a", "cm.yaml", CONFIGMAP)
        write(tree / "a", "orphan.yaml", CONFIGMAP)
        assert gate.main(["--walk", str(tree)]) == 1
        err = capsys.readouterr().err
        assert "orphan.yaml" in err and "kustomization.yml" not in err

    def test_a_missing_reference_is_a_violation(self, tmp_path, capsys):
        _app(tmp_path / "kubernetes", "a", "patches:\n  - path: no-such-patch.yaml\n")
        assert gate.main(["--walk", str(tmp_path / "kubernetes")]) == 1
        assert "no-such-patch.yaml" in capsys.readouterr().err

    def test_an_empty_listed_manifest_is_a_violation(self, tmp_path, capsys):
        _app(tmp_path / "kubernetes", "a")
        write(tmp_path / "kubernetes" / "a", "cm.yaml", "---\n# emptied\n")
        assert gate.main(["--walk", str(tmp_path / "kubernetes")]) == 1
        assert "carry no object" in capsys.readouterr().err

    def test_an_emptied_kustomization_is_a_violation(self, tmp_path, capsys):
        write(tmp_path / "kubernetes" / "a", "kustomization.yaml", "resources: []\n")
        assert gate.main(["--walk", str(tmp_path / "kubernetes")]) == 1
        assert "lists no resources" in capsys.readouterr().err

    def test_every_transformer_key_has_a_case(self):
        assert set(TRANSFORMER_KEY_CASES) == set(gate.CONTENT_KEYS) - set(gate.RESOURCE_KEYS)

    @pytest.mark.parametrize("key", sorted(TRANSFORMER_KEY_CASES))
    def test_a_component_holding_one_transformer_key_passes(self, tmp_path, key):
        tree = tmp_path / "kubernetes"
        body, files = TRANSFORMER_KEY_CASES[key]
        write(
            tree / "components" / "c",
            "kustomization.yaml",
            "apiVersion: kustomize.config.k8s.io/v1alpha1\nkind: Component\n" + body,
        )
        for name, content in files.items():
            write(tree / "components" / "c", name, content)
        assert gate.main(["--walk", str(tree)]) == 0

    @pytest.mark.parametrize("key", sorted(TRANSFORMER_KEY_CASES))
    def test_a_kustomization_holding_one_transformer_key_is_a_violation(
        self, tmp_path, capsys, key
    ):
        tree = tmp_path / "kubernetes"
        body, files = TRANSFORMER_KEY_CASES[key]
        write(tree / "a", "kustomization.yaml", body)
        for name, content in files.items():
            write(tree / "a", name, content)
        assert gate.main(["--walk", str(tree)]) == 1
        assert "lists only transformers" in capsys.readouterr().err

    def test_a_nested_kustomization_its_parent_does_not_list_is_its_own_stage(self, tmp_path):
        """A directory a Flux Kustomization reconciles directly is not its
        parent's orphan: each kustomization answers for its own siblings."""
        tree = tmp_path / "kubernetes"
        _app(tree, "controllers")
        _app(tree / "controllers", "metrics-server")
        assert gate.main(["--walk", str(tree)]) == 0
        assert gate.main([str(tree / "controllers")]) == 1

    def test_a_listed_directory_with_no_kustomization_is_a_violation(self, tmp_path, capsys):
        tree = tmp_path / "kubernetes"
        _app(tree, "a")
        write(tree / "a", "kustomization.yaml", "resources:\n  - cm.yaml\n  - sub\n")
        write(tree / "a" / "sub", "cm.yaml", CONFIGMAP)
        assert gate.main(["--walk", str(tree)]) == 1
        assert "kustomize builds nothing here" in capsys.readouterr().err

    def test_a_manifest_in_an_unkustomized_subdirectory_is_not_seen(self, tmp_path, capsys):
        """The walk checks each kustomization against its own siblings only, so
        a file in a subdirectory nothing lists is out of scope; descent sees it."""
        tree = tmp_path / "kubernetes"
        _app(tree, "app")
        write(tree / "app" / "sub", "orphan.yaml", CONFIGMAP)
        assert gate.main(["--walk", str(tree)]) == 0
        assert "no manifest is unlisted" in capsys.readouterr().out
        assert gate.main([str(tree / "app")]) == 1
        assert "orphan.yaml" in capsys.readouterr().err

    def test_too_few_kustomizations_is_an_operator_error(self, tmp_path, capsys):
        _app(tmp_path / "kubernetes", "a")
        argv = ["--walk", "--min-kustomizations", "2", str(tmp_path / "kubernetes")]
        assert gate.main(argv) == 2
        assert "broken walk" in capsys.readouterr().err
        _app(tmp_path / "kubernetes", "b")
        assert gate.main(argv) == 0

    def test_an_empty_tree_is_an_operator_error(self, tmp_path):
        (tmp_path / "kubernetes").mkdir()
        assert gate.main(["--walk", str(tmp_path / "kubernetes")]) == 2


class TestExemptions:
    """--exempt names manifests deliberately left unlisted, each with a reason."""

    def _tree(self, tmp_path):
        _app(tmp_path / "kubernetes", "a", files={"example.yaml": CONFIGMAP})
        return tmp_path

    def test_an_exempt_manifest_is_not_reported(self, tmp_path):
        root = self._tree(tmp_path)
        write(root, "exempt.yaml", "kubernetes/a/example.yaml: copy-me example\n")
        argv = ["--repo-root", str(root), "--walk", "--exempt", "exempt.yaml", "kubernetes"]
        assert gate.main(argv) == 0
        assert gate.main(argv[:3] + ["kubernetes"]) == 1

    def test_an_exemption_applies_to_the_descent_too(self, tmp_path):
        root = self._tree(tmp_path)
        write(root, "exempt.yaml", "./kubernetes/a/example.yaml: copy-me example\n")
        argv = ["--repo-root", str(root), "--exempt", "exempt.yaml", "kubernetes/a"]
        assert gate.main(argv) == 0

    def test_an_exemption_naming_a_gone_file_is_a_violation(self, tmp_path, capsys):
        root = self._tree(tmp_path)
        write(
            root, "exempt.yaml",
            "kubernetes/a/example.yaml: copy-me example\nkubernetes/a/gone.yaml: stale\n",
        )
        argv = ["--repo-root", str(root), "--walk", "--exempt", "exempt.yaml", "kubernetes"]
        assert gate.main(argv) == 1
        assert "kubernetes/a/gone.yaml" in capsys.readouterr().err

    @pytest.mark.parametrize(
        "body",
        ["- kubernetes/a/example.yaml\n", "kubernetes/a/example.yaml:\n",
         "kubernetes/a/example.yaml: ''\n", "a: [\n"],
        ids=["list", "no-reason", "empty-reason", "unparseable"],
    )
    def test_a_malformed_exemption_file_is_an_operator_error(self, tmp_path, body):
        root = self._tree(tmp_path)
        write(root, "exempt.yaml", body)
        argv = ["--repo-root", str(root), "--walk", "--exempt", "exempt.yaml", "kubernetes"]
        assert gate.main(argv) == 2

    def test_a_missing_exemption_file_is_an_operator_error(self, tmp_path):
        root = self._tree(tmp_path)
        argv = ["--repo-root", str(root), "--walk", "--exempt", "gone.yaml", "kubernetes"]
        assert gate.main(argv) == 2


def test_the_directory_resolves_under_the_repo_root(tmp_path, capsys):
    write(tmp_path / "kubernetes" / "flux", "kustomization.yaml", "resources:\n  - cm.yaml\n")
    write(tmp_path / "kubernetes" / "flux", "cm.yaml", CONFIGMAP)
    assert gate.main(["--repo-root", str(tmp_path)]) == 0
    assert gate.main(["--repo-root", str(tmp_path), "kubernetes/flux"]) == 0
    assert gate.main(["--repo-root", str(tmp_path), "kubernetes/db"]) == 2


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
