# CLAUDE.md — Project Context for Claude Code

## What This Project Is

An automated GBPJPY forex trading system (v3.0) that uses ICT (Inner Circle Trader) methodology. A MetaTrader 5 Expert Advisor captures chart screenshots, sends them to a Python FastAPI server which calls Claude's API for analysis, delivers trade setups via Telegram, and manages the full trade lifecycle including smart zone-based entry with M1 confirmation.

**Active pair:** GBPJPY only
**Session:** London Kill Zone — analysis at 08:00 MEZ, watches until 20:00 MEZ

## System Flow

```
08:00 MEZ: EA captures D1/H4/H1/M5 screenshots + market data
  → POST /analyze
  → Sonnet screening (H1+M5, ~$0.40) — quick viability check
  → If setup found: Opus full analysis (all timeframes + web search, ~$2.00)
  → Telegram alert with ICT checklist score, R:R, confluence
  → Auto-watch if checklist ≥7/12 (no manual button needed)
  → EA monitors entry zone locally (zero API cost)
  → When zone reached: M1 Haiku confirmation (~$0.05, max 3 attempts)
  → If confirmed: market order execution
  → TP1 closes 50%, break-even, trail to TP2
  → Trade close reported to SQLite + Telegram
  → Watches expire at 20:00 MEZ
```

## File Map

### Server (Python — `/server/`)
| File | Purpose | Lines |
|------|---------|-------|
| `main.py` | FastAPI app, endpoints, watch system, trade queue, background expiry loop | ~660 |
| `analyzer.py` | Claude API calls — Sonnet screening, Opus analysis (extended thinking + streaming), Haiku M1 confirmation | ~1000 |
| `telegram_bot.py` | Telegram commands (/scan, /stats, /news, /drawdown, /reset, /status, /help), Execute/Skip buttons, watch notifications | ~920 |
| `trade_tracker.py` | SQLite DB — trade lifecycle (queued→executed→closed), P&L tracking, performance stats, correlation checks | ~590 |
| `news_filter.py` | FTMO-compliant news filter — ForexFactory calendar, ±2 min blocking around high-impact events | ~235 |
| `pair_profiles.py` | Per-pair config — digits, spreads, kill zone times, search queries | ~130 |
| `models.py` | Pydantic models — MarketData, TradeSetup, WatchTrade, PendingTrade, AnalysisResult, TradeExecutionReport | ~150 |
| `config.py` | Environment variables (API keys, limits) | ~15 |

### MT5 (MQL5 — `/mt5/`)
| File | Purpose |
|------|---------|
| `AI_Analyst.mq5` | EA v6.00 — screenshot capture, zone watching, M1 confirmation, trade execution, position management |
| `SwingLevels.mq5` | Indicator v2.00 — draws swing high/low horizontal lines on chart |
| `GBPJPY_Analyst.mq5` | Legacy EA (deprecated, kept for reference) |

### Docs (`/docs/`)
- `GBPJPY_ICT_Strategy_Research.docx` — ICT methodology research
- `Integration_Notes.md` — Architecture decisions
- `MQL5_Implementation_Spec.md` — EA specification

## Key API Endpoints

| Method | Path | Called By | Purpose |
|--------|------|-----------|---------|
| POST | `/analyze` | MT5 EA | Send screenshots + market data, triggers analysis |
| GET | `/watch_trade?symbol=GBPJPY` | MT5 EA | Poll for entry zone to monitor |
| POST | `/confirm_entry` | MT5 EA | M1 screenshot for Haiku confirmation |
| GET | `/pending_trade?symbol=GBPJPY` | MT5 EA | Poll for confirmed trade to execute |
| POST | `/trade_executed` | MT5 EA | Report trade execution (tickets, lots) |
| POST | `/trade_closed` | MT5 EA | Report position close (TP/SL, P&L) |
| GET | `/health` | Monitoring | Server status, active watches/trades |
| GET | `/stats` | Telegram/API | Performance statistics |
| GET | `/scan` | Manual trigger | Re-run analysis with cached screenshots |

