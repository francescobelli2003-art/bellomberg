import axios from 'axios';
import { linguaCorrente } from '../i18n/lingua';
import type { SalvaPreferenza } from '../i18n/preferenze';
import type { TradeRequest, TradeResult, TradePreview } from './trade-entry';
import type { OpeningPreview, OpeningRequest, OpeningResult } from './position-opening';
import type { AnteprimaMandato, StatoMandato, ValoriMandato } from './mandato';

export const API_BASE = (window as any).bellomberg?.apiUrl || 'http://127.0.0.1:8765';

const api = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
});

// ============================================================
// HARDENING #32 (audit 04 C1): sessione con token X-BB-Token.
// Il token viene emesso da POST /auth/login (campo nuovo "token") e salvato
// dal LoginGate; il backend lo richiede SOLO sugli endpoint mutanti (POST/DELETE).
// ============================================================
export const TOKEN_STORAGE_KEY = 'bellomberg_token_v1';
let memorySessionToken: string | null = null;

/** Only called after authentication. False means storage failed: disclose a session lasting until reload. */
export function saveSessionToken(token: unknown): boolean {
  if (typeof token !== 'string' || !token.trim()) throw new Error('Missing session token / Token di sessione assente');
  memorySessionToken = token;
  try {
    localStorage.setItem(TOKEN_STORAGE_KEY, token);
    return localStorage.getItem(TOKEN_STORAGE_KEY) === token;
  } catch { return false; }
}

export function getSessionToken(): string | null {
  if (memorySessionToken) return memorySessionToken;
  try { return localStorage.getItem(TOKEN_STORAGE_KEY); } catch { return null; }
}

/** Capture once at the start of a request, including streamed responses. */
export function requestHeaders(): Record<string, string> {
  const token = getSessionToken();
  return { 'X-BB-Language': linguaCorrente(), ...(token ? { 'X-BB-Token': token } : {}) };
}

export function clearSessionAndReload(): void {
  memorySessionToken = null;
  try {
    localStorage.removeItem(TOKEN_STORAGE_KEY);
    localStorage.removeItem('bellomberg_unlocked_v1'); // il LoginGate fara' il resto
  } catch {}
  window.location.reload();
}

// Allega X-BB-Token a ogni richiesta (innocuo sui GET, richiesto sui mutanti)
api.interceptors.request.use(cfg => {
  if (!cfg.headers) (cfg as any).headers = {};
  (cfg.headers as any)['X-BB-Language'] = linguaCorrente();
  const t = getSessionToken();
  if (t) {
    if (!cfg.headers) (cfg as any).headers = {};
    (cfg.headers as any)['X-BB-Token'] = t;
  }
  return cfg;
});

// Su 401/403 (token assente/scaduto/invalidato da restart backend): pulisce la
// sessione e ricarica - il LoginGate ripresenta il PIN. Escluso /auth/login
// (il 401 "PIN errato" lo gestisce il form senza reload).
api.interceptors.response.use(
  r => r,
  err => {
    const status = err?.response?.status;
    const url = String(err?.config?.url || '');
    if ((status === 401 || status === 403) && !url.includes('/auth/login')) {
      clearSessionAndReload();
    }
    return Promise.reject(err);
  }
);

export interface Position {
  ticker: string;
  nome: string;
  quantita: number;
  prezzo_medio: number;
  prezzo_live: number | null;
  valuta: string;
  valore_mercato: number;
  pl_eur: number | null;
  pl_pct: number | null;
  peso_pct: number;
  tesi?: string;
  // P&L DAILY (backend 23/07): prev_close in VALUTA DI QUOTAZIONE + ts fonte;
  // GG% = live/prev-1; P&L gg EUR = qty*(live-prev)*fx_to_eur (GBX gia' dentro
  // il fx). Campi assenti (backend vecchio) o null = n.d. DICHIARATO, mai 0.
  prev_close?: number | null;
  prev_close_ts?: string | null;
  fx_to_eur?: number;
  // regola no-fallback 14/07: senza snapshot prezzo la riga vale il COSTO
  price_stale?: boolean;
  price_source?: string;
}

export interface PortfolioSnapshot {
  source: string;
  n_positions: number;
  positions: Position[];
  totale_valore_mercato_eur: number;
  totale_pl_eur: number;
  cash_disponibile_eur?: number;
  cash_source?: string | null;
  cash_source_note?: string | null;
  nav_total_eur?: number;
  timestamp: string;
  as_of?: string;
  stale_positions?: string[];
  fx_incomplete?: string[];
}

export interface DecisionNote {
  id: number;
  autore: 'PM' | 'AI';
  testo: string;
  timestamp: string;
}

export interface Decision {
  id: number;
  esecuzione?: {
    trade_ids: number[]; eur: number | null; pct: number | null;
    inferito: boolean; data: string | null;
    trades?: { id: number; data: string; quantita: number; prezzo: number; valuta: string;
      linked_decision_id?: number | null }[];
  } | null;
  memo_id: number | null;
  timestamp: string;
  action: string;
  ticker: string;
  eur_amount: number | null;
  timing: string;
  confidence: string;
  rationale?: string | null;
  status: string;
  pm_feedback: string | null;
  outcome_pct: number | null;
  outcome_eur?: number | null;
  outcome_notes?: string | null;
  closed_at?: string | null;
  // F10 v3: gruppo di visualizzazione calcolato dal backend (tutto resta nel DB)
  archived?: boolean;
  // F10-C: override manuale dell'archivio (1/0/null) — il backend lo applica gia'
  // ad 'archived', qui serve solo per mostrare "fissata dal PM" in pagina
  archive_override?: number | null;
  // F10 v3: thread note PM<->AI (solo righe RESEARCH)
  notes?: DecisionNote[];
  // F10 opzione A (PM 22/07): veto eterno — flag + motivo + date (revoca inclusa)
  veto?: number | null;
  veto_reason?: string | null;
  veto_at?: string | null;
  veto_revoked_at?: string | null;
}

// F17 Fundamentals (16/07): un Excel di valutazione (VAL_/DCF_) indicizzato dal backend.
// C1-v2: 'canonical' = modello unico vivo per ticker (senza timestamp nel nome);
// fair_value/price/upside/variant_view arrivano dall'ultima tesi in valuation_theses.
export interface ValuationDecision {
  method_id?: string | null;
  profile_id?: string | null;
  decision_status?: string | null;
  support_status?: string | null;
  requirements_status?: string | null;
  rationale?: string | null;
  missing_fields?: string[];
  evidence_ids?: string[];
}

export interface ValuationUsability {
  usable: boolean;
  reasons: string[];
  missing_fields: string[];
}

export interface ValuationAcquisitionTask {
  field?: string;
  status?: string;
  reason?: string;
  source?: string;
}

export interface ValuationMarketQuote {
  contract?: string;
  status?: string;
  status_at_read?: string;
  source_id?: string | null;
  source_status?: string | null;
  acquired_as_of?: string | null;
  information_cutoff?: string | null;
  exchange?: string | null;
  quote_source_name?: string | null;
  delayed_minutes?: number | null;
  currency?: string | null;
  price?: number | null;
  observed_at?: string | null;
  observed_local_date?: string | null;
  price_model?: number | null;
  price_model_as_of?: string | null;
  upside_bear_pct?: number | null;
  upside_base_pct?: number | null;
  upside_bull_pct?: number | null;
}

