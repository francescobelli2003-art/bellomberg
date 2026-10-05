import { showNotice, type Notice } from '@/lib/mandato-presentation';
import type { ReactNode } from 'react';
import { useLingua, useT } from '@/i18n/provider';
import { localeDi } from '@/i18n/lingua';
import { t as tr } from '@/i18n/t';
import { useEffect, useRef, useState } from 'react';
import ModernPage from '@/components/ModernPage';
import NewInterfaceBoundary from '@/components/NewInterfaceBoundary';
import { Bellomberg } from '@/lib/api';
import { Journal, JournalError, journalChanged, journalDraft,
  type JournalDraft, type JournalEntry, type JournalKind, type JournalPageResult,
  type JournalRevision, type JournalStatus, type JournalSummary } from '@/lib/journal';
import './journal.css';
import './operations-modern.css';
import { Archive, Check, ChevronLeft, ChevronRight, Globe, History, Lock, Plus, Search, X } from 'lucide-react';


const BODY_LIMIT = 30000;
type Destination = { id: number } | 'new';

function DeferredJournalView({ render }: { render: () => ReactNode }) {
  return render();
}

function JournalViewBoundary({ render }: { render: () => ReactNode }) {
  const language = useLingua();
  return <NewInterfaceBoundary language={language}>
    <DeferredJournalView render={render} />
  </NewInterfaceBoundary>;
}
// Bozza solo in memoria della sessione: sopravvive alla navigazione interna,
// senza scrivere contenuti privati in localStorage o fuori da SQLite.
let sessionDraft: { entry: JournalEntry | null; draft: JournalDraft } | null = null;
let pendingSave: Promise<JournalEntry> | null = null;

