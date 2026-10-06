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
    // gotcha SSR: il loader di /filings riscriverebbe lo stato seminato, quindi lo stub restituisce il seed
    '@/lib/api': { Bellomberg: { filingOverview: async () => seed[17] ?? null } }, '@/lib/useBox': { useBox: () => [{ current: null }, { w: 1000, h: 650 }] },
    '@/components/RunConfirmDialog': () => null,
  } });
  const language = carica('i18n/lingua.ts'), Page = carica('pages/AgentsLive.tsx').default;
  return selected => {
    index = 0; memoIndex = 0; language.impostaLinguaCorrente(selected);
    return renderToStaticMarkup(React.createElement(Page));
  };
}
const pageInLanguage = (selected, seed = {}) => retainedPage(seed)(selected);

// 02/10/2026 (redesign Nuova): il quadrante, la tabella delle cifre e il nastro a colonne fisse non
// ci sono piu'; questi test chiedono le stesse garanzie (n.d. mai zero, nessuna «nessuna run» su un
// heartbeat illeggibile, KO e dettagli originali conservati, singolare/plurale) al markup nuovo.
test('unreadable or absent first heartbeat never asserts that no run is active', () => {
  for (const selected of ['it', 'en']) {
    const html = pageInLanguage(selected, { 4: 'original heartbeat failure' });
    assert.match(html, /original heartbeat failure/);
    assert.doesNotMatch(html, /Nessuna run in corso|No run in progress|Lancia il comitato|Launch the committee/);
    assert.match(html, selected === 'it' ? /Stato non disponibile/ : /State unavailable/);
    assert.match(html, /data-vista="cieco"/);
  }
});

