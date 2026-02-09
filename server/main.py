from __future__ import annotations

import asyncio
import json
import logging

from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import config
from analyzer import analyze_charts
from models import AnalysisResult, MarketData, PendingTrade, TradeExecutionReport
from pair_profiles import get_profile
from telegram_bot import (
    create_bot_app,
    get_bot_app,
    send_analysis,
    send_trade_confirmation,
    set_scan_callback,
    store_analysis,
)
from trade_tracker import init_db, log_trade_executed, log_trade_closed, get_stats as get_trade_stats

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


def queue_pending_trade(trade: PendingTrade):
    """Called by telegram_bot when Execute is pressed."""
    _pending_trades[trade.symbol] = trade
    logger.info("[%s] Trade queued for MT5: %s %s", trade.symbol, trade.bias.upper(), trade.id)


def get_pending_trade(symbol: str) -> Optional[PendingTrade]:
    """Return current pending trade for symbol (or None)."""
    return _pending_trades.get(symbol)


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
            screenshots.get("h1", b""),
            screenshots.get("m15", b""),
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
    h1: bytes, m15: bytes, m5: bytes, market_data: MarketData
):
    """Run analysis pipeline and send results via Telegram."""
    symbol = market_data.symbol

    async with _analysis_lock:
        logger.info("[%s] Starting analysis pipeline...", symbol)
        result = await analyze_charts(h1, m15, m5, market_data)
        _last_results[symbol] = result
        store_analysis(result)
        logger.info(
            "[%s] Analysis complete: %d setups found", symbol, len(result.setups)
        )

        try:
            await send_analysis(result)
            logger.info("[%s] Telegram notifications sent", symbol)
        except Exception as e:
            logger.error("[%s] Failed to send Telegram notifications: %s", symbol, e)


# ---------------------------------------------------------------------------
# Lifespan — start / stop Telegram bot alongside FastAPI
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting AI Trade Analyst server on %s:%s", config.HOST, config.PORT)
    init_db()
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

    yield

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
    version="2.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "pairs_analyzed": list(_last_results.keys()),
        "pending_trades": list(_pending_trades.keys()),
        "setups": {s: len(r.setups) for s, r in _last_results.items()},
    }


@app.get("/stats")
async def stats(symbol: str = "", days: int = 30):
    """Performance statistics endpoint."""
    return get_trade_stats(symbol=symbol or None, days=days)


@app.post("/analyze")
async def analyze(
    request: Request,
    screenshot_h1: UploadFile = File(...),
    screenshot_m15: UploadFile = File(...),
    screenshot_m5: UploadFile = File(...),
    market_data: str = Form(...),
):
    """Receive screenshots and market data from MT5 EA, trigger analysis."""
    logger.info(
        "Received analysis request — files: h1=%s, m15=%s, m5=%s",
        screenshot_h1.filename,
        screenshot_m15.filename,
        screenshot_m5.filename,
    )

    h1_bytes = await screenshot_h1.read()
    m15_bytes = await screenshot_m15.read()
    m5_bytes = await screenshot_m5.read()

    logger.info(
        "Screenshot sizes: H1=%d, M15=%d, M5=%d bytes",
        len(h1_bytes),
        len(m15_bytes),
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
    _last_screenshots[symbol] = {"h1": h1_bytes, "m15": m15_bytes, "m5": m5_bytes}
    _last_market_data[symbol] = md

    asyncio.create_task(_run_analysis(h1_bytes, m15_bytes, m5_bytes, md))

    return {"status": "accepted", "symbol": symbol, "message": "Analysis started"}


@app.get("/scan")
async def manual_scan(symbol: str = ""):
    """Manual trigger endpoint."""
    target = symbol or (list(_last_screenshots.keys())[0] if _last_screenshots else "")

    if target and target in _last_screenshots and target in _last_market_data:
        screenshots = _last_screenshots[target]
        asyncio.create_task(
            _run_analysis(
                screenshots.get("h1", b""),
                screenshots.get("m15", b""),
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
    Returns the trade and immediately clears it (consume-on-read)."""
    trade = get_pending_trade(symbol)
    if trade:
        clear_pending_trade(symbol)
        logger.info("[%s] Pending trade consumed by MT5: %s", symbol, trade.id)
        return {"pending": True, "trade": trade.model_dump()}
    return {"pending": False}


@app.post("/trade_executed")
async def trade_executed(report: TradeExecutionReport):
    """MT5 EA calls this after placing a trade."""
    logger.info(
        "[%s] Trade execution report: id=%s status=%s",
        report.symbol,
        report.trade_id,
        report.status,
    )
    clear_pending_trade(report.symbol)

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
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=config.HOST,
        port=config.PORT,
        log_level=config.LOG_LEVEL.lower(),
    )
