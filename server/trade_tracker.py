"""Performance tracker — logs every trade and tracks outcomes.

Uses SQLite for persistence. Tracks:
- Trade setup details (entry, SL, TP, confidence, session)
- Execution status (market fill, limit pending, failed)
- Outcomes (TP1 hit, TP2 hit, SL hit)
- P&L in pips and money
- Per-pair, per-session, per-confidence statistics
"""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Database path — persistent volume in Docker, local in dev
# ---------------------------------------------------------------------------
DB_DIR = os.getenv("DATA_DIR", "/data")
DB_PATH = os.path.join(DB_DIR, "trades.db")

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    bias TEXT NOT NULL,
    confidence TEXT DEFAULT '',
    session TEXT DEFAULT '',

    -- Planned levels
    entry_min REAL DEFAULT 0,
    entry_max REAL DEFAULT 0,
    stop_loss REAL DEFAULT 0,
    tp1 REAL DEFAULT 0,
    tp2 REAL DEFAULT 0,
    sl_pips REAL DEFAULT 0,
    tp1_pips REAL DEFAULT 0,
    tp2_pips REAL DEFAULT 0,
    rr_tp1 REAL DEFAULT 0,
    rr_tp2 REAL DEFAULT 0,

    -- Execution
    status TEXT DEFAULT 'queued',
    actual_entry REAL DEFAULT 0,
    ticket_tp1 INTEGER DEFAULT 0,
    ticket_tp2 INTEGER DEFAULT 0,
    lots_tp1 REAL DEFAULT 0,
    lots_tp2 REAL DEFAULT 0,

    -- Outcomes
    tp1_hit INTEGER DEFAULT 0,
    tp2_hit INTEGER DEFAULT 0,
    sl_hit INTEGER DEFAULT 0,
    close_price_tp1 REAL DEFAULT 0,
    close_price_tp2 REAL DEFAULT 0,
    pnl_pips REAL DEFAULT 0,
    pnl_money REAL DEFAULT 0,
    outcome TEXT DEFAULT 'open',

    -- Timestamps (ISO 8601 UTC)
    created_at TEXT,
    executed_at TEXT,
    closed_at TEXT,

    -- Analysis context
    h1_trend TEXT DEFAULT '',
    counter_trend INTEGER DEFAULT 0,
    market_summary TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_created ON trades(created_at);
"""


# ---------------------------------------------------------------------------
# Database connection
# ---------------------------------------------------------------------------
def _ensure_db_dir():
    """Create database directory if it doesn't exist."""
    os.makedirs(DB_DIR, exist_ok=True)


@contextmanager
def _get_db():
    """Get a database connection with WAL mode for concurrent reads."""
    _ensure_db_dir()
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Initialize the database schema."""
    with _get_db() as conn:
        conn.executescript(_SCHEMA)
    logger.info("Trade tracker database initialized at %s", DB_PATH)


# ---------------------------------------------------------------------------
# Trade lifecycle
# ---------------------------------------------------------------------------
def log_trade_queued(
    trade_id: str,
    symbol: str,
    bias: str,
    entry_min: float,
    entry_max: float,
    stop_loss: float,
    tp1: float,
    tp2: float,
    sl_pips: float,
    confidence: str,
    tp1_pips: float = 0,
    tp2_pips: float = 0,
    rr_tp1: float = 0,
    rr_tp2: float = 0,
    session: str = "",
    h1_trend: str = "",
    counter_trend: bool = False,
    market_summary: str = "",
):
    """Log a trade when the user clicks Execute on Telegram."""
    now = datetime.now(timezone.utc).isoformat()
    with _get_db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO trades
            (id, symbol, bias, confidence, session,
             entry_min, entry_max, stop_loss, tp1, tp2,
             sl_pips, tp1_pips, tp2_pips, rr_tp1, rr_tp2,
             status, created_at, h1_trend, counter_trend, market_summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)""",
            (
                trade_id, symbol, bias, confidence, session,
                entry_min, entry_max, stop_loss, tp1, tp2,
                sl_pips, tp1_pips, tp2_pips, rr_tp1, rr_tp2,
                now, h1_trend, int(counter_trend), market_summary,
            ),
        )
    logger.info("[%s] Trade %s logged as QUEUED", symbol, trade_id)


