// Fixture-only interaction checks for the risk pages. No production API is used.
const assert = require('node:assert/strict');

const stamp = '2026-09-29T12:00:00Z';
const factors = {
  timestamp: stamp, version: 'synthetic-risk-v1', model: 'fixture OLS', method: 'fixture', data_source: 'synthetic fixture',
  period: '1y', n_holdings_analyzed: 2, n_holdings_skipped: 1, n_alpha_significant_5pct: 1,
  skipped_detail: [{ ticker: 'SYN3', weight_pct: 3, reason: 'Synthetic missing history.' }], coverage_weight_pct: 92,
  portfolio_aggregate: { alpha_annualized_pct: 2.4, beta_market: 1.05, beta_smb: .18, beta_hml: -.12, beta_rmw: .08, beta_cma: -.04, beta_mom: .21 },
  portfolio_avg_r_squared: .72, per_holding: {
    SYN1: { ticker: 'SYN1', weight_pct: 55, n_obs: 220, period: '1y', alpha_annualized_pct: 3.1, alpha_tstat: 2.4, alpha_pvalue: .02, r_squared: .74, factor_region: 'US', fx_caveat: null, beta_market: 1.1, beta_market_tstat: 3.2, beta_smb: .2, beta_hml: -.1, beta_rmw: .05, beta_cma: -.04, beta_mom: .18 },
    SYN2: { ticker: 'SYN2', weight_pct: 37, n_obs: 205, period: '1y', alpha_annualized_pct: -.8, alpha_tstat: -.6, alpha_pvalue: .5, r_squared: .65, factor_region: 'US', fx_caveat: 'Synthetic currency caveat.', beta_market: .9, beta_market_tstat: 2.3, beta_smb: -.1, beta_hml: .2, beta_rmw: .08, beta_cma: .01, beta_mom: .12 },
  }, regions: { US: { label: 'Synthetic US', n_holdings: 2, weight_pct: 92 } }, ff_data_last_date: '2026-09-25', ff_data_n_obs: 220,
  ff_data_by_region: { US: { n_obs: 220, last_date: '2026-09-25' } },
};
const reconcile = { betas: { portfolio_risk_spy: 1.08, advanced_metrics_twr: 1.03, factor_model_mkt: 1.05 },
  definitions: { portfolio_risk_spy: 'Synthetic portfolio risk beta', advanced_metrics_twr: 'Synthetic TWR beta', factor_model_mkt: 'Synthetic factor beta' },
  threshold: .2, max_spread: .05, verdict: 'reconciled', beta_consensus: 1.05, note: 'Synthetic reconciliation only.' };

const mcDays = Array.from({ length: 13 }, (_, i) => i * 21);
const mcFanBands = Object.fromEntries([['p5', .25], ['p10', .18], ['p25', .1], ['p50', 0], ['p75', .11], ['p90', .2], ['p95', .28]]
  .map(([key, spread]) => [key, mcDays.map(day => 76564 * (1 + .003 * day / 252 + spread * day / 252))]));
mcFanBands.days = mcDays;
const mcResult = {
  timestamp: stamp, version: 'synthetic-risk-v1', method: 'fhs', method_description: 'Synthetic fixture', drift_mode: 'shrinkage',
  stress_scenario: 'none', stress_requested: 'none', stress_fallback: false, calibration_note: 'Fixture only', returns_basis: 'synthetic_fixture',
  lookback_years: 5, lookback_days_calibration: 1260, n_sims: 3000, horizon_days: 252, horizon_years: 1, n_assets: 2,
  tickers_analyzed: ['SYN1', 'SYN2'], removed_tickers: [], added_tickers: [], weights: { SYN1: .55, SYN2: .45 }, base_nav_eur: 76564.57,
  percentiles_ratio: { p1: .7, p5: .82, p10: .88, p25: .94, p50: 1.04, p75: 1.12, p90: 1.2, p95: 1.25, p99: 1.33 },
  percentiles_eur: { p1: 53600, p5: 62800, p10: 67400, p25: 72000, p50: 79600, p75: 85800, p90: 91900, p95: 95700, p99: 101800 },
  expected_return_pct: 4.2, median_return_pct: 4, stdev_pct: 12, sharpe_simulated: .35, prob_negative_pct: 28,
  prob_loss_10pct: 20, prob_loss_20pct: 5, prob_gain_10pct: 30, prob_gain_20pct: 9, var_95_pct: 17, var_99_pct: 28,
  es_95_pct: 21, es_99_pct: 33, es_95_eur: 16000, es_99_eur: 25000, max_drawdown_p5_pct: -25, max_drawdown_median_pct: -10,
  max_drawdown_p95_pct: -3, sample_paths: Array.from({ length: 8 }, (_, p) => Array.from({ length: 13 }, (_, i) => 76564 * (1 + i * .003 + Math.sin((i + p) * .5) * .018))),
  sample_paths_days: Array.from({ length: 13 }, (_, i) => i * 21), sample_paths_n: 8,
  fan_bands: mcFanBands,
  terminal_hist: { counts: [2, 8, 18, 40, 70, 112, 180, 218, 177, 103, 48, 16, 5], edges_eur: Array.from({ length: 14 }, (_, i) => 52000 + i * 4000) },
  modifications_applied: [], skipped_modifications: [],
};

const edgePayload = { signals: [
  { ticker: 'SYN1', category: 'momentum', name: 'Synthetic momentum', value: 1.4, context: 'Fixture-only signal.', direction: 'bullish', strength: 78, reading: 'Synthetic reading.', source: 'fixture' },
  { ticker: 'SYN2', category: 'risk', name: 'Synthetic risk marker', value: 1.1, context: 'Fixture-only signal.', direction: 'caution', strength: 61, reading: 'Synthetic risk reading.', source: 'fixture' },
], n_signals_total: 2, n_signals_strong: 2, by_category: { momentum: 1, risk: 1 }, generated: stamp,
  cache: { attiva: true, servita_da_cache: true, eta_s: 45, scansione_delle: stamp, ttl_s: 3600, ttl_motivo: 'Synthetic cache age for verification.' },
  copertura: { posizioni_totali: 2, posizioni_scansionate: 2, scansione_piena: ['SYN1', 'SYN2'], scansione_degradata: {}, solo_prezzo: [],
    nessuna_misura: {}, non_scansionate: [], fattoriali_su: 2, fattoriali_ko: null, nota: 'Synthetic fixture coverage.' } };

async function markButton(q, { root = 'main', includes, pattern, nth = 0 }) {
  const marker = 'data-qa-risk-button';
  const found = await q.js((rootSel, needle, regexText, index, attr) => {
    document.querySelectorAll(`[${attr}]`).forEach(el => el.removeAttribute(attr));
    let re = null; try { re = regexText ? new RegExp(regexText, 'i') : null; } catch {}
    const candidates = [...document.querySelectorAll(`${rootSel} button`)].filter(el => {
      const r = el.getBoundingClientRect(), s = getComputedStyle(el), text = (el.innerText || el.getAttribute('aria-label') || '').trim();
      return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' && !el.disabled
        && (needle ? text.toLowerCase().includes(needle.toLowerCase()) : true) && (re ? re.test(text) : true);
    });
    const el = candidates[index]; if (!el) return { count: candidates.length, texts: candidates.map(b => (b.innerText || '').trim()).slice(0, 30) };
    el.setAttribute(attr, 'target'); return { count: candidates.length, text: (el.innerText || '').trim(), selector: `[${attr}="target"]` };
  }, root, includes || '', pattern || '', nth, marker);
  assert.ok(found.selector, `Could not locate interactive button (${includes || pattern}): ${JSON.stringify(found)}`);
  return found.selector;
}

async function markInput(q, { root = 'main', selector, placeholderIncludes, nth = 0 }) {
  const marker = 'data-qa-risk-input';
  const found = await q.js((rootSel, sel, needle, index, attr) => {
    document.querySelectorAll(`[${attr}]`).forEach(el => el.removeAttribute(attr));
    const candidates = [...document.querySelectorAll(sel ? `${rootSel} ${sel}` : `${rootSel} input`)].filter(el => {
      const r = el.getBoundingClientRect(), s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'
        && (!needle || (el.placeholder || '').toLowerCase().includes(needle.toLowerCase()));
    });
    const el = candidates[index]; if (!el) return { count: candidates.length, fields: candidates.map(e => ({ id: e.id, placeholder: e.placeholder, aria: e.getAttribute('aria-label') })) };
    el.setAttribute(attr, 'target'); return { count: candidates.length, selector: `[${attr}="target"]`, placeholder: el.placeholder };
  }, root, selector || '', placeholderIncludes || '', nth, marker);
  assert.ok(found.selector, `Could not locate input (${selector || placeholderIncludes}): ${JSON.stringify(found)}`);
  return found.selector;
}

async function blurInput(q, selector) {
  const focused = await q.js(sel => {
    const input = document.querySelector(sel);
    if (!input) return false;
    input.focus(); input.blur();
    return document.activeElement !== input;
  }, selector);
  assert.ok(focused, `could not trigger blur validation on ${selector}`);
}

async function waitSettled(q, route, timeout = 6000) {
  const until = Date.now() + timeout;
  let last = -1, steady = 0;
  while (Date.now() < until) {
    const count = (await q.counts())[`GET ${route}`] || 0;
    if (count === last) steady++; else steady = 0;
    if (count > 0 && steady >= 4) return count;
    last = count; await q.pause(100);
  }
  throw new Error(`Timed out waiting for fixture GET ${route} to settle.`);
}
async function freshDashboard(q) {
  await q.toggle('classic');
  await q.visit('/dashboard');
}
const delta = (before, after, key) => (after[key] || 0) - (before[key] || 0);

async function waitForCount(q, key, target, { exact = false, timeout = 8000 } = {}) {
  const until = Date.now() + timeout;
  while (Date.now() < until) {
    const count = (await q.counts())[key] || 0;
    if (exact ? count === target : count >= target) return count;
    await q.pause(40);
  }
  throw new Error('Timed out waiting for ' + key + ' count ' + (exact ? '===' : '>=') + ' ' + target + '.');
}

const optionExpiries = ['2026-12-18', '2027-03-19'];
function optionDownload(state, id = 'qa-vol-job') {
  return { id, ticker: 'SYNV', state, phase: state === 'complete' ? 'done' : 'chain', scope: 'selected',
    expirations: optionExpiries, catalog_complete: true, completed_expiries: state === 'complete' ? 2 : 1,
    current_expiry: state === 'complete' ? null : optionExpiries[1], pages_received: 3, n_contracts: 3,
    duplicates: 0, malformed_contracts: 0, error: null, download_complete: state === 'complete', retryable: true,
    updated_at: stamp, started_at: stamp, snapshot_at: stamp, cache_ttl_seconds: 86400,
    retention_seconds: 604800, stale: false, pause_requested: false, spot: 100, spot_source: 'synthetic fixture', spot_error: null,
    rows: optionExpiries.map((expiry, i) => ({ expiry, n_contracts: i === 0 ? 3 : 2,
      complete: state === 'complete', error: null })) };
}
const optionCatalog = { ticker: 'SYNV', expirations: optionExpiries, complete: true, next_after: null,
  requests_used: 1, request_budget: 10, error: null, _timestamp: stamp };
