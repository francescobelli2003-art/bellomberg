import { analizzaNumero } from '../i18n/numeri.js';
import { linguaCorrente, type Lingua } from '../i18n/lingua.js';
import { traduci } from '../i18n/t.js';
import { leggiNumero } from './cassa';
import { dataTrade, FrontendTradeError } from './trade-entry';

export type OpeningDraft = {
  ticker: string; nome: string; quantita: string; prezzo_medio: string; valuta: string;
  giorno: string; ora: string; precisione: 'day' | 'second'; provenienza: string; nota: string;
};
export type OpeningRequest = {
  ticker: string; nome?: string; quantita: number; prezzo_medio: number; valuta: string;
  as_of: string; provenienza: string; nota?: string; preview_id?: string;
};
export type OpeningRecord = Omit<OpeningRequest, 'preview_id'> & {
  precisione_data: 'day' | 'second'; id: number; created_at: string;
};
export type OpeningResult = {
  ok: true; opening: OpeningRecord;
  position: Pick<OpeningRequest, 'ticker' | 'nome' | 'quantita' | 'prezzo_medio' | 'valuta'> & { data_apertura: null };
  cash_delta_eur: 0; cash_disponibile_eur: number | null; performance_note: string;
};
export type OpeningPreview = Omit<OpeningResult, 'opening'> & {
  opening: Omit<OpeningRecord, 'id' | 'created_at'>; preview_id: string; expires_in_seconds: number;
};

/** Only an explicit documented balance; never constructs trade, cash or FX fields. */
export function preparaPosizioneIniziale(draft: OpeningDraft, language: Lingua, today: string,
  inputLanguages: Partial<Record<'quantita' | 'prezzo_medio', Lingua>> = {}): OpeningRequest {
  const t = (key: Parameters<typeof traduci>[1]) => traduci(language, key);
  const quantity = leggiNumero(draft.quantita, inputLanguages.quantita ?? language, language);
  const cost = analizzaNumero(draft.prezzo_medio, inputLanguages.prezzo_medio ?? language, language);
  if (!draft.ticker.trim()) throw new Error(t('trade.opening_ticker_required'));
  if (!quantity) throw new Error(t('trade.opening_quantity_required'));
  if (!quantity.ok) throw new Error(quantity.motivo);
  if (!cost) throw new Error(t('trade.opening_cost_required'));
  if (!cost.ok) throw new Error(cost.motivo);
  if (cost.valore < 0 || !Number.isFinite(quantity.valore * cost.valore)) throw new Error(t('trade.opening_cost_invalid'));
  if (!draft.provenienza.trim()) throw new Error(t('trade.opening_source_required'));
  if (!draft.giorno.trim() || !['day', 'second'].includes(draft.precisione)
      || (draft.precisione === 'second' && !draft.ora.trim())) throw new Error(t('trade.opening_date_required'));
  const asOf = dataTrade(draft.giorno, draft.precisione === 'second' ? draft.ora : '', today);
  if (!asOf) throw new Error(t('trade.opening_date_required'));
  if (!/^[A-Z]{3}$/.test(draft.valuta.trim().toUpperCase())) throw new Error(t('trade.opening_currency_invalid'));
  return {
    ticker: draft.ticker.trim().toUpperCase(), quantita: quantity.valore, prezzo_medio: cost.valore,
    valuta: draft.valuta.trim().toUpperCase(), as_of: asOf, provenienza: draft.provenienza.trim(),
    ...(draft.nome ? { nome: draft.nome } : {}), ...(draft.nota ? { nota: draft.nota } : {}),
  };
}

const object = (x: unknown): x is Record<string, unknown> => !!x && typeof x === 'object' && !Array.isArray(x);
const finite = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x);
const optionalText = (x: unknown) => x == null || typeof x === 'string';
function balance(x: unknown): x is OpeningPreview['opening'] {
  if (!object(x) || typeof x.ticker !== 'string' || !x.ticker.trim()
      || !finite(x.quantita) || x.quantita <= 0 || !finite(x.prezzo_medio) || x.prezzo_medio < 0
      || !Number.isFinite(x.quantita * x.prezzo_medio) || typeof x.valuta !== 'string' || !/^[A-Z]{3}$/.test(x.valuta)
      || typeof x.provenienza !== 'string' || !x.provenienza.trim() || typeof x.as_of !== 'string'
      || !optionalText(x.nome) || !optionalText(x.nota)) return false;
  const date = x.precisione_data === 'day' ? /^\d{4}-\d{2}-\d{2}$/
    : x.precisione_data === 'second' ? /^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d$/ : null;
  if (!date?.test(x.as_of)) return false;
  const day = x.as_of.slice(0, 10), parsed = new Date(day + 'T00:00:00Z');
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === day;
}
function record(x: unknown): x is OpeningRecord {
  if (!object(x)) return false;
  const row: Record<string, unknown> = x;
  return balance(row) && Number.isSafeInteger(x.id) && (x.id as number) > 0
    && typeof x.created_at === 'string' && /^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(x.created_at)
    && Number.isFinite(Date.parse(x.created_at));
}
function coherent(body: OpeningRequest, value: unknown): value is OpeningResult | OpeningPreview {
  if (!object(value) || value.ok !== true || value.cash_delta_eur !== 0
      || !(value.cash_disponibile_eur === null || finite(value.cash_disponibile_eur))
      || typeof value.performance_note !== 'string' || !value.performance_note.trim()
      || !balance(value.opening) || !object(value.position)) return false;
  const opening = value.opening, position = value.position;
  return (['ticker', 'quantita', 'prezzo_medio', 'valuta', 'as_of', 'provenienza'] as const)
      .every(key => opening[key] === body[key])
    && (opening.nota ?? null) === (body.nota ?? null)
    && (!('preview_id' in value) || (opening.nome ?? null) === (body.nome ?? null))
    && opening.precisione_data === (body.as_of.includes('T') ? 'second' : 'day')
    && (['ticker', 'quantita', 'prezzo_medio', 'valuta'] as const).every(key => position[key] === body[key])
    && (position.nome ?? null) === (body.nome ?? null) && position.data_apertura === null;
}
function immutable<T>(value: T): T {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(immutable);
    Object.freeze(value);
  }
  return value;
}
export function congelaPosizioneIniziale(body: OpeningRequest, value: unknown) {
  if (!coherent(body, value) || !('preview_id' in value) || !value.preview_id
      || typeof value.preview_id !== 'string' || !finite(value.expires_in_seconds) || value.expires_in_seconds <= 0) {
    throw new FrontendTradeError(() => traduci(linguaCorrente(), 'trade.opening_preview_invalid'));
  }
  const copy = JSON.parse(JSON.stringify(value)) as OpeningPreview;
  return immutable({ body: { ...body, preview_id: copy.preview_id }, preview: copy });
}
export function leggiRicevutaPosizione(body: OpeningRequest, value: unknown): OpeningResult {
  if (!coherent(body, value) || !record(value.opening)) {
    throw new FrontendTradeError(() => traduci(linguaCorrente(), 'trade.opening_receipt_invalid'));
  }
  return immutable(JSON.parse(JSON.stringify(value)) as OpeningResult);
}
export function leggiPosizioniIniziali(value: unknown): OpeningRecord[] {
  if (!object(value) || !Array.isArray(value.openings) || !value.openings.every(record)
      || new Set(value.openings.map(x => x.ticker)).size !== value.openings.length) {
    throw new FrontendTradeError(() => traduci(linguaCorrente(), 'trade.opening_list_invalid'));
  }
  return value.openings as OpeningRecord[];
}
