# AI Trade Bot ICT — Quick Reference

## System Architecture at a Glance

```
┌─────────────────────────────────────────────────────────────┐
│                      AI Trade Bot ICT                       │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  MetaTrader 5              FastAPI Server       Claude API  │
│  ┌──────────────┐          ┌──────────────┐    ┌─────────┐ │
│  │  AI_Analyst  │          │   analyzer   │    │ Sonnet  │ │
│  │   (EA v6)    ├─────────→│ (Tier 1: H1) ├───→│(~$0.40) │ │
│  │              │          │              │    │         │ │
│  │ - Screenshots│          │ (Tier 2: ALL)├───→│ Opus    │ │
│  │ - Zone watch │          │ (~$2.00)     │    │(~$2.00) │ │
│  │ - M1 confirm │          │              │    │         │ │
│  │ - Execution  │          │ (Tier 3: M1) ├───→│ Haiku   │ │
│  └──────────────┘          │ (~$0.05)     │    │(~$0.05) │ │
│        │                   └──────────────┘    └─────────┘ │
│        │                        │                            │
│        │                        ├─→ Telegram Bot            │
│        │                        ├─→ SQLite (trades.db)      │
│        │                        └─→ Dashboard (Streamlit)   │
│        │                                                     │
│        └──────────→ Poll every 5 seconds                    │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## File Map (Quick Lookup)

| File | Lines | Purpose |
|------|-------|---------|
| **Core Server** | | |
| `main.py` | 1,008 | FastAPI routes, watch system, trade queue |
| `analyzer.py` | 1,060 | Claude API calls (3-tier: Sonnet→Opus→Haiku) |
| `telegram_bot.py` | 1,108 | Bot commands, alerts, Execute/Skip buttons |
| `trade_tracker.py` | 759 | SQLite DB, trade lifecycle, P&L tracking |
| **Analysis & Risk** | | |
| `news_filter.py` | 235 | FTMO-compliant news blocking (±2 min) |
| `pair_profiles.py` | 181 | Per-pair config (GBPJPY, EURUSD, GBPUSD) |
| `models.py` | 215 | Pydantic data models |
| **Backtesting & Dashboard** | | |
| `backtest.py` | 540 | Historical backtesting engine |
| `backtest_report.py` | 374 | Report generation |
| `trade_simulator.py` | 359 | Paper trading simulation |
| `historical_data.py` | 329 | MT5 data fetching |
| `dashboard/app.py` | 587 | Streamlit web UI |
| **MT5 Expert Advisor** | | |
| `mt5/AI_Analyst.mq5` | 1,998 | Main EA v6.00 (production) |
| `mt5/SwingLevels.mq5` | 254 | Swing high/low indicator |
| **Supporting** | | |
| `config.py` | 25 | Environment variables |
| `shared_state.py` | 14 | Shared mutable state (avoids circular imports) |
| | | |
| **TOTAL** | 10,150 | |

## Trade Lifecycle Flow

```
08:00 MEZ: EA sends screenshots
    ↓
POST /analyze
    ↓
Tier 1: Sonnet screens H1+M5 (~$0.40)
    ├─ Dead market? → Cancel
    │
    └─ Setup found? → Continue
         ↓
    Tier 2: Opus analyzes D1/H4/H1/M5 + web search (~$2.00)
    Checklist score: 0-12 points
         ↓
    Telegram: Alert with checklist score + R:R
    Auto-watch if checklist ≥7/12 (no button press needed!)
         ↓
EA polls GET /watch_trade
Watches entry zone locally (zero API cost)
    ↓
Price reaches zone?
    ↓
POST /confirm_entry (M1 screenshot)
    ↓
Tier 3: Haiku confirms bullish/bearish reaction (~$0.05)
Max 3 attempts, 60s cooldown
    ↓
GET /pending_trade
    ↓
EA executes market order
POST /trade_executed
    ↓
Position management:
- TP1 closes adaptive % (40-60%)
- Break-even set
- Trail to TP2
    ↓
Position closes (TP or SL hit)
    ↓
POST /trade_closed
SQLite logged, Telegram alert
    ↓
