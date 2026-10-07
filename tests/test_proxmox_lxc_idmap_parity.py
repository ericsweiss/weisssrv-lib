"""The two idmap shell bodies share a prologue and an epilogue verbatim.

Only the gid-map generation differs, and nothing else holds them in step: a
guard dropped from one rewrites the container config unchecked.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
TASKS = (
    REPO / "ansible_collections" / "weisssrv" / "infra" / "roles"
    / "proxmox_lxc" / "tasks" / "main.yml"
)
STANDARD = "Configure UID/GID mapping (standard)"
GPU = "Configure UID/GID mapping (with GPU)"

# The echo group opens the differing half and `} >> "$STAGE"` closes it.
OPEN = "\n{\n"
CLOSE = '} >> "$STAGE"'
# GPU-only variables, stripped so the prologues compare equal.
GPU_ONLY = ("VIDEO_GID=", "RENDER_GID=")


def body(name: str) -> str:
    tasks = yaml.safe_load(TASKS.read_text(encoding="utf-8"))
    task = next(t for t in tasks if t.get("name") == name)
    return task["ansible.builtin.shell"]["cmd"]


def split(cmd: str) -> tuple[str, str]:
    head, _, rest = cmd.partition(OPEN)
    _, _, tail = rest.partition(CLOSE)
    head = "\n".join(
        line for line in head.splitlines()
        if not line.strip().startswith(GPU_ONLY)
    )
    return head.strip(), tail.strip()


def test_both_tasks_exist():
    for name in (STANDARD, GPU):
        assert body(name), f"{name} carries no shell body"


def test_the_shared_prologue_and_epilogue_are_identical():
    standard_head, standard_tail = split(body(STANDARD))
    gpu_head, gpu_tail = split(body(GPU))
    assert standard_head == gpu_head
    assert standard_tail == gpu_tail


def test_a_dropped_guard_in_one_copy_is_caught():
    """The negative arm: prove the comparison can fail."""
    drifted = body(GPU).replace(
        "grep -Eq '^(arch|hostname|ostype|rootfs|memory|cores|swap|net[0-9]+):'"
        ' "$STAGE" || {\n',
        "",
        1,
    )
    assert split(drifted)[0] != split(body(STANDARD))[0]


@pytest.mark.parametrize("name", [STANDARD, GPU])
def test_each_copy_keeps_the_gutted_config_guard(name):
    assert "refusing to publish a gutted config" in body(name)
    assert "EVERY line was an idmap line" in body(name)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