def log_trade_executed(
    trade_id: str,
    status: str,
    actual_entry: float = 0,
    ticket_tp1: int = 0,
    ticket_tp2: int = 0,
    lots_tp1: float = 0,
    lots_tp2: float = 0,
    error_message: str = "",
):
    """Update trade when MT5 EA confirms execution."""
    now = datetime.now(timezone.utc).isoformat()

    # Map EA status to tracker status
    if status == "executed":
        db_status = "executed"
    elif status == "pending":
        db_status = "pending"  # limit order placed
    elif status == "failed":
        db_status = "failed"
    else:
        db_status = status

    outcome = "open" if db_status in ("executed", "pending") else db_status

    with _get_db() as conn:
        conn.execute(
            """UPDATE trades SET
                status = ?, outcome = ?, actual_entry = ?,
                ticket_tp1 = ?, ticket_tp2 = ?,
                lots_tp1 = ?, lots_tp2 = ?,
                executed_at = ?
            WHERE id = ?""",
            (db_status, outcome, actual_entry,
             ticket_tp1, ticket_tp2, lots_tp1, lots_tp2,
             now, trade_id),
        )
    logger.info("Trade %s updated to %s", trade_id, db_status)


def log_trade_closed(
    trade_id: str,
    ticket: int,
    close_price: float,
    close_reason: str,
    profit: float,
):
    """Update trade when a position closes (TP1/TP2/SL hit).

    close_reason: "tp1", "tp2", "sl", "manual", "cancelled"
    profit: monetary P&L for this specific ticket
    """
    now = datetime.now(timezone.utc).isoformat()

    with _get_db() as conn:
        trade = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        if not trade:
            logger.warning("Trade %s not found for close update", trade_id)
            return

        updates = {"pnl_money": (trade["pnl_money"] or 0) + profit}

        if close_reason == "tp1":
            updates["tp1_hit"] = 1
            updates["close_price_tp1"] = close_price
        elif close_reason == "tp2":
            updates["tp2_hit"] = 1
            updates["close_price_tp2"] = close_price
        elif close_reason == "sl":
            updates["sl_hit"] = 1
        elif close_reason == "cancelled":
            pass

        # Determine if trade is fully closed
        tp1_hit = updates.get("tp1_hit", trade["tp1_hit"])
        tp2_hit = updates.get("tp2_hit", trade["tp2_hit"])
        sl_hit = updates.get("sl_hit", trade["sl_hit"])

        # Trade is closed if SL hit OR both TPs resolved
        is_closed = sl_hit or (tp1_hit and tp2_hit)

        if close_reason == "cancelled":
            is_closed = True

        if is_closed:
            # Calculate pip P&L
            entry = trade["actual_entry"] or ((trade["entry_min"] + trade["entry_max"]) / 2)
            if sl_hit and not tp1_hit and not tp2_hit:
                updates["pnl_pips"] = -trade["sl_pips"]
                updates["outcome"] = "loss"
            elif tp1_hit and tp2_hit:
                updates["pnl_pips"] = trade["tp1_pips"] + trade["tp2_pips"]
                updates["outcome"] = "full_win"
            elif tp1_hit and sl_hit:
                # TP1 hit then SL hit on runner — partial win
                updates["pnl_pips"] = trade["tp1_pips"] - trade["sl_pips"]
                updates["outcome"] = "partial_win"
            elif close_reason == "cancelled":
                updates["pnl_pips"] = 0
                updates["outcome"] = "cancelled"
            else:
                updates["outcome"] = "closed"

            updates["closed_at"] = now
            updates["status"] = "closed"

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [trade_id]
        conn.execute(f"UPDATE trades SET {set_clause} WHERE id = ?", values)

    logger.info("Trade %s: %s (profit=%.2f)", trade_id, close_reason, profit)


