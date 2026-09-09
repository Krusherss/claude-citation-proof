#!/usr/bin/env python3
# MAP: executable PDF-link-fragment rule: apply_fix + analyze for the learn loop
"""deliverable_link_rule (t225 self-learning-loop-v0) — the executable form of the
brain-wiki fact `pdf-deliverable-fragment-link-scheme`.

ONE rule module, consumed by BOTH stages of the loop so the correction lives in a
single place (DRY):
  - Stage 5 APPLY  -> apply_fix(url): rewrite a bad link the way the fact prescribes.
  - Stage 6 VERIFY -> analyze(url): objective outcome oracle; PASS/FAIL + reason.

THE RULE, REVERSED 2026-07-27 (t796) AND AMENDED 2026-08-07 (t902). A link INTO a PDF
carries BOTH conventions stacked: `#page=N:~:text=ANCHOR`. The text fragment supplies the
HIGHLIGHT and `page=N` supplies the JUMP, and they compose because `:~:` is the
fragment-DIRECTIVE delimiter — the browser strips it and everything after it before the
document sees the fragment, so the PDF viewer receives a clean `#page=N` while the browser
applies the text directive itself.

WHAT 2026-07-27 GOT HALF-RIGHT, corrected here rather than quietly rewritten: it said a
bare `#:~:text=` on a PDF "scrolls to the quote AND highlights it". The HIGHLIGHT half is
confirmed. The SCROLL half is not — four clicks on one 14-page poster in Chrome
(2026-08-07): full-quote fragment gave no jump and no highlight; truncated anchor gave
highlight only; `#page=3` gave jump only; the two stacked gave BOTH. Chromium's own
tracker agrees the scroll was never built: issue 40758205, "[Text Fragment] Scroll to text
in PDF files", closed Won't Fix (Obsolete) April 2022 — "Given that there's already a
commonly-used convention for how PDFs handle URL fragments, I don't think this can be
implemented." The 2026-07-27 Yale-PDF observation (jumped to page 11 AND highlighted) is
NOT deleted: it contradicts both the tracker and the poster, it is unexplained, and the
stacked form makes the contradiction stop mattering.

So `#page=N` ALONE is still the defect on a cited deliverable — it highlights nothing —
but it is no longer merely a fallback: it is half of the correct PDF link.

ALSO MEASURED (t902): truncate a PDF anchor before any SUPERSCRIPT-bearing token. The
full-quote fragment above matched nothing because the poster renders `KRAS` with a
superscript `G12D`, which a PDF text layer splits into separate runs (often reintroducing
a space), so a fragment spanning that word can never match — and Chromium fails it
SILENTLY. A truncated anchor is still a verbatim PREFIX of the quote, so nothing drifts.

WHAT THIS MODULE USED TO SAY, AND WHY IT WAS WRONG — kept, because this module is the
executable form of a brain-wiki fact that turned out to be false, and deleting the
history would let the same inference be made again. It said: "Linking INTO a PDF must
use the PDF fragment scheme (RFC 8118); a #:~:text= text fragment is an HTML-only
feature; on a PDF it is not part of the scheme and degrades SILENTLY." That was
DERIVED from the spec's HTML scope plus MDN's unmatched-fragment behaviour. It was
never clicked. Falsified 2026-07-27: a Yale COI-policy PDF opened in Chrome via a
`#:~:text=` link jumped to page 11 and highlighted the sentence (screenshot), then
three cited filings highlighted the same way. Two web searches found NO documentation
stating PDF support in either direction — that SILENCE is what the old rule mistook
for a "no". A specification states what was designed, not what ships.

LIMIT, so this module does not overclaim in the other direction: the highlight is
proven for Chromium's PDF viewer. It is NOT proven for Adobe Reader, Firefox pdf.js,
or mobile viewers. `#page=N` stays a legitimate unknown-viewer FALLBACK — it simply
cannot highlight anywhere, so it is never the preferred form for a cited quote.

DERIVABILITY, unchanged in spirit but now pointing the other way: a `#search=<phrase>`
link carries its own phrase, so search -> text is a faithful mechanical rewrite. A
bare `#page=N` carries NO phrase, so the correct link is NOT derivable from the bad
one; apply_fix leaves it alone and analyze says so. A rewrite that invented a phrase
would be a guess wearing a fix's clothing, and would assert a quote nothing proved.

Pure-stdlib, no side effects, safe to import anywhere.
"""
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit, quote, unquote

