# CLAUDE.md — Project Context for Claude Code

## What This Project Is

An automated multi-pair forex trading system (v3.0) that uses ICT (Inner Circle Trader) methodology. A MetaTrader 5 Expert Advisor captures chart screenshots, sends them to a Python FastAPI server which calls Claude's API for analysis enriched with live macro/sentiment data, delivers trade setups via Telegram, and manages the full trade lifecycle including smart zone-based entry with M1 confirmation.

**Active pairs:** GBPJPY, EURUSD, GBPUSD, XAUUSD, USDJPY, EURJPY (configurable via `ACTIVE_PAIRS` env var)
**Sessions:** Per-pair kill zones defined in `pair_profiles.py` (e.g., London Kill Zone 08:00-20:00 MEZ for GBPJPY)

## System Flow

```
Kill Zone Start: EA captures D1/H4/H1/M5 screenshots + market data
  → POST /analyze
  → Sonnet fundamentals fetch (web search, cached daily, ~$0.10)
  → Sonnet screening (H1+M5, ~$0.40) — quick viability check
  → If setup found: Opus full analysis (all timeframes + market context, ~$1.00)
      Market context injected: COT positioning, retail sentiment, rate differential, intermarket data
  → Telegram alert with ICT checklist score, R:R, confluence
  → Auto-watch if checklist ≥7/12 (no manual button needed)
  → EA monitors entry zone locally (zero API cost)
  → When zone reached: M1 Haiku confirmation (~$0.05, max 10 attempts)
      Retail sentiment contrarian signal passed to Haiku
  → If confirmed: market order execution
  → TP1 closes adaptive % (40-60% based on checklist score), break-even, trail to TP2
  → Trade close reported to SQLite + Telegram
  → Post-trade Haiku review (~$0.01) — learning loop for future analyses
  → Screenshots archived to /data/screenshots/ for backtesting
  → Watches expire at kill zone end (persisted across restarts)
  → Startup: alerts if scan missed, restores watches from DB
  → Weekly performance report auto-sent Sunday 19:00 MEZ
  → Monthly PDF report auto-sent 1st of month 08:00 MEZ
```

## File Map

### Server (Python — `/server/`) — 16 files, ~8,300 lines

| File | Purpose | Lines |
|------|---------|-------|
| `main.py` | FastAPI app, all endpoints, watch system, trade queue, background tasks (expiry, reports) | ~1250 |
| `analyzer.py` | Claude API — Sonnet screening, Opus analysis (extended thinking + streaming + market context), Haiku M1 confirmation, post-trade review | ~1040 |
| `telegram_bot.py` | Telegram commands (/scan, /stats, /context, /news, /drawdown, /report, /reset, /status, /backtest, /help), Execute/Skip/Force buttons, all notifications | ~1300 |
| `trade_tracker.py` | SQLite DB — trade lifecycle (queued→executed→closed), P&L tracking, performance stats, correlation checks, screening stats, post-trade reviews | ~940 |
| `market_context.py` | External macro data — COT positioning (CFTC), retail sentiment (Myfxbook), interest rate differential (API Ninjas/FRED), intermarket indicators (Yahoo Finance) | ~780 |
| `news_filter.py` | FTMO-compliant news filter — ForexFactory calendar, ±2 min blocking around high-impact events | ~235 |
| `pair_profiles.py` | Per-pair config — digits, spreads, kill zone times, session context prompts, search queries | ~180 |
| `models.py` | Pydantic models — MarketData, TradeSetup, WatchTrade, PendingTrade, AnalysisResult, TradeExecutionReport, TradeCloseReport, BacktestRequest, TestSetupRequest | ~220 |
| `public_feed.py` | Public P&L transparency — trade history endpoint, Google Sheets sync, public Telegram channel | ~380 |
| `backtest.py` | Backtest orchestration — run batch backtests against historical data | ~540 |
| `trade_simulator.py` | M1 OHLC simulation engine for backtesting | ~360 |
| `historical_data.py` | Historical OHLC data import (CSV), resampling (M1→M5/H1/H4/D1), querying | ~330 |
| `backtest_report.py` | Backtest report generation — performance metrics, pattern analysis | ~375 |
| `monthly_report.py` | Monthly PDF performance report generator (ReportLab) | ~320 |
| `config.py` | Environment variables (API keys, limits, API_KEY for auth, ANALYSIS_MODEL, external data keys) | ~35 |
| `shared_state.py` | Shared mutable state (last_market_data) — breaks circular import between main↔telegram_bot | ~15 |

