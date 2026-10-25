"""Tests for Sentry adapter and webhook ingestion."""
import json
import threading
import urllib.request
from http.server import HTTPServer
from pathlib import Path
from autoremediate.sentry import normalize_sentry_event, create_sentry_webhook_handler
from autoremediate.hook import load_failure


def test_normalize_sentry_event(tmp_path):
    fixture_path = Path("tests/fixtures/sentry_event.json")
    event_data = json.loads(fixture_path.read_text(encoding="utf-8"))

    saved_file = normalize_sentry_event(event_data, failures_dir=tmp_path)
    assert saved_file.exists()
    assert saved_file.name == "1.json"

    failure = load_failure("1", failures_dir=tmp_path)
    assert failure["id"] == "1"
    assert failure["task_name"] == "demo_app.tasks.send_receipt"
    assert failure["exception_type"] == "AttributeError"
    assert "'NoneType'" in failure["exception_message"]

    # Check frame filtering
    frame_files = [f["file"] for f in failure["frames"]]
    assert any("demo_app/tasks.py" in f for f in frame_files)
    assert not any("site-packages" in f for f in frame_files)
    assert not any("celery" in f for f in frame_files)

    # Check redaction of secrets in Sentry payload args and locals
    order_arg = failure["args"][0]
    assert order_arg["customer"]["password"] == "[REDACTED]"

    task_frame = next(f for f in failure["frames"] if "demo_app/tasks.py" in f["file"])
    assert "[REDACTED]" in task_frame["locals"]["order"]


def test_sentry_webhook_http_server(tmp_path):
    fixture_path = Path("tests/fixtures/sentry_event.json")
    payload_bytes = fixture_path.read_bytes()

    received_ids = []

    def on_event(fid):
        received_ids.append(fid)

    handler_cls = create_sentry_webhook_handler(failures_dir=tmp_path, on_event_callback=on_event)
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        url = f"http://127.0.0.1:{port}/webhook/sentry"
        req = urllib.request.Request(
            url,
            data=payload_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "ok"
            assert data["failure_id"] == "1"

        assert received_ids == ["1"]
        assert (tmp_path / "1.json").exists()
    finally:
        server.shutdown()
        server.server_close()
