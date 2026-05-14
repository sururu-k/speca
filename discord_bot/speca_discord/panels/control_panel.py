"""Persistent control-panel View — button row that mirrors the slash commands.

Discord View buttons require ``custom_id`` so they survive bot restarts; the
button callbacks resolve to slash-command-equivalent flows by importing the
respective cog at click time (avoids circular imports).
"""

from __future__ import annotations

import discord
from discord import ui


class ControlPanelView(ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @ui.button(
        label="Start run",
        style=discord.ButtonStyle.success,
        custom_id="speca:start-run",
    )
    async def start_run(self, interaction: discord.Interaction, _: ui.Button) -> None:
        await interaction.response.send_message(
            "Use `/speca-run target_repo:<owner/name> target_commit:<sha>`. "
            "The button-driven wizard is on the roadmap for the next slice.",
            ephemeral=True,
        )

    @ui.button(
        label="List runs",
        style=discord.ButtonStyle.primary,
        custom_id="speca:list-runs",
    )
    async def list_runs(self, interaction: discord.Interaction, _: ui.Button) -> None:
        # Importing the cog lazily avoids a circular import at module load.
        from ..cogs.corpus import render_list

        await render_list(interaction, ephemeral=True)

    @ui.button(
        label="Findings",
        style=discord.ButtonStyle.primary,
        custom_id="speca:findings",
    )
    async def findings(self, interaction: discord.Interaction, _: ui.Button) -> None:
        from ..cogs.findings import render_findings

        await render_findings(interaction, ephemeral=True)

    @ui.button(
        label="Ask Claude",
        style=discord.ButtonStyle.secondary,
        custom_id="speca:ask",
    )
    async def ask(self, interaction: discord.Interaction, _: ui.Button) -> None:
        await interaction.response.send_message(
            "Use `/ask question:<your question>`. The chat panel-driven "
            "modal form is on the roadmap.",
            ephemeral=True,
        )
