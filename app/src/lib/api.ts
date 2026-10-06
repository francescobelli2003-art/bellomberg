import axios from 'axios';
import { linguaCorrente } from '../i18n/lingua';
import type { SalvaPreferenza } from '../i18n/preferenze';
import type { TradeRequest, TradeResult, TradePreview } from './trade-entry';
import type { OpeningPreview, OpeningRequest, OpeningResult } from './position-opening';
import type { AnteprimaMandato, StatoMandato, ValoriMandato } from './mandato';
import type { NewsRefreshJob, NewsRefreshJobResponse, NewsRefreshResponse } from './news-refresh';

export const API_BASE = (window as any).bellomberg?.apiUrl || 'http://127.0.0.1:8765';

export interface FundResearchSource {
  id?: string; source?: string; url?: string | null; as_of?: string | null;
  published_at?: string | null; summary?: string; metadata?: { title?: string };
}
export interface FundResearchContent {
  summary?: string;
  dossier?: { key: string; title: string; paragraphs: string[]; tables?: {
    title: string; columns: string[]; rows: string[][]; source: string; unit: string; period: string;
  }[] }[];
  scenarios?: { name: string; analysis: string; evidence_ids?: string[] }[];
  risks?: string[]; catalysts?: string[]; invalidation?: string[]; review_conditions?: string[];
  data_gaps?: (string | { reason?: string })[];
  evidence?: FundResearchSource[]; documents?: FundResearchSource[]; report?: string;
}
export interface FundObservationRefresh {
  status?: string; last_attempt_at?: string | null; last_success_at?: string | null; error?: string | null;
}
export interface FundResearchCompany {
  ticker: string; name: string | null;
  quote: { value: number | null; currency: string | null; source: string | null; observed_at: string | null; status: string;
    acquired_at?: string | null; refresh?: FundObservationRefresh | null };
  consensus: { status: string; source: string | null; currency: string | null; currency_source?: string | null;
    acquired_at: string | null; data_as_of: string | null; mean: number | null; median: number | null;
    low: number | null; high: number | null; number_of_analysts: number | null;
    reason?: string | null; refresh?: FundObservationRefresh | null };
  comparison: { upside_pct: number | null; status: string };
  analysis: { status: string; origin: string | null; scope: string | null; as_of: string | null;
    run_id?: string | null; memo_id?: number | null; mode?: string | null; summary: string | null;
    judgment: string | null; technical_status?: string; round?: number;
    content: FundResearchContent | null; reference?: unknown };
}
export interface FundResearchList {
  status: string; count: number | null; items: FundResearchCompany[]; notices: string[];
  read_at?: string;
  market_refresh?: {status: string; last_completed_at?: string | null; last_result?: Record<string, unknown> | null;
    error?: string | null; next_retry_at?: string | null; notices?: string[]};
}
export interface FundArchiveItem {
  id: string; file: string; ticker: string | null; as_of: string | null; file_modified_at?: string;
  historical: boolean; available: boolean; reason?: string | null; integrity: string; download_path: string;
}
export interface FundArchiveList { items: FundArchiveItem[]; notices: string[] }

export interface WeeklyRecovery {
  memo_id: number; run_id?: string; status: string;
  analytical_status?: string; artifact_status?: string; delivery_status?: string;
  resume_available?: boolean; delivery_recovery_available?: boolean;
  blocked_reason?: string; reason?: string; remaining_work?: string[];
  first_error?: { phase?: string; desk?: string; message?: string; request_id?: string };
  request_costs?: { known_cost_usd?: number | null; cost_usd?: number | null; authorized_usd?: number | null;
    reserved_usd?: number | null; remaining_known_usd?: number | null; unknown_requests?: number };
}

