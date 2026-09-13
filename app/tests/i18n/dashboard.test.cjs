const { test } = require('node:test');
const assert = require('node:assert/strict');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

ambienteBrowser();
const load = creaCaricatore();
const language = load('i18n/lingua.ts');
const quota = load('lib/quota.ts');
const curva = load('lib/curva.ts');
const plancia = load('lib/plancia-data.ts');
const portfolio = load('lib/portfolio-values.ts');
const fixture = () => ({
  dates: ['2026-09-10', '2026-09-11', '2026-09-12'],
  twr_index: [100, 101, 102], values_eur: [1000, 1010, 1112.5],
  regimes: ['reconstructed', 'official', 'official'],
  regime_summary: { official_since: '2026-09-11' },
  external_flows: [{ date: '2026-09-12', type: 'DEPOSIT', amount_eur: 100 }],
  notes: ['Nota storica sintetica: mantenere il testo originale'],
});
const inLanguage = (lang, run) => { language.impostaLinguaCorrente(lang); return run(); };

test('quota captions switch IT/EN while numbers, ledger directions and original notes remain identical', () => {
  const output = lang => inLanguage(lang, () => {
    const q = quota.leggiQuota(fixture());
    return { q, head: quota.cimaF1(q, 1112.5, '2026-09-12'), flow: quota.notaFlusso(q, '2026-09-12') };
  });
  const it = output('it'), en = output('en');
  assert.deepEqual(it.q, en.q);
  assert.equal(en.q.flusso.parola, 'VERSATI'); // Existing canonical identifier.
  assert.equal(it.head.contaA, en.head.contaA);
  assert.equal(it.head.contaDa, en.head.contaDa);
  assert.match(it.head.occhiello, /VALORE QUOTA/);
  assert.match(en.head.occhiello, /UNIT VALUE/);
  assert.match(en.head.timbro, /IN PROGRESS/);
  assert.match(en.flow, /DEPOSITED/);
  assert.ok(en.head.nota.includes(fixture().notes[0]));
  assert.deepEqual(output('it'), it);
});

test('curve uses identical TWR/wealth arrays and rejects the same gaps in both languages', () => {
  const read = lang => inLanguage(lang, () => ({
    curve: curva.leggiCurva(fixture(), 'quota', false, null),
    wealth: curva.leggiCurva(fixture(), 'patrimonio', false, null),
    bad: curva.leggiCurva({ ...fixture(), twr_index: [100] }, 'quota', false, null),
    waiting: curva.leggiCurva(null, 'quota', true, null),
  }));
  const it = read('it'), en = read('en');
  for (const name of ['curve', 'wealth']) {
    for (const key of ['valori', 'euro', 'date', 'regimi', 'versamenti', 'confine', 'esclusi', 'piedeVerde']) {
      assert.deepEqual(it[name][key], en[name][key]);
    }
    assert.equal(en[name].stato, 'viva');
  }
  assert.match(en.curve.piede, /POINTS/);
  assert.match(en.wealth.piede, /DEPOSITED/);
  assert.equal(en.bad.stato, it.bad.stato);
  assert.match(en.bad.motivo, /3 dates/);
  assert.match(en.bad.motivo, /1 unit values/);
  assert.match(en.waiting.frase, /reading/i);
});

test('missing data and HTTP errors are localized without translating source details or manufacturing cash', () => {
  const raw = 'Dettaglio originale del backend';
  inLanguage('en', () => {
    assert.match(quota.leggiQuota(null).motivo, /accounting engine/);
    assert.match(quota.motivoChiamata({ response: { status: 503, data: { detail: raw } } }), new RegExp('HTTP 503.*' + raw));
    assert.match(quota.taglia('x'.repeat(125)), /5 more characters/);
    assert.equal(portfolio.portfolioValues({ cash_source_note: raw }).note, raw);
    const missing = portfolio.portfolioValues({ cash_disponibile_eur: 0, nav_total_eur: 1000 });
    assert.equal(missing.cash, null);
    assert.equal(missing.nav, null);
    assert.match(missing.note, /Cash unavailable/);
    assert.match(plancia.derivePlancia(null, [], undefined).reason, /heartbeat unavailable/);
  });
});

