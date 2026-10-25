"""Tests for autoremediate.verify."""
import re
import json
import shutil
from pathlib import Path
from autoremediate.verify import verify, apply_patch_to_repo


def _parse_canned(text: str) -> dict:
    repro = re.search(r"<reproducer_test>(.*?)</reproducer_test>", text, re.DOTALL).group(1).strip() + "\n"
    p_match = re.search(r'<patched_file\s+path="([^"]+)">(.*?)</patched_file>', text, re.DOTALL)
    p_path = p_match.group(1).strip()
    p_content = p_match.group(2).strip() + "\n"
    return {
        "reproducer_test": repro,
        "patched_files": {p_path: p_content},
    }


def test_verify_case1_success():
    repo_root = Path.cwd().resolve()
    failure_path = repo_root / "tests/fixtures/failure_1.json"
    failure = json.loads(failure_path.read_text(encoding="utf-8"))

    canned_text = (repo_root / "tests/fixtures/canned_final_1.txt").read_text(encoding="utf-8")
    final = _parse_canned(canned_text)

    # Initial file state snapshot
    tasks_file = repo_root / "demo_app/tasks.py"
    initial_content = tasks_file.read_text(encoding="utf-8")

    result = verify(failure, final, repo_root=repo_root, tests_dir="demo_app/tests")

    assert result.status == "VERIFIED"
    assert len(result.stages) == 4
    assert all(s.passed for s in result.stages)
    assert "+" in result.diff
    assert "demo_app/tasks.py" in result.diff

    # Verify real repo file is unchanged
    assert tasks_file.read_text(encoding="utf-8") == initial_content


def test_verify_case2_reproducer_passes_before():
    repo_root = Path.cwd().resolve()
    failure = {"id": "1", "exception_type": "AttributeError"}
    final = {
        "reproducer_test": "def test_already_passes():\n    assert True\n",
        "patched_files": {"demo_app/tasks.py": (repo_root / "demo_app/tasks.py").read_text(encoding="utf-8")},
    }
    result = verify(failure, final, repo_root=repo_root, tests_dir="demo_app/tests")
    assert result.status == "REJECTED_NO_REPRO"
    assert not result.diff


def test_verify_case3_patch_regression():
    repo_root = Path.cwd().resolve()
    failure_path = repo_root / "tests/fixtures/failure_1.json"
    failure = json.loads(failure_path.read_text(encoding="utf-8"))

    canned_text = (repo_root / "tests/fixtures/canned_final_1.txt").read_text(encoding="utf-8")
    final = _parse_canned(canned_text)

    # Corrupt tasks.py so that send_receipt produces bad output breaking test_tasks.py (e.g. omitting order id)
    broken_patch = final["patched_files"]["demo_app/tasks.py"].replace("(order {order['id']})", "")
    final["patched_files"]["demo_app/tasks.py"] = broken_patch

    result = verify(failure, final, repo_root=repo_root, tests_dir="demo_app/tests")
    assert result.status in ("REGRESSION", "PATCH_FAILED")


def test_verify_case4_patch_inside_tests():
    repo_root = Path.cwd().resolve()
    failure = {"id": "1", "exception_type": "AttributeError"}
    final = {
        "reproducer_test": "def test_repro():\n    assert False\n",
        "patched_files": {"demo_app/tests/test_tasks.py": "broken"},
    }
    result = verify(failure, final, repo_root=repo_root, tests_dir="demo_app/tests")
    assert result.status == "INVALID_PATCH"


def test_verify_case5_reproducer_timeout():
    repo_root = Path.cwd().resolve()
    failure = {"id": "1", "exception_type": "AttributeError"}
    final = {
        "reproducer_test": "import time\ndef test_infinite():\n    time.sleep(10)\n",
        "patched_files": {"demo_app/tasks.py": (repo_root / "demo_app/tasks.py").read_text(encoding="utf-8")},
    }
    result = verify(failure, final, repo_root=repo_root, tests_dir="demo_app/tests", timeout=2)
    assert result.status == "TIMEOUT"


def test_verify_case6_nonexistent_patch_path():
    repo_root = Path.cwd().resolve()
    failure = {"id": "1", "exception_type": "AttributeError"}
    final = {
        "reproducer_test": "def test_foo():\n    assert False\n",
        "patched_files": {"demo_app/nonexistent.py": "content"},
    }
    result = verify(failure, final, repo_root=repo_root, tests_dir="demo_app/tests")
    assert result.status == "INVALID_PATCH"


def test_apply_patch_to_repo(tmp_path):
    repo_copy = tmp_path / "repo"
    shutil.copytree(Path.cwd().resolve(), repo_copy, ignore=shutil.ignore_patterns(".venv", ".git", "__pycache__"))

    target_file = repo_copy / "demo_app/tasks.py"
    final = {
        "patched_files": {"demo_app/tasks.py": "# patched content\n"}
    }
    modified = apply_patch_to_repo(final, repo_root=repo_copy)
    assert modified == ["demo_app/tasks.py"]
    assert target_file.read_text(encoding="utf-8") == "# patched content\n"
