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
  /** Restituito dal backend (audit 09/10 §3), finora assente dal tipo. */
  close?: number | null;
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
  /** Contratto backend cbcfd0b (10/10): istante del job e completezza del download. */
  download_complete?: boolean; snapshot_at?: string | null; spot_timestamp?: string | null;
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
  rows: { expiry: string; days: number; status: 'loaded' | 'partial' | 'excluded' | 'error'; reason: string | null; n_contracts?: number;
    chain_complete?: boolean; selection_kind?: string }[];
  download_complete?: boolean; partial_expiries?: string[]; stopped_after_rate_limit?: boolean;
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

/* ===========================================================================
   VOL DECK — superficie, fette e quote osservate (09/10/2026, Opus 5.5).
   Funzioni PURE: niente React, niente richieste. Regole:
   - un null resta null (buco) dalla risposta al disegno: mai 0, mai un valore di ripiego;
   - la griglia del builder (iv_grid) e' gia' un'interpolazione lineare fra strike osservati
     (+ mediana a 3 punti dichiarata in `smoothing`): qui NON si interpola altro;
   - i punti MISURATI sono le quote dei contratti (chain scaricata), tenute separate.
   Unita': IV in FRAZIONE nel payload; giorni di calendario; moneyness = K/S sullo spot.
   =========================================================================== */
export interface SurfaceSlice {
  expiry: string; days: number; atm_iv?: number | null; iv_grid?: (number | null)[] | null;
  rr25?: number | null; bf25?: number | null; n_calls?: number; n_puts?: number;
  n_illiquidi_esclusi?: number; atm_iv_unqualified?: number | null; [extra: string]: unknown;
}
/** `atm` = atm_iv del builder (= iv_grid a K/S 1 dal 10/10). Con spot proxy il backend lo manda null e
 *  mette il grezzo in `atm_iv_unqualified`: qui resta separato in `atmUnqualified`, mai al posto di `atm`. */
export interface SurfaceRow { expiry: string; days: number; iv: (number | null)[]; atm: number | null; atmUnqualified: number | null; partial: boolean }
export interface SurfaceModel {
  grid: number[]; spot: number | null; strikes: (number | null)[]; rows: SurfaceRow[];
  excludedShort: string[]; cells: number; filledCells: number; holes: number;
  ivMin: number | null; ivMax: number | null;
}

export const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
/** Una cella e' «presente» solo se e' un numero finito e positivo: null/NaN/0/negativo = buco. */
const cell = (value: unknown): number | null => finite(value) && value > 0 ? value : null;

/** Righe della superficie disegnabile: 0–1 DTE fuori (microstruttura), dichiarati in excludedShort. */
export function surfaceModel(surface: any, partial: Iterable<string> = []): SurfaceModel {
  const grid: number[] = Array.isArray(surface?.moneyness_grid) ? surface.moneyness_grid.filter(finite) : [];
  const spot = finite(surface?.spot_est) && surface.spot_est > 0 ? surface.spot_est : null;
  const partialSet = new Set(partial);
  const rows: SurfaceRow[] = [], excludedShort: string[] = [];
  for (const s of (surface?.slices || []) as SurfaceSlice[]) {
    if (!s || typeof s.expiry !== 'string' || !finite(s.days)) continue;
    if (s.days < 2) { excludedShort.push(s.expiry); continue; }
    const raw = Array.isArray(s.iv_grid) ? s.iv_grid : [];
    rows.push({ expiry: s.expiry, days: s.days, iv: grid.map((_, i) => cell(raw[i])), atm: cell(s.atm_iv),
      atmUnqualified: cell(s.atm_iv_unqualified), partial: partialSet.has(s.expiry) });
  }
  rows.sort((a, b) => a.days - b.days);
  const values = rows.flatMap(r => r.iv.filter(finite));
  return { grid, spot, strikes: grid.map(m => spot == null ? null : Number((m * spot).toFixed(6))), rows, excludedShort,
    cells: rows.length * grid.length, filledCells: values.length, holes: rows.length * grid.length - values.length,
    ivMin: values.length ? Math.min(...values) : null, ivMax: values.length ? Math.max(...values) : null };
}

/** Scadenze che il backend dichiara parziali (chain troncata/incompleta): `status:"partial"` oppure
 *  `chain_complete:false` nella riga di copertura. Campo assente = non dichiarato, non «completo». */
