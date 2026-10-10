// Vol Deck Nuova (09/10/2026, Opus 5.5): funzioni pure della superficie e delle fette.
// Oracolo scritto qui (numeri e frasi), non letto dal codice sotto test. Regola provata ovunque:
// un dato mancante resta mancante (null / n.d.), mai 0 e mai un'altra misura al suo posto.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser } = require('../i18n/_carica.cjs');
ambienteBrowser();
const load = creaCaricatore({ stub: { './api': { API_BASE: 'http://synthetic.invalid', requestHeaders: () => ({}) } } });
const languages = load('i18n/lingua.ts');
const vd = load('lib/vol-deck.ts');
const plain = value => JSON.parse(JSON.stringify(value));

const surface = () => ({
  spot_est: 200, moneyness_grid: [0.9, 1, 1.1],
  slices: [
    { expiry: '2035-03-01', days: 60, atm_iv: 0.25, iv_grid: [0.3, 0.25, null] },
    { expiry: '2035-01-02', days: 1, atm_iv: 0.5, iv_grid: [0.6, 0.5, 0.55] },
    { expiry: '2035-02-01', days: 30, atm_iv: null, iv_grid: [0.32, 0, 'x'] },
  ],
  coverage: { rows: [{ expiry: '2035-02-01', status: 'partial' }, { expiry: '2035-03-01', status: 'loaded', chain_complete: false },
    { expiry: '2035-04-01', status: 'loaded', chain_complete: true }, { expiry: '2035-05-01', status: 'excluded' }] },
});

test('surface model: holes stay holes, 0 and junk are holes, 0-1 DTE excluded and declared, rows by days', () => {
  const m = vd.surfaceModel(surface(), ['2035-02-01']);
  assert.deepEqual(m.rows.map(r => r.expiry), ['2035-02-01', '2035-03-01']);
  assert.deepEqual(plain(m.excludedShort), ['2035-01-02']);
  assert.deepEqual(plain(m.rows[0].iv), [0.32, null, null], 'a 0 or a string in the grid is a hole, never a value');
  assert.deepEqual(plain(m.rows[1].iv), [0.3, 0.25, null]);
  assert.equal(m.rows[0].atm, null, 'a missing ATM stays null');
  assert.equal(m.rows[0].partial, true); assert.equal(m.rows[1].partial, false);
  assert.deepEqual(plain(m.strikes), [180, 200, 220]);
  assert.equal(m.cells, 6); assert.equal(m.filledCells, 3); assert.equal(m.holes, 3);
  assert.equal(m.ivMin, 0.25); assert.equal(m.ivMax, 0.32);
  const noSpot = vd.surfaceModel({ ...surface(), spot_est: null });
  assert.equal(noSpot.spot, null); assert.deepEqual(plain(noSpot.strikes), [null, null, null], 'no spot, no strike axis: never strike = K/S × 0');
  assert.equal(vd.surfaceModel(null).rows.length, 0);
});

test('partial chains are those the backend declares, absence is not completeness', () => {
  assert.deepEqual(plain(vd.partialExpiries(surface().coverage)), ['2035-02-01', '2035-03-01']);
  assert.deepEqual(plain(vd.partialExpiries(undefined)), []);
});

test('segments break the line at every missing value', () => {
  const pts = [1, null, 2, 3, undefined, NaN, 4].map(v => ({ v }));
  assert.deepEqual(vd.segments(pts, p => p.v).map(s => s.map(p => p.v)), [[1], [2, 3], [4]]);
});

test('term series keeps the builder ATM and the grid column as two separate measures', () => {
  const m = vd.surfaceModel(surface());
  const atmColumn = vd.atmIndex(m.grid);
  assert.equal(atmColumn, 1);
  const term = vd.termSeries(m, atmColumn);
  assert.deepEqual(plain(term.map(p => [p.expiry, p.atm, p.col])), [['2035-02-01', null, null], ['2035-03-01', 0.25, 0.25]],
    'missing ATM is not replaced by the grid, missing grid is not replaced by the ATM');
  assert.deepEqual(plain(vd.termSeries(m, 2).map(p => p.col)), [null, null]);
  assert.deepEqual(plain(vd.termSeries(m, null).map(p => p.col)), [null, null]);
  assert.deepEqual(plain(vd.smileSeries(m, '2035-03-01').map(p => [p.strike, p.iv])), [[180, 0.3], [200, 0.25], [220, null]]);
  assert.deepEqual(plain(vd.smileSeries(m, 'nope').map(p => p.iv)), [null, null, null]);
});

