// MACRO PULSE personalizzabile (richiesta PM 23/07): strumenti scelti dal PM
// da F15 col bottone "+ MACRO PULSE", persistiti in localStorage (preferenza
// locale del terminale — niente backend, dichiarato). La F1 li quota via
// /market/quote accanto ai pick fissi del feed overview.
export interface PulsePick { ticker: string; name: string }

const KEY = 'bb:pulseCustom';

export function getPulsePicks(): PulsePick[] {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || '[]');
    return Array.isArray(v) ? v.filter(p => p && typeof p.ticker === 'string' && p.ticker) : [];
  } catch { return []; }
}

export function isInPulse(ticker: string): boolean {
  return getPulsePicks().some(p => p.ticker === ticker);
}

/** Aggiunge/toglie; ritorna true se ORA è dentro. */
export function togglePulse(ticker: string, name?: string): boolean {
  const cur = getPulsePicks();
  const has = cur.some(p => p.ticker === ticker);
  const next = has ? cur.filter(p => p.ticker !== ticker) : [...cur, { ticker, name: name || ticker }];
  try { localStorage.setItem(KEY, JSON.stringify(next)); } catch {}
  return !has;
}
