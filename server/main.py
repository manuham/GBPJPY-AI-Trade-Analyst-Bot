# v3.0 — Smart entry confirmation + London Kill Zone
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone, timedelta

from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import config
from analyzer import analyze_charts, confirm_entry
from models import AnalysisResult, MarketData, PendingTrade, WatchTrade, TradeExecutionReport
from pair_profiles import get_profile
from telegram_bot import (
    create_bot_app,
    get_bot_app,
    send_analysis,
    send_trade_confirmation,
    set_scan_callback,
    store_analysis,
)
from trade_tracker import init_db, log_trade_executed, log_trade_closed, get_stats as get_trade_stats, cleanup_stale_open_trades

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory storage — keyed by symbol for multi-pair support
# ---------------------------------------------------------------------------
_last_screenshots: dict[str, dict[str, bytes]] = {}   # {"GBPJPY": {"h1": b"...", ...}}
_last_market_data: dict[str, MarketData] = {}          # {"GBPJPY": MarketData(...)}
_last_results: dict[str, AnalysisResult] = {}           # {"GBPJPY": AnalysisResult(...)}
_analysis_lock = asyncio.Lock()

# Trade execution queue — one pending trade per symbol
_pending_trades: dict[str, PendingTrade] = {}           # {"GBPJPY": PendingTrade(...)}

# Watch trades — EA monitors price, confirms via Haiku before entry
_watch_trades: dict[str, WatchTrade] = {}               # {"GBPJPY": WatchTrade(...)}

# Trade expiry window — all EAs (leader + followers) have this long to pick up a trade
PENDING_TRADE_TTL_SECONDS = 60

# Auto-queue: minimum checklist score to auto-watch (skip Execute button)
AUTO_QUEUE_MIN_CHECKLIST = 7


def queue_pending_trade(trade: PendingTrade):
    """Called by telegram_bot when Execute is pressed."""
    trade.queued_at = time.time()
    _pending_trades[trade.symbol] = trade
    logger.info("[%s] Trade queued for MT5: %s %s (TTL=%ds)", trade.symbol, trade.bias.upper(), trade.id, PENDING_TRADE_TTL_SECONDS)


def get_pending_trade(symbol: str) -> Optional[PendingTrade]:
    """Return current pending trade for symbol (or None).
    Auto-expires trades older than PENDING_TRADE_TTL_SECONDS."""
    trade = _pending_trades.get(symbol)
    if trade and trade.queued_at > 0:
        if time.time() - trade.queued_at > PENDING_TRADE_TTL_SECONDS:
            logger.info("[%s] Pending trade %s expired (>%ds)", symbol, trade.id, PENDING_TRADE_TTL_SECONDS)
            _pending_trades.pop(symbol, None)
            return None
    return trade


def clear_pending_trade(symbol: str):
    """Remove the pending trade after MT5 picks it up."""
    _pending_trades.pop(symbol, None)


async def _run_scan_from_telegram(symbol: str = ""):
    """Callback invoked by the /scan Telegram command."""
    # If no symbol specified, use the most recently scanned pair
    if not symbol and _last_results:
        symbol = max(_last_results, key=lambda s: _last_results[s].market_summary != "")
    if not symbol:
        symbol = "GBPJPY"

    screenshots = _last_screenshots.get(symbol)
    market_data = _last_market_data.get(symbol)

    if screenshots and market_data:
        await _run_analysis(
            screenshots.get("d1", b""),
            screenshots.get("h4", b""),
            screenshots.get("h1", b""),
            screenshots.get("m5", b""),
            market_data,
        )
    elif symbol in _last_results:
        await send_analysis(_last_results[symbol])
    else:
        raise RuntimeError(
            f"No screenshots available for {symbol}. Trigger a scan from MT5 first."
        )


