# GBPJPY ICT Strategy — MQL5 Implementation Specification

> **Target:** MetaTrader 5 Expert Advisor
> **Language:** MQL5
> **Pair:** GBPJPY
> **Account:** FTMO 10,000 EUR

---

## 1. Input Parameters

All configurable parameters with defaults and optimization ranges.

### 1.1 Risk Management

```mql5
input group "=== Risk Management ==="
input double   RiskPercent          = 1.0;    // Risk per trade (%) — optimize: 0.5–2.0, step 0.25
input double   ReducedRiskPercent   = 0.5;    // Risk when bias transitioning or post-loss (%)
input double   MaxDailyLossPct      = 4.0;    // EA daily loss halt (%) — 80% of FTMO 5% limit
input double   MaxTotalDrawdownPct  = 8.0;    // EA total drawdown halt (%) — 80% of FTMO 10%
input double   MinRiskReward        = 2.0;    // Minimum R:R to accept trade — optimize: 1.5–3.0
input int      MaxDailyTrades       = 3;      // Maximum trades per day — optimize: 2–5
input double   MaxSpreadPips        = 5.0;    // Maximum acceptable spread (pips) — optimize: 3–8
input int      MaxSLPips            = 70;     // Absolute SL cap (pips) — optimize: 50–100
input int      SLBufferPips         = 7;      // Buffer below/above OB for SL — optimize: 5–15
```

### 1.2 ICT Concept Detection

```mql5
input group "=== ICT Detection ==="
input int      SwingLookback        = 5;      // Bars for swing H/L detection — optimize: 3–10
input int      MSSDisplacementPips  = 15;     // Min displacement candle body (pips) — optimize: 10–25
input int      OBValidityH1        = 30;      // Max H1 candles OB stays valid — optimize: 20–50
input int      OBValidityH4        = 8;       // Max H4 candles OB stays valid — optimize: 5–15
input int      MinFVGPipsM15       = 15;      // Min FVG size on M15 (pips) — optimize: 10–25
input int      MinFVGPipsH1        = 25;      // Min FVG size on H1 (pips) — optimize: 15–40
input int      LiqSweepMinPips     = 5;       // Min sweep distance beyond level (pips) — optimize: 3–10
input double   OTELevelLow         = 0.62;    // OTE zone lower Fibonacci level
input double   OTELevelHigh        = 0.79;    // OTE zone upper Fibonacci level
input double   OTEOptimalLevel     = 0.705;   // Optimal entry Fibonacci level
input double   PremDiscLevel       = 0.5;     // Premium/Discount divider (50% Fib)
```

### 1.3 Killzone Timing

```mql5
input group "=== Killzone Timing (UTC hours) ==="
input int      LondonKZ_Start      = 7;       // London killzone start (UTC winter)
input int      LondonKZ_End        = 10;      // London killzone end (UTC winter)
input int      LNNY_KZ_Start       = 12;      // London-NY overlap start (UTC)
input int      LNNY_KZ_End         = 15;      // London-NY overlap end (UTC)
input int      DST_OffsetHours     = 0;       // DST adjustment (-1 for summer) — manual or auto
input bool     EnableLondonKZ      = true;     // Trade London killzone
input bool     EnableLNNYKZ        = true;     // Trade London-NY overlap
input double   SecondaryKZRiskMult = 0.75;     // Risk multiplier for secondary killzone
```

### 1.4 Position Management

```mql5
input group "=== Position Management ==="
input bool     UsePartialClose     = true;     // Enable partial close at TP1
input int      PartialClosePct     = 50;       // % of position to close at TP1 — optimize: 30–70
input bool     MoveToBreakeven     = true;     // Move SL to BE after TP1
input int      BreakevenBufferPips = 2;        // Pips above entry for BE SL — optimize: 0–5
input bool     UseTrailingStop     = true;     // Enable trailing stop after BE
input int      TrailingStopPips    = 20;       // Trail distance (pips) — optimize: 15–40
input int      TrailingStepPips    = 5;        // Trail step (pips) — optimize: 3–10
input int      CooldownMinutes     = 30;       // Minutes to wait after a loss — optimize: 15–60
```

### 1.5 FTMO Compliance

```mql5
input group "=== FTMO Compliance ==="
input bool     FTMOFundedMode      = false;    // Enable funded account restrictions
input bool     CloseBeforeWeekend  = false;    // Force close Friday 20:00 UTC (funded only)
input bool     NewsFilter          = false;    // Pause 2 min around high-impact news (funded only)
input int      FridayCloseHour     = 20;       // UTC hour for Friday position closure
input int      MagicNumber         = 202602;   // EA magic number for position identification
```

### 1.6 External Integration

```mql5
input group "=== External Integration ==="
input bool     EnableTelegram      = false;    // Send alerts to Telegram
input string   TelegramBotToken    = "";       // Telegram bot token
input string   TelegramChatID      = "";       // Telegram chat/group ID
input bool     EnableFastAPI       = false;    // Call FastAPI backend for AI analysis
input string   FastAPIBaseURL      = "http://localhost:8000";  // FastAPI server URL
input int      FastAPITimeoutMS    = 5000;     // API call timeout (ms)
```

---

## 2. Indicator & Data Requirements

### 2.1 Timeframes to Load

The EA requires simultaneous access to four timeframes:

| Timeframe | Purpose | Bars to Load | Update Frequency |
|-----------|---------|-------------|-----------------|
| D1 | Strategic bias (swing structure) | 100 bars | Once per new D1 bar |
| H4 | Tactical bias + Premium/Discount zones + OTE | 100 bars | Once per new H4 bar |
| H1 | Order block identification + FVG detection | 200 bars | Once per new H1 bar |
| M15 | MSS detection + entry trigger + FVG refinement | 200 bars | Every tick during killzone |

