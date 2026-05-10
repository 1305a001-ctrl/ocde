"""FastAPI /health + /metrics endpoints."""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import FastAPI, Response

from .pyth_ws import PythSnapshot
from .redis_client import get_client
from .settings import settings

log = logging.getLogger(__name__)


def make_app(snapshot: PythSnapshot, started_at_ms: int) -> FastAPI:
    app = FastAPI(title="ocde", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        try:
            r = await get_client()
            await r.ping()
            redis_ok = True
        except Exception:
            redis_ok = False

        feeds = snapshot.all_aliases()
        snapshot_count = len(feeds)

        ok = redis_ok and snapshot_count > 0

        return {
            "status": "ok" if ok else "degraded",
            "redis": "ok" if redis_ok else "down",
            "pyth_aliases": feeds,
            "pyth_count": snapshot_count,
            "uptime_sec": int((time.time() * 1000 - started_at_ms) / 1000),
            "version": "0.1.0",
        }

    @app.get("/metrics")
    async def metrics() -> Response:
        # Minimal Prometheus exposition; expand once we have score history.
        feeds = snapshot.all_aliases()
        body = "\n".join([
            "# HELP ocde_pyth_feeds_active Number of Pyth feeds actively snapshotted",
            "# TYPE ocde_pyth_feeds_active gauge",
            f"ocde_pyth_feeds_active {len(feeds)}",
            "# HELP ocde_uptime_seconds Process uptime in seconds",
            "# TYPE ocde_uptime_seconds counter",
            f"ocde_uptime_seconds {int((time.time() * 1000 - started_at_ms) / 1000)}",
            "",
        ])
        return Response(content=body, media_type="text/plain")

    return app
