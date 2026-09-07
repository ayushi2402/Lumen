"use client";

/**
 * Entry point.
 *
 * Google sign-in is offered only when a client id is configured; the demo path
 * is always available. OAuth misconfiguration must never block a judge from
 * seeing the product.
 */

import Link from "next/link";
import Script from "next/script";
import { useRouter } from "next/navigation";
import { useCallback, useRef, useState } from "react";

import { api, setSession } from "@/lib/api";

const GOOGLE_CLIENT_ID = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID || "";

interface GoogleCredentialResponse {
  credential?: string;
}

/** The slice of Google Identity Services this page uses. */
interface GoogleAccounts {
  accounts?: {
    id?: {
      initialize: (config: {
        client_id: string;
        callback: (response: GoogleCredentialResponse) => void;
      }) => void;
      renderButton: (
        parent: HTMLElement,
        options: { theme: string; size: string; width: number },
      ) => void;
    };
  };
}

export default function LoginPage() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const buttonRef = useRef<HTMLDivElement | null>(null);

  const handleCredential = useCallback(
    async (response: GoogleCredentialResponse) => {
      if (!response.credential) {
        setError("Google did not return a credential.");
        return;
      }
      setBusy(true);
      setError(null);
      try {
        const session = await api.googleLogin(response.credential);
        setSession(session);
        router.push(session.user.has_watchlist ? "/dashboard" : "/profile");
      } catch (err) {
        setError(err instanceof Error ? err.message : "Google sign-in failed.");
        setBusy(false);
      }
    },
    [router],
  );

  /**
   * Render Google's button. Invoked from the script's own load handler rather
   * than an effect: initialising an external SDK belongs with the event that
   * makes it available, and it keeps state updates out of an effect body.
   */
  const initGoogle = useCallback(() => {
    if (!GOOGLE_CLIENT_ID || !buttonRef.current) return;
    const google = (window as unknown as { google?: GoogleAccounts }).google;
    if (!google?.accounts?.id) {
      setError("Google sign-in did not load. You can still use the demo below.");
      return;
    }

    try {
      google.accounts.id.initialize({
        client_id: GOOGLE_CLIENT_ID,
        callback: handleCredential,
      });
      google.accounts.id.renderButton(buttonRef.current, {
        theme: "outline",
        size: "large",
        width: 320,
      });
    } catch {
      setError("Could not initialise Google sign-in. The demo below still works.");
    }
  }, [handleCredential]);

  const startDemo = async () => {
    setBusy(true);
    setError(null);
    try {
      const session = await api.guestLogin();
      setSession(session);
      router.push("/demo");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not start a demo session.",
      );
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen flex-col bg-white">
      {GOOGLE_CLIENT_ID ? (
        <Script
          src="https://accounts.google.com/gsi/client"
          onLoad={initGoogle}
          onError={() =>
            setError("Google sign-in could not be loaded. Use the demo below.")
          }
          strategy="afterInteractive"
        />
      ) : null}

      <header className="border-b border-ink-100">
        <div className="mx-auto max-w-6xl px-6 py-4">
          <Link href="/" className="text-lg font-semibold tracking-tight">
            LUMEN
          </Link>
        </div>
      </header>

      <main className="flex flex-1 items-center justify-center px-6 py-16">
        <div className="w-full max-w-sm">
          <h1 className="text-2xl font-semibold tracking-tight">Get started</h1>
          <p className="mt-2 text-sm text-ink-500">
            See what changed in the stocks you care about.
          </p>

          <div className="mt-8 space-y-3">
            {GOOGLE_CLIENT_ID ? (
              <div ref={buttonRef} className="flex justify-center" />
            ) : (
              <div className="rounded-lg border border-dashed border-ink-200 px-4 py-3 text-center text-xs text-ink-400">
                Google sign-in is not configured on this deployment. The demo
                below works without it.
              </div>
            )}

            <div className="flex items-center gap-3 py-1">
              <span className="h-px flex-1 bg-ink-100" />
              <span className="text-xs text-ink-400">or</span>
              <span className="h-px flex-1 bg-ink-100" />
            </div>

            <button
              type="button"
              onClick={startDemo}
              disabled={busy}
              className="btn-primary w-full py-3"
            >
              {busy ? "Starting..." : "Try LUMEN Demo"}
            </button>
            <p className="text-center text-xs text-ink-400">
              No account needed. Comes with a curated watchlist and replayable
              market scenarios.
            </p>
          </div>

          {error ? (
            <p className="mt-6 rounded-lg border border-down/20 bg-down-soft px-4 py-3 text-sm text-down">
              {error}
            </p>
          ) : null}
        </div>
      </main>
    </div>
  );
}