export interface ValuationModel {
  presentation?: {
    decision_display: { method_rationale: string | null; support_note: string | null; registry_version: string };
    requirements_display: {
      method_id: string; method_version: string; registry_version: string;
      fields?: { field: string; description: string; [key: string]: unknown }[];
      periods?: string[]; sources?: string[]; reconciliations?: string[]; scenarios?: string[];
      [key: string]: unknown;
    } | null;
  };
  file: string;
  dir: string;
  engine: string;
  ticker: string;
  matched: boolean;       // portfolio membership, independent from canonical identity
  identity_status?: 'canonical' | 'legacy_unverified';
  snapshot_id?: string | null;
  generation_id?: string | null;
  valuation_decision?: ValuationDecision | null;
  valuation_usability?: ValuationUsability;
  analytical_quality?: {status?: string; issues?: string[]} | null;
  acquisition_tasks?: ValuationAcquisitionTask[];
  canonical: boolean;
  generated_at: string | null;
  flagged: boolean;
  memo_id?: number | null;
  fair_value?: number | null;
  price_at_thesis?: number | null;
  price_model_as_of?: string | null;
  upside_pct?: number | null;
  upside_today_pct?: number | null;
  market_quote?: ValuationMarketQuote | null;
  thesis_date?: string | null;
  variant_view?: string | null;
  sanity_severity?: string | null;   // OK/WARN dal motore (audit/12 V0.4)
  sanity_headline?: string | null;   // il perche' del giudizio sanity
  // F17-B (PM 17/07): dettaglio dal sidecar VAL_X.payload.json (metodi/peer/IRR);
  // null = sidecar non ancora scritto (arriva alla prossima rigenerazione), dichiarato
  detail?: ValuationDetail | null;
}

export interface ValuationDetail {
  market_quote?: ValuationMarketQuote | null;
  valuation_date?: string | null;
  valuation_basis?: string | null;
  valuation_decision?: ValuationDecision | null;
  valuation_usability?: ValuationUsability;
  snapshot_id?: string | null;
  generation_id?: string | null;
  analytical_quality?: {status?: string; issues?: string[]} | null;
  acquisition_tasks?: ValuationAcquisitionTask[];
  engine?: string;
  method?: string;
  payload_currency?: string | null;
  fair_value_ri?: number | null;
  fair_value_ptbv?: number | null;
  fair_value_ddm?: number | null;
  fair_value_blend?: number | null;
  fair_value_bear?: number | null;
  fair_value_base?: number | null;
  fair_value_bull?: number | null;
  fair_value_weighted?: number | null;
  fair_value_comps_implied?: number | null;
  fair_value_final?: number | null;
  methods_delta?: number | null;
  methods_divergence?: number | null;
  blend_methods?: string[];
  holding_irr?: {
    irr?: number | null;
    irr_exit_peer?: number | null;
    exit_ptbv_just?: number | null;
    exit_ptbv_peer?: number | null;
    entry_pb?: number | null;
    years?: number;
    by_scenario?: Record<string, number | null>;
    exit_multiple?: number | null;
    exit_multiple_source?: string | null;
    gordon_check?: string | null;
    note?: string | null;
  } | null;
  peers_used?: string[];
  peer_note?: string | null;
  growth_source?: string | null;
  wacc_used?: number | null;
  sanity?: { severity?: string | null; headline?: string | null } | null;
  values_baked?: boolean;
  _timestamp?: string;
  // V7 motore RAB (F17 v2 opzione B, PM 22/07): metodi + scheda regolatoria dal sidecar
  fair_value_ev_rab?: number | null;
  fair_value_ddm_reg?: number | null;
  fair_value_peer?: number | null;
  peer_method?: string | null;
  rab_base?: number | null;            // mln, input analista (mai stimata)
  allowed_return_calc?: number | null; // REALE della delibera (frazione)
  allowed_return_nominal?: number | null; // solo riferimento
  service?: string | null;             // es. trasporto_gas
  convention?: string | null;          // real_pretax | cpih_real_vanilla | analyst_pretax
  anchor_stale?: boolean | null;
  rab_premium?: number | null;         // oggi NON nel payload rab (solo assumptions): n.d. dichiarato, P3 post-collaudo
  // V7 Lotto 3: aggregati SOTP (le righe per segmento vivono SOLO nel foglio Excel)
  fair_value_sotp?: number | null;
  sotp_delta_pct?: number | null;      // PUNTI percentuali (es. 421.9), non frazione
  sotp_ev_total?: number | null;       // mln, valuta di bilancio (non convertita, come il foglio)
  sotp_n_segments?: number | null;
  sotp_incomplete?: boolean | null;
  sotp_note?: string | null;
  sotp_warnings?: string[] | null;
  // V5 motore mNAV (F17 opzione B del mockup, PM 23/07): scheda veicolo dal sidecar
  profile_key?: string | null;              // dat_bitcoin | dat_hype | cef_nav
  price?: number | null;                    // prezzo usato dal modello (valuta del NAV)
  nav_per_share?: number | null;
  nav_per_share_dtl_addback?: number | null;
  mnav_equity?: number | null;
  mnav_ev?: number | null;
  mnav?: number | null;
  mnav_dtl_addback?: number | null;
  discount_to_nav_pct?: number | null;      // PUNTI percentuali (es. -33.9)
  nav_target?: number | null;               // target premio/sconto dell'analista (D2)
  fair_value_nav?: number | null;           // = NAV x target; assente senza target
  fv_note?: string | null;                  // il PERCHE' del FV n.d. (dichiarato)
  nav_vintage?: Record<string, string | number | null> | null;
  btc_nav_usd?: number | null;
  adjusted_nav_musd?: number | null;
  fd_shares_m?: number | null;
  hype_value_musd?: number | null;
  price_quote?: number | null;              // quotazione originale, es. in pence britannici
  price_quote_currency?: string | null;
  price_note?: string | null;
  warnings?: string[] | null;               // warnings del motore (tutti i motori, V5)
  // decisioni PM 23/07 (pacchetto): M7 e costo del rischio TTC in pagina
  margin_sanity?: string | null;            // ATTENZIONE M7 sul gross margin base (operating v3)
  cost_of_risk_ttc?: {
    avg_bps?: number | null;
    last_bps?: number | null;
    last_year?: number | string | null;
    years?: Array<number | string> | null;
    mixed?: boolean | null;
    sign_note?: string | null;
    source?: string | null;
  } | null;
}

export interface Memo {
  id: number;
  output_language?: 'it' | 'en' | null;
  timestamp: string;
  title: string;
  pdf_path: string;
  appendix_path: string;
  capo_tokens_in: number;
  capo_tokens_out: number;
  has_content?: boolean;
  pdf_available?: boolean;
  appendix_available?: boolean;
  // Campi che GET /memos consegna da sempre e che nessuno dichiarava, quindi
  // nessuno leggeva (audit 23, giacimento F9): il NAV al momento del memo, i
  // modelli DCF allegati (JSON di path, 56 file di cui 14 col flag _FLAGGED)
  // e le note. Misurati sul payload vero, non dedotti dallo schema.
  portfolio_nav_eur?: number | null;
  dcf_files?: string | null;
  notes?: string | null;
  // solo su GET /memos/{id}: la lista NON lo trasporta (bugfix 202-C)
  full_markdown?: string | null;
}

/** Un passo di memo trovato dalla ricerca semantica sui chunk embeddati.
 *  `distance` e' una distanza COSENO: piu' bassa = piu' vicina alla domanda.
 *  Non e' una percentuale di rilevanza e non va resa come tale. */
export interface MemoSearchHit {
  chunk_id: string;
  memo_id: number;
  content: string;
  distance: number;
}

