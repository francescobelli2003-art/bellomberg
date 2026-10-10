import type { CSSProperties, ReactNode } from 'react';
import { t as tr } from '@/i18n/t';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
/* ============================================================
   F4 AGENTI IN DIRETTA — la run del consigliere (redesign «tavolo + corsie», 05/10/2026)

   Stesso impianto della Dashboard (palette --bbn-*):
     · box della run in alto: stato, tappe Avvio → R0 → R1 → R2 → Sintesi → Memo,
       riga Filing, Trade Idea, dettagli, avvio/stop;
     · il tavolo: il Capo al centro con l'anello dei report consegnati, i desk in
       cerchio con la frase di cosa fanno; un impulso per ogni chiamata nuova, un'onda
       per ogni report nuovo; sotto, le ultime conclusioni (prima frase dei report);
     · a destra Adesso, Sotto la lente (ticker) e La run in cifre;
     · le corsie: una riga per desk, una barra per finestra misurata di ogni round;
     · due cassetti: il report di un desk e i dettagli (costi, chiamate, strumenti,
       filing, recupero delle run).

   La logica della run non cambia: avvio con RunConfirmDialog, polling di
   /agents/live ogni 1,5 s, stop con cancel + reset + rilettura, heartbeat
   illeggibile tenuto distinto da «nessuna run» (N7), un solo verdetto per
   agente dove vince il peggiore, n.d. mai mostrato come zero.
   ============================================================ */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useT } from '@/i18n/provider';
import ModernPage from '@/components/ModernPage';
import { leggiDetail } from '@/lib/quota';
import { useNavigate } from 'react-router-dom';
import { Bellomberg, AgentInfo, AgentsLiveState, EnginesInfo, FilingActivateMissing, FilingOverview, ToolLogEntry, UsageBySpecialist, UsageTotal } from '@/lib/api';
import RunConfirmDialog from '@/components/RunConfirmDialog';
import WeeklyRecoveryPanel from '@/components/WeeklyRecoveryPanel';
import { useInterfaceTheme } from '@/components/InterfaceThemeProvider';
import { useBox } from '@/lib/useBox';
import { derivePlancia, engineShort, fmtDurShort, tickerDiInput, type Desk } from '@/lib/plancia-data';
import { conservaDettaglioRun, dettaglioLeggibile, statusHttp } from '@/lib/mandato';
import { controlloAttivaSaltato, testoControlloAttiva, type TradeIdeaActiveCheck } from '@/lib/tradeIdeaActiveCheck';
import { frase } from '@/lib/frase';
import { Segmenti } from '@/components/nuova/Card';
import { ArrowUpRight, Check, CircleAlert, FileText, Lightbulb, PanelRight, Play, Square, TriangleAlert, WifiOff } from 'lucide-react';
import IconaDesk, { coloreDesk, tintaDesk } from './chat/IconaDesk';
import { AnelloDesk, Barra, Kpi, Tappe, type StatoDesk, type StatoRound, type Tappa, type VistaCapo, type VistaDesk } from './agents/VistaAgenti';
import Tavolo, { type DatiTavolo } from './agents/Tavolo';
import Corsie, { type DatiCorsie } from './agents/Corsie';
import Cassetto from './agents/Cassetto';
import { parole } from './agents/parole';
import { leggiEsito, statoEffettivo } from './agents/esito';
import CoperturaFiling, { mancantiFiling, type ErroreFiling } from './agents/CoperturaFiling';
import { aggiornamentoInCorso, pollFiling } from '@/lib/filing-poll';
import './agents-nuova.css';

type SchedaPannello = 'costi' | 'chiamate' | 'strumenti' | 'filing' | 'recupero';
/** nome neutro della scheda per i test desktop (il markup inglese non porta parole italiane) */
const PANE: Record<SchedaPannello, string> = { costi: 'costs', chiamate: 'calls', strumenti: 'tools', filing: 'filing', recupero: 'recovery' };

