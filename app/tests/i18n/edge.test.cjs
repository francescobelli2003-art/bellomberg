const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');
ambienteBrowser();
const idle = { inCorso: false, forzata: false, errore: null };
function fixture() {
  return { signals: [{ ticker: 'SYNTH.X', category: 'volatility', name: 'Nome dichiarato', reading: 'Lettura dichiarata', value: 1234.567, strength: 77, direction: 'bullish', source: 'synthetic', context: 'Original unmarked context' }],
    n_signals_total: 1, n_signals_strong: 1, by_category: { volatility: 1 }, generated: '2026-09-12T10:00:00',
    copertura: { posizioni_totali: 1, posizioni_scansionate: 1, scansione_piena: ['SYNTH.X'], scansione_degradata: {}, solo_prezzo: [], nessuna_misura: {}, non_scansionate: [], fattoriali_su: 1, fattoriali_ko: null, nota: 'Nota dichiarata' },
    cache: { attiva: true, servita_da_cache: true, ttl_s: 3600, eta_s: 120, scansione_delle: '2026-09-12T10:00:00' },
    _presentation_v1: { version: 1, texts: [
      { path: ['signals', 0, 'name'], it: 'Nome dichiarato', en: 'Declared name' },
      { path: ['signals', 0, 'reading'], it: 'Lettura dichiarata', en: 'Declared reading' },
      { path: ['copertura', 'nota'], it: 'Nota dichiarata', en: 'Declared note' },
    ] },
  };
}
function retained(data = fixture(), failure = null) {
  let state = 0, memoIndex = 0, effectIndex = 0, refIndex = 0;
  const values = {}, memos = [], refs = [], effects = [], effectDeps = [], calls = [], timers = [];
  const memo = (compute, deps) => { const at = memoIndex++, before = memos[at];
    if (!before || !deps || deps.some((v, i) => !Object.is(v, before.deps[i]))) memos[at] = { deps, value: compute() };
    return memos[at].value; };
  const load = creaCaricatore({ stub: {
    react: { ...React, useEffect(fn, deps) { const at = effectIndex++, before = effectDeps[at];
      if (!before || !deps || deps.some((v, i) => !Object.is(v, before[i]))) effects.push(fn); effectDeps[at] = deps; },
      useMemo: memo, useCallback: (fn, deps) => memo(() => fn, deps),
      useRef(initial) { const at = refIndex++; return refs[at] ||= { current: initial }; },
      useState(initial) { const at = state++; if (!(at in values)) values[at] = typeof initial === 'function' ? initial() : initial;
        return [values[at], value => { values[at] = typeof value === 'function' ? value(values[at]) : value; }]; } },
    '@/lib/api': { API_BASE: 'http://synthetic.invalid', requestHeaders: () => ({ 'X-BB-Language': load('i18n/lingua.ts').linguaCorrente(), 'X-Synthetic-Test': 'yes' }) },
    axios: { get: async (url, options) => { calls.push({ url, options }); if (failure) throw failure; return { data }; } },
  } });
  const language = load('i18n/lingua.ts'), Page = load('pages/EdgeScannerPage.tsx').default;
  const render = selected => { state = memoIndex = effectIndex = refIndex = 0; effects.length = 0; language.impostaLinguaCorrente(selected); return renderToStaticMarkup(React.createElement(Page)); };
  render.effects = async () => {
    const original = globalThis.setInterval;
    globalThis.setInterval = (fn, ms) => { timers.push({ fn, ms }); return 123; };
    try { for (const fn of effects) fn(); } finally { globalThis.setInterval = original; }
    await new Promise(resolve => setImmediate(resolve));
  };
  return { render, calls, load };
}

