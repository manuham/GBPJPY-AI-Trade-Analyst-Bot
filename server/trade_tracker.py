# v2.0 — H4 timeframe + ICT criteria
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
        # Migrations: add columns that may not exist yet
        _migrations = [
            "ALTER TABLE trades ADD COLUMN raw_response TEXT DEFAULT ''",
            "ALTER TABLE trades ADD COLUMN trend_alignment TEXT DEFAULT ''",
            "ALTER TABLE trades ADD COLUMN d1_trend TEXT DEFAULT ''",
            "ALTER TABLE trades ADD COLUMN entry_status TEXT DEFAULT ''",
            "ALTER TABLE trades ADD COLUMN entry_distance_pips REAL DEFAULT 0",
            "ALTER TABLE trades ADD COLUMN negative_factors TEXT DEFAULT ''",
            "ALTER TABLE trades ADD COLUMN price_zone TEXT DEFAULT ''",
            "ALTER TABLE trades ADD COLUMN h4_trend TEXT DEFAULT ''",
            "ALTER TABLE trades ADD COLUMN checklist_score TEXT DEFAULT ''",
        ]
        for migration in _migrations:
            try:
                conn.execute(migration)
            except sqlite3.OperationalError:
                pass  # Column already exists
    # --- scan_metadata table ---
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS scan_metadata (
            symbol TEXT PRIMARY KEY,
            last_scan_time TEXT,
            scan_date TEXT
        );
        CREATE TABLE IF NOT EXISTS watch_trades_persist (
            id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            watch_json TEXT NOT NULL,
            status TEXT DEFAULT 'watching',
            created_at TEXT
        );
    """)
    logger.info("Trade tracker database initialized at %s", DB_PATH)


# ---------------------------------------------------------------------------
# Scan metadata — track when last scan happened per symbol
# ---------------------------------------------------------------------------
def log_scan_completed(symbol: str):
    """Record that today's scan completed for this symbol."""
    now = datetime.now(timezone.utc)
    with _get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO scan_metadata (symbol, last_scan_time, scan_date) VALUES (?, ?, ?)",
            (symbol, now.isoformat(), now.strftime("%Y-%m-%d")),
        )
    logger.info("[%s] Scan recorded at %s", symbol, now.isoformat())


def get_last_scan_for_symbol(symbol: str) -> Optional[dict]:
    """Return last scan info for symbol, or None."""
    with _get_db() as conn:
        row = conn.execute(
            "SELECT last_scan_time, scan_date FROM scan_metadata WHERE symbol = ?",
            (symbol,),
        ).fetchone()
        if row:
            return {"last_scan_time": row["last_scan_time"], "scan_date": row["scan_date"]}
    return None


# ---------------------------------------------------------------------------
# Persistent watch trades — survive Docker restarts
# ---------------------------------------------------------------------------
def persist_watch(watch_id: str, symbol: str, watch_json: str, status: str = "watching"):
    """Save or update a watch trade in the database."""
    now = datetime.now(timezone.utc).isoformat()
    with _get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO watch_trades_persist (id, symbol, watch_json, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (watch_id, symbol, watch_json, status, now),
        )
    logger.info("[%s] Watch %s persisted (status=%s)", symbol, watch_id, status)


def load_active_watches() -> list[dict]:
    """Load all active watches from the database."""
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT watch_json FROM watch_trades_persist WHERE status = 'watching'"
        ).fetchall()
        return [{"watch_json": row["watch_json"]} for row in rows]


def delete_watch(watch_id: str):
    """Remove a watch from persistence after expiry/rejection/confirmation."""
    with _get_db() as conn:
        conn.execute("DELETE FROM watch_trades_persist WHERE id = ?", (watch_id,))
    logger.debug("Watch %s removed from persistence", watch_id)


def update_watch_status(watch_id: str, status: str):
    """Update the status of a persisted watch."""
    with _get_db() as conn:
        conn.execute(
            "UPDATE watch_trades_persist SET status = ? WHERE id = ?",
            (status, watch_id),
        )


