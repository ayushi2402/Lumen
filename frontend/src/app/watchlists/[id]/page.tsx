"use client";

/**
 * A single watchlist, ranked by attention rather than alphabetically - the
 * point of the product is that the list tells you where to look.
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { Sparkline } from "@/components/charts";
import {
  EmptyState,
  ErrorState,
  LoadingCard,
  ScoreBadge,
  SectionHeading,
} from "@/components/ui";
import { api } from "@/lib/api";
import { changeClass, inr, percent } from "@/lib/format";
import { currentMode } from "@/lib/mode";
import type { SearchResult, Watchlist, WatchlistItem } from "@/lib/types";

export default function WatchlistDetailPage() {
  return (
    <AppShell>
      <WatchlistDetailView />
    </AppShell>
  );
}

function WatchlistDetailView() {
  const params = useParams<{ id: string }>();
  const watchlistId = Number(params?.id);

  const [list, setList] = useState<Watchlist | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [noteDraft, setNoteDraft] = useState("");

  const load = useCallback(async () => {
    if (!Number.isFinite(watchlistId)) return;
    try {
      const next = await api.watchlist(watchlistId, currentMode());
      setList(next);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load this watchlist.");
    } finally {
      setLoading(false);
    }
  }, [watchlistId]);

  useEffect(() => {
    // Run as an async task so no state update happens during the effect's
    // synchronous phase.
    void (async () => {
      await load();
    })();
  }, [load]);

  const runSearch = async (value: string) => {
    setQuery(value);
    if (value.trim().length < 2) {
      setResults([]);
      return;
    }
    try {
      setResults(await api.search(value.trim(), currentMode()));
    } catch {
      setResults([]);
    }
  };

  const add = async (symbol: string) => {
    try {
      await api.addToWatchlist(watchlistId, symbol);
      setQuery("");
      setResults([]);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add that stock.");
    }
  };

  const update = async (
    symbol: string,
    body: { priority?: number; starred?: boolean; notes?: string },
  ) => {
    try {
      await api.updateWatchlistItem(watchlistId, symbol, body);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update that stock.");
    }
  };

  if (loading) {
    return (
      <div className="space-y-4">
        <LoadingCard />
        <LoadingCard lines={6} />
      </div>
    );
  }

  if (error && !list) {
    return <ErrorState message={error} onRetry={() => void load()} />;
  }

  if (!list) return null;

  return (
    <div className="space-y-6">
      <SectionHeading
        title={list.name}
        subtitle={`${list.item_count} stock${
          list.item_count === 1 ? "" : "s"
        }, ranked by how much they deserve your attention.`}
        action={
          <Link href="/watchlists" className="btn-ghost text-sm">
            All watchlists
          </Link>
        }
      />

      {error ? (
        <p className="rounded-lg border border-down/20 bg-down-soft px-4 py-2 text-sm text-down">
          {error}
        </p>
      ) : null}

      {/* Add a stock */}
      <div className="card p-4">
        <input
          value={query}
          onChange={(event) => void runSearch(event.target.value)}
          placeholder="Add a stock by name or ticker..."
          className="w-full rounded-lg border border-ink-200 px-3 py-2 text-sm outline-none focus:border-ink-400"
        />
        {results.length > 0 ? (
          <ul className="mt-2 max-h-56 divide-y divide-ink-100 overflow-auto rounded-lg border border-ink-200">
            {results.map((result) => (
              <li
                key={result.symbol}
                className="flex items-center justify-between gap-3 px-3 py-2"
              >
                <span className="min-w-0">
                  <span className="text-sm font-medium">{result.symbol}</span>
                  <span className="ml-2 truncate text-xs text-ink-500">
                    {result.name}
                  </span>
                </span>
                <span className="flex shrink-0 items-center gap-3">
                  <span className="numeric text-sm">
                    {result.price !== null ? inr(result.price) : "--"}
                  </span>
                  <button
                    type="button"
                    onClick={() => void add(result.symbol)}
                    disabled={!result.is_supported}
                    className="btn-secondary !px-3 !py-1 text-xs"
                    title={
                      result.is_supported
                        ? "Add to this watchlist"
                        : "LUMEN does not support this instrument yet"
                    }
                  >
                    {result.is_supported ? "Add" : "Unsupported"}
                  </button>
                </span>
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      {list.items.length === 0 ? (
        <EmptyState
          title="This watchlist is empty"
          description="Search above to add stocks. LUMEN will start watching them for meaningful change."
        />
      ) : (
        <div className="card divide-y divide-ink-100 overflow-hidden">
          {list.items.map((item) => (
            <WatchlistRow
              key={item.symbol}
              item={item}
              editing={editing === item.symbol}
              noteDraft={noteDraft}
              onNoteDraft={setNoteDraft}
              onEdit={(symbol) => {
                setEditing(symbol);
                setNoteDraft(item.notes ?? "");
              }}
              onCancelEdit={() => setEditing(null)}
              onUpdate={update}
              onRemove={async (symbol) => {
                await api.removeFromWatchlist(watchlistId, symbol).catch(() => undefined);
                await load();
              }}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function WatchlistRow({
  item,
  editing,
  noteDraft,
  onNoteDraft,
  onEdit,
  onCancelEdit,
  onUpdate,
  onRemove,
}: {
  item: WatchlistItem;
  editing: boolean;
  noteDraft: string;
  onNoteDraft: (value: string) => void;
  onEdit: (symbol: string) => void;
  onCancelEdit: () => void;
  onUpdate: (
    symbol: string,
    body: { priority?: number; starred?: boolean; notes?: string },
  ) => Promise<void>;
  onRemove: (symbol: string) => Promise<void>;
}) {
  return (
    <div className="p-4">
      <div className="flex flex-wrap items-center gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Link
              href={`/stocks/${item.symbol}`}
              className="font-semibold hover:underline"
            >
              {item.symbol}
            </Link>
            {item.starred ? <span className="text-warn">★</span> : null}
            {item.muted ? (
              <span className="rounded bg-ink-100 px-1.5 py-0.5 text-[10px] text-ink-500">
                Muted
              </span>
            ) : null}
            {item.priority > 0 ? (
              <span className="rounded bg-accent-soft px-1.5 py-0.5 text-[10px] text-accent">
                P{item.priority}
              </span>
            ) : null}
          </div>
          <p className="mt-0.5 truncate text-xs text-ink-500">{item.name}</p>
          {item.latest_reason ? (
            <p className="mt-1.5 line-clamp-2 text-sm text-ink-700">
              {item.latest_reason}
            </p>
          ) : (
            <p className="mt-1.5 text-sm text-ink-400">
              Nothing meaningful recently.
            </p>
          )}
          {item.notes ? (
            <p className="mt-1 text-xs italic text-ink-400">Note: {item.notes}</p>
          ) : null}
        </div>

        <Sparkline values={item.sparkline} className="hidden h-8 w-24 sm:block" />

        <div className="text-right">
          <p className="numeric text-sm font-semibold">
            {item.price ? inr(item.price.price) : "--"}
          </p>
          <p className={`numeric text-xs ${changeClass(item.price?.change)}`}>
            {item.price ? percent(item.price.change_percent) : ""}
          </p>
        </div>

        <div className="w-32 shrink-0">
          {item.score !== null && item.score !== undefined && item.severity ? (
            <ScoreBadge score={item.score} severity={item.severity} size="sm" />
          ) : (
            <span className="text-xs text-ink-400">Not scored</span>
          )}
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => void onUpdate(item.symbol, { starred: !item.starred })}
          className="btn-ghost !px-2 !py-1 text-xs"
        >
          {item.starred ? "Unstar" : "Star"}
        </button>
        <select
          value={item.priority}
          onChange={(event) =>
            void onUpdate(item.symbol, { priority: Number(event.target.value) })
          }
          className="rounded-md border border-ink-200 px-2 py-1 text-xs"
          aria-label={`Priority for ${item.symbol}`}
        >
          <option value={0}>No priority</option>
          <option value={1}>Priority 1</option>
          <option value={2}>Priority 2</option>
          <option value={3}>Priority 3</option>
        </select>

        {editing ? (
          <span className="flex flex-1 items-center gap-2">
            <input
              value={noteDraft}
              onChange={(event) => onNoteDraft(event.target.value)}
              placeholder="Add a note..."
              className="min-w-0 flex-1 rounded-md border border-ink-200 px-2 py-1 text-xs"
            />
            <button
              type="button"
              onClick={async () => {
                await onUpdate(item.symbol, { notes: noteDraft });
                onCancelEdit();
              }}
              className="btn-secondary !px-2 !py-1 text-xs"
            >
              Save
            </button>
            <button type="button" onClick={onCancelEdit} className="btn-ghost !px-2 !py-1 text-xs">
              Cancel
            </button>
          </span>
        ) : (
          <button
            type="button"
            onClick={() => onEdit(item.symbol)}
            className="btn-ghost !px-2 !py-1 text-xs"
          >
            {item.notes ? "Edit note" : "Add note"}
          </button>
        )}

        <div className="flex-1" />
        <button
          type="button"
          onClick={() => void onRemove(item.symbol)}
          className="btn-ghost !px-2 !py-1 text-xs"
        >
          Remove
        </button>
      </div>
    </div>
  );
}
