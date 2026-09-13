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
};
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

export function decisioneCompatibile(d: Pick<Decision, 'ticker' | 'action' | 'status' | 'veto'>,
                                     ticker: string, action: string): boolean {
  const side = (s: string) => ['BUY', 'ADD'].includes(s) ? 'buy'
    : ['SELL', 'TRIM'].includes(s) ? 'sell' : null;
  return d.ticker.toUpperCase() === ticker.toUpperCase() && !!side(action)
    && side(d.action) === side(action) && ['PENDING', 'EXECUTED', 'PARTIAL'].includes(d.status) && !d.veto;
}

export function legameTrade(selection: string, decisions: Decision[], ticker: string, action: string) {
  if (selection === 'none') return { senza_decisione: true };
  if (selection === 'unknown') return { senza_decisione: false };
  const id = Number(selection);
  const decision = Number.isSafeInteger(id) && id > 0 ? decisions.find(d => d.id === id) : undefined;
  if (!decision || !decisioneCompatibile(decision, ticker, action)) {
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
      || (body.linked_decision_id != null && p.decisione?.id !== body.linked_decision_id)) {
    throw new FrontendTradeError(() => tr('trade.preview_inconsistent'));
  }
  return { body: Object.freeze({ ...body, preview_id: p.preview_id }), preview: p };
}
