# AI Trade Bot ICT — Integration Notes

> Architecture guide for the MT5 EA ↔ FastAPI server ↔ Claude API analysis pipeline, Telegram alerting, and multi-pair portfolio management.
>
> **Last updated:** February 2026 — v3.0 (multi-pair, market context, learning loop)

---

## 1. System Architecture

### 1.1 Overview

```
┌──────────────────┐     HTTP/JSON (WebRequest)      ┌─────────────────────────┐
│   MT5 EA v6.00   │ ◄──────────────────────────────► │   FastAPI Server        │
│   (MQL5)         │                                  │   (Python, Docker)      │
│                  │  POST /analyze                   │                         │
│  - Screenshots   │  GET  /watch_trade               │  - Sonnet screening     │
│  - Market data   │  POST /confirm_entry             │  - Opus full analysis   │
│  - Zone watching │  GET  /pending_trade              │  - Haiku M1 confirm     │
│  - Trade exec    │  POST /trade_executed             │  - Market context       │
│  - Position mgmt │  POST /trade_closed               │  - News filter          │
└──────────────────┘                                  │  - Trade tracking       │
                                                      └──────────┬──────────────┘
                                                                 │
                                          ┌──────────────────────┼─────────────────────┐
                                          │                      │                     │
                                   Claude API            Telegram Bot          External APIs
                                   (Anthropic)           (alerts, cmds)        (free tier)
                                          │                      │                     │
                                   - Sonnet screen        - /scan, /stats      - CFTC COT
                                   - Opus analysis        - /context, /news    - Myfxbook
                                   - Haiku confirm        - Execute/Skip       - API Ninjas
                                   - Haiku review         - Trade alerts       - Yahoo Finance
                                                          - Weekly reports     - ForexFactory
```

### 1.2 Trade Lifecycle Flow

The system operates in a sequential pipeline with clear handoffs between the EA and server:

**Phase 1 — Analysis (Server-Driven)**

1. Kill zone starts (e.g., 08:00 MEZ for GBPJPY). EA captures D1/H4/H1/M5 chart screenshots.
2. EA sends screenshots + market data JSON to `POST /analyze`.
3. Server fetches fundamentals via Sonnet web search (cached daily in `fundamentals_cache.db`).
4. Server runs Sonnet screening on H1+M5 screenshots — quick viability check (~$0.40).
5. If screening says "setup possible," server runs Opus full analysis with extended thinking (~$1.00):
   - All 4 timeframes analyzed with ICT methodology
   - Market context injected (COT, retail sentiment, rate differential, intermarket — ~200-300 tokens)
   - 12-point ICT checklist scored
   - Entry zone, SL, TP1, TP2 calculated
6. Result sent to Telegram with full breakdown.
7. If checklist ≥ 7/12: auto-queued as a watch trade (no manual button needed).
8. If checklist 4-6/12: Telegram shows Execute/Skip buttons for manual decision.

**Phase 2 — Zone Watching (EA-Driven, Zero API Cost)**

9. EA polls `GET /watch_trade?symbol=GBPJPY` every 30 seconds.
10. When a watch is available, EA gets the entry zone (high/low) and monitors price locally.
11. EA checks news restriction before confirming — calls are cached server-side.
12. When price enters the zone: EA sends M1 screenshot to `POST /confirm_entry`.
13. Haiku reviews M1 price action for entry confirmation (~$0.05). Retail sentiment contrarian signal included.
14. If rejected: EA retries up to 10 times (5-min cooldown between attempts).
15. If confirmed: server queues the trade as "pending."

**Phase 3 — Execution (EA-Driven)**

16. EA polls `GET /pending_trade?symbol=GBPJPY` and receives execution parameters.
17. EA opens market order with calculated lot size (based on SL distance and risk %).
18. EA reports back via `POST /trade_executed` with ticket numbers and actual fill price.

**Phase 4 — Position Management (EA-Driven, Zero API Cost)**

19. EA manages the position entirely locally:
    - TP1: Closes adaptive % (40-60% based on confidence tier), moves SL to breakeven.
    - Trailing: Activates after TP1 hit, trails by configurable distance.
    - TP2: Closes remaining position at target.
20. On close: EA reports via `POST /trade_closed` with P&L, close reason, pips.
21. Server triggers post-trade Haiku review (~$0.01) — learning loop stored for future analyses.

---

## 2. API Endpoint Specifications

### 2.1 `POST /analyze` — Main Analysis Pipeline

**Called by:** MT5 EA at kill zone start

