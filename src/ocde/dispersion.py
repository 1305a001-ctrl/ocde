"""Publisher dispersion — the Pyth-internal disagreement signal.

Pyth aggregates from multiple publishers (Jane Street, Jump, Wintermute,
Cumberland, etc.). When publishers DISAGREE materially among themselves,
the underlying market has structural disagreement — typically because
one venue is leading or there's a regime change.

This module computes the spread between the highest and lowest publisher
prices (normalized to bps), and a "dispersion score" 0.0 → 1.0.

Pyth doesn't publicly expose individual publisher quotes via WS by default,
but does via the Hermes API. We document both paths and code against the
Hermes payload structure.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass


@dataclass(frozen=True)
class PublisherQuote:
    """One publisher's quote within a Pyth aggregate."""
    publisher_id: str        # e.g. "jane_street", "jump"
    price_usd: float
    confidence_usd: float
    slot: int                # Solana slot number when quote published


@dataclass(frozen=True)
class DispersionSignal:
    asset_alias: str
    n_publishers: int
    median_price_usd: float
    spread_bps: float        # (max - min) / median in bps
    iqr_bps: float           # interquartile range in bps; resilient outlier metric
    score: float             # 0.0 to 1.0
    outlier_publishers: tuple[str, ...]  # publishers > 2σ from median
    reason: str


def compute_dispersion(
    asset_alias: str,
    quotes: list[PublisherQuote],
    *,
    min_publishers: int = 3,
    significance_threshold_bps: float = 2.0,
) -> DispersionSignal:
    """Pure: compute the publisher dispersion across one Pyth aggregate.

    Returns score 0.0 if:
      - Fewer than min_publishers (default 3) publishers
      - Median price is 0 or negative
      - Spread below significance_threshold_bps (within normal noise)

    Score scales with IQR (interquartile range) since IQR is robust to
    a single mis-pricing outlier:
      - IQR < 2bps  → 0.0 (normal)
      - IQR 2-20bps → 0.0 to 0.6 (notable)
      - IQR 20bps+  → 0.6 to 1.0 (strong dispersion)
    """
    if len(quotes) < min_publishers:
        return _empty(asset_alias, f"too_few_publishers_n={len(quotes)}")

    prices = [q.price_usd for q in quotes if q.price_usd > 0]
    if len(prices) < min_publishers:
        return _empty(asset_alias, "too_few_valid_prices")

    median_price = statistics.median(prices)
    if median_price <= 0:
        return _empty(asset_alias, "zero_median")

    spread_bps = ((max(prices) - min(prices)) / median_price) * 10_000.0

    # IQR — robust to single outlier
    sorted_prices = sorted(prices)
    n = len(sorted_prices)
    q1 = sorted_prices[n // 4]
    q3 = sorted_prices[3 * n // 4]
    iqr_bps = ((q3 - q1) / median_price) * 10_000.0

    # Outlier detection: > 2σ from median (use stdev for tolerance,
    # not IQR, because stdev penalizes lone outlier publishers)
    stdev = statistics.stdev(prices) if len(prices) > 1 else 0.0
    outliers = tuple(
        q.publisher_id for q in quotes
        if stdev > 0 and abs(q.price_usd - median_price) > 2.0 * stdev
    )

    if iqr_bps < significance_threshold_bps:
        return DispersionSignal(
            asset_alias=asset_alias,
            n_publishers=len(quotes),
            median_price_usd=median_price,
            spread_bps=spread_bps,
            iqr_bps=iqr_bps,
            score=0.0,
            outlier_publishers=outliers,
            reason="below_significance_threshold",
        )

    # Linear ramp: 2bps → 0.0 ... 20bps → 0.6 ... 50bps+ → 1.0
    if iqr_bps < 20.0:
        score = 0.6 * (iqr_bps - 2.0) / 18.0
    elif iqr_bps < 50.0:
        score = 0.6 + 0.4 * (iqr_bps - 20.0) / 30.0
    else:
        score = 1.0

    reason = f"iqr_{iqr_bps:.1f}bps_n={len(quotes)}"
    if outliers:
        reason += f"_outliers={','.join(outliers)}"

    return DispersionSignal(
        asset_alias=asset_alias,
        n_publishers=len(quotes),
        median_price_usd=median_price,
        spread_bps=spread_bps,
        iqr_bps=iqr_bps,
        score=score,
        outlier_publishers=outliers,
        reason=reason,
    )


def _empty(asset: str, reason: str) -> DispersionSignal:
    return DispersionSignal(
        asset_alias=asset,
        n_publishers=0,
        median_price_usd=0.0,
        spread_bps=0.0,
        iqr_bps=0.0,
        score=0.0,
        outlier_publishers=(),
        reason=reason,
    )


__all__ = ["DispersionSignal", "PublisherQuote", "compute_dispersion"]
