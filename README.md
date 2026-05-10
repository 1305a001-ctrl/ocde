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

36+ tests (purely unit tests on the math; the WS subscriber and Redis
publisher are exercised in integration only).

## Security

- Pyth Hermes is public; no secrets needed for the WS subscriber.
- Redis URL contains the password; secret material lives in
  `/srv/secrets/ocde.env` (chmod 600 root:root).
- No private keys, no on-chain submission, no order routing.

## License

MIT.
