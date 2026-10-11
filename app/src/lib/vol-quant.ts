/* ============================================================================
   vol-quant — libreria di calcolo PURA della superficie di volatilità (10/10/2026, Opus 5.5; v2 stesso giorno
   dopo due revisioni avversariali).
   Nessuna UI, nessun import, nessuna rete. La consuma il Vol Deck (ramo opus55/volquant-ui).

   REGOLA DI CASA: un dato che manca resta mancante. Ogni risultato che non si può calcolare
   esce `null` con un `reason` (codice stabile, v. REASONS / reasonText) e un `detail` coi numeri.
   Mai spot·e^{rT} al posto del forward, mai estrapolazione fuori dagli strike coperti, mai 0.

   ---------------------------------------------------------------------------------------------
   CONVENZIONI (scelte, motivate, usate ovunque nel file)
   ---------------------------------------------------------------------------------------------
   T       anni ACT/365 fino alla chiusura di scadenza: è il `t_years` del builder
           (vol_surface.py, 16:00 New York). Le IV sono annualizzate sulla stessa base.
   k       moneyness forward logaritmica k = ln(K/F). È l'asse di smile, delta, arbitraggi.
   DELTA   FORWARD NON SCONTATO (Black-76 sul forward): Δcall = N(d1), Δput = N(d1) − 1,
           d1 = (−k + σ²T/2)/(σ√T). Motivo: il payload Polygon non porta né dividendi né tassi
           (il builder dichiara r = q = 0 per il suo rr25); il forward implicito dalla
           put-call parity incorpora tasso e dividendi, e il delta forward dipende SOLO da
           (F, K, T, σ). È la convenzione «forward delta» dei desk equity (OVDV permette
           forward o spot; qui forward, perché lo spot delta richiederebbe un q che non abbiamo).
           `bsDelta` (spot, r e q espliciti) resta esportata per confronti, non è usata dalla griglia.
   ATMF    K = F (k = 0); colonna e campi si chiamano «ATMF» (LABELS), mai «ATM». Motivo: è l'asse k = 0 di arbitraggi, forward vol e cono; il
           delta-neutral straddle (k = σ²T/2) differisce di σ²T/2, trascurabile sotto l'anno
           e comunque riportato nella cella (campo `delta` = N(σ√T/2)).
   SMILE   interpolazione LINEARE IN VARIANZA TOTALE w(k) = σ²T fra nodi adiacenti in k, mai
           fuori da [k_min, k_max] dei nodi (null, motivo 'delta_out_of_range' / 'outside_smile').
           Motivo: w lineare a tratti preserva la convessità di w e non inventa curvatura; è la
           stessa grandezza dei controlli di calendario. Minimo 3 nodi per smile (MIN_SMILE_NODES).
   FONTE   uno smile ha `source`: 'grid' = iv_grid del builder (K/S spot 0,80–1,20, mediana a
           3 punti, giunzione put/call a K = S, IV del provider) | 'observed' = quote OTM della
           chain scelte qui con giunzione a K = F (`smileFromChain` / `surfaceFromChains`).
           PREDEFINITA per la griglia delta: la superficie OSSERVATA; la griglia del builder è
           un'alternativa dichiarata (K/S 0,80–1,20: oltre i 3 mesi le ali 10Δ/25Δ escono n.d.).
           La griglia delta dichiara la fonte riga per riga.
   FORWARD da put-call parity DE-AMERICANIZZATA (default): EEP = CRR americano − europeo con l'IV
           del provider, sottratto dai mid; residuo e forward grezzo dichiarati.
   TASSO   r CONTINUO DECIMALE (0,043, non 4,3). FRED DGS3MO (rotta /options/strategy/rate) è
           in percento bond-equivalent: rateFromDgs3mo(percent) = 2·ln(1 + y/2). |r| > 0,5 = n.d.
           (quasi certamente un percento). Un solo tasso a 3 mesi per tutte le scadenze: sulle
           scadenze lunghe è un'approssimazione dichiarata.
   CONO    banda LOGNORMALE F·e^{±σ√T} (sempre positiva); `move` = F·σ√T è la mossa al primo ordine.

   LIMITI DICHIARATI
   - Calendario a k fisso: esatto con dividendi proporzionali; con dividendi in contanti fra
     due scadenze è un'approssimazione.
   - Opzioni USA americane: corrette con l'albero CRR (errore sul forward < 0,002% sui 12 casi
     del revisore, CRR a 2000 passi); i dividendi in contanti sono modellati come q continuo.
   - IV del provider (Polygon): modello, tasso e dividendi non documentati; IV put e call allo
     stesso strike calcolate separatamente (salto alla giunzione = origin 'junction').
   - Nessuna IV bid/ask nel payload: IV_NOISE è una convenzione, tarata su un esperimento del
     revisore (v. nodeIvNoise); dove bid/ask ci sono, butterfly e verticali sono in prezzo eseguibile
     e i segnali di modello sugli stessi nodi diventano `indicative`. Sulle ali il modello vede solo
     anomalie più grandi dello spread.
   - Forward: il residuo dipende dal modello di IV del provider usato nell'albero (IV per gamba):
     fino a ~0,1% a 2 anni con q = 3%, oltre la halfWidth delle quote.
   - Tempo a scadenza plausibile: 0 < T ≤ MAX_T_YEARS = 30 anni; oltre = 'invalid_input'.

   ---------------------------------------------------------------------------------------------
   API
   ---------------------------------------------------------------------------------------------
   normCdf(x), normPdf(x)                                   Hart 5666 (West): assoluto < 1e-15,
                                                            relativo in coda 5e-10 a |x| = 6
   bsDelta(type, S, K, T, sigma, r, q)                      delta spot BSM (e^{−qT}N(d1) …) | null
   deltaFromForward(type, F, K, T, sigma)                   delta forward non scontato | null
   forwardPrice(type, F, K, T, sigma)                       Black-76 NON scontato | null
   strikeForDelta(F, T, sigmaOfK, targetDelta, type, range) strike col delta richiesto, smile
                                                            che dipende dallo strike: scansione
                                                            + bisezione su k. put: target < 0.
   continuousFromBondEquivalent(y), rateFromDgs3mo(pct)     tasso continuo decimale
   impliedForward(chainsPerExpiry, options?)                Record<expiry, ForwardResult>
   forwardInfo(forwards, expiry)                            { F, reason } col motivo VERO
   buildSmile(slice, F, forwardReason?)                     smile interpolato di una scadenza
   smileFromChain(expiry, T, contracts, F, options?)        smile OTM osservato a giunzione K = F
   surfaceFromBuilder(payload)                              SmileSurface dalla /surface del builder
   surfaceFromChains(chains, forwards, options?)            superficie OSSERVATA (default della griglia)
   crrPrice(type, S, K, T, σ, r, q, american?, steps?)      albero CRR (de-americanizzazione)
   deltaGrid(surface, forwards)                             IV a 10ΔP 25ΔP ATMF 25ΔC 10ΔC
   skewMetrics(grid)                                        RR25 BF25 RR10 BF10 RR25/ATMF (BF = smile BF)
   forwardVol(term, options?)                               vol forward fra scadenze consecutive
   forwardVolByDelta(grid, options?)                        idem per colonna delta (ATMF inclusa)
   arbitrageChecks(surface, forwards, options?)             calendario / butterfly / verticale /
                                                            densità; clean = null se nulla controllato
   moneynessSurfaceFromBuilder(payload)                     griglia K/S × scadenza per il diff
   surfaceDiff(current, previous, options?)                 ΔIV a tenor costante × K/S
   eventVol(term, earningsDate, asOf, options?)             vol d'evento degli utili (asOf = data
                                                            New York dell'istantanea, obbligatoria)
   expectedMoveCone(F, term)                                ±1σ: σ√T, F·σ√T, banda F·e^{±σ√T}
   reasonText(code, lang)                                   testo IT/EN di un codice motivo
   ========================================================================== */

export type OptionType = 'call' | 'put';
export type SmileSource = 'grid' | 'observed';

/* ---------------------------------------------------------------- motivi */
export const REASONS = {
  invalid_input: ['input non valido (numero mancante, ≤ 0 o non finito)', 'invalid input (missing, ≤ 0 or non-finite number)'],
  rate_missing: ['tasso r n.d.: il forward da parità non si calcola', 'rate r n/a: the parity forward cannot be computed'],
  time_missing: ['tempo a scadenza n.d. o ≤ 0', 'time to expiry n/a or ≤ 0'],
  no_valid_pairs: ['nessuna coppia call/put con quote valide allo stesso strike', 'no call/put pair with valid quotes at the same strike'],
  too_few_pairs: ['coppie call/put valide sotto il minimo', 'valid call/put pairs below the minimum'],
  forward_missing: ['forward n.d. per la scadenza', 'forward n/a for the expiry'],
  spot_missing: ['spot n.d.: la griglia K/S non diventa strike', 'spot n/a: the K/S grid cannot become strikes'],
  spot_proxy: ['spot del builder = uno strike (proxy): griglia n.d.', 'builder spot is a strike (proxy): grid n/a'],
  surface_error: ['la superficie del builder è in errore', 'the builder surface reported an error'],
  duplicate_input: ['input ripetuto per la stessa chiave: nessuno dei due scelto a caso', 'repeated input for the same key: neither is picked at random'],
  iv_out_of_range: ['IV fuori dall’intervallo plausibile (IV_RANGE)', 'IV outside the plausible range (IV_RANGE)'],
  previous_missing: ['istantanea precedente n.d.', 'previous snapshot n/a'],
  smile_too_short: ['smile con meno nodi del minimo', 'smile with fewer nodes than the minimum'],
  delta_out_of_range: ['delta non raggiunto dentro gli strike coperti (mai estrapolato)', 'delta not reached within the covered strikes (never extrapolated)'],
  delta_ambiguous: ['più strike con lo stesso delta: smile troppo ripido', 'several strikes with the same delta: smile too steep'],
  outside_smile: ['fuori dagli strike coperti dallo smile (mai estrapolato)', 'outside the strikes covered by the smile (never extrapolated)'],
  iv_missing: ['IV n.d.', 'IV n/a'],
  same_time: ['scadenze con lo stesso tempo', 'expiries with the same time'],
  calendar_arbitrage: ['varianza totale decrescente: arbitraggio di calendario', 'decreasing total variance: calendar arbitrage'],
  single_expiry: ['una sola scadenza utilizzabile', 'only one usable expiry'],
  event_timing_ambiguous: ['utili nel giorno di scadenza: prima o dopo la chiusura non è nel payload', 'earnings on the expiry day: before/after the close is not in the payload'],
  event_past: ['utili già passati alla data dell’istantanea', 'earnings already past at the snapshot date'],
  as_of_missing: ['data dell’istantanea n.d.: non si sa se gli utili sono passati', 'snapshot date n/a: cannot tell whether the earnings are past'],
  no_expiry_after_event: ['nessuna scadenza dopo gli utili', 'no expiry after the earnings'],
  no_base_vol: ['vol base senza evento n.d. (serve un intervallo senza utili)', 'base vol without event n/a (needs an interval without earnings)'],
  no_event_premium: ['varianza d\'evento ≤ 0: nessun premio utili misurabile', 'event variance ≤ 0: no measurable earnings premium'],
  not_comparable: ['cella non confrontabile fra le due istantanee', 'cell not comparable between the two snapshots'],
  outside_term: ['tenor fuori dalle scadenze coperte (mai estrapolato)', 'tenor outside the covered expiries (never extrapolated)'],
} as const;
export type ReasonCode = keyof typeof REASONS;
export const reasonText = (code: ReasonCode | null | undefined, lang: 'it' | 'en' = 'it'): string =>
  code ? REASONS[code][lang === 'it' ? 0 : 1] : '';

/* ---------------------------------------------------------------- costanti dichiarate */
/** Nodi minimi per uno smile (2 nodi = una retta: nessuna curvatura misurabile). */
export const MIN_SMILE_NODES = 3;
/** Rumore di IV ammesso per nodo (0,5 punti vol ≈ metà di uno spread bid/ask in vol su un nome
 *  liquido): un'anomalia si segnala solo se sopravvive spostando ogni IV di ±IV_NOISE nel verso
 *  che la cancella. Il payload non porta IV bid/ask per contratto: il valore è una CONVENZIONE. */
export const IV_NOISE = 0.005;
/** Colonne standard della griglia delta (delta forward non scontato; put negativo). */
export const DELTA_COLUMNS = [
  { key: '10P', type: 'put', delta: -0.10 },
  { key: '25P', type: 'put', delta: -0.25 },
  { key: 'ATMF', type: null, delta: null },
  { key: '25C', type: 'call', delta: 0.25 },
  { key: '10C', type: 'call', delta: 0.10 },
] as const;
export type DeltaKey = typeof DELTA_COLUMNS[number]['key'];
/** Etichette da mostrare: l'ATM della libreria è ATMF (K = F), non l'«ATM spot» del builder; il
 *  BF è quello dello SMILE (media delle ali − ATMF), non lo strangle quotato dal mercato. */
