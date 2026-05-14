"""Bot bootstrap — assembles cogs and registers slash commands.

Slash commands are registered **per-guild** (via ``copy_global_to``) so
they propagate instantly during development. Global registration is a
separate slice once we're ready to publish the bot to multiple servers.
"""

from __future__ import annotations

import logging
import sys

import discord
from discord.ext import commands

from .config import BotConfig, get_config, set_config
from .panels.control_panel import ControlPanelView


def _setup_logging(level: int) -> None:
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def build_bot(cfg: BotConfig | None = None) -> commands.Bot:
    cfg = cfg or get_config()
    set_config(cfg)
    _setup_logging(cfg.log_level)

    intents = discord.Intents.default()
    intents.guilds = True
    intents.members = False  # we only need channel + interaction events
    intents.message_content = False

    bot = commands.Bot(
        command_prefix="!",  # unused — slash commands only — but discord.py requires a prefix.
        intents=intents,
        help_command=None,
    )

    @bot.event
    async def on_ready() -> None:
        log = logging.getLogger("speca_discord.main")
        log.info("logged in as %s (id=%s)", bot.user, bot.user.id if bot.user else "?")
        # Re-attach the persistent control-panel view so buttons posted in
        # previous bot lifetimes keep working after a restart.
        bot.add_view(ControlPanelView())
        guild = discord.Object(id=cfg.guild_id)
        try:
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
            log.info("slash commands synced to guild %d", cfg.guild_id)
        except discord.HTTPException as e:
            log.error("slash command sync failed: %s", e)

    return bot


async def _load_cogs(bot: commands.Bot) -> None:
    for ext in (
        "speca_discord.cogs.panel",
        "speca_discord.cogs.runs",
        "speca_discord.cogs.corpus",
        "speca_discord.cogs.findings",
        "speca_discord.cogs.chat",
    ):
        await bot.load_extension(ext)


async def run_bot() -> None:
    cfg = get_config()
    bot = build_bot(cfg)
    await _load_cogs(bot)
    try:
        await bot.start(cfg.discord_token)
    finally:
        await bot.close()
