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

    price = data.get("benchmark_price") or data.get("price")
    ts_ms = data.get("timestamp_ms") or (
        int(data.get("timestamp", 0) * 1000) if "timestamp" in data else 0
    )
    if price is None or price <= 0:
        return None

    return OraclePrice(
        source="chainlink",
        asset_alias=alias,
        price_usd=float(price),
        confidence_usd=0.0,    # Chainlink Data Streams doesn't publish ±band
        timestamp_ms=int(ts_ms),
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
