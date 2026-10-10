import { t as tr } from '../i18n/t.js';
import { leggiDataValuta } from './cassa';
import type { Decision } from './api';

/** Local UI prose can react to a language change; backend/source text is never rewritten. */
export class FrontendTradeError extends Error {
  constructor(readonly renderMessage: () => string) { super(renderMessage()); }
}

export type TradeRequest = {
  ticker: string; action: string; quantita: number; prezzo: number; valuta: string;
  note?: string; pm_rationale?: string; data?: string; linked_decision_id?: number;
  senza_decisione?: boolean; preview_id?: string;
  /** Verifica ISIN e divergenza manuale viaggiano col trade: il backend le
   *  valida in anteprima SENZA scriverle e le scrive solo alla conferma, nella
   *  stessa transazione del trade (tabelle append-only). */
  instrument_identity?: IdentityBody; manual_divergence?: { decision_id: number; reason: string };
};
export type IdentityBody = { isin: string; source: string; verified_at: string; reason: string };
/** Cio' che il backend ha validato e scrivera': il ticker della proposta lo
 *  sceglie il server dalla decisione, non il browser. */
export type IdentityView = IdentityBody & { proposed_ticker: string; execution_ticker: string };
export type DivergenceView = {
  decision_id: number; reason: string; ticker_proposto: string; ticker_eseguito: string;
  isin?: string | null; event_id?: number; assessment_status?: string | null;
};

/** Stati NON operativi che ammettono la divergenza manuale: gli stessi del backend
 *  (G6, memory_db.manual_divergence_details). Per OVERRIDE_PENDING e CHECK_UNAVAILABLE
 *  e' l'unica via per riconciliare cio' che il PM ha eseguito con la proposta. */
export const STATI_DIVERGENZA = ['BLOCKED', 'OVERRIDE_PENDING', 'CHECK_UNAVAILABLE'] as const;
export type StatoDivergenza = typeof STATI_DIVERGENZA[number];
export function statoDivergenza(d: Partial<Pick<Decision, 'assessment_status'>> | null | undefined): StatoDivergenza | null {
  const s = String(d?.assessment_status || '').toUpperCase();
  return (STATI_DIVERGENZA as readonly string[]).includes(s) ? s as StatoDivergenza : null;
}
const BADGE_DIVERGENZA = { BLOCKED: 'trade.blocked_badge', OVERRIDE_PENDING: 'trade.override_pending_badge',
  CHECK_UNAVAILABLE: 'trade.check_unavailable_badge' } as const;
const SPIEGAZIONE_DIVERGENZA = { BLOCKED: 'trade.manual_divergence_explanation',
  OVERRIDE_PENDING: 'trade.manual_divergence_explanation_override',
  CHECK_UNAVAILABLE: 'trade.manual_divergence_explanation_unavailable' } as const;
/** Etichetta dello stato (BLOCCATA / DEROGA IN ATTESA / NON VERIFICABILE); stato ignoto = n.d. */
export function etichettaStatoDivergenza(stato: string | null | undefined): string {
  const s = statoDivergenza({ assessment_status: stato as Decision['assessment_status'] });
  return s ? tr(BADGE_DIVERGENZA[s]) : tr('trade.na');
}
/** Spiegazione del caso per la proposta scelta: dice PERCHE' non e' operativa. */
export function spiegazioneDivergenza(stato: StatoDivergenza, ticker: string): string {
  return tr(SPIEGAZIONE_DIVERGENZA[stato], { ticker });
}
type ReplayState = {
  quantita: number; prezzo_medio: number; realized: number | null; data_apertura: string | null;
};
export type TradeResult = {
  ok?: boolean; trade_id?: number | string | null;
  cash_disponibile_eur?: number | null; cash_delta_eur?: number;
  cash_note?: string | null; guardia_note?: string | null;
  data?: string; ora_convenzionale?: boolean; link_origin?: 'explicit' | 'none' | 'unknown';
  fx?: { tasso: number; fonte: 'identity' | 'storico' | 'corrente'; nota?: string | null; data?: string };
  decisione?: { id: number; status: string; nota?: string | null } | null;
  ricalcolo?: { prima: ReplayState; dopo: ReplayState; valuta: string; trade_successivi: number[]; note?: string[] } | null;
  cassa_nota?: string;
  performance_note?: string | null;
  instrument_identity?: IdentityView | null;
  manual_divergence?: DivergenceView | null;
};
export type TradePreview = TradeResult & {
  ok: true; preview_id: string; expires_in_seconds: number;
  cash_delta_eur: number; cash_disponibile_eur: number;
  data: string; ora_convenzionale: boolean; link_origin: 'explicit' | 'none' | 'unknown';
  fx: NonNullable<TradeResult['fx']>;
};