test('run dial changes captions while stable phases, tool names and original tool inputs stay intact', () => {
  const st = { running: false, start_time: '2026-09-12T10:00:00', completed_at: '2026-09-12T10:02:00',
    tool_log: [{ time: '10:00:05', specialist: 'fundamentals', round: 1, tool: 'get_valuation', input: 'Nota sintetica originale' }],
    specialist_status: { fundamentals: 'done' },
  };
  const it = inLanguage('it', () => plancia.derivePlancia(st, [], undefined));
  const en = inLanguage('en', () => plancia.derivePlancia(st, [], undefined));
  assert.deepEqual(it.calls, en.calls);
  assert.deepEqual(it.phases, en.phases);
  assert.equal(it.runSec, en.runSec);
  assert.equal(it.scaleSec, en.scaleSec);
  assert.match(en.scaleNote, /one revolution/);
  assert.deepEqual(inLanguage('it', () => plancia.derivePlancia(st, [], undefined)), it);
});

function renderPage(relative, selected, seed = {}) {
  let state = 0;
  const carica = creaCaricatore({ stub: {
    react: { ...React, useState(initial) {
      const index = state++;
      return [index in seed ? seed[index] : relative.includes('Performance') && index === 0 ? { error: 'Synthetic source failure' }
        : relative.includes('Performance') && index === 11 ? 'risk' : typeof initial === 'function' ? initial() : initial, () => {}];
    } },
    'react-router-dom': { useNavigate: () => () => {} },
    '@/lib/api': { Bellomberg: {} },
    'lightweight-charts': {},
    '@/components/RunConfirmDialog': { default: () => null, __esModule: true },
  } });
  carica('i18n/lingua.ts').impostaLinguaCorrente(selected);
  return renderToStaticMarkup(React.createElement(carica(relative).default));
}

test('Dashboard initial view translates all authored loading copy with real dictionaries', () => {
  assert.match(renderPage('pages/Dashboard.tsx', 'it'), /Caricamento dati terminale/);
  const en = renderPage('pages/Dashboard.tsx', 'en');
  assert.match(en, /Loading terminal data/);
  assert.doesNotMatch(en, /⟦dashboard\./);
});

test('Performance switches empty-state instructions and period labels without translated provider IDs', () => {
  const it = renderPage('pages/PerformancePage.tsx', 'it');
  const en = renderPage('pages/PerformancePage.tsx', 'en');
  assert.match(it, /Storico NAV non disponibile/);
  assert.match(en, /NAV history unavailable/);
  assert.match(it, />1G</);
  assert.match(en, />1D</);
  assert.doesNotMatch(en, /⟦dashboard\./);
});

test('all five risk endpoints expose their KO details in both languages instead of disappearing', () => {
  const seed = Object.fromEntries([1, 2, 3, 4, 6].map(index => [index, { error: `Dettaglio originale sintetico ${index}` }]));
  for (const selected of ['it', 'en']) {
    const html = renderPage('pages/PerformancePage.tsx', selected, seed);
    for (const index of [1, 2, 3, 4, 6]) assert.ok(html.includes(`Dettaglio originale sintetico ${index}`), `visible endpoint ${index} in ${selected}`);
    assert.match(html, /\/portfolio\/analytics\/drawdowns/);
    assert.match(html, /\/portfolio\/metrics\/advanced/);
    assert.ok(html.includes(selected === 'it' ? 'Dati non disponibili' : 'Data unavailable'));
  }
});

// Declared hook simulation retains memo dependencies between language renders.
// The pages, formatters, translator and catalogs are real; no API or chart engine is started.
function retainedPage(relative, seed, api = { Bellomberg: {} }, exportName = 'default') {
  let stateIndex = 0, memoIndex = 0, effectIndex = 0;
  const memoCache = [], chartInputs = [], effects = [], pending = [], cleanups = [];
  const carica = creaCaricatore({ stub: {
    react: { ...React,
      useState(initial) {
        const index = stateIndex++;
        if (!(index in seed)) seed[index] = typeof initial === 'function' ? initial() : initial;
        return [seed[index], value => { seed[index] = typeof value === 'function' ? value(seed[index]) : value; }];
      },
      useEffect(effect, deps) {
        const index = effectIndex++, previous = effects[index];
        if (previous && deps && previous.length === deps.length && deps.every((value, i) => Object.is(value, previous[i]))) return;
        effects[index] = deps; pending.push(effect);
      },
      useMemo(calculate, deps) {
        const index = memoIndex++, previous = memoCache[index];
        if (previous && deps && previous.deps.length === deps.length && deps.every((value, i) => Object.is(value, previous.deps[i]))) return previous.value;
        const value = calculate(); memoCache[index] = { deps, value }; return value;
      },
    },
    'react-router-dom': { useNavigate: () => () => {} },
    '@/lib/api': api,
    '@/components/RunConfirmDialog': { default: () => null, __esModule: true },
    '@/components/TerminalChart': { default: props => { chartInputs.push(props); return React.createElement('i', { 'data-chart': props.mode }); }, __esModule: true },
  } });
  const language = carica('i18n/lingua.ts'), Page = carica(relative)[exportName];
  const render = selected => {
    stateIndex = 0; memoIndex = 0; effectIndex = 0; chartInputs.length = 0;
    language.impostaLinguaCorrente(selected);
    return { html: renderToStaticMarkup(React.createElement(Page)), charts: [...chartInputs] };
  };
  render.runEffects = async () => {
    for (const effect of pending.splice(0)) { const cleanup = effect(); if (cleanup) cleanups.push(cleanup); }
    await new Promise(setImmediate); await new Promise(setImmediate);
  };
  render.close = () => { for (const cleanup of cleanups) cleanup(); };
  return render;
}

