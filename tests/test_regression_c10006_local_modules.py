"""Regression test for c10006: moduleRoots() must include the helper's own node_modules.

Without this, `npm install --prefix scripts/` puts jsdom into
scripts/node_modules but the loader never looks there — it only checked
NODE_PATH and %APPDATA%/npm/node_modules.
"""
import subprocess
import json
import os
import sys

import pytest

HELPER = os.path.join(os.path.dirname(__file__), os.pardir, "scripts", "fragment_from_page.mjs")


def _node():
    """Return node path or skip."""
    for candidate in ["node", "node.exe"]:
        try:
            subprocess.run([candidate, "--version"], capture_output=True, check=True)
            return candidate
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    pytest.skip("node not available")


def test_module_roots_includes_local_node_modules():
    """moduleRoots() must list <script_dir>/node_modules as its first entry."""
    node = _node()
    script = (
        f'import {{ createRequire }} from "node:module";'
        f'import path from "node:path";'
        f'const require = createRequire(import.meta.url);'
        f'const m = await import(path.resolve("{HELPER.replace(chr(92), "/")}"));'
    )
    # Simpler: just grep the source for the pattern
    with open(os.path.normpath(HELPER), encoding="utf-8") as f:
        source = f.read()

    assert "node_modules" in source, "moduleRoots must reference node_modules"

    # The local node_modules path must appear BEFORE NODE_PATH entries
    lines = source.split("\n")
    roots_fn = False
    local_nm_line = None
    node_path_line = None
    for i, line in enumerate(lines):
        if "function moduleRoots" in line:
            roots_fn = True
        if not roots_fn:
            continue
        if "node_modules" in line and ("dirname" in line or "__dirname" in line or "import.meta" in line):
            local_nm_line = i
        if "NODE_PATH" in line and node_path_line is None:
            node_path_line = i
        if line.strip().startswith("return"):
            break

    assert local_nm_line is not None, (
        "moduleRoots() does not include the helper's own node_modules directory (c10006)"
    )
    assert node_path_line is not None, "moduleRoots() should also check NODE_PATH"
    assert local_nm_line < node_path_line, (
        "Local node_modules must be checked BEFORE NODE_PATH entries "
        f"(local at line {local_nm_line}, NODE_PATH at line {node_path_line})"
    )
