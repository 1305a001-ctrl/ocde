"""Pyth Hermes WS subscriber.

Subscribes to feed updates for the configured aliases via the Pyth
Hermes WebSocket and maintains an in-memory snapshot per asset.

Hermes WS protocol (May 2026):
  - Subscribe: {"type": "subscribe", "ids": ["0x<feed_id>", ...], "verbose": true}
  - Updates arrive as price_update events with Pyth's P², EMA, confidence,
    and per-publisher quotes (when verbose=true).

Snapshot fields:
  - price_usd:    aggregate median × 10^expo
  - confidence_usd: 1σ in USD (already scaled)
  - publisher_quotes: list of (publisher_id, price, conf) — for dispersion
  - timestamp_ms: publish time
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .dispersion import PublisherQuote
from .divergence import OraclePrice
from .settings import settings

log = logging.getLogger(__name__)


class PythSnapshot:
    """In-memory mirror of Pyth state, updated by the WS subscriber."""

    def __init__(self) -> None:
        # alias → (OraclePrice, list of PublisherQuote)
        self._snapshots: dict[str, tuple[OraclePrice, list[PublisherQuote]]] = {}

    def update(
        self,
        alias: str,
        price: OraclePrice,
        publishers: list[PublisherQuote],
    ) -> None:
        self._snapshots[alias] = (price, publishers)

    def get(self, alias: str) -> tuple[OraclePrice, list[PublisherQuote]] | None:
        return self._snapshots.get(alias)

    def all_aliases(self) -> list[str]:
        return list(self._snapshots.keys())


def parse_feed_ids() -> dict[str, str]:
    """Pure: alias → feed_id_hex (lowercase, no 0x prefix)."""
    out: dict[str, str] = {}
    for raw_pair in settings.pyth_feed_ids_csv.split(","):
        pair = raw_pair.strip()
        if not pair or ":" not in pair:
            continue
        alias, feed_id = pair.split(":", 1)
        feed_id_clean = feed_id.strip().removeprefix("0x").lower()
        if feed_id_clean:
            out[alias.strip()] = feed_id_clean
    return out


def _parse_price_update(
    raw: dict[str, Any],
    alias: str,
) -> tuple[OraclePrice, list[PublisherQuote]] | None:
    """Pure: turn a Hermes price_update payload into our types.

    Hermes shape (verbose=true):
    {
      "id": "0x...",
      "price": {"price": "...", "conf": "...", "expo": -8, "publish_time": ...},
      "ema_price": {...},
      "metadata": {...},
      // verbose payload — publishers list
      "vaa": "...",
    }

    Per-publisher quotes need a separate Hermes endpoint
    (/v2/updates/price/<feed_id>?publishers=true). We document this and
    parse what we can; full publisher dispersion needs a follow-up
    HTTP call OR a different endpoint that streams publisher data.
    """
    price_obj = raw.get("price") or {}
    raw_price = price_obj.get("price")
    raw_conf = price_obj.get("conf")
    expo = int(price_obj.get("expo", 0))
    publish_time = int(price_obj.get("publish_time", 0))

    if raw_price is None or raw_conf is None:
        return None

    try:
        scale = 10**expo
        price_usd = float(int(raw_price)) * scale
        conf_usd = float(int(raw_conf)) * scale
    except (TypeError, ValueError):
        return None
    if price_usd <= 0:
        return None

    op = OraclePrice(
        source="pyth",
        asset_alias=alias,
        price_usd=price_usd,
        confidence_usd=conf_usd,
        timestamp_ms=publish_time * 1000 if publish_time < 1e10 else int(publish_time),
    )

    # Publishers in the WS payload aren't always populated. We surface
    # an empty list here; the dispersion calculator handles min_publishers
    # and returns score=0 if too few.
    publishers: list[PublisherQuote] = []

    return op, publishers


async def run_subscriber(snapshot: PythSnapshot, *, stop_event: asyncio.Event) -> None:
    """Connect to Pyth Hermes WS and stream price updates into the snapshot.

    Reconnects with exponential backoff on disconnect (1s, 2s, 4s, ..., 60s cap).
    """
    feed_ids = parse_feed_ids()
    if not feed_ids:
        log.error("pyth_ws.no_feed_ids_configured")
        return

    backoff_sec = 1.0
    while not stop_event.is_set():
        try:
            await _stream_one(snapshot, feed_ids, stop_event)
            backoff_sec = 1.0   # reset on clean exit
        except Exception as e:
            log.exception("pyth_ws.disconnect err=%s; backoff=%.1fs", e, backoff_sec)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff_sec)
            except TimeoutError:
                pass
            backoff_sec = min(60.0, backoff_sec * 2.0)


async def _stream_one(
    snapshot: PythSnapshot,
    feed_ids: dict[str, str],
    stop_event: asyncio.Event,
) -> None:
    """One WebSocket connection lifetime."""
    import websockets

    url = settings.pyth_hermes_ws_url
    log.info("pyth_ws.connecting url=%s feeds=%d", url, len(feed_ids))

    async with websockets.connect(url, ping_interval=20.0) as ws:
        # Reverse map: feed_id → alias
        id_to_alias = {f"0x{fid}": alias for alias, fid in feed_ids.items()}

        # Subscribe
        await ws.send(json.dumps({
            "type": "subscribe",
            "ids": list(id_to_alias.keys()),
            "verbose": True,
        }))
        log.info("pyth_ws.subscribed n=%d", len(id_to_alias))

        async for raw in ws:
            if stop_event.is_set():
                break
            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if msg.get("type") != "price_update":
                continue

            feed_id = msg.get("price_feed", {}).get("id") or msg.get("id")
            if not feed_id:
                continue
            feed_id_norm = feed_id if feed_id.startswith("0x") else f"0x{feed_id}"
            alias = id_to_alias.get(feed_id_norm.lower())
            if alias is None:
                continue

            parsed = _parse_price_update(msg.get("price_feed") or msg, alias)
            if parsed is None:
                continue
            price, publishers = parsed
            snapshot.update(alias, price, publishers)


__all__ = ["PythSnapshot", "parse_feed_ids", "run_subscriber"]