export function partialExpiries(coverage: any): string[] {
  const out = new Set<string>(((coverage?.rows || []) as any[]).filter(r => r && typeof r.expiry === 'string'
    && (r.status === 'partial' || r.chain_complete === false)).map(r => r.expiry));
  for (const e of (Array.isArray(coverage?.partial_expiries) ? coverage.partial_expiries : [])) if (typeof e === 'string') out.add(e);
  return [...out].sort();
}

/** Tratti continui di una serie: un valore mancante spezza la linea (buco visibile, mai collegato). */
export function segments<T>(points: T[], value: (p: T) => number | null | undefined): T[][] {
  const out: T[][] = []; let current: T[] = [];
  for (const p of points) {
    if (finite(value(p))) current.push(p); else if (current.length) { out.push(current); current = []; }
  }
  if (current.length) out.push(current);
  return out;
}

export interface SmilePoint { m: number; strike: number | null; iv: number | null }
export function smileSeries(model: SurfaceModel, expiry: string | null): SmilePoint[] {
  const row = model.rows.find(r => r.expiry === expiry);
  return model.grid.map((m, i) => ({ m, strike: model.strikes[i], iv: row ? row.iv[i] : null }));
}

export interface TermPoint { expiry: string; days: number; atm: number | null; col: number | null; partial: boolean }
/** Term structure: `atm` = ATM del builder (atm_iv, sua definizione), `col` = colonna K/S della griglia.
 *  Sono due misure diverse e restano due serie: nessuna sostituisce l'altra quando manca. */
export function termSeries(model: SurfaceModel, column: number | null): TermPoint[] {
  return model.rows.map(r => ({ expiry: r.expiry, days: r.days, atm: r.atm, partial: r.partial,
    col: column == null || column < 0 || column >= model.grid.length ? null : r.iv[column] }));
}

/** Indice del valore piu' vicino (primo a parita'); -1 su lista vuota. */
export function nearestIndex(values: number[], target: number): number {
  let best = -1, distance = Infinity;
  values.forEach((v, i) => { const d = Math.abs(v - target); if (d < distance) { distance = d; best = i; } });
  return best;
}
export const atmIndex = (grid: number[]) => nearestIndex(grid, 1);

export interface ObservedQuote {
  type: 'call' | 'put'; strike: number; m: number; iv: number; bid: number | null; ask: number | null;
  mid: number | null; oi: number | null; volume: number | null; contract: string | null;
  quoteTime: string | null; liquid: boolean; otm: boolean; quality: string[];
  /** v2 (10/10): segnalata = flag di qualita' del fornitore o quota non negoziabile per i filtri del builder
   *  (incrociata, bid <= 0, spread relativo oltre la soglia). Resta disegnata, con un SEGNO diverso. */
  flagged: boolean; flags: string[];
}
/** Soglia di spread relativo del builder (quality_filters.max_rel_spread): letta dal payload, il default
 *  e' quello dichiarato dal backend cbcfd0b (1,0) e serve solo a marcare, mai a togliere un punto. */
export const DEFAULT_MAX_REL_SPREAD = 1;
/** Quote misurate di una scadenza: IV del fornitore per contratto, senza smoothing. `otm` segue la
 *  convenzione del builder (put a K<=S, call a K>S); `liquid` = OI>0 o volume>0 (stesso filtro del
 *  builder: le illiquide si disegnano vuote, non spariscono). Senza IV: escluse e contate.
 *  v2 (10/10, MEDIA-4): rettificati (adjusted o moltiplicatore != 100) ESCLUSI e contati come fa il
 *  builder: la loro IV non e' quella di un contratto standard. */
