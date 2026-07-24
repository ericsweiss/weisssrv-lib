"""Line-based edits to a kustomize `resources:` list.

Line surgery (not ruamel) is deliberate here: the list is trivial (`  - x.yaml`
lines) and the file carries opt-in COMMENTS (`# - hpa.yaml   # opt-in ...`) that
ruamel's round-trip drops when the adjacent item is removed. Line edits preserve
every comment, the `---` start, and the exact 2-space offset.
"""
from __future__ import annotations

import re

_ACTIVE_RE = re.compile(r"^(?P<indent>\s*)-\s+(?P<name>[\w.\-/]+)\s*$")
_COMMENTED_RE_T = r"^(?P<indent>\s*)#\s*-\s+{name}\b.*$"
# A zero-indent mapping key (`resources:`, `namespace: x`, `kind: …`). Used to
# scope list edits to the `resources:` block so a sibling list (`components:`,
# `patchesStrategicMerge:`, …) is never miscounted or mangled.
_TOP_KEY_RE = re.compile(r"^(?P<key>[A-Za-z0-9_.\-]+):")


def _in_resources_flags(lines) -> list[bool]:
    """For each line, whether it sits inside the top-level `resources:` block.

    Accepts lines with or without trailing newlines. The `resources:` header
    line itself is flagged False (it is not an item); a following zero-indent
    mapping key ends the block.
    """
    flags: list[bool] = []
    in_res = False
    for raw in lines:
        line = raw.rstrip("\n")
        if line[:1] and not line[0].isspace() and not line.lstrip().startswith("#"):
            m = _TOP_KEY_RE.match(line)
            if m:
                in_res = m.group("key") == "resources"
                flags.append(False)
                continue
        flags.append(in_res)
    return flags


def list_resources(text: str) -> list[str]:
    """Active (uncommented) entries in the `resources:` block, in order."""
    lines = text.splitlines()
    out: list[str] = []
    for line, in_res in zip(lines, _in_resources_flags(lines)):
        if not in_res:
            continue
        m = _ACTIVE_RE.match(line)
        if m:
            out.append(m.group("name"))
    return out


def has_resource(text: str, name: str) -> bool:
    return name in list_resources(text)


def remove_resource(text: str, name: str) -> tuple[str, bool]:
    """Drop the active `- <name>` line in the `resources:` block. Returns
    (text, changed)."""
    pat = re.compile(rf"^\s*-\s+{re.escape(name)}\s*$")
    lines = text.splitlines(keepends=True)
    kept, changed = [], False
    for line, in_res in zip(lines, _in_resources_flags(lines)):
        if in_res and pat.match(line.rstrip("\n")):
            changed = True
            continue
        kept.append(line)
    return "".join(kept), changed


def uncomment_resource(text: str, name: str) -> tuple[str, bool]:
    """Turn the first `  # - <name>   # note` in the `resources:` block into
    `  - <name>`.

    No-op if the resource is already active (avoids a duplicate entry). Returns
    (text, changed).
    """
    if has_resource(text, name):
        return text, False
    pat = re.compile(_COMMENTED_RE_T.format(name=re.escape(name)))
    lines = text.splitlines(keepends=True)
    out, changed = [], False
    for line, in_res in zip(lines, _in_resources_flags(lines)):
        m = pat.match(line.rstrip("\n"))
        if in_res and m and not changed:
            out.append(f"{m.group('indent')}- {name}\n")
            changed = True
        else:
            out.append(line)
    return "".join(out), changed


def add_resource(text: str, name: str) -> tuple[str, bool]:
    """Ensure `- <name>` is an active entry in the `resources:` block.

    Prefers uncommenting an existing `# - <name>` opt-in line; otherwise appends
    `  - <name>` after the last active resource, matching its indentation.
    Returns (text, changed). No-op (changed=False) if already active.
    """
    if has_resource(text, name):
        return text, False
    text2, changed = uncomment_resource(text, name)
    if changed:
        return text2, True

    lines = text.splitlines(keepends=True)
    flags = _in_resources_flags(lines)
    last_idx, indent = None, "  "
    for i, (line, in_res) in enumerate(zip(lines, flags)):
        if not in_res:
            continue
        m = _ACTIVE_RE.match(line.rstrip("\n"))
        if m:
            last_idx, indent = i, m.group("indent")
    new_line = f"{indent}- {name}\n"
    if last_idx is None:
        lines.append(new_line)
    else:
        # Preserve a missing trailing newline on the anchor line.
        if not lines[last_idx].endswith("\n"):
            lines[last_idx] += "\n"
        lines.insert(last_idx + 1, new_line)
    return "".join(lines), True