# Both patterns use a bounded negated class with no nesting and no backtracking
# alternation, so matching stays linear in the fragment length (external input).
TEXT_FRAGMENT_RE = re.compile(r":~:text=(?P<phrase>[^&#]*)")
SEARCH_RE = re.compile(r"(?:^|[&#])search=(?P<phrase>[^&#]*)")


def _path_is_pdf(url):
    """True when the URL's PATH (ignoring query/fragment) ends in .pdf."""
    parts = urlsplit(url)
    return parts.path.lower().endswith(".pdf")


def _has_text_fragment(url):
    return ":~:text=" in urlsplit(url).fragment


def _search_phrase(url):
    """The phrase inside an RFC 8118 `#search=` term, decoded, or None."""
    m = SEARCH_RE.search(urlsplit(url).fragment)
    if not m:
        return None
    return unquote(m.group("phrase").replace("+", " ")).strip() or None


def analyze(url):
    """Stage 6 VERIFY oracle. Returns a dict:
      {url, is_pdf, has_text_fragment, verdict: 'PASS'|'FAIL', reason, suggested_fix}

    FAIL iff the link points into a PDF and anchors a quote with a page/search
    fragment INSTEAD of `#:~:text=` — that form cannot highlight in any viewer.
    A PDF link carrying a text fragment is the CORRECT form and PASSes. A PDF link
    with no fragment at all is unanchored, which is weak but is not a wrong anchor.
    """
    is_pdf = _path_is_pdf(url)
    has_tf = _has_text_fragment(url)
    if not is_pdf or has_tf:
        return {
            "url": url,
            "is_pdf": is_pdf,
            "has_text_fragment": has_tf,
            "verdict": "PASS",
            "reason": ("text fragment on a .pdf — the correct form; Chromium's PDF "
                       "viewer scrolls to the quote and highlights it"
                       if is_pdf and has_tf else "ok"),
            "suggested_fix": None,
        }

    if not urlsplit(url).fragment:
        return {
            "url": url,
            "is_pdf": True,
            "has_text_fragment": False,
            "verdict": "PASS",
            "reason": "pdf link with no fragment — unanchored, but not the wrong anchor",
            "suggested_fix": None,
        }

    phrase = _search_phrase(url)
    fixed = apply_fix(url)
    return {
        "url": url,
        "is_pdf": True,
        "has_text_fragment": False,
        "verdict": "FAIL",
        "reason": ("page/search fragment on a cited .pdf — lands on the page and "
                   "highlights NOTHING; use the same #:~:text= fragment you would "
                   "use for HTML"
                   + ("" if phrase else
                      " (this link carries no phrase, so the correct fragment is "
                      "not derivable from it — re-anchor on the proven quote)")),
        "suggested_fix": fixed if fixed != url else None,
    }


def apply_fix(url):
    """Stage 5 APPLY. Rewrite an RFC 8118 `#search=` PDF link into a `#:~:text=`
    fragment carrying the same phrase, percent-encoded so no `&`/`#` in the phrase
    can add a directive or truncate the URL. Idempotent, and DELIBERATELY INERT when
    there is no phrase to reuse: inventing one would fabricate an anchor."""
    if not _path_is_pdf(url) or _has_text_fragment(url):
        return url
    phrase = _search_phrase(url)
    if not phrase:
        return url
    parts = urlsplit(url)
    new_fragment = ":~:text=" + quote(phrase, safe="")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, new_fragment))


