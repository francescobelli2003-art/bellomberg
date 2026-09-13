import { useEffect, useState, useCallback, useMemo, Fragment } from 'react';
import { useT } from '@/i18n/provider';
import { t as tr, type Chiave, type Parametri } from '@/i18n/t';
import { linguaCorrente, localeDi, type Lingua } from '@/i18n/lingua';
import { leggiDetail } from '@/lib/quota';
import { useNavigate } from 'react-router-dom';
import { Bellomberg, Decision, DecisionNote } from '@/lib/api';
import { fmtEUR, fmtNum, fmtPct } from '@/lib/format';
import { leggiNumeroConSegno } from '@/lib/cassa';
import { decisioneCompatibile } from '@/lib/trade-entry';
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

type FeedbackMessage = { key: Chiave; params?: Parametri; error: boolean }
  | { field: 'pct' | 'eur'; raw: string; language: Lingua };
const detail = (error: any) => leggiDetail(error?.response?.data?.detail) || leggiDetail(error?.message) || leggiDetail(error);
const statusLabel = (status: string) => {
  const keys = { EXECUTED: 'executed', PARTIAL: 'partial', PENDING: 'pending', SKIPPED: 'skipped', EXPIRED: 'expired' } as const;
  const key = keys[status as keyof typeof keys];
  return key ? tr(`decisiondesk.${key}`) : status;
};
const displayDate = (iso?: string | null, withTime = false) => {
  if (!iso) return tr('decisiondesk.na');
  const date = new Date(iso.slice(0, 10) + 'T00:00:00');
  if (!Number.isFinite(date.getTime())) return iso;
  return date.toLocaleDateString(localeDi(linguaCorrente()), { day: '2-digit', month: '2-digit', year: 'numeric' })
    + (withTime && iso.length >= 16 ? ' ' + iso.slice(11, 16) : '');
};

const giorniDa = (ts?: string | null) => {
  if (!ts) return null;
  const d = Math.floor((Date.now() - new Date(ts).getTime()) / 86400_000);
  return isFinite(d) ? d : null;
};

