"""
JARVIS – Claude API Adapter
============================
Makes Anthropic's Claude API a drop-in replacement for the OpenAI endpoint
used by awesome_chat.py.

Usage inside awesome_chat.py:
  from claude_adapter import call_claude
  # or set API_TYPE = "claude" and let send_request() route here automatically.

Environment variables:
  ANTHROPIC_API_KEY   – your Anthropic API key (sk-ant-…)

Supported models (set in config.yaml → model: ):
  claude-opus-4-6
  claude-sonnet-4-6
  claude-haiku-4-5-20251001
  claude-3-5-sonnet-20241022
  (any model available on the Anthropic Messages API)
"""

import os
import json
import re
from typing import List, Optional, Dict, Any

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL     = "claude-sonnet-4-6"
MAX_TOKENS        = 4096


def _get_api_key(explicit_key: Optional[str] = None) -> str:
    key = (explicit_key or "").strip()
    if not key:
        key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise ValueError(
            "Anthropic API key not found. "
            "Set ANTHROPIC_API_KEY env var or pass api_key explicitly."
        )
    return key


def _messages_to_claude(messages: List[Dict[str, str]]):
    """
    Convert OpenAI-style messages list to Claude Messages API format.
    Returns (system_prompt: str | None, messages: List[dict])
    """
    system_prompt = None
    claude_msgs   = []

    for msg in messages:
        role    = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            # Claude treats the first system message as the system prompt
            system_prompt = content
        elif role in ("user", "assistant"):
            claude_msgs.append({"role": role, "content": content})
        # skip unknown roles

    # Claude requires alternating user/assistant turns; deduplicate consecutive same-role
    clean = []
    for m in claude_msgs:
        if clean and clean[-1]["role"] == m["role"]:
            # Merge into previous message
            clean[-1]["content"] += "\n" + m["content"]
        else:
            clean.append(m)

    # Must start with "user"
    if clean and clean[0]["role"] != "user":
        clean.insert(0, {"role": "user", "content": "(continue)"})

    return system_prompt, clean


def call_claude(
    messages: List[Dict[str, str]],
    model:    str                   = DEFAULT_MODEL,
    api_key:  Optional[str]         = None,
    max_tokens: int                 = MAX_TOKENS,
    temperature: float              = 0.7,
    proxies: Optional[Dict]         = None,
) -> str:
    """
    Send a request to Claude and return the assistant's reply as a plain string.
    Compatible with the return type of awesome_chat.py's send_request().
    """
    import requests as req

    key                   = _get_api_key(api_key)
    system_prompt, msgs   = _messages_to_claude(messages)

    payload: Dict[str, Any] = {
        "model":       model,
        "max_tokens":  max_tokens,
        "temperature": temperature,
        "messages":    msgs,
    }
    if system_prompt:
        payload["system"] = system_prompt

    headers = {
        "x-api-key":         key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type":      "application/json",
    }

    response = req.post(
        ANTHROPIC_API_URL,
        json=payload,
        headers=headers,
        proxies=proxies,
        timeout=120,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Claude API error {response.status_code}: {response.text[:500]}"
        )

    data = response.json()

    # Extract text from the first content block
    content_blocks = data.get("content", [])
    for block in content_blocks:
        if block.get("type") == "text":
            return block["text"].strip()

    raise RuntimeError(f"Unexpected Claude response shape: {json.dumps(data)[:300]}")


# ─────────────────────────────────────────────────────────────────────────────
# Thin wrapper so awesome_chat.py's send_request() can call this directly
# ─────────────────────────────────────────────────────────────────────────────

def send_request_claude(data: Dict[str, Any], proxies: Optional[Dict] = None) -> str:
    """
    Mirror the interface of awesome_chat.py's send_request().
    data keys consumed: messages, model, api_key, max_tokens, temperature
    """
    messages    = data.get("messages", [])
    model       = data.get("model",       DEFAULT_MODEL)
    api_key     = data.get("api_key",     "")
    max_tokens  = data.get("max_tokens",  MAX_TOKENS)
    temperature = data.get("temperature", 0.7)

    return call_claude(
        messages    = messages,
        model       = model,
        api_key     = api_key,
        max_tokens  = int(max_tokens),
        temperature = float(temperature),
        proxies     = proxies,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Standalone test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    print("Testing Claude adapter…")
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        print("Set ANTHROPIC_API_KEY first.")
        sys.exit(1)

    reply = call_claude(
        messages=[
            {"role": "system",    "content": "You are JARVIS, a highly capable AI assistant."},
            {"role": "user",      "content": "Say 'JARVIS online' and nothing else."},
        ],
        api_key=key,
    )
    print(f"Response: {reply}")