# ---- fragment-vs-body checks (t778, 2026-07-27) -----------------------------------
# The three-hit lesson this encodes (hyphens t582, curly apostrophe t786, overlap
# t799): A CHECKER LOOSER THAN THE BROWSER PASSES LINKS THE BROWSER REJECTS. So every
# rule here is TIGHTER-OR-EQUAL than browser text-fragment matching, never looser:
#   EQUAL (t870, was TIGHTER and WRONG): character folding is now Chromium's own --
#     NFKD, whitespace collapse, combining marks stripped, lowercased (_chrome_normalize,
#     re-implemented from text-fragment-utils.js:979). The old case-sensitive zero-folding
#     rule was tighter than the browser in a way that had stopped being conservative: it
#     rejected fragments CHROME'S OWN GENERATOR emits, so adopting the generator was
#     impossible while it stood. Tighter is safe only while it rejects nothing real.
#   STILL TIGHTER (deliberate): the dash family and the quote family do NOT fold, because
#     the browser does not fold them either -- t786's defect was a checker that folded
#     what the browser does not, and NFKD leaves U+2013/U+2019 intact.
#   EQUAL: whitespace runs collapse to one space (rendered-text semantics); word
#     boundaries required at word-character edges; RANGE -- textEnd is searched only
#     AFTER textStart's match ENDS (t799: an overlapping start/end split on a short
#     quote can NEVER highlight); prefix-/-suffix must sit adjacent (whitespace only).
# Malformed directives FAIL (closed), and every FAIL names the part that failed, so
# the verdict carries its evidence.

_WS_RE = re.compile(r"\s+")
_COMBINING_RE = re.compile(r"[̀-ͯ]")


def _chrome_normalize(s):
    """Chromium's own text-fragment normalization, RE-IMPLEMENTED (t870).

    Mirrors `normalizeString` in text-fragments-polyfill
    `src/text-fragment-utils.js:979`, step for step and in its order:

        NFKD  ->  /\\s+/ -> ' '  ->  strip U+0300..U+036F  ->  toLowerCase

    Chromium applies this to BOTH the page and the fragment before matching, so
    a fragment its own generator emits is lowercased and decomposed. This module
    used to match exact, case-sensitive raw page text and therefore REJECTED
    fragments the browser would happily highlight — measured both directions in
    the B3 spike, on case alone.

    RE-IMPLEMENTED, NOT IMPORTED, deliberately (plan §2 rule 2): this module is
    the independent check ON that generator. A gate that calls the generator's
    own normalizer agrees with it by construction and can never catch its bugs.
    The cost of independence is that the two can drift, which is what
    tests/test_deliverable_link_rule_t870_chrome_norm.py pins.

    WHAT THIS DOES NOT RELAX: the dash family. NFKD leaves U+2013 alone and it is
    not a combining mark, so an ASCII '4-6' fragment still FAILS against a page
    serving '4–6'. That is t786's dead link, and it must keep failing — a folded
    dash is exactly the character a browser will not match."""
    s = unicodedata.normalize("NFKD", s or "")
    s = _WS_RE.sub(" ", s)
    return _COMBINING_RE.sub("", s).lower()


def _collapse(s):
    """Whitespace-collapse plus Chrome's folding — the single normalization both
    sides of every comparison in this module pass through."""
    return _chrome_normalize(s)


def _is_word_char(c):
    return c.isalnum() or c == "_"


def _find_word_bounded(hay, needle, start_at=0):
    """First index >= start_at where needle occurs in hay with word boundaries:
    a needle edge that is a word character must not touch another word character
    in hay (mirrors the browser; '10%' must not match inside '110%'). -1 if none."""
    i = hay.find(needle, start_at)
    while i != -1:
        left_ok = i == 0 or not (_is_word_char(needle[0]) and _is_word_char(hay[i - 1]))
        j = i + len(needle)
        right_ok = j == len(hay) or not (_is_word_char(needle[-1]) and _is_word_char(hay[j]))
        if left_ok and right_ok:
            return i
        i = hay.find(needle, i + 1)
    return -1


