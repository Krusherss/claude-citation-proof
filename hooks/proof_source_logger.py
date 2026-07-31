#!/usr/bin/env python3
"""proof_source_logger: PostToolUse hook (WebFetch|WebSearch|mcp__.*) — append
web-source provenance to <cwd>/.proof/sources_seen.jsonl so the later gate-time
verbatim check is cheap.

PostToolUse cannot block (the tool already ran), so this hook only records: for
each http(s) source it writes {url, ts, normalized_text_hash[, session_id]} and
exits 0. Self-contained (no project imports) — a global hook must run standalone.
The hash is over NFKC + dash-folded + whitespace-collapsed text, so the same page
served with reflowed whitespace still matches (mirrors cite_proof's norm()).

Each row carries the PostToolUse `session_id` so the
Stop-time behavior_gate can compare fetched-vs-cited WITHIN one session and not
false-positive across sessions (proof_gate.sources_for_session). session_id is
OMITTED when empty, preserving the legacy {url, ts, hash} shape (those legacy
rows are simply dropped by the scope filter — under-warn is the safe direction).

Safety properties: the logger fails open, has a local disable flag, accepts only
HTTP(S) URL fields, never evaluates shell text, and writes only below the current
project's `.proof` directory.
"""
import hashlib
import json
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

HOOK_NAME = "proof_source_logger"
HOOKS_DIR = Path.home() / ".claude" / "hooks"
DISABLE_FLAG = HOOKS_DIR / f"disable_{HOOK_NAME}.flag"

_DASHES = "[‐‑‒–—−]"
_GIT_EXCLUDE_ENTRY = ".proof/"
_GIT_EXCLUDE_COMMENT = "# Citation Proof local evidence (generated; do not commit)"


def ensure_local_git_exclude(project_dir: Path) -> bool:
    """Best-effort local Git protection without editing tracked .gitignore."""
    try:
        result = subprocess.run(
            ["git", "-C", str(project_dir), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
            check=False,
            shell=False,
        )
        common = result.stdout.strip()
        if result.returncode != 0 or not common:
            return False
        common_dir = Path(common)
        if not common_dir.is_absolute():
            common_dir = (project_dir / common_dir).resolve()
        exclude = common_dir / "info" / "exclude"
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if _GIT_EXCLUDE_ENTRY in {line.strip() for line in existing.splitlines()}:
            return True
        exclude.parent.mkdir(parents=True, exist_ok=True)
        separator = "" if not existing or existing.endswith(("\n", "\r")) else "\n"
        with exclude.open("a", encoding="utf-8") as handle:
            handle.write(
                f"{separator}{_GIT_EXCLUDE_COMMENT}\n{_GIT_EXCLUDE_ENTRY}\n"
            )
        return True
    except (OSError, UnicodeError, subprocess.SubprocessError):
        return False


def _normalize(s: str) -> str:
    """NFKC + dash-family->'-' + whitespace-collapse + strip; no lowercase.
    Mirrors cite_proof.norm() so a hash here means the same 'text' the proof
    key / verdict systems mean."""
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(_DASHES, "-", s)
    return re.sub(r"\s+", " ", s).strip()


def extract_url(tool_name: str, tool_input: dict):
    """Pull the http(s) source url from tool_input, else None. WebFetch/most mcp
    fetch tools carry it in `url`; WebSearch has only a query (-> None)."""
    url = (tool_input or {}).get("url")
    if isinstance(url, str) and re.match(r"https?://", url):
        return url
    return None


def make_log_entry(url: str, text: str, ts: str, session_id: str = "") -> dict:
    """Provenance record; hash is over normalized text (reflow-invariant).
    `session_id` (from the PostToolUse payload) scopes the row to one session so
    the Stop-time behavior_gate can compare fetched-vs-cited WITHIN a session and
    not false-positive across sessions. Omitted entirely when empty, so the legacy
    {url, ts, hash} shape is preserved for rows that predate it."""
    digest = hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()
    entry = {"url": url, "ts": ts, "normalized_text_hash": digest}
    if session_id:
        entry["session_id"] = session_id
    return entry


def _text_of(resp) -> str:
    """Best-effort plain text from a PostToolUse tool_response of any shape."""
    if isinstance(resp, str):
        return resp
    if isinstance(resp, dict):
        for k in ("content", "text", "result", "output"):
            v = resp.get(k)
            if isinstance(v, str):
                return v
        return json.dumps(resp, ensure_ascii=False)
    return str(resp) if resp is not None else ""


def main() -> None:
    if DISABLE_FLAG.exists():  # [2] escape hatch — before any other work
        sys.exit(0)
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)  # fail-open on bad input
    url = extract_url(payload.get("tool_name", ""), payload.get("tool_input", {}) or {})
    if url is None:
        sys.exit(0)  # nothing to log at this layer
    ts = datetime.now(timezone.utc).isoformat()
    session_id = payload.get("session_id", "") or ""
    entry = make_log_entry(url, _text_of(payload.get("tool_response")), ts, session_id)
    cwd = Path(payload.get("cwd") or ".").resolve()
    try:
        ensure_local_git_exclude(cwd)
        proof = cwd / ".proof"  # fixed file under cwd/.proof only
        proof.mkdir(parents=True, exist_ok=True)
        with (proof / "sources_seen.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # best-effort logging; never disrupt the session
    sys.exit(0)  # [3] always exits 0 — PostToolUse cannot block


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # [1] fail-open
        print(f"{HOOK_NAME} error (fail-open): {exc}", file=sys.stderr)
        sys.exit(0)
