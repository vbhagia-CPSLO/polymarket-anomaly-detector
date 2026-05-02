# Polymarket Anomaly Detector — Spec

## Overview

A local monitoring system that polls the top Polymarket markets, detects potentially anomalous trading activity (large contrarian positions, volume-adjusted price impact), classifies flagged events using a local LLM, and stores results in SQLite for analysis and demo.

The social good angle: surfacing potential insider trading on prediction markets improves market integrity and price accuracy, which makes the markets more useful as information aggregators.

## Goals

- Poll the top 100 markets from Polymarket every hour
- For each market, fetch recent trades and compute anomaly signals
- Flag suspicious trades based on size, price deviation, and price impact
- Classify flagged trades using a local Ollama instance
- Persist markets, trades, and flags to SQLite

## Non-Goals

- No web UI (for now)
- No trading or order execution
- No cloud deployment
- No real-time streaming (polling is fine for MVP)

## Users

Just me, running locally.

## Data Sources

### Gamma API — Market Discovery
`GET https://gamma-api.polymarket.com/markets`
- Fetch top 100 markets by volume
- Fields used: `conditionId`, `title`, `description`, `volume`, `liquidity`, `endDate`, `outcomePrices`

### Data API — Trade Fetch
`GET https://data-api.polymarket.com/trades?conditionId=<id>`
- No authentication required
- Fields used: `proxyWallet`, `side`, `size`, `price`, `timestamp`, `outcome`, `transactionHash`, `title`

### CLOB API — Current Price (skipped for MVP)
`GET https://clob.polymarket.com/price?token_id=<asset>`
- Requires API key; skipped for MVP
- Current price is derived from `outcomePrices` in the Gamma API response instead

## Anomaly Signals

### Market-Level (per market, per poll)
- **Volume-price shift**: price moved >10pp across the trade window while total volume is <10% of market liquidity. Indicates thin-market manipulation or informed trading.

### Trade-Level (per trade, within relevant markets)
- **Large position**: `size` ≥ $5,000
- **Contrarian trade**: trade `price` deviates >15pp from current market consensus (`outcomePrices`)
- **Rapid repeat trades**: same `proxyWallet` makes ≥3 trades in the same market within the trade window
- **Size outlier**: trade `size` > mean + 2σ of all trades in that market this cycle (requires ≥3 trades)

Noise reduction: require at least 2 signals to trigger a flag.

## Classification

- Model: `qwen2.5:32b` via local Ollama instance (configurable via `OLLAMA_MODEL`)
- Markets are classified for relevance in batches of 25 for accuracy
- Triggered only on flagged trades/events (not all trades)
- Input: market title, trade details, signals triggered, current market price
- Output (structured):
  - `anomaly_type`: one of `informed_trading`, `wash_trading`, `liquidity_shock`, `normal_large_trade`
  - `confidence`: low / medium / high
  - `reasoning`: brief natural language explanation

## Data Storage

SQLite, local file. Three tables:

**`markets`**
- `condition_id` (PK), `title`, `volume`, `liquidity`, `end_date`, `active`, `fetched_at`
- Only topically relevant (politics/economics) markets are stored
- `active` = 1 when `end_date` has not passed, 0 when expired

**`trades`**
- `transaction_hash` (PK), `condition_id` (FK), `proxy_wallet`, `side`, `size`, `price`, `outcome`, `timestamp`, `fetched_at`

**`flags`**
- `id` (PK), `transaction_hash` (UNIQUE FK), `condition_id` (FK), `signals_triggered`, `signal_count`, `anomaly_type`, `confidence`, `reasoning`, `flagged_at`

Upsert on `transaction_hash` to avoid duplicates across poll cycles.

## Configuration

- `POLL_INTERVAL` — how often to run (default: 1 hour)
- `MARKET_LIMIT` — number of markets to fetch, sorted by volume24hr (default: 100)
- `TRADE_WINDOW_HOURS` — how far back to fetch trades per market (default: 1 hour)
- `MIN_TRADE_SIZE` — minimum size to consider for `large_position` signal (default: $5,000)
- `OLLAMA_ENDPOINT` — local Ollama base URL (default: http://sunils-mac-studio:11434)
- `OLLAMA_MODEL` — model to use (default: qwen2.5:7b)
- `DB_PATH` — SQLite file path (default: ./polymarket.db)

## Out of Scope (for now)

- Alerting / notifications
- Dashboard or web UI
- Historical trend analysis beyond current poll window
- Wallet reputation tracking across time
