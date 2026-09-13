const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react'), JSX = require('react/jsx-runtime');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');
ambienteBrowser();
globalThis.window = { addEventListener() {}, removeEventListener() {} };
Object.assign(document, { addEventListener() {}, removeEventListener() {} });
function retained(mode = 'trade') {
  let si = 0, mi = 0, ei = 0, ri = 0;
  const states = [], memos = [], effects = [], deps = [], refs = [], nodes = [], calls = [];
  const remember = (fn, d) => { const at = mi++, before = memos[at]; if (!before || !d || d.some((v, i) => !Object.is(v, before.d[i]))) memos[at] = { d, value: fn() }; return memos[at].value; };
  const jsx = name => (type, props, ...args) => { nodes.push({ type, props }); return JSX[name](type, props, ...args); };
  const api = { portfolio: async () => ({ positions: [], cash: 10000 }), trades: async () => ({ trades: [] }),
    fx: async () => ({ rates: { EUR: 1 } }), cashMovements: async () => ({ movements: [] }), decisions: async () => ({ decisions: [] }),
    openingPositions: async () => ({ openings: [] }),
    previewTrade: async body => { calls.push(['trade', body]); throw new Error('Synthetic preview captured'); },
    logCashMovement: async body => { calls.push(['cash', body]); throw new Error('Synthetic cash captured; no write'); },
    previewOpeningPosition: async body => { calls.push(['opening', body]); throw new Error('Synthetic opening preview captured'); },
  };
  const load = creaCaricatore({ stub: { react: { ...React, useMemo: remember, useCallback: (fn, d) => remember(() => fn, d),
    useRef(initial) { return refs[ri++] ||= { current: initial }; },
    useState(initial) { const at = si++; if (!(at in states)) states[at] = typeof initial === 'function' ? initial() : initial; return [states[at], v => { states[at] = typeof v === 'function' ? v(states[at]) : v; }]; },
    useEffect(fn, d) { const at = ei++, before = deps[at]; if (!before || !d || d.some((v, i) => !Object.is(v, before[i]))) effects.push(fn); deps[at] = d; },
  }, 'react/jsx-runtime': { ...JSX, jsx: jsx('jsx'), jsxs: jsx('jsxs') }, '@/lib/api': { Bellomberg: api },
  'react-router-dom': { useSearchParams: () => [new URLSearchParams(mode === 'opening' ? 'mode=opening' : '')] } } });
  const language = load('i18n/lingua.ts'), Page = load('pages/TradeEntryPage.tsx').default;
  const render = lang => { si = mi = ei = ri = 0; effects.length = nodes.length = 0; language.impostaLinguaCorrente(lang); return renderToStaticMarkup(React.createElement(Page)); };
  const settle = async () => { for (const fn of effects.splice(0)) fn(); await new Promise(resolve => setImmediate(resolve)); };
  const ready = async lang => { render(lang); await settle(); return render(lang); };
  const field = id => nodes.find(n => n.type === 'input' && n.props.id === id);
  const type = (id, value, lang) => { assert.ok(field(id), id); field(id).props.onChange({ target: { value } }); return render(lang); };
  const submit = async index => { await nodes.filter(n => n.type === 'form')[index].props.onSubmit({ preventDefault() {} }); await settle(); };
  return { ready, render, field, type, submit, calls, nodes };
}

test('trade quantity and price preserve independently captured grammar after a language switch and further editing', async () => {
  for (const [initial, raw, next] of [['it', '1.234,50', 'en'], ['en', '1,234.50', 'it']]) {
    const ui = retained(); await ui.ready(initial); ui.type('f7-tk', 'SYNTH.X', initial);
    ui.type('f7-qt', raw, initial); ui.render(next); ui.type('f7-qt', raw.replace('50', '75'), next);
    ui.type('f7-pz', next === 'en' ? '1,234.50' : '1.234,50', next);
    await ui.submit(0); const sent = ui.calls.find(c => c[0] === 'trade'); assert.ok(sent);
    assert.equal(sent[1].quantita, 1234.75); assert.equal(sent[1].prezzo, 1234.5); assert.equal(sent[1].action, 'BUY');
  }
});

test('cash submission retains the typed amount, and clearing starts a field in the newly selected language', async () => {
  const ui = retained(); await ui.ready('it'); ui.type('f7-mv-imp', '1.234,50', 'it'); ui.render('en');
  ui.type('f7-mv-imp', '1.234,75', 'en'); await ui.submit(1);
  assert.equal(ui.calls[0]?.[0], 'cash'); assert.equal(ui.calls[0]?.[1].importo_eur, 1234.75);
  ui.type('f7-mv-imp', '', 'en'); ui.type('f7-mv-imp', '1,234.50', 'en'); await ui.submit(1);
  assert.equal(ui.calls[1]?.[1].importo_eur, 1234.5);
});

test('opening balance freezes each numeric field grammar while preserving zero cost and original provenance', async () => {
  const ui = retained('opening'); await ui.ready('it'); ui.type('f7-op-ticker', 'SYNTH.X', 'it');
  ui.type('f7-op-qty', '1.234,50', 'it'); ui.render('en'); ui.type('f7-op-qty', '1.234,75', 'en');
  ui.type('f7-op-cost', '0', 'en'); ui.type('f7-op-day', '2020-01-02', 'en'); ui.type('f7-op-source', 'Original source 158,50', 'en');
  await ui.submit(0); const sent = ui.calls.find(c => c[0] === 'opening'); assert.ok(sent);
  assert.equal(sent[1].quantita, 1234.75); assert.equal(sent[1].prezzo_medio, 0); assert.equal(sent[1].provenienza, 'Original source 158,50');
  assert.equal(sent[1].as_of, '2020-01-02'); assert.equal('action' in sent[1], false);
});

test('ambiguous drafts remain blocked and their diagnosis follows the interface language', async () => {
  const ui = retained(); await ui.ready('it'); ui.type('f7-qt', '1.234', 'it'); const en = ui.render('en');
  assert.match(en, /ambiguous/); await ui.submit(0); assert.equal(ui.calls.length, 0);
  const it = ui.render('it'); assert.match(it, /ambiguo/); assert.equal(ui.field('f7-qt').props.value, '1.234');
});