### 2.2 Data Structures

```mql5
// Rate arrays (set as series — index 0 = most recent bar)
MqlRates D1Rates[], H4Rates[], H1Rates[], M15Rates[];

// Swing point tracking
struct SwingPoint {
    int      barIndex;       // Bar index where swing occurred
    double   price;          // Swing high/low price level
    datetime time;           // Timestamp
    bool     isHigh;         // true = swing high, false = swing low
};

// Order block structure
struct OrderBlock {
    double   high;           // OB zone upper boundary
    double   low;            // OB zone lower boundary
    datetime formationTime;  // When OB formed
    int      formationBar;   // Bar index at formation
    bool     isBullish;      // true = bullish OB (last red candle before up move)
    bool     isMitigated;    // true if price has revisited and closed through
    ENUM_TIMEFRAMES tf;      // Timeframe of this OB (H1 or H4)
};

// Fair Value Gap structure
struct FairValueGap {
    double   top;            // Upper edge of gap
    double   bottom;         // Lower edge of gap
    double   ceLevel;        // Consequent encroachment (50% midpoint)
    datetime formationTime;
    int      formationBar;
    bool     isBullish;
    bool     isMitigated;
    ENUM_TIMEFRAMES tf;
};

// Liquidity level tracking
struct LiquidityLevel {
    double   price;          // Level where liquidity clusters
    int      touchCount;     // Number of times level was tested (equal H/L)
    bool     isBuySide;      // true = buy-side (above), false = sell-side (below)
    bool     isSwept;        // true if already swept
};

// Trade context (state machine)
struct TradeContext {
    int      dailyTradeCount;
    double   dailyStartEquity;
    double   peakEquity;
    datetime lastLossTime;
    double   dailyPnL;
    bool     tradingHalted;
    int      consecutiveLosses;
};
```

### 2.3 Calculations Required

| Calculation | Function | Inputs |
|------------|----------|--------|
| Swing detection | `FindSwingPoints()` | OHLC array, lookback period |
| D1/H4 bias | `DetermineBias()` | Swing points array |
| Premium/Discount | `CalcPremiumDiscount()` | H4 swing high, H4 swing low |
| OTE zone | `CalcOTEZone()` | Impulse high, impulse low, fib levels |
| Order blocks | `DetectOrderBlocks()` | OHLC array, timeframe, validity period |
| FVG detection | `DetectFVGs()` | OHLC array, min size, timeframe |
| MSS detection | `DetectMSS()` | M15 OHLC, swing points, displacement threshold |
| Liquidity mapping | `MapLiquidityLevels()` | Swing points, equal-level tolerance |
| Position sizing | `CalcLotSize()` | Equity, risk%, SL pips, symbol info |
| Pip value | `GetPipValue()` | Symbol, lot size |

---

## 3. EA Structure — Pseudocode

### 3.1 OnInit()

```
FUNCTION OnInit():
    // Set all rate arrays as series (index 0 = current bar)
    ArraySetAsSeries(D1Rates, true)
    ArraySetAsSeries(H4Rates, true)
    ArraySetAsSeries(H1Rates, true)
    ArraySetAsSeries(M15Rates, true)

    // Initialize trade context
    context.dailyStartEquity = AccountEquity()
    context.peakEquity = AccountEquity()
    context.dailyTradeCount = 0
    context.dailyPnL = 0
    context.tradingHalted = false
    context.consecutiveLosses = 0
    context.lastLossTime = 0

    // Validate broker settings
    IF SymbolInfoInteger(_Symbol, SYMBOL_TRADE_MODE) == SYMBOL_TRADE_MODE_DISABLED:
        RETURN INIT_FAILED

    // Register allowed URLs for WebRequest (if using external APIs)
    IF EnableFastAPI:
        // User must add FastAPIBaseURL to Tools > Options > Expert Advisors > Allow WebRequest
        Print("Ensure WebRequest is allowed for: " + FastAPIBaseURL)

    IF EnableTelegram:
        Print("Ensure WebRequest is allowed for: https://api.telegram.org")

    // Set timer for periodic tasks (daily reset, etc.)
    EventSetTimer(60)  // Every 60 seconds

    RETURN INIT_SUCCEEDED
```

### 3.2 OnTick() — Main Logic Flow

```
FUNCTION OnTick():
    // ─── PHASE 0: Safety Checks ───
    CheckDayReset()

    IF context.tradingHalted:
        ManageExistingPositions()  // Still manage open positions even when halted
        RETURN

    // ─── PHASE 1: Data Loading ───
    IF NOT LoadMultiTimeframeData():
        RETURN  // Data not ready

    // ─── PHASE 2: Killzone Filter ───
    currentKZ = GetCurrentKillzone()
    IF currentKZ == KZ_NONE:
        ManageExistingPositions()
        RETURN

    // ─── PHASE 3: FTMO Compliance Gate ───
    IF NOT CheckFTMOCompliance():
        RETURN

    // ─── PHASE 4: Spread Filter ───
    currentSpread = GetCurrentSpreadPips()
    IF currentSpread > MaxSpreadPips:
        RETURN

    // ─── PHASE 5: Cooldown Check ───
    IF InCooldownPeriod():
        ManageExistingPositions()
        RETURN

    // ─── PHASE 6: Position Management ───
    ManageExistingPositions()

    // ─── PHASE 7: Check if Already in Position ───
    IF HasOpenPosition(MagicNumber):
        RETURN  // Only one position at a time

    // ─── PHASE 8: Daily Trade Limit ───
    IF context.dailyTradeCount >= MaxDailyTrades:
        RETURN

    // ─── PHASE 9: Entry Logic ───
    signal = EvaluateEntrySignal(currentKZ)

    IF signal.isValid:
        ExecuteEntry(signal, currentKZ)
```

