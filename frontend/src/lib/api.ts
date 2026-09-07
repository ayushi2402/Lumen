/**
 * Backend client.
 *
 * One place that knows the API exists. Every call attaches the LUMEN session
 * token, and errors surface as a typed `ApiError` so pages can render a real
 * error state instead of a blank screen.
 */

import type {
  Dashboard,
  EventDetail,
  EventSummary,
  NotificationItem,
  Profile,
  ReplaySession,
  Scenario,
  SearchResult,
  Session,
  StockDetail,
  User,
  Watchlist,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "http://localhost:8000";

const TOKEN_KEY = "lumen.token";
const USER_KEY = "lumen.user";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/* ------------------------------------------------------------------ */
/* Session storage                                                      */
/* ------------------------------------------------------------------ */

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setSession(session: Session): void {
  try {
    window.localStorage.setItem(TOKEN_KEY, session.access_token);
    window.localStorage.setItem(USER_KEY, JSON.stringify(session.user));
  } catch {
    /* private browsing: the session simply will not persist */
  }
}

export function getStoredUser(): User | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(USER_KEY);
    return raw ? (JSON.parse(raw) as User) : null;
  } catch {
    return null;
  }
}

export function clearSession(): void {
  try {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(USER_KEY);
  } catch {
    /* nothing to clear */
  }
}

/* ------------------------------------------------------------------ */
/* Core request                                                         */
/* ------------------------------------------------------------------ */

type Query = Record<string, string | number | boolean | undefined | null>;

function buildUrl(path: string, query?: Query): string {
  const url = new URL(`${API_BASE}${path}`);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null) {
        url.searchParams.set(key, String(value));
      }
    }
  }
  return url.toString();
}

