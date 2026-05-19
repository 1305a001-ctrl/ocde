"""RedStone on-chain HYPE/USD reader via raw JSON-RPC.

HyperLend (the largest HyperEVM lending market) prices HYPE off RedStone
push feeds, not Chainlink Data Streams. To measure the gap between our
edge feed (Streams) and what HyperLend actually reads, we call the
RedStone price source contract directly through `eth_call`.

Implementation note: this module deliberately uses raw httpx JSON-RPC
rather than web3.py — OCDE has a hard no-new-dependencies rule and httpx
is already pinned for the Pyth REST path. The function is best-effort:
any failure (HTTP error, malformed result, contract revert) returns
None and logs a warning. The caller (hype_divergence_loop) must tolerate
None and degrade gracefully.

Timestamp: we record `time.time() * 1000` at read time. RedStone exposes
`latestRoundData()` for the true on-chain update time, but for divergence
the read-time stamp is sufficient — the gap is what we care about, not
absolute freshness. If we later need on-chain age, swap selector to
`0xfeaf968c` (`latestRoundData()`) and decode the 5-tuple.
"""
from __future__ import annotations

import logging
import time

import httpx

from .divergence import OraclePrice
from .settings import settings

log = logging.getLogger(__name__)

# Selector for `latestAnswer() returns (int256)` — RedStone aggregator interface.
_LATEST_ANSWER_SELECTOR = "0x50d25bcd"
_HTTP_TIMEOUT_S = 5.0


def _decode_int256(hex_result: str) -> int | None:
    """Decode a 32-byte hex int256 (two's complement) from an eth_call result.

    Returns None for malformed input. Empty/short results (a reverted call
    typically returns '0x') are treated as malformed.
    """
    if not isinstance(hex_result, str) or not hex_result.startswith("0x"):
        return None
    hex_body = hex_result[2:]
    if len(hex_body) != 64:
        return None
    try:
        raw = int(hex_body, 16)
    except ValueError:
        return None
    # Two's complement: if the top bit is set, this is negative.
    if raw >= 1 << 255:
        raw -= 1 << 256
    return raw


async def read_redstone_hype_price(  # noqa: PLR0911
    client: httpx.AsyncClient | None = None,
) -> OraclePrice | None:
    """Fetch RedStone HYPE/USD via eth_call to the price source contract.

    The return-count is intentionally high — defensive parsing of an
    untrusted RPC response surfaces ~7 distinct failure modes that each
    deserve their own warning + early return rather than nested branches.

    Args:
        client: optional reusable httpx.AsyncClient (tests inject mocks).
                When None, a fresh client is created with a short timeout.

    Returns:
        OraclePrice with source='redstone', or None on any failure.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [
            {
                "to": settings.redstone_hype_source,
                "data": _LATEST_ANSWER_SELECTOR,
            },
            "latest",
        ],
    }

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S)

    try:
        try:
            resp = await client.post(settings.hyperevm_rpc_url, json=payload)
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            log.warning("hyperevm_reader.http_error err=%s", e)
            return None

        if resp.status_code != 200:
            log.warning("hyperevm_reader.bad_status status=%d", resp.status_code)
            return None

        try:
            body = resp.json()
        except (ValueError, TypeError):
            log.warning("hyperevm_reader.bad_json")
            return None

        if not isinstance(body, dict):
            log.warning("hyperevm_reader.bad_body_shape")
            return None

        if "error" in body:
            log.warning("hyperevm_reader.rpc_error err=%s", body["error"])
            return None

        result = body.get("result")
        if result is None:
            log.warning("hyperevm_reader.missing_result")
            return None

        raw_int = _decode_int256(result)
        if raw_int is None:
            log.warning("hyperevm_reader.decode_failed result=%s", result)
            return None
        if raw_int <= 0:
            log.warning("hyperevm_reader.non_positive_price raw=%d", raw_int)
            return None

        price_usd = raw_int / (10 ** settings.redstone_hype_decimals)
        ts_ms = int(time.time() * 1000)
        # Note: ts_ms is read time, not the on-chain update time. Use
        # latestRoundData() (selector 0xfeaf968c) if true age is needed.

        return OraclePrice(
            source="redstone",
            asset_alias="hype",
            price_usd=price_usd,
            confidence_usd=0.0,
            timestamp_ms=ts_ms,
        )
    finally:
        if owns_client:
            await client.aclose()


__all__ = ["read_redstone_hype_price"]
