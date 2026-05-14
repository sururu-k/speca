"""Startup compatibility probe for the upstream speca repository.

The bot pins a small contract surface — a handful of ``run_phase.py``
flags, the ``speca-cli corpus`` subcommands, and the presence of the
JSON Schemas the bot reads. When the user merges upstream into their
fork, the contract may shift. We detect that **on startup** so the
operator sees an actionable warning instead of mysterious failures
mid-command.

Default mode is "warn and proceed" — the bot keeps running; only the
commands that depend on the missing surface fail when invoked. Strict
mode (env ``SPECA_DISCORD_COMPAT_STRICT=1``) raises and exits, which
is what we want in CI.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


# What the bot needs from upstream speca. Update this set deliberately
# whenever a new contract is added; CI then catches regressions.
REQUIRED_RUN_PHASE_FLAGS: frozenset[str] = frozenset(
    {
        "--json",
        "--phase",
        "--target",
        "--spec-urls",
        "--keywords",
        "--01a-scope",
        "--output-dir",
    }
)
REQUIRED_CORPUS_SUBCOMMANDS: frozenset[str] = frozenset(
    {"list", "show", "export", "gc"}
)
REQUIRED_SCHEMA_FILES: frozenset[str] = frozenset(
    {
        "RunManifest.schema.json",
        "PhaseStartedEvent.schema.json",
        "PhaseCompletedEvent.schema.json",
        "PhaseFailedEvent.schema.json",
        "PipelineStartedEvent.schema.json",
        "PipelineCompletedEvent.schema.json",
    }
)


@dataclass(slots=True)
class CompatReport:
    ok: bool = True
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def fail(self, message: str) -> None:
        self.ok = False
        self.warnings.append(message)

    def note(self, message: str) -> None:
        self.notes.append(message)


async def run_startup_probe(speca_repo_path: Path) -> CompatReport:
    """Run every probe and return a combined report."""
    report = CompatReport()
    await _probe_run_phase_help(speca_repo_path, report)
    await _probe_corpus_help(speca_repo_path, report)
    _probe_schemas_directory(speca_repo_path, report)
    return report


def enforce(report: CompatReport, *, strict: bool | None = None) -> None:
    """Surface the report on stderr; in strict mode, exit nonzero on failure."""
    if strict is None:
        strict = os.environ.get("SPECA_DISCORD_COMPAT_STRICT") == "1"
    for note in report.notes:
        log.info("compat: %s", note)
    for warning in report.warnings:
        log.warning("compat: %s", warning)
    if report.ok:
        log.info("compat: speca surface looks compatible")
        return
    if strict:
        log.error(
            "compat: strict mode aborting startup due to %d unmet contract(s)",
            len(report.warnings),
        )
        sys.exit(2)
    log.warning(
        "compat: continuing with %d unmet contract(s); affected commands "
        "will fail when invoked. Set SPECA_DISCORD_COMPAT_STRICT=1 to bail.",
        len(report.warnings),
    )


# ---------------------------------------------------------------------------
# Individual probes — each is best-effort and never raises out.
# ---------------------------------------------------------------------------


async def _probe_run_phase_help(
    speca_repo_path: Path, report: CompatReport
) -> None:
    help_text = await _capture_run_phase_help(speca_repo_path)
    if help_text is None:
        report.fail(
            "could not invoke `scripts/run_phase.py --help` — /speca-run "
            "will fail until upstream is reachable."
        )
        return
    missing = sorted(_missing_flags(help_text, REQUIRED_RUN_PHASE_FLAGS))
    if missing:
        report.fail(
            "run_phase.py is missing required flags: "
            + ", ".join(missing)
            + " — upstream may have renamed them; the bot needs an update."
        )
    else:
        report.note("run_phase.py --help: all required flags present")


async def _probe_corpus_help(
    speca_repo_path: Path, report: CompatReport
) -> None:
    help_text = await _capture_corpus_help(speca_repo_path)
    if help_text is None:
        report.note(
            "speca-cli `corpus` help not callable (cli not built / npx tsx "
            "unavailable); /corpus-export and /corpus-gc require it."
        )
        return
    missing = sorted(
        sub for sub in REQUIRED_CORPUS_SUBCOMMANDS if sub not in help_text
    )
    if missing:
        report.fail(
            "speca-cli corpus is missing subcommands: " + ", ".join(missing)
        )
    else:
        report.note("speca-cli corpus help: all required subcommands present")


def _probe_schemas_directory(
    speca_repo_path: Path, report: CompatReport
) -> None:
    base = speca_repo_path / "schemas"
    if not base.is_dir():
        report.fail(f"`{base}` not found — upstream schemas/ directory missing")
        return
    found = {p.name for p in base.glob("**/*.json")}
    missing = sorted(REQUIRED_SCHEMA_FILES - found)
    if missing:
        report.fail("schemas/ missing required files: " + ", ".join(missing))
        return
    # Sanity-check that RunManifest still has the fields archive_reader uses.
    rm_path = base / "RunManifest.schema.json"
    try:
        rm = json.loads(rm_path.read_text(encoding="utf-8"))
        required = set(rm.get("required") or [])
        if "run_id" not in required or "started_at" not in required:
            report.fail(
                "RunManifest.schema.json no longer requires {run_id, started_at}; "
                "archive_reader expects both."
            )
            return
    except (OSError, ValueError) as e:
        report.fail(f"RunManifest.schema.json unreadable: {e}")
        return
    report.note("schemas/ contract: present and field-compatible")


# ---------------------------------------------------------------------------
# Subprocess helpers — local to compat so we can stub them in tests.
# ---------------------------------------------------------------------------


async def _capture_run_phase_help(speca_repo_path: Path) -> str | None:
    """Return ``scripts/run_phase.py --help`` stdout, or None on failure."""
    # We import lazily to avoid a hard dependency cycle with the rest of the
    # service layer at startup.
    from .speca_subprocess import _python_runner_args  # type: ignore

    args = [*_python_runner_args(), "scripts/run_phase.py", "--help"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(speca_repo_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except (FileNotFoundError, OSError):
        return None
    stdout_b, _ = await proc.communicate()
    return stdout_b.decode("utf-8", errors="replace") if proc.returncode == 0 else None


async def _capture_corpus_help(speca_repo_path: Path) -> str | None:
    cli_entry = speca_repo_path / "cli" / "dist" / "cli.js"
    if cli_entry.is_file():
        cmd = ["node", str(cli_entry), "corpus", "help"]
    else:
        # Fall back to tsx on the un-built source tree.
        src_entry = speca_repo_path / "cli" / "src" / "cli.tsx"
        if not src_entry.is_file():
            return None
        cmd = ["npx", "--no-install", "tsx", str(src_entry), "corpus", "help"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(speca_repo_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, OSError):
        return None
    stdout_b, stderr_b = await proc.communicate()
    text = (stdout_b + stderr_b).decode("utf-8", errors="replace")
    return text if text else None


def _missing_flags(help_text: str, required: Iterable[str]) -> set[str]:
    """Return any required flag that's not literally present in --help."""
    return {flag for flag in required if flag not in help_text}
