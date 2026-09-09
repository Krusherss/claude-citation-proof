"""Regression test for c10005: subprocess.run must use encoding='utf-8'.

Without explicit encoding, Python on Windows uses the active codepage (e.g.
cp1252), garbling any non-ASCII UTF-8 output from the Node helper. The bug
was masked by PYTHONUTF8=1 in Claude's shell.
"""
import ast
import inspect
import textwrap

import pytest

from scripts.cite_proof import fragment_from_page


def test_subprocess_run_uses_utf8_encoding():
    """The subprocess.run call in fragment_from_page must pass encoding='utf-8'."""
    source = inspect.getsource(fragment_from_page)
    tree = ast.parse(textwrap.dedent(source))

    found_subprocess_run = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_src = ast.dump(node.func)
        if "subprocess" not in call_src or "run" not in call_src:
            continue
        found_subprocess_run = True
        kw_names = {kw.arg for kw in node.keywords}
        assert "encoding" in kw_names, (
            "subprocess.run in fragment_from_page missing encoding kwarg — "
            "Windows will use the active codepage instead of UTF-8 (c10005)"
        )
        for kw in node.keywords:
            if kw.arg == "encoding":
                assert isinstance(kw.value, ast.Constant)
                assert kw.value.value == "utf-8", (
                    f"encoding must be 'utf-8', got {kw.value.value!r}"
                )

    assert found_subprocess_run, "Could not find subprocess.run call in fragment_from_page"
