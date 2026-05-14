"""Modal form for the panel-driven "Start run" flow.

The control-panel "Start run" button used to dump the operator into the
slash command surface. Now it pops up a 5-field modal so the same
information can be entered without typing `/`. The submit handler calls
back into ``RunsCog.speca_run`` via a synthetic ``app_commands.Command``
invocation surrogate that mimics the slash-command-driven path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import ui

if TYPE_CHECKING:
    from ..cogs.runs import RunsCog


class StartRunModal(ui.Modal):
    """5-field modal for /speca-run. Fields map 1:1 to the slash command."""

    target_repo = ui.TextInput(
        label="Target repo (e.g. ethereum/go-ethereum)",
        placeholder="owner/name",
        required=False,
        max_length=120,
    )
    target_commit = ui.TextInput(
        label="Target commit SHA",
        placeholder="full or short SHA — leave blank for HEAD",
        required=False,
        max_length=64,
    )
    target = ui.TextInput(
        label="Target phase (01a / 01b / 01e / 02c / 03 / 04)",
        placeholder="04",
        required=False,
        default="04",
        max_length=4,
    )
    spec_urls = ui.TextInput(
        label="Spec URLs (comma-separated, optional)",
        placeholder="https://...,https://...",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )
    scope_01a = ui.TextInput(
        label="Phase 01a scope (all / primary / primary+1hop / N)",
        placeholder="all",
        required=False,
        max_length=20,
    )

    def __init__(self, cog: RunsCog) -> None:
        super().__init__(title="Start SPECA run")
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # Resolve the inner /speca-run callback. discord.py wraps the cog
        # method in a Command instance; .callback re-binds the original
        # async function with the cog as ``self``.
        cmd = self.cog.speca_run
        callback = getattr(cmd, "callback", None) or cmd
        target_value = (self.target.value or "").strip() or "04"
        await callback(
            self.cog,
            interaction,
            target_repo=(self.target_repo.value or "").strip() or None,
            target_commit=(self.target_commit.value or "").strip() or None,
            target=target_value,
            spec_urls=(self.spec_urls.value or "").strip() or None,
            keywords=None,
            scope_01a=(self.scope_01a.value or "").strip() or None,
            label=None,
        )