test('Dashboard relabels a retained quote/curve memo and old error state without replacing source content', () => {
  const note = 'Messaggio originale da conservare';
  const seed = { 0: { cash_source: 'sqlite:cash_state', cash_disponibile_eur: 100, nav_total_eur: 1112.5,
    totale_valore_mercato_eur: 1012.5, totale_pl_eur: 12.5, positions: [], n_positions: 0 },
    4: false, 6: { kind: 'error', detail: note }, 11: fixture(), 13: false, 15: 102 };
  const render = retainedPage('pages/Dashboard.tsx', seed);
  const it = render('it').html, en = render('en').html;
  assert.match(it, /VALORE QUOTA/);
  assert.match(en, /UNIT VALUE/);
  assert.match(it, /102,00/);
  assert.match(en, /102\.00/);
  assert.ok(en.includes(note));
  assert.ok(it.includes('Errore: ' + note));
  assert.ok(en.includes('Error: ' + note));
  assert.equal(render('it').html, it);
});

test('AI desk relabels retained authored roles without a new request or a claim of original source text', async () => {
  const calls = { list: 0, live: 0 };
  const raw = { agents: [{ id: 'synthetic', name: 'SYNTH', role: 'Analisi sintetica', color: '#abcdef' }],
    _presentation_v1: { version: 1, texts: [{ path: ['agents', 0, 'role'], it: 'Analisi sintetica', en: 'Synthetic analysis' }] } };
  const render = retainedPage('pages/Dashboard.tsx', {}, { Bellomberg: {
    agentsList: async () => { calls.list++; return raw; },
    agentsLive: async () => { calls.live++; return { running: false }; },
  } }, 'AiDeskPanel');
  try {
    render('it'); await render.runEffects();
    const it = render('it').html, en = render('en').html;
    assert.match(it, /Analisi sintetica/); assert.match(en, /Synthetic analysis/);
    assert.doesNotMatch(en, /original source text/i);
    await render.runEffects();
    assert.deepEqual(calls, { list: 1, live: 1 });
    assert.equal(raw.agents[0].role, 'Analisi sintetica');
  } finally { render.close(); }
});

test('AI desk declares list and heartbeat failures instead of reporting standby or ready', async () => {
  const render = retainedPage('pages/Dashboard.tsx', {}, { Bellomberg: {
    agentsList: async () => { throw new Error('Synthetic list failure'); },
    agentsLive: async () => { throw new Error('Synthetic heartbeat failure'); },
  } }, 'AiDeskPanel');
  try {
    render('it'); await render.runEffects();
    for (const lang of ['it', 'en']) {
      const html = render(lang).html;
      assert.match(html, /Synthetic list failure/); assert.match(html, /Synthetic heartbeat failure/);
      assert.doesNotMatch(html, />STANDBY<|>READY<|>PRONTO</);
    }
  } finally { render.close(); }
});

