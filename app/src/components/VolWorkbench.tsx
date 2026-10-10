import { linguaCorrente, localeDi } from '@/i18n/lingua';
import { t as tr } from '@/i18n/t';
import { useLingua } from '@/i18n/provider';
import type { ReactNode } from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import NewInterfaceBoundary from '@/components/NewInterfaceBoundary';
import { Check, ChevronDown, Layers3, Plus, RefreshCw } from 'lucide-react';
import {
  daysToExpiry, expiriesThrough, horizonDate, volNumber, volRequest, watchDownload,
  type ChainPage, type Coverage, type DownloadStatus, type ExpiryCatalog, type OptionContract,
} from '@/lib/vol-deck';
import OptionBuilder from '@/components/option-builder/OptionBuilder';
import { optionLeg, type BuilderLeg } from '@/lib/option-builder';
import './vol-workbench.css';
import { localizePayload } from '@/lib/api-presentation';
import type { VolWorkspace } from '@/lib/vol-atlas';

type Mode = VolWorkspace;
type Props = { ticker: string; mode: Mode; coverage?: Coverage; surfaceBusy: boolean;
  onSurface: (result: any, expiries: string[]) => void; onLaboratory: () => void; onAcquisition: () => void };

function DeferredVolWorkbenchView({ render }: { render: () => ReactNode }) { return render(); }

