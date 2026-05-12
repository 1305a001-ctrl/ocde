"""Env-driven settings for OCDE.

Defaults are SAFE: zero side effects. The service starts up and waits
for Pyth WS data; if Redis is unreachable it logs and retries.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Connectivity ---
    redis_url: str = "redis://localhost:6379/0"

    # --- Pyth (Hermes API + WS) ---
    # Mainnet Hermes endpoint (free, public). Beta-mainnet has lower
    # latency but smaller publisher set.
    pyth_hermes_ws_url: str = "wss://hermes.pyth.network/ws"
    pyth_hermes_http_url: str = "https://hermes.pyth.network"

    # Per-asset Pyth feed IDs. Fetch from https://pyth.network/developers/price-feed-ids
    # Stored as comma-separated key:value (alias:hex_id).
    # Default: BTC, ETH, SOL, USDC mainnet feeds (May 2026 snapshot).
    # cbeth is included here even though Chainlink doesn't publish it,
    # so liquidation-bot can still cover Aave V3 Base via Pyth fallback.
    pyth_feed_ids_csv: str = (
        "btc:e62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43,"
        "eth:ff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace,"
        "sol:ef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d,"
        "usdc:eaa020c61cc479712813461ce153894a96a6c00b21ed0cfc2798d1f9a9e9c94a"
    )

    # --- Chainlink Data Streams (consumes from chainlink-streams Redis) ---
    # Default reflects what our Chainlink account is currently entitled to
    # (verified 2026-05-12 via /api/v1/feeds): 7 majors covering the largest
    # crypto markets + Hyperliquid (HYPE).
    #
    # Missing from entitlements (request from Chainlink contact when ready):
    #   - USDC, USDT (stable pricing for liquidation-bot debt valuation)
    #   - WSTETH (Aave V3 ETH collateral)
    #   - WBTC if separate from BTC/USD spot
    #
    # OCDE composite gracefully degrades for non-entitled aliases — divergence
    # contributes 0 if no chainlink data, but Pyth-only confidence + dispersion
    # signals still score. So expand this list AFTER getting more Chainlink
    # entitlements, not before.
    chainlink_feed_aliases_csv: str = "btc,eth,sol,bnb,xrp,doge,hype"
    chainlink_redis_key_pattern: str = "chainlink:{alias}:latest"

    # --- Composite scoring ---
    # Weights must sum to ≤ 1.0
    composite_weight_divergence: float = 0.5
    composite_weight_confidence: float = 0.3
    composite_weight_dispersion: float = 0.2

    # Minimum composite score to publish (filters noise from Redis stream)
    composite_publish_threshold: float = 0.05

    # --- Cycle cadence ---
    cycle_interval_sec: float = 1.0   # how often to recompute composites

    # --- Confidence tracker ---
    confidence_window_n: int = 60   # last N samples (10min at 10s sampling)

    # --- Output streams ---
    score_stream_template: str = "ocde:scores:{alias}"
    score_stream_maxlen: int = 1_000_000

    # Latest score key pattern (overwritten each cycle for cheap reads)
    score_latest_template: str = "ocde:score:{alias}:latest"

    # --- HTTP ---
    http_host: str = "0.0.0.0"  # noqa: S104  — bound to 127.0.0.1 in compose
    http_port: int = 8014
    log_level: str = "INFO"


settings = Settings()