## Claude Models Used

| Model | Use | Cost |
|-------|-----|------|
| Sonnet (latest) | Tier 1 screening — H1+M5 quick check | ~$0.40/scan |
| Opus (`claude-opus-4-20250514`) | Tier 2 full analysis — D1+H4+H1+M5 + extended thinking + web search | ~$2.00/analysis |
| Haiku 4.5 (`claude-haiku-4-5-20251001`) | M1 confirmation — reaction check when price reaches zone | ~$0.03-0.08/check |

## ICT Analysis Methodology

The Opus prompt in `analyzer.py` follows this ICT structure:
1. **D1** — Strategic bias (HH/HL vs LL/LH structure)
2. **H4** — Tactical OTE zone (62-79% Fib), Premium/Discount, Order Blocks (max 8 candles)
3. **H1** — Key levels, BOS/ChoCH, Order Blocks (max 30 candles), swing structure
4. **M5** — Entry triggers: MSS (≥15 pip displacement), FVGs (≥15 pips), liquidity sweeps
5. **London Kill Zone** — Asian range sweep patterns, PDH/PDL targeting, 08:00-09:30 optimal window

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

**Scoring:** ≥10 = HIGH confidence, 7-9 = MEDIUM, 4-6 = LOW, <4 = no setup
**Auto-queue threshold:** ≥7/12 (skip Execute button, go straight to watch)

## Risk Management

- **FTMO news filter**: Blocks trades ±2 min around high-impact news events (ForexFactory calendar)
- **Daily drawdown**: Default 3% limit (configurable via `MAX_DAILY_DRAWDOWN_PCT`)
- **Max open trades**: Default 2 (configurable via `MAX_OPEN_TRADES`)
- **Correlation filter**: Prevents conflicting trades on correlated pairs
- **SL hard cap**: 70 pips maximum
- **R:R minimum**: 1:1.2 on TP1, 1:2 on TP2

## Deployment

- **VPS**: Hetzner (46.225.66.110), Docker container `ai-analyst` on port 8000
- **Build**: `docker-compose build --no-cache && docker-compose down && docker-compose up -d`
- **SQLite volume**: `./data:/data` (persistent trades.db)
- **Logs**: `./logs:/app/logs`
- **Note**: VPS also runs a separate project (LongEntry Market Scanner) on port 8001 via systemd+nginx — do NOT touch port 8001 or nginx config

## Common Tasks

### Updating the server (Python changes only)
```bash
ssh root@46.225.66.110
cd GBPJPY-AI-Trade-Analyst-Bot
git pull origin main
docker-compose build --no-cache && docker-compose down && docker-compose up -d
```

### Updating the EA (MQL5 changes)
Copy `mt5/AI_Analyst.mq5` to the Windows MT5 machine at `MQL5/Experts/AI_Analyst.mq5`, compile in MetaEditor (F7), reattach to GBPJPY chart.

### Adding a new endpoint
1. Add the route in `server/main.py`
2. Add any new models to `server/models.py`
3. If it sends Telegram notifications, add the function in `server/telegram_bot.py`
4. If the EA needs to call it, update `mt5/AI_Analyst.mq5` and add the URL handling

## Known Patterns & Gotchas

- **String concat bug**: Never use `f"title\n" f"\u2501" * 20` — Python implicit string concat happens before `*`, repeating the title. Always use explicit `+` before the separator: `f"title\n" + "\u2501" * 20`
- **Circular imports**: `main.py` and `telegram_bot.py` import each other — use lazy imports (`from telegram_bot import X`) inside functions, not at module level
- **docker-compose v1**: The VPS uses `docker-compose` (hyphenated), not `docker compose`
- **KeyError 'ContainerConfig'**: Run `docker-compose down` first, then `up -d`
- **MT5 allowed URLs**: Only the base URL `http://46.225.66.110:8000` is needed — covers all endpoints
- **MEZ timezone**: `datetime.now(timezone(timedelta(hours=1)))` — UTC+1 for CET
