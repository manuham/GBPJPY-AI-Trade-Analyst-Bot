# AI Trade Bot ICT (v3.0)

Automated multi-pair forex trading system using ICT (Inner Circle Trader) methodology. The MT5 Expert Advisor captures chart screenshots at kill zone open, sends them to Claude for institutional-grade analysis enriched with live macro/sentiment data, and manages the full trade lifecycle — from setup detection through smart zone-based entry with M1 confirmation to position close tracking.

## Architecture

```
MT5 EA (v6.00)                          FastAPI Server (Python)
  │                                         │
  ├─ 08:00 MEZ: Capture D1/H4/H1/M5 ──────→ POST /analyze
  │              screenshots + market data   │
  │                                         ├─ Tier 1: Sonnet screening (~$0.40)
  │                                         │   └─ Quick viability check (H1+M5)
  │                                         │
  │                                         ├─ Tier 2: Opus full analysis (~$2.00)
  │                                         │   └─ D1+H4+H1+M5 + web search
  │                                         │   └─ ICT checklist scoring (12 points)
  │                                         │
  │                                         ├─→ Telegram: Setup alerts
  │                                         │   └─ Auto-watch if checklist ≥7/12
  │                                         │
  ├─ Poll for watch zone ──────────────────→ GET /watch_trade
  │                                         │
  ├─ When price reaches zone: ─────────────→ POST /confirm_entry
  │   Send M1 screenshot                    │   └─ Haiku M1 check (~$0.05)
  │                                         │
  ├─ Pick up confirmed trade ──────────────→ GET /pending_trade
  │   Execute market orders                 │
  │                                         │
  ├─ Report execution ─────────────────────→ POST /trade_executed
  ├─ Report close (TP/SL) ────────────────→ POST /trade_closed
  │                                         │
  └─ Watch expires at 20:00 MEZ             └─ SQLite tracking + Telegram alerts
```

## Key Features

**Analysis (ICT Methodology)**
- Two-tier AI: Sonnet screens quickly, Opus analyzes deeply (saves cost on dead markets)
- 4 timeframes: D1 (strategic bias), H4 (OTE zones), H1 (structure), M5 (entry triggers)
- 12-point ICT entry checklist: BOS, ChoCH, Order Blocks, FVGs, liquidity sweeps, OTE alignment
- Daily web search for fundamentals (cached per pair per day)

**Smart Entry (v3.0)**
- No blind limit orders — EA watches entry zone locally (zero API cost)
- When price reaches zone: M1 Haiku confirmation checks for bullish/bearish reaction
- Max 3 confirmation attempts per setup, 60s cooldown between checks
- High-confidence setups (checklist ≥7/12) auto-watch without manual approval

**Risk Management (FTMO-compliant)**
- News filter: blocks trades ±2 min around high-impact events
- Daily drawdown limit (default 3%)
- Max open trades cap (default 2)
- Correlation conflict prevention
- SL hard cap at 70 pips

**Trade Lifecycle**
- Full tracking: queued → executed → TP1/TP2/SL → closed
- Break-even after TP1, trailing stop to TP2
- SQLite persistence with performance stats
- Telegram notifications at every stage

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/scan` | Re-analyze or re-send last result |
| `/status` | Bot status and last scan time |
| `/stats` | Performance stats (win rate, P&L, by confidence) |
| `/drawdown` | Daily P&L and risk dashboard |
| `/news` | Upcoming high-impact news events |
| `/reset` | Force-close stale open trades in DB |
| `/help` | Show all commands |

## Quick Start

```bash
# 1. Clone and configure
git clone https://github.com/manuham/AI-Trade-Bot-ICT.git
cd AI-Trade-Bot-ICT
cp server/.env.example server/.env
# Edit server/.env with your API keys

# 2. Run with Docker
docker-compose build --no-cache
docker-compose up -d

# 3. Install the EA in MT5 (see setup.md)
```

See [setup.md](setup.md) for full deployment instructions.

## Project Structure

```
├── mt5/
│   ├── AI_Analyst.mq5            # MT5 Expert Advisor (v6.00)
│   ├── GBPJPY_Analyst.mq5       # Legacy single-pair EA (deprecated)
│   └── SwingLevels.mq5          # Swing high/low indicator (v2.00)
├── server/
│   ├── main.py                   # FastAPI app — endpoints, watch system, trade queue
│   ├── analyzer.py               # Claude API — two-tier analysis + Haiku confirmation
│   ├── telegram_bot.py           # Telegram — alerts, commands, Execute/Skip buttons
│   ├── trade_tracker.py          # SQLite — trade lifecycle, P&L, performance stats
│   ├── news_filter.py            # FTMO — ForexFactory calendar, ±2 min blocking
│   ├── pair_profiles.py          # Per-pair config (digits, spreads, kill zone times)
│   ├── models.py                 # Pydantic models (MarketData, TradeSetup, WatchTrade)
│   ├── config.py                 # Environment variables
│   ├── requirements.txt          # Python dependencies
│   └── .env.example              # Environment variable template
├── docs/
│   ├── ICT_Strategy_Research.docx
│   ├── Integration_Notes.md
│   └── MQL5_Implementation_Spec.md
├── Dockerfile
├── docker-compose.yml
├── setup.md                      # Deployment guide
└── CLAUDE.md                     # Claude Code project context
```

## Cost Estimate

| Component | Cost | When |
|-----------|------|------|
| Sonnet screening | ~$0.40 | Every scan |
| Opus full analysis | ~$2.00 | Only when Sonnet finds a setup |
| Haiku M1 confirmation | ~$0.03-0.08 | Only when price reaches zone (max 3x) |
| Fundamentals web search | ~$0.30 | Once per pair per day (cached) |
| **Daily total** | **~$2.50-5.00** | 1-2 scans/day + confirmations |

## Configuration

### Environment Variables (`server/.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Claude API key |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token |
| `TELEGRAM_CHAT_ID` | — | Your Telegram chat ID |
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8000` | Server port |
| `LOG_LEVEL` | `INFO` | Logging level |
| `MAX_DAILY_DRAWDOWN_PCT` | `3.0` | Daily drawdown limit (%) |
| `MAX_OPEN_TRADES` | `2` | Max concurrent open trades |

### MT5 EA Key Inputs

| Parameter | Default | Description |
|-----------|---------|-------------|
| `InpServerURL` | `http://127.0.0.1:8000` | Server base URL |
| `InpRiskPercent` | `1.0` | Risk per trade (%) |
| `InpKillZoneStart` | `8` | Analysis start hour (MEZ) |
| `InpKillZoneEnd` | `11` | Kill zone end hour (MEZ) |
| `InpConfirmCooldown` | `60` | Seconds between M1 checks |
| `InpMode` | `leader` | `leader` (analyze+trade) or `follower` (trade only) |

## Deployment

**VPS:** Hetzner recommended, Ubuntu 22.04+, Docker installed

```bash
# Update
ssh root@YOUR_VPS_IP
cd AI-Trade-Bot-ICT
git pull origin main
docker-compose build --no-cache && docker-compose down && docker-compose up -d

# Verify
curl http://localhost:8000/health
docker-compose logs -f
```
