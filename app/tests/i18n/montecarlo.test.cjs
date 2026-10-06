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
  // ModernPage defers page-surface construction to a `render` prop so its
  // presentation boundary can catch view-only faults without re-running the
  // page controller. The hand-driven hook harness should probe that callback
  // for controls while leaving all existing controller/API assertions intact.
  render.view = () => typeof render.tree?.props?.render === 'function' ? render.tree.props.render() : render.tree;
  render.effects = async () => { for (const fn of effects) fn(); await new Promise(resolve => setImmediate(resolve)); };
  return { render, calls, load };
}
function find(node, predicate) {
  if (Array.isArray(node)) { for (const child of node) { const found = find(child, predicate); if (found) return found; } }
  if (node && typeof node === 'object' && node.props) { if (predicate(node)) return node; return find(node.props.children, predicate); }
}

// Testo reso senza tag, con i «?» ridotti al segno (la spiegazione vive nel tooltip):
// le attese si leggono come la pagina, «|» separa gli elementi.
const plain = html => html.replace(/<span class="mc-tipbox"[^>]*>[^<]*<\/span>/g, '').replace(/<[^>]+>/g, '|').replace(/\s*\|[\s|]*/g, '|').replace(/[\u00a0\u202f]/g, ' ');
const named = name => n => typeof n.type === 'function' && n.type.name === name;
async function settled(data, languages = ['it', 'en']) {
  const { render } = retained(data), out = {};
  render('it'); await render.effects();
  for (const language of languages) { out[language] = render(language); await render.effects(); }
  return out;
}

test('Monte Carlo preserves the numeric grammar of each draft through an interface language change', () => {
  const load = creaCaricatore(), language = load('i18n/lingua.ts'), mc = load('lib/montecarlo.ts');
  for (const [inputLanguage, amount] of [['it', '1.234,50'], ['en', '1,234.50']]) {
    const rows = [{ action: 'add', ticker: 'SYNTH.X', amount_eur: amount, amount_pct: '', inputLanguage }];
    for (const viewLanguage of ['it', 'en']) {
      language.impostaLinguaCorrente(viewLanguage);
      assert.equal(mc.classificaRiga(rows[0]).stato, 'entra');
      assert.deepEqual(mc.costruisciPayload(rows), [{ action: 'add', ticker: 'SYNTH.X', amount_eur: 1234.5 }]);
      assert.equal(mc.etichettaSimula(mc.contaBanco(rows), false), viewLanguage === 'it' ? 'Simula' : 'Simulate');
    }
  }
});

test('Monte Carlo renders local labels and declared variants while retaining the same simulation and curves', async () => {
  const data = fixture(), before = structuredClone(data), { render, calls } = retained(data);
  render('it'); await render.effects(); const it = render('it'); await render.effects();
  const en = render('en'); await render.effects();
  assert.match(it, /Note del motore/); assert.match(en, /Engine notes/);
  assert.match(it, /Nota dichiarata/); assert.match(en, /Declared note/); assert.match(en, /Declared method/);
  assert.match(it, /1\.234,50/); assert.match(en, /1,234\.50/); assert.match(en, /10\.50%/);
  for (const html of [it, en]) assert.match(html, /Original unmarked returns basis/);
  // tre bande + due tracce + mediana nel cono: le stesse curve in entrambe le lingue
  const paths = html => [...html.matchAll(/<path[^>]* d="([^"]+)"/g)].map(m => m[1]);
  assert.ok(paths(it).length >= 6); assert.deepEqual(paths(it), paths(en));
  assert.deepEqual(calls, [['portfolio'], ['base', { horizon_days: 252, n_sims: 10000, lookback_years: 5, method: 'fhs', drift_mode: 'zero', stress: 'none', force: false }]]);
  assert.deepEqual(data, before);
});

