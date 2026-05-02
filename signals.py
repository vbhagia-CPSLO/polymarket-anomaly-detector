import math
from collections import defaultdict
from datetime import datetime, timezone

import config
from models import Market, Trade

# Tunable thresholds
_CONTRARIAN_THRESHOLD = 0.15   # price deviation from market consensus (15pp)
_PRICE_SHIFT_THRESHOLD = 0.10  # market-level price movement (10pp)
_RAPID_REPEAT_MIN = 3          # same wallet, same market, within window
_SIZE_OUTLIER_STDEV = 2.0      # trade.size > mean + N * stdev
_SIZE_OUTLIER_MIN_TRADES = 3   # minimum trades in market to compute stdev
_RELATIVE_SIZE_THRESHOLD = 0.05  # trade.size / market.liquidity > 5%
_PRE_RESOLUTION_HOURS = 24     # large trade within 24h of market end_date
_PRICE_IMPACT_THRESHOLD = 0.05  # single trade caused >5pp price shift


def _mean_stdev(values: list[float]) -> tuple[float, float]:
    n = len(values)
    if n < 2:
        return 0.0, 0.0
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / n
    return mean, math.sqrt(variance)


def _parse_end_date(end_date: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def compute_signals(
    trades_by_market: dict[str, list[Trade]],
    markets_by_id: dict[str, Market],
) -> dict[str, list[str]]:
    """
    Returns {transaction_hash: [signal_name, ...]} for trades with >=1 signal.
    Caller filters for len(signals) >= 2 before flagging.
    """
    signals: dict[str, list[str]] = defaultdict(list)
    now = datetime.now(timezone.utc)

    for condition_id, trades in trades_by_market.items():
        # Sort by timestamp descending (newest first) for volume_price_shift / price_impact
        trades = sorted(trades, key=lambda t: t.timestamp, reverse=True)
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
        market_flagged = False
        if market and current_price is not None and len(trades) >= 2:
            total_volume = sum(t.size for t in trades)
            window_open_price = trades[-1].price
            price_shift = abs(current_price - window_open_price)
            if total_volume > 0 and market.liquidity > 0:
                if price_shift > _PRICE_SHIFT_THRESHOLD and total_volume < market.liquidity * 0.1:
                    market_flagged = True

        # --- pre_resolution_trade: is market within 24h of end_date? ---
        near_resolution = False
        if market:
            end_dt = _parse_end_date(market.end_date)
            if end_dt and 0 < (end_dt - now).total_seconds() < _PRE_RESOLUTION_HOURS * 3600:
                near_resolution = True

        for i, t in enumerate(trades):
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

            # relative_size: trade is >5% of market liquidity
            if market and market.liquidity > 0 and t.size / market.liquidity > _RELATIVE_SIZE_THRESHOLD:
                signals[t.transaction_hash].append("relative_size")

            # pre_resolution_trade: large trade within 24h of market close
            if near_resolution and t.size >= config.MIN_TRADE_SIZE:
                signals[t.transaction_hash].append("pre_resolution_trade")

            # price_impact: this trade caused >5pp shift vs adjacent trade
            if len(trades) >= 2 and i < len(trades) - 1:
                prev_price = trades[i + 1].price  # previous trade (older)
                if abs(t.price - prev_price) > _PRICE_IMPACT_THRESHOLD:
                    signals[t.transaction_hash].append("price_impact")

    return dict(signals)
