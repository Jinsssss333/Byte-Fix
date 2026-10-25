"""LLM client for Auto-Remediate with primary and fallback support."""
import os
from typing import Any
from dotenv import load_dotenv

# Import OpenAI in a way that's easily patchable
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

load_dotenv()


def chat(messages: list[dict[str, Any]]) -> tuple[str, str]:
    """Call the configured primary LLM, falling back to the secondary if configured.

    Returns (reply_text, model_label).
    """
    base_url = os.getenv("LLM_BASE_URL")
    model = os.getenv("LLM_MODEL")
    api_key = os.getenv("LLM_API_KEY")

    if not base_url or not model or not api_key:
        raise RuntimeError("LLM not configured: set LLM_BASE_URL, LLM_MODEL, LLM_API_KEY in .env")

    primary_err: Exception | None = None
    try:
        # Use the OpenAI class from module scope so it can be monkeypatched
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=60)
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.1,
        )
        content = None
        try:
            content = resp.choices[0].message.content
        except Exception:
            content = None
        if content and str(content).strip():
            return str(content), model
        primary_err = RuntimeError("Primary model returned empty reply")
    except Exception as exc:
        primary_err = exc

    # Attempt fallback if configured
    fb_base_url = os.getenv("FALLBACK_BASE_URL")
    fb_model = os.getenv("FALLBACK_MODEL")
    fb_api_key = os.getenv("FALLBACK_API_KEY")

    if fb_base_url and fb_model and fb_api_key:
        try:
            fb_client = OpenAI(base_url=fb_base_url, api_key=fb_api_key, timeout=60)
            fb_resp = fb_client.chat.completions.create(
                model=fb_model,
                messages=messages,
                temperature=0.1,
            )
            fb_content = None
            try:
                fb_content = fb_resp.choices[0].message.content
            except Exception:
                fb_content = None
            if fb_content and str(fb_content).strip():
                return str(fb_content), f"{fb_model} (fallback)"
            raise RuntimeError("Fallback model returned empty reply")
        except Exception as fb_exc:
            raise RuntimeError(
                f"Both primary and fallback models failed. Primary: {primary_err}; Fallback: {fb_exc}"
            ) from fb_exc

    raise RuntimeError(f"Primary model failed and no fallback configured: {primary_err}") from primary_err
