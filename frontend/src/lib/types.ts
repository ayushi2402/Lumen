/**
 * Types mirroring the backend's `/api/v1` schemas.
 *
 * Kept deliberately close to `app/schemas/api.py`. The frontend never
 * recomputes significance, ranking or freshness - it renders what the backend
 * decided, so these are read models rather than a second domain model.
 */

export type Severity = "Noise" | "Worth Watching" | "High Attention" | "Critical";
export type Direction = "positive" | "negative" | "neutral" | "mixed";
export type Confidence = "high" | "medium" | "low";
export type FreshnessState = "live" | "delayed" | "stale";
export type Classification = "stock_specific" | "sector_wide" | "market_wide";

export interface Freshness {
  state: FreshnessState;
  as_of: string;
  age_seconds: number;
  source: string;
  market_status: string;
  label: string;
}

export interface DataMode {
  mode: "live" | "replay";
  is_replay: boolean;
  degraded: boolean;
  label: string;
  reason?: string | null;
  provider?: string;
  chain?: string[];
  attempts?: string[];
  scenario?: string | null;
  step?: number | null;
  total_steps?: number | null;
  virtual_time?: string | null;
}

export interface Price {
  price: number;
  previous_close: number;
  change: number;
  change_percent: number;
  volume?: number | null;
}

export interface Signal {
  type: string;
  family: string;
  availability: "available" | "unavailable" | "insufficient_history";
  value: number | null;
  strength: number | null;
  direction: Direction;
  evidence: string;
  confidence: number;
}

export interface ScoreBreakdown {
  summary: string;
  objective_score: number;
  personalization_adjustment: number;
  final_score: number;
  severity: Severity;
  breakdown: Record<string, number>;
  confidence: Confidence;
  confidence_report: {
    level: Confidence;
    independent_signals: number;
    families_available: number;
    families_considered: number;
    coverage: number;
    contradictions: number;
    reasons: string[];
  };
  meaningful: boolean;
  signals: Signal[];
}

export interface Explanation {
  text: string;
  deterministic_text: string;
  source: "deterministic" | "llm";
  model?: string | null;
  fallback_reason?: string | null;
}

export interface EventSummary {
  id: number;
  event_key: string;
  symbol: string;
  name: string;
  headline: string;
  severity: Severity;
  direction: Direction;
  score: number;
  peak_score: number;
  confidence: Confidence;
  status: string;
  classification: Classification;
  is_market_wide: boolean;
  primary_reason: string;
  started_at: string;
  last_updated_at: string;
  session_date: string;
  observation_count: number;
  read: boolean;
  reviewed: boolean;
  dismissed: boolean;
}

export interface TimelinePoint {
  observed_at: string;
  score: number;
  severity: Severity;
  direction: Direction;
  price?: number | null;
}

export interface EventDetail extends EventSummary {
  explanation: Explanation;
  evidence: string[];
  signals: Signal[];
  score_detail: ScoreBreakdown;
  timeline: TimelinePoint[];
  market_context_note?: string | null;
  price?: Price | null;
}

export interface AttentionCard {
  rank: number;
  symbol: string;
  name: string;
  price: Price;
  score: number;
  severity: Severity;
  direction: Direction;
  confidence: Confidence;
  primary_reason: string;
  why_it_matters: string;
  event_id: number | null;
  classification: Classification;
}

export interface MarketContext {
  benchmark_return: number | null;
  advancing: number;
  declining: number;
  unchanged: number;
  total: number;
  breadth_ratio: number;
  median_return: number;
  dominant_direction: Direction;
  is_market_wide_move: boolean;
  label: string;
}

export interface DaySummary {
  session_date: string;
  headline: string;
  top_symbols: string[];
  event_count: number;
  events: EventSummary[];
}

export interface SinceAway {
  baseline_at: string;
  baseline_source: string;
  baseline_label: string;
  summary: string;
  sessions_missed: number;
  is_multi_session: boolean;
  top_events: EventSummary[];
  remainder_count: number;
  days: DaySummary[];
}

export interface Dashboard {
  generated_at: string;
  data_mode: DataMode;
  freshness: Freshness | null;
  attention: AttentionCard[];
  attention_headline: string;
  since_you_were_away: SinceAway;
  event_feed: EventSummary[];
  market: MarketContext;
  notifications: {
    unread_count: number;
    pending_delivery: Array<{
      id: number;
      title: string;
      body: string;
      severity: Severity;
      event_id: number | null;
    }>;
  };
  watchlist_count: number;
  tracked_symbols: number;
  muted_symbols: string[];
  warnings: string[];
}

export interface WatchlistItem {
  symbol: string;
  name: string;
  sector?: string | null;
  position: number;
  priority: number;
  starred: boolean;
  notes?: string | null;
  price?: Price | null;
  score?: number | null;
  severity?: Severity | null;
  latest_reason?: string | null;
  event_id?: number | null;
  sparkline: number[];
  muted: boolean;
}

export interface Watchlist {
  id: number;
  name: string;
  is_default: boolean;
  position: number;
  item_count: number;
  items: WatchlistItem[];
}

export interface SearchResult {
  symbol: string;
  name: string;
  sector?: string | null;
  price: number | null;
  change_percent: number | null;
  is_supported: boolean;
}

export interface StockDetail {
  instrument: {
    symbol: string;
    name: string;
    sector?: string | null;
    exchange: string;
    is_supported: boolean;
  };
  price: Price;
  freshness: Freshness;
  data_mode: DataMode;
  score: number | null;
  severity: Severity | null;
  direction: Direction | null;
  confidence: Confidence | null;
  current_event: EventSummary | null;
  event_timeline: EventSummary[];
  chart: Array<{
    timestamp: string;
    price: number | null;
    score: number;
    severity: Severity;
  }>;
  benchmark_return: number | null;
  sector_return: number | null;
  relative_to_benchmark: number | null;
  relative_to_sector: number | null;
  classification: Classification | null;
  muted: boolean;
  in_watchlists: string[];
}

export interface User {
  id: number;
  email?: string | null;
  display_name?: string | null;
  picture_url?: string | null;
  is_guest: boolean;
  onboarded: boolean;
  has_watchlist: boolean;
}

export interface Session {
  access_token: string;
  token_type: string;
  expires_at: string;
  user: User;
}

export interface Profile {
  risk_appetite?: string | null;
  investment_style?: string | null;
  sectors: string[];
  onboarded: boolean;
}

export interface Scenario {
  key: string;
  name: string;
  description: string;
  teaching_point: string;
  focus_symbols: string[];
  total_steps: number;
  step_minutes: number;
}

export interface ReplaySession {
  id: number;
  scenario_key: string;
  scenario_name: string;
  speed: number;
  status: string;
  step_index: number;
  total_steps: number;
  virtual_now: string;
  label: string;
  detected: EventSummary[];
  market: MarketContext | null;
}

export interface NotificationItem {
  id: number;
  event_id: number | null;
  title: string;
  body: string;
  severity: Severity;
  created_at: string;
  read: boolean;
  delivered: boolean;
}
