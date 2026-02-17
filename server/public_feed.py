# public_feed.py — Public P&L transparency layer
"""Handles public trade feed, Google Sheets sync, and public Telegram channel.

Phase 4 deliverables:
1. Public trade feed — every trade posted to public Telegram channel + API endpoint
2. Google Sheets sync — auto-sync trades to a public Google Sheet
3. Monthly PDF report — auto-generated on 1st of each month (see monthly_report.py)

All trades are immutable once posted — builds trust through full transparency.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PUBLIC_CHANNEL_ID = os.getenv("PUBLIC_TELEGRAM_CHANNEL_ID", "")  # e.g. "@gbpjpy_signals"

# Google Sheets config
GSHEETS_ENABLED = os.getenv("GSHEETS_ENABLED", "false").lower() == "true"
GSHEETS_SPREADSHEET_ID = os.getenv("GSHEETS_SPREADSHEET_ID", "")
GSHEETS_CREDENTIALS_FILE = os.getenv("GSHEETS_CREDENTIALS_FILE", "/data/gsheets_credentials.json")


# ---------------------------------------------------------------------------
# Public trade feed — formats trades for public consumption
# ---------------------------------------------------------------------------
def format_public_trade_alert(trade: dict, event: str = "opened") -> str:
    """Format a trade for public display (Telegram channel + API).

    event: "opened", "tp1_hit", "tp2_hit", "sl_hit", "closed"
    """
    symbol = trade.get("symbol", "UNKNOWN")
    bias = (trade.get("bias") or "").upper()
    confidence = (trade.get("confidence") or "").upper()
    checklist = trade.get("checklist_score", "N/A")

    if event == "opened":
        entry = trade.get("actual_entry", 0) or (
            (trade.get("entry_min", 0) + trade.get("entry_max", 0)) / 2
        )
        sl = trade.get("stop_loss", 0)
        tp1 = trade.get("tp1", 0)
        tp2 = trade.get("tp2", 0)
        sl_pips = trade.get("sl_pips", 0)
        rr_tp2 = trade.get("rr_tp2", 0)
        timestamp = trade.get("executed_at", "") or trade.get("created_at", "")

        return (
            f"\U0001f4e2 NEW TRADE \u2014 {symbol} {bias}\n"
            + "\u2501" * 25 + "\n"
            + f"\U0001f4cd Entry: {entry:.3f}\n"
            f"\U0001f534 SL: {sl:.3f} ({sl_pips:.1f} pips)\n"
            f"\U0001f3af TP1: {tp1:.3f}\n"
            f"\U0001f3af TP2: {tp2:.3f}\n"
            f"\U0001f4ca R:R to TP2: 1:{rr_tp2:.1f}\n\n"
            f"\U0001f4cb Checklist: {checklist}\n"
            f"\U0001f525 Confidence: {confidence}\n\n"
            f"\u23f0 {timestamp[:16] if timestamp else 'N/A'}\n"
            f"\U0001f916 AI-analyzed \u2022 ICT methodology"
        )

    elif event == "tp1_hit":
        pnl = trade.get("tp1_pips", 0)
        return (
            f"\U0001f3af TP1 HIT \u2014 {symbol} {bias}\n"
            + "\u2501" * 25 + "\n"
            + f"Partial close: +{pnl:.1f} pips\n"
            f"Runner still active \u2192 trailing to TP2\n"
            f"\U0001f4cb Checklist: {checklist}"
        )

    elif event == "tp2_hit":
        total_pnl = trade.get("pnl_pips", 0)
        return (
            f"\U0001f3af\U0001f3af FULL WIN \u2014 {symbol} {bias}\n"
            + "\u2501" * 25 + "\n"
            + f"Both targets hit! Total: +{total_pnl:.1f} pips\n"
            f"\U0001f4cb Checklist: {checklist}"
        )

    elif event == "sl_hit":
        loss = trade.get("sl_pips", 0)
        return (
            f"\U0001f534 STOP LOSS \u2014 {symbol} {bias}\n"
            + "\u2501" * 25 + "\n"
            + f"Loss: -{loss:.1f} pips\n"
            f"\U0001f4cb Checklist: {checklist}"
        )

    elif event == "closed":
        pnl = trade.get("pnl_pips", 0)
        outcome = trade.get("outcome", "closed")
        pnl_emoji = "\U0001f7e2" if pnl >= 0 else "\U0001f534"
        return (
            f"{pnl_emoji} TRADE CLOSED \u2014 {symbol} {bias}\n"
            + "\u2501" * 25 + "\n"
            + f"Outcome: {outcome}\n"
            f"P&L: {pnl:+.1f} pips\n"
            f"\U0001f4cb Checklist: {checklist}"
        )

    return f"Trade update: {symbol} {bias} — {event}"


async def post_to_public_channel(text: str) -> bool:
    """Post a message to the public Telegram channel.

    Requires PUBLIC_TELEGRAM_CHANNEL_ID to be set.
    Uses the same bot token as the private bot.
    """
    if not PUBLIC_CHANNEL_ID:
        logger.debug("Public channel ID not configured — skipping public post")
        return False

    try:
        from telegram_bot import _app
        if not _app:
            logger.warning("Telegram bot not initialized — cannot post to public channel")
            return False

        await _app.bot.send_message(
            chat_id=PUBLIC_CHANNEL_ID,
            text=text,
            parse_mode=None,  # Plain text — no formatting issues
        )
        logger.info("Posted to public channel %s", PUBLIC_CHANNEL_ID)
        return True

    except Exception as e:
        logger.error("Failed to post to public channel: %s", e)
        return False


# ---------------------------------------------------------------------------
# Public trade feed — JSON for API endpoint
# ---------------------------------------------------------------------------
def format_trade_for_api(trade: dict) -> dict:
    """Format a trade record for the public JSON API.

    Strips internal fields, keeps only what's safe/useful for public display.
    """
    return {
        "id": trade.get("id", ""),
        "symbol": trade.get("symbol", ""),
        "bias": trade.get("bias", ""),
        "confidence": trade.get("confidence", ""),
        "checklist_score": trade.get("checklist_score", ""),

        # Levels
        "entry_min": trade.get("entry_min", 0),
        "entry_max": trade.get("entry_max", 0),
        "actual_entry": trade.get("actual_entry", 0),
        "stop_loss": trade.get("stop_loss", 0),
        "tp1": trade.get("tp1", 0),
        "tp2": trade.get("tp2", 0),

        # Pips
        "sl_pips": trade.get("sl_pips", 0),
        "tp1_pips": trade.get("tp1_pips", 0),
        "tp2_pips": trade.get("tp2_pips", 0),
        "rr_tp1": trade.get("rr_tp1", 0),
        "rr_tp2": trade.get("rr_tp2", 0),

        # Outcome
        "status": trade.get("status", ""),
        "outcome": trade.get("outcome", ""),
        "pnl_pips": trade.get("pnl_pips", 0),
        "pnl_money": trade.get("pnl_money", 0),

        # Context
        "d1_trend": trade.get("d1_trend", ""),
        "h4_trend": trade.get("h4_trend", ""),
        "price_zone": trade.get("price_zone", ""),
        "trend_alignment": trade.get("trend_alignment", ""),

        # Timestamps
        "created_at": trade.get("created_at", ""),
        "executed_at": trade.get("executed_at", ""),
        "closed_at": trade.get("closed_at", ""),
    }


# ---------------------------------------------------------------------------
# Google Sheets sync
# ---------------------------------------------------------------------------
_sheets_service = None


def _get_sheets_service():
    """Lazy-load Google Sheets API service."""
    global _sheets_service
    if _sheets_service is not None:
        return _sheets_service

    if not GSHEETS_ENABLED:
        return None

    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build

        creds = Credentials.from_service_account_file(
            GSHEETS_CREDENTIALS_FILE,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        _sheets_service = build("sheets", "v4", credentials=creds)
        logger.info("Google Sheets service initialized")
        return _sheets_service

    except FileNotFoundError:
        logger.error("Google Sheets credentials file not found: %s", GSHEETS_CREDENTIALS_FILE)
        return None
    except ImportError:
        logger.error("Google API libraries not installed. Run: pip install google-auth google-api-python-client")
        return None
    except Exception as e:
        logger.error("Failed to initialize Google Sheets: %s", e)
        return None


def sync_trade_to_sheets(trade: dict) -> bool:
    """Append a trade row to the Google Sheet.

    Sheet structure (Row 1 = headers):
    Date | Symbol | Bias | Entry | SL | TP1 | TP2 | SL Pips | Checklist | Confidence | Outcome | P&L Pips | Notes
    """
    service = _get_sheets_service()
    if not service or not GSHEETS_SPREADSHEET_ID:
        return False

    try:
        row = [
            (trade.get("created_at") or "")[:10],           # Date
            trade.get("symbol", ""),                          # Symbol
            (trade.get("bias") or "").upper(),                # Bias
            trade.get("actual_entry", 0) or trade.get("entry_min", 0),  # Entry
            trade.get("stop_loss", 0),                        # SL
            trade.get("tp1", 0),                              # TP1
            trade.get("tp2", 0),                              # TP2
            trade.get("sl_pips", 0),                          # SL Pips
            trade.get("checklist_score", ""),                  # Checklist
            (trade.get("confidence") or "").upper(),           # Confidence
            trade.get("outcome", "open"),                     # Outcome
            trade.get("pnl_pips", 0) or 0,                   # P&L Pips
            "",                                                # Notes (manual)
        ]

        service.spreadsheets().values().append(
            spreadsheetId=GSHEETS_SPREADSHEET_ID,
            range="Trades!A:M",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()

        logger.info("Trade synced to Google Sheet: %s %s", trade.get("symbol"), trade.get("bias"))
        return True

    except Exception as e:
        logger.error("Failed to sync trade to Google Sheet: %s", e)
        return False


def update_trade_in_sheets(trade: dict) -> bool:
    """Update an existing trade row when it closes (update outcome + P&L columns).

    Searches for the trade by ID in column A, then updates the outcome and P&L.
    """
    service = _get_sheets_service()
    if not service or not GSHEETS_SPREADSHEET_ID:
        return False

    try:
        # Read all rows to find the one with matching date + symbol + entry
        result = service.spreadsheets().values().get(
            spreadsheetId=GSHEETS_SPREADSHEET_ID,
            range="Trades!A:M",
        ).execute()
        rows = result.get("values", [])

        trade_date = (trade.get("created_at") or "")[:10]
        trade_symbol = trade.get("symbol", "")
        trade_bias = (trade.get("bias") or "").upper()

        for i, row in enumerate(rows):
            if len(row) >= 3 and row[0] == trade_date and row[1] == trade_symbol and row[2] == trade_bias:
                # Update outcome (col K = index 10) and P&L (col L = index 11)
                row_num = i + 1  # 1-indexed
                service.spreadsheets().values().update(
                    spreadsheetId=GSHEETS_SPREADSHEET_ID,
                    range=f"Trades!K{row_num}:L{row_num}",
                    valueInputOption="USER_ENTERED",
                    body={"values": [[trade.get("outcome", ""), trade.get("pnl_pips", 0) or 0]]},
                ).execute()
                logger.info("Updated trade in Google Sheet row %d", row_num)
                return True

        logger.warning("Trade not found in Google Sheet for update: %s %s %s", trade_date, trade_symbol, trade_bias)
        return False

    except Exception as e:
        logger.error("Failed to update trade in Google Sheet: %s", e)
        return False


def init_sheets_headers() -> bool:
    """Create headers in the Google Sheet if they don't exist yet."""
    service = _get_sheets_service()
    if not service or not GSHEETS_SPREADSHEET_ID:
        return False

    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=GSHEETS_SPREADSHEET_ID,
            range="Trades!A1:M1",
        ).execute()

        if not result.get("values"):
            headers = [
                "Date", "Symbol", "Bias", "Entry", "SL", "TP1", "TP2",
                "SL Pips", "Checklist", "Confidence", "Outcome", "P&L Pips", "Notes"
            ]
            service.spreadsheets().values().update(
                spreadsheetId=GSHEETS_SPREADSHEET_ID,
                range="Trades!A1:M1",
                valueInputOption="RAW",
                body={"values": [headers]},
            ).execute()
            logger.info("Google Sheet headers initialized")

        return True

    except Exception as e:
        logger.error("Failed to init Google Sheet headers: %s", e)
        return False


