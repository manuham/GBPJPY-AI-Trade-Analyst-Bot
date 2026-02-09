from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from models import AnalysisResult, PendingTrade, TradeExecutionReport, TradeSetup

logger = logging.getLogger(__name__)

# Global state
_app: Optional[Application] = None
_last_analysis: Optional[AnalysisResult] = None
_last_scan_time: Optional[datetime] = None
_scan_callback = None  # Set by main.py to trigger analysis
_trade_queue_callback = None  # Set by main.py to queue trades for MT5


def set_scan_callback(callback):
    """Register the function to call when /scan is invoked."""
    global _scan_callback
    _scan_callback = callback


def set_trade_queue_callback(callback):
    """Register the function to queue a trade for MT5 execution."""
    global _trade_queue_callback
    _trade_queue_callback = callback


def store_analysis(result: AnalysisResult):
    """Store the latest analysis result."""
    global _last_analysis, _last_scan_time
    _last_analysis = result
    _last_scan_time = datetime.now(timezone.utc)


def _format_setup_message(setup: TradeSetup, summary: str) -> str:
    """Format a single trade setup as a Telegram message."""
    direction_emoji = "\U0001f7e2" if setup.bias == "long" else "\U0001f534"
    direction_label = "LONG" if setup.bias == "long" else "SHORT"
    tf_label = setup.timeframe_type.capitalize()

    confidence_emoji = {
        "high": "\U0001f525",
        "medium": "\u26a0\ufe0f",
        "low": "\u2753",
    }.get(setup.confidence, "")

    lines = [
        f"{direction_emoji} GBPJPY {direction_label} Setup ({tf_label})",
        "\u2501" * 20,
    ]

    # Show H1 trend and price zone context
    if setup.h1_trend:
        trend_emoji = {
            "bullish": "\U0001f7e2",
            "bearish": "\U0001f534",
            "ranging": "\u2194\ufe0f",
        }.get(setup.h1_trend, "")
        lines.append(f"{trend_emoji} H1 Trend: {setup.h1_trend.upper()}")
    if setup.price_zone:
        lines.append(f"\U0001f4cd Zone: {setup.price_zone.upper()}")
    if setup.counter_trend:
        lines.append("\u26a0\ufe0f COUNTER-TREND TRADE")

    lines += [
        "",
        f"\U0001f4cd Entry: {setup.entry_min:.3f} - {setup.entry_max:.3f}",
        f"\U0001f534 SL: {setup.stop_loss:.3f} ({setup.sl_pips:.0f} pips)",
        f"\U0001f3af TP1: {setup.tp1:.3f} ({setup.tp1_pips:.0f} pips) \u2014 close 50%",
        f"\U0001f3af TP2: {setup.tp2:.3f} ({setup.tp2_pips:.0f} pips) \u2014 runner",
        f"\U0001f4ca R:R: 1:{setup.rr_tp1:.1f} (TP1) | 1:{setup.rr_tp2:.1f} (TP2)",
        f"{confidence_emoji} Confidence: {setup.confidence.upper()}",
        "",
        "Confluence:",
    ]

    for reason in setup.confluence:
        lines.append(f"\u2022 {reason}")

    if setup.news_warning:
        lines.append("")
        lines.append(f"\u26a0\ufe0f {setup.news_warning}")

    lines.append("")
    lines.append(f"\U0001f4cb Summary: {summary}")

    return "\n".join(lines)