export default function Decisions() {
  const tr = useT();
  const navigate = useNavigate();
  const [filter, setFilter] = useState<string>('');
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [feedback, setFeedback] = useState('');
  const [outcomePct, setOutcomePct] = useState('');
  const [outcomeEur, setOutcomeEur] = useState('');
  const [inputLanguage, setInputLanguage] = useState<Lingua>(linguaCorrente);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<FeedbackMessage | null>(null);
  const [noteDraft, setNoteDraft] = useState<Record<number, string>>({});
  // F10 opzione A: motivo del veto (obbligatorio — senza testo il bottone resta spento)
  const [vetoReason, setVetoReason] = useState('');
  const [archOpenOps, setArchOpenOps] = useState(false);
  const [archOpenRes, setArchOpenRes] = useState(false);

  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    // review 16/07: con limit=100 su 170 decisioni, 10 PENDING operative non arrivavano
    // MAI al client e il contatore archivio sottostimava in silenzio.
    Bellomberg.decisions(filter || undefined, 500)
      .then(r => {
        if (!Array.isArray(r?.decisions)) { setLoadErr(''); return; }
        setLoadErr(null); setDecisions(r.decisions);
      })
      .catch((e: any) => setLoadErr(detail(e)))
      .finally(() => setLoading(false));
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
  const archiveEstimated = decisions.filter(d => d.archive_override == null && typeof d.archived !== 'boolean').length;
  const countLabel = (n: number) => loadErr !== null ? tr('decisiondesk.na') : loading ? '…' : fmtNum(n, 0);

  const toggle = (id: number) => {
    setExpanded(expanded === id ? null : id);
    setInputLanguage(linguaCorrente());
    setFeedback(''); setOutcomePct(''); setOutcomeEur(''); setVetoReason(''); setMsg(null);
  };

  // ⚠ Outcome scritti a mano: l'app gira in en-GB e su <input type="number"> la
  // virgola veniva CANCELLATA senza badInput (12,5 → 125 nel DB). Stessa cura di
  // F7 (lib/cassa.ts), variante col segno: un outcome può essere perdita o pari.
  const letturaPct = useMemo(() => leggiNumeroConSegno(outcomePct, inputLanguage, linguaCorrente()), [outcomePct, inputLanguage, tr]);
  const letturaEur = useMemo(() => leggiNumeroConSegno(outcomeEur, inputLanguage, linguaCorrente()), [outcomeEur, inputLanguage, tr]);
  const inputHint = tr(inputLanguage === 'it' ? 'decisiondesk.inputIt' : 'decisiondesk.inputEn');
  const messageText = (() => {
    if (!msg) return '';
    if ('field' in msg) {
      const parsed = leggiNumeroConSegno(msg.raw, msg.language, linguaCorrente());
      return tr(msg.field === 'pct' ? 'decisiondesk.invalidPct' : 'decisiondesk.invalidEur', {
        detail: parsed && !parsed.ok ? parsed.motivo : tr('decisiondesk.errorUnknown'),
      });
    }
    const params = msg.key === 'decisiondesk.updated' && msg.params ? { ...msg.params, status: statusLabel(String(msg.params.status)) } : msg.params;
    return tr(msg.key, msg.error && params ? { ...params, detail: params.detail || tr('decisiondesk.errorUnknown') } : params);
  })();

  const setStatus = async (d: Decision, status: string) => {
    // un outcome illeggibile non si scarta in silenzio: blocca e spiega
    if (letturaPct && !letturaPct.ok) { setMsg({ field: 'pct', raw: outcomePct, language: inputLanguage }); return; }
    if (letturaEur && !letturaEur.ok) { setMsg({ field: 'eur', raw: outcomeEur, language: inputLanguage }); return; }
    setSaving(true); setMsg(null);
    try {
      const body: Record<string, unknown> = { status };
      if (feedback.trim()) body.pm_feedback = feedback.trim();
      if (letturaPct && letturaPct.ok) body.outcome_pct = letturaPct.valore;
      if (letturaEur && letturaEur.ok) body.outcome_eur = letturaEur.valore;
      await Bellomberg.updateDecision(d.id, body);
      setMsg({ key: 'decisiondesk.updated', params: { id: d.id, status }, error: false });
      load();
    } catch (e: any) {
      setMsg({ key: 'decisiondesk.error', params: { detail: detail(e) }, error: true });
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
      setMsg({ key: 'decisiondesk.vetoActive', params: { id: d.id }, error: false });
      load();
    } catch (e: any) {
      setMsg({ key: 'decisiondesk.vetoError', params: { detail: detail(e) }, error: true });
    } finally {
      setSaving(false);
    }
  };

  const revokeVeto = async (d: Decision) => {
    if (!window.confirm(tr('decisiondesk.f001', {a: d.id, b: d.ticker}))) return;
    setSaving(true); setMsg(null);
    try {
      await Bellomberg.revokeDecisionVeto(d.id);
      setMsg({ key: 'decisiondesk.vetoRevoked', params: { id: d.id }, error: false });
      load();
    } catch (e: any) {
      setMsg({ key: 'decisiondesk.revokeError', params: { detail: detail(e) }, error: true });
    } finally {
      setSaving(false);
    }
  };

  // F10-C: archivia / riporta in pagina — persistente, vince sull'automatico
  const setArchive = async (d: Decision, archived: boolean | null) => {
    setSaving(true); setMsg(null);
    try {
      await Bellomberg.setDecisionArchive(d.id, archived);
      setMsg({ key: archived === null ? 'decisiondesk.archiveAuto' : archived ? 'decisiondesk.archived' : 'decisiondesk.restored', params: { id: d.id }, error: false });
      load();
    } catch (e: any) {
      setMsg({ key: 'decisiondesk.archiveError', params: { detail: detail(e) }, error: true });
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
      setMsg({ key: 'decisiondesk.researchClosed', params: { id: d.id }, error: false });
      load();
    } catch (e: any) {
      setMsg({ key: 'decisiondesk.error', params: { detail: detail(e) }, error: true });
    } finally {
      setSaving(false);
    }
  };
  const reopenResearch = async (d: Decision) => {
    setSaving(true); setMsg(null);
    try {
      await Bellomberg.updateDecision(d.id, { status: 'PENDING' });
      if (d.archive_override != null) await Bellomberg.setDecisionArchive(d.id, null);
      setMsg({ key: 'decisiondesk.researchReopened', params: { id: d.id }, error: false });
      load();
    } catch (e: any) {
      setMsg({ key: 'decisiondesk.error', params: { detail: detail(e) }, error: true });
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
      setMsg({ key: 'decisiondesk.noteError', params: { detail: detail(e) }, error: true });
    } finally {
      setSaving(false);
    }
  };

  // bottone archivio contestuale (attiva -> ARCHIVIA; archiviata -> RIPORTA IN PAGINA)
  const archiveBtn = (d: Decision, archived: boolean, compact = false) => (
    <span className="inline-flex items-center gap-1.5" onClick={e => e.stopPropagation()}>
      {!archived ? (
        <button disabled={saving} onClick={() => setArchive(d, true)}
          title={tr('decisiondesk.f002')}
          className={`${compact ? 'px-1.5 py-0.5' : 'px-2.5 py-1'} text-[10px] border border-border text-muted hover:text-[#ffb000] hover:border-[#7a5b1e] inline-flex items-center gap-1 disabled:opacity-50`}>
          <Archive size={10} /> {tr('decisiondesk.f003')}
        </button>
      ) : (
        <button disabled={saving} onClick={() => setArchive(d, false)}
          title={tr('decisiondesk.f004')}
          className={`${compact ? 'px-1.5 py-0.5' : 'px-2.5 py-1'} text-[10px] border border-[#7a5b1e] bg-[#1c1305] text-[#ffb000] hover:bg-[#33240a] inline-flex items-center gap-1 disabled:opacity-50`}>
          <ArchiveRestore size={10} /> {tr('decisiondesk.f005')}
        </button>
      )}
      {/* review 17/07 F8: nel compact solo il pin (gia' in riga); ⟲ auto vive nel dettaglio/card */}
      {!compact && d.archive_override != null && (
        <button disabled={saving} onClick={() => setArchive(d, null)}
          title={tr('decisiondesk.f006')}
          className="text-[9px] text-faint hover:text-muted underline decoration-dotted disabled:opacity-50">
          {tr('decisiondesk.f007')}
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
            <span>{tr('decisiondesk.f008')} <span className="text-white">{d.confidence || tr('decisiondesk.na')}</span></span>
            {d.timing && <span>{tr('decisiondesk.f009')} <span className="text-white">{d.timing}</span></span>}
            {d.memo_id && <span>{tr('decisiondesk.f010')} <span className="text-gold">#{d.memo_id}</span> {tr('decisiondesk.f011')}</span>}
            {d.closed_at && <span>{tr('decisiondesk.f012')} {displayDate(d.closed_at)}</span>}
          </div>
          <div>
            <div className={`${HDR} mb-1`}>{tr('decisiondesk.f013')}</div>
            <p className="text-white/90 whitespace-pre-wrap">{d.rationale || tr('decisiondesk.f014')}</p>
            {d.rationale && <span className="text-[10px] text-faint">{tr('decisiondesk.archivedText')}</span>}
          </div>
          {!!d.veto && (
            <div className="border border-crimson/60 bg-crimson/10 rounded px-3 py-2 text-xs"
                 onClick={e => e.stopPropagation()}>
              <span className="text-crimson font-semibold">{tr('decisiondesk.f015')} {displayDate(d.veto_at)}</span>
              <span className="text-white/90"> — “{d.veto_reason}{tr('decisiondesk.f016')}{d.id} {tr('decisiondesk.f017')}</span>
              <button disabled={saving} onClick={() => revokeVeto(d)}
                className="ml-3 underline decoration-dotted text-muted hover:text-crimson disabled:opacity-50">
                {tr('decisiondesk.f018')}
              </button>
            </div>
          )}
          {d.pm_feedback && (
            <div>
              <div className={`${HDR} mb-1`}>{tr('decisiondesk.f019')}</div>
              <p className="text-muted whitespace-pre-wrap">{d.pm_feedback}</p>
            </div>
          )}
          {d.outcome_notes && (
            <div>
              <div className={`${HDR} mb-1`}>{tr('decisiondesk.f020')}</div>
              <p className="text-muted whitespace-pre-wrap">{d.outcome_notes}</p>
            </div>
          )}
          <div className="border-t border-border/40 pt-3 space-y-2" onClick={e => e.stopPropagation()}>
            <div className="flex gap-2 items-center flex-wrap">
              <input value={feedback} onChange={e => setFeedback(e.target.value)}
                placeholder={tr('decisiondesk.f021')}
                className="bg-bg border border-border rounded px-2 py-1 text-xs w-64" />
              <input value={outcomePct} onChange={e => setOutcomePct(e.target.value)}
                placeholder={tr('decisiondesk.f022')} type="text" inputMode="decimal"
                aria-invalid={!!(letturaPct && !letturaPct.ok)}
                title={letturaPct && !letturaPct.ok ? letturaPct.motivo : inputHint}
                className={`bg-bg border rounded px-2 py-1 text-xs w-24 ${letturaPct && !letturaPct.ok ? 'border-crimson text-crimson' : 'border-border'}`} />
              <input value={outcomeEur} onChange={e => setOutcomeEur(e.target.value)}
                placeholder={tr('decisiondesk.f023')} type="text" inputMode="decimal"
                aria-invalid={!!(letturaEur && !letturaEur.ok)}
                title={letturaEur && !letturaEur.ok ? letturaEur.motivo : inputHint}
                className={`bg-bg border rounded px-2 py-1 text-xs w-24 ${letturaEur && !letturaEur.ok ? 'border-crimson text-crimson' : 'border-border'}`} />
            </div>
            <div className="flex gap-2 flex-wrap items-center">
              {decisioneCompatibile(d, d.ticker, d.action) && (
                <button onClick={() => navigate(`/trades?decision=${d.id}`)}
                  className="px-3 py-1 text-xs rounded border border-cyan text-cyan hover:bg-cyan/20">
                  {tr('decisiondesk.f024')}
                </button>
              )}
              {d.esecuzione && <span className="text-xs text-muted">
                {tr('decisiondesk.f025')} {fmtEUR(d.esecuzione.eur)}
                {d.esecuzione.pct != null ? tr('decisiondesk.f026', {a: fmtNum(d.esecuzione.pct, 1)}) : tr('decisiondesk.f027')}
                {' · '}{d.esecuzione.trade_ids.map(id => `#${id}`).join(', ')}
                {d.esecuzione.inferito ? tr('decisiondesk.f028') : tr('decisiondesk.f029')}
              </span>}
              <button disabled={saving} onClick={() => setStatus(d, 'EXECUTED')}
                className="px-3 py-1 text-xs rounded bg-emerald/20 text-emerald hover:bg-emerald/30 flex items-center gap-1 disabled:opacity-50">
                <Check size={12} /> {statusLabel('EXECUTED')}
              </button>
              <button disabled={saving} onClick={() => setStatus(d, 'PARTIAL')}
                className="px-3 py-1 text-xs rounded bg-cyan/20 text-cyan hover:bg-cyan/30 flex items-center gap-1 disabled:opacity-50">
                <MinusCircle size={12} /> {statusLabel('PARTIAL')}
              </button>
              <button disabled={saving} onClick={() => setStatus(d, 'SKIPPED')}
                className="px-3 py-1 text-xs rounded bg-crimson/20 text-crimson hover:bg-crimson/30 flex items-center gap-1 disabled:opacity-50">
                <X size={12} /> {statusLabel('SKIPPED')}
              </button>
              <button disabled={saving} onClick={() => setStatus(d, 'EXPIRED')}
                className="px-3 py-1 text-xs rounded bg-panel text-muted hover:text-crimson flex items-center gap-1 disabled:opacity-50">
                <Clock size={12} /> {statusLabel('EXPIRED')}
              </button>
              {!d.veto && (
                <span className="inline-flex items-center gap-1">
                  <input value={vetoReason} onChange={e => setVetoReason(e.target.value)}
                    placeholder={tr('decisiondesk.f030')}
                    title={tr('decisiondesk.f031')}
                    className="bg-bg border border-crimson/40 rounded px-2 py-1 text-xs w-56" />
                  <button disabled={saving || !vetoReason.trim()} onClick={() => setVeto(d)}
                    title={tr('decisiondesk.f032')}
                    className="px-3 py-1 text-xs rounded border border-crimson text-crimson bg-crimson/10 hover:bg-crimson/25 flex items-center gap-1 disabled:opacity-40">
                    {tr('decisiondesk.f033')}
                  </button>
                </span>
              )}
              {d.status !== 'PENDING' && !d.veto && (
                <button disabled={saving} onClick={() => setStatus(d, 'PENDING')}
                  className="px-3 py-1 text-xs rounded bg-gold/20 text-gold hover:bg-gold/30 disabled:opacity-50">
                  {tr('decisiondesk.f034')}
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
            {!!d.veto && <span className="text-[9px] px-1.5 py-0.5 ml-2 rounded border border-crimson text-crimson bg-crimson/10" title={tr('decisiondesk.f035', {a: displayDate(d.veto_at), b: d.veto_reason || ''})}>⛔ VETO</span>}
            {d.archive_override != null && <span className="text-[9px] text-faint ml-2" title={tr('decisiondesk.f036')}>📌</span>}
          </div>
          <div className="text-[10px] text-faint truncate max-w-[260px]">
            {displayDate(d.timestamp)}{d.rationale ? ` · ${d.rationale}` : ''}
          </div>
        </td>
        <td className="text-right text-xs font-mono">{d.eur_amount != null ? fmtEUR(d.eur_amount, true) : '-'}</td>
        <td className="text-center">
          <span className={`text-[10px] px-2 py-0.5 rounded ${STATUS_STYLE[d.status] || 'bg-panel text-muted'}`}>
            {statusLabel(d.status)}
          </span>
        </td>
        <td className={`text-right text-xs font-mono ${(d.outcome_pct || 0) >= 0 ? 'pl-positive' : 'pl-negative'}`}>
          {d.outcome_pct != null ? fmtPct(d.outcome_pct, true, 1) : '-'}
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
        <th className={`text-left py-1.5 ${HDR}`}>{tr('decisiondesk.f037')}</th>
        <th className={`text-right ${HDR}`}>EUR</th>
        <th className={`text-center ${HDR}`}>{tr('decisiondesk.f038')}</th>
        <th className={`text-right ${HDR}`}>{tr('decisiondesk.f039')}</th>
        <th className={`text-right pr-2 ${HDR}`}>{tr('decisiondesk.f040')}</th>
      </tr>
    </thead>
  );

  // ---------- colonna destra: PIPELINE RESEARCH (card + thread note) ----------
  const noteBubble = (n: DecisionNote) => (
    <div key={n.id}
         className={`text-xs rounded px-2 py-1.5 mb-1.5 border-l-2 ${n.autore === 'PM'
           ? 'bg-panel border-gold' : 'bg-bg/60 border-border-bright'}`}>
      <span className="text-[10px] uppercase tracking-wider text-muted">
        {n.autore} · {displayDate(n.timestamp, true)}
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
            {d.status === 'PENDING' ? tr('decisiondesk.f041', {a: g != null ? tr('decisiondesk.f042', {a: g}) : ''}) : statusLabel(d.status)}
          </span>
          {d.memo_id && <span className="text-[10px] text-muted">memo #{d.memo_id}</span>}
          {d.archive_override != null && <span className="text-[9px] text-faint" title={tr('decisiondesk.f043')}>{tr('decisiondesk.f044')}</span>}
          <span className="ml-auto text-[10px] text-muted">#{d.id} · {displayDate(d.timestamp)}</span>
        </div>
        {d.rationale && <p className="text-xs text-white/80 mt-1.5 whitespace-pre-wrap">{d.rationale}</p>}
        {d.rationale && <span className="text-[10px] text-faint">{tr('decisiondesk.archivedText')}</span>}
        {d.timing && <p className="text-[11px] text-cyan mt-1">{tr('decisiondesk.f045')} {d.timing}</p>}
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
                placeholder={tr('decisiondesk.f046')}
                className="bg-bg border border-border rounded px-2 py-1 text-xs flex-1" />
              <button disabled={saving || !(noteDraft[d.id] || '').trim()} onClick={() => sendNote(d)}
                className="px-2.5 py-1 text-xs rounded bg-gold text-bg font-semibold flex items-center gap-1 disabled:opacity-40">
                <Send size={11} /> {tr('decisiondesk.f047')}
              </button>
            </div>
          )}
          {!archived && d.status !== 'PENDING' && (
            <p className="text-[10px] text-faint mt-1.5">
              {tr('decisiondesk.f048')} {d.status}{tr('decisiondesk.f049')}
            </p>
          )}
          <div className="flex gap-2 mt-2 items-center flex-wrap">
            {!archived ? (
              <>
                <button disabled={saving} onClick={() => closeResearch(d)}
                  title={tr('decisiondesk.f050')}
                  className="px-2.5 py-1 text-[10px] border border-border text-muted hover:text-[#ffb000] hover:border-[#7a5b1e] inline-flex items-center gap-1 disabled:opacity-50">
                  <Archive size={10} /> {tr('decisiondesk.f051')}
                </button>
                {d.status !== 'PENDING' && (
                  <button disabled={saving} onClick={() => reopenResearch(d)}
                    className="px-2.5 py-1 text-[11px] rounded bg-gold/20 text-gold hover:bg-gold/30 disabled:opacity-50"
                    title={tr('decisiondesk.f052')}>
                    {tr('decisiondesk.f053')}
                  </button>
                )}
              </>
            ) : (
              <button disabled={saving} onClick={() => reopenResearch(d)}
                title={tr('decisiondesk.f054')}
                className="px-2.5 py-1 text-[10px] border border-[#7a5b1e] bg-[#1c1305] text-[#ffb000] hover:bg-[#33240a] inline-flex items-center gap-1 disabled:opacity-50">
                <ArchiveRestore size={10} /> {tr('decisiondesk.f055')}
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
        <h1 className={`text-lg font-bold font-mono ${ORANGE}`}>{tr('decisiondesk.f056')}</h1>
        <div className="flex gap-2 items-center">
          {['', 'PENDING', 'EXECUTED', 'PARTIAL', 'SKIPPED', 'EXPIRED'].map(s => (
            <button key={s} onClick={() => setFilter(s)}
              className={`px-3 py-1 text-[10px] font-mono ${filter === s ? 'bg-[#1c1305] border border-[#7a5b1e] text-[#ffb000]' : 'text-muted hover:text-[#ffb000]'}`}>
              {s ? statusLabel(s) : tr('decisiondesk.f057')}
            </button>
          ))}
        </div>
      </div>

      {/* review 17/07 F4: feedback GLOBALE — le azioni archivio partono da qualsiasi
          riga/card, il messaggio (anche gli errori) non puo' vivere solo nel dettaglio */}
      {msg && (
        <p className={`text-xs font-mono ${'field' in msg || msg.error ? 'text-crimson' : 'text-emerald'}`}>{messageText}</p>
      )}

      {loadErr !== null && (
        <p className="text-xs font-mono text-crimson border border-crimson/50 bg-crimson/5 px-3 py-2">
          {tr('decisiondesk.f058')} {loadErr || tr('decisiondesk.errorUnknown')}{tr('decisiondesk.f059')}
        </p>
      )}
      {archiveEstimated > 0 && <p className="text-xs text-muted">{tr('decisiondesk.archiveEstimated', { n: archiveEstimated })}</p>}

      {/* split 50/50 di F10 v3 (scelta PM) — reskin stile C */}
      <div className="grid grid-cols-2 gap-4 items-start">
        <div className="border border-border bg-black/40">
          <div className={`flex justify-between items-baseline px-3 py-2 bg-[#0c0f14] border-b border-border ${HDR}`}>
            <span>{tr('decisiondesk.f060')} {countLabel(proposte.length)} {tr('decisiondesk.f061')}</span>
            <span className="text-faint normal-case tracking-normal">{tr('decisiondesk.f062')}</span>
          </div>
          <table className="w-full text-sm">
            {opsHeader}
            <tbody>{opsRows(proposte, false)}</tbody>
          </table>
          {proposte.length === 0 && (
            <p className={loadErr !== null ? 'text-crimson text-sm p-4 font-mono' : 'text-muted text-sm p-4'}>
              {loadErr !== null ? tr('decisiondesk.f063') : loading ? tr('decisiondesk.loading') : tr('decisiondesk.f064')}
            </p>
          )}
          <button onClick={() => setArchOpenOps(!archOpenOps)}
            className={`w-full px-3 py-2 text-left flex justify-between border-t border-border bg-[#0c0f14] ${HDR} hover:text-[#ffb000]`}>
            <span>{archOpenOps ? '▾' : '▸'} {tr('decisiondesk.f065')}{countLabel(archProposte.length)})</span>
            <span className="text-faint normal-case tracking-normal">{tr('decisiondesk.f066')}</span>
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
            <span className="flex items-center gap-2"><FlaskConical size={12} /> {tr('decisiondesk.f067')} {countLabel(research.length)} {tr('decisiondesk.f068')}</span>
            <span className="text-faint normal-case tracking-normal">{tr('decisiondesk.f069')}</span>
          </div>
          <div className="p-3">
            {research.map(d => researchCard(d, false))}
            {research.length === 0 && (
              <p className={loadErr !== null ? 'text-crimson text-sm p-2 font-mono' : 'text-muted text-sm p-2'}>
                {loadErr !== null ? tr('decisiondesk.f063') : loading ? tr('decisiondesk.loading') : tr('decisiondesk.f070')}
              </p>
            )}
          </div>
          <button onClick={() => setArchOpenRes(!archOpenRes)}
            className={`w-full px-3 py-2 text-left flex justify-between border-t border-border bg-[#0c0f14] ${HDR} hover:text-[#ffb000]`}>
            <span>{archOpenRes ? '▾' : '▸'} {tr('decisiondesk.f071')}{countLabel(archResearch.length)})</span>
            <span className="text-faint normal-case tracking-normal">{tr('decisiondesk.f072')}</span>
          </button>
          {archOpenRes && <div className="p-3">{archResearch.map(d => researchCard(d, true))}</div>}
        </div>
      </div>

      <p className="text-muted text-xs text-center">
        {tr('decisiondesk.f073')}
      </p>
    </div>
  );
}
