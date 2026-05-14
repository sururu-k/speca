"""Config loader — required fields, defaults, fail-fast errors."""

from __future__ import annotations

from pathlib import Path

import pytest

from speca_discord.config import BotConfig


def _good_env(speca_repo: Path) -> dict[str, str]:
    return {
        "DISCORD_BOT_TOKEN": "fake-token",
        "SPECA_DISCORD_GUILD_ID": "123456789012345678",
        "SPECA_REPO_PATH": str(speca_repo),
    }


@pytest.fixture()
def fake_speca(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "run_phase.py").write_text("# stub\n", encoding="utf-8")
    return tmp_path


class TestRequiredFields:
    def test_loads_minimal_valid_env(self, fake_speca: Path) -> None:
        cfg = BotConfig.from_env(_good_env(fake_speca))
        assert cfg.discord_token == "fake-token"
        assert cfg.guild_id == 123456789012345678
        assert cfg.speca_repo_path == fake_speca.resolve()
        assert cfg.archive_root == (fake_speca / ".speca" / "runs").resolve()
        assert cfg.parent_category_name == "SPECA"
        assert cfg.anthropic_api_key is None

    def test_missing_token_raises(self, fake_speca: Path) -> None:
        env = _good_env(fake_speca) | {"DISCORD_BOT_TOKEN": ""}
        with pytest.raises(RuntimeError, match="DISCORD_BOT_TOKEN"):
            BotConfig.from_env(env)

    def test_guild_must_be_numeric(self, fake_speca: Path) -> None:
        env = _good_env(fake_speca) | {"SPECA_DISCORD_GUILD_ID": "not-a-number"}
        with pytest.raises(RuntimeError, match="SPECA_DISCORD_GUILD_ID"):
            BotConfig.from_env(env)

    def test_speca_repo_path_must_contain_run_phase(self, tmp_path: Path) -> None:
        # No scripts/run_phase.py present → fail fast.
        env = _good_env(tmp_path)
        with pytest.raises(RuntimeError, match=r"run_phase\.py is missing"):
            BotConfig.from_env(env)


class TestOptionalFields:
    def test_archive_root_override(self, fake_speca: Path, tmp_path: Path) -> None:
        archive = tmp_path / "elsewhere"
        archive.mkdir()
        env = _good_env(fake_speca) | {"SPECA_ARCHIVE_ROOT": str(archive)}
        cfg = BotConfig.from_env(env)
        assert cfg.archive_root == archive.resolve()

    def test_parent_category_default_when_blank(self, fake_speca: Path) -> None:
        env = _good_env(fake_speca) | {"SPECA_DISCORD_PARENT_CATEGORY": "   "}
        cfg = BotConfig.from_env(env)
        assert cfg.parent_category_name == "SPECA"

    def test_anthropic_api_key_picked_up(self, fake_speca: Path) -> None:
        env = _good_env(fake_speca) | {"ANTHROPIC_API_KEY": "sk-ant-x"}
        cfg = BotConfig.from_env(env)
        assert cfg.anthropic_api_key == "sk-ant-x"

    def test_max_lines_clamped(self, fake_speca: Path) -> None:
        env = _good_env(fake_speca) | {"SPECA_DISCORD_MAX_LIVE_LOG_LINES": "9999"}
        cfg = BotConfig.from_env(env)
        assert cfg.max_live_log_lines == 200

    def test_max_lines_floor(self, fake_speca: Path) -> None:
        env = _good_env(fake_speca) | {"SPECA_DISCORD_MAX_LIVE_LOG_LINES": "1"}
        cfg = BotConfig.from_env(env)
        assert cfg.max_live_log_lines == 5
