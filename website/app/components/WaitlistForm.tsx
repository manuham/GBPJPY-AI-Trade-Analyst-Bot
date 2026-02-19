"use client";

import { useState } from "react";

export default function WaitlistForm() {
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [message, setMessage] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email) return;

    setStatus("loading");
    try {
      const res = await fetch("/api/waitlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      const data = await res.json();

      if (res.ok) {
        setStatus("success");
        setMessage(data.message || "You're on the list!");
        setEmail("");
      } else {
        setStatus("error");
        setMessage(data.error || "Something went wrong.");
      }
    } catch {
      setStatus("error");
      setMessage("Network error — please try again.");
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      style={{
        display: "flex",
        gap: 10,
        maxWidth: 420,
        margin: "0 auto",
        flexWrap: "wrap",
        justifyContent: "center",
      }}
    >
      <input
        type="email"
        placeholder="your@email.com"
        value={email}
        onChange={(e) => {
          setEmail(e.target.value);
          if (status !== "idle") setStatus("idle");
        }}
        required
        style={{
          flex: "1 1 240px",
          padding: "12px 16px",
          borderRadius: 8,
          border: "1px solid var(--border)",
          background: "var(--bg-primary)",
          color: "var(--text-primary)",
          fontSize: 15,
          outline: "none",
        }}
      />
      <button
        type="submit"
        disabled={status === "loading"}
        style={{
          padding: "12px 24px",
          borderRadius: 8,
          border: "none",
          background: "var(--accent)",
          color: "#fff",
          fontWeight: 600,
          fontSize: 15,
          cursor: status === "loading" ? "wait" : "pointer",
          opacity: status === "loading" ? 0.7 : 1,
        }}
      >
        {status === "loading" ? "Joining..." : "Join Waitlist"}
      </button>

      {status === "success" && (
        <p style={{ width: "100%", textAlign: "center", color: "var(--green)", fontSize: 14, marginTop: 4 }}>
          {message}
        </p>
      )}
      {status === "error" && (
        <p style={{ width: "100%", textAlign: "center", color: "var(--red)", fontSize: 14, marginTop: 4 }}>
          {message}
        </p>
      )}
    </form>
  );
}
