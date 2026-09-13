import { t as tr } from '../i18n/t.js';
import { linguaCorrente, localeDi } from '../i18n/lingua.js';
import { leggiDetail } from './quota';
import { API_BASE, clearSessionAndReload, requestHeaders } from './api';

export interface ProgressDelta { available: boolean; hit_rate_pp: number | null; reason: string }
export interface ProgressPoint {
  run_id: string | null; memo_id: number | null; completed_at: string | null; computed_at: string | null;
  n: number | null; hits: number | null; hit_rate_pct: number | null; avg_edge_pct: number | null;
  ci95: { low_pct: number; high_pct: number } | null;
  quality: string[]; comparison_key: string | null; window_days: number | null; maturation_days: number | null;
  source: string; n_fetch_fail: number | null; n_unmeasurable: number | null; n_directional_candidates: number | null;
  delta?: ProgressDelta;
  operational: { status: string | null; models: string[]; usage: { cost_eur: number | null; partial: boolean;
    duration_s: number | null; api_calls: number | null; status: string; tokens_status?: string } | null };
}
export interface ProgressAgent {
  id: string; label: string; role: string; attribution: 'collective' | 'heuristic' | 'unsupported';
  latest: ProgressPoint | null; series: ProgressPoint[]; delta: ProgressDelta;
  current?: ProgressPoint | null;
}
export interface ProgressRun {
  run_id: string; memo_id: number; started_at: string; completed_at: string; captured_at: string;
  score_error: string | null;
  output_language?: 'it' | 'en' | null;
  review_status?: 'duplicate' | 'unmarked' | 'unavailable'; review_note?: string | null;
  scorecard?: { degraded?: boolean; by_action?: Record<string, ProgressAggregate>; by_confidence?: Record<string, ProgressAggregate>;
    by_confidence_scartate?: { n?: number; etichette?: Record<string, number>; motivo?: string };
    details?: ProgressCall[]; worst_calls?: ProgressCall[]; best_calls?: ProgressCall[] } | null;
  reflection: { text: string | null; status: string; kind: string; implementation_verified: boolean; performance_proven: boolean };
}
export interface ProgressAggregate { n?: number; hits?: number; hit_rate_pct?: number; avg_edge_pct?: number; small_sample?: boolean }
export interface ProgressCall { id?: number; ticker?: string; action?: string; date?: string; confidence?: string;
  confidence_bucket?: string; direction?: string; edge_pct?: number | null; hit?: boolean | null;
  horizon_used?: string; ret_1w_pct?: number | null; ret_4w_pct?: number | null; specialists?: string[]; status?: string }
export interface AgentProgress {
  source: string; paid_analysis: false;
  history: { state: string; count: number; available: boolean; first_captured_at: string | null; note: string };
  trend: { available: boolean; reason: string | null };
  agents: ProgressAgent[]; runs: ProgressRun[];
  current_scorecard: { available: boolean; stato: string; error: string | null; computed_at: string | null } | null;
  method: { score: string; attribution: string; horizon: string; comparison: string; learning: string; timing: string };
}

export async function getAgentProgress(signal?: AbortSignal): Promise<AgentProgress> {
  const headers = requestHeaders();
  const response = await fetch(`${API_BASE}/agents/progress?limit=100`, {
    signal, headers,
  });
  if (response.status === 401 || response.status === 403) clearSessionAndReload();
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(leggiDetail(body?.detail) || `HTTP ${response.status}`);
  }
  return response.json();
}

export const progressNumber = (value: number | null | undefined, digits = 1) =>
  value == null || !Number.isFinite(value) ? tr('progress.na') : new Intl.NumberFormat(localeDi(linguaCorrente()), {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  }).format(value);
export const progressPercent = (value: number | null | undefined) =>
  value == null || !Number.isFinite(value) ? tr('progress.na') : `${progressNumber(value)}%`;
export const qualityLabels = (): Record<string, string> => ({
  ok: tr('progress.quality_ok'), missing: tr('progress.quality_missing'), empty: tr('progress.quality_empty'),
  invalid: tr('progress.quality_invalid'), degraded: tr('progress.quality_degraded'), partial: tr('progress.quality_partial'),
  small_sample: tr('progress.quality_small_sample'), stale: tr('progress.quality_stale'), time_unknown: tr('progress.quality_time_unknown'),
  quality_unknown: tr('progress.quality_unknown'), cohort_unverified: tr('progress.quality_cohort_unverified'),
});
