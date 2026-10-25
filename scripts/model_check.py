"""T1: Model gate script to check model connectivity and output formatting."""
import os
import sys
import re
import json
from pathlib import Path

# Ensure repo root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from openai import OpenAI

from autoremediate.prompts import SYSTEM_PROMPT, FIRST_MESSAGE_TEMPLATE

load_dotenv()


def main():
    base_url = os.getenv("LLM_BASE_URL")
    model = os.getenv("LLM_MODEL")
    api_key = os.getenv("LLM_API_KEY")

    if not base_url or not model or not api_key:
        print("LLM not configured: set LLM_BASE_URL, LLM_MODEL, LLM_API_KEY in .env")
        return

    # Load failure fixture
    fixture_path = Path("tests/fixtures/failure_1.json")
    failure = json.loads(fixture_path.read_text(encoding="utf-8"))

    # Load real tasks.py content
    tasks_source = Path("demo_app/tasks.py").read_text(encoding="utf-8")

    # Format frame locals
    innermost_frame = failure["frames"][-1]
    frame_locals = "\n".join(f"{k} = {v}" for k, v in innermost_frame.get("locals", {}).items())

    # Format existing tests
    test_files = [str(p) for p in Path("demo_app/tests").glob("*.py")]
    existing_tests = "\n".join(test_files)

    user_message = FIRST_MESSAGE_TEMPLATE.format(
        task_name=failure.get("task_name", ""),
        exception_type=failure.get("exception_type", ""),
        exception_message=failure.get("exception_message", ""),
        args=json.dumps(failure.get("args", [])),
        kwargs=json.dumps(failure.get("kwargs", {})),
        traceback=failure.get("traceback", ""),
        frame_locals=frame_locals,
        source_path="demo_app/tasks.py",
        source_text=tasks_source,
        existing_tests=existing_tests,
    )

    client = OpenAI(base_url=base_url, api_key=api_key, timeout=60)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        temperature=0.1,
    )

    reply = response.choices[0].message.content or ""
    print("--- RAW REPLY ---")
    print(reply)
    print("--- VERDICT ---")

    has_final = "<final>" in reply and "</final>" in reply
    has_reproducer = "<reproducer_test>" in reply
    has_patch = bool(re.search(r'<patched_file\s+path="[^"]+">', reply))

    if has_final and has_reproducer and has_patch:
        print("VERDICT: Valid <final> format with reproducer and patch.")
    elif reply.strip().startswith("{") and reply.strip().endswith("}"):
        try:
            parsed = json.loads(reply.strip())
            if "action" in parsed:
                print(f"VERDICT: Tool call -> action: {parsed['action']}")
            else:
                print("VERDICT: JSON without action field.")
        except Exception:
            print("VERDICT: Malformed JSON.")
    else:
        print("VERDICT: Neither valid final nor tool call.")


if __name__ == "__main__":
    main()