export interface AgentInfo {
  id: string; name: string; role: string; color: string; model: string;
}

// Voce ponte 26/07 (changelog (47)): motori VERI del terminale, derivati dalle
// costanti dei moduli backend. agents[].model resta il modello CHAT (giusto per F3);
// il footer ENGINE legge committee_r1_r2. Import falliti = chiave *_error dichiarata.
export interface EnginesInfo {
  chat?: string; committee_r1_r2?: string; committee_r0?: string; capo?: string;
  red_team?: string; synthesizer?: string; news_classifier?: string;
  [k: string]: string | undefined;   // chiavi *_error dichiarate dal backend
}

export interface ToolLogEntry {
  specialist: string; round: number; tool: string; input: string; time: string;
}

// Costi LLM 15/07 — aggregato per agente (somma dei round) scritto dall'heartbeat.
// SEMANTICA v2 (sostituisce la v1): cost_eur e' la somma dei SOLI round con un costo
// noto, ed e' null SOLO se NESSUN round e' prezzabile. La v1 azzerava a null l'intero
// agente appena un round mancava: cancellava spesa REALE e gia' nota (principio 1).
// Un cost_eur null resta un buco DICHIARATO, non uno zero: mai collassarlo a 0 in UI.
//
// status = il PEGGIORE dei round dell'agente. Stringhe esatte dal backend:
//   'ok'                  -> token noti, modello a listino, costo calcolato
//   'api_error'           -> l'agente ha fallito la chiamata
//   'model_unknown'       -> modello non a listino -> costo NON calcolabile
//   'pricing_unavailable' -> listino non importabile -> costo NON calcolabile
//   'usage_unknown'       -> la risposta API non ha esposto usage -> token IGNOTI
// ATTENZIONE: status e cost_eur sono ORTOGONALI. Un 'api_error' con token != 0 ha
// un cost_eur REALE (spesa gia' bruciata prima di morire): la UI deve mostrare il
// numero MA marcare l'agente in errore. status !== 'ok' e' un buco anche col costo.
// Opzionale per backward compat con gli heartbeat delle run vecchie.
export type UsageStatus =
  | 'ok' | 'api_error' | 'model_unknown' | 'pricing_unavailable' | 'usage_unknown';

export interface UsageBySpecialist {
  in: number | null;
  out: number | null;
  cache_read: number | null;
  cache_write: number | null;
  cost_eur: number | null;
  duration_s: number | null;
  api_calls: number;
  status?: UsageStatus | string;
  // v2: true = cost_eur e' la somma dei soli round prezzabili mentre almeno un altro
  // round NON lo era -> la cifra e' un MINIMO, non il costo pieno dell'agente.
  // La UI deve dirlo (principio 2: mai un parziale spacciato per completo).
  // Assente sugli heartbeat pre-v2 -> nessuna rivendicazione, ne' in un senso ne' nell'altro.
  partial?: boolean;
  tokens_status?: 'completo' | 'parziale';
  tokens_missing?: string[];
}

// Totale run (v2). Stessa regola dell'aggregato per agente, ma sommando per ENTRY:
// - cost_eur        = somma di TUTTE le entry con un costo noto; null SOLO se nessuna
//   entry e' prezzabile (usage_log vuoto compreso: null, non 0.0).
// - partial         = almeno una entry senza costo -> la cifra e' un MINIMO.
// - unpriced_agents = agenti con almeno un round non prezzabile -> cost_eur e' PARZIALE
// - error_agents    = agenti KO (api_error / usage_unknown). Lista SEPARATA:
//   "non so quanto e' costato" != "e' andato KO". Un agente puo' stare in entrambe.
// - error           = l'aggregazione backend e' esplosa: il payload arriva SENZA
//   le chiavi attese e la UI non deve rivendicare nulla (ne' FX, ne' totali).
// Invariante attesa: cost_eur == somma degli usage_by_specialist[*].cost_eur non-null,
// e lo stesso numero deve comparire nel log della run (un solo punto di verita').
// Tutte opzionali: gli heartbeat delle run vecchie non le portano.
export interface UsageTotal {
  in?: number | null;
  out?: number | null;
  cache_read?: number | null;
  cache_write?: number | null;
  cost_eur?: number | null;
  partial?: boolean;
  tokens_status?: 'completo' | 'parziale';
  tokens_missing?: string[];
  fx_source?: 'live' | 'fallback' | 'n.d.' | null;
  unpriced_agents?: string[];
  error_agents?: string[];
  error?: string;
}

export interface AgentsLiveState {
  language?: 'it' | 'en' | null;
  running: boolean;
  start_time?: string;
  current_round?: number;
  current_specialist?: string | null;
  specialist_status?: Record<string, "running" | "done" | "idle" | "error">;
  tool_log?: ToolLogEntry[];
  reports_by_specialist?: Record<string, Record<string, string>>;
  memo_id?: number;
  // ripipeline 15/07: totale report atteso della run (R0+R1 tutti, R2 selettivo)
  expected_reports?: number | null;
  // ripipeline 15/07: id dei desk che replicano in R2 (contatore ROUNDS per-agente: 3 vs 2)
  r2_specialists?: string[];
  updated_at?: string;
  message?: string;
  completed_at?: string;
  n_tool_calls?: number;
  // ponte 27/08 (changelog backend (90)): nell'heartbeat VIVO `tool_log` sono le
  // ultime 50 righe e `tool_log_tappato` lo dichiara; `heartbeat: "illeggibile"`
  // distingue il file letto a meta' da «nessuna run» (chiave additiva)
  tool_log_tappato?: boolean;
  heartbeat?: string;
  // Hardening #32 (watchdog): calcolati dal backend su GET /agents/live
  stale_seconds?: number;
  stale_warning?: boolean;
  // Costi LLM 15/07: assenti negli heartbeat delle run vecchie -> opzionali,
  // la UI degrada nascondendo la metrica (mai NaN, mai 0 finto).
  usage_by_specialist?: Record<string, UsageBySpecialist>;
  usage_total?: UsageTotal;
}

export interface RiskAlert { level: 'high'|'med'|'low'; metric: string; message: string; }
export interface AssetRisk { vol_annual_pct: number; sharpe: number; var_95_1d_pct: number; max_dd_pct: number; weight_pct: number; }
export interface PortfolioRisk {
  timestamp: string;
  nav_eur: number;
  portfolio: {
    vol_annual_pct: number; sharpe: number;
    var_95_1d_pct: number; var_99_1d_pct: number;
    var_95_1d_eur: number; var_99_1d_eur: number;
    beta_vs_spy: number; max_dd_1y_pct: number;
  };
  per_asset: Record<string, AssetRisk>;
  correlation: { tickers: string[]; matrix: number[][] };
  alerts: RiskAlert[];
  n_assets_analyzed: number;
  skipped_tickers: string[];
  lookback_days: number;
  error?: string;
}

// Dichiarazione fonti mute (voce (25) backend, regola no-fallback-silenziosi):
// fonti_mute = {provider: motivo SKIP_BUDGET|SKIP_DISABLED}, avviso = testo per il PM.
// Campi OPZIONALI: oggi il backend li espone su /news/ticker|search|portfolio;
// sugli endpoint consumati da NewsPage arrivano col coordinamento richiesto in (F5).
// undefined = endpoint che NON dichiara (≠ null/assente = dichiarato "nessun muto").
export interface FontiMuteFields {
  fonti_mute?: Record<string, string> | null;
  avviso?: string | null;
}

