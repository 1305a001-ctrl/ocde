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

    # Per-asset Pyth feed IDs. Fetched 2026-05-12 from
    # https://hermes.pyth.network/v2/price_feeds?asset_type=crypto (584 feeds).
    # Stored as comma-separated key:value (alias:hex_id).
    #
    # Default = 31 majors covering everything our build needs PLUS the gaps
    # in Chainlink's catalog (USDC/USDT/WSTETH/CBETH not entitled at our
    # current Chainlink tier). Pyth has no entity gating — all feeds free.
    #
    # Pyth IS the primary oracle for all assets except the 7 Chainlink
    # entitled (BTC/ETH/SOL/BNB/XRP/DOGE/HYPE) where we use Chainlink
    # primary + Pyth secondary for divergence signal.
    pyth_feed_ids_csv: str = (
        "btc:e62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43,"
        "eth:ff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace,"
        "sol:ef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d,"
        "usdc:eaa020c61cc479712813461ce153894a96a6c00b21ed0cfc2798d1f9a9e9c94a,"
        "usdt:2b89b9dc8fdf9f34709a5b106b472f0f39bb6ca9ce04b0fd7f2e971688e2e53b,"
        "wsteth:6df640f3b8963d8f8358f791f352b8364513f6ab1cca5ed3f1f7b5448980e784,"
        "cbeth:15ecddd26d49e1a8f1de9376ebebc03916ede873447c1255d2d5891b92ce5717,"
        "aave:2b9ab1e972a281585084148ba1389800799bd4be63b957507db1349314e47445,"
        "link:8ac0c70fff57e9aefdf5edf44b51d62c2d433653cbb2cf5cc06bb115af04d221,"
        "avax:93da3352f9f1d105fdfe4971cfa80e9dd777bfc5d0f683ebb6e1294b92137bb7,"
        "arb:3fa4252848f9f0a1480be62745a4629d9eb1322aebab8a791e344b3b9c1adcf5,"
        "op:385f64d993f7b77d8182ed5003d97c60aa3361f3cecfe711544d2d59165e9bdf,"
        "near:c415de8d2eba7db216527dff4b60e8f3a5311c740dadb233e13e12547e226750,"
        "atom:b00b60f88b03a6a625a8d1c048c3f66653edf217439983d037e7222c4e612819,"
        "doge:dcef50dd0a4cd2dcc17e45df1676dcb336a11a61c69df7a0299b0150c672d25c,"
        "bnb:2f95862b045670cd22bee3114c39763a4a08beeb663b145d283c31d7d1101c4f,"
        "xrp:ec5d399846a9209f3fe5881d70aae9268c94339ff9817e8d18ff19fa05eea1c8,"
        "pol:ffd11c5a1cfd42f80afb2df4d9f264c15f956d68153335374ec10722edd70472,"
        "ada:2a01deaec9e51a579277b34b122399984d0bbf57e2458a7e42fecd2829867a0d,"
        "dot:ca3eed9b267293f6595901c734c7525ce8ef49adafe8284606ceb307afa2ca5b,"
        "uni:78d185a741d07edb3412b09008b7c5cfb9bbbd7d568bf00ba737b456ba171501,"
        "ltc:6e3f3fa8253588df9326580180233eb791e03b443a3ba7a1d892e73874e19a54,"
        "shib:f0d57deca57b3da2fe63a493f4c25925fdfd8edf834b20f93e1f84dbd1504d4a,"
        "pepe:d69731a2e74ac1ce884fc3890f7ee324b6deb66147055249568869ed700882e4,"
        "sui:23d7315113f5b1d3ba7a83604c44b94d79f4fd69af77f804fc7f920a6dc65744,"
        "apt:03ae4db29ed4ae33d323568895aa00337e658e348b37509f5372ae51f0af00d5,"
        "inj:7a5bc1d2b56ad029048cd63964b3ad2776eadf812edc1a43a31406cb54bff592,"
        "tia:09f7c1d7dfbb7df2b8fe3d3d87ee94a2259d212da4f30c1f0540d066dfa44723,"
        "hype:4279e31cc369bbcc2faf022b382b080e32a8e689ff20fbc530d2a603eb6cd98b,"
        "trx:67aed5a24fdad045475e7195c98a98aea119c763f272d4523f5bac93a4f33c2b,"
        "ton:8963217838ab4cf5cadc172203c1f0b763fbaa45f346d8ee50ba994bbcac3026"
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

    # --- Pyth raw-price publishing ---
    # OCDE re-publishes Pyth raw prices to Redis as `pyth:<alias>:latest`,
    # mirroring the chainlink-streams pattern. Downstream consumers
    # (liquidation-bot, strategy-runners) can fall back to Pyth when
    # Chainlink doesn't entitle the asset they need.
    pyth_publish_to_redis: bool = True
    pyth_latest_template: str = "pyth:{alias}:latest"
    pyth_publish_min_interval_ms: int = 100   # don't write more than 10/sec/asset

    # --- HYPE 3-source divergence (Streams vs RedStone vs HL order book) ---
    # HyperEVM lending audit ruled out atomic-Streams liquidation on HyperLend —
    # production HYPE markets price off RedStone push, not Streams. Instead of
    # liquidating, we emit a cross-oracle gap signal that captures the spread
    # between:
    #   (1) Chainlink Data Streams HYPE — our sub-second edge feed
    #   (2) RedStone on-chain HYPE     — what HyperLend actually reads
    #   (3) Hyperliquid order-book mid  — likely the upstream venue
    # Strategy-runners + Liquidity Pulse can consume this downstream.
    hyperevm_rpc_url: str = "https://rpc.hyperliquid.xyz/evm"
    # RedStone HYPE/USD price source on HyperEVM (verified 2026-05-20)
    redstone_hype_source: str = "0x40EA33eA76Fbe35e9FB422eDd175b8c8D84A63Cc"
    redstone_hype_decimals: int = 8
    hyperliquid_api_url: str = "https://api.hyperliquid.xyz/info"
    hype_divergence_poll_interval_s: int = 5
    hype_divergence_threshold_bps: float = 30.0
    hype_divergence_velocity_window_n: int = 12
    hype_divergence_stream_maxlen: int = 100_000
    hype_divergence_latest_key: str = "ocde:hype:divergence:latest"
    hype_divergence_stream_key: str = "ocde:hype:divergence"

    # --- HTTP ---
    http_host: str = "0.0.0.0"  # noqa: S104  — bound to 127.0.0.1 in compose
    http_port: int = 8014
    log_level: str = "INFO"


settings = Settings()
