// Option builder (09/10/2026, Opus 5.5). Legs on the Polygon chain served by the backend; every
// figure (payoff, P/L, greeks, breakevens, extremes, probability) comes from the backend engine
// POST /options/strategy/simulate. A missing quote/IV/spot/rate is declared n.d.: the engine is
// not called and no figure is shown in its place.
//
// 10/10/2026 (Opus 5.5) — impianto «Ticket di trading» scelto dal PM: a sinistra il ticket
// (strategia, scadenza, gambe, netto, ipotesi di mercato), a destra il profilo P/L (4 numeri,
// payoff, cursori di scenario), sotto un cassetto «Dettagli» chiuso. Stato, richieste e motore
// sono quelli di prima: cambia solo la disposizione. Le dichiarazioni che cambiano la decisione
// (gamba n.d., quote DELAYED, perdita illimitata, input mancanti) restano fuori dal cassetto.
import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import { ChevronDown, Plus, RefreshCw, RotateCcw, Trash2, X } from 'lucide-react';
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
  const [pending, setPendingRaw] = useState<PresetId | null>(null);
  // v2 review 10/10 (point 5): a pending preset belongs to the legs it was asked on; if the user edits
  // the legs while the chain is still loading, the request is dropped instead of overwriting the edit.
  const pendingFrom = useRef<BuilderLeg[] | null>(null);
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
  const [dragSpot, setDragSpot] = useState(false);
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
  // The strategy select shows the preset only while the legs are exactly the ones it built.
  const setPending = (id: PresetId | null) => { pendingFrom.current = legs; setPendingRaw(id); };
  useEffect(() => {
    if (pending && pendingFrom.current !== legs) setPendingRaw(null);
    // v3 review 10/10: the cancellation is said, not silent
    if (pending && pendingFrom.current !== legs) setNotice(tr('optionbuilder.pendingCancelled', { e: selected ? f.date(selected) : tr('optionbuilder.na') }));
  }, [legs]);
  // Identity, not shape: any edit (even one undone by hand) makes the ticket «Custom» — the strict reading.
  const [applied, setApplied] = useState<{ id: PresetId; legs: BuilderLeg[] } | null>(null);
  const strategy = applied && applied.legs === legs ? applied.id : '';
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
    if (out.ok) { setLegs(out.legs); setApplied({ id, legs: out.legs }); setPending(null); setMovePct(0); setDaysPassed(0); return; }
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

  const usedExpiries = [...new Set(legs.map(l => l.expiry).filter((e): e is string => !!e))];
  const refresh = () => requestExpiries(usedExpiries.length ? usedExpiries : selected ? [selected] : []);
  /** Ticket expiry: new legs and presets use it; a preset on the ticket is rebuilt on it. */
  const chooseExpiry = (e: string) => {
    if (!e) return;
    setSelected(e);
    if (!downloaded.includes(e)) requestExpiries([...usedExpiries, e]);
    if (strategy) { setNotice(''); setPending(strategy); }
  };

  const [addOpen, setAddOpen] = useState(false);
  const addLeg = (kind: 'call' | 'put' | 'stock') => {
    setAddOpen(false);
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

  // Market assumptions: compact by default, the editor opens on «Modifica» or on an unreadable field.
  const fieldError = (!!spotText.trim() && manualSpot == null) || (!!rateText.trim() && rateValue == null) || (!!divText.trim() && divValue == null)
    || (!!feeText.trim() && (feeValue == null || feeValue < 0)) || (!rateLoading && rateValue == null);
  const [editMarket, setEditMarket] = useState(false);
  const marketOpen = editMarket || fieldError;

  if (!ticker) return <section className="ob" aria-labelledby="vd-lab-title"><div className="ob-card ob-empty"><h2 id="vd-lab-title">{tr('optionbuilder.title')}</h2><p>{tr('optionbuilder.noTicker')}</p></div></section>;

  const loadingSelected = !!selected && !contracts.get(selected)?.length;
  const spotWarn = spotSource != null && !/polygon/i.test(spotSource);
  // quote delay of the contracts in use (DELAYED, REAL-TIME…), declared next to the spot
  const timeframes = [...new Set(quotes.filter(q => q.contract).map(q => q.contract!.quote_timeframe || tr('optionbuilder.na')))];
  const delayed = timeframes.some(tf => !/real/i.test(tf));
  const rateTag = rateManual ? { text: tr('optionbuilder.manual'), tone: 'manual' as const } : rate?.value != null
    ? { text: `${rate.source} · ${rate.date}${rate.status === 'stale' ? ' · ' + tr('optionbuilder.stale') : ''}`, tone: rate.status === 'stale' ? 'warn' as const : 'ok' as const }
    : rate ? { text: tr('optionbuilder.rateMissing', { err: rate.error || tr('optionbuilder.na') }), tone: 'bad' as const } : { text: tr('optionbuilder.loading'), tone: 'muted' as const };
  const legExpiries = [...new Set(legs.filter(l => l.kind === 'option' && l.expiry).map(l => l.expiry as string))].sort();
  const hasSecondExpiry = legs.some(l => l.kind === 'option' && l.expiry && l.expiry !== selected);
  const expiryList = [...new Set([...(selected ? [selected] : []), ...future])].sort();

  return <section className="ob" aria-labelledby="vd-lab-title" data-option-builder>
    {/* ── strip: underlying, spot and the state of the data ── */}
    <header className="ob-strip">
      <h2 id="vd-lab-title"><span className="ob-strip-ticker">{ticker}</span><span className="ob-strip-title">{tr('optionbuilder.title')}</span></h2>
      <strong className="ob-num ob-strip-spot" data-spot>{f.num(spot, 2)}</strong>
      <span className={'ob-badge' + (spotText.trim() ? ' is-manual' : spot == null ? ' is-bad' : spotWarn ? ' is-warn' : '')}>
        {spotText.trim() ? tr('optionbuilder.manual') : spot == null ? tr('optionbuilder.na') : `${spotSource || tr('optionbuilder.sourceUnknown')} · ${f.time(spotStamp || download?.snapshot_at)}`}
      </span>
      {latest && <span className={'ob-badge' + (delayed ? ' is-warn' : '')} data-quote-delay={timeframes.join(',') || undefined}>
        {tr('optionbuilder.quotesAt', { src: 'Polygon', time: f.time(latest._timestamp) })}{timeframes.length ? ' · ' + timeframes.join(' · ') : ''}</span>}
      {spotWarn && !spotText.trim() && <span className="ob-badge is-warn">{tr('optionbuilder.spotNotPolygon')}</span>}
      {downloadBusy && <span className="ob-badge is-muted"><RefreshCw size={12} className="ob-spin" aria-hidden />{tr('optionbuilder.downloading')}</span>}
      {loadingSelected && !downloadBusy && downloaded.includes(selected) && <span className="ob-badge is-muted">{tr('optionbuilder.loadingChain', { e: selected })}</span>}
      {Object.entries(chainErrors).map(([e, err]) => <span key={e} className="ob-badge is-bad">{tr('optionbuilder.chainError', { e, err })}</span>)}
      <span className="ob-grow" />
      <button type="button" className="ob-btn is-ghost" onClick={refresh} disabled={downloadBusy || (!usedExpiries.length && !selected)} title={tr('optionbuilder.refreshNote')}><RefreshCw size={14} aria-hidden />{tr('optionbuilder.refreshQuotes')}</button>
    </header>

    <div className="ob-main">
      {/* ── ticket ── */}
      <section className="ob-card ob-ticket" aria-labelledby="ob-ticket-title">
        <div className="ob-card-head">
          <h3 id="ob-ticket-title">{tr('optionbuilder.ticket')}</h3>
          <span className="ob-card-note">{legs.length === 1 ? tr('optionbuilder.legsCountOne', { t: ticker }) : tr('optionbuilder.legsCount', { t: ticker, n: legs.length })}</span>
          <span className="ob-grow" />
          {legs.length > 0 && <button type="button" className="ob-icon" onClick={() => setLegs([])} aria-label={tr('optionbuilder.clear')} title={tr('optionbuilder.clear')}><Trash2 size={14} /></button>}
          <div className="ob-add">
            <button type="button" className="ob-btn" aria-expanded={addOpen} aria-haspopup="menu" disabled={legs.length >= MAX_LEGS} onClick={() => setAddOpen(v => !v)} data-add-leg>
              <Plus size={14} aria-hidden />{tr('optionbuilder.addLeg')}</button>
            {addOpen && <div className="ob-menu" role="menu">
              <button type="button" role="menuitem" onClick={() => addLeg('call')}>{tr('optionbuilder.addCall')}</button>
              <button type="button" role="menuitem" onClick={() => addLeg('put')}>{tr('optionbuilder.addPut')}</button>
              <button type="button" role="menuitem" onClick={() => addLeg('stock')}>{tr('optionbuilder.addShares')}</button>
            </div>}
          </div>
        </div>
        <div className="ob-ticket-body">
          <div className="ob-pick">
            <label className="ob-field">
              <span className="ob-k">{tr('optionbuilder.strategy')}</span>
              <span className={'ob-select-box' + (pending ? ' is-pending' : '')}>
                {strategy ? <PresetGlyph id={strategy} /> : pending ? <PresetGlyph id={pending} /> : <span className="ob-glyph-empty" aria-hidden />}
                <select value={pending || strategy} aria-label={tr('optionbuilder.strategy')} title={tr('optionbuilder.presetReplace')} data-strategy
                  onChange={e => { const v = e.target.value as PresetId | ''; if (v && v !== strategy) applyPreset(v); }}>
                  <option value="">{legs.length ? tr('optionbuilder.strategyCustom') : tr('optionbuilder.strategyPick')}</option>
                  {PRESET_GROUPS.map(g => <optgroup key={g.id} label={tr(`optionbuilder.group_${g.id}`)}>
                    {g.presets.map(id => <option key={id} value={id}>{tr(`optionbuilder.preset_${id}`)}</option>)}</optgroup>)}
                </select>
                <ChevronDown size={14} aria-hidden className="ob-caret" />
              </span>
            </label>
            <label className="ob-field">
              <span className="ob-k">{tr('optionbuilder.expiry')}</span>
              <span className="ob-select-box">
                <select value={selected} aria-label={tr('optionbuilder.expiry')} onChange={e => chooseExpiry(e.target.value)} data-expiry>
                  {!selected && <option value="">{catalog.length ? tr('optionbuilder.pickExpiryShort') : tr('optionbuilder.noCatalog')}</option>}
                  {expiryList.map(e => <option key={e} value={e}>{f.date(e)} · {tr('optionbuilder.dte', { d: f.num(daysToClose(e, now), daysToClose(e, now) < 2 ? 2 : 0) })}{downloaded.includes(e) ? '' : ' · ' + tr('optionbuilder.toDownload')}</option>)}
                </select>
                <ChevronDown size={14} aria-hidden className="ob-caret" />
              </span>
            </label>
          </div>
          {(notice || pending) && <p className="ob-notice" role="status">{notice || tr('optionbuilder.presetPending', { name: tr(`optionbuilder.preset_${pending!}`) })}</p>}
          {legs.length ? <TicketLegs legs={legs} quotes={quotes} contracts={contracts} selected={selected} showExpiry={hasSecondExpiry} update={update}
            remove={id => setLegs(prev => prev.filter(l => l.id !== id))} invert={id => setLegs(prev => prev.map(l => l.id === id ? invertLeg(l) : l))} />
            : <p className="ob-empty-text">{tr('optionbuilder.emptyLegs')}</p>}
          {legs.length > 0 && <NetLine result={shown} mode={mode} quotes={quotes} />}
        </div>
        <div className={'ob-market' + (marketOpen ? ' is-open' : '')}>
          <div className="ob-market-head">
            <span>{tr('optionbuilder.market')}</span>
            <button type="button" className="ob-link" onClick={() => setEditMarket(v => !v)} aria-expanded={marketOpen} disabled={fieldError}>
              {marketOpen ? tr('optionbuilder.marketDone') : tr('optionbuilder.marketEdit')}</button>
          </div>
          {marketOpen ? <div className="ob-inputs">
            <Field label={tr('optionbuilder.spotOverride')} value={spotText} onChange={setSpotText} placeholder={fieldNumber(chainSpot, comma, 2)} unit="USD"
              error={spotText.trim() && manualSpot == null ? tr('optionbuilder.unreadable') : ''} />
            <Field label={tr('optionbuilder.rate')} value={rateText} onChange={v => { setRateText(v); setRateManual(true); }} unit="%"
              error={rateText.trim() && rateValue == null ? tr('optionbuilder.unreadable') : ''} tag={rateTag} />
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
          : <dl className="ob-market-row">
            <div title={spotText.trim() ? tr('optionbuilder.manual') : `${spotSource || tr('optionbuilder.sourceUnknown')}`}><dt>{tr('optionbuilder.spot')}</dt>
              <dd className={'ob-num' + (spotText.trim() ? ' is-manual' : '')}>{f.num(spot, 2)}</dd></div>
            <div title={rateTag.text}><dt>{tr('optionbuilder.rateShort')}</dt><dd className={'ob-num' + (rateTag.tone === 'warn' ? ' is-warn' : rateTag.tone === 'manual' ? ' is-manual' : '')}>
              {rateLoading ? '…' : rateValue == null ? tr('optionbuilder.na') : f.num(rateValue, 2) + '%'}</dd>
              {!rateManual && rate?.status === 'stale' && <dd className="ob-stale-t" data-rate-stale>{tr('optionbuilder.stale')}</dd>}</div>
            <div title={tr('optionbuilder.assumptionNoSource')}><dt>{tr('optionbuilder.dividendShort')}</dt><dd className="ob-num">{divValue == null ? tr('optionbuilder.na') : f.num(divValue, 2) + '%'}</dd></div>
            <div><dt>{tr('optionbuilder.commissionShort')}</dt><dd className="ob-num">{feeValue == null ? tr('optionbuilder.na') : f.num(feeValue, 2)}</dd></div>
            <div><dt>{tr('optionbuilder.priceShort')}</dt><dd>{tr(`optionbuilder.mode_${mode}`)}</dd></div>
          </dl>}
        </div>
      </section>

      {/* ── P/L profile ── */}
      <section className="ob-card ob-chart-card" aria-labelledby="ob-chart-title">
        <div className="ob-card-head">
          <h3 id="ob-chart-title">{tr('optionbuilder.plTitle')}</h3>
          <span className="ob-card-note" data-pl-sub>{legExpiries.length === 1 ? tr('optionbuilder.plSub', { e: f.date(legExpiries[0]) })
            : legExpiries.length > 1 ? tr('optionbuilder.plSubMixed') : tr('optionbuilder.plSubNone')}</span>
          {shown?.expiry_basis === 'model_first_expiry' && <span className="ob-badge is-manual">{tr('optionbuilder.basisModel')}</span>}
          {shown && !!shown.forward_vols?.length && <span className="ob-badge is-manual" data-vol-badge={shown.vol_model} title={shown.vol_model_basis}>{tr(`optionbuilder.volBadge_${shown.vol_model === 'constant' ? 'constant' : 'forward'}`)}</span>}
          <span className="ob-grow" />
          {busy && <span className="ob-badge is-muted"><RefreshCw size={12} className="ob-spin" aria-hidden />{tr('optionbuilder.computing')}</span>}
          {!busy && shown && !current && <span className="ob-badge is-warn">{tr('optionbuilder.staleResult')}</span>}
        </div>
        <Alerts nd={nd} inputErrors={inputErrors} engineError={engineError} rateLoading={rateLoading} />
        <KeyFigures result={shown} spot={spot} />
        {shown ? <PayoffChart result={shown} strikes={legs.flatMap(l => l.strike == null ? [] : [l.strike])} spot={spot as number} scenarioSpot={(spot as number) * (1 + movePct / 100)} elapsed={elapsed} ivPts={ivPts} freeze={dragSpot} />
          : <ChartPlaceholder legs={legs.length} nd={nd.length} />}
        <Scenario movePct={movePct} setMovePct={setMovePct} ivPts={ivPts} setIvPts={setIvPts} ivMin={ivMin}
          days={daysPassed} setDays={setDaysPassed} horizon={horizon} spot={spot} result={shown} elapsed={elapsed} onDrag={setDragSpot} />
      </section>
    </div>

    <Details result={shown} prem={prem} legs={legs} quotes={quotes} future={future} downloaded={downloaded} now={now} current={current}
      update={update} requestExpiries={e => requestExpiries([...usedExpiries, e])} />
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

function Alerts({ nd, inputErrors, engineError, rateLoading }: { nd: { index: number; reasons: NdReason[] }[]; inputErrors: string[]; engineError: string; rateLoading: boolean }) {
  return <>
    {(inputErrors.length > 0 || nd.length > 0 || engineError) && <div className="ob-alert" role="alert">
      {inputErrors.map(e => <p key={e}>{e}</p>)}
      {nd.map(m => <p key={m.index}>{tr('optionbuilder.ndLeg', { n: m.index + 1, reasons: m.reasons.map(r => tr(`optionbuilder.nd_${r}`)).join(' · ') })}</p>)}
      {engineError && <p>{tr('optionbuilder.engineError', { err: engineError })}</p>}
    </div>}
    {rateLoading && <p className="ob-note is-muted ob-pad" role="status" data-rate-loading>{tr('optionbuilder.rateLoading')}</p>}
  </>;
}

/** The four figures at the head of the P/L profile, all from the engine. */
function KeyFigures({ result, spot }: { result: EngineResult | null; spot: number | null }) {
  const f = useFormat();
  const kind = result?.entry_kind;
  const profitLabel = result && !result.unlimited_profit && result.max_profit != null && result.max_profit < 0 ? tr('optionbuilder.kpiMinLoss') : tr('optionbuilder.kpiMaxProfit');
  // Review M1: unbounded only because a European live call with q > 0 lags the short one.
  const theoreticalLoss = !!result?.unlimited_loss && result.unlimited_loss_reason === 'european_dividend';
  const bes = result ? result.breakevens : [];
  const intervals = result?.breakeven_intervals || [];
  const beText = !result ? f.na : [...bes.map(v => f.num(v, 2)), ...intervals.map(i => `${f.num(i.from, 2)}–${i.to == null ? '∞' : f.num(i.to, 2)}`)].join(' · ') || tr('optionbuilder.kpiBeNone');
  const beSub = result && spot && bes.length ? tr('optionbuilder.beFromSpot', { v: bes.map(b => f.signed((b / spot - 1) * 100, 1) + '%').join(' · ') }) : undefined;
  const usd = (v: number | null | undefined) => v == null ? f.na : f.num(v, 2);
  return <dl className="ob-figs" aria-label={tr('optionbuilder.summary')}>
    <Fig label={kind === 'credit' ? tr('optionbuilder.kpiCreditIn') : kind === 'even' ? tr('optionbuilder.kpiEven') : tr('optionbuilder.kpiDebitPaid')}
      value={result ? usd(Math.abs(result.entry_cost)) : f.na} unit={result ? 'USD' : undefined} data="entry" valueClass={'ob-hero-v' + (kind === 'credit' ? ' is-up' : '')}
      sub={result?.fees ? tr('optionbuilder.feesIncl', { v: f.num(result.fees, 2) }) : undefined} />
    <Fig label={profitLabel} tone={!result ? undefined : result.unlimited_profit || (result.max_profit ?? 0) > 0 ? 'up' : 'down'} data="max-profit"
      value={!result ? f.na : result.unlimited_profit ? tr('optionbuilder.unlimited') : f.signed(result.max_profit, 2)} unit={result && !result.unlimited_profit && result.max_profit != null ? 'USD' : undefined} />
    <Fig label={tr('optionbuilder.kpiMaxLoss')} tone={result ? 'down' : undefined} data="max-loss"
      value={!result ? f.na : result.unlimited_loss ? tr('optionbuilder.unlimitedLoss') + (theoreticalLoss ? ' · ' + tr('optionbuilder.lossTheoretical') : '') : f.signed(result.max_loss == null ? null : -result.max_loss, 2)}
      unit={result && !result.unlimited_loss && result.max_loss != null ? 'USD' : undefined}
      title={theoreticalLoss && result?.tail_reference ? tr('optionbuilder.lossTheoreticalNote', { v: f.signed(result.tail_reference.pnl, 2), p: f.num(result.tail_reference.price, 2) }) : undefined} />
    <Fig label={bes.length + intervals.length > 1 ? tr('optionbuilder.kpiBePlural') : tr('optionbuilder.kpiBe')} value={beText} sub={beSub} data="breakevens" />
  </dl>;
}

function Fig({ label, value, unit, sub, tone, valueClass, title, data }: { label: string; value: string; unit?: string; sub?: string; tone?: 'up' | 'down';
  valueClass?: string; title?: string; data?: string }) {
  return <div className="ob-fig" title={title} data-fig={data}><dt>{label}</dt>
    <dd className={'ob-num ob-fig-v' + (tone ? ' is-' + tone : '') + (valueClass ? ' ' + valueClass : '')}>{value}{unit && <small>{unit}</small>}</dd>
    {sub && <dd className="ob-sub">{sub}</dd>}</div>;
}

/** Net premium of the ticket: the engine's net_premium, on the same prices the engine used (chain or
 *  manual) — v2 review 10/10 point 1. The mid/natural spread stays in the drawer. */
function NetLine({ result, mode, quotes }: { result: EngineResult | null; mode: PriceMode; quotes: LegQuote[] }) {
  const f = useFormat();
  const v = result ? result.net_premium : null;
  const manual = quotes.filter(q => q.priceSource === 'manual').length;
  const label = v == null ? tr('optionbuilder.netNd') : v < 0 ? tr('optionbuilder.netCredit') : v > 0 ? tr('optionbuilder.netDebit') : tr('optionbuilder.kpiEven');
  return <div className="ob-net" data-net>
    <span className="ob-net-k">{label}</span>
    <b className={'ob-num' + (v != null && v < 0 ? ' is-up' : '')}>{v == null ? f.na : <>{f.num(Math.abs(v), 2)}<small>USD</small></>}</b>
    <small className="ob-net-sub">{tr(mode === 'mid' ? 'optionbuilder.pricedMid' : 'optionbuilder.pricedNatural')}
      {result?.fees ? ' · ' + tr('optionbuilder.netFeesExcluded') : ''}
      {manual ? ' · ' + tr('optionbuilder.manualPrices', { n: manual }) : ''}</small>
  </div>;
}

function TicketLegs({ legs, quotes, contracts, selected, showExpiry, update, remove, invert }: {
  legs: BuilderLeg[]; quotes: LegQuote[]; contracts: ReadonlyMap<string, readonly ChainContract[]>; selected: string; showExpiry: boolean;
  update: (id: string, change: Parameters<typeof editLeg>[1]) => void; remove: (id: string) => void; invert: (id: string) => void }) {
  const f = useFormat();
  const comma = f.language === 'it';
  return <div className="ob-table-wrap ob-ticket-wrap"><table className="ob-table ob-ticket-table">
    <thead><tr>
      <th scope="col">{tr('optionbuilder.col_side')}</th><th scope="col" className="r">{tr('optionbuilder.col_qty')}</th><th scope="col">{tr('optionbuilder.col_type')}</th>
      <th scope="col" className="r">{tr('optionbuilder.col_strike')}</th><th scope="col" className="r">{tr('optionbuilder.col_priceShort')}</th>
      <th scope="col" className="r">{tr('optionbuilder.col_iv')}</th><th scope="col"><span className="ob-sr">{tr('optionbuilder.col_actions')}</span></th>
    </tr></thead>
    <tbody>{legs.map((leg, i) => {
      const q = quotes[i];
      const strikes = leg.kind === 'option' ? strikesOf(contracts.get(leg.expiry || ''), leg.type) : [];
      const bad = q.reasons.length > 0;
      const c = q.contract;
      return <Fragment key={leg.id}><tr className={(leg.side === 'buy' ? 'is-buy' : 'is-sell') + (bad ? ' is-nd' : '')} data-leg={i + 1}>
        <td><button type="button" className={'ob-side is-' + leg.side} onClick={() => invert(leg.id)} aria-label={tr('optionbuilder.invert', { n: i + 1 })} title={tr('optionbuilder.invert', { n: i + 1 })}>
          {leg.side === 'buy' ? tr('optionbuilder.buy') : tr('optionbuilder.sell')}</button></td>
        <td className="r"><input className="ob-cell-input is-qty" type="text" inputMode="decimal" value={leg.qty} aria-label={tr('optionbuilder.qtyOf', { n: i + 1 })}
          onChange={e => update(leg.id, { qty: e.target.value })} aria-invalid={q.reasons.includes('bad_qty') || undefined} /></td>
        <td>{leg.kind === 'stock' ? <span className="ob-type is-stock">{tr('optionbuilder.sharesShort')}</span>
          : <select className="ob-cell-select" value={leg.type} aria-label={tr('optionbuilder.typeOf', { n: i + 1 })} onChange={e => update(leg.id, { type: e.target.value as 'call' | 'put' })}>
            <option value="call">{tr('optionbuilder.call')}</option><option value="put">{tr('optionbuilder.put')}</option></select>}
          {showExpiry && leg.kind === 'option' && leg.expiry && <small className={'ob-leg-exp' + (leg.expiry !== selected ? ' is-other' : '')}>{f.shortDate(leg.expiry)}</small>}</td>
        <td className="r">{leg.kind === 'stock' ? <span className="ob-muted">—</span>
          : <select className="ob-cell-select is-strike" value={leg.strike ?? ''} aria-label={tr('optionbuilder.strikeOf', { n: i + 1 })} onChange={e => update(leg.id, { strike: Number(e.target.value) })}>
            {leg.strike != null && !strikes.includes(leg.strike) && <option value={leg.strike}>{f.strike(leg.strike)} · {tr('optionbuilder.notInChain')}</option>}
            {strikes.map(k => <option key={k} value={k}>{f.strike(k)}</option>)}</select>}</td>
        <td className="r"><input className={'ob-cell-input is-price' + (q.priceSource === 'manual' ? ' is-manual' : '')} type="text" inputMode="decimal" value={leg.price || ''}
          placeholder={q.priceSource && q.priceSource !== 'manual' ? fieldNumber(q.price, comma, 2) : f.na}
          title={q.priceSource ? tr(`optionbuilder.src_${q.priceSource}`) : tr('optionbuilder.na')}
          aria-label={tr('optionbuilder.priceOf', { n: i + 1 })} onChange={e => update(leg.id, { price: e.target.value })} aria-invalid={q.reasons.includes('bad_price') || undefined} /></td>
        <td className="r">{leg.kind === 'stock' ? <span className="ob-muted">—</span> : <input className={'ob-cell-input is-iv' + (q.ivSource === 'manual' ? ' is-manual' : '')} type="text" inputMode="decimal" value={leg.iv || ''}
          placeholder={c?.iv != null ? fieldNumber(c.iv * 100, comma, 1) : f.na} title={q.ivSource === 'manual' ? tr('optionbuilder.src_manual') : q.ivSource ? tr('optionbuilder.src_chain') : tr('optionbuilder.na')}
          aria-label={tr('optionbuilder.ivOf', { n: i + 1 })} onChange={e => update(leg.id, { iv: e.target.value })} aria-invalid={q.reasons.includes('bad_iv') || undefined} />}</td>
        <td className="ob-actions"><button type="button" className="ob-icon is-small" onClick={() => remove(leg.id)} aria-label={tr('optionbuilder.remove', { n: i + 1 })} title={tr('optionbuilder.remove', { n: i + 1 })}><X size={13} /></button></td>
      </tr>
      {bad && <tr className="ob-nd-row" data-leg-nd={i + 1}><td colSpan={7}><span className="ob-badge is-bad">{tr('optionbuilder.na')}</span> {q.reasons.map(r => tr(`optionbuilder.nd_${r}`)).join(' · ')}</td></tr>}
      </Fragment>;
    })}</tbody>
  </table></div>;
}

function Scenario({ movePct, setMovePct, ivPts, setIvPts, ivMin, days, setDays, horizon, spot, result, elapsed, onDrag }: { movePct: number; setMovePct: (v: number) => void;
  ivPts: number; setIvPts: (v: number) => void; ivMin: number; days: number; setDays: (v: number) => void; horizon: number | null; spot: number | null;
  result: EngineResult | null; elapsed: number; onDrag: (v: boolean) => void }) {
  const f = useFormat();
  const maxDays = horizon == null ? 0 : Math.ceil(horizon);
  const shownDays = horizon == null ? 0 : Math.min(days, horizon);
  const moved = !!movePct || !!ivPts || !!days;
  return <div className="ob-scenario" aria-label={tr('optionbuilder.scenarioTitle')} role="group">
    <Slider label={tr('optionbuilder.sliderSpot')} value={movePct} min={-30} max={30} step={.5} onChange={setMovePct} data="spot" onDrag={onDrag}
      text={`${f.signed(movePct, 1)}%`} />
    <Slider label={tr('optionbuilder.sliderDays')} value={Math.min(days, maxDays)} min={0} max={Math.max(maxDays, 1)} step={1} onChange={setDays} disabled={horizon == null} data="days"
      text={horizon == null ? f.na : shownDays >= horizon ? tr('optionbuilder.atFirstExpiry') : tr('optionbuilder.tPlus', { d: f.num(shownDays, 0) })} />
    <Slider label={tr('optionbuilder.sliderIv')} value={ivPts} min={ivMin} max={50} step={.5} onChange={setIvPts} data="iv"
      text={tr('optionbuilder.ptsValue', { v: f.signed(ivPts, 1) })} />
    <div className="ob-scn-out" data-scenario-out>
      <span className="ob-k">{tr('optionbuilder.kpiScenario')}</span>
      <strong className={'ob-num' + (!result ? '' : result.scenario.pnl >= 0 ? ' is-up' : ' is-down')} data-scenario-pnl>{result ? <>{f.signed(result.scenario.pnl, 2)}<small>USD</small></> : f.na}</strong>
      <small className="ob-sub">{tr('optionbuilder.scenarioAt', { p: f.num(result ? result.scenario.price : spot == null ? null : spot * (1 + movePct / 100), 2), d: f.num(elapsed, elapsed % 1 ? 2 : 0), v: f.signed(ivPts, 1) })}</small>
      <button type="button" className="ob-btn is-ghost" onClick={() => { setMovePct(0); setIvPts(0); setDays(0); }} disabled={!moved} data-scenario-reset>
        <RotateCcw size={13} aria-hidden />{tr('optionbuilder.reset')}</button>
    </div>
  </div>;
}

function Slider({ label, value, min, max, step, onChange, text, disabled, data, onDrag }: { label: string; value: number; min: number; max: number; step: number;
  onChange: (v: number) => void; text: string; disabled?: boolean; data?: string; onDrag?: (v: boolean) => void }) {
  const pct = max > min ? (value - min) / (max - min) * 100 : 0;
  const zero = max > min ? (Math.min(Math.max(0, min), max) - min) / (max - min) * 100 : 0;
  return <label className="ob-slider">
    <span className="ob-k"><span title={label}>{label}</span><b className="ob-num">{text}</b></span>
    <input type="range" min={min} max={max} step={step} value={value} disabled={disabled} onChange={e => onChange(Number(e.target.value))} data-slider={data}
      onPointerDown={onDrag ? () => {
        onDrag(true);
        const off = () => { onDrag(false); window.removeEventListener('pointerup', off); window.removeEventListener('pointercancel', off); };
        window.addEventListener('pointerup', off); window.addEventListener('pointercancel', off);
      } : undefined}
      onBlur={onDrag ? () => onDrag(false) : undefined}
      style={{ '--ob-fill-a': Math.min(pct, zero) + '%', '--ob-fill-b': Math.max(pct, zero) + '%' } as React.CSSProperties} aria-valuetext={text} />
  </label>;
}

/** Drawer «Details», closed by default: what refines the decision but does not change it. */
function Details({ result, prem, legs, quotes, future, downloaded, now, current, update, requestExpiries }: {
  result: EngineResult | null; prem: ReturnType<typeof premiums>; legs: BuilderLeg[]; quotes: LegQuote[]; future: string[]; downloaded: string[]; now: number;
  current: boolean; update: (id: string, change: Parameters<typeof editLeg>[1]) => void; requestExpiries: (e: string) => void }) {
  const f = useFormat();
  const [open, setOpen] = useState(false);
  const pop = result?.probability_of_profit;
  const ror = result ? returnOnRisk(result) : null;
  const theoreticalLoss = !!result?.unlimited_loss && result.unlimited_loss_reason === 'european_dividend';
  return <details className="ob-card ob-drawer" open={open} onToggle={e => setOpen((e.currentTarget as HTMLDetailsElement).open)} data-details>
    <summary>
      <ChevronDown size={16} aria-hidden className="ob-chev" />
      <span className="ob-drawer-title">{tr('optionbuilder.details')}</span>
      <span className="ob-drawer-hint">{tr('optionbuilder.detailsHint')}</span>
      <span className="ob-grow" />
      <span className="ob-drawer-pop" title={pop?.basis}>{tr('optionbuilder.kpiPop')} <b className="ob-num">{pop ? f.pct(pop.value, 1) : f.na}</b></span>
    </summary>
    {open && <div className="ob-drawer-body">
      <div className="ob-drawer-grid">
        <table className="ob-greeks">
          <caption>{tr('optionbuilder.greeks')}<small>{tr('optionbuilder.greeksNote')}</small></caption>
          <thead><tr><th scope="col" /><th scope="col">{tr('optionbuilder.today')}</th><th scope="col">{tr('optionbuilder.scenario')}</th><th scope="col">{tr('optionbuilder.unit')}</th></tr></thead>
          <tbody>{(['delta', 'gamma', 'vega', 'theta', 'rho'] as const).map(g => <tr key={g}>
            <th scope="row">{tr(`optionbuilder.g_${g}`)}</th>
            <td className="ob-num">{result ? f.num(result.today[g], g === 'gamma' ? 3 : 2) : f.na}</td>
            <td className="ob-num">{result ? f.num(result.scenario[g], g === 'gamma' ? 3 : 2) : f.na}</td>
            <td>{tr(`optionbuilder.u_${g}`)}</td></tr>)}</tbody>
        </table>
        <div className="ob-panel">
          <h4>{tr('optionbuilder.riskTitle')}</h4>
          <dl className="ob-kpis">
            <Kpi label={tr('optionbuilder.kpiPop')} value={pop ? f.pct(pop.value, 1) : f.na}
              sub={pop ? tr('optionbuilder.kpiPopSub', { s: f.pct(pop.sigma, 1), d: f.num(pop.horizon_days, 1) }) : undefined} />
            <Kpi label={tr('optionbuilder.kpiRor')} value={ror == null ? (result && (result.unlimited_profit || result.unlimited_loss) ? tr('optionbuilder.rorUndefined') : f.na) : f.pct(ror, 0)} />
            <Kpi label={tr('optionbuilder.kpiSpread')} value={f.num(prem.spread, 2)} sub={tr('optionbuilder.kpiSpreadSub', { a: f.num(prem.natural, 2), b: f.num(prem.mid, 2) })} />
          </dl>
          {theoreticalLoss && result?.tail_reference && <p className="ob-note">{tr('optionbuilder.lossTheoreticalNote', { v: f.signed(result.tail_reference.pnl, 2), p: f.num(result.tail_reference.price, 2) })}</p>}
          {result?.tail_limit != null && <p className="ob-note">{tr('optionbuilder.tailLimit', { v: f.signed(result.tail_limit, 2) })}</p>}
          {!!result?.forward_vols?.length && <ul className="ob-note ob-fwd" data-forward-vols aria-label={tr('optionbuilder.volModel')}>
            {result.forward_vols.map(v => <li key={v.index}>{v.forward_iv == null
              ? tr('optionbuilder.forwardVolNd', { n: v.index + 1, l: f.pct(v.leg_iv, 1), s: f.pct(v.near_iv, 1) })
              : tr('optionbuilder.forwardVolRow', { n: v.index + 1, f: f.pct(v.forward_iv, 1), l: f.pct(v.leg_iv, 1), s: f.pct(v.near_iv, 1) })}
              {result.vol_model === 'constant' ? ' · ' + tr('optionbuilder.volBadge_constant') : ''}</li>)}</ul>}
          {legs.some(l => l.kind === 'stock') && <p className="ob-note">{tr('optionbuilder.sharesNote')}</p>}
        </div>
      </div>
      {legs.length > 0 && <LegsDetail legs={legs} quotes={quotes} future={future} downloaded={downloaded} now={now} result={result && current ? result : null}
        update={update} requestExpiries={requestExpiries} />}
      {result && <Heatmap result={result} />}
      <div className="ob-panel ob-method">
        <h4>{tr('optionbuilder.method')}</h4>
        <ul>{[tr('optionbuilder.subtitle'), tr('optionbuilder.engineNote'), tr('optionbuilder.daysNote'), tr('optionbuilder.priceNote'), ...(result?.limits || []),
          ...(result?.forward_vols?.length && result.vol_model_basis ? [result.vol_model_basis] : []),
          ...(result?.probability_of_profit ? [result.probability_of_profit.basis] : [])].map(t => <li key={t}>{t}</li>)}</ul>
      </div>
    </div>}
  </details>;
}

function Kpi({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return <div className="ob-kpi"><dt>{label}</dt><dd className="ob-num">{value}</dd>{sub && <dd className="ob-sub">{sub}</dd>}</div>;
}

function LegsDetail({ legs, quotes, future, downloaded, now, result, update, requestExpiries }: {
  legs: BuilderLeg[]; quotes: LegQuote[]; future: string[]; downloaded: string[]; now: number; result: EngineResult | null;
  update: (id: string, change: Parameters<typeof editLeg>[1]) => void; requestExpiries: (e: string) => void }) {
  const f = useFormat();
  return <div className="ob-panel"><h4>{tr('optionbuilder.legsDetail')}</h4>
    <div className="ob-table-wrap"><table className="ob-table ob-detail-table">
      <thead><tr>
        <th scope="col">#</th><th scope="col">{tr('optionbuilder.col_contract')}</th><th scope="col">{tr('optionbuilder.col_expiry')}</th>
        <th scope="col" className="r">{tr('optionbuilder.col_bid')}</th><th scope="col" className="r">{tr('optionbuilder.col_ask')}</th><th scope="col" className="r">{tr('optionbuilder.col_mid')}</th>
        <th scope="col">{tr('optionbuilder.col_src')}</th><th scope="col">{tr('optionbuilder.col_quote')}</th>
        <th scope="col" className="r">{tr('optionbuilder.col_delta')}</th><th scope="col" className="r">{tr('optionbuilder.col_theta')}</th><th scope="col" className="r">{tr('optionbuilder.col_vega')}</th>
        <th scope="col" className="r">{tr('optionbuilder.col_pnl')}</th>
      </tr></thead>
      <tbody>{legs.map((leg, i) => {
        const q = quotes[i];
        const c = q.contract;
        const detail = result?.legs?.[i];
        return <tr key={leg.id} data-leg-detail={i + 1}>
          <td className="ob-num">{i + 1}</td>
          <td className="ob-code">{leg.kind === 'stock' ? tr('optionbuilder.sharesMeta') : c?.contract || f.na}</td>
          <td>{leg.kind === 'stock' ? <span className="ob-muted">—</span>
            : <select className="ob-cell-select is-expiry" value={leg.expiry} aria-label={tr('optionbuilder.expiryOf', { n: i + 1 })} onChange={e => {
              const v = e.target.value; update(leg.id, { expiry: v });
              if (!downloaded.includes(v)) requestExpiries(v);
            }}>{[...new Set([...(leg.expiry ? [leg.expiry] : []), ...future])].sort().map(e => <option key={e} value={e}>
              {e.slice(0, 4) === new Date(now).toISOString().slice(0, 4) ? f.shortDate(e) : f.date(e)} · {tr('optionbuilder.dte', { d: f.num(daysToClose(e, now), daysToClose(e, now) < 2 ? 2 : 0) })}{downloaded.includes(e) ? '' : ' · ' + tr('optionbuilder.toDownload')}</option>)}</select>}</td>
          <td className="r ob-num">{leg.kind === 'stock' ? '—' : f.num(q.bid, 2)}</td>
          <td className="r ob-num">{leg.kind === 'stock' ? '—' : f.num(q.ask, 2)}</td>
          <td className="r ob-num">{leg.kind === 'stock' ? '—' : f.num(q.mid, 2)}
            {q.bid != null && q.ask != null && q.mid != null && q.mid > 0 && <small className="ob-muted">{tr('optionbuilder.spreadPct', { v: f.num((q.ask - q.bid) / q.mid * 100, 0) })}</small>}</td>
          <td><span className={'ob-src' + (q.priceSource === 'manual' ? ' is-manual' : '')}>{q.priceSource ? tr(`optionbuilder.src_${q.priceSource}`) : tr('optionbuilder.na')}</span>
            {leg.kind === 'option' && <small className={'ob-src' + (q.ivSource === 'manual' ? ' is-manual' : '')}>IV {q.ivSource === 'manual' ? tr('optionbuilder.src_manual') : q.ivSource ? tr('optionbuilder.src_chain') : tr('optionbuilder.na')}</small>}</td>
          <td className="ob-muted">{c ? tr('optionbuilder.quoteMeta', { tf: c.quote_timeframe || tr('optionbuilder.na'), time: f.time(c.quote_timestamp) }) + ' · ×' + f.num(c.multiplier, 0) + (c.quality.length ? ' · ' + c.quality.join(' · ') : '') : '—'}</td>
          <td className="r ob-num">{detail ? f.num(detail.delta, 1) : f.na}</td>
          <td className="r ob-num">{detail ? f.num(detail.theta, 2) : f.na}</td>
          <td className="r ob-num">{detail ? f.num(detail.vega, 2) : f.na}</td>
          <td className={'r ob-num' + (detail ? detail.pnl_today >= 0 ? ' is-up' : ' is-down' : '')}>{detail ? f.signed(detail.pnl_today, 2) : f.na}</td>
        </tr>;
      })}</tbody>
    </table></div></div>;
}

function Heatmap({ result }: { result: EngineResult }) {
  const f = useFormat();
  const max = Math.max(1, ...result.heatmap.flatMap(r => r.cells.map(c => Math.abs(c.pnl))));
  return <div className="ob-panel ob-heat" aria-labelledby="ob-heat-title">
    <h4 id="ob-heat-title">{tr('optionbuilder.heatmap')} <small>{tr('optionbuilder.heatmapNote')}</small></h4>
    <div className="ob-table-wrap"><table className="ob-heat-table">
      <thead><tr><th scope="col">{tr('optionbuilder.heatDays')}</th>{result.heatmap[0]?.cells.map(c => <th key={c.price} scope="col" className="ob-num">{f.num(c.price, 2)}</th>)}</tr></thead>
      <tbody>{result.heatmap.map((row, i) => <tr key={i}><th scope="row" className="ob-num">{f.num(row.elapsed_days, row.elapsed_days % 1 ? 1 : 0)}</th>
        {row.cells.map(c => <td key={c.price} className="ob-num" style={{ '--ob-heat': (Math.abs(c.pnl) / max).toFixed(3) } as React.CSSProperties}
          data-tone={c.pnl >= 0 ? 'up' : 'down'}>{f.signed(c.pnl, 0)}</td>)}</tr>)}</tbody>
    </table></div>
  </div>;
}

/** Tiny payoff silhouette for the strategy select (decorative). */
function PresetGlyph({ id }: { id: PresetId }) {
  const paths: Record<PresetId, string> = {
    long_call: 'M2 12H9L16 4', long_put: 'M2 4L9 12H16', covered_call: 'M2 14L9 6H16', cash_secured_put: 'M2 14L9 6H16',
    bull_call: 'M2 12H6L11 5H16', bear_call: 'M2 5H6L11 12H16', bull_put: 'M2 12H6L11 5H16', bear_put: 'M2 5H6L11 12H16',
    straddle: 'M2 4L9 12L16 4', strangle: 'M2 4L6 11H11L16 4', iron_condor: 'M2 12L5 6H12L15 12', iron_butterfly: 'M2 12L9 5L16 12', calendar: 'M2 12Q9 1 16 12',
  };
  return <svg className="ob-glyph" viewBox="0 0 18 16" aria-hidden data-glyph={id}><path d="M1 9H17" className="ob-glyph-zero" /><path d={paths[id]} /></svg>;
}

