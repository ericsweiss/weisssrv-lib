"""Metric semantics and check mode of the acme_certs distribution script.

The rendered script runs against a stubbed `ssh`, because what the gauges encode
lives in the exit paths: a dead target is not a failed renewal.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import jinja2
import pytest
import yaml
from _helpers import ansible_env

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "acme_certs"
TEMPLATE = ROLE / "templates" / "homelab-cert-reload.sh.j2"
RECEIVER = ROLE / "templates" / "cert-receive.sh.j2"
UNITS = ("homelab-cert-check.service", "homelab-cert-check.timer")

# The role's own defaults are the context: a hand-written stand-in would let a
# renamed default pass here while the real render fails.
DEFAULTS = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())

# A sudo target, so distribution goes through the forced-command receiver and
# the stubbed ssh is the only remote hop.
TARGET = {
    "host": "dns-01.example.test",
    "ip": "10.0.0.160",
    "cert_dir": "/opt/AdGuardHome/certs",
    "owner": "root",
    "group": "adguard",
    "cert_mode": "0644",
    "key_mode": "0640",
    "restart_service": "AdGuardHome",
}

LOCAL_HOST = "cert-01.example.test"
TARGET_GAUGE = 'cert_distribution_target_last_run_success{host="%s"}'
BUNDLE_DELIM = "CERT-RECEIVE-BUNDLE-BOUNDARY"

# A target file left by an earlier run, naming a host the inventory does not
# carry any more.
RETIRED_HOST = "retired.example.test"
STALE_TARGETS = (
    "# TYPE cert_distribution_target_last_run_success gauge\n"
    + TARGET_GAUGE % RETIRED_HOST
    + " 0\n"
    + "cert_distribution_last_run_failed_targets 1\n"
)


def _context(**overrides: object) -> dict:
    context = {k: v for k, v in DEFAULTS.items() if isinstance(v, (str, int, bool))}
    context.update({"ansible_managed": "Ansible managed", "inventory_hostname": LOCAL_HOST})
    context.update(overrides)
    return context


def _env() -> jinja2.Environment:
    return ansible_env(
        loader=jinja2.FileSystemLoader(str(TEMPLATE.parent)),
        keep_trailing_newline=True,
    )


def _render(
    tmp_path: Path, *, local_reload_command: str, local_cert: bool, targets: tuple[dict, ...]
) -> tuple[Path, Path]:
    prom_dir = tmp_path / "textfile"
    cert_dir = tmp_path / "certs"
    cert_dir.mkdir(parents=True)
    if local_cert:
        (cert_dir / "fullchain.pem").write_text("not-a-cert\n", encoding="utf-8")
        (cert_dir / "privkey.pem").write_text("not-a-key\n", encoding="utf-8")
    context = _context(
        acme_certs_textfile_dir=str(prom_dir),
        acme_certs_local_cert_dir=str(cert_dir),
        acme_certs_ssh_key_path=str(tmp_path / "id_ed25519_certs"),
        acme_certs_distribution_targets=list(targets),
        acme_certs_local_reload_command=local_reload_command,
    )
    script = tmp_path / "homelab-cert-reload.sh"
    script.write_text(_env().get_template(TEMPLATE.name).render(**context), encoding="utf-8")
    return script, prom_dir


def _stub_bin(tmp_path: Path, *, ssh_ok: bool) -> Path:
    """`ssh` answers as the forced-command receiver does, refusing an empty
    bundle, and appends every byte it is sent to $CERT_STUB_STDIN. `sha256sum`
    stands in for the GNU binary a non-Linux test host lacks."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    receiver = (
        't=$(mktemp)\ncat > "$t"\ncat "$t" >> "$CERT_STUB_STDIN"\n'
        'if [ -s "$t" ]; then rm -f "$t"; echo OK; exit 0; fi\n'
        'rm -f "$t"\necho "FAIL: empty bundle" >&2\nexit 3\n'
    )
    unreachable = 'cat >> "$CERT_STUB_STDIN"\necho "Permission denied (publickey)." >&2\nexit 255\n'
    stubs = {
        "ssh": "#!/bin/sh\n%s" % (receiver if ssh_ok else unreachable),
        "scp": "#!/bin/sh\nexit %d\n" % (0 if ssh_ok else 1),
        "sha256sum": "#!/bin/sh\nprintf '%s  %s\\n' deadbeef \"$1\"\n",
        "logger": "#!/bin/sh\nexit 0\n",
    }
    for name, body in stubs.items():
        path = bin_dir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
    return bin_dir


