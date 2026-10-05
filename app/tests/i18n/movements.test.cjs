const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const JSX = require('react/jsx-runtime');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');
ambienteBrowser();
const trade = extra => ({ id: 1, ticker: 'SYNTH.X', action: 'BUY', data: '2026-09-12T12:00:00', quantita: 2, prezzo: 1234.5,
  valuta: 'USD', pm_rationale: 'Motivo originale 158,50', note: 'Nota PM originale', realized_eur: 0, ora_convenzionale: true,
  link_origin: 'none', created_at: '2026-09-12T10:00:00', ...extra });
const cash = { id: 2, date: '2026-09-11', type: 'DEPOSIT', amount_eur: 0.25, note: 'Causale originale 158,50' };
const words = n => n == null ? '' : Array.isArray(n) ? n.map(words).join(' ') : typeof n === 'object' ? words(n.props?.children) : String(n);
function retained(options = {}) {
  let si = 0, mi = 0, ei = 0, ri = 0;
  const values = {}, memos = [], effects = [], deps = [], refs = [], nodes = [], calls = [];
  const remember = (fn, d) => { const at = mi++, before = memos[at]; if (!before || !d || d.some((v, i) => !Object.is(v, before.d[i]))) memos[at] = { d, value: fn() }; return memos[at].value; };
  const jsx = name => (type, props, ...args) => { nodes.push({ type, props }); return JSX[name](type, props, ...args); };
  const load = creaCaricatore({ stub: {
    react: { ...React, useMemo: remember, useCallback: (fn, d) => remember(() => fn, d),
      useRef(initial) { const at = ri++; return refs[at] ||= { current: initial }; },
      useState(initial) { const at = si++; if (!(at in values)) values[at] = typeof initial === 'function' ? initial() : initial; return [values[at], v => { values[at] = typeof v === 'function' ? v(values[at]) : v; }]; },
      useEffect(fn, d) { const at = ei++, before = deps[at]; if (!before || !d || d.some((v, i) => !Object.is(v, before[i]))) effects.push(fn); deps[at] = d; },
    }, 'react/jsx-runtime': { ...JSX, jsx: jsx('jsx'), jsxs: jsx('jsxs') },
    '@/lib/api': { Bellomberg: {
      trades: async limit => { calls.push(['trades', limit]); if (options.tradeError) throw options.tradeError; return options.tradePayload ?? { trades: options.trades ?? [trade()] }; },
      cashMovements: async limit => { calls.push(['cash', limit]); if (options.cashError) throw options.cashError; return options.cashPayload ?? { movements: options.cash ?? [cash] }; },
      marketLogos: async () => ({ logos: {}, motivi: {} }),
    } },
  } });
  const language = load('i18n/lingua.ts'), Page = load('pages/MovementsPage.tsx').default, helper = load('lib/movimenti.ts');
  const render = lang => { si = mi = ei = ri = 0; effects.length = nodes.length = 0; language.impostaLinguaCorrente(lang); return renderToStaticMarkup(React.createElement(Page)); };
  const settle = async () => { for (const fn of effects.splice(0)) fn(); await new Promise(resolve => setImmediate(resolve)); };
  const ready = async lang => { render(lang); await settle(); return render(lang); };
  // i controlli si cercano per attributo data-mov-*: identità stabile in entrambe le lingue
  const press = (lang, attr, value) => { const n = nodes.find(x => x.type === 'button' && (value == null ? attr in x.props : x.props[attr] === value));
    assert.ok(n, `button ${attr}=${value} not rendered`); n.props.onClick(); return render(lang); };
  const pressed = (attr, value) => nodes.find(x => x.type === 'button' && x.props[attr] === value)?.props['aria-pressed'];
  const refresh = async lang => { press(lang, 'data-mov-azione', 'aggiorna'); await settle(); return render(lang); };
  return { render, ready, settle, press, pressed, refresh, nodes, calls, helper, options };
}

test('the single register page speaks both languages, keeps archived words verbatim and reads each archive once', async () => {
  const ui = retained(); const it = await ui.ready('it'), en = ui.render('en'); await ui.settle();
  assert.match(it, /Registro/); assert.match(en, /Register/);
  assert.match(it, /Attività per mese/); assert.match(en, /Activity by month/);
  assert.match(it, /Settembre 2026/); assert.match(en, /September 2026/);
  assert.match(en, /1 on securities \+ 1 cash/); assert.match(it, /1 sui titoli \+ 1 di cassa/);
  assert.doesNotMatch(en, /1 days|1 movements\b/);
  // la riga scelta di default è il trade: commento completo (motivo e nota) nel dettaglio, la causale nel registro
  for (const html of [it, en]) { assert.match(html, /Motivo originale 158,50/); assert.match(html, /Nota PM originale/); assert.match(html, /Causale originale 158,50/); assert.match(html, /SYNTH.X/); }
  assert.match(en, /Security history/); assert.match(en, /conventional/); assert.match(it, /Storia del titolo/);
  assert.deepEqual(ui.calls, [['trades', 100], ['cash', 200]]);
});

