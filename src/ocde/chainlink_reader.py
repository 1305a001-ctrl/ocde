"""Read Chainlink Data Streams prices from Redis (published by chainlink-streams).

The chainlink-streams service writes JSON to `chainlink:<alias>:latest` per
asset. We mirror its format here so we don't import from that repo
(decoupled deployment).
"""
from __future__ import annotations

import json
import logging

from .divergence import OraclePrice
from .redis_client import get_client

log = logging.getLogger(__name__)


async def read_chainlink_price(alias: str) -> OraclePrice | None:
    """Fetch latest chainlink:<alias>:latest from Redis. Returns None
    if missing or malformed."""
    r = await get_client()
    key = f"chainlink:{alias}:latest"
    raw = await r.get(key)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        log.warning("chainlink_reader.bad_json key=%s", key)
        return None

    # chainlink-streams writes benchmark_price as a STRING (wei-scale int),
    # benchmark_price_float64 as a float (USD). Prefer the float; fall back
    # to parsing the string. NEVER compare the raw string to a number.
    price_raw = data.get("benchmark_price_float64")
    if price_raw is None:
        price_str = data.get("benchmark_price") or data.get("price")
        try:
            price_raw = float(price_str) if price_str is not None else None
        except (ValueError, TypeError):
            price_raw = None

    if price_raw is None:
        return None
    try:
        price_usd = float(price_raw)
    except (ValueError, TypeError):
        return None
    if price_usd <= 0:
        return None

    # Timestamp: prefer received_at_ns (sub-ms), then valid_from_ts*1000,
    # then any explicit timestamp_ms field. All produce an int ms.
    ts_ms = 0
    if data.get("received_at_ns"):
        try:
            ts_ms = int(data["received_at_ns"]) // 1_000_000
        except (ValueError, TypeError):
            ts_ms = 0
    if ts_ms == 0 and data.get("valid_from_ts"):
        try:
            ts_ms = int(data["valid_from_ts"]) * 1000
        except (ValueError, TypeError):
            ts_ms = 0
    if ts_ms == 0 and data.get("timestamp_ms"):
        try:
            ts_ms = int(data["timestamp_ms"])
        except (ValueError, TypeError):
            ts_ms = 0

    return OraclePrice(
        source="chainlink",
        asset_alias=alias,
        price_usd=price_usd,
        confidence_usd=0.0,    # Chainlink Data Streams doesn't publish ±band
        timestamp_ms=ts_ms,
    )


async def read_chainlink_prices(aliases: list[str]) -> dict[str, OraclePrice]:
    """Batch-read multiple aliases. Returns only those that succeed."""
    out: dict[str, OraclePrice] = {}
    for a in aliases:
        p = await read_chainlink_price(a)
        if p is not None:
            out[a] = p
    return out


__all__ = ["read_chainlink_price", "read_chainlink_prices"]
