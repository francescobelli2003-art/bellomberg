import { t as tr } from '@/i18n/t';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
// ============================================================================
// F6 FACTOR LAB — "RICONCILIAZIONE" · le derivazioni, FUORI dai componenti
// (Opus 5, 27/07. Spec: docs/superpowers/specs/2026-07-27-f6-factor-lab-riconciliazione-design.md)
//
// Modello: app/src/lib/cassa.ts (F7) e app/src/lib/movimenti.ts (F14).
// Funzioni PURE: niente React, niente fetch, niente stato. E' il posto dove i
// difetti si leggono senza montare nulla.
//
// QUATTRO REGOLE CHE QUESTO FILE FA RISPETTARE:
//  1. NIENTE FALLBACK SILENZIOSI (PM 14/07). Una t che manca non diventa un
//     intervallo largo zero: diventa `null` con il MOTIVO, e chi rende DEVE
//     dichiararlo con `perche()`.
//  2. ZERO NON E' UN DATO ASSENTE, nei due sensi. Un beta di 0,004 e' una
//     MISURA; un beta mancante non si rende come 0.
//  3. UNO SCARTO FRA DUE PERCENTUALI E' IN PUNTI PERCENTUALI. La funzione si
//     chiama `scartoInPunti` apposta: il nome impedisce di attaccarci un '%'.
//     (+8,662% contro -12,56% distano 21,22 PP, non "21,22%".)
//  4. UNA STIMA NON RICONCILIATA NON SI MESCOLA ALLE RICONCILIATE. Il
//     guardrail certifica RECONCILED su TRE beta; quello che questa pagina
//     mostra (finestra 1y) NON e' fra quelli. `lancette()` porta il flag e
//     chi rende lo usa.
//
// ⚠ IL CONTRATTO DEL BACKEND, MISURATO IL 27/07 (rifallo con
//   `python mockup_f6_factors/build_data.py`, che stampa byte e secondi):
//   /portfolio/factors            22.797 B · 22 chiavi · 27 holding x 33 campi
//   /portfolio/metrics/beta_reconcile 548 B · 3 beta + definizioni + verdetto
//   Il motore fattoriale tiene UNA SOLA finestra in cache: la riconciliazione
//   chiede la 3y, questa pagina la 1y, e l'alternanza costa 6,4-7,1 s di 27
//   regressioni. Vedi ORDINE_CHIAMATE piu' sotto.
// ============================================================================

/** i sei fattori Fama-French + Carhart, nell'ordine in cui si rendono */
export const FATTORI = [
  'beta_market', 'beta_smb', 'beta_hml', 'beta_rmw', 'beta_cma', 'beta_mom',
] as const;
export type Fattore = typeof FATTORI[number];

export const NOME_FATTORE: Record<Fattore, { corto: string; lungo: string }> = {
  beta_market: { corto: 'MKT', get lungo() { return tr('factors.f092'); } },
  beta_smb: { corto: 'SMB', get lungo() { return tr('factors.f093'); } },
  beta_hml: { corto: 'HML', get lungo() { return tr('factors.f094'); } },
  beta_rmw: { corto: 'RMW', get lungo() { return tr('factors.f095'); } },
  beta_cma: { corto: 'CMA', get lungo() { return tr('factors.f096'); } },
  beta_mom: { corto: 'MOM', lungo: 'Momentum' },
};

