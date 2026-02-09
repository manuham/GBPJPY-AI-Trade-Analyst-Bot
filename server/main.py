from __future__ import annotations

import asyncio
import json
import logging

from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

import config
from analyzer import analyze_charts
from models import AnalysisResult, MarketData, PendingTrade, TradeExecutionReport
from telegram_bot import (
    create_bot_app,
    get_bot_app,
    send_analysis,
    send_trade_confirmation,
    set_scan_callback,
    store_analysis,
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
# In-memory storage
# ---------------------------------------------------------------------------
_last_screenshots: dict[str, bytes] = {}
_last_market_data: Optional[MarketData] = None
_last_result: Optional[AnalysisResult] = None
_analysis_lock = asyncio.Lock()

# Trade execution queue — one pending trade at a time
_pending_trade: Optional[PendingTrade] = None


def queue_pending_trade(trade: PendingTrade):
    """Called by telegram_bot when Execute is pressed."""
    global _pending_trade
    _pending_trade = trade
    logger.info("Trade queued for MT5: %s %s", trade.bias.upper(), trade.id)


def get_pending_trade() -> Optional[PendingTrade]:
    """Return current pending trade (or None)."""
    return _pending_trade


def clear_pending_trade():
    """Remove the pending trade after MT5 picks it up."""
    global _pending_trade
    _pending_trade = None


async def _run_scan_from_telegram():
    """Callback invoked by the /scan Telegram command."""
    if _last_screenshots and _last_market_data:
        await _run_analysis(
            _last_screenshots.get("h1", b""),
            _last_screenshots.get("m15", b""),
            _last_screenshots.get("m5", b""),
            _last_market_data,
        )
    elif _last_result:
        await send_analysis(_last_result)
    else:
        raise RuntimeError(
            "No screenshots available. Trigger a scan from MT5 first."
        )


async def _run_analysis(
    h1: bytes, m15: bytes, m5: bytes, market_data: MarketData
):
    """Run analysis pipeline and send results via Telegram."""
    global _last_result

    async with _analysis_lock:
        logger.info("Starting analysis pipeline...")
        result = await analyze_charts(h1, m15, m5, market_data)
        _last_result = result
        store_analysis(result)
        logger.info(
            "Analysis complete: %d setups found", len(result.setups)
        )

        try:
            await send_analysis(result)
            logger.info("Telegram notifications sent")
        except Exception as e:
            logger.error("Failed to send Telegram notifications: %s", e)


# ---------------------------------------------------------------------------
# Lifespan — start / stop Telegram bot alongside FastAPI
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting GBPJPY Analyst server on %s:%s", config.HOST, config.PORT)
    try:
        bot_app = create_bot_app()
        set_scan_callback(_run_scan_from_telegram)
        # Give telegram_bot access to the trade queue
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
    title="GBPJPY AI Trade Analyst",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "last_analysis": _last_result is not None,
        "setups_count": len(_last_result.setups) if _last_result else 0,
    }


@app.post("/analyze")
async def analyze(
    request: Request,
    screenshot_h1: UploadFile = File(...),
    screenshot_m15: UploadFile = File(...),
    screenshot_m5: UploadFile = File(...),
    market_data: str = Form(...),
):
    """Receive screenshots and market data from MT5 EA, trigger analysis."""
    global _last_screenshots, _last_market_data

    logger.info(
        "Received analysis request — files: h1=%s, m15=%s, m5=%s",
        screenshot_h1.filename,
        screenshot_m15.filename,
        screenshot_m5.filename,
    )

    # Read screenshot bytes
    h1_bytes = await screenshot_h1.read()
    m15_bytes = await screenshot_m15.read()
    m5_bytes = await screenshot_m5.read()

    logger.info(
        "Screenshot sizes: H1=%d, M15=%d, M5=%d bytes",
        len(h1_bytes),
        len(m15_bytes),
        len(m5_bytes),
    )

    # Parse market data JSON
    try:
        md_dict = json.loads(market_data)
        md = MarketData(**md_dict)
    except Exception as e:
        logger.error("Failed to parse market data: %s", e)
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid market data JSON: {e}"},
        )

    # Store for later re-use (e.g. /scan command)
    _last_screenshots = {"h1": h1_bytes, "m15": m15_bytes, "m5": m5_bytes}
    _last_market_data = md

    # Fire-and-forget: run analysis in background
    asyncio.create_task(_run_analysis(h1_bytes, m15_bytes, m5_bytes, md))

    return {"status": "accepted", "message": "Analysis started"}


@app.get("/scan")
async def manual_scan():
    """Manual trigger endpoint."""
    if _last_screenshots and _last_market_data:
        asyncio.create_task(
            _run_analysis(
                _last_screenshots.get("h1", b""),
                _last_screenshots.get("m15", b""),
                _last_screenshots.get("m5", b""),
                _last_market_data,
            )
        )
        return {"status": "accepted", "message": "Re-analysis started"}

    if _last_result:
        return {
            "status": "cached",
            "message": "Returning last analysis",
            "setups": len(_last_result.setups),
        }

    return JSONResponse(
        status_code=404,
        content={"error": "No data available. Send screenshots from MT5 first."},
    )


@app.get("/pending_trade")
async def pending_trade():
    """MT5 EA polls this to check for trades to execute.
    Returns the trade and immediately clears it (consume-on-read)
    to prevent duplicate execution."""
    trade = get_pending_trade()
    if trade:
        clear_pending_trade()  # Clear immediately so next poll returns empty
        logger.info("Pending trade consumed by MT5: %s", trade.id)
        return {"pending": True, "trade": trade.model_dump()}
    return {"pending": False}


@app.post("/trade_executed")
async def trade_executed(report: TradeExecutionReport):
    """MT5 EA calls this after placing a trade."""
    logger.info(
        "Trade execution report: id=%s status=%s",
        report.trade_id,
        report.status,
    )
    clear_pending_trade()

    # Send confirmation to Telegram
    try:
        await send_trade_confirmation(report)
        logger.info("Trade confirmation sent to Telegram")
    except Exception as e:
        logger.error("Failed to send trade confirmation: %s", e)

    return {"status": "ok", "message": "Execution report received"}


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
