#!/usr/bin/env python
"""B3 step 3 — the deeplink is GENERATED FROM THE PAGE, not typed from the quote.

t786, four times over: a quote proven `present` is not yet a link that works.
`cite_proof.match_norm` folds the dash family and the quote family so a proof
succeeds against a page whose real characters differ — then the deeplink ships
those FOLDED characters and Chromium, which folds neither, highlights nothing.
Shortening the anchor by hand fixed instances and never the class.

The fix is structural: hand the quote's location to Chrome's own generator and
let it build the fragment out of the PAGE's bytes. The typed quote then cannot
reach the URL at all, so no folding it survived can leak into a link.

Everything here is offline: a body on disk, jsdom, no network.

SECURITY (Phase 1 items; page bodies are external input from arbitrary hosts):
  A03 OS command  — the quote travels in a JSON job file, never in argv.
  A03 grammar     — ',' '&' '-' percent-encoded, so page text cannot inject a
                    second text directive (t811).
  A08 integrity   — helper output is json.loads'd and shape-checked; garbage
                    falls back and never fabricates a fragment.
  A05 misconfig   — jsdom runs with scripts off and no external resource loading.
  A06 DoS         — per-page timeout; the 8.8 MB Apollo XBRL body hung 7 minutes
                    in the spike, so the timeout branch is tested, not hoped for.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cite_proof as cp  # noqa: E402
import deliverable_link_rule as dlr  # noqa: E402

URL = "https://example.com/report"

# The page says U+2013; a transcription says ASCII '-'. cite_proof folds them
# together and proves `present`; a browser never will.
PAGE_HTML = ("<html><body><h1>Fund formation</h1>"
             "<p>Most launches complete in <b>4\u20136 weeks</b> from KYC "
             "clearance, the manager\u2019s counsel reports.</p>"
             "<p>Unrelated closing text.</p></body></html>")
PAGE_TEXT = ("Fund formation Most launches complete in 4\u20136 weeks from KYC "
             "clearance, the manager\u2019s counsel reports. "
             "Unrelated closing text.")
QUOTE_ASCII = "4-6 weeks from KYC clearance"  # the FOLDED transcription

NODE = shutil.which("node")
HELPER = Path(cp.__file__).parent / "fragment_from_page.mjs"
def _jsdom_available():
    """Check jsdom availability mirroring fragment_from_page.mjs moduleRoots()."""
    candidates = [HELPER.parent / "node_modules" / "jsdom"]
    for p in os.environ.get("NODE_PATH", "").split(os.pathsep):
        if p:
            candidates.append(Path(p) / "jsdom")
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "npm" / "node_modules" / "jsdom")
    return any(c.exists() for c in candidates)


HAVE_NODE = bool(NODE) and _jsdom_available()


def frag_of(link):
    return link.split("#:~:text=", 1)[1] if "#:~:text=" in link else None


@unittest.skipUnless(HAVE_NODE, "node + global jsdom required")
class TestGeneratesFromPageBytes(unittest.TestCase):
    def test_fragment_carries_the_pages_en_dash_not_the_quotes_hyphen(self):
        """The whole point of B3, in one assertion."""
        link = cp.deeplink_for(QUOTE_ASCII, URL, html=PAGE_HTML)
        f = frag_of(link)
        self.assertIsNotNone(f)
        self.assertIn("%E2%80%93", f,
                      "the fragment must carry the PAGE's en dash (U+2013)")
        self.assertNotIn("4%2D6", f,
                         "the typed quote's ASCII hyphen must not reach the URL")

    def test_the_generated_fragment_passes_the_gate(self):
        """Steps 2 and 3 must agree — a generator whose output its own gate
        rejects is what t870 measured before this work."""
        link = cp.deeplink_for(QUOTE_ASCII, URL, html=PAGE_HTML)
        self.assertEqual(
            dlr.check_fragment_in_text(link, PAGE_TEXT)["verdict"], "PASS",
            dlr.check_fragment_in_text(link, PAGE_TEXT)["reason"])

    def test_curly_apostrophe_survives_the_same_way(self):
        link = cp.deeplink_for("the manager's counsel", URL, html=PAGE_HTML)
        self.assertEqual(
            dlr.check_fragment_in_text(link, PAGE_TEXT)["verdict"], "PASS")

    def test_components_are_percent_encoded_so_page_text_cannot_inject(self):
        """A03 (grammar): a comma or hyphen in the page must stay INSIDE one
        component, not become a second directive (t811)."""
        body = ("<html><body><p>Fees, costs and carry-forward amounts are "
                "disclosed annually in the notes.</p></body></html>")
        link = cp.deeplink_for("Fees, costs and carry-forward amounts", URL,
                               html=body)
        directives = dlr.parse_text_directives(link)
        self.assertEqual(len(directives), 1, "must remain ONE text directive")
        self.assertNotIn("-", frag_of(link).replace("%2D", ""),
                         "a literal '-' in a component is t811's silent death")


@unittest.skipUnless(HAVE_NODE, "node + global jsdom required")
class TestHostileBodies(unittest.TestCase):
    def test_a_script_in_the_body_does_not_execute(self):
        """A05: bodies come from arbitrary hosts. jsdom must not run them."""
        body = ("<html><body><p>Break-even AUM rose to US$82.9m.</p>"
                "<script>document.body.textContent = 'PWNED';</script>"
                "</body></html>")
        link = cp.deeplink_for("Break-even AUM rose to US$82.9m", URL,
                               html=body)
        self.assertIsNotNone(frag_of(link))
        self.assertNotIn("PWNED", link)

    def test_helper_never_enables_scripts_or_resource_loading(self):
        """Source-level assertion, because the safe setting is a DEFAULT and a
        default is exactly what a later edit turns on without noticing."""
        src = HELPER.read_text(encoding="utf-8")
        self.assertNotIn("runScripts", src)
        self.assertNotIn("resources", src)


class TestFallbacksNeverFabricate(unittest.TestCase):
    """Every failure path must land on the existing whole-quote deeplink."""

    def test_no_body_falls_back_to_the_whole_quote(self):
        link = cp.deeplink_for(QUOTE_ASCII, URL)
        self.assertEqual(link, cp.deeplink_for(QUOTE_ASCII, URL, html=None))
        self.assertIn("weeks", frag_of(link))

    @unittest.skipUnless(HAVE_NODE, "node + global jsdom required")
    def test_a_quote_absent_from_the_body_falls_back(self):
        link = cp.deeplink_for("a sentence this page never carried", URL,
                               html=PAGE_HTML)
        self.assertEqual(
            link, cp.deeplink_for("a sentence this page never carried", URL))

    def test_timeout_falls_back_rather_than_raising(self):
        """A06: the 8.8 MB XBRL body did not finish in 7 minutes."""
        link = cp.deeplink_for(QUOTE_ASCII, URL, html=PAGE_HTML, timeout=0.001)
        self.assertEqual(link, cp.deeplink_for(QUOTE_ASCII, URL))

    def test_an_oversize_body_is_refused_without_spawning_anything(self):
        """The cost guard: parse time is not linear in body size, so a body over
        the cap must fall back IMMEDIATELY rather than burn the whole timeout."""
        spawned = []
        orig = cp.subprocess.run
        cp.subprocess.run = lambda *a, **k: spawned.append(a) or (_ for _ in ()).throw(
            AssertionError("must not spawn for an over-cap body"))
        try:
            huge = PAGE_HTML + "x" * cp._FRAGMENT_MAX_BODY_CHARS
            link = cp.deeplink_for(QUOTE_ASCII, URL, html=huge)
        finally:
            cp.subprocess.run = orig
        self.assertEqual(spawned, [])
        self.assertEqual(link, cp.deeplink_for(QUOTE_ASCII, URL))

    def test_garbage_helper_output_falls_back(self):
        """A08: never trust the subprocess's stdout shape."""
        orig = cp.subprocess.run
        cp.subprocess.run = lambda *a, **k: subprocess.CompletedProcess(
            a[0] if a else [], 0, stdout="not json at all", stderr="")
        try:
            link = cp.deeplink_for(QUOTE_ASCII, URL, html=PAGE_HTML)
        finally:
            cp.subprocess.run = orig
        self.assertEqual(link, cp.deeplink_for(QUOTE_ASCII, URL))

    def test_wellformed_but_wrong_shape_output_falls_back(self):
        orig = cp.subprocess.run
        cp.subprocess.run = lambda *a, **k: subprocess.CompletedProcess(
            a[0] if a else [], 0,
            stdout=json.dumps({"ok": True, "fragment": {"textStart": ""}}),
            stderr="")
        try:
            link = cp.deeplink_for(QUOTE_ASCII, URL, html=PAGE_HTML)
        finally:
            cp.subprocess.run = orig
        self.assertEqual(link, cp.deeplink_for(QUOTE_ASCII, URL))


class TestNoCommandInjection(unittest.TestCase):
    """A03: the quote is external input and must never reach argv."""

    def test_the_quote_travels_in_a_file_not_in_argv(self):
        seen = {}

        def fake_run(argv, **kw):
            seen["argv"] = list(argv)
            seen["shell"] = kw.get("shell", False)
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        orig = cp.subprocess.run
        cp.subprocess.run = fake_run
        nasty = '"; touch pwned.txt; echo "$(whoami)'
        try:
            cp.deeplink_for(nasty, URL, html=PAGE_HTML)
        finally:
            cp.subprocess.run = orig
        self.assertIn("argv", seen, "the helper must be invoked at all")
        self.assertFalse(seen["shell"], "shell=True is never acceptable here")
        for arg in seen["argv"]:
            self.assertNotIn("whoami", arg)
            self.assertNotIn("touch pwned", arg)

    @unittest.skipUnless(HAVE_NODE, "node + global jsdom required")
    def test_a_hostile_quote_creates_no_file_and_raises_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            os.chdir(d)
            try:
                cp.deeplink_for('"; touch pwned.txt; echo "', URL,
                                html=PAGE_HTML)
                self.assertEqual(os.listdir(d), [])
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main(verbosity=2)