test('a first cash archive failure remains visible next to valid securities, including a structured source detail', async () => {
  const ui = retained({ cashError: { response: { data: { detail: [{ msg: 'Original cash failure' }] } } } });
  const it = await ui.ready('it'), en = ui.render('en');
  for (const html of [it, en]) { assert.match(html, /Original cash failure/); assert.match(html, /SYNTH.X/); }
  assert.match(en, /Cash archive not read/); assert.match(it, /Archivio cassa non letto/);
  assert.match(en, /1 on securities · cash not read/); assert.match(it, /1 sui titoli · cassa non letta/);
  assert.match(en, /Cash flows n\/a/); assert.match(it, /Flussi di cassa n\.d\./);
  assert.doesNotMatch(en, /cash is fresh|Cash rows from the last successful read/i);
  const cashOnly = retained({ tradeError: new Error('Securities archive unavailable') });
  assert.match(await cashOnly.ready('en'), /1 cash · securities not read/);
  assert.match(cashOnly.render('it'), /1 di cassa · titoli non letti/);
  assert.match(cashOnly.render('en'), /Securities archive not read: realized P&amp;L is unknown/);
});

test('empty transport details and malformed HTTP 200 arrays are declared failures in either language', async () => {
  for (const opts of [{ tradeError: { response: { data: { detail: '' } }, message: '' }, cash: [] }, { tradePayload: { trades: [null] }, cash: [] }]) {
    const ui = retained(opts); const en = await ui.ready('en'), it = ui.render('it');
    assert.match(en, /Incomplete history/); assert.match(it, /Storico incompleto/);
    assert.match(en, /Cash read: no movements/); assert.match(it, /Cassa letta: nessun movimento/);
    assert.doesNotMatch(en, /No movements recorded|Loading movements/);
    assert.doesNotMatch(it, /Nessun movimento registrato|Caricamento dei movimenti/);
  }
});

test('successful empty archives measure zero without claiming an absent realized field', async () => {
  const ui = retained({ trades: [], cash: [] }); const en = await ui.ready('en'), it = ui.render('it');
  assert.match(en, /No movements recorded/); assert.match(it, /Nessun movimento registrato/);
  assert.match(en, /No securities row to observe/); assert.doesNotMatch(en, /does not deliver realized/);
  assert.equal(ui.helper.contaRealizzato([]), null);
});

test('a failed refresh keeps previous rows and distinguishes stale archives from ones never read', async () => {
  const ui = retained({ cashError: new Error('Cash unavailable') }); await ui.ready('it');
  ui.options.tradeError = new Error('Securities refresh failed');
  const en = await ui.refresh('en'), it = ui.render('it');
  for (const html of [it, en]) { assert.match(html, /Securities refresh failed/); assert.match(html, /Cash unavailable/); assert.match(html, /SYNTH.X/); }
  assert.match(en, /Securities rows from the last successful read may be stale/); assert.match(en, /Cash archive not read/);
  assert.doesNotMatch(en, /cash is fresh/i); assert.equal(ui.calls.length, 4);
});

test('numeric lane geometry, native currency scaling, cash flows, realized zero and provenance retain their contracts in IT and EN', async () => {
  const rows = [trade(), trade({ id: 3, data: '2026-09-10T12:00:00', prezzo: 100, valuta: 'EUR', action: 'TRIM', realized_eur: -3 }),
    trade({ id: 4, data: '2026-09-11T12:00:00', prezzo: 50, valuta: 'EUR', action: 'ADD', realized_eur: null })];
  const ui = retained({ trades: rows }); await ui.ready('it'); const h = ui.helper, arco = h.arcoDi(rows);
  const it = { lanes: h.costruisciCorsie(rows, arco), flows: h.contaFlussi([cash]), realized: h.contaRealizzato(rows) };
  const monthsIt = h.raggruppaPerMese(h.fondiRegistro(rows, [cash])); const timeIt = h.oraTrade(rows[0]);
  ui.render('en'); const en = { lanes: h.costruisciCorsie(rows, arco), flows: h.contaFlussi([cash]), realized: h.contaRealizzato(rows) };
  assert.deepEqual(en, it); assert.equal(en.flows.netto, 0.25); assert.equal(en.realized.somma, -3);
  assert.deepEqual(en.lanes[0].mosse.map(x => x.rel), [1, 0.5, 1]);
  assert.equal(h.ggmmaa('2026-09-12'), '09/12/26'); assert.notEqual(h.oraTrade(rows[0]), timeIt);
  assert.match(h.oraTrade(rows[0]), /conventional/); assert.equal(h.oraTrade({ ...rows[0], ora_convenzionale: false }), '12:00:00');
  assert.match(h.legameMovimento(rows[0]), /Manual, without a decision/);
  assert.equal(h.raggruppaPerMese(h.fondiRegistro(rows, [cash]))[0].chiave, monthsIt[0].chiave);
  assert.equal(h.segnoPL(0), 'pari');
});