test('the explicit what-if run submits the preserved draft value after language changes', async () => {
  const mods = [{ id: 1, action: 'add', ticker: 'SYNTH.X', amount_eur: '1.234,50', amount_pct: '', inputLanguage: 'it' }];
  const { render, calls } = retained(fixture(), mods);
  render('it'); await render.effects(); render('en'); await render.effects();
  const button = find(render.view(), n => n.type === 'button' && n.props['data-action'] === 'simulate');
  assert.equal(button.props.disabled, false); await button.props.onClick();
  assert.equal(calls.filter(c => c[0] === 'whatif').length, 2);
  const sent = calls.at(-1)[1]; assert.equal(sent.force, true);
  assert.deepEqual(sent.modifications, [{ action: 'add', ticker: 'SYNTH.X', amount_eur: 1234.5 }]);
  assert.equal(mods[0].amount_eur, '1.234,50');
});

test('new rows added in the what-if panel keep the same raw text and amount after switching language', async () => {
  const { render, calls } = retained();
  render('it'); await render.effects(); render('it');
  assert.equal(find(render.view(), named('Banco')), undefined, 'the what-if panel opens only on request');
  find(render.view(), n => n.type === 'button' && n.props['data-action'] === 'open-banco').props.onClick();
  render('it');
  find(render.view(), named('Banco')).props.onAdd('add');
  render('it');
  const panel = () => find(render.view(), named('Banco')).props;
  const id = panel().mods[0].id;
  panel().onUpdate(id, { ticker: 'SYNTH.X' }); render('it');
  panel().onUpdate(id, { amount_eur: '1.234,50' });
  const en = render('en');
  assert.match(en, /value="1\.234,50"/); assert.match(en, /value="SYNTH\.X"/);
  assert.match(plain(en), /\|What-if\|/);
  const run = find(render.view(), n => n.type === 'button' && n.props['data-action'] === 'simulate');
  assert.equal(run.props.disabled, false); await run.props.onClick();
  assert.deepEqual(calls.at(-1)[1].modifications, [{ action: 'add', ticker: 'SYNTH.X', amount_eur: 1234.5 }]);
});

test('a blocking what-if row disables Simulate and says why next to the row', async () => {
  const mods = [{ id: 7, action: 'add', ticker: 'SYNTH.X', amount_eur: '', amount_pct: '', inputLanguage: 'it' }];
  const { render } = retained(fixture(), mods);
  render('it'); await render.effects(); render('it');
  find(render.view(), n => n.type === 'button' && n.props['data-action'] === 'open-banco').props.onClick();
  const it = render('it'), en = render('en');
  assert.equal(find(render.view(), n => n.type === 'button' && n.props['data-action'] === 'simulate').props.disabled, true);
  assert.match(plain(it), /\|importo mancante\|/); assert.match(plain(en), /\|amount missing\|/);
  assert.match(plain(it), /\|0 pronte · 1 da correggere\|/); assert.match(plain(en), /\|0 ready · 1 to fix\|/);
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
    it: ['|P95|1.728 €|n.d.|P90|n.d.|+30,00%|', '|Guadagno oltre il 20%|n.d.|', '|P95|19 su 20 scendono di più|n.d.|',
      '|n.d. traiettorie · 1 titolo|', '|su n.d.|', '|3 punti · 2 tracce su n.d.|', '|CF-VaR 99%|?|n.d.|n.d.|', '|Sharpe|?|n.d.|',
      '|VaR 95%|?|n.d.|n.d.|', '|ES 99%|?|n.d.|n.d.|', '|Volatilità|?|n.d.|', '|Atteso|?|n.d.|'],
    en: ['|P95|€1,728|n/a|P90|n/a|+30.00%|', '|Gain over 20%|n/a|', '|P95|19 in 20 fall further|n/a|',
      '|n/a paths · 1 security|', '|of n/a|', '|3 points · 2 sample paths of n/a|', '|CF-VaR 99%|?|n/a|n/a|', '|Sharpe|?|n/a|',
      '|VaR 95%|?|n/a|n/a|', '|ES 99%|?|n/a|n/a|', '|Volatility|?|n/a|', '|Expected|?|n/a|'],
  };
  for (const language of ['it', 'en']) {
    const text = plain(html[language]);
    for (const phrase of expected[language]) assert.ok(text.includes(phrase), `${language}: ${phrase}`);
  }
  assert.doesNotMatch(html.it, /n\/a/);
  // NAV di partenza assente: la riga di partenza (EUR a 2 decimali) dichiara il buco nella lingua della UI
  const nav = await settled({ ...fixture(), base_nav_eur: null });
  assert.ok(plain(nav.it).includes('|Partenza n.d. · 1 titolo in simulazione|'), 'it: NAV di partenza');
  assert.ok(plain(nav.en).includes('|Start n/a · 1 security simulated|'), 'en: starting NAV');
  assert.doesNotMatch(nav.it, /n\/a/);
});

