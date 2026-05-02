import sqlite3
from datetime import datetime, timezone, timedelta

import pytest

import db
from models import Market

NOW = datetime.now(timezone.utc)


def make_market(condition_id, end_date: datetime, **kwargs):
    return Market(
        condition_id=condition_id,
        title="Test Market",
        volume=1000.0,
        liquidity=500.0,
        end_date=end_date.isoformat(),
        outcome_prices=[0.6, 0.4],
        fetched_at=NOW.isoformat(),
        **kwargs,
    )


@pytest.fixture
def conn():
    c = db.init_db(":memory:")
    yield c
    c.close()


def get_active(conn, condition_id):
    return conn.execute(
        "SELECT active FROM markets WHERE condition_id=?", (condition_id,)
    ).fetchone()[0]


# --- active flag tests ---

def test_future_end_date_sets_active_1(conn):
    m = make_market("0x1", end_date=NOW + timedelta(days=30))
    db.upsert_market(conn, m)
    conn.commit()
    assert get_active(conn, "0x1") == 1


def test_past_end_date_sets_active_0(conn):
    m = make_market("0x1", end_date=NOW - timedelta(days=1))
    db.upsert_market(conn, m)
    conn.commit()
    assert get_active(conn, "0x1") == 0


def test_active_flag_updates_on_re_upsert(conn):
    # Insert as active
    m = make_market("0x1", end_date=NOW + timedelta(days=1))
    db.upsert_market(conn, m)
    conn.commit()
    assert get_active(conn, "0x1") == 1

    # Re-upsert after expiry
    m_expired = make_market("0x1", end_date=NOW - timedelta(seconds=1))
    db.upsert_market(conn, m_expired)
    conn.commit()
    assert get_active(conn, "0x1") == 0


def test_invalid_end_date_defaults_to_active_1(conn):
    m = make_market("0x1", end_date=NOW)  # override below
    m.end_date = "not-a-date"
    db.upsert_market(conn, m)
    conn.commit()
    assert get_active(conn, "0x1") == 1


# --- upsert idempotency ---

def test_upsert_market_is_idempotent(conn):
    m = make_market("0x1", end_date=NOW + timedelta(days=10))
    db.upsert_market(conn, m)
    db.upsert_market(conn, m)
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM markets WHERE condition_id='0x1'").fetchone()[0]
    assert count == 1


def test_upsert_market_updates_volume(conn):
    m1 = make_market("0x1", end_date=NOW + timedelta(days=10))
    m1.volume = 1000.0
    db.upsert_market(conn, m1)
    conn.commit()

    m2 = make_market("0x1", end_date=NOW + timedelta(days=10))
    m2.volume = 9999.0
    db.upsert_market(conn, m2)
    conn.commit()

    vol = conn.execute("SELECT volume FROM markets WHERE condition_id='0x1'").fetchone()[0]
    assert vol == 9999.0
