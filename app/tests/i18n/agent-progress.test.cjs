const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react'), JSX = require('react/jsx-runtime');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');
ambienteBrowser();
const point = (id, rate) => ({ run_id: `memo:${id}`, memo_id: id, completed_at: '2026-09-12T12:00:00Z', computed_at: '2026-09-12T11:00:00Z',
  n: 20, hits: rate / 5, hit_rate_pct: rate, avg_edge_pct: 2.5, ci95: { low_pct: 35, high_pct: 80 }, quality: ['ok'], comparison_key: null,
  source: 'synthetic scorekeeper', window_days: 270, maturation_days: 7, n_fetch_fail: 0, n_unmeasurable: 3, n_directional_candidates: 23,
  delta: { available: false, hit_rate_pp: null, reason: 'Original comparison reason' }, operational: { status: 'done', models: ['synthetic-model'], usage: { cost_eur: 0, duration_s: 2, api_calls: 1, status: 'ok', partial: false } } });
function fixture() {
  const points = [point(1, 50), point(2, 65)];
  return { source: 'synthetic SQLite', paid_analysis: false, history: { state: 'ok', count: 2, available: true, first_captured_at: '2026-09-01T10:00:00Z', note: 'Original history note' },
    trend: { available: true, reason: null }, current_scorecard: { available: true, computed_at: '2026-09-12T13:00:00Z', stato: 'ok', error: null },
    agents: [{ id: 'capo', label: 'Synthetic Committee', role: 'Original role', attribution: 'collective', latest: points[1], series: points, delta: points[1].delta },
      { id: 'red_team', label: 'Red Team', role: 'Original control role', attribution: 'unsupported', latest: null, series: [], delta: points[1].delta }],
    runs: points.map(p => ({ ...p, captured_at: p.completed_at, score_error: null, output_language: 'it', reflection: { text: 'Lezione originale 158,50 '.repeat(80), status: 'generated', kind: 'suggestion', implementation_verified: p.memo_id === 2, performance_proven: false } })),
    method: { score: 'Original score method', attribution: 'Original attribution', horizon: 'Original horizon', comparison: 'Original comparison', learning: 'Original learning method', timing: 'Original timing' } };
}
function retained(data = fixture(), failure = false) {
  let si = 0, mi = 0, ei = 0;
  const states = [], memos = [], effects = [], deps = [], nodes = [], calls = [];
  const remember = (fn, d) => { const at = mi++, before = memos[at]; if (!before || !d || d.some((v, i) => !Object.is(v, before.d[i]))) memos[at] = { d, value: fn() }; return memos[at].value; };
  const jsx = name => (type, props, ...args) => { nodes.push({ type, props }); return JSX[name](type, props, ...args); };
  const load = creaCaricatore({ stub: { react: { ...React, useMemo: remember, useCallback: (fn, d) => remember(() => fn, d),
    useState(initial) { const at = si++; if (!(at in states)) states[at] = typeof initial === 'function' ? initial() : initial; return [states[at], v => { states[at] = typeof v === 'function' ? v(states[at]) : v; }]; },
    useEffect(fn, d) { const at = ei++, before = deps[at]; if (!before || !d || d.some((v, i) => !Object.is(v, before[i]))) effects.push(fn); deps[at] = d; },
  }, 'react/jsx-runtime': { ...JSX, jsx: jsx('jsx'), jsxs: jsx('jsxs') }, '@/lib/useBox': { useBox: () => [null, { w: 900, h: 280 }] },
  './api': { API_BASE: 'http://synthetic.invalid', requestHeaders: () => ({ 'X-BB-Language': language.linguaCorrente() }), clearSessionAndReload() {} } } });
  const language = load('i18n/lingua.ts'), Page = load('pages/AgentProgressPage.tsx').default;
  globalThis.fetch = async (url, options) => { calls.push([url, options]); return { ok: !failure, status: failure ? 503 : 200, json: async () => failure ? { detail: [{ msg: 'Original archive failure' }] } : data }; };
  const render = lang => { si = mi = ei = 0; effects.length = nodes.length = 0; language.impostaLinguaCorrente(lang); return renderToStaticMarkup(React.createElement(Page)); };
  const settle = async () => { for (const fn of effects.splice(0)) fn(); await new Promise(resolve => setImmediate(resolve)); };
  const ready = async lang => { render(lang); await settle(); return render(lang); };
  return { render, ready, settle, nodes, calls };
}
test('evidence layout is bilingual with preserved uncertainty, original lesson and one read across language changes', async () => {
  const ui = retained(); const it = await ui.ready('it'), en = ui.render('en'); await ui.settle();
  assert.match(it, /Risultati con evidenza/); assert.match(en, /Results with evidence/); assert.doesNotMatch(en, /<main/);
  assert.match(it, /65,0%/); assert.match(en, /65\.0%/); assert.match(en, /35\.0–80\.0%/);
  assert.match(en, /Implementation: verified/); assert.match(en, /Performance effect: unproven/); assert.match(en, /Original output · Italian/);
  assert.match(en, /Lezione originale 158,50/); assert.equal(ui.calls.length, 1);
  assert.equal(ui.calls[0][1].headers['X-BB-Language'], 'it');
});
// 13/09 (Claude Opus 5): frasi attese scritte qui, non lette dal catalogo sotto prova.
test('the hit rate sample count keeps the Italian jargon and takes the English plural', async () => {
  const ui = retained(); const it = await ui.ready('it'), en = ui.render('en');
  assert.match(it, /<small>13 esiti corretti \/ 20 call<\/small>/);
  assert.match(en, /<small>13 correct outcomes \/ 20 calls<\/small>/);
});
test('keyboard selection preserves exact run and comparison gates, and the control role gets no invented score', async () => {
  const ui = retained(); await ui.ready('en'); const circles = ui.nodes.filter(n => n.type === 'circle' && n.props.role === 'button');
  let prevented = 0; circles[0].props.onKeyDown({ key: 'Enter', preventDefault() { prevented++; } }); const old = ui.render('en');
  assert.equal(prevented, 1); assert.match(old, /Implementation: unverified/); assert.match(old, /Original comparison reason/);
  assert.equal(ui.nodes.filter(n => n.type === 'line' && n.props.className === 'ap-series').length, 0);
  ui.nodes.find(n => n.type === 'button' && n.props.className?.startsWith('ap-agent ') && n.props.children[0].props.children === 'Red Team').props.onClick();
  const control = ui.render('en'); assert.match(control, /No independent directional score/); assert.doesNotMatch(control, /class="ap-metrics"/);
});
test('HTTP structured failures and an empty history remain declared without synthesizing past runs', async () => {
  const bad = retained(fixture(), true), error = await bad.ready('en'); assert.match(error, /Original archive failure/); assert.match(error, /Progress unavailable/);
  const empty = fixture(); empty.history = { ...empty.history, count: 0, available: false }; empty.runs = []; empty.agents.forEach(a => { a.series = []; a.latest = null; });
  const ui = retained(empty); const en = await ui.ready('en'); assert.match(en, /History begins here/); assert.match(en, /No measurable history point/);
});

