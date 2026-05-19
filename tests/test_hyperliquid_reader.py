"""Tests for the Hyperliquid HYPE-PERP mid reader.

Same pattern as test_hyperevm_reader.py — we inject a stub httpx client.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from ocde.divergence import OraclePrice
from ocde.hyperliquid_reader import read_hl_hype_mid


class _StubResponse:
    def __init__(self, *, status_code: int, json_body: Any = None):
        self.status_code = status_code
        self._json_body = json_body

    def json(self) -> Any:
        return self._json_body


class _StubClient:
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

    async def post(self, url: str, json: Any = None) -> _StubResponse:  # noqa: A002
        self.last_url = url
        self.last_json = json
        if self.raise_exc is not None:
            raise self.raise_exc
        assert self.response is not None
        return self.response

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_happy_path_parses_string_price():
    stub = _StubClient(_StubResponse(status_code=200, json_body={
        "BTC": "67000.5",
        "ETH": "3400.0",
        "HYPE": "47.94",
        "SOL": "210.0",
    }))
    price = await read_hl_hype_mid(client=stub)  # type: ignore[arg-type]
    assert isinstance(price, OraclePrice)
    assert price.source == "hyperliquid"
    assert price.asset_alias == "hype"
    assert abs(price.price_usd - 47.94) < 1e-9
    assert price.timestamp_ms > 0
    assert stub.last_json == {"type": "allMids"}


@pytest.mark.asyncio
async def test_missing_hype_in_response_returns_none():
    stub = _StubClient(_StubResponse(status_code=200, json_body={
        "BTC": "67000.5",
        "ETH": "3400.0",
    }))
    assert await read_hl_hype_mid(client=stub) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_http_500_returns_none():
    stub = _StubClient(_StubResponse(status_code=500, json_body={}))
    assert await read_hl_hype_mid(client=stub) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_http_error_returns_none():
    stub = _StubClient(raise_exc=httpx.ConnectError("conn refused"))
    assert await read_hl_hype_mid(client=stub) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_timeout_returns_none():
    stub = _StubClient(raise_exc=httpx.TimeoutException("timed out"))
    assert await read_hl_hype_mid(client=stub) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_non_numeric_value_returns_none():
    stub = _StubClient(_StubResponse(status_code=200, json_body={
        "HYPE": "not-a-number",
    }))
    assert await read_hl_hype_mid(client=stub) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_none_value_returns_none():
    stub = _StubClient(_StubResponse(status_code=200, json_body={
        "HYPE": None,
    }))
    assert await read_hl_hype_mid(client=stub) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_negative_or_zero_price_returns_none():
    stub_neg = _StubClient(_StubResponse(status_code=200, json_body={"HYPE": "-1.0"}))
    assert await read_hl_hype_mid(client=stub_neg) is None  # type: ignore[arg-type]
    stub_zero = _StubClient(_StubResponse(status_code=200, json_body={"HYPE": "0"}))
    assert await read_hl_hype_mid(client=stub_zero) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_non_dict_body_returns_none():
    stub = _StubClient(_StubResponse(status_code=200, json_body=["not", "a", "dict"]))
    assert await read_hl_hype_mid(client=stub) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_numeric_float_value_also_works():
    # API normally returns strings but be lenient with numeric inputs too
    stub = _StubClient(_StubResponse(status_code=200, json_body={"HYPE": 47.94}))
    price = await read_hl_hype_mid(client=stub)  # type: ignore[arg-type]
    assert price is not None
    assert abs(price.price_usd - 47.94) < 1e-9
