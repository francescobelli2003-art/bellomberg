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

test('quota missing and non-finite figures use the selected language while valid figures stay unchanged', () => {
  const q = quota.leggiQuota(fixture()), head = quota.cimaF1(q, 1112.5, '2026-09-12');
  for (const [lang, missing, valid] of [['it', 'n.d.', '102,00'], ['en', 'n/a', '102.00']]) {
    inLanguage(lang, () => {
      assert.equal(quota.formattaCifra({ ...head, numerabile: false }, 0), missing);
      for (const value of [NaN, Infinity, -Infinity]) assert.equal(quota.formattaCifra(head, value), missing);
      assert.equal(quota.formattaCifra(head, 102), valid);
    });
  }
});

test('the rendered run dial uses singular only for one call and preserves declared missing totals', () => {
  const Quadrante = load('components/Quadrante.tsx').default;
  const render = (lang, p) => inLanguage(lang, () => renderToStaticMarkup(React.createElement(Quadrante, {
    p, w: 1000, h: 700, cursor: 0, pinned: false, onCursor() {}, onPin() {}, koIds: [],
    fmtEur: () => '0', costoRun: 0, koCost: null, memoLabel: 'Synthetic memo',
  })));
  for (const count of [0, 1, 2]) {
    const state = { running: false, start_time: '2026-09-12T10:00:00', completed_at: '2026-09-12T10:02:00', tool_log: [], n_tool_calls: count };
    const p = plancia.derivePlancia(state, [], undefined), before = structuredClone(p);
    for (const lang of ['it', 'en']) {
      const unit = lang === 'it' ? (count === 1 ? 'chiamata' : 'chiamate') : (count === 1 ? 'call' : 'calls');
      assert.ok(render(lang, p).includes(` · ${count} ${unit}</text>`));
    }
    assert.deepEqual(p, before);
  }
  const fromLog = plancia.derivePlancia({ running: false, start_time: '2026-09-12T10:00:00', completed_at: '2026-09-12T10:02:00',
    tool_log: [{ time: '10:00:05', specialist: 'synthetic', round: 1, tool: 'synthetic_tool', input: 'Original input' }] }, [], undefined);
  assert.equal(fromLog.nCallsTot, null);
  assert.ok(render('it', fromLog).includes(' · 1 chiamata</text>'));
  assert.ok(render('en', fromLog).includes(' · 1 call</text>'));
  const missing = { ...plancia.derivePlancia(null, [], undefined), logTappato: true };
  assert.match(render('it', missing), /chiamate: totale n\.d\./);
  assert.match(render('en', missing), /calls: total n\/a/);
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
  assert.match(renderPage('pages/Dashboard.tsx', 'it'), /Carico il portafoglio…/);
  const en = renderPage('pages/Dashboard.tsx', 'en');
  assert.match(en, /Loading the portfolio…/);
  assert.doesNotMatch(en, /⟦dashboard\./);
});

// Performance state slots (c.f. PerformancePage.tsx): 0 NAV history, 1 retired slot (drawdowns), 2 liquidity (restored 04/10),
// 3 concentration, 4 VaR contribution, 5 TWR, 6 advanced metrics, 7 loading, 8 load failure, 9 period, 10 updated,
// 11 view, 12-14 attribution (payload, loading, failure), 15 risk, 16 tearsheet, 17 portfolio snapshot,
// 18-20 benchmark (official, fallback, failure), 21 Monte Carlo scenarios, 22 Method panel open.
test('Performance switches empty-state instructions and period labels without translated provider IDs', () => {
  const it = renderPage('pages/PerformancePage.tsx', 'it', { 11: 'tearsheet' });
  const en = renderPage('pages/PerformancePage.tsx', 'en', { 11: 'tearsheet' });
  assert.match(it, /Storico NAV non disponibile/);
  assert.match(en, /NAV history unavailable/);
  assert.match(it, />Da inizio anno</); assert.match(it, />Scheda</);
  assert.match(en, />Year to date</); assert.match(en, />Overview</);
  assert.doesNotMatch(en, /⟦/); assert.doesNotMatch(it, /⟦/);
});

