import { useT } from '@/i18n/provider';
import { linguaCorrente } from '@/i18n/lingua';
import { localizePayload } from '@/lib/api-presentation';
import { leggiDetail } from '@/lib/quota';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import ModernPage from '@/components/ModernPage';
import { AlertCircle, Clock, FlaskConical, Play, RefreshCw } from 'lucide-react';
import { Bellomberg, MonteCarloResult, Position } from '@/lib/api';
import { contaBanco, costruisciPayload, etichettaSimula } from '@/lib/montecarlo';
import { Banco, type DraftMod, type ModAction } from './montecarlo/Banco';
import { Cono, Distribuzione } from './montecarlo/Grafici';
import { Aiuto, DrawdownPercorso, Esito, NoteMotore, Percentili, Probabilita, RischioCoda, ScenarioDeterministico } from './montecarlo/Viste';
import { etichettaMotore, fmtEUR, fmtEURS, fmtInt, stamp } from './montecarlo/formato';
import '@/components/nuova/nuova.css';
import './montecarlo.css';

/* Monte Carlo (Nuova, 05/10): una plancia sola. In alto i parametri; al centro il cono
   delle traiettorie fra l'esito (sinistra) e la distribuzione a scadenza (destra); in basso
   rischio di coda, drawdown nel percorso e note del motore. Il banco di prova (what-if) si
   apre in un pannello laterale su richiesta. Dati, chiamate e giudizio delle righe sono gli
   stessi della plancia precedente: cambia solo la presentazione. */

type Method = 'fhs' | 'block_bootstrap' | 'parametric_t';
type Drift  = 'zero' | 'shrinkage' | 'historical';
type Stress = 'none' | 'gfc_2008' | 'covid_2020' | 'shock_3sigma';
// `banco`: il payload delle modifiche SPEDITO (JSON), per dire quando le righe sono cambiate dopo la run
interface Parametri { horizonDays: number; nSims: number; lookbackYears: number; method: Method; drift: Drift; stress: Stress; banco: string }

let modIdCounter = 1;

// La pagina è disegnata per l'area contenuti di un 1920×1080 e, sugli schermi più grandi, si
// ingrandisce in proporzione invece di allungare riquadri vuoti (stesso impianto di Mandato).
// Sotto 1180 px di larghezza o 640 di altezza resta a misura naturale e scorre. Altezza
// MINIMA e non fissa: se il contenuto non ci sta, la pagina scorre invece di sovrapporsi.
const DISEGNO_L = 1630, DISEGNO_H = 940, MAX_SCALA = 1.8;
function useAdattaSchermo() {
  const ref = useRef<HTMLDivElement>(null);
  const [misura, setMisura] = useState<{ k: number; h: number | null }>({ k: 1, h: null });
  useEffect(() => {
    const el = ref.current, box = el?.closest('main')?.querySelector<HTMLElement>(':scope > .relative') || el?.parentElement;
    if (!el || !box || typeof ResizeObserver === 'undefined') return;
    const calcola = () => {
      const stile = getComputedStyle(box), sopra = el.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop;
      const largo = el.parentElement ? el.parentElement.getBoundingClientRect().width : box.clientWidth;
      const alto = box.clientHeight - sopra - parseFloat(stile.paddingBottom || '0') - 4;
      const adatta = largo >= 1180 && alto >= 640;
      const k = adatta ? Math.max(1, Math.min(alto / DISEGNO_H, largo / DISEGNO_L, MAX_SCALA)) : 1;
      setMisura(m => { const h = adatta ? Math.floor(alto / k) : null; return m.k === k && m.h === h ? m : { k, h }; });
    };
    const ro = new ResizeObserver(calcola); ro.observe(box); calcola();
    return () => ro.disconnect();
  }, []);
  return { ref, adatta: misura.h != null, style: misura.h == null ? undefined : { zoom: misura.k, minHeight: misura.h + 'px' } as CSSProperties };
}

