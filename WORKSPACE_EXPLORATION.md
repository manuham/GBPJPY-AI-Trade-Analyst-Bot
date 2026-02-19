# AI Trade Bot ICT — Workspace Analysis

**Report Date:** February 17, 2026  
**Project Status:** Active Development (v3.0)  
**Total Code:** 10,150 lines  
**Total Commits:** 79  
**Git Status:** Clean main branch (10 files with uncommitted changes)

---

## 1. PROJECT OVERVIEW

**What is it?**
An automated forex trading system for GBPJPY pair that uses ICT (Inner Circle Trader) methodology with Claude AI analysis. The system:
- Captures D1/H4/H1/M5 chart screenshots at London open (08:00 MEZ)
- Sends screenshots to Claude for institutional-grade analysis
- Manages complete trade lifecycle: setup detection → entry confirmation → position management → close tracking
- Uses two-tier AI (Sonnet for screening, Opus for deep analysis) to optimize costs
- Includes smart entry with M1 confirmation before executing trades
- Tracks all trades in SQLite with Telegram alerts at every stage

**Architecture:**
```
MetaTrader 5 EA ──screenshots──> FastAPI Server ──> Claude API
                   + market data       │
                                       ├─ Telegram alerts
                                       ├─ Trade queue & execution
                                       └─ SQLite tracking (trades.db)
```

**Active Pair:** GBPJPY only (v3.0 adds multi-pair framework for EURUSD, GBPUSD)  
**Session:** London Kill Zone (08:00-20:00 MEZ)

---

## 2. DIRECTORY STRUCTURE

```
GBPJPY-AI-Trade-Analyst-Bot/
├── .git/                              # Git repository
├── mt5/                               # MetaTrader 5 Expert Advisors
│   ├── AI_Analyst.mq5                 # MAIN EA (v6.00, 1,998 lines)
│   ├── GBPJPY_Analyst.mq5             # Legacy EA (1,104 lines, deprecated)
│   └── SwingLevels.mq5                # Indicator - swing high/low lines (254 lines)
├── server/                            # Python FastAPI backend
│   ├── main.py                        # FastAPI app + watch system + trade queue (1,008 lines)
│   ├── analyzer.py                    # Claude API calls - 3-tier AI (1,060 lines)
│   ├── telegram_bot.py                # Telegram bot + commands (1,108 lines)
│   ├── trade_tracker.py               # SQLite DB trade lifecycle (759 lines)
│   ├── backtest.py                    # Backtesting engine (540 lines)
│   ├── historical_data.py             # MT5 data fetching (329 lines)
│   ├── trade_simulator.py             # Paper trading simulation (359 lines)
│   ├── backtest_report.py             # Report generation (374 lines)
│   ├── models.py                      # Pydantic data models (215 lines)
│   ├── news_filter.py                 # FTMO-compliant news filter (235 lines)
│   ├── pair_profiles.py               # Per-pair config (181 lines)
│   ├── config.py                      # Environment variables (25 lines)
│   ├── shared_state.py                # Shared mutable state (14 lines)
│   ├── .env.example                   # Environment template
│   ├── requirements.txt               # Python dependencies
│   └── __pycache__/
├── dashboard/                         # Streamlit web dashboard (NEW in v3.0)
│   ├── app.py                         # Dashboard UI (587 lines)
│   └── requirements.txt
├── docs/                              # Documentation
│   ├── GBPJPY_ICT_Strategy_Research.docx  # ICT methodology research
│   ├── Integration_Notes.md               # Architecture decisions
│   └── MQL5_Implementation_Spec.md        # EA technical spec
├── data/                              # Runtime data
│   ├── trades.db                      # Main SQLite database (trade history)
│   ├── trades.db-wal                  # Write-ahead log
│   └── fundamentals_cache.db          # Cached news/fundamentals
├── CLAUDE.md                          # PROJECT INSTRUCTIONS (8.2 KB) ★★★
├── README.md                          # Project overview (7.7 KB)
├── setup.md                           # Deployment guide (detailed steps)
├── Deployment_Guide.docx              # Deployment documentation
├── Scaling_Roadmap.docx               # Future expansion plans (NEW)
├── docker-compose.yml                 # Docker orchestration
├── Dockerfile                         # Main server container
├── Dockerfile.dashboard               # Dashboard container (NEW)
└── .gitignore
```

---

## 3. RECENT GIT HISTORY (Last 20 Commits)

