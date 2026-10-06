import type { ReactNode } from 'react';
import { useT } from '@/i18n/provider';
import type { MonteCarloResult } from '@/lib/api';
import { etichettaMotore, fmtEUR, fmtEUR2, fmtEURS, fmtInt, fmtNum, fmtPct, fmtPctS, naDi, orizzonte } from './formato';

/* Le sezioni della plancia Monte Carlo (stile Nuova). Tutte presentazionali: leggono il
   payload così com'è, e dove un campo manca lo dichiarano col segnaposto della lingua.
   Ogni tono/colore passa da un test null-safe: in JS `null >= 0` è true, e una metrica
   non applicabile (scenario deterministico) non deve uscire verde. */

export const PKEYS = ['p95', 'p90', 'p75', 'p50', 'p25', 'p10', 'p5'] as const;

/** «?» con la spiegazione del termine: si legge al passaggio e col fuoco da tastiera. */
export function Aiuto({ testo }: { testo: string }) {
  return <span className="mc-tip" tabIndex={0} aria-label={testo}>?<span className="mc-tipbox" role="tooltip">{testo}</span></span>;
}

function Testa({ titolo, aiuto, nota, children }: { titolo: string; aiuto?: string; nota?: ReactNode; children?: ReactNode }) {
  return <div className="bbn-card-head"><h2>{titolo}</h2>{aiuto && <Aiuto testo={aiuto} />}{children}<span className="bbn-grow" />
    {nota != null && <span className="bbn-card-note">{nota}</span>}</div>;
}

/** Classe del valore non applicabile: il segnaposto lungo va a capo, in grigio, invece di
 *  uscire dalla piastrella. Solo dove c'è `na` (metrica elencata dal motore). */
const nac = (v: number | null | undefined, na?: string) => na && (v == null || !isFinite(v)) ? ' is-na' : '';
const tono = (v: number | null | undefined) => v == null || !isFinite(v) ? '' : v > 0 ? 'mc-up' : v < 0 ? 'mc-down' : '';
function Pastiglia({ v, na }: { v: number | null | undefined; na?: string }) {
  const c = v == null || !isFinite(v) || v === 0 ? 'is-piatto' : v > 0 ? 'is-su' : 'is-giu';
  return <span className={'bbn-pill ' + c}>{fmtPctS(v, na)}</span>;
}

export function Esito({ r }: { r: MonteCarloResult }) {
  const tr = useT();
  return <section className="bbn-card mc-esito">
    <Testa titolo={tr('montecarlo.esito')} nota={tr('montecarlo.horizonNote', { h: orizzonte(r.horizon_days) })} />
    <div className="mc-hero-lab">{tr('montecarlo.medianValue')} <Aiuto testo={tr('montecarlo.medianHelp')} /></div>
    <div className="mc-hero-val"><b className={'num' + nac(r.percentiles_eur?.p50, naDi(r, 'percentiles_eur'))}>{fmtEUR(r.percentiles_eur?.p50, naDi(r, 'percentiles_eur'))}</b><Pastiglia v={r.median_return_pct} na={naDi(r, 'median_return_pct')} /></div>
    <div className="mc-hero-sub">{r.n_assets === 1 ? tr('montecarlo.startLineOne', { nav: fmtEUR2(r.base_nav_eur) }) : tr('montecarlo.startLine', { nav: fmtEUR2(r.base_nav_eur), n: r.n_assets ?? tr('montecarlo.na') })}</div>
    <div className="mc-tiles">
      <div className="mc-tile"><span>{tr('montecarlo.expected')} <Aiuto testo={tr('montecarlo.expectedHelp')} /></span>
        <b className={'num ' + tono(r.expected_return_pct) + nac(r.expected_return_pct, naDi(r, 'expected_return_pct'))}>{fmtPctS(r.expected_return_pct, naDi(r, 'expected_return_pct'))}</b></div>
      <div className="mc-tile"><span>{tr('montecarlo.vol')} <Aiuto testo={tr('montecarlo.volHelp')} /></span><b className={'num' + nac(r.stdev_pct, naDi(r, 'stdev_pct'))}>{fmtPct(r.stdev_pct, naDi(r, 'stdev_pct'))}</b></div>
      <div className="mc-tile"><span>Sharpe <Aiuto testo={tr('montecarlo.sharpeHelp')} /></span><b className={'num' + nac(r.sharpe_simulated, naDi(r, 'sharpe_simulated'))}>{fmtNum(r.sharpe_simulated, 2, naDi(r, 'sharpe_simulated'))}</b></div>
    </div>
    <div className="mc-dd-line"><span>{tr('montecarlo.ddMedian')}</span><b className={'num' + nac(r.max_drawdown_median_pct, naDi(r, 'max_drawdown_median_pct'))}>{fmtPct(r.max_drawdown_median_pct, naDi(r, 'max_drawdown_median_pct'))}</b>
      <small>{tr('montecarlo.ddMedianNote')}</small></div>
  </section>;
}

