#!/usr/bin/env python3
"""cite_proof.py — portable proof builder for the Provable Docs system.

Given a live web source and a verbatim quote expected on it, build a
link-rot-proof bundle of evidence and record an honest verdict:

  1. deeplink   — W3C Text Fragment (url#:~:text=...) that, where supported,
                  jumps to and highlights the quote on the live page.
  2. screenshot — Playwright loads the page, paints the highlight with injected
                  JS (headless Chromium does NOT apply #:~:text= itself), and
                  saves a viewport JPEG q70 to .proof/shots/<hash>.jpg.
  3. archive    — SingleFile CLI snapshots the page to .proof/archive/<hash>.html
                  (default --block-images, ~150 KB; "full" keeps images).
  4. wayback    — optional ~0-byte third-party witness URL (web.archive.org).
  5. verdict    — verbatim-substring check (present | absent | unreadable),
                  zero ML, deterministic, after unicode-dash + whitespace norm.

The result is appended (upsert by key) to .proof/proof_manifest.json keyed by
sha256("<url>|<quote>") — byte-for-byte the same key proof_gate.py computes.

HARD CONSTRAINTS
  - NO Claude tools inside. Never call WebFetch / WebSearch / any MCP tool.
    Everything goes through subprocess HTTP (urllib), Playwright, and the
    SingleFile CLI directly, so the PostToolUse source-logger never re-fires.
  - FAIL-HONEST. Each external step is isolated: if Playwright is missing, the
    quote is not found, SingleFile is absent, or a host is bot-walled, the other
    steps still run and the manifest records honest verdict/null fields. A proof
    is NEVER fabricated.

USAGE
  python cite_proof.py <url> "<quote>"
  python cite_proof.py <url> "<quote>" --proof-dir .proof \
      --archive noimg --wayback off --no-cache --re-anchor off

Defaults: --archive noimg, --wayback off, cache ON (TTL 24 h), re-anchor ON.

Page text is read through a shared URL-level cache
(.proof/url_cache/) so repeat quotes against the same page never re-fetch or
re-archive it; a verdict=absent quote triggers ONE re-anchor retry that proves
the best verbatim page snippet instead (original absent record kept as the
honest drop record) — an absent fact is never dropped silently.
"""
from __future__ import annotations

import argparse
import datetime
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.request
from pathlib import Path
from urllib.parse import quote as urlq
from urllib.parse import urlparse, urlsplit, urlunsplit

# A real desktop Chrome UA — many hosts bot-wall the default urllib/Playwright UA.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# Bot-wall / interstitial sentinels — page counts as "rendered" only if NONE
# are present AND innerText is long enough (mirrors the reference impl).
_INTERSTITIAL = ("checking your browser", "not automatically redirected",
                 "enable javascript and cookies", "verifying you are human",
                 "request could not be processed", "are you a robot")

# Injected highlight JS — VISUAL-ONLY (paints the spot for the screenshot; never
# decides the verdict; that is decide_verdict's job on verbatim text). Adapted
# from the DRUGSHEET4ED reference (_clicktest_shot.py): unicode-dash + whitespace
# normalization, tree-walker then block-element fallback, scrollIntoView. Headless
# page.goto ignores #:~:text=, so we paint it ourselves. The leading-span shrink
# is FLOORED at ~20 chars (whole quote if shorter) so it can never collapse to a
# stray common word like "this" and paint the wrong place — the failure mode that,
# combined with the old fuzzy-wins verdict, certified fabricated quotes.
HIGHLIGHT_JS = r"""
(quote) => {
  const norm = s => (s||'').replace(/[‐‑‒–—−]/g,'-').replace(/\s+/g,' ').trim().toLowerCase();
  // try progressively shorter leading spans so punctuation differences don't kill it
  const words = quote.split(/\s+/);
  const minLen = Math.min(20, norm(quote).length);  // shrink floor (visual safety)
  for (let n = words.length; n >= 1; n--) {
    const target = norm(words.slice(0, n).join(' '));
    if (target.length < minLen) break;  // monotonic shrink: stop at the floor, never match a stray word
    // 1) single text node
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node;
    while (node = w.nextNode()) {
      if (norm(node.nodeValue).includes(target)) {
        const el = node.parentElement;
        el.style.backgroundColor = '#fff200';
        el.style.outline = '3px solid #d40000';
        el.scrollIntoView({block:'center'});
        return {found:true, words:n, where:'textnode', tag:el.tagName};
      }
    }
    // 2) block element innerText (snippet may span inline children)
    for (const el of document.querySelectorAll('p,td,th,li,h1,h2,h3,span,div,caption')) {
      if (norm(el.innerText).includes(target) && el.children.length < 40) {
        el.style.backgroundColor = '#fff200';
        el.style.outline = '3px solid #d40000';
        el.scrollIntoView({block:'center'});
        return {found:true, words:n, where:'block', tag:el.tagName};
      }
    }
  }
  return {found:false};
}
"""


# --------------------------------------------------------------------------- #
# Keys, hashing, normalization                                                #
# --------------------------------------------------------------------------- #

