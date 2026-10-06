// Pagina Decisioni (Nuova, 05/10/2026): stesse garanzie della versione F10 riscritte sulla nuova struttura
// (elenco + dettaglio, blocco «Chiudi la decisione», veto con conferma). I selettori sono i data-dc-*.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const JSX = require('react/jsx-runtime');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');
ambienteBrowser();
globalThis.window = { confirm: () => true };
const recent = new Date(Date.now() - 2 * 86400_000).toISOString().slice(0, 19);
const decision = extra => ({ id: 123, memo_id: 1, timestamp: recent, action: 'BUY', ticker: 'SYNTH.X',
  eur_amount: 0, timing: 'Original timing', confidence: '77', rationale: 'Rationale storico originale', status: 'PENDING',
  pm_feedback: 'Feedback originale', outcome_pct: 0, outcome_eur: 0, outcome_notes: 'Nota storica originale', archived: false, ...extra });

function retained(options = {}) {
  let si = 0, mi = 0, ei = 0;
  const values = {}, memos = [], effects = [], deps = [], nodes = [], calls = [];
  const data = options.data || [decision()];
  const api = { decisions: async (...args) => { calls.push(['read', ...args]); if (options.readError) throw options.readError; return { decisions: data.map(x => ({ ...x })) }; },
    updateDecision: async (id, body) => { calls.push(['update', id, body]); if (options.writeError) throw options.writeError; return { success: true }; },
    setDecisionArchive: async (...args) => { calls.push(['archive', ...args]); return { success: true }; },
    vetoDecision: async (...args) => { calls.push(['veto', ...args]); return { success: true }; },
    revokeDecisionVeto: async (...args) => { calls.push(['revoke', ...args]); return { success: true }; },
    addDecisionNote: async (...args) => { calls.push(['note', ...args]); return { success: true }; },
    decisionEvents: async id => { calls.push(['events', id]); return { events: options.events || [] }; },
    marketLogos: async () => ({ logos: {}, motivi: {} }),
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
  const ready = async lang => { render(lang); await render.effects(); render(lang); await render.effects(); return render(lang); };
  const find = pred => nodes.find(n => n.props && pred(n.props, n.type));
  const field = name => find(p => p['data-dc-campo'] === name);
  const action = name => find(p => p['data-dc-azione'] === name);
  const outcome = status => find(p => p['data-dc-esito'] === status);
  const confirm = () => action('conferma').props.onClick();
  return { render, ready, nodes, calls, data, find, field, action, outcome, confirm };
}

test('labels are reactive while archived prose, stable states and the single read are preserved', async () => {
  const ui = retained(); const it = await ui.ready('it'), en = ui.render('en'); await ui.render.effects();
  assert.match(it, /Proposte del Comitato/); assert.match(en, /Committee proposals/);
  assert.match(it, /Chiudi la decisione/); assert.match(en, /Close the decision/);
  for (const html of [it, en]) {
    assert.match(html, /Rationale storico originale/); assert.match(html, /Feedback originale/); assert.match(html, /Nota storica originale/);
    assert.match(html, /Original timing/); assert.match(html, /SYNTH\.X/); assert.match(html, /77/);
  }
  assert.match(it, /0 €/); assert.match(en, /€0/);
  const reads = ui.calls.filter(c => c[0] === 'read');
  assert.equal(reads.length, 1); assert.deepEqual(reads[0], ['read', undefined, 500]);
  assert.deepEqual(ui.calls.filter(c => c[0] === 'events'), [['events', 123]], 'the timeline is read once for the open proposal');
  assert.equal(ui.data[0].status, 'PENDING');
});

test('a missing conviction is declared in the language of the page', async () => {
  const ui = retained({ data: [decision({ confidence: null })] });
  const it = await ui.ready('it'), en = ui.render('en');
  assert.match(it, /Convinzione<\/span><span class="v">n\.d\.<\/span>/);
  assert.match(en, /Confidence<\/span><span class="v">n\/a<\/span>/);
});

test('the same captured numeric grammar is used for outcomes submitted after an interface language change', async () => {
  for (const [inputLanguage, raw, opposite] of [['it', '1.234,50', 'en'], ['en', '1,234.50', 'it']]) {
    const ui = retained(); await ui.ready(inputLanguage);
    ui.field('eur').props.onChange({ target: { value: raw } }); ui.render(inputLanguage);
    ui.render(opposite); assert.equal(ui.field('eur').props.value, raw);
    ui.field('eur').props.onChange({ target: { value: raw.replace(/50$/, '75') } }); ui.render(opposite);
    await ui.confirm();
    assert.deepEqual(ui.calls.find(c => c[0] === 'update'), ['update', 123, { status: 'EXECUTED', outcome_eur: 1234.75 }]);
  }
});

test('an outcome draft never lands on another decision when the detail moves by itself', async () => {
  // 05/10 (Claude Opus 5.5), review of PR #6: after «Archive» the detail fell back to the next row and
  // kept the draft, so Confirm wrote the outcome typed for #123 on #124.
  const ui = retained({ data: [decision(), decision({ id: 124, ticker: 'SYNTH.Y' })] }); await ui.ready('it');
  ui.find(p => p['data-dc-sel'] === 123).props.onClick(); ui.render('it');
  assert.equal(ui.find(p => p['data-dc-dettaglio'] !== undefined).props['data-dc-dettaglio'], 123, 'fixture: #123 is shown');
  ui.field('pct').props.onChange({ target: { value: '5' } }); ui.field('feedback').props.onChange({ target: { value: 'Synthetic feedback' } }); ui.render('it');
  ui.data[0].archived = true;
  await ui.action('archivia').props.onClick(); ui.render('it'); await ui.render.effects(); ui.render('it');
  assert.equal(ui.find(p => p['data-dc-dettaglio'] !== undefined).props['data-dc-dettaglio'], 124, 'fixture: the detail moved to #124');
  assert.equal(ui.field('pct').props.value, '', 'the draft of #123 is not shown on #124');
  await ui.confirm();
  const writes = ui.calls.filter(c => c[0] === 'update');
  assert.ok(writes.every(c => c[2].outcome_pct === undefined && c[2].pm_feedback === undefined), 'nothing typed for #123 reaches #124: ' + JSON.stringify(writes));
});

test('veto and revoke dialogs start on Cancel, not on the dangerous button', async () => {
  // 05/10 (Claude Opus 5.5): the focus started on the veto button, so two Enter presses applied an eternal veto.
  const ui = retained(); await ui.ready('it');
  ui.field('veto').props.onChange({ target: { value: 'Synthetic reason' } }); ui.render('it');
  ui.action('veto').props.onClick(); ui.render('it');
  assert.ok(ui.find(p => p['data-dc-dialogo'] === 'veto'), 'fixture: the veto dialog is open');
  assert.equal(ui.action('annulla-dialogo').props.autoFocus, true, 'Cancel takes the focus');
  assert.ok(!ui.action('ok-dialogo').props.autoFocus, 'the veto button does not');
});

test('ambiguous numeric outcomes keep blocking while their diagnosis follows the current language', async () => {
  const ui = retained(); await ui.ready('it'); ui.field('pct').props.onChange({ target: { value: '1.234' } });
  ui.render('it'); const itTitle = ui.field('pct').props.title; ui.render('en'); const enTitle = ui.field('pct').props.title;
  assert.notEqual(itTitle, enTitle); assert.match(enTitle, /ambiguous/i);
  await ui.confirm(); const en = ui.render('en'), it = ui.render('it');
  assert.match(en, /data-dc-avviso="errore"[^]*ambiguous/i); assert.match(it, /data-dc-avviso="errore"[^]*ambiguo/i);
  assert.equal(ui.calls.filter(c => c[0] === 'update').length, 0);
});

test('structured mutation failures stay as errors and keep source details after switching language', async () => {
  const ui = retained({ writeError: { response: { data: { detail: [{ msg: 'Original mutation failure' }] } } } });
  await ui.ready('it'); await ui.confirm(); const it = ui.render('it'), en = ui.render('en');
  for (const html of [it, en]) assert.match(html, /data-dc-avviso="errore"[^]*Original mutation failure/);
  assert.match(en, /Error:/); assert.match(it, /Errore:/);
  assert.equal(ui.calls.filter(c => c[0] === 'update').length, 1);
  assert.equal(ui.data[0].status, 'PENDING');
});

test('a read failure never claims that zero decisions were measured', async () => {
  const ui = retained({ readError: { response: { data: { detail: [{ msg: 'Original read failure' }] } } } });
  const en = await ui.ready('en'), it = ui.render('it');
  assert.match(en, /Decisions unavailable/); assert.match(it, /Decisioni non disponibili/);
  for (const html of [it, en]) {
    assert.match(html, /Original read failure/);
    assert.doesNotMatch(html, /data-dc-conta="todo">0</);
    assert.match(html, /data-dc-conta="todo">n[./]/);
  }
});

const words = node => node == null ? '' : Array.isArray(node) ? node.map(words).join(' ') : typeof node === 'object' ? words(node.props?.children) : String(node);
test('translated outcome choices still send the exact canonical transitions and feedback', async () => {
  for (const [lang, labels] of [['it', ['Eseguita', 'Parziale', 'Non eseguita', 'Scaduta']], ['en', ['Executed', 'Partial', 'Skipped', 'Expired']]]) {
    const ui = retained(); await ui.ready(lang);
    ui.field('feedback').props.onChange({ target: { value: ' Original feedback 158,50 ' } });
    ui.render(lang);
    for (const [i, status] of ['EXECUTED', 'PARTIAL', 'SKIPPED', 'EXPIRED'].entries()) {
      ui.field('feedback').props.onChange({ target: { value: ' Original feedback 158,50 ' } }); ui.render(lang);
      const choice = ui.outcome(status);
      assert.equal(words(choice.props.children).trim(), labels[i]);
      choice.props.onClick(); ui.render(lang);
      assert.equal(ui.outcome(status).props['aria-checked'], true);
      await ui.confirm(); ui.render(lang);
    }
    assert.deepEqual(ui.calls.filter(c => c[0] === 'update').map(c => c[2]), ['EXECUTED', 'PARTIAL', 'SKIPPED', 'EXPIRED'].map(status => ({ status, pm_feedback: 'Original feedback 158,50' })));
  }
});

test('a proposal blocked by the risk check cannot be marked executed', async () => {
  const ui = retained({ data: [decision({ assessment_status: 'BLOCKED', assessment_reason: 'limite di settore' })] });
  const html = await ui.ready('it');
  assert.match(html, /Bloccate dal controllo rischio/); assert.match(html, /limite di settore/);
  assert.equal(ui.outcome('EXECUTED').props.disabled, true); assert.equal(ui.outcome('PARTIAL').props.disabled, true);
  await ui.confirm();
  assert.deepEqual(ui.calls.find(c => c[0] === 'update'), ['update', 123, { status: 'SKIPPED' }]);
});

test('veto reasons and research note drafts remain original, with unchanged backend actions', async () => {
  const ui = retained(); await ui.ready('it');
  assert.equal(ui.action('veto').props.disabled, true, 'no reason, no veto');
  ui.field('veto').props.onChange({ target: { value: ' Original veto 158,50 ' } });
  ui.render('en');
  const veto = ui.action('veto'); assert.equal(veto.props.disabled, false);
  veto.props.onClick(); const dialog = ui.render('en');
  assert.match(dialog, /Apply the permanent veto\?/); assert.match(dialog, /Original veto 158,50/);
  assert.equal(ui.calls.filter(c => c[0] === 'veto').length, 0, 'the veto waits for confirmation');
  await ui.action('ok-dialogo').props.onClick();
  assert.deepEqual(ui.calls.find(c => c[0] === 'veto'), ['veto', 123, 'Original veto 158,50']);

  const research = retained({ data: [decision({ action: 'RESEARCH', notes: [{ id: 8, autore: 'PM', testo: 'Nota PM originale', timestamp: recent }] })] });
  await research.ready('it');
  research.find(p => p.onClick && p['aria-pressed'] !== undefined && /In ricerca/.test(words(p.children))).props.onClick();
  research.render('it'); await research.render.effects();
  research.field('nota').props.onChange({ target: { value: ' Original research note 158,50 ' } });
  const html = research.render('en'); assert.match(html, /Nota PM originale/);
  const send = research.action('invia');
  assert.equal(words(send.props.children).trim(), 'Send');
  assert.equal(send.props.disabled, false); await send.props.onClick();
  assert.deepEqual(research.calls.find(c => c[0] === 'note'), ['note', 123, 'Original research note 158,50']);
});

test('research close and reopen keep their status semantics', async () => {
  const open = retained({ data: [decision({ action: 'RESEARCH' })] }); await open.ready('it');
  open.find(p => p.onClick && p['aria-pressed'] !== undefined && /In ricerca/.test(words(p.children))).props.onClick(); open.render('it');
  await open.action('ricerca-chiudi').props.onClick();
  assert.deepEqual(open.calls.find(c => c[0] === 'update'), ['update', 123, { status: 'EXPIRED' }]);
  const archived = retained({ data: [decision({ action: 'RESEARCH', status: 'EXPIRED', archived: true, archive_override: 1 })] }); await archived.ready('it');
  archived.find(p => p.onClick && p['aria-pressed'] !== undefined && /Archivio/.test(words(p.children))).props.onClick(); archived.render('it');
  archived.find(p => p.onClick && p['aria-pressed'] !== undefined && /Ricerche/.test(words(p.children))).props.onClick(); archived.render('it');
  await archived.action('ricerca-ripristina').props.onClick();
  assert.deepEqual(archived.calls.filter(c => c[0] === 'update' || c[0] === 'archive'), [['update', 123, { status: 'PENDING' }], ['archive', 123, null]]);
});

test('a research without a date and a timeline event without states declare n/a, never «0 days» or blanks', async () => {
  // 05/10 (Claude Opus 5.5), review of PR #6: a missing timestamp printed «da 0 g» and a missing
  // from/to status or trade id printed an empty slot in the timeline.
  const ui = retained({ data: [decision({ action: 'RESEARCH', timestamp: null })],
    events: [{ id: 1, decision_id: 123, event_type: 'STATUS_CHANGED', from_status: null, to_status: 'EXPIRED', created_at: recent, details: {} },
      { id: 2, decision_id: 123, event_type: 'TRADE_LINKED', created_at: recent, details: {} }] });
  await ui.ready('it');
  ui.find(p => p.onClick && p['aria-pressed'] !== undefined && /In ricerca/.test(words(p.children))).props.onClick();
  const html = ui.render('it'); await ui.render.effects(); const after = ui.render('it');
  assert.doesNotMatch(html + after, /da 0 g/, 'no «0 days» for an unknown date');
  assert.match(after, /n\.d\./);
  assert.doesNotMatch(after, /Stato:\s+→|n\.\s*(<|$)/, 'no blank state or trade id in the timeline');
});

test('compatibility archive placement is explicitly labelled without changing its rule', async () => {
  const record = decision({ timestamp: '2020-01-01T10:00:00' }); delete record.archived;
  const ui = retained({ data: [record] }); const en = await ui.ready('en'), it = ui.render('it');
  assert.match(en, /Archive placement estimated for 1 rows/); assert.match(it, /Posizionamento stimato per 1 righe/);
  assert.match(en, /data-dc-conta="arch">1</); assert.match(it, /data-dc-conta="arch">1</);
  assert.equal(ui.calls.filter(c => c[0] === 'read').length, 1);
});

test('hold confirmations are grouped and confirmed one by one, minus the excluded ones', async () => {
  const holds = [1, 2, 3].map(i => decision({ id: 200 + i, action: 'HOLD', ticker: 'HLD' + i, eur_amount: 0 }));
  const ui = retained({ data: holds }); const html = await ui.ready('it');
  assert.match(html, /Mantieni · 3 titoli/);
  ui.action('hold-tutte').props.onClick(); ui.render('it');
  ui.find(p => p.title === 'Escludi HLD2').props.onClick(); const dialog = ui.render('it');
  assert.match(dialog, /Confermare 2 posizioni come eseguite\?/);
  await ui.action('ok-dialogo').props.onClick();
  assert.deepEqual(ui.calls.filter(c => c[0] === 'update'), [['update', 203, { status: 'EXECUTED' }], ['update', 201, { status: 'EXECUTED' }]]);
});

test('archived operative decisions keep Close, Veto and Link trade; archived research does not', async () => {
  // 06/10 (PM): a late outcome is recorded from the Archive without «Bring back» first, as before the restyle.
  const apriArchivio = (ui, tipo) => {
    ui.find(p => p.onClick && p['aria-pressed'] !== undefined && /Archivio/.test(words(p.children))).props.onClick(); ui.render('it');
    if (tipo) { ui.find(p => p.onClick && p['aria-pressed'] !== undefined && tipo.test(words(p.children))).props.onClick(); ui.render('it'); }
  };
  const op = retained({ data: [decision({ status: 'EXECUTED', archived: true })] }); await op.ready('it');
  apriArchivio(op);
  assert.equal(op.find(p => p['data-dc-dettaglio'] !== undefined).props['data-dc-dettaglio'], 123, 'fixture: the archived proposal is open');
  for (const name of ['conferma', 'veto', 'collega', 'ripristina']) assert.ok(op.action(name), `${name} is offered in the archive`);
  assert.equal(op.action('riapri'), undefined, 'no reopen from the archive: it would hide a pending proposal');
  op.field('pct').props.onChange({ target: { value: '12' } }); op.render('it');
  await op.confirm();
  assert.deepEqual(op.calls.find(c => c[0] === 'update'), ['update', 123, { status: 'EXECUTED', outcome_pct: 12 }]);
  assert.equal(op.calls.filter(c => c[0] === 'read').length, 2, 'the page reloads after the write, as for active decisions');
  op.render('it');
  op.field('veto').props.onChange({ target: { value: 'Motivo sintetico' } }); op.render('it');
  op.action('veto').props.onClick(); op.render('it');
  await op.action('ok-dialogo').props.onClick();
  assert.deepEqual(op.calls.find(c => c[0] === 'veto'), ['veto', 123, 'Motivo sintetico']);
  op.render('it');
  op.action('collega').props.onClick();
  assert.deepEqual(op.calls.find(c => c[0] === 'navigate'), ['navigate', '/trades?decision=123']);

  const res = retained({ data: [decision({ action: 'RESEARCH', status: 'EXPIRED', archived: true })] }); await res.ready('it');
  apriArchivio(res, /Ricerche/);
  assert.equal(res.find(p => p['data-dc-dettaglio'] !== undefined).props['data-dc-dettaglio'], 123, 'fixture: the archived research is open');
  assert.ok(res.action('ricerca-ripristina'), 'fixture: research restore is there');
  for (const name of ['conferma', 'veto', 'collega']) assert.equal(res.action(name), undefined, `${name} is not offered on research`);
});
