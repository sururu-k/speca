"""archive_reader — list/show/parse against synthetic .speca/runs/ fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from speca_discord.services.archive_reader import (
    list_runs,
    parse_run_id_timestamp,
    show_run,
)


def _seed(run_dir: Path, manifest: dict, *, with_phases: list[str] | None = None) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    for phase in with_phases or []:
        phase_dir = run_dir / "phases" / phase
        (phase_dir / "partials").mkdir(parents=True, exist_ok=True)
        (phase_dir / "partials" / "p.json").write_text("{}", encoding="utf-8")


class TestListRuns:
    def test_empty_root_returns_empty(self, tmp_path: Path) -> None:
        assert list_runs(tmp_path / "nope") == []
        (tmp_path / "empty").mkdir()
        assert list_runs(tmp_path / "empty") == []

    def test_reads_one_ok_run(self, tmp_path: Path) -> None:
        _seed(
            tmp_path / "2026-05-13T12-00-00Z-abc-eip-7825",
            {
                "run_id": "2026-05-13T12-00-00Z-abc-eip-7825",
                "started_at": "2026-05-13T12:00:00Z",
                "ended_at": "2026-05-13T12:02:00Z",
                "phases_completed": ["01a", "01b"],
                "cost_usd_total": 0.4,
                "notes": "ok",
            },
        )
        rows = list_runs(tmp_path)
        assert len(rows) == 1
        assert rows[0].status == "ok"
        assert rows[0].phases_completed == ("01a", "01b")
        assert rows[0].cost_usd_total == pytest.approx(0.4)

    def test_floats_unreadable_to_bottom(self, tmp_path: Path) -> None:
        _seed(
            tmp_path / "2026-05-13T12-00-00Z-abc-ok",
            {
                "run_id": "2026-05-13T12-00-00Z-abc-ok",
                "started_at": "2026-05-13T12:00:00Z",
                "notes": "ok",
            },
        )
        broken = tmp_path / "2026-05-13T13-00-00Z-zzz-broken"
        broken.mkdir()
        (broken / "manifest.json").write_text("not json", encoding="utf-8")
        rows = list_runs(tmp_path)
        assert [r.status for r in rows] == ["ok", "unreadable"]

    def test_skips_dot_dirs(self, tmp_path: Path) -> None:
        (tmp_path / ".trash").mkdir()
        _seed(
            tmp_path / "2026-05-13T12-00-00Z-abc-ok",
            {"run_id": "2026-05-13T12-00-00Z-abc-ok", "started_at": "2026-05-13T12:00:00Z"},
        )
        rows = list_runs(tmp_path)
        assert [r.run_id for r in rows] == ["2026-05-13T12-00-00Z-abc-ok"]


class TestShowRun:
    def test_returns_none_for_unknown(self, tmp_path: Path) -> None:
        assert show_run(tmp_path, "nope") is None

    def test_includes_phase_breakdown(self, tmp_path: Path) -> None:
        rid = "2026-05-13T12-00-00Z-abc-rid"
        _seed(
            tmp_path / rid,
            {
                "run_id": rid,
                "started_at": "2026-05-13T12:00:00Z",
                "phases_completed": ["01a", "01b"],
                "spec_sources": ["https://example/spec"],
            },
            with_phases=["01a", "01b"],
        )
        detail = show_run(tmp_path, rid)
        assert detail is not None
        assert {p.phase for p in detail.phase_breakdown} == {"01a", "01b"}
        assert all(p.partial_count == 1 for p in detail.phase_breakdown)
        assert detail.spec_sources == ("https://example/spec",)


class TestRunIdTimestamp:
    def test_parses_canonical_format(self) -> None:
        dt = parse_run_id_timestamp("2026-05-13T12-00-00Z-abc1234-slug-xxxx")
        assert dt is not None
        assert dt.year == 2026 and dt.month == 5 and dt.day == 13
        assert dt.hour == 12

    def test_returns_none_on_garbage(self) -> None:
        assert parse_run_id_timestamp("nope") is None
        assert parse_run_id_timestamp("2026-05-13") is None
