# GBPJPY ICT Strategy — Integration Notes

> Architecture guide for connecting the MQL5 EA with the FastAPI + Claude API analysis pipeline, Telegram alerting, and multi-EA portfolio management.

---

## 1. FastAPI + Claude API Integration Architecture

### 1.1 System Overview

```
┌──────────────────┐     WebRequest (HTTP)     ┌──────────────────────┐
│   MT5 EA         │ ◄──────────────────────► │   FastAPI Backend     │
│   (MQL5)         │    JSON payloads          │   (Python)           │
│                  │                           │                      │
│  - Trade signals │                           │  - /api/analyze      │
│  - Market data   │                           │  - /api/validate     │
│  - Position state│                           │  - /api/log-trade    │
└──────────────────┘                           │  - /api/correlations │
                                               └──────────┬───────────┘
                                                          │
                                                  Claude API call
                                                          │
                                               ┌──────────▼───────────┐
                                               │   Claude API         │
                                               │   (Anthropic)        │
                                               │                      │
                                               │  - Bias validation   │
                                               │  - Setup scoring     │
                                               │  - Risk assessment   │
                                               └──────────────────────┘
```

### 1.2 Communication Flow

The EA communicates with the FastAPI backend at two key points in the trade lifecycle:

**Pre-Trade Validation (optional, adds ~1–3 seconds latency):**

1. EA detects a valid ICT setup (MSS + OB + FVG confluence)
2. EA sends market context to `POST /api/analyze` with current D1/H4/H1 structure summary
3. FastAPI routes the context to Claude API for a second-opinion bias analysis
4. Claude returns a confidence score (0–100) and any warning flags
5. EA uses the confidence score to adjust risk multiplier or skip trade entirely
6. If confidence < 60, trade is skipped; if 60–80, risk reduced to 0.5%; if > 80, full risk applied

**Post-Trade Logging (non-blocking):**

1. After trade execution or closure, EA sends trade data to `POST /api/log-trade`
2. FastAPI stores the trade record for performance analytics
3. This call is fire-and-forget — EA does not wait for response

### 1.3 FastAPI Endpoint Specifications

#### `POST /api/analyze` — Pre-Trade AI Validation

```python
from pydantic import BaseModel
from typing import Optional

class AnalysisRequest(BaseModel):
    symbol: str                    # "GBPJPY"
    direction: str                 # "LONG" or "SHORT"
    d1_bias: str                   # "BULLISH", "BEARISH", "RANGING"
    h4_bias: str                   # "BULLISH", "BEARISH", "RANGING"
    entry_price: float             # e.g. 191.410
    sl_price: float                # e.g. 191.150
    tp_price: float                # e.g. 192.190
    sl_pips: float                 # e.g. 26
    rr_ratio: float                # e.g. 3.0
    killzone: str                  # "LONDON" or "LNNY_OVERLAP"
    ob_timeframe: str              # "H1" or "H4"
    fvg_timeframe: str             # "M15" or "H1"
    has_liquidity_sweep: bool
    premium_discount: str          # "PREMIUM" or "DISCOUNT"
    current_daily_pnl: float       # e.g. -50.0
    equity: float                  # e.g. 9950.0
    consecutive_losses: int
    timestamp: int                 # Unix timestamp

class AnalysisResponse(BaseModel):
    confidence: int                # 0-100 score
    recommendation: str            # "PROCEED", "REDUCE_RISK", "SKIP"
    risk_multiplier: float         # 0.0 to 1.0
    reasoning: str                 # Brief AI explanation
    warnings: list[str]            # Any risk flags
```

**FastAPI Handler:**

