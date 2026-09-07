"use client";

/**
 * History: everything LUMEN has flagged, including reviewed and dismissed
 * events. Dismissal removes an event from the dashboard, never from the
 * record - this page is where that record lives.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { EventCard } from "@/components/EventCard";
import {
  EmptyState,
  ErrorState,
  LoadingCard,
  SectionHeading,
} from "@/components/ui";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { currentMode } from "@/lib/mode";
import type { EventSummary } from "@/lib/types";

type Filter = "all" | "reviewed" | "dismissed";

export default function HistoryPage() {
  return (
    <AppShell>
      <HistoryView />
    </AppShell>
  );
}

function HistoryView() {
  const [events, setEvents] = useState<EventSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  // `undefined` means the user has not chosen yet, so the most recent day is
  // open by default. Derived below rather than assigned in an effect.
  const [chosenDay, setChosenDay] = useState<string | null | undefined>(undefined);

  const load = useCallback(async () => {
    try {
      const next = await api.eventHistory(currentMode());
      setEvents(next);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load history.");
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
  }, [load]);

  const filtered = useMemo(() => {
    if (filter === "reviewed") return events.filter((event) => event.reviewed);
    if (filter === "dismissed") return events.filter((event) => event.dismissed);
    return events;
  }, [events, filter]);

  // Grouped by trading session so a day can be explored on its own.
  const byDay = useMemo(() => {
    const groups = new Map<string, EventSummary[]>();
    for (const event of filtered) {
      const list = groups.get(event.session_date) ?? [];
      list.push(event);
      groups.set(event.session_date, list);
    }
    return Array.from(groups.entries()).sort((a, b) => b[0].localeCompare(a[0]));
  }, [filtered]);

  const openDay = chosenDay === undefined ? (byDay[0]?.[0] ?? null) : chosenDay;

  if (loading) {
    return (
      <div className="space-y-4">
        <LoadingCard />
        <LoadingCard lines={5} />
      </div>
    );
  }

  if (error) {
    return <ErrorState message={error} onRetry={() => void load()} />;
  }

  return (
    <div className="space-y-6">
      <SectionHeading
        title="History"
        subtitle="Every meaningful change LUMEN has flagged, by trading day."
        action={
          <div className="flex overflow-hidden rounded-lg border border-ink-200">
            {(["all", "reviewed", "dismissed"] as Filter[]).map((value) => (
              <button
                key={value}
                type="button"
                onClick={() => setFilter(value)}
                className={`px-3 py-1.5 text-sm capitalize ${
                  filter === value
                    ? "bg-ink-900 text-white"
                    : "bg-white text-ink-500 hover:bg-ink-50"
                }`}
              >
                {value}
              </button>
            ))}
          </div>
        }
      />

      {byDay.length === 0 ? (
        <EmptyState
          title="No history yet"
          description={
            filter === "all"
              ? "Once LUMEN detects a meaningful change it will be recorded here permanently, even after you dismiss it."
              : `No ${filter} events yet.`
          }
        />
      ) : (
        <div className="space-y-3">
          {byDay.map(([day, dayEvents]) => {
            const isOpen = openDay === day;
            return (
              <div key={day} className="card overflow-hidden">
                <button
                  type="button"
                  onClick={() => setChosenDay(isOpen ? null : day)}
                  className="flex w-full items-center justify-between gap-3 px-5 py-4 text-left hover:bg-ink-50"
                >
                  <span>
                    <span className="block font-medium">{formatDate(day)}</span>
                    <span className="block text-xs text-ink-500">
                      {dayEvents.length} event{dayEvents.length === 1 ? "" : "s"}
                    </span>
                  </span>
                  <span className="text-xs text-ink-400">{isOpen ? "▲" : "▼"}</span>
                </button>

                {isOpen ? (
                  <div className="space-y-3 border-t border-ink-100 bg-ink-50/60 p-4">
                    {dayEvents.map((event) => (
                      <EventCard key={event.id} event={event} onChanged={load} />
                    ))}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
