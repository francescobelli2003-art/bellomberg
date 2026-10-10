import { useLayoutEffect, useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent, ReactNode } from 'react';
import { t as tr } from '@/i18n/t';
import {
  finite, ivText, nearestIndex, nearestQuotes, niceTicks, numText, pctTickText, priceText, segments, smileScale, smileSeries, termSeries,
  type ObservedQuote, type SurfaceModel,
} from '@/lib/vol-deck';

/* ============================================================================
   Fette della superficie (Vol Deck Nuova, 09/10/2026 — Opus 5.5).
   SMILE della scadenza scelta + TERM STRUCTURE (ATM del builder e colonna K/S scelta).
   Disegno in pixel veri (larghezza misurata): il testo resta a 12 px a ogni zoom.
   Regole: un buco del builder resta un buco (linea spezzata + banda tratteggiata), la linea
   e' la griglia interpolata del builder, i punti sono quote MISURATE (IV del fornitore).
   ========================================================================== */

export type AxisMode = 'moneyness' | 'strike';
const H = 300, M = { l: 54, r: 18, t: 18, b: 42 };

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current; if (!el) return;
    const measure = () => setWidth(Math.max(0, Math.floor(el.getBoundingClientRect().width)));
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure); ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

const pctTick = pctTickText;
const nd = () => tr('voldeck.ui_n_a_15');
const short = (expiry: string) => `${expiry.slice(8, 10)}/${expiry.slice(5, 7)}`;
const daysUnit = () => tr('voldeck.short_days');

function Tooltip({ x, y, width, children }: { x: number; y: number; width: number; children: ReactNode }) {
  const left = x > width * 0.6 ? undefined : x + 14, right = x > width * 0.6 ? width - x + 14 : undefined;
  return <div className="vdn-tip" role="presentation" style={{ left, right, top: Math.max(4, y - 10) }}>{children}</div>;
}