export default function JournalPage() {
  const tr = useT(), language = useLingua();
  const WHEN = (value: string) => new Date(value).toLocaleString(localeDi(language), { dateStyle: 'medium', timeStyle: 'short' });
  const number = (value: number) => value.toLocaleString(localeDi(language), { useGrouping: true });
const KIND = { thesis: tr('journal.thesis'), macro: tr('journal.macro') };
const ACTION = { create: tr('journal.created'), update: tr('journal.updated'), archive: tr('journal.archived'), restore: tr('journal.restored') };

  const incomingSave = useRef(pendingSave);
  const [entries, setEntries] = useState<JournalPageResult<JournalSummary> | null>(null);
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<JournalStatus>('active');
  const [kind, setKind] = useState<JournalKind | ''>('');
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(false);
  const [listError, setListError] = useState<Notice>('');
  const [current, setCurrent] = useState<JournalEntry | null>(() => sessionDraft?.entry ?? null);
  const [draft, setDraft] = useState<JournalDraft>(() => sessionDraft?.draft ?? journalDraft());
  const [busy, setBusy] = useState(!!incomingSave.current);
  const [message, setMessage] = useState<Notice>(() => sessionDraft ? { key: 'journal.recovered' } : '');
  const [error, setError] = useState<Notice>('');
  const [pending, setPending] = useState<Destination | null>(null);
  const [versions, setVersions] = useState<JournalRevision[]>([]);
  const [versionsTotal, setVersionsTotal] = useState(0);
  const [versionsBusy, setVersionsBusy] = useState(false);
  const [versionsError, setVersionsError] = useState<Notice>('');
  const [conflict, setConflict] = useState(false);
  const [remote, setRemote] = useState<JournalEntry | null>(null);
  const [tickers, setTickers] = useState<{ ticker: string; nome: string }[]>([]);
  const [tickerError, setTickerError] = useState(false);
  const operation = useRef(0);
  const selectedId = useRef<number | null>(current?.id ?? null);
  const historyGeneration = useRef(0);
  const dirty = journalChanged(draft, current);
  const archived = !!current?.archived_at;
  useEffect(() => {
    sessionDraft = dirty ? { entry: current, draft } : null;
  }, [current, draft, dirty]);

  useEffect(() => {
    const timer = window.setTimeout(() => { setSearch(query); setOffset(0); }, 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setListError('');
    Journal.list({ status, kind: kind || undefined, query: search, offset }, controller.signal)
      .then(result => { if (!controller.signal.aborted) setEntries(result); })
      .catch(e => { if (!controller.signal.aborted) { setEntries(null); setListError({error:e}); } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [status, kind, search, offset, refresh]);
  useEffect(() => {
    let alive = true;
    Bellomberg.portfolio().then(p => { if (alive) setTickers(p.positions.map(x => ({ ticker: x.ticker, nome: x.nome }))); })
      .catch(() => { if (alive) setTickerError(true); });
    return () => { alive = false; };
  }, []);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  useEffect(() => {
    ++historyGeneration.current;
    setVersions([]); setVersionsTotal(0); setVersionsError(''); setVersionsBusy(false);
    if (!current) return;
    const controller = new AbortController();
    setVersionsBusy(true);
    Journal.history(current.id, 0, controller.signal).then(r => {
      if (!controller.signal.aborted) { setVersions(r.items); setVersionsTotal(r.total); }
    }).catch(e => { if (!controller.signal.aborted) setVersionsError({error:e}); })
      .finally(() => { if (!controller.signal.aborted) setVersionsBusy(false); });
    return () => { controller.abort(); ++historyGeneration.current; };
  }, [current?.id, current?.version, refresh]);

  const accept = (entry: JournalEntry | null) => {
    setCurrent(entry); selectedId.current = entry?.id ?? null; setDraft(journalDraft(entry));
    setConflict(false); setRemote(null); setError(''); setMessage(''); setPending(null);
  };
  useEffect(() => {
    // A route change must not turn an in-flight CREATE into another new note.
    const request = incomingSave.current;
    if (!request) return;
    let active = true;
    request.then(saved => {
      if (!active) return;
      accept(saved); setMessage({ key: 'journal.saved_version', params: {a: saved.version} });
      setOffset(0); setRefresh(x => x + 1);
    }).catch(e => {
      if (!active) return;
      setError({error:e});
      if (e instanceof JournalError && e.status === 409) setConflict(true);
    }).finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, []);
  // Aggiunto con la Nuova (in coda: i test SSR indicizzano gli hook per posizione).
  const [storia, setStoria] = useState(false);
  const navigate = async (to: Destination, discard = false) => {
    if (busy) return;
    if (dirty && !discard) { setPending(to); return; }
    const ticket = ++operation.current;
    if (to === 'new') { accept(null); return; }
    setBusy(true); setError('');
    try { const entry = await Journal.get(to.id); if (ticket === operation.current) accept(entry); }
    catch (e) { if (ticket === operation.current) setError({error:e}); }
    finally { if (ticket === operation.current) setBusy(false); }
  };
  const change = <K extends keyof JournalDraft>(key: K, value: JournalDraft[K]) => {
    setDraft(x => ({ ...x, [key]: value })); setMessage('');
  };
  const save = async () => {
    if (busy || pendingSave || archived || conflict || !draft.title.trim() || !draft.body.trim()) return;
    setBusy(true); setError(''); setMessage('');
    const body = { ...draft, ticker: draft.ticker?.trim().toUpperCase() || null };
    const request = current ? Journal.update(current.id, current.version, body) : Journal.create(body);
    pendingSave = request;
    try {
      const saved = await request;
      sessionDraft = null;
      accept(saved); setMessage({ key: 'journal.saved_version', params: {a: saved.version} }); setOffset(0); setRefresh(x => x + 1);
    } catch (e) {
      setError({error:e});
      if (e instanceof JournalError && e.status === 409) setConflict(true);
    } finally { if (pendingSave === request) pendingSave = null; setBusy(false); }
  };
  const archive = async () => {
    if (!current || dirty || busy) return;
    setBusy(true); setError('');
    try {
      const saved = await Journal.archive(current.id, current.version, !archived);
      accept(saved); setMessage({key: saved.archived_at ? 'journal.archived_message' : 'journal.restored_message'});
      setOffset(0); setRefresh(x => x + 1);
    } catch (e) { setError({error:e}); if (e instanceof JournalError && e.status === 409) setConflict(true); }
    finally { setBusy(false); }
  };
  const compare = async () => {
    if (!current) return;
    setBusy(true);
    try { setRemote(await Journal.get(current.id)); }
    catch (e) { setError({error:e}); }
    finally { setBusy(false); }
  };
  const moreVersions = async () => {
    if (!current || versionsBusy) return;
    const id = current.id, generation = historyGeneration.current;
    const stillCurrent = () => selectedId.current === id && historyGeneration.current === generation;
    setVersionsBusy(true);
    try {
      const r = await Journal.history(id, versions.length);
      if (stillCurrent()) { setVersions(x => [...x, ...r.items]); setVersionsTotal(r.total); }
    } catch (e) { if (stillCurrent()) setVersionsError({error:e}); }
    finally { if (stillCurrent()) setVersionsBusy(false); }
  };

  const icona = (entry: { kind: JournalKind; ticker?: string | null }) => entry.kind === 'macro' || !entry.ticker
    ? <span className="jr-ico is-macro" aria-hidden="true"><Globe /></span>
    : <span className="jr-ico" aria-hidden="true" style={{ background: tinta(entry.ticker) }}>{entry.ticker.replace(/[^A-Za-z0-9]/g, '').slice(0, 2).toUpperCase()}</span>;
  return <ModernPage page="journal" render={() => <JournalViewBoundary render={() => (
  <section className="journal-page" data-layout="worktable" aria-label={tr('journal.aria')}>
    <div className="journal-layout">
      <aside className="journal-library bbn-card" aria-label={tr('journal.library_aria')}>
        <div className="journal-library-head"><h2>{tr('journal.your_notes')}</h2>{entries && <span className="jr-count">{number(entries.total)}</span>}<span className="jr-grow" /><button className="journal-primary bbn-btn is-primary" disabled={busy} onClick={() => navigate('new')}><Plus aria-hidden="true" />{tr('journal.new_note')}</button></div>
        <label className="journal-search"><Search aria-hidden="true" /><span className="jr-sr">{tr('journal.search')}</span><input type="search" placeholder={tr('journal.search_hint')} value={query} maxLength={200} onChange={e => setQuery(e.target.value)} /></label>
        <div className="journal-filters">
          <div className="bbn-seg" role="group" aria-label={tr('journal.show')}>{([['active', 'journal.active_notes'], ['archived', 'journal.archived_notes'], ['all', 'journal.all_notes']] as const).map(([v, k]) => <button key={v} type="button" aria-pressed={status === v} className={status === v ? 'is-on' : ''} onClick={() => { setStatus(v); setOffset(0); }}>{tr(k)}</button>)}</div>
          <div className="bbn-seg" role="group" aria-label={tr('journal.type')}>{([['', 'journal.all_types'], ['thesis', 'journal.thesis_short'], ['macro', 'journal.macro_short']] as const).map(([v, k]) => <button key={v || 'all'} type="button" aria-pressed={kind === v} className={kind === v ? 'is-on' : ''} onClick={() => { setKind(v as JournalKind | ''); setOffset(0); }}>{tr(k)}</button>)}</div>
        </div>
        {loading && <p className="journal-muted" role="status">{tr('journal.loading')}</p>}
        {listError && <div className="journal-error" role="alert">{showNotice(listError,language)}<button className="bbn-link" onClick={() => setRefresh(x => x + 1)}>{tr('journal.retry')}</button></div>}
        {!loading && entries?.total === 0 && <p className="journal-empty">{query || kind || status !== 'active' ? tr('journal.empty_filtered') : tr('journal.empty')}</p>}
        <div className="journal-notes" aria-busy={loading}>{entries?.items.map(entry => <button key={entry.id} disabled={busy || loading} onClick={() => navigate({ id: entry.id })} className={'journal-note ' + (current?.id === entry.id ? 'selected' : '')} aria-current={current?.id === entry.id ? 'true' : undefined}>
          {icona(entry)}
          <span className="jr-note-text"><strong>{entry.title}</strong><span className="journal-excerpt">{entry.excerpt}</span><span className="journal-note-kind">{entry.ticker || (entry.kind === 'macro' ? tr('journal.macro_short') : tr('journal.thesis_short'))} · <time dateTime={entry.updated_at}>{WHEN(entry.updated_at)}</time></span></span>
          <small className="jr-version">{entry.archived_at ? tr('journal.archived') : `v${entry.version}`}</small>
        </button>)}</div>
        {entries && entries.total > 0 && <footer className="journal-pagination"><span>{tr('journal.range',{a:number(offset+1),b:number(Math.min(offset+entries.items.length,entries.total)),c:number(entries.total)})}</span><span className="jr-grow" /><button className="bbn-icon-btn" disabled={!offset || loading} onClick={() => setOffset(Math.max(0, offset - 30))} aria-label={tr('journal.previous')}><ChevronLeft aria-hidden="true" /></button><button className="bbn-icon-btn" disabled={offset + entries.items.length >= entries.total || loading} onClick={() => setOffset(offset + 30)} aria-label={tr('journal.next')}><ChevronRight aria-hidden="true" /></button></footer>}
      </aside>
      <article className="journal-editor bbn-card" aria-label={tr('journal.editor')}>
        <div className="journal-editor-top">{current ? icona(current) : icona({ kind: draft.kind, ticker: draft.ticker })}
          <span className="jr-identity">{dirty && <span className="journal-session-draft">{tr('journal.session_draft')} · </span>}{current ? tr('journal.identity', {a: current.id, b: current.version}) : tr('journal.new_note')}{archived ? tr('journal.archived_suffix') : ''}{current && <> · {WHEN(current.updated_at)}</>}</span>
          <span className={'jr-state ' + (dirty ? 'journal-dirty is-dirty' : current ? 'is-saved' : '')}>{dirty ? tr('journal.unsaved') : current ? <><Check aria-hidden="true" />{tr('journal.saved')}</> : tr('journal.draft')}</span>
          <span className="jr-grow" />
          <button type="button" className="bbn-btn jr-history-btn" aria-expanded={storia} aria-controls="journal-history" disabled={!current} onClick={() => setStoria(true)}><History aria-hidden="true" />{tr('journal.history_open')}{versionsTotal > 0 && <span className="jr-badge">{number(versionsTotal)}</span>}</button></div>
        {pending && <div className="journal-warning" role="alert"><b>{tr('journal.pending_title')}</b><p>{tr('journal.pending_text')}</p><div><button className="bbn-btn" onClick={() => setPending(null)}>{tr('journal.keep_writing')}</button><button className="bbn-btn" onClick={() => navigate(pending, true)}>{tr('journal.discard_continue')}</button></div></div>}
        {error && <div className="journal-error" role="alert">{showNotice(error,language)}</div>}
        {message && <div className="journal-message" role="status">{showNotice(message,language)}</div>}
        {conflict && <div className="journal-warning"><p>{tr('journal.conflict')}</p><button className="bbn-btn" disabled={busy} onClick={compare}>{tr('journal.compare')}</button>
          {remote && <div className="journal-conflict"><h3>{tr('journal.saved_at',{a:number(remote.version),b:WHEN(remote.updated_at)})}</h3><strong>{remote.title}</strong><p>{KIND[remote.kind]}{remote.ticker ? ` · ${remote.ticker}` : ''}</p><pre>{remote.body}</pre>
            <div className="jr-conflict-actions">{!remote.archived_at && <button className="bbn-btn" disabled={busy} onClick={() => { setCurrent(remote); setConflict(false); setRemote(null); setError(''); setMessage({ key: 'journal.kept_on_current' }); }}>{tr('journal.keep_on_current')}</button>}
            {remote.archived_at && <button className="bbn-btn" disabled={busy} onClick={() => { setCurrent(null); selectedId.current = null; setConflict(false); setRemote(null); setError(''); setMessage({ key: 'journal.original_archived' }); }}>{tr('journal.continue_new')}</button>}
            <button className="bbn-btn" disabled={busy} onClick={() => accept(remote)}>{tr('journal.discard_use_current')}</button></div></div>}
        </div>}
        {archived && <div className="journal-warning">{tr('journal.archived_warning')}</div>}
        <fieldset disabled={busy || archived} className="journal-fields">
          <div className="journal-metadata">
            <div className="jr-meta-field"><span className="jr-label" id="journal-kind-label">{tr('journal.note_type')}</span><div className="bbn-seg" role="group" aria-labelledby="journal-kind-label">{(['thesis', 'macro'] as const).map(k => <button key={k} type="button" aria-pressed={draft.kind === k} className={draft.kind === k ? 'is-on' : ''} onClick={() => change('kind', k)}>{KIND[k]}</button>)}</div></div>
            <label className="jr-meta-field jr-ticker"><span className="jr-label">{tr('journal.ticker')} <small>{tr('journal.optional')}</small></span><span className="jr-input"><input list="journal-tickers" value={draft.ticker || ''} maxLength={32} autoCapitalize="characters" autoComplete="off" placeholder={tr('journal.ticker_hint')} onChange={e => change('ticker', e.target.value || null)} />{draft.ticker && tickers.find(x => x.ticker === draft.ticker?.toUpperCase()) && <small>{tickers.find(x => x.ticker === draft.ticker?.toUpperCase())?.nome}</small>}</span><datalist id="journal-tickers">{tickers.map(t => <option key={t.ticker} value={t.ticker}>{t.nome}</option>)}</datalist></label>
          </div>
          {tickerError && <p className="journal-muted">{tr('journal.ticker_unavailable')}</p>}
          <label className="journal-title-label"><span className="jr-sr">{tr('journal.note_title')}</span><input className="journal-title-input" value={draft.title} maxLength={160} placeholder={draft.kind === 'macro' ? tr('journal.title_macro') : tr('journal.title_thesis')} onChange={e => change('title', e.target.value)} /><small className="journal-title-count">{tr('journal.chars',{a:number(draft.title.length),b:number(160)})}</small></label>
          <div className="journal-writing-guide"><span className="jr-label">{tr('journal.outline')}</span><span><i>1</i>{tr('journal.idea')}</span><i aria-hidden="true">→</i><span><i>2</i>{tr('journal.evidence')}</span><i aria-hidden="true">→</i><span><i>3</i>{tr('journal.risks')}</span><i aria-hidden="true">→</i><span><i>4</i>{tr('journal.change_mind')}</span></div>
          <label className="journal-body-label"><span className="jr-sr">{tr('journal.body')}</span><textarea aria-label={tr('journal.body')} value={draft.body} maxLength={BODY_LIMIT} rows={18} spellCheck placeholder={tr('journal.body_hint')} onChange={e => change('body', e.target.value)} /></label>
        </fieldset>
        <footer className="journal-editor-actions"><span className="jr-foot-info"><span>{tr('journal.chars',{a:number(draft.body.length),b:number(BODY_LIMIT)})}</span><span><Lock aria-hidden="true" />{tr('journal.origin')}</span>{current && <span className="journal-origin">{tr('journal.created_note',{a:WHEN(current.created_at)})}</span>}</span>
          <div><button className="bbn-btn" disabled={busy || !dirty} onClick={() => { setDraft(journalDraft(current)); setPending(null); setError(''); setMessage({ key: 'journal.discarded' }); }}>{tr('journal.discard')}</button>
            <button className="journal-primary bbn-btn is-primary" onClick={save} disabled={busy || archived || conflict || !dirty || !draft.title.trim() || !draft.body.trim()}>{busy ? tr('journal.wait') : current ? tr('journal.save_version') : tr('journal.save_note')}</button></div>
          {current && <button className="bbn-btn" disabled={busy || dirty} onClick={archive}><Archive aria-hidden="true" />{archived ? tr('journal.restore') : tr('journal.archive')}</button>}</footer>
      </article>
    </div>
    <div className={'jr-drawer-root' + (storia ? ' is-open' : '')}>
      {storia && <div className="jr-scrim" onClick={() => setStoria(false)} />}
      <aside id="journal-history" className="journal-history jr-drawer" aria-label={tr('journal.history_aria')} role={storia ? 'dialog' : undefined} aria-modal={storia || undefined} onKeyDown={e => { if (e.key === 'Escape') setStoria(false); }}>
        <div className="jr-drawer-head"><h2>{tr('journal.history_title')}</h2><span className="jr-grow" /><button type="button" className="bbn-icon-btn" aria-label={tr('journal.close')} onClick={() => setStoria(false)}><X aria-hidden="true" /></button></div>
        <p>{tr('journal.history_hint')}</p>
        {!current && <div className="journal-empty">{tr('journal.history_empty')}</div>}
        {versionsError && <div className="journal-error" role="alert">{showNotice(versionsError,language)}<button className="bbn-link" onClick={() => setRefresh(x => x + 1)}>{tr('journal.retry')}</button></div>}
        <ol>{versions.map(version => <li key={version.version}><details><summary><span className="journal-version">v{version.version}</span><span className="jr-version-text"><b>{ACTION[version.action]}</b><time dateTime={version.saved_at}>{WHEN(version.saved_at)}</time></span><ChevronRight aria-hidden="true" className="jr-chev" /></summary><div className="journal-version-body"><strong>{version.title}</strong><span>{KIND[version.kind]}{version.ticker ? ` · ${version.ticker}` : ''}</span><pre>{version.body}</pre><small>{tr('journal.origin_user')} · {version.archived_at ? tr('journal.archived_version') : tr('journal.active_version')}</small><button className="bbn-btn" disabled={busy || archived || dirty || conflict || version.version === current?.version} onClick={() => { setDraft(journalDraft(version)); setStoria(false); setMessage({ key: 'journal.version_draft', params: {a: version.version} }); }}>{tr('journal.use_version')}</button></div></details></li>)}</ol>
        {versionsBusy && <p role="status">{tr('journal.history_loading')}</p>}
        {versions.length < versionsTotal && <button className="bbn-btn" disabled={versionsBusy} onClick={moreVersions}>{tr('journal.history_more')}</button>}
      </aside>
    </div>
  </section>
  )} />} />;
}

// Colore stabile per il ticker: lo stesso titolo ha sempre lo stesso cerchio.
function tinta(ticker: string) {
  let h = 0; for (const c of ticker.toUpperCase()) h = (h * 31 + c.charCodeAt(0)) % 360;
  return `hsl(${h} 55% 42%)`;
}
