"""scripts/run-render-gates.sh — ordering, stdin contract and the failure paths."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "run-render-gates.sh"
EXAMPLE = REPO / "examples" / "render-gates.example.conf"


def _run(tmp_path: Path, config: str, corpus: str = "kind: ConfigMap\n", **env):
    conf = tmp_path / "render-gates.conf"
    conf.write_text(config, encoding="utf-8")
    render_all = tmp_path / "corpus.yaml"
    render_all.write_text(corpus, encoding="utf-8")
    environment = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "RENDER_ALL": str(render_all),
    }
    environment.update(env)
    return subprocess.run(
        ["bash", str(SCRIPT), "--config", str(conf)],
        env=environment, capture_output=True, text=True, cwd=tmp_path,
    )


def test_every_passing_gate_gives_exit_zero(tmp_path):
    proc = _run(tmp_path, "One :: cat > /dev/null\nTwo :: true\n")
    assert proc.returncode == 0, proc.stderr
    assert "render gates OK (2 gate(s))" in proc.stdout


def test_gates_run_in_file_order_with_their_banners(tmp_path):
    proc = _run(tmp_path, "First :: true\nSecond :: true\n")
    assert proc.stdout.index("=== First ===") < proc.stdout.index("=== Second ===")


def test_a_failing_gate_fails_the_run_but_the_rest_still_run(tmp_path):
    proc = _run(tmp_path, "Bad :: false\nGood :: echo reached\n")
    assert proc.returncode == 1
    assert "reached" in proc.stdout
    assert "render gates FAILED" in proc.stderr


def test_a_gate_exiting_two_is_an_operator_error_not_a_finding(tmp_path):
    """rc 2 means the gate inspected nothing; collapsing it into rc 1 would read
    as a policy violation."""
    proc = _run(tmp_path, "Broken ::! sh -c 'exit 2'\n")
    assert proc.returncode == 2
    assert "OPERATOR ERROR" in proc.stderr


def test_an_operator_error_outranks_a_finding_in_the_same_run(tmp_path):
    proc = _run(tmp_path, "Finding :: false\nBroken ::! sh -c 'exit 2'\n")
    assert proc.returncode == 2


def test_a_gate_exiting_one_is_still_a_finding(tmp_path):
    proc = _run(tmp_path, "Finding ::! sh -c 'exit 1'\n")
    assert proc.returncode == 1
    assert "render gates FAILED" in proc.stderr


def test_the_corpus_arrives_on_stdin(tmp_path):
    proc = _run(tmp_path, "Read :: grep -q ConfigMap\n")
    assert proc.returncode == 0, proc.stderr


def test_a_bang_entry_gets_no_stdin_redirect(tmp_path):
    """The shape a step that APPENDS to the corpus needs."""
    proc = _run(tmp_path, 'Append ::! printf "kind: Secret\\n" >> "$RENDER_ALL"\n'
                          "Read :: grep -q Secret\n")
    assert proc.returncode == 0, proc.stderr


def test_a_bang_gate_that_drains_stdin_does_not_eat_the_gate_list(tmp_path):
    """The config is read on fd 3, so a gate's own stdin is not the gate list."""
    proc = _run(tmp_path, "Drain ::! cat >/dev/null\n"
                          "Second ::! echo second-gate-ran\n")
    assert proc.returncode == 0, proc.stderr
    assert "second-gate-ran" in proc.stdout
    assert "render gates OK (2 gate(s))" in proc.stdout


def test_comments_and_blank_lines_are_ignored(tmp_path):
    proc = _run(tmp_path, "# a comment\n\nOne :: true\n")
    assert "1 gate(s)" in proc.stdout


def test_an_empty_gate_list_is_an_operator_error(tmp_path):
    assert _run(tmp_path, "# nothing but a comment\n").returncode == 2


def test_a_missing_config_is_an_operator_error(tmp_path):
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--config", str(tmp_path / "absent.conf")],
        env={"PATH": "/usr/bin:/bin", "RENDER_ALL": "/etc/hostname"},
        capture_output=True, text=True,
    )
    assert proc.returncode == 2


def test_an_empty_corpus_is_an_operator_error(tmp_path):
    assert _run(tmp_path, "One :: true\n", corpus="").returncode == 2


def test_a_line_with_no_command_is_an_operator_error(tmp_path):
    assert _run(tmp_path, "Label ::\n").returncode == 2


def test_flux_env_without_a_versions_configmap_is_an_operator_error(tmp_path):
    conf = tmp_path / "render-gates.conf"
    conf.write_text("One :: true\n", encoding="utf-8")
    corpus = tmp_path / "corpus.yaml"
    corpus.write_text("kind: ConfigMap\n", encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--config", str(conf), "--flux-env", "scripts/x.sh"],
        env={"PATH": "/usr/bin:/bin", "RENDER_ALL": str(corpus)},
        capture_output=True, text=True,
    )
    assert proc.returncode == 2
    assert "VERSIONS_CONFIGMAP" in proc.stderr