### 3.3 Entry Logic Flow — Detailed

```
FUNCTION EvaluateEntrySignal(killzone):
    signal = EMPTY_SIGNAL

    // ─── STEP 1: Determine D1 Bias ───
    d1Bias = DetermineBias(D1Rates, SwingLookback)
    // d1Bias: BULLISH, BEARISH, or RANGING

    IF d1Bias == RANGING:
        riskMultiplier = 0.5  // Reduce risk in ranging markets
    ELSE:
        riskMultiplier = 1.0

    // ─── STEP 2: Determine H4 Bias and Check Alignment ───
    h4Bias = DetermineBias(H4Rates, SwingLookback)

    IF h4Bias != d1Bias AND d1Bias != RANGING:
        RETURN signal  // No trade — bias conflict

    // ─── STEP 3: Premium/Discount Zone Check ───
    h4SwingHigh = GetRecentSwingHigh(H4Rates, SwingLookback)
    h4SwingLow  = GetRecentSwingLow(H4Rates, SwingLookback)
    equilibrium = (h4SwingHigh + h4SwingLow) / 2.0
    currentPrice = SymbolInfoDouble(_Symbol, SYMBOL_BID)

    IF d1Bias == BULLISH AND currentPrice > equilibrium:
        RETURN signal  // Price in Premium — no longs

    IF d1Bias == BEARISH AND currentPrice < equilibrium:
        RETURN signal  // Price in Discount — no shorts

    // ─── STEP 4: Order Block Detection ───
    h4OBs[] = DetectOrderBlocks(H4Rates, OBValidityH4, d1Bias)
    h1OBs[] = DetectOrderBlocks(H1Rates, OBValidityH1, d1Bias)

    // Find active OB (price within or near OB zone)
    activeOB = FindActiveOB(h4OBs, h1OBs, currentPrice)

    IF activeOB == NULL:
        RETURN signal  // No valid OB at current price

    // ─── STEP 5: M15 Market Structure Shift Detection ───
    mss = DetectMSS(M15Rates, SwingLookback, MSSDisplacementPips, d1Bias)

    IF NOT mss.isValid:
        RETURN signal  // No MSS confirmed

    // ─── STEP 6: FVG Filter ───
    m15FVGs[] = DetectFVGs(M15Rates, MinFVGPipsM15, d1Bias)
    h1FVGs[]  = DetectFVGs(H1Rates, MinFVGPipsH1, d1Bias)

    activeFVG = FindRelevantFVG(m15FVGs, h1FVGs, activeOB, currentPrice)

    IF activeFVG == NULL:
        RETURN signal  // No confirming FVG

    // ─── STEP 7: Liquidity Sweep Check (Bonus) ───
    sweep = DetectLiquiditySweep(M15Rates, H1Rates, d1Bias)
    IF sweep.isValid:
        riskMultiplier *= 1.0  // Full confidence
    ELSE:
        riskMultiplier *= 0.85  // Slightly reduced without sweep

    // ─── STEP 8: Calculate SL and TP ───
    IF d1Bias == BULLISH:
        entryPrice = activeFVG.ceLevel  // Enter at CE of FVG
        slPrice    = activeOB.low - (SLBufferPips * PipSize())
        slPips     = (entryPrice - slPrice) / PipSize()
        tp1Price   = entryPrice + (slPips * PipSize())        // 1:1 RR
        tp2Price   = entryPrice + (slPips * MinRiskReward * PipSize())  // 1:R RR
    ELSE:  // BEARISH
        entryPrice = activeFVG.ceLevel
        slPrice    = activeOB.high + (SLBufferPips * PipSize())
        slPips     = (slPrice - entryPrice) / PipSize()
        tp1Price   = entryPrice - (slPips * PipSize())
        tp2Price   = entryPrice - (slPips * MinRiskReward * PipSize())

    // ─── STEP 9: Validate SL Cap ───
    IF slPips > MaxSLPips:
        RETURN signal  // SL too wide — skip trade

    // ─── STEP 10: Validate R:R ───
    rrRatio = CalculateRR(entryPrice, slPrice, tp2Price)
    IF rrRatio < MinRiskReward:
        RETURN signal  // R:R below minimum

    // ─── STEP 11: Check Correlated Positions ───
    IF HasCorrelatedPosition("GBPUSD") OR HasCorrelatedPosition("USDJPY"):
        riskMultiplier *= 0.5  // Reduce risk for correlation

    // ─── STEP 12: Killzone Risk Adjustment ───
    IF killzone == KZ_LNNY_OVERLAP:
        riskMultiplier *= SecondaryKZRiskMult

    // ─── BUILD SIGNAL ───
    signal.isValid      = true
    signal.direction    = d1Bias
    signal.entryPrice   = entryPrice
    signal.slPrice      = slPrice
    signal.tp1Price     = tp1Price
    signal.tp2Price     = tp2Price
    signal.slPips       = slPips
    signal.riskMult     = riskMultiplier
    signal.obRef        = activeOB
    signal.fvgRef       = activeFVG
    signal.hasSweep     = sweep.isValid

    RETURN signal
```

### 3.4 Trade Execution

