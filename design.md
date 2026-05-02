# Polymarket Anomaly Detector — Design

## Architecture Overview

A single-process Python application that runs on a configurable poll interval. Each poll cycle fetches markets and trades, computes anomaly signals, classifies flagged events via Ollama, and persists results to SQLite.

```
┌─────────────────────────────────────────────────────┐
│                    Poll Loop (hourly)                │
│                                                     │
│  Fetcher ──► Signal Engine ──► Classifier ──► DB    │
│    │               │               │           │    │
│  Gamma API      Trade-level     Ollama        SQLite│
│  Data API       Market-level   qwen2.5:7b           │
└─────────────────────────────────────────────────────┘
```

## Module Structure

```
polymarket_anomaly/
├── main.py              # Entry point, poll loop
├── config.py            # Config from env vars / defaults
├── fetcher.py           # API clients (Gamma, Data)
├── signals.py           # Anomaly signal computation
├── classifier.py        # Ollama LLM classification
├── db.py                # SQLite schema + upsert helpers
└── models.py            # Dataclasses: Market, Trade, Flag
```

## Data Flow

### Per Poll Cycle

1. **Fetch markets** — GET Gamma API, top `MARKET_LIMIT` markets sorted by `volume24hr` (`order=volume24hr&ascending=false`).
2. **Filter relevant markets** — Batch LLM call (`filter_relevant_markets`): markets are split into batches of 25 and sent to Ollama with `condition_id` + title. Returns `[{condition_id, relevant: true/false}]`. Validates no silent drops (retries once for dropped markets, fails open for persistent drops). Only relevant (politics/economics) markets proceed.
3. **Filter active** — From relevant markets, drop any whose `end_date` has passed. Upsert remaining into `markets` table (with `active` flag). These are the poll targets for this cycle.
4. ~~**Fetch current prices**~~ — CLOB API requires an API key; skipped for MVP. `current_price` is derived from `outcomePrices` returned by the Gamma API instead.
5. **Fetch trades** — For each active+relevant market, GET Data API with `conditionId`, filtered to `TRADE_WINDOW_HOURS`. Trades are accumulated into `trades_by_market` across **all** markets before signal computation. Upsert to `trades` table.
6. **Compute signals** — Run signal engine over the full `trades_by_market` dict (required for `cross_market_wallet` signal).
7. **Classify flags** — For each trade with ≥2 signals, call Ollama. Write result to `flags` table.

## Module Design

### `config.py`

Reads from environment variables with defaults:

```python
POLL_INTERVAL       = int(os.getenv("POLL_INTERVAL", 3600))
MARKET_LIMIT        = int(os.getenv("MARKET_LIMIT", 100))
TRADE_WINDOW_HOURS  = int(os.getenv("TRADE_WINDOW_HOURS", 1))
MIN_TRADE_SIZE     = float(os.getenv("MIN_TRADE_SIZE", 5000))
OLLAMA_ENDPOINT     = os.getenv("OLLAMA_ENDPOINT", "http://sunils-mac-studio:11434")
OLLAMA_MODEL        = os.getenv("OLLAMA_MODEL", "qwen2.5:32b")
DB_PATH             = os.getenv("DB_PATH", "./polymarket.db")
```

### `models.py`

```python
@dataclass
class Market:
    condition_id: str
    title: str
    volume: float
    liquidity: float
    end_date: str
    outcome_prices: list[float]
    fetched_at: str

@dataclass
class Trade:
    transaction_hash: str
    condition_id: str
    proxy_wallet: str
    side: str
    size: float
    price: float
    outcome: str
    timestamp: str
    fetched_at: str

@dataclass
class Flag:
    transaction_hash: str
    condition_id: str
    signals_triggered: list[str]   # serialized as JSON string in DB
    anomaly_type: str
    confidence: str
    reasoning: str
    flagged_at: str
```

### `fetcher.py`

Two thin API clients using `httpx` (async):

- `fetch_markets(limit) -> list[Market]`
- `fetch_trades(condition_id, since_hours) -> list[Trade]`

`fetch_price` via CLOB API is omitted for MVP (requires API key). Current price is read from `outcomePrices` in the Gamma market payload.

All requests use a shared `httpx.AsyncClient` with a 10s timeout. HTTP errors are logged and return empty results (non-fatal).

### `signals.py`

Returns a `dict[str, list[str]]` mapping `transaction_hash → [signal_names]`.

**Trade-level signals** (evaluated per trade):
- `large_position`: `trade.size >= MIN_TRADE_SIZE` (default $5000)
- `contrarian_trade`: `abs(trade.price - current_price) > 0.15` (15pp deviation; `current_price` sourced from `outcomePrices` in the Gamma market payload)
- `rapid_repeat_trades`: same `proxy_wallet` makes ≥3 trades in the same market within the trade window
- `size_outlier`: `trade.size > mean + 2σ` of all trade sizes in that market this cycle (requires ≥3 trades to compute)

