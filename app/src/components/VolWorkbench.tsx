import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDownLeft, ArrowUpRight, Check, ChevronDown, Layers3, Plus, RefreshCw, Trash2 } from 'lucide-react';
import {
  blankLeg, contractLeg, daysToExpiry, numberInput, numericText, serializeLegs, volNumber, volRequest,
  type ChainPage, type Coverage, type ExpiryCatalog, type LegDraft, type OptionContract, type StrategyResult,
} from '@/lib/vol-deck';
import './vol-workbench.css';

type Mode = 'surface' | 'desk' | 'laboratory';
type Props = { ticker: string; mode: Mode; coverage?: Coverage; surfaceBusy: boolean;
  onSurface: (expiries: string[]) => void; onLaboratory: () => void };

function Datum({ label, value, unit, tone }: { label: string; value: string; unit?: string; tone?: string }) {
  return <div className={'vd-datum ' + (tone || '')}><span>{label}</span><strong>{value}</strong>{unit && <small>{unit}</small>}</div>;
}

function NumField({ label, ariaLabel, value, onChange, unit, min, max }: { label: string; ariaLabel?: string; value: string; onChange: (s: string) => void; unit?: string; min?: number; max?: number }) {
  return <label className="vd-field"><span>{label}</span><div><input type="text" inputMode="decimal" value={value}
    onChange={e => onChange(e.target.value)} aria-label={ariaLabel || label} autoComplete="off" spellCheck={false}
    aria-description={min == null ? undefined : `Da ${min} a ${max ?? 'senza limite'}`} />{unit && <i>{unit}</i>}</div></label>;
}