```
FUNCTION ExecuteEntry(signal, killzone):
    // Calculate lot size
    effectiveRisk = RiskPercent * signal.riskMult
    equity = AccountInfoDouble(ACCOUNT_EQUITY)
    riskAmount = equity * (effectiveRisk / 100.0)

    tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE)
    tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE)
    pipValue  = tickValue * (PipSize() / tickSize)

    lotSize = riskAmount / (signal.slPips * pipValue)
    lotSize = NormalizeLotSize(lotSize)

    // Validate lot size
    IF lotSize < SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN):
        LogWarning("Lot size too small — skipping trade")
        RETURN

    // Validate daily loss headroom
    potentialLoss = signal.slPips * pipValue * lotSize
    IF (context.dailyPnL - potentialLoss) < -(equity * MaxDailyLossPct / 100.0):
        LogWarning("Trade would exceed daily loss limit — skipping")
        RETURN

    // Build trade request
    request.action  = TRADE_ACTION_DEAL
    request.symbol  = _Symbol
    request.volume  = lotSize
    request.type    = (signal.direction == BULLISH) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL
    request.price   = (signal.direction == BULLISH) ? Ask() : Bid()
    request.sl      = signal.slPrice
    request.tp      = signal.tp2Price  // Full TP target
    request.magic   = MagicNumber
    request.comment = BuildTradeComment(signal, killzone)
    request.deviation = 10  // 1 pip slippage tolerance

    // Execute
    result = OrderSend(request)

    IF result.retcode == TRADE_RETCODE_DONE:
        context.dailyTradeCount++

        // Store TP1 for partial close management
        StoreTP1Level(result.order, signal.tp1Price)

        // Send notifications
        IF EnableTelegram:
            SendTelegramAlert(signal, lotSize, killzone)

        IF EnableFastAPI:
            SendTradeToFastAPI(signal, lotSize)

        LogInfo("Trade executed: " + signal.ToString())
    ELSE:
        LogError("Order failed: " + IntegerToString(result.retcode))
```

### 3.5 Position Management

```
FUNCTION ManageExistingPositions():
    FOR i = PositionsTotal() - 1 TO 0:
        ticket = PositionGetTicket(i)
        IF NOT PositionSelectByTicket(ticket):
            CONTINUE

        IF PositionGetInteger(POSITION_MAGIC) != MagicNumber:
            CONTINUE

        openPrice   = PositionGetDouble(POSITION_PRICE_OPEN)
        currentSL   = PositionGetDouble(POSITION_SL)
        currentTP   = PositionGetDouble(POSITION_TP)
        profit      = PositionGetDouble(POSITION_PROFIT)
        volume      = PositionGetDouble(POSITION_VOLUME)
        posType     = PositionGetInteger(POSITION_TYPE)

        currentPrice = (posType == POSITION_TYPE_BUY) ? Bid() : Ask()
        profitPips   = CalcProfitPips(openPrice, currentPrice, posType)
        slPips       = CalcSLPips(openPrice, currentSL, posType)
        tp1Price     = GetStoredTP1Level(ticket)

        // ─── Partial Close at TP1 (1:1 RR) ───
        IF UsePartialClose AND NOT IsPartialClosed(ticket):
            IF HasReachedTP1(currentPrice, tp1Price, posType):
                closeLots = NormalizeLotSize(volume * PartialClosePct / 100.0)
                IF closeLots > 0:
                    PartialClosePosition(ticket, closeLots)
                    MarkPartialClosed(ticket)

                    // Move SL to breakeven + buffer
                    IF MoveToBreakeven:
                        newSL = openPrice + (BreakevenBufferPips * PipSize() * DirectionSign(posType))
                        ModifyPositionSL(ticket, newSL)

        // ─── Trailing Stop ───
        IF UseTrailingStop AND IsPartialClosed(ticket):
            trailPrice = CalcTrailingStopPrice(currentPrice, TrailingStopPips, posType)

            IF IsBetterSL(trailPrice, currentSL, posType):
                IF (trailPrice - currentSL) >= (TrailingStepPips * PipSize()):
                    ModifyPositionSL(ticket, trailPrice)

    // ─── FTMO Funded: Friday Close ───
    IF FTMOFundedMode AND CloseBeforeWeekend:
        IF IsFridayCloseTime():
            CloseAllPositions("Friday weekend closure")
```

### 3.6 Exit Logic

```
FUNCTION HandleTradeClose(ticket, closeReason):
    IF NOT PositionSelectByTicket(ticket):
        RETURN

    profit = PositionGetDouble(POSITION_PROFIT)

    // Update context
    context.dailyPnL += profit

    IF profit < 0:
        context.consecutiveLosses++
        context.lastLossTime = TimeCurrent()

        IF context.consecutiveLosses >= 3:
            LogWarning("3 consecutive losses — extending cooldown")
            // Cooldown will be enforced by InCooldownPeriod()
    ELSE:
        context.consecutiveLosses = 0

    // Update peak equity
    currentEquity = AccountInfoDouble(ACCOUNT_EQUITY)
    IF currentEquity > context.peakEquity:
        context.peakEquity = currentEquity

    // Send close notification
    IF EnableTelegram:
        SendTelegramCloseAlert(ticket, profit, closeReason)

    IF EnableFastAPI:
        SendCloseToFastAPI(ticket, profit, closeReason)
```

---

## 4. Core Algorithm Implementations

### 4.1 Swing Point Detection

