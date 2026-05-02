import requests
import pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict
import time

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"

def get_markets(limit=100, offset=0):
    """Fetch active markets from Polymarket Gamma API."""
    url = f"{GAMMA_BASE}/markets"
    params = {
        "limit": limit,
        "offset": offset,
        "active": True,
        "closed": False,
        "order": "volume24hr",
        "ascending": False
    }
    resp = requests.get(url, params=params)
    resp.raise_for_status()
    return resp.json()

def get_trades(market_id=None, limit=500, offset=0):
    """Fetch recent trades from CLOB API."""
    url = f"{CLOB_BASE}/trades"
    params = {"limit": limit, "offset": offset}
    if market_id:
        params["market"] = market_id
    resp = requests.get(url, params=params)
    resp.raise_for_status()
    return resp.json()

def get_market_trades_gamma(condition_id, limit=500):
    """Fetch trades for a specific market via Gamma API."""
    url = f"{GAMMA_BASE}/trades"
    params = {
        "market": condition_id,
        "limit": limit
    }
    resp = requests.get(url, params=params)
    resp.raise_for_status()
    return resp.json()

def analyze_large_trades(trades, volume_threshold_usd=5000):
    """Flag trades that are unusually large by notional value."""
    large_trades = []
    for t in trades:
        try:
            size = float(t.get("size", 0))
            price = float(t.get("price", 0))
            notional = size * price
            if notional >= volume_threshold_usd:
                large_trades.append({
                    "trade_id": t.get("id"),
                    "market": t.get("market"),
                    "outcome": t.get("outcome"),
                    "side": t.get("side"),
                    "price": price,
                    "size": size,
                    "notional_usd": round(notional, 2),
                    "timestamp": t.get("timestamp"),
                    "maker": t.get("maker_address"),
                    "taker": t.get("taker_address"),
                })
        except (TypeError, ValueError):
            continue
    return large_trades

def analyze_contrarian_trades(trades, contrarian_threshold=0.25):
    """
    Flag trades where someone bought YES at low probability (<threshold)
    or bought NO at high probability (>1-threshold), i.e., against consensus.
    """
    contrarian = []
    for t in trades:
        try:
            price = float(t.get("price", 0))
            side = t.get("side", "").upper()
            outcome = t.get("outcome", "").upper()
            size = float(t.get("size", 0))
            notional = size * price

            # Buying YES when market says it's unlikely
            if side == "BUY" and outcome == "YES" and price < contrarian_threshold and notional > 500:
                contrarian.append({**t, "flag": f"BUY YES at low prob ({price:.2%})", "notional_usd": round(notional, 2)})
            # Buying NO when market says it's very likely
            elif side == "BUY" and outcome == "NO" and price < contrarian_threshold and notional > 500:
                contrarian.append({**t, "flag": f"BUY NO at low prob ({price:.2%})", "notional_usd": round(notional, 2)})
        except (TypeError, ValueError):
            continue
    return contrarian

def build_user_profiles(all_trades):
    """
    Build per-user trade history to track win rates.
    Assumes resolved markets have outcome data attached.
    """
    user_trades = defaultdict(list)
    for t in all_trades:
        maker = t.get("maker_address")
        taker = t.get("taker_address")
        for addr in [maker, taker]:
            if addr:
                user_trades[addr].append(t)
    return user_trades

def score_users(user_trades, resolved_markets):
    """
    Score users by how often they were on the winning side of resolved markets.
    resolved_markets: dict of {condition_id: winning_outcome}
    """
    scores = []
    for addr, trades in user_trades.items():
        wins = 0
        total = 0
        total_notional = 0
        for t in trades:
            market_id = t.get("market")
            if market_id not in resolved_markets:
                continue
            winner = resolved_markets[market_id]
            outcome = t.get("outcome", "").upper()
            side = t.get("side", "").upper()
            try:
                price = float(t.get("price", 0))
                size = float(t.get("size", 0))
                notional = size * price
            except (TypeError, ValueError):
                continue

            total += 1
            total_notional += notional
            # A winning trade: bought the outcome that resolved true
            if side == "BUY" and outcome == winner.upper():
                wins += 1

        if total >= 5:  # only score users with meaningful history
            win_rate = wins / total
            scores.append({
                "address": addr,
                "total_trades": total,
                "wins": wins,
                "win_rate": round(win_rate, 4),
                "total_notional_usd": round(total_notional, 2),
            })

    df = pd.DataFrame(scores)
    if df.empty:
        return df
    return df.sort_values("win_rate", ascending=False)

