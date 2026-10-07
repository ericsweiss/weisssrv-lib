"""The three zfs_encryption behavior scripts share one work_root() probe.

ansible.builtin.script pushes one self-contained file, so the helper is inlined
in all three; this holds the copies in step.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FILES = (
    REPO / "ansible_collections" / "weisssrv" / "infra" / "roles" / "zfs_encryption"
    / "molecule" / "default" / "files"
)
SCRIPTS = {
    name: FILES / name
    for name in (
        "zfs-mount-behavior.sh",
        "zfs-load-key-behavior.sh",
        "zfs-start-guests-behavior.sh",
    )
}
BODY = re.compile(r"^work_root\(\) \{$.*?^\}$", re.DOTALL | re.MULTILINE)


def work_root_body(text: str) -> str:
    match = BODY.search(text)
    assert match, "no work_root() definition found"
    return match.group(0)


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_each_script_defines_a_non_empty_work_root(name):
    body = work_root_body(SCRIPTS[name].read_text(encoding="utf-8"))
    assert len(body.splitlines()) > 5
    assert "execprobe" in body


def _bodies() -> dict[str, str]:
    return {
        name: work_root_body(path.read_text(encoding="utf-8"))
        for name, path in SCRIPTS.items()
    }


def drifted(bodies: dict[str, str]) -> list[str]:
    """Names whose body differs from the alphabetically-first one (the reference)."""
    reference = bodies[sorted(bodies)[0]]
    return [name for name, body in sorted(bodies.items()) if body != reference]


def test_the_three_copies_are_identical():
    names = drifted(_bodies())
    assert names == [], f"drifted from {sorted(SCRIPTS)[0]}: {names}"


def test_a_mutated_copy_is_caught():
    """The negative arm: the same comparison the positive arm uses must fail."""
    bodies = _bodies()
    target = "zfs-mount-behavior.sh"
    bodies[target] = bodies[target].replace("/var/tmp /root", "/root", 1)
    assert bodies[target] != _bodies()[target], "mutation needle no longer present"
    assert drifted(bodies) == [target]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
