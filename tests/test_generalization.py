"""Generalization test for Auto-Remediate on second demo bug (Decimal serialization in generate_invoice)."""
import json
from pathlib import Path
from autoremediate.agent import parse_action
from autoremediate.verify import verify


def test_generalization_decimal_bug():
    repo_root = Path.cwd().resolve()
    failure_path = repo_root / "tests/fixtures/failure_2_decimal.json"
    failure = json.loads(failure_path.read_text(encoding="utf-8"))

    canned_path = repo_root / "tests/fixtures/canned_final_2.txt"
    canned_text = canned_path.read_text(encoding="utf-8")

    final = parse_action(canned_text)
    assert final["kind"] == "final"
    assert "Decimal" in final["root_cause"]
    assert "generate_invoice" in final["reproducer_test"]

    result = verify(failure, final, repo_root=repo_root, tests_dir="demo_app/tests")
    assert result.status == "VERIFIED"
    assert len(result.stages) == 4
    assert all(s.passed for s in result.stages)
    assert "default=str" in result.diff
