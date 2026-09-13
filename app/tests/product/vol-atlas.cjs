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
    assert.ok(html.includes(language === 'it' ? 'Disegna la strategia' : 'Design the strategy'));
    assert.ok(!html.includes('⟦'), 'no missing translation marker');
    assert.equal(numbers.numericText(12.5), language === 'it' ? '12,5' : '12.5');
  } } finally { global.fetch = previousFetch; }
  assert.equal(requests, 0);
});

test('actual 3D callback preserves surface geometry, holes, ATM ridge, camera and interactive config', async () => {
  const filename = path.resolve(__dirname, '../../src/pages/VolSurfacePage.tsx');
  const code = fs.readFileSync(filename, 'utf8');
  const source = ts.createSourceFile(filename, code, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let callback, percentile;
  function visit(node) {
    if (ts.isFunctionDeclaration(node) && node.name?.text === 'pctile') percentile = node.getText(source);
    if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)
      && node.expression.name.text === 'then' && node.expression.expression.getText(source) === 'loadPlotly()') callback = node.arguments[0].getText(source);
    ts.forEachChild(node, visit);
  }
  visit(source); assert.ok(callback && percentile);
  const surface = fixture(); surface.slices[0].iv_grid = [.2, null, 2.5];
  const ref = {}, calls = [];
  const sandbox = { exports: {}, active: true, plotRef: { current: ref }, data: surface,
    tr: translate.t, Math, Number, isFinite };
  vm.runInNewContext(ts.transpileModule(percentile + '\nexports.run = ' + callback, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, sandbox);
  for (const language of ['it', 'en']) {
    languages.impostaLinguaCorrente(language);
    await sandbox.exports.run({ newPlot: (...args) => { calls.push(args); return Promise.resolve(); } });
    const [node, traces, layout, config] = calls.at(-1);
    assert.equal(node, ref); assert.equal(traces[0].type, 'surface');
    assert.equal(traces[0].connectgaps, false); assert.equal(traces[0].z[0][1], null);
    assert.equal(traces[0].z[0][2], 250, 'outlier geometry never clipped to the colour cap');
    assert.equal(traces[1].type, 'scatter3d'); assert.equal(traces[1].z[0], 22, 'existing ATM fallback retained');
    assert.deepEqual(JSON.parse(JSON.stringify(traces[0].y)), [10, 40, 70]);
    assert.deepEqual(JSON.parse(JSON.stringify(layout.scene.camera)), { eye: { x: -1.75, y: -1.45, z: .55 } });
    assert.equal(layout.height, 480); assert.equal(config.responsive, true); assert.equal(config.displayModeBar, false);
    assert.ok(traces[0].hovertemplate.includes(language === 'it' ? 'giorni' : 'days'));
    assert.ok(traces[0].hovertemplate.includes('%{y}'));
  }
  const raw = JSON.parse(JSON.stringify(calls[0][1]));
  const en = JSON.parse(JSON.stringify(calls[1][1]));
  raw.forEach(trace => delete trace.hovertemplate); en.forEach(trace => delete trace.hovertemplate);
  assert.deepEqual(raw, en, 'language cannot alter any trace numeric value or interaction setting');
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
  const scope = { exports: {}, React, useState: React.useState, useRef: React.useRef, tr: translate.t, t: translate.t };
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

test('day units on the smile, heat map, forward ladder and cone follow the language', () => {
  const { SmileXray, HeatTopDown, FwdVolLadder } = withInternals('pages/VolSurfacePage.tsx', ['SmileXray', 'HeatTopDown', 'FwdVolLadder']);
  const grid = [.9, 1, 1.1];
  const slices = [{ expiry: '2035-02-10', days: 34, iv_grid: [.25, .22, .24] }, { expiry: '2035-03-10', days: 62, iv_grid: [.26, .23, .25] }];
  const term = slices.map(s => ({ expiry: s.expiry, days: s.days, atm_iv: s.iv_grid[1] }));
  const band = (days, current, young) => ({ window: days, current, min: current - .05, p25: current - .02, p50: current,
    p75: current + .02, max: current + .05, n_obs: young ? 40 : 250, young });
  const cone = { realized: { windows: [band(5, .3), band(21, .28), band(63, .25, true)] }, implied: {},
    confronto: [{ expiry: '2035-02-10', days: 34, window: 21, atm_iv: .251, pct_realized_leq_iv: 62 }] };
  const words = {
    it: { smile: 'FRONT 2035-02-10 · 34g', heat: '10/02 · 34g', ladder: '34g→62g', tick: '>21g</text>', young: '>63g*</text>',
      reading: 'pct realized 21g' },
    en: { smile: 'FRONT 2035-02-10 · 34d', heat: '10/02 · 34d', ladder: '34d→62d', tick: '>21d</text>', young: '>63d*</text>',
      reading: 'realised percentile 21d' },
  };
  const render = conePanel();
  for (const language of ['it', 'en']) {
    languages.impostaLinguaCorrente(language);
    const w = words[language];
    const smile = quietLayoutEffect(() => renderToStaticMarkup(React.createElement(SmileXray, { grid, slices, spot: 100 })));
    assert.ok(smile.includes(w.smile), `${language}: front label ${w.smile}: ${smile}`);
    const heat = renderToStaticMarkup(React.createElement(HeatTopDown, { grid, slices }));
    assert.ok(heat.includes(w.heat), `${language}: heat row ${w.heat}: ${heat}`);
    const ladder = renderToStaticMarkup(React.createElement(FwdVolLadder, { term }));
    assert.ok(ladder.includes(w.ladder), `${language}: forward ladder ${w.ladder}: ${ladder}`);
    const drawn = render('loaded', cone);
    for (const key of ['tick', 'young', 'reading']) assert.ok(drawn.includes(w[key]), `${language}: cone ${key} ${w[key]}`);
    for (const html of [smile, heat, ladder, drawn]) assert.ok(!html.includes('⟦'), 'no missing translation marker');
  }
});

test('earnings, expiry header and E legends of the tools view are written in the chosen language', () => {
  const { VStat } = withInternals('pages/VolSurfacePage.tsx', ['VStat']);
  const page = 'pages/VolSurfacePage.tsx';
  const earnings = fragment(page, 'VStat', '<VStat', 'rawData.next_earnings ||');
  const projector = fragment(page, 'div', 'className="vsxleg"', 'ui_band_spot_atm_iv_t_solid_1_dashed_2_horizontal_axis_in_76');
  const termLegend = fragment(page, 'div', 'className="vsxleg"', 'ui_horizontal_axis_in_t_hover_nodes_for_expiry_and_exact__87');
  const header = fragment(page, 'thead', '<thead', 'ui_days_97');
  const words = {
    it: { label: '>Risultati</div>', legend: 'E = risultati</span>', expiry: '>Scadenza</th>', foreign: ['Earnings', 'earnings', 'Expiry'] },
    en: { label: '>Earnings</div>', legend: 'E = earnings</span>', expiry: '>Expiry</th>', foreign: ['Risultati', 'risultati', 'Scadenza'] },
  };
  for (const language of ['it', 'en']) {
    languages.impostaLinguaCorrente(language);
    const w = words[language];
    const stat = earnings({ VStat, t: translate.t, rawData: { next_earnings: '2035-02-20' }, contextState: 'loaded' });
    assert.ok(stat.includes(w.label) && stat.includes('2035-02-20'), `${language}: earnings label: ${stat}`);
    const legends = [projector({ tr: translate.t }), termLegend({ tr: translate.t })];
    // the projector legend keeps its leading space inside the span, the term legend has it outside
    assert.ok(legends[0].includes('#B97A00"> ' + w.legend), `${language}: projector E legend: ${legends[0]}`);
    assert.ok(legends[1].includes('#B97A00">' + w.legend), `${language}: term E legend: ${legends[1]}`);
    const head = header({ tr: translate.t });
    assert.ok(head.includes(w.expiry), `${language}: expiry header: ${head}`);
    for (const html of [stat, ...legends, head]) {
      for (const word of w.foreign) assert.ok(!html.includes(word), `${language}: ${word} leaked into ${html}`);
      assert.ok(!html.includes('⟦'), 'no missing translation marker');
    }
  }
});

test('laboratory labels and numeric error labels say the same field in both languages', async () => {
  const { StrategyLab, Datum } = withInternals('components/VolWorkbench.tsx', ['StrategyLab', 'Datum']);
  const workbench = 'components/VolWorkbench.tsx';
  const strip = fragment(workbench, 'div', 'className="vd-result-strip"', 'ui_maximum_profit_at_expiry_238');
  const reading = fragment(workbench, 'div', 'className="vd-chart-reading"', 'ui_price_276');
  const filename = path.join(SRC, workbench);
  const source = ts.createSourceFile(filename, fs.readFileSync(filename, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const simulations = [];
  const visit = node => {
    if (ts.isFunctionDeclaration(node) && node.name?.text === 'simulate') simulations.push(node.getText(source));
    ts.forEachChild(node, visit);
  };
  visit(source); assert.equal(simulations.length, 1);
  const simulate = async fields => {
    const errors = [], requests = [];
    const scope = { exports: {}, AbortController, Error, String, request: { current: null }, setBusy: () => {},
      setError: value => errors.push(value), setResult: () => {}, setCalculatedKey: () => {}, configKey: 'synthetic',
      numberInput: numbers.numberInput, serializeLegs: numbers.serializeLegs, tr: translate.t, legs: [], currency: 'USD',
      volRequest: async (...args) => { requests.push(args); return {}; },
      spot: '100', scenarioSpot: '', rate: '0', dividend: '0', elapsed: '0', shift: '0', commission: '0', ...fields };
    vm.runInNewContext(ts.transpileModule(simulations[0] + '\nexports.run = simulate;', {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText, scope);
    await scope.exports.run();
    assert.equal(requests.length, 0, 'an unreadable field never reaches the simulator');
    return errors.at(-1);
  };
  const result = { entry_kind: 'debit', entry_cost: -1.5, currency: 'USD', scenario: { pnl: 2, price: 108.5 },
    unlimited_profit: false, max_profit: 5, unlimited_loss: false, max_loss: -1.5, same_expiry: true };
  const words = {
    it: { calendar: '>Spread calendario</button>', shock: '<span>Shock IV</span>', strip: '<span>P&amp;L scenario</span>',
      reading: '<span>P&amp;L scenario <b>', dividend: 'Dividend yield annuo: scrivi un numero', shift: 'Shock IV: scrivi un numero',
      foreign: ['Calendar spread', 'IV shock', 'Scenario P&amp;L'] },
    en: { calendar: '>Calendar spread</button>', shock: '<span>IV shock</span>', strip: '<span>Scenario P&amp;L</span>',
      reading: '<span>Scenario P&amp;L <b>', dividend: 'Annual dividend yield: enter a number', shift: 'IV shock: enter a number',
      foreign: ['Spread calendario', 'Shock IV', 'P&amp;L scenario'] },
  };
  for (const language of ['it', 'en']) {
    languages.impostaLinguaCorrente(language);
    const w = words[language];
    const lab = renderToStaticMarkup(React.createElement(StrategyLab, { ticker: '', legs: [numbers.blankLeg()], setLegs() {}, observedSpot: null }));
    assert.ok(lab.includes(w.calendar), `${language}: calendar template: ${lab}`);
    assert.ok(lab.includes(w.shock), `${language}: IV shock field`);
    const outcome = strip({ Datum, tr: translate.t, volNumber: numbers.volNumber, result });
    assert.ok(outcome.includes(w.strip), `${language}: result strip: ${outcome}`);
    const pointer = reading({ tr: translate.t, volNumber: numbers.volNumber, hoverRow: { price: 108.5, scenario: 2, expiry: 3 } });
    assert.ok(pointer.includes(w.reading), `${language}: pointer reading: ${pointer}`);
    for (const html of [lab, outcome, pointer]) {
      for (const word of w.foreign) assert.ok(!html.includes(word), `${language}: ${word} leaked`);
      assert.ok(!html.includes('⟦'), 'no missing translation marker');
    }
    assert.equal(await simulate({ dividend: '' }), w.dividend);
    assert.equal(await simulate({ shift: '' }), w.shift);
  }
});

test('the Italian browser QA and the handbook use the labels the app now renders', () => {
  const qa = fs.readFileSync(path.resolve(__dirname, '../../../tools/qa/vol_browser.py'), 'utf8');
  assert.ok(qa.includes('"language": "it"'), 'the browser QA runs the app in Italian');
  assert.equal(qa.split("get_by_role('columnheader', name='Scadenza', exact=True)").length - 1, 2, 'term table header in Italian');
  assert.ok(!qa.includes("name='Expiry'"), 'the Italian page has no Expiry header');
  const guide = fs.readFileSync(path.resolve(__dirname, '../../../docs/guide/pages/09-vol-deck.md'), 'utf8').replace(/\s+/g, ' ');
  assert.ok(guide.includes('**Calendar spread** (*Spread calendario*)'), 'handbook: calendar template in both languages');
  assert.ok(guide.includes('**IV shock** (*Shock IV*, percentage points)'), 'handbook: IV shock in both languages');
  assert.ok(!guide.includes('**Shock IV**'), 'handbook: English label is IV shock');
});