export function observedQuotes(chain: OptionContract[] | null | undefined, spot: number | null,
  maxRelSpread: number = DEFAULT_MAX_REL_SPREAD): { quotes: ObservedQuote[]; withoutIv: number; adjusted: number } {
  const quotes: ObservedQuote[] = []; let withoutIv = 0, adjusted = 0;
  if (!Array.isArray(chain) || !(spot != null && spot > 0)) return { quotes, withoutIv, adjusted };
  const num = (v: unknown) => finite(v) ? v : null;
  const spreadCap = finite(maxRelSpread) && maxRelSpread > 0 ? maxRelSpread : DEFAULT_MAX_REL_SPREAD;
  for (const c of chain) {
    if (!c || !finite(c.strike) || c.strike <= 0 || (c.type !== 'call' && c.type !== 'put')) continue;
    if (c.adjusted === true || (finite(c.multiplier) && c.multiplier !== 100)) { adjusted++; continue; }
    if (!finite(c.iv) || c.iv <= 0) { withoutIv++; continue; }
    const oi = num(c.oi), volume = num(c.volume), bid = num(c.bid), ask = num(c.ask);
    const quality = Array.isArray(c.quality) ? c.quality.filter(q => typeof q === 'string') : [];
    const flags = [...quality];
    if (bid != null && ask != null) {
      if (ask < bid) flags.push(tr('voldeck.n_flag_crossed'));
      else if (bid <= 0) flags.push(tr('voldeck.n_flag_no_bid'));
      else if ((ask - bid) / ((ask + bid) / 2) > spreadCap) flags.push(tr('voldeck.n_flag_wide'));
    }
    quotes.push({ type: c.type, strike: c.strike, m: c.strike / spot, iv: c.iv, bid, ask,
      mid: num(c.mid), oi, volume, contract: c.contract ?? null, quoteTime: c.quote_timestamp ?? null,
      liquid: (oi ?? 0) > 0 || (volume ?? 0) > 0, otm: c.type === 'put' ? c.strike <= spot : c.strike > spot,
      quality, flagged: flags.length > 0, flags });
  }
  quotes.sort((a, b) => a.strike - b.strike || a.type.localeCompare(b.type));
  return { quotes, withoutIv, adjusted };
}

/** MEDIA-6 (10/10): la chain del job si legge a pagine fino in fondo (`next_offset`), mai la sola prima
 *  pagina. `maxPages` e' un tetto di sicurezza: se scatta la lettura e' dichiarata troncata. Un
 *  `error` della riga (download di quella scadenza interrotto) non butta i contratti gia' ricevuti:
 *  resta come motivo dichiarato, insieme a `chainComplete`. */
export interface WholeChain {
  chain: OptionContract[]; pages: number; truncated: boolean; chainComplete: boolean | null;
  rowError: string | null; total: number | null;
}
export const CHAIN_PAGE_LIMIT = 1000;
export async function fetchWholeChain(request: (path: string) => Promise<ChainPage>, downloadId: string, expiry: string,
  { limit = CHAIN_PAGE_LIMIT, maxPages = 50 }: { limit?: number; maxPages?: number } = {}): Promise<WholeChain> {
  const chain: OptionContract[] = [];
  let offset = 0, pages = 0, last: ChainPage | null = null;
  for (;;) {
    const page = await request(`/options/download/${encodeURIComponent(downloadId)}/chain?expiry=${encodeURIComponent(expiry)}&offset=${offset}&limit=${limit}`);
    pages++; last = page;
    chain.push(...(Array.isArray(page?.chain) ? page.chain : []));
    if (page?.has_more !== true) break;
    const next = page.next_offset;
    if (!finite(next) || next <= offset) throw new Error(tr('voldeck.n_chain_offset_stuck', { e: expiry, o: offset }));
    if (pages >= maxPages) return { chain, pages, truncated: true, chainComplete: page.chain_complete ?? null,
      rowError: page.error ? String(page.error) : null, total: finite(page.filtered_contracts) ? page.filtered_contracts : null };
    offset = next;
  }
  return { chain, pages, truncated: false, chainComplete: typeof last?.chain_complete === 'boolean' ? last.chain_complete : null,
    rowError: last?.error ? String(last.error) : null, total: finite(last?.filtered_contracts) ? last!.filtered_contracts as number : null };
}

/** MEDIA-8 (10/10): scala y dello smile da griglia + quote OTM liquide e non segnalate nel dominio. Le
 *  illiquide/segnalate fuori scala NON allargano l'asse: si tagliano e si contano (`clipped`). */
export function smileScale(gridIvs: (number | null)[], quotes: ObservedQuote[]): { y0: number; y1: number; clipped: ObservedQuote[] } | null {
  const solid = [...gridIvs.filter(finite), ...quotes.filter(q => q.liquid && !q.flagged).map(q => q.iv)];
  if (!solid.length) return null;
  const lo = Math.min(...solid), hi = Math.max(...solid);
  const pad = Math.max(0.005, (hi - lo) * 0.12);
  const y0 = lo - pad, y1 = hi + pad;
  return { y0, y1, clipped: quotes.filter(q => q.iv < y0 || q.iv > y1) };
}

/** Stessa regola sulla superficie 3D: la scala la fanno le celle della griglia e le quote OTM liquide non
 *  segnalate dentro la griglia; le quote fuori da quella scala non si disegnano e si contano. */
