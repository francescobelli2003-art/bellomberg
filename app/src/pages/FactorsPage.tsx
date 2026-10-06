import { localizePayload } from '@/lib/api-presentation';
import { leggiDetail, dataIt } from '@/lib/quota';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Clock, RefreshCw } from 'lucide-react';
import ModernPage from '@/components/ModernPage';
import { Segmenti } from '@/components/nuova/Card';
import { Bellomberg } from '@/lib/api';
import type { PortfolioSnapshot, AdvancedMetrics, PortfolioRisk } from '@/lib/api';
import {
  ORDINE_CHIAMATE, COSTO_CACHE,
  calibro, copertura, confrontoFinestre, scalaComune, alphaPerTitolo,
  contaRumore, regioni, scartati, ritardoGiorni, num, perche,
} from '@/lib/fattori';
import type { PayloadFattori, PayloadRiconciliazione } from '@/lib/fattori';
import { useLingua } from '@/i18n/provider';
import { cifra, limiteAlpha, ordina, pct, righeTitoli } from './fattori/calcoli';
import type { Ordine } from './fattori/calcoli';
import MetodoFonti from './fattori/MetodoFonti';
import type { SezioneMetodo } from './fattori/MetodoFonti';
import { parole } from './fattori/parole';
import VistaBeta, { nomeFonte, viaLibera } from './fattori/VistaBeta';
import VistaFattori from './fattori/VistaFattori';
import VistaTitoli from './fattori/VistaTitoli';
import './fattori-nuova.css';

/* ============================================================================
   FATTORI DI RISCHIO in stile Nuova (05/10/2026). Mockup approvato:
   outputs/fattori-nuova/mockup.html — tre viste a segmenti (Beta e misure ·
   Fattori · Titoli), beta riconciliato come protagonista, mappa titoli ×
   fattori colorata solo dove il coefficiente e' significativo, note tecniche
   nei «?» e nel pannello «Metodo e fonti».

   Questo componente resta il controller della rotta: le sei letture, l'ordine
   misurato delle chiamate (lib/fattori.ts, ORDINE_CHIAMATE) e il guardiano di
   rientranza sono quelli di F6. Le viste in pages/fattori/ ricevono solo dati.
   Tutte le derivazioni delle misure stanno in lib/fattori.ts.
   ========================================================================== */

type Vista = 'beta' | 'fattori' | 'titoli';

