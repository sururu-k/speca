# speca-discord

Discord bot that wraps the [SPECA](https://github.com/NyxFoundation/speca) pipeline so a single Discord server can drive audit runs end-to-end from slash commands. Each run gets its own category with one channel per phase, plus a sticky `#info` board and a `#chat` channel hooked to Claude.

## Status

Skeleton — all 5 features wired and tested at the unit level. Runtime smoke tests (real Discord guild + real `scripts/run_phase.py`) are pending.

## Quick start

1. Install deps (Python 3.11+):

    ```bash
    cd discord_bot
    uv pip install -e .
    # or, in a plain venv:
    pip install -e .
    ```

2. Create a Discord application + bot at <https://discord.com/developers/applications> and copy the bot token. Invite the bot to your server with the `bot` and `application.commands` scopes; the in-server permissions it needs are: Manage Channels (creates categories + per-phase channels), Send Messages, Embed Links, Use Slash Commands.

3. Copy `.env.example` to `.env` and fill in:

    ```env
    DISCORD_BOT_TOKEN=...                      # bot token from the dev portal
    SPECA_DISCORD_GUILD_ID=...                 # the one server id the bot runs on
    SPECA_REPO_PATH=..                         # path to your speca checkout
    ANTHROPIC_API_KEY=sk-ant-...               # or SPECA_AUTH_JSON_PATH for OAuth
    ```

4. Launch:

    ```bash
    python -m speca_discord
    # or
    speca-discord
    ```

The bot syncs its slash commands to the configured guild on startup, so they appear instantly without the global propagation delay.

## What the bot does

- `/speca-run target_repo:... target_commit:... [target:04] [spec_urls:...] [keywords:...] [scope_01a:primary]`

  Creates a `Run <slug>` category under the `SPECA` parent category, then one channel per phase in the chain (`#01a` `#01b` `#01e` `#02c` `#03` `#04`) plus `#info` (sticky status board) and `#chat`. Spawns `scripts/run_phase.py --json` as a subprocess and streams NDJSON pipeline events to the matching channels.

- `/speca-status`  /  `/speca-cancel label:<run-label>`

  List active runs / send terminate signal.

- `/corpus-list`

  Tabular view of every run-id under `<speca_repo>/.speca/runs/`. Mirrors `speca corpus list`.

- `/corpus-show run_id:<id>`

  Manifest + per-phase breakdown (partials / logs / graphs / cost).

- `/corpus-export run_id:<id> [out:...] [include_logs] [phases:01a,01b,01e] [unsafe_include_findings]`

  Shells out to `speca-cli corpus export` so the canonical redaction policy is shared with the TUI.

- `/corpus-gc older_than:<dur> [no_dry_run]`

  Defaults to dry-run. Soft-deletes runs older than the cutoff into `<archive_root>/.trash/`.

- `/findings [output_dir:...] [severity:...]`

  Paginated embed walk over Phase 04 PARTIAL JSON. Buttons: Prev / Next / Close.

- `/ask question:<text> [system:...] [max_tokens:1024]`

  Single-turn Claude chat. Uses `ANTHROPIC_API_KEY` if set; otherwise reads OAuth `access_token` from `SPECA_AUTH_JSON_PATH` (the same file `speca-cli auth login` writes).

- `/speca-panel`  /  `/speca-help`

  Posts a persistent control panel embed with buttons (Start run / List runs / Findings / Ask). Buttons survive bot restarts; pin the message in a `#speca-control` channel for one-click access.

## Architecture

```
speca_discord/
├── config.py              # env loader with fail-fast validation
├── main.py                # bot bootstrap, slash command sync per-guild
├── __main__.py            # python -m speca_discord
├── cogs/
│   ├── panel.py           # /speca-panel, /speca-help
│   ├── runs.py            # /speca-run, /speca-status, /speca-cancel
│   ├── corpus.py          # /corpus-list, /corpus-show, /corpus-export, /corpus-gc
│   ├── findings.py        # /findings (paginated)
│   └── chat.py            # /ask
├── services/
│   ├── speca_subprocess.py  # async wrappers for run_phase.py + speca-cli
│   ├── archive_reader.py    # .speca/runs/ scan (mirrors lib/corpus/runs.ts)
│   ├── findings_reader.py   # outputs/04_PARTIAL_*.json loader
│   └── claude_api.py        # POST /v1/messages with API-key or OAuth header
├── panels/
│   ├── control_panel.py     # persistent View (custom_id buttons)
│   ├── finding_paginator.py # Prev/Next/Close View
│   └── embeds.py            # builders for every embed shape
└── tests/                   # pytest, asyncio mode auto
```

The bot **never imports** speca's Python modules — it shells out to `scripts/run_phase.py --json` and to the `speca-cli corpus` Node binary. Configuration changes in the upstream pipeline only affect this bot if the CLI surface or NDJSON event shape changes (both are pinned by the schemas in `schemas/*.schema.json`).

## Development

```bash
uv pip install -e ".[dev]"
pytest -v
ruff check speca_discord/
```

## Limitations

- Single-guild only by design. Multi-guild deployment needs auth scoping per guild + per-guild archive roots.
- `/ask` is single-turn. The multi-turn `#chat` channel-in-run-category flow is the next slice.
- The control panel buttons currently delegate to slash commands rather than running a wizard themselves. Slash flags cover everything the buttons would; the wizard is a polish slice.
- `corpus export` / `corpus gc` require the `cli/dist/cli.js` build or a working `npx tsx` to run the TypeScript source.

## Licence

MIT.