```mql5
// Detect swing highs and lows with configurable lookback
// Returns array of SwingPoint structs sorted by time (most recent first)
void FindSwingPoints(const MqlRates &rates[], int lookback, SwingPoint &swings[])
{
    ArrayResize(swings, 0);
    int barsAvailable = ArraySize(rates);

    for(int i = lookback; i < barsAvailable - lookback; i++)
    {
        // Check for swing high
        bool isSwingHigh = true;
        for(int j = 1; j <= lookback; j++)
        {
            if(rates[i].high <= rates[i-j].high || rates[i].high <= rates[i+j].high)
            {
                isSwingHigh = false;
                break;
            }
        }

        if(isSwingHigh)
        {
            int idx = ArraySize(swings);
            ArrayResize(swings, idx + 1);
            swings[idx].barIndex = i;
            swings[idx].price    = rates[i].high;
            swings[idx].time     = rates[i].time;
            swings[idx].isHigh   = true;
        }

        // Check for swing low
        bool isSwingLow = true;
        for(int j = 1; j <= lookback; j++)
        {
            if(rates[i].low >= rates[i-j].low || rates[i].low >= rates[i+j].low)
            {
                isSwingLow = false;
                break;
            }
        }

        if(isSwingLow)
        {
            int idx = ArraySize(swings);
            ArrayResize(swings, idx + 1);
            swings[idx].barIndex = i;
            swings[idx].price    = rates[i].low;
            swings[idx].time     = rates[i].time;
            swings[idx].isHigh   = false;
        }
    }
}
```

### 4.2 Bias Determination

```mql5
// Determine market bias from swing structure
// Returns: +1 (BULLISH), -1 (BEARISH), 0 (RANGING)
int DetermineBias(const MqlRates &rates[], int lookback)
{
    SwingPoint swings[];
    FindSwingPoints(rates, lookback, swings);

    if(ArraySize(swings) < 4)
        return 0;  // Not enough structure

    // Get last two swing highs and two swing lows
    double lastSH = 0, prevSH = 0, lastSL = 999999, prevSL = 999999;
    int shCount = 0, slCount = 0;

    for(int i = 0; i < ArraySize(swings) && (shCount < 2 || slCount < 2); i++)
    {
        if(swings[i].isHigh && shCount < 2)
        {
            if(shCount == 0) lastSH = swings[i].price;
            else prevSH = swings[i].price;
            shCount++;
        }
        if(!swings[i].isHigh && slCount < 2)
        {
            if(slCount == 0) lastSL = swings[i].price;
            else prevSL = swings[i].price;
            slCount++;
        }
    }

    bool higherHighs = (lastSH > prevSH);
    bool higherLows  = (lastSL > prevSL);
    bool lowerHighs  = (lastSH < prevSH);
    bool lowerLows   = (lastSL < prevSL);

    if(higherHighs && higherLows)  return +1;  // Bullish
    if(lowerHighs && lowerLows)   return -1;   // Bearish
    return 0;                                   // Ranging
}
```

### 4.3 Order Block Detection

```mql5
// Detect order blocks on given timeframe
// direction: +1 = bullish OBs only, -1 = bearish OBs only
void DetectOrderBlocks(const MqlRates &rates[], int validityCandles,
                       int direction, OrderBlock &obs[])
{
    ArrayResize(obs, 0);
    int bars = MathMin(ArraySize(rates), validityCandles + 10);

    for(int i = 1; i < bars - 1; i++)
    {
        if(direction == +1)  // Look for bullish OB (last bearish candle before impulse up)
        {
            bool isBearishCandle = (rates[i].close < rates[i].open);

            if(isBearishCandle)
            {
                // Check for bullish impulse following this candle
                double impulseSize = 0;
                for(int j = i - 1; j >= MathMax(0, i - 3); j--)
                {
                    impulseSize += (rates[j].close - rates[j].open);
                }

                if(impulseSize > MSSDisplacementPips * PipSize())
                {
                    int idx = ArraySize(obs);
                    ArrayResize(obs, idx + 1);
                    obs[idx].high          = MathMax(rates[i].open, rates[i].close);
                    obs[idx].low           = MathMin(rates[i].open, rates[i].close);
                    obs[idx].formationTime = rates[i].time;
                    obs[idx].formationBar  = i;
                    obs[idx].isBullish     = true;
                    obs[idx].isMitigated   = CheckOBMitigated(rates, i, obs[idx]);
                }
            }
        }
        else  // direction == -1, bearish OB (last bullish candle before impulse down)
        {
            bool isBullishCandle = (rates[i].close > rates[i].open);

            if(isBullishCandle)
            {
                double impulseSize = 0;
                for(int j = i - 1; j >= MathMax(0, i - 3); j--)
                {
                    impulseSize += (rates[j].open - rates[j].close);
                }

                if(impulseSize > MSSDisplacementPips * PipSize())
                {
                    int idx = ArraySize(obs);
                    ArrayResize(obs, idx + 1);
                    obs[idx].high          = MathMax(rates[i].open, rates[i].close);
                    obs[idx].low           = MathMin(rates[i].open, rates[i].close);
                    obs[idx].formationTime = rates[i].time;
                    obs[idx].formationBar  = i;
                    obs[idx].isBullish     = false;
                    obs[idx].isMitigated   = CheckOBMitigated(rates, i, obs[idx]);
                }
            }
        }
    }
}
```

### 4.4 Fair Value Gap Detection

