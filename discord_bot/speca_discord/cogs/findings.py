"""`/findings` — paginated browse of Phase 04 PARTIAL outputs.

Reads from ``<speca_repo>/outputs/04_PARTIAL_*.json`` by default, with an
optional ``--output-dir`` flag to point at a per-run isolated dir.
"""

from __future__ import annotations

from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from ..config import get_config
from ..panels.finding_paginator import FindingPaginator
from ..services.findings_reader import load_findings, sort_by_severity


async def render_findings(
    interaction: discord.Interaction,
    *,
    output_dir: str | None = None,
    severity: str | None = None,
    ephemeral: bool = False,
) -> None:
    cfg = get_config()
    base = Path(output_dir).expanduser().resolve() if output_dir else cfg.speca_repo_path / "outputs"
    findings = load_findings(base)
    if severity:
        sev_upper = severity.upper().strip()
        findings = [f for f in findings if f.severity.upper() == sev_upper]
    findings = sort_by_severity(findings)
    view = FindingPaginator(findings, author_id=interaction.user.id)
    em = view.current_embed()
    if interaction.response.is_done():
        await interaction.followup.send(embed=em, view=view, ephemeral=ephemeral)
    else:
        await interaction.response.send_message(embed=em, view=view, ephemeral=ephemeral)


class FindingsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.cfg = get_config()

    @app_commands.command(
        name="findings",
        description="Browse Phase 04 findings in a paginated embed.",
    )
    @app_commands.describe(
        output_dir="Override outputs/ root (default: <speca>/outputs/).",
        severity="Filter to a single severity (Critical / High / Medium / Low / Informational).",
    )
    async def findings_cmd(
        self,
        interaction: discord.Interaction,
        output_dir: str | None = None,
        severity: str | None = None,
    ) -> None:
        await render_findings(
            interaction,
            output_dir=output_dir,
            severity=severity,
            ephemeral=False,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FindingsCog(bot))
