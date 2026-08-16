"""Render-level tests for the acme_certs forced-command receiver.

`templates/cert-receive.sh.j2` is the whole security boundary of cert
distribution: it is what a leaked distribution key can run, and every
operational parameter is baked in at render time. Molecule covers the reachable
half (a real push into a real container), but the RELOAD branch is chosen by
Jinja, so the three shapes a target can produce — `restart_command`,
`restart_service`, and neither — only differ before bash ever runs.

So this renders the template directly, with the role's own defaults as context,
and asserts the three properties a review cannot check by eye:

* every shape is still valid bash (`bash -n`) — a quoting slip in the RELOAD
  assignment breaks the receiver on the target, not here;
* the empty shape refuses rather than running `bash -c ''` and reporting
  success (the `[ -n "$RELOAD" ]` belt);
* the applied-hash marker is written only AFTER a clean reload, so a failed
  reload re-fires on the next push instead of being masked by a stale hash.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

import jinja2
import pytest
import yaml

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
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATE.parent)),
        keep_trailing_newline=True,
    )
    # `quote` is an Ansible filter, not a stock Jinja one; Ansible's is
    # shlex.quote, and the shell-safety of the RELOAD assignment depends on it.
    env.filters["quote"] = shlex.quote
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