async function request<T>(
  path: string,
  options: RequestInit & { query?: Query } = {},
): Promise<T> {
  const { query, ...init } = options;
  const token = getToken();

  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(init.headers as Record<string, string> | undefined),
  };
  if (init.body) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;

  let response: Response;
  try {
    response = await fetch(buildUrl(path, query), { ...init, headers, cache: "no-store" });
  } catch {
    // Network-level failure: the backend is asleep, unreachable or CORS-blocked.
    throw new ApiError(
      "Could not reach the LUMEN backend. It may be starting up - free hosting sleeps when idle, so the first request can take up to a minute.",
      0,
    );
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const payload = text ? safeJson(text) : null;

  if (!response.ok) {
    const detail =
      (payload && typeof payload === "object" && "detail" in payload
        ? String((payload as { detail: unknown }).detail)
        : null) || `Request failed (${response.status}).`;
    throw new ApiError(detail, response.status);
  }

  return payload as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

/* ------------------------------------------------------------------ */
/* Endpoints                                                            */
/* ------------------------------------------------------------------ */

/** Replay parameters, threaded through every data call so demo stays demo. */
export interface ModeParams {
  mode?: "live" | "replay";
  scenario?: string;
  step?: number;
}

export const api = {
  health: () => request<Record<string, unknown>>("/health"),

  /* --- auth --- */
  guestLogin: () => request<Session>("/api/v1/auth/guest", { method: "POST" }),
  googleLogin: (idToken: string) =>
    request<Session>("/api/v1/auth/google", {
      method: "POST",
      body: JSON.stringify({ id_token: idToken }),
    }),
  me: () => request<User>("/api/v1/auth/me"),
  getProfile: () => request<Profile>("/api/v1/auth/profile"),
  updateProfile: (body: Partial<Profile>) =>
    request<Profile>("/api/v1/auth/profile", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  starterSuggestions: () =>
    request<{ suggestions: Array<{ symbol: string; name: string; sector: string }> }>(
      "/api/v1/auth/starter-suggestions",
    ),
  completeSetup: (symbols: string[], watchlistName?: string) =>
    request<{ watchlist_id: number; symbols: string[]; baseline_established: boolean }>(
      "/api/v1/auth/setup",
      {
        method: "POST",
        body: JSON.stringify({ symbols, watchlist_name: watchlistName ?? null }),
      },
    ),

  /* --- dashboard --- */
  dashboard: (params: ModeParams = {}) =>
    request<Dashboard>("/api/v1/dashboard", { query: { ...params } }),
  markDigestReviewed: () =>
    request<{ baseline: Record<string, string> }>("/api/v1/dashboard/digest/reviewed", {
      method: "POST",
    }),
  setBaselineHere: () =>
    request<{ baseline: Record<string, string> }>("/api/v1/dashboard/digest/baseline", {
      method: "POST",
    }),

  /* --- watchlists --- */
  watchlists: () => request<Watchlist[]>("/api/v1/watchlists"),
  watchlist: (id: number, params: ModeParams = {}) =>
    request<Watchlist>(`/api/v1/watchlists/${id}`, { query: { ...params } }),
  createWatchlist: (name: string) =>
    request<Watchlist>("/api/v1/watchlists", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  renameWatchlist: (id: number, name: string) =>
    request<Watchlist>(`/api/v1/watchlists/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),
  deleteWatchlist: (id: number) =>
    request<void>(`/api/v1/watchlists/${id}`, { method: "DELETE" }),
  addToWatchlist: (id: number, symbol: string) =>
    request<Watchlist>(`/api/v1/watchlists/${id}/items`, {
      method: "POST",
      body: JSON.stringify({ symbol }),
    }),
  removeFromWatchlist: (id: number, symbol: string) =>
    request<Watchlist>(`/api/v1/watchlists/${id}/items/${symbol}`, { method: "DELETE" }),
  updateWatchlistItem: (
    id: number,
    symbol: string,
    body: { priority?: number; starred?: boolean; notes?: string; position?: number },
  ) =>
    request<Watchlist>(`/api/v1/watchlists/${id}/items/${symbol}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  /* --- stocks --- */
  search: (q: string, params: ModeParams = {}) =>
    request<SearchResult[]>("/api/v1/stocks/search", { query: { q, ...params } }),
  stock: (symbol: string, params: ModeParams = {}) =>
    request<StockDetail>(`/api/v1/stocks/${symbol}`, { query: { ...params } }),

  /* --- events --- */
  events: (params: ModeParams & { include_dismissed?: boolean } = {}) =>
    request<EventSummary[]>("/api/v1/events", { query: { ...params } }),
  eventHistory: (params: ModeParams & { day?: string } = {}) =>
    request<EventSummary[]>("/api/v1/events/history", { query: { ...params } }),
  event: (id: number, explainWithLlm = true) =>
    request<EventDetail>(`/api/v1/events/${id}`, {
      query: { explain_with_llm: explainWithLlm },
    }),
  openWhy: (id: number) =>
    request<{ recorded: boolean }>(`/api/v1/events/${id}/why`, { method: "POST" }),
  openBreakdown: (id: number) =>
    request<{ recorded: boolean }>(`/api/v1/events/${id}/breakdown`, { method: "POST" }),
  reviewEvent: (id: number) =>
    request<{ reviewed: boolean }>(`/api/v1/events/${id}/review`, { method: "POST" }),
  dismissEvent: (id: number) =>
    request<{ dismissed: boolean }>(`/api/v1/events/${id}/dismiss`, { method: "POST" }),
  moreLikeThis: (eventId: number) =>
    request<{ recorded: boolean }>("/api/v1/events/feedback", {
      method: "POST",
      body: JSON.stringify({ event_id: eventId, feedback: "more_like_this" }),
    }),

  /* --- behaviour --- */
  recordInteraction: (kind: string, symbol?: string, eventId?: number) =>
    request<{ recorded: boolean }>("/api/v1/behavior/interactions", {
      method: "POST",
      body: JSON.stringify({ kind, symbol: symbol ?? null, event_id: eventId ?? null }),
    }),
  mute: (symbol: string, hours = 24) =>
    request<{ symbol: string; muted_until: string }>("/api/v1/behavior/mute", {
      method: "POST",
      body: JSON.stringify({ symbol, hours }),
    }),
  unmute: (symbol: string) =>
    request<{ symbol: string; muted: boolean }>(`/api/v1/behavior/mute/${symbol}`, {
      method: "DELETE",
    }),
  mutes: () => request<{ muted: string[] }>("/api/v1/behavior/mutes"),
  relevance: () =>
    request<{
      relevance: Record<string, number>;
      max_score_adjustment: number;
      note: string;
    }>("/api/v1/behavior/relevance"),

  /* --- replay --- */
  scenarios: () => request<Scenario[]>("/api/v1/replay/scenarios"),
  startReplay: (scenarioKey: string, speed = 1) =>
    request<ReplaySession>("/api/v1/replay/sessions", {
      method: "POST",
      body: JSON.stringify({ scenario_key: scenarioKey, speed }),
    }),
  stepReplay: (id: number, steps = 1) =>
    request<ReplaySession>(`/api/v1/replay/sessions/${id}/step`, {
      method: "POST",
      body: JSON.stringify({ steps }),
    }),
  setReplaySpeed: (id: number, speed: number) =>
    request<ReplaySession>(`/api/v1/replay/sessions/${id}/speed`, {
      method: "POST",
      query: { speed },
    }),
  resetReplay: (id: number) =>
    request<ReplaySession>(`/api/v1/replay/sessions/${id}/reset`, { method: "POST" }),
  replayTimeline: (id: number) =>
    request<{
      label: string;
      scenario: { key: string; name: string; description: string; teaching_point: string };
      virtual_now: string;
      step_index: number;
      total_steps: number;
      detections: Array<{
        at: string;
        symbol: string;
        headline: string;
        severity: string;
        score: number;
        classification: string;
        reason: string;
      }>;
      detected_count: number;
      note: string;
    }>(`/api/v1/replay/sessions/${id}/timeline`),

  /* --- notifications --- */
  notifications: () =>
    request<{
      notifications: NotificationItem[];
      unread_count: number;
      min_score_for_notification: number;
      note: string;
    }>("/api/v1/notifications"),
  markNotificationRead: (id: number) =>
    request<NotificationItem>(`/api/v1/notifications/${id}/read`, { method: "POST" }),
  markNotificationDelivered: (id: number) =>
    request<NotificationItem>(`/api/v1/notifications/${id}/delivered`, { method: "POST" }),
  markAllNotificationsRead: () =>
    request<{ marked_read: number }>("/api/v1/notifications/read-all", { method: "POST" }),
};

/** Ensure a session exists, starting a guest one if needed. */
export async function ensureSession(): Promise<User | null> {
  if (getToken()) {
    try {
      return await api.me();
    } catch {
      clearSession();
    }
  }
  const session = await api.guestLogin();
  setSession(session);
  return session.user;
}
