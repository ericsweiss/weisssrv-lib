"""Render-level tests for the acme_certs forced-command receiver.

The RELOAD branch is chosen by Jinja, so the three target shapes differ before
bash runs; molecule reaches only the push half.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

import jinja2
import pytest
import yaml
from _helpers import ansible_env

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "acme_certs"
TEMPLATE = ROLE / "templates" / "cert-receive.sh.j2"

# The role's own defaults are the context: a hand-written stand-in would let a
# renamed default pass here while the real render fails.
DEFAULTS = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())

# A target as README § Distribution targets documents one, minus the reload keys
# — which are what the three shapes vary.
BASE_TARGET = {
    "host": "dns-01.example.test",
    "cert_dir": "/opt/AdGuardHome/certs",
    "owner": "root",
    "group": "adguard",
    "cert_mode": "0644",
    "key_mode": "0640",
}

# name -> the reload keys that shape sets. The empty shape is reachable in
# production only through a hand-installed receiver (the role asserts against a
# target declaring neither), which is exactly why the script must refuse.
SHAPES = {
    "command": {"restart_command": "sudo systemctl reload nginx"},
    "service": {"restart_service": "AdGuardHome"},
    "empty": {},
}


def _render(**overrides: object) -> str:
    env = ansible_env(
        loader=jinja2.FileSystemLoader(str(TEMPLATE.parent)),
        keep_trailing_newline=True,
    )
    context = {
        **{k: v for k, v in DEFAULTS.items() if isinstance(v, (str, int, bool))},
        # The two the role requires the site to supply (defaults/main.yml
        # documents them as "no default"), plus Ansible's own managed banner.
        "ansible_managed": "Ansible managed",
        "acme_certs_domain": "example.test",
    }
    context.update(overrides)
    return env.get_template(TEMPLATE.name).render(**context)


def _render_shape(shape: str) -> str:
    return _render(acme_certs_target={**BASE_TARGET, **SHAPES[shape]})


@pytest.fixture(scope="module", params=sorted(SHAPES))
def shape(request) -> str:
    return request.param


@pytest.fixture(scope="module")
def rendered(shape: str) -> str:
    return _render_shape(shape)


def test_every_shape_is_valid_bash(tmp_path_factory, shape, rendered) -> None:
    """`bash -n` on the real render. The receiver runs as root behind a forced
    command, so a parse error there is a distribution outage discovered only on
    the next renewal."""
    script = tmp_path_factory.mktemp("cert-receive") / f"cert-receive-{shape}.sh"
    script.write_text(rendered)
    result = subprocess.run(
        ["bash", "-n", str(script)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"{shape} shape does not parse:\n{result.stderr}"


def test_every_shape_assigns_reload_exactly_once(rendered) -> None:
    """Two assignments would mean a branch fell through and the later one wins."""
    assert len(re.findall(r"(?m)^RELOAD=", rendered)) == 1


def test_the_command_shape_bakes_the_command_verbatim() -> None:
    rendered = _render_shape("command")
    assert f"RELOAD={shlex.quote(SHAPES['command']['restart_command'])}" in rendered


def test_the_service_shape_bakes_a_systemctl_restart() -> None:
    rendered = _render_shape("service")
    expected = shlex.quote(f"systemctl restart {SHAPES['service']['restart_service']}")
    assert f"RELOAD={expected}" in rendered


def test_a_restart_command_wins_over_a_restart_service() -> None:
    """Both keys set is an operator ambiguity; the template resolves it one way
    and the README documents that way, so pin it."""
    rendered = _render(
        acme_certs_target={
            **BASE_TARGET,
            **SHAPES["command"],
            **SHAPES["service"],
        }
    )
    assert f"RELOAD={shlex.quote(SHAPES['command']['restart_command'])}" in rendered
    assert "systemctl restart AdGuardHome" not in rendered


def test_the_empty_shape_bakes_an_empty_reload() -> None:
    assert "RELOAD=''" in _render_shape("empty")


def test_every_shape_carries_the_empty_reload_belt(rendered) -> None:
    """The belt is unconditional on purpose: it is what stops a receiver with no
    reload baked in from running `bash -c ''`, succeeding, and recording the
    cert as applied while the consuming service still serves the old one."""
    assert '[ -n "$RELOAD" ] || fail' in rendered


def test_a_target_with_a_whitespace_only_reload_is_treated_as_empty() -> None:
    """`| trim` in the template is what makes `restart_service: "  "` take the
    refusing branch instead of baking `systemctl restart` with no unit."""
    rendered = _render(acme_certs_target={**BASE_TARGET, "restart_service": "   "})
    assert "RELOAD=''" in rendered
    assert "systemctl restart" not in rendered


def test_the_applied_marker_is_written_only_after_a_clean_reload(rendered) -> None:
    """Ordering, not presence: a marker written before the reload masks a failed
    reload behind a stale 'applied' hash, and the next push reports `unchanged`
    instead of self-healing."""
    belt = rendered.index('[ -n "$RELOAD" ] || fail')
    reload_run = rendered.index('if bash -c "$RELOAD"; then', belt)
    marker = rendered.index(".applied-fullchain.sha256\"", reload_run)
    assert belt < reload_run < marker
    # And the else arm fails rather than falling through to the marker.
    assert re.search(r'else\n\s*fail "reload failed"', rendered[reload_run:])


def test_the_marker_write_is_inside_the_success_arm(rendered) -> None:
    """The write must sit between `if bash -c "$RELOAD"; then` and `else` — a
    write after `fi` would run on both arms and defeat the ordering above."""
    body = rendered[rendered.index('if bash -c "$RELOAD"; then') :]
    success_arm = body[: body.index("\nelse")]
    assert '> "${CERT_DIR}/.applied-fullchain.sha256"' in success_arm


def test_the_receiver_pins_the_expected_domain(rendered) -> None:
    """The SAN check is what stops a leaked key installing an unrelated but
    otherwise-valid cert, so the domain must be baked in, never read from
    stdin."""
    assert 'EXPECT_DOMAIN="example.test"' in rendered
    assert 'grep -Fxq "DNS:*.${EXPECT_DOMAIN}"' in rendered


def test_the_applied_marker_read_redirects_stderr_before_the_input(rendered) -> None:
    """Written `tr ... < file 2>/dev/null`, a missing marker prints a bash error
    on the first delivery to every target."""
    assert 'applied_hash="$(2>/dev/null tr -d ' in rendered


# --- the distribution driver, which chooses each target's reload -------------

RELOAD_TEMPLATE = ROLE / "templates" / "homelab-cert-reload.sh.j2"

RELOAD_CONTEXT = {
    "ansible_managed": "Ansible managed",
    "acme_certs_domain": "example.test",
    "inventory_hostname": "certs-01",
}


def _render_reload(targets: list) -> str:
    env = ansible_env(
        loader=jinja2.FileSystemLoader(str(RELOAD_TEMPLATE.parent)),
        keep_trailing_newline=True,
    )
    context = {
        **{k: v for k, v in DEFAULTS.items() if not isinstance(v, (dict, list))},
        **RELOAD_CONTEXT,
        "acme_certs_distribution_targets": targets,
    }
    return env.get_template(RELOAD_TEMPLATE.name).render(**context)


def _legacy_reload_arg(rendered: str) -> str:
    """The eleventh positional argument of the push_target_legacy call."""
    call = rendered[rendered.index("push_target_legacy \\") :]
    args = re.findall(r'"([^"]*)"', call[: call.index("\n\n")])
    assert len(args) >= 11, args
    return args[10]


RELOAD_SHAPES = {
    "empty restart_command falls back to the service": (
        {"ssh_no_sudo": True, "restart_command": "", "restart_service": "X"},
        "systemctl restart X",
    ),
    "whitespace-only restart_command falls back too": (
        {"ssh_no_sudo": True, "restart_command": "   ", "restart_service": "X"},
        "systemctl restart X",
    ),
    "restart_service alone": (
        {"ssh_no_sudo": True, "restart_service": "X"},
        "systemctl restart X",
    ),
    "restart_command alone renders verbatim, no injected sudo": (
        {"ssh_no_sudo": True, "restart_command": "doas rc-service nginx reload"},
        "doas rc-service nginx reload",
    ),
}


@pytest.mark.parametrize(
    ("overrides", "expected"),
    list(RELOAD_SHAPES.values()),
    ids=list(RELOAD_SHAPES),
)
def test_the_legacy_push_never_gets_an_empty_reload(overrides, expected) -> None:
    """An empty eleventh argument silently skips the reload on an appliance."""
    rendered = _render_reload([{**BASE_TARGET, "ip": "10.0.0.1", **overrides}])
    assert _legacy_reload_arg(rendered) == expected


def test_a_sudo_target_dispatches_the_receiver_with_no_reload_argument() -> None:
    """cert_dir and the reload are baked into the receiver on a sudo target."""
    rendered = _render_reload(
        [{**BASE_TARGET, "ip": "10.0.0.1", "restart_service": "X"}]
    )
    assert "push_target_receiver" in rendered
    assert "push_target_legacy \\" not in rendered


@pytest.mark.parametrize("name", list(RELOAD_SHAPES), ids=list(RELOAD_SHAPES))
def test_every_reload_shape_renders_valid_bash(tmp_path_factory, name) -> None:
    overrides, _ = RELOAD_SHAPES[name]
    rendered = _render_reload([{**BASE_TARGET, "ip": "10.0.0.1", **overrides}])
    script = tmp_path_factory.mktemp("reload") / "homelab-cert-reload.sh"
    script.write_text(rendered)
    proc = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
