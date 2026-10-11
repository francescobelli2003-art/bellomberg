import { useEffect, useRef } from 'react';
import { t as tr } from '@/i18n/t';
import { linguaCorrente } from '@/i18n/lingua';
import { finite, ivText, numText, pickFromPoint, priceText, type SurfaceModel } from '@/lib/vol-deck';
import type { AxisMode } from './SliceCharts';

/* ============================================================================
   Superficie IV 3D ruotabile (Vol Deck Nuova). Plotly locale gia' presente nell'app
   (public/vendor/plotly-2.32.0.min.js): nessuna libreria nuova.
   v3 «istituzionale» (10/10/2026, Opus 5.5 — impianto Vol-B scelto dal PM, stile OVDV 3D):
   - la superficie e' un `mesh3d` fatto SOLO dei quadrilateri coi 4 vertici CON UN VALORE (celle della
     griglia del builder, interpolata fra strike osservati: non quote misurate): un buco resta un buco
     rettangolare (nessun triangolo spurio sulle ali, nessuna cella riempita) + wireframe sottile lungo
     righe e colonne, spezzato a ogni buco;
   - v2 (review 10/10): ogni cella con un valore che NON e' vertice di una faccia ha un marcatore con
     lettura e clic (il wireframe non ha hover: prima una cella toccata solo dal tratto era muta);
   - i buchi restano dichiarati: croce sul pavimento (traccia `holes`) e «n.d.» tratteggiato nella griglia;
   - niente quote misurate ne' marcatori di cella sopra la superficie: le quote vivono sullo smile 2D;
   - scala colore continua blu scuro (IV bassa) → blu → ciano → verde → giallo → arancio → rosso (IV alta),
     uguale nei due temi, con colorbar verticale in % IV; satura solo il COLORE al p1/p99, mai la geometria;
   - asse scadenze in √t con etichette in giorni;
   - camera: quella VIVA (scene._scene.getCamera) si rilegge prima di ogni ridisegno e si passa esplicita
     nel layout; al rilascio del mouse OVUNQUE (listener su window) la camera viva si scrive nel layout.
     Prima (diagnosi 09/10, diag3d_f): un drag rilasciato fuori dal canvas non emetteva relayout e il
     ridisegno successivo riportava indietro la vista. `turntable` (niente capovolgimenti), rotella spenta
     (scorre la pagina), preset di camera;
   - un cambio di selezione (scadenza / K/S) aggiorna con `Plotly.restyle` SOLO le tre tracce di
     selezione; `react` dell'intera figura solo se cambiano dati, asse o tema.
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

type V3 = { x: number; y: number; z: number };
export interface Camera { eye: V3; up: V3; center: V3 }
export type CameraPreset = 'perspective' | 'top' | 'smile' | 'term';
export const CAMERAS: Record<CameraPreset, Camera> = {
  perspective: { eye: { x: 1.25, y: -1.95, z: 0.95 }, up: { x: 0, y: 0, z: 1 }, center: { x: 0.04, y: 0, z: -0.16 } },
  top: { eye: { x: 0, y: -0.01, z: 2.7 }, up: { x: 0, y: 1, z: 0 }, center: { x: 0, y: 0.04, z: 0 } },
  smile: { eye: { x: 0, y: -2.25, z: 0.12 }, up: { x: 0, y: 0, z: 1 }, center: { x: 0, y: 0, z: -0.08 } },
  term: { eye: { x: 2.3, y: 0, z: 0.12 }, up: { x: 0, y: 0, z: 1 }, center: { x: 0, y: 0, z: -0.08 } },
};
export const CAMERA_PRESETS = Object.keys(CAMERAS) as CameraPreset[];
export const DEFAULT_CAMERA: Camera = CAMERAS.perspective;
export const SCENE_REVISION = 'bellomberg-vol-surface-camera';
const plainCamera = (c: any): Camera => ({ eye: { ...c.eye }, up: { ...c.up }, center: { ...c.center } });
const gap = (a: V3, b: V3) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
const camDist = (a: Camera, b: Camera) => gap(a.eye, b.eye) + gap(a.up, b.up) + gap(a.center, b.center);

/** La camera che l'utente VEDE: quella della scena WebGL, non quella (forse vecchia) scritta nel layout. */
export function liveCamera(node: any): Camera | null {
  try {
    const c = node?._fullLayout?.scene?._scene?.getCamera?.();
    if (c?.eye && c?.up && c?.center) return plainCamera(c);
  } catch { /* scena non pronta */ }
  const c = node?._fullLayout?.scene?.camera;
  return c?.eye && c?.up && c?.center ? plainCamera(c) : null;
}

