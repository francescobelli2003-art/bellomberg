const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');

const field = (blocco, tipo, extra = {}) => ({ blocco, tipo, obbligatorio: true,
  descrizione: 'Synthetic field description', unita: '', intervallo: null, scelte: null, massimo_char: null, ...extra });
const state = () => ({ dichiarato: true, causa: null, dettaglio: null, campi_mancanti: [],
  valori: { profilo: { orizzonte_anni: 5 }, rischio: { var99_1g_pct: 2.5 } },
  origine: 'personalizzato', impronta: 'synthetic-fingerprint', dichiarato_il: '2026-09-12',
  campi: { orizzonte_anni: field('profilo', 'int', { intervallo: [1, 30] }),
    var99_1g_pct: field('rischio', 'pct', { intervallo: [0.5, 15] }) }, errori: [], esempio: null });

function harness(page, overrides = {}) {
  ambienteBrowser(); globalThis.sessionStorage = globalThis.localStorage;
  const listeners = new Map();
  globalThis.window = { addEventListener: (n, f) => listeners.set(n, f), removeEventListener() {},
    setTimeout, clearTimeout, dispatchEvent() {} };
  globalThis.document.getElementById = () => ({ focus() {}, scrollIntoView() {} });
  const slots = [], effects = [], calls = []; let cursor = 0, tree;
  const memo = (fn, deps) => { const i = cursor++, old = slots[i];
    if (!old || !deps || deps.some((v, j) => !Object.is(v, old.deps[j]))) slots[i] = { deps, value: fn() };
    return slots[i].value; };
  const hooks = { ...React, useState(initial) { const i = cursor++;
    if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial;
    return [slots[i], v => { slots[i] = typeof v === 'function' ? v(slots[i]) : v; }]; },
    useRef: initial => memo(() => ({ current: initial }), []), useMemo: memo,
    useCallback: (fn, deps) => memo(() => fn, deps), useEffect(fn, deps) { memo(() => { effects.push(fn); return true; }, deps); },
    useSyncExternalStore(_subscribe, snapshot) { return snapshot(); } };
  const fixtures = { mandato: state(), portfolio: { positions: [] }, ...overrides };
  const api = { Bellomberg: new Proxy({}, { get(_t, method) { return async (...args) => {
    calls.push({ method, args }); const value = fixtures[method];
    if (!(method in fixtures)) throw new Error('Unexpected API: ' + method);
    if (value instanceof Error) throw value; return typeof value === 'function' ? value(...args) : value;
  }; } }), API_BASE: 'http://synthetic.invalid', requestHeaders: () => ({}) };
  const load = creaCaricatore({ stub: { react: hooks, '@/lib/api': api, './api': api } });
  const module = load('pages/' + page + '.tsx');
  const Component = page === 'MandatoPage' ? module.MandatoForm : module.default;
  const language = load('i18n/lingua.ts');
  const nodes = (value, pred) => !value || typeof value !== 'object' ? [] : Array.isArray(value)
    ? value.flatMap(x => nodes(x, pred)) : [...(pred(value) ? [value] : []), ...nodes(value.props?.children, pred)];
  const render = () => { cursor = 0; tree = Component(); const html = renderToStaticMarkup(tree);
    while (effects.length) effects.shift()(); return html; };
  return { render, calls, load, language, async settle() { await Promise.resolve(); await Promise.resolve(); return render(); },
    nodes: pred => nodes(tree, pred) };
}

test('F18 loading is a status, then section navigation and language preserve the draft without another read', async () => {
  const h = harness('MandatoPage'); h.language.impostaLinguaCorrente('it');
  const loading = h.render(); assert.match(loading, /role="status"/); assert.doesNotMatch(loading, /NON LEGGIBILE/);
  await h.settle(); const qty = h.nodes(n => n.props?.id === 'm-orizzonte_anni')[0];
  qty.props.onChange({ target: { value: '7' } }); h.render(); const count = h.calls.length;
  h.language.impostaLinguaCorrente('en'); const en = h.render();
  assert.match(en, /Mandate sections/); assert.match(en, /Holding horizon/); assert.match(en, /value="7"/);
  assert.equal(h.calls.length, count); assert.match(en, /data-layout="worktable"/);
});

test('mandate preview discards a late response and only a matching readback confirms save', async () => {
  let resolve, savedBody; const h = harness('MandatoPage', {
    mandatoAnteprima: () => new Promise(r => { resolve = r; }),
    salvaMandato: value => { savedBody = value; return state(); } });
  h.render(); await h.settle();
  const click = key => h.nodes(n => n.props?.['data-action'] === key)[0].props.onClick();
  const inFlight = click('preview'); await Promise.resolve();
  h.nodes(n => n.props?.id === 'm-orizzonte_anni')[0].props.onChange({ target: { value: '8' } }); h.render();
  resolve({ testo: 'Original preview text', impronta: 'synthetic-fingerprint', origine: 'personalizzato' });
  await inFlight; h.render(); assert.equal(h.nodes(n => n.props?.['data-action'] === 'save')[0].props.disabled, true);
  assert.equal(savedBody, undefined);
});

