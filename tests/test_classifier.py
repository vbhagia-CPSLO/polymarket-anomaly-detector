import json
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

import pytest

from classifier import classify, filter_relevant_markets
from models import Flag, Market, Trade

NOW = datetime.now(timezone.utc).isoformat()

MARKET = Market(
    condition_id="0xabc",
    title="Will X happen?",
    volume=1000.0,
    liquidity=500.0,
    end_date="2026-12-01",
    outcome_prices=[0.6, 0.4],
    fetched_at=NOW,
)

TRADE = Trade(
    transaction_hash="0xtx1",
    condition_id="0xabc",
    proxy_wallet="0xwallet",
    side="BUY",
    size=600.0,
    price=0.2,
    outcome="Yes",
    timestamp="1777700000",
    fetched_at=NOW,
)

SIGNALS = ["large_position", "contrarian_trade"]


def _mock_ollama(content: str):
    resp = MagicMock()
    resp.json.return_value = {"message": {"content": content}}
    resp.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=resp)
    return mock_client


@pytest.mark.asyncio
async def test_classify_valid_response():
    payload = json.dumps({
        "anomaly_type": "informed_trading",
        "confidence": "high",
        "reasoning": "Large contrarian buy before price move.",
    })
    with patch("classifier.httpx.AsyncClient") as cls:
        cls.return_value.__aenter__.return_value = _mock_ollama(payload)
        flag = await classify(TRADE, MARKET, SIGNALS)

    assert isinstance(flag, Flag)
    assert flag.anomaly_type == "informed_trading"
    assert flag.confidence == "high"
    assert flag.signals_triggered == SIGNALS
    assert flag.transaction_hash == "0xtx1"


@pytest.mark.asyncio
async def test_classify_invalid_anomaly_type_returns_unclassified():
    payload = json.dumps({
        "anomaly_type": "totally_made_up",
        "confidence": "high",
        "reasoning": "Something.",
    })
    with patch("classifier.httpx.AsyncClient") as cls:
        cls.return_value.__aenter__.return_value = _mock_ollama(payload)
        flag = await classify(TRADE, MARKET, SIGNALS)

    assert flag.anomaly_type == "unclassified"
    assert flag.confidence == "low"


@pytest.mark.asyncio
async def test_classify_malformed_json_returns_unclassified():
    with patch("classifier.httpx.AsyncClient") as cls:
        cls.return_value.__aenter__.return_value = _mock_ollama("not json at all")
        flag = await classify(TRADE, MARKET, SIGNALS)

    assert flag.anomaly_type == "unclassified"
    assert "parse error" in flag.reasoning


@pytest.mark.asyncio
async def test_classify_network_error_returns_unclassified():
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=Exception("connection refused"))
    with patch("classifier.httpx.AsyncClient") as cls:
        cls.return_value.__aenter__.return_value = mock_client
        flag = await classify(TRADE, MARKET, SIGNALS)

    assert flag.anomaly_type == "unclassified"
    assert "connection refused" in flag.reasoning


@pytest.mark.asyncio
async def test_classify_no_outcome_prices():
    market_no_prices = Market(**{**MARKET.__dict__, "outcome_prices": []})
    payload = json.dumps({
        "anomaly_type": "normal_large_trade",
        "confidence": "medium",
        "reasoning": "Nothing suspicious.",
    })
    with patch("classifier.httpx.AsyncClient") as cls:
        cls.return_value.__aenter__.return_value = _mock_ollama(payload)
        flag = await classify(TRADE, market_no_prices, SIGNALS)

    assert flag.anomaly_type == "normal_large_trade"


# --- filter_relevant_markets tests ---

def _make_market(condition_id, title="Test"):
    return Market(condition_id=condition_id, title=title, volume=0, liquidity=0,
                  end_date="", outcome_prices=[], fetched_at=NOW)


def _mock_response(data):
    resp = MagicMock()
    resp.json.return_value = {"message": {"content": json.dumps(data)}}
    resp.raise_for_status = MagicMock()
    return resp


@pytest.mark.asyncio
async def test_filter_returns_relevant_only():
    markets = [_make_market("0x1"), _make_market("0x2"), _make_market("0x3")]
    response = [
        {"condition_id": "0x1", "relevant": True},
        {"condition_id": "0x2", "relevant": False},
        {"condition_id": "0x3", "relevant": True},
    ]
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_response(response))
    with patch("classifier.httpx.AsyncClient") as cls:
        cls.return_value.__aenter__.return_value = mock_client
        result = await filter_relevant_markets(markets)

    assert {m.condition_id for m in result} == {"0x1", "0x3"}


@pytest.mark.asyncio
async def test_filter_retries_dropped_markets():
    markets = [_make_market("0x1"), _make_market("0x2")]
    # First pass drops 0x2; retry returns it as relevant
    first = [{"condition_id": "0x1", "relevant": True}]
    second = [{"condition_id": "0x2", "relevant": True}]
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=[_mock_response(first), _mock_response(second)])
    with patch("classifier.httpx.AsyncClient") as cls:
        cls.return_value.__aenter__.return_value = mock_client
        result = await filter_relevant_markets(markets)

    assert {m.condition_id for m in result} == {"0x1", "0x2"}


@pytest.mark.asyncio
async def test_filter_fails_open_for_persistent_drops():
    markets = [_make_market("0x1"), _make_market("0x2")]
    # Both passes drop 0x2 — should fail open and include it
    first = [{"condition_id": "0x1", "relevant": True}]
    second = []  # still drops 0x2
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=[_mock_response(first), _mock_response(second)])
    with patch("classifier.httpx.AsyncClient") as cls:
        cls.return_value.__aenter__.return_value = mock_client
        result = await filter_relevant_markets(markets)

    assert {m.condition_id for m in result} == {"0x1", "0x2"}


@pytest.mark.asyncio
async def test_filter_discards_unknown_condition_ids():
    markets = [_make_market("0x1")]
    response = [{"condition_id": "0xUNKNOWN", "relevant": True}]
    mock_client = AsyncMock()
    # retry will also return nothing useful
    mock_client.post = AsyncMock(side_effect=[_mock_response(response), _mock_response([])])
    with patch("classifier.httpx.AsyncClient") as cls:
        cls.return_value.__aenter__.return_value = mock_client
        result = await filter_relevant_markets(markets)

    # 0x1 was never returned → fails open → included
    assert {m.condition_id for m in result} == {"0x1"}


@pytest.mark.asyncio
async def test_filter_fails_open_on_complete_ollama_failure():
    markets = [_make_market("0x1"), _make_market("0x2")]
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=Exception("connection refused"))
    with patch("classifier.httpx.AsyncClient") as cls:
        cls.return_value.__aenter__.return_value = mock_client
        result = await filter_relevant_markets(markets)

    assert result == markets


@pytest.mark.asyncio
async def test_filter_fails_open_on_bad_json():
    markets = [_make_market("0x1")]
    resp = MagicMock()
    resp.json.return_value = {"message": {"content": "not json"}}
    resp.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=resp)
    with patch("classifier.httpx.AsyncClient") as cls:
        cls.return_value.__aenter__.return_value = mock_client
        result = await filter_relevant_markets(markets)

    assert result == markets
