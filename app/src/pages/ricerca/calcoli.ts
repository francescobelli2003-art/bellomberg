// Derivazioni pure della pagina Ricerca opportunità: nessun fetch, nessun orologio.
import { oraIt, type Segnale } from '@/lib/edge';
import { parole, type ChiavePagina } from './parole';
import type { Diagnosi } from './VistaRicerca';

/** L'ordine fisso delle categorie del motore dei segnali (signal_engine). */
export const CATEGORIE = ['momentum', 'factor', 'volatility', 'positioning', 'insider', 'risk'] as const;

/** Le chiavi dei segnali di una scansione, nello stesso ordine. Nascono dal CONTENUTO, mai dalla
 *  posizione in lista: dopo una nuova scansione lo stesso segnale ritrova la stessa chiave anche se
 *  altri entrano, escono o cambiano forza. Il backend non dà un id (`signal_engine._sig`), quindi:
 *  titolo | categoria | rilevatore. Il nome no: arriva tradotto e cambierebbe con la lingua.
 *  Un rilevatore dà al più un segnale per titolo, quindi di norma la chiave è già unica; quando il
 *  rilevatore non si riconosce (null) o la regola non regge, il suffisso `#n` conta le occorrenze
 *  della stessa chiave di contenuto nell'ordine del backend: deterministico, e tocca solo i gemelli. */
export function chiaviSegnali(segnali: readonly Segnale[]): string[] {
  const visti = new Map<string, number>();
  return segnali.map(s => {
    const base = `${s.ticker}|${s.category}|${rilevatoreDi(s) ?? '?'}`;
    const n = visti.get(base) ?? 0;
    visti.set(base, n + 1);
    return `${base}#${n}`;
  });
}

/** Quale rilevatore ha prodotto il segnale. Il nome arriva tradotto dal backend, quindi si
 *  riconosce da categoria, direzione e forma del valore (come li scrive `signal_engine._sig`),
 *  mai dal testo: una forma sconosciuta torna null e la pagina non spiega nulla. */
export type Rilevatore = 'zscore' | 'alpha' | 'beta' | 'vrp' | 'rvol' | 'em' | 'gex' | 'cbuy' | 'csell';
export function rilevatoreDi(s: Segnale): Rilevatore | null {
  const v = String(s.value ?? '').trim();
  switch (s.category) {
    case 'momentum': return 'zscore';
    case 'positioning': return 'gex';
    case 'insider': return s.direction === 'bullish' ? 'cbuy' : s.direction === 'bearish' ? 'csell' : null;
    case 'factor': return v.startsWith('β') ? 'beta' : /^t\s/.test(v) ? 'alpha' : null;
    // VRP: dal 10/10 il backend scrive il RAPPORTO IV/RV («1.85x»); «pt» resta per i payload vecchi
    case 'volatility': return v.endsWith('pt') || /\dx$/.test(v) ? 'vrp' : v.startsWith('±') ? 'em' : /\d/.test(v) ? 'rvol' : null;
    default: return null;
  }
}
export const SPIEGAZIONE: Record<Rilevatore, [ChiavePagina, ChiavePagina]> = {
  zscore: ['edge.det_zscore', 'edge.det_zscore_f'],
  alpha: ['edge.det_alpha', 'edge.det_alpha_f'],
  beta: ['edge.det_beta', 'edge.det_beta_f'],
  vrp: ['edge.det_vrp', 'edge.det_vrp_f'],
  rvol: ['edge.det_rvol', 'edge.det_rvol_f'],
  em: ['edge.det_em', 'edge.det_em_f'],
  gex: ['edge.det_gex', 'edge.det_gex_f'],
  cbuy: ['edge.det_cbuy', 'edge.det_cbuy_f'],
  csell: ['edge.det_csell', 'edge.det_csell_f'],
};

/** Il livello a parole di una forza 0-100 (le stesse soglie dei bottoni). */
export function livelloForza(f: number): ChiavePagina {
  if (f >= 75) return 'edge.lvlVeryStrong';
  if (f >= 60) return 'edge.lvlStrong';
  if (f >= 45) return 'edge.lvlModerate';
  return 'edge.lvlWeak';
}