// Freschezza del feed (ponte (68) backend, 12/08): `ultimo_giro` su GET
// /news/providers — stato ∈ ok|degradato|n.d.|illeggibile; su ok/degradato
// arrivano timestamp/età/contatori/bloccati, su n.d./illeggibile il motivo.
// La chiave MANCA su un backend più vecchio del contratto: caso da DICHIARARE
// in pagina, mai da dedurre (regola 14/07).
export interface UltimoGiro {
  stato: 'ok' | 'degradato' | 'n.d.' | 'illeggibile' | string;
  timestamp?: string | null;
  age_minutes?: number | null;
  fetched?: number;
  classified?: number;
  saved?: number;
  skipped_duplicates?: number;
  providers_blocked?: Record<string, string> | null;
  motivo?: string | null;
}

export interface NewsItem {
  summary_status?: 'available' | 'legacy' | 'unavailable' | 'invalid';
  summary_language?: 'it' | 'en' | null;
  summary_note?: string | null;
  id: number;
  title: string;
  snippet: string | null;
  source: string | null;
  url: string | null;
  published_at: string | null;
  pulled_at: string;
  ticker_mentioned: string | null;
  theme: string | null;
  provider: string | null;
  sentiment: string | null;
  sentiment_score: number | null;
  relevance: number | null;
}

export interface MacroNewsItem {
  title: string;
  snippet: string | null;
  url: string | null;
  provider: string | null;
  source?: string | null;
  published_at: string | null;
  topic_id?: string;
  topic_label?: string;
  topic_category?: string;
  topic_importance?: number;
  tickers_affected?: string[];
  // Reddit-specific
  score?: number;
  num_comments?: number;
  flair?: string;
}

export interface CorporateEvent {
  title_origin?: string;
  snippet_origin?: string;
  presentation_languages?: string[];
  title: string;
  snippet: string | null;
  url: string | null;
  provider: string | null;
  published_at: string | null;
  ticker_mentioned: string | null;
  event_type?: string;
  source_type?: string;
  topic_importance?: number;
  metadata?: Record<string, any>;
}

export interface GlobalNewsItem {
  title: string;
  snippet: string | null;
  url: string | null;
  provider: string | null;
  source?: string | null;
  published_at: string | null;
}

export interface BriefingData {
  error_code?: string | null;
  language?: 'it' | 'en' | null;
  period?: string | null;
  slot_label?: string;
  generated_at?: string | null;
  lookback_hours?: number;
  news_count?: number;
  macro_indicators?: Record<string, { price: number | null; change_pct: number | null }>;
  portfolio_top?: Array<{ ticker: string; weight_pct: number; pnl_pct: number }>;
  briefing_md: string;
  tokens_in?: number;
  tokens_out?: number;
  stale?: boolean;
  age_minutes?: number;
  error?: string;
}

export interface EconomicEvent {
  date: string;
  time: string;
  type: string;
  title: string;
  importance: number;
  country: string;
  previous?: number | string | null;
  estimate?: number | string | null;
  actual?: number | string | null;
  unit?: string;
}

export interface NewsTopicMeta {
  id: string;
  label: string;
  category: string;
  query: string;
  importance: number;
  tickers_affected?: string[];
}

export interface MonteCarloResult {
  timestamp: string;
  version?: string;
  method: string;
  method_description?: string;
  drift_mode?: string;
  stress_scenario?: string;
  stress_requested?: string;
  stress_fallback?: boolean;
  stress_meta?: {
    requested?: string;
    applied?: string;
    fallback?: boolean;
    fallback_reason?: string;
    shock_note?: string;
    window?: { start: string; end: string; trading_days: number };
    real_history?: string[];
    proxied?: Record<string, string>;
    replaced_days?: number;
    window_truncated?: string;
    window_loss_pct?: number;
    window_loss_eur?: number;
    basis?: string;
  };
  calibration_note?: string | null;
  returns_basis?: string;
  lookback_years?: number;
  lookback_days_calibration: number;
  n_sims: number;
  horizon_days: number;
  horizon_years: number;
  n_assets: number;
  tickers_analyzed: string[];
  removed_tickers: string[];
  added_tickers: string[];
  weights: Record<string, number>;
  base_nav_eur: number;
  percentiles_ratio: Record<string, number>;
  percentiles_eur: Record<string, number>;
  expected_return_pct: number;
  median_return_pct: number;
  stdev_pct: number;
  sharpe_simulated: number;
  prob_negative_pct: number;
  prob_loss_10pct: number;
  prob_loss_20pct: number;
  prob_gain_10pct: number;
  prob_gain_20pct: number;
  var_95_pct?: number;
  var_99_pct?: number;
  var_99_cornish_fisher_pct?: number;
  es_95_pct?: number;
  es_99_pct?: number;
  es_95_eur?: number;
  es_99_eur?: number;
  max_drawdown_p5_pct: number;
  max_drawdown_median_pct: number;
  max_drawdown_p95_pct: number;
  sample_paths: number[][];
  // Giorno di ciascun punto di sample_paths (decimazione esplicita lato backend):
  // la UI NON re-indovina lo step. Assente sui payload vecchi -> si dichiara.
  sample_paths_days?: number[];
  // Quante traiettorie sono state SPEDITE (voce ponte (51), 26/07): i nostri due
  // endpoint ne mandano 200 su n_sims simulate, il default del motore resta 10 per
  // non gonfiare il contesto degli agenti ne' il fan chart del memo. Assente sui
  // payload pre-riavvio. NB: F5 conta comunque su `sample_paths.length` — quello
  // DISEGNATO e' l'unico numero che non puo' mentire; questo campo resta il contratto.
  sample_paths_n?: number;
  // Cono giorno-per-giorno (#178/#143): 7 percentili VERI dei path, gia' in EUR.
  // Consegnati dal backend da luglio ma MAI consumati dal frontend fino al vestito
  // v3 di F5 (26/07). Il p5/p50/p95 dell'ULTIMO punto coincide per costruzione con
  // percentiles_eur (fix di coerenza 15/07): se un giorno divergessero, e' un bug
  // del backend e la UI non deve mediarli.
  fan_bands?: {
    days: number[];
    p5: number[]; p10: number[]; p25: number[]; p50: number[];
    p75: number[]; p90: number[]; p95: number[];
  };
  // Distribuzione dei NAV a scadenza (#143): istogramma VERO delle simulazioni,
  // non ricampionato. edges_eur ha SEMPRE un elemento in piu' di counts.
  terminal_hist?: { counts: number[]; edges_eur: number[] };
  error?: string;
  weights_pre?: Record<string, number>;
  weights_post?: Record<string, number>;
  nav_pre_eur?: number;
  nav_post_eur?: number;
  modifications_applied?: any[];
  skipped_modifications?: { ticker: string; reason: string }[];
}

export interface FavCompany { ticker: string; name?: string; sector?: string; industry?: string; note?: string; added_at?: string; }
export interface MktSearchHit { symbol: string; name: string; exchange: string; type: string; }
export interface MktQuote { ticker: string; name: string; exchange?: string; currency?: string; price?: number; prev_close?: number;
  market_cap?: number; pe?: number; fwd_pe?: number; eps?: number; div_yield?: number; beta?: number;
  high_52w?: number; low_52w?: number; volume?: number; avg_volume?: number; sector?: string; industry?: string;
  target_mean?: number; recommendation?: string; short_pct_float?: number; summary?: string;
  ev?: number; ev_ebitda?: number; ev_sales?: number; peg?: number; pb?: number; fcf?: number; }
