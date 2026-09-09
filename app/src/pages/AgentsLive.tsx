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

/* ── formattatori: UNA sola convenzione in tutta la pagina (it-IT) ───────
   null/NaN => "n.d.": il dato NON e' calcolabile e va dichiarato. Uno
   "0,00 EUR" al posto di un buco e' il bug peggiore su un pannello costi
   (regola no-fallback-silenziosi): qui non deve MAI comparire. */
const fmtEur = (v: number | null | undefined) =>
  v == null || !isFinite(v) ? 'n.d.'
    : new Intl.NumberFormat('it-IT', { style: 'currency', currency: 'EUR',
        minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v);
const fmtN = (v: number | null | undefined, d = 0) =>
  v == null || !isFinite(v) ? 'n.d.'
    : new Intl.NumberFormat('it-IT', { minimumFractionDigits: d, maximumFractionDigits: d }).format(v);
const fmtTok = (v: number | null | undefined) =>
  v == null ? 'n.d.' : v >= 1000 ? fmtN(v / 1000, 1) + 'k' : fmtN(v);

/* status !== 'ok' = BUCO, esattamente come cost_eur null. status assente =
   heartbeat di una run vecchia -> nessun giudizio, in nessuno dei due sensi. */
const isHoleStatus = (s?: string | null) => s != null && s !== 'ok';
/* "e' andato KO" != "non so quanto e' costato": api_error/usage_unknown sono
   fallimenti dell'agente; model_unknown/pricing_unavailable sono solo costi
   non calcolabili su una chiamata riuscita. Il marchio KO va solo ai primi. */
const isErrorStatus = (s?: string | null) => s === 'api_error' || s === 'usage_unknown';

const STATUS_DESC: Record<string, string> = {
  api_error: "l'agente ha fallito la chiamata API",
  usage_unknown: 'la risposta API non ha esposto usage: token IGNOTI (diverso da zero)',
  model_unknown: 'modello non a listino: costo NON calcolabile',
  pricing_unavailable: 'listino prezzi non disponibile: costo NON calcolabile',
};

/* ── UN SOLO VERDETTO PER AGENTE, e vince il peggiore ────────────────────
   Prima la stessa card mostrava un badge verde "ok" (da specialist_status,
   che dice solo "ha consegnato il report") e un costo ambra (da
   usage.status = api_error, che dice "ha sbattuto contro l'API"). Sono due
   domande diverse, ma a schermo deve arrivare una risposta sola. */
type Verdict = { k: string; cls: string; t: string };
function verdictOf(d: Pick<Desk, 'statusRun' | 'statusUsage' | 'cost'>): Verdict {
  if (isErrorStatus(d.statusUsage))
    /* «ha consegnato il report» solo se e' done: un desk KO in un round prima
       puo' essere in corsa ORA (review F45), e allora si dicono i due fatti */
    return { k: 'KO', cls: 'ko',
      t: `${STATUS_DESC[d.statusUsage!] || 'errore dichiarato dal backend'}${
        d.statusRun === 'running' ? ` in un round precedente — in corsa ora, speso finora ${fmtEur(d.cost)}`
        : d.statusRun === 'done' ? ` — ha comunque consegnato il report e speso ${fmtEur(d.cost)}`
        : ` — speso ${fmtEur(d.cost)}`}` };
  if (isHoleStatus(d.statusUsage))
    return { k: 'n.d.', cls: 'amc',
      t: STATUS_DESC[d.statusUsage!] || `anomalia dichiarata dal backend (status ${d.statusUsage})` };
  if (d.statusRun === 'done') return { k: 'OK', cls: 'okc', t: 'report consegnato, nessun errore API dichiarato' };
  if (d.statusRun === 'running') return { k: 'RUN', cls: 'amc', t: 'in esecuzione' };
  if (d.statusRun === 'error') return { k: 'KO', cls: 'ko', t: 'errore dichiarato su specialist_status' };
  return { k: '—', cls: 'nd', t: 'nessuno stato dichiarato dal payload per questo agente' };
}

/* buchi da dichiarare sul totale. NB: unpriced_agents del backend mescola DUE
   casi (base.py: cost_eur is None OPPURE partial) — chi non ha alcun costo e
   chi ne ha uno incompleto. Vanno nominati separatamente, o la nota
   contraddice la cifra che le sta accanto. */
function costNotes(u?: UsageTotal | null, by?: Record<string, UsageBySpecialist> | null): string[] {
  if (!u) return [];
  const n: string[] = [];
  if (u.error) n.push('aggregazione costi FALLITA: ' + u.error);
  const named = u.unpriced_agents || [];
  const noCost = named.filter(a => by?.[a] && by[a].cost_eur == null);
  const onlyPartial = named.filter(a => by?.[a] && by[a].cost_eur != null);
  const unknownKind = named.filter(a => !by?.[a]);
  if (noCost.length) n.push('costo non calcolabile per: ' + noCost.join(', '));
  if (onlyPartial.length) n.push("costo PARZIALE (e' un MINIMO) per: " + onlyPartial.join(', '));
  if (unknownKind.length) n.push('costo mancante o parziale per: ' + unknownKind.join(', '));
  if (!named.length && u.partial === true) n.push("totale PARZIALE: almeno una voce non e' prezzabile");
  if (u.fx_source === 'fallback') n.push('FX USD/EUR da fallback, non live — costo indicativo');
  if (u.fx_source === 'n.d.') n.push('FX USD/EUR non disponibile');
  return n;
}
const isPartial = (u?: UsageTotal | null) =>
  u?.partial != null ? u.partial === true : !!u?.unpriced_agents?.length;

/* ══════════════════════════════════════════════════════════════════════ */
export default function AgentsLive() {
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
  const [hbIll, setHbIll] = useState<{ msg: string; da: number; al: number } | null>(null);
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
      .catch(e => setRosterErr(e?.response?.data?.detail || e?.message || 'errore sconosciuto'));
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
            const msg = s.message || 'heartbeat illeggibile';
            setHbIll(prev => ({ msg, da: prev?.da ?? Date.now(), al: Date.now() }));
          }
          else { setState(s); setHbIll(null); }
          setLiveErr(null);
        }
      }
      /* l'errore di RETE azzera hbIll: «l'ultimo poll ha risposto …» sarebbe
         falso sotto un poll che non ha risposto affatto (review 31/08); se al
         ritorno della rete il file e' ancora illeggibile, hbIll torna da solo */
      catch (e: any) { if (mounted) { setLiveErr(e?.response?.data?.detail || e?.message || 'errore sconosciuto'); setHbIll(null); } }
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

  const P = useMemo(() => derivePlancia(state, agents, engines, now), [state, agents, engines, now]);

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
  const [triggerMsg, setTriggerMsg] = useState<string | null>(null);
  const [askRun, setAskRun] = useState(false);   // Lotto D: la run costa, si conferma prima

  const triggerRun = async () => {
    setTriggerMsg('AVVIO PROCESSO CONSIGLIERE…');
    setState(prev => ({ ...(prev || {}), running: true,
      start_time: new Date().toISOString(), message: 'Avvio…' } as AgentsLiveState));
    try {
      const r = await Bellomberg.runConsigliere();
      const tid = r?.task_id || null;
      if (tid) { try { localStorage.setItem(ACTIVE_RUN_KEY, tid); } catch {} setActiveTaskId(tid); }
      setTriggerMsg(`RUN ${tid} ATTIVA — stimati 25-40 min — email a fine corsa`);
    } catch (e: any) {
      const detail = dettaglioLeggibile(e);
      setState(prev => prev ? { ...prev, running: false } : prev);
      setTriggerMsg(null);
      alert('Avvio fallito: ' + detail);
      if (statusHttp(e) === 428) { conservaDettaglioRun(detail); navigate('/mandato'); }
    }
  };

  const stopRun = async () => {
    if (!confirm('FERMARE il consigliere in corso? I risultati parziali vanno persi.')) return;
    setStopping(true);
    let tid = activeTaskId;
    if (!tid) {
      try { const a = await Bellomberg.consigliereActive(); if (a.active && a.task_id) tid = a.task_id; }
      catch (e) { console.warn('consigliereActive lookup failed', e); }
    }
    let killed = false;
    try {
      if (tid) { await Bellomberg.cancelConsigliere(tid); killed = true; }
      else { const r = await Bellomberg.cancelAllConsigliere(); killed = (r.n_killed || 0) > 0; }
    } catch (e) { console.error('cancel failed', e); }
    try { await Bellomberg.resetAgentsLive(); } catch (e) { console.warn('reset live failed', e); }
    try { localStorage.removeItem(ACTIVE_RUN_KEY); } catch {}
    setActiveTaskId(null); setTriggerMsg(null);
    /* lo stato che segue e' fabbricato QUI, non e' «l'ultimo stato buono» del
       backend: il warn N7 sopra di esso direbbe il falso (review 31/08) */
    setHbIll(null);
    setState(prev => prev ? { ...prev, running: false, specialist_status: {},
      /* col log azzerato anche il suo totale e il tappo (blocco D): un
         n_tool_calls stantio sulla testata direbbe N con zero chiamate sotto */
      current_specialist: null, tool_log: [], n_tool_calls: undefined, tool_log_tappato: undefined,
      completed_at: prev.completed_at || new Date().toISOString(),
      message: killed ? "Run annullata dall'operatore." : 'Annullamento inviato ma il backend non riportava nulla di attivo.' } : prev);
    setElapsed(0); setStopping(false);
  };

  useEffect(() => {
    if (state && state.running === false && activeTaskId) {
      try { localStorage.removeItem(ACTIVE_RUN_KEY); } catch {}
      setActiveTaskId(null);
    }
  }, [state?.running, activeTaskId]);

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
  const faseLabel = capoScrive ? 'SINTESI · CAPO'
    : fase ? fase.k + (fase.open ? ' · in corso' : '')
    : (P.live && P.round != null ? `R${P.round}` : 'attesa');
  /* la piastra vuota dice cosa c'e', non una causa inventata: nella coda di una
     run chiusa e' la catena di sintesi; prima della prima chiamata e' l'avvio;
     fra due fasi misurate e' il buco fra le due; altrimenti solo il fatto */
  const primaChiamata = P.calls.length ? P.calls[0].t : 0;
  const faseprima = [...P.phases].reverse().find(p => p.t1 < cur);
  const fasedopo = P.phases.find(p => p.t0 > cur);
  const fraseVuota = fase?.k === 'SINTESI'
    ? 'nessuna chiamata a strumento: gira la catena di sintesi — durata sì, orario no'
    : cur < primaChiamata ? 'run avviata: nessuna chiamata a strumento ancora'
    : faseprima && fasedopo ? `nessuna chiamata a strumento al minuto puntato — fra ${faseprima.k} e ${fasedopo.k}`
    : P.live && alPresente ? 'nessuna chiamata a strumento e nessun desk dichiarato in corsa dal backend'
    : 'nessuna chiamata a strumento al minuto puntato';
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
  const hhmm = (iso?: string) => iso ? new Date(iso).toLocaleTimeString('it-IT',
    { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : 'n.d.';

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
          <span className="t">// AGENTS LIVE</span>
          <span className={'chip ' + (isRunning ? 'g' : 'n')}>
            {/* a state null con heartbeat illeggibile, «IDLE — NESSUNA RUN IN
               CORSO» sarebbe un'affermazione senza alcun payload fidato: una
               run puo' essere viva dietro il file illeggibile (review 31/08) */}
            {!state && hbIll ? 'STATO N.D. — HEARTBEAT ILLEGGIBILE'
              : isRunning ? 'RUN IN CORSO' : 'IDLE — NESSUNA RUN IN CORSO'}
          </span>
        </div>

        {liveErr ? (
          <div className="cell"><span className="k">heartbeat</span>
            <span className="v sm ko">/agents/live NON risponde</span>
            <span className="s">{liveErr}</span></div>
        ) : !state ? (
          <div className="cell"><span className="k">heartbeat</span>
            {hbIll
              ? <><span className="v sm ko">illeggibile dal primo poll</span>
                  <span className="s">l'endpoint risponde, il file non si legge</span></>
              : <span className="v sm nd">interrogazione in corso…</span>}</div>
        ) : (
          <>
            <div className="cell">
              <span className="k">{isRunning ? 'run in corso · comitato' : 'ultima run · comitato'}</span>
              <span className="v sm">
                {state.start_time
                  ? new Date(state.start_time).toLocaleDateString('it-IT',
                      { day: '2-digit', month: 'short', year: 'numeric' }).toUpperCase()
                  : <span className="nd">n.d.</span>}
                <span className="dim"> · </span>{hhmm(state.start_time)}
                <span className="dim"> → </span>{isRunning ? 'in corso' : hhmm(state.completed_at)}
              </span>
            </div>
            <div className="cell">
              <span className="k">durata</span>
              <span className="v num">{fmtDurShort(isRunning ? elapsed : P.runSec)}</span>
              <span className="s">
                {lavoro > 0
                  ? `${fmtDurShort(lavoro)} di lavoro agente in ${fmtDurShort(isRunning ? elapsed : P.runSec)} di orologio`
                  : 'durate per agente non dichiarate dal payload'}
              </span>
            </div>
            <div className="cell">
              <span className="k">strumenti</span>
              {/* review 31/08: a tappato senza totale (o con un totale sotto le
                 righe del log: contratto violato, P.nCallsTot lo scarta) il
                 numero grande e' un n.d. dichiarato, non calls.length spacciato */}
              <span className="v num">{P.logTappato && P.nCallsTot == null
                ? <span className="nd">n.d.</span>
                : (P.nCallsTot ?? P.calls.length)}</span>
              {/* a log tappato la riga si RISCRIVE corta invece di allungarsi:
                  la versione lunga faceva collidere tre celle della testata
                  (misurato dal cancello: 3 sovrapposizioni a terzo) */}
              <span className="s">{P.logTappato
                ? <>{P.nToolsDistinct} strumenti · {P.tickers.length} ticker nelle ultime {P.calls.length} ricevute</>
                : <>{P.nToolsDistinct} strumenti distinti · {P.tickers.length} ticker toccati</>}</span>
            </div>
            <div className="cell">
              <span className="k">token toccati</span>
              <span className="v num">
                {total ? fmtTok([total.in, total.out, total.cache_read, total.cache_write].every(v => v != null)
                  ? total.in! + total.out! + total.cache_read! + total.cache_write! : null)
                       : <span className="nd">n.d.</span>}
              </span>
              <span className="s">
                {total?.cache_read && total?.in
                  ? `${fmtTok(total.cache_read)} riletti da cache = ${fmtN(total.cache_read / total.in, 1)}× l'input fresco`
                  : 'cache non dichiarata dal payload'}
              </span>
            </div>
            <div className="cell">
              <span className="k">costo run</span>
              <span className={'v num ' + (!hasTotal || partial || notes.length ? 'amc' : 'cyc')}>
                {(partial ? '~' : '') + fmtEur(total?.cost_eur)}
              </span>
              <span className="s">
                FX USD/EUR <b className={total?.fx_source === 'live' ? 'okc' : 'amc'}>{total?.fx_source ?? 'n.d.'}</b>
                {' · '}{partial ? 'totale PARZIALE' : 'totale non parziale'}
              </span>
            </div>
            <div className="cell qcell">
              <span className="k">memo prodotto</span>
              <span className="v num">{state.memo_id != null ? '#' + state.memo_id : <span className="nd">n.d.</span>}</span>
              <span className="s">{state.memo_id != null ? 'salvato in DB' : 'nessun memo dichiarato'}</span>
            </div>
          </>
        )}

        <div className="grow" />
        {isRunning ? (
          <button className="go stop" onClick={stopRun} disabled={stopping}
            title={activeTaskId ? `Ferma il task ${activeTaskId}` : 'Nessun task_id tracciato'}>
            <Square size={10} fill="currentColor" />{stopping ? 'FERMO…' : 'FERMA RUN'}
          </button>
        ) : (
          <button className="go" onClick={() => setAskRun(true)}><Play size={11} /> LANCIA RUN</button>
        )}
        <RunConfirmDialog open={askRun}
          onConfirm={() => { setAskRun(false); triggerRun(); }}
          onCancel={() => setAskRun(false)} />
      </div>

      {/* ══ I BUCHI IN VETRINA, non nei tooltip (regola PM 14/07) ═════ */}
      {triggerMsg && <div className="warn"><span>{triggerMsg}</span></div>}
      {isRunning && (state?.stale_warning || hbIllLungo) && (
        <div className="warn ko"><AlertCircle size={11} />
          <b>RUN POSSIBILMENTE INCAGLIATA</b>
          {/* review 31/08: a poll illeggibile `stale_seconds` resta CONGELATO
             all'ultimo payload buono mentre l'orologio avanza — vince la misura
             che cresce (now - updated_at), la stessa che il backend farebbe */}
          <span>heartbeat fermo da {Math.max(1, Math.floor(Math.max(state?.stale_seconds || 0, fermoDa || 0) / 60))} min — valuta FERMA RUN + regenerate_memo</span>
          {/* mentre il Capo genera il memo nessuno scrive l'heartbeat: il 26/08 e' stato
              fermo 7'20" a run sana. Un consiglio di FERMARE senza dirlo costerebbe il memo. */}
          {P.capo === 'running' && (
            <span>· il Capo è dichiarato in scrittura e mentre genera il memo l'heartbeat NON si aggiorna
              (il 26/08: 7'20" di silenzio a run sana) — un memo lungo non è un incaglio</span>
          )}
        </div>
      )}
      {hbIll && (
        /* N7: il buco in vetrina, con la DURATA, e senza affermare uno «stato
           buono» che a state null non e' mai esistito (review 31/08) */
        <div className="warn"><AlertCircle size={11} />
          <b>HEARTBEAT ILLEGGIBILE{hbIllDur != null && hbIllDur >= 3 ? ` DA ${fmtDurShort(hbIllDur)}` : ' IN QUESTO ISTANTE'}</b>
          <span>l'endpoint risponde ma il file heartbeat non si legge («{hbIll.msg}») — {state
            ? <>resta in pagina l'ultimo stato buono{state.updated_at ? ` (heartbeat delle ${hhmm(state.updated_at)})` : ''}</>
            : <>nessuno stato ricevuto dall'apertura della pagina</>} · si riprova ogni 1,5 s</span>
        </div>
      )}
      {koIds.length > 0 && (
        <div className="warn">
          <span className="ld r" />
          <b>{koIds.length} DESK SU {nDesk} HANNO SBATTUTO CONTRO L'API</b>
          <span className="dim">·</span>
          <span>{koIds.join(' e ')} dichiarano <b>status api_error</b> e hanno comunque consegnato il report</span>
          <span className="dim">·</span>
          <span>dentro i {fmtEur(total?.cost_eur)} ci sono <b>{fmtEur(koCost)}</b>
            {total?.cost_eur ? ` (${fmtN(koCost / total.cost_eur * 100, 0)}%)` : ''} spesi da loro</span>
          <span className="dim">· campo usage_total.error_agents</span>
        </div>
      )}
      {notes.length > 0 && <div className="warn"><AlertCircle size={11} /><span>{notes.join(' — ')}</span></div>}
      {rosterErr && (
        <div className="warn ko"><AlertCircle size={11} />
          <b>ROSTER AGENTI N.D.</b>
          <span>/agents/list non risponde ({rosterErr}): nomi, ruoli e colori dei desk non sono disponibili</span>
        </div>
      )}

      {/* ══ LA PLANCIA ════════════════════════════════════════════════ */}
      <div className="plancia">

        {/* ── lo strumento ───────────────────────────────────────────── */}
        <div className="p3 strum iw">
            <span className="tick tl" /><span className="tick tr" />
            <span className="tick bl" /><span className="tick br" />
            <div className="p3h am">
              <span>// PLANCIA ORBITALE</span>
              <span className="side">
                {P.ok ? `${P.scaleNote} · mezzogiorno = lo start · senso orario · ${P.desks.length} orbite${P.logTappato ? ' dal log ricevuto' : ''}`
                      : 'nessun quadrante da disegnare'}
              </span>
              <div className="instbar">
                <span className="k">cursore</span>
                <button className="tb" onClick={() => setPinned(v => !v)}>
                  {pinned ? '◆ INCHIODATO' : '○ SEGUE IL MOUSE'}
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
                    memoLabel={state?.memo_id != null ? `memo #${state.memo_id}` : 'nessun memo dichiarato'} />
                ) : (
                  <div className="buco ko">
                    <span className="t">quadrante non disegnabile</span>
                    {/* review 31/08: con hbIll e state null, P.reason direbbe
                       «/agents/live non risponde» — ma l'endpoint HA risposto */}
                    <span className="s">{!state && hbIll
                      ? "l'endpoint risponde ma l'heartbeat e' illeggibile (3 riletture fallite): nessuno stato ricevuto"
                      : P.reason || 'dati insufficienti'}</span>
                    <span className="s dim">
                      Non viene disegnato un quadrante finto: quando l'heartbeat porta l'origine
                      del tempo e almeno una chiamata a strumento, la plancia si accende da sola.
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
                      <span>minuto {fmtClock(cur)}</span>
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
                      <span className="corta">{fatte}/{P.calls.length}{P.logTappato ? ' ric.' : ''}</span>
                      <span className="pin">{pinned ? '◆ INCHIODATO' : 'CLICCA PER INCHIODARE'}</span>
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
                                title={ragiona ? `dichiarato running dal backend: ragiona da ${fmtDurShort(da)} dopo l'ultima chiamata (${last ? last.tool : '—'})` : undefined}>
                                {ragiona
                                  ? (fermoDa != null
                                      ? `dichiarato running · heartbeat fermo da ${fmtDurShort(fermoDa)}`
                                      : `ragiona da ${fmtDurShort(da)} · ultima ${last ? last.tool : '—'}`)
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
                                title={`dichiarato running dal backend ORA; nessuna chiamata sua${P.round != null ? ` in R${P.round}` : ''} nel log ricevuto (a run viva: le ultime 50 righe)`}>
                                {fermoDa != null
                                  ? `dichiarato running · heartbeat fermo da ${fmtDurShort(fermoDa)}`
                                  : `${alPresente ? 'ragiona' : 'in corsa ora'} · nessuna chiamata${P.round != null ? ` in R${P.round}` : ''} nel log`}
                              </span>
                            </div>
                          ))}
                          {capoScrive && (
                            <div className="rr capo">
                              <svg width={11} height={11} viewBox="-7 -7 14 14"><Sigil id="capo" color={capoCol} size={11} /></svg>
                              <span className="nm" style={{ color: capoCol }}>CAPO</span>
                              <span className={'st ' + (P.stale ? 'ko' : 'amc')}
                                title={P.capoT != null
                                  ? `in corsa dall'heartbeat delle ${hhmm(state?.updated_at)} (scritto quando il Capo e' partito)`
                                  : "updated_at assente nel payload: da quando scrive non e' dichiarato"}>
                                {fermoDa != null
                                  ? `dichiarato in scrittura · heartbeat fermo da ${fmtDurShort(fermoDa)}`
                                  : `scrive il memo${state?.memo_id != null ? ` #${state.memo_id}` : ''} · da ${
                                      P.capoT != null ? fmtDurShort(Math.max(0, Math.min(cur, P.runSec) - P.capoT)) : 'n.d. (updated_at assente)'}`}
                              </span>
                            </div>
                          )}
                        </>
                      )}
                      <div className="ft">
                        <b>{fatte}</b> chiamate fatte su {P.calls.length}
                        {/* il tappo lo DICE il backend (tool_log_tappato); la
                            disuguaglianza copre un backend NUOVO col flag perso.
                            Il backend VECCHIO non dichiara nulla (review 31/08):
                            a run viva col log esattamente al tetto (50) il
                            totale e' ignoto e si dichiara il dubbio, non «100%» */}
                        {(P.logTappato || (P.nCallsTot != null && P.nCallsTot > P.calls.length))
                          ? <> ricevute (le ultime{P.nCallsTot != null ? ` di ${P.nCallsTot}` : ' — totale n.d.'})</>
                          : P.live && P.nCallsTot == null && P.calls.length === 50
                          ? <> ricevute (50 e' il tetto del log: totale non dichiarato dal backend)</>
                          : (P.calls.length > 0 ? ` (${fmtN(fatte / P.calls.length * 100, 0)}%)` : '')}
                        {' · '}<b>{nDeskAlLavoro}</b> desk al lavoro
                        {capoScrive && P.capoT != null && <> · il Capo dall'heartbeat delle {hhmm(state?.updated_at)}</>}
                        {fase && <> · fase <b>{fase.k}</b> {fmtClock(fase.t0)}→{fase.open ? 'in corso' : fmtClock(fase.t1)}</>}
                      </div>
                    </div>
                  </div>

                  {/* il fatto della run, in vetrina */}
                  <div className="par hudplate" data-zona="parallelismo" data-par={P.parStato}>
                    <div className="k">il fatto della run · parallelismo</div>
                    {P.parStato === 'parziale' ? (
                      /* N2: a run viva la somma delle durate e' un minimo che cala con
                         l'orologio (0,68x -> 0,44x col solo passare del tempo): non e'
                         un parallelismo, e la piastra lo dice con i numeri */
                      <div className="s nd">
                        non calcolabile: {P.durDeclared < P.agentsKnown
                          ? <><b>{P.durDeclared} agenti su {P.agentsKnown}</b> hanno dichiarato la durata</>
                          : <><b>{P.agentsKnown}</b> agenti con durata dichiarata, {P.running.length + (P.capo === 'running' ? 1 : 0)} ancora in corsa (durate parziali)</>}
                        {P.live
                          ? <>{' — '}si calcola a run finita; finora almeno {fmtDurShort(lavoro)} di lavoro in {fmtDurShort(P.runSec)} di orologio</>
                          : <>{' — '}run chiusa: chi manca non l'ha dichiarata ({fmtDurShort(lavoro)} di lavoro dichiarato in {fmtDurShort(P.runSec)})</>}
                      </div>
                    ) : par == null ? (
                      <div className="s nd">durate per agente non dichiarate: il parallelismo non è calcolabile</div>
                    ) : (
                      <>
                        <div className="v"><b>{fmtN(par, 2)}×</b>
                          <span>{fmtDurShort(lavoro)} di lavoro agente dentro {fmtDurShort(P.runSec)} di orologio</span></div>
                        <div className="s">
                          {/* #1 (28/07): la frase era scritta senza condizione, e a 0,44x
                              diceva «supera l'orologio» — falso. Vera solo per par > 1. */}
                          {par > 1
                            ? <>le orbite si <u>sovrappongono</u>: la somma delle durate dichiarate dai
                                {' '}{P.agentsKnown} agenti supera l'orologio della run.</>
                            : P.sovrapposte
                            ? <>la somma delle durate sta dentro l'orologio, ma le orbite si sovrappongono
                                {' '}<u>a tratti</u>: i desk hanno lavorato in parte insieme.</>
                            : <>la somma delle durate sta dentro l'orologio e le orbite <u>non</u> si
                                sovrappongono: i desk hanno lavorato uno dopo l'altro.</>}
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
      <div className="p3h"><span>// PARALLELISMO</span><span className="side">desk aperti per minuto</span></div>
      <div className="p3b parw">
        {par == null ? (
          <div className="buco"><span className="t">n.d.</span>
            <span className="s">
              {P.parStato === 'parziale'
                ? <>{P.durDeclared < P.agentsKnown
                      ? <><b>{P.durDeclared} agenti su {P.agentsKnown}</b> hanno dichiarato la durata</>
                      : <><b>{P.agentsKnown}</b> agenti con durata dichiarata, {P.running.length + (P.capo === 'running' ? 1 : 0)} ancora in corsa (durate parziali)</>}
                    {P.live
                      ? <>: il parallelismo si calcola a run finita — finora almeno <b>{fmtDurShort(lavoro)}</b> di
                          lavoro in <b>{fmtDurShort(P.runSec)}</b> di orologio</>
                      : <>: run chiusa, chi manca non l'ha dichiarata — <b>{fmtDurShort(lavoro)}</b> di lavoro
                          dichiarato in <b>{fmtDurShort(P.runSec)}</b></>}</>
                : <>il payload non porta <b>duration_s</b> per agente: il parallelismo non è calcolabile</>}
            </span></div>
        ) : (
          <>
            <div className="bignum num">{fmtN(par, 2)}×</div>
            <div className="bigsub">
              <b>{fmtDurShort(lavoro)}</b> di lavoro dichiarato dai {P.agentsKnown} agenti
              dentro <b>{fmtDurShort(P.runSec)}</b> di orologio.
              {par > 1
                ? <> Il quadrante lo mostra con le orbite che si sovrappongono sullo stesso spicchio di minuti.</>
                : P.sovrapposte
                ? <> La somma sta dentro l'orologio, ma le orbite si sovrappongono a tratti.</>
                : <> Le orbite non si sovrappongono: i desk hanno lavorato uno dopo l'altro.</>}
            </div>
            <div className="simplot">
              {bins.map((n, m) => (
                <u key={m} title={`minuto ${m}' — ${n} desk al lavoro`}
                  style={{ height: `${(n / mx * 100).toFixed(1)}%`, background: n === mx ? '#FFA51E' : undefined }} />
              ))}
            </div>
            <div className="simax"><span>0'</span><span>{Math.round(P.runSec / 60)}'</span></div>
            <div className="simfoot">
              punta: <b>{mx} desk insieme</b> al minuto {peak}'.
              {P.phases.some(p => p.k === 'SINTESI') && <> Dal minuto {Math.round(P.lastCallT / 60)}' nessuna chiamata:
                gira la catena di sintesi, che ha durata ma <b>nessun orario</b> nel payload.</>}
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
  const rows: [string, number | null | undefined, string][] = [
    ['input fresco', total?.in, '#29D3F2'],
    ['output', total?.out, '#21E0A0'],
    ['letti da cache', total?.cache_read, '#FFA51E'],
    ['scritti in cache', total?.cache_write, '#9B7BFF'],
  ];
  const mx = Math.max(1, ...rows.map(r => r[1] || 0));
  const agenti = [...P.desks, ...P.pending, ...P.stages];
  const mxA = Math.max(1, ...agenti.map(a => (a.tin ?? 0) + (a.tout ?? 0) + (a.cacheR ?? 0) + (a.cacheW ?? 0)));

  return (
    <div className="p3 peco">
      <span className="tick tr" />
      <div className="p3h vi"><span>// ECONOMIA DELLA RUN</span><span className="side">token · cache · fx</span></div>
      <div className="p3b tokwrap">
        {!total ? (
          <div className="buco"><span className="t">n.d.</span>
            <span className="s">l'heartbeat non porta <b>usage_total</b>: i costi di questa run non sono dichiarati</span></div>
        ) : (
          <>
            {rows.map(([k, v, c]) => (
              <div className="tokrow" key={k}>
                <span className="k">{k}</span>
                <span className="bar"><u style={{ width: `${((v || 0) / mx * 100).toFixed(1)}%`, background: c }} /></span>
                <span className="v num" style={{ color: c }} title={v != null ? `${fmtN(v)} token` : 'non dichiarato'}>
                  {fmtTok(v)}</span>
              </div>
            ))}
            {agenti.length > 0 && (
              <div className="tkag">
                <div className="h"><span>token per agente</span><span>totale toccato</span></div>
                {agenti.map(a => {
                  const completo = [a.tin, a.tout, a.cacheR, a.cacheW].every(v => v != null);
                  const tot = completo ? a.tin! + a.tout! + a.cacheR! + a.cacheW! : null;
                  const seg: [number | null, string][] = [[a.tin, '#29D3F2'], [a.tout, '#21E0A0'],
                    [a.cacheR, '#FFA51E'], [a.cacheW, '#9B7BFF']];
                  return (
                    <div className="r" key={a.id} title={`${a.name}: ${fmtTok(a.tin)} in · ${fmtTok(a.tout)} out · ${fmtTok(a.cacheR)} letti · ${fmtTok(a.cacheW)} scritti${completo ? '' : ' · token parziali'}`}>
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
                ? <>Cache riletta <b>{fmtN(total.cache_read / total.in, 1)}×</b> l'input fresco: è l'economia vera della run.</>
                : <>Cache non dichiarata dal payload.</>}
              <br />Somma degli agenti <b>{fmtEur(sumAgents)}</b>
              {total.cost_eur != null && Math.abs(sumAgents - total.cost_eur) < 0.005
                ? <>, identica al totale: nessun delta da spiegare.</>
                : <>, contro un totale di <b>{fmtEur(total.cost_eur)}</b>: <span className="amc">il delta non è spiegato dal payload</span>.</>}
              {koIds.length > 0 && <><br /><span className="ko">Dentro ci sono {fmtEur(koCost)} spesi
                dai {koIds.length} desk in <b>api_error</b>.</span></>}
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
        <span className={'v ' + v.cls}>{v.k}</span>
      </div>
    );
  };
  const sommaTool = P.desks.reduce((s, d) => s + d.nTools, 0);
  return (
    <div className="p3 pcif">
      <span className="tick bl" />
      <div className="p3h"><span>// GLI AGENTI IN CIFRE</span><span className="side">un solo verdetto</span></div>
      <div className="p3b cif">
        <div className="hd">
          <span>desk · orbita</span><i>durata</i><i>chiam.</i><i>tool</i><i>api</i><i>costo</i><i className="ce">esito</i>
        </div>
        {P.desks.map(d => riga(d))}
        {P.pending.length > 0 && (
          <>
            {/* un desk senza chiamate nel log NON e' uno stadio di sintesi (N8):
                o non e' ancora partito in questo round, o le sue chiamate sono
                uscite dalla finestra dell'heartbeat */}
            <div className="sep">▾ desk senza chiamate nel log ricevuto — <b>non collocabili sul quadrante</b></div>
            {P.pending.map(d => riga(d))}
          </>
        )}
        {/* il Capo in corsa ha l'ORARIO (l'heartbeat di partenza, nella piastra di
            lettura) ma non ancora la durata: non sta sotto «durata sì, orario no» */}
        {P.live && P.capo === 'running' && P.stages.some(s => s.id === 'capo') && (
          <>
            <div className="sep">▾ il Capo — <b>in corsa</b>, durata a fine memo (da quando: nella piastra di lettura)</div>
            {P.stages.filter(s => s.id === 'capo').map(d => riga(d))}
          </>
        )}
        {P.stages.some(s => !(P.live && P.capo === 'running' && s.id === 'capo')) && (
          <>
            <div className="sep">▾ stadi di sintesi — <b>durata sì, orario no</b>: non collocabili sul quadrante</div>
            {P.stages.filter(s => !(P.live && P.capo === 'running' && s.id === 'capo')).map(d => riga(d))}
          </>
        )}
        {total?.cost_eur != null && (
          <div className="tot">
            <span className="n">somma agenti</span>
            <i>{fmtDurShort([...P.desks, ...P.pending, ...P.stages].reduce((s, d) => s + (d.dur || 0), 0))}</i>
            <i>{P.calls.length}</i><i>{P.nToolsDistinct}*</i>
            <i>{[...P.desks, ...P.pending, ...P.stages].reduce((s, d) => s + d.apiCalls, 0)}</i>
            <i className="c">{fmtEur(total.cost_eur)}</i>
            <span className={'v ' + (koIds.length ? 'ko' : 'okc')}>{koIds.length ? `${koIds.length} KO` : 'OK'}</span>
          </div>
        )}
        <div className="note">
          <b>chiam.</b> = chiamate a strumento · <b>tool</b> = strumenti <b>distinti</b> di quel desk.
          {P.logTappato && <> I conteggi valgono sulle <b>ultime {P.calls.length}</b> chiamate
            ricevute{P.nCallsTot != null && <> (la run ne ha fatte {P.nCallsTot})</>}: chi ha chiamate
            piu' vecchie ne mostra meno del vero.</>}
          {P.nToolsDistinct > 0 && <> * i {P.nToolsDistinct} strumenti della run non sono la somma
            della colonna ({sommaTool}): gli stessi strumenti li usano desk diversi.</>}
          <br />Motore del comitato da <b>engines</b>, non da agents[].model (quello è il modello della chat):
          {P.desks.length > 0 && <> desk <b>{engineShort(P.desks[0].id, engines, P.desks[0].rounds)}</b></>}
          {P.stages.some(s => s.id === 'capo') && <> · capo <b>{engineShort('capo', engines)}</b></>}
          {P.stages.some(s => s.id === '_reflection' || s.id === '_action_table') &&
            <> · reflection e action table: <b>n.d.</b>, non esposti</>}
        </div>
      </div>
    </div>
  );
}

function PannelloStrumenti({ P, fatte, cur }: { P: Plancia; fatte: number; cur: number }) {
  const mx = P.tools[0]?.n || 1;
  return (
    <div className="p3 patt">
      <span className="tick tl" />
      <div className="p3h cy"><span>// DOVE HA GUARDATO IL COMITATO</span>
        {/* «tutti e N» solo quando il log e' INTERO: a run viva sono le ultime 50 (N8) */}
        <span className="side">{P.logTappato
          ? `${P.nToolsDistinct} nelle ultime ${P.calls.length} chiamate`
          : `tutti e ${P.nToolsDistinct}`}</span></div>
      <div className="p3b attwrap">
        {P.tools.length === 0 ? (
          <div className="buco"><span className="t">nessuna chiamata</span>
            <span className="s">il <b>tool_log</b> dell'heartbeat è vuoto</span></div>
        ) : (
          <>
            <div className="atl">
              {P.desks.map(d => (
                <b key={d.id} style={{ color: d.color }}>
                  <svg width={10} height={10} viewBox="-7 -7 14 14"><Sigil id={d.id} color={d.color} size={10} /></svg>
                  {d.name.toUpperCase()}
                </b>
              ))}
              <span className="n">barra divisa per desk in ordine d'uso · il sigillo davanti alla barra è
                il desk che l'ha usato di più: l'identità sta nella <b>forma</b>, non nel colore
                (macro e crypto sono due ambre gemelle)</span>
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
  return (
    <div className="p3 ptk">
      <span className="tick br" />
      <div className="p3h"><span>// TICKER TOCCATI</span>
        <span className="side">{P.logTappato
          ? `${P.tickers.length} nelle ultime ${P.calls.length} chiamate`
          : `tutti e ${P.tickers.length}`}</span></div>
      <div className="p3b tkl">
        {P.tickers.length === 0 ? (
          <div className="buco"><span className="t">nessun ticker</span>
            <span className="s">gli <b>input</b> delle chiamate non nominano ticker espliciti</span></div>
        ) : P.tickers.map(x => <span key={x.k}>{x.k}<b>{x.n}</b></span>)}
      </div>
    </div>
  );
}

function PannelloNastro({ P, cur }: { P: Plancia; cur: number }) {
  return (
    <div className="p3 plog">
      <span className="tick tr" />
      <div className="p3h cy"><span>// NASTRO DELLE CHIAMATE</span>
        <span className="side">{P.logTappato
          ? `le ultime ${P.calls.length}${P.nCallsTot != null ? ` di ${P.nCallsTot}` : ''} ricevute`
          : `tutte e ${P.calls.length}`}, in ordine di tempo · T+ dallo start</span></div>
      <div className="p3b logwrap">
        {P.calls.length === 0 ? (
          <div className="buco"><span className="t">nastro vuoto</span>
            <span className="s">nessuna chiamata a strumento nel heartbeat</span></div>
        ) : (
          <>
            <div className="lhd">
              {[0, 1, 2].map(i => (
                <div className="lr" key={i}>
                  <span>T+</span><span>desk</span><span>rd</span><span>strumento</span><span>input</span>
                </div>
              ))}
            </div>
            <div className="logw">
              {P.calls.map((c, i) => {
                const d = P.desks.find(x => x.id === c.a);
                return (
                  <div className={'lr' + (Math.abs(c.t - cur) < 20 ? ' hot' : '')} key={i}
                    title={`orologio di parete ${c.hhmm} · ${c.input}`}>
                    <span className="tm num">{fmtClock(c.t)}</span>
                    <span className="ag" style={{ color: d?.color }}>{c.a.toUpperCase()}</span>
                    <span className="rd">R{c.r}</span>
                    <span className="tl">{c.tool}</span>
                    <span className="in">{c.input === '{}' ? '—' : c.input.replace(/[{}']/g, '')}</span>
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
