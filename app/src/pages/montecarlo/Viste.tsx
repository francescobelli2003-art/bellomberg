import type { ReactNode } from 'react';
import { useT } from '@/i18n/provider';
import type { MonteCarloResult } from '@/lib/api';
import { etichettaMotore, fmtEUR, fmtEUR2, fmtNum, fmtPct, fmtPctS, orizzonte } from './formato';

/* Le sezioni della plancia Monte Carlo (stile Nuova). Tutte presentazionali: leggono il
   payload così com'è, e dove un campo manca lo dichiarano col segnaposto della lingua. */

export const PKEYS = ['p95', 'p90', 'p75', 'p50', 'p25', 'p10', 'p5'] as const;

/** «?» con la spiegazione del termine: si legge al passaggio e col fuoco da tastiera. */
export function Aiuto({ testo }: { testo: string }) {
  return <span className="mc-tip" tabIndex={0} aria-label={testo}>?<span className="mc-tipbox" role="tooltip">{testo}</span></span>;
}

function Testa({ titolo, aiuto, nota, children }: { titolo: string; aiuto?: string; nota?: ReactNode; children?: ReactNode }) {
  return <div className="bbn-card-head"><h2>{titolo}</h2>{aiuto && <Aiuto testo={aiuto} />}{children}<span className="bbn-grow" />
    {nota != null && <span className="bbn-card-note">{nota}</span>}</div>;
}

const tono = (v: number | null | undefined) => v == null || !isFinite(v) ? '' : v > 0 ? 'mc-up' : v < 0 ? 'mc-down' : '';
function Pastiglia({ v }: { v: number | null | undefined }) {
  const c = v == null || !isFinite(v) || v === 0 ? 'is-piatto' : v > 0 ? 'is-su' : 'is-giu';
  return <span className={'bbn-pill ' + c}>{fmtPctS(v)}</span>;
}

export function Esito({ r }: { r: MonteCarloResult }) {
  const tr = useT();
  return <section className="bbn-card mc-esito">
    <Testa titolo={tr('montecarlo.esito')} nota={tr('montecarlo.horizonNote', { h: orizzonte(r.horizon_days) })} />
    <div className="mc-hero-lab">{tr('montecarlo.medianValue')} <Aiuto testo={tr('montecarlo.medianHelp')} /></div>
    <div className="mc-hero-val"><b className="num">{fmtEUR(r.percentiles_eur?.p50)}</b><Pastiglia v={r.median_return_pct} /></div>
    <div className="mc-hero-sub">{r.n_assets === 1 ? tr('montecarlo.startLineOne', { nav: fmtEUR2(r.base_nav_eur) }) : tr('montecarlo.startLine', { nav: fmtEUR2(r.base_nav_eur), n: r.n_assets ?? tr('montecarlo.na') })}</div>
    <div className="mc-tiles">
      <div className="mc-tile"><span>{tr('montecarlo.expected')} <Aiuto testo={tr('montecarlo.expectedHelp')} /></span>
        <b className={'num ' + tono(r.expected_return_pct)}>{fmtPctS(r.expected_return_pct)}</b></div>
      <div className="mc-tile"><span>{tr('montecarlo.vol')} <Aiuto testo={tr('montecarlo.volHelp')} /></span><b className="num">{fmtPct(r.stdev_pct)}</b></div>
      <div className="mc-tile"><span>Sharpe <Aiuto testo={tr('montecarlo.sharpeHelp')} /></span><b className="num">{fmtNum(r.sharpe_simulated, 2)}</b></div>
    </div>
    <div className="mc-dd-line"><span>{tr('montecarlo.ddMedian')}</span><b className="num">{fmtPct(r.max_drawdown_median_pct)}</b>
      <small>{tr('montecarlo.ddMedianNote')}</small></div>
  </section>;
}

export function Probabilita({ r }: { r: MonteCarloResult }) {
  const tr = useT();
  const righe: [string, number | undefined, 'bad' | 'good'][] = [
    [tr('montecarlo.probAny'), r.prob_negative_pct, 'bad'],
    [tr('montecarlo.probL10'), r.prob_loss_10pct, 'bad'],
    [tr('montecarlo.probL20'), r.prob_loss_20pct, 'bad'],
    [tr('montecarlo.probG10'), r.prob_gain_10pct, 'good'],
    [tr('montecarlo.probG20'), r.prob_gain_20pct, 'good'],
  ];
  return <section className="bbn-card mc-prob">
    <Testa titolo={tr('montecarlo.probTitle')} aiuto={tr('montecarlo.probHelp')} nota={tr('montecarlo.probOver', { n: fmtNum(r.n_sims, 0) })} />
    <div className="mc-probs">{righe.map(([l, v, c], i) => {
      const ok = v != null && isFinite(v);
      return <div key={l} className={'mc-prow' + (i === 3 ? ' is-sep' : '')} data-prob={i}>
        <span>{l}</span><b className={'num ' + (ok ? (c === 'bad' ? 'mc-down' : 'mc-up') : '')}>{ok ? fmtNum(v, 2) + '%' : tr('montecarlo.na')}</b>
        <div className="mc-pbar"><i className={'is-' + c} style={{ width: (ok ? Math.max(0, Math.min(100, v!)) : 0) + '%' }} /></div>
      </div>;
    })}</div>
  </section>;
}