export default function FactorsPage() {
  const lingua = useLingua();
  // una sorgente per stato, e ogni errore si DICHIARA: mai un buco muto
  const [facRaw, setFac] = useState<PayloadFattori | null>(null);
  const [facErr, setFacErr] = useState<string | null>(null);
  const [recRaw, setRec] = useState<PayloadRiconciliazione | null>(null);
  const [recErr, setRecErr] = useState<string | null>(null);
  const [recAtt, setRecAtt] = useState(false);
  const [fac3Raw, setFac3] = useState<PayloadFattori | null>(null);
  const [fac3Err, setFac3Err] = useState<string | null>(null);
  const [snapRaw, setSnap] = useState<PortfolioSnapshot | null>(null);
  const [snapErr, setSnapErr] = useState<string | null>(null);
  const [advRaw, setAdv] = useState<AdvancedMetrics | null>(null);
  // ⚠ `.catch(() => setAdv(null))` faceva sparire due misure senza dire che una rotta non aveva
  //   risposto: il fallback silenzioso vietato il 14/07. Ogni lettura ha il suo errore.
  const [advErr, setAdvErr] = useState<string | null>(null);
  const [riskRaw, setRisk] = useState<PortfolioRisk | null>(null);
  const [riskErr, setRiskErr] = useState<string | null>(null);
  const [caricando, setCaricando] = useState(false);
  const [riscalda, setRiscalda] = useState(false);
  const [scelta, setScelta] = useState(0);
  const [riscaldaErr, setRiscaldaErr] = useState<string | null>(null);
  // stato della presentazione Nuova (in coda: i test SSR leggono gli useState per indice)
  const [vista, setVista] = useState<Vista>('beta');
  const [metodoAperto, setMetodoAperto] = useState(false);
  const [titoloScelto, setTitoloScelto] = useState<string | null>(null);
  const [ordine, setOrdine] = useState<Ordine>('peso');
  // Only explicitly authored variants are projected; the original response is retained.
  const fac = useMemo(() => localizePayload(facRaw), [facRaw, lingua]);
  const rec = useMemo(() => localizePayload(recRaw), [recRaw, lingua]);
  const fac3 = useMemo(() => localizePayload(fac3Raw), [fac3Raw, lingua]);
  const snap = useMemo(() => localizePayload(snapRaw), [snapRaw, lingua]);
  const adv = useMemo(() => localizePayload(advRaw), [advRaw, lingua]);
  const risk = useMemo(() => localizePayload(riskRaw), [riskRaw, lingua]);
  const vivo = useRef(true);

  useEffect(() => () => { vivo.current = false; }, []);

  const err = (e: unknown): string => {
    const x = e as { response?: { data?: { detail?: unknown } }; message?: unknown };
    return leggiDetail(x?.response?.data?.detail ?? x?.message ?? e);
  };

  /* ⚠ L'ORDINE E' MISURATO, NON SCELTO A GUSTO. Vedi ORDINE_CHIAMATE in lib/fattori.ts: la
     cache del motore fattoriale tiene UNA finestra alla volta e la pagina ne usa due. Si
     disegna appena arriva la 1y, si riempie il righello quando arriva la riconciliazione, si
     prende la 3y gratis, e alla fine si rimette la 1y in cache per chi viene dopo.
     ⚠ RIENTRANZA: un secondo click durante la riconciliazione aprirebbe una SECONDA pipeline
     sullo stesso motore a cache singola. Il guardiano sta qui, non solo su `disabled`. */
  const inCorso = useRef(false);

  const carica = useCallback(async (force = false) => {
    if (inCorso.current) return;
    inCorso.current = true;
    setCaricando(true);
    setFacErr(null); setRecErr(null); setFac3Err(null); setSnapErr(null);
    setAdvErr(null); setRiskErr(null); setRiscaldaErr(null);

    // ⚠ Una risposta 200 puo' portare un campo `error` dentro (/metrics/advanced, fattori).
    const buono = <T extends { error?: string }>(r: T): T => {
      if (r && r.error) throw new Error(r.error);
      return r;
    };

    // queste tre NON toccano la cache del motore fattoriale: partono subito
    Bellomberg.portfolio()
      .then(r => { if (vivo.current) setSnap(r); })
      .catch(e => { if (vivo.current) { setSnap(null); setSnapErr(err(e)); } });
    Bellomberg.metricsAdvanced()
      .then(r => { if (vivo.current) setAdv(buono(r)); })
      .catch(e => { if (vivo.current) { setAdv(null); setAdvErr(err(e)); } });
    Bellomberg.portfolioRisk()
      .then(r => { if (vivo.current) setRisk(buono(r as { error?: string } & PortfolioRisk)); })
      .catch(e => { if (vivo.current) { setRisk(null); setRiskErr(err(e)); } });

    // 1. la finestra che la pagina mostra: appena arriva, si disegna
    try {
      const r: PayloadFattori = await Bellomberg.portfolioFactors(force);
      if (r && r.error) throw new Error(r.error);
      if (vivo.current) setFac(r);
    } catch (e) {
      if (vivo.current) { setFac(null); setFacErr(err(e)); }
    }
    if (vivo.current) setCaricando(false);

    // 2. il controllo del beta. Costa ~8 s perche' costringe il motore alla 3y.
    if (vivo.current) setRecAtt(true);
    try {
      const r = await Bellomberg.betaReconcile();
      if (vivo.current) setRec(buono(r as { error?: string }) as typeof r);
    } catch (e) {
      if (vivo.current) { setRec(null); setRecErr(err(e)); }
    }
    if (vivo.current) setRecAtt(false);

    // 3. la 3y ora e' in cache: gratis (misurato 0,00-0,03 s)
    try {
      const r: PayloadFattori = await Bellomberg.portfolioFactorsPeriodo('3y');
      if (vivo.current) setFac3(buono(r));
    } catch (e) {
      if (vivo.current) { setFac3(null); setFac3Err(err(e)); }
    }

    // 4. la cortesia: si rimette la 1y in cache come l'avevamo trovata (dichiarato in Metodo).
    if (vivo.current) setRiscalda(true);
    try { buono(await Bellomberg.portfolioFactors(false)); }
    catch (e) { if (vivo.current) setRiscaldaErr(err(e)); }
    if (vivo.current) setRiscalda(false);
    inCorso.current = false;
  }, []);

  useEffect(() => { carica(false); }, [carica]);

  // ── le derivazioni, tutte da lib/fattori.ts ───────────────────────────────
  const w = parole();
  const cal = calibro(rec, fac);
  const cop = copertura(fac, snap);
  const conf = confrontoFinestre(
    fac ? fac.portfolio_aggregate || null : null,
    fac3 ? fac3.portfolio_aggregate || null : null,
  );
  const scala = scalaComune(conf);
  const alfa = alphaPerTitolo(fac);
  const rumore = contaRumore(fac);
  const reg = regioni(fac);
  const fuori = scartati(fac);
  const ritardo = ritardoGiorni(fac ? fac.ff_data_last_date : null);
  const nomi = new Map((snap?.positions || []).map(p => [p.ticker, p.nome] as [string, string]));
  const righe = ordina(righeTitoli(fac, alfa, nomi), ordine);
  const obs = alfa.map(r => r.nObs).filter((v): v is number => v !== null);

  const finestra = (p: string | undefined | null) => {
    const k = String(p || '').toLowerCase();
    return k === '1y' ? w.year1 : k === '3y' ? w.years3 : k ? k.toUpperCase() : w.na;
  };
  const periodo = finestra(fac?.period || '1y');

  const alphaFattoriale = num(fac && fac.portfolio_aggregate ? fac.portfolio_aggregate.alpha_annualized_pct : null);
  const alphaBenchmark = num(adv && (adv as { benchmark?: { alpha_annual_pct?: number } }).benchmark
    ? (adv as { benchmark?: { alpha_annual_pct?: number } }).benchmark!.alpha_annual_pct : null);
  const sharpeRisk = num(risk && risk.portfolio ? risk.portfolio.sharpe : null);
  const sharpeAdv = num(adv ? (adv as { sharpe?: number }).sharpe : null);
  const rf = num(adv ? (adv as { risk_free_used?: number }).risk_free_used : null);
  // le frasi che il backend manda gia' scritte (allineamento delle serie, nota sullo Sharpe)
  const allineamento = adv && typeof (adv as { benchmark_alignment?: string }).benchmark_alignment === 'string'
    ? (adv as { benchmark_alignment?: string }).benchmark_alignment! : null;
  const notaSharpe = risk && typeof (risk as { sharpe_note?: string }).sharpe_note === 'string'
    ? (risk as { sharpe_note?: string }).sharpe_note! : null;
  const nSigAlfa = alfa.filter(r => r.ic && r.ic.sig).length;
  // il guardrail beta: via libera ed esclusioni per osservazioni. Il codice del punteggio quant
  // (`metrics.beta_guardrail`) oggi non arriva da nessuna rotta: non lo si ricostruisce qui
  // (sarebbe una seconda copia della regola di specialist_scores); `fraseGuardrailBeta` e' pronta.
  const libero = viaLibera(cal);
  const esclusioni = cal.lancette.some(l => l.esclusa !== null) || cal.esclusaSenzaBeta.length > 0;
  const nTitoli = num(fac?.n_holdings_analyzed);

  // ── il righello: navigazione da tastiera ──────────────────────────────────
  // Le lancette sono ordinate per valore da `calibro()`: l'indice E' la posizione, quindi ←/→
  // seguono il righello. Il roving tabindex non basta: va spostato anche il fuoco vero.
  const bottoni = useRef<(HTMLButtonElement | null)[]>([]);
  const vai = (i: number) => {
    const j = Math.max(0, Math.min(cal.lancette.length - 1, i));
    setScelta(j);
    const b = bottoni.current[j];
    if (b) b.focus();
  };
  const tasti = (e: React.KeyboardEvent) => {
    if (!cal.lancette.length) return;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { e.preventDefault(); vai(scelta + 1); }
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') { e.preventDefault(); vai(scelta - 1); }
    else if (e.key === 'Home') { e.preventDefault(); vai(0); }
    else if (e.key === 'End') { e.preventDefault(); vai(cal.lancette.length - 1); }
  };
  useEffect(() => {
    if (scelta > cal.lancette.length - 1) setScelta(Math.max(0, cal.lancette.length - 1));
  }, [cal.lancette.length, scelta]);

  const fase = caricando ? w.phaseFactors : recAtt ? w.phaseReconcile : riscalda ? w.phaseWarm : null;
  const avvisoMisure = [
    advErr !== null ? w.advUnavailable(advErr || w.errorMissing) : null,
    riskErr !== null ? w.riskUnavailable(riskErr || w.errorMissing) : null,
  ].filter((x): x is string => x !== null);

  const sezioni: SezioneMetodo[] = [
    { titolo: w.mDefs, testo: [w.mDefsNote],
      voci: cal.lancette.map(l => [nomeFonte(l, w, periodo), l.definizione || perche(l.definizioneMuta)] as [string, string]) },
    ...(cal.verdetto || cal.nota ? [{ titolo: w.mGuard, avviso: (cal.verdetto !== null && !libero) || esclusioni,
      testo: [cal.verdetto ? w.verdictTitle(cal.verdetto) : '', cal.nota || '',
        cal.minObs !== null ? w.minObsLine(cifra(cal.minObs, 0)) : ''].filter(Boolean) }] : []),
    ...(fac ? [{ titolo: w.mQuality, voci: [
      [w.qSig, `${rumore.significative} / ${rumore.celle}`],
      [w.qAlpha, `${cifra(num(fac.n_alpha_significant_5pct), 0)} / ${cifra(num(fac.n_holdings_analyzed), 0)}`],
      [w.qR2, pct(num(fac.portfolio_avg_r_squared) !== null ? fac.portfolio_avg_r_squared! * 100 : null, 1)],
      [w.qOld, String(rumore.colorateRegolaVecchia)],
      [w.qOldNs, String(rumore.colorateNonSignificative)],
      [w.qSat, String(rumore.inSaturazione)],
    ] as Array<[string, string]> }] : []),
    ...(allineamento || notaSharpe ? [{ titolo: w.mAlign, testo: [allineamento, notaSharpe].filter((x): x is string => !!x) }] : []),
    { titolo: w.mCost, avviso: riscaldaErr !== null, testo: [
      w.mCostText(cifra(COSTO_CACHE.totale, 1)),
      ...(riscalda ? [w.mCostRunning] : []),
      ...(riscaldaErr !== null ? [w.cacheRestoreFailed(riscaldaErr || w.errorMissing)] : []),
    ] },
  ];
  const daLeggere = riscaldaErr !== null || cal.fontiCadute.length > 0 || cop.avvisi.length > 0
    || (cal.verdetto !== null && !libero) || esclusioni;

  return (
    <ModernPage page="factors" render={() => (
      <div className="bbn-fattori" data-vista={vista}>
        <header className="fat-top">
          <h1>{w.title}</h1>
          {fac && (
            <span className="fat-chip" title={[w.modelHint, fac.model, fac.version].filter(Boolean).join(' · ')}>
              <b>{w.window(periodo)}</b> · {nTitoli === null ? <span className="fat-muted">{w.holdingsMissing}</span>
                : nTitoli === 1 ? w.holdingsOne : w.holdings(cifra(nTitoli, 0))}
            </span>
          )}
          {/* DECISIONE PM 27/07: il ritardo dei fattori si dichiara sempre in testata. */}
          <span className={'fat-pill ' + (ritardo !== null && ritardo > 90 ? 'is-bad' : 'is-warn')} title={w.delayHint}>
            <Clock size={13} aria-hidden="true" />
            {fac && fac.ff_data_last_date && ritardo !== null ? w.delay(dataIt(fac.ff_data_last_date), ritardo) : w.delayMissing}
          </span>
          <span className="bbn-grow" />
          <Segmenti<Vista> etichetta={w.views} valore={vista} onChange={setVista} className="is-large"
            opzioni={[{ id: 'beta', testo: w.viewBeta }, { id: 'fattori', testo: w.viewFactors }, { id: 'titoli', testo: w.viewSecurities }]} />
          <button type="button" className="bbn-btn fat-method-btn" onClick={() => setMetodoAperto(true)}>
            {daLeggere && <span className="fat-warn-dot" title={w.methodWarn} />}{w.method}
          </button>
          {/* il bottone dice in quale fase si trova: a riposo mentre lavora sarebbe una bugia */}
          <button type="button" className="bbn-btn bt" onClick={() => carica(true)}
            disabled={caricando || recAtt || riscalda} title={w.recalcHint(cifra(COSTO_CACHE.totale, 0))}>
            <RefreshCw size={15} aria-hidden="true" className={fase ? 'fat-spin' : undefined} />
            <span>{fase || w.recalc}</span>
          </button>
        </header>

        {facErr !== null && (
          <div className="avviso fat-note is-bad" role="alert"><span className="txt">{w.factorsUnavailable(facErr || w.errorMissing)}</span></div>
        )}
        {riscaldaErr !== null && (
          <div className="fat-note is-warn" role="alert"><span className="txt">{w.cacheRestoreFailed(riscaldaErr || w.errorMissing)}</span></div>
        )}

        {/* Tutte e tre le viste restano montate (la scelta cambia solo `hidden`): il righello e la
            tabella degli alpha esistono sempre, e cambiare vista non ricostruisce niente. */}
        <div className="fat-views">
          <div hidden={vista !== 'beta'} className="fat-slot">
            <VistaBeta w={w} cal={cal} periodo={periodo} scelta={scelta} onScelta={setScelta} onTasti={tasti}
              bottone={(i, el) => { bottoni.current[i] = el; }}
              inAttesa={recAtt || caricando} recAtt={recAtt} recErr={recErr} facErr={facErr}
              costoRiconciliazione={COSTO_CACHE.reconcile}
              alpha={{ dec: 1, unita: '%', scarto: w.gapPp,
                a: { nome: w.alphaA, v: alphaFattoriale,
                  come: w.alphaAHow(cifra(num(fac?.n_holdings_analyzed), 0), periodo, fac?.ff_data_last_date ? dataIt(fac.ff_data_last_date) : w.na) },
                b: { nome: w.alphaB, v: alphaBenchmark, come: w.alphaBHow } }}
              sharpe={{ dec: 2, unita: '', scarto: v => v,
                a: { nome: w.sharpeA, v: sharpeRisk, come: w.sharpeAHow },
                b: { nome: w.sharpeB, v: sharpeAdv, come: w.sharpeBHow(rf === null ? w.na : pct(rf * 100, 2)) } }}
              avvisoMisure={avvisoMisure} />
          </div>
          <div hidden={vista !== 'fattori'} className="fat-slot">
            <VistaFattori w={w} conf={conf} scala={scala} etichetta1={periodo} etichetta3={w.years3}
              stato3={fac3 ? 'ok' : fac3Err !== null ? 'errore' : 'attesa'} err3={fac3Err}
              nAnalizzati={nTitoli} cop={cop} snapErr={snapErr} reg={reg} fuori={fuori}
              ultimoDato={fac?.ff_data_last_date || null} ritardo={ritardo}
              storico={obs.length ? [Math.min(...obs), Math.max(...obs)] : null} />
          </div>
          <div hidden={vista !== 'titoli'} className="fat-slot">
            <VistaTitoli w={w} righe={righe} ordine={ordine} onOrdine={setOrdine}
              scelto={titoloScelto} onScelto={setTitoloScelto} limAlfa={limiteAlpha(alfa)}
              celleSig={rumore.significative} celle={rumore.celle} alfaSig={nSigAlfa}
              regioni={new Map(reg.map(r => [r.chiave, r.etichetta] as [string, string]))} />
          </div>
        </div>

        <MetodoFonti aperto={metodoAperto} onChiudi={() => setMetodoAperto(false)} sezioni={sezioni} w={w} />
      </div>
    )} />
  );
}

// riferimento usato solo per far fallire il compilatore se ORDINE_CHIAMATE cambia senza che
// questo file venga riletto: l'ordine e' una decisione misurata, non un dettaglio implementativo.
void ORDINE_CHIAMATE;