Watch expires at 20:00 MEZ
```

## API Endpoints

| Method | Path | Called By | Purpose |
|--------|------|-----------|---------|
| POST | `/analyze` | EA | Send screenshots + market data |
| GET | `/watch_trade?symbol=GBPJPY` | EA | Poll for entry zone |
| POST | `/confirm_entry` | EA | M1 screenshot for Haiku |
| GET | `/pending_trade?symbol=GBPJPY` | EA | Poll for confirmed trade |
| POST | `/trade_executed` | EA | Report execution (ticket, lots) |
| POST | `/trade_closed` | EA | Report position close |
| GET | `/health` | Monitoring | Server status |
| GET | `/stats` | Telegram/API | Performance stats |
| GET | `/scan` | Manual trigger | Re-run analysis |

## Telegram Commands

```
/scan       - Re-analyze or re-send last result
/status     - Bot status, last scan, active watches
/stats      - Win rate, P&L, by confidence level
/drawdown   - Daily P&L dashboard
/news       - Upcoming high-impact events
/reset      - Force-close stale trades
/help       - Show all commands
```

## 12-Point ICT Checklist

Scoring: ≥10 = HIGH, 7-9 = MEDIUM, 4-6 = LOW, <4 = no setup

1. D1 bias identified
2. H4 aligns with D1
3. Correct Premium/Discount zone
4. Active Order Block within validity
5. MSS confirmed on M5 (≥15 pip)
6. FVG present (≥15 pips)
7. Entry at CE level (50% FVG midpoint)
8. OTE zone (62-79%) alignment
9. Liquidity sweep detected
10. SL within 70 pip cap
11. R:R ≥1:2 on TP2
12. No conflicting news within 30 min

## Key Configurations

| Setting | Default | File | Notes |
|---------|---------|------|-------|
| Active pairs | GBPJPY, EURUSD, GBPUSD | `pair_profiles.py` | v3.0+ supports multiple |
| Kill zone | 08:00-20:00 MEZ | `pair_profiles.py` | London open hours |
| News buffer | ±2 minutes | `news_filter.py` | FTMO-compliant |
| Max drawdown | 3% daily | `config.py` | Configurable |
| Max open trades | 2 | `config.py` | Configurable |
| SL hard cap | 70 pips | `analyzer.py` | Safety limit |
| Min R:R TP1 | 1:1.2 | `analyzer.py` | Risk/reward minimum |
| Min R:R TP2 | 1:2 | `analyzer.py` | Risk/reward minimum |
| M1 attempts | 10 max | `analyzer.py` | Entry confirmation tries |
| M1 cooldown | 60 seconds | `main.py` | Between confirmation checks |

## Python Dependencies (Key)

```
fastapi              # Web framework
httpx                # HTTP client (for calendar fetching)
pydantic             # Data validation
anthropic            # Claude API
python-telegram-bot  # Telegram integration
sqlite3              # Database (built-in)
aiohttp              # Async HTTP
streamlit            # Dashboard UI
```

## Database Tables

```sql
trades              -- Core trade records
├─ symbol, direction, entry_price
├─ tp1, tp2, sl
├─ checklist_score, confluence_reason
├─ entry_reason, p_l, status
└─ timestamps

performance_stats   -- Aggregated metrics
├─ win_rate, sharpe_ratio
├─ total_p_l, max_drawdown
└─ by_confidence_level

daily_performance   -- Per-day tracking
├─ date, daily_p_l, daily_drawdown
└─ trade_count