```python
from fastapi import FastAPI
from anthropic import Anthropic

app = FastAPI()
client = Anthropic()

@app.post("/api/analyze", response_model=AnalysisResponse)
async def analyze_trade(req: AnalysisRequest):
    prompt = f"""You are an ICT trading analyst for GBPJPY on a 10,000 EUR FTMO account.

Evaluate this trade setup and return a confidence score (0-100):

Direction: {req.direction}
D1 Bias: {req.d1_bias} | H4 Bias: {req.h4_bias}
Entry: {req.entry_price} | SL: {req.sl_price} | TP: {req.tp_price}
Risk-Reward: {req.rr_ratio}
Killzone: {req.killzone}
Order Block TF: {req.ob_timeframe} | FVG TF: {req.fvg_timeframe}
Liquidity Sweep: {req.has_liquidity_sweep}
Premium/Discount Zone: {req.premium_discount}
Current Daily P&L: {req.current_daily_pnl} EUR
Consecutive Losses: {req.consecutive_losses}

Scoring guidelines:
- Full confluence (MSS+OB+FVG+sweep, aligned bias, London KZ): 85-100
- Missing sweep but other factors aligned: 65-84
- Bias conflict or wrong Premium/Discount zone: 0-30
- Elevated risk (3+ consecutive losses, daily loss > -200 EUR): reduce by 15 points

Return JSON with: confidence (int), recommendation (PROCEED/REDUCE_RISK/SKIP),
risk_multiplier (0.0-1.0), reasoning (1-2 sentences), warnings (list of strings).
"""

    response = client.messages.create(
        model="claude-sonnet-4-5-20250514",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )

    # Parse Claude's response (implement robust JSON extraction)
    result = parse_claude_response(response.content[0].text)
    return AnalysisResponse(**result)
```

#### `POST /api/log-trade` — Trade Logging

```python
class TradeLogRequest(BaseModel):
    symbol: str
    direction: str
    entry_price: float
    sl_price: float
    tp_price: float
    lot_size: float
    open_time: int
    close_time: Optional[int] = None
    close_price: Optional[float] = None
    profit: Optional[float] = None
    close_reason: Optional[str] = None
    killzone: str
    ob_timeframe: str
    fvg_timeframe: str
    had_sweep: bool
    ai_confidence: Optional[int] = None
    magic_number: int

@app.post("/api/log-trade")
async def log_trade(req: TradeLogRequest):
    # Store in database (SQLite, PostgreSQL, or simple JSON file)
    trade_db.insert(req.dict())
    return {"status": "logged"}
```

#### `POST /api/correlations` — Portfolio Correlation Check

```python
class CorrelationRequest(BaseModel):
    symbol: str                    # "GBPJPY"
    direction: str                 # "LONG" or "SHORT"
    open_positions: list[dict]     # [{symbol, direction, lot_size, entry_price}]

class CorrelationResponse(BaseModel):
    conflict_detected: bool
    conflicting_pairs: list[str]
    recommended_action: str        # "PROCEED", "REDUCE_SIZE", "SKIP"
    risk_multiplier: float

@app.post("/api/correlations", response_model=CorrelationResponse)
async def check_correlations(req: CorrelationRequest):
    # High-correlation pairs for GBPJPY
    correlations = {
        "GBPUSD": 0.94,   # Very high positive
        "USDJPY": 0.83,   # High positive
        "EURJPY": 0.78,   # Moderate positive
        "EURGBP": -0.65,  # Moderate negative (inverse)
    }

    conflicts = []
    max_exposure = 0

    for pos in req.open_positions:
        pair = pos["symbol"].replace(".", "")  # Remove broker suffix
        if pair in correlations:
            corr = correlations[pair]
            same_direction = (pos["direction"] == req.direction)

            # Positive correlation + same direction = amplified risk
            # Positive correlation + opposite direction = hedging
            if (corr > 0.7 and same_direction) or (corr < -0.7 and not same_direction):
                conflicts.append(pair)
                max_exposure += abs(corr)

    if max_exposure > 1.5:
        return CorrelationResponse(
            conflict_detected=True,
            conflicting_pairs=conflicts,
            recommended_action="SKIP",
            risk_multiplier=0.0
        )
    elif max_exposure > 0.7:
        return CorrelationResponse(
            conflict_detected=True,
            conflicting_pairs=conflicts,
            recommended_action="REDUCE_SIZE",
            risk_multiplier=0.5
        )
    else:
        return CorrelationResponse(
            conflict_detected=False,
            conflicting_pairs=[],
            recommended_action="PROCEED",
            risk_multiplier=1.0
        )
```

