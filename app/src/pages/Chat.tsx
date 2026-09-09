import { useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Bellomberg, AgentInfo, EnginesInfo, ChatSession, ChatMessage, MandatoMeta,
  getSessionToken, clearSessionAndReload,
} from '@/lib/api';
import { Send, Plus, Trash2, Square, Search, Wrench } from 'lucide-react';
import ConfirmDialog from '@/components/ConfirmDialog';
import ChatSuggestions from '@/components/ChatSuggestions';
import NastroEsecuzione, { ToolCall, FlowPoint, fmtKB, fmtMs } from '@/components/NastroEsecuzione';
import './dashboard-command.css';
import './chat-desk.css';

/* ════════════════════════════════════════════════════════════
   F3 v3 "DESK CONVERSAZIONALE" (Opus 5, 26/07)
   Impianto scelto dal PM sui mockup (mockup_f3_chat/F3_MIX.png):
   desk + archivio a sinistra · nastro d'esecuzione e conversazione
   al centro · catena di custodia, arsenale e telemetria a destra.

   La pagina consuma lo stream PER INTERO. Prima di oggi finivano
   in console.log — e la pagina scriveva a mano "sonnet-4-6":
     meta            -> modello VERO, n. strumenti dell'agente, cap iterazioni
     tool_use_start  -> nome, tool_id, iterazione, istante
     tool_result     -> ESITO, byte tornati, anteprima grezza
     done            -> ok/interrotta, iterazioni, token
   Nessuna stringa di modello e' cablata: si legge dal payload.
   ════════════════════════════════════════════════════════════ */

/** Ponte 26/07: l'archivio del PM contiene 16 conversazioni di due specialisti
 *  ritirati (politics, news) che /agents/list non elenca piu' — dalla pagina erano
 *  IRRAGGIUNGIBILI. Finche' il backend non espone l'archivio completo
 *  (richiesta nel ponte: GET /chat/sessions?all=1) questi due id restano qui,
 *  dichiarati in pagina come "LEGACY · SOLA LETTURA". */
const AGENTI_RITIRATI = ['politics', 'news'];

interface Segnale { pos: number; n: number }      // dove, nel testo, e' partita la chiamata n

interface RuntimeMessage extends Partial<ChatMessage> {
  role: 'user' | 'assistant';
  content: string;
  streaming?: boolean;
  /** null = il backend DICHIARA token non noti (stream morto prima delle
   *  metriche): non è uno zero, e non si rende come zero (audit/24 B.13). */
  tokens?: { in: number | null; out: number | null };
  /* materiale dello stream (solo live: il backend non lo salva) */
  calls?: ToolCall[];
  marks?: Segnale[];
  flow?: FlowPoint[];
  meta?: { model?: string; n_tools_available?: number; max_tool_iterations?: number; mandato?: MandatoMeta | null };
  ok?: boolean;
  iterations?: number;
  durata?: number | null;
  storico?: boolean;                              // caricato dal DB: niente traccia
  errore?: string;
  requestId?: number;
}

/** Titolo LETTERALE dal primo messaggio: e' quello che il PM ha scritto, ripulito.
 *  Provvisorio per costruzione — il titolo semantico lo scrive il backend (Haiku,
 *  0,00025 EUR a chat misurati) appena l'endpoint di rinomina esiste (voce nel ponte). */
export function titoloDaMessaggio(q: string, max = 54): string {
  let s = (q || '').replace(/\s+/g, ' ').trim();
  if (!s) return '';
  if (s === s.toUpperCase() && /[A-Z]{4}/.test(s)) s = s.toLowerCase();
  s = s.charAt(0).toUpperCase() + s.slice(1);
  const dom = s.indexOf('?');
  if (dom > 8 && dom <= max + 8) return s.slice(0, dom + 1);
  if (s.length <= max) return s;
  const taglio = s.slice(0, max);
  const sp = taglio.lastIndexOf(' ');
  return (sp > max * 0.55 ? taglio.slice(0, sp) : taglio) + '…';
}

const titoloGenerico = (t: string) => /^Chat con /i.test((t || '').trim());

