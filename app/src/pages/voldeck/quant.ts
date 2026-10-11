import * as vq from '@/lib/vol-quant';
import { finite, type OptionContract, type SurfaceModel, type SurfaceRow } from '@/lib/vol-deck';
import type { SavedSnapshot } from '@/lib/vol-snapshots';

/* ============================================================================
   Cablaggio del Vol Deck «quant» (10/10/2026, Opus 5.5). NESSUNA MATEMATICA QUI: ogni numero
   viene da lib/vol-quant (forward implicito de-americanizzato, griglia delta, skew, vol forward,
   arbitraggi, ΔIV, vol d'evento, cono). Questo file sceglie gli INPUT (payload della superficie,
   chain del job, tasso FRED, istantanea precedente) e rimette i risultati sulle coordinate della
   pagina (righe del modello × colonne K/S). Un dato che manca resta mancante col suo motivo.
   v2 (libreria v2, 26ea10b): tasso continuo da DGS3MO (rateFromDgs3mo sul percento), griglia delta
   sulla superficie OSSERVATA (default della libreria), arbitraggi anche in prezzo eseguibile, ΔIV a
   K/F quando entrambe le istantanee hanno i forward, eventVol con la data dell'istantanea, cono
   lognormale.
   ========================================================================== */

export type ChainStatus = 'ok' | 'loading' | 'error' | 'none';
/** Risposta di /options/strategy/rate: `percent` = DGS3MO in percento bond-equivalent. */
export interface RateInfo { value: number | null; percent: number | null; status: string | null; date: string | null; source: string | null; error: string | null }
export interface QuantInput {
  data: any; model: SurfaceModel;
  chains: Record<string, { status: ChainStatus; chain: OptionContract[] | null } | undefined>;
  rate: RateInfo | null; previous: SavedSnapshot | null;
}
/** motivo per cui una scadenza non ha forward: codice della libreria o stato della chain */
export type ForwardWhy = vq.ReasonCode | 'no_download' | 'chain_loading' | 'chain_error' | 'rate_loading';
/** Due livelli (regola coordinatore 10/10): «eseguibile» = provato su bid/ask, e' un arbitraggio;
 *  «indicativo» = sul modello (IV ± rumore, densita'): dentro lo spread non e' eseguibile. */
export type FlagLevel = 'executable' | 'indicative';
export interface FlagItem { id: number; flag: vq.ArbitrageFlag; set: 'grid' | 'observed'; level: FlagLevel; cells: { expiry: string; col: number }[]; ratio: number | null }
/** Livello di un flag: «eseguibile» solo se provato su bid/ask (test 'executable'); ogni segnale di modello
 *  (calendario, butterfly/verticale senza quote, densita') e' indicativo. Libreria v3: `indicative: true`
 *  marca i segnali di modello su nodi che HANNO bid/ask (fa fede il test eseguibile): restano indicativi e
 *  la lettura lo dice (quantText.flagLine). Un `indicative: true` su un test eseguibile non e' atteso: in
 *  quel caso vince la cautela (indicativo). */
export function levelOf(f: vq.ArbitrageFlag): FlagLevel {
  // alla giunzione put/call le IV del fornitore sono calcolate a parte: anche su bid/ask resta indicativo (review 10/10)
  return f.test === 'executable' && f.indicative !== true && f.origin !== 'junction' ? 'executable' : 'indicative';
}
export interface VolQuant {
  T: Record<string, number | null>;
  /** UN SOLO ATM per la pagina (review 10/10): l'ATMF della griglia delta della libreria, per scadenza (null = n.d.) */
  atmf: Record<string, number | null>;
  /** tasso continuo decimale usato (null = n.d.) */
  r: number | null;
  forwards: Record<string, vq.ForwardResult>;
  forwardOf: (expiry: string) => { F: number | null; why: ForwardWhy | null };
  delta: vq.DeltaGrid; skew: vq.SkewRow[];
  term: vq.TermPoint[]; fwdVol: vq.ForwardVolPair[];
  /** null = nessuna data utili nel payload (n.d., niente ripiego) */
  event: vq.EventVol | null;
  cone: vq.ConeRow[];
  arb: vq.ArbitrageReport; arbObserved: vq.ArbitrageReport | null;
  flags: FlagItem[]; cellFlags: Map<string, number[]>;
  /** livello piu' forte per cella (un eseguibile vince su un indicativo) */
  cellLevel: Map<string, FlagLevel>;
  nExecutable: number; nIndicative: number;
  diff: vq.SurfaceDiff | null; diffCells: (number | null)[][] | null; diffReasons: (vq.ReasonCode | null)[][] | null; diffRange: number | null;
  /** scivolamento sullo skew e resto del diff, sulle stesse celle */
  diffSlide: (number | null)[][] | null; diffExSlide: (number | null)[][] | null;
  deltaModel: SurfaceModel; deltaHeads: string[];
}

