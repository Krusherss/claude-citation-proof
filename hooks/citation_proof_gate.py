#!/usr/bin/env python3
"""Claude Code Stop hook for deterministic web-citation accountability.

The hook understands citation blocks in the assistant's response:

    Src: https://example.com/page
    Quote: "verbatim text from that page"

It checks those blocks against ``.proof/proof_manifest.json`` and compares web
URLs observed by ``proof_source_logger.py`` with the URLs cited during the same
Claude Code session. Projects opt into blocking by creating ``.proof/BLOCK``;
otherwise the hook only warns. All generated evidence remains under ``.proof``.

This is intentionally the small source-accountability lane. It does not include
unrelated assertion judges, transcript memory, or private global configuration.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

HOOK_NAME = "citation_proof_gate"
DISABLE_FLAG = Path.home() / ".claude" / "hooks" / f"disable_{HOOK_NAME}.flag"
CITE_PROOF = Path(__file__).resolve().parents[1] / "scripts" / "cite_proof.py"

_DASHES = "[‐‑‒–—−]"
_TEXT_FRAGMENT = "#:~:text="
_SRC_RE = re.compile(r"^\s*Src:\s*(\S.*?)\s*$")
_QUOTE_RE = re.compile(r"^\s*Quote:\s*(\S.*?)\s*$")
_HTTP_RE = re.compile(r"https?://")


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(_DASHES, "-", value)
    return re.sub(r"\s+", " ", value).strip()


def canon_url(url: str) -> str:
    """Match cite_proof's deliberately minimal URL canonicalization."""
    base = url.strip().split(_TEXT_FRAGMENT)[0]
    parts = urlsplit(base)
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, parts.fragment)
    )


def proof_key(url: str, quote: str) -> str:
    payload = f"{canon_url(url)}|{_norm(quote)}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _unwrap_quote(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def parse_citation_blocks(text: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    pending_url: str | None = None
    for line in text.splitlines():
        source = _SRC_RE.match(line)
        if source:
            pending_url = source.group(1)
            continue
        quote = _QUOTE_RE.match(line)
        if quote and pending_url is not None:
            if _HTTP_RE.match(pending_url):
                blocks.append(
                    {"url": pending_url, "quote": _unwrap_quote(quote.group(1))}
                )
            pending_url = None
    return blocks


def evaluate(text: str, manifest: dict, mode: str = "warn") -> dict:
    misses = []
    blocks = parse_citation_blocks(text)
    for block in blocks:
        key = proof_key(block["url"], block["quote"])
        entry = manifest.get(key)
        if entry is None:
            reason = "missing"
        elif entry.get("verdict") == "present":
            continue
        else:
            reason = f"verdict={entry.get('verdict')}"
        misses.append({**block, "key": key, "reason": reason})
    return {
        "action": "pass" if not misses else ("block" if mode == "block" else "warn"),
        "misses": misses,
        "processed": len(blocks),
        "passed": len(blocks) - len(misses),
    }


def sources_for_session(rows: list, session_id: str) -> list:
    if not session_id:
        return []
    return [
        row
        for row in rows
        if isinstance(row, dict) and row.get("session_id") == session_id
    ]


def behavior_gate(text: str, sources_seen: list, mode: str = "warn") -> dict:
    """Find sources fetched in this session but never cited in assistant text."""
    cited = {canon_url(block["url"]) for block in parse_citation_blocks(text)}
    uncited: list[str] = []
    seen: set[str] = set()
    for row in sources_seen:
        url = (row or {}).get("url")
        if not isinstance(url, str) or not _HTTP_RE.match(url):
            continue
        key = canon_url(url)
        if key in cited or key in seen:
            continue
        seen.add(key)
        uncited.append(url)
    if not uncited:
        return {"action": "pass", "uncited_sources": []}
    return {
        "action": "block" if mode == "block" else "warn",
        "uncited_sources": uncited,
        "remediation": (
            "Fetched but uncited web source(s): "
            + ", ".join(uncited)
            + ". Add one citation block per relied-on source:\n"
            + '  Src: <url>\n  Quote: "<verbatim text copied from that page>"'
        ),
    }


def _load_json(path: Path, default):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value
    except Exception:
        return default


def _load_jsonl(path: Path) -> list:
    rows = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    return rows