def get_resolved_markets(limit=200):
    """Fetch recently resolved markets."""
    url = f"{GAMMA_BASE}/markets"
    params = {
        "limit": limit,
        "closed": True,
        "order": "endDate",
        "ascending": False
    }
    resp = requests.get(url, params=params)
    resp.raise_for_status()
    markets = resp.json()
    resolved = {}
    for m in markets:
        cid = m.get("conditionId") or m.get("id")
        outcome = m.get("outcomePrices")  # list of final prices, e.g. [1, 0] means YES won
        outcomes = m.get("outcomes", ["YES", "NO"])
        if cid and outcome:
            try:
                prices = [float(p) for p in outcome]
                winner_idx = prices.index(max(prices))
                resolved[cid] = outcomes[winner_idx] if winner_idx < len(outcomes) else "YES"
            except (ValueError, TypeError):
                continue
    return resolved

def run_analysis(
    large_trade_threshold=5000,
    contrarian_threshold=0.20,
    markets_to_scan=20,
    min_win_rate=0.70,
    min_trades=5
):
    print("Fetching active markets...")
    markets = get_markets(limit=markets_to_scan)
    if isinstance(markets, dict):
        markets = markets.get("data", markets.get("markets", []))

    print(f"Fetched {len(markets)} markets. Fetching resolved markets...")
    resolved_markets = get_resolved_markets(limit=200)
    print(f"Found {len(resolved_markets)} resolved markets.")

    all_trades = []
    large_trade_flags = []
    contrarian_flags = []

    for i, market in enumerate(markets[:markets_to_scan]):
        cid = market.get("conditionId") or market.get("id")
        slug = market.get("slug", cid)
        print(f"  [{i+1}/{markets_to_scan}] Scanning market: {slug}")
        try:
            trades = get_market_trades_gamma(cid, limit=500)
            if isinstance(trades, dict):
                trades = trades.get("data", [])
            all_trades.extend(trades)
            large_trade_flags.extend(analyze_large_trades(trades, large_trade_threshold))
            contrarian_flags.extend(analyze_contrarian_trades(trades, contrarian_threshold))
            time.sleep(0.3)  # be polite to the API
        except Exception as e:
            print(f"    Error fetching trades for {slug}: {e}")
            continue

    print(f"\nTotal trades collected: {len(all_trades)}")
    print(f"Large trades flagged (>=${large_trade_threshold}): {len(large_trade_flags)}")
    print(f"Contrarian trades flagged: {len(contrarian_flags)}")

    # --- Large Trades Report ---
    if large_trade_flags:
        df_large = pd.DataFrame(large_trade_flags).sort_values("notional_usd", ascending=False)
        print("\n=== TOP LARGE TRADES ===")
        print(df_large[["market", "outcome", "side", "price", "notional_usd", "maker", "taker", "timestamp"]].head(20).to_string(index=False))
    else:
        print("\nNo large trades found above threshold.")

    # --- Contrarian Trades Report ---
    if contrarian_flags:
        df_contra = pd.DataFrame(contrarian_flags).sort_values("notional_usd", ascending=False)
        print("\n=== CONTRARIAN / AGAINST-THE-MARKET TRADES ===")
        cols = [c for c in ["market", "outcome", "side", "price", "notional_usd", "flag", "maker_address", "taker_address", "timestamp"] if c in df_contra.columns]
        print(df_contra[cols].head(20).to_string(index=False))
    else:
        print("\nNo significant contrarian trades found.")

    # --- User Win Rate Analysis ---
    print("\nBuilding user profiles for insider trading detection...")
    user_profiles = build_user_profiles(all_trades)
    df_scores = score_users(user_profiles, resolved_markets)

    if not df_scores.empty:
        suspicious = df_scores[
            (df_scores["win_rate"] >= min_win_rate) &
            (df_scores["total_trades"] >= min_trades)
        ]
        print(f"\n=== SUSPICIOUS HIGH WIN-RATE USERS (win_rate >= {min_win_rate:.0%}, trades >= {min_trades}) ===")
        if suspicious.empty:
            print("No suspicious users found with current thresholds.")
        else:
            print(suspicious.to_string(index=False))
            print(f"\nTotal suspicious addresses: {len(suspicious)}")
    else:
        print("Not enough resolved trade data to score users.")

    return {
        "large_trades": large_trade_flags,
        "contrarian_trades": contrarian_flags,
        "user_scores": df_scores,
    }

if __name__ == "__main__":
    results = run_analysis(
        large_trade_threshold=5000,   # flag trades > $5,000 notional
        contrarian_threshold=0.20,    # flag buys when prob < 20%
        markets_to_scan=20,           # number of top markets to scan
        min_win_rate=0.70,            # flag users winning 70%+ of resolved bets
        min_trades=5                  # minimum trades to be considered
    )
