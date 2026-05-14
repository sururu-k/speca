"""Async wrappers around ``scripts/run_phase.py`` and ``speca-cli corpus``.

We never import speca's Python modules directly: the Discord bot lives in
its own venv, talks to the real orchestrator (which has its own uv-managed
env) by spawning subprocesses, and parses the documented stdout contracts
(NDJSON for run_phase --json, plain JSON for corpus subcommands).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _python_runner_args() -> list[str]:
    """How to invoke run_phase.py.

    The speca side uses ``uv run`` in CI / docs. We honour ``$SPECA_PY`` for
    operators who prefer a plain venv, and fall back to ``uv`` if it's on
    PATH, otherwise to the current python.
    """
    override = os.environ.get("SPECA_PY")
    if override:
        return override.split()
    if any(
        (root := Path(p) / "uv").is_file() or (root.with_suffix(".exe")).is_file()
        for p in os.environ.get("PATH", "").split(os.pathsep)
        if p
    ):
        return ["uv", "run", "--", "python"]
    return [sys.executable]


@dataclass(slots=True)
class RunPhaseHandle:
    """Live handle for a running ``scripts/run_phase.py`` invocation.

    Exposes ``events()`` for NDJSON consumption and ``wait()`` for the exit
    code; ``cancel()`` sends SIGTERM (or terminate on Windows).
    """

    proc: asyncio.subprocess.Process

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        assert self.proc.stdout is not None
        async for line in self.proc.stdout:
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                yield json.loads(text)
            except json.JSONDecodeError:
                # Treat anything that isn't a valid JSON object as a
                # decorative stderr-on-stdout line and forward it as a
                # synthetic event so the caller can show it without crashing.
                yield {"event": "decorative", "line": text}

    async def wait(self) -> int:
        return await self.proc.wait()

    def cancel(self) -> None:
        try:
            self.proc.terminate()
        except ProcessLookupError:
            pass


async def start_run_phase(
    *,
    speca_repo_path: Path,
    phases: list[str] | None = None,
    target: str | None = None,
    output_dir: Path | None = None,
    spec_urls: list[str] | None = None,
    keywords: list[str] | None = None,
    scope_01a: str | None = None,
    extra_args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> RunPhaseHandle:
    """Spawn ``scripts/run_phase.py --json`` as an async subprocess.

    Caller iterates ``handle.events()``, then awaits ``handle.wait()``.
    """
    args = [*_python_runner_args(), "scripts/run_phase.py", "--json"]
    if phases:
        args += ["--phase", *phases]
    elif target:
        args += ["--target", target]
    if output_dir is not None:
        args += ["--output-dir", str(output_dir)]
    if spec_urls:
        args += ["--spec-urls", ",".join(spec_urls)]
    if keywords:
        args += ["--keywords", ",".join(keywords)]
    if scope_01a:
        args += ["--01a-scope", scope_01a]
    if extra_args:
        args += extra_args

    env_full = {**os.environ, "PYTHONIOENCODING": "utf-8", **(env or {})}

    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(speca_repo_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env_full,
    )
    return RunPhaseHandle(proc=proc)


async def corpus_subprocess(
    *,
    speca_repo_path: Path,
    subcommand: str,
    args: list[str] | None = None,
) -> tuple[int, str, str]:
    """One-shot ``speca-cli corpus <sub>`` invocation.

    Returns ``(exit_code, stdout, stderr)``. The bot uses this for read-only
    list/show plus the destructive export/gc commands. The CLI lives at
    ``<speca_repo_path>/cli`` (the built dist or the dev tsx entry).
    """
    cli_entry = speca_repo_path / "cli" / "dist" / "cli.js"
    if not cli_entry.is_file():
        # Fall back to tsx for un-built dev checkouts.
        cli_entry = speca_repo_path / "cli" / "src" / "cli.tsx"
        runner = ["npx", "--no-install", "tsx"]
    else:
        runner = ["node"]

    cmd = [*runner, str(cli_entry), "corpus", subcommand, *(args or [])]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(speca_repo_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    return (
        proc.returncode or 0,
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
    )