test('current measurement, duplicate review and archived evidence remain distinct across tabs and languages', async () => {
  const data = fixture(); data.agents[0].current = { ...point(null, 75), run_id: null, memo_id: null, delta: null };
  const run = data.runs[1]; run.review_status = 'duplicate'; run.review_note = 'Original duplicate evidence';
  run.scorecard = { degraded: false, by_action: { BUY: { n: 2, hits: 1, hit_rate_pct: 50, avg_edge_pct: 1, small_sample: true } },
    by_confidence: { ALTA: { n: 1, hits: 1, hit_rate_pct: 100, avg_edge_pct: 2 } },
    by_confidence_scartate: { n: 1, etichette: { 'MEDIUM-HIGH': 1 }, motivo: 'Original exclusion reason' },
    details: [{ id: 11, ticker: 'SYNTH', action: 'BUY', date: '2026-09-01', confidence: 'HIGH', confidence_bucket: 'ALTA',
      direction: 'long', edge_pct: 2, hit: true, horizon_used: '4w', ret_1w_pct: 1, ret_4w_pct: 2, specialists: ['synthetic'], status: 'open' }] };
  data._presentation_v1 = { version: 1, texts: [{ path: ['agents', 0, 'role'], it: 'Ruolo sintetico', en: 'Synthetic role' }] };
  data.agents[0].role = 'Ruolo sintetico';
  const before = JSON.stringify(data); const ui = retained(data); await ui.ready('it');
  ui.nodes.find(n => n.type === 'select').props.onChange({ target: { value: 'current' } });
  let html = ui.render('en'); assert.match(html, /75\.0%/); assert.match(html, /Synthetic role/); assert.doesNotMatch(html, /Original duplicate evidence/);
  ui.nodes.find(n => n.props.id === 'ap-tab-action').props.onClick(); html = ui.render('en'); assert.match(html, /Breakdown unavailable/);
  ui.nodes.find(n => n.type === 'select').props.onChange({ target: { value: 'memo:2' } }); html = ui.render('en');
  assert.match(html, /Original duplicate evidence/); assert.match(html, /Small sample/); assert.match(html, /50\.0%/);
  ui.nodes.find(n => n.props.id === 'ap-tab-confidence').props.onClick(); html = ui.render('en');
  assert.match(html, /MEDIUM-HIGH/); assert.match(html, /Original exclusion reason/);
  ui.nodes.find(n => n.props.id === 'ap-tab-calls').props.onClick(); html = ui.render('it');
  assert.match(html, /SYNTH/); assert.match(html, /HIGH/); assert.match(html, /ALTA/); assert.match(html, /2,0%/);
  assert.equal(ui.calls.length, 1); assert.equal(JSON.stringify(data), before);
});

