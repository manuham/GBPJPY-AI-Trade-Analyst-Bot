from __future__ import annotations

import base64
import json
import logging
from datetime import date
from typing import Optional

import anthropic

from config import ANTHROPIC_API_KEY
from models import AnalysisResult, MarketData, TradeSetup
from pair_profiles import get_profile
from trade_tracker import get_recent_closed_for_pair

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Daily fundamentals cache  (one web-search per pair per day)
# ---------------------------------------------------------------------------
_fundamentals_cache: dict[str, dict] = {}  # key = "GBPJPY:2026-02-09"


def _cache_key(symbol: str) -> str:
    return f"{symbol}:{date.today().isoformat()}"


def get_cached_fundamentals(symbol: str) -> Optional[str]:
    """Return cached fundamentals text for today, or None."""
    entry = _fundamentals_cache.get(_cache_key(symbol))
    return entry["text"] if entry else None


def store_fundamentals(symbol: str, text: str):
    """Cache fundamentals text for today."""
    _fundamentals_cache[_cache_key(symbol)] = {"text": text, "date": date.today().isoformat()}
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

    return f"""You are a senior institutional FX analyst specializing in {profile['specialization']} using ICT (Inner Circle Trader) methodology. You are analyzing live {symbol} charts from MetaTrader 5.

## CONTEXT
- Pair: {symbol}
- Active sessions: {profile['key_sessions']}
- Risk per trade: 1%
- TP strategy: 50% closed at TP1, runner to TP2
- Charts provided: **D1 (Daily)**, **H1 (Hourly)**, **M5 (5-Minute)** — top-down
- The charts include horizontal swing-level lines drawn by a custom indicator
- Market data JSON includes: previous day H/L/C, weekly H/L, Asian session range, RSI(14), ATR(14)

## YOUR TASK

{fundamentals_section}

### Step 1 — MANDATORY: Daily Trend & Structure (D1 chart)
Before ANYTHING, analyze the D1 (Daily) chart:
- Identify the last 10-20 daily candles for swing structure
- Are they making higher highs + higher lows (BULLISH) or lower highs + lower lows (BEARISH)?
- Note the **previous day high/low/close** from the market data — these are KEY institutional reference levels
- Note the **current week high/low** — price often targets these for liquidity sweeps
- Check D1 RSI: >70 = overbought (caution for longs), <30 = oversold (caution for shorts), 40-60 = neutral
- The D1 trend provides context. Prefer trend-aligned trades, but H1/M5 structure can override if reversal signals are clear.

### Step 2 — H1 Structure & Premium/Discount Zone
- Identify H1 swing structure within the context of D1 trend (alignment or divergence?)
- Define the current H1 range (recent swing high to recent swing low)
- Calculate the equilibrium (50% level)
- Determine if price is currently in:
  - DISCOUNT zone (below 50%) — favorable for longs
  - PREMIUM zone (above 50%) — favorable for shorts
  - EQUILIBRIUM (near 50%) — can still trade if there's a directional trigger (BOS, FVG, session level)
- Check H1 RSI for confirmation
- Premium/discount zone is a preference, not a hard rule — a strong OB or FVG at any level is tradeable

### Step 3 — Session Levels & Liquidity
Use the provided market data to identify key levels:
- **Previous Day High (PDH)** and **Previous Day Low (PDL)** — institutional liquidity magnets
- **Previous Day Close (PDC)** — acts as support/resistance pivot
- **Asian Session Range** (asian_high/asian_low) — London often sweeps one side of the Asian range before reversing
- **Weekly High/Low** — key swing liquidity targets
- Mark which of these levels price is currently near or has recently swept

### Step 4 — Multi-Timeframe Alignment (D1 → H1 → M5)
- Market structure per timeframe: BOS, ChoCH locations with exact prices
- Is M5 structure aligned with H1 and D1, or showing early reversal signs?
- Key swing highs/lows with exact price levels

### Step 5 — Key ICT Levels (be precise with prices)
- Order blocks / supply & demand zones — specify if tested or untested
- Fair Value Gaps (FVGs) — specify if filled or open
- Institutional liquidity pools (equal highs/lows, stop clusters)
- Breaker blocks, mitigation blocks where applicable
- Distinguish between: where price BOUNCED FROM vs where price IS NOW (these are different!)

### Step 6 — Setup Generation
Propose setups that satisfy these criteria:
1. D1 trend-aligned preferred. Counter-trend is allowed if you see a BOS/ChoCH reversal signal on H1 or M5
2. Entry near a key level (order block, FVG, session level, or swing level)
3. Minimum 1:1.5 R:R on TP1
4. At least 2 confluence factors
5. Clear invalidation level

For counter-trend setups:
- Label them "counter_trend: true"
- Show a ChoCH or BOS on H1 or M5 confirming the reversal
- Rate confidence as "medium" at most

Setups from equilibrium zone are acceptable if there is a clear directional trigger (BOS, FVG fill, session level sweep).

IMPORTANT: Your goal is to find tradeable setups. Most sessions have at least one valid entry — look harder before saying "no trade". Even a cautious low-confidence setup with a clear SL is better than no setup.

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
      "confidence": "high" or "medium" or "low",
      "news_warning": "description or null",
      "counter_trend": true or false,
      "h1_trend": "bullish" or "bearish" or "ranging",
      "price_zone": "premium" or "discount" or "equilibrium"
    }}
  ],
  "h1_trend_analysis": "2-3 sentences describing D1+H1 swing structure and dominant trend",
  "market_summary": "2-3 sentence summary including key session levels",
  "primary_scenario": "description",
  "alternative_scenario": "description",
  "fundamental_bias": {profile['fundamental_bias_options']},
  "upcoming_events": ["event1", "event2"]
}}

## RULES
- Analyze D1 trend FIRST for context, then look for setups on H1/M5
- Prefer trend-aligned trades, but counter-trend is allowed with reversal confirmation
- Use session levels (PDH/PDL/PDC, Asian range, weekly H/L) as confluence and targets
- Consider {symbol} spread (~{profile['typical_spread']}) in SL/TP calculations
- Use RSI as confirmation, not as a standalone signal
- Flag any setups near high-impact news events
- IMPORTANT: Actively look for setups. Most London and NY sessions offer at least one tradeable opportunity on {symbol}. A low-confidence setup with clear risk management is still useful — the trader decides whether to execute.
- Always respond with valid JSON, nothing else"""