export interface FilingCitation {
  sezione?: string; testo?: string; url?: string; sha256?: string;
  pagine_fisiche?: number[]; inizio?: number; fine?: number; src?: string;
}
export interface FilingDiff {
  stato: string; motivi?: string[]; limiti?: string[]; sezioni_confrontate?: string[];
  similarita_sezioni?: Record<string, { jaccard?: number | null; coseno?: number | null; metodo?: string }>;
  misure?: { segmenti_prima?: number; segmenti_dopo?: number; cambiamenti?: number };
  cambiamenti?: { tipo: string; prima?: FilingCitation; dopo?: FilingCitation }[];
  segmenti_non_confrontabili?: { prima?: FilingCitation; dopo?: FilingCitation }[];
  ambito?: string;
  /** Fase F: «sequenziale» = trimestre precedente (emittente SEC nuovo, manca l'anno prima). */
  regola?: 'sequenziale';
}
export interface FilingResult {
  controllo_leggero?: boolean; run_riferimento?: number;
  stato: string; motivi?: string[]; candidati?: { fonte?: string; url?: string; stato: string; motivi?: string[]; sha256?: string; metadati?: Record<string, string> }[];
  copertura?: { stato?: string; limiti?: string[]; candidati_osservati?: number; max_documenti?: number; documenti_tentati?: number };
  freschezza?: { stato: string; checked_at?: string; ultimo_periodo?: string; next_report_date?: string; next_report_source?: string; verificato_il?: string; motivi?: string[] };
  fonti?: { nome?: string; stato?: string; motivi?: string[] }[];
  coppia?: { ambito?: string; regola?: 'sequenziale'; prima?: { url?: string; sha256?: string; metadati?: Record<string, string> }; dopo?: { url?: string; sha256?: string; metadati?: Record<string, string> } } | null;
  confronto_corrente?: FilingDiff | null; confronto_storico?: FilingDiff | null;
  ultimo_non_verificato?: boolean;
  /** Numeri chiave della coppia (filing_numeri.variazioni); `variante` se vengono da un'altra variante. */
  numeri?: { stato: string; valuta?: string | null; voci?: { voce: string; prima: number; dopo: number; delta_pct: number | null }[];
    fonte?: string; variante?: string; motivo?: string;
    /** Fase F: numeri sul trimestre precedente, non sullo stesso periodo dell'anno prima. */
    confronto?: 'trimestre_precedente' } | null;
  /** Tipo della variante mostrata (annuale, semestrale, trimestrale, nove_mesi) e varianti del profilo. */
  variante?: string;
  varianti?: { tipo?: string; primaria?: boolean; regola?: 'sequenziale'; coppia_periodi?: (string | null)[]; completo?: boolean; motivi?: string[] }[];
}
export interface FilingRun {
  id: number; ticker: string; status: string; started_at?: string | null; finished_at?: string | null;
  trigger?: string; profile_version?: number | null; reason?: string | null;
  /** Controllo periodico leggero: nessun deposito nuovo, nessun download ne' confronto. */
  controllo_leggero?: boolean;
}
export interface FilingProfile {
  ticker: string; profile: Record<string, unknown>; version: number; enabled: boolean;
  interval_hours: number; qualitative_enabled: boolean; next_due_at?: string | null;
}
export interface FilingListing {
  ticker: string; status: string; reason?: string | null; profile: FilingProfile | null;
  runs: FilingRun[]; active_run: FilingRun | null;
  /** Ultimo run completo (anche oltre i 20 run elencati); status/reason lo descrivono. */
  ultimo_completo?: FilingRun | null;
  /** Ultimo controllo leggero, se e' il run piu' recente. */
  ultimo_controllo?: { at: string | null; esito: string | null } | null;
  /** Ultimo run in errore successivo al confronto mostrato (dichiarato a parte). */
  ultimo_errore?: { id: number; at: string | null; reason: string | null } | null;
}
export interface FilingCandidate { cik: string; ticker: string; nome: string; origine: 'ticker' | 'alias' | 'nome' | 'nome_simile' }
export interface FilingEsefCandidate { lei: string; nome: string; origine: 'negozio' | 'nome' | 'nome_simile' }
export interface FilingProposal {
  ticker: string; nome?: string | null; profilo_attivo: boolean; escluso: boolean;
  /** 'non_configurata': SEC_CONTACT_EMAIL assente, SEC mai interrogata (RUN-ANDREA, 05/10) */
  sec: { stato: 'univoco' | 'ambiguo' | 'nessuno' | 'errore' | 'non_configurata'; candidati: FilingCandidate[]; motivo: string };
  /** Presente solo quando SEC non e' univoco: emittente ESEF (filings.xbrl.org) per LEI. */
  esef?: { stato: 'univoco' | 'ambiguo' | 'nessuno' | 'errore'; candidati: FilingEsefCandidate[]; motivo: string };
  /** Fonte da proporre; assente sui backend precedenti (allora ESEF solo se SEC e' «nessuno»). */
  preferita?: 'sec' | 'esef' | null;
}
/** `GET /filings/{t}/ai-estimate`: misure dell'input e costo massimo, nessuna chiamata al modello. */
export interface FilingAiEstimate {
  stato: 'ok' | 'not_configured'; ticker: string; url: string; sha256: string; pagine: number;
  caratteri_input: number; righe: number; pagine_indice: number[]; troncato: number;
  lingua_rilevata?: string | null; cache: boolean; modello?: string | null; variabile?: string;
  token_input_stimati?: number; token_output_max?: number; costo_max_eur?: number | null;
  costo_max_usd?: number | null; tariffe_origine?: 'listino' | 'openrouter' | 'n.d.'; motivo?: string | null;
}
export interface FilingAiSezione { nome: string; inizio: string; fine: string; caratteri: number; pagine: number[]; anteprima: string }
/** `POST /filings/{t}/ai-proposal`: l'unica chiamata AI (pulsante); niente e' salvato prima dell'accettazione. */
export interface FilingAiProposal {
  /** in_corso/refused: contratto G2b 04/10 (lavoro sul server; rifiuto con `motivo`). */
  stato: 'done' | 'not_configured' | 'error' | 'in_corso' | 'refused'; sha256?: string; url?: string; tipo?: string; lingua?: string;
  job_id?: string | null; avviato_il?: string | null; secondi?: number | null; motivo?: string | null;
  /** «Riprova» costa una nuova chiamata? (assente = non dichiarato: si tratta come a pagamento) */
  riprova_paga?: boolean;
  /** rifiuto per intervallo minimo fra due proposte dello stesso titolo: secondi da attendere */
  riprova_tra_s?: number | null;
  periodo?: { inizio: string; fine: string } | null; verificate?: FilingAiSezione[];
  scartate?: { nome: string; motivo: string }[]; salvabile?: boolean; motivi?: string[];
  /** Regole di verifica di ripiego usate al posto di quelle proposte (dichiarate). */
  avvisi?: string[];
  /** Le stesse regole provate sugli altri PDF indicati (es. anno prima): solo verifica locale. */
  altri?: { url: string; stato: 'ok' | 'non_verificato'; periodo?: { inizio: string; fine: string } | null;
    sezioni_ok: string[]; sezioni_mancanti: Record<string, string>; motivi: string[] }[];
  modello?: string | null; costo_eur?: number | null; costo_usd?: number | null; cached?: boolean;
  dettaglio?: string; variabile?: string;
  /** Risposta pagata ma illeggibile: una nuova chiamata solo con «Riprova» (`riprova: true`). */
  riprovabile?: boolean;
}
/** `GET /filings/{t}/ai-proposal`: ultima proposta in cache, senza download ne' chiamata AI. */
export interface FilingAiProposalSalvata extends FilingAiProposal {
  at?: string | null; da_riverificare?: boolean; accettata?: boolean;
}
export interface FilingAiAccept {
  ticker: string; esito: 'salvato' | 'variante_aggiunta'; versione: number; sezioni: string[]; aggiornamento?: string;
}
export interface FilingActivation {
  ticker: string; esito: 'attivato' | 'da_confermare' | 'senza_fonte' | 'escluso' | 'gia_attivo' | 'errore';
  motivo?: string; profilo_versione?: number; fonte?: 'sec' | 'esef';
}
/** `GET /filings`: copertura del portafoglio vista dal Consigliere (filing_routes.overview). */
export interface FilingOverviewTitolo {
  ticker: string;
  /** 0 novita' dall'ultima run del comitato, 1 cambiamenti, 2 nessun cambiamento, 3 senza confronto */
  gruppo: 0 | 1 | 2 | 3;
  stato_riga: string; fonte: string | null; ultimo_confronto: string | null;
  /** run del confronto citato nella riga di stato (get_filing_changes run_id), null senza confronto */
  run_id?: number | null;
  novita: boolean; profilo: boolean; escluso: boolean;
  /** Fase F: freschezza della riga di stato (null senza profilo); assente con un backend precedente. */
  freschezza?: 'aggiornato' | 'non_aggiornato' | 'senza_confronto' | null;
  /** Fase E (pagina Filing): stato canonico per la UI (filing_stato_ui.stato_ui) e suo gruppo. */
  nome?: string | null;
  stato?: FilingStatoUi;
  gruppo_ui?: FilingGruppoUi;
  /** «SEC 10-Q», «ESEF annuale», «ESEF annuale + IR semestrale» (filing_stato_ui.documento) */
  documento?: string | null;
  /** cambiamenti del confronto citato */
  cambiamenti?: number;
  run_attivo?: { id: number; started_at?: string | null; trigger?: string | null } | null;
  ultimo_errore?: { at?: string | null; reason?: string | null } | null;
  /** ultimo esito di attivazione salvato (attiva, attiva i mancanti, rifiuto, scollegamento) */
  attivazione?: { esito: string; motivo?: string | null; candidati?: number | null; at?: string | null } | null;
  /** Fase F: PDF IR trovati sul sito (ultima esplorazione in cache), solo per i titoli senza profilo */
  pdf_ir?: FilingPdfIr | null;
  /** proposta AI in cache non ancora salvata ne' scartata */
  proposta_ai?: { sha256: string; url: string; at?: string | null; salvabile: boolean; verificate: number } | null;
  /** solo il portafoglio entra nel contesto del Consigliere */
  nel_contesto?: boolean;
  prossimo_at?: string | null;
}
/** Fase F: documento periodico scelto tra i PDF del sito (tipo e periodo dedotti dal nome). */
export interface FilingPdfDoc { url: string; testo: string; tipo: 'annuale' | 'semestrale' | 'trimestrale'; periodo: string }
export interface FilingPdfIr {
  tipo: FilingPdfDoc['tipo']; ultimo: FilingPdfDoc; precedente: FilingPdfDoc | null; candidati: number;
  at?: string | null; sito?: string | null;
}
export type FilingStatoUi = 'novita' | 'aggiornato' | 'invariato' | 'in_corso' | 'primo_confronto' | 'errore'
  | 'da_confermare' | 'proposta_ai' | 'senza_fonte' | 'non_attivo' | 'escluso';