# ---------------------------------------------------------------------------
# Statistics queries
# ---------------------------------------------------------------------------
def get_stats(
    symbol: Optional[str] = None,
    days: int = 30,
) -> dict:
    """Get performance statistics."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    with _get_db() as conn:
        where = "WHERE created_at >= ?"
        params: list = [cutoff]

        if symbol:
            where += " AND symbol = ?"
            params.append(symbol)

        # Overall stats
        rows = conn.execute(
            f"SELECT * FROM trades {where} ORDER BY created_at DESC", params
        ).fetchall()

        if not rows:
            return {
                "period_days": days,
                "symbol": symbol or "ALL",
                "total_trades": 0,
                "message": "No trades in this period.",
            }

        total = len(rows)
        closed = [r for r in rows if r["status"] == "closed"]
        open_trades = [r for r in rows if r["outcome"] == "open"]
        failed = [r for r in rows if r["status"] == "failed"]
        cancelled = [r for r in rows if r["outcome"] == "cancelled"]

        wins = [r for r in closed if r["outcome"] in ("full_win", "partial_win")]
        losses = [r for r in closed if r["outcome"] == "loss"]
        full_wins = [r for r in closed if r["outcome"] == "full_win"]
        partial_wins = [r for r in closed if r["outcome"] == "partial_win"]

        total_closed = len(closed)
        win_rate = (len(wins) / total_closed * 100) if total_closed else 0

        total_pnl_pips = sum(r["pnl_pips"] or 0 for r in closed)
        total_pnl_money = sum(r["pnl_money"] or 0 for r in closed)
        avg_win_pips = (
            sum(r["pnl_pips"] or 0 for r in wins) / len(wins)
        ) if wins else 0
        avg_loss_pips = (
            sum(r["pnl_pips"] or 0 for r in losses) / len(losses)
        ) if losses else 0

        # Per-pair breakdown
        pair_stats = {}
        symbols_seen = set(r["symbol"] for r in rows)
        for sym in sorted(symbols_seen):
            sym_closed = [r for r in closed if r["symbol"] == sym]
            sym_wins = [r for r in sym_closed if r["outcome"] in ("full_win", "partial_win")]
            sym_total = len(sym_closed)
            pair_stats[sym] = {
                "total": len([r for r in rows if r["symbol"] == sym]),
                "closed": sym_total,
                "wins": len(sym_wins),
                "win_rate": (len(sym_wins) / sym_total * 100) if sym_total else 0,
                "pnl_pips": sum(r["pnl_pips"] or 0 for r in sym_closed),
                "pnl_money": sum(r["pnl_money"] or 0 for r in sym_closed),
            }

        # Per-confidence breakdown
        conf_stats = {}
        for conf in ("high", "medium", "low"):
            conf_closed = [r for r in closed if r["confidence"] == conf]
            conf_wins = [r for r in conf_closed if r["outcome"] in ("full_win", "partial_win")]
            conf_total = len(conf_closed)
            if conf_total > 0:
                conf_stats[conf] = {
                    "total": conf_total,
                    "wins": len(conf_wins),
                    "win_rate": len(conf_wins) / conf_total * 100,
                }

        # Per-session breakdown
        session_stats = {}
        for sess in ("London", "NY", "Manual"):
            sess_closed = [r for r in closed if r["session"] == sess]
            sess_wins = [r for r in sess_closed if r["outcome"] in ("full_win", "partial_win")]
            sess_total = len(sess_closed)
            if sess_total > 0:
                session_stats[sess] = {
                    "total": sess_total,
                    "wins": len(sess_wins),
                    "win_rate": len(sess_wins) / sess_total * 100,
                }

        return {
            "period_days": days,
            "symbol": symbol or "ALL",
            "total_trades": total,
            "open_trades": len(open_trades),
            "closed_trades": total_closed,
            "failed_trades": len(failed),
            "cancelled_trades": len(cancelled),
            "wins": len(wins),
            "full_wins": len(full_wins),
            "partial_wins": len(partial_wins),
            "losses": len(losses),
            "win_rate": win_rate,
            "total_pnl_pips": total_pnl_pips,
            "total_pnl_money": total_pnl_money,
            "avg_win_pips": avg_win_pips,
            "avg_loss_pips": avg_loss_pips,
            "pair_stats": pair_stats,
            "confidence_stats": conf_stats,
            "session_stats": session_stats,
        }


def get_recent_trades(limit: int = 10, symbol: Optional[str] = None) -> list[dict]:
    """Get recent trades for display."""
    with _get_db() as conn:
        if symbol:
            rows = conn.execute(
                "SELECT * FROM trades WHERE symbol = ? ORDER BY created_at DESC LIMIT ?",
                (symbol, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

        return [dict(r) for r in rows]


def get_open_trades() -> list[dict]:
    """Get all currently open trades (for monitoring)."""
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM trades WHERE outcome = 'open' AND status IN ('executed', 'pending')"
        ).fetchall()
        return [dict(r) for r in rows]
