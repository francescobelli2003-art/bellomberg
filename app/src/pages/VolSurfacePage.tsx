import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import ModernPage from '@/components/ModernPage';
import NewInterfaceBoundary from '@/components/NewInterfaceBoundary';
import { useInterfaceTheme } from '@/components/InterfaceThemeProvider';
import Card from '@/components/nuova/Card';
import { useLingua, useT } from '@/i18n/provider';
import { t as tr } from '@/i18n/t';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import { surfaceExpiries, toggleExpiry, visibleSurface, type VolWorkspace } from '@/lib/vol-atlas';
import VolWorkbench from '@/components/VolWorkbench';
import {
  atmIndex, DEFAULT_MAX_REL_SPREAD, surfaceQuoteScale, discardTotals, finite, ivText, MAX_REFETCH_EXPIRIES, nearestIndex, nearestQuotes, numText, nyTime,
  observedQuotes, partialExpiries, priceText, surfaceFreshness, surfaceModel, volRequest, type ObservedQuote,
} from '@/lib/vol-deck';
import { localizePayload } from '@/lib/api-presentation';
import Surface3D, { SCALE_DARK, SCALE_LIGHT } from './voldeck/Surface3D';
import { SmileChart, TermChart, type AxisMode } from './voldeck/SliceCharts';
import PointReadout from './voldeck/PointReadout';
import { useObservedChains } from './voldeck/useObservedChains';
import '@/components/nuova/nuova.css';
import './vol-atlas.css';
import './risk-modern.css';
import './voldeck-nuova.css';

// #179 — F12 Volatility Surface: superficie IV da chain Polygon multi-expiry.
// v3 (22/07): i buchi dichiarati dal builder RESTANO buchi; geometria z mai tagliata (il p99 satura
//     solo il colore). v4 «VOL DECK» (25/07): proiettore di strike, altimetro IV rank, cono, GEX.
// v5 NUOVA (09/10/2026, Opus 5.5 — ordine PM «3D ruotabile + fette»): palette e componenti Nuova
//     (--bbn-*, Card, segmenti) come le pagine di Andrea; superficie 3D ruotabile con quote MISURATE
//     distinte dalla griglia interpolata del builder, buchi marcati; sotto SMILE della scadenza scelta e
//     TERM STRUCTURE (ATM del builder + colonna K/S scelta), lettura esatta al passaggio e clic che
//     sceglie le fette. Eliminati i due rendering ATM che portavano null a 0 (tabella term/skew e cresta
//     3D con ripiego silenzioso su atm_iv): una misura mancante resta n.d. L'acquisizione, la chain e il
//     laboratorio restano di VolWorkbench (montato nello stesso punto, stesso import).

function DeferredVolPagePresentation({ render }: { render: () => ReactNode }) { return render(); }

function VolPagePresentationBoundary({ render }: { render: () => ReactNode }) {
  const language = useLingua();
  return <NewInterfaceBoundary language={language}>
    <DeferredVolPagePresentation render={render} />
  </NewInterfaceBoundary>;
}

// prezzo con decimali sensati (stessa filosofia di fmtPx di TerminalChart)
const px = (v: number) =>
  v >= 1000 ? v.toLocaleString(localeDi(linguaCorrente()), { maximumFractionDigits: 0 })
  : v >= 100 ? v.toFixed(1) : v.toFixed(2);

// mix lineare fra due colori RGB (per la scala vicino→lontano dello X-RAY)
function mixc(a: [number, number, number], b: [number, number, number], t: number) {
  const c = a.map((v, i) => Math.round(v + (b[i] - v) * t));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}
/* ============================================================
   PROIETTORE DI STRIKE — strumento-firma F12: il cono expected-move
   per scadenza (spot ± ATM IV × √T), 1σ banda piena + 2σ tratteggiata,
   asse √t (il front respira), marker EARNINGS, tooltip con gli strike.
   È il prezzo DELLE OPZIONI, non una previsione: dichiarato in legenda.
   ============================================================ */
function StrikeProjector({ spot, term, earnings }: {
  spot: number; term: any[]; earnings?: string | null;
}) {
  const pts = (term || []).filter(s => s.days >= 2 && s.atm_iv != null && isFinite(s.atm_iv) && s.atm_iv > 0);
  if (!(spot > 0) || pts.length < 2) {
    return <div className="num" style={{ padding: '14px 12px', fontSize: 12, fontWeight: 600, color: 'var(--bbn-muted)' }}>
      {tr('voldeck.ui_n_a_spot_and_at_least_2_usable_expiries_are_required_0_2')}</div>;
  }
  const W = 332, H = 236, L = 46, T = 12, B = 24;
  const maxD = pts[pts.length - 1].days;
  const sig = (s: any, k: number) => s.atm_iv * Math.sqrt(s.days / 365) * k;
  const last = pts[pts.length - 1];
  const upperStrikeLabel = `${px(spot * (1 + sig(last, 1)))} · +${(sig(last, 1) * 100).toFixed(0)}%`;
  const lowerStrikeLabel = `${px(spot * (1 - sig(last, 1)))} · −${(sig(last, 1) * 100).toFixed(0)}%`;
  // Nuova SVG labels render at 12px. Estimate their rendered width and reserve
  // it on the right while keeping at least 120 user units for the plot itself.
  const R = Math.max(104, Math.min(W - L - 120,
    Math.ceil(Math.max(upperStrikeLabel.length, lowerStrikeLabel.length) * 7.2 + 14)));
  const sx = (d: number) => L + (W - L - R) * Math.sqrt(Math.max(0, d) / maxD);
  const pad = Math.max(...pts.map(p => sig(p, 2))) * 1.08;
  const sy = (rel: number) => T + (H - T - B) * (1 - (rel + pad) / (2 * pad));
  const apex = `${sx(0).toFixed(1)},${sy(0).toFixed(1)}`;
  const band = (k: number) => 'M' + apex
    + pts.map(p => `L${sx(p.days).toFixed(1)},${sy(sig(p, k)).toFixed(1)}`).join('')
    + [...pts].reverse().map(p => `L${sx(p.days).toFixed(1)},${sy(-sig(p, k)).toFixed(1)}`).join('') + 'Z';
  const line2 = (up: boolean) => 'M' + apex
    + pts.map(p => `L${sx(p.days).toFixed(1)},${sy(sig(p, up ? 2 : -2)).toFixed(1)}`).join('');
  // griglia orizzontale a passo "pulito" (~3-5 linee dentro la banda)
  let step = 1;
  for (const c of [0.05, 0.1, 0.2, 0.25, 0.5, 1, 2]) { if (2 * pad / c <= 6) { step = c; break; } }
  const gl: number[] = [];
  for (let v = step; v < pad; v += step) { gl.push(v); gl.push(-v); }
  // marker earnings se dentro la finestra delle scadenze
  const eT = earnings ? Date.parse(earnings + 'T00:00:00Z') : NaN;
  const eDays = isFinite(eT) ? Math.ceil((eT - Date.now()) / 86400000) : null;
  const showE = eDays != null && eDays > 0 && eDays <= maxD;
  // etichette asse x senza affollamento (√t comprime il fondo)
  let lastLx = -999;
  const xt = pts.map((p, i) => {
    const x = sx(p.days);
    const show = i === 0 || i === pts.length - 1 || x - lastLx > 30;
    if (show) lastLx = x;
    return { x, d: p.days, show };
  });
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="vsxsvg" role="img"
         aria-label={tr('voldeck.ui_expected_move_cone_by_expiry_spot_plus_minus_atm_iv_ti_3')}>
      {gl.map((v, i) => (
        <g key={i}>
          <line x1={L} x2={W - R} y1={sy(v)} y2={sy(v)} stroke="var(--vdn-grid)" strokeWidth="1" />
          <text x={L - 4} y={sy(v) + 3} fontSize="11" fontWeight={600} fill="var(--bbn-muted)" textAnchor="end">
            {(v > 0 ? '+' : '') + Math.round(v * 100) + '%'}
          </text>
        </g>
      ))}
      <path d={band(2)} fill="var(--vdn-band-weak)" />
      <path d={band(1)} fill="var(--vdn-band)" stroke="var(--vdn-band-line)" strokeWidth="1" />
      <path d={line2(true)} fill="none" stroke="var(--vdn-band-line)" strokeWidth="1" strokeDasharray="3 3" />
      <path d={line2(false)} fill="none" stroke="var(--vdn-band-line)" strokeWidth="1" strokeDasharray="3 3" />
      {/* spot: la linea di fede dello strumento */}
      <line x1={L} x2={W - R} y1={sy(0)} y2={sy(0)} stroke="var(--bbn-muted)" strokeWidth="1" strokeDasharray="1 3" />
      <text x={L - 4} y={sy(0) + 3} fontSize="11" fill="var(--bbn-muted)" textAnchor="end">{px(spot)}</text>
      {/* earnings: il premio evento reso visibile dove vive */}
      {showE && <g>
        <line x1={sx(eDays!)} x2={sx(eDays!)} y1={T} y2={H - B} stroke="var(--bbn-warn)" strokeWidth="1" strokeDasharray="4 3" />
        <text x={sx(eDays!) + 3} y={T + 9} fontSize="11" fill="var(--bbn-warn)">E {earnings!.slice(8, 10)}/{earnings!.slice(5, 7)}</text>
      </g>}
      {/* ticks scadenze + punti 1σ con tooltip strike */}
      {xt.map((t, i) => (
        <g key={i}>
          <line x1={t.x} x2={t.x} y1={H - B} y2={H - B + 3} stroke="var(--vdn-grid)" strokeWidth="1" />
          {t.show && <text x={t.x} y={H - B + 13} fontSize="11" fontWeight={600} fill="var(--bbn-muted)" textAnchor="middle">{t.d}{tr('voldeck.short_days')}</text>}
        </g>
      ))}
      {pts.map((p, i) => {
        const s1 = sig(p, 1);
        return (
          <g key={i}>
            <circle cx={sx(p.days)} cy={sy(s1)} r="2.2" fill="var(--bbn-accent)">
              <title>{p.expiry} · +{(s1 * 100).toFixed(1)}% → {px(spot * (1 + s1))}</title>
            </circle>
            <circle cx={sx(p.days)} cy={sy(-s1)} r="2.2" fill="var(--bbn-accent)">
              <title>{p.expiry} · −{(s1 * 100).toFixed(1)}% → {px(spot * (1 - s1))}</title>
            </circle>
          </g>
        );
      })}
      {/* strike 1σ all'ultima scadenza: il perimetro a fine finestra */}
      <text x={W - R + 5} y={sy(sig(last, 1)) + 2.5} fontSize="12" fill="var(--bbn-accent)">
        {upperStrikeLabel}
      </text>
      <text x={W - R + 5} y={sy(-sig(last, 1)) + 2.5} fontSize="12" fill="var(--bbn-accent)">
        {lowerStrikeLabel}
      </text>
      <text x={W - R + 5} y={sy(0) + 3} fontSize="11" fontWeight={600} fill="var(--bbn-muted)">SPOT</text>
    </svg>
  );
}

/* ============================================================
   ALTIMETRO IV RANK — iv_history_context (voce (39) backend), con la
   tricotomia di casa: chiave ASSENTE = backend pre-riavvio (dichiarato),
   error = verbatim (storia insufficiente / tabella assente), dato =
   scala 0-100 con YOUNG urlato finché la storia non matura. Mai un
   rank spacciato per maturo.
   ============================================================ */
