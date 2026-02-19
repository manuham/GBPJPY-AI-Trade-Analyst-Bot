# v3.0 — Smart entry confirmation + London Kill Zone
from __future__ import annotations

import base64
import json
import logging
import os
import sqlite3
from datetime import date
from typing import Optional

import anthropic

from config import ANTHROPIC_API_KEY
from models import AnalysisResult, MarketData, TradeSetup
from pair_profiles import get_profile
from trade_tracker import get_recent_closed_for_pair

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Daily fundamentals cache  (in-memory + SQLite persistence)
# Survives Docker restarts by persisting to /data/fundamentals_cache.db
# ---------------------------------------------------------------------------
_fundamentals_cache: dict[str, dict] = {}  # key = "GBPJPY:2026-02-09"
_CACHE_DB_DIR = os.getenv("DATA_DIR", "/data")
_CACHE_DB_PATH = os.path.join(_CACHE_DB_DIR, "fundamentals_cache.db")


def _init_cache_db():
    """Create the fundamentals cache table if it doesn't exist."""
    os.makedirs(_CACHE_DB_DIR, exist_ok=True)
    conn = sqlite3.connect(_CACHE_DB_PATH, timeout=5)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fundamentals_cache (
            cache_key TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            cache_date TEXT NOT NULL,
            text_content TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def _load_cache_from_db():
    """Load today's cached fundamentals from SQLite into memory."""
    try:
        _init_cache_db()
        today = date.today().isoformat()
        conn = sqlite3.connect(_CACHE_DB_PATH, timeout=5)
        rows = conn.execute(
            "SELECT cache_key, text_content FROM fundamentals_cache WHERE cache_date = ?",
            (today,),
        ).fetchall()
        conn.close()
        for key, text in rows:
            _fundamentals_cache[key] = {"text": text, "date": today}
        if rows:
            logger.info("Loaded %d cached fundamentals from disk", len(rows))
    except Exception as e:
        logger.warning("Failed to load fundamentals cache from disk: %s", e)


# Load on module import (runs once at server startup)
_load_cache_from_db()


def _cache_key(symbol: str) -> str:
    return f"{symbol}:{date.today().isoformat()}"


def get_cached_fundamentals(symbol: str) -> Optional[str]:
    """Return cached fundamentals text for today, or None."""
    entry = _fundamentals_cache.get(_cache_key(symbol))
    return entry["text"] if entry else None


def store_fundamentals(symbol: str, text: str):
    """Cache fundamentals text for today (memory + disk)."""
    key = _cache_key(symbol)
    today = date.today().isoformat()
    _fundamentals_cache[key] = {"text": text, "date": today}

    # Persist to SQLite
    try:
        conn = sqlite3.connect(_CACHE_DB_PATH, timeout=5)
        conn.execute(
            "INSERT OR REPLACE INTO fundamentals_cache (cache_key, symbol, cache_date, text_content) VALUES (?, ?, ?, ?)",
            (key, symbol, today, text),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("Failed to persist fundamentals cache: %s", e)

    logger.info("Fundamentals cached for %s (%d chars)", symbol, len(text))


# ---------------------------------------------------------------------------
# Image encoding
# ---------------------------------------------------------------------------
def _encode_image(image_bytes: bytes) -> str:
    return base64.standard_b64encode(image_bytes).decode("utf-8")


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------
def build_system_prompt(symbol: str, profile: dict, fundamentals: Optional[str] = None) -> str:
    """Build the full ICT analysis system prompt, parameterized by pair."""
    base = profile["base_currency"]
    quote = profile["quote_currency"]
    search_queries = ", ".join(f'"{q}"' for q in profile["search_queries"])

    # Fundamentals section: either inject cached text or instruct web search
    if fundamentals:
        fundamentals_section = f"""### Step 0 — Fundamentals (pre-loaded)
The following fundamental analysis was gathered earlier today. Use it as context — do NOT run web searches.

{fundamentals}"""
    else:
        fundamentals_section = f"""### Step 0 — Fundamentals (web search)
Use web search to check current {base} and {quote} drivers, breaking news, and the economic calendar for the next 24 hours. Search for {search_queries}."""

    session_name = profile.get("session_name", "London & NY Overlap (08:00-17:00 MEZ)")
    session_context = profile.get("session_context", "").format(symbol=symbol)
    session_rules = profile.get("session_rules", "").format(symbol=symbol)

    return f"""You are a senior institutional FX analyst specializing in {symbol} during the {session_name} using ICT (Inner Circle Trader) methodology. You are analyzing live {symbol} charts from MetaTrader 5.

## CONTEXT
- Pair: {symbol}
- Session: **{session_name}** — the highest-probability window for {symbol}
- Risk per trade: 1%
- TP strategy: 50% closed at TP1, runner to TP2
- Charts provided: **D1 (Daily)**, **H4 (4-Hour)**, **H1 (Hourly)**, **M5 (5-Minute)** — top-down
- The charts include horizontal swing-level lines drawn by a custom indicator
- Market data JSON includes: previous day H/L/C, weekly H/L, Asian session range, RSI(14), ATR(14) for all timeframes
- **IMPORTANT**: Setups from this analysis will NOT be executed immediately. The EA will WATCH the entry zone and only enter when price reaches it AND an M1 confirmation shows a reaction. So propose setups even if price is not yet at the entry zone — the system will wait.

## YOUR TASK

{fundamentals_section}

### Step 1 — MANDATORY: Daily Trend & Structure (D1 chart)
Before ANYTHING, analyze the D1 (Daily) chart:
- Identify the last 10-20 daily candles for swing structure
- Are they making higher highs + higher lows (BULLISH) or lower highs + lower lows (BEARISH)?
- Note the **previous day high/low/close** from the market data — these are KEY institutional reference levels
- Note the **current week high/low** — price often targets these for liquidity sweeps
- Check D1 RSI: >70 = overbought (caution for longs), <30 = oversold (caution for shorts), 40-60 = neutral
- The D1 trend provides strategic context — all trades should ideally align with this.

### Step 1b — H4 Tactical Bias & OTE Zone (H4 chart)
Analyze the H4 chart for tactical positioning within the D1 trend:
- Identify H4 swing structure — does it align with D1 bias?
- **Premium/Discount Zone**: Calculate the H4 range (recent swing high to swing low):
  - DISCOUNT zone (below 50%) — favorable for longs
  - PREMIUM zone (above 50%) — favorable for shorts
- **OTE (Optimal Trade Entry) Zone**: Calculate 62-79% Fibonacci retracement of the last H4 impulse move:
  - Optimal entry at the 70.5% level
  - Entries within the 62-79% zone are high-probability
  - Entries outside OTE need additional confluence to justify
- **H4 Order Blocks**: Identify OBs on H4 — these are valid for max 8 candles (~32 hours)
- Check H4 RSI for momentum confirmation
- Set "h4_trend" to "bullish"/"bearish"/"ranging"

### Step 2 — H1 Structure & Key Levels
- Identify H1 swing structure within the context of D1 and H4 trends
- **H1 Order Blocks**: Valid for max 30 candles (~30 hours)
- Mark BOS (Break of Structure) and ChoCH (Change of Character) with exact prices
- Check H1 RSI for confirmation

### Step 3 — Session Levels & Liquidity
Use the provided market data to identify key levels:
- **Previous Day High (PDH)** and **Previous Day Low (PDL)** — institutional liquidity magnets
- **Previous Day Close (PDC)** — acts as support/resistance pivot
- **Asian Session Range** (asian_high/asian_low) — London often sweeps one side of the Asian range before reversing
- **Weekly High/Low** — key swing liquidity targets
- Mark which of these levels price is currently near or has recently swept

{session_context}

### Step 4 — Multi-Timeframe Alignment (D1 → H4 → H1 → M5)
- Market structure per timeframe: BOS, ChoCH locations with exact prices
- Is M5 structure aligned with H1, H4, and D1, or showing early reversal signs?
- Key swing highs/lows with exact price levels
- Note any timeframe divergences

### Step 5 — Key ICT Levels (be precise with prices)
Apply these SPECIFIC criteria when identifying levels:

**Order Blocks (OB):**
- H4 OBs: valid for max 8 candles (~32 hours). Mark as stale if older.
- H1 OBs: valid for max 30 candles (~30 hours). Mark as stale if older.
- Specify if tested or untested — untested OBs are strongest
- The displacement candle creating the OB must have a body ≥15 pips (MSS displacement)

**Fair Value Gaps (FVG):**
- M5 FVGs: minimum 15 pips gap size to be significant
- H1 FVGs: minimum 25 pips gap size to be significant
- **Consequent Encroachment (CE)**: Calculate the 50% midpoint of each FVG — this is the OPTIMAL entry target
- Specify if filled, partially filled, or open

**Market Structure Shift (MSS):**
- Confirmed when a displacement candle (body ≥15 pips) breaks a swing high/low
- This is the primary entry trigger on M5

**Liquidity:**
- Institutional liquidity pools (equal highs/lows, stop clusters)
- Liquidity sweep = price exceeds a key level by ≥5 pips then reverses
- Breaker blocks, mitigation blocks where applicable

- Distinguish between: where price BOUNCED FROM vs where price IS NOW (these are different!)

### Step 6 — Setup Generation
Propose setups that satisfy these criteria:
1. D1+H4 trend-aligned preferred. Counter-trend allowed only with BOS/ChoCH reversal on H1 or M5
2. Entry at or near **CE level** (50% midpoint) of a valid FVG, within or near an active OB zone
3. Entry ideally within the **OTE zone** (62-79% Fibonacci of last H4 impulse)
4. **SL placement**: 5-10 pips beyond the Order Block extreme, with a **hard cap of 70 pips**
5. Minimum 1:1.2 R:R on TP1, minimum 1:2 R:R on TP2
6. At least 2 confluence factors
7. Clear invalidation level

For counter-trend setups:
- Label them "counter_trend: true"
- Show a ChoCH or BOS on H1 or M5 confirming the reversal
- Rate confidence as "medium" at most

Setups from equilibrium zone are acceptable if there is a clear directional trigger (BOS, FVG fill, session level sweep).

IMPORTANT: Your goal is to find tradeable setups. Most sessions have at least one valid entry — look harder before saying "no trade". Even a cautious low-confidence setup with a clear SL is better than no setup.

### Step 6b — Trend Alignment Score
For each setup, assess D1/H4/H1/M5 trend direction and compute an alignment score:
- Score = how many of D1/H4/H1/M5 agree with the trade bias (e.g., "4/4 bearish" if all bearish for a short)
- Example: D1 bearish, H4 bearish, H1 bearish, M5 bullish → 3/4 bearish for a short → "3/4 bearish (M5 diverging)"
- Include this as "trend_alignment" in the JSON output
- Set "d1_trend", "h4_trend", "h1_trend" in the JSON output

### Step 6c — Entry Distance & Status
For each setup, calculate how far price currently is from the entry zone:
- "entry_distance_pips": distance from current bid to entry zone midpoint, in pips
- "entry_status": classify as:
  - "at_zone" = price is within or very close to the entry zone (<10 pips)
  - "approaching" = price is moving toward entry zone (10-40 pips away)
  - "requires_pullback" = price needs to retrace significantly to reach entry (>40 pips)

### Step 6d — Negative Factors
For EVERY setup, identify 1-3 factors that work AGAINST the trade. Be honest — this helps the trader assess risk:
- Examples: "D1 trend opposes trade", "RSI overbought", "Price far from entry zone", "Asian session — low volume",
  "Near untested supply zone", "Wide spread", "H1 shows no clear BOS", "Counter-trend without strong reversal signal",
  "Entry outside OTE zone", "FVG too small", "OB is stale (beyond validity)", "SL near 70 pip cap"
- Include as "negative_factors" array in the JSON output

### Step 6e — Level Validation
Apply these rules when evaluating support/resistance levels:
- A level that has been tested 3+ times is WEAKENED — more likely to break on the next test
- Distinguish between: "untested" (fresh, strongest), "tested once" (confirmed), "tested 2x" (still valid), "tested 3x+" (weakened, likely to break)
- A level being tested RIGHT NOW is not the same as a level price bounced from in the past
- If your entry zone is at a weakened level (3+ tests), note this in negative_factors

### Step 6f — ICT Entry Checklist
For EVERY setup, score against this 12-point checklist:
1. D1 bias identified (bullish/bearish/ranging)
2. H4 bias aligns with D1
3. Price in correct Premium/Discount zone for bias (longs in discount, shorts in premium)
4. Active Order Block found (H4 or H1, within validity period: H4 ≤8 candles, H1 ≤30 candles)
5. Market Structure Shift confirmed on M5 (displacement candle body ≥15 pips)
6. Fair Value Gap present and meets minimum size (M5 ≥15 pips, H1 ≥25 pips)
7. Entry at or near CE level (50% midpoint) of FVG
8. Entry within OTE zone (62-79% Fibonacci of H4 impulse)
9. Liquidity sweep detected (price exceeded key level ≥5 pips then reversed)
10. SL within 70 pip cap
11. R:R ≥1:2 on TP2
12. No conflicting high-impact news within 30 minutes

Report as "checklist_score": "X/12" in the JSON output.
High confidence = 10-12/12, Medium-High = 8-9/12, Medium = 6-7/12, Low = 4-5/12. Below 4 = do not propose.

### Step 7 — NO TRADE Decision
Return an EMPTY setups array ONLY if:
- Market is in a dead-flat range with zero structure (rare)
- Spread is widened (off-session, holiday)
- High-impact news within 30 minutes
- You genuinely cannot identify ANY level to trade from

## OUTPUT FORMAT
Respond with ONLY valid JSON matching this structure:
{{
  "setups": [
    {{
      "bias": "long" or "short",
      "entry_min": price,
      "entry_max": price,
      "stop_loss": price,
      "sl_pips": number,
      "tp1": price,
      "tp1_pips": number,
      "tp2": price,
      "tp2_pips": number,
      "rr_tp1": number,
      "rr_tp2": number,
      "confluence": ["reason1", "reason2", "reason3"],
      "invalidation": "description",
      "timeframe_type": "scalp" or "intraday" or "swing",
      "confidence": "high" or "medium_high" or "medium" or "low",
      "news_warning": "description or null",
      "counter_trend": true or false,
      "h1_trend": "bullish" or "bearish" or "ranging",
      "h4_trend": "bullish" or "bearish" or "ranging",
      "d1_trend": "bullish" or "bearish" or "ranging",
      "trend_alignment": "4/4 bearish" or "3/4 bullish (M5 diverging)" etc,
      "price_zone": "premium" or "discount" or "equilibrium",
      "entry_distance_pips": number (pips from current price to entry zone midpoint),
      "entry_status": "at_zone" or "approaching" or "requires_pullback",
      "negative_factors": ["factor1", "factor2"],
      "checklist_score": "X/12"
    }}
  ],
  "h1_trend_analysis": "2-3 sentences describing D1+H4+H1 swing structure and dominant trend",
  "market_summary": "2-3 sentence summary including key session levels",
  "primary_scenario": "description",
  "alternative_scenario": "description",
  "fundamental_bias": {profile['fundamental_bias_options']},
  "upcoming_events": ["event1", "event2"]
}}

## RULES
- Analyze D1 trend FIRST, then H4, then H1/M5 — strict top-down
- Use the H4 chart to determine OTE zone and Premium/Discount — this is the tactical timeframe
- Prefer trend-aligned trades (D1+H4 aligned), but counter-trend is allowed with reversal confirmation on H1/M5
- Target CE level (50% midpoint of FVG) as optimal entry within OB zones
- Apply OB validity limits: H4 ≤8 candles, H1 ≤30 candles — stale OBs are weak
- Apply FVG minimums: M5 ≥15 pips, H1 ≥25 pips — smaller gaps are noise
- SL hard cap: 70 pips maximum, placed 5-10 pips beyond OB extreme
- Use session levels (PDH/PDL/PDC, Asian range, weekly H/L) as confluence and targets
- Consider {symbol} spread (~{profile['typical_spread']}) in SL/TP calculations
- Use RSI as confirmation, not as a standalone signal
- Flag any setups near high-impact news events
- {session_rules}
- Prefer setups where entry is near current price or approaching (entry_status "at_zone" or "approaching") — these are more likely to trigger during the kill zone window.
- Always respond with valid JSON, nothing else"""


def _build_screening_prompt(symbol: str, profile: dict, fundamentals: Optional[str] = None) -> str:
    """Lightweight screening prompt for Sonnet — quick yes/no on trade viability.
    Only receives H1+M5 charts (D1 info comes from market data numbers)."""
    fund_section = ""
    if fundamentals:
        fund_section = f"\n\nFundamental context (gathered earlier today):\n{fundamentals}"

    return f"""You are a quick-scan FX analyst. Look at these {symbol} charts (H1, M5) and the market data to determine if there is ANY potential ICT trade setup worth analyzing further.{fund_section}

The market data JSON includes D1/H4 RSI + ATR + previous day levels, so you can assess D1 and H4 bias without those charts.

IMPORTANT: Your job is to PASS setups through for detailed analysis, not to filter them out.
Lean toward "has_setup: true" if you see ANY of these:
- Price near a key level (OB, FVG, PDH/PDL, Asian range)
- Clear H1 trend with pullback opportunity
- Recent BOS or ChoCH on H1 or M5
- Price reacting to a swing level

Only say "has_setup: false" if the market is genuinely dead (no structure, tight range, no levels nearby).

Respond with ONLY this JSON:
{{
  "has_setup": true or false,
  "h1_trend": "bullish" or "bearish" or "ranging",
  "reasoning": "1-2 sentences explaining why trade or no trade",
  "market_summary": "1-2 sentence market overview"
}}"""


# ---------------------------------------------------------------------------
# Performance feedback builder (Feature 5)
# ---------------------------------------------------------------------------
def _build_performance_feedback(symbol: str) -> Optional[str]:
    """Build rich performance feedback from recent closed trades for this pair.

    Includes per-trade details AND pattern analysis so the AI can learn
    what works and what doesn't.
    """
    try:
        trades = get_recent_closed_for_pair(symbol, limit=20)
    except Exception as e:
        logger.warning("Failed to get performance history for %s: %s", symbol, e)
        return None

    if not trades:
        return None

    # --- Section 1: Per-trade details ---
    lines = [f"## Your last {len(trades)} completed trades for {symbol}"]
    wins = 0
    losses = 0
    total_pnl = 0.0

    for i, t in enumerate(trades, 1):
        outcome = t.get("outcome", "?")
        pnl = t.get("pnl_pips") or 0
        bias = (t.get("bias") or "?").upper()
        conf = t.get("confidence") or "?"
        trend_align = t.get("trend_alignment") or ""
        h1_trend = t.get("h1_trend") or "?"
        entry_st = t.get("entry_status") or "?"
        zone = t.get("price_zone") or "?"
        ct = " [CT]" if t.get("counter_trend") else ""
        neg = t.get("negative_factors") or ""
        date_str = (t.get("closed_at") or "")[:10]

        emoji = {"full_win": "W", "partial_win": "PW", "loss": "L"}.get(outcome, outcome)
        trend_info = trend_align if trend_align else h1_trend
        detail = f"  {i}. {emoji} {bias} ({conf}) {pnl:+.0f}p | {trend_info} | {zone} | entry:{entry_st}{ct}"
        if neg and outcome == "loss":
            detail += f" | risks: {neg}"
        lines.append(detail)

        if outcome in ("full_win", "partial_win"):
            wins += 1
        elif outcome == "loss":
            losses += 1
        total_pnl += pnl

    total = wins + losses
    wr = (wins / total * 100) if total > 0 else 0
    lines.append(f"\nOverall: {wr:.0f}% win rate ({wins}W / {losses}L) | Net: {total_pnl:+.0f} pips")

    # --- Section 2: Pattern analysis (only if enough data) ---
    if total >= 3:
        lines.append("\n## Pattern Analysis")

        def _wr_line(label, trade_list):
            w = sum(1 for t in trade_list if t.get("outcome") in ("full_win", "partial_win"))
            n = sum(1 for t in trade_list if t.get("outcome") in ("full_win", "partial_win", "loss"))
            if n > 0:
                lines.append(f"  {label}: {w/n*100:.0f}% ({w}/{n})")

        # Counter-trend vs trend-aligned
        ct_trades = [t for t in trades if t.get("counter_trend")]
        ta_trades = [t for t in trades if not t.get("counter_trend")]
        if ct_trades:
            _wr_line("Counter-trend", ct_trades)
        if ta_trades:
            _wr_line("Trend-aligned", ta_trades)

        # By trend alignment score (4-timeframe: D1/H4/H1/M5)
        for prefix in ("4/4", "3/4", "2/4", "1/4", "3/3", "2/3"):
            aligned = [t for t in trades if (t.get("trend_alignment") or "").startswith(prefix)]
            if aligned:
                _wr_line(f"{prefix} aligned", aligned)

        # By confidence (4 tiers)
        for conf in ("high", "medium_high", "medium", "low"):
            conf_trades = [t for t in trades if t.get("confidence") == conf]
            if conf_trades:
                _wr_line(f"{conf.upper().replace('_', '-')} confidence", conf_trades)

        # By entry status
        for status in ("at_zone", "approaching", "requires_pullback"):
            st_trades = [t for t in trades if t.get("entry_status") == status]
            if st_trades:
                _wr_line(f"Entry '{status}'", st_trades)

        # By price zone
        for zone in ("premium", "discount", "equilibrium"):
            z_trades = [t for t in trades if t.get("price_zone") == zone]
            if z_trades:
                _wr_line(f"{zone.upper()} zone", z_trades)

        # By bias direction
        for bias_dir in ("long", "short"):
            b_trades = [t for t in trades if t.get("bias") == bias_dir]
            if b_trades:
                _wr_line(bias_dir.upper(), b_trades)

    # --- Section 3: Actionable instructions ---
    lines.append("\n## Instructions")
    lines.append("Use the patterns above to improve your current analysis:")
    lines.append("- If a pattern consistently loses, AVOID it or rate confidence LOW")
    lines.append("- If a pattern consistently wins, actively LOOK FOR similar setups")
    lines.append("- If 'requires_pullback' entries lose often, prefer 'at_zone' entries")
    lines.append("- If counter-trend trades lose, only propose them with very strong reversal evidence")
    lines.append("- Mention any relevant pattern insight in your market_summary")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# User content builders
# ---------------------------------------------------------------------------
def _build_image_content(
    screenshot_d1: bytes,
    screenshot_h4: bytes,
    screenshot_h1: bytes,
    screenshot_m5: bytes,
    market_data: MarketData,
) -> list[dict]:
    """Build the full multi-modal user message content for Opus (all 4 charts + OHLC)."""
    content: list[dict] = []

    for label, img_bytes in [
        ("D1 (Daily)", screenshot_d1),
        ("H4 (4-Hour)", screenshot_h4),
        ("H1 (Hourly)", screenshot_h1),
        ("M5 (5-Minute)", screenshot_m5),
    ]:
        if not img_bytes:
            continue  # Skip if screenshot not available (backward compat)
        content.append({"type": "text", "text": f"--- {label} Chart ---"})
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": _encode_image(img_bytes),
                },
            }
        )

    market_dict = market_data.model_dump()
    ohlc_summary = {
        "d1_bars": len(market_dict.get("ohlc_d1", [])),
        "h4_bars": len(market_dict.get("ohlc_h4", [])),
        "h1_bars": len(market_dict.get("ohlc_h1", [])),
        "m5_bars": len(market_dict.get("ohlc_m5", [])),
    }
    display_data = {k: v for k, v in market_dict.items() if not k.startswith("ohlc_")}
    display_data["ohlc_bar_counts"] = ohlc_summary

    content.append(
        {
            "type": "text",
            "text": (
                "--- Market Data (includes session levels, RSI, ATR) ---\n"
                + json.dumps(display_data, indent=2)
                + "\n\n--- Full OHLC Data ---\n"
                + json.dumps(
                    {
                        "ohlc_d1": market_dict.get("ohlc_d1", []),
                        "ohlc_h4": market_dict.get("ohlc_h4", []),
                        "ohlc_h1": market_dict.get("ohlc_h1", []),
                        "ohlc_m5": market_dict.get("ohlc_m5", []),
                    }
                )
            ),
        }
    )

    return content


