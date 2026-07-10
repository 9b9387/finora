// Mirrors finora/web/schemas.py — dates are YYYY-MM-DD strings.

export interface SymbolSummary {
  symbol: string;
  rows: number;
  first_date: string;
  last_date: string;
  fresh: boolean;
}

export interface StoreOverview {
  total_rows: number;
  symbol_count: number;
  last_completed_session: string;
  store_size_bytes: number;
  symbols: SymbolSummary[];
}

export interface SymbolList {
  symbols: string[];
}

export interface Bar {
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number;
  volume: number | null;
  factor: number;
  dividend: number;
  split_ratio: number;
}

export interface BarsResponse {
  symbol: string;
  count: number;
  bars: Bar[];
}

export interface AdjustmentEvent {
  date: string;
  kind: "split" | "dividend";
  close: number;
  dividend: number | null;
  split_ratio: number | null;
}

export interface EventsResponse {
  symbol: string;
  events: AdjustmentEvent[];
}

export interface QualityThresholds {
  max_missing_run_days: number;
  max_abs_daily_return: number;
  min_price: number;
}

export interface QualityIssue {
  symbol: string;
  kind: string;
  detail: string;
  date: string | null;
}

export interface QualityResponse {
  checked_symbols: number;
  thresholds: QualityThresholds;
  issues: QualityIssue[];
  generated_at: string;
}

export interface SnapshotInfo {
  date: string;
  symbol_count: number;
}

export interface SnapshotList {
  snapshots: SnapshotInfo[];
}

export interface SnapshotDetail {
  date: string;
  symbols: string[];
}

export interface UniverseDiff {
  from_date: string;
  to_date: string;
  added: string[];
  removed: string[];
  unchanged_count: number;
}
