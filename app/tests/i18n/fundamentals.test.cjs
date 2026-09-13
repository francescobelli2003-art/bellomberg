const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');
ambienteBrowser();

function fixture() {
  return { ticker: 'SYNTH.X', file: 'VAL_SYNTH.X.xlsx', dir: 'synthetic', engine: 'operating',
    canonical: true, identity_status: 'canonical', matched: false, flagged: false,
    snapshot_id: 'a'.repeat(64), generation_id: 'synthetic-generation', fair_value: 1234.56, upside_pct: 14.7,
    sanity_severity: 'OK', analytical_quality: { status: 'DOCUMENTATA', issues: [] },
    valuation_usability: { usable: true, reasons: [], missing_fields: [] },
    valuation_decision: { method_id: 'operating_fcff', decision_status: 'resolved', support_status: 'integrated', requirements_status: 'complete', missing_fields: [], rationale: 'Original documented rationale' },
    variant_view: 'Original analyst thesis', detail: { engine: 'operating', sanity: { severity: 'OK' } } };
}
function retained(model, extra = {}, api = {}) {
  let state = 0, memoIndex = 0, effectIndex = 0;
  const memos = [], values = { 0: model ? [model] : [], ...extra }, effects = [], effectDeps = [], cleanups = [];
  const memo = (compute, deps) => {
    const at = memoIndex++, before = memos[at];
    if (!before || !deps || deps.some((value, i) => !Object.is(value, before.deps[i]))) memos[at] = { deps, value: compute() };
    return memos[at].value;
  };
  const load = creaCaricatore({ stub: {
    react: { ...React, useEffect(effect, deps) { const at = effectIndex++, before = effectDeps[at];
        if (!before || !deps || deps.some((v, i) => !Object.is(v, before[i]))) effects.push(() => { cleanups[at]?.(); cleanups[at] = effect(); });
        effectDeps[at] = deps; }, useMemo: memo, useCallback: (fn, deps) => memo(() => fn, deps),
      useState(initial) { const at = state++; if (!(at in values)) values[at] = typeof initial === 'function' ? initial() : initial;
        return [values[at], value => { values[at] = typeof value === 'function' ? value(values[at]) : value; }]; } },
    '@/lib/api': { Bellomberg: api, API_BASE: 'http://synthetic.invalid' },
  } });
  const language = load('i18n/lingua.ts'), Page = load('pages/FundamentalsPage.tsx').default;
  const render = selected => { state = memoIndex = effectIndex = 0; effects.length = 0; language.impostaLinguaCorrente(selected); return renderToStaticMarkup(React.createElement(Page)); };
  render.effects = async () => { for (const effect of effects) effect(); await new Promise(resolve => setImmediate(resolve)); };
  return render;
}

test('F17 changes labels and number presentation, keeping values, method IDs and stored prose intact', () => {
  const model = fixture(), before = structuredClone(model), render = retained(model);
  const it = render('it'), en = render('en');
  assert.match(it, /MODELLI DI VALUTAZIONE/); assert.match(en, /VALUATION MODELS/);
  assert.match(it, /1\.234,56/); assert.match(en, /1,234\.56/);
  for (const html of [it, en]) {
    assert.match(html, /operating_fcff/); assert.match(html, /Original documented rationale/); assert.match(html, /Original analyst thesis/); assert.match(html, />OK</);
  }
  assert.deepEqual(model, before);
});

test('missing valuation guards stay closed in both languages and their UI reason follows locale', () => {
  const model = fixture(); delete model.valuation_usability;
  const render = retained(model), it = render('it'), en = render('en');
  for (const html of [it, en]) { assert.doesNotMatch(html, /1234|1\.234|1,234|>OK</); }
  assert.match(it, /Verifica della valutazione mancante/);
  assert.match(en, /Valuation verification missing/);
});

test('Gordon warning highlights equivalent Italian and English engine warnings', () => {
  for (const warning of ['ATTENZIONE: original engine evidence', 'WARNING: original engine evidence']) {
    const model = fixture(); model.detail.holding_irr = { gordon_check: warning };
    const render = retained(model);
    for (const language of ['it', 'en']) {
      const html = render(language);
      assert.ok(html.includes(`<span class="text-amber">${warning}</span>`));
    }
  }
});