test('engine notes name method, drift and stress with the labels of the page selectors', async () => {
  const data = fixture();
  delete data.method_description; data._presentation_v1.texts = data._presentation_v1.texts.filter(x => x.path[0] !== 'method_description');
  Object.assign(data, { method: 'block_bootstrap', drift_mode: 'historical', stress_scenario: 'shock_3sigma',
    stress_requested: 'gfc_2008', stress_fallback: true, stress_meta: { fallback_reason: 'Original fallback reason' } });
  const html = await settled(data);
  const expected = {
    it: ['Metodo.</b> Block bootstrap · drift Storico (distorto) · stress Shock −3σ · 10.000 traiettorie · storico 5 anni · calibrazione su 1.000 osservazioni.',
      'Stress non applicato.</b> Richiesto GFC 2008: replay non disponibile — Original fallback reason',
      'Lo scenario GFC 2008 non è stato applicato'],
    en: ['Method.</b> Block bootstrap · drift Historical (biased) · stress −3σ shock · 10,000 paths · 5-year history · calibration over 1,000 observations.',
      'Stress not applied.</b> Requested GFC 2008: replay unavailable — Original fallback reason',
      'The GFC 2008 scenario was not applied'],
  };
  for (const language of ['it', 'en']) {
    for (const phrase of expected[language]) assert.ok(html[language].includes(phrase), `${language}: ${phrase}`);
    // solo le note del motore: i value dei select portano gli id di proposito
    const from = html[language].indexOf('<section class="bbn-card mc-notes">'), notes = html[language].slice(from, html[language].indexOf('</section>', from));
    assert.ok(from > 0 && notes.includes('mc-nrow'));
    assert.doesNotMatch(notes, /block_bootstrap|drift historical|shock_3sigma|gfc_2008/);
  }
  const labels = { fhs: ['FHS · GARCH + bootstrap dei residui', 'FHS · GARCH + residual bootstrap'],
    parametric_t: ['Student-t parametrica (legacy)', 'Parametric Student-t (legacy)'] };
  for (const [method, [it, en]] of Object.entries(labels)) {
    const other = await settled({ ...data, method, drift_mode: 'zero', stress_scenario: 'none', stress_fallback: false });
    assert.ok(other.it.includes(`Metodo.</b> ${it} · drift Zero (neutrale) · stress Nessuno ·`), `it ${method}`);
    assert.ok(other.en.includes(`Method.</b> ${en} · drift Zero (neutral) · stress None ·`), `en ${method}`);
  }
});

test('engine ids without a page label are shown as ids and declared, never given an invented label', async () => {
  const data = fixture();
  delete data.method_description; data._presentation_v1.texts = data._presentation_v1.texts.filter(x => x.path[0] !== 'method_description');
  Object.assign(data, { method: 'synthetic_method', drift_mode: 'risk_neutral_rf', stress_scenario: 'synthetic_stress',
    stress_requested: 'synthetic_request', stress_fallback: true, stress_meta: {} });
  const html = await settled(data);
  const expected = {
    it: ['Metodo.</b> synthetic_method (id del motore senza etichetta) · drift risk_neutral_rf (id del motore senza etichetta) · stress synthetic_stress (id del motore senza etichetta) ·',
      'Richiesto synthetic_request (id del motore senza etichetta): replay non disponibile'],
    en: ['Method.</b> synthetic_method (engine id without a label) · drift risk_neutral_rf (engine id without a label) · stress synthetic_stress (engine id without a label) ·',
      'Requested synthetic_request (engine id without a label): replay unavailable'],
  };
  for (const language of ['it', 'en']) {
    for (const phrase of expected[language]) assert.ok(html[language].includes(phrase), `${language}: ${phrase}`);
  }
});