test('cash and comment filters retain canonical identities and the selected row after switching language', async () => {
  const ui = retained(); await ui.ready('it');
  const cashIt = ui.press('it', 'data-mov-filtro', 'CASSA');
  assert.match(cashIt, /Causale originale 158,50/); assert.doesNotMatch(cashIt, /SYNTH.X/);
  const cashEn = ui.render('en'); assert.match(cashEn, /Causale originale 158,50/); assert.doesNotMatch(cashEn, /SYNTH.X/);
  assert.equal(ui.pressed('data-mov-filtro', 'CASSA'), true);
  // con la cassa filtrata il dettaglio segue la prima riga visibile: la causale, non il commento del trade
  assert.doesNotMatch(cashEn, /Nota PM originale/);
  const all = ui.press('en', 'data-mov-filtro', 'TUTTI'); assert.match(all, /Motivo originale 158,50/); assert.match(all, /Causale originale 158,50/);
  const comment = ui.press('it', 'data-mov-filtro', 'COMMENTO'); assert.match(comment, /Motivo originale 158,50/);
  assert.equal(ui.pressed('data-mov-filtro', 'COMMENTO'), true);
  const back = ui.render('en'); assert.equal(ui.pressed('data-mov-filtro', 'COMMENTO'), true); assert.match(back, /Nota PM originale/);
  assert.equal(ui.calls.length, 2);
});

test('a month bar filters the register and the security picked from realized drops cash rows, without another read', async () => {
  const rows = [trade(), trade({ id: 3, ticker: 'EXIT.X', data: '2026-08-10T12:00:00', prezzo: 100, valuta: 'EUR', action: 'TRIM', realized_eur: 12, pm_rationale: 'Uscita agosto' })];
  const ui = retained({ trades: rows }); const it = await ui.ready('it');
  assert.match(it, /Uscita agosto/);
  const aug = ui.press('it', 'data-mov-mese', '2026-08');
  assert.match(aug, /Uscita agosto/); assert.doesNotMatch(aug, /Causale originale 158,50/);
  assert.equal(ui.pressed('data-mov-mese', '2026-08'), true);
  ui.press('en', 'data-mov-togli', 'mese');
  const picked = ui.press('en', 'data-mov-titolo', 'EXIT.X');
  assert.match(picked, /Uscita agosto/); assert.doesNotMatch(picked, /Causale originale 158,50/);
  assert.match(picked, /Clear the security filter EXIT.X/);
  assert.equal(ui.calls.length, 2);
});

// 13/09 (Claude Opus 5): il chip filtro mostrava il codice grezzo DIVIDEND anche in IT. Dal 05/10 (stile Nuova)
// tutti i filtri parlano la lingua della pagina; i verbi BUY, TRIM e ADD restano codici sulle pillole delle righe.
test('the dividend filter chip speaks the page language while its filter identity stays canonical', async () => {
  const ui = retained({ trades: [trade(), trade({ id: 5, action: 'DIVIDEND', quantita: 0, prezzo: 0, pm_rationale: 'Dividendo originale' })] });
  const chips = () => ui.nodes.filter(n => n.type === 'button' && 'data-mov-filtro' in n.props).map(n => words(n.props.children).replace(/\s+/g, ' ').trim());
  await ui.ready('it'); const it = chips();
  assert.ok(it.includes('Dividendi 1'), it.join(' | ')); assert.ok(!it.some(c => /^DIVIDEND/.test(c)));
  assert.ok(it.includes('Acquisti 1') && it.includes('Aggiunte 0') && it.includes('Uscite 0'), it.join(' | '));
  ui.render('en'); const en = chips(); assert.ok(en.includes('Dividends 1'), en.join(' | '));
  const html = ui.press('it', 'data-mov-filtro', 'DIVIDEND'); assert.match(html, /Dividendo originale/); assert.doesNotMatch(html, /Motivo originale 158,50/);
  assert.equal(ui.pressed('data-mov-filtro', 'DIVIDEND'), true);
  assert.match(html, /data-mov-verbo="DIVIDEND"/);
  assert.equal(ui.calls.length, 2);
});

test('an obsolete response cannot overwrite newer successful archive reads or stop their loading indicator', async () => {
  const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
  const firstT = deferred(), firstC = deferred(), newerT = deferred(), newerC = deferred();
  const ui = retained({ tradePayload: firstT.promise, cashPayload: firstC.promise });
  await ui.ready('it'); ui.options.tradePayload = newerT.promise; ui.options.cashPayload = newerC.promise;
  await ui.refresh('en');
  firstT.resolve({ trades: [trade({ ticker: 'OBSOLETE.X' })] }); firstC.resolve({ movements: [cash] }); await ui.settle();
  const waiting = ui.render('en'); assert.match(waiting, /Loading movements/); assert.doesNotMatch(waiting, /OBSOLETE.X/);
  newerT.resolve({ trades: [trade()] }); newerC.resolve({ movements: [cash] }); await ui.settle();
  const fresh = ui.render('en'); assert.match(fresh, /SYNTH.X/); assert.doesNotMatch(fresh, /Loading movements|OBSOLETE.X/);
  assert.equal(ui.calls.length, 4);
});
