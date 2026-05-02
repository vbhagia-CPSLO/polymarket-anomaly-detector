from dataclasses import dataclass


@dataclass
class Market:
    condition_id: str
    title: str
    volume: float
    liquidity: float
    end_date: str
    outcome_prices: list[float]  # from Gamma outcomePrices; used as current_price
    fetched_at: str


@dataclass
class Trade:
    transaction_hash: str
    condition_id: str
    proxy_wallet: str
    side: str
    size: float
    price: float
    outcome: str
    timestamp: str
    fetched_at: str


@dataclass
class Flag:
    transaction_hash: str
    condition_id: str
    signals_triggered: list[str]  # stored as JSON string in DB
    anomaly_type: str              # informed_trading | wash_trading | liquidity_shock | normal_large_trade | unclassified
    confidence: str                # low | medium | high
    reasoning: str
    flagged_at: str
