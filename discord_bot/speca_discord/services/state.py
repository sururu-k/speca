"""Persistent state for crash recovery — atomic JSON file.

Every live run writes a small record here whenever its status changes. On
bot startup we replay this file so a host crash or a `kill -9` leaves no
zombie Discord categories: each ``running`` record is re-attached to its
Discord channels and the underlying ``scripts/run_phase.py`` subprocess is
respawned with the same ``SPECA_RUN_ID`` so the orchestrator's per-item
resume picks up where it left off.

We deliberately store **enough to re-create the subprocess and re-find
the channels** but nothing more — the actual phase outputs already live
under ``.speca/runs/<run-id>/`` and don't need to be duplicated here.

Storage: ``<SPECA_REPO_PATH>/.speca/discord-state.json``. ``.speca/`` is
gitignored in upstream speca so the state never leaks into a commit.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

# Status transitions for a tracked run.
STATUS_RUNNING = "running"
STATUS_STOPPED = "stopped"   # user clicked Stop — do NOT auto-resume
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


@dataclass(slots=True)
class ActiveRunRecord:
    """Minimal record needed to re-spawn run_phase.py + re-attach Discord UI."""

    label: str
    speca_run_id: str
    category_id: int
    channel_ids: dict[str, int]
    info_message_id: int | None
    status: str
    started_at: str
    # Args we use to respawn run_phase.py — kept verbatim so a future bot
    # version with new flags doesn't accidentally drop user-set fields.
    args: dict[str, object]
    last_update_at: str = ""

    @classmethod
    def make(
        cls,
        *,
        label: str,
        speca_run_id: str,
        category_id: int,
        channel_ids: dict[str, int],
        args: dict[str, object],
    ) -> Self:
        now = datetime.now(UTC).isoformat()
        return cls(
            label=label,
            speca_run_id=speca_run_id,
            category_id=category_id,
            channel_ids=dict(channel_ids),
            info_message_id=None,
            status=STATUS_RUNNING,
            started_at=now,
            args=dict(args),
            last_update_at=now,
        )


class StateStore:
    """Atomic JSON store keyed by run ``label``.

    The on-disk shape is a single object ``{"runs": {<label>: record, ...}}``
    so adding new top-level metadata (last schema version, etc.) later
    doesn't break older deployments. All public methods are thread-safe
    via an internal lock; the lock is only held during the swap, never
    during user-level work.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._cache: dict[str, ActiveRunRecord] = {}
        self._loaded = False

    # ------------------------------------------------------------------
    # Read paths
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Idempotent: replays the on-disk state into the in-memory cache."""
        with self._lock:
            self._cache = {}
            self._loaded = True
            if not self.path.exists():
                return
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # Corrupt state — refuse to wipe; start with empty in-memory
                # and let a future write overwrite when the operator inspects.
                return
            for label, entry in (raw.get("runs") or {}).items():
                try:
                    self._cache[label] = ActiveRunRecord(**entry)
                except TypeError:
                    # Schema drift — skip the entry rather than crash.
                    continue

    def get(self, label: str) -> ActiveRunRecord | None:
        with self._lock:
            if not self._loaded:
                self.load()
            return self._cache.get(label)

    def all_runs(self) -> list[ActiveRunRecord]:
        with self._lock:
            if not self._loaded:
                self.load()
            return list(self._cache.values())

    def resumable_runs(self) -> list[ActiveRunRecord]:
        """Runs that should auto-resume on next bot launch.

        Excludes ``stopped`` (user clicked Stop) and terminal records.
        """
        return [r for r in self.all_runs() if r.status == STATUS_RUNNING]

    # ------------------------------------------------------------------
    # Write paths — every mutation hits disk atomically.
    # ------------------------------------------------------------------

    def upsert(self, record: ActiveRunRecord) -> None:
        with self._lock:
            if not self._loaded:
                self.load()
            record.last_update_at = datetime.now(UTC).isoformat()
            self._cache[record.label] = record
            self._flush()

    def update_status(self, label: str, status: str) -> ActiveRunRecord | None:
        with self._lock:
            if not self._loaded:
                self.load()
            current = self._cache.get(label)
            if current is None:
                return None
            current.status = status
            current.last_update_at = datetime.now(UTC).isoformat()
            self._flush()
            return current

    def update_info_message(self, label: str, message_id: int) -> None:
        with self._lock:
            if not self._loaded:
                self.load()
            current = self._cache.get(label)
            if current is None:
                return
            current.info_message_id = message_id
            current.last_update_at = datetime.now(UTC).isoformat()
            self._flush()

    def forget(self, label: str) -> None:
        """Drop a record entirely — call only on operator-initiated cleanup."""
        with self._lock:
            if not self._loaded:
                self.load()
            self._cache.pop(label, None)
            self._flush()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _flush(self) -> None:
        """Atomic write — temp file + os.replace.

        We hold ``self._lock`` for the rename to keep cost.json-style
        Windows ``PermissionError(13)`` races contained.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "runs": {label: asdict(rec) for label, rec in self._cache.items()},
        }
        fd, tmp_path = tempfile.mkstemp(
            prefix=self.path.name + ".",
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        tmp = Path(tmp_path)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            os.replace(str(tmp), str(self.path))
        except BaseException:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise


def derive_default_state_path(speca_repo_path: Path) -> Path:
    """``<speca>/.speca/discord-state.json`` — co-located with the run archive."""
    return speca_repo_path / ".speca" / "discord-state.json"


def filter_by_status(records: Iterable[ActiveRunRecord], status: str) -> list[ActiveRunRecord]:
    return [r for r in records if r.status == status]
