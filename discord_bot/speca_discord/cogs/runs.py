"""`/speca-run` slash command — spawn run_phase.py and mirror progress to Discord.

For each invocation:
  1. Resolve / create the parent category (default "SPECA").
  2. Create a per-run category ``Run <slug>`` under the parent.
  3. Create one channel per phase listed in --target chain (#01a, #01b, ...)
     plus a control `#info` channel and an `#chat` channel for /ask threads.
  4. Spawn ``scripts/run_phase.py --json`` as an async subprocess.
  5. Iterate NDJSON events, routing them to the matching phase channel and
     posting a sticky "latest status" embed in `#info` that is edited in place.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands

from ..config import get_config
from ..panels.embeds import phase_event_line
from ..services.speca_subprocess import RunPhaseHandle, start_run_phase

log = logging.getLogger(__name__)

# Phases that run_phase.py supports as part of the chain. Channel name maps
# directly off the phase id so the Discord layout matches the doc layout.
_PHASE_CHAIN = ("01a", "01b", "01e", "02c", "03", "04")


def _slugify(text: str) -> str:
    """Discord category names allow most chars; we still strip slashes and
    cap the length so the layout is predictable."""
    cleaned = "".join(c if c.isalnum() or c in "-_." else "-" for c in text)
    cleaned = cleaned.strip("-")
    return cleaned[:60] or "run"


async def _ensure_parent_category(
    guild: discord.Guild, name: str
) -> discord.CategoryChannel:
    for cat in guild.categories:
        if cat.name == name:
            return cat
    return await guild.create_category(name=name, reason="SPECA bot bootstrap")


async def _create_run_category(
    guild: discord.Guild,
    parent: discord.CategoryChannel,
    run_label: str,
    phases: tuple[str, ...],
) -> tuple[discord.CategoryChannel, dict[str, discord.TextChannel]]:
    """Create ``Run <label>`` category and phase + info + chat channels."""
    name = f"Run {run_label}"[:100]
    category = await guild.create_category(
        name=name,
        reason=f"SPECA run {run_label}",
        position=parent.position + 1,
    )
    channels: dict[str, discord.TextChannel] = {}
    channels["info"] = await guild.create_text_channel(
        name="info", category=category, topic=f"Status board for run {run_label}"
    )
    for phase in phases:
        channels[phase] = await guild.create_text_channel(
            name=phase, category=category, topic=f"Stream-JSON events from Phase {phase}"
        )
    channels["chat"] = await guild.create_text_channel(
        name="chat", category=category, topic="Ask Claude follow-up questions"
    )
    return category, channels


class _ActiveRun:
    """Mutable state for a single live run — kept off the cog so multiple
    runs can be tracked concurrently."""

    __slots__ = ("buffers", "category", "channels", "handle", "info_message", "label")

    def __init__(
        self,
        label: str,
        category: discord.CategoryChannel,
        channels: dict[str, discord.TextChannel],
        info_message: discord.Message,
        handle: RunPhaseHandle,
    ) -> None:
        self.label = label
        self.category = category
        self.channels = channels
        self.info_message = info_message
        self.handle = handle
        self.buffers: dict[str, list[str]] = defaultdict(list)


class RunsCog(commands.Cog):
    """Spawn + monitor SPECA runs from Discord."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.cfg = get_config()
        self.active: dict[str, _ActiveRun] = {}
        # Strong refs to background event-loop tasks so Python's GC doesn't
        # snip them mid-flight (RUF006). Dropped from this set as each
        # task completes in _consume_events' finally clause.
        self._tasks: set[asyncio.Task[None]] = set()

    @app_commands.command(
        name="speca-run",
        description="Start a SPECA audit run (creates a per-run category with phase channels).",
    )
    @app_commands.describe(
        target_repo="Target repository (e.g. ethereum/go-ethereum). Sets TARGET_REPO env.",
        target_commit="Target commit SHA to pin (sets TARGET_COMMIT env).",
        target="run_phase --target chain end (default 04 = full audit).",
        spec_urls="Comma-separated seed URLs for Phase 01a discovery.",
        keywords="Comma-separated keywords for Phase 01a discovery.",
        scope_01a="Phase 01a filter: all (default) / primary / primary+1hop / N.",
        label="Custom slug used for the category name (default: derived from target_repo).",
    )
    async def speca_run(
        self,
        interaction: discord.Interaction,
        target_repo: str | None = None,
        target_commit: str | None = None,
        target: str = "04",
        spec_urls: str | None = None,
        keywords: str | None = None,
        scope_01a: str | None = None,
        label: str | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=False, thinking=True)
        guild = interaction.guild
        if guild is None or guild.id != self.cfg.guild_id:
            await interaction.followup.send(
                "This command must be run from the configured SPECA guild.",
                ephemeral=True,
            )
            return

        run_label = _slugify(label or target_repo or "ad-hoc")
        if run_label in self.active:
            await interaction.followup.send(
                f"A run with label `{run_label}` is already active; pick a "
                "different `label:` or stop the existing run first.",
                ephemeral=True,
            )
            return

        # Resolve which phase channels to create — only the chain up to --target.
        try:
            target_idx = _PHASE_CHAIN.index(target)
        except ValueError:
            await interaction.followup.send(
                f"`target` must be one of {_PHASE_CHAIN}. Got: {target!r}",
                ephemeral=True,
            )
            return
        phases = _PHASE_CHAIN[: target_idx + 1]

        parent = await _ensure_parent_category(guild, self.cfg.parent_category_name)
        category, channels = await _create_run_category(guild, parent, run_label, phases)

        # Sticky info message — we edit it as events arrive.
        info_em = discord.Embed(
            title=f"Run {run_label} — starting",
            description="Spawning `scripts/run_phase.py --json`...",
            color=discord.Color.blurple(),
        )
        info_msg = await channels["info"].send(embed=info_em)

        spec_url_list = (
            [u.strip() for u in spec_urls.split(",") if u.strip()] if spec_urls else None
        )
        keyword_list = (
            [k.strip() for k in keywords.split(",") if k.strip()] if keywords else None
        )

        env_overrides: dict[str, str] = {}
        if target_repo:
            env_overrides["TARGET_REPO"] = target_repo
        if target_commit:
            env_overrides["TARGET_COMMIT"] = target_commit

        try:
            handle = await start_run_phase(
                speca_repo_path=self.cfg.speca_repo_path,
                target=target,
                spec_urls=spec_url_list,
                keywords=keyword_list,
                scope_01a=scope_01a,
                env=env_overrides,
            )
        except FileNotFoundError as e:
            await channels["info"].send(
                f"Failed to spawn run_phase.py: `{e}`. Check `uv` is on PATH or set `$SPECA_PY`.",
            )
            await interaction.followup.send(
                "Run failed to start; see #info under the new category.",
                ephemeral=True,
            )
            return

        active = _ActiveRun(run_label, category, channels, info_msg, handle)
        self.active[run_label] = active

        await interaction.followup.send(
            f"Run `{run_label}` started — see {category.mention}.",
            ephemeral=False,
        )

        # Drive the event stream in a background task so the interaction returns
        # immediately. Errors inside the task are surfaced into #info.
        task = asyncio.create_task(self._consume_events(active))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _consume_events(self, active: _ActiveRun) -> None:
        try:
            async for event in active.handle.events():
                await self._dispatch_event(active, event)
        except Exception as e:
            await active.channels["info"].send(f"event stream error: `{e}`")
            log.exception("run %s event loop crashed", active.label)
        finally:
            rc = await active.handle.wait()
            await self._finalise(active, rc)
            self.active.pop(active.label, None)

    async def _dispatch_event(self, active: _ActiveRun, event: dict) -> None:
        line = phase_event_line(event)
        phase = event.get("phase") or event.get("phase_id")
        target_channel = (
            active.channels.get(phase) if isinstance(phase, str) else None
        ) or active.channels["info"]

        # Buffer recent lines per channel to avoid one-message-per-event flood.
        buf = active.buffers[target_channel.id]  # type: ignore[index]
        buf.append(line or "(event)")
        if len(buf) >= 5 or event.get("event") in {
            "phase-completed",
            "phase-failed",
            "pipeline-completed",
            "budget-exceeded",
            "circuit-breaker-tripped",
        }:
            await target_channel.send("```" + "\n".join(buf[-30:]) + "```")
            buf.clear()

        # Refresh the sticky info embed.
        if event.get("event") in {
            "pipeline-started",
            "phase-started",
            "phase-completed",
            "phase-failed",
            "pipeline-completed",
        }:
            em = discord.Embed(
                title=f"Run {active.label}",
                description=line,
                color=discord.Color.gold()
                if "fail" in (event.get("event") or "")
                else discord.Color.blurple(),
            )
            try:
                await active.info_message.edit(embed=em)
            except discord.HTTPException:
                # Edits can race against deletes — re-post if needed.
                active.info_message = await active.channels["info"].send(embed=em)

    async def _finalise(self, active: _ActiveRun, rc: int) -> None:
        # Flush any remaining buffers.
        for ch_id, buf in active.buffers.items():
            if not buf:
                continue
            channel = self.bot.get_channel(ch_id)
            if isinstance(channel, discord.TextChannel):
                await channel.send("```" + "\n".join(buf[-30:]) + "```")
        status = "completed" if rc == 0 else f"exited with code {rc}"
        await active.channels["info"].send(f"Pipeline {status}.")

    @app_commands.command(
        name="speca-status",
        description="Show currently running SPECA runs tracked by this bot.",
    )
    async def speca_status(self, interaction: discord.Interaction) -> None:
        if not self.active:
            await interaction.response.send_message(
                "No active runs.", ephemeral=True
            )
            return
        lines = [f"- `{label}` -> {a.category.mention}" for label, a in self.active.items()]
        await interaction.response.send_message(
            "Active runs:\n" + "\n".join(lines), ephemeral=True
        )

    @app_commands.command(
        name="speca-cancel",
        description="Cancel an active SPECA run by label.",
    )
    @app_commands.describe(label="Run label to cancel (see /speca-status).")
    async def speca_cancel(self, interaction: discord.Interaction, label: str) -> None:
        active = self.active.get(label)
        if active is None:
            await interaction.response.send_message(
                f"No active run with label `{label}`.", ephemeral=True
            )
            return
        active.handle.cancel()
        await interaction.response.send_message(
            f"Sent terminate signal to run `{label}`.", ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RunsCog(bot))
