"use client";

/**
 * The "3 stocks deserve your attention" cards.
 *
 * Rank 1 gets a large card; ranks 2 and 3 are compact. Every card shows what
 * changed and why it matters - and never a BUY/SELL/HOLD, because LUMEN does
 * not make recommendations.
 */

import Link from "next/link";

import { changeClass, inr, percent } from "@/lib/format";
import type { AttentionCard as AttentionCardType } from "@/lib/types";

import { ClassificationChip, ConfidenceChip, ScoreBadge, ScoreScale } from "./ui";

export function PrimaryAttentionCard({
  card,
  onWhy,
}: {
  card: AttentionCardType;
  onWhy?: (eventId: number) => void;
}) {
  return (
    <article className="card card-hover flex flex-col gap-5 p-6 md:p-7">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="rounded bg-ink-900 px-1.5 py-0.5 text-[10px] font-bold text-white">
              #1
            </span>
            <Link
              href={`/stocks/${card.symbol}`}
              className="text-2xl font-semibold tracking-tight hover:underline md:text-3xl"
            >
              {card.symbol}
            </Link>
          </div>
          <p className="mt-1 text-sm text-ink-500">{card.name}</p>
        </div>

        <div className="text-right">
          <div className="numeric text-2xl font-semibold md:text-3xl">
            {inr(card.price.price)}
          </div>
          <div className={`numeric text-base font-medium ${changeClass(card.price.change)}`}>
            {percent(card.price.change_percent)}
          </div>
        </div>
      </header>

      <div className="grid gap-4 sm:grid-cols-[auto,1fr] sm:items-center">
        <div className="min-w-[9rem]">
          <p className="label mb-1">Lumen Score</p>
          <ScoreBadge score={card.score} severity={card.severity} size="lg" />
        </div>
        <ScoreScale score={card.score} />
      </div>

      <div className="space-y-3 border-t border-ink-100 pt-4">
        <p className="text-lg font-medium leading-snug text-ink-900">{card.primary_reason}</p>
        <p className="text-sm leading-relaxed text-ink-500">{card.why_it_matters}</p>
      </div>

      <footer className="flex flex-wrap items-center gap-2">
        <ClassificationChip classification={card.classification} />
        <ConfidenceChip confidence={card.confidence} />
        <div className="flex-1" />
        {card.event_id ? (
          <button
            type="button"
            onClick={() => onWhy?.(card.event_id as number)}
            className="btn-primary"
          >
            Why it matters →
          </button>
        ) : (
          <Link href={`/stocks/${card.symbol}`} className="btn-secondary">
            View stock →
          </Link>
        )}
      </footer>
    </article>
  );
}

export function SecondaryAttentionCard({
  card,
  onWhy,
}: {
  card: AttentionCardType;
  onWhy?: (eventId: number) => void;
}) {
  return (
    <article className="card card-hover flex flex-col gap-4 p-5">
      <header className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="rounded bg-ink-100 px-1.5 py-0.5 text-[10px] font-bold text-ink-500">
              #{card.rank}
            </span>
            <Link
              href={`/stocks/${card.symbol}`}
              className="text-lg font-semibold hover:underline"
            >
              {card.symbol}
            </Link>
          </div>
          <p className="mt-0.5 truncate text-xs text-ink-500">{card.name}</p>
        </div>
        <div className="text-right">
          <div className="numeric text-base font-semibold">{inr(card.price.price)}</div>
          <div className={`numeric text-sm ${changeClass(card.price.change)}`}>
            {percent(card.price.change_percent)}
          </div>
        </div>
      </header>

      <ScoreBadge score={card.score} severity={card.severity} />

      <p className="line-clamp-3 text-sm leading-relaxed text-ink-700">
        {card.primary_reason}
      </p>

      <footer className="mt-auto flex items-center justify-between gap-2">
        <ClassificationChip classification={card.classification} />
        {card.event_id ? (
          <button
            type="button"
            onClick={() => onWhy?.(card.event_id as number)}
            className="text-sm font-medium text-accent hover:underline"
          >
            Why →
          </button>
        ) : null}
      </footer>
    </article>
  );
}
