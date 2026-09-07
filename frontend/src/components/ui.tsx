"use client";

/**
 * Small shared primitives: score badges, freshness, data-mode banner, and the
 * loading / empty / error / stale states every page is required to have.
 *
 * The rule these encode: a user must never see a blank screen, and must never
 * see replay data without knowing it is replay.
 */

import Link from "next/link";
import type { ReactNode } from "react";

import { SEVERITY_STYLES, CLASSIFICATION_LABEL } from "@/lib/format";
import type { Confidence, DataMode, Freshness, Severity } from "@/lib/types";

/* ------------------------------------------------------------------ */
/* Score                                                                */
/* ------------------------------------------------------------------ */

export function ScoreBadge({
  score,
  severity,
  size = "md",
}: {
  score: number;
  severity: Severity;
  size?: "sm" | "md" | "lg";
}) {
  const text = size === "lg" ? "text-3xl" : size === "sm" ? "text-base" : "text-xl";
  return (
    <div className="flex items-center gap-2.5">
      <span className={`numeric font-semibold ${text}`}>{Math.round(score)}</span>
      <span
        className={`rounded-md border px-2 py-0.5 text-xs font-medium ${SEVERITY_STYLES[severity]}`}
      >
        {severity}
      </span>
    </div>
  );
}

/**
 * A subtle proportional scale rather than a gauge. It shows where a score sits
 * across the four bands without pretending to be an instrument.
 */
export function ScoreScale({ score }: { score: number }) {
  const clamped = Math.max(0, Math.min(100, score));
  return (
    <div className="space-y-1">
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-ink-100">
        <div
          className="h-full rounded-full bg-ink-800 transition-all duration-500"
          style={{ width: `${clamped}%` }}
        />
      </div>
      <div className="flex justify-between text-[10px] text-ink-400">
        <span>Noise</span>
        <span>Worth watching</span>
        <span>High</span>
        <span>Critical</span>
      </div>
    </div>
  );
}

export function ConfidenceChip({ confidence }: { confidence: Confidence }) {
  const label =
    confidence === "high"
      ? "High confidence"
      : confidence === "medium"
        ? "Medium confidence"
        : "Low confidence";
  return (
    <span className="rounded-md bg-ink-100 px-2 py-0.5 text-xs text-ink-500">{label}</span>
  );
}

export function ClassificationChip({ classification }: { classification: string }) {
  const isSpecific = classification === "stock_specific";
  return (
    <span
      className={`rounded-md px-2 py-0.5 text-xs font-medium ${
        isSpecific ? "bg-accent-soft text-accent" : "bg-ink-100 text-ink-500"
      }`}
    >
      {CLASSIFICATION_LABEL[classification] ?? classification}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/* Data provenance                                                      */
/* ------------------------------------------------------------------ */

export function FreshnessBadge({ freshness }: { freshness: Freshness | null }) {
  if (!freshness) return null;
  const tone =
    freshness.state === "live"
      ? "bg-up-soft text-up"
      : freshness.state === "delayed"
        ? "bg-warn-soft text-warn"
        : "bg-ink-100 text-ink-500";
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs ${tone}`}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {freshness.label}
    </span>
  );
}

/**
 * Always rendered when data is replay or degraded. This is the guarantee that
 * demo data is never mistaken for live market data.
 */
export function DataModeBanner({ mode }: { mode: DataMode | null }) {
  if (!mode) return null;
  if (!mode.is_replay && !mode.degraded) return null;

  const isDemo = mode.is_replay;
  return (
    <div
      className={`flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border px-3 py-2 text-sm ${
        isDemo
          ? "border-warn/30 bg-warn-soft text-warn"
          : "border-ink-200 bg-ink-100 text-ink-500"
      }`}
      role="status"
    >
      <span className="rounded bg-white/70 px-2 py-0.5 text-xs font-semibold uppercase tracking-wide">
        {isDemo ? "Demo / Replay" : "Degraded"}
      </span>
      <span>{mode.reason || mode.label}</span>
      {mode.scenario ? (
        <span className="text-xs opacity-80">
          Scenario: {mode.scenario}
          {mode.total_steps ? ` · step ${mode.step ?? 0}/${mode.total_steps}` : ""}
        </span>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* States                                                               */
/* ------------------------------------------------------------------ */

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-3 text-sm text-ink-500">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-ink-200 border-t-ink-500" />
      {label ?? "Loading..."}
    </div>
  );
}

export function LoadingCard({ lines = 3 }: { lines?: number }) {
  return (
    <div className="card animate-pulse p-5">
      <div className="mb-3 h-4 w-1/3 rounded bg-ink-100" />
      {Array.from({ length: lines }).map((_, index) => (
        <div key={index} className="mb-2 h-3 w-full rounded bg-ink-100 last:w-2/3" />
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="card flex flex-col items-center gap-3 px-6 py-12 text-center">
      <h3 className="text-base font-semibold text-ink-900">{title}</h3>
      <p className="max-w-md text-sm text-ink-500">{description}</p>
      {action}
    </div>
  );
}

export function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="card border-down/20 bg-down-soft/40 px-6 py-8 text-center">
      <h3 className="text-base font-semibold text-down">Something went wrong</h3>
      <p className="mx-auto mt-2 max-w-lg text-sm text-ink-700">{message}</p>
      {onRetry ? (
        <button type="button" onClick={onRetry} className="btn-secondary mt-4">
          Try again
        </button>
      ) : null}
    </div>
  );
}

export function SectionHeading({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
}) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 className="text-lg font-semibold tracking-tight text-ink-900">{title}</h2>
        {subtitle ? <p className="mt-0.5 text-sm text-ink-500">{subtitle}</p> : null}
      </div>
      {action}
    </div>
  );
}

export function StockLink({
  symbol,
  name,
  className,
}: {
  symbol: string;
  name?: string;
  className?: string;
}) {
  return (
    <Link href={`/stocks/${symbol}`} className={className ?? "group"}>
      <span className="font-semibold text-ink-900 group-hover:underline">{symbol}</span>
      {name ? <span className="ml-2 text-sm text-ink-500">{name}</span> : null}
    </Link>
  );
}
