"use client";

/**
 * Stock detail: price, score, chart with event markers, and the event
 * timeline. Clicking a marker highlights the matching event below.
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { PriceChart } from "@/components/charts";
import { EventCard } from "@/components/EventCard";
import {
  ClassificationChip,
  ConfidenceChip,
  DataModeBanner,
  EmptyState,
  ErrorState,
  FreshnessBadge,
  LoadingCard,
  ScoreBadge,
  ScoreScale,
  SectionHeading,
} from "@/components/ui";
import { api } from "@/lib/api";
import { changeClass, inr, percent, points } from "@/lib/format";
import { currentMode } from "@/lib/mode";
import type { StockDetail } from "@/lib/types";

export default function StockPage() {
  return (
    <AppShell>
      <StockView />
    </AppShell>
  );
}

function StockView() {
  const params = useParams<{ symbol: string }>();
  const symbol = (params?.symbol ?? "").toUpperCase();

  const [data, setData] = useState<StockDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedPoint, setSelectedPoint] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!symbol) return;
    try {
      const next = await api.stock(symbol, currentMode());
      setData(next);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load this stock.");
    } finally {
      setLoading(false);
    }
  }, [symbol]);

  useEffect(() => {
    // Run as an async task so no state update happens during the effect's
    // synchronous phase.
    void (async () => {
      await load();
    })();
  }, [load]);

  const toggleMute = async () => {
    if (!data) return;
    setBusy(true);
    try {
      if (data.muted) await api.unmute(symbol);
      else await api.mute(symbol, 24);
      await load();
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="space-y-4">
        <LoadingCard lines={2} />
        <LoadingCard lines={6} />
      </div>
    );
  }

  if (error && !data) {
    return <ErrorState message={error} onRetry={() => void load()} />;
  }

  if (!data) return null;

  return (
    <div className="space-y-6">
      <DataModeBanner mode={data.data_mode} />

      {/* Header */}
      <section className="card p-5 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">
              {data.instrument.symbol}
            </h1>
            <p className="mt-0.5 text-sm text-ink-500">
              {data.instrument.name}
              {data.instrument.sector ? ` · ${data.instrument.sector}` : ""}
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <FreshnessBadge freshness={data.freshness} />
              {data.classification ? (
                <ClassificationChip classification={data.classification} />
              ) : null}
              {data.confidence ? <ConfidenceChip confidence={data.confidence} /> : null}
            </div>
          </div>

          <div className="text-right">
            <p className="numeric text-3xl font-semibold">{inr(data.price.price)}</p>
            <p className={`numeric text-base ${changeClass(data.price.change)}`}>
              {percent(data.price.change_percent)}{" "}
              <span className="text-sm text-ink-400">
                ({inr(data.price.change)})
              </span>
            </p>
            <p className="mt-1 text-xs text-ink-400">
              Prev close {inr(data.price.previous_close)}
            </p>
          </div>
        </div>

        {data.score !== null && data.severity ? (
          <div className="mt-5 grid gap-4 border-t border-ink-100 pt-5 sm:grid-cols-[auto,1fr] sm:items-center">
            <div className="min-w-[10rem]">
              <p className="label mb-1">Lumen Score</p>
              <ScoreBadge score={data.score} severity={data.severity} size="lg" />
            </div>
            <ScoreScale score={data.score} />
          </div>
        ) : null}

        <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-ink-100 pt-4">
          <button
            type="button"
            onClick={() => void toggleMute()}
            disabled={busy}
            className="btn-secondary !py-2 text-sm"
          >
            {data.muted ? "Unmute" : "Mute for 24h"}
          </button>
          {data.in_watchlists.length > 0 ? (
            <span className="text-xs text-ink-400">
              In: {data.in_watchlists.join(", ")}
            </span>
          ) : (
            <Link href="/watchlists" className="text-xs text-accent hover:underline">
              Add to a watchlist
            </Link>
          )}
        </div>
      </section>

      {/* Market comparison */}
      <section className="grid gap-3 sm:grid-cols-3">
        <div className="card p-4">
          <p className="label">vs NIFTY</p>
          <p
            className={`numeric mt-1 text-lg font-semibold ${changeClass(
              data.relative_to_benchmark,
            )}`}
          >
            {points(data.relative_to_benchmark)}
          </p>
          <p className="mt-0.5 text-xs text-ink-400">
            Index {percent(data.benchmark_return)}
          </p>
        </div>
        <div className="card p-4">
          <p className="label">vs sector</p>
          <p
            className={`numeric mt-1 text-lg font-semibold ${changeClass(
              data.relative_to_sector,
            )}`}
          >
            {points(data.relative_to_sector)}
          </p>
          <p className="mt-0.5 text-xs text-ink-400">
            Sector {percent(data.sector_return)}
          </p>
        </div>
        <div className="card p-4">
          <p className="label">Volume</p>
          <p className="numeric mt-1 text-lg font-semibold">
            {data.price.volume
              ? data.price.volume.toLocaleString("en-IN")
              : "--"}
          </p>
          <p className="mt-0.5 text-xs text-ink-400">Today so far</p>
        </div>
      </section>

      {/* Chart */}
      <section className="card p-5">
        <SectionHeading
          title="Price and events"
          subtitle="Markers show where LUMEN recorded a significant observation. Click one to highlight it below."
        />
        <PriceChart
          points={data.chart}
          selectedIndex={selectedPoint}
          onSelectPoint={setSelectedPoint}
        />
      </section>

      {/* Current event */}
      {data.current_event ? (
        <section>
          <SectionHeading title="Current event" />
          <EventCard
            event={data.current_event}
            onChanged={load}
            defaultExpanded={selectedPoint !== null}
          />
        </section>
      ) : (
        <EmptyState
          title="No active event"
          description={`${data.instrument.symbol} has not produced a meaningful change that clears LUMEN's threshold.`}
        />
      )}

      {/* Event history for this stock */}
      {data.event_timeline.length > 1 ? (
        <section>
          <SectionHeading
            title="Event history"
            subtitle="Previous meaningful changes for this stock."
          />
          <div className="space-y-3">
            {data.event_timeline.slice(1).map((event) => (
              <EventCard key={event.id} event={event} onChanged={load} />
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
