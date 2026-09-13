import { t as tr } from '@/i18n/t';
/* ============================================================
   F4 PLANCIA ORBITALE — strato DATI (Opus 5, 26/07)

   Ricava dalla risposta viva di `GET /agents/live` tutto cio' che il
   quadrante disegna. Il backend NON consegna finestre, fasi ne' conteggi
   per agente: consegna `tool_log` (una riga per chiamata) e
   `usage_by_specialist`. Qui si derivano, con le stesse formule del
   generatore dei mockup (mockup_f4_agents/build_data.py), cosi' che quello
   che il PM ha approvato sui PNG sia esattamente quello che vede in pagina.

   REGOLA 14/07 APPLICATA ALLA DERIVAZIONE: se manca l'ancoraggio temporale
   (`start_time`) o le righe di log, NON si inventa un tempo zero: si torna
   `ok:false` con il motivo, e la pagina lo dichiara invece di disegnare un
   quadrante finto.
   ============================================================ */
import type { AgentsLiveState, ToolLogEntry, AgentInfo, EnginesInfo, UsageBySpecialist } from './api';

/** una chiamata a strumento, con il tempo in secondi dall'inizio run */
export interface Call { a: string; r: number; tool: string; input: string; hhmm: string; t: number }

/** finestra di lavoro di un desk in un round: dalla prima all'ultima chiamata.
 *  `open`: a run viva il backend dichiara il desk `running` (specialist_status),
 *  quindi la finestra si allunga fino al presente; `tCall` e' l'ultima chiamata
 *  MISURATA, da cui in poi il desk ragiona senza strumenti (F45, blocco A). */
export interface Window { a: string; r: number; t0: number; t1: number; n: number; open?: boolean; tCall?: number }

/** fase della run: unione delle finestre di quel round; piu' la coda di sintesi.
 *  `open`: il round e' in corso (almeno un suo desk e' dichiarato `running`). */
export interface Phase { k: string; t0: number; t1: number; measured: boolean; open?: boolean }

/** com'e' il parallelismo: calcolabile, parziale (k agenti su n hanno dichiarato
 *  la durata — a run viva e' la norma), o non dichiarato affatto */
export type ParStato = 'calcolato' | 'parziale' | 'nd';

/** un agente come lo disegna la plancia */
export interface Desk {
  id: string; name: string; role: string; color: string;
  onGrid: boolean;              // ha una voce nel roster di /agents/list?
  statusRun?: string;           // specialist_status: ha consegnato il report?
  statusUsage?: string;         // usage.status: ha sbattuto contro l'API?
  dur: number | null; apiCalls: number; cost: number | null; partial?: boolean;
  tin: number | null; tout: number | null; cacheR: number | null; cacheW: number | null;
  nCalls: number;               // CHIAMATE a strumento (righe di tool_log)
  nTools: number;               // strumenti DISTINTI usati
  rounds: number[];
  obs0: number | null; obs1: number | null;   // finestra osservata
}

export interface Plancia {
  ok: boolean;
  reason?: string;              // perche' non e' derivabile (da DICHIARARE in pagina)
  runSec: number;               // durata della run in secondi
  scaleSec: number;             // quanto vale un giro completo del quadrante
  scaleNote: string;            // come si legge quel giro (va scritto in corona)
  live: boolean;
  startISO?: string; endISO?: string;
  calls: Call[];
  windows: Window[];
  phases: Phase[];
  desks: Desk[];                // chi ha usato strumenti: hanno un'orbita
  stages: Desk[];               // gli stadi di sintesi (capo, _red_team, …): stanno nel nucleo
  pending: Desk[];              // desk del comitato SENZA chiamate nel log ricevuto: non ancora
                                // partiti in questo round, o usciti dalla finestra (N8, blocco D)
  tools: { tool: string; n: number; by: Record<string, number> }[];
  tickers: { k: string; n: number }[];
  nToolsDistinct: number;
  lastCallT: number;            // ultima chiamata a strumento
  /* ── lo STATO DICHIARATO dal backend (F45, blocco A: N1/N10/N2) ────────
     Prima la pagina deduceva chi lavora dall'ultima chiamata del tool_log e
     ignorava `specialist_status`: «nessun desk al lavoro» mentre quattro desk
     ragionavano, e il Capo che scriveva il memo era invisibile. */
  running: string[];            // desk dichiarati `running` (solo a run viva)
  capo: string | null;          // specialist_status.capo: 'running' | 'done' | null
  capoT: number | null;         // da quando scrive, in secondi dallo start (= updated_at:
                                // l'heartbeat scritto da mark_specialist_start, v. derivePlancia)
  round: number | null;         // current_round dichiarato
  stale: boolean;               // stale_warning del backend: heartbeat fermo da >600 s
  durDeclared: number;          // quanti agenti hanno dichiarato duration_s
  agentsKnown: number;          // quanti agenti conosce il payload (usage ∪ log ∪ status)
  parStato: ParStato;
  /* almeno due finestre di desk DIVERSI si sovrappongono nel tempo: e' il fatto
     misurato dietro «le orbite si sovrappongono», che il solo rapporto
     durate/orologio non prova (par <= 1 non implica «uno dopo l'altro») */
  sovrapposte: boolean;
  /* ── il tool_log DICHIARATO (blocco D, N8): a run viva l'heartbeat porta le
     ULTIME 50 chiamate e lo dice con `tool_log_tappato`; il totale VERO e'
     `n_tool_calls`. Ogni «tutte e N» calcolato su `calls` a log tappato e'
     falso: chi scrive un denominatore legge questi due campi. */
  nCallsTot: number | null;     // n_tool_calls: il totale VERO; null = non dichiarato
  logTappato: boolean;          // true = `calls` sono le ultime 50 ricevute
}

