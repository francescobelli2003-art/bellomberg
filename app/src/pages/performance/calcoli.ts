/** Calcoli puri della pagina Performance (nessun React, nessuna rete). */
export type Periodo = '1S' | '1M' | '3M' | 'YTD' | 'Tutto';
export const PERIODI: readonly Periodo[] = ['1S', '1M', '3M', 'YTD', 'Tutto'];
export type PeriodoAttribuzione = '1W' | '30D' | '3M' | 'YTD' | 'INCEPTION';
const MAPPA: Record<Periodo, PeriodoAttribuzione> = { '1S': '1W', '1M': '30D', '3M': '3M', YTD: 'YTD', Tutto: 'INCEPTION' };
export const periodoAttribuzione = (p: Periodo): PeriodoAttribuzione => MAPPA[p];

const finito = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
/** Giorni di calendario PRIMA della fine inclusi nella finestra: stessi confini del backend (attribution _period_start). */
const INDIETRO: Partial<Record<Periodo, number>> = { '1S': 6, '1M': 29, '3M': 89 };

/** Indice della base del periodo: l'ultima seduta strettamente prima del primo giorno
 *  della finestra (il suo valore è la base del rendimento). 0 se la storia è più corta. */
export function indiceBase(date: string[], p: Periodo): number {
  const n = date.length;
  if (n === 0 || p === 'Tutto') return 0;
  const fine = date[n - 1];
  let inizio: string;
  if (p === 'YTD') inizio = fine.slice(0, 4) + '-01-01';
  else {
    const d = new Date(fine + 'T00:00:00Z');
    d.setUTCDate(d.getUTCDate() - (INDIETRO[p] as number));
    inizio = d.toISOString().slice(0, 10);
  }
  let base = 0;
  for (let i = 0; i < n && date[i] < inizio; i++) base = i;
  return base;
}

export interface StatPeriodo { rendimentoPct: number | null; maxDdPct: number | null; ddOggiPct: number | null; sharpe: number | null; nRendimenti: number }
/** Sharpe con la convenzione del backend (twr_engine._twr_metrics): (CAGR − rf) / volatilità annua,
 *  CAGR sullo span di calendario, varianza campionaria; solo con almeno 20 rendimenti e 20 giorni.
 *  Drawdown misurati dal picco DENTRO il periodo, cosi' riquadro e fascia sotto il grafico coincidono. */
export function statistichePeriodo(indice: number[], base: number, rfAnnuo: number, date: string[] = []): StatPeriodo {
  const b = Math.max(0, base);
  const s = indice.slice(b), d = date.slice(b);
  if (s.length < 2 || !s.every(finito) || !(s[0] > 0)) return { rendimentoPct: null, maxDdPct: null, ddOggiPct: null, sharpe: null, nRendimenti: 0 };
  const rendimentoPct = (s[s.length - 1] / s[0] - 1) * 100;
  let picco = s[0], dd = 0, oggi = 0;
  for (const v of s) { if (v > picco) picco = v; oggi = (v / picco - 1) * 100; dd = Math.min(dd, oggi); }
  const r: number[] = [];
  for (let i = 1; i < s.length; i++) if (s[i - 1] > 0) r.push(s[i] / s[i - 1] - 1);
  let sharpe: number | null = null;
  const giorni = d.length === s.length && d[0] && d[d.length - 1] ? (Date.parse(d[d.length - 1] + 'T00:00:00Z') - Date.parse(d[0] + 'T00:00:00Z')) / 864e5 : NaN;
  if (r.length >= 20 && giorni >= 20) {
    const m = r.reduce((a, x) => a + x, 0) / r.length;
    const vol = Math.sqrt(r.reduce((a, x) => a + (x - m) ** 2, 0) / (r.length - 1)) * Math.sqrt(252);
    const cagr = Math.pow(s[s.length - 1] / s[0], 365.25 / giorni) - 1;
    if (vol > 0) sharpe = (cagr - rfAnnuo) / vol;
  }
  return { rendimentoPct, maxDdPct: dd, ddOggiPct: oggi, sharpe, nRendimenti: r.length };
}

/** Rendimento % dall'ultimo valore con data <= `da` all'ultimo valore. null se la serie parte dopo. */
export function rendimentoAllaData(date: string[], valori: number[], da: string): number | null {
  const n = Math.min(date.length, valori.length);
  let b = -1;
  for (let i = 0; i < n && date[i] <= da; i++) b = i;
  if (b < 0 || n < 1 || !finito(valori[b]) || !(valori[b] > 0) || !finito(valori[n - 1])) return null;
  return (valori[n - 1] / valori[b] - 1) * 100;
}

