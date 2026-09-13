import { linguaCorrente, localeDi } from '@/i18n/lingua';
import { t as tr } from '@/i18n/t';
import { useLingua } from '@/i18n/provider';
import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDownLeft, ArrowUpRight, Check, ChevronDown, Layers3, Plus, RefreshCw, Trash2 } from 'lucide-react';
import {
  blankLeg, contractLeg, legSource, daysToExpiry, expiriesThrough, horizonDate, numberInput, numericText, serializeLegs, volNumber, volRequest, watchDownload,
  type ChainPage, type Coverage, type DownloadStatus, type ExpiryCatalog, type LegDraft, type OptionContract, type StrategyResult,
} from '@/lib/vol-deck';
import './vol-workbench.css';
import { localizePayload } from '@/lib/api-presentation';
import type { VolWorkspace } from '@/lib/vol-atlas';

type Mode = VolWorkspace;
type Props = { ticker: string; mode: Mode; coverage?: Coverage; surfaceBusy: boolean;
  onSurface: (result: any, expiries: string[]) => void; onLaboratory: () => void; onAcquisition: () => void };

function Datum({ label, value, unit, tone }: { label: string; value: string; unit?: string; tone?: string }) {
  return <div className={'vd-datum ' + (tone || '')}><span>{label}</span><strong>{value}</strong>{unit && <small>{unit}</small>}</div>;
}

function NumField({ label, ariaLabel, value, onChange, unit, min, max }: { label: string; ariaLabel?: string; value: string; onChange: (s: string) => void; unit?: string; min?: number; max?: number }) {
  return <label className="vd-field"><span>{label}</span><div><input type="text" inputMode="decimal" value={value}
    onChange={e => onChange(e.target.value)} aria-label={ariaLabel || label} autoComplete="off" spellCheck={false}
    aria-description={min == null ? undefined : tr('voldeck.fmt_from_a_to_b__26', {a: min, b: max ?? tr('voldeck.ui_unlimited_106')})} />{unit && <i>{unit}</i>}</div></label>;
}

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
  const [legs, setLegs] = useState<LegDraft[]>([]);
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

  async function startDownload(expiries?: string[]) {
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
      await observeDownload(job, controller);
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
    if (legs.length >= 12 || row.adjusted || !['call', 'put'].includes(row.type)) return;
    setLegs(prev => [...prev, contractLeg(row, side)]);
  };

  return <div className="vol-workbench">
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
            <span>Download {new Date(chain._timestamp).toLocaleTimeString(localeDi(linguaCorrente()))}{chain.cached ? tr('voldeck.ui_declared_cache_188') : ''}</span></div>
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
      <div hidden={mode !== 'laboratory'}><StrategyLab key={ticker} ticker={ticker} legs={legs} setLegs={setLegs} observedSpot={chain?.spot ?? null} /></div>
    </div>
  </div>;
}

