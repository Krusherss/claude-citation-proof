#!/usr/bin/env python3
"""Tests for cite_proof.py t564 items 2+3 (+ t581/t582 folded in).

Covers, with ZERO network (fetch/render/archive all monkeypatched):
  - URL cache (t564.3): roundtrip, TTL expiry, corrupt-file fail-open,
    archive merge preserving the text clock, text-size cap.
  - match_norm (t582): PDF line-break de-hyphenation + NFKC ligature fold;
    keying functions (canon_quote/proof_key) provably NOT affected.
  - find_reanchor (t564.2): finds the page sentence for a near-miss quote,
    rejects unrelated quotes, enforces the span floors.
  - build_proof fast path: cache-hit + verbatim -> present with NO render and
    NO live fetch, reusing the URL's cached archive.
  - build_proof re-anchor flow: original absent record kept (honest drop
    record) with `re_anchor` info; re-anchored record written under a new key
    with `re_anchored_from`; `re_anchor: None` when nothing anchors.
"""
import json
import time
from pathlib import Path

import sys
SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import cite_proof as cp  # noqa: E402

URL = "https://example.org/page"

PAGE = ("Example Domain. This domain is for use in illustrative examples "
        "in documents. You may use this domain in literature without prior "
        "coordination or asking for permission. More information here.")


def _seed_cache(proof_dir, text=PAGE, archive=None):
    cp.update_url_cache(proof_dir, URL, text=text, mode="html",
                        archive=archive)


def _no_network(monkeypatch, render=(False, False, "")):
    """Make any live fetch/render/archive attempt loud and observable."""
    calls = {"fetch": 0, "render": 0, "archive": 0}

    def fake_fetch(url):
        calls["fetch"] += 1
        raise AssertionError("live fetch_text called")

    def fake_render(url, quote, out_path):
        calls["render"] += 1
        return render

    def fake_archive(url, out_path, tier):
        calls["archive"] += 1
        return None

    monkeypatch.setattr(cp, "fetch_text", fake_fetch)
    monkeypatch.setattr(cp, "capture_screenshot", fake_render)
    monkeypatch.setattr(cp, "capture_archive", fake_archive)
    return calls


# --------------------------------------------------------------------------- #
# URL cache (t564 item 3)                                                      #
# --------------------------------------------------------------------------- #

def test_cache_roundtrip(tmp_path):
    _seed_cache(tmp_path)
    entry = cp.load_url_cache(tmp_path, URL, ttl=60)
    assert entry["text"] == PAGE
    assert entry["mode"] == "html"
    assert entry["canon_url"] == cp.canon_url(URL)


def test_cache_ttl_expiry(tmp_path):
    _seed_cache(tmp_path)
    p = cp.url_cache_path(tmp_path, URL)
    entry = json.loads(p.read_text(encoding="utf-8"))
    entry["fetched_at_epoch"] = time.time() - 100
    p.write_text(json.dumps(entry), encoding="utf-8")
    assert cp.load_url_cache(tmp_path, URL, ttl=50) is None
    assert cp.load_url_cache(tmp_path, URL, ttl=500) is not None


def test_cache_corrupt_is_miss(tmp_path):
    p = cp.url_cache_path(tmp_path, URL)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json", encoding="utf-8")
    assert cp.load_url_cache(tmp_path, URL, ttl=60) is None


def test_cache_archive_merge_keeps_text_clock(tmp_path):
    _seed_cache(tmp_path)
    before = json.loads(cp.url_cache_path(tmp_path, URL)
                        .read_text(encoding="utf-8"))
    cp.update_url_cache(tmp_path, URL, archive="X:/some/archive.html")
    after = json.loads(cp.url_cache_path(tmp_path, URL)
                       .read_text(encoding="utf-8"))
    assert after["archive"] == "X:/some/archive.html"
    assert after["text"] == PAGE
    assert after["fetched_at_epoch"] == before["fetched_at_epoch"]