export const LABELS = { ATMF: 'ATMF (K = F)', BF: 'smile BF (non strangle di mercato)', BF_EN: 'smile BF (not the market strangle)' } as const;
/** Tenor standard (giorni di calendario) del confronto fra istantanee. */
export const DIFF_TENORS_DAYS = [7, 14, 30, 60, 91, 182, 365] as const;
/** Sedute per anno della mossa d'evento: σ_event·√(1/252) = mossa del giorno degli utili. */
export const TRADING_DAYS = 252;

/* ---------------------------------------------------------------- numeri */
const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const isPos = (v: unknown): v is number => isNum(v) && v > 0;
/** Intervallo di plausibilità del tempo a scadenza: 0 < T ≤ MAX_T_YEARS (anni). */
export const MAX_T_YEARS = 30;
const isT = (v: unknown): v is number => isPos(v) && v <= MAX_T_YEARS;
/** T non valido: assurdo (> MAX_T_YEARS) = 'invalid_input', mancante o ≤ 0 = 'time_missing'. */
const tReason = (v: unknown): 'invalid_input' | 'time_missing' => isNum(v) && v > MAX_T_YEARS ? 'invalid_input' : 'time_missing';
const median = (xs: number[]): number => {
  const s = [...xs].sort((a, b) => a - b), n = s.length;
  return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2;
};

/** Funzione di ripartizione normale standard (Hart 1968 n. 5666, codice di West 2005). Misurata
 *  contro scipy: errore assoluto < 1e-15; relativo 1e-15 a |x| = 2, 5e-11 a 5, 5e-10 a 6 (code). */
export function normCdf(x: number): number {
  const a = Math.abs(x);
  let c: number;
  if (a > 37) c = 0;
  else {
    const e = Math.exp(-a * a / 2);
    if (a < 7.07106781186547) {
      let b = 3.52624965998911e-2 * a + 0.700383064443688;
      b = b * a + 6.37396220353165; b = b * a + 33.912866078383; b = b * a + 112.079291497871;
      b = b * a + 221.213596169931; b = b * a + 220.206867912376;
      c = e * b;
      b = 8.83883476483184e-2 * a + 1.75566716318264;
      b = b * a + 16.064177579207; b = b * a + 86.7807322029461; b = b * a + 296.564248779674;
      b = b * a + 637.333633378831; b = b * a + 793.826512519948; b = b * a + 440.413735824752;
      c = c / b;
    } else {
      let b = a + 0.65;
      b = a + 4 / b; b = a + 3 / b; b = a + 2 / b; b = a + 1 / b;
      c = e / b / 2.506628274631;
    }
  }
  return x > 0 ? 1 - c : c;
}
export const normPdf = (x: number): number => Math.exp(-x * x / 2) / Math.sqrt(2 * Math.PI);

/* ---------------------------------------------------------------- 1. delta */
/** Delta spot Black-Scholes-Merton europeo, r e q continui: call e^{−qT}N(d1), put e^{−qT}(N(d1)−1). */
export function bsDelta(type: OptionType, S: number, K: number, T: number, sigma: number, r: number, q: number): number | null {
  if (!isPos(S) || !isPos(K) || !isT(T) || !isPos(sigma) || !isNum(r) || !isNum(q)) return null;
  const d1 = (Math.log(S / K) + (r - q + sigma * sigma / 2) * T) / (sigma * Math.sqrt(T));
  const n = normCdf(d1);
  return Math.exp(-q * T) * (type === 'call' ? n : n - 1);
}

const d1Forward = (F: number, K: number, T: number, sigma: number) =>
  (Math.log(F / K) + sigma * sigma * T / 2) / (sigma * Math.sqrt(T));

/** Delta forward NON scontato: call N(d1), put N(d1) − 1, d1 = (ln F/K + σ²T/2)/(σ√T). */
export function deltaFromForward(type: OptionType, F: number, K: number, T: number, sigma: number): number | null {
  if (!isPos(F) || !isPos(K) || !isT(T) || !isPos(sigma)) return null;
  const n = normCdf(d1Forward(F, K, T, sigma));
  return type === 'call' ? n : n - 1;
}

/** Prezzo Black-76 NON scontato (in unità del sottostante forward). */
export function forwardPrice(type: OptionType, F: number, K: number, T: number, sigma: number): number | null {
  if (!isPos(F) || !isPos(K) || !isT(T) || !isPos(sigma)) return null;
  const d1 = d1Forward(F, K, T, sigma), d2 = d1 - sigma * Math.sqrt(T);
  return type === 'call' ? F * normCdf(d1) - K * normCdf(d2) : K * normCdf(-d2) - F * normCdf(-d1);
}
/** Vega Black-76 non scontata, per unità di vol (∂prezzo/∂σ). */
const forwardVega = (F: number, K: number, T: number, sigma: number) => F * normPdf(d1Forward(F, K, T, sigma)) * Math.sqrt(T);

export interface StrikeForDelta {
  strike: number | null; k: number | null; iv: number | null; delta: number | null;
  reason: ReasonCode | null; detail: string | null;
}
const noStrike = (reason: ReasonCode, detail: string | null = null): StrikeForDelta =>
  ({ strike: null, k: null, iv: null, delta: null, reason, detail });

/**
 * Strike al delta forward `targetDelta` (call in (0,1), put in (−1,0)) con uno smile σ(k) che
 * dipende dallo strike: Δ(k) = deltaFromForward(type, F, F·e^k, T, σ(k)) − target.
 * Metodo (v2): scansione di [range.kMin, range.kMax] a 2000 passi + i nodi in `range.nodes`;
 * dove |Δ − target| ha un minimo locale SENZA cambio di segno (due radici vicine possono stare
 * nello stesso passo) il tratto si riscandisce a 400 sottopassi; poi bisezione (|Δk| < 1e-13).
 * Nessun cambio di segno = 'delta_out_of_range' (mai estrapolato); più di uno = 'delta_ambiguous'.
 */
export function strikeForDelta(F: number, T: number, sigmaOfK: (k: number) => number | null, targetDelta: number,
  type: OptionType, range: { kMin: number; kMax: number; nodes?: number[] }): StrikeForDelta {
  if ((type !== 'call' && type !== 'put') || !isPos(F) || !isT(T) || !isNum(targetDelta) || !range || !isNum(range.kMin) || !isNum(range.kMax) || range.kMax <= range.kMin)
    return noStrike('invalid_input');
  if (type === 'call' ? !(targetDelta > 0 && targetDelta < 1) : !(targetDelta < 0 && targetDelta > -1))
    return noStrike('invalid_input', `target ${targetDelta} for ${type}`);
  const f = (k: number): number | null => {
    const s = sigmaOfK(k);
    if (!isPos(s)) return null;
    const d = deltaFromForward(type, F, F * Math.exp(k), T, s);
    return d === null ? null : d - targetDelta;
  };
  const steps = 2000, { kMin, kMax } = range;
  const ks = new Set<number>();
  for (let i = 0; i <= steps; i++) ks.add(kMin + (kMax - kMin) * i / steps);
  for (const n of range.nodes ?? []) if (isNum(n) && n >= kMin && n <= kMax) ks.add(n);
  const grid = [...ks].sort((a, b) => a - b);
  const vals: number[] = [];
  for (const k of grid) {
    const v = f(k);
    if (v === null) return noStrike('outside_smile', `σ(k) n/a at k=${k}`);
    vals.push(v);
  }
  const brackets: [number, number][] = [];
  const collect = (gs: number[], vs: number[]) => {
    for (let i = 0; i < gs.length; i++) {
      if (vs[i] === 0) { brackets.push([gs[i], gs[i]]); continue; }
      if (i + 1 < gs.length && vs[i + 1] !== 0 && (vs[i] > 0) !== (vs[i + 1] > 0)) brackets.push([gs[i], gs[i + 1]]);
    }
  };
  // raffinamento locale: minimo di |f| fra i vicini senza cambio di segno
  const refined = new Set<number>();
  for (let i = 1; i + 1 < grid.length; i++) {
    const a = vals[i - 1], b = vals[i], c = vals[i + 1];
    if ((a > 0) === (b > 0) && (b > 0) === (c > 0) && Math.abs(b) < Math.abs(a) && Math.abs(b) <= Math.abs(c)) {
      const gs: number[] = [], vs: number[] = [];
      for (let j = 0; j <= 400; j++) {
        const k = grid[i - 1] + (grid[i + 1] - grid[i - 1]) * j / 400, v = f(k);
        if (v === null) return noStrike('outside_smile', `σ(k) n/a at k=${k}`);
        gs.push(k); vs.push(v);
      }
      const before = brackets.length;
      collect(gs, vs);
      if (brackets.length > before) refined.add(i);
    }
  }
  collect(grid, vals);
  if (!brackets.length)
    return noStrike('delta_out_of_range', `Δ at k∈[${kMin.toFixed(4)}, ${kMax.toFixed(4)}] = [${(vals[0] + targetDelta).toFixed(4)}, ${(vals[vals.length - 1] + targetDelta).toFixed(4)}], target ${targetDelta}`);
  if (brackets.length > 1) return noStrike('delta_ambiguous', `${brackets.length} roots${refined.size ? ' (found by local refinement)' : ''}`);
  let [lo, hi] = brackets[0];
  if (lo !== hi) {
    let flo = f(lo) as number;
    for (let i = 0; i < 200 && hi - lo > 1e-13; i++) {
      const mid = (lo + hi) / 2, fm = f(mid) as number;
      if (fm === 0) { lo = hi = mid; break; }
      if ((fm > 0) === (flo > 0)) { lo = mid; flo = fm; } else hi = mid;
    }
  }
  const k = (lo + hi) / 2, iv = sigmaOfK(k) as number;
  return { strike: F * Math.exp(k), k, iv, delta: deltaFromForward(type, F, F * Math.exp(k), T, iv), reason: null, detail: null };
}

/* ---------------------------------------------------------------- 2. tasso e forward implicito */
/** Tasso continuo da un rendimento BOND-EQUIVALENT (capitalizzazione semestrale), y DECIMALE:
 *  r = 2·ln(1 + y/2). È la base dei Treasury CMT (H.15), quindi di FRED DGS3MO. */
export function continuousFromBondEquivalent(y: number | null | undefined): number | null {
  return isNum(y) && y > -2 ? 2 * Math.log1p(y / 2) : null;
}
/** FRED DGS3MO è in PERCENTO bond-equivalent (es. 4,37): r continuo decimale. La rotta
 *  /options/strategy/rate dà `percent` (questo) e `value` = percent/100 (per l'helper sopra). */
export const rateFromDgs3mo = (percent: number | null | undefined): number | null =>
  isNum(percent) ? continuousFromBondEquivalent(percent / 100) : null;
/** |r| oltre questa soglia è quasi certamente un tasso passato in percento: n.d. dichiarato. */
export const MAX_ABS_RATE = 0.5;

/**
 * Albero binomiale CRR (Cox-Ross-Rubinstein) con `steps` passi, dividendo continuo q.
 * american = true: esercizio anticipato a ogni nodo. Lo stesso albero in versione europea dà il
 * premio d'esercizio anticipato EEP = americano − europeo con l'errore di discretizzazione che si
 * elide. null su input non validi.
 */
export function crrPrice(type: OptionType, S: number, K: number, T: number, sigma: number, r: number, q: number,
  american = true, steps = CRR_STEPS): number | null {
  if ((type !== 'call' && type !== 'put') || !isPos(S) || !isPos(K) || !isT(T) || !isPos(sigma) || !isNum(r) || !isNum(q) || !(steps >= 1)) return null;
  const n = Math.floor(steps), dt = T / n, u = Math.exp(sigma * Math.sqrt(dt)), d = 1 / u;
  const p = (Math.exp((r - q) * dt) - d) / (u - d), disc = Math.exp(-r * dt);
  if (!(p > 0 && p < 1)) return null;
  // S·u^(i−j)·d^j = S·u^(i−2j): una tabella di potenze invece di due pow per nodo
  const sign = type === 'call' ? 1 : -1, V = new Float64Array(n + 1), P = new Float64Array(2 * n + 1);
  for (let m = -n; m <= n; m++) P[m + n] = S * Math.exp(m * sigma * Math.sqrt(dt));
  for (let j = 0; j <= n; j++) V[j] = Math.max(sign * (P[n - 2 * j + n] - K), 0);
  for (let i = n - 1; i >= 0; i--) for (let j = 0; j <= i; j++) {
    const cont = disc * (p * V[j] + (1 - p) * V[j + 1]);
    V[j] = american ? Math.max(cont, sign * (P[i - 2 * j + n] - K)) : cont;
  }
  return V[0];
}
/** Passi dell'albero CRR della de-americanizzazione (residuo dichiarato in ForwardResult). */
export const CRR_STEPS = 500;
/** Iterazioni massime q → EEP → F → q (si ferma prima se F si muove meno di 1e-7·F). */
export const DEAM_MAX_ITER = 8;