function StrategyLab({ ticker, legs, setLegs, observedSpot }: { ticker: string; legs: LegDraft[]; setLegs: (legs: LegDraft[] | ((p: LegDraft[]) => LegDraft[])) => void; observedSpot: number | null }) {
  const [spot, setSpot] = useState(''); const [scenarioSpot, setScenarioSpot] = useState('');
  const [rate, setRate] = useState('0'); const [dividend, setDividend] = useState('0');
  const [elapsed, setElapsed] = useState('0'); const [shift, setShift] = useState('0');
  const [commission, setCommission] = useState('0'); const [currency, setCurrency] = useState('USD');
  const language = useLingua();
  const [rawResult, setResult] = useState<StrategyResult | null>(null);
  const result = useMemo(() => localizePayload(rawResult, language), [rawResult, language]); const [busy, setBusy] = useState(false);
  const [error, setError] = useState(''); const [calculatedKey, setCalculatedKey] = useState('');
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  const configKey = JSON.stringify([legs, spot, scenarioSpot, rate, dividend, elapsed, shift, commission, currency]);
  const stale = result != null && configKey !== calculatedKey;

  function update(id: string, field: keyof LegDraft, value: string) {
    setLegs(prev => prev.map(l => l.id === id ? { ...l, [field]: value, sourceKind: 'edited', source: tr('voldeck.ui_assumption_edited_in_the_laboratory_not_a_current_quot_208') } : l));
  }
  async function simulate() {
    request.current?.abort(); const controller = new AbortController(); request.current = controller;
    setError(''); setBusy(true);
    try {
      const body = { spot: numberInput(spot, tr('voldeck.ui_initial_spot_209')), scenario_spot: numberInput(scenarioSpot || spot, tr('voldeck.ui_scenario_price_210')),
        rate: numberInput(rate, tr('voldeck.ui_rate_253')) / 100, dividend_yield: numberInput(dividend, tr('voldeck.ui_annual_dividend_yield_255')) / 100,
        elapsed_days: numberInput(elapsed, tr('voldeck.ui_elapsed_days_211')), iv_shift: numberInput(shift, tr('voldeck.ui_iv_shock')) / 100,
        commission: numberInput(commission, tr('voldeck.ui_cost_per_contract_212')), currency, legs: serializeLegs(legs) };
      const out = await volRequest<StrategyResult>('/options/strategy/simulate', body, controller.signal);
      if (!controller.signal.aborted) { setResult(out); setCalculatedKey(configKey); }
    } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }
  function pair(kind: 'vertical' | 'straddle' | 'calendar') {
    if (!legs.length || legs.length >= 12) return;
    const first = legs[0]; const second = { ...blankLeg(), ...first, id: blankLeg().id, sourceKind: 'derived' as const, source: tr('voldeck.ui_derived_leg_complete_the_new_fields_before_simulating_213') };
    if (kind === 'vertical') { second.side = first.side === 'buy' ? 'sell' : 'buy'; second.strike = ''; second.premium = ''; }
    if (kind === 'straddle') { second.type = first.type === 'call' ? 'put' : 'call'; second.premium = ''; }
    if (kind === 'calendar') { second.side = first.side === 'buy' ? 'sell' : 'buy'; second.days = ''; second.premium = ''; second.expiry = undefined; }
    setLegs(prev => [...prev, second]);
  }

  return <section className="vd-lab" aria-labelledby="vd-lab-title">
    <div className="vd-section-head"><div><h2 id="vd-lab-title">{tr('voldeck.ui_design_the_strategy_214')}</h2><p>{ticker || tr('voldeck.underlying')} {' '}{tr('voldeck.ui_local_theoretical_laboratory_no_orders_are_sent_215')}</p></div>
      <span className="vd-model">{tr('voldeck.ui_black_scholes_merton_european_216')}</span></div>
    <div className="vd-lab-grid">
      <div className="vd-legs"><div className="vd-legs-heading"><h3>{tr('voldeck.ui_legs_217')}{' '}<span>{legs.length}/12</span></h3><button className="vd-secondary" disabled={legs.length >= 12} onClick={() => setLegs(prev => [...prev, blankLeg()])}><Plus size={14} />{tr('voldeck.ui_manual_218')}</button></div>
        {!legs.length && <div className="vd-leg-empty"><Layers3 size={30} /><h4>{tr('voldeck.ui_start_with_a_contract_219')}</h4><p>{tr('voldeck.ui_use_buy_or_sell_in_the_chain_or_add_a_manual_leg_premi_220')}</p></div>}
        {legs.map((leg, i) => <article className={'vd-leg ' + leg.side} key={leg.id}>
          <div className="vd-leg-head"><span>{leg.side === 'buy' ? <ArrowUpRight size={18} /> : <ArrowDownLeft size={18} />}{tr('voldeck.ui_leg_221')}{' '}{i + 1}</span><button className="vd-icon-button" aria-label={tr('voldeck.fmt_remove_leg_a__12', {a: i + 1})} onClick={() => setLegs(prev => prev.filter(l => l.id !== leg.id))}><Trash2 size={14} /></button></div>
          <div className="vd-leg-kind"><select aria-label={tr('voldeck.fmt_leg_a_direction_13', {a: i + 1})} value={leg.side} onChange={e => update(leg.id, 'side', e.target.value)}><option value="buy">{tr('voldeck.ui_buy_193')}</option><option value="sell">{tr('voldeck.ui_sell_194')}</option></select><select aria-label={tr('voldeck.fmt_leg_a_type_14', {a: i + 1})} value={leg.type} onChange={e => update(leg.id, 'type', e.target.value)}><option value="call">Call</option><option value="put">Put</option></select></div>
          <div className="vd-leg-fields">{([['strike', 'Strike'], ['premium', tr('voldeck.ui_premium_unit_225')], ['quantity', tr('voldeck.ui_contracts_223')], ['multiplier', tr('voldeck.ui_multiplier_224')], ['days', tr('voldeck.ui_days_to_expiry_53')], ['iv', 'IV %']] as const).map(([key, label]) => <NumField key={key} label={label} ariaLabel={tr('voldeck.fmt__a_leg_b__15', {a: label, b: i + 1})} value={leg[key]} onChange={v => update(leg.id, key, v)} />)}</div>
          <p className="vd-leg-source">{legSource(leg)}{leg.quote_timestamp && <><br />{tr('voldeck.ui_quote_226')}{' '}{new Date(leg.quote_timestamp).toLocaleString(localeDi(linguaCorrente()))}</>}</p>
        </article>)}
        {legs.length > 0 && legs.length < 12 && <details className="vd-pair"><summary>{tr('voldeck.ui_build_from_the_first_leg_227')}</summary><button onClick={() => pair('vertical')}>{tr('voldeck.ui_vertical_spread_228')}</button><button onClick={() => pair('straddle')}>Straddle</button><button onClick={() => pair('calendar')}>{tr('voldeck.ui_calendar_spread')}</button><p>{tr('voldeck.ui_complete_the_new_leg_s_strike_days_or_premium_no_quote_229')}</p></details>}
      </div>
      <div className="vd-payoff-area">
        <div className="vd-scenario-controls"><NumField label={tr('voldeck.ui_initial_spot_209')} value={spot} onChange={setSpot} unit={currency} />
          <NumField label={tr('voldeck.ui_scenario_price_210')} value={scenarioSpot} onChange={setScenarioSpot} unit={currency} />
          <NumField label={tr('voldeck.ui_elapsed_time_230')} value={elapsed} onChange={setElapsed} unit={tr('voldeck.unit_days')} />
          <NumField label={tr('voldeck.ui_iv_shock')} value={shift} onChange={setShift} unit={tr('voldeck.unit_points')} /></div>
        <div className="vd-actions">{observedSpot != null && <button className="vd-secondary" onClick={() => { setSpot(numericText(observedSpot)); setScenarioSpot(numericText(observedSpot)); }}>{tr('voldeck.ui_use_chain_spot_231')}{' '}{volNumber(observedSpot)}</button>}
          <small>{tr('voldeck.ui_blank_scenario_price_initial_spot_without_a_price_shoc_232')}</small></div>
        {stale && <p className="vd-stale" role="status">{tr('voldeck.ui_assumptions_changed_the_chart_shows_the_last_simulatio_233')}</p>}
        {error && <p className="vd-error" role="alert">{error}</p>}
        {result ? <>
          <div className="vd-payoff-title"><div><h3>{tr('voldeck.ui_the_shape_of_the_return_234')}</h3><p>{result.same_expiry ? tr('voldeck.fmt_payoff_at_a_days_and_intermediate_theoretical_va_16', {a: volNumber(result.expiry_days, 0)}) : tr('voldeck.ui_mixed_expiries_theoretical_scenario_up_to_the_first_ex_235')}</p></div><span>{result.currency}</span></div>
          <PayoffChart result={result} />
          <div className="vd-result-strip"><Datum label={result.entry_kind === 'debit' ? tr('voldeck.ui_initial_outlay_236') : tr('voldeck.ui_initial_credit_237')} value={volNumber(Math.abs(result.entry_cost))} unit={result.currency} />
            <Datum label={tr('voldeck.ui_scenario_p_l')} value={volNumber(result.scenario.pnl)} unit={tr('voldeck.at_price', { currency: result.currency, price: volNumber(result.scenario.price) })} tone={result.scenario.pnl >= 0 ? 'positive' : 'negative'} />
            <Datum label={tr('voldeck.ui_maximum_profit_at_expiry_238')} value={result.unlimited_profit ? tr('voldeck.ui_unlimited_106') : volNumber(result.max_profit)} unit={result.same_expiry ? result.currency : tr('voldeck.ui_not_defined_for_calendars_239')} />
            <Datum label={tr('voldeck.ui_maximum_loss_at_expiry_240')} value={result.unlimited_loss ? tr('voldeck.ui_unlimited_106') : volNumber(result.max_loss)} unit={result.same_expiry ? result.currency : tr('voldeck.ui_not_defined_for_calendars_241')} /></div>
          <div className="vd-breakeven">{tr('voldeck.ui_breakeven_at_expiry_242')}{' '}<strong>{result.same_expiry ? [...result.breakevens.map(v => volNumber(v)), ...(result.breakeven_intervals || []).map(v => tr('voldeck.fmt_from_a_to_b__26', { a: volNumber(v.from), b: v.to == null ? '∞' : volNumber(v.to) }))].join(' / ') || tr('voldeck.ui_no_zero_crossing_243') : tr('voldeck.ui_n_a_for_mixed_expiries_244')}</strong><span>{tr('voldeck.ui_initial_premiums_and_fees_included_245')}</span></div>
          <div className="vd-greeks"><h4>{tr('voldeck.ui_scenario_greeks_246')}{' '}<small>{tr('voldeck.ui_aggregated_over_quantities_and_multipliers_247')}</small></h4>
            <div>{(['delta', 'gamma', 'vega', 'theta', 'rho'] as const).map(key => <Datum key={key} label={key} value={volNumber(result.scenario[key], key === 'gamma' ? 4 : 2)} unit={result.greek_units[key]} />)}</div></div>
          <ScenarioHeatmap result={result} />
        </> : <div className="vd-payoff-empty"><svg viewBox="0 0 560 180" role="img" aria-label={tr('voldeck.ui_payoff_chart_area_complete_the_legs_to_calculate_248')}><path d="M20 145H540M90 25V162" stroke="#334766" fill="none" /><path d="M35 125H200L355 55H520" stroke="#e9ba64" strokeWidth="3" fill="none" strokeDasharray="6 6" /><text x="300" y="155" fill="#a9bad1" fontSize="12">{tr('voldeck.ui_illustrative_diagram_without_values_249')}</text></svg><h3>{tr('voldeck.ui_your_assumptions_shape_the_payoff_250')}</h3><p>{tr('voldeck.ui_complete_at_least_one_leg_and_the_spot_price_gold_will_251')}</p></div>}
        <div className="vd-model-inputs"><h4>{tr('voldeck.ui_model_assumptions_252')}</h4><div><NumField label={tr('voldeck.ui_annual_rate_254')} value={rate} onChange={setRate} unit="%" /><NumField label={tr('voldeck.ui_annual_dividend_yield_255')} value={dividend} onChange={setDividend} unit="%" /><NumField label={tr('voldeck.ui_initial_cost_contract_256')} value={commission} onChange={setCommission} unit={currency} /><label className="vd-field"><span>{tr('voldeck.ui_common_currency_257')}</span><input value={currency} maxLength={3} onChange={e => setCurrency(e.target.value.toUpperCase())} aria-label={tr('voldeck.ui_simulation_currency_258')} /></label></div>
          <p>{tr('voldeck.ui_initial_zero_rate_dividend_yield_and_costs_are_editabl_259')}</p></div>
        <div className="vd-actions vd-simulate"><button className="vd-primary" disabled={!legs.length || busy} onClick={simulate}>{busy ? tr('voldeck.ui_calculating_260') : stale ? tr('voldeck.ui_recalculate_scenario_261') : tr('voldeck.ui_simulate_strategy_262')}</button><span>{tr('voldeck.ui_local_calculation_only_no_provider_or_ai_model_263')}</span></div>
        <details className="vd-model-notes"><summary>{tr('voldeck.ui_method_and_limitations_264')}</summary><ul>{(result?.limits || [tr('voldeck.ui_european_model_does_not_value_american_early_exercise__265'), tr('voldeck.ui_act_365_calendar_days_intraday_expiry_time_is_not_mode_266'), tr('voldeck.ui_constant_iv_per_leg_with_a_parallel_shock_entry_premiu_267'), tr('voldeck.ui_mixed_expiries_scenarios_stop_at_the_first_expiry_no_i_268'), tr('voldeck.ui_initial_fees_included_slippage_exit_costs_financing_an_269')]).map(note => <li key={note}>{note}</li>)}</ul></details>
      </div>
    </div>
  </section>;
}

