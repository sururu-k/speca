"""Loader for Phase 04 findings — mirrors cli/src/lib/findings/loader.ts.

Reads ``outputs/04_PARTIAL_*.json`` (or a passed glob), merges by
``property_id`` (last write wins), and produces a flat list of typed
``Finding`` objects for the Discord pagination cog.
"""

from __future__ import annotations

import glob
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class Finding:
    property_id: str
    severity: str
    verdict: str
    title: str
    summary: str
    source_file: str | None
    source_lines: str | None
    raw: dict[str, Any]


def _extract_finding(d: dict[str, Any]) -> Finding | None:
    pid = d.get("property_id") or d.get("id")
    if not isinstance(pid, str):
        return None
    severity = str(d.get("severity") or d.get("calibrated_severity") or "UNKNOWN")
    verdict = str(d.get("verdict") or d.get("final_verdict") or "UNKNOWN")
    title = str(d.get("title") or d.get("text") or "")
    summary = str(d.get("summary") or d.get("description") or "")
    source = d.get("source") or {}
    if isinstance(source, dict):
        sf = source.get("file") or source.get("source_file")
        sl = source.get("lines") or source.get("line_range")
        source_file = str(sf) if isinstance(sf, str) else None
        source_lines = str(sl) if isinstance(sl, (str, int)) else None
    else:
        source_file = None
        source_lines = None
    return Finding(
        property_id=pid,
        severity=severity,
        verdict=verdict,
        title=title,
        summary=summary,
        source_file=source_file,
        source_lines=source_lines,
        raw=d,
    )


def load_findings(
    output_dir: Path,
    pattern: str = "04_PARTIAL_*.json",
) -> list[Finding]:
    """Return findings de-duped by ``property_id``, last write wins.

    Files are visited in mtime order so the freshest partial overrides
    older ones for the same property.
    """
    paths = sorted(glob.glob(str(output_dir / pattern)), key=lambda p: Path(p).stat().st_mtime)
    by_id: dict[str, Finding] = {}
    for p in paths:
        try:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        items = data.get("results") or data.get("findings") or data.get("items") or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            f = _extract_finding(item)
            if f is None:
                continue
            by_id[f.property_id] = f
    return list(by_id.values())


_SEVERITY_RANK = {
    "CRITICAL": 0,
    "HIGH": 1,
    "MEDIUM": 2,
    "LOW": 3,
    "INFORMATIONAL": 4,
    "INFO": 4,
    "UNKNOWN": 5,
}


def sort_by_severity(findings: list[Finding]) -> list[Finding]:
    return sorted(
        findings,
        key=lambda f: (
            _SEVERITY_RANK.get(f.severity.upper(), 99),
            f.property_id,
        ),
    )
