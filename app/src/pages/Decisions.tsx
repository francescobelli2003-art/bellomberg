import { useEffect, useState, useCallback, useMemo, Fragment } from 'react';
import { Bellomberg, Decision, DecisionNote } from '@/lib/api';
import { fmtEUR } from '@/lib/format';
import { leggiNumeroConSegno } from '@/lib/cassa';
import { ChevronDown, ChevronRight, Check, X, Clock, MinusCircle, FlaskConical, Send, Archive, ArchiveRestore } from 'lucide-react';

// F10 v4 — restyle stile C scelto dal PM (17/07, mockup renderizzato F17, regola
// 15/07: "fai anche l'opzione C per F10"): board scura con header arancio, righe con
// sottotitolo, split 50/50 di v3 INVARIATO (era gia' scelta PM). NOVITA' funzionale:
// ARCHIVIA / RIPORTA IN PAGINA manuali e persistenti (archive_override in DB) — il
// click del PM vince sempre sulle regole automatiche (7gg operative / 30gg research).
// Tutto il resto di v3 resta: thread note PM<->AI, bottoni esito, auto-archivio.

const ORANGE = 'text-[#ff8c00]';
const HDR = `${ORANGE} uppercase text-[10px] tracking-[2px]`;

const STATUS_STYLE: Record<string, string> = {
  EXECUTED: 'bg-emerald/20 text-emerald',
  PARTIAL: 'bg-cyan/20 text-cyan',
  PENDING: 'bg-gold/20 text-gold',
  SKIPPED: 'bg-crimson/20 text-crimson',
  EXPIRED: 'bg-crimson/20 text-crimson',
};

const giorniDa = (ts?: string | null) => {
  if (!ts) return null;
  const d = Math.floor((Date.now() - new Date(ts).getTime()) / 86400_000);
  return isFinite(d) ? d : null;
};

