"use client";

/**
 * Application chrome: navigation, search, notification bell.
 *
 * Also owns session bootstrap. Any authenticated page renders inside this, and
 * a visitor without a session is given a guest one automatically - which is
 * what makes "Try LUMEN" work with no configuration at all.
 */

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { api, clearSession, ensureSession, getStoredUser } from "@/lib/api";
import { clearDemoMode, currentMode, isDemoActive } from "@/lib/mode";
import { deliverPending, permission, requestPermission } from "@/lib/notifications";
import type { NotificationItem, SearchResult, User } from "@/lib/types";

import { Spinner } from "./ui";

const NAV = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/watchlists", label: "Watchlists" },
  { href: "/history", label: "History" },
  { href: "/demo", label: "Demo" },
  { href: "/profile", label: "Profile" },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [booting, setBooting] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Read after mount, never during render: localStorage is unavailable on the
  // server and reading it inline would cause a hydration mismatch.
  const [demoActive, setDemoActive] = useState(false);

  useEffect(() => {
    void (async () => {
      setDemoActive(isDemoActive());
    })();
  }, [pathname]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const existing = getStoredUser();
        if (existing) setUser(existing);
        const resolved = await ensureSession();
        if (!cancelled) setUser(resolved);
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error
              ? err.message
              : "Could not start a LUMEN session.",
          );
        }
      } finally {
        if (!cancelled) setBooting(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (booting) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner label="Starting LUMEN..." />
      </div>
    );
  }

  if (error) {
    return (
      <div className="mx-auto flex min-h-screen max-w-lg flex-col items-center justify-center gap-4 px-6 text-center">
        <h1 className="text-lg font-semibold">LUMEN cannot reach its backend</h1>
        <p className="text-sm text-ink-500">{error}</p>
        <button type="button" onClick={() => window.location.reload()} className="btn-primary">
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-30 border-b border-ink-200 bg-white/90 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center gap-4 px-4 py-3 sm:px-6">
          <Link href="/dashboard" className="shrink-0 text-lg font-semibold tracking-tight">
            LUMEN
          </Link>

          <nav className="hidden items-center gap-1 md:flex">
            {NAV.map((item) => {
              const active = pathname?.startsWith(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`rounded-lg px-3 py-1.5 text-sm transition-colors ${
                    active
                      ? "bg-ink-100 font-medium text-ink-900"
                      : "text-ink-500 hover:bg-ink-50 hover:text-ink-900"
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>

          {/* min-w-0 lets this cluster shrink instead of forcing the header
              wider than the viewport on narrow screens. */}
          <div className="flex min-w-0 flex-1 items-center justify-end gap-2">
            <SearchBar />
            <NotificationBell />
            <UserMenu user={user} />
          </div>
        </div>

        {/* Mobile navigation */}
        <nav className="flex gap-1 overflow-x-auto border-t border-ink-100 px-4 py-2 md:hidden">
          {NAV.map((item) => {
            const active = pathname?.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`whitespace-nowrap rounded-lg px-3 py-1.5 text-sm ${
                  active ? "bg-ink-100 font-medium" : "text-ink-500"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
      </header>

      {/* Demo data must never be mistaken for live data, on any page. */}
      {demoActive ? (
        <div className="border-b border-warn/30 bg-warn-soft">
          <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2 sm:px-6">
            <span className="rounded bg-white/70 px-2 py-0.5 text-[11px] font-bold uppercase tracking-wide text-warn">
              Demo / Replay
            </span>
            <span className="text-xs text-warn">
              Showing a replayed scenario on a virtual clock, not live market data.
            </span>
            <button
              type="button"
              onClick={() => {
                clearDemoMode();
                setDemoActive(false);
                router.refresh();
              }}
              className="ml-auto text-xs font-medium text-warn underline underline-offset-2"
            >
              Exit demo
            </button>
          </div>
        </div>
      ) : null}

      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8">{children}</main>

      <footer className="border-t border-ink-200 px-4 py-6 text-center text-xs text-ink-400">
        LUMEN provides market information and analysis, not investment advice.
      </footer>
    </div>
  );
}

/* ------------------------------------------------------------------ */

function SearchBar() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [open, setOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Debounced search. Every state update happens inside the timer callback,
  // never synchronously in the effect body, so typing cannot cascade renders.
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    const term = query.trim();

    timer.current = setTimeout(async () => {
      if (term.length < 2) {
        setResults([]);
        setOpen(false);
        return;
      }
      try {
        setResults(await api.search(term, currentMode()));
        setOpen(true);
      } catch {
        setResults([]);
      }
    }, 250);

    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [query]);

  const go = (symbol: string) => {
    setOpen(false);
    setQuery("");
    router.push(`/stocks/${symbol}`);
  };

  // Hidden below lg: at tablet width the logo, nav, search and user menu
  // together exceed the viewport. Search stays reachable from watchlist pages.
  return (
    <div className="relative hidden w-52 lg:block xl:w-72">
      <input
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        onFocus={() => results.length && setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        placeholder="Search stocks..."
        aria-label="Search stocks"
        className="w-full rounded-lg border border-ink-200 bg-ink-50 px-3 py-1.5 text-sm
                   outline-none placeholder:text-ink-400 focus:border-ink-300 focus:bg-white"
      />
      {open && results.length > 0 ? (
        <ul className="absolute left-0 right-0 top-full z-40 mt-1 max-h-80 overflow-auto rounded-lg border border-ink-200 bg-white py-1 shadow-lift">
          {results.map((result) => (
            <li key={result.symbol}>
              <button
                type="button"
                onMouseDown={() => result.is_supported && go(result.symbol)}
                disabled={!result.is_supported}
                className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left hover:bg-ink-50 disabled:opacity-50"
              >
                <span className="min-w-0">
                  <span className="block text-sm font-medium">{result.symbol}</span>
                  <span className="block truncate text-xs text-ink-500">{result.name}</span>
                </span>
                <span className="numeric shrink-0 text-sm">
                  {result.price !== null ? `₹${result.price.toFixed(2)}` : "--"}
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function NotificationBell() {
  const router = useRouter();
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await api.notifications();
      setItems(data.notifications);
      setUnread(data.unread_count);
      if (permission() === "granted") {
        void deliverPending(
          data.notifications.filter((n) => !n.delivered && !n.read),
          (path) => router.push(path),
        );
      }
    } catch {
      /* the bell is non-critical */
    }
  }, [router]);

  useEffect(() => {
    // Kicked off in a microtask so the effect body itself performs no state
    // updates; the poll then keeps the badge current.
    const timer = setTimeout(() => void load(), 0);
    const interval = setInterval(load, 60_000);
    return () => {
      clearTimeout(timer);
      clearInterval(interval);
    };
  }, [load]);

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="relative rounded-lg px-2.5 py-1.5 text-sm text-ink-500 hover:bg-ink-50"
        aria-label="Notifications"
      >
        Alerts
        {unread > 0 ? (
          <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-down px-1 text-[10px] font-semibold text-white">
            {unread}
          </span>
        ) : null}
      </button>

      {open ? (
        <div className="absolute right-0 top-full z-40 mt-1 w-80 rounded-lg border border-ink-200 bg-white shadow-lift">
          <div className="flex items-center justify-between border-b border-ink-100 px-3 py-2">
            <span className="text-sm font-medium">Alerts</span>
            <div className="flex gap-2">
              {permission() === "default" ? (
                <button
                  type="button"
                  onClick={async () => {
                    await requestPermission();
                    void load();
                  }}
                  className="text-xs text-accent hover:underline"
                >
                  Enable
                </button>
              ) : null}
              <button
                type="button"
                onClick={async () => {
                  await api.markAllNotificationsRead().catch(() => undefined);
                  void load();
                }}
                className="text-xs text-ink-400 hover:underline"
              >
                Mark all read
              </button>
            </div>
          </div>

          <div className="max-h-80 overflow-auto">
            {items.length === 0 ? (
              <p className="px-3 py-6 text-center text-xs text-ink-400">
                No alerts yet. LUMEN only notifies for High Attention and Critical
                events.
              </p>
            ) : (
              items.slice(0, 15).map((item) => (
                <div
                  key={item.id}
                  className={`border-b border-ink-100 px-3 py-2 last:border-0 ${
                    item.read ? "opacity-60" : ""
                  }`}
                >
                  <p className="text-sm font-medium">{item.title}</p>
                  <p className="mt-0.5 line-clamp-2 text-xs text-ink-500">{item.body}</p>
                </div>
              ))
            )}
          </div>

          {permission() === "denied" ? (
            <p className="border-t border-ink-100 px-3 py-2 text-[11px] text-ink-400">
              Browser notifications are blocked. Alerts still appear here.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function UserMenu({ user }: { user: User | null }) {
  const router = useRouter();
  if (!user) return null;

  return (
    <div className="flex items-center gap-2">
      {user.is_guest ? (
        <span className="hidden rounded-md bg-warn-soft px-2 py-1 text-xs font-medium text-warn sm:inline">
          Guest
        </span>
      ) : null}
      <button
        type="button"
        onClick={() => {
          clearSession();
          clearDemoMode();
          router.push("/");
        }}
        className="rounded-lg px-2.5 py-1.5 text-sm text-ink-500 hover:bg-ink-50"
      >
        {user.is_guest ? "Exit" : "Sign out"}
      </button>
    </div>
  );
}