export type FilingGruppoUi = 'novita' | 'da_sistemare' | 'aggiornati' | 'senza_fonte';
export interface FilingControlloGiornaliero {
  attivo: boolean; forzato_spento_da_env: boolean; prossimo_at?: string | null; ultimo_fine_at?: string | null;
}
export interface FilingOverview {
  ambito?: 'portafoglio' | 'preferiti';
  controllo_giornaliero?: FilingControlloGiornaliero | null;
  titoli: FilingOverviewTitolo[];
  /** aggiornati / non_aggiornati / senza_confronto: la freschezza della riga di stato di ogni titolo con profilo */
  copertura: { totale: number; con_confronto: number; aggiornati: number; non_aggiornati: number; senza_confronto?: number; senza_profilo: number; esclusi: number };
  contesto: { caratteri: number; budget: number; omessi_totali: number };
  /** stato del manager di aggiornamento (`status` = 'running' | 'idle' | ...), null se assente */
  aggiornamento: { status?: string; trigger?: string; started_at?: string | null; finished_at?: string | null; error?: string | null } | null;
}
/** `POST /filings/activate-missing`: riepilogo per esito (filing_attivazione.attiva_mancanti). */
export interface FilingActivateMissing {
  attivati: string[]; da_confermare: string[]; senza_fonte: string[]; esclusi: string[]; gia_attivi: string[];
  /** titoli scollegati dall'utente: saltati dall'attivazione in blocco (fase E) */
  scollegati?: string[];
  errori: { ticker: string; motivo: string }[];
  /** presente solo quando il manager e' gia' al lavoro: testo del backend, sempre in italiano */
  aggiornamento?: string;
  /** motivo di ogni esito non in errore, per ticker (RUN-ANDREA, 05/10); assente sui backend precedenti */
  motivi?: Record<string, string | null>;
  /** non null se manca SEC_CONTACT_EMAIL (SEC ed ESEF non configurate): la card lo dice tradotto */
  avviso_configurazione?: string | null;
}
/** `GET /filings/{t}/context-preview`: la riga che il Consigliere riceve per quel titolo. */
export interface FilingContextPreview {
  ticker: string; testo: string; caratteri: number; omessi: number;
  /** ID dei cambiamenti («C3») nell'ordine del punteggio del contesto (fase E) */
  in_evidenza?: string[];
  contesto_totale: { caratteri: number; budget: number }; nota: string;
}
export interface FilingRunDetail extends FilingRun {
  result?: FilingResult | null;
  judgment?: { status: string; findings?: { category?: string; assessment?: string; citations?: string[] }[]; reason?: string; model?: string; usage?: Record<string, unknown>;
    coverage?: { shown?: number; total?: number }; citations_available?: { id: string; testo: string; sezione?: string; url: string; sha256: string }[] } | null;
  index?: { status: string; reason?: string } | null;
}

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
  // 17/09: da dove viene prev_close — "tradegate" (chiusura di sede, orologio
  // TR) | "position_prices" | "carico" | null (n.d. dichiarato).
  prev_close_source?: string | null;
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

