from ocde.composite import compute_composite, DEFAULT_WEIGHTS
from ocde.confidence import WideningSignal
from ocde.dispersion import DispersionSignal
from ocde.divergence import DivergenceSignal


def _div(score: float) -> DivergenceSignal:
    return DivergenceSignal(
        asset_alias="btc",
        pyth_price_usd=50000,
        chainlink_price_usd=50500,
        abs_diff_usd=500,
        rel_diff_bps=99.0,
        confidence_overlap=False,
        age_diff_sec=0.5,
        score=score,
        reason=f"divergence_{score}",
    )


def _conf(score: float) -> WideningSignal:
    return WideningSignal(
        asset_alias="btc",
        current_conf_bps=20,
        baseline_conf_bps=10,
        widening_ratio=2.0,
        score=score,
        reason=f"conf_{score}",
    )


def _disp(score: float) -> DispersionSignal:
    return DispersionSignal(
        asset_alias="btc",
        n_publishers=5,
        median_price_usd=50000,
        spread_bps=10,
        iqr_bps=5,
        score=score,
        outlier_publishers=(),
        reason=f"disp_{score}",
    )


def test_default_weights_sum():
    assert sum(DEFAULT_WEIGHTS) == 1.0


def test_no_signal_returns_zero():
    score = compute_composite("btc")
    assert score.composite == 0.0
    assert score.reason == "no_signal"


def test_only_divergence():
    score = compute_composite("btc", divergence=_div(1.0))
    assert score.composite == 0.5  # default weight
    assert score.divergence == 1.0


def test_only_confidence():
    score = compute_composite("btc", confidence=_conf(1.0))
    assert score.composite == 0.3


def test_only_dispersion():
    score = compute_composite("btc", dispersion=_disp(1.0))
    assert score.composite == 0.2


def test_all_signals_max_returns_one():
    score = compute_composite(
        "btc",
        divergence=_div(1.0),
        confidence=_conf(1.0),
        dispersion=_disp(1.0),
    )
    assert score.composite == 1.0


def test_weighted_combination():
    score = compute_composite(
        "btc",
        divergence=_div(0.4),
        confidence=_conf(0.8),
        dispersion=_disp(0.5),
    )
    expected = 0.5 * 0.4 + 0.3 * 0.8 + 0.2 * 0.5
    assert abs(score.composite - expected) < 1e-9


def test_custom_weights():
    score = compute_composite(
        "btc",
        divergence=_div(1.0),
        weights=(0.8, 0.1, 0.1),
    )
    assert score.composite == 0.8


def test_reason_includes_active_components():
    score = compute_composite(
        "btc",
        divergence=_div(0.5),
        dispersion=_disp(0.0),
    )
    assert "div:" in score.reason
    assert "disp:" not in score.reason  # 0-score components excluded


def test_components_passed_through():
    score = compute_composite("btc", divergence=_div(0.4), confidence=_conf(0.6), dispersion=_disp(0.2))
    assert score.divergence == 0.4
    assert score.confidence_widening == 0.6
    assert score.dispersion == 0.2