```mql5
// Detect FVGs using three-candle pattern
void DetectFVGs(const MqlRates &rates[], int minPips, int direction, FairValueGap &fvgs[])
{
    ArrayResize(fvgs, 0);
    double minGap = minPips * PipSize();

    for(int i = 2; i < ArraySize(rates); i++)
    {
        if(direction == +1)  // Bullish FVG
        {
            // Candle 1 (oldest) high < Candle 3 (newest) low
            // Remember: index 0 = newest when ArraySetAsSeries is true
            // So rates[i] = oldest candle, rates[i-2] = newest
            double gapBottom = rates[i].high;    // Candle 1 high
            double gapTop    = rates[i-2].low;   // Candle 3 low

            if(gapTop > gapBottom && (gapTop - gapBottom) >= minGap)
            {
                int idx = ArraySize(fvgs);
                ArrayResize(fvgs, idx + 1);
                fvgs[idx].top            = gapTop;
                fvgs[idx].bottom         = gapBottom;
                fvgs[idx].ceLevel        = (gapTop + gapBottom) / 2.0;
                fvgs[idx].formationTime  = rates[i-1].time;
                fvgs[idx].formationBar   = i - 1;
                fvgs[idx].isBullish      = true;
                fvgs[idx].isMitigated    = false;
            }
        }
        else  // Bearish FVG
        {
            double gapTop    = rates[i].low;     // Candle 1 low
            double gapBottom = rates[i-2].high;   // Candle 3 high

            if(gapTop > gapBottom && (gapTop - gapBottom) >= minGap)
            {
                int idx = ArraySize(fvgs);
                ArrayResize(fvgs, idx + 1);
                fvgs[idx].top            = gapTop;
                fvgs[idx].bottom         = gapBottom;
                fvgs[idx].ceLevel        = (gapTop + gapBottom) / 2.0;
                fvgs[idx].formationTime  = rates[i-1].time;
                fvgs[idx].formationBar   = i - 1;
                fvgs[idx].isBullish      = false;
                fvgs[idx].isMitigated    = false;
            }
        }
    }
}
```

### 4.5 Market Structure Shift Detection

```mql5
// Detect MSS on M15 timeframe
// Returns: true if valid MSS detected in the expected direction
struct MSSResult {
    bool     isValid;
    double   breakLevel;       // The structure level that was broken
    int      breakBar;         // Bar where break occurred
    double   displacementPips; // Size of displacement
};

MSSResult DetectMSS(const MqlRates &rates[], int lookback,
                    int minDisplacement, int expectedDirection)
{
    MSSResult result = {false, 0, 0, 0};

    SwingPoint swings[];
    FindSwingPoints(rates, lookback, swings);

    if(ArraySize(swings) < 3)
        return result;

    int currentBias = DetermineBias(rates, lookback);

    if(expectedDirection == +1)  // Looking for bullish MSS
    {
        // In bearish structure, find the most recent lower high
        for(int i = 0; i < ArraySize(swings); i++)
        {
            if(swings[i].isHigh)
            {
                double lowerHigh = swings[i].price;

                // Check if current price has broken above this lower high
                // with displacement
                for(int j = 0; j < swings[i].barIndex; j++)
                {
                    double candleBody = MathAbs(rates[j].close - rates[j].open);
                    bool closedAbove  = (rates[j].close > lowerHigh);

                    if(closedAbove && candleBody >= minDisplacement * PipSize())
                    {
                        result.isValid          = true;
                        result.breakLevel       = lowerHigh;
                        result.breakBar         = j;
                        result.displacementPips = candleBody / PipSize();
                        return result;
                    }
                }
                break;  // Only check the most recent lower high
            }
        }
    }
    else  // Looking for bearish MSS
    {
        for(int i = 0; i < ArraySize(swings); i++)
        {
            if(!swings[i].isHigh)
            {
                double higherLow = swings[i].price;

                for(int j = 0; j < swings[i].barIndex; j++)
                {
                    double candleBody = MathAbs(rates[j].close - rates[j].open);
                    bool closedBelow  = (rates[j].close < higherLow);

                    if(closedBelow && candleBody >= minDisplacement * PipSize())
                    {
                        result.isValid          = true;
                        result.breakLevel       = higherLow;
                        result.breakBar         = j;
                        result.displacementPips = candleBody / PipSize();
                        return result;
                    }
                }
                break;
            }
        }
    }

    return result;
}
```

### 4.6 Liquidity Sweep Detection

```mql5
// Detect liquidity sweeps (price exceeds key level then reverses)
struct SweepResult {
    bool   isValid;
    double sweepLevel;
    double sweepExtreme;  // How far beyond the level price reached
    int    sweepBar;
};

SweepResult DetectLiquiditySweep(const MqlRates &rates[], int direction)
{
    SweepResult result = {false, 0, 0, 0};

    SwingPoint swings[];
    FindSwingPoints(rates, SwingLookback, swings);

    // Look for equal highs/lows (liquidity clusters)
    double tolerance = 3 * PipSize();  // 3-pip tolerance for "equal" levels

    if(direction == +1)  // Bullish — looking for SSL sweep below equal lows
    {
        // Find equal lows
        for(int i = 0; i < ArraySize(swings) - 1; i++)
        {
            if(!swings[i].isHigh)
            {
                for(int j = i + 1; j < ArraySize(swings); j++)
                {
                    if(!swings[j].isHigh &&
                       MathAbs(swings[i].price - swings[j].price) < tolerance)
                    {
                        double eqlLevel = MathMin(swings[i].price, swings[j].price);

                        // Check if recent price swept below and reversed
                        for(int k = 0; k < swings[i].barIndex; k++)
                        {
                            if(rates[k].low < eqlLevel - LiqSweepMinPips * PipSize() &&
                               rates[k].close > eqlLevel)
                            {
                                result.isValid      = true;
                                result.sweepLevel   = eqlLevel;
                                result.sweepExtreme = rates[k].low;
                                result.sweepBar     = k;
                                return result;
                            }
                        }
                    }
                }
            }
        }
    }
    else  // Bearish — looking for BSL sweep above equal highs
    {
        for(int i = 0; i < ArraySize(swings) - 1; i++)
        {
            if(swings[i].isHigh)
            {
                for(int j = i + 1; j < ArraySize(swings); j++)
                {
                    if(swings[j].isHigh &&
                       MathAbs(swings[i].price - swings[j].price) < tolerance)
                    {
                        double eqhLevel = MathMax(swings[i].price, swings[j].price);

                        for(int k = 0; k < swings[i].barIndex; k++)
                        {
                            if(rates[k].high > eqhLevel + LiqSweepMinPips * PipSize() &&
                               rates[k].close < eqhLevel)
                            {
                                result.isValid      = true;
                                result.sweepLevel   = eqhLevel;
                                result.sweepExtreme = rates[k].high;
                                result.sweepBar     = k;
                                return result;
                            }
                        }
                    }
                }
            }
        }
    }

    return result;
}
```

