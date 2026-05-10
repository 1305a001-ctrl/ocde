from ocde.confidence import ConfidenceSnapshot, ConfidenceTracker


def _snap(price: float, conf: float, ts_ms: int = 0) -> ConfidenceSnapshot:
    return ConfidenceSnapshot("btc", price, conf, ts_ms)


def test_warmup_returns_zero_score():
    tracker = ConfidenceTracker()
    sig = tracker.observe(_snap(50000, 50.0))
    assert sig.score == 0.0
    assert sig.reason == "warmup_insufficient_samples"


def test_within_normal_band_returns_zero_score():
    tracker = ConfidenceTracker()
    # Establish baseline: 5 samples at confidence 100bps each
    for _ in range(5):
        tracker.observe(_snap(50000, 50.0))     # 50/50000 = 10bps
    # New sample at 1.5x baseline (15bps) — still within normal band
    sig = tracker.observe(_snap(50000, 75.0))
    assert sig.score == 0.0
    assert sig.reason == "within_normal_band"


def test_widening_2x_returns_low_score():
    tracker = ConfidenceTracker()
    for _ in range(5):
        tracker.observe(_snap(50000, 50.0))     # 10bps baseline
    # Exactly 2x widening (20bps) — should be at the lower edge of the
    # widening band; score = 0.7 * (2 - 2) / 3 = 0.0
    sig = tracker.observe(_snap(50000, 100.0))
    # >=2x triggers the widening branch but at exactly 2x the score is 0
    assert sig.widening_ratio >= 2.0
    assert 0 <= sig.score < 0.1


def test_widening_3x_mid_score():
    tracker = ConfidenceTracker()
    for _ in range(5):
        tracker.observe(_snap(50000, 50.0))
    # 3x widening — score = 0.7 * (3-2)/3 ≈ 0.233
    sig = tracker.observe(_snap(50000, 150.0))
    assert sig.widening_ratio >= 3.0 - 0.5
    assert 0.15 < sig.score < 0.35


def test_widening_5x_high_score():
    tracker = ConfidenceTracker()
    for _ in range(5):
        tracker.observe(_snap(50000, 50.0))
    sig = tracker.observe(_snap(50000, 250.0))
    assert sig.widening_ratio >= 5.0 - 0.5
    assert 0.5 < sig.score <= 0.8


def test_widening_10x_max_score():
    tracker = ConfidenceTracker()
    for _ in range(5):
        tracker.observe(_snap(50000, 50.0))
    sig = tracker.observe(_snap(50000, 500.0))
    assert sig.score == 1.0


def test_separate_assets_track_separately():
    tracker = ConfidenceTracker()
    for _ in range(5):
        tracker.observe(ConfidenceSnapshot("btc", 50000, 50.0, 0))
    # ETH starts fresh
    sig_eth = tracker.observe(ConfidenceSnapshot("eth", 3000, 3.0, 0))
    assert sig_eth.reason == "warmup_insufficient_samples"


def test_baseline_returns_none_during_warmup():
    tracker = ConfidenceTracker()
    tracker.observe(_snap(50000, 50.0))
    assert tracker.baseline("btc") is None


def test_baseline_returns_median_after_warmup():
    tracker = ConfidenceTracker()
    for _ in range(5):
        tracker.observe(_snap(50000, 50.0))
    baseline = tracker.baseline("btc")
    assert baseline is not None
    # 50/50000 × 10000 = 10 bps
    assert abs(baseline - 10.0) < 0.01