export default function VolWorkbench({ ticker, mode, coverage, surfaceBusy, onSurface, onLaboratory }: Props) {
  const [catalog, setCatalog] = useState<ExpiryCatalog | null>(null);
  const [catalogBusy, setCatalogBusy] = useState(false);
  const [catalogError, setCatalogError] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [chainExpiry, setChainExpiry] = useState('');
  const [chain, setChain] = useState<ChainPage | null>(null);
  const [chainBusy, setChainBusy] = useState(false);
  const [chainError, setChainError] = useState('');
  const [filter, setFilter] = useState('all');
  const [strikeSearch, setStrikeSearch] = useState('');
  const [inspect, setInspect] = useState<OptionContract | null>(null);
  const [legs, setLegs] = useState<LegDraft[]>([]);
  const catalogRequest = useRef<AbortController | null>(null);
  const chainRequest = useRef<AbortController | null>(null);

  useEffect(() => {
    catalogRequest.current?.abort(); chainRequest.current?.abort();
    setCatalog(null); setCatalogError(''); setChain(null); setChainError(''); setSelected([]);
    setChainExpiry(''); setInspect(null); setLegs([]); setCatalogBusy(false); setChainBusy(false);
    if (ticker) void loadCatalog(false);
    return () => { catalogRequest.current?.abort(); chainRequest.current?.abort(); };
  }, [ticker]);

  async function loadCatalog(more: boolean) {
    catalogRequest.current?.abort();
    const controller = new AbortController(); catalogRequest.current = controller;
    setCatalogBusy(true); setCatalogError('');
    try {
      const suffix = more && catalog?.next_after ? '?after=' + encodeURIComponent(catalog.next_after) : '';
      const result = await volRequest<ExpiryCatalog>(`/options/expiry_catalog/${encodeURIComponent(ticker)}${suffix}`, undefined, controller.signal);
      if (controller.signal.aborted) return;
      if (!Array.isArray(result.expirations)) throw new Error('Catalogo senza elenco scadenze');
      const dates = Array.from(new Set([...(more ? catalog?.expirations || [] : []), ...result.expirations])).sort();
      setCatalog({ ...result, expirations: dates }); setCatalogError(result.error || '');
      if (!more) {
        setSelected(dates.filter(e => daysToExpiry(e) >= 2).slice(0, 4));
        setChainExpiry(dates[0] || '');
      }
    } catch (e) { if (!controller.signal.aborted) setCatalogError(String(e instanceof Error ? e.message : e)); }
    finally { if (!controller.signal.aborted) setCatalogBusy(false); }
  }

  async function loadChain(more = false) {
    chainRequest.current?.abort();
    const controller = new AbortController(); chainRequest.current = controller;
    setChainBusy(true); setChainError(''); setInspect(null);
    if (!more) setChain(null);
    try {
      const cursor = more && chain?.next_cursor ? '&cursor=' + encodeURIComponent(chain.next_cursor) : '';
      const result = await volRequest<ChainPage>(`/options/chain_detail/${encodeURIComponent(ticker)}?expiry=${chainExpiry}${cursor}`, undefined, controller.signal);
      if (controller.signal.aborted) return;
      if (!Array.isArray(result.chain)) throw new Error('Risposta senza elenco contratti');
      const previous = more ? chain?.chain || [] : [];
      const seen = new Map<string, OptionContract>();
      [...previous, ...result.chain].forEach((r, i) => seen.set(r.contract || `${r.expiry}:${r.type}:${r.strike}:${i}`, r));
      setChain({ ...result, chain: [...seen.values()], spot: result.spot ?? (more ? chain?.spot ?? null : null) });
      setChainError(result.error || '');
    } catch (e) { if (!controller.signal.aborted) setChainError(String(e instanceof Error ? e.message : e)); }
    finally { if (!controller.signal.aborted) setChainBusy(false); }
  }

  const months = useMemo(() => {
    const groups = new Map<string, string[]>();
    for (const e of catalog?.expirations || []) groups.set(e.slice(0, 7), [...(groups.get(e.slice(0, 7)) || []), e]);
    return [...groups];
  }, [catalog]);
  const visibleRows = (chain?.chain || []).filter(r => (filter === 'all' || r.type === filter)
    && (!strikeSearch || String(r.strike).includes(strikeSearch.replace(',', '.'))));

  const addContract = (row: OptionContract, side: 'buy' | 'sell') => {
    if (legs.length >= 12 || row.adjusted || !['call', 'put'].includes(row.type)) return;
    setLegs(prev => [...prev, contractLeg(row, side)]);
  };

  return <div className="vol-workbench">
    <section className="vd-catalog" aria-labelledby="vd-calendar-title">
      <div className="vd-section-head"><div><h2 id="vd-calendar-title">Le scadenze, una per una</h2>
        <p>{ticker ? `${ticker}: scegli cosa caricare. Il catalogo non scarica le chain.` : 'Inserisci un ticker per esplorare le scadenze. Il laboratorio può lavorare anche con ipotesi manuali.'}</p></div>
        {ticker && <button className="vd-icon-button" disabled={catalogBusy} onClick={() => loadCatalog(false)} title="Rileggi il catalogo"><RefreshCw size={16} /><span>Rileggi</span></button>}
      </div>
      {catalogBusy && <p className="vd-loading" role="status">Lettura delle scadenze disponibili…</p>}
      {catalogError && <p className="vd-error" role="alert">{catalogError}{catalog?.expirations.length ? ' Le date già ricevute restano visibili; catalogo incompleto.' : ''}</p>}
      {catalog && <>
        <div className="vd-catalog-summary"><span className={catalog.complete ? 'vd-ok' : 'vd-amber'}>{catalog.complete ? <Check size={14} /> : <Layers3 size={14} />}
          {catalog.expirations.length} date ricevute · {catalog.complete ? 'fine catalogo confermata dal provider' : 'catalogo ancora incompleto'}</span>
          <span>{selected.length}/8 selezionate per il mesh</span>
          <span>{catalog.requests_used}/{catalog.request_budget} richieste nell’ultimo caricamento</span></div>
        <div className="vd-months">{months.map(([month, dates]) => <div className="vd-month" key={month}>
          <h3>{new Date(month + '-01T12:00:00Z').toLocaleDateString('it-IT', { month: 'long', year: 'numeric' })}</h3>
          <div>{dates.map(e => { const days = daysToExpiry(e); const isSelected = selected.includes(e); const status = coverage?.rows.find(r => r.expiry === e);
            return <button key={e} className={'vd-date ' + (isSelected ? 'selected ' : '') + (status?.status || '')}
              aria-pressed={isSelected} disabled={!isSelected && (selected.length >= 8 || days < 2)}
              title={days < 2 ? `${e}: disponibile nella chain; esclusa dal mesh 0–1 DTE` : `${e}${status?.reason ? ': ' + status.reason : ''}`}
              onClick={() => setSelected(prev => prev.includes(e) ? prev.filter(x => x !== e) : [...prev, e].sort())}>
              <b>{e.slice(8)}</b><small>{days}g</small>{isSelected && <Check size={11} />}</button>;
          })}</div>
        </div>)}</div>
        <div className="vd-actions">
          {!catalog.complete && <button className="vd-secondary" disabled={catalogBusy} onClick={() => loadCatalog(true)}><ChevronDown size={14} />Altre scadenze</button>}
          <button className="vd-primary" disabled={!selected.length || surfaceBusy} onClick={() => onSurface(selected)}>{surfaceBusy ? 'Caricamento superficie…' : 'Carica superficie selezionata'}</button>
          <button className="vd-secondary" onClick={onLaboratory}>Chain, greche e strategie</button>
          <small>Massimo due pagine chain per scadenza nel mesh; copertura dichiarata. 0–1 DTE consultabili nella chain.</small>
        </div>
      </>}
      {coverage && <details className="vd-coverage" open={!coverage.complete}>
        <summary>Copertura dell’ultima superficie: {coverage.loaded.length}/{coverage.requested.length} curve · {coverage.excluded.length} escluse · {coverage.errors.length} errori{!coverage.complete ? ' · incompleta' : ''}</summary>
        <ul>{coverage.rows.map(row => <li key={row.expiry} className={row.status}><span>{row.expiry}</span><b>{({ loaded: 'caricata', partial: 'parziale', error: 'errore', excluded: 'esclusa' })[row.status]}</b><span>{row.reason || `${row.n_contracts ?? 'n.d.'} contratti ricevuti`}</span></li>)}</ul>
      </details>}
    </section>

    <div hidden={mode !== 'laboratory'}>
      <section className="vd-chain" aria-labelledby="vd-chain-title">
        <div className="vd-section-head"><div><h2 id="vd-chain-title">La chain osservata</h2><p>Quote, IV e greche del provider. Clicca un contratto per controllarne qualità e timestamp.</p></div>
          <div className="vd-actions"><label>Scadenza <select value={chainExpiry} aria-label="Scadenza chain" onChange={e => {
            chainRequest.current?.abort(); setChainBusy(false); setChainExpiry(e.target.value); setChain(null); setChainError(''); setInspect(null);
          }}><option value="">Scegli una data</option>{catalog?.expirations.map(e => <option key={e} value={e}>{e} · {daysToExpiry(e)}g</option>)}</select></label>
          <button className="vd-primary" disabled={!chainExpiry || chainBusy} onClick={() => loadChain(false)}>{chainBusy ? 'Caricamento…' : 'Carica chain'}</button></div>
        </div>
        {chainError && <p className="vd-error" role="alert">{chainError}</p>}
        {chain && <>
          <div className="vd-chain-tools"><div className="vd-segment">{[['all', 'Tutte'], ['call', 'Call'], ['put', 'Put']].map(([id, label]) => <button key={id} aria-pressed={filter === id} onClick={() => setFilter(id)}>{label}</button>)}</div>
            <label>Cerca strike <input value={strikeSearch} inputMode="decimal" aria-label="Cerca strike" onChange={e => setStrikeSearch(e.target.value)} /></label>
            <span>{visibleRows.length}/{chain.chain.length} contratti · {chain.complete ? 'chain completa secondo il provider' : 'altre pagine disponibili'}{chain.malformed_contracts ? ` · ${chain.malformed_contracts} righe illeggibili` : ''}</span>
            <span>Download {new Date(chain._timestamp).toLocaleTimeString('it-IT')}{chain.cached ? ' · cache dichiarata' : ''}</span></div>
          <div className="vd-chain-scroll"><table><thead><tr><th>Tipo</th><th>Strike</th><th>Bid</th><th>Ask</th><th>IV %</th><th>Delta</th><th>Gamma</th><th>Vega</th><th>Theta</th><th>OI</th><th>Qualità</th><th>Strategia</th></tr></thead>
            <tbody>{visibleRows.map((row, i) => <tr key={row.contract || i} className={(row.strike && chain.spot && Math.abs(row.strike / chain.spot - 1) < .01 ? 'atm ' : '') + (inspect === row ? 'inspected' : '')}>
              <td><button className={'vd-contract-type ' + row.type} onClick={() => setInspect(row)} aria-label={`Dettaglio ${row.type} strike ${row.strike}`}>{row.type}</button></td>
              <th scope="row"><button className="vd-cell-button" onClick={() => setInspect(row)}>{volNumber(row.strike)}</button></th>
              <td>{volNumber(row.bid)}</td><td>{volNumber(row.ask)}</td><td>{volNumber(row.iv == null ? null : row.iv * 100, 1)}</td>
              <td>{volNumber(row.delta, 3)}</td><td>{volNumber(row.gamma, 4)}</td><td>{volNumber(row.vega, 3)}</td><td>{volNumber(row.theta, 3)}</td><td>{volNumber(row.oi, 0)}</td>
              <td><button className="vd-quality" onClick={() => setInspect(row)}>{row.quality.length ? `${row.quality.length} note` : 'Campi presenti'}</button></td>
              <td className="vd-leg-actions"><button disabled={legs.length >= 12 || !row.strike || row.adjusted || !['call', 'put'].includes(row.type)} onClick={() => addContract(row, 'buy')} aria-label={`Aggiungi acquisto ${row.type} ${row.strike}`}><Plus size={12} />Compra</button><button disabled={legs.length >= 12 || !row.strike || row.adjusted || !['call', 'put'].includes(row.type)} onClick={() => addContract(row, 'sell')}>Vendi</button></td>
            </tr>)}</tbody></table>
            {!visibleRows.length && <p className="vd-empty">Nessun contratto nei filtri selezionati.</p>}
          </div>
          <div className="vd-actions">{chain.next_cursor && <button className="vd-secondary" disabled={chainBusy} onClick={() => loadChain(true)}>Carica altri contratti</button>}
            <small>Ogni pagina carica al massimo 250 contratti. Greche e prezzi sono per unità sottostante; il moltiplicatore si applica nel laboratorio.</small></div>
          {inspect && <div className="vd-contract-inspector"><h3>{inspect.contract || `${inspect.type} ${inspect.strike}`} <span>{inspect.exercise_style || 'stile di esercizio n.d.'}</span></h3>
            <p>{inspect._source} · quota {inspect.quote_timestamp ? new Date(inspect.quote_timestamp).toLocaleString('it-IT') : 'timestamp n.d.'} · {inspect.quote_timeframe || 'ritardo n.d.'} · moltiplicatore {volNumber(inspect.multiplier, 0)} · rho {volNumber(inspect.rho, 3)}</p>
            {inspect.quality.length ? <ul>{inspect.quality.map(note => <li key={note}>{note}</li>)}</ul> : <p>I campi richiesti sono presenti; controlla l’istante della quota prima di usarla come riferimento.</p>}</div>}
        </>}
        {!chain && !chainBusy && <div className="vd-empty">Seleziona una scadenza e carica la sua chain. I dati mancanti compariranno come n.d.</div>}
      </section>
      <StrategyLab key={ticker} ticker={ticker} legs={legs} setLegs={setLegs} observedSpot={chain?.spot ?? null} />
    </div>
  </div>;
}

