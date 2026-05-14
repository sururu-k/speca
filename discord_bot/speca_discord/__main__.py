"""CLI entry point — ``python -m speca_discord`` / ``speca-discord``."""

from __future__ import annotations

import asyncio
import logging
import sys

from .main import run_bot


def main() -> None:
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logging.getLogger("speca_discord").info("interrupted by user, exiting")
        sys.exit(0)
    except RuntimeError as e:
        # Config errors land here — surface the message cleanly without a
        # full traceback (the cause is in the message itself).
        print(f"speca-discord: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
