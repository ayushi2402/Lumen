"use client";

/**
 * "Since you last checked".
 *
 * Short absence: the top changes plus a compact remainder.
 * Multi-session absence: an overall summary with per-trading-day drilldown.
 *
 * The summary text comes from the backend, generated from the actual events -
 * nothing about the market is asserted in this file.
 */

import { useState } from "react";

import { api } from "@/lib/api";
import { formatDate, timeAgo } from "@/lib/format";
import type { MarketContext, SinceAway } from "@/lib/types";

import { EventCard } from "./EventCard";
import { ScoreBadge } from "./ui";

export function SinceYouWereAway({
  digest,
  market,
  onChanged,
}: {
  digest: SinceAway;
  market?: MarketContext | null;
  onChanged?: () => void;
}) {
  const [openDay, setOpenDay] = useState<string | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);

  const nothingHappened = digest.top_events.length === 0;

  return (
    <section className="card p-5 sm:p-6">
      <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Since you last checked</h2>
          <p className="mt-0.5 text-sm text-ink-400">
            {timeAgo(digest.baseline_at)}
            {digest.sessions_missed > 0
              ? ` · ${digest.sessions_missed} trading session${
                  digest.sessions_missed === 1 ? "" : "s"
                }`
              : ""}
          </p>
        </div>

        {!acknowledged && !nothingHappened ? (
          <button
            type="button"
            onClick={async () => {
              await api.markDigestReviewed().catch(() => undefined);
              setAcknowledged(true);
              onChanged?.();
            }}
            className="btn-secondary !py-2 text-sm"
            title="Sets a new baseline for what counts as new next time"
          >
            Mark as reviewed
          </button>
        ) : null}
      </header>

      <p className="text-base leading-relaxed text-ink-900">{digest.summary}</p>

      {nothingHappened ? (
        <div className="mt-4 rounded-lg border border-ink-200 bg-ink-50 p-4">
          <p className="text-sm font-medium text-ink-900">Nothing important changed.</p>
          {market ? (
            <p className="mt-1 text-sm text-ink-500">{market.label}</p>
          ) : null}
          <p className="mt-2 text-xs text-ink-400">
            LUMEN only raises an event when several independent signals agree.
            Ordinary drift is filtered out on purpose.
          </p>
        </div>
      ) : null}

      {/* Multi-session: day-by-day drilldown */}
      {digest.is_multi_session && digest.days.length > 0 ? (
        <div className="mt-5 space-y-2">
          <p className="label">By trading day</p>
          {digest.days.map((day) => {
            const isOpen = openDay === day.session_date;
            return (
              <div key={day.session_date} className="rounded-lg border border-ink-200">
                <button
                  type="button"
                  onClick={() => setOpenDay(isOpen ? null : day.session_date)}
                  className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-ink-50"
                >
                  <span className="min-w-0">
                    <span className="block text-sm font-medium">
                      {formatDate(day.session_date)}
                    </span>
                    <span className="block truncate text-xs text-ink-500">
                      {day.headline}
                    </span>
                  </span>
                  <span className="shrink-0 text-xs text-ink-400">
                    {day.event_count} event{day.event_count === 1 ? "" : "s"}{" "}
                    {isOpen ? "▲" : "▼"}
                  </span>
                </button>

                {isOpen ? (
                  <div className="space-y-3 border-t border-ink-100 bg-ink-50/60 p-4">
                    {day.events.length === 0 ? (
                      <p className="text-sm text-ink-400">
                        Nothing meaningful on this day.
                      </p>
                    ) : (
                      day.events.map((event) => (
                        <EventCard key={event.id} event={event} onChanged={onChanged} />
                      ))
                    )}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : null}

      {/* Short absence: top changes, compact remainder */}
      {!digest.is_multi_session && digest.top_events.length > 0 ? (
        <div className="mt-5 space-y-3">
          {digest.top_events.map((event) => (
            <EventCard key={event.id} event={event} onChanged={onChanged} />
          ))}

          {digest.remainder_count > 0 ? (
            <a
              href="#event-feed"
              className="inline-block text-sm font-medium text-accent hover:underline"
            >
              {digest.remainder_count} more change
              {digest.remainder_count === 1 ? "" : "s"} in the feed below
            </a>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

/** Compact one-line row used for the digest remainder. */
export function CompactEventRow({
  symbol,
  headline,
  score,
  severity,
}: {
  symbol: string;
  headline: string;
  score: number;
  severity: SinceAway["top_events"][number]["severity"];
}) {
  return (
    <li className="flex items-center justify-between gap-3 px-4 py-2.5">
      <span className="min-w-0">
        <span className="text-sm font-medium">{symbol}</span>
        <span className="ml-2 truncate text-xs text-ink-500">{headline}</span>
      </span>
      <ScoreBadge score={score} severity={severity} size="sm" />
    </li>
  );
}
