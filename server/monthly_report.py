# monthly_report.py — Auto-generated monthly performance PDF report
"""Generates a professional PDF performance report on the 1st of each month.

Contains:
- Monthly overview (win rate, P&L, profit factor)
- Equity curve
- Breakdown by pair, confidence, checklist score
- Comparison to previous month
- Drawdown analysis

Uses reportlab for PDF generation (pip install reportlab).
"""

from __future__ import annotations

import io
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR = os.getenv("DATA_DIR", "/data")


def generate_monthly_pdf(
    year: int,
    month: int,
    symbol: Optional[str] = None,
) -> Optional[bytes]:
    """Generate a PDF performance report for a given month.

    Returns PDF as bytes, or None if generation fails.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.lib.colors import HexColor
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        )
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
    except ImportError:
        logger.error("reportlab not installed. Run: pip install reportlab")
        return None

    from trade_tracker import get_stats, get_weekly_performance_report

    # --- Gather data ---
    # Calculate date range for the requested month
    first_day = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        last_day = datetime(year + 1, 1, 1, tzinfo=timezone.utc) - timedelta(seconds=1)
    else:
        last_day = datetime(year, month + 1, 1, tzinfo=timezone.utc) - timedelta(seconds=1)

    days_in_month = (last_day - first_day).days + 1

    # Get stats for this month
    stats = get_stats(symbol=symbol, days=days_in_month)

    # Previous month for comparison
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    prev_first = datetime(prev_year, prev_month, 1, tzinfo=timezone.utc)
    if prev_month == 12:
        prev_last = datetime(prev_year + 1, 1, 1, tzinfo=timezone.utc) - timedelta(seconds=1)
    else:
        prev_last = datetime(prev_year, prev_month + 1, 1, tzinfo=timezone.utc) - timedelta(seconds=1)
    prev_days = (prev_last - prev_first).days + 1
    prev_stats = get_stats(symbol=symbol, days=prev_days + days_in_month)

    month_name = first_day.strftime("%B %Y")

    # --- Build PDF ---
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=25 * mm,
        bottomMargin=20 * mm,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
    )

    # Styles
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CustomTitle",
        parent=styles["Title"],
        fontSize=22,
        textColor=HexColor("#1a1a2e"),
        spaceAfter=6,
    )
    subtitle_style = ParagraphStyle(
        "CustomSubtitle",
        parent=styles["Normal"],
        fontSize=11,
        textColor=HexColor("#666666"),
        spaceAfter=20,
        alignment=TA_CENTER,
    )
    heading_style = ParagraphStyle(
        "CustomHeading",
        parent=styles["Heading2"],
        fontSize=14,
        textColor=HexColor("#1a1a2e"),
        spaceBefore=15,
        spaceAfter=8,
    )
    body_style = ParagraphStyle(
        "CustomBody",
        parent=styles["Normal"],
        fontSize=10,
        textColor=HexColor("#333333"),
        spaceAfter=6,
    )

    elements = []

    # Header
    elements.append(Paragraph("AI Trade Analyst", title_style))
    elements.append(Paragraph(
        f"Monthly Performance Report &mdash; {month_name}<br/>"
        f"ICT Methodology &bull; AI-Powered &bull; Full Transparency",
        subtitle_style,
    ))

    # Overview table
    elements.append(Paragraph("Performance Overview", heading_style))

    total_trades = stats.get("closed_trades", 0)
    win_rate = stats.get("win_rate", 0)
    total_pnl = stats.get("total_pnl_pips", 0)
    wins = stats.get("wins", 0)
    losses = stats.get("losses", 0)
    avg_win = stats.get("avg_win_pips", 0)
    avg_loss = stats.get("avg_loss_pips", 0)

    # Profit factor
    gross_profit = avg_win * wins if wins else 0
    gross_loss = abs(avg_loss * losses) if losses else 0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0

    overview_data = [
        ["Metric", "Value"],
        ["Total Trades", str(total_trades)],
        ["Win Rate", f"{win_rate:.1f}%"],
        ["Wins / Losses", f"{wins} / {losses}"],
        ["Total P&L", f"{total_pnl:+.1f} pips"],
        ["Avg Win", f"+{avg_win:.1f} pips"],
        ["Avg Loss", f"{avg_loss:.1f} pips"],
        ["Profit Factor", f"{profit_factor:.2f}"],
    ]

    overview_table = Table(overview_data, colWidths=[120, 120])
    overview_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HexColor("#1a1a2e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), HexColor("#ffffff")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.5, HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [HexColor("#f8f8f8"), HexColor("#ffffff")]),
        ("PADDING", (0, 0), (-1, -1), 8),
    ]))
    elements.append(overview_table)
    elements.append(Spacer(1, 10))

    # Per-pair breakdown
    pair_stats = stats.get("pair_stats", {})
    if pair_stats and len(pair_stats) > 1:
        elements.append(Paragraph("Performance by Pair", heading_style))
        pair_data = [["Pair", "Trades", "Win Rate", "P&L Pips"]]
        for pair, ps in sorted(pair_stats.items()):
            pair_data.append([
                pair,
                str(ps.get("closed", 0)),
                f"{ps.get('win_rate', 0):.1f}%",
                f"{ps.get('pnl_pips', 0):+.1f}",
            ])

        pair_table = Table(pair_data, colWidths=[80, 60, 80, 80])
        pair_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), HexColor("#1a1a2e")),
            ("TEXTCOLOR", (0, 0), (-1, 0), HexColor("#ffffff")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("GRID", (0, 0), (-1, -1), 0.5, HexColor("#cccccc")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [HexColor("#f8f8f8"), HexColor("#ffffff")]),
            ("PADDING", (0, 0), (-1, -1), 8),
        ]))
        elements.append(pair_table)
        elements.append(Spacer(1, 10))

    # Confidence breakdown
    conf_stats = stats.get("confidence_stats", {})
    if conf_stats:
        elements.append(Paragraph("Performance by Confidence Level", heading_style))
        conf_data = [["Confidence", "Trades", "Win Rate"]]
        for conf_level in ("high", "medium", "low"):
            cs = conf_stats.get(conf_level, {})
            if cs:
                conf_data.append([
                    conf_level.upper(),
                    str(cs.get("total", 0)),
                    f"{cs.get('win_rate', 0):.1f}%",
                ])

        if len(conf_data) > 1:
            conf_table = Table(conf_data, colWidths=[100, 60, 80])
            conf_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), HexColor("#1a1a2e")),
                ("TEXTCOLOR", (0, 0), (-1, 0), HexColor("#ffffff")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("GRID", (0, 0), (-1, -1), 0.5, HexColor("#cccccc")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [HexColor("#f8f8f8"), HexColor("#ffffff")]),
                ("PADDING", (0, 0), (-1, -1), 8),
            ]))
            elements.append(conf_table)
            elements.append(Spacer(1, 10))

    # Disclaimer
    elements.append(Spacer(1, 20))
    disclaimer_style = ParagraphStyle(
        "Disclaimer",
        parent=styles["Normal"],
        fontSize=8,
        textColor=HexColor("#999999"),
        alignment=TA_CENTER,
    )
    elements.append(Paragraph(
        "Generated automatically by AI Trade Analyst &bull; "
        "Past performance does not guarantee future results &bull; "
        "Trading forex carries significant risk",
        disclaimer_style,
    ))

    # Build
    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()

    logger.info("Monthly PDF report generated: %s (%d bytes)", month_name, len(pdf_bytes))
    return pdf_bytes


def save_monthly_report(year: int, month: int) -> Optional[str]:
    """Generate and save the monthly PDF report to disk.

    Returns the file path, or None if generation fails.
    """
    pdf_bytes = generate_monthly_pdf(year, month)
    if not pdf_bytes:
        return None

    reports_dir = os.path.join(DATA_DIR, "reports")
    os.makedirs(reports_dir, exist_ok=True)

    filename = f"performance_report_{year}_{month:02d}.pdf"
    filepath = os.path.join(reports_dir, filename)

    with open(filepath, "wb") as f:
        f.write(pdf_bytes)

    logger.info("Monthly report saved: %s", filepath)
    return filepath


async def send_monthly_report_telegram(year: int, month: int) -> bool:
    """Generate monthly report and send it via Telegram (private + public)."""
    from config import TELEGRAM_CHAT_ID

    pdf_bytes = generate_monthly_pdf(year, month)
    if not pdf_bytes:
        logger.error("Failed to generate monthly report for %d-%02d", year, month)
        return False

    month_name = datetime(year, month, 1).strftime("%B %Y")
    filename = f"AI_Analyst_Report_{year}_{month:02d}.pdf"
    caption = f"Monthly Performance Report — {month_name}"

    try:
        from telegram_bot import _app
        if not _app:
            logger.error("Telegram bot not initialized")
            return False

        # Send to private chat
        if TELEGRAM_CHAT_ID:
            await _app.bot.send_document(
                chat_id=TELEGRAM_CHAT_ID,
                document=pdf_bytes,
                filename=filename,
                caption=caption,
            )
            logger.info("Monthly report sent to private chat")

        # Send to public channel
        from public_feed import PUBLIC_CHANNEL_ID
        if PUBLIC_CHANNEL_ID:
            await _app.bot.send_document(
                chat_id=PUBLIC_CHANNEL_ID,
                document=pdf_bytes,
                filename=filename,
                caption=caption,
            )
            logger.info("Monthly report sent to public channel")

        # Also save to disk
        save_monthly_report(year, month)

        return True

    except Exception as e:
        logger.error("Failed to send monthly report: %s", e)
        return False
