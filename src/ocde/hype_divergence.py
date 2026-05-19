"""HYPE 3-source divergence — pure math primitive.

Combines three independent HYPE price sources:
  1. Chainlink Data Streams (our sub-second edge feed)
  2. RedStone on-chain (what HyperLend lending reads)
  3. Hyperliquid order-book mid (likely upstream venue)

Emits a HypeDivergenceSignal capturing pairwise bps gaps, the leader
(the source furthest from the median of available 2+ sources), and
velocity (rate-of-change of max_div_bps over a rolling window).

Pure — no I/O, no Redis. Caller maintains the rolling-history list and
passes it in each cycle. This keeps the module trivially unit-testable.

Edge cases:
  - 0 sources: max_div_bps=0.0, reason="no_sources", leader=None
  - 1 source : max_div_bps=0.0, reason="single_source", leader=None
  - 2 sources: one pair, leader is the source furthest from the pair median
               (the pair median = the average of the two — so the "leader"
               is whichever is further from that average, but they are
               equidistant. We document this and pick the higher-priced
               source as a deterministic convention. This is a synthetic
               leader for the 2-source case; consumers should treat the
               leader field as advisory below 3 sources.)
  - 3 sources: three pairs, leader = source with largest |price - median|

Velocity: rate of change of max_div_bps per minute over the history
window. Returns None if fewer than 2 history entries or if the time
span is < 1ms (avoid div-by-zero).
"""
from __future__ import annotations

from dataclasses import dataclass

from .divergence import OraclePrice


@dataclass(frozen=True)
class HypeDivergenceSignal:
    """Result of comparing up to three HYPE oracle sources."""
    ts_ms: int
    streams_price: float | None
    redstone_price: float | None
    hl_mid: float | None
    div_streams_redstone_bps: float | None
    div_streams_hl_bps: float | None
    div_redstone_hl_bps: float | None
    max_div_bps: float
    leader: str | None        # 'streams' | 'redstone' | 'hl' | None
    velocity_bps_per_min: float | None
    reason: str | None


class VelocityHistory:
    """Rolling list of (ts_ms, max_div_bps) capped at window_n.

    Caller pattern:
        history = VelocityHistory(window_n=12)
        ...each cycle...
        sig = compute_hype_divergence(..., history=history.entries, threshold_bps=30.0)
        history.append(sig.ts_ms, sig.max_div_bps)
    """

    def __init__(self, *, window_n: int) -> None:
        self._window_n = window_n
        self._entries: list[tuple[int, float]] = []

    def append(self, ts_ms: int, max_div_bps: float) -> None:
        self._entries.append((ts_ms, max_div_bps))
        # Trim oldest beyond the cap
        if len(self._entries) > self._window_n:
            self._entries = self._entries[-self._window_n :]

    @property
    def entries(self) -> list[tuple[int, float]]:
        """Read-only-ish view (return a copy to discourage external mutation)."""
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)


def _pair_bps(a: float, b: float) -> float:
    """abs(a - b) / mid * 10000. Returns 0.0 if mid is zero or both are zero."""
    mid = (a + b) / 2.0
    if mid <= 0:
        return 0.0
    return abs(a - b) / mid * 10_000.0


def _median3(a: float, b: float, c: float) -> float:
    """Median of three floats. Branchless for clarity."""
    return sorted([a, b, c])[1]


def _compute_velocity(history: list[tuple[int, float]]) -> float | None:
    """Rate of change of max_div_bps in bps-per-minute over the window.

    Uses first vs last sample (simple, robust). Returns None with fewer than
    2 samples or when the time span is < 1ms (would div-by-zero).
    """
    if len(history) < 2:
        return None
    t0, v0 = history[0]
    t1, v1 = history[-1]
    dt_ms = t1 - t0
    if dt_ms <= 0:
        return None
    dt_min = dt_ms / 60_000.0
    return (v1 - v0) / dt_min


