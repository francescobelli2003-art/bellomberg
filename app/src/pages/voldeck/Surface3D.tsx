import { useEffect, useRef } from 'react';
import { t as tr } from '@/i18n/t';
import { linguaCorrente } from '@/i18n/lingua';
import { finite, ivText, nearestIndex, numText, pickFromPoint, priceText, surfaceQuoteScale, type ObservedQuote, type SurfaceModel } from '@/lib/vol-deck';
import type { AxisMode } from './SliceCharts';

/* ============================================================================
   Superficie IV 3D ruotabile (Vol Deck Nuova, 09/10/2026 — Opus 5.5). Plotly locale gia'
   presente nell'app (public/vendor/plotly-2.32.0.min.js): nessuna libreria nuova.
   - superficie = griglia del builder; le celle null restano BUCHI (connectgaps false) e una
     croce sul pavimento le marca: nessuna cella viene riempita qui;
   - v2 (10/10, ALTA-2): Plotly non disegna un quadrilatero se manca anche uno solo dei suoi 4 vertici,
     quindi celle VALIDE vicine a un buco sparivano a schermo. Ogni cella valida ha ora anche un
     marcatore (traccia `cells`, colore = IV sulla stessa scala, hover e clic): «94 di 119 celle» e'
     vero a schermo, non solo nella tessera;
   - punti = quote MISURATE (IV del fornitore) delle scadenze mostrate; vuoti = illiquidi; rombo = con
     flag di qualita' (v2, MEDIA-4);
   - linea della scadenza scelta + linea del K/S scelto (le fette disegnate sotto);
   - clic su un punto = sceglie scadenza e K/S; camera conservata (uirevision) fra ridisegni;
   - v2 (ALTA-3): titolo nativo dell'asse y VUOTO (Plotly lo tagliava): l'etichetta e' l'HTML sotto il
     grafico, una sola volta; numeri col separatore della lingua (layout.separators).
   ========================================================================== */
declare global { interface Window { Plotly?: any } }

export function loadPlotly(): Promise<any> {
  return new Promise((resolve, reject) => {
    if (window.Plotly) return resolve(window.Plotly);
    const s = document.createElement('script');
    s.src = new URL('./vendor/plotly-2.32.0.min.js', document.baseURI).href;
    s.onload = () => resolve(window.Plotly);
    s.onerror = () => reject(new Error(tr('voldeck.ui_local_plotly_bundle_unavailable_1')));
    document.head.appendChild(s);
  });
}

export const DEFAULT_CAMERA = { eye: { x: -1.38, y: -1.48, z: 0.6 }, center: { x: 0, y: 0, z: -0.14 }, up: { x: 0, y: 0, z: 1 } };
export const SCENE_REVISION = 'bellomberg-vol-surface-camera';

/** p-esimo percentile (indice per difetto): satura solo la scala COLORE, mai la geometria. */
export function pctile(arr: number[], p: number): number | null {
  if (!arr.length) return null;
  const a = [...arr].sort((x, y) => x - y);
  return a[Math.min(a.length - 1, Math.max(0, Math.floor(p * (a.length - 1))))];
}

export interface Palette { text: string; muted: string; line: string; accent: string; violet: string; warn: string; card: string; scale: [number, string][] }
export const SCALE_LIGHT: [number, string][] = [[0, '#dbe6ff'], [0.35, '#8fb0ff'], [0.65, '#3f6ef2'], [0.85, '#7c3aed'], [1, '#b45309']];
export const SCALE_DARK: [number, string][] = [[0, '#1b2a55'], [0.35, '#2f5be0'], [0.65, '#5b8cff'], [0.85, '#a78bfa'], [1, '#f0c062']];

/** Tracce e scena: funzione pura (provata dai test), la stessa che il componente passa a Plotly.
 *  Convenzione customdata di OGNI traccia a punti: [0] = scadenza, [1] = indice di colonna K/S. */
