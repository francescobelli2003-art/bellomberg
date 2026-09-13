const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');
ambienteBrowser();

function fixtures() {
  const aggregate = { beta_market: 0.7, beta_smb: 0.2, beta_hml: -0.1, beta_rmw: 0.15, beta_cma: 0.05, beta_mom: 0.3, alpha_annualized_pct: 3.2 };
  return {
    fac: { period: '1y', n_holdings_analyzed: 1, n_holdings_skipped: 1, coverage_weight_pct: 80,
      portfolio_aggregate: aggregate, ff_data_last_date: '2026-06-01',
      per_holding: { 'SYNTH.X': { ticker: 'SYNTH.X', weight_pct: 80, n_obs: 200, alpha_annualized_pct: 3.2, alpha_tstat: 2.5, r_squared: 0.3, beta_market: 0.7, beta_market_tstat: 3, factor_region: 'synthetic', fx_caveat: 'Original unmarked caveat' } },
      regions: { synthetic: { label: 'Original region name', n_holdings: 1, weight_pct: 80 } },
      ff_data_by_region: { synthetic: { n_obs: 200, last_date: '2026-06-01' } },
      skipped_detail: [{ ticker: 'OMIT.X', weight_pct: 20, reason: 'Original missing history' }] },
    rec: { betas: { portfolio_risk_spy: 0.8, factor_model_mkt: 0.75, advanced_metrics_twr: 0.9 },
      definitions: { portfolio_risk_spy: 'Definizione dichiarata', factor_model_mkt: 'Original unmarked definition', advanced_metrics_twr: 'Original benchmark definition' },
      note: 'Nota dichiarata', verdict: 'RECONCILED', threshold: 0.25, max_spread: 0.15, beta_consensus: 0.82,
      _presentation_v1: { version: 1, texts: [
        { path: ['definitions', 'portfolio_risk_spy'], it: 'Definizione dichiarata', en: 'Declared definition' },
        { path: ['note'], it: 'Nota dichiarata', en: 'Declared note' },
      ] } },
    snap: { totale_valore_mercato_eur: 1234.5, cash_disponibile_eur: 100, nav_total_eur: 1334.5 },
    adv: { sharpe: 0.4, benchmark: { alpha_annual_pct: -2.1 }, benchmark_alignment: 'Original alignment' },
    risk: { portfolio: { sharpe: 0.9 }, sharpe_note: 'Original Sharpe note' },
  };
}

function retained(data = fixtures(), warm = 'ok', overrides = {}) {
  let state = 0, memoIndex = 0, effectIndex = 0, refIndex = 0, factorsReads = 0;
  const values = {}, memos = [], refs = [], effects = [], effectDeps = [], cleanups = [], calls = [];
  const memo = (compute, deps) => { const at = memoIndex++, before = memos[at];
    if (!before || !deps || deps.some((v, i) => !Object.is(v, before.deps[i]))) memos[at] = { deps, value: compute() };
    return memos[at].value; };
  const load = creaCaricatore({ stub: {
    react: { ...React, useEffect(effect, deps) { const at = effectIndex++, before = effectDeps[at];
      if (!before || !deps || deps.some((v, i) => !Object.is(v, before[i]))) effects.push(() => { cleanups[at]?.(); cleanups[at] = effect(); });
      effectDeps[at] = deps; }, useMemo: memo, useCallback: (fn, deps) => memo(() => fn, deps),
      useRef(initial) { const at = refIndex++; return refs[at] ||= { current: initial }; },
      useState(initial) { const at = state++; if (!(at in values)) values[at] = typeof initial === 'function' ? initial() : initial;
        return [values[at], value => { values[at] = typeof value === 'function' ? value(values[at]) : value; }]; } },
    '@/lib/api': { Bellomberg: {
      portfolio: async () => { calls.push('portfolio'); return data.snap; },
      metricsAdvanced: async () => { calls.push('advanced'); return data.adv; },
      portfolioRisk: async () => { calls.push('risk'); return data.risk; },
      portfolioFactors: async () => { calls.push(++factorsReads === 1 ? 'factors-1y' : 'riscalda-1y');
        if (factorsReads > 1 && warm === 'throw') throw { response: { data: { detail: [{ msg: 'Original warm failure' }] } } };
        return factorsReads > 1 && warm === 'error' ? { error: 'Original warm failure' } : data.fac; },
      betaReconcile: async () => { calls.push('beta_reconcile'); return data.rec; },
      portfolioFactorsPeriodo: async period => { calls.push('factors-' + period); return { ...data.fac, period }; },
      ...overrides,
    } },
  } });
  const language = load('i18n/lingua.ts'), Page = load('pages/FactorsPage.tsx').default;
  const render = selected => { state = memoIndex = effectIndex = refIndex = 0; effects.length = 0; language.impostaLinguaCorrente(selected); return renderToStaticMarkup(React.createElement(Page)); };
  render.effects = async () => { for (const effect of effects) effect(); await new Promise(resolve => setImmediate(resolve)); };
  return { render, calls, load };
}