test('measured quotes: OTM convention, illiquid kept and marked, contracts without IV counted', () => {
  const c = (type, strike, iv, oi, volume, bid = 1, ask = 1.1) => ({ type, strike, iv, oi, volume, bid, ask, mid: null, contract: 'O:X', quote_timestamp: null, quality: [] });
  const { quotes, withoutIv } = vd.observedQuotes([c('put', 190, 0.3, 5, 0), c('call', 190, 0.29, 5, 0), c('call', 210, 0.27, 0, 0, null, 0.2),
    c('put', 200, null, 5, 1), c('put', 205, 0, 5, 1), c('call', null, 0.2, 1, 1)], 200);
  assert.equal(withoutIv, 2);
  assert.deepEqual(plain(quotes.map(q => [q.type, q.strike, q.otm, q.liquid])), [['call', 190, false, true], ['put', 190, true, true], ['call', 210, true, false]]);
  assert.equal(quotes[2].bid, null, 'a missing bid stays null');
  assert.equal(quotes[1].m, 0.95);
  assert.deepEqual(plain(vd.observedQuotes([c('put', 190, 0.3, 1, 1)], null)), { quotes: [], withoutIv: 0, adjusted: 0 }, 'no spot: no moneyness is invented');
  const near = vd.nearestQuotes(quotes, 196);
  assert.equal(near.strike, 190); assert.equal(near.put.type, 'put'); assert.equal(near.call.type, 'call');
  assert.deepEqual(plain(vd.nearestQuotes(quotes, null)), { strike: null, put: null, call: null });
  assert.equal(vd.nearestQuotes(quotes, 212).put, null, 'no put listed at 210: n/a, not the neighbour');
});

test('ticks, IV and price texts: missing is the caller text, never 0', () => {
  assert.deepEqual(plain(vd.niceTicks(0.183, 0.262, 4)), [0.2, 0.22, 0.24, 0.26]);
  assert.deepEqual(plain(vd.niceTicks(NaN, 1)), []);
  for (const [language, iv, price] of [['en', '22.50%', '12,345'], ['it', '22,50%', '12.345']]) {
    languages.impostaLinguaCorrente(language);
    assert.equal(vd.ivText(0.225, 'n/a', 2), iv);
    assert.equal(vd.priceText(12345.4, 'n/a'), price);
    for (const missing of [null, undefined, NaN]) {
      assert.equal(vd.ivText(missing, 'n/a'), 'n/a'); assert.equal(vd.priceText(missing, 'n/a'), 'n/a');
    }
  }
});

test('a time without a time zone is shown as received and declared, a zoned one is converted', () => {
  assert.deepEqual(plain(vd.stampText('2026-10-09T15:30:00.123456')), { text: '2026-10-09 15:30:00', zoned: false });
  assert.equal(vd.stampText('2026-10-09T15:30:00Z').zoned, true);
  assert.equal(vd.stampText('2026-10-09T15:30:00+02:00').zoned, true);
  assert.equal(vd.stampText(''), null); assert.equal(vd.stampText(null), null);
});

// v2 10/10: i nomi sono quelli REALI del contratto backend cbcfd0b (la v1 leggeva session_date/plan_delay inventati)
test('freshness reads the real backend names, and only them', () => {
  const empty = vd.surfaceFreshness({ slices: [{ quote_time_min: '2026-10-09T19:30:00Z' }], session_date: '2026-10-09', plan_delay: '15 min', market_session_date: 'x' });
  assert.equal(empty.quoteFrom, null, 'a per-slice time is not promoted to the surface time');
  assert.equal(empty.session, null, 'invented top-level session names are not read'); assert.equal(empty.delay, null);
  assert.equal(empty.spotQualified, false); assert.equal(empty.partial, false);
  const f = vd.surfaceFreshness({ spot_timestamp: '2026-10-09T19:59:00+00:00', spot_qualified: true, spot_fallback: false, spot_proxy: null,
    quote_time_min: '2026-10-09T19:30:00+00:00', quote_time_max: '2026-10-09T19:58:00+00:00', data_delay: 'DELAYED', data_delay_note: 'n',
    market_session: { asof_utc: 'u', asof_new_york: '2026-10-09T15:59:00-04:00', market_open: false, session_date: '2026-10-09', note: 'chiuso' },
    snapshot_kind: 'download_snapshot', snapshot_at: '2026-10-09T20:00:00+00:00', partial: true, coverage: { stopped_after_rate_limit: true } });
  assert.deepEqual(plain(f), { quoteFrom: '2026-10-09T19:30:00+00:00', quoteTo: '2026-10-09T19:58:00+00:00', spotAt: '2026-10-09T19:59:00+00:00', spotKind: null,
    session: '2026-10-09', marketOpen: false, sessionNote: 'chiuso', asofNewYork: '2026-10-09T15:59:00-04:00', delay: 'DELAYED', delayNote: 'n',
    snapshotAt: '2026-10-09T20:00:00+00:00', snapshotKind: 'download_snapshot', partial: true, stoppedAfterRateLimit: true,
    spotQualified: true, spotFallback: false, spotProxy: null });
});