export function Probabilita({ r }: { r: MonteCarloResult }) {
  const tr = useT();
  const righe: [string, number | null | undefined, 'bad' | 'good', string][] = [
    [tr('montecarlo.probAny'), r.prob_negative_pct, 'bad', 'prob_negative_pct'],
    [tr('montecarlo.probL10'), r.prob_loss_10pct, 'bad', 'prob_loss_10pct'],
    [tr('montecarlo.probL20'), r.prob_loss_20pct, 'bad', 'prob_loss_20pct'],
    [tr('montecarlo.probG10'), r.prob_gain_10pct, 'good', 'prob_gain_10pct'],
    [tr('montecarlo.probG20'), r.prob_gain_20pct, 'good', 'prob_gain_20pct'],
  ];
  return <section className="bbn-card mc-prob">
    <Testa titolo={tr('montecarlo.probTitle')} aiuto={tr('montecarlo.probHelp')} nota={tr('montecarlo.probOver', { n: fmtNum(r.n_sims, 0) })} />
    <div className="mc-probs">{righe.map(([l, v, c, campo], i) => {
      const ok = v != null && isFinite(v);
      return <div key={l} className={'mc-prow' + (i === 3 ? ' is-sep' : '')} data-prob={i}>
        <span>{l}</span><b className={'num ' + (ok ? (c === 'bad' ? 'mc-down' : 'mc-up') : '') + nac(v, naDi(r, campo))}>{ok ? fmtNum(v, 2) + '%' : naDi(r, campo) ?? tr('montecarlo.na')}</b>
        <div className="mc-pbar"><i className={'is-' + c} style={{ width: (ok ? Math.max(0, Math.min(100, v!)) : 0) + '%' }} /></div>
      </div>;
    })}</div>
  </section>;
}

export function Percentili({ r }: { r: MonteCarloResult }) {
  const tr = useT();
  const naE = naDi(r, 'percentiles_eur'), naR = naDi(r, 'percentiles_ratio');
  return <section className="bbn-card mc-pct">
    <Testa titolo={tr('montecarlo.pctTitle')} nota={tr('montecarlo.pctNote')} />
    <table className="mc-ptab">
      <thead><tr><th>Percentile</th><th>{tr('montecarlo.colValue')}</th><th>{tr('montecarlo.colVsStart')}</th></tr></thead>
      <tbody>{PKEYS.map(k => {
        const ratio = r.percentiles_ratio?.[k];
        const d = ratio != null && isFinite(ratio) ? (ratio - 1) * 100 : null;
        return <tr key={k} className={k === 'p50' ? 'is-med' : undefined}>
          <td>{k === 'p50' ? tr('montecarlo.p50Label') : k.toUpperCase()}</td>
          <td className={'num' + nac(r.percentiles_eur?.[k], naE)}>{fmtEUR(r.percentiles_eur?.[k], naE)}</td>
          <td className={'num ' + (k === 'p50' ? '' : tono(d)) + nac(d, naR)}>{fmtPctS(d, naR)}</td>
        </tr>;
      })}</tbody>
    </table>
  </section>;
}

