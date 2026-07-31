import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOGGER_PATH = ROOT / "hooks" / "proof_source_logger.py"
SPEC = importlib.util.spec_from_file_location("proof_source_logger", LOGGER_PATH)
logger = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(logger)


def test_extract_url_requires_http_url_field():
    assert logger.extract_url("WebFetch", {"url": "https://example.com"}) == "https://example.com"
    assert logger.extract_url("WebSearch", {"query": "example"}) is None
    assert logger.extract_url("Read", {"url": "file:///tmp/data"}) is None


def test_log_entry_hash_is_reflow_invariant():
    first = logger.make_log_entry("https://example.com", "one\n two", "now", "s1")
    second = logger.make_log_entry("https://example.com", "one two", "now", "s1")
    assert first["normalized_text_hash"] == second["normalized_text_hash"]
    assert first["session_id"] == "s1"


def test_local_git_exclude_is_added_once(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    assert logger.ensure_local_git_exclude(tmp_path) is True
    assert logger.ensure_local_git_exclude(tmp_path) is True

    exclude = (tmp_path / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert exclude.splitlines().count(".proof/") == 1
    ignored = subprocess.run(
        ["git", "-C", str(tmp_path), "check-ignore", "-q", ".proof/probe.json"],
        check=False,
    )
    assert ignored.returncode == 0


def test_local_git_exclude_fails_open_on_non_utf8_file(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    exclude = tmp_path / ".git" / "info" / "exclude"
    exclude.write_bytes(b"\xff\xfe\x00")

    assert logger.ensure_local_git_exclude(tmp_path) is False
