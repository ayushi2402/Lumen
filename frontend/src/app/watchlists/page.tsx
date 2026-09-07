"use client";

/**
 * Watchlist index: create, rename, delete, and jump into a list.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import {
  EmptyState,
  ErrorState,
  LoadingCard,
  SectionHeading,
} from "@/components/ui";
import { api } from "@/lib/api";
import type { Watchlist } from "@/lib/types";

export default function WatchlistsPage() {
  return (
    <AppShell>
      <WatchlistsView />
    </AppShell>
  );
}

function WatchlistsView() {
  const [lists, setLists] = useState<Watchlist[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");

  const load = useCallback(async () => {
    try {
      const next = await api.watchlists();
      setLists(next);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load watchlists.");
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

  const create = async () => {
    const name = newName.trim();
    if (!name) return;
    setCreating(true);
    try {
      await api.createWatchlist(name);
      setNewName("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create watchlist.");
    } finally {
      setCreating(false);
    }
  };

  const remove = async (id: number, name: string) => {
    if (!window.confirm(`Delete "${name}"? This cannot be undone.`)) return;
    try {
      await api.deleteWatchlist(id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete watchlist.");
    }
  };

  if (loading) {
    return (
      <div className="space-y-4">
        <LoadingCard />
        <LoadingCard />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <SectionHeading
        title="Watchlists"
        subtitle="A stock can sit in several lists, with its own priority and notes in each."
      />

      {error ? <ErrorState message={error} onRetry={() => void load()} /> : null}

      <div className="card flex flex-wrap items-center gap-2 p-4">
        <input
          value={newName}
          onChange={(event) => setNewName(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && void create()}
          placeholder="New watchlist name"
          className="min-w-0 flex-1 rounded-lg border border-ink-200 px-3 py-2 text-sm outline-none focus:border-ink-400"
        />
        <button
          type="button"
          onClick={() => void create()}
          disabled={creating || !newName.trim()}
          className="btn-primary !py-2 text-sm"
        >
          {creating ? "Creating..." : "Create"}
        </button>
      </div>

      {lists.length === 0 ? (
        <EmptyState
          title="No watchlists yet"
          description="Create one above, then add the stocks you want LUMEN to watch for meaningful change."
        />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {lists.map((list) => (
            <div key={list.id} className="card card-hover flex flex-col p-5">
              <div className="flex items-start justify-between gap-2">
                <Link
                  href={`/watchlists/${list.id}`}
                  className="text-base font-semibold hover:underline"
                >
                  {list.name}
                </Link>
                {list.is_default ? (
                  <span className="rounded bg-ink-100 px-1.5 py-0.5 text-[10px] font-medium text-ink-500">
                    Default
                  </span>
                ) : null}
              </div>

              <p className="mt-1 text-sm text-ink-500">
                {list.item_count} stock{list.item_count === 1 ? "" : "s"}
              </p>

              <div className="mt-4 flex flex-wrap gap-1">
                {list.items.slice(0, 6).map((item) => (
                  <span
                    key={item.symbol}
                    className="rounded bg-ink-100 px-1.5 py-0.5 text-[11px] text-ink-500"
                  >
                    {item.symbol}
                  </span>
                ))}
                {list.item_count > 6 ? (
                  <span className="px-1 text-[11px] text-ink-400">
                    +{list.item_count - 6}
                  </span>
                ) : null}
              </div>

              <div className="mt-auto flex items-center gap-2 pt-4">
                <Link
                  href={`/watchlists/${list.id}`}
                  className="btn-secondary !py-1.5 text-sm"
                >
                  Open
                </Link>
                <button
                  type="button"
                  onClick={() => void remove(list.id, list.name)}
                  className="btn-ghost !py-1.5 text-sm"
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