export function Percentili({ r }: { r: MonteCarloResult }) {
  const tr = useT();
  return <section className="bbn-card mc-pct">
    <Testa titolo={tr('montecarlo.pctTitle')} nota={tr('montecarlo.pctNote')} />
    <table className="mc-ptab">
      <thead><tr><th>Percentile</th><th>{tr('montecarlo.colValue')}</th><th>{tr('montecarlo.colVsStart')}</th></tr></thead>
      <tbody>{PKEYS.map(k => {
        const ratio = r.percentiles_ratio?.[k];
        const d = ratio != null && isFinite(ratio) ? (ratio - 1) * 100 : null;
        return <tr key={k} className={k === 'p50' ? 'is-med' : undefined}>
          <td>{k === 'p50' ? tr('montecarlo.p50Label') : k.toUpperCase()}</td>
          <td className="num">{fmtEUR(r.percentiles_eur?.[k])}</td>
          <td className={'num ' + (k === 'p50' ? '' : tono(d))}>{fmtPctS(d)}</td>
        </tr>;
      })}</tbody>
    </table>
  </section>;
}

export function RischioCoda({ r }: { r: MonteCarloResult }) {
  const tr = useT();
  // L'euro: quello del payload quando c'è (ES), altrimenti la stessa perdita applicata al
  // NAV di partenza — una conversione lineare, non una stima nuova.
  const eur = (pct?: number | null, dato?: number | null) =>
    dato != null && isFinite(dato) ? dato : pct != null && isFinite(pct) && r.base_nav_eur != null ? r.base_nav_eur * pct / 100 : null;
  const celle: [string, string, number | undefined | null, number | null, string][] = [
    ['VaR 95%', tr('montecarlo.var95Help'), r.var_95_pct, eur(r.var_95_pct), tr('montecarlo.var95Sub')],
    ['VaR 99%', tr('montecarlo.var99Help'), r.var_99_pct, eur(r.var_99_pct), tr('montecarlo.var99Sub')],
    ['CF-VaR 99%', tr('montecarlo.cfHelp'), r.var_99_cornish_fisher_pct, eur(r.var_99_cornish_fisher_pct), tr('montecarlo.cfSub')],
    ['ES 95%', tr('montecarlo.es95Help'), r.es_95_pct, eur(r.es_95_pct, r.es_95_eur), tr('montecarlo.es95Sub')],
    ['ES 99%', tr('montecarlo.es99Help'), r.es_99_pct, eur(r.es_99_pct, r.es_99_eur), tr('montecarlo.es99Sub')],
    ['Drawdown P5', tr('montecarlo.ddP5Help'), r.max_drawdown_p5_pct, eur(r.max_drawdown_p5_pct), tr('montecarlo.ddP5Sub')],
  ];
  return <section className="bbn-card mc-risk">
    <Testa titolo={tr('montecarlo.riskTitle')} nota={tr('montecarlo.riskNote', { h: orizzonte(r.horizon_days) })} />
    <div className="mc-rtiles">{celle.map(([k, aiuto, v, e, sub]) =>
      <div className="mc-rt" key={k} data-metric={k}>
        <span>{k} <Aiuto testo={aiuto} /></span>
        <b className="num">{fmtPct(v)}</b>
        <small><em className="num">{fmtEUR(e)}</em> · {sub}</small>
      </div>)}</div>
  </section>;
}

export function DrawdownPercorso({ r }: { r: MonteCarloResult }) {
  const tr = useT();
  const righe: [string, string, number | undefined, boolean][] = [
    ['P95', tr('montecarlo.depthP95'), r.max_drawdown_p95_pct, false],
    [tr('montecarlo.depthMedian'), tr('montecarlo.depthMedianSub'), r.max_drawdown_median_pct, true],
    ['P5', tr('montecarlo.depthP5'), r.max_drawdown_p5_pct, false],
  ];
  const peggiore = Math.max(40, ...righe.map(([, , v]) => (v != null && isFinite(v) ? Math.abs(v) * 1.08 : 0)));
  return <section className="bbn-card mc-depth">
    <Testa titolo={tr('montecarlo.depthTitle')} aiuto={tr('montecarlo.depthHelp')} nota={tr('montecarlo.depthNote')} />
    <div className="mc-ddrows">{righe.map(([l, sub, v, med]) => {
      const ok = v != null && isFinite(v);
      return <div key={l} className={'mc-ddr' + (med ? ' is-med' : '')}>
        <span>{l}<small>{sub}</small></span>
        <div className="mc-ddbar"><i style={{ width: (ok ? Math.abs(v!) / peggiore * 100 : 0) + '%' }} /></div>
        <b className="num">{fmtPct(v)}</b>
      </div>;
    })}</div>
  </section>;
}

export function NoteMotore({ r, drift, stress }: { r: MonteCarloResult; drift: string; stress: string }) {
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
    righe.push({ testa: tr('montecarlo.noteReplay'),
      corpo: tr('montecarlo.noteReplayBody', { loss: fmtPct(meta.window_loss_pct), days: meta.replaced_days ?? tr('montecarlo.na'), eur: fmtEUR(meta.window_loss_eur) }) + proxy });
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
