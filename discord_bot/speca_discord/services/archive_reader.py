"""Read-only helpers for the ``.speca/runs/<run-id>/`` per-run archive.

The bot mirrors a slice of the cli/src/lib/corpus/ logic in Python so list /
show panels don't need a subprocess round-trip. For destructive operations
(export, gc) we still shell out to ``speca corpus`` to keep the redaction /
soft-delete behaviour in one place.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class RunSummary:
    run_id: str
    run_dir: Path
    manifest_path: Path
    started_at: str
    ended_at: str | None
    status: str  # ok / error / pending / unknown / unreadable
    phases_completed: tuple[str, ...]
    cost_usd_total: float
    speca_commit: str
    target_repo: str | None
    notes: str | None
    unreadable_reason: str | None = None


@dataclass(slots=True)
class PhaseSummary:
    phase: str
    partial_count: int
    log_count: int
    graph_count: int
    cost_usd: float | None


@dataclass(slots=True)
class RunDetail:
    summary: RunSummary
    phase_breakdown: tuple[PhaseSummary, ...] = field(default_factory=tuple)
    spec_sources: tuple[str, ...] = field(default_factory=tuple)


def _derive_status(notes: str | None, ended_at: str | None) -> str:
    n = (notes or "").strip().lower()
    if n == "ok":
        return "ok"
    if n.startswith("error"):
        return "error"
    if not ended_at:
        return "pending"
    return "unknown"


def _read_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def list_runs(archive_root: Path) -> list[RunSummary]:
    """Mirror of cli/src/lib/corpus/runs.ts:listRuns.

    Sort: readable rows desc by started_at, unreadable floats to bottom.
    """
    if not archive_root.exists():
        return []
    rows: list[RunSummary] = []
    for child in archive_root.iterdir():
        if child.name.startswith("."):
            continue
        if not child.is_dir():
            continue
        manifest_path = child / "manifest.json"
        try:
            m = _read_manifest(manifest_path)
            target_info = m.get("target_info") or {}
            target_repo = (
                target_info.get("target_repo")
                if isinstance(target_info, dict)
                else None
            )
            rows.append(
                RunSummary(
                    run_id=m.get("run_id", child.name),
                    run_dir=child,
                    manifest_path=manifest_path,
                    started_at=m.get("started_at", ""),
                    ended_at=m.get("ended_at"),
                    status=_derive_status(m.get("notes"), m.get("ended_at")),
                    phases_completed=tuple(m.get("phases_completed", [])),
                    cost_usd_total=float(m.get("cost_usd_total") or 0.0),
                    speca_commit=m.get("speca_commit", ""),
                    target_repo=target_repo if isinstance(target_repo, str) else None,
                    notes=m.get("notes"),
                )
            )
        except (OSError, ValueError) as exc:
            rows.append(
                RunSummary(
                    run_id=child.name,
                    run_dir=child,
                    manifest_path=manifest_path,
                    started_at="",
                    ended_at=None,
                    status="unreadable",
                    phases_completed=(),
                    cost_usd_total=0.0,
                    speca_commit="",
                    target_repo=None,
                    notes=None,
                    unreadable_reason=str(exc).splitlines()[0],
                )
            )

    def _key(r: RunSummary) -> tuple[int, str]:
        # readable=0, unreadable=1 ; tie-break by started_at desc for
        # readable, run_id asc for unreadable.
        if r.status == "unreadable":
            return (1, r.run_id)
        return (0, _invert_lex(r.started_at))

    rows.sort(key=_key)
    return rows


def _invert_lex(s: str) -> str:
    # Cheap descending sort key: invert byte order.
    return "".join(chr(0x10FFFF - ord(c)) if ord(c) < 0x10FFFF else c for c in s)


def show_run(archive_root: Path, run_id: str) -> RunDetail | None:
    run_dir = archive_root / run_id
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    m = _read_manifest(manifest_path)
    target_info = m.get("target_info") or {}
    target_repo = (
        target_info.get("target_repo") if isinstance(target_info, dict) else None
    )
    summary = RunSummary(
        run_id=m.get("run_id", run_id),
        run_dir=run_dir,
        manifest_path=manifest_path,
        started_at=m.get("started_at", ""),
        ended_at=m.get("ended_at"),
        status=_derive_status(m.get("notes"), m.get("ended_at")),
        phases_completed=tuple(m.get("phases_completed", [])),
        cost_usd_total=float(m.get("cost_usd_total") or 0.0),
        speca_commit=m.get("speca_commit", ""),
        target_repo=target_repo if isinstance(target_repo, str) else None,
        notes=m.get("notes"),
    )
    breakdown: list[PhaseSummary] = []
    for phase in summary.phases_completed:
        phase_dir = run_dir / "phases" / phase
        breakdown.append(
            PhaseSummary(
                phase=phase,
                partial_count=_count_files(phase_dir / "partials"),
                log_count=_count_files(phase_dir / "logs"),
                graph_count=_count_graphs(phase_dir / "graphs"),
                cost_usd=_read_phase_cost(phase_dir / "cost.json"),
            )
        )
    return RunDetail(
        summary=summary,
        phase_breakdown=tuple(breakdown),
        spec_sources=tuple(m.get("spec_sources", [])),
    )


def _count_files(d: Path) -> int:
    try:
        return sum(1 for _ in d.iterdir())
    except (OSError, FileNotFoundError):
        return 0


def _count_graphs(d: Path) -> int:
    n = 0
    try:
        for batch in d.iterdir():
            if not batch.is_dir():
                continue
            for spec in batch.iterdir():
                if not spec.is_dir():
                    continue
                n += sum(1 for f in spec.iterdir() if f.suffix == ".mmd")
    except (OSError, FileNotFoundError):
        return 0
    return n


def _read_phase_cost(path: Path) -> float | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        v = data.get("total_cost_usd")
        return float(v) if isinstance(v, (int, float)) else None
    except (OSError, ValueError):
        return None


_RUN_ID_TS_RE = __import__("re").compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})T(?P<hh>\d{2})-(?P<mm>\d{2})-(?P<ss>\d{2})Z"
)


def parse_run_id_timestamp(run_id: str) -> datetime | None:
    """Best-effort: parse the leading ``YYYY-MM-DDTHH-MM-SSZ`` segment.

    The on-disk run-id format uses ``-`` for time-of-day separators (so the
    string is a valid path segment on Windows); we rebuild it into a real
    ISO-8601 string for ``datetime.fromisoformat`` before parsing.
    """
    m = _RUN_ID_TS_RE.match(run_id)
    if not m:
        return None
    try:
        return datetime.fromisoformat(
            f"{m['date']}T{m['hh']}:{m['mm']}:{m['ss']}+00:00"
        )
    except ValueError:
        return None