function IvAltimeter({ ctx }: { ctx: any }) {
  if (ctx === undefined) {
    return <div className="num" style={{ padding: '12px 12px 14px', fontSize: 12, fontWeight: 600, color: 'var(--bbn-muted)', lineHeight: 1.7 }}>
      {tr('voldeck.ui_n_a_iv_history_missing_from_the_response_4')}<br />
      <span style={{ color: 'var(--bbn-warn)' }}>{tr('voldeck.ui_request_context_explicitly_to_check_the_available_iv_h_5')}</span>
    </div>;
  }
  if (!ctx || ctx.error) {
    return <div className="num" style={{ padding: '12px 12px 14px', fontSize: 12, color: 'var(--bbn-warn)', lineHeight: 1.7 }}>
      {tr('voldeck.ui_reported_by_the_backend_6')}{String(ctx?.error || tr('voldeck.ui_empty_context_7'))}
      {ctx?.n_obs != null && <span style={{ fontWeight: 600, color: 'var(--bbn-muted)' }}> · {ctx.n_obs} {' '}{tr('voldeck.ui_collected_observations_8')}</span>}
    </div>;
  }
  // audit 09/10 B8: Number(null) era 0 -> un percentile mancante si disegnava come «0°».
  if (!finite(ctx.iv_percentile)) {
    return <div className="num" style={{ padding: '12px 12px 14px', fontSize: 12, color: 'var(--bbn-warn)', lineHeight: 1.7 }}>
      {tr('voldeck.n_iv_percentile_na')}</div>;
  }
  const pct = Math.max(0, Math.min(100, ctx.iv_percentile as number));
  const H = 178, top = 12, bot = 166;
  const y = (p: number) => bot - (bot - top) * (p / 100);
  const tone = pct >= 80 ? 'var(--bbn-bad)' : pct >= 60 ? 'var(--bbn-warn)' : pct <= 20 ? 'var(--bbn-good)' : 'var(--bbn-accent)';
  return (
    <div style={{ display: 'flex', gap: 12, padding: '8px 12px 10px', alignItems: 'stretch' }}>
      <svg viewBox={`0 0 60 ${H}`} width="60" height={H} style={{ flex: '0 0 60px' }} role="img" aria-label={tr('voldeck.fmt_iv_rank_a_out_of__0', {a: pct})}>
        <line x1="40" x2="40" y1={top} y2={bot} stroke="var(--vdn-grid)" strokeWidth="1.5" />
        {Array.from({ length: 11 }, (_, i) => i * 10).map(p => (
          <g key={p}>
            <line x1={p % 50 === 0 ? 30 : 34} x2="40" y1={y(p)} y2={y(p)} stroke="var(--vdn-grid)" strokeWidth="1" />
            {p % 50 === 0 && <text x="26" y={y(p) + 3} fontSize="11" fontWeight={600} fill="var(--bbn-muted)" textAnchor="end">{p}</text>}
          </g>
        ))}
        <line x1="40" x2="48" y1={y(pct)} y2={y(pct)} stroke={tone} strokeWidth="2" />
        <path d={`M48,${y(pct)} l7,-4 v8 Z`} fill={tone} />
      </svg>
      <div className="num" style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 3, minWidth: 0 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--bbn-muted)' }}>{tr('voldeck.ui_iv_rank_historical_percentile_9')}</div>
        {/* contratto cbcfd0b: il rank e' sull'IV a scadenza costante 30g; si legge ACCANTO al suo valore corrente */}
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
          <span style={{ fontSize: 26, fontWeight: 700, lineHeight: 1, color: tone }} data-vol-iv-rank>{numText(ctx.iv_percentile, 0)}<span style={{ fontSize: 12, fontWeight: 400 }}>°</span></span>
          <span style={{ fontSize: 14, fontWeight: 700 }} data-vol-iv30-current>{tr('voldeck.n_iv30_current', { v: ivText(ctx.iv_30d_current, tr('voldeck.ui_n_a_15')) })}</span>
        </div>
        <div style={{ fontSize: 12, color: 'var(--bbn-muted)' }}>min {ivText(ctx.iv_min, tr('voldeck.ui_n_a_15'))} · max {ivText(ctx.iv_max, tr('voldeck.ui_n_a_15'))}</div>
        <div style={{ fontSize: 12, color: 'var(--bbn-muted)' }} title={ctx.front_basis || undefined}>{tr('voldeck.n_iv_front_secondary', {
          v: ivText(ctx.iv_front_current, tr('voldeck.ui_n_a_15')), p: finite(ctx.iv_front_percentile) ? numText(ctx.iv_front_percentile, 0) + '°' : tr('voldeck.ui_n_a_15') })}</div>
        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--bbn-muted)' }}>
          {ctx.n_obs} {' '}{tr('voldeck.ui_observations_since_10')}{' '}{ctx.history_from}
        </div>
        {(ctx.current_partial || ctx.current_legacy_v15) && <span className="fat-pill is-warn" style={{ alignSelf: 'flex-start' }}>
          {tr(ctx.current_partial ? 'voldeck.n_iv_current_partial' : 'voldeck.n_iv_current_legacy')}</span>}
        {ctx.young && (
          <span className="fat-pill is-warn" style={{ alignSelf: 'flex-start' }}
                title={finite(ctx.young_threshold_obs) ? tr('voldeck.fmt_history_below_a_observations_the_percentile_has__1', {a: ctx.young_threshold_obs}) : undefined}>
            {tr('voldeck.ui_short_history_11')}{ctx.n_obs}{finite(ctx.young_threshold_obs) ? '/' + ctx.young_threshold_obs : ' · ' + tr('voldeck.n_threshold_na')}
          </span>
        )}
      </div>
    </div>
  );
}
/* palette del mesh riusata per la vista top-down (coerenza fra le due rese) */
const rgb = (hex: string): [number, number, number] => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16)) as [number, number, number];
const HEAT_STOPS: [number, [number, number, number]][] = SCALE_LIGHT.map(([at, hex]) => [at, rgb(hex)]);
/* Dark Nuova: same five anchors as the dark Plotly colourscale below (deep blue
   -> sky -> amber -> orange), so the 3D mesh and the top-down view agree. */
const HEAT_STOPS_DARK: [number, [number, number, number]][] = SCALE_DARK.map(([at, hex]) => [at, rgb(hex)]);
function heatColor(t: number, stops = HEAT_STOPS) {
  const x = Math.max(0, Math.min(1, t));
  for (let i = 1; i < stops.length; i++) {
    if (x <= stops[i][0]) {
      const [a, ca] = stops[i - 1], [b, cb] = stops[i];
      return mixc(ca, cb, (x - a) / ((b - a) || 1));
    }
  }
  return mixc(stops[stops.length - 2][1], stops[stops.length - 1][1], 1);
}
const kfmt = (n: number) => n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n);
/* SURFACE TOP-DOWN // HEAT — la verità cella per cella (buchi = celle scure).
   v4-ter (feedback PM live "le scritte laterali non si leggono"): resa HTML
   con etichette a PX FISSI — la leggibilità non scala più col contenitore. */
function HeatTopDown({ grid, slices }: { grid: number[]; slices: any[] }) {
  const dark = useInterfaceTheme().effective === 'dark';
  const stops = dark ? HEAT_STOPS_DARK : HEAT_STOPS;
  const use = (slices || []).filter((s: any) => s.days >= 2);
  if (!grid?.length || !use.length) return <div className="num" style={{ padding: '12px', fontSize: 12, fontWeight: 600, color: 'var(--bbn-muted)' }}>{tr('voldeck.ui_n_a_15')}</div>;
  const vals = use.flatMap((s: any) => s.iv_grid.filter((v: any) => v != null && isFinite(v)));
  if (!vals.length) return <div className="num" style={{ padding: '12px', fontSize: 12, fontWeight: 600, color: 'var(--bbn-muted)' }}>{tr('voldeck.ui_n_a_empty_grid_20')}</div>;
  const vmin = Math.min(...vals), vmax = Math.max(...vals);
  const iAtm = atmIndex(grid);
  const LBL = 108;
  return (
    <div style={{ padding: '2px 10px 0' }} role="img" aria-label={tr('voldeck.ui_top_down_iv_surface_expiries_by_moneyness_21')}>
      {use.map((s: any, r: number) => (
        <div key={r} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 2 }}>
          <span className="num" style={{ flex: `0 0 ${LBL}px`, fontSize: 12, color: 'var(--bbn-muted)', textAlign: 'right' }}>
            {s.expiry.slice(8, 10)}/{s.expiry.slice(5, 7)} · {s.days}{tr('voldeck.short_days')}
          </span>
          <div style={{ flex: 1, display: 'flex', gap: 1, height: 24 }}>
            {grid.map((m: number, c: number) => {
              const v = s.iv_grid[c];
              const ok = v != null && isFinite(v);
              return (
                <div key={c}
                     title={`${s.expiry} · K/S ${m.toFixed(3)} · ${ok ? 'IV ' + (v * 100).toFixed(1) + '%' : tr('voldeck.ui_n_a_missing_quote_declared_gap_22')}`}
                     className={ok ? undefined : 'vdn-heat-hole'}
                     style={{ flex: 1, borderRadius: 3, background: ok ? heatColor((v - vmin) / ((vmax - vmin) || 1), stops) : undefined,
                              boxShadow: c === iAtm ? 'inset 0 0 0 1.5px var(--bbn-text)' : undefined }} />
              );
            })}
          </div>
        </div>
      ))}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 3 }}>
        <span style={{ flex: `0 0 ${LBL}px` }} />
        <div style={{ flex: 1, display: 'flex', gap: 1 }}>
          {grid.map((m: number, c: number) => {
            const show = c === 0 || c === grid.length - 1 || c === iAtm || c === Math.floor(grid.length / 4) || c === Math.floor(3 * grid.length / 4);
            return (
              <span key={c} className="num" style={{ flex: 1, fontSize: 12, color: c === iAtm ? 'var(--bbn-text)' : 'var(--bbn-muted)', textAlign: 'center' }}>
                {show ? (c === iAtm ? 'ATM' : m.toFixed(2)) : ''}
              </span>
            );
          })}
        </div>
      </div>
      <div className="num" style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '7px 0 2px' }}>
        <span style={{ flex: `0 0 ${LBL}px`, fontSize: 12, fontWeight: 600, color: 'var(--bbn-muted)', textAlign: 'right' }}>{tr('voldeck.ui_iv_scale_23')}</span>
        <div style={{ flex: '0 0 190px', height: 8, background: `linear-gradient(90deg, ${heatColor(0, stops)}, ${heatColor(0.35, stops)}, ${heatColor(0.62, stops)}, ${heatColor(0.85, stops)}, ${heatColor(1, stops)})` }} />
        <span style={{ fontSize: 12, color: 'var(--bbn-muted)' }}>{(vmin * 100).toFixed(0)}% → {(vmax * 100).toFixed(0)}%</span>
      </div>
    </div>
  );
}

/* FORWARD VOL — varianza forward fra scadenze consecutive: dove la curva
   prezza gli eventi. σ_fwd = √((σ2²·T2 − σ1²·T1)/(T2−T1)); varianza negativa
   = curva invertita, DICHIARATA (non un numero inventato). */
