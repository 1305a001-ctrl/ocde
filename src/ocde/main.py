"""OCDE entrypoint — three concurrent loops:
  1. Pyth WS subscriber (long-lived; reconnects on disconnect).
  2. Composite-scoring loop (every cycle_interval_sec).
  3. FastAPI /health + /metrics (uvicorn).
"""
from __future__ import annotations

import asyncio
import signal
import time

import structlog
import uvicorn

from .chainlink_reader import read_chainlink_prices
from .composite import compute_composite
from .confidence import ConfidenceSnapshot, ConfidenceTracker
from .dispersion import compute_dispersion
from .divergence import compute_divergence
from .health import make_app
from .pyth_publisher import publish_pyth_price
from .pyth_ws import PythSnapshot, parse_feed_ids, run_subscriber
from .redis_client import close as close_redis
from .redis_publisher import publish_score
from .settings import settings

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)
log = structlog.get_logger(__name__)


async def scoring_loop(
    pyth_snap: PythSnapshot,
    confidence_tracker: ConfidenceTracker,
    stop_event: asyncio.Event,
) -> None:
    """Every cycle: fetch chainlink prices, combine with pyth snapshot,
    compute components, write composite to Redis."""
    aliases = [a.strip() for a in settings.chainlink_feed_aliases_csv.split(",") if a.strip()]
    weights = (
        settings.composite_weight_divergence,
        settings.composite_weight_confidence,
        settings.composite_weight_dispersion,
    )

    while not stop_event.is_set():
        cycle_start = time.monotonic()
        try:
            chainlink_prices = await read_chainlink_prices(aliases)

            for alias in aliases:
                pyth_state = pyth_snap.get(alias)
                if pyth_state is None:
                    continue
                pyth_price, publishers = pyth_state
                cl_price = chainlink_prices.get(alias)

                # 1. Divergence (needs both)
                div_sig = None
                if cl_price is not None:
                    div_sig = compute_divergence(pyth_price, cl_price)

                # 2. Confidence widening (Pyth-only)
                conf_sig = confidence_tracker.observe(ConfidenceSnapshot(
                    asset_alias=alias,
                    price_usd=pyth_price.price_usd,
                    confidence_usd=pyth_price.confidence_usd,
                    timestamp_ms=pyth_price.timestamp_ms,
                ))

                # 3. Publisher dispersion (Pyth-only — empty list yields score 0)
                disp_sig = compute_dispersion(alias, publishers)

                # 4. Composite
                composite = compute_composite(
                    alias,
                    divergence=div_sig,
                    confidence=conf_sig,
                    dispersion=disp_sig,
                    weights=weights,
                )

                await publish_score(composite)

            # Publish ALL Pyth raw prices to Redis (for downstream fallback).
            # This includes Pyth-only assets where Chainlink doesn't entitle us
            # (USDC/USDT/WSTETH/etc) — liquidation-bot reads pyth:<alias>:latest
            # when chainlink:<alias>:latest is missing.
            for pyth_alias in pyth_snap.all_aliases():
                pyth_state = pyth_snap.get(pyth_alias)
                if pyth_state is not None:
                    await publish_pyth_price(pyth_state[0])

        except Exception as e:
            log.exception("scoring_loop.cycle_failed err=%s", e)

        elapsed = time.monotonic() - cycle_start
        sleep_for = max(0.0, settings.cycle_interval_sec - elapsed)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=sleep_for)
        except TimeoutError:
            pass


async def main() -> None:
    log.info("ocde.start version=0.1.0")

    feed_ids = parse_feed_ids()
    if not feed_ids:
        log.error("ocde.no_pyth_feeds_configured PYTH_FEED_IDS_CSV is empty")
        return

    pyth_snap = PythSnapshot()
    confidence_tracker = ConfidenceTracker(window_n=settings.confidence_window_n)
    stop_event = asyncio.Event()
    started_at_ms = int(time.time() * 1000)

    # FastAPI app + uvicorn server config (so we can shut it down gracefully)
    app = make_app(pyth_snap, started_at_ms)
    config = uvicorn.Config(
        app,
        host=settings.http_host,
        port=settings.http_port,
        log_level=settings.log_level.lower(),
    )
    server = uvicorn.Server(config)

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    await asyncio.gather(
        run_subscriber(pyth_snap, stop_event=stop_event),
        scoring_loop(pyth_snap, confidence_tracker, stop_event),
        server.serve(),
        return_exceptions=True,
    )

    log.info("ocde.shutdown_started")
    server.should_exit = True
    await close_redis()
    log.info("ocde.shutdown_complete")


if __name__ == "__main__":
    asyncio.run(main())