test('Performance retains chart series while memoized monthly/overlay captions and earlier benchmark KO switch language', () => {
  const benchmark = { dates: fixture().dates, index: [100, 100.5, 101], carried_flags: [false, true, false], ticker: 'SPY' };
  const render = retainedPage('pages/PerformancePage.tsx', { 5: fixture(), 13: benchmark });
  const it = render('it'), en = render('en');
  assert.ok(it.html.includes('PORTAFOGLIO 2026'));
  assert.ok(en.html.includes('BOOK 2026'));
  assert.deepEqual(it.charts.map(p => p.bars), en.charts.map(p => p.bars));
  assert.match(it.charts[0].overlays[0].label, /TR UFF/);
  assert.match(en.charts[0].overlays[0].label, /OFFICIAL TR/);
  assert.deepEqual(it.charts[0].overlays.map(p => p.points), en.charts[0].overlays.map(p => p.points));
  assert.ok(en.html.includes(fixture().notes[0]));
  const failed = retainedPage('pages/PerformancePage.tsx', { 15: { kind: 'fallback', why: { kind: 'restart' }, stage: 'alignment', detail: null } });
  assert.match(failed('it').html, /BENCHMARK N\.D\./);
  assert.match(failed('en').html, /BENCHMARK N\/A/);
  assert.doesNotMatch(failed('en').html, /serie uff\./);
});

test('Dashboard transport failure leaves loading state and retains its original detail across languages', async () => {
  let portfolioCalls = 0;
  const api = { Bellomberg: new Proxy({}, { get: (_target, name) => name === 'portfolio'
    ? () => { portfolioCalls++; return Promise.reject(new Error('Fonte sintetica irraggiungibile')); }
    : name === 'decisions' ? () => Promise.resolve({ decisions: [] })
    : () => new Promise(() => {}) }) };
  const render = retainedPage('pages/Dashboard.tsx', {}, api);
  try {
    render('it'); await render.runEffects();
    const it = render('it').html, en = render('en').html;
    assert.equal(portfolioCalls, 1);
    assert.ok(it.includes('Dati non disponibili'));
    assert.ok(en.includes('Data unavailable'));
    assert.ok(it.includes('Fonte sintetica irraggiungibile'));
    assert.ok(en.includes('Fonte sintetica irraggiungibile'));
    assert.doesNotMatch(en, /Loading terminal data/);
  } finally { render.close(); }
});

test('Dashboard failed decisions and risk remain visible with a usable portfolio and do not claim zero pending actions', async () => {
  const calls = {};
  const api = { Bellomberg: new Proxy({}, { get: (_target, name) => () => {
    calls[name] = (calls[name] || 0) + 1;
    if (name === 'portfolio') return Promise.resolve({ cash_source: 'sqlite:cash_state', cash_disponibile_eur: 100,
      nav_total_eur: 1100, totale_valore_mercato_eur: 1000, totale_pl_eur: 0, positions: [] });
    if (name === 'decisions' || name === 'portfolioRisk') return Promise.reject(new Error(`Dettaglio sintetico ${name}`));
    return new Promise(() => {});
  } }) };
  const render = retainedPage('pages/Dashboard.tsx', {}, api);
  try {
    render('it'); await render.runEffects();
    for (const selected of ['it', 'en']) {
      const html = render(selected).html;
      assert.ok(html.includes('Dettaglio sintetico decisions'));
      assert.ok(html.includes('Dettaglio sintetico portfolioRisk'));
      assert.doesNotMatch(html, /NESSUNA AZIONE IN ATTESA|NO PENDING ACTIONS/);
      assert.ok(html.includes(selected === 'it' ? 'Dati non disponibili' : 'Data unavailable'));
    }
    assert.equal(calls.portfolio, 1); assert.equal(calls.decisions, 1); assert.equal(calls.portfolioRisk, 1);
  } finally { render.close(); }
});

test('Performance renders authored cached notes in the selected language without requesting recalculation', () => {
  const raw = { ...fixture(), notes: ['Serie ufficiale coperta'],
    _presentation_v1: { version: 1, texts: [{ path: ['notes', 0], it: 'Serie ufficiale coperta', en: 'Covered official series' }] } };
  const before = structuredClone(raw);
  const render = retainedPage('pages/PerformancePage.tsx', { 5: raw });
  const it = render('it'), en = render('en'), again = render('it');
  assert.match(it.html, /Serie ufficiale coperta/);
  assert.match(en.html, /Covered official series/);
  assert.doesNotMatch(en.html, /Serie ufficiale coperta/);
  assert.doesNotMatch(en.html, /ORIGINAL SOURCE TEXT|original source text/);
  assert.deepEqual(raw, before);
  assert.deepEqual(it.charts.map(p => p.bars), en.charts.map(p => p.bars));
  assert.deepEqual(again.charts.map(p => p.bars), it.charts.map(p => p.bars));
});

