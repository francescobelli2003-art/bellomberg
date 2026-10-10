import { API_BASE, clearSessionAndReload, requestHeaders } from './api';
import { t } from '../i18n/t';

export type TradeIdeaJudgment = 'favorable' | 'rejected' | 'watch' | 'incomplete';
export type TradeIdeaTechnicalStatus = 'accepted' | 'running' | 'completed' | 'incomplete' | 'failed' | 'cancelled' | 'interrupted';
export type TradeIdeaEmailStatus = 'not_attempted' | 'preparing' | 'ready' | 'sending' | 'accepted' | 'failed' | 'uncertain' | 'blocked';

export interface TradeIdeaIdentity {
  ticker: string;
  name?: string | null;
  exchange?: string | null;
  currency?: string | null;
  status?: string;
  reason?: string | null;
}

export interface TradeIdeaModel {
  role: string;
  model: string;
  reasoning_effort?: string | null;
  status?: string | null;
  reason?: string | null;
}

export interface TradeIdeaGate {
  status: string;
  reason?: string | null;
  remaining?: number | null;
  currency?: string | null;
  limit_usd?: number | null;
  estimated_cost_usd?: number | null;
  model_prices?: Record<string, { prompt: string; completion: string }>;
}

export interface TradeIdeaDocumentSource {
  url: string; published_at: string; publication_quote: string; report_date: string; report_date_quote: string; issuer_quote: string; title?: string;
}
export interface TradeIdeaDocumentReceipt {
  status: 'not_supplied' | 'verified' | 'needs_verification'; contract: string; fingerprint?: string;
  documents: Array<{id?: string; url: string; status: string; reason?: string; sha256?: string; text_sha256?: string; published_at?: string; report_date?: string; bytes?: number; classification?: string}>;
  limitation?: string;
}
export interface TradeIdeaPreflight {
  analysis_mode?: string;
  document_sources?: TradeIdeaDocumentReceipt;
  ok: boolean;
  /** R14: presente quando lo storage Trade Idea non è pronto (preflight 200 con ok:false). */
  storage?: TradeIdeaStorage | null;
  source_qualification?: {
    status: string; reasons: string[]; method_id?: string; fingerprint?: string; analysis_mode?: string;
    /** R14: not_run = verifica mai eseguita (un controllo precedente ha bloccato), distinta da failed. */
    execution_status?: 'not_run' | 'completed' | 'failed';
    checklist?: Array<{label?: string; status?: string; reason?: string}>;
    coverage?: {sources?: {catalog_warnings?: Array<{source?: string; reason?: string}>; current_quotation?: {
      status: string; price?: number | null; currency?: string | null;
      observed_at?: string | null; acquired_as_of?: string | null; information_cutoff?: string | null;
      source_id?: string | null; quote_source_name?: string | null; exchange?: string | null;
      freshness_policy?: string | null;
      comparison_qualified?: boolean; comparison_status?: string | null;
    }}};
  };
  preparation?: {required: boolean; paid: boolean; status: string};
  ticker?: string;
  identity?: TradeIdeaIdentity | null;
  models?: TradeIdeaModel[];
  authorization?: TradeIdeaGate | null;
  budget?: TradeIdeaGate | null;
  reasons?: string[];
  active_run_id?: string | null;
  budget_limit_usd?: number | null;
  catalog_snapshot?: { checked_at?: string | null; source?: string | null; account_access_verified?: boolean | null } | null;
  valuation?: { status?: string | null; candidate_status?: string | null; candidate_reason?: string | null; reason?: string | null } | null;
}

export interface TradeIdeaUsage {
  cost_usd?: number | null;
  cost_eur?: number | null;
  partial?: boolean;
  status?: string | null;
  reason?: string | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
}

export interface TradeIdeaRun {
  analysis_mode?: string;
  id?: string;
  run_id?: string;
  ticker: string;
  company_name?: string | null;
  exchange?: string | null;
  currency?: string | null;
  identity?: TradeIdeaIdentity | null;
  view_text?: string | null;
  view_origin?: string | null;
  pm_view?: string | null;
  view_source?: string | null;
  technical_status?: TradeIdeaTechnicalStatus;
  status?: string;
  phase?: string | null;
  language?: 'it' | 'en' | string | null;
  created_at?: string | null;
  started_at?: string | null;
  updated_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
  reason?: string | null;
  models?: TradeIdeaModel[];
  usage?: TradeIdeaUsage | null;
  destination?: TradeIdeaDestination | null;
  /** R14 seguito (B6): sulle run SALVATE la controverifica fonti è `completed` (registrata all'accettazione)
   *  o `unknown_legacy` (run anteriori al marcatore: dato storico non registrato, né eseguita né fallita). */
  source_qualification?: { status?: string; execution_status?: 'completed' | 'unknown_legacy' | string | null } | null;
}