/**
 * ⚠ L'ORDINE DELLE CHIAMATE E' UNA DECISIONE DI IMPIANTO, NON UN DETTAGLIO —
 * e la prima stesura di questo commento affermava una cosa FALSA che non
 * avevo misurato ("l'ordine dimezza il costo"). Misurato tre volte:
 *
 *   reconcile(3y) 8,32 / 9,11 / 8,10 s   ← ricalcolo di 27 regressioni
 *   factors?3y    0,00 / 0,00 / 0,03 s   ← GRATIS: colpo di cache
 *   factors(1y)   6,53 / 6,71 / 6,71 s   ← altro ricalcolo
 *   TOTALE       14,85 / 15,82 / 14,84 s
 *
 * La pagina ha bisogno di ENTRAMBE le finestre e lo slot di cache e' UNO:
 * nessun ordine evita il ricalcolo e mezzo. Quello che l'ordine decide e'
 * (a) quanto in fretta la pagina appare, (b) quale finestra resta calda.
 *
 * Quindi:
 *   1. factors(1y) per primo -> la pagina si disegna (0,02 s se calda);
 *   2. beta_reconcile        -> il calibro si riempie, ~8 s;
 *   3. factors?period=3y     -> gratis, riempie il confronto;
 *   4. factors(1y) di nuovo, IN SOTTOFONDO, solo per rimettere la cache
 *      come l'ha trovata: la prossima apertura non paga il ricalcolo.
 *      ⚠ Va DICHIARATO in pagina: e' lavoro che il PM non ha chiesto.
 *
 * Le altre tre rotte (/portfolio, /metrics/advanced, /risk) NON toccano
 * quella cache e partono in parallelo fin dall'inizio.
 */
export const ORDINE_CHIAMATE = ['factors-1y', 'beta_reconcile', 'factors-3y', 'riscalda-1y'] as const;

/** i secondi misurati il 27/07, per non scrivere in pagina un numero a memoria */
export const COSTO_CACHE = { reconcile: 8.5, riscaldamento: 6.7, totale: 15.2 };

// ── LE FORME CHE ARRIVANO ───────────────────────────────────────────────────

export interface HoldingFattoriale {
  ticker?: string;
  weight?: number;
  weight_pct?: number;
  n_obs?: number;
  period?: string;
  alpha_annualized_pct?: number;
  alpha_tstat?: number;
  alpha_pvalue?: number;
  r_squared?: number;
  f_pvalue?: number;
  /** la regione del dataset K.French su cui QUESTO titolo e' stato regredito */
  factor_region?: string;
  factor_set?: string;
  region_source?: string;
  /** il backend dichiara qui che rendimenti e fattori sono in valute diverse */
  fx_caveat?: string | null;
  beta_btc?: number | null;
  beta_btc_tstat?: number | null;
  has_btc_factor?: boolean;
  [k: string]: unknown;
}

export interface RegioneFattoriale {
  label?: string;
  n_holdings?: number;
  weight_pct?: number;
}

export interface DatiRegione {
  n_obs?: number;
  last_date?: string;
}

export interface Aggregato {
  alpha_annualized_pct?: number;
  beta_market?: number;
  beta_smb?: number;
  beta_hml?: number;
  beta_rmw?: number;
  beta_cma?: number;
  beta_mom?: number;
  /** ⚠ NON ESISTE nel payload: verificato. L'alpha aggregato non ha t-stat, e
   *  quindi non e' giudicabile. Tenuto qui per non fingere che un domani
   *  arrivi da un'altra parte. */
  alpha_tstat?: number;
}

export interface PayloadFattori {
  timestamp?: string;
  version?: string;
  model?: string;
  method?: string;
  data_source?: string;
  period?: string;
  n_holdings_analyzed?: number;
  n_holdings_skipped?: number;
  n_alpha_significant_5pct?: number;
  skipped_detail?: { ticker: string; weight_pct: number; reason: string }[];
  coverage_weight_pct?: number;
  portfolio_aggregate?: Aggregato;
  portfolio_avg_r_squared?: number;
  per_holding?: Record<string, HoldingFattoriale>;
  regions?: Record<string, RegioneFattoriale>;
  /** ⚠ oggi `{}`. La pagina di prima rimandava a "leggere il payload" invece
   *  di stampare l'errore che aveva gia' in mano. */
  region_errors?: Record<string, string>;
  ff_data_last_date?: string;
  ff_data_n_obs?: number;
  /** ⚠ ff_data_n_obs descrive SOLO il dataset US. Questo li ha tutti. */
  ff_data_by_region?: Record<string, DatiRegione>;
  error?: string;
}

export interface PayloadRiconciliazione {
  betas?: Record<string, number>;
  definitions?: Record<string, string>;
  sources_failed?: Record<string, string>;
  threshold?: number;
  max_spread?: number;
  verdict?: string;
  beta_consensus?: number;
  /** ⚠ il guardrail manda anche una NOTA in prosa (es. il monito a non usare il
   *  beta come argomento decisionale quando le fonti divergono). La prima
   *  stesura non la dichiarava nemmeno nel tipo, quindi la buttava. */
  note?: string;
}