test('factor helpers translate presentation and preserve identifiers, confidence and coverage measures', () => {
  const load = creaCaricatore(), language = load('i18n/lingua.ts'), factors = load('lib/fattori.ts'), data = fixtures();
  const observations = [];
  for (const lang of ['it', 'en']) {
    language.impostaLinguaCorrente(lang);
    observations.push({ interval: factors.intervallo(0.7, 3), coverage: factors.copertura(data.fac, data.snap), order: factors.ORDINE_CHIAMATE, ids: factors.confrontoFinestre(data.fac.portfolio_aggregate, data.fac.portfolio_aggregate).map(x => x.chiave) });
    assert.equal(factors.n(1234.5, 2), lang === 'it' ? '1.234,50' : '1,234.50');
    assert.match(factors.perche('t-assente'), lang === 'it' ? /t assente/ : /t missing/);
    assert.equal(factors.NOME_FATTORE.beta_market.lungo, lang === 'it' ? 'Mercato' : 'Market');
  }
  assert.deepEqual(observations[0], observations[1]);
  assert.equal(observations[0].coverage.effettiva, 80 * 1234.5 / 1334.5);
  assert.equal(observations[0].interval.sig, true);
  assert.equal(factors.intervallo(0.7, 0).lo, null);
});

test('Factors updates labels and authored backend variants without repeating its ordered expensive pipeline', async () => {
  const data = fixtures(), before = structuredClone(data), { render, calls } = retained(data);
  render('it'); await render.effects(); const it = render('it'); await render.effects();
  const en = render('en'); await render.effects();
  assert.match(it, /RICONCILIAZIONE MISURE/); assert.match(en, /MEASURE RECONCILIATION/);
  assert.match(it, /Nota dichiarata/); assert.match(en, /Declared note/); assert.match(en, /Declared definition/);
  for (const html of [it, en]) { assert.match(html, /Original unmarked definition/); assert.match(html, /Original missing history/); assert.match(html, /Original unmarked caveat/); assert.match(html, /RECONCILED/); }
  assert.deepEqual(calls.filter(c => !['portfolio', 'advanced', 'risk'].includes(c)), ['factors-1y', 'beta_reconcile', 'factors-3y', 'riscalda-1y']);
  assert.equal(calls.length, 7);
  const geometry = html => [...html.matchAll(/style="([^"]*(?:left|width):[^"]*)"/g)].map(m => m[1]);
  assert.ok(geometry(it).length > 15, 'exercise actual ruler and interval geometry');
  assert.deepEqual(geometry(it), geometry(en));
  assert.deepEqual(data, before);
});

for (const mode of ['throw', 'error']) test(`cache restoration ${mode} is explicit in both languages, retaining source detail`, async () => {
  const { render, calls } = retained(fixtures(), mode);
  render('it'); await render.effects();
  for (const lang of ['it', 'en']) { const html = render(lang); await render.effects();
    assert.match(html, lang === 'it' ? /RIPRISTINO CACHE NON CONFERMATO/ : /CACHE RESTORATION NOT CONFIRMED/);
    assert.match(html, /Original warm failure/);
  }
  assert.equal(calls.filter(x => x === 'riscalda-1y').length, 1);
});

test('an error without detail cannot turn a failed 3Y request into an endless loading state', async () => {
  const { render } = retained(fixtures(), 'ok', { portfolioFactorsPeriodo: async () => { throw { response: { data: { detail: '' } } }; } });
  render('it'); await render.effects();
  for (const lang of ['it', 'en']) {
    const html = render(lang);
    assert.match(html, lang === 'it' ? /FINESTRA 3Y NON DISPONIBILE/ : /3Y WINDOW UNAVAILABLE/);
    assert.match(html, lang === 'it' ? /Dettaglio dell’errore non fornito/ : /Error detail not provided/);
    assert.doesNotMatch(html, /FINESTRA 3Y IN ARRIVO|3Y WINDOW LOADING/);
  }
});

test('Sharpe sources are real backend routes and a missing spread is named like the SPREAD beside it', async () => {
  const fs = require('node:fs'), path = require('node:path');
  const routes = fs.readFileSync(path.resolve(__dirname, '../../../src/bellomberg/api/bellomberg_api.py'), 'utf8');
  const data = fixtures(); delete data.adv.sharpe;
  const { render } = retained(data);
  render('it'); await render.effects();
  // Frasi attese scritte qui: il riquadro del Duello dice SPREAD quando il numero c'e'.
  const expected = { it: '<span class="muto">spread non calcolabile</span>', en: '<span class="muto">spread cannot be calculated</span>' };
  for (const language of ['it', 'en']) {
    const html = render(language); await render.effects();
    assert.ok(html.includes(expected[language]), `${language}: ${expected[language]}`);
    assert.match(html, /<div class="sc num">SPREAD 5[.,]30 PP<\/div>/);
    const shown = [...html.matchAll(/<div class="fonte">(\/[^<]+)<\/div>/g)].map(m => m[1]);
    assert.deepEqual(shown, ['/PORTFOLIO/RISK', '/PORTFOLIO/METRICS/ADVANCED']);
    for (const route of shown) assert.ok(routes.includes(`@app.get("${route.toLowerCase()}")`), `route ${route} exists`);
  }
});

test('Factors rejects malformed backend presentation explicitly and keeps raw measures unchanged', async () => {
  const data = fixtures(), before = structuredClone(data);
  data.rec._presentation_v1.texts[0].path = ['betas', 'portfolio_risk_spy'];
  const { render, calls } = retained(data);
  render('it'); await render.effects();
  assert.throws(() => render('en'), /Invalid presentation metadata/);
  assert.equal(data.rec.betas.portfolio_risk_spy, before.rec.betas.portfolio_risk_spy);
  assert.equal(calls.length, 7);
});
