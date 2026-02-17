# PROGRESS TRACKER — GBPJPY AI Trade Analyst Bot

**Last updated:** 2026-02-17
**Current phase:** Phase 4 (Public P&L & Transparency) — STARTING
**Reference:** See `Scaling_Roadmap.docx` for full 5-phase plan

---

## Phase 1: Backtesting System — COMMITTED (60% feature-complete)

**Status:** Core code committed (`c819bb1`). Functional but missing some roadmap features.

**What's done:**
- `server/backtest.py` — batch backtest engine, replays predefined setups
- `server/backtest_report.py` — performance reports with breakdowns by checklist, confidence, bias, day
- `server/historical_data.py` — OHLC data loader from CSV
- `server/trade_simulator.py` — M1 candle simulation (did price hit TP/SL?)
- DB tables: `backtest_runs`, `backtest_trades`
- Dashboard page: Backtest Explorer with equity curves and breakdown charts

**What's still missing:**
- No Sharpe ratio calculation
- No screenshot replay engine (feeds pre-defined data, not actual archived screenshots through Opus)
- Entry price uses zone midpoint instead of actual touch price
- No DST handling in timezone calculations
- CSV parser could be more robust for various MT5 export formats

---

## Phase 2: Streamlit Dashboard — COMMITTED + BUGS FIXED (75% feature-complete)

**Status:** Core committed (`b988408`). Three key bugs fixed on 2026-02-17.

**What's done:**
- `dashboard/app.py` — 6 pages: Live Monitor, Performance, Trade Journal, Backtest Explorer, Risk Panel, Analysis Stats
- Pair selector (ALL / per-pair filtering)
- Equity curves, confidence/session breakdowns, trade journal with expanders

**Bugs fixed (2026-02-17):**
1. Risk Panel showed $0.00 — dashboard used wrong key names (`total_pnl_pips` vs `daily_pnl`)
2. Analysis Stats Avg R:R showed 0.00 — `avg_rr` key was missing from weekly report
3. Breakdown tables showed 0 — key mismatch (`total`/`pnl_pips` vs `count`/`total_pnl`)
4. Telegram weekly report also used old keys — fixed `by_checklist` → `by_checklist_score`, `total` → `total_trades`

**What's still missing:**
- Trade Journal doesn't display screenshots (text only)
- REST API endpoints (`/api/trades`, `/api/equity-curve`, etc.) not created — dashboard imports Python directly
- No Next.js migration yet (Streamlit MVP only)

---

## Phase 3: Multi-Pair Expansion — COMMITTED + BUGS FIXED (85% feature-complete)

**Status:** Core committed (`063a159`). Two bugs fixed on 2026-02-17.

**What's done:**
- `server/pair_profiles.py` — 6 pairs configured (GBPJPY, EURUSD, GBPUSD, XAUUSD, USDJPY, EURJPY)
- `server/analyzer.py` — pair-specific prompts, kill zones, fundamentals, digits
- `server/config.py` — `ACTIVE_PAIRS` env var (comma-separated)
- `server/main.py` — all in-memory storage keyed by symbol
- `mt5/AI_Analyst.mq5` — per-chart pair support with symbol override

**Bugs fixed (2026-02-17):**
1. Hardcoded `["GBPJPY"]` in startup missed-scan check → now uses `config.ACTIVE_PAIRS`
2. Correlation filter upgraded with 3 rules:
   - Rule 1: Currency overlap detection (was already there)
   - Rule 2: GBP pair group — max 1 open trade across all GBP pairs
   - Rule 3: USD pair group — max 1 trade in same USD direction

**What's still missing:**
- EA kill zone times are hard-coded inputs (should be server-driven via pair_profiles)
- Not yet tested in production with multiple pairs (only GBPJPY active on VPS)
- `ACTIVE_PAIRS` env var on VPS still set to just "GBPJPY"

---

## Phase 4: Public P&L & Transparency — CODE COMPLETE (needs config + deploy)

**What's done (2026-02-17):**

1. **Public trade feed — BUILT**
   - `server/public_feed.py` — new module handling all public transparency features
   - Public Telegram channel posts: auto-posts every trade open/close to a public channel
   - Hooks in `main.py` `/trade_executed` and `/trade_closed` endpoints
   - Public API endpoints (no auth required):
     - `GET /public/trades` — JSON trade history
     - `GET /public/stats` — aggregated performance stats
     - `GET /public/feed` — beautiful HTML page showing all trades with dark theme
     - `GET /public/report/{year}/{month}` — downloadable monthly PDF report
   - Auth middleware updated to exempt `/public/*` paths

