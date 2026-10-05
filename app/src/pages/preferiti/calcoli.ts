/* Logica pura della pagina Preferiti: variazione, posizione nell'intervallo a 52 settimane,
   settori, ordinamento e filtro. Nessun dato viene alterato: i filtri agiscono sulla vista. */
import type { FavCompany, MktQuote } from '@/lib/api';

export type Ordine = 'var' | 'nome' | 'data';
/** chiave del settore «non indicato»: non è un nome di settore, la vista la traduce */
export const SENZA_SETTORE = '';
export const TUTTI = '*';

export const finito = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/** Variazione % sulla chiusura precedente; null se uno dei due prezzi manca (mai uno zero inventato). */
export function variazionePct(q: MktQuote | undefined): number | null {
  if (!q || !finito(q.price) || !finito(q.prev_close) || q.prev_close === 0) return null;
  return (q.price / q.prev_close - 1) * 100;
}

/** Posizione del prezzo tra minimo e massimo a 52 settimane, in % (0 = minimo). */
export function posizione52(q: MktQuote | undefined): number | null {
  if (!q || !finito(q.price) || !finito(q.low_52w) || !finito(q.high_52w) || q.high_52w <= q.low_52w) return null;
  return Math.min(100, Math.max(0, ((q.price - q.low_52w) / (q.high_52w - q.low_52w)) * 100));
}
/** Dove cade un valore (es. il target) sulla stessa scala; fuori scala resta ai bordi. */
export function suScala52(q: MktQuote | undefined, v: number | null | undefined): number | null {
  if (!q || !finito(v) || !finito(q.low_52w) || !finito(q.high_52w) || q.high_52w <= q.low_52w) return null;
  return Math.min(100, Math.max(0, ((v - q.low_52w) / (q.high_52w - q.low_52w)) * 100));
}

/** Settore del preferito: quello salvato, altrimenti quello della quotazione, altrimenti «senza settore». */
export function settoreDi(f: FavCompany, q?: MktQuote): string {
  return (f.sector || q?.sector || '').trim() || SENZA_SETTORE;
}
export function industriaDi(f: FavCompany, q?: MktQuote): string {
  return (f.industry || q?.industry || '').trim();
}

/** Settori per numero di preferiti (decrescente), «senza settore» sempre in fondo. */
export function settori(favs: FavCompany[], quotes: Record<string, MktQuote>): Array<{ settore: string; n: number }> {
  const conta = new Map<string, number>();
  for (const f of favs) { const s = settoreDi(f, quotes[f.ticker]); conta.set(s, (conta.get(s) || 0) + 1); }
  return [...conta.entries()].map(([settore, n]) => ({ settore, n }))
    .sort((a, b) => Number(a.settore === SENZA_SETTORE) - Number(b.settore === SENZA_SETTORE) || b.n - a.n || a.settore.localeCompare(b.settore));
}

/** Colore del settore per posizione nell'elenco dei settori: tinte sempre distinte fra loro
 *  (oltre l'ottavo si ricomincia); «senza settore» resta neutro. */
const TINTE = ['#5b8cff', '#2ed47a', '#f0a040', '#b69cff', '#2fb8c9', '#ff7aa8', '#e5c04a', '#9aa4b8'];
export function tintaSettore(settore: string, indice: number): string | null {
  return settore === SENZA_SETTORE ? null : TINTE[indice % TINTE.length];
}

export const nomeDi = (f: FavCompany, q?: MktQuote) => (q?.name || f.name || f.ticker).trim();

export function elenco(favs: FavCompany[], quotes: Record<string, MktQuote>, opz: { settore: string; testo: string; ordine: Ordine }): FavCompany[] {
  const testo = opz.testo.trim().toLocaleLowerCase();
  const r = favs.filter(f => (opz.settore === TUTTI || settoreDi(f, quotes[f.ticker]) === opz.settore)
    && (!testo || [f.ticker, nomeDi(f, quotes[f.ticker]), f.note || ''].some(v => v.toLocaleLowerCase().includes(testo))));
  const per = {
    // senza quotazione in fondo: l'assenza non è uno zero
    var: (a: FavCompany, b: FavCompany) => {
      const va = variazionePct(quotes[a.ticker]), vb = variazionePct(quotes[b.ticker]);
      return va == null && vb == null ? a.ticker.localeCompare(b.ticker) : va == null ? 1 : vb == null ? -1 : vb - va;
    },
    nome: (a: FavCompany, b: FavCompany) => nomeDi(a, quotes[a.ticker]).localeCompare(nomeDi(b, quotes[b.ticker])),
    data: (a: FavCompany, b: FavCompany) => (b.added_at || '').localeCompare(a.added_at || '') || a.ticker.localeCompare(b.ticker),
  }[opz.ordine];
  return r.slice().sort(per);
}

/** Giudizio Yahoo («buy», «strong_buy», …) nella chiave del catalogo; sconosciuto = testo originale. */
export const chiaveGiudizio = (r?: string | null) => (r || '').trim().toLowerCase().replace(/[\s-]+/g, '_');
