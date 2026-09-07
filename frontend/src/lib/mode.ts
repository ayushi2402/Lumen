/**
 * The active data mode, shared across pages.
 *
 * Without this, starting a demo and then opening a stock would silently drop
 * back to live data — the judge would click into RELIANCE mid-scenario and see
 * an empty page. The demo selection is therefore remembered and every data
 * call carries it, so replay stays replay until it is explicitly exited.
 *
 * Stored per browser only. It is a view preference, not user data.
 */

import type { ModeParams } from "./api";

const KEY = "lumen.mode";

export interface DemoMode {
  scenario: string;
  step: number;
}

export function setDemoMode(scenario: string, step: number): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify({ scenario, step }));
  } catch {
    /* private browsing: the demo still works, it just will not persist */
  }
}

export function clearDemoMode(): void {
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    /* nothing to clear */
  }
}

export function getDemoMode(): DemoMode | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<DemoMode>;
    if (!parsed.scenario) return null;
    return { scenario: parsed.scenario, step: Number(parsed.step) || 0 };
  } catch {
    return null;
  }
}

/** Query parameters for any data call: replay when a demo is active, else live. */
export function currentMode(): ModeParams {
  const demo = getDemoMode();
  if (!demo) return { mode: "live" };
  return { mode: "replay", scenario: demo.scenario, step: demo.step };
}

export function isDemoActive(): boolean {
  return getDemoMode() !== null;
}
