"""
LLM client abstraction.
Supports Anthropic Claude and OpenAI GPT.
Provider is chosen via the LLM_PROVIDER env var (default: anthropic).
"""

import os
import json
import urllib.request
import urllib.error
from typing import Any


LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "anthropic").lower()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# Model defaults
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")

MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "4096"))


def _post(url: str, headers: dict, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        raise RuntimeError(f"LLM API error [{e.code}]: {body_text}") from e


def call_anthropic(system: str, messages: list[dict], json_mode: bool = False) -> str:
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY is not set")
    effective_system = system
    if json_mode:
        effective_system = (
            f"{system}\n\n"
            "Return ONLY a valid JSON object. Do not include markdown fences or extra text."
        )

    resp = _post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        body={
            "model": ANTHROPIC_MODEL,
            "max_tokens": MAX_TOKENS,
            "system": effective_system,
            "messages": messages,
        },
    )
    return resp["content"][0]["text"]


def call_openai(system: str, messages: list[dict], json_mode: bool = False) -> str:
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is not set")
    openai_messages = [{"role": "system", "content": system}] + messages
    body = {
        "model": OPENAI_MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": openai_messages,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    resp = _post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        body=body,
    )
    return resp["choices"][0]["message"]["content"]


def complete(
    system: str,
    user_message: str,
    history: list[dict] | None = None,
    json_mode: bool = False,
) -> str:
    """
    Call the configured LLM provider.
    history: list of {"role": "user"|"assistant", "content": str}
    """
    messages = list(history or []) + [{"role": "user", "content": user_message}]
    if LLM_PROVIDER == "openai":
        return call_openai(system, messages, json_mode=json_mode)
    return call_anthropic(system, messages, json_mode=json_mode)