test('vehicle profiles do not invent a private trading veto and keep actual engine warnings', () => {
  for (const profile of ['dat_bitcoin', 'dat_hype']) {
    const model = fixture(); model.engine = 'mnav';
    model.detail = { engine: 'mnav', profile_key: profile, mnav: 1.2, mnav_ev: 1.2, warnings: ['Original supplied operational constraint'], sanity: { severity: 'OK' } };
    const render = retained(model);
    for (const language of ['it', 'en']) {
      const html = render(language);
      assert.doesNotMatch(html, /veto|nessuna proposta operativa|no trade proposal/);
      assert.match(html, /Original supplied operational constraint/);
    }
  }
});

test('current registry display descriptors are separate from the original model rationale', () => {
  const model = fixture();
  model.presentation = { decision_display: { method_rationale: 'Current registry descriptor', support_note: 'Current registry support', registry_version: 'synthetic-v1' }, requirements_display: null };
  const html = retained(model)('en');
  assert.match(html, /Current registry descriptor/); assert.match(html, /Current registry support/);
  assert.match(html, /Original documented rationale/); assert.match(html, /Original analyst thesis/);
  assert.match(html, /Original model text · language unknown/);
});

test('only the read-only model endpoint is repeated for language and late old responses are discarded', async () => {
  let releaseOld, reads = 0;
  const fresh = fixture(); fresh.presentation = { decision_display: { method_rationale: 'Current English registry descriptor', support_note: null, registry_version: 'synthetic-v1' }, requirements_display: null };
  const render = retained(null, {}, { valuationModels: () => ++reads === 1 ? new Promise(resolve => { releaseOld = resolve; }) : Promise.resolve({ models: [fresh] }) });
  render('it'); await render.effects();
  render('en'); await render.effects();
  releaseOld({ models: [{ ...fixture(), variant_view: 'Late obsolete model response' }] });
  await new Promise(resolve => setImmediate(resolve));
  const html = render('en');
  assert.equal(reads, 2); assert.match(html, /Current English registry descriptor/);
  assert.match(html, /Current registry descriptors · English/);
  assert.doesNotMatch(html, /Late obsolete model response/);
  assert.match(html, /Original analyst thesis/);
});

test('an unread initial archive does not claim zero usable models or a known empty archive', () => {
  for (const language of ['it', 'en']) {
    const html = retained(null)(language);
    assert.match(html, language === 'it' ? /Caricamento modelli/ : /Loading models/);
    assert.doesNotMatch(html, /0 con FV utilizzabile|0 with usable FV|Nessun modello\.|No models\./);
  }
});

test('the sanity line in the detail uses the same translated name as the table column', () => {
  const model = fixture(); model.sanity_severity = 'WARN'; model.sanity_headline = 'Original sanity headline';
  const render = retained(model);
  // Frasi attese scritte qui: la colonna della tabella si chiama Controllo/Validation.
  const expected = { it: '<span class="uppercase">Controllo</span> WARN: Original sanity headline',
    en: '<span class="uppercase">Validation</span> WARN: Original sanity headline' };
  for (const language of ['it', 'en']) {
    const html = render(language);
    assert.ok(html.includes(expected[language]), `${language}: ${expected[language]}`);
    assert.doesNotMatch(html, /SANITY/);
  }
});

test('the detail header names the valuation engine in the UI language and declares ids without a label', () => {
  const header = (engine, detailEngine, language) => {
    const model = fixture(); model.engine = engine;
    model.detail = { sanity: { severity: 'OK' } };
    if (detailEngine !== undefined) model.detail.engine = detailEngine;
    const html = retained(model)(language);
    const found = /<div class="text-\[#ff8c00\] uppercase text-\[10px\] tracking-\[2px\] border-b border-border pb-1">([^<]*)<\/div>/.exec(html);
    assert.ok(found, 'detail header rendered');
    return found[1];
  };
  const expected = [
    ['operating', 'operating', 'SYNTH.X · motore SOCIETÀ OPERATIVA', 'SYNTH.X · engine OPERATING COMPANY'],
    // id scritto da valuation/dcf_buyside_v3.py: il motore operativo vivo, lo stesso nome
    ['VAL', 'operating_v3', 'SYNTH.X · motore SOCIETÀ OPERATIVA', 'SYNTH.X · engine OPERATING COMPANY'],
    ['bank', 'bank', 'SYNTH.X · motore BANCA', 'SYNTH.X · engine BANK'],
    ['insurance', 'insurance', 'SYNTH.X · motore ASSICURAZIONE', 'SYNTH.X · engine INSURANCE'],
    ['VAL', undefined, 'SYNTH.X · motore VALUTAZIONE', 'SYNTH.X · engine VALUATION'],
    ['DCF', undefined, 'SYNTH.X · motore DCF', 'SYNTH.X · engine DCF'],
    ['n.d.', undefined, 'SYNTH.X · motore n.d.', 'SYNTH.X · engine n/a'],
    [undefined, undefined, 'SYNTH.X · motore n.d.', 'SYNTH.X · engine n/a'],
    // '?' = marcatore dell'API per il file legacy fuori pattern (sidecar senza engine): un buco, non un id
    ['?', undefined, 'SYNTH.X · motore n.d.', 'SYNTH.X · engine n/a'],
    ['?', '', 'SYNTH.X · motore n.d.', 'SYNTH.X · engine n/a'],
    ['VAL', 'etf_passive', 'SYNTH.X · motore etf_passive (motore senza etichetta)', 'SYNTH.X · engine etf_passive (engine without a label)'],
    // parola del backend per il motore non determinato (dcf_engine.py, sector_taxonomy.py): tradotta, non inventata
    ['VAL', 'sconosciuto', 'SYNTH.X · motore SCONOSCIUTO', 'SYNTH.X · engine UNKNOWN'],
    ['VAL', 'unknown', 'SYNTH.X · motore SCONOSCIUTO', 'SYNTH.X · engine UNKNOWN'],
  ];
  for (const [engine, detailEngine, it, en] of expected) {
    assert.equal(header(engine, detailEngine, 'it'), it);
    assert.equal(header(engine, detailEngine, 'en'), en);
  }
});

