import { t as tr } from '@/i18n/t';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
/* ============================================================
   F4 AGENTS LIVE v3 — "LA PLANCIA ORBITALE" (Opus 5, 26/07)

   Impianto scelto dal PM sui mockup (mockup_f4_agents/opzione_2.html,
   collaudato con qa.py alle tre inquadrature del suo 49"). La run del
   comitato diventa un quadrante: un giro = la durata vera, mezzogiorno =
   lo start, un'orbita per desk in ordine di accensione.

   COSA CAMBIA RISPETTO ALLA VERSIONE PRECEDENTE (686 righe, non-v3).
   Il payload di /agents/live consegnava — e la pagina buttava via:
     · `duration_s`      10 durate per agente, mai rese da nessuna parte
     · `cache_read/write` 1,33 M token riletti = 5,5x l'input fresco, mai resi
     · i tempi del `tool_log`  stampati come testo, mai usati come TEMPO
     · la struttura R0/R1/R2   resa come "R0" e mai aggregata
     · gli `input` delle chiamate  troncati in cella: i ticker non si leggevano
   Ora sono tutti in pagina.

   TRE DIFETTI DI RESA CHIUSI, verificati sul payload vero:
   1. la griglia mostrava "ROUNDS 0/3" su una run che aveva fatto 3 round,
      perche' `reports_by_specialist` NON e' nel payload di una run chiusa.
      I round ora si ricavano dal `tool_log`, che li porta davvero.
   2. la stessa card diceva "ok" in alto (specialist_status = ha consegnato)
      e "KO" in basso (usage.status = api_error): ora il verdetto e' UNO,
      e vince il peggiore.
   3. il totale mostrava 9,25 EUR senza dire in vetrina che 3,79 EUR (41%)
      li avevano spesi due desk andati in api_error.

   La logica di comando della run (avvio + conferma Lotto D, stop, watchdog)
   e' quella di prima: non si tocca cio' che funziona.
   ============================================================ */