# ---------------------------------------------------------------------------
# Weekly performance report
# ---------------------------------------------------------------------------
def get_weekly_performance_report(symbol: Optional[str] = None) -> dict:
    """Get detailed win rate breakdown for the last 7 days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    with _get_db() as conn:
        where = "WHERE created_at >= ? AND status = 'closed'"
        params: list = [cutoff]
        if symbol:
            where += " AND symbol = ?"
            params.append(symbol)

        rows = conn.execute(f"SELECT * FROM trades {where}", params).fetchall()

    if not rows:
        return {"total": 0, "message": "No closed trades in the last 7 days."}

    trades = [dict(r) for r in rows]
    total = len(trades)
    wins = [t for t in trades if t["outcome"] in ("full_win", "partial_win")]
    losses = [t for t in trades if t["outcome"] == "loss"]
    total_pnl = sum(t.get("pnl_pips") or 0 for t in trades)

    def _bucket_stats(key_fn):
        buckets: dict[str, dict] = {}
        for t in trades:
            bucket = key_fn(t)
            if not bucket:
                continue
            if bucket not in buckets:
                buckets[bucket] = {"wins": 0, "total": 0, "pnl_pips": 0}
            buckets[bucket]["total"] += 1
            buckets[bucket]["pnl_pips"] += t.get("pnl_pips") or 0
            if t["outcome"] in ("full_win", "partial_win"):
                buckets[bucket]["wins"] += 1
        # Calculate win rates
        for b in buckets.values():
            b["win_rate"] = (b["wins"] / b["total"] * 100) if b["total"] else 0
        return buckets

    def _checklist_bucket(t):
        score_str = t.get("checklist_score", "")
        if "/" not in score_str:
            return None
        try:
            score = int(score_str.split("/")[0])
        except ValueError:
            return None
        if score >= 10:
            return "10-12"
        elif score >= 7:
            return "7-9"
        elif score >= 4:
            return "4-6"
        else:
            return "0-3"

    return {
        "total": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / total * 100) if total else 0,
        "total_pnl_pips": total_pnl,
        "by_checklist": _bucket_stats(_checklist_bucket),
        "by_confidence": _bucket_stats(lambda t: t.get("confidence", "")),
        "by_entry_status": _bucket_stats(lambda t: t.get("entry_status", "")),
        "by_trend_alignment": _bucket_stats(lambda t: (t.get("trend_alignment", "") or "")[:3]),  # e.g., "4/4"
        "by_price_zone": _bucket_stats(lambda t: t.get("price_zone", "")),
        "by_bias": _bucket_stats(lambda t: t.get("bias", "")),
    }


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
    raw_response: str = "",
    trend_alignment: str = "",
    d1_trend: str = "",
    entry_status: str = "",
    entry_distance_pips: float = 0,
    negative_factors: str = "",
    price_zone: str = "",
    h4_trend: str = "",
    checklist_score: str = "",
):
    """Log a trade when the user clicks Execute on Telegram."""
    now = datetime.now(timezone.utc).isoformat()
    with _get_db() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO trades
            (id, symbol, bias, confidence, session,
             entry_min, entry_max, stop_loss, tp1, tp2,
             sl_pips, tp1_pips, tp2_pips, rr_tp1, rr_tp2,
             status, created_at, h1_trend, counter_trend, market_summary,
             raw_response, trend_alignment, d1_trend, entry_status,
             entry_distance_pips, negative_factors, price_zone,
             h4_trend, checklist_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trade_id, symbol, bias, confidence, session,
                entry_min, entry_max, stop_loss, tp1, tp2,
                sl_pips, tp1_pips, tp2_pips, rr_tp1, rr_tp2,
                now, h1_trend, int(counter_trend), market_summary,
                raw_response, trend_alignment, d1_trend, entry_status,
                entry_distance_pips, negative_factors, price_zone,
                h4_trend, checklist_score,
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
                # After TP1, EA moves runner SL to breakeven, so runner loss = 0 pips
                # Net P&L = TP1 profit only (runner closed at entry = 0 pips)
                updates["pnl_pips"] = trade["tp1_pips"]
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


# ---------------------------------------------------------------------------
# Risk management queries
# ---------------------------------------------------------------------------
def get_daily_pnl() -> dict:
    """Get today's realized P&L from closed trades (for FTMO drawdown check)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _get_db() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl_money), 0) as total_pnl, COUNT(*) as count "
            "FROM trades WHERE closed_at LIKE ? AND status = 'closed'",
            (f"{today}%",),
        ).fetchone()
        return {
            "daily_pnl": row["total_pnl"],
            "closed_trades_today": row["count"],
        }


