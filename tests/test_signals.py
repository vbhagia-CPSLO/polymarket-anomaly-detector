from signals import compute_signals
from models import Market, Trade
from datetime import datetime, timezone

NOW = datetime.now(timezone.utc).isoformat()


def make_market(condition_id, outcome_prices=None, liquidity=10000.0):
    return Market(
        condition_id=condition_id,
        title="Test Market",
        volume=5000.0,
        liquidity=liquidity,
        end_date="2026-12-01",
        outcome_prices=outcome_prices or [0.6, 0.4],
        fetched_at=NOW,
    )


def make_trade(tx, condition_id, size=100.0, price=0.6, wallet="0xwallet1"):
    return Trade(
        transaction_hash=tx,
        condition_id=condition_id,
        proxy_wallet=wallet,
        side="BUY",
        size=size,
        price=price,
        outcome="Yes",
        timestamp=str(int(datetime.now(timezone.utc).timestamp())),
        fetched_at=NOW,
    )


# --- large_position ---

def test_large_position_signal():
    trade = make_trade("tx1", "0xabc", size=6000.0)
    result = compute_signals({"0xabc": [trade]}, {"0xabc": make_market("0xabc")})
    assert "large_position" in result["tx1"]


def test_no_large_position_below_threshold():
    trade = make_trade("tx1", "0xabc", size=100.0)
    result = compute_signals({"0xabc": [trade]}, {"0xabc": make_market("0xabc")})
    assert "large_position" not in result.get("tx1", [])


# --- contrarian_trade ---

def test_contrarian_trade_signal():
    trade = make_trade("tx1", "0xabc", price=0.2)
    result = compute_signals({"0xabc": [trade]}, {"0xabc": make_market("0xabc", outcome_prices=[0.6, 0.4])})
    assert "contrarian_trade" in result["tx1"]


def test_no_contrarian_trade_within_threshold():
    trade = make_trade("tx1", "0xabc", price=0.65)
    result = compute_signals({"0xabc": [trade]}, {"0xabc": make_market("0xabc", outcome_prices=[0.6, 0.4])})
    assert "contrarian_trade" not in result.get("tx1", [])


# --- rapid_repeat_trades ---

def test_rapid_repeat_trades_signal():
    trades = [make_trade(f"tx{i}", "0xabc", wallet="0xspammer") for i in range(3)]
    result = compute_signals({"0xabc": trades}, {"0xabc": make_market("0xabc")})
    for t in trades:
        assert "rapid_repeat_trades" in result[t.transaction_hash]


def test_no_rapid_repeat_trades_below_threshold():
    trades = [make_trade(f"tx{i}", "0xabc", wallet="0xwallet") for i in range(2)]
    result = compute_signals({"0xabc": trades}, {"0xabc": make_market("0xabc")})
    for t in trades:
        assert "rapid_repeat_trades" not in result.get(t.transaction_hash, [])


# --- size_outlier ---

def test_size_outlier_signal():
    # 9 small trades + 1 giant outlier
    # With enough baseline trades the outlier dominates stdev and clears mean+2*stdev
    trades = [make_trade(f"tx{i}", "0xabc", size=10.0, wallet=f"0xwallet{i}") for i in range(9)]
    outlier = make_trade("tx_big", "0xabc", size=10000.0, wallet="0xoutlier")
    all_trades = trades + [outlier]
    result = compute_signals({"0xabc": all_trades}, {"0xabc": make_market("0xabc")})
    assert "size_outlier" in result["tx_big"]
    for t in trades:
        assert "size_outlier" not in result.get(t.transaction_hash, [])


def test_size_outlier_skipped_with_fewer_than_3_trades():
    trades = [make_trade(f"tx{i}", "0xabc", size=10000.0) for i in range(2)]
    result = compute_signals({"0xabc": trades}, {"0xabc": make_market("0xabc")})
    for t in trades:
        assert "size_outlier" not in result.get(t.transaction_hash, [])


# --- multiple signals ---

def test_multiple_signals_on_same_trade():
    trade = make_trade("tx1", "0xabc", size=6000.0, price=0.1)
    result = compute_signals({"0xabc": [trade]}, {"0xabc": make_market("0xabc", outcome_prices=[0.6, 0.4])})
    assert "large_position" in result["tx1"]
    assert "contrarian_trade" in result["tx1"]


def test_trade_with_no_signals_not_in_result():
    trade = make_trade("tx1", "0xabc", size=10.0, price=0.6)
    result = compute_signals({"0xabc": [trade]}, {"0xabc": make_market("0xabc", outcome_prices=[0.6, 0.4])})
    assert "tx1" not in result


def test_empty_input():
    assert compute_signals({}, {}) == {}

# --- volume_price_shift ---

def test_volume_price_shift_signal():
    # current_price=0.8, oldest trade price=0.6 → shift=0.2 > 0.10
    # total_volume=20, liquidity=10000 → 20 < 1000 (10% of liquidity)
    market = make_market("0xabc", outcome_prices=[0.8, 0.2], liquidity=10000.0)
    trades = [
        make_trade("tx_new", "0xabc", size=10.0, price=0.8),  # newest
        make_trade("tx_old", "0xabc", size=10.0, price=0.6),  # oldest (window open)
    ]
    result = compute_signals({"0xabc": trades}, {"0xabc": market})
    assert "volume_price_shift" in result["tx_new"]
    assert "volume_price_shift" in result["tx_old"]


def test_no_volume_price_shift_large_volume():
    # same price shift but volume is large relative to liquidity → no signal
    market = make_market("0xabc", outcome_prices=[0.8, 0.2], liquidity=10.0)
    trades = [
        make_trade("tx_new", "0xabc", size=5.0, price=0.8),
        make_trade("tx_old", "0xabc", size=5.0, price=0.6),
    ]
    # total_volume=10, liquidity=10 → 10 is NOT < 1.0 (10% of 10)
    result = compute_signals({"0xabc": trades}, {"0xabc": market})
    assert "volume_price_shift" not in result.get("tx_new", [])


def test_no_volume_price_shift_with_single_trade():
    # needs >=2 trades to compute window movement
    market = make_market("0xabc", outcome_prices=[0.8, 0.2], liquidity=10000.0)
    trade = make_trade("tx1", "0xabc", size=10.0, price=0.6)
    result = compute_signals({"0xabc": [trade]}, {"0xabc": market})
    assert "volume_price_shift" not in result.get("tx1", [])