async def _run_analysis(
    d1: bytes, h4: bytes, h1: bytes, m5: bytes, market_data: MarketData
):
    """Run analysis pipeline, auto-queue qualifying setups as watches, send to Telegram."""
    symbol = market_data.symbol

    async with _analysis_lock:
        logger.info("[%s] Starting analysis pipeline...", symbol)
        result = await analyze_charts(d1, h4, h1, m5, market_data)
        _last_results[symbol] = result
        store_analysis(result)
        logger.info(
            "[%s] Analysis complete: %d setups found", symbol, len(result.setups)
        )

        # --- Auto-queue qualifying setups as watch trades ---
        auto_queued_indices: set[int] = set()
        for i, setup in enumerate(result.setups):
            checklist_num = _parse_checklist_score(setup.checklist_score)
            if checklist_num >= AUTO_QUEUE_MIN_CHECKLIST:
                # Run risk filters before auto-queuing
                from telegram_bot import check_risk_filters
                passed, reason = await check_risk_filters(symbol, setup)
                if passed:
                    watch = _create_watch_trade(symbol, setup)
                    _watch_trades[symbol] = watch
                    auto_queued_indices.add(i)
                    logger.info("[%s] Auto-queued watch: %s %s (checklist %s)",
                                symbol, setup.bias.upper(), watch.id, setup.checklist_score)
                    try:
                        from telegram_bot import send_watch_started
                        await send_watch_started(watch)
                    except Exception as e:
                        logger.error("[%s] Failed to send watch notification: %s", symbol, e)
                else:
                    logger.info("[%s] Setup %d blocked by risk filter: %s", symbol, i, reason)

        try:
            await send_analysis(result, auto_queued_indices=auto_queued_indices)
            logger.info("[%s] Telegram notifications sent", symbol)
        except Exception as e:
            logger.error("[%s] Failed to send Telegram notifications: %s", symbol, e)


def _parse_checklist_score(score: str) -> int:
    """Parse '10/12' → 10. Returns 0 if unparseable."""
    try:
        return int(score.split("/")[0]) if "/" in score else 0
    except (ValueError, IndexError):
        return 0


def _create_watch_trade(symbol: str, setup) -> WatchTrade:
    """Create a WatchTrade from a TradeSetup."""
    return WatchTrade(
        id=uuid.uuid4().hex[:8],
        symbol=symbol,
        bias=setup.bias,
        entry_min=setup.entry_min,
        entry_max=setup.entry_max,
        stop_loss=setup.stop_loss,
        tp1=setup.tp1,
        tp2=setup.tp2,
        sl_pips=setup.sl_pips,
        confidence=setup.confidence,
        confluence=setup.confluence[:3] if setup.confluence else [],
        checklist_score=setup.checklist_score,
        created_at=time.time(),
    )


# ---------------------------------------------------------------------------
# Lifespan — start / stop Telegram bot alongside FastAPI
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting AI Trade Analyst server on %s:%s", config.HOST, config.PORT)
    init_db()
    cleanup_stale_open_trades()
    try:
        bot_app = create_bot_app()
        set_scan_callback(_run_scan_from_telegram)
        from telegram_bot import set_trade_queue_callback
        set_trade_queue_callback(queue_pending_trade)
        await bot_app.initialize()
        await bot_app.start()
        if bot_app.updater:
            await bot_app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram bot started (polling mode)")
    except Exception as e:
        logger.error("Failed to start Telegram bot: %s", e)

    # Start watch expiry background task
    expiry_task = asyncio.create_task(_watch_expiry_loop())

    yield

    # Cancel background task
    expiry_task.cancel()

    # Shutdown
    logger.info("Shutting down...")
    bot_app = get_bot_app()
    if bot_app:
        try:
            if bot_app.updater and bot_app.updater.running:
                await bot_app.updater.stop()
            if bot_app.running:
                await bot_app.stop()
            await bot_app.shutdown()
        except Exception as e:
            logger.error("Error during bot shutdown: %s", e)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="AI Trade Analyst",
    version="3.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    # Show pending trades with remaining TTL
    pending_info = {}
    for s, t in _pending_trades.items():
        age = int(time.time() - t.queued_at) if t.queued_at else 0
        remaining = max(0, PENDING_TRADE_TTL_SECONDS - age)
        pending_info[s] = {"trade_id": t.id, "ttl_remaining": remaining}

    # Show active watch trades
    watch_info = {}
    for s, w in _watch_trades.items():
        age = int(time.time() - w.created_at) if w.created_at else 0
        watch_info[s] = {
            "trade_id": w.id,
            "bias": w.bias,
            "status": w.status,
            "confirmations": f"{w.confirmations_used}/{w.max_confirmations}",
            "age_seconds": age,
        }

    return {
        "status": "ok",
        "pairs_analyzed": list(_last_results.keys()),
        "pending_trades": pending_info,
        "watch_trades": watch_info,
        "setups": {s: len(r.setups) for s, r in _last_results.items()},
    }