test('an empty archive points to a regeneration script that exists in the repository', () => {
  const fs = require('node:fs'), path = require('node:path');
  const render = retained(null, { 6: false });
  const expected = { it: 'Nessun modello. La run del consigliere li genera; oppure tools/ops/rigenera_modelli.py.',
    en: 'No models. An adviser run generates them; tools/ops/rigenera_modelli.py can also create them.' };
  for (const language of ['it', 'en']) {
    const html = render(language);
    assert.ok(html.includes(expected[language]), `${language}: ${expected[language]}`);
    assert.doesNotMatch(html, /scripts\/rigenera_modelli/);
    for (const [, script] of html.matchAll(/([\w./-]+\/rigenera_modelli\.py)/g)) {
      assert.ok(fs.existsSync(path.resolve(__dirname, '../../..', script)), `${script} exists`);
    }
  }
});

test('missing IRR notes, cache TTL and SOTP segment count use the unavailable marker of the UI language', () => {
  const bank = fixture(); bank.engine = 'bank';
  bank.detail = { engine: 'bank', holding_irr: { irr: null }, fair_value_sotp: 10, sotp_n_segments: null, sanity: { severity: 'OK' } };
  const operating = fixture(); operating.detail.holding_irr = { by_scenario: {} };
  const vehicle = fixture(); vehicle.engine = 'mnav';
  vehicle.detail = { engine: 'mnav', profile_key: 'dat_bitcoin', mnav_ev: 1.2, nav_vintage: {}, sanity: { severity: 'OK' } };
  const expected = {
    it: { bank: ['IRR periodo 3a</span><span><span class="text-muted">n.d.</span></span>', 'SOTP — n.d. segmenti'],
      operating: ['IRR periodo 3a</span><span><span class="text-muted">n.d.</span></span>'],
      vehicle: ['· prezzi live, cache n.d. min'] },
    en: { bank: ['Holding IRR 3y</span><span><span class="text-muted">n/a</span></span>', 'SOTP — n/a segments'],
      operating: ['Holding IRR 3y</span><span><span class="text-muted">n/a</span></span>'],
      vehicle: ['· live prices, cache n/a min'] },
  };
  for (const [name, model] of Object.entries({ bank, operating, vehicle })) {
    const render = retained(model);
    for (const language of ['it', 'en']) {
      const html = render(language);
      for (const phrase of expected[language][name]) assert.ok(html.includes(phrase), `${language} ${name}: ${phrase}`);
      if (language === 'en') assert.doesNotMatch(html, /n\.d\./, `en ${name}`);
    }
  }
});

test('an archive read failure preserves its original detail without manufacturing zero models', async () => {
  const render = retained(null, {}, { valuationModels: async () => { throw { response: { data: { detail: [{ msg: 'Original archive failure' }] } } }; } });
  for (const language of ['it', 'en']) {
    render(language); await render.effects();
    const html = render(language);
    assert.match(html, /Original archive failure/);
    assert.doesNotMatch(html, /0 con FV utilizzabile|0 with usable FV|Nessun modello\.|No models\./);
  }
});