export const DELTA_HEADS: Record<vq.DeltaKey, string> = { '10P': '10ΔP', '25P': '25ΔP', ATMF: 'ATMF', '25C': '25ΔC', '10C': '10ΔC' };
export const ATMF_HEAD = DELTA_HEADS.ATMF;
export const cellKey = (expiry: string, col: number) => expiry + '|' + col;

/** Payload visibile ristretto alle righe del modello (0–1 DTE e scadenze nascoste fuori). */
function visibleSlices(data: any, model: SurfaceModel): any {
  const keep = new Set(model.rows.map(r => r.expiry));
  return { ...data, slices: (Array.isArray(data?.slices) ? data.slices : []).filter((s: any) => keep.has(s?.expiry)) };
}

/** Gravita' per ordinare flag di unita' diverse: quante volte la tolleranza (stessa unita' dell'entita',
 *  libreria v2). I flag eseguibili (tolleranza 0: lo spread e' gia' pagato) non hanno rapporto: vanno in testa. */
const ratioOf = (f: vq.ArbitrageFlag) => {
  const sev = (f as vq.ArbitrageFlag & { severity?: unknown }).severity;
  if (typeof sev === 'number' && Number.isFinite(sev)) return sev;  // v3: gravita' della libreria
  return f.tolerance > 0 ? f.magnitude / f.tolerance : null;
};

/** Colonne del modello toccate da un flag: strike della griglia dentro [min, max] degli strike del
 *  flag per quella scadenza; se nessuna cade dentro, la colonna piu' vicina al centro. */
function flagCells(flag: vq.ArbitrageFlag, model: SurfaceModel): { expiry: string; col: number }[] {
  if (model.spot == null) return [];
  const out: { expiry: string; col: number }[] = [];
  flag.expiries.forEach((expiry, n) => {
    if (!model.rows.some(r => r.expiry === expiry)) return;
    const ks = flag.kind === 'calendar' && flag.strikes.length === 4 ? flag.strikes.slice(n * 2, n * 2 + 2) : flag.strikes;
    if (!ks.length) return;
    // un flag = un segno per scadenza: la colonna piu' vicina al centro dei suoi strike (mai una fila di cerchi)
    const mid = (Math.min(...ks) + Math.max(...ks)) / 2;
    let best = -1, d = Infinity;
    model.strikes.forEach((k, i) => { if (k != null && Math.abs(k - mid) < d) { d = Math.abs(k - mid); best = i; } });
    if (best >= 0) out.push({ expiry, col: best });
  });
  return out;
}

/** Data (New York) dell'istantanea per eventVol: la seduta dichiarata, altrimenti il giorno del timestamp. */
export const snapshotDay = (data: any): string | null => {
  const s = data?.market_session?.session_date ?? data?.snapshot_at ?? data?._timestamp;
  return typeof s === 'string' && s.trim() ? s : null;
};

