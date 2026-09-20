import { useEffect, useRef, useState } from 'react';
import { externalWebUrl } from '../../electron/security';
import { Bellomberg, type FilingCitation, type FilingDiff, type FilingListing, type FilingRunDetail } from '@/lib/api';
import { useT } from '@/i18n/provider';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import { leggiDetail } from '@/lib/quota';

const errorText = (error: any) => leggiDetail(error?.response?.data?.detail ?? error?.message ?? error) || String(error);
const shown = (value: unknown, missing: string) => value == null || value === '' ? missing : String(value);
const dateText = (value?: string | null) => {
  if (!value) return null;
  const time = Date.parse(value);
  return Number.isFinite(time) ? new Intl.DateTimeFormat(localeDi(linguaCorrente()), { dateStyle: 'medium', timeStyle: 'short' }).format(time) : value;
};

function Reasons({ items }: { items?: string[] }) {
  if (!items?.length) return null;
  return <ul className="list-disc pl-4 space-y-0.5 text-amber text-xs">{items.map((item, i) => <li key={i}>{item}</li>)}</ul>;
}

function Citation({ item, label }: { item?: FilingCitation; label: string }) {
  const tr = useT();
  if (!item) return null;
  const safeUrl = externalWebUrl(item.url || '');
  return <div className="border-l-2 border-border pl-2 py-1 text-xs break-words">
    <div className="text-faint uppercase text-[10px]">{label} · {item.sezione || tr('filings.unknown')}</div>
    {item.testo && <blockquote className="text-text-dim whitespace-pre-wrap">{item.testo}</blockquote>}
    <div className="text-faint text-[10px] font-mono break-all">
      {safeUrl ? <a href={safeUrl} target="_blank" rel="noreferrer" className="text-cyan hover:underline">{safeUrl}</a> : tr('filings.missingUrl')}
      {item.sha256 && <span> · {tr('filings.hash')}: {item.sha256}</span>}
      {item.pagine_fisiche?.length ? <span> · {tr('filings.pages')}: {item.pagine_fisiche.join(', ')}</span> : null}
      {item.inizio != null && item.fine != null && <span> · {tr('filings.offset')}: {item.inizio}–{item.fine}</span>}
    </div>
  </div>;
}

function DiffView({ diff }: { diff: FilingDiff }) {
  const tr = useT();
  const fmt = (v?: number | null) => v == null ? tr('filings.unknown') :
    new Intl.NumberFormat(localeDi(linguaCorrente()), { style: 'percent', maximumFractionDigits: 1 }).format(v);
  return <div className="space-y-2 text-xs">
    <p>{tr('filings.status')}: <strong>{diff.stato}</strong> · {tr('filings.changes')}: {diff.misure?.cambiamenti ?? diff.cambiamenti?.length ?? tr('filings.unknown')}</p>
    <p>{tr('filings.sections')}: {diff.sezioni_confrontate?.length ? diff.sezioni_confrontate.join(', ') : tr('filings.unknown')}</p>
    {Object.keys(diff.similarita_sezioni || {}).length > 0 && <details>
      <summary className="cursor-pointer text-cyan">{tr('filings.similarity')}</summary>
      <p className="text-faint">{tr('filings.notJudgment')}</p>
      {Object.entries(diff.similarita_sezioni || {}).map(([section, metric]) => <p key={section} className="text-muted">
        {section}: {tr('filings.jaccard')} {fmt(metric.jaccard)} · {tr('filings.cosine')} {fmt(metric.coseno)}
      </p>)}
    </details>}
    <Reasons items={diff.motivi} /><Reasons items={diff.limiti} />
    <div className="max-h-[480px] overflow-y-auto space-y-2">
      {(diff.cambiamenti || []).length === 0 && <p className="text-faint">{tr('filings.noChanges')}</p>}
      {(diff.cambiamenti || []).map((change, i) => <div key={i} className="border border-border p-2 space-y-1">
        <p className="text-gold uppercase text-[10px]">{change.tipo}</p>
        <Citation item={change.prima} label={tr('filings.before')} />
        <Citation item={change.dopo} label={tr('filings.after')} />
      </div>)}
      {!!diff.segmenti_non_confrontabili?.length && <div>
        <h4 className="text-amber">{tr('filings.uncomparable')}: {diff.segmenti_non_confrontabili.length}</h4>
        {diff.segmenti_non_confrontabili.map((part, i) => <div key={i} className="border border-border p-2">
          <Citation item={part.prima} label={tr('filings.before')} /><Citation item={part.dopo} label={tr('filings.after')} />
        </div>)}
      </div>}
    </div>
  </div>;
}