function StrategyLab({ ticker, legs, setLegs, observedSpot }: { ticker: string; legs: LegDraft[]; setLegs: (legs: LegDraft[] | ((p: LegDraft[]) => LegDraft[])) => void; observedSpot: number | null }) {
  const [spot, setSpot] = useState(''); const [scenarioSpot, setScenarioSpot] = useState('');
  const [rate, setRate] = useState('0'); const [dividend, setDividend] = useState('0');
  const [elapsed, setElapsed] = useState('0'); const [shift, setShift] = useState('0');
  const [commission, setCommission] = useState('0'); const [currency, setCurrency] = useState('USD');
  const [result, setResult] = useState<StrategyResult | null>(null); const [busy, setBusy] = useState(false);
  const [error, setError] = useState(''); const [calculatedKey, setCalculatedKey] = useState('');
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  const configKey = JSON.stringify([legs, spot, scenarioSpot, rate, dividend, elapsed, shift, commission, currency]);
  const stale = result != null && configKey !== calculatedKey;

  function update(id: string, field: keyof LegDraft, value: string) {
    setLegs(prev => prev.map(l => l.id === id ? { ...l, [field]: value, source: 'Ipotesi modificata nel laboratorio (non quota corrente).' } : l));
  }
  async function simulate() {
    request.current?.abort(); const controller = new AbortController(); request.current = controller;
    setError(''); setBusy(true);
    try {
      const body = { spot: numberInput(spot, 'Spot iniziale'), scenario_spot: numberInput(scenarioSpot || spot, 'Prezzo scenario'),
        rate: numberInput(rate, 'Tasso') / 100, dividend_yield: numberInput(dividend, 'Dividend yield') / 100,
        elapsed_days: numberInput(elapsed, 'Giorni trascorsi'), iv_shift: numberInput(shift, 'Shock IV') / 100,
        commission: numberInput(commission, 'Costo per contratto'), currency, legs: serializeLegs(legs) };
      const out = await volRequest<StrategyResult>('/options/strategy/simulate', body, controller.signal);
      if (!controller.signal.aborted) { setResult(out); setCalculatedKey(configKey); }
    } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }
  function pair(kind: 'vertical' | 'straddle' | 'calendar') {
    if (!legs.length || legs.length >= 12) return;
    const first = legs[0]; const second = { ...blankLeg(), ...first, id: blankLeg().id, source: 'Gamba derivata: completa i nuovi campi prima di simulare.' };
    if (kind === 'vertical') { second.side = first.side === 'buy' ? 'sell' : 'buy'; second.strike = ''; second.premium = ''; }
    if (kind === 'straddle') { second.type = first.type === 'call' ? 'put' : 'call'; second.premium = ''; }
    if (kind === 'calendar') { second.side = first.side === 'buy' ? 'sell' : 'buy'; second.days = ''; second.premium = ''; second.expiry = undefined; }
    setLegs(prev => [...prev, second]);
  }

  return <section className="vd-lab" aria-labelledby="vd-lab-title">
    <div className="vd-section-head"><div><h2 id="vd-lab-title">Disegna la strategia</h2><p>{ticker || 'Sottostante'} · laboratorio teorico locale. Nessun ordine viene inviato.</p></div>
      <span className="vd-model">Black–Scholes–Merton · europeo</span></div>
    <div className="vd-lab-grid">
      <div className="vd-legs"><div className="vd-legs-heading"><h3>Gambe <span>{legs.length}/12</span></h3><button className="vd-secondary" disabled={legs.length >= 12} onClick={() => setLegs(prev => [...prev, blankLeg()])}><Plus size={14} />Manuale</button></div>
        {!legs.length && <div className="vd-leg-empty"><Layers3 size={30} /><h4>Parti da un contratto</h4><p>Usa Compra o Vendi nella chain, oppure aggiungi una gamba manuale. Premio, IV e moltiplicatore restano visibili e modificabili.</p></div>}
        {legs.map((leg, i) => <article className={'vd-leg ' + leg.side} key={leg.id}>
          <div className="vd-leg-head"><span>{leg.side === 'buy' ? <ArrowUpRight size={18} /> : <ArrowDownLeft size={18} />}Gamba {i + 1}</span><button className="vd-icon-button" aria-label={`Rimuovi gamba ${i + 1}`} onClick={() => setLegs(prev => prev.filter(l => l.id !== leg.id))}><Trash2 size={14} /></button></div>
          <div className="vd-leg-kind"><select aria-label={`Direzione gamba ${i + 1}`} value={leg.side} onChange={e => update(leg.id, 'side', e.target.value)}><option value="buy">Compra</option><option value="sell">Vendi</option></select><select aria-label={`Tipo gamba ${i + 1}`} value={leg.type} onChange={e => update(leg.id, 'type', e.target.value)}><option value="call">Call</option><option value="put">Put</option></select></div>
          <div className="vd-leg-fields">{([['strike', 'Strike'], ['premium', 'Premio / unità'], ['quantity', 'Contratti'], ['multiplier', 'Moltiplicatore'], ['days', 'Giorni a scadenza'], ['iv', 'IV %']] as const).map(([key, label]) => <NumField key={key} label={label} ariaLabel={`${label} · gamba ${i + 1}`} value={leg[key]} onChange={v => update(leg.id, key, v)} />)}</div>
          <p className="vd-leg-source">{leg.source}{leg.quote_timestamp && <><br />Quota: {new Date(leg.quote_timestamp).toLocaleString('it-IT')}</>}</p>
        </article>)}
        {legs.length > 0 && legs.length < 12 && <details className="vd-pair"><summary>Costruisci dalla prima gamba</summary><button onClick={() => pair('vertical')}>Spread verticale</button><button onClick={() => pair('straddle')}>Straddle</button><button onClick={() => pair('calendar')}>Calendar spread</button><p>Strike, giorni o premio della nuova gamba restano da completare: nessuna quota inventata.</p></details>}
      </div>
      <div className="vd-payoff-area">
        <div className="vd-scenario-controls"><NumField label="Spot iniziale" value={spot} onChange={setSpot} unit={currency} />
          <NumField label="Prezzo scenario" value={scenarioSpot} onChange={setScenarioSpot} unit={currency} />
          <NumField label="Tempo trascorso" value={elapsed} onChange={setElapsed} unit="giorni" />
          <NumField label="Shock IV" value={shift} onChange={setShift} unit="punti %" /></div>
        <div className="vd-actions">{observedSpot != null && <button className="vd-secondary" onClick={() => { setSpot(numericText(observedSpot)); setScenarioSpot(numericText(observedSpot)); }}>Usa spot della chain: {volNumber(observedSpot)}</button>}
          <small>Prezzo scenario vuoto = spot iniziale, senza shock di prezzo. Le IV sono espresse in percentuale; +5 significa +5 punti.</small></div>
        {stale && <p className="vd-stale" role="status">Ipotesi modificate: il grafico mostra l’ultima simulazione. Ricalcola per aggiornarlo.</p>}
        {error && <p className="vd-error" role="alert">{error}</p>}
        {result ? <>
          <div className="vd-payoff-title"><div><h3>La forma del rendimento</h3><p>{result.same_expiry ? `Payoff a ${volNumber(result.expiry_days, 0)} giorni e valore teorico intermedio` : 'Scadenze diverse: scenario teorico fino alla prima scadenza'}</p></div><span>{result.currency}</span></div>
          <PayoffChart result={result} />
          <div className="vd-result-strip"><Datum label={result.entry_kind === 'debit' ? 'Esborso iniziale' : 'Incasso iniziale'} value={volNumber(Math.abs(result.entry_cost))} unit={result.currency} />
            <Datum label="P&L scenario" value={volNumber(result.scenario.pnl)} unit={`${result.currency} a ${volNumber(result.scenario.price)}`} tone={result.scenario.pnl >= 0 ? 'positive' : 'negative'} />
            <Datum label="Profitto max a scadenza" value={result.unlimited_profit ? 'Illimitato' : volNumber(result.max_profit)} unit={result.same_expiry ? result.currency : 'non definito per calendari'} />
            <Datum label="Perdita max a scadenza" value={result.unlimited_loss ? 'Illimitata' : volNumber(result.max_loss)} unit={result.same_expiry ? result.currency : 'non definita per calendari'} /></div>
          <div className="vd-breakeven">Pareggio a scadenza <strong>{result.same_expiry ? [...result.breakevens.map(v => volNumber(v)), ...(result.breakeven_intervals || []).map(v => `da ${volNumber(v.from)} a ${v.to == null ? '∞' : volNumber(v.to)}`)].join(' / ') || 'nessun attraversamento dello zero' : 'n.d. per scadenze diverse'}</strong><span>Premi e commissioni iniziali inclusi</span></div>
          <div className="vd-greeks"><h4>Greche dello scenario <small>aggregate su quantità e moltiplicatori</small></h4>
            <div>{(['delta', 'gamma', 'vega', 'theta', 'rho'] as const).map(key => <Datum key={key} label={key} value={volNumber(result.scenario[key], key === 'gamma' ? 4 : 2)} unit={result.greek_units[key]} />)}</div></div>
          <ScenarioHeatmap result={result} />
        </> : <div className="vd-payoff-empty"><svg viewBox="0 0 560 180" role="img" aria-label="Area del grafico payoff: completa le gambe per calcolare"><path d="M20 145H540M90 25V162" stroke="#334766" fill="none" /><path d="M35 125H200L355 55H520" stroke="#e9ba64" strokeWidth="3" fill="none" strokeDasharray="6 6" /><text x="300" y="155" fill="#a9bad1" fontSize="12">Schema illustrativo, senza valori</text></svg><h3>Il payoff prende forma dalle tue ipotesi</h3><p>Completa almeno una gamba e lo spot. La curva oro mostrerà il risultato a scadenza; la curva cyan il valore nello scenario.</p></div>}
        <div className="vd-model-inputs"><h4>Ipotesi del modello</h4><div><NumField label="Tasso annuo" value={rate} onChange={setRate} unit="%" /><NumField label="Dividend yield annuo" value={dividend} onChange={setDividend} unit="%" /><NumField label="Costo iniziale / contratto" value={commission} onChange={setCommission} unit={currency} /><label className="vd-field"><span>Valuta comune</span><input value={currency} maxLength={3} onChange={e => setCurrency(e.target.value.toUpperCase())} aria-label="Valuta della simulazione" /></label></div>
          <p>I valori iniziali zero di tasso, dividend yield e costi sono ipotesi modificabili, non dati di mercato. Premi per unità sottostante; costi per contratto. Nessun cambio valuta.</p></div>
        <div className="vd-actions vd-simulate"><button className="vd-primary" disabled={!legs.length || busy} onClick={simulate}>{busy ? 'Calcolo in corso…' : stale ? 'Ricalcola scenario' : 'Simula strategia'}</button><span>Solo calcolo locale · nessun provider o modello AI</span></div>
        <details className="vd-model-notes"><summary>Metodo e limiti da leggere</summary><ul>{(result?.limits || ['Modello europeo: non valorizza esercizio anticipato americano o dividendi discreti.', 'ACT/365 su giorni di calendario; orario di scadenza intraday non modellato.', 'IV costante per gamba con shock parallelo. Premi di ingresso inseriti o copiati dalla chain: non sono prezzi garantiti.', 'Scadenze miste: gli scenari si fermano alla prima scadenza. Nessun payoff finale inventato.', 'Commissioni iniziali incluse. Slippage, uscita, finanziamento e imposte esclusi.']).map(note => <li key={note}>{note}</li>)}</ul></details>
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
    aria-label="Grafico P&L: oro a scadenza, cyan scenario, grigio oggi. Frecce sinistra e destra per leggere i punti."
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
  </svg><div className="vd-chart-legend"><span className="expiry">{result.same_expiry ? 'A scadenza' : 'Payoff unico non definito'}</span><span className="scenario">Scenario teorico</span><span className="today">Oggi teorico</span><span>Prezzo sottostante ({result.currency})</span></div>
  <div className="vd-chart-reading" aria-live="polite">{hoverRow ? <>Prezzo <b>{volNumber(hoverRow.price)}</b><span>P&L scenario <b>{volNumber(hoverRow.scenario)}</b></span><span>P&L a scadenza <b>{volNumber(hoverRow.expiry)}</b></span></> : 'Passa sul grafico o usa le frecce per confrontare i risultati allo stesso prezzo.'}</div></div>;
}