def test_merged_cm_is_built_and_exported(tmp_path):
    flux_env = tmp_path / "flux-env.sh"
    flux_env.write_text('#!/bin/sh\necho "merged from $2"\n', encoding="utf-8")
    conf = tmp_path / "render-gates.conf"
    conf.write_text('Check ::! grep -q "merged from" "$MERGED_CM"\n', encoding="utf-8")
    corpus = tmp_path / "corpus.yaml"
    corpus.write_text("kind: ConfigMap\n", encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--config", str(conf), "--flux-env", str(flux_env)],
        env={"PATH": "/usr/bin:/bin", "RENDER_ALL": str(corpus),
             "VERSIONS_CONFIGMAP": "versions.yaml"},
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr


def _run_with_flux_env(tmp_path: Path, script: str):
    flux_env = tmp_path / "flux-env.sh"
    flux_env.write_text(script, encoding="utf-8")
    conf = tmp_path / "render-gates.conf"
    conf.write_text("Check ::! true\n", encoding="utf-8")
    corpus = tmp_path / "corpus.yaml"
    corpus.write_text("kind: ConfigMap\n", encoding="utf-8")
    return subprocess.run(
        ["bash", str(SCRIPT), "--config", str(conf), "--flux-env", str(flux_env)],
        env={"PATH": "/usr/bin:/bin", "RENDER_ALL": str(corpus),
             "VERSIONS_CONFIGMAP": "versions.yaml"},
        capture_output=True, text=True, cwd=tmp_path,
    )


def test_a_failing_flux_env_is_an_operator_error(tmp_path):
    proc = _run_with_flux_env(tmp_path, "#!/bin/sh\nexit 7\n")
    assert proc.returncode == 2
    assert "could not build a merged configmap" in proc.stderr


def test_an_empty_merged_configmap_is_an_operator_error(tmp_path):
    """A gate resolving ${...} from an empty configmap substitutes nothing."""
    proc = _run_with_flux_env(tmp_path, "#!/bin/sh\nexit 0\n")
    assert proc.returncode == 2
    assert "empty merged configmap" in proc.stderr


def test_help_prints_usage_and_no_source_code():
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--help"],
        env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Usage: run-render-gates.sh" in proc.stdout
    assert "set -" not in proc.stdout
    assert "CONFIG=" not in proc.stdout
    assert "case " not in proc.stdout


@pytest.mark.parametrize("flag", ["--config", "--flux-env"])
def test_an_option_with_no_value_exits_two(flag):
    """A trailing flag must fail fast, not loop on the failed shift."""
    proc = subprocess.run(
        ["bash", str(SCRIPT), flag],
        env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True,
        timeout=10,
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert f"{flag} needs a value" in proc.stderr


def test_the_shipped_example_config_parses(tmp_path):
    """Every line of the example is a label/command pair the runner accepts."""
    lines = [
        ln for ln in EXAMPLE.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    assert lines, "the example ships no gate line"

    rewritten = []
    for line in lines:
        label, _, body = line.partition("::")
        rewritten.append(
            f"{label}::! true" if body.lstrip().startswith("!") else f"{label}:: true"
        )
    flux_env = tmp_path / "flux-env.sh"
    flux_env.write_text('#!/bin/sh\necho "merged from $2"\n', encoding="utf-8")
    conf = tmp_path / "render-gates.conf"
    conf.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
    corpus = tmp_path / "corpus.yaml"
    corpus.write_text("kind: ConfigMap\n", encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--config", str(conf), "--flux-env", str(flux_env)],
        env={"PATH": "/usr/bin:/bin", "RENDER_ALL": str(corpus),
             "VERSIONS_CONFIGMAP": "versions.yaml"},
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"{len(lines)} gate" in proc.stdout


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))


# Consumer-supplied YAML arguments, not scripts this repo ships.
CONSUMER_SUPPLIED = {
    "scripts/autoscaling-policy.yaml",
    "scripts/helm-values-releases.yaml",
}


def test_every_script_the_example_names_exists():
    """A renamed gate would otherwise leave the example pointing at nothing."""
    text = EXAMPLE.read_text(encoding="utf-8")
    named = set(re.findall(r"scripts/[A-Za-z0-9_.-]+\.(?:py|sh)", text))
    missing = sorted(
        token for token in named
        if token not in CONSUMER_SUPPLIED and not (REPO / token).is_file()
    )
    assert missing == [], f"the example names scripts that do not exist: {missing}"


def test_every_flag_the_example_passes_is_accepted_by_its_script():
    text = EXAMPLE.read_text(encoding="utf-8")
    problems = []
    for line in text.splitlines():
        match = re.search(r"(scripts/[A-Za-z0-9_.-]+\.py)(.*)$", line)
        if not match or (REPO / match.group(1)).suffix != ".py":
            continue
        source = (REPO / match.group(1)).read_text(encoding="utf-8")
        for flag in re.findall(r"(?<![\w-])--[a-z][a-z0-9-]+", match.group(2)):
            if flag not in source:
                problems.append(f"{match.group(1)} does not accept {flag}")
    assert problems == [], problems
