"""Tests for the pure-math HYPE divergence module.

NO I/O, NO live network. Just frozen-dataclass primitives + arithmetic.
"""
from __future__ import annotations

from ocde.divergence import OraclePrice
from ocde.hype_divergence import (
    HypeDivergenceSignal,
    VelocityHistory,
    compute_hype_divergence,
)


def _streams(price: float, ts_ms: int = 1_700_000_000_000) -> OraclePrice:
    return OraclePrice("chainlink", "hype", price, 0.0, ts_ms)


def _redstone(price: float, ts_ms: int = 1_700_000_000_000) -> OraclePrice:
    return OraclePrice("redstone", "hype", price, 0.0, ts_ms)


def _hl(price: float, ts_ms: int = 1_700_000_000_000) -> OraclePrice:
    return OraclePrice("hyperliquid", "hype", price, 0.0, ts_ms)


# --- 3-source happy path ---------------------------------------------------

def test_three_sources_compute_all_pairs_and_leader():
    # streams=$48.00, redstone=$47.80, hl=$47.95 — redstone is furthest from median
    sig = compute_hype_divergence(
        _streams(48.00),
        _redstone(47.80),
        _hl(47.95),
        history=[],
        threshold_bps=10.0,
    )
    assert sig.streams_price == 48.00
    assert sig.redstone_price == 47.80
    assert sig.hl_mid == 47.95
    # All three pairwise diffs computed
    assert sig.div_streams_redstone_bps is not None and sig.div_streams_redstone_bps > 0
    assert sig.div_streams_hl_bps is not None and sig.div_streams_hl_bps > 0
    assert sig.div_redstone_hl_bps is not None and sig.div_redstone_hl_bps > 0
    # max_div is streams-vs-redstone (48 vs 47.80 = ~41.8 bps); above threshold
    assert sig.max_div_bps > 30
    assert sig.reason is None  # above threshold
    # Median of (48.00, 47.80, 47.95) = 47.95 (hl) — leader is redstone (furthest)
    assert sig.leader == "redstone"


def test_three_sources_below_threshold_emits_reason():
    # All three within 5bps — below 30 threshold
    sig = compute_hype_divergence(
        _streams(48.00),
        _redstone(48.01),
        _hl(48.005),
        history=[],
        threshold_bps=30.0,
    )
    assert sig.max_div_bps < 30
    assert sig.reason == "below_threshold"
    # Leader still computed (not None at 3-source)
    assert sig.leader in ("streams", "redstone", "hl")


def test_three_sources_leader_is_streams_when_streams_furthest():
    # streams way off, redstone+hl agree
    sig = compute_hype_divergence(
        _streams(50.00),
        _redstone(48.00),
        _hl(48.10),
        history=[],
        threshold_bps=10.0,
    )
    assert sig.leader == "streams"


# --- 2-source cases --------------------------------------------------------

def test_two_sources_streams_and_redstone_only():
    sig = compute_hype_divergence(
        _streams(48.00),
        _redstone(47.50),
        None,
        history=[],
        threshold_bps=10.0,
    )
    assert sig.div_streams_redstone_bps is not None
    assert sig.div_streams_hl_bps is None
    assert sig.div_redstone_hl_bps is None
    # max_div = streams-vs-redstone gap
    assert sig.max_div_bps == sig.div_streams_redstone_bps
    # Leader (synthetic): higher-priced of the two = streams
    assert sig.leader == "streams"


def test_two_sources_redstone_and_hl_only():
    sig = compute_hype_divergence(
        None,
        _redstone(47.50),
        _hl(48.00),
        history=[],
        threshold_bps=10.0,
    )
    assert sig.div_streams_redstone_bps is None
    assert sig.div_streams_hl_bps is None
    assert sig.div_redstone_hl_bps is not None
    # Leader: higher-priced = hl
    assert sig.leader == "hl"


# --- 1-source / 0-source --------------------------------------------------

def test_single_source_returns_single_source_reason():
    sig = compute_hype_divergence(
        _streams(48.00),
        None,
        None,
        history=[],
        threshold_bps=10.0,
    )
    assert sig.max_div_bps == 0.0
    assert sig.reason == "single_source"
    assert sig.leader is None
    assert sig.div_streams_redstone_bps is None


def test_zero_sources_returns_no_sources_reason():
    sig = compute_hype_divergence(
        None, None, None, history=[], threshold_bps=10.0,
    )
    assert sig.max_div_bps == 0.0
    assert sig.reason == "no_sources"
    assert sig.leader is None
    assert sig.ts_ms == 0


# --- bps math sanity ------------------------------------------------------

def test_bps_math_uses_midpoint_denominator():
    # 48.00 vs 48.48 — gap = 0.48, mid = 48.24 — bps = 0.48/48.24 * 10000 ≈ 99.5
    sig = compute_hype_divergence(
        _streams(48.00),
        _redstone(48.48),
        None,
        history=[],
        threshold_bps=10.0,
    )
    assert sig.div_streams_redstone_bps is not None
    assert abs(sig.div_streams_redstone_bps - 99.5) < 0.2


