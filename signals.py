import math
from collections import defaultdict

import config
from models import Market, Trade

# Tunable thresholds
_CONTRARIAN_THRESHOLD = 0.15   # price deviation from market consensus (15pp)
_PRICE_SHIFT_THRESHOLD = 0.10  # market-level price movement (10pp)
_RAPID_REPEAT_MIN = 3          # same wallet, same market, within window
_SIZE_OUTLIER_STDEV = 2.0      # trade.size > mean + N * stdev
_SIZE_OUTLIER_MIN_TRADES = 3   # minimum trades in market to compute stdev


def _mean_stdev(values: list[float]) -> tuple[float, float]:
    n = len(values)
    if n < 2:
        return 0.0, 0.0
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / n
    return mean, math.sqrt(variance)


def compute_signals(
    trades_by_market: dict[str, list[Trade]],
    markets_by_id: dict[str, Market],
) -> dict[str, list[str]]:
    """
    Returns {transaction_hash: [signal_name, ...]} for trades with >=1 signal.
    Caller filters for len(signals) >= 2 before flagging.
    """
    signals: dict[str, list[str]] = defaultdict(list)

    for condition_id, trades in trades_by_market.items():
        market = markets_by_id.get(condition_id)
        current_price = market.outcome_prices[0] if market and market.outcome_prices else None

        # --- rapid_repeat_trades: wallet with >=3 trades in this market ---
        wallet_trade_count: dict[str, int] = defaultdict(int)
        for t in trades:
            wallet_trade_count[t.proxy_wallet] += 1
        repeat_wallets = {w for w, c in wallet_trade_count.items() if c >= _RAPID_REPEAT_MIN}

        # --- size_outlier: trade.size > mean + 2*stdev for this market ---
        sizes = [t.size for t in trades]
        mean_size, stdev_size = _mean_stdev(sizes) if len(sizes) >= _SIZE_OUTLIER_MIN_TRADES else (0.0, 0.0)
        size_outlier_threshold = mean_size + _SIZE_OUTLIER_STDEV * stdev_size if stdev_size > 0 else None

        # --- volume_price_shift: market-level ---
        # Measures actual price movement: oldest trade in window vs current_price.
        # Trades are newest-first from the API; trades[-1] is the window-open price.
        market_flagged = False
        if market and current_price is not None and len(trades) >= 2:
            total_volume = sum(t.size for t in trades)
            window_open_price = trades[-1].price
            price_shift = abs(current_price - window_open_price)
            if total_volume > 0 and market.liquidity > 0:
                if price_shift > _PRICE_SHIFT_THRESHOLD and total_volume < market.liquidity * 0.1:
                    market_flagged = True

        for t in trades:
            # large_position
            if t.size >= config.MIN_TRADE_SIZE:
                signals[t.transaction_hash].append("large_position")

            # contrarian_trade
            if current_price is not None and abs(t.price - current_price) > _CONTRARIAN_THRESHOLD:
                signals[t.transaction_hash].append("contrarian_trade")

            # rapid_repeat_trades
            if t.proxy_wallet in repeat_wallets:
                signals[t.transaction_hash].append("rapid_repeat_trades")

            # size_outlier
            if size_outlier_threshold is not None and t.size > size_outlier_threshold:
                signals[t.transaction_hash].append("size_outlier")

            # volume_price_shift (market-level)
            if market_flagged:
                signals[t.transaction_hash].append("volume_price_shift")

    return dict(signals)