/** L'etichetta corta di un marcatore della mappa: il simbolo senza suffisso di borsa. */
export function etichettaMappa(ticker: string): string {
  return ticker.split('.')[0].slice(0, 6);
}

/** Le file dei marcatori di una corsia: in ordine di forza, ciascuno nella prima fila dove non
 *  tocca il precedente. Lo spazio di un'etichetta si stima in punti di forza dalla sua lunghezza
 *  (≈ larghezza della corsia sotto i 1180 px di contenuto), niente misure del DOM. */
export function fileMarcatori<T extends { strength: number; ticker: string }>(ss: T[]): { s: T; fila: number }[] {
  const fine: number[] = [];
  return [...ss].sort((a, b) => a.strength - b.strength).map(s => {
    const mezzo = (etichettaMappa(s.ticker).length * 1.1 + 3) / 2;
    const da = s.strength - mezzo;
    let fila = fine.findIndex(f => da > f);
    if (fila < 0) { fila = fine.length; fine.push(0); }
    fine[fila] = s.strength + mezzo;
    return { s, fila };
  });
}

/** Il grado di copertura di un titolo, per la striscia e il dialogo. */
export type Grado = 'piena' | 'solo' | 'muto' | 'nessuna' | 'non';

/** Posizione 0-100 di un punteggio netto sulla scala simmetrica della diagnosi. */
export function scalaDiagnosi(score: number): { pos: number; ampiezza: number } {
  const ampiezza = Math.max(2, Math.ceil(Math.abs(score)));
  return { pos: 50 + (Math.max(-ampiezza, Math.min(ampiezza, score)) / ampiezza) * 50, ampiezza };
}

/** Il nome di una categoria nella lingua corrente; Momentum e Insider sono uguali in IT e EN. */
export function nomeCategoria(c: string, t: (k: ChiavePagina) => string): string {
  switch (c) {
    case 'volatility': return t('edge.f001');
    case 'positioning': return t('edge.categoryPositioning');
    case 'factor': return t('edge.f002');
    case 'risk': return t('edge.f003');
    case 'momentum': return 'Momentum';
    case 'insider': return 'Insider';
    default: return c;
  }
}

function numeroFinito(x: unknown): x is number {
  return typeof x === 'number' && Number.isFinite(x);
}

/** Le soglie del verdetto dichiarate dal backend (position_doctor.thresholds, fonte unica
 *  core/soglie_score.DOCTOR_LIMITE). Assenti o incoerenti = null: la pagina lo dice, non ne
 *  inventa una copia. */
export function leggiSoglieDiagnosi(x: unknown): { limite: number; piena: number } | null {
  const o = x && typeof x === 'object' && !Array.isArray(x) ? x as Record<string, unknown> : null;
  if (!o || !numeroFinito(o.hold_borderline_abs) || !numeroFinito(o.full_recommendation_abs)) return null;
  const limite = o.hold_borderline_abs, piena = o.full_recommendation_abs;
  return limite > 0 && piena >= limite ? { limite, piena } : null;
}

/** La risposta di GET /signals/position_doctor/{ticker} → lo stato del box diagnosi.
 *  Niente valori di ripiego zitti: un `{"error"}` è un guasto col SUO motivo, un verdetto
 *  mancante si dichiara n.d., un conteggio non numerico resta null (non 0). */
export function leggiDiagnosi(p: unknown, ticker: string): Diagnosi {
  const { t } = parole();
  const o = p && typeof p === 'object' && !Array.isArray(p) ? p as Record<string, unknown> : null;
  if (o && typeof o.error === 'string' && o.error.trim()) return { ticker, stato: 'errore', motivo: o.error };
  if (!o || !numeroFinito(o.net_score)) return { ticker, stato: 'errore', motivo: t('edge.diagShape') };
  return {
    ticker, stato: 'ok', score: o.net_score,
    verdetto: typeof o.verdict === 'string' && o.verdict.trim() ? o.verdict : t('edge.diagVerdictNd'),
    nota: typeof o.verdict_nota === 'string' ? o.verdict_nota : '',
    nSegnali: numeroFinito(o.n_signals) ? o.n_signals : null,
    soglie: leggiSoglieDiagnosi(o.thresholds),
    alle: oraIt(typeof o._timestamp === 'string' ? o._timestamp : ''),
  };
}