def _transcript_messages(path: str) -> list:
    if not path:
        return []
    return _load_jsonl(Path(path))


def _assistant_texts(messages: list) -> list[str]:
    texts = []
    for row in messages:
        message = row.get("message") if isinstance(row, dict) else None
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts.append(
                "\n".join(
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                )
            )
    return texts


def _mode(cwd: Path) -> str:
    configured = os.environ.get("CITATION_PROOF_MODE", "").strip().lower()
    if configured in {"warn", "block"}:
        return configured
    return "block" if (cwd / ".proof" / "BLOCK").exists() else "warn"


def _append_log(cwd: Path, row: dict) -> None:
    try:
        proof_dir = cwd / ".proof"
        proof_dir.mkdir(parents=True, exist_ok=True)
        with (proof_dir / "gate_log.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _load_inflight(cwd: Path) -> set[str]:
    return {
        row.get("key")
        for row in _load_jsonl(cwd / ".proof" / "autocite_seen.jsonl")
        if isinstance(row, dict) and isinstance(row.get("key"), str)
    }


def _record_inflight(cwd: Path, key: str) -> None:
    try:
        proof_dir = cwd / ".proof"
        proof_dir.mkdir(parents=True, exist_ok=True)
        with (proof_dir / "autocite_seen.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"key": key}) + "\n")
    except Exception:
        pass


def _spawn_missing(cwd: Path, misses: list[dict], inflight: set[str]) -> None:
    """Build missing proofs in the background without evaluating shell text."""
    if not CITE_PROOF.exists():
        return
    for miss in misses:
        if miss.get("reason") != "missing" or miss.get("key") in inflight:
            continue
        argv = [
            sys.executable,
            str(CITE_PROOF),
            miss["url"],
            miss["quote"],
            "--proof-dir",
            str(cwd / ".proof"),
        ]
        kwargs = {
            "cwd": str(cwd),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if os.name == "nt":
            kwargs["creationflags"] = (
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            kwargs["start_new_session"] = True
        try:
            subprocess.Popen(argv, shell=False, **kwargs)
            _record_inflight(cwd, miss["key"])
        except Exception:
            continue


def main() -> None:
    if DISABLE_FLAG.exists():
        return
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        return
    if payload.get("stop_hook_active"):
        return

    cwd = Path(payload.get("cwd") or ".").resolve()
    session_id = payload.get("session_id") or ""
    messages = _transcript_messages(payload.get("transcript_path") or "")
    assistant_turns = _assistant_texts(messages)
    current_text = assistant_turns[-1] if assistant_turns else ""
    all_text = "\n".join(assistant_turns)
    mode = _mode(cwd)

    manifest = _load_json(cwd / ".proof" / "proof_manifest.json", {})
    if not isinstance(manifest, dict):
        manifest = {}
    result = evaluate(current_text, manifest, mode)
    result["session_id"] = session_id
    if result["processed"]:
        _append_log(cwd, result)

    inflight = _load_inflight(cwd)
    _spawn_missing(cwd, result["misses"], inflight)

    rows = sources_for_session(
        _load_jsonl(cwd / ".proof" / "sources_seen.jsonl"), session_id
    )
    behavior = behavior_gate(all_text, rows, mode)
    if behavior["action"] != "pass":
        _append_log(
            cwd,
            {
                "action": f"behavior_{behavior['action']}",
                "uncited_sources": behavior["uncited_sources"],
                "session_id": session_id,
            },
        )

    reasons = []
    if result["action"] == "block":
        detail = "\n".join(
            f"  - {miss['reason']}: {miss['url']}" for miss in result["misses"]
        )
        reasons.append(f"citation_proof_gate: cited claims lack proof:\n{detail}")
    if behavior["action"] == "block":
        reasons.append(f"citation_proof_gate: {behavior['remediation']}")
    if reasons:
        print("\n".join(reasons), file=sys.stderr)
        raise SystemExit(2)

    warnings = []
    if result["action"] == "warn":
        warnings.append(f"{len(result['misses'])} citation(s) lack proof")
    if behavior["action"] == "warn":
        warnings.append(f"{len(behavior['uncited_sources'])} fetched source(s) are uncited")
    if warnings:
        print("[citation_proof_gate] warn: " + "; ".join(warnings), file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        print(f"{HOOK_NAME} error (fail-open): {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(0)
