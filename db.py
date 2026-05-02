import json
import sqlite3
from datetime import datetime, timezone

import config
from models import Flag, Market, Trade


def init_db(db_path: str = config.DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS markets (
            condition_id TEXT PRIMARY KEY,
            title        TEXT,
            volume       REAL,
            liquidity    REAL,
            end_date     TEXT,
            active       INTEGER DEFAULT 1,
            fetched_at   TEXT
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
            signals_triggered TEXT,
            signal_count      INTEGER,
            anomaly_type      TEXT,
            confidence        TEXT,
            reasoning         TEXT,
            flagged_at        TEXT
        );
    """)
    conn.commit()
    return conn


def upsert_market(conn: sqlite3.Connection, m: Market) -> None:
    try:
        active = int(datetime.fromisoformat(m.end_date.replace("Z", "+00:00")) > datetime.now(timezone.utc))
    except Exception:
        active = 1
    conn.execute("""
        INSERT INTO markets VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(condition_id) DO UPDATE SET
            volume=excluded.volume, liquidity=excluded.liquidity,
            active=excluded.active, fetched_at=excluded.fetched_at
    """, (m.condition_id, m.title, m.volume, m.liquidity, m.end_date, active, m.fetched_at))


def upsert_trade(conn: sqlite3.Connection, t: Trade) -> None:
    conn.execute("""
        INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(transaction_hash) DO UPDATE SET fetched_at=excluded.fetched_at
    """, (t.transaction_hash, t.condition_id, t.proxy_wallet, t.side,
          t.size, t.price, t.outcome, t.timestamp, t.fetched_at))


def upsert_flag(conn: sqlite3.Connection, f: Flag) -> None:
    conn.execute("""
        INSERT INTO flags
            (transaction_hash, condition_id, signals_triggered, signal_count, anomaly_type, confidence, reasoning, flagged_at)
        VALUES (?,?,?,?,?,?,?,?)
    """, (f.transaction_hash, f.condition_id, json.dumps(f.signals_triggered),
          len(f.signals_triggered), f.anomaly_type, f.confidence, f.reasoning, f.flagged_at))
