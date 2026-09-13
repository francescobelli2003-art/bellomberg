const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { MemoryRouter } = require('react-router-dom');
const { creaCaricatore, ambienteBrowser, apiFinta } = require('./_carica.cjs');

test('nineteen destinations keep routes and shortcuts while their names follow the selected language', () => {
  ambienteBrowser();
  const load = creaCaricatore();
  const nav = load('lib/navigation.ts');
  for (const entry of nav.NAVIGATION) {
    const it = nav.localizeDestination(entry, 'it'), en = nav.localizeDestination(entry, 'en');
    for (const field of ['id', 'to', 'key', 'kind', 'short']) assert.equal(it[field], en[field], `${entry.id}.${field}`);
    assert.doesNotMatch(en.label + en.group, /⟦/);
  }
  assert.equal(nav.NAVIGATION.length, 19);
  assert.equal(nav.localizeDestination(nav.SETTINGS_DESTINATION, 'en').label, 'Settings');
  assert.equal(nav.localizeDestination(nav.SETTINGS_DESTINATION, 'it').label, 'Impostazioni');
  assert.equal(nav.localizeDestination(nav.NAVIGATION.find(x => x.id === 'mandato'), 'en').label, 'Mandate and Journal');
  assert.equal(nav.localizeDestination(nav.NAVIGATION.find(x => x.id === 'progress'), 'en').label, 'Agent Progress');
});

