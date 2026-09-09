import type { Position } from '@/lib/api';

/* P&L DAILY dai campi backend (contratto (28) 23/07): prev_close in VALUTA DI
   QUOTAZIONE, fx_to_eur gia' con GBX dentro. Campo assente (backend vecchio)
   o null = n.d. DICHIARATO — mai 0. Formula UNICA condivisa F1/F2. */

export const dayPct = (p: Position) => (p.prezzo_live != null && p.prev_close != null && p.prev_close > 0)
  ? (p.prezzo_live / p.prev_close - 1) * 100 : null;

export const dayEur = (p: Position) => (p.prezzo_live != null && p.prev_close != null && p.fx_to_eur != null)
  ? (p.prezzo_live - p.prev_close) * (p.quantita || 0) * p.fx_to_eur : null;

/* P&L GG dell'INTERO portafoglio (chiarimento PM 23/07): la % SOLO a copertura
   completa, sul valore investito di ieri — mai una % calcolata su un pezzo di
   book. dayTot con posizioni scoperte = somma PARZIALE, flag dichiarato. */
export function computeDailyPnl(positions: Position[]): {
  dayTot: number | null; dayPartial: boolean; dayTotPct: number | null;
} {
  const vals = positions.map(dayEur).filter((v): v is number => v != null);
  const dayTot = vals.length ? vals.reduce((s, v) => s + v, 0) : null;
  const dayPartial = vals.length > 0 && vals.length < positions.length;
  const prevInvested = positions.reduce((s, p) => {
    const v = (p.prev_close != null && p.fx_to_eur != null) ? p.prev_close * (p.quantita || 0) * p.fx_to_eur : null;
    return v != null ? s + v : s;
  }, 0);
  const dayTotPct = (dayTot != null && !dayPartial && prevInvested > 0) ? (dayTot / prevInvested) * 100 : null;
  return { dayTot, dayPartial, dayTotPct };
}