### 1.4 MQL5 WebRequest Configuration

For the EA to call external APIs, the user must whitelist URLs in MT5:

**Tools → Options → Expert Advisors → Allow WebRequest for listed URL:**
- `http://localhost:8000` (FastAPI local development)
- `https://your-vps.com` (FastAPI production)
- `https://api.telegram.org` (Telegram bot API)

The EA should gracefully degrade if WebRequest fails — all API calls are enhancement layers, not core trading logic. The EA must be fully functional without external dependencies.

### 1.5 Latency Considerations

| Operation | Expected Latency | Impact |
|-----------|-----------------|--------|
| FastAPI local call | 50–200ms | Negligible |
| FastAPI + Claude API | 1–3 seconds | Acceptable for M15 entries |
| Telegram notification | 200–500ms | Non-blocking, fire-and-forget |
| Full analysis pipeline | 2–5 seconds | Only called when full confluence detected |

For time-sensitive entries (e.g., market order at MSS confirmation), consider making the Claude API analysis call asynchronous: execute the trade immediately with conservative risk, then adjust position size based on AI response. If Claude recommends skipping, close the position within the first M15 candle.

---

## 2. Telegram Alert Format

### 2.1 Trade Entry Alert

```
🟢 <b>GBPJPY LONG</b> — London Killzone

📊 <b>Entry:</b> 191.410
🛑 <b>SL:</b> 191.150 (-26 pips)
✅ <b>TP1:</b> 191.670 (+26 pips, 1:1)
🎯 <b>TP2:</b> 192.190 (+78 pips, 1:3)

📐 <b>Setup:</b>
  • D1: Bullish | H4: Bullish (aligned)
  • OB: H1 Bullish @ 191.200–191.350
  • FVG: M15 Bullish @ 191.380–191.440
  • SSL Sweep: ✅ (191.180)
  • OTE Zone: 68% (in range)

💰 <b>Risk:</b>
  • Lot Size: 0.59
  • Risk: 1.0% (€100)
  • Daily P&L: -€0
  • AI Confidence: 87/100

⏰ 07:30 UTC | Magic: 202602
```

### 2.2 Trade Close Alert

```
🔴 <b>GBPJPY LONG CLOSED</b>

📊 Entry: 191.410 → Close: 192.190
💰 Profit: +€100.00 (+1.0%)
📏 Pips: +78 pips (R:R 1:3)
⏱ Duration: 7h 00m

📈 <b>Daily Summary:</b>
  • Trades Today: 1/3
  • Daily P&L: +€100.00
  • Peak Equity: €10,100

🏁 Close Reason: TP2 Hit
```

### 2.3 Risk Warning Alert

```
⚠️ <b>RISK WARNING — GBPJPY</b>

Daily loss has reached 70% of limit.
Current: -€350 / Max: -€500

Action: Position size reduced to 0.5%.
Remaining trades today: 1.
```

### 2.4 FTMO Violation Alert

```
🚨 <b>FTMO HALT — GBPJPY</b>

Daily loss limit BREACHED.
Loss: -€412 (exceeded €400 safety buffer)

All positions closed. Trading halted.
EA will resume at midnight CET.

Manual review recommended.
```

### 2.5 MQL5 Telegram Formatting Function