test('all five risk endpoints expose their KO details in both languages instead of disappearing', () => {
  const sources = { 3: '/portfolio/analytics/concentration', 4: '/portfolio/analytics/var_contribution', 6: '/portfolio/metrics/advanced', 15: '/portfolio/risk', 16: '/portfolio/tearsheet' };
  const seed = Object.fromEntries(Object.keys(sources).map(index => [index, { error: `Dettaglio originale sintetico ${index}` }]));
  for (const selected of ['it', 'en']) {
    const html = renderPage('pages/PerformancePage.tsx', selected, { ...seed, 22: true });
    for (const [index, path] of Object.entries(sources)) {
      assert.ok(html.includes(`Dettaglio originale sintetico ${index}`), `visible endpoint ${index} in ${selected}`);
      assert.ok(html.includes(path), `source path ${path} in ${selected}`);
    }
    assert.ok(html.includes(selected === 'it' ? 'Dati non disponibili' : 'Data unavailable'));
  }
});

// Declared hook simulation retains memo dependencies between language renders.
// Performance's state slots are listed above the first Performance test.
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

// Dashboard state slots: 0 portfolio, 4 loading, 6 run notice, 10 TWR payload, 12 TWR in flight.
test('Dashboard relabels a retained quote/curve memo and old error state without replacing source content', () => {
  const note = 'Messaggio originale da conservare';
  const seed = { 0: { cash_source: 'sqlite:cash_state', cash_disponibile_eur: 100, nav_total_eur: 1112.5,
    totale_valore_mercato_eur: 1012.5, totale_pl_eur: 12.5, positions: [], n_positions: 0 },
    4: false, 6: { kind: 'error', detail: note }, 10: fixture(), 12: false };
  const render = retainedPage('pages/Dashboard.tsx', seed);
  const it = render('it').html, en = render('en').html;
  assert.match(it, /Valore quota: 102,00/);
  assert.match(en, /Unit value: 102\.00/);
  assert.match(it, /Regime: Ufficiale dal /);
  assert.match(en, /Regime: Official since /);
  assert.doesNotMatch(it + en, /Regime: (official|reconstructed)/);
  assert.ok(en.includes(note));
  assert.ok(it.includes('Errore: ' + note));
  assert.ok(en.includes('Error: ' + note));
  assert.equal(render('it').html, it);
});

const linea = html => (html.match(/class="perf-line" d="([^"]+)"/) || [])[1];
test('Performance keeps the chart series while monthly captions and an earlier benchmark KO switch language', () => {
  const benchmark = { dates: fixture().dates, index: [100, 100.5, 101], carried_flags: [false, true, false], ticker: 'SPY' };
  const render = retainedPage('pages/PerformancePage.tsx', { 5: fixture(), 11: 'tearsheet', 18: benchmark, 9: 'Tutto' });
  const it = render('it'), en = render('en');
  assert.ok(it.html.includes('Rendimenti mensili') && it.html.includes('Portafoglio'));
  assert.ok(en.html.includes('Monthly returns') && en.html.includes('Portfolio'));
  assert.ok(linea(it.html), 'TWR line drawn');
  assert.equal(linea(it.html), linea(en.html));
  const failed = retainedPage('pages/PerformancePage.tsx', { 5: fixture(), 11: 'tearsheet', 20: { kind: 'fallback', why: { kind: 'restart' }, stage: 'alignment', detail: null } });
  assert.match(failed('it').html, /SPY non disponibile: serie uff\. si accende al riavvio backend/);
  assert.match(failed('en').html, /SPY unavailable: official series enabled after backend restart/);
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
  const render = retainedPage('pages/PerformancePage.tsx', { 5: raw, 11: 'tearsheet', 9: 'Tutto', 22: true });
  const it = render('it'), en = render('en'), again = render('it');
  assert.match(it.html, /Serie ufficiale coperta/);
  assert.match(en.html, /Covered official series/);
  assert.doesNotMatch(en.html, /Serie ufficiale coperta/);
  assert.doesNotMatch(en.html, /ORIGINAL SOURCE TEXT|original source text/);
  assert.deepEqual(raw, before);
  assert.equal(linea(it.html), linea(en.html));
  assert.equal(linea(again.html), linea(it.html));
});

