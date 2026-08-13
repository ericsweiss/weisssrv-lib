#!/usr/bin/env python3
"""Tests for scripts/lint-prometheus-config.sh — the wrapper that turns
extract-prometheus-config.py plus promtool/amtool into one gate. Its job is to
be non-zero on any failure; a regression that swallowed one would leave broken
PromQL or an invalid Alertmanager config green all the way to the cluster.

`promtool`, `amtool` and the extractor are stubs on a controlled PATH, so no
Prometheus tooling is needed to run this.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "lint-prometheus-config.sh"
BASH = shutil.which("bash") or "/bin/bash"

# The script's only external needs beyond the tools under test. Symlinked into
# the stub dir so PATH can be *just* that dir — which is what makes the
# "tool missing" branch testable at all.
PASSTHROUGH = ("mktemp", "rm", "cp", "mkdir")

# Records the subcommand, and on `test rules` dumps the rules file the script
# generated alongside the unit test so the annotation strip can be asserted.
FAKE_PROMTOOL = """\
#!%s
printf 'promtool %%s\\n' "$*" >> "$TRACE"
if [ "$1" = "test" ]; then
    cp "${3%%/*}/rules.yaml" "$STRIPPED"
fi
exit "${PROMTOOL_RC:-0}"
""" % BASH

FAKE_AMTOOL = """\
#!%s
printf 'amtool %%s\\n' "$*" >> "$TRACE"
exit "${AMTOOL_RC:-0}"
""" % BASH

# Stands in for extract-prometheus-config.py: records its argv, writes the file
# the caller asked for.
FAKE_EXTRACT = '''\
import sys

with open(sys.argv[0] + ".trace", "a") as handle:
    handle.write(" ".join(sys.argv[1:]) + "\\n")

if sys.argv[1] == "rules":
    body = """groups:
- name: demo
  rules:
  - alert: Demo
    expr: up == 0
    annotations:
      description: churn-prone prose
"""
else:
    body = "route:\\n  receiver: default\\n"
with open(sys.argv[2], "w") as handle:
    handle.write(body)
'''

RULE_TEST = """\
rule_files:
  - rules.yaml
tests:
  - interval: 1m
    alert_rule_test: []
"""


@pytest.fixture()
def run(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in PASSTHROUGH:
        real = shutil.which(name)
        assert real, "%s is required to run this suite" % name
        (bin_dir / name).symlink_to(real)
    (bin_dir / "python3").symlink_to(sys.executable)
    for name, body in (("promtool", FAKE_PROMTOOL), ("amtool", FAKE_AMTOOL)):
        stub = bin_dir / name
        stub.write_text(body)
        stub.chmod(0o755)

    extract = tmp_path / "extract.py"
    extract.write_text(FAKE_EXTRACT)
    tests_dir = tmp_path / "rule-tests"
    tests_dir.mkdir()
    trace = tmp_path / "trace"
    trace.write_text("")
    stripped = tmp_path / "stripped.yaml"

    def _run(*, drop=(), **env):
        for name in drop:
            (bin_dir / name).unlink()
        proc = subprocess.run(
            [BASH, str(SCRIPT)],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            env={
                "PATH": str(bin_dir),
                # The script's python3 heredoc imports yaml; in CI PyYAML is
                # user-site-installed, and user-site resolution needs HOME
                # (the runner uid has no passwd entry to fall back to).
                "HOME": os.environ.get("HOME", str(tmp_path)),
                **(
                    {"PYTHONPATH": os.environ["PYTHONPATH"]}
                    if "PYTHONPATH" in os.environ
                    else {}
                ),
                "TRACE": str(trace),
                "STRIPPED": str(stripped),
                "EXTRACT_SCRIPT": str(extract),
                "RULE_TESTS_DIR": str(tests_dir),
                **{k: str(v) for k, v in env.items()},
            },
        )
        return proc, trace.read_text().splitlines()

    _run.tests_dir = tests_dir
    _run.extract_trace = tmp_path / "extract.py.trace"
    _run.stripped = stripped
    return _run


def test_checks_rules_and_alertmanager_and_skips_absent_unit_tests(run):
    proc, calls = run()
    assert proc.returncode == 0
    assert [c.split()[0:2] for c in calls] == [["promtool", "check"], ["amtool", "check-config"]]
    assert "skipping promtool alert unit tests" in proc.stdout
    assert "Prometheus rules + Alertmanager config are valid." in proc.stdout


def test_runs_the_alert_unit_tests_against_annotation_stripped_rules(run):
    (run.tests_dir / "alerts.test.yaml").write_text(RULE_TEST)
    proc, calls = run()
    assert proc.returncode == 0
    assert any(c.startswith("promtool test rules ") for c in calls)
    # Unit tests assert alert LOGIC; leaving annotations in makes description
    # prose a test dependency, so the copy they run against has them removed.
    stripped = run.stripped.read_text()
    assert "alert: Demo" in stripped
    assert "annotations" not in stripped
    assert "alert unit tests pass" in proc.stdout


def test_the_manifest_overrides_reach_the_extractor(run):
    run(HELM_RELEASE="kube-prometheus-stack.yaml", AM_CONFIG="alertmanager-secret.yaml")
    argv = run.extract_trace.read_text().splitlines()
    assert argv[0].startswith("rules ") and argv[0].endswith("--release kube-prometheus-stack.yaml")
    assert argv[1].startswith("alertmanager ") and argv[1].endswith("--am-config alertmanager-secret.yaml")


@pytest.mark.parametrize("tool", ["promtool", "amtool"])
def test_a_missing_tool_fails_before_any_extraction(run, tool):
    proc, calls = run(drop=(tool,))
    assert proc.returncode == 1
    assert f"{tool} not found on PATH" in proc.stderr
    assert calls == []


def test_a_rules_check_failure_is_not_swallowed(run):
    proc, _ = run(PROMTOOL_RC=1)
    assert proc.returncode != 0
    assert "valid" not in proc.stdout


def test_an_alertmanager_check_failure_is_not_swallowed(run):
    proc, _ = run(AMTOOL_RC=1)
    assert proc.returncode != 0
    assert "valid" not in proc.stdout


def test_an_extraction_failure_is_not_swallowed(run, tmp_path):
    broken = tmp_path / "broken.py"
    broken.write_text("import sys\nsys.exit(3)\n")
    proc, calls = run(EXTRACT_SCRIPT=str(broken))
    assert proc.returncode != 0
    assert calls == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