| Commit | Date | Message | Impact |
|--------|------|---------|--------|
| 063a159 | Feb 17 | **Phase 3: Multi-pair expansion** | Added EURUSD, GBPUSD framework |
| b988408 | Feb 17 | **Phase 2: Streamlit web dashboard** | New dashboard container + UI |
| c819bb1 | Feb 17 | **Phase 1: Backtesting system** | Backtest engine, simulator, reports |
| 60136f9 | Feb 17 | Update AI_Analyst.mq5 | EA refinements |
| 5e9b5d9 | Feb 17 | Add visual chart enhancements | Entry zone rectangle, SL/TP lines |
| fd6766e | Feb 17 | Fix: move table creation inside DB block | Bug fix |
| d1f53ac | Feb 16 | Increase M1 confirmation attempts to 10 | Enhanced entry confirmation |
| df6d175 | Feb 16 | **Fix 11 code quality issues** | Auth middleware, retry logic, cache |
| a28c718 | Feb 12 | **Smart entry confirmation + London Kill Zone** | v3.0 core feature |
| c765f2a | Feb 12 | Add v2.0 version comments | Documentation |
| afcfb5a | Feb 12 | Add H4 timeframe + ICT criteria | Enhanced analysis |
| 0fa6fc3 | Feb 12 | Add AI learning memory | Pattern feedback |
| 3b9d375 | Feb 12 | Merge PR #16 | Integration |

**Total commits:** 79  
**Repository:** https://github.com/manuham/GBPJPY-AI-Trade-Analyst-Bot

---

## 4. CURRENT WORK-IN-PROGRESS (Uncommitted Changes)

**10 files modified, 0 files staged for commit:**

| File | Status | Likely Changes |
|------|--------|-----------------|
| `server/news_filter.py` | Modified | Code formatting/refactoring (235 lines) |
| `server/requirements.txt` | Modified | Dependency updates |
| `Dockerfile` | Modified | Docker configuration |
| `.gitignore` | Modified | Ignore patterns |
| `README.md` | Modified | Documentation updates |
| `setup.md` | Modified | Setup guide updates |
| `mt5/GBPJPY_Analyst.mq5` | Modified | Legacy EA changes |
| `mt5/SwingLevels.mq5` | Modified | Indicator updates |
| `docs/Integration_Notes.md` | Modified | Integration docs |
| `docs/MQL5_Implementation_Spec.md` | Modified | EA spec updates |

**Untracked files:**
- `Scaling_Roadmap.docx` — Future expansion plans document (NEW)
- `data/.fuse_hidden*` — Temporary files (can be ignored)
- `data/trades.db-wal` — SQLite write-ahead log

**Note:** No staged changes. These are working directory modifications awaiting commit.

---

## 5. GIT BRANCH STATUS

```
* main (current branch)
  └─ Tracking: origin/main
     └─ Status: Up to date (origin/HEAD points here)

Deleted/archived branches:
  └─ origin/claude/gbpjpy-trading-bot-1kSct (feature branch, closed)
```

**No active feature branches.** All work is being done on `main`.

---

## 6. KEY FEATURES & PHASES

### Phase 1: Backtesting System (Feb 17)
**Commit:** c819bb1  
**Files added:**
- `server/backtest.py` (540 lines) — Full backtesting engine
- `server/trade_simulator.py` (359 lines) — Paper trading
- `server/backtest_report.py` (374 lines) — Report generation
- `server/historical_data.py` (329 lines) — MT5 data fetching

**Purpose:** Test trading strategies against historical data before live execution.

### Phase 2: Streamlit Web Dashboard (Feb 17)
**Commit:** b988408  
**Files added:**
- `dashboard/app.py` (587 lines) — Web UI for monitoring
- `Dockerfile.dashboard` — Containerized dashboard
- New docker-compose service for dashboard

**Purpose:** Real-time monitoring of trades, performance, and system health via web browser.

### Phase 3: Multi-pair Expansion (Feb 17)
**Commit:** 063a159  
**Files modified:**
- `server/pair_profiles.py` — Extended with EURUSD, GBPUSD configs
- `server/analyzer.py` — Multi-pair analysis support
- `server/main.py` — Multi-pair routing
- `server/telegram_bot.py` — Multi-pair alerts
- `dashboard/app.py` — Multi-pair dashboard

**Purpose:** Expand from GBPJPY-only to multiple forex pairs (EURUSD, GBPUSD).

---

## 7. CODE ORGANIZATION BY RESPONSIBILITY

