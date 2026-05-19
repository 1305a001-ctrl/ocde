# OCDE — Oracle Confidence Divergence Engine

> Multiplier signal layer for the trading stack.

OCDE consumes Pyth + Chainlink oracle data and emits a per-asset
**composite score** (0.0 to 1.0) representing how much edge is
currently available in oracle disagreement. Every other strategy
in the stack can subscribe to `ocde:scores:<asset>` Redis stream
or read `ocde:score:<asset>:latest` for the cheap latest-only view.

## Why

Three independent edges, each weak alone but multiplicative when combined:

1. **Cross-oracle divergence** (Pyth ↔ Chainlink) — when two
   independent oracles disagree on the same asset, one is
   stale or one venue is leading. The lagging oracle is about
   to move. *Most actionable; gives a direction signal.*

2. **Confidence widening** (Pyth-only) — when Pyth's
   publishers disagree more than usual, market is about to
   move (volatility comes BEFORE price moves). *Leading
   indicator; no direction.*

3. **Publisher dispersion** (Pyth-internal) — when Jane
   Street and Jump publish different prices for the same
   asset, structural disagreement signals regime change.
   *Slowest signal but the most uncrowded edge.*

Default composite weights: 0.5 / 0.3 / 0.2. Override per-strategy.

## Architecture

```
                    ┌────────────────────────────────────────┐
                    │  ocde service (ai-staging, port 8014)  │
                    └────────────────────────────────────────┘
                                    │
   ┌───────────────────┬────────────┼────────────┬──────────────────┐
   │                   │            │            │                  │
   ▼                   ▼            ▼            ▼                  ▼
 Pyth WS          chainlink-      Composite   Redis            FastAPI
 subscriber        streams       scoring      publisher         /health
 (Hermes)          (via Redis)    loop                          /metrics
                                    │
                                    ▼
                           ocde:scores:<alias>     (history stream)
                           ocde:score:<alias>:latest (cheap read)
```

### HYPE 3-source divergence

A parallel loop emits cross-oracle gap signals for HYPE specifically.
HyperLend (the largest HyperEVM lending market) prices HYPE off **RedStone
push**, not Chainlink Data Streams — so atomic-Streams liquidation isn't
available on HyperLend. Instead, OCDE captures the gap between three
independent HYPE sources every 5 seconds:

| # | Source | Where |
|---|---|---|
| 1 | Chainlink Data Streams | `chainlink:hype:latest` (Redis, from chainlink-streams) |
| 2 | RedStone on-chain | `eth_call latestAnswer()` to the HyperEVM price source (raw JSON-RPC) |
| 3 | Hyperliquid order-book mid | `POST /info {"type":"allMids"}` |

Each cycle the loop:
1. Reads all three concurrently (`asyncio.gather`, `return_exceptions=True`).
2. Computes pairwise bps gaps + the leader (source furthest from median) +
   velocity (rate-of-change of `max_div_bps` per minute over a rolling window).
3. Writes the signal to Redis as:
   - `SET ocde:hype:divergence:latest` (TTL 60s) — cheap latest read
   - `XADD ocde:hype:divergence` — capped history stream (~100k entries)

Strategy-runners can read `ocde:hype:divergence:latest` and trigger trades
when `max_div_bps > threshold` with directional bias from `leader`. The
loop degrades gracefully — any failing source just becomes `None` and the
math handles it (`reason="single_source"` when only one survives).

Tunables (all in settings.py, env-overridable):

- `hype_divergence_poll_interval_s` (default 5)
- `hype_divergence_threshold_bps` (default 30)
- `hype_divergence_velocity_window_n` (default 12 samples ≈ 1 min)

## Where

- **Process**: ai-staging (Ryzen 3600 + RTX 2060). Lives in the
  per-PC redistribution: ingestion firehose belongs off ai-primary.
- **Reads**: chainlink-streams Redis (on ai-primary) for
  `chainlink:<alias>:latest`.
- **Writes**: ai-primary Redis: `ocde:scores:*` + `ocde:score:*:latest`.

## Run

### Local dev (Mac)

```bash
pip install -e ".[dev]"
pytest -q
```

### ai-staging deployment

```bash
# 1. Copy .env.example → /srv/secrets/ocde.env (fill REDIS_URL with auth)
# 2. Run via compose (image pulled from ghcr after CI build)
ssh ai-staging 'sudo docker compose -f /srv/compose/ocde/docker-compose.yml up -d'
# 3. Verify health
ssh ai-staging 'curl -s localhost:8014/health' | python3 -m json.tool
```

## Press-button-ready integration

Downstream strategies (oms-gateway, strategy-runners, liquidation-bot)
read the composite score behind a single env flag:

```python
# Pseudocode for downstream:
if settings.ocde_enabled:
    score = redis.get(f"ocde:score:{asset}:latest")
    if score and score["composite"] >= my_threshold:
        # bias signal up or down based on divergence direction
```

The OCDE service does NOT take direction signals — that's deliberate.
The composite is a *strength* score; the consumer pairs it with their
own direction logic.

## Score components

| Component | Range | Source |
|---|---|---|
| `divergence` | 0.0 to 1.0 | scaled rel-diff in bps; capped at 100bps |
| `confidence_widening` | 0.0 to 1.0 | current conf-bps / rolling baseline |
| `dispersion` | 0.0 to 1.0 | publisher IQR in bps |

Each component returns `score=0.0` with a typed `reason` when not actionable
(stale feed, warmup, below significance, etc.). Reasons are joined into the
composite's `reason` field for downstream debugging.

## Tests

```bash
PYTHONPATH=src pytest -q
```

82 tests (purely unit tests on the math + injected-stub readers; the WS
subscriber and Redis publisher are exercised in integration only).

## Security

- Pyth Hermes is public; no secrets needed for the WS subscriber.
- Redis URL contains the password; secret material lives in
  `/srv/secrets/ocde.env` (chmod 600 root:root).
- No private keys, no on-chain submission, no order routing.

## License

MIT.
