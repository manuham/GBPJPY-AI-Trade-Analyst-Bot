# PROGRESS TRACKER — GBPJPY AI Trade Analyst Bot

**Last updated:** 2026-02-17
**Current phase:** Phase 5 IN PROGRESS — Website landing page built, needs commit + Vercel deploy
**Reference:** See `Scaling_Roadmap.docx` for full 5-phase business plan

---

## Git Status (as of 2026-02-17)

**All code is committed and pushed to GitHub.** Latest commits:
```
39d5de5 Phase 4: Public P&L — trade feed, Google Sheets sync, monthly PDF reports, public API
dc7f2cd Fix dashboard key mismatches, add avg R:R, upgrade correlation filter, fix multi-pair startup check
5580d83 Fix dashboard key mismatches, add avg R:R, upgrade correlation filter, fix multi-pair startup check
063a159 Phase 3: Multi-pair expansion (EURUSD, GBPUSD)
b988408 Phase 2: Streamlit web dashboard
c819bb1 Phase 1: Backtesting system
```

**VPS is deployed and running with Phase 4 code.**

**Still uncommitted (non-critical, line ending normalization only):**
- .gitignore, Dockerfile, README.md, docs/Integration_Notes.md, docs/MQL5_Implementation_Spec.md
- mt5/GBPJPY_Analyst.mq5, mt5/SwingLevels.mq5, server/news_filter.py, setup.md
- These are CRLF→LF line ending changes only. No code changes. Safe to commit anytime:
```bash
git add .gitignore Dockerfile README.md docs/ mt5/GBPJPY_Analyst.mq5 mt5/SwingLevels.mq5 server/news_filter.py setup.md
git commit -m "Normalize line endings (CRLF to LF)"
git push origin main
```

