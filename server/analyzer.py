from __future__ import annotations

import base64
import json
import logging
from typing import Optional

import anthropic

from config import ANTHROPIC_API_KEY
from models import AnalysisResult, MarketData, TradeSetup

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a senior institutional FX analyst specializing in JPY crosses. You are analyzing live GBPJPY charts sent from MetaTrader 5.

## CONTEXT
- Pair: GBPJPY
- Active sessions: London & Tokyo overlap is key for this pair
- Risk per trade: 1%, targeting minimum 1:2 R:R
- The trader uses ICT methodology: BOS, ChoCH, order blocks, FVGs, liquidity sweeps
- TP strategy: 50% closed at TP1, runner to TP2

## YOUR TASK
1. First, use web search to check current GBP and JPY fundamental drivers, any breaking news, and the economic calendar for the next 24 hours. Search for "GBPJPY forecast today", "GBP news today", "JPY news today", and "forex economic calendar today GBP JPY".

2. Then analyze the three provided charts (H1, M15, M5) using this framework:

### Market Structure (Priority: High)
- Current trend direction per timeframe (H1 → M15 → M5)
- Key swing highs/lows with exact price levels
- Break of structure (BOS) or change of character (ChoCH) locations

### Key Levels (Be precise with prices)
- Institutional liquidity zones (equal highs/lows, stop hunts)
- Order blocks / supply & demand zones
- Fair Value Gaps (FVGs)
- Untested POIs (points of interest)

### Fundamental Snapshot
- Current GBP and JPY sentiment drivers (max 3 bullets each)
- Upcoming high-impact news within 24h
- Overall fundamental bias

### Trade Setups
For EACH valid setup, provide EXACTLY this JSON structure:
{
  "setups": [
    {
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
      "news_warning": "description or null"
    }
  ],
  "market_summary": "2-3 sentence summary",
  "primary_scenario": "description",
  "alternative_scenario": "description",
  "fundamental_bias": "bullish_gbp" or "bearish_gbp" or "neutral",
  "upcoming_events": ["event1", "event2"]
}

## RULES
- No setup is better than a bad setup — return empty setups array if no clear edge
- Prioritize setups with 3+ confluence factors
- Consider GBPJPY spread (~2-3 pips) in SL/TP calculations
- Flag any setups near high-impact news events
- Always respond with valid JSON matching the structure above, nothing else"""


def _encode_image(image_bytes: bytes) -> str:
    return base64.standard_b64encode(image_bytes).decode("utf-8")


def build_user_content(
    screenshot_h1: bytes,
    screenshot_m15: bytes,
    screenshot_m5: bytes,
    market_data: MarketData,
) -> list[dict]:
    """Build the multi-modal user message content for Claude."""
    content: list[dict] = []

    # Add chart images
    for label, img_bytes in [
        ("H1", screenshot_h1),
        ("M15", screenshot_m15),
        ("M5", screenshot_m5),
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

    # Add market data
    market_dict = market_data.model_dump()
    # Remove large OHLC arrays from display text to keep prompt concise
    ohlc_summary = {
        "h1_bars": len(market_dict.get("ohlc_h1", [])),
        "m15_bars": len(market_dict.get("ohlc_m15", [])),
        "m5_bars": len(market_dict.get("ohlc_m5", [])),
    }
    display_data = {k: v for k, v in market_dict.items() if not k.startswith("ohlc_")}
    display_data["ohlc_bar_counts"] = ohlc_summary

    content.append(
        {
            "type": "text",
            "text": (
                "--- Market Data ---\n"
                + json.dumps(display_data, indent=2)
                + "\n\n--- Full OHLC Data ---\n"
                + json.dumps(
                    {
                        "ohlc_h1": market_dict.get("ohlc_h1", []),
                        "ohlc_m15": market_dict.get("ohlc_m15", []),
                        "ohlc_m5": market_dict.get("ohlc_m5", []),
                    }
                )
            ),
        }
    )

    content.append(
        {
            "type": "text",
            "text": (
                "Analyze the charts and market data above. "
                "First use web_search to check fundamentals and news, "
                "then provide your analysis as JSON."
            ),
        }
    )

    return content


def _parse_response(raw_text: str) -> Optional[dict]:
    """Extract JSON from Claude's response, handling markdown code blocks."""
    text = raw_text.strip()

    # Try to extract from code block
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

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in text
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    return None


async def analyze_charts(
    screenshot_h1: bytes,
    screenshot_m15: bytes,
    screenshot_m5: bytes,
    market_data: MarketData,
) -> AnalysisResult:
    """Send charts and market data to Claude for analysis."""
    if not ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY not configured")
        return AnalysisResult(market_summary="Error: API key not configured")

    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    user_content = build_user_content(
        screenshot_h1, screenshot_m15, screenshot_m5, market_data
    )

    try:
        logger.info("Sending analysis request to Claude API...")
        response = await client.messages.create(
            model="claude-opus-4-20250514",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=[
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 10,
                }
            ],
            messages=[{"role": "user", "content": user_content}],
        )

        # Extract text from response content blocks
        raw_text = ""
        for block in response.content:
            if hasattr(block, "text") and block.text is not None:
                raw_text += block.text

        logger.info("Received response from Claude (%d chars)", len(raw_text))

        parsed = _parse_response(raw_text)
        if parsed is None:
            logger.warning("Failed to parse JSON from Claude response")
            return AnalysisResult(
                market_summary="Analysis received but JSON parsing failed.",
                raw_response=raw_text,
            )

        # Build setups
        setups = []
        for s in parsed.get("setups", []):
            try:
                setups.append(TradeSetup(**s))
            except Exception as e:
                logger.warning("Failed to parse setup: %s", e)

        return AnalysisResult(
            setups=setups,
            market_summary=parsed.get("market_summary", ""),
            primary_scenario=parsed.get("primary_scenario", ""),
            alternative_scenario=parsed.get("alternative_scenario", ""),
            fundamental_bias=parsed.get("fundamental_bias", "neutral"),
            upcoming_events=parsed.get("upcoming_events", []),
            raw_response=raw_text,
        )

    except anthropic.APIError as e:
        logger.error("Claude API error: %s", e)
        return AnalysisResult(market_summary=f"Claude API error: {e}")
    except Exception as e:
        logger.error("Unexpected error during analysis: %s", e, exc_info=True)
        return AnalysisResult(market_summary=f"Analysis error: {e}")
