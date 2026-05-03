# MarketSentinel

## Inspiration

A US Army Special Forces soldier was indicted last week for using advance knowledge of a classified military operation to make $440,000 on Polymarket. An Israeli Air Force reservist was indicted for betting on the timing of his own unit's airstrikes. Fifty newly created accounts placed bets on a US-Iran ceasefire minutes before Trump announced it on Truth Social. Congressional representatives have sent letters to the SEC and CFTC. The Norwegian Nobel Institute is investigating whether prize information was leaked to a trader who made $70,000 on María Corina Machado.

Prediction markets now move hundreds of millions of dollars on geopolitical events, and they have zero equivalent of the surveillance infrastructure that traditional financial markets have had for decades. Anyone with advance knowledge and a Polymarket account can profit with near-zero detection risk. We built MarketSentinel to change that.

---

## What it does

MarketSentinel polls Polymarket every hour, monitors active political and economic markets, and flags suspicious trades in real time using a five-signal anomaly engine backed by a local LLM.

Each cycle it: fetches the top 100 markets by 24-hour volume, uses qwen2.5:14b running locally via Ollama to filter down to politics and economics markets only (batched in groups of 25), fetches all trades from the last hour for those markets, and runs five anomaly signals — `large_position` (trade ≥ $5,000), `contrarian_trade` (execution price deviates >15pp from consensus, volatility-adjusted), `rapid_repeat_trades` (same wallet ≥3 trades in the window), `size_outlier` (trade > mean + 2σ for that market), and `volume_price_shift` (price moved >10pp on <10% of market liquidity). Trades tripping ≥2 signals are classified by the LLM as `informed_trading`, `wash_trading`, `liquidity_shock`, or `normal_large_trade`, with a confidence level and one-sentence reasoning. Everything persists to SQLite.

In the current live cycle, the top flags are on Iran airspace closure and Strait of Hormuz markets — exactly the kind of geopolitical questions where information asymmetry is highest.

---

## How we built it

Entirely with Kiro CLI in a single 13-hour session.

Vibe coding drove the architecture. We described the system — polling loop, LLM filter, signal engine, classifier, DB persistence — and Kiro generated the initial six-file structure in one pass. The most impressive single generation was `compute_signals`: five distinct signal implementations with correct statistical logic (mean/stdev for size outlier, wallet counting for rapid repeat, price deviation for contrarian), all async-safe, in one shot.

For the validation harness we switched to spec-driven development. We wrote out the full spec — 29 labeled markets sourced from criminal indictments, fetch all historical trades via the Polymarket data API, run signals on the densest 1-hour window, score at multiple thresholds, output a confusion matrix — and Kiro implemented it from that spec in one pass including the sliding window logic and multi-threshold scoring. Spec-driven was noticeably better than vibe coding here because the output format needed to be precise.

Steering docs kept every generation minimal and on-style. A steering doc specifying "minimal code only, match existing style, no defensive abstractions" prevented Kiro from adding unnecessary layers throughout the session.

The task list feature kept multi-step work organized — especially when the first approach to the data API failed (the filter parameter is `market=`, not `conditionId=`) and we needed to diagnose and pivot without losing track of where we were.

---

## Challenges we ran into

**The data API doesn't filter server-side by condition ID.** We discovered mid-validation that `conditionId=` is silently ignored — the API returns a global trade feed regardless. The correct parameter is `market=`. The existing production fetcher worked by coincidence (it filters client-side and the live feed happens to contain recent trades for active markets). Finding this cost us time and required a full re-run of the validation.

**The 7b model conflates signal names with classification categories.** When we tested with qwen2.5:7b, it returned `"anomaly_type": "rapid_repeat_trades"` — a signal name, not a valid classification. The prompt explicitly distinguishes the two, but the smaller model ignores it. Switching to 14b fixed this entirely.

**Signal deduplication.** Polymarket returns separate trade records per outcome for the same transaction hash. The signal engine was appending signals once per record, so a trade with two outcome records got every signal doubled — a 3-signal trade showed `signal_count: 6`. Fixed with a single `dict.fromkeys` deduplication at the return of `compute_signals`.

**Volatility creates false positives.** Bitcoin and NBA futures markets naturally trigger `contrarian_trade` and `price_impact` because their prices swing widely. We implemented volatility-adjusted thresholds that scale those signals by per-market price stdev, which compressed the noisy markets' flag rates. But the best overall result (F1=0.71, recall=86%) still came from the simpler 20% flag-rate threshold — the volatility adjustment improved precision by 1% at the cost of 7% recall, which is the wrong tradeoff for a detection system.

---

## Accomplishments that we're proud of

**86% recall on a labeled dataset built from criminal indictments.** We sourced 14 positive-label markets directly from documented cases — Van Dyke's Maduro trades, the IDF reservist's Iran strike bets, the Bubblemaps-traced $494k Iran cluster, the ceasefire accounts, the Nobel Prize leak. The signal engine catches 12 of 14 with a 0.71 F1 score. This isn't a toy benchmark — these are real trades that real people were indicted for.

**Fully local, zero external dependencies beyond Polymarket's public API.** No OpenAI, no cloud inference, no paid services. The LLM runs on a Mac Studio in the same room. The entire system costs nothing to operate.

**The LLM market filter works.** We ran a live test of the 14b model against 25 labeled markets (politics/economics vs. sports/entertainment) and got 25/25 correct in 26.5 seconds — well within the 60-second timeout. The 7b model failed this same test.

---

## What we learned

The hardest part of anomaly detection isn't the signals — it's the ground truth. Building a labeled dataset required going through criminal indictments, congressional letters, blockchain analysis reports, and news coverage to find markets where we actually know what happened. Most anomaly detection work skips this and evaluates on synthetic data. Doing it on real confirmed cases is harder but the results mean something.

We also learned that recall matters more than precision for this use case. A system that catches 86% of insider trading with some false positives is far more useful than one that's precise but misses cases. The false positives we do get are explainable — high-volatility markets where market-maker activity looks like insider clustering — and a human reviewer can dismiss them quickly.

---

## What's next for MarketSentinel

**Wallet graph analysis.** The most powerful signal we don't have yet is wallet clustering — detecting when multiple "new" accounts are funded from the same source address, as happened with the 50 ceasefire accounts. This requires on-chain data beyond what Polymarket's API exposes, but it's the difference between flagging suspicious trades and tracing the network behind them.

**Resolution-time correlation.** We currently run signals on a 1-hour window. A stronger approach would correlate flagged trades with market resolution — did the flagged wallet win? A wallet that consistently bets correctly on low-probability events right before they happen is a much stronger signal than any single trade.

**Regulatory reporting format.** The output right now is a SQLite database and a demo dashboard. The natural next step is a structured report format that could be submitted to the CFTC or shared with Polymarket's compliance team — something that maps each flag to the specific signals, the wallet address, the profit estimate, and the relevant news event.

**Broader market coverage.** We currently filter to politics and economics. The same signals apply to any market where information asymmetry exists — sports injury news, earnings announcements, FDA decisions. The LLM filter is already parameterized; expanding coverage is a config change.