test('the invested value before and after the what-if is shown in the header in the UI language', async () => {
  const html = await settled({ ...fixture(), nav_pre_eur: 1000, nav_post_eur: 1100 });
  assert.ok(plain(html.it).includes('|Banco di prova|Valore investito|1.000 €|→|1.100 €|+100 €|'));
  assert.ok(plain(html.en).includes('|What-if|Invested value|€1,000|→|€1,100|+€100|'));
  assert.ok(html.it.includes('title="prima"') && html.en.includes('title="before"'));
  assert.doesNotMatch(html.en, /Valore investito|Banco di prova/);
  // senza what-if il confronto non compare
  const base = await settled(fixture());
  assert.doesNotMatch(base.it, /data-cmp/);
});

test('a missing cone or terminal histogram is described in words and keeps the declared field id', async () => {
  const data = fixture(); delete data.fan_bands; delete data.terminal_hist;
  const html = await settled(data);
  const expected = {
    it: ['Cono non disponibile: il payload non porta <b>le bande dei percentili</b> (campo fan_bands: backend da riavviare o versione vecchia del motore). Senza le bande vere qui non si disegna niente: la pagina non inventa una distribuzione.',
      'Distribuzione non disponibile: il payload non porta <b>l’istogramma dei valori a scadenza</b> (campo terminal_hist). Restano i percentili nella tabella qui sotto.'],
    en: ['Cone unavailable: the payload has no <b>percentile bands</b> (fan_bands field: a backend restart or engine update may be needed). Actual bands are required to draw anything: the page does not invent a distribution.',
      'Distribution unavailable: the payload has no <b>terminal value histogram</b> (terminal_hist field). Percentiles remain in the table below.'],
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
    assert.match(html, lang === 'it' ? /Simulazione non riuscita/ : /Simulation failed/);
    assert.match(html, lang === 'it' ? /Portafoglio non disponibile/ : /Portfolio unavailable/);
  }
});

// ── review PR #12 (05/10) ──
// La cella di coda letta come la pagina: dal data-metric al </div> che la chiude.
const cella = (html, metric) => { const from = html.indexOf(`data-metric="${metric}"`); assert.ok(from > 0, metric);
  return plain(html.slice(from, html.indexOf('</div>', from))); };

test('tail risk shows only the euro the payload carries for ES and no euro for the peak-to-trough drawdown', async () => {
  // la fixture non porta es_95_eur/es_99_eur: l'euro dell'ES è un buco da dichiarare, non da ricalcolare
  const html = await settled(fixture());
  assert.match(cella(html.it, 'ES 95%'), /n\.d\./); assert.match(cella(html.en, 'ES 95%'), /n\/a/);
  // il drawdown si misura dal picco, non dalla partenza: un euro sul NAV iniziale sarebbe un numero sbagliato
  for (const lang of ['it', 'en']) assert.doesNotMatch(cella(html[lang], 'Drawdown P5'), /€/);
  // quando il payload porta l'euro dell'ES, si mostra quello
  const withEur = await settled({ ...fixture(), es_95_eur: -432 });
  assert.match(cella(withEur.it, 'ES 95%'), /432 €/); assert.match(cella(withEur.en, 'ES 95%'), /€432/);
});

test('engine notes name the drift actually sent, not the selector changed after the run', async () => {
  const data = fixture(); delete data.drift_mode; delete data.stress_scenario;
  const { render } = retained(data);
  render('it'); await render.effects(); render('it');
  const drift = find(render.view(), n => n.type === 'select' && /Rendimento atteso/.test(n.props['aria-label'] || ''));
  drift.props.onChange({ target: { value: 'historical' } });
  const it = render('it');
  const from = it.indexOf('<section class="bbn-card mc-notes">'), notes = it.slice(from, it.indexOf('</section>', from));
  assert.match(notes, /drift Zero \(neutrale\)/); assert.doesNotMatch(notes, /Storico/);
  assert.match(plain(it), /\|Parametri cambiati\|/);
});

