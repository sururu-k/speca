"""Runtime configuration for the SPECA Discord bot.

Reads from environment + an optional ``.env`` file in the working directory.
Validates required settings up-front so the bot fails fast on misconfiguration
rather than crashing mid-command.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

from dotenv import load_dotenv


@dataclass(slots=True)
class BotConfig:
    """Fully resolved runtime configuration."""

    discord_token: str
    guild_id: int
    speca_repo_path: Path
    archive_root: Path
    parent_category_name: str
    anthropic_api_key: str | None
    speca_auth_json_path: Path | None
    max_live_log_lines: int
    log_level: int

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Self:
        """Load + validate config from environment + .env (if present).

        ``env`` lets tests inject a deterministic mapping without touching
        ``os.environ`` globally.
        """
        if env is None:
            load_dotenv(override=False)
            env = dict(os.environ)

        token = env.get("DISCORD_BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "DISCORD_BOT_TOKEN is unset — set it in your .env or shell env "
                "(see .env.example)."
            )

        guild_raw = env.get("SPECA_DISCORD_GUILD_ID", "").strip()
        if not guild_raw or not guild_raw.isdigit():
            raise RuntimeError(
                "SPECA_DISCORD_GUILD_ID must be a Discord guild (server) id "
                "consisting of digits only."
            )
        guild_id = int(guild_raw)

        speca_repo_path = Path(env.get("SPECA_REPO_PATH", "..")).expanduser().resolve()
        if not (speca_repo_path / "scripts" / "run_phase.py").is_file():
            raise RuntimeError(
                f"SPECA_REPO_PATH={speca_repo_path} does not look like a speca "
                "checkout — scripts/run_phase.py is missing."
            )

        archive_root_raw = env.get("SPECA_ARCHIVE_ROOT", "").strip()
        archive_root = (
            Path(archive_root_raw).expanduser().resolve()
            if archive_root_raw
            else speca_repo_path / ".speca" / "runs"
        )

        parent_category = env.get("SPECA_DISCORD_PARENT_CATEGORY", "SPECA").strip()
        if not parent_category:
            parent_category = "SPECA"

        anthropic_api_key = env.get("ANTHROPIC_API_KEY") or None
        auth_path_raw = env.get("SPECA_AUTH_JSON_PATH") or None
        speca_auth_json_path = Path(auth_path_raw).expanduser() if auth_path_raw else None

        try:
            max_lines = int(env.get("SPECA_DISCORD_MAX_LIVE_LOG_LINES", "30"))
        except ValueError:
            max_lines = 30
        max_lines = max(5, min(max_lines, 200))

        log_level_name = env.get("SPECA_DISCORD_LOG_LEVEL", "INFO").upper()
        log_level = getattr(logging, log_level_name, logging.INFO)

        return cls(
            discord_token=token,
            guild_id=guild_id,
            speca_repo_path=speca_repo_path,
            archive_root=archive_root,
            parent_category_name=parent_category,
            anthropic_api_key=anthropic_api_key,
            speca_auth_json_path=speca_auth_json_path,
            max_live_log_lines=max_lines,
            log_level=log_level,
        )


@dataclass(slots=True)
class _ConfigHolder:
    """Lazy singleton so config can be set up once and shared across cogs."""

    current: BotConfig | None = field(default=None)


_HOLDER = _ConfigHolder()


def get_config() -> BotConfig:
    if _HOLDER.current is None:
        _HOLDER.current = BotConfig.from_env()
    return _HOLDER.current


def set_config(cfg: BotConfig) -> None:
    """Override the resolved config (tests / explicit bootstrap)."""
    _HOLDER.current = cfg