test('module bar tooltips separate key, page and group with a middle dot in both languages', () => {
  // c5 g2 (Claude Opus 5): the tooltip carried literal '?' bytes (lost glyph). Expected titles frozen here.
  ambienteBrowser();
  const load = creaCaricatore({ stub: { '@/lib/api': apiFinta(), './SettingsPanel': { default: () => null, __esModule: true } } });
  const language = load('i18n/lingua.ts'), Layout = load('components/Layout.tsx').default;
  for (const [locale, titles] of [
    ['it', ['F1 · Centro di comando · Portafoglio', 'F16 · Inserimento operazioni · Operazioni']],
    ['en', ['F1 · Command Center · Portfolio', 'F16 · Trade Entry · Operations']],
  ]) {
    language.impostaLinguaCorrente(locale);
    const html = renderToStaticMarkup(React.createElement(MemoryRouter, { initialEntries: ['/dashboard'] }, React.createElement(Layout, {}, null)));
    for (const title of titles) assert.ok(html.includes(`title="${title}"`), `${locale}: ${title}`);
    assert.doesNotMatch(html, /title="F\d+ \?/);
  }
});

test('confirmation, mandate loading and caught errors render in each language, preserving the original error', () => {
  ambienteBrowser();
  const load = creaCaricatore({ stub: { '@/lib/api': apiFinta() } });
  const language = load('i18n/lingua.ts');
  const Confirm = load('components/ConfirmDialog.tsx').default;
  const Mandate = load('components/MandatoGate.tsx').default;
  const Boundary = load('components/ErrorBoundary.tsx').default;
  for (const [locale, cancel, loading, errorLabel] of [['it', 'ANNULLA', 'VERIFICA MANDATO', 'ERRORE'], ['en', 'CANCEL', 'CHECKING MANDATE', 'ERROR']]) {
    language.impostaLinguaCorrente(locale);
    const confirm = renderToStaticMarkup(React.createElement(Confirm, { open: true, title: 'SYNTHETIC', confirmLabel: 'OK', onConfirm() {}, onCancel() {} }));
    assert.match(confirm, new RegExp(cancel));
    if (locale === 'en') assert.doesNotMatch(confirm, /ANNULLA|CAMBIA PULSANTE|INVIO ATTIVA/);
    const mandate = renderToStaticMarkup(React.createElement(MemoryRouter, {}, React.createElement(Mandate)));
    assert.match(mandate, new RegExp(loading));
    const boundary = new Boundary({ label: 'SYNTHETIC' });
    boundary.state = { hasError: true, error: new Error('Original diagnostic 731'), info: null };
    const failure = renderToStaticMarkup(boundary.render());
    assert.match(failure, /Original diagnostic 731/);
    assert.match(failure, new RegExp(errorLabel));
    if (locale === 'en') assert.doesNotMatch(failure, /Un componente|generato un|RIPROVA/);
  }
});

test('API language is captured at request time, independent of token availability', async () => {
  ambienteBrowser();
  global.window = { location: { reload() { throw new Error('unexpected reload'); } } };
  let interceptor;
  const requests = [];
  const api = { interceptors: { request: { use(fn) { interceptor = fn; } }, response: { use() {} } },
    get: async url => { const config = interceptor({ url, headers: {} }); requests.push(config); return { data: {} }; },
    put: async (url, data) => { const config = interceptor({ url, data, headers: {} }); requests.push(config); return { data: {} }; },
  };
  const load = creaCaricatore({ stub: { axios: { create: () => api } } });
  const language = load('i18n/lingua.ts'), client = load('lib/api.ts');
  language.impostaLinguaCorrente('en');
  const captured = client.requestHeaders();
  assert.deepEqual(captured, { 'X-BB-Language': 'en' });
  localStorage.setItem(client.TOKEN_STORAGE_KEY, 'synthetic-session');
  await client.Bellomberg.preferences();
  language.impostaLinguaCorrente('it');
  await client.Bellomberg.savePreferences({ language: 'en' });
  assert.deepEqual(requests.map(x => [x.url, x.headers['X-BB-Language']]), [['/preferences', 'en'], ['/preferences', 'it']]);
  assert.equal(requests[0].headers['X-BB-Token'], 'synthetic-session');
  assert.equal(captured['X-BB-Language'], 'en', 'in-flight request stays in its captured language');
});

test('sign-in is bilingual before choosing and follows the persisted language afterwards', () => {
  for (const locale of [null, 'it', 'en']) {
    ambienteBrowser();
    if (locale) localStorage.setItem('bellomberg.lingua', locale);
    let phaseRead = false;
    const hooks = { ...React,
      useState: initial => {
        const value = typeof initial === 'function' ? initial() : initial;
        if (value === 'check' && !phaseRead) { phaseRead = true; return ['login', () => {}]; }
        return [value, () => {}];
      },
      useRef: value => ({ current: value }), useEffect() {},
      useSyncExternalStore: (_subscribe, get) => get(),
    };
    // No canvas, API, timers or auth call is started; the actual login JSX is rendered.
    const load = creaCaricatore({ stub: { react: hooks, '@/lib/api': apiFinta() } });
    const Login = load('components/LoginGate.tsx').default;
    const html = renderToStaticMarkup(Login({ children: null }));
    if (locale !== 'en') assert.match(html, /DIGITA IL PIN/);
    if (locale !== 'it') assert.match(html, /ENTER PIN/);
    if (locale === 'en') assert.doesNotMatch(html, /VALUTAZIONI DCF|IN ATTESA DI AUTORIZZAZIONE|OPERATORE/);
    assert.match(html, /<button[^>]*class="authbtn"/);
    assert.doesNotMatch(html, /⟦/);
  }
});

test('a verified session remains usable when browser storage fails, and persistence is explicitly reported', () => {
  ambienteBrowser();
  let reloads = 0;
  global.window = { location: { reload() { reloads++; } } };
  const api = { interceptors: { request: { use() {} }, response: { use() {} } } };
  const load = creaCaricatore({ stub: { axios: { create: () => api } } });
  const client = load('lib/api.ts');
  localStorage.getItem = localStorage.setItem = localStorage.removeItem = () => { throw new Error('synthetic storage denied'); };
  assert.equal(client.saveSessionToken('synthetic-verified-token'), false, 'caller must disclose a memory-only session');
  assert.equal(client.getSessionToken(), 'synthetic-verified-token');
  assert.equal(client.requestHeaders()['X-BB-Token'], 'synthetic-verified-token');
  client.clearSessionAndReload();
  assert.equal(client.getSessionToken(), null);
  assert.equal(reloads, 1);
});

test('session persistence is read back and malformed authentication cannot replace a valid session', () => {
  ambienteBrowser();
  global.window = {};
  const api = { interceptors: { request: { use() {} }, response: { use() {} } } };
  const client = creaCaricatore({ stub: { axios: { create: () => api } } })('lib/api.ts');
  assert.equal(client.saveSessionToken('synthetic-valid'), true);
  for (const value of [null, undefined, '', '  ', 731]) assert.throws(() => client.saveSessionToken(value));
  assert.equal(client.getSessionToken(), 'synthetic-valid');
  localStorage.setItem = () => {};
  assert.equal(client.saveSessionToken('synthetic-new'), false, 'silently discarded storage writes are detected');
  assert.equal(client.getSessionToken(), 'synthetic-new', 'the verified fresh token supersedes the stale cached token');
});

test('blocked browser storage reaches PIN entry and shows the limitation instead of a blank screen', async () => {
  ambienteBrowser();
  localStorage.getItem = localStorage.setItem = localStorage.removeItem = () => { throw new Error('synthetic storage denied'); };
  global.window = { addEventListener() {}, removeEventListener() {} };
  const state = [], effects = [], cleanups = [];
  let cursor = 0, first = true, statusCalls = 0;
  const hooks = { ...React,
    useState(initial) { const i = cursor++; if (first) state[i] = typeof initial === 'function' ? initial() : initial;
      return [state[i], value => { state[i] = typeof value === 'function' ? value(state[i]) : value; }]; },
    useRef: value => ({ current: value }), useEffect(fn) { if (first) effects.push(fn); },
    useSyncExternalStore: (_subscribe, get) => get(),
  };
  const fake = apiFinta();
  fake.Bellomberg = { authStatus: async () => { statusCalls++; return { default_pin: false }; } };
  const Login = creaCaricatore({ stub: { react: hooks, '@/lib/api': fake } })('components/LoginGate.tsx').default;
  try {
    Login({ children: null });
    for (const effect of effects) { const cleanup = effect(); if (cleanup) cleanups.push(cleanup); }
    await Promise.resolve();
    first = false; cursor = 0;
    const html = renderToStaticMarkup(Login({ children: null }));
    assert.equal(statusCalls, 1);
    assert.match(html, /ENTER PIN/);
    assert.match(html, /Browser storage unavailable/);
    assert.match(html, /Memoria del browser non disponibile/);
  } finally { for (const cleanup of cleanups) cleanup(); }
});

for (const [surface, file, invoke] of [
  ['Journal', 'lib/journal.ts', mod => mod.Journal.get(731)],
  ['Vol Deck', 'lib/vol-deck.ts', mod => mod.volRequest('/options/synthetic')],
  ['Agent Progress', 'lib/agent-progress.ts', mod => mod.getAgentProgress()],
]) test(`${surface} captures language and token through its real fetch boundary`, async () => {
  ambienteBrowser();
  global.window = { setTimeout, clearTimeout, bellomberg: { apiUrl: 'http://synthetic.invalid' } };
  const axios = { create: () => ({ interceptors: { request: { use() {} }, response: { use() {} } } }) };
  const load = creaCaricatore({ stub: { axios } });
  const language = load('i18n/lingua.ts'), client = load('lib/api.ts'), mod = load(file);
  client.saveSessionToken('synthetic-verified');
  const previousFetch = global.fetch, calls = [];
  global.fetch = async (url, config) => { calls.push({ url, ...config }); return { ok: true, status: 200, json: async () => ({ synthetic: true }) }; };
  try {
    language.impostaLinguaCorrente('en');
    const first = invoke(mod);
    language.impostaLinguaCorrente('it');
    const second = invoke(mod);
    await Promise.all([first, second]);
    assert.deepEqual(calls.map(x => x.headers['X-BB-Language']), ['en', 'it']);
    assert.deepEqual(calls.map(x => x.headers['X-BB-Token']), ['synthetic-verified', 'synthetic-verified']);
  } finally { global.fetch = previousFetch; }
});