// ── PERCHE' UN NUMERO NON C'E' ──────────────────────────────────────────────

/** mai un trattino nudo: se un numero manca, si scrive PERCHE' */
export type Muto =
  | null
  | 'coefficiente-assente'    // il payload non porta il beta/alpha
  | 't-assente'               // senza t non esiste un intervallo
  | 'riconciliazione-assente' // /portfolio/metrics/beta_reconcile non ha risposto
  | 'portafoglio-assente'     // /portfolio non ha risposto: niente NAV, niente copertura
  | 'finestra-assente'        // manca una delle due finestre del confronto
  | 'altra-unita'             // sta su un'altra scala: non si disegna su questo righello
  | 'non-fornita';            // il backend non manda la definizione di questa misura

export function perche(m: Muto): string {
  switch (m) {
    case 'coefficiente-assente': return tr('factors.f097');
    case 't-assente': return tr('factors.f098');
    case 'riconciliazione-assente': return tr('factors.f099');
    case 'portafoglio-assente': return tr('factors.f100');
    case 'finestra-assente': return tr('factors.f101');
    case 'altra-unita': return tr('factors.f102');
    case 'non-fornita': return tr('factors.f103');
    default: return '';
  }
}

// ── L'INTERVALLO DI CONFIDENZA ──────────────────────────────────────────────
// ⚠ IL RISCHIO ESTETICO DI QUESTA PAGINA, IN UNA FUNZIONE. In F6 un beta non
//   e' un numero, e' un intervallo: il punto e' piccolo, il baffo e' l'oggetto.
//   Un coefficiente il cui intervallo comprende lo zero viene disegnato MENTRE
//   lo comprende, e non si puo' vestire da segnale.
//
//   La pagina di prima coloriva di verde/rosso ogni |beta| >= 0,2: su 162
//   celle ne dipingeva 129, di cui 58 (il 45%) con |t| < 1,96, cioe'
//   indistinguibili da zero. Questa funzione rende quell'errore impossibile,
//   perche' chi la usa non ha mai il punto senza il suo intervallo.

/** z al 95% bilaterale. Il campione e' grande (n_obs mediana 210): la normale basta. */
const Z95 = 1.96;

export interface Intervallo {
  beta: number;
  /** null quando la t manca: l'intervallo NON si inventa */
  lo: number | null;
  hi: number | null;
  se: number | null;
  t: number | null;
  /** |t| >= 1,96 */
  sig: boolean;
  /** true quando l'intervallo comprende lo zero — cioe' e' compatibile con "nessuna esposizione" */
  attraversaZero: boolean;
  muto: Muto;
}

export function intervallo(beta: unknown, tstat: unknown): Intervallo | null {
  const b = num(beta);
  if (b === null) return null;
  const t = num(tstat);
  // ⚠ t = 0 NON e' "non significativo con SE enorme": e' un valore che rende
  //   la SE non ricavabile da beta/t. Si dichiara, non si divide per zero.
  if (t === null || Math.abs(t) < 1e-9) {
    return {
      beta: b, lo: null, hi: null, se: null, t: t,
      sig: false, attraversaZero: false, muto: 't-assente',
    };
  }
  const se = Math.abs(b / t);
  const lo = b - Z95 * se;
  const hi = b + Z95 * se;
  return {
    beta: b, lo, hi, se, t,
    sig: Math.abs(t) >= Z95,
    attraversaZero: lo <= 0 && hi >= 0,
    muto: null,
  };
}

// ── LE QUATTRO LANCETTE ─────────────────────────────────────────────────────
// ⚠ REGOLA 4. `beta_reconcile` riconcilia TRE stime e dichiara RECONCILED.
//   Quella che questa pagina mostra (modello fattoriale a finestra 1y) NON e'
//   fra quelle: il guardrail certifica un numero che il PM non vede da nessuna
//   parte. Disegnarla come le altre sarebbe la dodicesima bugia della pagina.

