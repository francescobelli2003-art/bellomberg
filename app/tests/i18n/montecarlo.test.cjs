const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');
ambienteBrowser();

function fixture() {
  const ratios = { p5: 0.7, p10: 0.8, p25: 0.9, p50: 1.05, p75: 1.2, p90: 1.3, p95: 1.4 };
  const values = Object.fromEntries(Object.entries(ratios).map(([k, v]) => [k, v * 1234.5]));
  return { base_nav_eur: 1234.5, timestamp: '2026-09-12T10:41:00', n_assets: 1, n_sims: 10000,
    horizon_days: 252, horizon_years: 1, lookback_years: 5, lookback_days_calibration: 1000,
    method: 'fhs', method_description: 'Metodo dichiarato', drift_mode: 'zero', stress_scenario: 'none',
    calibration_note: 'Nota dichiarata', returns_basis: 'Original unmarked returns basis',
    expected_return_pct: 5.5, median_return_pct: 5, stdev_pct: 10.5, sharpe_simulated: 0.45,
    percentiles_ratio: ratios, percentiles_eur: values,
    fan_bands: { days: [0, 126, 252], ...Object.fromEntries(Object.entries(values).map(([k, v]) => [k, [1234.5, (1234.5 + v) / 2, v]])) },
    sample_paths: [[1, 0.8, 1.1], [1, 1.2, 1.3]], sample_paths_days: [0, 126, 252],
    terminal_hist: { counts: [1000, 5000, 4000], edges_eur: [600, 1000, 1400, 1800] },
    var_95_pct: -30, var_99_pct: -40, es_95_pct: -35, es_99_pct: -45,
    max_drawdown_median_pct: -15, max_drawdown_p5_pct: -35, max_drawdown_p95_pct: -5,
    prob_negative_pct: 30, prob_loss_10pct: 20, prob_loss_20pct: 10, prob_gain_10pct: 25, prob_gain_20pct: 15,
    _presentation_v1: { version: 1, texts: [
      { path: ['method_description'], it: 'Metodo dichiarato', en: 'Declared method' },
      { path: ['calibration_note'], it: 'Nota dichiarata', en: 'Declared note' },
    ] },
  };
}
function retained(data = fixture(), mods = [], overrides = {}) {
  let state = 0, memoIndex = 0, effectIndex = 0, refIndex = 0;
  const values = { 6: mods }, memos = [], refs = [], effects = [], effectDeps = [], calls = [];
  const memo = (compute, deps) => { const at = memoIndex++, before = memos[at];
    if (!before || !deps || deps.some((v, i) => !Object.is(v, before.deps[i]))) memos[at] = { deps, value: compute() };
    return memos[at].value; };
  const effect = (fn, deps) => { const at = effectIndex++, before = effectDeps[at];
    if (!before || !deps || deps.some((v, i) => !Object.is(v, before[i]))) effects.push(fn); effectDeps[at] = deps; };
  const load = creaCaricatore({ stub: {
    react: { ...React, useEffect: effect, useLayoutEffect: effect, useMemo: memo, useCallback: (fn, deps) => memo(() => fn, deps),
      useRef(initial) { const at = refIndex++; return refs[at] ||= { current: initial }; },
      useState(initial) { const at = state++; if (!(at in values)) values[at] = typeof initial === 'function' ? initial() : initial;
        return [values[at], value => { values[at] = typeof value === 'function' ? value(values[at]) : value; }]; } },
    '@/lib/useBox': { useBox: () => [{ current: null }, { w: 640, h: 320 }] },
    '@/lib/api': { Bellomberg: {
      portfolio: async () => { calls.push(['portfolio']); return { positions: [{ ticker: 'SYNTH.X' }] }; },
      portfolioMonteCarlo: async args => { calls.push(['base', args]); return data; },
      portfolioMonteCarloV3: async args => { calls.push(['whatif', args]); return data; },
      ...overrides,
    } },
  } });
  const language = load('i18n/lingua.ts'), Page = load('pages/MonteCarloPage.tsx').default;
  function Capture() { render.tree = Page(); return render.tree; }
  const render = selected => { state = memoIndex = effectIndex = refIndex = 0; effects.length = 0; language.impostaLinguaCorrente(selected); return renderToStaticMarkup(React.createElement(Capture)); };
  render.effects = async () => { for (const fn of effects) fn(); await new Promise(resolve => setImmediate(resolve)); };
  return { render, calls, load };
}
function find(node, predicate) {
  if (Array.isArray(node)) { for (const child of node) { const found = find(child, predicate); if (found) return found; } }
  if (node && typeof node === 'object' && node.props) { if (predicate(node)) return node; return find(node.props.children, predicate); }
}

