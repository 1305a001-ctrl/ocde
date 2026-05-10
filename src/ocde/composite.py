"""Composite OCDE score — combines divergence + confidence + dispersion.

The composite is what downstream strategies consume. Each strategy
declares its own SCORE_THRESHOLD; the score lives in the Redis stream
`ocde:scores:<asset>` with the latest value at `ocde:score:<asset>:latest`.

Combiner logic (default): weighted sum, clipped to [0, 1].
  composite = 0.5 × divergence + 0.3 × confidence + 0.2 × dispersion

Rationale for weights:
  - Divergence is the most actionable (gives a direction signal).
  - Confidence widening is a leading indicator of imminent volatility
    (no direction, but tradable as an exit/scale-down trigger).
  - Dispersion is the slowest signal but the most uncrowded — durable
    edge for cross-oracle arb.

Strategies can request raw component scores instead of just composite:
the Redis publisher writes ALL components per asset, not just the
weighted sum. Downstream is free to apply different weights.
"""
from __future__ import annotations

from dataclasses import dataclass

from .confidence import WideningSignal
from .dispersion import DispersionSignal
from .divergence import DivergenceSignal


@dataclass(frozen=True)
class CompositeScore:
    asset_alias: str
    composite: float                    # 0.0 to 1.0
    divergence: float
    confidence_widening: float
    dispersion: float
    weights: tuple[float, float, float] # divergence, confidence, dispersion
    reason: str                         # joined component reasons


DEFAULT_WEIGHTS: tuple[float, float, float] = (0.5, 0.3, 0.2)


def compute_composite(
    asset_alias: str,
    *,
    divergence: DivergenceSignal | None = None,
    confidence: WideningSignal | None = None,
    dispersion: DispersionSignal | None = None,
    weights: tuple[float, float, float] = DEFAULT_WEIGHTS,
) -> CompositeScore:
    """Pure: combine the three component scores into one composite.

    Missing components contribute 0 (NOT skipped — a missing signal is
    a NEGATIVE signal of edge, since it means we're flying blind on
    one input). Weights aren't normalized to remaining components.

    weights must sum to ≤ 1.0; we don't validate but warn at startup.
    """
    div_score = divergence.score if divergence else 0.0
    conf_score = confidence.score if confidence else 0.0
    disp_score = dispersion.score if dispersion else 0.0

    w_div, w_conf, w_disp = weights
    composite = max(0.0, min(1.0, w_div * div_score + w_conf * conf_score + w_disp * disp_score))

    reasons: list[str] = []
    if divergence and divergence.score > 0:
        reasons.append(f"div:{divergence.reason}")
    if confidence and confidence.score > 0:
        reasons.append(f"conf:{confidence.reason}")
    if dispersion and dispersion.score > 0:
        reasons.append(f"disp:{dispersion.reason}")
    if not reasons:
        reasons.append("no_signal")

    return CompositeScore(
        asset_alias=asset_alias,
        composite=composite,
        divergence=div_score,
        confidence_widening=conf_score,
        dispersion=disp_score,
        weights=weights,
        reason=" | ".join(reasons),
    )


__all__ = ["CompositeScore", "DEFAULT_WEIGHTS", "compute_composite"]
