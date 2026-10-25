"""Telegram Bot Integration for Human-in-the-Loop Patch Approval.

Sends rich failure & diff summaries with inline approval buttons to Telegram.
When approved, automatically applies the verified patch to the repository.
Uses standard library urllib (zero extra dependencies).
"""
import os
import json
import logging
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

from autoremediate.verify import apply_patch_to_repo

logger = logging.getLogger(__name__)


def _call_telegram_api(method: str, payload: dict[str, Any], bot_token: str) -> dict[str, Any]:
    """Call Telegram Bot API via HTTP POST."""
    url = f"https://api.telegram.org/bot{bot_token}/{method}"
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data_bytes,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send_approval_request(
    failure: dict[str, Any],
    final: dict[str, Any],
    diff: str = "",
    bot_token: str | None = None,
    chat_id: str | None = None,
) -> bool:
    """Send an approval request with inline buttons to Telegram."""
    token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
    cid = chat_id or os.getenv("TELEGRAM_CHAT_ID")

    if not token or not cid:
        logger.warning("Telegram token or chat_id not set in environment.")
        return False

    failure_id = str(failure.get("id", "1"))
    task_name = failure.get("task_name", "unknown_task")
    exc_type = failure.get("exception_type", "Exception")
    exc_msg = failure.get("exception_message", "")
    root_cause = final.get("root_cause", "No root cause provided.")
    confidence = final.get("confidence", "unknown")

    import html

    # Format truncated diff
    diff_snippet = diff[:1500] if diff else "No diff available."
    if len(diff) > 1500:
        diff_snippet += "\n... [diff truncated]"

    # Extract old error line from innermost frame
    frames = failure.get("frames", [])
    last_frame = frames[-1] if frames else {}
    err_file = last_frame.get("file", "")
    err_line = last_frame.get("line", "")
    err_code = last_frame.get("code", "").strip()

    # Extract replaced lines (-) and added lines (+) from diff
    replaced_lines = []
    added_lines = []
    if diff:
        for line in diff.splitlines():
            if line.startswith("-") and not line.startswith("---"):
                replaced_lines.append(line[1:].strip())
            elif line.startswith("+") and not line.startswith("+++"):
                added_lines.append(line[1:].strip())

    old_code_display = html.escape(err_code or ("\n".join(replaced_lines) if replaced_lines else "N/A"))
    new_code_display = html.escape("\n".join(added_lines) if added_lines else "See patch below")
    diff_escaped = html.escape(diff_snippet)

    loc_str = f" in <code>{err_file}:{err_line}</code>" if err_file else ""

    text = (
        f"🚨 <b>Auto-Remediate Approval: #{failure_id}</b>\n\n"
        f"<b>Task:</b> <code>{task_name}</code>{loc_str}\n"
        f"<b>Error:</b> <code>{exc_type}: {exc_msg}</code>\n"
        f"<b>Confidence:</b> {confidence.upper()}\n\n"
        f"🔴 <b>Old (Crashing Code Line):</b>\n"
        f"<pre>{old_code_display}</pre>\n\n"
        f"🟢 <b>New (Patched Code Line):</b>\n"
        f"<pre>{new_code_display}</pre>\n\n"
        f"🔍 <b>Root Cause:</b>\n"
        f"{html.escape(root_cause)}\n\n"
        f"📝 <b>Unified Diff:</b>\n"
        f"<pre>{diff_escaped}</pre>\n\n"
        f"<i>Review the fix above. Tap <b>Approve</b> to apply it to your files.</i>"
    )

    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "✅ Approve & Apply Patch", "callback_data": f"apply:{failure_id}"},
                {"text": "❌ Reject", "callback_data": f"reject:{failure_id}"},
            ]
        ]
    }

    payload = {
        "chat_id": cid,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": reply_markup,
    }

    try:
        res = _call_telegram_api("sendMessage", payload, token)
        return res.get("ok", False)
    except Exception as exc:
        logger.error(f"Failed to send Telegram message: {exc}")
        return False