/** La prima frase di un report (senza titoli e segni markdown): la «conclusione» del tavolo. */
function primaFrase(testo: string): string {
  const righe = testo.split('\n').map(r => r.trim()).filter(r => r && !/^#{1,6}\s/.test(r) && !/^[-=*_]{3,}$/.test(r));
  const piano = righe.join(' ').replace(/\*\*|__|`/g, '').replace(/^[-*•]\s+/, '').replace(/\s+/g, ' ').trim();
  const m = piano.match(/^.{20,220}?[.!?](?=\s|$)/);
  const f = m ? m[0] : piano.slice(0, 200);
  return f.length < piano.length && !m ? f.replace(/\s+\S*$/, '') + '…' : f;
}
/** iniziale maiuscola per le voci di catalogo scritte in minuscolo (frase() tocca solo il MAIUSCOLO) */
const maiuscola = (s: string) => s ? s.charAt(0).toLocaleUpperCase() + s.slice(1) : s;

const ACTIVE_RUN_KEY = 'bellomberg_active_run';
const POLL_INTERVAL_MS = 1500;

/* ── formattatori: convenzione della lingua corrente in tutta la pagina ─
   null/NaN => "n.d.": il dato NON e' calcolabile e va dichiarato. Uno
   "0,00 EUR" al posto di un buco e' il bug peggiore su un pannello costi
   (regola no-fallback-silenziosi): qui non deve MAI comparire. */
const fmtEur = (v: number | null | undefined) =>
  v == null || !isFinite(v) ? tr('activity.unavailable')
    : new Intl.NumberFormat(localeDi(linguaCorrente()), { style: 'currency', currency: 'EUR',
        minimumFractionDigits: 2, maximumFractionDigits: 2, useGrouping: true }).format(v);
const fmtN = (v: number | null | undefined, d = 0) =>
  v == null || !isFinite(v) ? tr('activity.unavailable')
    : new Intl.NumberFormat(localeDi(linguaCorrente()), { minimumFractionDigits: d, maximumFractionDigits: d, useGrouping: true }).format(v);
const fmtTok = (v: number | null | undefined) =>
  v == null ? tr('activity.unavailable') : v >= 1000 ? fmtN(v / 1000, 1) + 'k' : fmtN(v);

/* status !== 'ok' = BUCO, esattamente come cost_eur null. status assente =
   heartbeat di una run vecchia -> nessun giudizio, in nessuno dei due sensi. */
const isHoleStatus = (s?: string | null) => s != null && s !== 'ok';
/* "e' andato KO" != "non so quanto e' costato": api_error/usage_unknown sono
   fallimenti dell'agente; model_unknown/pricing_unavailable sono solo costi
   non calcolabili su una chiamata riuscita. Il marchio KO va solo ai primi. */
const isErrorStatus = (s?: string | null) => s === 'api_error' || s === 'usage_unknown';

function statusDescriptions(): Record<string, string> { return {
  api_error: tr('activity.apiFailed'),
  usage_unknown: tr('activity.usageMissing'),
  model_unknown: tr('activity.modelUnpriced'),
  pricing_unavailable: tr('activity.pricingMissing'),
}; }

/* ── UN SOLO VERDETTO PER AGENTE, e vince il peggiore ────────────────────
   Prima la stessa card mostrava un badge verde "ok" (da specialist_status,
   che dice solo "ha consegnato il report") e un costo ambra (da
   usage.status = api_error, che dice "ha sbattuto contro l'API"). Sono due
   domande diverse, ma a schermo deve arrivare una risposta sola. */
type Verdict = { k: string; cls: string; t: string };
type StopNotice = { issues: { source: string; detail: string | null }[]; running: boolean | null };
const phaseText = (key: string) => key === 'SINTESI' ? tr('activity.synthesis')
  : key === 'FRA ROUND' ? tr('activity.betweenRounds') : key;
/** verdetto di un desk con un errore solo nella STORIA (heartbeat senza status_finale) */
const TENTATIVI_KO = 'TENTATIVI_KO';
function verdictOf(d: Pick<Desk, 'statusRun' | 'statusUsage' | 'statusStoria' | 'cost'>): Verdict {
  const STATUS_DESC = statusDescriptions();
  /* review PR #19 (06/10): senza status_finale `status` e' il PEGGIORE dei tentativi, non
     l'esito. Un errore li' dice «almeno un tentativo in errore»: non e' un KO del desk, e
     l'esito finale non e' dichiarato. Il desk resta nel suo stato di run (consegnato, ecc.) */
  if (d.statusStoria && isErrorStatus(d.statusUsage))
    return { k: TENTATIVI_KO, cls: 'amc',
      t: tr('activity.attemptErrorNoFinal', { a: STATUS_DESC[d.statusUsage!] || tr('activity.declaredError') }) };
  if (isErrorStatus(d.statusUsage))
    /* «ha consegnato il report» solo se e' done: un desk KO in un round prima
       puo' essere in corsa ORA (review F45), e allora si dicono i due fatti */
    return { k: 'KO', cls: 'ko',
      t: `${STATUS_DESC[d.statusUsage!] || tr('activity.declaredError')}${
        d.statusRun === 'running' ? tr('activity.earlierRound', {a: fmtEur(d.cost)})
        : d.statusRun === 'done' ? tr('activity.deliveredDespite', {a: fmtEur(d.cost)})
        : tr('activity.spent', {a: fmtEur(d.cost)})}` };
  if (isHoleStatus(d.statusUsage))
    return { k: 'n.d.', cls: 'amc',
      t: STATUS_DESC[d.statusUsage!] || tr('activity.backendAnomaly', {a: d.statusUsage!}) };
  if (d.statusRun === 'done') return { k: 'OK', cls: 'okc', t: tr('activity.reportDelivered') };
  if (d.statusRun === 'running') return { k: 'RUN', cls: 'amc', t: tr('activity.executing') };
  if (d.statusRun === 'error') return { k: 'KO', cls: 'ko', t: tr('activity.specialistError') };
  return { k: '—', cls: 'nd', t: tr('activity.statusNotDeclared') };
}

/* buchi da dichiarare sul totale. NB: unpriced_agents del backend mescola DUE
   casi (base.py: cost_eur is None OPPURE partial) — chi non ha alcun costo e
   chi ne ha uno incompleto. Vanno nominati separatamente, o la nota
   contraddice la cifra che le sta accanto. */
function costNotes(u?: UsageTotal | null, by?: Record<string, UsageBySpecialist> | null): string[] {
  if (!u) return [];
  const n: string[] = [];
  if (u.error) n.push(tr('activity.costAggregationError', { a: u.error }));
  const named = u.unpriced_agents || [];
  const noCost = named.filter(a => by?.[a] && by[a].cost_eur == null);
  const onlyPartial = named.filter(a => by?.[a] && by[a].cost_eur != null);
  const unknownKind = named.filter(a => !by?.[a]);
  if (noCost.length) n.push(tr('activity.costCannotCalculate', { a: noCost.join(', ') }));
  if (onlyPartial.length) n.push(tr('activity.partialCost', { a: onlyPartial.join(', ') }));
  if (unknownKind.length) n.push(tr('activity.missingOrPartialCost', { a: unknownKind.join(', ') }));
  if (!named.length && u.partial === true) n.push(tr('activity.partialTotal'));
  if (u.fx_source === 'fallback') n.push(tr('activity.fxFallback'));
  if (u.fx_source === 'n.d.') n.push(tr('activity.fxMissing'));
  return n;
}
const isPartial = (u?: UsageTotal | null) =>
  u?.partial != null ? u.partial === true : !!u?.unpriced_agents?.length;

/* ══════════════════════════════════════════════════════════════════════ */
export default function AgentsLive() {
  const tr = useT();
  const navigate = useNavigate();
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [engines, setEngines] = useState<EnginesInfo | undefined>(undefined);
  const [rosterErr, setRosterErr] = useState<string | null>(null);
  const [state, setState] = useState<AgentsLiveState | null>(null);
  const [liveErr, setLiveErr] = useState<string | null>(null);
  /* N7 (blocco D): l'heartbeat letto male in QUESTO istante non e' «nessuna
     run» — il payload {running:false, heartbeat:"illeggibile"} non deve
     entrare in setState (cancellerebbe plancia e activeTaskId per un giro):
     resta l'ultimo stato buono e il buco si dichiara in vetrina CON LA SUA
     DURATA (da = primo poll illeggibile consecutivo, al = l'ultimo): senza,
     un buco di 15 minuti si leggeva come un glitch momentaneo (review 31/08). */
  const [hbIll, setHbIll] = useState<{ msg: string | null; da: number; al: number } | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const [cursor, setCursor] = useState<number | null>(null);
  const [pinned, setPinned] = useState(false);
  const [dialRef, dialBox] = useBox<HTMLDivElement>();

  useEffect(() => {
    Bellomberg.agentsList()
      .then(r => { setAgents(r?.agents || []); setEngines(r?.engines); setRosterErr(null); })
      .catch(e => setRosterErr(leggiDetail(e?.response?.data?.detail || e?.message || String(e))));
  }, []);

  useEffect(() => {
    let mounted = true, inFlight = false;
    const tick = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const s = await Bellomberg.agentsLive();
        if (mounted && s) {
          /* chiave nuova dal 27/08 (ponte (90)); il fallback sul testo copre il
             backend vecchio, che comincia il message con «read error» */
          const illeggibile = s.heartbeat === 'illeggibile'
            || (s.running === false && typeof s.message === 'string' && s.message.startsWith('read error'));
          if (illeggibile) {
            const msg = s.message || null;
            setHbIll(prev => ({ msg, da: prev?.da ?? Date.now(), al: Date.now() }));
          }
          else { setState(s); setHbIll(null); }
          setLiveErr(null);
        }
      }
      /* l'errore di RETE azzera hbIll: «l'ultimo poll ha risposto …» sarebbe
         falso sotto un poll che non ha risposto affatto (review 31/08); se al
         ritorno della rete il file e' ancora illeggibile, hbIll torna da solo */
      catch (e: any) { if (mounted) { setLiveErr(leggiDetail(e?.response?.data?.detail || e?.message || String(e))); setHbIll(null); } }
      finally { inFlight = false; }
    };
    tick();
    const i = setInterval(tick, POLL_INTERVAL_MS);
    return () => { mounted = false; clearInterval(i); };
  }, []);

  /* esito vero della run (06/10): se il backend smentisce `running` (processo morto o
     fermato) la pagina disegna la run come chiusa; nessun hook nuovo qui (i test SSR
     seminano gli stati per indice) */
  const esito = leggiEsito(state);
  const statoV = statoEffettivo(state, esito);
  const isRunning = statoV?.running === true;
  /* in avvio la run e' attiva anche se l'heartbeat e' ancora della precedente */
  const runAttiva = isRunning || esito?.attiva === true;

  /* review PR #19 (06/10): «Stato non verificabile» (heartbeat running senza pid e nessun
     processo noto) non e' una run che lavora: l'orologio si ferma e la durata e' l'ultima
     nota, cioe' quella all'ultimo heartbeat (updated_at). Senza updated_at resta n.d. */
  const nonConfermata = esito?.nonConfermata === true;
  const ultimoHbMs = state?.updated_at ? new Date(state.updated_at).getTime() : NaN;
  const orologio = isRunning && !nonConfermata;
  /* review PR #31 (Opus 5.5): «fermo da X» (stale_warning o heartbeat illeggibile) si misura su
     `now` anche su una run non confermata: con `now` spento la cifra restava quella dell'apertura
     della pagina. La plancia resta comunque all'ultimo heartbeat (nowPlancia) */
  const tickNow = isRunning && (orologio || statoV?.stale_warning === true || hbIll != null);

  /* l'orologio serve SOLO a una run viva: la sua durata non e' nel payload */
  useEffect(() => {
    if (!tickNow) return;
    const i = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(i);
  }, [tickNow]);

  useEffect(() => {
    if (!state?.start_time || !orologio) { setElapsed(0); return; }
    const start = new Date(state.start_time).getTime();
    if (!isFinite(start)) return;
    const i = setInterval(() => setElapsed(Math.floor((Date.now() - start) / 1000)), 1000);
    return () => clearInterval(i);
  }, [state?.start_time, orologio]);

  /* run non confermata: la plancia si legge all'istante dell'ultimo heartbeat, non a «adesso» */
  const nowPlancia = nonConfermata && isFinite(ultimoHbMs) ? ultimoHbMs : now;
  const P0 = useMemo(() => derivePlancia(statoV, agents, engines, nowPlancia), [statoV, agents, engines, nowPlancia, tr]);
  /* Il tema non cambia i dati: i colori dei desk li normalizza IconaDesk
     (coloreDesk), uguali in Chiaro e in Scuro. Il memo resta per non spostare
     l'ordine degli hook. */
  const dark = useInterfaceTheme().effective === 'dark';
  const P = useMemo(() => P0, [P0, dark]);

  /* Il cursore: su una run VIVA insegue il presente; su una run chiusa parte
     dal minuto di massima attivita', che e' l'istante piu' informativo da
     mostrare per primo (e non un default arbitrario). */
  useEffect(() => {
    if (pinned || !P.ok) return;
    if (P.live) { setCursor(P.runSec); return; }
    if (cursor == null) {
      let best = 0, bestN = -1;
      for (let s = 0; s <= P.runSec; s += 30) {
        const n = P.windows.filter(w => s >= w.t0 && s <= w.t1).length;
        if (n > bestN) { bestN = n; best = s; }
      }
      setCursor(best);
    }
  }, [P.ok, P.live, P.runSec, P.windows, pinned, cursor]);

  /* ── comando della run: identico alla versione precedente ───────────── */
  const [activeTaskId, setActiveTaskId] = useState<string | null>(() => {
    try { return localStorage.getItem(ACTIVE_RUN_KEY); } catch { return null; }
  });
  const [stopping, setStopping] = useState(false);
  const [triggerMsg, setTriggerMsg] = useState<{ kind: 'starting' | 'active'; id?: string | null; check?: TradeIdeaActiveCheck | null } | null>(null);
  const [askRun, setAskRun] = useState(false);   // Lotto D: la run costa, si conferma prima
  const [stopNotice, setStopNotice] = useState<StopNotice | null>(null);

  const triggerRun = async () => {
    setTriggerMsg({ kind: 'starting' }); setStopNotice(null);
    setState(prev => ({ ...(prev || {}), running: true,
      start_time: new Date().toISOString(), message: tr('activity.startShort') } as AgentsLiveState));
    try {
      const r = await Bellomberg.runConsigliere();
      const tid = r?.task_id || null;
      if (tid) { try { localStorage.setItem(ACTIVE_RUN_KEY, tid); } catch {} setActiveTaskId(tid); }
      // R14 seguito (B4): controllo «Trade Idea attiva» non eseguito dal backend = dichiarato, la run parte comunque
      setTriggerMsg({ kind: 'active', id: tid, check: controlloAttivaSaltato(r) });
    } catch (e: any) {
      const detail = dettaglioLeggibile(e);
      setState(prev => prev ? { ...prev, running: false } : prev);
      setTriggerMsg(null);
      alert(tr('activity.startFailed', { a: detail }));
      if (statusHttp(e) === 428) { conservaDettaglioRun(detail); navigate('/mandato'); }
    }
  };

  const stopRun = async () => {
    if (!confirm(tr('activity.stopQuestion'))) return;
    setStopping(true);
    setStopNotice(null);
    const issues: StopNotice['issues'] = [];
    let tid = activeTaskId;
    if (!tid) {
      try { const a = await Bellomberg.consigliereActive(); if (a.active && a.task_id) tid = a.task_id; }
      catch (e: any) { issues.push({ source: '/consigliere/active', detail: leggiDetail(e?.response?.data?.detail || e?.message || String(e)) }); }
    }
    let terminal = false;
    try {
      if (tid) {
        const r = await Bellomberg.cancelConsigliere(tid);
        terminal = ['cancelled', 'completed', 'failed'].includes(r?.status);
        if (!terminal) issues.push({ source: '/consigliere/cancel/' + tid, detail: leggiDetail(r) || null });
      } else {
        const r = await Bellomberg.cancelAllConsigliere();
        terminal = r?.n_killed > 0 && Array.isArray(r.errors) && r.errors.length === 0;
        if (!terminal) issues.push({ source: '/consigliere/cancel_all', detail: leggiDetail(r?.errors?.length ? r.errors : r) || null });
      }
    } catch (e: any) {
      issues.push({ source: tid ? '/consigliere/cancel/' + tid : '/consigliere/cancel_all', detail: leggiDetail(e?.response?.data?.detail || e?.message || String(e)) });
    }
    // A failed cancellation must not erase the heartbeat or the last observed log.
    if (terminal && issues.length === 0) {
      try {
        const reset = await Bellomberg.resetAgentsLive();
        if (reset?.ok !== true) issues.push({ source: '/agents/live/reset', detail: leggiDetail(reset?.error || reset) || null });
      } catch (e: any) { issues.push({ source: '/agents/live/reset', detail: leggiDetail(e?.response?.data?.detail || e?.message || String(e)) }); }
    }
    let observedRunning: boolean | null = null;
    try {
      const observed = await Bellomberg.agentsLive();
      const unreadable = observed?.heartbeat === 'illeggibile'
        || (observed?.running === false && typeof observed.message === 'string' && observed.message.startsWith('read error'));
      if (!observed || unreadable || typeof observed.running !== 'boolean') {
        issues.push({ source: '/agents/live', detail: leggiDetail(observed?.message || observed) || null });
      } else {
        setState(observed); setHbIll(null); observedRunning = observed.running;
        if (!observed.running && issues.length === 0) {
          try { localStorage.removeItem(ACTIVE_RUN_KEY); } catch {}
          setActiveTaskId(null); setTriggerMsg(null); setElapsed(0);
        }
      }
    } catch (e: any) { issues.push({ source: '/agents/live', detail: leggiDetail(e?.response?.data?.detail || e?.message || String(e)) }); }
    setStopNotice({ issues, running: observedRunning });
    setStopping(false);
  };

  useEffect(() => {
    if (statoV && statoV.running === false && !runAttiva && activeTaskId && !stopNotice?.issues.length) {
      try { localStorage.removeItem(ACTIVE_RUN_KEY); } catch {}
      setActiveTaskId(null);
    }
  }, [statoV?.running, runAttiva, activeTaskId, stopNotice]);


  /* ── presentazione (redesign 02/10/2026): quello che c'era sul quadrante sta
     ora nelle tappe, nelle card dei desk e nel pannello a schede. Hook nuovi in
     coda: i test i18n impostano lo stato per indice di useState. */
  const [scheda, setScheda] = useState<SchedaPannello>('costi');
  const [finitaQui, setFinitaQui] = useState(false);
  const eraViva = useRef<boolean | null>(null);
  useEffect(() => {
    if (statoV?.running == null) return;
    /* «completata» solo se la fine e' avvenuta sotto gli occhi del PM; aprire la
       pagina su una run gia' chiusa la mostra a riposo, con l'ultima run */
    if (eraViva.current === true && statoV.running === false) setFinitaQui(true);
    if (statoV.running === true) setFinitaQui(false);
    eraViva.current = statoV.running;
  }, [statoV?.running]);

  /* ── copertura filing (fase D, 03/10/2026): letta all'apertura e a fine run, quando
     cambiano le novita'; l'attivazione in blocco rilegge subito e dopo 15 s, il tempo
     dei primi confronti. Stato in coda: i test SSR seminano per indice. */
  const [filing, setFiling] = useState<FilingOverview | null>(null);
  const [filingErr, setFilingErr] = useState<ErroreFiling | null>(null);
  const [filingBusy, setFilingBusy] = useState(false);
  const [filingEsito, setFilingEsito] = useState<FilingActivateMissing | null>(null);
  /* redesign 05/10/2026: cassetti del report di un desk e dei dettagli (stato in coda) */
  const [repDesk, setRepDesk] = useState<{ id: string; r: number | null; aperto: boolean } | null>(null);
  const [dettagliAperti, setDettagliAperti] = useState(false);
  const montata = useRef(true);
  useEffect(() => { montata.current = true; return () => { montata.current = false; }; }, []);
  const caricaFiling = async () => {
    try { const r = await Bellomberg.filingOverview(); if (montata.current) { setFiling(r); setFilingErr(null); } }
    catch (e: any) { if (montata.current) setFilingErr({ stato: statusHttp(e), msg: dettaglioLeggibile(e) }); }
  };
  useEffect(() => { caricaFiling(); }, [isRunning]);
  /* aggiornamento in corso (attivazione, controllo orario): rilettura ogni 10 s finche' dura */
  const filingCorre = aggiornamentoInCorso(filing);
  useEffect(() => pollFiling(filingCorre, () => { if (montata.current) caricaFiling(); }), [filingCorre]);
  const attivaFiling = async () => {
    setFilingBusy(true); setFilingEsito(null);
    try {
      const r = await Bellomberg.filingActivateMissing();
      if (!montata.current) return;
      setFilingEsito(r);
      await caricaFiling();
      if (r.attivati.length) setTimeout(() => { if (montata.current) caricaFiling(); }, 15000);
    } catch (e: any) { if (montata.current) setFilingErr({ stato: statusHttp(e), msg: dettaglioLeggibile(e) }); }
    finally { if (montata.current) setFilingBusy(false); }
  };

  /* ── costi e buchi ──────────────────────────────────────────────────── */
  const total = state?.usage_total;
  const notes = costNotes(total, state?.usage_by_specialist);
  const hasTotal = total?.cost_eur != null;
  const partial = hasTotal && isPartial(total);
  /* review PR #19 (06/10): error_agents cambia significato col marcatore. Con
     «esito_finale_v2» sono i KO FINALI; senza (heartbeat vecchio) vuol dire «almeno un
     tentativo KO», e un desk ritentato e poi riuscito ci sta dentro. Li' il KO finale si
     prende da status_finale del desk quando c'e'; chi non ce l'ha resta «almeno un
     tentativo in errore, esito finale non dichiarato» — mai contato come errore finale. */
  const dichiaratiKo = total?.error_agents || [];
  const semanticaFinale = total?.error_agents_semantica === 'esito_finale_v2';
  const finaleDi = (id: string) => state?.usage_by_specialist?.[id]?.status_finale;
  const koIds = semanticaFinale ? dichiaratiKo : dichiaratiKo.filter(id => isErrorStatus(finaleDi(id)));
  const tentativiKo = semanticaFinale ? [] : dichiaratiKo.filter(id => !koIds.includes(id));
  const tentativiPoiOk = tentativiKo.filter(id => finaleDi(id) != null);
  const tentativiSenzaFinale = tentativiKo.filter(id => finaleDi(id) == null);
  // costo non dichiarato = n.d., mai 0 nella somma (08b S13, revisione G9b)
  const costoNoto = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
  const koCostiNoti = koIds.map(id => state?.usage_by_specialist?.[id]?.cost_eur);
  const koCost = koCostiNoti.every(costoNoto) ? koCostiNoti.reduce((s, v) => s + (v as number), 0) : null;
  const costiAgenti = Object.values(state?.usage_by_specialist || {}).map(u => u.cost_eur);
  const agentiSenzaCosto = costiAgenti.filter(v => !costoNoto(v)).length;
  const sumAgents = costiAgenti.filter(costoNoto).reduce((s, v) => s + v, 0);
  const koVisibile = koCost == null ? koIds.length > 0 : koCost > 0;
  const tutti = [...P.desks, ...P.pending, ...P.stages];
  const lavoro = tutti.reduce((s, d) => s + (d.dur || 0), 0);
  /* il numero esiste SOLO quando e' calcolabile (N2): a run viva la somma delle
     durate e' un minimo che cala con l'orologio, non un parallelismo */
  const par = P.parStato === 'calcolato' && P.runSec > 0 && lavoro > 0 ? lavoro / P.runSec : null;

  /* heartbeat fermo (>600 s, stale_warning del backend) o illeggibile oltre la
     stessa soglia: «ragiona da X» sarebbe un'inferenza da uno stato che nessuno
     conferma piu' — si scrive il fatto: dichiarato, e fermo da quanto. */
  const hbIllDur = hbIll ? Math.max(0, (hbIll.al - hbIll.da) / 1000) : null;
  const hbIllLungo = hbIllDur != null && hbIllDur > 600;
  const fermoDa = isRunning && (P.stale || hbIllLungo) && state?.updated_at
    ? Math.max(0, (now - new Date(state.updated_at).getTime()) / 1000) : null;
  const hhmm = (iso?: string) => iso ? new Date(iso).toLocaleTimeString(localeDi(linguaCorrente()),
    { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : tr('activity.unavailable');
  const ora = (iso?: string) => iso ? new Date(iso).toLocaleTimeString(localeDi(linguaCorrente()),
    { hour: '2-digit', minute: '2-digit' }) : tr('activity.unavailable');
  const giorno = (iso?: string) => iso ? new Date(iso).toLocaleDateString(localeDi(linguaCorrente()),
    { day: 'numeric', month: 'short' }) : tr('activity.unavailable');

  /* ── i desk del comitato: in ordine di roster, cosi' il tavolo non ruota ── */
  const isStage = (id: string) => id === 'capo' || id.startsWith('_');
  const byId = (id: string) => tutti.find(x => x.id === id);
  const ordine = new Map(agents.map((a, i) => [a.id, i]));
  const deskDellaRun = [...P.desks, ...P.pending].filter(d => !isStage(d.id));
  const deskElenco: Desk[] = (deskDellaRun.length ? deskDellaRun : agents.filter(a => !isStage(a.id)).map(a => ({
    id: a.id, name: a.name, role: a.role, color: a.color, onGrid: true, dur: null, apiCalls: 0, cost: null,
    tin: null, tout: null, cacheR: null, cacheW: null, nCalls: 0, nTools: 0, rounds: [], obs0: null, obs1: null,
  }))).slice().sort((a, b) => (ordine.get(a.id) ?? 999) - (ordine.get(b.id) ?? 999) || a.name.localeCompare(b.name));
  const rosterDi = (id: string) => agents.find(a => a.id === id);
  const nomeDi = (d: Pick<Desk, 'id' | 'name'>) => frase(rosterDi(d.id)?.name || d.name);
  const coloreDi = (id: string) => rosterDi(id)?.color || byId(id)?.color || null;
  const nDesk = deskElenco.length;
  const r2 = state?.r2_specialists;
  const pulisci = (input: string) => input === '{}' ? '' : input.replace(/[{}']/g, '').trim();

  /* ── redesign 05/10/2026: il tavolo e le corsie ─────────────────────────
     A run viva l'heartbeat porta solo le ULTIME 50 chiamate (tool_log_tappato): per le
     corsie si tengono quelle viste da quando la pagina e' aperta, dichiarandolo. Le
     cifre e i conteggi della pagina restano quelli del payload (P), non questa somma. */
  const visteRef = useRef<{ start: string | null; mappa: Map<string, ToolLogEntry> }>({ start: null, mappa: new Map() });
  const logVisto = useMemo(() => {
    const v = visteRef.current, start = state?.start_time ?? null;
    if (v.start !== start) { v.start = start; v.mappa = new Map(); }
    for (const e of state?.tool_log || []) v.mappa.set(`${e.specialist}|${e.round}|${e.time}|${e.tool}|${e.input}`, e);
    return [...v.mappa.values()];
  }, [state]);
  const PC = useMemo(() => statoV && P.logTappato && logVisto.length > (statoV.tool_log?.length || 0)
    ? derivePlancia({ ...statoV, tool_log: logVisto }, agents, engines, nowPlancia) : P, [P, logVisto, statoV, agents, engines, nowPlancia]);

  /* i report consegnati (reports_by_specialist: i primi 500 caratteri di ogni round).
     L'ora si scrive solo se l'arrivo e' stato VISTO qui; quelli gia' presenti
     all'apertura si ordinano sulla fine della finestra del desk, senza ora. */
  const arriviRef = useRef<{ start: string | null; prima: boolean; mappa: Map<string, { ms: number; visto: boolean }> }>({ start: null, prima: true, mappa: new Map() });
  const consegne = useMemo(() => {
    const a = arriviRef.current, start = state?.start_time ?? null;
    if (a.start !== start) { a.start = start; a.prima = true; a.mappa = new Map(); }
    const startMs = start ? new Date(start).getTime() : NaN;
    const out: { id: string; r: number; testo: string; key: string; ms: number; visto: boolean }[] = [];
    for (const [id, giri] of Object.entries(state?.reports_by_specialist || {})) {
      if (isStage(id)) continue;
      for (const [rk, testo] of Object.entries(giri || {})) {
        const r = Number.parseInt(String(rk).replace(/\D/g, ''), 10);
        if (!Number.isFinite(r) || typeof testo !== 'string') continue;
        const key = `${id}#${r}`;
        if (!a.mappa.has(key)) {
          const w = PC.windows.find(x => x.a === id && x.r === r);
          a.mappa.set(key, a.prima ? { ms: (isFinite(startMs) ? startMs : 0) + (w ? w.t1 : r * 1e6) * 1000, visto: false } : { ms: Date.now(), visto: true });
        }
        const arr = a.mappa.get(key)!;
        out.push({ id, r, testo, key, ms: arr.ms, visto: arr.visto });
      }
    }
    if (state) a.prima = false;
    return out.sort((x, y) => x.ms - y.ms || x.r - y.r);
  }, [state, PC.windows]);

  const apriReport = (id: string, r?: number) => { setDettagliAperti(false); setRepDesk({ id, r: r ?? null, aperto: true }); };
  const chiudiReport = () => setRepDesk(prev => prev ? { ...prev, aperto: false } : prev);
  const apriDettagli = (s?: SchedaPannello) => { if (s) setScheda(s); setRepDesk(prev => prev ? { ...prev, aperto: false } : prev); setDettagliAperti(true); };
  const chiudiDettagli = () => setDettagliAperti(false);

  return (
    <>
    <ModernPage page="agents" render={() => {
      const w = parole();
      const vivo = P.live;
      /* «ha consegnato il memo» solo se il backend lo afferma (esito_run.memo_consegnato) */
      const memoOk = esito?.memo === true;
      const statoEsito = esito?.stato ?? 'sconosciuta';
      const lavagnaAssente = state?.lavagna === 'assente';
      const nonInR2 = (id: string) => !!r2 && !r2.includes(id) && (!vivo || (P.round ?? 0) >= 2);
      /* desk e Capo «fermi»: heartbeat fermo (fermoDa) oppure run che nessun processo conferma */
      const fermo = fermoDa != null || nonConfermata;
      const fermoTappa = fermoDa != null ? w.stepStale(fmtDurShort(fermoDa)) : w.stepUnconfirmed;

      const desks: VistaDesk[] = deskElenco.map(d => {
        const v = verdictOf(d);
        const corre = vivo && (d.statusRun === 'running' || P.running.includes(d.id));
        const mie = P.calls.filter(c => c.a === d.id);
        const ultima = mie[mie.length - 1];
        const fa = ultima ? Math.max(0, P.runSec - ultima.t) : null;
        const stato: StatoDesk = v.cls === 'ko' ? 'ko'
          : v.k === 'n.d.' ? 'nd'
          : corre ? (fermo ? 'stale' : ultima && (P.round == null || ultima.r === P.round) && fa != null && fa <= 20 ? 'run' : 'think')
          : d.statusRun === 'done' ? 'ok'
          : 'wait';
        const giri = [d.rounds.length ? w.rounds(d.rounds.length) : null, nonInR2(d.id) ? w.notInR2 : null,
          ultima ? w.at(ultima.hhmm.slice(0, 5)) : null].filter(Boolean).join(' · ');
        /* lo strumento detto in parole; se non ha una frase resta il suo nome tecnico */
        const tkUltima = ultima ? tickerDiInput(ultima.input)[0] ?? null : null;
        const inParole = ultima ? w.faccio(ultima.tool, tkUltima) : null;
        const fare: VistaDesk['fare'] =
          stato === 'run' && ultima ? (inParole
              ? { icona: 'tool', codice: false, testo: inParole, tk: tkUltima, sotto: w.doingAgo(pulisci(ultima.input), fmtDurShort(fa)) }
              : { icona: 'tool', codice: true, testo: ultima.tool, sotto: w.doingAgo(pulisci(ultima.input), fmtDurShort(fa)) })
          : stato === 'think' ? (ultima && (P.round == null || ultima.r === P.round)
              ? { icona: 'think', codice: false, testo: w.thinkingSince(fmtDurShort(fa)), sotto: w.lastTool(ultima.tool) + (pulisci(ultima.input) ? ' · ' + pulisci(ultima.input) : '') }
              : { icona: 'think', codice: false, testo: w.noCallYet, sotto: w.noCallYetSub })
          : stato === 'stale' ? { icona: 'pause', codice: false, testo: fermoDa != null ? w.staleDoing(fmtDurShort(fermoDa)) : w.unconfirmedDoing, sotto: ultima ? w.lastTool(ultima.tool) : '' }
          : stato === 'ko' ? { icona: 'ko', codice: false, testo: w.koTitle, sotto: v.t }
          : stato === 'nd' ? { icona: 'file', codice: false, testo: d.statusRun === 'done' ? w.delivered : frase(v.k), sotto: v.t }
          : stato === 'ok' ? { icona: 'file', codice: false,
              testo: vivo && P.round != null && !nonInR2(d.id) ? w.deliveredRound(P.round) : w.delivered, sotto: v.k === TENTATIVI_KO ? v.t : giri }
          : { icona: 'wait', codice: false, testo: vivo ? w.waitingLive : w.waitingIdle, sotto: state ? (vivo ? '' : v.t) : '' };
        const piuAlto = d.rounds.length ? Math.max(...d.rounds) : -1;
        const round = [0, 1, 2].map((r): StatoRound => {
          if (r === 2 && nonInR2(d.id)) return 'off';
          if (stato === 'ko' && r === piuAlto) return 'ko';
          if (corre && r === P.round) return 'on';
          if (d.rounds.includes(r) || (vivo && P.round != null && r < P.round) || (!vivo && d.statusRun === 'done' && r <= piuAlto)) return 'ok';
          return '';
        });
        return {
          id: d.id, nome: nomeDi(d), ruolo: rosterDi(d.id)?.role || d.role, colore: coloreDi(d.id) || d.color, stato,
          pastiglia: { run: w.stWorking, think: w.stThinking, ok: w.stDone, ko: w.stKo, nd: w.stNd, wait: w.stWaiting,
            stale: fermoDa != null ? w.stStale : w.stUnconfirmed }[stato],
          titolo: v.t, fare, round, esito: v.k,
          dur: fmtDurShort(d.dur), chiamate: w.calls(d.nCalls),
          costo: d.cost == null ? tr('activity.unavailable') : (d.partial ? '~' : '') + fmtEur(d.cost), costoNd: d.cost == null,
          dettaglio: w.deskDetail(d.nTools, d.apiCalls, engineShort(d.id, engines, d.rounds)),
        };
      });
      const consegnati = deskElenco.filter(d => d.statusRun === 'done').length;

      /* ── il Capo ───────────────────────────────────────────────────── */
      const capoD = byId('capo');
      const capoV = capoD ? verdictOf(capoD) : null;
      const memoId = state?.memo_id ?? null;
      const chiusaConRun = !vivo && !!state?.start_time;
      const capoStato: StatoDesk = capoV?.cls === 'ko' ? 'ko'
        : vivo && P.capo === 'running' ? (fermo ? 'stale' : 'run')
        : chiusaConRun && memoOk ? 'ok'
        : 'wait';
      const vaiDecisioni = () => navigate('/decisions');
      /* il chip Filing apre i dettagli della run sulla scheda Filing */
      const apriFiling = () => apriDettagli('filing');
      const capo: VistaCapo = {
        stato: capoStato, colore: coloreDi('capo'),
        titolo: capoStato === 'ko' ? w.koTitle
          : capoStato === 'run' || capoStato === 'stale' ? (P.capoT != null && !nonConfermata ? w.capoWriting(fmtDurShort(Math.max(0, P.runSec - P.capoT))) : w.capoWritingNoTime)
          : capoStato === 'ok' ? (memoId != null ? w.capoSaved(memoId) : w.pillDone)
          : chiusaConRun ? w.capoNoMemo
          : vivo ? w.capoWaiting : w.capoIdle,
        sotto: capoStato === 'ko' ? capoV!.t
          : capoStato === 'stale' ? (fermoDa != null ? w.capoWritingStale(fmtDurShort(fermoDa)) : w.unconfirmedDoing)
          : capoStato === 'run' ? (state?.updated_at ? w.capoWritingSub(ora(state.updated_at)) : tr('activity.capoStartMissing'))
          : capoStato === 'ok' ? w.capoSavedSub(fmtDurShort(capoD?.dur), ora(state?.completed_at))
          : chiusaConRun ? w.capoNoMemoSub
          : vivo ? w.capoWaitingSub(consegnati, nDesk) : '',
        pastiglia: capoStato === 'ko' ? w.stKo : capoStato === 'run' || capoStato === 'stale' ? w.capoPillWriting
          : capoStato === 'ok' ? w.capoPillDone : w.capoPillWaiting,
        stadi: ([['_red_team', w.stageRedTeam], ['_reflection', w.stageReflection], ['_action_table', w.stageActions]] as const).map(([id, nome]) => {
          const s = byId(id);
          return s?.dur != null ? { id, nome, stato: 'ok' as const, nota: fmtDurShort(s.dur) }
            : { id, nome, stato: 'wait' as const, nota: chiusaConRun && s ? w.stageNoDuration : w.stageAfter };
        }),
        azioni: capoStato === 'ok' ? <>
          <button type="button" className="bbn-btn is-primary is-sm" onClick={vaiDecisioni}><FileText size={15} />{w.openDecisions}</button>
          <button type="button" className="bbn-btn is-sm" onClick={() => navigate('/memos')}>{w.memoArchive}</button>
        </> : undefined,
      };

      /* ── le tappe ──────────────────────────────────────────────────── */
      const fase = (k: string) => P.phases.find(p => p.k === k);
      const arco = (p: { t0: number; t1: number }) => `${fmtDurShort(p.t0)} – ${fmtDurShort(p.t1)}`;
      const capoScrive = vivo && P.capo === 'running';
      const tappe: Tappa[] = [{ id: 'avvio', nome: w.stepStart,
        stato: state?.start_time ? 'done' : 'wait', sotto: state?.start_time ? ora(state.start_time) : w.stepWaiting }];
      for (const r of [0, 1, 2]) {
        const p = fase('R' + r);
        let t: Tappa = { id: 'r' + r, nome: w.stepRound(r), stato: 'wait', sotto: w.stepWaiting };
        if (vivo && capoScrive) t = p ? { ...t, stato: 'done', sotto: arco(p) } : { ...t, stato: P.round != null && r <= P.round ? 'done' : 'skip', sotto: P.round != null && r <= P.round ? w.stepDone : w.stepSkipped };
        else if (vivo && P.round != null) {
          if (r < P.round) t = { ...t, stato: 'done', sotto: p ? arco(p) : w.stepDone };
          else if (r === P.round) t = { ...t, stato: 'now', sotto: fermo ? fermoTappa : p ? w.stepNow(fmtDurShort(Math.max(0, P.runSec - p.t0))) : w.stepNowShort };
        } else if (vivo) {
          if (p) t = { ...t, stato: p.open ? 'now' : 'done', sotto: p.open ? (fermo ? fermoTappa : w.stepNow(fmtDurShort(Math.max(0, P.runSec - p.t0)))) : arco(p) };
        } else if (p) t = { ...t, stato: 'done', sotto: arco(p) };
        else if (state?.start_time) {
          /* review PR #19: l'assenza dal tool_log (tappato a 50) non prova «nessuna chiamata»;
             lo provano round_esiti «assente» o la lavagna assente. Senza round_esiti = n.d. */
          const re0 = state.round_esiti?.[String(r)]?.stato;
          t = re0 === 'eseguito' ? { ...t, stato: 'done', sotto: w.stepDone }
            : re0 === 'assente' || lavagnaAssente ? { ...t, stato: 'skip', sotto: w.stepSkipped }
            : { ...t, stato: 'skip', sotto: w.stepRoundNd };
        }
        /* round_esiti (06/10): «ripreso» = fatto in un tentativo precedente, non «nessuna chiamata» */
        const re = !vivo ? state?.round_esiti?.[String(r)]?.stato : undefined;
        if (re === 'ripreso') t = { ...t, stato: 'done', sotto: w.stepResumed };
        else if (re === 'misto') t = { ...t, stato: 'done', sotto: (p ? arco(p) : w.stepDone) + ' · ' + w.stepPartlyResumed };
        tappe.push(t);
      }
      const sintesi = fase('SINTESI');
      tappe.push(capoScrive
        ? { id: 'sintesi', nome: w.stepSynthesis, stato: 'now', sotto: fermo ? fermoTappa
            : P.capoT != null ? w.stepNow(fmtDurShort(Math.max(0, P.runSec - P.capoT))) : w.stepNowShort }
        : !vivo && sintesi ? { id: 'sintesi', nome: w.stepSynthesis, stato: 'done', sotto: arco(sintesi) }
        : chiusaConRun && (memoOk || P.capo === 'done') ? { id: 'sintesi', nome: w.stepSynthesis, stato: 'done', sotto: w.stepDone }
        : chiusaConRun ? { id: 'sintesi', nome: w.stepSynthesis, stato: 'skip', sotto: w.stepSkipped }
        : { id: 'sintesi', nome: w.stepSynthesis, stato: 'wait', sotto: w.stepWaiting });
      tappe.push(chiusaConRun && memoOk
        ? { id: 'memo', nome: w.stepMemo, stato: 'done', sotto: memoId != null ? `#${memoId} · ${ora(state?.completed_at)}` : ora(state?.completed_at) }
        : chiusaConRun ? { id: 'memo', nome: w.stepMemo, stato: 'skip', sotto: w.stepNoMemo }
        : { id: 'memo', nome: w.stepMemo, stato: 'wait', sotto: w.stepWaiting });

      /* ── la frase del presente (solo a run viva) ───────────────────── */
      const fraRound = P.phases.find(p => p.k === 'FRA ROUND' && p.open);
      const adesso: [string, string?] | null = !vivo ? null
        : fermoDa != null ? [w.nowStale(fmtDurShort(fermoDa)), w.nowStaleSub(hhmm(state?.updated_at))]
        : nonConfermata ? [w.nowUnconfirmed, state?.updated_at ? w.nowStaleSub(hhmm(state.updated_at)) : undefined]
        : capoScrive ? [w.nowSynthesis, w.nowCapo]
        : P.round != null && P.running.length ? [w.nowRound(P.round), w.nowWorking(P.running.length, nDesk, consegnati)]
        : fraRound ? [w.nowBetween]
        : !P.calls.length ? [w.nowStarting]
        : P.round != null ? [w.nowRound(P.round), w.nowWorking(P.running.length, nDesk, consegnati)] : null;

      /* ── cifre ─────────────────────────────────────────────────────── */
      const chiamateTot = P.logTappato && P.nCallsTot == null ? null : (P.nCallsTot ?? P.calls.length);
      const costoTesto = (partial ? '~' : '') + fmtEur(total?.cost_eur);
      const costoTono = !hasTotal || partial || notes.length ? 'warn' as const : undefined;
      /* run non confermata: orologio fermo, durata all'ultimo heartbeat (dichiarata), n.d. senza updated_at */
      const durataRun = nonConfermata ? (isFinite(ultimoHbMs) ? P.runSec : null) : isRunning ? (elapsed || P.runSec) : P.runSec;
      const kDurata = <Kpi etichetta={w.kDuration} valore={fmtDurShort(durataRun)} nd={durataRun == null}
        sotto={nonConfermata ? (isFinite(ultimoHbMs) ? w.sLastKnown(hhmm(state?.updated_at)) : w.sClockStopped)
          : isRunning ? w.sClock : lavoro > 0 ? w.sWork(fmtDurShort(lavoro)) : tr('activity.durationMissing')}
        tono={nonConfermata ? 'warn' : undefined} />;
      /* review PR #19: lavagna assente = la run e' morta prima del comitato, nessuna chiamata LLM
         partita. Il costo di QUESTA run e' «nessuno» (un fatto, non un buco): niente «n.d.» sopra
         «nessun costo», e niente 0,00 € inventato. Se il backend porta comunque un totale, vince quello. */
      const senzaCostoRun = lavagnaAssente && !isRunning && !hasTotal;
      const kCosto = senzaCostoRun
        ? <Kpi etichetta={w.kCost} valore={w.kNoRunCost} sotto={w.sNoRunCost} />
        : <Kpi etichetta={isRunning ? w.kCostSoFar : w.kCost} valore={costoTesto} nd={!hasTotal}
        sotto={!hasTotal ? w.sUnpriced : isRunning ? (partial ? w.sSoFarPartial : w.sSoFar)
          : koVisibile ? w.sKoSpent(fmtEur(koCost)) : partial ? w.sPartial : w.sComplete}
        tono={koVisibile && !isRunning ? 'warn' : costoTono} />;
      const kChiamate = <Kpi etichetta={w.kCalls} valore={chiamateTot ?? tr('activity.unavailable')} nd={chiamateTot == null}
        sotto={P.logTappato ? w.sLast50 : isRunning ? w.sDistinctTools(P.nToolsDistinct) : w.sTickers(P.tickers.length)} />;
      const kQuarta = par != null
        ? <Kpi etichetta={w.kParallelism} valore={fmtN(par, 2) + '×'} sotto={w.sTogether} />
        : <Kpi etichetta={w.kTickers} valore={P.tickers.length} sotto={isRunning ? w.sTickersSoFar : w.sDistinctTools(P.nToolsDistinct)} />;
      /* «Nessun errore» solo su una run completata: interrotta, ferma o senza esito lo dicono (revisione PR #16) */
      const esitoGrave = ['bloccata', 'fallita', 'interrotta'].includes(statoEsito);
      /* «Nessun errore» non si dice se l'esito finale di un desk con tentativi in errore non e' dichiarato */
      const sottoEsito = [tentativiKo.length ? w.attemptsShort(tentativiKo.length) : '',
        statoEsito !== 'completata' ? w.memoEsito(esito?.memo ?? null, memoId) : ''].filter(Boolean).join(' · ');
      const kEsito = <Kpi etichetta={w.kOutcome}
        valore={koIds.length ? w.outcomeKo(koIds.length) : statoEsito === 'completata' && !tentativiSenzaFinale.length ? w.outcomeOk : w.pillEsito(statoEsito)}
        sotto={koIds.length ? koIds.map(id => nomeDi({ id, name: id })).join(', ') : sottoEsito || undefined}
        tono={koIds.length || esitoGrave ? 'bad' : statoEsito !== 'completata' || tentativiSenzaFinale.length ? 'warn' : undefined} />;

      const cieco = !state || !!liveErr;
      const inAvvio = runAttiva && !isRunning;   // processo vivo, heartbeat ancora della run precedente
      const nonVerificabile = statoEsito === 'in_corso_senza_segnale' && esito?.fonte === 'processo_non_verificabile';
      const chiusaEsito = !runAttiva && !!esito?.chiusa && !!state?.start_time;
      const tonoEsito = statoEsito === 'completata' ? 'is-su' : ['bloccata', 'fallita', 'interrotta'].includes(statoEsito) ? 'is-giu' : 'is-warn';
      const pillola = !state && !liveErr && !hbIll ? <span className="bbn-pill is-piatto"><i className="ag-dot" />{w.pillQuerying}</span>
        : cieco ? <span className="bbn-pill is-giu"><WifiOff size={13} />{w.pillUnreadable}</span>
        : nonVerificabile ? <span className="bbn-pill is-warn"><i className="ag-dot" />{w.pillUnverified}</span>
        : inAvvio ? <span className="bbn-pill is-acc"><i className="ag-pulse" />{w.pillStarting}</span>
        : isRunning ? <span className={'bbn-pill ' + (fermoDa != null ? 'is-warn' : 'is-acc')}><i className={fermoDa != null ? 'ag-dot' : 'ag-pulse'} />{fermoDa != null ? w.pillStuck : w.pillRunning}</span>
        : chiusaEsito && (finitaQui || statoEsito !== 'completata') ? <span className={'bbn-pill ' + tonoEsito}><i className="ag-dot" />{w.pillEsito(statoEsito)}</span>
        : <span className="bbn-pill is-piatto"><i className="ag-dot" />{w.pillIdle}</span>;
      const lingua = state?.language === 'it' ? tr('activity.originalRunIt') : state?.language === 'en' ? tr('activity.originalRunEn') : null;
      const meta = isRunning && state?.start_time ? [w.startedAt(ora(state.start_time)), lingua].filter(Boolean).join(' · ')
        : chiusaEsito && (finitaQui || statoEsito !== 'completata') ? [w.runSpan(giorno(state.start_time), ora(state.start_time), ora(state.completed_at)), lingua].filter(Boolean).join(' · ')
        : '';
      /* la run chiusa male si mostra sempre; una completata solo se e' finita sotto gli occhi del PM */
      const vista: 'cieco' | 'viva' | 'finita' | 'riposo' = cieco ? 'cieco' : runAttiva ? 'viva'
        : chiusaEsito && (finitaQui || statoEsito !== 'completata') ? 'finita' : 'riposo';
      const titolo = vista === 'cieco' ? (state || liveErr || hbIll ? w.titleUnreadable : w.titleIdle)
        : vista === 'viva' ? (nonVerificabile ? w.titleUnverified : inAvvio ? w.titleStarting : capoScrive ? w.titleSynthesis : w.titleLive)
        : vista === 'finita' ? (statoEsito === 'completata' ? (memoOk ? w.titleDone(memoId) : esito?.memo === false ? w.titleDoneNoMemo : w.titleDoneMemoUnmeasured) : w.titleEsito(statoEsito))
        : w.titleIdle;
      const descrizione = vista === 'cieco' ? (state || liveErr || hbIll ? w.descUnreadable(state?.updated_at ? hhmm(state.updated_at) : null) : w.descIdle(nDesk))
        : vista === 'viva' ? (nonVerificabile ? w.descUnverified : inAvvio ? w.descStarting : w.descLive)
        : vista === 'finita' ? (lavagnaAssente ? w.descNoCommittee
          : statoEsito === 'completata' && memoOk ? w.descDone(nDesk, P.phases.filter(p => /^R\d/.test(p.k)).length)
          : w.descEsito(statoEsito, esito?.motivo ?? null, esito?.memo ?? null, memoId, !!esito?.ripresaDisponibile))
        : w.descIdle(nDesk);
      const bottone = runAttiva
        ? <button type="button" className="bbn-btn ag-stop" onClick={stopRun} disabled={stopping}
            title={activeTaskId ? tr('activity.stopTask', { a: activeTaskId }) : tr('activity.noTrackedTask')}>
            <Square size={15} />{stopping ? w.stopping : w.stop}</button>
        : vista === 'finita' && memoOk
          ? <><button type="button" className="bbn-btn is-primary" onClick={vaiDecisioni}><FileText size={16} />{w.openDecisions}</button>
              <button type="button" className="bbn-btn ag-launch" onClick={() => setAskRun(true)}><Play size={15} />{w.newRun}</button></>
          : <button type="button" className="bbn-btn is-primary ag-launch" onClick={() => setAskRun(true)}><Play size={15} />{w.launch}</button>;

      /* ── avvisi: i buchi in vetrina, non nei tooltip (regola PM 14/07) ── */
      const avvisi: { k: string; tono: 'bad' | 'warn' | 'good' | 'info'; icona: ReactNode; testo: ReactNode; azione?: ReactNode }[] = [];
      if (vista === 'finita' && statoEsito === 'completata') avvisi.push({ k: 'finita', tono: 'good', icona: <Check size={18} />,
        testo: <b>{w.done(ora(state?.completed_at), memoOk ? memoId : null)}</b>,
        azione: memoOk ? <button type="button" className="bbn-link" onClick={vaiDecisioni}>{w.openDecisions} <ArrowUpRight size={14} /></button> : undefined });
      if (vista === 'finita' && esito?.ripresaDisponibile) avvisi.push({ k: 'ripresa', tono: 'info', icona: <Play size={18} />,
        testo: <b>{w.resumeAvailable}</b>,
        azione: <button type="button" className="bbn-link" onClick={() => apriDettagli('recupero')}>{w.resumeOpen} <ArrowUpRight size={14} /></button> });
      /* tentativi falliti e poi riusciti: storia della run, non un KO (06/10) */
      const ritentati = (total?.tentativi_falliti_poi_riusciti || []).filter(x => x && x.agent);
      if (ritentati.length) avvisi.push({ k: 'ritentati', tono: 'info', icona: <CircleAlert size={18} />,
        testo: <>{ritentati.map((x, i) => <span key={i} className="ag-ban-line">{w.retriedBeforeSuccess(nomeDi({ id: x.agent, name: x.agent }), x.tentativi_falliti, x.round)}</span>)}</> });
      if (triggerMsg) avvisi.push({ k: 'avvio', tono: 'info', icona: <Play size={18} />,
        testo: frase(triggerMsg.kind === 'starting' ? tr('activity.starting') : tr('activity.runActiveEstimate', { a: triggerMsg.id ?? tr('activity.unavailable') })) });
      if (triggerMsg?.check) avvisi.push({ k: 'ti-check', tono: 'warn', icona: <TriangleAlert size={18} />,
        testo: <b data-ag-ti-check={triggerMsg.check.status}>{testoControlloAttiva(tr, triggerMsg.check)}</b> });
      if (stopNotice) avvisi.push({ k: 'stop', tono: stopNotice.running === false && !stopNotice.issues.length ? 'good' : 'bad', icona: <Square size={18} />,
        testo: <><b>{tr(stopNotice.running === false ? 'activity.stopObservedIdle' : 'activity.stopUnconfirmed')}</b>
          {stopNotice.issues.map((issue, i) => <span key={i} className="ag-ban-line">{issue.source} — {issue.detail || tr('activity.errorNotDescribed')}</span>)}</> });
      if (liveErr) avvisi.push({ k: 'backend', tono: 'bad', icona: <WifiOff size={18} />,
        testo: <><b>{w.backendDown}</b> {w.backendDownSub(liveErr, state?.updated_at ? hhmm(state.updated_at) : null)}</> });
      if (isRunning && (state?.stale_warning || hbIllLungo)) avvisi.push({ k: 'fermo', tono: 'bad', icona: <TriangleAlert size={18} />,
        /* review 31/08: a poll illeggibile `stale_seconds` resta CONGELATO all'ultimo
           payload buono mentre l'orologio avanza — vince la misura che cresce */
        testo: <><b>{w.stuck}</b> {w.stuckSub(Math.max(1, Math.floor(Math.max(state?.stale_seconds || 0, fermoDa || 0) / 60)))}
          {/* mentre il Capo genera il memo nessuno scrive l'heartbeat: un consiglio di FERMARE senza dirlo costerebbe il memo */}
          {P.capo === 'running' && <> {tr('activity.capoHeartbeatNote')}</>}</> });
      if (hbIll) avvisi.push({ k: 'illeggibile', tono: 'warn', icona: <TriangleAlert size={18} />,
        testo: <><b>{frase(tr('activity.heartbeatUnreadableUpper') + (hbIllDur != null && hbIllDur >= 3 ? tr('activity.unreadableSince', { a: fmtDurShort(hbIllDur) }) : tr('activity.unreadableNow')))}.</b>{' '}
          {maiuscola(tr('activity.unreadableMessagePrefix'))}{hbIll.msg ?? tr('activity.unreadableHeartbeat')}{tr('activity.unreadableMessageSuffix')}{state
            ? <>{tr('activity.lastGoodState')}{state.updated_at ? tr('activity.heartbeatTime', { a: hhmm(state.updated_at) }) : ''}</>
            : <>{tr('activity.noneSinceOpen')}</>} {tr('activity.retryCadence')}</> });
      if (koIds.length > 0) avvisi.push({ k: 'ko', tono: 'bad', icona: <CircleAlert size={18} />,
        testo: <><b>{w.koBanner(koIds.length, nDesk || koIds.length)}</b>{' '}
          {/* review PR #19: koIds sono sempre KO FINALI (v2, o status_finale sull'heartbeat vecchio):
              «ha comunque consegnato il report» non e' mai vero per loro */}
          <span>{koIds.map(id => nomeDi({ id, name: id })).join(tr('movements.and'))} {tr(koIds.length === 1 ? 'activity.koFinalOne' : 'activity.koFinal')}</span>.{' '}
          <span>{maiuscola(tr('activity.withinTotalPrefix'))} {fmtEur(total?.cost_eur)} {tr('activity.include')} <b>{fmtEur(koCost)}</b>
            {total?.cost_eur && koCost != null ? ` (${fmtN(koCost / total.cost_eur * 100, 0)}%)` : ''} {tr(koIds.length === 1 ? 'activity.spentByThemOne' : 'activity.spentByThem')}</span>.</> });
      /* heartbeat senza marcatore: error_agents = «almeno un tentativo in errore». Si dice
         questo, con l'esito finale quando status_finale lo porta, mai «finito in errore» */
      const nomi = (ids: string[]) => ids.map(id => nomeDi({ id, name: id })).join(tr('movements.and'));
      if (tentativiKo.length > 0) avvisi.push({ k: 'tentativi', tono: 'warn', icona: <TriangleAlert size={18} />,
        testo: <><b>{w.attemptsBanner(tentativiKo.length, nDesk || tentativiKo.length)}</b>{' '}
          {w.attemptsOldSemantics}
          {tentativiPoiOk.length > 0 && <span className="ag-ban-line">{w.attemptsFinalOk(nomi(tentativiPoiOk))}</span>}
          {tentativiSenzaFinale.length > 0 && <span className="ag-ban-line">{w.attemptsFinalNd(nomi(tentativiSenzaFinale))}</span>}</> });
      /* «totale parziale» lo dice il titolo del banner: la nota del catalogo non si ripete */
      const altreNote = partial ? notes.filter(n => n !== tr('activity.partialTotal')) : notes;
      if (notes.length > 0) avvisi.push({ k: 'costi', tono: 'warn', icona: <TriangleAlert size={18} />,
        testo: <>{partial && <><b>{w.partialBanner}</b> </>}{altreNote.map(maiuscola).join(' — ')}</> });
      if (rosterErr) avvisi.push({ k: 'roster', tono: 'bad', icona: <CircleAlert size={18} />,
        testo: <><b>{frase(tr('activity.rosterMissing'))}.</b> {tr('activity.rosterErrorPrefix')}{rosterErr}{tr('activity.rosterErrorSuffix')}</> });

      /* ── dettagli della run (cassetto): costi, chiamate, strumenti, filing, recupero ── */
      const conteggioChiamate = P.logTappato ? w.last50Of(P.calls.length, P.nCallsTot) : w.allN(P.calls.length);
      const corpoChiamate = P.calls.length === 0 ? <p className="bbn-empty">{w.noCalls}</p> : (
        <div className="ag-tape">
          <div className="ag-day">{isRunning ? w.callsNow : w.callsNewestFirst}</div>
          {[...P.calls].reverse().map((c, i) => (
            <div key={i} className={'ag-call' + (isRunning && P.runSec - c.t < 20 ? ' is-hot' : '')} title={tr('activity.wallClock', { a: c.hhmm, b: c.input })}>
              <IconaDesk id={c.a} colore={coloreDi(c.a)} dimensione="xs" />
              <span className="w"><code>{c.tool}</code><span>{nomeDi({ id: c.a, name: c.a })}{pulisci(c.input) ? ' · ' + pulisci(c.input) : ''}</span></span>
              <span className="t num">{fmtDurShort(c.t)}<span className="rd">R{c.r}</span></span>
            </div>
          ))}
        </div>
      );
      const righeToken: [string, number | null | undefined, string, string][] = [
        [maiuscola(tr('activity.freshInput')), total?.in, 'var(--bbn-accent)', 'input'],
        [maiuscola(tr('activity.output')), total?.out, 'var(--bbn-good)', 'output'],
        [maiuscola(tr('activity.cacheRead')), total?.cache_read, 'var(--ag-cache-read)', 'cache-read'],
        [maiuscola(tr('activity.cacheWrite')), total?.cache_write, 'var(--ag-cache-write)', 'cache-write'],
      ];
      const mxTok = Math.max(1, ...righeToken.map(r => r[1] || 0));
      const agentiCosto = [...deskElenco, ...P.stages.filter(s => s.id === 'capo')];
      const mxCosto = Math.max(0.0001, ...agentiCosto.map(a => a.cost || 0));
      const fx = total?.fx_source == null || total.fx_source === 'n.d.' ? tr('activity.unavailable') : total.fx_source;
      const corpoCosti = !total ? (senzaCostoRun ? <p className="bbn-empty">{w.descNoCommittee}</p>
          : <p className="bbn-empty">{tr('activity.heartbeatMissing')} <b>usage_total</b>{tr('activity.runCostsMissing')}</p>) : (
        <div className="ag-costs">
          <div className="ag-cost-tot"><div className="ag-big"><span className={'num' + (hasTotal || senzaCostoRun ? '' : ' is-nd')}>{senzaCostoRun ? w.kNoRunCost : costoTesto}</span>
            {hasTotal && <span className={'bbn-pill ' + (partial ? 'is-warn' : 'is-su')}>{partial ? w.pillPartial : w.pillComplete}</span>}
            <span className="fx">{w.fx} <b className={total.fx_source === 'live' ? 'is-live' : 'is-warn'}>{fx}</b></span></div>
          <p className="ag-foot">
            {agentiSenzaCosto > 0 ? w.sumMissing(fmtEur(sumAgents), agentiSenzaCosto)
              : total.cost_eur != null && Math.abs(sumAgents - total.cost_eur) < 0.005 ? w.sumEqual(fmtEur(sumAgents)) : w.sumDiff(fmtEur(sumAgents), fmtEur(total.cost_eur))}
            {koIds.length > 0 && <> <span className="is-ko">{w.koInside(fmtEur(koCost), koIds.length)}</span></>}
            <br />{w.engines(engineShort(deskElenco[0]?.id || 'macro', engines, deskElenco[0]?.rounds), engineShort('capo', engines))}
          </p></div>
          <div className="ag-cost-tok"><div className="ag-sub">{w.tokens}</div>
            <div className="ag-bars">{righeToken.map(([k, v, c, kind]) => (
              <div className="ag-barrow" key={kind} data-metric={kind}><span className="k">{k}</span><Barra quota={(v || 0) / mxTok} colore={c} />
                <span className="v num" title={v != null ? tr(v === 1 ? 'activity.tokenCountOne' : 'communications.tokenCount', { a: fmtN(v) }) : tr('activity.notDeclared')}>{fmtTok(v)}</span></div>
            ))}</div>
            <p className="ag-foot">{total.cache_read && total.in ? w.cacheNote(fmtN(total.cache_read / total.in, 1)) : tr('activity.cacheMissingSentence')}</p></div>
          {agentiCosto.length > 0 && <div className="ag-cost-agt"><div className="ag-sub">{w.costPerAgent}</div>
            <div className="ag-bars">{agentiCosto.map(a => {
              const ko = koIds.includes(a.id);
              const valore = a.cost != null ? (a.partial ? '~' : '') + fmtEur(a.cost) : a.id === 'capo' && P.capo === 'running' ? w.inProgressCost : tr('activity.unavailable');
              return <div className="ag-agc" key={a.id} title={tr('activity.agentTokenBreakdown', { a: nomeDi(a), b: fmtTok(a.tin), c: fmtTok(a.tout), d: fmtTok(a.cacheR), e: fmtTok(a.cacheW), f: [a.tin, a.tout, a.cacheR, a.cacheW].every(v => v != null) ? '' : tr('activity.partialTokens') })}>
                <IconaDesk id={a.id} colore={coloreDi(a.id) || a.color} dimensione="xs" /><span className="k">{nomeDi(a)}</span>
                <Barra quota={(a.cost || 0) / mxCosto} colore={ko ? 'var(--bbn-bad)' : 'var(--bbn-accent)'} />
                <span className={'v num' + (ko ? ' is-ko' : '') + (a.cost == null ? ' is-nd' : '')}>{valore}</span></div>;
            })}</div></div>}
        </div>
      );
      const deskDiStrumento = (id: string) => deskElenco.find(d => d.id === id);
      const mxTool = P.tools[0]?.n || 1;
      const corpoStrumenti = P.tools.length === 0 ? <p className="bbn-empty">{tr('activity.theLog')} <b>tool_log</b> {tr('activity.heartbeatLogEmpty')}</p> : (
        <div className="ag-tools">
          <div className="ag-legend">{deskElenco.filter(d => d.nCalls > 0).map(d => <span key={d.id}><i style={{ background: coloreDesk(coloreDi(d.id) || d.color) }} />{nomeDi(d)}</span>)}</div>
          {P.tools.map(t => (
            <div className="ag-tool" key={t.tool} title={Object.entries(t.by).map(([a, n]) => `${a} ${n}`).join(' · ')}>
              <code>{t.tool}</code><span className="n num">{t.n}</span>
              <span className="ag-bar">{Object.entries(t.by).sort((a, b) => b[1] - a[1]).map(([a, n]) =>
                <u key={a} style={{ width: `${n / mxTool * 100}%`, background: coloreDesk(coloreDi(a) || deskDiStrumento(a)?.color) }} />)}</span>
            </div>
          ))}
          <p className="ag-foot">{P.logTappato ? w.toolsFootLive(P.calls.length, P.nCallsTot) : w.toolsFoot(P.nToolsDistinct, P.calls.length)}</p>
        </div>
      );
      const corpoTicker = P.tickers.length === 0 ? <p className="bbn-empty">{w.noTickers}</p> : (
        <div className="ag-tickers">{P.tickers.map(x => <span key={x.k} className="ag-tk">{x.k}<b className="num">{x.n}</b></span>)}</div>
      );
      const corpoFiling = <div className="ag-filing-tab"><CoperturaFiling dati={filing} errore={filingErr} occupato={filingBusy} bloccato={isRunning}
        esito={filingEsito} onAttiva={attivaFiling} onRiprova={caricaFiling} fmt={n => fmtN(n)} conTitolo
        onApri={t => navigate(t ? `/filing?t=${encodeURIComponent(t)}` : '/filing')} /></div>;
      const corpoRecupero = <WeeklyRecoveryPanel disabled={isRunning} onStarted={tid => {
        try { localStorage.setItem(ACTIVE_RUN_KEY, tid); } catch {}
        setActiveTaskId(tid); setTriggerMsg({ kind: 'active', id: tid }); setStopNotice(null); setDettagliAperti(false);
        setState(prev => ({ ...(prev || {}), running: true, start_time: new Date().toISOString(),
          message: tr('tradeidea.recoveryBusy') } as AgentsLiveState));
      }} />;
      const corpi: Record<SchedaPannello, ReactNode> = { costi: corpoCosti, chiamate: corpoChiamate,
        strumenti: <>{corpoStrumenti}<div className="ag-sub ag-sub-tk">{w.tabTickers}</div>{corpoTicker}</>, filing: corpoFiling, recupero: corpoRecupero };
      const conteggi: Record<SchedaPannello, string> = {
        costi: '', chiamate: conteggioChiamate, strumenti: w.lookedCount(P.nToolsDistinct, P.tickers.length),
        filing: filing ? w.filingCount(filing.copertura.con_confronto, filing.copertura.totale) : '', recupero: '',
      };

      /* ── il tavolo ─────────────────────────────────────────────────── */
      const tintaDi = (id: string) => tintaDesk(coloreDi(id) || byId(id)?.color);
      const nomeId = (id: string) => nomeDi({ id, name: byId(id)?.name || id });
      const attesi = typeof state?.expected_reports === 'number' && state.expected_reports > 0 ? state.expected_reports : null;
      const conta = (...stati: StatoDesk[]) => desks.filter(d => stati.includes(d.stato)).length;
      /* heartbeat fermo = stato non confermato: sta fra i problemi, non fra chi lavora (revisione PR #16) */
      const problemi = conta('ko', 'nd', 'stale');
      const datiTavolo: DatiTavolo = {
        vivo,
        nodi: desks.map(d => ({ id: d.id, nome: d.nome, colore: d.colore, tinta: tintaDesk(d.colore), stato: d.stato,
          pastiglia: d.pastiglia, titolo: d.titolo, esito: d.esito, round: d.round,
          fare: { testo: d.fare.testo, tk: d.fare.tk ?? null, codice: d.fare.codice, sotto: d.fare.sotto } })),
        capo: { stato: capoStato, colore: coloreDi('capo'), segmenti: consegne.map(c => tintaDi(c.id)), attesi,
          testo: !state?.start_time ? w.capoReady : attesi != null ? w.reportsOf(consegne.length, attesi) : w.reportsN(consegne.length) },
        chiamate: PC.calls.map(c => ({ key: `${c.a}|${c.r}|${c.hhmm}|${c.tool}|${c.input}`, a: c.a })),
        report: consegne.map(c => ({ key: c.key, a: c.id })),
        angoli: vista === 'viva' || vista === 'finita' ? {
          crono: fmtDurShort(durataRun),
          /* niente durata «tipica» (nessuno strumento la misura) e la barra si riempie solo coi
             report consegnati: una run finita male non arriva al 100% (revisione PR #16) */
          sotto: vista === 'finita' ? w.runEnded : attesi != null ? w.reportsOf(consegne.length, attesi) : w.reportsN(consegne.length),
          avanz: attesi != null ? consegne.length / attesi : null,
          legenda: [{ k: 'lav', testo: w.legWorking, n: conta('run', 'think') }, { k: 'att', testo: w.legWaiting, n: conta('wait') },
            { k: 'ok', testo: w.legDone, n: conta('ok') }, ...(problemi ? [{ k: 'ko', testo: w.legIssue, n: problemi }] : [])],
        } : null,
        conclusioni: consegne.slice(-4).reverse().map(c => ({ key: c.key, id: c.id, nome: nomeId(c.id), tinta: tintaDi(c.id), round: c.r,
          ora: c.visto ? ora(new Date(c.ms).toISOString()) : '', frase: primaFrase(c.testo) })),
      };

      /* ── le corsie ─────────────────────────────────────────────────── */
      const sint = PC.phases.find(p => p.k === 'SINTESI');
      const capoBarre = capoScrive && P.capoT != null ? [{ t0: P.capoT, t1: P.runSec, aperta: true, etichetta: w.stepSynthesis }]
        : sint ? [{ t0: sint.t0, t1: sint.t1, aperta: !!sint.open, etichetta: w.stepSynthesis }] : [];
      const capoCosto = capoD?.cost != null ? (capoD.partial ? '~' : '') + fmtEur(capoD.cost) : P.capo === 'running' ? w.inProgressCost : tr('activity.unavailable');
      const datiCorsie: DatiCorsie = {
        T: Math.max(vivo ? 1800 : 60, PC.runSec * 1.03), ora: vivo ? PC.runSec : null,
        vuota: PC.windows.length === 0 && capoBarre.length === 0, parziale: P.logTappato,
        fasi: PC.phases.filter(p => p.k !== 'FRA ROUND').map(p => ({ k: p.k, t0: p.t0, etichetta: p.k === 'SINTESI' ? w.stepSynthesis : w.stepRound(Number(p.k.slice(1))) })),
        righe: desks.map(v => ({ id: v.id, nome: v.nome, ruolo: v.ruolo, tinta: tintaDesk(v.colore), esito: v.esito, dettaglio: v.dettaglio,
          chiamate: v.chiamate, costo: v.costo, costoNd: v.costoNd, report: consegne.some(c => c.id === v.id),
          barre: PC.windows.filter(x => x.a === v.id).map(x => ({ r: x.r, t0: x.t0, t1: x.t1, aperta: !!x.open,
            tacche: PC.calls.filter(c => c.a === v.id && c.r === x.r).map(c => c.t) })) })),
        capo: { nome: w.capo, sotto: w.lanesCapo, barre: capoBarre, costo: capoCosto, costoNd: capoD?.cost == null && P.capo !== 'running', memo: memoOk },
      };

      /* ── sotto la lente: i ticker scritti negli input, con chi li ha guardati ── */
      const lente = new Map<string, { n: number; desks: Set<string> }>();
      for (const c of PC.calls) for (const k of tickerDiInput(c.input)) {
        const x = lente.get(k) || { n: 0, desks: new Set<string>() }; x.n++; x.desks.add(c.a); lente.set(k, x);
      }
      const lenteTop = [...lente.entries()].sort((a, b) => b[1].n - a[1].n);
      const quotaCosti = agentiCosto.filter(a => a.cost != null && a.cost > 0);
      const sommaQuote = quotaCosti.reduce((s, a) => s + (a.cost || 0), 0);
      /* la barra ripartisce solo i costi noti: chi non ce l'ha si dichiara (revisione PR #16) */
      const quoteIgnote = agentiCosto.filter(a => a.cost == null && !(a.id === 'capo' && P.capo === 'running')).length;

      /* ── report del desk scelto ─────────────────────────────────────── */
      const sel = repDesk ? desks.find(d => d.id === repDesk.id) || null : null;
      const giriSel = sel ? state?.reports_by_specialist?.[sel.id] || {} : {};
      const testoGiro = (r: number) => Object.entries(giriSel).find(([k]) => Number.parseInt(String(k).replace(/\D/g, ''), 10) === r)?.[1] ?? null;
      const roundSel = sel ? [0, 1, 2].filter(r => r < 2 || !r2 || r2.includes(sel.id) || testoGiro(r) != null) : [];
      const consegnatiSel = roundSel.filter(r => testoGiro(r) != null);
      const rSel = repDesk?.r ?? (consegnatiSel.length ? consegnatiSel[consegnatiSel.length - 1] : 0);
      const testoSel = sel ? testoGiro(rSel) : null;
      const tkSel = sel ? [...new Set(PC.calls.filter(c => c.a === sel.id).flatMap(c => tickerDiInput(c.input)))] : [];

      const faseOra = tappe.find(t => t.stato === 'now');
      const titoloAdesso = vista === 'cieco' ? w.titleUnreadable : vista === 'viva' ? (faseOra?.nome ?? w.pillRunning)
        : vista === 'finita' ? w.pillEsito(statoEsito) : w.pillIdle;
      const lavorano = desks.filter(d => d.stato === 'run' || d.stato === 'think');

      return (
        <div className="bbn-agents bbn-font" data-vista={vista} data-heartbeat={P.stale ? 'fermo' : 'ok'}>
          {/* ── la run: stato, tappe, comandi ── */}
          <section className="bbn-card ag-run" aria-live="polite">
            <div className="ag-run-id">
              <AnelloDesk id="capo" colore={coloreDi('capo')} grande
                stato={vista === 'viva' ? (fermoDa != null || nonVerificabile ? 'stale' : 'run') : vista === 'finita' && memoOk ? 'ok' : vista === 'finita' && tonoEsito === 'is-giu' ? 'ko' : 'wait'} />
              <div className="ag-run-copy">
                <span className="k">{pillola}{meta && <span className="meta">{meta}</span>}</span>
                <h1>{titolo}</h1>
                <p>{descrizione}</p>
              </div>
            </div>
            <Tappe tappe={tappe} etichetta={w.steps} />
            <div className="ag-run-act">
              <p className="ag-filing-line" data-filing-line={filingErr ? 'errore' : filing ? (mancantiFiling(filing) ? 'mancanti' : 'completa') : 'caricamento'}>
                <FileText size={14} />
                <span>{filingErr ? w.filingLineDown : filing ? w.filingLine(filing.copertura.con_confronto, filing.copertura.totale, mancantiFiling(filing)) : w.filingLineLoading}</span>
                <button type="button" className="bbn-link" data-filing-open="1" onClick={apriFiling}>{w.filingOpen} <ArrowUpRight size={14} /></button>
              </p>
              <button type="button" className="bbn-btn" onClick={() => navigate('/agents/trade-idea')}><Lightbulb size={15} />{tr('tradeidea.openTradeIdea')}</button>
              <button type="button" className="bbn-btn ag-dett" data-dettagli="1" onClick={() => apriDettagli()} aria-label={w.detailsOpen} title={w.detailsOpen}><PanelRight size={16} /></button>
              {bottone}
            </div>
          </section>

          {avvisi.length > 0 && <div className="ag-bans">
            {avvisi.map(a => <div key={a.k} className={'ag-ban is-' + a.tono} data-avviso={a.k} role={a.tono === 'bad' ? 'alert' : 'status'}>
              {a.icona}<span>{a.testo}</span>{a.azione && <span className="act">{a.azione}</span>}</div>)}
          </div>}

          {/* ── il tavolo ── */}
          <section className="bbn-card ag-tavolo" aria-label={w.desks}>
            <Tavolo dati={datiTavolo} box={dialBox} areaRef={dialRef} onApri={apriReport}
              testi={{ etichetta: w.tableLabel, elapsed: w.elapsed, legenda: w.legendTitle, conclTitolo: w.conclTitle,
                conclHint: w.conclHint, conclVuoto: w.conclEmpty, roundBreve: r => w.stepRound(r) }} />
          </section>

          {/* ── adesso, sotto la lente, in cifre ── */}
          <div className="ag-side">
            <section className="bbn-card ag-adesso">
              <header className="bbn-card-head"><h2>{w.nowTitle}</h2><span className="bbn-grow" />
                {lavorano.length > 0 && <span className="ag-chi">{lavorano.map(d => <IconaDesk key={d.id} id={d.id} colore={d.colore} dimensione="xs" />)}</span>}</header>
              <div className="ag-adesso-b">
                <b className="ag-fase">{titoloAdesso}</b>
                {adesso ? <p className="ag-now"><span><b>{adesso[0]}</b>{adesso[1] ? ' · ' + adesso[1] : ''}</span>
                    {capoScrive && !fermo && <span className="muted">{w.nowCapoNote}</span>}</p>
                  : <p className="ag-now-p">{vista === 'finita' ? (memoOk ? w.nowDoneMemo : descrizione) : vista === 'cieco' || inAvvio || nonVerificabile ? descrizione : w.nowIdle}</p>}
                {(capoScrive || vista === 'finita') && <div className="ag-stadi">{capo.stadi.map(s => (
                  <span key={s.id} className={'ag-stadio is-' + s.stato} data-stadio={s.id}>{s.stato === 'ok' ? <Check size={13} /> : <i />}{s.nome}<em>{s.nota}</em></span>
                ))}</div>}
                {vista === 'riposo' && state?.start_time && <div className="ag-last">
                  <span className="t">{w.lastRun} <span>· {w.runSpan(giorno(state.start_time), ora(state.start_time), state.completed_at ? ora(state.completed_at) : tr('activity.unavailable'))}{lingua ? ' · ' + lingua : ''}</span></span>
                  {memoOk && memoId != null && <button type="button" className="bbn-link ag-memo-link" onClick={vaiDecisioni}>{w.memo(memoId)} <ArrowUpRight size={14} /></button>}
                </div>}
                {capo.azioni && <div className="ag-adesso-act">{capo.azioni}</div>}
              </div>
            </section>
            <section className="bbn-card ag-lente">
              <header className="bbn-card-head"><h2>{w.lensTitle}</h2><span className="bbn-grow" />
                {lenteTop.length > 0 && <span className="bbn-card-count">{w.lensCount(lenteTop.length)}</span>}</header>
              {lenteTop.length === 0 ? <p className="bbn-empty">{w.lensEmpty}</p> : <div className="ag-lente-l">
                {lenteTop.slice(0, 16).map(([k, x]) => (
                  <span key={k} className="ag-lt"><b>{k}</b>
                    <em aria-hidden="true">{[...x.desks].slice(0, 4).map(id => <i key={id} style={{ background: tintaDi(id) }} title={nomeId(id)} />)}</em>
                    <small className="num">{x.n}</small></span>
                ))}
                {lenteTop.length > 16 && <span className="ag-lt-more">{w.lensMore(lenteTop.length - 16)}</span>}
              </div>}
            </section>
            <section className="bbn-card ag-cifre">
              <header className="bbn-card-head"><h2>{w.figuresTitle}</h2><span className="bbn-grow" />
                <button type="button" className="bbn-link" data-dettagli-costi="1" onClick={() => apriDettagli('costi')}>{w.costDetails} <ArrowUpRight size={14} /></button></header>
              {cieco && !state ? <p className="bbn-empty">{w.descUnreadable(null)}</p> : <div className="ag-cifre-b">
                <div className="ag-cifre-costo">{kCosto}</div>
                {sommaQuote > 0 && <span className="ag-quota" title={w.costByDesk}>{quotaCosti.map(a => (
                  <u key={a.id} style={{ width: `${(a.cost || 0) / sommaQuote * 100}%`, background: tintaDi(a.id) }} title={`${nomeDi(a)} ${fmtEur(a.cost)}`} />
                ))}</span>}
                {sommaQuote > 0 && (quoteIgnote > 0 || partial) && <span className="ag-quota-nota">{w.shareKnownOnly(quoteIgnote)}</span>}
                <div className="ag-kpis">{kDurata}{kChiamate}{kQuarta}{(vista === 'finita' || vista === 'riposo') && state?.start_time ? kEsito : null}</div>
              </div>}
            </section>
          </div>

          {/* ── le corsie ── */}
          <Corsie dati={datiCorsie} onReport={id => apriReport(id)} onMemo={vaiDecisioni}
            testi={{ titolo: w.lanesTitle, hint: w.lanesHint, vuoto: w.lanesEmpty, parziale: w.lanesPartial, report: w.lanesReport, memo: w.lanesMemo }} />

          {/* ── cassetti: dettagli della run, report di un desk ── */}
          <Cassetto aperto={dettagliAperti} onChiudi={chiudiDettagli} etichetta={w.detailsOpen} chiudi={w.close} classe="ag-dettagli"
            testa={<div className="ag-cass-t"><b>{w.detailsOpen}</b><span>{w.detailsSub}</span></div>}
            schede={<><Segmenti<SchedaPannello> valore={scheda} etichetta={w.panel} onChange={setScheda} opzioni={[
                { id: 'costi', testo: w.tabCosts }, { id: 'chiamate', testo: w.tabCalls }, { id: 'strumenti', testo: w.tabTools },
                { id: 'filing', testo: w.tabFiling }, { id: 'recupero', testo: w.tabRecovery },
              ]} /><span className="bbn-grow" />{conteggi[scheda] && <span className="bbn-card-note">{conteggi[scheda]}</span>}</>}>
            {(Object.keys(corpi) as SchedaPannello[]).map(k => (
              <div key={k} className="ag-pane" data-pane={PANE[k]} hidden={scheda !== k}>
                {k === 'costi' && <h2 className="ag-pane-t">{w.costsTitle}</h2>}{corpi[k]}</div>
            ))}
          </Cassetto>
          <Cassetto aperto={!!repDesk?.aperto && !!sel} onChiudi={chiudiReport} etichetta={sel?.nome || w.desks} chiudi={w.close} classe="ag-report"
            testa={sel && <div className="ag-cass-t is-desk"><IconaDesk id={sel.id} colore={sel.colore} dimensione="md" />
              <span><b>{sel.nome}</b><span>{sel.ruolo}</span></span>
              <span className={'bbn-pill ' + ({ run: 'is-acc', think: 'is-acc', ok: 'is-su', ko: 'is-giu', nd: 'is-warn', wait: 'is-piatto', stale: 'is-warn' } as const)[sel.stato]}>{sel.pastiglia}</span></div>}
            schede={sel && <Segmenti<string> valore={String(rSel)} etichetta={w.steps}
              onChange={v => setRepDesk({ id: sel.id, r: Number(v), aperto: true })}
              opzioni={roundSel.map(r => ({ id: String(r), testo: w.stepRound(r) }))} />}>
            {sel && <div className="ag-rep-b">
              <div className="ag-rep-meta">
                <span className="ag-tk">{sel.chiamate}</span><span className="ag-tk">{sel.dur}</span>
                <span className={'ag-tk' + (sel.costoNd ? ' is-nd' : '')}>{sel.costo}</span>
                <span className="ag-tk is-muted" title={sel.dettaglio}>{sel.dettaglio}</span>
              </div>
              {tkSel.length > 0 && <div className="ag-rep-tk"><span className="ag-sub">{w.reportTickers}</span>
                <div className="ag-tickers">{tkSel.map(t => <span key={t} className="ag-tk">{t}</span>)}</div></div>}
              {testoSel != null ? <>
                <div className="ag-rep-testo">{testoSel}</div>
                <p className="ag-foot">{w.reportPreview}</p>
              </> : <p className="bbn-empty">{!state?.start_time ? w.reportNone
                : vivo && sel.stato !== 'ko' && sel.stato !== 'nd' ? w.reportWaiting(rSel, sel.nome) : w.reportMissingRound(rSel, sel.nome)}</p>}
            </div>}
          </Cassetto>
        </div>
      );
    }} />
    <RunConfirmDialog open={askRun} pagePresentation
      onConfirm={() => { setAskRun(false); triggerRun(); }}
      onCancel={() => setAskRun(false)} />
    </>
  );
}
