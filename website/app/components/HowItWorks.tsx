const steps = [
  {
    icon: "🧠",
    title: "AI Multi-Timeframe Analysis",
    description:
      "Every morning at 08:00 CET, Claude AI scans D1, H4, H1 and M5 charts — screening for ICT setups with institutional-grade precision.",
  },
  {
    icon: "📋",
    title: "12-Point ICT Checklist",
    description:
      "Each setup is scored against 12 ICT criteria: bias alignment, order blocks, FVGs, MSS, OTE zones, liquidity sweeps, R:R and more.",
  },
  {
    icon: "🎯",
    title: "Smart Zone Entry",
    description:
      "High-scoring setups are automatically watched. When price reaches the entry zone, a separate AI confirms on the M1 chart before executing.",
  },
];

const stepCardStyle: React.CSSProperties = {
  background: "var(--bg-card)",
  border: "1px solid var(--border)",
  borderRadius: 12,
  padding: "32px 24px",
  flex: "1 1 260px",
  textAlign: "center",
};

export default function HowItWorks() {
  return (
    <section
      id="how-it-works"
      style={{
        padding: "80px 1rem",
        background: "var(--bg-secondary)",
      }}
    >
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
          How It Works
        </p>
        <h2
          style={{
            textAlign: "center",
            fontSize: "1.8rem",
            fontWeight: 700,
            marginBottom: 40,
          }}
        >
          Three Layers of Intelligence
        </h2>

        <div style={{ display: "flex", gap: 20, flexWrap: "wrap" }}>
          {steps.map((step, i) => (
            <div key={i} style={stepCardStyle}>
              <div style={{ fontSize: "2.5rem", marginBottom: 16 }}>
                {step.icon}
              </div>
              <div
                style={{
                  fontSize: 12,
                  color: "var(--accent)",
                  fontWeight: 700,
                  letterSpacing: 1,
                  marginBottom: 8,
                }}
              >
                STEP {i + 1}
              </div>
              <h3
                style={{
                  fontSize: "1.1rem",
                  fontWeight: 600,
                  marginBottom: 12,
                }}
              >
                {step.title}
              </h3>
              <p
                style={{
                  color: "var(--text-secondary)",
                  fontSize: 15,
                  lineHeight: 1.6,
                }}
              >
                {step.description}
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
