"""Tests for autoremediate.tools."""
import os
import pytest
from pathlib import Path
from autoremediate.tools import read_file, grep, run_tool


def test_read_file_numbering_and_truncation(tmp_path):
    repo_root = tmp_path
    sample = repo_root / "sample.py"
    sample.write_text("\n".join(f"line_{i}" for i in range(1, 20)), encoding="utf-8")

    # Read with max_lines=5
    output = read_file("sample.py", repo_root=repo_root, max_lines=5)
    lines = output.splitlines()
    assert len(lines) == 6  # 5 lines + "... (truncated)"
    assert lines[0] == "   1 | line_1"
    assert lines[4] == "   5 | line_5"
    assert lines[5] == "... (truncated)"

    # Read all lines
    full_output = read_file("sample.py", repo_root=repo_root, max_lines=50)
    assert "... (truncated)" not in full_output
    assert "  12 | line_12" in full_output


def test_grep_matches_and_skips_ignored(tmp_path):
    repo_root = tmp_path

    # Create files in normal dir
    pkg = repo_root / "my_pkg"
    pkg.mkdir()
    (pkg / "mod.py").write_text("def find_me():\n    pass\n", encoding="utf-8")

    # Create files in ignored dirs
    for ignored in [".git", "__pycache__", ".venv", "failures", "recordings"]:
        d = repo_root / ignored
        d.mkdir()
        (d / "dummy.py").write_text("find_me in ignored\n", encoding="utf-8")

    output = grep("find_me", "my_pkg", repo_root=repo_root)
    assert "my_pkg/mod.py:1: def find_me():" in output

    # Grep repo_root should skip ignored dirs
    root_output = grep("find_me", ".", repo_root=repo_root)
    assert "my_pkg/mod.py:1: def find_me():" in root_output
    assert ".git" not in root_output
    assert ".venv" not in root_output
    assert "failures" not in root_output


def test_grep_max_matches(tmp_path):
    repo_root = tmp_path
    sample = repo_root / "many.py"
    sample.write_text("\n".join(f"target_{i}" for i in range(10)), encoding="utf-8")

    output = grep("target", "many.py", repo_root=repo_root, max_matches=3)
    lines = [l for l in output.splitlines() if l.strip()]
    assert len(lines) == 3


def test_run_tool_path_traversal_and_escapes(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    outside_file = tmp_path / "secret.txt"
    outside_file.write_text("classified", encoding="utf-8")

    # 1. Absolute path rejected
    res_abs = run_tool("read_file", {"path": str(outside_file)}, repo_root=repo_root)
    assert res_abs.startswith("ERROR:")

    # 2. ".." traversal rejected
    res_dotdot = run_tool("read_file", {"path": "../secret.txt"}, repo_root=repo_root)
    assert res_dotdot.startswith("ERROR:")

    # 3. Symlink escape rejected
    symlink = repo_root / "escape_link.txt"
    try:
        symlink.symlink_to(outside_file)
        res_sym = run_tool("read_file", {"path": "escape_link.txt"}, repo_root=repo_root)
        assert res_sym.startswith("ERROR:")
    except (OSError, NotImplementedError):
        # In environments without symlink permissions, skip this assertion
        pass

    # 4. Unknown action rejected
    res_unknown = run_tool("delete_file", {"path": "anything"}, repo_root=repo_root)
    assert res_unknown.startswith("ERROR:")

    # 5. Missing args rejected
    res_bad_args = run_tool("read_file", {}, repo_root=repo_root)
    assert res_bad_args.startswith("ERROR:")


def test_run_tool_valid_calls(tmp_path):
    repo_root = tmp_path
    f = repo_root / "test.py"
    f.write_text("hello world\n", encoding="utf-8")

    read_res = run_tool("read_file", {"path": "test.py"}, repo_root=repo_root)
    assert "   1 | hello world" in read_res

    grep_res = run_tool("grep", {"pattern": "world", "path": "."}, repo_root=repo_root)
    assert "test.py:1: hello world" in grep_res