def _build_screening_content(
    screenshot_h1: bytes,
    screenshot_m5: bytes,
    market_data: MarketData,
) -> list[dict]:
    """Build lightweight content for Sonnet screening (H1+M5 only, no OHLC).
    Saves ~40% vs full content by skipping D1 image and OHLC arrays.
    D1 trend info comes from market data (RSI_D1, PDH/PDL/PDC, ATR_D1)."""
    content: list[dict] = []

    for label, img_bytes in [
        ("H1 (Hourly)", screenshot_h1),
        ("M5 (5-Minute)", screenshot_m5),
    ]:
        content.append({"type": "text", "text": f"--- {label} Chart ---"})
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": _encode_image(img_bytes),
                },
            }
        )

    # Summary market data only — no OHLC arrays
    market_dict = market_data.model_dump()
    display_data = {k: v for k, v in market_dict.items() if not k.startswith("ohlc_")}

    content.append(
        {
            "type": "text",
            "text": (
                "--- Market Data (includes D1/H4 RSI/ATR, session levels) ---\n"
                + json.dumps(display_data, indent=2)
            ),
        }
    )

    return content


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------
def _parse_response(raw_text: str) -> Optional[dict]:
    """Extract JSON from Claude's response, handling markdown code blocks."""
    text = raw_text.strip()

    if "```" in text:
        parts = text.split("```")
        for part in parts:
            cleaned = part.strip()
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
            if cleaned.startswith("{"):
                try:
                    return json.loads(cleaned)
                except json.JSONDecodeError:
                    continue

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    return None