### MT5 (MQL5 — `/mt5/`)
| File | Purpose |
|------|---------|
| `AI_Analyst.mq5` | EA v6.00 — screenshot capture (1600x900), zone watching, M1 confirmation, trade execution, adaptive TP1 close %, leader/follower mode |
| `SwingLevels.mq5` | Indicator v2.00 — draws swing high/low horizontal lines on chart (up to 15 levels, auto-removes broken) |
| `GBPJPY_Analyst.mq5` | Legacy EA (deprecated, kept for reference) |

### Docs (`/docs/`)
- `ICT_Strategy_Research.docx` — ICT methodology research
- `Integration_Notes.md` — Architecture decisions, endpoint specs, correlation management
- `MQL5_Implementation_Spec.md` — EA specification, algorithms, pseudocode

## Key API Endpoints

| Method | Path | Called By | Purpose |
|--------|------|-----------|---------|
| POST | `/analyze` | MT5 EA | Send screenshots + market data, triggers full analysis pipeline |
| GET | `/watch_trade?symbol=GBPJPY` | MT5 EA | Poll for entry zone to monitor |
| POST | `/confirm_entry` | MT5 EA | M1 screenshot for Haiku confirmation |
| GET | `/pending_trade?symbol=GBPJPY` | MT5 EA | Poll for confirmed trade to execute |
| POST | `/trade_executed` | MT5 EA | Report trade execution (tickets, lots) |
| POST | `/trade_closed` | MT5 EA | Report position close (TP/SL, P&L), triggers post-trade review |
| GET | `/health` | Monitoring | Server status, active watches/trades |
| GET | `/stats` | Telegram/API | Performance statistics |
| GET | `/scan` | Manual trigger | Re-run analysis with cached screenshots |
| POST | `/backtest/import` | API | Upload M1 CSV for backtesting |
| POST | `/backtest/run` | API | Run batch backtest |
| POST | `/backtest/test` | API | Test single setup against one date |
| GET | `/backtest/runs` | API | List backtest runs |
| GET | `/backtest/results/{id}` | API | Get backtest report |
| GET | `/backtest/history_stats` | API | Historical data availability |
| GET | `/public/trades` | Website/API | Public trade history (no auth) |
| GET | `/public/stats` | Website/API | Public performance stats (no auth) |
| GET | `/public/feed` | Browser | Full HTML P&L page |
| GET | `/public/report/{year}/{month}` | Browser | Monthly PDF download |

## Claude Models Used

| Model | Use | Cost |
|-------|-----|------|
| Sonnet (latest) | Fundamentals fetch (web search, cached daily) | ~$0.10/day |
| Sonnet (latest) | Tier 1 screening — H1+M5 quick check (with prompt caching) | ~$0.40/scan |
| Opus (`claude-opus-4-20250514`) | Tier 2 full analysis — D1+H4+H1+M5 + market context + extended thinking (6K budget) | ~$1.00/analysis |
| Haiku 4.5 (`claude-haiku-4-5-20251001`) | M1 confirmation — reaction check when price reaches zone | ~$0.03-0.08/check |
| Haiku 4.5 (`claude-haiku-4-5-20251001`) | Post-trade review — learning loop after each closed trade | ~$0.01/review |

**A/B testing**: Set `ANALYSIS_MODEL` env var to switch Opus↔Sonnet 4.5 for tier 2 analysis.

## Market Context System (`market_context.py`)

External data injected into every Opus analysis (~200-300 tokens, ~$0.01 extra). All APIs are FREE.

| Data Source | API | Cache | What It Provides |
|-------------|-----|-------|------------------|
| COT Positioning | CFTC Socrata API | 24h | Speculator net long/short for base & quote currency futures, week-over-week change |
| Retail Sentiment | Myfxbook Community Outlook | 4h | % long/short, crowd bias, contrarian signal |
| Interest Rate Differential | API Ninjas → FRED fallback | 24h | Central bank rates, spread in bps, carry trade status |
| Intermarket Indicators | Yahoo Finance | 2h | Pair-specific indices (Nikkei/FTSE/DAX/VIX/DXY/US10Y), risk sentiment |

**Pair-specific intermarket logic:**
- JPY pairs → Nikkei 225 (risk-on = JPY weak)
- GBP pairs → FTSE 100 (FTSE up = GBP strong)
- EUR pairs → DAX (DAX up = EUR strong)
- XAU/Gold → VIX + DXY inverse (strong USD = bearish gold, rising VIX = bullish gold)
- All pairs → DXY + US 10Y yield

**Env vars** (optional, all have fallbacks):
- `API_NINJAS_KEY` — free tier, 10K requests/month (for interest rates)
- `FRED_API_KEY` — free (Federal Reserve data, fallback for rates)

## ICT Analysis Methodology

