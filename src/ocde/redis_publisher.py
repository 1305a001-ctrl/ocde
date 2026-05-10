"""Publish composite scores to Redis.

Two writes per asset per cycle:
  - XADD ocde:scores:<alias>  (history stream, capped at maxlen)
  - SET  ocde:score:<alias>:latest  (cheap latest-only read for downstream)

The latest key has a TTL of 5x the cycle interval so consumers know the
score is stale if it expires.
"""
from __future__ import annotations

import json
import logging
import time

from .composite import CompositeScore
from .redis_client import get_client
from .settings import settings

log = logging.getLogger(__name__)


async def publish_score(score: CompositeScore) -> None:
    """Push the composite score to both stream + latest key.

    Filters out below-threshold scores from the stream to avoid noise,
    BUT always writes to the latest key (so consumers know we're alive).
    """
    r = await get_client()
    payload = {
        "alias": score.asset_alias,
        "composite": score.composite,
        "divergence": score.divergence,
        "confidence": score.confidence_widening,
        "dispersion": score.dispersion,
        "weights": list(score.weights),
        "reason": score.reason,
        "ts_ms": int(time.time() * 1000),
    }
    payload_json = json.dumps(payload)

    # Always write latest (so consumers can detect stale)
    latest_key = settings.score_latest_template.format(alias=score.asset_alias)
    ttl_sec = max(5, int(settings.cycle_interval_sec * 5))
    try:
        await r.set(latest_key, payload_json, ex=ttl_sec)
    except Exception:
        log.exception("publisher.set_latest_failed alias=%s", score.asset_alias)

    # Stream only above-threshold (signal-only history)
    if score.composite >= settings.composite_publish_threshold:
        stream_key = settings.score_stream_template.format(alias=score.asset_alias)
        try:
            await r.xadd(
                stream_key,
                {"payload": payload_json},
                maxlen=settings.score_stream_maxlen,
                approximate=True,
            )
        except Exception:
            log.exception("publisher.xadd_failed alias=%s", score.asset_alias)


__all__ = ["publish_score"]
