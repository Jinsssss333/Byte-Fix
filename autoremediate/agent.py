"""Agent loop and message parsing for Auto-Remediate."""
import re
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from autoremediate.prompts import SYSTEM_PROMPT, FIRST_MESSAGE_TEMPLATE, CORRECTION_MESSAGE
from autoremediate.tools import run_tool
from autoremediate import llm


@dataclass
class AgentResult:
    kind: str  # "final" | "give_up" | "error"
    payload: dict  # output of parse_action for final/give_up, or {"reason": str} for error
    trace: list[dict]  # one entry per step: {"step": int, "type": "tool"|"final"|"give_up"|"parse_error"|"llm_error", "detail": str, "model": str}


def _strip_fences(content: str) -> str:
    """Strip markdown code fences (``` or ```python) from content."""
    lines = content.strip().splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    cleaned = "\n".join(lines).strip()
    return cleaned + "\n" if cleaned else ""


def _find_balanced_json(text: str) -> str | None:
    """Find first balanced JSON object {...} in text."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    return None


def parse_action(text: str) -> dict[str, Any]:
    """Parse model reply into tool call, final answer, or give_up dict.

    Raises ValueError if unparseable or incomplete.
    """
    if "<final>" in text and "</final>" in text:
        final_block = re.search(r"<final>(.*?)</final>", text, re.DOTALL)
        if not final_block:
            raise ValueError("Malformed <final> block")
        inner = final_block.group(1)

        root_cause_m = re.search(r"<root_cause>(.*?)</root_cause>", inner, re.DOTALL)
        root_cause = root_cause_m.group(1).strip() if root_cause_m else ""

        evidence_m = re.search(r"<evidence>(.*?)</evidence>", inner, re.DOTALL)
        evidence = []
        if evidence_m:
            for line in evidence_m.group(1).splitlines():
                line = line.strip()
                if line.startswith("-"):
                    evidence.append(line.lstrip("-").strip())

        confidence_m = re.search(r"<confidence>(.*?)</confidence>", inner, re.DOTALL)
        confidence = confidence_m.group(1).strip() if confidence_m else "medium"

        repro_m = re.search(r"<reproducer_test>(.*?)</reproducer_test>", inner, re.DOTALL)
        if not repro_m or not repro_m.group(1).strip():
            raise ValueError("Final answer missing <reproducer_test>")
        reproducer_test = _strip_fences(repro_m.group(1))

        patched_matches = list(re.finditer(r'<patched_file\s+path="([^"]+)">(.*?)</patched_file>', inner, re.DOTALL))
        if not patched_matches:
            raise ValueError("Final answer missing <patched_file>")

        patched_files: dict[str, str] = {}
        for pm in patched_matches:
            path = pm.group(1).strip()
            content = _strip_fences(pm.group(2))
            patched_files[path] = content

        return {
            "kind": "final",
            "root_cause": root_cause,
            "evidence": evidence,
            "confidence": confidence,
            "reproducer_test": reproducer_test,
            "patched_files": patched_files,
        }

    if "<give_up>" in text and "</give_up>" in text:
        giveup_block = re.search(r"<give_up>(.*?)</give_up>", text, re.DOTALL)
        if not giveup_block:
            raise ValueError("Malformed <give_up> block")
        inner = giveup_block.group(1)

        reason_m = re.search(r"<reason>(.*?)</reason>", inner, re.DOTALL)
        reason = reason_m.group(1).strip() if reason_m else ""

        evidence_m = re.search(r"<evidence>(.*?)</evidence>", inner, re.DOTALL)
        evidence = []
        if evidence_m:
            for line in evidence_m.group(1).splitlines():
                line = line.strip()
                if line.startswith("-"):
                    evidence.append(line.lstrip("-").strip())

        return {
            "kind": "give_up",
            "reason": reason,
            "evidence": evidence,
        }

    # Attempt tool call parsing
    json_candidate = _find_balanced_json(text)
    if json_candidate:
        try:
            parsed = json.loads(json_candidate)
            if isinstance(parsed, dict) and "action" in parsed:
                action = parsed["action"]
                if action in ("read_file", "grep"):
                    args = parsed.get("args", {})
                    return {
                        "kind": "tool",
                        "action": action,
                        "args": args if isinstance(args, dict) else {},
                    }
        except Exception:
            pass

    raise ValueError("Unparseable response: expected tool call, <final>, or <give_up>")


def build_first_message(failure: dict[str, Any], repo_root: Path, tests_dir: str) -> str:
    """Build the first user prompt populating FIRST_MESSAGE_TEMPLATE."""
    frames = failure.get("frames", [])
    source_path = ""
    frame_locals = ""

    root_resolved = repo_root.resolve()

    if frames:
        last_frame = frames[-1]
        source_path = last_frame.get("file", "")
        locals_dict = last_frame.get("locals", {})
        frame_locals = "\n".join(f"{k} = {v}" for k, v in locals_dict.items())
    else:
        # Fallback to traceback inspection
        tb = failure.get("traceback", "")
        matches = re.findall(r'File "([^"]+\.py)"', tb)
        if matches:
            source_path = matches[0]

    source_text = "(source unavailable)"
    if source_path:
        full_source_path = (root_resolved / source_path).resolve()
        if full_source_path.is_file():
            try:
                lines = full_source_path.read_text(encoding="utf-8", errors="replace").splitlines()
                source_text = "\n".join(lines[:400])
            except Exception:
                pass

    tests_path = (root_resolved / tests_dir).resolve()
    existing_tests_list = []
    if tests_path.is_dir():
        for p in sorted(tests_path.glob("*.py")):
            try:
                existing_tests_list.append(str(p.relative_to(root_resolved)))
            except ValueError:
                existing_tests_list.append(str(p.name))
    existing_tests = "\n".join(existing_tests_list)

    return FIRST_MESSAGE_TEMPLATE.format(
        task_name=failure.get("task_name", ""),
        exception_type=failure.get("exception_type", ""),
        exception_message=failure.get("exception_message", ""),
        args=json.dumps(failure.get("args", [])),
        kwargs=json.dumps(failure.get("kwargs", {})),
        traceback=failure.get("traceback", ""),
        frame_locals=frame_locals,
        source_path=source_path or "(unknown)",
        source_text=source_text,
        existing_tests=existing_tests,
    )


def run_agent(
    failure: dict[str, Any],
    repo_root: Path,
    chat_fn: Callable[[list[dict]], tuple[str, str]] | None = None,
    on_step: Callable[[dict], None] | None = None,
    max_steps: int = 8,
) -> AgentResult:
    """Run the multi-turn agent loop to find root cause and propose a patch."""
    if chat_fn is None:
        chat_fn = llm.chat

    tests_dir = os.getenv("TARGET_TESTS", "demo_app/tests")
    first_message = build_first_message(failure, repo_root=repo_root, tests_dir=tests_dir)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": first_message},
    ]

    trace: list[dict] = []
    parse_error_count = 0
    last_model_label = "unknown"

    for step in range(1, max_steps + 1):
        try:
            reply, model_label = chat_fn(messages)
            last_model_label = model_label
        except Exception as exc:
            entry = {
                "step": step,
                "type": "llm_error",
                "detail": str(exc),
                "model": last_model_label,
            }
            trace.append(entry)
            if on_step:
                on_step(entry)
            return AgentResult(kind="error", payload={"reason": str(exc)}, trace=trace)

        try:
            action = parse_action(reply)
        except ValueError as ve:
            parse_error_count += 1
            entry = {
                "step": step,
                "type": "parse_error",
                "detail": str(ve),
                "model": last_model_label,
            }
            trace.append(entry)
            if on_step:
                on_step(entry)

            if parse_error_count > 1:
                return AgentResult(
                    kind="error",
                    payload={"reason": f"Agent failed with parse error: {ve}"},
                    trace=trace,
                )

            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user", "content": CORRECTION_MESSAGE})
            continue

        if action["kind"] == "tool":
            detail = f"{action['action']}({action.get('args', {})})"
            entry = {
                "step": step,
                "type": "tool",
                "detail": detail,
                "model": last_model_label,
            }
            trace.append(entry)
            if on_step:
                on_step(entry)

            tool_res = run_tool(action["action"], action.get("args", {}), repo_root=repo_root)
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user", "content": "TOOL RESULT:\n" + tool_res})
            continue

        if action["kind"] == "final":
            entry = {
                "step": step,
                "type": "final",
                "detail": action.get("root_cause", ""),
                "model": last_model_label,
            }
            trace.append(entry)
            if on_step:
                on_step(entry)
            return AgentResult(kind="final", payload=action, trace=trace)

        if action["kind"] == "give_up":
            entry = {
                "step": step,
                "type": "give_up",
                "detail": action.get("reason", ""),
                "model": last_model_label,
            }
            trace.append(entry)
            if on_step:
                on_step(entry)
            return AgentResult(kind="give_up", payload=action, trace=trace)

    return AgentResult(
        kind="error",
        payload={"reason": f"agent did not converge in {max_steps} steps"},
        trace=trace,
    )
