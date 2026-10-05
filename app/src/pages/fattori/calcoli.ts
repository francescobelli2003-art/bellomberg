import { linguaCorrente, localeDi } from '@/i18n/lingua';
import { FATTORI, intervallo } from '@/lib/fattori';
import type { Fattore, Intervallo, Lancetta, PayloadFattori, RigaAlpha } from '@/lib/fattori';

/* Derivazioni di sola presentazione per Fattori di rischio. Le misure restano in lib/fattori.ts:
   qui solo la forma delle viste (heatmap, righello, ordinamento). */

// ── numeri ──────────────────────────────────────────────────────────────────
/** Valore assente = «—» (mai 0); segno meno tipografico «−» come nel resto della Nuova. */
export const VUOTO = '—';
const finito = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const numero = (v: number, dec: number) =>
  Math.abs(v).toLocaleString(localeDi(linguaCorrente()), { minimumFractionDigits: dec, maximumFractionDigits: dec, useGrouping: true });
/** Il segno segue il valore MOSTRATO: −0,001 arrotondato a due decimali è 0,00, senza segno. */
const segno = (v: number, conPiu: boolean, dec: number) => {
  const r = Number(v.toFixed(dec));
  return r < 0 ? '−' : conPiu && r > 0 ? '+' : '';
};
export const cifra = (v: number | null | undefined, dec = 2, conPiu = false) =>
  finito(v) ? segno(v, conPiu, dec) + numero(v, dec) : VUOTO;
export const pct = (v: number | null | undefined, dec = 1, conPiu = false) =>
  finito(v) ? segno(v, conPiu, dec) + numero(v, dec) + '%' : VUOTO;
export const euro = (v: number | null | undefined, dec = 2) =>
  finito(v) ? segno(v, false, dec) + numero(v, dec) + ' €' : VUOTO;

/** Posizione percentuale su un asse simmetrico [−lim, +lim], limitata al riquadro. */
export const suAsse = (v: number, lim: number) => Math.max(0, Math.min(100, 50 + (v / (lim * 2)) * 100));
/** Percentuale per gli attributi di stile: stessa stringa in ogni lingua. */
export const p2 = (v: number) => v.toFixed(2) + '%';

// ── heatmap titoli × fattori ────────────────────────────────────────────────
export interface RigaTitolo extends RigaAlpha {
  nome: string | null;
  /** un intervallo per fattore, nell'ordine di FATTORI; null se il payload non porta il beta */
  celle: Array<Intervallo | null>;
}

export function righeTitoli(fac: PayloadFattori | null, alfa: RigaAlpha[], nomi: Map<string, string>): RigaTitolo[] {
  const ph = fac && fac.per_holding ? fac.per_holding : {};
  return alfa.map(r => {
    const h = ph[r.ticker] || Object.values(ph).find(x => x.ticker === r.ticker) || {};
    return {
      ...r,
      nome: nomi.get(r.ticker) || null,
      celle: FATTORI.map(f => intervallo(h[f], h[f + '_tstat'])),
    };
  });
}

export type Ordine = 'peso' | 'alpha';
/** Per peso (decrescente) o per alpha (decrescente); chi non ha il dato va in fondo, non a zero. */
export function ordina(righe: RigaTitolo[], ordine: Ordine): RigaTitolo[] {
  const chiave = (r: RigaTitolo) => ordine === 'peso' ? r.peso : r.alpha;
  return [...righe].sort((x, y) => {
    const a = chiave(x), b = chiave(y);
    if (a === null && b === null) return x.ticker.localeCompare(y.ticker);
    if (a === null) return 1;
    if (b === null) return -1;
    return b - a;
  });
}

/** Intensità della cella: solo i coefficienti significativi hanno colore. */
export function tinta(ic: Intervallo | null): { alfa: number; segno: 1 | -1 } | null {
  if (!ic || !ic.sig) return null;
  return { alfa: Math.min(0.85, 0.18 + (Math.abs(ic.beta) / 1.6) * 0.67), segno: ic.beta >= 0 ? 1 : -1 };
}

/** Limite simmetrico comune per i baffi dell'alpha: tutte le righe sulla stessa scala. */
export function limiteAlpha(righe: RigaAlpha[]): number {
  return righe.reduce((m, r) => {
    if (!r.ic) return m;
    const lo = r.ic.lo === null ? r.ic.beta : r.ic.lo;
    const hi = r.ic.hi === null ? r.ic.beta : r.ic.hi;
    return Math.max(m, Math.abs(lo), Math.abs(hi));
  }, 1) * 1.05;
}

// ── righello del beta ───────────────────────────────────────────────────────
export interface Righello {
  a0: number; a1: number;
  pc: (v: number) => number;
  tacche: number[];
}

/** Il righello si stringe attorno alle stime: serve a far vedere quanto distano fra loro. */
export function righello(lancette: Lancetta[], extra: Array<number | null> = []): Righello {
  const vals = lancette.map(l => l.valore).concat(extra.filter(finito));
  const lo = vals.length ? Math.min(...vals) : 0;
  const hi = vals.length ? Math.max(...vals) : 1;
  const pad = Math.max((hi - lo) * 0.14, 0.08);
  const a0 = Math.floor((lo - pad) * 10) / 10, a1 = Math.ceil((hi + pad) * 10) / 10;
  const pc = (v: number) => (a1 - a0 > 0 ? ((v - a0) / (a1 - a0)) * 100 : 50);
  const passo = a1 - a0 > 1.2 ? 0.2 : 0.1;
  const tacche: number[] = [];
  for (let t = Math.ceil(a0 / passo - 1e-9) * passo; t <= a1 + 1e-9; t += passo) tacche.push(Number(t.toFixed(2)));
  return { a0, a1, pc, tacche };
}

/** Altezze a scalini per le lancette, nell'ordine del righello: due vicine non si coprono. */
export const QUOTE = [34, 4, 52, 18];

export const CHIAVE_FATTORE: Record<Fattore, 'MKT' | 'SMB' | 'HML' | 'RMW' | 'CMA' | 'MOM'> = {
  beta_market: 'MKT', beta_smb: 'SMB', beta_hml: 'HML', beta_rmw: 'RMW', beta_cma: 'CMA', beta_mom: 'MOM',
};

