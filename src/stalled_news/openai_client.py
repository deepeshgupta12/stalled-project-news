from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

import httpx
from dotenv import load_dotenv


def _load_env() -> None:
    # Keep it robust even if caller didn't load env
    load_dotenv(".env", override=True)


def openai_api_key() -> str:
    _load_env()
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("Missing OPENAI_API_KEY in .env")
    return key


def openai_model() -> str:
    _load_env()
    return (os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip()


def chat_completion_json(
    *,
    system: str,
    user: str,
    temperature: float = 0.2,
    max_tokens: int = 900,
    timeout_s: float = 60.0,
) -> Dict[str, Any]:
    """
    Calls OpenAI Chat Completions API (HTTP) and expects JSON object response.
    """
    key = openai_api_key()
    model = openai_model()

    payload = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
    }

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=timeout_s) as client:
        r = client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
        if r.status_code >= 400:
            raise RuntimeError(f"OpenAI error {r.status_code}: {r.text}")

    data = r.json()
    content = data["choices"][0]["message"]["content"]
    try:
        return json.loads(content)
    except Exception as e:
        raise RuntimeError(f"Model did not return valid JSON. Error={e}. Content={content[:500]}") from e


# ------------------------------------------------------------
# Backward-compatible JSON chat helper
# Used by news_generator.py (expects openai_chat_json)
# ------------------------------------------------------------
def openai_chat_json(
    *,
    system: str,
    user: str,
    model: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 1400,
) -> Dict[str, Any]:
    """
    Calls OpenAI chat and returns a parsed JSON object.
    Strict contract: return dict or raise.

    Loads .env automatically.
    """
    _load_env()

    # Lazy import so non-OpenAI commands don't fail if SDK isn't present
    from openai import OpenAI

    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY in .env")

    m = (model or (os.getenv("OPENAI_MODEL") or "").strip() or "gpt-4.1-mini").strip()

    client = OpenAI(api_key=api_key)

    resp = client.chat.completions.create(
        model=m,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
    )

    content = resp.choices[0].message.content or ""
    try:
        return json.loads(content)
    except Exception as e:
        raise RuntimeError(f"OpenAI returned non-JSON. First 300 chars: {content[:300]}") from e
