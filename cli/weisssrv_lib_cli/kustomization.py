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


def list_resources(text: str) -> list[str]:
    """Active (uncommented) resource entries, in order."""
    out: list[str] = []
    for line in text.splitlines():
        m = _ACTIVE_RE.match(line)
        if m:
            out.append(m.group("name"))
    return out


def has_resource(text: str, name: str) -> bool:
    return name in list_resources(text)


def remove_resource(text: str, name: str) -> tuple[str, bool]:
    """Drop the active `- <name>` line. Returns (text, changed)."""
    pat = re.compile(rf"^\s*-\s+{re.escape(name)}\s*$")
    kept, changed = [], False
    for line in text.splitlines(keepends=True):
        if pat.match(line.rstrip("\n")):
            changed = True
            continue
        kept.append(line)
    return "".join(kept), changed


def uncomment_resource(text: str, name: str) -> tuple[str, bool]:
    """Turn the first `  # - <name>   # note` into `  - <name>`.

    No-op if the resource is already active (avoids a duplicate entry). Returns
    (text, changed).
    """
    if has_resource(text, name):
        return text, False
    pat = re.compile(_COMMENTED_RE_T.format(name=re.escape(name)))
    out, changed = [], False
    for line in text.splitlines(keepends=True):
        m = pat.match(line.rstrip("\n"))
        if m and not changed:
            out.append(f"{m.group('indent')}- {name}\n")
            changed = True
        else:
            out.append(line)
    return "".join(out), changed


def add_resource(text: str, name: str) -> tuple[str, bool]:
    """Ensure `- <name>` is an active resource.

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
    last_idx, indent = None, "  "
    for i, line in enumerate(lines):
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