export function volQuant({ data, model, chains, rate, previous }: QuantInput): VolQuant {
  const visible = visibleSlices(data, model);
  const surface = vq.surfaceFromBuilder(visible);
  const T: Record<string, number | null> = Object.fromEntries(surface.slices.map(s => [s.expiry, s.T]));
  const r = rate ? vq.rateFromDgs3mo(rate.percent) : null;
  const inputs: vq.ForwardInput[] = model.rows.flatMap(row => {
    const c = chains[row.expiry];
    return c?.status === 'ok' && c.chain ? [{ expiry: row.expiry, T: T[row.expiry] ?? null, r, contracts: c.chain, spot: model.spot }] : [];
  });
  const forwards = vq.impliedForward(inputs);
  const forwardOf = (expiry: string): { F: number | null; why: ForwardWhy | null } => {
    if (forwards[expiry]) {
      const fi = vq.forwardInfo(forwards, expiry);
      return { F: fi.F, why: fi.F == null ? (fi.reason === 'rate_missing' && !rate ? 'rate_loading' : fi.reason) : null };
    }
    const c = chains[expiry];
    return { F: null, why: !c || c.status === 'none' ? 'no_download' : c.status === 'loading' ? 'chain_loading' : 'chain_error' };
  };
  // superficie OSSERVATA (quote OTM della chain a giunzione K = F): la fonte predefinita della griglia delta
  const observed = vq.surfaceFromChains(inputs.map(i => ({ expiry: i.expiry, T: i.T, contracts: i.contracts as vq.ParityContract[] })), forwards,
    { asOf: surface.asOf, spot: model.spot, r });
  const delta = vq.deltaGrid(observed, forwards);
  const skew = vq.skewMetrics(delta);
  const atmf: Record<string, number | null> = Object.fromEntries(model.rows.map(row => [row.expiry, delta.rows.find(x => x.expiry === row.expiry)?.cells.ATMF?.iv ?? null]));
  // struttura a termine, vol forward, utili e cono: l'ATMF della libreria (un solo ATM per tutta la pagina)
  const term: vq.TermPoint[] = model.rows.map(row => ({ expiry: row.expiry, T: T[row.expiry] ?? null, iv: atmf[row.expiry] }));
  const fwdVol = vq.forwardVol(term);
  const earnings = typeof data?.next_earnings === 'string' && data.next_earnings ? data.next_earnings : null;
  const event = earnings ? vq.eventVol(term, earnings, snapshotDay(data)) : null;
  const cone = vq.expectedMoveCone(forwards, term);
  const arb = vq.arbitrageChecks(surface, forwards);
  const arbObserved = inputs.length ? vq.arbitrageChecks(observed, forwards) : null;
  const flags: FlagItem[] = [
    ...arb.flags.map(flag => ({ flag, set: 'grid' as const })),
    ...(arbObserved?.flags || []).map(flag => ({ flag, set: 'observed' as const })),
  ].map((x, id) => ({ ...x, id, level: levelOf(x.flag), cells: flagCells(x.flag, model), ratio: ratioOf(x.flag) }))
    .sort((a, b) => (a.level === 'executable' ? 0 : 1) - (b.level === 'executable' ? 0 : 1)
      || (b.ratio ?? -1) - (a.ratio ?? -1) || b.flag.magnitude - a.flag.magnitude);
  const cellFlags = new Map<string, number[]>();
  const cellLevel = new Map<string, FlagLevel>();
  flags.forEach((f, i) => f.cells.forEach(c => {
    const k = cellKey(c.expiry, c.col);
    cellFlags.set(k, [...(cellFlags.get(k) || []), i]);
    if (cellLevel.get(k) !== 'executable') cellLevel.set(k, f.level);
  }));
  const nExecutable = flags.filter(f => f.level === 'executable').length, nIndicative = flags.length - nExecutable;

  // ΔIV: tenor = il tempo di ciascuna scadenza mostrata (T·365), cosi' le righe del diff sono le righe del modello.
  // Asse K/F se entrambe le istantanee portano i forward (la precedente li ha salvati), altrimenti K/S dichiarato.
  let diff: vq.SurfaceDiff | null = null;
  let diffCells: (number | null)[][] | null = null, diffReasons: (vq.ReasonCode | null)[][] | null = null, diffRange: number | null = null;
  let diffSlide: (number | null)[][] | null = null, diffExSlide: (number | null)[][] | null = null;
  if (previous) {
    const rowsWithT = model.rows.filter(row => finite(T[row.expiry]) && (T[row.expiry] as number) > 0);
    diff = vq.surfaceDiff(vq.moneynessSurfaceFromBuilder(visible), vq.moneynessSurfaceFromBuilder(previous),
      { tenorsDays: rowsWithT.map(row => (T[row.expiry] as number) * 365) });
    const col = new Map(diff.moneyness.map((m, i) => [Math.round(m * 1e9), i]));
    const byExpiry = new Map(rowsWithT.map((row, i) => [row.expiry, diff!.rows[i]]));
    const pick = <V>(get: (d: vq.SurfaceDiff['rows'][number], i: number) => V, none: V) => model.rows.map(row => model.grid.map(m => {
      const d = byExpiry.get(row.expiry), i = col.get(Math.round(m * 1e9));
      return d && i != null ? get(d, i) : none;
    }));
    diffCells = pick((d, i) => d.diff[i], null);
    diffReasons = pick<vq.ReasonCode | null>((d, i) => d.reasons[i], 'not_comparable');
    diffSlide = pick((d, i) => d.skewSlide[i], null);
    diffExSlide = pick((d, i) => d.diffExSlide[i], null);
    const abs = diffCells.flat().filter(finite).map(Math.abs);
    diffRange = abs.length ? Math.max(...abs) : null;
  }

  // asse in delta: le colonne della griglia delta della libreria al posto di K/S (stesse righe)
  const keys = delta.columns;
  const byExp = new Map(delta.rows.map(row => [row.expiry, row]));
  const deltaRows: SurfaceRow[] = model.rows.map(row => ({ ...row, atm: atmf[row.expiry], atmUnqualified: null, iv: keys.map(k => byExp.get(row.expiry)?.cells[k]?.iv ?? null) }));
  const dv = deltaRows.flatMap(row => row.iv.filter(finite));
  const deltaModel: SurfaceModel = { grid: keys.map((_, i) => i), spot: null, strikes: keys.map(() => null), rows: deltaRows,
    excludedShort: model.excludedShort, cells: deltaRows.length * keys.length, filledCells: dv.length, holes: deltaRows.length * keys.length - dv.length,
    ivMin: dv.length ? Math.min(...dv) : null, ivMax: dv.length ? Math.max(...dv) : null };

  return { T, atmf, r, forwards, forwardOf, delta, skew, term, fwdVol, event, cone, arb, arbObserved, flags, cellFlags, cellLevel, nExecutable, nIndicative,
    diff, diffCells, diffReasons, diffRange, diffSlide, diffExSlide, deltaModel, deltaHeads: keys.map(k => DELTA_HEADS[k]) };
}
