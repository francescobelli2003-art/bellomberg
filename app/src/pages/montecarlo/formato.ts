import { t as tr, type Chiave } from '@/i18n/t';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import type { MonteCarloResult } from '@/lib/api';
import { fmtEUR as fmtEURlib, fmtPct as fmtPctlib, fmtNum as fmtNumlib } from '@/lib/format';

// B-UI15: logica di formato UNICA in lib/format; qui solo la convenzione locale della
// pagina — EUR a 0 decimali, numeri nella lingua corrente. Il buco lo dichiara la pagina
// col segnaposto della lingua della UI: lib/format stampa un 'n/a' fisso.
// `na`: il segnaposto da stampare al posto del buco; di norma «n.d.» (campo mancante), ma
// con lo scenario deterministico le metriche statistiche sono null PER COSTRUZIONE e si dice
// perché (vedi `naDi`).
export const naOr = (v: number | null | undefined, f: (x: number) => string, na?: string) =>
  v == null || !isFinite(v) ? (na ?? tr('montecarlo.na')) : f(v);
export const fmtEUR = (v: number | null | undefined, na?: string) => naOr(v, x => fmtEURlib(x, false, 0), na);
export const fmtEURS = (v: number | null | undefined, na?: string) => naOr(v, x => fmtEURlib(x, true, 0), na);
export const fmtEUR2 = (v: number | null | undefined, na?: string) => naOr(v, x => fmtEURlib(x, false, 2), na);
export const fmtPct = (v: number | null | undefined, na?: string) => naOr(v, x => fmtPctlib(x, false), na);
export const fmtPctS = (v: number | null | undefined, na?: string) => naOr(v, x => fmtPctlib(x, true), na);
export const fmtNum = (v: number | null | undefined, dec = 2, na?: string) => naOr(v, x => fmtNumlib(x, dec), na);
export const fmtInt = (v: number | null | undefined) =>
  naOr(v, x => Math.round(x).toLocaleString(localeDi(linguaCorrente()), { useGrouping: true }));

/** Scenario deterministico (replay che copre tutto l'orizzonte): lo dice il motore, col
 *  blocco `deterministic_scenario`; la pagina non lo deduce da una varianza nulla. */
export const deterministico = (r: MonteCarloResult | null | undefined) =>
  r?.stress_nature === 'deterministic' || r?.deterministic_scenario != null;

/** Segnaposto di un campo del payload: «n.a. (scenario deterministico)» se il motore lo
 *  elenca fra le metriche non applicabili, altrimenti undefined (= il «n.d.» di sempre).
 *  Solo i campi ELENCATI: un buco qualunque resta un buco, non diventa «non applicabile». */
export const naDi = (r: MonteCarloResult | null | undefined, campo: string): string | undefined =>
  r?.deterministic_scenario?.metrics_not_applicable?.includes(campo) ? tr('montecarlo.naDeterministic') : undefined;

/** Timbro d'esecuzione della simulazione: «5 ott 2026 · 19:27». Con l'anno (review): una
 *  simulazione in cache può venire da un altro anno, e senza l'anno la data mentirebbe. Mai
 *  inventato: se il payload non porta il timestamp, si dichiara. */
export const stamp = (iso?: string) => {
  const d = iso ? new Date(iso) : null;
  if (!d || isNaN(d.getTime())) return tr('montecarlo.stampMissing');
  const loc = localeDi(linguaCorrente());
  const day = d.toLocaleDateString(loc, { day: 'numeric', month: 'short', year: 'numeric' }).replace('.', '');
  const time = d.toLocaleTimeString(loc, { hour: '2-digit', minute: '2-digit', hour12: false });
  return `${day} · ${time}`;
};

const ORIZZONTI = [21, 63, 126, 252, 504];
/** L'orizzonte in parole («1 anno»); un numero di giorni fuori dai cinque del selettore si
 *  dice in giorni, non si arrotonda a un'etichetta vicina. */
export const orizzonte = (days?: number | null) =>
  days == null ? tr('montecarlo.na')
    : ORIZZONTI.includes(days) ? tr(`montecarlo.horizonLong_${days}` as Chiave)
      : tr('montecarlo.horizonDays', { d: days });

/** Id del motore (metodo, drift, stress) -> la stessa etichetta dei selettori della pagina.
 *  Un id senza etichetta si mostra com'è e si dichiara: mai un nome inventato. */
export const etichettaMotore = (kind: 'method' | 'drift' | 'stress', id?: string | null): string => {
  if (!id) return tr('montecarlo.na');
  const labels: Record<string, string> = kind === 'method'
    ? { fhs: tr('montecarlo.methodFhs'), block_bootstrap: 'Block bootstrap', parametric_t: tr('montecarlo.methodParam') }
    : kind === 'drift'
      ? { zero: tr('montecarlo.driftZero'), shrinkage: tr('montecarlo.driftShrink'), historical: tr('montecarlo.driftHist') }
      : { none: tr('montecarlo.stressNone'), gfc_2008: 'GFC 2008', covid_2020: 'COVID 2020', shock_3sigma: tr('montecarlo.stressShock') };
  return Object.prototype.hasOwnProperty.call(labels, id) ? labels[id] : tr('montecarlo.unlabelledId', { id });
};