def canon_url(url: str) -> str:
    """Canonical URL identity for keying. MINIMAL by design — normalize only the
    invisible/ambiguous (each rule RFC-3986- or tool-artifact-justified); preserve
    everything meaningful so two different resources never collide:
      - strip surrounding whitespace
      - strip a trailing #:~:text= text-fragment (the tool's OWN addition; a real
        #section fragment is part of the resource and is kept)
      - lowercase scheme + host only (RFC 3986 §3.1/§3.2.2: case-insensitive)
      - PRESERVE path/query case, trailing slash, www, percent-encoding, port
    Mirrored byte-for-byte by proof_gate.py; conformance pinned by key_vectors.json."""
    parts = urlsplit(strip_fragment(url.strip()))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                       parts.path, parts.query, parts.fragment))


def canon_quote(q: str) -> str:
    """Canonical quote identity for keying — exactly norm(): NFKC + dash-family→'-'
    + whitespace-collapse + strip, NO lowercasing. Case is part of verbatim
    identity and is preserved (so 'Aspirin' and 'aspirin' key distinctly). The
    gate must strip the surrounding "" of a `Quote: "..."` block BEFORE calling
    this; quote-unwrapping is a parse step, not a normalization step."""
    return norm(q)


def proof_key(url: str, quote: str) -> str:
    """sha256("<canon_url>|<canon_quote>") — MUST stay byte-for-byte identical to
    the key proof_gate.py derives from a (Src:, Quote:) block. The two sides
    cannot share code (the gate is a standalone ~/.claude/hooks/ file), so both
    canonicalize first via the rules above and the shared key_vectors.json corpus
    pins them against drift. Literal pipe separator (residual: a '|' inside a canon
    field is an accepted, astronomically-unlikely collision risk — documented)."""
    return hashlib.sha256(
        f"{canon_url(url)}|{canon_quote(quote)}".encode("utf-8")).hexdigest()


def norm(s: str) -> str:
    """Normalize for the verbatim-substring verdict: NFKC, collapse the unicode
    dash family to ASCII '-', collapse whitespace runs, trim. No lowercasing
    here (the substring check lowercases both sides itself)."""
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[‐‑‒–—−]", "-", s)
    return re.sub(r"\s+", " ", s).strip()


# Curly-quote family -> ASCII, PRESENCE CHECK ONLY (t582): a transcription
# writes ' / " where the page serves U+2018/2019/201C/201D (or vice versa).
_CURLY_QUOTES = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
})


def match_norm(s: str) -> str:
    """Normalization for the PRESENCE CHECK ONLY — NEVER for keying (canon_quote
    / proof_key must stay byte-for-byte in lockstep with proof_gate.py and never
    call this): norm() + lowercase + curly-quote fold + PDF line-break
    de-hyphenation + punctuation-adjacent space fold, applied to BOTH needle
    and haystack.

    t582: extracted PDF text splits words across line breaks as 'man- agement' /
    'regu- latory', so any long anchor spanning one silently returns absent while
    the fact is true. The regex removes '- ' only BETWEEN word chars, so real
    hyphenated compounds ('cost-effective', no space) are untouched. fi/fl
    ligatures — the other PDF trap — are already folded by norm()'s NFKC.

    t582 (remaining axes): curly quotes fold to ASCII; whitespace ADJACENT TO
    PUNCTUATION folds away, because fetch_text replaces every HTML tag with a
    space, so inline markup (<sup>1</sup>, XBRL spans, tag-broken parens)
    injects spaces the rendered page never shows. Word-space-word is NEVER
    folded ('the rapist' cannot become 'therapist'). Order is load-bearing:
    de-hyphenation must run BEFORE the space fold, which would otherwise eat
    the '- ' pattern and strand 'man-agement'."""
    s = norm(s).lower().translate(_CURLY_QUOTES)
    s = re.sub(r"(?<=\w)- (?=\w)", "", s)
    return re.sub(r"\s+(?=[^\w\s])|(?<=[^\w\s])\s+", "", s)


def strip_fragment(url: str) -> str:
    """Drop any pre-existing #:~:text= (and only that fragment) so we never feed
    an already-fragmented URL back in and produce a double-fragment deeplink."""
    return url.split("#:~:text=")[0]


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# 1. Deeplink                                                                  #
# --------------------------------------------------------------------------- #

def frag_component(text: str) -> str:
    """Percent-encode ONE text-fragment component (a textStart or a textEnd).

    t811: the grammar RESERVES '-' — it marks `prefix-,` and `,-suffix` — so a
    literal '-' inside a component makes Chromium fail the match SILENTLY: the
    page opens at the top, no highlight, and every presence check still passes.
    urllib.parse.quote will NOT do this for us: '-' lives in its _ALWAYS_SAFE
    set, so it survives quoting untouched. safe="" covers ',' '&' '#' '%'; the
    '-' needs its own pass.

    Proven in real Chromium (Playwright, target below the fold, detector
    scrollY > 0): the same fragment is DEAD with a literal '-' and MATCHES with
    '%2D', in both single-part and two-part shapes — see
    tests/frag_hyphen_chromium_matrix.py, which refuses to report unless it
    first reproduces the observed dead/alive baseline.
    """
    return urlq(text, safe="").replace("-", "%2D")