export function surfaceQuoteScale(model: SurfaceModel, observed: Record<string, ObservedQuote[] | undefined>) {
  const lo = model.grid[0], hi = model.grid[model.grid.length - 1];
  const quotes = model.rows.flatMap(r => (observed[r.expiry] || []).filter(q => q.otm && q.m >= lo - 1e-9 && q.m <= hi + 1e-9));
  const scale = smileScale(model.rows.flatMap(r => r.iv), quotes);
  return { scale, clipped: scale ? scale.clipped.length : 0 };
}

/** Clic sulla superficie 3D: customdata[0] = scadenza, customdata[1] = indice di colonna (se il punto
 *  lo porta); altrimenti la colonna piu' vicina all'x cliccata sull'asse mostrato. */
export function pickFromPoint(point: any, model: SurfaceModel, axis: 'moneyness' | 'strike', ySqrt = false): { expiry: string; column: number } | null {
  if (!point) return null;
  const cd = point.customdata;
  // v3 (10/10, Opus 5.5): il 3D disegna le scadenze in √t; senza customdata la y si confronta con √giorni,
  // con tolleranza RELATIVA (v2: la y torna da WebGL in float, 1e-9 assoluto la scartava)
  const yOf = (days: number) => ySqrt ? Math.sqrt(days) : days;
  const expiry = Array.isArray(cd) && typeof cd[0] === 'string' ? cd[0] : model.rows.find(r => Math.abs(yOf(r.days) - Number(point.y)) <= 1e-6 * Math.max(1, Math.abs(Number(point.y))))?.expiry;
  if (!expiry || !model.rows.some(r => r.expiry === expiry)) return null;
  let column = Array.isArray(cd) && Number.isInteger(cd[1]) ? cd[1] as number : -1;
  if (column < 0 || column >= model.grid.length) {
    const xs = axis === 'strike' && model.spot != null ? model.strikes.map(k => k as number) : model.grid;
    column = nearestIndex(xs, Number(point.x));
  }
  return column >= 0 ? { expiry, column } : null;
}

/** La put e la call allo strike LISTATO piu' vicino a quello chiesto (nessuno strike inventato). */
export function nearestQuotes(quotes: ObservedQuote[], strike: number | null): { strike: number | null; put: ObservedQuote | null; call: ObservedQuote | null } {
  if (strike == null || !quotes.length) return { strike: null, put: null, call: null };
  const strikes = [...new Set(quotes.map(q => q.strike))];
  const k = strikes[nearestIndex(strikes, strike)];
  return { strike: k, put: quotes.find(q => q.strike === k && q.type === 'put') || null,
    call: quotes.find(q => q.strike === k && q.type === 'call') || null };
}

/** Tacche «tonde» (1-2-2,5-5 × 10^n) dentro [lo, hi]. */
export function niceTicks(lo: number, hi: number, target = 5): number[] {
  if (!finite(lo) || !finite(hi)) return [];
  if (hi <= lo) return [lo];
  const raw = (hi - lo) / Math.max(1, target), mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map(f => f * mag).find(s => s >= raw) || 10 * mag;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step - 1e-9) * step; v <= hi + step * 1e-9; v += step) out.push(Number(v.toFixed(10)));
  return out;
}