// Registro eventi append-only di una decisione (GET /decisions/{id}/events, migrazione 13).
export interface DecisionEvent {
  id: number;
  decision_id: number;
  event_type: string;
  from_status?: string | null;
  to_status?: string | null;
  actor?: string | null;
  reason?: string | null;
  details_json?: string | null;
  created_at: string;
}

export interface Decision {
  id: number;
  trade_idea?: {
    origin: 'trade_idea'; run_id: string; destination_kind: 'dcn' | 'research';
    ticker: string; memo_id: number | null; technical_status: string;
    destination_reason: string | null; artifacts_ready: boolean;
  } | null;
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
  proposal_action?: string | null;
  proposal_ticker?: string | null;
  proposal_ticker_cell?: string | null;
  proposal_row_index?: number | null;
  assessment_status?: 'OPERATIVE' | 'BLOCKED' | 'OVERRIDE_PENDING' | 'CHECK_UNAVAILABLE' | null;
  assessment_reason?: string | null;
  assessment_override_rationale?: string | null;
  manual_divergences?: Array<{
    id: number; actor: string; reason: string; created_at: string;
    details: { trade_id: number; ticker_proposto: string; ticker_eseguito: string;
      trade_action: string; trade_data: string; isin?: string | null };
  }>;
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
  model_valuation_date?: string | null;
  fx?: { rate: number; on: string; financial_currency: string; quote_currency: string;
    source_url: string; available_at: string; limitation: string } | null;
  comparison_fair_values?: { bear: number; base: number; bull: number };
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
  current_generation?: boolean;
  current_download?: string;
  historical_download?: boolean;
  automation?: {
    status: string; locked: boolean | null;
    current: { generation_id: string; revision: number; as_of: string; published_at: string } | null;
    approval?: { publication_origin: string; active: boolean };
    latest_publication_attempt?: { status: string; reason: string; created_at: string } | null;
    latest_prepare_job?: { status: string; reason: string; updated_at: string } | null;
    latest_price_job?: { status: string; reason: string; updated_at: string } | null;
  };
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
  // Archivio (voce 8, 04/10): i memo Trade Idea entrano con etichetta; campi additivi
  kind?: 'consigliere' | 'trade_idea' | string | null;
  label?: string | null;
  trade_idea_run_id?: string | null;
  trade_idea_provenance_error?: string | null;
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
  /** Stesse metriche su SPY (EUR, stessi giorni). Assente/null = backend vecchio o SPY n.d. */
  benchmark?: {
    ticker: string; vol_annual_pct: number; sharpe: number;
    var_95_1d_pct: number; var_99_1d_pct: number;
    beta_vs_spy: number; max_dd_1y_pct: number; n_obs: number;
  } | null;
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
  classification_attempted?: number;
  classification_failed?: number;
  saved?: number;
  skipped_duplicates?: number;
  providers_blocked?: Record<string, string> | null;
  motivo?: string | null;
}