export type ChiaveBeta =
  | 'portfolio_risk_spy'
  | 'factor_model_mkt'
  | 'advanced_metrics_twr'
  | 'factor_model_mkt_1y'
  | string;

export interface Lancetta {
  chiave: ChiaveBeta;
  etichetta: string;
  valore: number;
  definizione: string | null;
  definizioneMuta: Muto;
  /** false = non partecipa alla riconciliazione del guardrail */
  riconciliato: boolean;
}

const ETICHETTA_BETA: Record<string, string> = {
  advanced_metrics_twr: 'TWR vs BENCHMARK',
  portfolio_risk_spy: 'BOOK vs SPY',
  get factor_model_mkt() { return tr('factors.f104'); },
  get factor_model_mkt_1y() { return tr('factors.f105'); },
};

export interface Calibro {
  lancette: Lancetta[];
  /** gli estremi delle sole riconciliate: e' la banda del consenso */
  consensoMin: number | null;
  consensoMax: number | null;
  soglia: number | null;
  spreadMax: number | null;
  consenso: number | null;
  verdetto: string | null;
  /** le fonti che il guardrail non e' riuscito a interrogare, VERBATIM */
  fontiCadute: [string, string][];
  /** la nota in prosa del guardrail, VERBATIM. Null se non la manda. */
  nota: string | null;
  muto: Muto;
}

export function calibro(
  rec: PayloadRiconciliazione | null,
  fac: PayloadFattori | null,
): Calibro {
  const lancette: Lancetta[] = [];

  if (rec && rec.betas) {
    for (const k of Object.keys(rec.betas)) {
      const v = num(rec.betas[k]);
      if (v === null) continue;
      const d = rec.definitions ? rec.definitions[k] : undefined;
      lancette.push({
        chiave: k,
        etichetta: ETICHETTA_BETA[k] || k,
        valore: v,
        definizione: typeof d === 'string' && d.trim() ? d.trim() : null,
        definizioneMuta: typeof d === 'string' && d.trim() ? null : 'non-fornita',
        riconciliato: true,
      });
    }
  }

  const mio = num(fac && fac.portfolio_aggregate ? fac.portfolio_aggregate.beta_market : null);
  if (mio !== null) {
    const p = (fac && fac.period ? fac.period : '1y').toUpperCase();
    lancette.push({
      chiave: 'factor_model_mkt_1y',
      etichetta: tr('factors.f106') + p,
      valore: mio,
      definizione: tr('factors.f107') + p.toLowerCase() +
        tr('factors.f108'),
      definizioneMuta: null,
      riconciliato: false,
    });
  }

  lancette.sort((a, b) => a.valore - b.valore);

  const ric = lancette.filter(l => l.riconciliato).map(l => l.valore);
  const cadute: [string, string][] = [];
  if (rec && rec.sources_failed) {
    for (const k of Object.keys(rec.sources_failed)) {
      cadute.push([k, String(rec.sources_failed[k])]);
    }
  }

  return {
    lancette,
    consensoMin: ric.length > 1 ? Math.min.apply(null, ric) : null,
    consensoMax: ric.length > 1 ? Math.max.apply(null, ric) : null,
    soglia: num(rec ? rec.threshold : null),
    spreadMax: num(rec ? rec.max_spread : null),
    consenso: num(rec ? rec.beta_consensus : null),
    verdetto: rec && typeof rec.verdict === 'string' ? rec.verdict : null,
    fontiCadute: cadute,
    nota: rec && typeof rec.note === 'string' && rec.note.trim() ? rec.note.trim() : null,
    muto: rec ? null : 'riconciliazione-assente',
  };
}

