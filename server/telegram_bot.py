# v3.0 — Smart entry confirmation + London Kill Zone
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

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, MAX_DAILY_DRAWDOWN_PCT, MAX_OPEN_TRADES
import shared_state
from models import AnalysisResult, PendingTrade, WatchTrade, TradeExecutionReport, TradeSetup
from news_filter import check_news_restriction, get_upcoming_news
from pair_profiles import get_profile
from trade_tracker import (
    log_trade_queued, get_stats, get_recent_trades, get_open_trades,
    get_daily_pnl, check_correlation_conflict, force_close_all_open_trades,
    get_weekly_performance_report,
)

logger = logging.getLogger(__name__)

# Global state
_app: Optional[Application] = None
_last_analyses: dict[str, AnalysisResult] = {}   # keyed by symbol
_last_scan_times: dict[str, datetime] = {}        # keyed by symbol
_scan_callback = None
_trade_queue_callback = None


def set_scan_callback(callback):
    global _scan_callback
    _scan_callback = callback


def set_trade_queue_callback(callback):
    global _trade_queue_callback
    _trade_queue_callback = callback


def store_analysis(result: AnalysisResult):
    """Store the latest analysis result, keyed by symbol."""
    symbol = result.symbol or "UNKNOWN"
    _last_analyses[symbol] = result
    _last_scan_times[symbol] = datetime.now(timezone.utc)


def _fmt(price: float, digits: int) -> str:
    """Format a price with the correct number of decimal places."""
    return f"{price:.{digits}f}"


async def check_risk_filters(symbol: str, setup: TradeSetup) -> tuple[bool, str]:
    """Check all risk filters for a trade setup.
    Returns (passed: bool, block_reason: str). Reused by Execute button AND auto-queue."""
    # --- FTMO News Filter ---
    news_check = await check_news_restriction(symbol)
    if news_check.blocked:
        return False, f"News: {news_check.event_title}"

    # --- Daily Drawdown Check ---
    try:
        daily = get_daily_pnl()
        daily_pnl = daily["daily_pnl"]
        md = shared_state.last_market_data.get(symbol)
        if md and md.account_balance > 0:
            drawdown_pct = abs(min(0, daily_pnl)) / md.account_balance * 100
            if drawdown_pct >= MAX_DAILY_DRAWDOWN_PCT:
                return False, f"Drawdown: {drawdown_pct:.1f}%"
    except Exception:
        pass

    # --- Max Open Trades ---
    try:
        open_trades = get_open_trades()
        if len(open_trades) >= MAX_OPEN_TRADES:
            return False, f"Max trades: {len(open_trades)}/{MAX_OPEN_TRADES}"
    except Exception:
        pass

    # --- Correlation Filter ---
    try:
        corr_warning = check_correlation_conflict(symbol, setup.bias)
        if corr_warning:
            return False, f"Correlation: {corr_warning}"
    except Exception:
        pass

    return True, ""


def _format_setup_message(setup: TradeSetup, summary: str, symbol: str, digits: int) -> str:
    """Format a single trade setup as a Telegram message."""
    direction_emoji = "\U0001f7e2" if setup.bias == "long" else "\U0001f534"
    direction_label = "LONG" if setup.bias == "long" else "SHORT"
    tf_label = setup.timeframe_type.capitalize()

    confidence_emoji = {
        "high": "\U0001f525",
        "medium_high": "\U0001f7e2",
        "medium": "\u26a0\ufe0f",
        "low": "\u2753",
    }.get(setup.confidence, "")

    lines = [
        f"{direction_emoji} {symbol} {direction_label} Setup ({tf_label})",
        "\u2501" * 20,
    ]

    # Trend alignment (D1/H4/H1/M5 score)
    if setup.trend_alignment:
        align_emoji = "\U0001f7e2" if setup.trend_alignment.startswith("4/4") else "\U0001f7e2" if setup.trend_alignment.startswith("3/4") else "\U0001f7e1" if setup.trend_alignment.startswith("2/4") else "\U0001f534"
        lines.append(f"{align_emoji} Trend: {setup.trend_alignment}")
    elif setup.h1_trend:
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
    if setup.checklist_score:
        score_num = int(setup.checklist_score.split("/")[0]) if "/" in setup.checklist_score else 0
        cl_emoji = "\U0001f7e2" if score_num >= 10 else "\U0001f7e2" if score_num >= 8 else "\U0001f7e1" if score_num >= 6 else "\U0001f534"
        lines.append(f"{cl_emoji} ICT Checklist: {setup.checklist_score}")

    # Entry distance & status
    if setup.entry_status:
        status_emoji = {
            "at_zone": "\U0001f7e2",
            "approaching": "\U0001f7e1",
            "requires_pullback": "\U0001f534",
        }.get(setup.entry_status, "")
        dist_text = f"{setup.entry_distance_pips:.0f}p away" if setup.entry_distance_pips else ""
        lines.append(f"{status_emoji} Entry: {setup.entry_status.upper().replace('_', ' ')}" + (f" ({dist_text})" if dist_text else ""))

    lines += [
        "",
        f"\U0001f4cd Entry: {_fmt(setup.entry_min, digits)} - {_fmt(setup.entry_max, digits)}",
        f"\U0001f534 SL: {_fmt(setup.stop_loss, digits)} ({setup.sl_pips:.0f} pips)",
        f"\U0001f3af TP1: {_fmt(setup.tp1, digits)} ({setup.tp1_pips:.0f} pips) \u2014 close 50%",
        f"\U0001f3af TP2: {_fmt(setup.tp2, digits)} ({setup.tp2_pips:.0f} pips) \u2014 runner",
        f"\U0001f4ca R:R: 1:{setup.rr_tp1:.1f} (TP1) | 1:{setup.rr_tp2:.1f} (TP2)",
        f"{confidence_emoji} Confidence: {setup.confidence.upper().replace('_', '-')}",
        "",
        "Confluence:",
    ]

    for reason in setup.confluence:
        lines.append(f"\u2022 {reason}")

    # Negative factors (risks working against the trade)
    if setup.negative_factors:
        lines.append("")
        lines.append("Risks:")
        for factor in setup.negative_factors:
            lines.append(f"\u26a0\ufe0f {factor}")

    if setup.news_warning:
        lines.append("")
        lines.append(f"\u26a0\ufe0f {setup.news_warning}")

    lines.append("")
    lines.append(f"\U0001f4cb Summary: {summary}")

    return "\n".join(lines)


