"""The offer list (scripts/vendorable-paths.yml) stays honest.

The library's half of the vendored-copy contract: consumers own their
manifests, so the only library-side claims left to gate are that every offered
path exists in the tree (an offer for a deleted file strands every manifest
naming it) and that the list stays sorted and duplicate-free (the engine
treats it as a set; a duplicate is always an editing accident).
"""
from __future__ import annotations

from pathlib import Path

import yaml

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
    """The engine holds manifest entries to canonical repo-relative spelling;
    the offer must obey its own rule or a legitimate manifest could never
    reference an offered path."""
    from pathlib import Path
    bad = [
        p for p in _offered()
        if not p.strip()
        or "\x00" in p
        or Path(p).is_absolute()
        or ".." in Path(p).parts
        or p != Path(p).as_posix()
    ]
    assert not bad, "non-canonical offered paths: %s" % bad


def test_offer_list_is_sorted_and_unique():
    paths = _offered()
    assert len(paths) == len(set(paths)), "duplicate entries in the offer list"
    assert paths == sorted(paths), "keep the offer list sorted — diffs stay reviewable"


def test_the_engine_itself_is_not_offered():
    """check-vendored-copies.py runs FROM the library checkout at the pinned
    ref; a vendored copy of the engine would gate itself with itself and drift
    invisibly between pins."""
    assert "scripts/check-vendored-copies.py" not in _offered()
