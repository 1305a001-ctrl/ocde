"""Cross-oracle divergence: Pyth vs Chainlink Data Streams.

When the same asset has materially different prices on two independent
oracle sources, one of the following is happening:
  1. One oracle hasn't updated yet — divergence will close in seconds.
     Tradable signal: the lagging oracle is about to move.
  2. One oracle has stale data (publisher offline, network partition).
     NOT tradable — feed should be quarantined.
  3. Real market dislocation between the venues each oracle aggregates.
     Tradable on the slower-to-update venue.

This module is PURE — input prices in, score out. No I/O, no Redis.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OraclePrice:
    """One oracle's snapshot of an asset price."""
    source: str            # "pyth" or "chainlink"
    asset_alias: str
    price_usd: float
    confidence_usd: float  # ±1σ in USD; 0 for Chainlink Data Streams
    timestamp_ms: int      # publish time in ms


@dataclass(frozen=True)
class DivergenceSignal:
    """Result of comparing two oracle prices for the same asset."""
    asset_alias: str
    pyth_price_usd: float
    chainlink_price_usd: float
    abs_diff_usd: float
    rel_diff_bps: float    # basis points (10000 = 100%)
    confidence_overlap: bool   # do the ±1σ bands overlap?
    age_diff_sec: float        # how stale is the older one
    score: float           # 0.0 (no signal) to 1.0 (strong signal)
    reason: str            # explanation for downstream logging


def compute_divergence(
    pyth: OraclePrice,
    chainlink: OraclePrice,
    *,
    significance_threshold_bps: float = 5.0,   # 5bps = 0.05%
    max_age_diff_sec: float = 30.0,
) -> DivergenceSignal:
    """Pure: compare two oracle prices, score the divergence.

    Score is 0.0 if any of these are true:
      - Different assets (programmer error — caller should have filtered)
      - Confidence bands overlap (rel_diff is within 1σ noise)
      - Age difference exceeds max_age_diff_sec (one feed is stale)
      - rel_diff < significance_threshold_bps

    Otherwise score scales linearly with rel_diff up to 100bps (1%):
      - 5bps  → 0.05
      - 50bps → 0.5
      - 100bps+ → 1.0
    """
    if pyth.asset_alias != chainlink.asset_alias:
        return _empty(asset=pyth.asset_alias, reason="asset_mismatch")

    pyth_p = pyth.price_usd
    cl_p = chainlink.price_usd
    if pyth_p <= 0 or cl_p <= 0:
        return _empty(asset=pyth.asset_alias, reason="non_positive_price")

    abs_diff = abs(pyth_p - cl_p)
    mid = (pyth_p + cl_p) / 2.0
    rel_diff_bps = (abs_diff / mid) * 10_000.0 if mid > 0 else 0.0

    # Confidence-band overlap: if Pyth's ±1σ contains chainlink's price,
    # the divergence is within Pyth's stated uncertainty — no signal.
    # Require confidence > 0 to count (zero-conf doesn't trigger overlap).
    confidence_overlap = (
        pyth.confidence_usd > 0 and abs(pyth_p - cl_p) <= pyth.confidence_usd
    )

    age_diff_sec = abs(pyth.timestamp_ms - chainlink.timestamp_ms) / 1000.0

    if age_diff_sec > max_age_diff_sec:
        return DivergenceSignal(
            asset_alias=pyth.asset_alias,
            pyth_price_usd=pyth_p,
            chainlink_price_usd=cl_p,
            abs_diff_usd=abs_diff,
            rel_diff_bps=rel_diff_bps,
            confidence_overlap=confidence_overlap,
            age_diff_sec=age_diff_sec,
            score=0.0,
            reason=f"feed_stale_age={age_diff_sec:.1f}s",
        )

    if confidence_overlap:
        return DivergenceSignal(
            asset_alias=pyth.asset_alias,
            pyth_price_usd=pyth_p,
            chainlink_price_usd=cl_p,
            abs_diff_usd=abs_diff,
            rel_diff_bps=rel_diff_bps,
            confidence_overlap=True,
            age_diff_sec=age_diff_sec,
            score=0.0,
            reason="within_pyth_confidence",
        )

    if rel_diff_bps < significance_threshold_bps:
        return DivergenceSignal(
            asset_alias=pyth.asset_alias,
            pyth_price_usd=pyth_p,
            chainlink_price_usd=cl_p,
            abs_diff_usd=abs_diff,
            rel_diff_bps=rel_diff_bps,
            confidence_overlap=False,
            age_diff_sec=age_diff_sec,
            score=0.0,
            reason="below_significance_threshold",
        )

    # Linear ramp 0 → 1 across [significance_threshold, 100bps]
    score = min(1.0, max(0.0, rel_diff_bps / 100.0))

    return DivergenceSignal(
        asset_alias=pyth.asset_alias,
        pyth_price_usd=pyth_p,
        chainlink_price_usd=cl_p,
        abs_diff_usd=abs_diff,
        rel_diff_bps=rel_diff_bps,
        confidence_overlap=False,
        age_diff_sec=age_diff_sec,
        score=score,
        reason=f"divergence_{rel_diff_bps:.1f}bps",
    )


def _empty(*, asset: str, reason: str) -> DivergenceSignal:
    return DivergenceSignal(
        asset_alias=asset,
        pyth_price_usd=0.0,
        chainlink_price_usd=0.0,
        abs_diff_usd=0.0,
        rel_diff_bps=0.0,
        confidence_overlap=False,
        age_diff_sec=0.0,
        score=0.0,
        reason=reason,
    )


__all__ = [
    "DivergenceSignal",
    "OraclePrice",
    "compute_divergence",
]