async def send_analysis(result: AnalysisResult):
    """Send analysis results to Telegram."""
    if not _app:
        logger.error("Telegram bot not initialized")
        return

    chat_id = TELEGRAM_CHAT_ID
    if not chat_id:
        logger.error("TELEGRAM_CHAT_ID not configured")
        return

    store_analysis(result)

    if not result.setups:
        # No setups found
        msg = (
            "\U0001f50d GBPJPY Analysis Complete\n"
            + "\u2501" * 20
            + "\n\n"
            + "\u274c No valid trade setups identified.\n\n"
        )
        if result.h1_trend_analysis:
            msg += f"\U0001f4c8 H1 Trend: {result.h1_trend_analysis}\n\n"
        msg += f"\U0001f4cb {result.market_summary}\n\n"
        if result.primary_scenario:
            msg += f"\U0001f4c8 Primary: {result.primary_scenario}\n"
        if result.alternative_scenario:
            msg += f"\U0001f4c9 Alternative: {result.alternative_scenario}\n"
        if result.upcoming_events:
            msg += "\n\U0001f4c5 Upcoming events:\n"
            for evt in result.upcoming_events:
                msg += f"\u2022 {evt}\n"

        try:
            await _app.bot.send_message(chat_id=chat_id, text=msg)
        except Exception as e:
            logger.error("Failed to send no-setup message: %s", e)
        return

    # Send each setup as a separate message with action buttons
    for i, setup in enumerate(result.setups):
        msg = _format_setup_message(setup, result.market_summary)

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "\u2705 Execute", callback_data=f"execute_{i}"
                    ),
                    InlineKeyboardButton("\u274c Skip", callback_data=f"skip_{i}"),
                ]
            ]
        )

        try:
            await _app.bot.send_message(
                chat_id=chat_id, text=msg, reply_markup=keyboard
            )
        except Exception as e:
            logger.error("Failed to send setup %d: %s", i, e)

    # Send summary with events
    if result.upcoming_events:
        events_msg = "\U0001f4c5 Upcoming Events:\n"
        for evt in result.upcoming_events:
            events_msg += f"\u2022 {evt}\n"
        try:
            await _app.bot.send_message(chat_id=chat_id, text=events_msg)
        except Exception as e:
            logger.error("Failed to send events message: %s", e)


async def _handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard button presses."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("execute_"):
        idx = int(data.split("_")[1])
        await query.edit_message_reply_markup(reply_markup=None)

        # Queue the trade for MT5 pickup
        if _last_analysis and 0 <= idx < len(_last_analysis.setups):
            setup = _last_analysis.setups[idx]
            trade_id = uuid.uuid4().hex[:8]
            pending = PendingTrade(
                id=trade_id,
                bias=setup.bias,
                entry_min=setup.entry_min,
                entry_max=setup.entry_max,
                stop_loss=setup.stop_loss,
                tp1=setup.tp1,
                tp2=setup.tp2,
                sl_pips=setup.sl_pips,
                confidence=setup.confidence,
            )
            if _trade_queue_callback:
                _trade_queue_callback(pending)
                direction = "LONG" if setup.bias == "long" else "SHORT"
                await query.message.reply_text(
                    f"\u2705 {direction} trade queued for MT5 execution!\n"
                    f"Trade ID: {trade_id}\n"
                    f"Entry: {setup.entry_min:.3f} - {setup.entry_max:.3f}\n"
                    f"SL: {setup.stop_loss:.3f} | TP1: {setup.tp1:.3f} | TP2: {setup.tp2:.3f}\n\n"
                    f"\u23f3 Waiting for MT5 EA to pick up..."
                )
            else:
                await query.message.reply_text(
                    "\u26a0\ufe0f Trade queue not available. Execute manually on MT5."
                )
        else:
            await query.message.reply_text(
                "\u26a0\ufe0f Setup data no longer available. Execute manually on MT5."
            )
        logger.info("Setup %s: EXECUTE selected", idx)

    elif data.startswith("skip_"):
        idx = data.split("_")[1]
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("\u274c Setup skipped")
        logger.info("Setup %s: SKIP selected", idx)


async def _cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /scan command."""
    chat_id = str(update.effective_chat.id)
    if TELEGRAM_CHAT_ID and chat_id != TELEGRAM_CHAT_ID:
        await update.message.reply_text("Unauthorized.")
        return

    if _scan_callback:
        await update.message.reply_text(
            "\U0001f50d Triggering manual scan... This may take a minute."
        )
        try:
            await _scan_callback()
        except Exception as e:
            logger.error("Scan callback failed: %s", e)
            await update.message.reply_text(f"\u274c Scan failed: {e}")
    elif _last_analysis:
        await update.message.reply_text(
            "\U0001f504 Re-sending last analysis result..."
        )
        await send_analysis(_last_analysis)
    else:
        await update.message.reply_text(
            "\u274c No previous analysis available.\n"
            "Trigger a scan from MT5 first (click the Scan button on chart), "
            "or wait for the next scheduled session."
        )


async def _cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command."""
    chat_id = str(update.effective_chat.id)
    if TELEGRAM_CHAT_ID and chat_id != TELEGRAM_CHAT_ID:
        await update.message.reply_text("Unauthorized.")
        return

    lines = ["\U0001f4ca GBPJPY Analyst Bot Status", "\u2501" * 20]

    lines.append("\u2705 Bot: Online")

    if _last_scan_time:
        lines.append(f"\U0001f553 Last scan: {_last_scan_time.strftime('%Y-%m-%d %H:%M UTC')}")
    else:
        lines.append("\U0001f553 Last scan: None")

    if _last_analysis and _last_analysis.setups:
        lines.append(f"\U0001f4c8 Last result: {len(_last_analysis.setups)} setup(s)")
    elif _last_analysis:
        lines.append("\U0001f4c8 Last result: No setups")
    else:
        lines.append("\U0001f4c8 Last result: N/A")

    lines.append("")
    lines.append("Scheduled scans:")
    lines.append("\u2022 London Open: 08:00 CET")
    lines.append("\u2022 NY Open: 14:30 CET")

    await update.message.reply_text("\n".join(lines))