/** IV (frazione) in % col separatore della lingua; mancante -> il testo n.d. del chiamante, mai «0%». */
export function ivText(value: number | null | undefined, missing: string, digits = 1): string {
  if (!finite(value)) return missing;
  return (value * 100).toLocaleString(localeDi(linguaCorrente()), { minimumFractionDigits: digits, maximumFractionDigits: digits }) + '%';
}
export function priceText(value: number | null | undefined, missing: string): string {
  if (!finite(value)) return missing;
  const digits = Math.abs(value) >= 1000 ? 0 : Math.abs(value) >= 100 ? 1 : 2;
  return value.toLocaleString(localeDi(linguaCorrente()), { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/** Orario di un timestamp: con fuso dichiarato -> ora locale con sigla del fuso; senza fuso il testo
 *  resta quello ricevuto e `zoned` e' false (la UI lo dice: un orario senza fuso non si converte). */
export function stampText(value: unknown): { text: string; zoned: boolean } | null {
  if (typeof value !== 'string' || !value.trim()) return null;
  const trimmed = value.trim(), zoned = /(Z|[+-]\d{2}:?\d{2})$/.test(trimmed);
  if (!zoned) return { text: trimmed.replace('T', ' ').slice(0, 19), zoned: false };
  const date = new Date(trimmed);
  if (Number.isNaN(date.getTime())) return { text: trimmed, zoned: false };
  return { text: date.toLocaleString(localeDi(linguaCorrente()), { day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit', timeZoneName: 'short' }), zoned: true };
}

/** Numero col separatore della lingua e cifre fisse; mancante -> testo del chiamante. */
export function numText(value: number | null | undefined, digits: number, missing = tr('voldeck.ui_n_a_15')): string {
  if (!finite(value)) return missing;
  return value.toLocaleString(localeDi(linguaCorrente()), { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
/** Percentuale da frazione col separatore della lingua (tacche degli assi: fino a 1 decimale). */
export function pctTickText(value: number): string {
  return (Math.round(value * 1000) / 10).toLocaleString(localeDi(linguaCorrente()), { maximumFractionDigits: 1 }) + '%';
}

/** Orario sul fuso di NEW YORK (seduta delle opzioni USA). Senza fuso nel testo: mostrato come ricevuto,
 *  `zoned:false` (un orario senza fuso non si converte). `clock` = solo ore:minuti. */
export function nyTime(value: unknown, clock = false): { text: string; zoned: boolean } | null {
  if (typeof value !== 'string' || !value.trim()) return null;
  const trimmed = value.trim(), zoned = /(Z|[+-]\d{2}:?\d{2})$/.test(trimmed);
  if (!zoned) return { text: trimmed.replace('T', ' ').slice(0, clock ? 16 : 19), zoned: false };
  const date = new Date(trimmed);
  if (Number.isNaN(date.getTime())) return { text: trimmed, zoned: false };
  const text = date.toLocaleString(localeDi(linguaCorrente()), { timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', hour12: false,
    ...(clock ? {} : { day: '2-digit', month: '2-digit' }) });
  return { text: `${text} New York`, zoned: true };
}

/** Campi del contratto backend cbcfd0b (10/10), coi nomi REALI: letti solo se presenti, mai dedotti. */
export interface Freshness {
  quoteFrom: string | null; quoteTo: string | null; spotAt: string | null; spotKind: string | null;
  session: string | null; marketOpen: boolean | null; sessionNote: string | null; asofNewYork: string | null;
  delay: string | null; delayNote: string | null; snapshotAt: string | null; snapshotKind: string | null;
  partial: boolean; stoppedAfterRateLimit: boolean;
  spotQualified: boolean; spotFallback: boolean; spotProxy: string | null;
}
export function surfaceFreshness(surface: any): Freshness {
  const str = (v: unknown) => typeof v === 'string' && v.trim() ? v : null;
  const session = surface?.market_session && typeof surface.market_session === 'object' ? surface.market_session : null;
  return { quoteFrom: str(surface?.quote_time_min), quoteTo: str(surface?.quote_time_max),
    spotAt: str(surface?.spot_timestamp), spotKind: str(surface?.spot_timestamp_kind),
    session: str(session?.session_date), marketOpen: typeof session?.market_open === 'boolean' ? session.market_open : null,
    sessionNote: str(session?.note), asofNewYork: str(session?.asof_new_york),
    delay: str(surface?.data_delay), delayNote: str(surface?.data_delay_note),
    snapshotAt: str(surface?.snapshot_at) ?? str(surface?._timestamp), snapshotKind: str(surface?.snapshot_kind),
    partial: surface?.partial === true, stoppedAfterRateLimit: surface?.coverage?.stopped_after_rate_limit === true,
    spotQualified: surface?.spot_qualified === true, spotFallback: surface?.spot_fallback === true, spotProxy: str(surface?.spot_proxy) };
}

/** La rotta che RISCARICA (/options/vol_surface) accetta al massimo 12 scadenze esplicite (422 oltre). */
export const MAX_REFETCH_EXPIRIES = 12;

/** Quote scartate dal builder per motivo, sommate sulle scadenze (codici macchina del backend). */
export function discardTotals(slices: any[]): { total: number; adjusted: number; byReason: Record<string, number> } {
  const byReason: Record<string, number> = {};
  let total = 0, adjusted = 0;
  for (const s of Array.isArray(slices) ? slices : []) {
    if (finite(s?.n_quote_scartate)) total += s.n_quote_scartate;
    if (finite(s?.n_rettificati_esclusi)) adjusted += s.n_rettificati_esclusi;
    for (const [k, v] of Object.entries(s?.quote_discards || {})) if (finite(v)) byReason[k] = (byReason[k] || 0) + v;
  }
  return { total, adjusted, byReason };
}
