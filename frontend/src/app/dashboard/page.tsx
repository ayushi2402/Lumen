"use client";

/**
 * The dashboard. "3 stocks deserve your attention" is the dominant element.
 *
 * Everything shown here is decided by the backend - ranking, significance,
 * digest baseline, freshness. The page renders that decision; it never
 * re-derives it.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import {
  PrimaryAttentionCard,
  SecondaryAttentionCard,
} from "@/components/AttentionCard";
import { EventCard } from "@/components/EventCard";
import { SinceYouWereAway } from "@/components/SinceYouWereAway";
import {
  DataModeBanner,
  EmptyState,
  ErrorState,
  FreshnessBadge,
  LoadingCard,
  SectionHeading,
} from "@/components/ui";
import { api } from "@/lib/api";
import { percent } from "@/lib/format";
import { currentMode } from "@/lib/mode";
import type { Dashboard } from "@/lib/types";

export default function DashboardPage() {
  return (
    <AppShell>
      <DashboardView />
    </AppShell>
  );
}

function DashboardView() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [focusEventId, setFocusEventId] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await api.dashboard(currentMode());
      setData(next);
      // Cleared only on success: a failed refresh should not blank an error
      // the user is still reading, and this keeps setState out of the
      // effect's synchronous path.
      setError(null);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not load your dashboard.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Run as an async task so no state update happens during the effect's
    // synchronous phase.
    void (async () => {
      await load();
    })();
    // Prices move; a slow refresh keeps the page current without hammering
    // the backend. Intelligence only changes when an event changes.
    const interval = setInterval(load, 60_000);
    return () => clearInterval(interval);
  }, [load]);

  if (loading && !data) {
    return (
      <div className="space-y-6">
        <div className="h-8 w-72 animate-pulse rounded bg-ink-100" />
        <LoadingCard lines={5} />
        <div className="grid gap-4 md:grid-cols-2">
          <LoadingCard />
          <LoadingCard />
        </div>
      </div>
    );
  }

  if (error && !data) {
    return <ErrorState message={error} onRetry={() => void load()} />;
  }

  if (!data) return null;

  const [first, ...rest] = data.attention;
  const focusEvent = focusEventId
    ? data.event_feed.find((event) => event.id === focusEventId)
    : null;

  return (
    <div className="space-y-8">
      <DataModeBanner mode={data.data_mode} />

      {data.warnings.length > 0 ? (
        <div className="rounded-lg border border-ink-200 bg-ink-100 px-3 py-2 text-xs text-ink-500">
          {data.warnings.join(" · ")}
        </div>
      ) : null}

      {/* Hero */}
      <section>
        <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">
              {data.attention_headline}
            </h1>
            <p className="mt-1 text-sm text-ink-500">
              Ranked by how much each change deserves your time, across{" "}
              {data.tracked_symbols} stock{data.tracked_symbols === 1 ? "" : "s"}{" "}
              you follow.
            </p>
          </div>
          <FreshnessBadge freshness={data.freshness} />
        </div>

        {data.attention.length === 0 ? (
          <EmptyState
            title="Nothing needs your attention right now"
            description={
              data.tracked_symbols === 0
                ? "Add some stocks to a watchlist and LUMEN will start watching them for meaningful change."
                : `${data.market.label} LUMEN raises an event only when several independent signals agree, so a quiet result is a real result.`
            }
            action={
              data.tracked_symbols === 0 ? (
                <Link href="/watchlists" className="btn-primary mt-2">
                  Set up a watchlist
                </Link>
              ) : (
                <Link href="/demo" className="btn-secondary mt-2">
                  See it detect something in the demo
                </Link>
              )
            }
          />
        ) : (
          <div className="space-y-4">
            {first ? (
              <PrimaryAttentionCard card={first} onWhy={setFocusEventId} />
            ) : null}
            {rest.length > 0 ? (
              <div className="grid gap-4 md:grid-cols-2">
                {rest.map((card) => (
                  <SecondaryAttentionCard
                    key={card.symbol}
                    card={card}
                    onWhy={setFocusEventId}
                  />
                ))}
              </div>
            ) : null}
          </div>
        )}
      </section>

      {/* Expanded event, opened from a card */}
      {focusEvent ? (
        <section>
          <SectionHeading
            title={`Why ${focusEvent.symbol} matters`}
            action={
              <button
                type="button"
                onClick={() => setFocusEventId(null)}
                className="btn-ghost text-sm"
              >
                Close
              </button>
            }
          />
          <EventCard event={focusEvent} onChanged={load} defaultExpanded />
        </section>
      ) : null}

      {/* Since you were away */}
      <SinceYouWereAway
        digest={data.since_you_were_away}
        market={data.market}
        onChanged={load}
      />

      {/* Market context */}
      <section className="card p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="label">Market context</p>
            <p className="mt-1 text-sm text-ink-900">{data.market.label}</p>
          </div>
          <div className="flex gap-6 text-sm">
            <div>
              <p className="label">NIFTY</p>
              <p className="numeric mt-0.5 font-medium">
                {percent(data.market.benchmark_return)}
              </p>
            </div>
            <div>
              <p className="label">Advancing</p>
              <p className="numeric mt-0.5 font-medium text-up">
                {data.market.advancing}
              </p>
            </div>
            <div>
              <p className="label">Declining</p>
              <p className="numeric mt-0.5 font-medium text-down">
                {data.market.declining}
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* Event feed */}
      <section id="event-feed">
        <SectionHeading
          title="Meaningful changes"
          subtitle="Ranked by significance, most recent first within each tier."
        />
        {data.event_feed.length === 0 ? (
          <EmptyState
            title="No meaningful events"
            description="Prices moved, but nothing crossed LUMEN's threshold for deserving your attention."
          />
        ) : (
          <div className="space-y-3">
            {data.event_feed.map((event) => (
              <EventCard key={event.id} event={event} onChanged={load} />
            ))}
          </div>
        )}
      </section>

      {data.muted_symbols.length > 0 ? (
        <p className="text-xs text-ink-400">
          Muted: {data.muted_symbols.join(", ")} - excluded from attention and
          alerts until the mute expires.
        </p>
      ) : null}
    </div>
  );
}
