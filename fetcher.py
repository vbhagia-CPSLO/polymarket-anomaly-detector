import json
import logging
from datetime import datetime, timezone

import httpx

import config
from models import Market, Trade

logger = logging.getLogger(__name__)

_GAMMA_URL = "https://gamma-api.polymarket.com/markets"
_DATA_URL  = "https://data-api.polymarket.com/trades"


def _parse_outcome_prices(raw: str) -> list[float]:
    try:
        return [float(p) for p in json.loads(raw)]
    except Exception:
        return []


async def fetch_markets(limit: int = config.MARKET_LIMIT) -> list[Market]:
    params = {
        "active": "true",
        "closed": "false",
        "limit": limit,
        "order": "volume24hr",
        "ascending": "false",
    }
    fetched_at = datetime.now(timezone.utc).isoformat()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_GAMMA_URL, params=params)
            resp.raise_for_status()
            return [
                Market(
                    condition_id=m["conditionId"],
                    title=m.get("question", ""),
                    volume=float(m.get("volume", 0) or 0),
                    liquidity=float(m.get("liquidity", 0) or 0),
                    end_date=m.get("endDate", ""),
                    outcome_prices=_parse_outcome_prices(m.get("outcomePrices", "[]")),
                    fetched_at=fetched_at,
                )
                for m in resp.json()
                if m.get("conditionId")
            ]
    except Exception as e:
        logger.warning("fetch_markets failed: %s", e)
        return []


async def fetch_trades(condition_id: str, since_hours: int = config.TRADE_WINDOW_HOURS) -> list[Trade]:
    cutoff = datetime.now(timezone.utc).timestamp() - since_hours * 3600
    fetched_at = datetime.now(timezone.utc).isoformat()
    page_size = 500
    trades = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            offset = 0
            while True:
                resp = await client.get(_DATA_URL, params={
                    "conditionId": condition_id, "limit": page_size, "offset": offset,
                })
                resp.raise_for_status()
                batch = resp.json()
                if not batch:
                    break
                past_window = False
                for t in batch:
                    ts = float(t.get("timestamp", 0))
                    if ts < cutoff:
                        past_window = True
                        continue
                    if t.get("conditionId") != condition_id:
                        continue
                    trades.append(Trade(
                        transaction_hash=t["transactionHash"],
                        condition_id=t["conditionId"],
                        proxy_wallet=t.get("proxyWallet", ""),
                        side=t.get("side", ""),
                        size=float(t.get("size", 0) or 0),
                        price=float(t.get("price", 0) or 0),
                        outcome=t.get("outcome", ""),
                        timestamp=str(t.get("timestamp", "")),
                        fetched_at=fetched_at,
                    ))
                # If we got trades older than the window, no need to fetch more
                if past_window or len(batch) < page_size:
                    break
                offset += page_size
        return trades
    except Exception as e:
        logger.warning("fetch_trades(%s) failed: %s", condition_id, e)
        return trades  # return whatever we got so far