/** I campi della chain che servono (sottoinsieme di OptionContract di vol-deck.ts). */
export interface ParityContract {
  type: OptionType | string; strike: number | null; bid: number | null; ask: number | null;
  oi?: number | null; volume?: number | null; adjusted?: boolean; multiplier?: number | null; iv?: number | null;
}
export interface ForwardInput {
  expiry: string; T: number | null;
  /** tasso CONTINUO DECIMALE (es. 0,043). Da DGS3MO: rateFromDgs3mo(percent). */
  r: number | null;
  contracts: ParityContract[];
  /** spot: OBBLIGATORIO con esercizio americano (serve all'albero della de-americanizzazione);
   *  dà anche il carry implicito. Mai usato come ripiego del forward. */
  spot?: number | null;
}
export interface ForwardOptions {
  /** coppie usate, le più vicine all'ATM (|C−P| minimo). Default 4. */
  maxPairs?: number;
  /** coppie valide minime per dichiarare un forward. Default 2. */
  minPairs?: number;
  /** spread relativo massimo (ask−bid)/mid di ciascuna gamba. Default 0,5 (v. QUOTE_MAX_REL_SPREAD). */
  maxRelSpread?: number;
  /** 'american' (default: opzioni su azioni USA) = de-americanizzazione CRR prima della parità;
   *  'european' = parità diretta sui mid (indici europei/cash-settled). */
  exercise?: 'american' | 'european';
}
/** Spread relativo massimo di una quota usata qui (forward e smile osservato). PIÙ STRETTO del
 *  builder (MAX_REL_SPREAD = 1 + eccezione 3 tick da 0,05 $): l'errore del forward è l'errore del
 *  mid, quindi qui si chiede una quota più negoziabile. Le quote senza bid/ask, che il builder
 *  tiene come «non verificabili», qui sono escluse: senza mid non c'è parità. */
export const QUOTE_MAX_REL_SPREAD = 0.5;
/** Intervallo di IV plausibile: lo stesso IV_RANGE del builder (vol_surface.py). */
export const IV_RANGE: readonly [number, number] = [0.005, 5.0];
const isIv = (v: unknown): v is number => isNum(v) && v >= IV_RANGE[0] && v <= IV_RANGE[1];
export interface ParityPair {
  strike: number;
  /** mid osservati (americani se exercise = 'american') */
  callMid: number; putMid: number;
  /** premi d'esercizio anticipato sottratti (0 con 'european') */
  callEep: number; putEep: number;
  forward: number; halfWidth: number;
}
export interface ForwardResult {
  expiry: string; forward: number | null; reason: ReasonCode | null; detail: string | null;
  T: number | null; r: number | null;
  exercise: 'american' | 'european';
  /** pairs: coppie usate (le più vicine all'ATM); `forward` = mediana dei loro candidati */
  pairs: ParityPair[]; validPairs: number;
  /** max − min dei candidati, assoluta e relativa al forward: la QUALITÀ della stima */
  dispersion: number | null; dispersionRel: number | null;
  /** mediana dell'incertezza da bid/ask di un candidato: e^{rT}·(spreadC + spreadP)/2 */
  halfWidth: number | null;
  /** forward dalla parità sui mid AMERICANI grezzi: forward − rawForward = correzione d'esercizio */
  rawForward: number | null;
  /** |F ultima iterazione − F penultima|: residuo dichiarato della de-americanizzazione */
  deamericanizationResidual: number | null;
  /** diagnostico (con lo spot): ln(F/S)/T e q implicito = r − carry */
  impliedCarry: number | null; impliedDividendYield: number | null;
}

/** Motivo di esclusione di una quota, o null se utilizzabile. */
const quoteDefect = (c: ParityContract, maxRelSpread: number): string | null => {
  if (!isPos(c.strike)) return 'no_strike';
  if (c.adjusted || (c.multiplier != null && c.multiplier !== 100)) return 'adjusted';
  if (!((c.oi ?? 0) > 0 || (c.volume ?? 0) > 0)) return 'no_activity';
  if (!isPos(c.bid) || !isPos(c.ask)) return 'no_bid_ask';
  if ((c.ask as number) < (c.bid as number)) return 'crossed';
  const mid = ((c.bid as number) + (c.ask as number)) / 2;
  return ((c.ask as number) - (c.bid as number)) / mid <= maxRelSpread ? null : 'wide_spread';
};
const objs = <T>(xs: unknown): T[] => Array.isArray(xs) ? xs.filter(x => x !== null && typeof x === 'object') as T[] : [];
/** Migliore quota per (tipo, strike): OI maggiore, a parità lo spread più stretto. */
const bestQuotes = (contracts: unknown, maxRel: number, extra: (c: ParityContract) => string | null = () => null,
  excluded?: Record<string, number>) => {
  const best = new Map<string, ParityContract>();
  for (const c of objs<ParityContract>(contracts)) {
    if (c.type !== 'call' && c.type !== 'put') { if (excluded) excluded.no_type = (excluded.no_type ?? 0) + 1; continue; }
    const why = quoteDefect(c, maxRel) ?? extra(c);
    if (why) { if (excluded) excluded[why] = (excluded[why] ?? 0) + 1; continue; }
    const key = c.type + '|' + c.strike, prev = best.get(key);
    const spread = (x: ParityContract) => (x.ask as number) - (x.bid as number);
    if (!prev || (c.oi ?? 0) > (prev.oi ?? 0) || ((c.oi ?? 0) === (prev.oi ?? 0) && spread(c) < spread(prev))) best.set(key, c);
  }
  return best;
};

/**
 * Forward implicito per scadenza dalla put-call parity europea: C − P = e^{−rT}(F − K) ⇒
 * F = K + e^{rT}(C − P). Quote valide: bid > 0, ask ≥ bid, (ask−bid)/mid ≤ maxRelSpread (default
 * 0,5, più stretto del builder), OI o volume > 0, non rettificata, moltiplicatore 100. Per ogni
 * strike con call e put valide un candidato; si tengono i `maxPairs` col |C − P| minimo (i più
 * vicini all'ATM) e il forward è la MEDIANA.
 * ESERCIZIO AMERICANO (default): la parità europea sui prezzi americani sposta F in modo
 * sistematico (−0,6% a 1 anno con r = 4,5%: il premio della put ITM). Cura: per ogni gamba si
 * calcola l'EEP = CRR americano − CRR europeo (CRR_STEPS passi, stesso albero) con l'IV del
 * provider, lo spot e il q implicito; si sottrae dal mid e si applica la parità ai prezzi europei
 * equivalenti. Il q parte da quello della parità grezza e si itera (fino a DEAM_MAX_ITER, stop a 1e-7·F); `rawForward` e
 * `deamericanizationResidual` lo dichiarano. Senza spot → 'spot_missing'; gambe senza IV del
 * provider escluse (conteggio in `detail`). LIMITE: dividendi in contanti modellati come q continuo.
 * Nessun ripiego: r mancante → 'rate_missing'; |r| > MAX_ABS_RATE → 'invalid_input' (percento?);
 * scadenza ripetuta nell'input → 'duplicate_input' per quella scadenza; coppie < minPairs → n.d.
 */
export function impliedForward(chainsPerExpiry: ForwardInput[], options: ForwardOptions = {}): Record<string, ForwardResult> {
  const maxPairs = options.maxPairs ?? 4, minPairs = options.minPairs ?? 2, maxRel = options.maxRelSpread ?? QUOTE_MAX_REL_SPREAD;
  const exercise = options.exercise === 'european' ? 'european' : 'american';
  const out: Record<string, ForwardResult> = {};
  const inputs = objs<ForwardInput>(chainsPerExpiry), count = new Map<string, number>();
  for (const i of inputs) count.set(String(i.expiry ?? ''), (count.get(String(i.expiry ?? '')) ?? 0) + 1);
  for (const input of inputs) {
    const expiry = String(input.expiry ?? '');
    const base: ForwardResult = { expiry, forward: null, reason: null, detail: null, T: isT(input.T) ? input.T : null, r: isNum(input.r) ? input.r : null,
      exercise, pairs: [], validPairs: 0, dispersion: null, dispersionRel: null, halfWidth: null, rawForward: null, deamericanizationResidual: null,
      impliedCarry: null, impliedDividendYield: null };
    if ((count.get(expiry) ?? 0) > 1) { out[expiry] = { ...base, reason: 'duplicate_input', detail: `${count.get(expiry)} inputs for ${expiry}` }; continue; }
    if (!isT(input.T)) { out[expiry] = { ...base, reason: tReason(input.T) }; continue; }
    if (!isNum(input.r)) { out[expiry] = { ...base, reason: 'rate_missing' }; continue; }
    if (Math.abs(input.r) > MAX_ABS_RATE) { out[expiry] = { ...base, reason: 'invalid_input', detail: `r = ${input.r}: percent instead of a decimal continuous rate?` }; continue; }
    const S = isPos(input.spot) ? input.spot : null;
    if (exercise === 'american' && S === null) { out[expiry] = { ...base, reason: 'spot_missing', detail: 'the early-exercise tree needs the spot' }; continue; }
    const T = input.T, r = input.r, growth = Math.exp(r * T);
    const best = bestQuotes(input.contracts, maxRel);
    type Raw = { strike: number; call: ParityContract; put: ParityContract; cm: number; pm: number };
    const raws: Raw[] = [];
    let noIv = 0;
    for (const [key, call] of best) {
      if (!key.startsWith('call|')) continue;
      const put = best.get('put|' + call.strike);
      if (!put) continue;
      if (exercise === 'american' && (!isIv(call.iv) || !isIv(put.iv))) { noIv++; continue; }
      raws.push({ strike: call.strike as number, call, put, cm: ((call.bid as number) + (call.ask as number)) / 2, pm: ((put.bid as number) + (put.ask as number)) / 2 });
    }
    const fwdOf = (x: Raw, ce: number, pe: number) => x.strike + growth * ((x.cm - ce) - (x.pm - pe));
    const valid = raws.filter(x => fwdOf(x, 0, 0) > 0);
    base.validPairs = valid.length;
    const ivNote = noIv ? `${noIv} pairs without provider IV excluded` : null;
    if (!valid.length) { out[expiry] = { ...base, reason: 'no_valid_pairs', detail: ivNote }; continue; }
    if (valid.length < minPairs) { out[expiry] = { ...base, reason: 'too_few_pairs', detail: `${valid.length} < ${minPairs}${ivNote ? '; ' + ivNote : ''}` }; continue; }
    const used = [...valid].sort((a, b) => Math.abs(a.cm - a.pm) - Math.abs(b.cm - b.pm) || a.strike - b.strike)
      .slice(0, maxPairs).sort((a, b) => a.strike - b.strike);
    const rawF = median(used.map(x => fwdOf(x, 0, 0)));
    let eeps = used.map(() => [0, 0]), F = rawF, residual: number | null = null;
    if (exercise === 'american') {
      let q = r - Math.log(rawF / (S as number)) / T, prevF = rawF;
      for (let it = 0; it < DEAM_MAX_ITER; it++) {
        eeps = used.map(x => ['call', 'put'].map(ty => {
          const c = ty === 'call' ? x.call : x.put;
          const am = crrPrice(ty as OptionType, S as number, x.strike, T, c.iv as number, r, q, true);
          const eu = crrPrice(ty as OptionType, S as number, x.strike, T, c.iv as number, r, q, false);
          return am !== null && eu !== null ? Math.max(am - eu, 0) : NaN;
        }));
        if (eeps.some(e => !Number.isFinite(e[0]) || !Number.isFinite(e[1]))) { residual = null; F = NaN; break; }
        F = median(used.map((x, i) => fwdOf(x, eeps[i][0], eeps[i][1])));
        residual = Math.abs(F - prevF); prevF = F;
        if (!(F > 0) || residual < 1e-7 * F) break;
        q = r - Math.log(F / (S as number)) / T;
      }
      if (!(F > 0)) { out[expiry] = { ...base, rawForward: rawF, reason: 'invalid_input', detail: 'de-americanization failed (tree parameters)' }; continue; }
    }
    const pairs: ParityPair[] = used.map((x, i) => ({ strike: x.strike, callMid: x.cm, putMid: x.pm, callEep: eeps[i][0], putEep: eeps[i][1],
      forward: fwdOf(x, eeps[i][0], eeps[i][1]),
      halfWidth: growth * (((x.call.ask as number) - (x.call.bid as number)) + ((x.put.ask as number) - (x.put.bid as number))) / 2 }));
    const fs = pairs.map(p => p.forward), dispersion = Math.max(...fs) - Math.min(...fs);
    const carry = S !== null ? Math.log(F / S) / T : null;
    out[expiry] = { ...base, forward: F, detail: ivNote, pairs, dispersion, dispersionRel: dispersion / F, rawForward: rawF, deamericanizationResidual: residual,
      halfWidth: median(pairs.map(p => p.halfWidth)), impliedCarry: carry, impliedDividendYield: carry === null ? null : r - carry };
  }
  return out;
}