def _build_screening_prompt(symbol: str, profile: dict, fundamentals: Optional[str] = None) -> str:
    """Lightweight screening prompt for Sonnet — quick yes/no on trade viability.
    Only receives H1+M5 charts (D1 info comes from market data numbers)."""
    fund_section = ""
    if fundamentals:
        fund_section = f"\n\nFundamental context (gathered earlier today):\n{fundamentals}"

    return f"""You are a quick-scan FX analyst. Look at these {symbol} charts (H1, M5) and the market data to determine if there is ANY potential ICT trade setup worth analyzing further.{fund_section}

The market data JSON includes D1 RSI + ATR + previous day levels, so you can assess D1 bias without the D1 chart.

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
    """Build performance feedback text from recent closed trades for this pair."""
    try:
        trades = get_recent_closed_for_pair(symbol, limit=10)
    except Exception as e:
        logger.warning("Failed to get performance history for %s: %s", symbol, e)
        return None

    if not trades:
        return None

    lines = [f"Your last {len(trades)} completed trades for {symbol}:"]
    wins = 0
    losses = 0
    total_pnl = 0.0

    for i, t in enumerate(trades, 1):
        outcome = t.get("outcome", "?")
        pnl = t.get("pnl_pips") or 0
        bias = (t.get("bias") or "?").upper()
        conf = t.get("confidence") or "?"
        trend = t.get("h1_trend") or "?"
        ct = " [COUNTER-TREND]" if t.get("counter_trend") else ""
        date_str = (t.get("closed_at") or "")[:10]

        emoji = {"full_win": "W", "partial_win": "PW", "loss": "L"}.get(outcome, outcome)
        lines.append(f"  {i}. {emoji} {bias} ({conf}) {pnl:+.0f}p — {trend}{ct} — {date_str}")

        if outcome in ("full_win", "partial_win"):
            wins += 1
        elif outcome == "loss":
            losses += 1
        total_pnl += pnl

    total = wins + losses
    wr = (wins / total * 100) if total > 0 else 0
    lines.append(f"\nWin rate: {wr:.0f}% ({wins}W / {losses}L) | Net: {total_pnl:+.0f} pips")
    lines.append("Learn from these results. Avoid patterns that keep losing. Double down on what works.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# User content builders
# ---------------------------------------------------------------------------
def _build_image_content(
    screenshot_d1: bytes,
    screenshot_h1: bytes,
    screenshot_m5: bytes,
    market_data: MarketData,
) -> list[dict]:
    """Build the full multi-modal user message content for Opus (all 3 charts + OHLC)."""
    content: list[dict] = []

    for label, img_bytes in [
        ("D1 (Daily)", screenshot_d1),
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

    market_dict = market_data.model_dump()
    ohlc_summary = {
        "d1_bars": len(market_dict.get("ohlc_d1", [])),
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
                "--- Market Data (includes D1 RSI/ATR, session levels) ---\n"
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
    screenshot_h1: bytes,
    screenshot_m5: bytes,
    market_data: MarketData,
    profile: dict,
    fundamentals: Optional[str] = None,
) -> dict:
    """Quick Sonnet screen — is there a setup worth analyzing in detail?
    Cost-optimized: only H1+M5 images, no OHLC data, prompt caching.
    Returns dict with has_setup, h1_trend, reasoning, market_summary."""
    if not ANTHROPIC_API_KEY:
        return {"has_setup": True, "reasoning": "API key missing, skipping screen"}

    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    symbol = market_data.symbol

    # Lightweight content: only H1+M5, no OHLC (saves ~40% vs full content)
    user_content = _build_screening_content(screenshot_h1, screenshot_m5, market_data)
    user_content.append({"type": "text", "text": "Screen these H1/M5 charts plus the market data (D1 bias from RSI/ATR/PDH/PDL). Is there a valid ICT setup? Reply with JSON only."})

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
    screenshot_h1: bytes,
    screenshot_m5: bytes,
    market_data: MarketData,
    profile: dict,
    fundamentals: Optional[str] = None,
) -> AnalysisResult:
    """Full Opus analysis with detailed ICT methodology."""
    if not ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY not configured")
        return AnalysisResult(market_summary="Error: API key not configured")

    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    symbol = market_data.symbol

    user_content = _build_image_content(screenshot_d1, screenshot_h1, screenshot_m5, market_data)

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
            "text": "Analyze the D1/H1/M5 charts and market data above (including session levels, RSI, ATR). First use web_search to check fundamentals and news, then provide your full ICT analysis as JSON.",
        })
    else:
        user_content.append({
            "type": "text",
            "text": "Analyze the D1/H1/M5 charts and market data above (including session levels, RSI, ATR) using the pre-loaded fundamentals. Provide your full ICT analysis as JSON.",
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
    screenshot_h1: bytes,
    screenshot_m5: bytes,
    market_data: MarketData,
) -> AnalysisResult:
    """Two-tier analysis: Sonnet screens → Opus analyzes (if setup found).
    Fundamentals fetched once per day via Sonnet + web search.
    Charts: D1 (daily trend), H1 (intraday structure), M5 (entry timing)."""
    symbol = market_data.symbol
    profile = get_profile(symbol)

    # Step 1: Fetch fundamentals (cached daily, cheap Sonnet + web search)
    fundamentals = await fetch_fundamentals(symbol, profile)

    # Step 2: Sonnet screening (cheap, every scan, no web search)
    screening = await screen_charts(
        screenshot_d1, screenshot_h1, screenshot_m5,
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
        screenshot_d1, screenshot_h1, screenshot_m5,
        market_data, profile, fundamentals,
    )