test('Dashboard renders cached TWR presentation without translating unmarked source text', () => {
  const raw = { ...fixture(), notes: ['Serie ufficiale coperta', 'Testo fonte intatto'],
    _presentation_v1: { version: 1, texts: [{ path: ['notes', 0], it: 'Serie ufficiale coperta', en: 'Covered official series' }] } };
  const seed = { 0: { cash_source: 'sqlite:cash_state', cash_disponibile_eur: 100, nav_total_eur: 1112.5,
    totale_valore_mercato_eur: 1012.5, totale_pl_eur: 12.5, positions: [], n_positions: 0 },
    4: false, 10: raw, 12: false };
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
  const render = retainedPage('pages/PerformancePage.tsx', { 6: raw, 22: true });
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

test('Performance reconciliation, SPY since inception and benchmark source follow the language with unchanged numbers', () => {
  const twr = { ...fixture(), metrics: { twr_total_pct: 2 },
    reconciliation: { nav_live_eur: 1112.5, last_snapshot_date: '2026-09-11', last_snapshot_nav_eur: 1110, delta_pct: 0.2, note: '' } };
  const benchmark = { dates: fixture().dates, index: [100, 100.5, 101], carried_flags: [false, true, false], ticker: 'SPY',
    base_date: '2026-09-10', leading_dropped: 2, coverage_pct: 99.5, carried_days: 1, src: 'benchmark_series' };
  const official = retainedPage('pages/PerformancePage.tsx', { 5: twr, 11: 'tearsheet', 18: benchmark, 22: true });
  const it = flat(official('it').html), en = flat(official('en').html);
  assert.ok(it.includes('Ultimo snapshot (2026-09-11)'), around(it, 'NAV live'));
  assert.ok(en.includes('Last snapshot (2026-09-11)'), around(en, 'Live NAV'));
  assert.ok(it.includes('1.112,50 €') && en.includes('1,112.50 €'));
  assert.ok(it.includes('SPY +1,00% · +1,00 pt'), around(it, 'Dall’inizio'));
  assert.ok(en.includes('SPY +1.00% · +1.00 pt'), around(en, 'Since inception'));
  assert.ok(it.includes('99,5%') && en.includes('99.5%'));
  assert.ok(it.includes('benchmark_series') && !it.includes('CALC'));
  const day = d => Math.floor(Date.parse(d + 'T00:00:00Z') / 1000);
  const provisional = retainedPage('pages/PerformancePage.tsx', { 5: twr, 11: 'tearsheet', 22: true, 19: [{ t: day('2026-09-10'), v: 100 }, { t: day('2026-09-11'), v: 101 }, { t: day('2026-09-12'), v: 102 }] });
  const itProvisional = flat(provisional('it').html), enProvisional = flat(provisional('en').html);
  assert.ok(itProvisional.includes('SPY +2,00% · 0,00 pt'), around(itProvisional, 'Dall’inizio'));
  assert.ok(itProvisional.includes('SPY × EURUSD · CALC') && enProvisional.includes('SPY × EURUSD · CALC'));
});

test('Performance short history: Sharpe is declared too short instead of a number (Review Focus 1)', () => {
  const short = { dates: ['2026-09-25', '2026-09-28', '2026-09-29', '2026-09-30', '2026-10-01', '2026-10-02'],
    twr_index: [100, 101, 100.5, 102, 101.5, 103], values_eur: [1000, 1010, 1005, 1020, 1015, 1030], flows_eur: [0, 0, 0, 0, 0, 0],
    regimes: Array(6).fill('official'), metrics: { twr_total_pct: 3, risk_free_used: 0.032 } };
  for (const [lang, hint] of [['it', 'Periodo troppo breve'], ['en', 'Period too short']]) {
    const html = flat(renderPage('pages/PerformancePage.tsx', lang, { 5: short, 9: 'Tutto', 11: 'tearsheet' }));
    assert.match(html, new RegExp(`<b title="${hint}[^"]*">—</b>`), around(html, 'Sharpe'));
    assert.ok(html.includes('+3,00%') || html.includes('+3.00%'));
  }
});

test('Performance without a benchmark keeps the chart and disables the SPY toggle (Review Focus 2)', () => {
  const html = renderPage('pages/PerformancePage.tsx', 'it', { 5: fixture(), 9: 'Tutto', 11: 'tearsheet' });
  assert.ok(linea(html), 'TWR line drawn without SPY');
  assert.match(html, /<button type="button" class="bbn-toggle-chip" aria-pressed="false" disabled=""[^>]*>.*?SPY<\/button>/);
});

test('Performance scenarios fail one by one with their own retry (Review Focus 3)', () => {
  const ok = { base_nav_eur: 6000, percentiles_eur: { p5: 5166, p50: 5950 }, median_return_pct: -0.83, prob_loss_10pct: 13.3, es_95_eur: -1023 };
  for (const [lang, retry, median] of [['it', 'Riprova', 'Perdita mediana'], ['en', 'Retry', 'Median loss']]) {
    const html = flat(renderPage('pages/PerformancePage.tsx', lang, { 11: 'risk', 21: { none: ok, gfc_2008: { error: 'boom sintetico' } } }));
    const tile = id => html.slice(html.indexOf(`data-scenario="${id}"`), html.indexOf('data-scenario=', html.indexOf(`data-scenario="${id}"`) + 20));
    assert.match(tile('gfc_2008'), /boom sintetico/); assert.ok(tile('gfc_2008').includes(retry));
    assert.ok(tile('none').includes(median)); assert.ok(tile('none').includes('−834 €'.replace(',', lang === 'en' ? '.' : ',')) || tile('none').includes('834'));
  }
});



test('Performance monthly heatmap colours cells with the RGB heat tokens the Dashboard uses', () => {
  const html = renderPage('pages/PerformancePage.tsx', 'it', { 5: fixture(), 9: 'Tutto', 11: 'tearsheet' });
  assert.match(html, /background:rgba\(var\(--bbn-heat-up\), ?0?\.\d+\)/);
  assert.doesNotMatch(html, /color-mix\(in srgb, var\(--bbn-heat/);
});

test('Performance chart has no drawdown band under it (removed on request, 02/10/2026)', () => {
  const html = renderPage('pages/PerformancePage.tsx', 'it', { 5: fixture(), 9: 'Tutto', 11: 'tearsheet' });
  assert.ok(html.includes('class="perf-line"'), 'TWR line still drawn');
  assert.doesNotMatch(html, /perf-ddline|perf-chart-dd|Drawdown nel periodo/);
});

test('Performance Rischio histogram labels its VaR lines in HTML, not as stretched SVG text', () => {
  const twr = { dates: ['2026-09-28', '2026-09-29', '2026-09-30', '2026-10-01'], twr_index: [100, 99, 100.5, 99.8],
    values_eur: [1000, 990, 1005, 998], flows_eur: [0, 0, 0, 0], regimes: Array(4).fill('official'), metrics: { twr_total_pct: -0.2 } };
  const risk = { lookback_days: 249, portfolio: { vol_annual_pct: 30, sharpe: 1, var_95_1d_pct: -0.5, var_99_1d_pct: -0.8, var_95_1d_eur: -5, var_99_1d_eur: -8, beta_vs_spy: 0.9, max_dd_1y_pct: -10 } };
  const html = renderPage('pages/PerformancePage.tsx', 'it', { 5: twr, 11: 'risk', 15: risk });
  const hist = html.slice(html.indexOf('perf-hist-chart'), html.indexOf('perf-axis', html.indexOf('perf-hist-chart')));
  assert.ok(hist.includes('<rect'), 'bars drawn');
  assert.doesNotMatch(hist, /<text/);
  assert.match(hist, /class="perf-var-tag"[^>]*>VaR 95% −5 €</);
});

test('Performance drawdown table separates the peak, the trough, the depth and where the portfolio is today', () => {
  const tear = { drawdowns: { n_episodes_total: 2,
    top: [{ start_date: '2026-06-22', trough_date: '2026-07-29', depth_pct: -21.31, days_to_trough: 27, recovery_date: null, days_total: 72, open: true },
      { start_date: '2026-06-01', trough_date: '2026-06-11', depth_pct: -11.81, days_to_trough: 8, recovery_date: '2026-06-19', days_total: 14, open: false }],
    current: { start_date: '2026-06-22', trough_date: '2026-07-29', depth_pct: -21.31, days_to_trough: 27, days_total: 72, open: true, current_dd_pct: -9.55 } } };
  const it = flat(renderPage('pages/PerformancePage.tsx', 'it', { 11: 'risk', 16: tear }));
  const en = flat(renderPage('pages/PerformancePage.tsx', 'en', { 11: 'risk', 16: tear }));
  for (const [html, head, trough, now, rec] of [[it, 'Dal massimo', '29 lug', 'in corso · oggi −9,55%', '6 sedute · 19 giu'],
    [en, 'From peak', '29 Jul', 'ongoing · today −9.55%', '6 sessions · 19 Jun']]) {
    assert.ok(html.includes(`<th>${head}</th>`), around(html, 'perf-ep'));
    assert.ok(html.includes(trough), around(html, 'perf-ep'));
    assert.ok(html.includes(now), around(html, 'perf-ep'));
    assert.ok(html.includes(rec), around(html, 'perf-ep'));
  }
});

// 04/10/2026 (G9b): la tabella Liquidity scores torna nella vista Rischio; il KO del servizio si vede.
test('Performance Rischio fetches /portfolio/analytics/liquidity again and renders the table from its payload', async () => {
  const calls = [];
  const liquidity = { items: [{ ticker: 'ZZTEST', position_eur: 5000, avg_daily_volume_eur: 100, days_to_liquidate: 250, score: 'red' }],
    n_green: 0, n_yellow: 0, n_red: 1, threshold_green_days: 1, threshold_yellow_days: 5, assumption_pct_of_volume: 0.2, note: 'nota sintetica' };
  const api = { Bellomberg: new Proxy({}, { get: (_t, name) => async () => {
    calls.push(name);
    if (name === 'liquidity') return liquidity;
    throw new Error('sintetico ' + String(name));
  } }) };
  const render = retainedPage('pages/PerformancePage.tsx', { 11: 'risk' }, api);
  render('it'); await render.runEffects();
  const html = render('it').html;
  render.close();
  assert.ok(calls.includes('liquidity'), 'liquidity requested: ' + calls.join(','));
  assert.match(html, /data-qa="perf-liquidity"/);
  assert.match(html, /ZZTEST/); assert.match(html, /250,00 g/);
});

test('Performance liquidity KO is declared in the card and in Method sources, in both languages', () => {
  for (const selected of ['it', 'en']) {
    const html = renderPage('pages/PerformancePage.tsx', selected, { 2: { error: 'Dettaglio liquidita sintetico' }, 11: 'risk', 22: true });
    assert.ok(html.includes('/portfolio/analytics/liquidity'), 'source path in ' + selected);
    assert.ok(html.split('Dettaglio liquidita sintetico').length >= 3, 'detail in card and in Method (' + selected + ')');
  }
});

// 04/10/2026 (G9b): la card Allocazione non dice «100% investito» quando la cassa manca o è negativa.
test('Dashboard allocation: missing cash is n/a with its reason, negative cash shows invested above 100%', () => {
  const carica = creaCaricatore({ stub: { '@/lib/api': { Bellomberg: { concentration: () => new Promise(() => {}) } } } });
  const language = carica('i18n/lingua.ts');
  const Card = carica('pages/dashboard/CardAllocazione.tsx').default;
  const posizioni = [{ ticker: 'ZZTEST', nome: 'Zeta Test', valore_mercato: 8400, peso_pct: 84, valuta: 'EUR' },
    { ticker: 'ACME.MI', nome: 'Acme', valore_mercato: 2100, peso_pct: 21, valuta: 'EUR' }];
  const render = (lang, liquidita, patrimonio, motivoNd = null) => {
    language.impostaLinguaCorrente(lang);
    return renderToStaticMarkup(React.createElement(Card, { posizioni, patrimonio, liquidita, motivoNd, vista: 'titoli',
      onVista() {}, metrica: 'totale', onApri() {} }));
  };
  const negativa = render('it', -500, 10000);
  assert.match(negativa, /<b class="num">105%<\/b>/);
  assert.match(negativa, /Liquidità negativa: −?-?5,0% del patrimonio/);
  assert.doesNotMatch(negativa, /<b class="num">100%<\/b>/);
  const assente = render('it', null, 10000, 'cassa sintetica illeggibile');
  assert.match(assente, /<b class="num">n\.d\.<\/b>/);
  assert.match(assente, /Liquidità n\.d\.: quota investita non misurabile \(cassa sintetica illeggibile\)/);
  const navNd = render('en', null, null, 'synthetic cash unreadable');
  assert.match(navNd, /Allocation n\/a: net asset value cannot be measured \(synthetic cash unreadable\)/);
  assert.doesNotMatch(navNd, /No positions/);
  const ok = render('it', 1000, 10000);
  assert.match(ok, /<b class="num">90%<\/b>/);
  language.impostaLinguaCorrente('it');
});

// 04/10/2026 (G9b): ITD n.d. torna col suo MOTIVO (regressione rispetto a e44e955).
test('Dashboard ITD n/a carries the reason of the failed nav_history read', async () => {
  const api = { Bellomberg: new Proxy({}, { get: (_target, name) => () => {
    if (name === 'portfolio') return Promise.resolve({ cash_source: 'sqlite:cash_state', cash_disponibile_eur: 100,
      nav_total_eur: 1100, totale_valore_mercato_eur: 1000, totale_pl_eur: 0, positions: [] });
    if (name === 'navHistory') return Promise.resolve({ error: 'Storico sintetico non ricostruibile' });
    if (name === 'decisions' || name === 'portfolioRisk') return Promise.reject(new Error(`sintetico ${name}`));
    return new Promise(() => {});
  } }) };
  const render = retainedPage('pages/Dashboard.tsx', {}, api);
  try {
    render('it'); await render.runEffects();
    const html = render('it').html;
    assert.match(html, /ITD[^]*Storico sintetico non ricostruibile/);
  } finally { render.close(); }
});

// 04/10/2026 (G9b): il grafico del patrimonio non usa preserveAspectRatio="none" (deforma) e resta interattivo.
test('Dashboard hero chart: no preserveAspectRatio="none", viewBox from the box, crosshair handler kept', () => {
  const JSX = require('react/jsx-runtime');
  const nodes = [];
  const wrap = kind => (type, props, key) => { nodes.push({ type, props }); return JSX[kind](type, props, key); };
  const carica = creaCaricatore({ stub: { 'react/jsx-runtime': { ...JSX, jsx: wrap('jsx'), jsxs: wrap('jsxs') } } });
  carica('i18n/lingua.ts').impostaLinguaCorrente('it');
  const Hero = carica('pages/dashboard/HeroPatrimonio.tsx').default;
  const date = ['2026-09-01', '2026-09-02', '2026-09-03', '2026-09-04'];
  const html = renderToStaticMarkup(React.createElement(Hero, { patrimonio: 1000, spy: null, dettagli: [], avvisi: [], periodo: 'Tutto',
    onPeriodo() {}, spyAcceso: false, onSpy() {},
    curva: { stato: 'viva', date, valori: [100, 101, 99, 102], euro: [1000, 1010, 990, 1020] },
    giorno: { eur: null, pct: null, multiDay: false, windowLabel: null, parziale: false } }));
  assert.match(html, /class="bbn-chart-svg"/);
  assert.doesNotMatch(html, /preserveAspectRatio/i);
  assert.match(html, /viewBox="0 0 1000 300"/);
  assert.ok(nodes.some(n => n.props?.className === 'bbn-chart' && typeof n.props.onMouseMove === 'function'), 'crosshair handler');
});

// 2026-10-05: the hero figure has two views. NAV (default) = unit value; Cash = euro of holdings + cash.
test('Dashboard hero: NAV (unit value) is the default view, Cash shows the euro total', () => {
  const carica = creaCaricatore();
  carica('i18n/lingua.ts').impostaLinguaCorrente('it');
  const Hero = carica('pages/dashboard/HeroPatrimonio.tsx').default;
  const base = { patrimonio: 1234.5, quota: { valore: 105, base: 100, dal: '2026-01-15' }, spy: null, dettagli: [], avvisi: [],
    periodo: 'Tutto', onPeriodo() {}, spyAcceso: false, onSpy() {},
    curva: { stato: 'viva', date: ['2026-01-15', '2026-02-16'], valori: [100, 105], euro: [1000, 1234.5] },
    giorno: { eur: null, pct: null, multiDay: false, windowLabel: null, parziale: false } };
  const valore = html => html.match(/class="bbn-hero-value num"[^>]*>(.*?)<\/div>/)[1].replace(/<[^>]+>/g, '');
  const nav = renderToStaticMarkup(React.createElement(Hero, base));
  assert.match(nav, /<button[^>]*aria-pressed="true"[^>]*>NAV</);
  assert.match(valore(nav), /^105,00valore quota · base 100 dal 15 gen 2026$/);
  assert.match(nav, /title="Cash: 1\.234,50\s€/);
  const cash = renderToStaticMarkup(React.createElement(Hero, { ...base, vista: 'cash' }));
  assert.match(cash, /<button[^>]*aria-pressed="true"[^>]*>Cash</);
  assert.match(valore(cash), /^1\.234,50\s€titoli \+ liquidità$/);
  const senzaQuota = renderToStaticMarkup(React.createElement(Hero, { ...base, quota: null }));
  assert.doesNotMatch(valore(senzaQuota), /€/, 'NAV view never falls back to the euro figure');
});

// Revisione G9b: ripieghi preesistenti chiusi (Performance snapshot e Sharpe, flussi senza importo).
test('Performance: a failed portfolio read is declared, never the old snapshot shown as current', async () => {
  let n = 0;
  const api = { Bellomberg: new Proxy({}, { get: (_t, name) => () => {
    if (name === 'portfolio') return Promise.reject(new Error('Portafoglio sintetico irraggiungibile'));
    n++; return new Promise(() => {});
  } }) };
  const render = retainedPage('pages/PerformancePage.tsx', { 11: 'tearsheet', 17: { positions: [], timestamp: '2026-10-01T10:00:00' } }, api);
  try {
    render('it'); await render.runEffects();
    const html = render('it').html;
    assert.match(html, /portafoglio non leggibile: Portafoglio sintetico irraggiungibile/);
  } finally { render.close(); }
});

test('Performance: Sharpe without a declared risk-free rate is n/a with the reason, never rf 0', () => {
  const twr = { ...fixture(), metrics: { twr_total_pct: 2 } };
  const html = renderPage('pages/PerformancePage.tsx', 'it', { 5: twr, 9: 'Tutto', 11: 'tearsheet' });
  assert.match(html, /tasso privo di rischio non dichiarato: Sharpe n\.d\./);
  assert.doesNotMatch(html, /risk-free 0,00%/);
  // serie abbastanza lunga da avere uno Sharpe: senza rf il numero NON si mostra
  const dates = [], idx = [], val = [];
  for (let d = new Date(Date.UTC(2026, 4, 4)); dates.length < 45; d.setUTCDate(d.getUTCDate() + 1)) {
    if (d.getUTCDay() % 6) { dates.push(d.toISOString().slice(0, 10)); idx.push(100 * (1 + 0.001 * dates.length) * (dates.length % 2 ? 1.003 : 1)); val.push(idx[idx.length - 1] * 10); }
  }
  const lunga = { dates, twr_index: idx, values_eur: val, flows_eur: dates.map(() => 0), regimes: dates.map(() => 'official'), metrics: { twr_total_pct: 5 } };
  const h2 = renderPage('pages/PerformancePage.tsx', 'it', { 5: lunga, 9: 'Tutto', 11: 'tearsheet' });
  assert.match(h2, /<b title="tasso privo di rischio non dichiarato: Sharpe n\.d\.">—<\/b>/);
  const conRf = renderPage('pages/PerformancePage.tsx', 'it', { 5: { ...lunga, metrics: { twr_total_pct: 5, risk_free_used: 0.02 } }, 9: 'Tutto', 11: 'tearsheet' });
  assert.match(conRf, /risk-free 2,00%/);
  assert.doesNotMatch(conRf, /Sharpe n\.d\./);
});

test('curve: a deposit without an amount is declared not counted, never «+0 €»', () => {
  const twr = { ...fixture(), external_flows: [{ date: '2026-09-12', type: 'DEPOSIT', amount_eur: null }] };
  const c = curva.leggiCurva(twr, 'quota', false, null);
  assert.equal(c.stato, 'viva');
  assert.deepEqual(c.versamenti, {});
  assert.match(c.ignoti['2026-09-12'], /DEPOSIT senza importo/);
});