### 4.7 Position Sizing

```mql5
// Calculate normalized lot size based on risk parameters
double CalcLotSize(double riskPercent, double slPips)
{
    double equity    = AccountInfoDouble(ACCOUNT_EQUITY);
    double riskAmt   = equity * (riskPercent / 100.0);

    double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
    double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
    double pipSize   = PipSize();

    // Pip value per standard lot
    double pipValuePerLot = tickValue * (pipSize / tickSize);

    // Calculate raw lot size
    double lots = riskAmt / (slPips * pipValuePerLot);

    // Normalize to broker step
    double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
    double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
    double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

    lots = MathFloor(lots / lotStep) * lotStep;
    lots = MathMax(lots, minLot);
    lots = MathMin(lots, maxLot);

    return NormalizeDouble(lots, 2);
}

// Get pip size for JPY pairs (0.01) vs standard (0.0001)
double PipSize()
{
    int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
    if(digits == 3 || digits == 2)
        return 0.01;   // JPY pair
    else
        return 0.0001; // Standard pair
}
```

### 4.8 FTMO Compliance Module

```mql5
// Comprehensive FTMO compliance checking
bool CheckFTMOCompliance()
{
    double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
    double balance = AccountInfoDouble(ACCOUNT_BALANCE);

    // ── Daily Loss Check ──
    context.dailyPnL = equity - context.dailyStartEquity;
    double dailyLossLimit = balance * (MaxDailyLossPct / 100.0);

    // Warning level (80% of limit)
    if(context.dailyPnL < -(dailyLossLimit * 0.875))
    {
        LogWarning("FTMO WARNING: Daily loss at 87.5% of limit");
        SendTelegramAlert("WARNING: Daily loss approaching limit!");
    }

    // Critical level — halt trading
    if(context.dailyPnL < -dailyLossLimit)
    {
        LogError("FTMO CRITICAL: Daily loss limit reached — halting");
        context.tradingHalted = true;
        CloseAllPositions("FTMO daily loss limit");
        SendTelegramAlert("HALT: Daily loss limit reached. All positions closed.");
        return false;
    }

    // ── Total Drawdown Check ──
    double drawdown = context.peakEquity - equity;
    double drawdownLimit = balance * (MaxTotalDrawdownPct / 100.0);

    if(drawdown > drawdownLimit)
    {
        LogError("FTMO CRITICAL: Total drawdown limit reached — halting");
        context.tradingHalted = true;
        CloseAllPositions("FTMO total drawdown limit");
        SendTelegramAlert("HALT: Total drawdown limit reached.");
        return false;
    }

    // ── News Filter (Funded accounts only) ──
    if(FTMOFundedMode && NewsFilter)
    {
        if(IsHighImpactNewsWindow())
        {
            return false;  // No trading 2 min before/after news
        }
    }

    // ── Weekend Closure (Funded accounts only) ──
    if(FTMOFundedMode && CloseBeforeWeekend)
    {
        MqlDateTime dt;
        TimeToStruct(TimeCurrent(), dt);

        if(dt.day_of_week == 5 && dt.hour >= FridayCloseHour)
        {
            CloseAllPositions("Friday weekend closure");
            return false;
        }
    }

    return true;
}

// Daily reset (called from OnTimer or OnTick)
void CheckDayReset()
{
    static int lastDay = -1;

    MqlDateTime dt;
    TimeToStruct(TimeCurrent(), dt);

    // FTMO resets at midnight CET — approximate with UTC+1
    int currentDay = dt.day;

    if(currentDay != lastDay)
    {
        context.dailyStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);
        context.dailyTradeCount  = 0;
        context.dailyPnL         = 0;
        context.tradingHalted    = false;  // Reset halt on new day
        lastDay = currentDay;

        LogInfo("New trading day. Start equity: " +
                DoubleToString(context.dailyStartEquity, 2));
    }
}
```

### 4.9 Killzone Time Filter

```mql5
// Returns current killzone or KZ_NONE
enum KILLZONE { KZ_NONE, KZ_LONDON, KZ_LNNY_OVERLAP, KZ_LONDON_CLOSE };

KILLZONE GetCurrentKillzone()
{
    MqlDateTime dt;
    TimeToStruct(TimeCurrent(), dt);

    int hour = dt.hour + DST_OffsetHours;  // Apply DST correction
    if(hour < 0) hour += 24;
    if(hour >= 24) hour -= 24;

    // Skip weekends
    if(dt.day_of_week == 0 || dt.day_of_week == 6)
        return KZ_NONE;

    // London Open Killzone (PRIMARY)
    if(EnableLondonKZ && hour >= LondonKZ_Start && hour < LondonKZ_End)
        return KZ_LONDON;

    // London-NY Overlap Killzone (SECONDARY)
    if(EnableLNNYKZ && hour >= LNNY_KZ_Start && hour < LNNY_KZ_End)
        return KZ_LNNY_OVERLAP;

    return KZ_NONE;
}
```