test('editing the what-if rows after a run flags the shown results as stale', async () => {
  const { render } = retained();
  render('it'); await render.effects(); render('it');
  assert.doesNotMatch(plain(render('it')), /\|Modifiche cambiate\|/);
  find(render.view(), n => n.type === 'button' && n.props['data-action'] === 'open-banco').props.onClick(); render('it');
  find(render.view(), named('Banco')).props.onAdd('add'); render('it');
  const panel = () => find(render.view(), named('Banco')).props, id = panel().mods[0].id;
  // una riga senza titolo non entra nel calcolo: non rende vecchi i risultati
  assert.doesNotMatch(plain(render('it')), /\|Modifiche cambiate\|/);
  panel().onUpdate(id, { ticker: 'SYNTH.X', amount_eur: '1000' });
  assert.match(plain(render('it')), /\|Modifiche cambiate\|/); assert.match(plain(render('en')), /\|What-if changes edited\|/);
});

test('the what-if dialog keeps keyboard focus inside while it is open', async () => {
  const { render } = retained();
  render('it'); await render.effects(); render('it');
  find(render.view(), n => n.type === 'button' && n.props['data-action'] === 'open-banco').props.onClick(); render('it');
  const banco = find(render.view(), named('Banco'));
  // il pannello si espande dentro un render vero (useT legge lo store della lingua)
  let tree; renderToStaticMarkup(React.createElement(function Cattura() { tree = banco.type(banco.props); return tree; }));
  const dialog = find(tree, n => n.props && n.props.role === 'dialog');
  assert.equal(typeof dialog.props.onKeyDown, 'function');
  // Tab sull'ultimo elemento torna al primo; Maiusc+Tab sul primo va all'ultimo
  const first = { focus() { moved.push('first'); } }, last = { focus() { moved.push('last'); } }, moved = [];
  let prevented = 0;
  const box = { querySelectorAll: () => [first, last] };
  global.document = { activeElement: last };
  dialog.props.onKeyDown({ key: 'Tab', shiftKey: false, currentTarget: box, preventDefault() { prevented++; } });
  global.document = { activeElement: first };
  dialog.props.onKeyDown({ key: 'Tab', shiftKey: true, currentTarget: box, preventDefault() { prevented++; } });
  assert.deepEqual(moved, ['first', 'last']); assert.equal(prevented, 2);
});

// ── scenario deterministico (sync 2a72bf8) ──
// Il replay copre tutto l'orizzonte: il motore manda le metriche statistiche a null e lo
// dichiara. Numeri inventati interi (regola dei test): NAV 1000, scenario −40% / −400 €.
const NA_DET = ['expected_return_pct', 'median_return_pct', 'stdev_pct', 'sharpe_simulated', 'prob_negative_pct',
  'prob_loss_10pct', 'prob_loss_20pct', 'prob_gain_10pct', 'prob_gain_20pct', 'var_95_pct', 'var_99_pct',
  'var_99_cornish_fisher_pct', 'es_95_pct', 'es_99_pct', 'es_95_eur', 'es_99_eur', 'max_drawdown_p5_pct',
  'max_drawdown_median_pct', 'max_drawdown_p95_pct', 'percentiles_ratio', 'percentiles_eur'];
function deterministicFixture() {
  const data = fixture(), via = [1000, 800, 600];
  Object.assign(data, { base_nav_eur: 1000, stress_scenario: 'gfc_2008', stress_requested: 'gfc_2008', stress_fallback: false,
    stress_meta: { applied: 'gfc_2008', replaced_days: 252, window_loss_pct: -40, window_loss_eur: -400 },
    stress_nature: 'deterministic', stress_nature_label: 'Frase natura it',
    fan_bands: { days: [1, 126, 252], ...Object.fromEntries(['p5', 'p10', 'p25', 'p50', 'p75', 'p90', 'p95'].map(k => [k, via])),
      deterministic: true, label: 'Frase bande it' },
    sample_paths: [[1, 0.8, 0.6]], terminal_hist: { counts: [0, 10000, 0], edges_eur: [500, 550, 650, 700] },
    deterministic_scenario: { label: 'scenario deterministico', scenario: 'gfc_2008', replayed_days: 252, horizon_days: 252,
      scenario_loss_pct: -40, scenario_loss_eur: -400, scenario_max_drawdown_pct: -45, metrics_not_applicable: NA_DET,
      sign_convention: 'Convenzione it', reason: 'Motivo it' } });
  for (const k of NA_DET) data[k] = null;
  data._presentation_v1.texts.push(
    { path: ['stress_nature_label'], it: 'Frase natura it', en: 'Nature sentence en' },
    { path: ['fan_bands', 'label'], it: 'Frase bande it', en: 'Bands sentence en' },
    { path: ['deterministic_scenario', 'label'], it: 'scenario deterministico', en: 'deterministic scenario' },
    { path: ['deterministic_scenario', 'sign_convention'], it: 'Convenzione it', en: 'Convention en' },
    { path: ['deterministic_scenario', 'reason'], it: 'Motivo it', en: 'Reason en' });
  return data;
}
const sezione = (html, cls) => { const from = html.indexOf(`<section class="bbn-card ${cls}"`); assert.ok(from >= 0, cls);
  return html.slice(from, html.indexOf('</section>', from)); };