export interface MktNewsItem { title: string; link?: string; publisher?: string; published?: string | number; }
export interface FinBlock { years: (number | string)[]; rows: Record<string, (number | null)[]>; }
export interface MktFinancials { ticker: string; statements?: { income: FinBlock; balance: FinBlock; cashflow: FinBlock }; error?: string; }
export interface MktHolders { ticker: string; major: { label: string; value: number | string | null }[]; institutional: Record<string, any>[]; }
export interface MktOverviewRow { ticker: string; name: string; price?: number | null; change_pct?: number | null; }
export interface MktOverview { country: string; countries: string[]; indici: MktOverviewRow[]; azioni: MktOverviewRow[];
  commodities: MktOverviewRow[]; valute: MktOverviewRow[]; obbligazioni: MktOverviewRow[]; futures: MktOverviewRow[]; }
export interface OhlcBar { t: number; o: number; h: number; l: number; c: number; v: number; }
export interface OhlcResponse { ticker: string; period: string; interval: string; bars: OhlcBar[]; error?: string; }

export interface NavHistory {
  dates: string[];
  nav_eur: number[];
  cost_basis_eur: number[];
  pnl_eur: number[];
  dividend_income_eur?: number[];
  realized_sales_eur?: number[];
  total_return_eur?: number[];
  cash_eur: number;
  nav_total_eur: number[];
  first_trade_date: string;
  tickers: string[];
  n_days: number;
  final_nav_eur?: number;
  final_cost_basis_eur?: number;
  final_pnl_eur?: number;
  final_pnl_pct?: number;
  final_dividend_income_eur?: number;
  final_realized_sales_eur?: number;
  final_total_return_eur?: number;
  final_total_return_pct?: number;
  irr_annual_pct?: number | null;
  error?: string;
}

// Quant fase 1b (36): contribution attribution — contratto di portfolio_attribution.py
// (200 con `error` dichiarato per input non validi; 500 solo su eccezioni vere)
export interface AttributionPosition {
  ticker: string;
  contribution_pct: number;
  local_pct: number;
  fx_pct: number;
  cross_pct: number;
  avg_weight_pct: number;
  currency: string;
}
export interface AttributionPayload {
  period?: { label: string; base_day: string; end: string; n_trading_days: number };
  portfolio_return_pct?: number;
  by_position?: AttributionPosition[];
  by_bucket?: { bucket: string; contribution_pct: number; tickers: string[] }[];
  by_currency?: { currency: string; contribution_pct: number; fx_contribution_pct: number; tickers: string[] }[];
  totals?: { local_pct: number; fx_pct: number; cross_pct: number };
  excluded?: { ticker: string; reasons: Record<string, number>; reason_details?: { reason: string; label: string; days: number }[]; days_excluded: number; days_total: number; partial: boolean }[];
  reconciliation?: { recon_return_pct?: number; official_twr_pct?: number; delta_pp?: number; note?: string; error?: string };
  notes?: string[];
  basis?: string;
  error?: string;
}

// fix #30: payload del motore contabile TWR (GET /portfolio/analytics/twr)
export interface TwrReconciliation {
  nav_live_eur: number;
  last_snapshot_date: string;
  last_snapshot_nav_eur: number;
  last_snapshot_created_at?: string | null;
  delta_pct: number | null;
  note: string;
  // backend 23/07: stato d'allarme CALCOLATO dal backend (soglia sua, oggi 1%)
  // — la UI legge questi, niente piu' soglia-colore hardcoded in pagina
  breach?: boolean;
  tolerance_pct?: number;
}

export interface TwrMetrics {
  twr_total_pct: number;
  twr_annualized_pct: number | null;
  max_drawdown_pct: number;
  current_drawdown_pct: number;
  vol_annual_pct: number | null;
  sharpe: number | null;
  risk_free_used: number;
  irr_annual_pct: number | null;
  irr_basis: string;
}

export interface TwrPayload {
  copertura?: {
    primo_trade: string | null; primo_snapshot: string | null; ultimo_snapshot: string | null;
    official_since: string | null; n_trade_prima_del_primo_snapshot: number | null;
    giorni_senza_snapshot: number | null; nota: string | null;
  };
  as_of?: { computed_at: string; price_basis: string; fx_basis: string };
  dates: string[];
  twr_index: number[];
  regimes: ('reconstructed' | 'official')[];
  values_eur: number[];
  flows_eur: number[];
  regime_summary?: {
    official_since: string | null;
    n_official_days: number;
    n_reconstructed_days: number;
    seamless_transition: boolean;
  };
  metrics?: TwrMetrics;
  external_flows?: { date: string; type: string; amount_eur: number; note?: string | null }[];
  reconciliation?: TwrReconciliation | null;
  notes?: string[];
  methodology?: string;
  n_days?: number;
  timestamp?: string;
  error?: string;
}

export interface AdvancedMetrics {
  error?: string;
  n_obs?: number;
  cagr_pct?: number | null;
  vol_annual_pct?: number | null;
  sharpe?: number | null;
  sortino?: number | null;
  calmar?: number | null;
  max_drawdown_pct?: number | null;
  benchmark?: {
    beta?: number | null;
    alpha_annual_pct?: number | null;
    correlation?: number | null;
    information_ratio?: number | null;
    treynor_ratio?: number | null;
  } | null;
  benchmark_ticker?: string;
  benchmark_alignment?: string;
  risk_free_used?: number;
  _source?: string;
  [k: string]: any;
}

/* Serie benchmark UFFICIALE backend (voce (38)): total-return EUR sul
   calendario TWR, carry-forward CONTATO per data, testa senza dato ESCLUSA
   (leading_dropped), rebase 100 sul primo giorno comune col TWR.
   `error` valorizzato = serie n.d., gestire come il 404-safe di casa. */
export interface BenchmarkPayload {
  ticker?: string;
  currency?: string;            // "EUR"
  quote_currency?: string;
  total_return?: boolean;       // true = dividendi inclusi (coerente col book TWR)
  aligned_to?: string;
  base_date?: string;
  dates?: string[];
  close_eur?: number[];
  index?: number[];             // base 100 sul primo giorno comune col TWR
  ret_daily?: (number | null)[];
  n?: number;
  native_days?: number;
  carried_days?: number;
  carried_flags?: boolean[];    // true = carry (benchmark fermo quel giorno)
  leading_dropped?: number;
  coverage_pct?: number;
  src?: string;
  error?: string;
}

export interface DrawdownEpisode {
  peak_date: string;
  peak_nav: number;
  trough_date: string;
  trough_nav: number;
  recovery_date?: string;
  depth_pct: number;
  duration_to_trough_days: number;
  recovery_days?: number;
  total_days?: number;
  recovered: boolean;
}

export interface CurrentDrawdown {
  peak_date: string;
  peak_nav: number;
  trough_date: string;
  trough_nav: number;
  current_date: string;
  current_nav: number;
  depth_from_peak_pct: number;
  max_depth_pct: number;
  days_since_peak: number;
  days_in_dd: number;
  recovered: false;
}

export interface DrawdownsResult {
  n_episodes_total: number;
  top_5_drawdowns: DrawdownEpisode[];
  current_drawdown: CurrentDrawdown | null;
  max_drawdown_pct: number;
  avg_drawdown_pct: number;
  pain_index: number;
  n_days_analyzed: number;
  first_date: string;
  last_date: string;
  error?: string;
}

export interface LiquidityItem {
  ticker: string;
  position_eur?: number;
  avg_daily_volume_shares?: number;
  avg_daily_volume_eur?: number;
  days_to_liquidate: number | null;
  score: 'green' | 'yellow' | 'red' | 'skip' | 'unknown' | 'error';
  reason?: string;
}

