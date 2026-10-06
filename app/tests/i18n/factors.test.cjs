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
      n_obs: { portfolio_risk_spy: 200, factor_model_mkt: 300, advanced_metrics_twr: 250 }, min_obs: 60, beta_per_decisioni: true,
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

test('dataset and skipped-security captions handle zero, one and two without changing coverage', async () => {
  for (const datasets of [0, 1, 2]) for (const skipped of [0, 1, 2]) {
    const data = fixtures();
    data.fac.regions = Object.fromEntries(Array.from({ length: datasets }, (_, i) => [`synthetic${i}`, { label: `Synthetic region ${i}`, n_holdings: 1, weight_pct: 40 }]));
    data.fac.ff_data_by_region = Object.fromEntries(Object.keys(data.fac.regions).map(key => [key, { n_obs: 200, last_date: '2026-06-01' }]));
    data.fac.n_holdings_skipped = skipped;
    data.fac.skipped_detail = Array.from({ length: skipped }, (_, i) => ({ ticker: `OMIT.${i}`, weight_pct: 10, reason: 'Original missing history' }));
    const before = structuredClone(data), { render } = retained(data);
    render('it'); await render.effects();
    const it = render('it'), en = render('en');
    if (datasets) {
      assert.match(en, new RegExp(`${datasets} ${datasets === 1 ? 'dataset' : 'datasets'} · ${skipped} ${skipped === 1 ? 'security' : 'securities'} skipped`));
      assert.match(it, new RegExp(`${datasets} dataset · ${skipped} ${skipped === 1 ? 'titolo scartato' : 'titoli scartati'}`));
    } else { assert.doesNotMatch(en, /0 datasets/); assert.doesNotMatch(it, /0 dataset/); }
    assert.deepEqual(data, before);
  }
});

test('Factors updates labels and authored backend variants without repeating its ordered expensive pipeline', async () => {
  const data = fixtures(), before = structuredClone(data), { render, calls } = retained(data);
  render('it'); await render.effects(); const it = render('it'); await render.effects();
  const en = render('en'); await render.effects();
  assert.match(it, /Fattori di rischio/); assert.match(en, /Risk factors/);
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
    assert.match(html, lang === 'it' ? /Ripristino della cache non confermato/ : /Cache restoration not confirmed/);
    assert.match(html, /Original warm failure/);
  }
  assert.equal(calls.filter(x => x === 'riscalda-1y').length, 1);
});

test('an error without detail cannot turn a failed 3Y request into an endless loading state', async () => {
  const { render } = retained(fixtures(), 'ok', { portfolioFactorsPeriodo: async () => { throw { response: { data: { detail: '' } } }; } });
  render('it'); await render.effects();
  for (const lang of ['it', 'en']) {
    const html = render(lang);
    assert.match(html, lang === 'it' ? /Finestra a 3 anni non disponibile/ : /3-year window unavailable/);
    assert.match(html, lang === 'it' ? /Dettaglio dell’errore non fornito/ : /Error detail not provided/);
    assert.doesNotMatch(html, /Finestra a 3 anni in arrivo|3-year window loading/);
  }
});