test('Monte Carlo preserves the numeric grammar of each draft through an interface language change', () => {
  const load = creaCaricatore(), language = load('i18n/lingua.ts'), mc = load('lib/montecarlo.ts');
  for (const [inputLanguage, amount] of [['it', '1.234,50'], ['en', '1,234.50']]) {
    const rows = [{ action: 'add', ticker: 'SYNTH.X', amount_eur: amount, amount_pct: '', inputLanguage }];
    for (const viewLanguage of ['it', 'en']) {
      language.impostaLinguaCorrente(viewLanguage);
      assert.equal(mc.classificaRiga(rows[0]).stato, 'entra');
      assert.deepEqual(mc.costruisciPayload(rows), [{ action: 'add', ticker: 'SYNTH.X', amount_eur: 1234.5 }]);
      assert.equal(mc.etichettaSimula(mc.contaBanco(rows), false), viewLanguage === 'it' ? 'SIMULA' : 'SIMULATE');
    }
  }
});

test('Monte Carlo renders local labels and declared variants while retaining the same simulation and curves', async () => {
  const data = fixture(), before = structuredClone(data), { render, calls } = retained(data);
  render('it'); await render.effects(); const it = render('it'); await render.effects();
  const en = render('en'); await render.effects();
  assert.match(it, /NOTE DEL MOTORE/); assert.match(en, /ENGINE NOTES/);
  assert.match(it, /Nota dichiarata/); assert.match(en, /Declared note/); assert.match(en, /Declared method/);
  assert.match(it, /1\.234,50/); assert.match(en, /1,234\.50/); assert.match(en, /10\.50%/);
  for (const html of [it, en]) assert.match(html, /Original unmarked returns basis/);
  const paths = html => [...html.matchAll(/<path[^>]* d="([^"]+)"/g)].map(m => m[1]);
  assert.ok(paths(it).length >= 10); assert.deepEqual(paths(it), paths(en));
  assert.deepEqual(calls, [['portfolio'], ['base', { horizon_days: 252, n_sims: 10000, lookback_years: 5, method: 'fhs', drift_mode: 'zero', stress: 'none', force: false }]]);
  assert.deepEqual(data, before);
});

test('the explicit what-if run submits the preserved draft value after language changes', async () => {
  const mods = [{ id: 1, action: 'add', ticker: 'SYNTH.X', amount_eur: '1.234,50', amount_pct: '', inputLanguage: 'it' }];
  const { render, calls } = retained(fixture(), mods);
  render('it'); await render.effects(); render('en'); await render.effects();
  const button = find(render.tree, n => n.type === 'button' && n.props.className === 'go');
  assert.equal(button.props.disabled, false); await button.props.onClick();
  assert.equal(calls.filter(c => c[0] === 'whatif').length, 2);
  const sent = calls.at(-1)[1]; assert.equal(sent.force, true);
  assert.deepEqual(sent.modifications, [{ action: 'add', ticker: 'SYNTH.X', amount_eur: 1234.5 }]);
  assert.equal(mods[0].amount_eur, '1.234,50');
});

test('new rows capture input grammar and keep the same raw text and amount after switching language', async () => {
  const { render, calls } = retained();
  render('it'); await render.effects(); render('it');
  const add = find(render.tree, n => n.type === 'button' && n.props.className === 'addb'); add.props.onClick();
  render('it');
  find(render.tree, n => n.type === 'input' && n.props.placeholder?.startsWith('ticker')).props.onChange({ target: { value: 'SYNTH.X' } });
  render('it');
  find(render.tree, n => n.type === 'input' && n.props.placeholder === 'importo in euro').props.onChange({ target: { value: '1.234,50' } });
  const en = render('en');
  assert.match(en, /value="1\.234,50"/);
  const run = find(render.tree, n => n.type === 'button' && n.props.className === 'go');
  assert.equal(run.props.disabled, false); await run.props.onClick();
  assert.deepEqual(calls.at(-1)[1].modifications, [{ action: 'add', ticker: 'SYNTH.X', amount_eur: 1234.5 }]);
});

test('numeric draft errors follow the UI language while preserving original input grammar', () => {
  const load = creaCaricatore(), language = load('i18n/lingua.ts'), mc = load('lib/montecarlo.ts');
  const row = { action: 'add', ticker: 'SYNTH.X', amount_eur: '1.234', amount_pct: '', inputLanguage: 'it' };
  language.impostaLinguaCorrente('it'); const it = mc.classificaRiga(row);
  language.impostaLinguaCorrente('en'); const en = mc.classificaRiga(row);
  assert.equal(it.stato, 'blocca'); assert.equal(en.stato, 'blocca');
  assert.notEqual(it.motivo, en.motivo); assert.match(en.motivo, /ambiguous/i);
  assert.equal(row.amount_eur, '1.234');
});

