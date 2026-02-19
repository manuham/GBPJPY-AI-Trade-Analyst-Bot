import WaitlistForm from "./WaitlistForm";

const tiers = [
  {
    name: "Free",
    price: "$0",
    period: "forever",
    description: "See what the AI finds — delayed by 1 day.",
    features: [
      "Public trade history (24h delay)",
      "Weekly performance reports",
      "Telegram community access",
    ],
    highlight: false,
    cta: "Current Plan",
  },
  {
    name: "Starter",
    price: "$29",
    period: "/month",
    description: "Real-time signals for one pair.",
    features: [
      "Live GBPJPY signals via Telegram",
      "Real-time entry, SL & TP levels",
      "ICT analysis breakdown",
      "Email support",
    ],
    highlight: false,
    cta: "Join Waitlist",
  },
  {
    name: "Pro",
    price: "$79",
    period: "/month",
    description: "All pairs + full analysis access.",
    features: [
      "All currency pairs (as added)",
      "Full ICT checklist with reasoning",
      "Chart screenshots & annotations",
      "Priority Telegram group",
      "Monthly PDF reports",
    ],
    highlight: true,
    cta: "Join Waitlist",
  },
  {
    name: "Enterprise",
    price: "$199",
    period: "/month",
    description: "API access + custom configuration.",
    features: [
      "Everything in Pro",
      "REST API access for your systems",
      "Custom pair selection",
      "Custom risk parameters",
      "1-on-1 onboarding call",
    ],
    highlight: false,
    cta: "Join Waitlist",
  },
];

export default function Pricing() {
  return (
    <section
      id="pricing"
      style={{
        padding: "80px 1rem",
        background: "var(--bg-secondary)",
      }}
    >
      <div style={{ maxWidth: 1060, margin: "0 auto" }}>
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
          Pricing
        </p>
        <h2
          style={{
            textAlign: "center",
            fontSize: "1.8rem",
            fontWeight: 700,
            marginBottom: 8,
          }}
        >
          Simple, Transparent Plans
        </h2>
        <p
          style={{
            textAlign: "center",
            color: "var(--text-secondary)",
            marginBottom: 40,
            fontSize: 15,
          }}
        >
          Launching soon — join the waitlist to lock in early-bird pricing.
        </p>

        {/* Tier cards */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))",
            gap: 18,
            marginBottom: 48,
          }}
        >
          {tiers.map((tier) => (
            <div
              key={tier.name}
              style={{
                background: "var(--bg-card)",
                border: tier.highlight
                  ? "2px solid var(--accent)"
                  : "1px solid var(--border)",
                borderRadius: 14,
                padding: "32px 24px",
                display: "flex",
                flexDirection: "column",
                position: "relative",
              }}
            >
              {tier.highlight && (
                <div
                  style={{
                    position: "absolute",
                    top: -12,
                    left: "50%",
                    transform: "translateX(-50%)",
                    background: "var(--accent)",
                    color: "#fff",
                    fontSize: 11,
                    fontWeight: 700,
                    padding: "4px 14px",
                    borderRadius: 999,
                    letterSpacing: 1,
                    textTransform: "uppercase",
                  }}
                >
                  Most Popular
                </div>
              )}

              <h3 style={{ fontSize: "1.1rem", fontWeight: 600, marginBottom: 4 }}>
                {tier.name}
              </h3>
              <div style={{ marginBottom: 12 }}>
                <span style={{ fontSize: "2rem", fontWeight: 700 }}>
                  {tier.price}
                </span>
                <span
                  style={{
                    fontSize: 14,
                    color: "var(--text-secondary)",
                  }}
                >
                  {tier.period}
                </span>
              </div>
              <p
                style={{
                  fontSize: 14,
                  color: "var(--text-secondary)",
                  marginBottom: 20,
                  lineHeight: 1.5,
                }}
              >
                {tier.description}
              </p>

              <ul style={{ listStyle: "none", padding: 0, margin: 0, flex: 1 }}>
                {tier.features.map((f) => (
                  <li
                    key={f}
                    style={{
                      fontSize: 14,
                      color: "var(--text-secondary)",
                      padding: "5px 0",
                      display: "flex",
                      alignItems: "flex-start",
                      gap: 8,
                    }}
                  >
                    <span style={{ color: "var(--green)", flexShrink: 0 }}>✓</span>
                    {f}
                  </li>
                ))}
              </ul>

              <div
                style={{
                  marginTop: 24,
                  padding: "10px 0",
                  textAlign: "center",
                  borderRadius: 8,
                  fontWeight: 600,
                  fontSize: 14,
                  background: tier.highlight
                    ? "var(--accent)"
                    : "transparent",
                  border: tier.highlight
                    ? "none"
                    : "1px solid var(--border)",
                  color: tier.highlight ? "#fff" : "var(--text-secondary)",
                  cursor: tier.cta === "Current Plan" ? "default" : "pointer",
                }}
              >
                {tier.cta}
              </div>
            </div>
          ))}
        </div>

        {/* Waitlist form */}
        <div style={{ textAlign: "center", marginBottom: 12 }}>
          <h3 style={{ fontSize: "1.2rem", fontWeight: 600, marginBottom: 16 }}>
            Get Notified When We Launch
          </h3>
        </div>
        <WaitlistForm />
      </div>
    </section>
  );
}
