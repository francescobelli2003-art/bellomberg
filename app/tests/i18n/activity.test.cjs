const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const vm = require('node:vm');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');
const source = fs.readFileSync(path.resolve(__dirname, '../../src/pages/AgentsLive.tsx'), 'utf8');
const tree = ts.createSourceFile('AgentsLive.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const functions = {};
function visit(node) {
  if (ts.isVariableDeclaration(node) && ['stopRun', 'fmtEur', 'fmtN', 'fmtTok', 'STATUS_DESC'].includes(node.name.getText(tree))) functions[node.name.getText(tree)] = node.initializer.getText(tree);
  if (ts.isFunctionDeclaration(node) && ['isErrorStatus', 'isHoleStatus', 'verdictOf', 'costNotes', 'statusDescriptions'].includes(node.name?.text)) functions[node.name.text] = node.getText(tree);
  ts.forEachChild(node, visit);
}
visit(tree);
ambienteBrowser();
const load = creaCaricatore(), language = load('i18n/lingua.ts'), { t: tr } = load('i18n/t.ts');
const { leggiDetail } = load('lib/quota.ts');
function bind(name, scope) {
  const code = ts.transpileModule('globalThis.fn = ' + functions[name], { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  const context = vm.createContext(scope); vm.runInContext(code, context); return context.fn;
}

function stopScenario(overrides = {}, task = 'SYNTHETIC-TASK') {
  const original = { running: true, start_time: '2026-09-12T10:00:00',
    tool_log: [{ tool: 'get_synthetic_data', input: 'original synthetic evidence' }] };
  const state = { live: original, task, notice: null, stopping: false, reads: 0, resets: 0 };
  localStorage.setItem('bellomberg_active_run', task || '');
  const scope = {
    tr, leggiDetail, ACTIVE_RUN_KEY: 'bellomberg_active_run', localStorage,
    confirm: () => true, console: { error() {}, warn() {} },
    activeTaskId: task,
    setStopping: value => { state.stopping = value; }, setElapsed() {}, setHbIll() {},
    setActiveTaskId: value => { state.task = value; },
    setTriggerMsg: value => { state.notice = value; },
    setStopNotice: value => { state.notice = value; },
    setState: value => { state.live = typeof value === 'function' ? value(state.live) : value; },
    Bellomberg: {
      consigliereActive: async () => ({ active: false }),
      cancelConsigliere: async () => ({ status: 'cancelled', task_id: task }),
      cancelAllConsigliere: async () => ({ n_killed: 1, errors: [] }),
      resetAgentsLive: async () => { state.resets++; return { ok: true }; },
      agentsLive: async () => { state.reads++; return original; },
      ...overrides,
    },
  };
  return { state, original, run: () => bind('stopRun', scope)() };
}

for (const selected of ['it', 'en']) {
test(`failed stop keeps the observed running task and log and performs read-only reconciliation (${selected})`, async () => {
  language.impostaLinguaCorrente(selected);
  const scenario = stopScenario({ cancelConsigliere: async () => { throw new Error('original cancellation failure'); } });
  await scenario.run();
  assert.deepEqual(scenario.state.live, scenario.original);
  assert.equal(scenario.state.task, 'SYNTHETIC-TASK');
  assert.equal(scenario.state.resets, 0);
  assert.equal(scenario.state.reads, 1);
  assert.match(JSON.stringify(scenario.state.notice), /original cancellation failure/);
  assert.equal(scenario.state.stopping, false);
});

test(`HTTP 200 cancel_all errors do not confirm stop or trigger reset (${selected})`, async () => {
  language.impostaLinguaCorrente(selected);
  const scenario = stopScenario({ cancelAllConsigliere: async () => ({ n_killed: 1, errors: [{ error: 'original partial failure' }] }) }, null);
  await scenario.run();
  assert.deepEqual(scenario.state.live, scenario.original);
  assert.equal(scenario.state.resets, 0);
  assert.match(JSON.stringify(scenario.state.notice), /original partial failure/);
});

test(`failed reset is visible and cannot manufacture an idle heartbeat (${selected})`, async () => {
  language.impostaLinguaCorrente(selected);
  const scenario = stopScenario({ resetAgentsLive: async () => ({ ok: false, error: 'original reset failure' }) });
  await scenario.run();
  assert.deepEqual(scenario.state.live, scenario.original);
  assert.equal(scenario.state.task, 'SYNTHETIC-TASK');
  assert.match(JSON.stringify(scenario.state.notice), /original reset failure/);
});

test(`successful stop renders the actual reread state, and unreadable reconciliation retains evidence (${selected})`, async () => {
  language.impostaLinguaCorrente(selected);
  const observed = { running: false, memo_id: 123, tool_log: [{ tool: 'get_synthetic_data', input: 'final evidence' }] };
  const good = stopScenario({ agentsLive: async () => observed });
  await good.run();
  assert.deepEqual(good.state.live, observed);
  assert.equal(good.state.task, null);
  assert.equal(localStorage.getItem('bellomberg_active_run'), null);
  const bad = stopScenario({ agentsLive: async () => { throw new Error('original reconciliation failure'); } });
  await bad.run();
  assert.deepEqual(bad.state.live, bad.original);
  assert.equal(bad.state.task, 'SYNTHETIC-TASK');
  assert.match(JSON.stringify(bad.state.notice), /original reconciliation failure/);
});
}

function retainedPage(seed = {}) {
  let index = 0, memoIndex = 0;
  const memos = [];
  const memo = (compute, deps) => {
    const at = memoIndex++, before = memos[at];
    if (!before || !deps || deps.some((value, i) => !Object.is(value, before.deps[i]))) memos[at] = { deps, value: compute() };
    return memos[at].value;
  };
  const carica = creaCaricatore({ stub: {
    react: { ...React, useLayoutEffect() {}, useMemo: memo, useCallback: (fn, deps) => memo(() => fn, deps), useState(initial) {
      const at = index++;
      return [at in seed ? seed[at] : typeof initial === 'function' ? initial() : initial, () => {}];
    } },
    'react-router-dom': { useNavigate: () => () => {} },
    '@/lib/api': { Bellomberg: {} }, '@/lib/useBox': { useBox: () => [{ current: null }, { w: 1000, h: 650 }] },
    '@/components/SkyCanvas': () => null, '@/components/RunConfirmDialog': () => null,
  } });
  const language = carica('i18n/lingua.ts'), Page = carica('pages/AgentsLive.tsx').default;
  return selected => {
    index = 0; memoIndex = 0; language.impostaLinguaCorrente(selected);
    return renderToStaticMarkup(React.createElement(Page));
  };
}
const pageInLanguage = (selected, seed = {}) => retainedPage(seed)(selected);

test('unreadable or absent first heartbeat never asserts that no run is active', () => {
  for (const selected of ['it', 'en']) {
    const html = pageInLanguage(selected, { 4: 'original heartbeat failure' });
    assert.match(html, /original heartbeat failure/);
    assert.doesNotMatch(html, /NESSUNA RUN IN CORSO|NO RUN IN PROGRESS/);
    assert.match(html, selected === 'it' ? /STATO N.D./ : /STATE N\/A/);
  }
});

test('missing total cost does not claim a complete total in either language', () => {
  for (const selected of ['it', 'en']) {
    const html = pageInLanguage(selected, { 3: { running: false } });
    assert.doesNotMatch(html, /totale non parziale|non-partial total/);
    assert.match(html, selected === 'it' ? /costo totale non disponibile/ : /total cost unavailable/);
  }
});

test('agent costs and domain verdicts translate without replacing zero, KO or raw source details', () => {
  const fixture = { running: false, start_time: '2026-09-12T10:00:00', completed_at: '2026-09-12T10:02:00',
    usage_total: { cost_eur: 1234.5, partial: true, unpriced_agents: ['quant'], error_agents: ['quant'],
      error: 'original aggregation failure', in: 1000, out: 100, cache_read: 0, cache_write: 0 },
    usage_by_specialist: { quant: { status: 'api_error', cost_eur: 0, duration_s: 60, in: 1000, out: 100, cache_read: 0, cache_write: 0 } },
    specialist_status: { quant: 'done' }, tool_log: [{ time: '10:00:05', specialist: 'quant', round: 1, tool: 'get_synthetic_data', input: 'Original synthetic input' }],
  };
  const roster = [{ id: 'quant', name: 'QUANT', role: 'Original role', color: '#29D3F2' }];
  const it = pageInLanguage('it', { 0: roster, 3: fixture }), en = pageInLanguage('en', { 0: roster, 3: fixture });
  assert.match(it, /ECONOMIA DELLA RUN/); assert.match(en, /RUN ECONOMICS/);
  assert.match(it, /1\.234,50/); assert.match(en, /1,234\.50/);
  assert.match(it, /0,00/); assert.match(en, /0\.00/);
  assert.match(it, /MINIMO/); assert.match(en, /LOWER BOUND/);
  assert.match(en, /agent failed the API call/);
  for (const html of [it, en]) {
    assert.match(html, /original aggregation failure/);
    assert.match(html, /get_synthetic_data/);
    assert.match(html, /Original synthetic input/);
    assert.match(html, /data-esito="KO"/);
  }
});

test('a retained run memo switches labels and preserves its original language, timeline and cost evidence', () => {
  const fixture = { language: 'it', running: false, start_time: '2026-09-12T10:00:00', completed_at: '2026-09-12T10:02:00',
    tool_log: [{ time: '10:00:05', specialist: 'quant', round: 1, tool: 'get_synthetic_data', input: 'Original synthetic input' }],
  };
  const render = retainedPage({ 3: fixture });
  const it = render('it'), en = render('en'), again = render('it');
  assert.match(it, /un giro/); assert.match(en, /one revolution/);
  assert.match(en, /Original run · Italian/);
  // Text label boxes may grow with translation; orbital arcs must keep the same timeline.
  const geometry = html => [...html.matchAll(/<path\b[^>]*\bd="([^"]* A[^"]*)"[^>]*>/g)].map(m => m[1]);
  assert.deepEqual(geometry(en), geometry(it));
  assert.equal(it, again);
});

// 13/09 (Claude Opus 5): frasi attese congelate qui, non lette dai cataloghi sotto prova.
const tapeFixture = (tools = ['get_synthetic_a', 'get_synthetic_a', 'get_synthetic_b'], usage = {}) => ({
  running: false, start_time: '2026-09-12T10:00:00', completed_at: '2026-09-12T10:05:00',
  usage_total: { cost_eur: 2, partial: false, error_agents: ['quant', 'macro'], in: 1000, out: 100, cache_read: 0, cache_write: 0, ...usage },
  usage_by_specialist: {
    quant: { status: 'api_error', cost_eur: 1, duration_s: 60, api_calls: 2, in: 500, out: 50, cache_read: 0, cache_write: 0 },
    macro: { status: 'api_error', cost_eur: 1, duration_s: 60, api_calls: 2, in: 500, out: 50, cache_read: 0, cache_write: 0 },
  },
  specialist_status: { quant: 'done', macro: 'done' },
  tool_log: tools.map((tool, i) => ({ time: `10:00:${String(5 + i * 10).padStart(2, '0')}`, specialist: i === 0 ? 'quant' : 'macro', round: 1, tool, input: `Synthetic input ${i}` })),
});
const tapeRoster = [{ id: 'quant', name: 'QUANT', role: 'Original role', color: '#29D3F2' }, { id: 'macro', name: 'MACRO', role: 'Original role', color: '#FFA51E' }];

test('the calls tape keeps its round CSS class in both languages and its round header fits the 20px column', () => {
  const it = pageInLanguage('it', { 0: tapeRoster, 3: tapeFixture() }), en = pageInLanguage('en', { 0: tapeRoster, 3: tapeFixture() });
  for (const html of [it, en]) {
    assert.match(html, /<span class="rd">R1<\/span>/);
    assert.doesNotMatch(html, /class="(?:round|rnd)"/);
  }
  assert.match(it, /<span>T\+<\/span><span>desk<\/span><span>rd<\/span><span>strumento<\/span><span>input<\/span>/);
  assert.match(en, /<span>T\+<\/span><span>desk<\/span><span>rnd<\/span><span>tool<\/span><span>input<\/span>/);
});

test('the figures table header, legend and footnote read in each language', () => {
  const it = pageInLanguage('it', { 0: tapeRoster, 3: tapeFixture() }), en = pageInLanguage('en', { 0: tapeRoster, 3: tapeFixture() });
  assert.match(it, /<i>durata<\/i><i>chiam\.<\/i><i>strum<\/i><i>api<\/i><i>costo<\/i><i class="ce">esito<\/i>/);
  assert.match(en, /<i>dur\.<\/i><i>calls<\/i><i>tool<\/i><i>api<\/i><i>cost<\/i><i class="ce">state<\/i>/);
  assert.match(it, /<b>strum<\/b> = strumenti <b>distinti<\/b> di quel desk\./);
  assert.match(en, /<b>tool<\/b> = <b>distinct<\/b> tools used by that desk\./);
  assert.match(it, / \* i 2 strumenti della run non sono la somma della colonna \(3\): gli stessi strumenti li usano desk diversi\./);
  assert.match(en, / \* the 2 tools in the run are not the sum of the column \(3\): different desks use the same tools\./);
  const one = tapeFixture(['get_synthetic_a', 'get_synthetic_a']);
  assert.match(pageInLanguage('it', { 0: tapeRoster, 3: one }), / \* l&#x27;unico strumento della run non è la somma della colonna \(2\): lo stesso strumento lo usano desk diversi\./);
  assert.match(pageInLanguage('en', { 0: tapeRoster, 3: one }), / \* the only tool in the run is not the sum of the column \(2\): different desks use the same tool\./);
  assert.doesNotMatch(en, / \* i /);
});

test('fixed-width headers of the figures table and of the tape stay inside their columns in both languages', () => {
  // JetBrains Mono: 0,6 em di avanzamento; testate maiuscole con letter-spacing .1em (agents-plancia.css).
  // Misurato in Chrome il 13/09 sul markup SSR vero: 6,3 px a carattere a 9px, 5,6 px a 8px.
  const css = fs.readFileSync(path.resolve(__dirname, '../../src/pages/agents-plancia.css'), 'utf8');
  const columns = selector => {
    const at = css.indexOf(selector); assert.ok(at >= 0, selector);
    const m = /grid-template-columns:([^;]+);gap:(\d+)px/.exec(css.slice(at, at + 400)); assert.ok(m, selector);
    return { widths: m[1].trim().split(/\s+/).map(w => (w.endsWith('px') ? Number(w.slice(0, -2)) : null)), gap: Number(m[2]) };
  };
  const cif = columns('.f4p .cif .hd,.f4p .cif .r,.f4p .cif .tot{display:grid;'), tape = columns('.f4p .lr{display:grid;');
  for (const selected of ['it', 'en']) {
    const html = pageInLanguage(selected, { 0: tapeRoster, 3: tapeFixture() });
    const head = /<div class="hd"><span>[^<]*<\/span>((?:<i[^>]*>[^<]*<\/i>){6})<\/div>/.exec(html); assert.ok(head, selected);
    const labels = [...head[1].matchAll(/<i[^>]*>([^<]*)<\/i>/g)].map(m => m[1]);
    labels.forEach((label, i) => {
      const width = label.length * 6.3, cell = cif.widths[i + 1], last = i === labels.length - 1;
      assert.ok(width <= cell + (last ? 0 : cif.gap), `${selected} «${label}» ${width.toFixed(1)}px in ${cell}px`);
    });
    const round = new RegExp('<span>desk</span><span>([^<]*)</span>').exec(html); assert.ok(round, selected);
    assert.ok(round[1].length * 5.6 <= tape.widths[2], `${selected} «${round[1]}» in ${tape.widths[2]}px`);
  }
});

test('heartbeat message quotes, desk conjunction, FX source hole and token tooltip follow the language', () => {
  const seed = extra => ({ 0: tapeRoster, 3: tapeFixture(), 5: { msg: 'Original read error', da: 1, al: 1 }, ...extra });
  const it = pageInLanguage('it', seed()), en = pageInLanguage('en', seed());
  assert.match(it, /non si legge \(«Original read error»\) — /);
  assert.match(en, /cannot be read \(“Original read error”\) — /);
  assert.doesNotMatch(en, /»/);
  const noMsg = { 5: { msg: null, da: 1, al: 1 } };
  assert.match(pageInLanguage('it', seed(noMsg)), /\(«heartbeat illeggibile»\) — /);
  assert.match(pageInLanguage('en', seed(noMsg)), /\(“unreadable heartbeat”\) — /);
  assert.match(it, /<span>quant e macro dichiarano/);
  assert.match(en, /<span>quant and macro report/);
  assert.match(it, /FX USD\/EUR <b class="amc">n\.d\.<\/b>/);
  assert.match(en, /FX USD\/EUR <b class="amc">n\/a<\/b>/);
  const declaredHole = { 3: tapeFixture(undefined, { fx_source: 'n.d.' }) };
  assert.match(pageInLanguage('en', seed(declaredHole)), /FX USD\/EUR <b class="amc">n\/a<\/b>/);
  const live = { 3: tapeFixture(undefined, { fx_source: 'live' }) };
  for (const selected of ['it', 'en']) assert.match(pageInLanguage(selected, seed(live)), /FX USD\/EUR <b class="okc">live<\/b>/);
  assert.match(it, /title="1\.000 token"/);
  assert.match(en, /title="1,000 tokens"/);
});
