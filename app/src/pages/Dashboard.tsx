import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Cpu, ListChecks, Play, RefreshCw } from 'lucide-react';
import { useT } from '@/i18n/provider';
import { localizePayload } from '@/lib/api-presentation';
import { t as tr } from '@/i18n/t';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import { Bellomberg } from '@/lib/api';
import type { BenchmarkPayload, Decision, MktQuote, NavHistory, PortfolioRisk, PortfolioSnapshot, TwrPayload } from '@/lib/api';
import { fmtEUR, fmtNum, fmtPct } from '@/lib/format';
import { leggiQuota, motivoChiamata, leggiDetail } from '@/lib/quota';
import { leggiCurva } from '@/lib/curva';
import type { EsitoCurva } from '@/lib/curva';
import { conservaDettaglioRun, dettaglioLeggibile, statusHttp } from '@/lib/mandato';
import { computeDailyPnl, liveAsOf } from '@/lib/dailypl';
import { portfolioValues } from '@/lib/portfolio-values';
import { caricaLoghi } from '@/lib/loghi-remoti';
import RunConfirmDialog from '@/components/RunConfirmDialog';
import DettaglioTitolo from '@/components/nuova/DettaglioTitolo';
import PastigliaVariazione from '@/components/nuova/PastigliaVariazione';
import HeroPatrimonio from './dashboard/HeroPatrimonio';
import type { Periodo } from './dashboard/HeroPatrimonio';
import ListaPosizioni, { nomeTitolo } from './dashboard/ListaPosizioni';
import type { Metrica } from './dashboard/ListaPosizioni';
import CardNotizie from './dashboard/CardNotizie';
import CardAllocazione from './dashboard/CardAllocazione';
import type { VistaAllocazione } from './dashboard/CardAllocazione';
import CardRischio from './dashboard/CardRischio';
import CardEventi from './dashboard/CardEventi';
import { parole } from './dashboard/parole';
import './dashboard-modern.css';

type ReadFailure = { error: unknown };
const finito = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

function readFailureText(failure: ReadFailure): string {
  const error = failure.error as { response?: { data?: { detail?: unknown } }; message?: string };
  const detail = leggiDetail(error?.response?.data?.detail) || error?.message;
  const status = statusHttp(error);
  return (status ? `HTTP ${status} · ` : '') + (detail || tr('dashboard.client_request_failed'));
}

/** Preferenze di vista: ricordate solo in questo browser, mai dati. */
function usePreferenza<T extends string>(chiave: string, valide: readonly T[], iniziale: T): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try { const saved = localStorage.getItem(chiave) as T | null; return saved && valide.includes(saved) ? saved : iniziale; } catch { return iniziale; }
  });
  const set = (next: T) => {
    setValue(next);
    try { localStorage.setItem(chiave, next); } catch { /* preferenza non salvabile: vale per questa sessione */ }
  };
  return [value, set];
}

// nav_history: regge solo il rendimento ITD mostrato nei dettagli del patrimonio.
// Il guasto resta nello STESSO slot di stato (i test SSR seminano gli useState per indice) e il
// suo MOTIVO arriva al chip ITD: n.d. senza perché era un ingoio silenzioso (regola 14/07, come in e44e955).
type GuastoNav = { guasto: 'empty' } | { guasto: 'source'; detail: string } | { guasto: 'transport'; error: unknown };
function useNavHistory(): { hist: NavHistory | null; motivo: string | null } {
  const t = useT();
  const [histRaw, setHist] = useState<NavHistory | GuastoNav | null>(null);
  const caricata = histRaw && !('guasto' in histRaw) ? histRaw : null;
  const hist = useMemo(() => localizePayload(caricata), [caricata, t]);
  useEffect(() => {
    let m = true;
    Bellomberg.navHistory(false)
      .then(h => { if (!m) return; setHist(h && !h.error ? h : h?.error ? { guasto: 'source', detail: h.error } : { guasto: 'empty' }); })
      .catch(error => { if (m) setHist({ guasto: 'transport', error }); });
    return () => { m = false; };
  }, []);
  const g = histRaw && 'guasto' in histRaw ? histRaw : null;
  const motivo = g?.guasto === 'empty' ? tr('dashboard.nav_no_series')
    : g?.guasto === 'source' ? g.detail
    : g?.guasto === 'transport' ? motivoChiamata(g.error) : null;
  return { hist, motivo };
}

