"""proxmox_firewall's molecule.yml survives molecule's pre-YAML interpolation.

Molecule parses the config through string.Template, so one undoubled dollar sign
in it — a value or a comment — aborts the scenario before create.
"""
from __future__ import annotations

import string
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "proxmox_firewall"
CONFIG = ROLE / "molecule" / "default" / "molecule.yml"


class MoleculeTemplate(string.Template):
    """string.Template as molecule configures it.

    molecule.interpolation.TemplateWithDefaults, restated because the pytest job
    has no molecule: the ${VAR:-default} form the platform image line uses.
    """

    idpattern = r"[_a-z][_a-z0-9]*(?::?-[^}]+)?"


def invalid_placeholders(text: str) -> list[tuple[int, int]]:
    """(line, col) of every dollar molecule rejects, numbered as it numbers them.

    Mirrors string.Template._invalid, which is what raises the ValueError
    molecule reports as "Invalid placeholder in string: line L, col C".
    """
    found = []
    for match in MoleculeTemplate.pattern.finditer(text):
        if match.group("invalid") is None:
            continue
        start = match.start("invalid")
        lines = text[:start].splitlines(keepends=True)
        if not lines:
            found.append((1, 1))
            continue
        found.append((len(lines), start - len("".join(lines[:-1]))))
    return found


def config_text() -> str:
    return CONFIG.read_text(encoding="utf-8")


def test_the_config_has_a_literal_dollar_to_escape() -> None:
    """Without one the interpolation test below would pass vacuously."""
    assert "$$" in config_text()


def test_no_dollar_in_the_config_is_an_invalid_placeholder() -> None:
    bad = invalid_placeholders(config_text())
    assert not bad, (
        "molecule aborts the scenario at config parse on %s. Double every "
        "literal dollar sign in %s, comments included."
        % (", ".join("line %d, col %d" % site for site in bad), CONFIG.name)
    )


def test_the_escape_pass_leaves_parseable_yaml_and_a_single_anchor() -> None:
    """safe_substitute stands in for molecule's substitute: it applies the same
    `$$` -> `$` escape and leaves an unresolved ${VAR:-default} in place."""
    config = yaml.safe_load(MoleculeTemplate(config_text()).safe_substitute({}))
    groups = config["provisioner"]["inventory"]["group_vars"]["all"][
        "proxmox_firewall_security_groups"
    ]
    noauth = next(g for g in groups if g["name"] == "sg-example-noauth")
    assert "'$'" in noauth["rules"]
    assert config["platforms"][0]["name"] == "proxmox-firewall-test"


def test_the_detector_reports_a_bare_dollar_in_a_comment() -> None:
    """Mutation case: the comment that carried the bug is the exact shape the
    detector has to catch."""
    assert invalid_placeholders("a:\n  # a bare `$` aborts the parse\n  b: 1\n") == [
        (2, 13)
    ]


def test_the_detector_accepts_what_molecule_accepts() -> None:
    accepted = "a: $$literal\nb: ${MOLECULE_TEST_IMAGE:-fallback:latest}\nc: $PLAIN\n"
    assert invalid_placeholders(accepted) == []
