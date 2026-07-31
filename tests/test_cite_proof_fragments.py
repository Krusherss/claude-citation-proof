#!/usr/bin/env python3
"""Tests for deeplink_for() double-'#' handling on section anchors.

deeplink_for() calls strip_fragment(),
which by design strips only a pre-existing '#:~:text=' fragment and KEEPS a real
'#section' anchor. So for a URL cited by section anchor (the normal way to cite
API docs) it appends its own '#:~:text=' AFTER the surviving anchor, producing
two '#' in one URL — an invalid text-fragment deeplink the browser can never
resolve.

  in : https://docs.python.org/3/library/os.html#os.replace
  out: https://docs.python.org/3/library/os.html#os.replace#:~:text=...   (INVALID)

SCOPE (load-bearing): the fix lives in deeplink_for ONLY. The shared
strip_fragment() must NOT change, because canon_url()/proof_key() call it and
deliberately keep a '#section' anchor as part of the resource identity — that
keying is mirrored byte-for-byte by proof_gate.py and pinned by key_vectors.json,
so touching it would re-key (orphan) every section-anchored proof already on
disk. The last block below pins that invariant: canon/key behaviour is unchanged.
"""
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import cite_proof as cp  # noqa: E402

ANCHORED = "https://docs.python.org/3/library/os.html#os.replace"
BARE = "https://docs.python.org/3/library/os.html"
QUOTE = "If successful, the renaming will be an atomic operation"


# --------------------------------------------------------------------------- #
# The bug: a section anchor must not survive into the deeplink                 #
# --------------------------------------------------------------------------- #

def test_deeplink_has_exactly_one_hash_for_section_anchored_url():
    """A section-anchored source URL yields a SINGLE '#', not a double fragment."""
    dl = cp.deeplink_for(QUOTE, ANCHORED)
    assert dl.count("#") == 1, dl


def test_deeplink_drops_section_anchor_before_text_fragment():
    """The text fragment is appended to the bare page, not after '#os.replace'."""
    dl = cp.deeplink_for(QUOTE, ANCHORED)
    assert "#os.replace" not in dl, dl
    assert dl.startswith(BARE + "#:~:text="), dl


def test_deeplink_for_anchored_equals_bare():
    """Anchored and bare source URLs produce the identical deeplink."""
    assert cp.deeplink_for(QUOTE, ANCHORED) == cp.deeplink_for(QUOTE, BARE)


def test_deeplink_still_strips_preexisting_text_fragment():
    """A source that already carries a '#:~:text=' fragment is not doubled."""
    pre = BARE + "#:~:text=stale%20highlight"
    dl = cp.deeplink_for(QUOTE, pre)
    assert dl.count("#") == 1, dl
    assert dl.startswith(BARE + "#:~:text="), dl


def test_deeplink_bare_url_unaffected():
    """Regression guard: a bare URL keeps building a valid single-'#' deeplink."""
    dl = cp.deeplink_for(QUOTE, BARE)
    assert dl.count("#") == 1, dl
    assert dl.startswith(BARE + "#:~:text="), dl


# --------------------------------------------------------------------------- #
# INVARIANT — keying is untouched (proves the fix stayed inside deeplink_for)  #
# These pass BEFORE and AFTER the fix; a failure here means strip_fragment /   #
# canon_url / proof_key were wrongly modified and existing proofs are orphaned.#
# --------------------------------------------------------------------------- #

def test_strip_fragment_still_keeps_section_anchor():
    assert cp.strip_fragment(ANCHORED) == ANCHORED


def test_strip_fragment_still_removes_text_fragment():
    assert cp.strip_fragment(BARE + "#:~:text=foo") == BARE


def test_canon_url_keeps_section_anchor_as_identity():
    assert cp.canon_url(ANCHORED).endswith("#os.replace")


def test_proof_key_distinguishes_anchored_from_bare():
    """The section anchor is part of resource identity: distinct keys."""
    assert cp.proof_key(ANCHORED, QUOTE) != cp.proof_key(BARE, QUOTE)