test('Dashboard renders cached TWR presentation without translating unmarked source text', () => {
  const raw = { ...fixture(), notes: ['Serie ufficiale coperta', 'Testo fonte intatto'],
    _presentation_v1: { version: 1, texts: [{ path: ['notes', 0], it: 'Serie ufficiale coperta', en: 'Covered official series' }] } };
  const seed = { 0: { cash_source: 'sqlite:cash_state', cash_disponibile_eur: 100, nav_total_eur: 1112.5,
    totale_valore_mercato_eur: 1012.5, totale_pl_eur: 12.5, positions: [], n_positions: 0 },
    4: false, 11: raw, 13: false, 15: 102 };
  const render = retainedPage('pages/Dashboard.tsx', seed);
  const it = render('it'), en = render('en');
  assert.match(it.html, /Serie ufficiale coperta/);
  assert.match(en.html, /Covered official series/);
  assert.match(en.html, /Testo fonte intatto/);
  assert.doesNotMatch(en.html, /Serie ufficiale coperta/);
  assert.deepEqual(raw.notes, ['Serie ufficiale coperta', 'Testo fonte intatto']);
});

test('advanced metric risk-free fallback is visible and localized, with its measured value unchanged', () => {
  const raw = { risk_free_used: .03, risk_free_status: 'fallback', risk_free_source: 'advanced_metrics.static_fallback',
    risk_free_note: 'Ripiego statico 3%: fonte assente',
    _presentation_v1: { version: 1, texts: [{ path: ['risk_free_note'], it: 'Ripiego statico 3%: fonte assente', en: 'Static 3% fallback: source unavailable' }] } };
  const render = retainedPage('pages/PerformancePage.tsx', { 6: raw });
  assert.match(render('it').html, /Ripiego statico 3%: fonte assente/);
  assert.match(render('en').html, /Static 3% fallback: source unavailable/);
  assert.equal(raw.risk_free_used, .03);
});

// c5 g2 (Claude Opus 5): expected sentences are frozen here, not read from the catalogs under test.
const flat = html => html.replace(/<!-- -->/g, '');
const around = (html, anchor) => { const at = html.indexOf(anchor); return at < 0 ? `missing anchor ${anchor}` : html.slice(at, at + 260); };

test('NAV history gap shows the real import script, dry run first and the app closed before --apply', () => {
  const it = flat(renderPage('pages/PerformancePage.tsx', 'it'));
  const en = flat(renderPage('pages/PerformancePage.tsx', 'en'));
  assert.ok(it.includes('Registra i trade dalla pagina Inserimento operazioni, o importali dalla cartella del progetto con <code>python tools/ops/importa_trade_csv.py miei_trade.csv</code>'), around(it, 'text-muted'));
  assert.ok(it.includes(': senza --apply è una prova che non registra trade. Per scrivere aggiungi --apply con l’app chiusa (se il backend risponde sulla porta 8765 lo script si ferma), poi riapri l’app. L’import non aggiorna la cassa: allineala dalla pagina Inserimento operazioni.'), around(it, '</code>'));
  assert.ok(en.includes('Record trades on the Trade Entry page, or import them from the project folder with <code>python tools/ops/importa_trade_csv.py my_trades.csv</code>'), around(en, 'text-muted'));
  assert.ok(en.includes(': without --apply it is a dry run that records no trades. To write, add --apply with the app closed (the script stops if the backend responds on port 8765), then reopen the app. The import does not update cash: reconcile it on the Trade Entry page.'), around(en, '</code>'));
  for (const html of [it, en]) {
    assert.doesNotMatch(html, /scripts\/importa_trade_csv|--apply<\/code>|Cassa\/Trade|Cash\/Trade|poi refresh|then refresh/);
  }
});

