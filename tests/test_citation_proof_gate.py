import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE_PATH = ROOT / "hooks" / "citation_proof_gate.py"
SPEC = importlib.util.spec_from_file_location("citation_proof_gate", GATE_PATH)
gate = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(gate)


def _transcript(path: Path, text: str) -> None:
    row = {"message": {"role": "assistant", "content": text}}
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")


def _run_gate(tmp_path: Path, text: str, *, block: bool, sources=None):
    proof = tmp_path / ".proof"
    proof.mkdir()
    if block:
        (proof / "BLOCK").touch()
    if sources:
        (proof / "sources_seen.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in sources), encoding="utf-8"
        )
    transcript = tmp_path / "transcript.jsonl"
    _transcript(transcript, text)
    payload = {
        "cwd": str(tmp_path),
        "session_id": "session-test",
        "transcript_path": str(transcript),
        "stop_hook_active": False,
    }
    return subprocess.run(
        [sys.executable, str(GATE_PATH)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )


def test_parse_and_key_ignore_text_fragment():
    text = 'Src: https://Example.COM/page#:~:text=old\nQuote: "Exact quote"'
    blocks = gate.parse_citation_blocks(text)
    assert blocks == [{"url": "https://Example.COM/page#:~:text=old", "quote": "Exact quote"}]
    assert gate.proof_key(blocks[0]["url"], "Exact quote") == gate.proof_key(
        "https://example.com/page", "Exact quote"
    )


def test_gate_local_git_exclude_is_added_once(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    assert gate.ensure_local_git_exclude(tmp_path) is True
    assert gate.ensure_local_git_exclude(tmp_path) is True

    exclude = (tmp_path / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert exclude.splitlines().count(".proof/") == 1


def test_gate_git_exclude_uses_common_dir_for_linked_worktree(tmp_path):
    repository = tmp_path / "repository"
    linked = tmp_path / "linked"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    (repository / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "tracked.txt"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=Citation Proof Tests",
            "-c",
            "user.email=tests@example.invalid",
            "commit",
            "-qm",
            "baseline",
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "worktree", "add", "-q", str(linked)],
        check=True,
    )

    assert gate.ensure_local_git_exclude(linked) is True

    exclude = (repository / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert exclude.splitlines().count(".proof/") == 1


def test_manifest_present_passes():
    text = 'Src: https://example.com/page\nQuote: "Exact quote"'
    key = gate.proof_key("https://example.com/page", "Exact quote")
    result = gate.evaluate(text, {key: {"verdict": "present"}}, "block")
    assert result["action"] == "pass"
    assert result["passed"] == 1


def test_uncited_source_blocks_only_in_block_mode(tmp_path):
    rows = [{"url": "https://example.com/page", "session_id": "session-test"}]
    blocked = _run_gate(tmp_path, "I used a web source.", block=True, sources=rows)
    assert blocked.returncode == 2
    assert "Fetched but uncited" in blocked.stderr


def test_warn_mode_does_not_block(tmp_path):
    rows = [{"url": "https://example.com/page", "session_id": "session-test"}]
    warned = _run_gate(tmp_path, "I used a web source.", block=False, sources=rows)
    assert warned.returncode == 0
    assert "warn" in warned.stderr


def test_other_sessions_are_ignored(tmp_path):
    rows = [{"url": "https://example.com/page", "session_id": "someone-else"}]
    result = _run_gate(tmp_path, "No web claim here.", block=True, sources=rows)
    assert result.returncode == 0