// I due rendering ATM che portavano null a 0 (tabella term/skew; cresta 3D con ripiego su atm_iv) e i
// tre punti B8 dell'audit 09/10 (percentile IV, put OI del GEX): resi sul componente VERO.
const SRC = require('node:path').join(__dirname, '../../src');
const internals = (() => {
  const fs = require('node:fs'), path = require('node:path'), ts = require('typescript');
  const filename = path.join(SRC, 'pages/VolSurfacePage.tsx');
  const code = fs.readFileSync(filename, 'utf8') + '\nexport const __internals = { SkewTable, IvAltimeter, GexProfile };\n';
  const js = ts.transpileModule(code, { fileName: filename, compilerOptions: { module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const resolve = base => ['', '.ts', '.tsx', '/index.ts', '/index.tsx'].map(e => base + e).find(f => fs.existsSync(f) && fs.statSync(f).isFile());
  const req = spec => /\.css$/.test(spec) ? {} : (!spec.startsWith('@/') && !spec.startsWith('.')) ? require(spec)
    : load(resolve(spec.startsWith('@/') ? path.join(SRC, spec.slice(2)) : path.resolve(path.dirname(filename), spec)));
  const holder = { exports: {} };
  new Function('exports', 'require', 'module', js)(holder.exports, req, holder);
  return holder.exports.__internals;
})();

test('term/skew table: a missing ATM IV is n/a with no bar, never «0.0%»', () => {
  languages.impostaLinguaCorrente('en');
  const html = renderToStaticMarkup(React.createElement(internals.SkewTable, { term: [
    { expiry: '2035-01-10', days: 10, atm_iv: null, rr25: null, bf25: null, pc_oi_ratio: null },
    { expiry: '2035-02-10', days: 40, atm_iv: 0.25, rr25: -0.02, bf25: 0.004, pc_oi_ratio: 1.2 }] }));
  assert.ok(!html.includes('0.0%'), html);
  assert.ok(html.includes('25.0%'));
  assert.equal(html.split('n/a').length - 1, 4, 'ATM, RR, BF and P/C of the first row are n/a: ' + html);
  assert.equal(html.split('vdn-hole-chip').length - 1, 1, 'the missing ATM draws a hole, not a zero bar');
});

test('IV rank: a null percentile is declared, not drawn as 0; GEX put OI missing is n/a', () => {
  languages.impostaLinguaCorrente('en');
  const rank = renderToStaticMarkup(React.createElement(internals.IvAltimeter, { ctx: { iv_percentile: null, error: null } }));
  assert.ok(rank.includes('IV percentile n/a'), rank); assert.ok(!rank.includes('>0<'), rank);
  const ok = renderToStaticMarkup(React.createElement(internals.IvAltimeter, { ctx: { iv_percentile: 42, iv_front_current: null, iv_min: 0.18, iv_max: 0.4, n_obs: 90, history_from: '2026-05-01' } }));
  assert.ok(ok.includes('Secondary: front ATM n/a'), 'a missing stored front ATM is n/a and secondary: ' + ok);
  assert.ok(ok.includes('30-day IV n/a'), 'the rank is read next to its 30-day IV, n/a when absent: ' + ok);
  const full = renderToStaticMarkup(React.createElement(internals.IvAltimeter, { ctx: { iv_percentile: 42, iv_30d_current: 0.215, iv_min: 0.18, iv_max: 0.4, n_obs: 20, young: true, history_from: '2026-05-01' } }));
  assert.ok(full.includes('30-day IV 21.5%'), full);
  assert.ok(full.includes('20 · threshold n/a') && !full.includes('/60'), 'a missing young threshold is not replaced by 60: ' + full);
  const gex = renderToStaticMarkup(React.createElement(internals.GexProfile, { spot: 100, gex: { error: null, net_gex_1pct_usd: 1000, flip_strike: 102.5, basis: 'b',
    by_strike: [{ strike: 100, gex_1pct_usd: 500, call_oi: 10, put_oi: null }, { strike: 105, gex_1pct_usd: -200, call_oi: 5, put_oi: 7 }] } }));
  assert.ok(gex.includes('put OI n/a'), gex);
  assert.ok(gex.includes('⚑'), 'the flip midpoint marks the nearest listed strike');
  assert.ok(gex.includes('midpoint between strikes'), gex);
});

/* ===========================================================================
   v2 (10/10/2026, Opus 5.5) — riserve del revisore sul Vol Deck. Oracoli scritti qui.
   =========================================================================== */
const { installDom, dispatch } = require('./_mini-dom.cjs');
const translate = load('i18n/t.ts');
const quote = (type, strike, iv, extra = {}) => ({ type, strike, iv, oi: 10, volume: 1, bid: 1, ask: 1.1, mid: 1.05, contract: 'O:S',
  quote_timestamp: null, quality: [], multiplier: 100, adjusted: false, ...extra });

test('MEDIA-4: adjusted or non-100 multiplier contracts are excluded and counted; quality flags mark, never hide', () => {
  languages.impostaLinguaCorrente('en');
  const out = vd.observedQuotes([quote('put', 190, 0.3), quote('put', 185, 0.31, { adjusted: true }), quote('put', 180, 0.32, { multiplier: 10 }),
    quote('put', 175, 0.33, { multiplier: null }), quote('call', 210, 0.27, { bid: 1.2, ask: 1.1 }), quote('call', 215, 0.27, { bid: 0, ask: 0.2 }),
    quote('call', 220, 0.27, { bid: 0.1, ask: 0.9 }), quote('call', 225, 0.27, { quality: ['Missing Greeks: rho'] })], 200);
  assert.equal(out.adjusted, 2, 'adjusted=true and multiplier 10 are out; a missing multiplier is not «adjusted»');
  assert.deepEqual(plain(out.quotes.map(q => [q.strike, q.flagged])), [[175, false], [190, false], [210, true], [215, true], [220, true], [225, true]]);
  assert.deepEqual(plain(out.quotes.find(q => q.strike === 210).flags), ['crossed quote (bid > ask)']);
  assert.deepEqual(plain(out.quotes.find(q => q.strike === 215).flags), ['zero bid']);
  assert.deepEqual(plain(out.quotes.find(q => q.strike === 220).flags), ['spread above the builder threshold'], '(0.9-0.1)/0.5 = 1.6 > 1');
  assert.equal(vd.observedQuotes([quote('call', 220, 0.27, { bid: 0.1, ask: 0.9 })], 200, 2).quotes[0].flagged, false, 'the threshold comes from the payload');
  assert.deepEqual(plain(out.quotes.find(q => q.strike === 225).flags), ['Missing Greeks: rho']);
});

test('MEDIA-6: the job chain is read page after page until next_offset ends, and a stuck cursor is an error', async () => {
  languages.impostaLinguaCorrente('en');
  const pages = { 0: { chain: Array.from({ length: 1000 }, (_, i) => quote('put', i + 1, 0.3)), has_more: true, next_offset: 1000, chain_complete: true, filtered_contracts: 2300 },
    1000: { chain: Array.from({ length: 1000 }, (_, i) => quote('put', 1001 + i, 0.3)), has_more: true, next_offset: 2000, chain_complete: true },
    2000: { chain: Array.from({ length: 300 }, (_, i) => quote('put', 2001 + i, 0.3)), has_more: false, next_offset: null, chain_complete: false, error: 'row KO', filtered_contracts: 2300 } };
  const asked = [];
  const whole = await vd.fetchWholeChain(async p => { asked.push(p); return pages[Number(new URL('http://x' + p).searchParams.get('offset'))]; }, 'job 1', '2035-01-10');
  assert.deepEqual(asked, [0, 1000, 2000].map(o => `/options/download/job%201/chain?expiry=2035-01-10&offset=${o}&limit=1000`));
  assert.equal(whole.chain.length, 2300); assert.equal(whole.pages, 3); assert.equal(whole.truncated, false);
  assert.equal(whole.chainComplete, false); assert.equal(whole.rowError, 'row KO', 'a row error keeps the received contracts and is declared');
  const capped = await vd.fetchWholeChain(async () => pages[0], 'j', 'e', { maxPages: 1 });
  assert.equal(capped.truncated, true); assert.equal(capped.chain.length, 1000);
  await assert.rejects(vd.fetchWholeChain(async () => ({ chain: [], has_more: true, next_offset: 0 }), 'j', 'e'), /not advancing/);
});

test('MEDIA-8: the smile scale follows the grid and liquid unflagged quotes; illiquid or flagged outliers are clipped and counted', () => {
  const qs = vd.observedQuotes([quote('put', 190, 0.3), quote('call', 210, 0.9, { oi: 0, volume: 0 }), quote('call', 215, 1.4, { bid: 0, ask: 0.1 }), quote('call', 205, 0.24)], 200).quotes;
  const scale = vd.smileScale([0.28, null, 0.25], qs);
  assert.ok(scale.y1 < 0.4, 'a 90% illiquid quote does not stretch the axis: ' + scale.y1);
  assert.deepEqual(plain(scale.clipped.map(q => q.strike)), [210, 215]);
  assert.equal(vd.smileScale([null], []), null);
});

test('MEDIA-8 in 3D: off-scale illiquid or flagged quotes are not drawn on the surface and are counted', () => {
  const S3 = load('pages/voldeck/Surface3D.tsx');
  const m = vd.surfaceModel({ spot_est: 200, moneyness_grid: [0.9, 1, 1.1], slices: [{ expiry: '2035-02-01', days: 30, iv_grid: [0.3, 0.25, 0.27] }] });
  const observed = { '2035-02-01': vd.observedQuotes([quote('put', 190, 0.29), quote('call', 215, 0.9, { oi: 0, volume: 0 }), quote('call', 205, 1.2, { bid: 1.2, ask: 1.1 })], 200).quotes };
  assert.equal(vd.surfaceQuoteScale(m, observed).clipped, 2);
  const fig = S3.surfaceFigure(m, { axis: 'moneyness', expiry: null, column: null, observed, palette: { text: '#000', muted: '#555', line: '#ddd', accent: '#00f', violet: '#70f', warn: '#850', card: '#fff', scale: S3.SCALE_LIGHT }, labels: { na: 'n/a' } });
  assert.deepEqual(fig.traces.filter(t => ['observed', 'illiquid', 'flagged'].includes(t.meta)).map(t => [t.meta, t.x.length]), [['observed', 1]]);
});

test('3D click picks the column carried by the point, else the nearest one on the axis shown', () => {
  const m = vd.surfaceModel({ spot_est: 200, moneyness_grid: [0.9, 1, 1.1], slices: [{ expiry: '2035-02-01', days: 30, iv_grid: [0.3, 0.25, 0.27] }] });
  assert.deepEqual(plain(vd.pickFromPoint({ x: 0.9, y: 30, customdata: ['2035-02-01', 2] }, m, 'moneyness')), { expiry: '2035-02-01', column: 2 }, 'customdata wins over x');
  assert.deepEqual(plain(vd.pickFromPoint({ x: 1.08, y: 30 }, m, 'moneyness')), { expiry: '2035-02-01', column: 2 });
  assert.deepEqual(plain(vd.pickFromPoint({ x: 181, y: 30 }, m, 'strike')), { expiry: '2035-02-01', column: 0 });
  assert.equal(vd.pickFromPoint({ x: 1, y: 99 }, m, 'moneyness'), null, 'an unknown expiry picks nothing');
});

test('New York times and localized numbers', () => {
  for (const [language, sep] of [['it', ','], ['en', '.']]) {
    languages.impostaLinguaCorrente(language);
    const summer = vd.nyTime('2026-10-09T19:30:00+00:00'), winter = vd.nyTime('2026-12-09T19:30:00Z', true);
    assert.ok(summer.zoned && summer.text.includes('15:30') && summer.text.endsWith('New York'), summer.text);
    assert.ok(winter.text.startsWith('14:30') && winter.text.endsWith('New York'), 'EST in December: ' + winter.text);
    assert.deepEqual(plain(vd.nyTime('2026-10-09T15:30:00')), { text: '2026-10-09 15:30:00', zoned: false });
    assert.equal(vd.numText(1.025, 3), `1${sep}025`); assert.equal(vd.pctTickText(0.225), `22${sep}5%`);
    assert.equal(vd.numText(null, 1, 'n/a'), 'n/a');
  }
  assert.deepEqual(plain(vd.discardTotals([{ n_quote_scartate: 3, n_rettificati_esclusi: 1, quote_discards: { crossed: 2, stale: 1 } },
    { n_quote_scartate: 1, quote_discards: { crossed: 1 } }, { n_quote_scartate: null }])), { total: 4, adjusted: 1, byReason: { crossed: 3, stale: 1 } });
});

test('side reading: a grid hole stays a hole, never the ATM; an unqualified ATM is shown only as such', () => {
  languages.impostaLinguaCorrente('en');
  const PointReadout = load('pages/voldeck/PointReadout.tsx').default;
  const m = vd.surfaceModel({ spot_est: 200, moneyness_grid: [0.9, 1, 1.1],
    slices: [{ expiry: '2035-02-01', days: 30, atm_iv: 0.25, iv_grid: [0.3, 0.25, null] }, { expiry: '2035-03-01', days: 60, atm_iv: null, atm_iv_unqualified: 0.231, iv_grid: [0.3, 0.26, 0.27] }] });
  const html = renderToStaticMarkup(React.createElement(PointReadout, { model: m, row: m.rows[0], column: 2 }));
  const iv = html.slice(html.indexOf('data-vol-readout-iv'), html.indexOf('</dd>', html.indexOf('data-vol-readout-iv')));
  assert.ok(iv.includes('hole (no quote)') && !iv.includes('25.00%'), 'hole, not the ATM: ' + iv);
  assert.ok(html.includes('1.100') && html.includes('220.0'), html);
  const proxy = renderToStaticMarkup(React.createElement(PointReadout, { model: m, row: m.rows[1], column: 1 }));
  const atm = proxy.slice(proxy.indexOf('data-vol-readout-atm'));
  assert.ok(atm.includes('n/a') && atm.includes('raw 23.10%, not qualified'), atm);
});

// ── cablaggio vero: React monta i componenti su un DOM minimo, effetti ed eventi compresi ──
async function mount(element) {
  const dom = installDom();
  const { createRoot } = require('react-dom/client');
  const container = dom.document.createElement('div'); dom.document.body.appendChild(container);
  const root = createRoot(container);
  const settle = () => React.act(async () => { for (let i = 0; i < 6; i++) await new Promise(r => setTimeout(r, 0)); });
  await React.act(async () => { root.render(element); }); await settle();
  return { dom, container, settle,
    rerender: async el => { await React.act(async () => { root.render(el); }); await settle(); },
    unmount: async () => { await React.act(async () => { root.unmount(); }); dom.restore(); } };
}
const fakePlotly = () => {
  const calls = [], handlers = {};
  return { calls, handlers,
    newPlot(node, traces, layout) { calls.push(['newPlot', node, traces, layout]); node._fullLayout = layout; node.on = (ev, fn) => { handlers[ev] = fn; }; return Promise.resolve(node); },
    react(node, traces, layout) { calls.push(['react', node, traces, layout]); return Promise.resolve(node); },
    relayout(node, update) { calls.push(['relayout', node, update]); return Promise.resolve(); },
    purge(node) { calls.push(['purge', node]); } };
};

test('3D wiring: newPlot once then react (camera kept), click picks the right column, reset camera, purge on unmount', async () => {
  languages.impostaLinguaCorrente('en');
  const S3 = load('pages/voldeck/Surface3D.tsx');
  const m = vd.surfaceModel({ spot_est: 200, moneyness_grid: [0.9, 1, 1.1],
    slices: [{ expiry: '2035-02-01', days: 30, iv_grid: [0.3, null, 0.27] }, { expiry: '2035-03-01', days: 60, iv_grid: [0.29, 0.26, 0.27] }] });
  const picks = [], errors = [];
  const props = { model: m, axis: 'moneyness', expiry: '2035-02-01', column: 1, observed: {}, dark: false, resetKey: 0,
    onPick: (e, c) => picks.push([e, c]), onError: e => errors.push(e) };
  const plotly = fakePlotly();
  const view = await mount(React.createElement('div')); // DOM installato, poi Plotly gia' caricato come nell'app
  globalThis.window.Plotly = plotly;
  await view.rerender(React.createElement(S3.default, props));
  try {
    const node = view.container.byAttr('data-vol-3d')[0];
    assert.equal(node.getAttribute('role'), 'group', 'an interactive chart is not role=img');
    assert.deepEqual(plotly.calls.map(c => c[0]), ['newPlot'], 'first draw creates the plot: ' + errors.join());
    assert.equal(plotly.calls[0][1], node, 'Plotly draws into the real node');
    const cells = plotly.calls[0][2].find(t => t.meta === 'cells');
    assert.equal(cells.x.length, 5, 'every valid cell (5 of 6) has a clickable marker');
    assert.equal(plotly.calls[0][3].scene.yaxis.title.text, '');
    await view.rerender(React.createElement(S3.default, { ...props, column: 2 }));
    assert.deepEqual(plotly.calls.map(c => c[0]), ['newPlot', 'react'], 'a redraw updates the plot (react), never recreates it');
    assert.equal(plotly.calls[1][3].scene.uirevision, S3.SCENE_REVISION, 'uirevision keeps the user camera');
    plotly.handlers.plotly_click({ points: [{ x: 1.1, y: 30, customdata: ['2035-02-01', 2] }] });
    plotly.handlers.plotly_click({ points: [{ x: 0.91, y: 60 }] });
    assert.deepEqual(picks, [['2035-02-01', 2], ['2035-03-01', 0]]);
    await view.rerender(React.createElement(S3.default, { ...props, column: 2, resetKey: 1 }));
    const reset = plotly.calls.find(c => c[0] === 'relayout');
    assert.deepEqual(plain(reset[2]), { 'scene.camera': plain(S3.DEFAULT_CAMERA) });
    await view.rerender(React.createElement('div')); // il grafico esce dalla pagina
  } finally { await view.unmount(); }
  const purge = plotly.calls.find(c => c[0] === 'purge');
  assert.ok(purge && purge[1] === plotly.calls[0][1], 'the plot is purged when the component goes away (WebGL context released)');
  assert.deepEqual(errors, []);
});

test('smile wiring: holes break the line, ITM is not drawn, flagged marked, hover/click/keys read the right column', async () => {
  languages.impostaLinguaCorrente('en');
  const { SmileChart } = load('pages/voldeck/SliceCharts.tsx');
  const m = vd.surfaceModel({ spot_est: 200, moneyness_grid: [0.9, 0.95, 1, 1.05, 1.1],
    slices: [{ expiry: '2035-02-01', days: 30, atm_iv: 0.25, iv_grid: [0.3, 0.28, null, 0.25, 0.26] }] });
  const qs = vd.observedQuotes([quote('put', 190, 0.29), quote('put', 220, 0.2), quote('call', 180, 0.4),
    quote('call', 210, 0.255, { bid: 1.2, ask: 1.1 }), quote('call', 215, 0.95, { oi: 0, volume: 0 })], 200).quotes;
  const cols = [], steps = [];
  const view = await mount(React.createElement(SmileChart, { model: m, expiry: '2035-02-01', column: 1, quotes: qs, axis: 'moneyness',
    onColumn: c => cols.push(c), onExpiryStep: s => steps.push(s) }));
  try {
    const chart = view.container.byAttr('data-vol-smile')[0];
    assert.equal(chart.getAttribute('role'), 'group');
    assert.equal(chart.byClass('vdn-line-grid').length, 2, 'the hole at K/S 1 splits the grid line in two');
    assert.equal(chart.byClass('vdn-hole').length, 1);
    assert.equal(chart.byClass('vdn-dot-obs').length, 1, 'only the OTM put at 190 is drawn as measured (ITM put 220 and call 180 are not)');
    assert.equal(chart.byClass('vdn-dot-flag').length, 1, 'the crossed OTM call is drawn with the flag sign');
    assert.equal(chart.byClass('vdn-dot-illiquid').length, 0, 'the 95% illiquid call is clipped, not drawn');
    assert.ok(chart.byClass('vdn-clip-note')[0].textContent.startsWith('1 illiquid or flagged'), 'and the clip is declared');
    const x = i => 54 + 728 * i / 4;
    await React.act(async () => { dispatch(view.container, chart, 'pointermove', { clientX: x(2), clientY: 100 }); });
    const tip = chart.byClass('vdn-tip')[0].textContent;
    assert.ok(tip.includes('K/S 1.000') && tip.includes('hole (no quote)') && !tip.includes('25.00%'), 'hover on the hole reads the hole, not the ATM: ' + tip);
    await React.act(async () => { dispatch(view.container, chart, 'pointermove', { clientX: x(3) + 3, clientY: 100 }); });
    assert.ok(chart.byClass('vdn-tip')[0].textContent.includes('25.00%'), 'hover reads the nearest column');
    await React.act(async () => { dispatch(view.container, chart, 'pointerdown', { clientX: x(4) - 2, clientY: 100, buttons: 1 }); });
    await React.act(async () => { dispatch(view.container, chart, 'keydown', { key: 'ArrowRight' }); dispatch(view.container, chart, 'keydown', { key: 'ArrowUp' }); });
    assert.deepEqual(cols, [4, 2], 'click picks column 4; the right arrow from column 1 picks 2');
    assert.deepEqual(steps, [1]);
  } finally { await view.unmount(); }
});

test('term wiring: the K/S tooltip line reads a grid hole as a hole, never the ATM; click picks the expiry', async () => {
  languages.impostaLinguaCorrente('en');
  const { TermChart } = load('pages/voldeck/SliceCharts.tsx');
  const m = vd.surfaceModel({ spot_est: 200, moneyness_grid: [0.9, 1, 1.1],
    slices: [{ expiry: '2035-02-01', days: 25, atm_iv: 0.25, iv_grid: [0.3, 0.25, 0.2] }, { expiry: '2035-03-01', days: 100, atm_iv: 0.27, iv_grid: [0.31, 0.27, null] }] });
  const picked = [];
  const view = await mount(React.createElement(TermChart, { model: m, column: 2, expiry: '2035-02-01', onExpiry: e => picked.push(e) }));
  try {
    const chart = view.container.byAttr('data-vol-term')[0];
    await React.act(async () => { dispatch(view.container, chart, 'pointermove', { clientX: 54 + 728 - 1, clientY: 80 }); });
    const lines = chart.byClass('vdn-tip')[0].childNodes.map(n => n.textContent);
    assert.ok(lines[0].startsWith('2035-03-01'), lines.join(' | '));
    const colLine = lines.find(l => l.startsWith('Grid at K/S 1.100'));
    assert.ok(colLine && colLine.includes('hole (no quote)') && !colLine.includes('27.00%'), 'column line: ' + lines.join(' | '));
    assert.ok(lines.some(l => !l.startsWith('Grid at') && l.includes('27.00%')), 'the ATM stays on its own line');
    assert.equal(chart.byClass('vdn-line-col').length, 1, 'the column line stops at the hole');
    await React.act(async () => { dispatch(view.container, chart, 'pointerdown', { clientX: 54 + 728 * Math.sqrt(25 / 100) + 2, clientY: 80, buttons: 1 }); });
    assert.deepEqual(picked, ['2035-02-01']);
  } finally { await view.unmount(); }
});

test('MEDIA-5: chain loads survive a change of expiries mid-flight and never stay loading; a new job drops the old', async () => {
  const { useObservedChains } = load('pages/voldeck/useObservedChains.ts');
  const pending = [], asked = [];
  const savedFetch = globalThis.fetch;
  globalThis.fetch = (url, init) => new Promise((resolve, reject) => {
    const u = new URL(url);
    const job = u.pathname.split('/')[3];
    asked.push(job + ':' + u.searchParams.get('expiry') + ':' + u.searchParams.get('offset'));
    const entry = { job, expiry: u.searchParams.get('expiry'), offset: Number(u.searchParams.get('offset')),
      reply: payload => resolve({ ok: true, status: 200, json: async () => payload }) };
    pending.push(entry);
    init?.signal?.addEventListener('abort', () => { const at = pending.indexOf(entry); if (at >= 0) pending.splice(at, 1); reject(Object.assign(new Error('aborted'), { name: 'AbortError' })); });
  });
  let last = null;
  const Harness = ({ id, exps }) => { last = useObservedChains(id, exps, true); return null; };
  const page = (n, more) => ({ chain: Array.from({ length: n }, (_, i) => quote('put', 100 + i, 0.3)), has_more: more, next_offset: more ? 1000 : null, chain_complete: true });
  const answer = async (match, payload) => { const at = pending.findIndex(match); assert.ok(at >= 0, 'pending request'); const [p] = pending.splice(at, 1);
    await React.act(async () => { p.reply(payload); for (let i = 0; i < 6; i++) await new Promise(r => setTimeout(r, 0)); }); };
  const view = await mount(React.createElement(Harness, { id: 'job1', exps: ['A', 'B'] }));
  try {
    assert.deepEqual([...asked].sort(), ['job1:A:0', 'job1:B:0']);
    await view.rerender(React.createElement(Harness, { id: 'job1', exps: ['A', 'B', 'C'] })); // le scadenze cambiano a richieste in volo
    assert.deepEqual(asked.slice(2), ['job1:C:0'], 'only the new expiry is requested; A and B stay in flight');
    await answer(p => p.expiry === 'A' && p.offset === 0, page(1000, true));
    await answer(p => p.expiry === 'A' && p.offset === 1000, page(500, false));
    await answer(p => p.expiry === 'B', page(10, false));
    await answer(p => p.expiry === 'C', page(5, false));
    const states = ['A', 'B', 'C'].map(e => last.of(e));
    assert.deepEqual(states.map(s => s.state), ['ok', 'ok', 'ok'], 'nothing is left loading');
    assert.equal(states[0].chain.length, 1500, 'A read across two pages');
    await view.rerender(React.createElement(Harness, { id: 'job2', exps: ['A'] }));
    assert.equal(last.of('A').state, 'loading', 'a new job starts over');
    await view.rerender(React.createElement(Harness, { id: 'job3', exps: ['A'] }));
    const late = pending.find(p => p.job === 'job2');
    if (late) { pending.splice(pending.indexOf(late), 1); await React.act(async () => { late.reply(page(1, false)); await new Promise(r => setTimeout(r, 0)); }); }
    await answer(p => p.job === 'job3', page(2, false));
    assert.equal(last.of('A').chain.length, 2, 'the late answer of a dropped job never paints the current one');
    // reset (contesto da un'altra istantanea) a richiesta in volo: la richiesta annullata non scrive sopra la nuova
    await view.rerender(React.createElement(Harness, { id: 'job3', exps: ['A', 'D'] }));
    await React.act(async () => { last.reset(); for (let i = 0; i < 6; i++) await new Promise(r => setTimeout(r, 0)); });
    assert.equal(last.of('D').state, 'loading', 'the aborted request never paints an error over the new one');
    assert.equal(last.of('A').state, 'loading', 'reset clears the quotes of the old snapshot and reloads them');
    await answer(p => p.job === 'job3' && p.expiry === 'D', page(3, false));
    await answer(p => p.job === 'job3' && p.expiry === 'A', page(4, false));
    assert.deepEqual([last.of('A').chain.length, last.of('D').chain.length], [4, 3]);
  } finally { await view.unmount(); globalThis.fetch = savedFetch; }
});

test('ALTA-1: context comes from the job snapshot route; without a download it is a declared new snapshot (max 12 expiries)', async () => {
  const fs = require('node:fs'), path = require('node:path'), ts = require('typescript'), vm = require('node:vm');
  const filename = path.join(SRC, 'pages/VolSurfacePage.tsx');
  const source = ts.createSourceFile(filename, fs.readFileSync(filename, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const fns = {};
  const visit = node => { if (ts.isVariableDeclaration(node) && ['loadSurface', 'loadContext'].includes(node.name.getText(source))) fns[node.name.getText(source)] = node.initializer.getText(source); ts.forEachChild(node, visit); };
  visit(source); assert.ok(fns.loadSurface && fns.loadContext);
  languages.impostaLinguaCorrente('en');
  const run = async ({ downloadId, expiries, rawData }) => {
    const calls = [], origins = [], errors = [], states = []; let resets = 0;
    const scope = { exports: {}, AbortController, Error, String, encodeURIComponent, ticker: 'SYNTH', downloadId, rawData,
      requestRef: { current: null }, lastExpiries: { current: expiries }, MAX_REFETCH_EXPIRIES: vd.MAX_REFETCH_EXPIRIES, tr: translate.t,
      setLoading: () => {}, setError: v => errors.push(v), setData: () => {}, setCone: () => {}, setContextState: v => states.push(v),
      setContextOrigin: v => origins.push(v), observedChains: { reset: () => { resets++; } },
      volRequest: async p => { calls.push(p); return p.startsWith('/options/vol_cone/') ? {} : { slices: [{}], snapshot_at: p.includes('/context') ? '2026-10-09T19:00:00+00:00' : '2026-10-09T20:15:00+00:00' }; } };
    vm.runInNewContext(ts.transpileModule(`const loadSurface = ${fns.loadSurface};\nexports.run = ${fns.loadContext};`, {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, scope);
    await scope.exports.run();
    return { calls, origins, errors, states, resets };
  };
  const job = await run({ downloadId: 'job 7', expiries: Array.from({ length: 20 }, (_, i) => `2035-01-${String(i + 1).padStart(2, '0')}`), rawData: {} });
  assert.deepEqual(job.calls, ['/options/download/job%207/context', '/options/vol_cone/SYNTH'], 'the job route, even with 20 expiries');
  assert.deepEqual(plain(job.origins), [{ kind: 'download', at: '2026-10-09T19:00:00+00:00' }]); assert.equal(job.resets, 0, 'same snapshot: quotes kept');
  const many = await run({ downloadId: null, expiries: Array.from({ length: 13 }, (_, i) => `2035-01-${i + 10}`), rawData: {} });
  assert.deepEqual(many.calls, [], 'over 12 expiries the re-downloading route is not even asked');
  assert.ok(many.errors.at(-1).includes('13 expiries') && many.errors.at(-1).includes('at most 12'), many.errors.at(-1));
  assert.deepEqual(many.states, ['error']);
  const fresh = await run({ downloadId: null, expiries: ['2035-01-10'], rawData: { snapshot_at: '2026-10-09T18:00:00+00:00' } });
  assert.equal(fresh.calls[0], '/options/vol_surface/SYNTH?expiries=2035-01-10&include_context=true');
  assert.deepEqual(plain(fresh.origins), [{ kind: 'new_fetch', at: '2026-10-09T20:15:00+00:00', previous: '2026-10-09T18:00:00+00:00' }]);
  assert.equal(fresh.resets, 1, 'quotes of another snapshot are cleared');
  const words = translate.t('voldeck.n_ctx_from_new', { a: vd.nyTime('2026-10-09T20:15:00+00:00', true).text, b: vd.nyTime('2026-10-09T18:00:00+00:00', true).text });
  assert.equal(words, 'New surface of 16:15 New York, download quotes of 14:00 New York: different snapshots, measured quotes cleared.');
});