/* ── SMILE ───────────────────────────────────────────────────────────── */
export function SmileChart({ model, expiry, column, onColumn, onExpiryStep, quotes, axis }: {
  model: SurfaceModel; expiry: string | null; column: number | null;
  onColumn: (index: number) => void; onExpiryStep: (step: -1 | 1) => void;
  quotes: ObservedQuote[] | null; axis: AxisMode;
}) {
  const [box, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<{ index: number; y: number } | null>(null);
  const points = smileSeries(model, expiry);
  const row = model.rows.find(r => r.expiry === expiry) || null;
  const grid = model.grid;
  if (!grid.length || !row) return <p className="bbn-empty vdn-empty-line">{tr('voldeck.n_smile_empty')}</p>;
  const lo = grid[0], hi = grid[grid.length - 1];
  // solo quote OTM dentro la griglia: le ITM non si disegnano (convenzione OTM del builder)
  const inDomain = (quotes || []).filter(q => q.otm && q.m >= lo - 1e-9 && q.m <= hi + 1e-9);
  const W = Math.max(width, 320), plotW = W - M.l - M.r, plotH = H - M.t - M.b;
  // MEDIA-8: la scala la fanno griglia e quote liquide non segnalate; le altre fuori scala si tagliano e si contano
  const scale = smileScale(points.map(p => p.iv), inDomain);
  const y0 = scale ? scale.y0 : 0.1, y1 = scale ? scale.y1 : 0.3;
  const clipped = scale ? scale.clipped : [];
  const drawn = inDomain.filter(q => !clipped.includes(q));
  const X = (m: number) => M.l + plotW * (m - lo) / ((hi - lo) || 1);
  const Y = (v: number) => M.t + plotH * (1 - (v - y0) / ((y1 - y0) || 1));
  const spot = model.spot;
  const xLabel = (m: number) => axis === 'strike' && spot != null ? priceText(m * spot, nd()) : numText(m, 2);
  const lines = segments(points, p => p.iv).map(seg => seg.map((p, i) => `${i ? 'L' : 'M'}${X(p.m).toFixed(1)},${Y(p.iv as number).toFixed(1)}`).join(''));
  // bande dei buchi: tratti di griglia senza dato, larghi mezza cella per lato
  const step = plotW / Math.max(1, grid.length - 1);
  const holes: { x0: number; x1: number }[] = [];
  points.forEach((p, i) => {
    if (p.iv != null) return;
    const x0 = Math.max(M.l, X(p.m) - step / 2), x1 = Math.min(W - M.r, X(p.m) + step / 2);
    const last = holes[holes.length - 1];
    if (last && Math.abs(last.x1 - x0) < 0.5) last.x1 = x1; else holes.push({ x0, x1 });
  });
  const xTicks = grid.filter((_, i) => i % 4 === 0 || i === grid.length - 1);
  const yTicks = niceTicks(y0, y1, 5);
  const pick = (clientX: number, clientY: number) => {
    const r = box.current?.getBoundingClientRect(); if (!r) return null;
    const vx = clientX - r.left;
    const index = nearestIndex(grid.map(X), vx);
    return index < 0 ? null : { index, y: clientY - r.top };
  };
  const hi2 = hover ?? (column != null ? { index: column, y: M.t + 20 } : null);
  const sel = hi2 ? points[hi2.index] : null;
  const near = sel ? nearestQuotes(quotes || [], sel.strike) : null;
  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      e.preventDefault(); setHover(null);
      onColumn(Math.max(0, Math.min(grid.length - 1, (column ?? Math.floor(grid.length / 2)) + (e.key === 'ArrowLeft' ? -1 : 1))));
    } else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') { e.preventDefault(); onExpiryStep(e.key === 'ArrowUp' ? 1 : -1); }
  };
  return (
    <div className="vdn-chart" ref={box} data-vol-smile tabIndex={0} role="group" aria-roledescription={tr('voldeck.n_interactive_chart')} onKeyDown={onKey}
      aria-label={tr('voldeck.n_smile_aria', { e: row.expiry })}
      onPointerMove={(e: PointerEvent) => setHover(pick(e.clientX, e.clientY))} onPointerLeave={() => setHover(null)}
      onPointerDown={(e: PointerEvent) => { const p = pick(e.clientX, e.clientY); if (p) onColumn(p.index); }}>
      {width > 0 && <svg width={W} height={H} aria-hidden="true">
        <defs><pattern id="vdn-hatch-smile" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="6" className="vdn-hatch-line" /></pattern></defs>
        {holes.map((h, i) => <rect key={i} x={h.x0} y={M.t} width={Math.max(1, h.x1 - h.x0)} height={plotH} fill="url(#vdn-hatch-smile)" className="vdn-hole" />)}
        {yTicks.map(v => <g key={v}>
          <line x1={M.l} x2={W - M.r} y1={Y(v)} y2={Y(v)} className="vdn-gridline" />
          <text x={M.l - 8} y={Y(v) + 4} textAnchor="end" className="vdn-tick">{pctTick(v)}</text></g>)}
        {xTicks.map(m => <text key={m} x={X(m)} y={H - M.b + 18} textAnchor="middle" className="vdn-tick">{xLabel(m)}</text>)}
        <text x={W - M.r} y={H - 6} textAnchor="end" className="vdn-axis-title">{axis === 'strike' ? 'Strike' : 'K/S'}</text>
        {1 >= lo && 1 <= hi && <g>
          <line x1={X(1)} x2={X(1)} y1={M.t} y2={M.t + plotH} className="vdn-spotline" />
          <text x={X(1) + 4} y={M.t + 11} className="vdn-tick">{spot != null ? `Spot ${priceText(spot, nd())}` : 'K/S 1'}</text></g>}
        {column != null && grid[column] != null && <line x1={X(grid[column])} x2={X(grid[column])} y1={M.t} y2={M.t + plotH} className="vdn-sel-col" />}
        {lines.map((d, i) => <path key={i} d={d} className="vdn-line-grid" />)}
        {points.map((p, i) => p.iv != null && <circle key={i} cx={X(p.m)} cy={Y(p.iv)} r={2.6} className="vdn-node-grid" />)}
        {drawn.map((q, i) => q.flagged
          ? <path key={'q' + i} d={`M${X(q.m).toFixed(1)},${(Y(q.iv) - 4.5).toFixed(1)}l4.5,4.5l-4.5,4.5l-4.5,-4.5Z`} className="vdn-dot-flag" />
          : <circle key={'q' + i} cx={X(q.m)} cy={Y(q.iv)} r={3.4} className={q.liquid ? 'vdn-dot-obs' : 'vdn-dot-illiquid'} />)}
        {hi2 && <g pointerEvents="none">
          <line x1={X(grid[hi2.index])} x2={X(grid[hi2.index])} y1={M.t} y2={M.t + plotH} className="vdn-crosshair" />
          {points[hi2.index].iv != null && <circle cx={X(grid[hi2.index])} cy={Y(points[hi2.index].iv as number)} r={5} className="vdn-node-focus" />}
        </g>}
      </svg>}
      {hover && sel && <Tooltip x={X(sel.m)} y={hover.y} width={W}>
        <b>K/S {numText(sel.m, 3)}{sel.strike != null ? ` · ${tr('voldeck.n_strike_eq_short')} ${priceText(sel.strike, nd())}` : ''}</b>
        <span>{row.expiry} · {row.days}{daysUnit()}</span>
        <span>{tr('voldeck.n_iv_grid')}: <b>{sel.iv == null ? tr('voldeck.n_hole_word') : ivText(sel.iv, nd(), 2)}</b></span>
        <QuoteLines near={near} quotes={quotes} />
      </Tooltip>}
      {clipped.length > 0 && <p className="vdn-legend vdn-clip-note" data-vol-smile-clipped={clipped.length}>
        {tr('voldeck.n_smile_clipped', { n: clipped.length, max: ivText(Math.max(...clipped.map(q => q.iv)), nd()) })}</p>}
    </div>
  );
}

