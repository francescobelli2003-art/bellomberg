// Payoff chart of the option builder (09/10/2026, Opus 5.5). Draws ONLY the engine's curve points:
// the crosshair snaps to the nearest computed price and reads the engine's exact values there.
// 10/10/2026 (Opus 5.5), impianto «Ticket»: profit zone between the expiry curve and zero, loss
// zone below zero, breakeven and spot lines labelled on top. Today is always drawn; a scenario
// (days or IV moved) adds its curve.
// 11/10/2026 (Opus 5.5), «grafico da desk» (richiesta PM): the chart is the protagonist of the page.
//   - expiry solid ink, today long-dashed accent, scenario dotted violet: colour AND line style;
//   - profit and loss filled between the expiry curve and zero, soft tints; a stronger loss tint
//     when the loss is unbounded;
//   - x-axis marks: strikes of the legs (ticks + «K…»), spot, scenario spot, breakevens with the
//     % from spot; max profit and max loss annotated on the plot (engine figures);
//   - ±1σ / ±2σ bands of the lognormal move at the first expiry (σ = ATM IV, declared by the caller);
//   - y axis in USD with abbreviated ticks (k) and a highlighted zero;
//   - crosshair (pointer or ← →): exact engine values + probability of ending above that price;
//   - x zoom: drag across the plot (or + / − on the keyboard), «Adatta» restores the first window.
import { useMemo, useRef, useState } from 'react';
import { t as tr } from '@/i18n/t';
import { useBox } from '@/lib/useBox';
import { lognormalAt, nearestPoint, probAbove, type EngineResult, type Lognormal } from '@/lib/option-builder';
import { useFormat } from './useFormat';

/** Distribution behind the bands and the crosshair probability; `label` declares σ and its source. */
export type ChartDistribution = Lognormal & { label: string; basis: string };

type Props = { result: EngineResult; strikes: number[]; spot: number; scenarioSpot: number; elapsed: number; ivPts: number; freeze?: boolean;
  dist?: ChartDistribution | null; theory?: boolean };

function niceTicks(lo: number, hi: number, count: number): number[] {
  const span = hi - lo; if (!(span > 0)) return [lo];
  const raw = span / count, mag = 10 ** Math.floor(Math.log10(raw)), norm = raw / mag;
  const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
  return out;
}

const DRAG_MIN_PX = 8;

