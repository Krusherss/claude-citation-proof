#!/usr/bin/env python
"""t870 — the gate and Chrome's fragment generator disagreed by construction.

Chromium normalizes BOTH sides before matching a text fragment. Its
`normalizeString` (text-fragments-polyfill `src/text-fragment-utils.js:979`) is:

    NFKD  ->  /\\s+/ -> ' '  ->  strip U+0300..U+036F  ->  toLowerCase

so the fragments its generator emits are lowercased and decomposed. This
module's `check_fragment_in_text` matched EXACT, CASE-SENSITIVE raw page text,
so a fragment Chromium itself produced and would happily highlight FAILED our
gate on case alone. Measured in the B3 spike, both directions.

Adopting the generator therefore costs two coordinated changes, and this is the
gate half. The normalization is RE-IMPLEMENTED here rather than imported from
the library on purpose (plan §2 rule 2): this module is the independent check ON
that generator, and a gate that calls the generator's own normalizer agrees with
it by construction and can never catch its bugs.

THE DIRECTION THAT MATTERS MOST IS THE SECOND ONE. Loosening a checker is how a
checker becomes a rubber stamp — the t838 lesson, where a generator's links went
unchecked and "0 failures" meant "nothing was inspected". Every relaxation below
is paired with a perturbation that must still FAIL.
"""
import os
import sys
import unittest
from urllib.parse import quote as urlq

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import deliverable_link_rule as dlr  # noqa: E402

BASE = "https://example.com/report"


def frag(*parts):
    """Encode components the way cite_proof.frag_component does (t811: the
    grammar reserves '-', so a literal one must ship as %2D)."""
    return (BASE + "#:~:text="
            + ",".join(urlq(p, safe="").replace("-", "%2D") for p in parts))


def verdict(url, page):
    return dlr.check_fragment_in_text(url, page)["verdict"]


def reason(url, page):
    return dlr.check_fragment_in_text(url, page)["reason"]


class TestChromeNormalizer(unittest.TestCase):
    """The re-implementation is pinned to the read source, not to intuition."""

    def test_matches_the_four_documented_steps(self):
        self.assertEqual(dlr._chrome_normalize("The  SURVEY\nFound"),
                         "the survey found")
        self.assertEqual(dlr._chrome_normalize("Société Générale"),
                         "societe generale")
        # NFKD decomposes the ligature; the en dash is neither decomposed nor
        # a combining mark, so it SURVIVES — which is the whole t786 fix.
        self.assertEqual(dlr._chrome_normalize("ﬁrm 4–6"), "firm 4–6")
        self.assertEqual(dlr._chrome_normalize("a b"), "a b")


class TestGateAcceptsRealChromeFragments(unittest.TestCase):
    PAGE = ("The Survey Found Breakeven AUM increased to US$82.9m "
            "in 4–6 weeks at Société Générale.")

    def test_lowercased_fragment_matches_a_capitalised_page(self):
        """RED: the generator lowercases; the gate demanded exact case."""
        self.assertEqual(
            verdict(frag("the survey found breakeven aum"), self.PAGE), "PASS")

    def test_decomposed_fragment_matches_an_accented_page(self):
        self.assertEqual(verdict(frag("societe generale"), self.PAGE), "PASS")

    def test_endash_fragment_matches_the_endash_page(self):
        """The B3 root fix: a fragment built from PAGE bytes carries U+2013."""
        self.assertEqual(verdict(frag("in 4–6 weeks"), self.PAGE), "PASS")

    def test_ascii_hyphen_against_an_endash_page_still_fails(self):
        """t786's dead link — cite_proof folds the dash, a browser does not.
        This must stay a FAIL: it is the defect, not a false positive."""
        self.assertEqual(verdict(frag("in 4-6 weeks"), self.PAGE), "FAIL")


class TestTheGateStillInspects(unittest.TestCase):
    """Every relaxation above, paired with something that must still FAIL."""

    PAGE = ("The Survey Found Breakeven AUM increased to US$82.9m. "
            "Fees rose 110% of the prior level. " + ("filler text. " * 60)
            + "The survey found breakeven AUM increased again.")

    def test_a_perturbed_word_fails(self):
        self.assertEqual(
            verdict(frag("the survey found breakeven NAV"), self.PAGE), "FAIL")

    def test_text_absent_from_the_page_fails(self):
        self.assertEqual(verdict(frag("the survey found nothing"), self.PAGE),
                         "FAIL")

    def test_word_boundaries_survive_normalization(self):
        """'10%' must not match inside '110%' just because case folded."""
        self.assertEqual(verdict(frag("10% of the prior level"), self.PAGE),
                         "FAIL")

    def test_range_overlap_still_fails(self):
        """t799: textEnd is searched only AFTER textStart's match ends."""
        v = dlr.check_fragment_in_text(
            frag("the survey found breakeven", "found breakeven aum"), self.PAGE)
        self.assertEqual(v["verdict"], "FAIL")
        self.assertIn("RANGE", v["reason"])

    def test_runaway_range_still_fails(self):
        """t813: textStart resolves at its FIRST hit and the highlight runs."""
        v = dlr.check_fragment_in_text(
            frag("the survey found", "increased again"), self.PAGE)
        self.assertEqual(v["verdict"], "FAIL")
        self.assertIn("RUNAWAY", v["reason"])

    def test_literal_hyphen_in_a_component_still_fails(self):
        """t811 is a GRAMMAR rule, not a matching rule — normalization must not
        soften it: Chromium fails such a match silently."""
        v = dlr.check_fragment_in_text(
            BASE + "#:~:text=the%20survey-found", self.PAGE)
        self.assertEqual(v["verdict"], "FAIL")

    def test_prefix_must_still_be_adjacent(self):
        v = dlr.check_fragment_in_text(
            frag("fees rose-", "the survey found breakeven aum"), self.PAGE)
        self.assertEqual(v["verdict"], "FAIL")


if __name__ == "__main__":
    unittest.main(verbosity=2)
