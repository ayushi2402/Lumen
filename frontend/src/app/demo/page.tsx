"use client";

/**
 * Demo / Replay.
 *
 * The judge flow: pick a scenario, advance the virtual clock, watch prices
 * move, watch LUMEN detect (or correctly not detect) an event, open the
 * explanation, see the evidence and the score.
 *
 * Every surface here is labelled "Demo / Replay". The clock is virtual, so
 * this behaves identically at any hour on any day, with or without market
 * access.
 */

import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { EventCard } from "@/components/EventCard";
import {
  EmptyState,
  ErrorState,
  LoadingCard,
  ScoreBadge,
  SectionHeading,
  Spinner,
} from "@/components/ui";
import { api } from "@/lib/api";
import { changeClass, formatTime, inr, percent } from "@/lib/format";
import { setDemoMode } from "@/lib/mode";
import type { Dashboard, ReplaySession, Scenario } from "@/lib/types";

const SPEEDS = [1, 10, 60] as const;

/** Shown in every state of this page, including while loading. */
function DemoLabel() {
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border border-warn/30 bg-warn-soft px-4 py-3">
      <span className="rounded bg-white/70 px-2 py-0.5 text-xs font-bold uppercase tracking-wide text-warn">
        Demo / Replay
      </span>
      <p className="text-sm text-warn">
        Synthetic scenario data on a virtual clock. This is not live market data.
      </p>
    </div>
  );
}

export default function DemoPage() {
  return (
    <AppShell>
      <DemoView />
    </AppShell>
  );
}

