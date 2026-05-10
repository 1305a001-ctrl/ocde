"""Redis client wrapper. Lazy connect; reuses one connection per asyncio task."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .settings import settings

log = logging.getLogger(__name__)

_client: Any = None
_lock = asyncio.Lock()


async def get_client() -> Any:
    global _client
    async with _lock:
        if _client is None:
            from redis.asyncio import Redis
            _client = Redis.from_url(settings.redis_url, decode_responses=True)
        return _client


async def close() -> None:
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            log.exception("redis_close_failed")
        _client = None
