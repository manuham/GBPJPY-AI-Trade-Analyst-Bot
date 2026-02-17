# GBPJPY AI Trade Analyst — Streamlit Dashboard
"""Web dashboard for monitoring live trades, performance analytics,
trade journal, backtest results, and risk management."""

import os
import sys
import time
from datetime import datetime, timezone, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

# ---------------------------------------------------------------------------
# Make server modules importable (copied into container at /app)
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(__file__))

# Lazy imports — these share the /data volume with the main server
import trade_tracker
import backtest as bt
import backtest_report as bt_report
import historical_data as hd

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
API_BASE = os.getenv("API_BASE_URL", "http://ai-analyst:8000")
API_KEY = os.getenv("API_KEY", "")
REFRESH_INTERVAL = 30  # seconds for live monitor

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="GBPJPY AI Trade Analyst",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _api_get(path: str, params: dict = None) -> dict:
    """Call the FastAPI server."""
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    try:
        r = requests.get(f"{API_BASE}{path}", headers=headers, params=params, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------
st.sidebar.title("📈 GBPJPY AI Analyst")
st.sidebar.markdown("---")

page = st.sidebar.radio(
    "Navigation",
    ["🟢 Live Monitor", "📊 Performance", "📒 Trade Journal",
     "🔬 Backtest Explorer", "⚠️ Risk Panel", "📋 Analysis Stats"],
    label_visibility="collapsed",
)

st.sidebar.markdown("---")
st.sidebar.caption("v3.0 — ICT Methodology")
st.sidebar.caption(f"Updated: {datetime.now().strftime('%H:%M:%S')}")


# ============================================================================
# PAGE 1: Live Monitor
# ============================================================================
if page == "🟢 Live Monitor":
    st.title("🟢 Live Monitor")

    # Auto-refresh checkbox
    auto_refresh = st.checkbox("Auto-refresh (30s)", value=False)

    # Fetch health data
    health = _api_get("/health")

    if "error" in health:
        st.error(f"❌ Server unreachable: {health['error']}")
    else:
        # Status indicator
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Server Status", "🟢 Online")
        with col2:
            watches = health.get("watch_trades", {})
            st.metric("Active Watches", len(watches))
        with col3:
            pending = health.get("pending_trades", {})
            st.metric("Pending Trades", len(pending))
        with col4:
            setups = health.get("latest_setups", {})
            st.metric("Cached Setups", len(setups))

        # Active watches
        st.subheader("Active Watch Trades")
        if watches:
            watch_data = []
            for symbol, w in watches.items():
                watch_data.append({
                    "Symbol": symbol,
                    "Bias": w.get("bias", "").upper(),
                    "Entry Zone": f"{w.get('entry_min', 0):.3f} - {w.get('entry_max', 0):.3f}",
                    "SL": f"{w.get('stop_loss', 0):.3f}",
                    "TP1": f"{w.get('tp1', 0):.3f}",
                    "TP2": f"{w.get('tp2', 0):.3f}",
                    "Confidence": w.get("confidence", ""),
                    "Checklist": w.get("checklist_score", ""),
                    "Confirmations": f"{w.get('confirmations_used', 0)}/{w.get('max_confirmations', 10)}",
                    "Status": w.get("status", ""),
                })
            st.dataframe(pd.DataFrame(watch_data), use_container_width=True, hide_index=True)
        else:
            st.info("No active watch trades.")

        # Pending trades
        st.subheader("Pending Trades (Waiting for MT5)")
        if pending:
            pending_data = []
            for symbol, p in pending.items():
                pending_data.append({
                    "Symbol": symbol,
                    "Bias": p.get("bias", "").upper(),
                    "Entry Zone": f"{p.get('entry_min', 0):.3f} - {p.get('entry_max', 0):.3f}",
                    "Confidence": p.get("confidence", ""),
                })
            st.dataframe(pd.DataFrame(pending_data), use_container_width=True, hide_index=True)
        else:
            st.info("No pending trades.")

    # Auto-refresh logic
    if auto_refresh:
        time.sleep(REFRESH_INTERVAL)
        st.rerun()


# ============================================================================
# PAGE 2: Performance
# ============================================================================
elif page == "📊 Performance":
    st.title("📊 Performance Dashboard")

    # Time period selector
    col1, col2 = st.columns([1, 4])
    with col1:
        days = st.selectbox("Period", [7, 14, 30, 90], index=2)

    stats = trade_tracker.get_stats(days=days)

    if stats.get("total_trades", 0) == 0:
        st.info(f"No trades in the last {days} days.")
    else:
        # KPI cards
        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            st.metric("Win Rate", f"{stats.get('win_rate', 0):.1f}%")
        with c2:
            pnl = stats.get("total_pnl_pips", 0)
            st.metric("Total P&L", f"{pnl:+.1f} pips")
        with c3:
            wins = stats.get("wins", 0)
            losses = stats.get("losses", 0)
            st.metric("W/L", f"{wins}/{losses}")
        with c4:
            avg_win = stats.get("avg_win_pips", 0)
            st.metric("Avg Win", f"{avg_win:+.1f} pips")
        with c5:
            avg_loss = stats.get("avg_loss_pips", 0)
            st.metric("Avg Loss", f"{avg_loss:.1f} pips")

        st.markdown("---")

        # Equity curve from recent trades
        st.subheader("Equity Curve")
        trades = trade_tracker.get_recent_trades(limit=200)
        closed = [t for t in trades if t.get("status") == "closed" and t.get("pnl_pips") is not None]

        if closed:
            # Sort by closed_at
            closed.sort(key=lambda t: t.get("closed_at", "") or "")
            cumulative = 0.0
            eq_data = []
            for t in closed:
                cumulative += t.get("pnl_pips", 0) or 0
                eq_data.append({
                    "Date": (t.get("closed_at") or t.get("created_at", ""))[:10],
                    "Cumulative P&L (pips)": round(cumulative, 1),
                })

            df_eq = pd.DataFrame(eq_data)
            fig = px.line(df_eq, x="Date", y="Cumulative P&L (pips)",
                          title="Cumulative P&L Over Time")
            fig.update_layout(height=350)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No closed trades for equity curve.")

        # Confidence breakdown
        col1, col2 = st.columns(2)

        with col1:
            st.subheader("Win Rate by Confidence")
            conf_stats = stats.get("confidence_stats", {})
            if conf_stats:
                conf_df = pd.DataFrame([
                    {"Confidence": k.upper(), "Win Rate": v["win_rate"], "Trades": v["total"]}
                    for k, v in conf_stats.items()
                ])
                fig = px.bar(conf_df, x="Confidence", y="Win Rate",
                             text="Trades", color="Confidence",
                             color_discrete_map={"HIGH": "#2ecc71", "MEDIUM": "#f1c40f", "LOW": "#e74c3c"})
                fig.update_layout(height=300, showlegend=False)
                fig.update_yaxes(range=[0, 100])
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No confidence data.")

        with col2:
            st.subheader("Win Rate by Session")
            sess_stats = stats.get("session_stats", {})
            if sess_stats:
                sess_df = pd.DataFrame([
                    {"Session": k, "Win Rate": v["win_rate"], "Trades": v["total"]}
                    for k, v in sess_stats.items()
                ])
                fig = px.bar(sess_df, x="Session", y="Win Rate",
                             text="Trades", color="Session")
                fig.update_layout(height=300, showlegend=False)
                fig.update_yaxes(range=[0, 100])
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No session data.")


# ============================================================================
# PAGE 3: Trade Journal
# ============================================================================
elif page == "📒 Trade Journal":
    st.title("📒 Trade Journal")

    # Filters
    col1, col2, col3 = st.columns(3)
    with col1:
        filter_bias = st.selectbox("Bias", ["All", "long", "short"])
    with col2:
        filter_conf = st.selectbox("Confidence", ["All", "high", "medium", "low"])
    with col3:
        filter_outcome = st.selectbox("Outcome", ["All", "full_win", "partial_win", "loss", "breakeven", "open"])

    trades = trade_tracker.get_recent_trades(limit=100)

    # Apply filters
    if filter_bias != "All":
        trades = [t for t in trades if t.get("bias") == filter_bias]
    if filter_conf != "All":
        trades = [t for t in trades if t.get("confidence") == filter_conf]
    if filter_outcome != "All":
        trades = [t for t in trades if t.get("outcome") == filter_outcome]

    if not trades:
        st.info("No trades matching filters.")
    else:
        st.caption(f"Showing {len(trades)} trades")

        for t in trades:
            outcome = t.get("outcome", "open")
            pnl = t.get("pnl_pips", 0) or 0
            bias = (t.get("bias") or "").upper()
            conf = (t.get("confidence") or "").upper()
            date = (t.get("created_at") or "")[:16]

            # Color coding
            if outcome in ("full_win", "partial_win"):
                icon = "🟢"
            elif outcome == "loss":
                icon = "🔴"
            elif outcome == "open":
                icon = "🔵"
            else:
                icon = "🟡"

            with st.expander(
                f"{icon} {date} — {bias} | {conf} | {outcome} | {pnl:+.1f} pips"
            ):
                c1, c2, c3, c4 = st.columns(4)
                with c1:
                    st.write(f"**Entry:** {t.get('entry_min', 0):.3f} - {t.get('entry_max', 0):.3f}")
                    st.write(f"**Actual Entry:** {t.get('actual_entry', 0):.3f}")
                with c2:
                    st.write(f"**SL:** {t.get('stop_loss', 0):.3f} ({t.get('sl_pips', 0):.1f} pips)")
                    st.write(f"**TP1:** {t.get('tp1', 0):.3f} ({t.get('tp1_pips', 0):.1f} pips)")
                    st.write(f"**TP2:** {t.get('tp2', 0):.3f} ({t.get('tp2_pips', 0):.1f} pips)")
                with c3:
                    st.write(f"**Checklist:** {t.get('checklist_score', 'N/A')}")
                    st.write(f"**Trend Alignment:** {t.get('trend_alignment', 'N/A')}")
                    st.write(f"**Price Zone:** {t.get('price_zone', 'N/A')}")
                with c4:
                    st.write(f"**D1 Trend:** {t.get('d1_trend', 'N/A')}")
                    st.write(f"**H4 Trend:** {t.get('h4_trend', 'N/A')}")
                    st.write(f"**H1 Trend:** {t.get('h1_trend', 'N/A')}")

                # Negative factors
                neg = t.get("negative_factors", "")
                if neg:
                    st.warning(f"**Negative factors:** {neg}")


# ============================================================================
# PAGE 4: Backtest Explorer
# ============================================================================
elif page == "🔬 Backtest Explorer":
    st.title("🔬 Backtest Explorer")

    # Historical data stats
    try:
        m1_count = hd.get_candle_count("GBPJPY", "M1")
        date_range = hd.get_date_range("GBPJPY", "M1")

        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("M1 Candles", f"{m1_count:,}")
        with c2:
            st.metric("From", date_range[0][:10] if date_range[0] else "N/A")
        with c3:
            st.metric("To", date_range[1][:10] if date_range[1] else "N/A")
    except Exception:
        st.warning("No historical data loaded. Upload M1 CSV via the API first.")

    st.markdown("---")

    # List backtest runs
    runs = bt.get_backtest_runs(limit=20)

    if not runs:
        st.info("No backtest runs yet. Use POST /backtest/run or /backtest/test to create one.")
    else:
        st.subheader("Backtest Runs")

        # Run selector
        run_options = {
            f"{r['id']} — {r.get('start_date', '?')} to {r.get('end_date', '?')} ({r.get('total_trades', 0)} trades, {r.get('win_rate', 0):.1f}% WR)": r['id']
            for r in runs
        }
        selected_label = st.selectbox("Select a run", list(run_options.keys()))
        selected_id = run_options[selected_label]

        # Load full results
        run = bt.get_backtest_run(selected_id)
        trades = bt.get_backtest_trades(selected_id)
        report = bt_report.generate_report(run, trades)

        if run:
            # Summary cards
            o = report["overview"]
            c1, c2, c3, c4, c5 = st.columns(5)
            with c1:
                st.metric("Win Rate", f"{o['win_rate']}%")
            with c2:
                st.metric("Total P&L", f"{o['total_pnl_pips']:+.1f} pips")
            with c3:
                st.metric("Profit Factor", f"{o['profit_factor']}")
            with c4:
                st.metric("Max Drawdown", f"{o['max_drawdown_pips']:.1f} pips")
            with c5:
                st.metric("Avg R:R", f"{o['avg_rr_achieved']}")

            st.markdown("---")

            # Equity curve
            eq_curve = report.get("equity_curve", [])
            if eq_curve:
                st.subheader("Equity Curve")
                df_eq = pd.DataFrame(eq_curve)
                fig = px.line(df_eq, x=df_eq.index, y="cumulative",
                              title="Cumulative P&L (pips)",
                              labels={"index": "Trade #", "cumulative": "Pips"})
                # Color fill based on positive/negative
                fig.update_traces(fill="tozeroy")
                fig.update_layout(height=350)
                st.plotly_chart(fig, use_container_width=True)

            # Breakdowns side by side
            col1, col2 = st.columns(2)

            with col1:
                st.subheader("By Confidence")
                by_conf = report.get("by_confidence", {})
                if by_conf:
                    conf_df = pd.DataFrame([
                        {"Confidence": k, "Win Rate": v["win_rate"],
                         "Trades": v["count"], "P&L": v["total_pnl"]}
                        for k, v in by_conf.items()
                    ])
                    fig = px.bar(conf_df, x="Confidence", y="Win Rate",
                                 text="Trades", color="Confidence")
                    fig.update_layout(height=300, showlegend=False)
                    fig.update_yaxes(range=[0, 100])
                    st.plotly_chart(fig, use_container_width=True)

            with col2:
                st.subheader("By Checklist Score")
                by_score = report.get("by_checklist_score", {})
                if by_score:
                    score_df = pd.DataFrame([
                        {"Score": k, "Win Rate": v["win_rate"],
                         "Trades": v["count"], "P&L": v["total_pnl"]}
                        for k, v in by_score.items()
                    ])
                    fig = px.bar(score_df, x="Score", y="Win Rate",
                                 text="Trades", color="Score")
                    fig.update_layout(height=300, showlegend=False)
                    fig.update_yaxes(range=[0, 100])
                    st.plotly_chart(fig, use_container_width=True)

            # Trades table
            st.subheader("Individual Trades")
            if trades:
                trade_df = pd.DataFrame(trades)[
                    ["trade_date", "bias", "outcome", "pnl_pips", "entry_price",
                     "sl_pips", "tp1_hit", "tp2_hit", "duration_minutes",
                     "checklist_score", "confidence"]
                ]
                trade_df.columns = ["Date", "Bias", "Outcome", "P&L Pips", "Entry",
                                    "SL Pips", "TP1 Hit", "TP2 Hit", "Duration (min)",
                                    "Checklist", "Confidence"]
                st.dataframe(trade_df, use_container_width=True, hide_index=True)


# ============================================================================
# PAGE 5: Risk Panel
# ============================================================================
elif page == "⚠️ Risk Panel":
    st.title("⚠️ Risk Panel")

    # Daily P&L
    daily = trade_tracker.get_daily_pnl()
    max_dd_pct = float(os.getenv("MAX_DAILY_DRAWDOWN_PCT", "3.0"))

    c1, c2, c3 = st.columns(3)
    with c1:
        today_pnl = daily.get("total_pnl_pips", 0)
        color = "normal" if today_pnl >= 0 else "inverse"
        st.metric("Today's P&L", f"{today_pnl:+.1f} pips", delta_color=color)
    with c2:
        today_money = daily.get("total_pnl_money", 0)
        st.metric("Today's P&L ($)", f"${today_money:+.2f}")
    with c3:
        st.metric("Max Daily DD Limit", f"{max_dd_pct}%")

    st.markdown("---")

    # Open trades
    st.subheader("Open Trades")
    open_trades = trade_tracker.get_open_trades()
    if open_trades:
        open_data = []
        for t in open_trades:
            open_data.append({
                "Symbol": t.get("symbol", ""),
                "Bias": (t.get("bias") or "").upper(),
                "Entry": f"{t.get('actual_entry', 0):.3f}",
                "SL": f"{t.get('stop_loss', 0):.3f}",
                "TP1": f"{t.get('tp1', 0):.3f}",
                "TP2": f"{t.get('tp2', 0):.3f}",
                "Confidence": (t.get("confidence") or "").upper(),
                "Opened": (t.get("executed_at") or "")[:16],
            })
        st.dataframe(pd.DataFrame(open_data), use_container_width=True, hide_index=True)
    else:
        st.info("No open trades.")

    # Exposure
    st.subheader("Currency Exposure")
    exposure = trade_tracker.get_open_currency_exposure()
    if exposure:
        for ccy, pairs in exposure.items():
            st.write(f"**{ccy}:** {', '.join(pairs)}")
    else:
        st.info("No open exposure.")


# ============================================================================
# PAGE 6: Analysis Stats
# ============================================================================
elif page == "📋 Analysis Stats":
    st.title("📋 Analysis Stats")

    report = trade_tracker.get_weekly_performance_report()

    if not report or report.get("total_trades", 0) == 0:
        st.info("No trade data for weekly analysis.")
    else:
        # Overview
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("Trades (7d)", report.get("total_trades", 0))
        with c2:
            st.metric("Win Rate (7d)", f"{report.get('win_rate', 0):.1f}%")
        with c3:
            st.metric("P&L (7d)", f"{report.get('total_pnl_pips', 0):+.1f} pips")
        with c4:
            st.metric("Avg R:R (7d)", f"{report.get('avg_rr', 0):.2f}")

        st.markdown("---")

        # Breakdown by checklist score
        st.subheader("By Checklist Score")
        by_score = report.get("by_checklist_score", {})
        if by_score:
            score_data = []
            for bracket, data in by_score.items():
                score_data.append({
                    "Score Bracket": bracket,
                    "Trades": data.get("count", 0),
                    "Win Rate": f"{data.get('win_rate', 0):.1f}%",
                    "P&L": f"{data.get('total_pnl', 0):+.1f} pips",
                })
            st.dataframe(pd.DataFrame(score_data), use_container_width=True, hide_index=True)

        # Breakdown by confidence
        st.subheader("By Confidence Level")
        by_conf = report.get("by_confidence", {})
        if by_conf:
            conf_data = []
            for level, data in by_conf.items():
                conf_data.append({
                    "Confidence": level.upper(),
                    "Trades": data.get("count", 0),
                    "Win Rate": f"{data.get('win_rate', 0):.1f}%",
                    "P&L": f"{data.get('total_pnl', 0):+.1f} pips",
                })
            st.dataframe(pd.DataFrame(conf_data), use_container_width=True, hide_index=True)

        # By bias
        col1, col2 = st.columns(2)

        with col1:
            st.subheader("By Bias")
            by_bias = report.get("by_bias", {})
            if by_bias:
                bias_data = []
                for bias, data in by_bias.items():
                    bias_data.append({
                        "Bias": bias.upper(),
                        "Trades": data.get("count", 0),
                        "Win Rate": f"{data.get('win_rate', 0):.1f}%",
                        "P&L": f"{data.get('total_pnl', 0):+.1f} pips",
                    })
                st.dataframe(pd.DataFrame(bias_data), use_container_width=True, hide_index=True)

        with col2:
            st.subheader("By Entry Status")
            by_entry = report.get("by_entry_status", {})
            if by_entry:
                entry_data = []
                for status, data in by_entry.items():
                    entry_data.append({
                        "Entry Status": status,
                        "Trades": data.get("count", 0),
                        "Win Rate": f"{data.get('win_rate', 0):.1f}%",
                    })
                st.dataframe(pd.DataFrame(entry_data), use_container_width=True, hide_index=True)