export function sottAcqua(indice: number[]): number[] {
  let picco = -Infinity;
  return indice.map(v => { if (v > picco) picco = v; return picco > 0 ? (v / picco - 1) * 100 : 0; });
}

export function mediaMobile(valori: number[], n: number): (number | null)[] {
  let somma = 0;
  return valori.map((v, i) => { somma += v; if (i >= n) somma -= valori[i - n]; return i >= n - 1 ? somma / n : null; });
}

export type AnnoMensile = { anno: string; celle: (number | null)[]; ytd: number | null };
/** TWR mensile (fine mese / fine mese precedente − 1); YTD sulla fine dell'anno precedente. */
export function rendimentiMensili(date: string[], valori: number[], inizioRichiesto?: string): { anni: AnnoMensile[]; ultimoMese: string } | null {
  const n = Math.min(date.length, valori.length);
  if (n < 2) return null;
  /** serie che parte dopo l'inizio richiesto (es. SPY dopo la base TWR): il primo mese e l'anno non sono misurati */
  const tardiva = !!inizioRichiesto && date[0] > inizioRichiesto;
  const ultimo = new Map<string, number>(); const ordine: string[] = [];
  for (let i = 0; i < n; i++) { const ym = date[i].slice(0, 7); if (!ultimo.has(ym)) ordine.push(ym); ultimo.set(ym, valori[i]); }
  const anni: AnnoMensile[] = [];
  let prec = valori[0], inizioAnno = valori[0], cur: AnnoMensile | null = null;
  for (const ym of ordine) {
    const [y, m] = ym.split('-');
    if (!cur || cur.anno !== y) {
      if (cur) { cur.ytd = inizioAnno > 0 ? (prec / inizioAnno - 1) * 100 : null; inizioAnno = prec; }
      cur = { anno: y, celle: Array(12).fill(null), ytd: null }; anni.push(cur);
    }
    const v = ultimo.get(ym)!;
    cur.celle[parseInt(m, 10) - 1] = prec > 0 ? (v / prec - 1) * 100 : null;
    prec = v;
  }
  if (cur) cur.ytd = inizioAnno > 0 ? (prec / inizioAnno - 1) * 100 : null;
  if (tardiva && anni.length) {
    anni[0].celle[parseInt(ordine[0].slice(5, 7), 10) - 1] = null;
    anni[0].ytd = null;
  }
  return { anni, ultimoMese: ordine[ordine.length - 1] };
}

export interface Seduta { data: string; eur: number }
/** Sedute misurate + quelle escluse perché il flusso del giorno manca (regola 14/07: un flusso
 *  mancante NON vale 0, altrimenti un versamento diventa P&L di mercato). `disallineata` = la serie
 *  dei flussi non ha la stessa lunghezza delle date: nessun giorno è attribuibile, serie n.d. */
export interface PnlGiornaliero { sedute: Seduta[]; senzaFlusso: string[]; disallineata: boolean }
/** P&L giornaliero in euro dal rendimento dell'indice TWR: pnl_t = (V_t − F_t) · r_t / (1 + r_t), con
 *  r_t = I_t / I_{t−1} − 1. Coincide con V_t − V_{t−1} − F_t nei giorni normali (twr_engine.compute_twr) e
 *  vale 0 sul salto ricostruita → ufficiale, dove il backend fissa r = 0 (lo snapshot include la cassa). */
export function pnlGiornalieri(date: string[], valori: number[], flussi: number[] | null | undefined, indice: number[]): PnlGiornaliero {
  if (!Array.isArray(flussi) || flussi.length !== date.length) return { sedute: [], senzaFlusso: [], disallineata: true };
  const out: Seduta[] = [], senzaFlusso: string[] = [];
  const n = Math.min(date.length, valori.length, indice.length);
  for (let i = 1; i < n; i++) {
    if (!finito(valori[i]) || !finito(indice[i]) || !finito(indice[i - 1]) || !(indice[i - 1] > 0)) continue;
    if (!finito(flussi[i])) { senzaFlusso.push(date[i]); continue; }
    const f = flussi[i];
    const r = indice[i] / indice[i - 1] - 1;
    out.push({ data: date[i], eur: (valori[i] - f) * r / (1 + r) });
  }
  return { sedute: out, senzaFlusso, disallineata: false };
}