export interface LiquidityResult {
  items: LiquidityItem[];
  n_green: number;
  n_yellow: number;
  n_red: number;
  threshold_green_days: number;
  threshold_yellow_days: number;
  assumption_pct_of_volume: number;
  note: string;
  error?: string;
}

export interface ConcentrationResult {
  by_ticker: {
    hhi: number;
    classification: 'diversified' | 'moderate' | 'concentrated';
    effective_n: number;
    top_5_pct: number;
    top_holdings: { ticker: string; weight_pct: number }[];
  };
  by_region: {
    hhi: number;
    classification: string;
    weights_pct: Record<string, number>;
  };
  by_currency: {
    hhi: number;
    classification: string;
    weights_pct: Record<string, number>;
  };
  interpretation: Record<string, string>;
  error?: string;
}

export interface VarContributionItem {
  ticker: string;
  weight_pct: number;
  component_var_pct: number;
  component_var_eur: number;
  contribution_pct_of_total_var: number;
  marginal_var_pct_per_1pct_weight: number;
}

export interface VarContributionResult {
  portfolio_var_pct_daily: number;
  portfolio_var_eur_daily: number;
  portfolio_vol_annual_pct: number;
  confidence_level: number;
  z_alpha: number;
  lookback_days: number;
  n_assets: number;
  items: VarContributionItem[];
  methodology: string;
  error?: string;
}

export interface TickerValidation {
  ok: boolean;
  symbol: string;
  name?: string | null;
  currency?: string | null;
  last_price?: number | null;
  exchange?: string | null;
  error?: string | null;
  timestamp?: string;
}

export interface PortfolioModification {
  action: 'add' | 'remove' | 'trim';
  ticker: string;
  amount_eur?: number | null;
  amount_pct?: number | null;
}

export interface ChatSession {
  output_language?: 'it' | 'en' | null;
  id: number; specialist: string; title: string;
  started_at: string; last_activity: string; msg_count?: number;
}
export interface MandatoMeta {
  impronta: string;
  dichiarato_il: string | null;
  origine: string;
}
export interface ChatMessage {
  output_language?: 'it' | 'en' | null;
  id: number; role: 'user'|'assistant'|'system'; content: string; timestamp: string;
  /** n.5 del lotto backend (58): misure dal DB, valorizzate sulle righe
   *  assistant. ⚠ `tokens_in` NON è l'input totale: è il resto non cachato
   *  (caveat `tokens_semantica` nel payload di sessione). null = non noti. */
  tokens_in?: number | null;
  tokens_out?: number | null;
}
export interface ChatSessionDetail extends ChatSession { messages: ChatMessage[]; }

/** Una riga del registro `cash_movements`, come la serve `GET /cash/movements`
 *  (backend changelog (70), bellomberg_api.py:1830). Tipizzata e non `any`
 *  per la stessa ragione di `logTrade`: e' il payload su cui F7 e F14
 *  affermano dei numeri, e un rename lato backend deve rompere il compilatore
 *  invece di passare zitto. */
export interface MovimentoCassa {
  id: number;
  /** giorno ISO YYYY-MM-DD: il backend normalizza al giorno prima dell'INSERT
   *  (memory_db.py:1087), quindi qui non arrivano mai orari */
  date: string;
  type: 'DEPOSIT' | 'WITHDRAWAL';
  amount_eur: number;
  note: string | null;
  created_at: string;
}

/** La risposta di `POST /cash/movement`.
 *  ⚠ `cash_disponibile_eur` e' `null` ESATTAMENTE nei casi in cui `cash_note`
 *  e' valorizzata: movimento a registro, cassa NON aggiornata
 *  (bellomberg_api.py:1806-1826). I due campi si leggono insieme o non si
 *  legge niente. */
export interface RispostaMovimentoCassa {
  ok?: boolean;
  movement_id?: number | string | null;
  tipo?: string;
  importo_eur?: number;
  cash_disponibile_eur?: number | null;
  cash_note?: string | null;
}