def test_equal_prices_yield_zero_bps():
    sig = compute_hype_divergence(
        _streams(48.00),
        _redstone(48.00),
        _hl(48.00),
        history=[],
        threshold_bps=10.0,
    )
    assert sig.max_div_bps == 0.0
    assert sig.reason == "below_threshold"


# --- Velocity calc --------------------------------------------------------

def test_velocity_with_sufficient_history():
    # 6 entries spanning 60s; max_div climbs 0 → 50 bps linearly.
    # Expected velocity: (50 - 0) / 1min = 50 bps/min
    history = [
        (1_700_000_000_000, 0.0),
        (1_700_000_012_000, 10.0),
        (1_700_000_024_000, 20.0),
        (1_700_000_036_000, 30.0),
        (1_700_000_048_000, 40.0),
        (1_700_000_060_000, 50.0),
    ]
    sig = compute_hype_divergence(
        _streams(48.00),
        _redstone(47.80),
        _hl(48.10),
        history=history,
        threshold_bps=10.0,
    )
    assert sig.velocity_bps_per_min is not None
    assert abs(sig.velocity_bps_per_min - 50.0) < 0.001


def test_velocity_with_insufficient_history():
    # Only 1 entry → can't compute velocity
    sig = compute_hype_divergence(
        _streams(48.00),
        _redstone(47.80),
        _hl(48.10),
        history=[(1_700_000_000_000, 5.0)],
        threshold_bps=10.0,
    )
    assert sig.velocity_bps_per_min is None


def test_velocity_with_empty_history():
    sig = compute_hype_divergence(
        _streams(48.00),
        _redstone(47.80),
        None,
        history=[],
        threshold_bps=10.0,
    )
    assert sig.velocity_bps_per_min is None


def test_velocity_with_zero_time_span_returns_none():
    # Two samples with identical ts → div-by-zero guard
    history = [(1_700_000_000_000, 5.0), (1_700_000_000_000, 20.0)]
    sig = compute_hype_divergence(
        _streams(48.00),
        _redstone(47.80),
        None,
        history=history,
        threshold_bps=10.0,
    )
    assert sig.velocity_bps_per_min is None


# --- VelocityHistory helper ------------------------------------------------

def test_velocity_history_caps_at_window_n():
    h = VelocityHistory(window_n=3)
    for i in range(10):
        h.append(1_700_000_000_000 + i * 1000, float(i))
    assert len(h) == 3
    # Oldest trimmed — should keep last 3
    entries = h.entries
    assert entries[0][1] == 7.0
    assert entries[1][1] == 8.0
    assert entries[2][1] == 9.0


def test_velocity_history_under_cap_keeps_all():
    h = VelocityHistory(window_n=10)
    h.append(1, 1.0)
    h.append(2, 2.0)
    assert len(h) == 2
    assert h.entries == [(1, 1.0), (2, 2.0)]


def test_velocity_history_returns_copy_not_internal_ref():
    h = VelocityHistory(window_n=10)
    h.append(1, 1.0)
    view = h.entries
    view.append((99, 99.0))  # mutate the copy
    # Internal state unaffected
    assert len(h) == 1
    assert h.entries == [(1, 1.0)]


# --- Frozen dataclass sanity ----------------------------------------------

def test_signal_dataclass_is_frozen():
    import dataclasses

    sig = compute_hype_divergence(
        _streams(48.00), None, None, history=[], threshold_bps=10.0,
    )
    assert dataclasses.is_dataclass(sig)
    # Frozen — assignment must raise
    try:
        sig.reason = "mutated"  # type: ignore[misc]
    except (dataclasses.FrozenInstanceError, AttributeError):
        pass
    else:
        raise AssertionError("HypeDivergenceSignal should be frozen")


# --- Timestamp propagation -------------------------------------------------

def test_ts_ms_uses_streams_when_available():
    sig = compute_hype_divergence(
        _streams(48.00, ts_ms=1_234_567_890_000),
        _redstone(47.80, ts_ms=9_999_999_999_999),
        None,
        history=[],
        threshold_bps=10.0,
    )
    assert sig.ts_ms == 1_234_567_890_000


def test_ts_ms_falls_back_to_redstone_then_hl():
    sig = compute_hype_divergence(
        None,
        _redstone(47.80, ts_ms=5_555_555_555_000),
        _hl(48.00, ts_ms=9_999_999_999_999),
        history=[],
        threshold_bps=10.0,
    )
    assert sig.ts_ms == 5_555_555_555_000

    sig2 = compute_hype_divergence(
        None,
        None,
        _hl(48.00, ts_ms=7_777_777_777_000),
        history=[],
        threshold_bps=10.0,
    )
    assert sig2.ts_ms == 7_777_777_777_000


# --- Return-type sanity ----------------------------------------------------

def test_returns_signal_type():
    sig = compute_hype_divergence(
        _streams(48.00), None, None, history=[], threshold_bps=10.0,
    )
    assert isinstance(sig, HypeDivergenceSignal)
