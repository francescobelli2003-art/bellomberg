// Payoff chart of the option builder (09/10/2026, Opus 5.5). Draws ONLY the engine's curve points:
// the crosshair snaps to the nearest computed price and reads the engine's exact values there.
// 10/10/2026 (Opus 5.5), impianto «Ticket»: profit zone between the expiry curve and zero, loss
// zone below zero, breakeven and spot lines labelled on top; extremes live in the key figures, so
// the plot no longer repeats them. Today is always drawn; a scenario (days or IV moved) adds its curve.
import { useMemo, useRef, useState } from 'react';
import { t as tr } from '@/i18n/t';
import { useBox } from '@/lib/useBox';
import { nearestPoint, type EngineResult } from '@/lib/option-builder';
import { useFormat } from './useFormat';

type Props = { result: EngineResult; strikes: number[]; spot: number; scenarioSpot: number; elapsed: number; ivPts: number; freeze?: boolean };

function niceTicks(lo: number, hi: number, count: number): number[] {
  const span = hi - lo; if (!(span > 0)) return [lo];
  const raw = span / count, mag = 10 ** Math.floor(Math.log10(raw)), norm = raw / mag;
  const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
  return out;
}

export default function PayoffChart({ result, strikes, spot, scenarioSpot, elapsed, ivPts, freeze = false }: Props) {
  const f = useFormat();
  const [box, size] = useBox<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  // Display window around strikes, breakevens, spot and scenario spot (padded): only engine points
  // inside it are drawn and read; the extremes outside it stay in the key figures above the chart.
  // While the spot slider is dragged the window stays put (v2 review 10/10): the axis does not rescale
  // under the pointer; it re-centres on release.
  const frozen = useRef<[number, number] | null>(null);
  const rows = useMemo(() => {
    const focus = [spot, scenarioSpot, ...strikes, ...result.breakevens].filter(Number.isFinite);
    if (!focus.length) return result.curve;
    let a = Math.min(...focus), b = Math.max(...focus);
    const span = Math.max(b - a, Math.abs(spot) * .1);
    a -= span * .45; b += span * .45;
    if (freeze && frozen.current) [a, b] = frozen.current; else frozen.current = [a, b];
    const inside = result.curve.filter(r => r.price >= a && r.price <= b);
    return inside.length >= 2 ? inside : result.curve;
  }, [result.curve, result.breakevens, strikes.join(','), spot, scenarioSpot, freeze]);
  const W = Math.max(size.w || 0, 200), H = Math.max(size.h || 0, 200);
  const pad = { l: 58, r: 16, t: 34, b: 30 };
  const scenarioIsToday = elapsed === 0 && ivPts === 0;
  const geo = useMemo(() => {
    const minX = rows[0].price, maxX = rows[rows.length - 1].price;
    const values = rows.flatMap(r => [r.expiry ?? 0, r.today, ...(scenarioIsToday ? [] : [r.scenario])]);
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
  const yTicks = niceTicks(lo, hi, 5), xTicks = niceTicks(minX, maxX, Math.max(3, Math.floor((W - pad.l - pad.r) / 90)));
  const hoverRow = hover == null ? null : rows[hover];
  const inside = (p: number) => p >= minX && p <= maxX;
  const isModel = result.expiry_basis === 'model_first_expiry';
  const id = useMemo(() => 'obc' + Math.random().toString(36).slice(2, 8), []);
  const bottom = H - pad.b, zero = Math.min(Math.max(y(0), pad.t), bottom);

  // labels on top: spot and breakevens; neighbours closer than a label width go up one row
  const marks = [...(inside(spot) ? [{ v: spot, kind: 'spot' as const, text: tr('optionbuilder.spotLabel', { v: f.num(spot, 2) }) }] : []),
    ...result.breakevens.filter(inside).map(v => ({ v, kind: 'be' as const, text: f.num(v, 2) }))].sort((a, b) => a.v - b.v);
  let lastEnd = -Infinity, lastRow = 0;
  const placed = marks.map(m => {
    const w = m.text.length * 6.6 + 8, start = x(m.v) - w / 2;
    const row = start < lastEnd ? (lastRow ? 0 : 1) : 0;
    lastEnd = x(m.v) + w / 2; lastRow = row;
    return { ...m, row };
  });

  const pick = (clientX: number, el: SVGSVGElement) => {
    const b = el.getBoundingClientRect();
    const price = minX + (clientX - b.left - pad.l) / (W - pad.l - pad.r) * (maxX - minX);
    setHover(nearestPoint(rows, price));
  };
  const tipLeft = hoverRow ? x(hoverRow.price) > W * .62 : false;
  const expiryLabel = isModel ? tr('optionbuilder.curveFirstExpiry') : tr('optionbuilder.curveExpiry');
  const scenarioLabel = tr('optionbuilder.curveScenario', { d: f.num(elapsed, elapsed % 1 ? 2 : 0), v: f.signed(ivPts, 1) });

  return <div className="ob-chart">
    <div className="ob-legend" aria-hidden>
      <span className="is-expiry">{expiryLabel}</span>
      <span className={'is-today' + (scenarioIsToday ? '' : ' is-dim')}>{tr('optionbuilder.curveToday')}</span>
      {!scenarioIsToday && <span className="is-scenario">{scenarioLabel}</span>}
      {result.breakevens.length > 0 && <span className="is-be">{tr('optionbuilder.legendBe')}</span>}
    </div>
    <div className="ob-plot" ref={box}>
      <svg width={W} height={H} role="img" tabIndex={0} aria-label={tr('optionbuilder.chartAria')}
        onPointerMove={e => pick(e.clientX, e.currentTarget)} onPointerLeave={() => setHover(null)}
        onKeyDown={e => { if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault();
          setHover(v => Math.max(0, Math.min(rows.length - 1, (v ?? nearestPoint(rows, spot)) + (e.key === 'ArrowLeft' ? -1 : 1)))); } }}>
        <defs>
          <clipPath id={id + 'up'}><rect x={0} y={0} width={W} height={zero} /></clipPath>
        </defs>
        {zero < bottom && <rect className="ob-zone-dn" x={pad.l} y={zero} width={W - pad.l - pad.r} height={bottom - zero} />}
        {yTicks.map(v => <g key={'y' + v}><line className="ob-grid" x1={pad.l} x2={W - pad.r} y1={y(v)} y2={y(v)} />
          <text className="ob-tick" x={pad.l - 8} y={y(v) + 4} textAnchor="end">{f.signed(v, Math.abs(hi - lo) < 20 ? 1 : 0)}</text></g>)}
        {xTicks.map(v => <text key={'x' + v} className="ob-tick" x={x(v)} y={bottom + 20} textAnchor="middle">{f.num(v, maxX - minX < 20 ? 1 : 0)}</text>)}
        {area && <path className="ob-area-up" d={area} clipPath={`url(#${id}up)`} />}
        <line className="ob-zero" x1={pad.l} x2={W - pad.r} y1={y(0)} y2={y(0)} />
        {(result.breakeven_intervals || []).map((iv, i) => <line key={'z' + i} className="ob-zero-band" x1={x(Math.max(minX, iv.from))} x2={x(Math.min(maxX, iv.to ?? maxX))} y1={y(0)} y2={y(0)} />)}
        {placed.map(m => <g key={m.kind + m.v} className={m.kind === 'spot' ? 'ob-spot-mark' : 'ob-be-mark'} data-mark={m.kind}>
          <line x1={x(m.v)} x2={x(m.v)} y1={pad.t - 2} y2={bottom} />
          <text x={x(m.v)} y={pad.t - 8 - m.row * 13} textAnchor="middle">{m.text}</text></g>)}
        {scenarioSpot !== spot && inside(scenarioSpot) && <line className="ob-scn-line" x1={x(scenarioSpot)} x2={x(scenarioSpot)} y1={pad.t} y2={bottom} />}
        <path className={'ob-curve-today' + (scenarioIsToday ? '' : ' is-dim')} d={line(r => r.today)} />
        <path className="ob-curve-expiry" d={expiryPath} />
        {!scenarioIsToday && <path className="ob-curve-scenario" d={line(r => r.scenario)} />}
        {result.breakevens.filter(inside).map(v => <circle key={'be' + v} className="ob-be" cx={x(v)} cy={y(0)} r={3.5} />)}
        {result.unlimited_profit && <text className="ob-tail is-up" x={W - pad.r - 2} y={pad.t + 14} textAnchor="end">{tr('optionbuilder.tailUp')}</text>}
        {result.unlimited_loss && <text className="ob-tail is-down" x={W - pad.r - 2} y={bottom - 8} textAnchor="end" data-tail-loss={result.unlimited_loss_reason || undefined}>
          {result.unlimited_loss_reason === 'european_dividend' ? tr('optionbuilder.tailDownTheoretical') : tr('optionbuilder.tailDown')}</text>}
        {(!scenarioIsToday || scenarioSpot !== spot) && inside(scenarioSpot) && <circle className="ob-scn-dot" cx={x(scenarioSpot)} cy={y(result.scenario.pnl)} r={5} data-scenario-dot />}
        {/* v3: with the window frozen (or the scenario past the engine curve) the scenario point is not
            dropped: an arrow on the edge says where it is */}
        {(!scenarioIsToday || scenarioSpot !== spot) && !inside(scenarioSpot) && (() => {
          const right = scenarioSpot > maxX, ex = right ? W - pad.r - 1 : pad.l + 1;
          const ey = Math.min(Math.max(y(result.scenario.pnl), pad.t + 8), bottom - 8);
          return <g className="ob-scn-edge" data-scenario-edge={right ? 'right' : 'left'}>
            <path d={right ? `M${ex},${ey}l-10,-6v12z` : `M${ex},${ey}l10,-6v12z`} />
            <text x={right ? ex - 14 : ex + 14} y={ey + 4} textAnchor={right ? 'end' : 'start'}>{f.num(scenarioSpot, 2)}</text></g>;
        })()}
        {hoverRow && <g className="ob-cross">
          <line x1={x(hoverRow.price)} x2={x(hoverRow.price)} y1={pad.t} y2={bottom} />
          {hoverRow.expiry != null && <circle className="is-expiry" cx={x(hoverRow.price)} cy={y(hoverRow.expiry)} r={4} />}
          <circle className="is-today" cx={x(hoverRow.price)} cy={y(hoverRow.today)} r={4} />
          {!scenarioIsToday && <circle className="is-scenario" cx={x(hoverRow.price)} cy={y(hoverRow.scenario)} r={4.5} />}
        </g>}
      </svg>
      {hoverRow && <div className={'ob-tip' + (tipLeft ? ' is-left' : '')} style={{ left: x(hoverRow.price) + (tipLeft ? -12 : 12), top: pad.t + 6 }} role="status" data-tip>
        <div className="ob-tip-head"><b className="ob-num">{f.num(hoverRow.price, 2)}</b><span>{f.signed((hoverRow.price / spot - 1) * 100, 1)}% {tr('optionbuilder.hoverMove')}</span></div>
        <dl>
          <div className="is-expiry"><dt>{expiryLabel}</dt><dd className="ob-num">{f.signed(hoverRow.expiry, 2)}</dd></div>
          <div className="is-today"><dt>{tr('optionbuilder.curveToday')}</dt><dd className="ob-num">{f.signed(hoverRow.today, 2)}</dd></div>
          {!scenarioIsToday && <div className="is-scenario"><dt>{tr('optionbuilder.scenario')}</dt><dd className="ob-num">{f.signed(hoverRow.scenario, 2)}</dd></div>}
        </dl>
      </div>}
    </div>
  </div>;
}
