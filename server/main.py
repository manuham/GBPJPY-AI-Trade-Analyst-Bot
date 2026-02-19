# v3.0 — Smart entry confirmation + London Kill Zone
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

import config
import shared_state
from analyzer import analyze_charts, confirm_entry
from models import AnalysisResult, MarketData, PendingTrade, WatchTrade, TradeExecutionReport, TradeCloseReport
from pair_profiles import get_profile
from telegram_bot import (
    create_bot_app,
    get_bot_app,
    send_analysis,
    send_trade_confirmation,
    set_scan_callback,
    store_analysis,
)
from trade_tracker import (
    init_db, log_trade_executed, log_trade_closed, get_stats as get_trade_stats,
    cleanup_stale_open_trades, log_scan_completed, get_last_scan_for_symbol,
    persist_watch, load_active_watches, delete_watch, update_watch_status,
)

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
# shared_state.last_market_data is in shared_state.py (breaks circular import with telegram_bot)
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
        symbol = config.ACTIVE_PAIRS[0] if config.ACTIVE_PAIRS else "GBPJPY"

    screenshots = _last_screenshots.get(symbol)
    market_data = shared_state.last_market_data.get(symbol)

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
        log_scan_completed(symbol)
        logger.info(
            "[%s] Analysis complete: %d setups found", symbol, len(result.setups)
        )

        # Archive screenshots for backtesting
        _archive_screenshots(symbol, {"d1": d1, "h4": h4, "h1": h1, "m5": m5})

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
                    persist_watch(watch.id, symbol, watch.model_dump_json())
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
    # Adaptive TP1 close %: high confidence → let more ride, low → take profit early
    checklist_num = _parse_checklist_score(setup.checklist_score)
    if checklist_num >= 10:
        tp1_pct = 40.0  # HIGH (10-12): close less at TP1, let 60% ride to TP2
    elif checklist_num >= 8:
        tp1_pct = 45.0  # MEDIUM_HIGH (8-9): slightly more rides to TP2
    elif checklist_num >= 6:
        tp1_pct = 55.0  # MEDIUM (6-7): balanced
    else:
        tp1_pct = 60.0  # LOW (4-5): take more profit at TP1

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
        tp1_close_pct=tp1_pct,
        created_at=time.time(),
    )


# ---------------------------------------------------------------------------
# Screenshot archiving — save for replay / backtesting
# ---------------------------------------------------------------------------
SCREENSHOTS_DIR = os.path.join(os.getenv("DATA_DIR", "/data"), "screenshots")
SCREENSHOT_RETENTION_DAYS = 30


def _archive_screenshots(symbol: str, screenshots: dict[str, bytes]):
    """Save screenshots to disk for backtesting / review."""
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        screenshot_dir = Path(SCREENSHOTS_DIR) / f"{today}_{symbol}"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%H%M%S")
        for tf, data in screenshots.items():
            if data:
                path = screenshot_dir / f"{ts}_{tf}.png"
                path.write_bytes(data)
        logger.info("[%s] Screenshots archived to %s", symbol, screenshot_dir)
    except Exception as e:
        logger.error("[%s] Failed to archive screenshots: %s", symbol, e)


def _cleanup_old_screenshots():
    """Delete screenshots older than SCREENSHOT_RETENTION_DAYS."""
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=SCREENSHOT_RETENTION_DAYS)
        screenshots_path = Path(SCREENSHOTS_DIR)
        if not screenshots_path.exists():
            return
        for d in screenshots_path.iterdir():
            if not d.is_dir():
                continue
            date_str = d.name.split("_")[0]
            try:
                dir_date = datetime.strptime(date_str, "%Y-%m-%d")
                if dir_date.date() < cutoff.date():
                    shutil.rmtree(d)
                    logger.info("Deleted old screenshots: %s", d)
            except (ValueError, OSError):
                pass
    except Exception as e:
        logger.error("Screenshot cleanup error: %s", e)


