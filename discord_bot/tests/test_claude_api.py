"""claude_api — credential resolution + OAuth token reader.

We don't hit the real Anthropic API; the HTTP call is covered by the
integration test for the cog (deferred to a manual stage). This file pins
the auth-credential precedence + OAuth-store parsing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from speca_discord.services.claude_api import (
    ClaudeAuthError,
    _read_oauth_token,
    _resolve_credentials,
)


def test_api_key_path_returns_xapikey_header() -> None:
    kind, headers = _resolve_credentials(
        anthropic_api_key="sk-ant-xyz", speca_auth_json_path=None
    )
    assert kind == "apikey"
    assert headers["x-api-key"] == "sk-ant-xyz"
    assert headers["anthropic-version"] == "2023-06-01"


def test_oauth_path_returns_bearer_header(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps(
            {
                "accounts": {
                    "default": {
                        "type": "oauth",
                        "access_token": "atok-1",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    kind, headers = _resolve_credentials(
        anthropic_api_key=None, speca_auth_json_path=auth
    )
    assert kind == "oauth"
    assert headers["authorization"] == "Bearer atok-1"


def test_api_key_wins_over_oauth(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps(
            {"accounts": {"d": {"type": "oauth", "access_token": "atok"}}}
        ),
        encoding="utf-8",
    )
    kind, _ = _resolve_credentials(
        anthropic_api_key="sk-ant", speca_auth_json_path=auth
    )
    assert kind == "apikey"


def test_no_credentials_raises() -> None:
    with pytest.raises(ClaudeAuthError):
        _resolve_credentials(anthropic_api_key=None, speca_auth_json_path=None)


def test_missing_auth_json_falls_through_to_error(tmp_path: Path) -> None:
    # File doesn't exist → token resolution returns None → no creds → error.
    with pytest.raises(ClaudeAuthError):
        _resolve_credentials(
            anthropic_api_key=None,
            speca_auth_json_path=tmp_path / "nonexistent.json",
        )


def test_oauth_reader_handles_corrupt_json(tmp_path: Path) -> None:
    bad = tmp_path / "auth.json"
    bad.write_text("not json", encoding="utf-8")
    assert _read_oauth_token(bad) is None


def test_oauth_reader_skips_api_key_account(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps(
            {"accounts": {"d": {"type": "apikey", "key": "sk-ant"}}}
        ),
        encoding="utf-8",
    )
    assert _read_oauth_token(auth) is None
