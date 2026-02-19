import { fetchStats } from "@/lib/api";

const statCardStyle: React.CSSProperties = {
  background: "var(--bg-card)",
  border: "1px solid var(--border)",
  borderRadius: 12,
  padding: "28px 24px",
  textAlign: "center",
  flex: "1 1 200px",
};

export default async function LiveStats() {
  let stats;
  try {
    stats = await fetchStats();
  } catch {
    return (
      <section id="stats" style={{ padding: "60px 1rem", textAlign: "center" }}>
        <p style={{ color: "var(--text-secondary)" }}>
          Stats temporarily unavailable — check back soon.
        </p>
      </section>
    );
  }

  const cards = [
    {
      label: "Win Rate",
      value: `${stats.win_rate.toFixed(1)}%`,
      color: stats.win_rate >= 50 ? "var(--green)" : "var(--red)",
    },
    {
      label: "Total P&L",
      value: `${stats.total_pnl_pips >= 0 ? "+" : ""}${stats.total_pnl_pips.toFixed(0)} pips`,
      color: stats.total_pnl_pips >= 0 ? "var(--green)" : "var(--red)",
    },
    {
      label: "Trades Taken",
      value: stats.total_trades.toString(),
      color: "var(--accent)",
    },
    {
      label: "Avg Win",
      value: `+${stats.avg_win_pips.toFixed(1)} pips`,
      color: "var(--green)",
    },
  ];

  return (
    <section id="stats" style={{ padding: "60px 1rem" }}>
      <div style={{ maxWidth: 900, margin: "0 auto" }}>
        {/* Section label */}
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
          Live Performance
        </p>
        <h2
          style={{
            textAlign: "center",
            fontSize: "1.8rem",
            fontWeight: 700,
            marginBottom: 8,
          }}
        >
          Verified Track Record
        </h2>
        <p
          style={{
            textAlign: "center",
            color: "var(--text-secondary)",
            marginBottom: 36,
            fontSize: 15,
          }}
        >
          Last {stats.period_days} days — updated every 60 seconds from our live trading server.
        </p>

        {/* Stat cards */}
        <div
          style={{
            display: "flex",
            gap: 16,
            flexWrap: "wrap",
          }}
        >
          {cards.map((card) => (
            <div key={card.label} style={statCardStyle}>
              <div
                style={{
                  fontSize: "2rem",
                  fontWeight: 700,
                  color: card.color,
                  marginBottom: 6,
                }}
              >
                {card.value}
              </div>
              <div
                style={{
                  fontSize: 14,
                  color: "var(--text-secondary)",
                  textTransform: "uppercase",
                  letterSpacing: 1,
                }}
              >
                {card.label}
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
