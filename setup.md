# GBPJPY AI Trade Analyst Bot — Setup Guide

## Architecture

```
MT5 EA (v6.00) ──screenshots+data──> FastAPI Server ──analysis──> Claude API
                                          │
                                          ├──> Telegram Bot ──> You
                                          └──> SQLite DB (trade tracking)
```

Three components:
1. **MT5 Expert Advisor** (`mt5/AI_Analyst.mq5`) — captures D1/H4/H1/M5 charts, watches entry zones, executes trades
2. **Python FastAPI Server** (`server/`) — two-tier AI analysis, smart entry confirmation, trade queue
3. **Telegram Bot** (integrated in server) — trade alerts, Execute/Skip buttons, performance stats

---

## Prerequisites

- A VPS (Hetzner recommended) running Ubuntu 22.04+
- Docker and Docker Compose installed on the VPS
- MetaTrader 5 running on a Windows machine
- A Telegram account
- An Anthropic API key (Claude API)

---

## Step 1: Create a Telegram Bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Choose a name (e.g., "GBPJPY Analyst") and username (e.g., `gbpjpy_analyst_bot`)
4. Copy the **bot token** — you'll need it later
5. Send a message to your new bot (just say "hello")
6. Get your **chat ID**: search for **@userinfobot** on Telegram, start it, and it will show your chat ID

---

## Step 2: Get an Anthropic API Key