export default function VolWorkbench({ ticker, mode, coverage, surfaceBusy, onSurface, onLaboratory, onAcquisition }: Props) {
  const language = useLingua();
  const [rawCatalog, setCatalog] = useState<ExpiryCatalog | null>(null);
  const catalog = useMemo(() => localizePayload(rawCatalog, language), [rawCatalog, language]);
  const [catalogBusy, setCatalogBusy] = useState(false);
  const [catalogError, setCatalogError] = useState('');
  const [finalExpiry, setFinalExpiry] = useState('');
  const [chainExpiry, setChainExpiry] = useState('');
  const [rawChain, setChain] = useState<ChainPage | null>(null);
  const chain = useMemo(() => localizePayload(rawChain, language), [rawChain, language]);
  const [chainBusy, setChainBusy] = useState(false);
  const [chainError, setChainError] = useState('');
  const [filter, setFilter] = useState('all');
  const [strikeSearch, setStrikeSearch] = useState('');
  const [selectedContract, setInspect] = useState<OptionContract | null>(null);
  const inspect = selectedContract && (chain?.chain.find(row => row.contract === selectedContract.contract && row.type === selectedContract.type && row.strike === selectedContract.strike) || selectedContract);
  // 09/10 (Opus 5.5): legs are references into the chain (option builder), not frozen copies.
  const [legs, setLegs] = useState<BuilderLeg[]>([]);
  // mounted on first visit, then kept: tab switches do not lose chains, scenario or inputs
  const [labSeen, setLabSeen] = useState(mode === 'laboratory');
  useEffect(() => { if (mode === 'laboratory') setLabSeen(true); }, [mode]);
  const [rawDownload, setDownload] = useState<DownloadStatus | null>(null);
  const download = useMemo(() => localizePayload(rawDownload, language), [rawDownload, language]);
  const [downloadError, setDownloadError] = useState('');
  const [downloadAction, setDownloadAction] = useState(false);
  const [sliceBusy, setSliceBusy] = useState(false);
  const [clock, setClock] = useState(Date.now());
  const downloadRequest = useRef<AbortController | null>(null);
  const surfaceRequest = useRef<AbortController | null>(null);
  const downloadRef = useRef<DownloadStatus | null>(null);
  const savedJobs = useRef(new Map<string, DownloadStatus>());
  const catalogRequest = useRef<AbortController | null>(null);
  const chainRequest = useRef<AbortController | null>(null);

  useEffect(() => { const timer = setInterval(() => setClock(Date.now()), 10000); return () => clearInterval(timer); }, []);

  useEffect(() => {
    catalogRequest.current?.abort(); chainRequest.current?.abort();
    downloadRequest.current?.abort(); surfaceRequest.current?.abort();
    setCatalog(null); setCatalogError(''); setChain(null); setChainError(''); setFinalExpiry('');
    setChainExpiry(''); setInspect(null); setLegs([]); setCatalogBusy(false); setChainBusy(false);
    setFilter('all'); setStrikeSearch(''); setDownloadError(''); setDownloadAction(false); setSliceBusy(false);
    const saved = savedJobs.current.get(ticker) || null;
    downloadRef.current = saved; setDownload(saved);
    if (ticker) void loadCatalog(false);
    if (saved) {
      const controller = new AbortController(); downloadRequest.current = controller;
      void observeDownload(saved, controller, false);
    }
    return () => {
      catalogRequest.current?.abort(); chainRequest.current?.abort();
      downloadRequest.current?.abort(); surfaceRequest.current?.abort();
      const old = downloadRef.current;
      if (old && ['running', 'queued'].includes(old.state)) {
        // Do not claim cancellation until acknowledged; reopening the ticker rereads status.
        void volRequest<DownloadStatus>(`/options/download/${old.id}/pause`, {}).then(s => savedJobs.current.set(old.ticker, s)).catch(() => {});
      }
    };
  }, [ticker]);

  async function loadCatalog(more: boolean) {
    catalogRequest.current?.abort();
    const controller = new AbortController(); catalogRequest.current = controller;
    setCatalogBusy(true); setCatalogError('');
    try {
      let previous = more ? catalog : null;
      for (;;) {
        const suffix = previous?.next_after ? '?after=' + encodeURIComponent(previous.next_after) : '';
        const result = await volRequest<ExpiryCatalog>(`/options/expiry_catalog/${encodeURIComponent(ticker)}${suffix}`, undefined, controller.signal);
        if (controller.signal.aborted) return;
        if (!Array.isArray(result.expirations) || result.ticker !== ticker) throw new Error(tr('voldeck.ui_catalogue_has_no_expiries_or_a_different_ticker_107'));
        const dates = Array.from(new Set([...(previous?.expirations || []), ...result.expirations])).sort();
        const accumulated: ExpiryCatalog = { ...result, expirations: dates, requests_used: (previous?.requests_used || 0) + result.requests_used };
        setCatalog(accumulated); setCatalogError(result.error || '');
        if (!previous) setChainExpiry(dates[0] || '');
        if (result.complete || result.error) break;
        if (!result.next_after || result.next_after === previous?.next_after) throw new Error(tr('voldeck.ui_incomplete_catalogue_cursor_did_not_advance_108'));
        previous = accumulated;
      }
    } catch (e) { if (!controller.signal.aborted) setCatalogError(String(e instanceof Error ? e.message : e)); }
    finally { if (!controller.signal.aborted) setCatalogBusy(false); }
  }

  async function showSurface(job: DownloadStatus) {
    surfaceRequest.current?.abort();
    const controller = new AbortController(); surfaceRequest.current = controller;
    setSliceBusy(true); setDownloadError('');
    try {
      const result = await volRequest<any>(`/options/download/${job.id}/surface`, undefined, controller.signal);
      if (result.error) throw new Error(result.error);
      if (!controller.signal.aborted && downloadRef.current?.id === job.id) onSurface(result, job.expirations);
    } catch (e) { if (!controller.signal.aborted) setDownloadError(e instanceof Error ? e.message : String(e)); }
    finally { if (!controller.signal.aborted) setSliceBusy(false); }
  }

  async function observeDownload(job: DownloadStatus, controller: AbortController, render = true) {
    try {
      const final = await watchDownload(job.id, job.ticker,
        () => volRequest<DownloadStatus>(`/options/download/${job.id}/status`, undefined, controller.signal),
        status => { setDownload(status); downloadRef.current = status; savedJobs.current.set(status.ticker, status); }, controller.signal);
      if (render && final.state === 'complete') await showSurface(final);
    } catch (e) { if (!controller.signal.aborted) setDownloadError(e instanceof Error ? e.message : String(e)); }
  }

  // `surface=false`: the option builder asks for chains only; the page must not jump to the surface.
  async function startDownload(expiries?: string[], surface = true) {
    downloadRequest.current?.abort(); surfaceRequest.current?.abort(); chainRequest.current?.abort();
    const controller = new AbortController(); downloadRequest.current = controller;
    setDownloadAction(true); setDownloadError(''); setChain(null); setChainBusy(false); setSliceBusy(false);
    try {
      const old = downloadRef.current;
      if (old && ['queued', 'running'].includes(old.state)) await volRequest(`/options/download/${old.id}/pause`, {});
      if (controller.signal.aborted) return;
      // Retain the returned id even if the user changes ticker while POST is in flight.
      const job = await volRequest<DownloadStatus>(`/options/download/${encodeURIComponent(ticker)}`, expiries ? { expiries } : {});
      savedJobs.current.set(job.ticker, job);
      if (controller.signal.aborted) { await volRequest(`/options/download/${job.id}/pause`, {}); return; }
      setDownload(job); downloadRef.current = job;
      setDownloadAction(false);
      await observeDownload(job, controller, surface);
    } catch (e) { if (!controller.signal.aborted) setDownloadError(e instanceof Error ? e.message : String(e)); }
    finally { if (!controller.signal.aborted) setDownloadAction(false); }
  }

  async function controlDownload(action: 'pause' | 'resume') {
    if (!download) return;
    downloadRequest.current?.abort();
    const controller = new AbortController(); downloadRequest.current = controller;
    setDownloadAction(true); setDownloadError('');
    try {
      const job = await volRequest<DownloadStatus>(`/options/download/${download.id}/${action}`, {});
      savedJobs.current.set(job.ticker, job);
      if (controller.signal.aborted) return;
      setDownload(job); downloadRef.current = job; setDownloadAction(false);
      await observeDownload(job, controller, action === 'resume');
    } catch (e) { if (!controller.signal.aborted) setDownloadError(e instanceof Error ? e.message : String(e)); }
    finally { if (!controller.signal.aborted) setDownloadAction(false); }
  }

  async function loadChain(offset = 0) {
    if (!download?.rows.some(row => row.expiry === chainExpiry && row.n_contracts > 0)) {
      await startDownload([chainExpiry]); return;
    }
    chainRequest.current?.abort();
    const controller = new AbortController(); chainRequest.current = controller;
    setChainBusy(true); setChainError(''); setInspect(null);
    try {
      const query = new URLSearchParams({ expiry: chainExpiry, offset: String(offset), limit: '250', side: filter, strike: strikeSearch.replace(',', '.') });
      const result = await volRequest<ChainPage>(`/options/download/${download.id}/chain?${query}`, undefined, controller.signal);
      if (controller.signal.aborted) return;
      if (!Array.isArray(result.chain)) throw new Error(tr('voldeck.ui_response_has_no_contract_list_109'));
      setChain(result);
      setChainError(result.error || '');
    } catch (e) { if (!controller.signal.aborted) setChainError(String(e instanceof Error ? e.message : e)); }
    finally { if (!controller.signal.aborted) setChainBusy(false); }
  }

  useEffect(() => {
    if (!download?.rows.some(row => row.expiry === chainExpiry && row.n_contracts > 0)) return;
    const timer = setTimeout(() => { void loadChain(0); }, 180);
    return () => { clearTimeout(timer); chainRequest.current?.abort(); };
  }, [download?.id, download?.state, chainExpiry, filter, strikeSearch]);

  const months = useMemo(() => {
    const groups = new Map<string, string[]>();
    for (const e of catalog?.expirations || []) groups.set(e.slice(0, 7), [...(groups.get(e.slice(0, 7)) || []), e]);
    return [...groups];
  }, [catalog]);
  const visibleRows = chain?.chain || [];
  const selected = expiriesThrough(catalog?.expirations || [], finalExpiry);
  const downloadBusy = downloadAction || !!download && ['queued', 'running'].includes(download.state);
  const downloadStale = !!download && (download.stale || clock - Date.parse(download.started_at) > download.cache_ttl_seconds * 1000);
  const downloadLabel = download && (download.download_complete ? tr('voldeck.ui_download_complete_138') : ({ queued: tr('voldeck.ui_queued_139'), running: tr('voldeck.ui_downloading_140'), paused: tr('voldeck.ui_paused_141'), error: tr('voldeck.ui_download_interrupted_142'), complete: tr('voldeck.ui_download_with_gaps_143') })[download.state]);
  const coverageLabel = { loaded: tr('voldeck.ui_loaded_171'), partial: tr('voldeck.ui_partial_172'), error: tr('voldeck.ui_error_173'), excluded: tr('voldeck.ui_excluded_174') };
  // 13/09: every workspace but Acquisition hides the acquisition section; its gaps and errors are declared there too.
  const coverageGaps = coverage?.rows.filter(row => row.status !== 'loaded') || [];
  const toolsNotice = !!catalogError || !!downloadError || !!download?.error || (!!download && !download.download_complete) || downloadStale || (!!coverage && !coverage.complete);

  const addContract = (row: OptionContract, side: 'buy' | 'sell') => {
    if (legs.length >= 12 || row.adjusted || row.strike == null || !['call', 'put'].includes(row.type)) return;
    setLegs(prev => [...prev, optionLeg(row.type, side, row.expiry, row.strike as number)]);
  };

  // Option builder: every contract of one downloaded expiry (pages of 1000, no new Polygon request).
  const fetchFullChain = async (expiry: string, signal: AbortSignal): Promise<ChainPage> => {
    const job = downloadRef.current;
    if (!job) throw new Error(tr('voldeck.ui_response_has_no_contract_list_109'));
    const rows: OptionContract[] = [];
    for (let offset = 0; ;) {
      const query = new URLSearchParams({ expiry, offset: String(offset), limit: '1000', side: 'all', strike: '' });
      const page = await volRequest<ChainPage>(`/options/download/${job.id}/chain?${query}`, undefined, signal);
      if (!Array.isArray(page.chain)) throw new Error(tr('voldeck.ui_response_has_no_contract_list_109'));
      rows.push(...page.chain);
      if (!page.has_more || page.next_offset == null || page.next_offset <= offset) return { ...page, chain: rows };
      offset = page.next_offset;
    }
  };
  // Option builder: download the requested expiries, keeping those already in a selective job.
  const requestExpiries = (expiries: string[]) => {
    const job = downloadRef.current;
    const wanted = expiries.filter(Boolean);
    if (!wanted.length) return;
    if (job && wanted.every(e => job.expirations.includes(e)) && ['queued', 'running'].includes(job.state)) return;
    const keep = job && job.scope === 'selected' ? job.expirations : [];
    void startDownload([...new Set([...keep, ...wanted])].sort(), false);
  };

  return <NewInterfaceBoundary language={language}>
  <DeferredVolWorkbenchView render={() => (<div className="vol-workbench">
    <section className="vd-catalog" aria-labelledby="vd-calendar-title" hidden={mode !== 'acquisition'}>
      <div className="vd-section-head"><div><h2 id="vd-calendar-title">{tr('voldeck.ui_how_far_ahead_do_you_want_to_look_110')}</h2>
        <p>{ticker ? tr('voldeck.fmt__a_choose_the_final_expiry_all_available_earlier_4', {a: ticker}) : tr('voldeck.ui_enter_a_ticker_to_explore_expiries_the_laboratory_also_111')}</p></div>
        {ticker && <button className="vd-icon-button" disabled={catalogBusy} onClick={() => loadCatalog(false)} title={tr('voldeck.ui_reload_catalogue_112')}><RefreshCw size={16} /><span>{tr('voldeck.ui_reload_113')}</span></button>}
      </div>
      {catalogBusy && <p className="vd-loading" role="status">{tr('voldeck.ui_reading_all_available_expiries_114')}{' '}<button className="vd-secondary" onClick={() => { catalogRequest.current?.abort(); setCatalogBusy(false); }}>{tr('voldeck.ui_pause_catalogue_115')}</button></p>}
      {catalogError && <p className="vd-error" role="alert">{catalogError}{catalog?.expirations.length ? tr('voldeck.ui_received_dates_remain_visible_catalogue_incomplete_116') : ''}</p>}
      {catalog && <>
        <div className="vd-catalog-summary"><span className={catalog.complete ? 'vd-ok' : 'vd-amber'}>{catalog.complete ? <Check size={14} /> : <Layers3 size={14} />}
          {catalog.expirations.length} {' '}{tr('voldeck.ui_dates_received_117')}{' '}{catalog.complete ? tr('voldeck.ui_catalogue_end_confirmed_by_provider_118') : tr('voldeck.ui_catalogue_still_incomplete_119')}</span>
          <span>{selected.length} {' '}{tr('voldeck.ui_dates_in_the_selected_horizon_120')}</span>
          <span>{catalog.requests_used} {' '}{tr('voldeck.ui_catalogue_requests_121')}</span></div>
        <div className="vd-horizon">
          <label className="vd-field"><span>{tr('voldeck.ui_final_expiry_122')}</span><select aria-label={tr('voldeck.ui_final_expiry_122')} value={finalExpiry} onChange={e => setFinalExpiry(e.target.value)}>
            <option value="">{tr('voldeck.ui_full_chain_all_expiries_123')}</option>
            {finalExpiry && !catalog.expirations.includes(finalExpiry) && <option value={finalExpiry}>{tr('voldeck.ui_by_124')}{' '}{new Date(finalExpiry + 'T12:00:00Z').toLocaleDateString(localeDi(linguaCorrente()))}</option>}
            {catalog.expirations.map(e => <option key={e} value={e}>{new Date(e + 'T12:00:00Z').toLocaleDateString(localeDi(linguaCorrente()), { day: 'numeric', month: 'long', year: 'numeric' })} · {daysToExpiry(e)}{tr('voldeck.short_days')}</option>)}
          </select></label>
          <div className="vd-actions">{([[1, tr('voldeck.ui_1_month_125')], [3, tr('voldeck.ui_3_months_126')], [6, tr('voldeck.ui_6_months_127')], [12, tr('voldeck.ui_1_year_128')]] as const).map(([months, label]) => <button className="vd-secondary" key={months} aria-pressed={finalExpiry === horizonDate(months)} onClick={() => setFinalExpiry(horizonDate(months))}>{label}</button>)}
            <button className="vd-secondary" aria-pressed={!finalExpiry} onClick={() => setFinalExpiry('')}>{tr('voldeck.ui_full_chain_129')}</button>
          </div>
          <p>{selected.length ? tr('voldeck.fmt_from_the_first_available_date_to_a_b_expiries_5', {a: new Date(selected[selected.length - 1] + 'T12:00:00Z').toLocaleDateString(localeDi(linguaCorrente())), b: selected.length}) : tr('voldeck.ui_no_expiries_available_within_this_horizon_130')}{!catalog.complete ? tr('voldeck.ui_catalogue_loading_131') : ''}</p>
        </div>
        <details className="vd-expiry-list"><summary>{tr('voldeck.ui_expiry_list_and_coverage_132')}</summary>
        <div className="vd-months">{months.map(([month, dates]) => <div className="vd-month" key={month}>
          <h3>{new Date(month + '-01T12:00:00Z').toLocaleDateString(localeDi(linguaCorrente()), { month: 'long', year: 'numeric' })}</h3>
          <div>{dates.map(e => { const days = daysToExpiry(e); const isSelected = selected.includes(e); const status = coverage?.rows.find(r => r.expiry === e);
            return <span key={e} className={'vd-date ' + (isSelected ? 'selected ' : '') + (status?.status || '')}
              data-selected={isSelected}
              title={days < 2 ? tr('voldeck.fmt__a_available_in_the_chain_excluded_from_the_dte__6', {a: e}) : `${e}${status?.reason ? ': ' + status.reason : ''}`}
              ><b>{e.slice(8)}</b><small>{days}{tr('voldeck.short_days')}</small>{isSelected && <Check size={11} />}</span>;
        })}</div>
        </div>)}</div></details>
        <div className="vd-actions">
          {!catalog.complete && !catalogBusy && <button className="vd-secondary" onClick={() => loadCatalog(true)}><ChevronDown size={14} />{tr('voldeck.ui_resume_catalogue_133')}</button>}
          <button className="vd-primary" disabled={!selected.length || catalogBusy || !catalog.complete || downloadBusy || surfaceBusy} onClick={() => startDownload(finalExpiry ? selected : undefined)}>{tr('voldeck.ui_load_chain_and_surface_134')}</button>
          <button className="vd-secondary" onClick={onLaboratory}>{tr('voldeck.ui_chain_greeks_and_strategies_135')}</button>
          <small>{tr('voldeck.ui_the_full_chain_includes_all_provider_dates_and_contrac_136')}</small>
        </div>
      </>}
      {downloadError && <p className="vd-error" role="alert">{downloadError}</p>}
      {download && <div className="vd-download" aria-label={tr('voldeck.ui_chain_download_progress_137')}>
        <div className="vd-catalog-summary" role="status">
          <strong className={download.download_complete && !downloadStale ? 'vd-ok' : 'vd-amber'}>{downloadLabel}</strong>
          {downloadStale && <span className="vd-amber">{tr('voldeck.ui_stale_acquisition_started_more_than_144')}{' '}{Math.round(download.cache_ttl_seconds / 60)} {' '}{tr('voldeck.ui_minutes_ago_145')}</span>}
          {download.pause_requested && download.state === 'running' && <span>{tr('voldeck.ui_pause_requested_the_in_flight_response_will_be_retaine_146')}</span>}
          <span>{download.n_contracts.toLocaleString(localeDi(linguaCorrente()))} {' '}{tr('voldeck.ui_contracts_147')}{' '}{download.pages_received} {' '}{tr('voldeck.ui_pages_148')}</span>
          <span>{download.completed_expiries}/{download.expirations.length} {' '}{tr('voldeck.ui_expiries_downloaded_149')}{!download.catalog_complete ? tr('voldeck.ui_catalogue_still_open_150') : ''}</span>
          <span>{download.scope === 'all' ? tr('voldeck.ui_whole_catalogue_151') : tr('voldeck.ui_requested_dates_only_152')}{download.current_expiry ? ` · ${download.current_expiry}` : ''}</span>
          {!!download.page_revisions && <span>{download.page_revisions} {' '}{tr('voldeck.ui_responses_refreshed_during_resume_153')}{' '}{download.superseded_contracts || 0} {' '}{tr('voldeck.ui_superseded_rows_excluded_154')}</span>}
        </div>
        <progress aria-label={tr('voldeck.ui_downloaded_expiries_155')} value={download.completed_expiries} max={Math.max(1, download.expirations.length)} style={{ width: '100%' }} />
        {download.error && <p className="vd-error" role="alert">{download.error} {' '}{tr('voldeck.ui_received_contracts_remain_available_156')}</p>}
        {download.spot_error && <p className="vd-stale">Spot: {download.spot_error}</p>}
        <div className="vd-actions">
          {['queued', 'running'].includes(download.state) && <button className="vd-secondary" disabled={downloadAction} onClick={() => controlDownload('pause')}>{tr('voldeck.ui_pause_download_157')}</button>}
          {['paused', 'error'].includes(download.state) && <button className="vd-primary" disabled={downloadAction || (download.state === 'error' && !download.retryable)} onClick={() => controlDownload('resume')}>{tr('voldeck.ui_resume_download_158')}</button>}
          <button className="vd-secondary" disabled={!download.n_contracts || sliceBusy || downloadAction} onClick={() => showSurface(download)}>{sliceBusy ? tr('voldeck.ui_building_surface_159') : tr('voldeck.ui_show_received_surface_160')}</button>
          <small>{download.duplicates} {' '}{tr('voldeck.ui_duplicates_identified_161')}{' '}{download.malformed_contracts} {' '}{tr('voldeck.ui_unreadable_rows_download_162')}{' '}{new Date(download.updated_at).toLocaleString(localeDi(linguaCorrente()))}{tr('voldeck.ui_retained_for_163')}{' '}{Math.round(download.retention_seconds / 60)} {' '}{tr('voldeck.ui_minutes_of_inactivity_until_backend_restart_this_is_no_164')}</small>
        </div>
      </div>}
      {coverage && <details className="vd-coverage" open={!coverage.complete}>
        <summary>{tr('voldeck.ui_latest_surface_coverage_165')}{' '}{coverage.loaded.length}/{coverage.requested.length} {' '}{tr('voldeck.ui_curves_166')}{' '}{coverage.excluded.length} {' '}{tr('voldeck.ui_excluded_167')}{' '}{coverage.errors.length} {' '}{tr('voldeck.ui_errors_168')}{!coverage.complete ? tr('voldeck.ui_incomplete_mesh_169') : ''}{coverage.download_complete === true ? tr('voldeck.ui_all_data_downloaded_170') : ''}</summary>
        <ul>{coverage.rows.map(row => <li key={row.expiry} className={row.status}><span>{row.expiry}</span><b>{coverageLabel[row.status]}</b><span>{row.reason || tr('voldeck.fmt__a_contracts_received_7', {a: row.n_contracts ?? tr('voldeck.ui_n_a_15')})}</span></li>)}</ul>
      </details>}
    </section>
    {mode !== 'acquisition' && toolsNotice && <section className="va-context-action" aria-label={tr('voldeck.coverage_sources')} data-vol-coverage-notice>
      <div className="vd-actions">
        <div className="vd-actions" role="status">
          {catalogError && <span className="vd-amber">{tr('voldeck.catalog')}: {catalogError}</span>}
          {download && (!download.download_complete || downloadStale || !!download.error) && <span className="vd-amber">{downloadLabel} · {download.completed_expiries}/{download.expirations.length} {tr('voldeck.ui_expiries_downloaded_149')}{downloadStale ? ` · ${tr('voldeck.ui_stale_acquisition_started_more_than_144')} ${Math.round(download.cache_ttl_seconds / 60)} ${tr('voldeck.ui_minutes_ago_145')}` : ''}{download.error ? ` · ${download.error}` : ''}</span>}
          {downloadError && <span className="vd-amber">{tr('voldeck.ui_reported_error_60')} {downloadError}</span>}
          {coverage && <span>{tr('voldeck.ui_latest_surface_coverage_165')} {coverage.loaded.length}/{coverage.requested.length} {tr('voldeck.ui_curves_166')} {coverage.excluded.length} {tr('voldeck.ui_excluded_167')} {coverage.errors.length} {tr('voldeck.ui_errors_168')}{!coverage.complete ? tr('voldeck.ui_incomplete_mesh_169') : ''}</span>}
          {coverageGaps.map(row => <span key={row.expiry} className="vd-amber" title={row.reason || undefined}>{row.expiry} · {coverageLabel[row.status]}{row.reason ? ` — ${row.reason}` : ''}</span>)}
        </div>
        <button type="button" className="vd-secondary" onClick={onAcquisition}>{tr('voldeck.coverage_sources')}</button>
      </div>
    </section>}

    <div>
      <section className="vd-chain" aria-labelledby="vd-chain-title" hidden={mode !== 'chain'}>
        <div className="vd-section-head"><div><h2 id="vd-chain-title">{tr('voldeck.ui_observed_chain_175')}</h2><p>{tr('voldeck.ui_provider_quotes_iv_and_greeks_select_a_contract_to_ins_176')}</p></div>
          <div className="vd-actions"><label>{tr('voldeck.ui_expiry_177')}{' '}<select value={chainExpiry} aria-label={tr('voldeck.ui_chain_expiry_178')} onChange={e => {
            chainRequest.current?.abort(); setChainBusy(false); setChainExpiry(e.target.value); setChain(null); setChainError(''); setInspect(null);
          }}><option value="">{tr('voldeck.ui_choose_a_date_179')}</option>{catalog?.expirations.map(e => <option key={e} value={e}>{e} · {daysToExpiry(e)}{tr('voldeck.short_days')}</option>)}</select></label>
          <button className="vd-primary" disabled={!chainExpiry || chainBusy || (downloadBusy && !download?.rows.some(row => row.expiry === chainExpiry && row.n_contracts > 0))} onClick={() => loadChain(0)}>{chainBusy ? tr('voldeck.ui_reading_180') : tr('voldeck.ui_load_chain_181')}</button></div>
        </div>
        {chainError && <p className="vd-error" role="alert">{chainError}</p>}
        {chain && <>
          <div className="vd-chain-tools"><div className="vd-segment">{[['all', tr('voldeck.ui_all_182')], ['call', 'Call'], ['put', 'Put']].map(([id, label]) => <button key={id} aria-pressed={filter === id} onClick={() => setFilter(id)}>{label}</button>)}</div>
            <label>{tr('voldeck.ui_find_strike_183')}{' '}<input value={strikeSearch} inputMode="decimal" aria-label={tr('voldeck.ui_find_strike_183')} onChange={e => setStrikeSearch(e.target.value)} /></label>
            <span>{chain.n_contracts} {' '}{tr('voldeck.ui_downloaded_contracts_184')}{' '}{chain.filtered_contracts ?? chain.n_contracts} {' '}{tr('voldeck.ui_matching_filters_185')}{' '}{chain.chain_complete ? tr('voldeck.ui_expiry_fully_downloaded_186') : tr('voldeck.ui_expiry_download_incomplete_187')}{chain.malformed_contracts ? tr('voldeck.fmt__a_unreadable_rows_8', {a: chain.malformed_contracts}) : ''}</span>
            <span>{tr('voldeck.chainDownloadedAt', { time: new Date(chain._timestamp).toLocaleTimeString(localeDi(linguaCorrente())) })}{chain.cached ? tr('voldeck.ui_declared_cache_188') : ''}</span></div>
          <div className="vd-chain-scroll"><table><thead><tr><th>{tr('voldeck.ui_type_189')}</th><th>Strike</th><th>Bid</th><th>Ask</th><th>IV %</th><th>Delta</th><th>Gamma</th><th>Vega</th><th>Theta</th><th>OI</th><th>{tr('voldeck.ui_quality_190')}</th><th>{tr('voldeck.ui_strategy_191')}</th></tr></thead>
            <tbody>{visibleRows.map((row, i) => <tr key={row.contract || i} className={(row.strike && chain.spot && Math.abs(row.strike / chain.spot - 1) < .01 ? 'atm ' : '') + (inspect === row ? 'inspected' : '')}>
              <td><button className={'vd-contract-type ' + row.type} onClick={() => setInspect(row)} aria-label={tr('voldeck.fmt_inspect_a_strike_b__9', {a: row.type, b: row.strike ?? tr('voldeck.na')})}>{row.type}</button></td>
              <th scope="row"><button className="vd-cell-button" onClick={() => setInspect(row)}>{volNumber(row.strike)}</button></th>
              <td>{volNumber(row.bid)}</td><td>{volNumber(row.ask)}</td><td>{volNumber(row.iv == null ? null : row.iv * 100, 1)}</td>
              <td>{volNumber(row.delta, 3)}</td><td>{volNumber(row.gamma, 4)}</td><td>{volNumber(row.vega, 3)}</td><td>{volNumber(row.theta, 3)}</td><td>{volNumber(row.oi, 0)}</td>
              <td><button className="vd-quality" onClick={() => setInspect(row)}>{row.quality.length ? tr('voldeck.fmt__a_notes_10', {a: row.quality.length}) : tr('voldeck.ui_fields_present_192')}</button></td>
              <td className="vd-leg-actions"><button disabled={legs.length >= 12 || !row.strike || row.adjusted || !['call', 'put'].includes(row.type)} onClick={() => addContract(row, 'buy')} aria-label={tr('voldeck.fmt_add_buy_a_b__11', {a: row.type, b: row.strike ?? tr('voldeck.na')})}><Plus size={12} />{tr('voldeck.ui_buy_193')}</button><button disabled={legs.length >= 12 || !row.strike || row.adjusted || !['call', 'put'].includes(row.type)} onClick={() => addContract(row, 'sell')}>{tr('voldeck.ui_sell_194')}</button></td>
            </tr>)}</tbody></table>
            {!visibleRows.length && <p className="vd-empty">{tr('voldeck.ui_no_contracts_match_the_selected_filters_195')}</p>}
          </div>
          <div className="vd-actions">
            <button className="vd-secondary" disabled={chainBusy || !chain.offset} onClick={() => loadChain(Math.max(0, (chain.offset || 0) - 250))}>{tr('voldeck.ui_previous_contracts_196')}</button>
            <button className="vd-secondary" disabled={chainBusy || !chain.has_more} onClick={() => loadChain(chain.next_offset || 0)}>{tr('voldeck.ui_next_contracts_197')}</button>
            <span>{tr('voldeck.ui_rows_198')}{' '}{visibleRows.length ? (chain.offset || 0) + 1 : 0}–{(chain.offset || 0) + visibleRows.length} {' '}{tr('voldeck.ui_view_pagination_without_additional_polygon_requests_199')}</span>
            <small>{tr('voldeck.ui_greeks_and_prices_per_underlying_unit_the_multiplier_a_200')}</small></div>
          {inspect && <div className="vd-contract-inspector"><h3>{inspect.contract || `${inspect.type} ${inspect.strike}`} <span>{inspect.exercise_style || tr('voldeck.ui_exercise_style_n_a_201')}</span></h3>
            <p>{inspect._source} {' '}{tr('voldeck.ui_quote_202')}{' '}{inspect.quote_timestamp ? new Date(inspect.quote_timestamp).toLocaleString(localeDi(linguaCorrente())) : tr('voldeck.ui_timestamp_n_a_203')} · {inspect.quote_timeframe || tr('voldeck.ui_delay_n_a_204')} {' '}{tr('voldeck.ui_multiplier_205')}{' '}{volNumber(inspect.multiplier, 0)} · rho {volNumber(inspect.rho, 3)}</p>
            {inspect.quality.length ? <ul>{inspect.quality.map(note => <li key={note}>{note}</li>)}</ul> : <p>{tr('voldeck.ui_required_fields_are_present_check_the_quote_timestamp__206')}</p>}</div>}
        </>}
        {!chain && !chainBusy && <div className="vd-empty">{tr('voldeck.ui_choose_an_expiry_and_load_its_chain_missing_data_will__207')}</div>}
      </section>
      <div hidden={mode !== 'laboratory'}>{(labSeen || mode === 'laboratory') && <OptionBuilder key={ticker} ticker={ticker} download={download} catalog={catalog?.expirations || []}
        downloadBusy={downloadBusy} fetchChain={fetchFullChain} requestExpiries={requestExpiries} legs={legs} setLegs={setLegs} />}</div>
    </div>
  </div>)} />
  </NewInterfaceBoundary>;
}