**Request:** Multipart form with:
- `screenshots`: 4 PNG files (D1, H4, H1, M5) — 1600×900 pixels
- `market_data`: JSON string with RSI, ATR, session levels, PDH/PDL, spread, etc.

**Response:**
```json
{
    "status": "setup_found",
    "symbol": "GBPJPY",
    "setups": [
        {
            "bias": "LONG",
            "entry_zone_high": 191.450,
            "entry_zone_low": 191.350,
            "sl": 191.100,
            "tp1": 191.800,
            "tp2": 192.300,
            "confidence": "HIGH",
            "checklist_score": 10,
            "checklist_items": [...],
            "reasoning": "...",
            "negative_factors": [...],
            "rr_tp1": 1.3,
            "rr_tp2": 2.9,
            "sl_pips": 35.0
        }
    ]
}
```

**Pipeline steps (inside the endpoint):**
1. Sonnet fundamentals fetch (web search, cached daily)
2. Sonnet screening (H1+M5 only)
3. If viable: Opus full analysis (all 4 timeframes + market context + extended thinking)
4. Result parsed, trade queued if auto-threshold met, Telegram notified

### 2.2 `GET /watch_trade?symbol=GBPJPY` — Poll for Watch

**Called by:** MT5 EA every 30 seconds during kill zone.

**Response (watch available):**
```json
{
    "status": "watch",
    "trade_id": "GBPJPY_20260219_083000",
    "bias": "LONG",
    "entry_zone_high": 191.450,
    "entry_zone_low": 191.350,
    "sl": 191.100,
    "tp1": 191.800,
    "tp2": 192.300,
    "confidence": "HIGH",
    "checklist_score": 10,
    "tp1_close_pct": 40
}
```

**Response (no watch):**
```json
{"status": "none"}
```

### 2.3 `POST /confirm_entry` — M1 Confirmation

**Called by:** EA when price enters the entry zone.

**Request:** Multipart form with M1 screenshot + trade context JSON.

**Response:**
```json
{
    "status": "confirmed",
    "entry_price": 191.410,
    "adjusted_sl": 191.100,
    "reasoning": "M1 shows bullish engulfing at FVG CE level..."
}
```
Or: `{"status": "rejected", "reasoning": "No clear M1 reaction yet..."}`

### 2.4 `GET /pending_trade?symbol=GBPJPY` — Poll for Confirmed Trade

**Called by:** EA after M1 confirmation.

**Response:**
```json
{
    "status": "pending",
    "trade_id": "GBPJPY_20260219_083000",
    "bias": "LONG",
    "entry_price": 191.410,
    "sl": 191.100,
    "tp1": 191.800,
    "tp2": 192.300,
    "lot_size_suggestion": 0.59,
    "tp1_close_pct": 40
}
```

### 2.5 `POST /trade_executed` — Execution Report

**Called by:** EA after opening the position.

```json
{
    "symbol": "GBPJPY",
    "trade_id": "GBPJPY_20260219_083000",
    "ticket_tp1": 12345678,
    "ticket_tp2": 12345679,
    "lots_tp1": 0.24,
    "lots_tp2": 0.35,
    "actual_entry": 191.412,
    "slippage_pips": 0.2
}
```

### 2.6 `POST /trade_closed` — Close Report

**Called by:** EA when position closes (TP, SL, or manual).

```json
{
    "symbol": "GBPJPY",
    "trade_id": "GBPJPY_20260219_083000",
    "ticket": 12345678,
    "close_price": 191.800,
    "profit": 92.50,
    "pips": 38.8,
    "close_reason": "TP1",
    "close_time": "2026-02-19T10:30:00Z"
}
```

**Server action:** Updates trade record in SQLite, triggers post-trade Haiku review, sends Telegram notification with P&L and review insight.

### 2.7 Public Endpoints (No Authentication)

| Endpoint | Purpose |
|----------|---------|
| `GET /public/trades` | JSON trade history |
| `GET /public/stats` | Aggregated performance stats |
| `GET /public/feed` | Full HTML P&L page |
| `GET /public/report/{year}/{month}` | Monthly PDF download |

### 2.8 Backtest Endpoints

| Endpoint | Purpose |
|----------|---------|
| `POST /backtest/import` | Upload M1 CSV historical data |
| `POST /backtest/run` | Run batch backtest |
| `POST /backtest/test` | Test single setup against one date |
| `GET /backtest/runs` | List backtest runs |
| `GET /backtest/results/{id}` | Get backtest report |
| `GET /backtest/history_stats` | Historical data availability |

---

## 3. Authentication

All EA→Server communication is authenticated via API key:

- **Server side:** `API_KEY` environment variable in `server/.env`
- **EA side:** `InpApiKey` input parameter
- **Header:** `X-API-Key: <key>` on every request
- **Exempt endpoints:** `/health`, `/public/*` (no auth required)

Server middleware validates the key and returns 401 if missing/invalid.

---

## 4. Market Context Integration

Every Opus analysis is enriched with live macro/sentiment data from free external APIs. This adds ~200-300 tokens to the prompt at negligible cost.

### 4.1 Data Sources

| Source | API | Data | Update Freq | Pair Coverage |
|--------|-----|------|-------------|---------------|
| COT Positioning | CFTC Socrata (free) | Speculator net positions, week-over-week change | Weekly (cached 24h) | All 6 currencies + Gold |
| Retail Sentiment | Myfxbook (free) | % long/short, contrarian signal | Live (cached 4h) | All major pairs |
| Rate Differential | API Ninjas → FRED fallback | Central bank rates, carry trade spread | Monthly (cached 24h) | All except XAU |
| Intermarket | Yahoo Finance (free) | Equity indices, DXY, US10Y, VIX | Live (cached 2h) | Pair-specific tickers |

### 4.2 Pair-Specific Intermarket Logic

Different pairs correlate with different instruments:

- **JPY pairs (GBPJPY, USDJPY, EURJPY):** Nikkei 225 — risk-on sentiment weakens JPY
- **GBP pairs (GBPJPY, GBPUSD):** FTSE 100 — strong FTSE = strong GBP
- **EUR pairs (EURUSD, EURJPY):** DAX — strong DAX = strong EUR
- **Gold (XAUUSD):** VIX (fear gauge) + DXY inverse — strong USD = bearish gold, rising VIX = bullish gold
- **All pairs:** DXY (USD index) + US 10-year yield (rate expectations)

### 4.3 Interpretation in Opus Prompt

The market context is injected as a structured text block before the screenshot analysis. The prompt includes interpretation guidance:

- COT: Large speculator positioning shifts can foreshadow reversals
- Retail sentiment: >65% one-sided = contrarian signal (fade the crowd)
- Rate differential: Positive carry favors base currency in trending conditions
- Intermarket: Risk sentiment alignment confirms or contradicts chart bias

### 4.4 Graceful Degradation

All external API calls are wrapped in try/except. If any source fails, analysis proceeds without it. The cache system (separate `market_context_cache.db`) ensures stale data is available even during API outages.

---

## 5. News Filter (FTMO Compliance)

### 5.1 Rule

FTMO prohibits opening or closing positions within 2 minutes before or after high-impact news events affecting the traded instrument.

### 5.2 Implementation

- **Source:** ForexFactory economic calendar (JSON feed, cached 6 hours)
- **Blocking window:** ±2 minutes around high-impact events (configurable)
- **Warning window:** 30 minutes ahead — setups show a warning if news is imminent
- **Currency matching:** Events matched to the pair's base and quote currencies via `pair_profiles.py`

### 5.3 Integration Points

- `check_news_restriction(symbol)` called before M1 confirmation
- `get_upcoming_news(symbols)` used by `/news` Telegram command
- Warning flag included in Opus analysis if news is within 30 minutes

---

## 6. Portfolio & Risk Management

### 6.1 Correlation Filter

The server prevents conflicting trades on correlated pairs. Before queuing a trade, it checks all active watches and open trades:

- GBPJPY + GBPUSD: High positive correlation (shared GBP)
- GBPJPY + USDJPY: High positive correlation (shared JPY)
- GBPJPY + EURJPY: Moderate positive (shared JPY)
- Opposing bias on correlated pairs → trade blocked

### 6.2 Risk Limits

| Parameter | Default | Config |
|-----------|---------|--------|
| Max daily drawdown | 3% | `MAX_DAILY_DRAWDOWN_PCT` |
| Max open trades | 2 | `MAX_OPEN_TRADES` |
| SL hard cap | 70 pips | Enforced in Opus prompt |
| Min R:R (TP1) | 1:1.2 | Enforced in Opus prompt |
| Min R:R (TP2) | 1:2 | Enforced in Opus prompt |

### 6.3 Adaptive Position Sizing

TP1 close percentage adapts based on the 4-tier confidence scoring:

| Confidence | Checklist Score | TP1 Close % | Logic |
|------------|----------------|-------------|-------|
| HIGH | 10-12/12 | 40% | High conviction → let more ride to TP2 |
| MEDIUM-HIGH | 8-9/12 | 45% | Good setup → slightly more at TP1 |
| MEDIUM | 6-7/12 | 55% | Moderate setup → secure more profit early |
| LOW | 4-5/12 | 60% | Lower conviction → take more off the table |