def test_cache_archive_alone_cannot_seed(tmp_path):
    cp.update_url_cache(tmp_path, URL, archive="X:/a.html")
    assert not cp.url_cache_path(tmp_path, URL).exists()


def test_cache_text_cap(tmp_path):
    cp.update_url_cache(tmp_path, URL,
                        text="x" * (cp._URL_CACHE_TEXT_CAP + 1), mode="html")
    assert not cp.url_cache_path(tmp_path, URL).exists()


# --------------------------------------------------------------------------- #
# match_norm (t582) — presence-check only, keys untouched                      #
# --------------------------------------------------------------------------- #

def test_match_norm_dehyphenates_pdf_linebreaks():
    assert cp.match_norm("man- agement") == "management"
    assert cp.match_norm("regu- latory oversight") == "regulatory oversight"
    # real hyphenated compounds (no space) untouched
    assert cp.match_norm("cost-effective") == "cost-effective"
    # space before the hyphen is not a line-break artifact: the words are
    # never merged ('1020'). The t582 space fold does tighten it to '10-20',
    # applied identically to needle and haystack (see test_cite_proof_t582).
    assert cp.match_norm("10 - 20") == "10-20"
    assert "1020" not in cp.match_norm("10 - 20")


def test_match_norm_folds_ligatures():
    assert cp.match_norm("signiﬁcantly") == "significantly"  # fi ligature


def test_keying_not_affected_by_match_norm():
    # canon_quote keeps case and does NOT de-hyphenate — key stability with
    # proof_gate.py depends on this.
    assert cp.canon_quote("Man- agement") == "Man- agement"
    assert cp.canon_quote("Aspirin") == "Aspirin"
    k1 = cp.proof_key(URL, "man- agement")
    k2 = cp.proof_key(URL, "management")
    assert k1 != k2


# --------------------------------------------------------------------------- #
# find_reanchor (t564 item 2)                                                  #
# --------------------------------------------------------------------------- #

def test_reanchor_finds_page_sentence():
    quote = "This domain is for use in illustrative examples in every report"
    got = cp.find_reanchor(quote, PAGE)
    assert got is not None
    candidate, sim = got
    assert "illustrative examples" in candidate
    assert cp.match_norm(candidate) in cp.match_norm(PAGE)
    assert sim >= cp._REANCHOR_MIN_RATIO


def test_reanchor_rejects_unrelated_quote():
    assert cp.find_reanchor("completely different topic sentence", PAGE) is None


def test_reanchor_enforces_floors():
    # 2-word overlap only -> below the 3-word floor
    assert cp.find_reanchor("domain is xyzzy quux flarp", PAGE) is None


def test_reanchor_candidate_is_verbatim_under_match_norm():
    text = "Intro. The man- agement team met regulators today. Outro."
    quote = "The management team met regulators yesterday"
    got = cp.find_reanchor(quote, text)
    assert got is not None
    candidate, _ = got
    assert cp.match_norm(candidate) in cp.match_norm(text)


# --------------------------------------------------------------------------- #
# build_proof — fast path (t564 item 3)                                        #
# --------------------------------------------------------------------------- #

def test_fast_path_skips_render_and_fetch(tmp_path, monkeypatch):
    arch = tmp_path / "archive" / "seed.html"
    arch.parent.mkdir(parents=True, exist_ok=True)
    arch.write_text("<html>seed</html>", encoding="utf-8")
    _seed_cache(tmp_path, archive=str(arch))
    calls = _no_network(monkeypatch)

    rec = cp.build_proof(URL, "illustrative examples in documents", tmp_path,
                         archive_tier="noimg", wayback_on=False)
    assert rec["verdict"] == "present"
    assert rec["served_from_cache"] is True
    assert rec["archive"] == str(arch)          # reused, not re-captured
    assert rec["screenshot"] is None
    assert calls == {"fetch": 0, "render": 0, "archive": 0}
    manifest = json.loads((tmp_path / "proof_manifest.json")
                          .read_text(encoding="utf-8"))
    key = cp.proof_key(URL, "illustrative examples in documents")
    assert manifest[key]["verdict"] == "present"