test('missing total cost does not claim a complete total in either language', () => {
  for (const selected of ['it', 'en']) {
    const html = pageInLanguage(selected, { 3: { running: false, start_time: '2026-09-12T10:00:00', completed_at: '2026-09-12T10:02:00' } });
    assert.doesNotMatch(html, /totale completo|complete total|Completo<|Complete</);
    assert.match(html, selected === 'it' ? /costo non disponibile/ : /cost unavailable/);
    assert.doesNotMatch(html, /0,00\s€|€0\.00/);
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
  assert.match(it, /Costi della run/); assert.match(en, /Run costs/);
  assert.match(it, /1\.234,50/); assert.match(en, /1,234\.50/);
  assert.match(it, /0,00/); assert.match(en, /0\.00/);
  assert.match(it, /MINIMO/); assert.match(en, /LOWER BOUND/);
  assert.match(en, /agent failed the API call/);
  assert.match(it, /Errore API dichiarato/); assert.match(en, /API error declared/);
  for (const html of [it, en]) {
    assert.match(html, /original aggregation failure/);
    assert.match(html, /get_synthetic_data/);
    assert.match(html, /Original synthetic input/);
    assert.match(html, /data-esito="KO"/);
  }
});

test('a retained run memo switches labels and preserves its original language and stage times', () => {
  const fixture = { language: 'it', running: false, start_time: '2026-09-12T10:00:00', completed_at: '2026-09-12T10:02:00',
    tool_log: [{ time: '10:00:05', specialist: 'quant', round: 1, tool: 'get_synthetic_data', input: 'Original synthetic input' }],
    esito_run: { stato: 'completata', memo_consegnato: true, motivo: null, ripresa_disponibile: null, ripresa: false, fonte: 'registro_run_settimanale', stato_memo_db: 'scritto' },
  };
  const render = retainedPage({ 3: fixture });
  const it = render('it'), en = render('en'), again = render('it');
  assert.match(it, /Ultima run/); assert.match(en, /Last run/);
  assert.match(en, /Original run · Italian/);
  // le tappe portano gli stessi tempi in tutte e due le lingue
  const steps = html => [...html.matchAll(/data-tappa="([^"]+)" data-stato="([^"]+)"/g)].map(m => m[1] + ':' + m[2]);
  assert.deepEqual(steps(en), steps(it));
  assert.ok(steps(it).includes('r1:done'), steps(it).join(' '));
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

test('the calls tape keeps its round label and the original call input in both languages', () => {
  const it = pageInLanguage('it', { 0: tapeRoster, 3: tapeFixture() }), en = pageInLanguage('en', { 0: tapeRoster, 3: tapeFixture() });
  for (const html of [it, en]) {
    assert.match(html, /<span class="rd">R1<\/span>/);
    assert.doesNotMatch(html, /class="(?:round|rnd)"/);
    assert.match(html, /<code>get_synthetic_b<\/code>/);
    assert.match(html, /Synthetic input 2/);
  }
  assert.match(it, /dalla più recente/); assert.match(en, /newest first/);
});

test('desk cards read their report, calls and verdict in each language', () => {
  const it = pageInLanguage('it', { 0: tapeRoster, 3: tapeFixture() }), en = pageInLanguage('en', { 0: tapeRoster, 3: tapeFixture() });
  assert.match(it, /<b>Quant<\/b><span>Original role<\/span>/);
  assert.match(it, /Errore API<\/span>/); assert.match(en, /API error<\/span>/);
  assert.match(it, /<span>1 chiamata<\/span>/); assert.match(en, /<span>1 call<\/span>/);
  assert.match(it, /<span>2 chiamate<\/span>/); assert.match(en, /<span>2 calls<\/span>/);
  assert.match(it, /strumenti distinti · 2 chiamate API/); assert.match(en, /distinct tools · 2 API calls/);
  assert.doesNotMatch(en, /chiamat/);
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
  assert.match(it, /<span>Quant e Macro dichiarano/);
  assert.match(en, /<span>Quant and Macro report/);
  assert.match(it, /FX USD\/EUR <b class="is-warn">n\.d\.<\/b>/);
  assert.match(en, /FX USD\/EUR <b class="is-warn">n\/a<\/b>/);
  const declaredHole = { 3: tapeFixture(undefined, { fx_source: 'n.d.' }) };
  assert.match(pageInLanguage('en', seed(declaredHole)), /FX USD\/EUR <b class="is-warn">n\/a<\/b>/);
  const live = { 3: tapeFixture(undefined, { fx_source: 'live' }) };
  for (const selected of ['it', 'en']) assert.match(pageInLanguage(selected, seed(live)), /FX USD\/EUR <b class="is-live">live<\/b>/);
  assert.match(it, /title="1\.000 token"/);
  assert.match(en, /title="1,000 tokens"/);
});

// 13/09 (Claude Opus 5, lotto C2 P3): singolare con conteggio 1, plurale con 2, nelle due lingue.
test('one desk in API error takes the singular and two desks keep the plural, in both languages', () => {
  const one = tapeFixture(['get_synthetic_a'], { error_agents: ['quant'] });
  const oneIt = pageInLanguage('it', { 0: tapeRoster, 3: one }), oneEn = pageInLanguage('en', { 0: tapeRoster, 3: one });
  assert.match(oneIt, /<b>1 desk su 2 ha sbattuto contro l’API\.<\/b>/);
  assert.match(oneIt, /<span>Quant dichiara <b>status api_error<\/b> e ha comunque consegnato il report<\/span>/);
  assert.match(oneIt, /\(50%\) spesi da quel desk<\/span>/);
  assert.match(oneIt, /Dentro ci sono 1,00\s€ spesi da 1 desk finito in errore\./);
  assert.match(oneEn, /<b>1 desk out of 2 hit API errors\.<\/b>/);
  assert.match(oneEn, /<span>Quant reports <b>status api_error<\/b> and still delivered the report<\/span>/);
  assert.match(oneEn, /\(50%\) spent by that desk<\/span>/);
  assert.match(oneEn, /This includes €1\.00 spent by 1 desk that ended in error\./);
  const twoIt = pageInLanguage('it', { 0: tapeRoster, 3: tapeFixture() }), twoEn = pageInLanguage('en', { 0: tapeRoster, 3: tapeFixture() });
  assert.match(twoIt, /<b>2 desk su 2 hanno sbattuto contro l’API\.<\/b>/);
  assert.match(twoIt, /<span>Quant e Macro dichiarano <b>status api_error<\/b> e hanno comunque consegnato il report<\/span>/);
  assert.match(twoIt, /\(100%\) spesi da loro<\/span>/);
  assert.match(twoIt, /Dentro ci sono 2,00\s€ spesi da 2 desk finiti in errore\./);
  assert.match(twoEn, /<b>2 desks out of 2 hit API errors\.<\/b>/);
  assert.match(twoEn, /<span>Quant and Macro report <b>status api_error<\/b> and still delivered the report<\/span>/);
  assert.match(twoEn, /\(100%\) spent by them<\/span>/);
  assert.match(twoEn, /This includes €2\.00 spent by 2 desks that ended in error\./);
});

// 06/10 (esito_run): titolo, pastiglia e «memo consegnato» vengono dall'esito del backend,
// mai da running + memo_id. Numeri e motivi inventati.
const esitoDi = (stato, extra = {}) => ({ stato, memo_consegnato: false, motivo: null, ripresa_disponibile: null, ripresa: false,
  fonte: 'registro_run_settimanale', stato_memo_db: 'in_corso', ...extra });
const chiusa = (esito, extra = {}) => ({ running: false, start_time: '2026-09-12T10:00:00', completed_at: '2026-09-12T10:05:00',
  memo_id: 77, esito_run: esito, ...extra });

test('esito_run drives title, pill and memo: a blocked run with memo_id is never «delivered», in both languages', () => {
  const ferma = chiusa(esitoDi('bloccata', { motivo: 'synthetic blocked reason', ripresa_disponibile: true }));
  const it = pageInLanguage('it', { 0: tapeRoster, 3: ferma }), en = pageInLanguage('en', { 0: tapeRoster, 3: ferma });
  assert.match(it, /<h1>Run ferma · nessun memo consegnato<\/h1>/);
  assert.match(it, /Motivo: synthetic blocked reason\. Si può riprendere\./);
  assert.match(it, />Ferma</);
  assert.match(en, /<h1>Run stopped · no memo delivered<\/h1>/);
  assert.match(en, /Reason: synthetic blocked reason\. It can be resumed\./);
  for (const html of [it, en]) {
    assert.doesNotMatch(html, /consegnato il memo|delivered the memo|Nessun errore|No errors/);
    assert.match(html, /data-tappa="memo" data-stato="skip"/);
    assert.match(html, /data-avviso="ripresa"/);
  }
});

test('a completed run with the memo delivered keeps the completed wording', () => {
  const ok = chiusa(esitoDi('completata', { memo_consegnato: true, stato_memo_db: 'scritto' }));
  const it = retainedPage({ 0: tapeRoster, 3: ok })('it');
  assert.match(it, /data-tappa="memo" data-stato="done"/);
  assert.match(it, /Memo #77/);
});

test('a dead process overrides running:true: interrupted run, no desk at work, outcome not «no errors»', () => {
  const morta = chiusa(esitoDi('interrotta', { fonte: 'processo_morto', motivo: 'synthetic' }), { running: true, completed_at: null,
    specialist_status: { quant: 'running', macro: 'done' }, updated_at: '2026-09-12T10:04:00' });
  const it = pageInLanguage('it', { 0: tapeRoster, 3: morta }), en = pageInLanguage('en', { 0: tapeRoster, 3: morta });
  assert.match(it, /<h1>Run interrotta<\/h1>/);
  assert.match(it, /Il processo si è chiuso senza scrivere un esito\. Nessun memo consegnato\./);
  assert.match(en, /<h1>Run interrupted<\/h1>/);
  for (const html of [it, en]) {
    assert.match(html, /data-vista="finita"/);
    assert.doesNotMatch(html, /Ferma la run|Stop the run|Nessun errore|No errors/);
  }
});

test('a starting run says the data below belong to the previous run', () => {
  const avvio = chiusa(esitoDi('in_corso', { fonte: 'processo_vivo' }));
  const it = pageInLanguage('it', { 0: tapeRoster, 3: avvio }), en = pageInLanguage('en', { 0: tapeRoster, 3: avvio });
  assert.match(it, /<h1>Run avviata · in attesa del primo segnale<\/h1>/);
  assert.match(it, /I dati sotto sono della run precedente\./);
  assert.match(en, /<h1>Run started · waiting for the first signal<\/h1>/);
  for (const html of [it, en]) assert.match(html, /data-vista="viva"/);
});

test('a run that died before the committee says so; failed-then-succeeded attempts are information, not KO', () => {
  const vuota = chiusa(esitoDi('fallita', { motivo: 'synthetic' }), { lavagna: 'assente' });
  assert.match(pageInLanguage('it', { 0: tapeRoster, 3: vuota }), /La run si è fermata prima di avviare il comitato: nessuna chiamata, nessun costo di questa run\./);
  const ritenta = chiusa(esitoDi('completata', { memo_consegnato: true }), { usage_total: { cost_eur: 2, partial: false, error_agents: [],
    tentativi_falliti_poi_riusciti: [{ agent: 'quant', round: 1, tentativi_falliti: 2 }, { agent: 'macro', round: 0, tentativi_falliti: 1 }] } });
  const it = pageInLanguage('it', { 0: tapeRoster, 3: ritenta }), en = pageInLanguage('en', { 0: tapeRoster, 3: ritenta });
  assert.match(it, /Quant: 2 tentativi falliti prima di riuscire \(Round 1\)/);
  assert.match(it, /Macro: 1 tentativo fallito prima di riuscire \(Round 0\)/);
  assert.match(en, /Quant: 2 failed attempts before succeeding \(Round 1\)/);
  assert.doesNotMatch(it, /data-avviso="ko"/);
});

test('a live round says how many desks work out of how many, with one or two, in both languages', () => {
  // seed 7 = l'orologio (now): la run viva dura 5 minuti; seed 3 = l'heartbeat dichiarato.
  const start = Date.parse('2026-09-12T10:00:00');
  const live = running => ({ running: true, start_time: '2026-09-12T10:00:00', current_round: 1, updated_at: '2026-09-12T10:04:58',
    specialist_status: Object.fromEntries(['quant', 'macro'].map(id => [id, running.includes(id) ? 'running' : 'done'])),
    tool_log: [{ time: '10:04:50', specialist: 'quant', round: 1, tool: 'get_synthetic_a', input: '{}' },
      { time: '10:03:00', specialist: 'macro', round: 1, tool: 'get_synthetic_b', input: '{}' }] });
  const one = { 0: tapeRoster, 3: live(['quant']), 7: start + 300000 }, two = { 0: tapeRoster, 3: live(['quant', 'macro']), 7: start + 300000 };
  assert.match(pageInLanguage('it', one), /<b>Round 1 in corso<\/b> · 1 desk su 2 al lavoro, 1 ha consegnato/);
  assert.match(pageInLanguage('en', one), /<b>Round 1 in progress<\/b> · 1 of 2 desks working, 1 delivered/);
  assert.match(pageInLanguage('it', two), /<b>Round 1 in corso<\/b> · 2 desk su 2 al lavoro</);
  assert.match(pageInLanguage('en', two), /<b>Round 1 in progress<\/b> · 2 of 2 desks working</);
  // il desk che ha appena chiamato uno strumento lo mostra; l'altro ragiona dall'ultima chiamata
  assert.match(pageInLanguage('it', two), /<code>get_synthetic_a<\/code>/);
  assert.match(pageInLanguage('it', two), /Ragiona da 2&#x27;00&quot;/);
});

test('the token tooltip of the run economics reads one token with a count of 1', () => {
  const usage = tapeFixture(undefined, { in: 2, out: 1 });
  const it = pageInLanguage('it', { 0: tapeRoster, 3: usage }), en = pageInLanguage('en', { 0: tapeRoster, 3: usage });
  assert.match(it, /title="2 token"/); assert.match(it, /title="un token"/);
  assert.match(en, /title="2 tokens"/); assert.match(en, /title="one token"/);
  assert.doesNotMatch(en, /title="1 tokens"/);
});

// Chat: stesso harness di communications.test.cjs, ridotto a cio' che serve al chip dei token.
function chatPage(seed) {
  let index = 0, refIndex = 0;
  const states = { ...seed }, refs = [];
  const hooks = { ...React, useRef(current) { const at = refIndex++; return refs[at] ||= { current }; },
    useState(initial) {
      const at = index++;
      if (!(at in states)) states[at] = typeof initial === 'function' ? initial() : initial;
      return [states[at], value => { states[at] = typeof value === 'function' ? value(states[at]) : value; }];
    },
    useEffect() {},
  };
  const carica = creaCaricatore({ stub: { react: hooks, '../lib/api': { Bellomberg: {} }, '@/lib/api': { Bellomberg: {} },
    'react-markdown': props => React.createElement('div', {}, props.children), 'remark-gfm': () => {},
    '@/components/ConfirmDialog': () => null, './ConfirmDialog': () => null,
    '@/lib/useBox': { useBox: () => [{ current: null }, { w: 1000, h: 190 }] },
  } });
  const lingua = carica('i18n/lingua.ts'), Chat = carica('pages/Chat.tsx').default;
  return selected => { index = 0; refIndex = 0; lingua.impostaLinguaCorrente(selected); return renderToStaticMarkup(React.createElement(Chat)); };
}

test('the chat output token chip reads one token with a count of 1 and keeps the plural with 2', () => {
  const agent = { id: 'quant', name: 'QUANT', role: 'Original role', color: '#29D3F2' };
  const chip = out => chatPage({ 0: { agents: [agent] }, 3: agent, 8: [
    { id: 1, role: 'assistant', content: 'Original synthetic answer', tokens: { in: 10, out } },
  ] });
  const one = chip(1), two = chip(2);
  assert.match(one('it'), /<span class="chip n">un token<\/span>/);
  assert.match(one('en'), /<span class="chip n">one token<\/span>/);
  assert.match(two('it'), /<span class="chip n">2 token<\/span>/);
  assert.match(two('en'), /<span class="chip n">2 tokens<\/span>/);
});

// 03/10/2026 (filing fase D, task 8): copertura filing nella card della run e nella scheda «Filing» del pannello.
// Indici di useState in coda: 15 = scheda, 17 = panoramica /filings, 18 = errore, 19 = attivazione in corso, 20 = esito.
const filingOverview = (senza = 5) => ({
  titoli: [['NOVA', 0, true], ['KORE', 1, true], ['ACME', 2, true], ['NOVB', 2, true], ['KORB', 1, true], ['ACMB', 2, true], ['NOVC', 2, true],
    ...['KORC', 'ACMC', 'NOVD', 'KORD', 'ACMD'].slice(0, senza).map(t => [t, 3, false])]
    // forma reale della riga di stato (filing_context.scheda_da_run): «TICKER · fonte · …»
    .map(([ticker, gruppo, profilo]) => ({ ticker, gruppo,
      stato_riga: profilo ? `${ticker} · SEC 10-K/10-Q CIK 0009990001 · anno al 31/12/2026 vs 31/12/2025 · confronto del 02/10 · run 12 · Synthetic status`
        : `${ticker} · non disponibile: nessun profilo (attivabile dalla pagina Filing)`,
      fonte: profilo ? 'SEC 10-K/10-Q CIK 0009990001' : null, ultimo_confronto: profilo ? '2026-10-02T08:00:00' : null,
      run_id: profilo ? 12 : null, novita: gruppo === 0, profilo, escluso: false })),
  copertura: { totale: 7 + senza, con_confronto: 7, aggiornati: 5, non_aggiornati: 2, senza_confronto: 0, senza_profilo: senza, esclusi: 0 },
  contesto: { caratteri: 9840, budget: 14000, omessi_totali: 0 }, aggiornamento: null,
});

test('filing words exist in both languages with the coverage and result phrases', () => {
  const p = load('pages/agents/parole.ts');
  for (const selected of ['it', 'en']) {
    language.impostaLinguaCorrente(selected);
    const w = p.parole();
    assert.equal(typeof w.filingTitle, 'string');
    assert.equal(typeof w.filingCoverage, 'function');
    assert.equal(typeof w.filingResult, 'function');
  }
  language.impostaLinguaCorrente('it');
  assert.equal(p.parole().filingTitle, 'Filing per il Comitato');
  assert.equal(p.parole().filingCoverage(7, 12), '7 di 12 titoli con confronto');
  assert.equal(p.parole().filingResult(3, 1, 1), '3 attivati · 1 da confermare · 1 senza fonte');
  assert.equal(p.parole().filingResult(1, 0, 0), '1 attivato · 0 da confermare · 0 senza fonte');
  language.impostaLinguaCorrente('en');
  assert.equal(p.parole().filingTitle, 'Filings for the committee');
  assert.equal(p.parole().filingCoverage(7, 12), '7 of 12 holdings with a comparison');
  assert.equal(p.parole().filingResult(3, 1, 1), '3 activated · 1 to confirm · 1 without a source');
});

test('the run card carries the compact filing line and the filing tab shows coverage, context and the bulk action', () => {
  const it = pageInLanguage('it', { 15: 'filing', 17: filingOverview() }), en = pageInLanguage('en', { 15: 'filing', 17: filingOverview() });
  assert.match(it, /Filing: 7\/12 con confronto · 5 da attivare/);
  assert.match(en, /Filings: 7\/12 with a comparison · 5 to activate/);
  assert.match(it, /Filing per il Comitato/); assert.match(en, /Filings for the committee/);
  assert.match(it, /7 di 12 titoli con confronto/);
  assert.match(it, /5 aggiornati/); assert.match(it, /2 non aggiornati/); assert.match(it, /5 senza profilo/);
  assert.match(it, /9\.840 \/ 14\.000 caratteri/); assert.match(en, /9,840 \/ 14,000 characters/);
  assert.match(it, /aria-pressed="true"[^>]*>Filing</);
  for (const html of [it, en]) {
    assert.match(html, /data-filing-activate="1"/);
    assert.doesNotMatch(html, /data-filing-activate="1"[^>]*disabled/);
    assert.match(html, /run 12 · Synthetic status/);
  }
  assert.match(it, />Attiva i mancanti</); assert.match(en, />Activate missing</);
  assert.doesNotMatch(en.replace(/<[^>]*>/g, ' '), /mancanti|con confronto|caratteri/);
});

test('a holding row shows the source once and the status without repeating ticker and source', () => {
  for (const selected of ['it', 'en']) {
    const html = pageInLanguage(selected, { 15: 'filing', 17: filingOverview() });
    // fase E (03/10/2026): le righe sono pulsanti che aprono la pagina Filing sul titolo
    assert.match(html, /<button type="button" class="ag-filing-tk is-link" data-filing-ticker="NOVA"/);
    assert.match(html, /data-filing-pagina="1"/);
    const riga = html.match(/data-filing-ticker="NOVA"[^>]*>([\s\S]*?)<\/button>/)[1].replace(/<[^>]*>/g, ' ');
    assert.equal(riga.split('SEC 10-K/10-Q CIK 0009990001').length - 1, 1, riga);
    assert.equal(riga.split('NOVA').length - 1, 1, riga);
    assert.match(riga, /SEC 10-K\/10-Q CIK 0009990001 · anno al 31\/12\/2026/);
    const senza = html.match(/data-filing-ticker="KORC"[^>]*>([\s\S]*?)<\/button>/)[1].replace(/<[^>]*>/g, ' ');
    assert.equal(senza.split('KORC').length - 1, 1, senza);
    assert.match(senza, /non disponibile: nessun profilo/);
  }
});

test('holdings with a profile but no comparison yet get their own legend entry', () => {
  const dati = filingOverview();
  dati.copertura = { ...dati.copertura, aggiornati: 4, senza_confronto: 1 };
  assert.match(pageInLanguage('it', { 15: 'filing', 17: dati }), /1 senza confronto/);
  assert.match(pageInLanguage('en', { 15: 'filing', 17: dati }), /1 without a comparison/);
  assert.doesNotMatch(pageInLanguage('it', { 15: 'filing', 17: filingOverview() }), /senza confronto</);
});

test('the bulk action is disabled with nothing missing, during a run, and when the archive is unavailable', () => {
  const disabled = html => /<button[^>]*data-filing-activate="1"[^>]*disabled/.test(html) || /<button[^>]*disabled[^>]*data-filing-activate="1"/.test(html);
  for (const selected of ['it', 'en']) {
    assert.ok(disabled(pageInLanguage(selected, { 15: 'filing', 17: filingOverview(0) })), 'nothing missing');
    const live = { running: true, start_time: '2026-09-12T10:00:00' };
    assert.ok(disabled(pageInLanguage(selected, { 3: live, 15: 'filing', 17: filingOverview() })), 'run active');
    const down = pageInLanguage(selected, { 15: 'filing', 17: null, 18: { stato: 503, msg: 'Synthetic archive fault' } });
    assert.ok(disabled(down), 'archive unavailable');
    assert.match(down, selected === 'it' ? /Archivio filing non disponibile/ : /Filing archive unavailable/);
    assert.match(down, /Synthetic archive fault/);
  }
  assert.match(pageInLanguage('it', { 15: 'filing', 17: filingOverview(0) }), /Filing: 7\/7 con confronto · nessuno da attivare/);
});

test('after the bulk activation the tab summarises the outcome and lists the holdings to confirm', () => {
  const esito = { attivati: ['KORC', 'ACMC', 'NOVD'], da_confermare: ['KORD'], senza_fonte: ['ACMD'], esclusi: [], gia_attivi: [], errori: [{ ticker: 'NOVX', motivo: 'Synthetic failure' }],
    aggiornamento: 'in coda: subito dopo il controllo in corso' };
  const it = pageInLanguage('it', { 15: 'filing', 17: filingOverview(), 20: esito }), en = pageInLanguage('en', { 15: 'filing', 17: filingOverview(), 20: esito });
  assert.match(it, /3 attivati · 1 da confermare · 1 senza fonte/);
  assert.match(en, /3 activated · 1 to confirm · 1 without a source/);
  for (const html of [it, en]) {
    assert.match(html, /data-filing-confirm="KORD"/);
    assert.match(html, /NOVX/); assert.match(html, /Synthetic failure/);
  }
  assert.match(it, /partono appena finisce il controllo in corso/); assert.match(en, /start as soon as the current check ends/);
});

// Revisione G9b (08b S13): costo di un agente non dichiarato = n.d., mai 0 nella somma.
test('agent cost missing is n/a in the KO share and the agent sum, never counted as 0', () => {
  const fixture = { running: false, start_time: '2026-09-12T10:00:00', completed_at: '2026-09-12T10:02:00',
    usage_total: { cost_eur: 10, partial: true, error_agents: ['quant'], in: 1, out: 1, cache_read: 0, cache_write: 0 },
    usage_by_specialist: { quant: { status: 'api_error', cost_eur: null, duration_s: 60, in: 1, out: 1 },
      macro: { status: 'ok', cost_eur: 10, duration_s: 60, in: 1, out: 1 } },
    specialist_status: { quant: 'done', macro: 'done' }, tool_log: [] };
  const roster = [{ id: 'quant', name: 'QUANT', role: 'r', color: '#29D3F2' }, { id: 'macro', name: 'MACRO', role: 'r', color: '#29D3F2' }];
  const html = pageInLanguage('it', { 0: roster, 3: fixture });
  assert.match(html, /1 senza costo dichiarato \(n\.d\., non contati come 0\)/);
  assert.doesNotMatch(html, /Somma degli agenti 10,00\s€ uguale|coincide/);
});

// Revisione PR #16 (05/10/2026, Claude Opus 5.5): il tavolo e i cassetti non affermano piu' di quanto
// il payload sa. Fixture sintetiche; le frasi attese sono scritte qui, non lette dai cataloghi.
const tavoloRoster = [{ id: 'quant', name: 'QUANT', role: 'Original role', color: '#29D3F2' }];
const tavoloFinita = { running: false, start_time: '2026-09-12T10:00:00', completed_at: '2026-09-12T10:05:00', expected_reports: 4,
  reports_by_specialist: { quant: { '0': 'Synthetic opening sentence for the desk.' } }, specialist_status: { quant: 'done' },
  tool_log: [{ time: '10:00:05', specialist: 'quant', round: 0, tool: 'get_synthetic_data', input: 'Synthetic input' }] };

test('the table never quotes a run length that no tool measured', () => {
  const live = { ...tavoloFinita, running: true, completed_at: null, updated_at: new Date().toISOString(), specialist_status: { quant: 'running' } };
  for (const selected of ['it', 'en']) {
    const html = pageInLanguage(selected, { 0: tavoloRoster, 3: live });
    assert.match(html, /class="ag-angolo is-sx"/);
    assert.doesNotMatch(html, /25[–-]40|di solito|usually/);
  }
});

test('an ended run fills the progress bar only with the reports actually delivered', () => {
  for (const selected of ['it', 'en']) {
    const html = pageInLanguage(selected, { 0: tavoloRoster, 3: tavoloFinita, 16: true });
    assert.match(html, /data-vista="finita"/);
    assert.match(html, /class="ag-avanz"><u style="width:25%"/);
    assert.doesNotMatch(html, /class="ag-avanz"><u style="width:100%"/);
  }
});

test('a desk frozen by a stale heartbeat is not counted as working in the table legend', () => {
  const ferma = { ...tavoloFinita, running: true, completed_at: null, updated_at: '2026-09-12T10:01:00', stale_warning: true, specialist_status: { quant: 'running' } };
  for (const selected of ['it', 'en']) {
    const html = pageInLanguage(selected, { 0: tavoloRoster, 3: ferma });
    assert.match(html, /data-agente="quant"[^>]*data-stato="stale"/);
    assert.match(html, /<li class="is-lav">[^<]*<b class="num">0<\/b>/);
    assert.match(html, /<li class="is-ko">[^<]*<b class="num">1<\/b>/);
  }
});

test('the report drawer of an ended run does not promise a report that will never arrive', () => {
  const it = pageInLanguage('it', { 0: tavoloRoster, 3: tavoloFinita, 16: true, 21: { id: 'quant', r: 1, aperto: true } });
  const en = pageInLanguage('en', { 0: tavoloRoster, 3: tavoloFinita, 16: true, 21: { id: 'quant', r: 1, aperto: true } });
  assert.doesNotMatch(it, /arriva quando/); assert.doesNotMatch(en, /arrives when/);
  assert.match(it, /Nessun report del Round 1 di Quant in questa run/);
  assert.match(en, /No Round 1 report from Quant in this run/);
});

test('the report preview does not claim the full desk report is inside the memo', () => {
  for (const selected of ['it', 'en']) {
    const html = pageInLanguage(selected, { 0: tavoloRoster, 3: tavoloFinita, 16: true, 21: { id: 'quant', r: 0, aperto: true } });
    assert.match(html, /Synthetic opening sentence for the desk\./);
    assert.match(html, /500/);
    assert.doesNotMatch(html, /intero è nel memo|full text is in the memo/);
  }
});

test('the insider tool phrase does not narrow insider trades to purchases', () => {
  const p = load('pages/agents/parole.ts');
  for (const selected of ['it', 'en']) {
    language.impostaLinguaCorrente(selected);
    const w = p.parole();
    for (const f of [w.faccio('get_insider_trades', null), w.faccio('get_insider_trades', 'ACME')]) assert.doesNotMatch(f, /acquist|buying/);
  }
  language.impostaLinguaCorrente('it');
});
