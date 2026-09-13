const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react'), JSX = require('react/jsx-runtime');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');
ambienteBrowser(); globalThis.sessionStorage = { getItem() { return null; }, removeItem() {} };
const words = n => n == null ? '' : Array.isArray(n) ? n.map(words).join(' ') : typeof n === 'object' ? words(n.props?.children) : String(n);
function retained(fail = {}) {
  let si = 0, mi = 0, ei = 0, ri = 0;
  const states = [], memos = [], effects = [], deps = [], refs = [], nodes = [], calls = [];
  const remember = (fn, d) => { const at = mi++, before = memos[at]; if (!before || !d || d.some((v, i) => !Object.is(v, before.d[i]))) memos[at] = { d, value: fn() }; return memos[at].value; };
  const jsx = name => (type, props, ...args) => { nodes.push({ type, props }); return JSX[name](type, props, ...args); };
  const data = { mktOverview: {}, mktSearch: { results: [] }, favorites: { favorites: [] },
    mktQuote: { ticker: 'SYNTH.X', name: 'Synthetic', sector: 'Settore originale', summary: 'Profilo originale 158,50', price: 12.5, prev_close: 10 },
    mktNews: { items: [] }, mktFinancials: { ticker: 'SYNTH.X', statements: { income: { years: ['2025'], rows: { 'Ricavi originali': [1234.5] } } } },
    mktHolders: { ticker: 'SYNTH.X', major: [], institutional: [] }, favAdd: {}, favDel: {} };
  const api = Object.fromEntries(Object.entries(data).map(([name, result]) => [name, async (...args) => {
    calls.push([name, ...args]); if (name in fail) throw fail[name]; return result;
  }]));
  const load = creaCaricatore({ stub: { react: { ...React, useMemo: remember, useCallback: (fn, d) => remember(() => fn, d),
    useRef(initial) { return refs[ri++] ||= { current: initial }; },
    useState(initial) { const at = si++; if (!(at in states)) states[at] = typeof initial === 'function' ? initial() : initial; return [states[at], v => { states[at] = typeof v === 'function' ? v(states[at]) : v; }]; },
    useEffect(fn, d) { const at = ei++, before = deps[at]; if (!before || !d || d.some((v, i) => !Object.is(v, before[i]))) effects.push(fn); deps[at] = d; },
  }, 'react/jsx-runtime': { ...JSX, jsx: jsx('jsx'), jsxs: jsx('jsxs') }, '@/lib/api': { Bellomberg: api },
  '@/components/TvChartPanel': { __esModule: true, default: () => null } } });
  const language = load('i18n/lingua.ts'), Page = load('pages/MarketPage.tsx').default;
  const render = lang => { si = mi = ei = ri = 0; effects.length = nodes.length = 0; language.impostaLinguaCorrente(lang); return renderToStaticMarkup(React.createElement(Page)); };
  const settle = async (delay = 0) => { for (const fn of effects.splice(0)) fn(); await new Promise(resolve => setTimeout(resolve, delay)); };
  const ready = async lang => { render(lang); await settle(); return render(lang); };
  const select = async lang => { nodes.find(n => n.type === 'button' && words(n.props.children) === 'AAPL').props.onClick(); render(lang); await settle(); return render(lang); };
  return { ready, render, settle, select, nodes, calls };
}
const detail = msg => ({ response: { data: { detail: [{ msg }] } } });
test('search failures are visible in both languages and never masquerade as no matches', async () => {
  const ui = retained({ mktSearch: detail('Original search error') }); await ui.ready('it');
  ui.nodes.find(n => n.type === 'input').props.onChange({ target: { value: 'SYNTH' } }); ui.render('it'); await ui.settle(350);
  const it = ui.render('it'), en = ui.render('en'); for (const html of [it, en]) assert.match(html, /Original search error/);
  assert.match(it, /Ricerca non disponibile/); assert.match(en, /Search unavailable/);
  assert.equal(ui.calls.filter(c => c[0] === 'mktSearch').length, 1);
});
test('failed favorite reads keep the state unknown and financial failures retain their original detail', async () => {
  const ui = retained({ favorites: detail('Original favorite read error'), mktFinancials: detail('Original financial error') });
  await ui.ready('it'); const it = await ui.select('it'), en = ui.render('en');
  for (const html of [it, en]) { assert.match(html, /Original favorite read error/); assert.match(html, /Original financial error/); }
  const favorite = ui.nodes.find(n => n.type === 'button' && n.props.title?.includes('favorites'));
  assert.equal(favorite.props.disabled, true); assert.equal(ui.calls.filter(c => c[0] === 'favorites').length, 1);
});
// 13/09 (Claude Opus 5): azionariato vuoto era 'n/d' in entrambe le lingue e le frasi EN dicevano 'n.d.'.
test('empty or failed ownership and a failed overview are declared with the abbreviation of the page language', async () => {
  const ui = retained(); await ui.ready('it'); const it = await ui.select('it'), en = ui.render('en');
  assert.equal((it.match(/>n\.d\.<\/div>/g) || []).length, 2); assert.equal((en.match(/>n\/a<\/div>/g) || []).length, 2);
  for (const html of [it, en]) assert.doesNotMatch(html, /n\/d/);
  const failed = retained({ mktHolders: detail('Original holders error') }); await failed.ready('it');
  const itKo = await failed.select('it'), enKo = failed.render('en');
  assert.match(itKo, /n\.d\. — azionariato in errore: Original holders error/); assert.match(itKo, />n\.d\. — azionariato in errore</);
  assert.match(enKo, /n\/a — ownership failed: Original holders error/); assert.match(enKo, />n\/a — ownership failed</);
  assert.doesNotMatch(enKo, /n\.d\./);
  const overview = retained({ mktOverview: detail('Original overview error') }); const itOv = await overview.ready('it'), enOv = overview.render('en');
  assert.match(itOv, /n\.d\. — panoramica in errore \(v\. sopra\)/); assert.match(enOv, /n\/a — overview failed \(see above\)/);
  assert.doesNotMatch(enOv, /n\.d\./);
});
test('favorite mutation failures report uncertainty and preserve provider prose with an original-source label', async () => {
  const ui = retained({ favAdd: detail('Original mutation error') }); await ui.ready('it'); await ui.select('en');
  await ui.nodes.find(n => n.type === 'button' && n.props.title?.includes('Add to favorites')).props.onClick();
  const en = ui.render('en'); assert.match(en, /Original mutation error/); assert.match(en, /Favorite change not confirmed/);
  assert.match(en, /Original source text/); assert.match(en, /Ricavi originali/); assert.match(en, /Settore originale/);
  ui.nodes.find(n => n.type === 'button' && /company profile/i.test(words(n.props.children))).props.onClick();
  assert.match(ui.render('it'), /Profilo originale 158,50/); assert.equal(ui.calls.filter(c => c[0] === 'favAdd').length, 1);
});
