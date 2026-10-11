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
  const code = fs.readFileSync(filename, 'utf8') + '\nexport const __internals = { IvAltimeter, GexProfile };\n';
  const js = ts.transpileModule(code, { fileName: filename, compilerOptions: { module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const resolve = base => ['', '.ts', '.tsx', '/index.ts', '/index.tsx'].map(e => base + e).find(f => fs.existsSync(f) && fs.statSync(f).isFile());
  const req = spec => /\.css$/.test(spec) ? {} : (!spec.startsWith('@/') && !spec.startsWith('.')) ? require(spec)
    : load(resolve(spec.startsWith('@/') ? path.join(SRC, spec.slice(2)) : path.resolve(path.dirname(filename), spec)));
  const holder = { exports: {} };
  new Function('exports', 'require', 'module', js)(holder.exports, req, holder);
  return holder.exports.__internals;
})();

// 10/10 (review): la tabella RR/BF del builder e' stata tolta (un solo RR25 in pagina, quello della libreria)
test('the builder RR/BF table is gone: one RR25 on the page', () => {
  const src = require('node:fs').readFileSync(require('node:path').join(SRC, 'pages/VolSurfacePage.tsx'), 'utf8');
  assert.ok(!src.includes('function SkewTable') && !src.includes("'n_m_rr25'"), 'no builder RR25 table nor its method line');
});

test('IV rank: a null percentile is declared, not drawn as 0; GEX put OI missing is n/a', () => {
  languages.impostaLinguaCorrente('en');
  const rank = renderToStaticMarkup(React.createElement(internals.IvAltimeter, { ctx: { iv_percentile: null, error: null } }));
  assert.ok(rank.includes('IV percentile n/a'), rank); assert.ok(!rank.includes('>0<'), rank);
  const ok = renderToStaticMarkup(React.createElement(internals.IvAltimeter, { ctx: { iv_percentile: 42, iv_front_current: null, iv_min: 0.18, iv_max: 0.4, n_obs: 90, history_from: '2026-05-01' } }));
  assert.ok(!ok.includes('front ATM'), 'no backend front ATM next to the rank: the page has one ATM, the library ATMF (review 2): ' + ok);
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

test('v3 (10/10): measured quotes are no longer drawn on the 3D surface (they live on the smile); the scale helper still counts off-scale ones', () => {
  const S3 = load('pages/voldeck/Surface3D.tsx');
  const m = vd.surfaceModel({ spot_est: 200, moneyness_grid: [0.9, 1, 1.1], slices: [{ expiry: '2035-02-01', days: 30, iv_grid: [0.3, 0.25, 0.27] }] });
  const observed = { '2035-02-01': vd.observedQuotes([quote('put', 190, 0.29), quote('call', 215, 0.9, { oi: 0, volume: 0 }), quote('call', 205, 1.2, { bid: 1.2, ask: 1.1 })], 200).quotes };
  assert.equal(vd.surfaceQuoteScale(m, observed).clipped, 2);
  const fig = S3.surfaceFigure(m, { axis: 'moneyness', expiry: null, column: null, observed, palette: { text: '#000', muted: '#555', line: '#ddd', accent: '#00f', violet: '#70f', warn: '#850', card: '#fff', scale: S3.SCALE_LIGHT }, labels: { na: 'n/a' } });
  assert.deepEqual(fig.traces.filter(t => ['observed', 'illiquid', 'flagged', 'cells'].includes(t.meta)), []);
  // v2: una sola scadenza = nessuna faccia, ogni cella ha il suo marcatore con lettura e clic
  assert.deepEqual(fig.traces.map(t => t.meta), ['surface', 'wire', 'orphans', 'sel-expiry', 'sel-col', 'sel-point']);
});

test('3D click picks the column carried by the point, else the nearest one on the axis shown', () => {
  const m = vd.surfaceModel({ spot_est: 200, moneyness_grid: [0.9, 1, 1.1], slices: [{ expiry: '2035-02-01', days: 30, iv_grid: [0.3, 0.25, 0.27] }] });
  assert.deepEqual(plain(vd.pickFromPoint({ x: 0.9, y: 30, customdata: ['2035-02-01', 2] }, m, 'moneyness')), { expiry: '2035-02-01', column: 2 }, 'customdata wins over x');
  assert.deepEqual(plain(vd.pickFromPoint({ x: 1.08, y: 30 }, m, 'moneyness')), { expiry: '2035-02-01', column: 2 });
  assert.deepEqual(plain(vd.pickFromPoint({ x: 181, y: 30 }, m, 'strike')), { expiry: '2035-02-01', column: 0 });
  assert.equal(vd.pickFromPoint({ x: 1, y: 99 }, m, 'moneyness'), null, 'an unknown expiry picks nothing');
  // v3: il 3D disegna le scadenze in sqrt(t); senza customdata la y e' sqrt(giorni)
  assert.deepEqual(plain(vd.pickFromPoint({ x: 1.01, y: Math.sqrt(30) }, m, 'moneyness', true)), { expiry: '2035-02-01', column: 1 });
  assert.equal(vd.pickFromPoint({ x: 1, y: 30 }, m, 'moneyness', true), null, 'on the sqrt axis a raw day count is not an expiry');
  // v3: la y torna da WebGL in float: uno scarto relativo di 1e-7 sceglie comunque la scadenza giusta, fra scadenze vicine
  const near = vd.surfaceModel({ spot_est: 100, moneyness_grid: [0.9, 1, 1.1], slices: [2, 3, 4].map(d => ({ expiry: '2035-01-0' + d, days: d, iv_grid: [0.2, 0.2, 0.2] })) });
  for (const d of [2, 3, 4]) for (const f of [1 + 1e-7, 1 - 1e-7]) {
    assert.equal(vd.pickFromPoint({ x: 1, y: Math.sqrt(d) * f }, near, 'moneyness', true)?.expiry, '2035-01-0' + d, `float y for ${d} days`);
  }
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
// v3 10/10 (Opus 5.5): il finto Plotly tiene DUE camere come quello vero: quella scritta nel layout
// (_fullLayout.scene.camera) e quella che la scena WebGL mostra (_scene.getCamera()). Un drag rilasciato
// fuori dal canvas muove la seconda senza toccare la prima: e' il difetto della diagnosi diag3d_f.
const fakePlotly = () => {
  const calls = [], handlers = {};
  const scene = { written: null, shown: null };
  const fullLayout = layout => ({ scene: { get camera() { return scene.written; }, _scene: { getCamera: () => scene.shown } }, _src: layout });
  const copy = c => JSON.parse(JSON.stringify(c));
  return { calls, handlers, scene,
    /** l'utente ruota con un drag che finisce fuori dal canvas: la vista cambia, il layout no */
    rotate(camera) { scene.shown = copy(camera); },
    newPlot(node, traces, layout, config) { calls.push(['newPlot', node, traces, layout, config]); scene.written = copy(layout.scene.camera); scene.shown = copy(layout.scene.camera);
      node._fullLayout = fullLayout(layout); node.on = (ev, fn) => { handlers[ev] = fn; }; return Promise.resolve(node); },
    react(node, traces, layout, config) { calls.push(['react', node, traces, layout, config]); scene.written = copy(layout.scene.camera); scene.shown = copy(layout.scene.camera); return Promise.resolve(node); },
    restyle(node, update, indices) { calls.push(['restyle', node, update, indices]); scene.shown = copy(scene.written); return Promise.resolve(node); },
    relayout(node, update) { calls.push(['relayout', node, update]);
      if (update['scene.camera']) { scene.written = copy(update['scene.camera']); scene.shown = copy(update['scene.camera']); handlers.plotly_relayout?.(update); }
      return Promise.resolve(); },
    purge(node) { calls.push(['purge', node]); } };
};

test('3D wiring: camera survives redraws (live camera read back), selection = restyle only, wheel off, turntable, presets, purge', async () => {
  languages.impostaLinguaCorrente('en');
  const S3 = load('pages/voldeck/Surface3D.tsx');
  const m = vd.surfaceModel({ spot_est: 200, moneyness_grid: [0.9, 1, 1.1],
    slices: [{ expiry: '2035-02-01', days: 30, iv_grid: [0.3, null, 0.27] }, { expiry: '2035-03-01', days: 60, iv_grid: [0.29, 0.26, 0.27] }] });
  const picks = [], errors = [];
  let rotations = 0;
  const props = { model: m, axis: 'moneyness', expiry: '2035-02-01', column: 0, dark: false, resetKey: 0,
    onPick: (e, c) => picks.push([e, c]), onError: e => errors.push(e), onUserRotate: () => rotations++ };
  const plotly = fakePlotly();
  const view = await mount(React.createElement('div')); // DOM installato, poi Plotly gia' caricato come nell'app
  const winListeners = {};
  globalThis.window.Plotly = plotly;
  globalThis.window.addEventListener = (type, fn) => { (winListeners[type] ||= []).push(fn); };
  globalThis.window.removeEventListener = (type, fn) => { winListeners[type] = (winListeners[type] || []).filter(f => f !== fn); };
  await view.rerender(React.createElement(S3.default, props));
  const names = () => plotly.calls.map(c => c[0]);
  const rotated = { eye: { x: 0.3, y: 2.0, z: -0.4 }, up: { x: 0, y: 0, z: 1 }, center: { x: 0, y: 0, z: 0 } };
  const rotated2 = { eye: { x: -2.1, y: 0.2, z: 0.7 }, up: { x: 0, y: 0, z: 1 }, center: { x: 0.1, y: 0, z: -0.1 } };
  try {
    const node = view.container.byAttr('data-vol-3d')[0];
    assert.equal(node.getAttribute('role'), 'group', 'an interactive chart is not role=img');
    assert.deepEqual(names(), ['newPlot'], 'first draw creates the plot: ' + errors.join());
    assert.equal(plotly.calls[0][1], node, 'Plotly draws into the real node');
    assert.equal(plotly.calls[0][4].scrollZoom, false, 'the wheel scrolls the page');
    assert.equal(plotly.calls[0][3].scene.dragmode, 'turntable');
    assert.equal(plotly.calls[0][3].scene.yaxis.title.text, '');
    assert.equal(plotly.calls[0][2][0].type, 'mesh3d');
    // 1) rotazione con rilascio fuori dal canvas, poi cambio di selezione: la vista NON torna indietro
    plotly.rotate(rotated);
    await view.rerender(React.createElement(S3.default, { ...props, column: 1 }));
    assert.deepEqual(names(), ['newPlot', 'relayout', 'restyle'], 'a selection change is a restyle (after writing the live camera), never a react');
    assert.deepEqual(plain(plotly.calls[1][2]['scene.camera']), rotated, 'the live camera is written into the layout before restyle');
    const [, , upd, idx] = plotly.calls[2];
    const sel = plotly.calls[0][2].map((t, i) => [t.meta, i]).filter(([meta]) => meta.startsWith('sel-')).map(([, i]) => i);
    assert.deepEqual(plain(idx), sel, 'restyle touches only the three selection traces');
    assert.deepEqual(plain(upd.z[1]), [null, 26], 'the K/S line at column 1: grid values, the hole stays null');
    assert.deepEqual(plain(upd.x[2]), [], 'no selected-point marker on a hole');
    assert.deepEqual(plain(plotly.scene.shown), rotated, 'after the redraw the user still sees the rotated view');
    // 2) seconda rotazione rilasciata fuori: il mouseup su window la scrive nel layout
    plotly.rotate(rotated2);
    assert.ok((winListeners.mouseup || []).length >= 1, 'mouseup listened on window, not only on the canvas');
    for (const fn of winListeners.mouseup) fn({});
    await view.settle();
    assert.deepEqual(plain(plotly.calls[plotly.calls.length - 1][2]['scene.camera']), rotated2);
    // 3) un ridisegno completo (asse strike) passa la camera VIVA esplicita nel layout
    await view.rerender(React.createElement(S3.default, { ...props, column: 2, axis: 'strike' }));
    const react = plotly.calls.filter(c => c[0] === 'react').pop();
    assert.ok(react, 'axis change is a full react: ' + names().join());
    assert.deepEqual(plain(react[3].scene.camera), rotated2, 'camera preserved after a full redraw');
    assert.equal(react[3].scene.uirevision, S3.SCENE_REVISION);
    assert.deepEqual(plain(plotly.scene.shown), rotated2);
    // clic: customdata vince; senza customdata la y e' sqrt(giorni)
    plotly.handlers.plotly_click({ points: [{ x: 220, y: Math.sqrt(30), customdata: ['2035-02-01', 2] }] });
    plotly.handlers.plotly_click({ points: [{ x: 181, y: Math.sqrt(60) }] });
    assert.deepEqual(picks, [['2035-02-01', 2], ['2035-03-01', 0]]);
    // drag vs clic: gl3d emette plotly_click DURANTE la pressione; si applica al rilascio solo se il puntatore e' fermo
    const own = type => node.listeners.filter(l => l.type === type && l.capture).map(l => l.listener);
    const fire = (type, ev) => { for (const fn of own(type)) fn(ev); };
    const win = (type, ev) => { for (const fn of winListeners[type] || []) fn(ev); };
    fire('pointerdown', { clientX: 100, clientY: 100 });
    win('pointermove', { clientX: 140, clientY: 110, buttons: 1 });
    plotly.handlers.plotly_click({ points: [{ x: 200, y: Math.sqrt(60), customdata: ['2035-03-01', 1] }] });
    win('pointerup', {}); win('mouseup', {});
    await view.settle();
    assert.equal(picks.length, 2, 'a rotation drag released on the canvas does not change the selection');
    fire('pointerdown', { clientX: 100, clientY: 100 });
    win('pointermove', { clientX: 102, clientY: 101, buttons: 1 });
    plotly.handlers.plotly_click({ points: [{ x: 200, y: Math.sqrt(60), customdata: ['2035-03-01', 1] }] });
    assert.equal(picks.length, 2, 'the pick waits for the release');
    win('pointerup', {});
    await view.settle();
    assert.deepEqual(picks[2], ['2035-03-01', 1], 'a still click (<= DRAG_PX) picks on release');
    // rotella: in cattura sul contenitore, ferma la propagazione verso il canvas e NON blocca lo scorrimento
    let stopped = 0, prevented = 0;
    fire('wheel', { stopPropagation: () => stopped++, preventDefault: () => prevented++ });
    assert.equal(stopped, 1, 'the wheel never reaches the Plotly canvas (which would preventDefault)');
    assert.equal(prevented, 0, 'the page keeps scrolling');
    // v2: solo il drag oltre DRAG_PX e' una rotazione manuale (il preset in vista si spegne); il clic fermo no
    assert.equal(rotations, 1, 'one manual rotation so far (the 40 px drag), the still click is not one');
    // pointercancel: il gesto annullato non sceglie nulla e non e' una rotazione
    fire('pointerdown', { clientX: 100, clientY: 100 });
    plotly.handlers.plotly_click({ points: [{ x: 200, y: Math.sqrt(30), customdata: ['2035-02-01', 0] }] });
    win('pointercancel', {}); win('pointerup', {});
    await view.settle();
    assert.equal(picks.length, 3, 'a cancelled gesture picks nothing'); assert.equal(rotations, 1);
    // cambio di tema: ridisegno completo (react), mai un restyle coi colori vecchi
    const before = plotly.calls.length;
    await view.rerender(React.createElement(S3.default, { ...props, column: 2, axis: 'strike', dark: true }));
    assert.deepEqual(plotly.calls.slice(before).map(c => c[0]).filter(n => n !== 'relayout'), ['react'], 'theme change is a react');
    await view.rerender(React.createElement(S3.default, { ...props, column: 2, axis: 'strike' }));
    // 4) preset di camera e ripristino
    await view.rerender(React.createElement(S3.default, { ...props, column: 2, axis: 'strike', preset: { name: 'top', n: 1 } }));
    assert.deepEqual(plain(plotly.calls.filter(c => c[0] === 'relayout').pop()[2]), { 'scene.camera': plain(S3.CAMERAS.top) });
    await view.rerender(React.createElement(S3.default, { ...props, column: 2, axis: 'strike', preset: { name: 'top', n: 1 }, resetKey: 1 }));
    assert.deepEqual(plain(plotly.calls.filter(c => c[0] === 'relayout').pop()[2]), { 'scene.camera': plain(S3.DEFAULT_CAMERA) });
    // dopo il ripristino un cambio di selezione non resuscita la vista vecchia
    await view.rerender(React.createElement(S3.default, { ...props, column: 0, axis: 'strike', preset: { name: 'top', n: 1 }, resetKey: 1 }));
    assert.equal(plotly.calls[plotly.calls.length - 1][0], 'restyle');
    assert.deepEqual(plain(plotly.scene.shown), plain(S3.DEFAULT_CAMERA));
    await view.rerender(React.createElement('div')); // il grafico esce dalla pagina
    assert.deepEqual(winListeners.mouseup, [], 'the window listener is removed on unmount');
  } finally { await view.unmount(); }
  const purge = plotly.calls.find(c => c[0] === 'purge');
  assert.ok(purge && purge[1] === plotly.calls[0][1], 'the plot is purged when the component goes away (WebGL context released)');
  assert.deepEqual(errors, []);
});

test('v3 numeric grid: holes are hatched n/a never filled, cells use the 3D scale with readable ink, click/hover sync expiry and K/S', async () => {
  languages.impostaLinguaCorrente('en');
  const IvGrid = load('pages/voldeck/IvGrid.tsx').default;
  const S3 = load('pages/voldeck/Surface3D.tsx');
  const m = vd.surfaceModel({ spot_est: 200, moneyness_grid: [0.9, 1, 1.1],
    slices: [{ expiry: '2035-02-01', days: 30, iv_grid: [0.3, null, 0.2] }, { expiry: '2035-03-01', days: 60, iv_grid: [0.29, 0.26, 0.4] }] }, ['2035-03-01']);
  const picks = [], exps = [], cols = [];
  const props = { model: m, axis: 'moneyness', expiry: '2035-03-01', column: 2, onPick: (e, c) => picks.push([e, c]), onExpiry: e => exps.push(e), onColumn: c => cols.push(c) };
  const html = renderToStaticMarkup(React.createElement(IvGrid, props));
  const holes = html.match(/<td[^>]*data-vol-grid-hole[^>]*>([^<]*)<\/td>/g) || [];
  assert.equal(holes.length, 1, 'one hole, one hatched cell');
  assert.ok(holes[0].includes('>n/a</td>') && !holes[0].includes('background'), 'a hole is n/a with no fill colour: ' + holes[0]);
  const cells = [...html.matchAll(/<td class="([^"]*)" style="background:rgb\((\d+),(\d+),(\d+)\);color:(#[0-9a-f]+)"[^>]*>([^<]*)<\/td>/g)];
  assert.equal(cells.length, 5, 'five valid cells are coloured: ' + html);
  const top = cells.find(c => c[6] === '40.0'), low = cells.find(c => c[6] === '20.0');
  assert.ok(Number(top[2]) > 200 && Number(top[4]) < 80, 'highest IV is red: ' + top.slice(2, 5));
  assert.ok(Number(low[4]) > Number(low[2]), 'lowest IV is blue: ' + low.slice(2, 5));
  for (const c of cells) assert.equal(c[5], S3.inkOn([+c[2], +c[3], +c[4]]), 'ink chosen for contrast');
  assert.ok(top[1].includes('is-pick'), 'the selected cell (60d, K/S 1.1) is marked');
  // v2: stessi estremi del 3D (colorRange, p1/p99) — qui il p99 (30%) non e' il massimo (40%)
  const cr = S3.colorRange(m);
  assert.equal(cr.cmax, 30, 'p99 of five cells is the second largest');
  for (const c of cells) {
    const rgb = S3.scaleColor((Number(c[6]) - cr.cmin) / (cr.cmax - cr.cmin));
    assert.deepEqual([+c[2], +c[3], +c[4]], rgb, `cell ${c[6]} coloured on the shared extremes`);
  }
  // v2 accessibilita': una sola cella nel Tab (quella scelta), ⚠ parziale con testo per i lettori di schermo
  assert.equal((html.match(/tabindex="0"/g) || []).length, 1);
  assert.ok(/<td class="is-pick[^"]*" style="[^"]*" title="[^"]*" tabindex="0"/.test(html), 'the tab stop is the selected cell');
  assert.ok(html.includes('<span aria-hidden="true">⚠</span><span class="vdn-sr">partial chain</span>'), 'partial flag has accessible text');
  const view = await mount(React.createElement(IvGrid, props));
  try {
    const tds = view.container.byTag('td');
    await React.act(async () => { dispatch(view.container, tds[1], 'click'); });
    await React.act(async () => { dispatch(view.container, tds[0], 'click'); });
    await React.act(async () => { dispatch(view.container, tds[3], 'keydown', { key: 'Enter' }); });
    await React.act(async () => { dispatch(view.container, tds[0], 'keydown', { key: 'ArrowDown' }); });
    assert.equal(view.dom.document.activeElement, tds[3], 'ArrowDown moves the focus one expiry DOWN (row 1, same column)');
    await React.act(async () => { dispatch(view.container, tds[3], 'keydown', { key: 'ArrowRight' }); });
    assert.equal(view.dom.document.activeElement, tds[4], 'ArrowRight moves one K/S right');
    await React.act(async () => { dispatch(view.container, tds[4], 'keydown', { key: 'ArrowUp' }); });
    assert.equal(view.dom.document.activeElement, tds[1], 'ArrowUp moves one expiry up');
    await React.act(async () => { dispatch(view.container, tds[4], 'focusin'); });
    const read = view.container.byAttr('data-vol-grid-read')[0].textContent;
    assert.ok(read.includes('2035-03-01') && read.includes('1.000') && read.includes('26.00%'), 'focus gives the exact reading: ' + read);
    const buttons = view.container.byTag('button');
    await React.act(async () => { dispatch(view.container, buttons[0], 'click'); dispatch(view.container, buttons[buttons.length - 1], 'click'); });
    assert.deepEqual(picks, [['2035-02-01', 1], ['2035-02-01', 0], ['2035-03-01', 0]],
      'a hole picks itself (readouts then say hole); a valid cell picks row AND column; Enter on a focused cell picks it');
    assert.deepEqual(cols, [0]); assert.deepEqual(exps, ['2035-03-01']);
  } finally { await view.unmount(); }
});

test('v3 slices take the MEASURED width (250 px at 390): no fixed 320 px minimum that overflows the page', async () => {
  languages.impostaLinguaCorrente('en');
  const { SmileChart, TermChart } = load('pages/voldeck/SliceCharts.tsx');
  const m = vd.surfaceModel({ spot_est: 200, moneyness_grid: [0.9, 1, 1.1],
    slices: [{ expiry: '2035-02-01', days: 30, iv_grid: [0.3, 0.25, 0.27] }, { expiry: '2035-03-01', days: 60, iv_grid: [0.29, 0.26, 0.27] }] });
  const view = await mount(React.createElement('div'));
  view.dom.document.defaultRect = { left: 0, top: 0, width: 250, height: 300 };
  try {
    await view.rerender(React.createElement('div', null,
      React.createElement(SmileChart, { model: m, expiry: '2035-02-01', column: 1, onColumn() {}, onExpiryStep() {}, quotes: null, axis: 'moneyness' }),
      React.createElement(TermChart, { model: m, column: 1, expiry: '2035-02-01', onExpiry() {} })));
    const svgs = view.container.byTag('svg');
    assert.equal(svgs.length, 2);
    for (const svg of svgs) assert.equal(String(svg.getAttribute('width')), '250', 'the chart is as wide as its box, not 320');
  } finally { await view.unmount(); }
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

/* ===========================================================================
   Vol Deck «quant» (10/10/2026, Opus 5.5): CABLAGGIO payload → lib/vol-quant → numero mostrato.
   Oracoli scritti qui: il forward della chain sintetica e' costruito per parita' esatta (F letterale),
   la vista confronta il numero a schermo con la libreria chiamata DIRETTAMENTE sugli stessi input.
   =========================================================================== */
const VQ = load('lib/vol-quant.ts');
const QUANT = load('pages/voldeck/quant.ts');
const QTEXT = load('pages/voldeck/quantText.ts');
const OVER = load('pages/voldeck/overlays.ts');
const SNAPS = load('lib/vol-snapshots.ts');
const QGRID = load('pages/voldeck/IvGrid.tsx').default;
const SKEWV = load('pages/voldeck/SkewView.tsx');
const QUALV = load('pages/voldeck/QualityView.tsx');
const TERMV = load('pages/voldeck/TermView.tsx').default;
const S3Q = load('pages/voldeck/Surface3D.tsx');
const quiet = render => { const prev = console.error; console.error = () => {}; try { return render(); } finally { console.error = prev; } };

const QGRID_M = [0.8, 0.85, 0.9, 0.95, 1, 1.05, 1.1, 1.15, 1.2];
const QEXP = [{ e: '2035-02-01', d: 30, atm: 0.2, F: 100.3 }, { e: '2035-03-03', d: 60, atm: 0.21, F: 100.7 }, { e: '2035-04-02', d: 90, atm: 0.22, F: 101.0 }];
const QR = 0.04;
const smileIv = (m, atm) => atm - 0.1 * Math.log(m) + 0.3 * Math.log(m) ** 2;
/** t_years del builder (fino alla chiusura di New York) DIVERSO da days/365: l'oracolo usa T, mai i giorni (review N2) */
const TY = x => (x.d + 0.3) / 365;
/** con una data utili, le scadenze DOPO portano la varianza d'evento di una mossa del 4% (griglia E chain) */
const evOf = (x, earnings, premium) => (v, T) => earnings && premium && x.e > earnings ? Math.sqrt(v * v + 0.04 ** 2 / T) : v;
/** payload del builder; `bend` abbassa l'ala destra della 60 g (calendario voluto); una fetta a 1 giorno che il modello
 *  esclude (0-1 DTE) e che non deve entrare in nessun calcolo (review N11) */
const qPayload = ({ bend = 1, earnings = null, premium = true } = {}) => ({ ticker: 'SYNQ', spot_est: 100, snapshot_at: '2035-01-02T21:00:00Z', moneyness_grid: QGRID_M,
  next_earnings: earnings,
  slices: [{ expiry: '2035-01-03', days: 1, t_years: 1.3 / 365, atm_iv: 0.5, iv_grid: QGRID_M.map(m => 0.9 - 0.4 * m) },
    ...QEXP.map(x => { const ev = evOf(x, earnings, premium); return { expiry: x.e, days: x.d, t_years: TY(x), atm_iv: ev(x.atm, TY(x)),
    iv_grid: QGRID_M.map(m => Number((ev(smileIv(m, x.atm), TY(x)) * (x.d === 60 && m >= 1.1 ? bend : 1)).toFixed(6))) }; })] });
/** Black-76 scritto qui (Abramowitz-Stegun 7.1.26 per N, errore < 2e-7): prezzi senza arbitraggio */
const qN = x => { const t = 1 / (1 + 0.3275911 * Math.abs(x) / Math.SQRT2), y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x / 2);
  return x >= 0 ? (1 + y) / 2 : (1 - y) / 2; };
const qPut = (F, K, T, v) => { const s = v * Math.sqrt(T), d1 = (Math.log(F / K) + s * s / 2) / s; return Math.exp(-QR * T) * (K * qN(-(d1 - s)) - F * qN(-d1)); };
/** chain a parita' ESATTA: C − P = e^{−rT}(F − K) sui mid → il forward della parita' grezza e' F (oracolo letterale) */
const qChain = (x, { earnings = null, premium = true } = {}) => {
  const T = TY(x), out = [], ev = evOf(x, earnings, premium);
  for (let K = 80; K <= 120; K += 5) {
    const iv = ev(smileIv(K / 100, x.atm), T);
    const put = qPut(x.F, K, T, iv), call = put + Math.exp(-QR * T) * (x.F - K);
    for (const [type, mid] of [['put', put], ['call', call]]) out.push({ type, strike: K, expiry: x.e, bid: mid - 0.02, ask: mid + 0.02, mid,
      iv, oi: 50, volume: 5, multiplier: 100, adjusted: false, quality: [], contract: null, quote_timestamp: null });
  }
  return out;
};
const qChains = (opts = {}) => Object.fromEntries(QEXP.map(x => [x.e, { status: 'ok', chain: qChain(x, opts) }]));
const qPrev = () => ({ ticker: 'SYNQ', at: '2035-01-01T21:00:00Z', downloadId: 'prev', kind: 'download_snapshot', snapshot_at: '2035-01-01T21:00:00Z',
  spot_est: 99.5, moneyness_grid: QGRID_M, slices: QEXP.map(x => ({ expiry: x.e, days: x.d + 1, t_years: TY(x) + 1 / 365, atm_iv: x.atm - 0.01,
    iv_grid: QGRID_M.map(m => smileIv(m, x.atm) - 0.01 - 0.004 * (m - 1)) })) });
// DGS3MO e' in percento bond-equivalent: il percento che da' esattamente r = QR continuo
const QPCT = 200 * (Math.exp(QR / 2) - 1);
const qBuild = (opts = {}, { previous = null, rate = { value: QPCT / 100, percent: QPCT, status: 'solid', date: '2035-01-01', source: 'FRED DGS3MO', error: null }, chains = qChains(opts) } = {}) => {
  const data = qPayload(opts), model = vd.surfaceModel(data);
  return { data, model, q: QUANT.volQuant({ data, model, chains, rate, previous }) };
};
/** solo le fette che il modello disegna (>= 2 giorni) */
const qVisible = data => ({ ...data, slices: data.slices.filter(x => x.days >= 2) });
/** la libreria chiamata DIRETTAMENTE sugli stessi input (non attraverso quant.ts) */
const libDirect = (data, opts = {}) => {
  const vis = qVisible(data);
  const surface = VQ.surfaceFromBuilder(vis);
  const fwd = VQ.impliedForward(QEXP.map(x => ({ expiry: x.e, T: TY(x), r: QR, contracts: qChain(x, opts), spot: 100 })));
  const observed = VQ.surfaceFromChains(QEXP.map(x => ({ expiry: x.e, T: TY(x), contracts: qChain(x, opts) })), fwd, { asOf: data.snapshot_at, spot: 100, r: QR });
  const grid = VQ.deltaGrid(observed, fwd);
  // un solo ATM: l'ATMF della griglia delta (termine, vol forward, utili, cono)
  const term = QEXP.map(x => ({ expiry: x.e, T: TY(x), iv: grid.rows.find(r => r.expiry === x.e).cells.ATMF.iv }));
  return { vis, surface, fwd, observed, grid, term };
};

test('quant wiring: forward from parity, delta grid and OVDV table show exactly the library numbers', () => {
  languages.impostaLinguaCorrente('en');
  const { data, model, q } = qBuild();
  assert.ok(Math.abs(q.r - QR) < 1e-12, 'DGS3MO percent bond-equivalent → continuous decimal r');
  const lib = libDirect(data), skew = VQ.skewMetrics(lib.grid);
  QEXP.forEach(x => {
    assert.ok(Math.abs(q.forwards[x.e].rawForward - x.F) < 1e-9, `raw parity forward ${x.e}: ${q.forwards[x.e].rawForward} vs ${x.F} (oracle)`);
    assert.equal(q.forwardOf(x.e).F, lib.fwd[x.e].forward, 'de-americanised forward = library');
  });
  assert.deepEqual(plain(q.deltaHeads), ['10ΔP', '25ΔP', 'ATMF', '25ΔC', '10ΔC']);
  q.deltaModel.rows.forEach((row, j) => row.iv.forEach((v, i) => assert.equal(v, lib.grid.rows[j].cells[lib.grid.columns[i]].iv, `delta cell ${j},${i}`)));
  // griglia in delta: intestazioni e celle a schermo
  const grid = renderToStaticMarkup(React.createElement(QGRID, { model: q.deltaModel, axis: 'delta', expiry: QEXP[1].e, column: 2, onPick() {}, onExpiry() {}, onColumn() {},
    extras: { heads: q.deltaHeads, cellLabel: SKEWV.deltaCellLabel(q, model) } }));
  for (const h of q.deltaHeads) assert.ok(grid.includes(`>${h}</button>`), 'delta header ' + h);
  const atm60 = lib.grid.rows[1].cells.ATMF.iv;
  assert.ok(grid.includes(`>${vd.numText(atm60 * 100, 1)}</td>`), 'ATMF 60d cell shows the library value ' + atm60);
  // tabella OVDV: ATM, RR25, BF25, RR10, BF10, skew normalizzato della riga 60 g
  const table = renderToStaticMarkup(React.createElement(SKEWV.OvdvTable, { q, model, expiry: null, onExpiry() {} }));
  const row60 = table.split('data-vol-skew-row="2035-03-03"')[1].split('</tr>')[0];
  for (const v of [vd.ivText(skew[1].atmf, '', 1), QTEXT.ptText(skew[1].rr25), QTEXT.ptText(skew[1].bf25), QTEXT.ptText(skew[1].rr10),
    QTEXT.ptText(skew[1].bf10), QTEXT.ratioText(skew[1].skewNorm25)]) assert.ok(row60.includes(`>${v}</td>`), `OVDV shows ${v}: ${row60}`);
  // il forward con la sua qualita' (coppie e dispersione dei candidati della parita')
  const fr = q.forwards['2035-03-03'];
  assert.ok(row60.includes(`>${vd.priceText(fr.forward, '')}<small> ±${vd.numText(fr.dispersionRel * 100, 3)}%</small>`), 'forward and its dispersion: ' + row60);
  assert.ok(row60.includes('de-americanised (raw F ' + vd.numText(fr.rawForward, 2)), 'raw forward and de-americanisation residual declared: ' + row60);
  assert.ok(row60.includes(`${fr.pairs.length} call/put pairs`), 'pairs used are declared');
  assert.ok(skew[1].rr25 < 0, 'synthetic put skew: RR25 negative');
});

test('quant wiring: delta axis in the 3D uses the delta model, delta ticks and the library strike in the hover', () => {
  languages.impostaLinguaCorrente('en');
  const { model, q } = qBuild();
  const palette = { text: '#000', muted: '#555', line: '#ddd', accent: '#00f', violet: '#70f', warn: '#a50', card: '#fff', scale: S3Q.SCALE_VOL };
  const label = SKEWV.deltaCellLabel(q, model);
  const fig = S3Q.surfaceFigure(q.deltaModel, { axis: 'delta', expiry: QEXP[0].e, column: 2, palette, labels: { na: 'n/a', d: 'd', ivGrid: 'IV grid', strikeEq: 'K', hole: 'hole', partial: 'p' },
    xAxis: { title: 'Delta (forward)', ticktext: q.deltaHeads, cellLabel: label } });
  assert.deepEqual(plain(fig.layout.scene.xaxis.ticktext), q.deltaHeads);
  assert.deepEqual(plain(fig.layout.scene.xaxis.tickvals), [0, 1, 2, 3, 4]);
  const mesh = fig.traces[0];
  assert.ok(mesh.x.every(x => [0, 1, 2, 3, 4].includes(x)), 'delta model columns on x');
  const strike25c = q.delta.rows[0].cells['25C'].strike;
  assert.ok(mesh.text.some(t => t.includes('25ΔC · K ' + vd.priceText(strike25c, 'n/a'))), 'hover carries the library strike at 25ΔC');
  // sullo stesso modello K/S l'asse resta quello di prima (nessun xAxis = nessuna tacca forzata)
  const ks = S3Q.surfaceFigure(model, { axis: 'moneyness', expiry: null, column: null, palette, labels: { na: 'n/a', d: 'd' } });
  assert.equal(ks.layout.scene.xaxis.ticktext, undefined);
});

test('quant wiring: ΔIV with a previous download shows the library diff; without one it is declared n/a, never a fallback', () => {
  languages.impostaLinguaCorrente('it');
  const previous = qPrev();
  const { data, model, q } = qBuild({}, { previous });
  const vis = qVisible(data), T = vis.slices.map(s => s.t_years * 365);
  const lib = VQ.surfaceDiff(VQ.moneynessSurfaceFromBuilder(vis), VQ.moneynessSurfaceFromBuilder(previous), { tenorsDays: T });
  assert.equal(q.diffRange, Math.max(...lib.rows.flatMap(r => r.diff).filter(v => v != null).map(Math.abs)), 'scale = the largest |ΔIV| (review N10)');
  model.rows.forEach((row, j) => row.iv.forEach((_, i) => assert.equal(q.diffCells[j][i], lib.rows[j].diff[i], `diff ${j},${i}`)));
  assert.ok(q.diffCells.flat().some(v => v != null), 'some cells are comparable');
  assert.equal(q.diff.axis, 'K/S', 'previous without forwards: spot K/S, declared');
  // anche col precedente che porta i forward il ΔIV resta sulle colonne K/S disegnate (review: mai un K/F su colonne K/S)
  const prevF = { ...previous, forwards: Object.fromEntries(QEXP.map(x => [x.e, x.F - 0.4])) };
  const kf = qBuild({}, { previous: prevF });
  const libKF = VQ.surfaceDiff(VQ.moneynessSurfaceFromBuilder(vis), VQ.moneynessSurfaceFromBuilder(prevF), { tenorsDays: T });
  assert.equal(kf.q.diff.axis, 'K/S');
  model.rows.forEach((row, j) => row.iv.forEach((_, i) => {
    assert.equal(kf.q.diffCells[j][i], libKF.rows[j].diff[i], `K/S diff ${j},${i}`);
    assert.equal(kf.q.diffSlide[j][i], libKF.rows[j].skewSlide[i], `skew slide ${j},${i}`);
  }));
  const line = QUALV.diffLineOf(kf.q)(1, 4);
  assert.ok(/skew|scivolamento/.test(line) || line.includes('n.d.'), 'the cell reading names the skew slide: ' + line);
  const grid = renderToStaticMarkup(React.createElement(QGRID, { model, axis: 'moneyness', expiry: null, column: null, onPick() {}, onExpiry() {}, onColumn() {},
    extras: { diff: { cells: q.diffCells, range: q.diffRange, line: QUALV.diffLineOf(q) } } }));
  const shown = [...grid.matchAll(/data-vol-grid-diff="([^"]+)"/g)].map(m => m[1]);
  assert.equal(shown.length, model.filledCells);
  assert.deepEqual(shown.filter(v => v !== 'na').map(Number), q.diffCells.flat().filter((v, n) => model.rows.flatMap(r => r.iv)[n] != null && v != null));
  // senza download precedente: nessun diff, la vista lo dice, il 3D resta grigio senza scala
  const none = qBuild().q;
  assert.equal(none.diff, null); assert.equal(none.diffCells, null);
  const view = renderToStaticMarkup(React.createElement(QUALV.default, { q: none, model, expiry: null, column: null, onPick() {}, onExpiry() {}, onColumn() {},
    diff: { nowText: 'ora', prevText: null, persisted: true }, onShowDiff() {} }));
  assert.ok(view.includes('n.d.: nessun download precedente'), view);
  const palette = { text: '#000', muted: '#555', line: '#ddd', accent: '#00f', violet: '#70f', warn: '#a50', card: '#fff', scale: S3Q.SCALE_VOL };
  const fig = S3Q.surfaceFigure(model, { axis: 'moneyness', expiry: null, column: null, palette, labels: { na: 'n.d.', d: 'g' },
    color: { kind: 'diff', cells: model.rows.map(r => r.iv.map(() => null)), range: null, title: 'ΔIV', line: QUALV.diffLineOf(none) } });
  assert.ok(fig.traces[0].vertexcolor.every(c => c === S3Q.DIFF_NA), 'no previous: every vertex grey');
  assert.ok(!fig.traces.some(t => t.meta === 'diff-scale'), 'no previous: no diverging scale');
  assert.equal(fig.traces[0].intensity, undefined, 'the IV level is not used as a silent stand-in');
});

test('snapshot store: latest strictly earlier save of ANOTHER download; versioned schema; corrupt data never crashes; limit and clear', () => {
  const store = new Map();
  globalThis.localStorage = { getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v)), removeItem: k => store.delete(k) };
  try {
    SNAPS.__resetSnapshotMemory();
    const at = (d, id, extra = {}) => ({ ...qPayload(), snapshot_at: `2035-01-0${d}T21:00:00Z`, download_id: id, ...extra });
    const a = at(1, 'a'), b = at(2, 'b'), c = at(3, 'c');
    assert.equal(SNAPS.previousSnapshot('SYNQ', b.snapshot_at), null, 'nothing saved yet');
    assert.deepEqual(plain(SNAPS.saveSnapshot(a, 'SYNQ')), { saved: true, persisted: true });
    SNAPS.saveSnapshot(c, 'SYNQ'); SNAPS.saveSnapshot(b, 'SYNQ');
    assert.equal(SNAPS.previousSnapshot('SYNQ', c.snapshot_at).at, b.snapshot_at, 'the LATEST earlier one, not the oldest (review N7)');
    assert.equal(SNAPS.previousSnapshot('SYNQ', a.snapshot_at), null, 'a snapshot is never its own previous');
    assert.equal(SNAPS.previousSnapshot('OTHER', b.snapshot_at), null, 'another underlying never counts');
    // lo stesso download riletto piu' tardi non e' un precedente
    SNAPS.saveSnapshot(at(4, 'c'), 'SYNQ');
    assert.equal(SNAPS.previousSnapshot('SYNQ', '2035-01-05T21:00:00Z', 'c').at, b.snapshot_at, 'the same download id is excluded (review 5)');
    assert.equal(SNAPS.saveSnapshot({ ...b, snapshot_at: null, _timestamp: null }, 'SYNQ').saved, false, 'no time, no save');
    assert.equal(SNAPS.saveSnapshot({ ...b, snapshot_at: '2035-01-02T21:00:00' }, 'SYNQ').saved, false, 'a time without zone is not an instant');
    assert.equal(SNAPS.previousSnapshot('SYNQ', c.snapshot_at).slices.find(x => x.expiry === '2035-03-03').t_years, TY(QEXP[1]), 'the saved copy keeps t_years');
    // forward salvati: un nuovo salvataggio senza forward non li cancella (review N14)
    SNAPS.saveSnapshot(b, 'SYNQ', { '2035-03-03': 100.7 });
    SNAPS.saveSnapshot(b, 'SYNQ');
    assert.deepEqual(plain(SNAPS.previousSnapshot('SYNQ', c.snapshot_at).forwards), { '2035-03-03': 100.7 });
    // stesso contenuto = istantanea identica
    assert.equal(SNAPS.identicalContent(c, SNAPS.previousSnapshot('SYNQ', c.snapshot_at)), true, 'same grid and spot: identical');
    assert.equal(SNAPS.identicalContent({ ...c, spot_est: 101 }, SNAPS.previousSnapshot('SYNQ', c.snapshot_at)), false);
    // schema dentro i dati
    assert.equal(JSON.parse(store.get(SNAPS.SNAPSHOT_KEY)).v, SNAPS.SCHEMA_VERSION);
    // dati corrotti: nessun crash, voci scartate e contate (review 2)
    for (const bad of ['{not json', JSON.stringify([1, 2]), JSON.stringify({ v: 1, tickers: {} }),
      JSON.stringify({ v: 2, tickers: { SYNQ: [{ ticker: 'SYNQ', at: 'ieri' }, null, 7] , X: 'boh' } })]) {
      store.set(SNAPS.SNAPSHOT_KEY, bad); SNAPS.__resetSnapshotMemory();
      assert.equal(SNAPS.previousSnapshot('SYNQ', c.snapshot_at), null, 'corrupt archive: no previous, no crash: ' + bad);
      assert.ok(SNAPS.snapshotStoreStatus().discarded >= 1, 'discarded entries are counted: ' + bad);
      assert.equal(SNAPS.saveSnapshot(c, 'SYNQ').saved, true, 'saving over a corrupt archive works');
    }
    // limite globale: i sottostanti visti da piu' tempo escono per primi
    SNAPS.clearSnapshots();
    for (let i = 0; i < 12; i++) for (let d = 1; d <= 4; d++) SNAPS.saveSnapshot({ ...at(d, 'x' + i + d), ticker: 'T' + String(i).padStart(2, '0'),
      snapshot_at: `2035-0${1 + Math.floor(i / 4)}-0${d}T${String(10 + (i % 4)).padStart(2, '0')}:00:00Z` }, 'T' + i);
    const st = SNAPS.snapshotStoreStatus();
    assert.ok(st.total <= SNAPS.MAX_TOTAL && st.total > 0, 'global limit: ' + st.total);
    assert.equal(SNAPS.previousSnapshot('T00', '2036-01-01T00:00:00Z'), null, 'the oldest underlying was evicted first');
    assert.ok(SNAPS.previousSnapshot('T11', '2036-01-01T00:00:00Z'), 'the newest stays');
    assert.equal(SNAPS.clearSnapshots(), true);
    assert.equal(SNAPS.snapshotStoreStatus().total, 0, 'clear empties the archive');
  } finally { delete globalThis.localStorage; SNAPS.__resetSnapshotMemory(); }
});

test('quant wiring: arbitrage flags reach the grid, the 3D ring and the quality table in two levels, executable and indicative', () => {
  languages.impostaLinguaCorrente('en');
  // chain con le quote a 60 g, K = 110, gonfiate di 1: butterfly ESEGUIBILE sulle quote osservate, sulle stesse
  // celle dell'ala destra segnata dal calendario indicativo (una cella con due livelli: vince l'eseguibile)
  const bumped = qChains();
  bumped['2035-03-03'].chain = bumped['2035-03-03'].chain.map(c => c.strike === 110 ? { ...c, bid: c.bid + 1, ask: c.ask + 1, mid: c.mid + 1 } : c);
  const { data, model, q } = qBuild({ bend: 0.5 }, { chains: bumped });
  const lib = VQ.arbitrageChecks(VQ.surfaceFromBuilder(qVisible(data)), libDirect(data).fwd);
  assert.equal(q.flags.filter(f => f.set === 'grid').length, lib.flags.length);
  // la fetta a 1 giorno (esclusa dal modello) non entra nei controlli, nemmeno come «saltata» (review N11)
  assert.ok([...q.arb.checked.expiries, ...q.arb.skipped.map(x => x.expiry)].every(e => model.rows.some(r => r.expiry === e)), JSON.stringify(q.arb.skipped));
  const cal = q.flags.find(f => f.flag.kind === 'calendar');
  assert.ok(cal, 'the bent wing is a calendar flag: ' + JSON.stringify(lib.flags.map(f => f.kind)));
  assert.ok(cal.cells.length > 0 && cal.cells.every(c => model.grid[c.col] >= 1.05), 'the calendar mark sits on the right wing');
  // livelli: il calendario e' di modello = indicativo; la quota gonfiata e' un arbitraggio eseguibile
  assert.equal(cal.level, 'indicative');
  const exec = q.flags.filter(f => f.level === 'executable');
  assert.ok(exec.length >= 1 && exec.every(f => f.flag.test === 'executable' && f.set === 'observed'), 'executable flags come from bid/ask: ' + JSON.stringify(q.flags.map(f => [f.level, f.flag.kind, f.flag.test])));
  assert.equal(q.flags[0].level, 'executable', 'executable flags are listed first');
  assert.equal(q.nExecutable, exec.length); assert.equal(q.nIndicative, q.flags.length - exec.length);
  // libreria v3: `indicative` marca i segnali di modello su nodi quotati; il livello eseguibile resta solo del test su bid/ask
  assert.equal(QUANT.levelOf({ ...exec[0].flag, indicative: true }), 'indicative', 'caution wins');
  assert.equal(QUANT.levelOf({ ...cal.flag, indicative: false }), 'indicative', 'a model calendar is never executable');
  assert.ok(q.flags.filter(f => f.flag.indicative).every(f => f.level === 'indicative' && QTEXT.flagLine(f).includes('quotes exist')));
  const flagMap = new Map([...q.cellFlags].map(([k, ids]) => [k, ids.map(i => QTEXT.flagLine(q.flags[i]))]));
  const grid = renderToStaticMarkup(React.createElement(QGRID, { model, axis: 'moneyness', expiry: null, column: null, onPick() {}, onExpiry() {}, onColumn() {},
    extras: { flags: flagMap, flagLevel: q.cellLevel } }));
  const shownCells = [...q.cellFlags.keys()].filter(k => { const [e, c] = k.split('|'); return model.rows.find(r => r.expiry === e).iv[+c] != null; });
  assert.equal((grid.match(/data-vol-grid-flag=/g) || []).length, shownCells.length);
  assert.equal((grid.match(/data-vol-grid-flag-level="executable"/g) || []).length, shownCells.filter(k => q.cellLevel.get(k) === 'executable').length);
  const nExecCells = shownCells.filter(k => q.cellLevel.get(k) === 'executable').length;
  assert.ok(nExecCells >= 1 && nExecCells < shownCells.length, 'both levels on the grid');
  assert.equal((grid.match(/is-flag-exec/g) || []).length, nExecCells, 'strong mark = executable cells');
  assert.equal((grid.match(/is-flag-ind/g) || []).length, shownCells.length - nExecCells, 'faint mark = indicative cells');
  for (const [k, ids] of q.cellFlags) assert.equal(q.cellLevel.get(k), ids.some(i => q.flags[i].level === 'executable') ? 'executable' : 'indicative', 'executable wins in ' + k);
  assert.equal([...q.cellFlags.values()].reduce((a, ids) => a + ids.length, 0), q.flags.reduce((a, f) => a + f.cells.length, 0));
  assert.ok(q.flags.every(f => f.cells.length <= f.flag.expiries.length), 'one mark per flag and expiry, never a row of rings');
  // alla giunzione put/call anche un test su bid/ask resta indicativo
  assert.equal(QUANT.levelOf({ ...exec[0].flag, origin: 'junction' }), 'indicative');
  assert.ok(grid.includes('Calendar'), 'the cell title names the type');
  // in ogni lettura «arbitraggio» porta il livello accanto
  for (const f of q.flags) assert.ok(QTEXT.flagLine(f).startsWith(f.level === 'executable' ? 'Executable arbitrage (bid/ask)' : 'Indicative: not executable within the spread'), QTEXT.flagLine(f));
  const traces = OVER.overlayTraces(model, q, { forward: false, cone: false, earnings: false, arb: true }, { forward: '#a', cone: '#b', earnings: '#c', arb: '#d', arbInd: '#f', muted: '#e' }, null, false)(
    { xs: model.grid, ys: model.rows.map(r => Math.sqrt(r.days)), floor: 10, zTop: 30, strikeAxis: false });
  const ringE = traces.find(t => t.meta === 'ov-arb-exec'), ringI = traces.find(t => t.meta === 'ov-arb-ind');
  assert.ok(ringE && ringI, '3D: two ring traces');
  assert.equal(ringE.x.length + ringI.x.length, shownCells.length, '3D: one ring per flagged cell');
  assert.equal(ringE.x.length, shownCells.filter(k => q.cellLevel.get(k) === 'executable').length);
  assert.ok(ringE.marker.size > ringI.marker.size && ringI.marker.opacity < 1 && ringE.marker.color === '#d' && ringI.marker.color === '#f', 'executable strong, indicative faint');
  const view = renderToStaticMarkup(React.createElement(QUALV.default, { q, model, expiry: null, column: null, onPick() {}, onExpiry() {}, onColumn() {},
    diff: { nowText: 'now', prevText: null, persisted: true }, onShowDiff() {} }));
  assert.equal((view.match(/data-vol-flag-row=/g) || []).length, q.flags.length);
  assert.equal((view.match(/data-vol-flag-level="executable"/g) || []).length, q.nExecutable);
  assert.ok(view.includes(`data-vol-flags-exec="true">${q.nExecutable}<`) && view.includes(`data-vol-flags-ind="true">${q.nIndicative}<`), 'the two levels are counted apart');
  assert.equal((view.match(/class="fat-pill is-(bad|ind)"/g) || []).length, q.flags.length, 'every row carries its level');
  assert.ok(view.includes(QTEXT.magnitudeText(cal.flag, cal.level)) && view.includes(QTEXT.originText(cal.flag.origin)), 'size and origin shown');
  for (const f of q.flags) if (f.level === 'indicative') assert.ok(!QTEXT.flagLine(f).includes('executable arbitrage of') && !QTEXT.flagLine(f).includes('certain on the quotes'), 'an indicative flag never reads as executable: ' + QTEXT.flagLine(f));
  assert.ok(Math.abs(cal.ratio - cal.flag.magnitude / cal.flag.tolerance) < 1e-12, 'severity = size over the tolerance in the same unit (library v2)');
  const chip = renderToStaticMarkup(React.createElement(QUALV.QualitySummary, { q, onOpen() {} }));
  assert.ok(chip.includes(`${q.nExecutable} executable arbitrages · ${q.nIndicative} indicative`) && chip.includes('is-bad'), chip);
  // superficie pulita: nessun flag, la vista lo dice
  const cleanQ = qBuild().q; assert.equal(cleanQ.flags.length, 0, "the clean synthetic surface raises no flag: " + JSON.stringify(cleanQ.flags.map(f => [f.set, f.flag.kind, f.flag.test, f.flag.origin, f.flag.expiries])));
});

test('quant wiring: forward line and ±1σ cone on the 3D sit at the library forward and cone, the term chart at the library forward vol', () => {
  languages.impostaLinguaCorrente('en');
  const { model, q } = qBuild();
  const traces = OVER.overlayTraces(model, q, { forward: true, cone: true, earnings: false, arb: false }, { forward: '#a', cone: '#b', earnings: '#c', arb: '#d', muted: '#e' }, null, false)(
    { xs: model.grid, ys: model.rows.map(r => Math.sqrt(r.days)), floor: 10, zTop: 30, strikeAxis: false });
  const fwd = traces.find(t => t.meta === 'ov-forward');
  assert.ok(fwd, 'forward trace present');
  const libF = libDirect(qPayload()).fwd;
  QEXP.forEach((x, j) => assert.ok(Math.abs(fwd.x[j] - libF[x.e].forward / 100) < 1e-12, `F/S ${x.e}`));
  assert.ok(fwd.text[1].includes('F ' + vd.priceText(libF[QEXP[1].e].forward, '')) && fwd.text[1].includes('de-americanised'), fwd.text[1]);
  // oracolo indipendente da quant.ts: termine = ATM del payload sul suo t_years
  const L = libDirect(qPayload()), libTerm = L.term;
  const cone = VQ.expectedMoveCone(L.fwd, libTerm);
  const low = traces.find(t => t.meta === 'ov-cone-low'), high = traces.find(t => t.meta === 'ov-cone-high');
  assert.ok(low && high, 'cone traces present');
  cone.forEach((r, j) => { if (low.x[j] != null) assert.ok(Math.abs(low.x[j] - r.low / 100) < 1e-12 && Math.abs(high.x[j] - r.high / 100) < 1e-12, 'cone ' + r.expiry); });
  assert.ok(low.z.every(z => z == null || z === 10), 'the cone lies on the floor');
  // strike axis: same points in price
  const s = OVER.overlayTraces(model, q, { forward: true, cone: false, earnings: false, arb: false }, { forward: '#a', cone: '#b', earnings: '#c', arb: '#d', muted: '#e' }, null, false)(
    { xs: model.strikes, ys: model.rows.map(r => Math.sqrt(r.days)), floor: 10, zTop: 30, strikeAxis: true });
  assert.ok(Math.abs(s[0].x[0] - libF[QEXP[0].e].forward) < 1e-9, 'strike axis: the forward in price');
  const fv = VQ.forwardVol(libTerm);
  const marks = OVER.termMarks(model, q, null, { fwd: true, earnings: true });
  assert.deepEqual(plain(marks.fwd.map(m => m.v)), plain(fv.map(p => p.forwardVol)));
  assert.equal(marks.earnings, null, 'no earnings date, no earnings mark');
  // nessuna chain: forward n.d. col motivo, nessun punto inventato dallo spot
  const nochain = qBuild({}, { chains: {} }).q;
  assert.equal(nochain.forwardOf(QEXP[0].e).F, null); assert.equal(nochain.forwardOf(QEXP[0].e).why, 'no_download');
  const none = OVER.overlayTraces(model, nochain, { forward: true, cone: true, earnings: false, arb: false }, { forward: '#a', cone: '#b', earnings: '#c', arb: '#d', muted: '#e' }, null, false)(
    { xs: model.grid, ys: model.rows.map(r => Math.sqrt(r.days)), floor: 10, zTop: 30, strikeAxis: false });
  assert.deepEqual(none, [], 'without a forward neither line nor cone is drawn');
});

test('quant wiring: earnings n/a when the date is missing; with a date the event vol is the library one', () => {
  languages.impostaLinguaCorrente('it');
  const { model, q } = qBuild();
  assert.equal(q.event, null, 'no date in the payload: no event computed');
  assert.equal(OVER.eventLine(q, null), 'Utili: n.d. — nessuna data nel payload (arriva col contesto)');
  const view = quiet(() => renderToStaticMarkup(React.createElement(TERMV, { q, model, column: 4, expiry: null, onExpiry() {}, earnings: null })));
  assert.ok(view.includes('data-vol-event-na') && view.includes('nessuna data nel payload'), 'the event box declares n/a');
  const noTrace = OVER.overlayTraces(model, q, { forward: false, cone: false, earnings: true, arb: false }, { forward: '#a', cone: '#b', earnings: '#c', arb: '#d', muted: '#e' }, null, false)(
    { xs: model.grid, ys: model.rows.map(r => Math.sqrt(r.days)), floor: 10, zTop: 30, strikeAxis: false });
  assert.equal(noTrace.length, 0, 'no date, no earnings line on the 3D');
  const withDate = qBuild({ earnings: '2035-02-15' });
  const ev = VQ.eventVol(libDirect(withDate.data, { earnings: '2035-02-15' }).term, '2035-02-15', '2035-01-02');
  assert.deepEqual(plain(withDate.q.event), plain(ev));
  const v2 = quiet(() => renderToStaticMarkup(React.createElement(TERMV, { q: withDate.q, model: withDate.model, column: 4, expiry: null, onExpiry() {}, earnings: '2035-02-15' })));
  assert.ok(ev.eventMove != null, 'the synthetic event premium is measurable: ' + ev.reason);
  assert.ok(v2.includes('data-vol-event-move="true">±' + vd.numText(ev.eventMove * 100, 2) + '%<'), 'day move shown in its cell');
  assert.ok(v2.includes('data-vol-event-vol="true">' + vd.ivText(ev.eventVolAnnualized, '', 1) + '<'), 'annualised event vol shown in its cell');
  // l'evento senza premio resta n.d. col suo motivo
  const flat = qBuild({ earnings: '2035-02-15', premium: false });
  const v3 = quiet(() => renderToStaticMarkup(React.createElement(TERMV, { q: flat.q, model: flat.model, column: 4, expiry: null, onExpiry() {}, earnings: '2035-02-15' })));
  assert.ok(flat.q.event.eventMove == null && v3.includes(QTEXT.whyText(flat.q.event.reason).replace(/'/g, '&#x27;')), 'event n/a reason shown: ' + flat.q.event.reason);
});

test('sub-pages: one per question, state in the URL (vista=…), unknown or missing = Surface, other params kept', () => {
  const SP = load('pages/voldeck/subpages.ts');
  assert.deepEqual(plain(SP.SUBPAGES), ['superficie', 'griglia', 'skew', 'termine', 'coerenza', 'variazioni', 'contesto', 'realizzata']);
  assert.equal(SP.parseSubpage(null), 'superficie'); assert.equal(SP.parseSubpage('boh'), 'superficie');
  for (const p of SP.SUBPAGES) assert.equal(SP.parseSubpage(p), p);
  const next = SP.withSubpage(new URLSearchParams('t=SYNQ&vista=skew'), 'termine');
  assert.equal(next.get('vista'), 'termine'); assert.equal(next.get('t'), 'SYNQ');
  const src = require('node:fs').readFileSync(require('node:path').join(SRC, 'pages/voldeck/subpages.ts'), 'utf8');
  assert.ok(!/replace:\s*true/.test(src), 'a sub-page change is a history entry (back works), never a replace');
});

test('Termine: the curve and its tooltip read the library ATMF, never the builder spot ATM labelled as ATMF (review R12)', async () => {
  languages.impostaLinguaCorrente('en');
  const { model, q } = qBuild();
  const last = model.rows[model.rows.length - 1];
  const atmf = vd.ivText(q.atmf[last.expiry], '', 2), spot = vd.ivText(last.atm, '', 2);
  assert.notEqual(atmf, spot, 'the fixture tells the two ATMs apart');
  assert.deepEqual(plain(load('pages/voldeck/TermView.tsx').termModelOf(model, q).rows.map(r => r.atm)), plain(model.rows.map(r => q.atmf[r.expiry])));
  const view = await mount(React.createElement(TERMV, { q, model, column: 4, expiry: null, onExpiry() {}, earnings: null }));
  try {
    const chart = view.container.byAttr('data-vol-term')[0];
    await React.act(async () => { dispatch(view.container, chart, 'pointermove', { clientX: 54 + 728 - 1, clientY: 80 }); });
    const lines = chart.byClass('vdn-tip')[0].childNodes.map(n => n.textContent);
    const atmLine = lines.find(l => l.startsWith('ATMF'));
    assert.ok(atmLine && atmLine.includes(atmf) && !atmLine.includes(spot), 'tooltip ATMF line: ' + lines.join(' | '));
  } finally { await view.unmount(); }
});