# ---------------------------------------------------------------------------
# Tier 0: Fetch fundamentals (Sonnet + web search, once per day per pair)
# ---------------------------------------------------------------------------
async def fetch_fundamentals(symbol: str, profile: dict) -> str:
    """Fetch fundamentals via Sonnet with web search. Cached daily."""
    cached = get_cached_fundamentals(symbol)
    if cached:
        logger.info("Using cached fundamentals for %s", symbol)
        return cached

    if not ANTHROPIC_API_KEY:
        return ""

    base = profile["base_currency"]
    quote = profile["quote_currency"]
    search_queries = ", ".join(f'"{q}"' for q in profile["search_queries"])

    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    try:
        logger.info("Fetching fundamentals for %s via Sonnet + web search...", symbol)
        response = await client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=1500,
            system=f"You are a forex news analyst. Search for current {base}/{quote} fundamentals and news. Be concise.",
            tools=[
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 5,
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Search for {search_queries}. "
                        f"Summarize in bullet points:\n"
                        f"1. Current {base} drivers (max 3 bullets)\n"
                        f"2. Current {quote} drivers (max 3 bullets)\n"
                        f"3. Key upcoming events in next 24h\n"
                        f"4. Overall fundamental bias for {symbol}\n"
                        f"Keep it under 300 words."
                    ),
                }
            ],
        )

        raw_text = ""
        for block in response.content:
            if hasattr(block, "text") and block.text is not None:
                raw_text += block.text

        if raw_text:
            store_fundamentals(symbol, raw_text)
            logger.info("Fundamentals fetched for %s (%d chars)", symbol, len(raw_text))
        return raw_text

    except Exception as e:
        logger.error("Failed to fetch fundamentals for %s: %s", symbol, e)
        return ""