export function surfaceFigure(model: SurfaceModel, opts: {
  axis: AxisMode; expiry: string | null; column: number | null;
  observed: Record<string, ObservedQuote[] | undefined>; palette: Palette; labels: Record<string, string>;
}) {
  const { axis, palette: p, labels: L } = opts;
  const strikeAxis = axis === 'strike' && model.spot != null;
  const xs = strikeAxis ? model.strikes.map(k => k as number) : model.grid;
  const y = model.rows.map(r => r.days);
  const z = model.rows.map(r => r.iv.map(v => v == null ? null : v * 100));
  const flat = z.flat().filter(finite);
  const cmax = pctile(flat, 0.99), cmin = pctile(flat, 0.01);
  const floor = flat.length ? Math.min(...flat) - Math.max(0.6, (Math.max(...flat) - Math.min(...flat)) * 0.08) : 0;
  const nd = L.na;
  const ks = (m: number) => numText(m, 3, nd);
  // surface: nell'hovertemplate di Plotly 2.32 ne' %{customdata} ne' %{text} si risolvono (misurato 10/10):
  // il testo dell'hover e' scritto per cella, con l'IV gia' formattata, e mostrato con hoverinfo 'text'.
  const cellText = (r: SurfaceModel['rows'][number], i: number) => `<b>${r.expiry}</b> · ${r.days}${L.d}${r.partial ? ` · ${L.partial}` : ''}`
    + `<br>K/S ${ks(model.grid[i])} · ${L.strikeEq} ${model.strikes[i] == null ? nd : priceText(model.strikes[i], nd)}`
    + `<br>${L.ivGrid} <b>${ivText(r.iv[i], nd, 2)}</b>`;
  const text = model.rows.map(r => model.grid.map((_, i) => r.iv[i] == null ? '' : cellText(r, i)));
  const traces: any[] = [{
    type: 'surface', name: L.surface, meta: 'surface', x: xs, y, z, text, hoverinfo: 'text', connectgaps: false,
    colorscale: p.scale, cmin: cmin ?? undefined, cmax: cmax ?? undefined, opacity: 0.94,
    lighting: { ambient: 0.72, diffuse: 0.6, specular: 0.12, roughness: 0.85, fresnel: 0.05 },
    contours: {
      x: { show: true, color: p.line, width: 1, highlight: false },
      y: { show: true, color: p.line, width: 1, highlight: false },
      z: { show: false, highlight: false },
    },
    colorbar: { thickness: 10, len: 0.62, outlinewidth: 0, x: 1.0, ticksuffix: '%',
      tickfont: { color: p.muted, size: 12, family: 'Manrope Variable, Manrope, sans-serif' },
      title: { text: 'IV', font: { color: p.muted, size: 12, family: 'Manrope Variable, Manrope, sans-serif' } } },
  }];
  // ALTA-2: un marcatore per OGNI cella valida (anche quelle che la mesh non puo' chiudere vicino ai buchi)
  const cx: number[] = [], cy: number[] = [], cz: number[] = [], cc: (string | number)[][] = [], ct: string[] = [];
  model.rows.forEach(r => r.iv.forEach((v, i) => {
    if (v == null) return;
    cx.push(xs[i]); cy.push(r.days); cz.push(v * 100); cc.push([r.expiry, i]); ct.push(cellText(r, i));
  }));
  if (cx.length) traces.push({ type: 'scatter3d', mode: 'markers', name: L.cells || L.ivGrid, meta: 'cells', x: cx, y: cy, z: cz,
    customdata: cc, text: ct, hoverinfo: 'text',
    marker: { symbol: 'square', size: 2.4, color: cz, colorscale: p.scale, cmin: cmin ?? undefined, cmax: cmax ?? undefined,
      opacity: 0.95, line: { color: p.line, width: 0.5 } } });
  // buchi: una croce sul pavimento per ogni cella null (dichiarata, mai riempita)
  const hx: number[] = [], hy: number[] = [], hz: number[] = [], hc: (string | number)[][] = [], ht: string[] = [];
  model.rows.forEach(r => r.iv.forEach((v, i) => {
    if (v != null) return;
    hx.push(xs[i]); hy.push(r.days); hz.push(floor); hc.push([r.expiry, i]);
    ht.push(`<b>${r.expiry}</b> · ${r.days}${L.d}<br>K/S ${ks(model.grid[i])}<br>${L.hole}`);
  }));
  if (hx.length) traces.push({ type: 'scatter3d', mode: 'markers', name: L.hole, meta: 'holes', x: hx, y: hy, z: hz, customdata: hc,
    text: ht, hoverinfo: 'text', marker: { symbol: 'x', size: 2.6, color: p.muted, opacity: 0.55 } });
  // quote misurate: liquide, illiquide, segnalate per qualita' (segno diverso)
  const lo = model.grid[0], hi = model.grid[model.grid.length - 1];
  // MEDIA-8 anche in 3D: le quote fuori dalla scala di celle + quote liquide non allungano l'asse IV (contate a parte)
  const { scale } = surfaceQuoteScale(model, opts.observed);
  const inScale = (q: ObservedQuote) => !scale || (q.iv >= scale.y0 && q.iv <= scale.y1);
  const kinds = [
    { meta: 'observed', name: L.observed, keep: (q: ObservedQuote) => q.liquid && !q.flagged, marker: { symbol: 'circle', size: 2.6, color: p.text, opacity: 0.9 } },
    { meta: 'illiquid', name: L.illiquid, keep: (q: ObservedQuote) => !q.liquid && !q.flagged, marker: { symbol: 'circle-open', size: 3, color: p.text, opacity: 0.9 } },
    { meta: 'flagged', name: L.flagged || L.observed, keep: (q: ObservedQuote) => q.flagged, marker: { symbol: 'diamond-open', size: 3.4, color: p.warn, opacity: 0.95 } },
  ];
  for (const kind of kinds) {
    const ox: number[] = [], oy: number[] = [], oz: number[] = [], oc: (string | number)[][] = [], ot: string[] = [];
    model.rows.forEach(r => (opts.observed[r.expiry] || []).forEach(q => {
      if (!q.otm || !kind.keep(q) || !inScale(q) || q.m < lo - 1e-9 || q.m > hi + 1e-9) return;
      ox.push(strikeAxis ? q.strike : q.m); oy.push(r.days); oz.push(q.iv * 100);
      oc.push([r.expiry, nearestIndex(model.grid, q.m)]);
      ot.push(`<b>${kind.name}</b><br>${q.type === 'put' ? 'Put' : 'Call'} ${priceText(q.strike, nd)} · ${r.expiry} · ${r.days}${L.d}`
        + `<br>IV <b>${ivText(q.iv, nd, 2)}</b> · Bid ${priceText(q.bid, nd)} / Ask ${priceText(q.ask, nd)} · OI ${q.oi == null ? nd : q.oi}`
        + (q.flags.length ? `<br>⚠ ${q.flags.slice(0, 3).join(' · ')}` : ''));
    }));
    if (ox.length) traces.push({ type: 'scatter3d', mode: 'markers', name: kind.name, meta: kind.meta, x: ox, y: oy, z: oz,
      customdata: oc, text: ot, hoverinfo: 'text', marker: kind.marker });
  }
  const row = model.rows.find(r => r.expiry === opts.expiry);
  if (row) traces.push({ type: 'scatter3d', mode: 'lines', name: L.selExpiry, meta: 'sel-expiry', x: xs, y: xs.map(() => row.days),
    z: row.iv.map(v => v == null ? null : v * 100), connectgaps: false, line: { color: p.accent, width: 7 }, hoverinfo: 'skip' });
  if (opts.column != null && opts.column >= 0 && opts.column < model.grid.length) {
    const c = opts.column;
    traces.push({ type: 'scatter3d', mode: 'lines', name: L.selCol, meta: 'sel-col', x: model.rows.map(() => xs[c]), y,
      z: model.rows.map(r => r.iv[c] == null ? null : (r.iv[c] as number) * 100), connectgaps: false,
      line: { color: p.violet, width: 7 }, hoverinfo: 'skip' });
  }
  const axisStyle = (title: string, suffix = '') => ({
    title: { text: title, font: { size: 12, color: p.muted, family: 'Manrope Variable, Manrope, sans-serif' } },
    tickfont: { size: 11, color: p.muted, family: 'Manrope Variable, Manrope, sans-serif' },
    gridcolor: p.line, zerolinecolor: p.line, linecolor: p.line, showbackground: false, ticksuffix: suffix, showspikes: false,
  });
  const layout = {
    paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', height: 520, showlegend: false,
    margin: { l: 0, r: 0, t: 0, b: 0 }, uirevision: 'bellomberg-vol-surface',
    separators: linguaCorrente() === 'it' ? ',.' : '.,',
    font: { family: 'Manrope Variable, Manrope, sans-serif', color: p.text },
    hoverlabel: { bgcolor: p.card, bordercolor: p.line, font: { family: 'Manrope Variable, Manrope, sans-serif', size: 12, color: p.text } },
    scene: {
      xaxis: { ...axisStyle(strikeAxis ? 'Strike' : 'K/S'), tickformat: strikeAxis ? '.0f' : '.2f' },
      // ALTA-3: Plotly tagliava il titolo nativo dell'asse dei giorni; l'etichetta e' l'HTML #vol-surface-expiry-axis-label
      yaxis: axisStyle(''),
      zaxis: axisStyle('IV', '%'),
      bgcolor: 'rgba(0,0,0,0)', camera: DEFAULT_CAMERA, uirevision: SCENE_REVISION,
      aspectratio: { x: 1.25, y: 1.55, z: 0.72 }, dragmode: 'orbit',
    },
  };
  return { traces, layout, config: { displayModeBar: false, responsive: true, scrollZoom: true } };
}

