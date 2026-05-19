"""Hyperliquid public order-book mid reader.

Hyperliquid's `/info` endpoint returns mids for every perpetual via the
`allMids` request type. We read HYPE-PERP only. The Hyperliquid order
book is almost certainly upstream of the RedStone HYPE oracle (RedStone
publishers aggregate from venues), so HL mid often leads both Streams
and RedStone — making the (HL vs others) gap a forward-looking signal.

Key-name verification (2026-05-20):
  - POST {"type":"meta"} to /info → "HYPE" is in the perp universe
    (maxLeverage=10, marginTableId=52). So `allMids["HYPE"]` is the
    PERP mid, not spot. Confirmed by absence of "HYPE" pair in spotMeta
    universe (HYPE exists as a spot token at index 150 but has no
    spot pair — perp is its primary venue).
  - If HL ever renames the perp (e.g. "HYPE-PERP", "@HYPE") the reader
    will return None with `missing_hype_key`. That's a loud failure,
    visible in journalctl — easy to spot and patch the key name.

Best-effort: failures (HTTP error, missing key, non-numeric value)
return None and log a warning. Caller degrades gracefully.

Note: allMids returns string prices (e.g. "47.94"), not floats — parse
defensively.
"""
from __future__ import annotations

import logging
import time

import httpx

from .divergence import OraclePrice
from .settings import settings

log = logging.getLogger(__name__)

_HTTP_TIMEOUT_S = 5.0


async def read_hl_hype_mid(  # noqa: PLR0911
    client: httpx.AsyncClient | None = None,
) -> OraclePrice | None:
    """Fetch the current HYPE-PERP mid from Hyperliquid's public API.

    The return-count is intentionally high — defensive parsing of an
    untrusted HTTP response surfaces ~7 distinct failure modes that each
    deserve their own warning + early return rather than nested branches.

    Args:
        client: optional reusable httpx.AsyncClient (tests inject mocks).
                When None, a fresh client is created with a short timeout.

    Returns:
        OraclePrice with source='hyperliquid', or None on any failure.
    """
    payload = {"type": "allMids"}

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S)

    try:
        try:
            resp = await client.post(settings.hyperliquid_api_url, json=payload)
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            log.warning("hyperliquid_reader.http_error err=%s", e)
            return None

        if resp.status_code != 200:
            log.warning("hyperliquid_reader.bad_status status=%d", resp.status_code)
            return None

        try:
            body = resp.json()
        except (ValueError, TypeError):
            log.warning("hyperliquid_reader.bad_json")
            return None

        if not isinstance(body, dict):
            log.warning("hyperliquid_reader.bad_body_shape")
            return None

        raw = body.get("HYPE")
        if raw is None:
            log.warning("hyperliquid_reader.missing_hype_key")
            return None

        try:
            price_usd = float(raw)
        except (ValueError, TypeError):
            log.warning("hyperliquid_reader.non_numeric_value raw=%r", raw)
            return None

        if price_usd <= 0:
            log.warning("hyperliquid_reader.non_positive_price price=%f", price_usd)
            return None

        return OraclePrice(
            source="hyperliquid",
            asset_alias="hype",
            price_usd=price_usd,
            confidence_usd=0.0,
            timestamp_ms=int(time.time() * 1000),
        )
    finally:
        if owns_client:
            await client.aclose()


__all__ = ["read_hl_hype_mid"]
