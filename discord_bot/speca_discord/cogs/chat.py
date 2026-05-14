"""`/ask` — single-turn chat against Claude using bot-side credentials.

Multi-turn / streaming is intentionally out of scope for the first slice:
Discord's 2000-char message limit + ephemeral interaction lifecycles make
streaming awkward to do well. The chat *channel* under each run category
will be wired to a richer multi-turn flow in the next slice.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..config import get_config
from ..services.claude_api import ClaudeAuthError, ask_claude

log = logging.getLogger(__name__)


DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant embedded in the SPECA security audit "
    "framework's Discord bot. Answer concisely (under ~400 words) and "
    "reference SPECA pipeline concepts (Phase 01a/01b/01e/02c/03/04, "
    "BUG_BOUNTY_SCOPE, TARGET_INFO) when they apply."
)


class ChatCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.cfg = get_config()

    @app_commands.command(
        name="ask",
        description="Ask Claude a single-turn question.",
    )
    @app_commands.describe(
        question="Your question (will be sent to Claude as a single user message).",
        system="Optional system prompt override.",
        max_tokens="Max tokens in the reply (default 1024).",
    )
    async def ask(
        self,
        interaction: discord.Interaction,
        question: str,
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> None:
        await interaction.response.defer(thinking=True)
        try:
            reply = await ask_claude(
                prompt=question,
                system=system or DEFAULT_SYSTEM_PROMPT,
                max_tokens=max_tokens,
                anthropic_api_key=self.cfg.anthropic_api_key,
                speca_auth_json_path=self.cfg.speca_auth_json_path,
            )
        except ClaudeAuthError as e:
            await interaction.followup.send(
                f"Auth error: {e}", ephemeral=True
            )
            return
        except Exception as e:
            log.exception("ask: Claude API call failed")
            await interaction.followup.send(
                f"Claude call failed: `{e}`", ephemeral=True
            )
            return

        text = reply.text or "(no text returned)"
        # Discord caps each message at 2000 chars. Chunk if needed.
        chunks = _chunk(text, 1900)
        await interaction.followup.send(
            f"**Q:** {question[:300]}\n**A:** {chunks[0]}"
        )
        for ch in chunks[1:]:
            await interaction.followup.send(ch)


def _chunk(s: str, limit: int) -> list[str]:
    if len(s) <= limit:
        return [s]
    out: list[str] = []
    buf = s
    while buf:
        out.append(buf[:limit])
        buf = buf[limit:]
    return out


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ChatCog(bot))
