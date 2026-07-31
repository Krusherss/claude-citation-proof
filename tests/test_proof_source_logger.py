import importlib.util
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
