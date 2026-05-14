"""Thin async wrapper around the Anthropic API for the /ask cog.

We deliberately don't depend on ``anthropic`` (the official SDK) to keep
the bot's dependency surface small — the API surface we need is one
``POST /v1/messages`` call. This also lets us swap in the Claude-Code
subscription OAuth flow later without churning the dependency tree.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-4-6"


@dataclass(slots=True)
class ChatReply:
    text: str
    model: str
    stop_reason: str | None


class ClaudeAuthError(RuntimeError):
    """Raised when no usable credentials are available."""


def _read_oauth_token(auth_json_path: Path) -> str | None:
    try:
        data = json.loads(auth_json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    # speca-cli writes one of two shapes; try the canonical first.
    accounts = data.get("accounts") if isinstance(data, dict) else None
    if isinstance(accounts, dict):
        for entry in accounts.values():
            if isinstance(entry, dict) and entry.get("type") == "oauth":
                tok = entry.get("access_token")
                if isinstance(tok, str) and tok:
                    return tok
    return None


def _resolve_credentials(
    *,
    anthropic_api_key: str | None,
    speca_auth_json_path: Path | None,
) -> tuple[str, dict[str, str]]:
    """Return ``(auth_kind, headers)`` for the chosen credential source.

    Falls back from API key → OAuth token → raises.
    """
    if anthropic_api_key:
        return "apikey", {
            "x-api-key": anthropic_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
    if speca_auth_json_path is not None:
        tok = _read_oauth_token(speca_auth_json_path)
        if tok:
            return "oauth", {
                "authorization": f"Bearer {tok}",
                "anthropic-version": "2023-06-01",
                "anthropic-beta": "oauth-2025-04-20",
                "content-type": "application/json",
            }
    raise ClaudeAuthError(
        "No usable Claude credentials: set ANTHROPIC_API_KEY or point "
        "SPECA_AUTH_JSON_PATH at a valid speca-cli auth.json."
    )


async def ask_claude(
    *,
    prompt: str,
    system: str | None = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 1024,
    anthropic_api_key: str | None = None,
    speca_auth_json_path: Path | None = None,
    timeout_s: float = 60.0,
) -> ChatReply:
    """One-shot question/answer call. Returns assistant text or raises."""
    _, headers = _resolve_credentials(
        anthropic_api_key=anthropic_api_key,
        speca_auth_json_path=speca_auth_json_path,
    )
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        body["system"] = system

    timeout = aiohttp.ClientTimeout(total=timeout_s)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(ANTHROPIC_API_URL, headers=headers, json=body) as resp:
            data = await resp.json()
            if resp.status >= 400:
                msg = (data or {}).get("error", {}).get("message") or str(data)
                raise RuntimeError(f"Anthropic API {resp.status}: {msg}")
            content = data.get("content") or []
            text_parts: list[str] = [
                str(block.get("text", ""))
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            return ChatReply(
                text="\n".join(p for p in text_parts if p),
                model=data.get("model", model),
                stop_reason=data.get("stop_reason"),
            )