test('Performance reconciliation, VS SPY caption and benchmark chip follow the language with unchanged numbers', () => {
  const twr = { ...fixture(), metrics: { twr_total_pct: 2 },
    reconciliation: { nav_live_eur: 1112.5, last_snapshot_date: '2026-09-11', last_snapshot_nav_eur: 1110, delta_pct: 0.2, note: '' } };
  const benchmark = { dates: fixture().dates, index: [100, 100.5, 101], carried_flags: [false, true, false], ticker: 'SPY',
    base_date: '2026-09-10', leading_dropped: 2, coverage_pct: 99.5, carried_days: 1 };
  const official = retainedPage('pages/PerformancePage.tsx', { 5: twr, 13: benchmark });
  const it = flat(official('it').html), en = flat(official('en').html);
  assert.ok(it.includes(' vs snapshot del 2026-09-11 '), around(it, 'NAV attuale'));
  assert.ok(en.includes(' vs 2026-09-11 snapshot '), around(en, 'Live NAV'));
  assert.ok(it.includes('PORTAFOGLIO +2,00% · SPY € +1,00% · TR UFF. DAL 10/09'), around(it, 'PORTAFOGLIO +'));
  assert.ok(en.includes('BOOK +2.00% · SPY € +1.00% · OFFICIAL TR SINCE 10/09'), around(en, 'BOOK +'));
  assert.ok(it.includes('BENCH SPY TR · COPERT. 99,5% · CARRY 1G'), around(it, 'BENCH SPY'));
  assert.ok(en.includes('BENCH SPY TR · COV 99.5% · CARRY 1D'), around(en, 'BENCH SPY'));
  assert.doesNotMatch(en, /TR UFF\.|CALC PROVV\.| DA 10\/09|COPERT\./);
  assert.doesNotMatch(it, /vs snapshot 2026|TR · COV /);
  const day = d => Math.floor(Date.parse(d + 'T00:00:00Z') / 1000);
  const provisional = retainedPage('pages/PerformancePage.tsx', { 5: twr, 14: [{ t: day('2026-09-10'), v: 100 }, { t: day('2026-09-11'), v: 101 }, { t: day('2026-09-12'), v: 102 }] });
  const itProvisional = flat(provisional('it').html), enProvisional = flat(provisional('en').html);
  assert.ok(itProvisional.includes('PORTAFOGLIO +2,00% · SPY € +2,00% · CALC PROVV.'), around(itProvisional, 'PORTAFOGLIO +'));
  assert.ok(enProvisional.includes('BOOK +2.00% · SPY € +2.00% · PROVISIONAL CALC'), around(enProvisional, 'BOOK +'));
  assert.doesNotMatch(enProvisional, /CALC PROVV\.|PROVISIONAL CALC SINCE/);
});

test('Dashboard risk telemetry counts assets with singular/plural and localizes the 1-day/1-year windows', () => {
  const risk = n => ({ timestamp: '2026-09-12T10:00:00', nav_eur: 1112.5, n_assets_analyzed: n, lookback_days: 252, alerts: [],
    portfolio: { vol_annual_pct: 12, sharpe: 1.1, var_95_1d_pct: -1.5, var_99_1d_pct: -2.5, var_95_1d_eur: 0, var_99_1d_eur: 0, beta_vs_spy: 0.9, max_dd_1y_pct: -8 } });
  const seed = n => ({ 0: { cash_source: 'sqlite:cash_state', cash_disponibile_eur: 100, nav_total_eur: 1112.5,
    totale_valore_mercato_eur: 1012.5, totale_pl_eur: 12.5, positions: [], n_positions: 0 }, 2: risk(n), 4: false });
  const many = retainedPage('pages/Dashboard.tsx', seed(12)), one = retainedPage('pages/Dashboard.tsx', seed(1));
  const itMany = flat(many('it').html), enMany = flat(many('en').html);
  const itOne = flat(one('it').html), enOne = flat(one('en').html);
  assert.ok(itMany.includes('· 12 ASSET · 252G'), around(itMany, 'TELEMETRIA'));
  assert.ok(enMany.includes('· 12 ASSETS · 252D'), around(enMany, 'TELEMETRY'));
  assert.ok(itOne.includes('· 1 ASSET · 252G'), around(itOne, 'TELEMETRIA'));
  assert.ok(enOne.includes('· 1 ASSET · 252D'), around(enOne, 'TELEMETRY'));
  assert.doesNotMatch(enOne, /1 ASSETS/);
  for (const [html, day, year] of [[itMany, '1G', '1A'], [enMany, '1D', '1Y']]) {
    assert.ok(html.includes(`>VaR 95 ${day}<`) && html.includes(`>VaR 99 ${day}<`), 'VaR window ' + day);
    assert.equal(html.split(`>Max DD ${year}<`).length - 1, 2, 'bar and NAV row ' + year);
  }
  assert.doesNotMatch(itMany, /VaR 9\d 1D|Max DD 1Y/);
});
