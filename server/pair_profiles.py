"""Pair-specific configuration profiles for multi-pair support."""

from __future__ import annotations

PAIR_PROFILES: dict[str, dict] = {
    "GBPJPY": {
        "digits": 3,
        "typical_spread": "2-3 pips",
        "key_sessions": "London Kill Zone (08:00-11:00 MEZ)",
        "base_currency": "GBP",
        "quote_currency": "JPY",
        "specialization": "GBPJPY London Kill Zone — Asian range sweep patterns",
        "kill_zone_start_mez": 8,
        "kill_zone_end_mez": 20,
        "search_queries": [
            "GBPJPY forecast today",
            "GBP news today",
            "JPY news today",
            "forex economic calendar today GBP JPY",
        ],
        "fundamental_bias_options": '"bullish_gbp" or "bearish_gbp" or "neutral"',
    },
    "EURUSD": {
        "digits": 5,
        "typical_spread": "0.5-1.5 pips",
        "key_sessions": "London & NY overlap",
        "base_currency": "EUR",
        "quote_currency": "USD",
        "specialization": "major EUR pairs",
        "search_queries": [
            "EURUSD forecast today",
            "EUR news today",
            "USD news today",
            "forex economic calendar today EUR USD",
        ],
        "fundamental_bias_options": '"bullish_eur" or "bearish_eur" or "neutral"',
    },
    "GBPUSD": {
        "digits": 5,
        "typical_spread": "1-2 pips",
        "key_sessions": "London & NY overlap",
        "base_currency": "GBP",
        "quote_currency": "USD",
        "specialization": "major GBP pairs",
        "search_queries": [
            "GBPUSD forecast today",
            "GBP news today",
            "USD news today",
            "forex economic calendar today GBP USD",
        ],
        "fundamental_bias_options": '"bullish_gbp" or "bearish_gbp" or "neutral"',
    },
    "XAUUSD": {
        "digits": 2,
        "typical_spread": "2-4 pips",
        "key_sessions": "London & NY overlap",
        "base_currency": "XAU",
        "quote_currency": "USD",
        "specialization": "gold / precious metals",
        "search_queries": [
            "XAUUSD gold forecast today",
            "gold price news today",
            "USD news today",
            "forex economic calendar today USD gold",
        ],
        "fundamental_bias_options": '"bullish_gold" or "bearish_gold" or "neutral"',
    },
    "USDJPY": {
        "digits": 3,
        "typical_spread": "1-2 pips",
        "key_sessions": "Tokyo & NY overlap",
        "base_currency": "USD",
        "quote_currency": "JPY",
        "specialization": "JPY crosses",
        "search_queries": [
            "USDJPY forecast today",
            "USD news today",
            "JPY news today",
            "forex economic calendar today USD JPY",
        ],
        "fundamental_bias_options": '"bullish_usd" or "bearish_usd" or "neutral"',
    },
    "EURJPY": {
        "digits": 3,
        "typical_spread": "2-3 pips",
        "key_sessions": "London & Tokyo overlap",
        "base_currency": "EUR",
        "quote_currency": "JPY",
        "specialization": "JPY crosses",
        "search_queries": [
            "EURJPY forecast today",
            "EUR news today",
            "JPY news today",
            "forex economic calendar today EUR JPY",
        ],
        "fundamental_bias_options": '"bullish_eur" or "bearish_eur" or "neutral"',
    },
}


def get_profile(symbol: str) -> dict:
    """Get pair profile. Returns sensible defaults for unknown pairs."""
    if symbol in PAIR_PROFILES:
        return PAIR_PROFILES[symbol]

    # Auto-detect defaults based on symbol name
    is_jpy = symbol.endswith("JPY")
    is_gold = symbol.startswith("XAU")

    # Try to extract base/quote currencies
    base = symbol[:3]
    quote = symbol[3:]

    return {
        "digits": 2 if is_gold else (3 if is_jpy else 5),
        "typical_spread": "2-4 pips" if is_gold else ("2-3 pips" if is_jpy else "1-2 pips"),
        "key_sessions": "London & NY overlap",
        "base_currency": base,
        "quote_currency": quote,
        "specialization": "forex pairs",
        "search_queries": [
            f"{symbol} forecast today",
            f"{base} news today",
            f"{quote} news today",
            f"forex economic calendar today {base} {quote}",
        ],
        "fundamental_bias_options": f'"bullish_{base.lower()}" or "bearish_{base.lower()}" or "neutral"',
    }