@app.get("/stats")
async def stats(symbol: str = "", days: int = 30):
    """Performance statistics endpoint."""
    return get_trade_stats(symbol=symbol or None, days=days)


@app.post("/analyze")
async def analyze(
    request: Request,
    screenshot_d1: UploadFile = File(...),
    screenshot_h4: UploadFile = File(None),
    screenshot_h1: UploadFile = File(...),
    screenshot_m5: UploadFile = File(...),
    market_data: str = Form(...),
):
    """Receive screenshots and market data from MT5 EA, trigger analysis."""
    h4_bytes = b""
    if screenshot_h4:
        h4_bytes = await screenshot_h4.read()

    logger.info(
        "Received analysis request — files: d1=%s, h4=%s, h1=%s, m5=%s",
        screenshot_d1.filename,
        screenshot_h4.filename if screenshot_h4 else "N/A",
        screenshot_h1.filename,
        screenshot_m5.filename,
    )

    d1_bytes = await screenshot_d1.read()
    h1_bytes = await screenshot_h1.read()
    m5_bytes = await screenshot_m5.read()

    logger.info(
        "Screenshot sizes: D1=%d, H4=%d, H1=%d, M5=%d bytes",
        len(d1_bytes),
        len(h4_bytes),
        len(h1_bytes),
        len(m5_bytes),
    )

    try:
        md_dict = json.loads(market_data)
        md = MarketData(**md_dict)
    except Exception as e:
        logger.error("Failed to parse market data: %s", e)
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid market data JSON: {e}"},
        )

    symbol = md.symbol
    logger.info("[%s] Analysis request received", symbol)

    # Store for later re-use (e.g. /scan command) — keyed by symbol
    _last_screenshots[symbol] = {"d1": d1_bytes, "h4": h4_bytes, "h1": h1_bytes, "m5": m5_bytes}
    _last_market_data[symbol] = md

    asyncio.create_task(_run_analysis(d1_bytes, h4_bytes, h1_bytes, m5_bytes, md))

    return {"status": "accepted", "symbol": symbol, "message": "Analysis started"}


@app.get("/scan")
async def manual_scan(symbol: str = ""):
    """Manual trigger endpoint."""
    target = symbol or (list(_last_screenshots.keys())[0] if _last_screenshots else "")

    if target and target in _last_screenshots and target in _last_market_data:
        screenshots = _last_screenshots[target]
        asyncio.create_task(
            _run_analysis(
                screenshots.get("d1", b""),
                screenshots.get("h4", b""),
                screenshots.get("h1", b""),
                screenshots.get("m5", b""),
                _last_market_data[target],
            )
        )
        return {"status": "accepted", "symbol": target, "message": "Re-analysis started"}

    if target and target in _last_results:
        return {
            "status": "cached",
            "symbol": target,
            "message": "Returning last analysis",
            "setups": len(_last_results[target].setups),
        }

    return JSONResponse(
        status_code=404,
        content={"error": "No data available. Send screenshots from MT5 first."},
    )


@app.get("/pending_trade")
async def pending_trade(symbol: str = ""):
    """MT5 EA polls this to check for trades to execute.
    Leader/follower mode: trade stays available for 60 seconds so all
    EAs (leader + followers) can pick it up. Each EA's g_lastTradeId
    prevents duplicate execution on the same account."""
    trade = get_pending_trade(symbol)
    if trade:
        age = int(time.time() - trade.queued_at) if trade.queued_at else 0
        logger.info("[%s] Pending trade served: %s (age=%ds/%ds)", symbol, trade.id, age, PENDING_TRADE_TTL_SECONDS)
        return {"pending": True, "trade": trade.model_dump()}
    return {"pending": False}