def handle_telegram_callback(
    callback_query: dict[str, Any],
    repo_root: Path,
    bot_token: str,
    final: dict[str, Any] | None = None,
) -> str:
    """Handle approval or rejection callback from Telegram."""
    cb_id = callback_query.get("id")
    data = callback_query.get("data", "")
    message = callback_query.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    msg_id = message.get("message_id")

    if not data:
        return "No action data."

    action, _, failure_id = data.partition(":")

    if action == "apply":
        # Load verified final payload (from memory or recording)
        patch_final = final
        if not patch_final:
            rec_dir = Path(os.getenv("RECORDINGS_DIR", "recordings"))
            rec_file = rec_dir / f"run_{failure_id}.json"
            if rec_file.exists():
                try:
                    rec_data = json.loads(rec_file.read_text(encoding="utf-8"))
                    patch_final = rec_data.get("final", {})
                except Exception:
                    pass

        if not patch_final:
            feedback = f"⚠️ Could not load patch for failure #{failure_id}."
            _call_telegram_api("answerCallbackQuery", {"callback_query_id": cb_id, "text": feedback}, bot_token)
            return feedback

        try:
            applied_files = apply_patch_to_repo(patch_final, repo_root)

            toast = f"✅ Applied patch to {len(applied_files)} file(s)!"
            _call_telegram_api("answerCallbackQuery", {"callback_query_id": cb_id, "text": toast}, bot_token)

            # Update message to show approved state
            files_list = ", ".join(applied_files) or "codebase"
            orig_text = message.get("text", "")
            updated_text = (
                f"{orig_text}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ <b>APPROVED & APPLIED</b> by human reviewer.\n"
                f"Files modified: <code>{files_list}</code>"
            )
            _call_telegram_api(
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": msg_id,
                    "text": updated_text,
                    "parse_mode": "HTML",
                },
                bot_token,
            )
            return f"Applied to {files_list}"
        except Exception as exc:
            err_msg = f"Failed to apply patch: {exc}"
            _call_telegram_api("answerCallbackQuery", {"callback_query_id": cb_id, "text": err_msg}, bot_token)
            return err_msg

    elif action == "reject":
        toast = f"❌ Patch for failure #{failure_id} rejected."
        _call_telegram_api("answerCallbackQuery", {"callback_query_id": cb_id, "text": toast}, bot_token)

        orig_text = message.get("text", "")
        updated_text = (
            f"{orig_text}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"❌ <b>REJECTED</b> by human reviewer."
        )
        _call_telegram_api(
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": msg_id,
                "text": updated_text,
                "parse_mode": "HTML",
            },
            bot_token,
        )
        return f"Rejected #{failure_id}"

    return "Unknown action."


def send_and_wait_for_approval(
    failure: dict[str, Any],
    final: dict[str, Any],
    diff: str = "",
    repo_root: Path | None = None,
    bot_token: str | None = None,
    chat_id: str | None = None,
    timeout: int = 120,
    on_wait_poll: Any = None,
) -> bool:
    """Send approval request to Telegram and wait inline for human decision."""
    import time
    token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
    cid = chat_id or os.getenv("TELEGRAM_CHAT_ID")
    root = (repo_root or Path.cwd()).resolve()

    if not token or not cid:
        logger.warning("Telegram token or chat_id not set.")
        return False

    failure_id = str(failure.get("id", "1"))

    # 1. Flush any past pending updates
    try:
        past = _call_telegram_api("getUpdates", {"offset": -1, "timeout": 0}, token)
        results = past.get("result", [])
        offset = results[-1].get("update_id", 0) + 1 if results else 0
    except Exception:
        offset = 0

    # 2. Send the interactive message
    ok = send_approval_request(failure=failure, final=final, diff=diff, bot_token=token, chat_id=cid)
    if not ok:
        return False

    # 3. Poll for user decision
    start_time = time.time()
    while time.time() - start_time < timeout:
        if on_wait_poll:
            on_wait_poll()
        try:
            updates = _call_telegram_api(
                "getUpdates",
                {"offset": offset, "timeout": 2, "allowed_updates": ["callback_query"]},
                token,
            )
            for item in updates.get("result", []):
                offset = max(offset, item.get("update_id", 0) + 1)
                cb = item.get("callback_query")
                if cb and cb.get("data", "").endswith(f":{failure_id}"):
                    action = cb.get("data", "").split(":")[0]
                    handle_telegram_callback(cb, repo_root=root, bot_token=token, final=final)
                    return action == "apply"
        except Exception as exc:
            logger.debug(f"Polling error: {exc}")

        time.sleep(1)

    return False


def listen_for_approvals(
    repo_root: Path,
    bot_token: str | None = None,
    poll_timeout: int = 20,
    max_loops: int | None = None,
) -> None:
    """Long-poll Telegram for callback queries and execute approved patches."""
    token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN must be set in .env or passed.")

    offset = 0
    loops = 0

    while max_loops is None or loops < max_loops:
        loops += 1
        try:
            updates = _call_telegram_api(
                "getUpdates",
                {"offset": offset, "timeout": poll_timeout, "allowed_updates": ["callback_query"]},
                token,
            )
            for item in updates.get("result", []):
                offset = max(offset, item.get("update_id", 0) + 1)
                if "callback_query" in item:
                    res = handle_telegram_callback(item["callback_query"], repo_root, token)
                    logger.info(f"Telegram approval action: {res}")
        except Exception as exc:
            logger.error(f"Error polling Telegram: {exc}")

