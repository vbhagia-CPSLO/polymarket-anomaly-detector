import json
import logging
from datetime import datetime, timezone

import httpx

import config
from models import Flag, Market, Trade

logger = logging.getLogger(__name__)

_VALID_ANOMALY_TYPES = {"informed_trading", "wash_trading", "liquidity_shock", "normal_large_trade"}
_VALID_CONFIDENCE = {"low", "medium", "high"}

_RELEVANCE_PROMPT = """\
You are a strict classifier for prediction market questions. For each market, classify whether it is DIRECTLY about politics or economics. Be conservative — when in doubt, mark false.

RELEVANT (mark true):
- Elections, government policy, legislation, political figures, political parties
- Geopolitical events: war, diplomacy, international relations, regime change, military action, sanctions, treaties
- Macroeconomics: interest rates, inflation, GDP, employment, central bank decisions, tariffs, trade policy
- Commodities ONLY when tied to policy (e.g. "Will oil sanctions be lifted?")

NOT RELEVANT (mark false):
- Sports: ANY sports market including esports, NBA, NFL, MLB, soccer, MMA, League of Legends, Counter-Strike, etc.
- Cryptocurrency: Bitcoin, Ethereum, token prices, DeFi, NFTs, crypto regulation
- Entertainment: movies, TV shows, music, awards, celebrities, social media
- Weather, science, technology products, company earnings, stock prices
- Gaming, streaming, YouTube, TikTok
- Anything where the core question is about a game, match, or competition outcome

RULES:
- Return ONLY a valid JSON array, no preamble, no explanation, no markdown, no code fences
- You MUST return one entry per market provided — do not skip any
- If a market could be interpreted as both political and sports/entertainment, mark false

OUTPUT FORMAT:
[{{"condition_id": "0x...", "relevant": true}}, ...]

Now classify the following markets:
{markets_json}"""

_PROMPT_TEMPLATE = """\
Market: {title}
Trade: {side} {size} shares of "{outcome}" at price {price}
Current market price: {current_price} (from Gamma outcomePrices)
Signals triggered: {signals}

Classify this trade. Respond with JSON only:
{{
  "anomaly_type": "informed_trading|wash_trading|liquidity_shock|normal_large_trade",
  "confidence": "low|medium|high",
  "reasoning": "<one sentence>"
}}"""


async def _classify_batch(markets: list[Market]) -> tuple[set[str], set[str]]:
    """Single LLM batch call. Returns (relevant_ids, returned_ids)."""
    payload = [{"condition_id": m.condition_id, "question": m.title} for m in markets]
    prompt = _RELEVANCE_PROMPT.format(markets_json=json.dumps(payload, indent=2))
    valid_ids = {m.condition_id for m in markets}

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{config.OLLAMA_ENDPOINT}/api/chat",
                json={
                    "model": config.OLLAMA_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {"temperature": 0},
                },
            )
            resp.raise_for_status()
            raw = resp.json()["message"]["content"].strip()
    except Exception as e:
        logger.warning("_classify_batch ollama call failed: %s", e)
        return set(), set()  # caller handles failure

    try:
        classifications = json.loads(raw)
    except Exception as e:
        logger.warning("_classify_batch parse failed: %s | raw: %s", e, raw)
        return set(), set()

    relevant_ids: set[str] = set()
    returned_ids: set[str] = set()
    for entry in classifications:
        if not isinstance(entry, dict):
            continue
        cid = entry.get("condition_id")
        if cid not in valid_ids:
            logger.warning("_classify_batch: unknown condition_id %r — discarding", cid)
            continue
        returned_ids.add(cid)
        if entry.get("relevant") is True:
            relevant_ids.add(cid)

    return relevant_ids, returned_ids


async def filter_relevant_markets(markets: list[Market]) -> list[Market]:
    """Batch LLM call to filter markets to politics/economics only.
    Retries once for any markets silently dropped in the first pass.
    Fails open (treats as relevant) for any still missing after retry."""
    if not markets:
        return []

    markets_by_id = {m.condition_id: m for m in markets}
    valid_ids = set(markets_by_id)

    # First pass
    relevant_ids, returned_ids = await _classify_batch(markets)

    if not returned_ids:
        # Complete failure — fail open
        logger.warning("filter_relevant_markets: first pass failed entirely, treating all as relevant")
        return markets

    missing_ids = valid_ids - returned_ids
    if missing_ids:
        logger.warning("filter_relevant_markets: first pass dropped %d markets, retrying", len(missing_ids))
        missing_markets = [markets_by_id[cid] for cid in missing_ids]
        retry_relevant, retry_returned = await _classify_batch(missing_markets)
        relevant_ids |= retry_relevant

        still_missing = missing_ids - retry_returned
        if still_missing:
            logger.warning(
                "filter_relevant_markets: %d markets still dropped after retry — treating as relevant: %s",
                len(still_missing),
                [markets_by_id[cid].title for cid in still_missing],
            )
            relevant_ids |= still_missing  # fail open for persistent drops

    logger.info("filter_relevant_markets: %d/%d markets relevant", len(relevant_ids), len(markets))
    return [m for m in markets if m.condition_id in relevant_ids]


def _unclassified(trade: Trade, error: str) -> Flag:
    return Flag(
        transaction_hash=trade.transaction_hash,
        condition_id=trade.condition_id,
        signals_triggered=[],
        anomaly_type="unclassified",
        confidence="low",
        reasoning=error,
        flagged_at=datetime.now(timezone.utc).isoformat(),
    )


async def classify(trade: Trade, market: Market, signals: list[str]) -> Flag:
    current_price = market.outcome_prices[0] if market.outcome_prices else "unknown"
    prompt = _PROMPT_TEMPLATE.format(
        title=market.title,
        side=trade.side,
        size=trade.size,
        outcome=trade.outcome,
        price=trade.price,
        current_price=current_price,
        signals=", ".join(signals),
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{config.OLLAMA_ENDPOINT}/api/chat",
                json={
                    "model": config.OLLAMA_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {"temperature": 0},
                },
            )
            resp.raise_for_status()
            raw = resp.json()["message"]["content"]
    except Exception as e:
        logger.warning("classify(%s) ollama call failed: %s", trade.transaction_hash, e)
        return _unclassified(trade, str(e))

    try:
        # Strip markdown code fences if the model wraps its output
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.strip()

        data = json.loads(cleaned)
        anomaly_type = data.get("anomaly_type", "")
        confidence = data.get("confidence", "")
        reasoning = data.get("reasoning", "")

        if not anomaly_type:
            raise ValueError("missing anomaly_type")
        if not confidence:
            raise ValueError("missing confidence")
        if anomaly_type not in _VALID_ANOMALY_TYPES:
            raise ValueError(f"invalid anomaly_type: {anomaly_type!r}")
        if confidence not in _VALID_CONFIDENCE:
            raise ValueError(f"invalid confidence: {confidence!r}")

        return Flag(
            transaction_hash=trade.transaction_hash,
            condition_id=trade.condition_id,
            signals_triggered=signals,
            anomaly_type=anomaly_type,
            confidence=confidence,
            reasoning=str(reasoning),
            flagged_at=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as e:
        logger.warning("classify(%s) parse failed: %s | raw: %s", trade.transaction_hash, e, raw)
        return _unclassified(trade, f"parse error: {e} | raw: {raw}")