@app.get("/watch_trade")
async def watch_trade_endpoint(symbol: str = ""):
    """MT5 EA polls this to get the current watch trade (zone to monitor).
    Returns the entry zone levels so the EA can watch locally."""
    watch = _watch_trades.get(symbol)
    if watch and watch.status == "watching":
        age = int(time.time() - watch.created_at) if watch.created_at else 0
        return {
            "has_watch": True,
            "trade": watch.model_dump(),
            "age_seconds": age,
        }
    return {"has_watch": False}


@app.post("/confirm_entry")
async def confirm_entry_endpoint(
    screenshot_m1: UploadFile = File(...),
    trade_id: str = Form(...),
    symbol: str = Form(...),
    bias: str = Form(...),
    current_price: float = Form(...),
    entry_min: float = Form(...),
    entry_max: float = Form(...),
):
    """MT5 EA calls this when price reaches the entry zone.
    Runs a Haiku M1 confirmation check before allowing entry."""
    watch = _watch_trades.get(symbol)
    if not watch or watch.id != trade_id:
        return JSONResponse(
            status_code=404,
            content={"confirmed": False, "reasoning": "Watch trade not found or ID mismatch"},
        )

    if watch.status != "watching":
        return {"confirmed": False, "reasoning": f"Watch is {watch.status}, not active"}

    if watch.confirmations_used >= watch.max_confirmations:
        watch.status = "rejected"
        return {"confirmed": False, "reasoning": "Max confirmation attempts exhausted", "remaining_checks": 0}

    # Read M1 screenshot
    m1_bytes = await screenshot_m1.read()
    logger.info("[%s] M1 confirmation request: trade=%s, price=%.3f (attempt %d/%d)",
                symbol, trade_id, current_price, watch.confirmations_used + 1, watch.max_confirmations)

    # Notify Telegram that zone was reached
    try:
        from telegram_bot import send_zone_reached
        await send_zone_reached(watch, watch.confirmations_used + 1)
    except Exception as e:
        logger.error("[%s] Failed to send zone-reached notification: %s", symbol, e)

    # Run Haiku confirmation
    result = await confirm_entry(
        screenshot_m1=m1_bytes,
        symbol=symbol,
        bias=bias,
        current_price=current_price,
        entry_min=entry_min,
        entry_max=entry_max,
        confluence=watch.confluence,
    )

    watch.confirmations_used += 1
    confirmed = result.get("confirmed", False)
    reasoning = result.get("reasoning", "")
    remaining = watch.max_confirmations - watch.confirmations_used

    # Notify Telegram of confirmation result
    try:
        from telegram_bot import send_confirmation_result
        await send_confirmation_result(watch, confirmed, reasoning)
    except Exception as e:
        logger.error("[%s] Failed to send confirmation result: %s", symbol, e)

    if confirmed:
        # Convert watch → pending trade for MT5 to pick up via /pending_trade
        watch.status = "confirmed"
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
        )
        queue_pending_trade(pending)

        # Log to performance tracker
        try:
            from trade_tracker import log_trade_queued
            analysis = _last_results.get(symbol)
            # Find matching setup
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
                entry_status="at_zone",
                entry_distance_pips=0,
                negative_factors=", ".join(setup.negative_factors) if setup and setup.negative_factors else "",
                price_zone=setup.price_zone if setup else "",
                h4_trend=setup.h4_trend if setup else "",
                checklist_score=watch.checklist_score,
            )
        except Exception as e:
            logger.error("[%s] Failed to log confirmed trade: %s", symbol, e)

        logger.info("[%s] M1 CONFIRMED — trade %s queued for execution", symbol, watch.id)
        return {"confirmed": True, "reasoning": reasoning, "remaining_checks": remaining}
    else:
        if remaining <= 0:
            watch.status = "rejected"
            logger.info("[%s] M1 REJECTED — max attempts reached, watch cancelled", symbol)
        else:
            logger.info("[%s] M1 REJECTED — %d attempts remaining", symbol, remaining)
        return {"confirmed": False, "reasoning": reasoning, "remaining_checks": remaining}


