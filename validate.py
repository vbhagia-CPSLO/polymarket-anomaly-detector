"""Validation harness: test anomaly detection against known insider-trading and clean markets."""
import asyncio
import json
import logging
import sys
import time
from collections import defaultdict
from statistics import median

import httpx

from models import Market, Trade
from signals import compute_signals

logging.basicConfig(level=logging.WARNING)

DATA_URL = "https://data-api.polymarket.com/trades"
CLOB_URL = "https://clob.polymarket.com/markets"

# ── Labeled dataset ──────────────────────────────────────────────────────────
# label: "insider" = confirmed/highly-probable insider trading
#        "clean"   = no insider trading expected

LABELED_MARKETS = [
    # ── POSITIVES: Insider Trading ──────────────────────────────────────────
    # Maduro capture (confirmed — criminal indictment of Gannon Van Dyke)
    {"cid": "0x69ac865d7824f22808d29deec2ed5090eaeeb27094e6936970c4b4c5660a87d5",
     "label": "insider", "case": "Maduro capture"},

    # US strikes Iran Feb 28 cluster (Bubblemaps analysis — $494k profit)
    {"cid": "0x3488f31e6449f9803f99a8b5dd232c7ad883637f1c86e6953305a2ef19c77f20",
     "label": "insider", "case": "US strikes Iran Feb 28"},
    {"cid": "0x15aa3c1259a716915e068a0d63c3885d2301d29e8982cbb1717ecb9b63d02d95",
     "label": "insider", "case": "US strikes Iran Mar 1"},
    {"cid": "0x1b9cfd142b849edf7a886364d9821f7e8049cf7648e97c73f05e68827f516e2e",
     "label": "insider", "case": "US strikes Iran Feb 19 (failed pre-position)"},
    {"cid": "0xe1c67f75aac5b10dc28f1a2fbb79b079fc7f7320abfbd6a950a50c372979569b",
     "label": "insider", "case": "US strikes Iran Feb 20"},

    # US forces enter Iran (congressional scrutiny — $2M cluster)
    {"cid": "0x306d10d4a4d51b41910dbc779ca00908bd917c131541c5c42bbbc736258d2d56",
     "label": "insider", "case": "US forces enter Iran Mar 31"},
    {"cid": "0x6d0e09d0f04572d9b1adad84703458b0297bc5603b69dccbde93147ee4443246",
     "label": "insider", "case": "US forces enter Iran Apr 30"},
    {"cid": "0x128115e9d8f5144654fac057655943efef46434ca944e9733e0b09f3c9271ddf",
     "label": "insider", "case": "US forces enter Iran Mar 1"},

    # Iran ceasefire (April 7 — 50 new accounts, congressional letter)
    {"cid": "0x0916a8da49aeeecca946d33ce561f1f1f432720d7d2b4b02bd5dbb54da24ea74",
     "label": "insider", "case": "Israel-Iran ceasefire"},
    {"cid": "0x0141bc972b2f4a4591e471742426c99da055fa513f41de9dfdc449fdfc60747f",
     "label": "insider", "case": "US-Iran ceasefire before July"},

    # Nobel Peace Prize — Machado (Norwegian investigation, "dirtycup" $70k bet)
    {"cid": "0x14a3dfeba8b22a32feb0f10763db68bc4d2abeb5bff90e9ae20de53793b35a1d",
     "label": "insider", "case": "Nobel Peace Prize Machado"},

    # Israel strike Iran Oct 2024 (IDF reservist indictment)
    {"cid": "0x142942528bc24bc82165d4b4929e7628400199925ffbcaace270dedb35fb665f",
     "label": "insider", "case": "Israel strike Iran Sun Oct 13"},
    {"cid": "0x50f2f68bdbb073f682c429cddaaee7f1685d6df1f04d66becf270c56fb5ee42c",
     "label": "insider", "case": "Israel strike Iran Sat Oct 12"},

    # Israel strike Yemen Sept 2025 (IDF reservist — second incident)
    {"cid": "0xe803479873077dc26a42bcb2ee3f146c6e4a2846861ce66bfa40a080b8ea1adf",
     "label": "insider", "case": "Israel strike Yemen Sept 11"},

    # ── NEGATIVES: Clean Markets ────────────────────────────────────────────
    # Cabinet confirmations — predictable, public process
    {"cid": "0x426ed0832cdf69783a463d39111d70841ad808bd8696e77098f8e3f41ff24e5b",
     "label": "clean", "case": "Bessent confirmed Treasury"},
    {"cid": "0x3b1121fddcadbd2b8bb2c96bf815a5303b72b8f90fa3145140ab607cec232fae",
     "label": "clean", "case": "Rubio confirmed SecState"},

    # Fed rate decisions — telegraphed, broad participation
    {"cid": "0xa0811c97f529d627b7774a5b188e605736b745a1f892c39e16c5a022fdb84b8b",
     "label": "clean", "case": "Fed rate cut Sept 2024"},
    {"cid": "0x4057c2528a3815d460f0c5889f52a914745b23400c4548804afee8f9c4a56ec3",
     "label": "clean", "case": "Fed rate cut Dec 2024"},
    {"cid": "0x735a2a984e13b28aa5e0c540d99fb8d798b2f6825bd0b992679ea779bf0911ca",
     "label": "clean", "case": "Fed rate cut 2025"},

    # NBA Finals — sports, high volume, public outcome
    {"cid": "0x166ef8a14442f6c1dd88c5fda30f3b2a4294f7fead2220351cdb67d909af43a2",
     "label": "clean", "case": "NBA Finals 2024 Winner"},
    {"cid": "0xd1cfd61da9ac8931fc3aa48ad05eb9687e6f925a15502eb23a04e0174d2c9f30",
     "label": "clean", "case": "Celtics win 2025 NBA Finals"},
    {"cid": "0x6edc6c77c16ef3ba1bcd646159f12f8b8a39528e500dcff95b9220ccfbb75141",
     "label": "clean", "case": "OKC Thunder win 2025 NBA Finals"},

    # Champions League — sports, predictable favorites
    {"cid": "0x5350cf22749cb59279c514dbdb70fc470f4fed16ff8635ec715f669906dfbc34",
     "label": "clean", "case": "CL Final 2023 Man City v Inter"},
    {"cid": "0x6c76c5dcf7969b636d59d5e91a1d85d784038503fc3569af08f1d71b2bf155c5",
     "label": "clean", "case": "CL Final 2024 Real Madrid v Dortmund"},
    {"cid": "0x613a94c60d78d182169b047c9590955373a84ca76c27a94e0c39eb2471879341",
     "label": "clean", "case": "CL Final 2025 PSG v Inter"},

    # Bitcoin weekly price — data-driven, broad participation
    {"cid": "0xa8019c363268cbe8864c032b133631c5fd2e0bf437135cdbff7c97903532a154",
     "label": "clean", "case": "Bitcoin above $100k Dec 13 2024"},
    {"cid": "0x3d68c893b43c24d3d4e5f0b8f64204d94a37745589a41544555a74412936153c",
     "label": "clean", "case": "Bitcoin above $90k Nov 22 2024"},
    {"cid": "0xba54f4ceb28ea56c4fd03f520a365923739bb7addd7b89c2f2dab638d359415f",
     "label": "clean", "case": "Bitcoin above $100k Nov 29 2024"},
    {"cid": "0x6767550e239bc46ea3a955881a7e81b92eb4bf8b0278f216a8aea882eb47c084",
     "label": "clean", "case": "Bitcoin above $94k May 2 2025"},
]


