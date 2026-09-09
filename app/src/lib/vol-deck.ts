import { API_BASE, getSessionToken } from './api';
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
  source: string; contract?: string; expiry?: string; quote_timestamp?: string | null;
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

export async function volRequest<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const token = getSessionToken();
  const response = await fetch(API_BASE + path, {
    method: body === undefined ? 'GET' : 'POST', signal,
    headers: { ...(token ? { 'X-BB-Token': token } : {}), ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.detail;
    throw new Error(response.status === 401 || response.status === 403
      ? 'Sessione scaduta o accesso negato. Accedi di nuovo prima di caricare i dati.'
      : typeof detail === 'string' ? detail : `Richiesta fallita (HTTP ${response.status})`);
  }
  if (!payload || typeof payload !== 'object') throw new Error('Risposta vuota o non leggibile');
  return payload as T;
}

/** A stale response may never paint the next ticker; failure/paused are resumable terminal states. */
export async function watchDownload(id: string, ticker: string,
  read: () => Promise<DownloadStatus>, update: (status: DownloadStatus) => void,
  signal: AbortSignal, interval = 650): Promise<DownloadStatus> {
  const cancelled = () => { if (signal.aborted) throw new DOMException('Caricamento interrotto', 'AbortError'); };
  for (;;) {
    cancelled();
    const result = await read();
    cancelled();
    if (result.id !== id || result.ticker !== ticker) throw new Error('Risposta download con identità diversa dal ticker richiesto');
    update(result);
    if (!['queued', 'running'].includes(result.state)) return result;
    await new Promise<void>((resolve, reject) => {
      const abort = () => { clearTimeout(timer); reject(new DOMException('Caricamento interrotto', 'AbortError')); };
      const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, interval);
      signal.addEventListener('abort', abort, { once: true });
      if (signal.aborted) { signal.removeEventListener('abort', abort); abort(); }
    });
  }
}

export function numberInput(text: string, label: string): number {
  const result = leggiNumeroConSegno(text);
  if (!result || !result.ok) throw new Error(`${label}: ${result && !result.ok ? result.motivo : 'scrivi un numero'}`);
  return result.valore;
}

export const numericText = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? '' : String(value).replace('.', ',');
export const volNumber = (value: number | null | undefined, digits = 2) => value == null || !Number.isFinite(value)
  ? 'n.d.' : value.toLocaleString('it-IT', { maximumFractionDigits: digits, minimumFractionDigits: digits });

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
    days: '', iv: '', premium: '', multiplier: '', source: 'Ipotesi manuale: completa tutti i campi.' };
}

export function contractLeg(row: OptionContract, side: 'buy' | 'sell'): LegDraft {
  const premium = side === 'buy' ? row.ask : row.bid;
  const usableQuote = row.bid != null && row.ask != null && row.ask >= row.bid && row.ask > 0;
  return { ...blankLeg(), type: row.type, side, strike: numericText(row.strike),
    days: numericText(Math.max(0, daysToExpiry(row.expiry))), iv: numericText(row.iv == null ? null : row.iv * 100),
    premium: numericText(usableQuote ? premium : null), multiplier: numericText(row.multiplier),
    contract: row.contract || undefined, expiry: row.expiry, quote_timestamp: row.quote_timestamp,
    source: `${side === 'buy' ? 'Ask' : 'Bid'} osservato · ${row._source} · ${row.quote_timeframe || 'ritardo n.d.'}. Modificabile come ipotesi.` };
}

export function serializeLegs(legs: LegDraft[]) {
  return legs.map((leg, index) => ({ type: leg.type, side: leg.side,
    quantity: numberInput(leg.quantity, `Gamba ${index + 1}, quantità`),
    strike: numberInput(leg.strike, `Gamba ${index + 1}, strike`),
    days: numberInput(leg.days, `Gamba ${index + 1}, giorni`),
    iv: numberInput(leg.iv, `Gamba ${index + 1}, IV`) / 100,
    premium: numberInput(leg.premium, `Gamba ${index + 1}, premio`),
    multiplier: numberInput(leg.multiplier, `Gamba ${index + 1}, moltiplicatore`),
  }));
}
