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

    # Format truncated diff
    diff_snippet = diff[:1500] if diff else "No diff available."
    if len(diff) > 1500:
        diff_snippet += "\n... [diff truncated]"

    text = (
        f"🚨 <b>Auto-Remediate Approval Request: #{failure_id}</b>\n\n"
        f"<b>Task:</b> <code>{task_name}</code>\n"
        f"<b>Error:</b> <code>{exc_type}: {exc_msg}</code>\n"
        f"<b>Confidence:</b> {confidence.upper()}\n\n"
        f"<b>🔍 Root Cause:</b>\n{root_cause}\n\n"
        f"<b>📝 Proposed Patch:</b>\n<pre>{diff_snippet}</pre>\n\n"
        f"<i>Review the verified patch below. Tap Approve to apply it to the codebase.</i>"
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
        # Load verified recording
        rec_dir = Path(os.getenv("RECORDINGS_DIR", "recordings"))
        rec_file = rec_dir / f"run_{failure_id}.json"

        if not rec_file.exists():
            feedback = f"⚠️ Recording run_{failure_id}.json not found."
            _call_telegram_api("answerCallbackQuery", {"callback_query_id": cb_id, "text": feedback}, bot_token)
            return feedback

        try:
            rec_data = json.loads(rec_file.read_text(encoding="utf-8"))
            final = rec_data.get("final", {})
            applied_files = apply_patch_to_repo(final, repo_root)

            toast = f"✅ Applied patch to {len(applied_files)} file(s)!"
            _call_telegram_api("answerCallbackQuery", {"callback_query_id": cb_id, "text": toast}, bot_token)

            # Update message to show approved state
            files_list = ", ".join(applied_files) or "codebase"
            updated_text = (
                f"{message.get('text', '')}\n\n"
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

        updated_text = (
            f"{message.get('text', '')}\n\n"
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