def deeplink_for(quote: str, url: str) -> str:
    """Build url#:~:text=start,end (W3C Text Fragment). Whole quote if <=8 words,
    else first5,last5. frag_component percent-encodes the chars the grammar
    reserves inside each half (',' '&' '-'); the single literal ',' between the
    two halves stays raw as the range separator.

    t597: drop the WHOLE fragment here — not just a pre-existing '#:~:text='
    (strip_fragment's job) — because a surviving '#section' anchor would collide
    with the appended text fragment and yield an invalid double-'#' URL. The
    shared strip_fragment/canon_url KEEP the anchor for keying identity; only
    this human-facing deeplink strips it."""
    parts = urlsplit(url)
    clean = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
    w = quote.split()
    if len(w) <= 8:
        frag = frag_component(quote)
    else:
        frag = frag_component(" ".join(w[:5])) + "," + frag_component(" ".join(w[-5:]))
    return f"{clean}#:~:text={frag}"


# --------------------------------------------------------------------------- #
# Bot-block handling (ported pattern from the reference impl)                  #
# --------------------------------------------------------------------------- #

def render_host(url: str):
    """Return (render_url, original_host). PMC is bot-walled in a headless
    browser, so render via the EuropePMC mirror — but the cited URL stays the
    original (the reader clicks the original, only rendering uses the mirror)."""
    host = urlparse(url).netloc.replace("www.", "")
    if host == "pmc.ncbi.nlm.nih.gov":
        m = re.search(r"(PMC\d+)", url)
        if m:
            return f"https://europepmc.org/article/PMC/{m.group(1)}", host
    return url, host


# --------------------------------------------------------------------------- #
# 2. Screenshot (Playwright)                                                   #
# --------------------------------------------------------------------------- #

def capture_screenshot(url: str, quote: str, out_path: Path):
    """Render the page; if the quote is verbatim-present in the rendered DOM, paint
    the highlight and save a viewport JPEG q70.

    Returns (rendered_ok, rendered_found, dom_text):
      rendered_ok    bool — page passed the interstitial/length gate (a real page
                     was reached), regardless of whether the quote is on it. False
                     if Playwright/browser is missing or the render never settled.
      rendered_found bool — the quote appears VERBATIM (normalized, case-insensitive)
                     in the rendered DOM innerText. This is a deterministic check and
                     a verdict input; the fuzzy HIGHLIGHT_JS paint is NOT.
      dom_text       str — the rendered DOM innerText ('' when not rendered), so the
                     re-anchor retry can search JS-rendered pages whose served HTML
                     lacks the text (t564 item 2).

    The screenshot is saved ONLY when rendered_found — so there is no orphan .jpg on
    absent pages and, critically, no screenshot 'evidence' for a fabricated quote.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:  # Playwright not installed -> honest, not fatal
        print(f"  [screenshot] Playwright unavailable: {type(e).__name__}: "
              f"{str(e)[:80]}", file=sys.stderr)
        return False, False, ""

    render_url, host = render_host(url)
    rendered_ok = False
    rendered_found = False
    dom_text = ""
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as e:  # browser binary missing -> honest
                print(f"  [screenshot] chromium launch failed: "
                      f"{type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
                return False, False, ""
            ctx = browser.new_context(
                user_agent=UA, viewport={"width": 1000, "height": 1400})
            page = ctx.new_page()
            try:
                page.goto(render_url, wait_until="load", timeout=45000)
                # Render-readiness / interstitial poll: up to 10 x 1.5s.
                for _ in range(10):
                    page.wait_for_timeout(1500)
                    t = page.evaluate(
                        "document.body?document.body.innerText:''")
                    low = t.lower()
                    if not any(m in low for m in _INTERSTITIAL) and len(t) > 500:
                        rendered_ok = True
                        break
                # EuropePMC serves abstract-only until "Free full text" is
                # clicked; body-text quotes need it expanded first.
                if rendered_ok and host == "pmc.ncbi.nlm.nih.gov":
                    try:
                        ft = page.query_selector("a[href='#free-full-text']")
                        if ft:
                            before = len(page.evaluate(
                                "document.body.innerText"))
                            ft.click()
                            for _ in range(12):
                                page.wait_for_timeout(1500)
                                if len(page.evaluate(
                                        "document.body.innerText")) > before + 5000:
                                    break
                    except Exception as e:
                        print(f"  [ft expand] {host}: {str(e)[:60]}",
                              file=sys.stderr)
                if rendered_ok:
                    # Deterministic verdict input: is the quote verbatim in the
                    # rendered DOM text? (Distinct from the fuzzy paint below.)
                    dom_text = page.evaluate(
                        "document.body?document.body.innerText:''")
                    rendered_found = match_norm(quote) in match_norm(dom_text)
                    # Fuzzy highlight = VISUAL-ONLY: it scrolls the quote into view
                    # and paints it for the screenshot. Its hit/miss does NOT decide
                    # the verdict (see decide_verdict).
                    page.evaluate(HIGHLIGHT_JS, quote)
                    # Capture ONLY when the quote is verbatim-present. HIGHLIGHT_JS
                    # already ran scrollIntoView({block:'center'}), so a VIEWPORT shot
                    # (not full_page) frames the quote plus a screenful of context —
                    # bounded size regardless of page length. Whole-page fidelity is
                    # the archive's job; the deeplink is the live-jump.
                    if rendered_found:
                        out_path.parent.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(out_path),
                                        type="jpeg", quality=70)
            except Exception as e:
                # goto / evaluate / screenshot threw -> stay honest.
                print(f"  [screenshot] {host}: {type(e).__name__}: "
                      f"{str(e)[:80]}", file=sys.stderr)
                rendered_ok = False
                rendered_found = False
                dom_text = ""
            finally:
                browser.close()
    except Exception as e:  # sync_playwright context itself failed
        print(f"  [screenshot] playwright context failed: "
              f"{type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
        return False, False, ""
    return rendered_ok, rendered_found, dom_text


# --------------------------------------------------------------------------- #
# Verbatim-substring verdict via served text (HTTP / PDF)                      #
# --------------------------------------------------------------------------- #

def fetch_text(url: str):
    """Return (text, mode). Raw HTTP fetch — survives hosts whose *visual*
    render is bot-walled but whose *served* HTML/PDF text is not. PDF -> text via
    PyMuPDF (if available); else HTML tag-stripped. mode in {"html","pdf"}.

    Scheme guard (security): only http(s); refuse file:// and friends."""
    if not re.match(r"https?://", url):
        raise ValueError(f"refusing non-http URL: {url!r}")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    r = urllib.request.urlopen(req, timeout=45)  # nosec B310  # nosemgrep: scheme guarded to http(s) directly above
    ct = r.headers.get("Content-Type", "").lower()
    body = r.read()
    if "pdf" in ct or url.lower().endswith(".pdf"):
        import fitz  # PyMuPDF — optional
        doc = fitz.open(stream=body, filetype="pdf")
        return " ".join(str(page.get_text()) for page in doc), "pdf"
    txt = body.decode("utf-8", "replace")
    txt = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", txt)
    txt = re.sub(r"<[^>]+>", " ", txt)
    import html as _html
    return _html.unescape(txt), "html"