The Opus prompt in `analyzer.py` follows this ICT structure:
1. **D1** — Strategic bias (HH/HL vs LL/LH structure)
2. **H4** — Tactical OTE zone (62-79% Fib), Premium/Discount, Order Blocks (max 8 candles)
3. **H1** — Key levels, BOS/ChoCH, Order Blocks (max 30 candles), swing structure
4. **M5** — Entry triggers: MSS (≥15 pip displacement), FVGs (≥15 pips), liquidity sweeps
5. **Session Context** — Per-pair: London Kill Zone for GBPJPY, London & NY Overlap for EURUSD/GBPUSD, etc.

**12-Point ICT Checklist** (scored per setup):
1. D1 bias identified
2. H4 aligns with D1
3. Correct Premium/Discount zone
4. Active Order Block within validity
5. MSS confirmed on M5 (≥15 pip displacement)
6. FVG present (meets minimum size)
7. Entry at CE level (50% FVG midpoint)
8. OTE zone (62-79%) alignment
9. Liquidity sweep detected
10. SL within 70 pip cap
11. R:R ≥1:2 on TP2
12. No conflicting news within 30 min

**4-Tier Scoring:** ≥10 = HIGH, 8-9 = MEDIUM-HIGH, 6-7 = MEDIUM, 4-5 = LOW. Below 4 = no setup.
**Auto-queue threshold:** ≥7/12 (skip Execute button, go straight to watch)
**Adaptive TP1 close %:** HIGH=40%, MED-HIGH=45%, MEDIUM=55%, LOW=60%

## Learning Loop (Post-Trade Reviews)

After every closed trade:
1. Haiku reviews the trade outcome, checklist score, entry status, negative factors (~$0.01)
2. Review stored in `post_trade_reviews` SQLite table
3. Last 5 reviews injected into next Opus analysis via performance feedback
4. Review sent to Telegram as "Post-Trade Insight"

Performance feedback also includes pattern analysis: win rates by confidence tier, trend alignment, entry status, price zone, and counter-trend vs trend-aligned trades.

## Risk Management

- **FTMO news filter**: Blocks trades ±2 min around high-impact news events (ForexFactory calendar)
- **Daily drawdown**: Default 3% limit (configurable via `MAX_DAILY_DRAWDOWN_PCT`)
- **Max open trades**: Default 2 (configurable via `MAX_OPEN_TRADES`)
- **Correlation filter**: Prevents conflicting trades on correlated pairs
- **SL hard cap**: 70 pips maximum
- **R:R minimum**: 1:1.2 on TP1, 1:2 on TP2
- **Market context alignment**: COT/sentiment can lower confidence by 1 tier if opposing chart bias

## Database Schema

Three SQLite databases in `/data/`:

**trades.db** — Main database (WAL mode):
- `trades` — 35+ columns: full trade lifecycle from queued→executed→closed, P&L, AI reasoning, checklist scores, trend alignment, entry status, negative factors, m1_confirmations_used, analysis_model
- `scan_metadata` — Daily scan timestamps per symbol
- `watch_trades_persist` — Active watches (survive Docker restarts)
- `screening_stats` — Sonnet screening pass/fail rates
- `post_trade_reviews` — Haiku post-trade insights
- `backtest_runs` / `backtest_trades` — Backtest results
- `historical_ohlc` — Imported M1/M5/H1/H4/D1 candle data

**fundamentals_cache.db** — Daily Sonnet web search results (avoids duplicate API calls)

**market_context_cache.db** — COT, sentiment, rates, intermarket data (per-source TTL)

## Telegram Commands

| Command | Purpose |
|---------|---------|
| `/scan` or `/scan GBPJPY` | Trigger re-analysis for a pair |
| `/stats` or `/stats GBPJPY 7` | Performance stats (optional pair/days filter) |
| `/context` or `/context EURUSD` | Show live market context (COT, sentiment, rates, intermarket) |
| `/report` | Weekly performance breakdown by pattern |
| `/drawdown` | Daily P&L and risk status dashboard |
| `/news` | Upcoming high-impact news events (24h) |
| `/backtest` | Show backtest results and historical data stats |
| `/reset` | Force-close stale open trades in DB |
| `/status` | Bot status for all active pairs |
| `/help` | Show all commands |

Inline buttons: Execute/Skip (for non-auto-queued setups), Force Execute/Dismiss (on M1 rejection)

## Deployment

- **VPS**: Hetzner (46.225.66.110), Docker container `ict-tradebot` on port 8000
- **Build**: `docker-compose build --no-cache && docker-compose down && docker-compose up -d`
- **SQLite volume**: `./data:/data` (persistent trades.db, fundamentals_cache.db, market_context_cache.db)
- **Logs**: `./logs:/app/logs`
- **Note**: VPS also runs a separate project (LongEntry Market Scanner) on port 8001 via systemd+nginx — do NOT touch port 8001 or nginx config

