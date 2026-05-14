"""Embed builders — make sure no surprise None-handling crashes.

The Ink-equivalent visual is hard to assert on, so we only verify the
embed objects round-trip and contain expected substrings.
"""

from __future__ import annotations

from speca_discord.panels.embeds import (
    control_panel_embed,
    phase_event_line,
    run_list_embed,
    run_show_embed,
)
from speca_discord.services.archive_reader import (
    PhaseSummary,
    RunDetail,
    RunSummary,
)


def _row(**kw: object) -> RunSummary:
    base: dict[str, object] = {
        "run_id": "rid",
        "run_dir": __import__("pathlib").Path("/tmp"),
        "manifest_path": __import__("pathlib").Path("/tmp/m"),
        "started_at": "2026-05-13T12:00:00Z",
        "ended_at": "2026-05-13T12:02:00Z",
        "status": "ok",
        "phases_completed": ("01a",),
        "cost_usd_total": 0.1,
        "speca_commit": "abc",
        "target_repo": None,
        "notes": "ok",
    }
    base.update(kw)
    return RunSummary(**base)  # type: ignore[arg-type]


class TestPhaseEventLine:
    def test_phase_started(self) -> None:
        line = phase_event_line({"event": "phase-started", "phase": "01a"})
        assert "phase 01a" in line
        assert line.startswith("[START]")

    def test_phase_completed(self) -> None:
        line = phase_event_line(
            {"event": "phase-completed", "phase": "01a", "duration_s": 12.3, "total_results": 5}
        )
        assert "01a" in line
        assert "12.3" in line
        assert "5 results" in line

    def test_phase_failed(self) -> None:
        line = phase_event_line(
            {"event": "phase-failed", "phase": "01a", "reason": "boom"}
        )
        assert "boom" in line
        assert "01a" in line

    def test_decorative_passthrough(self) -> None:
        line = phase_event_line({"event": "decorative", "line": "ok then"})
        assert "ok then" in line

    def test_no_emoji_in_output(self) -> None:
        # Memory rule: no emoji in bot output. Sanity-check every event
        # rendering for the canonical pictographic glyphs we used to use.
        forbidden = {"✓", "✗", "▶", "·"}
        for kind in (
            "phase-started",
            "phase-completed",
            "phase-failed",
            "pipeline-started",
            "pipeline-completed",
            "budget-exceeded",
            "circuit-breaker-tripped",
        ):
            line = phase_event_line({"event": kind, "phase": "01a"})
            for g in forbidden:
                assert g not in line, f"glyph {g!r} leaked into {kind!r}: {line!r}"


class TestRunListEmbed:
    def test_empty_state_has_hint(self) -> None:
        em = run_list_embed([], archive_root="/tmp/x")
        body = "\n".join(f.value for f in em.fields)
        assert "/speca-run" in body

    def test_one_row_renders(self) -> None:
        em = run_list_embed([_row()], archive_root="/tmp/x")
        body = "\n".join(f.value for f in em.fields)
        assert "OK" in body
        assert "01a" in body

    def test_more_than_10_rows_footer(self) -> None:
        rows = [_row(run_id=f"r{i}") for i in range(15)]
        em = run_list_embed(rows, archive_root="/tmp/x")
        assert em.footer.text and "+ 5 more" in em.footer.text


class TestRunShowEmbed:
    def test_renders_phase_table(self) -> None:
        detail = RunDetail(
            summary=_row(),
            phase_breakdown=(
                PhaseSummary(
                    phase="01a",
                    partial_count=1,
                    log_count=1,
                    graph_count=0,
                    cost_usd=0.05,
                ),
            ),
            spec_sources=("https://e/x",),
        )
        em = run_show_embed(detail)
        body = "\n".join(str(f.value) for f in em.fields)
        assert "01a" in body
        assert "https://e/x" in body


class TestControlPanelEmbed:
    def test_has_all_slash_commands(self) -> None:
        em = control_panel_embed()
        body = "\n".join(str(f.value) for f in em.fields)
        for cmd in (
            "/speca-run",
            "/speca-status",
            "/corpus-list",
            "/corpus-show",
            "/corpus-export",
            "/corpus-gc",
            "/findings",
            "/ask",
        ):
            assert cmd in body, f"control panel missing reference to {cmd}"
