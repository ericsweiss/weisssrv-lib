"""Tests for scripts/tenant_common.py, the shared half of the two tenant gates."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest
from script_loader import load_script

common = load_script("tenant_common.py")


def parse(argv: list[str], root: Path):
    parser = argparse.ArgumentParser()
    common.add_target_arguments(parser, root)
    return common.resolve_targets(parser.parse_args(argv))


def tenants(root: Path, cluster: str) -> Path:
    path = root / "kubernetes" / "clusters" / cluster / "tenants"
    path.mkdir(parents=True)
    return path


def test_the_one_tenants_directory_is_discovered(tmp_path: Path) -> None:
    found = tenants(tmp_path, "alpha")
    assert common.discover_tenants_dir(tmp_path) == found
    assert parse([], tmp_path).tenants_dir == found


def test_no_tenants_directory_is_vacuous(tmp_path: Path) -> None:
    with pytest.raises(common.OperatorError, match="--tenants-dir"):
        common.discover_tenants_dir(tmp_path)


def test_two_tenants_directories_need_a_choice(tmp_path: Path) -> None:
    tenants(tmp_path, "alpha")
    beta = tenants(tmp_path, "beta")
    with pytest.raises(common.OperatorError, match="--cluster-dir"):
        parse([], tmp_path)
    assert parse(["--cluster-dir", "kubernetes/clusters/beta"], tmp_path).tenants_dir == beta


def test_the_tenants_dir_flag_resolves_under_the_root(tmp_path: Path) -> None:
    found = tenants(tmp_path, "alpha")
    rel = "kubernetes/clusters/alpha/tenants"
    assert parse(["--repo-root", str(tmp_path), "--tenants-dir", rel], Path("/")).tenants_dir == found


def test_files_are_targets_on_their_own(tmp_path: Path) -> None:
    wiring = tmp_path / "demo.yaml"
    wiring.write_text("kind: Namespace\n")
    targets = parse([str(wiring)], tmp_path)
    assert targets.files == [wiring] and targets.tenants_dir is None
    assert targets.label(wiring) == "demo.yaml"


def test_relative_positionals_resolve_under_the_root(tmp_path: Path) -> None:
    found = tenants(tmp_path, "alpha")
    wiring = found / "demo.yaml"
    wiring.write_text("kind: Namespace\n")
    rel = "kubernetes/clusters/alpha/tenants"
    assert parse(["--repo-root", str(tmp_path), rel], Path("/")).tenants_dir == found
    targets = parse(["--repo-root", str(tmp_path), f"{rel}/demo.yaml"], Path("/"))
    assert targets.files == [wiring]
    with pytest.raises(common.OperatorError, match="no such wiring file"):
        parse(["--repo-root", str(tmp_path), f"{rel}/gone.yaml"], Path("/"))


def test_a_directory_target_is_a_tenants_directory(tmp_path: Path) -> None:
    found = tenants(tmp_path, "alpha")
    assert parse([str(found)], tmp_path).tenants_dir == found


@pytest.mark.parametrize(
    ("shape", "message"),
    [
        ("two-directories", "not a mix"),
        ("directory-and-file", "not a mix"),
        ("file-and-flag", "not both"),
    ],
)
def test_an_ambiguous_target_is_vacuous(tmp_path: Path, shape: str, message: str) -> None:
    first, second = tenants(tmp_path, "alpha"), tenants(tmp_path, "beta")
    wiring = tmp_path / "x.yaml"
    wiring.write_text("kind: Namespace\n")
    argv = {
        "two-directories": [str(first), str(second)],
        "directory-and-file": [str(first), str(wiring)],
        "file-and-flag": ["--tenants-dir", str(first), str(wiring)],
    }[shape]
    with pytest.raises(common.OperatorError, match=message):
        parse(argv, tmp_path)


def test_a_label_outside_the_root_is_the_path_as_given(tmp_path: Path) -> None:
    targets = common.Targets(tmp_path / "root")
    assert targets.label(Path("/elsewhere/demo.yaml")) == "/elsewhere/demo.yaml"


def test_metadata_and_kind_helpers_degrade_on_a_malformed_document() -> None:
    docs = [
        {"kind": "Namespace", "metadata": {"name": "a"}},
        {"kind": "ConfigMap", "metadata": "malformed"},
        {"kind": "Namespace"},
    ]
    assert common.of_kind(docs, "Namespace") == [docs[0], docs[2]]
    assert common.of_kind(docs, "Secret") == []
    assert common.metadata(docs[0]) == {"name": "a"}
    assert common.metadata(docs[1]) == {}
    assert common.metadata(docs[2]) == {}


def test_the_aggregator_resources_are_normalized(tmp_path: Path) -> None:
    (tmp_path / common.AGGREGATOR).write_text("resources:\n  - ./a.yaml\n  - b.yaml\n")
    assert common.listed_resources(tmp_path) == {"a.yaml", "b.yaml"}


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("resources: x\n", "is not a list"),
        ("- a\n", "is not a mapping"),
        ("resources: [\n", "unreadable"),
        ("resources: [1]\n", "not file names"),
        ("resources: ['']\n", "not file names"),
        ("resources: ['  ']\n", "not file names"),
        ("resources:\n  - ../x.yaml\n", "outside the tenant directory"),
        ("resources: [/x.yaml]\n", "outside the tenant directory"),
        ("resources: [kustomization.yaml]\n", "referring to itself"),
        ("resources: ['.']\n", "referring to itself"),
        ("resources: [./sub/..]\n", "referring to itself"),
    ],
)
def test_an_unreadable_aggregator_is_vacuous(tmp_path: Path, body: str, message: str) -> None:
    (tmp_path / common.AGGREGATOR).write_text(body)
    with pytest.raises(common.OperatorError, match=message):
        common.listed_resources(tmp_path)


def test_operator_error_is_the_gate_common_class() -> None:
    """One exit-2 error type serves both helper layers a tenant gate imports."""
    assert common.OperatorError is sys.modules["gate_common"].OperatorError


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"a: [1,\n", "w.yaml: unparseable YAML:"),
        (b"a: \xff\xfe\n", "w.yaml: unreadable:"),
    ],
)
def test_an_unloadable_input_is_vacuous_with_its_label(
    tmp_path: Path, body: bytes, message: str
) -> None:
    path = tmp_path / "w.yaml"
    path.write_bytes(body)
    with pytest.raises(common.OperatorError, match=message):
        common.load_mappings(path, "w.yaml")


def test_only_a_parse_failure_carries_the_flux_remedy(tmp_path: Path) -> None:
    path = tmp_path / "w.yaml"
    path.write_text("a: [1,\n")
    with pytest.raises(common.OperatorError, match="make it parse"):
        common.load_mappings(path, "w.yaml")
    with pytest.raises(common.OperatorError) as missing:
        common.load_mappings(tmp_path / "absent.yaml", "absent.yaml")
    assert "make it parse" not in str(missing.value)