export interface TradeIdeaProgressEntry {
  id?: string;
  name?: string;
  role?: string;
  status: string;
  round?: string | number | null;
  detail?: string | null;
  updated_at?: string | null;
}

export interface TradeIdeaToolEntry {
  name: string;
  specialist?: string | null;
  status?: string | null;
  count?: number | null;
  at?: string | null;
  detail?: string | null;
}

export interface TradeIdeaProgress {
  reports?: TradeIdeaResult['reports'];
  red_team?: TradeIdeaResult['red_team'];
  phase?: string | null;
  updated_at?: string | null;
  specialists?: TradeIdeaProgressEntry[];
  tools?: TradeIdeaToolEntry[];
  events?: Array<{ at?: string | null; level?: string | null; message: string }>;
  report_quality?: TradeIdeaReportQuality | null;
}

export interface TradeIdeaDestination {
  kind: 'dcn' | 'research' | 'none';
  decision_id?: number | string | null;
  reason?: string | null;
}

export interface TradeIdeaResult {
  ticker?: string;
  judgment?: TradeIdeaJudgment | null;
  summary?: string | null;
  pm_view_response?: string | null;
  pros?: string[];
  cons?: string[];
  risks?: string[];
  catalysts?: string[];
  invalidation?: string[] | string | null;
  data_gaps?: string[];
  review_conditions?: string[];
  scenarios?: Array<{ name: string; analysis: string; evidence_ids?: string[] }>;
  objections?: Array<{ objection: string; response: string; resolved: boolean; evidence_ids?: string[] }>;
  history_review?: Array<{ kind: 'decision' | 'trade'; id: number; response: string }>;
  evidence?: Array<{ id: string; source: string; as_of: string; summary: string; url?: string | null }>;
  dossier?: Array<{ key: string; title: string; paragraphs: string[]; evidence_ids?: string[]; tables?: Array<{ title: string; columns: string[]; rows: string[][]; source: string; unit: string; period: string }>; charts?: Array<{ table_index: number; kind: string; label_column: number; value_columns: number[] }> }>;
  proposal?: { ticker: string; action: string; eur_amount?: number | null; timing: string; confidence: string; rationale: string; sizing_source?: string | null } | null;
  valuation?: { status?: string; method?: string | null; summary?: string | null; reason?: string | null } | null;
  reports?: Array<{ specialist: string; round?: string | number | null; title?: string | null; text?: string | null; status?: string | null }>;
  red_team?: { status?: string; text?: string | null; objections?: string[]; unresolved?: string[]; reason?: string | null } | null;
  memo?: string | null;
  destination?: TradeIdeaDestination | null;
}

export interface TradeIdeaArtifact {
  id: string;
  name: string;
  kind: string;
  status: string;
  reason?: string | null;
  sha256?: string | null;
  download_url?: string | null;
}

export interface TradeIdeaEmail {
  status: TradeIdeaEmailStatus;
  error?: string | null;
  reason?: string | null;
  attempted_at?: string | null;
  accepted_at?: string | null;
}

/** Costo della catena di run (backend `_cost_summary`): importi in USD come stringhe decimali. */
export interface TradeIdeaCostSummary extends TradeIdeaUsage {
  budget_limit_usd?: string | number | null;
  charged_usd?: string | null;
  reserved_usd?: string | null;
  unknown_requests?: number | null;
  held_unknown_requests?: number | null;
  unacknowledged_unknown_requests?: number | null;
  unknown_reserved_usd?: string | null;
  remaining_after_holds_usd?: string | null;
  overrun?: boolean;
}

/** Esito di POST /runs/{id}/costs/reconcile per una richiesta dal costo incerto.
 *  settleable = il provider ha la fattura misurata (anteprima, nulla scritto); settled = registrata;
 *  pending = resta incerta, sempre con il motivo. */
export interface TradeIdeaCostOutcome {
  request_id: string;
  status: 'settleable' | 'settled' | 'pending';
  charged_usd?: string | number | null;
  reason?: string | null;
  run_id?: string;
  role?: string | null;
  model?: string | null;
  reserved_usd?: string | number | null;
}
export interface TradeIdeaCostReconciliation {
  run_id?: string;
  apply?: boolean;
  outcomes: TradeIdeaCostOutcome[];
  settled: number;
  pending: number;
}

