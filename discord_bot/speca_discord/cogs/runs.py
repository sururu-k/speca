"""`/speca-run` slash command — spawn run_phase.py and mirror progress to Discord.

For each invocation:
  1. Mint a ``SPECA_RUN_ID`` upfront and persist a state record.
  2. Resolve / create the parent "SPECA" category.
  3. Create a per-run category ``Run <slug>`` under the parent.
  4. Create one channel per phase listed in --target chain (#01a, #01b, ...)
     plus a control `#info` channel (with Stop / Refresh buttons attached)
     and a `#chat` channel.
  5. Spawn ``scripts/run_phase.py --json`` as an async subprocess with the
     pre-minted run_id pinned via ``SPECA_RUN_ID``.
  6. Iterate NDJSON events; route them to the matching phase channel and
     edit the sticky #info embed in place.

Crash recovery (the load-bearing piece): every transition writes to the
``StateStore``. On bot startup the cog reads ``resumable_runs()`` and
re-attaches the Discord UI + respawns the subprocess for each one. The
orchestrator's resume.py skips already-processed items by scanning the
existing PARTIAL files, so the new subprocess picks up where the dead
one left off.

Termination policy: the **only** action that marks a run "stopped" (and
therefore prevents auto-resume) is the user clicking the Stop button or
running ``/speca-cancel``. A host crash, a SIGKILL, or a network blip
just suspends the run until next bot launch.
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
from ..panels.run_panel import RunControlView
from ..services.run_id import make_run_id
from ..services.speca_subprocess import RunPhaseHandle, start_run_phase
from ..services.state import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_STOPPED,
    ActiveRunRecord,
    StateStore,
    derive_default_state_path,
)

log = logging.getLogger(__name__)

# Phase id chain that run_phase.py exposes — used to derive which channels
# to create per run based on --target.
_PHASE_CHAIN = ("01a", "01b", "01e", "02c", "03", "04")


def _slugify(text: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_." else "-" for c in text)
    return cleaned.strip("-")[:60] or "run"


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
            name=phase,
            category=category,
            topic=f"Stream-JSON events from Phase {phase}",
        )
    channels["chat"] = await guild.create_text_channel(
        name="chat", category=category, topic="Ask Claude follow-up questions"
    )
    return category, channels


class _ActiveRun:
    """In-memory mirror of the persisted state — also holds the live handle."""

    __slots__ = (
        "buffers",
        "category",
        "channels",
        "handle",
        "info_message",
        "label",
        "speca_run_id",
    )

    def __init__(
        self,
        label: str,
        speca_run_id: str,
        category: discord.CategoryChannel,
        channels: dict[str, discord.TextChannel],
        info_message: discord.Message,
        handle: RunPhaseHandle,
    ) -> None:
        self.label = label
        self.speca_run_id = speca_run_id
        self.category = category
        self.channels = channels
        self.info_message = info_message
        self.handle = handle
        self.buffers: dict[int, list[str]] = defaultdict(list)


class RunsCog(commands.Cog):
    """Spawn + monitor + crash-recover SPECA runs from Discord."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.cfg = get_config()
        self.active: dict[str, _ActiveRun] = {}
        self.state = StateStore(derive_default_state_path(self.cfg.speca_repo_path))
        self._tasks: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------
    # /speca-run + helpers
    # ------------------------------------------------------------------

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

        try:
            target_idx = _PHASE_CHAIN.index(target)
        except ValueError:
            await interaction.followup.send(
                f"`target` must be one of {_PHASE_CHAIN}. Got: {target!r}",
                ephemeral=True,
            )
            return
        phases = _PHASE_CHAIN[: target_idx + 1]

        # Mint run_id BEFORE spawning so the state record (and the
        # subprocess) agree on which .speca/runs/<run-id>/ they target.
        speca_run_id = make_run_id(spec_slug=label or target_repo or "ad-hoc")

        args_payload: dict[str, object] = {
            "target": target,
            "target_repo": target_repo,
            "target_commit": target_commit,
            "spec_urls": spec_urls,
            "keywords": keywords,
            "scope_01a": scope_01a,
            "phases": list(phases),
        }

        parent = await _ensure_parent_category(guild, self.cfg.parent_category_name)
        category, channels = await _create_run_category(guild, parent, run_label, phases)

        info_em = discord.Embed(
            title=f"Run {run_label} — starting",
            description=f"Spawning `scripts/run_phase.py --json` (id `{speca_run_id}`)…",
            color=discord.Color.blurple(),
        )
        view = RunControlView(run_label, self)
        info_msg = await channels["info"].send(embed=info_em, view=view)

        # Persist the record now — even if spawning crashes, the bot can
        # surface the half-created run on next launch and let the operator
        # clean up via Stop.
        record = ActiveRunRecord.make(
            label=run_label,
            speca_run_id=speca_run_id,
            category_id=category.id,
            channel_ids={k: c.id for k, c in channels.items()},
            args=args_payload,
        )
        record.info_message_id = info_msg.id
        self.state.upsert(record)

        handle = await self._spawn_run_phase(args_payload, speca_run_id)
        if handle is None:
            await channels["info"].send(
                "Failed to spawn `run_phase.py`. Check `uv` on PATH or set `$SPECA_PY`."
            )
            self.state.update_status(run_label, STATUS_FAILED)
            await interaction.followup.send(
                "Run failed to start; see #info under the new category.",
                ephemeral=True,
            )
            return

        active = _ActiveRun(
            run_label, speca_run_id, category, channels, info_msg, handle
        )
        self.active[run_label] = active

        await interaction.followup.send(
            f"Run `{run_label}` started — see {category.mention}. Click "
            "**Stop run** in #info to terminate; a host crash will NOT stop "
            "the run (it will resume on next bot launch).",
            ephemeral=False,
        )

        self._spawn_consumer(active)

    async def _spawn_run_phase(
        self, args: dict[str, object], speca_run_id: str
    ) -> RunPhaseHandle | None:
        env_overrides: dict[str, str] = {"SPECA_RUN_ID": speca_run_id}
        if isinstance(args.get("target_repo"), str):
            env_overrides["TARGET_REPO"] = args["target_repo"]  # type: ignore[assignment]
        if isinstance(args.get("target_commit"), str):
            env_overrides["TARGET_COMMIT"] = args["target_commit"]  # type: ignore[assignment]
        try:
            return await start_run_phase(
                speca_repo_path=self.cfg.speca_repo_path,
                target=str(args.get("target") or "04"),
                spec_urls=_split_csv(args.get("spec_urls")),
                keywords=_split_csv(args.get("keywords")),
                scope_01a=(
                    str(args["scope_01a"])
                    if isinstance(args.get("scope_01a"), str)
                    else None
                ),
                env=env_overrides,
            )
        except FileNotFoundError as e:
            log.error("spawn run_phase.py failed: %s", e)
            return None

    def _spawn_consumer(self, active: _ActiveRun) -> None:
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

        buf = active.buffers[target_channel.id]
        buf.append(line or "(event)")
        is_terminal = event.get("event") in {
            "phase-completed",
            "phase-failed",
            "pipeline-completed",
            "budget-exceeded",
            "circuit-breaker-tripped",
        }
        if len(buf) >= 5 or is_terminal:
            await target_channel.send("```" + "\n".join(buf[-30:]) + "```")
            buf.clear()

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
                color=(
                    discord.Color.gold()
                    if "fail" in (event.get("event") or "")
                    else discord.Color.blurple()
                ),
            )
            try:
                await active.info_message.edit(embed=em)
            except discord.HTTPException:
                active.info_message = await active.channels["info"].send(embed=em)
                self.state.update_info_message(active.label, active.info_message.id)

    async def _finalise(self, active: _ActiveRun, rc: int) -> None:
        for ch_id, buf in active.buffers.items():
            if not buf:
                continue
            channel = self.bot.get_channel(ch_id)
            if isinstance(channel, discord.TextChannel):
                await channel.send("```" + "\n".join(buf[-30:]) + "```")
        record = self.state.get(active.label)
        new_status = (
            STATUS_STOPPED
            if record is not None and record.status == STATUS_STOPPED
            else (STATUS_COMPLETED if rc == 0 else STATUS_FAILED)
        )
        self.state.update_status(active.label, new_status)
        status_desc = "completed" if rc == 0 else f"exited with code {rc}"
        if new_status == STATUS_STOPPED:
            status_desc = "stopped (operator)"
        await active.channels["info"].send(f"Pipeline {status_desc}.")

    # ------------------------------------------------------------------
    # Stop button callback
    # ------------------------------------------------------------------

    async def request_stop(
        self, interaction: discord.Interaction, run_label: str
    ) -> None:
        active = self.active.get(run_label)
        if active is None:
            # Run is in state file but not live — mark stopped so a future
            # bot launch doesn't auto-resume.
            self.state.update_status(run_label, STATUS_STOPPED)
            await interaction.response.send_message(
                f"No live subprocess for `{run_label}`; record marked stopped.",
                ephemeral=True,
            )
            return
        # Mark stopped BEFORE terminating so a race against the finalize
        # path sees the correct status.
        self.state.update_status(run_label, STATUS_STOPPED)
        active.handle.cancel()
        await interaction.response.send_message(
            f"Sent terminate signal to `{run_label}` and marked it stopped.",
            ephemeral=True,
        )

    async def refresh_status(
        self, interaction: discord.Interaction, run_label: str
    ) -> None:
        record = self.state.get(run_label)
        if record is None:
            await interaction.response.send_message(
                f"No record for `{run_label}` — it may have been forgotten or "
                "the state file was reset.",
                ephemeral=True,
            )
            return
        live = "live" if run_label in self.active else "not currently live"
        em = discord.Embed(
            title=f"Run {run_label}",
            color=discord.Color.blurple(),
            description=(
                f"**status:** {record.status}\n"
                f"**speca_run_id:** `{record.speca_run_id}`\n"
                f"**started_at:** {record.started_at}\n"
                f"**last_update:** {record.last_update_at}\n"
                f"**subprocess:** {live}"
            ),
        )
        await interaction.response.send_message(embed=em, ephemeral=True)

    # ------------------------------------------------------------------
    # Auto-resume — called from main on_ready after the client is ready.
    # ------------------------------------------------------------------

    async def resume_persisted_runs(self) -> None:
        """Re-attach to every ``status=running`` record on disk.

        Spawns a fresh run_phase.py subprocess with the original
        ``SPECA_RUN_ID`` so the orchestrator's per-item resume skips
        already-processed items. Tolerates missing Discord channels
        (operator deleted a category) by marking the record failed.
        """
        records = self.state.resumable_runs()
        if not records:
            return
        guild = self.bot.get_guild(self.cfg.guild_id)
        if guild is None:
            log.warning(
                "auto-resume: guild %s not visible; the bot may need to be "
                "re-invited, or the configured guild id is wrong.",
                self.cfg.guild_id,
            )
            return
        for record in records:
            try:
                await self._resume_one(guild, record)
            except Exception as e:
                log.exception("auto-resume failed for %s", record.label)
                self.state.update_status(record.label, STATUS_FAILED)
                await self._announce_resume_failure(guild, record, e)

    async def _resume_one(
        self, guild: discord.Guild, record: ActiveRunRecord
    ) -> None:
        category = guild.get_channel(record.category_id)
        if not isinstance(category, discord.CategoryChannel):
            log.warning(
                "auto-resume: category %s for run %s is gone; marking failed",
                record.category_id,
                record.label,
            )
            self.state.update_status(record.label, STATUS_FAILED)
            return
        channels: dict[str, discord.TextChannel] = {}
        for key, ch_id in record.channel_ids.items():
            ch = guild.get_channel(ch_id)
            if isinstance(ch, discord.TextChannel):
                channels[key] = ch
        if "info" not in channels:
            log.warning(
                "auto-resume: #info channel for %s missing; marking failed",
                record.label,
            )
            self.state.update_status(record.label, STATUS_FAILED)
            return

        # Reattach the persistent control View so Stop/Refresh keep working.
        self.bot.add_view(RunControlView(record.label, self))

        info_em = discord.Embed(
            title=f"Run {record.label} — resuming",
            description=(
                f"Bot restarted; re-spawning `run_phase.py` with the same "
                f"`SPECA_RUN_ID={record.speca_run_id}` so the orchestrator's "
                "resume picks up where it left off."
            ),
            color=discord.Color.blurple(),
        )
        try:
            info_msg = (
                await channels["info"].fetch_message(record.info_message_id)
                if record.info_message_id
                else None
            )
        except (discord.NotFound, discord.HTTPException):
            info_msg = None
        if info_msg is None:
            info_msg = await channels["info"].send(embed=info_em)
            self.state.update_info_message(record.label, info_msg.id)
        else:
            try:
                await info_msg.edit(embed=info_em)
            except discord.HTTPException:
                pass

        handle = await self._spawn_run_phase(record.args, record.speca_run_id)
        if handle is None:
            self.state.update_status(record.label, STATUS_FAILED)
            await channels["info"].send(
                "Auto-resume could not spawn `run_phase.py` — marking failed."
            )
            return
        active = _ActiveRun(
            record.label,
            record.speca_run_id,
            category,
            channels,
            info_msg,
            handle,
        )
        self.active[record.label] = active
        self._spawn_consumer(active)

    async def _announce_resume_failure(
        self,
        guild: discord.Guild,
        record: ActiveRunRecord,
        error: Exception,
    ) -> None:
        info_id = record.channel_ids.get("info")
        if info_id is None:
            return
        ch = guild.get_channel(info_id)
        if isinstance(ch, discord.TextChannel):
            await ch.send(
                f"Auto-resume failed for `{record.label}`: `{error}`. "
                "Click **Stop run** to clear the record."
            )

    # ------------------------------------------------------------------
    # Slash housekeeping
    # ------------------------------------------------------------------

    @app_commands.command(
        name="speca-status",
        description="Show currently running / persisted SPECA runs.",
    )
    async def speca_status(self, interaction: discord.Interaction) -> None:
        live = set(self.active)
        records = self.state.all_runs()
        if not records:
            await interaction.response.send_message(
                "No active or persisted runs.", ephemeral=True
            )
            return
        lines: list[str] = []
        for r in records:
            marker = "live" if r.label in live else r.status
            lines.append(f"- `{r.label}` ({marker}) -> `{r.speca_run_id}`")
        await interaction.response.send_message(
            "Tracked runs:\n" + "\n".join(lines), ephemeral=True
        )

    @app_commands.command(
        name="speca-cancel",
        description="Cancel an active SPECA run by label (stops auto-resume).",
    )
    @app_commands.describe(label="Run label to cancel (see /speca-status).")
    async def speca_cancel(self, interaction: discord.Interaction, label: str) -> None:
        await self.request_stop(interaction, label)

    @app_commands.command(
        name="speca-forget",
        description="Drop a completed/stopped run from the state file.",
    )
    @app_commands.describe(label="Run label to forget.")
    async def speca_forget(self, interaction: discord.Interaction, label: str) -> None:
        record = self.state.get(label)
        if record is None:
            await interaction.response.send_message(
                f"No record for `{label}`.", ephemeral=True
            )
            return
        if record.status == STATUS_RUNNING and label in self.active:
            await interaction.response.send_message(
                f"`{label}` is still running; stop it first.", ephemeral=True
            )
            return
        self.state.forget(label)
        await interaction.response.send_message(
            f"Forgotten record for `{label}`.", ephemeral=True
        )


def _split_csv(value: object) -> list[str] | None:
    if not isinstance(value, str):
        return None
    items = [v.strip() for v in value.split(",") if v.strip()]
    return items or None


async def setup(bot: commands.Bot) -> None:
    cog = RunsCog(bot)
    await bot.add_cog(cog)
    # Stash the cog on the bot so main.on_ready can call resume_persisted_runs.
    bot.runs_cog = cog  # type: ignore[attr-defined]