async def _cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    msg = (
        "\U0001f916 GBPJPY AI Analyst Bot\n"
        + "\u2501" * 20
        + "\n\n"
        "Commands:\n"
        "/scan - Trigger manual analysis or re-send last result\n"
        "/status - Show bot status and last scan info\n"
        "/help - Show this help message\n\n"
        "The bot automatically analyzes GBPJPY at:\n"
        "\u2022 London Open (08:00 CET)\n"
        "\u2022 NY Open (14:30 CET)\n\n"
        "Trade setups include Execute/Skip buttons.\n"
        "Execute = manual confirmation to take trade on MT5."
    )
    await update.message.reply_text(msg)


async def _cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"\U0001f44b Welcome to GBPJPY AI Analyst Bot!\n\n"
        f"Your chat ID: {chat_id}\n\n"
        f"Use /help to see available commands."
    )


async def send_trade_confirmation(report: TradeExecutionReport):
    """Send trade execution confirmation to Telegram."""
    if not _app:
        logger.error("Telegram bot not initialized")
        return

    chat_id = TELEGRAM_CHAT_ID
    if not chat_id:
        return

    separator = "\u2501" * 20

    if report.status == "pending":
        # Limit orders placed — waiting for price to reach entry zone
        msg = (
            f"\u23f3 Limit Orders Placed on MT5!\n"
            f"{separator}\n"
            f"\U0001f194 Trade ID: {report.trade_id}\n"
            f"\U0001f4cd Limit Entry: {report.actual_entry:.3f}\n"
            f"\U0001f534 SL: {report.actual_sl:.3f}\n"
            f"\U0001f3af TP1: {report.actual_tp1:.3f} ({report.lots_tp1:.2f} lots) — order #{report.ticket_tp1}\n"
            f"\U0001f3af TP2: {report.actual_tp2:.3f} ({report.lots_tp2:.2f} lots) — order #{report.ticket_tp2}\n\n"
            f"Waiting for price to reach entry zone..."
        )
    elif report.status == "executed":
        msg = (
            f"\u2705 Trade Executed on MT5!\n"
            f"{separator}\n"
            f"\U0001f194 Trade ID: {report.trade_id}\n"
            f"\U0001f4b0 Entry: {report.actual_entry:.3f}\n"
            f"\U0001f534 SL: {report.actual_sl:.3f}\n"
            f"\U0001f3af TP1: {report.actual_tp1:.3f} ({report.lots_tp1:.2f} lots) — ticket #{report.ticket_tp1}\n"
            f"\U0001f3af TP2: {report.actual_tp2:.3f} ({report.lots_tp2:.2f} lots) — ticket #{report.ticket_tp2}\n"
        )
    else:
        msg = (
            f"\u274c Trade Execution Failed!\n"
            f"{separator}\n"
            f"\U0001f194 Trade ID: {report.trade_id}\n"
            f"\u26a0\ufe0f Error: {report.error_message}\n"
        )

    try:
        await _app.bot.send_message(chat_id=chat_id, text=msg)
    except Exception as e:
        logger.error("Failed to send trade confirmation: %s", e)


def create_bot_app() -> Application:
    """Create and configure the Telegram bot application."""
    global _app

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not configured")
        raise ValueError("TELEGRAM_BOT_TOKEN is required")

    _app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    _app.add_handler(CommandHandler("start", _cmd_start))
    _app.add_handler(CommandHandler("scan", _cmd_scan))
    _app.add_handler(CommandHandler("status", _cmd_status))
    _app.add_handler(CommandHandler("help", _cmd_help))
    _app.add_handler(CallbackQueryHandler(_handle_callback))

    logger.info("Telegram bot application created")
    return _app


def get_bot_app() -> Optional[Application]:
    """Get the current bot application instance."""
    return _app
