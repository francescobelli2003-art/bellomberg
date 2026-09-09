import { useEffect, useRef, useState } from 'react';
import { Bellomberg } from '@/lib/api';
import { Journal, JournalError, journalChanged, journalDraft,
  type JournalDraft, type JournalEntry, type JournalKind, type JournalPageResult,
  type JournalRevision, type JournalStatus, type JournalSummary } from '@/lib/journal';
import './journal.css';

const WHEN = (value: string) => new Date(value).toLocaleString('it-IT', { dateStyle: 'medium', timeStyle: 'short' });
const BODY_LIMIT = 30000;
const KIND = { thesis: 'Tesi su un titolo', macro: 'Scenario macro' };
const ACTION = { create: 'Nota creata', update: 'Testo aggiornato', archive: 'Archiviata', restore: 'Ripristinata' };
type Destination = { id: number } | 'new';
// Bozza solo in memoria della sessione: sopravvive alla navigazione interna,
// senza scrivere contenuti privati in localStorage o fuori da SQLite.
let sessionDraft: { entry: JournalEntry | null; draft: JournalDraft } | null = null;
let pendingSave: Promise<JournalEntry> | null = null;

export default function JournalPage() {
  const incomingSave = useRef(pendingSave);
  const [entries, setEntries] = useState<JournalPageResult<JournalSummary> | null>(null);
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<JournalStatus>('active');
  const [kind, setKind] = useState<JournalKind | ''>('');
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(false);
  const [listError, setListError] = useState('');
  const [current, setCurrent] = useState<JournalEntry | null>(() => sessionDraft?.entry ?? null);
  const [draft, setDraft] = useState<JournalDraft>(() => sessionDraft?.draft ?? journalDraft());
  const [busy, setBusy] = useState(!!incomingSave.current);
  const [message, setMessage] = useState(() => sessionDraft ? 'Bozza recuperata da questa sessione. Salvala per conservarla nel Diario.' : '');
  const [error, setError] = useState('');
  const [pending, setPending] = useState<Destination | null>(null);
  const [versions, setVersions] = useState<JournalRevision[]>([]);
  const [versionsTotal, setVersionsTotal] = useState(0);
  const [versionsBusy, setVersionsBusy] = useState(false);
  const [versionsError, setVersionsError] = useState('');
  const [conflict, setConflict] = useState(false);
  const [remote, setRemote] = useState<JournalEntry | null>(null);
  const [tickers, setTickers] = useState<{ ticker: string; nome: string }[]>([]);
  const [tickerError, setTickerError] = useState('');
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
      .catch(e => { if (!controller.signal.aborted) { setEntries(null); setListError(e.message); } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [status, kind, search, offset, refresh]);
  useEffect(() => {
    let alive = true;
    Bellomberg.portfolio().then(p => { if (alive) setTickers(p.positions.map(x => ({ ticker: x.ticker, nome: x.nome }))); })
      .catch(() => { if (alive) setTickerError('Suggerimenti dal portafoglio non disponibili. Puoi scrivere il ticker.'); });
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
    }).catch(e => { if (!controller.signal.aborted) setVersionsError(e.message); })
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
      accept(saved); setMessage(`Versione ${saved.version} salvata nel Diario.`);
      setOffset(0); setRefresh(x => x + 1);
    }).catch(e => {
      if (!active) return;
      setError((e as Error).message);
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
    catch (e) { if (ticket === operation.current) setError((e as Error).message); }
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
      accept(saved); setMessage(`Versione ${saved.version} salvata nel Diario.`); setOffset(0); setRefresh(x => x + 1);
    } catch (e) {
      setError((e as Error).message);
      if (e instanceof JournalError && e.status === 409) setConflict(true);
    } finally { if (pendingSave === request) pendingSave = null; setBusy(false); }
  };
  const archive = async () => {
    if (!current || dirty || busy) return;
    setBusy(true); setError('');
    try {
      const saved = await Journal.archive(current.id, current.version, !archived);
      accept(saved); setMessage(saved.archived_at ? 'Nota archiviata. Puoi ripristinarla in ogni momento.' : 'Nota ripristinata. Puoi aggiornarla.');
      setOffset(0); setRefresh(x => x + 1);
    } catch (e) { setError((e as Error).message); if (e instanceof JournalError && e.status === 409) setConflict(true); }
    finally { setBusy(false); }
  };
  const compare = async () => {
    if (!current) return;
    setBusy(true);
    try { setRemote(await Journal.get(current.id)); }
    catch (e) { setError((e as Error).message); }
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
    } catch (e) { if (stillCurrent()) setVersionsError((e as Error).message); }
    finally { if (stillCurrent()) setVersionsBusy(false); }
  };

  return <section className="journal-page" aria-label="Diario di investimento">
    <header className="journal-head">
      <div><h1>Il filo delle tue idee</h1><p>Scrivi una tesi, annota nuove prove, ritrova cosa pensavi prima.</p></div>
      <div className="journal-private"><span aria-hidden="true">◇</span><div>Diario personale<small>Le note restano qui. Il comitato non le legge automaticamente.</small></div></div>
    </header>
    <div className="journal-layout">
      <aside className="journal-library" aria-label="Note del Diario">
        <div className="journal-library-head"><h2>Le tue note</h2><button className="journal-primary" disabled={busy} onClick={() => navigate('new')}>Nuova nota</button></div>
        <label className="journal-search">Cerca nel Diario<input type="search" placeholder="Titolo, ticker o testo" value={query} maxLength={200} onChange={e => setQuery(e.target.value)} /></label>
        <div className="journal-filters">
          <label>Mostra<select value={status} onChange={e => { setStatus(e.target.value as JournalStatus); setOffset(0); }}><option value="active">Note attive</option><option value="archived">Archiviate</option><option value="all">Tutte le note</option></select></label>
          <label>Tipo<select value={kind} onChange={e => { setKind(e.target.value as JournalKind | ''); setOffset(0); }}><option value="">Tutti i tipi</option><option value="thesis">Tesi</option><option value="macro">Macro</option></select></label>
        </div>
        {loading && <p className="journal-muted" role="status">Lettura delle note…</p>}
        {listError && <div className="journal-error" role="alert">{listError}<button onClick={() => setRefresh(x => x + 1)}>Riprova</button></div>}
        {!loading && entries?.total === 0 && <p className="journal-empty">{query || kind || status !== 'active' ? 'Nessuna nota con questi filtri.' : 'La prima nota può partire da una domanda: quale evidenza cambierebbe la mia idea?'}</p>}
        <div className="journal-notes" aria-busy={loading}>{entries?.items.map(entry => <button key={entry.id} disabled={busy || loading} onClick={() => navigate({ id: entry.id })} className={'journal-note ' + (current?.id === entry.id ? 'selected' : '')} aria-current={current?.id === entry.id ? 'true' : undefined}>
          <span className="journal-note-kind">{entry.ticker || (entry.kind === 'macro' ? 'Macro' : 'Tesi')}<small>{entry.archived_at ? 'Archiviata' : `v${entry.version}`}</small></span>
          <strong>{entry.title}</strong><span className="journal-excerpt">{entry.excerpt}</span><time dateTime={entry.updated_at}>{WHEN(entry.updated_at)}</time>
        </button>)}</div>
        {entries && entries.total > 0 && <footer className="journal-pagination"><span>{offset + 1}–{Math.min(offset + entries.items.length, entries.total)} di {entries.total}</span><button disabled={!offset || loading} onClick={() => setOffset(Math.max(0, offset - 30))} aria-label="Pagina precedente">‹</button><button disabled={offset + entries.items.length >= entries.total || loading} onClick={() => setOffset(offset + 30)} aria-label="Pagina successiva">›</button></footer>}
      </aside>
      <article className="journal-editor" aria-label="Editor della nota">
        <div className="journal-editor-top"><span>{current ? `Nota ${current.id} · Versione ${current.version}` : 'Nuova nota'}{archived ? ' · Archiviata' : ''}</span><span className={dirty ? 'journal-dirty' : ''}>{dirty ? 'Modifiche da salvare' : current ? 'Salvata' : 'Bozza'}</span></div>
        {pending && <div className="journal-warning" role="alert"><b>La bozza contiene modifiche non salvate.</b><p>Puoi continuare a scrivere oppure scartarla per aprire l’altra nota.</p><div><button onClick={() => setPending(null)}>Continua a scrivere</button><button onClick={() => navigate(pending, true)}>Scarta bozza e continua</button></div></div>}
        {error && <div className="journal-error" role="alert">{error}</div>}
        {message && <div className="journal-message" role="status">{message}</div>}
        {conflict && <div className="journal-warning"><p>La tua bozza è conservata. Confrontala con l’ultima versione prima di salvare.</p><button disabled={busy} onClick={compare}>Confronta versione corrente</button>
          {remote && <div className="journal-conflict"><h3>Versione {remote.version} salvata · {WHEN(remote.updated_at)}</h3><strong>{remote.title}</strong><p>{KIND[remote.kind]}{remote.ticker ? ` · ${remote.ticker}` : ''}</p><pre>{remote.body}</pre>
            {!remote.archived_at && <button disabled={busy} onClick={() => { setCurrent(remote); setConflict(false); setRemote(null); setError(''); setMessage('Bozza mantenuta sulla versione corrente. Verifica il testo e salva una nuova revisione.'); }}>Mantieni la mia bozza sulla versione corrente</button>}
            {remote.archived_at && <button disabled={busy} onClick={() => { setCurrent(null); selectedId.current = null; setConflict(false); setRemote(null); setError(''); setMessage('La nota originale resta archiviata. La tua bozza è pronta da salvare come nuova nota.'); }}>Continua la bozza come nuova nota</button>}
            <button disabled={busy} onClick={() => accept(remote)}>Scarta bozza e usa la versione corrente</button></div>}
        </div>}
        {archived && <div className="journal-warning">Questa nota è nell’archivio. Ripristinala per continuare a scrivere.</div>}
        <fieldset disabled={busy || archived} className="journal-fields">
          <div className="journal-metadata"><label>Tipo di nota<select value={draft.kind} onChange={e => change('kind', e.target.value as JournalKind)}><option value="thesis">Tesi su un titolo</option><option value="macro">Scenario macro</option></select></label>
            <label>Ticker <small>facoltativo</small><input list="journal-tickers" value={draft.ticker || ''} maxLength={32} autoCapitalize="characters" autoComplete="off" placeholder="Scegli o scrivi un ticker" onChange={e => change('ticker', e.target.value || null)} /><datalist id="journal-tickers">{tickers.map(t => <option key={t.ticker} value={t.ticker}>{t.nome}</option>)}</datalist></label></div>
          {tickerError && <p className="journal-muted">{tickerError}</p>}
          <label className="journal-title-label">Titolo<input className="journal-title-input" value={draft.title} maxLength={160} placeholder={draft.kind === 'macro' ? 'Quale scenario sto osservando?' : 'La tesi in una frase'} onChange={e => change('title', e.target.value)} /></label>
          <div className="journal-writing-guide"><span>Idea</span><i aria-hidden="true">→</i><span>Evidenze</span><i aria-hidden="true">→</i><span>Rischi</span><i aria-hidden="true">→</i><span>Cosa mi farebbe cambiare idea</span></div>
          <label className="journal-body-label">La tua nota<textarea aria-label="La tua nota" value={draft.body} maxLength={BODY_LIMIT} rows={18} spellCheck placeholder="Scrivi liberamente. Distingui la tua ipotesi dai fatti osservati, indica le fonti e annota gli eventi da seguire." onChange={e => change('body', e.target.value)} /></label>
        </fieldset>
        <div className="journal-writing-foot"><span>{draft.body.length.toLocaleString('it-IT')} / {BODY_LIMIT.toLocaleString('it-IT')} caratteri</span><span>Origine: inserita dall’utente</span></div>
        <footer className="journal-editor-actions"><div><button className="journal-primary" onClick={save} disabled={busy || archived || conflict || !dirty || !draft.title.trim() || !draft.body.trim()}>{busy ? 'Attendi…' : current ? 'Salva nuova versione' : 'Salva nota'}</button>
          <button disabled={busy || !dirty} onClick={() => { setDraft(journalDraft(current)); setPending(null); setError(''); setMessage('Modifiche scartate.'); }}>Scarta modifiche</button></div>
          {current && <button disabled={busy || dirty} onClick={archive}>{archived ? 'Ripristina dall’archivio' : 'Archivia nota'}</button>}</footer>
        {current && <p className="journal-origin">Creata {WHEN(current.created_at)}. Ogni salvataggio conserva la versione precedente. Le note del Diario non modificano il mandato o le tesi delle posizioni.</p>}
      </article>
      <aside className="journal-history" aria-label="Cronologia della nota"><h2>Come evolve la tua idea</h2><p>Rileggi le versioni precedenti. Puoi usarle come bozza di una nuova revisione.</p>
        {!current && <div className="journal-empty">La cronologia inizia quando salvi la prima nota.</div>}
        {versionsError && <div className="journal-error" role="alert">{versionsError}<button onClick={() => setRefresh(x => x + 1)}>Riprova</button></div>}
        <ol>{versions.map(version => <li key={version.version}><details><summary><span className="journal-version">v{version.version}</span><span>{ACTION[version.action]}<time dateTime={version.saved_at}>{WHEN(version.saved_at)}</time></span></summary><div className="journal-version-body"><strong>{version.title}</strong><span>{KIND[version.kind]}{version.ticker ? ` · ${version.ticker}` : ''}</span><pre>{version.body}</pre><small>Origine utente · {version.archived_at ? 'Archiviata in questa versione' : 'Attiva in questa versione'}</small><button disabled={busy || archived || dirty || conflict || version.version === current?.version} onClick={() => { setDraft(journalDraft(version)); setMessage(`Versione ${version.version} caricata come bozza. Salvala per creare una nuova revisione.`); }}>Usa questa versione come bozza</button></div></details></li>)}</ol>
        {versionsBusy && <p role="status">Lettura cronologia…</p>}
        {versions.length < versionsTotal && <button disabled={versionsBusy} onClick={moreVersions}>Carica versioni precedenti</button>}
      </aside>
    </div>
  </section>;
}
