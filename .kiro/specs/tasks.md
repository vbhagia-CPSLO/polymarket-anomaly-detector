# Tasks

## Implementation Note — Cross-Market Signal Requires Global Trade Accumulation

> **Important for `main.py` and `signals.py`:**
> Trades must be accumulated across **all** markets into a `trades_by_market: dict[str, list[Trade]]` before `compute_signals` is called. The `cross_market_wallet` signal requires a global view of all trades in the cycle — it cannot be computed per-market in isolation. Do **not** call `compute_signals` inside the per-market fetch loop.

---

## Tasks

- [x] **1. `models.py`** — Define `Market`, `Trade`, and `Flag` dataclasses as specified in the design.

- [x] **2. `config.py`** — Read all config vars from environment with defaults: `POLL_INTERVAL`, `MARKET_LIMIT`, `TRADE_WINDOW_HOURS`, `MIN_TRADE_SIZE`, `OLLAMA_ENDPOINT`, `OLLAMA_MODEL`, `DB_PATH`.

- [x] **3. `db.py`** — Create SQLite schema (`markets`, `trades`, `flags`) on `init_db()`. Implement upsert helpers for each table using `INSERT ... ON CONFLICT DO UPDATE`.

- [x] **4. `fetcher.py`** — Implement `fetch_markets(limit) -> list[Market]` (Gamma API) and `fetch_trades(condition_id, since_hours) -> list[Trade]` (Data API) using a shared `httpx.AsyncClient` with 10s timeout. HTTP errors log a warning and return `[]`.

- [x] **5. `signals.py`** — Implement `compute_signals(trades_by_market, markets_by_id) -> dict[str, list[str]]`. Signals:
  - `large_position`: `trade.size >= MIN_TRADE_SIZE`
  - `contrarian_trade`: `abs(trade.price - current_price) > 0.15` (current price from `market.outcome_prices`)
  - `cross_market_wallet`: same `proxy_wallet` in ≥2 distinct markets within the cycle (requires full `trades_by_market`)
  - `volume_price_shift`: market-level; price moved >10pp while window volume < median hourly volume

- [x] **6. `classifier.py`** — Implement `classify(trade, market, signals) -> Flag`. POST to Ollama `/api/chat` (`stream: false`, `temperature: 0`). Parse JSON from `response["message"]["content"]` into `Flag`. Validate fields defensively; on failure return `Flag` with `anomaly_type="unclassified"`, `confidence="low"`, `reasoning=<error>`.

- [x] **7. `main.py`** — Implement `poll_cycle()` and `main()`:
  1. Fetch all markets, upsert to DB
  2. Fetch trades for **all** markets, accumulating into `trades_by_market: dict[str, list[Trade]]`, upsert all trades to DB
  3. Call `compute_signals(trades_by_market, markets_by_id)` once after all trades are fetched
  4. For each trade with ≥2 signals, call `classify()` and upsert the resulting `Flag`
  5. Log counts: markets fetched, trades fetched, flags raised, classifications completed
  6. Loop with `asyncio.sleep(config.POLL_INTERVAL)`
