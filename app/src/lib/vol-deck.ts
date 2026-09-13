import { linguaCorrente, localeDi } from '@/i18n/lingua';
import { t as tr } from '@/i18n/t';
import { API_BASE, requestHeaders } from './api';
import { leggiNumeroConSegno } from './cassa';

export interface OptionContract {
  contract: string | null; type: 'call' | 'put'; strike: number | null; expiry: string;
  iv: number | null; delta: number | null; gamma: number | null; theta: number | null;
  vega: number | null; rho: number | null; bid: number | null; ask: number | null;
  mid: number | null; oi: number | null; volume: number | null; multiplier: number | null;
  exercise_style: string | null; quote_timestamp: string | null; quote_timeframe: string | null;
  adjusted?: boolean; quote_age_seconds?: number | null;
  quality: string[]; _source: string;
}
export interface ExpiryCatalog {
  ticker: string; expirations: string[]; complete: boolean; next_after: string | null;
  requests_used: number; request_budget: number; error: string | null; _timestamp: string;
}
export interface ChainPage {
  ticker: string; expiry: string; chain: OptionContract[]; complete: boolean;
  next_cursor: string | null; spot: number | null; spot_timeframe: string | null;
  spot_timestamp_ns: number | null; _timestamp: string; error: string | null;
  malformed_contracts: number; cached: boolean;
  n_contracts?: number; filtered_contracts?: number; chain_complete?: boolean;
  has_more?: boolean; next_offset?: number | null; offset?: number; limit?: number;
}
export interface DownloadStatus {
  id: string; ticker: string; state: 'queued' | 'running' | 'paused' | 'error' | 'complete';
  phase: 'catalog' | 'chain' | 'done'; scope: 'all' | 'selected';
  expirations: string[]; catalog_complete: boolean; completed_expiries: number;
  current_expiry: string | null; pages_received: number; n_contracts: number;
  duplicates: number; malformed_contracts: number; error: string | null;
  superseded_contracts?: number; page_revisions?: number;
  download_complete: boolean; retryable: boolean; updated_at: string; started_at: string;
  snapshot_at: string | null; cache_ttl_seconds: number;
  retention_seconds: number; stale: boolean; pause_requested: boolean;
  spot: number | null; spot_source: string | null; spot_error: string | null;
  rows: { expiry: string; n_contracts: number; complete: boolean; error: string | null }[];
}
export interface Coverage {
  requested: string[]; loaded: string[]; excluded: string[]; errors: string[]; complete: boolean;
  rows: { expiry: string; days: number; status: 'loaded' | 'partial' | 'excluded' | 'error'; reason: string | null; n_contracts?: number }[];
  download_complete?: boolean;
}
export interface LegDraft {
  id: string; type: 'call' | 'put'; side: 'buy' | 'sell'; quantity: string; strike: string;
  days: string; iv: string; premium: string; multiplier: string;
  source: string; sourceKind?: 'manual' | 'edited' | 'derived' | 'observed'; sourceProvider?: string; sourceTimeframe?: string; sourceQuote?: 'Ask' | 'Bid'; contract?: string; expiry?: string; quote_timestamp?: string | null;
}
export interface StrategyPoint { price: number; pnl: number; delta: number | null; gamma: number | null; vega: number | null; theta: number | null; rho: number | null }
export interface StrategyResult {
  currency: string; model: string; model_source: string; greek_units: Record<string, string>;
  entry_cost: number; net_premium: number; fees: number; entry_kind: 'debit' | 'credit';
  same_expiry: boolean; expiry_days: number; breakevens: number[];
  breakeven_intervals?: { from: number; to: number | null }[];
  max_profit: number | null; max_loss: number | null; unlimited_profit: boolean; unlimited_loss: boolean;
  today: StrategyPoint; scenario: StrategyPoint;
  curve: { price: number; expiry: number | null; today: number; scenario: number }[];
  heatmap: { elapsed_days: number; cells: StrategyPoint[] }[];
  assumptions: Record<string, unknown>; limits: string[];
}

// 13/09: who wrote the error text. Only a backend `detail` is 'backend'; a network failure, an
// unreadable body or a locally worded HTTP status is 'client', so no panel can present it as
// «reported by the backend».
export type VolErrorOrigin = 'backend' | 'client';
const withOrigin = (error: Error, origin: VolErrorOrigin) => Object.assign(error, { origin });

export async function volRequest<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const headers = requestHeaders();
  const response = await fetch(API_BASE + path, {
    method: body === undefined ? 'GET' : 'POST', signal,
    headers: { ...headers, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  }).catch((error: unknown) => { throw withOrigin(error instanceof Error ? error : new Error(String(error)), 'client'); });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.detail;
    if (response.status !== 401 && response.status !== 403 && typeof detail === 'string') throw withOrigin(new Error(detail), 'backend');
    throw withOrigin(new Error(response.status === 401 || response.status === 403
      ? tr('voldeck.ui_session_expired_or_access_denied_sign_in_again_before__282')
      : tr('voldeck.fmt_request_failed_http_a__18', {a: response.status})), 'client');
  }
  if (!payload || typeof payload !== 'object') throw withOrigin(new Error(tr('voldeck.ui_empty_or_unreadable_response_283')), 'client');
  return payload as T;
}

