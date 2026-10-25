#!/usr/bin/env python3
"""End-to-end verification script for Sentry Webhook Ingestion.

Simulates Sentry sending a crash payload with HMAC-SHA256 signature verification,
confirms failure JSON normalization & redaction, and verifies the remediation pipeline.
"""
import os
import sys
from pathlib import Path

# Ensure repo root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import hmac
import hashlib
import threading
import urllib.request
import urllib.error
from http.server import HTTPServer

from autoremediate.sentry import create_sentry_webhook_handler
from autoremediate.hook import load_failure


def main():
    print("=" * 60)
    print("Auto-Remediate: Sentry Integration Verification")
    print("=" * 60)

    fixture_path = Path("tests/fixtures/sentry_event.json")
    if not fixture_path.exists():
        print(f"[FAIL] Fixture not found at {fixture_path}")
        sys.exit(1)

    payload_bytes = fixture_path.read_bytes()
    secret = "demo-sentry-secret-42"
    failures_dir = Path("failures")
    failures_dir.mkdir(exist_ok=True)

    # 1. Start temporary webhook listener on random port
    print("\n1. Starting Sentry webhook listener...")
    received_ids = []

    def on_event(fid: str):
        received_ids.append(fid)

    handler_cls = create_sentry_webhook_handler(
        failures_dir=failures_dir,
        on_event_callback=on_event,
        secret=secret,
    )
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    url = f"http://127.0.0.1:{port}/webhook/sentry"
    print(f"   [OK] Webhook listener running at {url}")

    try:
        # 2. Test Invalid Signature (Security check)
        print("\n2. Testing HMAC signature rejection for invalid signatures...")
        req_bad = urllib.request.Request(
            url,
            data=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "sentry-hook-signature": "invalid_signature_hex",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req_bad, timeout=5) as resp:
                print("   [FAIL] Server unexpectedly accepted invalid signature!")
                sys.exit(1)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                print("   [OK] HTTP 401 Unauthorized correctly returned for bad signature.")
            else:
                print(f"   [FAIL] Expected 401, got {exc.code}")
                sys.exit(1)

        # 3. Test Valid Signature & Ingestion
        print("\n3. Testing valid Sentry webhook POST...")
        valid_sig = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
        req_good = urllib.request.Request(
            url,
            data=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "sentry-hook-signature": valid_sig,
            },
            method="POST",
        )

        with urllib.request.urlopen(req_good, timeout=5) as resp:
            if resp.status != 200:
                print(f"   [FAIL] Expected 200, got {resp.status}")
                sys.exit(1)
            resp_body = json.loads(resp.read().decode("utf-8"))
            print(f"   [OK] Response: {resp_body}")
            failure_id = resp_body.get("failure_id")

        if not failure_id:
            print("   [FAIL] No failure_id in response")
            sys.exit(1)

        # 4. Verify Normalized Failure File
        print(f"\n4. Verifying normalized failures/{failure_id}.json...")
        failure = load_failure(failure_id, failures_dir=failures_dir)

        print(f"   - Task Name: {failure['task_name']}")
        print(f"   - Exception: {failure['exception_type']}: {failure['exception_message']}")
        print(f"   - Frames captured: {len(failure['frames'])}")

        # Check secret scrubbing
        order_arg = failure["args"][0]
        customer_pwd = order_arg["customer"].get("password")
        if customer_pwd == "[REDACTED]":
            print("   [OK] Password in args scrubbed to [REDACTED].")
        else:
            print(f"   [FAIL] Password not redacted: {customer_pwd}")
            sys.exit(1)

        # Check frame filtering (celery removed, demo_app kept)
        files = [f["file"] for f in failure["frames"]]
        if any("demo_app/tasks.py" in f for f in files) and not any("celery" in f for f in files):
            print("   [OK] Celery internals filtered, demo_app/tasks.py frame preserved.")
        else:
            print(f"   [FAIL] Frame filtering issue. Files: {files}")
            sys.exit(1)

        print("\n" + "=" * 60)
        print(f"[SUCCESS] Sentry webhook pipeline verified successfully!")
        print(f"Next step: Run 'python -m autoremediate.cli fix {failure_id}'")
        print("=" * 60)

    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