**Market-level signal** (evaluated per market, applied to all trades in that market):
- `volume_price_shift`: price moved >10pp while volume in window is below 10% of market liquidity

**Flag threshold**: a trade is flagged only if it has ≥2 signals.

Thresholds are constants in `signals.py`, easy to tune after initial data collection.

### `classifier.py`

Two functions, both calling Ollama's `/api/chat` with `stream: false` and `temperature: 0`.

**`filter_relevant_markets(markets) -> list[Market]`** — Batched call (25 markets per batch). Sends market titles + condition_ids, gets back a JSON array of `{condition_id, relevant}`. Validates every condition_id against the input set, retries once for dropped markets, and fails open for persistent drops or Ollama failure.

**`classify(trade, market, signals) -> Flag`** — Single-trade call. Prompt:
```
Market: {title}
Trade: {side} {size} shares of "{outcome}" at price {price}
Current market price: {current_price}  (from Gamma outcomePrices)
Signals triggered: {signals}

Classify this trade. Respond with JSON only:
{
  "anomaly_type": "informed_trading|wash_trading|liquidity_shock|normal_large_trade",
  "confidence": "low|medium|high",
  "reasoning": "<one sentence>"
}
```

If Ollama is unreachable or returns malformed JSON, the flag is written with `anomaly_type="unclassified"`, `confidence="low"`, and the raw error as `reasoning`.

### `db.py`

Uses Python's built-in `sqlite3`. Schema created on startup with `CREATE TABLE IF NOT EXISTS`.

**Upsert pattern** (all three tables):
```sql
INSERT INTO trades (...) VALUES (...)
ON CONFLICT(transaction_hash) DO UPDATE SET fetched_at=excluded.fetched_at
```

`signals_triggered` in `flags` is stored as a JSON string.

### `main.py`

```python
async def poll_cycle():
    markets = await fetch_markets(config.MARKET_LIMIT)
    upsert_markets(markets)                          # all 100, with active flag

    relevant = await filter_relevant_markets(markets)
    upsert_relevant_markets(relevant)

    active_relevant = [m for m in relevant if not expired(m.end_date)]

    trades_by_market = {}
    for market in active_relevant:                   # accumulate ALL before signals
        trades = await fetch_trades(market.condition_id)
        trades_by_market[market.condition_id] = trades
        upsert_trades(trades)

    signal_map = compute_signals(trades_by_market, markets_by_id)
    for tx_hash, signals in signal_map.items():
        if len(signals) >= 2:
            flag = await classify(trade, market, signals)
            upsert_flag(flag)

async def main():
    init_db()
    while True:
        await poll_cycle()
        await asyncio.sleep(config.POLL_INTERVAL)
```

## Database Schema

```sql
CREATE TABLE IF NOT EXISTS markets (
    condition_id TEXT PRIMARY KEY,
    title        TEXT,
    volume       REAL,
    liquidity    REAL,
    end_date     TEXT,
    active       INTEGER DEFAULT 1,  -- 0 if end_date has passed
    fetched_at   TEXT
    -- only topically relevant (politics/economics) markets are stored here
);
CREATE INDEX IF NOT EXISTS idx_markets_active ON markets(active);

CREATE TABLE IF NOT EXISTS trades (
    transaction_hash TEXT PRIMARY KEY,
    condition_id     TEXT REFERENCES markets(condition_id),
    proxy_wallet     TEXT,
    side             TEXT,
    size             REAL,
    price            REAL,
    outcome          TEXT,
    timestamp        TEXT,
    fetched_at       TEXT
);

CREATE TABLE IF NOT EXISTS flags (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_hash  TEXT REFERENCES trades(transaction_hash),
    condition_id      TEXT REFERENCES markets(condition_id),
    signals_triggered TEXT,   -- JSON array
    anomaly_type      TEXT,
    confidence        TEXT,
    reasoning         TEXT,
    flagged_at        TEXT
);
```

## Dependencies

| Package   | Purpose                        |
|-----------|--------------------------------|
| `httpx`   | Async HTTP client for all APIs |
| `sqlite3` | Built-in, no extra dep needed  |

No ORM. No framework. Standard library + `httpx` only.

## Error Handling

- API failures: log warning, skip that market/trade for the cycle. Do not crash the poll loop.
- Ollama failure: write flag with `anomaly_type="unclassified"`. Log the error.
- DB errors: log and re-raise (fatal — the DB is the source of truth).

## Logging

Use Python's `logging` module at `INFO` level by default. Each poll cycle logs:
- Number of markets fetched
- Number of trades fetched
- Number of flags raised
- Number of classifications completed

## Open Questions / Tuning After First Run

- `contrarian_trade` threshold (currently 15pp) — may need adjustment based on observed price distributions
- `volume_price_shift` threshold — needs baseline data to calibrate
- Whether to deduplicate flags across poll cycles (currently a new flag row is written each cycle for the same trade if it still meets the threshold)
