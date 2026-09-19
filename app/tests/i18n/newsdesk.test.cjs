const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');

ambienteBrowser();
globalThis.sessionStorage = { getItem: () => null, removeItem() {} };
const source = fs.readFileSync(path.resolve(__dirname, '../../src/pages/NewsPage.tsx'), 'utf8');
const tree = ts.createSourceFile('NewsPage.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const page = tree.statements.find(n => ts.isFunctionDeclaration(n) && n.name?.text === 'NewsPage');
const stateNames = [];
function states(node) {
  if (ts.isVariableDeclaration(node) && ts.isArrayBindingPattern(node.name) && node.initializer && ts.isCallExpression(node.initializer) && node.initializer.expression.getText(tree) === 'useState') stateNames.push(node.name.elements[0].name.text);
  ts.forEachChild(node, states);
}
states(page);

function retainedPage(seed = {}, responses = {}) {
  let index = 0, memoIndex = 0, refIndex = 0, effectIndex = 0, selectedLanguage = 'it';
  const values = {}, memos = [], refs = [], effectDeps = [], effects = [], requests = [];
  for (const [name, value] of Object.entries(seed)) { assert.ok(stateNames.includes(name), name); values[stateNames.indexOf(name)] = value; }
  const memo = (compute, deps) => {
    const at = memoIndex++, old = memos[at];
    if (!old || !deps || deps.some((value, i) => !Object.is(value, old.deps[i]))) memos[at] = { deps, value: compute() };
    return memos[at].value;
  };
  const api = new Proxy({}, { get: (_target, name) => async (...args) => {
    requests.push({ name, args, language: selectedLanguage });
    if (name in responses) return responses[name](...args, selectedLanguage);
    return {};
  } });
  const load = creaCaricatore({ stub: {
    react: { ...React, useMemo: memo, useCallback: (fn, deps) => memo(() => fn, deps), useLayoutEffect() {},
      useState(initial) { const at = index++; if (!(at in values)) values[at] = typeof initial === 'function' ? initial() : initial;
        return [values[at], value => { values[at] = typeof value === 'function' ? value(values[at]) : value; }]; },
      useRef(current) { return refs[refIndex++] ||= { current }; },
      useEffect(effect, deps) { const at = effectIndex++, before = effectDeps[at];
        if (!before || !deps || deps.some((value, i) => !Object.is(value, before[i]))) effects.push(effect);
        effectDeps[at] = deps; },
    },
    '@/lib/api': { Bellomberg: api },
    'react-markdown': props => React.createElement('div', {}, props.children), 'remark-gfm': () => {},
    '../lib/svg-kit': { useScalaTesto: () => 1 },
  } });
  const language = load('i18n/lingua.ts'), Component = load('pages/NewsPage.tsx').default;
  return {
    requests,
    set(name, value) { assert.ok(stateNames.includes(name)); values[stateNames.indexOf(name)] = value; },
    render(selected) { index = memoIndex = refIndex = effectIndex = 0; effects.length = 0;
      selectedLanguage = selected;
      language.impostaLinguaCorrente(selected); return renderToStaticMarkup(React.createElement(Component)); },
    async effects() { const timer = globalThis.setInterval; globalThis.setInterval = () => 0;
      try { for (const effect of effects) effect(); await new Promise(resolve => setImmediate(resolve)); }
      finally { globalThis.setInterval = timer; } },
  };
}

test('news labels and retained theme memo follow IT/EN without changing original feed or filters', () => {
  const feed = [{ id: 1, title: 'Original source headline', snippet: 'Original source prose', theme: 'ukraine', ticker_mentioned: 'SYNTH.X', relevance: 9, _materiality: 12.5, published_at: new Date().toISOString() }];
  const view = retainedPage({ feed, fThemes: ['ukraine'], fTickers: ['SYNTH.X'] });
  const it = view.render('it'), en = view.render('en');
  assert.match(it, /FILTRI/); assert.match(en, /FILTERS/);
  assert.match(it, /UCRAINA/); assert.match(en, /UKRAINE/);
  for (const html of [it, en]) { assert.match(html, /Original source headline/); assert.match(html, /Original source prose/); assert.match(html, /SYNTH.X/); }
});

test('active filter count renders zero, one and two in both languages without changing the filters', () => {
  for (const count of [0, 1, 2]) {
    const seed = { fPeriod: 'all', fThemes: count > 0 ? ['portfolio'] : [], fTickers: count > 1 ? ['SYNTH.X'] : [] };
    const before = structuredClone(seed), view = retainedPage(seed);
    assert.match(view.render('it'), new RegExp(`${count} ${count === 1 ? 'filtro attivo' : 'filtri attivi'}`));
    assert.match(view.render('en'), new RegExp(`${count} active ${count === 1 ? 'filter(?!s)' : 'filters'}`));
    assert.deepEqual(seed, before);
  }
});

test('stored summary and briefing keep their original language and declare missing English summaries', () => {
  const feed = [{ id: 2, title: 'Titolo sintetico italiano conservato', snippet: 'Sintesi storica conservata', summary_status: 'available', summary_language: 'it' },
    { id: 3, title: 'Original provider headline', summary_status: 'unavailable', summary_language: null, summary_note: 'Original backend note: no English summary' }];
  const wire = retainedPage({ feed }).render('en');
  assert.match(wire, /Original summary · Italian/);
  assert.match(wire, /Original backend note: no English summary/);
  assert.match(wire, /Titolo sintetico italiano conservato/);
  assert.match(wire, /Original source/);
  const desk = retainedPage({ view: 'desk', briefing: { briefing_md: 'Testo briefing storico invariato', language: 'it', generated_at: '2026-09-12T08:00:00' } }).render('en');
  assert.match(desk, /Original briefing · Italian/);
  assert.match(desk, /Testo briefing storico invariato/);
});

test('favorites and scheduler errors remain explicit; language changes reread only local feed and briefing caches', async () => {
  const view = retainedPage({}, {
    favorites: async () => { throw new Error('Original favorites failure'); },
    scheduledTasks: async () => { throw new Error('Original scheduler failure'); },
  });
  view.render('it'); await view.effects();
  const before = view.requests.map(x => x.name);
  const it = view.render('it'), en = view.render('en'); await view.effects();
  for (const html of [it, en]) { assert.match(html, /Original favorites failure/); assert.match(html, /Original scheduler failure/); }
  assert.match(it, /Preferiti non disponibili/); assert.match(en, /Favorites unavailable/);
  assert.deepEqual(view.requests.map(x => x.name), [...before, 'newsFeed', 'briefingCurrent']);
  assert.deepEqual(view.requests.slice(-2).map(x => x.language), ['en', 'en']);
  assert.equal(view.requests.filter(x => /Refresh$/.test(x.name)).length, 0);
});

test('late Italian cache responses cannot overwrite the selected English versions', async () => {
  let releaseFeed, releaseBriefing;
  const view = retainedPage({}, {
    newsFeed: (_args, language) => language === 'it' ? new Promise(resolve => { releaseFeed = resolve; }) : { items: [{ title: 'English cached headline', summary_status: 'available', summary_language: 'en' }] },
    briefingCurrent: language => language === 'it' ? new Promise(resolve => { releaseBriefing = resolve; }) : { briefing_md: 'English cached briefing', language: 'en', generated_at: '2026-09-12T08:00:00' },
  });
  view.render('it'); await view.effects();
  view.render('en'); await view.effects();
  releaseFeed({ items: [{ title: 'Late Italian headline', summary_language: 'it' }] });
  releaseBriefing({ briefing_md: 'Late Italian briefing', language: 'it' });
  await new Promise(resolve => setImmediate(resolve));
  const wire = view.render('en');
  assert.match(wire, /English cached headline/); assert.doesNotMatch(wire, /Late Italian headline/);
  view.set('view', 'desk');
  const desk = view.render('en');
  assert.match(desk, /English cached briefing/); assert.doesNotMatch(desk, /Late Italian briefing/);
  assert.equal(view.requests.filter(x => /Refresh$/.test(x.name)).length, 0);
});

test('a loading calendar is not an empty calendar, and missing briefing age is not never generated', () => {
  for (const language of ['it', 'en']) {
    const html = retainedPage({ view: 'desk', econLoad: true, briefing: { briefing_md: 'Original briefing', generated_at: '2026-09-12T08:00:00', language: 'it' } }).render(language);
    assert.match(html, language === 'it' ? /Caricamento calendario/ : /Loading calendar/);
    assert.match(html, language === 'it' ? /età non disponibile/ : /age unavailable/);
  }
});

test('summary metadata and generated SEC text are not mislabelled as original provider prose', () => {
  const wire = retainedPage({ feed: [{ title: 'Original legacy text with unknown provenance' }] }).render('en');
  assert.match(wire, /Received content · origin and language unknown/);
  const desk = retainedPage({ view: 'desk', rawCorp: { items: [{ title: 'SYNTH: 5 azioni', source_type: 'sec', snippet: 'Mixed generated prose', published_at: new Date().toISOString() }] } }).render('en');
  assert.match(desk, /Original Bellomberg text · language unknown/);
  assert.match(desk, /SYNTH: 5 azioni/);
});

test('calendar numbers follow locale while provider numeric strings and titles stay verbatim', () => {
  // Keep the event inside the default upcoming-week filter across UTC/local midnight.
  const eventDate = new Date(Date.now() + 3 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
  const view = retainedPage({ view: 'desk', econ: [{ date: eventDate, country: 'US', importance: 5, title: 'Original release title', previous: 1234.5, estimate: '1,234.50 provider text', actual: 0 }] });
  const it = view.render('it'), en = view.render('en');
  assert.match(it, /1\.234,5/); assert.match(en, /1,234\.5/);
  for (const html of [it, en]) { assert.match(html, /1,234\.50 provider text/); assert.match(html, /Original release title/); }
});

test('cached topic labels and SEC wording change language while source excerpts and requests stay intact', async () => {
  const published_at = new Date().toISOString();
  const corporate = { items: [{ title: 'SYNTH: depositato evento', snippet: 'Original filing excerpt', title_origin: 'bellomberg',
    snippet_origin: 'source', presentation_languages: ['it', 'en'], source_type: 'sec', published_at }],
    _presentation_v1: { version: 1, texts: [{ path: ['items', 0, 'title'], it: 'SYNTH: depositato evento', en: 'SYNTH: event filed' }] } };
  const macro = { items: [{ title: 'Original macro headline', topic_label: 'Mercati emergenti', published_at }],
    _presentation_v1: { version: 1, texts: [{ path: ['items', 0, 'topic_label'], it: 'Mercati emergenti', en: 'Emerging markets' }] } };
  const view = retainedPage({ view: 'desk' }, { newsCorporateEvents: async () => corporate, newsMacro: async () => macro });
  view.render('it'); await view.effects();
  const before = view.requests.filter(row => ['newsCorporateEvents', 'newsMacro'].includes(row.name));
  const it = view.render('it'), en = view.render('en'); await view.effects();
  assert.match(it, /SYNTH: depositato evento/); assert.match(en, /SYNTH: event filed/);
  assert.match(en, /Emerging markets/); assert.match(en, /Original macro headline/);
  assert.match(en, /Original filing excerpt/);
  assert.match(en, /Bellomberg wording · English/); assert.match(en, /Source excerpt/);
  assert.doesNotMatch(en, /Original backend label/);
  assert.deepEqual(view.requests.filter(row => ['newsCorporateEvents', 'newsMacro'].includes(row.name)), before);
  assert.equal(corporate.items[0].title, 'SYNTH: depositato evento');
});

test('wire colors, chronological filters, relevance ranking and radar coordinates are invariant across IT/EN', () => {
  const now = new Date().toISOString();
  const feed = [
    { id: 1, title: 'First synthetic source', published_at: now, relevance: 9, _materiality: 12.5, sentiment: 'bullish', theme: 'fed' },
    { id: 2, title: 'Second synthetic source', published_at: now, relevance: 8, _materiality: 10, sentiment: 'bearish', theme: 'ukraine' },
  ];
  const view = retainedPage({ feed });
  const it = view.render('it'), en = view.render('en');
  const circles = html => [...html.matchAll(/<circle\b[^>]*>/g)].map(x => x[0]);
  assert.ok(circles(it).length > 3);
  assert.deepEqual(circles(it), circles(en));
  const bars = html => [...html.matchAll(/<i class="(?:on|now|)"[^>]*style="([^"]*)"/g)].map(x => x[1]);
  assert.equal(bars(it).length, 24);
  assert.deepEqual(bars(it), bars(en));
  assert.match(it, /class="now"/); assert.match(en, /class="now"/);
  assert.match(it, /M12,5/); assert.match(en, /M12\.5/);
  view.set('fRel', 8); view.set('fSent', ['neg']); view.set('fThemes', ['ukraine']);
  const filtered = view.render('en');
  assert.match(filtered, /1\/2 in wire · 3 active filters/);
});

// 13/09 (Claude Opus 5): frasi attese scritte qui, non lette dai cataloghi sotto prova.
test('the radar contact count handles zero, one and two contacts in IT/EN', () => {
  const now = new Date().toISOString();
  for (const count of [0, 1, 2]) {
    const feed = Array.from({ length: count }, (_, id) => ({ id, title: 'Synthetic source', published_at: now }));
    const before = structuredClone(feed), view = retainedPage({ feed });
    assert.ok(view.render('it').includes(`<span class="side num">${count} BLIP</span>`));
    assert.ok(view.render('en').includes(`<span class="side num">${count} ${count === 1 ? 'BLIP' : 'BLIPS'}</span>`));
    assert.deepEqual(feed, before);
  }
});

test('limiter codes of muted providers are translated, while free backend reasons stay verbatim', () => {
  const mute = { newsapi: 'SKIP_BUDGET', gnews: 'SKIP_DISABLED', tiingo: 'SENZA_CHIAVE: TIINGO_API_KEY assente nel .env' };
  const giro = { stato: 'degradato', timestamp: new Date().toISOString(), fetched: 3, classified: 2, saved: 1, skipped_duplicates: 0,
    providers_blocked: { newsapi: 'SKIP_BUDGET', gnews: 'SKIP_DISABLED' } };
  const view = retainedPage({ fonti: { declared: true, mute, avviso: null }, giro });
  const it = view.render('it'), en = view.render('en');
  assert.match(it, /newsapi \(BUDGET ESAURITO\) · gnews \(SOSPESO\) · tiingo \(CHIAVE ASSENTE: TIINGO_API_KEY assente nel \.env\)/);
  assert.match(en, /newsapi \(BUDGET EXHAUSTED\) · gnews \(SUSPENDED\) · tiingo \(MISSING API KEY: TIINGO_API_KEY assente nel \.env\)/);
  assert.match(it, /<span class="mt">BUDGET ESAURITO<\/span>/); assert.match(it, /<span class="mt">SOSPESO<\/span>/);
  assert.match(en, /<span class="mt">BUDGET EXHAUSTED<\/span>/); assert.match(en, /<span class="mt">SUSPENDED<\/span>/);
  assert.match(it, / Bloccati: newsapi \(BUDGET ESAURITO\) · gnews \(SOSPESO\)\./);
  assert.match(en, / Blocked: newsapi \(BUDGET EXHAUSTED\) · gnews \(SUSPENDED\)\./);
  for (const html of [it, en]) assert.doesNotMatch(html, /\((?:BUDGET|DISABLED)\)|>(?:BUDGET|DISABLED)</);
});

test('missing key and module codes are localized at each news display while suffixes stay verbatim', () => {
  const suffix = ': Original detail SENZA_CHIAVE: nested code';
  for (const [code, it, en] of [['SENZA_CHIAVE', 'CHIAVE ASSENTE', 'MISSING API KEY'], ['MODULO_ASSENTE', 'MODULO ASSENTE', 'MODULE UNAVAILABLE']]) {
    for (const tail of ['', suffix]) {
      const mute = { synthetic: code + tail };
      const giro = { stato: 'degradato', timestamp: new Date().toISOString(), fetched: 0, classified: 0, saved: 0, skipped_duplicates: 0, providers_blocked: mute };
      const before = structuredClone({ mute, giro }), view = retainedPage({ fonti: { declared: true, mute, avviso: null }, giro });
      for (const [language, label] of [['it', it], ['en', en]]) {
        const html = view.render(language), reason = label + tail;
        assert.ok(html.includes(` </b>synthetic (${reason})</span>`), 'muted-source banner');
        assert.ok(html.includes(`${language === 'it' ? ' Bloccati: ' : ' Blocked: '}synthetic (${reason}).`), 'last feed run');
        assert.ok(html.includes(`<span class="mt">${reason}</span>`), 'source card');
      }
      assert.deepEqual({ mute, giro }, before);
    }
  }
  for (const reason of ['SENZA_CHIAVE_EXTRA: original', 'MODULO_ASSENTE_EXTRA: original', 'Original SENZA_CHIAVE: detail']) {
    const view = retainedPage({ fonti: { declared: true, mute: { synthetic: reason }, avviso: null } });
    for (const language of ['it', 'en']) assert.ok(view.render(language).includes(`synthetic (${reason})`));
  }
});

// 13/09 (Claude Opus 5): chiavi e codici come li scrive news_aggregator.providers_blocked (TERMINI_MUTI,
// TEMI_TITOLI_MUTI, "NEGOZIO_{origine}: {motivo}"); le frasi attese sono scritte qui, non lette dai cataloghi.
// In fonti_mute il motivo arriva nella lingua della richiesta; in ultimo_giro (providers_blocked scritto dal
// giro del feed) resta nella lingua del giro che l'ha scritto. In entrambi si traduce solo il codice in testa.
test('the two private stores among muted sources get a reader name, and only the code of their reason is translated', () => {
  const unreadable = 'JSONDecodeError: DEMO line 3 column 1';
  const expected = {
    it: { reason: 'negozio non trovato: DEMO/termini.json (copia DEMO.example.json in data/)', blocked: ' Bloccati: ',
      terms: 'termini di ricerca news (negozio)', topics: 'titoli per tema (negozio)',
      missing: 'NEGOZIO ASSENTE', broken: 'NEGOZIO ILLEGGIBILE', sub: 'TITOLI PER TEMA (NEGOZIO) · TERMI…' },
    en: { reason: 'Store not found: DEMO/termini.json (copy DEMO.example.json into data/)', blocked: ' Blocked: ',
      terms: 'news search terms (store)', topics: 'securities by topic (store)',
      missing: 'STORE MISSING', broken: 'STORE UNREADABLE', sub: 'SECURITIES BY TOPIC (STORE) · NEW…' },
  };
  for (const [language, x] of Object.entries(expected)) {
    const mute = { 'termini_news (negozio)': `NEGOZIO_ASSENTE: ${x.reason}`, 'temi_titoli (negozio)': `NEGOZIO_ILLEGGIBILE: ${unreadable}` };
    const giro = { stato: 'degradato', timestamp: new Date().toISOString(), fetched: 3, classified: 2, saved: 1, skipped_duplicates: 0,
      providers_blocked: mute };
    const html = retainedPage({ fonti: { declared: true, mute, avviso: null }, giro }).render(language);
    const has = text => assert.ok(html.includes(text), `${language} lacks: ${text}`);
    const listed = `${x.terms} (${x.missing}: ${x.reason}) · ${x.topics} (${x.broken}: ${unreadable})`;
    has(` </b>${listed}</span>`);
    has(`${x.blocked}${listed}.`);
    has(`>${x.sub}</div>`);
    has(`<span class="nm">${x.terms}</span>`); has(`<span class="nm">${x.topics}</span>`);
    has(`<span class="mt">${x.missing}: ${x.reason}</span>`); has(`<span class="mt">${x.broken}: ${unreadable}</span>`);
    has(`title="${x.terms}: `); has(`title="${x.topics}: `);
    assert.doesNotMatch(html, /termini_n|temi_titoli|NEGOZIO_(?:ASSENTE|ILLEGGIBILE)/i);
  }
});

test('a missing cached briefing is not presented as a generated original briefing', () => {
  const html = retainedPage({ view: 'desk', briefing: { language: 'en', generated_at: null, stale: true, briefing_md: 'No cached English briefing exists.' } }).render('en');
  assert.match(html, /No cached English briefing exists/);
  assert.doesNotMatch(html, /Original briefing · English/);
});

test('an unreadable briefing cache is a fault, never first use or never generated', () => {
  for (const language of ['it', 'en']) {
    const html = retainedPage({ view: 'desk', briefing: { language, generated_at: null, stale: true, error_code: 'briefing_cache_unreadable', error: 'Original cache corruption', briefing_md: 'Original unreadable cache notice' } }).render(language);
    assert.doesNotMatch(html, />MAI<|>NEVER</);
    assert.match(html, language === 'it' ? /CACHE ILLEGGIBILE/ : /UNREADABLE CACHE/);
  }
});