/** Consumo del budget di un provider contingentato (backend 02/10/2026, zero rete). */
export interface NewsProviderBudget {
  used: number; daily: number; allowed_now: number | null; pace: [number, number] | null; next_call_at: string | null;
}

export interface NewsProvidersResponse extends FontiMuteFields {
  providers_contingentati?: string[];
  budget?: Record<string, NewsProviderBudget>;
  ultimo_giro?: UltimoGiro | null;
  refresh_job?: NewsRefreshJob | null;
  nota?: string;
  timestamp: string;
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

/** Riassunto AI di un articolo (backend: market_data/article_summary.py). Cache per notizia e lingua. */
export type ArticleSummaryResult =
  | { status: 'none' }
  | { status: 'done'; news_id: number; summary: string; points: string[]; key_numbers: string[]; portfolio: string;
      model: string; cost_eur: number | null; cost_usd: number | null; cost_status: string; words: number;
      duration_s: number; created_at: string; language: 'it' | 'en'; cached: boolean }
  | { status: 'not_configured'; variable: string }
  | { status: 'unreadable'; reason: 'paywall' | 'blocked' | 'too_short' | 'not_html' | 'fetch' | 'unsafe_url' | 'no_url'; detail: string }
  | { status: 'error'; detail: string };

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
  /** true when the date is estimated from a recurring release window, not an official date */
  date_estimated?: boolean;
  /** Earnings only: the portfolio ticker the release belongs to */
  ticker?: string;
  /** Earnings only: where the date comes from */
  source?: 'finnhub' | 'yfinance';
}

export interface NewsTopicMeta {
  id: string;
  label: string;
  category: string;
  query: string;
  importance: number;
  tickers_affected?: string[];
}

/** Scenario deterministico (stress_nature = "deterministic"): il replay storico copre tutto
 *  l'orizzonte. `scenario_loss_pct/eur` sono un rendimento CON SEGNO (negativo = perdita). */
