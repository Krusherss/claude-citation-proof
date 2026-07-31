#!/usr/bin/env python3
"""Tests for cite_proof.py matcher normalization axes.

PDF de-hyphenation and ligature folding are pinned in the cache/re-anchor tests.
These tests cover two additional axes:

  1. CURLY QUOTES — a transcription writes ASCII '/" while the page serves
     U+2018/2019/201C/201D (or vice versa): verdict flips absent while true.
     (Ed HedgeFund re-anchor cases fund-03/13.)
  2. TAG-BOUNDARY SPACES — fetch_text replaces HTML tags with a space, so
     inline markup (<sup>1</sup>, XBRL spans, tag-broken parens) injects
     spaces the rendered page never shows: a quote copied from the rendered
     page returns absent against the served text.
     (Ed HedgeFund re-anchor cases fund-03/04; Apollo 10-K XBRL.)

Both folds are PRESENCE-CHECK ONLY: keying (canon_quote/proof_key) must stay
byte-for-byte in lockstep with proof_gate.py — pinned below.
"""
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import cite_proof as cp  # noqa: E402

URL = "https://example.org/page"


# --------------------------------------------------------------------------- #
# Axis 1: curly-quote family folds to ASCII (both directions)                  #
# --------------------------------------------------------------------------- #

def test_curly_apostrophe_needle_ascii_page_curly():
    needle = "the fund's returns"
    page = "text before the fund’s returns text after"
    assert cp.match_norm(needle) in cp.match_norm(page)


def test_curly_apostrophe_needle_curly_page_ascii():
    needle = "the fund’s returns"
    page = "text before the fund's returns text after"
    assert cp.match_norm(needle) in cp.match_norm(page)


def test_curly_double_quotes_fold():
    needle = 'said "hello world" loudly'
    page = "he said “hello world” loudly today"
    assert cp.match_norm(needle) in cp.match_norm(page)


def test_single_low_and_high_curly_variants_fold():
    # U+2018 left single, U+201A low-9 single — the rest of the family
    assert cp.match_norm("‘quoted’") == cp.match_norm("'quoted'")
    assert cp.match_norm("„quoted“") == cp.match_norm('"quoted"')


# --------------------------------------------------------------------------- #
# Axis 2: spaces adjacent to punctuation fold away (tag-boundary artifact)     #
# --------------------------------------------------------------------------- #

def test_tag_boundary_space_around_parens():
    # the real fund-03/04 class: the paren region is tag-broken, so the
    # served (tag-stripped) text carries spaces the rendered page never shows
    needle = ("a spouse or spousal equivalent (excluding the value of the "
              "person's primary residence)")
    served = ("together with a spouse or spousal equivalent ( excluding the "
              "value of the person’s primary residence ) at the time")
    assert cp.match_norm(needle) in cp.match_norm(served)


def test_tag_boundary_space_reverse_direction():
    # transcription carries the extra space; page text is tight
    needle = "up to 20% of the total ( gross )"
    page = "normally up to 20% of the total (gross) returns"
    assert cp.match_norm(needle) in cp.match_norm(page)


def test_space_fold_never_merges_two_words():
    # word-space-word must stay distinct: no 'the rapist' -> 'therapist'
    assert cp.match_norm("the rapist") not in cp.match_norm("a therapist here")


def test_dehyphenation_runs_before_space_fold():
    # order is load-bearing: space-fold first would eat the '- ' pattern and
    # leave 'man-agement', which never matches 'management'
    assert cp.match_norm("man- agement") == "management"


def test_numeric_range_not_merged_to_digits():
    # '10 - 20' may fold its spaces ('10-20') but must NEVER merge to '1020'
    out = cp.match_norm("10 - 20")
    assert "1020" not in out
    assert cp.match_norm("10 - 20") == cp.match_norm("10-20")


# --------------------------------------------------------------------------- #
# Keying stays byte-for-byte (proof_gate lockstep) — folds are match-only      #
# --------------------------------------------------------------------------- #

def test_keying_unaffected_by_curly_fold():
    assert cp.canon_quote("the fund’s") == "the fund’s"
    assert cp.proof_key(URL, "the fund’s") != cp.proof_key(URL, "the fund's")


def test_keying_unaffected_by_space_fold():
    assert cp.canon_quote("returns ( net )") == "returns ( net )"
    assert cp.proof_key(URL, "a ( b )") != cp.proof_key(URL, "a (b)")


# --------------------------------------------------------------------------- #
# Present-preservation: previously-matching pairs must keep matching           #
# --------------------------------------------------------------------------- #

def test_previously_present_pairs_still_match():
    # exact-substring, ligature, U+2011, PDF de-hyphenation — the classes the
    # 58 shipped `present` verdicts rely on (t582 regression bar)
    pairs = [
        ("illustrative examples in documents",
         "use in illustrative examples in documents. You may"),
        ("significantly", "rose signiﬁcantly here"),
        ("1-3 years", "over 1‑3 years now"),
        ("management team", "the man- agement team met"),
        ("13.8% of all programs", "overall, 13.8% of all programs reach"),
    ]
    for needle, hay in pairs:
        assert cp.match_norm(needle) in cp.match_norm(hay), needle