function PayoffChart({ result }: { result: StrategyResult }) {
  const [hover, setHover] = useState<number | null>(null);
  const rows = result.curve; const width = 860, height = 340, left = 66, right = 20, top = 22, bottom = 42;
  const values = rows.flatMap(r => [r.today, r.scenario, ...(r.expiry == null ? [] : [r.expiry])]);
  const minX = rows[0].price, maxX = rows[rows.length - 1].price;
  const low = Math.min(0, ...values), high = Math.max(0, ...values), pad = Math.max((high - low) * .12, 1);
  const minY = low - pad, maxY = high + pad;
  const x = (s: number) => left + (s - minX) / (maxX - minX) * (width - left - right);
  const y = (v: number) => top + (maxY - v) / (maxY - minY) * (height - top - bottom);
  const path = (field: 'expiry' | 'today' | 'scenario') => rows.filter(r => r[field] != null).map((r, i) => `${i ? 'L' : 'M'}${x(r.price).toFixed(2)},${y(r[field]!).toFixed(2)}`).join(' ');
  const hoverRow = hover == null ? null : rows[hover];
  const hoverAt = (event: React.PointerEvent<SVGSVGElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const target = minX + ((event.clientX - box.left) / box.width * width - left) / (width - left - right) * (maxX - minX);
    setHover(rows.reduce((best, row, i) => Math.abs(row.price - target) < Math.abs(rows[best].price - target) ? i : best, 0));
  };
  return <div className="vd-chart"><svg viewBox={`0 0 ${width} ${height}`} role="img" tabIndex={0}
    aria-label={tr('voldeck.ui_p_l_chart_gold_at_expiry_cyan_scenario_grey_today_use__270')}
    onPointerMove={hoverAt} onPointerLeave={() => setHover(null)} onKeyDown={e => { if (['ArrowLeft', 'ArrowRight'].includes(e.key)) { e.preventDefault(); setHover(v => Math.max(0, Math.min(rows.length - 1, (v ?? Math.floor(rows.length / 2)) + (e.key === 'ArrowLeft' ? -1 : 1)))); } }}>
    <defs><linearGradient id="vd-profit-wash" x1="0" y1="0" x2="0" y2="1"><stop stopColor="#2cb8a2" stopOpacity=".10" /><stop offset="1" stopColor="#2cb8a2" stopOpacity="0" /></linearGradient></defs>
    <rect x={left} y={top} width={width-left-right} height={y(0)-top} fill="url(#vd-profit-wash)" />
    {[0,1,2,3,4].map(i => { const val = minY + (maxY-minY)*i/4; return <g key={i}><line x1={left} x2={width-right} y1={y(val)} y2={y(val)} stroke="#26364f" strokeDasharray="2 5" /><text x={left-10} y={y(val)+4} textAnchor="end">{volNumber(val, 0)}</text></g>; })}
    {[0,1,2,3,4,5,6].map(i => { const val = minX+(maxX-minX)*i/6; return <g key={i}><text x={x(val)} y={height-15} textAnchor="middle">{volNumber(val, 1)}</text></g>; })}
    <line x1={left} x2={width-right} y1={y(0)} y2={y(0)} stroke="#7689a6" />
    {result.breakevens.filter(v => v >= minX && v <= maxX).map(v => <g key={v}><line x1={x(v)} x2={x(v)} y1={top} y2={height-bottom} stroke="#9b804f" strokeDasharray="3 5" /><circle cx={x(v)} cy={y(0)} r="4" fill="#e9ba64" /></g>)}
    <path d={path('today')} stroke="#91a2ba" strokeWidth="1.6" strokeDasharray="5 5" fill="none" />
    {result.same_expiry && <path d={path('expiry')} stroke="#e9ba64" strokeWidth="3" fill="none" />}
    <path d={path('scenario')} stroke="#55d6ef" strokeWidth="2.5" fill="none" />
    {hoverRow && <g><line x1={x(hoverRow.price)} x2={x(hoverRow.price)} y1={top} y2={height-bottom} stroke="#c6d3e7" strokeDasharray="2 3" /><circle cx={x(hoverRow.price)} cy={y(hoverRow.scenario)} r="5" fill="#55d6ef" stroke="#08101e" strokeWidth="2" /></g>}
  </svg><div className="vd-chart-legend"><span className="expiry">{result.same_expiry ? tr('voldeck.ui_at_expiry_271') : tr('voldeck.ui_single_payoff_not_defined_272')}</span><span className="scenario">{tr('voldeck.ui_theoretical_scenario_273')}</span><span className="today">{tr('voldeck.ui_theoretical_value_today_274')}</span><span>{tr('voldeck.ui_underlying_price_275')}{result.currency})</span></div>
  <div className="vd-chart-reading" aria-live="polite">{hoverRow ? <>{tr('voldeck.ui_price_276')}{' '}<b>{volNumber(hoverRow.price)}</b><span>{tr('voldeck.ui_scenario_p_l')}{' '}<b>{volNumber(hoverRow.scenario)}</b></span><span>{tr('voldeck.ui_p_l_at_expiry_277')}{' '}<b>{volNumber(hoverRow.expiry)}</b></span></> : tr('voldeck.ui_point_at_the_chart_or_use_arrow_keys_to_compare_result_278')}</div></div>;
}