/* ---------------------------------------------------------------- superficie (smile) */
export interface SmilePoint {
  strike: number; iv: number;
  /** facoltativi (smile osservato): tipo e quote del contratto, per i controlli in PREZZO ESEGUIBILE */
  type?: OptionType; bid?: number | null; ask?: number | null;
}
/** Giunzione put/call dello smile: put a K < strike (o ≤ se putsInclusive), call sopra. */
export interface SmileJunction { strike: number; putsInclusive: boolean }
export interface SmileSlice {
  expiry: string; T: number | null; days?: number | null; points: SmilePoint[]; source: SmileSource;
  /** false = il builder dichiara la griglia NON qualificata (spot non allineato): gli strike
   *  K = (K/S)·spot restano quelli su cui il builder l'ha costruita, ma l'etichetta va mostrata */
  qualified?: boolean;
  /** perché i punti mancano, se lo sa chi ha costruito la fetta (spot proxy, errore, forward) */
  reason?: ReasonCode | null;
  junction?: SmileJunction | null;
  /** fattore di sconto e^{−rT} (smile osservato con r noto): serve solo per convertire put↔call
   *  nei butterfly eseguibili a cavallo della giunzione */
  discount?: number | null;
  /** quote escluse per motivo (solo smileFromChain) */
  excluded?: Record<string, number>;
}
export interface SmileSurface { asOf: string | null; spot: number | null; slices: SmileSlice[]; reason?: ReasonCode | null; detail?: string | null }
export type ForwardLike = number | null | undefined | { forward: number | null; reason?: ReasonCode | null };
export type Forwards = Record<string, ForwardLike>;
/** Forward della scadenza e, se manca, il motivo VERO (es. 'rate_missing' dal ForwardResult). */
export function forwardInfo(forwards: Forwards | null | undefined, expiry: string): { F: number | null; reason: ReasonCode | null } {
  const v = forwards && typeof forwards === 'object' ? forwards[expiry] : null;
  const f = typeof v === 'number' ? v : v && typeof v === 'object' ? v.forward : null;
  if (isPos(f)) return { F: f, reason: null };
  const why = v && typeof v === 'object' && v.reason && v.reason in REASONS ? v.reason : null;
  return { F: null, reason: why ?? 'forward_missing' };
}

interface Node { k: number; strike: number; iv: number; w: number; type: OptionType | null; bid: number | null; ask: number | null }
export interface Smile {
  expiry: string; T: number; F: number; source: SmileSource; nodes: Node[]; kMin: number; kMax: number;
  junction: SmileJunction | null; qualified: boolean; discount: number | null;
  /** strike duplicati mediati e punti con IV fuori IV_RANGE scartati */
  duplicates: number; outOfRange: number;
  /** varianza totale interpolata (lineare in k fra nodi), null fuori dai nodi */
  w: (k: number) => number | null;
  sigma: (k: number) => number | null;
}

/** Smile su k = ln(K/F) di una scadenza: nodi validi ordinati, IV fuori IV_RANGE scartate,
 *  strike duplicati MEDIATI (dichiarato: `duplicates`; le quote del nodo mediato si perdono).
 *  `forwardReason` = il motivo vero se F manca. */
export function buildSmile(slice: SmileSlice, F: number | null, forwardReason: ReasonCode | null = null):
  { smile: Smile | null; reason: ReasonCode | null; duplicates: number } {
  if (!slice || typeof slice !== 'object') return { smile: null, reason: 'invalid_input', duplicates: 0 };
  if (!isT(slice.T)) return { smile: null, reason: tReason(slice.T), duplicates: 0 };
  if (!isPos(F)) return { smile: null, reason: forwardReason ?? 'forward_missing', duplicates: 0 };
  const T = slice.T;
  const byStrike = new Map<number, SmilePoint[]>();
  let outOfRange = 0;
  for (const p of objs<SmilePoint>(slice.points)) {
    if (!isPos(p.strike) || !isNum(p.iv)) continue;
    if (!isIv(p.iv)) { outOfRange++; continue; }
    byStrike.set(p.strike, [...(byStrike.get(p.strike) ?? []), p]);
  }
  let duplicates = 0;
  const nodes: Node[] = [...byStrike].map(([strike, ps]) => {
    duplicates += ps.length - 1;
    const iv = ps.reduce((a, b) => a + b.iv, 0) / ps.length, one = ps.length === 1 ? ps[0] : null;
    return { strike, iv, k: Math.log(strike / F), w: iv * iv * T, type: one?.type === 'call' || one?.type === 'put' ? one.type : null,
      bid: one && isPos(one.bid) ? one.bid : null, ask: one && isPos(one.ask) ? one.ask : null };
  }).sort((a, b) => a.k - b.k);
  if (nodes.length < MIN_SMILE_NODES) return { smile: null, reason: nodes.length === 0 && slice.reason ? slice.reason : 'smile_too_short', duplicates };
  const kMin = nodes[0].k, kMax = nodes[nodes.length - 1].k, eps = 1e-12;
  const w = (k: number): number | null => {
    if (!isNum(k) || k < kMin - eps || k > kMax + eps) return null;
    if (k <= kMin) return nodes[0].w;
    for (let i = 1; i < nodes.length; i++) {
      const a = nodes[i - 1], b = nodes[i];
      if (k <= b.k) return a.w + (b.w - a.w) * (k - a.k) / (b.k - a.k);
    }
    return nodes[nodes.length - 1].w;
  };
  const sigma = (k: number) => { const v = w(k); return v === null || !(v > 0) ? null : Math.sqrt(v / T); };
  return { smile: { expiry: slice.expiry, T, F, source: slice.source, nodes, kMin, kMax, junction: slice.junction ?? null,
    qualified: slice.qualified !== false, discount: isPos(slice.discount) ? slice.discount : null, duplicates, outOfRange, w, sigma }, reason: null, duplicates };
}

/**
 * Smile OSSERVATO di una scadenza dalla chain: OTM rispetto al FORWARD (put K < F, call K ≥ F),
 * IV del provider (Polygon: modello/tasso/dividendi non documentati). Esclusioni: le stesse
 * quoteDefect del forward (rettificati, moltiplicatore ≠ 100, OI e volume nulli, bid o ask
 * mancanti, quota incrociata, spread relativo > maxRelSpread) più IV fuori IV_RANGE; contate
 * per motivo in `excluded`. NON replicate dal builder: l'eccezione dei 3 tick e le quote vecchie
 * (STALE_QUOTE_SECONDS). Duplicati (tipo, strike): OI maggiore, poi spread più stretto.
 * I punti portano tipo, bid e ask: i butterfly/verticali si controllano in PREZZO ESEGUIBILE.
 * GIUNZIONE: a K = F lo smile passa dalle IV put alle IV call del provider; la parità dice che a
 * pari strike devono coincidere, ma il provider le calcola separatamente (e su americane): un
 * salto lì è rumore del provider più che del mercato. Gli arbitraggi a cavallo della giunzione
 * escono con origin 'junction'. Senza forward non c'è giunzione: smile vuoto, motivo dichiarato.
 * `r` facoltativo: dà lo sconto e^{−rT} per i butterfly eseguibili a cavallo della giunzione.
 */
export function smileFromChain(expiry: string, T: number | null, contracts: ParityContract[], F: number | null,
  options: { maxRelSpread?: number; r?: number | null } = {}): SmileSlice {
  const slice: SmileSlice = { expiry, T: isT(T) ? T : null, points: [], source: 'observed', excluded: {}, junction: null,
    discount: isT(T) && isNum(options.r) && Math.abs(options.r) <= MAX_ABS_RATE ? Math.exp(-options.r * T) : null };
  if (!isPos(F)) return { ...slice, reason: 'forward_missing' };
  slice.junction = { strike: F, putsInclusive: false };
  const ex = slice.excluded as Record<string, number>;
  const otm = objs<ParityContract>(contracts).filter(c => !isPos(c.strike) || c.type === ((c.strike as number) < F ? 'put' : 'call'));
  const best = bestQuotes(otm, options.maxRelSpread ?? QUOTE_MAX_REL_SPREAD, c => isIv(c.iv) ? null : 'iv_out_of_range', ex);
  slice.points = [...best.values()].map(c => ({ strike: c.strike as number, iv: c.iv as number, type: c.type as OptionType, bid: c.bid, ask: c.ask }))
    .sort((a, b) => a.strike - b.strike);
  return slice;
}

/** Superficie OSSERVATA (la fonte predefinita della griglia delta): uno smileFromChain per
 *  scadenza col forward di `forwards` (il suo motivo se manca) e lo sconto da `r`. */
export function surfaceFromChains(chains: { expiry: string; T: number | null; contracts: ParityContract[] }[], forwards: Forwards,
  options: { asOf?: string | null; spot?: number | null; r?: number | null; maxRelSpread?: number } = {}): SmileSurface {
  return { asOf: options.asOf ?? null, spot: isPos(options.spot) ? options.spot : null, reason: null,
    slices: objs<{ expiry: string; T: number | null; contracts: ParityContract[] }>(chains).map(c => {
      const fi = forwardInfo(forwards, c.expiry);
      const s = smileFromChain(c.expiry, c.T, c.contracts, fi.F, { r: options.r, maxRelSpread: options.maxRelSpread });
      return fi.F === null ? { ...s, reason: fi.reason } : s;
    }) };
}

/** Payload /options/download/{id}/surface (o build_vol_surface) → SmileSurface su strike assoluti
 *  K = (K/S)·spot_est. ALTERNATIVA dichiarata alla superficie osservata: la griglia K/S 0,80–1,20
 *  oltre i 3 mesi lascia quasi tutte le ali 10Δ/25Δ n.d. Celle null restano fuori dallo smile;
 *  spot n.d. = smile vuoti (mai K/S×0). `error` = superficie vuota col motivo. `spot_proxy`
 *  (spot = uno strike) = fette vuote, motivo 'spot_proxy'. `iv_grid_qualified: false` = fette
 *  marcate `qualified: false`: gli strike restano giusti (sono quelli su cui il builder ha
 *  costruito la griglia), ma lo spot non è allineato e la giunzione a K = S può essere fuori posto. */
export function surfaceFromBuilder(payload: any): SmileSurface {
  const spot = isPos(payload?.spot_est) ? payload.spot_est as number : null;
  const asOf = typeof payload?.snapshot_at === 'string' ? payload.snapshot_at : null;
  if (payload?.error) return { asOf, spot, slices: [], reason: 'surface_error', detail: String(payload.error) };
  const grid: unknown[] = Array.isArray(payload?.moneyness_grid) ? payload.moneyness_grid : [];
  const proxy = Boolean(payload?.spot_proxy), surfaceQualified = payload?.iv_grid_qualified !== false;
  const slices: SmileSlice[] = objs<any>(payload?.slices).map(s => ({
    expiry: String(s.expiry ?? ''), T: isT(s.t_years) ? s.t_years : null, days: isNum(s.days) ? s.days : null, source: 'grid' as const,
    qualified: surfaceQualified && s.iv_grid_qualified !== false,
    reason: proxy ? 'spot_proxy' as const : spot === null ? 'spot_missing' as const : null,
    junction: spot === null ? null : { strike: spot, putsInclusive: true },
    points: spot === null || proxy ? [] : grid.flatMap((m, i) => isPos(m) && isPos(s.iv_grid?.[i]) ? [{ strike: (m as number) * spot, iv: s.iv_grid[i] as number }] : []),
  }));
  return { asOf, spot, slices, reason: null };
}

/* ---------------------------------------------------------------- 3. griglia delta */
export interface DeltaCell {
  key: DeltaKey; iv: number | null; strike: number | null; k: number | null;
  /** delta forward della cella (per ATM: N(σ√T/2), il delta della call a K = F) */
  delta: number | null; reason: ReasonCode | null; detail: string | null;
}
export interface DeltaRow {
  expiry: string; T: number | null; days: number | null; forward: number | null; source: SmileSource;
  /** false = griglia del builder dichiarata non qualificata: mostrare l'etichetta */
  qualified: boolean;
  /** strike duplicati mediati, IV fuori IV_RANGE scartate (dichiarati, mai scelti a caso) */
  duplicates: number; outOfRange: number;
  kRange: [number, number] | null; reason: ReasonCode | null; cells: Record<DeltaKey, DeltaCell>;
}
export interface DeltaGrid {
  deltaConvention: 'forward-undiscounted'; atmConvention: 'ATMF (K = F)'; interpolation: 'total-variance-linear-in-k';
  columns: DeltaKey[]; rows: DeltaRow[];
}
const naCells = (reason: ReasonCode, detail: string | null = null) =>
  Object.fromEntries(DELTA_COLUMNS.map(c => [c.key, { key: c.key, iv: null, strike: null, k: null, delta: null, reason, detail }])) as Record<DeltaKey, DeltaCell>;

