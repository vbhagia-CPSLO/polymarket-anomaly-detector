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

1. **Fetch markets** — GET Gamma API, top `MARKET_LIMIT` markets sorted by `valuation24hr`. Store/upsert to `markets` table.
2. **Fetch trades** — For each market, GET Data API with `conditionId`, filtered to `TRADE_WINDOW_HOURS`. Upsert to `trades` table.
3. ~~**Fetch current prices**~~ — CLOB API requires an API key; skipped for MVP. `current_price` is derived from `outcomePrices` returned by the Gamma API instead.
4. **Compute signals** — Run signal engine over fetched trades + market data.
5. **Classify flags** — For each trade with ≥2 signals, call Ollama. Write result to `flags` table.

## Module Design

### `config.py`

Reads from environment variables with defaults:

```python
POLL_INTERVAL       = int(os.getenv("POLL_INTERVAL", 3600))
MARKET_LIMIT        = int(os.getenv("MARKET_LIMIT", 100))
TRADE_WINDOW_HOURS  = int(os.getenv("TRADE_WINDOW_HOURS", 1))
MIN_TRADE_SIZE      = float(os.getenv("MIN_TRADE_SIZE", 500))
OLLAMA_ENDPOINT     = os.getenv("OLLAMA_ENDPOINT", "http://localhost:11434")
OLLAMA_MODEL        = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
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
- `large_position`: `trade.size >= MIN_TRADE_SIZE`
- `contrarian_trade`: `abs(trade.price - current_price) > 0.15` (15pp deviation; `current_price` sourced from `outcomePrices` in the Gamma market payload)
- `cross_market_wallet`: same `proxy_wallet` seen in ≥2 markets with the same `eventSlug` within the trade window

**Market-level signal** (evaluated per market, applied to all trades in that market):
- `volume_price_shift`: price moved >10pp while volume in window is below the market's median hourly volume

**Flag threshold**: a trade is flagged only if it has ≥2 signals.

Thresholds for `contrarian_trade` and `volume_price_shift` are constants in `signals.py`, easy to tune after initial data collection.

### `classifier.py`

Calls Ollama's `/api/generate` endpoint with a structured prompt. Parses JSON from the response.

**Prompt template:**
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
    upsert_markets(markets)
    for market in markets:
        trades = await fetch_trades(market.condition_id, config.TRADE_WINDOW_HOURS)
        upsert_trades(trades)
    signal_map = compute_signals(trades_by_market, current_prices)
    for tx_hash, signals in signal_map.items():
        if len(signals) >= 2:
            result = await classify(trade, market, signals, current_prices)
            upsert_flag(result)

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
    fetched_at   TEXT
);

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
