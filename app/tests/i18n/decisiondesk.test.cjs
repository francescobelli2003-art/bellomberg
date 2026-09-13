const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const JSX = require('react/jsx-runtime');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');
ambienteBrowser();
globalThis.window = { confirm: () => true };
const decision = extra => ({ id: 123, memo_id: 1, timestamp: '2026-09-12T10:00:00', action: 'BUY', ticker: 'SYNTH.X',
  eur_amount: 0, timing: 'Original timing', confidence: '77', rationale: 'Rationale storico originale', status: 'PENDING',
  pm_feedback: 'Feedback originale', outcome_pct: 0, outcome_eur: 0, outcome_notes: 'Nota storica originale', archived: false, ...extra });

function retained(options = {}) {
  let si = 0, mi = 0, ei = 0;
  const values = {}, memos = [], effects = [], deps = [], nodes = [], calls = [];
  const data = options.data || [decision()];
  const api = { decisions: async (...args) => { calls.push(['read', ...args]); if (options.readError) throw options.readError; return { decisions: data }; },
    updateDecision: async (id, body) => { calls.push(['update', id, body]); if (options.writeError) throw options.writeError; return { success: true }; },
    setDecisionArchive: async (...args) => { calls.push(['archive', ...args]); return { success: true }; },
    vetoDecision: async (...args) => { calls.push(['veto', ...args]); return { success: true }; },
    revokeDecisionVeto: async (...args) => { calls.push(['revoke', ...args]); return { success: true }; },
    addDecisionNote: async (...args) => { calls.push(['note', ...args]); return { success: true }; },
  };
  const remember = (fn, d) => { const at = mi++, before = memos[at]; if (!before || !d || d.some((v, i) => !Object.is(v, before.d[i]))) memos[at] = { d, value: fn() }; return memos[at].value; };
  const jsx = name => (type, props, ...args) => { nodes.push({ type, props }); return JSX[name](type, props, ...args); };
  const load = creaCaricatore({ stub: {
    react: { ...React, useMemo: remember, useCallback: (fn, d) => remember(() => fn, d),
      useState(initial) { const at = si++; if (!(at in values)) values[at] = typeof initial === 'function' ? initial() : initial; return [values[at], v => { values[at] = typeof v === 'function' ? v(values[at]) : v; }]; },
      useEffect(fn, d) { const at = ei++, before = deps[at]; if (!before || !d || d.some((v, i) => !Object.is(v, before[i]))) effects.push(fn); deps[at] = d; },
    },
    'react/jsx-runtime': { ...JSX, jsx: jsx('jsx'), jsxs: jsx('jsxs') },
    '@/lib/api': { Bellomberg: api },
    'react-router-dom': { useNavigate: () => route => calls.push(['navigate', route]) },
  } });
  const language = load('i18n/lingua.ts'), Page = load('pages/Decisions.tsx').default;
  const render = lang => { si = mi = ei = 0; effects.length = nodes.length = 0; language.impostaLinguaCorrente(lang); return renderToStaticMarkup(React.createElement(Page)); };
  render.effects = async () => { for (const fn of effects) fn(); await new Promise(resolve => setImmediate(resolve)); };
  const ready = async lang => { render(lang); await render.effects(); return render(lang); };
  const expand = lang => { nodes.find(n => n.type === 'tr' && n.props.onClick).props.onClick(); return render(lang); };
  const decimals = () => nodes.filter(n => n.type === 'input' && n.props.inputMode === 'decimal');
  const execute = () => nodes.find(n => n.type === 'button' && n.props.className.includes('bg-emerald/20')).props.onClick();
  return { render, ready, expand, nodes, calls, decimals, execute, data };
}

test('decision labels are reactive while archived prose, stable states, zero amount and request count are preserved', async () => {
  const ui = retained(); await ui.ready('it'); const it = ui.expand('it'), en = ui.render('en'); await ui.render.effects();
  assert.match(it, /Decisioni operative/); assert.match(en, /Operational decisions/);
  for (const html of [it, en]) {
    assert.match(html, /Rationale storico originale/); assert.match(html, /Feedback originale/); assert.match(html, /Nota storica originale/);
    assert.match(html, /Original timing/); assert.match(html, /SYNTH.X/); assert.match(html, /77/);
  }
  assert.match(it, /0,00\u00a0€/); assert.match(en, /€0\.00/);
  assert.equal(ui.calls.length, 1); assert.deepEqual(ui.calls[0], ['read', undefined, 500]);
  assert.equal(ui.data[0].status, 'PENDING');
});

// 13/09 (Claude Opus 5): frasi attese scritte qui, non lette dal catalogo sotto prova.
test('a missing conviction is declared in the language of the page', async () => {
  const ui = retained({ data: [decision({ confidence: null })] }); await ui.ready('it');
  const it = ui.expand('it'), en = ui.render('en');
  assert.match(it, /Convinzione: <span class="text-white">n\.d\.<\/span>/);
  assert.match(en, /Confidence: <span class="text-white">n\/a<\/span>/);
});

test('the same captured numeric grammar is used for outcomes submitted after an interface language change', async () => {
  for (const [inputLanguage, raw, opposite] of [['it', '1.234,50', 'en'], ['en', '1,234.50', 'it']]) {
    const ui = retained(); await ui.ready(inputLanguage); ui.expand(inputLanguage);
    ui.decimals()[1].props.onChange({ target: { value: raw } }); ui.render(inputLanguage);
    ui.render(opposite); assert.equal(ui.decimals()[1].props.value, raw);
    ui.decimals()[1].props.onChange({ target: { value: raw.replace(/50$/, '75') } }); ui.render(opposite);
    await ui.execute();
    assert.deepEqual(ui.calls.find(c => c[0] === 'update'), ['update', 123, { status: 'EXECUTED', outcome_eur: 1234.75 }]);
  }
});

