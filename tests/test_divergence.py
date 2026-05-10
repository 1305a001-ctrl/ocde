from ocde.divergence import OraclePrice, compute_divergence


def _pyth(price: float, conf: float = 0.0, ts_ms: int = 1_700_000_000_000) -> OraclePrice:
    return OraclePrice("pyth", "btc", price, conf, ts_ms)


def _chainlink(price: float, ts_ms: int = 1_700_000_000_000) -> OraclePrice:
    return OraclePrice("chainlink", "btc", price, 0.0, ts_ms)


def test_no_divergence_when_prices_equal():
    sig = compute_divergence(_pyth(50000), _chainlink(50000))
    assert sig.score == 0.0
    assert sig.rel_diff_bps == 0.0
    assert sig.reason == "below_significance_threshold"


def test_score_scales_with_relative_diff():
    # 1% diff = ~100bps → score ~ 1.0
    sig = compute_divergence(_pyth(50000), _chainlink(50500))
    assert abs(sig.rel_diff_bps - 99.5) < 0.1
    assert 0.99 < sig.score < 1.0


def test_score_capped_at_1():
    # 5% diff → score still 1.0
    sig = compute_divergence(_pyth(50000), _chainlink(52500))
    assert sig.score == 1.0


def test_below_significance_returns_zero_score():
    # 4bps diff (under default 5bps threshold) → no signal
    sig = compute_divergence(_pyth(50000), _chainlink(50020))
    assert sig.score == 0.0
    assert sig.reason == "below_significance_threshold"


def test_confidence_overlap_returns_zero():
    # Pyth says $50000 ± $200; Chainlink reads $50100. Within 1σ → no signal.
    sig = compute_divergence(_pyth(50000, conf=200.0), _chainlink(50100))
    assert sig.score == 0.0
    assert sig.confidence_overlap is True
    assert sig.reason == "within_pyth_confidence"


def test_stale_feed_returns_zero():
    # Pyth at t=0, chainlink at t=60s — exceeds 30s default window
    pyth = _pyth(50000, ts_ms=0)
    cl = _chainlink(50500, ts_ms=60_000)
    sig = compute_divergence(pyth, cl)
    assert sig.score == 0.0
    assert "feed_stale" in sig.reason
    assert sig.age_diff_sec == 60.0


def test_asset_mismatch_returns_zero():
    pyth = OraclePrice("pyth", "btc", 50000, 0, 0)
    cl = OraclePrice("chainlink", "eth", 3000, 0, 0)
    sig = compute_divergence(pyth, cl)
    assert sig.score == 0.0
    assert sig.reason == "asset_mismatch"


def test_non_positive_price_returns_zero():
    sig = compute_divergence(_pyth(0), _chainlink(50000))
    assert sig.score == 0.0
    assert sig.reason == "non_positive_price"


def test_custom_significance_threshold():
    # Tighter threshold catches smaller divergences
    sig = compute_divergence(_pyth(50000), _chainlink(50020), significance_threshold_bps=2.0)
    assert sig.score > 0
