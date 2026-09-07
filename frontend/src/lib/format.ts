/** Formatting helpers. Indian number conventions for prices. */

import type { Direction, Severity } from "./types";

export function inr(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  return `₹${value.toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export function percent(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

export function points(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)} pp`;
}

export function compactNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  if (value >= 1e7) return `${(value / 1e7).toFixed(2)} Cr`;
  if (value >= 1e5) return `${(value / 1e5).toFixed(2)} L`;
  if (value >= 1e3) return `${(value / 1e3).toFixed(1)}K`;
  return value.toFixed(0);
}

/** "3 hours ago" - used for "since you last checked". */
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "--";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "--";
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));

  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} day${days === 1 ? "" : "s"} ago`;
  return new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "--";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "--";
  return date.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "--";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "--";
  return date.toLocaleDateString("en-IN", {
    weekday: "short",
    day: "numeric",
    month: "short",
  });
}

/** Colour classes for a direction. Never green=good / red=bad, just up/down. */
export function directionClass(direction: Direction | null | undefined): string {
  if (direction === "positive") return "text-up";
  if (direction === "negative") return "text-down";
  return "text-ink-500";
}

export function changeClass(change: number | null | undefined): string {
  if (change === null || change === undefined) return "text-ink-500";
  if (change > 0) return "text-up";
  if (change < 0) return "text-down";
  return "text-ink-500";
}

export const SEVERITY_STYLES: Record<Severity, string> = {
  Critical: "bg-down-soft text-down border-down/20",
  "High Attention": "bg-warn-soft text-warn border-warn/20",
  "Worth Watching": "bg-accent-soft text-accent border-accent/20",
  Noise: "bg-ink-100 text-ink-500 border-ink-200",
};

export const CLASSIFICATION_LABEL: Record<string, string> = {
  stock_specific: "Stock-specific",
  sector_wide: "Sector-wide",
  market_wide: "Market-wide",
};

export function familyLabel(family: string): string {
  return (
    {
      price_movement: "Price movement",
      relative_performance: "vs market",
      volume: "Volume",
      volatility: "Volatility",
      gap: "Opening gap",
      news: "News",
    }[family] ?? family.replace(/_/g, " ")
  );
}
