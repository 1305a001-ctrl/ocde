"""Tests for the RedStone HYPE/USD reader.

We avoid the live HyperEVM RPC by injecting a stub httpx.AsyncClient via
the function's `client` parameter. respx is not in the dev deps and we
can't add new dependencies, so we hand-roll a minimal async stub.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from ocde.divergence import OraclePrice
from ocde.hyperevm_reader import _decode_int256, read_redstone_hype_price


class _StubResponse:
    """Mimics the slice of httpx.Response that hyperevm_reader uses."""

    def __init__(self, *, status_code: int, json_body: Any = None, raw_body: str | None = None):
        self.status_code = status_code
        self._json_body = json_body
        self._raw_body = raw_body

    def json(self) -> Any:
        if self._raw_body is not None:
            # Force a JSON parse error on a non-JSON raw body
            return json.loads(self._raw_body)
        return self._json_body


class _StubClient:
    """Mimics httpx.AsyncClient.post() for tests. Records the last request."""

    def __init__(
        self,
        response: _StubResponse | None = None,
        *,
        raise_exc: Exception | None = None,
    ):
        self.response = response
        self.raise_exc = raise_exc
        self.last_url: str | None = None
        self.last_json: Any = None

    async def post(self, url: str, json: Any = None) -> _StubResponse:  # noqa: A002 — match httpx signature
        self.last_url = url
        self.last_json = json
        if self.raise_exc is not None:
            raise self.raise_exc
        assert self.response is not None
        return self.response

    async def aclose(self) -> None:
        return None


# --- _decode_int256 unit tests ----------------------------------------------

def test_decode_int256_positive():
    # 1_000_000_000 in 32-byte hex
    hex_val = "0x" + format(1_000_000_000, "064x")
    assert _decode_int256(hex_val) == 1_000_000_000


def test_decode_int256_zero():
    assert _decode_int256("0x" + "0" * 64) == 0


def test_decode_int256_negative_twos_complement():
    # -1 in int256 two's complement = 0xff..ff (64 f's)
    assert _decode_int256("0x" + "f" * 64) == -1


def test_decode_int256_rejects_short_input():
    # A revert often returns "0x" — too short to decode
    assert _decode_int256("0x") is None
    assert _decode_int256("0x1234") is None


def test_decode_int256_rejects_non_hex():
    assert _decode_int256("not-hex") is None
    assert _decode_int256("") is None


def test_decode_int256_rejects_garbage_chars():
    # 64 chars but not hex
    assert _decode_int256("0x" + "z" * 64) is None


# --- read_redstone_hype_price tests ----------------------------------------

@pytest.mark.asyncio
async def test_happy_path_decodes_and_scales():
    # 4794000000 / 1e8 = 47.94 (RedStone uses 8 decimals)
    raw = 4_794_000_000
    hex_result = "0x" + format(raw, "064x")
    stub = _StubClient(_StubResponse(status_code=200, json_body={
        "jsonrpc": "2.0",
        "id": 1,
        "result": hex_result,
    }))

    price = await read_redstone_hype_price(client=stub)  # type: ignore[arg-type]
    assert isinstance(price, OraclePrice)
    assert price.source == "redstone"
    assert price.asset_alias == "hype"
    assert abs(price.price_usd - 47.94) < 1e-9
    assert price.timestamp_ms > 0
    # Request was an eth_call to the RedStone source
    assert stub.last_json["method"] == "eth_call"
    assert stub.last_json["params"][0]["data"] == "0x50d25bcd"


@pytest.mark.asyncio
async def test_http_500_returns_none():
    stub = _StubClient(_StubResponse(status_code=500, json_body={}))
    assert await read_redstone_hype_price(client=stub) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_http_error_returns_none():
    stub = _StubClient(raise_exc=httpx.ConnectError("conn refused"))
    assert await read_redstone_hype_price(client=stub) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_timeout_returns_none():
    stub = _StubClient(raise_exc=httpx.TimeoutException("timed out"))
    assert await read_redstone_hype_price(client=stub) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_malformed_body_returns_none():
    # Body is not a dict
    stub = _StubClient(_StubResponse(status_code=200, json_body=[1, 2, 3]))
    assert await read_redstone_hype_price(client=stub) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_missing_result_returns_none():
    stub = _StubClient(_StubResponse(status_code=200, json_body={"jsonrpc": "2.0", "id": 1}))
    assert await read_redstone_hype_price(client=stub) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_rpc_error_returns_none():
    stub = _StubClient(_StubResponse(status_code=200, json_body={
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32000, "message": "execution reverted"},
    }))
    assert await read_redstone_hype_price(client=stub) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_revert_result_returns_none():
    # A contract revert often surfaces as result="0x" (decode failure)
    stub = _StubClient(_StubResponse(status_code=200, json_body={
        "jsonrpc": "2.0",
        "id": 1,
        "result": "0x",
    }))
    assert await read_redstone_hype_price(client=stub) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_zero_or_negative_price_returns_none():
    # Zero
    stub = _StubClient(_StubResponse(status_code=200, json_body={
        "jsonrpc": "2.0", "id": 1, "result": "0x" + "0" * 64,
    }))
    assert await read_redstone_hype_price(client=stub) is None  # type: ignore[arg-type]
    # Negative (two's complement -1)
    stub2 = _StubClient(_StubResponse(status_code=200, json_body={
        "jsonrpc": "2.0", "id": 1, "result": "0x" + "f" * 64,
    }))
    assert await read_redstone_hype_price(client=stub2) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_malformed_result_string_returns_none():
    stub = _StubClient(_StubResponse(status_code=200, json_body={
        "jsonrpc": "2.0", "id": 1, "result": "garbage",
    }))
    assert await read_redstone_hype_price(client=stub) is None  # type: ignore[arg-type]