/** P&L in euro del periodo: somma del P&L giornaliero (pnlGiornalieri) sulle sedute DOPO la base,
 *  cosi' euro e % del riquadro misurano la stessa finestra. Flussi disallineati o nessuna seduta = null;
 *  i giorni senza flusso restano fuori e si contano in `esclusi` (mai 0 inventato, regola 14/07). */
export interface PnlPeriodo { eur: number | null; esclusi: number }
export function pnlPeriodo(date: string[], valori: number[], flussi: number[] | null | undefined, indice: number[], base: number): PnlPeriodo {
  const p = pnlGiornalieri(date, valori, flussi, indice);
  const da = date[Math.max(0, base)];
  if (p.disallineata || !da) return { eur: null, esclusi: 0 };
  const dentro = p.sedute.filter(s => s.data > da);
  return { eur: dentro.length ? dentro.reduce((a, s) => a + s.eur, 0) : null, esclusi: p.senzaFlusso.filter(d => d > da).length };
}

/** P&L totale dall'inizio = non realizzato + realizzato + dividendi; una voce assente lo rende n.d. */
export function pnlTotale(nonRealizzato: number | null, realizzato: number | null, dividendi: number | null): number | null {
  return finito(nonRealizzato) && finito(realizzato) && finito(dividendi) ? nonRealizzato + realizzato + dividendi : null;
}

/** Richieste lunghe per chiave (scenari Monte Carlo): una sola in volo per chiave, e conta solo la risposta
 *  dell'ultima aperta. `apri` restituisce il numero della richiesta, o null se una e' gia' in volo e non si forza. */
export class RichiesteUltime<K> {
  private ultima = new Map<K, number>();
  private aperte = new Set<K>();
  private contatore = 0;
  inVolo(k: K): boolean { return this.aperte.has(k); }
  apri(k: K, forza = false): number | null {
    if (this.aperte.has(k) && !forza) return null;
    const n = ++this.contatore;
    this.ultima.set(k, n); this.aperte.add(k);
    return n;
  }
  /** true se `n` e' l'ultima richiesta per `k` (la risposta va usata); chiude la chiave. */
  chiudi(k: K, n: number | null): boolean {
    if (n == null || this.ultima.get(k) !== n) return false;
    this.aperte.delete(k);
    return true;
  }
}

export interface Classe { da: number; a: number; n: number }
const PASSI = [5, 10, 20, 25, 50, 100, 200, 250, 500, 1000];
/** Classi contigue di larghezza `passo` (se assente: circa 24 classi, passo «tondo»). */
export function istogramma(valori: number[], passo?: number): { classi: Classe[]; passo: number } {
  const v = valori.filter(finito);
  if (!v.length) return { classi: [], passo: passo ?? 25 };
  const lo = Math.min(...v), hi = Math.max(...v);
  const p = passo ?? (PASSI.find(x => (hi - lo) / x <= 24) ?? 1000);
  const da = Math.floor(lo / p) * p, a = Math.floor(hi / p) * p + p;
  const classi: Classe[] = [];
  for (let x = da; x < a; x += p) classi.push({ da: x, a: x + p, n: 0 });
  for (const x of v) classi[Math.min(classi.length - 1, Math.floor((x - da) / p))].n++;
  return { classi, passo: p };
}

/** oltreVar = null quando il VaR manca: il conteggio non è misurabile, mai 0 inventato. */
export interface Sintesi { sedute: number; oltreVar: number | null; attese: number; peggiore: Seduta | null; migliore: Seduta | null }
/** var95Eur: soglia di perdita POSITIVA (166 = −166 €). */
export function sintesiDistribuzione(sedute: Seduta[], var95Eur: number | null): Sintesi {
  let peggiore: Seduta | null = null, migliore: Seduta | null = null;
  for (const s of sedute) { if (!peggiore || s.eur < peggiore.eur) peggiore = s; if (!migliore || s.eur > migliore.eur) migliore = s; }
  return {
    sedute: sedute.length,
    oltreVar: !finito(var95Eur) ? null : sedute.filter(s => s.eur <= -Math.abs(var95Eur)).length,
    attese: Math.round(sedute.length * 0.05),
    peggiore, migliore,
  };
}