/** IV ai delta standard per scadenza, sullo smile della fonte della superficie (dichiarata in `source`). */
export function deltaGrid(surface: SmileSurface, forwards: Forwards): DeltaGrid {
  const rows = objs<SmileSlice>(surface?.slices).sort((a, b) => (isT(a.T) ? a.T : Infinity) - (isT(b.T) ? b.T : Infinity)).map((slice): DeltaRow => {
    const { F, reason: fReason } = forwardInfo(forwards, slice.expiry);
    const row = { expiry: slice.expiry, T: isT(slice.T) ? slice.T : null, days: isNum(slice.days) ? slice.days : null, forward: F, source: slice.source, qualified: slice.qualified !== false };
    const { smile, reason, duplicates } = buildSmile(slice, F, fReason);
    if (!smile) return { ...row, kRange: null, reason, duplicates, outOfRange: 0, cells: naCells(reason as ReasonCode) };
    const cells = {} as Record<DeltaKey, DeltaCell>;
    for (const col of DELTA_COLUMNS) {
      if (col.type === null) {
        const iv = smile.sigma(0);
        cells[col.key] = iv === null
          ? { key: col.key, iv: null, strike: null, k: null, delta: null, reason: 'outside_smile', detail: `k=0 outside [${smile.kMin.toFixed(4)}, ${smile.kMax.toFixed(4)}]` }
          : { key: col.key, iv, strike: smile.F, k: 0, delta: normCdf(iv * Math.sqrt(smile.T) / 2), reason: null, detail: null };
        continue;
      }
      const r = strikeForDelta(smile.F, smile.T, smile.sigma, col.delta, col.type, { kMin: smile.kMin, kMax: smile.kMax, nodes: smile.nodes.map(n => n.k) });
      cells[col.key] = { key: col.key, iv: r.iv, strike: r.strike, k: r.k, delta: r.delta, reason: r.reason, detail: r.detail };
    }
    return { ...row, kRange: [smile.kMin, smile.kMax], reason: null, duplicates, outOfRange: smile.outOfRange, cells };
  });
  return { deltaConvention: 'forward-undiscounted', atmConvention: 'ATMF (K = F)', interpolation: 'total-variance-linear-in-k',
    columns: DELTA_COLUMNS.map(c => c.key), rows };
}

/* ---------------------------------------------------------------- 4. skew */
export interface SkewRow {
  /** atmf = IV a K = F (ATMF), BF = smile BF: v. LABELS */
  expiry: string; T: number | null; atmf: number | null;
  rr25: number | null; bf25: number | null; rr10: number | null; bf10: number | null;
  /** RR25 / IV ATM, segno conservato (negativo = put più care): convenzione di
   *  soglie_score.skew_normalizzato (ramo opus55/soglie). null se l'ATM manca o è ≤ 0. */
  skewNorm25: number | null;
  reasons: Partial<Record<'rr25' | 'bf25' | 'rr10' | 'bf10' | 'skewNorm25', ReasonCode>>;
}
/** RR = IV call − IV put allo stesso |Δ|; BF = (IV call + IV put)/2 − IV ATM. */
export function skewMetrics(grid: DeltaGrid): SkewRow[] {
  return objs<DeltaRow>(grid?.rows).map(row => {
    const cells = (row.cells && typeof row.cells === 'object' ? row.cells : {}) as Partial<Record<DeltaKey, DeltaCell>>;
    const iv = (k: DeltaKey) => { const v = cells[k]?.iv; return isPos(v) ? v : null; };
    const why = (...ks: DeltaKey[]) => ks.map(k => cells[k]?.reason).find(Boolean) ?? row.reason ?? 'iv_missing';
    const atm = iv('ATMF'), c25 = iv('25C'), p25 = iv('25P'), c10 = iv('10C'), p10 = iv('10P');
    const reasons: SkewRow['reasons'] = {};
    const rr25 = c25 !== null && p25 !== null ? c25 - p25 : (reasons.rr25 = why('25C', '25P'), null);
    const rr10 = c10 !== null && p10 !== null ? c10 - p10 : (reasons.rr10 = why('10C', '10P'), null);
    const bf25 = c25 !== null && p25 !== null && atm !== null ? (c25 + p25) / 2 - atm : (reasons.bf25 = why('25C', '25P', 'ATMF'), null);
    const bf10 = c10 !== null && p10 !== null && atm !== null ? (c10 + p10) / 2 - atm : (reasons.bf10 = why('10C', '10P', 'ATMF'), null);
    const skewNorm25 = rr25 !== null && atm !== null && atm > 0 ? rr25 / atm : (reasons.skewNorm25 = rr25 === null ? reasons.rr25 : why('ATMF'), null);
    return { expiry: row.expiry, T: isT(row.T) ? row.T : null, atmf: atm, rr25, bf25, rr10, bf10, skewNorm25, reasons };
  });
}

/* ---------------------------------------------------------------- 5. vol forward */
export interface TermPoint { expiry: string; T: number | null; iv: number | null }
export interface ForwardVolPair {
  from: string; to: string; T1: number | null; T2: number | null;
  /** σ_fwd = √((σ2²T2 − σ1²T1)/(T2 − T1)); null se il radicando è < 0 o manca un dato */
  forwardVol: number | null;
  /** il radicando (varianza forward annua): negativo = arbitraggio di calendario */
  forwardVariance: number | null;
  calendarArbitrage: boolean;
  /** entità: w1 − w2 (varianza totale persa), > 0 solo con arbitraggio */
  varianceDrop: number | null;
  /** l'arbitraggio resta anche spostando ciascuna IV di ±ivNoise nel verso che lo cancella? */
  beyondNoise: boolean;
  reason: ReasonCode | null;
}
/** Vol forward fra scadenze CONSECUTIVE (ordinate per T) in varianza totale. Un buco interrompe
 *  la coppia (n.d.), non viene saltato: «consecutive» resta la verità della superficie. */
export function forwardVol(term: TermPoint[], options: { ivNoise?: number } = {}): ForwardVolPair[] {
  const eps = options.ivNoise ?? IV_NOISE;
  const pts = objs<TermPoint>(term).sort((a, b) => (isT(a.T) ? a.T : Infinity) - (isT(b.T) ? b.T : Infinity));
  const out: ForwardVolPair[] = [];
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1], b = pts[i];
    const pair: ForwardVolPair = { from: a.expiry, to: b.expiry, T1: isT(a.T) ? a.T : null, T2: isT(b.T) ? b.T : null, forwardVol: null, forwardVariance: null,
      calendarArbitrage: false, varianceDrop: null, beyondNoise: false, reason: null };
    if (!isNum(eps) || eps < 0) { out.push({ ...pair, reason: 'invalid_input' }); continue; }
    if (!isT(a.T) || !isT(b.T)) { out.push({ ...pair, reason: !isT(a.T) ? tReason(a.T) : tReason(b.T) }); continue; }
    if (!isPos(a.iv) || !isPos(b.iv)) { out.push({ ...pair, reason: 'iv_missing' }); continue; }
    if (!isIv(a.iv) || !isIv(b.iv)) { out.push({ ...pair, reason: 'iv_out_of_range' }); continue; }
    if (!(b.T > a.T)) { out.push({ ...pair, reason: 'same_time' }); continue; }
    const w1 = a.iv * a.iv * a.T, w2 = b.iv * b.iv * b.T, fv = (w2 - w1) / (b.T - a.T);
    if (fv < 0) {
      const lo1 = Math.max(a.iv - eps, 0);
      out.push({ ...pair, forwardVariance: fv, calendarArbitrage: true, varianceDrop: w1 - w2,
        beyondNoise: (b.iv + eps) ** 2 * b.T < lo1 * lo1 * a.T, reason: 'calendar_arbitrage' });
    } else out.push({ ...pair, forwardVariance: fv, forwardVol: Math.sqrt(fv) });
  }
  return out;
}
/** Vol forward a delta costante (colonne della griglia; 'ATMF' = K = F). A delta costante gli
 *  strike delle due scadenze differiscono: è la lettura di desk, non un controllo di arbitraggio
 *  (quello è a k costante in arbitrageChecks). */
export function forwardVolByDelta(grid: DeltaGrid, options: { ivNoise?: number } = {}): Record<DeltaKey, ForwardVolPair[]> {
  return Object.fromEntries(DELTA_COLUMNS.map(c => [c.key, forwardVol(objs<DeltaRow>(grid?.rows).map(r => ({ expiry: r.expiry, T: r.T, iv: r.cells?.[c.key]?.iv ?? null })), options)])) as Record<DeltaKey, ForwardVolPair[]>;
}

/* ---------------------------------------------------------------- 6. arbitraggi */
export type ArbitrageKind = 'calendar' | 'butterfly' | 'vertical' | 'density';
export interface ArbitrageFlag {
  kind: ArbitrageKind;
  /** 'nodes' = l'anomalia sta nei valori dei nodi (osservati o griglia del builder: v. source) e
   *  resta qualunque interpolazione monotona fra nodi; 'interpolation' = nasce dall'interpolazione;
   *  'junction' = coinvolge nodi dei due lati della giunzione put/call (IV put e IV call del
   *  provider calcolate separatamente: può essere rumore del provider, non del mercato) */
  origin: 'nodes' | 'interpolation' | 'junction';
  /** 'executable' = provato su bid/ask veri (comprabile a costo negativo); 'model' = su prezzi
   *  Black-76 dalle IV con la tolleranza di rumore */
  test: 'executable' | 'model';
  /** fonte degli smile coinvolti: 'observed' = quote vere, 'grid' = griglia già lisciata del builder */
  source: SmileSource | 'mixed';
  expiries: string[]; strikes: number[]; k: number[];
  /** entità nell'unità di `unit` (sempre > 0) */
  magnitude: number; unit: 'total-variance' | 'price/F' | 'price' | 'slope' | 'g(k)' | 'probability';
  /** per il calendario: punti di vol che mancano alla scadenza lunga (√(w1/T2) − σ2) */
  volPoints: number | null;
  /** margine concesso al rumore, NELLA STESSA UNITÀ di magnitude (segnale ⇔ magnitude > tolerance;
   *  0 sui test eseguibili: lo spread è già pagato) */
  tolerance: number;
  /** il rumore di IV all'ATM da cui la tolleranza discende (punti vol, decimale) */
  ivNoise: number;
  /** true = segnale di MODELLO (butterfly/verticale/densità) su nodi che hanno tutti bid/ask
   *  veri: lì fa fede il test eseguibile, quindi il segnale è solo indicativo e non rende la
   *  superficie «sporca» (fuori campione il modello segnalava 10/40 superfici a rumore 0,3/1,5 e
   *  39/40 a 0,5/2,0, l'eseguibile 0/40) */
  indicative: boolean;
}
export interface ArbitrageReport {
  flags: ArbitrageFlag[];
  /** true = controllato e pulito (i segnali `indicative` non contano); false = segnali; null = NON
   *  controllato (nessuna scadenza utilizzabile o opzioni non valide) */
  clean: boolean | null;
  cleanReason: ReasonCode | null;
  /** false = nessuna coppia di scadenze confrontabile: il calendario non è stato controllato */
  calendarChecked: boolean;
  checked: { expiries: string[]; calendarPairs: number; butterflyTriplets: number; executableTriplets: number };
  skipped: { expiry: string; reason: ReasonCode }[];
  /** ivNoise null = valore passato non valido (non riecheggiato) */
  tolerances: { ivNoise: number | null; rule: string };
}
export interface ArbitrageOptions { ivNoise?: number; subdivisions?: number }

/** Rumore di IV di un nodo: ε·(1 + WING_NOISE_SLOPE·|k|/(σ√T)). Le ali hanno spread in vol più
 *  larghi (vega piccola). WING_NOISE_SLOPE = 1,5 è TARATO su un esperimento (SSVI + rumore
 *  uniforme 0,3 punti ATM → 1,5 ali a 2 deviazioni standard, 40 superfici, strike ogni 1% e
 *  2,5% in k: 0/40 superfici con segnali; con pendenza 1: 20/40 e 4/40), non misurato su spread
 *  veri: fuori campione (rumore 0,5 → 2,0) il modello segnala ancora (39/40), per questo dove ci
 *  sono bid/ask fa fede il test eseguibile e i segnali di modello sono `indicative`. Conseguenza:
 *  sulle ali il controllo di modello vede solo anomalie più grandi del rumore ammesso (≈ lo
 *  spread in vol). Usato da butterfly, verticale e densità; il calendario usa ε costante. */
export const WING_NOISE_SLOPE = 1.5;
export const nodeIvNoise = (eps: number, k: number, iv: number, T: number, slope = WING_NOISE_SLOPE) => eps * (1 + slope * Math.abs(k) / (iv * Math.sqrt(T)));
/** Pendenza del rumore nel CALENDARIO: CAL_NOISE_SLOPE (misurata, v. arbitrageChecks). */
export const CAL_NOISE_SLOPE = 0;

