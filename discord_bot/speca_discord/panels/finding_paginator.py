"""Paginated finding browser View.

Used by both the slash command and the control-panel button. Pages are
1-indexed in the UI but the underlying list is 0-indexed. Pagination is
stateless beyond the in-memory list — for very large finding sets the
caller is expected to apply a pre-filter via slash command flags.
"""

from __future__ import annotations

import discord
from discord import ui

from ..services.findings_reader import Finding
from .embeds import finding_embed


class FindingPaginator(ui.View):
    def __init__(
        self,
        findings: list[Finding],
        *,
        author_id: int,
        timeout: float = 600.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.findings = findings
        self.index = 0
        self.author_id = author_id
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        total = len(self.findings)
        self.prev_button.disabled = self.index <= 0
        self.next_button.disabled = self.index >= total - 1

    def current_embed(self) -> discord.Embed:
        if not self.findings:
            em = discord.Embed(
                title="No findings",
                description=(
                    "No `04_PARTIAL_*.json` results found under the configured "
                    "output dir. Run Phase 04 first."
                ),
                color=discord.Color.greyple(),
            )
            return em
        return finding_embed(self.findings[self.index], self.index, len(self.findings))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Only the user who opened the paginator should drive it — otherwise
        # multiple operators trample each other in shared channels.
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This paginator belongs to another operator; open your own "
                "with `/findings`.",
                ephemeral=True,
            )
            return False
        return True

    @ui.button(label="Prev", style=discord.ButtonStyle.secondary, custom_id="speca:findings:prev")
    async def prev_button(self, interaction: discord.Interaction, _: ui.Button) -> None:
        if self.index > 0:
            self.index -= 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    @ui.button(label="Next", style=discord.ButtonStyle.primary, custom_id="speca:findings:next")
    async def next_button(self, interaction: discord.Interaction, _: ui.Button) -> None:
        if self.index < len(self.findings) - 1:
            self.index += 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    @ui.button(label="Close", style=discord.ButtonStyle.danger, custom_id="speca:findings:close")
    async def close_button(self, interaction: discord.Interaction, _: ui.Button) -> None:
        for item in self.children:
            if isinstance(item, ui.Button):
                item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()
