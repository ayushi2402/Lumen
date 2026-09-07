"use client";

/**
 * Event feed card and the expandable detail beneath it.
 *
 * Collapsed, a card answers only two questions: what changed, and why it
 * matters. Supporting signals, evidence and the timeline live one level down,
 * and the raw score arithmetic lives behind "How LUMEN calculated this" - the
 * card must never open as a wall of numbers.
 */

import { useCallback, useState } from "react";

import { api } from "@/lib/api";
import {
  changeClass,
  familyLabel,
  formatTime,
  percent,
  timeAgo,
} from "@/lib/format";
import type { EventDetail, EventSummary } from "@/lib/types";

import {
  ClassificationChip,
  ConfidenceChip,
  ScoreBadge,
  ScoreScale,
  Spinner,
  StockLink,
} from "./ui";

export function EventCard({
  event,
  onChanged,
  defaultExpanded = false,
}: {
  event: EventSummary;
  onChanged?: () => void;
  defaultExpanded?: boolean;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [detail, setDetail] = useState<EventDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showBreakdown, setShowBreakdown] = useState(false);
  const [busy, setBusy] = useState(false);

  const loadDetail = useCallback(async () => {
    if (detail || loading) return;
    setLoading(true);
    setError(null);
    try {
      const loaded = await api.event(event.id);
      setDetail(loaded);
      // Opening "Why" is a strong interest signal; the backend caps how much
      // it can influence ranking.
      void api.openWhy(event.id).catch(() => undefined);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load this event.");
    } finally {
      setLoading(false);
    }
  }, [detail, event.id, loading]);

  const toggle = () => {
    const next = !expanded;
    setExpanded(next);
    if (next) void loadDetail();
  };

  const act = async (action: "review" | "dismiss" | "more") => {
    setBusy(true);
    try {
      if (action === "review") await api.reviewEvent(event.id);
      if (action === "dismiss") await api.dismissEvent(event.id);
      if (action === "more") await api.moreLikeThis(event.id);
      onChanged?.();
    } catch {
      /* a failed interaction must not break the feed */
    } finally {
      setBusy(false);
    }
  };

  return (
    <article className="card overflow-hidden">
      <div className="flex flex-col gap-3 p-5">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <StockLink symbol={event.symbol} name={event.name} />
            <p className="mt-1 text-xs text-ink-400">
              {timeAgo(event.last_updated_at)} · {event.observation_count} update
              {event.observation_count === 1 ? "" : "s"}
              {event.reviewed ? " · reviewed" : ""}
            </p>
          </div>
          <ScoreBadge score={event.score} severity={event.severity} size="sm" />
        </header>

        {/* WHAT CHANGED */}
        <div>
          <p className="label">What changed</p>
          <p className="mt-1 font-medium leading-snug text-ink-900">{event.headline}</p>
        </div>

        {/* WHY IT MATTERS */}
        <div>
          <p className="label">Why it matters</p>
          <p className="mt-1 text-sm leading-relaxed text-ink-700">{event.primary_reason}</p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <ClassificationChip classification={event.classification} />
          <ConfidenceChip confidence={event.confidence} />
        </div>

        <div className="flex flex-wrap items-center gap-2 pt-1">
          <button type="button" onClick={toggle} className="btn-secondary !py-2 text-sm">
            {expanded ? "Hide details" : "Why it matters →"}
          </button>
          <div className="flex-1" />
          <button
            type="button"
            disabled={busy}
            onClick={() => act("more")}
            className="btn-ghost !py-2 text-sm"
            title="Show me more events like this"
          >
            More like this
          </button>
          {!event.reviewed ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => act("review")}
              className="btn-ghost !py-2 text-sm"
            >
              Mark reviewed
            </button>
          ) : null}
          <button
            type="button"
            disabled={busy}
            onClick={() => act("dismiss")}
            className="btn-ghost !py-2 text-sm"
            title="Removed from your dashboard, kept in history"
          >
            Dismiss
          </button>
        </div>
      </div>

      {expanded ? (
        <div className="border-t border-ink-100 bg-ink-50/60 p-5">
          {loading ? <Spinner label="Loading explanation..." /> : null}
          {error ? <p className="text-sm text-down">{error}</p> : null}

          {detail ? (
            <div className="space-y-5">
              {/* Explanation */}
              <section>
                <p className="label mb-1.5">Explanation</p>
                <p className="text-sm leading-relaxed text-ink-900">
                  {detail.explanation.text}
                </p>
                <p className="mt-1.5 text-xs text-ink-400">
                  {detail.explanation.source === "llm"
                    ? `AI-assisted phrasing of verified facts (${detail.explanation.model ?? "model"}).`
                    : "Generated from verified market facts."}
                </p>
              </section>

              {detail.market_context_note ? (
                <section className="rounded-lg border border-ink-200 bg-white p-3">
                  <p className="label mb-1">Market context</p>
                  <p className="text-sm text-ink-700">{detail.market_context_note}</p>
                </section>
              ) : null}

              {/* Supporting signals */}
              {detail.evidence.length > 0 ? (
                <section>
                  <p className="label mb-1.5">Supporting signals</p>
                  <ul className="space-y-1.5">
                    {detail.evidence.map((item, index) => (
                      <li key={index} className="flex gap-2 text-sm text-ink-700">
                        <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-ink-300" />
                        {item}
                      </li>
                    ))}
                  </ul>
                </section>
              ) : null}

              {/* Unavailable evidence is shown too - missing data is information */}
              {detail.signals.some((s) => s.availability !== "available") ? (
                <section>
                  <p className="label mb-1.5">Not measurable</p>
                  <ul className="space-y-1">
                    {detail.signals
                      .filter((s) => s.availability !== "available")
                      .map((signal) => (
                        <li key={signal.type} className="text-xs text-ink-400">
                          {familyLabel(signal.family)}: {signal.evidence}
                        </li>
                      ))}
                  </ul>
                </section>
              ) : null}

              {/* Timeline */}
              {detail.timeline.length > 0 ? (
                <section>
                  <p className="label mb-1.5">Timeline</p>
                  <ol className="space-y-1.5">
                    {detail.timeline.map((point, index) => (
                      <li
                        key={index}
                        className="flex items-center gap-3 text-sm text-ink-700"
                      >
                        <span className="numeric w-14 shrink-0 text-xs text-ink-400">
                          {formatTime(point.observed_at)}
                        </span>
                        <span className="numeric w-10 font-medium">
                          {Math.round(point.score)}
                        </span>
                        <span className="text-xs text-ink-500">{point.severity}</span>
                        {point.price ? (
                          <span className="numeric ml-auto text-xs text-ink-400">
                            ₹{point.price.toFixed(2)}
                          </span>
                        ) : null}
                      </li>
                    ))}
                  </ol>
                </section>
              ) : null}

              {/* Score breakdown - collapsed by default */}
              <section>
                <button
                  type="button"
                  onClick={() => {
                    const next = !showBreakdown;
                    setShowBreakdown(next);
                    if (next) void api.openBreakdown(event.id).catch(() => undefined);
                  }}
                  className="text-sm font-medium text-accent hover:underline"
                >
                  {showBreakdown ? "Hide" : "How LUMEN calculated this"}
                </button>

                {showBreakdown ? (
                  <ScoreBreakdownPanel detail={detail} />
                ) : null}
              </section>
            </div>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

export function ScoreBreakdownPanel({ detail }: { detail: EventDetail }) {
  const score = detail.score_detail;
  const max = Math.max(1, ...Object.values(score.breakdown));

  return (
    <div className="mt-3 space-y-4 rounded-lg border border-ink-200 bg-white p-4">
      <div>
        <p className="text-sm text-ink-700">{score.summary}</p>
      </div>

      <ScoreScale score={score.final_score} />

      <div className="space-y-2">
        {Object.entries(score.breakdown).map(([family, contribution]) => (
          <div key={family} className="flex items-center gap-3">
            <span className="w-32 shrink-0 text-xs text-ink-500">{familyLabel(family)}</span>
            <div className="h-2 flex-1 overflow-hidden rounded-full bg-ink-100">
              <div
                className={`h-full rounded-full ${
                  contribution > 0 ? "bg-ink-700" : "bg-ink-200"
                }`}
                style={{ width: `${(contribution / max) * 100}%` }}
              />
            </div>
            <span className="numeric w-10 text-right text-xs text-ink-500">
              {contribution.toFixed(1)}
            </span>
          </div>
        ))}
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 border-t border-ink-100 pt-3 text-sm sm:grid-cols-3">
        <div>
          <dt className="label">Objective</dt>
          <dd className="numeric font-medium">{score.objective_score.toFixed(1)}</dd>
        </div>
        <div>
          <dt className="label">Personalized</dt>
          <dd
            className={`numeric font-medium ${changeClass(score.personalization_adjustment)}`}
          >
            {score.personalization_adjustment >= 0 ? "+" : ""}
            {score.personalization_adjustment.toFixed(1)}
          </dd>
        </div>
        <div>
          <dt className="label">Final</dt>
          <dd className="numeric font-medium">{score.final_score.toFixed(1)}</dd>
        </div>
      </dl>

      {score.confidence_report.reasons.length > 0 ? (
        <div className="border-t border-ink-100 pt-3">
          <p className="label mb-1">Confidence: {score.confidence}</p>
          <ul className="space-y-0.5">
            {score.confidence_report.reasons.map((reason, index) => (
              <li key={index} className="text-xs text-ink-500">
                {reason}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <p className="border-t border-ink-100 pt-3 text-xs text-ink-400">
        Personalization is capped and can only reorder events that are already
        significant. LUMEN provides information, not investment advice.
      </p>
    </div>
  );
}

export function EventFeed({
  events,
  onChanged,
  emptyMessage = "No meaningful events right now.",
}: {
  events: EventSummary[];
  onChanged?: () => void;
  emptyMessage?: string;
}) {
  if (events.length === 0) {
    return (
      <div className="card px-6 py-10 text-center">
        <p className="text-sm text-ink-500">{emptyMessage}</p>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      {events.map((event) => (
        <EventCard key={event.id} event={event} onChanged={onChanged} />
      ))}
    </div>
  );
}

export function ChangePill({ value }: { value: number | null | undefined }) {
  return (
    <span className={`numeric text-sm font-medium ${changeClass(value)}`}>
      {percent(value)}
    </span>
  );
}