const readPalette = (node: HTMLElement, dark: boolean): Palette => {
  const css = getComputedStyle(node), v = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
  return { text: v('--bbn-text', dark ? '#ffffff' : '#0a0a0b'), muted: v('--bbn-muted', dark ? '#a1a1aa' : '#62626b'),
    line: v('--vdn-grid', dark ? '#2e2e33' : '#e2e2e8'), accent: v('--bbn-accent', dark ? '#5b8cff' : '#1d56f0'),
    violet: v('--vdn-violet', dark ? '#c4b5fd' : '#7c3aed'), warn: v('--bbn-warn', '#8a5300'), card: v('--bbn-card', dark ? '#161618' : '#ffffff'),
    scale: dark ? SCALE_DARK : SCALE_LIGHT };
};

export default function Surface3D({ model, axis, expiry, column, observed, dark, onPick, onError, resetKey }: {
  model: SurfaceModel; axis: AxisMode; expiry: string | null; column: number | null;
  observed: Record<string, ObservedQuote[] | undefined>; dark: boolean;
  onPick: (expiry: string, column: number) => void; onError: (message: string) => void; resetKey: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const pickRef = useRef(onPick); pickRef.current = onPick;
  const modelRef = useRef(model); modelRef.current = model;
  const axisRef = useRef(axis); axisRef.current = axis;

  useEffect(() => {
    let active = true;
    loadPlotly().then(async Plotly => {
      if (!active || !ref.current) return;
      const node = ref.current as HTMLDivElement & { _fullLayout?: unknown; on?: Function; __vdnBound?: boolean };
      const labels = { na: tr('voldeck.ui_n_a_15'), d: tr('voldeck.short_days'), surface: tr('voldeck.n_surface_title'),
        strikeEq: tr('voldeck.n_strike_eq_short'), ivGrid: tr('voldeck.n_iv_grid'), hole: tr('voldeck.n_hole_word'),
        observed: tr('voldeck.n_observed_short'), illiquid: tr('voldeck.n_illiquid_short'), flagged: tr('voldeck.n_flagged_short'),
        cells: tr('voldeck.n_legend_cells'), selExpiry: tr('voldeck.n_legend_sel_expiry'),
        selCol: tr('voldeck.n_legend_sel_col'), days: tr('voldeck.ui_days_to_expiry_53'), partial: tr('voldeck.n_partial_badge') };
      const fig = surfaceFigure(model, { axis, expiry, column, observed, palette: readPalette(node, dark), labels });
      // il primo disegno crea il grafico; i successivi lo aggiornano (react) e la camera dell'utente resta
      const draw = queue.current.catch(() => undefined).then(() => {
        if (!active || !ref.current) return;
        return node._fullLayout ? Plotly.react(node, fig.traces, fig.layout, fig.config) : Plotly.newPlot(node, fig.traces, fig.layout, fig.config);
      });
      queue.current = draw;
      await draw;
      if (active && node.on && !node.__vdnBound) {
        node.__vdnBound = true;
        node.on('plotly_click', (event: any) => {
          const picked = pickFromPoint(event?.points?.[0], modelRef.current, axisRef.current);
          if (picked) pickRef.current(picked.expiry, picked.column);
        });
      }
    }).catch(e => { if (active) onError(e instanceof Error ? e.message : String(e)); });
    return () => { active = false; };
  }, [model, axis, expiry, column, observed, dark]);

  useEffect(() => {
    if (!resetKey || !ref.current || !window.Plotly) return;
    const node = ref.current as HTMLDivElement & { _fullLayout?: unknown };
    if (node._fullLayout) window.Plotly.relayout(node, { 'scene.camera': DEFAULT_CAMERA });
  }, [resetKey]);

  // il nodo si cattura al montaggio: allo smontaggio React ha gia' azzerato ref.current (v1: purge mai eseguito)
  useEffect(() => {
    const node = ref.current;
    return () => { if (node && window.Plotly) { try { window.Plotly.purge(node); } catch { /* gia' rimosso */ } } };
  }, []);

  return <div ref={ref} className="vdn-plot" data-vol-3d role="group" aria-roledescription={tr('voldeck.n_interactive_chart')}
    aria-label={tr('voldeck.mesh_label')} aria-describedby="vol-surface-expiry-axis-label" />;
}
