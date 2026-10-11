import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import ModernPage from '@/components/ModernPage';
import NewInterfaceBoundary from '@/components/NewInterfaceBoundary';
import { useInterfaceTheme } from '@/components/InterfaceThemeProvider';
import Card, { Segmenti } from '@/components/nuova/Card';
import { useLingua, useT } from '@/i18n/provider';
import { t as tr } from '@/i18n/t';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import { surfaceExpiries, toggleExpiry, visibleSurface, type VolWorkspace } from '@/lib/vol-atlas';
import VolWorkbench from '@/components/VolWorkbench';
import {
  atmIndex, DEFAULT_MAX_REL_SPREAD, discardTotals, finite, ivText, MAX_REFETCH_EXPIRIES, nearestIndex, nearestQuotes, numText, nyTime,
  observedQuotes, partialExpiries, priceText, surfaceFreshness, surfaceModel, volRequest, type ObservedQuote, type OptionContract,
} from '@/lib/vol-deck';
import { localizePayload } from '@/lib/api-presentation';
import Surface3D, { CAMERA_PRESETS, SCALE_DIFF, SCALE_VOL, type CameraPreset, type FigureExtras } from './voldeck/Surface3D';
import { SmileChart, TermChart, type AxisMode } from './voldeck/SliceCharts';
import PointReadout from './voldeck/PointReadout';
import IvGrid from './voldeck/IvGrid';
import { useObservedChains } from './voldeck/useObservedChains';
import { clearSnapshots, identicalContent, previousSnapshot, saveSnapshot, snapshotStoreStatus, snapshotTime } from '@/lib/vol-snapshots';
import { ATMF_HEAD, snapshotDay, volQuant, type ChainStatus, type RateInfo } from './voldeck/quant';
import { eventLine, forwardCoverage, NO_OVERLAYS, overlayTraces, termMarks, type OverlayToggles } from './voldeck/overlays';
import SkewView, { deltaCellLabel } from './voldeck/SkewView';
import TermView from './voldeck/TermView';
import QualityView, { DiffBar, diffLineOf, QualitySummary, type PreviousSource } from './voldeck/QualityView';
import { useBackendPrevious } from './voldeck/useBackendPrevious';
import { flagLine, fwdQuality } from './voldeck/quantText';
import { SUBPAGES, useSubpage, useWorkspace, type Subpage } from './voldeck/subpages';
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

/** Altezze dei grafici nelle sotto-pagine: ciascuna sta in una schermata 1440×900. */
const SURFACE_H = 380, SLICE_H = 180, GRID_SLICE_H = 150;

/** Nessun extra sul 3D: oggetto stabile, cosi' la vista predefinita non si ridisegna a ogni nuovo calcolo. */
const NO_FIGURE_EXTRAS: FigureExtras = {};

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

/* PROIETTORE DI STRIKE: tolto il 10/10 (decisione PM, Opus 5.5). Era un cono centrato sullo SPOT del builder
   con l'ATM spot: nella vista Termine resta solo il cono ±1σ sul FORWARD della libreria (expectedMoveCone). */

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
const kfmt = (n: number) => n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n);

/* FORWARD VOL: dal 10/10 (Opus 5.5) la vol forward fra scadenze consecutive la calcola lib/vol-quant
   (forwardVol, varianza totale, arbitraggio di calendario dichiarato) e la mostra la vista «Termine». */

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
  const W = 1400, H = 270, L = 84, R = 36, T = 26, B = 44;
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

/* TABELLA RR/BF DEL BUILDER: tolta il 10/10 (review): l'unico RR25/BF25 in pagina e' quello della libreria (ATMF). */

/* Provenienza della superficie, sempre in vista sotto la legenda (v2/v3 10/10): griglia interpolata del
   builder, saturazione del colore, fonte dichiarata (o «non dichiarata», mai vuota). */