test('history chart gives an exact reading under the pointer and on keyboard focus, declares gaps and never changes the selection', async () => {
  // Chart box from the useBox stub: w=900, left=48, right=30, so run 0 sits at x=48 and run 1 at x=870.
  const data = fixture(); data.agents[0].series = [{ ...data.agents[0].series[0], hit_rate_pct: null, ci95: null, quality: ['missing'] }, data.agents[0].series[1]];
  const ui = retained(data); await ui.ready('en');
  const chart = () => ui.nodes.find(n => n.type === 'svg' && n.props.role === 'img' && /Historical hit rate/.test(n.props['aria-label']));
  const reading = () => ui.nodes.filter(n => n.props?.className === 'ap-chart-reading').map(n => n.props.children);
  const crosshair = () => ui.nodes.filter(n => n.type === 'line' && n.props.pointerEvents === 'none').map(n => [n.props.x1, n.props.x2]);
  const circles = () => ui.nodes.filter(n => n.type === 'circle' && n.props.role === 'button');
  const selection = () => ui.nodes.find(n => n.type === 'select').props.value;
  const at = px => ({ clientX: 100 + px, currentTarget: { getBoundingClientRect: () => ({ left: 100 }) } });
  assert.ok(chart(), 'history chart rendered'); assert.deepEqual(reading(), [], 'no reading before pointer or focus reach the chart');
  assert.equal(typeof chart()?.props.onPointerMove, 'function', 'the chart listens to the pointer');
  chart().props.onPointerMove(at(860)); let html = ui.render('en');
  assert.equal(reading().length, 1);
  assert.match(reading()[0], /^Memo 2 · 65\.0% · n=20 · measured .+ · 95% interval 35\.0–80\.0% · Measurement available$/);
  assert.match(html, /class="ap-chart-reading">Memo 2 · 65\.0%/); assert.deepEqual(crosshair(), [[870, 870]]); assert.equal(selection(), '');
  chart().props.onPointerMove(at(40)); ui.render('en');
  assert.match(reading()[0], /^Memo 1 · n\/a · n=20 · measured .+ · 95% interval n\/a · Measurement missing$/); assert.deepEqual(crosshair(), [[48, 48]]);
  chart().props.onPointerMove(at(870)); ui.render('it');
  assert.match(reading()[0], /^Memo 2 .* 65,0% .* · Intervallo al 95% 35,0–80,0% · Misura disponibile$/);
  ui.render('en'); assert.match(reading()[0], /^Memo 2 · 65\.0% /);
  chart().props.onPointerLeave(); ui.render('en'); assert.deepEqual(reading(), []); assert.deepEqual(crosshair(), []);
  assert.equal(typeof circles()[1].props.onFocus, 'function', 'points expose the reading to keyboard focus');
  circles()[1].props.onFocus(); ui.render('en'); assert.match(reading()[0], /^Memo 2 · 65\.0% · n=20 /);
  let prevented = 0; const focused = [];
  circles()[1].props.onKeyDown({ key: 'ArrowLeft', preventDefault() { prevented++; },
    currentTarget: { ownerSVGElement: { querySelectorAll: () => [0, 1].map(i => ({ focus() { focused.push(i); } })) } } });
  ui.render('en'); assert.equal(prevented, 1); assert.deepEqual(focused, [0]);
  assert.match(reading()[0], /^Memo 1 · n\/a /); assert.deepEqual(crosshair(), [[48, 48]]); assert.equal(selection(), '');
  circles()[0].props.onBlur(); ui.render('en'); assert.deepEqual(reading(), []);
  assert.equal(ui.calls.length, 1);
});
