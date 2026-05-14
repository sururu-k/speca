"""compat module — startup probe + warn/strict enforcement."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from speca_discord.services import compat
from speca_discord.services.compat import (
    REQUIRED_RUN_PHASE_FLAGS,
    REQUIRED_SCHEMA_FILES,
    CompatReport,
    _missing_flags,
    _probe_schemas_directory,
    enforce,
    run_startup_probe,
)


def _write_schema(base: Path, name: str, required: list[str] | None = None) -> None:
    body: dict = {"type": "object"}
    if required:
        body["required"] = required
    (base / name).write_text(__import__("json").dumps(body), encoding="utf-8")


@pytest.fixture()
def speca_root(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "run_phase.py").write_text("# stub\n", encoding="utf-8")
    (tmp_path / "schemas").mkdir()
    for name in REQUIRED_SCHEMA_FILES:
        _write_schema(
            tmp_path / "schemas",
            name,
            required=(
                ["run_id", "started_at"] if name == "RunManifest.schema.json" else None
            ),
        )
    return tmp_path


class TestMissingFlags:
    def test_all_present(self) -> None:
        text = " ".join(REQUIRED_RUN_PHASE_FLAGS) + " extra-junk"
        assert _missing_flags(text, REQUIRED_RUN_PHASE_FLAGS) == set()

    def test_some_missing(self) -> None:
        text = "--json --phase --target"
        missing = _missing_flags(text, REQUIRED_RUN_PHASE_FLAGS)
        assert "--01a-scope" in missing


class TestSchemasProbe:
    def test_ok_when_all_present(self, speca_root: Path) -> None:
        report = CompatReport()
        _probe_schemas_directory(speca_root, report)
        assert report.ok
        assert any("contract" in n for n in report.notes)

    def test_missing_schema_fails(self, speca_root: Path) -> None:
        (speca_root / "schemas" / "RunManifest.schema.json").unlink()
        report = CompatReport()
        _probe_schemas_directory(speca_root, report)
        assert not report.ok
        assert any("missing required files" in w for w in report.warnings)

    def test_run_manifest_required_check(self, speca_root: Path) -> None:
        # Remove the required list — probe should reject because run_id /
        # started_at are no longer pinned.
        _write_schema(speca_root / "schemas", "RunManifest.schema.json", required=[])
        report = CompatReport()
        _probe_schemas_directory(speca_root, report)
        assert not report.ok

    def test_missing_schemas_dir(self, tmp_path: Path) -> None:
        report = CompatReport()
        _probe_schemas_directory(tmp_path, report)
        assert not report.ok


class TestEnforce:
    def test_warn_mode_keeps_running(self, monkeypatch, caplog) -> None:
        report = CompatReport()
        report.fail("synthetic missing flag")
        monkeypatch.delenv("SPECA_DISCORD_COMPAT_STRICT", raising=False)
        caplog.set_level(logging.WARNING, logger="speca_discord.services.compat")
        # warn mode: must NOT raise / exit.
        enforce(report, strict=False)
        joined = "\n".join(r.message for r in caplog.records)
        assert "synthetic missing flag" in joined

    def test_strict_mode_exits(self) -> None:
        report = CompatReport()
        report.fail("synthetic missing flag")
        with pytest.raises(SystemExit):
            enforce(report, strict=True)


@pytest.mark.asyncio
async def test_run_startup_probe_returns_report(
    speca_root: Path, monkeypatch
) -> None:
    # Replace the help-capture functions so the probe completes without
    # actually invoking python / node subprocesses.
    async def fake_run_phase(*_args, **_kwargs):
        return " ".join(REQUIRED_RUN_PHASE_FLAGS)

    async def fake_corpus(*_args, **_kwargs):
        return "list show export gc"

    monkeypatch.setattr(compat, "_capture_run_phase_help", fake_run_phase)
    monkeypatch.setattr(compat, "_capture_corpus_help", fake_corpus)
    report = await run_startup_probe(speca_root)
    assert report.ok, report.warnings


@pytest.mark.asyncio
async def test_run_startup_probe_flags_run_phase_regression(
    speca_root: Path, monkeypatch
) -> None:
    async def fake_run_phase(*_args, **_kwargs):
        return "--json --phase"  # missing --target / --01a-scope / etc.

    async def fake_corpus(*_args, **_kwargs):
        return None

    monkeypatch.setattr(compat, "_capture_run_phase_help", fake_run_phase)
    monkeypatch.setattr(compat, "_capture_corpus_help", fake_corpus)
    report = await run_startup_probe(speca_root)
    assert not report.ok
    assert any("missing required flags" in w for w in report.warnings)
