"""Verification engine for Auto-Remediate.

Executes a 4-stage verification in an isolated temporary sandbox:
1. reproducer_fails_before (must fail and match exception_type)
2. patch_applied
3. reproducer_passes_after (must pass with code 0)
4. suite_passes (full existing test suite must pass with code 0)
"""
import os
import sys
import shutil
import difflib
import tempfile
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Stage:
    name: str  # "reproducer_fails_before" | "patch_applied" | "reproducer_passes_after" | "suite_passes"
    passed: bool
    output: str  # pytest output, truncated to 3000 chars


@dataclass
class VerifyResult:
    status: str  # VERIFIED | REJECTED_NO_REPRO | REJECTED_WRONG_FAILURE | PATCH_FAILED | REGRESSION | INVALID_PATCH | TIMEOUT
    reason: str
    stages: list[Stage]
    diff: str  # unified diff of patched files vs the real repo ("" if none)


def _run_pytest(args: list[str], cwd: Path, timeout: int) -> tuple[int, str, bool]:
    """Run pytest in a subprocess with isolated PYTHONPATH and timeout."""
    env = os.environ.copy()
    cwd_str = str(cwd.resolve())
    env["PYTHONPATH"] = cwd_str + (os.pathsep + env["PYTHONPATH"] if "PYTHONPATH" in env else "")

    cmd = [sys.executable, "-m", "pytest"] + args
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        return proc.returncode, out[:3000], False
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        return -1, out[:3000] if out else "Execution timed out", True


def verify(
    failure: dict,
    final: dict,
    repo_root: Path,
    tests_dir: str,
    timeout: int = 60,
) -> VerifyResult:
    """Verify that a reproducer and patch solve the failure without regressions."""
    patched_files = final.get("patched_files", {})
    if not patched_files:
        return VerifyResult("INVALID_PATCH", "No patched files provided in final", [], "")

    root_resolved = repo_root.resolve()
    tests_dir_resolved = (root_resolved / tests_dir).resolve()

    # 1. Validate paths
    for rel_path in patched_files:
        if not isinstance(rel_path, str) or not rel_path.strip():
            return VerifyResult("INVALID_PATCH", f"Invalid path string: {rel_path!r}", [], "")

        p = Path(rel_path)
        if p.is_absolute() or ".." in p.parts:
            return VerifyResult("INVALID_PATCH", f"Non-relative or traversal path: {rel_path}", [], "")

        target = (root_resolved / p).resolve()
        try:
            target.relative_to(root_resolved)
        except ValueError:
            return VerifyResult("INVALID_PATCH", f"Path outside repo root: {rel_path}", [], "")

        if not target.is_file():
            return VerifyResult("INVALID_PATCH", f"Patched file does not exist: {rel_path}", [], "")

        # Must not be inside tests directory
        try:
            target.relative_to(tests_dir_resolved)
            return VerifyResult("INVALID_PATCH", f"Patched file cannot be inside test directory: {rel_path}", [], "")
        except ValueError:
            pass

    reproducer_code = final.get("reproducer_test")
    if not reproducer_code:
        return VerifyResult("INVALID_PATCH", "No reproducer_test provided", [], "")

    # 2. Temp copy
    stages: list[Stage] = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        temp_repo = Path(tmp_dir) / "sandbox"
        ignore_patterns = shutil.ignore_patterns(
            ".git", "__pycache__", ".venv", ".pytest_cache", "failures", "recordings"
        )
        shutil.copytree(root_resolved, temp_repo, ignore=ignore_patterns, symlinks=True)

        failure_id = str(failure.get("id", "1"))
        reproducer_rel = f"{tests_dir}/test_repro_{failure_id}.py"
        reproducer_file = temp_repo / reproducer_rel
        reproducer_file.parent.mkdir(parents=True, exist_ok=True)
        reproducer_file.write_text(reproducer_code, encoding="utf-8")

        # 4. Run BEFORE patch
        ret, out, timed_out = _run_pytest(
            [reproducer_rel, "-q", "-x", "-p", "no:cacheprovider"],
            cwd=temp_repo,
            timeout=timeout,
        )
        if timed_out:
            stages.append(Stage("reproducer_fails_before", False, out))
            return VerifyResult("TIMEOUT", "Reproducer timed out before patch", stages, "")

        if ret == 0:
            stages.append(Stage("reproducer_fails_before", False, out))
            return VerifyResult("REJECTED_NO_REPRO", "reproducer passed before the patch", stages, "")

        expected_exc = failure.get("exception_type", "")
        if expected_exc and expected_exc not in out:
            stages.append(Stage("reproducer_fails_before", False, out))
            return VerifyResult(
                "REJECTED_WRONG_FAILURE",
                f"Reproducer failed without expected exception type {expected_exc}",
                stages,
                "",
            )

        stages.append(Stage("reproducer_fails_before", True, out))

        # 5. Apply patch in temp copy
        for rel_path, content in patched_files.items():
            dest = temp_repo / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
        stages.append(Stage("patch_applied", True, "Patch applied cleanly in sandbox"))

        # 6. Run reproducer AFTER patch
        ret, out, timed_out = _run_pytest(
            [reproducer_rel, "-q", "-x", "-p", "no:cacheprovider"],
            cwd=temp_repo,
            timeout=timeout,
        )
        if timed_out:
            stages.append(Stage("reproducer_passes_after", False, out))
            return VerifyResult("TIMEOUT", "Reproducer timed out after patch", stages, "")

        if ret != 0:
            stages.append(Stage("reproducer_passes_after", False, out))
            return VerifyResult("PATCH_FAILED", "Reproducer still fails after patch", stages, "")

        stages.append(Stage("reproducer_passes_after", True, out))

        # 7. Run full suite
        ret, out, timed_out = _run_pytest(
            [tests_dir, "-q", "-x", "-p", "no:cacheprovider"],
            cwd=temp_repo,
            timeout=timeout,
        )
        if timed_out:
            stages.append(Stage("suite_passes", False, out))
            return VerifyResult("TIMEOUT", "Suite timed out after patch", stages, "")

        if ret != 0:
            stages.append(Stage("suite_passes", False, out))
            return VerifyResult("REGRESSION", "Existing test suite failed with patch", stages, "")

        stages.append(Stage("suite_passes", True, out))

    # 8. All good -> Build unified diff
    diff_parts: list[str] = []
    for rel_path, new_content in patched_files.items():
        original_file = root_resolved / rel_path
        orig_text = original_file.read_text(encoding="utf-8")
        diff = difflib.unified_diff(
            orig_text.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{rel_path}",
            tofile=f"b/{rel_path}",
        )
        diff_str = "".join(diff)
        if diff_str:
            diff_parts.append(diff_str)

    return VerifyResult(
        status="VERIFIED",
        reason="All verification stages passed",
        stages=stages,
        diff="\n".join(diff_parts),
    )


def apply_patch_to_repo(final: dict, repo_root: Path) -> list[str]:
    """Apply the patched files from final dict directly into the repository root."""
    patched_files = final.get("patched_files", {})
    modified: list[str] = []
    root_resolved = repo_root.resolve()

    for rel_path, content in patched_files.items():
        target = (root_resolved / rel_path).resolve()
        target.relative_to(root_resolved)  # Ensure within repo root
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        modified.append(rel_path)

    return modified
