"""Generate run-ids that match speca's ``scripts/run_phase.py:make_run_id``.

We mirror the format exactly so the bot can mint the id **before** spawning
the subprocess (and pass it via ``SPECA_RUN_ID`` env). That lets:

  - The state store record the run-id from the moment the subprocess starts
  - A crashed bot find the matching ``.speca/runs/<run-id>/`` for resume

If speca changes the format we still keep the env-based override working;
this module's output just becomes "compatible enough" to look up the archive.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime

_SLUG_FALLBACK = "unknown"


def make_run_id(spec_slug: str | None = None, sha: str | None = None) -> str:
    """Return ``<YYYY-MM-DDTHH-MM-SSZ>-<7hex>-<slug>-<4hex>``.

    Hyphens (not colons) separate time-of-day components so the string is
    a valid path segment on Windows.
    """
    now = datetime.now(UTC)
    ts = now.strftime("%Y-%m-%dT%H-%M-%SZ")
    resolved_sha = (sha or "").strip() or secrets.token_hex(4)[:7]
    slug = _slugify(spec_slug or _SLUG_FALLBACK, 40)
    nonce = secrets.token_hex(2)
    return f"{ts}-{resolved_sha}-{slug}-{nonce}"


def _slugify(text: str, max_len: int = 40) -> str:
    try:
        text = text.encode("ascii", errors="ignore").decode("ascii")
    except UnicodeError:
        pass
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or _SLUG_FALLBACK