# ---------------------------------------------------------------------------
# Tier 1: Sonnet screening (cheap, every scan)
# ---------------------------------------------------------------------------
async def screen_charts(
    screenshot_d1: bytes,
    screenshot_h4: bytes,
    screenshot_h1: bytes,
    screenshot_m5: bytes,
    market_data: MarketData,
    profile: dict,
    fundamentals: Optional[str] = None,
) -> dict:
    """Quick Sonnet screen — is there a setup worth analyzing in detail?
    Cost-optimized: only H1+M5 images, no OHLC data, prompt caching.
    H4/D1 bias comes from market data (RSI, ATR).
    Returns dict with has_setup, h1_trend, reasoning, market_summary."""
    if not ANTHROPIC_API_KEY:
        return {"has_setup": True, "reasoning": "API key missing, skipping screen"}

    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    symbol = market_data.symbol

    # Lightweight content: only H1+M5, no OHLC (saves ~40% vs full content)
    user_content = _build_screening_content(screenshot_h1, screenshot_m5, market_data)
    user_content.append({"type": "text", "text": "Screen these H1/M5 charts plus the market data (D1/H4 bias from RSI/ATR/PDH/PDL). Is there a valid ICT setup? Reply with JSON only."})

    # System prompt with caching (90% discount on repeat calls for same pair)
    system_prompt = _build_screening_prompt(symbol, profile, fundamentals)

    try:
        logger.info("[%s] Sonnet screening (lightweight: H1+M5 only)...", symbol)
        response = await client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=500,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_content}],
        )

        raw_text = ""
        for block in response.content:
            if hasattr(block, "text") and block.text is not None:
                raw_text += block.text

        # Log token usage for cost tracking
        usage = response.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        logger.info(
            "[%s] Sonnet screening: input=%d (cache_read=%d, cache_write=%d), output=%d",
            symbol, usage.input_tokens, cache_read, cache_write, usage.output_tokens,
        )

        parsed = _parse_response(raw_text)
        if parsed:
            has_setup = parsed.get("has_setup", False)
            logger.info("[%s] Sonnet screening result: has_setup=%s — %s",
                        symbol, has_setup, parsed.get("reasoning", ""))
            return parsed

        logger.warning("[%s] Sonnet screening: failed to parse, escalating to Opus", symbol)
        return {"has_setup": True, "reasoning": "Parse failed, escalating"}

    except Exception as e:
        logger.error("[%s] Sonnet screening error: %s — escalating to Opus", symbol, e)
        return {"has_setup": True, "reasoning": f"Screen error: {e}"}


