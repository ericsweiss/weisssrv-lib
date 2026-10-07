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
    return gate.check_dir(directory, {"prometheus", "loki"},
                          "grafana_dashboard", "1", "grafana_folder")[0]


def test_a_correct_directory_passes(tmp_path):
    assert _check(_dir(tmp_path)) == []


def test_malformed_json_fails(tmp_path):
    assert any("not valid JSON" in f for f in _check(_dir(tmp_path, raw="{nope")))


def test_an_export_for_sharing_file_fails(tmp_path):
    doc = dict(GOOD, __inputs=[{"name": "DS_PROMETHEUS"}])
    assert any("__inputs" in f for f in _check(_dir(tmp_path, doc=doc)))


def test_a_surviving_import_placeholder_fails(tmp_path):
    doc = {"panels": [{"datasource": {"uid": "${DS_PROMETHEUS}"}}]}
    assert any("placeholder" in f for f in _check(_dir(tmp_path, doc=doc)))


def test_an_unprovisioned_datasource_uid_fails(tmp_path):
    doc = {"panels": [{"datasource": {"type": "prometheus", "uid": "thanos"}}]}
    assert any("not provisioned" in f for f in _check(_dir(tmp_path, doc=doc)))


def test_a_dashboard_uid_is_not_mistaken_for_a_datasource_uid(tmp_path):
    """Top-level `uid` names the dashboard and is free-form."""
    assert _check(_dir(tmp_path, doc=dict(GOOD, uid="anything-at-all"))) == []


def test_a_declared_templated_datasource_variable_is_allowed(tmp_path):
    doc = {
        "templating": {"list": [{"name": "datasource", "type": "datasource"}]},
        "panels": [{"datasource": {"uid": "${datasource}"}}],
    }
    assert _check(_dir(tmp_path, doc=doc)) == []


def test_an_undeclared_templated_datasource_variable_fails(tmp_path):
    """A renamed or mistyped variable renders as 'Datasource not found'."""
    doc = {"panels": [{"datasource": {"uid": "$ds"}}]}
    assert any("templating.list" in f for f in _check(_dir(tmp_path, doc=doc)))


def test_a_variable_declared_as_a_constant_is_allowed(tmp_path):
    """A dashboard may drive its datasource from any declared variable."""
    doc = {
        "templating": {"list": [{"name": "ds", "type": "constant"}]},
        "panels": [{"datasource": {"uid": "$ds"}}],
    }
    assert _check(_dir(tmp_path, doc=doc)) == []


def test_a_legacy_string_datasource_fails(tmp_path):
    """Provisioned Grafana matches by uid, so a name resolves to the default."""
    doc = {"panels": [{"datasource": "Prometheus"}]}
    assert any("legacy string datasource" in f for f in _check(_dir(tmp_path, doc=doc)))


def test_a_datasource_dict_with_no_uid_fails(tmp_path):
    doc = {"panels": [{"datasource": {"type": "prometheus"}}]}
    assert any("no `uid`" in f for f in _check(_dir(tmp_path, doc=doc)))


def test_builtin_and_templated_string_datasources_pass(tmp_path):
    doc = {
        "templating": {"list": [{"name": "ds", "type": "datasource"}]},
        "panels": [{"datasource": "-- Mixed --"}, {"datasource": "${ds}"}],
    }
    assert _check(_dir(tmp_path, doc=doc)) == []


def test_a_null_datasource_is_ignored(tmp_path):
    """A row panel carries `datasource: null` legitimately."""
    doc = {"panels": [{"type": "row", "datasource": None}]}
    assert _check(_dir(tmp_path, doc=doc)) == []


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


def test_a_key_equals_path_entry_is_registration(tmp_path):
    """kustomize's `key=path` form names the ConfigMap key, not the file."""
    directory = _dir(tmp_path, kustomization=KUSTOMIZATION.replace(
        "      - example.json", "      - board.json=./example.json"))
    assert _check(directory) == []


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


def test_a_missing_sidecar_label_fails(tmp_path):
    directory = _dir(tmp_path, kustomization=KUSTOMIZATION.replace(
        '        grafana_dashboard: "1"\n', ""))
    assert any("sidecar never picks" in f for f in _check(directory))


def test_a_missing_folder_annotation_fails(tmp_path):
    directory = _dir(tmp_path, kustomization=KUSTOMIZATION.replace(
        '        grafana_folder: "Infrastructure"\n', ""))
    assert any("default folder" in f for f in _check(directory))


def test_a_directory_without_a_kustomization_fails(tmp_path):
    assert any("nothing ships" in f for f in _check(_dir(tmp_path, kustomization=None)))


def test_a_key_prefixed_generator_entry_is_understood(tmp_path):
    directory = _dir(tmp_path, kustomization=KUSTOMIZATION.replace(
        "      - example.json", "      - example.json=example.json"))
    assert _check(directory) == []


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


def test_file_level_generator_options_supply_the_label(tmp_path):
    directory = _dir(tmp_path, kustomization=GENERATOR_OPTIONS)
    assert _check(directory) == []
    assert gate.main([str(directory)]) == 0


def test_a_label_in_neither_place_still_fails(tmp_path):
    directory = _dir(tmp_path, kustomization=GENERATOR_OPTIONS.replace(
        '  labels:\n    grafana_dashboard: "1"\n', ""))
    assert any("sidecar never picks" in f for f in _check(directory))
    assert gate.main([str(directory)]) == 1


def test_the_entry_wins_over_the_file_level_label(tmp_path):
    directory = _dir(tmp_path, kustomization=GENERATOR_OPTIONS.replace(
        "      annotations:",
        '      labels:\n        grafana_dashboard: "0"\n      annotations:'))
    assert any("sidecar never picks" in f for f in _check(directory))


def test_main_returns_one_on_a_finding(tmp_path):
    assert gate.main([str(_dir(tmp_path, raw="{nope"))]) == 1


def test_skip_registration_lints_a_source_directory(tmp_path):
    """A directory holding dashboards as source has no kustomization beside them."""
    directory = _dir(tmp_path, kustomization=None)
    assert gate.check_dir(directory, {"prometheus", "loki"}, "grafana_dashboard",
                          "1", "grafana_folder", check_registration=False)[0] == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))


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

    def test_skip_registration_drops_the_kustomization_requirement(self, tmp_path):
        directory = _dir(tmp_path, kustomization=None)
        assert gate.main([str(directory)]) == 1
        assert gate.main([str(directory), "--skip-registration"]) == 0

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

    def test_the_folder_annotation_is_configurable(self, tmp_path):
        """A chart annotating the folder under its own key must be able to pass."""
        directory = _dir(
            tmp_path,
            kustomization=KUSTOMIZATION.replace("grafana_folder", "custom_folder"),
        )
        assert gate.main([str(directory)]) == 1
        assert gate.main([str(directory), "--folder-annotation", "custom_folder"]) == 0

    def test_the_sidecar_label_value_is_configurable(self, tmp_path):
        """A chart setting `labelValue: "true"` must be able to pass the gate."""
        directory = _dir(
            tmp_path, kustomization=KUSTOMIZATION.replace('"1"', '"true"')
        )
        assert gate.main([str(directory)]) == 1
        assert gate.main([str(directory), "--dashboard-label-value", "true"]) == 0


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