async function settled(data, languages = ['it', 'en']) {
  const { render } = retained(data), out = {};
  render('it'); await render.effects();
  for (const language of languages) { out[language] = render(language); await render.effects(); }
  return out;
}

test('unavailable percentiles, probabilities, drawdowns and path counts use the marker of the UI language', async () => {
  const data = fixture();
  delete data.percentiles_ratio.p95; delete data.prob_gain_20pct; delete data.max_drawdown_p95_pct; data.n_sims = null;
  // Buchi che stampava lib/format (segnaposto fisso 'n/a'): CF-VaR assente dalla fixture,
  // sharpe, VaR 95%, ES 99% e rendimento atteso a null, deviazione standard non finita,
  // valore in EUR del p90 assente.
  delete data.var_99_cornish_fisher_pct; data.sharpe_simulated = null; data.var_95_pct = null; data.es_99_pct = null; data.stdev_pct = NaN;
  data.expected_return_pct = null; delete data.percentiles_eur.p90;
  const html = await settled(data);
  // Frasi attese scritte qui, una per ogni punto che stampava il segnaposto fisso.
  const expected = {
    it: ['<td class="num" style="font-weight:600;color:#8D9FC4">n.d.</td>', 'text-anchor="end" font-family="&#x27;JetBrains Mono&#x27;, monospace">n.d.</text>',
      '>p95 n.d.</text>', 'SU n.d. TRAIETTORIE',
      '<div class="k">CF-VaR 99%</div><div class="v num">n.d.</div>', '<i>sharpe</i><b class="num" style="color:#FFA51E">n.d.</b>',
      '<div class="k">VaR 95%</div><div class="v num">n.d.</div>', '<div class="k">ES 99%</div><div class="v num">n.d.</div>',
      '<b class="num" style="color:#29D3F2">n.d.</b>', '<i>atteso</i><b class="num" style="font-weight:600;color:#21E0A0">n.d.</b>',
      '<td>P90</td><td class="num" style="color:#ECF1FA">n.d.</td>'],
    en: ['<td class="num" style="font-weight:600;color:#8D9FC4">n/a</td>', 'text-anchor="end" font-family="&#x27;JetBrains Mono&#x27;, monospace">n/a</text>',
      '>p95 n/a</text>', 'OVER n/a PATHS',
      '<div class="k">CF-VaR 99%</div><div class="v num">n/a</div>', '<i>sharpe</i><b class="num" style="color:#FFA51E">n/a</b>',
      '<div class="k">VaR 95%</div><div class="v num">n/a</div>', '<div class="k">ES 99%</div><div class="v num">n/a</div>',
      '<b class="num" style="color:#29D3F2">n/a</b>', '<b class="num" style="font-weight:600;color:#21E0A0">n/a</b>',
      '<td>P90</td><td class="num" style="color:#ECF1FA">n/a</td>'],
  };
  for (const language of ['it', 'en']) {
    for (const phrase of expected[language]) assert.ok(html[language].includes(phrase), `${language}: ${phrase}`);
  }
  assert.doesNotMatch(html.it, /n\/a/);
  // NAV di partenza assente: la cifra grande (EUR a 2 decimali) dichiara il buco nella lingua della UI
  const nav = await settled({ ...fixture(), base_nav_eur: null });
  assert.ok(nav.it.includes('<div class="big num">n.d.</div>'), 'it: NAV di partenza');
  assert.ok(nav.en.includes('<div class="big num">n/a</div>'), 'en: starting NAV');
  assert.doesNotMatch(nav.it, /n\/a/);
});

test('engine notes name method, drift and stress with the labels of the page selectors', async () => {
  const data = fixture();
  delete data.method_description; data._presentation_v1.texts = data._presentation_v1.texts.filter(x => x.path[0] !== 'method_description');
  Object.assign(data, { method: 'block_bootstrap', drift_mode: 'historical', stress_scenario: 'shock_3sigma',
    stress_requested: 'gfc_2008', stress_fallback: true, stress_meta: { fallback_reason: 'Original fallback reason' } });
  const html = await settled(data);
  const expected = {
    it: ['[1] Metodo.</b> Block bootstrap · drift storico (distorto) · stress shock −3σ · 10.000 traiettorie · lookback 5Y · calibrazione su 1000 osservazioni.',
      '[!] Stress in fallback.</b> richiesto GFC 2008, replay non disponibile — Original fallback reason'],
    en: ['[1] Method.</b> Block bootstrap · drift historical (biased) · stress shock −3σ · 10,000 paths · lookback 5Y · calibration over 1000 observations.',
      '[!] Stress fallback.</b> requested GFC 2008, replay unavailable — Original fallback reason'],
  };
  for (const language of ['it', 'en']) {
    for (const phrase of expected[language]) assert.ok(html[language].includes(phrase), `${language}: ${phrase}`);
    // solo le note del motore: i value dei select portano gli id di proposito
    const from = html[language].indexOf('<div class="notes">'), notes = html[language].slice(from, html[language].indexOf('</div></div></div>', from));
    assert.ok(from > 0 && notes.includes('[1]'));
    assert.doesNotMatch(notes, /block_bootstrap|drift historical ·|shock_3sigma|gfc_2008/);
  }
  const labels = { fhs: ['FHS · GARCH + bootstrap residui', 'FHS · GARCH + residual bootstrap'],
    parametric_t: ['Student-t parametrica (legacy)', 'Parametric Student-t (legacy)'] };
  for (const [method, [it, en]] of Object.entries(labels)) {
    const other = await settled({ ...data, method, drift_mode: 'zero', stress_scenario: 'none', stress_fallback: false });
    assert.ok(other.it.includes(`[1] Metodo.</b> ${it} · drift zero (neutrale) · stress nessuno ·`), `it ${method}`);
    assert.ok(other.en.includes(`[1] Method.</b> ${en} · drift zero (neutral) · stress none ·`), `en ${method}`);
  }
});

