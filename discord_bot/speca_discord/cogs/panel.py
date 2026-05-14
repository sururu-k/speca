"""Control-panel cog — posts the persistent panel message and serves /speca-help."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..config import get_config
from ..panels.control_panel import ControlPanelView
from ..panels.embeds import control_panel_embed


class PanelCog(commands.Cog):
    """Manages the persistent control-panel message in the SPECA parent category."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.cfg = get_config()

    @app_commands.command(
        name="speca-panel",
        description="Post (or repost) the SPECA control panel in this channel.",
    )
    async def speca_panel(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "Run this from a text channel where the bot can post.",
                ephemeral=True,
            )
            return
        em = control_panel_embed()
        view = ControlPanelView()
        await interaction.channel.send(embed=em, view=view)
        await interaction.response.send_message(
            "Control panel posted. Buttons survive bot restarts; you can pin "
            "this message for easy access.",
            ephemeral=True,
        )

    @app_commands.command(
        name="speca-help",
        description="Show the SPECA bot help embed (same content as the panel).",
    )
    async def speca_help(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=control_panel_embed(), ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PanelCog(bot))
