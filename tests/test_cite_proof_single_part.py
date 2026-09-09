#!/usr/bin/env python
"""t839 — deeplink_for must emit a SINGLE-PART text fragment, never `start,end`.

Two shipped defect classes both come from the two-part split, and both vanish
when the fragment carries the whole quote:

  t799 OVERLAP. The browser searches for textEnd only AFTER textStart's match
  ENDS. At exactly nine words `w[:5]` and `w[-5:]` share a word, so textEnd
  never occurs after textStart ends and NO range can match. The link is dead
  on arrival while `present`, the substring audit and the byte audit all pass,
  because none of them models range semantics.

  t813 RUNAWAY RANGE. textStart resolves against its FIRST occurrence on the
  page. When those five words recur earlier, the highlight starts at the wrong
  hit and runs all the way to textEnd, covering a block of unrelated text. One
  shipped link resolved 3,738 chars from 78 chars of anchor (47x); another
  13,229 from 54 (244x).

A whole-quote fragment has no textEnd to overlap and its range is the quote
itself, so neither class is expressible. The cost is URL length only.

Evidence these were live, not theoretical: one client deliverable shipped 65
links of which 4 were t799 overlaps and 2 were t813 runaways.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cite_proof import deeplink_for  # noqa: E402

URL = "https://example.com/doc.pdf"


def frag(link):
    return link.split("#:~:text=", 1)[1]


class TestSinglePartFragment(unittest.TestCase):
    def test_nine_word_quote_is_not_two_part(self):
        """The exact overlap case: w[:5] and w[-5:] share word five."""
        q = "one two three four five six seven eight nine"
        self.assertEqual(len(q.split()), 9)
        self.assertNotIn(",", frag(deeplink_for(q, URL)))

    def test_long_quote_carries_the_whole_text(self):
        q = ("The top decile of the HFRI Fund Weighted Composite Index "
             "delivered an average return of 47.3% in 2025")
        f = frag(deeplink_for(q, URL))
        self.assertNotIn(",", f)
        # every word survives, so the range is the quote and cannot run away
        for word in ("decile", "HFRI", "Composite", "2025"):
            self.assertIn(word, f)

    def test_no_length_threshold_switches_shape(self):
        """A quote must not change fragment SHAPE as it grows a word."""
        words = ["w%d" % i for i in range(1, 21)]
        for n in range(1, 21):
            f = frag(deeplink_for(" ".join(words[:n]), URL))
            self.assertNotIn(",", f, "%d-word quote emitted a two-part fragment" % n)

    def test_reserved_chars_still_encoded(self):
        """Single-part must not regress t811: - & , stay percent-encoded."""
        f = frag(deeplink_for("R&D spend, year-on-year, out-of-pocket", URL))
        for ch in "&-":
            self.assertNotIn(ch, f)
        self.assertNotIn(",", f)
        self.assertIn("%2D", f)
        self.assertIn("%26", f)
        self.assertIn("%2C", f)

    def test_short_quote_unchanged(self):
        self.assertEqual(frag(deeplink_for("fee earning AUM", URL)),
                         "fee%20earning%20AUM")


if __name__ == "__main__":
    unittest.main()
