"""Oracle Confidence Divergence Engine.

Multiplier signal layer: combines Pyth (price + confidence + publishers)
with Chainlink Data Streams to produce a per-asset composite score that
every other strategy can consume.

See README.md for the architectural overview.
"""

__version__ = "0.1.0"