def parse_text_directives(url):
    """Every `text=` directive in the URL's fragment directive (after `:~:`),
    parsed per the WICG syntax  text=[prefix-,]textStart[,textEnd][,-suffix],
    percent-decoded AFTER splitting so an encoded %2C comma stays literal.
    Returns [] when the URL carries no text directive.
    Raises ValueError on a malformed directive -- callers report FAIL, never PASS."""
    frag = urlsplit(url).fragment
    if ":~:" not in frag:
        return []
    out = []
    for d in frag.split(":~:", 1)[1].split("&"):
        if not d.startswith("text="):
            continue
        parts = d[len("text="):].split(",")
        prefix = suffix = None
        raw = []
        if parts and parts[0].endswith("-"):
            if parts[0] == "-":
                raise ValueError("malformed text directive (bare '-' prefix): " + d)
            raw.append(parts[0][:-1])
            prefix = unquote(parts.pop(0)[:-1])
        if parts and parts[-1].startswith("-"):
            if parts[-1] == "-":
                raise ValueError("malformed text directive (bare '-' suffix): " + d)
            raw.append(parts[-1][1:])
            suffix = unquote(parts.pop()[1:])
        raw += parts
        # t811: '-' is RESERVED grammar inside a component (it marks prefix- and
        # -suffix), so a LITERAL one must never appear in a component's text -- only
        # %2D does. Chromium fails such a match SILENTLY: the page opens at the top
        # with no highlight while presence, RANGE and byte audits all stay green.
        # Checked on the still-encoded token, because %2D decodes to '-'. FAILING
        # here is the tighter-than-the-browser direction the module commits to: the
        # remedy is to encode the hyphen, never to re-anchor around it.
        for tok in raw:
            if "-" in tok:
                raise ValueError(
                    "literal '-' in a text-fragment component (t811): '" + tok
                    + "' -- the grammar reserves '-'; percent-encode it as %2D or "
                    "Chromium silently fails the match: " + d)
        core = [unquote(p) for p in parts]
        if len(core) not in (1, 2) or not all(core):
            raise ValueError("malformed text directive (need 1-2 non-empty core parts, "
                             f"got {len(core)}): " + d)
        out.append({"prefix": prefix, "start": core[0],
                    "end": core[1] if len(core) == 2 else None,
                    "suffix": suffix})
    return out