def served_text_has_quote(url: str, quote: str):
    """True iff the quote appears verbatim (after normalization, case-insensitive)
    in the served page text. (text_found_bool, reachable_bool).

    reachable=False means the served-text channel itself failed (network, scheme,
    PDF lib missing) — distinct from "reached the page but the quote was absent"."""
    try:
        text, _ = fetch_text(url)
    except Exception as e:
        print(f"  [served-text] fetch failed: {type(e).__name__}: "
              f"{str(e)[:80]}", file=sys.stderr)
        return False, False
    return (match_norm(quote) in match_norm(text)), True


def decide_verdict(served_found: bool, served_reachable: bool,
                   rendered_found: bool, rendered_ok: bool) -> str:
    """Compose the honest verdict. Verbatim presence — in the served HTTP text OR
    the rendered DOM innerText — is the SOLE authority. The fuzzy DOM highlight
    (HIGHLIGHT_JS) is deliberately NOT an input here: it is visual-only and must
    never be able to make a fabricated quote 'present' (the original bug).

      present     quote is verbatim-present in at least one channel
      absent      at least one channel reached the page but lacked the quote
      unreadable  no channel reached the page at all
    """
    if served_found or rendered_found:
        return "present"
    if served_reachable or rendered_ok:
        return "absent"
    return "unreadable"


# --------------------------------------------------------------------------- #
# Shared URL-level fetch cache (t564 item 3)                                   #
# --------------------------------------------------------------------------- #
# Research fan-outs prove several quotes against the SAME page (and item 2's
# re-anchor retry re-proves a corrected quote against the page it just fetched).
# Without a cache each proof re-fetches the page; with it, the served text and
# the URL's one archive are fetched ONCE per URL and shared by every subsequent
# proof — including proofs from concurrent subagents (cache lives in the shared
# .proof/ that all agents of a session write, keyed by canon_url so the key is
# quote-independent). Correctness note: the cache can only ever say "present"
# early (verbatim hit in cached text); a cached MISS still runs the full live
# pipeline, so a page edit can flip absent->present next run but a stale cache
# can never fabricate a presence that was not served.

URL_CACHE_TTL_DEFAULT = 86400  # seconds; cached served text is trusted for 24 h
_URL_CACHE_TEXT_CAP = 2_000_000  # chars; refuse to cache pathological pages


def url_cache_path(proof_dir: Path, url: str) -> Path:
    """.proof/url_cache/<sha256(canon_url)>.json — URL-level, quote-independent."""
    h = hashlib.sha256(canon_url(url).encode("utf-8")).hexdigest()
    return proof_dir / "url_cache" / f"{h}.json"


def load_url_cache(proof_dir: Path, url: str, ttl: float):
    """Return the cached entry dict if fresh (age <= ttl), else None. Fail-open:
    a missing/corrupt/expired cache file is a miss, never an error."""
    try:
        entry = json.loads(url_cache_path(proof_dir, url)
                           .read_text(encoding="utf-8"))
        if not isinstance(entry, dict) or not entry.get("text"):
            return None
        if time.time() - float(entry.get("fetched_at_epoch", 0)) > ttl:
            return None
        return entry
    except Exception:
        return None