export function RischioCoda({ r }: { r: MonteCarloResult }) {
  const tr = useT();
  // L'euro. ES: SOLO quello del payload (es_*_eur); se manca è un buco dichiarato, mai
  // ricalcolato zitto (review PR #12). VaR: il payload non porta un campo in euro, e la
  // perdita % scalata sul NAV di partenza è la stessa convenzione con cui il motore scrive
  // es_*_eur («i campi *_eur scalano sul NAV EUR»). Drawdown: si misura dal PICCO, non
  // dalla partenza — un euro sul NAV iniziale sarebbe un numero sbagliato, quindi niente euro.
  const scala = (pct?: number | null) =>
    pct != null && isFinite(pct) && r.base_nav_eur != null && isFinite(r.base_nav_eur) ? r.base_nav_eur * pct / 100 : null;
  // ultimi due: il campo del payload della % e quello dell'euro, per il segnaposto (naDi)
  const celle: [string, string, number | undefined | null, number | null | undefined, string, string, string][] = [
    ['VaR 95%', tr('montecarlo.var95Help'), r.var_95_pct, scala(r.var_95_pct), tr('montecarlo.var95Sub'), 'var_95_pct', 'var_95_pct'],
    ['VaR 99%', tr('montecarlo.var99Help'), r.var_99_pct, scala(r.var_99_pct), tr('montecarlo.var99Sub'), 'var_99_pct', 'var_99_pct'],
    ['CF-VaR 99%', tr('montecarlo.cfHelp'), r.var_99_cornish_fisher_pct, scala(r.var_99_cornish_fisher_pct), tr('montecarlo.cfSub'), 'var_99_cornish_fisher_pct', 'var_99_cornish_fisher_pct'],
    ['ES 95%', tr('montecarlo.es95Help'), r.es_95_pct, r.es_95_eur ?? null, tr('montecarlo.es95Sub'), 'es_95_pct', 'es_95_eur'],
    ['ES 99%', tr('montecarlo.es99Help'), r.es_99_pct, r.es_99_eur ?? null, tr('montecarlo.es99Sub'), 'es_99_pct', 'es_99_eur'],
    // undefined = la cella non ha un euro per costruzione (≠ null = buco da dichiarare)
    ['Drawdown P5', tr('montecarlo.ddP5Help'), r.max_drawdown_p5_pct, undefined, tr('montecarlo.ddP5Sub'), 'max_drawdown_p5_pct', ''],
  ];
  return <section className="bbn-card mc-risk">
    <Testa titolo={tr('montecarlo.riskTitle')} nota={tr('montecarlo.riskNote', { h: orizzonte(r.horizon_days) })} />
    <div className="mc-rtiles">{celle.map(([k, aiuto, v, e, sub, campo, campoE]) =>
      <div className="mc-rt" key={k} data-metric={k}>
        <span>{k} <Aiuto testo={aiuto} /></span>
        <b className={'num' + nac(v, naDi(r, campo))}>{fmtPct(v, naDi(r, campo))}</b>
        <small>{e !== undefined && <><em className={'num' + nac(e, naDi(r, campoE))}>{fmtEUR(e, naDi(r, campoE))}</em> · </>}{sub}</small>
      </div>)}</div>
  </section>;
}

export function DrawdownPercorso({ r }: { r: MonteCarloResult }) {
  const tr = useT();
  const righe: [string, string, number | null | undefined, boolean, string][] = [
    ['P95', tr('montecarlo.depthP95'), r.max_drawdown_p95_pct, false, 'max_drawdown_p95_pct'],
    [tr('montecarlo.depthMedian'), tr('montecarlo.depthMedianSub'), r.max_drawdown_median_pct, true, 'max_drawdown_median_pct'],
    ['P5', tr('montecarlo.depthP5'), r.max_drawdown_p5_pct, false, 'max_drawdown_p5_pct'],
  ];
  const peggiore = Math.max(40, ...righe.map(([, , v]) => (v != null && isFinite(v) ? Math.abs(v) * 1.08 : 0)));
  return <section className="bbn-card mc-depth">
    <Testa titolo={tr('montecarlo.depthTitle')} aiuto={tr('montecarlo.depthHelp')} nota={tr('montecarlo.depthNote')} />
    <div className="mc-ddrows">{righe.map(([l, sub, v, med, campo]) => {
      const ok = v != null && isFinite(v);
      return <div key={l} className={'mc-ddr' + (med ? ' is-med' : '')}>
        <span>{l}<small>{sub}</small></span>
        <div className="mc-ddbar"><i style={{ width: (ok ? Math.abs(v!) / peggiore * 100 : 0) + '%' }} /></div>
        <b className={'num' + nac(v, naDi(r, campo))}>{fmtPct(v, naDi(r, campo))}</b>
      </div>;
    })}</div>
  </section>;
}

/** Scenario deterministico (stress_nature = "deterministic"): il replay storico copre tutto
 *  l'orizzonte, ogni simulazione è la stessa traiettoria e il numero da leggere è l'esito
 *  dello scenario, non una media né una probabilità. `scenario_loss_pct/eur` hanno il SEGNO
 *  (negativo = perdita): il colore lo segue, e un campo assente resta «n.d.», mai zero. */
