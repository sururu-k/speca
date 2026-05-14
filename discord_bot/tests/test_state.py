"""StateStore — atomic JSON store + resumable_runs filter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from speca_discord.services.state import (
    STATUS_COMPLETED,
    STATUS_RUNNING,
    STATUS_STOPPED,
    ActiveRunRecord,
    StateStore,
    derive_default_state_path,
)


@pytest.fixture()
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "discord-state.json"


@pytest.fixture()
def store(state_path: Path) -> StateStore:
    s = StateStore(state_path)
    s.load()
    return s


def _record(label: str = "demo", **kwargs: object) -> ActiveRunRecord:
    defaults: dict[str, object] = {
        "label": label,
        "speca_run_id": "2026-05-15T12-00-00Z-abc-demo-xxxx",
        "category_id": 111,
        "channel_ids": {"info": 222, "01a": 333},
        "info_message_id": 444,
        "status": STATUS_RUNNING,
        "started_at": "2026-05-15T12:00:00+00:00",
        "args": {"target": "04"},
    }
    defaults.update(kwargs)
    return ActiveRunRecord(**defaults)  # type: ignore[arg-type]


class TestEmptyStore:
    def test_get_returns_none(self, store: StateStore) -> None:
        assert store.get("missing") is None

    def test_resumable_returns_empty(self, store: StateStore) -> None:
        assert store.resumable_runs() == []

    def test_load_tolerates_missing_file(self, state_path: Path) -> None:
        StateStore(state_path).load()  # no exception


class TestPersistence:
    def test_upsert_writes_atomically(
        self, store: StateStore, state_path: Path
    ) -> None:
        store.upsert(_record())
        on_disk = json.loads(state_path.read_text(encoding="utf-8"))
        assert "demo" in on_disk["runs"]
        assert on_disk["runs"]["demo"]["status"] == STATUS_RUNNING
        # last_update_at is set by upsert.
        assert on_disk["runs"]["demo"]["last_update_at"]

    def test_round_trip_through_new_instance(
        self, state_path: Path
    ) -> None:
        s1 = StateStore(state_path)
        s1.upsert(_record())
        s2 = StateStore(state_path)
        s2.load()
        assert s2.get("demo") is not None
        assert s2.get("demo").label == "demo"  # type: ignore[union-attr]

    def test_update_status(self, store: StateStore) -> None:
        store.upsert(_record())
        store.update_status("demo", STATUS_STOPPED)
        assert store.get("demo").status == STATUS_STOPPED  # type: ignore[union-attr]

    def test_update_status_missing_label_returns_none(
        self, store: StateStore
    ) -> None:
        assert store.update_status("nope", STATUS_COMPLETED) is None

    def test_forget(self, store: StateStore) -> None:
        store.upsert(_record())
        store.forget("demo")
        assert store.get("demo") is None

    def test_corrupt_state_file_starts_empty_in_memory(
        self, state_path: Path
    ) -> None:
        state_path.write_text("not json", encoding="utf-8")
        s = StateStore(state_path)
        s.load()
        assert s.all_runs() == []

    def test_unknown_field_in_record_skipped_on_load(
        self, state_path: Path
    ) -> None:
        # Forward-compat: a future bot version may add fields. A previous
        # version reading that state file should just skip the entry.
        state_path.write_text(
            json.dumps(
                {
                    "runs": {
                        "demo": {
                            "label": "demo",
                            "speca_run_id": "x",
                            "category_id": 1,
                            "channel_ids": {},
                            "info_message_id": None,
                            "status": STATUS_RUNNING,
                            "started_at": "x",
                            "args": {},
                            "future_field": "ignore-me",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        s = StateStore(state_path)
        s.load()
        # Skipped via TypeError catch in load().
        assert s.all_runs() == []


class TestResumableRuns:
    def test_filters_to_running_only(self, store: StateStore) -> None:
        store.upsert(_record(label="r1", status=STATUS_RUNNING))
        store.upsert(_record(label="r2", status=STATUS_STOPPED))
        store.upsert(_record(label="r3", status=STATUS_COMPLETED))
        labels = {r.label for r in store.resumable_runs()}
        assert labels == {"r1"}


class TestDefaultPath:
    def test_lives_under_speca_repo(self, tmp_path: Path) -> None:
        p = derive_default_state_path(tmp_path)
        assert p == tmp_path / ".speca" / "discord-state.json"
