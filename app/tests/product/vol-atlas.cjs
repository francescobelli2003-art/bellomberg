const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const vm = require('node:vm');
const scope = { exports: {} };
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.resolve(__dirname, '../../src/lib/vol-atlas.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, scope);
const { visibleSurface, surfaceExpiries, toggleExpiry } = scope.exports;
const fixture = () => ({ ticker: 'DEMO', spot_est: 100, n_expiries: 3,
  moneyness_grid: [.9, 1, 1.1], expected_move_pct: 5,
  slices: ['2035-01-10', '2035-02-10', '2035-03-10'].map((expiry, i) => ({ expiry, days: 10 + i * 30, iv_grid: [.2, null, .3], atm_iv: .22 })),
  term_structure: ['2035-01-10', '2035-02-10', '2035-03-10'].map(expiry => ({ expiry, atm_iv: .22 })),
  coverage: { requested: ['2035-01-10', '2035-02-10', '2035-03-10', '2035-04-10'], complete: false }, download_id: 'job-demo' });
test('display selection preserves raw snapshot, null holes, coverage and job identity', () => {
  const raw = fixture(), before = JSON.stringify(raw);
  const selected = visibleSurface(raw, ['2035-02-10']);
  assert.equal(selected.slices.length, 1); assert.equal(selected.term_structure.length, 1);
  assert.equal(selected.slices[0].expiry, '2035-02-10');
  assert.equal(selected.slices[0].iv_grid[1], null);
  assert.equal(selected.download_id, 'job-demo');
  assert.equal(selected.coverage.requested.length, 4);
  assert.equal(selected.expected_move_pct, 5);
  assert.equal(JSON.stringify(raw), before);
});
test('all dates is explicit null, zero selected never silently restores the full plot', () => {
  const raw = fixture();
  assert.equal(visibleSurface(raw, null).slices.length, 3);
  assert.equal(visibleSurface(raw, []).slices.length, 0);
  assert.equal(visibleSurface(raw, ['not-in-snapshot']).slices.length, 0);
  assert.equal(visibleSurface(null, null), null);
});
test('date controls are stable and can remove the last selection and restore all', () => {
  const dates = Array.from(surfaceExpiries(fixture()));
  assert.deepEqual(dates, ['2035-01-10', '2035-02-10', '2035-03-10']);
  assert.deepEqual(Array.from(toggleExpiry(null, dates, dates[0])), dates.slice(1));
  assert.deepEqual(Array.from(toggleExpiry([dates[0]], dates, dates[0])), []);
  assert.deepEqual(Array.from(toggleExpiry([], dates, dates[0])), [dates[0]]);
  assert.throws(() => toggleExpiry(null, dates, 'not-in-snapshot'), /unknown/);
});

const { creaCaricatore, ambienteBrowser } = require('../i18n/_carica.cjs');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
ambienteBrowser();
const load = creaCaricatore({ stub: { './api': { API_BASE: 'http://synthetic.invalid', requestHeaders: () => ({}) } } });
const languages = load('i18n/lingua.ts');
const translate = load('i18n/t.ts');
const numbers = load('lib/vol-deck.ts');

test('both language catalogues have identical keys and placeholders, including Plotly hover variables', () => {
  const it = load('i18n/it/voldeck.ts').voldeck, en = load('i18n/en/voldeck.ts').voldeck;
  assert.deepEqual(Object.keys(it).sort(), Object.keys(en).sort());
  for (const key of Object.keys(it)) {
    assert.deepEqual([...it[key].matchAll(/\{([a-zA-Z_]+)\}/g)].map(m => m[1]).sort(),
      [...en[key].matchAll(/\{([a-zA-Z_]+)\}/g)].map(m => m[1]).sort(), key);
  }
});

test('empty actual page renders four workspaces in IT and EN without starting requests', () => {
  const Page = load('pages/VolSurfacePage.tsx').default;
  const previousFetch = global.fetch; let requests = 0;
  global.fetch = () => { requests++; throw new Error('SSR must not request data'); };
  try { for (const language of ['it', 'en', 'it']) {
    languages.impostaLinguaCorrente(language);
    const html = renderToStaticMarkup(React.createElement(Page));
    for (const mode of ['acquisition', 'tools', 'chain', 'laboratory']) assert.ok(html.includes('data-vol-workspace="' + mode + '"'));
    assert.ok(html.includes(language === 'it' ? 'Atlante della volatilità' : 'Volatility atlas'));
    assert.ok(html.includes(language === 'it' ? 'Scadenza chain' : 'Chain expiry'));
    // 09/10 (Opus 5.5): the option builder mounts on the first visit to Laboratory (it asks for the FRED rate)
    assert.ok(!html.includes('data-option-builder'), 'builder not mounted before the Laboratory is opened');
    assert.ok(!html.includes('⟦'), 'no missing translation marker');
    assert.equal(numbers.numericText(12.5), language === 'it' ? '12,5' : '12.5');
  } } finally { global.fetch = previousFetch; }
  assert.equal(requests, 0);
});

test('3D surface figure keeps holes, unclipped geometry, measured points apart and the camera across languages and themes', () => {
  // 09/10 (Opus 5.5): la superficie e' ora una funzione pura (surfaceFigure) provata qui sul file vero.
  const { surfaceFigure, SCALE_LIGHT, SCALE_DARK, SCENE_REVISION, DEFAULT_CAMERA } = load('pages/voldeck/Surface3D.tsx');
  const surface = fixture(); surface.slices[0].iv_grid = [.2, null, 2.5]; surface.slices[1].atm_iv = null;
  const model = numbers.surfaceModel(surface, ['2035-02-10']);
  const quote = (type, strike, iv, oi) => ({ type, strike, iv, oi, volume: oi ? 3 : 0, bid: 1, ask: 1.2, mid: 1.1, contract: null, expiry: '2035-01-10', quality: [] });
  const observed = { '2035-01-10': numbers.observedQuotes([quote('put', 90, .3, 10), quote('call', 110, .28, 0), quote('call', 95, .9, 10)], 100).quotes };
  const palette = (dark) => ({ text: '#000', muted: '#555', line: '#ddd', accent: '#00f', violet: '#70f', warn: '#850', card: '#fff', scale: dark ? SCALE_DARK : SCALE_LIGHT });
  const figures = [];
  for (const language of ['it', 'en']) for (const dark of [false, true]) {
    languages.impostaLinguaCorrente(language);
    const labels = { na: translate.t('voldeck.ui_n_a_15'), d: translate.t('voldeck.short_days'), surface: 's', strikeEq: 'k', ivGrid: translate.t('voldeck.n_iv_grid'),
      hole: translate.t('voldeck.n_hole_word'), observed: 'o', illiquid: 'i', selExpiry: 'e', selCol: 'c', days: translate.t('voldeck.ui_days_to_expiry_53'), partial: 'p' };
    figures.push({ language, dark, fig: surfaceFigure(model, { axis: 'moneyness', expiry: '2035-01-10', column: 1, observed, palette: palette(dark), labels }) });
  }
  for (const { language, dark, fig } of figures) {
    const [mesh, ...rest] = fig.traces;
    // v3 10/10 (Opus 5.5): mesh3d fatto dei soli vertici con un valore; un buco non ha vertice (mai 0, mai riempito)
    assert.equal(mesh.type, 'mesh3d'); assert.equal(mesh.meta, 'surface');
    assert.equal(mesh.z.length, model.filledCells, 'one vertex per valid cell, none for a hole');
    assert.ok(!mesh.z.includes(0) && !mesh.z.includes(null), 'a hole is never a 0 or null vertex');
    assert.ok(!mesh.customdata.some(cd => cd[1] === 1), 'the null column has no vertex at all');
    assert.ok(mesh.z.includes(250), 'outlier geometry is never clipped to the colour cap');
    assert.deepEqual(JSON.parse(JSON.stringify([...new Set(mesh.y)])), [10, 40, 70].map(Math.sqrt), 'expiry axis in sqrt(t)');
    assert.equal(mesh.i.length, 0, 'no quad has its 4 corners here (column 1 is a hole everywhere): no face is drawn across it');
    assert.ok(mesh.text[0].includes(language === 'it' ? 'IV griglia' : 'IV grid'), mesh.text[0]);
    const holes = rest.find(t => t.meta === 'holes');
    assert.equal(holes.marker.symbol, 'x'); assert.equal(holes.x.length, 3, 'every null cell (one per slice) is marked on the floor');
    // le quote misurate vivono sullo smile: sopra la superficie non si disegna nessun marcatore di quota o di cella
    assert.deepEqual(rest.filter(t => ['observed', 'illiquid', 'flagged', 'cells'].includes(t.meta)), [], 'no quote/cell markers in 3D');
    assert.ok(rest.some(t => t.meta === 'wire' && t.connectgaps === false), 'thin wireframe broken at every hole');
    assert.deepEqual(JSON.parse(JSON.stringify(mesh.customdata[0])), ['2035-01-10', 0], 'customdata = [expiry, column index]');
    // ALTA-3: titolo nativo dell'asse y vuoto (l'etichetta e' l'HTML sotto il grafico, una volta sola)
    assert.equal(fig.layout.scene.yaxis.title.text, '');
    assert.deepEqual(fig.layout.scene.yaxis.ticktext, language === 'it' ? ['10g', '40g', '70g'] : ['10d', '40d', '70d'], 'day labels on the sqrt(t) axis');
    assert.equal(fig.layout.separators, language === 'it' ? ',.' : '.,', 'Plotly numbers use the language separators');
    assert.ok(mesh.text[0].includes(language === 'it' ? '20,00%' : '20.00%'), mesh.text[0]);
    const column = rest.find(t => t.meta === 'sel-col');
    assert.deepEqual(JSON.parse(JSON.stringify(column.z)), [null, null, null], 'the K/S line never falls back to atm_iv when the grid cell is missing');
    assert.deepEqual(rest.find(t => t.meta === 'sel-point').x, [], 'no selected-point marker on a hole');
    assert.equal(fig.layout.scene.uirevision, SCENE_REVISION); assert.deepEqual(fig.layout.scene.camera, DEFAULT_CAMERA);
    assert.equal(fig.layout.scene.dragmode, 'turntable', 'turntable: the vertical axis never flips');
    assert.equal(fig.config.scrollZoom, false, 'the wheel scrolls the page, never zooms the plot');
    assert.equal(fig.config.responsive, true); assert.equal(fig.config.displayModeBar, false);
    assert.deepEqual(mesh.colorscale, dark ? SCALE_DARK : SCALE_LIGHT);
    assert.equal(mesh.showscale, true); assert.equal(mesh.colorbar.ticksuffix, '%');
    assert.ok(mesh.colorbar.tickfont.size >= 11);
  }
  const numeric = f => JSON.stringify(f.traces.map(t => [t.x, t.y, t.z, t.i, t.j, t.k]));
  assert.ok(figures.every(f => numeric(f.fig) === numeric(figures[0].fig)), 'language and theme cannot alter any numeric value');
  // asse strike: x = K/S × spot, stesse z
  const strike = surfaceFigure(model, { axis: 'strike', expiry: null, column: null, observed: {}, palette: palette(false), labels: { na: 'n/a' } });
  assert.deepEqual(JSON.parse(JSON.stringify([...new Set(strike.traces[0].x)])), [90, 110]);
});

test('v3 colour scale: dark blue for low IV, red for high IV, continuous, the same in both themes, readable ink on every stop', () => {
  const { SCALE_VOL, SCALE_LIGHT, SCALE_DARK, scaleColor, inkOn } = load('pages/voldeck/Surface3D.tsx');
  assert.equal(SCALE_LIGHT, SCALE_VOL); assert.equal(SCALE_DARK, SCALE_VOL);
  assert.deepEqual(SCALE_VOL.map(s => s[0]), [...SCALE_VOL.map(s => s[0])].sort((a, b) => a - b), 'stops in order');
  assert.equal(SCALE_VOL[0][0], 0); assert.equal(SCALE_VOL[SCALE_VOL.length - 1][0], 1);
  const [r0, g0, b0] = scaleColor(0), [r1, g1, b1] = scaleColor(1);
  assert.ok(b0 > 90 && b0 > r0 * 3 && b0 > g0 * 2, 'low end is dark blue: ' + [r0, g0, b0]);
  assert.ok(r1 > 200 && g1 < 80 && b1 < 80, 'high end is red: ' + [r1, g1, b1]);
  const mid = scaleColor(0.5); assert.ok(mid[1] > mid[0] && mid[1] > mid[2], 'the middle is green: ' + mid);
  // ogni tacca della scala porta un testo con contrasto WCAG >= 4,5
  const lum = c => { const l = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * l(c[0]) + 0.7152 * l(c[1]) + 0.0722 * l(c[2]); };
  for (let t = 0; t <= 1.0001; t += 0.05) {
    const bg = scaleColor(t), ink = inkOn(bg), Lb = lum(bg), Li = ink === '#ffffff' ? 1 : lum([10, 10, 11]);
    const ratio = (Math.max(Lb, Li) + 0.05) / (Math.min(Lb, Li) + 0.05);
    assert.ok(ratio >= 4.5, `t=${t.toFixed(2)} ${bg} ${ink} contrast ${ratio.toFixed(2)}`);
  }
});

test('v3 Laboratory keeps every status warning visible (stale download, catalogue, gaps): only the neutral surface-coverage summary is left out', () => {
  const { CoverageNoticeLines } = load('components/VolWorkbench.tsx');
  languages.impostaLinguaCorrente('it');
  const download = { download_complete: true, state: 'complete', completed_expiries: 7, expirations: Array(7).fill('2035-01-10'), cache_ttl_seconds: 900, error: null };
  const coverage = { loaded: ['a'], requested: ['a', 'b'], excluded: [], errors: [], complete: false,
    rows: [{ expiry: '2035-01-10', status: 'loaded' }, { expiry: '2035-02-10', status: 'partial', reason: 'chain troncata' }] };
  const props = { catalogError: 'catalogo non raggiungibile', download, downloadStale: true, downloadLabel: 'Download completo', downloadError: '', coverage };
  const lab = renderToStaticMarkup(React.createElement(CoverageNoticeLines, { ...props, mode: 'laboratory' }));
  assert.ok(lab.includes('data-vol-download-warning') && lab.includes('15'), 'stale download declared in Laboratory: ' + lab);
  assert.ok(lab.includes('catalogo non raggiungibile'), 'catalogue error declared in Laboratory');
  assert.ok(lab.includes('2035-02-10') && lab.includes('chain troncata'), 'coverage gap declared in Laboratory');
  assert.ok(!lab.includes('data-vol-coverage-summary'), 'only the neutral surface summary is omitted in Laboratory');
  const tools = renderToStaticMarkup(React.createElement(CoverageNoticeLines, { ...props, mode: 'tools' }));
  assert.ok(tools.includes('data-vol-coverage-summary') && tools.includes('data-vol-download-warning'));
  // e nessun foglio di stile della pagina nasconde l'avviso o i suoi avvisi ambra
  for (const css of ['pages/voldeck-nuova.css', 'pages/vol-atlas.css', 'components/vol-workbench.css']) {
    const text = fs.readFileSync(path.join(SRC, css), 'utf8');
    const rules = text.replace(/\/\*[\s\S]*?\*\//g, '').split('}');
    for (const rule of rules) {
      const [sel, body = ''] = rule.split('{');
      if (/data-vol-coverage-notice|vd-amber|data-vol-download-warning/.test(sel || '')) {
        assert.ok(!/display\s*:\s*none|visibility\s*:\s*hidden/.test(body), `${css}: «${sel.trim()}» hides a status warning`);
      }
    }
  }
});

test('v3 the surface source line is always shown and never empty: declared source, or «not declared»', () => {
  const { SurfaceSourceLine } = withInternals('pages/VolSurfacePage.tsx', ['SurfaceSourceLine']);
  for (const language of ['it', 'en']) {
    languages.impostaLinguaCorrente(language);
    const given = renderToStaticMarkup(React.createElement(SurfaceSourceLine, { data: { _source: 'fixture sintetica' } }));
    assert.ok(given.includes('[src: fixture sintetica]') && given.includes(language === 'it' ? 'interpolata fra strike osservati' : 'interpolated between observed strikes'), given);
    const missing = renderToStaticMarkup(React.createElement(SurfaceSourceLine, { data: {} }));
    assert.ok(missing.includes(language === 'it' ? 'fonte non dichiarata' : 'source not declared'), missing);
    assert.ok(/data-vol-surface-src="?"?[^>]*>[^<]{20,}</.test(missing), 'the line is never empty');
  }
});

test('v2 every cell with a value is readable and clickable in 3D: face vertex or marker (one expiry, hole column, widening wings)', () => {
  const { surfaceFigure, SCALE_VOL } = load('pages/voldeck/Surface3D.tsx');
  const pal = { text: '#000', muted: '#555', line: '#ddd', accent: '#00f', violet: '#70f', warn: '#850', card: '#fff', scale: SCALE_VOL };
  const cases = {
    'one expiry': { spot_est: 100, moneyness_grid: [.9, 1, 1.1], slices: [{ expiry: '2035-02-10', days: 40, iv_grid: [.25, .22, .24] }] },
    'hole column': { spot_est: 100, moneyness_grid: [.9, 1, 1.1], slices: ['2035-01-10', '2035-02-10', '2035-03-10'].map((e, i) => ({ expiry: e, days: 10 + 30 * i, iv_grid: [.2, null, .3] })) },
    'widening wings': { spot_est: 100, moneyness_grid: [.8, .9, 1, 1.1, 1.2], slices: [
      { expiry: '2035-01-10', days: 10, iv_grid: [null, .22, .2, .21, null] }, { expiry: '2035-02-10', days: 40, iv_grid: [.26, .23, .21, .22, .25] }] },
  };
  const expectMarkers = { 'one expiry': 3, 'hole column': 6, 'widening wings': 2 };
  for (const [name, raw] of Object.entries(cases)) {
    const m = numbers.surfaceModel(raw);
    const f = surfaceFigure(m, { axis: 'moneyness', expiry: null, column: null, palette: pal, labels: { na: 'n/a' } });
    const mesh = f.traces[0], inFace = new Set([...mesh.i, ...mesh.j, ...mesh.k]);
    const marker = f.traces.find(t => t.meta === 'orphans');
    const marked = new Set((marker?.customdata || []).map(c => c.join('|')));
    const readable = mesh.customdata.filter((cd, n) => inFace.has(n) || marked.has(cd.join('|')));
    assert.equal(readable.length, m.filledCells, `${name}: ${readable.length}/${m.filledCells} cells readable`);
    assert.equal(marked.size, expectMarkers[name], `${name}: markers on the cells that are not a face corner`);
    assert.equal(marker.hoverinfo, 'text'); assert.equal(marker.text.length, marked.size, 'every marker has its hover text');
  }
});

test('v2 colour extremes: mesh and markers use colorRange (p1/p99), not the data max; selection traces live on the sqrt(t) axis', () => {
  const { surfaceFigure, SCALE_VOL, colorRange } = load('pages/voldeck/Surface3D.tsx');
  const pal = { text: '#000', muted: '#555', line: '#ddd', accent: '#00f', violet: '#70f', warn: '#850', card: '#fff', scale: SCALE_VOL };
  // 10 celle: p99 = penultimo valore, diverso dal massimo (outlier 0,9)
  const m = numbers.surfaceModel({ spot_est: 100, moneyness_grid: [.9, .95, 1, 1.05, 1.1], slices: [
    { expiry: '2035-01-10', days: 10, iv_grid: [.3, .27, .25, .26, .9] }, { expiry: '2035-02-10', days: 40, iv_grid: [null, .26, .24, .25, .28] },
    { expiry: '2035-03-10', days: 90, iv_grid: [.29, .26, null, .25, .27] }] });
  const cr = colorRange(m);
  assert.ok(cr.cmax < 90 && cr.cmax > 25, 'p99 differs from the max here: ' + cr.cmax);
  const f = surfaceFigure(m, { axis: 'moneyness', expiry: '2035-02-10', column: 3, palette: pal, labels: { na: 'n/a' } });
  const mesh = f.traces[0];
  assert.equal(mesh.cmin, cr.cmin); assert.equal(mesh.cmax, cr.cmax);
  const marker = f.traces.find(t => t.meta === 'orphans');
  assert.ok(marker, 'cells that are not face corners get markers here');
  assert.equal(marker.marker.cmin, cr.cmin); assert.equal(marker.marker.cmax, cr.cmax);
  const se = f.traces.find(t => t.meta === 'sel-expiry'), sc = f.traces.find(t => t.meta === 'sel-col'), sp = f.traces.find(t => t.meta === 'sel-point');
  assert.ok(se.y.every(y => Math.abs(y - Math.sqrt(40)) < 1e-12), 'selected expiry line at sqrt(40)');
  assert.deepEqual(JSON.parse(JSON.stringify(sc.y)), [10, 40, 90].map(Math.sqrt), 'selected K/S line on sqrt(days)');
  assert.deepEqual(JSON.parse(JSON.stringify(sp.y)), [Math.sqrt(40)]);
  assert.deepEqual(JSON.parse(JSON.stringify([...new Set(mesh.y)])), [10, 40, 90].map(Math.sqrt));
});

test('v2 wireframe never jumps a hole: every drawn segment joins two adjacent grid cells', () => {
  const { surfaceFigure, SCALE_VOL } = load('pages/voldeck/Surface3D.tsx');
  const pal = { text: '#000', muted: '#555', line: '#ddd', accent: '#00f', violet: '#70f', warn: '#850', card: '#fff', scale: SCALE_VOL };
  const grid = [.9, .95, 1, 1.05, 1.1], days = [10, 40, 90];
  const m = numbers.surfaceModel({ spot_est: 100, moneyness_grid: grid, slices: [
    { expiry: '2035-01-10', days: 10, iv_grid: [.3, null, .25, .26, .28] }, { expiry: '2035-02-10', days: 40, iv_grid: [.29, .26, .24, null, .27] },
    { expiry: '2035-03-10', days: 90, iv_grid: [null, .26, .23, .25, .27] }] });
  const f = surfaceFigure(m, { axis: 'moneyness', expiry: null, column: null, palette: pal, labels: { na: 'n/a' } });
  const w = f.traces.find(t => t.meta === 'wire');
  const cellOf = (x, y) => [days.findIndex(d => Math.abs(Math.sqrt(d) - y) < 1e-9), grid.findIndex(g => Math.abs(g - x) < 1e-9)];
  let segs = 0;
  for (let n = 1; n < w.x.length; n++) {
    if (w.x[n] == null || w.x[n - 1] == null) continue;
    const [j0, i0] = cellOf(w.x[n - 1], w.y[n - 1]), [j1, i1] = cellOf(w.x[n], w.y[n]);
    assert.ok(j0 >= 0 && i0 >= 0 && j1 >= 0 && i1 >= 0, 'wire points are grid cells');
    assert.equal(Math.abs(j0 - j1) + Math.abs(i0 - i1), 1, `segment ${j0},${i0} -> ${j1},${i1} joins adjacent cells only`);
    assert.ok(m.rows[j0].iv[i0] != null && m.rows[j1].iv[i1] != null, 'both ends have a value');
    segs++;
  }
  // righe: 2 + 2 + 3 tratti; colonne: 1 + 1 + 2 + 0 + 2 tratti
  assert.equal(segs, 13, 'every adjacent pair with values is joined, nothing more');
});

test('v3 holes are never filled: faces only on quads with 4 measured corners, isolated cells still drawn', () => {
  const { surfaceFigure, SCALE_VOL } = load('pages/voldeck/Surface3D.tsx');
  const pal = { text: '#000', muted: '#555', line: '#ddd', accent: '#00f', violet: '#70f', warn: '#850', card: '#fff', scale: SCALE_VOL };
  const model = numbers.surfaceModel({ spot_est: 100, moneyness_grid: [.9, .95, 1, 1.05],
    slices: [{ expiry: '2035-01-10', days: 10, iv_grid: [null, .21, .2, .22] }, { expiry: '2035-02-10', days: 40, iv_grid: [.24, .22, .21, .23] },
      { expiry: '2035-03-10', days: 70, iv_grid: [.25, .23, .22, .24] }] });
  const fig = surfaceFigure(model, { axis: 'moneyness', expiry: null, column: null, palette: pal, labels: { na: 'n/a' } });
  const mesh = fig.traces[0];
  assert.equal(mesh.i.length, 10, '6 quads, the one touching the hole is dropped: 5 quads = 10 triangles');
  const corner = mesh.customdata.map(cd => cd.join('|'));
  for (let f = 0; f < mesh.i.length; f++) for (const v of [mesh.i[f], mesh.j[f], mesh.k[f]]) {
    assert.ok(Number.isInteger(v) && v >= 0 && v < mesh.z.length, 'every face points at a measured vertex');
    assert.notEqual(corner[v], '2035-01-10|0');
  }
  assert.ok(!fig.traces.some(t => t.meta === 'orphans'), 'no marker when every valid cell is a face corner');
  // il buco in OGNUNO dei 4 angoli di un solo quadrilatero: nessuna faccia (mai un triangolo coi 3 vertici rimasti)
  for (const [j, i] of [[0, 0], [0, 1], [1, 0], [1, 1]]) {
    const g = [[.2, .21], [.22, .23]]; g[j][i] = null;
    const one = numbers.surfaceModel({ spot_est: 100, moneyness_grid: [.95, 1.05],
      slices: [{ expiry: '2035-01-10', days: 10, iv_grid: g[0] }, { expiry: '2035-02-10', days: 40, iv_grid: g[1] }] });
    const f1 = surfaceFigure(one, { axis: 'moneyness', expiry: null, column: null, palette: pal, labels: { na: 'n/a' } });
    assert.equal(f1.traces[0].i.length, 0, `hole at corner ${j},${i}: no face`);
    assert.equal(f1.traces[0].z.length, 3);
  }
  // v2: ogni cella con un valore che non e' vertice di una faccia ha il marcatore (anche se il wireframe la tocca)
  const lonely = numbers.surfaceModel({ spot_est: 100, moneyness_grid: [.9, 1, 1.1],
    slices: [{ expiry: '2035-01-10', days: 10, iv_grid: [.3, null, .25] }, { expiry: '2035-02-10', days: 40, iv_grid: [null, null, .26] }] });
  const f2 = surfaceFigure(lonely, { axis: 'moneyness', expiry: '2035-01-10', column: 0, palette: pal, labels: { na: 'n/a' } });
  const orph = f2.traces.find(t => t.meta === 'orphans');
  assert.deepEqual(JSON.parse(JSON.stringify(orph.customdata)), [['2035-01-10', 0], ['2035-01-10', 2], ['2035-02-10', 2]]);
  assert.equal(f2.traces[0].i.length, 0);
  assert.deepEqual(f2.traces.find(t => t.meta === 'sel-point').z, [30], 'the selected valid cell is marked');
  assert.deepEqual(f2.traces.slice(-3).map(t => t.meta), ['sel-expiry', 'sel-col', 'sel-point'], 'selection traces last (restyle indices)');
  assert.deepEqual(f2.selIndex, [f2.traces.length - 3, f2.traces.length - 2, f2.traces.length - 1]);
});


test('same received payload changes authored notes in RAM without a request or numeric mutation', () => {
  const { localizePayload } = load('lib/api-presentation.ts');
  const raw = fixture(); raw.smoothing = 'Metodo dichiarato'; raw.source_text = 'Originale provider';
  raw._presentation_v1 = { version: 1, texts: [{ path: ['smoothing'], it: 'Metodo dichiarato', en: 'Declared method' }] };
  const before = JSON.stringify(raw), previousFetch = global.fetch; let requests = 0;
  global.fetch = () => { requests++; throw new Error('language change must not request data'); };
  try {
    for (const language of ['it', 'en', 'it']) {
      const view = localizePayload(raw, language);
      assert.equal(view.smoothing, language === 'it' ? 'Metodo dichiarato' : 'Declared method');
      assert.equal(view.slices, raw.slices); assert.equal(view.coverage, raw.coverage);
      assert.equal(view.source_text, 'Originale provider');
    }
  } finally { global.fetch = previousFetch; }
  assert.equal(requests, 0); assert.equal(JSON.stringify(raw), before);
});

test('draft provenance switches language without changing numeric inputs or original provider text', () => {
  languages.impostaLinguaCorrente('it');
  const manual = numbers.blankLeg();
  const observed = numbers.contractLeg({ type:'call', strike:100, expiry:'2035-01-10', iv:.2,
    bid:1.25, ask:1.5, multiplier:100, _source:'Originale provider', quote_timeframe:'DELAYED' }, 'buy');
  const before = JSON.stringify([manual, observed]);
  languages.impostaLinguaCorrente('en');
  assert.ok(numbers.legSource(manual).startsWith('Manual assumption'));
  assert.ok(numbers.legSource(observed).startsWith('Observed Ask'));
  assert.ok(numbers.legSource(observed).includes('Originale provider'));
  assert.ok(numbers.legSource(observed).includes('DELAYED'));
  languages.impostaLinguaCorrente('it');
  assert.equal(numbers.legSource(manual), manual.source);
  assert.equal(numbers.legSource(observed), observed.source);
  assert.equal(JSON.stringify([manual, observed]), before);
});


test('surface builder KO is reported and cannot replace the current rendered snapshot', async () => {
  const filename = path.resolve(__dirname, '../../src/components/VolWorkbench.tsx');
  const source = ts.createSourceFile(filename, fs.readFileSync(filename, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let fn;
  const visit = node => {
    if (ts.isFunctionDeclaration(node) && node.name?.text === 'showSurface') fn = node.getText(source);
    ts.forEachChild(node, visit);
  };
  visit(source); assert.ok(fn);
  const busy = [], errors = [], painted = [], calls = [];
  const scope = { exports:{}, AbortController, surfaceRequest:{current:null}, downloadRef:{current:{id:'synthetic'}},
    setSliceBusy:value => busy.push(value), setDownloadError:value => errors.push(value),
    volRequest:async (...args) => { calls.push(args); return { error:'Synthetic IV coverage missing', coverage:{complete:false} }; },
    onSurface:(...args) => painted.push(args), Error, String };
  vm.runInNewContext(ts.transpileModule(fn + '\nexports.run = showSurface;', {
    compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022},
  }).outputText, scope);
  await scope.exports.run({id:'synthetic',expirations:['2035-01-10']});
  assert.deepEqual(busy, [true,false]);
  assert.deepEqual(errors, ['', 'Synthetic IV coverage missing']);
  assert.equal(painted.length, 0); assert.equal(calls.length, 1);
  assert.equal(calls[0][0], '/options/download/synthetic/surface');
});

test('workspaces that hide acquisition declare its coverage gaps, with the reason, and acquisition does not repeat them', () => {
  const Workbench = load('components/VolWorkbench.tsx').default;
  const coverage = { requested: ['2035-01-10', '2035-02-10'], loaded: ['2035-02-10'], excluded: ['2035-01-10'], errors: [],
    complete: false, download_complete: true,
    rows: [{ expiry: '2035-01-10', days: 0, status: 'excluded', reason: 'Synthetic 0-1 DTE' },
      { expiry: '2035-02-10', days: 31, status: 'loaded', reason: null, n_contracts: 12 }] };
  const props = { ticker: '', coverage, surfaceBusy: false, onSurface() {}, onLaboratory() {}, onAcquisition() {} };
  const notice = html => {
    const at = html.indexOf('data-vol-coverage-notice');
    return at < 0 ? null : html.slice(html.lastIndexOf('<section', at), html.indexOf('</section>', at));
  };
  for (const language of ['it', 'en']) {
    languages.impostaLinguaCorrente(language);
    for (const other of ['chain', 'laboratory']) {
      assert.ok(notice(renderToStaticMarkup(React.createElement(Workbench, { ...props, mode: other }))), other + ' must declare the gaps too');
    }
    const tools = notice(renderToStaticMarkup(React.createElement(Workbench, { ...props, mode: 'tools' })));
    assert.ok(tools, 'Strumenti must declare the gaps held by the hidden Acquisizione section');
    // the reason is text on screen, not only the title attribute (which a tooltip-only cure would still carry)
    assert.ok(tools.includes('2035-01-10 · ' + (language === 'it' ? 'esclusa' : 'excluded') + ' — Synthetic 0-1 DTE'),
      'the backend reason is readable, not only a tooltip: ' + tools);
    assert.doesNotMatch(tools.slice(0, tools.indexOf('>')), /\shidden/);
    assert.ok(tools.includes('1/2'), tools);
    assert.ok(tools.includes('2035-01-10 · ' + (language === 'it' ? 'esclusa' : 'excluded')), tools);
    assert.ok(!tools.includes('2035-02-10 · '), 'loaded expiries are not listed as gaps');
    assert.ok(tools.includes(language === 'it' ? 'Copertura e fonti' : 'Coverage and sources'), tools);
    assert.ok(!tools.includes('⟦'), 'no missing translation marker');
    assert.equal(notice(renderToStaticMarkup(React.createElement(Workbench, { ...props, mode: 'acquisition' }))), null);
    const complete = { ...coverage, excluded: [], loaded: coverage.requested, complete: true,
      rows: coverage.rows.map(row => ({ ...row, status: 'loaded', reason: null })) };
    assert.equal(notice(renderToStaticMarkup(React.createElement(Workbench, { ...props, mode: 'tools', coverage: complete }))), null);
  }
});

// 13/09 (Claude Opus 5): the cone panel named /options/vol_cone as «reported by the backend» while
// the context request was still running or had failed, and it attributed client-side failures
// (network, unreadable body, locally worded HTTP status) to the backend.
const conePanel = () => {
  const filename = path.resolve(__dirname, '../../src/pages/VolSurfacePage.tsx');
  const source = ts.createSourceFile(filename, fs.readFileSync(filename, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let cone, panel;
  const visit = node => {
    if (ts.isFunctionDeclaration(node) && node.name?.text === 'VolCone') cone = node.getText(source);
    if (ts.isJsxExpression(node) && node.expression && ts.isConditionalExpression(node.expression)
      && /<VolCone\b/.test(node.expression.getText(source))) panel = node.expression.getText(source);
    ts.forEachChild(node, visit);
  };
  visit(source); assert.ok(cone && panel, 'VolCone and its panel come from the real page');
  const scope = { exports: {}, React, useState: React.useState, useRef: React.useRef, tr: translate.t, t: translate.t,
    finite: numbers.finite, numText: numbers.numText, nyTime: numbers.nyTime };
  vm.runInNewContext(ts.transpileModule(cone + '\nexports.panel = (contextState, cone) => (' + panel + ');', {
    fileName: 'vol-cone-panel.tsx',
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React },
  }).outputText, scope);
  return (state, value) => renderToStaticMarkup(scope.exports.panel(state, value));
};

test('an unanswered context is named as its state, never as an empty /options/vol_cone payload', () => {
  const render = conePanel();
  const band = (days, current) => ({ window: days, current, min: current - .05, p25: current - .02, p50: current, p75: current + .02, max: current + .05, n_obs: 250 });
  const previous = { realized: { windows: [band(5, .3), band(21, .28), band(63, .25)] }, implied: {}, confronto: [] };
  // Frozen oracle: the words the PM reads, not keys taken from the code under test.
  const words = {
    it: { loading: 'In caricamento', error: 'Richiesta fallita', idle: 'Non richiesto', backend: 'DICHIARATO DAL BACKEND:',
      client: 'RICHIESTA DEL CONO NON RIUSCITA:', coneLoading: 'interrogo /options/vol_cone' },
    en: { loading: 'Loading', error: 'Request failed', idle: 'Not requested', backend: 'REPORTED BY THE BACKEND:',
      client: 'VOLATILITY CONE REQUEST FAILED:', coneLoading: 'Loading volatility cone' },
  };
  for (const language of ['it', 'en']) {
    languages.impostaLinguaCorrente(language);
    const w = words[language];
    for (const state of ['loading', 'error']) {
      const html = render(state, null), label = `${language}:${state}:no cone`;
      assert.ok(html.includes(w[state]), label + ' names the context state: ' + html);
      assert.ok(!html.includes(w.backend), label + ' never attributes an uncalled /options/vol_cone to the backend: ' + html);
      assert.ok(!html.includes(w.coneLoading), label + ' does not pretend the cone request is running: ' + html);
      assert.ok(!html.includes('⟦'), label + ' has no missing translation marker');
      // A KO or a new request never removes the cone already on screen with its own surface.
      assert.ok(render(state, previous).includes('vsxsvg'), `${language}:${state}: the previous cone stays drawn`);
    }
    assert.ok(render('not_requested', null).includes(w.idle));
    assert.ok(render('loaded', { __loading: true }).includes(w.coneLoading));
    const backend = render('loaded', { error: 'Synthetic cone KO' });
    assert.ok(backend.includes(w.backend) && backend.includes('Synthetic cone KO'), 'a backend cone error stays attributed verbatim');
    const client = render('loaded', { error: 'Failed to fetch', origin: 'client' });
    assert.ok(client.includes(w.client) && client.includes('Failed to fetch') && !client.includes(w.backend),
      'a client-side failure is not presented as reported by the backend: ' + client);
    assert.ok(render('loaded', previous).includes('vsxsvg'), 'a loaded cone is still drawn');
  }
});

test('volRequest marks who wrote the error: only a backend detail is backend', async () => {
  languages.impostaLinguaCorrente('en');
  const saved = globalThis.fetch;
  const reply = (status, body) => async () => ({ ok: status < 400, status, json: async () => { if (body instanceof Error) throw body; return body; } });
  const cases = [
    ['network failure', async () => { throw new TypeError('Failed to fetch'); }, 'client', 'Failed to fetch'],
    ['backend detail', reply(500, { detail: 'Synthetic backend detail' }), 'backend', 'Synthetic backend detail'],
    ['status without detail', reply(503, null), 'client', 'Request failed (HTTP 503)'],
    ['session refused', reply(401, { detail: 'Synthetic token detail' }), 'client', null],
    ['unreadable body', reply(200, new SyntaxError('Unexpected token')), 'client', 'Empty or unreadable response'],
  ];
  try {
    for (const [label, fake, origin, message] of cases) {
      globalThis.fetch = fake;
      const failure = await numbers.volRequest('/options/vol_cone/SYNTH').then(() => null, error => error);
      assert.ok(failure, label + ' must reject');
      assert.equal(failure.origin, origin, label);
      if (message) assert.equal(failure.message, message, label);
    }
    globalThis.fetch = reply(200, { realized: {} });
    assert.deepEqual(JSON.parse(JSON.stringify(await numbers.volRequest('/options/vol_cone/SYNTH'))), { realized: {} });
  } finally { globalThis.fetch = saved; }
});

test('a failed context request never calls /options/vol_cone; a cone failure keeps its origin', async () => {
  const filename = path.resolve(__dirname, '../../src/pages/VolSurfacePage.tsx');
  const source = ts.createSourceFile(filename, fs.readFileSync(filename, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let fn;
  const visit = node => {
    if (ts.isVariableDeclaration(node) && node.name.getText(source) === 'loadSurface') fn = node.initializer.getText(source);
    ts.forEachChild(node, visit);
  };
  visit(source); assert.ok(fn);
  for (const scenario of ['surface KO', 'cone client KO', 'cone backend KO']) {
    const calls = [], states = [], cones = [];
    const scope = { exports: {}, AbortController, Error, String, encodeURIComponent, ticker: 'SYNTH',
      requestRef: { current: null }, lastExpiries: { current: [] },
      setLoading: () => {}, setError: () => {}, setData: () => {},
      setContextState: value => states.push(value), setCone: value => cones.push(value),
      volRequest: async p => {
        calls.push(p);
        if (scenario === 'surface KO') throw new Error('Synthetic surface KO');
        if (p.startsWith('/options/vol_cone/')) {
          throw scenario === 'cone backend KO' ? Object.assign(new Error('Synthetic cone KO'), { origin: 'backend' }) : new Error('Synthetic cone KO');
        }
        return { slices: [] };
      } };
    vm.runInNewContext(ts.transpileModule('exports.run = ' + fn, {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText, scope);
    await scope.exports.run(['2035-01-10'], true);
    assert.equal(calls[0], '/options/vol_surface/SYNTH?expiries=2035-01-10&include_context=true');
    if (scenario === 'surface KO') {
      assert.equal(calls.length, 1, 'context failure: /options/vol_cone is never requested');
      assert.deepEqual(states, ['loading', 'error']); assert.equal(cones.length, 0);
    } else {
      assert.deepEqual(calls.slice(1), ['/options/vol_cone/SYNTH']);
      assert.deepEqual(states, ['loading', 'loaded']);
      assert.deepEqual(JSON.parse(JSON.stringify(cones)), [{ __loading: true },
        { error: 'Synthetic cone KO', origin: scenario === 'cone backend KO' ? 'backend' : 'client' }]);
    }
  }
});

// 13/09 (Claude Opus 5, c5 g1-voldeck): words the Vol Deck wrote in one language only — the «g» of
// days, «Earnings», the «Expiry» header, «Calendar spread», «Shock IV», «P&L scenario» and the
// labels of two numeric error messages. Frozen oracle: the phrases the PM reads are written here,
// never read from the catalogues under test. Components and fragments come from the real files.
const { SRC } = require('../i18n/_carica.cjs');
const withInternals = (relative, names) => {
  const filename = path.join(SRC, relative);
  const code = fs.readFileSync(filename, 'utf8') + `\nexport const __internals = { ${names.join(', ')} };\n`;
  const js = ts.transpileModule(code, { fileName: filename, compilerOptions: { module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const resolveFile = base => ['', '.ts', '.tsx', '/index.ts', '/index.tsx'].map(ext => base + ext)
    .find(file => fs.existsSync(file) && fs.statSync(file).isFile());
  const req = spec => {
    if (/\.css$/.test(spec)) return {};
    if (!spec.startsWith('@/') && !spec.startsWith('.')) return require(spec);
    const file = resolveFile(spec.startsWith('@/') ? path.join(SRC, spec.slice(2)) : path.resolve(path.dirname(filename), spec));
    assert.ok(file, 'unresolved import ' + spec);
    return load(file); // same cache as the language module the test switches
  };
  const holder = { exports: {} };
  new Function('exports', 'require', 'module', js)(holder.exports, req, holder);
  return holder.exports.__internals;
};
const fragment = (relative, tag, opening, anchor) => {
  const filename = path.join(SRC, relative);
  const source = ts.createSourceFile(filename, fs.readFileSync(filename, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const found = [];
  const visit = node => {
    const open = ts.isJsxElement(node) ? node.openingElement : ts.isJsxSelfClosingElement(node) ? node : null;
    if (open && open.tagName.getText(source) === tag && open.getText(source).includes(opening)
      && node.getText(source).includes(anchor)) found.push(node.getText(source));
    ts.forEachChild(node, visit);
  };
  visit(source);
  assert.equal(found.length, 1, `${relative}: <${tag}> with ${anchor} must be unique`);
  return scope => {
    const sandbox = { exports: {}, React, ...scope };
    vm.runInNewContext(ts.transpileModule('exports.node = (' + found[0] + ');', { fileName: 'fragment.tsx',
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React } }).outputText, sandbox);
    return renderToStaticMarkup(sandbox.exports.node);
  };
};
// React 18 SSR warns about the layout effect of useScalaTesto and about the pre-existing SVG
// <title>{expiry} · {days}{unit}</title> of SmileXray (several text nodes); only those two are muted.
const SSR_ONLY_WARNINGS = ['useLayoutEffect does nothing on the server', 'A title element received an array with more than 1 element'];
const quietLayoutEffect = render => {
  const previous = console.error;
  console.error = (...args) => { if (!SSR_ONLY_WARNINGS.some(text => String(args[0]).includes(text))) previous(...args); };
  try { return render(); } finally { console.error = previous; }
};

test('day units on the numeric grid, forward ladder and cone follow the language', () => {
  // v3 10/10 (Opus 5.5): la vista dall'alto e' diventata la griglia numerica IvGrid
  const { FwdVolLadder } = withInternals('pages/VolSurfacePage.tsx', ['FwdVolLadder']);
  const IvGrid = load('pages/voldeck/IvGrid.tsx').default;
  const grid = [.9, 1, 1.1];
  const slices = [{ expiry: '2035-02-10', days: 34, iv_grid: [.25, .22, .24] }, { expiry: '2035-03-10', days: 62, iv_grid: [.26, .23, .25] }];
  const term = slices.map(s => ({ expiry: s.expiry, days: s.days, atm_iv: s.iv_grid[1] }));
  const band = (days, current, young) => ({ window: days, current, min: current - .05, p25: current - .02, p50: current,
    p75: current + .02, max: current + .05, n_obs: young ? 40 : 250, young });
  const cone = { realized: { windows: [band(5, .3), band(21, .28), band(63, .25, true)] }, implied: {},
    confronto: [{ expiry: '2035-02-10', days: 34, window: 21, atm_iv: .251, pct_realized_leq_iv: 62 }] };
  const words = {
    it: { heat: '<b>10/02/35</b><span>34g</span>', ladder: '34g→62g', tick: '>21g</text>', young: '>63g*</text>',
      reading: 'pct realized 21g' },
    en: { heat: '<b>10/02/35</b><span>34d</span>', ladder: '34d→62d', tick: '>21d</text>', young: '>63d*</text>',
      reading: 'realised percentile 21d' },
  };
  const render = conePanel();
  for (const language of ['it', 'en']) {
    languages.impostaLinguaCorrente(language);
    const w = words[language];
    const heatModel = numbers.surfaceModel({ spot_est: 100, moneyness_grid: grid, slices });
    const heat = renderToStaticMarkup(React.createElement(IvGrid, { model: heatModel, axis: 'moneyness', expiry: null, column: null, onPick() {}, onExpiry() {}, onColumn() {} }));
    assert.ok(heat.includes(w.heat), `${language}: heat row ${w.heat}: ${heat}`);
    const ladder = renderToStaticMarkup(React.createElement(FwdVolLadder, { term }));
    assert.ok(ladder.includes(w.ladder), `${language}: forward ladder ${w.ladder}: ${ladder}`);
    const drawn = render('loaded', cone);
    for (const key of ['tick', 'young', 'reading']) assert.ok(drawn.includes(w[key]), `${language}: cone ${key} ${w[key]}`);
    for (const html of [heat, ladder, drawn]) assert.ok(!html.includes('⟦'), 'no missing translation marker');
  }
});

test('earnings, expiry header and E legend of the tools view are written in the chosen language', () => {
  const { Tile } = withInternals('pages/VolSurfacePage.tsx', ['Tile']);
  const page = 'pages/VolSurfacePage.tsx';
  const earnings = fragment(page, 'Tile', '<Tile', 'rawData.next_earnings ||');
  const projector = fragment(page, 'p', 'className="vdn-legend"', 'ui_band_spot_atm_iv_t_solid_1_dashed_2_horizontal_axis_in_76');
  const header = fragment(page, 'thead', '<thead', 'ui_days_97');
  const words = {
    it: { label: '<span>Risultati</span>', legend: 'E = risultati</span>', expiry: '>Scadenza</th>', foreign: ['Earnings', 'earnings', 'Expiry'] },
    en: { label: '<span>Earnings</span>', legend: 'E = earnings</span>', expiry: '>Expiry</th>', foreign: ['Risultati', 'risultati', 'Scadenza'] },
  };
  for (const language of ['it', 'en']) {
    languages.impostaLinguaCorrente(language);
    const w = words[language];
    const stat = earnings({ Tile, t: translate.t, na: 'n/a', rawData: { next_earnings: '2035-02-20' }, contextState: 'loaded' });
    assert.ok(stat.includes(w.label) && stat.includes('2035-02-20'), `${language}: earnings label: ${stat}`);
    const legend = projector({ tr: translate.t });
    assert.ok(legend.includes('vdn-warn-text"> ' + w.legend), `${language}: projector E legend: ${legend}`);
    const head = header({ tr: translate.t });
    assert.ok(head.includes(w.expiry), `${language}: expiry header: ${head}`);
    for (const html of [stat, legend, head]) {
      for (const word of w.foreign) assert.ok(!html.includes(word), `${language}: ${word} leaked into ${html}`);
      assert.ok(!html.includes('⟦'), 'no missing translation marker');
    }
  }
});

test('option builder renders in both languages, declares an n/a leg and starts no request during render', () => {
  // 09/10 (Opus 5.5): StrategyLab replaced by components/option-builder (figures from the backend engine).
  const Workbench = load('components/VolWorkbench.tsx').default;
  const OptionBuilder = load('components/option-builder/OptionBuilder.tsx').default;
  const ob = load('lib/option-builder.ts');
  const previousFetch = global.fetch; let requests = 0;
  global.fetch = () => { requests++; throw new Error('SSR must not request data'); };
  const words = {
    it: { title: 'Costruttore di opzioni', preset: 'Iron condor', calendar: 'Spread calendario', legs: 'Ticket', /* 10/10 impianto Ticket (Opus 5.5) */ nd: 'contratto assente dalla catena caricata',
      foreign: ['Option builder', 'Calendar spread', 'contract not in the loaded chain', 'Design the strategy'] },
    en: { title: 'Option builder', preset: 'Iron condor', calendar: 'Calendar spread', legs: 'Order ticket', nd: 'contract not in the loaded chain',
      foreign: ['Costruttore di opzioni', 'Spread calendario', 'contratto assente', 'Disegna la strategia'] },
  };
  try { for (const language of ['it', 'en']) {
    languages.impostaLinguaCorrente(language);
    const w = words[language];
    const lab = renderToStaticMarkup(React.createElement(Workbench, { ticker: 'SYNTH', mode: 'laboratory', surfaceBusy: false,
      onSurface() {}, onLaboratory() {}, onAcquisition() {} }));
    assert.ok(lab.includes('data-option-builder') && lab.includes(w.title), `${language}: builder mounted in Laboratory`);
    assert.ok(lab.includes(w.preset) && lab.includes(w.calendar), `${language}: strategy library`);
    const legs = [ob.optionLeg('call', 'buy', '2035-01-19', 100)];
    const html = renderToStaticMarkup(React.createElement(OptionBuilder, { ticker: 'SYNTH', download: null, catalog: ['2035-01-19'], downloadBusy: false,
      fetchChain: async () => { throw new Error('no fetch in render'); }, requestExpiries() {}, legs, setLegs() {} }));
    assert.ok(html.includes(w.legs) && html.includes(w.nd), `${language}: the leg without a chain is declared n/a: ${html.slice(0, 200)}`);
    assert.ok(!/<td[^>]*>0,00<\/td>/.test(html), 'an n/a leg never shows a zero price');
    for (const page of [lab, html]) {
      for (const word of w.foreign) assert.ok(!page.includes(word), `${language}: ${word} leaked`);
      assert.ok(!page.includes('⟦'), 'no missing translation marker');
    }
    assert.ok(!html.includes('type="number"'), 'numeric fields are text + inputMode decimal (comma-safe)');
  } } finally { global.fetch = previousFetch; }
  assert.equal(requests, 0);
});

// tools/qa is not part of the public tree (release allowlist): there the QA check is skipped and
// says why, while the handbook check below runs everywhere.
const VOL_BROWSER_QA = path.resolve(__dirname, '../../../tools/qa/vol_browser.py');

test('the Italian browser QA uses the labels the app now renders', {
  skip: fs.existsSync(VOL_BROWSER_QA) ? false : 'tools/qa/vol_browser.py is not in this tree (not published)',
}, () => {
  const qa = fs.readFileSync(VOL_BROWSER_QA, 'utf8');
  assert.ok(qa.includes('"language": "it"'), 'the browser QA runs the app in Italian');
  assert.equal(qa.split("get_by_role('columnheader', name='Scadenza', exact=True)").length - 1, 2, 'term table header in Italian');
  assert.ok(!qa.includes("name='Expiry'"), 'the Italian page has no Expiry header');
});

test('the handbook uses the labels the app now renders', () => {
  const guide = fs.readFileSync(path.resolve(__dirname, '../../../docs/guide/pages/09-vol-deck.md'), 'utf8').replace(/\s+/g, ' ');
  assert.ok(guide.includes('**Calendar spread** (*Spread calendario*)'), 'handbook: calendar template in both languages');
  assert.ok(guide.includes('**IV shock** (*Shock IV*, percentage points)'), 'handbook: IV shock in both languages');
  assert.ok(!guide.includes('**Shock IV**'), 'handbook: English label is IV shock');
});
