"use client";

/**
 * Landing page.
 *
 * The value proposition is the intelligence layer, not the model. AI appears
 * as one component of the explanation step - never as the headline claim.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { api, setSession } from "@/lib/api";

export default function LandingPage() {
  const router = useRouter();
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const tryLumen = async () => {
    setStarting(true);
    setError(null);
    try {
      const session = await api.guestLogin();
      setSession(session);
      router.push("/demo");
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Could not start a demo session. Is the backend running?",
      );
      setStarting(false);
    }
  };

  return (
    <div className="min-h-screen bg-white">
      {/* Nav */}
      <header className="border-b border-ink-100">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <span className="text-lg font-semibold tracking-tight">LUMEN</span>
          <div className="flex items-center gap-2">
            <a href="#how-it-works" className="btn-ghost text-sm">
              How it works
            </a>
            <Link href="/login" className="btn-secondary text-sm">
              Sign in
            </Link>
          </div>
        </div>
      </header>

      {/* Hero */}
      <section className="mx-auto max-w-6xl px-6 pb-16 pt-16 sm:pt-24">
        <div className="max-w-3xl">
          <p className="label mb-4">Market change intelligence</p>
          <h1 className="text-4xl font-semibold leading-[1.1] tracking-tight sm:text-6xl">
            See what changed.
            <br />
            Understand why.
            <br />
            <span className="text-ink-400">Know what matters.</span>
          </h1>

          <p className="mt-7 max-w-2xl text-lg leading-relaxed text-ink-500">
            You were away from the market. Something moved. LUMEN tells you
            which of the stocks you follow actually deserve your attention -
            and, just as importantly, which moves were only the market moving.
          </p>

          <div className="mt-9 flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={tryLumen}
              disabled={starting}
              className="btn-primary px-6 py-3 text-base"
            >
              {starting ? "Starting..." : "See what changed"}
            </button>
            <a href="#how-it-works" className="btn-secondary px-6 py-3 text-base">
              How it works
            </a>
          </div>

          <p className="mt-4 text-sm text-ink-400">
            No sign-up required. The demo runs on a deterministic market replay.
          </p>

          {error ? (
            <p className="mt-4 rounded-lg border border-down/20 bg-down-soft px-4 py-3 text-sm text-down">
              {error}
            </p>
          ) : null}
        </div>
      </section>

      {/* The core distinction */}
      <section className="border-y border-ink-100 bg-ink-50">
        <div className="mx-auto max-w-6xl px-6 py-16">
          <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">
            A 4% fall is not always news.
          </h2>
          <p className="mt-3 max-w-2xl text-ink-500">
            Most tools sort by percentage change. That surfaces whatever moved
            most, which on a bad day is simply everything. LUMEN asks a
            different question.
          </p>

          <div className="mt-10 grid gap-4 md:grid-cols-2">
            <article className="card p-6">
              <span className="rounded-md bg-ink-100 px-2 py-1 text-xs font-medium text-ink-500">
                Not an event
              </span>
              <h3 className="mt-4 text-lg font-semibold">
                A stock falls 4%. So did the index.
              </h3>
              <p className="mt-2 text-sm leading-relaxed text-ink-500">
                There is almost no excess return here and nothing specific to
                explain. LUMEN reports it as market context and does not
                interrupt you.
              </p>
            </article>

            <article className="card border-down/20 p-6">
              <span className="rounded-md bg-down-soft px-2 py-1 text-xs font-medium text-down">
                Critical
              </span>
              <h3 className="mt-4 text-lg font-semibold">
                A stock falls 4%. The index is flat.
              </h3>
              <p className="mt-2 text-sm leading-relaxed text-ink-500">
                Heavy volume against its own normal, volatility well above its
                usual range, relevant news nearby. Several independent signals
                agree - this one is worth your time.
              </p>
            </article>
          </div>
        </div>
      </section>

      {/* How it works */}
      <section id="how-it-works" className="mx-auto max-w-6xl px-6 py-16">
        <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">
          How LUMEN decides
        </h2>

        <div className="mt-10 grid gap-8 md:grid-cols-3">
          <div>
            <span className="numeric text-sm font-semibold text-ink-300">01</span>
            <h3 className="mt-2 text-lg font-semibold">Detect</h3>
            <p className="mt-2 text-sm leading-relaxed text-ink-500">
              Six independent families of evidence: price movement measured
              against the stock&apos;s own history, performance relative to
              NIFTY and to its sector, volume against its normal for that time
              of day, volatility, opening gaps, and related news.
            </p>
          </div>
          <div>
            <span className="numeric text-sm font-semibold text-ink-300">02</span>
            <h3 className="mt-2 text-lg font-semibold">Score</h3>
            <p className="mt-2 text-sm leading-relaxed text-ink-500">
              Each event gets a Lumen Score from 0 to 100 with a plain label -
              Noise, Worth Watching, High Attention, Critical. No single signal
              can carry an event on its own; corroboration is what makes
              something significant.
            </p>
          </div>
          <div>
            <span className="numeric text-sm font-semibold text-ink-300">03</span>
            <h3 className="mt-2 text-lg font-semibold">Explain</h3>
            <p className="mt-2 text-sm leading-relaxed text-ink-500">
              Every score opens up into the evidence behind it. Explanations
              state what was measured and distinguish coincidence from cause -
              if LUMEN cannot establish why, it says so.
            </p>
          </div>
        </div>

        <div className="mt-12 grid gap-4 sm:grid-cols-3">
          <div className="card p-5">
            <p className="label">Lumen Score</p>
            <p className="mt-2 text-sm text-ink-500">
              A 0-100 measure of how much an event deserves your attention,
              combining several independent signals. Direction and significance
              are kept separate: a large rise and a large fall are equally
              significant.
            </p>
          </div>
          <div className="card p-5">
            <p className="label">Where AI fits</p>
            <p className="mt-2 text-sm text-ink-500">
              Detection and scoring are deterministic. A language model only
              rephrases facts LUMEN has already established - it never decides
              whether something happened, and never changes a score.
            </p>
          </div>
          <div className="card p-5">
            <p className="label">What LUMEN will not do</p>
            <p className="mt-2 text-sm text-ink-500">
              No buy, sell or hold calls. No price targets. LUMEN explains what
              changed and how much it matters; the decision stays yours.
            </p>
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="border-t border-ink-100 bg-ink-900 text-white">
        <div className="mx-auto max-w-6xl px-6 py-16 text-center">
          <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">
            Try it on a live scenario
          </h2>
          <p className="mx-auto mt-3 max-w-xl text-ink-300">
            Six replayable market scenarios, including one where everything
            falls and LUMEN correctly tells you nothing needs your attention.
          </p>
          <div className="mt-8 flex flex-wrap justify-center gap-3">
            <button
              type="button"
              onClick={tryLumen}
              disabled={starting}
              className="btn bg-white px-6 py-3 text-base text-ink-900 hover:bg-ink-100"
            >
              {starting ? "Starting..." : "Try LUMEN"}
            </button>
            <Link
              href="/login"
              className="btn border border-ink-700 px-6 py-3 text-base text-white hover:bg-ink-800"
            >
              Sign in with Google
            </Link>
          </div>
        </div>
      </section>

      <footer className="mx-auto max-w-6xl px-6 py-8 text-center text-xs text-ink-400">
        LUMEN provides market information and analysis, not investment advice.
        Demo scenarios use synthetic data and are labelled as such.
      </footer>
    </div>
  );
}