test('a deterministic scenario shows its outcome and declares the statistical metrics not applicable, never green or as numbers', async () => {
  const html = await settled(deterministicFixture());
  const marker = { it: 'n.a. (scenario deterministico)', en: 'n/a (deterministic scenario)' };
  const expected = {
    it: ['|scenario deterministico · GFC 2008|?|una sola traiettoria|Frase natura it|Esito dello scenario|?|-40,00%|-400 €|Drawdown massimo|?|-45,00%|Sedute replicate|252 su 252|Motivo it|',
      '|Atteso|?|n.a. (scenario deterministico)|', '|Volatilità|?|n.a. (scenario deterministico)|', '|Sharpe|?|n.a. (scenario deterministico)|',
      '|Perdita di qualsiasi entità|n.a. (scenario deterministico)|', '|Guadagno oltre il 20%|n.a. (scenario deterministico)|',
      '|P95|n.a. (scenario deterministico)|n.a. (scenario deterministico)|', '|ES 99%|?|n.a. (scenario deterministico)|n.a. (scenario deterministico)|',
      '|Traiettoria|Partenza|Frase bande it|'],
    en: ['|deterministic scenario · GFC 2008|?|a single path|Nature sentence en|Scenario outcome|?|-40.00%|-€400|Maximum drawdown|?|-45.00%|Replayed sessions|252 of 252|Reason en|',
      '|Expected|?|n/a (deterministic scenario)|', '|Volatility|?|n/a (deterministic scenario)|', '|Sharpe|?|n/a (deterministic scenario)|',
      '|Any loss|n/a (deterministic scenario)|', '|Gain over 20%|n/a (deterministic scenario)|',
      '|P95|n/a (deterministic scenario)|n/a (deterministic scenario)|', '|ES 99%|?|n/a (deterministic scenario)|n/a (deterministic scenario)|',
      '|Path|Start|Bands sentence en|'],
  };
  for (const language of ['it', 'en']) {
    const page = html[language], text = plain(page);
    for (const phrase of expected[language]) assert.ok(text.includes(phrase), `${language}: ${phrase}`);
    // `null >= 0` è true in JS: nessuna metrica non applicabile esce colorata come guadagno o perdita
    for (const cls of ['mc-esito', 'mc-prob', 'mc-pct', 'mc-risk', 'mc-depth']) {
      const box = sezione(page, cls);
      assert.doesNotMatch(box, /mc-up|mc-down|is-su|is-giu/, `${language} ${cls}: no sign colour`);
      // ogni valore dichiarato non applicabile porta solo il segnaposto, mai un numero
      const values = [...box.matchAll(/class="num[^"]*is-na[^"]*">([^<]*)</g)].map(m => m[1]);
      assert.ok(values.length > 0, `${language} ${cls}: declared values`);
      for (const v of values) assert.equal(v, marker[language], `${language} ${cls}`);
    }
    // l'esito dello scenario ha il segno: −40% è una perdita, rossa
    const det = sezione(page, 'mc-det');
    assert.match(det, /class="num mc-down">-40[.,]00%</);
    // sul grafico: niente bande né etichette dei percentili, una traiettoria dichiarata
    const cone = sezione(page, 'mc-cone');
    assert.doesNotMatch(cone, /p5–p95|mc-band|>P95</);
    assert.match(cone, /data-det-bands/);
  }
  assert.doesNotMatch(html.it, /n\/a/); assert.doesNotMatch(html.en, /n\.a\. \(scenario/);
});

test('a stress fixed then simulated declares that the metrics are conditional on the scenario', async () => {
  const data = fixture();
  Object.assign(data, { stress_scenario: 'shock_3sigma', stress_nature: 'fixed_then_simulated', stress_nature_label: 'Condizionate it', deterministic_scenario: null });
  data._presentation_v1.texts.push({ path: ['stress_nature_label'], it: 'Condizionate it', en: 'Conditional en' });
  const html = await settled(data);
  assert.ok(plain(html.it).includes('|Condizionate it|')); assert.ok(plain(html.en).includes('|Conditional en|'));
  // le metriche restano numeri veri: niente blocco deterministico né segnaposto
  for (const language of ['it', 'en']) assert.doesNotMatch(html[language], /data-deterministic|is-na/);
});

test('the simulation time stamp carries the year', async () => {
  const html = await settled(fixture());
  assert.ok(plain(html.it).includes('|Calcolata 12 set 2026 · 10:41|'));
  assert.match(plain(html.en), /\|Computed 12 Sept? 2026 · 10:41\|/);
});

test('the what-if tabs are real tabs connected to their panels, with arrow keys', async () => {
  const { render } = retained({ ...fixture(), weights_pre: { 'SYNTH.X': 1 }, weights_post: { 'SYNTH.X': 0.5, 'SYNTH.Y': 0.5 } });
  render('it'); await render.effects(); render('it');
  find(render.view(), n => n.type === 'button' && n.props['data-action'] === 'open-banco').props.onClick(); render('it');
  const banco = find(render.view(), named('Banco'));
  const draw = props => { let tree; renderToStaticMarkup(React.createElement(function Cattura() { tree = banco.type(props); return tree; })); return tree; };
  const tree = draw(banco.props);
  const list = find(tree, n => n.props && n.props.role === 'tablist');
  assert.ok(list.props['aria-label']);
  const tabs = ['mod', 'pesi'].map(id => find(tree, n => n.props && n.props.role === 'tab' && n.props.id === `mc-tab-${id}`));
  const panels = ['mod', 'pesi'].map(id => find(tree, n => n.props && n.props.role === 'tabpanel' && n.props.id === `mc-panel-${id}`));
  tabs.forEach((tab, i) => {
    assert.equal(tab.props['aria-controls'], panels[i].props.id);
    assert.equal(panels[i].props['aria-labelledby'], tab.props.id);
  });
  assert.deepEqual(tabs.map(t => [t.props['aria-selected'], t.props.tabIndex]), [[true, 0], [false, -1]]);
  assert.deepEqual(panels.map(p => p.props.hidden), [false, true]);
  // freccia a destra: scheda «pesi», e il fuoco la segue
  const chosen = [], focused = [];
  global.document = { getElementById: id => ({ focus() { focused.push(id); } }) };
  const press = (props, key) => find(draw(props), n => n.props && n.props.role === 'tablist').props.onKeyDown({ key, preventDefault() {} });
  press({ ...banco.props, onTab: t => chosen.push(t) }, 'ArrowRight');
  press({ ...banco.props, tab: 'pesi', onTab: t => chosen.push(t) }, 'Home');
  assert.deepEqual(chosen, ['pesi', 'mod']); assert.deepEqual(focused, ['mc-tab-pesi', 'mc-tab-mod']);
  const onPesi = draw({ ...banco.props, tab: 'pesi' });
  assert.equal(find(onPesi, n => n.props && n.props.id === 'mc-panel-pesi').props.hidden, false);
  assert.equal(find(onPesi, n => n.props && n.props.id === 'mc-tab-pesi').props['aria-selected'], true);
  // senza confronto dei pesi nel payload la scheda è spenta e le frecce restano su «Modifiche»
  const without = []; press({ ...banco.props, result: fixture(), onTab: t => without.push(t) }, 'ArrowRight');
  assert.deepEqual(without, ['mod']);
});