```mql5
string FormatEntryAlert(double entry, double sl, double tp1, double tp2,
                        double lotSize, double riskPct, string killzone,
                        string d1Bias, string h4Bias, string obInfo,
                        string fvgInfo, bool hasSweep, int aiConfidence)
{
    string dir = (sl < entry) ? "LONG" : "SHORT";
    string emoji = (sl < entry) ? "🟢" : "🔴";
    double slPips = MathAbs(entry - sl) / PipSize();
    double tp1Pips = MathAbs(tp1 - entry) / PipSize();
    double tp2Pips = MathAbs(tp2 - entry) / PipSize();
    double riskEUR = AccountInfoDouble(ACCOUNT_EQUITY) * riskPct / 100.0;

    string msg = emoji + " <b>GBPJPY " + dir + "</b> — " + killzone + "\n\n";
    msg += "📊 <b>Entry:</b> " + DoubleToString(entry, 3) + "\n";
    msg += "🛑 <b>SL:</b> " + DoubleToString(sl, 3) +
           " (-" + DoubleToString(slPips, 0) + " pips)\n";
    msg += "✅ <b>TP1:</b> " + DoubleToString(tp1, 3) +
           " (+" + DoubleToString(tp1Pips, 0) + " pips, 1:1)\n";
    msg += "🎯 <b>TP2:</b> " + DoubleToString(tp2, 3) +
           " (+" + DoubleToString(tp2Pips, 0) + " pips, 1:" +
           DoubleToString(tp2Pips/slPips, 1) + ")\n\n";
    msg += "📐 <b>Setup:</b>\n";
    msg += "  D1: " + d1Bias + " | H4: " + h4Bias + "\n";
    msg += "  OB: " + obInfo + "\n";
    msg += "  FVG: " + fvgInfo + "\n";
    msg += "  Sweep: " + (hasSweep ? "✅" : "❌") + "\n\n";
    msg += "💰 <b>Risk:</b>\n";
    msg += "  Lot: " + DoubleToString(lotSize, 2) + "\n";
    msg += "  Risk: " + DoubleToString(riskPct, 1) + "% (€" +
           DoubleToString(riskEUR, 0) + ")\n";
    if(aiConfidence > 0)
        msg += "  AI Confidence: " + IntegerToString(aiConfidence) + "/100\n";

    return msg;
}
```

---

## 3. Portfolio Correlation Management

### 3.1 GBPJPY Correlation Map

| Pair | Correlation | Risk Action |
|------|------------|-------------|
| GBP/USD | +0.94 | Same-direction = amplified risk. Reduce GBPJPY lot by 50%. |
| USD/JPY | +0.83 | Same-direction = amplified risk. Reduce GBPJPY lot by 50%. |
| EUR/JPY | +0.78 | Moderate overlap. Reduce GBPJPY lot by 25%. |
| EUR/GBP | -0.65 | Inverse. Same-direction trades on both = natural hedge. No reduction. |
| AUD/JPY | +0.72 | JPY carry correlation. Reduce GBPJPY lot by 25%. |
| GBP/CHF | +0.60 | Mild overlap. Monitor but no automatic reduction. |

### 3.2 Multi-EA Conflict Prevention

If you run multiple EAs on the same MT5 account (e.g., a EUR/USD EA alongside this GBPJPY EA), implement these safeguards:

**Magic Number Convention:**
Assign each EA a unique magic number range so they don't interfere with each other's positions:

| EA | Magic Number | Notes |
|----|-------------|-------|
| GBPJPY ICT Strategy | 202602 | This EA |
| EURUSD Scalper | 202701 | Example other EA |
| USDJPY Swing | 202801 | Example other EA |

**Shared Risk Pool:**
All EAs should contribute to a single daily loss tracker. Implement this via a shared file or named pipe:

```python
# FastAPI shared risk endpoint
@app.get("/api/portfolio-risk")
async def get_portfolio_risk():
    """Returns aggregate risk across all EAs"""
    all_positions = get_all_open_positions()  # From MT5 bridge

    total_risk = sum(pos["risk_eur"] for pos in all_positions)
    total_pnl = sum(pos["current_pnl"] for pos in all_positions)

    return {
        "total_risk_eur": total_risk,
        "total_daily_pnl": total_pnl,
        "remaining_daily_budget": 500 - abs(total_pnl),  # FTMO limit
        "position_count": len(all_positions),
        "correlated_exposure": calculate_correlation_risk(all_positions)
    }
```

