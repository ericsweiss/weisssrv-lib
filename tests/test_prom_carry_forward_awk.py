"""The metrics carry-forward reads exactly one value from a .prom file.

A textfile with two lines for the same series would otherwise make the awk emit
both, and node_exporter rejects the whole file on the malformed result.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ROLES = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles"

# template -> the metric name the carry-forward reads, with ${prefix} resolved.
TEMPLATES = {
    "acme_certs/templates/homelab-cert-reload.sh.j2": "cert_renewal_last_success_timestamp_seconds",
    "nas_storage/templates/swap-clean.sh.j2": "swap_clean_last_success_timestamp_seconds",
    "compose_app/templates/write_prom_metrics.sh.j2": "myapp_last_success_timestamp_seconds",
    "restic_offsite/templates/restic-offsitectl.sh.j2": "restic_offsite_last_success_timestamp_seconds",
}

AWK = re.compile(r"""awk -v (?:k|n)=("?[^ ]+?"?) '(.+?)' "\$\{?\w+\}?\"""")


def _awk_program(template: str) -> str:
    body = (ROLES / template).read_text(encoding="utf-8")
    matches = AWK.findall(body)
    assert matches, "no carry-forward awk found in %s" % template
    names = {m[0] for m in matches}
    programs = {m[1] for m in matches}
    assert len(programs) == 1, "%s uses inconsistent carry-forward awk: %s" % (template, programs)
    assert names, names
    return programs.pop()


def _read(template: str, prom: Path, name: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["awk", "-v", "k=%s" % name, "-v", "n=%s" % name, _awk_program(template), str(prom)],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("template,name", sorted(TEMPLATES.items()))
def test_a_single_line_is_carried_forward(tmp_path, template, name) -> None:
    prom = tmp_path / "metrics.prom"
    prom.write_text("# HELP %s x\n%s 111\n" % (name, name), encoding="utf-8")
    proc = _read(template, prom, name)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == ["111"]


@pytest.mark.parametrize("template,name", sorted(TEMPLATES.items()))
def test_a_duplicated_line_still_carries_one_value(tmp_path, template, name) -> None:
    prom = tmp_path / "metrics.prom"
    prom.write_text("%s 111\n%s 222\n" % (name, name), encoding="utf-8")
    proc = _read(template, prom, name)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == ["222"], proc.stdout


@pytest.mark.parametrize("template,name", sorted(TEMPLATES.items()))
def test_an_absent_series_carries_nothing(tmp_path, template, name) -> None:
    prom = tmp_path / "metrics.prom"
    prom.write_text("other_metric 1\n", encoding="utf-8")
    proc = _read(template, prom, name)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