class Run:
    """One execution of the rendered script, with what it published."""

    def __init__(self, proc, prom_dir: Path, sent: Path) -> None:
        self.proc = proc
        self.rc = proc.returncode
        self.renewal = _gauges(prom_dir / "cert_renewal.prom")
        self.targets = _gauges(prom_dir / "cert_distribution_targets.prom")
        self.renewal_raw = _read(prom_dir / "cert_renewal.prom")
        self.targets_raw = _read(prom_dir / "cert_distribution_targets.prom")
        self.sent = _read(sent)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _gauges(path: Path) -> dict[str, float]:
    gauges: dict[str, float] = {}
    for line in _read(path).splitlines():
        if not line or line.startswith("#"):
            continue
        name, _, value = line.rpartition(" ")
        gauges[name] = float(value)
    return gauges


def _run(
    tmp_path: Path,
    *,
    ssh_ok: bool = True,
    local_reload_command: str = "",
    local_cert: bool = True,
    args: tuple[str, ...] = (),
    env_extra: dict[str, str] | None = None,
    seed_renewal: str | None = None,
    seed_targets: str | None = None,
    targets: tuple[dict, ...] = (TARGET,),
) -> Run:
    script, prom_dir = _render(
        tmp_path,
        local_reload_command=local_reload_command,
        local_cert=local_cert,
        targets=targets,
    )
    for name, seed in (
        ("cert_renewal.prom", seed_renewal),
        ("cert_distribution_targets.prom", seed_targets),
    ):
        if seed is not None:
            prom_dir.mkdir(parents=True, exist_ok=True)
            (prom_dir / name).write_text(seed, encoding="utf-8")
    bin_dir = _stub_bin(tmp_path, ssh_ok=ssh_ok)
    sent = tmp_path / "sent-to-target"
    sent.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": "%s:%s" % (bin_dir, os.environ["PATH"]),
        "CERT_STUB_STDIN": str(sent),
        **(env_extra or {}),
    }
    proc = subprocess.run(
        ["bash", str(script), *args], capture_output=True, text=True, check=False, env=env
    )
    return Run(proc, prom_dir, sent)


def test_a_dead_target_leaves_the_renewal_bit_at_one(tmp_path) -> None:
    run = _run(tmp_path, ssh_ok=False)
    assert run.rc == 2, "a failed target must exit 2, not %d" % run.rc
    assert run.renewal["cert_renewal_last_run_success"] == 1
    assert run.targets["cert_distribution_last_run_failed_targets"] == 1
    assert run.targets[TARGET_GAUGE % TARGET["host"]] == 0


def test_every_target_ok_publishes_a_clean_run(tmp_path) -> None:
    run = _run(tmp_path)
    assert run.rc == 0, "a clean run must exit 0, not %d" % run.rc
    assert run.renewal["cert_renewal_last_run_success"] == 1
    assert run.targets["cert_distribution_last_run_failed_targets"] == 0
    assert run.targets[TARGET_GAUGE % TARGET["host"]] == 1
    assert BUNDLE_DELIM in run.sent, "a real run must send the cert bundle"


def test_a_missing_local_cert_fails_the_renewal_bit(tmp_path) -> None:
    run = _run(tmp_path, local_cert=False)
    assert run.rc == 1, "a hard local error must exit 1, not %d" % run.rc
    assert run.renewal["cert_renewal_last_run_success"] == 0
    assert run.targets == {}, "a local error before the loop published a target set"


def test_a_failed_local_reload_fails_the_renewal_bit(tmp_path) -> None:
    run = _run(tmp_path, local_reload_command="false")
    assert run.rc == 2
    assert run.renewal["cert_renewal_last_run_success"] == 0
    assert run.targets["cert_distribution_last_run_failed_targets"] == 1
    assert run.targets[TARGET_GAUGE % LOCAL_HOST] == 0


@pytest.mark.parametrize(
    "args,env_extra",
    [(("--check",), None), ((), {"HOMELAB_CERT_CHECK": "1"})],
    ids=["flag", "env"],
)
def test_check_mode_probes_without_pushing(tmp_path, args, env_extra) -> None:
    run = _run(tmp_path, args=args, env_extra=env_extra)
    assert run.rc == 0, run.proc.stderr
    assert BUNDLE_DELIM not in run.sent, "check mode must not send the cert bundle"
    assert run.renewal == {}, "check mode must not write cert_renewal.prom"
    assert run.targets[TARGET_GAUGE % TARGET["host"]] == 1
    assert run.targets["cert_distribution_last_run_failed_targets"] == 0