export default function MonteCarloPage() {
  const tr = useT();
  // ⚠ l'ordine degli useState è un contratto dei test SSR (montecarlo.test.cjs semina le
  // righe del banco all'indice 6): lo stato nuovo si aggiunge IN CODA.
  const [horizonDays, setHorizonDays] = useState(252);
  const [nSims, setNSims] = useState(10000);
  const [lookbackYears, setLookbackYears] = useState(5);
  const [method, setMethod] = useState<Method>('fhs');
  const [drift, setDrift] = useState<Drift>('zero');
  const [stress, setStress] = useState<Stress>('none');

  const [mods, setMods] = useState<DraftMod[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [posErr, setPosErr] = useState<string | null>(null);

  const [resultRaw, setResult] = useState<MonteCarloResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<{ detail: string; draft?: DraftMod[] } | null>(null);
  const [ran, setRan] = useState<Parametri | null>(null);
  const [bancoAperto, setBancoAperto] = useState(false);
  const [bancoTab, setBancoTab] = useState<'mod' | 'pesi'>('mod');
  const [tracce, setTracce] = useState(true);
  const [soglie, setSoglie] = useState(true);
  const schermo = useAdattaSchermo();
  const result = useMemo(() => localizePayload(resultRaw), [resultRaw, tr]);
  const apriBancoRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    // Buco DICHIARATO (regola 14/07): picker vuoto muto = falso "book vuoto"
    Bellomberg.portfolio()
      .then(p => { setPosErr(null); setPositions(p.positions || []); })
      .catch((e: any) => setPosErr(leggiDetail(e?.response?.data?.detail ?? e?.message ?? e)));
  }, []);

  const ownedTickers = useMemo(
    () => Array.from(new Set(positions.map(p => p.ticker.toUpperCase()))).sort(),
    [positions]
  );

  const addMod = (action: ModAction) => {
    setMods(prev => [...prev, {
      id: modIdCounter++, action, ticker: '', amount_eur: '',
      amount_pct: action === 'trim' ? '20' : '', inputLanguage: linguaCorrente(),
    }]);
  };
  const updateMod = (id: number, patch: Partial<DraftMod>) => setMods(prev => prev.map(m => m.id === id ? { ...m, ...patch } : m));
  const removeMod = (id: number) => setMods(prev => prev.filter(m => m.id !== id));

  // Il giudizio sulle righe vive in lib/montecarlo, in una funzione sola da cui si deriva
  // anche il payload (§9-unquadragies-septdecies). `bookVuoto`: senza di lui una riga
  // TOGLI/RIDUCI a book non caricato direbbe «senza titolo», addossando all'utente una
  // mancanza che è dell'endpoint.
  const ctxBanco = useMemo(() => ({ bookVuoto: ownedTickers.length === 0 }), [ownedTickers]);
  const banco = useMemo(() => contaBanco(mods, ctxBanco), [mods, ctxBanco, tr]);

  const validateMod = async (id: number, tickerRaw: string) => {
    const t = tickerRaw.trim().toUpperCase();
    if (!t) return;
    updateMod(id, { validating: true, validation: undefined });
    try {
      const v = await Bellomberg.validateTicker(t);
      updateMod(id, { validating: false, validation: v });
    } catch (e: any) {
      updateMod(id, { validating: false, validation: { ok: false, symbol: t, error: leggiDetail(e?.response?.data?.detail ?? e?.message ?? e) } });
    }
  };

  const runSim = async (force: boolean = true) => {  // 202-C: il mount usa la cache backend
    if (banco.bloccanti > 0) {
      // difesa in profondità: il tasto è `disabled` sulla STESSA condizione, ma `runSim`
      // non deve dipendere da chi la chiama
      setError({ detail: '', draft: mods });
      return;
    }
    setRunning(true);
    setError(null);
    try {
      const payload = costruisciPayload(mods, ctxBanco);
      const params: Parametri = { horizonDays, nSims, lookbackYears, method, drift, stress, banco: JSON.stringify(payload) };
      const r = payload.length > 0
        ? await Bellomberg.portfolioMonteCarloV3({
            horizon_days: horizonDays, n_sims: nSims, lookback_years: lookbackYears,
            method, drift_mode: drift, stress, modifications: payload, force,
          })
        : await Bellomberg.portfolioMonteCarlo({
            horizon_days: horizonDays, n_sims: nSims, lookback_years: lookbackYears,
            method, drift_mode: drift, stress, force,
          });
      if (r.error) throw new Error(r.error);
      setResult(r);
      setRan(params);
    } catch (e: any) {
      setError({ detail: leggiDetail(e?.response?.data?.detail ?? e?.message ?? e) });
    } finally {
      setRunning(false);
    }
  };

  useEffect(() => { runSim(false); }, []); // 202-C: auto-run dal mount = cache, niente 10k sim gratuite // eslint-disable-line react-hooks/exhaustive-deps

  // FINESTRA VERTICALE condivisa da cono e distribuzione, ricavata dai DATI (mai da
  // costanti): contiene il cono vero E il 99% della massa d'arrivo, così le code tagliate
  // restano una briciola — e comunque contate e DICHIARATE (regola 14/07).
  const view = useMemo(() => {
    const fb = result?.fan_bands;
    if (!fb || !fb.days?.length) return null;
    let lo = Math.min(...fb.p5), hi = Math.max(...fb.p95);
    const th = result?.terminal_hist;
    if (th && th.counts.length && th.edges_eur.length === th.counts.length + 1) {
      const tot = th.counts.reduce((a, b) => a + b, 0);
      if (tot > 0) {
        let cum = 0, qlo = th.edges_eur[0], qhi = th.edges_eur[th.edges_eur.length - 1];
        for (let i = 0; i < th.counts.length; i++) {
          const before = cum; cum += th.counts[i];
          if (before < tot * 0.005 && cum >= tot * 0.005) qlo = th.edges_eur[i];
          if (before < tot * 0.995 && cum >= tot * 0.995) qhi = th.edges_eur[i + 1];
        }
        lo = Math.min(lo, qlo); hi = Math.max(hi, qhi);
      }
    }
    const pad = (hi - lo) * 0.04;
    return { lo: lo - pad, hi: hi + pad };
  }, [result?.fan_bands, result?.terminal_hist]);

  const outsideWindow = useMemo(() => {
    const th = result?.terminal_hist;
    if (!th || !view) return null;
    let out = 0, tot = 0;
    th.counts.forEach((c, i) => {
      const mid = (th.edges_eur[i] + th.edges_eur[i + 1]) / 2;
      tot += c;
      if (mid < view.lo || mid > view.hi) out += c;
    });
    return { out, tot };
  }, [result?.terminal_hist, view]);

  const bloccato = banco.bloccanti > 0;
  const simulaLabel = running ? tr('montecarlo.running', { n: fmtInt(nSims) }) : etichettaSimula(banco, bloccato);
  const simulaTitle = bloccato
    ? tr('montecarlo.f023') + banco.difetti.map(x => `${x.riga.ticker.trim()} — ${x.motivo}`).join(' · ')
    : banco.inerti > 0
      // il motivo si dice UNA volta per tipo, non per ogni riga inerte
      ? tr(banco.entrano === 1 ? 'montecarlo.dispatchOne' : 'montecarlo.dispatchMany', { sent: banco.entrano, total: banco.totale })
        + tr(banco.inerti === 1 ? 'montecarlo.asideOne' : 'montecarlo.asideMany', { count: banco.inerti })
        + Array.from(new Set(banco.inerziali.map(x => x.motivo))).join(' · ')
      : undefined;
  const dirty = !!ran && (ran.horizonDays !== horizonDays || ran.nSims !== nSims || ran.lookbackYears !== lookbackYears
    || ran.method !== method || ran.drift !== drift || ran.stress !== stress);
  // review PR #12: col banco chiuso le righe non si vedono più accanto ai risultati, quindi
  // si dichiara quando quelle che entrerebbero nel calcolo non sono più quelle spedite
  const firmaBanco = useMemo(() => JSON.stringify(costruisciPayload(mods, ctxBanco)), [mods, ctxBanco, tr]);
  const dirtyBanco = !!ran && ran.banco !== firmaBanco;
  const apriBanco = (tab: 'mod' | 'pesi') => { setBancoTab(tab); setBancoAperto(true); };
  const chiudiBanco = () => { setBancoAperto(false); apriBancoRef.current?.focus(); };
  const conConfronto = result && result.nav_pre_eur != null && result.nav_post_eur != null;
  const scartate = result?.skipped_modifications?.length ?? 0;

  const sel = <T extends string | number>(value: T, set: (v: T) => void, opts: [T, string][], label: string) =>
    <span className="mc-sel"><select value={value} aria-label={label}
      onChange={e => set((typeof value === 'number' ? parseInt(e.target.value) : e.target.value) as T)}>
      {opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
    </select></span>;
  const seg = (value: number, set: (v: number) => void, opts: [number, string][], label: string) =>
    <div className="bbn-seg mc-seg" role="group" aria-label={label}>{opts.map(([v, l]) =>
      <button key={v} type="button" aria-pressed={value === v} className={value === v ? 'is-on' : ''} onClick={() => set(v)}>{l}</button>)}</div>;

  return (
    <ModernPage page="montecarlo" render={() => (
    <div className="bbn-mc bbn-font" data-fit={schermo.adatta ? 'on' : 'off'} ref={schermo.ref} style={schermo.style}>
      <header className="mc-head">
        <h1>Monte Carlo</h1>
        {result && !error && <>
          <span className="bbn-chip mc-chip"><Clock aria-hidden="true" />{tr('montecarlo.calculatedAt', { when: stamp(result.timestamp) })}</span>
          <span className="bbn-chip">{result.n_assets === 1 ? tr('montecarlo.pathsAssetsOne', { paths: fmtInt(result.n_sims) }) : tr('montecarlo.pathsAssets', { paths: fmtInt(result.n_sims), assets: result.n_assets ?? tr('montecarlo.na') })}</span>
        </>}
        {conConfronto && !error && <div className="mc-cmp" data-cmp>
          <span className="mc-cmp-t"><FlaskConical aria-hidden="true" />{tr('montecarlo.cmpTitle')}</span>
          <span className="mc-cmp-k">{tr('montecarlo.cmpInvested')}</span>
          <b className="num" title={tr('montecarlo.cmpBefore')}>{fmtEUR(result!.nav_pre_eur)}</b><span className="mc-cmp-k">→</span>
          <b className="num" title={tr('montecarlo.cmpAfter')}>{fmtEUR(result!.nav_post_eur)}</b>
          <span className={'bbn-pill ' + (result!.nav_post_eur! - result!.nav_pre_eur! >= 0 ? 'is-su' : 'is-giu')}>{fmtEURS(result!.nav_post_eur! - result!.nav_pre_eur!)}</span>
          {scartate > 0 && <button type="button" className="bbn-warn-pill mc-cmp-skip" title={tr('montecarlo.cmpSkippedHelp')} onClick={() => apriBanco('mod')}>
            {scartate === 1 ? tr('montecarlo.cmpSkippedOne') : tr('montecarlo.cmpSkippedMany', { count: scartate })}</button>}
          {result!.weights_pre && result!.weights_post && <button type="button" className="bbn-link" onClick={() => apriBanco('pesi')}>{tr('montecarlo.cmpWeights')}</button>}
        </div>}
        <span className="bbn-grow" />
        <button type="button" ref={apriBancoRef} className="bbn-btn" data-action="open-banco" aria-haspopup="dialog" aria-expanded={bancoAperto}
                onClick={() => apriBanco('mod')}>
          <FlaskConical aria-hidden="true" />{tr('montecarlo.tryChanges')}{mods.length > 0 && <span className="mc-count">{mods.length}</span>}</button>
      </header>

      <section className="bbn-card mc-params" aria-label={tr('montecarlo.pMethod')}>
        <label className="mc-fld"><span className="mc-fld-lab">{tr('montecarlo.pMethod')} <Aiuto testo={tr('montecarlo.pMethodHelp')} /></span>
          {sel<Method>(method, setMethod, [['fhs', tr('montecarlo.methodFhs')], ['block_bootstrap', 'Block bootstrap'], ['parametric_t', tr('montecarlo.methodParam')]], tr('montecarlo.pMethod'))}</label>
        <label className="mc-fld"><span className="mc-fld-lab">{tr('montecarlo.pDrift')} <Aiuto testo={tr('montecarlo.pDriftHelp')} /></span>
          {sel<Drift>(drift, setDrift, [['zero', tr('montecarlo.driftZero')], ['shrinkage', tr('montecarlo.driftShrink')], ['historical', tr('montecarlo.driftHist')]], tr('montecarlo.pDrift'))}</label>
        <label className="mc-fld"><span className="mc-fld-lab">{tr('montecarlo.pLookback')} <Aiuto testo={tr('montecarlo.pLookbackHelp')} /></span>
          {sel<number>(lookbackYears, setLookbackYears, [[1, tr('montecarlo.lb1')], [2, tr('montecarlo.lb2')], [5, tr('montecarlo.lb5')], [10, tr('montecarlo.lb10')]], tr('montecarlo.pLookback'))}</label>
        <label className="mc-fld"><span className="mc-fld-lab">{tr('montecarlo.pStress')} <Aiuto testo={tr('montecarlo.pStressHelp')} /></span>
          {sel<Stress>(stress, setStress, [['none', etichettaMotore('stress', 'none')], ['gfc_2008', 'GFC 2008'], ['covid_2020', 'COVID 2020'], ['shock_3sigma', etichettaMotore('stress', 'shock_3sigma')]], tr('montecarlo.pStress'))}</label>
        <span className="mc-vsep" />
        <div className="mc-fld"><span className="mc-fld-lab">{tr('montecarlo.pHorizon')}</span>
          {seg(horizonDays, setHorizonDays, [[21, tr('montecarlo.h21')], [63, tr('montecarlo.h63')], [126, tr('montecarlo.h126')], [252, tr('montecarlo.h252')], [504, tr('montecarlo.h504')]], tr('montecarlo.pHorizon'))}</div>
        <div className="mc-fld"><span className="mc-fld-lab">{tr('montecarlo.pPaths')} <Aiuto testo={tr('montecarlo.pPathsHelp')} /></span>
          {seg(nSims, setNSims, [3000, 10000, 25000, 50000].map(n => [n, fmtInt(n)] as [number, string]), tr('montecarlo.pPaths'))}</div>
        <div className="mc-run">
          {dirty && !running && <span className="bbn-warn-pill" title={tr('montecarlo.dirtyHelp')}>{tr('montecarlo.dirty')}</span>}
          {dirtyBanco && !running && <span className="bbn-warn-pill" title={tr('montecarlo.dirtyModsHelp')}>{tr('montecarlo.dirtyMods')}</span>}
          <button type="button" className="bbn-btn is-primary" data-action="simulate" onClick={() => runSim(true)} disabled={running || bloccato} title={simulaTitle}>
            {running ? <RefreshCw aria-hidden="true" className="animate-spin" /> : <Play aria-hidden="true" />}{simulaLabel}</button>
        </div>
      </section>

      {posErr !== null && !bancoAperto && <div className="mc-note is-bad" role="alert"><AlertCircle aria-hidden="true" />
        <span>{tr('montecarlo.portfolioUnavailable')} — {posErr || tr('montecarlo.errorMissing')}</span></div>}

      {error ? <>
        <div className="mc-note is-bad" role="alert" data-error><AlertCircle aria-hidden="true" />
          <span><b>{tr('montecarlo.simFailed')}</b> {error.draft
            ? tr('montecarlo.f003') + contaBanco(error.draft, ctxBanco).difetti.map(x => `${x.riga.ticker.trim()}: ${x.motivo}`).join(' · ')
            : error.detail || tr('montecarlo.errorMissing')} {tr('montecarlo.simFailedNote')}</span>
          <span className="bbn-grow" />
          <button type="button" className="bbn-link mc-note-act" disabled={running || bloccato} onClick={() => runSim(true)}>{tr('montecarlo.retry')}</button>
        </div>
        <section className="bbn-card mc-empty"><div><b>{tr('montecarlo.emptyTitle')}</b>{tr('montecarlo.emptyBody')}</div></section>
      </> : result ? <>
        {result.stress_fallback && <div className="mc-note is-warn"><AlertCircle aria-hidden="true" />
          <span>{tr('montecarlo.fallbackBanner', { req: etichettaMotore('stress', result.stress_requested) })}</span></div>}
        {/* natura dello stress (sync 2a72bf8): deterministico = blocco dello scenario con
            l'esito da leggere; fisso-poi-simulato = metriche CONDIZIONATE, lo si dice sopra.
            Senza la frase del motore parla quella della pagina: la natura non resta muta. */}
        {result.deterministic_scenario
          ? <ScenarioDeterministico r={result} />
          : (result.stress_nature === 'fixed_then_simulated' || result.stress_nature === 'deterministic')
            && <div className="mc-note is-warn" data-stress-nature={result.stress_nature}><AlertCircle aria-hidden="true" />
              <span>{result.stress_nature_label || tr(result.stress_nature === 'deterministic' ? 'montecarlo.natureDetFallback' : 'montecarlo.natureFixedFallback')}</span></div>}
        <div className={'mc-plancia' + (running ? ' is-loading' : '')} aria-busy={running}>
          <div className="mc-upper">
            <div className="mc-col is-left"><Esito r={result} /><Probabilita r={result} /></div>
            <Cono r={result} view={view} tracce={tracce} soglie={soglie} onTracce={() => setTracce(x => !x)} onSoglie={() => setSoglie(x => !x)} />
            <div className="mc-col is-right"><Distribuzione r={result} view={view} fuori={outsideWindow} /><Percentili r={result} /></div>
          </div>
          <div className="mc-lower"><RischioCoda r={result} /><DrawdownPercorso r={result} /><NoteMotore r={result} drift={ran?.drift} stress={ran?.stress} /></div>
        </div>
      </> : <section className="bbn-card mc-empty" aria-busy={running}><div>
        {running && <RefreshCw aria-hidden="true" className="animate-spin" />}
        <b>{tr('montecarlo.loadingTitle')}</b>{tr('montecarlo.loadingBody')}</div></section>}

      {bancoAperto && <Banco tab={bancoTab} onTab={setBancoTab} onClose={chiudiBanco} mods={mods} ctx={ctxBanco} ownedTickers={ownedTickers}
        posErr={posErr} banco={banco} running={running} simulaLabel={simulaLabel} simulaTitle={simulaTitle}
        onAdd={addMod} onUpdate={updateMod} onRemove={removeMod} onClear={() => setMods([])} onValidate={validateMod}
        onRun={() => { chiudiBanco(); runSim(true); }} result={result} />}
    </div>
    )} />
  );
}
