export default function Hero() {
  return (
    <section
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        textAlign: "center",
        padding: "2rem 1rem",
        background:
          "radial-gradient(ellipse at 50% 0%, rgba(59,130,246,0.15) 0%, transparent 60%)",
      }}
    >
      <div style={{ maxWidth: 720 }}>
        {/* Badge */}
        <div
          style={{
            display: "inline-block",
            padding: "6px 16px",
            borderRadius: 999,
            border: "1px solid var(--border)",
            fontSize: 13,
            color: "var(--text-secondary)",
            marginBottom: 24,
            letterSpacing: "0.5px",
          }}
        >
          🤖 Powered by Claude AI &amp; ICT Methodology
        </div>

        {/* Headline */}
        <h1
          style={{
            fontSize: "clamp(2.2rem, 5vw, 3.5rem)",
            fontWeight: 700,
            lineHeight: 1.15,
            marginBottom: 20,
          }}
        >
          AI-Powered Forex Signals
          <br />
          <span style={{ color: "var(--accent)" }}>With Full Transparency</span>
        </h1>

        {/* Sub-headline */}
        <p
          style={{
            fontSize: "clamp(1rem, 2vw, 1.2rem)",
            color: "var(--text-secondary)",
            lineHeight: 1.7,
            marginBottom: 36,
            maxWidth: 560,
            marginLeft: "auto",
            marginRight: "auto",
          }}
        >
          Every trade is shown — wins <strong style={{ color: "var(--green)" }}>AND</strong>{" "}
          losses. No cherry-picking. Our AI analyzes D1→M5 structure using ICT
          methodology and delivers setups straight to your Telegram.
        </p>

        {/* CTA buttons */}
        <div
          style={{
            display: "flex",
            gap: 16,
            justifyContent: "center",
            flexWrap: "wrap",
          }}
        >
          <a
            href="#pricing"
            style={{
              padding: "14px 32px",
              background: "var(--accent)",
              color: "#fff",
              borderRadius: 8,
              fontWeight: 600,
              fontSize: 16,
              textDecoration: "none",
              transition: "background 0.2s",
            }}
          >
            Join Waitlist
          </a>
          <a
            href="#track-record"
            style={{
              padding: "14px 32px",
              border: "1px solid var(--border)",
              color: "var(--text-primary)",
              borderRadius: 8,
              fontWeight: 600,
              fontSize: 16,
              textDecoration: "none",
              transition: "border-color 0.2s",
            }}
          >
            View Track Record ↓
          </a>
        </div>
      </div>
    </section>
  );
}