function SurfaceSourceLine({ data }: { data: any }) {
  return <p className="vdn-src-line" data-vol-surface-src>{tr('voldeck.n_surface_src_line', { s: data?._source || tr('voldeck.n_source_na') })}</p>;
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
    ['n_m_moneyness', data?.moneyness_basis], ['n_m_atm', data?.atm_method],
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
  const [workspace, setWorkspace] = useWorkspace();
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
  const [view, setView] = useState<{ name: CameraPreset | null; n: number }>({ name: 'perspective', n: 0 });
  const [plotError, setPlotError] = useState<string | null>(null);
  // ALTA-1 (v2 10/10): da quale istantanea viene il contesto mostrato
  const [contextOrigin, setContextOrigin] = useState<{ kind: 'download'; at: string | null } | { kind: 'new_fetch'; at: string | null; previous: string | null } | null>(null);
  // Vol Deck «quant» (10/10, Opus 5.5): vista, asse delta, colore ΔIV, sovrapposizioni, tasso per il forward
  // sotto-pagina degli Strumenti nell'URL (#/vol?vista=…): indietro torna alla precedente, si puo' linkare
  const [deskView, setDeskView] = useSubpage();
  const [colorMode, setColorMode] = useState<'level' | 'diff'>('level');
  const [overlays, setOverlays] = useState<OverlayToggles>(NO_OVERLAYS);
  const [pickedDeltaColumn, setPickedDeltaColumn] = useState<number | null>(null);
  const [rate, setRate] = useState<RateInfo | null>(null);
  const [snapPersisted, setSnapPersisted] = useState(true);

  useEffect(() => {
    requestRef.current?.abort(); setData(null); setError(null); setLoading(false); lastExpiries.current = [];
    setCone(null); setContextState('not_requested'); setSelectedExpiries(null);
    setDownloadId(null); setPickedExpiry(null); setPickedColumn(null); setPlotError(null); setContextOrigin(null);
    setColorMode('level'); setOverlays(NO_OVERLAYS); setPickedDeltaColumn(null); setRate(null);
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
  const columnKS = pickedColumn != null && pickedColumn >= 0 && pickedColumn < model.grid.length ? pickedColumn
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
  const nOutside = model.rows.reduce((a, r) => a + (observed[r.expiry] || []).filter(q => q.otm && (q.m < lo - 1e-9 || q.m > hi + 1e-9)).length, 0);
  const selQuotes = expiry ? (observed[expiry] ?? null) : null;

  // ── Vol Deck «quant»: tasso FRED (forward da parita'), istantanea precedente, risultati di lib/vol-quant ──
  useEffect(() => {
    if (workspace !== 'tools' || !rawData || rate) return;
    const controller = new AbortController();
    volRequest<any>('/options/strategy/rate', undefined, controller.signal)
      .then(r => { if (!controller.signal.aborted) setRate({ value: finite(r?.value) ? r.value : null, percent: finite(r?.percent) ? r.percent : null, status: typeof r?.status === 'string' ? r.status : null,
        date: typeof r?.date === 'string' ? r.date : null, source: typeof r?.source === 'string' ? r.source : null, error: r?.error ? String(r.error) : null }); })
      .catch(e => { if (!controller.signal.aborted) setRate({ value: null, percent: null, status: 'error', date: null, source: null, error: e instanceof Error ? e.message : String(e) }); });
    return () => controller.abort();
  }, [workspace, rawData, rate]);
  // precedente per il ΔIV: l'archivio del BACKEND e' la fonte preferita; la cache locale si usa solo se il backend
  // non risponde, e la pagina dice sempre da dove viene (RICHIESTE_UI.md R2, review 10/10)
  const [cacheRev, setCacheRev] = useState(0);
  const localPrevious = useMemo(() => surfaceSnapshot ? previousSnapshot(surfaceSnapshot.ticker || ticker, snapshotTime(surfaceSnapshot),
    typeof surfaceSnapshot.download_id === 'string' ? surfaceSnapshot.download_id : null) : null,
  // eslint-disable-next-line react-hooks/exhaustive-deps
  [surfaceSnapshot, ticker, cacheRev]);
  const archive = useBackendPrevious(surfaceSnapshot, ticker, workspace === 'tools');
  const previous = archive.state === 'ok' ? archive.snapshot : archive.state === 'error' ? localPrevious : null;
  // archivio ok ma senza precedente: «nessuno nell'archivio» (la cache locale non entra in silenzio, review R14)
  const previousSource: PreviousSource = archive.state === 'ok' ? (archive.snapshot || archive.identicalAt ? 'backend' : 'none') : archive.state === 'error' ? (localPrevious ? 'cache' : 'none') : archive.state === 'loading' ? 'loading' : 'none';
  const identicalAt = archive.state === 'ok' ? archive.identicalAt ?? null : null;
  const identical = identicalAt ? true : previous ? identicalContent(surfaceSnapshot, previous) : false;
  const store = useMemo(() => snapshotStoreStatus(),
  // eslint-disable-next-line react-hooks/exhaustive-deps
  [surfaceSnapshot, cacheRev, snapPersisted]);
  useEffect(() => { if (surfaceSnapshot) setSnapPersisted(saveSnapshot(surfaceSnapshot, ticker).persisted); }, [surfaceSnapshot, ticker]);
  const chainInput = useMemo(() => Object.fromEntries(model.rows.map(r => {
    const c = observedChains.of(r.expiry);
    const v: { status: ChainStatus; chain: OptionContract[] | null } = !downloadId ? { status: 'none', chain: null } : !c || c.state === 'loading' ? { status: 'loading', chain: null }
      : c.state === 'ok' ? { status: 'ok', chain: c.chain } : { status: 'error', chain: null };
    return [r.expiry, v];
  // eslint-disable-next-line react-hooks/exhaustive-deps
  })), [model, observedChains.chains, downloadId]);
  // istantanea identica (stessa impronta): nessun ΔIV tutto a zero, la pagina lo dice con la data
  const q = useMemo(() => volQuant({ data, model, chains: chainInput, rate, previous: identical ? null : previous }), [data, model, chainInput, rate, previous, identical]);
  const deltaAxis = axis === 'delta';
  const deltaAtm = q.deltaHeads.indexOf(ATMF_HEAD);
  // l'istantanea si salva di nuovo coi forward calcolati: il prossimo ΔIV della libreria e' a K/F
  const fwdMap = useMemo(() => Object.fromEntries(Object.entries(q.forwards).map(([e, f]) => [e, f.forward])), [q]);
  useEffect(() => { if (surfaceSnapshot && Object.values(fwdMap).some(v => v != null)) setSnapPersisted(saveSnapshot(surfaceSnapshot, ticker, fwdMap).persisted); }, [fwdMap, surfaceSnapshot, ticker]);
  const columnD = pickedDeltaColumn != null && pickedDeltaColumn >= 0 && pickedDeltaColumn < q.deltaModel.grid.length ? pickedDeltaColumn : deltaAtm;
  const column = deltaAxis ? columnD : columnKS;
  const activeModel = deltaAxis ? q.deltaModel : model;
  // stesse celle di K/S, ma l'ATM e' l'ATMF della libreria (lettura del punto, curva a termine): mai l'ATM spot del builder
  const atmfModel = useMemo(() => ({ ...model, rows: model.rows.map(r => ({ ...r, atm: q.atmf[r.expiry] ?? null, atmUnqualified: null })) }), [model, q]);
  const readModel = deltaAxis ? q.deltaModel : atmfModel;
  const deltaCell = deltaAxis && expiry ? q.delta.rows.find(r => r.expiry === expiry)?.cells[q.delta.columns[columnD]] ?? null : null;
  const selStrike = deltaAxis ? (deltaCell?.strike ?? null) : columnKS != null ? model.strikes[columnKS] : null;
  const earningsDate = typeof data?.next_earnings === 'string' && data.next_earnings ? data.next_earnings as string : null;
  const nowText = nyTime(surfaceFreshness(rawData).snapshotAt)?.text || tr('voldeck.ui_n_a_15');
  const prevAt = identicalAt ?? previous?.at ?? null;
  const prevText = prevAt ? nyTime(prevAt)?.text || prevAt : null;
  const diffInfo = { nowText, prevText, persisted: snapPersisted, source: previousSource, identical, archiveError: archive.state === 'error' ? archive.error : null,
    store, onClear: () => { clearSnapshots(); setCacheRev(r => r + 1); }, retention: archive.state === 'ok' ? archive.retention ?? null : null };
  const showDiff = colorMode === 'diff' && !deltaAxis;
  const anyOverlay = overlays.forward || overlays.cone || overlays.earnings || overlays.arb;
  const surfaceExtras = useMemo<FigureExtras>(() => !deltaAxis && !showDiff && !anyOverlay ? NO_FIGURE_EXTRAS : ({
    xAxis: deltaAxis ? { title: tr('voldeck.q_axis_delta_title'), ticktext: q.deltaHeads, cellLabel: deltaCellLabel(q, model) } : null,
    color: showDiff ? { kind: 'diff', cells: q.diffCells || model.rows.map(r => r.iv.map(() => null)), range: q.diffRange, title: tr('voldeck.q_diff_scale'), line: diffLineOf(q) } : null,
    overlays: anyOverlay ? overlayTraces(model, q, overlays, themeDark
      ? { forward: '#c4b5fd', cone: '#93c5fd', earnings: '#fbbf24', arb: '#ff6b6b', arbInd: '#a1a1aa', muted: '#a1a1aa' }
      : { forward: '#7c3aed', cone: '#1d56f0', earnings: '#b45309', arb: '#b91c1c', arbInd: '#62626b', muted: '#62626b' }, earningsDate, deltaAxis, snapshotDay(data)) : null,
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [deltaAxis, showDiff, anyOverlay, overlays, q, model, themeDark, earningsDate, language]);
  const flagLines = useMemo(() => new Map<string, string[]>([...q.cellFlags].map(([k, ids]) => [k, ids.map(i => flagLine(q.flags[i]))])), [q]);
  const smileMarks = (() => {
    if (!expiry || !model.spot) return null;
    const F = q.forwardOf(expiry).F, cone = q.cone.find(r => r.expiry === expiry);
    const na = tr('voldeck.ui_n_a_15');
    return {
      forward: overlays.forward && F != null ? { m: deltaAxis ? deltaAtm : F / model.spot,
        text: tr('voldeck.q_fwd_short', { f: priceText(F, na), m: numText(F / model.spot, 4) }) + (q.forwards[expiry] ? ' · ' + fwdQuality(q.forwards[expiry]) : '') } : null,
      cone: overlays.cone && !deltaAxis && cone?.low != null ? { lo: cone.low / model.spot, hi: (cone.high as number) / model.spot,
        text: tr('voldeck.q_cone_short', { dn: numText((cone.downPct as number) * 100, 2), up: numText((cone.upPct as number) * 100, 2), lo: priceText(cone.low, na), hi: priceText(cone.high, na) }) } : null,
      flags: overlays.arb && !deltaAxis ? new Map(model.grid.flatMap((_, i) => { const l = flagLines.get(expiry + '|' + i); return l ? [[i, l] as [number, string[]]] : []; })) : undefined,
      flagLevels: new Map(model.grid.flatMap((_, i) => { const l = q.cellLevel.get(expiry + '|' + i); return l ? [[i, l] as [number, 'executable' | 'indicative']] : []; })),
    };
  })();
  const fwdCov = forwardCoverage(model, q);
  const rateLine = rate == null ? tr('voldeck.q_rate_loading') : rate.value == null ? tr('voldeck.q_rate_na', { e: rate.error || tr('voldeck.ui_n_a_15') })
    : q.r == null ? tr('voldeck.q_rate_na', { e: tr('voldeck.ui_n_a_15') })
    : tr(rate.status === 'stale' ? 'voldeck.q_rate_stale' : 'voldeck.q_rate_line', { v: numText(q.r * 100, 3), p: numText(rate.percent, 2), s: rate.source || '', d: rate.date || tr('voldeck.ui_n_a_15') });
  const near = selQuotes ? nearestQuotes(selQuotes, selStrike) : null;
  const strikeAxis = model.spot != null;

  const pick = (e: string, c: number) => { setPickedExpiry(e); if (deltaAxis) setPickedDeltaColumn(c); else setPickedColumn(c); };
  const pickColumn = (c: number) => { if (deltaAxis) setPickedDeltaColumn(c); else setPickedColumn(c); };
  const pickDelta = (e: string, c: number) => { setPickedExpiry(e); setPickedDeltaColumn(c); };
  const toggleOverlay = (key: keyof OverlayToggles) => setOverlays(o => ({ ...o, [key]: !o[key] }));
  // la vista Superficie resta montata (camera conservata): al ritorno Plotly rimisura il contenitore
  useEffect(() => { if (deskView === 'superficie') window.dispatchEvent(new Event('resize')); }, [deskView]);
  const stepExpiry = (step: -1 | 1) => {
    const i = model.rows.findIndex(r => r.expiry === expiry);
    const next = model.rows[Math.max(0, Math.min(model.rows.length - 1, (i < 0 ? 0 : i) + step))];
    if (next) setPickedExpiry(next.expiry);
  };

  // ── testata: istantanea, fuso New York, seduta, ritardo; numeri di sintesi (nomi del contratto cbcfd0b) ──
  const na = t('voldeck.na');
  const ivrv = data?.iv_rv_spread_30d;
  // 10/10 (Opus 5.5): la lettura del prezzo della protezione arriva dal backend (protection_price,
  // rapporto IV/RV con le soglie condivise core/soglie_score): niente seconda taratura qui (prima ±3 pt)
  const ivrvRatio = data?.iv_rv_ratio_30d;
  const vrpCode = data?.protection_price;
  const ivrvTone = vrpCode === 'EXPENSIVE' ? 'bad' : vrpCode === 'DISCOUNT' ? 'good' : undefined;
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
  // tessere di sintesi: fuori dagli Strumenti in testata; dentro, Termine (ATM spot, struttura, expected move)
  const tilesGrid = rawData ? [
    <Tile key="t3" label={tr('voldeck.n_tile_holes')} value={String(model.holes)} tone={model.holes ? 'warn' : undefined}
          sub={tr('voldeck.n_tile_holes_sub', { f: model.filledCells, c: model.cells })} />,
    <Tile key="t4" label={tr('voldeck.n_tile_measured')} value={downloadId ? String(nMeasured) : na}
          sub={!downloadId ? tr('voldeck.n_observed_none') : nLoading ? tr('voldeck.n_observed_loading') : tr('voldeck.n_tile_measured_sub', { e: nLoaded, n: model.rows.length })} />,
  ] : [];


  return (
    <ModernPage page="vol" presentationBoundary={false} render={() => (
    <div className={'bbn-vol bbn-font' + (themeDark ? ' is-dark' : '')} data-vol-atlas data-workspace={workspace}>
      <VolPagePresentationBoundary render={() => <>
      <header className="vdn-top">
        <div className="vdn-title">
          <h1>{t('voldeck.atlas_title')}</h1>
          <p>{t('voldeck.atlas_intro')}</p>
        </div>
        <nav className="bbn-seg is-large vdn-tabs" aria-label={t('voldeck.workspace')}>
          {workspaces.map(mode => <button key={mode} type="button" aria-pressed={workspace === mode} className={workspace === mode ? 'is-on' : undefined}
            onClick={() => setWorkspace(mode)} data-vol-workspace={mode}>{t(`voldeck.${mode}`)}</button>)}
        </nav>
        <span className="bbn-grow" />
        <form className="va-ticker vdn-ticker" onSubmit={e => { e.preventDefault(); go(); setWorkspace('acquisition'); }}>
          <label htmlFor="va-ticker">{t('voldeck.underlying')}</label>
          <input id="va-ticker" value={input} onChange={e => setInput(e.target.value.toUpperCase())}
            spellCheck={false} placeholder="SYNTH" autoComplete="off" />
          <button type="submit" className="bbn-btn is-primary" disabled={loading}>{t('voldeck.catalog')}</button>
        </form>
      </header>
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
        {workspace === 'tools' && chainErrors.length > 0 && <span className="fat-pill is-warn" data-vol-strip-chain-errors
          title={chainErrors.map(x => tr('voldeck.n_observed_error', { e: x.e, err: x.c.error })).join('\n')}>{tr('voldeck.q_strip_chain_errors', { n: chainErrors.length })}</span>}
        {workspace === 'tools' && chainNotes.length > 0 && <span className="fat-pill is-warn" data-vol-strip-chain-partial
          title={chainNotes.map(x => x.c.truncated ? tr('voldeck.n_observed_truncated', { e: x.e, n: x.c.chain.length })
            : tr('voldeck.n_observed_partial', { e: x.e, n: x.c.chain.length, why: x.c.rowError || tr('voldeck.n_chain_incomplete') })).join('\n')}>{tr('voldeck.q_strip_chain_partial', { n: chainNotes.length })}</span>}
        {workspace === 'tools' && !downloadId && <span className="fat-pill is-warn" data-vol-strip-no-chain>{tr('voldeck.n_observed_none')}</span>}
        <span className="fat-chip vdn-method" title={rawData.smoothing || undefined}>{rawData.smoothing || t('voldeck.method_unknown')}</span>
      </div>}
      {rawData && workspace !== 'laboratory' && workspace !== 'tools' && <div className="vdn-tiles is-two">
        {tilesGrid}
      </div>}
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
        {/* ══ SOTTO-PAGINE degli Strumenti (10/10, Opus 5.5 — richiesta PM): una domanda, una sola in vista, stato nell'URL ══ */}
        <div className="vdn-deskviews" data-vol-desk-views>
          <Segmenti<Subpage> etichetta={tr('voldeck.q_views_aria')} valore={deskView} onChange={setDeskView} className="is-large"
            opzioni={SUBPAGES.map(id => ({ id, testo: tr(`voldeck.q_view_${id}`),
              title: id === 'coerenza' && q.flags.length ? tr('voldeck.q_quality_chip', { e: q.nExecutable, i: q.nIndicative }) : undefined }))} />
        </div>
        <div className="vdn-subq-row">
          <h2 className="vdn-subq" data-vol-subpage-q={deskView}>{tr(`voldeck.q_view_${deskView}_q`)}</h2>
          <span className="bbn-grow" />
          <details className="vdn-expfilter vdn-method-panel" data-vol-method-panel>
            <summary>{tr('voldeck.q_method_panel')}</summary>
            <div className="vdn-expfilter-body vdn-method-body">
              <SurfaceSourceLine data={data} />
              <p>{rateLine}</p>
              <p>{tr('voldeck.n_surface_method')} {tr('voldeck.n_smoothing_note', { m: rawData?.smoothing || t('voldeck.method_unknown') })}</p>
              <p>{tr('voldeck.n_drag_hint')} {tr('voldeck.n_keys_hint')}</p>
              {selQuotes && <p>{tr('voldeck.n_itm_hidden')}</p>}
              <p>{tr('voldeck.n_term_legend')}</p>
              <p>{tr('voldeck.q_skew_legend')}</p>
              <p>{tr('voldeck.q_term_legend')} {tr('voldeck.q_term_table_legend')} {tr('voldeck.q_event_legend')}</p>
              <p>{tr('voldeck.q_quality_legend')}</p>
              <p>{tr('voldeck.q_diff_legend')}</p>
              <p>{tr('voldeck.ui_percentile_historical_days_with_front_atm_iv_current_s_81')} {tr('voldeck.ui_red_put_oi_green_call_oi_p_c_1_5_in_amber_src_polygon__103')}
                {' '}{tr('voldeck.ui_gex_0_dealer_long_gamma_dampened_moves_0_short_gamma_a_105')} {tr('voldeck.ui_light_band_min_max_solid_band_p25_p75_dashed_median_cy_91')}</p>
              <MethodNotes data={rawData} />
            </div>
          </details>
          {rawData && <details className="vdn-expfilter" data-vol-expiry-filter>
            <summary>{t('voldeck.selected_count', { n: data?.slices?.length || 0, total: surfaceExpiries(rawData).length })}</summary>
            <div className="vdn-expfilter-body">
              <div className="va-expiries vdn-expiry-chips">{surfaceExpiries(rawData).map(e => <button key={e} type="button" className="bbn-toggle-chip"
                aria-pressed={selectedExpiries === null || selectedExpiries.includes(e)}
                onClick={() => setSelectedExpiries(value => toggleExpiry(value, surfaceExpiries(rawData), e))}>{e}{partial.includes(e) ? ' ⚠' : ''}</button>)}</div>
              <p className="vdn-legend"><button type="button" className="bbn-link" onClick={() => setSelectedExpiries(null)}>{t('voldeck.all_dates')}</button>
                {' · '}<button type="button" className="bbn-link" onClick={() => setSelectedExpiries([])}>{t('voldeck.clear_dates')}</button> · {t('voldeck.display_only')}</p>
            </div>
          </details>}
        </div>
        {/* la Superficie resta montata quando e' nascosta: la camera 3D non si perde cambiando sotto-pagina */}
        <div className="vdn-view" hidden={deskView !== 'superficie'} data-vol-view-panel="superficie">
        <section className="bbn-card vdn-surface-card" aria-label={tr('voldeck.n_surface_title')}>
          {/* barra unica: Asse · Colore · Sovrapposizioni (default = la vista di prima: K/S, livello IV, nessuna) */}
          <div className="vdn-controlbar" data-vol-controls>
            <div className="vdn-ctl"><span>{tr('voldeck.q_ctl_axis')}</span>
              <div className="bbn-seg" role="group" aria-label={tr('voldeck.n_axis_mode')}>
                <button type="button" aria-pressed={axis === 'moneyness'} className={axis === 'moneyness' ? 'is-on' : undefined} onClick={() => setAxis('moneyness')}>K/S</button>
                <button type="button" aria-pressed={axis === 'strike'} className={axis === 'strike' ? 'is-on' : undefined} disabled={!strikeAxis}
                  title={strikeAxis ? undefined : tr('voldeck.n_strike_unavailable')} onClick={() => setAxis('strike')}>Strike</button>
                <button type="button" aria-pressed={axis === 'delta'} className={axis === 'delta' ? 'is-on' : undefined} data-vol-axis="delta"
                  title={tr('voldeck.q_axis_delta_hint')} onClick={() => setAxis('delta')}>Delta</button>
              </div></div>
            <div className="vdn-ctl"><span>{tr('voldeck.q_ctl_color')}</span>
              <div className="bbn-seg" role="group" aria-label={tr('voldeck.q_ctl_color')}>
                <button type="button" aria-pressed={colorMode === 'level'} className={colorMode === 'level' ? 'is-on' : undefined} onClick={() => setColorMode('level')}>{tr('voldeck.q_color_level')}</button>
                <button type="button" aria-pressed={colorMode === 'diff'} className={colorMode === 'diff' ? 'is-on' : undefined} data-vol-color="diff" disabled={deltaAxis}
                  title={deltaAxis ? tr('voldeck.q_diff_delta_off') : undefined} onClick={() => setColorMode('diff')}>
                  {tr('voldeck.q_color_diff', { d: previous ? (nyTime(previous.at, false)?.text || previous.at).replace(' New York', '') : tr('voldeck.q_date_word') })}</button>
              </div></div>
            <div className="vdn-ctl"><span>{tr('voldeck.q_ctl_overlays')}</span>
              <div className="vdn-toggles" role="group" aria-label={tr('voldeck.q_ctl_overlays')}>
                {(['forward', 'cone', 'earnings', 'arb'] as const).map(key => {
                  const off = deltaAxis && (key === 'cone' || key === 'arb');
                  return <button key={key} type="button" className="bbn-toggle-chip" aria-pressed={overlays[key]} disabled={off} data-vol-overlay={key}
                    title={off ? tr('voldeck.q_ov_delta_off') : undefined} onClick={() => toggleOverlay(key)}>{tr(`voldeck.q_ov_${key}`)}</button>;
                })}
              </div></div>
          </div>
          {colorMode === 'diff' && !deltaAxis && <DiffBar info={diffInfo} axis={q.diff?.axis ?? null} />}
          <div className="vdn-surface-split">
          <div className="vdn-plot-wrap">
            <div className="bbn-seg vdn-cam-presets" role="group" aria-label={tr('voldeck.n_view_aria')} data-vol-views>
              {CAMERA_PRESETS.map(name => <button key={name} type="button" aria-pressed={view.name === name} className={view.name === name ? 'is-on' : undefined}
                data-vol-view={name} onClick={() => setView(v => ({ name, n: v.n + 1 }))}>{tr(`voldeck.n_view_${name}`)}</button>)}
              <button type="button" className="vdn-reset" data-vol-reset aria-label={tr('voldeck.n_reset_view')} title={tr('voldeck.n_reset_view')}
                onClick={() => { setView(v => ({ name: 'perspective', n: v.n })); setResetKey(k => k + 1); }}>↺</button>
            </div>
            <Surface3D model={activeModel} axis={deltaAxis ? 'delta' : axis === 'strike' && strikeAxis ? 'strike' : 'moneyness'} expiry={expiry} column={column}
              dark={themeDark} onPick={pick} onError={setPlotError} resetKey={resetKey} preset={view} extras={surfaceExtras} height={SURFACE_H}
              onUserRotate={() => setView(v => (v.name == null ? v : { name: null, n: v.n }))} />
            <div id="vol-surface-expiry-axis-label" data-vol-axis-title="expiry" className="vdn-axis-note">
              {tr(deltaAxis ? 'voldeck.q_axes_note_delta' : 'voldeck.n_axes_note', { d: tr('voldeck.ui_days_to_expiry_53') })}
            </div>
          </div>
            <aside className="vdn-surface-side" data-vol-surface-side>
          <div className="vdn-selbar is-side">
            {row && <PointReadout model={readModel} row={readModel.rows.find(r => r.expiry === row.expiry) || row} column={column}
              delta={deltaAxis ? { head: q.deltaHeads[columnD] || '', strike: deltaCell?.strike ?? null } : null} />}
            <div className="vdn-quotes">
              <h3>{tr('voldeck.n_sel_quotes')}</h3>
              {!downloadId ? <p className="vdn-legend">{tr('voldeck.n_observed_none')}</p>
                : chainOf(expiry)?.state === 'loading' || !chainOf(expiry) ? <p className="vdn-legend">{tr('voldeck.n_observed_loading')}</p>
                : chainOf(expiry)?.state === 'error' ? <p className="vdn-warn-text">{tr('voldeck.n_observed_error', { e: expiry || '', err: (chainOf(expiry) as any).error })}</p>
                : !near || near.strike == null ? <p className="vdn-legend">{tr('voldeck.n_sel_none')}</p>
                : <table className="vdn-table is-compact num" title={tr('voldeck.n_quotes_source', { k: priceText(near.strike, na) })}><thead><tr><th className="l">{tr('voldeck.n_col_type')}</th><th>Strike</th><th>Bid</th><th>Ask</th><th>IV</th><th>OI</th></tr></thead>
                  <tbody>{([['Put', near.put], ['Call', near.call]] as const).map(([label, q]) => <tr key={label} className={q && !q.liquid ? 'is-dim' : undefined} title={q?.flags.length ? q.flags.join(' · ') : undefined}>
                    <td className="l">{label}{q && q.otm ? <small> OTM</small> : null}{q?.flagged ? <small className="vdn-warn-text"> ⚠</small> : null}</td><td>{priceText(q?.strike ?? near.strike, na)}</td>
                    <td>{priceText(q?.bid, na)}</td><td>{priceText(q?.ask, na)}</td><td>{ivText(q?.iv, na)}</td><td>{q?.oi == null ? na : q.oi}</td>
                  </tr>)}</tbody></table>}
            </div>
          </div>
          {/* stato dei dati: resta sempre in vista (buchi, esclusioni, quote mancanti o parziali) */}
          <div className="vdn-notes" data-vol-status>
            {row?.partial && <p className="vdn-warn-text">{tr('voldeck.n_partial_note')}</p>}
            {model.excludedShort.length > 0 && <p>{tr('voldeck.n_short_excluded', { n: model.excludedShort.length })}</p>}
            {nOpt > 0 && <p data-vol-quality>{tr('voldeck.n_quotes_used', { n: nOpt, i: nIll })}
              {discards.total ? ' · ' + tr('voldeck.n_quality_excluded', { n: discards.total }) + ' (' + QUALITY_REASONS.filter(k => discards.byReason[k])
                .map(k => tr(`voldeck.n_reason_${k}`) + ' ' + discards.byReason[k])
                .concat(Object.keys(discards.byReason).filter(k => !(QUALITY_REASONS as readonly string[]).includes(k)).map(k => k + ' ' + discards.byReason[k])).join(', ') + ')' : ''}
              {discards.adjusted ? ' · ' + tr('voldeck.n_adjusted_excluded', { n: discards.adjusted }) : ''}</p>}
            {!downloadId ? <p className="vdn-warn-text">{tr('voldeck.n_observed_none')}</p>
              : nLoading ? <p>{tr('voldeck.n_observed_loading')}</p>
              : <p>{tr('voldeck.n_observed_count', { n: nMeasured, e: nLoaded })}{nFlagged ? ' · ' + tr('voldeck.n_flagged_count', { n: nFlagged }) : ''}{nAdjustedObs ? ' · ' + tr('voldeck.n_adjusted_hidden', { n: nAdjustedObs }) : ''}{nOutside ? ' · ' + tr('voldeck.n_out_of_grid', { n: nOutside, a: numText(lo, 2), b: numText(hi, 2) }) : ''}</p>}
            {chainErrors.map(x => <p key={x.e} className="vdn-warn-text">{tr('voldeck.n_observed_error', { e: x.e, err: x.c.error })}</p>)}
            {chainNotes.map(x => <p key={x.e} className="vdn-warn-text">{x.c.truncated ? tr('voldeck.n_observed_truncated', { e: x.e, n: x.c.chain.length })
              : tr('voldeck.n_observed_partial', { e: x.e, n: x.c.chain.length, why: x.c.rowError || tr('voldeck.n_chain_incomplete') })}</p>)}
          </div>
          <div className="vdn-keyrow" aria-label={tr('voldeck.n_legend_aria')}>
            {showDiff ? <span data-vol-key-diff><i className="vdn-key is-scale" style={{ background: `linear-gradient(90deg, ${SCALE_DIFF.map(([, c]) => c).join(', ')})` }} />{tr('voldeck.q_legend_diff')}</span>
              : <span><i className="vdn-key is-scale" style={{ background: `linear-gradient(90deg, ${SCALE_VOL.map(([, c]) => c).join(', ')})` }} />{tr('voldeck.n_legend_scale')}</span>}
            <span><i className="vdn-key is-sel-line" />{tr('voldeck.n_legend_sel_expiry')}</span>
            <span><i className="vdn-key is-sel-dash" />{tr('voldeck.n_legend_sel_col')}</span>
            <span><i className="vdn-key is-hole">×</i>{tr('voldeck.n_legend_hole')}</span>
            {overlays.forward && <span data-vol-key-forward><i className="vdn-key is-fwd-line" />{tr('voldeck.q_key_forward', { n: fwdCov.ok, t: fwdCov.total })}{fwdCov.why ? ' · ' + fwdCov.why : ''}</span>}
            {overlays.cone && !deltaAxis && <span data-vol-key-cone><i className="vdn-key is-cone" />{tr('voldeck.q_key_cone', { n: q.cone.filter(r => r.low != null).length, t: q.cone.length })}</span>}
            {overlays.earnings && <span data-vol-key-earnings><i className="vdn-key is-earn" />{eventLine(q, earningsDate)}</span>}
            {overlays.arb && !deltaAxis && <span data-vol-key-arb><i className="vdn-key is-arb-exec" />{tr('voldeck.q_key_arb_exec', { n: q.nExecutable })}
              <i className="vdn-key is-arb-ind" />{tr('voldeck.q_key_arb_ind', { n: q.nIndicative })}</span>}
          </div>
              <p className="vdn-src-line" data-vol-surface-count>{tr('voldeck.n_surface_title')} · {tr('voldeck.n_surface_count', { r: model.rows.length, c: model.cells, h: model.holes })}</p>
            </aside>
          </div>
        </section>
        </div>
        {deskView === 'griglia' && <div className="vdn-view" data-vol-view-panel="griglia">
        <div className="vdn-slices">
          <Card className="vdn-slice-card" titolo={tr('voldeck.n_smile_title')}
            conteggio={row ? `${row.expiry} · ${row.days}${tr('voldeck.short_days')}` : undefined}
            azioni={row?.partial ? <span className="fat-pill is-warn">{tr('voldeck.n_partial_badge')}</span> : undefined}>
            <SmileChart model={activeModel} expiry={expiry} column={column} onColumn={pickColumn} onExpiryStep={stepExpiry}
              quotes={deltaAxis ? null : selQuotes} axis={deltaAxis ? 'delta' : axis === 'strike' && strikeAxis ? 'strike' : 'moneyness'}
              heads={deltaAxis ? q.deltaHeads : undefined} marks={smileMarks} height={GRID_SLICE_H} />
            <div className="vdn-chart-key">
              <span><i className="vdn-key is-grid" />{tr('voldeck.n_smile_key_grid')}</span>
              <span><i className="vdn-key is-obs" />{tr('voldeck.n_smile_key_obs')}</span>
              <span><i className="vdn-key is-illiquid" />{tr('voldeck.n_legend_illiquid')}</span>
              <span><i className="vdn-key is-flag" />{tr('voldeck.n_legend_flagged')}</span>
              <span><i className="vdn-key is-hole-band" />{tr('voldeck.n_legend_hole')}</span>
            </div>
          </Card>
          <Card className="vdn-slice-card" titolo={tr('voldeck.n_term_title')}
            conteggio={deltaAxis ? q.deltaHeads[columnD] : column != null ? `K/S ${numText(model.grid[column], 3)}` : undefined}>
            <TermChart model={readModel} column={column} expiry={expiry} onExpiry={e => setPickedExpiry(e)}
              colHead={deltaAxis ? q.deltaHeads[columnD] : null} marks={termMarks(model, q, earningsDate, { fwd: overlays.forward, earnings: overlays.earnings }, snapshotDay(data))} height={GRID_SLICE_H} />
          </Card>
        </div>
        <Card className="vdn-grid-card" titolo={deltaAxis ? tr('voldeck.q_delta_grid_title') : tr('voldeck.n_grid_title')}
          conteggio={tr('voldeck.n_tile_holes_sub', { f: activeModel.filledCells, c: activeModel.cells })}
          azioni={<QualitySummary q={q} onOpen={() => setDeskView('coerenza')} />}>
          <IvGrid model={activeModel} axis={deltaAxis ? 'delta' : axis === 'strike' && strikeAxis ? 'strike' : 'moneyness'} expiry={expiry} column={column}
            onPick={pick} onExpiry={e => setPickedExpiry(e)} onColumn={pickColumn}
            extras={{ heads: deltaAxis ? q.deltaHeads : undefined, cellLabel: deltaAxis ? deltaCellLabel(q, model) : undefined,
              diff: showDiff ? { cells: q.diffCells || [], range: q.diffRange, line: diffLineOf(q) } : null,
              flags: overlays.arb && !deltaAxis ? flagLines : null, flagLevel: q.cellLevel }} />
        </Card>
        </div>}
        {deskView === 'skew' && <SkewView q={q} model={model} expiry={expiry} column={columnD} onExpiry={e => setPickedExpiry(e)}
          onColumn={c => setPickedDeltaColumn(c)} onPick={pickDelta} onExpiryStep={stepExpiry} rateLine={rateLine} />}
        {deskView === 'termine' && <TermView q={q} model={model} column={columnKS} expiry={expiry} onExpiry={e => setPickedExpiry(e)} earnings={earningsDate} chartHeight={SLICE_H} />}
        {(deskView === 'coerenza' || deskView === 'variazioni') && <QualityView q={q} model={model} expiry={expiry} column={columnKS}
          part={deskView === 'coerenza' ? 'flags' : 'diff'} onPick={(e, c) => { setPickedExpiry(e); setPickedColumn(c); }}
          onExpiry={e => setPickedExpiry(e)} onColumn={c => setPickedColumn(c)} diff={diffInfo}
          onShowDiff={() => { setAxis('moneyness'); setColorMode('diff'); setDeskView('superficie'); }} />}
        {deskView === 'realizzata' && <div className="vdn-view" data-vol-view-panel="realizzata">
        <Card className="vdn-inst is-full" titolo="Vol cone · realized vs implied" conteggio={tr('voldeck.ui_5_10_21_63_trading_day_windows_1y_percentiles_90')}>
          <div className="vdn-inst-body">
            {contextState === 'loaded' || (contextState !== 'not_requested' && cone != null) ? <VolCone cone={cone} /> : <p className="vdn-legend">{t(`voldeck.context_${contextState}`)} · {t('voldeck.explicit_provider')}</p>}
          </div>
        </Card>
        </div>}
        {deskView === 'contesto' && <div className="vdn-view" data-vol-view-panel="contesto">
      {rawData?.slices?.length > 0 && <div className="fat-note is-plain vdn-context" data-vol-context>
        <span className="txt">{tr('voldeck.ui_additional_provider_requests_context_is_not_loaded_aut_56')}
          {' '}{downloadId ? tr('voldeck.n_ctx_route_download') : tr('voldeck.n_ctx_route_new')}</span>
        <button type="button" className="bbn-btn" disabled={loading} onClick={() => { void loadContext(); }}>{tr('voldeck.ui_load_rv_iv_rank_gex_and_cone_context_55')}</button>
      </div>}
      {contextOrigin && <div className={'fat-note ' + (contextOrigin.kind === 'download' ? 'is-plain' : 'is-warn')} data-vol-context-origin={contextOrigin.kind}>
        <span className="txt">{contextOrigin.kind === 'download'
          ? tr('voldeck.n_ctx_from_download', { t: nyTime(contextOrigin.at)?.text || na })
          : tr('voldeck.n_ctx_from_new', { a: nyTime(contextOrigin.at, true)?.text || na, b: nyTime(contextOrigin.previous, true)?.text || na })}
          {sources && <span className="vdn-ctx-sources">{[
            sources.gex ? tr('voldeck.n_src_gex', { s: String(sources.gex), t: nyTime(sources.gex_fetched_at, true)?.text || na }) : null,
            sources.realized_vol ? tr('voldeck.n_src_rv', { s: String(sources.realized_vol) }) : null,
            sources.iv_history_context ? tr('voldeck.n_src_ivrank', { s: String(sources.iv_history_context) }) : null,
          ].filter(Boolean).map((line, i) => <span key={i}>{line}</span>)}</span>}</span>
      </div>}
      {rawData && <div className="vdn-tiles is-four">
        <Tile label={tr('voldeck.n_tile_iv30')} value={ivText(rawData.iv_30d, na)}
          sub={finite(rawData.iv_30d) ? (Array.isArray(rawData.iv_30d_bracket_days) ? tr('voldeck.n_iv30_bracket', { a: rawData.iv_30d_bracket_days[0], b: rawData.iv_30d_bracket_days[1] }) : na)
            : (rawData.iv_30d_reason || na)} title={rawData.iv_constant_maturity_method || undefined} />
        <Tile label={tr('voldeck.n_tile_ivrv')} value={!finite(ivrvRatio) ? na : numText(ivrvRatio, 2) + 'x' + (finite(ivrv) ? ' · ' + numText(ivrv * 100, 1) + ' pt' : '')} tone={ivrvTone}
          sub={!vrpCode ? t(`voldeck.context_${contextState}`) : vrpCode === 'EXPENSIVE' ? t('voldeck.expensive') : vrpCode === 'DISCOUNT' ? t('voldeck.discounted') : t('voldeck.normal_premium')} />
        <Tile label={tr('voldeck.n_tile_rv21', { n: finite(rawData.realized_vol_window_sessions) ? rawData.realized_vol_window_sessions : 21 })} value={ivText(rawData.realized_vol_21d, na)}
          sub={!finite(rawData.rv_percentile_1y) ? t(`voldeck.context_${contextState}`) : numText(rawData.rv_percentile_1y, 0) + '° · 1Y' + (rawData.realized_vol_asof ? ' · ' + rawData.realized_vol_asof : '')} />
        <Tile label={t('voldeck.earnings')} value={rawData.next_earnings || na}
          sub={contextState !== 'loaded' ? t(`voldeck.context_${contextState}`) : rawData.next_earnings ? t('voldeck.earnings_known') : t('voldeck.earnings_unknown')} />
      </div>}
      {data?.slices?.length > 0 && <div className="vdn-instruments">
        <Card className="vdn-inst" titolo={tr('voldeck.ui_iv_rank_altimeter_79')} conteggio={tr('voldeck.ui_against_collected_history_80')}>
          {contextState === 'not_requested' ? <p className="vdn-legend">{t('voldeck.context_not_requested')} · {t('voldeck.explicit_provider')}</p> : <IvAltimeter ctx={data.iv_history_context} />}
        </Card>
        <Card className="vdn-inst is-wide" titolo="Open interest" conteggio={tr('voldeck.ui_positioning_by_expiry_102')}>
          <OiProfile term={data.term_structure} />
        </Card>
        <Card className="vdn-inst" titolo="Gamma exposure" conteggio={tr('voldeck.ui_by_strike_104')}>
          {contextState === 'not_requested' ? <p className="vdn-legend">{t('voldeck.context_not_requested')} · {t('voldeck.explicit_provider')}</p> : <GexProfile gex={data.gex} spot={Number(data.spot_est)} />}
        </Card>

      </div>}


        </div>}
      </>}
      </>} />
    </div>
    )} />
  );
}