// ── LA BASE DI CALCOLO ──────────────────────────────────────────────────────
// ⚠ LA BUGIA PIU' FACILE DA RIPETERE. `coverage_weight_pct` e' calcolato sul
//   CAPITALE INVESTITO (portfolio_factors.py:558 e :683), e la pagina di prima
//   lo scriveva "% NAV". Misurato: 229.726,10 / 255.973,15 = 89,75%. I
//   contanti in cassa (10,25% del NAV) non hanno esposizione fattoriale —
//   ed e' giusto che non ne abbiano. Sbagliata e' la dicitura.
//
// ⚠⚠ E LA PRIMA STESURA DI QUESTA FUNZIONE RIPETEVA LA BUGIA DA UN'ALTRA
//    PORTA, con un commento che AFFERMAVA il contrario. Calcolava
//    `effettiva = investito / nav`, cioe' la quota di NAV investita — non la
//    quota di NAV COPERTA DALL'ANALISI. Sono due cose diverse e la vera e' il
//    PRODOTTO: il motore analizza `dichiarata`% dell'investito, e l'investito
//    e' `investito/nav` del NAV.
//    Trovato dalla review avversariale del 27/07 eseguendo QUESTA funzione
//    transpilata sui payload veri con una regione Fama-French caduta: il
//    riquadro scriveva 65,5% in rosso sopra e 89,75% in verde sotto, mentre
//    la copertura vera era 0,655 x 0,8975 = 58,78%. Trentuno punti
//    sovrastimati, e il numero verde — quello presentato come LA CORREZIONE —
//    non si muoveva di un decimale proprio mentre la copertura crollava.
//    Oggi non si vede solo perche' `coverage_weight_pct` vale 100,0 e
//    `n_holdings_skipped` vale 0. Tre rami vivi lo innescano: SKIP_TICKERS,
//    download regionale fallito, MIN_OBSERVATIONS.

export interface Copertura {
  /** quello che il backend manda: la copertura sul CAPITALE INVESTITO */
  dichiarata: number | null;
  /** la quota di NAV davvero coperta dall'analisi = dichiarata x investito/nav */
  effettiva: number | null;
  /** quanta parte del NAV e' investita (il solo secondo fattore del prodotto) */
  investitoSuNav: number | null;
  investito: number | null;
  cassa: number | null;
  nav: number | null;
  cassaPct: number | null;
  /** cio' che il backend dichiara sporco su questo NAV, VERBATIM */
  avvisi: string[];
  muto: Muto;
  /** perche' `effettiva` non c'e', quando non c'e' */
  effettivaMuta: Muto;
}

export interface SnapshotBase {
  totale_valore_mercato_eur?: number;
  cash_disponibile_eur?: number;
  nav_total_eur?: number;
  /** ⚠ il backend dichiara qui i cambi mancanti: il NAV in euro non e' pulito */
  fx_incomplete?: string[] | null;
  stale_positions?: string[] | null;
  source?: string;
}

export function copertura(
  fac: PayloadFattori | null,
  snap: SnapshotBase | null,
): Copertura {
  const dichiarata = num(fac ? fac.coverage_weight_pct : null);
  const investito = num(snap ? snap.totale_valore_mercato_eur : null);
  const cassa = num(snap ? snap.cash_disponibile_eur : null);
  const nav = num(snap ? snap.nav_total_eur : null);

  // ⚠ REGOLA 2: senza il NAV la copertura vera NON e' 100, e' ignota.
  const quota = (investito !== null && nav !== null && nav > 0)
    ? (investito / nav) : null;
  let effettiva: number | null = null;
  let effettivaMuta: Muto = null;
  if (quota === null) effettivaMuta = snap ? 'coefficiente-assente' : 'portafoglio-assente';
  else if (dichiarata === null) effettivaMuta = 'coefficiente-assente';
  else effettiva = dichiarata * quota;

  const avvisi: string[] = [];
  const fxIn = snap && Array.isArray(snap.fx_incomplete) ? snap.fx_incomplete : [];
  const stale = snap && Array.isArray(snap.stale_positions) ? snap.stale_positions : [];
  if (fxIn.length) avvisi.push(tr('factors.f109', {a: fxIn.length, b: fxIn.join(' ')}));
  if (stale.length) avvisi.push(tr('factors.f110', {a: stale.length, b: stale.join(' ')}));

  return {
    dichiarata, effettiva, investitoSuNav: quota === null ? null : quota * 100,
    investito, cassa, nav,
    cassaPct: (cassa !== null && nav !== null && nav > 0) ? (cassa / nav) * 100 : null,
    avvisi,
    muto: snap ? null : 'portafoglio-assente',
    effettivaMuta,
  };
}