# ---------------------------------------------------------------------------
# Lifespan — start / stop Telegram bot alongside FastAPI
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting AI Trade Analyst server on %s:%s", config.HOST, config.PORT)
    init_db()
    cleanup_stale_open_trades()

    # --- Restore persisted watch trades ---
    try:
        saved_watches = load_active_watches()
        for row in saved_watches:
            watch = WatchTrade.model_validate_json(row["watch_json"])
            if watch.status == "watching":
                _watch_trades[watch.symbol] = watch
                logger.info("[%s] Restored watch %s from DB", watch.symbol, watch.id)
        if saved_watches:
            logger.info("Restored %d active watch(es) from database", len(saved_watches))
    except Exception as e:
        logger.error("Failed to restore watches: %s", e)

    # --- Cleanup old screenshots ---
    _cleanup_old_screenshots()

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

    # --- Send startup notification to Telegram ---
    try:
        from telegram_bot import send_startup_notification
        await send_startup_notification()
    except Exception as e:
        logger.error("Failed to send startup notification: %s", e)

    # --- Check if today's scan was missed ---
    try:
        now_mez = datetime.now(timezone(timedelta(hours=1)))
        for symbol in config.ACTIVE_PAIRS:
            profile = get_profile(symbol)
            kz_start = profile.get("kill_zone_start_mez", 8)
            kz_end = profile.get("kill_zone_end_mez", 20)
            last_scan = get_last_scan_for_symbol(symbol)
            today_str = now_mez.strftime("%Y-%m-%d")
            scan_done_today = last_scan and last_scan["scan_date"] == today_str

            if not scan_done_today and kz_start <= now_mez.hour < kz_end:
                logger.warning("[%s] Missed today's scan — sending alert", symbol)
                try:
                    from telegram_bot import send_missed_scan_alert
                    await send_missed_scan_alert(symbol, now_mez.hour)
                except Exception as e:
                    logger.error("[%s] Failed to send missed scan alert: %s", symbol, e)
    except Exception as e:
        logger.error("Startup scan check error: %s", e)

    # Start background tasks
    expiry_task = asyncio.create_task(_system_tasks_loop())

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

# CORS — allow dashboard (Streamlit) to call API
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# API key authentication middleware
# ---------------------------------------------------------------------------
@app.middleware("http")
async def verify_api_key(request: Request, call_next):
    """Check X-API-Key header on all endpoints except /health and /webhook/telegram."""
    # Skip auth for health check, Telegram webhook, and public endpoints
    if request.url.path in ("/health", "/webhook/telegram") or request.url.path.startswith("/public/"):
        return await call_next(request)

    # Skip auth if no API_KEY configured (backward compatible)
    if not config.API_KEY:
        return await call_next(request)

    api_key = request.headers.get("X-API-Key", "")
    if api_key != config.API_KEY:
        logger.warning("Unauthorized request to %s from %s", request.url.path, request.client.host if request.client else "unknown")
        return JSONResponse(status_code=401, content={"error": "Unauthorized — invalid or missing X-API-Key"})

    return await call_next(request)


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
    shared_state.last_market_data[symbol] = md

    asyncio.create_task(_run_analysis(d1_bytes, h4_bytes, h1_bytes, m5_bytes, md))

    return {"status": "accepted", "symbol": symbol, "message": "Analysis started"}