/** Sezioni del PDF con la pagina in cui iniziano (manifest `pdf_quality.section_pages`,
 *  progress `report_quality.section_pages`): chiavi del dossier più executive, recommendation,
 *  thesis, risks, data_notes, sources, annex. Ogni chiave può mancare. */
export type TradeIdeaSectionPages = Record<string, number>;
export interface TradeIdeaReportQuality {
  status?: string | null;
  pages?: number | Record<string, number> | null;
  section_pages?: TradeIdeaSectionPages | null;
  reasons?: string[] | null;
}

export interface TradeIdeaDetail {
  run: TradeIdeaRun;
  progress?: TradeIdeaProgress | null;
  result?: TradeIdeaResult | null;
  artifacts?: TradeIdeaArtifact[];
  email?: TradeIdeaEmail | null;
  cost?: TradeIdeaCostSummary | null;
  states?: Record<string, string> | null;
  artifact_availability?: { status?: string; reason?: string };
  recovery?: { successor_run_id?: string | null; can_continue?: boolean; can_recover_delivery?: boolean;
    price_refresh_required?: boolean; can_continue_with_price_refresh?: boolean;
    can_complete_truncated_response?: boolean; requires_truncated_response_authorization?: boolean;
    can_retry_failed_response?: boolean; requires_failed_response_authorization?: boolean;
    failed_response_recovery?: { request_id: string; desk: string; round_n: number;
      replacement_max_tokens: number; native_validation_required: boolean } | null;
    truncated_response_recovery?: { request_id: string; desk: string; round_n: number;
      replacement_max_tokens: number; native_validation_required: boolean } | null;
    checkpoint_available?: boolean; blocked_reason?: string | null; remaining_work?: {
      remaining?: string[]; feasibility?: string; reason?: string;
      largest_output_reservation_floor_usd?: string | null;
    } | null } | null;
  primary_failure?: { phase?: string; desk?: string; request_id?: string; message?: string; error?: string } | null;
}

export interface TradeIdeaList {
  runs: TradeIdeaRun[];
  total?: number;
}

/** R14: diagnosi dello storage Trade Idea non pronto (503 delle rotte, `storage` del preflight,
 *  `trade_idea_storage` di GET /decisions). Solo `update_required` (schema_absent/schema_partial) giustifica
 *  la migrazione esplicita; `status: lookup_failed` (lettura non classificata) è un guasto d'accesso, mai migrazione. */
export interface TradeIdeaStorage {
  status: string;
  error_code: string;
  update_required: boolean;
  action: string;
  documentation?: string | null;
}

/** Accetta la diagnosi solo se ha la forma del contratto: un oggetto storto non si mostra come diagnosi. */
export function readTradeIdeaStorage(value: unknown): TradeIdeaStorage | null {
  if (!value || typeof value !== 'object') return null;
  const s = value as Record<string, unknown>;
  if (typeof s.status !== 'string' || typeof s.action !== 'string' || typeof s.update_required !== 'boolean'
      || typeof s.error_code !== 'string') return null;
  return { status: s.status, error_code: s.error_code, update_required: s.update_required, action: s.action,
    documentation: typeof s.documentation === 'string' ? s.documentation : null };
}

export class TradeIdeaApiError extends Error {
  constructor(message: string, public readonly status: number,
    public readonly errorCode?: string, public readonly storage: TradeIdeaStorage | null = null) {
    super(message); this.name = 'TradeIdeaApiError';
  }
}

/** Errore HTTP dal corpo JSON: conserva detail, error_code e storage (R14), non il solo messaggio. */
export function tradeIdeaApiError(value: unknown, status: number): TradeIdeaApiError {
  const body = value && typeof value === 'object' ? value as Record<string, unknown> : null;
  return new TradeIdeaApiError(errorMessage(value, status), status,
    typeof body?.error_code === 'string' ? body.error_code : undefined, readTradeIdeaStorage(body?.storage));
}

export const storageOf = (error: unknown): TradeIdeaStorage | null =>
  error instanceof TradeIdeaApiError ? error.storage : null;

function errorMessage(value: unknown, status: number): string {
  const detail = value && typeof value === 'object' && 'detail' in value ? (value as { detail: unknown }).detail : value;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string') return detail.message;
  if (Array.isArray(detail)) return detail.map(item => typeof item?.msg === 'string' ? item.msg : String(item)).join('; ');
  return `HTTP ${status}`;
}