// ── LO SCARTO FRA DUE MISURE ────────────────────────────────────────────────
// ⚠ REGOLA 3, SCRITTA NEL NOME. La distanza fra +8,662% e -12,56% e' 21,22
//   PUNTI PERCENTUALI. Scrivere "21,22%" e' lo stesso errore di categoria che
//   questa pagina esiste per denunciare.

export function scartoInPunti(a: unknown, b: unknown): number | null {
  const x = num(a), y = num(b);
  if (x === null || y === null) return null;
  return Math.abs(x - y);
}

// ── L'ETA' DEI FATTORI ──────────────────────────────────────────────────────
// ⚠ Decisione del PM 27/07: BADGE IN TESTATA E SI DISEGNA. Nessun dato viene
//   nascosto, ma il ritardo e' sempre visibile. Una pagina che disegna beta
//   fermi a due mesi senza dirlo e' il fallback silenzioso vietato il 14/07.

export function ritardoGiorni(ultimoISO: unknown, oggi?: Date): number | null {
  if (typeof ultimoISO !== 'string') return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(ultimoISO);
  if (!m) return null;
  const a = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  const n = oggi || new Date();
  const b = Date.UTC(n.getFullYear(), n.getMonth(), n.getDate());
  if (!isFinite(a) || !isFinite(b)) return null;
  return Math.round((b - a) / 86400000);
}

// ── GLI ALPHA, UNO PER UNO ──────────────────────────────────────────────────

export interface RigaAlpha {
  ticker: string;
  peso: number | null;
  alpha: number | null;
  ic: Intervallo | null;
  r2: number | null;
  /** ⚠ PURR e' stimato su 121 osservazioni contro una mediana di 210 e mostra
   *  +198,8%: la riga piu' spettacolare della tabella e' anche la meno
   *  sostenuta, e la pagina di prima buttava questo campo. */
  nObs: number | null;
  /** ⚠ valorizzato su 21 holding su 27 (83,76% del capitale): rendimenti in
   *  valuta locale contro fattori in USD. Va reso, VERBATIM. */
  fxCaveat: string | null;
  regione: string | null;
}

export function alphaPerTitolo(fac: PayloadFattori | null): RigaAlpha[] {
  const ph = fac && fac.per_holding ? fac.per_holding : null;
  if (!ph) return [];
  const righe: RigaAlpha[] = Object.keys(ph).map(t => {
    const h = ph[t];
    const a = num(h.alpha_annualized_pct);
    return {
      ticker: (typeof h.ticker === 'string' && h.ticker) ? h.ticker : t,
      peso: num(h.weight_pct),
      alpha: a,
      ic: intervallo(h.alpha_annualized_pct, h.alpha_tstat),
      r2: num(h.r_squared),
      nObs: num(h.n_obs),
      fxCaveat: (typeof h.fx_caveat === 'string' && h.fx_caveat.trim()) ? h.fx_caveat.trim() : null,
      regione: typeof h.factor_region === 'string' ? h.factor_region : null,
    };
  });
  // decrescente per alpha; chi non ha alpha va in fondo, non a zero
  righe.sort((x, y) => {
    if (x.alpha === null && y.alpha === null) return 0;
    if (x.alpha === null) return 1;
    if (y.alpha === null) return -1;
    return y.alpha - x.alpha;
  });
  return righe;
}

// ── IL CONFRONTO FRA LE DUE FINESTRE ────────────────────────────────────────
// ⚠ UN RIGHELLO SOLO PER I SEI BETA. L'alpha e' in un'altra unita' (punti
//   percentuali contro coefficienti adimensionali): dargli una scala propria
//   dentro la stessa colonna sarebbe la scala che cambia riga per riga — il
//   difetto che questa pagina esiste per denunciare, commesso da dentro.

export interface RigaFinestra {
  chiave: string;
  etichetta: string;
  unAnno: number | null;
  treAnni: number | null;
  delta: number | null;
  /** true = non sta sul righello comune dei beta */
  fuoriScala: boolean;
  muto: Muto;
}