### Environment Variables (`server/.env`)
```
ANTHROPIC_API_KEY=sk-ant-...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
API_KEY=...                          # MT5 EA authentication
ACTIVE_PAIRS=GBPJPY                  # Comma-separated: GBPJPY,EURUSD,GBPUSD
ANALYSIS_MODEL=claude-opus-4-20250514  # Or claude-sonnet-4-5-20250929 for A/B test
MAX_DAILY_DRAWDOWN_PCT=3.0
MAX_OPEN_TRADES=2
API_NINJAS_KEY=...                   # Optional — free tier for interest rates
FRED_API_KEY=...                     # Optional — FRED fallback for rates
PUBLIC_TELEGRAM_CHANNEL=...          # Optional — public P&L channel
GOOGLE_SHEETS_CREDENTIALS_PATH=...   # Optional — Google Sheets sync
GOOGLE_SHEETS_SPREADSHEET_ID=...     # Optional — Google Sheets ID
```

## Common Tasks

### Updating the server (Python changes only)
```bash
ssh root@46.225.66.110
cd AI-Trade-Bot-ICT
git pull origin main
docker-compose build --no-cache && docker-compose down && docker-compose up -d
```

### Updating the EA (MQL5 changes)
Copy `mt5/AI_Analyst.mq5` to the Windows MT5 machine at `MQL5/Experts/AI_Analyst.mq5`, compile in MetaEditor (F7), reattach to chart.

### Adding a new pair
1. Add profile in `server/pair_profiles.py` with session context, kill zone hours, search queries
2. Add to `ACTIVE_PAIRS` env var on VPS
3. Attach EA to new chart in MT5 with `InpSymbolOverride` if broker uses suffix (e.g., "GBPJPYm" → "GBPJPY")

### Adding a new endpoint
1. Add the route in `server/main.py`
2. Add any new models to `server/models.py`
3. If it sends Telegram notifications, add the function in `server/telegram_bot.py`
4. If the EA needs to call it, update `mt5/AI_Analyst.mq5` and add the URL handling

## EA Input Parameters (v6.00)

| Input | Default | Purpose |
|-------|---------|---------|
| `InpServerURL` | `http://46.225.66.110:8000/analyze` | Full /analyze endpoint URL |
| `InpServerBase` | `http://46.225.66.110:8000` | Base URL for all other endpoints |
| `InpKillZoneStart` | 8 | Kill Zone start hour (MEZ) |
| `InpKillZoneEnd` | 20 | Kill Zone end hour (MEZ) |
| `InpTimezoneOffset` | 1 | UTC offset (Server - MEZ) |
| `InpScreenshotWidth` | 1600 | Screenshot width (pixels) |
| `InpScreenshotHeight` | 900 | Screenshot height (pixels) |
| `InpRiskPercent` | 1.0 | Risk % per trade |
| `InpMagicNumber` | 888888 | Magic number for trade identification |
| `InpConfirmCooldown` | 300 | Seconds between M1 confirmation attempts |
| `InpMode` | "leader" | "leader" (analyze+trade) or "follower" (trade only) |
| `InpSymbolOverride` | "" | Map broker symbol to server symbol (e.g., "GBPJPYm" → "GBPJPY") |
| `InpApiKey` | "" | Must match `API_KEY` in server .env |

## Known Patterns & Gotchas

- **String concat bug**: Never use `f"title\n" f"\u2501" * 20` — Python implicit string concat happens before `*`, repeating the title. Always use explicit `+` before the separator: `f"title\n" + "\u2501" * 20`
- **Shared state pattern**: `shared_state.py` holds mutable data (like `last_market_data`) that both `main.py` and `telegram_bot.py` need — avoids circular imports. `main.py` still uses lazy imports for telegram_bot notification functions inside endpoint handlers
- **docker-compose v1**: The VPS uses `docker-compose` (hyphenated), not `docker compose`
- **KeyError 'ContainerConfig'**: Run `docker-compose down` first, then `up -d`
- **API authentication**: Set `API_KEY` in `server/.env` and `InpApiKey` in EA inputs — server middleware checks `X-API-Key` header. Health and public endpoints are exempt
- **MT5 allowed URLs**: Only the base URL `http://46.225.66.110:8000` is needed — covers all endpoints
- **MEZ timezone**: `datetime.now(timezone(timedelta(hours=1)))` — UTC+1 for CET
- **Market context graceful degradation**: If any external API fails, analysis proceeds without that data source. All fetches are wrapped in try/except with individual fallbacks
- **Cache databases**: market_context_cache.db and fundamentals_cache.db are separate from trades.db to avoid lock contention. Each has its own TTL management
- **Gold pairs**: XAU has no central bank rate — `fetch_rate_differential` returns None, intermarket data provides VIX + DXY inverse correlation instead
- **Post-trade review**: Only triggers for trades with outcome in (full_win, partial_win, loss) — not for cancelled/failed trades