def test_check_mode_never_runs_the_local_reload(tmp_path) -> None:
    marker = tmp_path / "reloaded"
    command = "touch %s" % marker
    check = _run(tmp_path / "check", args=("--check",), local_reload_command=command)
    assert check.rc == 2, "local drift must be reported, not reloaded"
    assert not marker.exists(), "check mode ran the local reload command"
    assert check.targets[TARGET_GAUGE % LOCAL_HOST] == 0

    push = _run(tmp_path / "push", local_reload_command=command)
    assert push.rc == 0, push.proc.stderr
    assert marker.exists(), "a real run must run the local reload command"
    assert push.targets[TARGET_GAUGE % LOCAL_HOST] == 1


def test_check_mode_reports_an_unusable_target(tmp_path) -> None:
    run = _run(tmp_path, ssh_ok=False, args=("--check",))
    assert run.rc == 2
    assert run.renewal == {}
    assert run.targets[TARGET_GAUGE % TARGET["host"]] == 0
    assert run.targets["cert_distribution_last_run_failed_targets"] == 1


def test_a_failed_check_leaves_the_renewal_file_untouched(tmp_path) -> None:
    seed = "cert_renewal_last_run_success 1\ncert_renewal_last_success_timestamp_seconds 111\n"
    run = _run(tmp_path, ssh_ok=False, args=("--check",), seed_renewal=seed)
    assert run.rc == 2
    assert run.renewal_raw == seed


@pytest.mark.parametrize("args", [("--check",), ()], ids=["check", "renewal"])
def test_a_local_failure_before_the_loop_keeps_the_target_gauges(tmp_path, args) -> None:
    """A run that never reached the targets says nothing about them, so the
    previous gauges stand."""
    run = _run(tmp_path, local_cert=False, args=args, seed_targets=STALE_TARGETS)
    assert run.rc == 1, run.proc.stderr
    assert run.targets_raw == STALE_TARGETS


@pytest.mark.parametrize("args", [("--check",), ()], ids=["check", "renewal"])
def test_no_configured_targets_retires_the_stale_gauges(tmp_path, args) -> None:
    """An empty target list is an answer: the per-target series go, and the
    run-level count reads zero, so a removed host stops alerting."""
    run = _run(tmp_path, targets=(), args=args, seed_targets=STALE_TARGETS)
    assert run.rc == 0, run.proc.stderr
    assert run.targets == {"cert_distribution_last_run_failed_targets": 0}
    assert RETIRED_HOST not in run.targets_raw
    if args:
        assert run.renewal == {}, "check mode must not write cert_renewal.prom"
    else:
        assert run.renewal["cert_renewal_last_run_success"] == 1


def test_an_unknown_argument_is_fatal(tmp_path) -> None:
    run = _run(tmp_path, args=("--chek",))
    assert run.rc == 1
    assert "usage" in run.proc.stderr
    assert run.renewal == {} and run.targets == {}


def test_check_mode_reads_the_refusal_the_receiver_emits(tmp_path) -> None:
    """The probe recognises a reachable target by the receiver's own refusal, so
    the two templates have to agree on that string."""
    assert 'fail "empty bundle"' in RECEIVER.read_text(encoding="utf-8")
    assert "grep -q 'FAIL: empty bundle'" in TEMPLATE.read_text(encoding="utf-8")


@pytest.mark.parametrize("unit", UNITS)
def test_the_check_units_render_from_the_role_defaults(unit) -> None:
    rendered = _env().get_template("%s.j2" % unit).render(**_context())
    assert "{{" not in rendered, "unresolved placeholder in %s" % unit
    if unit.endswith(".service"):
        # Without --check the daily timer would push to every target.
        assert "ExecStart=/usr/local/sbin/homelab-cert-reload.sh --check" in rendered
        assert "Nice=%s" % DEFAULTS["acme_certs_distribution_check_nice"] in rendered
    else:
        assert "OnCalendar=%s" % DEFAULTS["acme_certs_distribution_check_schedule"] in rendered
        assert "Unit=homelab-cert-check.service" in rendered
        assert "WantedBy=timers.target" in rendered


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