# ---------------------------------------------------------------------------
# Public trade history — for the HTML page + API
# ---------------------------------------------------------------------------
def get_public_trade_history(limit: int = 100, symbol: Optional[str] = None) -> list[dict]:
    """Get trade history formatted for public display."""
    from trade_tracker import get_recent_trades
    trades = get_recent_trades(limit=limit, symbol=symbol)

    # Only show executed or closed trades (not queued/failed)
    public_trades = [
        format_trade_for_api(t) for t in trades
        if t.get("status") in ("executed", "closed")
    ]
    return public_trades


def get_public_stats(days: int = 30) -> dict:
    """Get aggregated performance stats for public display."""
    from trade_tracker import get_stats
    stats = get_stats(days=days)

    # Strip internal fields, keep only public-safe metrics
    return {
        "period_days": stats.get("period_days", days),
        "total_trades": stats.get("closed_trades", 0),
        "win_rate": round(stats.get("win_rate", 0), 1),
        "total_pnl_pips": round(stats.get("total_pnl_pips", 0), 1),
        "avg_win_pips": round(stats.get("avg_win_pips", 0), 1),
        "avg_loss_pips": round(stats.get("avg_loss_pips", 0), 1),
        "wins": stats.get("wins", 0),
        "losses": stats.get("losses", 0),
        "pair_stats": stats.get("pair_stats", {}),
        "confidence_stats": stats.get("confidence_stats", {}),
    }
