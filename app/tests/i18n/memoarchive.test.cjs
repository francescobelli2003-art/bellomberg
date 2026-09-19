const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const JSX = require('react/jsx-runtime');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');
ambienteBrowser();
globalThis.window = { addEventListener() {}, removeEventListener() {} };

const markdown = header => `# Original title\n## ACTION TABLE\n| ${header} | Ticker | EUR | Timing | Conviction |\n|---|---|---|---|---|\n| BUY | SYNTH.X | 1.234,50 | Original timing | 77 |\n## Original section\nTesto storico italiano [src: original_tool] #123.\n`;
const memo = (id = 1) => ({ id, timestamp: '2026-09-12T10:00:00', title: 'Titolo storico italiano', portfolio_nav_eur: 0, has_content: true,
  pdf_available: false, appendix_available: false, dcf_files: '[]', capo_tokens_in: 0, capo_tokens_out: 0, output_language: 'it' });
function retained(options = {}) {
  let state = 0, mi = 0, ei = 0, ri = 0;
  const values = {}, memos = [], effects = [], deps = [], refs = [], calls = [], nodes = [], cleanups = [];
  const response = options.memo || memo();
  const api = {
    memos: async limit => { calls.push(['memos', limit]); if (options.error) throw options.error; return options.listResponse || { memos: options.list || [response] }; },
    memosAll: async limit => { calls.push(['all', limit]); return { memos: options.all || [response] }; },
    decisions: async (...args) => { calls.push(['decisions', ...args]); return { decisions: [{ id: 123, memo_id: 1, ticker: 'SYNTH.X', action: 'BUY', status: 'EXECUTED', outcome_pct: 0 }] }; },
    memoById: async id => { calls.push(['detail', id]); if (options.detail) return options.detail(id); return { ...response, full_markdown: markdown(options.header || 'Azione') }; },
    memosSearch: async (q, n) => { calls.push(['search', q, n]); return { results: [{ chunk_id: 'synthetic', memo_id: 1, content: 'Passo storico originale', distance: 0.1234 }] }; },
  };
  const remember = (fn, d) => { const at = mi++, before = memos[at]; if (!before || !d || d.some((v, i) => !Object.is(v, before.d[i]))) memos[at] = { d, value: fn() }; return memos[at].value; };
  const jsx = name => (type, props, ...args) => { nodes.push({ type, props }); return JSX[name](type, props, ...args); };
  const load = creaCaricatore({ stub: {
    react: { ...React, useMemo: remember, useCallback: (fn, d) => remember(() => fn, d),
      useState(initial) { const at = state++; if (!(at in values)) values[at] = typeof initial === 'function' ? initial() : initial; return [values[at], v => { values[at] = typeof v === 'function' ? v(values[at]) : v; }]; },
      useRef(initial) { const at = ri++; return refs[at] ||= { current: initial }; },
      useEffect(fn, d) { const at = ei++, before = deps[at]; if (!before || !d || d.some((v, i) => !Object.is(v, before[i]))) effects.push(() => { cleanups[at]?.(); cleanups[at] = fn(); }); deps[at] = d; },
    },
    'react/jsx-runtime': { ...JSX, jsx: jsx('jsx'), jsxs: jsx('jsxs') },
    '@/lib/api': { Bellomberg: api, API_BASE: 'http://synthetic.invalid' },
    '@/lib/useBox': { useBox: () => [{ current: null }, { w: 900, h: 104 }] },
  } });
  const language = load('i18n/lingua.ts'), Page = load('pages/MemoArchive.tsx').default;
  const render = lang => { state = mi = ei = ri = 0; effects.length = nodes.length = 0; language.impostaLinguaCorrente(lang); return renderToStaticMarkup(React.createElement(Page)); };
  render.effects = async () => { for (const fn of effects) fn(); await new Promise(resolve => setImmediate(resolve)); };
  const ready = async lang => { render(lang); await render.effects(); render(lang); await render.effects(); return render(lang); };
  return { render, ready, calls, nodes, response };
}