test('Edge updates authored variants and labels locally, preserving strength, source text and request semantics', async () => {
  const data = fixture(), before = structuredClone(data), { render, calls } = retained(data);
  render('en'); await render.effects(); const en = render('en'); await render.effects();
  const it = render('it'); await render.effects();
  assert.match(en, /Strength threshold/); assert.match(it, /Soglia forza/);
  assert.match(en, /Declared reading/); assert.match(en, /Declared note/); assert.match(it, /Lettura dichiarata/);
  assert.match(en, /1,234\.567/); assert.match(it, /1\.234,567/);
  for (const html of [it, en]) { assert.match(html, /Original unmarked context/); assert.match(html, /SYNTH.X/); assert.match(html, /#b07a1e/i); }
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].options, { params: { min_strength: 45 }, timeout: 420000, headers: { 'X-BB-Language': 'en', 'X-Synthetic-Test': 'yes' } });
  assert.deepEqual(data, before);
});

test('the positioning category is named in the UI language in filters, summary and signal tags', async () => {
  const data = fixture();
  data.signals.push({ ...data.signals[0], ticker: 'SYNTH.Y', category: 'positioning' });
  data.n_signals_total = 2; data.n_signals_strong = 2; data.by_category = { volatility: 1, positioning: 1 };
  const { render } = retained(data);
  render('it'); await render.effects();
  // Etichette attese scritte qui, non lette dal catalogo.
  const expected = { it: 'Posizionamento', en: 'Positioning' };
  for (const language of ['it', 'en']) {
    const html = render(language);
    const label = expected[language];
    assert.ok(html.includes(`>${label}</button>`), `${language} filter`);
    assert.ok(html.includes(`>${label}: <span class="text-white">1</span>`), `${language} summary`);
    assert.match(html, new RegExp(`rounded-sm">${label}</span>`), `${language} signal tag`);
    if (language === 'it') assert.doesNotMatch(html, /Positioning/);
  }
});

test('timeout diagnosis follows the UI language and never turns into a measured zero', async () => {
  const { render, calls } = retained(null, { code: 'ECONNABORTED', message: 'timeout of 420000ms exceeded' });
  render('it'); await render.effects();
  const it = render('it'), en = render('en');
  assert.match(it, /Il backend continua/); assert.match(en, /The backend continues/);
  for (const html of [it, en]) { assert.match(html, /data-origine="timeout"/); assert.doesNotMatch(html, /data-vuoto="misurato"/); }
  assert.equal(calls.length, 1);
});

test('zero coverage classifications and scan age are equivalent in both languages', () => {
  const load = creaCaricatore(), language = load('i18n/lingua.ts'), edge = load('lib/edge.ts');
  const results = [];
  for (const lang of ['it', 'en']) {
    language.impostaLinguaCorrente(lang);
    const source = fixture(); source.signals = []; source.n_signals_total = 0; source.n_signals_strong = 0;
    const full = edge.leggiScan(source, idle); const measured = edge.vuotoScan(full, 45, '', 0);
    assert.match(measured.testo, lang === 'it' ? /ZERO MISURATO/ : /MEASURED ZERO/);
    source.copertura.fattoriali_su = null; source.copertura.fattoriali_ko = 'Original factor failure';
    const partial = edge.vuotoScan(edge.leggiScan(source, idle), 45, '', 0);
    assert.match(partial.testo, /Original factor failure/);
    delete source.copertura;
    const unknown = edge.vuotoScan(edge.leggiScan(source, idle), 45, '', 0);
    const age = edge.etaScan(full.eta, 1000, 11000);
    source.cache.servita_da_cache = false;
    const fresh = edge.leggiScan(source, idle);
    assert.equal(edge.etaScan(fresh.eta, 1000, 11000).tono, 'fresca');
    assert.equal(edge.etaScan(fresh.eta, 1000, 3601001).tono, 'scaduta');
    results.push({ measured: measured.tono, partial: partial.tono, unknown: unknown.tono, seconds: age.secondi, expired: age.scaduta });
  }
  assert.deepEqual(results[0], results[1]);
  assert.deepEqual(results[0], { measured: 'misurato', partial: 'parziale', unknown: 'nd', seconds: 130, expired: false });
});
