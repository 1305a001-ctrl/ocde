"""Confidence widening — pre-volatility signal.

Pyth publishes price + confidence band. When the confidence band
WIDENS rapidly relative to its recent baseline, it means publishers
disagree more than usual — a leading indicator of imminent volatility.

This is a known signal in the Pyth literature but rarely used as a
DECISION INPUT for downstream strategies. We compute a per-asset
"confidence widening score" that scales 0.0 → 1.0 as current
confidence (in bps) exceeds its rolling-window baseline.
"""
from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class ConfidenceSnapshot:
    """One Pyth confidence reading."""
    asset_alias: str
    price_usd: float
    confidence_usd: float
    timestamp_ms: int


@dataclass(frozen=True)
class WideningSignal:
    asset_alias: str
    current_conf_bps: float
    baseline_conf_bps: float
    widening_ratio: float      # current / baseline; 1.0 = no widening
    score: float               # 0.0 to 1.0
    reason: str


class ConfidenceTracker:
    """Stateful: maintains a rolling window of Pyth confidence readings
    per asset and computes the widening score on each new sample.

    Window: last N samples (default 60 ≈ 10 min at 10s sampling).
    """

    def __init__(self, *, window_n: int = 60) -> None:
        self._window: dict[str, deque[float]] = {}
        self._window_n = window_n

    def observe(self, snap: ConfidenceSnapshot) -> WideningSignal:
        """Pure-ish (state in self): compute widening signal for this snapshot."""
        if snap.price_usd <= 0:
            return _empty(snap.asset_alias, "non_positive_price")

        current_conf_bps = (snap.confidence_usd / snap.price_usd) * 10_000.0

        history = self._window.setdefault(snap.asset_alias, deque(maxlen=self._window_n))

        if len(history) < 5:
            # Need at least 5 samples to establish baseline
            history.append(current_conf_bps)
            return _empty(snap.asset_alias, "warmup_insufficient_samples")

        baseline_bps = statistics.median(history)
        history.append(current_conf_bps)

        if baseline_bps <= 0:
            return _empty(snap.asset_alias, "zero_baseline")

        widening_ratio = current_conf_bps / baseline_bps

        # Score scales:
        #   1.0× to 2.0×  → 0.0 (normal noise)
        #   2.0× to 5.0×  → 0.0 to 0.7 (notable widening)
        #   5.0×+         → 0.7 to 1.0 (strong widening)
        if widening_ratio < 2.0:
            score = 0.0
            reason = "within_normal_band"
        elif widening_ratio < 5.0:
            score = 0.7 * (widening_ratio - 2.0) / 3.0
            reason = f"widening_{widening_ratio:.1f}x_baseline"
        else:
            score = min(1.0, 0.7 + 0.3 * (widening_ratio - 5.0) / 5.0)
            reason = f"strong_widening_{widening_ratio:.1f}x_baseline"

        return WideningSignal(
            asset_alias=snap.asset_alias,
            current_conf_bps=current_conf_bps,
            baseline_conf_bps=baseline_bps,
            widening_ratio=widening_ratio,
            score=score,
            reason=reason,
        )

    def baseline(self, asset_alias: str) -> float | None:
        """Pure: current baseline confidence in bps for an asset."""
        history = self._window.get(asset_alias)
        if not history or len(history) < 5:
            return None
        return statistics.median(history)


def _empty(asset: str, reason: str) -> WideningSignal:
    return WideningSignal(
        asset_alias=asset,
        current_conf_bps=0.0,
        baseline_conf_bps=0.0,
        widening_ratio=0.0,
        score=0.0,
        reason=reason,
    )


__all__ = ["ConfidenceSnapshot", "ConfidenceTracker", "WideningSignal"]