After TP1 closes, SL moves to breakeven on the remaining position.

---

## 7. Learning Loop (Post-Trade Reviews)

### 7.1 How It Works

After every closed trade (win or loss), a Haiku review runs automatically:

1. Trade outcome data sent to Haiku (~$0.01 per review)
2. Haiku compares: predicted bias vs actual outcome, checklist score vs result, entry quality
3. Review stored in `post_trade_reviews` table
4. Last 5 reviews for the pair injected into the next Opus analysis as performance feedback
5. Review summary sent to Telegram as "Post-Trade Insight"

### 7.2 Performance Feedback

The Opus prompt receives a structured feedback section including:

- Recent trade outcomes (win rate, avg P&L)
- Pattern analysis: win rates by confidence tier, trend alignment, entry status
- Counter-trend vs trend-aligned performance
- Post-trade review insights from Haiku

This creates a learning loop where past mistakes directly inform future analyses.

---

## 8. Database Schema

Three SQLite databases in `/data/`, all using WAL mode for concurrent reads:

### 8.1 `trades.db` — Main Database

**`trades` table** (~35 columns):
- Trade identity: `trade_id`, `symbol`, `bias`, `confidence`, `checklist_score`
- Prices: `entry_zone_high/low`, `sl`, `tp1`, `tp2`, `actual_entry`
- Execution: `ticket_tp1/tp2`, `lots_tp1/tp2`, `status` (queued→executed→closed)
- Outcome: `close_price`, `profit`, `pips`, `close_reason`
- AI data: `reasoning`, `negative_factors`, `checklist_items`, `analysis_model`
- Learning: `trend_alignment`, `entry_status`, `price_zone`, `m1_confirmations_used`

**`screening_stats`:** Sonnet screening pass/fail tracking per symbol per day.

**`post_trade_reviews`:** Haiku insights linked to trade_id.

**`watch_trades_persist`:** Active watches that survive Docker restarts.

**`scan_metadata`:** Tracks when each symbol was last scanned.

**`backtest_runs` / `backtest_trades`:** Backtest execution results.

**`historical_ohlc`:** Imported M1/M5/H1/H4/D1 candle data for backtesting.

### 8.2 `fundamentals_cache.db`

Stores daily Sonnet web search results per symbol. Avoids duplicate API calls when re-scanning the same pair within a day.

### 8.3 `market_context_cache.db`

Stores external data with per-source TTL. Separate from trades.db to avoid lock contention during high-frequency cache reads.

---

## 9. Telegram Bot

### 9.1 Commands

| Command | Description |
|---------|-------------|
| `/scan [SYMBOL]` | Trigger analysis (uses cached screenshots if available) |
| `/stats [SYMBOL] [DAYS]` | Performance statistics with optional filters |
| `/context [SYMBOL]` | Live market context (COT, sentiment, rates, intermarket) |
| `/report` | Weekly performance breakdown by pattern |
| `/drawdown` | Daily P&L and risk status dashboard |
| `/news` | Upcoming high-impact news events (next 24h) |
| `/backtest` | Backtest results and historical data stats |
| `/reset` | Force-close stale open trades in DB |
| `/status` | Bot status for all active pairs |
| `/help` | List all commands |

### 9.2 Interactive Buttons

- **Execute / Skip:** Shown for setups with checklist 4-6/12 (below auto-threshold)
- **Force Execute / Dismiss:** Shown when M1 confirmation is rejected (manual override)

### 9.3 Automated Notifications

- Setup found (with full ICT breakdown)
- Watch queued / expired
- M1 confirmation result
- Trade executed (with ticket numbers)
- Trade closed (with P&L + post-trade review insight)
- Weekly performance report (Sunday 19:00 MEZ)
- Monthly PDF report (1st of month, 08:00 MEZ)

---

## 10. MT5 EA Configuration

### 10.1 Input Parameters (v6.00)

