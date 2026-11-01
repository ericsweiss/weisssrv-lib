"""scripts/check-dashboards.py — every arm of the gate can fail."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from script_loader import load_script

gate = load_script("check-dashboards.py")

GOOD = {
    "uid": "example",
    "panels": [
        {"datasource": {"type": "prometheus", "uid": "prometheus"},
         "targets": [{"expr": "up"}]},
        {"datasource": {"type": "loki", "uid": "loki"}},
        {"datasource": {"type": "datasource", "uid": "-- Grafana --"}},
    ],
}

KUSTOMIZATION = """---
configMapGenerator:
  - name: grafana-dashboard-example
    files:
      - example.json
    options:
      labels:
        grafana_dashboard: "1"
      annotations:
        grafana_folder: "Infrastructure"
"""


def _dir(tmp_path: Path, doc=None, raw=None, kustomization=KUSTOMIZATION) -> Path:
    directory = tmp_path / "dashboards"
    directory.mkdir(exist_ok=True)
    (directory / "example.json").write_text(
        raw if raw is not None else json.dumps(doc if doc is not None else GOOD),
        encoding="utf-8",
    )
    if kustomization is not None:
        (directory / "kustomization.yaml").write_text(kustomization, encoding="utf-8")
    return directory


def _check(directory: Path):
    return gate.check_dir(directory, {"prometheus", "loki"})[0]


DS_VAR = {"list": [{"name": "ds", "type": "datasource"}]}


@pytest.mark.parametrize("doc,needle", [
    pytest.param(GOOD, None, id="a_correct_directory_passes"),
    pytest.param("{nope", "not valid JSON", id="malformed_json_fails"),
    pytest.param(dict(GOOD, __inputs=[{"name": "DS_PROMETHEUS"}]), "__inputs",
                 id="an_export_for_sharing_file_fails"),
    pytest.param({"panels": [{"datasource": {"uid": "${DS_PROMETHEUS}"}}]}, "placeholder",
                 id="a_surviving_import_placeholder_fails"),
    pytest.param({"panels": [{"datasource": {"type": "prometheus", "uid": "thanos"}}]},
                 "not provisioned", id="an_unprovisioned_datasource_uid_fails"),
    # Top-level `uid` names the dashboard and is free-form.
    pytest.param(dict(GOOD, uid="anything-at-all"), None,
                 id="a_dashboard_uid_is_not_mistaken_for_a_datasource_uid"),
    pytest.param({"templating": DS_VAR, "panels": [{"datasource": {"uid": "${ds}"}}]}, None,
                 id="a_declared_templated_datasource_variable_is_allowed"),
    # A renamed or mistyped variable renders as 'Datasource not found'.
    pytest.param({"panels": [{"datasource": {"uid": "$ds"}}]}, "templating.list",
                 id="an_undeclared_templated_datasource_variable_fails"),
    pytest.param({"templating": {"list": [{"name": "ds", "type": "constant"}]},
                  "panels": [{"datasource": {"uid": "$ds"}}]}, None,
                 id="a_variable_declared_as_a_constant_is_allowed"),
    # Provisioned Grafana matches by uid, so a name resolves to the default.
    pytest.param({"panels": [{"datasource": "Prometheus"}]}, "legacy string datasource",
                 id="a_legacy_string_datasource_fails"),
    pytest.param({"panels": [{"datasource": {"type": "prometheus"}}]}, "no `uid`",
                 id="a_datasource_dict_with_no_uid_fails"),
    pytest.param({"templating": DS_VAR,
                  "panels": [{"datasource": "-- Mixed --"}, {"datasource": "${ds}"}]}, None,
                 id="builtin_and_templated_string_datasources_pass"),
    # A row panel carries `datasource: null` legitimately.
    pytest.param({"panels": [{"type": "row", "datasource": None}]}, None,
                 id="a_null_datasource_is_ignored"),
])
def test_dashboard_content(tmp_path, doc, needle):
    raw = doc if isinstance(doc, str) else None
    findings = _check(_dir(tmp_path, doc=None if raw else doc, raw=raw))
    if needle is None:
        assert findings == []
    else:
        assert any(needle in f for f in findings), findings


def test_an_unregistered_file_fails(tmp_path):
    directory = _dir(tmp_path)
    (directory / "orphan.json").write_text(json.dumps(GOOD), encoding="utf-8")
    assert any("in no configMapGenerator" in f for f in _check(directory))


def test_a_registered_file_that_does_not_exist_fails(tmp_path):
    directory = _dir(tmp_path)
    (directory / "kustomization.yaml").write_text(
        KUSTOMIZATION.replace("      - example.json",
                              "      - example.json\n      - gone.json"),
        encoding="utf-8",
    )
    assert any("does not exist" in f for f in _check(directory))


def test_a_subdirectory_source_is_registration(tmp_path):
    """A `key=sub/file.json` source is a real file, not a missing one."""
    directory = _dir(tmp_path, kustomization=KUSTOMIZATION.replace(
        "      - example.json", "      - example.json\n      - sub/extra.json"))
    (directory / "sub").mkdir()
    (directory / "sub" / "extra.json").write_text(json.dumps(GOOD), encoding="utf-8")
    assert _check(directory) == []


def test_a_subdirectory_dashboard_is_content_checked(tmp_path):
    """A registered subdirectory dashboard must not ship unvalidated."""
    directory = _dir(tmp_path, kustomization=KUSTOMIZATION.replace(
        "      - example.json", "      - example.json\n      - sub/extra.json"))
    (directory / "sub").mkdir()
    (directory / "sub" / "extra.json").write_text(
        json.dumps({"panels": [{"datasource": {"uid": "thanos"}}]}), encoding="utf-8")
    findings = _check(directory)
    assert any("not provisioned" in f for f in findings), findings
    assert gate.main([str(directory)]) == 1


def test_a_directory_without_a_kustomization_fails(tmp_path):
    assert any("nothing ships" in f for f in _check(_dir(tmp_path, kustomization=None)))


def test_scanning_nothing_is_an_operator_error(tmp_path, monkeypatch):
    empty = tmp_path / "dashboards"
    empty.mkdir()
    (empty / "kustomization.yaml").write_text("---\n", encoding="utf-8")
    assert gate.main([str(empty)]) == 2


def test_a_missing_directory_is_an_operator_error(tmp_path):
    assert gate.main([str(tmp_path / "nope")]) == 2


def test_one_missing_directory_among_several_is_named(tmp_path, capsys):
    good = _dir(tmp_path)
    assert gate.main([str(good), str(tmp_path / "nope")]) == 2
    assert "nope" in capsys.readouterr().err


GENERATOR_OPTIONS = """---
generatorOptions:
  disableNameSuffixHash: true
  labels:
    grafana_dashboard: "1"