def test_cache_miss_on_quote_runs_full_pipeline(tmp_path, monkeypatch):
    _seed_cache(tmp_path)
    calls = _no_network(monkeypatch)
    rec = cp.build_proof(URL, "zebra quagga wombat xylophone", tmp_path,
                         archive_tier="none", wayback_on=False,
                         re_anchor=False)
    # cached text stands in for the served channel (no live fetch), but the
    # render DID run because the quote was not in the cached text
    assert calls["fetch"] == 0
    assert calls["render"] == 1
    assert rec["verdict"] == "absent"


def test_no_cache_flag_fetches_live(tmp_path, monkeypatch):
    _seed_cache(tmp_path)
    calls = {"fetch": 0}

    def fake_fetch(url):
        calls["fetch"] += 1
        return PAGE, "html"

    monkeypatch.setattr(cp, "fetch_text", fake_fetch)
    monkeypatch.setattr(cp, "capture_screenshot",
                        lambda u, q, p: (False, False, ""))
    monkeypatch.setattr(cp, "capture_archive", lambda u, p, t: None)
    rec = cp.build_proof(URL, "illustrative examples in documents", tmp_path,
                         archive_tier="none", wayback_on=False,
                         use_cache=False)
    assert calls["fetch"] == 1
    assert rec["verdict"] == "present"
    assert "served_from_cache" not in rec


# --------------------------------------------------------------------------- #
# build_proof — re-anchor flow (t564 item 2)                                   #
# --------------------------------------------------------------------------- #

def test_reanchor_flow_writes_both_records(tmp_path, monkeypatch):
    _seed_cache(tmp_path)
    _no_network(monkeypatch)
    quote = "You may use this domain in literature without any permission slip"
    rec = cp.build_proof(URL, quote, tmp_path,
                         archive_tier="none", wayback_on=False)
    assert rec["verdict"] == "absent"            # honest drop record kept
    ra = rec["re_anchor"]
    assert ra is not None and ra["verdict"] == "present"
    manifest = json.loads((tmp_path / "proof_manifest.json")
                          .read_text(encoding="utf-8"))
    orig = manifest[cp.proof_key(URL, quote)]
    assert orig["verdict"] == "absent"
    assert orig["re_anchor"]["proof_id"] == ra["proof_id"]
    sub = manifest[ra["proof_id"]]
    assert sub["verdict"] == "present"
    assert sub["re_anchored_from"] == cp.proof_key(URL, quote)
    # re-anchored quote must itself be verbatim page text (match-normalized)
    assert cp.match_norm(ra["quote"]) in cp.match_norm(PAGE)


def test_reanchor_none_when_nothing_anchors(tmp_path, monkeypatch):
    _seed_cache(tmp_path)
    _no_network(monkeypatch)
    rec = cp.build_proof(URL, "totally unrelated fabricated assertion here",
                         tmp_path, archive_tier="none", wayback_on=False)
    assert rec["verdict"] == "absent"
    assert "re_anchor" in rec and rec["re_anchor"] is None  # ran, found nothing


def test_reanchor_never_recurses(tmp_path, monkeypatch):
    _seed_cache(tmp_path)
    _no_network(monkeypatch)
    depth = {"n": 0}
    orig_find = cp.find_reanchor

    def counting_find(quote, text):
        depth["n"] += 1
        return orig_find(quote, text)

    monkeypatch.setattr(cp, "find_reanchor", counting_find)
    cp.build_proof(URL, "You may use this domain in literature without any "
                        "permission slip", tmp_path,
                   archive_tier="none", wayback_on=False)
    assert depth["n"] == 1  # the retry itself never triggers another retry


def test_reanchor_off_flag(tmp_path, monkeypatch):
    _seed_cache(tmp_path)
    _no_network(monkeypatch)
    rec = cp.build_proof(URL, "You may use this domain in literature without "
                              "any permission slip", tmp_path,
                         archive_tier="none", wayback_on=False,
                         re_anchor=False)
    assert rec["verdict"] == "absent"
    assert "re_anchor" not in rec
