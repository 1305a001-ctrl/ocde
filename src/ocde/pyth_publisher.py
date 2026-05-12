"""Publish Pyth raw prices to Redis as `pyth:<alias>:latest`.

Mirrors the chainlink-streams Redis topology so downstream consumers
(liquidation-bot, strategy-runners) can fall back to Pyth for any asset
Chainlink doesn't entitle. Same key shape, same JSON payload fields,
just `source: "pyth"` instead of `source: "chainlink"`.

Throttled per-asset: at most one write per `pyth_publish_min_interval_ms`.
Pyth fires updates ~10x/sec for active assets; we don't need every tick
in Redis, just the latest. Reduces Redis write rate from ~3000/sec
(31 feeds × 10 Hz) to ~300/sec without losing freshness.
"""
from __future__ import annotations

import json
import logging
import time

from .divergence import OraclePrice
from .redis_client import get_client
from .settings import settings

log = logging.getLogger(__name__)


# In-memory throttle: alias → last publish ts (ms)
_last_publish_ms: dict[str, int] = {}


async def publish_pyth_price(price: OraclePrice) -> None:
    """Write Pyth latest to Redis. Throttled per-asset."""
    if not settings.pyth_publish_to_redis:
        return

    now_ms = int(time.time() * 1000)
    last = _last_publish_ms.get(price.asset_alias, 0)
    if now_ms - last < settings.pyth_publish_min_interval_ms:
        return

    payload = {
        "source": "pyth",
        "asset_alias": price.asset_alias,
        "benchmark_price_float64": price.price_usd,
        "benchmark_price": str(price.price_usd),
        "confidence_usd": price.confidence_usd,
        "timestamp_ms": price.timestamp_ms,
        "received_at_ms": now_ms,
    }

    try:
        r = await get_client()
        key = settings.pyth_latest_template.format(alias=price.asset_alias)
        # 60s TTL — if OCDE goes down, downstream sees stale-and-expired
        # rather than ancient prices.
        await r.set(key, json.dumps(payload), ex=60)
        _last_publish_ms[price.asset_alias] = now_ms
    except Exception:
        log.exception("pyth_publisher.set_failed alias=%s", price.asset_alias)


__all__ = ["publish_pyth_price"]
