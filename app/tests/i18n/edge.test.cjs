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
    '@/lib/loghi-remoti': { caricaLoghi: async () => {}, useLogoRemoto: () => ({}) },
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
  assert.match(en, /Minimum strength/); assert.match(it, /Forza minima/);
  assert.match(en, /Declared reading/); assert.match(en, /Declared note/); assert.match(it, /Lettura dichiarata/);
  assert.match(en, /1,234\.567/); assert.match(it, /1\.234,567/);
  for (const html of [it, en]) { assert.match(html, /Original unmarked context/); assert.match(html, /SYNTH.X/); assert.match(html, /<b>77<\/b>/); }
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
    assert.match(html, new RegExp(`data-cat="positioning"[^>]*>${label}(<!-- -->)? <b>1</b></button>`), `${language} filter`);
    assert.ok(html.includes(`<span class="ro-lane-l">${label}<small>`), `${language} strength map lane`);
    assert.ok(html.includes(`ro-cat is-positioning">${label}</span>`), `${language} signal tag`);
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


test('the scanner footer describes supplied data without asserting a completed backtest or real account', async () => {
  const { render } = retained();
  render('it'); await render.effects();
  const it = render('it'), en = render('en');
  assert.match(it, /Segnali calcolati sui dati forniti dagli strumenti/);
  assert.match(en, /Signals calculated from data supplied by the tools/);
  for (const html of [it, en]) assert.doesNotMatch(html, /#148|actual market data|dati di mercato reali|Formal validation through backtesting|Validazione formale via backtest/);
});

// ── review della PR #13 (Opus 5.5): la vista Nuova non trasforma un buco in una misura ──

function vista(lang, { diagnosi = null, data = fixture(), copertura = false, selPersa = false } = {}) {
  const load = creaCaricatore({ stub: { '@/lib/loghi-remoti': { caricaLoghi: async () => {}, useLogoRemoto: () => ({}) } } });
  const language = load('i18n/lingua.ts'), edge = load('lib/edge.ts');
  language.impostaLinguaCorrente(lang);
  const Vista = load('pages/ricerca/VistaRicerca.tsx').default;
  const esito = edge.leggiScan(data, idle);
  const tutti = esito.segnali.map((s, i) => ({ s, k: `k${i}` }));
  const eta = edge.etaScan(esito.eta, 1000, 11000);
  const d = { esito, viva: esito, errore: null, loading: false, forzata: false, attesaS: 0, timeoutMs: 420000,
    minStrength: 45, sogliaResa: 45, cat: '', tutti, righe: tutti, sel: tutti[0] || null, selPersa, vuoto: null, eta,
    oraScan: '10:00', copertura, diagnosi };
  const noop = () => {};
  const a = { soglia: noop, categoria: noop, scegli: noop, rifai: noop, riprova: noop, copertura: noop, diagnosi: noop, apriMercati: noop };
  return { html: renderToStaticMarkup(React.createElement(Vista, { d, a })), load };
}

test('a position check with zero signals is declared as not a measurement, never drawn as a HOLD gauge', () => {
  for (const lang of ['it', 'en']) {
    const diagnosi = { ticker: 'SYNTH.X', stato: 'ok', score: 0, verdetto: 'HOLD — synthetic', nota: '', nSegnali: 0, alle: '10:00' };
    const { html } = vista(lang, { diagnosi });
    assert.match(html, /data-avviso="diagnosi-zero"/, `${lang}: zero-signal warning`);
    assert.doesNotMatch(html, /ro-gauge"/, `${lang}: no gauge on a zero-signal check`);
    const undeclared = vista(lang, { diagnosi: { ...diagnosi, nSegnali: null } }).html;
    assert.match(undeclared, /data-avviso="diagnosi-zero"/, `${lang}: undeclared count is not a measurement either`);
    const measured = vista(lang, { diagnosi: { ...diagnosi, score: 1.2, nSegnali: 2 } }).html;
    assert.match(measured, /ro-gauge"/); assert.doesNotMatch(measured, /data-avviso="diagnosi-zero"/);
  }
});

test('the position check names its scope: per-ticker detectors only, factors excluded', () => {
  const it = vista('it').html, en = vista('en').html;
  assert.match(it, /fattoriali esclusi/); assert.match(en, /factors excluded/);
});

test('position-check payloads: a backend error is reported verbatim and a missing verdict is declared n/a', () => {
  const load = creaCaricatore(), language = load('i18n/lingua.ts'), calcoli = load('pages/ricerca/calcoli.ts');
  for (const lang of ['it', 'en']) {
    language.impostaLinguaCorrente(lang);
    const ko = calcoli.leggiDiagnosi({ error: 'Synthetic provider failure' }, 'SYNTH.X');
    assert.equal(ko.stato, 'errore'); assert.match(ko.motivo, /Synthetic provider failure/);
    const shape = calcoli.leggiDiagnosi({ verdict: 'HOLD' }, 'SYNTH.X');
    assert.equal(shape.stato, 'errore'); assert.match(shape.motivo, /net_score/);
    const noVerdict = calcoli.leggiDiagnosi({ net_score: 0.5, n_signals: 1 }, 'SYNTH.X');
    assert.equal(noVerdict.stato, 'ok'); assert.match(noVerdict.verdetto, lang === 'it' ? /n\.d\./ : /n\/a/);
    const nan = calcoli.leggiDiagnosi({ net_score: 1, verdict: 'X', n_signals: 'tre' }, 'SYNTH.X');
    assert.equal(nan.nSegnali, null);
  }
});

test('an undeclared cache TTL is said in the visible register, not only in a tooltip', async () => {
  const data = fixture(); delete data.cache.ttl_s;
  const { render } = retained(data);
  render('it'); await render.effects();
  for (const [lang, rx] of [['it', /TTL non dichiarato/], ['en', /TTL not declared/]]) {
    const html = render(lang);
    const chip = html.match(/<span class="ro-chip-t">([\s\S]*?)<\/span><\/span>/);
    assert.ok(chip, `${lang}: register chip`);
    assert.match(chip[1], rx, `${lang}: visible TTL declaration`);
  }
});

test('the degraded-coverage bucket does not claim exactly one silent detector', () => {
  const data = fixture();
  data.copertura.scansione_piena = []; data.copertura.scansione_degradata = { 'SYNTH.X': 'vol risk premium down; dealer gamma down' };
  const it = vista('it', { data }).html, en = vista('en', { data }).html;
  assert.doesNotMatch(it, /Un rilevatore muto/); assert.doesNotMatch(en, /One silent detector/);
  assert.match(it, /Almeno un rilevatore muto/); assert.match(en, /At least one silent detector/);
});

// ── review: la chiave di un segnale nasce dal contenuto, non dalla posizione in lista ──

test('a signal keeps its key across rescans that reorder, insert or drop other signals', () => {
  const calcoli = creaCaricatore()('pages/ricerca/calcoli.ts');
  const base = fixture().signals[0];
  const sig = (ticker, category, value, strength, direction = 'bullish') => ({ ...base, ticker, category, value, strength, direction });
  const vrp = sig('SYNTH.X', 'volatility', '+3.0pt', 80);
  const zs = sig('SYNTH.Y', 'momentum', '+2.0σ', 70);
  const beta = sig('SYNTH.X', 'factor', 'β 1.80', 60);
  const prima = [vrp, zs, beta];
  const k = s => calcoli.chiaviSegnali(prima)[prima.indexOf(s)];
  // nuova scansione: entra un segnale in testa, l'ordine per forza cambia, uno esce
  const em = sig('SYNTH.Z', 'volatility', '±4.0%', 90);
  const dopo = [em, { ...beta, strength: 85 }, { ...vrp, strength: 50 }];
  const kd = calcoli.chiaviSegnali(dopo);
  assert.equal(kd[2], k(vrp), 'same VRP signal, same key after moving from first to last');
  assert.equal(kd[1], k(beta), 'same beta signal, same key with a different strength');
  assert.ok(!kd.includes(k(zs)), 'a dropped signal leaves no key behind for another one to inherit');
  assert.equal(new Set(kd).size, kd.length);
  // il nome arriva tradotto: cambiare lingua non cambia la chiave
  assert.deepEqual(calcoli.chiaviSegnali([{ ...vrp, name: 'Vol Risk Premium' }]), calcoli.chiaviSegnali([{ ...vrp, name: 'Premio volatilità' }]));
  // rilevatore non riconosciuto: i gemelli si distinguono col contatore, gli altri non si spostano
  const risk1 = sig('SYNTH.W', 'risk', 'x', 40), risk2 = sig('SYNTH.W', 'risk', 'y', 30);
  const kr = calcoli.chiaviSegnali([risk1, vrp, risk2]);
  assert.equal(new Set(kr).size, 3);
  assert.equal(kr[1], k(vrp));
  assert.equal(calcoli.chiaviSegnali([em, risk1, vrp, risk2])[2], k(vrp));
});

test('a selection missing from the latest scan is declared, not silently swapped', () => {
  for (const [lang, rx] of [['it', /Il segnale scelto non è nell&#x27;ultima scansione/], ['en', /The selected signal is not in the latest scan/]]) {
    const lost = vista(lang, { selPersa: true }).html;
    assert.match(lost, /data-avviso="scelta-persa"/, `${lang}: declared fallback`);
    assert.match(lost, rx, `${lang}: fallback text`);
    assert.doesNotMatch(vista(lang).html, /data-avviso="scelta-persa"/, `${lang}: no warning when the selection exists`);
  }
});
