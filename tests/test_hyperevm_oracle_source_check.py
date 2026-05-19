"""Tests for `verify_hyperlend_whype_source` (trap-surface monitor).

This is the OCDE-side counterpart of `gmx_strategies.watchdog.check_hyperlend_oracle_source`:
it runs in-process every N cycles of the divergence loop and detects a
governance-rotation of the HyperLend WHYPE oracle source via
`IAaveOracle.getSourceOfAsset(WHYPE)`.

Pattern mirrors the existing `test_hyperevm_reader.py` — stub
httpx.AsyncClient.post + record the eth_call payload. No new deps.

Coverage:
  - Happy path: live source matches expected → returns True.
  - Drift path: live source rotated → returns False + logs ERROR.
  - Zero-address drift: source cleared via setSourceOfAsset(token, 0).
  - Unreachable: RPC failure → returns None (NOT True, NOT False).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest

from ocde.hyperevm_reader import verify_hyperlend_whype_source


class _StubResponse:
    """Mimics the slice of httpx.Response that hyperevm_reader uses."""

    def __init__(self, *, status_code: int = 200, json_body: Any = None) -> None:
        self.status_code = status_code
        self._json_body = json_body

    def json(self) -> Any:
        return self._json_body


class _StubClient:
    """Mimics httpx.AsyncClient.post() for tests. Records the last request."""

    def __init__(
        self,
        response: _StubResponse | None = None,
        *,
        raise_exc: Exception | None = None,
    ) -> None:
        self.response = response
        self.raise_exc = raise_exc
        self.last_url: str | None = None
        self.last_json: Any = None

    async def post(self, url: str, *, json: Any = None) -> _StubResponse:
        self.last_url = url
        self.last_json = json
        if self.raise_exc is not None:
            raise self.raise_exc
        assert self.response is not None
        return self.response

    async def aclose(self) -> None:
        return None


def _encode_address_result(addr: str) -> str:
    """Pack a single-address eth_call return blob (right-padded to 32 bytes).

    No eth_abi dep — the encoding is just 12 zero bytes followed by the
    20-byte address. Lowercased to match what the production decoder returns.
    """
    assert addr.startswith("0x") and len(addr) == 42, f"bad addr: {addr}"
    return "0x" + ("00" * 12) + addr[2:].lower()


@pytest.mark.asyncio
async def test_verify_source_returns_true_on_match() -> None:
    """Live source equals expected → True. No ERROR log."""
    expected = "0x40EA33eA76Fbe35e9FB422eDd175b8c8D84A63Cc"
    stub = _StubClient(_StubResponse(json_body={
        "jsonrpc": "2.0", "id": 1,
        "result": _encode_address_result(expected),
    }))
    result = await verify_hyperlend_whype_source(client=stub)  # type: ignore[arg-type]
    assert result is True
    # Sanity: the eth_call payload targeted the configured oracle address.
    # (We don't pin-check the WHYPE token bytes — the encoded payload is
    # opaque hex and that's exercised at the implementation level.)
    assert stub.last_json["method"] == "eth_call"


@pytest.mark.asyncio
async def test_verify_source_returns_false_on_rotation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Live source has rotated → False + ERROR log line."""
    # Simulate rotation onto the kHYPE composite source (the specific
    # contamination case called out in arch_hyperevm_lending_audit.md).
    rotated = "0x6dcFA746f7b11918eF3522c92e6429CA589C3875"
    stub = _StubClient(_StubResponse(json_body={
        "jsonrpc": "2.0", "id": 1,
        "result": _encode_address_result(rotated),
    }))
    with caplog.at_level(logging.ERROR, logger="ocde.hyperevm_reader"):
        result = await verify_hyperlend_whype_source(client=stub)  # type: ignore[arg-type]
    assert result is False
    # The ERROR log MUST mention both expected and observed addresses so
    # an operator can grep + diff without re-reading the source.
    error_lines = [r for r in caplog.records if "oracle_source_drift" in r.message]
    assert len(error_lines) == 1
    assert error_lines[0].levelname == "ERROR"
    assert "0x40EA33eA76Fbe35e9FB422eDd175b8c8D84A63Cc" in error_lines[0].message
    # The rotated address should also appear (case-flex — we EIP-55 it).
    assert "6dcFA746f7b11918eF3522c92e6429CA589C3875".lower() in (
        error_lines[0].message.lower()
    )


@pytest.mark.asyncio
async def test_verify_source_returns_false_on_zero_address(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Source cleared (setSourceOfAsset(token, 0)) → False + dedicated message."""
    zero = "0x0000000000000000000000000000000000000000"
    stub = _StubClient(_StubResponse(json_body={
        "jsonrpc": "2.0", "id": 1,
        "result": _encode_address_result(zero),
    }))
    with caplog.at_level(logging.ERROR, logger="ocde.hyperevm_reader"):
        result = await verify_hyperlend_whype_source(client=stub)  # type: ignore[arg-type]
    assert result is False
    msgs = [r.message for r in caplog.records if "oracle_source_drift" in r.message]
    assert msgs and "zero-address" in msgs[0]


@pytest.mark.asyncio
async def test_verify_source_returns_none_on_rpc_failure() -> None:
    """RPC connection error → None (NOT True, NOT False)."""
    stub = _StubClient(raise_exc=httpx.ConnectError("conn refused"))
    result = await verify_hyperlend_whype_source(client=stub)  # type: ignore[arg-type]
    assert result is None


@pytest.mark.asyncio
async def test_verify_source_returns_none_on_timeout() -> None:
    """Timeout → None."""
    stub = _StubClient(raise_exc=httpx.TimeoutException("timed out"))
    result = await verify_hyperlend_whype_source(client=stub)  # type: ignore[arg-type]
    assert result is None


@pytest.mark.asyncio
async def test_verify_source_returns_none_on_non_200() -> None:
    """RPC returns 500 → None."""
    stub = _StubClient(_StubResponse(status_code=500, json_body={}))
    result = await verify_hyperlend_whype_source(client=stub)  # type: ignore[arg-type]
    assert result is None


@pytest.mark.asyncio
async def test_verify_source_returns_none_on_rpc_error_body() -> None:
    """JSON-RPC error response → None."""
    stub = _StubClient(_StubResponse(json_body={
        "jsonrpc": "2.0", "id": 1,
        "error": {"code": -32000, "message": "execution reverted"},
    }))
    result = await verify_hyperlend_whype_source(client=stub)  # type: ignore[arg-type]
    assert result is None


@pytest.mark.asyncio
async def test_verify_source_returns_none_on_short_result() -> None:
    """A revert often surfaces as `result=0x` — decode fails → None."""
    stub = _StubClient(_StubResponse(json_body={
        "jsonrpc": "2.0", "id": 1, "result": "0x",
    }))
    result = await verify_hyperlend_whype_source(client=stub)  # type: ignore[arg-type]
    assert result is None