def get_open_currency_exposure() -> dict[str, list[str]]:
    """Get currently exposed currencies from open trades.

    For each currency, returns list of exposures like:
      {"GBP": ["GBPJPY:long_GBP", "GBPUSD:long_GBP"],
       "JPY": ["GBPJPY:short_JPY"]}

    Long GBPJPY = long GBP + short JPY
    Short GBPJPY = short GBP + long JPY
    """
    open_trades = get_open_trades()
    exposure: dict[str, list[str]] = {}
    for t in open_trades:
        symbol = t["symbol"]
        bias = t["bias"]
        base = symbol[:3]
        quote = symbol[3:]

        if bias == "long":
            exposure.setdefault(base, []).append(f"{symbol}:long_{base}")
            exposure.setdefault(quote, []).append(f"{symbol}:short_{quote}")
        else:
            exposure.setdefault(base, []).append(f"{symbol}:short_{base}")
            exposure.setdefault(quote, []).append(f"{symbol}:long_{quote}")

    return exposure


def check_correlation_conflict(symbol: str, bias: str) -> Optional[str]:
    """Check if a new trade would create dangerous currency correlation.

    Only flags when a DIFFERENT pair creates overlapping currency exposure.
    Same-pair conflicts are not correlation — that's just adding to the same position.

    Returns warning message if conflict found, None if safe.
    """
    exposure = get_open_currency_exposure()
    base = symbol[:3]
    quote = symbol[3:]

    # Determine what this new trade would add
    if bias == "long":
        new_base_dir = "long"
        new_quote_dir = "short"
    else:
        new_base_dir = "short"
        new_quote_dir = "long"

    conflicts = []

    # Check base currency — only from DIFFERENT pairs
    for existing in exposure.get(base, []):
        existing_symbol = existing.split(":")[0]
        if existing_symbol == symbol:
            continue  # Skip same pair — not a correlation issue
        existing_dir = existing.split(":")[1].split("_")[0]  # "long" or "short"
        if existing_dir == new_base_dir:
            conflicts.append(f"{base} already {new_base_dir} via {existing_symbol}")

    # Check quote currency — only from DIFFERENT pairs
    for existing in exposure.get(quote, []):
        existing_symbol = existing.split(":")[0]
        if existing_symbol == symbol:
            continue  # Skip same pair
        existing_dir = existing.split(":")[1].split("_")[0]
        if existing_dir == new_quote_dir:
            conflicts.append(f"{quote} already {new_quote_dir} via {existing_symbol}")

    if conflicts:
        return "Correlation risk: " + "; ".join(conflicts)
    return None


def cleanup_stale_open_trades(max_age_hours: int = 24):
    """Mark old 'open' trades as closed if they've been open too long.

    This handles cases where MT5 EA didn't report a close (manual close,
    restart, etc.). Trades older than max_age_hours are assumed closed.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
    with _get_db() as conn:
        result = conn.execute(
            "UPDATE trades SET status = 'closed', outcome = 'closed', "
            "closed_at = ? WHERE outcome = 'open' AND created_at < ?",
            (datetime.now(timezone.utc).isoformat(), cutoff),
        )
        if result.rowcount > 0:
            logger.info("Cleaned up %d stale open trades (older than %dh)",
                        result.rowcount, max_age_hours)


def force_close_all_open_trades() -> int:
    """Force-close ALL open trades in the DB. Used when MT5 positions were
    closed manually but the EA didn't report it. Returns count of closed trades."""
    now = datetime.now(timezone.utc).isoformat()
    with _get_db() as conn:
        result = conn.execute(
            "UPDATE trades SET status = 'closed', outcome = 'closed', "
            "closed_at = ? WHERE outcome = 'open'",
            (now,),
        )
        count = result.rowcount
        if count > 0:
            logger.info("Force-closed %d open trades", count)
        return count


def get_recent_closed_for_pair(symbol: str, limit: int = 10) -> list[dict]:
    """Get last N closed trades for a specific pair (for AI performance feedback)."""
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT id, bias, confidence, outcome, pnl_pips, pnl_money, "
            "sl_pips, tp1_pips, tp2_pips, h1_trend, counter_trend, "
            "trend_alignment, d1_trend, h4_trend, entry_status, entry_distance_pips, "
            "negative_factors, price_zone, checklist_score, "
            "created_at, closed_at "
            "FROM trades WHERE symbol = ? AND status = 'closed' "
            "ORDER BY closed_at DESC LIMIT ?",
            (symbol, limit),
        ).fetchall()
        return [dict(r) for r in rows]
