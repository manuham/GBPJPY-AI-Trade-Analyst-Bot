# v3.0 — Smart entry confirmation + London Kill Zone
from __future__ import annotations

import base64
import io
import json
import logging
import os
import sqlite3
from datetime import date
from typing import Optional

import anthropic
from PIL import Image

from config import ANTHROPIC_API_KEY
from market_context import build_market_context
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


def _compress_image(png_bytes: bytes, quality: int = 85) -> tuple[bytes, str]:
    """Convert PNG screenshot to compressed JPEG. Returns (bytes, media_type).

    JPEG at quality 85 is ~60% smaller than PNG with negligible visual loss
    for chart reading. Falls back to original PNG on error.
    """
    try:
        img = Image.open(io.BytesIO(png_bytes))
        if img.mode == "RGBA":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        compressed = buf.getvalue()
        logger.debug("Image compressed: %d → %d bytes (%.0f%% reduction)",
                      len(png_bytes), len(compressed),
                      (1 - len(compressed) / len(png_bytes)) * 100 if png_bytes else 0)
        return compressed, "image/jpeg"
    except Exception as e:
        logger.warning("Image compression failed, using original PNG: %s", e)
        return png_bytes, "image/png"


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

    return f"""You are a senior institutional FX analyst specializing in {symbol} during the {session_name} using ICT methodology, analyzing live MT5 charts.

## CONTEXT
- Pair: {symbol} | Session: {session_name} | Risk: 1% per trade | TP: 50% at TP1, runner to TP2
- Charts: D1, H4, H1, M5 (top-down) with swing-level indicator lines
- Market data JSON: PDH/PDL/PDC, weekly H/L, Asian range, RSI(14), ATR(14) per timeframe
- Setups are NOT executed immediately — the EA watches the entry zone and confirms on M1 before entering. Propose setups even if price is not yet at the zone.

{fundamentals_section}

## ANALYSIS (strict top-down)

### 1. D1 + H4 Strategic Bias
**D1**: Identify swing structure (last 10-20 candles: HH/HL = bullish, LH/LL = bearish). Note PDH/PDL/PDC and weekly H/L as institutional reference levels. Check D1 RSI (>70 overbought, <30 oversold).
**H4**: Does H4 align with D1? Calculate Premium/Discount zone (above/below 50% of H4 range). Find OTE zone (62-79% Fib of last H4 impulse, optimal at 70.5%). Identify H4 Order Blocks (valid ≤8 candles). Set d1_trend and h4_trend.

### 2. H1 Structure + Session Levels
- H1 swing structure, BOS/ChoCH with exact prices, OBs (valid ≤30 candles), RSI confirmation
- Session levels from market data: PDH/PDL (liquidity magnets), PDC (pivot), Asian range (London sweep target), weekly H/L
{session_context}

### 3. M5 Entry Triggers + Multi-TF Alignment
- MSS on M5: displacement candle body ≥15 pips breaking swing H/L
- FVGs: M5 ≥15 pips, H1 ≥25 pips. Calculate CE (50% midpoint) as optimal entry
- Liquidity sweeps: price exceeds key level ≥5 pips then reverses
- OB validity: H4 ≤8 candles, H1 ≤30 candles. Untested = strongest, tested 3x+ = weakened
- Note D1/H4/H1/M5 alignment vs divergences
- Distinguish: where price BOUNCED FROM vs where price IS NOW

### 4. Setup Generation
**Criteria**: D1+H4 aligned preferred | Entry at CE of FVG within OB zone | Ideally within OTE (62-79%) | SL: 5-10 pips beyond OB extreme, **hard cap 70 pips** | Min R:R 1:1.2 (TP1), 1:2 (TP2) | ≥2 confluence factors | Clear invalidation

**Counter-trend**: Allowed only with BOS/ChoCH reversal on H1/M5. Mark counter_trend: true, max confidence "medium".

**For each setup, also provide:**
- **trend_alignment**: "X/4 direction" (how many of D1/H4/H1/M5 agree, e.g. "3/4 bearish (M5 diverging)")
- **entry_distance_pips** + **entry_status**: "at_zone" (<10p), "approaching" (10-40p), "requires_pullback" (>40p)
- **negative_factors**: 1-3 honest risks (e.g. "D1 opposes", "RSI overbought", "OB stale", "SL near cap")
- **checklist_score**: Score against 12-point ICT checklist:
  1. D1 bias identified | 2. H4 aligns with D1 | 3. Correct Premium/Discount zone | 4. Active OB (within validity) | 5. MSS on M5 (≥15p displacement) | 6. FVG meets min size | 7. Entry at CE level | 8. Within OTE zone | 9. Liquidity sweep detected | 10. SL ≤70 pips | 11. R:R ≥1:2 on TP2 | 12. No news conflict within 30 min
  Scoring: HIGH=10-12, MEDIUM-HIGH=8-9, MEDIUM=6-7, LOW=4-5. Below 4 = don't propose.

IMPORTANT: Quality over quantity. Only propose setups with genuine ICT confluence. An empty setups array on a flat day is better than forcing a weak setup. But the {session_name} usually offers at least one opportunity — don't give up too easily.

### 5. No Trade
Return empty setups ONLY if: spread widened (off-session/holiday), high-impact news within 30 min, or genuinely no tradeable structure.

## OUTPUT — JSON ONLY
{{
  "setups": [{{
    "bias": "long"/"short", "entry_min": price, "entry_max": price,
    "stop_loss": price, "sl_pips": N, "tp1": price, "tp1_pips": N,
    "tp2": price, "tp2_pips": N, "rr_tp1": N, "rr_tp2": N,
    "confluence": ["..."], "invalidation": "...",
    "timeframe_type": "scalp"/"intraday"/"swing",
    "confidence": "high"/"medium_high"/"medium"/"low",
    "news_warning": "..." or null, "counter_trend": bool,
    "h1_trend": "bullish"/"bearish"/"ranging",
    "h4_trend": "bullish"/"bearish"/"ranging",
    "d1_trend": "bullish"/"bearish"/"ranging",
    "trend_alignment": "X/4 ...", "price_zone": "premium"/"discount"/"equilibrium",
    "entry_distance_pips": N, "entry_status": "at_zone"/"approaching"/"requires_pullback",
    "negative_factors": ["..."], "checklist_score": "X/12"
  }}],
  "h1_trend_analysis": "2-3 sentences on D1+H4+H1 structure",
  "market_summary": "2-3 sentences with key session levels",
  "primary_scenario": "...", "alternative_scenario": "...",
  "fundamental_bias": {profile['fundamental_bias_options']},
  "upcoming_events": ["..."]
}}

Consider {symbol} spread (~{profile['typical_spread']}). {session_rules}
Prefer "at_zone"/"approaching" entries. Use RSI as confirmation only. Respond with valid JSON only."""


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

    # --- Section 3: Post-trade review insights ---
    try:
        from trade_tracker import get_recent_reviews
        reviews = get_recent_reviews(symbol, limit=5)
        if reviews:
            lines.append("\n## Recent Post-Trade Insights")
            for r in reviews:
                lines.append(f"  - {r['review_text']}")
    except Exception:
        pass

    # --- Section 4: Actionable instructions ---
    lines.append("\n## Instructions")
    lines.append("Use the patterns and insights above to improve your current analysis:")
    lines.append("- If a pattern consistently loses, AVOID it or rate confidence LOW")
    lines.append("- If a pattern consistently wins, actively LOOK FOR similar setups")
    lines.append("- If 'requires_pullback' entries lose often, prefer 'at_zone' entries")
    lines.append("- If counter-trend trades lose, only propose them with very strong reversal evidence")

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
    """Build the full multi-modal user message content for Opus (all 4 charts)."""
    content: list[dict] = []

    for label, img_bytes in [
        ("D1 (Daily)", screenshot_d1),
        ("H4 (4-Hour)", screenshot_h4),
        ("H1 (Hourly)", screenshot_h1),
        ("M5 (5-Minute)", screenshot_m5),
    ]:
        if not img_bytes:
            continue  # Skip if screenshot not available (backward compat)
        compressed, media_type = _compress_image(img_bytes)
        content.append({"type": "text", "text": f"--- {label} Chart ---"})
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": _encode_image(compressed),
                },
            }
        )

    # Strip OHLC arrays — screenshots already contain this data visually.
    # Keeping only numeric indicators (RSI, ATR, session levels) saves ~4,000 tokens.
    market_dict = market_data.model_dump()
    display_data = {k: v for k, v in market_dict.items() if not k.startswith("ohlc_")}

    content.append(
        {
            "type": "text",
            "text": (
                "--- Market Data (session levels, RSI, ATR) ---\n"
                + json.dumps(display_data, indent=2)
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
        compressed, media_type = _compress_image(img_bytes)
        content.append({"type": "text", "text": f"--- {label} Chart ---"})
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": _encode_image(compressed),
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
            # Log screening result for analytics (Step 7)
            try:
                from trade_tracker import log_screening_result
                log_screening_result(symbol, has_setup, parsed.get("reasoning", ""))
            except Exception as log_err:
                logger.warning("Failed to log screening result: %s", log_err)
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

    # Inject market context (COT, sentiment, rates, intermarket — all free APIs)
    try:
        market_ctx = await build_market_context(symbol, profile)
        if market_ctx:
            user_content.append({
                "type": "text",
                "text": f"--- {market_ctx}",
            })
            logger.info("[%s] Market context injected (%d chars)", symbol, len(market_ctx))
    except Exception as e:
        logger.warning("[%s] Market context fetch failed (non-fatal): %s", symbol, e)

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
    # 6K budget is sufficient — typical analysis uses 4-5K thinking tokens
    thinking_config = {"type": "enabled", "budget_tokens": 6000}

    from config import ANALYSIS_MODEL
    analysis_model = ANALYSIS_MODEL

    try:
        logger.info("[%s] Full analysis (model=%s, web_search=%s, thinking=6k)...",
                     symbol, analysis_model, use_web_search)
        # Streaming required for extended thinking with large max_tokens
        async with client.messages.stream(
            model=analysis_model,
            max_tokens=8000,
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

    # Quick sentiment check for M1 context (cached, no extra API call)
    sentiment_text = ""
    try:
        from market_context import fetch_retail_sentiment
        sentiment = await fetch_retail_sentiment(symbol)
        if sentiment:
            contrarian = sentiment.get("contrarian_signal", "neutral")
            if contrarian != "neutral":
                sentiment_text = f"\nRetail sentiment: {sentiment['pct_long']:.0f}% long / {sentiment['pct_short']:.0f}% short (contrarian {contrarian})"
    except Exception:
        pass

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

    m1_compressed, m1_media = _compress_image(screenshot_m1)
    user_content = [
        {"type": "text", "text": f"--- M1 (1-Minute) Chart ---"},
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": m1_media,
                "data": _encode_image(m1_compressed),
            },
        },
        {
            "type": "text",
            "text": (
                f"Setup: {bias.upper()} {symbol}\n"
                f"Entry zone: {entry_min:.{digits}f} - {entry_max:.{digits}f}\n"
                f"Current price: {current_price:.{digits}f}\n"
                f"Looking for: {direction} reaction at this zone"
                f"{confluence_text}{sentiment_text}\n\n"
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


# ---------------------------------------------------------------------------
# Post-trade Haiku review — learning loop after each closed trade
# ---------------------------------------------------------------------------
async def post_trade_review(trade: dict, symbol: str) -> str:
    """Quick Haiku review after a trade closes. Returns 2-3 sentence insight.

    Cost: ~$0.01-0.02 per review (text only, no images).
    These insights are stored and fed into future Opus prompts via performance feedback.
    """
    if not ANTHROPIC_API_KEY:
        return ""

    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    outcome = trade.get("outcome", "unknown")
    bias = trade.get("bias", "unknown")
    confidence = trade.get("confidence", "unknown")
    checklist = trade.get("checklist_score", "?/12")
    pnl_pips = trade.get("pnl_pips", 0)
    sl_pips = trade.get("sl_pips", 0)
    tp1_pips = trade.get("tp1_pips", 0)
    tp2_pips = trade.get("tp2_pips", 0)
    trend_align = trade.get("trend_alignment", "")
    entry_status = trade.get("entry_status", "")
    neg_factors = trade.get("negative_factors", "")
    price_zone = trade.get("price_zone", "")

    prompt = f"""You are reviewing a closed {symbol} trade for pattern learning. Be concise (2-3 sentences max).

Trade details:
- Bias: {bias} | Outcome: {outcome} | P&L: {pnl_pips:+.1f} pips
- Confidence: {confidence} | Checklist: {checklist}
- Trend alignment: {trend_align} | Price zone: {price_zone}
- Entry status at signal: {entry_status}
- Negative factors flagged: {neg_factors}
- SL: {sl_pips:.0f} pips | TP1: {tp1_pips:.0f} pips | TP2: {tp2_pips:.0f} pips

What's the key takeaway? Focus on what the system should learn for future {symbol} trades (e.g., "counter-trend setups with <8/12 checklist tend to lose", "at_zone entries outperform requires_pullback"). Be specific and actionable."""

    try:
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )

        review_text = ""
        for block in response.content:
            if hasattr(block, "text") and block.text:
                review_text += block.text

        logger.info("[%s] Post-trade review for %s: %s", symbol, trade.get("id", "?"), review_text[:100])
        return review_text.strip()

    except Exception as e:
        logger.error("[%s] Post-trade review error: %s", symbol, e)
        return ""
