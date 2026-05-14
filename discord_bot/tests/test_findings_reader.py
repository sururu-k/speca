"""findings_reader — merge by property_id, sort by severity, edge cases."""

from __future__ import annotations

import json
from pathlib import Path

from speca_discord.services.findings_reader import (
    load_findings,
    sort_by_severity,
)


def _write_partial(path: Path, items: list[dict]) -> None:
    path.write_text(json.dumps({"results": items}), encoding="utf-8")


def test_empty_dir_returns_empty(tmp_path: Path) -> None:
    assert load_findings(tmp_path) == []


def test_loads_single_partial(tmp_path: Path) -> None:
    _write_partial(
        tmp_path / "04_PARTIAL_W0B0_1.json",
        [
            {
                "property_id": "PROP-1",
                "severity": "High",
                "verdict": "CONFIRMED_VULNERABILITY",
                "title": "Reentrancy",
                "summary": "long description",
            }
        ],
    )
    fs = load_findings(tmp_path)
    assert len(fs) == 1
    assert fs[0].property_id == "PROP-1"
    assert fs[0].severity == "High"


def test_last_write_wins_on_dup(tmp_path: Path) -> None:
    _write_partial(
        tmp_path / "04_PARTIAL_W0B0_1.json",
        [{"property_id": "X", "severity": "Low", "title": "old"}],
    )
    p2 = tmp_path / "04_PARTIAL_W0B0_2.json"
    _write_partial(
        p2,
        [{"property_id": "X", "severity": "High", "title": "new"}],
    )
    # ensure mtime ordering is deterministic
    import os
    older = (tmp_path / "04_PARTIAL_W0B0_1.json").stat().st_mtime
    os.utime(p2, (older + 100, older + 100))
    fs = load_findings(tmp_path)
    assert len(fs) == 1
    assert fs[0].title == "new"


def test_sort_by_severity_orders_critical_first() -> None:
    from speca_discord.services.findings_reader import Finding

    fs = [
        Finding(
            property_id="b",
            severity="Low",
            verdict="x",
            title="",
            summary="",
            source_file=None,
            source_lines=None,
            raw={},
        ),
        Finding(
            property_id="a",
            severity="Critical",
            verdict="x",
            title="",
            summary="",
            source_file=None,
            source_lines=None,
            raw={},
        ),
        Finding(
            property_id="c",
            severity="High",
            verdict="x",
            title="",
            summary="",
            source_file=None,
            source_lines=None,
            raw={},
        ),
    ]
    sorted_fs = sort_by_severity(fs)
    assert [f.property_id for f in sorted_fs] == ["a", "c", "b"]


def test_malformed_json_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "04_PARTIAL_W0B0_1.json").write_text("not json", encoding="utf-8")
    _write_partial(
        tmp_path / "04_PARTIAL_W0B0_2.json",
        [{"property_id": "X", "severity": "High"}],
    )
    fs = load_findings(tmp_path)
    assert [f.property_id for f in fs] == ["X"]