export async function tradeIdeaRequest<T>(path: string, body?: object, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}/trade-ideas${path}`, {
    method: body ? 'POST' : 'GET',
    headers: { ...requestHeaders(), ...(body ? { 'Content-Type': 'application/json' } : {}) },
    cache: 'no-store', signal,
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  if (response.status === 401 || response.status === 403) clearSessionAndReload();
  const value = await response.json().catch(() => null);
  if (!response.ok) throw tradeIdeaApiError(value, response.status);
  if (value === null) throw new TradeIdeaApiError(`HTTP ${response.status}: ${t('tradeidea.apiNoJson')}`, response.status);
  return value as T;
}

const documentRequest = (sources: TradeIdeaDocumentSource[]) => sources.map(source =>
  Object.fromEntries(Object.entries(source).filter(([,value]) => typeof value === 'string' && value.trim())));

export const TradeIdeas = {
  preflight: (ticker: string, pmView: string, viewSource: string, budgetLimitUsd: number, signal?: AbortSignal, documentSources: TradeIdeaDocumentSource[] = []) =>
    tradeIdeaRequest<TradeIdeaPreflight>('/preflight', { ticker, pm_view: pmView, view_source: viewSource, budget_limit_usd: budgetLimitUsd, ...(documentSources.length ? {document_sources: documentRequest(documentSources)} : {}) }, signal),
  start: (ticker: string, pmView: string, viewSource: string, budgetLimitUsd: number, idempotencyKey: string, sourceFingerprint: string, documentSources: TradeIdeaDocumentSource[] = []) =>
    tradeIdeaRequest<{ run_id: string; status: string }>('/runs', {
      ticker, pm_view: pmView, view_source: viewSource, budget_limit_usd: budgetLimitUsd,
      idempotency_key: idempotencyKey, cost_acknowledged: true,
      ...(documentSources.length ? {document_sources: documentRequest(documentSources)} : {}),
      authorization: {accepted: true, source_fingerprint: sourceFingerprint,
        activities: ['committee'], max_revision_rounds: 0},
    }),
  active: () => tradeIdeaRequest<{ run_id: string | null }>('/active'),
  list: (filters: { ticker?: string; status?: string; limit?: number; offset?: number } = {}) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) if (value !== undefined && value !== '') params.set(key, String(value));
    return tradeIdeaRequest<TradeIdeaList>(`/runs${params.size ? '?' + params : ''}`);
  },
  detail: (runId: string) => tradeIdeaRequest<TradeIdeaDetail>(`/runs/${encodeURIComponent(runId)}`),
  stop: (runId: string) => tradeIdeaRequest<{ run_id: string; status: string }>(`/runs/${encodeURIComponent(runId)}/stop`, {}),
  resume: (runId: string, idempotencyKey: string, recoverTruncatedRequestId?: string, authorizePriceRefresh = false, recoverFailedRequestId?: string) => tradeIdeaRequest<{ run_id: string; status: string }>(`/runs/${encodeURIComponent(runId)}/resume`, { idempotency_key: idempotencyKey, cost_acknowledged: true, ...(recoverTruncatedRequestId ? { recover_truncated_request_id: recoverTruncatedRequestId } : {}), ...(authorizePriceRefresh ? { authorize_price_refresh: true } : {}), ...(recoverFailedRequestId ? { recover_failed_request_id: recoverFailedRequestId } : {}) }),
  recoverDelivery: (runId: string) => tradeIdeaRequest<TradeIdeaDetail>(`/runs/${encodeURIComponent(runId)}/delivery/recover`, {}),
  /** Costi incerti: `apply: false` è l'anteprima (lettura gratuita della fattura del provider, nessuna
   *  scrittura); `apply: true` registra gli importi verificati e va chiamata solo dopo la conferma esplicita
   *  del PM. 409 se la run è in corso. */
  reconcileCosts: (runId: string, apply: boolean) =>
    tradeIdeaRequest<TradeIdeaCostReconciliation>(`/runs/${encodeURIComponent(runId)}/costs/reconcile`, { apply }),
  retryEmail: (runId: string, acknowledgeUncertain = false) => tradeIdeaRequest<TradeIdeaEmail>(`/runs/${encodeURIComponent(runId)}/email/retry`, { acknowledge_uncertain: acknowledgeUncertain }),
};

export async function tradeIdeaArtifactBlob(runId: string, artifactId: string): Promise<Blob> {
  const response = await fetch(`${API_BASE}/trade-ideas/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifactId)}`, {
    headers: requestHeaders(), cache: 'no-store',
  });
  if (response.status === 401 || response.status === 403) clearSessionAndReload();
  if (!response.ok) throw tradeIdeaApiError(await response.json().catch(() => null), response.status);
  return response.blob();
}
