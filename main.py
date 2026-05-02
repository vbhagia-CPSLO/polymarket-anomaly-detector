import asyncio
import logging
from datetime import datetime, timezone

import config
import db
from classifier import classify, filter_relevant_markets
from fetcher import fetch_markets, fetch_trades
from signals import compute_signals

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def poll_cycle(conn) -> None:
    # 1. Fetch markets
    all_markets = await fetch_markets(config.MARKET_LIMIT)
    logger.info("markets fetched: %d", len(all_markets))

    # 2. Filter to politics/economics via LLM batch call
    relevant = await filter_relevant_markets(all_markets)

    # 3. Filter to active (end_date not passed), upsert only these to DB
    active_relevant = [m for m in relevant if m.end_date and
                       datetime.fromisoformat(m.end_date.replace("Z", "+00:00")) > datetime.now(timezone.utc)]
    for m in active_relevant:
        db.upsert_market(conn, m)
    conn.commit()
    logger.info("relevant markets: %d | active: %d", len(relevant), len(active_relevant))

    markets_by_id = {m.condition_id: m for m in active_relevant}

    # 4. Fetch trades for ALL active relevant markets, accumulate globally
    trades_by_market: dict[str, list] = {}
    for market in active_relevant:
        trades = await fetch_trades(market.condition_id, config.TRADE_WINDOW_HOURS)
        if trades:
            trades_by_market[market.condition_id] = trades
            for t in trades:
                db.upsert_trade(conn, t)
    conn.commit()

    total_trades = sum(len(v) for v in trades_by_market.values())
    logger.info("trades fetched: %d across %d markets", total_trades, len(trades_by_market))

    # 4. Compute signals over the full global trade set
    signal_map = compute_signals(trades_by_market, markets_by_id)

    # 5. Classify and persist flags for trades with >=2 signals
    flags_raised = 0
    classified = 0
    for tx_hash, signals in signal_map.items():
        if len(signals) < 2:
            continue
        flags_raised += 1
        trade = next(
            t
            for trades in trades_by_market.values()
            for t in trades
            if t.transaction_hash == tx_hash
        )
        market = markets_by_id[trade.condition_id]
        flag = await classify(trade, market, signals)
        db.upsert_flag(conn, flag)
        classified += 1

    conn.commit()
    logger.info("flags raised: %d | classified: %d", flags_raised, classified)


async def main() -> None:
    conn = db.init_db(config.DB_PATH)
    try:
        while True:
            logger.info("--- poll cycle start ---")
            await poll_cycle(conn)
            logger.info("--- poll cycle done, sleeping %ds ---", config.POLL_INTERVAL)
            await asyncio.sleep(config.POLL_INTERVAL)
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(main())