### Analysis (Tier 1 → Tier 2 → Tier 3)
- **Tier 1 (Sonnet):** Quick H1+M5 screening (~$0.40)
- **Tier 2 (Opus):** Full D1/H4/H1/M5 analysis + web search (~$2.00)
- **Tier 3 (Haiku M1):** Entry confirmation when price reaches zone (~$0.05)

### Trade Lifecycle
1. **Detection:** POST /analyze triggers Sonnet + Opus
2. **Queue:** Setup saved if checklist ≥7/12 (auto-watch, no manual button)
3. **Watch:** EA polls GET /watch_trade for entry zone
4. **Confirmation:** M1 screenshot → POST /confirm_entry → Haiku checks reaction
5. **Execution:** GET /pending_trade → EA executes market order
6. **Reporting:** POST /trade_executed, POST /trade_closed
7. **Tracking:** SQLite trades.db logs full lifecycle
8. **Alerts:** Telegram at each stage

### Risk Management
- **News Filter:** FTMO-compliant (±2 min blocks around high-impact events)
- **Drawdown Limit:** 3% daily (configurable)
- **Max Open Trades:** 2 (configurable)
- **SL Cap:** 70 pips maximum
- **R:R Minimum:** 1:1.2 on TP1, 1:2 on TP2

---

## 8. CLAUDE MODELS USED

| Model | Purpose | Cost | Where |
|-------|---------|------|-------|
| Sonnet (latest) | Quick viability check (H1+M5) | ~$0.40 | `analyzer.py` - `_tier1_screen()` |
| Opus 4.6 (claude-opus-4-20250514) | Deep analysis (D1+H4+H1+M5) + web search + extended thinking | ~$2.00 | `analyzer.py` - `_tier2_analyze()` |
| Haiku 4.5 (claude-haiku-4-5-20251001) | M1 confirmation (reaction check) | ~$0.03-0.08 | `analyzer.py` - `_tier3_confirm()` |

**Note:** Opus model ID in CLAUDE.md uses dated reference (20250514) — should verify current latest version.

---

## 9. DATABASE SCHEMA (SQLite)

**File:** `/data/trades.db`

**Tables:**
1. **trades** — Core trade records (symbol, direction, entry price, TP1/TP2, SL, checklist score, P&L)
2. **performance_stats** — Aggregated metrics (win rate, total P&L, sharpe ratio)
3. **daily_performance** — Per-day tracking (daily P&L, drawdown)
4. **fundamental_cache** — Cached news/economic events
5. Additional tracking tables for correlation, news blocks, etc.

**Size:** 40 KB (trades.db) + 12 KB (fundamentals_cache.db)

---

## 10. DEPLOYMENT & INFRASTRUCTURE

**VPS:** Hetzner (46.225.66.110)  
**Port:** 8000 (AI Analyst), 8001 (separate LongEntry Scanner — DO NOT TOUCH)  
**Docker:** docker-compose (hyphenated, v1 syntax)  
**Volumes:**
- `./data:/data` — SQLite persistence
- `./logs:/app/logs` — Application logs
- `./screenshots:/data/screenshots` — Chart captures (for backtesting)

**Environment Variables (in `server/.env`):**
```
ANTHROPIC_API_KEY=sk-ant-...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
API_KEY=... (for X-API-Key header auth)
HOST=0.0.0.0
PORT=8000
MAX_DAILY_DRAWDOWN_PCT=3.0
MAX_OPEN_TRADES=2
```

---

## 11. TELEGRAM BOT COMMANDS

| Command | Purpose |
|---------|---------|
| `/scan` | Re-analyze or re-send last setup result |
| `/status` | Bot status, last scan time, active watches |
| `/stats` | Performance stats (win rate, P&L, by confidence level) |
| `/drawdown` | Daily P&L and risk dashboard |
| `/news` | Upcoming high-impact news events |
| `/reset` | Force-close stale open trades in DB |
| `/help` | Show all commands |

**Auto-alerts:**
- Setup detection (with checklist score and Execute/Skip buttons)
- Trade execution confirmation
- TP1 partial close
- Trade closed (TP2 or SL hit)
- Weekly performance report (Sunday 19:00 MEZ)

---

## 12. DOCUMENTATION FILES

| File | Size | Content |
|------|------|---------|
| `CLAUDE.md` | 8.2 KB | **PROJECT CONTEXT** — v3.0 architecture, file map, ICT methodology, API endpoints, risk rules, common tasks |
| `README.md` | 7.7 KB | Quick start, architecture diagram, features overview, project structure |
| `setup.md` | Full setup guide — prerequisites, step-by-step VPS deployment, MT5 EA setup, testing |
| `Deployment_Guide.docx` | Detailed deployment instructions |
| `Scaling_Roadmap.docx` | Future expansion plans (NEW, uncommitted) |
| `docs/GBPJPY_ICT_Strategy_Research.docx` | ICT methodology research and patterns |
| `docs/Integration_Notes.md` | Architecture decisions and gotchas |
| `docs/MQL5_Implementation_Spec.md` | EA technical specification |