export function QuoteLines({ near, quotes }: { near: ReturnType<typeof nearestQuotes> | null; quotes: ObservedQuote[] | null }) {
  if (quotes == null) return <span className="vdn-tip-muted">{tr('voldeck.n_quotes_not_loaded')}</span>;
  if (!near || near.strike == null) return <span className="vdn-tip-muted">{tr('voldeck.n_sel_none')}</span>;
  const line = (q: ObservedQuote | null, label: string) => q == null
    ? <span key={label} className="vdn-tip-muted">{label} {priceText(near.strike, nd())}: {nd()}</span>
    : <span key={label}>{label} {priceText(q.strike, nd())} · Bid {priceText(q.bid, nd())} / Ask {priceText(q.ask, nd())} · IV {ivText(q.iv, nd())}{q.liquid ? '' : ` · ${tr('voldeck.n_illiquid_short')}`}{q.flagged ? ` · ⚠ ${q.flags[0]}` : ''}</span>;
  return <>{line(near.put, 'Put')}{line(near.call, 'Call')}</>;
}

/* ── TERM STRUCTURE ──────────────────────────────────────────────────── */
export function TermChart({ model, column, expiry, onExpiry }: {
  model: SurfaceModel; column: number | null; expiry: string | null; onExpiry: (expiry: string) => void;
}) {
  const [box, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<{ index: number; y: number } | null>(null);
  const pts = termSeries(model, column);
  if (!pts.length) return <p className="bbn-empty vdn-empty-line">{tr('voldeck.ui_n_a_at_least_2_expiries_required_16')}</p>;
  const values = pts.flatMap(p => [p.atm, p.col]).filter(finite);
  const W = Math.max(width, 320), plotW = W - M.l - M.r, plotH = H - M.t - M.b;
  const maxD = Math.max(...pts.map(p => p.days));
  const X = (d: number) => M.l + plotW * Math.sqrt(Math.max(0, d) / (maxD || 1));
  const yLo = values.length ? Math.min(...values) : 0.1, yHi = values.length ? Math.max(...values) : 0.3;
  const pad = Math.max(0.005, (yHi - yLo) * 0.15), y0 = yLo - pad, y1 = yHi + pad;
  const Y = (v: number) => M.t + plotH * (1 - (v - y0) / ((y1 - y0) || 1));
  const path = (key: 'atm' | 'col') => segments(pts, p => p[key]).map(seg => seg.map((p, i) => `${i ? 'L' : 'M'}${X(p.days).toFixed(1)},${Y(p[key] as number).toFixed(1)}`).join(''));
  const colLabel = column != null && model.grid[column] != null ? numText(model.grid[column], 3) : null;
  const yTicks = niceTicks(y0, y1, 5);
  let lastX = -999;
  const pick = (clientX: number, clientY: number) => {
    const r = box.current?.getBoundingClientRect(); if (!r) return null;
    const index = nearestIndex(pts.map(p => X(p.days)), clientX - r.left);
    return index < 0 ? null : { index, y: clientY - r.top };
  };
  const selIndex = pts.findIndex(p => p.expiry === expiry);
  const onKey = (e: KeyboardEvent) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault(); setHover(null);
    const next = Math.max(0, Math.min(pts.length - 1, (selIndex < 0 ? 0 : selIndex) + (e.key === 'ArrowLeft' ? -1 : 1)));
    onExpiry(pts[next].expiry);
  };
  const h = hover ? pts[hover.index] : null;
  return (
    <div className="vdn-chart" ref={box} data-vol-term tabIndex={0} role="group" aria-roledescription={tr('voldeck.n_interactive_chart')} onKeyDown={onKey}
      aria-label={tr('voldeck.n_term_aria')}
      onPointerMove={(e: PointerEvent) => setHover(pick(e.clientX, e.clientY))} onPointerLeave={() => setHover(null)}
      onPointerDown={(e: PointerEvent) => { const p = pick(e.clientX, e.clientY); if (p) onExpiry(pts[p.index].expiry); }}>
      {width > 0 && <svg width={W} height={H} aria-hidden="true">
        {yTicks.map(v => <g key={v}>
          <line x1={M.l} x2={W - M.r} y1={Y(v)} y2={Y(v)} className="vdn-gridline" />
          <text x={M.l - 8} y={Y(v) + 4} textAnchor="end" className="vdn-tick">{pctTick(v)}</text></g>)}
        {pts.map((p, i) => {
          const x = X(p.days), show = i === 0 || i === pts.length - 1 || x - lastX > 44;
          if (show) lastX = x;
          return <g key={p.expiry}>
            <line x1={x} x2={x} y1={M.t + plotH} y2={M.t + plotH + 4} className="vdn-axisline" />
            {show && <text x={x} y={H - M.b + 18} textAnchor="middle" className="vdn-tick">{p.days}{daysUnit()}</text>}
          </g>;
        })}
        <text x={W - M.r} y={H - 6} textAnchor="end" className="vdn-axis-title">{tr('voldeck.ui_days_to_expiry_53')} (√t)</text>
        {selIndex >= 0 && <line x1={X(pts[selIndex].days)} x2={X(pts[selIndex].days)} y1={M.t} y2={M.t + plotH} className="vdn-sel-expiry" />}
        {path('col').map((d, i) => <path key={'c' + i} d={d} className="vdn-line-col" />)}
        {path('atm').map((d, i) => <path key={'a' + i} d={d} className="vdn-line-atm" />)}
        {pts.map(p => <g key={'n' + p.expiry}>
          {p.atm != null && <circle cx={X(p.days)} cy={Y(p.atm)} r={3.6} className="vdn-node-atm" />}
          {p.col != null && <circle cx={X(p.days)} cy={Y(p.col)} r={3.2} className="vdn-node-col" />}
          {p.partial && <circle cx={X(p.days)} cy={Y((p.atm ?? p.col ?? y0))} r={7} className="vdn-node-partial" />}
        </g>)}
        {hover && h && <line x1={X(h.days)} x2={X(h.days)} y1={M.t} y2={M.t + plotH} className="vdn-crosshair" pointerEvents="none" />}
      </svg>}
      {hover && h && <Tooltip x={X(h.days)} y={hover.y} width={W}>
        <b>{h.expiry} · {h.days}{daysUnit()}</b>
        <span><i className="vdn-key is-atm" />{tr('voldeck.n_term_atm_short')}: <b>{ivText(h.atm, nd(), 2)}</b></span>
        {colLabel && <span><i className="vdn-key is-col" />{tr('voldeck.n_term_col', { m: colLabel })}: <b>{h.col == null ? tr('voldeck.n_hole_word') : ivText(h.col, nd(), 2)}</b></span>}
        {h.partial && <span className="vdn-tip-warn">{tr('voldeck.n_partial_badge')}</span>}
      </Tooltip>}
      <div className="vdn-chart-key">
        <span><i className="vdn-key is-atm" />{tr('voldeck.n_term_atm')}</span>
        {colLabel && <span><i className="vdn-key is-col" />{tr('voldeck.n_term_col', { m: colLabel })}</span>}
      </div>
    </div>
  );
}

export { short as shortExpiry };