1. Go to [console.anthropic.com](https://console.anthropic.com/)
2. Sign up or log in
3. Navigate to **API Keys**
4. Create a new key and copy it

---

## Step 3: Deploy the Server on VPS

### 3.1 — Initial server setup

```bash
# SSH into your VPS
ssh root@YOUR_VPS_IP

# Install Docker (if not already installed)
curl -fsSL https://get.docker.com | sh
```

### 3.2 — Clone the repository

```bash
git clone https://github.com/manuham/GBPJPY-AI-Trade-Analyst-Bot.git
cd GBPJPY-AI-Trade-Analyst-Bot
```

### 3.3 — Configure environment variables

```bash
cp server/.env.example server/.env
nano server/.env
```

Fill in your values:
```
ANTHROPIC_API_KEY=sk-ant-your-key-here
TELEGRAM_BOT_TOKEN=123456789:your-token-here
TELEGRAM_CHAT_ID=your-chat-id-here
HOST=0.0.0.0
PORT=8000
LOG_LEVEL=INFO
MAX_DAILY_DRAWDOWN_PCT=3.0
MAX_OPEN_TRADES=2
```

### 3.4 — Build and start

```bash
docker-compose build --no-cache
docker-compose up -d
```

### 3.5 — Verify it's running

```bash
# Check container status
docker-compose ps

# Check logs
docker-compose logs -f

# Test health endpoint
curl http://localhost:8000/health
```

You should see `{"status":"ok","pairs_analyzed":[],...}`.

### 3.6 — Open the firewall port

```bash
ufw allow 8000/tcp
```

> **Security note**: For production, consider restricting port 8000 to your MT5 machine's IP only.

---

## Step 4: Configure the MT5 Expert Advisor

### 4.1 — Allow WebRequest URLs in MT5

1. Open MetaTrader 5
2. Go to **Tools > Options > Expert Advisors**
3. Check **"Allow WebRequest for listed URL"**
4. Click **Add** and enter: `http://YOUR_VPS_IP:8000`
   (Only the base URL is needed — it covers all endpoints)
5. Click **OK**

### 4.2 — Install the EA

1. Copy `mt5/AI_Analyst.mq5` to your MT5 data folder:
   ```
   MQL5/Experts/AI_Analyst.mq5
   ```
   (In MT5: File > Open Data Folder > MQL5 > Experts)
2. In MT5 Navigator, right-click **Expert Advisors** and select **Refresh**
3. Compile the EA: double-click to open in MetaEditor, press F7

### 4.3 — Install the SwingLevels indicator (optional)

1. Copy `mt5/SwingLevels.mq5` to:
   ```
   MQL5/Indicators/SwingLevels.mq5
   ```
2. Compile in MetaEditor (F7)
3. Drag onto your GBPJPY chart — it draws swing high/low levels

### 4.4 — Attach the EA to a GBPJPY chart

1. Open a **GBPJPY** chart (any timeframe — the EA opens its own temporary charts)
2. Drag **AI_Analyst** from the Navigator onto the chart
3. In the **Inputs** tab, configure:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `InpServerURL` | `http://127.0.0.1:8000` | Change to `http://YOUR_VPS_IP:8000` |
| `InpRiskPercent` | `1.0` | Risk per trade as % of account balance |
| `InpKillZoneStart` | `8` | London Kill Zone start hour (MEZ) |
| `InpKillZoneStartMin` | `0` | Start minute |
| `InpKillZoneEnd` | `11` | Kill Zone end hour (MEZ) |
| `InpConfirmCooldown` | `60` | Seconds between M1 confirmation checks |
| `InpTimezoneOffset` | `0` | Broker server time minus MEZ (e.g., if broker is UTC+2 and MEZ is UTC+1, set to 1) |
| `InpCooldownMinutes` | `30` | Minimum time between full scans |
| `InpScreenWidth` | `2560` | Screenshot width in pixels |
| `InpScreenHeight` | `1440` | Screenshot height in pixels |
| `InpMode` | `leader` | `leader` = analyze+trade, `follower` = trade only (for multi-account) |
| `InpManualTrigger` | `false` | Set to `true` to force an immediate scan |
| `InpMagicNumber` | `20250101` | Unique ID for this EA's trades |

4. Check **"Allow Algo Trading"** and click **OK**
5. Make sure the **AutoTrading** button in the MT5 toolbar is enabled (green icon)

---

## Step 5: Test the Connection

### Quick test from VPS

```bash
curl http://YOUR_VPS_IP:8000/health
```

### Test from MT5

1. Set `InpManualTrigger = true` in the EA inputs to force an immediate scan
2. Check the **Experts** tab in MT5 for log messages
3. Check your Telegram for the analysis result
4. Set `InpManualTrigger` back to `false` after testing

### Test Telegram bot

Send these commands to your bot on Telegram:
- `/start` — verify the bot responds
- `/status` — check connection status
- `/help` — see all available commands

---

## How It Works (v3.0 — Smart Entry)

1. **08:00 MEZ** — EA triggers, captures D1/H4/H1/M5 screenshots + market data
2. **Sonnet screening** — quick viability check (H1+M5 only, ~$0.40)
3. **Opus full analysis** — if Sonnet says "has setup" (~$2.00, with web search for fundamentals)
4. **Telegram alert** — setup details with checklist score, R:R, confluence factors
5. **Auto-watch** — setups scoring ≥7/12 on ICT checklist start watching automatically
6. **Zone monitoring** — EA polls `/watch_trade` and monitors price locally (no API cost)
7. **M1 confirmation** — when price reaches entry zone, EA sends M1 screenshot to Haiku (~$0.05)
8. **Execution** — if Haiku confirms bullish/bearish reaction, trade is queued and executed
9. **Position management** — break-even after TP1, trailing stop, close reporting
10. **Watch expiry** — unfilled watches expire at 20:00 MEZ

---

## Troubleshooting

### EA says "URL not allowed"
- Go to Tools > Options > Expert Advisors > Allowed URLs and add `http://YOUR_VPS_IP:8000`
- Only the base URL is needed (no need to add individual endpoints)

### EA says "WebRequest failed"
- Check that the VPS firewall allows port 8000: `ufw status`
- Verify the server is running: `docker-compose ps`
- Test from the MT5 machine: open `http://YOUR_VPS_IP:8000/health` in a browser

### No Telegram messages
- Verify `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `server/.env`
- Make sure you've sent at least one message to the bot first
- Check server logs: `docker-compose logs -f`

### Claude API errors
- Verify `ANTHROPIC_API_KEY` in `server/.env`
- Check your API credit balance at [console.anthropic.com](https://console.anthropic.com/)
- Check server logs for specific error messages

### Watch never triggers
- Ensure `InpKillZoneStart` and `InpKillZoneEnd` match your timezone offset
- Check that the EA is polling: look for "PollWatchTrade" in the Experts tab
- Verify the server has an active watch: `curl http://YOUR_VPS_IP:8000/health`

---

## Operations

### View logs
```bash
docker-compose logs -f
```

### Restart the server
```bash
docker-compose restart
```

### Update to latest version
```bash
git pull origin main
docker-compose build --no-cache && docker-compose down && docker-compose up -d
```

### Stop the server
```bash
docker-compose down
```

### Check trade stats
```bash
curl http://YOUR_VPS_IP:8000/stats
```
Or use `/stats` in Telegram.