export function confrontoFinestre(a1: Aggregato | null, a3: Aggregato | null): RigaFinestra[] {
  const righe: RigaFinestra[] = FATTORI.map(f => {
    const x = num(a1 ? a1[f] : null);
    const y = num(a3 ? a3[f] : null);
    return {
      chiave: f,
      etichetta: NOME_FATTORE[f].corto + ' · ' + NOME_FATTORE[f].lungo,
      unAnno: x, treAnni: y,
      delta: (x !== null && y !== null) ? y - x : null,
      fuoriScala: false,
      muto: (x === null || y === null) ? 'finestra-assente' : null,
    };
  });
  const x = num(a1 ? a1.alpha_annualized_pct : null);
  const y = num(a3 ? a3.alpha_annualized_pct : null);
  righe.push({
    chiave: 'alpha_annualized_pct',
    etichetta: 'α ann. · %',
    unAnno: x, treAnni: y,
    delta: (x !== null && y !== null) ? y - x : null,
    fuoriScala: true,
    muto: (x === null || y === null) ? 'finestra-assente' : 'altra-unita',
  });
  return righe;
}

/** il semi-ampiezza del righello comune: solo i beta, mai l'alpha */
export function scalaComune(righe: RigaFinestra[]): number {
  let m = 0.01;
  for (const r of righe) {
    if (r.fuoriScala) continue;
    if (r.unAnno !== null) m = Math.max(m, Math.abs(r.unAnno));
    if (r.treAnni !== null) m = Math.max(m, Math.abs(r.treAnni));
  }
  return m * 1.15;
}

// ── IL CONTO DEL RUMORE ─────────────────────────────────────────────────────
// Serve a dire in pagina quanto della mappa di calore di prima era rumore:
// 162 celle, 71 significative, 129 colorate dalla regola |beta| >= 0,2, di cui
// 58 non distinguibili da zero, 24 in saturazione sopra |1,5|.

export interface ContoRumore {
  celle: number;
  significative: number;
  /** quante ne colorava la regola in uso prima di questa riscrittura */
  colorateRegolaVecchia: number;
  colorateNonSignificative: number;
  inSaturazione: number;
}

export function contaRumore(fac: PayloadFattori | null): ContoRumore {
  const out: ContoRumore = {
    celle: 0, significative: 0, colorateRegolaVecchia: 0,
    colorateNonSignificative: 0, inSaturazione: 0,
  };
  const ph = fac && fac.per_holding ? fac.per_holding : null;
  if (!ph) return out;
  for (const t of Object.keys(ph)) {
    const h = ph[t];
    for (const f of FATTORI) {
      const b = num(h[f]);
      if (b === null) continue;
      out.celle++;
      const ic = intervallo(h[f], h[f + '_tstat']);
      const sig = !!(ic && ic.sig);
      if (sig) out.significative++;
      const colorata = Math.abs(b) >= 0.2;          // la regola di FactorsPage.tsx:91-97
      if (colorata) {
        out.colorateRegolaVecchia++;
        if (!sig) out.colorateNonSignificative++;
      }
      if (Math.abs(b) >= 1.5) out.inSaturazione++;  // la troncatura di :359
    }
  }
  return out;
}

// ── LE REGIONI ──────────────────────────────────────────────────────────────
// ⚠ `ff_data_n_obs` descrive SOLO il dataset US (15.833 oss.) mentre 18
//   holding su 27 usano dataset da 9.282. `ff_data_by_region` esiste ed e'
//   quello giusto: la pagina di prima non lo mostrava.

export interface RigaRegione {
  chiave: string;
  etichetta: string;
  nHolding: number | null;
  peso: number | null;
  nObs: number | null;
  ultimaData: string | null;
  /** l'errore del backend su questa regione, VERBATIM. Oggi `{}`. */
  errore: string | null;
}