# ---------------------------------------------------------------------------
# Tier 2: Opus full analysis (expensive, only when screening passes)
# ---------------------------------------------------------------------------
async def analyze_charts_full(
    screenshot_d1: bytes,
    screenshot_h4: bytes,
    screenshot_h1: bytes,
    screenshot_m5: bytes,
    market_data: MarketData,
    profile: dict,
    fundamentals: Optional[str] = None,
) -> AnalysisResult:
    """Full Opus analysis with detailed ICT methodology (D1/H4/H1/M5)."""
    if not ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY not configured")
        return AnalysisResult(market_summary="Error: API key not configured")

    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    symbol = market_data.symbol

    user_content = _build_image_content(screenshot_d1, screenshot_h4, screenshot_h1, screenshot_m5, market_data)

    # If we have cached fundamentals, no web search needed
    use_web_search = fundamentals is None
    system_prompt = build_system_prompt(symbol, profile, fundamentals)

    # Inject performance feedback (Feature 5)
    perf_feedback = _build_performance_feedback(symbol)
    if perf_feedback:
        user_content.append({
            "type": "text",
            "text": f"--- Your Recent Trade Performance ---\n{perf_feedback}",
        })

    if use_web_search:
        user_content.append({
            "type": "text",
            "text": "Analyze the D1/H4/H1/M5 charts and market data above (including session levels, RSI, ATR). First use web_search to check fundamentals and news, then provide your full ICT analysis as JSON.",
        })
    else:
        user_content.append({
            "type": "text",
            "text": "Analyze the D1/H4/H1/M5 charts and market data above (including session levels, RSI, ATR) using the pre-loaded fundamentals. Provide your full ICT analysis as JSON.",
        })

    tools = []
    if use_web_search:
        tools.append({
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 10,
        })

    # Extended thinking: let Opus reason internally before outputting JSON
    # Improves setup quality and reduces false positives
    thinking_config = {"type": "enabled", "budget_tokens": 10000}

    try:
        logger.info("[%s] Opus full analysis (web_search=%s, thinking=10k, streaming)...", symbol, use_web_search)
        # Streaming required for extended thinking with large max_tokens
        async with client.messages.stream(
            model="claude-opus-4-20250514",
            max_tokens=16000,
            thinking=thinking_config,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=tools if tools else anthropic.NOT_GIVEN,
            messages=[{"role": "user", "content": user_content}],
        ) as stream:
            response = await stream.get_final_message()

        raw_text = ""
        for block in response.content:
            if hasattr(block, "text") and block.text is not None:
                raw_text += block.text

        # Log token usage for cost tracking
        usage = response.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        logger.info(
            "[%s] Opus response: %d chars, input=%d (cache_read=%d, cache_write=%d), output=%d",
            symbol, len(raw_text), usage.input_tokens, cache_read, cache_write, usage.output_tokens,
        )

        # If this was a web-search call, extract and cache fundamentals for next time
        if use_web_search and raw_text:
            # Store a summary of the response as fundamentals cache
            parsed_for_cache = _parse_response(raw_text)
            if parsed_for_cache:
                events = parsed_for_cache.get("upcoming_events", [])
                bias = parsed_for_cache.get("fundamental_bias", "neutral")
                cache_text = f"Fundamental bias: {bias}\nUpcoming events: {', '.join(events)}"
                store_fundamentals(symbol, cache_text)

        parsed = _parse_response(raw_text)
        if parsed is None:
            logger.warning("[%s] Failed to parse JSON from Opus response", symbol)
            return AnalysisResult(
                symbol=symbol,
                digits=profile["digits"],
                market_summary="Analysis received but JSON parsing failed.",
                raw_response=raw_text,
            )

        setups = []
        for s in parsed.get("setups", []):
            try:
                setups.append(TradeSetup(**s))
            except Exception as e:
                logger.warning("[%s] Failed to parse setup: %s", symbol, e)

        return AnalysisResult(
            symbol=symbol,
            digits=profile["digits"],
            setups=setups,
            h1_trend_analysis=parsed.get("h1_trend_analysis", ""),
            market_summary=parsed.get("market_summary", ""),
            primary_scenario=parsed.get("primary_scenario", ""),
            alternative_scenario=parsed.get("alternative_scenario", ""),
            fundamental_bias=parsed.get("fundamental_bias", "neutral"),
            upcoming_events=parsed.get("upcoming_events", []),
            raw_response=raw_text,
        )

    except anthropic.APIError as e:
        logger.error("[%s] Claude API error: %s", symbol, e)
        return AnalysisResult(symbol=symbol, digits=profile["digits"],
                              market_summary=f"Claude API error: {e}")
    except Exception as e:
        logger.error("[%s] Unexpected error during analysis: %s", symbol, e, exc_info=True)
        return AnalysisResult(symbol=symbol, digits=profile["digits"],
                              market_summary=f"Analysis error: {e}")


