"""Every path in scripts/vendorable-paths.yml exists, and the list stays sorted
and duplicate-free.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from script_loader import load_script

engine = load_script("check-vendored-copies.py")
REPO = Path(__file__).resolve().parent.parent
OFFER = REPO / "scripts" / "vendorable-paths.yml"


def _offered() -> list[str]:
    doc = yaml.safe_load(OFFER.read_text())
    assert isinstance(doc, dict) and isinstance(doc.get("vendorable"), list), (
        "%s needs a `vendorable:` list" % OFFER
    )
    paths = doc["vendorable"]
    assert all(isinstance(p, str) for p in paths), "every offered path is a string"
    return paths


def test_every_offered_path_exists():
    missing = [p for p in _offered() if not (REPO / p).is_file()]
    assert not missing, (
        "offered but not in the tree — a consumer manifest naming these fails at "
        "its next pin bump with no consumer-side fix: %s" % missing
    )


def test_every_offered_path_is_canonical():
    """The offer obeys the engine's own path rule, or no legitimate manifest
    could reference an offered path."""
    for path in _offered():
        engine._validate_relpath(path, "offered")


def test_offer_list_is_sorted_and_unique():
    paths = _offered()
    assert len(paths) == len(set(paths)), "duplicate entries in the offer list"
    assert paths == sorted(paths), "keep the offer list sorted — diffs stay reviewable"


def test_the_engine_itself_is_not_offered():
    """check-vendored-copies.py runs FROM the library checkout at the pinned
    ref; a vendored copy of the engine would gate itself with itself and drift
    invisibly between pins."""
    assert "scripts/check-vendored-copies.py" not in _offered()
