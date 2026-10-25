"""Sentry Webhook Ingestion Adapter & Normalizer for Auto-Remediate.

Converts Sentry error/issue payloads into standard failures/<id>.json schema.
"""
import os
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable
from datetime import datetime, timezone

from autoremediate.hook import redact, _to_json_safe


def normalize_sentry_event(event_payload: dict[str, Any], failures_dir: Path | str | None = None) -> Path:
    """Transform Sentry webhook event JSON into standard failures/<id>.json format."""
    # Sentry webhooks may wrap the event in {"data": {"event": ...}}
    event = event_payload.get("data", {}).get("event", event_payload)
    if "event" in event_payload:
        event = event_payload["event"]

    # Extract exception details
    exc_values = event.get("exception", {}).get("values", [])
    exc_type = "Exception"
    exc_msg = ""
    raw_frames = []
    if exc_values:
        first_exc = exc_values[0]
        exc_type = first_exc.get("type", "Exception")
        exc_msg = first_exc.get("value", "")
        raw_frames = first_exc.get("stacktrace", {}).get("frames", [])

    # Filter frames (exclude site-packages, celery internals, venvs)
    frames = []
    tb_lines = ["Traceback (from Sentry):\n"]
    for f in raw_frames:
        fn = f.get("filename", "")
        abs_p = f.get("abs_path", "")
        check_path = (abs_p or fn).lower()

        if "site-packages" in check_path or ".venv" in check_path or "celery" in check_path:
            continue

        rel_file = fn
        # If absolute path was given, strip leading /app/ or similar docker prefixes
        if "/demo_app/" in abs_p:
            rel_file = "demo_app/" + abs_p.split("/demo_app/", 1)[1]
        elif rel_file.startswith("/"):
            p = Path(rel_file)
            rel_file = str(Path(*p.parts[-2:]))

        lineno = f.get("lineno", 0)
        func = f.get("function", "")
        code = (f.get("context_line") or "").strip()

        # Locals extraction & redaction
        raw_vars = f.get("vars", {})
        frame_locals = {}
        for k, v in raw_vars.items():
            safe_v = redact(v, key=str(k))
            frame_locals[str(k)] = repr(safe_v)[:300]

        frames.append({
            "file": rel_file,
            "line": lineno,
            "function": func,
            "code": code,
            "locals": frame_locals,
        })
        tb_lines.append(f'  File "{rel_file}", line {lineno}, in {func}\n    {code}\n')

    tb_lines.append(f"{exc_type}: {exc_msg}\n")
    traceback_str = "".join(tb_lines)

    # Extract task details
    task_name = event.get("transaction") or ""
    if not task_name:
        for tag in event.get("tags", []):
            if isinstance(tag, (list, tuple)) and len(tag) >= 2 and tag[0] == "task_name":
                task_name = tag[1]
                break

    task_id = event.get("contexts", {}).get("celery", {}).get("task_id", "")
    extra = event.get("extra", {})
    raw_args = extra.get("args", [])
    raw_kwargs = extra.get("kwargs", {})

    clean_args = _to_json_safe(redact(raw_args))
    clean_kwargs = _to_json_safe(redact(raw_kwargs))

    # Allocate next incremental failure ID
    dir_path = Path(failures_dir or os.getenv("FAILURES_DIR", "failures")).resolve()
    dir_path.mkdir(parents=True, exist_ok=True)

    existing_ids = []
    for p in dir_path.glob("*.json"):
        if p.stem.isdigit():
            existing_ids.append(int(p.stem))
    next_id = max(existing_ids, default=0) + 1
    id_str = str(next_id)

    target_file = dir_path / f"{id_str}.json"

    failure_record = {
        "id": id_str,
        "timestamp": event.get("timestamp") or datetime.now(timezone.utc).isoformat(),
        "task_name": task_name or "unknown_task",
        "task_id": str(task_id),
        "args": clean_args if isinstance(clean_args, list) else [clean_args],
        "kwargs": clean_kwargs if isinstance(clean_kwargs, dict) else {},
        "exception_type": exc_type,
        "exception_message": exc_msg,
        "traceback": traceback_str,
        "frames": frames,
        "git_sha": event.get("release", "unknown"),
    }

    target_file.write_text(json.dumps(failure_record, indent=2), encoding="utf-8")
    return target_file


def create_sentry_webhook_handler(
    failures_dir: Path | str | None = None,
    on_event_callback: Callable[[str], None] | None = None,
):
    """Create an HTTP request handler class to ingest Sentry webhooks."""
    class SentryHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len).decode("utf-8")
            try:
                payload = json.loads(body)
                saved_path = normalize_sentry_event(payload, failures_dir=failures_dir)
                failure_id = saved_path.stem

                if on_event_callback:
                    on_event_callback(failure_id)

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                resp = json.dumps({"status": "ok", "failure_id": failure_id})
                self.wfile.write(resp.encode("utf-8"))
            except Exception as exc:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                resp = json.dumps({"status": "error", "message": str(exc)})
                self.wfile.write(resp.encode("utf-8"))

        def log_message(self, format, *args):
            # Suppress default noisy access logging
            pass

    return SentryHandler
