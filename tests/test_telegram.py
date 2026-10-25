"""Tests for Telegram human-in-the-loop approval module."""
import json
import pytest
from pathlib import Path

from autoremediate.telegram import (
    send_approval_request,
    handle_telegram_callback,
    listen_for_approvals,
)


def test_send_approval_request(monkeypatch):
    calls = []

    def fake_call_telegram(method, payload, token):
        calls.append({"method": method, "payload": payload, "token": token})
        return {"ok": True, "result": {"message_id": 42}}

    monkeypatch.setattr("autoremediate.telegram._call_telegram_api", fake_call_telegram)

    failure = {
        "id": "1",
        "task_name": "demo_app.tasks.send_receipt",
        "exception_type": "AttributeError",
        "exception_message": "'NoneType' has no attribute 'lower'",
    }
    final = {
        "root_cause": "Email is None.",
        "confidence": "high",
    }
    diff = "--- demo_app/tasks.py\n+++ demo_app/tasks.py\n@@ -11 +11 @@"

    ok = send_approval_request(
        failure=failure,
        final=final,
        diff=diff,
        bot_token="test_bot_token",
        chat_id="12345678",
    )

    assert ok is True
    assert len(calls) == 1
    call = calls[0]
    assert call["method"] == "sendMessage"
    assert call["payload"]["chat_id"] == "12345678"
    assert "apply:1" in json.dumps(call["payload"]["reply_markup"])
    assert "reject:1" in json.dumps(call["payload"]["reply_markup"])
    assert "Email is None." in call["payload"]["text"]


def test_handle_telegram_callback_apply(tmp_path, monkeypatch):
    # Setup temporary repo and recording
    repo_file = tmp_path / "demo_app" / "tasks.py"
    repo_file.parent.mkdir(parents=True)
    repo_file.write_text("old_content = True\n", encoding="utf-8")

    rec_dir = tmp_path / "recordings"
    rec_dir.mkdir()
    rec_file = rec_dir / "run_1.json"
    rec_data = {
        "failure_id": "1",
        "final": {
            "patched_files": {
                "demo_app/tasks.py": "new_patched_content = True\n",
            }
        },
    }
    rec_file.write_text(json.dumps(rec_data), encoding="utf-8")
    monkeypatch.setenv("RECORDINGS_DIR", str(rec_dir))

    api_calls = []

    def fake_call_telegram(method, payload, token):
        api_calls.append({"method": method, "payload": payload})
        return {"ok": True}

    monkeypatch.setattr("autoremediate.telegram._call_telegram_api", fake_call_telegram)

    callback_query = {
        "id": "cb_999",
        "data": "apply:1",
        "message": {
            "message_id": 100,
            "chat": {"id": 123456},
            "text": "Approval request for #1",
        },
    }

    res = handle_telegram_callback(callback_query, repo_root=tmp_path, bot_token="fake_token")
    assert "demo_app/tasks.py" in res
    assert repo_file.read_text(encoding="utf-8") == "new_patched_content = True\n"

    # Assert Telegram notifications sent
    methods = [c["method"] for c in api_calls]
    assert "answerCallbackQuery" in methods
    assert "editMessageText" in methods


def test_handle_telegram_callback_reject(tmp_path, monkeypatch):
    api_calls = []

    def fake_call_telegram(method, payload, token):
        api_calls.append({"method": method, "payload": payload})
        return {"ok": True}

    monkeypatch.setattr("autoremediate.telegram._call_telegram_api", fake_call_telegram)

    callback_query = {
        "id": "cb_999",
        "data": "reject:1",
        "message": {
            "message_id": 100,
            "chat": {"id": 123456},
            "text": "Approval request for #1",
        },
    }

    res = handle_telegram_callback(callback_query, repo_root=tmp_path, bot_token="fake_token")
    assert "Rejected #1" in res

    methods = [c["method"] for c in api_calls]
    assert "answerCallbackQuery" in methods
    assert "editMessageText" in methods
    edit_call = next(c for c in api_calls if c["method"] == "editMessageText")
    assert "REJECTED" in edit_call["payload"]["text"]


def test_telegram_missing_env(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    # Should safely return False without raising
    ok = send_approval_request(
        failure={"id": "1"},
        final={},
        bot_token=None,
        chat_id=None,
    )
    assert ok is False



def test_send_and_wait_for_approval_inline(tmp_path, monkeypatch):
    from autoremediate.telegram import send_and_wait_for_approval

    repo_file = tmp_path / "demo_app" / "tasks.py"
    repo_file.parent.mkdir(parents=True)
    repo_file.write_text("old = True\n", encoding="utf-8")

    api_calls = []
    poll_count = 0

    def fake_call(method, payload, token):
        nonlocal poll_count
        api_calls.append(method)
        if method == "getUpdates":
            poll_count += 1
            if poll_count >= 2:
                # Return simulated button click
                return {
                    "ok": True,
                    "result": [
                        {
                            "update_id": 10,
                            "callback_query": {
                                "id": "cb_inline",
                                "data": "apply:1",
                                "message": {"message_id": 50, "chat": {"id": 123}, "text": "Approve #1"},
                            },
                        }
                    ],
                }
            return {"ok": True, "result": []}
        return {"ok": True, "result": {"message_id": 50}}

    monkeypatch.setattr("autoremediate.telegram._call_telegram_api", fake_call)

    failure = {"id": "1", "task_name": "demo_app.tasks.send_receipt"}
    final = {"patched_files": {"demo_app/tasks.py": "patched = True\n"}}

    approved = send_and_wait_for_approval(
        failure=failure,
        final=final,
        diff="--- a\n+++ b",
        repo_root=tmp_path,
        bot_token="fake_token",
        chat_id="fake_chat",
        timeout=5,
    )

    assert approved is True
    assert repo_file.read_text(encoding="utf-8") == "patched = True\n"