async def fetch_all_trades(cid: str) -> list[Trade]:
    """Fetch ALL trades for a market (no time window). Stops on 400 (API offset cap)."""
    trades = []
    async with httpx.AsyncClient(timeout=30) as client:
        offset = 0
        while True:
            resp = await client.get(DATA_URL, params={
                "market": cid, "limit": 500, "offset": offset,
            })
            if resp.status_code == 400:
                break  # API offset cap reached
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            for t in batch:
                trades.append(Trade(
                    transaction_hash=t["transactionHash"],
                    condition_id=cid,
                    proxy_wallet=t.get("proxyWallet", ""),
                    side=t.get("side", ""),
                    size=float(t.get("size", 0) or 0),
                    price=float(t.get("price", 0) or 0),
                    outcome=t.get("outcome", ""),
                    timestamp=str(t.get("timestamp", "")),
                    fetched_at="",
                ))
            if len(batch) < 500:
                break
            offset += 500
    return trades


async def fetch_market_meta(cid: str) -> dict:
    """Fetch market metadata from CLOB API."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{CLOB_URL}/{cid}")
        resp.raise_for_status()
        return resp.json()


def make_market(cid: str, meta: dict, trades: list[Trade]) -> Market:
    """Build a Market object. Use median trade price instead of resolved outcome price."""
    prices = [t.price for t in trades if 0 < t.price < 1]
    synthetic_price = median(prices) if prices else 0.5

    liq = float(meta.get("minimum_order_size", 0) or 0)
    # Estimate liquidity from trade volume if not available
    total_vol = sum(t.size for t in trades)
    liquidity = max(total_vol * 0.1, 10000) if liq == 0 else liq * 10000

    return Market(
        condition_id=cid,
        title=meta.get("question", "?"),
        volume=total_vol,
        liquidity=liquidity,
        end_date=meta.get("end_date_iso", ""),
        outcome_prices=[synthetic_price],
        fetched_at="",
    )


def window_trades(trades: list[Trade], window_hours: int = 1) -> list[tuple[list[Trade], float]]:
    """Slide a window over trades and return (window_trades, median_price) for each step.
    Returns the window with the highest flag rate to simulate 'worst hour'."""
    if not trades:
        return []

    sorted_trades = sorted(trades, key=lambda t: float(t.timestamp))
    window_sec = window_hours * 3600

    best_window = None
    best_count = 0

    # Slide in 30-min steps
    step = 1800
    ts_min = float(sorted_trades[0].timestamp)
    ts_max = float(sorted_trades[-1].timestamp)

    t = ts_min
    while t <= ts_max:
        w = [tr for tr in sorted_trades if t <= float(tr.timestamp) < t + window_sec]
        if len(w) >= 5 and len(w) > best_count:
            best_count = len(w)
            best_window = w
        t += step

    return best_window or sorted_trades[-50:]  # fallback to last 50 trades


async def run_validation():
    print("=" * 90)
    print("POLYMARKET ANOMALY DETECTOR — VALIDATION HARNESS")
    print("=" * 90)

    results = []
    total = len(LABELED_MARKETS)

    for i, entry in enumerate(LABELED_MARKETS):
        cid = entry["cid"]
        label = entry["label"]
        case = entry["case"]
        print(f"\n[{i+1}/{total}] {case} ({label})")
        print(f"  Fetching trades for {cid[:20]}...", end=" ", flush=True)

        start = time.time()
        try:
            trades = await fetch_all_trades(cid)
        except Exception as e:
            print(f"FAILED: {e}")
            results.append({"case": case, "label": label, "error": str(e)})
            continue
        elapsed_trades = time.time() - start

        if not trades:
            print(f"0 trades ({elapsed_trades:.1f}s)")
            results.append({"case": case, "label": label, "trades": 0, "flagged": 0, "signals": {}})
            continue

        print(f"{len(trades)} trades ({elapsed_trades:.1f}s)")

        try:
            meta = await fetch_market_meta(cid)
        except Exception:
            meta = {}

        market = make_market(cid, meta, trades)
        print(f"  Market: {market.title[:70]}")
        print(f"  Synthetic price: {market.outcome_prices[0]:.3f}, liquidity: {market.liquidity:.0f}")

        # Find the densest 1-hour window (simulates live polling)
        window = window_trades(trades, window_hours=1)
        w_prices = [t.price for t in window if 0 < t.price < 1]
        w_median = median(w_prices) if w_prices else market.outcome_prices[0]
        market.outcome_prices = [w_median]

        # Run signal engine on the window only
        trades_by_market = {cid: window}
        markets_by_id = {cid: market}
        signal_map = compute_signals(trades_by_market, markets_by_id)

        # Count flagged trades (>=2 signals)
        flagged = {tx: sigs for tx, sigs in signal_map.items() if len(sigs) >= 2}
        signal_counts = defaultdict(int)
        for sigs in flagged.values():
            for s in sigs:
                signal_counts[s] += 1

        max_signals = max((len(s) for s in flagged.values()), default=0)

        # ── Additional metrics ───────────────────────────────────────────
        # Wallet concentration: fewer unique wallets = more suspicious
        wallets = set(t.proxy_wallet for t in window if t.proxy_wallet)
        wallet_ratio = len(wallets) / len(window) if window else 1.0

        # Repeat wallet volume: what % of total $ comes from repeat wallets
        from collections import Counter
        wallet_counts = Counter(t.proxy_wallet for t in window)
        repeat_wallets = {w for w, c in wallet_counts.items() if c >= 3}
        repeat_vol = sum(t.size for t in window if t.proxy_wallet in repeat_wallets)
        total_vol = sum(t.size for t in window) or 1
        repeat_vol_pct = repeat_vol / total_vol

        # Contrarian + large combo: trades that are BOTH contrarian AND large_position
        contrarian_large = sum(1 for sigs in flagged.values()
                               if "contrarian_trade" in sigs and "large_position" in sigs)

        # High-signal density: trades with >=3 signals (strong anomalies)
        high_signal = sum(1 for sigs in flagged.values() if len(sigs) >= 3)
        high_signal_rate = high_signal / len(window) if window else 0

        # Composite score (0-100): weighted combination
        score = (
            min(r_flag_rate := (len(flagged) / len(window) if window else 0), 1.0) * 25 +  # flag rate
            min(high_signal_rate * 5, 1.0) * 25 +  # high-signal density
            min(repeat_vol_pct * 2, 1.0) * 25 +  # repeat wallet volume
            (1 - wallet_ratio) * 25  # wallet concentration (lower = more suspicious)
        )

        print(f"  Window: {len(window)} trades, median price: {w_median:.3f}")
        print(f"  Trades with >=1 signal: {len(signal_map)}, flagged (>=2): {len(flagged)}, max signals: {max_signals}")
        print(f"  Wallets: {len(wallets)} unique ({wallet_ratio:.2f} ratio), repeat wallet vol: {repeat_vol_pct:.1%}")
        print(f"  Contrarian+large: {contrarian_large}, high-signal (>=3): {high_signal}, composite: {score:.1f}")
        if signal_counts:
            print(f"  Signal breakdown: {dict(signal_counts)}")

        results.append({
            "case": case,
            "label": label,
            "trades": len(trades),
            "window": len(window),
            "flagged": len(flagged),
            "max_signals": max_signals,
            "signals": dict(signal_counts),
            "flag_rate": len(flagged) / len(window) if window else 0,
            "wallet_ratio": wallet_ratio,
            "repeat_vol_pct": repeat_vol_pct,
            "contrarian_large": contrarian_large,
            "high_signal": high_signal,
            "high_signal_rate": high_signal_rate,
            "composite": score,
        })

    # ── Scoring ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 90)
    print("RESULTS — FLAG RATE ONLY")
    print("=" * 90)

    for rate_thresh in [0.20, 0.30, 0.40, 0.50]:
        tp = fp = tn = fn = 0
        for r in results:
            if "error" in r or r.get("trades", 0) == 0:
                continue
            detected = r.get("flag_rate", 0) >= rate_thresh
            if r["label"] == "insider":
                if detected: tp += 1
                else: fn += 1
            else:
                if detected: fp += 1
                else: tn += 1
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
        print(f"\n  Flag-rate >= {rate_thresh:.0%}: TP={tp} FP={fp} TN={tn} FN={fn} | P={precision:.2f} R={recall:.2f} F1={f1:.2f}")

    # ── Composite score thresholds ───────────────────────────────────────
    print("\n" + "=" * 90)
    print("RESULTS — COMPOSITE SCORE")
    print("=" * 90)
    print("  Composite = 25% flag_rate + 25% high_signal_density + 25% repeat_wallet_vol + 25% wallet_concentration")

    for thresh in [20, 30, 40, 50]:
        tp = fp = tn = fn = 0
        for r in results:
            if "error" in r or r.get("trades", 0) == 0:
                continue
            detected = r.get("composite", 0) >= thresh
            if r["label"] == "insider":
                if detected: tp += 1
                else: fn += 1
            else:
                if detected: fp += 1
                else: tn += 1
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
        print(f"\n  Composite >= {thresh}: TP={tp} FP={fp} TN={tn} FN={fn} | P={precision:.2f} R={recall:.2f} F1={f1:.2f}")

    # ── Combined metrics ──────────────────────────────────────────────────
    print("\n" + "=" * 90)
    print("RESULTS — COMBINED METRICS")
    print("=" * 90)

    # Best combo: flag_rate >= X AND high_signal_rate >= Y
    combos = [
        ("flag_rate>=20% AND high_signal>=1%", lambda r: r.get("flag_rate",0)>=0.20 and r.get("high_signal_rate",0)>=0.01),
        ("flag_rate>=20% AND wallet_ratio<0.75", lambda r: r.get("flag_rate",0)>=0.20 and r.get("wallet_ratio",1)<0.75),
        ("flag_rate>=20% AND repeat_vol>=5%", lambda r: r.get("flag_rate",0)>=0.20 and r.get("repeat_vol_pct",0)>=0.05),
        ("flag_rate>=15% AND high_signal>=3 AND wallet_ratio<0.80", lambda r: r.get("flag_rate",0)>=0.15 and r.get("high_signal",0)>=3 and r.get("wallet_ratio",1)<0.80),
        ("high_signal>=3 AND wallet_ratio<0.70", lambda r: r.get("high_signal",0)>=3 and r.get("wallet_ratio",1)<0.70),
        ("flag_rate>=20% AND contrarian_large>=1", lambda r: r.get("flag_rate",0)>=0.20 and r.get("contrarian_large",0)>=1),
    ]
    for name, pred in combos:
        tp = fp = tn = fn = 0
        for r in results:
            if "error" in r or r.get("trades", 0) == 0:
                continue
            detected = pred(r)
            if r["label"] == "insider":
                if detected: tp += 1
                else: fn += 1
            else:
                if detected: fp += 1
                else: tn += 1
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
        print(f"\n  {name}")
        print(f"    TP={tp} FP={fp} TN={tn} FN={fn} | P={precision:.2f} R={recall:.2f} F1={f1:.2f}")

    # ── Per-market detail ────────────────────────────────────────────────────
    print("\n" + "=" * 90)
    print("PER-MARKET DETAIL (sorted by composite score)")
    print("=" * 90)
    print(f"{'Label':<8} {'Score':>5} {'Rate':>6} {'WalR':>5} {'RptV':>5} {'Hi3':>4} {'C+L':>4}  Case")
    print("-" * 100)
    for r in sorted(results, key=lambda x: x.get("composite", 0), reverse=True):
        if "error" in r:
            print(f"{'ERROR':<8} {'':>5} {'':>6} {'':>5} {'':>5} {'':>4} {'':>4}  {r['case']}")
            continue
        rate = f"{r.get('flag_rate',0):.0%}"
        wr = f"{r.get('wallet_ratio',0):.2f}"
        rv = f"{r.get('repeat_vol_pct',0):.0%}"
        hs = f"{r.get('high_signal',0)}"
        cl = f"{r.get('contrarian_large',0)}"
        sc = f"{r.get('composite',0):.0f}"
        print(f"{r['label']:<8} {sc:>5} {rate:>6} {wr:>5} {rv:>5} {hs:>4} {cl:>4}  {r['case']}")


if __name__ == "__main__":
    import signals as _sig

    # Sweep VOL_BASELINE values
    if "--sweep" in sys.argv:
        async def sweep():
            print("=" * 90)
            print("VOLATILITY BASELINE SWEEP")
            print("=" * 90)

            # Pre-fetch all data once
            all_data = []
            for entry in LABELED_MARKETS:
                cid = entry["cid"]
                print(f"  Fetching {entry['case'][:40]}...", end=" ", flush=True)
                try:
                    trades = await fetch_all_trades(cid)
                    meta = await fetch_market_meta(cid) if trades else {}
                except Exception:
                    trades, meta = [], {}
                print(f"{len(trades)} trades")
                all_data.append((entry, trades, meta))

            print(f"\n{'baseline':>8} {'thresh':>6} {'TP':>3} {'FP':>3} {'TN':>3} {'FN':>3} {'P':>5} {'R':>5} {'F1':>5}")
            print("-" * 50)
            best_f1, best_cfg = 0, ""
            for baseline in [0.05, 0.07, 0.08, 0.10, 0.12, 0.15]:
                _sig.VOL_BASELINE = baseline
                # Pre-compute flag rates for this baseline
                rates = []
                for entry, trades, meta in all_data:
                    if not trades:
                        rates.append((entry, 0))
                        continue
                    market = make_market(entry["cid"], meta, trades)
                    window = window_trades(trades, window_hours=1)
                    w_prices = [t.price for t in window if 0 < t.price < 1]
                    market.outcome_prices = [median(w_prices) if w_prices else 0.5]
                    sig_map = compute_signals({entry["cid"]: window}, {entry["cid"]: market})
                    flagged = sum(1 for s in sig_map.values() if len(s) >= 2)
                    rates.append((entry, flagged / len(window) if window else 0))

                for thresh in [0.15, 0.20, 0.25, 0.30]:
                    tp = fp = tn = fn = 0
                    for entry, rate in rates:
                        detected = rate >= thresh
                        if entry["label"] == "insider":
                            if detected: tp += 1
                            else: fn += 1
                        else:
                            if detected: fp += 1
                            else: tn += 1
                    p = tp / (tp + fp) if (tp + fp) > 0 else 0
                    r = tp / (tp + fn) if (tp + fn) > 0 else 0
                    f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0
                    marker = " <<<" if f1 > best_f1 else ""
                    if f1 > best_f1:
                        best_f1 = f1
                        best_cfg = f"baseline={baseline}, thresh={thresh}"
                    print(f"{baseline:>8.2f} {thresh:>6.0%} {tp:>3} {fp:>3} {tn:>3} {fn:>3} {p:>5.2f} {r:>5.2f} {f1:>5.2f}{marker}")

            print(f"\nBest: {best_cfg} → F1={best_f1:.2f}")

        asyncio.run(sweep())
    else:
        asyncio.run(run_validation())
