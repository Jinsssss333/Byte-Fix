"""Path-safe exploration tools (read_file, grep, run_tool) for Auto-Remediate."""
import os
import re
from pathlib import Path
from typing import Any

IGNORED_DIRS = {".git", "__pycache__", ".venv", "failures", "recordings", ".pytest_cache"}


def _resolve_safe_path(rel_path_str: str, repo_root: Path) -> Path:
    """Validate and resolve relative path ensuring it stays within repo_root."""
    if not isinstance(rel_path_str, str):
        raise ValueError(f"Invalid path type: {type(rel_path_str)}")

    p_str = rel_path_str.strip()
    if not p_str:
        p_str = "."

    p = Path(p_str)
    if p.is_absolute() or p_str.startswith("/") or p_str.startswith("\\"):
        raise ValueError(f"Absolute paths are not allowed: {rel_path_str}")

    if ".." in p.parts:
        raise ValueError(f"Directory traversal '..' is not allowed: {rel_path_str}")

    root_resolved = repo_root.resolve()
    target_resolved = (root_resolved / p).resolve()

    try:
        target_resolved.relative_to(root_resolved)
    except ValueError:
        raise ValueError(f"Path resolves outside repository root: {rel_path_str}")

    return target_resolved


def read_file(path: str, repo_root: Path, max_lines: int = 200) -> str:
    """Read a file and return numbered lines, truncating after max_lines."""
    target = _resolve_safe_path(path, repo_root)
    if not target.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    truncated = len(lines) > max_lines
    display_lines = lines[:max_lines]

    formatted = [f"{idx:>4} | {line}" for idx, line in enumerate(display_lines, start=1)]
    if truncated:
        formatted.append("... (truncated)")

    return "\n".join(formatted)


def grep(pattern: str, path: str, repo_root: Path, max_matches: int = 30) -> str:
    """Search for a regex pattern within files, skipping ignored directories."""
    regex = re.compile(pattern)
    target = _resolve_safe_path(path, repo_root)
    if not target.exists():
        raise FileNotFoundError(f"Path not found: {path}")

    root_resolved = repo_root.resolve()
    files_to_search: list[Path] = []

    if target.is_file():
        files_to_search = [target]
    else:
        for root, dirs, files in os.walk(target):
            dirs[:] = [
                d
                for d in sorted(dirs)
                if d not in IGNORED_DIRS and not d.startswith(".")
            ]
            for fn in sorted(files):
                files_to_search.append(Path(root) / fn)

    matches: list[str] = []
    for file_path in files_to_search:
        if len(matches) >= max_matches:
            break

        try:
            rel_file = file_path.relative_to(root_resolved)
        except ValueError:
            continue

        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            for lineno, line in enumerate(content.splitlines(), start=1):
                if regex.search(line):
                    matches.append(f"{rel_file}:{lineno}: {line.strip()}")
                    if len(matches) >= max_matches:
                        break
        except Exception:
            continue

    return "\n".join(matches)


def run_tool(action: str, args: dict[str, Any], repo_root: Path) -> str:
    """Safely execute a tool action. Never raises; returns 'ERROR: ...' on bad input."""
    try:
        if not isinstance(args, dict):
            return f"ERROR: Expected args to be a dictionary, got {type(args).__name__}"

        if action == "read_file":
            path = args.get("path")
            if not path:
                return "ERROR: Missing required argument 'path'"
            max_lines = int(args.get("max_lines", 200))
            return read_file(path, repo_root=repo_root, max_lines=max_lines)

        if action == "grep":
            pattern = args.get("pattern")
            if not pattern:
                return "ERROR: Missing required argument 'pattern'"
            path = args.get("path", ".")
            max_matches = int(args.get("max_matches", 30))
            return grep(pattern, path, repo_root=repo_root, max_matches=max_matches)

        return f"ERROR: Unknown action: {action}"
    except Exception as exc:
        return f"ERROR: {exc}"