def check_fragment_in_text(url, page_text):
    """Verify a URL's #:~:text= fragment against the page's rendered text the way
    a browser resolves it, or stricter (see the block comment above). page_text is
    supplied by the caller -- this module stays pure and does no I/O.
    Returns {url, verdict: 'PASS'|'FAIL', reason}."""
    def fail(reason):
        return {"url": url, "verdict": "FAIL", "reason": reason}
    try:
        directives = parse_text_directives(url)
    except ValueError as e:
        return fail(str(e))
    if not directives:
        return {"url": url, "verdict": "PASS",
                "reason": "no text directive -- nothing to match"}
    hay = _collapse(page_text)
    for d in directives:
        start = _collapse(d["start"])
        s_idx = _find_word_bounded(hay, start)
        if s_idx == -1:
            return fail("textStart not found in page text (Chrome-normalized: "
                        "NFKD, whitespace-collapsed, diacritics stripped, "
                        f"lowercased; word-bounded): '{d['start']}'")
        if d["prefix"] is not None:
            pfx = _collapse(d["prefix"])
            anchored = -1
            i = s_idx
            while i != -1:
                before = hay[:i].rstrip(" ")
                if before.endswith(pfx):
                    p_i = len(before) - len(pfx)
                    if p_i == 0 or not (_is_word_char(pfx[0]) and _is_word_char(before[p_i - 1])):
                        anchored = i
                        break
                i = _find_word_bounded(hay, start, i + 1)
            if anchored == -1:
                return fail(f"prefix '{d['prefix']}' does not immediately precede "
                            f"textStart '{d['start']}'")
            s_idx = anchored
        match_end = s_idx + len(start)
        if d["end"] is not None:
            end = _collapse(d["end"])
            e_idx = _find_word_bounded(hay, end, match_end)
            if e_idx == -1:
                anywhere = _find_word_bounded(hay, end) != -1
                return fail("RANGE (t799): textEnd never occurs AFTER textStart's match "
                            "ends" + (" -- the halves overlap or END precedes START, so "
                                      "no browser range can match" if anywhere else
                                      f" and is absent from the page: '{d['end']}'")
                            + f"; textEnd: '{d['end']}'")
            match_end = e_idx + len(end)
        if d["suffix"] is not None:
            sfx = _collapse(d["suffix"])
            rest = hay[match_end:].lstrip(" ")
            j = len(sfx)
            if not (rest.startswith(sfx)
                    and (j == len(rest) or not (_is_word_char(sfx[-1]) and _is_word_char(rest[j])))):
                return fail(f"suffix '{d['suffix']}' does not immediately follow the match")
        # t813: RUNAWAY RANGE. A two-part fragment resolves against the FIRST textStart on
        # the page, not the one the author was reading. When that word sequence recurs, the
        # browser anchors on the earlier hit and highlights everything from there to
        # textEnd -- a block of unrelated text instead of the quote. It DOES highlight, so
        # it looks alive, and presence and RANGE both pass because both are satisfied.
        # Detected by proportion, since this module never sees the intended quote: a
        # healthy range is about as long as the anchor text naming it. Thresholds are
        # MEASURED, not guessed -- across the 45 links of the deliverable that exposed
        # this, the defective link resolved at 47.9x its component length (3,738 chars for
        # a 224-char quote) and the worst HEALTHY link at 4.5x (284 chars). 10x with a
        # 400-char floor sits in that gap with margin on both sides; the floor stops a
        # short quote with tiny components from tripping on ratio alone.
        span = match_end - s_idx
        parts_len = len(start) + (len(_collapse(d["end"])) if d["end"] is not None else 0)
        if span > 400 and span > 10 * parts_len:
            return fail(
                f"RUNAWAY RANGE (t813): the fragment resolves to {span} chars from "
                f"{parts_len} chars of anchor text ({span // parts_len}x). textStart "
                f"'{d['start']}' almost certainly occurs earlier on the page than the "
                "quote does, so the browser anchors on the wrong one and highlights a "
                "block. Lengthen textStart until it is unique on the page.")
    return {"url": url, "verdict": "PASS",
            "reason": f"{len(directives)} text directive(s) match with range semantics"}


# ---- self-tests (run: python deliverable_link_rule.py --test) --------------------
_TESTS = [
    # (url, expected_verdict)
    # THE REVERSAL: a text fragment on a .pdf is the CORRECT form, not the defect
    ("https://trials.host/study.pdf#:~:text=methods%20section", "PASS"),
    ("https://trials.host/study.pdf#:~:text=the%20methods,were%20blinded", "PASS"),
    # page/search anchors cannot highlight in any viewer -> now the defect
    ("https://trials.host/study.pdf#page=7", "FAIL"),
    ("https://trials.host/study.pdf#search=methods", "FAIL"),
    ("https://trials.host/study.pdf#page=7&search=methods%20section", "FAIL"),
    # an unanchored pdf link is weak, but it is not the WRONG anchor
    ("https://trials.host/study.pdf", "PASS"),
    # fragments on an HTML page are not this rule's business
    ("https://example.com/article#:~:text=intro", "PASS"),
    ("https://example.com/article#page=7", "PASS"),
    # pdf with query string before fragment
    ("https://h/x/report.pdf?v=2#search=abstract", "FAIL"),
    # .PDF uppercase
    ("https://h/DECK.PDF#page=3", "FAIL"),
]