function ScenarioHeatmap({ result }: { result: StrategyResult }) {
  const max = Math.max(1, ...result.heatmap.flatMap(r => r.cells.map(c => Math.abs(c.pnl))));
  return <div className="vd-heatmap"><h4>Se il prezzo cambia mentre passa il tempo</h4><p>P&L teorico con lo shock IV scelto. Colonne = prezzo; righe = giorni trascorsi.</p><div><table><thead><tr><th>Giorni</th>{result.heatmap[0]?.cells.map(c => <th key={c.price}>{volNumber(c.price, 1)}</th>)}</tr></thead><tbody>{result.heatmap.map((row, i) => <tr key={i}><th>{volNumber(row.elapsed_days, 1)}</th>{row.cells.map(c => <td key={c.price} style={{ backgroundColor: c.pnl >= 0 ? `rgba(45,177,161,${.07 + Math.abs(c.pnl)/max*.35})` : `rgba(220,102,124,${.07 + Math.abs(c.pnl)/max*.35})` }} title={`Prezzo ${volNumber(c.price)}, giorno ${volNumber(row.elapsed_days, 1)}: P&L ${volNumber(c.pnl)} ${result.currency}`}>{volNumber(c.pnl, 0)}</td>)}</tr>)}</tbody></table></div></div>;
}
