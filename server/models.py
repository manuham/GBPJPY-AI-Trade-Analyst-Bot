# v3.0 — Smart entry confirmation + London Kill Zone
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
    # ATR values
    atr_d1: float = 0.0
    atr_h4: float = 0.0
    atr_h1: float = 0.0
    atr_m5: float = 0.0
    # Today's range
    daily_high: float = 0.0
    daily_low: float = 0.0
    daily_range_pips: float = 0.0
    # Previous day levels (ICT key levels)
    prev_day_high: float = 0.0
    prev_day_low: float = 0.0
    prev_day_close: float = 0.0
    # Current week levels
    week_high: float = 0.0
    week_low: float = 0.0
    # Asian session range
    asian_high: float = 0.0
    asian_low: float = 0.0
    # RSI (14-period)
    rsi_d1: float = 0.0
    rsi_h4: float = 0.0
    rsi_h1: float = 0.0
    rsi_m5: float = 0.0
    # Account
    account_balance: float = 0.0
    # OHLC bars (D1=20, H4=30, H1=100, M5=60)
    ohlc_d1: list[OHLCBar] = []
    ohlc_h4: list[OHLCBar] = []
    ohlc_h1: list[OHLCBar] = []
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
    d1_trend: str = ""
    h4_trend: str = ""
    trend_alignment: str = ""  # e.g. "4/4 bearish" or "3/4 bullish (M5 diverging)"
    price_zone: str = ""
    entry_distance_pips: float = 0.0
    entry_status: str = ""  # "at_zone", "approaching", "requires_pullback"
    negative_factors: list[str] = []
    checklist_score: str = ""  # e.g. "10/12" from ICT entry checklist


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


class WatchTrade(BaseModel):
    """A setup being watched — EA monitors price, confirms via Haiku before entry."""
    id: str                          # UUID hex[:8]
    symbol: str = ""
    bias: str                        # "long" or "short"
    entry_min: float                 # Bottom of entry zone
    entry_max: float                 # Top of entry zone
    stop_loss: float
    tp1: float
    tp2: float
    sl_pips: float
    confidence: str
    confluence: list[str] = []       # Passed to Haiku for context
    checklist_score: str = ""
    created_at: float = 0.0          # Unix timestamp when watch started
    max_confirmations: int = 3       # Max Haiku checks before giving up
    confirmations_used: int = 0      # How many times Haiku was called
    status: str = "watching"         # "watching" | "confirmed" | "rejected" | "expired"


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