export function ScenarioDeterministico({ r }: { r: MonteCarloResult }) {
  const tr = useT();
  const ds = r.deterministic_scenario;
  if (!ds) return null;
  return <section className="bbn-card mc-det" data-deterministic>
    <Testa titolo={`${ds.label} · ${etichettaMotore('stress', ds.scenario || r.stress_scenario)}`} aiuto={tr('montecarlo.detHelp')}
      nota={tr('montecarlo.detNote')} />
    <p className="mc-det-lead">{r.stress_nature_label || tr('montecarlo.natureDetFallback')}</p>
    <div className="mc-det-row">
      <div className="mc-tile" data-det="return"><span>{tr('montecarlo.detReturn')} <Aiuto testo={tr('montecarlo.detReturnHelp')} /></span>
        <b className={'num ' + tono(ds.scenario_loss_pct)}>{fmtPctS(ds.scenario_loss_pct)}</b>
        <small className={'num ' + tono(ds.scenario_loss_eur)}>{fmtEURS(ds.scenario_loss_eur)}</small></div>
      <div className="mc-tile" data-det="drawdown"><span>{tr('montecarlo.detMaxDd')} <Aiuto testo={tr('montecarlo.detMaxDdHelp')} /></span>
        <b className="num">{fmtPct(ds.scenario_max_drawdown_pct)}</b></div>
      <div className="mc-tile" data-det="days"><span>{tr('montecarlo.detDays')}</span>
        <b className="num">{tr('montecarlo.detDaysValue', { a: fmtInt(ds.replayed_days), b: fmtInt(ds.horizon_days) })}</b></div>
    </div>
    {ds.reason && <p className="mc-det-reason">{ds.reason}</p>}
  </section>;
}

/** `drift`/`stress`: i valori SPEDITI con la simulazione mostrata (non i selettori di
 *  adesso, che possono essere già cambiati); servono solo se il payload non li porta. */
export function NoteMotore({ r, drift, stress }: { r: MonteCarloResult; drift?: string; stress?: string }) {
  const tr = useT();
  const meta = r.stress_meta;
  const righe: { tono?: 'warn' | 'bad'; testa: string; corpo: string }[] = [{
    testa: tr('montecarlo.noteMethod'),
    corpo: tr('montecarlo.noteMethodBody', {
      method: r.method_description || etichettaMotore('method', r.method),
      drift: etichettaMotore('drift', r.drift_mode || drift), stress: etichettaMotore('stress', r.stress_scenario || stress),
      sims: fmtNum(r.n_sims, 0), years: r.lookback_years ?? tr('montecarlo.na'), obs: fmtNum(r.lookback_days_calibration, 0),
    }),
  }];
  if (r.calibration_note) righe.push({ tono: 'warn', testa: tr('montecarlo.noteCalib'), corpo: r.calibration_note });
  if (r.returns_basis) righe.push({ testa: tr('montecarlo.noteBasis'), corpo: r.returns_basis });
  if (r.stress_fallback) righe.push({ tono: 'bad', testa: tr('montecarlo.noteFallback'),
    corpo: tr('montecarlo.noteFallbackBody', { req: etichettaMotore('stress', r.stress_requested) }) + (meta?.fallback_reason ? ` — ${meta.fallback_reason}` : '') });
  if (meta?.window_loss_pct != null) {
    const proxy = meta.proxied && Object.keys(meta.proxied).length > 0
      ? tr('montecarlo.noteProxies', { list: Object.entries(meta.proxied).map(([t, p]) => `${t} (${p})`).join('; ') }) : '';
    // window_loss_* è un rendimento CON SEGNO (negativo = perdita): il «+» del guadagno si
    // scrive, altrimenti un replay chiuso in positivo si leggerebbe come una perdita
    righe.push({ testa: tr('montecarlo.noteReplay'),
      corpo: tr('montecarlo.noteReplayBody', { loss: fmtPctS(meta.window_loss_pct), days: meta.replaced_days ?? tr('montecarlo.na'), eur: fmtEURS(meta.window_loss_eur) }) + proxy });
  } else if (!r.stress_fallback && (r.stress_scenario || stress) === 'none') {
    righe.push({ testa: tr('montecarlo.noteReplay'), corpo: tr('montecarlo.noteNoStress') });
  }
  let n = 0;
  return <section className="bbn-card mc-notes">
    <Testa titolo={tr('montecarlo.notesTitle')} nota={tr('montecarlo.notesNote')} />
    <div className="mc-nlist">{righe.map(x =>
      <div key={x.testa} className={'mc-nrow' + (x.tono ? ' is-' + x.tono : '')} title={x.corpo}>
        <span className="mc-n">{x.tono === 'bad' ? '!' : ++n}</span>
        <span className="mc-nt"><b>{x.testa}</b> {x.corpo}</span>
      </div>)}</div>
  </section>;
}