# ---------------------------------------------------------------------------
# Main entry point: two-tier analysis pipeline
# ---------------------------------------------------------------------------
async def analyze_charts(
    screenshot_d1: bytes,
    screenshot_h4: bytes,
    screenshot_h1: bytes,
    screenshot_m5: bytes,
    market_data: MarketData,
) -> AnalysisResult:
    """Two-tier analysis: Sonnet screens → Opus analyzes (if setup found).
    Fundamentals fetched once per day via Sonnet + web search.
    Charts: D1 (daily trend), H4 (tactical/OTE), H1 (intraday structure), M5 (entry timing)."""
    symbol = market_data.symbol
    profile = get_profile(symbol)

    # Step 1: Fetch fundamentals (cached daily, cheap Sonnet + web search)
    fundamentals = await fetch_fundamentals(symbol, profile)

    # Step 2: Sonnet screening (cheap, every scan, no web search)
    screening = await screen_charts(
        screenshot_d1, screenshot_h4, screenshot_h1, screenshot_m5,
        market_data, profile, fundamentals,
    )

    if not screening.get("has_setup", False):
        # No setup — return Sonnet's summary without calling Opus
        logger.info("[%s] Sonnet says no setup — skipping Opus ($saved)", symbol)
        return AnalysisResult(
            symbol=symbol,
            digits=profile["digits"],
            h1_trend_analysis=f"H1 trend: {screening.get('h1_trend', 'unknown')}",
            market_summary=screening.get("market_summary", screening.get("reasoning", "No valid setup identified.")),
            primary_scenario=screening.get("reasoning", ""),
        )

    # Step 3: Opus full analysis (expensive, only when Sonnet found something)
    logger.info("[%s] Sonnet found potential setup — escalating to Opus", symbol)
    return await analyze_charts_full(
        screenshot_d1, screenshot_h4, screenshot_h1, screenshot_m5,
        market_data, profile, fundamentals,
    )