function FwdVolLadder({ term }: { term: any[] }) {
  const pts = (term || []).filter(s => s.days >= 2 && s.atm_iv != null && s.atm_iv > 0);
  if (pts.length < 2) return <div className="num" style={{ padding: '12px', fontSize: 12, fontWeight: 600, color: 'var(--bbn-muted)' }}>{tr('voldeck.ui_n_a_at_least_2_expiries_required_16')}</div>;
  const rows: { lab: string; fwd: number | null }[] = [];
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1], b = pts[i];
    const T1 = a.days / 365, T2 = b.days / 365;
    const vf = (b.atm_iv * b.atm_iv * T2 - a.atm_iv * a.atm_iv * T1) / (T2 - T1);
    rows.push({ lab: `${a.days}${tr('voldeck.short_days')}→${b.days}${tr('voldeck.short_days')}`, fwd: vf > 0 ? Math.sqrt(vf) : null });
  }
  const mx = Math.max(...rows.map(r => r.fwd ?? 0), 0.0001);
  return (
    <div style={{ padding: '6px 12px 8px', display: 'flex', flexDirection: 'column', gap: 5 }}>
      {rows.map((r, i) => (
        <div key={i} className="num" style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
          <span style={{ width: 82, fontWeight: 600, color: 'var(--bbn-muted)' }}>{r.lab}</span>
          <span style={{ flex: 1, height: 6, background: 'var(--bbn-raised)', position: 'relative' }}>
            {r.fwd != null && <i style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: (100 * r.fwd / mx) + '%', background: 'var(--vdn-violet)' }} />}
          </span>
          <span style={{ width: 82, textAlign: 'right', color: r.fwd == null ? 'var(--bbn-warn)' : 'var(--bbn-text)' }}>
            {r.fwd == null ? tr('voldeck.ui_inverted_24') : (r.fwd * 100).toFixed(1) + '%'}
          </span>
        </div>
      ))}
    </div>
  );
}

/* OPEN INTEREST — posizionamento put/call per scadenza (dal payload) */
function OiProfile({ term }: { term: any[] }) {
  // v2 (10/10): un OI assente resta assente (barra tratteggiata n.d.), mai una barra a zero
  const pts = (term || []).filter(s => (finite(s.call_oi) && s.call_oi > 0) || (finite(s.put_oi) && s.put_oi > 0));
  if (!pts.length) return <div className="num" style={{ padding: '12px', fontSize: 12, fontWeight: 600, color: 'var(--bbn-muted)' }}>{tr('voldeck.ui_n_a_oi_missing_from_the_response_25')}</div>;
  const mx = Math.max(...pts.flatMap(p => [p.call_oi, p.put_oi].filter(finite)), 1);
  const bar = (v: unknown, color: string) => finite(v) ? <i style={{ width: (100 * v / mx) + '%', background: color }} />
    : <em className="vdn-hole-chip" style={{ width: '100%' }} data-vol-oi-na />;
  return (
    <div style={{ padding: '6px 12px 8px', display: 'flex', flexDirection: 'column', gap: 5 }}>
      {pts.map((p, i) => (
        <div key={i} className="num" style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}
             title={`${p.expiry} · put OI ${p.put_oi ?? tr('voldeck.ui_n_a_15')} · call OI ${p.call_oi ?? tr('voldeck.ui_n_a_15')} · P/C ${p.pc_oi_ratio ?? tr('voldeck.ui_n_a_15')}`}>
          <span style={{ width: 48, fontWeight: 600, color: 'var(--bbn-muted)' }}>{p.expiry.slice(8, 10)}/{p.expiry.slice(5, 7)}</span>
          <span style={{ flex: 1, display: 'flex', justifyContent: 'flex-end', height: 6, background: 'var(--bbn-raised)' }}>
            {bar(p.put_oi, 'var(--bbn-bad)')}
          </span>
          <span style={{ flex: 1, display: 'flex', height: 6, background: 'var(--bbn-raised)' }}>
            {bar(p.call_oi, 'var(--bbn-good)')}
          </span>
          <span style={{ width: 48, textAlign: 'right', color: p.pc_oi_ratio != null && p.pc_oi_ratio > 1.5 ? 'var(--bbn-warn)' : 'var(--bbn-muted)' }}>
            {p.pc_oi_ratio != null ? p.pc_oi_ratio.toFixed(2) : tr('voldeck.ui_n_a_15')}
          </span>
        </div>
      ))}
      <div className="num" style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, fontWeight: 600, color: 'var(--bbn-muted)', letterSpacing: '.1em' }}>
        <span>◄ PUT OI (max {kfmt(mx)})</span><span>{tr('voldeck.ui_call_oi_right_column_p_c_26')}</span>
      </div>
    </div>
  );
}

/* ============================================================
   VOL CONE — endpoint (43) backend `GET /options/vol_cone/{ticker}`:
   realized vol per orizzonte (5/10/21/63 giorni di BORSA, percentili 1y)
   vs term structure implicita corrente; `confronto[]` = percentile
   dell'IV nella distribuzione realized della finestra abbinata
   (calendario→borsa ×252/365, fix M1 review). 404 = pre-riavvio,
   dichiarato: si accende da solo. Solo ticker della lista IV (SPY/MSTR
   oggi): fuori lista = error dichiarato dal backend, reso verbatim.
   ============================================================ */
