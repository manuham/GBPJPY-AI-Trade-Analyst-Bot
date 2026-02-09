from __future__ import annotations

from typing import Optional
from pydantic import BaseModel


class OHLCBar(BaseModel):
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class MarketData(BaseModel):
    symbol: str = "GBPJPY"
    session: str = ""
    timestamp: str = ""
    bid: float = 0.0
    ask: float = 0.0
    spread_pips: float = 0.0
    atr_h1: float = 0.0
    atr_m15: float = 0.0
    atr_m5: float = 0.0
    daily_high: float = 0.0
    daily_low: float = 0.0
    daily_range_pips: float = 0.0
    account_balance: float = 0.0
    ohlc_h1: list[OHLCBar] = []
    ohlc_m15: list[OHLCBar] = []
    ohlc_m5: list[OHLCBar] = []


class TradeSetup(BaseModel):
    bias: str
    entry_min: float
    entry_max: float
    stop_loss: float
    sl_pips: float
    tp1: float
    tp1_pips: float
    tp2: float
    tp2_pips: float
    rr_tp1: float
    rr_tp2: float
    confluence: list[str]
    invalidation: str
    timeframe_type: str
    confidence: str
    news_warning: Optional[str] = None
    counter_trend: bool = False
    h1_trend: str = ""
    price_zone: str = ""


class AnalysisResult(BaseModel):
    symbol: str = ""
    digits: int = 3
    setups: list[TradeSetup] = []
    h1_trend_analysis: str = ""
    market_summary: str = ""
    primary_scenario: str = ""
    alternative_scenario: str = ""
    fundamental_bias: str = "neutral"
    upcoming_events: list[str] = []
    raw_response: str = ""


class PendingTrade(BaseModel):
    """A trade approved via Telegram Execute button, waiting for MT5 pickup."""
    id: str
    symbol: str = ""
    bias: str  # "long" or "short"
    entry_min: float
    entry_max: float
    stop_loss: float
    tp1: float
    tp2: float
    sl_pips: float
    confidence: str
    queued_at: float = 0.0  # Unix timestamp — for multi-consumer expiry (60s window)


class TradeExecutionReport(BaseModel):
    """Confirmation from MT5 EA after trade is placed."""
    trade_id: str
    symbol: str = ""
    ticket_tp1: int = 0
    ticket_tp2: int = 0
    lots_tp1: float = 0.0
    lots_tp2: float = 0.0
    actual_entry: float = 0.0
    actual_sl: float = 0.0
    actual_tp1: float = 0.0
    actual_tp2: float = 0.0
    status: str = "executed"  # "executed" or "failed"
    error_message: str = ""
