// Option builder (09/10/2026, Opus 5.5). Legs on the Polygon chain served by the backend; every
// figure (payoff, P/L, greeks, breakevens, extremes, probability) comes from the backend engine
// POST /options/strategy/simulate. A missing quote/IV/spot/rate is declared n.d.: the engine is
// not called and no figure is shown in its place.
import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import { Download, Plus, RefreshCw, RotateCcw, Trash2 } from 'lucide-react';
import { t as tr } from '@/i18n/t';
import { leggiNumero, leggiNumeroConSegno } from '@/lib/cassa';
import { volRequest, type ChainPage, type DownloadStatus } from '@/lib/vol-deck';
import {
  PRESET_GROUPS, buildPreset, daysToClose, editLeg, engineLegs, engineResultProblem, fieldNumber, horizonDays, invertLeg, optionLeg,
  premiums, quoteLeg, returnOnRisk, stockLeg, strikesOf,
  type BuilderLeg, type ChainContract, type EngineResult, type LegQuote, type NdReason, type PresetId, type PriceMode, type RateQuote, type VolModel,
} from '@/lib/option-builder';
import PayoffChart from './PayoffChart';
import { useFormat } from './useFormat';
import '@/components/nuova/nuova.css';
import './option-builder.css';

type Props = {
  ticker: string;
  download: DownloadStatus | null;
  catalog: string[];
  downloadBusy: boolean;
  fetchChain: (expiry: string, signal: AbortSignal) => Promise<ChainPage>;
  requestExpiries: (expiries: string[]) => void;
  legs: BuilderLeg[];
  setLegs: Dispatch<SetStateAction<BuilderLeg[]>>;
};

type ChainEntry = { key: string; rows: ChainContract[]; page: ChainPage & { spot_source?: string | null; spot_timestamp?: string | null } };

const MAX_LEGS = 12;
const CLOCK_STEP_MS = 5 * 60000;
const read = (s: string) => { const r = leggiNumero(s); return r && r.ok ? r.valore : null; };
const readSigned = (s: string) => { const r = leggiNumeroConSegno(s); return r && r.ok ? r.valore : null; };