export default function Decisions() {
  const [filter, setFilter] = useState<string>('');
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [feedback, setFeedback] = useState('');
  const [outcomePct, setOutcomePct] = useState('');
  const [outcomeEur, setOutcomeEur] = useState('');
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [noteDraft, setNoteDraft] = useState<Record<number, string>>({});
  // F10 opzione A: motivo del veto (obbligatorio — senza testo il bottone resta spento)
  const [vetoReason, setVetoReason] = useState('');
  const [archOpenOps, setArchOpenOps] = useState(false);
  const [archOpenRes, setArchOpenRes] = useState(false);

  const [loadErr, setLoadErr] = useState<string | null>(null);

  const load = useCallback(() => {
    // review 16/07: con limit=100 su 170 decisioni, 10 PENDING operative non arrivavano
    // MAI al client e il contatore archivio sottostimava in silenzio.
    Bellomberg.decisions(filter || undefined, 500)
      .then(r => { setLoadErr(null); setDecisions(r.decisions); })
      .catch((e: any) => setLoadErr(e?.response?.data?.detail || e?.message || String(e)));
  }, [filter]);
  useEffect(() => { load(); }, [load]);

  // F10 v3: il gruppo archivio arriva dal backend ('archived', override incluso);
  // fallback client con le STESSE regole per backend non ancora riavviato.
  const isArchived = (d: Decision) => {
    if (d.archive_override != null) return !!d.archive_override;
    if (typeof d.archived === 'boolean') return d.archived;
    const res = (d.action || '').toUpperCase() === 'RESEARCH';
    if (res) return d.status !== 'PENDING';
    const g = giorniDa(d.timestamp);
    return g != null && g > 7;
  };
  const tutteProposte = decisions.filter(d => (d.action || '').toUpperCase() !== 'RESEARCH');
  const tuttaResearch = decisions.filter(d => (d.action || '').toUpperCase() === 'RESEARCH');
  const proposte = tutteProposte.filter(d => !isArchived(d));
  const archProposte = tutteProposte.filter(d => isArchived(d));
  const research = tuttaResearch.filter(d => !isArchived(d));
  const archResearch = tuttaResearch.filter(d => isArchived(d));

  const toggle = (id: number) => {
    setExpanded(expanded === id ? null : id);
    setFeedback(''); setOutcomePct(''); setOutcomeEur(''); setVetoReason(''); setMsg(null);
  };

  // ⚠ Outcome scritti a mano: l'app gira in en-GB e su <input type="number"> la
  // virgola veniva CANCELLATA senza badInput (12,5 → 125 nel DB). Stessa cura di
  // F7 (lib/cassa.ts), variante col segno: un outcome può essere perdita o pari.
  const letturaPct = useMemo(() => leggiNumeroConSegno(outcomePct), [outcomePct]);
  const letturaEur = useMemo(() => leggiNumeroConSegno(outcomeEur), [outcomeEur]);

  const setStatus = async (d: Decision, status: string) => {
    // un outcome illeggibile non si scarta in silenzio: blocca e spiega
    if (letturaPct && !letturaPct.ok) { setMsg(`Errore Outcome %: ${letturaPct.motivo}`); return; }
    if (letturaEur && !letturaEur.ok) { setMsg(`Errore Outcome €: ${letturaEur.motivo}`); return; }
    setSaving(true); setMsg(null);
    try {
      const body: Record<string, unknown> = { status };
      if (feedback.trim()) body.pm_feedback = feedback.trim();
      if (letturaPct && letturaPct.ok) body.outcome_pct = letturaPct.valore;
      if (letturaEur && letturaEur.ok) body.outcome_eur = letturaEur.valore;
      await Bellomberg.updateDecision(d.id, body);
      setMsg(`Decision #${d.id} aggiornata → ${status}`);
      load();
    } catch (e: any) {
      setMsg('Errore: ' + (e?.response?.data?.detail || e?.message || String(e)));
    } finally {
      setSaving(false);
    }
  };

  // F10 opzione A: veto eterno (SKIPPED + flag; il canale della run lo legge per sempre)
  const setVeto = async (d: Decision) => {
    const motivo = vetoReason.trim();
    if (!motivo) return;
    setSaving(true); setMsg(null);
    try {
      await Bellomberg.vetoDecision(d.id, motivo);
      setVetoReason('');
      setMsg(`⛔ Veto attivo su #${d.id} — eterno finché non lo revochi`);
      load();
    } catch (e: any) {
      setMsg('Errore veto: ' + (e?.response?.data?.detail || e?.message || String(e)));
    } finally {
      setSaving(false);
    }
  };

  const revokeVeto = async (d: Decision) => {
    if (!window.confirm(`Revocare il veto su #${d.id} (${d.ticker})? La run potrà riproporre l'idea.`)) return;
    setSaving(true); setMsg(null);
    try {
      await Bellomberg.revokeDecisionVeto(d.id);
      setMsg(`Veto su #${d.id} revocato`);
      load();
    } catch (e: any) {
      setMsg('Errore revoca: ' + (e?.response?.data?.detail || e?.message || String(e)));
    } finally {
      setSaving(false);
    }
  };

  // F10-C: archivia / riporta in pagina — persistente, vince sull'automatico
  const setArchive = async (d: Decision, archived: boolean | null) => {
    setSaving(true); setMsg(null);
    try {
      await Bellomberg.setDecisionArchive(d.id, archived);
      setMsg(archived === null ? `#${d.id}: archivio di nuovo automatico`
        : archived ? `#${d.id} archiviata` : `#${d.id} riportata in pagina`);
      load();
    } catch (e: any) {
      setMsg('Errore archivio: ' + (e?.response?.data?.detail || e?.message || String(e)));
    } finally {
      setSaving(false);
    }
  };

  // decisione PM 17/07 (opzione B): sulle RESEARCH un bottone = un significato —
  // ARCHIVIA chiude DAVVERO (status EXPIRED: la run la molla), RIPORTA riapre
  // DAVVERO (status PENDING: la run la riprende al giro dopo). L'override resta
  // per le operative; sulle research si azzera per lasciare comandare lo status.
  const closeResearch = async (d: Decision) => {
    setSaving(true); setMsg(null);
    try {
      await Bellomberg.updateDecision(d.id, { status: 'EXPIRED' });
      if (d.archive_override != null) await Bellomberg.setDecisionArchive(d.id, null);
      setMsg(`#${d.id} ricerca chiusa e archiviata: la run non la lavora piu'`);
      load();
    } catch (e: any) {
      setMsg('Errore: ' + (e?.response?.data?.detail || e?.message || String(e)));
    } finally {
      setSaving(false);
    }
  };
  const reopenResearch = async (d: Decision) => {
    setSaving(true); setMsg(null);
    try {
      await Bellomberg.updateDecision(d.id, { status: 'PENDING' });
      if (d.archive_override != null) await Bellomberg.setDecisionArchive(d.id, null);
      setMsg(`#${d.id} ricerca riaperta: la run la riprende al prossimo giro`);
      load();
    } catch (e: any) {
      setMsg('Errore: ' + (e?.response?.data?.detail || e?.message || String(e)));
    } finally {
      setSaving(false);
    }
  };

  const sendNote = async (d: Decision) => {
    const testo = (noteDraft[d.id] || '').trim();
    if (!testo) return;
    setSaving(true); setMsg(null);
    try {
      await Bellomberg.addDecisionNote(d.id, testo);
      setNoteDraft(prev => ({ ...prev, [d.id]: '' }));
      load();
    } catch (e: any) {
      setMsg('Errore nota: ' + (e?.response?.data?.detail || e?.message || String(e)));
    } finally {
      setSaving(false);
    }
  };

  // bottone archivio contestuale (attiva -> ARCHIVIA; archiviata -> RIPORTA IN PAGINA)
  const archiveBtn = (d: Decision, archived: boolean, compact = false) => (
    <span className="inline-flex items-center gap-1.5" onClick={e => e.stopPropagation()}>
      {!archived ? (
        <button disabled={saving} onClick={() => setArchive(d, true)}
          title="sposta in archivio (status e storia restano intatti)"
          className={`${compact ? 'px-1.5 py-0.5' : 'px-2.5 py-1'} text-[10px] border border-border text-muted hover:text-[#ffb000] hover:border-[#7a5b1e] inline-flex items-center gap-1 disabled:opacity-50`}>
          <Archive size={10} /> ARCHIVIA
        </button>
      ) : (
        <button disabled={saving} onClick={() => setArchive(d, false)}
          title="riporta la riga tra le attive (resta li' finche' non la riarchivi)"
          className={`${compact ? 'px-1.5 py-0.5' : 'px-2.5 py-1'} text-[10px] border border-[#7a5b1e] bg-[#1c1305] text-[#ffb000] hover:bg-[#33240a] inline-flex items-center gap-1 disabled:opacity-50`}>
          <ArchiveRestore size={10} /> RIPORTA IN PAGINA
        </button>
      )}
      {/* review 17/07 F8: nel compact solo il pin (gia' in riga); ⟲ auto vive nel dettaglio/card */}
      {!compact && d.archive_override != null && (
        <button disabled={saving} onClick={() => setArchive(d, null)}
          title="il posizionamento e' FISSATO da un tuo click: torna alle regole automatiche (7gg/30gg)"
          className="text-[9px] text-faint hover:text-muted underline decoration-dotted disabled:opacity-50">
          fissata dal PM · ⟲ auto
        </button>
      )}
    </span>
  );

  // ---------- colonna sinistra: DECISIONI OPERATIVE ----------
  const detailRowOps = (d: Decision, archived: boolean) => (
    <tr key={`${d.id}-detail`} className="border-b border-border/30 bg-[#0a0a0a]">
      <td colSpan={6} className="p-4">{/* review 17/07: 6 colonne nel layout C */}
        <div className="space-y-3 text-sm">
          <div className="flex gap-6 text-xs text-muted flex-wrap">
            <span>ID: <span className="text-white">#{d.id}</span></span>
            <span>Confidence: <span className="text-white">{d.confidence || 'n/a'}</span></span>
            {d.timing && <span>Timing: <span className="text-white">{d.timing}</span></span>}
            {d.memo_id && <span>Da memo <span className="text-gold">#{d.memo_id}</span> (Archivio memo)</span>}
            {d.closed_at && <span>Chiusa: {d.closed_at.slice(0, 10)}</span>}
          </div>
          <div>
            <div className={`${HDR} mb-1`}>Rationale Capo</div>
            <p className="text-white/90 whitespace-pre-wrap">{d.rationale || 'Nessun rationale salvato.'}</p>
          </div>
          {!!d.veto && (
            <div className="border border-crimson/60 bg-crimson/10 rounded px-3 py-2 text-xs"
                 onClick={e => e.stopPropagation()}>
              <span className="text-crimson font-semibold">⛔ VETO ATTIVO dal {(d.veto_at || '').slice(0, 10)}</span>
              <span className="text-white/90"> — “{d.veto_reason}”: la run DEVE citare il veto #{d.id} con fatti nuovi dichiarati per riproporre.</span>
              <button disabled={saving} onClick={() => revokeVeto(d)}
                className="ml-3 underline decoration-dotted text-muted hover:text-crimson disabled:opacity-50">
                REVOCA VETO (chiede conferma)
              </button>
            </div>
          )}
          {d.pm_feedback && (
            <div>
              <div className={`${HDR} mb-1`}>PM Feedback</div>
              <p className="text-muted whitespace-pre-wrap">{d.pm_feedback}</p>
            </div>
          )}
          {d.outcome_notes && (
            <div>
              <div className={`${HDR} mb-1`}>Outcome Notes</div>
              <p className="text-muted whitespace-pre-wrap">{d.outcome_notes}</p>
            </div>
          )}
          <div className="border-t border-border/40 pt-3 space-y-2" onClick={e => e.stopPropagation()}>
            <div className="flex gap-2 items-center flex-wrap">
              <input value={feedback} onChange={e => setFeedback(e.target.value)}
                placeholder="Motivo / PM feedback (opzionale)"
                className="bg-bg border border-border rounded px-2 py-1 text-xs w-64" />
              <input value={outcomePct} onChange={e => setOutcomePct(e.target.value)}
                placeholder="Outcome %" type="text" inputMode="decimal"
                aria-invalid={!!(letturaPct && !letturaPct.ok)}
                title={letturaPct && !letturaPct.ok ? letturaPct.motivo : undefined}
                className={`bg-bg border rounded px-2 py-1 text-xs w-24 ${letturaPct && !letturaPct.ok ? 'border-crimson text-crimson' : 'border-border'}`} />
              <input value={outcomeEur} onChange={e => setOutcomeEur(e.target.value)}
                placeholder="Outcome €" type="text" inputMode="decimal"
                aria-invalid={!!(letturaEur && !letturaEur.ok)}
                title={letturaEur && !letturaEur.ok ? letturaEur.motivo : undefined}
                className={`bg-bg border rounded px-2 py-1 text-xs w-24 ${letturaEur && !letturaEur.ok ? 'border-crimson text-crimson' : 'border-border'}`} />
            </div>
            <div className="flex gap-2 flex-wrap items-center">
              <button disabled={saving} onClick={() => setStatus(d, 'EXECUTED')}
                className="px-3 py-1 text-xs rounded bg-emerald/20 text-emerald hover:bg-emerald/30 flex items-center gap-1 disabled:opacity-50">
                <Check size={12} /> EXECUTED
              </button>
              <button disabled={saving} onClick={() => setStatus(d, 'PARTIAL')}
                className="px-3 py-1 text-xs rounded bg-cyan/20 text-cyan hover:bg-cyan/30 flex items-center gap-1 disabled:opacity-50">
                <MinusCircle size={12} /> PARTIAL
              </button>
              <button disabled={saving} onClick={() => setStatus(d, 'SKIPPED')}
                className="px-3 py-1 text-xs rounded bg-crimson/20 text-crimson hover:bg-crimson/30 flex items-center gap-1 disabled:opacity-50">
                <X size={12} /> SKIPPED
              </button>
              <button disabled={saving} onClick={() => setStatus(d, 'EXPIRED')}
                className="px-3 py-1 text-xs rounded bg-panel text-muted hover:text-crimson flex items-center gap-1 disabled:opacity-50">
                <Clock size={12} /> EXPIRED
              </button>
              {!d.veto && (
                <span className="inline-flex items-center gap-1">
                  <input value={vetoReason} onChange={e => setVetoReason(e.target.value)}
                    placeholder="motivo obbligatorio per il veto"
                    title="senza testo il bottone VETO resta spento"
                    className="bg-bg border border-crimson/40 rounded px-2 py-1 text-xs w-56" />
                  <button disabled={saving || !vetoReason.trim()} onClick={() => setVeto(d)}
                    title="veto ETERNO: imposta SKIPPED + divieto permanente nel canale della run, finché non lo revochi dal dettaglio"
                    className="px-3 py-1 text-xs rounded border border-crimson text-crimson bg-crimson/10 hover:bg-crimson/25 flex items-center gap-1 disabled:opacity-40">
                    ⛔ VETO — eterno
                  </button>
                </span>
              )}
              {d.status !== 'PENDING' && !d.veto && (
                <button disabled={saving} onClick={() => setStatus(d, 'PENDING')}
                  className="px-3 py-1 text-xs rounded bg-gold/20 text-gold hover:bg-gold/30 disabled:opacity-50">
                  ↺ riapri PENDING
                </button>
              )}
              <span className="ml-auto">{archiveBtn(d, archived)}</span>
            </div>
          </div>
        </div>
      </td>
    </tr>
  );

  const opsRows = (list: Decision[], archived: boolean) => list.map(d => (
    <Fragment key={d.id}>
      <tr onClick={() => toggle(d.id)}
          className="border-b border-border/20 hover:bg-bg/40 cursor-pointer">
        <td className="text-muted pl-2">
          {expanded === d.id ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        </td>
        <td className="py-1.5">
          {/* stile C: riga principale + sottotitolo */}
          <div className="font-mono font-semibold text-white text-sm">{d.ticker}
            <span className="text-gold text-xs ml-2">{d.action}</span>
            {!!d.veto && <span className="text-[9px] px-1.5 py-0.5 ml-2 rounded border border-crimson text-crimson bg-crimson/10" title={`VETO attivo dal ${(d.veto_at || '').slice(0, 10)}: ${d.veto_reason || ''}`}>⛔ VETO</span>}
            {d.archive_override != null && <span className="text-[9px] text-faint ml-2" title="posizionamento fissato da un tuo click (⟲ auto nel dettaglio)">📌</span>}
          </div>
          <div className="text-[10px] text-faint truncate max-w-[260px]">
            {d.timestamp?.slice(0, 10)}{d.rationale ? ` · ${d.rationale}` : ''}
          </div>
        </td>
        <td className="text-right text-xs font-mono">{d.eur_amount ? fmtEUR(d.eur_amount, true) : '-'}</td>
        <td className="text-center">
          <span className={`text-[10px] px-2 py-0.5 rounded ${STATUS_STYLE[d.status] || 'bg-panel text-muted'}`}>
            {d.status}
          </span>
        </td>
        <td className={`text-right text-xs font-mono ${(d.outcome_pct || 0) >= 0 ? 'pl-positive' : 'pl-negative'}`}>
          {d.outcome_pct != null ? `${d.outcome_pct > 0 ? '+' : ''}${d.outcome_pct.toFixed(1)}%` : '-'}
        </td>
        <td className="text-right pr-2">{archiveBtn(d, archived, true)}</td>
      </tr>
      {expanded === d.id && detailRowOps(d, archived)}
    </Fragment>
  ));

  const opsHeader = (
    <thead>
      <tr className="bg-[#141414]">
        <th className="w-6"></th>
        <th className={`text-left py-1.5 ${HDR}`}>Titolo / azione</th>
        <th className={`text-right ${HDR}`}>EUR</th>
        <th className={`text-center ${HDR}`}>Esito</th>
        <th className={`text-right ${HDR}`}>Outcome</th>
        <th className={`text-right pr-2 ${HDR}`}>Archivio</th>
      </tr>
    </thead>
  );

  // ---------- colonna destra: PIPELINE RESEARCH (card + thread note) ----------
  const noteBubble = (n: DecisionNote) => (
    <div key={n.id}
         className={`text-xs rounded px-2 py-1.5 mb-1.5 border-l-2 ${n.autore === 'PM'
           ? 'bg-panel border-gold' : 'bg-bg/60 border-border-bright'}`}>
      <span className="text-[10px] uppercase tracking-wider text-muted">
        {n.autore} · {n.timestamp?.slice(0, 16).replace('T', ' ')}
      </span>
      <p className="text-white/85 whitespace-pre-wrap mt-0.5">{n.testo}</p>
    </div>
  );

  const researchCard = (d: Decision, archived: boolean) => {
    const g = giorniDa(d.timestamp);
    return (
      <div key={d.id}
           className={`bg-black/40 border border-border p-3 mb-3 ${archived ? 'opacity-75' : ''}`}>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-mono font-semibold text-white">{d.ticker}</span>
          <span className={`text-[10px] px-2 py-0.5 rounded ${STATUS_STYLE[d.status] || 'bg-panel text-muted'}`}>
            {d.status === 'PENDING' ? `IN RICERCA${g != null ? ` · ${g}g` : ''}` : d.status}
          </span>
          {d.memo_id && <span className="text-[10px] text-muted">memo #{d.memo_id}</span>}
          {d.archive_override != null && <span className="text-[9px] text-faint" title="posizionamento fissato da un tuo click">📌 fissata</span>}
          <span className="ml-auto text-[10px] text-muted">#{d.id} · {d.timestamp?.slice(0, 10)}</span>
        </div>
        {d.rationale && <p className="text-xs text-white/80 mt-1.5 whitespace-pre-wrap">{d.rationale}</p>}
        {d.timing && <p className="text-[11px] text-cyan mt-1">▷ trigger: {d.timing}</p>}
        {d.outcome_notes && <p className="text-[10px] text-muted italic mt-1">{d.outcome_notes}</p>}
        <div className="mt-2 pt-2 border-t border-dashed border-border/60">
          {(d.notes || []).map(noteBubble)}
          {/* review 17/07 (ALTA F2): la run legge SOLO le research PENDING — l'input
              note compare solo se la nota verra' DAVVERO letta; altrimenti si dichiara */}
          {!archived && d.status === 'PENDING' && (
            <div className="flex gap-2 items-center mt-1.5">
              <input value={noteDraft[d.id] || ''}
                onChange={e => setNoteDraft(prev => ({ ...prev, [d.id]: e.target.value }))}
                onKeyDown={e => { if (e.key === 'Enter') sendNote(d); }}
                placeholder="scrivi una nota alla run..."
                className="bg-bg border border-border rounded px-2 py-1 text-xs flex-1" />
              <button disabled={saving || !(noteDraft[d.id] || '').trim()} onClick={() => sendNote(d)}
                className="px-2.5 py-1 text-xs rounded bg-gold text-bg font-semibold flex items-center gap-1 disabled:opacity-40">
                <Send size={11} /> INVIA
              </button>
            </div>
          )}
          {!archived && d.status !== 'PENDING' && (
            <p className="text-[10px] text-faint mt-1.5">
              status {d.status}: la run NON la sta lavorando — riaprila (PENDING) perche' torni in pipeline.
            </p>
          )}
          <div className="flex gap-2 mt-2 items-center flex-wrap">
            {!archived ? (
              <>
                <button disabled={saving} onClick={() => closeResearch(d)}
                  title="chiude la ricerca (la run la molla) e la sposta in archivio — sempre recuperabile"
                  className="px-2.5 py-1 text-[10px] border border-border text-muted hover:text-[#ffb000] hover:border-[#7a5b1e] inline-flex items-center gap-1 disabled:opacity-50">
                  <Archive size={10} /> ARCHIVIA E CHIUDI
                </button>
                {d.status !== 'PENDING' && (
                  <button disabled={saving} onClick={() => reopenResearch(d)}
                    className="px-2.5 py-1 text-[11px] rounded bg-gold/20 text-gold hover:bg-gold/30 disabled:opacity-50"
                    title="riapre lo STATUS: la run la riprende al prossimo giro">
                    ↺ riapri (PENDING)
                  </button>
                )}
              </>
            ) : (
              <button disabled={saving} onClick={() => reopenResearch(d)}
                title="riporta la card in pagina E riapre la ricerca: la run la riprende al prossimo giro"
                className="px-2.5 py-1 text-[10px] border border-[#7a5b1e] bg-[#1c1305] text-[#ffb000] hover:bg-[#33240a] inline-flex items-center gap-1 disabled:opacity-50">
                <ArchiveRestore size={10} /> RIPORTA IN PAGINA E RIAPRI
              </button>
            )}
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className="space-y-4">
      <div className="flex items-baseline justify-between border-b-2 border-[#ff8c00] pb-2">
        <h1 className={`text-lg font-bold font-mono ${ORANGE}`}>BELLOMBERG &lt;DEC&gt; — DECISIONS TRACKER</h1>
        <div className="flex gap-2 items-center">
          {['', 'PENDING', 'EXECUTED', 'PARTIAL', 'SKIPPED', 'EXPIRED'].map(s => (
            <button key={s} onClick={() => setFilter(s)}
              className={`px-3 py-1 text-[10px] font-mono ${filter === s ? 'bg-[#1c1305] border border-[#7a5b1e] text-[#ffb000]' : 'text-muted hover:text-[#ffb000]'}`}>
              {s || 'ALL'}
            </button>
          ))}
        </div>
      </div>

      {/* review 17/07 F4: feedback GLOBALE — le azioni archivio partono da qualsiasi
          riga/card, il messaggio (anche gli errori) non puo' vivere solo nel dettaglio */}
      {msg && (
        <p className={`text-xs font-mono ${msg.startsWith('Errore') ? 'text-crimson' : 'text-emerald'}`}>{msg}</p>
      )}

      {loadErr && (
        <p className="text-xs font-mono text-crimson border border-crimson/50 bg-crimson/5 px-3 py-2">
          DECISIONI NON DISPONIBILI — {loadErr}. Le decisioni non sono perse: backend in errore, cambia filtro o ricarica per riprovare.
        </p>
      )}

      {/* split 50/50 di F10 v3 (scelta PM) — reskin stile C */}
      <div className="grid grid-cols-2 gap-4 items-start">
        <div className="border border-border bg-black/40">
          <div className={`flex justify-between items-baseline px-3 py-2 bg-[#0c0f14] border-b border-border ${HDR}`}>
            <span>Decisioni operative — {proposte.length} attive</span>
            <span className="text-faint normal-case tracking-normal">auto-archivio 7gg · il tuo click vince</span>
          </div>
          <table className="w-full text-sm">
            {opsHeader}
            <tbody>{opsRows(proposte, false)}</tbody>
          </table>
          {proposte.length === 0 && (
            <p className={loadErr ? 'text-crimson text-sm p-4 font-mono' : 'text-muted text-sm p-4'}>
              {loadErr ? 'n.d. — backend in errore (v. banner)' : 'Nessuna proposta operativa attiva per questo filtro.'}
            </p>
          )}
          <button onClick={() => setArchOpenOps(!archOpenOps)}
            className={`w-full px-3 py-2 text-left flex justify-between border-t border-border bg-[#0c0f14] ${HDR} hover:text-[#ffb000]`}>
            <span>{archOpenOps ? '▾' : '▸'} Archivio operative ({archProposte.length})</span>
            <span className="text-faint normal-case tracking-normal">tutto resta nel DB · RIPORTA IN PAGINA quando vuoi</span>
          </button>
          {archOpenOps && (
            <table className="w-full text-sm opacity-85">
              {opsHeader}
              <tbody>{opsRows(archProposte, true)}</tbody>
            </table>
          )}
        </div>

        <div className="border border-border bg-black/40">
          <div className={`flex justify-between items-baseline px-3 py-2 bg-[#0c0f14] border-b border-border ${HDR}`}>
            <span className="flex items-center gap-2"><FlaskConical size={12} /> Pipeline research — {research.length} vive</span>
            <span className="text-faint normal-case tracking-normal">le note le legge la run</span>
          </div>
          <div className="p-3">
            {research.map(d => researchCard(d, false))}
            {research.length === 0 && (
              <p className={loadErr ? 'text-crimson text-sm p-2 font-mono' : 'text-muted text-sm p-2'}>
                {loadErr ? 'n.d. — backend in errore (v. banner)' : 'Nessuna ricerca aperta — la run ne propone di nuove nel memo.'}
              </p>
            )}
          </div>
          <button onClick={() => setArchOpenRes(!archOpenRes)}
            className={`w-full px-3 py-2 text-left flex justify-between border-t border-border bg-[#0c0f14] ${HDR} hover:text-[#ffb000]`}>
            <span>{archOpenRes ? '▾' : '▸'} Archivio research ({archResearch.length})</span>
            <span className="text-faint normal-case tracking-normal">chiuse, promosse o scadute — recuperabili</span>
          </button>
          {archOpenRes && <div className="p-3">{archResearch.map(d => researchCard(d, true))}</div>}
        </div>
      </div>

      <p className="text-muted text-xs text-center">
        OPERATIVE: archivio automatico a 7 giorni, ma ARCHIVIA/RIPORTA sono tuoi e vincono sempre — e una riga fissata
        attiva (📌) l'automatismo NON la tocca mai (decisione PM 17/07). RESEARCH: ARCHIVIA E CHIUDI ferma davvero la
        run, RIPORTA IN PAGINA E RIAPRI la fa ripartire. Tutto resta per sempre nel DB. Le note sulle ricerche sono
        il filo PM↔AI: la run le legge e risponde qui.
      </p>
    </div>
  );
}