function VolCone({ cone }: { cone: any }) {
  const [selW, setSelW] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  if (cone?.__loading) {
    return <div className="num" style={{ padding: '12px', fontSize: 12, fontWeight: 600, color: 'var(--bbn-muted)' }}>{tr('voldeck.ui_loading_volatility_cone_27')}</div>;
  }
  if (cone === undefined) {
    return <div className="num" style={{ padding: '12px 12px 14px', fontSize: 12, fontWeight: 600, color: 'var(--bbn-muted)', lineHeight: 1.7 }}>
      {tr('voldeck.ui_n_a_volatility_cone_endpoint_unavailable_28')}<br />
      <span style={{ color: 'var(--bbn-warn)' }}>{tr('voldeck.ui_the_requested_endpoint_returned_http_404_29')}</span>
    </div>;
  }
  if (!cone || cone.error) {
    return <div className="num" style={{ padding: '12px 12px 14px', fontSize: 12, color: 'var(--bbn-warn)', lineHeight: 1.7 }}>
      {tr(cone?.origin === 'client' ? 'voldeck.ui_cone_request_failed_client' : 'voldeck.ui_reported_by_the_backend_6')}{String(cone?.error || tr('voldeck.ui_empty_response_30'))}
    </div>;
  }
  const wins = (cone.realized?.windows || []).filter((w: any) => !w.error && w.current != null);
  const missing = (cone.realized?.windows || []).filter((w: any) => w.error);
  if (wins.length < 2) {
    return <div className="num" style={{ padding: '12px', fontSize: 12, color: 'var(--bbn-warn)' }}>
      {tr('voldeck.ui_n_a_31')}{missing.length ? String(missing[0].error) : tr('voldeck.ui_fewer_than_2_usable_realised_volatility_windows_32')}
    </div>;
  }
  // v2 (10/10): un percentile assente e' n.d., non «= °»
  const pctOf = (c: any) => finite(c.pct_realized_leq_iv) ? numText(c.pct_realized_leq_iv, 0) + '°' : tr('voldeck.ui_n_a_15');
  const ivPts = (!cone.implied?.error ? (cone.confronto || []) : []).filter((c: any) => c.atm_iv != null && c.window != null);
  const W = 1400, H = 350, L = 84, R = 36, T = 26, B = 44;
  const maxW = wins[wins.length - 1].window;
  const X = (w: number) => L + (W - L - R) * Math.sqrt(Math.max(0, w) / maxW);
  const allV = [...wins.flatMap((w: any) => [w.min, w.max, w.current]), ...ivPts.map((c: any) => c.atm_iv)]
    .filter((v: any) => v != null && isFinite(v)).map((v: number) => v * 100);
  const vmin = Math.min(...allV), vmax = Math.max(...allV);
  const pad = Math.max(0.5, (vmax - vmin) * 0.1);
  const Y = (v: number) => T + (H - T - B) * (1 - (v - (vmin - pad)) / ((vmax + pad) - (vmin - pad)));
  const area = (lo: string, hi: string) => 'M'
    + wins.map((w: any, i: number) => `${i ? 'L' : ''}${X(w.window).toFixed(1)},${Y(w[hi] * 100).toFixed(1)}`).join('')
    + [...wins].reverse().map((w: any) => `L${X(w.window).toFixed(1)},${Y(w[lo] * 100).toFixed(1)}`).join('') + 'Z';
  const line = (k: string) => wins.map((w: any, i: number) => `${i ? 'L' : 'M'}${X(w.window).toFixed(1)},${Y(w[k] * 100).toFixed(1)}`).join('');
  // assi a passo PULITO (multipli tondi, mai min/mid/max casuali)
  let step = 100;
  for (const c of [2, 5, 10, 20, 25, 50, 100]) { if ((vmax - vmin) / c <= 5) { step = c; break; } }
  const glines: number[] = [];
  for (let v = Math.ceil((vmin - pad) / step) * step; v <= vmax + pad; v += step) glines.push(v);
  // interazione: press/hover → finestra più vicina (stesso idioma dello X-RAY)
  const pick = (clientX: number) => {
    const el = svgRef.current; if (!el) return;
    const r = el.getBoundingClientRect();
    if (!(r.width > 0)) return;
    const vx = (clientX - r.left) / r.width * W;
    let best: number | null = null, bd = Infinity;
    wins.forEach((w: any) => { const d = Math.abs(X(w.window) - vx); if (d < bd) { bd = d; best = w.window; } });
    setSelW(best);
  };
  const sel = wins.find((w: any) => w.window === selW) || null;
  const selIvs = ivPts.filter((c: any) => c.window === selW);
  // marker IV nella stessa finestra: sfalsamento DETERMINISTICO per indice
  const seen: Record<number, number> = {};
  return (
    <>
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="vsxsvg" role="group" aria-roledescription={tr('voldeck.n_interactive_chart')}
           aria-label={tr('voldeck.ui_interactive_volatility_cone_realised_percentiles_by_ho_33')}
           tabIndex={0} onKeyDown={e => { if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); const at = wins.findIndex((w: any) => w.window === selW); setSelW(wins[Math.max(0, Math.min(wins.length - 1, (at < 0 ? 0 : at) + (e.key === 'ArrowLeft' ? -1 : 1)))].window); } }}
           style={{ cursor: 'crosshair', touchAction: 'none' }}
           onPointerDown={e => pick(e.clientX)}
           onPointerMove={e => { if (e.pointerType === 'mouse' || e.buttons > 0) pick(e.clientX); }}>
        {glines.map((v, i) => (
          <g key={i}>
            <line x1={L} x2={W - R} y1={Y(v)} y2={Y(v)} stroke="var(--vdn-grid)" strokeWidth="1" />
            <text x={L - 8} y={Y(v) + 4} fontSize="12" fontWeight={600} fill="var(--bbn-muted)" textAnchor="end">{v}%</text>
          </g>
        ))}
        <path d={area('min', 'max')} fill="var(--vdn-band-weak)" />
        <path d={area('p25', 'p75')} fill="var(--vdn-band)" />
        <path d={line('p50')} fill="none" stroke="var(--bbn-muted)" strokeWidth="1.3" strokeDasharray="6 4" />
        <path d={line('current')} fill="none" stroke="var(--bbn-accent)" strokeWidth="2.2" />
        {/* colonna selezionata: riga di fede oro */}
        {sel && (
          <g pointerEvents="none">
            <line x1={X(sel.window)} x2={X(sel.window)} y1={T} y2={H - B} stroke="var(--vdn-violet)" strokeWidth="1.2" strokeDasharray="5 4" />
            <circle cx={X(sel.window)} cy={Y(sel.current * 100)} r="7" fill="none" stroke="var(--vdn-violet)" strokeWidth="1.4" />
          </g>
        )}
        {wins.map((w: any, i: number) => (
          <g key={i}>
            <circle cx={X(w.window)} cy={Y(w.current * 100)} r="4" fill="var(--bbn-accent)" stroke="var(--bbn-card)" strokeWidth="1.2">
              <title>{tr('voldeck.fmt_realised_a_d_current_b_min_c_p_d_max_e_f_obs_g__2', {a: w.window, b: (w.current * 100).toFixed(1), c: (w.min * 100).toFixed(1), d: (w.p50 * 100).toFixed(1), e: (w.max * 100).toFixed(1), f: w.n_obs, g: w.young ? ' · YOUNG' : ''})}</title>
            </circle>
            <text x={X(w.window)} y={Y(w.current * 100) - 11} fontSize="12" fill="var(--bbn-accent)" textAnchor="middle">
              {(w.current * 100).toFixed(0)}%
            </text>
            <text x={X(w.window)} y={H - B + 17} fontSize="12" fill={w.young ? 'var(--bbn-warn)' : selW === w.window ? 'var(--bbn-text)' : 'var(--bbn-muted)'} textAnchor="middle">
              {w.window}{tr('voldeck.short_days')}{w.young ? '*' : ''}
            </text>
          </g>
        ))}
        {ivPts.map((c: any, i: number) => {
          const k = (seen[c.window] = (seen[c.window] ?? -1) + 1);
          const x = X(c.window) + k * 14;
          return (
            <g key={i} transform={`translate(${x},${Y(c.atm_iv * 100)})`} opacity={selW == null || c.window === selW ? 1 : 0.4}>
              <path d="M0,-6 L6,0 L0,6 L-6,0 Z" fill="var(--vdn-violet)" stroke="var(--bbn-card)" strokeWidth="1.2">
                <title>{tr('voldeck.fmt__a_b_calendar_days_c_trading_day_window_iv_d_e_t_3', {a: c.expiry, b: c.days, c: c.window, d: numText(c.atm_iv * 100, 1), e: finite(c.pct_realized_leq_iv) ? numText(c.pct_realized_leq_iv, 0) : tr('voldeck.ui_n_a_15')})}</title>
              </path>
              <text x={8} y={4} fontSize="10" fill="var(--vdn-violet)">{c.days}{tr('voldeck.short_days')}</text>
            </g>
          );
        })}
        <text x={W - R} y={T - 8} fontSize="12" fill="var(--vdn-violet)" textAnchor="end">{tr('voldeck.ui_atm_iv_by_expiry_placed_on_the_matching_window_34')}</text>
      </svg>
      {/* lettura interattiva: percentili completi della finestra selezionata */}
      <div className="num" aria-live="polite" style={{ display: 'flex', flexWrap: 'wrap', gap: '3px 16px', padding: '6px 10px 2px', fontSize: 12, alignItems: 'baseline', minHeight: 24 }}>
        {!sel ? (
          <span style={{ fontWeight: 600, color: 'var(--bbn-muted)', fontSize: 12, letterSpacing: '.08em' }}>{tr('voldeck.ui_point_or_use_arrow_keys_for_window_percentiles_and_mat_35')}</span>
        ) : (
          <>
            <span style={{ color: 'var(--vdn-violet)', fontWeight: 700 }}>{tr('voldeck.ui_window_36')}{' '}{sel.window}{tr('voldeck.short_days')}</span>
            <span style={{ color: 'var(--bbn-accent)', fontWeight: 700 }}>{tr('voldeck.ui_current_37')}{' '}{(sel.current * 100).toFixed(1)}%</span>
            <span style={{ color: 'var(--bbn-muted)' }}>MIN {(sel.min * 100).toFixed(1)} · P25 {(sel.p25 * 100).toFixed(1)} · <b>{tr('voldeck.ui_median_38')}{' '}{(sel.p50 * 100).toFixed(1)}</b> · P75 {(sel.p75 * 100).toFixed(1)} · MAX {(sel.max * 100).toFixed(1)}</span>
            <span style={{ fontWeight: 600, color: 'var(--bbn-muted)' }}>{sel.n_obs} {' '}{tr('voldeck.ui_1y_obs_39')}{sel.young ? ' · YOUNG (<60)' : ''}</span>
            {selIvs.map((c: any, i: number) => {
              const p = finite(c.pct_realized_leq_iv) ? c.pct_realized_leq_iv : null;
              const col = p == null ? 'var(--bbn-muted)' : p >= 80 ? 'var(--bbn-bad)' : p >= 60 ? 'var(--bbn-warn)' : p <= 20 ? 'var(--bbn-good)' : 'var(--bbn-muted)';
              return <span key={i} style={{ color: col }}>◆ {c.days}{tr('voldeck.ui_d_iv_40')}{' '}{numText(c.atm_iv * 100, 1)}% = <b>{pctOf(c)}</b> PCT</span>;
            })}
          </>
        )}
      </div>
      <div className="num" aria-live="polite" style={{ display: 'flex', flexWrap: 'wrap', gap: '3px 16px', padding: '2px 10px 2px', fontSize: 12, alignItems: 'baseline' }}>
        {ivPts.map((c: any, i: number) => {
          const p = finite(c.pct_realized_leq_iv) ? c.pct_realized_leq_iv : null;
          const col = p == null ? 'var(--bbn-muted)' : p >= 80 ? 'var(--bbn-bad)' : p >= 60 ? 'var(--bbn-warn)' : p <= 20 ? 'var(--bbn-good)' : 'var(--bbn-muted)';
          return (
            <span key={i} style={{ color: col }}>
              {c.days}{tr('voldeck.ui_d_iv_41')}{' '}{numText(c.atm_iv * 100, 1)}% = <b>{pctOf(c)}</b> {' '}{tr('voldeck.ui_realised_percentile_42')}{' '}{c.window}{tr('voldeck.short_days')}
            </span>
          );
        })}
        {cone.implied?.error && <span style={{ color: 'var(--bbn-warn)' }}>{tr('voldeck.ui_implied_n_a_43')}{' '}{String(cone.implied.error)}</span>}
        {/* contratto cbcfd0b: l'implied del cono e' una superficie CAMPIONATA riscaricata, non l'istantanea del download */}
        {cone.implied && !cone.implied.error && <span data-vol-cone-implied style={{ fontWeight: 600, color: cone.implied.coverage_complete === false ? 'var(--bbn-warn)' : 'var(--bbn-muted)' }}>
          {tr('voldeck.n_cone_implied_src', { t: nyTime(cone.implied.snapshot_at)?.text || tr('voldeck.ui_n_a_15') })}
          {cone.implied.coverage_complete === false ? ' · ' + tr('voldeck.n_cone_implied_partial', {
            p: (cone.implied.partial_expiries || []).length, x: (cone.implied.excluded_expiries || []).length, e: (cone.implied.error_expiries || []).length }) : ''}
        </span>}
        {missing.map((w: any, i: number) => <span key={'m' + i} style={{ fontWeight: 600, color: 'var(--bbn-muted)' }}>{w.window}{tr('voldeck.ui_d_44')}{' '}{String(w.error)}</span>)}
      </div>
    </>
  );
}

/* ============================================================
   GAMMA EXPOSURE // DEALER (richiesta PM 25/07 live) — il payload di
   oggi NON espone OI/gamma per strike: qui NIENTE numeri inventati.
   Pannello CABLATO sul contratto proposto alla chat backend nel ponte
   (campo `gex`: by_strike[]{strike,gex_1pct_usd,call_oi,put_oi} +
   flip_strike + net_gex_1pct_usd + basis + error): si accende DA SOLO
   al primo payload col campo — stesso pattern di fonti_mute F8.
   ============================================================ */
function GexProfile({ gex, spot }: { gex: any; spot?: number }) {
  if (gex === undefined) {
    return <div className="num" style={{ padding: '12px 12px 14px', fontSize: 12, fontWeight: 600, color: 'var(--bbn-muted)', lineHeight: 1.7 }}>
      {tr('voldeck.ui_n_a_gamma_oi_by_strike_missing_from_the_response_45')}<br />
      <span style={{ color: 'var(--bbn-warn)' }}>{tr('voldeck.ui_request_context_explicitly_to_check_gamma_exposure_fro_46')}</span>
    </div>;
  }
  if (!gex || gex.error) {
    return <div className="num" style={{ padding: '12px 12px 14px', fontSize: 12, color: 'var(--bbn-warn)', lineHeight: 1.7 }}>
      {tr('voldeck.ui_reported_by_the_backend_6')}{String(gex?.error || tr('voldeck.ui_empty_gex_47'))}
    </div>;
  }
  const rows = (gex.by_strike || []).filter((r: any) => r.strike != null && r.gex_1pct_usd != null);
  if (rows.length < 2) {
    return <div className="num" style={{ padding: '12px', fontSize: 12, fontWeight: 600, color: 'var(--bbn-muted)' }}>{tr('voldeck.ui_n_a_strike_data_empty_or_insufficient_48')}</div>;
  }
  const mx = Math.max(...rows.map((r: any) => Math.abs(r.gex_1pct_usd)), 1e-9);
  const flipRow = finite(gex.flip_strike) ? nearestIndex(rows.map((r: any) => Number(r.strike)), gex.flip_strike) : -1;
  const net = gex.net_gex_1pct_usd;
  return (
    <div style={{ padding: '6px 12px 8px', display: 'flex', flexDirection: 'column', gap: 4 }}>
      {net != null && (
        <div className="num" style={{ fontSize: 12, marginBottom: 2, fontWeight: 600, color: net >= 0 ? 'var(--bbn-good)' : 'var(--bbn-bad)' }}>
          NET GEX {net >= 0 ? '+' : ''}{kfmt(Math.abs(net))} $/1% · {net >= 0 ? tr('voldeck.ui_dealer_long_moves_dampened_49') : tr('voldeck.ui_dealer_short_moves_amplified_50')}
        </div>
      )}
      {rows.map((r: any, i: number) => {
        const neg = r.gex_1pct_usd < 0;
        const w = 50 * Math.abs(r.gex_1pct_usd) / mx;
        // il flip e' un punto medio fra strike (audit 09/10 M5): si marca lo strike listato piu' vicino
        const isFlip = finite(gex.flip_strike) && i === flipRow;
        const isSpot = spot != null && spot > 0 && Math.abs(r.strike - spot) / spot < 0.005;
        return (
          <div key={i} className="num" style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}
               title={`strike ${r.strike} · GEX ${r.gex_1pct_usd >= 0 ? '+' : ''}${kfmt(Math.abs(r.gex_1pct_usd))} $/1%` + (r.call_oi != null ? ` · call OI ${kfmt(r.call_oi)} · put OI ${r.put_oi == null ? tr('voldeck.ui_n_a_15') : kfmt(r.put_oi)}` : '')}>
            <span style={{ width: 56, color: isFlip ? 'var(--bbn-warn)' : isSpot ? 'var(--bbn-accent)' : 'var(--bbn-muted)', fontWeight: isFlip || isSpot ? 700 : 400 }}>
              {px(Number(r.strike))}{isFlip ? ' ⚑' : isSpot ? ' ◈' : ''}
            </span>
            <span style={{ flex: 1, height: 6, background: 'var(--bbn-raised)', position: 'relative' }}>
              <span style={{ position: 'absolute', left: '50%', top: 0, bottom: 0, width: 1, background: 'var(--vdn-grid)' }} />
              <i style={{ position: 'absolute', top: 0, bottom: 0, ...(neg ? { right: '50%', width: w + '%' } : { left: '50%', width: w + '%' }), background: neg ? 'var(--bbn-bad)' : 'var(--bbn-good)' }} />
            </span>
          </div>
        );
      })}
      <div className="num" style={{ fontSize: 12, fontWeight: 600, color: 'var(--bbn-muted)', letterSpacing: '.08em' }}>
        ⚑ ≈ gamma flip{finite(gex.flip_strike) ? ' ' + px(gex.flip_strike) + ' · ' + tr('voldeck.n_flip_midpoint') : ''} · ◈ = spot · {gex.basis || ''}
      </div>
    </div>
  );
}