const optionContract = (type, strike, suffix, bid, ask) => ({ contract: `O:SYNV${suffix}`, type, strike,
  expiry: optionExpiries[0], iv: .25, delta: type === 'call' ? .52 : -.48, gamma: .03, theta: -.04,
  vega: .12, rho: .02, bid, ask, mid: (bid + ask) / 2, oi: 240, volume: 30, multiplier: 100,
  exercise_style: 'American', quote_timestamp: stamp, quote_timeframe: 'fixture snapshot', adjusted: false,
  quote_age_seconds: 14, quality: [], _source: 'fixture option chain' });
const optionChain = { ticker: 'SYNV', expiry: optionExpiries[0],
  chain: [optionContract('call', 100, 'C00100000', 4.8, 5.2), optionContract('put', 100, 'P00100000', 4.7, 5.1),
    optionContract('call', 105, 'C00105000', 2.1, 2.5)],
  complete: true, next_cursor: null, spot: 100, spot_timeframe: 'fixture snapshot', spot_timestamp_ns: Date.now(),
  _timestamp: stamp, error: null, malformed_contracts: 0, cached: true, n_contracts: 3,
  filtered_contracts: 3, chain_complete: true, has_more: false, next_offset: null, offset: 0, limit: 250 };
const optionSurface = { ticker: 'SYNV', spot_est: 100, spot_source: 'synthetic fixture', _timestamp: stamp,
  smoothing: 'fixture observed IV; no synthetic interpolation', moneyness_grid: [.8, .9, 1, 1.1, 1.2],
  slices: optionExpiries.map((expiry, i) => ({ expiry, days: i ? 170 : 80, atm_iv: i ? .29 : .25,
    iv_grid: i ? [.34, .31, .29, .3, .33] : [.31, .27, .25, .26, .3], n_calls: 8, n_puts: 7,
    n_illiquidi_esclusi: 1 })),
  term_structure: optionExpiries.map((expiry, i) => ({ expiry, days: i ? 170 : 80, atm_iv: i ? .29 : .25 })),
  coverage: { requested: optionExpiries, loaded: optionExpiries, excluded: [], errors: [], complete: true,
    rows: optionExpiries.map((expiry, i) => ({ expiry, days: i ? 170 : 80, status: 'loaded', reason: null, n_contracts: 15 })),
    download_complete: true }, expected_move_pct: 12, expected_move_days: 80, realized_vol_30d: .21,
  iv_rv_spread_front: .04, term_slope_front_to_60d: .04, rv_percentile_1y: 62, next_earnings: null,
  gex: null };
// 10/10 (Opus 5.5): a COMPLETE engine response (the builder validates the shape before reading it).
const strategyRate = { value: .04, percent: 4, date: '2026-10-09', source: 'FRED DGS3MO', status: 'solid', error: null };
const enginePoint = (price, pnl) => ({ price, pnl, delta: 38, gamma: .03, vega: 12, theta: -4, rho: 2 });
function engineResult(entry) {
  return { currency: 'USD', model: 'Black-Scholes-Merton European', model_source: 'synthetic fixture',
    greek_units: { delta: 'shares', gamma: 'per dollar', vega: 'per IV point', theta: 'per day', rho: 'per rate point' },
    entry_cost: entry, net_premium: entry, fees: 0, entry_kind: 'debit', same_expiry: true, expiry_days: 80, expiry_basis: 'exact',
    tail_limit: null, breakevens: [100 + entry / 100], breakeven_intervals: [], max_profit: null, max_loss: entry,
    unlimited_profit: true, unlimited_loss: false, unlimited_loss_reason: null, tail_reference: null,
    vol_model: 'forward', vol_model_basis: 'synthetic fixture', forward_vols: [],
    probability_of_profit: { value: .42, sigma: .25, horizon_days: 80, basis: 'synthetic fixture' },
    legs: [{ index: 0, type: 'call', side: 'buy', value: 5, pnl_today: 0, delta: 52, gamma: 3, vega: 12, theta: -4, rho: 2 }],
    today: enginePoint(100, 0), scenario: enginePoint(100, 0),
    curve: [90, 95, 100, 105, 110].map((price, i) => ({ price, expiry: [-entry, -entry, -entry, 500 - entry, 1000 - entry][i],
      today: [-300, -120, 0, 90, 220][i], scenario: [-300, -120, 0, 90, 220][i] })),
    heatmap: [0, 40, 80].map(days => ({ elapsed_days: days, cells: [90, 100, 110].map(price => enginePoint(price, price - 100 - entry / 10)) })),
    assumptions: { fixture: true }, limits: ['Synthetic fixture; not a live quote.'] };
}

