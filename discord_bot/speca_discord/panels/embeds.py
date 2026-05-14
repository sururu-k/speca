"""Embed builders shared across cogs.

We centralise the formatting so layout drift is impossible — every cog
renders runs / findings / phases through the same builders.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

import discord

from ..services.archive_reader import (
    PhaseSummary,
    RunDetail,
    RunSummary,
)
from ..services.findings_reader import Finding

_STATUS_COLORS = {
    "ok": discord.Color.green(),
    "error": discord.Color.red(),
    "pending": discord.Color.gold(),
    "unreadable": discord.Color.dark_orange(),
    "unknown": discord.Color.greyple(),
}


def _color_for(status: str) -> discord.Color:
    return _STATUS_COLORS.get(status, discord.Color.blurple())


def _format_cost(usd: float) -> str:
    if usd == 0:
        return "—"
    if usd < 0.01:
        return "$<0.01"
    return f"${usd:.2f}"


def _format_ts(ts: str) -> str:
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        return ts


def run_list_embed(rows: Iterable[RunSummary], archive_root: str) -> discord.Embed:
    rows = list(rows)
    em = discord.Embed(
        title="SPECA corpus — runs",
        description=f"{len(rows)} run(s) under `{archive_root}`",
        color=discord.Color.blurple(),
    )
    if not rows:
        em.add_field(
            name="(empty)",
            value="No runs yet. Use `/speca-run` to start one.",
            inline=False,
        )
        return em
    # Discord embed fields cap at 1024 chars; render up to 10 rows per embed.
    lines: list[str] = []
    for r in rows[:10]:
        status = r.status.upper()
        phases = ",".join(r.phases_completed) if r.phases_completed else "—"
        lines.append(
            f"`{status:<10}` {_format_ts(r.started_at):<23} {phases:<14} "
            f"{_format_cost(r.cost_usd_total):<7} `{r.run_id[-32:]}`"
        )
    em.add_field(
        name="status / started_at / phases / cost / run_id",
        value="```" + "\n".join(lines) + "```",
        inline=False,
    )
    if len(rows) > 10:
        em.set_footer(text=f"+ {len(rows) - 10} more — refine with /corpus-show")
    return em


def run_show_embed(detail: RunDetail) -> discord.Embed:
    s = detail.summary
    em = discord.Embed(
        title=f"Run {s.run_id}",
        color=_color_for(s.status),
        description=f"Status: **{s.status.upper()}**" + (
            f"\nNotes: `{(s.notes or '').strip().splitlines()[0][:200]}`"
            if s.notes and s.notes.strip().lower() != "ok"
            else ""
        ),
    )
    em.add_field(name="started_at", value=_format_ts(s.started_at) or "—", inline=True)
    em.add_field(name="ended_at", value=_format_ts(s.ended_at or "") or "—", inline=True)
    em.add_field(name="commit", value=s.speca_commit or "—", inline=True)
    em.add_field(name="cost_total", value=_format_cost(s.cost_usd_total), inline=True)
    em.add_field(name="target_repo", value=s.target_repo or "—", inline=True)
    em.add_field(
        name="phases_completed",
        value=", ".join(s.phases_completed) or "—",
        inline=True,
    )
    if detail.spec_sources:
        em.add_field(
            name="spec_sources",
            value="\n".join(f"- {u}" for u in detail.spec_sources[:6])
            + (
                f"\n... +{len(detail.spec_sources) - 6} more"
                if len(detail.spec_sources) > 6
                else ""
            ),
            inline=False,
        )
    if detail.phase_breakdown:
        em.add_field(
            name="phase breakdown",
            value="```"
            + _render_phase_table(detail.phase_breakdown)
            + "```",
            inline=False,
        )
    return em


def _render_phase_table(rows: tuple[PhaseSummary, ...]) -> str:
    header = f"{'phase':<8}{'partials':<10}{'logs':<6}{'graphs':<8}{'cost':<8}"
    body = "\n".join(
        f"{r.phase:<8}{r.partial_count:<10}{r.log_count:<6}{r.graph_count:<8}"
        + (_format_cost(r.cost_usd) if r.cost_usd is not None else "—")
        for r in rows
    )
    return f"{header}\n{body}"


def phase_event_line(event: dict) -> str:
    """One-liner for the live log channel — keep it compact, ASCII only."""
    kind = event.get("event") or event.get("type") or "?"
    phase = event.get("phase") or event.get("phase_id")
    if kind == "phase-started":
        return f"[START] phase {phase}"
    if kind == "phase-completed":
        d = event.get("duration_s")
        n = event.get("total_results")
        if isinstance(d, (int, float)):
            return f"[OK]    phase {phase} completed ({d:.1f}s, {n} results)"
        return f"[OK]    phase {phase} completed"
    if kind == "phase-failed":
        return f"[FAIL]  phase {phase}: {event.get('reason', 'unknown')}"
    if kind == "budget-exceeded":
        return (
            f"[BUDGET] {phase} exceeded: "
            f"${event.get('cost_usd', 0):.2f} / ${event.get('max_budget_usd', 0):.2f}"
        )
    if kind == "circuit-breaker-tripped":
        return f"[BREAK]  circuit breaker tripped on {phase}"
    if kind == "pipeline-started":
        ph = event.get("phases") or []
        return f"[START] pipeline ({','.join(ph)})"
    if kind == "pipeline-completed":
        return f"[OK]    pipeline completed in {event.get('duration_s', 0):.1f}s"
    if kind == "decorative":
        line = str(event.get("line", "")).strip()
        return f"   - {line[:200]}" if line else ""
    return f"- {kind} {phase or ''}".rstrip()


def control_panel_embed() -> discord.Embed:
    em = discord.Embed(
        title="SPECA Control Panel",
        description=(
            "Use the buttons below to launch an audit run or browse the "
            "corpus. Slash commands cover everything the buttons do plus "
            "fine-grained flags."
        ),
        color=discord.Color.blurple(),
    )
    em.add_field(
        name="Slash commands",
        value=(
            "`/speca-run` — start a Phase 04 audit on a target repo\n"
            "`/speca-status` — current run state\n"
            "`/corpus-list` — runs in the archive\n"
            "`/corpus-show <run-id>` — manifest + phase breakdown\n"
            "`/corpus-export <run-id>` — packaged slice for sharing\n"
            "`/corpus-gc --older-than <dur>` — soft-delete old runs\n"
            "`/findings` — browse Phase 04 findings\n"
            "`/ask` — chat with Claude (subscription or API key)"
        ),
        inline=False,
    )
    em.set_footer(text="SPECA Discord bot · slash commands + buttons")
    return em


def finding_embed(f: Finding, index: int, total: int) -> discord.Embed:
    em = discord.Embed(
        title=f"[{f.severity}] {f.title or f.property_id}",
        color=_finding_color(f.severity),
        description=(
            f"**Verdict:** `{f.verdict}`\n"
            f"**Property:** `{f.property_id}`\n"
            + (f"**Source:** `{f.source_file}:{f.source_lines}`\n" if f.source_file else "")
        ),
    )
    if f.summary:
        em.add_field(name="Summary", value=_clamp(f.summary, 1024), inline=False)
    em.set_footer(text=f"finding {index + 1} / {total}")
    return em


_SEVERITY_COLORS = {
    "CRITICAL": discord.Color.dark_red(),
    "HIGH": discord.Color.red(),
    "MEDIUM": discord.Color.orange(),
    "LOW": discord.Color.gold(),
    "INFORMATIONAL": discord.Color.greyple(),
    "INFO": discord.Color.greyple(),
}


def _finding_color(severity: str) -> discord.Color:
    return _SEVERITY_COLORS.get(severity.upper(), discord.Color.blurple())


def _clamp(s: str, limit: int) -> str:
    return s if len(s) <= limit else s[: limit - 1] + "…"