/** A stale response may never paint the next ticker; failure/paused are resumable terminal states. */
export async function watchDownload(id: string, ticker: string,
  read: () => Promise<DownloadStatus>, update: (status: DownloadStatus) => void,
  signal: AbortSignal, interval = 650): Promise<DownloadStatus> {
  const cancelled = () => { if (signal.aborted) throw new DOMException(tr('voldeck.ui_loading_interrupted_284'), 'AbortError'); };
  for (;;) {
    cancelled();
    const result = await read();
    cancelled();
    if (result.id !== id || result.ticker !== ticker) throw new Error(tr('voldeck.ui_download_response_identity_differs_from_the_requested__285'));
    update(result);
    if (!['queued', 'running'].includes(result.state)) return result;
    await new Promise<void>((resolve, reject) => {
      const abort = () => { clearTimeout(timer); reject(new DOMException(tr('voldeck.ui_loading_interrupted_284'), 'AbortError')); };
      const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, interval);
      signal.addEventListener('abort', abort, { once: true });
      if (signal.aborted) { signal.removeEventListener('abort', abort); abort(); }
    });
  }
}

export function numberInput(text: string, label: string): number {
  const result = leggiNumeroConSegno(text);
  if (!result || !result.ok) throw new Error(`${label}: ${result && !result.ok ? result.motivo : tr('voldeck.ui_enter_a_number_286')}`);
  return result.valore;
}

export const numericText = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? ''
  : linguaCorrente() === 'it' ? String(value).replace('.', ',') : String(value);
export const volNumber = (value: number | null | undefined, digits = 2) => value == null || !Number.isFinite(value)
  ? tr('voldeck.ui_n_a_15') : value.toLocaleString(localeDi(linguaCorrente()), { maximumFractionDigits: digits, minimumFractionDigits: digits });

export function daysToExpiry(expiry: string, today = new Date()): number {
  // Calendar dates, not time-of-day arithmetic. The model explicitly omits intraday expiry timing.
  const utcToday = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
  return Math.round((Date.parse(expiry + 'T00:00:00Z') - utcToday) / 86400000);
}

export const expiriesThrough = (dates: string[], finalExpiry: string): string[] =>
  dates.filter(expiry => !finalExpiry || expiry <= finalExpiry);

export function horizonDate(months: number, today = new Date()): string {
  const year = today.getFullYear(), month = today.getMonth() + months;
  const lastDay = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  return new Date(Date.UTC(year, month, Math.min(today.getDate(), lastDay))).toISOString().slice(0, 10);
}

let nextLegId = 0;
export function blankLeg(): LegDraft {
  return { id: `leg-${++nextLegId}`, type: 'call', side: 'buy', quantity: '1', strike: '',
    days: '', iv: '', premium: '', multiplier: '', sourceKind: 'manual', source: tr('voldeck.ui_manual_assumption_complete_all_fields_287') };
}

export function contractLeg(row: OptionContract, side: 'buy' | 'sell'): LegDraft {
  const premium = side === 'buy' ? row.ask : row.bid;
  const usableQuote = row.bid != null && row.ask != null && row.ask >= row.bid && row.ask > 0;
  return { ...blankLeg(), type: row.type, side, strike: numericText(row.strike),
    days: numericText(Math.max(0, daysToExpiry(row.expiry))), iv: numericText(row.iv == null ? null : row.iv * 100),
    premium: numericText(usableQuote ? premium : null), multiplier: numericText(row.multiplier),
    contract: row.contract || undefined, expiry: row.expiry, quote_timestamp: row.quote_timestamp,
    sourceKind: 'observed', sourceProvider: row._source, sourceTimeframe: row.quote_timeframe || undefined, sourceQuote: side === 'buy' ? 'Ask' : 'Bid',
    source: tr('voldeck.fmt_observed_a_b_c_editable_as_an_assumption__19', {a: side === 'buy' ? 'Ask' : 'Bid', b: row._source, c: row.quote_timeframe || tr('voldeck.ui_delay_n_a_204')}) };
}

export function serializeLegs(legs: LegDraft[]) {
  return legs.map((leg, index) => ({ type: leg.type, side: leg.side,
    quantity: numberInput(leg.quantity, tr('voldeck.fmt_leg_a_quantity_20', {a: index + 1})),
    strike: numberInput(leg.strike, tr('voldeck.fmt_leg_a_strike_21', {a: index + 1})),
    days: numberInput(leg.days, tr('voldeck.fmt_leg_a_days_22', {a: index + 1})),
    iv: numberInput(leg.iv, tr('voldeck.fmt_leg_a_iv_23', {a: index + 1})) / 100,
    premium: numberInput(leg.premium, tr('voldeck.fmt_leg_a_premium_24', {a: index + 1})),
    multiplier: numberInput(leg.multiplier, tr('voldeck.fmt_leg_a_multiplier_25', {a: index + 1})),
  }));
}

/** Only our own provenance labels change language; provider words stay verbatim. */
export function legSource(leg: LegDraft): string {
  if (leg.sourceKind === 'manual') return tr('voldeck.ui_manual_assumption_complete_all_fields_287');
  if (leg.sourceKind === 'edited') return tr('voldeck.ui_assumption_edited_in_the_laboratory_not_a_current_quot_208');
  if (leg.sourceKind === 'derived') return tr('voldeck.ui_derived_leg_complete_the_new_fields_before_simulating_213');
  if (leg.sourceKind === 'observed') return tr('voldeck.fmt_observed_a_b_c_editable_as_an_assumption__19', {
    a: leg.sourceQuote || '', b: leg.sourceProvider || '', c: leg.sourceTimeframe || tr('voldeck.ui_delay_n_a_204'),
  });
  return leg.source;
}