export interface MonteCarloDeterministicScenario {
  label: string;
  scenario?: string | null;
  replayed_days: number | null;
  horizon_days: number | null;
  scenario_loss_pct: number | null;
  scenario_loss_eur: number | null;
  scenario_max_drawdown_pct: number | null;
  metrics_not_applicable: string[];
  sign_convention?: string | null;
  reason?: string | null;
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
  // Natura dello stress (voce 5 handoff-4, sync 2a72bf8). "deterministic" = il replay copre
  // TUTTO l'orizzonte: ogni simulazione è identica, non c'è distribuzione e le metriche
  // statistiche escono null (elenco in deterministic_scenario.metrics_not_applicable).
  // "fixed_then_simulated" = replay o shock fisso e poi simulazione: metriche CONDIZIONATE.
  // Assenti sui payload pre-sync: la pagina non deduce la natura da sola.
  stress_nature?: 'none' | 'deterministic' | 'fixed_then_simulated';
  stress_nature_label?: string | null;
  deterministic_scenario?: MonteCarloDeterministicScenario | null;
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
  // ⚠ null con lo scenario deterministico (vedi stress_nature): mai letti come zero
  percentiles_ratio: Record<string, number> | null;
  percentiles_eur: Record<string, number> | null;
  expected_return_pct: number | null;
  median_return_pct: number | null;
  stdev_pct: number | null;
  sharpe_simulated: number | null;
  prob_negative_pct: number | null;
  prob_loss_10pct: number | null;
  prob_loss_20pct: number | null;
  prob_gain_10pct: number | null;
  prob_gain_20pct: number | null;
  var_95_pct?: number | null;
  var_99_pct?: number | null;
  var_99_cornish_fisher_pct?: number | null;
  es_95_pct?: number | null;
  es_99_pct?: number | null;
  es_95_eur?: number | null;
  es_99_eur?: number | null;
  max_drawdown_p5_pct: number | null;
  max_drawdown_median_pct: number | null;
  max_drawdown_p95_pct: number | null;
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
    // true = scenario deterministico: UNA traiettoria, i p5..p95 coincidono e NON sono
    // percentili; `label` lo dice (testo del motore, localizzato)
    deterministic?: boolean;
    label?: string | null;
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
export interface MktNewsItem {
  title: string; link?: string; publisher?: string; published?: string | number;
  /** where the item comes from: Yahoo news of the symbol, or Yahoo search */
  source?: 'yahoo_ticker_news' | 'yahoo_search';
  /** how it was matched: by symbol, or by company NAME (may concern another company) */
  match?: 'simbolo' | 'nome';
  /** the company name used for a by-name match */
  match_query?: string;
}
/** /market/news: `errori` lists every route that failed (never an empty list in silence) */
export interface MktNewsResponse { ticker: string; symbol?: string; items: MktNewsItem[]; errori?: string[] | null; }
export interface FinBlock { years: (number | string)[]; rows: Record<string, (number | null)[]>; }
export interface MktFinancials { ticker: string; statements?: { income: FinBlock; balance: FinBlock; cashflow: FinBlock }; error?: string; }
export interface MktHolders { ticker: string; major: { label: string; value: number | string | null }[]; institutional: Record<string, any>[]; }
export interface MktOverviewRow { ticker: string; name: string; price?: number | null; change_pct?: number | null; }
/** Stato dichiarato di una fonte di mercato: `ok` oppure `non_disponibile` con il motivo. */
export interface MktFonteStato { stato: 'ok' | 'non_disponibile' | string; motivo?: string | null; fonte?: string | null; n?: number }
export interface MktOverview { country: string; countries: string[]; indici: MktOverviewRow[]; azioni: MktOverviewRow[];
  commodities: MktOverviewRow[]; valute: MktOverviewRow[]; obbligazioni: MktOverviewRow[]; futures: MktOverviewRow[];
  /** Fonte delle azioni del paese (screener): con `non_disponibile` le azioni possono essere []. */
  azioni_fonte?: MktFonteStato; }
export interface MktMoverRow extends MktOverviewRow { country: string; }
/** Le azioni più grandi per capitalizzazione di ogni paese (fino a 20, dal provider). Nulla disponibile:
 *  `azioni: []` e `motivo` valorizzato; `paesi` dichiara lo stato paese per paese. */
export interface MktMovers { azioni: MktMoverRow[]; paesi?: Record<string, MktFonteStato>; fonte?: string; motivo?: string | null; }
export interface MktNewsTranslation { status: 'done' | 'not_configured' | 'error'; titles?: string[]; language?: string;
  cached?: boolean; complete?: boolean; model?: string | null; cost_eur?: number | null; variable?: string; detail?: string; }
export interface OhlcBar { t: number; o: number; h: number; l: number; c: number; v: number; }
export interface OhlcResponse { ticker: string; period: string; interval: string; bars: OhlcBar[]; error?: string; }

// Gap-days 17/09: una riga per seduta di borsa della finestra. `forming` = la
// close di oggi non e' ancora ufficiale (live snapshot vs ultima chiusura);
// `missing` = nomi senza close su quel giorno (n.d. dichiarato, mai 0).
export interface GapDay {
  pnl_eur: number; pnl_pct: number | null; invested_start_eur: number;
  forming: boolean; missing: string[]; tickers: Record<string, number>;
}
export interface GapDaysResponse {
  window: { start: string; end: string; today: string } | null;
  days: Record<string, GapDay>;
  stub_eur: number | null; window_check_eur: number | null; window_live_eur: number | null;
  baseline_gap_eur?: number | null;
  qty_stable: boolean | null; qty_nota: string | null;
  unpriced: string[]; no_baseline: string[]; approx_today?: string[];
  sources: string | null; nota?: string | null; error?: string | null;
}

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
export type AttributionPeriod = 'MTD' | '30D' | '1W' | '3M' | 'YTD' | 'INCEPTION';
/** Episodio di drawdown sull'indice TWR (portfolio_tearsheet._drawdown_episodes). */
export interface TearsheetDrawdown {
  start_date: string; trough_date: string; depth_pct: number; days_to_trough: number;
  recovery_date: string | null; days_total: number | null; open: boolean;
}
export interface TearsheetPayload {
  drawdowns?: {
    top: TearsheetDrawdown[];
    current: (Omit<TearsheetDrawdown, 'recovery_date'> & { current_dd_pct: number }) | null;
    n_episodes_total: number;
  };
  error?: string;
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
  fundamentalsResearch: () => api.get<FundResearchList>('/fundamentals/research').then(r => r.data),
  fundamentalsCompany: (ticker: string) => api.get<FundResearchCompany>(`/fundamentals/research/${encodeURIComponent(ticker)}`).then(r => r.data),
  fundamentalsArchive: () => api.get<FundArchiveList>('/fundamentals/archive').then(r => r.data),
  filingList: (ticker: string) => api.get<FilingListing>(`/filings/${encodeURIComponent(ticker)}`).then(r => r.data),
  filingRun: (runId: number) => api.get<FilingRunDetail>(`/filings/runs/${encodeURIComponent(runId)}`).then(r => r.data),
  filingRefresh: (ticker: string) => api.post<{run_id: number; status: 'queued'}>(`/filings/${encodeURIComponent(ticker)}/refresh`).then(r => r.data),
  filingProposal: (ticker: string) => api.get<FilingProposal>(`/filings/${encodeURIComponent(ticker)}/proposal`).then(r => r.data),
  /** Senza scelta attiva la fonte univoca; `cik` conferma un emittente SEC, `lei` uno ESEF (mai entrambi). */
  filingActivate: (ticker: string, scelta?: { cik?: string; lei?: string }) =>
    api.post<FilingActivation>(`/filings/${encodeURIComponent(ticker)}/activate`,
      scelta?.cik ? { cik: scelta.cik } : scelta?.lei ? { lei: scelta.lei } : {}).then(r => r.data),
  /** Stima gratuita (scarica il PDF indicato e misura l'input); nessuna chiamata AI. */
  filingAiEstimate: (ticker: string, url: string) =>
    api.get<FilingAiEstimate>(`/filings/${encodeURIComponent(ticker)}/ai-estimate`, { params: { url } }).then(r => r.data),
  /** Fase F: esplora il sito e sceglie i PDF IR (gratis, nessuna AI, nessun PDF scaricato). */
  filingSearchPdf: (ticker: string) =>
    api.post<{ ticker: string; pdf_ir: FilingPdfIr | null; sito: string | null; pagine: number; motivi: string[] }>(
      `/filings/${encodeURIComponent(ticker)}/search-pdf`, {}).then(r => r.data),
  /** UNICA chiamata AI del filing: solo dal pulsante «Proponi con AI». Il server attende ~20 s; se il
   *  lavoro continua risponde {stato:'in_corso', job_id} e si legge filingAiProposalJob (lib/filing-ai-job). */
  filingAiProposal: (ticker: string, url: string, altri: string[] = [], riprova = false) =>
    api.post<FilingAiProposal>(`/filings/${encodeURIComponent(ticker)}/ai-proposal`,
      { url, ...(altri.length ? { altri_url: altri } : {}), ...(riprova ? { riprova: true } : {}) }).then(r => r.data),
  /** Stato del lavoro della proposta AI (contratto G2b 04/10: 404 se job sconosciuto o di un altro titolo). */
  filingAiProposalJob: (ticker: string, jobId: string) =>
    api.get<FilingAiProposal>(`/filings/${encodeURIComponent(ticker)}/ai-proposal/job/${encodeURIComponent(jobId)}`).then(r => r.data),
  filingAiAccept: (ticker: string, body: { sha256: string; ir_urls: string[]; sostituisci?: boolean; aggiungi_variante?: boolean }) =>
    api.post<FilingAiAccept>(`/filings/${encodeURIComponent(ticker)}/ai-proposal/accept`, body).then(r => r.data),
  filingOverview: () => api.get<FilingOverview>('/filings').then(r => r.data),
  filingActivateMissing: () => api.post<FilingActivateMissing>('/filings/activate-missing').then(r => r.data),
  filingContextPreview: (ticker: string) =>
    api.get<FilingContextPreview>(`/filings/${encodeURIComponent(ticker)}/context-preview`).then(r => r.data),
  filingOverviewAmbito: (ambito: 'portafoglio' | 'preferiti') =>
    api.get<FilingOverview>('/filings', { params: { ambito } }).then(r => r.data),
  filingNovita: () => api.get<{ n: number; tickers: string[] }>('/filings/novita').then(r => r.data),
  filingAutoRefresh: (attivo: boolean) => api.put<{ attivo: boolean }>('/filings/auto-refresh', { attivo }).then(r => r.data),
  filingReject: (ticker: string, scelta: { cik?: string | string[]; lei?: string | string[] }) =>
    api.post<{ ticker: string; proposta: FilingProposal; esito: string }>(`/filings/${encodeURIComponent(ticker)}/reject`, scelta).then(r => r.data),
  filingUnlink: (ticker: string) =>
    api.post<{ ticker: string; esito: 'scollegato'; versione: number }>(`/filings/${encodeURIComponent(ticker)}/unlink`, {}).then(r => r.data),
  filingExclude: (ticker: string, escluso: boolean) =>
    api.post<{ ticker: string; escluso: boolean }>(`/filings/${encodeURIComponent(ticker)}/exclude`, { escluso }).then(r => r.data),
  /** Ultima proposta AI in cache: gratis, nessun download ne' chiamata al modello. */
  filingAiSaved: (ticker: string) =>
    api.get<FilingAiProposalSalvata>(`/filings/${encodeURIComponent(ticker)}/ai-proposal`).then(r => r.data),
  filingAiDiscard: (ticker: string, sha256: string) =>
    api.post<{ ticker: string; sha256: string; scartata: boolean }>(`/filings/${encodeURIComponent(ticker)}/ai-proposal/discard`, { sha256 }).then(r => r.data),
  filingSaveProfile: (ticker: string, data: { profile: Record<string, unknown>; enabled: boolean; interval_hours: number; qualitative_enabled: boolean }) =>
    api.put<FilingProfile>(`/filings/${encodeURIComponent(ticker)}/profile`, data).then(r => r.data),
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
  // cronologia in sola lettura della pagina Decisioni (registro decision_events)
  decisionEvents: (id: number) =>
    api.get<{events: DecisionEvent[]}>(`/decisions/${id}/events`).then(r => r.data),
  // Verifica ISIN e divergenza manuale NON hanno piu' chiamate proprie qui:
  // viaggiano nel corpo di previewTrade/logTrade e il backend le scrive nella
  // transazione del trade confermato (tabelle append-only: mai prima della conferma).
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
    add?: string; remove?: string; force?: boolean; sample_paths_n?: number;
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
  weeklyRecoveries: () => api.get<{ runs: WeeklyRecovery[]; reason?: string }>('/consigliere/runs').then(r => r.data),
  recoverConsigliere: (memoId: number, deliveryOnly: boolean) => api.post<{ task_id: string }>('/consigliere/run', {
    resume_memo_id: memoId, delivery_only: deliveryOnly, authorize_new_ai: !deliveryOnly, send_email: false,
  }).then(r => r.data),
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
  attribution: (period: AttributionPeriod = 'YTD') =>
    api.get<AttributionPayload>('/portfolio/attribution', { params: { period }, timeout: 120000 }).then(r => r.data),
  // Tearsheet sul TWR ufficiale (fase 1c): qui servono gli episodi di drawdown sull'indice TWR.
  tearsheet: (force = false) =>
    api.get<TearsheetPayload>('/portfolio/tearsheet', { params: force ? { force: true } : {}, timeout: 90000 }).then(r => r.data),
  ohlc: (ticker: string, period = '1y', interval = '1d') =>
    api.get<OhlcResponse>('/market/ohlc', { params: { ticker, period, interval }, timeout: 60000 }).then(r => r.data),
  // Gap-days 17/09: scomposizione della finestra multi-seduta per seduta di
  // borsa (chiusure Yahoo + live). Prima chiamata scarica lo storico (lenta),
  // poi cache server 10min. Solo giorni con sedute, mai un finto daily.
  gapDays: (force = false) =>
    api.get<GapDaysResponse>('/portfolio/gap_days', { params: force ? { force: true } : {}, timeout: 120000 }).then(r => r.data),
  mktSearch: (q: string) =>
    api.get<{results: MktSearchHit[]}>('/market/search', { params: { q }, timeout: 15000 }).then(r => r.data),
  mktOverview: (country = 'US') =>
    api.get<MktOverview>('/market/overview', { params: { country }, timeout: 30000 }).then(r => r.data),
  mktMovers: () =>
    api.get<MktMovers>('/market/movers', { timeout: 45000 }).then(r => r.data),
  mktNewsTranslate: (titles: string[]) =>
    api.post<MktNewsTranslation>('/market/news/translate', { titles }, { timeout: 60000 }).then(r => r.data),
  mktQuote: (ticker: string) =>
    api.get<MktQuote>('/market/quote', { params: { ticker }, timeout: 30000 }).then(r => r.data),
  mktNews: (ticker: string) =>
    api.get<MktNewsResponse>('/market/news', { params: { ticker }, timeout: 30000 }).then(r => r.data),
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
    api.get<NewsProvidersResponse>('/news/providers').then(r => r.data),
  /** Stato di un job di refresh (contratto G3 04/10: 404 se l'id è sconosciuto; tiene gli ultimi 20). */
  newsRefreshJob: (jobId: string) =>
    api.get<NewsRefreshJobResponse>('/news/feed/refresh', { params: { job_id: jobId } }).then(r => r.data),
  newsFeedRefresh: (days = 1, classify = true) =>
    api.post<NewsRefreshResponse & FontiMuteFields>(
      '/news/feed/refresh', null, { params: { days, classify }, timeout: 30000 }
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
  economicCalendar: (days_ahead = 7, earnings_days_ahead?: number) =>
    api.get<{ count: number; items: EconomicEvent[]; timestamp: string }>(
      '/news/economic-calendar', { params: earnings_days_ahead ? { days_ahead, earnings_days_ahead } : { days_ahead } }
    ).then(r => r.data),
  /** Company logos as data: URLs (downloaded once by the backend, kept in data/loghi). */
  marketLogos: (tickers: string[]) =>
    api.get<{ logos: Record<string, string | null>; motivi?: Record<string, string>; fonti?: Record<string, string> }>(
      '/market/logos', { params: { tickers: tickers.join(',') }, timeout: 60000 }
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
  // Riassunto AI dell'articolo: GET legge solo la cache (gratis), POST lo genera (crediti OpenRouter).
  newsArticleSummary: (newsId: number) =>
    api.get<ArticleSummaryResult>(`/news/${newsId}/article-summary`).then(r => r.data),
  newsArticleSummaryRun: (newsId: number, regenerate = false) =>
    api.post<ArticleSummaryResult>(`/news/${newsId}/article-summary`, null, { params: { regenerate }, timeout: 120000 }).then(r => r.data),
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
