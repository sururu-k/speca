"""Per-run control View — Stop / Refresh / Open archive buttons in #info.

Buttons use stable ``custom_id`` values that embed the run label, so a
restart on either the bot or the host can re-bind the View to the same
runs after recovery without losing button affordance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import ui

if TYPE_CHECKING:
    from ..cogs.runs import RunsCog


_STOP_ID_PREFIX = "speca:run-stop:"
_REFRESH_ID_PREFIX = "speca:run-refresh:"


class RunControlView(ui.View):
    """A persistent View instance per active run.

    Encoded ``custom_id`` carries the run label so the button click handler
    can locate the active run state from the cog after a restart.
    """

    def __init__(self, label: str, cog: RunsCog) -> None:
        super().__init__(timeout=None)
        self.label = label
        self.cog = cog
        # Build buttons with run-scoped custom_ids so add_view() can rebind
        # them after a restart by deriving the label from custom_id alone.
        self.add_item(
            _StopButton(custom_id=f"{_STOP_ID_PREFIX}{label}", label="Stop run")
        )
        self.add_item(
            _RefreshButton(
                custom_id=f"{_REFRESH_ID_PREFIX}{label}", label="Refresh status"
            )
        )


class _StopButton(ui.Button):
    def __init__(self, *, custom_id: str, label: str) -> None:
        super().__init__(
            style=discord.ButtonStyle.danger,
            label=label,
            custom_id=custom_id,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = _resolve_cog(interaction)
        run_label = (self.custom_id or "")[len(_STOP_ID_PREFIX):]
        if not cog or not run_label:
            await interaction.response.send_message(
                "Stop button has lost its binding (custom_id parse failure). "
                "Try `/speca-cancel` instead.",
                ephemeral=True,
            )
            return
        await cog.request_stop(interaction, run_label)


class _RefreshButton(ui.Button):
    def __init__(self, *, custom_id: str, label: str) -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label=label,
            custom_id=custom_id,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = _resolve_cog(interaction)
        run_label = (self.custom_id or "")[len(_REFRESH_ID_PREFIX):]
        if not cog or not run_label:
            await interaction.response.send_message(
                "Refresh button binding lost.", ephemeral=True
            )
            return
        await cog.refresh_status(interaction, run_label)


def _resolve_cog(interaction: discord.Interaction) -> RunsCog | None:
    bot = interaction.client
    cog = bot.get_cog("RunsCog") if hasattr(bot, "get_cog") else None
    return cog  # type: ignore[return-value]