function ScenarioHeatmap({ result }: { result: StrategyResult }) {
  const max = Math.max(1, ...result.heatmap.flatMap(r => r.cells.map(c => Math.abs(c.pnl))));
  return <div className="vd-heatmap"><h4>{tr('voldeck.ui_if_price_changes_as_time_passes_279')}</h4><p>{tr('voldeck.ui_theoretical_p_l_with_the_chosen_iv_shock_columns_price_280')}</p><div><table><thead><tr><th>{tr('voldeck.ui_days_281')}</th>{result.heatmap[0]?.cells.map(c => <th key={c.price}>{volNumber(c.price, 1)}</th>)}</tr></thead><tbody>{result.heatmap.map((row, i) => <tr key={i}><th>{volNumber(row.elapsed_days, 1)}</th>{row.cells.map(c => <td key={c.price} style={{ backgroundColor: c.pnl >= 0 ? `rgba(45,177,161,${.07 + Math.abs(c.pnl)/max*.35})` : `rgba(220,102,124,${.07 + Math.abs(c.pnl)/max*.35})` }} title={tr('voldeck.fmt_price_a_day_b_p_l_c_d__17', {a: volNumber(c.price), b: volNumber(row.elapsed_days, 1), c: volNumber(c.pnl), d: result.currency})}>{volNumber(c.pnl, 0)}</td>)}</tr>)}</tbody></table></div></div>;
}