/** p-esimo percentile (indice per difetto): satura solo la scala COLORE, mai la geometria. */
export function pctile(arr: number[], p: number): number | null {
  if (!arr.length) return null;
  const a = [...arr].sort((x, y) => x - y);
  return a[Math.min(a.length - 1, Math.max(0, Math.floor(p * (a.length - 1))))];
}

/** Scala «OVDV»: blu scuro = IV bassa … rosso = IV alta. La stessa nei due temi (la legge la colorbar). */
export const SCALE_VOL: [number, string][] = [
  [0, '#0b1e6e'], [0.16, '#1d4ed8'], [0.33, '#06b6d4'], [0.5, '#22c55e'], [0.67, '#facc15'], [0.84, '#f97316'], [1, '#dc2626'],
];
/** Alias storici (test e chiamanti): i due temi condividono la scala, cambia solo lo sfondo. */
export const SCALE_LIGHT = SCALE_VOL;
export const SCALE_DARK = SCALE_VOL;

const hexRgb = (hex: string): [number, number, number] => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16)) as [number, number, number];
/** Colore della scala in t ∈ [0,1] (interpolazione lineare RGB, come Plotly). */
export function scaleColor(t: number, scale: [number, string][] = SCALE_VOL): [number, number, number] {
  const x = Math.max(0, Math.min(1, finite(t) ? t : 0));
  for (let i = 1; i < scale.length; i++) {
    if (x <= scale[i][0]) {
      const [a, ca] = scale[i - 1], [b, cb] = scale[i];
      const f = (x - a) / ((b - a) || 1), A = hexRgb(ca), B = hexRgb(cb);
      return A.map((v, k) => Math.round(v + (B[k] - v) * f)) as [number, number, number];
    }
  }
  return hexRgb(scale[scale.length - 1][1]);
}
/** Testo leggibile su un fondo: vince fra bianco e nero quello col contrasto WCAG maggiore. */
export function inkOn([r, g, b]: [number, number, number]): '#0a0a0b' | '#ffffff' {
  const lin = (c: number) => { const s = c / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
  const L = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
  return 1.05 / (L + 0.05) >= (L + 0.05) / 0.05 ? '#ffffff' : '#0a0a0b';
}

/** Estremi della scala colore in % IV (p1/p99 delle celle): la stessa per 3D e griglia numerica. */
export function colorRange(model: SurfaceModel): { cmin: number; cmax: number } | null {
  const flat = model.rows.flatMap(r => r.iv.filter(finite).map(v => v * 100));
  const cmin = pctile(flat, 0.01), cmax = pctile(flat, 0.99);
  return cmin == null || cmax == null ? null : { cmin, cmax: cmax > cmin ? cmax : cmin + 0.01 };
}

/** Scala divergente del confronto «ΔIV vs data»: blu = vol scesa, bianco = 0, rosso = vol salita. */
export const SCALE_DIFF: [number, string][] = [[0, '#1e3a8a'], [0.25, '#3b82f6'], [0.5, '#f4f4f5'], [0.75, '#f87171'], [1, '#b91c1c']];
/** Colore di una cella senza confronto (n.d.): grigio neutro, distinto dal bianco dello 0. */
export const DIFF_NA = '#a1a1aa';

/** Asse x diverso da K/S e strike (asse delta): tacche, titolo e riga di lettura di ogni cella. */
export interface XAxisSpec { title: string; ticktext: string[]; cellLabel: (row: number, col: number) => string }
/** Modalita' colore: livello IV (default) o ΔIV rispetto a un'istantanea precedente (punti vol). */
export type ColorSpec = { kind: 'level' } | { kind: 'diff'; cells: (number | null)[][]; range: number | null; title: string; line: (row: number, col: number) => string };
/** Contesto per le sovrapposizioni: coordinate gia' calcolate della figura. */
export interface OverlayCtx { xs: number[]; ys: number[]; floor: number; zTop: number; strikeAxis: boolean }

export interface Palette { text: string; muted: string; line: string; accent: string; violet: string; warn: string; card: string; scale: [number, string][]; wall?: string; wire?: string }

/** Tacche dell'asse √t: una per scadenza, diradate perche' le etichette non si sovrappongano. */
export function sqrtDayTicks(days: number[], minGap = 0.07): number[] {
  const ys = days.map(d => Math.sqrt(Math.max(0, d)));
  if (!ys.length) return [];
  const span = (ys[ys.length - 1] - ys[0]) || 1, keep: number[] = [0];
  for (let i = 1; i < ys.length; i++) if ((ys[i] - ys[keep[keep.length - 1]]) / span >= minGap) keep.push(i);
  const last = ys.length - 1;
  if (keep[keep.length - 1] !== last) {
    if (keep.length > 1 && (ys[last] - ys[keep[keep.length - 1]]) / span < minGap) keep.pop();
    keep.push(last);
  }
  return keep;
}

type Opts = { axis: AxisMode; expiry: string | null; column: number | null; palette: Palette; labels: Record<string, string>; camera?: Camera;
  xAxis?: XAxisSpec | null; color?: ColorSpec | null; overlays?: ((ctx: OverlayCtx) => any[]) | null; height?: number };

/** Le tre tracce di selezione (scadenza, K/S, punto), sempre presenti e in coda: il componente le
 *  aggiorna con restyle senza ridisegnare la superficie. Una cella buco resta null (linea spezzata). */
export function selectionTraces(model: SurfaceModel, opts: Pick<Opts, 'axis' | 'expiry' | 'column' | 'palette'>) {
  const p = opts.palette;
  const strikeAxis = opts.axis === 'strike' && model.spot != null;
  const xs = strikeAxis ? model.strikes.map(k => k as number) : model.grid;
  const ys = model.rows.map(r => Math.sqrt(r.days));
  const row = model.rows.find(r => r.expiry === opts.expiry) || null;
  const c = opts.column != null && opts.column >= 0 && opts.column < model.grid.length ? opts.column : null;
  const cell = row && c != null ? row.iv[c] : null;
  const point = row && c != null && cell != null;
  return [
    { type: 'scatter3d', mode: 'lines', meta: 'sel-expiry', name: 'sel-expiry', x: row ? xs : [], y: row ? xs.map(() => Math.sqrt(row.days)) : [],
      z: row ? row.iv.map(v => v == null ? null : v * 100) : [], connectgaps: false, line: { color: p.text, width: 5 }, hoverinfo: 'skip' },
    { type: 'scatter3d', mode: 'lines', meta: 'sel-col', name: 'sel-col', x: c != null ? model.rows.map(() => xs[c]) : [], y: c != null ? ys : [],
      z: c != null ? model.rows.map(r => r.iv[c] == null ? null : (r.iv[c] as number) * 100) : [], connectgaps: false,
      line: { color: p.text, width: 5, dash: 'dash' }, hoverinfo: 'skip' },
    { type: 'scatter3d', mode: 'markers', meta: 'sel-point', name: 'sel-point', x: point ? [xs[c as number]] : [],
      y: point ? [Math.sqrt((row as SurfaceModel['rows'][number]).days)] : [], z: point ? [(cell as number) * 100] : [],
      marker: { symbol: 'diamond', size: 7, color: p.text, line: { color: p.card, width: 2 } }, hoverinfo: 'skip' },
  ];
}

/** Tracce e scena: funzione pura (provata dai test), la stessa che il componente passa a Plotly.
 *  Convenzione customdata di OGNI traccia cliccabile: [0] = scadenza, [1] = indice di colonna K/S. */
export function surfaceFigure(model: SurfaceModel, opts: Opts) {
  const { axis, palette: p, labels: L } = opts;
  const strikeAxis = axis === 'strike' && model.spot != null;
  const xs = strikeAxis ? model.strikes.map(k => k as number) : model.grid;
  const ys = model.rows.map(r => Math.sqrt(r.days));
  const flat = model.rows.flatMap(r => r.iv.filter(finite).map(v => v * 100));
  const range = colorRange(model);
  const zmin = flat.length ? Math.min(...flat) : 0, zmax = flat.length ? Math.max(...flat) : 1;
  const floor = flat.length ? zmin - Math.max(0.6, (zmax - zmin) * 0.08) : 0;
  const nd = L.na;
  const ks = (m: number) => numText(m, 3, nd);
  const xAxis = opts.xAxis || null, diff = opts.color?.kind === 'diff' ? opts.color : null;
  const rowIndex = new Map(model.rows.map((r, j) => [r.expiry, j]));
  const cellText = (r: SurfaceModel['rows'][number], i: number) => `<b>${r.expiry}</b> · ${r.days}${L.d}${r.partial ? ` · ${L.partial}` : ''}`
    + (xAxis ? `<br>${xAxis.cellLabel(rowIndex.get(r.expiry) as number, i)}`
      : `<br>K/S ${ks(model.grid[i])} · ${L.strikeEq} ${model.strikes[i] == null ? nd : priceText(model.strikes[i], nd)}`)
    + `<br>${L.ivGrid} <b>${ivText(r.iv[i], nd, 2)}</b>`
    + (diff ? `<br>${diff.line(rowIndex.get(r.expiry) as number, i)}` : '');
  // ΔIV: colore per vertice dalla scala divergente simmetrica (±range); una cella senza confronto e' grigia
  const diffRgb = (j: number, i: number): string => {
    const v = diff?.cells[j]?.[i];
    if (!diff || !finite(v) || !finite(diff.range) || !(diff.range > 0)) return DIFF_NA;
    const [r, g, b] = scaleColor(0.5 + v / (2 * diff.range), SCALE_DIFF);
    return `rgb(${r},${g},${b})`;
  };
  // vertici = sole celle con un valore; facce = soli quadrilateri coi 4 vertici con un valore (2 triangoli)
  const vx: number[] = [], vy: number[] = [], vz: number[] = [], vc: [string, number][] = [], vt: string[] = [], vcol: string[] = [];
  const at = new Map<string, number>();
  model.rows.forEach((r, j) => r.iv.forEach((v, i) => {
    if (v == null) return;
    at.set(j + ':' + i, vx.length);
    vx.push(xs[i]); vy.push(ys[j]); vz.push(v * 100); vc.push([r.expiry, i]); vt.push(cellText(r, i)); vcol.push(diffRgb(j, i));
  }));
  const fi: number[] = [], fj: number[] = [], fk: number[] = [];
  const touched = new Set<number>();
  for (let j = 0; j < model.rows.length - 1; j++) for (let i = 0; i < xs.length - 1; i++) {
    const a = at.get(j + ':' + i), b = at.get(j + ':' + (i + 1)), c = at.get((j + 1) + ':' + (i + 1)), d = at.get((j + 1) + ':' + i);
    if (a == null || b == null || c == null || d == null) continue;
    fi.push(a, a); fj.push(b, c); fk.push(c, d);
    [a, b, c, d].forEach(v => touched.add(v));
  }
  const font = 'Manrope Variable, Manrope, sans-serif';
  const traces: any[] = [{
    type: 'mesh3d', name: L.surface, meta: 'surface', x: vx, y: vy, z: vz, i: fi, j: fj, k: fk,
    ...(diff ? { vertexcolor: vcol, showscale: false }
      : { intensity: vz, intensitymode: 'vertex', colorscale: p.scale, cmin: range?.cmin, cmax: range?.cmax, showscale: true,
        colorbar: { thickness: 12, len: 0.78, outlinewidth: 0, x: 1.0, xpad: 4, ticksuffix: '%', nticks: 7,
          tickfont: { color: p.muted, size: 12, family: font },
          title: { text: L.ivScale || 'IV %', side: 'top', font: { color: p.muted, size: 12, family: font } } } }),
    customdata: vc, text: vt, hoverinfo: 'text', flatshading: false, opacity: 1,
    lighting: { ambient: 0.85, diffuse: 0.32, specular: 0.04, roughness: 0.95, fresnel: 0.02 },
  }];
  // wireframe: righe (scadenze) e colonne (K/S), spezzato a ogni buco; sollevato dello 0,8% dell'escursione
  // solo per non affondare nel mesh (z-fighting): e' disegno, la lettura esatta resta sul mesh
  const lift = (zmax - zmin) * 0.008;
  const wx: (number | null)[] = [], wy: (number | null)[] = [], wz: (number | null)[] = [];
  const run = (pts: ([number, number, number] | null)[]) => {
    for (const q of pts) { wx.push(q ? q[0] : null); wy.push(q ? q[1] : null); wz.push(q ? q[2] + lift : null); }
    wx.push(null); wy.push(null); wz.push(null);
  };
  model.rows.forEach((r, j) => run(r.iv.map((v, i) => v == null ? null : [xs[i], ys[j], v * 100])));
  xs.forEach((x, i) => run(model.rows.map((r, j) => r.iv[i] == null ? null : [x, ys[j], (r.iv[i] as number) * 100])));
  traces.push({ type: 'scatter3d', mode: 'lines', name: 'wire', meta: 'wire', x: wx, y: wy, z: wz, connectgaps: false,
    line: { color: p.wire || 'rgba(10,10,12,.45)', width: 1.2 }, hoverinfo: 'skip' });
  // celle con un valore che non sono vertice di nessuna faccia (una sola scadenza, colonna buca, ali che si
  // allargano, celle fra buchi): un marcatore con lettura e clic, cosi' nessun valore resta muto a schermo
  const orphans = vx.map((_, n) => n).filter(n => !touched.has(n));
  if (orphans.length) traces.push({ type: 'scatter3d', mode: 'markers', name: L.ivGrid, meta: 'orphans',
    x: orphans.map(n => vx[n]), y: orphans.map(n => vy[n]), z: orphans.map(n => vz[n]), customdata: orphans.map(n => vc[n]),
    text: orphans.map(n => vt[n]), hoverinfo: 'text',
    marker: { symbol: 'square', size: 4.5, ...(diff ? { color: orphans.map(n => vcol[n]) } : { color: orphans.map(n => vz[n]), colorscale: p.scale, cmin: range?.cmin, cmax: range?.cmax }),
      line: { color: p.line, width: 0.5 } } });
  // ΔIV: la scala divergente (in punti vol) la porta una traccia senza punti visibili, solo per la colorbar
  if (diff && finite(diff.range) && diff.range > 0) traces.push({ type: 'scatter3d', mode: 'markers', name: 'diff-scale', meta: 'diff-scale',
    x: [xs[0]], y: [ys[0]], z: [zmin], hoverinfo: 'skip', showlegend: false,
    marker: { size: 0.1, opacity: 0, color: [0], colorscale: SCALE_DIFF, cmin: -diff.range * 100, cmax: diff.range * 100, showscale: true,
      colorbar: { thickness: 12, len: 0.78, outlinewidth: 0, x: 1.0, xpad: 4, ticksuffix: ' pt', nticks: 7,
        tickfont: { color: p.muted, size: 12, family: font }, title: { text: diff.title, side: 'top', font: { color: p.muted, size: 12, family: font } } } } });
  // buchi: una croce sul pavimento per ogni cella null (dichiarata, mai riempita)
  const hx: number[] = [], hy: number[] = [], hz: number[] = [], hc: [string, number][] = [], ht: string[] = [];
  model.rows.forEach((r, j) => r.iv.forEach((v, i) => {
    if (v != null) return;
    hx.push(xs[i]); hy.push(ys[j]); hz.push(floor); hc.push([r.expiry, i]);
    ht.push(`<b>${r.expiry}</b> · ${r.days}${L.d}<br>${xAxis ? xAxis.cellLabel(j, i) : 'K/S ' + ks(model.grid[i])}<br>${L.hole}`);
  }));
  if (hx.length) traces.push({ type: 'scatter3d', mode: 'markers', name: L.hole, meta: 'holes', x: hx, y: hy, z: hz, customdata: hc,
    text: ht, hoverinfo: 'text', marker: { symbol: 'x', size: 2.6, color: p.muted, opacity: 0.6 } });
  // sovrapposizioni (forward, cono, utili, arbitraggi): prima della selezione, che resta in coda per il restyle
  if (opts.overlays) traces.push(...opts.overlays({ xs, ys, floor, zTop: flat.length ? zmax : 1, strikeAxis }));
  const sel = selectionTraces(model, opts);
  const selIndex = sel.map((_, n) => traces.length + n);
  traces.push(...sel);

  const tickIdx = sqrtDayTicks(model.rows.map(r => r.days));
  const axisStyle = (title: string, extra: Record<string, unknown> = {}) => ({
    title: { text: title, font: { size: 12, color: p.muted, family: font } },
    tickfont: { size: 12, color: p.muted, family: font },
    gridcolor: p.line, zerolinecolor: p.line, linecolor: p.line, showline: true,
    showbackground: true, backgroundcolor: p.wall || 'rgba(0,0,0,0)',
    // crosshair 3D: al passaggio Plotly traccia le linee di lettura fino alle pareti
    showspikes: true, spikecolor: p.muted, spikethickness: 1, spikesides: false,
    ...extra,
  });
  const layout = {
    paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', height: opts.height ?? 560, showlegend: false,
    margin: { l: 0, r: 0, t: 0, b: 0 }, uirevision: 'bellomberg-vol-surface',
    separators: linguaCorrente() === 'it' ? ',.' : '.,',
    font: { family: font, color: p.text },
    hoverlabel: { bgcolor: p.card, bordercolor: p.line, font: { family: font, size: 12, color: p.text } },
    scene: {
      xaxis: xAxis ? axisStyle(xAxis.title, { tickmode: 'array', tickvals: xs, ticktext: xAxis.ticktext })
        : axisStyle(strikeAxis ? 'Strike' : 'K/S', { tickformat: strikeAxis ? ',.0f' : '.2f', nticks: 6 }),
      // ALTA-3: Plotly tagliava il titolo nativo dell'asse dei giorni; l'etichetta e' l'HTML #vol-surface-expiry-axis-label
      yaxis: axisStyle('', { tickmode: 'array', tickvals: tickIdx.map(n => ys[n]), ticktext: tickIdx.map(n => `${model.rows[n].days}${L.d}`) }),
      zaxis: axisStyle('IV', { ticksuffix: '%', nticks: 6, range: flat.length ? [floor, zmax + (zmax - zmin) * 0.04 + 0.1] : undefined }),
      bgcolor: 'rgba(0,0,0,0)', camera: plainCamera(opts.camera || DEFAULT_CAMERA), uirevision: SCENE_REVISION,
      aspectmode: 'manual', aspectratio: { x: 1.7, y: 1.45, z: 0.72 }, dragmode: 'turntable',
    },
  };
  return { traces, layout, selIndex, config: { displayModeBar: false, responsive: true, scrollZoom: false } };
}

const readPalette = (node: HTMLElement, dark: boolean): Palette => {
  const css = getComputedStyle(node), v = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
  return { text: v('--bbn-text', dark ? '#ffffff' : '#0a0a0b'), muted: v('--bbn-muted', dark ? '#a1a1aa' : '#62626b'),
    line: v('--vdn-grid', dark ? '#2e2e33' : '#e2e2e8'), accent: v('--bbn-accent', dark ? '#5b8cff' : '#1d56f0'),
    violet: v('--vdn-violet', dark ? '#c4b5fd' : '#7c3aed'), warn: v('--bbn-warn', '#8a5300'), card: v('--bbn-card', dark ? '#161618' : '#ffffff'),
    wall: v('--vdn-wall', dark ? '#141418' : '#f4f4f7'), wire: dark ? 'rgba(0,0,0,.55)' : 'rgba(15,23,42,.55)', scale: SCALE_VOL };
};

/** Oltre questo spostamento (px) fra pressione e rilascio il gesto e' una rotazione, non un clic. */
export const DRAG_PX = 4;
type PlotNode = HTMLDivElement & { _fullLayout?: any; on?: Function; __vdnBound?: boolean };
const mark = (node: PlotNode, key: string, value: string) => { try { node.setAttribute(key, value); } catch { /* DOM minimo */ } };

/** Parte della figura oltre al modello (asse delta, colore ΔIV, sovrapposizioni): un oggetto NUOVO
 *  = ridisegno completo (react), lo stesso oggetto = solo restyle della selezione. */
export interface FigureExtras { xAxis?: XAxisSpec | null; color?: ColorSpec | null; overlays?: ((ctx: OverlayCtx) => any[]) | null }
const NO_EXTRAS: FigureExtras = {};

export default function Surface3D({ model, axis, expiry, column, dark, onPick, onError, resetKey, preset, onUserRotate, extras = NO_EXTRAS, height = 560 }: {
  model: SurfaceModel; axis: AxisMode; expiry: string | null; column: number | null; dark: boolean; extras?: FigureExtras;
  /** altezza del grafico in px (la sotto-pagina Superficie la sceglie per stare in una schermata) */
  height?: number;
  onPick: (expiry: string, column: number) => void; onError: (message: string) => void; resetKey: number;
  /** vista preimpostata chiesta dall'utente (n cresce a ogni clic, anche sulla stessa vista) */
  preset?: { name: CameraPreset | null; n: number };
  /** l'utente ha ruotato a mano: nessun preset e' piu' quello in vista */
  onUserRotate?: () => void;
}) {
  const ref = useRef<PlotNode>(null);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const pickRef = useRef(onPick); pickRef.current = onPick;
  const rotateRef = useRef(onUserRotate); rotateRef.current = onUserRotate;
  const modelRef = useRef(model); modelRef.current = model;
  const axisRef = useRef(axis); axisRef.current = axis;
  const camera = useRef<Camera>(plainCamera(DEFAULT_CAMERA));
  const drawn = useRef<{ model: SurfaceModel; axis: AxisMode; dark: boolean; extras: FigureExtras; selIndex: number[] } | null>(null);
  const press = useRef<{ x: number; y: number; moved: number } | null>(null);
  const pending = useRef<{ expiry: string; column: number } | null>(null);

  // camera viva -> layout: se la vista differisce da quella scritta, la si scrive (nessun salto: e' la stessa)
  const syncCamera = (Plotly: any, node: PlotNode): Promise<unknown> => {
    const live = liveCamera(node);
    if (!live) return Promise.resolve();
    camera.current = live;
    const written = node._fullLayout?.scene?.camera;
    if (written?.eye && written?.up && written?.center && camDist(plainCamera(written), live) < 1e-6) return Promise.resolve();
    return Promise.resolve(Plotly.relayout(node, { 'scene.camera': live }));
  };

  useEffect(() => {
    let active = true;
    loadPlotly().then(async Plotly => {
      if (!active || !ref.current) return;
      const node = ref.current;
      const labels = { na: tr('voldeck.ui_n_a_15'), d: tr('voldeck.short_days'), surface: tr('voldeck.n_surface_title'),
        strikeEq: tr('voldeck.n_strike_eq_short'), ivGrid: tr('voldeck.n_iv_grid'), hole: tr('voldeck.n_hole_word'),
        partial: tr('voldeck.n_partial_badge'), ivScale: tr('voldeck.n_iv_scale_title') };
      const palette = readPalette(node, dark);
      const draw = queue.current.catch(() => undefined).then(async () => {
        if (!active || !ref.current) return;
        const t0 = performance.now();
        const last = drawn.current;
        if (node._fullLayout && last && last.model === model && last.axis === axis && last.dark === dark && last.extras === extras) {
          // solo la selezione e' cambiata: restyle delle tre tracce di selezione, la superficie non si tocca
          await syncCamera(Plotly, node);
          const sel = selectionTraces(model, { axis, expiry, column, palette });
          await Plotly.restyle(node, { x: sel.map(t => t.x), y: sel.map(t => t.y), z: sel.map(t => t.z) }, last.selIndex);
          mark(node, 'data-vdn-draw', 'restyle');
        } else {
          if (node._fullLayout) await syncCamera(Plotly, node);
          const fig = surfaceFigure(model, { axis, expiry, column, palette, labels, camera: camera.current, height, ...extras });
          await (node._fullLayout ? Plotly.react(node, fig.traces, fig.layout, fig.config) : Plotly.newPlot(node, fig.traces, fig.layout, fig.config));
          drawn.current = { model, axis, dark, extras, selIndex: fig.selIndex };
          mark(node, 'data-vdn-draw', 'react');
        }
        mark(node, 'data-vdn-draw-ms', String(Math.round(performance.now() - t0)));
      });
      queue.current = draw;
      await draw;
      if (active && node.on && !node.__vdnBound) {
        node.__vdnBound = true;
        // gl3d emette plotly_click dal ciclo di render MENTRE il tasto e' premuto (gia' alla pressione, anche
        // quando poi si trascina per ruotare): durante una pressione il punto resta in sospeso e si applica
        // al rilascio solo se il puntatore si e' mosso al massimo DRAG_PX (vedi onUp)
        node.on('plotly_click', (event: any) => {
          const picked = pickFromPoint(event?.points?.[0], modelRef.current, axisRef.current, true);
          if (!picked) return;
          if (press.current) { pending.current = picked; return; }
          pickRef.current(picked.expiry, picked.column);
        });
        node.on('plotly_relayout', (event: any) => {
          const c = event?.['scene.camera'];
          if (c?.eye && c?.up && c?.center) camera.current = plainCamera(c);
        });
      }
    }).catch(e => { if (active) onError(e instanceof Error ? e.message : String(e)); });
    return () => { active = false; };
  }, [model, axis, expiry, column, dark, extras]);

  // rilascio del drag OVUNQUE (anche fuori dal canvas, sul pannello accanto): la vista viva va nel layout
  useEffect(() => {
    const onUp = () => {
      // clic o rotazione? il punto in sospeso si applica solo se il puntatore e' rimasto fermo
      const s = press.current, p = pending.current;
      press.current = null; pending.current = null;
      if (s && p && s.moved <= DRAG_PX) pickRef.current(p.expiry, p.column);
      if (s && s.moved > DRAG_PX) rotateRef.current?.();
      const node = ref.current, Plotly = window.Plotly;
      if (!node || !Plotly || !node._fullLayout) return;
      queue.current = queue.current.catch(() => undefined).then(() => syncCamera(Plotly, node)).catch(() => undefined);
    };
    // lo spostamento si misura anche fuori dal grafico (il drag puo' uscire dal canvas)
    const onMove = (e: Event) => {
      const p = e as PointerEvent, s = press.current;
      if (s && p.buttons) s.moved = Math.max(s.moved, Math.hypot(p.clientX - s.x, p.clientY - s.y));
    };
    window.addEventListener('mouseup', onUp);
    window.addEventListener('pointerup', onUp);
    // gesto interrotto dal sistema (tocco annullato, finestra persa): nessun clic, nessuna rotazione dichiarata
    const onCancel = () => { press.current = null; pending.current = null; };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointercancel', onCancel);
    return () => {
      window.removeEventListener('mouseup', onUp); window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointermove', onMove); window.removeEventListener('pointercancel', onCancel);
    };
  }, []);

  // rotella: scrollZoom false spegne lo zoom ma il controllo camera di Plotly 2.32 (mouse-wheel sul canvas)
  // chiama comunque preventDefault, e la pagina non scorreva (misurato 10/10: 0 px sopra il 3D, 600 px sulla
  // griglia). In CATTURA sul contenitore l'evento non raggiunge il canvas; nessun preventDefault: la pagina scorre.
  // drag vs clic: la pressione sul grafico apre un gesto (vedi plotly_click e onUp: misurato 10/10, una
  // rotazione rilasciata sul canvas cambiava scadenza e K/S).
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const onWheel = (e: Event) => { e.stopPropagation(); };
    const onDown = (e: Event) => { const p = e as PointerEvent; press.current = { x: p.clientX, y: p.clientY, moved: 0 }; pending.current = null; };
    const opts = { capture: true, passive: true };
    node.addEventListener('wheel', onWheel, opts);
    node.addEventListener('pointerdown', onDown, opts);
    return () => {
      node.removeEventListener('wheel', onWheel, { capture: true } as EventListenerOptions);
      node.removeEventListener('pointerdown', onDown, { capture: true } as EventListenerOptions);
    };
  }, []);

  const setView = (next: Camera) => {
    camera.current = plainCamera(next);
    const node = ref.current;
    if (node?._fullLayout && window.Plotly) {
      const Plotly = window.Plotly;
      queue.current = queue.current.catch(() => undefined).then(() => Plotly.relayout(node, { 'scene.camera': plainCamera(next) })).catch(() => undefined);
    }
  };
  useEffect(() => { if (resetKey) setView(DEFAULT_CAMERA); }, [resetKey]);
  useEffect(() => { if (preset?.name && preset.n) setView(CAMERAS[preset.name]); }, [preset?.name, preset?.n]);

  // il nodo si cattura al montaggio: allo smontaggio React ha gia' azzerato ref.current (v1: purge mai eseguito)
  useEffect(() => {
    const node = ref.current;
    return () => { if (node && window.Plotly) { try { window.Plotly.purge(node); } catch { /* gia' rimosso */ } } };
  }, []);

  return <div ref={ref} className="vdn-plot" data-vol-3d role="group" aria-roledescription={tr('voldeck.n_interactive_chart')} style={{ minHeight: height }}
    aria-label={tr('voldeck.mesh_label')} aria-describedby="vol-surface-expiry-axis-label" />;
}
