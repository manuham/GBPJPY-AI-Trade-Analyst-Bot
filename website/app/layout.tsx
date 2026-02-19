import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Trade Bot ICT — AI-Powered Forex Signals",
  description:
    "Professional forex trade signals using Claude AI and ICT methodology. Full transparency — every trade shown, wins AND losses.",
  openGraph: {
    title: "AI Trade Bot ICT — AI-Powered Forex Signals",
    description:
      "Professional forex signals with verified track record. ICT methodology meets artificial intelligence.",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