export const Bellomberg = {
  health: () => api.get('/health').then(r => r.data),
  preferences: () => api.get('/preferences').then(r => r.data),
  savePreferences: (body: SalvaPreferenza) => api.put('/preferences', body).then(r => r.data),
  authLogin: (pin: string) =>
    api.post<{ ok: boolean; user: string; brand: string; token?: string; expires_in_s?: number }>('/auth/login', { pin }).then(r => r.data),
  authStatus: () =>
    api.get<{ default_pin: boolean; configured: boolean }>('/auth/status').then(r => r.data),
  portfolio: () => api.get<PortfolioSnapshot>('/portfolio').then(r => r.data),
  fx: () => api.get<{rates: Record<string, number>}>('/fx').then(r => r.data),
  memos: (limit = 20) => api.get<{memos: Memo[]}>(`/memos?limit=${limit}`).then(r => r.data),
  valuationModels: () => api.get<{count: number; models: ValuationModel[]; nota?: string}>('/fundamentals/models').then(r => r.data),
  memoById: (id: number) => api.get<Memo>(`/memos/${id}`).then(r => r.data),
  // include_empty=true fa vedere anche le run FALLITE (markdown < 100 char):
  // in DB sono 46 righe e la lista di default ne mostra 18. F9 le conta per
  // dichiararle, perche' un archivio che nasconde i propri fallimenti mente
  // per omissione — non le mette in elenco, ma scrive quante sono.
  memosAll: (limit = 200) =>
    api.get<{memos: Memo[]}>(`/memos?limit=${limit}&include_empty=true`).then(r => r.data),
  // ricerca semantica sui chunk gia' embeddati (collection memo_chunks).
  // L'endpoint esisteva da sempre e non lo chiamava nessuno.
  memosSearch: (q: string, n = 8) =>
    api.get<{query: string; results: MemoSearchHit[]}>(
      `/memos/search/${encodeURIComponent(q)}?n=${n}`).then(r => r.data),
  decisions: (status?: string, limit = 30) => {
    const params: Record<string, string|number> = { limit };
    if (status) params.status = status;
    return api.get<{decisions: Decision[]}>('/decisions', { params }).then(r => r.data);
  },
  updateDecision: (id: number, body: any) =>
    api.post(`/decisions/${id}/update`, body).then(r => r.data),
  // F10 opzione A: veto eterno (motivo obbligatorio) + revoca
  vetoDecision: (id: number, reason: string) =>
    api.post(`/decisions/${id}/veto`, { reason }).then(r => r.data),
  revokeDecisionVeto: (id: number) =>
    api.post(`/decisions/${id}/veto/revoke`, {}).then(r => r.data),
  // F10 v3: nota del PM sul filo di una decisione RESEARCH (la run risponde)
  addDecisionNote: (id: number, testo: string) =>
    api.post(`/decisions/${id}/note`, { testo }).then(r => r.data),
  // F10-C (PM 17/07): archivia / riporta in pagina (true/false; null = automatico)
  setDecisionArchive: (id: number, archived: boolean | null) =>
    api.post(`/decisions/${id}/archive`, { archived }).then(r => r.data),
  // La risposta porta `cash_note`: e' il campo con cui il backend dichiara di
  // NON essere riuscito ad aggiornare la cassa dopo aver scritto il trade
  // (bellomberg_api.py:1654-1682). Tipizzato apposta: finche' era `any`, un
  // rename lato backend sarebbe passato senza che il compilatore fiatasse, e
  // il difetto che F7 esiste per chiudere sarebbe tornato in silenzio.
  previewTrade: (body: TradeRequest) =>
    api.post<TradePreview>('/trade/preview', body).then(r => r.data),
  openingPositions: () => api.get('/positions/opening').then(r => r.data),
  openingPosition: (ticker: string) => api.get(`/positions/opening/${encodeURIComponent(ticker)}`).then(r => r.data),
  previewOpeningPosition: (body: OpeningRequest) =>
    api.post<OpeningPreview>('/positions/opening/preview', body).then(r => r.data),
  createOpeningPosition: (body: OpeningRequest) =>
    api.post<OpeningResult>('/positions/opening', body).then(r => r.data),
  logTrade: (body: TradeRequest) => api.post<TradeResult>('/trade', body).then(r => r.data),
  trades: (limit = 60) => api.get('/trades', { params: { limit } }).then(r => r.data),
  // ── IL CANALE CASSA (backend changelog (70)+(71)) ────────────────────
  // Versare e prelevare «come se fosse un trade» (voce PM 12/08).
  // ⚠ `conferma` e' UNA chiave per DUE guardie: la manda chi ha appena letto
  // il 422 di UNA di esse, ma spegne anche l'altra (memory_db.py:958 e :964).
  // Chi la valorizza deve dirlo a schermo — la resa sta in TradeEntryPage.
  logCashMovement: (body: {
    tipo: 'DEPOSIT' | 'WITHDRAWAL';
    importo_eur: number;
    /** YYYY-MM-DD; assente = oggi lato backend */
    data?: string;
    nota?: string;
    conferma?: boolean;
  }) => api.post<RispostaMovimentoCassa>('/cash/movement', body).then(r => r.data),
  /** ⚠ il backend clampa `limit` a 1..1000 (memory_db.py:1119): chiedere di
   *  piu' non porta di piu', e chiederne esattamente `limit` significa che
   *  quella che vedi e' una finestra, non lo storico. */
  cashMovements: (limit = 100) =>
    api.get<{ count: number; movements: MovimentoCassa[] }>(
      '/cash/movements', { params: { limit } }).then(r => r.data),
  addFeedback: (body: any) => api.post('/feedback', body).then(r => r.data),
  macro: () => api.get('/macro').then(r => r.data),
  scheduledTasks: () => api.get<{tasks: any[]}>('/tasks/scheduled').then(r => r.data),
  updatePrices: () => api.post('/prices/update').then(r => r.data),
  portfolioRisk: (force = false) =>
    api.get<PortfolioRisk>('/portfolio/risk', { params: force ? { force: true } : {}, timeout: 60000 }).then(r => r.data),
  portfolioFactors: (force = false) =>
    api.get<any>('/portfolio/factors', { params: force ? { force: true } : {}, timeout: 90000 }).then(r => r.data),
  // F6 (F23): la finestra si sceglie. ⚠ Il motore fattoriale tiene UNA SOLA
  // finestra in cache: alternare 1y e 3y costa 6,4-7,1 s di 27 regressioni
  // (misurato 27/07). Vedi ORDINE_CHIAMATE in lib/fattori.ts.
  portfolioFactorsPeriodo: (period: '1y' | '3y', force = false) =>
    api.get<any>('/portfolio/factors', {
      params: force ? { period, force: true } : { period }, timeout: 120000,
    }).then(r => r.data),
  // F6 (F23): il guardrail che riconcilia i beta del book da tre motori diversi.
  // ⚠ Chiede al modulo la finestra 3y, quindi SPOSTA la cache di cui sopra.
  betaReconcile: () =>
    api.get<{
      betas?: Record<string, number>;
      definitions?: Record<string, string>;
      sources_failed?: Record<string, string>;
      threshold?: number; max_spread?: number;
      verdict?: string; beta_consensus?: number;
    }>('/portfolio/metrics/beta_reconcile', { timeout: 120000 }).then(r => r.data),
  portfolioMonteCarlo: (params: {
    horizon_days?: number; n_sims?: number; lookback_years?: number;
    method?: 'parametric_t'|'fhs'|'block_bootstrap';
    drift_mode?: 'zero'|'shrinkage'|'historical';
    stress?: 'none'|'gfc_2008'|'covid_2020'|'shock_3sigma';
    add?: string; remove?: string; force?: boolean;
  } = {}) =>
    api.get<MonteCarloResult>('/portfolio/montecarlo', { params, timeout: 120000 }).then(r => r.data),
  portfolioMonteCarloV3: (body: {
    horizon_days?: number; n_sims?: number; lookback_years?: number;
    method?: 'parametric_t'|'fhs'|'block_bootstrap';
    drift_mode?: 'zero'|'shrinkage'|'historical';
    stress?: 'none'|'gfc_2008'|'covid_2020'|'shock_3sigma';
    modifications?: PortfolioModification[];
    force?: boolean;
  }) =>
    api.post<MonteCarloResult>('/portfolio/montecarlo/v3', body, { timeout: 120000 }).then(r => r.data),
  validateTicker: (symbol: string) =>
    api.get<TickerValidation>('/portfolio/validate_ticker', { params: { symbol }, timeout: 15000 }).then(r => r.data),
  runConsigliere: () => api.post('/consigliere/run').then(r => r.data),
  mandato: () => api.get<StatoMandato>('/mandato').then(r => r.data),
  mandatoAnteprima: (valori?: ValoriMandato) => (valori
    ? api.post<AnteprimaMandato>('/mandato/anteprima', valori)
    : api.get<AnteprimaMandato>('/mandato/anteprima')).then(r => r.data),
  salvaMandato: (valori: ValoriMandato) => api.put<StatoMandato>('/mandato', valori).then(r => r.data),
  consigliereStatus: (id: string) => api.get(`/consigliere/status/${id}`).then(r => r.data),
  cancelConsigliere: (taskId: string) =>
    api.post<{ task_id: string; status: string; message: string }>(`/consigliere/cancel/${taskId}`).then(r => r.data),
  consigliereActive: () =>
    api.get<{ active: boolean; task_id?: string; started?: string; has_proc_handle?: boolean }>('/consigliere/active').then(r => r.data),
  cancelAllConsigliere: () =>
    api.post<{ killed: any[]; errors: any[]; n_killed: number }>('/consigliere/cancel_all').then(r => r.data),
  resetAgentsLive: () =>
    api.post<{ ok: boolean; message?: string; error?: string }>('/agents/live/reset').then(r => r.data),
  navHistory: (force = false) =>
    api.get<NavHistory>('/portfolio/analytics/nav_history', { params: force ? { force: true } : {}, timeout: 90000 }).then(r => r.data),
  twr: (force = false) =>
    api.get<TwrPayload>('/portfolio/analytics/twr', { params: force ? { force: true } : {}, timeout: 90000 }).then(r => r.data),
  metricsAdvanced: (benchmark = 'SPY') =>
    api.get<AdvancedMetrics>('/portfolio/metrics/advanced', { params: { benchmark }, timeout: 90000 }).then(r => r.data),
  // Serie benchmark UFFICIALE (voce (38)): total-return EUR sul calendario TWR.
  // Prima chiamata puo' scaricare candele dal provider (lenta), poi cache server 10min.
  benchmark: (ticker = 'SPY', force = false) =>
    api.get<BenchmarkPayload>('/portfolio/analytics/benchmark', { params: force ? { ticker, force: true } : { ticker }, timeout: 120000 }).then(r => r.data),
  // Quant fase 1b (36): contribution attribution (Carino) per posizione/bucket/valuta.
  // Prima chiamata puo' scaricare candele (lenta), poi cache server 10min day-aware.
  attribution: (period: 'MTD' | '30D' | 'YTD' | 'INCEPTION' = 'YTD') =>
    api.get<AttributionPayload>('/portfolio/attribution', { params: { period }, timeout: 120000 }).then(r => r.data),
  ohlc: (ticker: string, period = '1y', interval = '1d') =>
    api.get<OhlcResponse>('/market/ohlc', { params: { ticker, period, interval }, timeout: 60000 }).then(r => r.data),
  mktSearch: (q: string) =>
    api.get<{results: MktSearchHit[]}>('/market/search', { params: { q }, timeout: 15000 }).then(r => r.data),
  mktOverview: (country = 'US') =>
    api.get<MktOverview>('/market/overview', { params: { country }, timeout: 30000 }).then(r => r.data),
  mktQuote: (ticker: string) =>
    api.get<MktQuote>('/market/quote', { params: { ticker }, timeout: 30000 }).then(r => r.data),
  mktNews: (ticker: string) =>
    api.get<{ticker: string; items: MktNewsItem[]}>('/market/news', { params: { ticker }, timeout: 30000 }).then(r => r.data),
  favorites: () => api.get<{favorites: FavCompany[]}>('/favorites').then(r => r.data),
  favAdd: (f: FavCompany) => api.post('/favorites', null, { params: f }).then(r => r.data),
  favDel: (ticker: string) => api.delete(`/favorites/${ticker}`).then(r => r.data),
  favSetNote: (ticker: string, note: string) => api.post(`/favorites/${ticker}/note`, null, { params: { note } }).then(r => r.data),
  mktFinancials: (ticker: string) =>
    api.get<MktFinancials>('/market/financials', { params: { ticker }, timeout: 45000 }).then(r => r.data),
  mktHolders: (ticker: string) =>
    api.get<MktHolders>('/market/holders', { params: { ticker }, timeout: 45000 }).then(r => r.data),
  drawdowns: (force = false) =>
    api.get<DrawdownsResult>('/portfolio/analytics/drawdowns', { params: force ? { force: true } : {}, timeout: 90000 }).then(r => r.data),
  liquidity: () =>
    api.get<LiquidityResult>('/portfolio/analytics/liquidity', { timeout: 60000 }).then(r => r.data),
  concentration: () =>
    api.get<ConcentrationResult>('/portfolio/analytics/concentration').then(r => r.data),
  varContribution: (lookback_days = 252, confidence = 0.05) =>
    api.get<VarContributionResult>('/portfolio/analytics/var_contribution', { params: { lookback_days, confidence }, timeout: 60000 }).then(r => r.data),
  newsFeed: (params: { limit?: number; min_relevance?: number; ticker?: string; sentiment?: string } = {}) =>
    api.get<{ count: number; items: NewsItem[]; timestamp: string } & FontiMuteFields>('/news/feed', { params }).then(r => r.data),
  // lettura LEGGERA: il backend legge data/news_feed_status.json, zero provider
  newsProviders: () =>
    api.get<{ providers_contingentati?: string[]; ultimo_giro?: UltimoGiro | null;
              nota?: string; timestamp: string } & FontiMuteFields>('/news/providers').then(r => r.data),
  newsFeedRefresh: (days = 1, classify = true) =>
    api.post<{ fetched: number; classified: number; saved: number; skipped_duplicates: number } & FontiMuteFields>(
      '/news/feed/refresh', null, { params: { days, classify }, timeout: 180000 }
    ).then(r => r.data),
  // ===== NEW: Bloomberg-style news terminal =====
  newsMacro: (params: {
      categories?: string;
      min_importance?: number;
      days?: number;
      max_per_topic?: number;
      include_reddit?: boolean;
  } = {}) =>
    api.get<{ count: number; items: MacroNewsItem[]; categories_requested: string[] | null; timestamp: string } & FontiMuteFields>(
      '/news/macro', { params, timeout: 120000 }
    ).then(r => r.data),
  newsCorporateEvents: (days = 14, max_items = 30) =>
    api.get<{ count: number; items: CorporateEvent[]; timestamp: string } & FontiMuteFields>(
      '/news/corporate-events', { params: { days, max_items }, timeout: 120000 }
    ).then(r => r.data),
  newsTopGlobal: (limit = 15) =>
    api.get<{ count: number; items: GlobalNewsItem[]; timestamp: string } & FontiMuteFields>(
      '/news/top-global', { params: { limit } }
    ).then(r => r.data),
  briefingCurrent: () =>
    api.get<BriefingData>('/news/briefing/current').then(r => r.data),
  briefingRefresh: (period?: 'morning' | 'midday' | 'afternoon' | 'evening') =>
    api.post<BriefingData>('/news/briefing/refresh', null, {
      params: period ? { period } : {}, timeout: 90000,
    }).then(r => r.data),
  economicCalendar: (days_ahead = 7) =>
    api.get<{ count: number; items: EconomicEvent[]; timestamp: string }>(
      '/news/economic-calendar', { params: { days_ahead } }
    ).then(r => r.data),
  newsTopicsList: () =>
    api.get<{ categories: Record<string, string>; topics: NewsTopicMeta[] }>('/news/topics').then(r => r.data),
  // News alert system - news non ancora notificate con relevance alta
  newsAlertsUnnotified: (min_relevance = 8, limit = 10) =>
    api.get<{ count: number; items: Array<{
      id: number; title: string; snippet: string | null; url: string | null;
      provider: string | null; ticker: string | null; sentiment: string | null;
      relevance: number | null; published_at: string | null; pulled_at: string;
    }>}>('/news/alerts/unnotified', { params: { min_relevance, limit } }).then(r => r.data),
  newsAlertsMarkNotified: (ids: number[]) =>
    api.post<{ updated: number }>('/news/alerts/mark-notified', ids).then(r => r.data),
  // Database backups
  dbBackupCreate: () =>
    api.post<{ ok: boolean; backup_path: string; size_mb: number; files_count: number; files: string[]; db_quick_check: Record<string, string>; timestamp: string }>(
      '/db/backup', null, { timeout: 60000 }
    ).then(r => r.data),
  dbBackupsList: () =>
    api.get<{ count: number; backups: Array<{ filename: string; path: string; size_mb: number; created: string }> }>('/db/backups').then(r => r.data),
  dbBackupDelete: (filename: string) =>
    api.delete<{ ok: boolean; deleted: string }>(`/db/backups/${encodeURIComponent(filename)}`).then(r => r.data),
  agentsList: () => api.get<{agents: AgentInfo[]; engines?: EnginesInfo}>('/agents/list').then(r => r.data),
  agentsLive: () => api.get<AgentsLiveState>('/agents/live').then(r => r.data),
  chatListSessions: (agentId: string, limit = 30) =>
    api.get<{ sessions: ChatSession[] }>(`/chat/${agentId}/sessions`, { params: { limit } }).then(r => r.data),
  chatCreateSession: (agentId: string, title?: string) =>
    api.post<{ session_id: number; agent_id: string; agent_name: string; title: string }>(
      '/chat/sessions', { agent_id: agentId, title }
    ).then(r => r.data),
  chatGetSession: (sessionId: number) =>
    api.get<ChatSessionDetail>(`/chat/sessions/${sessionId}`).then(r => r.data),
  chatDeleteSession: (sessionId: number) =>
    api.delete(`/chat/sessions/${sessionId}`).then(r => r.data),
  chatStreamUrl: (sessionId: number) => `${API_BASE}/chat/sessions/${sessionId}/stream`,
};