| Input | Default | Purpose |
|-------|---------|---------|
| `InpServerURL` | `http://46.225.66.110:8000/analyze` | Full /analyze endpoint URL |
| `InpServerBase` | `http://46.225.66.110:8000` | Base URL for all other endpoints |
| `InpApiKey` | `""` | Must match `API_KEY` in server .env |
| `InpKillZoneStart` | 8 | Kill zone start hour (MEZ) |
| `InpKillZoneEnd` | 20 | Kill zone end hour (MEZ) |
| `InpTimezoneOffset` | 1 | Server time offset from MEZ |
| `InpScreenshotWidth` | 1600 | Screenshot width (pixels) |
| `InpScreenshotHeight` | 900 | Screenshot height (pixels) |
| `InpRiskPercent` | 1.0 | Risk % per trade |
| `InpMagicNumber` | 888888 | Magic number for trade identification |
| `InpConfirmCooldown` | 300 | Seconds between M1 confirmation attempts |
| `InpMode` | `"leader"` | `"leader"` (analyze+trade) or `"follower"` (trade only) |
| `InpSymbolOverride` | `""` | Map broker symbol to server symbol |

### 10.2 MT5 URL Whitelist

Only one URL needs to be whitelisted in MT5 → Tools → Options → Expert Advisors → Allow WebRequest:

```
http://46.225.66.110:8000
```

This single base URL covers all endpoints.

### 10.3 EA States

The EA operates as a state machine:

1. **IDLE** — Waiting for kill zone
2. **SCANNING** — Kill zone active, capturing screenshots and sending to server
3. **WATCHING** — Monitoring entry zone (polls `/watch_trade` every 30s)
4. **CONFIRMING** — Price in zone, sending M1 screenshots for confirmation
5. **EXECUTING** — Opening positions based on confirmed trade
6. **MANAGING** — Position open, managing TP1/TP2/trailing/breakeven
7. **CLOSED** — Position closed, reporting to server

---

## 11. Deployment

### 11.1 Server (Docker on Hetzner VPS)

```bash
ssh root@46.225.66.110
cd AI-Trade-Bot-ICT
git pull origin main
docker-compose build --no-cache && docker-compose down && docker-compose up -d
```

Docker volumes:
- `./data:/data` — SQLite databases (trades.db, fundamentals_cache.db, market_context_cache.db)
- `./logs:/app/logs` — Application logs

Port 8000 only. Port 8001 is a separate project — do not touch.

### 11.2 Environment Variables (`server/.env`)

```
# Required
ANTHROPIC_API_KEY=sk-ant-...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
API_KEY=...                             # EA authentication

# Trading config
ACTIVE_PAIRS=GBPJPY                     # Comma-separated
ANALYSIS_MODEL=claude-opus-4-20250514   # Or claude-sonnet-4-5-20250929
MAX_DAILY_DRAWDOWN_PCT=3.0
MAX_OPEN_TRADES=2

# External data (optional, all have fallbacks)
API_NINJAS_KEY=...                      # Free tier, 10K req/month
FRED_API_KEY=...                        # Free, FRED fallback for rates

# Public feed (optional)
PUBLIC_TELEGRAM_CHANNEL=...
GOOGLE_SHEETS_CREDENTIALS_PATH=...
GOOGLE_SHEETS_SPREADSHEET_ID=...
```

### 11.3 EA Deployment

Copy `mt5/AI_Analyst.mq5` to the Windows MT5 machine at `MQL5/Experts/AI_Analyst.mq5`, compile in MetaEditor (F7), and attach to the desired chart. Set `InpSymbolOverride` if the broker uses a symbol suffix (e.g., "GBPJPYm" → "GBPJPY").

---

## 12. Cost Breakdown

| Component | Cost | Frequency |
|-----------|------|-----------|
| Sonnet fundamentals fetch | ~$0.10 | Once per pair per day |
| Sonnet screening | ~$0.40 | Per scan |
| Opus full analysis | ~$1.00 | Per viable setup |
| Haiku M1 confirmation | ~$0.03-0.08 | Per attempt (max 10) |
| Haiku post-trade review | ~$0.01 | Per closed trade |
| External data APIs | $0.00 | All free tier |
| **Typical daily cost (1 pair)** | **~$1.50-2.00** | |

Optimizations applied: JPEG compression, 1600×900 screenshots, OHLC arrays removed (screenshots sufficient), reduced thinking budget (6K), reduced max_tokens (8K), prompt condensed ~30%.

---

## 13. Adding a New Pair

1. Add pair profile in `server/pair_profiles.py` with: session context template, kill zone hours, search queries, fundamental bias options, typical spread
2. Add to `ACTIVE_PAIRS` env var on VPS (comma-separated)
3. Attach EA to new chart in MT5 with appropriate `InpKillZoneStart/End` for the pair's session
4. Market context module auto-detects pair-specific intermarket indicators via `pair_profiles.py`
5. News filter auto-matches high-impact events to the pair's currencies

Unknown pairs get sensible defaults from `get_profile()` — JPY pairs get 3 digits, gold gets 2 digits, everything else gets 5 digits.
