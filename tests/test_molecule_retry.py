#!/usr/bin/env python3
"""Tests for scripts/molecule-retry.sh — the pass/fail wrapper every molecule job
runs, so a regression that loses `exit "$rc"` would turn the whole suite green.

A `molecule` stub on PATH decides the outcome and records its arguments; a
`sleep` stub keeps the 20-65s jitter window out of the test runtime.

Run with pytest:
    python3 -m pytest tests/test_molecule_retry.py -v
"""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "molecule-retry.sh"

FAKE_MOLECULE = """\
#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$MOL_LOG"
for arg in "$@"; do
    [ "$arg" = "destroy" ] && exit 0
done
attempts=$(cat "$MOL_COUNT" 2>/dev/null || echo 0)
attempts=$((attempts + 1))
echo "$attempts" > "$MOL_COUNT"
if [ "$attempts" -le "${MOL_FAIL_UNTIL:-0}" ]; then
    exit "${MOL_FAIL_RC:-1}"
fi
exit 0
"""

FAKE_SLEEP = "#!/usr/bin/env bash\nexit 0\n"


@pytest.fixture()
def run(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("molecule", FAKE_MOLECULE), ("sleep", FAKE_SLEEP)):
        stub = bin_dir / name
        stub.write_text(body)
        stub.chmod(0o755)
    log = tmp_path / "molecule.log"
    log.write_text("")

    def _run(**env):
        proc = subprocess.run(
            ["bash", str(SCRIPT)],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            env={
                **os.environ,
                "PATH": "%s:%s" % (bin_dir, os.environ["PATH"]),
                "MOL_LOG": str(log),
                "MOL_COUNT": str(tmp_path / "count"),
                **{k: str(v) for k, v in env.items()},
            },
        )
        return proc, log.read_text().split()

    return _run


def test_passes_on_the_first_attempt(run):
    proc, calls = run(MOL_SCEN="-s default")
    assert proc.returncode == 0
    assert calls == ["test", "-s", "default"]


def test_retries_after_a_failure_and_then_passes(run):
    proc, calls = run(MOL_FAIL_UNTIL=1)
    assert proc.returncode == 0
    assert calls == ["test", "destroy", "test"]
    assert "destroying + retrying (2/4)" in proc.stdout


def test_exits_with_molecules_code_after_exhausting_the_retries(run):
    proc, calls = run(MOL_FAIL_UNTIL=9, MOL_FAIL_RC=3, MOL_MAX=2)
    assert proc.returncode == 3
    assert calls == ["test", "destroy", "test"]


def test_clears_the_failed_attempts_junit_xml(run, tmp_path):
    junit = tmp_path / "junit"
    junit.mkdir()
    (junit / "attempt-1.xml").write_text("<testsuite/>")
    proc, _ = run(MOL_FAIL_UNTIL=1, JUNIT_OUTPUT_DIR=str(junit))
    assert proc.returncode == 0
    # Only the deciding attempt may reach the pipeline's test report.
    assert list(junit.iterdir()) == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