/**
 * Controlli di arbitraggio statico sulla superficie, a moneyness forward k = ln(K/F).
 * TOLLERANZA (test sul modello): un'anomalia si segnala solo se sopravvive spostando OGNI IV
 * coinvolta del suo rumore ε_i = ivNoise·(1 + 1,5·|k_i|/(σ_i√T)) nel verso che la cancella
 * (ivNoise = 0,5 punti all'ATM; v. nodeIvNoise). Tolleranza riportata nella
 * stessa unità dell'entità. ivNoise non finito o negativo = n.d. ('invalid_input').
 *  - CALENDARIO: w(k,T2) ≥ w(k,T1) per T2 > T1 a k fisso. Segnale se (σ2+ε)²T2 < (σ1−ε)²T1 con
 *    ε = ivNoise COSTANTE (CAL_NOISE_SLOPE = 0, misurato sull'esperimento del revisore: SSVI con
 *    rumore 0,3 punti ATM → 1,5 ali, 40 superfici: 0 falsi positivi e 40/40 inversioni da 1 punto
 *    rilevate; con il rumore crescente nelle ali le inversioni da 1 punto rilevate erano 36/40).
 *    Coppie consecutive su tutta la sovrapposizione; coppie NON consecutive solo sui k che
 *    nessuna scadenza intermedia copre. 'nodes' se al nodo di una scadenza la violazione regge
 *    contro ENTRAMBI i nodi che lo racchiudono nell'altra, altrimenti 'interpolation'. Un segnale
 *    per tratto contiguo di k. LIMITE: a k fisso è esatto con dividendi proporzionali; con
 *    dividendi in contanti fra T1 e T2 è un'approssimazione.
 *  - BUTTERFLY e VERTICALE in PREZZO ESEGUIBILE quando i tre (due) nodi portano bid/ask (smile
 *    osservato): λ1·A1 − B2 + λ3·A3 < 0 (A = ask, B = bid; stesso tipo, o put convertite in call
 *    con C = P + e^{−rT}(F − K): F si elide, serve solo lo sconto); verticale: comprare lo spread
 *    a credito, o venderlo oltre e^{−rT}·ΔK (senza sconto: ΔK, valido con r ≥ 0). Altrimenti sul
 *    MODELLO: prezzi call Black-76 normalizzati c = C/F, B = λ1·c1 − c2 + λ3·c3 < −Σλ·vega·ε;
 *    pendenza (c2 − c1)/(K̃2 − K̃1) fuori da [−1, 0] oltre (v1ε1 + v2ε2)/ΔK̃.
 *  - DENSITÀ dell'interpolante ('interpolation'), due parti, entrambe con tolleranza ε:
 *    a) dentro un tratto w è lineare (w'' = 0): Durrleman g(k) = (1 − k·w'/(2w))² −
 *       (w'²/4)(1/w + 1/4) ≥ 0, in `subdivisions` punti interni; segnale solo se g resta < 0
 *       con la pendenza ridotta e w alzato dal rumore dei due nodi;
 *    b) a ogni nodo interno la pendenza di w salta di Δw': massa puntuale della densità
 *       m = φ(d1)·Δw'/(2√w·K̃) (probabilità). Gomito concavo = massa negativa: segnale se resta
 *       negativa alzando di ε le IV dei vicini e abbassando quella del nodo.
 */
export function arbitrageChecks(surface: SmileSurface, forwards: Forwards, options: ArbitrageOptions = {}): ArbitrageReport {
  const eps = options.ivNoise ?? IV_NOISE, sub = Math.max(1, Math.floor(isNum(options.subdivisions) ? options.subdivisions : 4));
  const rule = 'flag only if the anomaly survives moving every IV involved by its noise against it: ivNoise·(1 + 1.5·|k|/(σ√T)) for butterfly/vertical/density, constant ivNoise for the calendar; bid/ask triplets are tested on executable prices';
  const flags: ArbitrageFlag[] = [], skipped: ArbitrageReport['skipped'] = [], smiles: Smile[] = [];
  if (!isNum(eps) || eps < 0)
    return { flags, clean: null, cleanReason: 'invalid_input', calendarChecked: false,
      checked: { expiries: [], calendarPairs: 0, butterflyTriplets: 0, executableTriplets: 0 }, skipped, tolerances: { ivNoise: null, rule } };
  for (const s of objs<SmileSlice>(surface?.slices)) {
    const fi = forwardInfo(forwards, s.expiry);
    const { smile, reason } = buildSmile(s, fi.F, fi.reason);
    if (smile) smiles.push(smile); else skipped.push({ expiry: s.expiry, reason: reason as ReasonCode });
  }
  smiles.sort((a, b) => a.T - b.T);
  let triplets = 0, executable = 0, pairs = 0;
  const callN = (k: number, T: number, iv: number) => forwardPrice('call', 1, Math.exp(k), T, iv) as number;
  const vegaN = (k: number, T: number, iv: number) => forwardVega(1, Math.exp(k), T, iv);
  const originOf = (sm: Smile, strikes: number[], fallback: ArbitrageFlag['origin']): ArbitrageFlag['origin'] => {
    const j = sm.junction;
    if (!j) return fallback;
    const put = (K: number) => j.putsInclusive ? K <= j.strike : K < j.strike;
    return strikes.some(put) && strikes.some(K => !put(K)) ? 'junction' : fallback;
  };

  for (const sm of smiles) {
    const n = sm.nodes;
    const c = n.map(x => callN(x.k, sm.T, x.iv)), v = n.map(x => vegaN(x.k, sm.T, x.iv)), kt = n.map(x => Math.exp(x.k));
    const e = n.map(x => nodeIvNoise(eps, x.k, x.iv, sm.T));
    const one = (kind: ArbitrageKind, origin: ArbitrageFlag['origin'], test: ArbitrageFlag['test'], idx: number[], magnitude: number,
      unit: ArbitrageFlag['unit'], tolerance: number): ArbitrageFlag => {
      const strikes = idx.map(i => n[i].strike);
      return { kind, origin: originOf(sm, strikes, origin), test, source: sm.source, expiries: [sm.expiry], strikes, k: idx.map(i => n[i].k),
        magnitude, unit, volPoints: null, tolerance, ivNoise: eps,
        indicative: test === 'model' && idx.every(i => n[i].bid !== null && n[i].ask !== null) };
    };
    // quote eseguibili di un gruppo di nodi, nella stessa specie (tutte call o tutte put, o call via sconto)
    const quotes = (idx: number[]): { kind: OptionType; bid: number[]; ask: number[] } | null => {
      const ns = idx.map(i => n[i]);
      if (!ns.every(x => x.type && x.bid !== null && x.ask !== null)) return null;
      const types = new Set(ns.map(x => x.type));
      if (types.size === 1) return { kind: ns[0].type as OptionType, bid: ns.map(x => x.bid as number), ask: ns.map(x => x.ask as number) };
      if (sm.discount === null) return null;
      const shift = (x: Node) => x.type === 'put' ? (sm.discount as number) * (sm.F - x.strike) : 0;
      return { kind: 'call', bid: ns.map(x => (x.bid as number) + shift(x)), ask: ns.map(x => (x.ask as number) + shift(x)) };
    };
    for (let i = 0; i + 1 < n.length; i++) {
      const qt = quotes([i, i + 1]), dK = n[i + 1].strike - n[i].strike, maxPay = (sm.discount ?? 1) * dK;
      if (qt) {
        // call: C1 − C2 ∈ [0, D·ΔK]; put: P2 − P1 ∈ [0, D·ΔK]
        const [lo, hi] = qt.kind === 'call' ? [0, 1] : [1, 0];
        const buyCredit = -(qt.ask[lo] - qt.bid[hi]), sellOver = (qt.bid[lo] - qt.ask[hi]) - maxPay;
        const excess = Math.max(buyCredit, sellOver);
        if (excess > 0) flags.push(one('vertical', 'nodes', 'executable', [i, i + 1], excess, 'price', 0));
        continue;
      }
      const slope = (c[i + 1] - c[i]) / (kt[i + 1] - kt[i]), tol = (e[i] * v[i] + e[i + 1] * v[i + 1]) / (kt[i + 1] - kt[i]);
      const excess = slope > 0 ? slope : slope < -1 ? -1 - slope : 0;
      if (excess > tol) flags.push(one('vertical', 'nodes', 'model', [i, i + 1], excess, 'slope', tol));
    }
    for (let i = 1; i + 1 < n.length; i++) {
      triplets++;
      const qt = quotes([i - 1, i, i + 1]);
      const K1 = n[i - 1].strike, K2 = n[i].strike, K3 = n[i + 1].strike;
      if (qt) {
        executable++;
        const L1 = (K3 - K2) / (K3 - K1), L3 = (K2 - K1) / (K3 - K1);
        const cost = L1 * qt.ask[0] - qt.bid[1] + L3 * qt.ask[2];
        if (cost < 0) flags.push(one('butterfly', 'nodes', 'executable', [i - 1, i, i + 1], -cost, 'price', 0));
        continue;
      }
      const l1 = (kt[i + 1] - kt[i]) / (kt[i + 1] - kt[i - 1]), l3 = (kt[i] - kt[i - 1]) / (kt[i + 1] - kt[i - 1]);
      const B = l1 * c[i - 1] - c[i] + l3 * c[i + 1], tol = l1 * v[i - 1] * e[i - 1] + v[i] * e[i] + l3 * v[i + 1] * e[i + 1];
      if (B < -tol) flags.push(one('butterfly', 'nodes', 'model', [i - 1, i, i + 1], -B, 'price/F', tol));
    }
    const wUp = (i: number) => (n[i].iv + e[i]) ** 2 * sm.T, wDn = (i: number) => Math.max(n[i].iv - e[i], 0) ** 2 * sm.T;
    const g = (k: number, w: number, wp: number) => (1 - k * wp / (2 * w)) ** 2 - (wp * wp / 4) * (1 / w + 1 / 4);
    for (let i = 0; i + 1 < n.length; i++) {
      const a = n[i], b = n[i + 1], h = b.k - a.k, wp = (b.w - a.w) / h;
      // pendenza ridotta in modulo dal rumore dei due nodi, livello alzato
      const wpFav = wp > 0 ? Math.max((wDn(i + 1) - wUp(i)) / h, 0) : Math.min((wUp(i + 1) - wDn(i)) / h, 0);
      let worst: { g: number; gFav: number } | null = null;
      for (let j = 1; j <= sub; j++) {
        const t = j / (sub + 1), k = a.k + h * t, w = a.w + wp * h * t, wF = wUp(i) + (wUp(i + 1) - wUp(i)) * t;
        const gv = g(k, w, wp), gF = g(k, wF, wpFav);
        if (gv < 0 && gF < 0 && (!worst || gv < worst.g)) worst = { g: gv, gFav: gF };
      }
      if (worst) flags.push(one('density', 'interpolation', 'model', [i, i + 1], -worst.g, 'g(k)', worst.gFav - worst.g));
    }
    for (let i = 1; i + 1 < n.length; i++) {
      const p = n[i - 1], x = n[i], q = n[i + 1];
      const dw = (q.w - x.w) / (q.k - x.k) - (x.w - p.w) / (x.k - p.k);
      if (!(dw < 0)) continue;
      const dwFav = (wUp(i + 1) - wDn(i)) / (q.k - x.k) - (wDn(i) - wUp(i - 1)) / (x.k - p.k);
      if (!(dwFav < 0)) continue;
      const coef = normPdf((-x.k + x.w / 2) / Math.sqrt(x.w)) / (2 * Math.sqrt(x.w) * Math.exp(x.k));
      flags.push(one('density', 'interpolation', 'model', [i - 1, i, i + 1], -coef * dw, 'probability', coef * (dwFav - dw)));
    }
  }

  // calendario: consecutive su tutta la sovrapposizione, non consecutive dove nessuna intermedia copre k
  for (let i = 0; i < smiles.length; i++) for (let j = i + 1; j < smiles.length; j++) {
    const s1 = smiles[i], s2 = smiles[j];
    if (!(s2.T > s1.T)) { if (j === i + 1) skipped.push({ expiry: s2.expiry, reason: 'same_time' }); continue; }
    const mids = smiles.filter(m => m.T > s1.T && m.T < s2.T);
    const lo = Math.max(s1.kMin, s2.kMin), hi = Math.min(s1.kMax, s2.kMax);
    if (!(hi > lo)) continue;
    const node1 = new Set(s1.nodes.map(x => x.k)), node2 = new Set(s2.nodes.map(x => x.k));
    const ks = new Set<number>([lo, hi]);
    for (const x of [...s1.nodes, ...s2.nodes]) if (x.k >= lo && x.k <= hi) ks.add(x.k);
    for (const m of mids) for (const b of [m.kMin, m.kMax]) if (b > lo && b < hi) ks.add(b);
    const base = [...ks].sort((a, b) => a - b);
    for (let u = 0; u + 1 < base.length; u++) for (let m = 1; m <= sub; m++) ks.add(base[u] + (base[u + 1] - base[u]) * m / (sub + 1));
    const grid = [...ks].sort((a, b) => a - b).filter(k => !mids.some(m => k >= m.kMin && k <= m.kMax));
    if (!grid.length) continue;
    pairs++;
    const lo1 = (k: number, iv: number) => Math.max(iv - nodeIvNoise(eps, k, iv, s1.T, CAL_NOISE_SLOPE), 0) ** 2 * s1.T;
    const hi2 = (k: number, iv: number) => (iv + nodeIvNoise(eps, k, iv, s2.T, CAL_NOISE_SLOPE)) ** 2 * s2.T;
    const bracket = (sm: Smile, k: number) => {
      const b = sm.nodes.findIndex(x => x.k >= k);
      return b < 0 ? [sm.nodes[sm.nodes.length - 1]] : b === 0 || sm.nodes[b].k === k ? [sm.nodes[b]] : [sm.nodes[b - 1], sm.nodes[b]];
    };
    let run: ArbitrageFlag | null = null, prevK: number | null = null;
    const close = () => {
      if (run) {
        run.strikes = [s1.F * Math.exp(run.k[0]), s1.F * Math.exp(run.k[1]), s2.F * Math.exp(run.k[0]), s2.F * Math.exp(run.k[1])];
        flags.push(run); run = null;
      }
    };
    for (const k of grid) {
      if (prevK !== null && mids.some(m => m.kMin > (prevK as number) && m.kMin <= k)) close();
      prevK = k;
      const v1 = s1.sigma(k), v2 = s2.sigma(k);
      if (v1 === null || v2 === null || !(hi2(k, v2) < lo1(k, v1))) { close(); continue; }
      const robust = (node1.has(k) && bracket(s2, k).every(x => hi2(x.k, x.iv) < lo1(k, v1)))
        || (node2.has(k) && bracket(s1, k).every(x => hi2(k, v2) < lo1(x.k, x.iv)));
      const drop = v1 * v1 * s1.T - v2 * v2 * s2.T, volPts = Math.sqrt(v1 * v1 * s1.T / s2.T) - v2;
      const tolW = (v1 * v1 * s1.T - lo1(k, v1)) + (hi2(k, v2) - v2 * v2 * s2.T);
      if (!run) run = { kind: 'calendar', origin: 'interpolation', test: 'model', source: s1.source === s2.source ? s1.source : 'mixed',
        expiries: [s1.expiry, s2.expiry], strikes: [], k: [k, k], magnitude: drop, unit: 'total-variance', volPoints: volPts, tolerance: tolW, ivNoise: eps, indicative: false };
      const r = run as ArbitrageFlag;
      r.k[1] = k;
      if (robust) r.origin = 'nodes';
      if (drop > r.magnitude) { r.magnitude = drop; r.volPoints = volPts; r.tolerance = tolW; }
    }
    close();
  }
  const checkedAny = smiles.length > 0;
  return { flags, clean: checkedAny ? flags.every(f => f.indicative) : null,
    cleanReason: checkedAny ? null : (skipped[0]?.reason ?? 'iv_missing'), calendarChecked: pairs > 0,
    checked: { expiries: smiles.map(s => s.expiry), calendarPairs: pairs, butterflyTriplets: triplets, executableTriplets: executable },
    skipped, tolerances: { ivNoise: eps, rule } };
}