export default function Dashboard() {
  const t = useT();
  const w = parole();
  const navigate = useNavigate();
  const [snapRaw, setSnap] = useState<PortfolioSnapshot | null>(null);
  const snap = useMemo(() => localizePayload(snapRaw), [snapRaw, t]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [riskRaw, setRisk] = useState<PortfolioRisk | null>(null);
  const risk = useMemo(() => localizePayload(riskRaw), [riskRaw, t]);
  const [riskLoading, setRiskLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [runNotice, setRunNotice] = useState<{ kind: 'starting' } | { kind: 'active'; task: string } | { kind: 'error'; detail: string } | null>(null);
  const [askRun, setAskRun] = useState(false);  // la run costa: si conferma prima
  const [readFailures, setReadFailures] = useState<Record<string, ReadFailure | null>>({});
  const setReadFailure = (source: string, failure: ReadFailure | null) =>
    setReadFailures(previous => ({ ...previous, [source]: failure }));

  const { hist: navHist, motivo: navHistMotivo } = useNavHistory();

  // Valore quota (TWR): la curva del patrimonio al netto dei versamenti.
  const [twrRaw, setTwr] = useState<TwrPayload | null>(null);
  const twr = useMemo(() => localizePayload(twrRaw), [twrRaw, t]);
  const [twrFailure, setTwrFailure] = useState<{ error: unknown } | null>(null);
  const twrErr = twrFailure ? motivoChiamata(twrFailure.error) : null;
  const [twrInCorso, setTwrInCorso] = useState(true);
  useEffect(() => {
    let m = true;
    Bellomberg.twr(false)
      .then(r => { if (m) { setTwr(r); setTwrFailure(null); } })
      .catch(error => { if (m) { setTwr(null); setTwrFailure({ error }); } })
      .finally(() => { if (m) setTwrInCorso(false); });
    return () => { m = false; };
  }, []);
  const quota = useMemo(() => leggiQuota(twr, twrErr, twrInCorso), [twr, twrErr, twrInCorso, t]);
  const curva: EsitoCurva = useMemo(() => leggiCurva(twr, 'quota', twrInCorso, twrErr), [twr, twrInCorso, twrErr, t]);

  const [spySerie, setSpySerie] = useState<BenchmarkPayload | null>(null);
  useEffect(() => {
    let m = true;
    Bellomberg.benchmark('SPY')
      .then(payload => { if (m) setSpySerie(payload?.error ? null : payload); })
      .catch(() => { if (m) setSpySerie(null); /* senza SPY l'interruttore resta spento, con il motivo */ });
    return () => { m = false; };
  }, []);

  // Preferenze di vista della pagina.
  const [periodo, setPeriodo] = usePreferenza<Periodo>('bb.dashboard.period', ['1G', '1S', '1M', '1A', 'Tutto'], '1M');
  const [spyAcceso, setSpyAcceso] = useState(false);
  const [metrica, setMetrica] = usePreferenza<Metrica>('bb.dashboard.change', ['oggi', 'totale'], 'oggi');
  const [vistaAlloc, setVistaAlloc] = usePreferenza<VistaAllocazione>('bb.dashboard.allocation', ['titoli', 'regioni', 'heatmap'], 'titoli');
  const [dettagliata, setDettagliata] = useState(false);
  const [titoloAperto, setTitoloAperto] = useState<string | null>(null);
  // Dati chiave del titolo aperto (/market/quote, cache 5 min nel motore): letti solo all'apertura.
  const [quoteAperta, setQuoteAperta] = useState<{ ticker: string; quote: MktQuote | null; errore: string | null } | null>(null);
  useEffect(() => {
    if (!titoloAperto) { setQuoteAperta(null); return; }
    let m = true;
    setQuoteAperta({ ticker: titoloAperto, quote: null, errore: null });
    Bellomberg.mktQuote(titoloAperto)
      .then(quote => { if (m) setQuoteAperta({ ticker: titoloAperto, quote, errore: null }); })
      .catch(error => { if (m) setQuoteAperta({ ticker: titoloAperto, quote: null, errore: motivoChiamata(error) || tr('dashboard.na') }); });
    return () => { m = false; };
  }, [titoloAperto]);

  // silent=true: rilettura quasi-live senza smontare la pagina
  const loadAll = async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const [p, d] = await Promise.all([
        Bellomberg.portfolio(),
        Bellomberg.decisions('PENDING', -1)
          .then(result => { setReadFailure('/decisions', null); return result; })
          .catch(error => { setReadFailure('/decisions', { error }); return { decisions: [] }; }),
      ]);
      setSnap(p);
      setReadFailure('/portfolio', p ? null : { error: null });
      setDecisions(d?.decisions || []);
    } catch (e: any) {
      setReadFailure('/portfolio', { error: e });
      console.error('[Dashboard.loadAll]', e);
    } finally {
      if (!silent) setLoading(false);
    }
  };

  const loadRisk = async (force = false) => {
    setRiskLoading(true);
    try {
      const r = await Bellomberg.portfolioRisk(force);
      setRisk(r);
      setReadFailure('/portfolio/risk', null);
    } catch (e: any) {
      setRisk(null);
      setReadFailure('/portfolio/risk', { error: e });
      console.error('[Dashboard.loadRisk]', e);
    } finally {
      setRiskLoading(false);
    }
  };

  const refreshPrices = async () => {
    setRefreshing(true);
    try {
      await Bellomberg.updatePrices();
      setReadFailure('/prices/update', null);
      await loadAll(true);
    } catch (error) {
      setReadFailure('/prices/update', { error });
    } finally {
      setRefreshing(false);
    }
  };

  const triggerRun = async () => {
    setRunNotice({ kind: 'starting' });
    try {
      const r = await Bellomberg.runConsigliere();
      setRunNotice({ kind: 'active', task: r.task_id });
    } catch (e: any) {
      const detail = dettaglioLeggibile(e);
      setRunNotice({ kind: 'error', detail });
      if (statusHttp(e) === 428) { conservaDettaglioRun(detail); navigate('/mandato'); }
    }
  };

  useEffect(() => { loadAll(); loadRisk(false); }, []);
  // Rilettura dei dati non coperti dal runner prezzi globale di Layout.
  useEffect(() => {
    const a = setInterval(() => { if (!document.hidden) loadAll(true); }, 30 * 1000);
    const b = setInterval(() => loadRisk(false), 5 * 60 * 1000);
    return () => { clearInterval(a); clearInterval(b); };
  }, []);
  useEffect(() => {
    const onSnapshot = (event: Event) => {
      const snapshot = (event as CustomEvent<PortfolioSnapshot>).detail;
      if (snapshot) setSnap(snapshot);
    };
    if (typeof window === 'undefined' || typeof window.addEventListener !== 'function') return;
    window.addEventListener('bb:portfolio-snapshot', onSnapshot);
    return () => window.removeEventListener('bb:portfolio-snapshot', onSnapshot);
  }, []);

  // Loghi delle società: una richiesta per i titoli nuovi, poi li tiene lo store.
  const tickerPosizioni = (Array.isArray(snap?.positions) ? snap!.positions : []).map(p => p.ticker).join(',');
  useEffect(() => { if (tickerPosizioni) caricaLoghi(tickerPosizioni.split(',')); }, [tickerPosizioni]);

  // Il titolo aperto si chiude cambiando pagina; «Apri in Mercati» porta il ticker con sé.
  const goMkt = (ticker: string) => {
    try { sessionStorage.setItem('bb:mktTicker', ticker); } catch { /* il ticker resta solo nell'URL di Mercati */ }
    setTitoloAperto(null);
    navigate('/market');
  };

  if (loading || !snap) {
    const failure = readFailures['/portfolio'];
    return (
      <div className="dashboard-modern dashboard-modern-loading" role={failure && !loading ? 'alert' : 'status'}>
        <div className="modern-loading-mark"><Cpu size={22} className={loading ? 'animate-pulse' : ''} /></div>
        <strong>{!loading && failure ? w.readFailure : w.loading}</strong>
        {!loading && failure && <>
          <p>/portfolio · {readFailureText(failure)}</p>
          <button type="button" className="bbn-btn is-primary" onClick={() => loadAll()}>{w.retry}</button>
        </>}
      </div>
    );
  }

  const positions = Array.isArray(snap.positions) ? snap.positions : [];
  const values = portfolioValues(snap);
  const navTotal = values.nav;
  // P&L di oggi: formula condivisa in lib/dailypl (n.d. dichiarato, finestra multi-seduta dichiarata).
  const { dayTot, dayPartial, dayTotPct, multiDay, windowLabel } = computeDailyPnl(positions, liveAsOf(snap));
  const oggiISO = new Date().toLocaleDateString('sv-SE');
  const na = tr('dashboard.na');
  const totRet = navHist && navHist.final_total_return_eur != null ? Number(navHist.final_total_return_eur) : null;
  const totRetPct = totRet != null && navHist?.final_total_return_pct != null ? Number(navHist.final_total_return_pct) : null;
  const snapshotDate = snap.timestamp && Number.isFinite(Date.parse(snap.timestamp))
    ? new Date(snap.timestamp).toLocaleString(localeDi(linguaCorrente())) : na;

  const dettagli = [
    { etichetta: w.unitValue, valore: quota.stato === 'viva' ? `${fmtNum(quota.valore, 2)} · ${w.twrSince} ${fmtPct((quota.valore / quota.base - 1) * 100)}` : na },
    { etichetta: w.invested, valore: fmtEUR(values.invested) },
    { etichetta: w.cash, valore: fmtEUR(values.cash) + (values.note ? ` · ${values.note}` : '') },
    { etichetta: 'ITD', valore: `${fmtEUR(finito(totRet) ? totRet : null, true)} · ${fmtPct(finito(totRetPct) ? totRetPct : null)}`
      + (navHistMotivo ? ` · ${navHistMotivo}` : navHist && !finito(totRet) ? ` · ${w.itdNotInResponse}` : '') },
    { etichetta: w.regime, valore: quota.stato !== 'viva' || !quota.regime ? na
      : quota.regime === 'official' ? w.regimeOfficial(quota.ufficialeDal ? new Date(quota.ufficialeDal + 'T00:00:00').toLocaleDateString(localeDi(linguaCorrente())) : null)
      : w.regimeReconstructed },
    { etichetta: w.updated, valore: snapshotDate },
    { etichetta: w.source, valore: snap.source || na },
    // ciò che il motore dichiara di sé (ledger vuoto, ricostruzione assente…): mai buttato
    ...(quota.stato === 'viva' && quota.note.length ? [{ etichetta: w.engineNotes, valore: quota.note.join(' · ') }] : []),
  ];

  const nStale = snap.stale_positions?.length || 0;
  const twrRec = twr?.reconciliation || null;
  const avvisi = [
    ...(nStale > 0 ? [{ testo: w.staleCount(nStale), title: (snap.stale_positions || []).join(', ') }] : []),
    ...((snap.fx_incomplete?.length || 0) > 0 ? [{ testo: w.fxMissing, title: snap.fx_incomplete!.join(', ') }] : []),
    ...(twrRec?.breach ? [{ testo: `Δ live ${finito(twrRec.delta_pct) ? fmtPct(twrRec.delta_pct) : na}`, title: tr('dashboard.official_close') }] : []),
  ];
  const errori = Object.entries(readFailures).filter(([source, failure]) => failure && source !== '/portfolio/risk');
  const posAperta = titoloAperto ? positions.find(p => p.ticker === titoloAperto) || null : null;
  const runText = runNotice?.kind === 'starting' ? w.runStarting
    : runNotice?.kind === 'active' ? w.runActive
    : runNotice?.kind === 'error' ? `${tr('dashboard.error_prefix')}${runNotice.detail}` : null;

  return (
    <div className="dashboard-modern bbn-dashboard">
      <header className="bbn-toolbar">
        <h1>{w.title}</h1>
        {/* P&L del giorno del portafoglio: stessa formula di lib/dailypl usata dalla pastiglia del grafico */}
        <span className="bbn-day" data-testid="dashboard-day-pnl"
          title={multiDay ? w.windowHint : dayPartial ? w.partialHint : undefined}>
          <span className="bbn-day-label">{multiDay && windowLabel ? w.dayPnlWindow(windowLabel) : w.dayPnl}</span>
          {finito(dayTot)
            ? <PastigliaVariazione valore={dayTot}>
                <span className="num">{fmtEUR(dayTot, true)}{dayPartial ? ' ±' : ''}</span>
                {finito(dayTotPct) && <span className="num">{fmtPct(dayTotPct)}</span>}
              </PastigliaVariazione>
            : <span className="bbn-day-na">{na}</span>}
        </span>
        {runText && <span className={'bbn-run' + (runNotice?.kind === 'error' ? ' is-error' : '')} role="status">
          {runNotice?.kind !== 'error' && <i className="bbn-run-pulse" aria-hidden="true" />}{runText}
          {runNotice?.kind === 'active' && <button type="button" className="bbn-link" onClick={() => navigate('/agents')}>{w.runFollow}</button>}
        </span>}
        <span className="bbn-grow" />
        <button type="button" className="bbn-btn" data-action="view-decisions" title={w.decisionsHint} onClick={() => navigate('/decisions')}>
          <ListChecks size={15} aria-hidden="true" />{w.decisions}
          {readFailures['/decisions'] ? null : <span className="bbn-count num">{decisions.length}</span>}
        </button>
        <button type="button" className="bbn-btn" data-action="refresh-prices" onClick={refreshPrices} disabled={refreshing}>
          <RefreshCw size={15} className={refreshing ? 'animate-spin' : undefined} aria-hidden="true" />{refreshing ? w.refreshing : w.refresh}
        </button>
        <button type="button" className="bbn-btn is-primary" data-action="run-adviser" onClick={() => setAskRun(true)} disabled={runNotice?.kind === 'starting'}>
          <Play size={15} aria-hidden="true" />{w.runCommittee}
        </button>
      </header>
      {errori.length > 0 && <div className="bbn-failures">{errori.map(([source, failure]) => (
        <span key={source} role="status" className="bbn-warn-pill">{w.readFailure} · {source} · {readFailureText(failure!)}</span>
      ))}</div>}

      <div className="bbn-grid">
        <HeroPatrimonio patrimonio={navTotal} curva={curva} spy={spySerie} periodo={periodo} onPeriodo={setPeriodo}
          spyAcceso={spyAcceso} onSpy={setSpyAcceso} dettagli={dettagli} avvisi={avvisi}
          giorno={{ eur: finito(dayTot) ? dayTot : null, pct: finito(dayTotPct) ? dayTotPct : null, multiDay, windowLabel, parziale: dayPartial }} />
        <CardAllocazione posizioni={positions} patrimonio={navTotal} liquidita={values.cash} motivoNd={values.note} vista={vistaAlloc} onVista={setVistaAlloc}
          metrica={metrica} onApri={setTitoloAperto} />
        <ListaPosizioni posizioni={positions} metrica={metrica} onMetrica={setMetrica}
          dettagliata={dettagliata} onDettagliata={setDettagliata} onApri={setTitoloAperto} />
        <CardNotizie posizioni={positions} onApri={setTitoloAperto} onTutte={() => navigate('/news')} />
        <div className="bbn-side">
          <CardRischio rischio={risk} caricamento={riskLoading} posizioni={positions} onRicalcola={() => loadRisk(true)}
            errore={readFailures['/portfolio/risk'] ? readFailureText(readFailures['/portfolio/risk']!) : null} />
          <CardEventi oggiISO={oggiISO} posizioni={positions} />
        </div>
      </div>

      <DettaglioTitolo posizione={posAperta} nome={posAperta ? nomeTitolo(posAperta) : ''} onChiudi={() => setTitoloAperto(null)} onApriMercati={goMkt}
        quote={quoteAperta && quoteAperta.ticker === posAperta?.ticker ? quoteAperta : null}
        testi={{ detailOf: w.detailOf, close: w.close, openMarkets: w.openMarkets, today: w.today, value: w.value,
          totalPl: w.totalPl, weight: w.weight, quantity: w.quantity, stalePrice: w.stalePrice, ...w.titolo }} />
      <RunConfirmDialog open={askRun} onConfirm={() => { setAskRun(false); triggerRun(); }} onCancel={() => setAskRun(false)} />
    </div>
  );
}
