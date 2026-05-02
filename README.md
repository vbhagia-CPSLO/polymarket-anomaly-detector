# Polymarket Anomaly Detector

A local polling system that monitors political and economic prediction markets on Polymarket, detects suspicious trading activity, and classifies flagged trades using a local LLM.

## What it does

Each hour it:
1. Fetches the top 100 markets by 24hr volume from Polymarket
2. Uses a local Ollama instance to filter down to politics/economics markets only (batched in groups of 25 for accuracy)
3. Fetches all trades from the last hour for active relevant markets
4. Runs a signal engine to detect anomalous trades
5. Classifies flagged trades via LLM and persists everything to SQLite

## Anomaly signals

A trade is flagged if it trips **≥2** of the following:

| Signal | Description |
|--------|-------------|
| `large_position` | Trade size ≥ $5,000 |
| `contrarian_trade` | Execution price deviates >15pp from market consensus |
| `rapid_repeat_trades` | Same wallet makes ≥3 trades in the same market within the window |
| `size_outlier` | Trade size > mean + 2σ of all trades in that market this cycle |
| `volume_price_shift` | Price moved >10pp across the trade window on <10% of market liquidity |

Flagged trades are classified by the LLM as: `informed_trading`, `wash_trading`, `liquidity_shock`, or `normal_large_trade`.

## Requirements

- Python 3.9+
- [Ollama](https://ollama.ai) running locally with `qwen2.5:32b` pulled

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` if your Ollama endpoint or model differs from the defaults.

## Run

```bash
source .venv/bin/activate && python main.py
```

To run persistently in the background:

```bash
nohup python main.py > polymarket.log 2>&1 &
echo $! > polymarket.pid
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `POLL_INTERVAL` | `3600` | Seconds between poll cycles |
| `MARKET_LIMIT` | `100` | Number of markets to fetch per cycle |
| `TRADE_WINDOW_HOURS` | `1` | How far back to fetch trades |
| `MIN_TRADE_SIZE` | `5000` | Minimum trade size for `large_position` signal |
| `OLLAMA_ENDPOINT` | `http://sunils-mac-studio:11434` | Ollama base URL |
| `OLLAMA_MODEL` | `qwen2.5:32b` | Model to use for classification |
| `DB_PATH` | `./polymarket.db` | SQLite database path |

## Database

Three tables in `polymarket.db`:

- **`markets`** — active politics/economics markets (indexed by `active`)
- **`trades`** — all trades fetched within the window for relevant markets
- **`flags`** — trades that tripped ≥2 signals, with LLM classification and `signal_count`

Useful queries:

```sql
-- Most suspicious trades first
SELECT * FROM flags ORDER BY signal_count DESC;

-- Trades that tripped 3+ signals
SELECT f.signal_count, f.signals_triggered, f.anomaly_type, f.confidence, f.reasoning,
       m.title
FROM flags f JOIN markets m ON f.condition_id = m.condition_id
WHERE f.signal_count >= 3;

-- Active relevant markets
SELECT title, volume, liquidity FROM markets WHERE active = 1;
```

## Tests

```bash
source .venv/bin/activate && python -m pytest tests/ -q
```

38 tests covering fetcher, signals, classifier (including LLM retry logic), and DB upsert behaviour.