configMapGenerator:
  - name: grafana-dashboard-example
    files:
      - example.json
    options:
      annotations:
        grafana_folder: "Infrastructure"
"""


@pytest.mark.parametrize("kustomization,needle", [
    pytest.param(KUSTOMIZATION.replace('        grafana_dashboard: "1"\n', ""),
                 "sidecar never picks", id="a_missing_sidecar_label_fails"),
    pytest.param(KUSTOMIZATION.replace('        grafana_folder: "Infrastructure"\n', ""),
                 "default folder", id="a_missing_folder_annotation_fails"),
    # kustomize's `key=path` form names the ConfigMap key, not the file.
    pytest.param(KUSTOMIZATION.replace("      - example.json", "      - board.json=./example.json"),
                 None, id="a_key_equals_path_entry_is_registration"),
    pytest.param(GENERATOR_OPTIONS.replace('  labels:\n    grafana_dashboard: "1"\n', ""),
                 "sidecar never picks", id="a_label_in_neither_place_still_fails"),
    pytest.param(GENERATOR_OPTIONS.replace(
        "      annotations:", '      labels:\n        grafana_dashboard: "0"\n      annotations:'),
                 "sidecar never picks", id="the_entry_wins_over_the_file_level_label"),
])
def test_kustomization_registration(tmp_path, kustomization, needle):
    findings = _check(_dir(tmp_path, kustomization=kustomization))
    if needle is None:
        assert findings == []
    else:
        assert any(needle in f for f in findings), findings


def test_file_level_generator_options_supply_the_label(tmp_path):
    directory = _dir(tmp_path, kustomization=GENERATOR_OPTIONS)
    assert _check(directory) == []
    assert gate.main([str(directory)]) == 0


def test_main_returns_one_on_a_finding(tmp_path):
    assert gate.main([str(_dir(tmp_path, raw="{nope"))]) == 1


class TestCommandLine:
    """The documented flags are driven through main(), not only check_dir()."""

    def _thanos(self, tmp_path: Path) -> Path:
        doc = {
            "uid": "example",
            "panels": [{"datasource": {"type": "prometheus", "uid": "thanos"},
                        "targets": [{"expr": "up"}]}],
        }
        return _dir(tmp_path, doc=doc)

    def test_allowed_uid_is_repeatable_and_replaces_the_default(self, tmp_path):
        directory = self._thanos(tmp_path)
        assert gate.main([str(directory)]) == 1
        assert gate.main([str(directory), "--allowed-uid", "thanos"]) == 0

    def test_the_default_glob_finds_a_dashboards_directory(self, tmp_path, monkeypatch):
        root = tmp_path / "kubernetes" / "observability"
        root.mkdir(parents=True)
        _dir(root)
        monkeypatch.chdir(tmp_path)
        assert gate.main([]) == 0

    def test_the_default_glob_with_no_dashboards_is_an_operator_error(
        self, tmp_path, monkeypatch
    ):
        (tmp_path / "kubernetes").mkdir()
        monkeypatch.chdir(tmp_path)
        assert gate.main([]) == 2


class TestOperatorErrors:
    """Unreadable input is exit 2, never exit 1 (a dashboard violation)."""

    def test_an_unparseable_kustomization_is_an_operator_error(self, tmp_path, capsys):
        directory = _dir(tmp_path, kustomization="configMapGenerator: [\n")
        assert gate.main([str(directory)]) == 2
        assert "ERROR:" in capsys.readouterr().err

    def test_a_non_utf8_dashboard_is_an_operator_error(self, tmp_path, capsys):
        directory = _dir(tmp_path)
        (directory / "example.json").write_bytes(b"\xff\xfe{")
        assert gate.main([str(directory)]) == 2
        assert "ERROR:" in capsys.readouterr().err