# Body for the fragment-vs-body tests. Carries the three defect classes this module
# exists to catch: a short quote whose split halves overlap (t799), a curly apostrophe
# a straight-quote fragment must NOT match (t786), and a substring inside a longer
# number that a word-bounded match must reject.
_BODY = ("The survey found breakeven AUM increased to US$82.9m from US$70.1m "
         "in 2024. Average headcount has risen to 10 employees per firm. "
         "In a typical seed investment, the manager’s future revenues are shared. "
         "the rate was 110% overall. The estimated out-of-pocket cost per compound. "
         # t813 fixture: filler long enough that a range from the FIRST 'breakeven AUM' to
         # the tail clears the 400-char floor, followed by a SECOND mention of that phrase.
         + "Filler sentence carrying no anchor phrase at all. " * 12
         + "A second breakeven AUM mention sits far below, at $1,395 million "
           "per approved compound.")

_BODY_TESTS = [
    # (name, url, expected_verdict, required substring of reason or '')
    ("presence-pass", "https://h/p#:~:text=breakeven%20AUM%20increased", "PASS", ""),
    ("presence-fail", "https://h/p#:~:text=quote%20not%20on%20page", "FAIL", "textStart not found"),
    # t799 REGRESSION: overlapping halves of a short quote can never highlight
    ("range-overlap-fail",
     "https://h/p#:~:text=breakeven%20AUM%20increased,increased%20to%20US%2482.9m",
     "FAIL", "RANGE"),
    ("range-ok-pass", "https://h/p#:~:text=breakeven%20AUM,US%2470.1m", "PASS", ""),
    # t786 REGRESSION: the browser does not fold the quote family; neither may we
    ("strict-apostrophe-fail", "https://h/p#:~:text=the%20manager%27s%20future", "FAIL", ""),
    ("strict-apostrophe-pass", "https://h/p#:~:text=the%20manager%E2%80%99s%20future", "PASS", ""),
    ("word-boundary-fail", "https://h/p#:~:text=10%25%20overall", "FAIL", ""),
    # t870 CONTRACT CHANGE, recorded rather than quietly deleted: this case used
    # to expect FAIL, pinning a case-SENSITIVE match. Chromium matches case-
    # insensitively (it normalizes both sides), so the old expectation asserted
    # something false about the browser and made Chrome's own generator output
    # unusable. Now PASS. The characters the browser really does distinguish --
    # the dash and quote families -- keep their FAIL cases directly above.
    ("case-insensitive-pass", "https://h/p#:~:text=BREAKEVEN%20AUM", "PASS", ""),
    ("malformed-fail", "https://h/p#:~:text=a,b,c,d", "FAIL", "malformed"),
    ("empty-fail", "https://h/p#:~:text=", "FAIL", "malformed"),
    ("no-directive-pass", "https://h/p#section-2", "PASS", "no text directive"),
    ("prefix-adjacent-pass", "https://h/p#:~:text=survey%20found-,breakeven%20AUM", "PASS", ""),
    ("prefix-not-adjacent-fail", "https://h/p#:~:text=headcount%20has-,breakeven%20AUM",
     "FAIL", "prefix"),
    # t811 REGRESSION: a LITERAL '-' inside a component is reserved grammar, not text.
    # Chromium fails the match silently; %2D is the only spelling that highlights.
    # The text below IS on the page, so only the encoding decides the verdict.
    ("literal-hyphen-start-fail", "https://h/p#:~:text=estimated%20out-of-pocket%20cost",
     "FAIL", "literal '-'"),
    ("literal-hyphen-end-fail",
     "https://h/p#:~:text=The%20estimated%20out,of-pocket%20cost%20per%20compound", "FAIL",
     "literal '-'"),
    ("literal-hyphen-in-prefix-fail",
     "https://h/p#:~:text=estimated%20out-of-pocket-,cost", "FAIL", "literal '-'"),
    ("encoded-hyphen-pass", "https://h/p#:~:text=estimated%20out%2Dof%2Dpocket%20cost",
     "PASS", ""),
    # t813 REGRESSION: textStart recurs, so the browser anchors on the FIRST hit and the
    # range runs across the whole body. 'breakeven AUM' appears at the top of _BODY and
    # again in the runaway tail, and the fragment still satisfies presence AND range.
    ("runaway-range-fail", "https://h/p#:~:text=breakeven%20AUM,per%20approved%20compound",
     "FAIL", "RUNAWAY RANGE"),
    # Same body, textStart lengthened until unique -- the prescribed fix must PASS.
    ("runaway-range-fixed-pass",
     "https://h/p#:~:text=second%20breakeven%20AUM%20mention,per%20approved%20compound",
     "PASS", ""),
]