/* ---------------------------------------------------------------- 7. diff fra istantanee */
export interface MoneynessSurface {
  asOf: string | null; spot: number | null; moneyness: number[];
  slices: { expiry: string; T: number | null; iv: (number | null)[] }[];
  /** false = griglia K/S su uno spot dichiarato non qualificato dal builder (iv_grid_qualified) */
  qualified?: boolean;
  /** spot_qualified / spot_fallback del builder, così come arrivano (null = assenti) */
  spotQualified?: boolean | null; spotFallback?: boolean | null;
  reason?: ReasonCode | null;
}
/** Payload del builder → griglia K/S spot × scadenza (celle non valide = null). `error` o spot
 *  proxy = nessuna fetta, col motivo; flag di qualifica portati come arrivano. */
export function moneynessSurfaceFromBuilder(payload: any): MoneynessSurface {
  const moneyness: number[] = (Array.isArray(payload?.moneyness_grid) ? payload.moneyness_grid : []).map((m: unknown) => isPos(m) ? m as number : NaN);
  const asOf = typeof payload?.snapshot_at === 'string' ? payload.snapshot_at : null, spot = isPos(payload?.spot_est) ? payload.spot_est : null;
  const flagsOf = { spotQualified: typeof payload?.spot_qualified === 'boolean' ? payload.spot_qualified : null,
    spotFallback: typeof payload?.spot_fallback === 'boolean' ? payload.spot_fallback : null };
  if (payload?.error) return { asOf, spot, moneyness, slices: [], qualified: false, ...flagsOf, reason: 'surface_error' };
  if (payload?.spot_proxy) return { asOf, spot, moneyness, slices: [], qualified: false, ...flagsOf, reason: 'spot_proxy' };
  return { asOf, spot, moneyness, qualified: payload?.iv_grid_qualified !== false, ...flagsOf, reason: null,
    slices: objs<any>(payload?.slices).map(s => ({ expiry: String(s.expiry ?? ''), T: isT(s.t_years) ? s.t_years : null,
      iv: moneyness.map((_, i) => isIv(s.iv_grid?.[i]) ? s.iv_grid[i] as number : null) })) };
}
type Cell = { iv: number | null; reason: ReasonCode | null };
/** IV a tenor costante T* da una colonna di celle per scadenza: varianza totale lineare in T fra
 *  le due scadenze CONSECUTIVE che racchiudono T* (un buco in una delle due = n.d., non si salta
 *  alla successiva); scadenza esatta = il suo valore; w decrescente fra le due = n.d.
 *  'calendar_arbitrage'; due scadenze con lo stesso T = 'same_time'; fuori = 'outside_term'. */
function ivAtTenor(rows: { T: number; cell: Cell }[], T: number): Cell {
  const sl = [...rows].sort((a, b) => a.T - b.T);
  const exact = sl.filter(x => Math.abs(x.T - T) < 1e-9);
  if (exact.length > 1) return { iv: null, reason: 'same_time' };
  if (exact.length === 1) return exact[0].cell.iv === null ? { iv: null, reason: exact[0].cell.reason ?? 'iv_missing' } : exact[0].cell;
  const j = sl.findIndex(x => x.T > T);
  if (j <= 0) return { iv: null, reason: 'outside_term' };
  const a = sl[j - 1], b = sl[j];
  if ((j > 1 && Math.abs(sl[j - 2].T - a.T) < 1e-12) || (j + 1 < sl.length && Math.abs(sl[j + 1].T - b.T) < 1e-12)) return { iv: null, reason: 'same_time' };
  if (a.cell.iv === null || b.cell.iv === null) return { iv: null, reason: (a.cell.iv === null ? a.cell.reason : b.cell.reason) ?? 'iv_missing' };
  const wa = a.cell.iv ** 2 * a.T, wb = b.cell.iv ** 2 * b.T;
  if (wb < wa) return { iv: null, reason: 'calendar_arbitrage' };
  return { iv: Math.sqrt((wa + (wb - wa) * (T - a.T) / (b.T - a.T)) / T), reason: null };
}
/** IV di una fetta K/S nel punto K/F = x, col forward F e lo spot S: w lineare in ln(K/S) fra i
 *  nodi validi, mai estrapolata. */
function sliceAtKF(m: number[], ivs: (number | null)[], T: number, S: number, F: number, x: number): Cell {
  const pts = m.flatMap((mm, i) => isPos(mm) && isIv(ivs[i]) ? [{ z: Math.log(mm), w: (ivs[i] as number) ** 2 * T }] : []).sort((a, b) => a.z - b.z);
  if (pts.length < 2) return { iv: null, reason: 'smile_too_short' };
  const z = Math.log(x * F / S);
  if (z < pts[0].z - 1e-12 || z > pts[pts.length - 1].z + 1e-12) return { iv: null, reason: 'outside_smile' };
  for (let i = 1; i < pts.length; i++) if (z <= pts[i].z + 1e-12) {
    const a = pts[i - 1], b = pts[i], t = Math.min(Math.max((z - a.z) / (b.z - a.z), 0), 1);
    return { iv: Math.sqrt((a.w + (b.w - a.w) * t) / T), reason: null };
  }
  return { iv: null, reason: 'outside_smile' };
}
export interface SurfaceDiff {
  currentAsOf: string | null; previousAsOf: string | null; currentSpot: number | null; previousSpot: number | null;
  currentQualified: boolean; previousQualified: boolean;
  currentSpotQualified: boolean | null; previousSpotQualified: boolean | null;
  currentSpotFallback: boolean | null; previousSpotFallback: boolean | null;
  /** motivo a livello di superficie (es. 'previous_missing'): tutte le celle n.d. */
  reason: ReasonCode | null;
  /** 'K/F' se i forward di entrambe le istantanee sono passati, altrimenti 'K/S' (dichiarato) */
  axis: 'K/F' | 'K/S';
  alignment: string;
  /** colonne: valori di K/F (axis 'K/F') o di K/S (axis 'K/S') */
  moneyness: number[]; tenorsDays: number[];
  /** valori di K/S presenti più volte in una griglia: colonne escluse (mai un nodo a caso) */
  duplicateMoneyness: number[];
  /** Δ ln S = ln(spot corrente / spot precedente); null se uno spot manca */
  dLnSpot: number | null;
  rows: {
    tenorDays: number; T: number; current: (number | null)[]; previous: (number | null)[]; diff: (number | null)[]; reasons: (ReasonCode | null)[];
    /** scivolamento sullo skew: pendenza dσ/d ln(moneyness) della precedente × Δ ln S (quanto il
     *  diff sarebbe con lo smile fermo sugli strike, «sticky strike»); null se manca un vicino */
    skewSlide: (number | null)[];
    /** diff − skewSlide: la parte che non è solo il movimento dello spot lungo lo skew */
    diffExSlide: (number | null)[];
  }[];
}
/**
 * ΔIV = IV(corrente) − IV(precedente) cella per cella, a TENOR COSTANTE (giorni di calendario
 * dalla data di ciascuna istantanea, come OVDV). Asse: K/F se passi `currentForwards` e
 * `previousForwards` (le griglie K/S del builder si rileggono a K/F = (K/S)·S/F, w lineare in
 * ln K, mai estrapolate; scadenze senza forward = n.d. col motivo del forward); altrimenti K/S
 * spot, dichiarato («sticky moneyness» sullo spot). Interpolazione in T: varianza totale lineare
 * fra le scadenze consecutive a cavallo, mai estrapolata. Una cella n.d. da una parte resta n.d.
 * Il backend non conserva superfici passate: `previous` lo deve aver salvato chi chiama.
 * Gli spot non qualificati del builder sono riportati (current/previousSpotQualified, …Fallback).
 */
