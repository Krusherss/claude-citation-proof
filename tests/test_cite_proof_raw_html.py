#!/usr/bin/env python
"""t871 — cite_proof must PERSIST THE RAW HTML it fetched, not throw it away.

`fetch_text` stripped every tag and returned text only, so `.proof/url_cache`
held tag-stripped text and nothing else. A W3C text fragment has to be generated
from a DOM Range over the real markup (Chrome's own generator takes a Range), and
there was no body on disk to build one from — only the SingleFile archive, which
in this project exists for 29 of 41 cached URLs and is an EMPTY FILE for PDF-mode
entries. That absence, not the algorithm, is what blocked B3.

The body is now returned by `fetch_text` and written to a sidecar beside the
cache entry, because a multi-megabyte body inside the JSON would blow the
existing 2,000,000-char text cap and be re-serialized on every merge.

CONTRACT NOTE (load-bearing): callers must tolerate a 2-TUPLE return. The test
suite monkeypatches `fetch_text` with `(text, mode)` fakes whose whole job is to
make a live fetch loud; a fake that no longer matches the call site is a fake
that lets the network through silently.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cite_proof as cp  # noqa: E402

URL = "https://example.com/report"
PAGE_HTML = ("<html><body><p>Break-even AUM rose to "
             "<b>US$82.9m</b> in 2025.</p></body></html>")
PAGE_TEXT = " Break-even AUM rose to  US$82.9m  in 2025. "


class TestFetchTextReturnsRawHtml(unittest.TestCase):
    def test_html_fetch_returns_three_parts_with_the_body(self):
        """RED: fetch_text returns (text, mode) and the body is unrecoverable."""
        class FakeResp:
            headers = {"Content-Type": "text/html; charset=utf-8"}

            def read(self):
                return PAGE_HTML.encode("utf-8")

        orig = cp.urllib.request.urlopen
        cp.urllib.request.urlopen = lambda *a, **k: FakeResp()
        try:
            res = cp.fetch_text(URL)
        finally:
            cp.urllib.request.urlopen = orig
        self.assertEqual(len(res), 3, "fetch_text must return (text, mode, raw_html)")
        text, mode, raw = res
        self.assertEqual(mode, "html")
        self.assertIn("US$82.9m", text)
        self.assertNotIn("<b>", text, "the stripped text must stay stripped")
        self.assertEqual(raw, PAGE_HTML, "the raw body must survive verbatim")

    def test_unpack_fetch_tolerates_the_legacy_two_tuple(self):
        """A monkeypatched (text, mode) fake must not break the call site."""
        self.assertEqual(cp.unpack_fetch(("t", "html")), ("t", "html", None))
        self.assertEqual(cp.unpack_fetch(("t", "html", "<p>t</p>")),
                         ("t", "html", "<p>t</p>"))

    def test_pdf_fetch_has_no_html_and_says_so(self):
        """PDF mode has no markup; the slot must be None, never a lie."""
        class FakeResp:
            headers = {"Content-Type": "application/pdf"}

            def read(self):
                return b"%PDF-1.4 not-a-real-pdf"

        orig = cp.urllib.request.urlopen
        cp.urllib.request.urlopen = lambda *a, **k: FakeResp()
        try:
            cp.fetch_text(URL + ".pdf")
        except Exception:
            self.skipTest("PyMuPDF unavailable or refused the stub PDF")
        finally:
            cp.urllib.request.urlopen = orig


class TestHtmlSidecar(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory()
        self.proof = Path(self.tmp.name) / ".proof"

    def tearDown(self):
        self.tmp.cleanup()

    def test_update_url_cache_writes_the_body_to_a_sidecar(self):
        cp.update_url_cache(self.proof, URL, text=PAGE_TEXT, mode="html",
                            html=PAGE_HTML)
        entry = json.loads(cp.url_cache_path(self.proof, URL)
                           .read_text(encoding="utf-8"))
        self.assertIn("html", entry, "cache entry must record the sidecar path")
        from pathlib import Path
        self.assertTrue(Path(entry["html"]).exists())
        self.assertEqual(Path(entry["html"]).read_text(encoding="utf-8"),
                         PAGE_HTML)
        self.assertNotIn("<b>", entry.get("text", ""),
                         "the body belongs in the sidecar, not the JSON")

    def test_cached_html_reads_it_back_and_misses_honestly(self):
        self.assertIsNone(cp.cached_html(self.proof, URL),
                          "no cache entry must be a miss, never an error")
        cp.update_url_cache(self.proof, URL, text=PAGE_TEXT, mode="html",
                            html=PAGE_HTML)
        self.assertEqual(cp.cached_html(self.proof, URL), PAGE_HTML)

    def test_a_pathological_body_is_refused_not_written(self):
        """Resource bound: the sidecar is capped, and the cap is a MISS."""
        huge = "x" * (cp._URL_CACHE_HTML_CAP + 1)
        cp.update_url_cache(self.proof, URL, text=PAGE_TEXT, mode="html",
                            html=huge)
        self.assertIsNone(cp.cached_html(self.proof, URL))

    def test_html_merges_without_disturbing_text_or_its_clock(self):
        cp.update_url_cache(self.proof, URL, text=PAGE_TEXT, mode="html")
        before = json.loads(cp.url_cache_path(self.proof, URL)
                            .read_text(encoding="utf-8"))
        cp.update_url_cache(self.proof, URL, html=PAGE_HTML)
        after = json.loads(cp.url_cache_path(self.proof, URL)
                           .read_text(encoding="utf-8"))
        self.assertEqual(before["text"], after["text"])
        self.assertEqual(before["fetched_at_epoch"], after["fetched_at_epoch"],
                         "an html merge must not touch the freshness clock")
        self.assertEqual(cp.cached_html(self.proof, URL), PAGE_HTML)


class TestBuildProofPersistsTheBody(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory()
        self.proof = Path(self.tmp.name) / ".proof"

    def tearDown(self):
        self.tmp.cleanup()

    def _stub(self, fake_fetch):
        cp.capture_screenshot = lambda u, q, p: (False, False, "")
        cp.capture_archive = lambda u, p, t: None
        cp.fetch_text = fake_fetch

    def test_live_fetch_puts_the_body_on_disk(self):
        orig = (cp.fetch_text, cp.capture_screenshot, cp.capture_archive)
        self._stub(lambda url: (PAGE_TEXT, "html", PAGE_HTML))
        try:
            rec = cp.build_proof(URL, "Break-even AUM rose to", self.proof,
                                 archive_tier="none", wayback_on=False)
        finally:
            (cp.fetch_text, cp.capture_screenshot, cp.capture_archive) = orig
        self.assertEqual(rec["verdict"], "present")
        self.assertEqual(cp.cached_html(self.proof, URL), PAGE_HTML)

    def test_a_two_tuple_fake_still_drives_build_proof(self):
        """Back-compat: the t564 fakes return (text, mode) and must keep working."""
        orig = (cp.fetch_text, cp.capture_screenshot, cp.capture_archive)
        self._stub(lambda url: (PAGE_TEXT, "html"))
        try:
            rec = cp.build_proof(URL, "Break-even AUM rose to", self.proof,
                                 archive_tier="none", wayback_on=False)
        finally:
            (cp.fetch_text, cp.capture_screenshot, cp.capture_archive) = orig
        self.assertEqual(rec["verdict"], "present")
        self.assertIsNone(cp.cached_html(self.proof, URL))


if __name__ == "__main__":
    unittest.main(verbosity=2)