# ---------------------------------------------------------------------------
# Haiku M1 entry confirmation (cheap, called when price reaches zone)
# ---------------------------------------------------------------------------
async def confirm_entry(
    screenshot_m1: bytes,
    symbol: str,
    bias: str,
    current_price: float,
    entry_min: float,
    entry_max: float,
    confluence: list[str] | None = None,
) -> dict:
    """Quick Haiku check: is price showing a reaction at the entry zone?

    Called by the EA when price reaches the entry zone.
    Cost: ~$0.03-0.08 per call (1 small image + minimal text).
    Returns: {"confirmed": bool, "reasoning": "1 sentence"}
    """
    if not ANTHROPIC_API_KEY:
        return {"confirmed": True, "reasoning": "API key missing, auto-confirming"}

    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    profile = get_profile(symbol)
    digits = profile["digits"]

    direction = "bullish" if bias == "long" else "bearish"
    opposite = "bearish" if bias == "long" else "bullish"

    confluence_text = ""
    if confluence:
        confluence_text = f"\nOriginal confluence: {', '.join(confluence[:3])}"

    system_prompt = f"""You are a fast M1 price-action reader for {symbol}. Your ONLY job is to check if there is a {direction} reaction forming on the M1 chart right now.

CRITICAL: Focus ONLY on the LAST 5 candles (the rightmost candles on the chart). Ignore everything else — the higher timeframe analysis has already been done and confirmed this is a valid setup. You are just checking for a basic reaction at the zone.

The setup has ALREADY been validated on D1, H4, H1, and M5 with a high ICT checklist score. Your job is simply to confirm price is not slicing straight through the zone. Even a SMALL reaction is enough.

Say YES (confirmed: true) if you see ANY of these in the last 5 candles:
- Any wick rejection off {'support' if bias == 'long' else 'resistance'} (even a small one)
- A {'bullish' if bias == 'long' else 'bearish'} candle after {'bearish' if bias == 'long' else 'bullish'} ones (reversal attempt)
- Price slowing down or stalling at the zone (small-body candles, dojis)
- Any {'bullish' if bias == 'long' else 'bearish'} engulfing or FVG
- Price simply sitting in or near the entry zone without aggressive {opposite} momentum

Say NO (confirmed: false) ONLY if:
- Price is clearly slicing through the zone with strong {opposite} momentum (large-body {opposite} candles with no wicks)
- The last 5 candles show zero hesitation — pure one-directional {opposite} movement through the zone

When in doubt, say YES. The higher timeframe analysis supports this trade.

Respond with ONLY this JSON:
{{"confirmed": true or false, "reasoning": "1 sentence about the last 5 candles"}}"""

    user_content = [
        {"type": "text", "text": f"--- M1 (1-Minute) Chart ---"},
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": _encode_image(screenshot_m1),
            },
        },
        {
            "type": "text",
            "text": (
                f"Setup: {bias.upper()} {symbol}\n"
                f"Entry zone: {entry_min:.{digits}f} - {entry_max:.{digits}f}\n"
                f"Current price: {current_price:.{digits}f}\n"
                f"Looking for: {direction} reaction at this zone"
                f"{confluence_text}\n\n"
                f"Is there a {direction} reaction on M1? JSON only."
            ),
        },
    ]

    try:
        logger.info("[%s] Haiku M1 confirmation check (%s at %.{0}f)...".replace("{0}", str(digits)),
                     symbol, bias.upper(), current_price)
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )

        raw_text = ""
        for block in response.content:
            if hasattr(block, "text") and block.text is not None:
                raw_text += block.text

        usage = response.usage
        logger.info("[%s] Haiku confirmation: input=%d, output=%d",
                     symbol, usage.input_tokens, usage.output_tokens)

        parsed = _parse_response(raw_text)
        if parsed:
            confirmed = parsed.get("confirmed", False)
            reasoning = parsed.get("reasoning", "")
            logger.info("[%s] Haiku verdict: confirmed=%s — %s", symbol, confirmed, reasoning)
            return {"confirmed": confirmed, "reasoning": reasoning}

        logger.warning("[%s] Haiku confirmation parse failed, defaulting to NO", symbol)
        return {"confirmed": False, "reasoning": "Parse failed — skipping for safety"}

    except Exception as e:
        logger.error("[%s] Haiku confirmation error: %s — defaulting to NO", symbol, e)
        return {"confirmed": False, "reasoning": f"Error: {e}"}