**Pre-Trade Portfolio Check:**
Before entering a GBPJPY trade, the EA queries the portfolio risk endpoint. If the aggregate exposure (across all EAs) would push daily risk above the FTMO daily loss buffer, the GBPJPY EA reduces its position size or skips the trade entirely.

### 3.3 Avoiding Conflicting Positions

The GBPJPY EA should never open a trade that directly conflicts with another EA's position on a highly correlated pair:

```
Rule: If GBPUSD EA is LONG and GBPJPY signal is SHORT → SKIP
Rule: If USDJPY EA is SHORT and GBPJPY signal is SHORT → REDUCE (both weaken JPY)
Rule: If EURJPY EA is LONG and GBPJPY signal is LONG → REDUCE (both weaken JPY)
```

Implement this by iterating through all open positions (regardless of magic number) and checking for correlated symbols before executing.

### 3.4 Aggregate Drawdown Protection

The FTMO daily loss and total drawdown limits apply to the entire account, not per-EA. A critical protection layer:

```
Account-Level Daily Budget = €500 (5% of €10,000)

IF any single EA has lost more than €250 (50% of daily budget):
    → All EAs reduce risk to 0.5% per trade
    → Alert sent to Telegram

IF aggregate daily loss exceeds €350 (70% of daily budget):
    → All EAs halt new entries
    → Only position management (trailing stops, partial closes)
    → Alert sent to Telegram

IF aggregate daily loss exceeds €400 (80% of daily budget):
    → All EAs close all positions
    → Full trading halt until midnight CET reset
    → Critical alert sent to Telegram
```

---

## 4. Deployment Checklist

### 4.1 MT5 Configuration

- [ ] WebRequest URLs whitelisted (FastAPI, Telegram)
- [ ] EA has AutoTrading permission enabled
- [ ] Correct symbol suffix for broker (e.g., GBPJPY vs GBPJPY.i)
- [ ] Chart timeframe set to M15 (primary)
- [ ] EA attached to GBPJPY chart
- [ ] Input parameters loaded from preset file
- [ ] Magic number set and unique across all EAs

### 4.2 FastAPI Backend

- [ ] Server running and accessible from MT5 machine
- [ ] Claude API key configured in environment variables
- [ ] Database initialized for trade logging
- [ ] Correlation matrix loaded
- [ ] Health check endpoint (`GET /health`) responding
- [ ] Rate limiting configured (prevent runaway EA from exhausting API quota)

### 4.3 Telegram Bot

- [ ] Bot created via @BotFather
- [ ] Bot token stored securely (not hardcoded in EA)
- [ ] Chat ID verified (send test message)
- [ ] Bot added to target group/channel
- [ ] Parse mode set to HTML for formatting

### 4.4 FTMO Account

- [ ] Correct account phase selected (Challenge / Verification / Funded)
- [ ] `FTMOFundedMode` toggled appropriately
- [ ] Weekend close enabled/disabled per account rules
- [ ] News filter enabled/disabled per account phase
- [ ] Server timezone verified (for daily reset alignment with CET)

### 4.5 Testing Protocol

- [ ] Backtest on 2+ years of M15 data with tick-by-tick modeling
- [ ] Walk-forward analysis completed (minimum 6 windows)
- [ ] Paper trading for 2+ weeks on demo account
- [ ] All Telegram alerts verified (entry, close, warning, halt)
- [ ] FastAPI endpoints tested with mock data
- [ ] Correlation checks verified with multiple open positions
- [ ] Daily loss halt verified (simulate 5 consecutive losses)
- [ ] Weekend closure verified (run through Friday UTC 20:00)
- [ ] DST transition verified (test March/November clock changes)