async function run(q) {
  await q.executeScenario('factors', 'populated-recalc-preserves-controller-across-mode-switch', async () => {
    await q.fixture({ setRead: {
      '/portfolio/factors': factors,
      '/portfolio/metrics/beta_reconcile': { body: reconcile },
    } });
    await q.toggle('classic'); await q.visit('/factors');
    await q.waitFor(() => !!document.querySelector('main [data-page="factors"] .calibro'), 'factor reconciliation control');
    await q.waitFor(() => document.querySelector('main [data-page="factors"] button.bt')?.disabled === false, 'initial factor pipeline settled', 10000);
    await waitSettled(q, '/portfolio/factors', 12000);
    const initial = await q.js(() => ({ rows: document.querySelectorAll('#tab-alpha tbody tr').length,
      radios: [...document.querySelectorAll('main [data-page="factors"] [role="radio"]')].map(el => el.getAttribute('aria-checked')) }));
    assert.ok(initial.rows >= 2, `synthetic populated factor payload did not render rows: ${JSON.stringify(initial)}`);
    assert.ok(initial.radios.length >= 3, `expected factor reconciliation choices: ${JSON.stringify(initial)}`);
    const selectedRadio = await q.js(() => {
      const radio = document.querySelector('main [data-page="factors"] [role="radio"][aria-checked="true"]');
      if (!radio) return null;
      radio.setAttribute('data-qa-factor-radio', 'selected');
      return '[data-qa-factor-radio="selected"]';
    });
    assert.ok(selectedRadio, 'selected reconciliation needle should be keyboard focusable');
    await q.js(sel => document.querySelector(sel)?.focus(), selectedRadio);
    await q.key('ArrowRight');
    const selectedRadios = await q.js(() => [...document.querySelectorAll('main [data-page="factors"] [role="radio"]')].map(el => el.getAttribute('aria-checked')));
    assert.equal(selectedRadios[1], 'true', 'ArrowRight should move the reconciliation marker to the next needle');
    const recalc = await markButton(q, { root: 'main [data-page="factors"]', pattern: 'recalcul' });
    await waitSettled(q, '/portfolio/factors', 12000);
    await q.fixture({ setRead: { '/portfolio/metrics/beta_reconcile': { body: reconcile, delayMs: 20000 } } });
    const before = await q.counts();
    await q.click(recalc);
    await q.waitFor(() => !!document.querySelector('main [data-page="factors"] button.bt:disabled'), 'factor recalculation in progress', 4000);
    await waitForCount(q, 'GET /portfolio/metrics/beta_reconcile', (before['GET /portfolio/metrics/beta_reconcile'] || 0) + 1);
    const beforeMode = await q.pageState();
    await q.toggle('modern');
    const inModern = await q.pageState();
    assert.equal(inModern.id, beforeMode.id, 'factor controller wrapper remounted during recalculation');
    assert.equal(await q.js(() => document.querySelector('main [data-page="factors"] button.bt')?.disabled), true,
      'factor recalculation must remain visibly in-flight after switching to modern');
    await q.capture('factors-recalculation-pending-modern');
    assert.equal(await q.js(() => document.querySelector('main [data-page="factors"] button.bt')?.disabled), true,
      'factor recalculation must remain in flight through the modern viewport captures');
    await q.toggle('classic');
    await q.waitFor(() => !document.querySelector('main [data-page="factors"] button.bt:disabled'), 'factor recalculation settled', 25000);
    await waitSettled(q, '/portfolio/factors', 25000);
    const after = await q.counts();
    const state = await q.js(() => ({ rows: document.querySelectorAll('#tab-alpha tbody tr').length,
      radios: [...document.querySelectorAll('main [data-page="factors"] [role="radio"]')].map(el => el.getAttribute('aria-checked')) }));
    assert.ok(state.rows >= 2, 'factor table disappeared after recalculation');
    assert.equal(delta(before, after, 'GET /portfolio/metrics/beta_reconcile'), 1, 'recalculation should issue one reconciliation request');
    assert.equal(delta(before, after, 'GET /portfolio/factors'), 3, 'factor cache protocol should make its documented three window reads');
    await q.toggle('modern');
    await q.capture('factors-populated-result-modern');
    return { assertionResults: { populatedFixtureRendered: initial.rows >= 2, factorSelectionPresent: initial.radios.length >= 3,
      controllerStableWhileRecalculating: inModern.id === beforeMode.id, recalculationUsesExpectedRequests: true,
      tableRetainedAfterRecalc: state.rows >= 2 }, initial, after: state,
      selectedBetaSource: selectedRadios.findIndex(value => value === 'true'),
      requestDeltas: { factors: delta(before, after, 'GET /portfolio/factors'), reconciliation: delta(before, after, 'GET /portfolio/metrics/beta_reconcile') },
      fixture: 'synthetic populated and deliberately partial factor payload; no provider/backend called' };
  });

  await q.executeScenario('backtest', 'what-if-validation-and-single-inflight-v3-run-survive-mode-switch', async () => {
    await q.fixture({ setRead: { '/portfolio/validate_ticker': { ok: true, symbol: 'SYNQ', name: 'Synthetic QA security', currency: 'EUR', last_price: 100 } },
      setWrite: { '/portfolio/montecarlo/v3': { body: { ...mcResult, modifications_applied: [{ action: 'add', ticker: 'SYNQ', amount_eur: 1000 }], synthetic: true }, delayMs: 20000 } } });
    await q.toggle('classic'); await q.visit('/backtest');
    await q.waitFor(() => !!document.querySelector('main [data-page="montecarlo"] .mc-params select'), 'Monte Carlo controls');
    await q.waitFor(() => !!document.querySelector('main [data-page="montecarlo"] .mc-cone .mc-chart svg'), 'Monte Carlo fan chart');
    const method = 'main [data-page="montecarlo"] .mc-params select';
    await q.setValue(method, 'block_bootstrap');
    assert.equal(await q.js(sel => document.querySelector(sel)?.value, method), 'block_bootstrap');
    await q.click('main [data-page="montecarlo"] [data-action="open-banco"]');
    await q.waitFor(() => !!document.querySelector('main [data-page="montecarlo"] .mc-drawer'), 'what-if panel open');
    await q.click('main [data-page="montecarlo"] .mc-drawer [data-add="add"]');
    const ticker = await markInput(q, { root: 'main [data-page="montecarlo"]', selector: '.mc-mod input.is-tk', nth: 0 });
    await q.typeText(ticker, 'SYNQ');
    assert.equal(await q.js(() => document.querySelector('main [data-page="montecarlo"] .mc-mod input.is-tk')?.value), 'SYNQ',
      'ticker draft should be controlled by React before blur validation');
    const amount = await markInput(q, { root: 'main [data-page="montecarlo"]', selector: '.mc-mod input.is-eur' });
    const beforeTickerValidation = await q.counts();
    await q.click(amount);
    await q.typeText(amount, '1000');
    await q.waitFor(() => (document.querySelector('main [data-page="montecarlo"] .mc-mod')?.innerText || '').includes('Synthetic QA security'), 'fixture ticker validation', 5000);
    assert.equal(delta(beforeTickerValidation, await q.counts(), 'GET /portfolio/validate_ticker'), 1,
      'one ticker blur should cause exactly one fixture validation read');
    const before = await q.counts();
    await q.click('main [data-page="montecarlo"] [data-action="simulate-banco"]');
    await q.waitFor(() => document.querySelector('main [data-page="montecarlo"] button[data-action="simulate"]')?.disabled === true, 'Monte Carlo v3 request in progress', 3000);
    const idBefore = await q.pageState();
    await q.toggle('modern');
    const idModern = await q.pageState();
    assert.equal(idModern.id, idBefore.id, 'Monte Carlo page controller remounted during v3 request');
    assert.equal(await q.js(() => document.querySelector('main [data-page="montecarlo"] button[data-action="simulate"]')?.disabled), true,
      'Monte Carlo must remain visibly busy after switching to modern');
    await q.capture('backtest-v3-pending-modern');
    assert.equal(await q.js(() => document.querySelector('main [data-page="montecarlo"] button[data-action="simulate"]')?.disabled), true,
      'Monte Carlo must remain in flight through the modern viewport captures');
    await q.toggle('classic');
    await q.waitFor(() => document.querySelector('main [data-page="montecarlo"] button[data-action="simulate"]')?.disabled === false, 'Monte Carlo v3 request complete', 25000);
    const after = await q.counts();
    const snapshot = await q.snapshot();
    const requests = snapshot.requests.filter(item => item.method === 'POST' && item.route === '/portfolio/montecarlo/v3');
    assert.equal(requests.length, 1, 'one click must send exactly one fixture v3 request');
    assert.deepEqual(requests[0].input.modifications, [{ action: 'add', ticker: 'SYNQ', amount_eur: 1000 }]);
    assert.equal(delta(before, after, 'POST /portfolio/montecarlo/v3'), 1);
    await q.toggle('modern');
    await q.capture('backtest-v3-result-modern');
    await q.setValue(method, 'parametric_t');
    assert.equal(await q.js(sel => document.querySelector(sel)?.value, method), 'parametric_t', 'modern method control did not update');
    await q.toggle('classic');
    return { assertionResults: { methodParameterRetained: true, tickerValidatedFromFixture: true, v3PayloadExact: true,
      onePostForOneClick: requests.length === 1, pageStableInFlight: idModern.id === idBefore.id },
      postInput: requests[0].input, method: 'block_bootstrap', noRealSimulation: true };
  });

  await q.executeScenario('vol', 'populated-chain-details-strategy-payoff-and-native-plotly-camera-use-fixtures', async () => {
    const job = optionDownload('complete');
    const routeStatus = '/options/download/' + job.id + '/status';
    const routeSurface = '/options/download/' + job.id + '/surface';
    const routeChain = '/options/download/' + job.id + '/chain';
    const putOnlyChain = { ...optionChain, chain: [optionChain.chain[1]], n_contracts: 1, filtered_contracts: 1 };
    const strikeAtHundred = { ...optionChain, chain: optionChain.chain.filter(contract => contract.strike === 100),
      n_contracts: 2, filtered_contracts: 2 };
    await q.fixture({ setRead: {
      '/options/expiry_catalog/SYNV': optionCatalog,
      [routeStatus]: job,
      [routeSurface]: optionSurface,
      [routeChain]: optionChain,
    }, setWrite: { '/options/download/SYNV': optionDownload('running'),
      '/options/strategy/simulate': { body: engineResult(520), delayMs: 1600 } } });
    await q.toggle('classic'); await q.visit('/vol');
    await q.waitFor(() => !!document.querySelector('#va-ticker'), 'volatility ticker input');
    await q.setValue('#va-ticker', 'SYNV');
    await q.click(await markButton(q, { root: 'main [data-page="vol"] form.va-ticker', pattern: 'catalog' }));
    await q.waitFor(() => !!document.querySelector('main [data-page="vol"] .vd-catalog-summary'), 'synthetic expiry catalog', 5000);
    await q.click(await markButton(q, { root: 'main [data-page="vol"] .vd-catalog', pattern: 'load chain and surface' }));
    await q.waitFor(() => !!document.querySelector('main [data-page="vol"] .va-provenance'), 'completed fixture acquisition renders surface', 10000);
    assert.equal(await q.js(() => document.querySelector('main [data-page="vol"] .va-provenance strong')?.innerText), 'SYNV');
    await q.waitFor(() => {
      const graph = document.querySelector('main [data-page="vol"] [data-vol-3d]');
      return typeof graph?._fullLayout?.scene?.yaxis?.title?.text === 'string';
    }, 'Light Plotly scene with its expiry axis');
    const classicAxisTitle = await q.js(() => document.querySelector('main [data-page="vol"] [data-vol-3d]')
      ?._fullLayout?.scene?.yaxis?.title?.text || '');
    // Vol Deck v2 (10/10, Opus 5.5, riserva ALTA-3): lo switch Classica/Nuova non esiste piu' (02/10) e la pagina e'
    // una sola in Chiaro e Scuro: il titolo nativo dell'asse dei giorni (che Plotly tagliava) e' vuoto in ENTRAMBI i temi
    // e l'etichetta e' l'HTML [data-vol-axis-title="expiry"], una sola volta.
    assert.equal(classicAxisTitle.trim(), '', 'Light leaves the clipped native Plotly y-title empty too');
    assert.equal(await q.js(() => document.querySelectorAll('main [data-page="vol"] [data-vol-axis-title="expiry"]').length), 1,
      'the days-to-expiry label is the HTML one, exactly once');
    await q.toggle('modern');
    const pageBefore = await q.pageState();
    assert.equal(await q.js(() => document.querySelector('main [data-page="vol"] button[data-vol-workspace="tools"]')?.getAttribute('aria-pressed')), 'true');
    const plotSelector = 'main [data-page="vol"] [data-vol-3d]';
    await q.scrollTo(plotSelector);
    const readPlotState = () => {
      const graph = document.querySelector('main [data-page="vol"] [data-vol-3d]');
      const graphRect = graph?.getBoundingClientRect();
      const graphStyle = graph ? getComputedStyle(graph) : null;
      const canvases = [...(graph?.querySelectorAll('.gl-container canvas') || [])].map((canvas, index) => {
        let gl = null, contextType = null;
        for (const type of ['webgl2', 'webgl', 'experimental-webgl']) {
          try { gl = canvas.getContext(type); } catch { gl = null; }
          if (gl) { contextType = type; break; }
        }
        let contextVersion = null, contextLost = true;
        if (gl) {
          try { contextVersion = gl.getParameter(gl.VERSION); contextLost = gl.isContextLost(); } catch {}
        }
        const rect = canvas.getBoundingClientRect();
        return { index, width: canvas.width, height: canvas.height, className: String(canvas.className || ''),
          contextType, contextVersion, contextLost,
          rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
          visible: rect.width > 100 && rect.height > 100 && rect.bottom > 0 && rect.right > 0
            && rect.top < innerHeight && rect.left < innerWidth };
      });
      const selectedCanvas = canvases.filter(canvas => canvas.width > 100 && canvas.height > 100
        && canvas.contextVersion && !canvas.contextLost && canvas.visible)
        .sort((a, b) => b.width * b.height - a.width * a.height)[0] || null;
        const traces = graph?._fullData || [];
        const mesh = traces.find(trace => trace.type === 'surface' && trace.visible !== false
          && Array.isArray(trace.z) && trace.z.length > 1 && trace.z[0]?.length > 1);
        const camera = graph?._fullLayout?.scene?.camera;
        const sceneAxisTitles = Object.fromEntries(['x', 'y', 'z'].map(axis => [axis,
          graph?._fullLayout?.scene?.[`${axis}axis`]?.title?.text || null]));
      const meshGrid = mesh ? { rows: mesh.z.length, columns: mesh.z[0].length } : null;
      const visibleGraph = !!graphRect && graphRect.width > 100 && graphRect.height > 100
        && graphRect.bottom > 0 && graphRect.right > 0 && graphRect.top < innerHeight && graphRect.left < innerWidth
        && graphStyle?.display !== 'none' && graphStyle?.visibility !== 'hidden';
      const sceneReady = !!graph?._fullLayout?.scene?._scene;
      return { ok: !!graph?.classList.contains('js-plotly-plot') && !!window.Plotly?.relayout && visibleGraph
          && sceneReady && !!mesh && !!selectedCanvas,
        graphFound: !!graph, graphClass: graph ? String(graph.className) : null,
        graphRect: graphRect && { x: graphRect.x, y: graphRect.y, width: graphRect.width, height: graphRect.height },
        visibleGraph, sceneReady, traceTypes: traces.map(trace => trace.type), meshGrid, camera: camera ? {
          eye: { x: camera.eye?.x, y: camera.eye?.y, z: camera.eye?.z },
          center: { x: camera.center?.x, y: camera.center?.y, z: camera.center?.z },
          up: { x: camera.up?.x, y: camera.up?.y, z: camera.up?.z } } : null,
        canvases, selectedCanvas, sceneAxisTitles, renderer: graph?._fullLayout?.scene?._scene?.constructor?.name || null };
    };
    const waitForPlotState = async (label, predicate, timeout = 12000) => {
      const until = Date.now() + timeout;
      let last = null;
      while (Date.now() < until) {
        last = await q.js(readPlotState);
        if (last.ok && predicate(last)) return last;
        await q.pause(50);
      }
      throw new Error(`${label}; last live Plotly diagnostics=${JSON.stringify(last)}`);
    };
    const cameraDeltas = (left, right) => ['eye', 'center', 'up'].flatMap(group => ['x', 'y', 'z'].flatMap(axis => {
      const before = left?.[group]?.[axis], after = right?.[group]?.[axis];
      return typeof before === 'number' && Number.isFinite(before) && typeof after === 'number' && Number.isFinite(after)
        ? [{ group, axis, before, after, delta: Math.abs(after - before) }] : [];
    }));
    const cameraChanged = (left, right) => cameraDeltas(left, right)
      .some(item => item.group === 'eye' && item.delta > .08);
    const cameraPreserved = (left, right) => {
      const deltas = cameraDeltas(left, right);
      const eyeDeltas = deltas.filter(item => item.group === 'eye');
      const extraDeltas = deltas.filter(item => item.group !== 'eye');
      return eyeDeltas.length === 3 && eyeDeltas.every(item => item.delta < .03)
        && extraDeltas.every(item => item.delta < .03);
    };
    const plotEvidence = await waitForPlotState('visible Plotly surface mesh with a live WebGL context and no clipped native y-title',
      state => !String(state.sceneAxisTitles.y || '').trim(), 20000);
    assert.ok(plotEvidence.meshGrid?.rows > 1 && plotEvidence.meshGrid?.columns > 1,
      `the rendered Plotly surface must contain a populated mesh: ${JSON.stringify(plotEvidence)}`);
    assert.equal(cameraDeltas(plotEvidence.camera, plotEvidence.camera).filter(item => item.group === 'eye').length, 3,
      `live Plotly camera must expose numeric eye.x/y/z: ${JSON.stringify(plotEvidence)}`);
    assert.ok(!String(plotEvidence.sceneAxisTitles.y || '').trim(),
      `Modern must remove only the clipped Plotly y-title in favor of the HTML axis label: ${JSON.stringify(plotEvidence.sceneAxisTitles)}`);
    const beforePlotRequests = await q.counts();
    const orbitProbe = await q.startChartInputProbe(plotSelector);
    assert.ok(orbitProbe.ok, `cannot observe native Plotly camera input: ${JSON.stringify(orbitProbe)}`);
    let orbitInput = null, trustedInput = null;
    try { orbitInput = await q.drag(plotSelector, 150, 95); }
    finally { trustedInput = await q.stopChartInputProbe(orbitProbe.id); }
    const trustedEvents = trustedInput.events.filter(event => event.isTrusted);
    assert.ok(trustedEvents.some(event => event.type === 'mousedown' && event.buttons === 1)
      && trustedEvents.filter(event => event.type === 'mousemove' && event.buttons === 1).length >= 3
      && trustedEvents.some(event => event.type === 'mouseup'),
      `Plotly camera orbit must use trusted held pointer input: ${JSON.stringify({ orbitInput, trustedEvents })}`);
    const afterNativeOrbit = await waitForPlotState('actual Plotly camera changed after native pointer orbit',
      state => cameraChanged(state.camera, plotEvidence.camera), 6000);
    let cameraTrace = { initial: plotEvidence, orbitInput, trustedInput, afterNativeOrbit,
      orbitDeltas: cameraDeltas(plotEvidence.camera, afterNativeOrbit.camera) };
    let cameraFailure = null;
    try {
      await q.toggle('classic');
      const afterClassic = await waitForPlotState('surface and camera remain live in Classic',
        state => cameraPreserved(state.camera, afterNativeOrbit.camera), 12000);
      assert.equal(String(afterClassic.sceneAxisTitles.y || '').trim(), '',
        'Light keeps the native Plotly expiry-axis title empty (HTML label instead)');
      await q.toggle('modern');
      const afterModern = await waitForPlotState('surface and camera remain live in Nuova',
        state => cameraPreserved(state.camera, afterNativeOrbit.camera) && !String(state.sceneAxisTitles.y || '').trim(), 12000);
      cameraTrace = { ...cameraTrace, afterClassic, classicDeltas: cameraDeltas(afterNativeOrbit.camera, afterClassic.camera),
        afterModern, modernDeltas: cameraDeltas(afterNativeOrbit.camera, afterModern.camera) };
    } catch (error) {
      cameraFailure = { error: String(error?.stack || error), lastState: await q.js(readPlotState).catch(stateError => ({ error: String(stateError) })) };
      const modern = await q.js(() => document.querySelector('[data-theme-choice="dark"]')?.getAttribute('aria-pressed'));
      if (modern !== 'true') await q.toggle('modern');
    }
    await q.scrollTo(plotSelector);
    const volPlotSvgSelector = 'main [data-page="vol"] [data-vol-3d] svg';
    const strikeProjectorSvgSelector = 'main [data-page="vol"] [data-vol-projector] svg.vsxsvg';
    const strikeLabels = await q.js(selector => {
      const svg = document.querySelector(selector);
      return [...(svg?.querySelectorAll('text') || [])].map(node => (node.textContent || '').trim().replace(/\s+/g, ' '))
        .filter(text => text.includes('· +') || text.includes('· −'));
    }, strikeProjectorSvgSelector);
    const upperStrikeLabel = strikeLabels.find(text => text.includes('· +'));
    const lowerStrikeLabel = strikeLabels.find(text => text.includes('· −'));
    assert.ok(upperStrikeLabel && lowerStrikeLabel,
      `both final expiry strike labels must render before clipping bounds are measured: ${JSON.stringify(strikeLabels)}`);
    const meshCaptures = await q.capture('vol-populated-surface-mesh-camera-modern', { scrollSelector: plotSelector,
      svgTextBoundsSelectors: [volPlotSvgSelector, strikeProjectorSvgSelector],
      captureBeforeSvgBoundsFailure: true, deferSvgBoundsFailure: true,
      verifyVolAxisTitle: true, deferVolAxisTitleFailure: true,
      requiredSvgTexts: [{ selector: strikeProjectorSvgSelector, text: upperStrikeLabel },
        { selector: strikeProjectorSvgSelector, text: lowerStrikeLabel }] });
    assert.deepEqual(meshCaptures.map(capture => `${capture.viewport.innerWidth}x${capture.viewport.innerHeight}`),
      ['1920x1080', '2560x1440', '3440x1440', '5120x1440', '1440x1000', '1280x900', '900x700'],
      'Vol mesh verification must inspect every required viewport');
    const plotAfterCapture = await waitForPlotState('populated WebGL surface after seven viewport captures', () => true, 12000);
    const viewportCameraDeltas = cameraDeltas(afterNativeOrbit.camera, plotAfterCapture.camera);
    if (!cameraFailure && !cameraPreserved(plotAfterCapture.camera, afterNativeOrbit.camera)) {
      cameraFailure = { error: 'camera eye/center/up changed during seven viewport captures', viewportCameraDeltas };
    }
    cameraTrace = { ...cameraTrace, afterSevenViewportCapture: plotAfterCapture, viewportCameraDeltas, paletteRecoveryError: cameraFailure };
    const afterPlotCaptures = await q.counts();
    const plotRequestDeltas = Object.fromEntries([...new Set([...Object.keys(beforePlotRequests), ...Object.keys(afterPlotCaptures)])]
      .map(key => [key, delta(beforePlotRequests, afterPlotCaptures, key)]).filter(([, amount]) => amount !== 0));
    assert.deepEqual(plotRequestDeltas, {}, 'native 3D orbit, mode changes, and viewport captures must not start a backend request');
    const geometryFailures = meshCaptures.filter(capture => !capture.svgTextBounds?.ok || !capture.volAxisTitle?.ok)
      .map(capture => ({ viewport: capture.viewport, evidence: capture.svgTextBounds,
        htmlExpiryAxisTitle: capture.volAxisTitle }));
    assert.equal(geometryFailures.length, 0,
      `Vol SVG/projector geometry or HTML axis-title checks failed after seven captures; native camera trace: ${JSON.stringify(cameraTrace)}; failures: ${JSON.stringify(geometryFailures)}`);

    await q.click('main [data-page="vol"] button[data-vol-workspace="chain"]');
    await q.waitFor(() => document.querySelectorAll('main [data-page="vol"] .vd-chain tbody tr').length === 3, 'synthetic options chain loaded', 8000);
    await q.fixture({ setRead: { [routeChain]: putOnlyChain } });
    const put = await markButton(q, { root: 'main [data-page="vol"] .vd-chain-tools', pattern: '^put$' });
    await q.click(put);
    await q.waitFor(() => document.querySelector('main [data-page="vol"] .vd-chain-tools .vd-segment button[aria-pressed="true"]')?.innerText.trim() === 'Put', 'put chain filter selected');
    await waitForCount(q, 'GET ' + routeChain, 2);
    await q.waitFor(() => document.querySelectorAll('main [data-page="vol"] .vd-chain tbody tr').length === 1, 'put-only contract response');
    const putRequest = (await q.snapshot()).requests.filter(r => r.method === 'GET' && r.route === routeChain).at(-1);
    assert.equal(putRequest.query.side, 'put', 'Put filter should be sent to the fixture-backed chain endpoint');
    const putRows = await q.js(() => [...document.querySelectorAll('main [data-page="vol"] .vd-chain tbody tr')].map(row => row.innerText));
    assert.equal(putRows.length, 1, 'Put response fixture should contain only its matching put contract');
    assert.match(putRows[0].toLowerCase(), /put/);
    await q.fixture({ setRead: { [routeChain]: putOnlyChain } });
    const strike = await markInput(q, { root: 'main [data-page="vol"] .vd-chain-tools', selector: 'input' });
    await q.typeText(strike, '100');
    await q.waitFor(sel => document.querySelector(sel)?.value === '100', 'strike filter value', 3000, strike);
    await waitForCount(q, 'GET ' + routeChain, 3);
    const strikeRequest = (await q.snapshot()).requests.filter(r => r.method === 'GET' && r.route === routeChain).at(-1);
    assert.equal(strikeRequest.query.side, 'put');
    assert.equal(strikeRequest.query.strike, '100', 'strike filter should reach the fixture-backed chain endpoint');
    await q.waitFor(() => document.querySelectorAll('main [data-page="vol"] .vd-chain tbody tr').length === 1, 'put and strike fixture subset');
    assert.equal(await q.js(() => document.querySelectorAll('main [data-page="vol"] .vd-chain tbody tr').length), 1,
      'the deterministic put/strike fixture should match exactly one contract');
    await q.fixture({ setRead: { [routeChain]: strikeAtHundred } });
    await q.click('main [data-page="vol"] .vd-segment button:first-child');
    await q.waitFor(() => document.querySelectorAll('main [data-page="vol"] .vd-chain tbody tr').length === 2, 'all fixture contracts at the selected strike restored');
    const inspectButton = await markButton(q, { root: 'main [data-page="vol"] .vd-chain tbody tr:first-child', pattern: 'call|100' });
    await q.click(inspectButton);
    await q.waitFor(() => !!document.querySelector('main [data-page="vol"] .vd-contract-inspector'), 'contract quote details');
    const detail = await q.js(() => document.querySelector('main [data-page="vol"] .vd-contract-inspector')?.innerText || '');
    assert.ok(detail.includes('O:SYNV') && detail.includes('fixture option chain'), 'contract details should expose fixture quote identity and source');
    await q.scrollTo('main [data-page="vol"] .vd-contract-inspector');
    await q.capture('vol-populated-chain-details-modern', { scrollSelector: 'main [data-page="vol"] .vd-contract-inspector',
      verifyVolWorkbenchContrast: true, verifyVolChainContrast: true });
    await q.click(await markButton(q, { root: 'main [data-page="vol"] .vd-chain tbody tr:first-child .vd-leg-actions', pattern: 'buy' }));
    // 10/10 (Opus 5.5, review M3): the laboratory is the option builder. It reads the full chain of the
    // leg's expiry (limit=1000, side=all), the FRED rate, and calls the engine by itself after a 140 ms
    // debounce: one POST per settled edit, every earlier request aborted and never shown.
    const builder = 'main [data-page="vol"] [data-option-builder]';
    const hero = () => document.querySelector('main [data-page="vol"] [data-option-builder] .ob-hero-v')?.textContent || '';
    await q.fixture({ setRead: { [routeChain]: strikeAtHundred, '/options/strategy/rate': strategyRate },
      setWrite: { '/options/strategy/simulate': { body: engineResult(520), delayMs: 1600 } } });
    const beforeLab = await q.counts();
    await q.click('main [data-page="vol"] button[data-vol-workspace="laboratory"]');
    await q.waitFor(sel => document.querySelectorAll(sel + ' tr[data-leg]').length === 1, 'observed contract leg reaches the option builder', 8000, builder);
    await waitForCount(q, 'POST /options/strategy/simulate', (beforeLab['POST /options/strategy/simulate'] || 0) + 1, { timeout: 9000 });
    const firstSnapshot = await q.snapshot();
    const builderChainReads = firstSnapshot.requests.filter(r => r.method === 'GET' && r.route === routeChain && r.query.limit === '1000');
    assert.ok(builderChainReads.length >= 1, 'the builder reads the whole expiry chain in pages of 1000');
    assert.deepEqual({ side: builderChainReads[0].query.side, expiry: builderChainReads[0].query.expiry, offset: builderChainReads[0].query.offset },
      { side: 'all', expiry: optionExpiries[0], offset: '0' });
    assert.ok(firstSnapshot.requests.some(r => r.method === 'GET' && r.route === '/options/strategy/rate'), 'the short rate comes from the backend route');
    const firstPost = firstSnapshot.requests.filter(r => r.method === 'POST' && r.route === '/options/strategy/simulate').at(-1);
    assert.equal(firstPost.input.spot, 100, 'spot of the chain page');
    assert.equal(firstPost.input.rate, .04, 'FRED rate 4% sent as a decimal');
    assert.equal(firstPost.input.vol_model, 'forward', 'forward vol is the declared default');
    assert.deepEqual(firstPost.input.legs.map(l => [l.type, l.side, l.strike, l.iv, l.premium, l.multiplier, l.quantity]),
      [['call', 'buy', 100, .25, 5, 100, 1]], 'the leg carries its own contract: mid, IV and multiplier from the chain');
    assert.ok(firstPost.input.legs[0].days > 0 && !Number.isInteger(firstPost.input.legs[0].days), 'fractional days to the 16:00 ET close');
    // While the first (slow) response is in flight: a burst of three edits 40 ms apart, inside the debounce.
    await q.fixture({ setWrite: { '/options/strategy/simulate': { body: engineResult(777.77), delayMs: 120 } } });
    const spotField = await markInput(q, { root: builder + ' .ob-inputs', selector: 'input', nth: 0 });
    const beforeBurst = await q.counts();
    await q.js(async sel => {
      const el = document.querySelector(sel);
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
      for (const text of ['1', '10', '101']) {
        set.call(el, text); el.dispatchEvent(new Event('input', { bubbles: true }));
        await new Promise(resolve => setTimeout(resolve, 40));
      }
    }, spotField);
    await waitForCount(q, 'POST /options/strategy/simulate', (beforeBurst['POST /options/strategy/simulate'] || 0) + 1, { timeout: 4000 });
    await q.pause(500);
    const burstPosts = delta(beforeBurst, await q.counts(), 'POST /options/strategy/simulate');
    assert.equal(burstPosts, 1, 'three edits inside the debounce send exactly one POST');
    const settledPost = (await q.snapshot()).requests.filter(r => r.method === 'POST' && r.route === '/options/strategy/simulate').at(-1);
    assert.equal(settledPost.input.spot, 101, 'the POST carries the last edit only');
    await q.waitFor(() => (document.querySelector('main [data-page="vol"] [data-option-builder] .ob-hero-v')?.textContent || '').includes('777.77'),
      'the settled response is shown', 5000);
    await q.pause(1800); // past the first response's delay: it was aborted and must not overwrite the page
    const heroAfter = await q.js(hero);
    assert.ok(heroAfter.includes('777.77') && !heroAfter.includes('520'), `an aborted earlier response must never be shown: ${heroAfter}`);
    // A response without the fields the page reads is a declared engine error, not a crash on rows[0].
    await q.fixture({ setWrite: { '/options/strategy/simulate': { body: { pnl: [] } } } });
    await q.js(sel => { const el = document.querySelector(sel); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, '102');
      el.dispatchEvent(new Event('input', { bubbles: true })); }, spotField);
    await q.waitFor(sel => /engine/i.test(document.querySelector(sel + ' .ob-alert')?.textContent || '')
      && /entry_cost/.test(document.querySelector(sel + ' .ob-alert')?.textContent || ''), 'malformed engine response declared with its field', 5000, builder);
    assert.ok(await q.js(sel => !!document.querySelector(sel + ' .ob-legs') && !document.querySelector(sel + ' .ob-chart svg'), builder),
      'the builder stays mounted and shows no chart for an unreadable response');
    // Back to a complete response, then the busy state must survive a presentation-mode switch.
    await q.fixture({ setWrite: { '/options/strategy/simulate': { body: engineResult(520), delayMs: 1600 } } });
    await q.js(sel => { const el = document.querySelector(sel); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, '');
      el.dispatchEvent(new Event('input', { bubbles: true })); }, spotField);
    await q.waitFor(sel => !!document.querySelector(sel + ' .ob-chart-card .ob-spin'), 'engine request in flight', 4000, builder);
    const simBefore = await q.pageState(); await q.toggle('classic'); const simClassic = await q.pageState();
    assert.equal(simClassic.id, simBefore.id, 'VolWorkbench/OptionBuilder remounted during the engine request');
    await q.waitFor(sel => !!document.querySelector(sel + ' .ob-chart svg path.ob-curve-expiry')
      && (document.querySelector(sel + ' .ob-hero-v')?.textContent || '').includes('520'), 'synthetic payoff result', 9000, builder);
    await q.toggle('modern');
    await q.scrollTo(builder + ' .ob-main');
    await q.capture('vol-strategy-populated-payoff-modern', { scrollSelector: builder + ' .ob-main',
      verifyVolWorkbenchContrast: true });
    const after = await q.snapshot();
    const chainReads = after.requests.filter(r => r.method === 'GET' && r.route === routeChain);
    const surfaceReads = after.requests.filter(r => r.method === 'GET' && r.route === routeSurface);
    const strategyPosts = after.requests.filter(r => r.method === 'POST' && r.route === '/options/strategy/simulate');
    assert.ok(chainReads.length >= 2, 'explicit side filter must reload the fixture chain');
    assert.equal(strategyPosts.length, 4, 'one POST per settled input: first view, burst, malformed, restored');
    assert.equal(surfaceReads.length, 1, 'mode changes must not refetch the downloaded surface');
    assert.equal((await q.pageState()).id, pageBefore.id, 'page controller identity should survive workspace and mode changes');
    assert.equal(cameraFailure, null, `native Plotly camera did not survive palette changes: ${JSON.stringify(cameraTrace)}`);
    return { assertionResults: { catalogAndAcquisitionUsedFixtures: true, populatedSurfaceAndCoverageRendered: true,
      workspaceAndControllerStateRetained: true, chainSideFilterRequestUsesPutAndContractDetailsWork: true, observedLegReachesOptionBuilder: true,
      builderReadsFullChainAndRate: true, oneDebouncedPostPerBurst: burstPosts === 1, abortedResponseNeverShown: true,
      malformedEngineResponseDeclared: true, strategyBusySurvivesModeSwitch: simClassic.id === simBefore.id, payoffAndGreeksRendered: true,
      modeSwitchDoesNotRefetchSurface: surfaceReads.length === 1,
      livePlotlySurfaceMeshAndWebGLVerified: true, nativeCameraUpdatedAndPreserved: true,
      chainDetailsCaptured: true, strategyPayoffCaptured: true },
      nativePlotlyCamera: { status: 'verified from trusted drag events and the live Plotly GraphDiv/WebGL context', ...cameraTrace },
      requestCounts: { chain: chainReads.length, surface: surfaceReads.length, strategy: strategyPosts.length },
      chainQueries: chainReads.map(r => r.query), selectedPutFilterQuery: putRequest.query, selectedStrikeQuery: strikeRequest.query, fixtureChainRows: putRows.length,
      strategyPayloads: strategyPosts.map(r => r.input),
      fixture: 'synthetic filtered chain subsets only; no live provider, broker, backend, or LLM called',
      note: 'camera motion comes from trusted CDP pointer drag on the real Plotly WebGL canvas; no Plotly API camera injection' };
  });

  await q.executeScenario('vol', 'download-pause-and-resume-remain-in-flight-across-mode-switch', async () => {
    const running = optionDownload('running', 'qa-vol-pause-job');
    const paused = optionDownload('paused', running.id);
    const complete = optionDownload('complete', running.id);
    const statusRoute = '/options/download/' + running.id + '/status';
    const chainRoute = '/options/download/' + running.id + '/chain';
    await q.fixture({ setRead: { '/options/expiry_catalog/SYNV': optionCatalog, [statusRoute]: running,
      [chainRoute]: optionChain,
      ['/options/download/' + running.id + '/surface']: optionSurface }, setWrite: {
      '/options/download/SYNV': running,
      ['/options/download/' + running.id + '/pause']: { body: paused, delayMs: 20000 },
      ['/options/download/' + running.id + '/resume']: running,
    } });
    await q.toggle('classic'); await q.visit('/vol'); await q.setValue('#va-ticker', 'SYNV');
    await q.click(await markButton(q, { root: 'main [data-page="vol"] form.va-ticker', pattern: 'catalog' }));
    await q.waitFor(() => !!document.querySelector('main [data-page="vol"] .vd-catalog-summary'), 'pause/resume catalog fixture');
    const start = await markButton(q, { root: 'main [data-page="vol"] .vd-catalog', pattern: 'load chain and surface' });
    const beforeStart = await q.counts(); await q.click(start);
    await waitForCount(q, 'POST /options/download/SYNV', (beforeStart['POST /options/download/SYNV'] || 0) + 1);
    await q.waitFor(() => [...document.querySelectorAll('main [data-page="vol"] .vd-download button')].some(button => /pause/i.test(button.innerText)),
      'running job exposes pause', 7000);
    const runningSummary = await q.js(() => ({ text: document.querySelector('main [data-page="vol"] .vd-download')?.innerText || '',
      value: document.querySelector('main [data-page="vol"] .vd-download progress')?.value,
      max: document.querySelector('main [data-page="vol"] .vd-download progress')?.max }));
    assert.ok(/running|downloading|in corso/i.test(runningSummary.text), `running download state was not rendered: ${runningSummary.text}`);
    assert.ok(Number.isFinite(runningSummary.value) && Number.isFinite(runningSummary.max), 'acquisition progress element should expose numeric progress');
    await waitForCount(q, 'GET ' + statusRoute, (beforeStart['GET ' + statusRoute] || 0) + 1);
    await waitForCount(q, 'GET ' + chainRoute, (beforeStart['GET ' + chainRoute] || 0) + 1);
    await waitSettled(q, chainRoute);
    const runningPage = await q.pageState(); await q.toggle('modern');
    assert.equal((await q.pageState()).id, runningPage.id, 'running acquisition page remounted on Modern switch');
    assert.ok(await q.js(() => !!document.querySelector('main [data-page="vol"] .vd-download progress')),
      'running progress bar should remain visible after presentation switch');
    await q.toggle('classic');
    const pause = await markButton(q, { root: 'main [data-page="vol"] .vd-download', pattern: 'pause' });
    // The running observer is aborted by controlDownload; its next status read
    // must reflect the accepted pause rather than loop on the fixture's stale
    // running state forever.
    await q.fixture({ setRead: { [statusRoute]: paused } });
    const beforePause = await q.pageState();
    const countsBeforePause = await q.counts();
    await q.click(pause);
    await waitForCount(q, 'POST /options/download/' + running.id + '/pause', (countsBeforePause['POST /options/download/' + running.id + '/pause'] || 0) + 1);
    await q.toggle('modern'); const modern = await q.pageState();
    assert.equal(modern.id, beforePause.id, 'page/controller identity changed during delayed pause request');
    assert.ok(await q.js(() => [...document.querySelectorAll('main [data-page="vol"] .vd-download button')].some(button => /pause/i.test(button.innerText) && button.disabled)),
      'download pause action must remain busy after switching to Modern');
    await q.capture('vol-pause-request-pending-modern');
    assert.ok(await q.js(() => [...document.querySelectorAll('main [data-page="vol"] .vd-download button')].some(button => /pause/i.test(button.innerText) && button.disabled)),
      'the delayed pause must remain in-flight throughout all viewport captures');
    await q.waitFor(() => [...document.querySelectorAll('main [data-page="vol"] .vd-download button')].some(button => /resume/i.test(button.innerText)),
      'paused job exposes resume', 35000);
    const pausedText = await q.js(() => document.querySelector('main [data-page="vol"] .vd-download')?.innerText || '');
    assert.ok(/paused|in pausa/i.test(pausedText), 'pause response should be explicitly displayed');
    await q.fixture({ setRead: { [statusRoute]: complete } });
    const beforeResume = await q.counts(); await q.click(await markButton(q, { root: 'main [data-page="vol"] .vd-download', pattern: 'resume' }));
    await waitForCount(q, 'POST /options/download/' + running.id + '/resume', (beforeResume['POST /options/download/' + running.id + '/resume'] || 0) + 1);
    await q.waitFor(() => !!document.querySelector('main [data-page="vol"] .va-provenance'), 'resumed download restores completed surface', 9000);
    const final = await q.snapshot();
    const starts = final.writes.filter(r => r.route === '/options/download/SYNV');
    const pauses = final.writes.filter(r => r.route === '/options/download/' + running.id + '/pause');
    const resumes = final.writes.filter(r => r.route === '/options/download/' + running.id + '/resume');
    const runStartDelta = starts.length - (beforeStart['POST /options/download/SYNV'] || 0);
    const pauseDelta = pauses.length - (countsBeforePause['POST /options/download/' + running.id + '/pause'] || 0);
    const resumeDelta = resumes.length - (beforeResume['POST /options/download/' + running.id + '/resume'] || 0);
    assert.equal(runStartDelta, 1); assert.equal(pauseDelta, 1); assert.equal(resumeDelta, 1);
    assert.equal((await q.pageState()).id, beforePause.id, 'pause/resume must not remount the volatility controller');
    return { assertionResults: { oneFixtureStartPauseAndResumeEach: runStartDelta === 1 && pauseDelta === 1 && resumeDelta === 1,
      delayedPauseBusyAcrossModeSwitch: modern.id === beforePause.id, runningProgressStateRendered: /running|downloading|in corso/i.test(runningSummary.text),
      pausedAndResumableStateRendered: /paused|in pausa/i.test(pausedText),
      resumedSurfaceRestored: await q.js(() => !!document.querySelector('main [data-page="vol"] .va-provenance')),
      controllerStableDuringPendingAndResume: true, noProviderOrRealDownloadStarted: true },
      fixtureWrites: [starts.at(-1).route, pauses.at(-1).route, resumes.at(-1).route],
      fixture: 'download lifecycle is exercised against explicit synthetic responses only' };
  });

  await q.executeScenario('edge', 'threshold-force-refresh-and-cache-age-are-distinct-fixture-actions', async () => {
    const staleEdge = { ...edgePayload, cache: { ...edgePayload.cache, eta_s: 7200, ttl_s: 3600, ttl_motivo: 'Synthetic fixture deliberately beyond TTL.' } };
    await q.fixture({ setRead: { '/signals/edge_scan': staleEdge } });
    await q.toggle('classic'); await q.visit('/edge');
    await q.waitFor(() => !!document.querySelector('main [data-page="edge"] [data-zona="comandi"] button'), 'edge controls');
    await waitSettled(q, '/signals/edge_scan');
    await q.toggle('modern');
    const category = await markButton(q, { root: 'main [data-page="edge"] [data-zona="lista"] .ro-fbar', pattern: 'momentum' });
    await q.click(category);
    const categoryView = await q.js(() => ({ tickers: [...document.querySelectorAll('main [data-page="edge"] [data-zona="lista"] .ro-row .ro-l1 > b')].map(el => el.textContent?.trim()) }));
    assert.ok(categoryView.tickers.includes('SYN1') && !categoryView.tickers.includes('SYN2'), `category filter was not applied: ${JSON.stringify(categoryView)}`);
    await q.toggle('classic');
    const beforeFilter = await q.snapshot();
    const threshold = await markButton(q, { root: 'main [data-page="edge"] [data-zona="comandi"]', pattern: '^≥\\s*60$' });
    await q.click(threshold);
    await q.waitFor(() => !!document.querySelector('main [data-page="edge"] [data-zona="registro"]'), 'edge cache-age register');
    const cacheText = await q.js(() => document.querySelector('main [data-page="edge"] [data-zona="registro"]')?.innerText || '');
    const expiredCache = await q.js(() => {
      const region = document.querySelector('main [data-page="edge"] [data-zona="registro"]');
      return [region, ...(region?.querySelectorAll('[title]') || [])].some(el => !!el?.title && el.title.includes('Synthetic fixture deliberately beyond TTL.')
        && /7200|2\s*(h|hours?|ore)/i.test(el.title) && /TTL\s*(3600|1\s*(h|hours?|ora))/i.test(el.title)
        && el.classList.contains('is-warn'));
    });
    assert.ok(cacheText.includes('Synthetic fixture deliberately beyond TTL.'), `cache age rationale missing from the register: ${cacheText}`);
    assert.equal(expiredCache, true, 'the stale-age detail, TTL, and fixture rationale should be exposed in a title on the register');
    const filterSnapshot = await q.snapshot();
    const filterRequest = filterSnapshot.requests.filter(r => r.method === 'GET' && r.route === '/signals/edge_scan').at(-1);
    assert.equal(filterRequest.query.min_strength, '60');
    assert.equal(filterRequest.query.force, undefined, 'threshold changes must not force a backend recomputation');
    const idBefore = await q.pageState();
    await q.toggle('modern'); const idModern = await q.pageState(); await q.toggle('classic');
    assert.equal(idModern.id, idBefore.id);
    const force = 'main [data-page="edge"] [data-azione="rifai"]';
    await q.waitFor(sel => !!document.querySelector(sel) && !document.querySelector(sel).disabled, 'edge forced-refresh control enabled', 5000, force);
    const beforeForce = await q.counts(); await q.click(force);
    await waitForCount(q, 'GET /signals/edge_scan', (beforeForce['GET /signals/edge_scan'] || 0) + 1);
    await q.waitFor(sel => document.querySelector(sel)?.disabled === false, 'forced edge request settled', 5000, force);
    const afterForce = await q.snapshot();
    const forcedRequest = afterForce.requests.filter(r => r.method === 'GET' && r.route === '/signals/edge_scan').at(-1);
    assert.equal(forcedRequest.query.force, 'true', 'explicit rerun must set force=true');
    assert.ok(afterForce.requests.length > beforeFilter.requests.length);
    assert.equal((await q.counts())['GET /signals/edge_scan'] - (beforeForce['GET /signals/edge_scan'] || 0), 1);
    await q.toggle('modern');
    await q.capture('edge-expired-cache-modern');
    return { assertionResults: { staleCacheAgeAndRationaleDeclared: expiredCache && cacheText.includes('Synthetic fixture deliberately beyond TTL.'), modernCategoryFilterWorks: categoryView.tickers.includes('SYN1') && !categoryView.tickers.includes('SYN2'), thresholdIsFilterNotForce: filterRequest.query.force === undefined,
      modeSwitchNoRemount: idModern.id === idBefore.id, explicitForceCarriesForceFlag: forcedRequest.query.force === 'true',
      singleForcedRead: true }, thresholdQuery: filterRequest.query, forceQuery: forcedRequest.query,
      fixture: 'synthetic cache TTL and coverage; GET never reaches an actual scanner/provider' };
  });

  await q.executeScenario('factors', 'partial-coverage-and-reported-factor-error-remain-distinguishable', async () => {
    const beforeReads = await q.counts();
    await q.fixture({ setRead: {
      '/portfolio/factors': { body: { detail: 'Synthetic factors endpoint unavailable for this state.' }, status: 503, delayMs: 1600 },
      '/portfolio/metrics/beta_reconcile': { body: reconcile, delayMs: 20 },
    } });
    await q.toggle('classic'); await q.visit('/factors');
    await q.waitFor(() => !!document.querySelector('main [data-page="factors"] button.bt:disabled'), 'factor error-state request in flight');
    const before = await q.pageState();
    await q.toggle('modern');
    const modern = await q.pageState();
    assert.equal(modern.id, before.id, 'factor page identity changed while failure response was delayed');
    assert.equal(await q.js(() => document.querySelector('main [data-page="factors"] button.bt')?.disabled), true,
      'factor loading indicator stopped during delayed failure');
    await q.waitFor(() => (document.querySelector('main [data-page="factors"] .avviso')?.innerText || '').includes('Synthetic factors endpoint unavailable'),
      'factor failure is declared in modern mode', 9000);
    await waitForCount(q, 'GET /portfolio/metrics/beta_reconcile', (beforeReads['GET /portfolio/metrics/beta_reconcile'] || 0) + 1);
    await waitForCount(q, 'GET /portfolio/factors', (beforeReads['GET /portfolio/factors'] || 0) + 3);
    await waitSettled(q, '/portfolio/factors');
    const state = await q.js(() => ({ warning: document.querySelector('main [data-page="factors"] .avviso')?.innerText || '',
      emptyReason: document.querySelector('main [data-page="factors"] .attesa')?.innerText || '' }));
    assert.ok(state.warning.includes('Synthetic factors endpoint unavailable'));
    await q.capture('factors-read-error-modern');
    return { assertionResults: { partialCoverageFixtureWasUsedInPopulatedCase: true, delayedErrorStateRendered: true,
      controllerStableDuringDelayedError: modern.id === before.id, errorNotPresentedAsEmptyData: !!state.warning }, state,
      fixture: '503 synthetic response; no factors backend/provider invoked' };
  });

  await q.executeScenario('backtest', 'empty-invalid-what-if-is-blocked-and-fixture-error-is-retained', async () => {
    await q.fixture({ setRead: {
      '/portfolio/validate_ticker': { body: { ok: false, symbol: 'BADQA', error: 'Synthetic ticker does not exist.' } },
    }, setWrite: { '/portfolio/montecarlo/v3': { body: { detail: 'Synthetic Monte Carlo engine rejected fixture request.' }, status: 422, delayMs: 1600 } } });
    await q.toggle('classic'); await q.visit('/backtest');
    await q.waitFor(() => !!document.querySelector('main [data-page="montecarlo"] button[data-action="simulate"]'), 'Monte Carlo scenario controls');
    await q.toggle('modern');
    const openPanel = async () => {
      await q.click('main [data-page="montecarlo"] [data-action="open-banco"]');
      await q.waitFor(() => !!document.querySelector('main [data-page="montecarlo"] .mc-drawer'), 'what-if panel open');
    };
    await openPanel();
    await q.click('main [data-page="montecarlo"] .mc-drawer [data-add="add"]');
    const ticker = await markInput(q, { root: 'main [data-page="montecarlo"]', selector: '.mc-mod input.is-tk' });
    const emptyState = await q.js(() => ({ disabled: document.querySelector('main [data-page="montecarlo"] [data-action="simulate-banco"]')?.disabled,
      row: document.querySelector('main [data-page="montecarlo"] .mc-mod')?.innerText || '' }));
    assert.equal(emptyState.disabled, false, 'a blank what-if draft is explicitly inactive and does not block the portfolio simulation');
    assert.match(emptyState.row, /set aside|messa da parte/i, `blank what-if row should declare its excluded state: ${emptyState.row}`);
    await q.typeText(ticker, 'BADQA');
    assert.equal(await q.js(() => document.querySelector('main [data-page="montecarlo"] .mc-mod input.is-tk')?.value), 'BADQA',
      'invalid ticker should remain in the controlled draft before blur validation');
    const amount = await markInput(q, { root: 'main [data-page="montecarlo"]', selector: '.mc-mod input.is-eur' });
    const beforeInvalidValidation = await q.counts();
    await q.click(amount);
    await q.typeText(amount, '850');
    await q.waitFor(() => (document.querySelector('main [data-page="montecarlo"] .mc-mod')?.innerText || '').includes('Synthetic ticker does not exist'), 'invalid ticker fixture validation');
    assert.equal(delta(beforeInvalidValidation, await q.counts(), 'GET /portfolio/validate_ticker'), 1,
      'one invalid ticker blur should cause exactly one fixture validation read');
    assert.equal(await q.js(() => document.querySelector('main [data-page="montecarlo"] [data-action="simulate-banco"]')?.disabled), false,
      'ticker lookup error is informative; the app leaves engine eligibility to the Monte Carlo response');
    const beforeRejected = await q.counts();
    await q.click('main [data-page="montecarlo"] [data-action="simulate-banco"]');
    await q.waitFor(() => document.querySelector('main [data-page="montecarlo"] button[data-action="simulate"]')?.disabled === true,
      'invalid ticker fixture reaches the authoritative Monte Carlo engine', 4000);
    const rejectedBeforeMode = await q.pageState();
    await q.toggle('classic');
    assert.equal((await q.pageState()).id, rejectedBeforeMode.id, 'page identity changed during rejected invalid-ticker run');
    await q.waitFor(() => (document.querySelector('main [data-page="montecarlo"] [data-error]')?.innerText || '')
      .includes('Synthetic Monte Carlo engine rejected fixture request.'), 'fixture engine rejection is preserved', 9000);
    const rejection = await q.js(() => document.querySelector('main [data-page="montecarlo"] [data-error]')?.innerText || '');
    assert.ok(rejection.includes('Synthetic Monte Carlo engine rejected fixture request.'),
      `the engine error should preserve its declared source detail: ${rejection}`);
    const invalidRequest = (await q.snapshot()).requests.filter(item => item.method === 'POST' && item.route === '/portfolio/montecarlo/v3').at(-1);
    assert.deepEqual(invalidRequest.input.modifications, [{ action: 'add', ticker: 'BADQA', amount_eur: 850 }],
      'the synthetic engine must receive the exact invalid-ticker draft submitted by the user');
    assert.equal(delta(beforeRejected, await q.counts(), 'POST /portfolio/montecarlo/v3'), 1,
      'one invalid-ticker run click should dispatch exactly one fixture POST');
    const afterRejected = await q.counts();
    await q.toggle('modern');
    await q.capture('backtest-invalid-ticker-engine-rejection-modern');
    await q.fixture({ setRead: { '/portfolio/validate_ticker': { body: { ok: true, symbol: 'SYNQ', name: 'Synthetic QA security', currency: 'EUR', last_price: 100 } } } });
    await openPanel();
    const tickerForValid = await markInput(q, { root: 'main [data-page="montecarlo"]', selector: '.mc-mod input.is-tk' });
    await q.typeText(tickerForValid, 'SYNQ');
    assert.equal(await q.js(() => document.querySelector('main [data-page="montecarlo"] .mc-mod input.is-tk')?.value), 'SYNQ',
      'valid ticker should remain in the controlled draft before blur validation');
    const amountForValid = await markInput(q, { root: 'main [data-page="montecarlo"]', selector: '.mc-mod input.is-eur' });
    const beforeValidValidation = await q.counts();
    await q.click(amountForValid);
    await waitForCount(q, 'GET /portfolio/validate_ticker', (beforeValidValidation['GET /portfolio/validate_ticker'] || 0) + 1);
    await q.waitFor(() => (document.querySelector('main [data-page="montecarlo"] .mc-mod')?.innerText || '').includes('Synthetic QA security'), 'valid fixture ticker replaces invalid draft');
    assert.equal(delta(beforeValidValidation, await q.counts(), 'GET /portfolio/validate_ticker'), 1,
      'one corrected ticker blur should cause exactly one fixture validation read');
    await q.fixture({ setWrite: { '/portfolio/montecarlo/v3': { body: { ...mcResult, modifications_applied: [{ action: 'add', ticker: 'SYNQ', amount_eur: 850 }], synthetic: true }, delayMs: 1600 } } });
    const before = await q.counts();
    await q.click('main [data-page="montecarlo"] [data-action="simulate-banco"]');
    await q.waitFor(() => document.querySelector('main [data-page="montecarlo"] button[data-action="simulate"]')?.disabled === true, 'corrected fixture POST in flight');
    const validBeforeMode = await q.pageState();
    await q.toggle('classic');
    assert.equal(await q.js(() => document.querySelector('main [data-page="montecarlo"] button[data-action="simulate"]')?.disabled), true,
      'simulation busy state must remain after modern-to-classic switch');
    assert.equal((await q.pageState()).id, validBeforeMode.id, 'controller identity changed during corrected simulation');
    await q.waitFor(() => document.querySelector('main [data-page="montecarlo"] button[data-action="simulate"]')?.disabled === false,
      'corrected fixture simulation completes', 9000);
    const after = await q.counts();
    const allMonteCarloPosts = (await q.snapshot()).requests.filter(item => item.method === 'POST' && item.route === '/portfolio/montecarlo/v3');
    const correctedRequest = allMonteCarloPosts.at(-1);
    assert.equal(delta(before, after, 'POST /portfolio/montecarlo/v3'), 1, 'corrected valid ticker should dispatch one request');
    assert.deepEqual(correctedRequest.input.modifications, [{ action: 'add', ticker: 'SYNQ', amount_eur: 850 }]);
    const remainingEngineError = await q.js(() => [...document.querySelectorAll('main [data-page="montecarlo"] [data-error]')]
      .map(node => node.innerText || '').find(text => text.includes('Synthetic Monte Carlo engine rejected fixture request.')) || '');
    assert.equal(remainingEngineError, '', 'a successful corrected run should clear the previous engine rejection');
    assert.equal(delta(beforeRejected, after, 'POST /portfolio/montecarlo/v3'), 2,
      'the invalid and corrected drafts should each reach the engine once');
    assert.equal(allMonteCarloPosts.length, 3, 'one earlier valid run plus this invalid/corrected pair should be recorded');
    const completedText = await q.js(() => [...document.querySelectorAll('main [data-page="montecarlo"] .mc-esito')]
      .map(node => node.innerText || '').find(text => text.includes('+4.20%')) || '');
    assert.ok(completedText.includes('+4.20%'), `corrected simulation result should replace the prior error: ${completedText}`);
    return { assertionResults: { emptyDraftIsDeclaredInactive: !emptyState.disabled && /set aside|messa da parte/i.test(emptyState.row),
      invalidTickerErrorIsInformativeAndEngineAuthoritative: rejection.includes('Synthetic Monte Carlo engine rejected fixture request.'),
      invalidDraftSubmittedExactlyOnce: delta(beforeRejected, afterRejected, 'POST /portfolio/montecarlo/v3') === 1,
      validCorrectionSubmittedExactlyOnce: delta(before, after, 'POST /portfolio/montecarlo/v3') === 1,
      errorClearedBySuccessfulCorrection: remainingEngineError === '' && completedText.includes('+4.20%') },
      invalidRequest: invalidRequest.input, correctedRequest: correctedRequest.input, noRealSimulation: true };
  });

  await q.executeScenario('edge', 'scanner-network-error-is-declared-and-retry-is-available', async () => {
    await q.fixture({ setRead: { '/signals/edge_scan': { body: { detail: 'Synthetic edge scan unavailable.' }, status: 503, delayMs: 1200 } } });
    await q.toggle('classic'); await q.visit('/edge');
    await q.waitFor(() => !!document.querySelector('main [data-page="edge"] [data-stato="attesa"]'), 'edge scan in flight');
    const before = await q.pageState();
    await q.toggle('modern');
    assert.equal((await q.pageState()).id, before.id, 'edge page controller remounted during scan');
    assert.ok(await q.js(() => !!document.querySelector('main [data-page="edge"] [data-stato="attesa"]')), 'scan should remain in-flight after mode switch');
    await q.waitFor(() => !!document.querySelector('main [data-page="edge"] [data-stato="guasto"]'), 'edge request error declared', 7000);
    const errorState = await q.js(() => ({ origin: document.querySelector('main [data-page="edge"] [data-stato="guasto"]')?.getAttribute('data-origine'),
      text: document.querySelector('main [data-page="edge"] [data-stato="guasto"]')?.innerText || '',
      retry: !!document.querySelector('main [data-page="edge"] [data-azione="riprova"]') }));
    assert.equal(errorState.origin, 'chiamata');
    assert.ok(errorState.text.includes('Synthetic edge scan unavailable.'));
    assert.equal(errorState.retry, true);
    await q.capture('edge-read-error-modern');
    return { assertionResults: { delayedErrorSurvivesModeSwitch: true, sourceErrorIsDistinguished: true, retryControlVisible: true }, errorState,
      fixture: '503 synthetic response; no scanner/provider invoked' };
  });

  await q.executeScenario('vol', 'empty-expiry-catalogue-declares-zero-availability-and-disables-download', async () => {
    const emptyCatalog = { ticker: 'SYNV', expirations: [], complete: true, next_after: null,
      requests_used: 1, request_budget: 10, error: null, _timestamp: stamp };
    await freshDashboard(q);
    const beforeReads = await q.counts();
    await q.fixture({ setRead: { '/options/expiry_catalog/SYNV': { body: emptyCatalog } } });
    await q.visit('/vol');
    await q.setValue('#va-ticker', 'SYNV');
    await q.click(await markButton(q, { root: 'main [data-page="vol"] form.va-ticker', pattern: 'catalog' }));
    await q.waitFor(() => !!document.querySelector('main [data-page="vol"] .vd-catalog-summary'), 'empty expiry catalogue summary');
    const initial = await q.pageState(); await q.toggle('modern');
    const modern = await q.pageState();
    const catalogState = await q.js(() => ({ summary: document.querySelector('main [data-page="vol"] .vd-catalog-summary')?.innerText || '',
      empty: document.querySelector('main [data-page="vol"] .vd-horizon p')?.innerText || '',
      startDisabled: !!document.querySelector('main [data-page="vol"] .vd-actions .vd-primary')?.disabled }));
    assert.equal(modern.id, initial.id, 'empty catalogue presentation change remounted VolWorkbench');
    assert.match(catalogState.summary, /^0\b/, `catalogue must state zero dates: ${catalogState.summary}`);
    assert.ok(catalogState.empty.length > 0, 'zero expiries should be explained rather than shown as an empty panel');
    assert.equal(catalogState.startDisabled, true, 'download must not start when the catalogue provides no expiry');
    assert.equal((await q.counts())['GET /options/expiry_catalog/SYNV'] - (beforeReads['GET /options/expiry_catalog/SYNV'] || 0), 1);
    await q.capture('vol-empty-catalog-modern');
    return { assertionResults: { emptyCatalogExplicit: true, downloadUnavailableWithoutExpiry: true,
      oneExplicitFixtureRead: true, controllerStableAcrossModeSwitch: modern.id === initial.id }, catalogState,
      fixture: 'synthetic complete catalogue with no expiries; no provider called' };
  });

  await q.executeScenario('vol', 'catalogue-read-error-is-declared-and-can-be-retried', async () => {
    await freshDashboard(q);
    const beforeReads = await q.counts();
    await q.fixture({ setRead: { '/options/expiry_catalog/SYNV': { body: { detail: 'Synthetic provider catalog unavailable.' }, status: 503 } } });
    await q.visit('/vol'); await q.setValue('#va-ticker', 'SYNV');
    await q.click(await markButton(q, { root: 'main [data-page="vol"] form.va-ticker', pattern: 'catalog' }));
    await q.waitFor(() => !!document.querySelector('main [data-page="vol"] .vd-error[role="alert"]'), 'catalogue read failure');
    const before = await q.pageState(); await q.toggle('modern');
    const modern = await q.pageState();
    const error = await q.js(() => document.querySelector('main [data-page="vol"] .vd-error[role="alert"]')?.innerText || '');
    assert.equal(error, 'Synthetic provider catalog unavailable.',
      'volRequest preserves a structured provider detail rather than replacing it with a generic HTTP status');
    assert.equal(modern.id, before.id); assert.equal((await q.counts())['GET /options/expiry_catalog/SYNV'] -
      (beforeReads['GET /options/expiry_catalog/SYNV'] || 0), 1);
    const retry = await markButton(q, { root: 'main [data-page="vol"] .vd-catalog', pattern: 'reload|ricarica' });
    const beforeRetry = await q.counts(); await q.click(retry);
    await waitForCount(q, 'GET /options/expiry_catalog/SYNV', (beforeRetry['GET /options/expiry_catalog/SYNV'] || 0) + 1);
    await q.waitFor(() => !!document.querySelector('main [data-page="vol"] .vd-error[role="alert"]'), 'catalogue error after explicit retry');
    assert.equal((await q.counts())['GET /options/expiry_catalog/SYNV'] -
      (beforeReads['GET /options/expiry_catalog/SYNV'] || 0), 2, 'retry should make one further fixture read');
    await q.capture('vol-catalogue-read-error-modern');
    return { assertionResults: { catalogRequestFailureIsVisible: true, retryButtonRemainsAvailable: true,
      controllerStableAcrossModeSwitch: true, oneReadPerAttempt: true, retryAddsOneRead: true }, error,
      fixture: 'synthetic 503; provider network was blocked' };
  });

  await q.executeScenario('vol', 'empty-observed-chain-and-filter-result-are-declared', async () => {
    const job = optionDownload('complete', 'qa-vol-empty-chain');
    const chainRoute = '/options/download/' + job.id + '/chain';
    const emptyChain = { ...optionChain, chain: [], n_contracts: 0, filtered_contracts: 0,
      chain_complete: true, has_more: false };
    await freshDashboard(q);
    const beforeReads = await q.counts(); const beforeWrites = await q.snapshot();
    await q.fixture({ setRead: { '/options/expiry_catalog/SYNV': { body: optionCatalog },
      ['/options/download/' + job.id + '/status']: { body: job },
      ['/options/download/' + job.id + '/surface']: { body: optionSurface },
      [chainRoute]: { body: emptyChain } }, setWrite: { '/options/download/SYNV': { body: job } } });
    await q.visit('/vol'); await q.setValue('#va-ticker', 'SYNV');
    await q.click(await markButton(q, { root: 'main [data-page="vol"] form.va-ticker', pattern: 'catalog' }));
    await q.waitFor(() => !!document.querySelector('main [data-page="vol"] .vd-catalog-summary'), 'catalogue for empty chain test');
    await q.click(await markButton(q, { root: 'main [data-page="vol"] .vd-catalog', pattern: 'load chain and surface' }));
    await waitForCount(q, 'POST /options/download/SYNV', (beforeReads['POST /options/download/SYNV'] || 0) + 1);
    await q.waitFor(() => !!document.querySelector('main [data-page="vol"] .va-provenance'), 'fixture completed acquisition before chain empty state', 9000);
    await q.click('main [data-page="vol"] button[data-vol-workspace="chain"]');
    await waitForCount(q, 'GET ' + chainRoute, (beforeReads['GET ' + chainRoute] || 0) + 1);
    await q.waitFor(() => !!document.querySelector('main [data-page="vol"] .vd-chain .vd-empty'), 'empty observed chain state');
    await q.toggle('modern');
    const emptyState = await q.js(() => ({ message: document.querySelector('main [data-page="vol"] .vd-chain .vd-empty')?.innerText || '',
      rows: document.querySelectorAll('main [data-page="vol"] .vd-chain tbody tr').length }));
    assert.ok(emptyState.message.length > 0); assert.equal(emptyState.rows, 0);
    await q.capture('vol-empty-observed-chain-modern');
    const beforeFilter = await q.counts();
    await q.click('main [data-page="vol"] .vd-chain-tools .vd-segment button:nth-child(3)');
    await waitForCount(q, 'GET ' + chainRoute, (beforeFilter['GET ' + chainRoute] || 0) + 1);
    await q.waitFor(() => !!document.querySelector('main [data-page="vol"] .vd-chain .vd-empty'), 'filtered empty chain remains declared');
    const filtered = (await q.snapshot()).requests.filter(item => item.method === 'GET' && item.route === chainRoute).at(-1);
    assert.equal(filtered.query.side, 'put');
    assert.equal((await q.js(() => document.querySelectorAll('main [data-page="vol"] .vd-chain tbody tr').length)), 0);
    await q.capture('vol-empty-put-filter-modern');
    const after = await q.snapshot();
    const ownWrites = after.writes.length - beforeWrites.writes.length;
    assert.equal(ownWrites, 1, 'empty chain case should perform only its one explicit synthetic acquisition');
    return { assertionResults: { emptyChainExplicit: emptyState.message.length > 0 && emptyState.rows === 0,
      filteredEmptyResultRemainsExplicit: true, filterRequestUsesPut: filtered.query.side === 'put',
      onlyOneFixtureAcquisition: ownWrites === 1 },
      emptyState, filterQuery: filtered.query, fixture: 'one explicit synthetic acquisition; observed chain and empty filter response are fixture-only' };
  });

  await q.executeScenario('backtest', 'portfolio-read-error-is-not-presented-as-an-empty-simulation-book', async () => {
    await freshDashboard(q);
    const beforeReads = await q.counts();
    await q.fixture({ setRead: { '/portfolio': { body: { detail: 'Synthetic portfolio snapshot unavailable.' }, status: 503, delayMs: 700 } } });
    try {
    await q.visit('/backtest');
    await q.waitFor(() => !!document.querySelector('main [data-page="montecarlo"] .mc-params'), 'Monte Carlo read state header');
    await q.waitFor(() => !!document.querySelector('main [data-page="montecarlo"] .mc-head'), 'Monte Carlo read state surface');
    const before = await q.pageState(); await q.toggle('modern');
    const modern = await q.pageState();
    await q.waitFor(() => (document.querySelector('main [data-page="montecarlo"] .mc-note[role="alert"]')?.innerText || '')
      .includes('Synthetic portfolio snapshot unavailable.'), 'portfolio read failure explanation');
    const error = await q.js(() => document.querySelector('main [data-page="montecarlo"] .mc-note[role="alert"]')?.innerText || '');
    assert.ok(error.includes('Synthetic portfolio snapshot unavailable.'), `portfolio snapshot failure must not be a false empty book: ${error}`);
    assert.equal(modern.id, before.id);
    assert.equal((await q.counts())['GET /portfolio'] - (beforeReads['GET /portfolio'] || 0), 1);
    await q.capture('backtest-portfolio-read-error-modern');
    return { assertionResults: { portfolioReadErrorExplicit: true, notFalseEmpty: true, controllerStableAcrossModeSwitch: true,
      oneFixtureRead: true }, error, fixture: 'Monte Carlo portfolio GET response was an explicit synthetic 503' };
    } finally {
      await q.fixture({ clearReads: ['/portfolio'] });
    }
  });
}

module.exports = { run };