2. **Google Sheets sync — BUILT**
   - Auto-syncs every trade to Google Sheet on execution
   - Auto-updates outcome + P&L when trade closes
   - Headers auto-created on first sync
   - **Needs:** Google Cloud service account setup (instructions below)

3. **Monthly PDF report — BUILT**
   - `server/monthly_report.py` — generates professional PDF with reportlab
   - Contains: overview table, per-pair breakdown, confidence breakdown, disclaimer
   - Auto-sends on 1st of each month at 08:00 MEZ (added to system tasks loop)
   - Sends to both private chat AND public channel
   - Also saved to `/data/reports/` for archival
   - Can also be generated on-demand via `/public/report/{year}/{month}`

4. **Myfxbook — MANUAL SETUP NEEDED**
   - Not code-dependent — Manuel needs to create account and connect MT5
   - Instructions provided below

**New files:**
- `server/public_feed.py` — public trade feed, Google Sheets sync, API formatting
- `server/monthly_report.py` — PDF report generation with reportlab

**Modified files:**
- `server/main.py` — added public endpoints, auth exemption, trade lifecycle hooks, monthly report scheduler
- `server/requirements.txt` — added reportlab, google-auth, google-api-python-client

**Config needed (add to server/.env):**
```bash
# Public Telegram channel (create one first, add bot as admin)
PUBLIC_TELEGRAM_CHANNEL_ID=@your_channel_name

# Google Sheets (optional — set to true after service account setup)
GSHEETS_ENABLED=false
GSHEETS_SPREADSHEET_ID=your_spreadsheet_id_here
GSHEETS_CREDENTIALS_FILE=/data/gsheets_credentials.json
```

**Google Sheets setup steps:**
1. Go to https://console.cloud.google.com/ and create a new project
2. Enable "Google Sheets API" in APIs & Services
3. Create a Service Account (IAM & Admin > Service Accounts)
4. Create a JSON key for that service account, download it
5. Upload the JSON key to VPS as `/data/gsheets_credentials.json`
6. Create a Google Sheet, share it with the service account email (Editor access)
7. Copy the spreadsheet ID from the URL and set GSHEETS_SPREADSHEET_ID
8. Set GSHEETS_ENABLED=true

**Myfxbook setup steps:**
1. Go to https://www.myfxbook.com/ and register
2. Go to Portfolio > Add Account > MetaTrader 5
3. Download and install the Myfxbook EA on your MT5
4. Connect using your MT5 investor password (read-only, safe)
5. Set account to "Public" in settings
6. Add the Myfxbook URL to your Telegram bio and future website

---

## Phase 5: Community & Education — NOT STARTED

**Roadmap deliverables:**
- Discord server structure
- Subscription tiers (Free/$29/$79/$199)
- Content calendar (daily analysis, weekly ICT deep dive, monthly voice sessions)
- Website/landing page

---

## Uncommitted Changes (as of 2026-02-17)

**Commit 1 — Bug fixes (Phase 2+3):**
```bash
git add dashboard/app.py server/trade_tracker.py server/main.py server/telegram_bot.py
git commit -m "Fix dashboard key mismatches, add avg R:R, upgrade correlation filter, fix multi-pair startup check"
```

**Commit 2 — Phase 4 (Public P&L):**
```bash
git add server/public_feed.py server/monthly_report.py server/main.py server/requirements.txt PROGRESS.md
git commit -m "Phase 4: Public P&L — trade feed, Google Sheets sync, monthly PDF reports, public API"
```

**Push and deploy:**
```bash
git push origin main
ssh root@46.225.66.110
cd GBPJPY-AI-Trade-Analyst-Bot
git pull origin main
docker-compose build --no-cache && docker-compose down && docker-compose up -d
```

---

## Notes for Claude

- Manuel is a beginner — always provide step-by-step terminal commands for git/VPS operations
- MQL5 is Manuel's primary language — he codes Expert Advisors for MetaTrader 5
- VPS is Hetzner at 46.225.66.110, Docker container `ai-analyst` on port 8000
- Port 8001 belongs to a DIFFERENT project (LongEntry Market Scanner) — never touch it
- The `Scaling_Roadmap.docx` in project root has the full business plan with revenue projections