def update_url_cache(proof_dir: Path, url: str, *, text=None, mode=None,
                     archive=None) -> None:
    """Merge fields into the URL's cache entry atomically (tempfile+os.replace;
    no lock — concurrent writers fetched the same page, last-writer-wins is
    safe). `text` refreshes the entry and its fetched_at clock; `archive` merges
    WITHOUT touching the clock (text age governs freshness) so the URL's one
    archive is reused, never re-captured, by later quotes against the page."""
    if text is not None and len(text) > _URL_CACHE_TEXT_CAP:
        return
    p = url_cache_path(proof_dir, url)
    try:
        prior = {}
        if p.exists():
            try:
                prior = json.loads(p.read_text(encoding="utf-8"))
                if not isinstance(prior, dict):
                    prior = {}
            except Exception:
                prior = {}
        entry = dict(prior)
        entry["url"] = url
        entry["canon_url"] = canon_url(url)
        if text is not None:
            entry["text"] = text
            entry["mode"] = mode
            entry["fetched_at"] = now_iso()
            entry["fetched_at_epoch"] = time.time()
        if archive is not None:
            entry["archive"] = archive
        if not entry.get("text"):
            return  # nothing useful to keep (archive alone can't seed an entry)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.parent / f"{p.name}.tmp.{os.getpid()}"
        tmp.write_text(json.dumps(entry), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as e:
        print(f"  [url-cache] write failed: {type(e).__name__}: {str(e)[:80]}",
              file=sys.stderr)


# --------------------------------------------------------------------------- #
# Re-anchor retry (t564 item 2)                                                #
# --------------------------------------------------------------------------- #
# A verdict=absent quote is usually a NEAR-MISS transcription (WebFetch
# summarizer paraphrase, memory drift, punctuation) of text that IS on the
# page. Before the fact is dropped, find the best VERBATIM page snippet to
# re-anchor the citation to. The candidate is always literal page text —
# fabrication is impossible, and the recursive build_proof re-verifies it
# through the normal verdict pipeline anyway. The floors only prevent
# anchoring to an unrelated stray phrase (wrong-place citation).

_REANCHOR_MIN_SPAN_WORDS = 3     # anchor must share >=3 contiguous quote words
_REANCHOR_MIN_SPAN_CHARS = 15    # ... totalling >=15 chars (HIGHLIGHT_JS floor+)
_REANCHOR_MIN_RATIO = 0.4        # candidate must resemble the intended quote
_REANCHOR_WINDOW_CHARS = 400     # sentence-expansion search window per side


def find_reanchor(quote: str, text: str):
    """Return (candidate, similarity) — the best verbatim page snippet to
    re-anchor an absent quote to — or None when no safe candidate exists.

    Mirrors HIGHLIGHT_JS's monotonic-shrink discipline: find the LONGEST
    contiguous word-span of the quote verbatim-present in the page (floored at
    {_REANCHOR_MIN_SPAN_WORDS} words / {_REANCHOR_MIN_SPAN_CHARS} chars), then
    expand that anchor to its containing sentence in the PAGE's own words and
    gate on SequenceMatcher similarity vs the intended quote."""
    dehyph = lambda s: re.sub(r"(?<=\w)- (?=\w)", "", s)  # match_norm's, case-kept
    nq = dehyph(norm(quote))
    nt = dehyph(norm(text))
    ntl = nt.lower()
    if len(ntl) != len(nt):
        ntl = nt  # unicode lowering shifted offsets; fall back case-sensitive
    words = nq.split()
    anchor = None
    for n in range(len(words), _REANCHOR_MIN_SPAN_WORDS - 1, -1):
        for start in range(0, len(words) - n + 1):
            span = " ".join(words[start:start + n])
            if len(span) < _REANCHOR_MIN_SPAN_CHARS:
                continue
            idx = ntl.find(span.lower() if ntl is not nt else span)
            if idx >= 0:
                anchor = (idx, idx + len(span))
                break
        if anchor:
            break
    if not anchor:
        return None
    lo, hi = anchor
    # Expand to sentence-ish page-native boundaries around the anchor, capped.
    win_lo = max(0, lo - _REANCHOR_WINDOW_CHARS)
    dot = ntl.rfind(". ", win_lo, lo)
    lo2 = dot + 2 if dot != -1 else win_lo
    if lo2 > 0 and nt[lo2 - 1] != " " and dot == -1:  # window cut mid-word
        sp = nt.find(" ", lo2, lo)
        lo2 = sp + 1 if sp != -1 else lo2
    win_hi = min(len(nt), hi + _REANCHOR_WINDOW_CHARS)
    dot = ntl.find(". ", hi, win_hi)
    hi2 = dot + 1 if dot != -1 else win_hi
    if hi2 < len(nt) and nt[hi2] != " " and dot == -1:  # window cut mid-word
        sp = nt.rfind(" ", hi, hi2)
        hi2 = sp if sp != -1 else hi2
    candidate = nt[lo2:hi2].strip()
    if not candidate:
        return None
    sim = difflib.SequenceMatcher(None, nq.lower(),
                                  candidate.lower()).ratio()
    if sim < _REANCHOR_MIN_RATIO:
        return None
    return candidate, sim


# --------------------------------------------------------------------------- #
# 3. Archive (SingleFile CLI)                                                  #
# --------------------------------------------------------------------------- #

def _single_file_cmd():
    """Locate the SingleFile CLI on PATH. Returns an argv prefix list or None.
    Tries the two published binary names; npx is intentionally NOT auto-run
    (it can hang on a first-time download prompt)."""
    for name in ("single-file", "single-file-cli"):
        found = shutil.which(name)
        if found:
            return [found]
    return None


def capture_archive(url: str, out_path: Path, tier: str):
    """SingleFile snapshot -> .proof/archive/<hash>.html. tier:
      "none"  -> skip (returns None)
      "noimg" -> pass --block-images (~150 KB, the default tier)
      "full"  -> keep images
    Returns the archive path string on success, else None (honest — never fake).
    """
    if tier == "none":
        return None
    cmd = _single_file_cmd()
    if not cmd:
        print("  [archive] single-file CLI not on PATH "
              "(install: npm i -g single-file-cli) — skipping archive",
              file=sys.stderr)
        return None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    argv = list(cmd)
    if tier == "noimg":
        argv.append("--block-images=true")
    argv += [url, str(out_path)]
    # Windows: single-file-cli is a .cmd shim (cmd.exe -> node -> chromium); without
    # CREATE_NO_WINDOW each invocation flashes a visible console window. Per cited
    # claim this fires alongside the Playwright screenshot launch, so a multi-source
    # proof run bursts into many black windows. CREATE_NO_WINDOW suppresses the
    # console for this process and its children; no-op (0) off-Windows.
    _NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    # t581: NEVER capture the shim's pipes. The node->chromium grandchildren
    # inherit and hold the stdout pipe open, so a captured run() that times out
    # kills only cmd.exe and then blocks forever in its post-kill communicate()
    # (observed: batch rows hanging past 600s despite timeout=120). DEVNULL
    # removes the pipe entirely; diagnostics are rc + out_path existence.
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                creationflags=_NO_WINDOW)
        try:
            rc = proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            # Tree-kill (t581): plain kill() fells only the cmd.exe shim and
            # orphans the node->chromium grandchildren still holding the page.
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True, timeout=30, creationflags=_NO_WINDOW)
            else:
                proc.kill()
            proc.wait(timeout=30)
            print("  [archive] single-file timed out (120s) — tree-killed",
                  file=sys.stderr)
            return None
    except Exception as e:
        print(f"  [archive] single-file failed: {type(e).__name__}: "
              f"{str(e)[:80]}", file=sys.stderr)
        return None
    if rc != 0 or not out_path.exists():
        print(f"  [archive] single-file rc={rc} "
              f"(exists={out_path.exists()})", file=sys.stderr)
        return None
    return str(out_path)