test('memo language is reactive while historical prose, actions, citations, zero NAV and requests remain unchanged', async () => {
  const { render, ready, calls } = retained();
  const it = await ready('it'), count = calls.length, en = render('en'); await render.effects();
  assert.match(it, /Archivio del comitato/); assert.match(en, /Committee archive/);
  assert.match(en, /Original output · Italian/);
  assert.match(en, /source declared by the memo/); assert.match(it, /fonte dichiarata dal memo/);
  assert.match(it, /0\u00a0€/); assert.match(en, /€0/);
  for (const html of [it, en]) {
    assert.match(html, /Testo storico italiano/); assert.match(html, /Titolo storico italiano/);
    assert.match(html, /1\.234,50/); assert.match(html, /SYNTH.X/); assert.match(html, /Original timing/);
    assert.equal((html.match(/<td class="ac">/g) || []).length, 1);
    assert.match(html, /class="ex-EXECUTED"/);
  }
  assert.equal(calls.length, count);
});

test('Italian and English ACTION TABLE headers produce the same single historical action', async () => {
  for (const header of ['Azione', 'Action']) {
    const { ready } = retained({ header }); const html = await ready('en');
    assert.equal((html.match(/<td class="ac">/g) || []).length, 1);
    assert.match(html, /<td class="ac">BUY<\/td>/);
  }
});

test('archive gaps are counted from declared row evidence, not from unequal pagination limits', async () => {
  const all = Array.from({ length: 55 }, (_, i) => memo(i + 1));
  all.push({ ...memo(90), has_content: false, pdf_available: false }, { ...memo(91), has_content: false, pdf_available: true });
  const { ready, render } = retained({ all }); const en = await ready('en'), it = render('it');
  assert.match(en, /1 row without readable content/); assert.match(it, /1 riga senza contenuto leggibile/);
  assert.match(en, /57 sampled rows/); assert.doesNotMatch(en, /56 failed/);
});

test('action and archive row counts use singular only for one while preserving historical markdown', async () => {
  for (const count of [0, 1, 2]) {
    const row = '| BUY | SYNTH.X | 1.234,50 | Original timing | 77 |';
    const body = markdown('Action').replace(row, Array(count).fill(row).join('\n'));
    const all = Array.from({ length: count }, (_, i) => ({ ...memo(i + 1), has_content: false }));
    const view = retained({ all, detail: async () => ({ ...memo(), full_markdown: body }) });
    const en = await view.ready('en'), it = view.render('it');
    assert.match(en, new RegExp(`${count} ${count === 1 ? 'row' : 'rows'} without readable content in ${count} sampled ${count === 1 ? 'row(?!s)' : 'rows'}`));
    assert.match(it, new RegExp(`${count} ${count === 1 ? 'riga' : 'righe'} senza contenuto leggibile su ${count} ${count === 1 ? 'riga' : 'righe'} del campione`));
    if (count) {
      assert.match(en, new RegExp(`ACTION TABLE — ${count} ${count === 1 ? 'ROW,' : 'ROWS,'}`));
      assert.match(it, new RegExp(`ACTION TABLE — ${count} ${count === 1 ? 'RIGA,' : 'RIGHE,'}`));
      for (const html of [it, en]) assert.match(html, /1\.234,50/);
    } else assert.match(en, /NO ACTION TABLE IN THIS MEMO/);
  }
});

test('saved memo amount is labelled as securities excluding cash, preserving finite zero and missing values', async () => {
  for (const value of [0, 5960, null]) {
    const record = { ...memo(), portfolio_nav_eur: value }, before = structuredClone(record);
    const view = retained({ memo: record }), en = await view.ready('en'), it = view.render('it');
    assert.match(en, /Securities at the time/); assert.match(it, /Investito al momento/);
    assert.match(en, /excludes cash/); assert.match(it, /cassa esclusa/);
    assert.doesNotMatch(en, /NAV at the time/); assert.doesNotMatch(it, /NAV alla data/);
    if (value === null) { assert.match(en, /Invested amount n\/a/); assert.match(it, /Investito n.d./); }
    else { assert.match(en, value === 0 ? /€0/ : /€5,960/); assert.match(it, value === 0 ? /0\u00a0€/ : /5\.960\u00a0€/); }
    assert.deepEqual(record, before);
  }
});

