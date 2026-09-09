#!/usr/bin/env python3
"""t811 — deeplink_for must percent-encode '-' inside each text-fragment component.

The W3C text-fragment grammar RESERVES '-' (it marks prefix- and -suffix), so a
literal '-' inside textStart/textEnd makes Chromium fail the match SILENTLY: the
page loads at the top with no highlight. urllib.parse.quote never encodes '-' —
it is in _ALWAYS_SAFE — so every deeplink built over hyphenated prose shipped
broken while every presence check passed.

Evidence (real Chromium, Playwright, quote verbatim from the source PDF, target
pushed below the fold, detector scrollY > 0):

    case                                    literal -   %2D
    two-part, clean END (control)           MATCH       MATCH
    two-part, hyphen in END (shipped)       DEAD        MATCH
    END is the hyphenated word only         DEAD        MATCH
    single-part containing the hyphen       DEAD        MATCH
    hyphenated word alone                   DEAD        MATCH

Harness: scripts/tests/frag_hyphen_chromium_matrix.py (self-invalidating — it
refuses to report unless it first reproduces the live dead/alive baseline).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cite_proof import deeplink_for  # noqa: E402

URL = "https://example.com/doc.pdf"


def frag(link):
    return link.split("#:~:text=", 1)[1]


class HyphenEncoding(unittest.TestCase):
    def test_short_quote_hyphen_is_encoded(self):
        # <=8 words -> single-part fragment
        link = deeplink_for("the out-of-pocket cost", URL)
        self.assertNotIn("-", frag(link))
        self.assertIn("%2D", frag(link))

    def test_long_quote_hyphen_encoded_throughout(self):
        # t839: the fragment is single-part now, so there are no halves to
        # check separately -- the hyphen rule applies to the whole span, and
        # the hyphens in the MIDDLE of the quote are covered too, which the
        # old first5/last5 assertion could never see.
        q = ("The estimated average out-of-pocket cost per approved new "
             "compound is a well-known figure of 1395 million dollars")
        f = frag(deeplink_for(q, URL))
        self.assertNotIn("-", f)
        self.assertEqual(f.count("%2D"), 3)  # out-of-pocket x2, well-known x1

    def test_no_range_separator_is_emitted(self):
        # t839 REVERSES this test's original assertion. It used to require
        # exactly one raw ',' as the textStart,textEnd separator; that range is
        # the whole t799/t813 defect surface, so a raw ',' is now a bug.
        q = ("one two three four five six seven eight nine ten eleven "
             "twelve thirteen fourteen")
        self.assertEqual(frag(deeplink_for(q, URL)).count(","), 0)

    def test_comma_inside_text_stays_encoded(self):
        self.assertIn("%2C", frag(deeplink_for("class (i.e., non-founders)", URL)))

    def test_no_bare_reserved_chars_left(self):
        # & , - are the three chars the grammar reserves inside a component.
        for part in frag(deeplink_for("R&D spend, year-on-year", URL)).split(","):
            for ch in "&-":
                self.assertNotIn(ch, part)

    def test_hyphen_free_quote_is_unchanged(self):
        self.assertEqual(frag(deeplink_for("fee earning AUM", URL)),
                         "fee%20earning%20AUM")


if __name__ == "__main__":
    unittest.main()