test('ambiguous numeric outcomes keep blocking while their diagnosis follows the current language', async () => {
  const ui = retained(); await ui.ready('it'); ui.expand('it'); ui.decimals()[0].props.onChange({ target: { value: '1.234' } });
  ui.render('it'); const itTitle = ui.decimals()[0].props.title; ui.render('en'); const enTitle = ui.decimals()[0].props.title;
  assert.notEqual(itTitle, enTitle); assert.match(enTitle, /ambiguous/i);
  await ui.execute(); const en = ui.render('en'), it = ui.render('it');
  assert.match(en, /ambiguous/i); assert.match(it, /ambiguo/i);
  assert.equal(ui.calls.filter(c => c[0] === 'update').length, 0);
});

test('structured mutation failures remain red and keep source details after switching language', async () => {
  const ui = retained({ writeError: { response: { data: { detail: [{ msg: 'Original mutation failure' }] } } } });
  await ui.ready('it'); ui.expand('it'); await ui.execute(); const it = ui.render('it'), en = ui.render('en');
  for (const html of [it, en]) assert.match(html, /text-crimson[^>]*>[^<]*Original mutation failure/);
  assert.match(en, /Error:/); assert.match(it, /Errore:/);
  assert.equal(ui.calls.filter(c => c[0] === 'update').length, 1);
  assert.equal(ui.data[0].status, 'PENDING');
});

test('a read failure never claims that zero operational decisions were measured', async () => {
  const ui = retained({ readError: { response: { data: { detail: [{ msg: 'Original read failure' }] } } } });
  const en = await ui.ready('en'), it = ui.render('it');
  assert.match(en, /DECISIONS UNAVAILABLE/); assert.match(it, /DECISIONI NON DISPONIBILI/);
  for (const html of [it, en]) { assert.match(html, /Original read failure/); assert.doesNotMatch(html, /0 active|0 attive/); }
});

const words = node => node == null ? '' : Array.isArray(node) ? node.map(words).join(' ') : typeof node === 'object' ? words(node.props?.children) : String(node);
test('translated status controls still send the exact canonical transitions and feedback', async () => {
  for (const [lang, labels] of [['it', ['ESEGUITA', 'PARZIALE', 'NON ESEGUITA', 'SCADUTA']], ['en', ['EXECUTED', 'PARTIAL', 'SKIPPED', 'EXPIRED']]]) {
    const ui = retained(); await ui.ready(lang); ui.expand(lang);
    ui.nodes.find(n => n.type === 'input' && n.props.placeholder.includes('feedback')).props.onChange({ target: { value: ' Original feedback 158,50 ' } });
    ui.render(lang);
    for (const label of labels) {
      const button = ui.nodes.find(n => n.type === 'button' && words(n.props.children).trim() === label && n.props.className.includes('disabled:opacity-50'));
      assert.ok(button, label); await button.props.onClick(); ui.render(lang);
    }
    assert.deepEqual(ui.calls.filter(c => c[0] === 'update').map(c => c[2]), ['EXECUTED', 'PARTIAL', 'SKIPPED', 'EXPIRED'].map(status => ({ status, pm_feedback: 'Original feedback 158,50' })));
  }
});

test('veto reasons and research note drafts remain original, with unchanged backend actions', async () => {
  const ui = retained(); await ui.ready('it'); ui.expand('it');
  ui.nodes.find(n => n.type === 'input' && n.props.placeholder === 'motivo obbligatorio per il veto').props.onChange({ target: { value: ' Original veto 158,50 ' } });
  ui.render('en');
  const veto = ui.nodes.find(n => n.type === 'button' && n.props.title?.includes('sets SKIPPED'));
  assert.equal(veto.props.disabled, false); await veto.props.onClick();
  assert.deepEqual(ui.calls.find(c => c[0] === 'veto'), ['veto', 123, 'Original veto 158,50']);

  const research = retained({ data: [decision({ action: 'RESEARCH', notes: [{ id: 8, autore: 'PM', testo: 'Nota PM originale', timestamp: '2026-09-12T10:00:00' }] })] });
  await research.ready('it');
  research.nodes.find(n => n.type === 'input').props.onChange({ target: { value: ' Original research note 158,50 ' } });
  const html = research.render('en'); assert.match(html, /Nota PM originale/);
  const send = research.nodes.find(n => n.type === 'button' && words(n.props.children).trim() === 'SEND');
  assert.equal(send.props.disabled, false); await send.props.onClick();
  assert.deepEqual(research.calls.find(c => c[0] === 'note'), ['note', 123, 'Original research note 158,50']);
});

test('compatibility archive placement is explicitly labelled without changing its rule', async () => {
  const record = decision({ timestamp: '2020-01-01T10:00:00' }); delete record.archived;
  const ui = retained({ data: [record] }); const en = await ui.ready('en'), it = ui.render('it');
  assert.match(en, /Archive placement estimated for 1 rows/); assert.match(it, /Posizionamento stimato per 1 righe/);
  assert.match(en, /Operational archive \(1\)/); assert.match(it, /Archivio operative \(1\)/);
  assert.equal(ui.calls.length, 1);
});