test('invalid DCF metadata and structured request failures are explicit in both languages', async () => {
  const broken = retained({ memo: { ...memo(), dcf_files: '{broken' } });
  const en = await broken.ready('en'), it = broken.render('it');
  assert.match(en, /DCF file metadata invalid/); assert.match(it, /Metadati dei file DCF non validi/);
  const error = retained({ error: { response: { data: { detail: [{ msg: 'Original failure evidence' }] } } } });
  const failureIt = await error.ready('it'), failureEn = error.render('en');
  for (const html of [failureIt, failureEn]) assert.match(html, /Original failure evidence/);
  assert.match(failureEn, /MEMO ARCHIVE UNAVAILABLE/);
});

test('semantic search draft and original results survive a language change without another search', async () => {
  const { ready, render, nodes, calls } = retained(); await ready('it');
  nodes.find(n => n.type === 'button' && n.props.className === 'qcall').props.onClick(); render('it');
  nodes.find(n => n.type === 'input').props.onChange({ target: { value: 'Domanda storica 158,50' } });
  render('en');
  const input = nodes.find(n => n.type === 'input'); assert.equal(input.props.value, 'Domanda storica 158,50');
  input.props.onKeyDown({ key: 'Enter', preventDefault() {} }); await new Promise(resolve => setImmediate(resolve));
  const en = render('en'), it = render('it');
  assert.match(en, /Passo storico originale/); assert.match(it, /Passo storico originale/);
  assert.match(en, /0\.1234/); assert.match(it, /0,1234/);
  assert.deepEqual(calls.filter(c => c[0] === 'search'), [['search', 'Domanda storica 158,50', 8]]);
});

test('undeclared sample evidence stays unavailable and historical language is not guessed', async () => {
  const record = { ...memo(), output_language: null }; delete record.has_content;
  const { ready, render } = retained({ memo: record }); const en = await ready('en'), it = render('it');
  assert.match(en, /Count n\/a: text or PDF availability is undeclared/);
  assert.match(it, /Conteggio n.d.: disponibilità del testo o PDF non dichiarata/);
  assert.match(en, /Original output · language unknown/);
  assert.doesNotMatch(en, /0 rows without readable content/);
});

test('malformed list responses are unavailable instead of an invitation to run another committee', async () => {
  const { ready, render } = retained({ listResponse: {} }); const en = await ready('en'), it = render('it');
  assert.match(en, /MEMO ARCHIVE UNAVAILABLE/); assert.match(it, /ARCHIVIO MEMO NON DISPONIBILE/);
  assert.doesNotMatch(en, /No memo yet/);
});

test('a text read failure cannot remain attached to a different cached memo', async () => {
  const { ready, render, nodes } = retained({ list: [memo(1), { ...memo(2), timestamp: '2026-09-11T10:00:00' }],
    detail: async id => { if (id === 2) throw new Error('Original second-memo failure'); return { ...memo(), full_markdown: markdown('Action') }; } });
  await ready('en');
  const select = id => nodes.find(n => n.type === 'div' && /^mr/.test(n.props.className || '') && n.props.children?.[0]?.props?.children === id).props.onClick();
  select(2); render('en'); await render.effects(); const failed = render('en');
  assert.match(failed, /Original second-memo failure/); assert.doesNotMatch(failed, /LOADING…/);
  select(1); render('en'); await render.effects(); const html = render('en');
  assert.match(html, /Testo storico italiano/); assert.doesNotMatch(html, /Original second-memo failure/);
});