def compute_hype_divergence(
    streams: OraclePrice | None,
    redstone: OraclePrice | None,
    hl: OraclePrice | None,
    *,
    history: list[tuple[int, float]],
    threshold_bps: float,
) -> HypeDivergenceSignal:
    """Pure: compute the 3-source divergence signal.

    Args:
        streams:  Chainlink Data Streams HYPE OraclePrice, or None on read fail.
        redstone: RedStone on-chain HYPE OraclePrice, or None.
        hl:       Hyperliquid order-book mid OraclePrice, or None.
        history:  list of prior (ts_ms, max_div_bps) — caller maintains.
        threshold_bps: emit reason="below_threshold" when max_div_bps is under.

    Returns:
        HypeDivergenceSignal — always returned; check `reason` for context.
    """
    # Determine cycle timestamp: prefer streams (sub-second age) > redstone > hl;
    # fall back to 0 only if all sources are None (caller should not call us in
    # that case but we degrade gracefully).
    ts_ms = 0
    for src in (streams, redstone, hl):
        if src is not None and src.timestamp_ms > 0:
            ts_ms = src.timestamp_ms
            break

    sp = streams.price_usd if streams is not None else None
    rp = redstone.price_usd if redstone is not None else None
    hp = hl.price_usd if hl is not None else None

    present_count = sum(1 for p in (sp, rp, hp) if p is not None and p > 0)

    # Pairwise gaps (only when both members are present and positive)
    def _ok(x: float | None, y: float | None) -> bool:
        return x is not None and y is not None and x > 0 and y > 0

    div_sr = _pair_bps(sp, rp) if _ok(sp, rp) else None  # type: ignore[arg-type]
    div_sh = _pair_bps(sp, hp) if _ok(sp, hp) else None  # type: ignore[arg-type]
    div_rh = _pair_bps(rp, hp) if _ok(rp, hp) else None  # type: ignore[arg-type]

    velocity = _compute_velocity(history)

    if present_count == 0:
        return HypeDivergenceSignal(
            ts_ms=ts_ms,
            streams_price=sp,
            redstone_price=rp,
            hl_mid=hp,
            div_streams_redstone_bps=div_sr,
            div_streams_hl_bps=div_sh,
            div_redstone_hl_bps=div_rh,
            max_div_bps=0.0,
            leader=None,
            velocity_bps_per_min=velocity,
            reason="no_sources",
        )

    if present_count == 1:
        return HypeDivergenceSignal(
            ts_ms=ts_ms,
            streams_price=sp,
            redstone_price=rp,
            hl_mid=hp,
            div_streams_redstone_bps=div_sr,
            div_streams_hl_bps=div_sh,
            div_redstone_hl_bps=div_rh,
            max_div_bps=0.0,
            leader=None,
            velocity_bps_per_min=velocity,
            reason="single_source",
        )

    # 2 or 3 sources: pick the max of available pairwise gaps.
    pairs = [p for p in (div_sr, div_sh, div_rh) if p is not None]
    max_bps = max(pairs) if pairs else 0.0

    # Leader detection
    leader: str | None = None
    if present_count == 3:
        # Median of three, then leader = source furthest from median.
        # sp/rp/hp are all not-None here per present_count == 3.
        med = _median3(sp, rp, hp)  # type: ignore[arg-type]
        candidates = [
            ("streams", abs(sp - med)),  # type: ignore[operator]
            ("redstone", abs(rp - med)),  # type: ignore[operator]
            ("hl", abs(hp - med)),  # type: ignore[operator]
        ]
        leader = max(candidates, key=lambda kv: kv[1])[0]
    else:
        # 2-source case: the pair's "median" is its average, so both members
        # are equidistant. Per docstring, deterministically pick the higher-
        # priced source as the synthetic leader. This is advisory; consumers
        # should treat leader as soft below 3 sources.
        active: list[tuple[str, float]] = []
        if sp is not None and sp > 0:
            active.append(("streams", sp))
        if rp is not None and rp > 0:
            active.append(("redstone", rp))
        if hp is not None and hp > 0:
            active.append(("hl", hp))
        if len(active) == 2:
            leader = max(active, key=lambda kv: kv[1])[0]

    reason: str | None = None
    if max_bps < threshold_bps:
        reason = "below_threshold"

    return HypeDivergenceSignal(
        ts_ms=ts_ms,
        streams_price=sp,
        redstone_price=rp,
        hl_mid=hp,
        div_streams_redstone_bps=div_sr,
        div_streams_hl_bps=div_sh,
        div_redstone_hl_bps=div_rh,
        max_div_bps=max_bps,
        leader=leader,
        velocity_bps_per_min=velocity,
        reason=reason,
    )


__all__ = [
    "HypeDivergenceSignal",
    "VelocityHistory",
    "compute_hype_divergence",
]