export default function Chat() {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [engines, setEngines] = useState<EnginesInfo | null>(null);
  const [agentsErr, setAgentsErr] = useState<string | null>(null);
  const [selectedAgent, setSelectedAgent] = useState<AgentInfo | null>(null);
  const [ritirato, setRitirato] = useState<string | null>(null);   // sessione legacy aperta
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [sessionsErr, setSessionsErr] = useState<string | null>(null);
  const [activeSession, setActiveSession] = useState<number | null>(null);
  const [messages, setMessages] = useState<RuntimeMessage[]>([]);
  const [msgsErr, setMsgsErr] = useState<string | null>(null);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [query, setQuery] = useState('');
  const [hot, setHot] = useState<number | null>(null);
  const [pin, setPin] = useState<number | null>(null);
  const [daEliminare, setDaEliminare] = useState<ChatSession | null>(null);
  // archivio diviso per agente (ordine PM 26/07): aperta la sezione dell'agente al desk
  const [apertiArchivio, setApertiArchivio] = useState<Set<string>>(new Set());

  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const skipFetchRef = useRef<number | null>(null);
  const sendingRef = useRef(false);
  const requestRef = useRef(0);

  useEffect(() => () => {
    ++requestRef.current;
    sendingRef.current = false;
    abortRef.current?.abort();
  }, []);

  useEffect(() => {
    setAgentsErr(null);
    Bellomberg.agentsList().then(r => {
      setAgents(r?.agents || []);
      setEngines(r?.engines || null);
      if (r?.agents?.length && !selectedAgent) setSelectedAgent(r.agents[0]);
    }).catch(e => {
      // Buco DICHIARATO (regola 14/07): rail vuota muta = falso "nessun agente"
      console.error('agentsList', e);
      setAgentsErr(e?.response?.data?.detail || e?.message || String(e));
    });
  }, []);

  /** L'archivio e' UNICO: tutte le conversazioni di tutti gli agenti, compresi i due
   *  ritirati. Una GET per agente (query indicizzata su specialist, nessun costo). */
  const caricaArchivio = async (ids: string[]) => {
    setSessionsErr(null);
    const esiti = await Promise.allSettled(
      ids.map(id => Bellomberg.chatListSessions(id, 100).then(r => r?.sessions || [])),
    );
    const buchi: string[] = [];
    const tutte: ChatSession[] = [];
    esiti.forEach((e, i) => {
      if (e.status === 'fulfilled') tutte.push(...e.value);
      else buchi.push(ids[i] + ': ' + ((e.reason as any)?.response?.data?.detail || (e.reason as any)?.message || String(e.reason)));
    });
    tutte.sort((a, b) => String(b.last_activity || b.started_at).localeCompare(String(a.last_activity || a.started_at)));
    setSessions(tutte);
    if (buchi.length) setSessionsErr(buchi.join(' · '));   // buco dichiarato, non archivio vuoto
  };

  useEffect(() => {
    if (!agents.length) return;
    caricaArchivio([...agents.map(a => a.id), ...AGENTI_RITIRATI]);
  }, [agents.length]);

  useEffect(() => {
    let alive = true;
    if (!activeSession) { setMessages([]); setMsgsErr(null); setMessagesLoading(false); return; }
    if (skipFetchRef.current === activeSession) { skipFetchRef.current = null; setMessagesLoading(false); return; }
    setMessages([]);
    setMessagesLoading(true);
    setMsgsErr(null);
    Bellomberg.chatGetSession(activeSession).then(s => {
      if (!alive) return;
      setMessages((s.messages || []).map(m => ({
        role: m.role as 'user' | 'assistant',
        content: m.content,
        id: m.id,
        timestamp: m.timestamp,
        storico: true,          // dal DB: nessuna traccia degli strumenti, e la pagina lo dice
        // n.5 del lotto backend (58): i token dallo storico ARRIVANO (misure
        // dal DB). «Non consegnati per lo storico» era diventato falso: erano
        // consegnati, era questo mapping a buttarli (confutatore 03/08). La
        // dichiarazione resta per le sole righe dove il DB ha davvero null.
        tokens: typeof m.tokens_in === 'number' && typeof m.tokens_out === 'number'
          ? { in: m.tokens_in, out: m.tokens_out } : undefined,
      })));
    }).catch(e => {
      if (!alive) return;
      console.error('getSession', e);
      setMessages([]);
      setMsgsErr(e?.response?.data?.detail || e?.message || String(e));
    }).finally(() => { if (alive) setMessagesLoading(false); });
    return () => { alive = false; };
  }, [activeSession]);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages.length]);

  /* la sezione dell'agente scelto al desk si apre da sola, le altre restano chiuse */
  useEffect(() => {
    if (selectedAgent) setApertiArchivio(new Set([selectedAgent.id]));
  }, [selectedAgent?.id]);

  /* ── selezione ──────────────────────────────────────────── */
  const apriSessione = (s: ChatSession) => {
    stopStream();
    const ag = agents.find(a => a.id === s.specialist);
    if (ag) { setSelectedAgent(ag); setRitirato(null); }
    else setRitirato(s.specialist);         // specialista ritirato: sola lettura
    setApertiArchivio(prev => new Set([...prev, s.specialist]));
    setActiveSession(s.id);
    setPin(null); setHot(null);
  };

  const nuovaConversazione = () => {
    stopStream();
    // Niente riga a vuoto nel DB: la sessione nasce col primo messaggio, gia' titolata.
    setActiveSession(null); setMessages([]); setRitirato(null);
    setPin(null); setHot(null);
    setTimeout(() => inputRef.current?.focus(), 50);
  };

  const eliminaConfermata = async () => {
    const s = daEliminare;
    setDaEliminare(null);
    if (!s) return;
    try {
      await Bellomberg.chatDeleteSession(s.id);
      if (activeSession === s.id) { setActiveSession(null); setMessages([]); }
      setSessions(prev => prev.filter(x => x.id !== s.id));
    } catch (e: any) {
      console.error('deleteSession', e);
      setSessionsErr('eliminazione #' + s.id + ' fallita — ' + (e?.response?.data?.detail || e?.message || String(e)));
    }
  };

  const stopStream = () => {
    const stoppedRequest = requestRef.current;
    ++requestRef.current;
    sendingRef.current = false;
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
    setMessages(prev => prev.map(m => m.requestId === stoppedRequest && m.streaming
      ? { ...m, streaming: false, ok: false, errore: 'interrotta da te (STOP o cambio conversazione)' }
      : m));
  };

  /* ── invio + stream ─────────────────────────────────────── */
  const sendMessage = async (messageOverride?: string) => {
    const textToSend = (messageOverride ?? input).trim();
    if (!textToSend || !selectedAgent || streaming || sendingRef.current || messagesLoading || ritirato) return;

    // La guardia precede anche la creazione HTTP della sessione.
    sendingRef.current = true;
    const requestId = ++requestRef.current;
    const isCurrent = () => requestRef.current === requestId;
    setStreaming(true);

    let sid = activeSession;
    if (!sid) {
      try {
        // titolo LETTERALE alla nascita: nessuna conversazione si chiama piu' "Chat con X"
        const s = await Bellomberg.chatCreateSession(selectedAgent.id, titoloDaMessaggio(textToSend));
        if (!isCurrent()) return;
        sid = s.session_id;
        skipFetchRef.current = sid;
        setActiveSession(sid);
      } catch (e: any) {
        if (!isCurrent()) return;
        setMsgsErr('sessione non creata — ' + (e?.response?.data?.detail || e?.message || String(e)));
        sendingRef.current = false;
        setStreaming(false);
        return;
      }
    }

    const userMsg = textToSend;
    if (!messageOverride) setInput('');
    setPin(null); setHot(null);
    setMessages(prev => [
      ...prev,
      { role: 'user', content: userMsg },
      { role: 'assistant', content: '', streaming: true, calls: [], marks: [], flow: [], durata: null, requestId },
    ]);
    setStreaming(true);

    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const t0 = performance.now();
    const ora = () => performance.now() - t0;
    let ultimoCampione = -1;

    /** Aggiorna la risposta di questa richiesta, mai l'ultimo messaggio di un altro desk. */
    const patch = (f: (m: RuntimeMessage) => RuntimeMessage) => setMessages(prev => {
      if (!isCurrent()) return prev;
      const index = prev.findIndex(m => m.role === 'assistant' && m.requestId === requestId);
      if (index < 0) return prev;
      const copy = [...prev];
      copy[index] = f(copy[index]);
      return copy;
    });

    try {
      const bbToken = getSessionToken();
      const resp = await fetch(Bellomberg.chatStreamUrl(sid), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json', 'Accept': 'text/event-stream',
          // Hardening #32: lo stream chat e' un POST protetto da X-BB-Token
          ...(bbToken ? { 'X-BB-Token': bbToken } : {}),
        },
        body: JSON.stringify({ message: userMsg }),
        signal: ctrl.signal,
      });
      if (resp.status === 401 || resp.status === 403) { clearSessionAndReload(); throw new Error('HTTP ' + resp.status); }
      if (!resp.ok || !resp.body) {
        const detail = await resp.json().catch(() => null);
        throw new Error('HTTP ' + resp.status + (typeof detail?.detail === 'string' ? ' — ' + detail.detail : ''));
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let terminalEvent = false;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        if (!isCurrent()) { await reader.cancel(); break; }
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split(/\r?\n\r?\n/);
        buffer = events.pop() || '';

        for (const evt of events) {
          if (!evt.trim()) continue;
          let evtName = 'message';
          const dataLines: string[] = [];
          for (const line of evt.split(/\r?\n/)) {
            if (line.startsWith('event:')) evtName = line.slice(6).trim();
            else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''));
          }
          if (dataLines.length === 0) continue;
          const dataStr = dataLines.join('\n');
          let payload: any = null;
          try { payload = JSON.parse(dataStr); } catch { payload = { text: dataStr }; }

          if (evtName === 'delta' && payload?.text) {
            patch(m => {
              const content = m.content + payload.text;
              const t = ora();
              // ritmo del testo campionato ogni ~120 ms: dato misurato, non decorazione
              const flow = (t - ultimoCampione > 120 || !m.flow?.length)
                ? [...(m.flow || []), { t, len: content.length }]
                : m.flow;
              if (flow !== m.flow) ultimoCampione = t;
              return { ...m, content, flow };
            });
          } else if (evtName === 'tool_use_start') {
            patch(m => {
              const n = (m.calls?.length || 0) + 1;
              const call: ToolCall = {
                n, id: String(payload?.tool_id ?? 'tool-' + n),
                name: String(payload?.tool_name ?? '(senza nome)'),
                iteration: Number(payload?.iteration ?? 1),
                t0: ora(),
              };
              return {
                ...m,
                calls: [...(m.calls || []), call],
                marks: [...(m.marks || []), { pos: m.content.length, n }],
              };
            });
          } else if (evtName === 'tool_result') {
            patch(m => ({
              ...m,
              calls: (m.calls || []).map(c => c.id === String(payload?.tool_id) ? {
                ...c,
                t1: ora(),
                ok: payload?.ok !== false,
                bytes: typeof payload?.result_size_bytes === 'number' ? payload.result_size_bytes : undefined,
                preview: typeof payload?.result_preview === 'string' ? payload.result_preview : undefined,
              } : c),
            }));
          } else if (evtName === 'meta') {
            patch(m => ({
              ...m,
              meta: {
                model: payload?.model, n_tools_available: payload?.n_tools_available,
                max_tool_iterations: payload?.max_tool_iterations,
                mandato: payload?.mandato ?? null,
              },
            }));
          } else if (evtName === 'done') {
            terminalEvent = true;
            patch(m => ({
              ...m,
              streaming: false,
              durata: ora(),
              ok: payload?.ok !== false,
              iterations: typeof payload?.iterations === 'number' ? payload.iterations : undefined,
              tokens: {
                in: typeof payload?.tokens_in === 'number' ? payload.tokens_in : null,
                out: typeof payload?.tokens_out === 'number' ? payload.tokens_out : null,
              },
            }));
          } else if (evtName === 'error') {
            terminalEvent = true;
            patch(m => ({ ...m, streaming: false, ok: false, errore: String(payload?.message || 'errore non descritto') }));
          }
        }
      }
      if (isCurrent() && !terminalEvent) {
        patch(m => ({ ...m, streaming: false, ok: false, durata: ora(), errore: 'stream terminato senza conferma del server; risposta incompleta' }));
      }
    } catch (e: any) {
      if (e.name !== 'AbortError') {
        console.error('[Chat] network error', e);
        patch(m => ({ ...m, streaming: false, ok: false, durata: ora(), errore: 'rete — ' + (e?.message || String(e)) }));
      } else {
        patch(m => ({ ...m, streaming: false, durata: ora(), errore: 'interrotta da te (STOP)' }));
      }
    } finally {
      if (isCurrent()) {
        sendingRef.current = false;
        setStreaming(false);
        abortRef.current = null;
      }
      if (isCurrent() && selectedAgent && sid) {
        Bellomberg.chatListSessions(selectedAgent.id, 100)
          .then(r => setSessions(prev => {
            const altre = prev.filter(s => s.specialist !== selectedAgent.id);
            const tutte = [...altre, ...(r?.sessions || [])];
            tutte.sort((a, b) => String(b.last_activity || b.started_at).localeCompare(String(a.last_activity || a.started_at)));
            return tutte;
          }))
          .catch(() => { /* l'archivio resta quello di prima: nessun dato inventato */ });
      }
    }
  };

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  };

  /* ── derivati ───────────────────────────────────────────── */
  const ultimaRisposta = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) if (messages[i].role === 'assistant') return messages[i];
    return null;
  }, [messages]);

  const calls = ultimaRisposta?.calls || [];
  const modello = ultimaRisposta?.meta?.model || selectedAgent?.model || engines?.chat || null;
  const nStrumenti = ultimaRisposta?.meta?.n_tools_available ?? null;
  const maxIter = ultimaRisposta?.meta?.max_tool_iterations ?? null;

  const archivio = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return sessions;
    return sessions.filter(s =>
      (s.title || '').toLowerCase().includes(q) ||
      (agents.find(a => a.id === s.specialist)?.name || s.specialist).toLowerCase().includes(q));
  }, [sessions, query, agents]);

  const sessioneAttiva = sessions.find(s => s.id === activeSession) || null;
  const agenteCorrente = selectedAgent;
  const colore = agenteCorrente?.color || '#FFA51E';

  return (
    <div className="obsx f3d">
      {/* ── barra d'assetto ── */}
      <div className="bar">
        <span className="lab">Desk conversazionale</span>
        <span className="sep" />
        <span className="k">Agente</span>
        <span className="v">
          {ritirato
            ? <b className="ko">{ritirato.toUpperCase()} · RITIRATO</b>
            : agenteCorrente
              ? <><b style={{ color: colore }}>{agenteCorrente.name.toUpperCase()}</b> · {agenteCorrente.role}</>
              : <b className="ko">nessuno</b>}
        </span>
        <span className="sep" />
        <span className="k">Motore</span>
        <span className="v">{modello ? <b>{modello}</b> : <b className="ko">N.D. — nessun modello nel payload</b>}</span>
        <span className="sep" />
        <span className="k">Arsenale</span>
        <span className="v">
          {nStrumenti != null
            ? <><b>{nStrumenti}</b> strumenti{calls.length > 0 && <> · <b style={{ color: '#29D3F2' }}>{calls.length} accesi</b></>}</>
            : <span style={{ fontWeight: 600, color: '#73829F' }}>si legge dallo stream al primo messaggio</span>}
        </span>
        <span className="sep" />
        <span className="k">Archivio</span>
        <span className="v"><b>{sessions.length}</b> conversazioni</span>
        <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 7 }}>
          <StatoRisposta m={ultimaRisposta} streaming={streaming} />
        </span>
      </div>

      <div className="body">
        {/* ══ sinistra: desk + archivio ══ */}
        <div className="cL">
          <div className="p3">
            <span className="tick tl" /><span className="tick tr" />
            <span className="tick bl" /><span className="tick br" />
            <div className="p3h am">Desk <span className="side">{agents.length} specialisti</span></div>
            {agentsErr && agents.length === 0 ? (
              <div className="dec ko">
                <b>AGENTI N.D.</b> — {agentsErr}. Backend in errore: ricarica la pagina.
              </div>
            ) : agents.map(a => (
              <button key={a.id} className={'dk' + (a.id === agenteCorrente?.id && !ritirato ? ' on' : '')}
                      style={a.id === agenteCorrente?.id && !ritirato ? { borderLeftColor: a.color } : undefined}
                      onClick={() => { setSelectedAgent(a); setRitirato(null); nuovaConversazione(); }}>
                <span className="ld" style={{ background: a.color, boxShadow: '0 0 7px ' + a.color }} />
                <span style={{ minWidth: 0 }}>
                  <span className="nm" style={{ color: a.id === agenteCorrente?.id && !ritirato ? a.color : '#8D9FC4', display: 'block' }}>
                    {a.name.toUpperCase()}
                  </span>
                  <span className="rl" style={{ display: 'block' }}>{a.role}</span>
                </span>
                <span className="rt" title={sessions.filter(s => s.specialist === a.id).length + ' conversazioni con ' + a.name}>
                  <Istogramma n={sessions.filter(s => s.specialist === a.id).length}
                              max={Math.max(1, ...agents.map(x => sessions.filter(s => s.specialist === x.id).length))}
                              colore={a.id === agenteCorrente?.id ? a.color : null} />
                  <span className="tl" style={{ display: 'block' }}>
                    <b>{sessions.filter(s => s.specialist === a.id).length}</b>
                  </span>
                </span>
              </button>
            ))}
          </div>

          <div className="p3" style={{ flex: 1, minHeight: 0 }}>
            <span className="tick tl" /><span className="tick tr" />
            <span className="tick bl" /><span className="tick br" />
            <div className="p3h">
              Archivio
              <span className="side">
                {sessions.filter(s => AGENTI_RITIRATI.includes(s.specialist)).length} legacy
              </span>
              <button className="nuova" onClick={nuovaConversazione}
                      title="Nuova conversazione — si salva da sola al primo invio"
                      aria-label="nuova conversazione">
                <Plus size={11} />
              </button>
            </div>
            <div className="srch">
              <Search size={10} style={{ color: '#73829F', flexShrink: 0 }} />
              <input value={query} onChange={e => setQuery(e.target.value)}
                     placeholder="cerca fra le conversazioni…" aria-label="cerca nell'archivio" />
              <span className="cnt">{archivio.length}/{sessions.length}</span>
            </div>
            <div className="arch">
              {sessionsErr && (
                <div className="dec ko">
                  <b>ARCHIVIO PARZIALE</b> — {sessionsErr}. Lo storico non e' perso: backend in errore.
                </div>
              )}
              {archivio.length === 0 && !sessionsErr ? (
                <div className="vuoto">
                  {query ? <>Nessuna conversazione per <b>“{query}”</b>.</> : <>Nessuna conversazione in archivio.</>}
                </div>
              ) : (
                <Archivio righe={archivio} agents={agents} attiva={activeSession}
                          onApri={apriSessione} onElimina={setDaEliminare}
                          aperti={apertiArchivio} ricerca={query.trim().length > 0}
                          onApri1={id => setApertiArchivio(prev => {
                            const n = new Set(prev);
                            if (n.has(id)) n.delete(id); else n.add(id);
                            return n;
                          })} />
              )}
            </div>
          </div>
        </div>

        {/* ══ centro: nastro + conversazione + composer ══ */}
        <div className="cM">
          <NastroEsecuzione
            calls={calls}
            flow={ultimaRisposta?.flow || []}
            durata={ultimaRisposta?.durata ?? null}
            streaming={!!ultimaRisposta?.streaming}
            disponibile={!!ultimaRisposta && !ultimaRisposta.storico}
            motivoAssenza={!ultimaRisposta
              ? 'nessuna risposta in questa conversazione: manda un messaggio e il nastro si accende.'
              : undefined}
            hot={hot} onHot={setHot} pin={pin} onPin={setPin}
          />

          <div className="p3 am" style={{ flex: 1, minHeight: 0 }}>
            <span className="tick tl" /><span className="tick tr" />
            <span className="tick bl" /><span className="tick br" />
            <div className="p3h am">
              {sessioneAttiva
                ? <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{sessioneAttiva.title}</span>
                : 'Nuova conversazione'}
              <span className="side">
                {sessioneAttiva
                  ? <>#{sessioneAttiva.id} · {dataIt(sessioneAttiva.last_activity || sessioneAttiva.started_at)}
                      {sessioneAttiva.msg_count != null && <> · {sessioneAttiva.msg_count} messaggi</>}</>
                  : 'si salva da sola al primo invio'}
              </span>
            </div>
            <div className="conv" ref={scrollRef}>
              {msgsErr && (
                <div className="dec ko">
                  <b>CONVERSAZIONE N.D.</b> — {msgsErr}. I messaggi ci sono: e' il backend a non risponderci.
                </div>
              )}
              {!agenteCorrente && !ritirato && !agentsErr && (
                <div className="vuoto">Seleziona uno specialista dal desk.</div>
              )}
              {messagesLoading && <div className="vuoto">Caricamento conversazione…</div>}
              {messages.length === 0 && agenteCorrente && !msgsErr && !messagesLoading && (
                <Ingresso agente={agenteCorrente} modello={modello}
                          disabled={streaming || messagesLoading || !!ritirato} onPrompt={p => sendMessage(p)} />
              )}
              {messages.map((m, i) => (
                <Turno key={m.id ?? 'live-' + i} m={m} agente={agenteCorrente}
                       hot={hot} pin={pin} onHot={setHot} onPin={setPin} />
              ))}
            </div>
          </div>

          <div className="comp">
            {messages.length > 0 && agenteCorrente && !ritirato && <details className="chat-followups">
              <summary>Domande del desk e titoli del portafoglio</summary>
              <ChatSuggestions agent={agenteCorrente.id} name={agenteCorrente.name}
                disabled={streaming || messagesLoading} onPrompt={p => void sendMessage(p)} />
            </details>}
            <div className="field">
              <textarea ref={inputRef} value={input} rows={2}
                        onChange={e => setInput(e.target.value)} onKeyDown={onKey}
                         disabled={!agenteCorrente || streaming || messagesLoading || !!ritirato}
                        placeholder={ritirato
                          ? 'Conversazione in sola lettura: lo specialista ' + ritirato + ' e\' stato ritirato dal desk'
                          : agenteCorrente
                            ? 'Chiedi a ' + agenteCorrente.name + '… — Invio manda, Maiusc+Invio va a capo'
                            : 'Seleziona uno specialista per iniziare'} />
              {streaming ? (
                <button className="send stop" onClick={stopStream}><Square size={11} /> Ferma</button>
              ) : (
                <button className="send" onClick={() => sendMessage()}
                        disabled={!agenteCorrente || !input.trim() || messagesLoading || !!ritirato}>
                  <Send size={11} /> Manda
                </button>
              )}
            </div>
            <div className="flow">
              <Flusso m={ultimaRisposta} />
              <span className="r">
                <span>{input.length} caratteri</span>
                <span style={{ color: '#22304F' }}>|</span>
                <span>{modello || 'modello n.d.'}{maxIter != null && ' · max ' + maxIter + ' iterazioni'}</span>
              </span>
            </div>
          </div>
        </div>

        {/* ══ destra: catena + arsenale + telemetria ══ */}
        <div className="cR">
          <div className="p3 cy" style={{ flex: 1, minHeight: 0 }}>
            <span className="tick tl" /><span className="tick tr" />
            <span className="tick bl" /><span className="tick br" />
            <div className="p3h c">
              Catena di custodia
              <span className="side">{calls.length ? calls.filter(c => c.ok === false).length + ' KO / ' + calls.length : '—'}</span>
            </div>
            <div className="cat">
              <Catena calls={calls} m={ultimaRisposta} hot={hot} pin={pin} onHot={setHot} onPin={setPin} />
              {nStrumenti != null && (
                <div className="ars">
                  <div className="h">
                    Arsenale{agenteCorrente ? ' di ' + agenteCorrente.name : ''}
                    <b>{calls.length} / {nStrumenti} accesi</b>
                  </div>
                  <div className="grid">
                    {Array.from({ length: Math.min(nStrumenti, 60) }, (_, k) => (
                      <i key={k} className={k < calls.length ? 'on' : ''} />
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="p3" style={{ flex: '0 0 auto' }}>
            <span className="tick tl" /><span className="tick tr" />
            <span className="tick bl" /><span className="tick br" />
            <div className="p3h">Telemetria risposta</div>
            <Telemetria m={ultimaRisposta} maxIter={maxIter} modello={modello} />
          </div>
        </div>
      </div>

      <ConfirmDialog
        open={!!daEliminare}
        title="Eliminare la conversazione?"
        intro="Sparisce dal DB con tutti i suoi messaggi. Non si torna indietro."
        rows={daEliminare ? [
          { k: 'CONVERSAZIONE', v: '#' + daEliminare.id + ' · ' + daEliminare.title },
          { k: 'SPECIALISTA', v: (agents.find(a => a.id === daEliminare.specialist)?.name || daEliminare.specialist).toUpperCase() },
          { k: 'MESSAGGI', v: daEliminare.msg_count != null ? String(daEliminare.msg_count) : 'n.d.', tone: 'crimson' },
        ] : []}
        confirmLabel="ELIMINA"
        tone="crimson"
        onConfirm={eliminaConfermata}
        onCancel={() => setDaEliminare(null)}
      />
    </div>
  );
}

/* ════════════════════════ pezzi ════════════════════════ */

function dataIt(s?: string) {
  if (!s) return 'data n.d.';
  const d = new Date(String(s).replace(' ', 'T'));
  if (isNaN(d.getTime())) return String(s);
  return d.toLocaleDateString('it-IT', { day: '2-digit', month: '2-digit', year: '2-digit' });
}

function gruppoData(s?: string) {
  if (!s) return 'SENZA DATA';
  const d = new Date(String(s).replace(' ', 'T'));
  if (isNaN(d.getTime())) return 'SENZA DATA';
  const gg = Math.floor((Date.now() - d.getTime()) / 864e5);
  if (gg <= 0) return 'OGGI';
  if (gg <= 1) return 'IERI';
  if (gg <= 7) return 'ULTIMI 7 GIORNI';
  if (gg <= 31) return 'ULTIMI 30 GIORNI';
  return 'PRIMA';
}

function Istogramma({ n, max, colore }: { n: number; max: number; colore: string | null }) {
  const h = Math.max(2, Math.round((n / Math.max(1, max)) * 10));
  return (
    <span className="fdr">
      {Array.from({ length: 7 }, (_, k) => (
        <i key={k} style={{
          height: Math.max(2, Math.min(10, Math.round(h * (0.55 + 0.55 * Math.abs(Math.sin((n + k) * 1.9)))))),
          background: colore ? colore + '99' : undefined,
        }} />
      ))}
    </span>
  );
}

/** L'archivio e' diviso PER AGENTE (ordine del PM 26/07): ogni specialista ha la sua
 *  sezione, i ritirati in coda. Aperta quella dell'agente al desk; con la ricerca
 *  attiva si aprono tutte le sezioni che hanno risultati. */
function Archivio({ righe, agents, attiva, onApri, onElimina, aperti, onApri1, ricerca }: {
  righe: ChatSession[]; agents: AgentInfo[]; attiva: number | null;
  onApri: (s: ChatSession) => void; onElimina: (s: ChatSession) => void;
  aperti: Set<string>; onApri1: (id: string) => void; ricerca: boolean;
}) {
  const ordine: { id: string; nome: string; colore: string; legacy: boolean }[] = [
    ...agents.map(a => ({ id: a.id, nome: a.name, colore: a.color, legacy: false })),
    ...Array.from(new Set(righe.map(s => s.specialist)))
      .filter(id => !agents.some(a => a.id === id))
      .map(id => ({ id, nome: id, colore: '#5A6480', legacy: true })),
  ];

  return (
    <>
      {ordine.map(g => {
        const sue = righe.filter(s => s.specialist === g.id);
        if (ricerca && sue.length === 0) return null;      // in ricerca spariscono i gruppi vuoti
        const aperto = ricerca ? sue.length > 0 : aperti.has(g.id);
        return (
          <div key={g.id}>
            <button className={'grp' + (aperto ? ' on' : '')} onClick={() => onApri1(g.id)}
                    aria-expanded={aperto}>
              <span className="ld" style={{ background: g.colore, boxShadow: aperto ? '0 0 7px ' + g.colore : 'none' }} />
              <span className="gnm" style={aperto ? { color: g.colore } : undefined}>{g.nome.toUpperCase()}</span>
              {g.legacy && <span className="lg">RITIRATO</span>}
              <span className="gn">{sue.length}</span>
              <span className="chev">{aperto ? '▾' : '▸'}</span>
            </button>
            {aperto && (sue.length === 0
              ? <div className="vuoto" style={{ padding: '8px 10px', fontSize: 9 }}>Nessuna conversazione con {g.nome}.</div>
              : <RigheArchivio righe={sue} agents={agents} attiva={attiva} onApri={onApri} onElimina={onElimina} />)}
          </div>
        );
      })}
    </>
  );
}

function RigheArchivio({ righe, agents, attiva, onApri, onElimina }: {
  righe: ChatSession[]; agents: AgentInfo[]; attiva: number | null;
  onApri: (s: ChatSession) => void; onElimina: (s: ChatSession) => void;
}) {
  const out: JSX.Element[] = [];
  righe.forEach(s => {
    const ag = agents.find(a => a.id === s.specialist);
    const legacy = !ag;
    const colore = ag?.color || '#5A6480';
    const vuota = s.msg_count === 0;
    const generico = titoloGenerico(s.title);
    out.push(
      <div className="sess-wrap" key={s.id} style={{ position: 'relative' }}>
        <button className={'sess' + (attiva === s.id ? ' on' : '')} onClick={() => onApri(s)}>
          <span className="ld" style={vuota ? { background: '#22304F' }
            : { background: colore, boxShadow: '0 0 6px ' + colore + '99' }} />
          <span style={{ minWidth: 0, flex: 1 }}>
            <span className={'ttl' + (vuota || generico ? ' gen' : '')} style={{ display: 'block' }}>
              {vuota && generico ? 'Conversazione aperta e mai usata' : s.title}
            </span>
            <span className="meta">
              {/* l'agente non si ripete: e' il titolo della sezione */}
              {['OGGI', 'IERI'].includes(gruppoData(s.last_activity || s.started_at)) && (
                <span className="who" style={{ color: '#6E5A2E' }}>
                  {gruppoData(s.last_activity || s.started_at)}
                </span>
              )}
              <span>{dataIt(s.last_activity || s.started_at)}</span>
              <span>{s.msg_count != null ? s.msg_count + ' msg' : 'msg n.d.'}</span>
              {legacy && <span className="lg">SOLA LETTURA</span>}
              {generico && !vuota && <span style={{ fontWeight: 600, color: '#73829F' }}>· da titolare</span>}
            </span>
          </span>
        </button>
        <button className="del" title="Elimina" aria-label={'elimina conversazione ' + s.id}
                onClick={e => { e.stopPropagation(); onElimina(s); }}>
          <Trash2 size={10} />
        </button>
      </div>,
    );
  });
  return <>{out}</>;
}

function StatoRisposta({ m, streaming }: { m: RuntimeMessage | null; streaming: boolean }) {
  if (streaming) return (<><span className="ld a" /><span className="v" style={{ fontSize: 9, color: '#8D9FC4' }}>in ascolto…</span></>);
  if (!m) return <span className="v" style={{ fontSize: 9, fontWeight: 600, color: '#73829F' }}>nessuna risposta in corso</span>;
  if (m.errore) return (<><span className="ld r" /><span className="v ko" style={{ fontSize: 9 }}>{m.errore}</span></>);
  if (m.storico) return <span className="v" style={{ fontSize: 9, fontWeight: 600, color: '#73829F' }}>conversazione dall'archivio</span>;
  if (m.ok === false) return (<><span className="ld r" /><span className="v ko" style={{ fontSize: 9 }}>stream chiuso in errore (done.ok = false)</span></>);
  return (<><span className="ld g" /><span className="v" style={{ fontSize: 9, color: '#8D9FC4' }}>
    stream chiuso regolarmente{m.iterations != null && ' · ' + m.iterations + ' iterazion' + (m.iterations === 1 ? 'e' : 'i')}
  </span></>);
}

function Flusso({ m }: { m: RuntimeMessage | null }) {
  const flow = m?.flow || [];
  if (flow.length < 3) return <span style={{ fontWeight: 600, color: '#73829F' }}>FLUSSO — nessun campione</span>;
  const rates = flow.map((f, i) => i ? (f.len - flow[i - 1].len) / Math.max(1, f.t - flow[i - 1].t) : 0);
  const max = Math.max(...rates, 1e-6);
  const car = flow[flow.length - 1].len;
  const sec = (flow[flow.length - 1].t - flow[0].t) / 1000;
  return (
    <>
      <span style={{ color: '#29D3F2' }}>FLUSSO</span>
      <span className="sp">
        {rates.slice(-26).map((r, k) => <i key={k} style={{ height: Math.max(2, Math.round(11 * (r / max))) }} />)}
      </span>
      <span>
        {car.toLocaleString('it-IT')} caratteri in {sec.toLocaleString('it-IT', { maximumFractionDigits: 1 })} s
        {sec > 0 && ' · ' + Math.round(car / sec) + ' car/s'}
      </span>
      {m?.tokens && (
        <>
          <span style={{ color: '#22304F' }}>|</span>
          <span>{m.tokens.in != null && m.tokens.out != null
            ? `in ${m.tokens.in.toLocaleString('it-IT')} · out ${m.tokens.out.toLocaleString('it-IT')}`
            : 'token non noti o parziali'}</span>
        </>
      )}
    </>
  );
}

function Catena({ calls, m, hot, pin, onHot, onPin }: {
  calls: ToolCall[]; m: RuntimeMessage | null;
  hot: number | null; pin: number | null;
  onHot: (n: number | null) => void; onPin: (n: number | null) => void;
}) {
  if (!m) return (
    /* Righe SINGOLE: la colonna e' larga 330px, cioe' ~45 caratteri a 12px.
       Erano tre righe di prosa in un pannello che, a riposo, non ha altro da
       dire. */
    <div className="catvuoto">
      Nessuna risposta in corso.
      <div className="nota1">QUI — strumento, esito, byte, anteprima.</div>
    </div>
  );
  if (m.storico) return (
    <div className="catvuoto">
      <b>TRACCIA NON SALVATA</b> — il backend conserva il testo della risposta, non gli
      strumenti che l'hanno prodotta: per le conversazioni gia' in archivio la catena non
      esiste e non viene inventata. (Richiesta aperta nel ponte.)
    </div>
  );
  if (calls.length === 0) return (
    <div className="catvuoto">
      {m.streaming ? <>In attesa: l'agente non ha ancora chiamato strumenti.</> : (
        <><span className="ko">NESSUNO STRUMENTO CHIAMATO</span> — questa risposta e' uscita dal solo
          modello, senza interrogare un dato. In casa i numeri arrivano dai tool: <b>leggila con quel metro</b>.</>
      )}
    </div>
  );
  const sel = pin ?? hot;
  return (
    <>
      {calls.map(c => (
        <div key={c.id}
             className={'ev' + (sel === c.n ? ' hot' : '') + (c.ok === false ? ' ko' : '')}
             onMouseEnter={() => onHot(c.n)} onMouseLeave={() => onHot(null)}
             onClick={() => onPin(pin === c.n ? null : c.n)}>
          <div className="r1">
            <span className="ix">[{c.n}]</span>
            <span className={'ld ' + (c.ok === false ? 'r' : c.ok == null ? 'a' : 'g')} />
            <span className="tn">{c.name}</span>
            <span className="kb">{fmtKB(c.bytes) ?? (c.ok == null ? 'in corso…' : 'n.d.')}</span>
          </div>
          <div className="r2">
            <span className="arg">iterazione {c.iteration}</span>
            <span className="rt">{c.t1 != null ? fmtMs(c.t1 - c.t0) : fmtMs(c.t0) + ' dall\'avvio'}</span>
          </div>
          {c.ok === false && (
            <div className="warn">▲ lo strumento ha risposto con un ERRORE dichiarato: quello che
              l'agente scrive qui sotto non poggia su questo dato.</div>
          )}
          {sel === c.n && c.preview && <div className="prev">{c.preview}</div>}
        </div>
      ))}
    </>
  );
}

function Telemetria({ m, maxIter, modello }: {
  m: RuntimeMessage | null; maxIter: number | null; modello: string | null;
}) {
  const iter = m?.iterations ?? null;
  const cap = maxIter ?? 8;
  return (
    <div className="tel">
      <div>
        <div className="telrow">Iterazioni tool
          {iter != null ? <b className="am">{iter} / {maxIter ?? '?'}</b> : <b className="nd">n.d.</b>}
        </div>
        <div className="iter">
          {Array.from({ length: Math.min(cap, 12) }, (_, k) => (
            <i key={k} className={iter != null && k < iter ? 'on' : ''} />
          ))}
        </div>
      </div>
      <div className="telrow">Token in / out
        {m?.tokens
          ? (m.tokens.in != null && m.tokens.out != null
              ? <b>{m.tokens.in.toLocaleString('it-IT')} / {m.tokens.out.toLocaleString('it-IT')}</b>
              : <b className="nd">non noti — stream morto prima delle metriche</b>)
          : <b className="nd">{!m ? 'n.d.'
              : m.storico ? 'non consegnati per lo storico'
              /* «a fine risposta» SOLO mentre lo stream e' vivo: e' l'unico stato
                 in cui i token arriveranno davvero. A stream chiuso senza `done`
                 (STOP, rete caduta, troncamento) prometteva un numero su una
                 risposta gia' finita (confutatore 03/08, BASSA (a) — MASTER
                 §9-unquadragies-octies). La frase e' al PRESENTE di proposito:
                 «non arriveranno mai» sarebbe falsa nella finestra fra l'evento
                 `error` e il `done` che lo segue sempre (fra i due il backend fa
                 una scrittura SQLite e un to_thread: non e' un istante). */
              : m.streaming ? 'a fine risposta'
                : 'non arrivati: manca l’evento done'}</b>}
      </div>
      <div className="telrow">Durata
        {m?.durata != null ? <b>{fmtMs(m.durata)}</b> : <b className="nd">n.d.</b>}
      </div>
      {/* Il motore e' un DATO, non una nota: sta in una riga come gli altri. */}
      {modello && <div className="telrow">Motore<b>{modello}</b></div>}
      {/* Note in RIGA SINGOLA e prefissate, forma di F6 (lotto chiarezza 28/07):
          un paragrafo di commento in mezzo ai numeri e' cio' che il PM ha chiesto
          di togliere. Erano tre frasi in un blocco di quattro righe.
          ⚠️ E una di quelle frasi era FALSA: diceva «il costo in euro non c'e'
          perche' il backend non lo consegna». `cost_eur` viaggia sull'evento
          `done` dal lotto backend del 26/07 sera-6, applicato il 27/07 (ponte).
          Un buco dichiarato che non e' piu' un buco non e' una cautela: e' una
          bugia con l'aria di essere prudente. Ora dice il vero — il campo c'e',
          questa pagina non lo legge ancora — e la voce di consumo e' a TODO. */}
      <div className="telnote">BASE — token dal backend, durata da questa pagina.</div>
      <div className="telnote">COSTO — <b>cost_eur</b> arriva su done, non ancora reso.</div>
    </div>
  );
}

/* ── un turno della conversazione ── */
function Turno({ m, agente, hot, pin, onHot, onPin }: {
  m: RuntimeMessage; agente: AgentInfo | null;
  hot: number | null; pin: number | null;
  onHot: (n: number | null) => void; onPin: (n: number | null) => void;
}) {
  if (m.role === 'user') {
    return (
      <div className="pmq">
        <div className="tag">PM{m.timestamp && <span style={{ display: 'block', fontWeight: 600, color: '#73829F', fontSize: 9 }}>{oraIt(m.timestamp)}</span>}</div>
        <div className="q">{m.content}</div>
      </div>
    );
  }
  const colore = agente?.color || '#FFA51E';
  const nomiUsati = new Set((m.calls || []).map(c => c.name));
  return (
    <div className="turn">
      <div className="ansh">
        <span className="ld" style={{ background: colore, boxShadow: '0 0 7px ' + colore }} />
        <span className="nm" style={{ color: colore }}>{(agente?.name || 'AGENTE').toUpperCase()}</span>
        {m.timestamp && <span className="chip n">{dataIt(m.timestamp)} {oraIt(m.timestamp)}</span>}
        {m.streaming && <span className="chip a">in scrittura…</span>}
        {!!m.calls?.length && (
          <span className="chip c" style={{ marginLeft: 'auto' }}>
            FIRMATA DA {m.calls.length} STRUMENT{m.calls.length === 1 ? 'O' : 'I'}
          </span>
        )}
        {!m.streaming && !m.storico && !m.calls?.length && (
          <span className="chip a" style={{ marginLeft: 'auto' }}>NESSUNO STRUMENTO</span>
        )}
        {m.tokens && (m.tokens.out != null
          ? <span className="chip n">{m.tokens.out.toLocaleString('it-IT')} token</span>
          : <span className="chip a">TOKEN NON NOTI</span>)}
      </div>
      <Corpo m={m} hot={hot} pin={pin} onHot={onHot} onPin={onPin} nomiUsati={nomiUsati} />
      <div className="time" title={m.meta?.mandato?.impronta}>
        {m.meta?.mandato
          ? `Mandato ${m.meta.mandato.impronta.slice(0, 8)} · ${m.meta.mandato.origine} · dichiarato il ${m.meta.mandato.dichiarato_il || 'n.d.'}`
          : 'Provenienza del mandato non disponibile'}
      </div>
      {m.errore && (
        <div className="dec ko" style={{ margin: '8px 0 0' }}>
          <b>RISPOSTA INTERROTTA</b> — {m.errore}. Quello che leggi sopra e' parziale.
        </div>
      )}
    </div>
  );
}

function oraIt(s?: string) {
  if (!s) return '';
  const d = new Date(String(s).replace(' ', 'T'));
  return isNaN(d.getTime()) ? '' : d.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' });
}

/** Il corpo della risposta, spezzato nei punti in cui l'agente ha chiamato uno strumento:
 *  il richiamo [n] non e' decorativo, sta esattamente dove lo stream l'ha visto passare. */
function Corpo({ m, hot, pin, onHot, onPin, nomiUsati }: {
  m: RuntimeMessage; hot: number | null; pin: number | null;
  onHot: (n: number | null) => void; onPin: (n: number | null) => void;
  nomiUsati: Set<string>;
}) {
  const sel = pin ?? hot;
  if (!m.content && m.streaming) return <div className="prose" style={{ fontWeight: 600, color: '#73829F' }}>…</div>;
  if (!m.content && !m.errore) return <div className="prose" style={{ fontWeight: 600, color: '#73829F' }}>(risposta vuota)</div>;

  const marks = m.marks || [];
  const pezzi: JSX.Element[] = [];
  let cur = 0;
  const tappe = [...new Set(marks.map(k => k.pos))].sort((a, b) => a - b);
  tappe.forEach(pos => {
    const testo = m.content.slice(cur, pos);
    if (testo.trim()) pezzi.push(<Prosa key={'t' + cur} md={testo} nomiUsati={nomiUsati} sel={sel} onHot={onHot} m={m} />);
    const qui = marks.filter(k => k.pos === pos);
    pezzi.push(
      <div className="refrow" key={'r' + pos}>
        <span className="lb">strumenti</span>
        {qui.map(k => {
          const c = (m.calls || []).find(x => x.n === k.n);
          return (
            <button key={k.n}
                    className={'ref' + (sel === k.n ? ' hot' : '') + (c?.ok === false ? ' ko' : '')}
                    onMouseEnter={() => onHot(k.n)} onMouseLeave={() => onHot(null)}
                    onClick={() => onPin(pin === k.n ? null : k.n)}
                    title="Vedi la chiamata nella catena di custodia e sul nastro">
              <Wrench size={9} /> [{k.n}] {c?.name || '—'}
              {c?.bytes != null && <span style={{ opacity: .7 }}>{fmtKB(c.bytes)}</span>}
            </button>
          );
        })}
      </div>,
    );
    cur = pos;
  });
  const coda = m.content.slice(cur);
  if (coda.trim() || pezzi.length === 0) {
    pezzi.push(<Prosa key={'t' + cur} md={coda} nomiUsati={nomiUsati} sel={sel} onHot={onHot} m={m} />);
  }
  return <>{pezzi}</>;
}

function Prosa({ md, nomiUsati, sel, onHot, m }: {
  md: string; nomiUsati: Set<string>; sel: number | null;
  onHot: (n: number | null) => void; m: RuntimeMessage;
}) {
  // [src: tool] e' la convenzione anti-allucinazione di casa: resta un chip, e quando
  // il nome combacia con uno strumento chiamato davvero si accende insieme alla catena.
  const processed = md.replace(/\[src:\s*([^\]]+)\]/g, (_x, s) => '`__SRC__' + String(s).trim() + '`');
  return (
    <div className="prose">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code: ({ inline, children, ...rest }: any) => {
            const txt = String(children).replace(/\n$/, '');
            if (txt.startsWith('__SRC__')) {
              const src = txt.slice('__SRC__'.length);
              const call = (m.calls || []).find(c => c.name === src);
              const acceso = call != null && sel === call.n;
              return (
                <span className={'src' + (call ? ' link' : '') + (acceso ? ' hot' : '')}
                      onMouseEnter={() => call && onHot(call.n)}
                      onMouseLeave={() => call && onHot(null)}
                      title={call ? 'strumento [' + call.n + '] della catena' : 'fonte dichiarata dall\'agente'}>
                  src: {src}
                </span>
              );
            }
            if (inline) return <code {...rest}>{children}</code>;
            return <code {...rest}>{children}</code>;
          },
        }}
      >
        {processed}
      </ReactMarkdown>
    </div>
  );
}

function Ingresso({ agente, modello, onPrompt, disabled }: {
  agente: AgentInfo; modello: string | null; onPrompt: (p: string) => void; disabled: boolean;
}) {
  return (
    <div className="intro">
      <span className="ld" style={{ background: agente.color, boxShadow: '0 0 14px ' + agente.color, width: 9, height: 9 }} />
      <div className="big" style={{ color: agente.color }}>{agente.name}</div>
      <div className="rl">{agente.role}</div>
      {/* Nota in RIGA SINGOLA: 64 caratteri stanno nei 430px di `max-width` a 10px
          (0,6em = 6px/car = 71 car per riga). Erano tre righe con tre frasi.
          Il MOTORE non si ripete qui: la barra in alto lo scrive gia' («MOTORE
          claude-sonnet-5»), e su F6 la lezione era che un secondo posto dove
          leggere lo stesso dato non aggiunge informazione, aggiunge rumore.
          Anche «si salva da sola» e' via: sta nell'intestazione del riquadro. */}
      <div className="hint">METODO — ogni numero passa da uno strumento: il nastro li elenca.</div>
      <ChatSuggestions agent={agente.id} name={agente.name} disabled={disabled} onPrompt={onPrompt} />
    </div>
  );
}