const HHMMSS = /^(\d{1,2}):(\d{2}):(\d{2})$/;

/** 'HH:MM:SS' -> secondi dalla mezzanotte; null se il formato non e' quello */
function clockSec(s?: string): number | null {
  if (!s) return null;
  const m = HHMMSS.exec(s.trim());
  if (!m) return null;
  return (+m[1]) * 3600 + (+m[2]) * 60 + (+m[3]);
}

/**
 * Deriva tutto. `now` serve solo quando la run e' IN CORSO: la durata di una
 * run viva non e' nel payload, e' l'orologio che scorre.
 */
export function derivePlancia(
  st: AgentsLiveState | null,
  roster: AgentInfo[],
  _engines: EnginesInfo | undefined,
  now: number = Date.now(),
): Plancia {
  const empty: Plancia = {
    ok: false, runSec: 0, scaleSec: 3600, scaleNote: '', live: false,
    calls: [], windows: [], phases: [], desks: [], stages: [], pending: [],
    tools: [], tickers: [], nToolsDistinct: 0, lastCallT: 0,
    running: [], capo: null, capoT: null, round: null, stale: false,
    durDeclared: 0, agentsKnown: 0, parStato: 'nd', sovrapposte: false,
    nCallsTot: null, logTappato: false,
  };
  if (!st) return { ...empty, reason: tr('dashboard.heartbeat_missing') };

  const live = st.running === true;
  const startMs = st.start_time ? new Date(st.start_time).getTime() : NaN;
  if (!isFinite(startMs)) {
    return { ...empty, live, reason: tr('dashboard.dial_no_start') };
  }
  const start0 = clockSec(new Date(startMs).toTimeString().slice(0, 8));
  const endMs = st.completed_at ? new Date(st.completed_at).getTime() : NaN;

  /* durata: dal payload se la run e' chiusa, dall'orologio se e' viva */
  const runSec = live
    ? Math.max(1, (now - startMs) / 1000)
    : (isFinite(endMs) ? Math.max(1, (endMs - startMs) / 1000) : 0);

  /* ── le chiamate, col tempo in secondi dall'inizio ──────────────────────
     `time` e' 'HH:MM:SS' SENZA data: l'unico modo di ancorarlo e' la
     differenza con l'ora di start. Se la run scavalca la mezzanotte il
     valore tornerebbe negativo: si riporta avanti di 24h una volta sola.  */
  const raw: ToolLogEntry[] = st.tool_log || [];
  const calls: Call[] = [];
  for (const r of raw) {
    const c = clockSec(r.time);
    if (c == null || start0 == null) continue;
    let t = c - start0;
    if (t < -60) t += 86400;                 // run a cavallo di mezzanotte
    calls.push({ a: r.specialist, r: r.round, tool: r.tool, input: r.input, hhmm: r.time, t });
  }
  calls.sort((p, q) => p.t - q.t);
  const lastCallT = calls.length ? calls[calls.length - 1].t : 0;

  /* ── finestre: per (desk, round), dalla prima all'ultima chiamata ─────── */
  const wmap = new Map<string, Window>();
  for (const c of calls) {
    const k = c.a + '#' + c.r;
    const w = wmap.get(k);
    if (!w) wmap.set(k, { a: c.a, r: c.r, t0: c.t, t1: c.t, n: 1 });
    else { w.t1 = c.t; w.n++; }
  }
  const windows = [...wmap.values()].sort((p, q) => p.t0 - q.t0);

  /* ── chi e' in corsa lo DICE il backend (F45, blocco A) ──────────────────
     `specialist_status` e' il fatto; l'ultima chiamata del tool_log e' solo
     l'ultima volta che il desk ha usato uno strumento. Fra due chiamate (e a
     R1/R2 e' la maggior parte del tempo) il desk ragiona: la sua finestra del
     round corrente si allunga fino al presente e resta `open`. Un desk
     `running` SENZA chiamate in questo round non riceve una finestra — il suo
     t0 non e' nel payload e non si inventa (regola 14/07): sta in `pending`
     e la piastra di lettura lo dichiara «ragiona · nessuna chiamata ancora». */
  const status = st.specialist_status || {};
  const running = live ? Object.keys(status).filter(id => status[id] === 'running' && id !== 'capo') : [];
  const capo: string | null = status.capo ?? null;
  const round = typeof st.current_round === 'number' ? st.current_round : null;
  const stale = st.stale_warning === true;
  if (live && round != null) {
    /* round non dichiarato: NON si indovina quale finestra allungare (sarebbe
       l'ultima di un round magari chiuso): il desk resta senza finestra e la
       piastra lo dice come «dichiarato in corsa, nessuna chiamata nel log» */
    for (const id of running) {
      const w = windows.find(x => x.a === id && x.r === round);
      if (w && runSec > w.t1) { w.tCall = w.t1; w.t1 = runSec; w.open = true; }
    }
  }
  /* il Capo: `updated_at` e' l'heartbeat scritto da `mark_specialist_start`
     (specialists/base.py) e nessun altro scrittore lo tocca finche' lui scrive
     il memo — `run_capo` non scrive heartbeat, `record_usage('capo')` arriva a
     memo finito (misurato il 26/08: 19:19:15 fermo per 5'43"). E' il suo
     istante di partenza, e la pagina lo dichiara come «heartbeat delle HH:MM».
     Contratto scritto nel ponte (COORDINAMENTO_CHAT, 27/08): se il backend un
     giorno rinfrescasse l'heartbeat durante il Capo, serve un campo dedicato. */
  const updMs = st.updated_at ? new Date(st.updated_at).getTime() : NaN;
  const capoT = live && capo === 'running' && isFinite(updMs) ? Math.max(0, (updMs - startMs) / 1000) : null;

  /* ── fasi: unione delle finestre per round, piu' la coda di sintesi ────
     La coda e' MISURATA solo nei suoi estremi (ultima chiamata -> fine run):
     dentro ci girano gli agenti senza strumenti, di cui il payload da' la
     durata ma NON l'orario. `measured:false` lo dichiara a chi disegna.
     A run viva la coda esiste SOLO se nessun desk e' dichiarato in corsa:
     con un desk running il tempo dopo l'ultima chiamata e' il round che
     continua (fase `open`), non la sintesi (N1: «SINTESI» su ogni pausa). */
  const rounds = [...new Set(windows.map(w => w.r))].sort((a, b) => a - b);
  const phases: Phase[] = rounds.map(r => {
    const ws = windows.filter(w => w.r === r);
    /* aperto anche se l'unico desk in corsa del round non ha ancora chiamate
       (nessuna finestra sua): il round e' dichiarato in corso dal backend */
    const open = ws.some(w => w.open) || (live && r === round && running.length > 0);
    return { k: 'R' + r, t0: Math.min(...ws.map(w => w.t0)),
             t1: open ? Math.max(runSec, ...ws.map(w => w.t1)) : Math.max(...ws.map(w => w.t1)),
             measured: true, open };
  });
  if (windows.length && runSec > lastCallT + 1 && running.length === 0 && !phases.some(p => p.open)) {
    /* a run viva la coda e' SINTESI solo quando il Capo e' dichiarato (in corsa o
       finito): nei secondi fra un round e l'altro nessuno e' dichiarato e la
       coda si chiama col suo nome, FRA ROUND, senza attribuirle una causa.
       E la sua fine e' il presente, non un estremo: `open`, come i round. */
    const k = !live || capo === 'running' || capo === 'done' ? 'SINTESI' : 'FRA ROUND';
    phases.push({ k, t0: lastCallT, t1: runSec, measured: false, open: live });
  }

  /* ── gli agenti ────────────────────────────────────────────────────────
     nCalls e nTools sono DUE numeri diversi e vanno chiamati col loro nome:
     un desk con 78 chiamate puo' aver usato 15 strumenti distinti. Chiamare
     "tool" il primo e' l'errore che il collaudo dei mockup ha scovato.     */
  const byId = new Map(roster.map(a => [a.id, a]));
  const usage: Record<string, UsageBySpecialist> = st.usage_by_specialist || {};
  /* anche chi sta SOLO in specialist_status: il Capo mentre scrive (N10) e un
     desk running prima della sua prima chiamata non hanno ancora usage */
  const ids = [...new Set([...Object.keys(usage), ...calls.map(c => c.a), ...Object.keys(status)])];
  const all: Desk[] = ids.map(id => {
    const u = usage[id];
    const meta = byId.get(id);
    const mine = calls.filter(c => c.a === id);
    return {
      id,
      name: meta?.name || id.replace(/^_/, '').replace(/_/g, ' ').toUpperCase(),
      role: meta?.role || '',
      color: meta?.color || '#5A6685',
      onGrid: !!meta,
      statusRun: st.specialist_status?.[id],
      statusUsage: u?.status,
      dur: u?.duration_s ?? null,
      apiCalls: u?.api_calls ?? 0,
      cost: u?.cost_eur ?? null,
      partial: u?.partial,
      tin: u?.in ?? null, tout: u?.out ?? null, cacheR: u?.cache_read ?? null, cacheW: u?.cache_write ?? null,
      nCalls: mine.length,
      nTools: new Set(mine.map(c => c.tool)).size,
      rounds: [...new Set(mine.map(c => c.r))].sort((a, b) => a - b),
      obs0: mine.length ? mine[0].t : null,
      obs1: mine.length ? mine[mine.length - 1].t : null,
    };
  });
  /* i desk in ordine di ACCENSIONE: e' l'ordine delle orbite, dalla piu' esterna */
  const desks = all.filter(d => d.nCalls > 0).sort((a, b) => (a.obs0 ?? 0) - (b.obs0 ?? 0));
  /* senza chiamate nel log: gli STADI di sintesi (capo, _red_team, _reflection,
     _action_table) stanno nel nucleo per natura; un DESK del comitato senza
     chiamate invece non e' «sintesi» — o non e' ancora partito in questo round,
     o le sue chiamate sono uscite dalla finestra [-50:] dell'heartbeat (N8). */
  const isStage = (id: string) => id === 'capo' || id.startsWith('_');
  const stages = all.filter(d => d.nCalls === 0 && isStage(d.id));
  const pending = all.filter(d => d.nCalls === 0 && !isStage(d.id));

  /* ── il parallelismo si puo' calcolare? (N2) ─────────────────────────────
     `duration_s` arriva a fine desk e si ACCUMULA round dopo round: a run viva
     la somma e' un minimo che cala rispetto all'orologio col solo passare del
     tempo. Quindi: viva → parziale (dichiarando k su n); chiusa → calcolabile
     solo se TUTTI gli agenti noti hanno dichiarato la durata.               */
  const durDeclared = all.filter(d => d.dur != null).length;
  const agentsKnown = all.length;
  const parStato: ParStato = durDeclared === 0 ? 'nd'
    : (live || durDeclared < agentsKnown) ? 'parziale' : 'calcolato';
  /* la sovrapposizione MISURATA: due finestre di desk diversi che condividono
     un istante (t0 < t1' e t0' < t1). Le finestre aperte contano fino al presente. */
  const sovrapposte = windows.some((w, i) => windows.some((v, j) =>
    j > i && v.a !== w.a && v.t0 < w.t1 && w.t0 < v.t1));

  /* ── strumenti: quante volte, e da chi ─────────────────────────────────── */
  const tmap = new Map<string, { tool: string; n: number; by: Record<string, number> }>();
  for (const c of calls) {
    let e = tmap.get(c.tool);
    if (!e) { e = { tool: c.tool, n: 0, by: {} }; tmap.set(c.tool, e); }
    e.n++; e.by[c.a] = (e.by[c.a] || 0) + 1;
  }
  const tools = [...tmap.values()].sort((a, b) => b.n - a.n);

  /* ── ticker citati negli input: su CHI ha lavorato il comitato ──────────
     Oggi la pagina rende `input` troncato in una cella di tabella e questo
     non si legge da nessuna parte. Si estrae solo cio' che e' esplicito. */
  const tk = new Map<string, number>();
  for (const c of calls) {
    for (const key of ["'ticker': '", "'market': '", '"ticker": "', '"market": "']) {
      const i = c.input.indexOf(key);
      if (i < 0) continue;
      const rest = c.input.slice(i + key.length);
      const v = rest.slice(0, rest.search(/['"]/));
      if (v) tk.set(v, (tk.get(v) || 0) + 1);
    }
  }
  const tickers = [...tk.entries()].map(([k, n]) => ({ k, n })).sort((a, b) => b.n - a.n);

  /* ── la scala del quadrante ────────────────────────────────────────────
     Run CHIUSA: un giro = la durata vera, ed e' il caso che il PM ha
     approvato sui mockup. Run VIVA: la durata non si conosce ancora, quindi
     un giro = 60' tondi (un quadrante d'orologio vero, stabile mentre la
     lancetta avanza); oltre l'ora il quadrante inizia un secondo giro e lo
     dichiara. In entrambi i casi la corona SCRIVE quanto vale un giro: una
     scala muta sarebbe un numero inventato.                               */
  const scaleSec = live ? Math.max(3600, Math.ceil(runSec / 3600) * 3600) : Math.max(1, runSec);
  const giri = live ? Math.max(1, Math.ceil(runSec / 3600)) : 1;
  const scaleNote = live
    ? tr('dashboard.dial_scale_live', {a: fmtDurShort(runSec), b: giri > 1 ? tr('dashboard.dial_revolution', {a: giri}) : ''})
    : tr('dashboard.dial_scale_done', {a: fmtDurShort(runSec)});

  const ok = calls.length > 0 && runSec > 0;
  return {
    ok,
    reason: ok ? undefined
      : (!calls.length ? tr('dashboard.dial_no_calls')
                       : tr('dashboard.dial_no_duration')),
    runSec, scaleSec, scaleNote, live,
    startISO: st.start_time, endISO: st.completed_at,
    calls, windows, phases, desks, stages, pending, tools, tickers,
    nToolsDistinct: tools.length, lastCallT,
    running, capo, capoT, round, stale, durDeclared, agentsKnown, parStato, sovrapposte,
    /* un n_tool_calls SOTTO le righe del log e' un contratto violato (il
       totale non puo' essere minore di cio' che si vede): meglio un n.d.
       dichiarato dai punti di resa che quattro frasi aritmeticamente
       impossibili («le ultime 50 di 30») — review 31/08 */
    nCallsTot: typeof st.n_tool_calls === 'number' && st.n_tool_calls >= calls.length
      ? st.n_tool_calls : null,
    logTappato: st.tool_log_tappato === true,
  };
}

/** mm'ss" — la lettura naturale di una run da qualche decina di minuti */
export function fmtDurShort(s: number | null | undefined): string {
  if (s == null || !isFinite(s)) return tr('dashboard.na');
  return Math.floor(s / 60) + "'" + String(Math.round(s % 60)).padStart(2, '0') + '"';
}

/** mm:ss — per le etichette di orario relativo sul quadrante */
export function fmtClock(s: number): string {
  return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(Math.round(s % 60)).padStart(2, '0');
}

/* ══════════════════════════════════════════════════════════════════════
   IL MOTORE DI UN AGENTE — e NON e' `agents[].model`.

   `agents[].model` e' il modello con cui quell'agente risponde IN CHAT
   (serve a F3). Il motore con cui ha girato NEL COMITATO sta in `engines`
   (voce ponte 26/07). Non e' una contraddizione del payload: sono due cose
   diverse, e mostrare la prima su questa pagina e' semplicemente sbagliato.
   Dove `engines` non espone nulla (reflection, action table) si dichiara
   n.d.: non si deduce.
   ══════════════════════════════════════════════════════════════════════ */
const ENGINE_KEY: Record<string, string | null> = {
  capo: 'capo', _red_team: 'red_team', _reflection: null, _action_table: null,
};

export function engineOf(id: string, e: EnginesInfo | undefined, round?: number): string | null {
  if (!e) return null;
  if (id in ENGINE_KEY) {
    const k = ENGINE_KEY[id];
    return k ? (e[k] || null) : null;
  }
  return (round === 0 ? e.committee_r0 : e.committee_r1_r2) || null;
}

/** forma corta per le targhe strette: `claude-opus-5` -> `opus-5` */
export function engineShort(id: string, e: EnginesInfo | undefined, rounds: number[] = []): string {
  const cut = (m: string | null) => (m ? m.replace(/^claude-/, '') : null);
  if (id in ENGINE_KEY) return cut(engineOf(id, e)) || tr('dashboard.na');
  const r0 = cut(engineOf(id, e, 0)), r12 = cut(engineOf(id, e, 1));
  if (!r0 && !r12) return tr('dashboard.na');
  if (r0 === r12) return r0 as string;
  return rounds.some(r => r > 0) ? `${r12 ?? tr('dashboard.na')} (R1/R2)` : `${r0 ?? tr('dashboard.na')} (R0)`;
}
