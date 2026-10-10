// Payoff chart of the option builder (09/10/2026, Opus 5.5). Draws ONLY the engine's curve points:
// the crosshair snaps to the nearest computed price and reads the engine's exact values there.
import { useMemo, useState } from 'react';
import { t as tr } from '@/i18n/t';
import { useBox } from '@/lib/useBox';
import { nearestPoint, returnOnRisk, type EngineResult } from '@/lib/option-builder';
import { useFormat } from './useFormat';

type Props = { result: EngineResult; strikes: number[]; spot: number; scenarioSpot: number; elapsed: number; ivPts: number };

function niceTicks(lo: number, hi: number, count: number): number[] {
  const span = hi - lo; if (!(span > 0)) return [lo];
  const raw = span / count, mag = 10 ** Math.floor(Math.log10(raw)), norm = raw / mag;
  const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
  return out;
}

export default function PayoffChart({ result, strikes, spot, scenarioSpot, elapsed, ivPts }: Props) {
  const f = useFormat();
  const [box, size] = useBox<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const rows = result.curve;
  const W = Math.max(size.w, 320), H = Math.max(size.h, 260);
  const pad = { l: 64, r: 18, t: 30, b: 40 };
  const scenarioIsToday = elapsed === 0 && ivPts === 0;
  const geo = useMemo(() => {
    const minX = rows[0].price, maxX = rows[rows.length - 1].price;
    const values = rows.flatMap(r => [r.expiry ?? 0, r.scenario, ...(scenarioIsToday ? [] : [r.today])]);
    let lo = Math.min(0, ...values), hi = Math.max(0, ...values);
    const span = hi - lo || 1; lo -= span * .08; hi += span * .1;
    const x = (p: number) => pad.l + (p - minX) / (maxX - minX) * (W - pad.l - pad.r);
    const y = (v: number) => pad.t + (hi - v) / (hi - lo) * (H - pad.t - pad.b);
    return { minX, maxX, lo, hi, x, y };
  }, [rows, W, H, scenarioIsToday]);
  const { x, y, minX, maxX, lo, hi } = geo;
  const line = (pick: (r: EngineResult['curve'][number]) => number | null) => rows.filter(r => pick(r) != null)
    .map((r, i) => `${i ? 'L' : 'M'}${x(r.price).toFixed(1)},${y(pick(r) as number).toFixed(1)}`).join('');
  const expiryPath = line(r => r.expiry);
  const area = expiryPath ? `${expiryPath}L${x(maxX).toFixed(1)},${y(0).toFixed(1)}L${x(minX).toFixed(1)},${y(0).toFixed(1)}Z` : '';
  const yTicks = niceTicks(lo, hi, 5), xTicks = niceTicks(minX, maxX, Math.max(4, Math.floor((W - pad.l - pad.r) / 90)));
  const sigma = result.probability_of_profit;
  const band = sigma ? [spot * Math.exp(-sigma.sigma * Math.sqrt(sigma.horizon_days / 365)), spot * Math.exp(sigma.sigma * Math.sqrt(sigma.horizon_days / 365))] : null;
  const hoverRow = hover == null ? null : rows[hover];
  const ror = returnOnRisk(result);
  const inside = (p: number) => p >= minX && p <= maxX;
  const isModel = result.expiry_basis === 'model_first_expiry';
  const id = useMemo(() => 'obc' + Math.random().toString(36).slice(2, 8), []);

  const pick = (clientX: number, el: SVGSVGElement) => {
    const b = el.getBoundingClientRect();
    const price = minX + (clientX - b.left - pad.l) / (W - pad.l - pad.r) * (maxX - minX);
    setHover(nearestPoint(rows, price));
  };
  const maxP = result.unlimited_profit ? null : result.max_profit, maxL = result.unlimited_loss ? null : result.max_loss;
  const tipLeft = hoverRow ? x(hoverRow.price) > W * .62 : false;

  return <div className="ob-chart">
    <div className="ob-legend" aria-hidden>
      <span className="is-expiry">{isModel ? tr('optionbuilder.curveFirstExpiry') : tr('optionbuilder.curveExpiry')}</span>
      <span className="is-scenario">{scenarioIsToday ? tr('optionbuilder.curveToday') : tr('optionbuilder.curveScenario', { d: f.num(elapsed, elapsed % 1 ? 2 : 0), v: f.signed(ivPts, 1) })}</span>
      {!scenarioIsToday && <span className="is-today">{tr('optionbuilder.curveToday')}</span>}
      {band && <span className="is-band">{tr('optionbuilder.sigmaBand')}</span>}
    </div>
    <div className="ob-plot" ref={box}>
      <svg width={W} height={H} role="img" tabIndex={0} aria-label={tr('optionbuilder.chartAria')}
        onPointerMove={e => pick(e.clientX, e.currentTarget)} onPointerLeave={() => setHover(null)}
        onKeyDown={e => { if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault();
          setHover(v => Math.max(0, Math.min(rows.length - 1, (v ?? nearestPoint(rows, spot)) + (e.key === 'ArrowLeft' ? -1 : 1)))); } }}>
        <defs>
          <clipPath id={id + 'up'}><rect x={0} y={0} width={W} height={Math.max(0, y(0))} /></clipPath>
          <clipPath id={id + 'dn'}><rect x={0} y={y(0)} width={W} height={Math.max(0, H - y(0))} /></clipPath>
        </defs>
        {band && <rect className="ob-band" x={x(Math.max(minX, band[0]))} y={pad.t} width={Math.max(0, x(Math.min(maxX, band[1])) - x(Math.max(minX, band[0])))} height={H - pad.t - pad.b} />}
        {yTicks.map(v => <g key={'y' + v}><line className="ob-grid" x1={pad.l} x2={W - pad.r} y1={y(v)} y2={y(v)} />
          <text className="ob-tick" x={pad.l - 8} y={y(v) + 4} textAnchor="end">{f.signed(v, Math.abs(hi - lo) < 20 ? 1 : 0)}</text></g>)}
        {xTicks.map(v => <text key={'x' + v} className="ob-tick" x={x(v)} y={H - pad.b + 18} textAnchor="middle">{f.num(v, maxX - minX < 20 ? 1 : 0)}</text>)}
        {[...new Set(strikes)].filter(inside).sort((a, b) => a - b).map((k, i, all) => {
          const lift = i > 0 && x(k) - x(all[i - 1]) < 46 && i % 2 === 1 ? 13 : 0;
          return <g key={'k' + k} className="ob-strike">
            <line x1={x(k)} x2={x(k)} y1={H - pad.b} y2={H - pad.b + 5} /><text x={x(k)} y={H - pad.b - 5 - lift} textAnchor="middle">{'K ' + f.num(k, k % 1 ? 1 : 0)}</text></g>; })}
        <text className="ob-axis-title" x={W - pad.r} y={H - 6} textAnchor="end">{tr('optionbuilder.axisPrice')}</text>
        <text className="ob-axis-title" x={8} y={12}>{tr('optionbuilder.axisPnl')}</text>
        {area && <path className="ob-area-up" d={area} clipPath={`url(#${id}up)`} />}
        {area && <path className="ob-area-dn" d={area} clipPath={`url(#${id}dn)`} />}
        <line className="ob-zero" x1={pad.l} x2={W - pad.r} y1={y(0)} y2={y(0)} />
        {(result.breakeven_intervals || []).map((iv, i) => <line key={'z' + i} className="ob-zero-band" x1={x(Math.max(minX, iv.from))} x2={x(Math.min(maxX, iv.to ?? maxX))} y1={y(0)} y2={y(0)} />)}
        {maxP != null && maxP > lo && maxP < hi && <g><line className="ob-limit is-up" x1={pad.l} x2={W - pad.r} y1={y(maxP)} y2={y(maxP)} />
          <text className="ob-limit-label is-up" x={W - pad.r - 4} y={y(maxP) - 5} textAnchor="end">{tr('optionbuilder.maxProfitLine', { v: f.signed(maxP, 0) })}</text></g>}
        {maxL != null && -maxL > lo && -maxL < hi && <g><line className="ob-limit is-down" x1={pad.l} x2={W - pad.r} y1={y(-maxL)} y2={y(-maxL)} />
          <text className="ob-limit-label is-down" x={W - pad.r - 4} y={y(-maxL) + 14} textAnchor="end">{tr('optionbuilder.maxLossLine', { v: f.signed(-maxL, 0) })}</text></g>}
        {inside(spot) && <g><line className="ob-spot-line" x1={x(spot)} x2={x(spot)} y1={pad.t} y2={H - pad.b} />
          <text className="ob-spot-label" x={x(spot) + 4} y={pad.t + 10}>{tr('optionbuilder.spotLabel', { v: f.num(spot, 2) })}</text></g>}
        {scenarioSpot !== spot && inside(scenarioSpot) && <line className="ob-scn-line" x1={x(scenarioSpot)} x2={x(scenarioSpot)} y1={pad.t} y2={H - pad.b} />}
        {!scenarioIsToday && <path className="ob-curve-today" d={line(r => r.today)} />}
        <path className="ob-curve-expiry" d={expiryPath} />
        <path className="ob-curve-scenario" d={line(r => r.scenario)} />
        {result.breakevens.filter(inside).map((v, i, all) => {
          // neighbours closer than a label width alternate above/below the zero line
          const crowded = all.length > 1 && all.some((w, j) => j !== i && Math.abs(x(w) - x(v)) < 110);
          const below = crowded ? i % 2 === 0 : y(0) < H - pad.b - 24;
          return <g key={'be' + v}>
            <circle className="ob-be" cx={x(v)} cy={y(0)} r={4.5} />
            <text className="ob-be-label" x={x(v)} y={y(0) + (below ? 18 : -10)} textAnchor="middle">{tr('optionbuilder.beLabel', { v: f.num(v, 2) })}</text></g>; })}
        {result.unlimited_profit && <text className="ob-tail is-up" x={W - pad.r - 2} y={pad.t + 12} textAnchor="end">{tr('optionbuilder.tailUp')}</text>}
        {result.unlimited_loss && <text className="ob-tail is-down" x={W - pad.r - 2} y={H - pad.b - 8} textAnchor="end" data-tail-loss={result.unlimited_loss_reason || undefined}>
          {result.unlimited_loss_reason === 'european_dividend' ? tr('optionbuilder.tailDownTheoretical') : tr('optionbuilder.tailDown')}</text>}
        {inside(scenarioSpot) && <circle className="ob-scn-dot" cx={x(scenarioSpot)} cy={y(result.scenario.pnl)} r={5} />}
        {hoverRow && <g className="ob-cross">
          <line x1={x(hoverRow.price)} x2={x(hoverRow.price)} y1={pad.t} y2={H - pad.b} />
          {hoverRow.expiry != null && <circle className="is-expiry" cx={x(hoverRow.price)} cy={y(hoverRow.expiry)} r={4} />}
          {!scenarioIsToday && <circle className="is-today" cx={x(hoverRow.price)} cy={y(hoverRow.today)} r={3.5} />}
          <circle className="is-scenario" cx={x(hoverRow.price)} cy={y(hoverRow.scenario)} r={4.5} />
        </g>}
      </svg>
      {hoverRow && <div className={'ob-tip' + (tipLeft ? ' is-left' : '')} style={{ left: x(hoverRow.price) + (tipLeft ? -12 : 12), top: pad.t + 6 }} role="status">
        <div className="ob-tip-head"><b className="ob-num">{f.num(hoverRow.price, 2)}</b><span>{f.signed((hoverRow.price / spot - 1) * 100, 1)}% {tr('optionbuilder.hoverMove')}</span></div>
        <dl>
          <div className="is-expiry"><dt>{isModel ? tr('optionbuilder.curveFirstExpiry') : tr('optionbuilder.curveExpiry')}</dt><dd className="ob-num">{f.signed(hoverRow.expiry, 2)}</dd></div>
          <div className="is-scenario"><dt>{scenarioIsToday ? tr('optionbuilder.curveToday') : tr('optionbuilder.scenario')}</dt><dd className="ob-num">{f.signed(hoverRow.scenario, 2)}</dd></div>
          {!scenarioIsToday && <div className="is-today"><dt>{tr('optionbuilder.curveToday')}</dt><dd className="ob-num">{f.signed(hoverRow.today, 2)}</dd></div>}
          {ror != null && result.max_loss ? <div><dt>{tr('optionbuilder.hoverRor')}</dt><dd className="ob-num">{f.signed((hoverRow.expiry ?? 0) / result.max_loss * 100, 0)}%</dd></div> : null}
        </dl>
      </div>}
    </div>
    <p className="ob-chart-hint">{tr('optionbuilder.hoverHint')}</p>
  </div>;
}