**Untracked files (do NOT commit):**
- QUICK_REFERENCE.md, README_EXPLORATION.md, WORKSPACE_EXPLORATION.md — auto-generated exploration docs
- Scaling_Roadmap.docx — business plan (keep local, not in git)
- data/* — database files (mounted volume, not in git)

---

## Phase 1: Backtesting System — COMMITTED (60% feature-complete)

**Commit:** `c819bb1`

**Files:**
- `server/backtest.py` — batch backtest engine, replays predefined setups
- `server/backtest_report.py` — performance reports (checklist, confidence, bias, day, streaks)
- `server/historical_data.py` — OHLC data loader from CSV, resampling M1→M5/H1/H4/D1
- `server/trade_simulator.py` — M1 candle simulation (did price hit TP/SL?)
- DB tables: `backtest_runs`, `backtest_trades` (created via trade_tracker.init_db)
- Dashboard page: Backtest Explorer with equity curves and breakdown charts

**Still missing (for future improvement):**
- No Sharpe ratio calculation
- No screenshot replay engine (uses pre-defined setups, not archived screenshots through Opus)
- Entry price uses zone midpoint instead of actual touch price
- No DST handling in timezone calculations
- CSV parser fragile for non-standard MT5 export formats

---

## Phase 2: Streamlit Dashboard — COMMITTED + BUGS FIXED (75% feature-complete)

**Commit:** `b988408` (original) + `5580d83` (bug fixes)

**Files:**
- `dashboard/app.py` — 6 pages: Live Monitor, Performance, Trade Journal, Backtest Explorer, Risk Panel, Analysis Stats

**Bugs fixed (2026-02-17, commit `5580d83`):**
1. Risk Panel showed $0.00 — `total_pnl_pips`/`total_pnl_money` → `daily_pnl`/`closed_trades_today`
2. Analysis Stats Avg R:R showed 0.00 — added `avg_rr` calculation to weekly report
3. Breakdown tables showed 0 — `total`/`pnl_pips` → `count`/`total_pnl`
4. Telegram weekly report used old keys — `by_checklist` → `by_checklist_score`, `total` → `total_trades`

**Still missing:**
- Trade Journal shows text only (no screenshots)
- REST API endpoints not created — dashboard imports Python modules directly
- No Next.js migration yet (Streamlit MVP only)

---

## Phase 3: Multi-Pair Expansion — COMMITTED + BUGS FIXED (85% feature-complete)

**Commit:** `063a159` (original) + `5580d83` / `dc7f2cd` (bug fixes)

**Files:**
- `server/pair_profiles.py` — 6 pairs: GBPJPY, EURUSD, GBPUSD, XAUUSD, USDJPY, EURJPY
- `server/config.py` — `ACTIVE_PAIRS` env var (comma-separated)
- `server/analyzer.py` — pair-specific prompts, kill zones, fundamentals, digit formatting
- `server/main.py` — all in-memory storage keyed by symbol
- `mt5/AI_Analyst.mq5` — per-chart pair support with symbol override input

**Bugs fixed (2026-02-17):**
1. Hardcoded `["GBPJPY"]` in startup scan check → `config.ACTIVE_PAIRS` (commit `5580d83`)
2. Correlation filter upgraded with 3 rules (commit `dc7f2cd`):
   - Rule 1: Currency overlap detection (existing)
   - Rule 2: GBP pair group — max 1 open trade across all GBP pairs
   - Rule 3: USD pair group — max 1 trade in same USD direction

**Still missing:**
- EA kill zone times hard-coded (should be server-driven via pair_profiles)
- Not tested in production with multiple pairs (only GBPJPY active on VPS)
- `ACTIVE_PAIRS` env var on VPS still = "GBPJPY"

---

## Phase 4: Public P&L & Transparency — COMPLETE + DEPLOYED

**Commits:** `dc7f2cd` (main.py hooks/endpoints) + `39d5de5` (new files)

### Code built:

**New files:**
- `server/public_feed.py` — public trade feed, Google Sheets sync, API formatting (~380 lines)
- `server/monthly_report.py` — auto-generated PDF reports with reportlab (~320 lines)

**Modified files:**
- `server/main.py` — public endpoints, auth exemption, trade lifecycle hooks, monthly scheduler
- `server/requirements.txt` — added reportlab, google-auth, google-api-python-client

### Features:

1. **Public trade feed (Telegram + Web)**
   - Auto-posts every trade open/close to public Telegram channel
   - Hooks in `/trade_executed` and `/trade_closed` endpoints
   - Formats: opened, tp1_hit, tp2_hit, sl_hit, closed

2. **Public API endpoints (no auth required):**
   - `GET /public/trades` — JSON trade history (filterable by symbol, limit)
   - `GET /public/stats` — aggregated 30-day performance stats
   - `GET /public/feed` — dark-themed HTML page with stats cards + trade table
   - `GET /public/report/{year}/{month}` — downloadable monthly PDF
   - Auth middleware exempts all `/public/*` paths

3. **Google Sheets sync**
   - Auto-appends trade row on execution (sync_trade_to_sheets)
   - Auto-updates outcome + P&L on close (update_trade_in_sheets)
   - Auto-creates headers on first sync (init_sheets_headers)
   - Gracefully handles missing credentials (just logs debug, no crash)

4. **Monthly PDF report**
   - Generates on 1st of each month at 08:00 MEZ (system tasks loop)
   - Reports on PREVIOUS month's performance
   - Sends to both private chat + public channel as PDF document
   - Also saved to `/data/reports/` for archival
   - Contains: overview table, per-pair breakdown, confidence breakdown, disclaimer

### Config (already done on VPS):
```bash
# In server/.env on VPS:
PUBLIC_TELEGRAM_CHANNEL_ID=@gbpjpy_ai_signals  # Manuel's public channel
GSHEETS_ENABLED=true
GSHEETS_SPREADSHEET_ID=1PQE861KiftLTUnN-Lwk-RyrUpDM9yiUuim0Sj8Gc_ZE
GSHEETS_CREDENTIALS_FILE=/data/gsheets_credentials.json
```

### External setup completed:
- Public Telegram channel created and bot added as admin
- Google Cloud project "Ai Analyst" created with Sheets API enabled
- Service account created, JSON key uploaded to VPS at `/data/gsheets_credentials.json`
- Google Sheet "AI Trade Analyst - Public P&L" created and shared with service account
- Myfxbook account created (MT5 connection still pending)

### Live URLs:
- Public P&L page: http://46.225.66.110:8000/public/feed
- Public trades API: http://46.225.66.110:8000/public/trades
- Public stats API: http://46.225.66.110:8000/public/stats
- February 2026 PDF: http://46.225.66.110:8000/public/report/2026/2

### Code integrity verified:
- All imports correct (lazy imports avoid circular dependencies)
- All key names consistent across dashboard, trade_tracker, telegram_bot
- Error handling robust (try-except around all external API calls)
- Graceful degradation when optional services not configured

---

## Phase 5: Community & Education — WEBSITE BUILT (landing page complete)

### Website (Next.js Landing Page) — BUILT

**Stack:** Next.js 15, React 19, Tailwind CSS v4, SWR, TypeScript
**Location:** `/website/` directory

**Files created:**
| File | Purpose |
|------|---------|
| `package.json` | Dependencies: next 15, react 19, swr, tailwindcss v4 |
| `tsconfig.json` | TypeScript config with path aliases |
| `postcss.config.mjs` | PostCSS with @tailwindcss/postcss |
| `next.config.ts` | Minimal Next.js config |
| `.gitignore` | node_modules, .next, data/ |
| `lib/api.ts` | API URL config, TypeScript interfaces, fetch helpers |
| `app/globals.css` | Tailwind import + dark theme CSS variables |
| `app/layout.tsx` | Root layout with SEO/OpenGraph metadata |
| `app/page.tsx` | Main landing page composing all sections |
| `app/components/Hero.tsx` | Gradient background, headline, CTA buttons |
| `app/components/LiveStats.tsx` | Async server component — fetches /public/stats, 4 stat cards |
| `app/components/HowItWorks.tsx` | 3-step visual: AI Analysis → ICT Checklist → Smart Entry |
| `app/components/TradeTable.tsx` | Client component with SWR polling /public/trades every 30s |
| `app/components/Pricing.tsx` | 4 tier cards (Free/Starter/Pro/Enterprise) |
| `app/components/WaitlistForm.tsx` | Email form, posts to /api/waitlist |
| `app/components/Footer.tsx` | Links, disclaimer, copyright |
| `app/api/waitlist/route.ts` | Waitlist API — saves emails to data/waitlist.json |

**TypeScript:** Compiles clean, zero errors

**API connection:** Reads from `NEXT_PUBLIC_API_URL` env var (default: http://46.225.66.110:8000)
- LiveStats fetches `/public/stats` (server component, revalidates every 60s)
- TradeTable fetches `/public/trades` (client component, SWR polls every 30s)

### To deploy (Vercel):
```
1. Push code to GitHub (commit the website/ folder)
2. Go to vercel.com → New Project → Import from GitHub
3. Set Root Directory to "website"
4. Add env var: NEXT_PUBLIC_API_URL = http://46.225.66.110:8000
5. Deploy
```

### Still TODO for Phase 5:
- Commit and push website code to GitHub
- Deploy to Vercel
- Buy domain name (e.g., aitradeanalyst.com)
- Connect domain in Vercel settings
- Discord server setup
- Content calendar automation
- Myfxbook MT5 connection (account created but not connected yet)

**Revenue target:** $35,000 - $50,000 Year 1

---

## Complete File Map

### Server (Python — `/server/`)
| File | Purpose | Phase |
|------|---------|-------|
| `main.py` | FastAPI app, all endpoints, watch system, trade queue, background loops | Core + P2-4 |
| `analyzer.py` | Claude API — Sonnet screening, Opus analysis, Haiku M1 confirmation | Core |
| `telegram_bot.py` | Telegram commands, Execute/Skip buttons, all notifications | Core + P2-3 |
| `trade_tracker.py` | SQLite DB, trade lifecycle, stats, correlation filter | Core + P2-3 |
| `public_feed.py` | Public trade feed, Google Sheets sync, API formatting | **Phase 4** |
| `monthly_report.py` | Auto-generated monthly PDF reports | **Phase 4** |
| `backtest.py` | Backtest engine, batch replay | Phase 1 |
| `backtest_report.py` | Backtest performance reports + Telegram formatting | Phase 1 |
| `historical_data.py` | OHLC data loader, CSV import, resampling | Phase 1 |
| `trade_simulator.py` | M1 candle simulation for backtests | Phase 1 |
| `news_filter.py` | FTMO-compliant ForexFactory news filter | Core |
| `pair_profiles.py` | Per-pair config (digits, spreads, kill zones) | Phase 3 |
| `models.py` | Pydantic models for all data structures | Core |
| `config.py` | Environment variables | Core |
| `shared_state.py` | Shared mutable state (breaks circular imports) | Core |

### Dashboard (`/dashboard/`)
| File | Purpose | Phase |
|------|---------|-------|
| `app.py` | Streamlit web dashboard (6 pages) | Phase 2 |

### Website (`/website/`)
| File | Purpose | Phase |
|------|---------|-------|
| `app/page.tsx` | Main landing page composing all sections | Phase 5 |
| `app/layout.tsx` | Root layout with SEO metadata | Phase 5 |
| `app/globals.css` | Tailwind + dark theme CSS variables | Phase 5 |
| `app/components/*.tsx` | Hero, LiveStats, HowItWorks, TradeTable, Pricing, WaitlistForm, Footer | Phase 5 |
| `app/api/waitlist/route.ts` | Waitlist email collection API | Phase 5 |
| `lib/api.ts` | API helpers + TypeScript interfaces | Phase 5 |

### MT5 (`/mt5/`)
| File | Purpose |
|------|---------|
| `AI_Analyst.mq5` | EA v6.00 — screenshots, zone watching, M1 confirmation, execution |
| `SwingLevels.mq5` | Indicator v2.00 — swing high/low horizontal lines |

---

## VPS Deployment

**Server:** Hetzner, 46.225.66.110, Docker container `ai-analyst` on port 8000
**WARNING:** Port 8001 = DIFFERENT project (LongEntry Market Scanner) — NEVER touch it

**Deploy commands:**
```bash
ssh root@46.225.66.110
cd GBPJPY-AI-Trade-Analyst-Bot
git pull origin main
docker-compose build --no-cache && docker-compose down && docker-compose up -d
```

**View logs:**
```bash
docker-compose logs -f --tail=100
```

---

## Notes for Claude

- Manuel is a **beginner** — always provide step-by-step terminal commands for git/VPS operations
- MQL5 is Manuel's primary language — he codes Expert Advisors for MetaTrader 5
- Always explain what each command does before asking Manuel to run it
- If memory is lost, read this file + CLAUDE.md + Scaling_Roadmap.docx to get full context
- The `Scaling_Roadmap.docx` in project root has the full business plan with revenue projections
- Known gotchas are documented in CLAUDE.md (string concat bug, shared_state pattern, docker-compose v1, etc.)