# --------------------------------------------------------------------------- #
# 4. Wayback (third-party witness)                                             #
# --------------------------------------------------------------------------- #

def capture_wayback(url: str):
    """Request a Wayback Machine snapshot and return its archived URL, or None.
    ~0-byte third-party witness; failure is honest (None), never fatal."""
    if not re.match(r"https?://", url):  # scheme guard (defense in depth)
        return None
    save_url = "https://web.archive.org/save/" + url
    try:
        req = urllib.request.Request(save_url, headers={"User-Agent": UA})
        r = urllib.request.urlopen(req, timeout=60)  # nosec B310  # nosemgrep: request scheme hardcoded https://web.archive.org
        # Preferred: the Content-Location header carries /web/<ts>/<url>.
        cl = r.headers.get("Content-Location")
        if cl:
            return "https://web.archive.org" + cl
        # Fallback: the final URL after redirects, if it's a /web/ snapshot.
        final = r.geturl()
        if "/web/" in final:
            return final
    except Exception as e:
        print(f"  [wayback] save failed: {type(e).__name__}: {str(e)[:80]}",
              file=sys.stderr)
    return None


# --------------------------------------------------------------------------- #
# Manifest                                                                     #
# --------------------------------------------------------------------------- #

# A whole-file read-modify-write on one shared manifest is unsafe when several
# sessions write the same .proof/ concurrently: interleaved RMW silently loses
# entries (last-writer-wins) AND a reader can observe a half-written file. The
# TTL file-lock serializes the critical section; the tempfile + os.replace makes
# the swap atomic so a reader never sees a partial file. (t053)
_LOCK_TTL_SEC = 10.0   # a lock older than this is presumed orphaned (holder died)
_LOCK_WAIT_SEC = 15.0  # total time to wait for the lock before failing open
_LOCK_POLL_SEC = 0.02


def _acquire_lock(lock_path: Path) -> bool:
    """Best-effort exclusive lock via O_CREAT|O_EXCL. Reclaims a stale lock past
    the TTL (crashed holder). Fails OPEN (returns False) after _LOCK_WAIT_SEC
    rather than deadlocking the proof build — the worst case is the old race for
    a single write, never a hung session."""
    deadline = time.monotonic() + _LOCK_WAIT_SEC
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except (FileExistsError, PermissionError):
            # FileExistsError: lock is held. PermissionError (Windows): the lock
            # file is delete-pending because the holder is releasing it this
            # instant — both are transient contention, so retry. (t053 Windows
            # robustness: Windows raises EACCES, not EEXIST, on a delete-pending
            # open.)
            try:
                if time.time() - lock_path.stat().st_mtime > _LOCK_TTL_SEC:
                    lock_path.unlink()  # reclaim orphaned lock
                    continue
            except OSError:
                continue  # released/delete-pending between open and stat — retry
            if time.monotonic() >= deadline:
                return False
            time.sleep(_LOCK_POLL_SEC)