test('engine ids without a page label are shown as ids and declared, never given an invented label', async () => {
  const data = fixture();
  delete data.method_description; data._presentation_v1.texts = data._presentation_v1.texts.filter(x => x.path[0] !== 'method_description');
  Object.assign(data, { method: 'synthetic_method', drift_mode: 'risk_neutral_rf', stress_scenario: 'synthetic_stress',
    stress_requested: 'synthetic_request', stress_fallback: true, stress_meta: {} });
  const html = await settled(data);
  const expected = {
    it: ['[1] Metodo.</b> synthetic_method (id del motore senza etichetta) · drift risk_neutral_rf (id del motore senza etichetta) · stress synthetic_stress (id del motore senza etichetta) ·',
      'richiesto synthetic_request (id del motore senza etichetta), replay non disponibile'],
    en: ['[1] Method.</b> synthetic_method (engine id without a label) · drift risk_neutral_rf (engine id without a label) · stress synthetic_stress (engine id without a label) ·',
      'requested synthetic_request (engine id without a label), replay unavailable'],
  };
  for (const language of ['it', 'en']) {
    for (const phrase of expected[language]) assert.ok(html[language].includes(phrase), `${language}: ${phrase}`);
  }
});

test('NAV before and after the what-if follow the wording of the weight table', async () => {
  const html = await settled({ ...fixture(), nav_pre_eur: 1000, nav_post_eur: 1100 });
  assert.ok(html.it.includes('<div class="k">NAV pre</div>') && html.it.includes('<div class="k">NAV post</div>'));
  assert.ok(html.en.includes('<div class="k">NAV before</div>') && html.en.includes('<div class="k">NAV after</div>'));
  assert.doesNotMatch(html.en, /NAV pre|NAV post/);
});

test('a missing cone or terminal histogram is described in words and keeps the declared field id', async () => {
  const data = fixture(); delete data.fan_bands; delete data.terminal_hist;
  const html = await settled(data);
  const expected = {
    it: ['CONO NON DISPONIBILE — il payload non porta <b>le bande dei percentili</b> (campo fan_bands: backend da riavviare o versione vecchia del motore). Senza le bande vere qui non si disegna niente: la UI non inventa una distribuzione.',
      'PROFILO NON DISPONIBILE — il payload non porta <b>l’istogramma dei valori a scadenza</b> (campo terminal_hist). Restano i percentili nella tabella qui sotto.'],
    en: ['CONE UNAVAILABLE — the payload has no <b>percentile bands</b> (fan_bands field: a backend restart or engine update may be needed). Actual bands are required to draw a distribution.',
      'DISTRIBUTION UNAVAILABLE — the payload has no <b>terminal value histogram</b> (terminal_hist field). Percentiles remain in the table below.'],
  };
  for (const language of ['it', 'en']) {
    for (const phrase of expected[language]) assert.ok(html[language].includes(phrase), `${language}: ${phrase}`);
    assert.doesNotMatch(html[language], /<b>(fan_bands|terminal_hist)<\/b>/);
  }
});

test('simulation and portfolio read errors are explicit and preserve structured source details in both languages', async () => {
  const { render } = retained(fixture(), [], {
    portfolio: async () => { throw { response: { data: { detail: [{ msg: 'Original portfolio failure' }] } } }; },
    portfolioMonteCarlo: async () => { throw { response: { data: { detail: [{ msg: 'Original simulation failure' }] } } }; },
  });
  render('it'); await render.effects();
  for (const lang of ['it', 'en']) {
    const html = render(lang); assert.match(html, /Original portfolio failure/); assert.match(html, /Original simulation failure/);
    assert.match(html, lang === 'it' ? /SIMULAZIONE FALLITA/ : /SIMULATION FAILED/);
  }
});
