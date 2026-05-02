import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fetcher import fetch_markets, fetch_trades
from models import Market, Trade

GAMMA_MARKET = {
    "conditionId": "0xabc",
    "question": "Will X happen?",
    "volume": "1000.5",
    "liquidity": "200.0",
    "endDate": "2026-12-01T00:00:00Z",
    "outcomePrices": '["0.6", "0.4"]',
}

DATA_TRADE = {
    "transactionHash": "0xtx1",
    "conditionId": "0xabc",
    "proxyWallet": "0xwallet1",
    "side": "BUY",
    "size": 600.0,
    "price": 0.6,
    "timestamp": 9999999999,  # far future — always within window
    "outcome": "Yes",
}


def _mock_response(data):
    resp = MagicMock()
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


@pytest.mark.asyncio
async def test_fetch_markets_returns_markets():
    with patch("fetcher.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client.get = AsyncMock(return_value=_mock_response([GAMMA_MARKET]))

        result = await fetch_markets(limit=1)

    assert len(result) == 1
    m = result[0]
    assert isinstance(m, Market)
    assert m.condition_id == "0xabc"
    assert m.title == "Will X happen?"
    assert m.volume == 1000.5
    assert m.outcome_prices == [0.6, 0.4]


@pytest.mark.asyncio
async def test_fetch_markets_skips_missing_condition_id():
    bad = {**GAMMA_MARKET, "conditionId": None}
    with patch("fetcher.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client.get = AsyncMock(return_value=_mock_response([bad]))

        result = await fetch_markets()

    assert result == []


@pytest.mark.asyncio
async def test_fetch_markets_returns_empty_on_error():
    with patch("fetcher.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client.get = AsyncMock(side_effect=Exception("network error"))

        result = await fetch_markets()

    assert result == []


@pytest.mark.asyncio
async def test_fetch_trades_returns_trades():
    with patch("fetcher.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client.get = AsyncMock(return_value=_mock_response([DATA_TRADE]))

        result = await fetch_trades("0xabc", since_hours=1)

    assert len(result) == 1
    t = result[0]
    assert isinstance(t, Trade)
    assert t.transaction_hash == "0xtx1"
    assert t.size == 600.0
    assert t.proxy_wallet == "0xwallet1"


@pytest.mark.asyncio
async def test_fetch_trades_filters_old_trades():
    old_trade = {**DATA_TRADE, "timestamp": 1}  # epoch 1 — always outside window
    with patch("fetcher.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client.get = AsyncMock(return_value=_mock_response([old_trade]))

        result = await fetch_trades("0xabc", since_hours=1)

    assert result == []


@pytest.mark.asyncio
async def test_fetch_trades_filters_wrong_condition_id():
    wrong = {**DATA_TRADE, "conditionId": "0xother"}
    with patch("fetcher.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client.get = AsyncMock(return_value=_mock_response([wrong]))

        result = await fetch_trades("0xabc", since_hours=1)

    assert result == []


@pytest.mark.asyncio
async def test_fetch_trades_returns_empty_on_error():
    with patch("fetcher.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client.get = AsyncMock(side_effect=Exception("timeout"))

        result = await fetch_trades("0xabc")

    assert result == []
