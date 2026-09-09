import { API_BASE, clearSessionAndReload, getSessionToken } from './api';

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
}
export interface ProgressRun {
  run_id: string; memo_id: number; started_at: string; completed_at: string; captured_at: string;
  score_error: string | null;
  reflection: { text: string | null; status: string; kind: string; implementation_verified: boolean; performance_proven: boolean };
}
export interface AgentProgress {
  source: string; paid_analysis: false;
  history: { state: string; count: number; available: boolean; first_captured_at: string | null; note: string };
  trend: { available: boolean; reason: string | null };
  agents: ProgressAgent[]; runs: ProgressRun[];
  current_scorecard: { available: boolean; stato: string; error: string | null; computed_at: string | null } | null;
  method: { score: string; attribution: string; horizon: string; comparison: string; learning: string; timing: string };
}

export async function getAgentProgress(signal?: AbortSignal): Promise<AgentProgress> {
  const token = getSessionToken();
  const response = await fetch(`${API_BASE}/agents/progress?limit=100`, {
    signal, headers: token ? { 'X-BB-Token': token } : {},
  });
  if (response.status === 401 || response.status === 403) clearSessionAndReload();
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === 'string' ? body.detail : `Lettura progressi non disponibile (${response.status})`);
  }
  return response.json();
}

export const progressNumber = (value: number | null | undefined, digits = 1) =>
  value == null || !Number.isFinite(value) ? 'n.d.' : new Intl.NumberFormat('it-IT', {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  }).format(value);
export const progressPercent = (value: number | null | undefined) =>
  value == null || !Number.isFinite(value) ? 'n.d.' : `${progressNumber(value)}%`;
export const QUALITY_LABELS: Record<string, string> = {
  ok: 'Misura disponibile', missing: 'Misura assente', empty: 'Campione vuoto',
  invalid: 'Dati incoerenti', degraded: 'Misura fallita', partial: 'Copertura parziale',
  small_sample: 'Campione piccolo', stale: 'Dato non fresco alla rilevazione', time_unknown: 'Data non verificabile',
  quality_unknown: 'Copertura non verificabile', cohort_unverified: 'Coorte non verificabile',
};