export default function PayoffChart({ result, strikes, spot, scenarioSpot, elapsed, ivPts, freeze = false, dist = null, theory = false }: Props) {
  const f = useFormat();
  const [box, size] = useBox<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const [zoom, setZoom] = useState<[number, number] | null>(null);
  const [drag, setDrag] = useState<{ from: number; to: number } | null>(null);
  const bands = useMemo(() => dist && dist.sigma > 0 && dist.days > 0
    ? { m2: lognormalAt(dist, -2), m1: lognormalAt(dist, -1), p1: lognormalAt(dist, 1), p2: lognormalAt(dist, 2) } : null, [dist]);
  // Display window around strikes, breakevens, spot, scenario spot and the ±1σ band (padded): only
  // engine points inside it are drawn and read; the extremes outside it stay in the key figures.
  // While the spot slider is dragged the window stays put (v2 review 10/10); a zoom replaces it.
  const frozen = useRef<[number, number] | null>(null);
  const base = useMemo<[number, number]>(() => {
    // the ±1σ band joins the window only within ±15% of spot: a long-dated σ would flatten the payoff
    const band1 = bands ? [Math.max(bands.m1, spot * .85), Math.min(bands.p1, spot * 1.15)] : [];
    const focus = [spot, scenarioSpot, ...strikes, ...result.breakevens, ...band1].filter(Number.isFinite);
    if (!focus.length) return [result.curve[0].price, result.curve[result.curve.length - 1].price];
    let a = Math.min(...focus), b = Math.max(...focus);
    const span = Math.max(b - a, Math.abs(spot) * .1);
    a -= span * .3; b += span * .3;
    if (freeze && frozen.current) return frozen.current;
    frozen.current = [a, b];
    return [a, b];
  }, [result.breakevens, strikes.join(','), spot, scenarioSpot, freeze, bands]);
  const rows = useMemo(() => {
    const [a, b] = zoom || base;
    const inside = result.curve.filter(r => r.price >= a && r.price <= b);
    return inside.length >= 2 ? inside : result.curve;
  }, [result.curve, base, zoom]);
  const W = Math.max(size.w || 0, 200), H = Math.max(size.h || 0, 200);
  const pad = { l: 56, r: 18, t: 44, b: 50 };
  const scenarioIsToday = elapsed === 0 && ivPts === 0;
  const geo = useMemo(() => {
    const minX = rows[0].price, maxX = rows[rows.length - 1].price;
    const values = rows.flatMap(r => [r.expiry ?? 0, r.today, ...(scenarioIsToday ? [] : [r.scenario])]);
    let lo = Math.min(0, ...values), hi = Math.max(0, ...values);
    const span = hi - lo || 1; lo -= span * .1; hi += span * .12;
    const x = (p: number) => pad.l + (p - minX) / (maxX - minX) * (W - pad.l - pad.r);
    const y = (v: number) => pad.t + (hi - v) / (hi - lo) * (H - pad.t - pad.b);
    return { minX, maxX, lo, hi, x, y };
  }, [rows, W, H, scenarioIsToday]);
  const { x, y, minX, maxX, lo, hi } = geo;
  const priceAt = (px: number) => minX + (px - pad.l) / (W - pad.l - pad.r) * (maxX - minX);
  const line = (pick: (r: EngineResult['curve'][number]) => number | null) => rows.filter(r => pick(r) != null)
    .map((r, i) => `${i ? 'L' : 'M'}${x(r.price).toFixed(1)},${y(pick(r) as number).toFixed(1)}`).join('');
  const expiryPath = line(r => r.expiry);
  const area = expiryPath ? `${expiryPath}L${x(maxX).toFixed(1)},${y(0).toFixed(1)}L${x(minX).toFixed(1)},${y(0).toFixed(1)}Z` : '';
  const yTicks = niceTicks(lo, hi, 5), xTicks = niceTicks(minX, maxX, Math.max(3, Math.floor((W - pad.l - pad.r) / 90)));
  const yStep = yTicks.length > 1 ? Math.abs(yTicks[1] - yTicks[0]) : 1;
  const yLabel = (v: number) => Math.abs(v) >= 1000 ? f.signed(v / 1000, yStep < 1000 ? 1 : 0) + 'k' : f.signed(v, yStep < 1 ? 2 : yStep < 10 ? 1 : 0);
  const hoverRow = hover == null ? null : rows[Math.min(hover, rows.length - 1)];
  const inside = (p: number) => p >= minX && p <= maxX;
  const isModel = result.expiry_basis === 'model_first_expiry';
  const id = useMemo(() => 'obc' + Math.random().toString(36).slice(2, 8), []);
  const bottom = H - pad.b, zero = Math.min(Math.max(y(0), pad.t), bottom);
  const pctFromSpot = (v: number) => f.signed((v / spot - 1) * 100, 1) + '%';

  // labels on top: spot, scenario spot and breakevens (with % from spot); a label that would overlap
  // goes up one row (three rows at most)
  const scenarioMoved = scenarioSpot !== spot;
  const marks = [...(inside(spot) ? [{ v: spot, kind: 'spot' as const, text: tr('optionbuilder.spotLabel', { v: f.num(spot, 2) }) }] : []),
    ...(scenarioMoved && inside(scenarioSpot) ? [{ v: scenarioSpot, kind: 'scenario' as const, text: tr('optionbuilder.scenarioSpotLabel', { v: f.num(scenarioSpot, 2) }) }] : []),
    ...result.breakevens.filter(inside).map(v => ({ v, kind: 'be' as const, text: tr('optionbuilder.beMark', { v: f.num(v, 2), p: pctFromSpot(v) }) }))].sort((a, b) => a.v - b.v);
  const ends: number[] = [];
  const placed = marks.map(m => {
    const w = m.text.length * 6.4 + 10, start = x(m.v) - w / 2;
    let row = ends.findIndex(end => start > end);
    if (row < 0) row = Math.min(ends.length, 2);
    ends[row] = x(m.v) + w / 2;
    return { ...m, row };
  });
  // strike ticks on the x axis; a label closer than 34 px to the previous one is dropped (the tick stays)
  const strikeMarks = [...new Set(strikes)].filter(inside).sort((a, b) => a - b);
  let lastStrikeX = -Infinity;
  const strikeLabels = strikeMarks.map(k => { const show = x(k) - lastStrikeX >= 34; if (show) lastStrikeX = x(k); return { k, show }; });
  // engine extremes annotated on the plot (only when finite and inside the vertical range)
  const extremes = [
    ...(!result.unlimited_profit && result.max_profit != null && result.max_profit > 0 ? [{ kind: 'max-profit', v: result.max_profit, text: tr('optionbuilder.maxProfitLine', { v: f.signed(result.max_profit, 2) }) }] : []),
    ...(!result.unlimited_loss && result.max_loss != null && result.max_loss > 0 ? [{ kind: 'max-loss', v: -result.max_loss, text: tr('optionbuilder.maxLossLine', { v: f.signed(-result.max_loss, 2) }) }] : []),
  ].filter(e => e.v >= lo && e.v <= hi);

  const pick = (clientX: number, el: SVGSVGElement) => priceAt(clientX - el.getBoundingClientRect().left);
  const zoomBy = (factor: number) => {
    const c = hoverRow ? hoverRow.price : Math.min(Math.max(spot, minX), maxX);
    const half = (maxX - minX) * factor / 2;
    const first = result.curve[0].price, last = result.curve[result.curve.length - 1].price;
    const a = Math.max(first, c - half), b = Math.min(last, c + half);
    if (b - a > 0) { setZoom([a, b]); setHover(null); }
  };
  const tipLeft = hoverRow ? x(hoverRow.price) > W * .62 : false;
  const expiryLabel = isModel ? tr('optionbuilder.curveFirstExpiry') : tr('optionbuilder.curveExpiry');
  const scenarioLabel = tr('optionbuilder.curveScenario', { d: f.num(elapsed, elapsed % 1 ? 2 : 0), v: f.signed(ivPts, 1) });
  const above = hoverRow && dist ? probAbove(dist, hoverRow.price) : null;
  const band = (a: number, b: number, k: 1 | 2) => {
    const x1 = x(Math.max(a, minX)), x2 = x(Math.min(b, maxX));
    return x2 > x1 ? <rect className={'ob-band is-' + k} x={x1} y={pad.t} width={x2 - x1} height={bottom - pad.t} data-band={k} /> : null;
  };
  const bandLabel = (v: number, text: string, k: number) => inside(v) ? <text className="ob-band-t" x={x(v) + 4} y={bottom - 6} data-band-label={k}>{text}</text> : null;

  return <div className="ob-chart">
    <div className="ob-legend-row">
      <div className="ob-legend" aria-hidden>
        <span className="is-expiry">{expiryLabel}</span>
        <span className={'is-today' + (scenarioIsToday ? '' : ' is-dim')}>{tr('optionbuilder.curveToday')}</span>
        {!scenarioIsToday && <span className="is-scenario">{scenarioLabel}</span>}
        {bands && <span className="is-band" data-band-legend title={dist!.basis}>{dist!.label}</span>}
        {theory && <span className="is-theory" data-chart-theory>{tr('optionbuilder.chartTheory')}</span>}
      </div>
      <span className="ob-grow" />
      {zoom ? <button type="button" className="ob-btn is-small" onClick={() => { setZoom(null); setHover(null); }} data-fit>{tr('optionbuilder.fit')}</button>
        : <span className="ob-zoom-hint">{tr('optionbuilder.zoomHint')}</span>}
    </div>
    <div className="ob-plot" ref={box}>
      <svg width={W} height={H} role="img" tabIndex={0} aria-label={tr('optionbuilder.chartAria')} data-zoomed={zoom ? '' : undefined}
        onPointerDown={e => { if (e.button === 0) { const p = pick(e.clientX, e.currentTarget); setDrag({ from: p, to: p }); } }}
        onPointerMove={e => { const p = pick(e.clientX, e.currentTarget); if (drag && e.buttons) setDrag({ ...drag, to: p }); setHover(nearestPoint(rows, p)); }}
        onPointerUp={() => {
          if (drag && Math.abs(x(drag.to) - x(drag.from)) >= DRAG_MIN_PX) { setZoom([Math.min(drag.from, drag.to), Math.max(drag.from, drag.to)]); setHover(null); }
          setDrag(null);
        }}
        onPointerLeave={() => { setHover(null); setDrag(null); }}
        onKeyDown={e => {
          if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault();
            setHover(v => Math.max(0, Math.min(rows.length - 1, (v ?? nearestPoint(rows, spot)) + (e.key === 'ArrowLeft' ? -1 : 1)))); }
          else if (e.key === '+' || e.key === '=') { e.preventDefault(); zoomBy(.5); }
          else if (e.key === '-') { e.preventDefault(); zoomBy(2); }
          else if (e.key === '0' || e.key === 'Escape') { setZoom(null); setHover(null); }
        }}>
        <defs>
          <clipPath id={id + 'up'}><rect x={0} y={0} width={W} height={zero} /></clipPath>
          <clipPath id={id + 'dn'}><rect x={0} y={zero} width={W} height={Math.max(0, H - zero)} /></clipPath>
        </defs>
        {bands && <g className="ob-bands">{band(bands.m2, bands.p2, 2)}{band(bands.m1, bands.p1, 1)}
          {bandLabel(bands.m2, '−2σ', -2)}{bandLabel(bands.m1, '−1σ', -1)}{bandLabel(bands.p1, '+1σ', 1)}{bandLabel(bands.p2, '+2σ', 2)}</g>}
        {yTicks.map(v => <g key={'y' + v}><line className={'ob-grid' + (v === 0 ? ' is-zero' : '')} x1={pad.l} x2={W - pad.r} y1={y(v)} y2={y(v)} />
          <text className={'ob-tick' + (v === 0 ? ' is-zero' : '')} x={pad.l - 8} y={y(v) + 4} textAnchor="end" data-y-tick>{yLabel(v)}</text></g>)}
        {xTicks.map(v => <text key={'x' + v} className="ob-tick" x={x(v)} y={bottom + 18} textAnchor="middle">{f.num(v, maxX - minX < 20 ? 1 : 0)}</text>)}
        {area && <path className="ob-area-up" d={area} clipPath={`url(#${id}up)`} />}
        {area && <path className={'ob-area-dn' + (result.unlimited_loss ? ' is-unlimited' : '')} d={area} clipPath={`url(#${id}dn)`} data-loss-area={result.unlimited_loss ? 'unlimited' : 'bounded'} />}
        <line className="ob-zero" x1={pad.l} x2={W - pad.r} y1={y(0)} y2={y(0)} />
        <line className="ob-axis" x1={pad.l} x2={W - pad.r} y1={bottom} y2={bottom} />
        {(result.breakeven_intervals || []).map((iv, i) => <line key={'z' + i} className="ob-zero-band" x1={x(Math.max(minX, iv.from))} x2={x(Math.min(maxX, iv.to ?? maxX))} y1={y(0)} y2={y(0)} />)}
        {strikeLabels.map(({ k, show }) => <g key={'k' + k} className="ob-strike-mark" data-strike-mark={k}>
          <line x1={x(k)} x2={x(k)} y1={bottom} y2={bottom - 7} />
          {show && <text x={x(k)} y={bottom + 36} textAnchor="middle">{tr('optionbuilder.strikeMark', { v: f.strike(k) })}</text>}</g>)}
        {extremes.map(e => <g key={e.kind} className={'ob-extreme is-' + e.kind} data-extreme={e.kind}>
          <line x1={pad.l} x2={W - pad.r} y1={y(e.v)} y2={y(e.v)} />
          <text x={W - pad.r - 4} y={y(e.v) + (e.v > 0 ? -6 : 14)} textAnchor="end">{e.text}</text></g>)}
        {placed.map(m => <g key={m.kind + m.v} className={m.kind === 'spot' ? 'ob-spot-mark' : m.kind === 'scenario' ? 'ob-scn-mark' : 'ob-be-mark'} data-mark={m.kind}>
          <line x1={x(m.v)} x2={x(m.v)} y1={pad.t - 2 - m.row * 13} y2={bottom} />
          <text x={x(m.v)} y={pad.t - 8 - m.row * 13} textAnchor="middle">{m.text}</text></g>)}
        <path className={'ob-curve-today' + (scenarioIsToday ? '' : ' is-dim')} d={line(r => r.today)} />
        <path className="ob-curve-expiry" d={expiryPath} />
        {!scenarioIsToday && <path className="ob-curve-scenario" d={line(r => r.scenario)} />}
        {result.breakevens.filter(inside).map(v => <circle key={'be' + v} className="ob-be" cx={x(v)} cy={y(0)} r={3.5} />)}
        {result.unlimited_profit && <text className="ob-tail is-up" x={W - pad.r - 2} y={pad.t + 14} textAnchor="end">{tr('optionbuilder.tailUp')}</text>}
        {result.unlimited_loss && <text className="ob-tail is-down" x={W - pad.r - 2} y={bottom - 22} textAnchor="end" data-tail-loss={result.unlimited_loss_reason || undefined}>
          {result.unlimited_loss_reason === 'european_dividend' ? tr('optionbuilder.tailDownTheoretical') : tr('optionbuilder.tailDown')}</text>}
        {(!scenarioIsToday || scenarioMoved) && inside(scenarioSpot) && <circle className="ob-scn-dot" cx={x(scenarioSpot)} cy={y(result.scenario.pnl)} r={5} data-scenario-dot />}
        {/* v3: with the window frozen (or the scenario past the engine curve) the scenario point is not
            dropped: an arrow on the edge says where it is */}
        {(!scenarioIsToday || scenarioMoved) && !inside(scenarioSpot) && (() => {
          const right = scenarioSpot > maxX, ex = right ? W - pad.r - 1 : pad.l + 1;
          const ey = Math.min(Math.max(y(result.scenario.pnl), pad.t + 8), bottom - 8);
          return <g className="ob-scn-edge" data-scenario-edge={right ? 'right' : 'left'}>
            <path d={right ? `M${ex},${ey}l-10,-6v12z` : `M${ex},${ey}l10,-6v12z`} />
            <text x={right ? ex - 14 : ex + 14} y={ey + 4} textAnchor={right ? 'end' : 'start'}>{f.num(scenarioSpot, 2)}</text></g>;
        })()}
        {drag && Math.abs(x(drag.to) - x(drag.from)) >= DRAG_MIN_PX && <rect className="ob-brush" x={Math.min(x(drag.from), x(drag.to))} y={pad.t}
          width={Math.abs(x(drag.to) - x(drag.from))} height={bottom - pad.t} />}
        {hoverRow && <g className="ob-cross">
          <line x1={x(hoverRow.price)} x2={x(hoverRow.price)} y1={pad.t} y2={bottom} />
          {hoverRow.expiry != null && <circle className="is-expiry" cx={x(hoverRow.price)} cy={y(hoverRow.expiry)} r={4} />}
          <circle className="is-today" cx={x(hoverRow.price)} cy={y(hoverRow.today)} r={4} />
          {!scenarioIsToday && <circle className="is-scenario" cx={x(hoverRow.price)} cy={y(hoverRow.scenario)} r={4.5} />}
        </g>}
      </svg>
      {hoverRow && <div className={'ob-tip' + (tipLeft ? ' is-left' : '')} style={{ left: x(hoverRow.price) + (tipLeft ? -12 : 12), top: pad.t + 6 }} role="status" data-tip>
        <div className="ob-tip-head"><b className="ob-num">{f.num(hoverRow.price, 2)}</b><span>{pctFromSpot(hoverRow.price)} {tr('optionbuilder.hoverMove')}</span></div>
        <dl>
          <div className="is-expiry"><dt>{expiryLabel}</dt><dd className="ob-num" data-tip-expiry>{f.signed(hoverRow.expiry, 2)}</dd></div>
          <div className="is-today"><dt>{tr('optionbuilder.curveToday')}</dt><dd className="ob-num" data-tip-today>{f.signed(hoverRow.today, 2)}</dd></div>
          {!scenarioIsToday && <div className="is-scenario"><dt>{tr('optionbuilder.scenario')}</dt><dd className="ob-num" data-tip-scenario>{f.signed(hoverRow.scenario, 2)}</dd></div>}
          {above != null && <div className="is-prob"><dt>{tr('optionbuilder.probAbove')}</dt><dd className="ob-num" data-tip-above>{f.pct(above, 1)}</dd></div>}
        </dl>
        {above != null && <p className="ob-tip-note" data-tip-basis>{dist!.basis}</p>}
      </div>}
    </div>
  </div>;
}