def _run_tests():
    failures = []
    for url, exp_verdict in _TESTS:
        got = analyze(url)
        if got["verdict"] != exp_verdict:
            failures.append(f"verdict {url}: got {got['verdict']} want {exp_verdict}")

    # fragment-vs-body: parsing, presence, RANGE (t799), strictness (t786), boundaries
    for name, url, exp, reason_sub in _BODY_TESTS:
        got = check_fragment_in_text(url, _BODY)
        if got["verdict"] != exp:
            failures.append(f"body {name}: got {got['verdict']} want {exp} ({got['reason']})")
        elif reason_sub and reason_sub not in got["reason"]:
            failures.append(f"body {name}: reason lacks '{reason_sub}': {got['reason']}")
    ws = check_fragment_in_text("https://h/p#:~:text=Average%20headcount",
                                _BODY.replace("Average headcount", "Average\n  headcount"))
    if ws["verdict"] != "PASS":
        failures.append(f"body whitespace-collapse: {ws}")
    parsed = parse_text_directives("https://h/p#:~:text=pre-,a%2Cb,c,-suf&text=solo")
    if parsed != [{"prefix": "pre", "start": "a,b", "end": "c", "suffix": "suf"},
                  {"prefix": None, "start": "solo", "end": None, "suffix": None}]:
        failures.append(f"parse full-form: {parsed}")

    # a search= link CARRIES its phrase, so the rewrite is derivable and must close the loop
    derivable = "https://trials.host/study.pdf#search=methods%20section"
    fixed = apply_fix(derivable)
    if ":~:text=" not in fixed:
        failures.append(f"fix {derivable}: produced no text fragment: {fixed}")
    if "methods section" not in unquote(urlsplit(fixed).fragment):
        failures.append(f"fix {derivable}: phrase lost: {fixed}")
    if analyze(fixed)["verdict"] != "PASS":
        failures.append(f"fix {derivable}: fixed link still FAILs: {fixed}")

    # A08 DATA INTEGRITY: page-only carries NO phrase. The correct link is not derivable
    # from the bad one, and an invented phrase would assert a quote nothing proved.
    page_only = "https://trials.host/study.pdf#page=7"
    if apply_fix(page_only) != page_only:
        failures.append(f"A08: invented a phrase for {page_only}: {apply_fix(page_only)}")
    if analyze(page_only)["suggested_fix"] is not None:
        failures.append(f"A08: {page_only} offered a fix it cannot derive")
    if "not derivable" not in analyze(page_only)["reason"]:
        failures.append(f"A08: {page_only} reason does not say the fix is underivable")

    # A03 INJECTION: the phrase is external input and is rebuilt into a URL. A phrase
    # carrying & or # must be percent-encoded, never allowed to add a term or truncate.
    inj = "https://trials.host/study.pdf#search=a%26b%3Dc%23x"
    inj_fixed = apply_fix(inj)
    inj_frag = urlsplit(inj_fixed).fragment
    if not inj_frag.startswith(":~:text="):
        failures.append(f"A03: fragment not a lone text directive: {inj_fixed}")
    if "&" in inj_frag or "#" in inj_frag:
        failures.append(f"A03: raw delimiter survived into the fragment: {inj_fixed}")
    if unquote(inj_frag[len(":~:text="):]) != "a&b=c#x":
        failures.append(f"A03: phrase not round-tripped intact: {inj_fixed}")

    # idempotency: 'fixing' an already-correct link is a no-op
    good = "https://trials.host/study.pdf#:~:text=methods"
    if apply_fix(good) != good:
        failures.append(f"idempotency: {good} -> {apply_fix(good)}")

    # REGRESSION GUARD for the reversal itself: the link that falsified the old rule
    # must never be reported FAIL again.
    yale = ("https://research-support.yale.edu/coi_policy_0.pdf"
            "#:~:text=Financial%20interests%20or%20activities")
    if analyze(yale)["verdict"] != "PASS":
        failures.append("REGRESSION: the falsifying Yale link is being reported FAIL")
    return failures


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        fails = _run_tests()
        if fails:
            print(f"FAIL {len(fails)}/{len(_TESTS)} cases:")
            for f in fails:
                print("  -", f)
            sys.exit(1)
        print(f"OK {len(_TESTS)} verify+apply + {len(_BODY_TESTS) + 2} "
              "fragment-vs-body cases pass")
        sys.exit(0)
    if "--check" in sys.argv:
        target = sys.argv[sys.argv.index("--check") + 1]
        import json
        print(json.dumps(analyze(target), indent=2))
        sys.exit(0)
    if "--check-body" in sys.argv:
        # one URL against one body file: JSON verdict, exit 0 PASS / 1 FAIL
        import json
        i = sys.argv.index("--check-body")
        target, body_path = sys.argv[i + 1], sys.argv[i + 2]
        with open(body_path, encoding="utf-8") as fh:
            verdict = check_fragment_in_text(target, fh.read())
        print(json.dumps(verdict, indent=2))
        sys.exit(0 if verdict["verdict"] == "PASS" else 1)
    if "--check-cache" in sys.argv:
        # Build-gate mode (t778): every URL in <urls-file> (one per line) is checked
        # against its cached rendered body in <cache-dir> (cite_proof url_cache
        # entries: {url, canon_url, text}). BOTH rules run: the pdf-anchor rule
        # (analyze) and the fragment-vs-body rule. A URL whose page has no cached
        # body FAILS LOUDLY -- an unverifiable link must never pass silently.
        import json, glob, os
        i = sys.argv.index("--check-cache")
        urls_path, cache_dir = sys.argv[i + 1], sys.argv[i + 2]
        bodies = {}
        for cf in glob.glob(os.path.join(cache_dir, "*.json")):
            with open(cf, encoding="utf-8") as fh:
                entry = json.load(fh)
            bodies[entry.get("url")] = entry.get("text", "")
            bodies[entry.get("canon_url", entry.get("url"))] = entry.get("text", "")
        with open(urls_path, encoding="utf-8") as fh:
            urls = [ln.strip() for ln in fh if ln.strip()]
        n_fail = 0
        for u in urls:
            anchor = analyze(u)
            if anchor["verdict"] == "FAIL":
                n_fail += 1
                print(f"FAIL {u}\n     anchor rule: {anchor['reason']}")
                continue
            base = u.split("#")[0]
            if base not in bodies:
                n_fail += 1
                print(f"FAIL {u}\n     no cached body for {base} -- run cite_proof "
                      "on a quote from this page to cache it; an uncheckable link "
                      "does not pass")
                continue
            v = check_fragment_in_text(u, bodies[base])
            if v["verdict"] == "FAIL":
                n_fail += 1
                print(f"FAIL {u}\n     {v['reason']}")
        print(f"{len(urls)} link(s) checked, {n_fail} failure(s)")
        sys.exit(1 if n_fail else 0)
    print("usage: deliverable_link_rule.py --test | --check <url> | "
          "--check-body <url> <bodyfile> | --check-cache <urlsfile> <cachedir>")
