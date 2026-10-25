#!/usr/bin/env python3
"""Send a real Celery task crash event to your live Sentry.io project.

Reads SENTRY_DSN from .env or accepts --dsn.
"""
import os
import sys
import argparse
from pathlib import Path

# Ensure repo root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration

load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="Trigger live crash to Sentry.io")
    parser.add_argument("--dsn", default=None, help="Sentry DSN (defaults to SENTRY_DSN in .env)")
    args = parser.parse_args()

    dsn = args.dsn or os.getenv("SENTRY_DSN")
    if not dsn:
        print("[ERROR] No Sentry DSN provided. Add SENTRY_DSN to .env or pass --dsn <URL>")
        sys.exit(1)

    print(f"[*] Initializing Sentry SDK with DSN: {dsn[:25]}...")
    sentry_sdk.init(
        dsn=dsn,
        traces_sample_rate=1.0,
        environment="hackathon-demo",
        release="auto-remediate-1.0.0",
        integrations=[CeleryIntegration()],
    )

    from demo_app.tasks import send_receipt

    # Buggy order: customer email is None, password included for redaction test
    buggy_order = {
        "id": 42,
        "customer": {
            "name": "Asha Rao",
            "email": None,
            "password": "supersecretpassword123",
        },
        "items": [
            {"price": 10.0, "qty": 2},
            {"price": 4.5, "qty": 1},
        ],
    }

    print("[*] Simulating crashed task execution: demo_app.tasks.send_receipt...")
    try:
        # Call the task with invalid data to raise AttributeError
        send_receipt.run(buggy_order)
    except Exception as exc:
        print(f"[*] Caught expected exception: {type(exc).__name__}: {exc}")
        print("[*] Capturing exception to Sentry...")
        event_id = sentry_sdk.capture_exception(exc)
        sentry_sdk.flush(timeout=5)
        print(f"[SUCCESS] Event sent to Sentry! Event ID: {event_id}")
        print("Check your Sentry dashboard at: https://sentry.io/issues/")


if __name__ == "__main__":
    main()
