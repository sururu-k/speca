"""run_id generator — format conformance + uniqueness."""

from __future__ import annotations

import re

from speca_discord.services.run_id import _slugify, make_run_id

_RUN_ID_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z-[0-9a-f]{7}-[a-z0-9-]+-[0-9a-f]{4}$"
)


def test_make_run_id_format() -> None:
    rid = make_run_id(spec_slug="eip-7825", sha="a1b2c3d")
    assert _RUN_ID_PATTERN.match(rid), rid


def test_two_calls_produce_distinct_ids() -> None:
    a = make_run_id(spec_slug="x", sha="abcdefa")
    b = make_run_id(spec_slug="x", sha="abcdefa")
    # Even at identical second + sha + slug, the 4-hex nonce differs.
    assert a != b


def test_slugify_strips_garbage() -> None:
    assert _slugify("Hello, World!") == "hello-world"
    assert _slugify("EIP-7825 secp256r1") == "eip-7825-secp256r1"


def test_slugify_falls_back_when_input_empty() -> None:
    assert _slugify("") == "unknown"
    assert _slugify("!!!!!") == "unknown"


def test_slugify_caps_length() -> None:
    long_input = "a" * 100
    assert len(_slugify(long_input, 40)) == 40


def test_missing_sha_uses_random_hex() -> None:
    # make_run_id with no sha still produces a conforming run-id.
    rid = make_run_id(spec_slug="x")
    assert _RUN_ID_PATTERN.match(rid), rid