test('captured mandate grammar remains stable while parser diagnostics follow the current interface', () => {
  const load = creaCaricatore(); const { leggiNumeroMandato } = load('lib/mandato.ts');
  const campo = field('rischio', 'pct', { intervallo: [0, 100] });
  assert.deepEqual(leggiNumeroMandato('12,50', campo, 'it', 'en'), { ok: true, valore: 12.5 });
  const invalid = leggiNumeroMandato('abc', campo, 'it', 'en');
  assert.equal(invalid.ok, false); assert.match(invalid.motivo, /digits/i);
});

test('Journal renders English controls and keeps original text after a language change without network writes', async () => {
  const oldFetch = globalThis.fetch; const requests = [];
  globalThis.fetch = async (url, options) => { requests.push({ url, options }); return { ok: true,
    json: async () => ({ items: [], total: 0, limit: 30, offset: 0 }) }; };
  try {
    const h = harness('JournalPage'); h.language.impostaLinguaCorrente('it'); h.render(); await h.settle();
    h.nodes(n => n.props?.className === 'journal-title-input')[0].props.onChange({ target: { value: 'Originale: α — 1,25' } });
    h.render(); const count = requests.length; h.language.impostaLinguaCorrente('en'); const en = h.render();
    assert.match(en, /Your notes/); assert.match(en, /Originale: α — 1,25/); assert.match(en, /Save note/);
    assert.doesNotMatch(en, /Salva nota|Lettura delle note|Come evolve la tua idea/);
    assert.equal(requests.length, count); assert.ok(requests.every(r => r.options.method === 'GET'));
  } finally { globalThis.fetch = oldFetch; }
});

test('a recovered draft without a recognised number format says so, and the notice stays true after restore', async () => {
  // 13/09 (Claude Opus 5): a draft saved before the page recorded inputLanguage was restored as
  // Italian without a word, and the notice written for it was never rendered.
  const DRAFT_SLOT = 'bellomberg_mandato_bozza_v1';
  const draft = extra => JSON.stringify({ baseImpronta: 'synthetic-fingerprint',
    form: { orizzonte_anni: '7', var99_1g_pct: '2,5' }, salvataIl: '2026-09-12T10:00:00Z', ...extra });
  const h = harness('MandatoPage'); h.language.impostaLinguaCorrente('en');
  const it = h.load('i18n/it/mandate.ts').mandate, en = h.load('i18n/en/mandate.ts').mandate;
  globalThis.sessionStorage.setItem(DRAFT_SLOT, draft({}));
  h.render(); const before = await h.settle();
  assert.ok(before.includes(en.recoverable), 'fixture: the draft banner is on screen');
  assert.ok(before.includes(en.format_en), 'fixture: an English interface starts from the English grammar');
  assert.ok(before.includes(en.legacy_format), 'a draft without inputLanguage must declare the assumed format');
  h.nodes(n => n.type === 'button' && n.props?.children === en.restore_explicit)[0].props.onClick();
  const restored = h.render();
  assert.ok(restored.includes(en.format_it), 'restore reads the undeclared draft as Italian, as the notice says');
  assert.ok(restored.includes(en.legacy_format), 'the notice is still on screen after restore');
  const saved = JSON.parse(globalThis.sessionStorage.getItem(DRAFT_SLOT));
  assert.equal(saved.inputLanguage, 'it');
  assert.equal(saved.formatoPresunto, true, 'the re-saved draft keeps the assumption, not a declared format');
  h.language.impostaLinguaCorrente('it'); assert.ok(h.render().includes(it.legacy_format));

  // The same page opened again reads the re-saved draft: the assumption is still declared.
  const again = harness('MandatoPage'); again.language.impostaLinguaCorrente('it');
  globalThis.sessionStorage.setItem(DRAFT_SLOT, JSON.stringify(saved));
  again.render(); assert.ok((await again.settle()).includes(it.legacy_format), 'a remount keeps the notice until the mandate is saved');

  // An unrecognised language no longer reaches the number parser: the page renders and says so.
  const foreign = harness('MandatoPage'); foreign.language.impostaLinguaCorrente('it');
  globalThis.sessionStorage.setItem(DRAFT_SLOT, draft({ inputLanguage: 'fr' }));
  foreign.render(); const odd = await foreign.settle();
  assert.ok(odd.includes(it.legacy_format), 'an unrecognised format is declared too');
  foreign.nodes(n => n.type === 'button' && n.props?.children === it.restore_explicit)[0].props.onClick();
  assert.ok(foreign.render().includes(it.format_it), 'restoring an unrecognised format reads Italian without throwing');

  const declared = harness('MandatoPage'); declared.language.impostaLinguaCorrente('it');
  globalThis.sessionStorage.setItem(DRAFT_SLOT, draft({ inputLanguage: 'en' }));
  declared.render(); const modern = await declared.settle();
  assert.ok(modern.includes(it.recoverable), 'fixture: the declared draft banner is on screen');
  assert.ok(!modern.includes(it.legacy_format), 'a draft that declares its format carries no legacy notice');
});
