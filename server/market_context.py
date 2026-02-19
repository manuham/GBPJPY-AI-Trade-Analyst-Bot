"""Market context module — external data feeds for enhanced AI analysis.

Fetches and caches macro/sentiment data that screenshots alone can't show:
1. COT (Commitment of Traders) — institutional positioning from CFTC
2. Myfxbook Sentiment — retail positioning (contrarian indicator)
3. Interest Rate Differential — BoE vs BoJ carry trade attractiveness
4. Intermarket Data — Nikkei 225, DXY, US 10Y yield for correlation context

All data is cached to avoid redundant API calls. Total token cost: ~200-300
tokens added to the Opus prompt per analysis. All APIs are FREE.
"""

from __future__ import annotations

import io
import csv
import json
import logging
import os
import sqlite3
import zipfile
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache database (same /data dir as trade_tracker)
# ---------------------------------------------------------------------------
_CACHE_DB_DIR = os.getenv("DATA_DIR", "/data")
_CACHE_DB_PATH = os.path.join(_CACHE_DB_DIR, "market_context_cache.db")


def _init_cache():
    """Create the market context cache table."""
    os.makedirs(_CACHE_DB_DIR, exist_ok=True)
    conn = sqlite3.connect(_CACHE_DB_PATH, timeout=5)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS context_cache (
            cache_key TEXT PRIMARY KEY,
            data_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def _get_cache(key: str, max_age_hours: float = 24) -> Optional[dict]:
    """Get cached data if fresh enough."""
    try:
        conn = sqlite3.connect(_CACHE_DB_PATH, timeout=5)
        row = conn.execute(
            "SELECT data_json, fetched_at FROM context_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
        conn.close()

        if not row:
            return None

        fetched = datetime.fromisoformat(row[1])
        age = datetime.now(timezone.utc) - fetched
        if age.total_seconds() > max_age_hours * 3600:
            return None

        return json.loads(row[0])
    except Exception as e:
        logger.debug("Cache read error for %s: %s", key, e)
        return None


def _set_cache(key: str, data: dict):
    """Store data in cache."""
    try:
        conn = sqlite3.connect(_CACHE_DB_PATH, timeout=5)
        conn.execute(
            "INSERT OR REPLACE INTO context_cache (cache_key, data_json, fetched_at) VALUES (?, ?, ?)",
            (key, json.dumps(data), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug("Cache write error for %s: %s", key, e)


# Initialize cache on module import
try:
    _init_cache()
except Exception:
    pass


# ---------------------------------------------------------------------------
# 1. COT Data — CFTC Commitment of Traders
# ---------------------------------------------------------------------------
# CFTC publishes weekly on Fridays. We fetch the latest report.
# GBP futures = "BRITISH POUND" or commodity code 096742
# JPY futures = "JAPANESE YEN" or commodity code 097741
COT_URL = "https://www.cftc.gov/dea/newcot/deafut.txt"

# Simpler approach: use the CFTC JSON API
COT_REPORT_URL = "https://publicreporting.cftc.gov/resource/jun7-fc8e.json"


async def fetch_cot_data(base_currency: str = "GBP", quote_currency: str = "JPY") -> Optional[dict]:
    """Fetch latest COT positioning for base and quote currency futures.

    Returns net speculator positioning and changes.
    Cached for 24 hours (reports update weekly on Fridays).
    """
    cache_key = f"cot_{base_currency}_{quote_currency}_{date.today().isoformat()}"
    cached = _get_cache(cache_key, max_age_hours=24)
    if cached:
        return cached

    # Map currencies to CFTC contract names
    currency_map = {
        "GBP": "BRITISH POUND STERLING",
        "JPY": "JAPANESE YEN",
        "EUR": "EURO FX",
        "USD": "U.S. DOLLAR INDEX",
        "AUD": "AUSTRALIAN DOLLAR",
        "CAD": "CANADIAN DOLLAR",
        "CHF": "SWISS FRANC",
        "NZD": "NEW ZEALAND DOLLAR",
        "XAU": "GOLD",
    }

    result = {}

    for label, currency in [("base", base_currency), ("quote", quote_currency)]:
        contract_name = currency_map.get(currency)
        if not contract_name:
            continue

        try:
            # CFTC Socrata API — get latest report for this contract
            params = {
                "$where": f"contract_market_name like '%{contract_name}%'",
                "$order": "report_date_as_yyyy_mm_dd DESC",
                "$limit": 2,  # Get 2 weeks for change calculation
            }

            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(COT_REPORT_URL, params=params)
                resp.raise_for_status()
                data = resp.json()

            if not data:
                logger.warning("No COT data found for %s (%s)", currency, contract_name)
                continue

            latest = data[0]
            # Non-commercial (speculator) positions
            spec_long = int(latest.get("noncomm_positions_long_all", 0))
            spec_short = int(latest.get("noncomm_positions_short_all", 0))
            net_position = spec_long - spec_short
            report_date = latest.get("report_date_as_yyyy_mm_dd", "")

            entry = {
                "currency": currency,
                "net_speculator": net_position,
                "spec_long": spec_long,
                "spec_short": spec_short,
                "report_date": report_date,
            }

            # Calculate week-over-week change if we have 2 reports
            if len(data) >= 2:
                prev = data[1]
                prev_long = int(prev.get("noncomm_positions_long_all", 0))
                prev_short = int(prev.get("noncomm_positions_short_all", 0))
                prev_net = prev_long - prev_short
                entry["net_change"] = net_position - prev_net
                entry["positioning_shift"] = (
                    "increasing_long" if entry["net_change"] > 0
                    else "increasing_short" if entry["net_change"] < 0
                    else "unchanged"
                )

            result[label] = entry
            logger.info("COT %s: net=%+d (change=%+d)", currency, net_position,
                        entry.get("net_change", 0))

        except Exception as e:
            logger.warning("Failed to fetch COT data for %s: %s", currency, e)
            continue

    if result:
        _set_cache(cache_key, result)

    return result if result else None


# ---------------------------------------------------------------------------
# 2. Myfxbook Community Sentiment
# ---------------------------------------------------------------------------
MYFXBOOK_OUTLOOK_URL = "https://www.myfxbook.com/community/outlook"


async def fetch_retail_sentiment(symbol: str = "GBPJPY") -> Optional[dict]:
    """Fetch retail sentiment from Myfxbook community outlook.

    Returns % long vs % short for the pair.
    Cached for 4 hours (sentiment updates frequently but doesn't change drastically).
    """
    cache_key = f"sentiment_{symbol}_{date.today().isoformat()}"
    cached = _get_cache(cache_key, max_age_hours=4)
    if cached:
        return cached

    try:
        # Myfxbook API endpoint for community outlook
        url = f"https://www.myfxbook.com/api/get-community-outlook.json"

        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()

        if not data.get("symbols"):
            logger.warning("No sentiment data in Myfxbook response")
            return None

        # Find our symbol in the list
        for item in data["symbols"]:
            name = item.get("name", "").upper().replace("/", "")
            if name == symbol or name == symbol.replace("/", ""):
                pct_long = float(item.get("longPercentage", 50))
                pct_short = float(item.get("shortPercentage", 50))
                vol_long = int(item.get("longVolume", 0))
                vol_short = int(item.get("shortVolume", 0))

                result = {
                    "symbol": symbol,
                    "pct_long": round(pct_long, 1),
                    "pct_short": round(pct_short, 1),
                    "vol_long": vol_long,
                    "vol_short": vol_short,
                    "crowd_bias": "long" if pct_long > 55 else ("short" if pct_short > 55 else "neutral"),
                    "contrarian_signal": (
                        "bullish" if pct_short >= 65 else
                        "bearish" if pct_long >= 65 else
                        "neutral"
                    ),
                }
                _set_cache(cache_key, result)
                logger.info("Sentiment %s: %.1f%% long / %.1f%% short (contrarian: %s)",
                            symbol, pct_long, pct_short, result["contrarian_signal"])
                return result

        logger.info("Symbol %s not found in Myfxbook outlook data", symbol)
        return None

    except Exception as e:
        logger.warning("Failed to fetch retail sentiment for %s: %s", symbol, e)
        return None


# ---------------------------------------------------------------------------
# 3. Interest Rate Differential (BoE vs BoJ)
# ---------------------------------------------------------------------------
try:
    from config import API_NINJAS_KEY
except ImportError:
    API_NINJAS_KEY = os.getenv("API_NINJAS_KEY", "")
INTEREST_RATE_URL = "https://api.api-ninjas.com/v1/interestrate"


async def fetch_rate_differential(
    base_currency: str = "GBP",
    quote_currency: str = "JPY",
) -> Optional[dict]:
    """Fetch central bank interest rates and calculate the carry trade spread.

    Uses API Ninjas (free, 10K requests/month) for rate data.
    Falls back to hardcoded recent values if API unavailable.
    Cached for 24 hours (rates change at most monthly).
    """
    cache_key = f"rates_{base_currency}_{quote_currency}_{date.today().isoformat()}"
    cached = _get_cache(cache_key, max_age_hours=24)
    if cached:
        return cached

    # Gold doesn't have a central bank — skip rate differential, use intermarket instead
    if base_currency == "XAU" or quote_currency == "XAU":
        logger.debug("Skipping rate differential for gold pair %s/%s", base_currency, quote_currency)
        return None

    # Map currencies to central bank names
    bank_map = {
        "GBP": "Bank of England",
        "JPY": "Bank of Japan",
        "EUR": "European Central Bank",
        "USD": "Federal Reserve",
        "AUD": "Reserve Bank of Australia",
        "CAD": "Bank of Canada",
        "CHF": "Swiss National Bank",
        "NZD": "Reserve Bank of New Zealand",
    }

    base_bank = bank_map.get(base_currency, "")
    quote_bank = bank_map.get(quote_currency, "")

    result = {
        "base_currency": base_currency,
        "quote_currency": quote_currency,
        "base_bank": base_bank,
        "quote_bank": quote_bank,
    }

    if API_NINJAS_KEY:
        try:
            headers = {"X-Api-Key": API_NINJAS_KEY}
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(INTEREST_RATE_URL, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            # Parse response — API returns list of central banks
            banks = data.get("central_bank_rates", data) if isinstance(data, dict) else data
            if isinstance(banks, list):
                for bank in banks:
                    name = bank.get("central_bank", "")
                    rate = bank.get("rate_pct", 0)
                    if base_bank and base_bank.lower() in name.lower():
                        result["base_rate"] = float(rate)
                    elif quote_bank and quote_bank.lower() in name.lower():
                        result["quote_rate"] = float(rate)

        except Exception as e:
            logger.warning("API Ninjas rate fetch failed: %s", e)

    # Calculate spread if we have both rates
    if "base_rate" in result and "quote_rate" in result:
        spread_bps = (result["base_rate"] - result["quote_rate"]) * 100
        result["spread_bps"] = round(spread_bps)
        result["carry_trade_status"] = (
            "strong" if spread_bps >= 400
            else "moderate" if spread_bps >= 250
            else "weakening" if spread_bps >= 100
            else "minimal"
        )
        logger.info("Rate diff %s/%s: %.2f%% - %.2f%% = %+d bps (%s)",
                    base_currency, quote_currency,
                    result["base_rate"], result["quote_rate"],
                    result["spread_bps"], result["carry_trade_status"])
        _set_cache(cache_key, result)
        return result

    # Fallback: try FRED API for major rates (no API key needed)
    return await _fetch_rates_fred(base_currency, quote_currency, result)


async def _fetch_rates_fred(
    base_currency: str,
    quote_currency: str,
    result: dict,
) -> Optional[dict]:
    """Fallback: fetch rates from FRED API (Federal Reserve Economic Data).
    Free, no API key required for basic access.
    """
    # FRED series IDs for central bank policy rates
    fred_series = {
        "GBP": "BOERUKM",    # Bank of England Official Bank Rate
        "EUR": "ECBMLFR",    # ECB Main Refinancing Rate
        "USD": "FEDFUNDS",   # Federal Funds Rate
        "JPY": "IRSTCB01JPM156N",  # BoJ policy rate
    }

    try:
        from config import FRED_API_KEY as fred_api_key
    except ImportError:
        fred_api_key = os.getenv("FRED_API_KEY", "")

    for label, currency in [("base", base_currency), ("quote", quote_currency)]:
        series_id = fred_series.get(currency)
        if not series_id:
            continue
        if f"{label}_rate" in result:
            continue  # Already fetched from API Ninjas

        try:
            params = {
                "series_id": series_id,
                "sort_order": "desc",
                "limit": 1,
                "file_type": "json",
            }
            if fred_api_key:
                params["api_key"] = fred_api_key

            url = "https://api.stlouisfed.org/fred/series/observations"
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

            observations = data.get("observations", [])
            if observations:
                rate_str = observations[0].get("value", ".")
                if rate_str != ".":
                    result[f"{label}_rate"] = float(rate_str)
                    logger.info("FRED %s rate: %.2f%%", currency, float(rate_str))

        except Exception as e:
            logger.debug("FRED fetch failed for %s: %s", currency, e)

    # Calculate spread
    if "base_rate" in result and "quote_rate" in result:
        spread_bps = (result["base_rate"] - result["quote_rate"]) * 100
        result["spread_bps"] = round(spread_bps)
        result["carry_trade_status"] = (
            "strong" if spread_bps >= 400
            else "moderate" if spread_bps >= 250
            else "weakening" if spread_bps >= 100
            else "minimal"
        )
        cache_key = f"rates_{base_currency}_{quote_currency}_{date.today().isoformat()}"
        _set_cache(cache_key, result)
        return result

    return None


# ---------------------------------------------------------------------------
# 4. Intermarket Data — Nikkei, DXY, US 10Y Yield
# ---------------------------------------------------------------------------
# Using Yahoo Finance unofficial API (yfinance-style) via a lightweight approach
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"


async def fetch_intermarket_data(
    base_currency: str = "GBP",
    quote_currency: str = "JPY",
) -> Optional[dict]:
    """Fetch key intermarket indicators relevant to the traded pair.

    Pair-specific correlations:
    - JPY pairs: Nikkei 225 (risk-on/off), DXY, US 10Y yield
    - GBP pairs: FTSE 100, DXY, US 10Y yield
    - EUR pairs: DAX, DXY, US 10Y yield
    - XAU pairs: DXY (inverse!), US 10Y yield (inverse!), VIX (positive)

    Cached for 2 hours (intraday data changes).
    """
    cache_key = f"intermarket_{base_currency}_{quote_currency}_{date.today().isoformat()}_{datetime.now(timezone.utc).hour // 2}"
    cached = _get_cache(cache_key, max_age_hours=2)
    if cached:
        return cached

    # Core tickers always included
    tickers = {
        "dxy": "DX-Y.NYB",
        "us_10y_yield": "^TNX",
    }

    # Add pair-specific indices
    currencies = {base_currency, quote_currency}

    if "JPY" in currencies:
        tickers["nikkei_225"] = "^N225"
    if "GBP" in currencies:
        tickers["ftse_100"] = "^FTSE"
    if "EUR" in currencies:
        tickers["dax"] = "^GDAXI"
    if "XAU" in currencies:
        tickers["gold_etf"] = "GLD"      # Gold ETF for volume context
        tickers["vix"] = "^VIX"          # Fear index — gold rises with VIX
    if "USD" in currencies and "DXY" not in [t.upper() for t in tickers]:
        pass  # DXY already included above
    if "AUD" in currencies:
        tickers["asx_200"] = "^AXJO"
    if "CAD" in currencies:
        tickers["oil_wti"] = "CL=F"      # CAD correlates with oil

    result = {}
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; TradingBot/1.0)",
    }

    for name, ticker in tickers.items():
        try:
            url = YAHOO_CHART_URL.format(ticker=ticker)
            params = {"interval": "1d", "range": "5d"}

            async with httpx.AsyncClient(timeout=10, headers=headers) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

            chart = data.get("chart", {}).get("result", [{}])[0]
            meta = chart.get("meta", {})
            quotes = chart.get("indicators", {}).get("quote", [{}])[0]

            price = meta.get("regularMarketPrice", 0)
            prev_close = meta.get("chartPreviousClose", 0) or meta.get("previousClose", 0)

            closes = quotes.get("close", [])
            # Filter out None values
            closes = [c for c in closes if c is not None]

            if price and prev_close:
                change_pct = ((price - prev_close) / prev_close) * 100
            else:
                change_pct = 0

            # 5-day trend
            if len(closes) >= 5:
                five_day_change = ((closes[-1] - closes[0]) / closes[0]) * 100 if closes[0] else 0
                trend = "up" if five_day_change > 0.5 else ("down" if five_day_change < -0.5 else "flat")
            else:
                five_day_change = 0
                trend = "unknown"

            result[name] = {
                "price": round(price, 2),
                "daily_change_pct": round(change_pct, 2),
                "five_day_change_pct": round(five_day_change, 2),
                "trend": trend,
            }

            logger.debug("Intermarket %s: %.2f (%+.2f%%)", name, price, change_pct)

        except Exception as e:
            logger.debug("Failed to fetch %s (%s): %s", name, ticker, e)

    if result:
        # Derive risk sentiment from equity indices
        equity_indices = ["nikkei_225", "ftse_100", "dax", "asx_200"]
        bullish_count = 0
        bearish_count = 0
        for idx in equity_indices:
            chg = result.get(idx, {}).get("daily_change_pct", 0)
            if chg > 0.3:
                bullish_count += 1
            elif chg < -0.3:
                bearish_count += 1

        if bullish_count >= 2:
            result["risk_sentiment"] = "risk_on"
        elif bearish_count >= 2:
            result["risk_sentiment"] = "risk_off"
        else:
            result["risk_sentiment"] = "mixed"

        # Gold-specific: DXY inverse correlation note
        if "XAU" in currencies:
            dxy_chg = result.get("dxy", {}).get("daily_change_pct", 0)
            vix_chg = result.get("vix", {}).get("daily_change_pct", 0)
            if dxy_chg < -0.3 or vix_chg > 3:
                result["gold_bias"] = "bullish (DXY weak / fear rising)"
            elif dxy_chg > 0.3 and vix_chg < -3:
                result["gold_bias"] = "bearish (DXY strong / calm markets)"
            else:
                result["gold_bias"] = "neutral"

        _set_cache(cache_key, result)
        logger.info("Intermarket data: %d indicators fetched, risk=%s",
                    len(result) - 1, result.get("risk_sentiment", "unknown"))

    return result if result else None


# ---------------------------------------------------------------------------
# Combined context builder — called before each Opus analysis
# ---------------------------------------------------------------------------
async def build_market_context(symbol: str, profile: dict) -> Optional[str]:
    """Build a concise market context string for injection into the Opus prompt.

    Fetches all available data sources in parallel, formats into ~200-300 tokens
    of structured context. Returns None if no data could be fetched.

    Cost: $0.00 (all free APIs) + ~200 extra input tokens (~$0.01 on Opus).
    """
    base = profile.get("base_currency", symbol[:3])
    quote = profile.get("quote_currency", symbol[3:])

    import asyncio

    # Fetch all sources in parallel
    cot_task = asyncio.create_task(fetch_cot_data(base, quote))
    sentiment_task = asyncio.create_task(fetch_retail_sentiment(symbol))
    rates_task = asyncio.create_task(fetch_rate_differential(base, quote))
    intermarket_task = asyncio.create_task(fetch_intermarket_data(base, quote))

    cot, sentiment, rates, intermarket = await asyncio.gather(
        cot_task, sentiment_task, rates_task, intermarket_task,
        return_exceptions=True,
    )

    # Handle exceptions gracefully
    if isinstance(cot, Exception):
        logger.warning("COT fetch exception: %s", cot)
        cot = None
    if isinstance(sentiment, Exception):
        logger.warning("Sentiment fetch exception: %s", sentiment)
        sentiment = None
    if isinstance(rates, Exception):
        logger.warning("Rates fetch exception: %s", rates)
        rates = None
    if isinstance(intermarket, Exception):
        logger.warning("Intermarket fetch exception: %s", intermarket)
        intermarket = None

    sections = []

    # --- COT Positioning ---
    if cot:
        cot_lines = []
        for label, entry in cot.items():
            cur = entry.get("currency", "?")
            net = entry.get("net_speculator", 0)
            change = entry.get("net_change", 0)
            shift = entry.get("positioning_shift", "")
            bias = "bullish" if net > 0 else "bearish"
            cot_lines.append(
                f"  {cur}: speculators net {net:+,} ({bias}, WoW change: {change:+,} — {shift})"
            )
        sections.append("COT Positioning (CFTC weekly):\n" + "\n".join(cot_lines))

    # --- Retail Sentiment ---
    if sentiment:
        crowd = sentiment.get("crowd_bias", "neutral")
        contrarian = sentiment.get("contrarian_signal", "neutral")
        sections.append(
            f"Retail Sentiment (Myfxbook):\n"
            f"  {symbol}: {sentiment['pct_long']:.0f}% long / {sentiment['pct_short']:.0f}% short "
            f"(crowd {crowd}, contrarian signal: {contrarian})"
        )

    # --- Interest Rate Differential ---
    if rates and "spread_bps" in rates:
        sections.append(
            f"Interest Rate Differential:\n"
            f"  {rates.get('base_bank', base)}: {rates.get('base_rate', '?'):.2f}% | "
            f"{rates.get('quote_bank', quote)}: {rates.get('quote_rate', '?'):.2f}%\n"
            f"  Spread: {rates['spread_bps']:+d} bps — carry trade: {rates.get('carry_trade_status', '?')}"
        )

    # --- Intermarket Indicators ---
    if intermarket:
        im_lines = []
        # Show all fetched indicators (dynamic per pair)
        skip_keys = {"risk_sentiment", "gold_bias"}
        for name, ind in intermarket.items():
            if name in skip_keys or not isinstance(ind, dict) or "price" not in ind:
                continue
            display = name.replace("_", " ").title()
            im_lines.append(
                f"  {display}: {ind['price']:.2f} ({ind['daily_change_pct']:+.2f}% today, "
                f"5d trend: {ind['trend']})"
            )
        risk = intermarket.get("risk_sentiment", "mixed")
        im_lines.append(f"  Overall risk sentiment: {risk}")
        if "gold_bias" in intermarket:
            im_lines.append(f"  Gold macro bias: {intermarket['gold_bias']}")
        sections.append("Intermarket Indicators:\n" + "\n".join(im_lines))

    if not sections:
        logger.info("[%s] No market context data available", symbol)
        return None

    context = "## MACRO & SENTIMENT CONTEXT (live data)\n" + "\n\n".join(sections)

    # Add interpretation guidance for the AI (pair-aware)
    context += "\n\nUse the above as additional confluence:"
    context += "\n- If COT opposes your chart bias → lower confidence by 1 tier"
    context += "\n- If retail is 65%+ one-sided → contrarian signal supports opposite direction"

    if base == "XAU":
        context += "\n- Gold: DXY inverse correlation — strong USD = bearish gold. Rising VIX = bullish gold"
        context += "\n- Gold: US 10Y yield inverse — rising real yields = bearish gold"
    else:
        if quote == "JPY":
            context += f"\n- If Nikkei is risk-off → JPY strengthens → bearish for {symbol}"
        if "spread_bps" in (rates if isinstance(rates, dict) else {}):
            context += "\n- If carry trade weakening → favor shorter-term setups over swings"
        if base == "GBP":
            context += "\n- FTSE 100 rallying supports GBP strength"
        if base == "EUR":
            context += "\n- DAX rallying supports EUR via risk-on sentiment"

    context += "\nDo NOT override chart-based ICT analysis — use this as a tiebreaker or confidence adjuster."

    logger.info("[%s] Market context built: %d chars, %d sections",
                symbol, len(context), len(sections))
    return context


# ---------------------------------------------------------------------------
# Quick summary for Telegram /context command
# ---------------------------------------------------------------------------
async def get_context_summary(symbol: str, profile: dict) -> str:
    """Get a formatted summary of all market context data for Telegram."""
    base = profile.get("base_currency", symbol[:3])
    quote = profile.get("quote_currency", symbol[3:])

    import asyncio

    cot, sentiment, rates, intermarket = await asyncio.gather(
        fetch_cot_data(base, quote),
        fetch_retail_sentiment(symbol),
        fetch_rate_differential(base, quote),
        fetch_intermarket_data(base, quote),
        return_exceptions=True,
    )

    lines = [f"\U0001f4ca {symbol} Market Context", "\u2501" * 25, ""]

    # COT
    if isinstance(cot, dict) and cot:
        lines.append("\U0001f4c8 COT Positioning:")
        for label, entry in cot.items():
            cur = entry.get("currency", "?")
            net = entry.get("net_speculator", 0)
            change = entry.get("net_change", 0)
            bias_emoji = "\U0001f7e2" if net > 0 else "\U0001f534"
            lines.append(f"  {bias_emoji} {cur}: net {net:+,} (WoW: {change:+,})")
    else:
        lines.append("\U0001f4c8 COT: unavailable")

    # Sentiment
    if isinstance(sentiment, dict) and sentiment:
        contrarian = sentiment.get("contrarian_signal", "neutral")
        c_emoji = {
            "bullish": "\U0001f7e2",
            "bearish": "\U0001f534",
            "neutral": "\u2796",
        }.get(contrarian, "")
        lines.append(
            f"\n\U0001f465 Retail Sentiment:\n"
            f"  {sentiment['pct_long']:.0f}% long / {sentiment['pct_short']:.0f}% short\n"
            f"  {c_emoji} Contrarian: {contrarian}"
        )
    else:
        lines.append("\n\U0001f465 Sentiment: unavailable")

    # Rates
    if isinstance(rates, dict) and "spread_bps" in rates:
        status = rates.get("carry_trade_status", "?")
        s_emoji = {
            "strong": "\U0001f7e2",
            "moderate": "\U0001f7e1",
            "weakening": "\U0001f7e0",
            "minimal": "\U0001f534",
        }.get(status, "")
        lines.append(
            f"\n\U0001f4b0 Rate Differential:\n"
            f"  {rates.get('base_bank', '')}: {rates.get('base_rate', '?'):.2f}%\n"
            f"  {rates.get('quote_bank', '')}: {rates.get('quote_rate', '?'):.2f}%\n"
            f"  {s_emoji} Spread: {rates['spread_bps']:+d} bps ({status})"
        )
    else:
        lines.append("\n\U0001f4b0 Rates: unavailable")

    # Intermarket
    if isinstance(intermarket, dict) and intermarket:
        risk = intermarket.get("risk_sentiment", "mixed")
        r_emoji = {
            "risk_on": "\U0001f7e2",
            "risk_off": "\U0001f534",
            "mixed": "\U0001f7e1",
        }.get(risk, "")
        lines.append(f"\n\U0001f30d Intermarket ({r_emoji} {risk}):")
        skip_keys = {"risk_sentiment", "gold_bias"}
        for name, ind in intermarket.items():
            if name in skip_keys or not isinstance(ind, dict) or "price" not in ind:
                continue
            display = name.replace("_", " ").title()
            chg = ind["daily_change_pct"]
            emoji = "\U0001f7e2" if chg > 0.3 else ("\U0001f534" if chg < -0.3 else "\u2796")
            lines.append(f"  {emoji} {display}: {ind['price']:.2f} ({chg:+.2f}%)")
        if "gold_bias" in intermarket:
            lines.append(f"  \U0001f947 Gold bias: {intermarket['gold_bias']}")
    else:
        lines.append("\n\U0001f30d Intermarket: unavailable")

    lines.append(f"\n\u23f0 Data cached — refreshes automatically")

    return "\n".join(lines)
