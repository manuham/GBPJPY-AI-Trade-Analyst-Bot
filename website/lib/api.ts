// API configuration and fetch helpers
const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://46.225.66.110:8000";

export interface PublicStats {
  period_days: number;
  total_trades: number;
  win_rate: number;
  total_pnl_pips: number;
  avg_win_pips: number;
  avg_loss_pips: number;
  wins: number;
  losses: number;
}

export interface PublicTrade {
  id: string;
  symbol: string;
  bias: string;
  confidence: string;
  checklist_score: string;
  actual_entry: number;
  stop_loss: number;
  tp1: number;
  tp2: number;
  sl_pips: number;
  rr_tp2: number;
  status: string;
  outcome: string;
  pnl_pips: number;
  created_at: string;
  closed_at: string;
}

export async function fetchStats(): Promise<PublicStats> {
  const res = await fetch(`${API_URL}/public/stats`, {
    next: { revalidate: 60 }, // Revalidate every 60 seconds
  });
  if (!res.ok) throw new Error("Failed to fetch stats");
  return res.json();
}

export async function fetchTrades(limit = 50): Promise<PublicTrade[]> {
  const res = await fetch(`${API_URL}/public/trades?limit=${limit}`, {
    cache: "no-store", // Always fresh for trade data
  });
  if (!res.ok) throw new Error("Failed to fetch trades");
  const data = await res.json();
  return data.trades;
}

// Client-side fetcher for SWR
export const swrFetcher = (url: string) =>
  fetch(url).then((r) => r.json());

export { API_URL };