// ⚠⚠ LA COLONNA "ERRORE" ERA IRRAGGIUNGIBILE PER COSTRUZIONE, e ci ho messo
//    dentro il testo del backend credendo di renderlo. `region_errors[reg]` si
//    valorizza SOLO nell'except del download (portfolio_factors.py:572-577); in
//    quel caso quella regione non entra in `ff_by_region` e nessuna sua holding
//    viene analizzata, quindi `regions` — costruito dai soli `factor_region`
//    delle ANALIZZATE (:666-675) — non ha quella chiave. Le due mappe hanno
//    chiavi DISGIUNTE: iterando solo `regions` l'errore non compare MAI.
//    Trovato dalla review avversariale del 27/07 chiamando questa funzione con
//    region_errors={'europe':'HTTP 404'}: tre righe, tutte con errore null.
//    Cura: si itera l'UNIONE, e le chiavi di sola-erroneita' producono una riga
//    con i conteggi a null e il motivo del backend VERBATIM.
export function regioni(fac: PayloadFattori | null): RigaRegione[] {
  if (!fac) return [];
  const R = fac.regions || {};
  const BR = fac.ff_data_by_region || {};
  const ERR = fac.region_errors || {};
  const chiavi = Object.keys(R).concat(
    Object.keys(ERR).filter(k => !(k in R)),
  );
  if (!chiavi.length) return [];
  return chiavi.map(k => {
    const r = R[k] || {};
    const b = BR[k] || {};
    const e = ERR[k];
    return {
      chiave: k,
      etichetta: typeof r.label === 'string' && r.label ? r.label : k,
      nHolding: num(r.n_holdings),
      peso: num(r.weight_pct),
      nObs: num(b.n_obs),
      ultimaData: typeof b.last_date === 'string' ? b.last_date : null,
      errore: typeof e === 'string' && e.trim() ? e.trim() : null,
    };
  }).sort((a, b) => (b.peso ?? -1) - (a.peso ?? -1));
}

// ── CIO' CHE IL MOTORE HA SCARTATO ──────────────────────────────────────────
// ⚠ REGRESSIONE RISPETTO ALLA PAGINA SOSTITUITA, trovata dalla review: quella
//   stampava "N skipped" e un pannello "Skipped Holdings — with reasons". La
//   mia prima stesura non leggeva ne' `n_holdings_skipped` ne' `skipped_detail`,
//   cioe' buttava un motivo che il backend consegna gia' scritto.

export interface Scartato {
  ticker: string;
  peso: number | null;
  motivo: string;
}

export function scartati(fac: PayloadFattori | null): { n: number; righe: Scartato[] } {
  const n = num(fac ? fac.n_holdings_skipped : null) ?? 0;
  const d = fac && Array.isArray(fac.skipped_detail) ? fac.skipped_detail : [];
  return {
    n,
    righe: d.map(x => ({
      ticker: String((x && x.ticker) || '?'),
      peso: num(x && x.weight_pct),
      motivo: (x && typeof x.reason === 'string' && x.reason.trim())
        ? x.reason.trim() : perche('non-fornita'),
    })),
  };
}

// ── FORMATTAZIONE ───────────────────────────────────────────────────────────
// Raggruppamento esplicito con lingua corrente, come in lib/format.ts.

// stessa forma di TradeEntryPage.tsx:55 (F7): `useGrouping:'always'` non e' nel
// tipo NumberFormatOptions del lib di TS in uso, e il cast e' dichiarato invece
// che nascosto dietro un `any`.
const opz = (dec: number): Intl.NumberFormatOptions => {
  const o: Record<string, unknown> = {
    minimumFractionDigits: dec, maximumFractionDigits: dec, useGrouping: 'always',
  };
  return o as Intl.NumberFormatOptions;
};

export function n(v: unknown, dec = 2): string | null {
  const x = num(v);
  if (x === null) return null;
  return x.toLocaleString(localeDi(linguaCorrente()), opz(dec));
}

/** un numero che non c'e' non e' un trattino: e' una frase */
export function dato(v: unknown, dec = 2, m?: Muto): string {
  const s = n(v, dec);
  return s === null ? (m ? perche(m) : tr('factors.f001')) : s;
}

// ── ATTREZZO ────────────────────────────────────────────────────────────────

/** number finito o null. Mai NaN, mai 0 di ripiego, mai una stringa "12". */
export function num(v: unknown): number | null {
  return typeof v === 'number' && isFinite(v) ? v : null;
}