function DemoView() {
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [selected, setSelected] = useState<Scenario | null>(null);
  const [session, setSession] = useState<ReplaySession | null>(null);
  const [snapshot, setSnapshot] = useState<Dashboard | null>(null);
  const [speed, setSpeed] = useState<number>(1);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const list = await api.scenarios();
        setScenarios(list);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Could not load demo scenarios.",
        );
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  /** Refresh the dashboard for the scenario at its current step. */
  const refreshSnapshot = useCallback(
    async (scenarioKey: string, step: number) => {
      try {
        setSnapshot(
          await api.dashboard({ mode: "replay", scenario: scenarioKey, step }),
        );
      } catch {
        /* the replay panel still works without the dashboard view */
      }
    },
    [],
  );

  const start = async (scenario: Scenario) => {
    setBusy(true);
    setError(null);
    setSnapshot(null);
    try {
      const created = await api.startReplay(scenario.key, speed);
      setSelected(scenario);
      setSession(created);
      // Remember the scenario so the rest of the app stays in replay.
      setDemoMode(scenario.key, created.step_index);
      await refreshSnapshot(scenario.key, created.step_index);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start the scenario.");
    } finally {
      setBusy(false);
    }
  };

  const advance = async (steps: number) => {
    if (!session || !selected) return;
    setBusy(true);
    try {
      const updated = await api.stepReplay(session.id, steps);
      setSession(updated);
      setDemoMode(selected.key, updated.step_index);
      await refreshSnapshot(selected.key, updated.step_index);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not advance the replay.");
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    if (!session || !selected) return;
    setBusy(true);
    try {
      const updated = await api.resetReplay(session.id);
      setSession(updated);
      setDemoMode(selected.key, updated.step_index);
      setSnapshot(null);
      await refreshSnapshot(selected.key, 0);
    } finally {
      setBusy(false);
    }
  };

  const changeSpeed = async (next: number) => {
    setSpeed(next);
    if (session) {
      try {
        setSession(await api.setReplaySpeed(session.id, next));
      } catch {
        /* speed is a client-side preference until the next step */
      }
    }
  };

  if (loading) {
    // The demo label is part of the loading state too: at no point should this
    // page be on screen without saying it is replayed data.
    return (
      <div className="space-y-4">
        <DemoLabel />
        <LoadingCard />
        <LoadingCard />
      </div>
    );
  }

  if (error && scenarios.length === 0) {
    return <ErrorState message={error} onRetry={() => window.location.reload()} />;
  }

  const progress = session
    ? Math.round((session.step_index / Math.max(1, session.total_steps)) * 100)
    : 0;

  return (
    <div className="space-y-6">
      <DemoLabel />

      <div>
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">
          Watch LUMEN decide
        </h1>
        <p className="mt-1 max-w-2xl text-sm text-ink-500">
          Each scenario is built to show the difference between{" "}
          <em>something moved</em> and <em>something deserves attention</em>.
          Advance the clock and watch what LUMEN does - including the scenario
          where it correctly reports nothing.
        </p>
      </div>

      {/* Scenario picker */}
      <section>
        <SectionHeading title="Choose a scenario" />
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {scenarios.map((scenario) => {
            const active = selected?.key === scenario.key;
            return (
              <button
                key={scenario.key}
                type="button"
                onClick={() => void start(scenario)}
                disabled={busy}
                className={`card card-hover p-4 text-left transition-colors ${
                  active ? "border-ink-900 ring-1 ring-ink-900" : ""
                }`}
              >
                <p className="text-sm font-semibold">{scenario.name}</p>
                <p className="mt-1.5 text-xs leading-relaxed text-ink-500">
                  {scenario.description}
                </p>
                <p className="mt-2 text-[11px] text-ink-400">
                  {scenario.focus_symbols.join(", ") || "Broad market"}
                </p>
              </button>
            );
          })}
        </div>
      </section>

      {!session ? (
        <EmptyState
          title="Pick a scenario to begin"
          description="Start with 'Sharp decline in a calm market' to see a Critical event, then try 'Broad market selloff' to see LUMEN correctly stay silent."
        />
      ) : null}

      {session && selected ? (
        <>
          {/* Controls */}
          <section className="card p-5">
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div>
                <p className="label">Now playing</p>
                <p className="mt-0.5 font-semibold">{session.scenario_name}</p>
                <p className="numeric mt-0.5 text-xs text-ink-400">
                  Virtual time {formatTime(session.virtual_now)} · step{" "}
                  {session.step_index}/{session.total_steps}
                </p>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <div className="flex overflow-hidden rounded-lg border border-ink-200">
                  {SPEEDS.map((value) => (
                    <button
                      key={value}
                      type="button"
                      onClick={() => void changeSpeed(value)}
                      className={`px-3 py-1.5 text-sm ${
                        speed === value
                          ? "bg-ink-900 text-white"
                          : "bg-white text-ink-500 hover:bg-ink-50"
                      }`}
                    >
                      {value}×
                    </button>
                  ))}
                </div>

                <button
                  type="button"
                  onClick={() => void advance(1)}
                  disabled={busy || session.step_index >= session.total_steps}
                  className="btn-secondary !py-2 text-sm"
                >
                  Step
                </button>
                <button
                  type="button"
                  onClick={() =>
                    void advance(speed === 60 ? session.total_steps : speed)
                  }
                  disabled={busy || session.step_index >= session.total_steps}
                  className="btn-primary !py-2 text-sm"
                >
                  {busy ? "Running..." : `Play ${speed}×`}
                </button>
                <button
                  type="button"
                  onClick={() => void reset()}
                  disabled={busy}
                  className="btn-ghost !py-2 text-sm"
                >
                  Reset
                </button>
              </div>
            </div>

            <div className="mt-4">
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-ink-100">
                <div
                  className="h-full rounded-full bg-ink-800 transition-all duration-300"
                  style={{ width: `${progress}%` }}
                />
              </div>
            </div>

            <p className="mt-4 rounded-lg bg-ink-50 p-3 text-xs leading-relaxed text-ink-500">
              <span className="font-medium text-ink-700">What to watch: </span>
              {selected.teaching_point}
            </p>
          </section>

          {/* What LUMEN detected */}
          <section>
            <SectionHeading
              title="LUMEN detected"
              subtitle={
                session.detected.length === 0
                  ? "Nothing yet. Advance the clock."
                  : `${session.detected.length} event${
                      session.detected.length === 1 ? "" : "s"
                    } so far.`
              }
              action={busy ? <Spinner /> : undefined}
            />

            {session.step_index === 0 ? (
              <EmptyState
                title="The session has not started"
                description="Press Play or Step to advance the virtual clock and let LUMEN observe the market."
              />
            ) : session.detected.length === 0 ? (
              <div className="card border-ink-200 bg-ink-50 p-6">
                <p className="font-medium text-ink-900">
                  Nothing meaningful detected.
                </p>
                <p className="mt-1.5 text-sm text-ink-500">
                  {snapshot?.market.label ??
                    "Prices moved, but no event crossed the threshold."}
                </p>
                <p className="mt-3 text-xs text-ink-400">
                  This is the correct answer, not a missing feature. A tool that
                  always finds something to show is not filtering anything.
                </p>
              </div>
            ) : (
              <div className="space-y-3">
                {session.detected.map((event) => (
                  <EventCard key={event.id} event={event} />
                ))}
              </div>
            )}
          </section>

          {/* Live watchlist prices during the replay */}
          {snapshot ? (
            <section>
              <SectionHeading
                title="Watchlist during this scenario"
                subtitle="Prices update as the virtual clock advances."
              />

              {snapshot.attention.length > 0 ? (
                <div className="mb-3 rounded-lg border border-ink-200 bg-white p-4">
                  <p className="label mb-2">Deserves attention</p>
                  <div className="flex flex-wrap gap-4">
                    {snapshot.attention.map((card) => (
                      <div key={card.symbol} className="flex items-center gap-3">
                        <span className="font-semibold">{card.symbol}</span>
                        <ScoreBadge
                          score={card.score}
                          severity={card.severity}
                          size="sm"
                        />
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              <div className="card divide-y divide-ink-100 overflow-hidden">
                {snapshot.event_feed.length === 0 && snapshot.attention.length === 0 ? (
                  <p className="px-4 py-6 text-center text-sm text-ink-400">
                    {snapshot.market.label}
                  </p>
                ) : null}
                {snapshot.attention.map((card) => (
                  <div
                    key={card.symbol}
                    className="flex items-center justify-between gap-3 px-4 py-3"
                  >
                    <div className="min-w-0">
                      <p className="text-sm font-medium">{card.symbol}</p>
                      <p className="truncate text-xs text-ink-500">{card.name}</p>
                    </div>
                    <div className="text-right">
                      <p className="numeric text-sm font-medium">
                        {inr(card.price.price)}
                      </p>
                      <p
                        className={`numeric text-xs ${changeClass(card.price.change)}`}
                      >
                        {percent(card.price.change_percent)}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          ) : null}
        </>
      ) : null}

      {error ? (
        <p className="rounded-lg border border-down/20 bg-down-soft px-4 py-3 text-sm text-down">
          {error}
        </p>
      ) : null}
    </div>
  );
}
