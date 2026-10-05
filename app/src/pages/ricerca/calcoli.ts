// Derivazioni pure della pagina Ricerca opportunità: nessun fetch, nessun orologio.
import type { Segnale } from '@/lib/edge';
import type { ChiavePagina } from './parole';

/** L'ordine fisso delle categorie del motore dei segnali (signal_engine). */
export const CATEGORIE = ['momentum', 'factor', 'volatility', 'positioning', 'insider', 'risk'] as const;

/** Chiave stabile di un segnale nella scansione: un rilevatore dà al più un segnale per titolo. */
export function chiaveSegnale(s: Segnale, i: number): string {
  return `${s.ticker}|${s.category}|${s.name}|${i}`;
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
    case 'volatility': return v.endsWith('pt') ? 'vrp' : v.startsWith('±') ? 'em' : /\d/.test(v) ? 'rvol' : null;
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
