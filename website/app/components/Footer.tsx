export default function Footer() {
  return (
    <footer
      style={{
        borderTop: "1px solid var(--border)",
        padding: "40px 1rem",
        textAlign: "center",
      }}
    >
      <div style={{ maxWidth: 700, margin: "0 auto" }}>
        {/* Links */}
        <div
          style={{
            display: "flex",
            justifyContent: "center",
            gap: 24,
            flexWrap: "wrap",
            marginBottom: 20,
          }}
        >
          <a
            href="#stats"
            style={{
              color: "var(--text-secondary)",
              textDecoration: "none",
              fontSize: 14,
            }}
          >
            Live Stats
          </a>
          <a
            href="#track-record"
            style={{
              color: "var(--text-secondary)",
              textDecoration: "none",
              fontSize: 14,
            }}
          >
            Track Record
          </a>
          <a
            href="#pricing"
            style={{
              color: "var(--text-secondary)",
              textDecoration: "none",
              fontSize: 14,
            }}
          >
            Pricing
          </a>
          <a
            href="https://t.me/ai_trade_analyst"
            target="_blank"
            rel="noopener noreferrer"
            style={{
              color: "var(--text-secondary)",
              textDecoration: "none",
              fontSize: 14,
            }}
          >
            Telegram
          </a>
        </div>

        {/* Disclaimer */}
        <p
          style={{
            fontSize: 12,
            color: "var(--text-secondary)",
            lineHeight: 1.6,
            maxWidth: 560,
            margin: "0 auto 16px",
            opacity: 0.7,
          }}
        >
          Trading forex involves significant risk. Past performance does not guarantee
          future results. AI Trade Analyst provides analysis, not financial advice.
          Always do your own research and never trade with money you cannot afford
          to lose.
        </p>

        {/* Copyright */}
        <p style={{ fontSize: 13, color: "var(--text-secondary)", opacity: 0.5 }}>
          &copy; {new Date().getFullYear()} AI Trade Analyst. All rights reserved.
        </p>
      </div>
    </footer>
  );
}