export default function FilingDiffPanel({ ticker }: { ticker: string }) {
  const tr = useT();
  const [listing, setListing] = useState<FilingListing | null>(null);
  const [detail, setDetail] = useState<FilingRunDetail | null>(null);
  const [runId, setRunId] = useState<number | null>(null);
  const [readError, setReadError] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [writeError, setWriteError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [writing, setWriting] = useState(false);
  const [revision, setRevision] = useState(0);
  const [profileJson, setProfileJson] = useState('');
  const [enabled, setEnabled] = useState(false);
  const [intervalHours, setIntervalHours] = useState('168');
  const [qualitative, setQualitative] = useState(false);
  const loadedProfile = useRef<string | null>(null);

  useEffect(() => {
    setListing(null); setDetail(null); setRunId(null); setReadError(null); setRunError(null); setLoading(true);
    setWriteError(null); setNotice(null); setProfileJson(''); loadedProfile.current = null;
  }, [ticker]);

  useEffect(() => {
    let active = true;
    if (!ticker) { setLoading(false); return () => { active = false; }; }
    const load = async () => {
      try {
        const row = await Bellomberg.filingList(ticker);
        if (!active) return;
        setListing(row); setReadError(null);
        setRunId(id => id != null && row.runs?.some(r => r.id === id) ? id : (row.active_run?.id ?? row.runs?.[0]?.id ?? null));
        const profileKey = row.profile ? `${ticker}:${row.profile.version}` : `${ticker}:none`;
        if (loadedProfile.current !== profileKey) {
          loadedProfile.current = profileKey;
          setProfileJson(row.profile ? JSON.stringify(row.profile.profile, null, 2) : '');
          setEnabled(row.profile?.enabled ?? false);
          setIntervalHours(row.profile ? String(row.profile.interval_hours) : '168');
          setQualitative(row.profile?.qualitative_enabled ?? false);
        }
      } catch (error) { if (active) { setReadError(errorText(error)); setListing(null); } }
      finally { if (active) setLoading(false); }
    };
    void load();
    // Poll only while the backend reports an active job; old ticker responses are discarded.
    return () => { active = false; };
  }, [ticker, revision]);

  useEffect(() => {
    if (!listing?.active_run || !ticker) return;
    const timer = window.setTimeout(() => setRevision(n => n + 1), 5000);
    return () => window.clearTimeout(timer);
  }, [listing?.active_run?.id, revision, ticker]);

  useEffect(() => {
    let active = true;
    setDetail(null); setRunError(null);
    if (runId == null) return () => { active = false; };
    Bellomberg.filingRun(runId).then(row => { if (active && row.ticker === ticker) setDetail(row); })
      .catch(error => { if (active) setRunError(errorText(error)); });
    return () => { active = false; };
  }, [ticker, runId, revision]);

  const refresh = async () => {
    setWriting(true); setWriteError(null); setNotice(null);
    try {
      const queued = await Bellomberg.filingRefresh(ticker);
      setRunId(queued.run_id); setNotice(tr('filings.queued')); setRevision(n => n + 1);
    } catch (error) { setWriteError(tr('filings.refreshError', { error: errorText(error) })); }
    finally { setWriting(false); }
  };
  const save = async () => {
    let profile: Record<string, unknown>;
    try {
      const parsed = JSON.parse(profileJson);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error();
      profile = parsed;
    } catch { setWriteError(tr('filings.invalidJson')); return; }
    const interval = Number(intervalHours);
    if (!Number.isSafeInteger(interval) || interval < 1) { setWriteError(tr('filings.invalidInterval')); return; }
    setWriting(true); setWriteError(null); setNotice(null);
    try {
      await Bellomberg.filingSaveProfile(ticker, { profile, enabled, interval_hours: interval, qualitative_enabled: qualitative });
      setNotice(tr('filings.saved')); setRevision(n => n + 1);
    } catch (error) { setWriteError(tr('filings.saveError', { error: errorText(error) })); }
    finally { setWriting(false); }
  };

  const result = detail?.result;
  const diff = result?.confronto_corrente || result?.confronto_storico;
  const citationById: Record<string, FilingCitation> = {};
  diff?.cambiamenti?.forEach((change, index) => {
    if (change.prima) citationById[`C${index + 1}-prima`] = change.prima;
    if (change.dopo) citationById[`C${index + 1}-dopo`] = change.dopo;
  });
  const unknown = tr('filings.unknown');
  return <section className="p3 font-mono text-xs space-y-3" data-testid="filing-diff-panel" data-ticker={ticker}>
    <div className="p3h am">{tr('filings.title')} · {ticker}</div>
    <p className="text-faint text-[10px]">{tr('filings.valuationNote')}</p>
    {loading && <p className="text-muted">{tr('filings.loading')}</p>}
    {readError && <p role="alert" className="text-crimson">{tr('filings.readError', { error: readError })}</p>}
    {listing && <>
      <div className="flex gap-2 items-center flex-wrap">
        <span>{tr('filings.status')}: <strong>{listing.status}</strong></span>
        {listing.reason && <span className="text-amber">{listing.reason}</span>}
        <button type="button" disabled={writing || !!listing.active_run || !listing.profile} onClick={refresh}
          className="tb disabled:opacity-40">{writing ? tr('filings.refreshing') : tr('filings.refresh')}</button>
        {listing.active_run && <span className="text-amber">{tr('filings.queued')} {listing.active_run.status}</span>}
      </div>
      {!listing.profile && <p className="text-amber">{tr('filings.noProfile')}</p>}
      {listing.profile?.next_due_at && <p>{tr('filings.nextDue')}: {dateText(listing.profile.next_due_at)}</p>}
      {writeError && <p role="alert" className="text-crimson">{writeError}</p>}
      {notice && <p role="status" className="text-cyan">{notice}</p>}
      <details className="border border-border p-2">
        <summary className="cursor-pointer text-cyan">{tr('filings.profile')}</summary>
        <p className="text-muted my-2">{tr('filings.profileHelp')}</p>
        <label className="block">{tr('filings.profileJson')}
          <textarea value={profileJson} onChange={e => setProfileJson(e.target.value)} rows={9}
            className="block w-full bg-bg border border-border text-text p-2 font-mono text-xs" spellCheck={false} />
        </label>
        <div className="flex gap-4 my-2 flex-wrap">
          <label><input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} /> {tr('filings.automatic')}</label>
          <label>{tr('filings.interval')} <input type="number" min="1" step="1" value={intervalHours} onChange={e => setIntervalHours(e.target.value)} className="w-20 bg-bg border border-border text-text" /></label>
          <label><input type="checkbox" checked={qualitative} onChange={e => setQualitative(e.target.checked)} /> {tr('filings.qualitative')}</label>
        </div>
        <button type="button" className="tb" disabled={writing} onClick={save}>{writing ? tr('filings.saving') : tr('filings.save')}</button>
      </details>
      <div>
        <label htmlFor={`filing-run-${ticker}`}>{tr('filings.history')}: </label>
        {listing.runs?.length ? <select id={`filing-run-${ticker}`} value={runId ?? ''} onChange={e => setRunId(Number(e.target.value))}
          className="bg-bg border border-border text-text p-1">
          {listing.runs.map(run => <option key={run.id} value={run.id}>#{run.id} · {run.status} · {dateText(run.started_at) || tr('filings.dateUnknown')}</option>)}
        </select> : <span className="text-faint">{tr('filings.noRuns')}</span>}
      </div>
    </>}
    {runError && <p role="alert" className="text-crimson">{tr('filings.runError', { error: runError })}</p>}
    {detail && <div className="space-y-3">
      <p>{tr('filings.run')} #{detail.id}: <strong>{detail.status}</strong> · {detail.trigger || unknown} · {dateText(detail.started_at) || unknown}</p>
      {detail.reason && <p className="text-amber">{detail.reason}</p>}
      {result ? <>
        <p>{tr('filings.status')}: <strong>{result.stato}</strong>{result.ultimo_non_verificato ? <span className="text-amber"> · {tr('filings.unverifiedLatest')}</span> : null}</p>
        <Reasons items={result.motivi} />
        <div className="border border-border p-2 space-y-1">
          <h4 className="text-gold uppercase">{tr('filings.coverage')}: {result.copertura?.stato || unknown}</h4>
          <p>{tr('filings.candidates')}: {shown(result.copertura?.candidati_osservati, unknown)} · {tr('filings.attempts')}: {shown(result.copertura?.documenti_tentati, unknown)} · {tr('filings.maxDocs')}: {shown(result.copertura?.max_documenti, unknown)}</p>
          <Reasons items={result.copertura?.limiti} />
          {result.fonti?.map((source, i) => <p key={i}>{source.nome || unknown}: {source.stato || unknown} {source.motivi?.join('; ')}</p>)}
        </div>
        <div className="border border-border p-2 space-y-1">
          <h4 className="text-gold uppercase">{tr('filings.freshness')}: {result.freschezza?.stato || unknown}</h4>
          <p>{tr('filings.period')}: {result.freschezza?.ultimo_periodo || unknown} · {tr('filings.nextDue')}: {result.freschezza?.next_report_date || unknown}</p>
          {result.freschezza?.next_report_source && externalWebUrl(result.freschezza.next_report_source) && <a href={externalWebUrl(result.freschezza.next_report_source) || undefined} target="_blank" rel="noreferrer" className="text-cyan break-all">{result.freschezza.next_report_source}</a>}
          <Reasons items={result.freschezza?.motivi} />
        </div>
        {!!result.candidati?.length && <details><summary className="cursor-pointer text-cyan">{tr('filings.candidates')}: {result.candidati.length}</summary>
          <div className="max-h-[300px] overflow-y-auto">{result.candidati.map((candidate, i) => <div key={i} className="border-b border-border py-1 break-all">
            {candidate.fonte || unknown} · {candidate.stato} · {candidate.metadati?.periodo_fine || unknown}<Reasons items={candidate.motivi} />
            {candidate.url && externalWebUrl(candidate.url) && <a href={externalWebUrl(candidate.url) || undefined} target="_blank" rel="noreferrer" className="text-cyan">{candidate.url}</a>}
            {candidate.sha256 && <p>{tr('filings.hash')}: {candidate.sha256}</p>}
          </div>)}</div></details>}
        {result.coppia && <div className="border border-border p-2">
          <h4 className="text-gold uppercase">{tr('filings.pair')}: {result.coppia.ambito || unknown}</h4>
          {(['prima', 'dopo'] as const).map(side => { const doc = result.coppia?.[side]; const url = externalWebUrl(doc?.url || ''); return <p key={side} className="break-all">
            {side === 'prima' ? tr('filings.before') : tr('filings.after')}: {doc?.metadati?.periodo_inizio || unknown}–{doc?.metadati?.periodo_fine || unknown} · {doc?.sha256 || unknown} · {url ? <a href={url} target="_blank" rel="noreferrer" className="text-cyan">{url}</a> : tr('filings.missingUrl')}
          </p>; })}
        </div>}
        <div className="border border-border p-2">
          <h4 className="text-gold uppercase">{tr('filings.deterministic')}: {result.confronto_corrente ? tr('filings.current') : result.confronto_storico ? tr('filings.historical') : tr('filings.noComparison')}</h4>
          {diff && <DiffView diff={diff} />}
        </div>
      </> : <p className="text-faint">{tr('filings.noComparison')}</p>}
      <div className="border border-border p-2 space-y-1">
        <h4 className="text-gold uppercase">{tr('filings.ai')}</h4>
        {detail.judgment ? <>
          <p>{tr('filings.aiStatus')}: {detail.judgment.status} {detail.judgment.model && `· ${detail.judgment.model}`}</p>
          {detail.judgment.coverage && <p>{tr('filings.qualitativeCoverage')}: {shown(detail.judgment.coverage.shown, unknown)}/{shown(detail.judgment.coverage.total, unknown)}</p>}
          {detail.judgment.reason && <p className="text-amber">{detail.judgment.reason}</p>}
          {detail.judgment.findings?.map((finding, i) => <div key={i} className="border-l border-border pl-2">
            <strong>{finding.category || unknown}</strong>: {finding.assessment || unknown}
            {!!finding.citations?.length && <div className="space-y-1"><p className="text-faint">{tr('filings.aiCitations')}: {finding.citations.join(', ')}</p>
              {finding.citations.map(id => citationById[id]
                ? <Citation key={id} item={citationById[id]} label={id} />
                : <p key={id} className="text-crimson">{id}: {tr('filings.citationMissing')}</p>)}
            </div>}
          </div>)}
        </> : <p className="text-faint">{tr('filings.aiMissing')}</p>}
        {detail.index && <p>{tr('filings.index')}: {detail.index.status} {detail.index.reason && `· ${detail.index.reason}`}</p>}
      </div>
    </div>}
  </section>;
}
