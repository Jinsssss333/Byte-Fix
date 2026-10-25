"""Tests for autoremediate.hook."""
import pytest
from pathlib import Path
from autoremediate.hook import (
    redact,
    capture_failure,
    on_task_failure,
    load_failure,
    list_failures,
    install,
)
from demo_app.tasks import send_receipt


def test_redact_secrets_and_pii(monkeypatch):
    monkeypatch.setenv("REDACT_PII", "1")

    # Leaves None and numbers alone
    assert redact(None) is None
    assert redact(123) == 123
    assert redact(45.67) == 45.67
    assert redact(True) is True

    # Masks password/token keys at depth
    data = {
        "user": "john",
        "password": "supersecret123",
        "nested": {"api_key": "xyz1234", "token": "secret_token"},
    }
    redacted = redact(data)
    assert redacted["password"] == "[REDACTED]"
    assert redacted["nested"]["api_key"] == "[REDACTED]"
    assert redacted["nested"]["token"] == "[REDACTED]"
    assert redacted["user"] == "john"

    # Masks emails and 13-16 digit numbers inside strings
    pii_data = {
        "text": "contact me at alice@example.com or card 1234567890123456 please",
        "short_num": 12345,
    }
    masked = redact(pii_data)
    assert "[REDACTED_EMAIL]" in masked["text"]
    assert "[REDACTED_NUMBER]" in masked["text"]
    assert masked["short_num"] == 12345

    # When REDACT_PII is 0, string emails are not masked
    monkeypatch.setenv("REDACT_PII", "0")
    assert redact("alice@example.com") == "alice@example.com"


def test_capture_failure_and_frames(tmp_path):
    order_with_none_email = {
        "id": 42,
        "customer": {"name": "Asha Rao", "email": None},
        "items": [{"price": 10.0, "qty": 2}],
    }

    try:
        send_receipt.run(order_with_none_email)
    except AttributeError as exc:
        saved_path = capture_failure(
            exc=exc,
            task_name="demo_app.tasks.send_receipt",
            args=[order_with_none_email],
            kwargs={},
            task_id="test-task-1",
            failures_dir=tmp_path,
        )

    assert saved_path == tmp_path / "1.json"
    assert saved_path.exists()

    data = load_failure("1", failures_dir=tmp_path)
    assert data["id"] == "1"
    assert data["exception_type"] == "AttributeError"
    assert "lower" in data["exception_message"]

    # Frame assertions
    frame_files = [f["file"] for f in data["frames"]]
    assert any("demo_app/tasks.py" in f for f in frame_files)
    assert not any("site-packages" in f for f in frame_files)
    assert not any(".venv" in f for f in frame_files)

    task_frame = next(f for f in data["frames"] if "demo_app/tasks.py" in f["file"])
    assert "order" in task_frame["locals"]
    assert "Asha Rao" in task_frame["locals"]["order"]


def test_ids_increment(tmp_path):
    exc = ValueError("first crash")
    p1 = capture_failure(exc, "task1", [], {}, failures_dir=tmp_path)
    p2 = capture_failure(exc, "task2", [], {}, failures_dir=tmp_path)

    assert p1.name == "1.json"
    assert p2.name == "2.json"

    failures = list_failures(failures_dir=tmp_path)
    assert len(failures) == 2
    assert failures[0]["id"] == "2"  # Newest first
    assert failures[1]["id"] == "1"


def test_load_failure_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_failure("999", failures_dir=tmp_path)


def test_on_task_failure_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("FAILURES_DIR", str(tmp_path))
    # Calling with None or garbage does not raise
    on_task_failure(sender=None, exception=None)
    on_task_failure(sender="bad_sender", exception=Exception("oops"), args=object())

    # install is idempotent
    install()
    install()