export function surfaceDiff(current: MoneynessSurface, previous: MoneynessSurface | null,
  options: { tenorsDays?: number[]; currentForwards?: Forwards | null; previousForwards?: Forwards | null } = {}): SurfaceDiff {
  const tenors = (options.tenorsDays ?? [...DIFF_TENORS_DAYS]).filter(isPos);
  const missing: ReasonCode | null = !current || typeof current !== 'object' ? 'invalid_input' : !previous || typeof previous !== 'object' ? 'previous_missing'
    : current.reason ?? previous.reason ?? null;
  const axis: 'K/F' | 'K/S' = options.currentForwards && options.previousForwards && typeof options.currentForwards === 'object'
    && typeof options.previousForwards === 'object' ? 'K/F' : 'K/S';
  const key = (m: number) => Math.round(m * 1e9);
  const dupOf = (ms: number[]) => { const c = new Map<number, number>(); for (const m of ms) if (isPos(m)) c.set(key(m), (c.get(key(m)) ?? 0) + 1); return c; };
  const curM = Array.isArray(current?.moneyness) ? current.moneyness : [], prevM = Array.isArray(previous?.moneyness) ? (previous as MoneynessSurface).moneyness : [];
  const cd = dupOf(curM), pd = dupOf(prevM);
  const duplicates = [...new Set([...curM, ...prevM].filter(m => isPos(m) && ((cd.get(key(m)) ?? 0) > 1 || (pd.get(key(m)) ?? 0) > 1)))];
  const prevIdx = new Map<number, number>();
  prevM.forEach((m, i) => { if (isPos(m) && (pd.get(key(m)) ?? 0) === 1) prevIdx.set(key(m), i); });
  const cols = curM.flatMap((m, i) => {
    if (!isPos(m) || (cd.get(key(m)) ?? 0) !== 1) return [];
    if (axis === 'K/S' && !missing && !prevIdx.has(key(m))) return [];
    return [{ m, ci: i, pi: prevIdx.get(key(m)) ?? -1 }];
  });
  // celle per scadenza di una superficie, sull'asse scelto
  const cellsOf = (s: MoneynessSurface, fw: Forwards | null | undefined, which: 'ci' | 'pi') =>
    objs<MoneynessSurface['slices'][number]>(s?.slices).filter(x => isT(x.T)).map(x => {
      const T = x.T as number;
      if (axis === 'K/S') return { T, cells: cols.map(c => { const v = c[which] >= 0 ? x.iv?.[c[which]] : null; return isIv(v) ? { iv: v, reason: null } : { iv: null, reason: 'iv_missing' as ReasonCode }; }) };
      const fi = forwardInfo(fw, x.expiry);
      if (fi.F === null || !isPos(s.spot)) return { T, cells: cols.map(() => ({ iv: null, reason: (fi.reason ?? 'spot_missing') as ReasonCode })) };
      return { T, cells: cols.map(c => sliceAtKF(s.moneyness, x.iv ?? [], T, s.spot as number, fi.F as number, c.m)) };
    });
  const curCells = missing ? [] : cellsOf(current, options.currentForwards, 'ci');
  const prevCells = missing ? [] : cellsOf(previous as MoneynessSurface, options.previousForwards, 'pi');
  const dLnSpot = isPos(current?.spot) && isPos(previous?.spot) ? Math.log((current.spot as number) / ((previous as MoneynessSurface).spot as number)) : null;
  const xs = cols.map(c => Math.log(c.m));
  return {
    currentAsOf: current?.asOf ?? null, previousAsOf: previous?.asOf ?? null, currentSpot: current?.spot ?? null, previousSpot: previous?.spot ?? null,
    currentQualified: current?.qualified !== false, previousQualified: previous?.qualified !== false,
    currentSpotQualified: current?.spotQualified ?? null, previousSpotQualified: previous?.spotQualified ?? null,
    currentSpotFallback: current?.spotFallback ?? null, previousSpotFallback: previous?.spotFallback ?? null,
    reason: missing, axis,
    alignment: `tenor (constant calendar days, T = days/365) × ${axis === 'K/F' ? 'K/F (forward per expiry)' : 'K/S spot (no forwards passed)'}`,
    moneyness: cols.map(c => c.m), tenorsDays: tenors, duplicateMoneyness: duplicates, dLnSpot,
    rows: tenors.map(d => {
      const T = d / 365, nul = cols.map(() => null);
      if (missing) return { tenorDays: d, T, current: nul, previous: nul, diff: nul, reasons: cols.map(() => missing), skewSlide: nul, diffExSlide: nul };
      const cur = cols.map((_, i) => ivAtTenor(curCells.map(s => ({ T: s.T, cell: s.cells[i] })), T));
      const prev = cols.map((_, i) => ivAtTenor(prevCells.map(s => ({ T: s.T, cell: s.cells[i] })), T));
      const diff = cur.map((x, i) => x.iv !== null && prev[i].iv !== null ? x.iv - (prev[i].iv as number) : null);
      const slope = (i: number): number | null => {
        const l = i > 0 ? prev[i - 1].iv : null, r = i + 1 < prev.length ? prev[i + 1].iv : null, me = prev[i].iv;
        if (l !== null && r !== null) return (r - l) / (xs[i + 1] - xs[i - 1]);
        if (me !== null && r !== null) return (r - me) / (xs[i + 1] - xs[i]);
        if (me !== null && l !== null) return (me - l) / (xs[i] - xs[i - 1]);
        return null;
      };
      const skewSlide = cols.map((_, i) => { const s = slope(i); return s !== null && dLnSpot !== null && prev[i].iv !== null ? s * dLnSpot : null; });
      return { tenorDays: d, T, current: cur.map(x => x.iv), previous: prev.map(x => x.iv), diff,
        reasons: cur.map((x, i) => x.iv !== null && prev[i].iv !== null ? null : x.reason ?? prev[i].reason ?? 'not_comparable'),
        skewSlide, diffExSlide: diff.map((x, i) => x !== null && skewSlide[i] !== null ? x - (skewSlide[i] as number) : null) };
    }),
  };
}

/* ---------------------------------------------------------------- 8. vol d'evento */
export interface EventVol {
  earningsDate: string; asOf: string | null; before: string | null; after: string | null;
  /** varianza della mossa del giorno degli utili: v_e = w(T_after) − w(T_before) − σ_b²·(T_after − T_before) */
  eventVariance: number | null;
  /** ±1σ della mossa del giorno (frazione): √v_e */
  eventMove: number | null;
  /** vol d'evento annualizzata su 252 sedute: σ_event = √(252·v_e), così σ_event·√(1/252) = eventMove */
  eventVolAnnualized: number | null;
  baseVol: number | null; baseMethod: 'pre' | 'post' | 'pre+post' | null;
  reason: ReasonCode | null; detail: string | null;
}
const isoDay = (s: unknown): string | null => {
  if (typeof s !== 'string') return null;
  const d = s.slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) return null;
  const t = Date.parse(d + 'T00:00:00Z');
  return Number.isFinite(t) && new Date(t).toISOString().slice(0, 10) === d ? d : null;
};
/**
 * Modello: varianza totale = σ_b²·T + v_e·1[T dopo gli utili]. Con la scadenza appena prima
 * (T1; se gli utili cadono prima della prima scadenza, T1 = 0 e w1 = 0: l'intervallo da adesso
 * alla prima scadenza contiene l'evento) e quella appena dopo (T2):
 *   v_e = σ2²T2 − σ1²T1 − σ_b²(T2 − T1).
 * σ_b² (base SENZA evento) = varianza forward di un intervallo senza utili: 'pre' = l'intervallo
 * che finisce in T1 (σ1² se T1 è la prima scadenza), 'post' = quello fra T2 e la successiva;
 * default la media dei disponibili ('pre+post').
 * `asOf` = data dell'istantanea (OBBLIGATORIA: passare la data di New York, es.
 * market_session.session_date; un timestamp ISO viene troncato al giorno). Utili < asOf =
 * 'event_past' (un next_earnings vecchio non diventa un premio inventato); utili = asOf o nel
 * giorno di una scadenza = 'event_timing_ambiguous' (prima/dopo la seduta non è nel payload).
 * w decrescente attraverso l'evento o varianze forward negative = 'calendar_arbitrage'.
 * LIMITE: un secondo evento nell'intervallo 'post' gonfia la base; nessun calendario degli
 * eventi oltre `next_earnings` è nel payload.
 */
export function eventVol(term: TermPoint[], earningsDate: string | null, asOf: string | null, options: { base?: 'pre' | 'post' | 'mean' } = {}): EventVol {
  const ed = isoDay(earningsDate), today = isoDay(asOf);
  const out: EventVol = { earningsDate: ed ?? String(earningsDate ?? ''), asOf: today, before: null, after: null, eventVariance: null, eventMove: null, eventVolAnnualized: null,
    baseVol: null, baseMethod: null, reason: null, detail: null };
  if (!ed) return { ...out, reason: 'invalid_input', detail: 'earningsDate' };
  if (!today) return { ...out, reason: 'as_of_missing' };
  if (ed < today) return { ...out, reason: 'event_past' };
  if (ed === today) return { ...out, reason: 'event_timing_ambiguous', detail: 'earnings on the snapshot day' };
  const pts = objs<TermPoint>(term).filter(p => isT(p.T) && isIv(p.iv) && isoDay(p.expiry))
    .map(p => ({ expiry: isoDay(p.expiry) as string, T: p.T as number, w: (p.iv as number) ** 2 * (p.T as number) })).sort((a, b) => a.T - b.T);
  if (pts.length < 2) return { ...out, reason: 'single_expiry' };
  if (pts.some(p => p.expiry === ed)) return { ...out, reason: 'event_timing_ambiguous', detail: 'earnings on an expiry day' };
  const j = pts.findIndex(p => p.expiry > ed);
  if (j < 0) return { ...out, reason: 'no_expiry_after_event' };
  const b = j > 0 ? pts[j - 1] : null, a = pts[j];
  const res = { ...out, before: b?.expiry ?? null, after: a.expiry };
  const used = [j > 1 ? pts[j - 2] : null, b, a, j + 1 < pts.length ? pts[j + 1] : null].filter((x): x is typeof a => x !== null);
  for (let u = 1; u < used.length; u++) if (!(used[u].T > used[u - 1].T)) return { ...res, reason: 'same_time' };
  const T1 = b ? b.T : 0, w1 = b ? b.w : 0;
  if (a.w < w1) return { ...res, reason: 'calendar_arbitrage', detail: `w(after) ${a.w.toExponential(4)} < w(before) ${w1.toExponential(4)}` };
  const pre = b ? (j > 1 ? (b.w - pts[j - 2].w) / (b.T - pts[j - 2].T) : b.w / b.T) : null;
  const post = j + 1 < pts.length ? (pts[j + 1].w - a.w) / (pts[j + 1].T - a.T) : null;
  const mode = options.base ?? 'mean';
  const use = mode === 'pre' ? [pre] : mode === 'post' ? [post] : [pre, post];
  const avail = use.filter((x): x is number => x !== null);
  if (!avail.length) return { ...res, reason: 'no_base_vol' };
  if (avail.some(x => !(x >= 0))) return { ...res, reason: 'calendar_arbitrage', detail: `forward variance ${avail.map(x => x.toFixed(6)).join(', ')}` };
  const base = avail.reduce((s, x) => s + x, 0) / avail.length;
  const ve = a.w - w1 - base * (a.T - T1);
  const baseMethod: EventVol['baseMethod'] = mode === 'pre' ? 'pre' : mode === 'post' ? 'post'
    : pre !== null && post !== null ? 'pre+post' : pre !== null ? 'pre' : 'post';
  const withBase = { ...res, baseVol: Math.sqrt(base), baseMethod, eventVariance: ve };
  // soglia numerica: un residuo di arrotondamento (≤ 1e-9 della varianza totale) non è un premio
  if (!(ve > 1e-9 * a.w)) return { ...withBase, reason: 'no_event_premium', detail: `v_e = ${ve.toExponential(3)}` };
  return { ...withBase, eventMove: Math.sqrt(ve), eventVolAnnualized: Math.sqrt(TRADING_DAYS * ve) };
}

/* ---------------------------------------------------------------- 9. cono */
export interface ConeRow {
  expiry: string; T: number | null; forward: number | null;
  /** 'per-expiry' = forward della scadenza; 'single-value' = UN numero passato per tutte le
   *  scadenze (se è lo spot, il centro NON è il forward: mostrarlo) */
  forwardSource: 'per-expiry' | 'single-value'; iv: number | null;
  /** ±σ√T (frazione, «±1σ in log») */ pct: number | null;
  /** F·σ·√T (prezzo, mossa 1σ al primo ordine) */ move: number | null;
  /** banda LOGNORMALE F·e^{∓σ√T}: sempre positiva (la lineare F ± move va sotto zero per σ√T > 1) */
  low: number | null; high: number | null;
  /** la banda in % dal centro: 1 − e^{−σ√T} sotto, e^{σ√T} − 1 sopra */
  downPct: number | null; upPct: number | null;
  reason: ReasonCode | null;
}
/** ±1σ per scadenza: pct = σ_ATM·√T, move = F·pct, banda lognormale [F·e^{−pct}, F·e^{+pct}].
 *  F: il forward per scadenza (Forwards) oppure un numero unico dichiarato 'single-value'.
 *  Senza F: % calcolata, prezzi n.d. col motivo vero. */
export function expectedMoveCone(F: number | Forwards | null, term: TermPoint[]): ConeRow[] {
  return objs<TermPoint>(term).sort((a, b) => (isT(a.T) ? a.T : Infinity) - (isT(b.T) ? b.T : Infinity)).map(p => {
    const single = typeof F === 'number';
    const fi = single ? { F: isPos(F) ? F : null, reason: isPos(F) ? null : 'forward_missing' as ReasonCode } : forwardInfo(F, p.expiry);
    const row: ConeRow = { expiry: p.expiry, T: isT(p.T) ? p.T : null, forward: fi.F, forwardSource: single ? 'single-value' : 'per-expiry', iv: isIv(p.iv) ? p.iv : null,
      pct: null, move: null, low: null, high: null, downPct: null, upPct: null, reason: null };
    if (!isT(p.T)) return { ...row, reason: tReason(p.T) };
    if (!isPos(p.iv)) return { ...row, reason: 'iv_missing' };
    if (!isIv(p.iv)) return { ...row, reason: 'iv_out_of_range' };
    if (!Number.isFinite(Math.exp(p.iv * Math.sqrt(p.T)))) return { ...row, reason: 'invalid_input' };
    const pct = p.iv * Math.sqrt(p.T), withPct = { ...row, pct, downPct: 1 - Math.exp(-pct), upPct: Math.exp(pct) - 1 };
    if (fi.F === null) return { ...withPct, reason: fi.reason };
    return { ...withPct, move: fi.F * pct, low: fi.F * Math.exp(-pct), high: fi.F * Math.exp(pct) };
  });
}
