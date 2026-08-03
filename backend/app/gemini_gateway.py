"""Minimal Gemini gateway; no automatic provider fallback.

Plain chat responses and JSON extraction use separate Gemini response modes.
Keeping that distinction here prevents an Ask turn from accidentally requesting
JSON merely because another workflow needs structured output.
"""
from __future__ import annotations
import os
import httpx

def _prompt_text(prompt: object) -> str:
    messages=getattr(prompt, "messages", None)
    return "\n".join(str(getattr(message, "content", message)) for message in messages) if messages else str(prompt)


def _generate(model: str, prompt: object, timeout_seconds: float, *, json_mode: bool) -> str:
    key=os.getenv("GEMINI_API_KEY", "").strip()
    if not key: raise RuntimeError("gemini_api_key_missing")
    generation_config={"responseMimeType":"application/json"} if json_mode else {}
    response=httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key":key},
        json={"contents":[{"parts":[{"text":_prompt_text(prompt)}]}], **({"generationConfig":generation_config} if generation_config else {})},
        timeout=max(.5,min(timeout_seconds,35)),
    )
    if response.status_code==429: raise RuntimeError("gemini_rate_limited")
    response.raise_for_status(); payload=response.json()
    try: return str(payload["candidates"][0]["content"]["parts"][0]["text"])
    except (KeyError, IndexError, TypeError) as exc: raise RuntimeError("gemini_invalid_response") from exc


def generate_text(model: str, prompt: object, timeout_seconds: float) -> str:
    """Generate normal prose for Ask/chat flows."""
    return _generate(model, prompt, timeout_seconds, json_mode=False)


def generate_json(model: str, prompt: object, timeout_seconds: float) -> str:
    """Generate JSON only for a workflow that validates it independently."""
    return _generate(model, prompt, timeout_seconds, json_mode=True)
