"use client";

/**
 * Profile: the optional lightweight investor profile, notification permission,
 * and muted stocks. Deliberately short - not a questionnaire.
 */

import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { ErrorState, LoadingCard, SectionHeading } from "@/components/ui";
import { api, getStoredUser } from "@/lib/api";
import { permission, requestPermission } from "@/lib/notifications";
import type { Profile, User } from "@/lib/types";

const RISK_OPTIONS = ["low", "moderate", "high"];
const STYLE_OPTIONS = ["long_term", "swing", "active"];
const SECTORS = [
  "IT",
  "Banking",
  "Energy",
  "Automobile",
  "Metals",
  "Pharma",
  "FMCG",
  "Infrastructure",
  "Telecom",
];

export default function ProfilePage() {
  return (
    <AppShell>
      <ProfileView />
    </AppShell>
  );
}

function ProfileView() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [muted, setMuted] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notifState, setNotifState] = useState<string>("unsupported");

  const load = useCallback(async () => {
    try {
      const [loadedProfile, loadedMutes] = await Promise.all([
        api.getProfile(),
        api.mutes(),
      ]);
      setProfile(loadedProfile);
      setMuted(loadedMutes.muted);
      setUser(getStoredUser());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load your profile.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Run as an async task so no state update happens during the effect's
    // synchronous phase.
    void (async () => {
      await load();
      setNotifState(permission());
    })();
  }, [load]);

  const save = async () => {
    if (!profile) return;
    setSaving(true);
    setSaved(false);
    try {
      const updated = await api.updateProfile({
        risk_appetite: profile.risk_appetite,
        investment_style: profile.investment_style,
        sectors: profile.sectors,
      });
      setProfile(updated);
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save your profile.");
    } finally {
      setSaving(false);
    }
  };

  const toggleSector = (sector: string) => {
    if (!profile) return;
    const next = profile.sectors.includes(sector)
      ? profile.sectors.filter((value) => value !== sector)
      : [...profile.sectors, sector];
    setProfile({ ...profile, sectors: next });
  };

  if (loading) return <LoadingCard lines={6} />;
  if (error && !profile) return <ErrorState message={error} onRetry={() => void load()} />;
  if (!profile) return null;

  return (
    <div className="max-w-2xl space-y-6">
      <SectionHeading
        title="Profile"
        subtitle="Optional. LUMEN works fully without any of this."
      />

      {user ? (
        <div className="card p-5">
          <p className="label">Account</p>
          <p className="mt-1 font-medium">
            {user.display_name || user.email || "Guest session"}
          </p>
          {user.is_guest ? (
            <p className="mt-1 text-xs text-ink-400">
              You are using a guest demo session. Sign in with Google to keep
              your watchlists across devices.
            </p>
          ) : null}
        </div>
      ) : null}

      <div className="card space-y-5 p-5">
        <div>
          <p className="label mb-2">Risk appetite</p>
          <div className="flex flex-wrap gap-2">
            {RISK_OPTIONS.map((option) => (
              <button
                key={option}
                type="button"
                onClick={() =>
                  setProfile({
                    ...profile,
                    risk_appetite: profile.risk_appetite === option ? null : option,
                  })
                }
                className={`rounded-lg border px-3 py-1.5 text-sm capitalize ${
                  profile.risk_appetite === option
                    ? "border-ink-900 bg-ink-900 text-white"
                    : "border-ink-200 bg-white text-ink-500 hover:bg-ink-50"
                }`}
              >
                {option}
              </button>
            ))}
          </div>
        </div>

        <div>
          <p className="label mb-2">Investment style</p>
          <div className="flex flex-wrap gap-2">
            {STYLE_OPTIONS.map((option) => (
              <button
                key={option}
                type="button"
                onClick={() =>
                  setProfile({
                    ...profile,
                    investment_style:
                      profile.investment_style === option ? null : option,
                  })
                }
                className={`rounded-lg border px-3 py-1.5 text-sm ${
                  profile.investment_style === option
                    ? "border-ink-900 bg-ink-900 text-white"
                    : "border-ink-200 bg-white text-ink-500 hover:bg-ink-50"
                }`}
              >
                {option.replace("_", " ")}
              </button>
            ))}
          </div>
        </div>

        <div>
          <p className="label mb-2">Sectors you follow</p>
          <div className="flex flex-wrap gap-2">
            {SECTORS.map((sector) => (
              <button
                key={sector}
                type="button"
                onClick={() => toggleSector(sector)}
                className={`rounded-lg border px-3 py-1.5 text-sm ${
                  profile.sectors.includes(sector)
                    ? "border-ink-900 bg-ink-900 text-white"
                    : "border-ink-200 bg-white text-ink-500 hover:bg-ink-50"
                }`}
              >
                {sector}
              </button>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-3 border-t border-ink-100 pt-4">
          <button
            type="button"
            onClick={() => void save()}
            disabled={saving}
            className="btn-primary !py-2 text-sm"
          >
            {saving ? "Saving..." : "Save"}
          </button>
          {saved ? <span className="text-sm text-up">Saved.</span> : null}
        </div>
      </div>

      {/* Notifications */}
      <div className="card p-5">
        <p className="label">Notifications</p>
        <p className="mt-1 text-sm text-ink-500">
          LUMEN only notifies for High Attention and Critical events. Everything
          else stays in the feed.
        </p>
        <div className="mt-3 flex items-center gap-3">
          <span className="rounded-md bg-ink-100 px-2 py-1 text-xs text-ink-500">
            {notifState === "granted"
              ? "Enabled"
              : notifState === "denied"
                ? "Blocked by browser"
                : notifState === "unsupported"
                  ? "Not supported in this browser"
                  : "Not enabled"}
          </span>
          {notifState === "default" ? (
            <button
              type="button"
              onClick={async () => setNotifState(await requestPermission())}
              className="btn-secondary !py-1.5 text-sm"
            >
              Enable browser notifications
            </button>
          ) : null}
        </div>
        {notifState === "denied" ? (
          <p className="mt-2 text-xs text-ink-400">
            Alerts still appear in the in-app inbox.
          </p>
        ) : null}
      </div>

      {/* Muted */}
      <div className="card p-5">
        <p className="label">Muted stocks</p>
        {muted.length === 0 ? (
          <p className="mt-1 text-sm text-ink-500">Nothing is muted.</p>
        ) : (
          <ul className="mt-3 space-y-2">
            {muted.map((symbol) => (
              <li key={symbol} className="flex items-center justify-between gap-3">
                <span className="text-sm font-medium">{symbol}</span>
                <button
                  type="button"
                  onClick={async () => {
                    await api.unmute(symbol).catch(() => undefined);
                    await load();
                  }}
                  className="btn-ghost !px-2 !py-1 text-xs"
                >
                  Unmute
                </button>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-3 text-xs text-ink-400">
          Mutes are temporary by design and expire on their own.
        </p>
      </div>

      {error ? (
        <p className="rounded-lg border border-down/20 bg-down-soft px-4 py-2 text-sm text-down">
          {error}
        </p>
      ) : null}
    </div>
  );
}