test('alpha and Sharpe name their sources and a missing spread is declared, not drawn as zero', async () => {
  const data = fixtures(); delete data.adv.sharpe;
  const { render } = retained(data);
  render('it'); await render.effects();
  const expected = {
    it: { missing: 'scarto non calcolabile', spread: /scarto 5,3 pp/, sources: ['Rischio di portafoglio', 'Metriche avanzate', 'Modello fattoriale', 'Benchmark ufficiale'] },
    en: { missing: 'spread cannot be calculated', spread: /spread 5\.3 pp/, sources: ['Portfolio risk', 'Advanced metrics', 'Factor model', 'Official benchmark'] },
  };
  for (const language of ['it', 'en']) {
    const html = render(language); await render.effects();
    assert.ok(html.includes(expected[language].missing), `${language}: ${expected[language].missing}`);
    assert.match(html, expected[language].spread);
    for (const source of expected[language].sources) assert.ok(html.includes(source), `${language}: ${source}`);
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

// Review PR #10: the backend sends three verdicts (advanced_metrics.py reconcile_betas). With fewer
// than two engines it is INSUFFICIENT_SOURCES: nothing was compared, so the page must not say the
// estimates disagree or that the spread exceeds the threshold.
test('INSUFFICIENT_SOURCES is not shown as diverging sources beyond the threshold', async () => {
  const data = fixtures();
  data.rec = { betas: { portfolio_risk_spy: 0.8 }, definitions: { portfolio_risk_spy: 'Original book definition' },
    sources_failed: { factor_model_mkt: 'Original engine failure', advanced_metrics_twr: 'Original twr failure' },
    threshold: 0.25, verdict: 'INSUFFICIENT_SOURCES', note: 'Original insufficient note' };
  const { render } = retained(data);
  render('it'); await render.effects();
  const expected = {
    it: { no: [/Fonti discordanti/, /supera la soglia/], yes: /Fonti insufficienti/ },
    en: { no: [/Sources disagree/, /exceeds the threshold/], yes: /Not enough sources/ },
  };
  for (const lang of ['it', 'en']) {
    const html = render(lang); await render.effects();
    for (const wrong of expected[lang].no) assert.doesNotMatch(html, wrong, `${lang}: ${wrong}`);
    assert.match(html, expected[lang].yes);
    assert.match(html, /Original insufficient note/);
    assert.match(html, /Original engine failure/);
  }
});

test('a beta estimate without a backend definition declares the gap instead of a generic gloss', async () => {
  const data = fixtures(); delete data.rec.definitions.advanced_metrics_twr;
  const { render } = retained(data);
  render('it'); await render.effects();
  for (const lang of ['it', 'en']) {
    const html = render(lang); await render.effects();
    assert.doesNotMatch(html, lang === 'it' ? /Stima dichiarata dal controllo/ : /Estimate declared by the check/);
  }
});

test('an alpha without t-statistic declares the missing interval instead of an empty whisker', async () => {
  const data = fixtures(); delete data.fac.per_holding['SYNTH.X'].alpha_tstat;
  const { render } = retained(data);
  render('it'); await render.effects();
  for (const lang of ['it', 'en']) {
    const html = render(lang); await render.effects();
    const cell = html.match(/<td class="fat-whisk-cell">(.*?)<\/td>/);
    assert.ok(cell, 'alpha whisker cell rendered');
    assert.match(cell[1], /n\.d\.|n\/a/, `${lang}: the missing interval is declared`);
    assert.match(cell[1], /aria-label="[^"]+"/, `${lang}: the reason is reachable`);
  }
});

// Contratto del guardrail beta del 06/10 (advanced_metrics.reconcile_betas): `betas` porta SOLO le
// riconciliate, le fonti sotto min_obs stanno in sources_insufficient, la nota c'e' anche con
// RECONCILED, e con UNRELIABLE arriva un intervallo «non per decisioni». Numeri inventati, interi.
function contratto06() {
  const data = fixtures();
  data.rec = { verdict: 'RECONCILED', betas: { portfolio_risk_spy: 1, factor_model_mkt: 1 },
    definitions: { portfolio_risk_spy: 'Original book definition', factor_model_mkt: 'Original factor definition', advanced_metrics_twr: 'Original twr definition' },
    n_obs: { advanced_metrics_twr: 30, portfolio_risk_spy: 200, factor_model_mkt: 300 }, min_obs: 60,
    sources_insufficient: { advanced_metrics_twr: { beta: 2, n_obs: 30, min_obs: 60, reason: 'Original only 30 common dates' } },
    sources_failed: {}, beta_consensus: 1, beta_per_decisioni: true,
    note: 'Original reconciled 2 of 3 — excluded advanced_metrics_twr (30/60)' };
  return data;
}

test('RECONCILED with a source excluded for insufficient observations shows the exclusion, its reason and the note', async () => {
  const { render } = retained(contratto06());
  render('it'); await render.effects();
  const expected = {
    it: { pill: 'esclusa · 30/60 osservazioni', radio: 'esclusa dal consenso per osservazioni insufficienti', ok: /Riconciliato/ },
    en: { pill: 'excluded · 30/60 observations', radio: 'excluded from the consensus for insufficient observations', ok: /Reconciled/ },
  };
  for (const lang of ['it', 'en']) {
    const html = render(lang); await render.effects();
    const e = expected[lang];
    assert.ok(html.includes(e.pill), `${lang}: excluded pill`);
    assert.ok(html.includes(e.radio), `${lang}: the ruler marker says why it is outside`);
    assert.match(html, /Original only 30 common dates/, `${lang}: backend reason verbatim`);
    assert.match(html, /Original reconciled 2 of 3/, `${lang}: note shown even with RECONCILED`);
    assert.match(html, e.ok);
    assert.doesNotMatch(html, /senza via libera|without clearance/);
    // la nota con esclusioni non e' «liscia»: si segnala come da leggere
    assert.match(html, /fat-note is-warn[^>]*>.*?Original reconciled 2 of 3/);
    // la banda del consenso copre SOLO le riconciliate (1 e 1): l'esclusa a 2 non la allarga
    const band = html.match(/class="fat-band is-ok" style="left:([\d.]+)%;width:([\d.]+)%"/);
    assert.ok(band, `${lang}: consensus band drawn`);
    assert.equal(Number(band[2]), 0, `${lang}: band spans only the reconciled estimates`);
    // il codice grezzo del guardrail non compare mai
    assert.doesNotMatch(html, /RECONCILED_SENZA|NON_DISPONIBILE|NON_CALCOLATO/);
  }
});

test('UNRELIABLE draws the indicative range labelled not-for-decisions, never as a consensus band', async () => {
  const data = contratto06();
  data.rec = { verdict: 'UNRELIABLE', betas: { portfolio_risk_spy: 1, factor_model_mkt: 3, advanced_metrics_twr: 2 },
    n_obs: { portfolio_risk_spy: 200, factor_model_mkt: 300, advanced_metrics_twr: 250 }, min_obs: 60,
    sources_insufficient: {}, sources_failed: {}, threshold: 1, max_spread: 2, beta_per_decisioni: false,
    indicative: { range: [1, 3], median: 2, basis: ['advanced_metrics_twr', 'factor_model_mkt', 'portfolio_risk_spy'], uso: 'non_per_decisioni', note: 'Original descriptive only' },
    note: 'Original diverging note' };
  const { render } = retained(data);
  render('it'); await render.effects();
  const expected = {
    it: { label: 'non per decisioni', band: 'distanza tra le stime riconciliate', median: 'mediana indicativa 2,00' },
    en: { label: 'not for decisions', band: 'spread of the reconciled estimates', median: 'indicative median 2.00' },
  };
  for (const lang of ['it', 'en']) {
    const html = render(lang); await render.effects();
    const e = expected[lang];
    assert.match(html, /class="fat-band is-indicativa" data-uso="non_per_decisioni"/, `${lang}: indicative band`);
    assert.doesNotMatch(html, /class="fat-band( is-ok)?"/, `${lang}: no consensus band`);
    assert.ok(!html.includes(e.band), `${lang}: the key does not call it the reconciled spread`);
    assert.ok(html.split(e.label).length - 1 >= 3, `${lang}: fixed label on band, key and median`);
    assert.ok(html.includes(e.median), `${lang}: median declared as indicative`);
    assert.match(html, /<span class="fat-big num"><span class="fat-muted">n[./][da]\.?<\/span>/, `${lang}: no consensus value, declared n.d.`);
  }
});

test('beta_guardrail codes become sentences in both languages; unknown and missing codes are declared n.d.', () => {
  const load = creaCaricatore(), language = load('i18n/lingua.ts');
  const { fraseGuardrailBeta } = load('pages/fattori/VistaBeta.tsx'), { parole } = load('pages/fattori/parole.ts');
  const codes = ['RECONCILED', 'UNRELIABLE', 'INSUFFICIENT_SOURCES', 'NON_CALCOLATO', 'NON_DISPONIBILE', 'RECONCILED_SENZA_VIA_LIBERA', 'RECONCILED_SENZA_FONTE_RISCHIO'];
  for (const lang of ['it', 'en']) {
    language.impostaLinguaCorrente(lang);
    const w = parole(), seen = new Set();
    for (const code of codes) {
      const sentence = fraseGuardrailBeta(code, w);
      assert.ok(!sentence.includes(code), `${lang}: ${code} is never shown raw`);
      assert.match(sentence, lang === 'it' ? /^Punteggio quantitativo: / : /^Quant score: /);
      seen.add(sentence);
    }
    assert.equal(seen.size, codes.length, `${lang}: one distinct sentence per code`);
    assert.match(fraseGuardrailBeta('NEW_VERDICT', w), lang === 'it' ? /guardrail n\.d\. \(codice NEW_VERDICT non riconosciuto\)/ : /guardrail n\/a \(unrecognised code NEW_VERDICT\)/);
    for (const missing of [null, undefined, '']) assert.match(fraseGuardrailBeta(missing, w), lang === 'it' ? /guardrail n\.d\./ : /guardrail n\/a/);
  }
});

test('RECONCILED without the risk-beta source declares its undeclared observations as n.d., never a raw code', async () => {
  const data = contratto06();
  data.rec.betas = { factor_model_mkt: 1, advanced_metrics_twr: 1 };
  data.rec.sources_insufficient = { portfolio_risk_spy: { beta: 2, n_obs: null, min_obs: 60, reason: 'Original observations not declared' } };
  const { render } = retained(data);
  render('it'); await render.effects();
  for (const lang of ['it', 'en']) {
    const html = render(lang); await render.effects();
    assert.ok(html.includes(lang === 'it' ? 'esclusa · n.d./60 osservazioni' : 'excluded · n/a/60 observations'), `${lang}: undeclared observations are n.d., not zero`);
    assert.doesNotMatch(html, /RECONCILED_SENZA_FONTE_RISCHIO/);
  }
});

test('a region without weight and a missing holdings count are declared n.d., not drawn as zero', async () => {
  const data = fixtures();
  delete data.fac.regions.synthetic.weight_pct;
  delete data.fac.n_holdings_analyzed;
  const { render } = retained(data);
  render('it'); await render.effects();
  for (const lang of ['it', 'en']) {
    const html = render(lang); await render.effects();
    const na = lang === 'it' ? 'n.d.' : 'n/a';
    assert.match(html, /class="fat-wbar is-empty"/, `${lang}: empty bar, declared`);
    assert.doesNotMatch(html, /fat-wbar"><i style="width:0\.00%"/, `${lang}: no zero-width bar presented as measured`);
    assert.ok(html.includes(lang === 'it' ? 'Peso della regione non fornito dal backend' : 'Region weight not provided by the backend'));
    assert.ok(html.includes(lang === 'it' ? 'titoli analizzati n.d.' : 'securities analysed n/a'), `${lang}: holdings chip declares n.d.`);
    assert.doesNotMatch(html, /— (titoli|securities)/, `${lang}: no em-dash posing as a count`);
    assert.ok(html.includes(na));
  }
});

// Revisione del 06/10 sul guardrail beta: osservazioni non verificabili, nomi delle fonti negli
// avvisi, esito sconosciuto senza banda e suggerimenti tradotti. Numeri inventati, interi.
test('an excluded source with undeclared observations says they are not verifiable, not insufficient', async () => {
  const data = contratto06();
  data.rec.betas = { factor_model_mkt: 1, advanced_metrics_twr: 1 };
  data.rec.sources_insufficient = { portfolio_risk_spy: { beta: 2, n_obs: null, min_obs: 60, reason: 'Original observations not declared' } };
  const { render } = retained(data);
  render('it'); await render.effects();
  const expected = {
    it: { yes: 'esclusa dal consenso: osservazioni non verificabili', no: 'esclusa dal consenso per osservazioni insufficienti' },
    en: { yes: 'excluded from the consensus: observations not verifiable', no: 'excluded from the consensus for insufficient observations' },
  };
  for (const lang of ['it', 'en']) {
    const html = render(lang); await render.effects();
    const marker = html.match(/role="radio"[^>]*aria-label="([^"]*Original book definition[^"]*)"/);
    assert.ok(marker, `${lang}: excluded marker rendered`);
    assert.ok(marker[1].includes(expected[lang].yes), `${lang}: marker says the observations are not verifiable`);
    assert.ok(!html.includes(expected[lang].no), `${lang}: never called insufficient without a count`);
  }
  // con n_obs dichiarato resta «insufficienti»
  const declared = retained(contratto06());
  declared.render('it'); await declared.render.effects();
  const html = declared.render('it'); await declared.render.effects();
  assert.ok(html.includes(expected.it.no) && !html.includes(expected.it.yes), 'declared count keeps the insufficient wording');
});

test('excluded and failed source warnings use the source names, never the technical keys', async () => {
  const data = contratto06();
  data.rec.sources_insufficient = { advanced_metrics_twr: { beta: null, n_obs: 30, min_obs: 60, reason: 'Original only 30 common dates' },
    new_engine: { beta: null, n_obs: 10, min_obs: 60, reason: 'Original new engine reason' } };
  data.rec.sources_failed = { portfolio_risk_spy: 'Original risk failure' };
  data.rec.betas = { factor_model_mkt: 1, advanced_metrics_twr: 1 };
  const { render } = retained(data);
  render('it'); await render.effects();
  const expected = {
    it: { twr: 'TWR contro benchmark (Original only 30 common dates)', unknown: 'fonte non riconosciuta (new_engine) (Original new engine reason)', spy: /Fonti non interrogate: [^<]*\(Original risk failure\)/ },
    en: { twr: 'TWR vs benchmark (Original only 30 common dates)', unknown: 'unrecognised source (new_engine) (Original new engine reason)', spy: /Sources not queried: [^<]*\(Original risk failure\)/ },
  };
  for (const lang of ['it', 'en']) {
    const html = render(lang); await render.effects();
    const notes = (html.match(/<div class="fat-note is-warn"><span class="txt">[^<]*<\/span><\/div>/g) || []).join('\n');
    assert.ok(notes.includes(expected[lang].twr), `${lang}: excluded source named`);
    assert.ok(notes.includes(expected[lang].unknown), `${lang}: unknown source declared, key only in parentheses`);
    assert.match(notes, expected[lang].spy, `${lang}: failed source warning present`);
    assert.doesNotMatch(notes, /advanced_metrics_twr|portfolio_risk_spy/, `${lang}: no technical key in the warnings`);
  }
});

test('an unknown verdict draws no consensus band and is declared not recognised, with a translated tooltip', async () => {
  const data = contratto06();
  data.rec = { verdict: 'NEW_VERDICT', betas: { portfolio_risk_spy: 1, factor_model_mkt: 2 },
    n_obs: { portfolio_risk_spy: 200, factor_model_mkt: 300 }, min_obs: 60, threshold: 1, max_spread: 1, beta_consensus: 1 };
  const { render } = retained(data);
  render('it'); await render.effects();
  const expected = {
    it: { pill: 'Esito n.d.', title: 'Esito del controllo: esito n.d. (codice NEW_VERDICT non riconosciuto)', text: /Esito del controllo non riconosciuto \(NEW_VERDICT\)/, band: 'distanza tra le stime riconciliate' },
    en: { pill: 'Result n/a', title: 'Check result: result n/a (unrecognised code NEW_VERDICT)', text: /Unrecognised check result \(NEW_VERDICT\)/, band: 'spread of the reconciled estimates' },
  };
  for (const lang of ['it', 'en']) {
    const html = render(lang); await render.effects();
    const e = expected[lang];
    assert.doesNotMatch(html, /class="fat-band/, `${lang}: no band at all`);
    assert.ok(!html.includes(e.band), `${lang}: no consensus band in the key`);
    const pill = html.match(/<span class="fat-pill ([^"]*)" title="([^"]*)" data-verdetto="NEW_VERDICT"><i class="fat-dot"><\/i>([^<]*)<\/span>/);
    assert.ok(pill, `${lang}: verdict pill rendered`);
    assert.equal(pill[1], 'is-warn', `${lang}: never red as diverging`);
    assert.equal(pill[2], e.title, `${lang}: tooltip translated, code only in parentheses`);
    assert.equal(pill[3], e.pill, `${lang}: pill declares n.d.`);
    assert.match(html, e.text, `${lang}: the verdict is declared not recognised`);
  }
});

test('known verdicts get translated tooltips, never the raw code', async () => {
  const cases = [
    [contratto06(), { it: 'Esito del controllo: Riconciliato', en: 'Check result: Reconciled' }],
    [contratto06(), { it: 'Esito del controllo: Fonti discordanti', en: 'Check result: Sources disagree' }, 'UNRELIABLE'],
    [contratto06(), { it: 'Esito del controllo: Fonti insufficienti', en: 'Check result: Not enough sources' }, 'INSUFFICIENT_SOURCES'],
  ];
  for (const [data, titles, verdict] of cases) {
    if (verdict) { data.rec.verdict = verdict; data.rec.beta_per_decisioni = false; }
    const { render } = retained(data);
    render('it'); await render.effects();
    for (const lang of ['it', 'en']) {
      const html = render(lang); await render.effects();
      const title = html.match(/title="([^"]*)" data-verdetto=/);
      assert.ok(title, `${lang}: verdict pill rendered`);
      assert.equal(title[1], titles[lang], `${lang}: ${verdict || 'RECONCILED'} tooltip translated`);
      assert.doesNotMatch(title[1], /RECONCILED|UNRELIABLE|INSUFFICIENT_SOURCES/);
    }
  }
});

test('the not-for-decisions label sits in its own row above the ruler, not over the markers', async () => {
  const data = contratto06();
  data.rec = { verdict: 'UNRELIABLE', betas: { portfolio_risk_spy: 1, factor_model_mkt: 3 },
    n_obs: { portfolio_risk_spy: 200, factor_model_mkt: 300 }, min_obs: 60, threshold: 1, max_spread: 2, beta_per_decisioni: false,
    indicative: { range: [1, 3], median: 2 } };
  const { render } = retained(data);
  render('it'); await render.effects();
  const html = render('it'); await render.effects();
  const row = html.indexOf('class="fat-band-row"'), ruler = html.indexOf('class="calibro fat-ruler"');
  assert.ok(row > 0 && row < ruler, 'label row comes before the ruler');
  assert.match(html, /class="fat-band-row"[^>]*><span class="fat-band-lbl" style="left:[\d.]+%">non per decisioni<\/span><\/div>/);
  assert.match(html, /class="fat-band is-indicativa"[^>]*><\/span>/, 'the band itself carries no label');
});