/* ============================================================
   VISTA NUOVA — tessere, tabella skew e pagina.
   ============================================================ */
function Tile({ label, value, sub, tone, title }: { label: string; value: string; sub?: string; tone?: 'good' | 'bad' | 'warn'; title?: string }) {
  return <div className="vdn-tile" title={title}><span>{label}</span><b className={tone ? 'is-' + tone : undefined}>{value}</b>{sub && <small>{sub}</small>}</div>;
}

/* TERM STRUCTURE // ATM + SKEW 25Δ — un valore mancante resta n.d.: niente barra a zero, niente «0,0%». */
function SkewTable({ term, note }: { term: any[]; note?: string | null }) {
  const na = tr('voldeck.ui_n_a_15');
  const ivs = term.map(s => s.atm_iv).filter(finite), rrs = term.map(s => s.rr25).filter(finite).map(Math.abs);
  const maxIv = ivs.length ? Math.max(...ivs) : null, maxRr = rrs.length ? Math.max(...rrs) : null;
  const pts = (v: unknown) => finite(v) ? (v > 0 ? '+' : '') + (v * 100).toLocaleString(localeDi(linguaCorrente()), { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' pt' : na;
  return <>
    <div className="vdn-table-wrap"><table className="vdn-table num">
      <thead><tr>
        <th className="l">{tr('voldeck.ui_expiry_177')}</th><th>{tr('voldeck.ui_days_97')}</th><th>ATM IV</th>
        <th aria-hidden="true" />
        <th>RR 25Δ</th><th className="c">SKEW</th><th>BF 25Δ</th><th>P/C OI</th>
      </tr></thead>
      <tbody>{term.map(s => <tr key={s.expiry}>
        <td className="l">{s.expiry}</td><td>{s.days}</td>
        <td className={finite(s.atm_iv) ? 'is-acc' : 'is-na'}>{ivText(s.atm_iv, na)}</td>
        <td className="bar">{finite(s.atm_iv) && maxIv ? <i style={{ width: `${Math.max(3, 100 * s.atm_iv / maxIv)}%` }} /> : <em className="vdn-hole-chip" />}</td>
        <td className={!finite(s.rr25) ? 'is-na' : s.rr25 < 0 ? 'is-bad' : 'is-good'}>{pts(s.rr25)}</td>
        <td className="c">{finite(s.rr25) && maxRr ? <span className="vdn-bip" title={s.rr25 < 0 ? 'put skew' : 'call skew'}>
          <i className={s.rr25 < 0 ? 'is-bad' : 'is-good'} style={s.rr25 < 0 ? { right: '50%', width: `${50 * Math.abs(s.rr25) / maxRr}%` } : { left: '50%', width: `${50 * s.rr25 / maxRr}%` }} />
        </span> : null}</td>
        <td className={finite(s.bf25) ? undefined : 'is-na'}>{pts(s.bf25)}</td>
        <td className={finite(s.pc_oi_ratio) && s.pc_oi_ratio > 1.5 ? 'is-warn' : finite(s.pc_oi_ratio) ? undefined : 'is-na'}>{finite(s.pc_oi_ratio) ? s.pc_oi_ratio.toFixed(2) : na}</td>
      </tr>)}</tbody>
    </table></div>
    {note && <p className="vdn-legend"><b>{tr('voldeck.ui_skew_note_src_builder_98')}</b> {note}</p>}
    <p className="vdn-legend">{tr('voldeck.ui_composite_otm_puts_below_spot_calls_above_k_s_grid_0_8_99')}</p>
  </>;
}

/* METODO E LIMITI — le definizioni dichiarate dal builder (contratto cbcfd0b), una per riga; un campo
   assente e' «non dichiarato», mai una definizione scritta qui al posto del backend. */
const QUALITY_REASONS = ['no_bid', 'crossed', 'wide_spread', 'stale', 'iv_out_of_range'] as const;
function MethodNotes({ data }: { data: any }) {
  const missing = tr('voldeck.n_method_missing');
  const q = data?.quality_filters;
  const filters = q && typeof q === 'object' ? [
    finite(q.max_rel_spread) ? tr('voldeck.n_filter_spread', { v: numText(q.max_rel_spread, 2) }) : null,
    finite(q.stale_quote_seconds) ? tr('voldeck.n_filter_stale', { v: numText(q.stale_quote_seconds / 60, 0) }) : null,
    Array.isArray(q.iv_range) && q.iv_range.length === 2 ? tr('voldeck.n_filter_iv', { a: ivText(q.iv_range[0], missing), b: ivText(q.iv_range[1], missing, 0) }) : null,
  ].filter(Boolean).join(' · ') : '';
  const items = [
    ['n_m_moneyness', data?.moneyness_basis], ['n_m_atm', data?.atm_method], ['n_m_rr25', data?.rr25_method],
    ['n_m_smoothing', data?.smoothing], ['n_m_cm', data?.iv_constant_maturity_method], ['n_m_expected', data?.expected_move_basis],
    ['n_m_iv_model', data?.iv_model_note], ['n_m_spot', data?.spot_basis],
    ['n_m_quality', [typeof q?.rules === 'string' ? q.rules : null, filters || null].filter(Boolean).join(' — ') || null],
    ['n_m_delay', data?.data_delay_note],
  ] as const;
  return <dl className="vdn-method-list" data-vol-method>
    {items.map(([key, value]) => <div key={key}><dt>{tr(`voldeck.${key}`)}</dt>
      <dd className={typeof value === 'string' && value.trim() ? undefined : 'is-na'}>{typeof value === 'string' && value.trim() ? value : missing}</dd></div>)}
  </dl>;
}

export default function VolSurfacePage() {
  const t = useT(), language = useLingua();
  const themeDark = useInterfaceTheme().effective === 'dark';
  const [ticker, setTicker] = useState('');
  const [input, setInput] = useState('');
  const [workspace, setWorkspace] = useState<VolWorkspace>('acquisition');
  const [surfaceSnapshot, setData] = useState<any>(null);
  const rawData = useMemo(() => localizePayload(surfaceSnapshot, language), [surfaceSnapshot, language]);
  const [selectedExpiries, setSelectedExpiries] = useState<string[] | null>(null);
  const data = useMemo(() => visibleSurface(rawData, selectedExpiries), [rawData, selectedExpiries]);
  const [contextState, setContextState] = useState<'not_requested' | 'loading' | 'loaded' | 'error'>('not_requested');
  const [coneSnapshot, setCone] = useState<any>(null);
  const cone = useMemo(() => localizePayload(coneSnapshot, language), [coneSnapshot, language]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestRef = useRef<AbortController | null>(null);
  const lastExpiries = useRef<string[]>([]);
  // stato della presentazione Nuova (in coda: gli useState sopra restano nell'ordine di prima)
  const [downloadId, setDownloadId] = useState<string | null>(null);
  const [axis, setAxis] = useState<AxisMode>('moneyness');
  const [pickedExpiry, setPickedExpiry] = useState<string | null>(null);
  const [pickedColumn, setPickedColumn] = useState<number | null>(null);
  const [resetKey, setResetKey] = useState(0);
  const [plotError, setPlotError] = useState<string | null>(null);
  // ALTA-1 (v2 10/10): da quale istantanea viene il contesto mostrato
  const [contextOrigin, setContextOrigin] = useState<{ kind: 'download'; at: string | null } | { kind: 'new_fetch'; at: string | null; previous: string | null } | null>(null);

  useEffect(() => {
    requestRef.current?.abort(); setData(null); setError(null); setLoading(false); lastExpiries.current = [];
    setCone(null); setContextState('not_requested'); setSelectedExpiries(null);
    setDownloadId(null); setPickedExpiry(null); setPickedColumn(null); setPlotError(null); setContextOrigin(null);
    return () => requestRef.current?.abort();
  }, [ticker]);

  // `path` (v2): la rotta del contesto di un download; senza, la rotta che RISCARICA la superficie.
  const loadSurface = async (expiries: string[], context = false, path?: string) => {
    requestRef.current?.abort(); const controller = new AbortController(); requestRef.current = controller;
    setLoading(true); setError(null); lastExpiries.current = expiries;
    if (context) setContextState('loading');
    try {
      const result = await volRequest<any>(path ?? `/options/vol_surface/${encodeURIComponent(ticker)}?expiries=${encodeURIComponent(expiries.join(','))}&include_context=${context}`, undefined, controller.signal);
      if (controller.signal.aborted) return;
      if (result.error) throw new Error(result.error);
      setData(result); if (context) setContextState('loaded');
      if (context) {
        setCone({ __loading: true });
        try {
          const resultCone = await volRequest<any>(`/options/vol_cone/${encodeURIComponent(ticker)}`, undefined, controller.signal);
          if (!controller.signal.aborted) setCone(resultCone);
        } catch (e) { if (!controller.signal.aborted) setCone({ error: e instanceof Error ? e.message : String(e), origin: (e as { origin?: string } | null)?.origin === 'backend' ? 'backend' : 'client' }); }
      } else {
        setCone(null); setContextState('not_requested');
      }
      return result;
    } catch (e) { if (!controller.signal.aborted) { setError(e instanceof Error ? e.message : String(e)); if (context) setContextState('error'); } }
    finally { if (!controller.signal.aborted) setLoading(false); }
  };

  /* ALTA-1: «carica contesto». Con un download il contesto si calcola sulla SUA istantanea
     (GET /options/download/{id}/context: stessa superficie, stesse quote, nessuna chain riscaricata).
     Senza download si riscarica la superficie: istantanea NUOVA, dichiarata con i due orari, e le quote
     misurate (di un'altra istantanea) si azzerano. Oltre 12 scadenze la rotta che riscarica rifiuta (422):
     lo si dichiara prima di chiedere. */
  const loadContext = async () => {
    if (downloadId) {
      const result = await loadSurface(lastExpiries.current, true, `/options/download/${encodeURIComponent(downloadId)}/context`);
      if (result) setContextOrigin({ kind: 'download', at: result.snapshot_at ?? result._timestamp ?? null });
      return;
    }
    if (lastExpiries.current.length > MAX_REFETCH_EXPIRIES) {
      setContextState('error');
      setError(tr('voldeck.n_ctx_too_many', { n: lastExpiries.current.length, max: MAX_REFETCH_EXPIRIES }));
      return;
    }
    const previous = rawData?.snapshot_at ?? rawData?._timestamp ?? null;
    const result = await loadSurface(lastExpiries.current, true);
    if (result) { observedChains.reset(); setContextOrigin({ kind: 'new_fetch', at: result.snapshot_at ?? result._timestamp ?? null, previous }); }
  };

  const go = () => { const t = input.trim().toUpperCase(); if (/^[A-Z0-9][A-Z0-9.\-^]{0,24}$/.test(t)) setTicker(t); else setError(tr('voldeck.ui_enter_a_valid_ticker_before_loading_the_catalogue_54')); };

  // ── modello della superficie e fette scelte (derivati: nessun effetto che riscrive lo stato) ──
  const partial = useMemo(() => partialExpiries(rawData?.coverage), [rawData]);
  const model = useMemo(() => surfaceModel(data, partial), [data, partial]);
  // scelta iniziale: la scadenza piu' vicina a 30 giorni (stessa convenzione dell'expected move del builder)
  const expiry = model.rows.some(r => r.expiry === pickedExpiry) ? pickedExpiry
    : model.rows[nearestIndex(model.rows.map(r => r.days), 30)]?.expiry ?? null;
  const column = pickedColumn != null && pickedColumn >= 0 && pickedColumn < model.grid.length ? pickedColumn
    : model.grid.length ? atmIndex(model.grid) : null;
  const row = model.rows.find(r => r.expiry === expiry) || null;

  // ── quote MISURATE: chain del job letta tutta a pagine (memoria del backend, nessuna richiesta al fornitore) ──
  const rowExpiries = useMemo(() => model.rows.map(r => r.expiry), [model]);
  const observedChains = useObservedChains(downloadId, rowExpiries, workspace === 'tools');
  const chainOf = observedChains.of;
  const maxRelSpread = finite(rawData?.quality_filters?.max_rel_spread) ? rawData.quality_filters.max_rel_spread : DEFAULT_MAX_REL_SPREAD;
  const observedFull = useMemo(() => {
    const out: Record<string, ReturnType<typeof observedQuotes> | undefined> = {};
    for (const r of model.rows) { const c = observedChains.of(r.expiry); if (c?.state === 'ok') out[r.expiry] = observedQuotes(c.chain, model.spot, maxRelSpread); }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model, observedChains.chains, downloadId, maxRelSpread]);
  const observed = useMemo(() => Object.fromEntries(Object.entries(observedFull).map(([e, o]) => [e, o?.quotes])) as Record<string, ObservedQuote[] | undefined>, [observedFull]);
  const chainStates = model.rows.map(r => chainOf(r.expiry));
  const nLoaded = chainStates.filter(c => c?.state === 'ok').length;
  const nLoading = chainStates.filter(c => c?.state === 'loading').length;
  const chainErrors = model.rows.map(r => ({ e: r.expiry, c: chainOf(r.expiry) })).filter(x => x.c?.state === 'error') as { e: string; c: { state: 'error'; error: string } }[];
  const chainNotes = model.rows.flatMap(r => { const c = chainOf(r.expiry); return c?.state === 'ok' && (c.truncated || c.chainComplete === false || c.rowError) ? [{ e: r.expiry, c }] : []; });
  const lo = model.grid[0], hi = model.grid[model.grid.length - 1];
  const inGrid = (q: ObservedQuote) => q.otm && q.m >= lo - 1e-9 && q.m <= hi + 1e-9;
  const nMeasured = model.rows.reduce((a, r) => a + (observed[r.expiry] || []).filter(q => inGrid(q) && q.liquid && !q.flagged).length, 0);
  const nFlagged = model.rows.reduce((a, r) => a + (observed[r.expiry] || []).filter(q => inGrid(q) && q.flagged).length, 0);
  const nAdjustedObs = Object.values(observedFull).reduce((a, o) => a + (o?.adjusted || 0), 0);
  const nClipped3d = useMemo(() => surfaceQuoteScale(model, observed).clipped, [model, observed]);
  const nOutside = model.rows.reduce((a, r) => a + (observed[r.expiry] || []).filter(q => q.otm && (q.m < lo - 1e-9 || q.m > hi + 1e-9)).length, 0);
  const selQuotes = expiry ? (observed[expiry] ?? null) : null;
  const selStrike = column != null ? model.strikes[column] : null;
  const near = selQuotes ? nearestQuotes(selQuotes, selStrike) : null;
  const strikeAxis = model.spot != null;

  const pick = (e: string, c: number) => { setPickedExpiry(e); setPickedColumn(c); };
  const stepExpiry = (step: -1 | 1) => {
    const i = model.rows.findIndex(r => r.expiry === expiry);
    const next = model.rows[Math.max(0, Math.min(model.rows.length - 1, (i < 0 ? 0 : i) + step))];
    if (next) setPickedExpiry(next.expiry);
  };

  // ── testata: istantanea, fuso New York, seduta, ritardo; numeri di sintesi (nomi del contratto cbcfd0b) ──
  const na = t('voldeck.na');
  const ivrv = data?.iv_rv_spread_30d;
  const ivrvTone = !finite(ivrv) ? undefined : ivrv > 0.03 ? 'bad' : ivrv < -0.03 ? 'good' : undefined;
  const slope = data?.term_slope_front_to_60d;
  const fresh = surfaceFreshness(rawData);
  const snap = nyTime(fresh.snapshotAt);
  const quoteFrom = nyTime(fresh.quoteFrom), quoteTo = nyTime(fresh.quoteTo, true), spotAt = nyTime(fresh.spotAt);
  const discards = discardTotals(data?.slices || []);
  const nOpt = (data?.slices || []).reduce((a: number, s: any) => a + (finite(s.n_calls) ? s.n_calls : 0) + (finite(s.n_puts) ? s.n_puts : 0), 0);
  const nIll = (data?.slices || []).reduce((a: number, s: any) => a + (finite(s.n_illiquidi_esclusi) ? s.n_illiquidi_esclusi : 0), 0);
  const sources = rawData?.context_sources && typeof rawData.context_sources === 'object' ? rawData.context_sources : null;
  const front = model.rows[0] || null;
  const workspaces = ['acquisition', 'tools', 'chain', 'laboratory'] as const;

  return (
    <ModernPage page="vol" presentationBoundary={false} render={() => (
    <div className={'bbn-vol bbn-font' + (themeDark ? ' is-dark' : '')} data-vol-atlas data-workspace={workspace}>
      <VolPagePresentationBoundary render={() => <>
      <header className="vdn-top">
        <div className="vdn-title">
          <h1>{t('voldeck.atlas_title')}</h1>
          <p>{t('voldeck.atlas_intro')}</p>
        </div>
        <span className="bbn-grow" />
        <form className="va-ticker vdn-ticker" onSubmit={e => { e.preventDefault(); go(); setWorkspace('acquisition'); }}>
          <label htmlFor="va-ticker">{t('voldeck.underlying')}</label>
          <input id="va-ticker" value={input} onChange={e => setInput(e.target.value.toUpperCase())}
            spellCheck={false} placeholder="SYNTH" autoComplete="off" />
          <button type="submit" className="bbn-btn is-primary" disabled={loading}>{t('voldeck.catalog')}</button>
        </form>
      </header>
      <nav className="bbn-seg is-large vdn-tabs" aria-label={t('voldeck.workspace')}>
        {workspaces.map(mode => <button key={mode} type="button" aria-pressed={workspace === mode} className={workspace === mode ? 'is-on' : undefined}
          onClick={() => setWorkspace(mode)} data-vol-workspace={mode}>{t(`voldeck.${mode}`)}</button>)}
      </nav>
      {rawData && <div className="va-provenance vdn-chips" data-vol-provenance>
        <strong className="vdn-ticker-chip">{rawData.ticker || ticker}</strong>
        <span className="fat-chip">{t('voldeck.spot')} <b>{finite(rawData.spot_est) ? priceText(rawData.spot_est, na) : na}</b>
          {' · '}<span className={fresh.spotProxy || fresh.spotFallback ? 'vdn-warn-text' : undefined}>{rawData.spot_source || t('voldeck.source_unknown')}</span>
          {spotAt && fresh.spotQualified && <> · {tr('voldeck.n_spot_time', { t: spotAt.text })}</>}</span>
        {/* spot non qualificato: ripiego yfinance (senza orario della quota) o proxy di uno strike — dichiarato */}
        {fresh.spotFallback && !fresh.spotProxy && <span className="fat-pill is-warn" data-vol-spot-fallback title={rawData.spot_basis || undefined}>
          {tr('voldeck.n_spot_fallback', { t: spotAt?.text || na })}</span>}
        {fresh.spotProxy && <span className="fat-pill is-warn" data-vol-spot-proxy title={rawData.spot_basis || undefined}>{tr('voldeck.n_spot_proxy')}</span>}
        {!fresh.spotQualified && !fresh.spotFallback && !fresh.spotProxy && finite(rawData.spot_est) && <span className="fat-pill is-warn">{tr('voldeck.n_spot_unqualified')}</span>}
        <span className="fat-chip" title={snap && !snap.zoned ? tr('voldeck.n_no_tz_hint') : undefined} data-vol-snapshot>
          {snap ? <>{tr(fresh.snapshotKind === 'download_snapshot' ? 'voldeck.n_snapshot_download' : fresh.snapshotKind === 'new_fetch' ? 'voldeck.n_snapshot_new' : 'voldeck.n_snapshot', { t: snap.text })}
            {!snap.zoned && <span className="vdn-warn-text"> · {tr('voldeck.n_no_tz')}</span>}</> : t('voldeck.time_unknown')}</span>
        {(quoteFrom || quoteTo) && <span className="fat-chip" data-vol-quote-times>{tr('voldeck.n_quotes_time', { a: quoteFrom?.text || na, b: quoteTo?.text || na })}</span>}
        {fresh.delay === 'DELAYED' ? <span className="fat-pill is-warn" title={fresh.delayNote || undefined} data-vol-delay>{tr('voldeck.n_delay_delayed')}</span>
          : fresh.delay === 'REAL-TIME' ? <span className="fat-chip" data-vol-delay>{tr('voldeck.n_delay_realtime')}</span>
          : fresh.delay === 'MIXED' ? <span className="fat-pill is-warn" title={fresh.delayNote || undefined} data-vol-delay>{tr('voldeck.n_delay_mixed')}</span>
          : rawData.slices?.length ? <span className="fat-pill is-warn" title={fresh.delayNote || undefined} data-vol-delay>{tr('voldeck.n_delay_unknown')}</span> : null}
        {fresh.session && <span className={fresh.marketOpen ? 'fat-chip' : 'fat-pill is-warn'} title={fresh.sessionNote || undefined} data-vol-session>
          {tr('voldeck.n_session', { d: fresh.session })}{fresh.marketOpen === false ? ' · ' + tr('voldeck.n_market_closed') : fresh.marketOpen ? ' · ' + tr('voldeck.n_market_open') : ''}</span>}
        {(partial.length > 0 || fresh.partial) && <span className="fat-pill is-warn" title={partial.join(', ') || undefined} data-vol-partial>
          {partial.length ? tr('voldeck.n_partial_count', { n: partial.length }) : tr('voldeck.n_partial_surface')}</span>}
        {fresh.stoppedAfterRateLimit && <span className="fat-pill is-warn">{tr('voldeck.n_rate_limited')}</span>}
        <span className="fat-chip vdn-method" title={rawData.smoothing || undefined}>{rawData.smoothing || t('voldeck.method_unknown')}</span>
      </div>}
      {rawData && <div className="vdn-tiles">
        <Tile label={tr('voldeck.n_tile_front')} value={ivText(front?.atm, na)}
          sub={front ? `${front.expiry} · ${front.days}${tr('voldeck.short_days')}${front.atm == null && front.atmUnqualified != null ? ' · ' + tr('voldeck.n_unqualified', { v: ivText(front.atmUnqualified, na) }) : ''}` : na} title={tr('voldeck.n_term_atm')} />
        <Tile label={t('voldeck.structure')} value={!finite(slope) ? na : slope < 0 ? 'Backwardation' : 'Contango'}
          sub={!finite(slope) ? (rawData.term_slope_reason || na) : numText(slope * 100, 1) + ' pt · ' + t('voldeck.front_60')}
          title={!finite(slope) ? rawData.term_slope_reason || undefined : undefined} />
        <Tile label={t('voldeck.expected_move')} value={!finite(rawData.expected_move_pct) ? na : `±${numText(rawData.expected_move_pct, 1)}%`}
          sub={!finite(rawData.expected_move_days) ? (rawData.expected_move_basis || na) : tr('voldeck.n_expected_sub', { n: rawData.expected_move_days, b: rawData.expected_move_basis || na })}
          title={rawData.expected_move_basis || undefined} />
        <Tile label={tr('voldeck.n_tile_holes')} value={String(model.holes)} tone={model.holes ? 'warn' : undefined}
          sub={tr('voldeck.n_tile_holes_sub', { f: model.filledCells, c: model.cells })} />
        <Tile label={tr('voldeck.n_tile_measured')} value={downloadId ? String(nMeasured) : na}
          sub={!downloadId ? tr('voldeck.n_observed_none') : nLoading ? tr('voldeck.n_observed_loading') : tr('voldeck.n_tile_measured_sub', { e: nLoaded, n: model.rows.length })} />
      </div>}
      {workspace === 'tools' && rawData && <section className="bbn-card vdn-filter" aria-labelledby="va-display-title">
        <header className="bbn-card-head"><h2 id="va-display-title">{t('voldeck.display_sample')}</h2>
          <span className="bbn-card-count">{t('voldeck.selected_count', { n: data?.slices?.length || 0, total: surfaceExpiries(rawData).length })}</span>
          <span className="bbn-grow" />
          <button type="button" className="bbn-link" onClick={() => setSelectedExpiries(null)}>{t('voldeck.all_dates')}</button>
          <button type="button" className="bbn-link" onClick={() => setSelectedExpiries([])}>{t('voldeck.clear_dates')}</button>
        </header>
        <div className="vdn-filter-body">
          <div className="va-expiries vdn-expiry-chips">{surfaceExpiries(rawData).map(e => <button key={e} type="button" className="bbn-toggle-chip"
            aria-pressed={selectedExpiries === null || selectedExpiries.includes(e)}
            onClick={() => setSelectedExpiries(value => toggleExpiry(value, surfaceExpiries(rawData), e))}>{e}{partial.includes(e) ? ' ⚠' : ''}</button>)}</div>
          <p className="vdn-legend">{t('voldeck.display_only')}</p>
          {!data?.slices?.length && <p role="status" className="fat-note is-warn">{t('voldeck.no_selected')}</p>}
        </div>
      </section>}
      </>} />
      <div className="vol-atlas vdn-workbench-slot">
        <VolWorkbench ticker={ticker} mode={workspace} coverage={rawData?.coverage} surfaceBusy={loading}
          onSurface={(result, expiries) => {
            requestRef.current?.abort(); setLoading(false); setData(result); setError(result.error || null);
            lastExpiries.current = expiries; setWorkspace('tools'); setSelectedExpiries(null);
            setCone(null); setContextState('not_requested');
            setDownloadId(typeof result?.download_id === 'string' ? result.download_id : null); setContextOrigin(null);
            setPickedExpiry(null); setPickedColumn(null); setPlotError(null);
          }} onLaboratory={() => setWorkspace('chain')} onAcquisition={() => setWorkspace('acquisition')} />
      </div>
      <VolPagePresentationBoundary render={() => <>
      {workspace === 'tools' && !rawData && <section className="bbn-card vdn-empty">
        <h2>{t('voldeck.tools_empty')}</h2><p>{t('voldeck.tools_empty_help')}</p>
        <button type="button" className="bbn-btn is-primary" onClick={() => setWorkspace('acquisition')}>{t('voldeck.acquisition')}</button>
      </section>}
      {loading && <div className="fat-note is-plain" role="status"><span className="txt">
        {tr('voldeck.ui_loading_selected_chains_57')}{' '}{ticker} {' '}{tr('voldeck.ui_polygon_opra_unrequested_dates_are_not_loaded_58')}</span></div>}
      {error && <div className="fat-note is-bad" role="alert"><span className="txt">{tr('voldeck.ui_reported_error_60')}{' '}{error}</span></div>}
      {plotError && <div className="fat-note is-bad" role="alert"><span className="txt">{plotError}</span></div>}

      {workspace === 'tools' && model.rows.length > 0 && <>
        {/* ══ SUPERFICIE 3D + PUNTO SCELTO ══ */}
        <div className="vdn-stage">
          <Card className="vdn-surface-card" titolo={tr('voldeck.n_surface_title')}
            conteggio={tr('voldeck.n_surface_count', { r: model.rows.length, c: model.cells, h: model.holes })}
            azioni={<>
              <div className="bbn-seg" role="group" aria-label={tr('voldeck.n_axis_mode')}>
                <button type="button" aria-pressed={axis === 'moneyness'} className={axis === 'moneyness' ? 'is-on' : undefined} onClick={() => setAxis('moneyness')}>K/S</button>
                <button type="button" aria-pressed={axis === 'strike'} className={axis === 'strike' ? 'is-on' : undefined} disabled={!strikeAxis}
                  title={strikeAxis ? undefined : tr('voldeck.n_strike_unavailable')} onClick={() => setAxis('strike')}>Strike</button>
              </div>
              <button type="button" className="bbn-btn vdn-small-btn" onClick={() => setResetKey(k => k + 1)}>{tr('voldeck.n_reset_view')}</button>
            </>}>
            <div className="vdn-plot-wrap">
              <Surface3D model={model} axis={axis === 'strike' && strikeAxis ? 'strike' : 'moneyness'} expiry={expiry} column={column}
                observed={observed} dark={themeDark} onPick={pick} onError={setPlotError} resetKey={resetKey} />
              <div id="vol-surface-expiry-axis-label" data-vol-axis-title="expiry" className="vdn-axis-note">
                {tr('voldeck.n_axes_note', { d: tr('voldeck.ui_days_to_expiry_53') })}
              </div>
            </div>
            <div className="vdn-keyrow" aria-label={tr('voldeck.n_legend_aria')}>
              <span><i className="vdn-key is-surface" />{tr('voldeck.n_legend_grid')}</span>
              <span><i className="vdn-key is-obs" />{tr('voldeck.n_legend_observed')}</span>
              <span><i className="vdn-key is-illiquid" />{tr('voldeck.n_legend_illiquid')}</span>
              <span><i className="vdn-key is-flag" />{tr('voldeck.n_legend_flagged')}</span>
              <span><i className="vdn-key is-cell" />{tr('voldeck.n_legend_cells')}</span>
              <span><i className="vdn-key is-hole">×</i>{tr('voldeck.n_legend_hole')}</span>
              <span><i className="vdn-key is-sel-expiry" />{tr('voldeck.n_legend_sel_expiry')}</span>
              <span><i className="vdn-key is-col" />{tr('voldeck.n_legend_sel_col')}</span>
            </div>
            <div className="vdn-notes">
              <p>{tr('voldeck.n_drag_hint')}</p>
              <p>{tr('voldeck.n_smoothing_note', { m: rawData?.smoothing || t('voldeck.method_unknown') })} {tr('voldeck.ui_colour_saturated_above_the_99th_percentile_geometry_un_69')}{data?._source || tr('voldeck.n_source_na')}]</p>
              {model.excludedShort.length > 0 && <p>{tr('voldeck.n_short_excluded', { n: model.excludedShort.length })}</p>}
              {nOpt > 0 && <p data-vol-quality>{tr('voldeck.n_quotes_used', { n: nOpt, i: nIll })}
                {discards.total ? ' · ' + tr('voldeck.n_quality_excluded', { n: discards.total }) + ' (' + QUALITY_REASONS.filter(k => discards.byReason[k])
                  .map(k => tr(`voldeck.n_reason_${k}`) + ' ' + discards.byReason[k])
                  .concat(Object.keys(discards.byReason).filter(k => !(QUALITY_REASONS as readonly string[]).includes(k)).map(k => k + ' ' + discards.byReason[k])).join(', ') + ')' : ''}
                {discards.adjusted ? ' · ' + tr('voldeck.n_adjusted_excluded', { n: discards.adjusted }) : ''}</p>}
              {!downloadId ? <p className="vdn-warn-text">{tr('voldeck.n_observed_none')}</p>
                : nLoading ? <p>{tr('voldeck.n_observed_loading')}</p>
                : <p>{tr('voldeck.n_observed_count', { n: nMeasured, e: nLoaded })}{nFlagged ? ' · ' + tr('voldeck.n_flagged_count', { n: nFlagged }) : ''}{nAdjustedObs ? ' · ' + tr('voldeck.n_adjusted_hidden', { n: nAdjustedObs }) : ''}{nOutside ? ' · ' + tr('voldeck.n_out_of_grid', { n: nOutside, a: numText(lo, 2), b: numText(hi, 2) }) : ''}{nClipped3d ? ' · ' + tr('voldeck.n_surface_clipped', { n: nClipped3d }) : ''}</p>}
              {chainErrors.map(x => <p key={x.e} className="vdn-warn-text">{tr('voldeck.n_observed_error', { e: x.e, err: x.c.error })}</p>)}
              {chainNotes.map(x => <p key={x.e} className="vdn-warn-text">{x.c.truncated ? tr('voldeck.n_observed_truncated', { e: x.e, n: x.c.chain.length })
                : tr('voldeck.n_observed_partial', { e: x.e, n: x.c.chain.length, why: x.c.rowError || tr('voldeck.n_chain_incomplete') })}</p>)}
            </div>
          </Card>

          <Card className="vdn-side-card" titolo={tr('voldeck.n_sel_title')}>
            <div className="vdn-side-body">
              <div className="vdn-expiry-list" role="listbox" aria-label={tr('voldeck.n_sel_expiries')}>
                {model.rows.map(r => <button key={r.expiry} type="button" role="option" aria-selected={r.expiry === expiry}
                  className={'vdn-expiry' + (r.expiry === expiry ? ' is-on' : '')} onClick={() => setPickedExpiry(r.expiry)}>
                  <b>{r.expiry}</b><span>{r.days}{tr('voldeck.short_days')}</span>
                  {r.partial && <em className="fat-pill is-warn">{tr('voldeck.n_partial_badge')}</em>}
                  <span className="vdn-expiry-iv" title={r.atm == null && r.atmUnqualified != null ? tr('voldeck.n_unqualified', { v: ivText(r.atmUnqualified, na) }) : undefined}>{ivText(r.atm, na)}</span>
                </button>)}
              </div>
              {row && <PointReadout model={model} row={row} column={column} />}
              {row?.partial && <p className="fat-note is-warn"><span className="txt">{tr('voldeck.n_partial_note')}</span></p>}
              <div className="vdn-quotes">
                <h3>{tr('voldeck.n_sel_quotes')}</h3>
                {!downloadId ? <p className="vdn-legend">{tr('voldeck.n_observed_none')}</p>
                  : chainOf(expiry)?.state === 'loading' || !chainOf(expiry) ? <p className="vdn-legend">{tr('voldeck.n_observed_loading')}</p>
                  : chainOf(expiry)?.state === 'error' ? <p className="vdn-warn-text">{tr('voldeck.n_observed_error', { e: expiry || '', err: (chainOf(expiry) as any).error })}</p>
                  : !near || near.strike == null ? <p className="vdn-legend">{tr('voldeck.n_sel_none')}</p>
                  : <table className="vdn-table is-compact num"><thead><tr><th className="l">{tr('voldeck.n_col_type')}</th><th>Strike</th><th>Bid</th><th>Ask</th><th>IV</th><th>OI</th></tr></thead>
                    <tbody>{([['Put', near.put], ['Call', near.call]] as const).map(([label, q]) => <tr key={label} className={q && !q.liquid ? 'is-dim' : undefined} title={q?.flags.length ? q.flags.join(' · ') : undefined}>
                      <td className="l">{label}{q && q.otm ? <small> OTM</small> : null}{q?.flagged ? <small className="vdn-warn-text"> ⚠</small> : null}</td><td>{priceText(q?.strike ?? near.strike, na)}</td>
                      <td>{priceText(q?.bid, na)}</td><td>{priceText(q?.ask, na)}</td><td>{ivText(q?.iv, na)}</td><td>{q?.oi == null ? na : q.oi}</td>
                    </tr>)}</tbody></table>}
                {near?.strike != null && <p className="vdn-legend">{tr('voldeck.n_quotes_source', { k: priceText(near.strike, na) })}</p>}
              </div>
              <p className="vdn-legend">{tr('voldeck.n_keys_hint')}</p>
            </div>
          </Card>
        </div>

        {/* ══ FETTE: SMILE della scadenza scelta + TERM STRUCTURE ══ */}
        <div className="vdn-slices">
          <Card className="vdn-slice-card" titolo={tr('voldeck.n_smile_title')}
            conteggio={row ? `${row.expiry} · ${row.days}${tr('voldeck.short_days')}` : undefined}
            azioni={row?.partial ? <span className="fat-pill is-warn">{tr('voldeck.n_partial_badge')}</span> : undefined}>
            <SmileChart model={model} expiry={expiry} column={column} onColumn={c => setPickedColumn(c)} onExpiryStep={stepExpiry}
              quotes={selQuotes} axis={axis === 'strike' && strikeAxis ? 'strike' : 'moneyness'} />
            <div className="vdn-chart-key">
              <span><i className="vdn-key is-grid" />{tr('voldeck.n_smile_key_grid')}</span>
              <span><i className="vdn-key is-obs" />{tr('voldeck.n_smile_key_obs')}</span>
              <span><i className="vdn-key is-illiquid" />{tr('voldeck.n_legend_illiquid')}</span>
              <span><i className="vdn-key is-flag" />{tr('voldeck.n_legend_flagged')}</span>
              <span><i className="vdn-key is-hole-band" />{tr('voldeck.n_legend_hole')}</span>
            </div>
            {selQuotes && <p className="vdn-legend">{tr('voldeck.n_itm_hidden')}{(() => { const c = chainOf(expiry); const o = expiry ? observedFull[expiry] : undefined; return (o?.withoutIv ? ' · ' + tr('voldeck.n_without_iv', { n: o.withoutIv }) : '') + (o?.adjusted ? ' · ' + tr('voldeck.n_adjusted_hidden', { n: o.adjusted }) : ''); })()}</p>}
          </Card>
          <Card className="vdn-slice-card" titolo={tr('voldeck.n_term_title')}
            conteggio={column != null ? `K/S ${numText(model.grid[column], 3)}` : undefined}>
            <TermChart model={model} column={column} expiry={expiry} onExpiry={e => setPickedExpiry(e)} />
            <p className="vdn-legend">{tr('voldeck.n_term_legend')}</p>
          </Card>
        </div>
      </>}

      {workspace === 'tools' && rawData?.slices?.length > 0 && <div className="fat-note is-plain vdn-context" data-vol-context>
        <span className="txt">{tr('voldeck.ui_additional_provider_requests_context_is_not_loaded_aut_56')}
          {' '}{downloadId ? tr('voldeck.n_ctx_route_download') : tr('voldeck.n_ctx_route_new')}</span>
        <button type="button" className="bbn-btn" disabled={loading} onClick={() => { void loadContext(); }}>{tr('voldeck.ui_load_rv_iv_rank_gex_and_cone_context_55')}</button>
      </div>}
      {workspace === 'tools' && contextOrigin && <div className={'fat-note ' + (contextOrigin.kind === 'download' ? 'is-plain' : 'is-warn')} data-vol-context-origin={contextOrigin.kind}>
        <span className="txt">{contextOrigin.kind === 'download'
          ? tr('voldeck.n_ctx_from_download', { t: nyTime(contextOrigin.at)?.text || na })
          : tr('voldeck.n_ctx_from_new', { a: nyTime(contextOrigin.at, true)?.text || na, b: nyTime(contextOrigin.previous, true)?.text || na })}
          {sources && <span className="vdn-ctx-sources">{[
            sources.gex ? tr('voldeck.n_src_gex', { s: String(sources.gex), t: nyTime(sources.gex_fetched_at, true)?.text || na }) : null,
            sources.realized_vol ? tr('voldeck.n_src_rv', { s: String(sources.realized_vol) }) : null,
            sources.iv_history_context ? tr('voldeck.n_src_ivrank', { s: String(sources.iv_history_context) }) : null,
          ].filter(Boolean).map((line, i) => <span key={i}>{line}</span>)}</span>}</span>
      </div>}
      {workspace === 'tools' && rawData && <div className="vdn-tiles is-four">
        <Tile label={tr('voldeck.n_tile_iv30')} value={ivText(rawData.iv_30d, na)}
          sub={finite(rawData.iv_30d) ? (Array.isArray(rawData.iv_30d_bracket_days) ? tr('voldeck.n_iv30_bracket', { a: rawData.iv_30d_bracket_days[0], b: rawData.iv_30d_bracket_days[1] }) : na)
            : (rawData.iv_30d_reason || na)} title={rawData.iv_constant_maturity_method || undefined} />
        <Tile label={tr('voldeck.n_tile_ivrv')} value={!finite(ivrv) ? na : numText(ivrv * 100, 1) + ' pt'} tone={ivrvTone}
          sub={!finite(ivrv) ? t(`voldeck.context_${contextState}`) : ivrv > .03 ? t('voldeck.expensive') : ivrv < -.03 ? t('voldeck.discounted') : t('voldeck.normal_premium')} />
        <Tile label={tr('voldeck.n_tile_rv21', { n: finite(rawData.realized_vol_window_sessions) ? rawData.realized_vol_window_sessions : 21 })} value={ivText(rawData.realized_vol_21d, na)}
          sub={!finite(rawData.rv_percentile_1y) ? t(`voldeck.context_${contextState}`) : numText(rawData.rv_percentile_1y, 0) + '° · 1Y' + (rawData.realized_vol_asof ? ' · ' + rawData.realized_vol_asof : '')} />
        <Tile label={t('voldeck.earnings')} value={rawData.next_earnings || na}
          sub={contextState !== 'loaded' ? t(`voldeck.context_${contextState}`) : rawData.next_earnings ? t('voldeck.earnings_known') : t('voldeck.earnings_unknown')} />
      </div>}

      {/* ══ STRUMENTI DEL DESK (dal payload, formule dichiarate) ══ */}
      {data?.slices?.length > 0 && workspace === 'tools' && <div className="vdn-instruments">
        {data.interpretation && <Card className="vdn-inst is-wide vdn-reading" titolo={tr('voldeck.ui_reading_desk_note_82')}
          conteggio={tr('voldeck.ui_generated_by_the_builder_from_the_figures_below_83')}>
          <p className="vdn-reading-text">{data.interpretation}</p>
        </Card>}
        <Card className="vdn-inst" titolo={tr('voldeck.ui_strike_projector_74')} conteggio={tr('voldeck.ui_expected_move_by_expiry_75')} data-vol-projector="">
          <div className="vdn-inst-body"><StrikeProjector spot={Number(data.spot_est)} term={data.term_structure} earnings={data.next_earnings} /></div>
          <p className="vdn-legend">{tr('voldeck.ui_band_spot_atm_iv_t_solid_1_dashed_2_horizontal_axis_in_76')}<span className="vdn-warn-text"> {tr('voldeck.earnings_legend')}</span> {tr('voldeck.ui_hover_nodes_for_strikes_this_is_the_options_price_not__77')}</p>
        </Card>
        <Card className="vdn-inst" titolo="Forward vol" conteggio={tr('voldeck.ui_between_consecutive_expiries_100')}>
          <FwdVolLadder term={data.term_structure} />
          <p className="vdn-legend">{tr('voldeck.ui_fwd_t_t_t_t_inputs_response_atm_iv_inverted_negative_f_101')}</p>
        </Card>
        <Card className="vdn-inst" titolo={tr('voldeck.ui_iv_rank_altimeter_79')} conteggio={tr('voldeck.ui_against_collected_history_80')}>
          {contextState === 'not_requested' ? <p className="vdn-legend">{t('voldeck.context_not_requested')} · {t('voldeck.explicit_provider')}</p> : <IvAltimeter ctx={data.iv_history_context} />}
          <p className="vdn-legend">{tr('voldeck.ui_percentile_historical_days_with_front_atm_iv_current_s_81')}</p>
        </Card>
        <Card className="vdn-inst is-wide" titolo="Term structure · ATM + skew 25Δ" conteggio={tr('voldeck.ui_rr25_call25_put25_bf25_curvature_vs_atm_96')}>
          <SkewTable term={data.term_structure || []} note={data.skew_note} />
        </Card>
        <Card className="vdn-inst is-wide" titolo="Open interest" conteggio={tr('voldeck.ui_positioning_by_expiry_102')}>
          <OiProfile term={data.term_structure} />
          <p className="vdn-legend">{tr('voldeck.ui_red_put_oi_green_call_oi_p_c_1_5_in_amber_src_polygon__103')}</p>
        </Card>
        <Card className="vdn-inst is-wide" titolo={tr('voldeck.n_heat_title')} conteggio={tr('voldeck.ui_cell_by_cell_observations_92')}>
          <HeatTopDown grid={data.moneyness_grid} slices={data.slices} />
          <p className="vdn-legend">{tr('voldeck.n_heat_legend')}</p>
        </Card>
        <Card className="vdn-inst" titolo="Gamma exposure" conteggio={tr('voldeck.ui_by_strike_104')}>
          {contextState === 'not_requested' ? <p className="vdn-legend">{t('voldeck.context_not_requested')} · {t('voldeck.explicit_provider')}</p> : <GexProfile gex={data.gex} spot={Number(data.spot_est)} />}
          <p className="vdn-legend">{tr('voldeck.ui_gex_0_dealer_long_gamma_dampened_moves_0_short_gamma_a_105')}</p>
        </Card>
        <Card className="vdn-inst is-wide" titolo={tr('voldeck.n_method_title')} conteggio={tr('voldeck.n_method_count')}>
          <MethodNotes data={rawData} />
        </Card>
        <Card className="vdn-inst is-full" titolo="Vol cone · realized vs implied" conteggio={tr('voldeck.ui_5_10_21_63_trading_day_windows_1y_percentiles_90')}>
          <div className="vdn-inst-body">
            {contextState === 'loaded' || (contextState !== 'not_requested' && cone != null) ? <VolCone cone={cone} /> : <p className="vdn-legend">{t(`voldeck.context_${contextState}`)} · {t('voldeck.explicit_provider')}</p>}
          </div>
          <p className="vdn-legend">{tr('voldeck.ui_light_band_min_max_solid_band_p25_p75_dashed_median_cy_91')}</p>
        </Card>
      </div>}
      </>} />
    </div>
    )} />
  );
}