---

## 5. External Communication Functions

### 5.1 Telegram Alerts

```mql5
bool SendTelegramMessage(string message)
{
    if(!EnableTelegram || TelegramBotToken == "" || TelegramChatID == "")
        return false;

    string url = "https://api.telegram.org/bot" + TelegramBotToken + "/sendMessage";

    // URL-encode the message
    string postData = "chat_id=" + TelegramChatID +
                      "&text=" + UrlEncode(message) +
                      "&parse_mode=HTML";

    char post[], result[];
    string headers = "Content-Type: application/x-www-form-urlencoded\r\n";
    string resultHeaders;

    StringToCharArray(postData, post, 0, WHOLE_ARRAY, CP_UTF8);

    int response = WebRequest("POST", url, headers, 5000, post, result, resultHeaders);

    return (response == 200);
}
```

### 5.2 FastAPI Integration

```mql5
// Send trade data to FastAPI backend for AI analysis
string CallFastAPI(string endpoint, string jsonPayload)
{
    if(!EnableFastAPI || FastAPIBaseURL == "")
        return "";

    string url = FastAPIBaseURL + endpoint;

    char post[], result[];
    string headers = "Content-Type: application/json\r\n";
    string resultHeaders;

    StringToCharArray(jsonPayload, post, 0, WHOLE_ARRAY, CP_UTF8);

    int response = WebRequest("POST", url, headers, FastAPITimeoutMS,
                              post, result, resultHeaders);

    if(response == 200)
        return CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);

    LogError("FastAPI call failed: " + IntegerToString(response));
    return "";
}

// Build JSON payload for trade signal
string BuildTradeJSON(double entry, double sl, double tp, string direction,
                      double lotSize, string killzone, string obTF, string fvgTF)
{
    string json = "{";
    json += "\"symbol\":\"" + _Symbol + "\",";
    json += "\"direction\":\"" + direction + "\",";
    json += "\"entry\":" + DoubleToString(entry, 3) + ",";
    json += "\"sl\":" + DoubleToString(sl, 3) + ",";
    json += "\"tp\":" + DoubleToString(tp, 3) + ",";
    json += "\"lot_size\":" + DoubleToString(lotSize, 2) + ",";
    json += "\"killzone\":\"" + killzone + "\",";
    json += "\"ob_timeframe\":\"" + obTF + "\",";
    json += "\"fvg_timeframe\":\"" + fvgTF + "\",";
    json += "\"equity\":" + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2) + ",";
    json += "\"daily_pnl\":" + DoubleToString(context.dailyPnL, 2) + ",";
    json += "\"timestamp\":" + IntegerToString((long)TimeCurrent());
    json += "}";

    return json;
}
```

---

## 6. File Structure

```
GBPJPY_ICT_EA/
├── GBPJPY_ICT_EA.mq5           // Main EA file
├── Include/
│   ├── ICT_SwingDetection.mqh   // Swing high/low algorithms
│   ├── ICT_OrderBlocks.mqh      // Order block detection
│   ├── ICT_FairValueGaps.mqh    // FVG detection
│   ├── ICT_MarketStructure.mqh  // MSS and BOS detection
│   ├── ICT_Liquidity.mqh        // Liquidity level mapping and sweep detection
│   ├── ICT_Killzones.mqh        // Session and killzone time filters
│   ├── FTMO_Compliance.mqh      // FTMO rule enforcement
│   ├── RiskManager.mqh          // Position sizing, drawdown tracking
│   ├── TradeExecutor.mqh        // Order execution and position management
│   └── ExternalComms.mqh        // Telegram + FastAPI integration
├── Scripts/
│   └── GBPJPY_Backtest_Report.mq5  // Custom backtesting report generator
└── Presets/
    ├── Conservative.set         // 0.5% risk, strict filters
    ├── Balanced.set             // 1.0% risk, standard filters
    └── Aggressive.set           // 2.0% risk, relaxed filters
```

---

## 7. Optimization & Backtesting Notes

### 7.1 Strategy Tester Configuration

| Setting | Value |
|---------|-------|
| Period | M15 (primary chart) |
| Modeling | Every tick based on real ticks |
| Date range | Minimum 2 years (2024-01-01 to 2026-01-01) |
| Deposit | 10,000 EUR |
| Leverage | 1:100 |
| Optimization | Genetic algorithm |

### 7.2 Key Metrics to Monitor

| Metric | Acceptable | Target |
|--------|-----------|--------|
| Profit Factor | > 1.3 | > 1.8 |
| Win Rate | > 40% | > 55% |
| Max Drawdown | < 10% | < 6% |
| Sharpe Ratio | > 0.8 | > 1.5 |
| Recovery Factor | > 2 | > 4 |
| Avg Win / Avg Loss | > 1.5 | > 2.5 |
| Max Consecutive Losses | < 8 | < 5 |

### 7.3 Walk-Forward Analysis

Split 2-year data into:
- **In-sample:** 6-month rolling windows for optimization
- **Out-of-sample:** 2-month forward windows for validation
- **Robustness test:** Strategy must be profitable in at least 70% of out-of-sample windows

### 7.4 Monte Carlo Simulation

After optimization, run Monte Carlo with 1,000 iterations randomizing trade order to verify:
- 95th percentile max drawdown stays below 10%
- 5th percentile final equity remains profitable
- Strategy degrades gracefully rather than catastrophically under stress