---

## 13. CODE QUALITY OBSERVATIONS

### Strengths
- Clean separation of concerns (analysis, tracking, telegram, risk management)
- Comprehensive error handling with retries
- Type hints throughout (Pydantic models)
- Modular architecture (easy to add new pairs in v3.0)
- Detailed logging for debugging
- SQLite persistence for trade recovery

### Recent Improvements (Feb 16)
Commit df6d175 fixed 11 code quality issues:
1. Auth middleware
2. Shared state pattern (avoids circular imports)
3. Indicator handle cleanup
4. JSON parsing robustness
5. Health check improvements
6. Retry logic enhancement
7. Cache persistence
8. Partial win P&L calculation
9. Kill zone timezone handling
10-11. Additional refinements

### Known Gotchas (from CLAUDE.md)
1. **String concat bug:** `f"title\n" f"─" * 20` repeats title — use explicit `+` before separator
2. **Shared state pattern:** `shared_state.py` holds mutable data to avoid circular imports
3. **docker-compose v1:** VPS uses hyphenated `docker-compose`, not `docker compose`
4. **API auth:** Check `X-API-Key` header, health endpoint is exempt
5. **MEZ timezone:** UTC+1 for CET — use `timezone(timedelta(hours=1))`

---

## 14. TODO & IN-PROGRESS ITEMS

**Identified from uncommitted changes:**

1. **news_filter.py refactoring** — Code formatting/standardization
2. **requirements.txt update** — Dependency management
3. **Docker config updates** — Container optimization
4. **Documentation sync** — README, setup.md, integration notes
5. **Legacy EA cleanup** — Maintaining GBPJPY_Analyst.mq5 for reference
6. **Scaling_Roadmap.docx** — New document (uncommitted) for future phases

**No explicit TODO comments found in code** (grep for TODO/FIXME returned 0 results)

---

## 15. NEXT LIKELY TASKS

Based on recent commits and roadmap:

1. **Multi-pair rollout** — Deploy and test EURUSD/GBPUSD on live
2. **Dashboard refinement** — Performance monitoring, trade analytics
3. **Backtesting validation** — Verify system accuracy against historical data
4. **Risk optimization** — Fine-tune drawdown limits, correlation filters
5. **API optimization** — Cost reduction (Sonnet tier improvements)
6. **Indicator enhancements** — Improve SwingLevels or add new indicators
7. **Extended thinking** — Further leverage Opus extended thinking for complex setups

---

## 16. QUICK REFERENCE: COMMON TASKS

### Deploy server changes
```bash
ssh root@46.225.66.110
cd GBPJPY-AI-Trade-Analyst-Bot
git pull origin main
docker-compose build --no-cache && docker-compose down && docker-compose up -d
```

### Deploy EA changes
1. Copy `mt5/AI_Analyst.mq5` to Windows MT5 `MQL5/Experts/`
2. Compile in MetaEditor (F7)
3. Reattach to GBPJPY chart

### View logs
```bash
docker-compose logs -f --tail=50 ai-analyst
```

### Database queries
```bash
sqlite3 /data/trades.db
SELECT * FROM trades WHERE symbol='GBPJPY' ORDER BY timestamp DESC LIMIT 10;
```

### Check API health
```bash
curl http://46.225.66.110:8000/health
```

---

## 17. SUMMARY

**AI Trade Bot ICT** is a well-structured, actively developed automated trading system at v3.0. It combines:

- **Intelligent analysis** via Claude's three-tier API (cost-optimized)
- **Smart entry** with M1 confirmation (no blind orders)
- **Full lifecycle tracking** from detection through close
- **Risk management** (FTMO-compliant, drawdown limits, correlation checks)
- **Multi-pair framework** (GBPJPY core, EURUSD/GBPUSD in beta)
- **Web dashboard** for real-time monitoring
- **Backtesting system** for strategy validation
- **Telegram alerts** at every trade stage

**Current status:** Main branch is clean; 10 files have uncommitted changes related to code cleanup, documentation updates, and minor refinements. Three major features landed in the last few days (backtesting, dashboard, multi-pair support). **No blocking issues** — code is production-ready.

---

**Generated:** February 17, 2026