async def send_analysis(result: AnalysisResult, auto_queued_indices: set[int] | None = None):
    """Send analysis results to Telegram. auto_queued_indices = setups already watching."""
    if not _app:
        logger.error("Telegram bot not initialized")
        return

    chat_id = TELEGRAM_CHAT_ID
    if not chat_id:
        logger.error("TELEGRAM_CHAT_ID not configured")
        return

    store_analysis(result)

    symbol = result.symbol or "UNKNOWN"
    digits = result.digits or 3

    if not result.setups:
        msg = (
            f"\U0001f50d {symbol} Analysis Complete\n"
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

    # Check for upcoming news to add warning to setup messages
    news_check = await check_news_restriction(symbol)

    if auto_queued_indices is None:
        auto_queued_indices = set()

    # Only send Telegram messages for HIGH and MEDIUM_HIGH confidence setups
    NOTIFY_CONFIDENCES = {"high", "medium_high"}

    for i, setup in enumerate(result.setups):
        confidence = (setup.confidence or "").lower().strip()

        if confidence not in NOTIFY_CONFIDENCES and i not in auto_queued_indices:
            # Skip Telegram notification for medium/low setups (still logged server-side)
            logger.info("[%s] Setup %d skipped for Telegram (confidence=%s)", symbol, i, confidence)
            continue

        msg = _format_setup_message(setup, result.market_summary, symbol, digits)

        if i in auto_queued_indices:
            # This setup was auto-queued as a watch trade
            msg += (
                f"\n\n\U0001f50d AUTO-WATCHING\n"
                f"EA will monitor entry zone and confirm on M1 before entering."
            )
            keyboard = None  # No Execute/Skip buttons
        else:
            if news_check.blocked:
                msg += (
                    f"\n\n\U0001f6ab FTMO NEWS BLOCK ACTIVE\n"
                    f"\U0001f4f0 {news_check.event_currency}: {news_check.event_title}\n"
                    f"Execute button will be blocked until restriction passes."
                )
            elif news_check.warning:
                msg += f"\n\n\u26a0\ufe0f {news_check.message}"

            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "\u2705 Execute", callback_data=f"execute_{symbol}_{i}"
                        ),
                        InlineKeyboardButton(
                            "\u274c Skip", callback_data=f"skip_{symbol}_{i}"
                        ),
                    ]
                ]
            )

        try:
            await _app.bot.send_message(
                chat_id=chat_id, text=msg, reply_markup=keyboard
            )
        except Exception as e:
            logger.error("Failed to send setup %d: %s", i, e)

    if result.upcoming_events:
        events_msg = f"\U0001f4c5 {symbol} Upcoming Events:\n"
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
        # Format: execute_GBPJPY_0
        parts = data.split("_", 2)  # ["execute", "GBPJPY", "0"]
        if len(parts) == 3:
            symbol = parts[1]
            idx = int(parts[2])
        else:
            # Backward compat: execute_0
            symbol = ""
            idx = int(parts[1])

        await query.edit_message_reply_markup(reply_markup=None)

        analysis = _last_analyses.get(symbol)
        if analysis and 0 <= idx < len(analysis.setups):
            setup = analysis.setups[idx]
            digits = analysis.digits or 3

            # --- Run all risk filters ---
            passed, block_reason = await check_risk_filters(symbol, setup)
            if not passed:
                await query.message.reply_text(
                    f"\U0001f6ab {symbol} TRADE BLOCKED\n"
                    + "\u2501" * 20 + "\n"
                    + f"\u26a0\ufe0f {block_reason}\n\n"
                    f"Wait for the condition to clear, then try again."
                )
                logger.info("[%s] Trade BLOCKED: %s", symbol, block_reason)
                return

            trade_id = uuid.uuid4().hex[:8]
            pending = PendingTrade(
                id=trade_id,
                symbol=symbol,
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

                # Log to performance tracker (with full AI reasoning — Feature 6)
                try:
                    log_trade_queued(
                        trade_id=trade_id,
                        symbol=symbol,
                        bias=setup.bias,
                        entry_min=setup.entry_min,
                        entry_max=setup.entry_max,
                        stop_loss=setup.stop_loss,
                        tp1=setup.tp1,
                        tp2=setup.tp2,
                        sl_pips=setup.sl_pips,
                        confidence=setup.confidence,
                        tp1_pips=setup.tp1_pips,
                        tp2_pips=setup.tp2_pips,
                        rr_tp1=setup.rr_tp1,
                        rr_tp2=setup.rr_tp2,
                        h1_trend=setup.h1_trend,
                        counter_trend=setup.counter_trend,
                        raw_response=analysis.raw_response,
                        trend_alignment=setup.trend_alignment,
                        d1_trend=setup.d1_trend,
                        entry_status=setup.entry_status,
                        entry_distance_pips=setup.entry_distance_pips,
                        negative_factors=", ".join(setup.negative_factors) if setup.negative_factors else "",
                        price_zone=setup.price_zone,
                        h4_trend=setup.h4_trend,
                        checklist_score=setup.checklist_score,
                    )
                except Exception as e:
                    logger.error("Failed to log trade: %s", e)

                direction = "LONG" if setup.bias == "long" else "SHORT"

                await query.message.reply_text(
                    f"\u2705 {symbol} {direction} trade queued for MT5!\n"
                    f"Trade ID: {trade_id}\n"
                    f"Entry: {_fmt(setup.entry_min, digits)} - {_fmt(setup.entry_max, digits)}\n"
                    f"SL: {_fmt(setup.stop_loss, digits)} | TP1: {_fmt(setup.tp1, digits)} | TP2: {_fmt(setup.tp2, digits)}\n"
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
        logger.info("[%s] Setup %s: EXECUTE selected", symbol, idx)

    elif data.startswith("skip_"):
        parts = data.split("_", 2)
        symbol = parts[1] if len(parts) == 3 else ""
        idx = parts[2] if len(parts) == 3 else parts[1]
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(f"\u274c {symbol} setup skipped")
        logger.info("[%s] Setup %s: SKIP selected", symbol, idx)

    elif data.startswith("force_"):
        # Format: force_GBPJPY_tradeId
        parts = data.split("_", 2)
        if len(parts) == 3:
            symbol = parts[1]
            trade_id = parts[2]
        else:
            await query.message.reply_text("\u26a0\ufe0f Invalid force command.")
            return

        await query.edit_message_reply_markup(reply_markup=None)

        # Find the watch trade and convert it to a pending trade
        try:
            from main import _watch_trades, queue_pending_trade
            from trade_tracker import log_trade_queued

            watch = _watch_trades.get(symbol)
            if watch and watch.id == trade_id:
                # Convert watch → pending trade (same as confirmation success)
                watch.status = "confirmed"
                from main import delete_watch
                delete_watch(watch.id)

                pending = PendingTrade(
                    id=watch.id,
                    symbol=symbol,
                    bias=watch.bias,
                    entry_min=watch.entry_min,
                    entry_max=watch.entry_max,
                    stop_loss=watch.stop_loss,
                    tp1=watch.tp1,
                    tp2=watch.tp2,
                    sl_pips=watch.sl_pips,
                    confidence=watch.confidence,
                    tp1_close_pct=watch.tp1_close_pct,
                )
                queue_pending_trade(pending)

                # Log to tracker
                try:
                    analysis = _last_analyses.get(symbol)
                    setup = None
                    if analysis:
                        for s in analysis.setups:
                            if s.bias == watch.bias and abs(s.entry_min - watch.entry_min) < 0.01:
                                setup = s
                                break
                    log_trade_queued(
                        trade_id=watch.id,
                        symbol=symbol,
                        bias=watch.bias,
                        entry_min=watch.entry_min,
                        entry_max=watch.entry_max,
                        stop_loss=watch.stop_loss,
                        tp1=watch.tp1,
                        tp2=watch.tp2,
                        sl_pips=watch.sl_pips,
                        confidence=watch.confidence,
                        tp1_pips=setup.tp1_pips if setup else 0,
                        tp2_pips=setup.tp2_pips if setup else 0,
                        rr_tp1=setup.rr_tp1 if setup else 0,
                        rr_tp2=setup.rr_tp2 if setup else 0,
                        h1_trend=setup.h1_trend if setup else "",
                        counter_trend=setup.counter_trend if setup else False,
                        raw_response=analysis.raw_response if analysis else "",
                        trend_alignment=setup.trend_alignment if setup else "",
                        d1_trend=setup.d1_trend if setup else "",
                        entry_status="force_executed",
                        entry_distance_pips=setup.entry_distance_pips if setup else 0,
                        negative_factors=", ".join(setup.negative_factors) if setup and setup.negative_factors else "",
                        price_zone=setup.price_zone if setup else "",
                        h4_trend=setup.h4_trend if setup else "",
                        checklist_score=watch.checklist_score,
                    )
                except Exception as e:
                    logger.error("Failed to log force-executed trade: %s", e)

                direction = "LONG" if watch.bias == "long" else "SHORT"
                digits_num = get_profile(symbol).get("digits", 3)
                await query.message.reply_text(
                    f"\u26a1 {symbol} {direction} FORCE EXECUTED!\n"
                    f"Trade ID: {trade_id}\n"
                    f"Entry: {_fmt(watch.entry_min, digits_num)} - {_fmt(watch.entry_max, digits_num)}\n"
                    f"SL: {_fmt(watch.stop_loss, digits_num)} | TP1: {_fmt(watch.tp1, digits_num)} | TP2: {_fmt(watch.tp2, digits_num)}\n"
                    f"\u23f3 Waiting for MT5 EA to pick up..."
                )
                logger.info("[%s] Force execute: %s (M1 rejection overridden)", symbol, trade_id)
            else:
                await query.message.reply_text(
                    f"\u26a0\ufe0f Watch trade {trade_id} no longer active for {symbol}."
                )
        except Exception as e:
            logger.error("Force execute error: %s", e)
            await query.message.reply_text(f"\u26a0\ufe0f Force execute failed: {e}")

    elif data.startswith("dismiss_"):
        # Just dismiss the force execute button
        await query.edit_message_reply_markup(reply_markup=None)
        parts = data.split("_", 2)
        symbol = parts[1] if len(parts) >= 2 else ""
        await query.message.reply_text(f"\U0001f44c {symbol} M1 rejection acknowledged")


async def _cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /scan command. Usage: /scan or /scan GBPJPY"""
    chat_id = str(update.effective_chat.id)
    if TELEGRAM_CHAT_ID and chat_id != TELEGRAM_CHAT_ID:
        await update.message.reply_text("Unauthorized.")
        return

    # Parse optional symbol argument
    symbol = ""
    if context.args:
        symbol = context.args[0].upper()

    if _scan_callback:
        label = symbol or "last pair"
        await update.message.reply_text(
            f"\U0001f50d Triggering scan for {label}... This may take a minute."
        )
        try:
            await _scan_callback(symbol)
        except Exception as e:
            logger.error("Scan callback failed: %s", e)
            await update.message.reply_text(f"\u274c Scan failed: {e}")
    elif _last_analyses:
        target = symbol or list(_last_analyses.keys())[0]
        if target in _last_analyses:
            await update.message.reply_text(
                f"\U0001f504 Re-sending last {target} analysis..."
            )
            await send_analysis(_last_analyses[target])
        else:
            await update.message.reply_text(
                f"\u274c No analysis available for {target}."
            )
    else:
        await update.message.reply_text(
            "\u274c No previous analysis available.\n"
            "Trigger a scan from MT5 first, or wait for the next session.\n"
            "Usage: /scan or /scan GBPJPY"
        )


async def _cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command."""
    chat_id = str(update.effective_chat.id)
    if TELEGRAM_CHAT_ID and chat_id != TELEGRAM_CHAT_ID:
        await update.message.reply_text("Unauthorized.")
        return

    lines = ["\U0001f4ca AI Trade Analyst Status", "\u2501" * 20, "", "\u2705 Bot: Online", ""]

    if _last_scan_times:
        for symbol, scan_time in sorted(_last_scan_times.items()):
            analysis = _last_analyses.get(symbol)
            count = len(analysis.setups) if analysis else 0
            time_str = scan_time.strftime("%H:%M UTC")
            lines.append(f"\U0001f4b1 {symbol}: {count} setup(s) @ {time_str}")
    else:
        lines.append("\U0001f553 No scans yet")

    # Show active pairs from config
    try:
        from config import ACTIVE_PAIRS
        pairs_str = ", ".join(ACTIVE_PAIRS)
    except Exception:
        pairs_str = "GBPJPY"

    lines += [
        "",
        "Active pairs: " + pairs_str,
        "\u2022 Smart entry with M1 confirmation",
    ]

    await update.message.reply_text("\n".join(lines))


async def _cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command. Usage: /stats or /stats GBPJPY or /stats 7"""
    chat_id = str(update.effective_chat.id)
    if TELEGRAM_CHAT_ID and chat_id != TELEGRAM_CHAT_ID:
        await update.message.reply_text("Unauthorized.")
        return

    # Parse arguments: /stats, /stats GBPJPY, /stats 7, /stats GBPJPY 7
    symbol = None
    days = 30

    if context.args:
        for arg in context.args:
            if arg.isdigit():
                days = int(arg)
            else:
                symbol = arg.upper()

    stats = get_stats(symbol=symbol, days=days)

    if stats.get("total_trades", 0) == 0:
        await update.message.reply_text(
            f"\U0001f4ca No trades in the last {days} days"
            + (f" for {symbol}" if symbol else "")
            + ".\nTrades are logged when you press Execute."
        )
        return

    s = stats
    pnl_emoji = "\U0001f7e2" if s["total_pnl_pips"] >= 0 else "\U0001f534"

    lines = [
        f"\U0001f4ca Performance — {s['symbol']} ({s['period_days']}d)",
        "\u2501" * 25,
        "",
        f"Trades: {s['closed_trades']} closed | {s['open_trades']} open | {s['failed_trades']} failed",
        f"\u2705 Wins: {s['wins']} ({s['full_wins']} full + {s['partial_wins']} partial)",
        f"\u274c Losses: {s['losses']}",
        f"\U0001f3af Win Rate: {s['win_rate']:.0f}%",
        "",
        f"{pnl_emoji} P&L: {s['total_pnl_pips']:+.1f} pips | ${s['total_pnl_money']:+.2f}",
        f"\U0001f4c8 Avg Win: {s['avg_win_pips']:+.1f} pips",
        f"\U0001f4c9 Avg Loss: {s['avg_loss_pips']:.1f} pips",
    ]

    # Per-pair breakdown
    if s.get("pair_stats") and len(s["pair_stats"]) > 1:
        lines += ["", "\U0001f4b1 Per Pair:"]
        for sym, ps in s["pair_stats"].items():
            wr = f"{ps['win_rate']:.0f}%" if ps["closed"] else "n/a"
            lines.append(f"  {sym}: {ps['wins']}/{ps['closed']}W ({wr}) | {ps['pnl_pips']:+.1f} pips")

    # Per-confidence breakdown
    if s.get("confidence_stats"):
        lines += ["", "\U0001f525 By Confidence:"]
        for conf, cs in s["confidence_stats"].items():
            lines.append(f"  {conf.upper()}: {cs['wins']}/{cs['total']}W ({cs['win_rate']:.0f}%)")

    # Per-session breakdown
    if s.get("session_stats"):
        lines += ["", "\U0001f553 By Session:"]
        for sess, ss in s["session_stats"].items():
            lines.append(f"  {sess}: {ss['wins']}/{ss['total']}W ({ss['win_rate']:.0f}%)")

    # Recent trades
    recent = get_recent_trades(limit=5, symbol=symbol)
    if recent:
        lines += ["", "Recent trades:"]
        for t in recent:
            outcome_emoji = {
                "full_win": "\u2705",
                "partial_win": "\U0001f7e1",
                "loss": "\u274c",
                "open": "\u23f3",
                "cancelled": "\u2796",
                "failed": "\u26a0\ufe0f",
            }.get(t.get("outcome", ""), "\u2753")
            date_str = t.get("created_at", "")[:10]
            pnl = t.get("pnl_pips") or 0
            lines.append(
                f"  {outcome_emoji} {t['symbol']} {t['bias'].upper()} "
                f"({t.get('confidence', '?')}) {pnl:+.0f}p — {date_str}"
            )

    # Screening stats (Sonnet gate effectiveness)
    try:
        from trade_tracker import get_screening_stats, get_avg_m1_confirmations
        screen = get_screening_stats(days=days)
        if screen["total_scans"] > 0:
            lines += [
                "",
                f"\U0001f50d Screening: {screen['passed']}/{screen['total_scans']} passed ({screen['pass_rate']:.0f}%)",
            ]
        avg_m1 = get_avg_m1_confirmations(days=days)
        if avg_m1 > 0:
            lines.append(f"\U0001f4cd Avg M1 checks: {avg_m1}/trade")
    except Exception:
        pass

    lines += ["", f"Usage: /stats [SYMBOL] [DAYS]"]

    await update.message.reply_text("\n".join(lines))


async def _cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /news command. Shows upcoming high-impact news for tracked pairs."""
    chat_id = str(update.effective_chat.id)
    if TELEGRAM_CHAT_ID and chat_id != TELEGRAM_CHAT_ID:
        await update.message.reply_text("Unauthorized.")
        return

    # Use tracked pairs or default set
    tracked = list(_last_analyses.keys()) if _last_analyses else ["GBPJPY", "EURUSD", "GBPUSD", "USDJPY"]

    events = await get_upcoming_news(symbols=tracked, hours_ahead=24)

    if not events:
        await update.message.reply_text(
            "\U0001f4c5 No high-impact news in the next 24h for your pairs.\n"
            f"Tracked: {', '.join(tracked)}"
        )
        return

    lines = ["\U0001f4f0 Upcoming High-Impact News (24h)", "\u2501" * 20, ""]

    for evt in events:
        time_str = evt["time"].strftime("%a %H:%M UTC")
        forecast = f" (F: {evt['forecast']})" if evt["forecast"] else ""
        lines.append(f"\U0001f534 {time_str} — {evt['currency']}: {evt['title']}{forecast}")

    lines.append("")
    lines.append(f"\u26a0\ufe0f FTMO: No trades 2 min before/after these events")
    lines.append(f"\U0001f4b1 Tracked: {', '.join(tracked)}")

    await update.message.reply_text("\n".join(lines))


async def _cmd_drawdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /drawdown command — show daily P&L and risk status."""
    chat_id = str(update.effective_chat.id)
    if TELEGRAM_CHAT_ID and chat_id != TELEGRAM_CHAT_ID:
        await update.message.reply_text("Unauthorized.")
        return

    daily = get_daily_pnl()
    open_trades = get_open_trades()

    # Get account balance from latest market data
    balance_str = "unknown"
    drawdown_pct = 0.0
    limit_pct = MAX_DAILY_DRAWDOWN_PCT
    try:
        if shared_state.last_market_data:
            md = next(iter(shared_state.last_market_data.values()))
            if md.account_balance > 0:
                balance_str = f"${md.account_balance:,.2f}"
                drawdown_pct = abs(min(0, daily["daily_pnl"])) / md.account_balance * 100
    except Exception:
        pass

    pnl_emoji = "\U0001f7e2" if daily["daily_pnl"] >= 0 else "\U0001f534"
    status_emoji = "\u2705" if drawdown_pct < limit_pct else "\U0001f6d1"

    lines = [
        "\U0001f4ca Daily Risk Dashboard",
        "\u2501" * 25,
        "",
        f"\U0001f4b0 Account Balance: {balance_str}",
        f"{pnl_emoji} Daily P&L: ${daily['daily_pnl']:+.2f}",
        f"\U0001f4c9 Drawdown: {drawdown_pct:.2f}% / {limit_pct}% limit",
        f"{status_emoji} Status: {'TRADING ALLOWED' if drawdown_pct < limit_pct else 'BLOCKED — limit reached'}",
        "",
        f"\U0001f4ca Closed today: {daily['closed_trades_today']}",
        f"\U0001f4b1 Open trades: {len(open_trades)}/{MAX_OPEN_TRADES}",
    ]

    if open_trades:
        lines.append("")
        lines.append("Open positions:")
        for t in open_trades:
            direction = "\U0001f7e2" if t["bias"] == "long" else "\U0001f534"
            lines.append(f"  {direction} {t['symbol']} {t['bias'].upper()} ({t.get('confidence', '?')})")

    await update.message.reply_text("\n".join(lines))


async def _cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /reset command — force-close all stale open trades in DB."""
    chat_id = str(update.effective_chat.id)
    if TELEGRAM_CHAT_ID and chat_id != TELEGRAM_CHAT_ID:
        await update.message.reply_text("Unauthorized.")
        return

    open_trades = get_open_trades()
    if not open_trades:
        await update.message.reply_text(
            "\u2705 No open trades in database. Nothing to reset."
        )
        return

    count = force_close_all_open_trades()
    await update.message.reply_text(
        f"\u2705 Reset complete!\n"
        f"Force-closed {count} stale trade(s) in the database.\n\n"
        f"You can now execute new trades without blocks."
    )
    logger.info("User reset %d stale open trades via /reset", count)


async def _cmd_context(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /context command — show current macro/sentiment data."""
    chat_id = str(update.effective_chat.id)
    if TELEGRAM_CHAT_ID and chat_id != TELEGRAM_CHAT_ID:
        await update.message.reply_text("Unauthorized.")
        return

    symbol = "GBPJPY"
    if context.args:
        symbol = context.args[0].upper()

    await update.message.reply_text(f"\U0001f50d Fetching market context for {symbol}...")

    try:
        from market_context import get_context_summary
        profile = get_profile(symbol)
        summary = await get_context_summary(symbol, profile)
        await update.message.reply_text(summary)
    except Exception as e:
        logger.error("Context command error: %s", e)
        await update.message.reply_text(f"\u274c Failed to fetch context: {e}")


async def _cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    msg = (
        "\U0001f916 AI Trade Analyst Bot\n"
        + "\u2501" * 20
        + "\n\n"
        "Commands:\n"
        "/scan - Re-scan last pair or /scan GBPJPY\n"
        "/stats - Performance stats or /stats GBPJPY 7\n"
        "/context - Show macro/sentiment data (COT, rates, sentiment)\n"
        "/report - Weekly performance breakdown by pattern\n"
        "/drawdown - Daily P&L and risk status\n"
        "/news - Show upcoming high-impact news events\n"
        "/backtest - Show backtest results & data stats\n"
        "/reset - Force-close stale trades in DB\n"
        "/status - Show bot status for all pairs\n"
        "/help - Show this help message\n\n"
        "The bot analyzes active pairs during their session windows:\n"
        "\u2022 Each pair scans at kill zone start\n"
        "\u2022 EA watches entry zones locally (zero API cost)\n"
        "\u2022 M1 confirmation when price reaches zone\n\n"
        "High-confidence setups auto-watch (no manual approval).\n"
        "Lower confidence setups still show Execute/Skip buttons.\n"
        "Risk management: FTMO news filter, daily drawdown limit,\n"
        "correlation filter, max open trades cap."
    )
    await update.message.reply_text(msg)


async def _cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"\U0001f44b Welcome to AI Trade Analyst Bot!\n\n"
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

    symbol = report.symbol or "UNKNOWN"
    digits = get_profile(symbol)["digits"]
    separator = "\u2501" * 20

    if report.status == "pending":
        msg = (
            f"\u23f3 {symbol} Limit Orders Placed!\n"
            f"{separator}\n"
            f"\U0001f194 Trade ID: {report.trade_id}\n"
            f"\U0001f4cd Limit Entry: {_fmt(report.actual_entry, digits)}\n"
            f"\U0001f534 SL: {_fmt(report.actual_sl, digits)}\n"
            f"\U0001f3af TP1: {_fmt(report.actual_tp1, digits)} ({report.lots_tp1:.2f} lots) \u2014 order #{report.ticket_tp1}\n"
            f"\U0001f3af TP2: {_fmt(report.actual_tp2, digits)} ({report.lots_tp2:.2f} lots) \u2014 order #{report.ticket_tp2}\n\n"
            f"Waiting for price to reach entry zone..."
        )
    elif report.status == "executed":
        msg = (
            f"\u2705 {symbol} Trade Executed!\n"
            f"{separator}\n"
            f"\U0001f194 Trade ID: {report.trade_id}\n"
            f"\U0001f4b0 Entry: {_fmt(report.actual_entry, digits)}\n"
            f"\U0001f534 SL: {_fmt(report.actual_sl, digits)}\n"
            f"\U0001f3af TP1: {_fmt(report.actual_tp1, digits)} ({report.lots_tp1:.2f} lots) \u2014 ticket #{report.ticket_tp1}\n"
            f"\U0001f3af TP2: {_fmt(report.actual_tp2, digits)} ({report.lots_tp2:.2f} lots) \u2014 ticket #{report.ticket_tp2}\n"
        )
    else:
        msg = (
            f"\u274c {symbol} Trade Failed!\n"
            f"{separator}\n"
            f"\U0001f194 Trade ID: {report.trade_id}\n"
            f"\u26a0\ufe0f Error: {report.error_message}\n"
        )

    try:
        await _app.bot.send_message(chat_id=chat_id, text=msg)
    except Exception as e:
        logger.error("Failed to send trade confirmation: %s", e)


async def send_trade_close_notification(report):
    """Send notification when a position closes (TP/SL hit)."""
    if not _app:
        return

    chat_id = TELEGRAM_CHAT_ID
    if not chat_id:
        return

    symbol = report.symbol or "UNKNOWN"
    reason = report.close_reason or "unknown"

    reason_emoji = {
        "tp1": "\U0001f3af",
        "tp2": "\U0001f3af\U0001f3af",
        "sl": "\U0001f534",
        "manual": "\u270b",
        "cancelled": "\u2796",
    }.get(reason, "\u2753")

    pnl_emoji = "\U0001f7e2" if report.profit >= 0 else "\U0001f534"

    msg = (
        f"{reason_emoji} {symbol} Position Closed \u2014 {reason.upper()}\n"
        + "\u2501" * 20 + "\n"
        + f"\U0001f194 Trade: {report.trade_id}\n"
        f"\U0001f4b0 Close: {report.close_price}\n"
        f"{pnl_emoji} Profit: ${report.profit:+.2f}\n"
    )

    try:
        await _app.bot.send_message(chat_id=chat_id, text=msg)
    except Exception as e:
        logger.error("Failed to send close notification: %s", e)


# ---------------------------------------------------------------------------
# Watch trade notifications (smart entry flow)
# ---------------------------------------------------------------------------
async def send_watch_started(watch: WatchTrade):
    """Notify that a setup is being auto-watched."""
    if not _app:
        return
    chat_id = TELEGRAM_CHAT_ID
    if not chat_id:
        return

    profile = get_profile(watch.symbol)
    digits = profile["digits"]
    direction = "LONG" if watch.bias == "long" else "SHORT"

    msg = (
        f"\U0001f50d {watch.symbol} {direction} \u2014 Auto-Watching\n"
        + "\u2501" * 20 + "\n"
        + f"\U0001f194 Watch ID: {watch.id}\n"
        f"\U0001f4cd Zone: {watch.entry_min:.{digits}f} - {watch.entry_max:.{digits}f}\n"
        f"\U0001f525 Checklist: {watch.checklist_score} | Confidence: {watch.confidence.upper()}\n\n"
        f"EA is monitoring price. When zone is reached,\n"
        f"M1 will be checked for {watch.bias} reaction before entry.\n"
        f"Max {watch.max_confirmations} confirmation attempts."
    )

    try:
        await _app.bot.send_message(chat_id=chat_id, text=msg)
    except Exception as e:
        logger.error("Failed to send watch started: %s", e)


async def send_zone_reached(watch: WatchTrade, attempt: int):
    """Notify that price has reached the entry zone."""
    if not _app:
        return
    chat_id = TELEGRAM_CHAT_ID
    if not chat_id:
        return

    direction = "LONG" if watch.bias == "long" else "SHORT"
    reaction = "bullish" if watch.bias == "long" else "bearish"

    msg = (
        f"\U0001f4cd {watch.symbol} {direction} \u2014 Zone Reached!\n"
        + "\u2501" * 20 + "\n"
        + f"\U0001f194 Watch: {watch.id}\n"
        f"Checking M1 for {reaction} reaction... (attempt {attempt}/{watch.max_confirmations})"
    )

    try:
        await _app.bot.send_message(chat_id=chat_id, text=msg)
    except Exception as e:
        logger.error("Failed to send zone reached: %s", e)


async def send_confirmation_result(watch: WatchTrade, confirmed: bool, reasoning: str):
    """Notify the M1 confirmation result. On rejection, show Force Execute button."""
    if not _app:
        return
    chat_id = TELEGRAM_CHAT_ID
    if not chat_id:
        return

    direction = "LONG" if watch.bias == "long" else "SHORT"
    remaining = watch.max_confirmations - watch.confirmations_used

    keyboard = None

    if confirmed:
        msg = (
            f"\u2705 {watch.symbol} {direction} \u2014 M1 CONFIRMED!\n"
            + "\u2501" * 20 + "\n"
            + f"\U0001f194 Trade: {watch.id}\n"
            f"\U0001f4ac {reasoning}\n\n"
            f"Executing trade via MT5..."
        )
    else:
        status = f"{remaining} attempts left" if remaining > 0 else "Watch cancelled"
        msg = (
            f"\u274c {watch.symbol} {direction} \u2014 M1 Rejected\n"
            + "\u2501" * 20 + "\n"
            + f"\U0001f194 Watch: {watch.id}\n"
            f"\U0001f4ac {reasoning}\n"
            f"\u23f3 {status}"
        )

        # Always show Force Execute button on rejection so user can override
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "\u26a1 Force Execute", callback_data=f"force_{watch.symbol}_{watch.id}"
                    ),
                    InlineKeyboardButton(
                        "\u274c Dismiss", callback_data=f"dismiss_{watch.symbol}_{watch.id}"
                    ),
                ]
            ]
        )

    try:
        await _app.bot.send_message(chat_id=chat_id, text=msg, reply_markup=keyboard)
    except Exception as e:
        logger.error("Failed to send confirmation result: %s", e)


async def send_post_trade_insight(symbol: str, trade_id: str, review: str):
    """Send a post-trade Haiku review insight via Telegram."""
    if not _app:
        return
    chat_id = TELEGRAM_CHAT_ID
    if not chat_id:
        return

    msg = (
        f"\U0001f4a1 {symbol} Post-Trade Insight\n"
        + "\u2501" * 20 + "\n"
        + f"\U0001f194 Trade: {trade_id}\n"
        f"\U0001f4ac {review}"
    )

    try:
        await _app.bot.send_message(chat_id=chat_id, text=msg)
    except Exception as e:
        logger.error("Failed to send post-trade insight: %s", e)


async def send_watch_expired(watch: WatchTrade):
    """Notify that a watch has expired (kill zone ended)."""
    if not _app:
        return
    chat_id = TELEGRAM_CHAT_ID
    if not chat_id:
        return

    direction = "LONG" if watch.bias == "long" else "SHORT"
    profile = get_profile(watch.symbol)
    end_hour = profile.get("kill_zone_end_mez", 11)

    msg = (
        f"\u23f0 {watch.symbol} {direction} \u2014 Watch Expired\n"
        + "\u2501" * 20 + "\n"
        + f"\U0001f194 Watch: {watch.id}\n"
        f"London Kill Zone ended ({end_hour}:00 MEZ).\n"
        f"Price never reached the entry zone with M1 confirmation."
    )

    try:
        await _app.bot.send_message(chat_id=chat_id, text=msg)
    except Exception as e:
        logger.error("Failed to send watch expired: %s", e)


async def send_startup_notification():
    """Send a notification when the server starts/restarts."""
    if not _app:
        return
    chat_id = TELEGRAM_CHAT_ID
    if not chat_id:
        return
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    msg = (
        "\U0001f504 Bot Restarted\n"
        + "\u2501" * 20 + "\n"
        + f"Server is online and ready at {now}.\n"
        f"Use /status to check scan history."
    )
    try:
        await _app.bot.send_message(chat_id=chat_id, text=msg)
    except Exception as e:
        logger.error("Failed to send startup notification: %s", e)


async def send_missed_scan_alert(symbol: str, current_hour: int):
    """Alert that today's scan has not happened yet (called on startup)."""
    if not _app:
        return
    chat_id = TELEGRAM_CHAT_ID
    if not chat_id:
        return
    msg = (
        f"\u26a0\ufe0f {symbol} — Missed Scan Alert\n"
        + "\u2501" * 20 + "\n"
        + f"No scan recorded today. Current time: {current_hour}:00 MEZ.\n"
        f"The bot may have restarted after the Kill Zone opened.\n\n"
        f"Use /scan to trigger a manual scan (requires cached screenshots from MT5)."
    )
    try:
        await _app.bot.send_message(chat_id=chat_id, text=msg)
    except Exception as e:
        logger.error("Failed to send missed scan alert: %s", e)


async def send_scan_deadline_warning(symbol: str):
    """Warn at 08:30 MEZ that no scan has happened yet."""
    if not _app:
        return
    chat_id = TELEGRAM_CHAT_ID
    if not chat_id:
        return
    msg = (
        f"\u26a0\ufe0f {symbol} — Scan Deadline Warning\n"
        + "\u2501" * 20 + "\n"
        + "It is 08:30 MEZ and no analysis scan has arrived yet.\n"
        "Check that the MT5 EA is running and connected."
    )
    try:
        await _app.bot.send_message(chat_id=chat_id, text=msg)
    except Exception as e:
        logger.error("Failed to send scan deadline warning: %s", e)


async def send_weekly_report():
    """Send the weekly performance report automatically."""
    if not _app:
        return
    chat_id = TELEGRAM_CHAT_ID
    if not chat_id:
        return

    report = get_weekly_performance_report()
    msg = _format_weekly_report(report)

    try:
        await _app.bot.send_message(chat_id=chat_id, text=msg)
    except Exception as e:
        logger.error("Failed to send weekly report: %s", e)


def _format_weekly_report(report: dict) -> str:
    """Format the weekly performance report for Telegram."""
    if report.get("total", 0) == 0:
        return "\U0001f4ca Weekly Report\n" + "\u2501" * 20 + "\nNo closed trades this week."

    pnl_emoji = "\U0001f7e2" if report["total_pnl_pips"] >= 0 else "\U0001f534"
    lines = [
        "\U0001f4ca Weekly Performance Report",
        "\u2501" * 25,
        "",
        f"Trades: {report['total_trades']} | Wins: {report['wins']} | Losses: {report['losses']}",
        f"\U0001f3af Win Rate: {report['win_rate']:.0f}%",
        f"{pnl_emoji} P&L: {report['total_pnl_pips']:+.1f} pips",
    ]

    section_names = {
        "by_checklist_score": "\U0001f4cb By Checklist Score",
        "by_confidence": "\U0001f525 By Confidence",
        "by_entry_status": "\U0001f4cd By Entry Status",
        "by_trend_alignment": "\U0001f4c8 By Trend Alignment",
        "by_price_zone": "\U0001f4ca By Price Zone",
        "by_bias": "\U0001f4b1 By Bias",
    }

    for key, title in section_names.items():
        data = report.get(key, {})
        if not data:
            continue
        lines.append(f"\n{title}:")
        for bucket, stats in sorted(data.items()):
            if not bucket:
                continue
            lines.append(
                f"  {bucket}: {stats['wins']}/{stats['count']}W "
                f"({stats['win_rate']:.0f}%) | {stats['total_pnl']:+.1f}p"
            )

    return "\n".join(lines)


async def _cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /report command — weekly performance breakdown."""
    chat_id = str(update.effective_chat.id)
    if TELEGRAM_CHAT_ID and chat_id != TELEGRAM_CHAT_ID:
        await update.message.reply_text("Unauthorized.")
        return

    symbol = None
    if context.args:
        symbol = context.args[0].upper()

    report = get_weekly_performance_report(symbol=symbol)
    msg = _format_weekly_report(report)
    await update.message.reply_text(msg)


async def _cmd_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show latest backtest run summary or history data stats."""
    try:
        from backtest import get_backtest_runs, get_backtest_run, get_backtest_trades
        from backtest_report import generate_report, format_telegram_report
        from historical_data import get_candle_count, get_date_range

        # First show data availability
        m1_count = get_candle_count("GBPJPY", "M1")
        date_range = get_date_range("GBPJPY", "M1")

        lines = [
            "📊 *Backtest System*",
            f"{'━' * 28}",
            "",
            "*Historical Data:*",
        ]

        if m1_count > 0:
            lines.append(f"  M1 candles: {m1_count:,}")
            lines.append(f"  Range: {date_range[0][:10]} → {date_range[1][:10]}")
        else:
            lines.append("  ⚠️ No historical data loaded yet")
            lines.append("  Upload M1 CSV via /backtest\\_import")

        # Show latest backtest run
        runs = get_backtest_runs(limit=1)
        if runs:
            run = runs[0]
            trades = get_backtest_trades(run["id"])
            report = generate_report(run, trades)
            telegram_text = format_telegram_report(report)

            lines.append("")
            lines.append("*Latest Backtest Run:*")
            lines.append(telegram_text)
        else:
            lines.append("")
            lines.append("_No backtest runs yet._")
            lines.append("Use the API to run backtests: POST /backtest/run")

        await update.message.reply_text(
            "\n".join(lines),
            parse_mode="Markdown",
        )

    except Exception as e:
        logger.error("Backtest command error: %s", e)
        await update.message.reply_text(f"❌ Error: {e}")


def create_bot_app() -> Application:
    """Create and configure the Telegram bot application."""
    global _app

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not configured")
        raise ValueError("TELEGRAM_BOT_TOKEN is required")

    _app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    _app.add_handler(CommandHandler("start", _cmd_start))
    _app.add_handler(CommandHandler("scan", _cmd_scan))
    _app.add_handler(CommandHandler("stats", _cmd_stats))
    _app.add_handler(CommandHandler("drawdown", _cmd_drawdown))
    _app.add_handler(CommandHandler("news", _cmd_news))
    _app.add_handler(CommandHandler("reset", _cmd_reset))
    _app.add_handler(CommandHandler("status", _cmd_status))
    _app.add_handler(CommandHandler("help", _cmd_help))
    _app.add_handler(CommandHandler("report", _cmd_report))
    _app.add_handler(CommandHandler("context", _cmd_context))
    _app.add_handler(CommandHandler("backtest", _cmd_backtest))
    _app.add_handler(CallbackQueryHandler(_handle_callback))

    logger.info("Telegram bot application created")
    return _app


def get_bot_app() -> Optional[Application]:
    """Get the current bot application instance."""
    return _app
