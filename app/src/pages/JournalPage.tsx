import { showNotice, type Notice } from '@/lib/mandato-presentation';
import { useLingua, useT } from '@/i18n/provider';
import { localeDi } from '@/i18n/lingua';
import { t as tr } from '@/i18n/t';
import { useEffect, useRef, useState } from 'react';
import { Bellomberg } from '@/lib/api';
import { Journal, JournalError, journalChanged, journalDraft,
  type JournalDraft, type JournalEntry, type JournalKind, type JournalPageResult,
  type JournalRevision, type JournalStatus, type JournalSummary } from '@/lib/journal';
import './journal.css';


const BODY_LIMIT = 30000;
type Destination = { id: number } | 'new';
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

  return <section className="journal-page" data-layout="worktable" aria-label={tr('journal.aria')}>
    <header className="journal-head">
      <div><h1>{tr('journal.title')}</h1><p>{tr('journal.subtitle')}</p></div>
      <div className="journal-private"><span aria-hidden="true">◇</span><div>{tr('journal.private')}<small>{tr('journal.privacy')}</small></div></div>
    </header>
    <div className="journal-layout">
      <aside className="journal-library" aria-label={tr('journal.library_aria')}>
        <div className="journal-library-head"><h2>{tr('journal.your_notes')}</h2><button className="journal-primary" disabled={busy} onClick={() => navigate('new')}>{tr('journal.new_note')}</button></div>
        <label className="journal-search">{tr('journal.search')}<input type="search" placeholder={tr('journal.search_hint')} value={query} maxLength={200} onChange={e => setQuery(e.target.value)} /></label>
        <div className="journal-filters">
          <label>{tr('journal.show')}<select value={status} onChange={e => { setStatus(e.target.value as JournalStatus); setOffset(0); }}><option value="active">{tr('journal.active_notes')}</option><option value="archived">{tr('journal.archived_notes')}</option><option value="all">{tr('journal.all_notes')}</option></select></label>
          <label>{tr('journal.type')}<select value={kind} onChange={e => { setKind(e.target.value as JournalKind | ''); setOffset(0); }}><option value="">{tr('journal.all_types')}</option><option value="thesis">{tr('journal.thesis_short')}</option><option value="macro">{tr('journal.macro_short')}</option></select></label>
        </div>
        {loading && <p className="journal-muted" role="status">{tr('journal.loading')}</p>}
        {listError && <div className="journal-error" role="alert">{showNotice(listError,language)}<button onClick={() => setRefresh(x => x + 1)}>{tr('journal.retry')}</button></div>}
        {!loading && entries?.total === 0 && <p className="journal-empty">{query || kind || status !== 'active' ? tr('journal.empty_filtered') : tr('journal.empty')}</p>}
        <div className="journal-notes" aria-busy={loading}>{entries?.items.map(entry => <button key={entry.id} disabled={busy || loading} onClick={() => navigate({ id: entry.id })} className={'journal-note ' + (current?.id === entry.id ? 'selected' : '')} aria-current={current?.id === entry.id ? 'true' : undefined}>
          <span className="journal-note-kind">{entry.ticker || (entry.kind === 'macro' ? tr('journal.macro_short') : tr('journal.thesis_short'))}<small>{entry.archived_at ? tr('journal.archived') : `v${entry.version}`}</small></span>
          <strong>{entry.title}</strong><span className="journal-excerpt">{entry.excerpt}</span><time dateTime={entry.updated_at}>{WHEN(entry.updated_at)}</time>
        </button>)}</div>
        {entries && entries.total > 0 && <footer className="journal-pagination"><span>{tr('journal.range',{a:number(offset+1),b:number(Math.min(offset+entries.items.length,entries.total)),c:number(entries.total)})}</span><button disabled={!offset || loading} onClick={() => setOffset(Math.max(0, offset - 30))} aria-label={tr('journal.previous')}>‹</button><button disabled={offset + entries.items.length >= entries.total || loading} onClick={() => setOffset(offset + 30)} aria-label={tr('journal.next')}>›</button></footer>}
      </aside>
      <article className="journal-editor" aria-label={tr('journal.editor')}>
        <div className="journal-editor-top"><span>{dirty && <span className="journal-session-draft">{tr('journal.session_draft')} · </span>}{current ? tr('journal.identity', {a: current.id, b: current.version}) : tr('journal.new_note')}{archived ? tr('journal.archived_suffix') : ''}</span><span className={dirty ? 'journal-dirty' : ''}>{dirty ? tr('journal.unsaved') : current ? tr('journal.saved') : tr('journal.draft')}</span></div>
        {pending && <div className="journal-warning" role="alert"><b>{tr('journal.pending_title')}</b><p>{tr('journal.pending_text')}</p><div><button onClick={() => setPending(null)}>{tr('journal.keep_writing')}</button><button onClick={() => navigate(pending, true)}>{tr('journal.discard_continue')}</button></div></div>}
        {error && <div className="journal-error" role="alert">{showNotice(error,language)}</div>}
        {message && <div className="journal-message" role="status">{showNotice(message,language)}</div>}
        {conflict && <div className="journal-warning"><p>{tr('journal.conflict')}</p><button disabled={busy} onClick={compare}>{tr('journal.compare')}</button>
          {remote && <div className="journal-conflict"><h3>{tr('journal.saved_at',{a:number(remote.version),b:WHEN(remote.updated_at)})}</h3><strong>{remote.title}</strong><p>{KIND[remote.kind]}{remote.ticker ? ` · ${remote.ticker}` : ''}</p><pre>{remote.body}</pre>
            {!remote.archived_at && <button disabled={busy} onClick={() => { setCurrent(remote); setConflict(false); setRemote(null); setError(''); setMessage({ key: 'journal.kept_on_current' }); }}>{tr('journal.keep_on_current')}</button>}
            {remote.archived_at && <button disabled={busy} onClick={() => { setCurrent(null); selectedId.current = null; setConflict(false); setRemote(null); setError(''); setMessage({ key: 'journal.original_archived' }); }}>{tr('journal.continue_new')}</button>}
            <button disabled={busy} onClick={() => accept(remote)}>{tr('journal.discard_use_current')}</button></div>}
        </div>}
        {archived && <div className="journal-warning">{tr('journal.archived_warning')}</div>}
        <fieldset disabled={busy || archived} className="journal-fields">
          <div className="journal-metadata"><label>{tr('journal.note_type')}<select value={draft.kind} onChange={e => change('kind', e.target.value as JournalKind)}><option value="thesis">{tr('journal.thesis')}</option><option value="macro">{tr('journal.macro')}</option></select></label>
            <label>{tr('journal.ticker')} <small>{tr('journal.optional')}</small><input list="journal-tickers" value={draft.ticker || ''} maxLength={32} autoCapitalize="characters" autoComplete="off" placeholder={tr('journal.ticker_hint')} onChange={e => change('ticker', e.target.value || null)} /><datalist id="journal-tickers">{tickers.map(t => <option key={t.ticker} value={t.ticker}>{t.nome}</option>)}</datalist></label></div>
          {tickerError && <p className="journal-muted">{tr('journal.ticker_unavailable')}</p>}
          <label className="journal-title-label">{tr('journal.note_title')}<small className="journal-title-count">{tr('journal.chars',{a:number(draft.title.length),b:number(160)})}</small><input className="journal-title-input" value={draft.title} maxLength={160} placeholder={draft.kind === 'macro' ? tr('journal.title_macro') : tr('journal.title_thesis')} onChange={e => change('title', e.target.value)} /></label>
          <div className="journal-writing-guide"><span>{tr('journal.idea')}</span><i aria-hidden="true">→</i><span>{tr('journal.evidence')}</span><i aria-hidden="true">→</i><span>{tr('journal.risks')}</span><i aria-hidden="true">→</i><span>{tr('journal.change_mind')}</span></div>
          <label className="journal-body-label">{tr('journal.body')}<textarea aria-label={tr('journal.body')} value={draft.body} maxLength={BODY_LIMIT} rows={18} spellCheck placeholder={tr('journal.body_hint')} onChange={e => change('body', e.target.value)} /></label>
        </fieldset>
        <div className="journal-writing-foot"><span>{tr('journal.chars',{a:number(draft.body.length),b:number(BODY_LIMIT)})}</span><span>{tr('journal.origin')}</span></div>
        <footer className="journal-editor-actions"><span>{tr('journal.footer')}</span><div><button className="journal-primary" onClick={save} disabled={busy || archived || conflict || !dirty || !draft.title.trim() || !draft.body.trim()}>{busy ? tr('journal.wait') : current ? tr('journal.save_version') : tr('journal.save_note')}</button>
          <button disabled={busy || !dirty} onClick={() => { setDraft(journalDraft(current)); setPending(null); setError(''); setMessage({ key: 'journal.discarded' }); }}>{tr('journal.discard')}</button></div>
          {current && <button disabled={busy || dirty} onClick={archive}>{archived ? tr('journal.restore') : tr('journal.archive')}</button>}</footer>
        {current && <p className="journal-origin">{tr('journal.created_note',{a:WHEN(current.created_at)})}</p>}
      </article>
      <aside className="journal-history" aria-label={tr('journal.history_aria')}><h2>{tr('journal.history_title')}</h2><p>{tr('journal.history_hint')}</p>
        {!current && <div className="journal-empty">{tr('journal.history_empty')}</div>}
        {versionsError && <div className="journal-error" role="alert">{showNotice(versionsError,language)}<button onClick={() => setRefresh(x => x + 1)}>{tr('journal.retry')}</button></div>}
        <ol>{versions.map(version => <li key={version.version}><details><summary><span className="journal-version">v{version.version}</span><span>{ACTION[version.action]}<time dateTime={version.saved_at}>{WHEN(version.saved_at)}</time></span></summary><div className="journal-version-body"><strong>{version.title}</strong><span>{KIND[version.kind]}{version.ticker ? ` · ${version.ticker}` : ''}</span><pre>{version.body}</pre><small>{tr('journal.origin_user')} · {version.archived_at ? tr('journal.archived_version') : tr('journal.active_version')}</small><button disabled={busy || archived || dirty || conflict || version.version === current?.version} onClick={() => { setDraft(journalDraft(version)); setMessage({ key: 'journal.version_draft', params: {a: version.version} }); }}>{tr('journal.use_version')}</button></div></details></li>)}</ol>
        {versionsBusy && <p role="status">{tr('journal.history_loading')}</p>}
        {versions.length < versionsTotal && <button disabled={versionsBusy} onClick={moreVersions}>{tr('journal.history_more')}</button>}
      </aside>
    </div>
  </section>;
}