import { useEffect, useLayoutEffect, useMemo, useState } from 'react';
import { useT } from '@/i18n/provider';
import { leggiDetail } from '@/lib/quota';
import { useNavigate } from 'react-router-dom';
import { Bellomberg, AgentInfo, AgentsLiveState, EnginesInfo, UsageBySpecialist, UsageTotal } from '@/lib/api';
import RunConfirmDialog from '@/components/RunConfirmDialog';
import Quadrante from '@/components/Quadrante';
import SkyCanvas from '@/components/SkyCanvas';
import { Sigil } from '@/lib/Sigil';
import { useBox } from '@/lib/useBox';
import { derivePlancia, engineShort, fmtDurShort, fmtClock, type Plancia, type Desk } from '@/lib/plancia-data';
import { conservaDettaglioRun, dettaglioLeggibile, statusHttp } from '@/lib/mandato';
import { Play, Square, Radio, AlertCircle } from 'lucide-react';
import './agents-plancia.css';

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
function verdictOf(d: Pick<Desk, 'statusRun' | 'statusUsage' | 'cost'>): Verdict {
  const STATUS_DESC = statusDescriptions();
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
  /* La piastra di lettura si MISURA: le fasce delle etichette del quadrante
     partono sotto il suo fondo vero, non sotto un numero scritto a mano (v.
     BAND_L in Quadrante.tsx). Nessuna retroazione col ResizeObserver: la
     piastra e' `position:absolute` e la sua altezza dipende solo da quanti
     desk lavoravano al minuto puntato, non da dove finiscono le etichette.

     ⚠️ QUI `useBox` NON VA BENE, e il primo tentativo e' fallito proprio cosi':
     quel hook aggancia l'osservatore in un `useLayoutEffect` con dipendenze
     `[]`, cioe' UNA VOLTA SOLA al montaggio della pagina. Il quadrante (.inst)
     c'e' sempre e infatti funziona; questa piastra invece esiste solo quando
     `P.ok` e' vero, cioe' NASCE DOPO, quando arrivano i dati — e a quel punto
     l'effetto non gira piu' e la misura resta 0 per sempre. Il ref di stato
     rifa' l'effetto quando l'elemento compare davvero. */
  const [readEl, setReadEl] = useState<HTMLDivElement | null>(null);
  const [readH, setReadH] = useState(0);
  useLayoutEffect(() => {
    if (!readEl) return;
    const misura = () => setReadH(readEl.clientHeight);
    misura();
    const ro = new ResizeObserver(misura);
    ro.observe(readEl);
    return () => ro.disconnect();
  }, [readEl]);

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

  const isRunning = state?.running === true;

  /* l'orologio serve SOLO a una run viva: la sua durata non e' nel payload */
  useEffect(() => {
    if (!isRunning) return;
    const i = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(i);
  }, [isRunning]);

  useEffect(() => {
    if (!state?.start_time || !isRunning) { setElapsed(0); return; }
    const start = new Date(state.start_time).getTime();
    if (!isFinite(start)) return;
    const i = setInterval(() => setElapsed(Math.floor((Date.now() - start) / 1000)), 1000);
    return () => clearInterval(i);
  }, [state?.start_time, isRunning]);

  const P = useMemo(() => derivePlancia(state, agents, engines, now), [state, agents, engines, now, tr]);

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
  const [triggerMsg, setTriggerMsg] = useState<{ kind: 'starting' | 'active'; id?: string | null } | null>(null);
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
      setTriggerMsg({ kind: 'active', id: tid });
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
    if (state && state.running === false && activeTaskId && !stopNotice?.issues.length) {
      try { localStorage.removeItem(ACTIVE_RUN_KEY); } catch {}
      setActiveTaskId(null);
    }
  }, [state?.running, activeTaskId, stopNotice]);

  /* ── costi e buchi ──────────────────────────────────────────────────── */
  const total = state?.usage_total;
  const notes = costNotes(total, state?.usage_by_specialist);
  const hasTotal = total?.cost_eur != null;
  const partial = hasTotal && isPartial(total);
  const koIds = total?.error_agents || [];
  const koCost = koIds.reduce((s, id) => s + (state?.usage_by_specialist?.[id]?.cost_eur || 0), 0);
  const sumAgents = Object.values(state?.usage_by_specialist || {}).reduce((s, u) => s + (u.cost_eur || 0), 0);
  const nDesk = P.desks.length || agents.filter(a => a.id !== 'capo').length;
  const tutti = [...P.desks, ...P.pending, ...P.stages];
  const lavoro = tutti.reduce((s, d) => s + (d.dur || 0), 0);
  /* il numero esiste SOLO quando e' calcolabile (N2): a run viva la somma delle
     durate e' un minimo che cala con l'orologio, non un parallelismo */
  const par = P.parStato === 'calcolato' && P.runSec > 0 && lavoro > 0 ? lavoro / P.runSec : null;

  const cur = cursor ?? 0;
  const attivi = P.windows.filter(w => cur >= w.t0 && cur <= w.t1);
  const fatte = P.calls.filter(c => c.t <= cur).length;
  const fase = P.phases.find(p => cur >= p.t0 && cur <= p.t1);

  /* ── chi lavora al minuto puntato (F45, blocco A) ─────────────────────────
     Tre fonti, tutte dichiarate: le finestre (chiamate misurate, allungate al
     presente se il desk e' `running`), i desk `running` senza finestra in
     questo round, e il Capo (specialist_status.capo). Prima c'erano solo le
     finestre: «nessun desk al lavoro» per la maggior parte della run (N1) e il
     Capo che scriveva il memo invisibile (N10). */
  /* la lancetta e' sul presente (a run viva il cursore ha un tetto: runSec) */
  const alPresente = P.live && Math.abs(cur - P.runSec) <= 1.5;
  const byId = (id: string) => tutti.find(x => x.id === id);
  const righeDesk = attivi.map(w => {
    const past = P.calls.filter(c => c.a === w.a && c.t <= cur);
    const last = past[past.length - 1];
    const ragiona = !!w.open && w.tCall != null && cur > w.tCall;
    return { w, d: byId(w.a) as Desk, last, ragiona, da: ragiona ? cur - (w.tCall as number) : 0 };
  }).filter(r => r.d);
  /* i desk dichiarati in corsa SENZA finestra in questo round sono un fatto del
     PRESENTE (specialist_status) e restano scritti anche a cursore inchiodato
     nel passato — la riga dice «ora», cosi' non si confonde col minuto puntato
     (review F45: inchiodare faceva sparire il desk e scrivere «attesa») */
  const senzaFinestra: Desk[] = P.live
    ? P.running.filter(id => !attivi.some(w => w.a === id)).map(byId).filter((d): d is Desk => !!d)
    : [];
  /* il Capo: dichiarato `running` -> si vede; se manca l'orario di partenza
     (capoT) la durata e' n.d. dichiarato, non un Capo invisibile */
  const capoScrive = P.live && P.capo === 'running' && (P.capoT == null || cur >= P.capoT);
  const capoDesk = byId('capo');
  const capoCol = capoDesk?.color ?? '#5A6685';     // lo stesso ripiego di derivePlancia (fuori roster)
  const nDeskAlLavoro = righeDesk.length + senzaFinestra.length;
  const nRighe = nDeskAlLavoro + (capoScrive ? 1 : 0);
  const statoLettura = !P.live ? 'idle'
    : capoScrive ? 'capo'
    : (righeDesk.some(r => r.ragiona) || senzaFinestra.length) ? 'ragionano'
    : nRighe ? 'chiamate' : 'attesa';
  const faseLabel = capoScrive ? tr('activity.synthesisHead')
    : fase ? phaseText(fase.k) + (fase.open ? tr('activity.progressSuffix') : '')
    : (P.live && P.round != null ? `R${P.round}` : tr('activity.waiting'));
  /* la piastra vuota dice cosa c'e', non una causa inventata: nella coda di una
     run chiusa e' la catena di sintesi; prima della prima chiamata e' l'avvio;
     fra due fasi misurate e' il buco fra le due; altrimenti solo il fatto */
  const primaChiamata = P.calls.length ? P.calls[0].t : 0;
  const faseprima = [...P.phases].reverse().find(p => p.t1 < cur);
  const fasedopo = P.phases.find(p => p.t0 > cur);
  const fraseVuota = fase?.k === 'SINTESI'
    ? tr('activity.synthesisNoTools')
    : cur < primaChiamata ? tr('activity.startingNoTools')
    : faseprima && fasedopo ? tr('activity.betweenPhases', {a: phaseText(faseprima.k), b: phaseText(fasedopo.k)})
    : P.live && alPresente ? tr('activity.noneRunning')
    : tr('activity.noToolAtMinute');
  /* heartbeat fermo (>600 s, stale_warning del backend): «ragiona da X» e «scrive
     da X» sarebbero inferenze da uno stato che nessuno conferma piu' — si scrive
     il fatto: dichiarato, e fermo da quanto.
     review 31/08: a heartbeat ILLEGGIBILE persistente il backend non puo' piu'
     consegnare stale_warning (risponde la forma read-error, che la guardia N7
     scarta): oltre la STESSA soglia (600 s, bellomberg_api) il fermo lo misura
     il client, con la stessa grandezza (now - updated_at) — altrimenti «ragiona
     da X» crescerebbe per sempre su uno stato che nessuno conferma. */
  const hbIllDur = hbIll ? Math.max(0, (hbIll.al - hbIll.da) / 1000) : null;
  const hbIllLungo = hbIllDur != null && hbIllDur > 600;
  const fermoDa = (P.stale || hbIllLungo) && state?.updated_at
    ? Math.max(0, (now - new Date(state.updated_at).getTime()) / 1000) : null;
  const hhmm = (iso?: string) => iso ? new Date(iso).toLocaleTimeString(localeDi(linguaCorrente()),
    { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : tr('activity.unavailable');

  /* le griglie dense ricevono il conteggio righe: con grid-auto-flow:column
     una riga in meno significa una COLONNA in piu' fuori dal box, cioe' un
     troncamento zitto (agents-plancia.css §10 e §12) */
  const vars = {
    ['--f4attr2' as any]: Math.max(1, Math.ceil(P.tools.length / 2)),
    ['--f4attr1' as any]: Math.max(1, P.tools.length),
    ['--f4logr' as any]: Math.max(1, Math.ceil(P.calls.length / 3)),
    /* quante righe ha la ripartizione per agente di ECONOMIA: agli schermi
       larghi-e-bassi il pannello vive in fascia bassa e la riga della griglia
       deve conoscere IL SUO appetito, non solo quello degli strumenti — a run
       viva con pochi tool la riga si accorciava e amputava il pannello
       (misurato dal cancello: 4px visibili su 66) */
    ['--f4peco' as any]: Math.max(1, tutti.length),
  } as React.CSSProperties;

  return (
    /* `obsx` porta il vestito comune (.p3 .p3h .tick .chip .ld .tb): senza,
       i pannelli non hanno ne' cornice ne' testata e le due meta' dell'header
       finiscono una sull'altra. Stessa firma di F5 (.obsx .f5p) e F3 (.obsx .f3d). */
    <div className="obsx f4p animate-fadeIn" style={vars}>

      {/* ══ BARRA DI ASSETTO ══════════════════════════════════════════ */}
      <div className="asst">
        <span className="tick tl" /><span className="tick tr" />
        <span className="tick bl" /><span className="tick br" />

        <div className="id">
          <Radio size={12} className={isRunning ? 'okc' : 'dim'} />
          <span className="t">{tr('activity.pageTitle')}</span>
          <span className={'chip ' + (isRunning ? 'g' : 'n')}>
            {/* a state null con heartbeat illeggibile, «IDLE — NESSUNA RUN IN
               CORSO» sarebbe un'affermazione senza alcun payload fidato: una
               run puo' essere viva dietro il file illeggibile (review 31/08) */}
            {!state ? tr(hbIll ? 'activity.stateUnreadable' : 'activity.stateMissing')
              : isRunning ? tr('activity.runInProgress') : tr('activity.idle')}
          </span>
        </div>

        {liveErr ? (
          <div className="cell"><span className="k">heartbeat</span>
            <span className="v sm ko">{tr('activity.heartbeatNoResponse')}</span>
            <span className="s">{liveErr}</span></div>
        ) : !state ? (
          <div className="cell"><span className="k">heartbeat</span>
            {hbIll
              ? <><span className="v sm ko">{tr('activity.unreadableFromFirstPoll')}</span>
                  <span className="s">{tr('activity.endpointRespondsFileUnreadable')}</span></>
              : <span className="v sm nd">{tr('activity.querying')}</span>}</div>
        ) : (
          <>
            <div className="cell">
              <span className="k">{isRunning ? tr('activity.currentCommitteeRun') : tr('activity.lastCommitteeRun')}</span>
              <span className="v sm">
                {state.start_time
                  ? new Date(state.start_time).toLocaleDateString(localeDi(linguaCorrente()),
                      { day: '2-digit', month: 'short', year: 'numeric' }).toUpperCase()
                  : <span className="nd">{tr('activity.unavailable')}</span>}
                <span className="dim"> · </span>{hhmm(state.start_time)}
                <span className="dim"> → </span>{isRunning ? tr('activity.inProgress') : hhmm(state.completed_at)}
              </span>
              <span className="s">{tr(state.language === 'it' ? 'activity.originalRunIt'
                : state.language === 'en' ? 'activity.originalRunEn' : 'activity.originalRunUnknown')}</span>
            </div>
            <div className="cell">
              <span className="k">{tr('activity.duration')}</span>
              <span className="v num">{fmtDurShort(isRunning ? elapsed : P.runSec)}</span>
              <span className="s">
                {lavoro > 0
                  ? tr('activity.workVsClock', {a: fmtDurShort(lavoro), b: fmtDurShort(isRunning ? elapsed : P.runSec)})
                  : tr('activity.durationMissing')}
              </span>
            </div>
            <div className="cell">
              <span className="k">{tr('activity.tools')}</span>
              {/* review 31/08: a tappato senza totale (o con un totale sotto le
                 righe del log: contratto violato, P.nCallsTot lo scarta) il
                 numero grande e' un n.d. dichiarato, non calls.length spacciato */}
              <span className="v num">{P.logTappato && P.nCallsTot == null
                ? <span className="nd">{tr('activity.unavailable')}</span>
                : (P.nCallsTot ?? P.calls.length)}</span>
              {/* a log tappato la riga si RISCRIVE corta invece di allungarsi:
                  la versione lunga faceva collidere tre celle della testata
                  (misurato dal cancello: 3 sovrapposizioni a terzo) */}
              <span className="s">{P.logTappato
                ? <>{P.nToolsDistinct} {tr('activity.toolsRecentPrefix')} {P.tickers.length} {tr('activity.tickersRecentPrefix')} {P.calls.length} {tr('activity.received')}</>
                : <>{P.nToolsDistinct} {tr('activity.distinctToolsPrefix')} {P.tickers.length} {tr('activity.tickersTouchedShort')}</>}</span>
            </div>
            <div className="cell">
              <span className="k">{tr('activity.tokensTouched')}</span>
              <span className="v num">
                {total ? fmtTok([total.in, total.out, total.cache_read, total.cache_write].every(v => v != null)
                  ? total.in! + total.out! + total.cache_read! + total.cache_write! : null)
                       : <span className="nd">{tr('activity.unavailable')}</span>}
              </span>
              <span className="s">
                {total?.cache_read && total?.in
                  ? tr('activity.cacheMultiple', {a: fmtTok(total.cache_read), b: fmtN(total.cache_read / total.in, 1)})
                  : tr('activity.cacheMissing')}
              </span>
            </div>
            <div className="cell">
              <span className="k">{tr('activity.runCost')}</span>
              <span className={'v num ' + (!hasTotal || partial || notes.length ? 'amc' : 'cyc')}>
                {(partial ? '~' : '') + fmtEur(total?.cost_eur)}
              </span>
              <span className="s">
                {/* campo assente e 'n.d.' dichiarato dal backend sono lo STESSO buco (v. costNotes) */}
                FX USD/EUR <b className={total?.fx_source === 'live' ? 'okc' : 'amc'}>{total?.fx_source == null || total.fx_source === 'n.d.' ? tr('activity.unavailable') : total.fx_source}</b>
                {' · '}{!hasTotal ? tr('activity.totalCostMissing') : partial ? tr('activity.partialTotalShort') : tr('activity.notPartialTotal')}
              </span>
            </div>
            <div className="cell qcell">
              <span className="k">{tr('activity.memoProduced')}</span>
              <span className="v num">{state.memo_id != null ? '#' + state.memo_id : <span className="nd">{tr('activity.unavailable')}</span>}</span>
              <span className="s">{state.memo_id != null ? tr('activity.savedDb') : tr('activity.noMemo')}</span>
            </div>
          </>
        )}

        <div className="grow" />
        {isRunning ? (
          <button className="go stop" onClick={stopRun} disabled={stopping}
            title={activeTaskId ? tr('activity.stopTask', {a: activeTaskId}) : tr('activity.noTrackedTask')}>
            <Square size={10} fill="currentColor" />{stopping ? tr('activity.stopping') : tr('activity.stopRun')}
          </button>
        ) : (
          <button className="go" onClick={() => setAskRun(true)}><Play size={11} /> {tr('activity.launchRun')}</button>
        )}
        <RunConfirmDialog open={askRun}
          onConfirm={() => { setAskRun(false); triggerRun(); }}
          onCancel={() => setAskRun(false)} />
      </div>

      {/* ══ I BUCHI IN VETRINA, non nei tooltip (regola PM 14/07) ═════ */}
      {triggerMsg && <div className="warn"><span>{triggerMsg.kind === 'starting' ? tr('activity.starting')
        : tr('activity.runActiveEstimate', { a: triggerMsg.id ?? tr('activity.unavailable') })}</span></div>}
      {stopNotice && <div className="warn" role="status">
        <b>{tr(stopNotice.running === false ? 'activity.stopObservedIdle' : 'activity.stopUnconfirmed')}</b>
        {stopNotice.issues.map((issue, i) => <span key={i}>{issue.source} — {issue.detail || tr('activity.errorNotDescribed')}</span>)}
      </div>}
      {isRunning && (state?.stale_warning || hbIllLungo) && (
        <div className="warn ko"><AlertCircle size={11} />
          <b>{tr('activity.possiblyStuck')}</b>
          {/* review 31/08: a poll illeggibile `stale_seconds` resta CONGELATO
             all'ultimo payload buono mentre l'orologio avanza — vince la misura
             che cresce (now - updated_at), la stessa che il backend farebbe */}
          <span>{tr('activity.heartbeatStaleFor')} {Math.max(1, Math.floor(Math.max(state?.stale_seconds || 0, fermoDa || 0) / 60))} {tr('activity.stuckAction')}</span>
          {/* mentre il Capo genera il memo nessuno scrive l'heartbeat: il 26/08 e' stato
              fermo 7'20" a run sana. Un consiglio di FERMARE senza dirlo costerebbe il memo. */}
          {P.capo === 'running' && (
            <span>{tr('activity.capoHeartbeatNote')}</span>
          )}
        </div>
      )}
      {hbIll && (
        /* N7: il buco in vetrina, con la DURATA, e senza affermare uno «stato
           buono» che a state null non e' mai esistito (review 31/08) */
        <div className="warn"><AlertCircle size={11} />
          <b>{tr('activity.heartbeatUnreadableUpper')}{hbIllDur != null && hbIllDur >= 3 ? tr('activity.unreadableSince', {a: fmtDurShort(hbIllDur)}) : tr('activity.unreadableNow')}</b>
          <span>{tr('activity.unreadableMessagePrefix')}{hbIll.msg ?? tr('activity.unreadableHeartbeat')}{tr('activity.unreadableMessageSuffix')}{state
            ? <>{tr('activity.lastGoodState')}{state.updated_at ? tr('activity.heartbeatTime', {a: hhmm(state.updated_at)}) : ''}</>
            : <>{tr('activity.noneSinceOpen')}</>} {tr('activity.retryCadence')}</span>
        </div>
      )}
      {koIds.length > 0 && (
        <div className="warn">
          <span className="ld r" />
          <b>{tr(koIds.length === 1 ? 'activity.desksApiFailedOne' : 'activity.desksApiFailedMany', {a: koIds.length, b: nDesk})}</b>
          <span className="dim">·</span>
          <span>{koIds.join(tr('movements.and'))} {tr(koIds.length === 1 ? 'activity.declareOne' : 'activity.declare')} <b>status api_error</b> {tr(koIds.length === 1 ? 'activity.reportAnywayOne' : 'activity.reportAnyway')}</span>
          <span className="dim">·</span>
          <span>{tr('activity.withinTotalPrefix')} {fmtEur(total?.cost_eur)} {tr('activity.include')} <b>{fmtEur(koCost)}</b>
            {total?.cost_eur ? ` (${fmtN(koCost / total.cost_eur * 100, 0)}%)` : ''} {tr(koIds.length === 1 ? 'activity.spentByThemOne' : 'activity.spentByThem')}</span>
          <span className="dim">{tr('activity.errorAgentsField')}</span>
        </div>
      )}
      {notes.length > 0 && <div className="warn"><AlertCircle size={11} /><span>{notes.join(' — ')}</span></div>}
      {rosterErr && (
        <div className="warn ko"><AlertCircle size={11} />
          <b>{tr('activity.rosterMissing')}</b>
          <span>{tr('activity.rosterErrorPrefix')}{rosterErr}{tr('activity.rosterErrorSuffix')}</span>
        </div>
      )}

      {/* ══ LA PLANCIA ════════════════════════════════════════════════ */}
      <div className="plancia">

        {/* ── lo strumento ───────────────────────────────────────────── */}
        <div className="p3 strum iw">
            <span className="tick tl" /><span className="tick tr" />
            <span className="tick bl" /><span className="tick br" />
            <div className="p3h am">
              <span>{tr('activity.orbitalDashboard')}</span>
              <span className="side">
                {P.ok ? tr('activity.dialNote', {a: P.scaleNote, b: P.desks.length, c: P.logTappato ? tr('activity.fromLog') : ''})
                      : tr('activity.noDial')}
              </span>
              <div className="instbar">
                <span className="k">{tr('activity.cursor')}</span>
                <button className="tb" onClick={() => setPinned(v => !v)}>
                  {pinned ? tr('activity.pinned') : tr('activity.followMouse')}
                </button>
                <span className="sep" />
                <span className="k">{fmtClock(cur)}</span>
              </div>
            </div>
            <div className="p3b">
              <div className="skywrap"><SkyCanvas /></div>
              <div className="scanline" />
              <div className={'inst' + (isRunning ? ' live' : '')} ref={dialRef}>
                {P.ok ? (
                  <Quadrante p={P} w={dialBox.w} h={dialBox.h} cursor={cur} pinned={pinned}
                    onCursor={setCursor} onPin={() => setPinned(v => !v)}
                    koIds={koIds} fmtEur={fmtEur}
                    costoRun={total?.cost_eur} koCost={koCost}
                    /* `top:12px` della piastra (agents-plancia.css) + la sua
                       altezza misurata, nella stessa origine su cui disegna
                       l'SVG. ⚠️ `clientHeight` NON comprende i due bordi da 1px:
                       il fondo vero e' 2px piu' in basso, e l'aria di 10px sotto
                       li assorbe. 0 finche' non e' stata misurata. */
                    readBottom={readH ? 12 + readH : 0}
                    memoLabel={state?.memo_id != null ? `memo #${state.memo_id}` : tr('activity.noMemo')} />
                ) : (
                  <div className="buco ko">
                    <span className="t">{tr('activity.undrawableDial')}</span>
                    {/* review 31/08: con hbIll e state null, P.reason direbbe
                       «/agents/live non risponde» — ma l'endpoint HA risposto */}
                    <span className="s">{!state && hbIll
                      ? tr('activity.noReadableState')
                      : P.reason || tr('activity.insufficientData')}</span>
                    <span className="s dim">
                      {tr('activity.noFakeDial')}
                    </span>
                  </div>
                )}
              </div>
              <div className="glassx" />
              <div className="hudframe"><i className="tl" /><i className="tr" /><i className="bl" /><i className="br" /></div>

              {P.ok && (
                <>
                  {/* lettura: chi lavorava all'istante puntato */}
                  <div className="readout hudplate" ref={setReadEl}
                    data-zona="lettura" data-stato={statoLettura} data-capo={P.capo ?? 'assente'}
                    data-n={nRighe} data-heartbeat={P.stale ? 'fermo' : 'ok'}>
                    <div className="rh">
                      <span>{tr('activity.minute')} {fmtClock(cur)}</span>
                      <span>{faseLabel}</span>
                      {/* ALLE ALTEZZE CORTE il piede della piastra sparisce (v. la
                          media query in agents-plancia.css), perche' altrimenti la
                          piastra copre il box OPTIONS FLOW: misurato 198x54px, il
                          100% di quel box. Ma il piede porta un dato che non sta
                          in nessun altro punto della pagina — quante chiamate
                          erano FATTE al minuto puntato — e un dato non si
                          cancella per far passare un collaudo. Quindi risale qui,
                          in forma compatta, e si vede SOLO quando il piede non
                          c'e': il css lo tiene nascosto sopra i 1000px, dove il
                          piede lo dice per esteso. Nessuna duplicazione a schermo. */}
                      <span className="corta">{fatte}/{P.calls.length}{P.logTappato ? tr('activity.receivedAbbrev') : ''}</span>
                      <span className="pin">{pinned ? tr('activity.pinned') : tr('activity.clickPin')}</span>
                    </div>
                    <div className="rb">
                      {nRighe === 0 ? (
                        <div className="rr nd">{fraseVuota}</div>
                      ) : (
                        <>
                          {righeDesk.map(({ w, d, last, ragiona, da }) => (
                            <div className={'rr' + (ragiona ? ' rag' : '')} key={w.a}>
                              <svg width={11} height={11} viewBox="-7 -7 14 14"><Sigil id={d.id} color={d.color} size={11} /></svg>
                              <span className="nm" style={{ color: d.color }}>{d.name.toUpperCase()}</span>
                              <span className={'st ' + (ragiona ? (P.stale ? 'ko' : 'amc') : 'cyc')}
                                title={ragiona ? tr('activity.thinkingAfterTool', {a: fmtDurShort(da), b: last ? last.tool : '—'}) : undefined}>
                                {ragiona
                                  ? (fermoDa != null
                                      ? tr('activity.runningStale', {a: fmtDurShort(fermoDa)})
                                      : tr('activity.thinkingLast', {a: fmtDurShort(da), b: last ? last.tool : '—'}))
                                  : (last ? last.tool : '—')}
                              </span>
                            </div>
                          ))}
                          {senzaFinestra.map(d => (
                            <div className="rr rag" key={d.id}>
                              <svg width={11} height={11} viewBox="-7 -7 14 14"><Sigil id={d.id} color={d.color} size={11} /></svg>
                              <span className="nm" style={{ color: d.color }}>{d.name.toUpperCase()}</span>
                              {/* UN solo nodo di testo, corto: la cella `.st` e' a ellissi e un
                                  secondo nodo finiva tagliato fuori vista (misurato: 1,6:1) */}
                              {/* «nel log»: a run viva il log ricevuto sono le ultime 50 righe (N8),
                                  quindi lo zero non e' una misura e non si scrive come tale */}
                              <span className={'st ' + (P.stale ? 'ko' : 'amc')}
                                title={tr('activity.runningNoLog', {a: P.round != null ? tr('activity.inRound', {a: P.round}) : ''})}>
                                {fermoDa != null
                                  ? tr('activity.runningStale', {a: fmtDurShort(fermoDa)})
                                  : tr('activity.noCallsRound', {a: alPresente ? tr('activity.thinking') : tr('activity.runningNow'), b: P.round != null ? tr('activity.inRound', {a: P.round}) : ''})}
                              </span>
                            </div>
                          ))}
                          {capoScrive && (
                            <div className="rr capo">
                              <svg width={11} height={11} viewBox="-7 -7 14 14"><Sigil id="capo" color={capoCol} size={11} /></svg>
                              <span className="nm" style={{ color: capoCol }}>CAPO</span>
                              <span className={'st ' + (P.stale ? 'ko' : 'amc')}
                                title={P.capoT != null
                                  ? tr('activity.capoStart', {a: hhmm(state?.updated_at)})
                                  : tr('activity.capoStartMissing')}>
                                {fermoDa != null
                                  ? tr('activity.writingStale', {a: fmtDurShort(fermoDa)})
                                  : tr('activity.writingMemoSince', {a: state?.memo_id != null ? ` #${state.memo_id}` : '', b: P.capoT != null ? fmtDurShort(Math.max(0, Math.min(cur, P.runSec) - P.capoT)) : tr('activity.updatedAtMissing')})}
                              </span>
                            </div>
                          )}
                        </>
                      )}
                      <div className="ft">
                        <b>{fatte}</b> {tr(fatte === 1 ? 'activity.callsDoneOutOfOne' : 'activity.callsDoneOutOf')} {P.calls.length}
                        {/* il tappo lo DICE il backend (tool_log_tappato); la
                            disuguaglianza copre un backend NUOVO col flag perso.
                            Il backend VECCHIO non dichiara nulla (review 31/08):
                            a run viva col log esattamente al tetto (50) il
                            totale e' ignoto e si dichiara il dubbio, non «100%» */}
                        {(P.logTappato || (P.nCallsTot != null && P.nCallsTot > P.calls.length))
                          ? <> {tr('activity.receivedLatest')}{P.nCallsTot != null ? tr('activity.ofTotal', {a: P.nCallsTot}) : tr('activity.unknownTotal')})</>
                          : P.live && P.nCallsTot == null && P.calls.length === 50
                          ? <> {tr('activity.logCapUnknown')}</>
                          : (P.calls.length > 0 ? ` (${fmtN(fatte / P.calls.length * 100, 0)}%)` : '')}
                        {' · '}<b>{nDeskAlLavoro}</b> {tr(nDeskAlLavoro === 1 ? 'activity.desksWorkingOne' : 'activity.desksWorking')}
                        {capoScrive && P.capoT != null && <> {tr('activity.capoSinceHeartbeat')} {hhmm(state?.updated_at)}</>}
                        {fase && <> {tr('activity.phasePrefix')} <b>{phaseText(fase.k)}</b> {fmtClock(fase.t0)}→{fase.open ? tr('activity.inProgress') : fmtClock(fase.t1)}</>}
                      </div>
                    </div>
                  </div>

                  {/* il fatto della run, in vetrina */}
                  <div className="par hudplate" data-zona="parallelismo" data-par={P.parStato}>
                    <div className="k">{tr('activity.parallelismFact')}</div>
                    {P.parStato === 'parziale' ? (
                      /* N2: a run viva la somma delle durate e' un minimo che cala con
                         l'orologio (0,68x -> 0,44x col solo passare del tempo): non e'
                         un parallelismo, e la piastra lo dice con i numeri */
                      <div className="s nd">
                        {tr('activity.notCalculable')} {P.durDeclared < P.agentsKnown
                          ? <><b>{P.durDeclared} {tr('activity.agentsOutOf')} {P.agentsKnown}</b> {tr('activity.durationDeclared')}</>
                          : <><b>{P.agentsKnown}</b> {tr('activity.agentsWithDuration')} {P.running.length + (P.capo === 'running' ? 1 : 0)} {tr('activity.stillRunningPartial')}</>}
                        {P.live
                          ? <>{' — '}{tr('activity.calculateWhenFinished')} {fmtDurShort(lavoro)} {tr('activity.workIn')} {fmtDurShort(P.runSec)} {tr('activity.elapsedTime')}</>
                          : <>{' — '}{tr('activity.closedMissingDuration')}{fmtDurShort(lavoro)} {tr('activity.declaredWorkIn')} {fmtDurShort(P.runSec)})</>}
                      </div>
                    ) : par == null ? (
                      <div className="s nd">{tr('activity.durationMissingParallelism')}</div>
                    ) : (
                      <>
                        <div className="v"><b>{fmtN(par, 2)}×</b>
                          <span>{fmtDurShort(lavoro)} {tr('activity.agentWorkWithin')} {fmtDurShort(P.runSec)} {tr('activity.elapsedTime')}</span></div>
                        <div className="s">
                          {/* #1 (28/07): la frase era scritta senza condizione, e a 0,44x
                              diceva «supera l'orologio» — falso. Vera solo per par > 1. */}
                          {par > 1
                            ? <>{tr('activity.orbitsSubject')} <u>{tr('activity.overlap')}</u>{tr('activity.durationSumBy')}
                                {' '}{P.agentsKnown} {tr('activity.exceedsClock')}</>
                            : P.sovrapposte
                            ? <>{tr('activity.someOverlap')}
                                {' '}<u>{tr('activity.atTimes')}</u>{tr('activity.partlyTogether')}</>
                            : <>{tr('activity.noOverlapPrefix')} <u>{tr('activity.not')}</u> {tr('activity.sequential')}</>}
                        </div>
                      </>
                    )}
                  </div>

                  {/* ⚠️ LE DUE PIASTRE IN BASSO SONO STATE TOLTE — ordine PM 28/07:
                      «leviamo i due box in basso, tra cui quello che copre crypto,
                      ci sono scritte dentro cose inutili sul tempo e cosi' via».
                      Erano `.legend` (basso-sx, 5 righe di spiegazione dei simboli)
                      e `.scale` (basso-dx, cosa vale un giro/una tacca/il raggio).

                      MISURATO PRIMA DI TOGLIERLE, perche' una piastra che porta un
                      dato unico non si cancella: NESSUN numero e' andato perso.
                        · «260 chiamate» -> intestazione STRUMENTI (riga 317), piu'
                          le righe 464, 685, 777;
                        · «1 giro = 50'01"» -> intestazione DURATA, righe 480 e 555;
                        · «4 stadi di sintesi, durata si' orario no» -> righe 483,
                          554 e la tabella agenti (675);
                        · «coda SINTESI 40:19->50:01» -> riga 567 («dal minuto 40'
                          nessuna chiamata: gira la catena di sintesi») E l'etichetta
                          disegnata sul quadrante.
                      Tutto il resto era spiegazione dei simboli, non dato.

                      E il difetto che il PM ha visto e' MISURATO: a s150 (3413x960,
                      il suo schermo con Windows al 150%) il cancello trovava 27
                      scatole sovrapposte, con `div.scale.hudplate` sopra una piastra
                      del quadrante. A schermo intero erano 0: il difetto viveva solo
                      alla sua scala, ed e' la ragione per cui non era mai emerso. */}
                </>
              )}
            </div>
        </div>

        {/* Ogni pannello e' una CELLA della griglia, non un figlio di una
            colonna: cosi' ognuno puo' cambiare posto e misura a ogni
            inquadratura senza toccare il DOM, e nessuno deve scorrere
            dentro se stesso per far stare il contenuto (ordine PM). */}
        <PannelloParallelismo P={P} par={par} lavoro={lavoro} />
        <PannelloEconomia P={P} total={total} sumAgents={sumAgents} koCost={koCost} koIds={koIds} />
        <PannelloCifre P={P} engines={engines} koIds={koIds} total={total} />
        <PannelloStrumenti P={P} fatte={fatte} cur={cur} />
        <PannelloTicker P={P} />
        <PannelloNastro P={P} cur={cur} />
      </div>
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════════
   PANNELLI
   ══════════════════════════════════════════════════════════════════════ */

function PannelloParallelismo({ P, par, lavoro }: { P: Plancia; par: number | null; lavoro: number }) {
  const tr = useT();
  const bins = useMemo(() => {
    const M = Math.max(1, Math.ceil(P.runSec / 60));
    return Array.from({ length: M }, (_, m) => {
      const t = m * 60 + 30;
      return P.windows.filter(w => t >= w.t0 && t <= w.t1).length;
    });
  }, [P.runSec, P.windows]);
  const mx = Math.max(1, ...bins);
  const peak = bins.indexOf(mx);

  return (
    <div className="p3 ppar">
      <span className="tick tl" /><span className="tick br" />
      <div className="p3h"><span>{tr('activity.parallelism')}</span><span className="side">{tr('activity.desksPerMinute')}</span></div>
      <div className="p3b parw">
        {par == null ? (
          <div className="buco"><span className="t">{tr('activity.unavailable')}</span>
            <span className="s">
              {P.parStato === 'parziale'
                ? <>{P.durDeclared < P.agentsKnown
                      ? <><b>{P.durDeclared} {tr('activity.agentsOutOf')} {P.agentsKnown}</b> {tr('activity.durationDeclared')}</>
                      : <><b>{P.agentsKnown}</b> {tr('activity.agentsWithDuration')} {P.running.length + (P.capo === 'running' ? 1 : 0)} {tr('activity.stillRunningPartial')}</>}
                    {P.live
                      ? <>{tr('activity.parallelismWhenFinished')} <b>{fmtDurShort(lavoro)}</b> {tr('activity.workIn')} <b>{fmtDurShort(P.runSec)}</b> {tr('activity.elapsedTime')}</>
                      : <>{tr('activity.closedMissingDurations')} <b>{fmtDurShort(lavoro)}</b> {tr('activity.declaredWorkIn')} <b>{fmtDurShort(P.runSec)}</b></>}</>
                : <>{tr('activity.payloadMissing')} <b>duration_s</b> {tr('activity.durationPerAgentMissing')}</>}
            </span></div>
        ) : (
          <>
            <div className="bignum num">{fmtN(par, 2)}×</div>
            <div className="bigsub">
              <b>{fmtDurShort(lavoro)}</b> {tr('activity.workReportedBy')} {P.agentsKnown} {tr('activity.agentsWithin')} <b>{fmtDurShort(P.runSec)}</b> {tr('activity.elapsedTimeSentence')}
              {par > 1
                ? <> {tr('activity.overlapDial')}</>
                : P.sovrapposte
                ? <> {tr('activity.partialOverlapSentence')}</>
                : <> {tr('activity.sequentialSentence')}</>}
            </div>
            <div className="simplot">
              {bins.map((n, m) => (
                <u key={m} title={tr('activity.minuteDesks', {a: m, b: n})}
                  style={{ height: `${(n / mx * 100).toFixed(1)}%`, background: n === mx ? '#FFA51E' : undefined }} />
              ))}
            </div>
            <div className="simax"><span>0'</span><span>{Math.round(P.runSec / 60)}'</span></div>
            <div className="simfoot">
              {tr('activity.peak')} <b>{mx} {tr('activity.desksTogether')}</b> {tr('activity.atMinute')} {peak}'.
              {P.phases.some(p => p.k === 'SINTESI') && <> {tr('activity.fromMinute')} {Math.round(P.lastCallT / 60)}{tr('activity.synthesisFromMinute')} <b>{tr('activity.noTimestamps')}</b> {tr('activity.inPayload')}</>}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function PannelloEconomia({ P, total, sumAgents, koCost, koIds }: {
  P: Plancia; total?: UsageTotal; sumAgents: number; koCost: number; koIds: string[];
}) {
  const tr = useT();
  const rows: [string, number | null | undefined, string][] = [
    [tr('activity.freshInput'), total?.in, '#29D3F2'],
    [tr('activity.output'), total?.out, '#21E0A0'],
    [tr('activity.cacheRead'), total?.cache_read, '#FFA51E'],
    [tr('activity.cacheWrite'), total?.cache_write, '#9B7BFF'],
  ];
  const mx = Math.max(1, ...rows.map(r => r[1] || 0));
  const agenti = [...P.desks, ...P.pending, ...P.stages];
  const mxA = Math.max(1, ...agenti.map(a => (a.tin ?? 0) + (a.tout ?? 0) + (a.cacheR ?? 0) + (a.cacheW ?? 0)));

  return (
    <div className="p3 peco">
      <span className="tick tr" />
      <div className="p3h vi"><span>{tr('activity.economics')}</span><span className="side">token · cache · fx</span></div>
      <div className="p3b tokwrap">
        {!total ? (
          <div className="buco"><span className="t">{tr('activity.unavailable')}</span>
            <span className="s">{tr('activity.heartbeatMissing')} <b>usage_total</b>{tr('activity.runCostsMissing')}</span></div>
        ) : (
          <>
            {rows.map(([k, v, c]) => (
              <div className="tokrow" key={k}>
                <span className="k">{k}</span>
                <span className="bar"><u style={{ width: `${((v || 0) / mx * 100).toFixed(1)}%`, background: c }} /></span>
                <span className="v num" style={{ color: c }} title={v != null ? tr(v === 1 ? 'activity.tokenCountOne' : 'communications.tokenCount', {a: fmtN(v)}) : tr('activity.notDeclared')}>
                  {fmtTok(v)}</span>
              </div>
            ))}
            {agenti.length > 0 && (
              <div className="tkag">
                <div className="h"><span>{tr('activity.tokensPerAgent')}</span><span>{tr('activity.totalTouched')}</span></div>
                {agenti.map(a => {
                  const completo = [a.tin, a.tout, a.cacheR, a.cacheW].every(v => v != null);
                  const tot = completo ? a.tin! + a.tout! + a.cacheR! + a.cacheW! : null;
                  const seg: [number | null, string][] = [[a.tin, '#29D3F2'], [a.tout, '#21E0A0'],
                    [a.cacheR, '#FFA51E'], [a.cacheW, '#9B7BFF']];
                  return (
                    <div className="r" key={a.id} title={tr('activity.agentTokenBreakdown', {a: a.name, b: fmtTok(a.tin), c: fmtTok(a.tout), d: fmtTok(a.cacheR), e: fmtTok(a.cacheW), f: completo ? '' : tr('activity.partialTokens')})}>
                      <span className="k" style={{ color: a.color }}>{a.name.toUpperCase()}</span>
                      <span className="sb">
                        {seg.map(([v, c], i) => <u key={i} style={{ width: `${((v ?? 0) / mxA * 100).toFixed(1)}%`, background: c }} />)}
                      </span>
                      <span className="v num">{fmtTok(tot)}</span>
                    </div>
                  );
                })}
              </div>
            )}
            <div className="tokfoot">
              {total.cache_read && total.in
                ? <>{tr('activity.cacheReread')} <b>{fmtN(total.cache_read / total.in, 1)}×</b> {tr('activity.cacheBenefit')}</>
                : <>{tr('activity.cacheMissingSentence')}</>}
              <br />{tr('activity.agentSumTitle')} <b>{fmtEur(sumAgents)}</b>
              {total.cost_eur != null && Math.abs(sumAgents - total.cost_eur) < 0.005
                ? <>{tr('activity.sameAsTotal')}</>
                : <>{tr('activity.againstTotal')} <b>{fmtEur(total.cost_eur)}</b>: <span className="amc">{tr('activity.unexplainedDelta')}</span>.</>}
              {koIds.length > 0 && <><br /><span className="ko">{tr('activity.included')} {fmtEur(koCost)} {tr(koIds.length === 1 ? 'activity.spentByOne' : 'activity.spentBy')} {koIds.length} {tr(koIds.length === 1 ? 'activity.desksWithStatusOne' : 'activity.desksWithStatus')} <b>api_error</b>.</span></>}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function PannelloCifre({ P, engines, koIds, total }: {
  P: Plancia; engines?: EnginesInfo; koIds: string[]; total?: UsageTotal;
}) {
  const tr = useT();
  const riga = (d: Desk) => {
    const v = verdictOf(d);
    return (
      <div className="r" key={d.id} title={v.t} data-agente={d.id} data-esito={v.k}>
        <span className="n">
          <svg width={12} height={12} viewBox="-7 -7 14 14"><Sigil id={d.id} color={d.color} size={12} /></svg>
          <span style={{ color: d.color }}>{d.name.toUpperCase()}</span>
        </span>
        <i>{fmtDurShort(d.dur)}</i>
        <i>{d.nCalls || <span className="nd">—</span>}</i>
        <i>{d.nTools || <span className="nd">—</span>}</i>
        <i>{d.apiCalls || <span className="nd">—</span>}</i>
        <i className={koIds.includes(d.id) ? 'amc' : 'c'}>{fmtEur(d.cost)}</i>
        <span className={'v ' + v.cls}>{v.k === 'n.d.' ? tr('activity.unavailable') : v.k}</span>
      </div>
    );
  };
  const sommaTool = P.desks.reduce((s, d) => s + d.nTools, 0);
  return (
    <div className="p3 pcif">
      <span className="tick bl" />
      <div className="p3h"><span>{tr('activity.agentsFigures')}</span><span className="side">{tr('activity.singleVerdict')}</span></div>
      <div className="p3b cif">
        <div className="hd">
          {/* colonne fisse (agents-plancia.css §9): ogni etichetta, in ogni lingua, sta nella
              sua colonna piu' lo spazio che la segue — la prova e' in activity.test.cjs */}
          <span>{tr('activity.deskOrbit')}</span><i>{tr('activity.durationAbbrev')}</i><i>{tr('activity.callsAbbrev')}</i><i>{tr('activity.toolsAbbrev')}</i><i>api</i><i>{tr('activity.cost')}</i><i className="ce">{tr('activity.outcome')}</i>
        </div>
        {P.desks.map(d => riga(d))}
        {P.pending.length > 0 && (
          <>
            {/* un desk senza chiamate nel log NON e' uno stadio di sintesi (N8):
                o non e' ancora partito in questo round, o le sue chiamate sono
                uscite dalla finestra dell'heartbeat */}
            <div className="sep">{tr('activity.desksNoLog')} <b>{tr('activity.notOnDial')}</b></div>
            {P.pending.map(d => riga(d))}
          </>
        )}
        {/* il Capo in corsa ha l'ORARIO (l'heartbeat di partenza, nella piastra di
            lettura) ma non ancora la durata: non sta sotto «durata sì, orario no» */}
        {P.live && P.capo === 'running' && P.stages.some(s => s.id === 'capo') && (
          <>
            <div className="sep">{tr('activity.capoSection')} <b>{tr('activity.running')}</b>{tr('activity.capoDurationEnd')}</div>
            {P.stages.filter(s => s.id === 'capo').map(d => riga(d))}
          </>
        )}
        {P.stages.some(s => !(P.live && P.capo === 'running' && s.id === 'capo')) && (
          <>
            <div className="sep">{tr('activity.synthesisStages')} <b>{tr('activity.durationNoTime')}</b>{tr('activity.notOnDialSuffix')}</div>
            {P.stages.filter(s => !(P.live && P.capo === 'running' && s.id === 'capo')).map(d => riga(d))}
          </>
        )}
        {total?.cost_eur != null && (
          <div className="tot">
            <span className="n">{tr('activity.agentSum')}</span>
            <i>{fmtDurShort([...P.desks, ...P.pending, ...P.stages].reduce((s, d) => s + (d.dur || 0), 0))}</i>
            <i>{P.calls.length}</i><i>{P.nToolsDistinct}*</i>
            <i>{[...P.desks, ...P.pending, ...P.stages].reduce((s, d) => s + d.apiCalls, 0)}</i>
            <i className="c">{fmtEur(total.cost_eur)}</i>
            <span className={'v ' + (koIds.length ? 'ko' : 'okc')}>{koIds.length ? `${koIds.length} KO` : 'OK'}</span>
          </div>
        )}
        <div className="note">
          <b>{tr('activity.callsAbbrev')}</b> {tr('activity.callsDefinition')} <b>{tr('activity.toolsAbbrev')}</b> {tr('activity.toolsDefinition')} <b>{tr('activity.distinct')}</b> {tr('activity.ofDesk')}
          {P.logTappato && <> {tr('activity.countsOnLatest')} <b>{tr('activity.latest')} {P.calls.length}</b> {tr('activity.callsReceived')}{P.nCallsTot != null && <> {tr('activity.runMadeCalls')} {P.nCallsTot})</>}{tr('activity.olderCallsUnderCount')}</>}
          {P.nToolsDistinct > 0 && <> {tr(P.nToolsDistinct === 1 ? 'activity.toolsFootnoteOne' : 'activity.toolsFootnoteMany', {a: P.nToolsDistinct, b: sommaTool})}</>}
          <br />{tr('activity.committeeEngineFrom')} <b>engines</b>{tr('activity.chatModelIsSeparate')}
          {P.desks.length > 0 && <> desk <b>{engineShort(P.desks[0].id, engines, P.desks[0].rounds)}</b></>}
          {P.stages.some(s => s.id === 'capo') && <> {tr('activity.capoInline')} <b>{engineShort('capo', engines)}</b></>}
          {P.stages.some(s => s.id === '_reflection' || s.id === '_action_table') &&
            <> {tr('activity.reflectionAction')} <b>{tr('activity.unavailable')}</b>{tr('activity.notExposed')}</>}
        </div>
      </div>
    </div>
  );
}

function PannelloStrumenti({ P, fatte, cur }: { P: Plancia; fatte: number; cur: number }) {
  const tr = useT();
  const mx = P.tools[0]?.n || 1;
  return (
    <div className="p3 patt">
      <span className="tick tl" />
      <div className="p3h cy"><span>{tr('activity.committeeLooked')}</span>
        {/* «tutti e N» solo quando il log e' INTERO: a run viva sono le ultime 50 (N8) */}
        <span className="side">{P.logTappato
          ? tr('activity.countInCalls', {a: P.nToolsDistinct, b: P.calls.length})
          : tr('activity.allCount', {a: P.nToolsDistinct})}</span></div>
      <div className="p3b attwrap">
        {P.tools.length === 0 ? (
          <div className="buco"><span className="t">{tr('activity.noCalls')}</span>
            <span className="s">{tr('activity.theLog')} <b>tool_log</b> {tr('activity.heartbeatLogEmpty')}</span></div>
        ) : (
          <>
            <div className="atl">
              {P.desks.map(d => (
                <b key={d.id} style={{ color: d.color }}>
                  <svg width={10} height={10} viewBox="-7 -7 14 14"><Sigil id={d.id} color={d.color} size={10} /></svg>
                  {d.name.toUpperCase()}
                </b>
              ))}
              <span className="n">{tr('activity.toolBarExplanation')} <b>{tr('activity.shape')}</b>{tr('activity.notColour')}</span>
            </div>
            <div className="att">
              {P.tools.map(t => {
                const seg = Object.entries(t.by).sort((a, b) => b[1] - a[1]);
                const top = P.desks.find(x => x.id === seg[0]?.[0]);
                return (
                  <div className="r" key={t.tool} title={seg.map(([a, n]) => `${a} ${n}`).join(' · ')}>
                    <span className="t">{t.tool}</span>
                    <span className="c num">{t.n}</span>
                    <span className="m">
                      {top && <svg width={9} height={9} viewBox="-7 -7 14 14"><Sigil id={top.id} color={top.color} size={9} /></svg>}
                      {seg.map(([a, n]) => {
                        const d = P.desks.find(x => x.id === a);
                        return <u key={a} style={{ width: `${(n / mx * 70).toFixed(1)}px`, background: d?.color || '#5A6685' }} />;
                      })}
                    </span>
                  </div>
                );
              })}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function PannelloTicker({ P }: { P: Plancia }) {
  const tr = useT();
  return (
    <div className="p3 ptk">
      <span className="tick br" />
      <div className="p3h"><span>{tr('activity.tickersTouched')}</span>
        <span className="side">{P.logTappato
          ? tr('activity.countInCalls', {a: P.tickers.length, b: P.calls.length})
          : tr('activity.allCount', {a: P.tickers.length})}</span></div>
      <div className="p3b tkl">
        {P.tickers.length === 0 ? (
          <div className="buco"><span className="t">{tr('activity.noTicker')}</span>
            <span className="s">{tr('activity.theInputs')} <b>input</b> {tr('activity.inputsNoTickers')}</span></div>
        ) : P.tickers.map(x => <span key={x.k}>{x.k}<b>{x.n}</b></span>)}
      </div>
    </div>
  );
}

function PannelloNastro({ P, cur }: { P: Plancia; cur: number }) {
  const tr = useT();
  return (
    <div className="p3 plog">
      <span className="tick tr" />
      <div className="p3h cy"><span>{tr('activity.callsTape')}</span>
        <span className="side">{P.logTappato
          ? tr('activity.lastReceived', {a: P.calls.length, b: P.nCallsTot != null ? tr('activity.ofTotal', {a: P.nCallsTot}) : ''})
          : tr('activity.allCalls', {a: P.calls.length})}{tr('activity.chronological')}</span></div>
      <div className="p3b logwrap">
        {P.calls.length === 0 ? (
          <div className="buco"><span className="t">{tr('activity.emptyTape')}</span>
            <span className="s">{tr('activity.heartbeatNoCalls')}</span></div>
        ) : (
          <>
            <div className="lhd">
              {[0, 1, 2].map(i => (
                <div className="lr" key={i}>
                  <span>T+</span><span>desk</span><span>{tr('activity.roundAbbrev')}</span><span>{tr('activity.tool')}</span><span>input</span>
                </div>
              ))}
            </div>
            <div className="logw">
              {P.calls.map((c, i) => {
                const d = P.desks.find(x => x.id === c.a);
                return (
                  <div className={'lr' + (Math.abs(c.t - cur) < 20 ? ' hot' : '')} key={i}
                    title={tr('activity.wallClock', {a: c.hhmm, b: c.input})}>
                    <span className="tm num">{fmtClock(c.t)}</span>
                    <span className="ag" style={{ color: d?.color }}>{c.a.toUpperCase()}</span>
                    {/* la classe CSS non viene mai da un catalogo: .rd e' la regola di agents-plancia.css §12 */}
                    <span className="rd">R{c.r}</span>
                    <span className="tl">{c.tool}</span>
                    <span className="in" title={tr('activity.originalSource')}>{c.input === '{}' ? '—' : c.input.replace(/[{}']/g, '')}</span>
                  </div>
                );
              })}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