@app.get("/scan")
async def manual_scan(symbol: str = ""):
    """Manual trigger endpoint."""
    target = symbol or (list(_last_screenshots.keys())[0] if _last_screenshots else "")

    if target and target in _last_screenshots and target in shared_state.last_market_data:
        screenshots = _last_screenshots[target]
        asyncio.create_task(
            _run_analysis(
                screenshots.get("d1", b""),
                screenshots.get("h4", b""),
                screenshots.get("h1", b""),
                screenshots.get("m5", b""),
                shared_state.last_market_data[target],
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

    confirmed = result.get("confirmed", False)
    reasoning = result.get("reasoning", "")

    # Only count real analysis attempts, not transient API errors
    # (errors start with "Error:" from analyzer.py exception handler)
    is_transient_error = reasoning.startswith("Error:") or reasoning.startswith("Parse failed")
    if not is_transient_error:
        watch.confirmations_used += 1
    else:
        logger.warning("[%s] Haiku transient error (not counted as attempt): %s", symbol, reasoning)
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

        # Track M1 confirmation attempts (Step 8)
        try:
            from trade_tracker import update_trade_confirmations
            update_trade_confirmations(watch.id, watch.confirmations_used)
        except Exception as e:
            logger.error("[%s] Failed to log M1 confirmations: %s", symbol, e)

        logger.info("[%s] M1 CONFIRMED — trade %s queued for execution", symbol, watch.id)
        return {"confirmed": True, "reasoning": reasoning, "remaining_checks": remaining}
    else:
        if remaining <= 0:
            watch.status = "rejected"
            delete_watch(watch.id)
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

    # --- Phase 4: Public feed + Google Sheets ---
    if report.status == "executed":
        try:
            from public_feed import format_public_trade_alert, post_to_public_channel, sync_trade_to_sheets
            from trade_tracker import get_recent_trades
            # Get the full trade record for public feed
            trades = get_recent_trades(limit=5, symbol=report.symbol)
            trade = next((t for t in trades if t.get("id") == report.trade_id), None)
            if trade:
                public_msg = format_public_trade_alert(trade, event="opened")
                await post_to_public_channel(public_msg)
                sync_trade_to_sheets(trade)
        except Exception as e:
            logger.error("[%s] Public feed error on trade execution: %s", report.symbol, e)

    return {"status": "ok", "message": "Execution report received"}


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

    # --- Phase 4: Public feed + Google Sheets update ---
    try:
        from public_feed import format_public_trade_alert, post_to_public_channel, update_trade_in_sheets
        from trade_tracker import get_recent_trades
        trades = get_recent_trades(limit=10, symbol=report.symbol)
        trade = next((t for t in trades if t.get("id") == report.trade_id), None)
        if trade and trade.get("status") == "closed":
            event = {
                "full_win": "tp2_hit",
                "partial_win": "tp1_hit",
                "loss": "sl_hit",
            }.get(trade.get("outcome", ""), "closed")
            public_msg = format_public_trade_alert(trade, event=event)
            await post_to_public_channel(public_msg)
            update_trade_in_sheets(trade)

            # --- Post-trade Haiku review (learning loop) ---
            if trade.get("outcome") in ("full_win", "partial_win", "loss"):
                try:
                    from analyzer import post_trade_review
                    from trade_tracker import store_post_trade_review
                    review = await post_trade_review(trade, report.symbol or "UNKNOWN")
                    if review:
                        store_post_trade_review(report.trade_id, report.symbol or "UNKNOWN", review)
                        # Send review with close notification (if not already sent)
                        try:
                            from telegram_bot import send_post_trade_insight
                            await send_post_trade_insight(report.symbol or "UNKNOWN", report.trade_id, review)
                        except Exception:
                            pass  # send_post_trade_insight may not exist yet
                except Exception as e:
                    logger.error("[%s] Post-trade review error: %s", report.symbol, e)
    except Exception as e:
        logger.error("[%s] Public feed error on trade close: %s", report.symbol, e)

    return {"status": "ok", "message": "Close report received"}


# ---------------------------------------------------------------------------
# Backtest endpoints
# ---------------------------------------------------------------------------
@app.post("/backtest/import")
async def backtest_import(
    file: UploadFile = File(...),
    symbol: str = Form("GBPJPY"),
    timeframe: str = Form("M1"),
    resample: bool = Form(True),
):
    """Upload a CSV file with historical OHLC data from MT5."""
    from historical_data import import_csv_to_db, resample_and_store, get_candle_count, get_date_range

    # Save uploaded file temporarily
    upload_dir = Path(os.getenv("DATA_DIR", "/data")) / "history"
    upload_dir.mkdir(parents=True, exist_ok=True)
    filepath = upload_dir / f"{symbol}_{timeframe}_{file.filename}"

    with open(filepath, "wb") as f:
        content = await file.read()
        f.write(content)

    try:
        count = import_csv_to_db(str(filepath), symbol, timeframe)
        if count == 0:
            return JSONResponse(status_code=400, content={"error": "No valid rows found in CSV"})

        result = {"imported": count, "symbol": symbol, "timeframe": timeframe}

        # Resample to higher timeframes
        if resample and timeframe == "M1":
            resampled = resample_and_store(symbol)
            result["resampled"] = resampled

        # Add stats
        result["total_m1_candles"] = get_candle_count(symbol, "M1")
        date_range = get_date_range(symbol, "M1")
        result["date_range"] = {"from": date_range[0], "to": date_range[1]}

        logger.info("Imported %d %s candles for %s", count, timeframe, symbol)
        return result

    except Exception as e:
        logger.error("CSV import failed: %s", e)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/backtest/run")
async def backtest_run_endpoint(request: Request):
    """Run a batch backtest with multiple setups."""
    from backtest import run_backtest
    from models import BacktestRequest

    try:
        body = await request.json()
        req = BacktestRequest(**body)

        setups = [s.model_dump() for s in req.setups]
        result = run_backtest(
            symbol=req.symbol,
            setups=setups,
            kill_zone_end_hour=req.kill_zone_end_hour,
            timezone_offset=req.timezone_offset,
            tp1_close_pct=req.tp1_close_pct,
            notes=req.notes,
        )

        return result
    except Exception as e:
        logger.error("Backtest run failed: %s", e)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/backtest/test")
async def backtest_test_endpoint(request: Request):
    """Test a single hypothetical setup against one date."""
    from backtest import test_setup
    from models import TestSetupRequest

    try:
        body = await request.json()
        req = TestSetupRequest(**body)

        result = test_setup(
            symbol=req.symbol,
            date=req.date,
            bias=req.bias,
            entry_min=req.entry_min,
            entry_max=req.entry_max,
            stop_loss=req.stop_loss,
            tp1=req.tp1,
            tp2=req.tp2,
            sl_pips=req.sl_pips,
            search_start=req.search_start,
            kill_zone_end_hour=req.kill_zone_end_hour,
            timezone_offset=req.timezone_offset,
            tp1_close_pct=req.tp1_close_pct,
            checklist_score=req.checklist_score,
            confidence=req.confidence,
        )

        return result.to_dict()
    except Exception as e:
        logger.error("Setup test failed: %s", e)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/backtest/runs")
async def backtest_runs_list(limit: int = 20):
    """List recent backtest runs."""
    from backtest import get_backtest_runs
    return get_backtest_runs(limit=limit)


@app.get("/backtest/results/{run_id}")
async def backtest_results(run_id: str):
    """Get full results and report for a backtest run."""
    from backtest import get_backtest_run, get_backtest_trades
    from backtest_report import generate_report

    run = get_backtest_run(run_id)
    if not run:
        return JSONResponse(status_code=404, content={"error": "Run not found"})

    trades = get_backtest_trades(run_id)
    report = generate_report(run, trades)

    return {
        "run": run,
        "trades": trades,
        "report": report,
    }


@app.get("/backtest/history_stats")
async def backtest_history_stats(symbol: str = "GBPJPY"):
    """Get stats about available historical data."""
    from historical_data import get_candle_count, get_date_range, get_trading_dates

    m1_count = get_candle_count(symbol, "M1")
    date_range = get_date_range(symbol, "M1")
    trading_dates = get_trading_dates(symbol, "M1")

    return {
        "symbol": symbol,
        "m1_candles": m1_count,
        "date_range": {"from": date_range[0], "to": date_range[1]},
        "trading_days": len(trading_dates),
        "timeframes": {
            tf: get_candle_count(symbol, tf)
            for tf in ["M1", "M5", "H1", "H4", "D1"]
        },
    }


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
# Background tasks — watch expiry + scan deadline + weekly report
# ---------------------------------------------------------------------------
_scan_deadline_alerted_today: set[str] = set()  # prevent duplicate alerts
_weekly_report_sent = False


async def _system_tasks_loop():
    """Background loop: watch expiry, scan deadline check, weekly report."""
    global _weekly_report_sent

    while True:
        try:
            await asyncio.sleep(60)  # Check every minute

            # MEZ = UTC+1 (CET), MESZ = UTC+2 (CEST) — use UTC+1 for simplicity
            now_mez = datetime.now(timezone(timedelta(hours=1)))
            mez_hour = now_mez.hour
            today_str = now_mez.strftime("%Y-%m-%d")

            # --- Watch expiry ---
            for symbol, watch in list(_watch_trades.items()):
                if watch.status != "watching":
                    continue

                profile = get_profile(symbol)
                kill_zone_end = profile.get("kill_zone_end_mez", 11)

                if mez_hour >= kill_zone_end:
                    watch.status = "expired"
                    delete_watch(watch.id)
                    logger.info("[%s] Watch %s expired — Kill Zone ended (%d:00 MEZ)",
                                symbol, watch.id, kill_zone_end)
                    try:
                        from telegram_bot import send_watch_expired
                        await send_watch_expired(watch)
                    except Exception as e:
                        logger.error("[%s] Failed to send watch expiry notification: %s", symbol, e)

            # --- Scan deadline check (per pair, 30 min after kill zone start) ---
            for symbol in config.ACTIVE_PAIRS:
                profile = get_profile(symbol)
                kz_start = profile.get("kill_zone_start_mez", 8)
                # Check 30 min after kill zone start
                deadline_hour = kz_start
                deadline_min_start = 25
                deadline_min_end = 35

                if mez_hour == deadline_hour and deadline_min_start <= now_mez.minute < deadline_min_end:
                    alert_key = f"{symbol}_{today_str}"
                    if alert_key in _scan_deadline_alerted_today:
                        continue
                    last_scan = get_last_scan_for_symbol(symbol)
                    if not last_scan or last_scan["scan_date"] != today_str:
                        _scan_deadline_alerted_today.add(alert_key)
                        logger.warning("[%s] %d:30 MEZ — no scan yet today!", symbol, kz_start)
                        try:
                            from telegram_bot import send_scan_deadline_warning
                            await send_scan_deadline_warning(symbol)
                        except Exception as e:
                            logger.error("[%s] Failed to send deadline warning: %s", symbol, e)

            # --- Reset daily alerts at midnight MEZ ---
            if mez_hour == 0 and now_mez.minute < 5:
                _scan_deadline_alerted_today.clear()

            # --- Weekly report (Sunday 19:00 MEZ) ---
            if now_mez.weekday() == 6 and mez_hour == 19 and now_mez.minute < 5:
                if not _weekly_report_sent:
                    _weekly_report_sent = True
                    try:
                        from telegram_bot import send_weekly_report
                        await send_weekly_report()
                    except Exception as e:
                        logger.error("Failed to send weekly report: %s", e)
            elif now_mez.weekday() != 6 or mez_hour != 19:
                _weekly_report_sent = False

            # --- Monthly PDF report (1st of month, 08:00 MEZ) ---
            if now_mez.day == 1 and mez_hour == 8 and now_mez.minute < 5:
                if not getattr(_system_tasks_loop, "_monthly_report_sent", False):
                    _system_tasks_loop._monthly_report_sent = True
                    # Report for the PREVIOUS month
                    prev = now_mez - timedelta(days=1)
                    try:
                        from monthly_report import send_monthly_report_telegram
                        await send_monthly_report_telegram(prev.year, prev.month)
                        logger.info("Monthly report sent for %d-%02d", prev.year, prev.month)
                    except Exception as e:
                        logger.error("Failed to send monthly report: %s", e)
            elif now_mez.day != 1 or mez_hour != 8:
                _system_tasks_loop._monthly_report_sent = False

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("System tasks loop error: %s", e)


# ---------------------------------------------------------------------------
# Phase 4: Public P&L endpoints (no auth required)
# ---------------------------------------------------------------------------
@app.get("/public/trades")
async def public_trades(limit: int = 50, symbol: str = None):
    """Public trade history — no API key required.
    Shows all executed/closed trades with full transparency."""
    from public_feed import get_public_trade_history
    trades = get_public_trade_history(limit=limit, symbol=symbol)
    return {"trades": trades, "count": len(trades)}


@app.get("/public/stats")
async def public_stats(days: int = 30):
    """Public performance stats — no API key required."""
    from public_feed import get_public_stats
    return get_public_stats(days=days)


@app.get("/public/report/{year}/{month}")
async def public_monthly_report(year: int, month: int):
    """Download the monthly PDF performance report."""
    from monthly_report import generate_monthly_pdf
    from fastapi.responses import Response

    pdf_bytes = generate_monthly_pdf(year, month)
    if not pdf_bytes:
        return JSONResponse(status_code=404, content={"error": "Could not generate report"})

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=AI_Analyst_Report_{year}_{month:02d}.pdf"},
    )


@app.get("/public/feed")
async def public_feed_html():
    """Public HTML page showing all trades — embeddable, shareable."""
    from public_feed import get_public_trade_history, get_public_stats
    from fastapi.responses import HTMLResponse

    trades = get_public_trade_history(limit=100)
    stats = get_public_stats(days=30)

    # Build trade rows
    trade_rows = ""
    for t in trades:
        outcome = t.get("outcome", "open")
        pnl = t.get("pnl_pips", 0) or 0
        pnl_class = "win" if pnl > 0 else ("loss" if pnl < 0 else "open")
        outcome_display = {
            "full_win": "Full Win", "partial_win": "Partial Win",
            "loss": "Loss", "open": "Open", "breakeven": "BE",
        }.get(outcome, outcome)

        trade_rows += f"""
        <tr class="{pnl_class}">
            <td>{(t.get('created_at') or '')[:10]}</td>
            <td><strong>{t.get('symbol', '')}</strong></td>
            <td>{(t.get('bias') or '').upper()}</td>
            <td>{t.get('actual_entry', 0):.3f}</td>
            <td>{t.get('stop_loss', 0):.3f}</td>
            <td>{t.get('tp1', 0):.3f} / {t.get('tp2', 0):.3f}</td>
            <td>{t.get('checklist_score', 'N/A')}</td>
            <td>{(t.get('confidence') or '').upper()}</td>
            <td>{outcome_display}</td>
            <td class="{pnl_class}">{pnl:+.1f}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Trade Analyst - Public P&L</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
               background: #0a0a0f; color: #e0e0e0; padding: 20px; }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        h1 {{ color: #fff; margin-bottom: 5px; }}
        .subtitle {{ color: #888; margin-bottom: 30px; font-size: 14px; }}
        .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
                       gap: 15px; margin-bottom: 30px; }}
        .stat-card {{ background: #151520; border: 1px solid #252530; border-radius: 8px;
                      padding: 15px; text-align: center; }}
        .stat-value {{ font-size: 24px; font-weight: bold; color: #fff; }}
        .stat-label {{ font-size: 12px; color: #888; margin-top: 4px; }}
        .stat-value.positive {{ color: #22c55e; }}
        .stat-value.negative {{ color: #ef4444; }}
        table {{ width: 100%; border-collapse: collapse; background: #151520;
                 border-radius: 8px; overflow: hidden; }}
        th {{ background: #1a1a2e; padding: 12px 8px; text-align: left;
              font-size: 12px; color: #888; text-transform: uppercase; }}
        td {{ padding: 10px 8px; border-bottom: 1px solid #1a1a2e; font-size: 13px; }}
        tr:hover {{ background: #1a1a28; }}
        .win {{ color: #22c55e; }}
        .loss {{ color: #ef4444; }}
        .open {{ color: #3b82f6; }}
        .footer {{ text-align: center; margin-top: 30px; color: #555; font-size: 12px; }}
        .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px;
                  font-size: 11px; font-weight: bold; }}
        .badge-high {{ background: #22c55e22; color: #22c55e; }}
        .badge-medium {{ background: #eab30822; color: #eab308; }}
        .badge-low {{ background: #ef444422; color: #ef4444; }}
        .refresh {{ color: #555; font-size: 12px; margin-bottom: 15px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>AI Trade Analyst &mdash; Public P&L</h1>
        <p class="subtitle">ICT Methodology &bull; AI-Powered Analysis &bull; Full Transparency</p>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-value">{stats.get('total_trades', 0)}</div>
                <div class="stat-label">Trades (30d)</div>
            </div>
            <div class="stat-card">
                <div class="stat-value {'positive' if stats.get('win_rate', 0) >= 55 else ''}">{stats.get('win_rate', 0):.1f}%</div>
                <div class="stat-label">Win Rate</div>
            </div>
            <div class="stat-card">
                <div class="stat-value {'positive' if stats.get('total_pnl_pips', 0) >= 0 else 'negative'}">{stats.get('total_pnl_pips', 0):+.1f}</div>
                <div class="stat-label">Total Pips (30d)</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{stats.get('wins', 0)}/{stats.get('losses', 0)}</div>
                <div class="stat-label">Wins / Losses</div>
            </div>
            <div class="stat-card">
                <div class="stat-value positive">+{stats.get('avg_win_pips', 0):.1f}</div>
                <div class="stat-label">Avg Win (pips)</div>
            </div>
            <div class="stat-card">
                <div class="stat-value negative">{stats.get('avg_loss_pips', 0):.1f}</div>
                <div class="stat-label">Avg Loss (pips)</div>
            </div>
        </div>

        <p class="refresh">Last 100 trades &bull; Auto-updates with each trade</p>
        <table>
            <thead>
                <tr>
                    <th>Date</th><th>Pair</th><th>Bias</th><th>Entry</th>
                    <th>SL</th><th>TP1/TP2</th><th>Checklist</th>
                    <th>Confidence</th><th>Outcome</th><th>P&L Pips</th>
                </tr>
            </thead>
            <tbody>
                {trade_rows if trade_rows else '<tr><td colspan="10" style="text-align:center;color:#555;">No trades yet</td></tr>'}
            </tbody>
        </table>

        <div class="footer">
            <p>Every trade shown &mdash; wins AND losses &bull; No cherry-picking</p>
            <p>Powered by Claude AI &bull; ICT Methodology &bull; Fully Automated</p>
        </div>
    </div>
</body>
</html>"""

    return HTMLResponse(content=html)


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