@app.post("/trade_executed")
async def trade_executed(report: TradeExecutionReport):
    """MT5 EA calls this after placing a trade.
    Note: does NOT clear the pending trade — TTL handles expiry so all
    EAs (leader + followers) can pick up the same trade within 60s."""
    logger.info(
        "[%s] Trade execution report: id=%s status=%s",
        report.symbol,
        report.trade_id,
        report.status,
    )

    # Log to performance tracker
    try:
        log_trade_executed(
            trade_id=report.trade_id,
            status=report.status,
            actual_entry=report.actual_entry,
            ticket_tp1=report.ticket_tp1,
            ticket_tp2=report.ticket_tp2,
            lots_tp1=report.lots_tp1,
            lots_tp2=report.lots_tp2,
            error_message=report.error_message,
        )
    except Exception as e:
        logger.error("[%s] Failed to log trade execution: %s", report.symbol, e)

    try:
        await send_trade_confirmation(report)
        logger.info("[%s] Trade confirmation sent to Telegram", report.symbol)
    except Exception as e:
        logger.error("[%s] Failed to send trade confirmation: %s", report.symbol, e)

    return {"status": "ok", "message": "Execution report received"}


class TradeCloseReport(BaseModel):
    """Report from MT5 EA when a position is closed (TP/SL hit)."""
    trade_id: str
    symbol: str = ""
    ticket: int = 0
    close_price: float = 0
    close_reason: str = ""     # "tp1", "tp2", "sl", "manual", "cancelled"
    profit: float = 0          # monetary P&L


@app.post("/trade_closed")
async def trade_closed(report: TradeCloseReport):
    """MT5 EA calls this when a position is closed (TP/SL hit)."""
    logger.info(
        "[%s] Trade close report: id=%s reason=%s profit=%.2f",
        report.symbol, report.trade_id, report.close_reason, report.profit,
    )

    try:
        log_trade_closed(
            trade_id=report.trade_id,
            ticket=report.ticket,
            close_price=report.close_price,
            close_reason=report.close_reason,
            profit=report.profit,
        )
    except Exception as e:
        logger.error("[%s] Failed to log trade close: %s", report.symbol, e)

    # Notify via Telegram
    try:
        from telegram_bot import send_trade_close_notification
        await send_trade_close_notification(report)
    except Exception as e:
        logger.error("[%s] Failed to send close notification: %s", report.symbol, e)

    return {"status": "ok", "message": "Close report received"}


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Telegram webhook endpoint (alternative to polling)."""
    bot_app = get_bot_app()
    if not bot_app:
        return JSONResponse(status_code=503, content={"error": "Bot not initialized"})

    try:
        data = await request.json()
        from telegram import Update

        update = Update.de_json(data, bot_app.bot)
        await bot_app.process_update(update)
        return {"status": "ok"}
    except Exception as e:
        logger.error("Webhook processing error: %s", e)
        return JSONResponse(status_code=500, content={"error": str(e)})


# ---------------------------------------------------------------------------
# Watch expiry background task
# ---------------------------------------------------------------------------
async def _watch_expiry_loop():
    """Expire active watches when the London Kill Zone ends (11:00 MEZ)."""
    while True:
        try:
            await asyncio.sleep(60)  # Check every minute

            # MEZ = UTC+1 (CET), MESZ = UTC+2 (CEST) — use UTC+1 for simplicity
            now_mez = datetime.now(timezone(timedelta(hours=1)))
            mez_hour = now_mez.hour

            for symbol, watch in list(_watch_trades.items()):
                if watch.status != "watching":
                    continue

                profile = get_profile(symbol)
                kill_zone_end = profile.get("kill_zone_end_mez", 11)

                if mez_hour >= kill_zone_end:
                    watch.status = "expired"
                    logger.info("[%s] Watch %s expired — Kill Zone ended (%d:00 MEZ)",
                                symbol, watch.id, kill_zone_end)
                    try:
                        from telegram_bot import send_watch_expired
                        await send_watch_expired(watch)
                    except Exception as e:
                        logger.error("[%s] Failed to send watch expiry notification: %s", symbol, e)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Watch expiry loop error: %s", e)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=config.HOST,
        port=config.PORT,
        log_level=config.LOG_LEVEL.lower(),
    )