export default function OptionBuilder({ ticker, download, catalog, downloadBusy, fetchChain, requestExpiries, legs, setLegs }: Props) {
  const f = useFormat();
  const comma = f.language === 'it';
  // Review L4 (10/10): the clock behind days-to-expiry advances every 5 minutes, not every minute,
  // so an idle page does not re-run the engine for a change of ~0.0007 days; declared in daysNote.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), CLOCK_STEP_MS); return () => clearInterval(timer); }, []);

  // ── chains of the downloaded expiries ──────────────────────────────────
  const [chains, setChains] = useState<Map<string, ChainEntry>>(new Map());
  const [chainErrors, setChainErrors] = useState<Record<string, string>>({});
  const inflight = useRef(new Map<string, AbortController>());
  const [selected, setSelected] = useState('');
  const [pending, setPending] = useState<PresetId | null>(null);
  const [notice, setNotice] = useState('');
  useEffect(() => () => { for (const c of inflight.current.values()) c.abort(); }, []);
  useEffect(() => { setChains(new Map()); setChainErrors({}); setSelected(''); setPending(null); setNotice(''); }, [ticker]);

  const ready = useMemo(() => (download?.rows || []).filter(r => r.n_contracts > 0
    && (r.complete || !['queued', 'running'].includes(download!.state))), [download]);
  const downloaded = useMemo(() => ready.map(r => r.expiry).sort(), [ready]);
  const future = useMemo(() => catalog.filter(e => daysToClose(e, now) > 0), [catalog, now]);

  useEffect(() => {
    if (selected && future.includes(selected)) return;
    const first = downloaded.find(e => daysToClose(e, now) >= 1) || downloaded.find(e => daysToClose(e, now) > 0);
    if (first) setSelected(first);
  }, [downloaded, future, selected, now]);

  const needed = useMemo(() => [...new Set([selected, ...legs.map(l => l.expiry || '')].filter(Boolean))], [selected, legs]);
  useEffect(() => {
    if (!download) return;
    for (const expiry of needed) {
      const row = ready.find(r => r.expiry === expiry);
      if (!row) continue;
      const key = `${download.id}|${row.n_contracts}|${row.complete}`;
      if (chains.get(expiry)?.key === key || inflight.current.has(expiry + key)) continue;
      const controller = new AbortController(); inflight.current.set(expiry + key, controller);
      fetchChain(expiry, controller.signal).then(page => {
        setChains(prev => new Map(prev).set(expiry, { key, rows: page.chain as ChainContract[], page }));
        setChainErrors(prev => { const next = { ...prev }; delete next[expiry]; return next; });
      }).catch(e => { if (!controller.signal.aborted) setChainErrors(prev => ({ ...prev, [expiry]: e instanceof Error ? e.message : String(e) })); })
        .finally(() => inflight.current.delete(expiry + key));
    }
  }, [needed, ready, download?.id, chains, fetchChain]);

  const contracts = useMemo(() => new Map([...chains].map(([e, entry]) => [e, entry.rows] as const)), [chains]);
  const latest = useMemo(() => [...chains.values()].sort((a, b) => String(b.page._timestamp).localeCompare(String(a.page._timestamp)))[0]?.page, [chains]);

  // ── market inputs ──────────────────────────────────────────────────────
  const [spotText, setSpotText] = useState('');
  const chainSpot = latest?.spot ?? download?.spot ?? null;
  const spotSource = latest?.spot_source ?? download?.spot_source ?? null;
  const spotStamp = latest?.spot_timestamp ?? null;
  const manualSpot = spotText.trim() ? read(spotText) : null;
  const spot = spotText.trim() ? manualSpot : chainSpot;
  const [mode, setMode] = useState<PriceMode>('mid');
  // Review M2: legs that outlive the first expiry. Default = forward vol from today's surface.
  const [volModel, setVolModel] = useState<VolModel>('forward');
  const [rate, setRate] = useState<RateQuote | null>(null);
  const [rateText, setRateText] = useState('');
  const [rateManual, setRateManual] = useState(false);
  const [divText, setDivText] = useState('0');
  const [feeText, setFeeText] = useState('0');
  useEffect(() => {
    const controller = new AbortController();
    volRequest<RateQuote>('/options/strategy/rate', undefined, controller.signal).then(r => {
      setRate(r);
      if (r.percent != null) setRateText(prev => prev || fieldNumber(r.percent, comma, 4));
    }).catch(e => { if (!controller.signal.aborted) setRate({ value: null, percent: null, date: null, source: 'FRED DGS3MO', status: 'error', error: e instanceof Error ? e.message : String(e) }); });
    return () => controller.abort();
  }, []);
  const rateValue = rateText.trim() ? readSigned(rateText) : null;
  const divValue = divText.trim() ? readSigned(divText) : null;
  const feeValue = feeText.trim() ? readSigned(feeText) : null;

  // ── scenario ───────────────────────────────────────────────────────────
  const [movePct, setMovePct] = useState(0);
  const [ivPts, setIvPts] = useState(0);
  const [daysPassed, setDaysPassed] = useState(0);
  const quotes = useMemo(() => legs.map(l => quoteLeg(l, contracts, mode, spot, now, read)), [legs, contracts, mode, spot, now]);
  const horizon = horizonDays(quotes);
  const elapsed = horizon == null ? 0 : Math.min(daysPassed, horizon);
  const minIv = Math.min(...quotes.filter(q => q.iv != null).map(q => (q.iv as number) * 100));
  const ivMin = Number.isFinite(minIv) ? Math.min(0, -Math.floor(minIv - 1)) : -20;
  useEffect(() => { if (ivPts < ivMin) setIvPts(ivMin); }, [ivMin, ivPts]);

  // ── engine request ─────────────────────────────────────────────────────
  const engine = engineLegs(legs, quotes);
  const inputErrors: string[] = [];
  // Review L2: while the FRED rate is still loading (and nothing was typed) the rate is pending,
  // not wrong: no red alarm, no engine call, a muted "loading" line instead.
  const rateLoading = rate == null && !rateText.trim();
  if (spot == null) inputErrors.push(tr('optionbuilder.nd_no_spot'));
  if (!rateLoading && (rateValue == null || Math.abs(rateValue) > 100)) inputErrors.push(tr('optionbuilder.rateRequired'));
  if (divValue == null || Math.abs(divValue) > 100) inputErrors.push(tr('optionbuilder.dividendRequired'));
  if (feeValue == null || feeValue < 0) inputErrors.push(tr('optionbuilder.feeRequired'));
  const inputsReady = !inputErrors.length && !rateLoading;
  const body = legs.length && engine.ok && inputsReady ? {
    spot, scenario_spot: (spot as number) * (1 + movePct / 100), rate: (rateValue as number) / 100,
    dividend_yield: (divValue as number) / 100, elapsed_days: elapsed, iv_shift: ivPts / 100,
    commission: feeValue, currency: 'USD', vol_model: volModel, legs: engine.legs } : null;
  const bodyKey = body ? JSON.stringify(body) : '';
  const [result, setResult] = useState<EngineResult | null>(null);
  const [resultKey, setResultKey] = useState('');
  const [engineError, setEngineError] = useState('');
  const [busy, setBusy] = useState(false);
  const request = useRef<AbortController | null>(null);
  useEffect(() => {
    request.current?.abort();
    if (!bodyKey) { setBusy(false); return; }
    const controller = new AbortController(); request.current = controller;
    setBusy(true);
    const timer = setTimeout(() => {
      volRequest<unknown>('/options/strategy/simulate', JSON.parse(bodyKey), controller.signal).then(out => {
        if (controller.signal.aborted) return;
        // Review M3: a response without the fields the page reads is a declared engine error.
        const problem = engineResultProblem(out);
        if (problem) { setResult(null); setResultKey(''); setEngineError(tr('optionbuilder.engineShape', { k: problem })); setBusy(false); return; }
        setResult(out as EngineResult); setResultKey(bodyKey); setEngineError(''); setBusy(false);
      }).catch(e => { if (!controller.signal.aborted) { setEngineError(e instanceof Error ? e.message : String(e)); setBusy(false); } });
    }, 140);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [bodyKey]);
  useEffect(() => { if (!legs.length) { setResult(null); setResultKey(''); setEngineError(''); } }, [legs.length]);
  const current = !!result && resultKey === bodyKey;
  const shown = legs.length && (engine.ok && inputsReady) ? result : null;

  // ── presets ────────────────────────────────────────────────────────────
  const applyPreset = (id: PresetId) => {
    setNotice('');
    if (!selected) { setNotice(tr('optionbuilder.pickExpiry')); return; }
    if (!contracts.get(selected)?.length) {
      setPending(id);
      if (!downloaded.includes(selected)) requestExpiries([selected]);
      return;
    }
    const later = future.filter(e => e > selected);
    const out = buildPreset(id, spot, selected, contracts, later.filter(e => contracts.get(e)?.length));
    if (out.ok) { setLegs(out.legs); setPending(null); setMovePct(0); setDaysPassed(0); return; }
    if (out.reason === 'needs_far_expiry') {
      const far = later.find(e => downloaded.includes(e)) || later[0];
      if (!far) { setNotice(tr('optionbuilder.presetFail_no_far')); return; }
      setPending(id);
      if (!downloaded.includes(far)) requestExpiries([selected, far]);
      else setFarHint(far);
      return;
    }
    setNotice(tr(`optionbuilder.presetFail_${out.reason}` as 'optionbuilder.presetFail_no_spot'));
  };
  const [farHint, setFarHint] = useState('');
  // the calendar's far expiry has to be READ too, even when no leg uses it yet
  useEffect(() => {
    if (!farHint || !download) return;
    const row = ready.find(r => r.expiry === farHint);
    if (!row || contracts.get(farHint)?.length) return;
    const controller = new AbortController();
    // Review L1: a failed read of the far expiry is declared (chain badge + preset notice), not swallowed.
    fetchChain(farHint, controller.signal).then(page => setChains(prev => new Map(prev).set(farHint, { key: `${download.id}|${row.n_contracts}|${row.complete}`, rows: page.chain as ChainContract[], page })))
      .catch(e => {
        if (controller.signal.aborted) return;
        const err = e instanceof Error ? e.message : String(e);
        setChainErrors(prev => ({ ...prev, [farHint]: err }));
        setPending(null); setFarHint('');
        setNotice(tr('optionbuilder.farChainError', { e: farHint, err }));
      });
    return () => controller.abort();
  }, [farHint, ready, download?.id]);
  useEffect(() => {
    if (!pending || !selected || !contracts.get(selected)?.length) return;
    if (pending === 'calendar') {
      const far = future.find(e => e > selected && downloaded.includes(e));
      if (far && !contracts.get(far)?.length) { setFarHint(far); return; }
    }
    applyPreset(pending);
  }, [pending, contracts, selected, downloaded]);

  const addLeg = (kind: 'call' | 'put' | 'stock') => {
    if (legs.length >= MAX_LEGS) return;
    if (kind === 'stock') { setLegs(prev => [...prev, stockLeg('buy', 100)]); return; }
    const chain = contracts.get(selected);
    const ks = strikesOf(chain, kind);
    const k = ks.length && spot != null ? ks.reduce((a, b) => Math.abs(b - spot) < Math.abs(a - spot) ? b : a) : ks[0];
    if (!selected || k == null) { setNotice(tr('optionbuilder.presetFail_no_chain')); return; }
    setLegs(prev => [...prev, optionLeg(kind, 'buy', selected, k)]);
  };
  const update = (id: string, change: Parameters<typeof editLeg>[1]) => setLegs(prev => prev.map(l => l.id === id ? editLeg(l, change) : l));
  const prem = premiums(legs, quotes);
  const nd = engine.ok ? [] : engine.missing;

  const usedExpiries = [...new Set(legs.map(l => l.expiry).filter((e): e is string => !!e))];
  const refresh = () => requestExpiries(usedExpiries.length ? usedExpiries : selected ? [selected] : []);

  if (!ticker) return <section className="ob" aria-labelledby="vd-lab-title"><div className="ob-card ob-empty"><h2 id="vd-lab-title">{tr('optionbuilder.title')}</h2><p>{tr('optionbuilder.noTicker')}</p></div></section>;

  const loadingSelected = !!selected && !contracts.get(selected)?.length;
  const spotWarn = spotSource != null && !/polygon/i.test(spotSource);
  return <section className="ob" aria-labelledby="vd-lab-title" data-option-builder>
    {/* ── head: underlying, expiry, inputs ── */}
    <header className="ob-card ob-head">
      <div className="ob-title">
        <span className="ob-eyebrow">{tr('optionbuilder.eyebrow')}</span>
        <h2 id="vd-lab-title">{ticker} <span>{tr('optionbuilder.title')}</span></h2>
        <p>{tr('optionbuilder.subtitle')}</p>
      </div>
      <div className="ob-spot">
        <span className="ob-k">{tr('optionbuilder.spot')}</span>
        <strong className="ob-num">{f.num(spot, 2)}</strong>
        <span className={'ob-badge' + (spotText.trim() ? ' is-manual' : spot == null ? ' is-bad' : spotWarn ? ' is-warn' : '')}>
          {spotText.trim() ? tr('optionbuilder.manual') : spot == null ? tr('optionbuilder.na') : `${spotSource || tr('optionbuilder.sourceUnknown')} · ${f.time(spotStamp || download?.snapshot_at)}`}
        </span>
        {spotWarn && !spotText.trim() && <small className="ob-warn-t">{tr('optionbuilder.spotNotPolygon')}</small>}
      </div>
      <div className="ob-inputs">
        <Field label={tr('optionbuilder.spotOverride')} value={spotText} onChange={setSpotText} placeholder={fieldNumber(chainSpot, comma, 2)} unit="USD"
          error={spotText.trim() && manualSpot == null ? tr('optionbuilder.unreadable') : ''} />
        <Field label={tr('optionbuilder.rate')} value={rateText} onChange={v => { setRateText(v); setRateManual(true); }} unit="%"
          error={rateText.trim() && rateValue == null ? tr('optionbuilder.unreadable') : ''}
          tag={rateManual ? { text: tr('optionbuilder.manual'), tone: 'manual' } : rate?.value != null
            ? { text: `${rate.source} · ${rate.date}${rate.status === 'stale' ? ' · ' + tr('optionbuilder.stale') : ''}`, tone: rate.status === 'stale' ? 'warn' : 'ok' }
            : rate ? { text: tr('optionbuilder.rateMissing', { err: rate.error || tr('optionbuilder.na') }), tone: 'bad' } : { text: tr('optionbuilder.loading'), tone: 'muted' }} />
        <Field label={tr('optionbuilder.dividend')} value={divText} onChange={setDivText} unit="%"
          error={divText.trim() && divValue == null ? tr('optionbuilder.unreadable') : ''} tag={{ text: tr('optionbuilder.assumptionNoSource'), tone: 'manual' }} />
        <Field label={tr('optionbuilder.commission')} value={feeText} onChange={setFeeText} unit="USD"
          error={feeText.trim() && (feeValue == null || feeValue < 0) ? tr('optionbuilder.unreadable') : ''} />
        <div className="ob-field">
          <span className="ob-k">{tr('optionbuilder.priceMode')}</span>
          <div className="ob-seg" role="group" aria-label={tr('optionbuilder.priceMode')}>
            {(['mid', 'natural'] as const).map(m => <button key={m} type="button" aria-pressed={mode === m} className={mode === m ? 'is-on' : ''} onClick={() => setMode(m)} title={tr(`optionbuilder.mode_${m}_help`)}>{tr(`optionbuilder.mode_${m}`)}</button>)}
          </div>
        </div>
        <div className="ob-field" data-vol-model>
          <span className="ob-k">{tr('optionbuilder.volModel')}</span>
          <div className="ob-seg" role="group" aria-label={tr('optionbuilder.volModel')}>
            {(['forward', 'constant'] as const).map(m => <button key={m} type="button" aria-pressed={volModel === m} className={volModel === m ? 'is-on' : ''} onClick={() => setVolModel(m)} title={tr(`optionbuilder.vol_${m}_help`)}>{tr(`optionbuilder.vol_${m}`)}</button>)}
          </div>
        </div>
      </div>
      <div className="ob-expiries">
        <span className="ob-k">{tr('optionbuilder.expiry')}</span>
        <div className="ob-chips">
          {future.slice(0, 14).map(e => {
            const isDl = downloaded.includes(e);
            return <button key={e} type="button" className={'ob-chip' + (e === selected ? ' is-on' : '') + (isDl ? ' is-dl' : '')} aria-pressed={e === selected}
              onClick={() => { setSelected(e); if (!isDl) requestExpiries([...usedExpiries, e]); }}
              title={isDl ? tr('optionbuilder.downloaded') : tr('optionbuilder.toDownload')}>
              <b>{f.date(e)}</b><small>{tr('optionbuilder.dte', { d: f.num(daysToClose(e, now), daysToClose(e, now) < 2 ? 2 : 0) })}</small>{!isDl && <Download size={12} aria-hidden />}
            </button>;
          })}
          {future.length > 14 && <select className="ob-select" aria-label={tr('optionbuilder.moreExpiries')} value={future.slice(0, 14).includes(selected) ? '' : selected}
            onChange={e => { const v = e.target.value; if (!v) return; setSelected(v); if (!downloaded.includes(v)) requestExpiries([...usedExpiries, v]); }}>
            <option value="">{tr('optionbuilder.moreExpiries')}</option>
            {future.slice(14).map(e => <option key={e} value={e}>{f.date(e)}{downloaded.includes(e) ? '' : ' · ' + tr('optionbuilder.toDownload')}</option>)}
          </select>}
          {!catalog.length && <span className="ob-muted">{tr('optionbuilder.noCatalog')}</span>}
        </div>
        <div className="ob-status" role="status">
          {downloadBusy && <span className="ob-badge is-muted"><RefreshCw size={12} className="ob-spin" aria-hidden />{tr('optionbuilder.downloading')}</span>}
          {loadingSelected && !downloadBusy && downloaded.includes(selected) && <span className="ob-badge is-muted">{tr('optionbuilder.loadingChain', { e: selected })}</span>}
          {latest && <span className="ob-badge">{tr('optionbuilder.quotesAt', { src: 'Polygon', time: f.time(latest._timestamp) })}</span>}
          {Object.entries(chainErrors).map(([e, err]) => <span key={e} className="ob-badge is-bad">{tr('optionbuilder.chainError', { e, err })}</span>)}
          <button type="button" className="ob-btn is-ghost" onClick={refresh} disabled={downloadBusy || (!usedExpiries.length && !selected)} title={tr('optionbuilder.refreshNote')}><RefreshCw size={14} aria-hidden />{tr('optionbuilder.refreshQuotes')}</button>
        </div>
      </div>
    </header>

    {/* ── strategy library ── */}
    <nav className="ob-card ob-library" aria-label={tr('optionbuilder.library')}>
      {PRESET_GROUPS.map(g => <div key={g.id} className="ob-group"><span className="ob-k">{tr(`optionbuilder.group_${g.id}`)}</span>
        <div className="ob-chips">{g.presets.map(id => <button key={id} type="button" className={'ob-preset' + (pending === id ? ' is-pending' : '')} data-preset={id}
          onClick={() => applyPreset(id)} title={tr('optionbuilder.presetReplace')}><PresetGlyph id={id} />{tr(`optionbuilder.preset_${id}`)}</button>)}</div></div>)}
      {(notice || pending) && <p className="ob-notice" role="status">{notice || tr('optionbuilder.presetPending', { name: tr(`optionbuilder.preset_${pending!}`) })}</p>}
    </nav>

    {/* ── chart + summary ── */}
    <div className="ob-main">
      <section className="ob-card ob-chart-card" aria-labelledby="ob-chart-title">
        <div className="ob-card-head"><h3 id="ob-chart-title">{tr('optionbuilder.chart')}</h3>
          {shown && <span className="ob-badge">{shown.expiry_basis === 'model_first_expiry' ? tr('optionbuilder.basisModel') : tr('optionbuilder.basisExact')}</span>}
          {shown && !!shown.forward_vols?.length && <span className="ob-badge is-manual" data-vol-badge={shown.vol_model} title={shown.vol_model_basis}>{tr(`optionbuilder.volBadge_${shown.vol_model === 'constant' ? 'constant' : 'forward'}`)}</span>}
          <span className="ob-grow" />
          {busy && <span className="ob-badge is-muted"><RefreshCw size={12} className="ob-spin" aria-hidden />{tr('optionbuilder.computing')}</span>}
          {!busy && shown && !current && <span className="ob-badge is-warn">{tr('optionbuilder.staleResult')}</span>}
        </div>
        {shown ? <PayoffChart result={shown} strikes={legs.flatMap(l => l.strike == null ? [] : [l.strike])} spot={spot as number} scenarioSpot={(spot as number) * (1 + movePct / 100)} elapsed={elapsed} ivPts={ivPts} />
          : <ChartPlaceholder legs={legs.length} nd={nd.length} />}
        <Scenario movePct={movePct} setMovePct={setMovePct} ivPts={ivPts} setIvPts={setIvPts} ivMin={ivMin}
          days={daysPassed} setDays={setDaysPassed} horizon={horizon} spot={spot} />
      </section>
      <Summary result={shown} prem={prem} mode={mode} quotes={quotes} legs={legs} nd={nd} inputErrors={inputErrors} engineError={engineError}
        movePct={movePct} elapsed={elapsed} ivPts={ivPts} rateLoading={rateLoading} />
    </div>

    {/* ── legs ── */}
    <section className="ob-card ob-legs" aria-labelledby="ob-legs-title">
      <div className="ob-card-head"><h3 id="ob-legs-title">{tr('optionbuilder.legsTitle')} <span className="ob-count">{legs.length}/{MAX_LEGS}</span></h3><span className="ob-grow" />
        <button type="button" className="ob-btn is-ghost" disabled={legs.length >= MAX_LEGS} onClick={() => addLeg('call')}><Plus size={14} aria-hidden />{tr('optionbuilder.addCall')}</button>
        <button type="button" className="ob-btn is-ghost" disabled={legs.length >= MAX_LEGS} onClick={() => addLeg('put')}><Plus size={14} aria-hidden />{tr('optionbuilder.addPut')}</button>
        <button type="button" className="ob-btn is-ghost" disabled={legs.length >= MAX_LEGS} onClick={() => addLeg('stock')}><Plus size={14} aria-hidden />{tr('optionbuilder.addShares')}</button>
        <button type="button" className="ob-btn is-ghost" disabled={!legs.length} onClick={() => setLegs([])}><Trash2 size={14} aria-hidden />{tr('optionbuilder.clear')}</button>
      </div>
      {legs.length ? <LegsTable legs={legs} quotes={quotes} contracts={contracts} future={future} downloaded={downloaded} now={now}
        result={shown && current ? shown : null} update={update} remove={id => setLegs(prev => prev.filter(l => l.id !== id))}
        invert={id => setLegs(prev => prev.map(l => l.id === id ? invertLeg(l) : l))} requestExpiries={e => requestExpiries([...usedExpiries, e])} />
        : <p className="ob-empty-text">{tr('optionbuilder.emptyLegs')}</p>}
    </section>

    {shown && <Heatmap result={shown} />}
    <details className="ob-card ob-method"><summary>{tr('optionbuilder.method')}</summary>
      <ul>{[tr('optionbuilder.engineNote'), tr('optionbuilder.daysNote'), tr('optionbuilder.priceNote'), ...(shown?.limits || []),
        ...(shown?.forward_vols?.length && shown.vol_model_basis ? [shown.vol_model_basis] : []),
        ...(shown?.probability_of_profit ? [shown.probability_of_profit.basis] : [])].map(t => <li key={t}>{t}</li>)}</ul></details>
  </section>;
}

function Field({ label, value, onChange, unit, placeholder, error, tag }: { label: string; value: string; onChange: (v: string) => void; unit?: string;
  placeholder?: string; error?: string; tag?: { text: string; tone: 'ok' | 'warn' | 'bad' | 'manual' | 'muted' } }) {
  return <label className={'ob-field' + (error ? ' has-error' : '')}>
    <span className="ob-k">{label}</span>
    <span className="ob-input"><input type="text" inputMode="decimal" autoComplete="off" spellCheck={false} value={value} placeholder={placeholder}
      onChange={e => onChange(e.target.value)} aria-invalid={!!error || undefined} />{unit && <i>{unit}</i>}</span>
    {error ? <small className="ob-bad-t">{error}</small> : tag ? <small className={'ob-tag is-' + tag.tone} title={tag.text}>{tag.text}</small> : null}
  </label>;
}

function ChartPlaceholder({ legs, nd }: { legs: number; nd: number }) {
  return <div className="ob-chart-empty">
    <svg viewBox="0 0 320 120" aria-hidden><path d="M10 95H310" className="ob-ph-axis" /><path d="M20 95L120 95L200 30L300 30" className="ob-ph-line" /></svg>
    <p>{!legs ? tr('optionbuilder.emptyChart') : nd ? tr('optionbuilder.ndBlock', { n: nd }) : tr('optionbuilder.waitingInputs')}</p>
  </div>;
}

function Scenario({ movePct, setMovePct, ivPts, setIvPts, ivMin, days, setDays, horizon, spot }: { movePct: number; setMovePct: (v: number) => void;
  ivPts: number; setIvPts: (v: number) => void; ivMin: number; days: number; setDays: (v: number) => void; horizon: number | null; spot: number | null }) {
  const f = useFormat();
  const maxDays = horizon == null ? 0 : Math.ceil(horizon);
  const shownDays = horizon == null ? 0 : Math.min(days, horizon);
  return <div className="ob-scenario" aria-label={tr('optionbuilder.scenarioTitle')} role="group">
    <Slider label={tr('optionbuilder.sliderSpot')} value={movePct} min={-30} max={30} step={.5} onChange={setMovePct}
      text={`${f.signed(movePct, 1)}% · ${f.num(spot == null ? null : spot * (1 + movePct / 100), 2)}`} />
    <Slider label={tr('optionbuilder.sliderIv')} value={ivPts} min={ivMin} max={50} step={.5} onChange={setIvPts}
      text={tr('optionbuilder.ptsValue', { v: f.signed(ivPts, 1) })} />
    <Slider label={tr('optionbuilder.sliderDays')} value={Math.min(days, maxDays)} min={0} max={Math.max(maxDays, 1)} step={1} onChange={setDays} disabled={horizon == null}
      text={horizon == null ? f.na : shownDays >= horizon ? tr('optionbuilder.atFirstExpiry') : tr('optionbuilder.tPlus', { d: f.num(shownDays, 0) })} />
    <button type="button" className="ob-btn is-ghost" onClick={() => { setMovePct(0); setIvPts(0); setDays(0); }} disabled={!movePct && !ivPts && !days}>
      <RotateCcw size={14} aria-hidden />{tr('optionbuilder.reset')}</button>
  </div>;
}

function Slider({ label, value, min, max, step, onChange, text, disabled }: { label: string; value: number; min: number; max: number; step: number;
  onChange: (v: number) => void; text: string; disabled?: boolean }) {
  const pct = max > min ? (value - min) / (max - min) * 100 : 0;
  const zero = max > min ? (Math.min(Math.max(0, min), max) - min) / (max - min) * 100 : 0;
  return <label className="ob-slider">
    <span className="ob-k">{label}<b className="ob-num">{text}</b></span>
    <input type="range" min={min} max={max} step={step} value={value} disabled={disabled} onChange={e => onChange(Number(e.target.value))}
      style={{ '--ob-fill-a': Math.min(pct, zero) + '%', '--ob-fill-b': Math.max(pct, zero) + '%' } as React.CSSProperties} aria-valuetext={text} />
  </label>;
}

function Summary({ result, prem, mode, quotes, legs, nd, inputErrors, engineError, movePct, elapsed, ivPts, rateLoading }: {
  result: EngineResult | null; prem: ReturnType<typeof premiums>; mode: PriceMode; quotes: LegQuote[]; legs: BuilderLeg[];
  nd: { index: number; reasons: NdReason[] }[]; inputErrors: string[]; engineError: string; movePct: number; elapsed: number; ivPts: number; rateLoading: boolean }) {
  const f = useFormat();
  const manual = quotes.filter(q => q.priceSource === 'manual').length;
  const ror = result ? returnOnRisk(result) : null;
  const be = result ? [...result.breakevens.map(v => f.num(v, 2)), ...(result.breakeven_intervals || []).map(i => `${f.num(i.from, 2)}–${i.to == null ? '∞' : f.num(i.to, 2)}`)] : [];
  const kind = result?.entry_kind;
  const profitLabel = result && !result.unlimited_profit && result.max_profit != null && result.max_profit < 0 ? tr('optionbuilder.kpiMinLoss') : tr('optionbuilder.kpiMaxProfit');
  const pop = result?.probability_of_profit;
  // Review M1: unbounded only because a European live call with q > 0 lags the short one.
  const theoreticalLoss = !!result?.unlimited_loss && result.unlimited_loss_reason === 'european_dividend';
  return <aside className="ob-card ob-summary" aria-label={tr('optionbuilder.summary')}>
    {(inputErrors.length > 0 || nd.length > 0 || engineError) && <div className="ob-alert" role="alert">
      {inputErrors.map(e => <p key={e}>{e}</p>)}
      {nd.map(m => <p key={m.index}>{tr('optionbuilder.ndLeg', { n: m.index + 1, reasons: m.reasons.map(r => tr(`optionbuilder.nd_${r}`)).join(' · ') })}</p>)}
      {engineError && <p>{tr('optionbuilder.engineError', { err: engineError })}</p>}
    </div>}
    {rateLoading && <p className="ob-note is-muted" role="status" data-rate-loading>{tr('optionbuilder.rateLoading')}</p>}
    <div className="ob-hero">
      <span className="ob-k">{kind === 'credit' ? tr('optionbuilder.kpiCredit') : kind === 'even' ? tr('optionbuilder.kpiEven') : tr('optionbuilder.kpiDebit')}</span>
      <strong className={'ob-num ob-hero-v' + (kind === 'credit' ? ' is-up' : '')}>{result ? f.num(Math.abs(result.entry_cost), 2) : f.na}<small>USD</small></strong>
      <span className="ob-sub">{tr(mode === 'mid' ? 'optionbuilder.pricedMid' : 'optionbuilder.pricedNatural')}{manual ? ' · ' + tr('optionbuilder.manualPrices', { n: manual }) : ''}{result?.fees ? ' · ' + tr('optionbuilder.feesIncl', { v: f.num(result.fees, 2) }) : ''}</span>
    </div>
    <dl className="ob-kpis">
      <Kpi label={tr('optionbuilder.kpiSpread')} value={f.num(prem.spread, 2)} sub={tr('optionbuilder.kpiSpreadSub', { a: f.num(prem.natural, 2), b: f.num(prem.mid, 2) })} />
      <Kpi label={profitLabel} tone={result?.unlimited_profit || (result?.max_profit ?? 0) > 0 ? 'up' : 'down'}
        value={!result ? f.na : result.unlimited_profit ? tr('optionbuilder.unlimited') : f.num(result.max_profit, 2)} />
      <Kpi label={tr('optionbuilder.kpiMaxLoss')} tone="down"
        value={!result ? f.na : result.unlimited_loss ? tr('optionbuilder.unlimitedLoss') + (theoreticalLoss ? ' · ' + tr('optionbuilder.lossTheoretical') : '') : f.num(result.max_loss == null ? null : -result.max_loss, 2)}
        sub={theoreticalLoss && result?.tail_reference ? tr('optionbuilder.lossTheoreticalNote', { v: f.signed(result.tail_reference.pnl, 2), p: f.num(result.tail_reference.price, 2) }) : undefined} />
      <Kpi label={tr('optionbuilder.kpiBe')} value={!result ? f.na : be.length ? be.join(' · ') : tr('optionbuilder.kpiBeNone')} />
      <Kpi label={tr('optionbuilder.kpiPop')} value={pop ? f.pct(pop.value, 1) : f.na}
        sub={pop ? tr('optionbuilder.kpiPopSub', { s: f.pct(pop.sigma, 1), d: f.num(pop.horizon_days, 1) }) : undefined} />
      <Kpi label={tr('optionbuilder.kpiRor')} value={ror == null ? (result && (result.unlimited_profit || result.unlimited_loss) ? tr('optionbuilder.rorUndefined') : f.na) : f.pct(ror, 0)} />
      <Kpi label={tr('optionbuilder.kpiScenario')} tone={!result ? undefined : result.scenario.pnl >= 0 ? 'up' : 'down'} wide
        value={result ? f.signed(result.scenario.pnl, 2) : f.na}
        sub={tr('optionbuilder.kpiScenarioSub', { p: f.num(result?.scenario.price, 2), m: f.signed(movePct, 1), d: f.num(elapsed, elapsed % 1 ? 2 : 0), v: f.signed(ivPts, 1) })} />
    </dl>
    {result?.tail_limit != null && <p className="ob-note">{tr('optionbuilder.tailLimit', { v: f.signed(result.tail_limit, 2) })}</p>}
    {!!result?.forward_vols?.length && <ul className="ob-note ob-fwd" data-forward-vols aria-label={tr('optionbuilder.volModel')}>
      {result.forward_vols.map(v => <li key={v.index}>{v.forward_iv == null
        ? tr('optionbuilder.forwardVolNd', { n: v.index + 1, l: f.pct(v.leg_iv, 1), s: f.pct(v.near_iv, 1) })
        : tr('optionbuilder.forwardVolRow', { n: v.index + 1, f: f.pct(v.forward_iv, 1), l: f.pct(v.leg_iv, 1), s: f.pct(v.near_iv, 1) })}
        {result.vol_model === 'constant' ? ' · ' + tr('optionbuilder.volBadge_constant') : ''}</li>)}</ul>}
    <table className="ob-greeks">
      <caption>{tr('optionbuilder.greeks')}<small>{tr('optionbuilder.greeksNote')}</small></caption>
      <thead><tr><th scope="col" /><th scope="col">{tr('optionbuilder.today')}</th><th scope="col">{tr('optionbuilder.scenario')}</th><th scope="col">{tr('optionbuilder.unit')}</th></tr></thead>
      <tbody>{(['delta', 'gamma', 'vega', 'theta', 'rho'] as const).map(g => <tr key={g}>
        <th scope="row">{tr(`optionbuilder.g_${g}`)}</th>
        <td className="ob-num">{result ? f.num(result.today[g], g === 'gamma' ? 3 : 2) : f.na}</td>
        <td className="ob-num">{result ? f.num(result.scenario[g], g === 'gamma' ? 3 : 2) : f.na}</td>
        <td>{tr(`optionbuilder.u_${g}`)}</td></tr>)}</tbody>
    </table>
    {legs.some(l => l.kind === 'stock') && <p className="ob-note">{tr('optionbuilder.sharesNote')}</p>}
  </aside>;
}

function Kpi({ label, value, sub, tone, wide }: { label: string; value: string; sub?: string; tone?: 'up' | 'down'; wide?: boolean }) {
  return <div className={'ob-kpi' + (wide ? ' is-wide' : '')}><dt>{label}</dt><dd className={'ob-num' + (tone ? ' is-' + tone : '')}>{value}</dd>{sub && <dd className="ob-sub">{sub}</dd>}</div>;
}

function LegsTable({ legs, quotes, contracts, future, downloaded, now, result, update, remove, invert, requestExpiries }: {
  legs: BuilderLeg[]; quotes: LegQuote[]; contracts: ReadonlyMap<string, readonly ChainContract[]>; future: string[]; downloaded: string[]; now: number;
  result: EngineResult | null; update: (id: string, change: Parameters<typeof editLeg>[1]) => void; remove: (id: string) => void;
  invert: (id: string) => void; requestExpiries: (e: string) => void }) {
  const f = useFormat();
  const comma = f.language === 'it';
  return <div className="ob-table-wrap"><table className="ob-table">
    <thead><tr>
      <th scope="col">{tr('optionbuilder.col_side')}</th><th scope="col">{tr('optionbuilder.col_qty')}</th><th scope="col">{tr('optionbuilder.col_type')}</th>
      <th scope="col">{tr('optionbuilder.col_expiry')}</th><th scope="col">{tr('optionbuilder.col_strike')}</th>
      <th scope="col" className="r">{tr('optionbuilder.col_bid')}</th><th scope="col" className="r">{tr('optionbuilder.col_ask')}</th><th scope="col" className="r">{tr('optionbuilder.col_mid')}</th>
      <th scope="col" className="r">{tr('optionbuilder.col_price')}</th><th scope="col" className="r">{tr('optionbuilder.col_iv')}</th>
      <th scope="col" className="r">{tr('optionbuilder.col_delta')}</th><th scope="col" className="r">{tr('optionbuilder.col_theta')}</th><th scope="col" className="r">{tr('optionbuilder.col_vega')}</th>
      <th scope="col" className="r">{tr('optionbuilder.col_pnl')}</th><th scope="col"><span className="ob-sr">{tr('optionbuilder.col_actions')}</span></th>
    </tr></thead>
    <tbody>{legs.map((leg, i) => {
      const q = quotes[i];
      const detail = result?.legs?.[i];
      const strikes = leg.kind === 'option' ? strikesOf(contracts.get(leg.expiry || ''), leg.type) : [];
      const bad = q.reasons.length > 0;
      const c = q.contract;
      return <Fragment key={leg.id}><tr className={(leg.side === 'buy' ? 'is-buy' : 'is-sell') + (bad ? ' is-nd' : '')} data-leg={i + 1}>
        <td><button type="button" className={'ob-side is-' + leg.side} onClick={() => invert(leg.id)} aria-label={tr('optionbuilder.invert', { n: i + 1 })} title={tr('optionbuilder.invert', { n: i + 1 })}>
          {leg.side === 'buy' ? tr('optionbuilder.buy') : tr('optionbuilder.sell')}</button></td>
        <td><input className="ob-cell-input is-qty" type="text" inputMode="decimal" value={leg.qty} aria-label={tr('optionbuilder.qtyOf', { n: i + 1 })}
          onChange={e => update(leg.id, { qty: e.target.value })} aria-invalid={q.reasons.includes('bad_qty') || undefined} />
          {leg.kind === 'stock' && <small className="ob-muted">{tr('optionbuilder.shares')}</small>}</td>
        <td>{leg.kind === 'stock' ? <span className="ob-type is-stock">{tr('optionbuilder.shares')}</span>
          : <select className="ob-cell-select" value={leg.type} aria-label={tr('optionbuilder.typeOf', { n: i + 1 })} onChange={e => update(leg.id, { type: e.target.value as 'call' | 'put' })}>
            <option value="call">{tr('optionbuilder.call')}</option><option value="put">{tr('optionbuilder.put')}</option></select>}</td>
        <td>{leg.kind === 'stock' ? <span className="ob-muted">—</span>
          : <select className="ob-cell-select is-expiry" value={leg.expiry} aria-label={tr('optionbuilder.expiryOf', { n: i + 1 })} onChange={e => {
            const v = e.target.value; update(leg.id, { expiry: v });
            if (!downloaded.includes(v)) requestExpiries(v);
          }}>{[...new Set([...(leg.expiry ? [leg.expiry] : []), ...future])].sort().map(e => <option key={e} value={e}>
            {e.slice(0, 4) === new Date(now).toISOString().slice(0, 4) ? f.shortDate(e) : f.date(e)} · {tr('optionbuilder.dte', { d: f.num(daysToClose(e, now), daysToClose(e, now) < 2 ? 2 : 0) })}{downloaded.includes(e) ? '' : ' · ' + tr('optionbuilder.toDownload')}</option>)}</select>}</td>
        <td>{leg.kind === 'stock' ? <span className="ob-muted">—</span>
          : <select className="ob-cell-select is-strike" value={leg.strike ?? ''} aria-label={tr('optionbuilder.strikeOf', { n: i + 1 })} onChange={e => update(leg.id, { strike: Number(e.target.value) })}>
            {leg.strike != null && !strikes.includes(leg.strike) && <option value={leg.strike}>{f.strike(leg.strike)} · {tr('optionbuilder.notInChain')}</option>}
            {strikes.map(k => <option key={k} value={k}>{f.strike(k)}</option>)}</select>}</td>
        <td className="r ob-num">{leg.kind === 'stock' ? '—' : f.num(q.bid, 2)}</td>
        <td className="r ob-num">{leg.kind === 'stock' ? '—' : f.num(q.ask, 2)}</td>
        <td className="r ob-num">{leg.kind === 'stock' ? '—' : f.num(q.mid, 2)}
          {q.bid != null && q.ask != null && q.mid != null && q.mid > 0 && <small className="ob-muted">{tr('optionbuilder.spreadPct', { v: f.num((q.ask - q.bid) / q.mid * 100, 0) })}</small>}</td>
        <td className="r"><input className="ob-cell-input is-price" type="text" inputMode="decimal" value={leg.price || ''} placeholder={q.priceSource && q.priceSource !== 'manual' ? fieldNumber(q.price, comma, 2) : f.na}
          aria-label={tr('optionbuilder.priceOf', { n: i + 1 })} onChange={e => update(leg.id, { price: e.target.value })} aria-invalid={q.reasons.includes('bad_price') || undefined} />
          <small className={'ob-src' + (q.priceSource === 'manual' ? ' is-manual' : '')}>{q.priceSource ? tr(`optionbuilder.src_${q.priceSource}`) : tr('optionbuilder.na')}</small></td>
        <td className="r">{leg.kind === 'stock' ? <span className="ob-muted">—</span> : <><input className="ob-cell-input is-iv" type="text" inputMode="decimal" value={leg.iv || ''}
          placeholder={c?.iv != null ? fieldNumber(c.iv * 100, comma, 1) : f.na} aria-label={tr('optionbuilder.ivOf', { n: i + 1 })} onChange={e => update(leg.id, { iv: e.target.value })}
          aria-invalid={q.reasons.includes('bad_iv') || undefined} />
          <small className={'ob-src' + (q.ivSource === 'manual' ? ' is-manual' : '')}>{q.ivSource === 'manual' ? tr('optionbuilder.src_manual') : q.ivSource ? tr('optionbuilder.src_chain') : tr('optionbuilder.na')}</small></>}</td>
        <td className="r ob-num">{detail ? f.num(detail.delta, 1) : f.na}</td>
        <td className="r ob-num">{detail ? f.num(detail.theta, 2) : f.na}</td>
        <td className="r ob-num">{detail ? f.num(detail.vega, 2) : f.na}</td>
        <td className={'r ob-num' + (detail ? detail.pnl_today >= 0 ? ' is-up' : ' is-down' : '')}>{detail ? f.signed(detail.pnl_today, 2) : f.na}</td>
        <td className="ob-actions"><button type="button" className="ob-icon" onClick={() => remove(leg.id)} aria-label={tr('optionbuilder.remove', { n: i + 1 })} title={tr('optionbuilder.remove', { n: i + 1 })}><Trash2 size={14} /></button></td>
      </tr>
      <tr className={'ob-sub-row' + (bad ? ' is-nd' : '')}>
        {bad && <td className="ob-nd-cell" colSpan={15}><span className="ob-badge is-bad">{tr('optionbuilder.na')}</span> {q.reasons.map(r => tr(`optionbuilder.nd_${r}`)).join(' · ')}</td>}
        {!bad && c && <td className="ob-meta-cell" colSpan={15}>{c.contract || ''} · {tr('optionbuilder.quoteMeta', { tf: c.quote_timeframe || tr('optionbuilder.na'), time: f.time(c.quote_timestamp) })} · ×{f.num(c.multiplier, 0)}{c.quality.length ? ' · ' + c.quality.join(' · ') : ''}</td>}
        {!bad && !c && <td className="ob-meta-cell" colSpan={15}>{tr('optionbuilder.sharesMeta')}</td>}
      </tr></Fragment>;
    })}</tbody>
  </table></div>;
}

function Heatmap({ result }: { result: EngineResult }) {
  const f = useFormat();
  const max = Math.max(1, ...result.heatmap.flatMap(r => r.cells.map(c => Math.abs(c.pnl))));
  return <section className="ob-card ob-heat" aria-labelledby="ob-heat-title">
    <div className="ob-card-head"><h3 id="ob-heat-title">{tr('optionbuilder.heatmap')}</h3><span className="ob-card-note">{tr('optionbuilder.heatmapNote')}</span></div>
    <div className="ob-table-wrap"><table className="ob-heat-table">
      <thead><tr><th scope="col">{tr('optionbuilder.heatDays')}</th>{result.heatmap[0]?.cells.map(c => <th key={c.price} scope="col" className="ob-num">{f.num(c.price, 2)}</th>)}</tr></thead>
      <tbody>{result.heatmap.map((row, i) => <tr key={i}><th scope="row" className="ob-num">{f.num(row.elapsed_days, row.elapsed_days % 1 ? 1 : 0)}</th>
        {row.cells.map(c => <td key={c.price} className="ob-num" style={{ '--ob-heat': (Math.abs(c.pnl) / max).toFixed(3) } as React.CSSProperties}
          data-tone={c.pnl >= 0 ? 'up' : 'down'}>{f.signed(c.pnl, 0)}</td>)}</tr>)}</tbody>
    </table></div>
  </section>;
}

/** Tiny payoff silhouette for the strategy chips (decorative). */
function PresetGlyph({ id }: { id: PresetId }) {
  const paths: Record<PresetId, string> = {
    long_call: 'M2 12H9L16 4', long_put: 'M2 4L9 12H16', covered_call: 'M2 14L9 6H16', cash_secured_put: 'M2 14L9 6H16',
    bull_call: 'M2 12H6L11 5H16', bear_call: 'M2 5H6L11 12H16', bull_put: 'M2 12H6L11 5H16', bear_put: 'M2 5H6L11 12H16',
    straddle: 'M2 4L9 12L16 4', strangle: 'M2 4L6 11H11L16 4', iron_condor: 'M2 12L5 6H12L15 12', iron_butterfly: 'M2 12L9 5L16 12', calendar: 'M2 12Q9 1 16 12',
  };
  return <svg className="ob-glyph" viewBox="0 0 18 16" aria-hidden><path d="M1 9H17" className="ob-glyph-zero" /><path d={paths[id]} /></svg>;
}