def _release_lock(lock_path: Path):
    try:
        lock_path.unlink()
    except OSError:
        pass


def upsert_manifest(manifest_path: Path, key: str, record: dict):
    """Append/replace exactly one key in the JSON-object manifest, preserving all
    other entries (merge, never overwrite the whole file). Concurrency-safe:
    serialized by a TTL file-lock and committed atomically via os.replace."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = manifest_path.parent / (manifest_path.name + ".lock")
    locked = _acquire_lock(lock_path)
    try:
        manifest = {}
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if not isinstance(manifest, dict):
                    manifest = {}
            except Exception as e:
                print(f"  [manifest] unreadable, starting fresh: "
                      f"{type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
                manifest = {}
        manifest[key] = record
        tmp_path = manifest_path.parent / f"{manifest_path.name}.tmp.{os.getpid()}"
        tmp_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        os.replace(tmp_path, manifest_path)  # atomic swap; readers never see partial
    finally:
        if locked:
            _release_lock(lock_path)


# --------------------------------------------------------------------------- #
# Orchestration                                                               #
# --------------------------------------------------------------------------- #

def build_proof(url: str, quote: str, proof_dir: Path,
                archive_tier: str, wayback_on: bool,
                use_cache: bool = True,
                cache_ttl: float = URL_CACHE_TTL_DEFAULT,
                re_anchor: bool = True,
                _re_anchored_from: str = None) -> dict:
    """Run all steps best-effort, assemble the manifest record, write it, return
    it. The verdict is decided SOLELY by verbatim presence — in the rendered DOM
    text OR the served HTTP text (the bot-wall bypass: a headless-blocked page whose
    served text still carries the quote is honestly 'present'). The fuzzy DOM
    highlight only controls whether a screenshot is painted, never the verdict.
    Never fabricates. See decide_verdict.

    t564 item 3: the served-text channel reads through the shared URL cache; when
    the (fresh) cached text verbatim-contains the quote, the served channel alone
    already decides 'present', so the Playwright render + SingleFile re-capture
    are skipped and the URL's one archive is reused (screenshot=None, honest —
    same as a bot-walled render). A cache MISS on the quote always runs the full
    live pipeline, so staleness can never fabricate presence.

    t564 item 2 / t582: on verdict=absent (and re_anchor), find_reanchor searches
    the page text for the best verbatim snippet and a proof is recursively built
    for it (new key, `re_anchored_from` back-pointer). The original absent record
    is KEPT as the honest drop record and gains a `re_anchor` field: a dict when
    a candidate was proven, None when the retry ran and found nothing."""
    key = proof_key(url, quote)

    # Scaffold the portable .proof layout so the tool runs standalone.
    shots_dir = proof_dir / "shots"
    archive_dir = proof_dir / "archive"
    shots_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)

    deeplink = deeplink_for(quote, url)
    shot_path = shots_dir / f"{key}.jpg"
    arch_path = archive_dir / f"{key}.html"

    cached = load_url_cache(proof_dir, url, cache_ttl) if use_cache else None
    served_text = cached["text"] if cached else None
    dom_text = ""

    fast_path = bool(
        served_text and match_norm(quote) in match_norm(served_text))
    if fast_path:
        # Cache hit AND the quote is verbatim in the cached served text: the
        # served channel is already the sole 'present' authority, so skip the
        # render — the "don't re-fetch + re-prove the same page" path (t564.3).
        # A pre-existing shot for this exact key is still honored below.
        rendered_ok, rendered_found = False, False
        served_found, served_reachable = True, True
    else:
        # 2. Render + deterministic rendered-DOM verbatim check (saves the shot
        #    only when the quote is verbatim-present on the rendered page).
        rendered_ok, rendered_found, dom_text = capture_screenshot(
            url, quote, shot_path)
        # Served-text verbatim check — the deterministic core and the bot-wall
        # bypass when the visual render is blocked. Fresh cached text stands in
        # for a live fetch; a live fetch refreshes the cache for the next quote.
        if served_text is None:
            try:
                served_text, text_mode = fetch_text(url)
                if use_cache:
                    update_url_cache(proof_dir, url,
                                     text=served_text, mode=text_mode)
            except Exception as e:
                print(f"  [served-text] fetch failed: {type(e).__name__}: "
                      f"{str(e)[:80]}", file=sys.stderr)
                served_text = None
        served_reachable = served_text is not None
        served_found = bool(
            served_text and match_norm(quote) in match_norm(served_text))
    screenshot_field = str(shot_path) if shot_path.exists() else None

    # Verbatim presence (rendered OR served) is the SOLE verdict authority.
    verdict = decide_verdict(served_found, served_reachable,
                             rendered_found, rendered_ok)

    # 3. Archive (best-effort). The URL's one archive is shared across quotes
    #    (t564.3): reuse a cached capture while its file exists, else capture
    #    and record it for the next quote against this page.
    if use_cache and cached and cached.get("archive") \
            and Path(cached["archive"]).exists():
        archive_field = cached["archive"]
    else:
        archive_field = capture_archive(url, arch_path, archive_tier)
        if use_cache and archive_field:
            update_url_cache(proof_dir, url, archive=archive_field)

    # 4. Wayback witness (best-effort, opt-in).
    wayback_field = capture_wayback(url) if wayback_on else None

    record = {
        "url": url,
        "quote": quote,
        "deeplink": deeplink,
        "screenshot": screenshot_field,
        "archive": archive_field,
        "wayback": wayback_field,
        "verdict": verdict,
        "checked_at": now_iso(),
    }
    if cached:
        record["served_from_cache"] = True
        record["text_fetched_at"] = cached.get("fetched_at")
    if _re_anchored_from:
        record["re_anchored_from"] = _re_anchored_from
    upsert_manifest(proof_dir / "proof_manifest.json", key, record)

    # t564 item 2: re-anchor retry — runs once (never recurses), only on absent.
    if verdict == "absent" and re_anchor and not _re_anchored_from:
        source_text = served_text or dom_text
        found = find_reanchor(quote, source_text) if source_text else None
        if found:
            candidate, sim = found
            sub = build_proof(url, candidate, proof_dir, archive_tier,
                              wayback_on, use_cache=use_cache,
                              cache_ttl=cache_ttl, re_anchor=False,
                              _re_anchored_from=key)
            record["re_anchor"] = {
                "proof_id": proof_key(url, candidate),
                "quote": candidate,
                "similarity": round(sim, 3),
                "verdict": sub["verdict"],
            }
        else:
            record["re_anchor"] = None  # retry ran, no verbatim candidate
        upsert_manifest(proof_dir / "proof_manifest.json", key, record)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="cite_proof.py",
        description=("Build a verifiable proof bundle (deeplink + highlighted "
                     "screenshot + link-rot archive + verbatim verdict) for a "
                     "cited web source, and append it to the .proof manifest. "
                     "Uses subprocess HTTP / Playwright / SingleFile only — "
                     "never Claude tools."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="example:\n  python cite_proof.py "
               "https://example.com/page \"the verbatim quote on the page\"",
    )
    parser.add_argument("url", help="the live source page URL (http/https)")
    parser.add_argument("quote",
                        help="verbatim text expected on the page "
                             "(quote as one shell token)")
    parser.add_argument("--proof-dir", default=".proof",
                        help="proof output directory (default: .proof)")
    parser.add_argument("--archive", choices=("none", "noimg", "full"),
                        default="noimg",
                        help="archive tier: none (skip), noimg "
                             "(SingleFile --block-images, ~150 KB, default), "
                             "full (keep images)")
    parser.add_argument("--wayback", choices=("on", "off"), default="off",
                        help="capture a Wayback Machine witness URL "
                             "(default: off)")
    parser.add_argument("--no-cache", action="store_true",
                        help="bypass the shared URL fetch cache "
                             "(.proof/url_cache/) — always fetch live")
    parser.add_argument("--cache-ttl", type=int, default=URL_CACHE_TTL_DEFAULT,
                        help="max age (seconds) a cached page text is trusted "
                             f"(default: {URL_CACHE_TTL_DEFAULT})")
    parser.add_argument("--re-anchor", choices=("on", "off"), default="on",
                        help="on verdict=absent, retry by re-anchoring to the "
                             "best verbatim page snippet before dropping the "
                             "fact (default: on)")
    args = parser.parse_args(argv)

    if not re.match(r"https?://", args.url):
        parser.error(f"url must be http(s): {args.url!r}")

    record = build_proof(
        url=args.url,
        quote=args.quote,
        proof_dir=Path(args.proof_dir),
        archive_tier=args.archive,
        wayback_on=(args.wayback == "on"),
        use_cache=not args.no_cache,
        cache_ttl=args.cache_ttl,
        re_anchor=(args.re_anchor == "on"),
    )

    key = proof_key(args.url, args.quote)
    print(f"key:        {key}")
    print(f"verdict:    {record['verdict']}")
    print(f"deeplink:   {record['deeplink']}")
    print(f"screenshot: {record['screenshot']}")
    print(f"archive:    {record['archive']}")
    print(f"wayback:    {record['wayback']}")
    if record.get("served_from_cache"):
        print(f"cache:      hit (text fetched {record.get('text_fetched_at')})")
    # t564 item 2: an absent fact is NEVER dropped silently — either hand the
    # caller a proven re-anchored quote to adopt, or an explicit UNPROVEN order.
    if "re_anchor" in record:
        ra = record["re_anchor"]
        if ra and ra.get("verdict") == "present":
            print(f"re-anchor:  FOUND verbatim on page "
                  f"(similarity {ra['similarity']})")
            print(f'  re-anchored quote: "{ra["quote"]}"')
            print(f"  re-anchored key:   {ra['proof_id']} (verdict=present)")
            print("  ACTION: update your Quote: block to the re-anchored "
                  "quote above (verbatim).")
        else:
            print("re-anchor:  no verbatim candidate found on the page")
            print("  UNPROVEN: report this fact as unproven in your output "
                  "(never drop it silently),")
            print("  or fetch a better verbatim quote and re-run cite_proof.")
    # Honest exit codes: 0 only when the quote is verifiably present.
    return 0 if record["verdict"] == "present" else 1


if __name__ == "__main__":
    sys.exit(main())
