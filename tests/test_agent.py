"""Tests for autoremediate.agent."""
import json
import pytest
from pathlib import Path
from autoremediate.agent import parse_action, build_first_message, run_agent
from autoremediate.verify import verify


def test_parse_action():
    fixtures_dir = Path("tests/fixtures")

    # Tool call
    tool_reply = '{"action": "read_file", "args": {"path": "demo_app/tasks.py"}}'
    act = parse_action(tool_reply)
    assert act["kind"] == "tool"
    assert act["action"] == "read_file"
    assert act["args"] == {"path": "demo_app/tasks.py"}

    # Final from canned_final_1.txt
    final_text = (fixtures_dir / "canned_final_1.txt").read_text(encoding="utf-8")
    act_final = parse_action(final_text)
    assert act_final["kind"] == "final"
    assert "send_receipt assumes" in act_final["root_cause"]
    assert len(act_final["evidence"]) >= 2
    assert act_final["confidence"] == "high"
    assert "def test_send_receipt_handles_missing_email" in act_final["reproducer_test"]
    assert "demo_app/tasks.py" in act_final["patched_files"]
    assert "def send_receipt" in act_final["patched_files"]["demo_app/tasks.py"]

    # Fenced code inside tags
    fenced_final = (
        "<final>\n"
        "<root_cause>bug</root_cause>\n"
        "<evidence>- ev1</evidence>\n"
        "<confidence>high</confidence>\n"
        "<reproducer_test>\n```python\ndef test_foo(): pass\n```\n</reproducer_test>\n"
        '<patched_file path="foo.py">\n```python\n# code\n```\n</patched_file>\n'
        "</final>"
    )
    act_fenced = parse_action(fenced_final)
    assert "```" not in act_fenced["reproducer_test"]
    assert "```" not in act_fenced["patched_files"]["foo.py"]

    # Give up from canned_giveup_3.txt
    giveup_text = (fixtures_dir / "canned_giveup_3.txt").read_text(encoding="utf-8")
    act_giveup = parse_action(giveup_text)
    assert act_giveup["kind"] == "give_up"
    assert "TimeoutError" in act_giveup["reason"]
    assert len(act_giveup["evidence"]) >= 1

    # Garbage raises ValueError
    with pytest.raises(ValueError):
        parse_action("I am an unformatted response with no JSON or tags.")


def test_agent_tool_then_final(tmp_path):
    repo_root = Path.cwd().resolve()
    failure = json.loads((repo_root / "tests/fixtures/failure_1.json").read_text(encoding="utf-8"))
    canned_final = (repo_root / "tests/fixtures/canned_final_1.txt").read_text(encoding="utf-8")

    responses = [
        ('{"action": "read_file", "args": {"path": "demo_app/tasks.py"}}', "test-model"),
        (canned_final, "test-model"),
    ]
    call_count = 0

    def fake_chat(messages):
        nonlocal call_count
        resp = responses[call_count]
        call_count += 1
        return resp

    result = run_agent(failure, repo_root=repo_root, chat_fn=fake_chat)
    assert result.kind == "final"
    assert len(result.trace) == 2
    assert result.trace[0]["type"] == "tool"
    assert result.trace[1]["type"] == "final"


def test_agent_one_garbage_then_valid(tmp_path):
    repo_root = Path.cwd().resolve()
    failure = json.loads((repo_root / "tests/fixtures/failure_1.json").read_text(encoding="utf-8"))
    canned_final = (repo_root / "tests/fixtures/canned_final_1.txt").read_text(encoding="utf-8")

    responses = [
        ("Some garbage that fails to parse", "test-model"),
        (canned_final, "test-model"),
    ]
    idx = 0

    def fake_chat(messages):
        nonlocal idx
        resp = responses[idx]
        idx += 1
        return resp

    result = run_agent(failure, repo_root=repo_root, chat_fn=fake_chat)
    assert result.kind == "final"
    assert result.trace[0]["type"] == "parse_error"
    assert result.trace[1]["type"] == "final"


def test_agent_two_garbage_replies_error(tmp_path):
    repo_root = Path.cwd().resolve()
    failure = json.loads((repo_root / "tests/fixtures/failure_1.json").read_text(encoding="utf-8"))

    def fake_chat(messages):
        return "Garbage response", "test-model"

    result = run_agent(failure, repo_root=repo_root, chat_fn=fake_chat)
    assert result.kind == "error"
    assert "parse error" in result.payload["reason"].lower()


def test_agent_does_not_converge(tmp_path):
    repo_root = Path.cwd().resolve()
    failure = json.loads((repo_root / "tests/fixtures/failure_1.json").read_text(encoding="utf-8"))

    def fake_chat(messages):
        return '{"action": "read_file", "args": {"path": "demo_app/tasks.py"}}', "test-model"

    result = run_agent(failure, repo_root=repo_root, chat_fn=fake_chat, max_steps=3)
    assert result.kind == "error"
    assert "did not converge" in result.payload["reason"]


def test_agent_chat_fn_raises(tmp_path):
    repo_root = Path.cwd().resolve()
    failure = json.loads((repo_root / "tests/fixtures/failure_1.json").read_text(encoding="utf-8"))

    def fake_chat(messages):
        raise ConnectionError("API offline")

    result = run_agent(failure, repo_root=repo_root, chat_fn=fake_chat)
    assert result.kind == "error"
    assert "API offline" in result.payload["reason"]
    assert result.trace[0]["type"] == "llm_error"


def test_build_first_message():
    repo_root = Path.cwd().resolve()
    failure = json.loads((repo_root / "tests/fixtures/failure_1.json").read_text(encoding="utf-8"))

    msg = build_first_message(failure, repo_root=repo_root, tests_dir="demo_app/tests")
    assert "demo_app.tasks.send_receipt" in msg
    assert "demo_app/tasks.py" in msg
    assert "def send_receipt" in msg
    assert "test_tasks.py" in msg


def test_end_to_end_fake_agent_and_verify():
    repo_root = Path.cwd().resolve()
    failure = json.loads((repo_root / "tests/fixtures/failure_1.json").read_text(encoding="utf-8"))
    canned_final = (repo_root / "tests/fixtures/canned_final_1.txt").read_text(encoding="utf-8")

    def fake_chat(messages):
        return canned_final, "test-model"

    agent_res = run_agent(failure, repo_root=repo_root, chat_fn=fake_chat)
    assert agent_res.kind == "final"

    verify_res = verify(failure, agent_res.payload, repo_root=repo_root, tests_dir="demo_app/tests")
    assert verify_res.status == "VERIFIED"
    assert "+" in verify_res.diff
