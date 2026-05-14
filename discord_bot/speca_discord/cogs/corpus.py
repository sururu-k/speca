"""`/corpus-*` slash commands — list / show / export / gc against `.speca/runs/`.

list and show use the Python ``archive_reader`` helper (no subprocess) so
they're cheap. export and gc shell out to the speca-cli node binary so the
canonical redaction / soft-delete code is shared between TUI and bot.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..config import get_config
from ..panels.embeds import run_list_embed, run_show_embed
from ..services.archive_reader import list_runs, show_run
from ..services.speca_subprocess import corpus_subprocess

log = logging.getLogger(__name__)


async def render_list(interaction: discord.Interaction, *, ephemeral: bool = False) -> None:
    """Shared between slash command and control-panel button."""
    cfg = get_config()
    rows = list_runs(cfg.archive_root)
    em = run_list_embed(rows, archive_root=str(cfg.archive_root))
    if interaction.response.is_done():
        await interaction.followup.send(embed=em, ephemeral=ephemeral)
    else:
        await interaction.response.send_message(embed=em, ephemeral=ephemeral)


class CorpusCog(commands.Cog):
    """Read + (subprocess-mediated) write operations on the run archive."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.cfg = get_config()

    @app_commands.command(
        name="corpus-list",
        description="List runs in the SPECA archive (sorted desc by started_at).",
    )
    async def corpus_list(self, interaction: discord.Interaction) -> None:
        await render_list(interaction, ephemeral=False)

    @app_commands.command(
        name="corpus-show",
        description="Show manifest + per-phase breakdown for one run.",
    )
    @app_commands.describe(run_id="The run-id (copy from /corpus-list).")
    async def corpus_show(
        self, interaction: discord.Interaction, run_id: str
    ) -> None:
        detail = show_run(self.cfg.archive_root, run_id)
        if detail is None:
            await interaction.response.send_message(
                f"Run `{run_id}` not found under `{self.cfg.archive_root}`.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(embed=run_show_embed(detail))

    @app_commands.command(
        name="corpus-export",
        description="Package a redacted slice of a run for sharing (subprocess to speca-cli).",
    )
    @app_commands.describe(
        run_id="The run-id to export.",
        out="Output directory (default <cwd>/speca-corpus-<run-id>/).",
        include_logs="Include redacted stream-json logs.",
        phases="Comma-separated phases (default 01a,01b,01e).",
        unsafe_include_findings="Allow 02c/03/04 phases (target-code data).",
    )
    async def corpus_export(
        self,
        interaction: discord.Interaction,
        run_id: str,
        out: str | None = None,
        include_logs: bool = False,
        phases: str | None = None,
        unsafe_include_findings: bool = False,
    ) -> None:
        await interaction.response.defer(thinking=True)
        args: list[str] = [run_id]
        if out:
            args += ["--out", out]
        if include_logs:
            args.append("--include-logs")
        if phases:
            args += ["--phases", phases]
        if unsafe_include_findings:
            args.append("--unsafe-include-findings")
        rc, stdout, stderr = await corpus_subprocess(
            speca_repo_path=self.cfg.speca_repo_path,
            subcommand="export",
            args=args,
        )
        if rc == 0:
            await interaction.followup.send(
                f"Export succeeded.\n```{stdout.strip()[-1800:]}```",
            )
        else:
            await interaction.followup.send(
                f"Export failed (exit {rc}).\n```{(stderr or stdout).strip()[-1800:]}```",
                ephemeral=True,
            )

    @app_commands.command(
        name="corpus-gc",
        description="Soft-delete runs older than a duration (default: dry-run).",
    )
    @app_commands.describe(
        older_than="Duration like 90d / 2w / 36h.",
        no_dry_run="Actually move archives into .trash/ (default off).",
    )
    async def corpus_gc(
        self,
        interaction: discord.Interaction,
        older_than: str,
        no_dry_run: bool = False,
    ) -> None:
        await interaction.response.defer(thinking=True)
        args: list[str] = ["--older-than", older_than]
        if no_dry_run:
            args.append("--no-dry-run")
        rc, stdout, stderr = await corpus_subprocess(
            speca_repo_path=self.cfg.speca_repo_path,
            subcommand="gc",
            args=args,
        )
        if rc == 0:
            await interaction.followup.send(
                f"```{stdout.strip()[-1800:]}```",
            )
        else:
            await interaction.followup.send(
                f"gc failed (exit {rc}).\n```{(stderr or stdout).strip()[-1800:]}```",
                ephemeral=True,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CorpusCog(bot))
