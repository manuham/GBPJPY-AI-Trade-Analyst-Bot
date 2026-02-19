"use client";

import useSWR from "swr";
import { API_URL, swrFetcher, PublicTrade } from "@/lib/api";

export default function TradeTable() {
  const { data, error, isLoading } = useSWR<{ trades: PublicTrade[] }>(
    `${API_URL}/public/trades?limit=20`,
    swrFetcher,
    { refreshInterval: 30000 } // Poll every 30s
  );

  const trades = data?.trades || [];

  return (
    <section id="track-record" style={{ padding: "80px 1rem" }}>
      <div style={{ maxWidth: 960, margin: "0 auto" }}>
        <p
          style={{
            textAlign: "center",
            fontSize: 13,
            color: "var(--accent)",
            fontWeight: 600,
            letterSpacing: 1.5,
            textTransform: "uppercase",
            marginBottom: 8,
          }}
        >
          Track Record
        </p>
        <h2
          style={{
            textAlign: "center",
            fontSize: "1.8rem",
            fontWeight: 700,
            marginBottom: 8,
          }}
        >
          Recent Trades — Live &amp; Unfiltered
        </h2>
        <p
          style={{
            textAlign: "center",
            color: "var(--text-secondary)",
            marginBottom: 32,
            fontSize: 15,
          }}
        >
          Every trade is recorded — no deletions, no cherry-picking. Updates every 30 seconds.
        </p>

        {/* Table container */}
        <div
          style={{
            overflowX: "auto",
            border: "1px solid var(--border)",
            borderRadius: 12,
            background: "var(--bg-card)",
          }}
        >
          {isLoading ? (
            <div
              style={{
                padding: 40,
                textAlign: "center",
                color: "var(--text-secondary)",
              }}
            >
              Loading trades...
            </div>
          ) : error ? (
            <div
              style={{
                padding: 40,
                textAlign: "center",
                color: "var(--text-secondary)",
              }}
            >
              Unable to load trades — server may be offline.
            </div>
          ) : trades.length === 0 ? (
            <div
              style={{
                padding: 40,
                textAlign: "center",
                color: "var(--text-secondary)",
              }}
            >
              No trades yet — the bot is waiting for the next London session.
            </div>
          ) : (
            <table
              style={{
                width: "100%",
                borderCollapse: "collapse",
                fontSize: 14,
              }}
            >
              <thead>
                <tr
                  style={{
                    borderBottom: "1px solid var(--border)",
                    textAlign: "left",
                  }}
                >
                  {[
                    "Date",
                    "Pair",
                    "Bias",
                    "Score",
                    "Entry",
                    "SL",
                    "TP2",
                    "R:R",
                    "Outcome",
                    "P&L",
                  ].map((h) => (
                    <th
                      key={h}
                      style={{
                        padding: "12px 14px",
                        color: "var(--text-secondary)",
                        fontWeight: 600,
                        fontSize: 12,
                        textTransform: "uppercase",
                        letterSpacing: 0.5,
                        whiteSpace: "nowrap",
                      }}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {trades.map((t) => {
                  const isWin = t.pnl_pips > 0;
                  const isBE = t.pnl_pips === 0;
                  const pnlColor = isWin
                    ? "var(--green)"
                    : isBE
                      ? "var(--yellow)"
                      : "var(--red)";

                  const date = new Date(t.created_at).toLocaleDateString(
                    "en-GB",
                    {
                      day: "2-digit",
                      month: "short",
                    }
                  );

                  return (
                    <tr
                      key={t.id}
                      style={{
                        borderBottom: "1px solid var(--border)",
                      }}
                    >
                      <td style={{ padding: "10px 14px", whiteSpace: "nowrap" }}>
                        {date}
                      </td>
                      <td style={{ padding: "10px 14px", fontWeight: 600 }}>
                        {t.symbol}
                      </td>
                      <td style={{ padding: "10px 14px" }}>
                        <span
                          style={{
                            color:
                              t.bias === "LONG"
                                ? "var(--green)"
                                : "var(--red)",
                            fontWeight: 600,
                          }}
                        >
                          {t.bias}
                        </span>
                      </td>
                      <td style={{ padding: "10px 14px" }}>
                        {t.checklist_score}
                      </td>
                      <td style={{ padding: "10px 14px" }}>
                        {t.actual_entry?.toFixed(3) || "—"}
                      </td>
                      <td style={{ padding: "10px 14px" }}>
                        {t.stop_loss?.toFixed(3) || "—"}
                      </td>
                      <td style={{ padding: "10px 14px" }}>
                        {t.tp2?.toFixed(3) || "—"}
                      </td>
                      <td style={{ padding: "10px 14px" }}>
                        {t.rr_tp2 ? `1:${t.rr_tp2.toFixed(1)}` : "—"}
                      </td>
                      <td style={{ padding: "10px 14px" }}>
                        <span
                          style={{
                            padding: "3px 10px",
                            borderRadius: 6,
                            fontSize: 12,
                            fontWeight: 600,
                            background:
                              t.status === "open"
                                ? "rgba(59,130,246,0.15)"
                                : isWin
                                  ? "rgba(34,197,94,0.12)"
                                  : "rgba(239,68,68,0.12)",
                            color:
                              t.status === "open"
                                ? "var(--accent)"
                                : pnlColor,
                          }}
                        >
                          {t.status === "open" ? "OPEN" : t.outcome || "—"}
                        </span>
                      </td>
                      <td
                        style={{
                          padding: "10px 14px",
                          fontWeight: 700,
                          color: t.status === "open" ? "var(--text-secondary)" : pnlColor,
                        }}
                      >
                        {t.status === "open"
                          ? "—"
                          : `${t.pnl_pips >= 0 ? "+" : ""}${t.pnl_pips.toFixed(1)}`}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </section>
  );
}