export function dataTrade(day: string, time: string, today: string): string | undefined {
  const value = leggiDataValuta(day, today);
  if (!value) {
    if (time.trim()) throw new FrontendTradeError(() => tr('trade.date_before_time'));
    return undefined;
  }
  if (!value.ok) throw new FrontendTradeError(() => {
    const current = leggiDataValuta(day, today); return current && !current.ok ? current.motivo : value.motivo;
  });
  if (value.iso > today) throw new FrontendTradeError(() => tr('trade.trade_date_future'));
  if (!time.trim()) return value.iso;
  if (!/^([01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$/.test(time.trim())) {
    throw new FrontendTradeError(() => tr('trade.trade_time_format'));
  }
  return `${value.iso}T${time.trim().length === 5 ? time.trim() + ':00' : time.trim()}`;
}

/** Collegamento sospeso (10/10, Opus 5.5): GET /decisions dichiara `trade_idea_storage` (provenienza Trade
 *  Idea NON letta) e la decisione non porta `trade_idea`. Quel null vuol dire «non letto», non «nessuna Trade
 *  Idea»: la decisione potrebbe venire da una run non completata o non DCN, quindi non si collega finche' la
 *  provenienza non si legge. Una `trade_idea` presente e' stata letta e si valuta come sempre. */
export function collegamentoSospeso(d: Pick<Decision, 'trade_idea'>, tradeIdeaStorage: unknown): boolean {
  return tradeIdeaStorage != null && !d.trade_idea;
}

export function decisioneCompatibile(d: Pick<Decision, 'ticker' | 'action' | 'status' | 'veto' | 'trade_idea'>
                                      & Partial<Pick<Decision, 'assessment_status'>>,
                                     ticker: string, action: string,
                                     verifiedTickerAlias = false,
                                     tradeIdeaStorage: unknown = null): boolean {
  const side = (s: string) => ['BUY', 'ADD'].includes(s) ? 'buy'
    : ['SELL', 'TRIM'].includes(s) ? 'sell' : null;
  const idea = d.trade_idea;
  const usableIdea = !collegamentoSospeso(d, tradeIdeaStorage) && (!idea || (idea.technical_status === 'completed'
    && idea.destination_kind === 'dcn' && idea.artifacts_ready === true));
  return usableIdea
    && (d.ticker.toUpperCase() === ticker.toUpperCase() || verifiedTickerAlias)
    && !!side(action) && side(d.action) === side(action)
    && ['PENDING', 'EXECUTED', 'PARTIAL'].includes(d.status) && !d.veto
    && assessmentAllowsExecution(d);
}

export function assessmentAllowsExecution(d: Partial<Pick<Decision, 'assessment_status'>>): boolean {
  const status = String(d.assessment_status || '').toUpperCase();
  return !status || status === 'OPERATIVE';
}

/** Il legame di partenza: l'id della decisione se il trade nasce dal suo pulsante,
 *  altrimenti «non dichiarato». Partire da «nessuna decisione» faceva salvare
 *  un'affermazione che il PM non ha mai fatto. */
export function legameIniziale(decisionFromRoute: string | null): string {
  return decisionFromRoute || 'unknown';
}

export function legameTrade(selection: string, decisions: Decision[], ticker: string, action: string,
                            verifiedTickerAlias = false, tradeIdeaStorage: unknown = null) {
  if (selection === 'none') return { senza_decisione: true };
  if (selection === 'unknown') return { senza_decisione: false };
  const id = Number(selection);
  const decision = Number.isSafeInteger(id) && id > 0 ? decisions.find(d => d.id === id) : undefined;
  if (decision && collegamentoSospeso(decision, tradeIdeaStorage)) {
    throw new FrontendTradeError(() => tr('trade.link_suspended_unreadable'));
  }
  if (!decision || !decisioneCompatibile(decision, ticker, action, verifiedTickerAlias, tradeIdeaStorage)) {
    throw new FrontendTradeError(() => tr('trade.decision_incompatible'));
  }
  return { senza_decisione: false, linked_decision_id: id };
}

export function congelaAnteprima(body: TradeRequest, value: unknown) {
  const p = value as TradePreview | null;
  const finite = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n);
  const expectedOrigin = body.linked_decision_id != null ? 'explicit' : body.senza_decisione ? 'none' : 'unknown';
  if (!p || p.ok !== true || typeof p.preview_id !== 'string' || !p.preview_id
      || !finite(p.expires_in_seconds) || p.expires_in_seconds <= 0
      || !finite(p.cash_delta_eur) || !finite(p.cash_disponibile_eur)
      || typeof p.data !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/.test(p.data)
      || typeof p.ora_convenzionale !== 'boolean' || p.link_origin !== expectedOrigin
      || !p.fx || !finite(p.fx.tasso) || p.fx.tasso <= 0
      || !['identity', 'storico', 'corrente'].includes(p.fx.fonte)
      || (body.linked_decision_id != null && p.decisione?.id !== body.linked_decision_id)
      || !anteprimaRiportaLegame(body, p)) {
    throw new FrontendTradeError(() => tr('trade.preview_inconsistent'));
  }
  return { body: Object.freeze({ ...body, preview_id: p.preview_id }), preview: p };
}

/** La verifica ISIN e la divergenza che il PM conferma devono essere quelle che
 *  il backend ha validato: un'anteprima che non le riporta (o ne riporta altre)
 *  non diventa una conferma. */
function anteprimaRiportaLegame(body: TradeRequest, p: TradePreview): boolean {
  const id = p.instrument_identity, div = p.manual_divergence;
  const identitaOk = body.instrument_identity
    ? !!id && id.isin === body.instrument_identity.isin.trim().toUpperCase()
      && id.execution_ticker === body.ticker.trim().toUpperCase() && !!id.proposed_ticker
    : id == null;
  const divergenzaOk = body.manual_divergence
    ? !!div && div.decision_id === body.manual_divergence.decision_id
      && div.reason === body.manual_divergence.reason.trim()
    : div == null;
  return identitaOk && divergenzaOk;
}

/** Il corpo della verifica ISIN dai campi del modulo. La data/ora arriva da un
 *  `datetime-local` (ora locale): si converte in ISO UTC. Una data illeggibile
 *  e' un errore dichiarato, non il «RangeError: Invalid time value» grezzo. */
export function corpoIdentita(isin: string, source: string, verifiedAt: string, reason: string): IdentityBody {
  if (!isin.trim() || !source.trim() || !verifiedAt.trim() || !reason.trim()) {
    throw new FrontendTradeError(() => tr('trade.manual_divergence_identity_required'));
  }
  const quando = new Date(verifiedAt.trim());
  if (!Number.isFinite(quando.getTime())) {
    throw new FrontendTradeError(() => tr('trade.identity_verified_at_invalid'));
  }
  return { isin: isin.trim().toUpperCase(), source: source.trim(), verified_at: quando.toISOString(), reason: reason.trim() };
}

/** Le righe del dialog di conferma per alias del ticker e divergenza manuale,
 *  lette SOLO dall'anteprima validata dal backend (cio' che verra' scritto). */
export function righeLegame(p: TradePreview): { k: string; v: string; tone?: 'amber' | 'crimson' }[] {
  const rows: { k: string; v: string; tone?: 'amber' | 'crimson' }[] = [];
  const id = p.instrument_identity;
  if (id) {
    rows.push({ k: tr('trade.confirm_identity'), tone: 'amber',
      v: tr('trade.confirm_identity_value', { proposal: id.proposed_ticker, execution: id.execution_ticker,
        isin: id.isin, source: id.source, at: id.verified_at }) });
    rows.push({ k: tr('trade.identity_reason_label'), v: id.reason });
  }
  const div = p.manual_divergence;
  if (div) {
    rows.push({ k: tr('trade.confirm_divergence'), tone: 'crimson',
      v: tr('trade.confirm_divergence_value', { id: div.decision_id, stato: etichettaStatoDivergenza(div.assessment_status), proposal: div.ticker_proposto,
        execution: div.ticker_eseguito }) });
    rows.push({ k: tr('trade.confirm_divergence_reason'), v: div.reason });
  }
  return rows;
}

/** Totali versato/prelevato del registro letto. Nessun importo mancante conta
 *  come 0 e nessun tipo sconosciuto finisce sotto «prelevato»: in quei casi il
 *  totale e' n.d. col motivo. A finestra piena la somma e' un minimo (`parziale`). */
export type TotaliMovimenti = {
  versati: number | null; prelevati: number | null; parziale: boolean;
  motivoVersati: string | null; motivoPrelevati: string | null;
};
export function totaliMovimenti(movimenti: { type: string; amount_eur: unknown }[], limite: number): TotaliMovimenti {
  const somma = { DEPOSIT: 0, WITHDRAWAL: 0 }, mancanti = { DEPOSIT: 0, WITHDRAWAL: 0 };
  const ignoti = new Set<string>();
  for (const m of movimenti) {
    // un tipo che non si sa classificare puo' essere un'entrata o un'uscita:
    // rende n.d. ENTRAMBI i totali, invece di finire d'ufficio sotto «prelevato»
    if (m.type !== 'DEPOSIT' && m.type !== 'WITHDRAWAL') { ignoti.add(String(m.type)); continue; }
    const v = typeof m.amount_eur === 'number' && Number.isFinite(m.amount_eur) && m.amount_eur > 0 ? m.amount_eur : null;
    if (v == null) mancanti[m.type] += 1; else somma[m.type] += v;
  }
  const motivo = (tipo: 'DEPOSIT' | 'WITHDRAWAL'): string | null => {
    const motivi: string[] = [];
    if (mancanti[tipo]) motivi.push(tr('trade.totals_missing_amount', { n: mancanti[tipo] }));
    if (ignoti.size) motivi.push(tr('trade.totals_unknown_type', { types: [...ignoti].join(', ') }));
    return motivi.length ? motivi.join(' · ') : null;
  };
  const motivoVersati = motivo('DEPOSIT'), motivoPrelevati = motivo('WITHDRAWAL');
  return { versati: motivoVersati ? null : somma.DEPOSIT, prelevati: motivoPrelevati ? null : somma.WITHDRAWAL,
           parziale: movimenti.length >= limite, motivoVersati, motivoPrelevati };
}