fundamental_cache   -- Cached news/events
├─ symbol, date, event_data
└─ cached_at
```

## Deployment Quick Start

### Option 1: VPS (Production)
```bash
ssh root@46.225.66.110
cd AI-Trade-Bot-ICT
git pull origin main
docker-compose build --no-cache
docker-compose down && docker-compose up -d
docker-compose logs -f --tail=50
```

### Option 2: Local Development
```bash
git clone https://github.com/manuham/AI-Trade-Bot-ICT.git
cd AI-Trade-Bot-ICT
cp server/.env.example server/.env
# Edit server/.env with your API keys
docker-compose up -d
```

### Environment Variables (.env)
```
ANTHROPIC_API_KEY=sk-ant-...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
API_KEY=... (for X-API-Key header auth)
HOST=0.0.0.0
PORT=8000
```

## Claude Model Selection

| Model | Use Case | Cost | Context |
|-------|----------|------|---------|
| Sonnet (latest) | Quick H1+M5 viability check | ~$0.40 | Cost-effective screening |
| Opus 4.6 | D1/H4/H1/M5 full analysis + web search + extended thinking | ~$2.00 | Deep institutional analysis |
| Haiku 4.5 | M1 confirmation — reaction check | ~$0.03-0.08 | Fast entry validation |

## Known Gotchas & Patterns

1. **String concat bug:**
   ```python
   # WRONG (repeats title)
   f"title\n" f"─" * 20
   
   # CORRECT (separator only)
   f"title\n" + "─" * 20
   ```

2. **Circular imports:** Use `shared_state.py` for mutable data shared between modules

3. **Docker:** VPS uses `docker-compose` (hyphenated), not `docker compose`

4. **API auth:** All endpoints except `/health` check `X-API-Key` header

5. **Timezone:** Always use `timezone(timedelta(hours=1))` for MEZ (UTC+1)

6. **M1 confirmation:** Max 10 attempts per setup, 60-second cooldown between checks

## Recent Major Features (Feb 2026)

| Date | Commit | Feature |
|------|--------|---------|
| Feb 17 | 063a159 | Multi-pair expansion (EURUSD, GBPUSD) |
| Feb 17 | b988408 | Streamlit web dashboard |
| Feb 17 | c819bb1 | Backtesting system |
| Feb 16 | df6d175 | Fix 11 code quality issues |
| Feb 12 | a28c718 | Smart entry confirmation + London Kill Zone (v3.0) |
| Feb 12 | afcfb5a | Add H4 timeframe + ICT criteria |

## File Size Reference

```
mt5/AI_Analyst.mq5          1,998 lines  ← Largest MQL5 file (main EA)
server/analyzer.py          1,060 lines  ← Largest Python file (Claude calls)
server/telegram_bot.py      1,108 lines  ← Telegram integration
server/main.py              1,008 lines  ← FastAPI app
dashboard/app.py              587 lines  ← Streamlit dashboard
server/trade_tracker.py       759 lines  ← SQLite persistence
server/backtest.py            540 lines  ← Backtesting engine
```

## Uncommitted Changes (as of Feb 17)

10 files modified, awaiting commit:
- `server/news_filter.py` (code formatting)
- `server/requirements.txt` (dependency updates)
- `Dockerfile` (container config)
- `README.md`, `setup.md` (docs)
- `mt5/GBPJPY_Analyst.mq5`, `mt5/SwingLevels.mq5` (EA/indicator)
- `docs/Integration_Notes.md`, `docs/MQL5_Implementation_Spec.md` (spec updates)
- `.gitignore`

Untracked: `Scaling_Roadmap.docx` (future expansion plans)

## Key Documentation

| File | Location | Content |
|------|----------|---------|
| CLAUDE.md | Root | Official v3.0 project context + system flow + API endpoints |
| README.md | Root | Quick start + features overview |
| setup.md | Root | Full deployment guide |
| WORKSPACE_EXPLORATION.md | Root | This workspace analysis (17 KB) |
| docs/GBPJPY_ICT_Strategy_Research.docx | docs/ | ICT methodology research |
| docs/Integration_Notes.md | docs/ | Architecture decisions + gotchas |
| docs/MQL5_Implementation_Spec.md | docs/ | EA technical specification |

## Quick Debugging

```bash
# Check server health
curl http://46.225.66.110:8000/health

# View logs
docker-compose logs -f --tail=100 ict-tradebot

# Query latest trades
sqlite3 /data/trades.db "SELECT symbol, status, p_l FROM trades ORDER BY timestamp DESC LIMIT 5;"

# Get performance stats
curl http://46.225.66.110:8000/stats -H "X-API-Key: YOUR_API_KEY"

# Test Telegram
/scan command in Telegram bot
```

## Repository

- **GitHub:** https://github.com/manuham/AI-Trade-Bot-ICT
- **VPS:** Hetzner (46.225.66.110)
- **Port:** 8000 (AI Analyst), 8001 (separate scanner — don't touch)
- **Main branch:** production-ready, clean with 10 files pending commit

---

**Generated:** February 17, 2026  
**Version:** v3.0 (Multi-pair, Dashboard, Backtesting)
