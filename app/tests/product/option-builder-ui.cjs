// Option builder, impianto «Ticket» (10/10/2026, Opus 5.5): il CABLAGGIO del componente vero, montato
// con React su un DOM minimo. Il motore e' sostituito da una risposta sintetica che dipende dalla
// richiesta (cosi' si vede che ogni comando arriva al motore e la cifra torna in pagina); dati sintetici.
// v2 (review avversariale 10/10): un caso per ogni mutazione del revisore (riga netta, etichette, avvisi,
// comandi del ticket, preset e scadenza, attesa annullata, DELAYED, editor, errori, finestra, segni).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const { creaCaricatore, ambienteBrowser } = require('../i18n/_carica.cjs');
const { installDom, dispatch } = require('./_mini-dom.cjs');

ambienteBrowser();
const load = creaCaricatore({ stub: { './api': { API_BASE: 'http://synthetic.invalid', requestHeaders: () => ({}) } } });
const languages = load('i18n/lingua.ts');
const ob = load('lib/option-builder.ts');
const OptionBuilder = load('components/option-builder/OptionBuilder.tsx').default;

const EXPIRY = '2035-01-19', EXPIRY2 = '2035-02-16';
const row = (type, strike, expiry, over = {}) => ({ contract: `O:SYNT${expiry.slice(2).replace(/-/g, '')}${type[0].toUpperCase()}${strike}`, type, strike, expiry, iv: .25,
  bid: 1, ask: 1.2, mid: 1.1, multiplier: 100, adjusted: false, quote_timestamp: '2035-01-09T15:00:00Z', quote_timeframe: 'DELAYED', quality: [], _source: 'synthetic', ...over });
// 11/10: the call 110 of the first expiry has no quote but has its IV (theoretical price); the call 90 has
// neither quote nor IV (n.d.: a manual price AND a manual IV are its only way into the engine)
const UNQUOTED = { bid: null, ask: null, mid: null };
const holeOf = (e, k) => e === EXPIRY && k === 110 ? UNQUOTED : e === EXPIRY && k === 90 ? { ...UNQUOTED, iv: null } : {};
const chainOf = (e, over = {}) => [90, 95, 100, 105, 110].flatMap(k => [row('call', k, e, { ...holeOf(e, k), ...over }), row('put', k, e, over)]);
const download = { id: 'job-synt', state: 'complete', spot: 100, spot_source: 'Polygon (synthetic)', snapshot_at: '2035-01-09T15:00:00Z',
  rows: [EXPIRY, EXPIRY2].map(expiry => ({ expiry, n_contracts: 10, complete: true })) };
const pageOf = (e, over) => ({ chain: chainOf(e, over), spot: 100, spot_source: 'Polygon (synthetic)', spot_timestamp: '2035-01-09T15:00:00Z', _timestamp: '2035-01-09T15:00:00Z' });

/** Engine stand-in. Net premium = Σ side × premium × qty × multiplier (positive = paid), scenario P/L =
 *  10 × (scenario spot − spot) − 5 × days; extremes and breakevens are chosen by the test. */
function engine(body, o) {
  const point = (price, pnl) => ({ price, pnl, delta: .5, gamma: .01, vega: 10, theta: -2, rho: 1 });
  const net = body.legs.reduce((a, l) => a + (l.side === 'buy' ? 1 : -1) * l.premium * l.quantity * (l.multiplier ?? 1), 0);
  const scn = 10 * (body.scenario_spot - body.spot) - 5 * body.elapsed_days;
  const curve = Array.from({ length: 101 }, (_, i) => ({ price: 60 + i, expiry: (i - 40) * 10, today: (i - 40) * 9, scenario: (i - 40) * 9 }));
  const fees = o.fees || 0, cost = net + fees;
  return { currency: 'USD', model: 'BSM', greek_units: {}, entry_cost: cost, net_premium: net, fees,
    entry_kind: cost > 0 ? 'debit' : cost < 0 ? 'credit' : 'even', same_expiry: true, expiry_days: 10,
    breakevens: o.breakevens ?? [101.1], max_profit: o.maxProfit ?? 500, max_loss: o.unlimited ? null : o.maxLoss ?? 110,
    unlimited_profit: false, unlimited_loss: !!o.unlimited, unlimited_loss_reason: o.unlimited ? 'structural' : null,
    today: point(body.spot, 0), scenario: point(body.scenario_spot, scn), curve,
    heatmap: [{ elapsed_days: 0, cells: [point(90, -100), point(110, 400)] }], limits: ['synthetic limit'] };
}

const RATE = { value: .04, percent: 4, date: '2035-01-08', source: 'FRED DGS3MO', status: 'solid', error: null };

async function mount(o) {
  const dom = installDom();
  const bodies = [], deferred = {}, windowListeners = [];
  // the minimal window drops listeners: record them, so a test can deliver pointerup/pointercancel
  dom.window.addEventListener = (type, listener) => windowListeners.push({ type, listener });
  dom.window.removeEventListener = (type, listener) => { const i = windowListeners.findIndex(l => l.type === type && l.listener === listener); if (i >= 0) windowListeners.splice(i, 1); };
  const savedFetch = globalThis.fetch, savedRO = globalThis.ResizeObserver, savedNow = Date.now;
  if (o.now != null) Date.now = () => o.now; // v2: a fixed clock, so an oracle can be a literal
  globalThis.ResizeObserver = class { observe() {} disconnect() {} };
  globalThis.fetch = async (url, init) => {
    const path = new URL(url).pathname;
    if (path === '/options/strategy/rate') {
      if (o.rate === 'fail') return { ok: false, status: 503, json: async () => ({ detail: 'FRED sintetico KO' }) };
      return { ok: true, status: 200, json: async () => (o.rate === 'stale' ? { ...RATE, status: 'stale' } : RATE) };
    }
    if (path === '/options/strategy/simulate') {
      const body = JSON.parse(init.body); bodies.push(body);
      if (o.simulateFail) return { ok: false, status: 500, json: async () => ({ detail: 'motore sintetico KO' }) };
      return { ok: true, status: 200, json: async () => engine(body, o) };
    }
    throw new Error('unexpected request ' + path);
  };
  const fetchChain = async expiry => {
    if (o.chainFail) throw new Error('catena sintetica KO');
    if (o.deferExpiry === expiry) return new Promise(resolve => { deferred.resolve = () => resolve(pageOf(expiry, o.noQuotes ? UNQUOTED : {})); });
    return pageOf(expiry, o.noQuotes ? UNQUOTED : {});
  };
  const { createRoot } = require('react-dom/client');
  const container = dom.document.createElement('div'); dom.document.body.appendChild(container);
  const root = createRoot(container);
  const Harness = () => {
    const [state, setState] = React.useState(o.legs || []);
    return React.createElement(OptionBuilder, { ticker: 'SYNT', download, catalog: [EXPIRY, EXPIRY2], downloadBusy: false,
      fetchChain, requestExpiries() {}, legs: state, setLegs: setState });
  };
  const settle = () => React.act(async () => { for (let i = 0; i < 4; i++) await new Promise(r => setTimeout(r, 90)); });
  await React.act(async () => { root.render(React.createElement(Harness)); }); await settle();
  const view = { container, bodies, deferred, windowListeners, settle,
    fire: async (el, type, value) => { if (value !== undefined) el.value = value; await React.act(async () => { dispatch(container, el, type); }); await settle(); },
    one: pred => { const all = container.all(pred); assert.equal(all.length, 1, 'exactly one match'); return all[0]; },
    unmount: async () => { await React.act(async () => { root.unmount(); }); dom.restore(); globalThis.fetch = savedFetch; Date.now = savedNow;
      if (savedRO === undefined) delete globalThis.ResizeObserver; else globalThis.ResizeObserver = savedRO; } };
  view.text = pred => view.one(pred).textContent;
  return view;
}

const attr = (name, value) => el => el.getAttribute(name) === String(value);
const has = name => el => el.hasAttribute(name);
// 11/10: the strategy is a grid of buttons; the one on the ticket is aria-pressed
const strategyGlyph = view => view.container.all(el => el.hasAttribute('data-preset') && el.getAttribute('aria-pressed') === 'true').map(el => el.getAttribute('data-preset'));
const preset = (view, id) => view.one(attr('data-preset', id));

test('the details drawer is closed by default: greeks, contracts and method are not on the page until opened', async () => {
  languages.impostaLinguaCorrente('it');
  const view = await mount({ legs: [ob.optionLeg('call', 'buy', EXPIRY, 100)] });
  try {
    const drawer = view.one(has('data-details'));
    assert.equal(drawer.localName, 'details');
    assert.ok(!drawer.hasAttribute('open'), 'closed by default');
    assert.equal(drawer.byClass('ob-greeks').length, 0, 'greeks are not rendered while closed');
    assert.ok(!view.container.textContent.includes('O:SYNT350119C100'), 'contract codes stay in the drawer');
    assert.ok(drawer.textContent.includes('Dettagli'));
    // what changes the decision stays outside: the DELAYED quotes, declared AND highlighted
    const delay = view.one(attr('data-quote-delay', 'DELAYED'));
    assert.ok(delay.className.includes('is-warn'), 'delayed quotes are highlighted: ' + delay.className);
    drawer.open = true; drawer.setAttribute('open', '');
    // 'toggle' non risale: React lo ascolta sull'elemento stesso
    await React.act(async () => { for (const l of drawer.listeners.filter(x => x.type === 'toggle')) l.listener({ type: 'toggle', target: drawer, currentTarget: drawer,
      bubbles: false, preventDefault() {}, stopPropagation() {}, isTrusted: true, timeStamp: Date.now() }); });
    await view.settle();
    assert.equal(drawer.byClass('ob-greeks').length, 1, 'opening the drawer shows the greeks');
    assert.ok(drawer.textContent.includes('O:SYNT350119C100'), 'and the OCC contract code');
  } finally { await view.unmount(); }
});

test('the scenario sliders reach the engine and the scenario P/L shown is the engine answer', async () => {
  languages.impostaLinguaCorrente('it');
  const view = await mount({ legs: [ob.optionLeg('call', 'buy', EXPIRY, 100)] });
  try {
    const pnl = () => view.text(has('data-scenario-pnl'));
    assert.equal(view.bodies.at(-1).scenario_spot, 100);
    assert.ok(pnl().startsWith('0,00'), pnl());
    await view.fire(view.one(attr('data-slider', 'spot')), 'input', '3');
    assert.ok(Math.abs(view.bodies.at(-1).scenario_spot - 103) < 1e-9, 'spot +3% reaches the engine');
    assert.ok(pnl().startsWith('+30,00'), 'P/L = engine answer at 103: ' + pnl());
    await view.fire(view.one(attr('data-slider', 'days')), 'input', '2');
    assert.equal(view.bodies.at(-1).elapsed_days, 2);
    assert.ok(pnl().startsWith('+20,00'), pnl());
    await view.fire(view.one(attr('data-slider', 'iv')), 'input', '5');
    assert.ok(Math.abs(view.bodies.at(-1).iv_shift - .05) < 1e-12, 'the IV shift reaches the engine as a decimal');
    await view.fire(view.one(has('data-scenario-reset')), 'click');
    const last = view.bodies.at(-1);
    assert.deepEqual([last.scenario_spot, last.elapsed_days, last.iv_shift], [100, 0, 0]);
    assert.ok(pnl().startsWith('0,00'), 'reset: ' + pnl());
  } finally { await view.unmount(); }
});

test('an unlimited loss is written «Illimitata» in the key figures, never a number', async () => {
  for (const [language, word] of [['it', 'Illimitata'], ['en', 'Unlimited']]) {
    languages.impostaLinguaCorrente(language);
    const view = await mount({ legs: [ob.optionLeg('call', 'sell', EXPIRY, 100)], unlimited: true });
    try {
      const loss = view.one(attr('data-fig', 'max-loss'));
      assert.ok(loss.textContent.includes(word), language + ': ' + loss.textContent);
      assert.ok(!/\d/.test(loss.byClass('ob-fig-v')[0].textContent), 'no figure in place of an unbounded loss');
      assert.equal(view.container.all(el => el.getAttribute('data-tail-loss') === 'structural').length, 1, 'the chart marks the tail');
    } finally { await view.unmount(); }
  }
});

test('a bounded loss is a negative figure, never «Illimitata»', async () => {
  languages.impostaLinguaCorrente('it');
  const view = await mount({ legs: [ob.optionLeg('call', 'buy', EXPIRY, 100)] });
  try {
    const value = view.one(attr('data-fig', 'max-loss')).byClass('ob-fig-v')[0].textContent;
    assert.match(value, /^[-−]110,00/, 'max loss 110 is shown as a loss: ' + value);
    assert.ok(!value.includes('Illimitata'), value);
  } finally { await view.unmount(); }
});

test('the decimal comma is accepted in the assumptions and in the ticket (text fields, never type=number)', async () => {
  languages.impostaLinguaCorrente('it');
  const view = await mount({ legs: [ob.optionLeg('call', 'buy', EXPIRY, 100)] });
  try {
    assert.equal(view.container.all(el => el.localName === 'input' && el.getAttribute('type') === 'number').length, 0);
    await view.fire(view.one(el => el.localName === 'button' && el.className.includes('ob-link')), 'click');
    const inputs = view.one(el => el.className === 'ob-inputs').byTag('input');
    assert.equal(inputs.length, 4, 'spot, rate, dividend, commission');
    await view.fire(inputs[2], 'input', '1,5');
    assert.equal(view.bodies.at(-1).dividend_yield, .015, '1,5 % is 0.015, not 15');
    const price = view.one(el => el.localName === 'input' && el.className.includes('is-price'));
    await view.fire(price, 'input', '0,75');
    assert.equal(view.bodies.at(-1).legs[0].premium, .75, 'a manual price typed with a comma');
    assert.ok(price.className.includes('is-manual'), 'and it is declared manual');
  } finally { await view.unmount(); }
});

test('a leg without a quote is declared n.d. in the ticket and in the alert, and the engine is not called', async () => {
  languages.impostaLinguaCorrente('it');
  const view = await mount({ legs: [ob.optionLeg('call', 'buy', EXPIRY, 100), ob.optionLeg('put', 'buy', EXPIRY, 85)] });
  try {
    const nd = view.one(attr('data-leg-nd', 2));
    assert.ok(nd.textContent.includes('n.d.') && nd.textContent.includes('contratto assente'), nd.textContent);
    assert.ok(view.text(attr('role', 'alert')).includes('Gamba 2 n.d.'), 'the P/L card says which leg blocks the figures');
    assert.equal(view.bodies.length, 0, 'nothing is priced with a hole');
    assert.ok(view.one(attr('data-fig', 'max-loss')).textContent.includes('n.d.'));
  } finally { await view.unmount(); }
});

// ── v2 (review 10/10) ─────────────────────────────────────────────────────────────────────────

test('net line and entry figure carry the engine sign: buying is a debit, selling a credit', async () => {
  languages.impostaLinguaCorrente('it');
  for (const [side, net, entry] of [['buy', 'Debito netto', 'Debito pagato'], ['sell', 'Credito netto', 'Credito incassato']]) {
    const view = await mount({ legs: [ob.optionLeg('call', side, EXPIRY, 100)] });
    try {
      const line = view.text(has('data-net'));
      assert.ok(line.startsWith(net) && line.includes('110,00'), side + ': ' + line);
      assert.ok(view.text(attr('data-fig', 'entry')).startsWith(entry), side + ': ' + view.text(attr('data-fig', 'entry')));
    } finally { await view.unmount(); }
  }
});

test('point 1: the net line uses the price the engine used — a manual price on an unquoted leg is not n.d.', async () => {
  languages.impostaLinguaCorrente('it');
  const view = await mount({ legs: [ob.optionLeg('call', 'buy', EXPIRY, 90)] });
  try {
    assert.ok(view.text(has('data-net')).includes('n.d.'), 'no quote, no IV, no manual price: n.d.');
    assert.equal(view.bodies.length, 0);
    await view.fire(view.one(el => el.localName === 'input' && el.className.includes('is-iv')), 'input', '25');
    await view.fire(view.one(el => el.localName === 'input' && el.className.includes('is-price')), 'input', '0,75');
    assert.equal(view.bodies.at(-1).legs[0].premium, .75);
    const line = view.text(has('data-net'));
    assert.ok(line.startsWith('Debito netto') && line.includes('75,00') && !line.includes('n.d.'), line);
  } finally { await view.unmount(); }
});

test('quantity, strike and side edited in the ticket reach the engine', async () => {
  languages.impostaLinguaCorrente('it');
  const view = await mount({ legs: [ob.optionLeg('call', 'buy', EXPIRY, 100)] });
  try {
    await view.fire(view.one(el => el.localName === 'input' && el.className.includes('is-qty')), 'input', '2');
    assert.equal(view.bodies.at(-1).legs[0].quantity, 2);
    await view.fire(view.one(el => el.localName === 'select' && el.className.includes('is-strike')), 'change', '105');
    assert.equal(view.bodies.at(-1).legs[0].strike, 105);
    await view.fire(view.one(el => el.localName === 'button' && el.className.includes('ob-side')), 'click');
    assert.equal(view.bodies.at(-1).legs[0].side, 'sell');
  } finally { await view.unmount(); }
});

test('a preset follows the ticket expiry; an edit makes it «Personalizzata»', async () => {
  languages.impostaLinguaCorrente('it');
  const view = await mount({});
  try {
    await view.fire(preset(view, 'long_call'), 'click');
    const near = view.bodies.at(-1).legs;
    assert.deepEqual(near.map(l => [l.type, l.side, l.strike]), [['call', 'buy', 100]]);
    assert.deepEqual(strategyGlyph(view), ['long_call']);
    await view.fire(view.one(has('data-expiry')), 'change', EXPIRY2);
    const far = view.bodies.at(-1).legs;
    assert.ok(far[0].days > near[0].days + 20, `rebuilt on the new expiry: ${near[0].days} → ${far[0].days}`);
    assert.deepEqual(strategyGlyph(view), ['long_call'], 'still the preset');
    assert.ok(view.text(has('data-pl-sub')).includes('feb'), 'the P/L header names the legs expiry: ' + view.text(has('data-pl-sub')));
    await view.fire(view.one(el => el.localName === 'input' && el.className.includes('is-qty')), 'input', '2');
    assert.deepEqual(strategyGlyph(view), [], 'an edited preset is no longer the preset');
    assert.ok(view.text(has('data-strategy-name')).startsWith('Personalizzata'), 'the ticket says Personalizzata: ' + view.text(has('data-strategy-name')));
  } finally { await view.unmount(); }
});

test('point 5: an edit made while a preset waits for its chain cancels the preset (the edit is kept)', async () => {
  languages.impostaLinguaCorrente('it');
  const view = await mount({ deferExpiry: EXPIRY2 });
  try {
    await view.fire(preset(view, 'long_call'), 'click');
    const nearDays = view.bodies.at(-1).legs[0].days;
    await view.fire(view.one(has('data-expiry')), 'change', EXPIRY2);
    assert.ok(view.deferred.resolve, 'the far chain is in flight');
    await view.fire(view.one(el => el.localName === 'input' && el.className.includes('is-qty')), 'input', '3');
    await React.act(async () => { view.deferred.resolve(); });
    await view.settle();
    const last = view.bodies.at(-1).legs;
    assert.equal(last.length, 1); assert.equal(last[0].quantity, 3, 'the user edit survives');
    assert.equal(last[0].days, nearDays, 'the preset was not rebuilt over it');
    assert.deepEqual(strategyGlyph(view), [], 'and the select does not claim the preset');
    const notice = view.text(el => el.className === 'ob-notice');
    assert.ok(notice.includes('annullata: hai modificato le gambe'), 'the cancellation is said: ' + notice);
  } finally { await view.unmount(); }
});

test('point 4: legs on two expiries are «scadenze miste» in the P/L header', async () => {
  languages.impostaLinguaCorrente('it');
  const view = await mount({ legs: [ob.optionLeg('call', 'sell', EXPIRY, 100), ob.optionLeg('call', 'buy', EXPIRY2, 100)] });
  try { assert.ok(view.text(has('data-pl-sub')).includes('scadenze miste'), view.text(has('data-pl-sub'))); } finally { await view.unmount(); }
});

test('a missing rate opens the assumptions editor by itself and is declared', async () => {
  languages.impostaLinguaCorrente('it');
  const view = await mount({ legs: [ob.optionLeg('call', 'buy', EXPIRY, 100)], rate: 'fail' });
  try {
    assert.equal(view.container.all(el => el.className === 'ob-inputs').length, 1, 'editor open without clicking «Modifica»');
    assert.ok(view.text(attr('role', 'alert')).includes('Tasso'), view.text(attr('role', 'alert')));
    assert.equal(view.bodies.length, 0, 'no engine call without a rate');
  } finally { await view.unmount(); }
});

test('point 6: a stale rate says «non aggiornato» in the compact row', async () => {
  languages.impostaLinguaCorrente('it');
  const view = await mount({ legs: [ob.optionLeg('call', 'buy', EXPIRY, 100)], rate: 'stale' });
  try { assert.equal(view.text(has('data-rate-stale')), 'non aggiornato'); } finally { await view.unmount(); }
});

test('engine and chain errors are visible on the page', async () => {
  languages.impostaLinguaCorrente('it');
  let view = await mount({ legs: [ob.optionLeg('call', 'buy', EXPIRY, 100)], simulateFail: true });
  try { assert.ok(view.text(attr('role', 'alert')).includes('motore sintetico KO')); } finally { await view.unmount(); }
  view = await mount({ legs: [ob.optionLeg('call', 'buy', EXPIRY, 100)], chainFail: true });
  try {
    const badges = view.container.all(el => el.className.includes('ob-badge') && el.className.includes('is-bad')).map(el => el.textContent);
    assert.ok(badges.some(t => t.includes('catena sintetica KO')), badges.join(' | '));
  } finally { await view.unmount(); }
});

test('the chart window always contains the breakevens', async () => {
  languages.impostaLinguaCorrente('it');
  const view = await mount({ legs: [ob.optionLeg('call', 'buy', EXPIRY, 100)], breakevens: [130] });
  try {
    const marks = view.container.all(attr('data-mark', 'be'));
    assert.equal(marks.length, 1, 'breakeven 130 is drawn although the strike is 100');
    assert.ok(marks[0].textContent.includes('130'));
  } finally { await view.unmount(); }
});

// ── v3 (re-review 10/10) ──────────────────────────────────────────────────────────────────────

test('with fees the net line is the premium (fees excluded, said) while the entry figure includes them', async () => {
  languages.impostaLinguaCorrente('it');
  // sold call: premium −110 (credit), fees 150 → entry cost +40 (debit): the two lines legitimately differ in sign
  const view = await mount({ legs: [ob.optionLeg('call', 'sell', EXPIRY, 100)], fees: 150 });
  try {
    const line = view.text(has('data-net'));
    assert.ok(line.startsWith('Credito netto') && line.includes('110,00') && line.includes('commissioni escluse'), line);
    const entry = view.text(attr('data-fig', 'entry'));
    assert.ok(entry.startsWith('Debito pagato') && entry.includes('40,00'), entry);
  } finally { await view.unmount(); }
});

test('the P/L header names the legs expiry, not the ticket expiry', async () => {
  languages.impostaLinguaCorrente('it');
  const view = await mount({ legs: [ob.optionLeg('call', 'buy', EXPIRY2, 100)] });
  try {
    assert.equal(view.one(has('data-expiry')).byTag('option').find(op => op.selected)?.getAttribute('value') ?? view.one(has('data-expiry')).value, EXPIRY, 'the ticket stays on the first expiry');
    const sub = view.text(has('data-pl-sub'));
    assert.ok(sub.includes('feb') && !sub.includes('gen'), sub);
  } finally { await view.unmount(); }
});

const ticks = view => view.container.all(el => el.getAttribute('class') === 'ob-tick').map(el => el.textContent).join('|');

test('dragging the spot slider freezes the chart window; pointercancel or blur release it; an off-window scenario is an edge arrow', async () => {
  languages.impostaLinguaCorrente('it');
  const view = await mount({ legs: [ob.optionLeg('call', 'buy', EXPIRY, 100)] });
  try {
    const slider = view.one(attr('data-slider', 'spot'));
    const before = ticks(view);
    await React.act(async () => { dispatch(view.container, slider, 'pointerdown', { buttons: 1 }); });
    await view.fire(slider, 'input', '30');
    assert.ok(Math.abs(view.bodies.at(-1).scenario_spot - 130) < 1e-9);
    assert.equal(ticks(view), before, 'the axis does not move under the pointer');
    assert.equal(view.container.all(attr('data-scenario-edge', 'right')).length, 1, 'the scenario point is an arrow on the right edge');
    const cancel = view.windowListeners.filter(l => l.type === 'pointercancel');
    assert.equal(cancel.length, 1, 'pointercancel unlocks too');
    await React.act(async () => { cancel[0].listener({ type: 'pointercancel' }); }); await view.settle();
    assert.notEqual(ticks(view), before, 'released: the window re-centres on the scenario');
    assert.equal(view.container.all(has('data-scenario-edge')).length, 0);
    assert.equal(view.container.all(has('data-scenario-dot')).length, 1, 'and the point is back on the curve');
    assert.equal(view.windowListeners.length, 0, 'no listener left behind');
    // blur releases as well
    await view.fire(slider, 'input', '0');
    const centred = ticks(view);
    await React.act(async () => { dispatch(view.container, slider, 'pointerdown', { buttons: 1 }); });
    await view.fire(slider, 'input', '-30');
    assert.equal(ticks(view), centred, 'frozen again');
    await view.fire(slider, 'focusout');
    assert.notEqual(ticks(view), centred, 'blur releases the window');
  } finally { await view.unmount(); }
});

// ── 11/10/2026 (Opus 5.5): weekend download (bid/ask null on every contract, only IV) ─────────────

const theory = (type, strike, days) => ob.theoreticalPrice(type, 100, strike, days, .25, .04, 0);

test('no quotes: the empty builder starts from a declared example, priced at the theoretical price and labelled so', async () => {
  languages.impostaLinguaCorrente('it');
  const view = await mount({ noQuotes: true });
  try {
    const legs = view.bodies.at(-1).legs;
    assert.deepEqual(legs.map(l => [l.type, l.side, l.strike]), [['call', 'buy', 100], ['call', 'sell', 105]], 'bull call spread ATM');
    for (const l of legs) assert.ok(Math.abs(l.premium - theory('call', l.strike, l.days)) < 1e-9, `premium = BSM with the contract IV: ${l.premium}`);
    assert.equal(view.text(has('data-example')), 'Esempio');
    assert.ok(view.text(has('data-example-note')).includes('Esempio di partenza'));
    assert.deepEqual(strategyGlyph(view), ['bull_call']);
    assert.equal(view.container.all(has('data-price-theory')).length, 2, 'each leg says «teorico» under its price');
    assert.equal(view.one(has('data-net-basis')).getAttribute('data-net-basis'), 'theory');
    assert.ok(view.text(has('data-net')).includes('teorico · quote assenti') && !view.text(has('data-net')).includes('n.d.'), view.text(has('data-net')));
    assert.ok(view.text(has('data-no-quotes')).includes('Quote assenti'), 'the strip declares the missing quotes');
    assert.equal(view.container.all(attr('role', 'alert')).length, 0, 'nothing is n.d.');
    assert.ok(view.container.all(el => el.getAttribute('class') === 'ob-curve-expiry').length === 1, 'the payoff is drawn');
  } finally { await view.unmount(); }
});

test('no quotes: any strategy of the grid is selectable and computed; the example badge goes away', async () => {
  languages.impostaLinguaCorrente('it');
  const view = await mount({ noQuotes: true });
  try {
    const ids = view.container.all(has('data-preset')).map(el => el.getAttribute('data-preset'));
    assert.deepEqual(ids, ob.PRESET_GROUPS.flatMap(g => g.presets), 'every preset is a button, in its group');
    for (const id of ['straddle', 'iron_condor', 'long_put']) {
      const before = view.bodies.length;
      await view.fire(preset(view, id), 'click');
      assert.ok(view.bodies.length > before, id + ': the engine is called');
      assert.deepEqual(strategyGlyph(view), [id]);
      for (const l of view.bodies.at(-1).legs) assert.ok(Math.abs(l.premium - theory(l.type, l.strike, l.days)) < 1e-9, `${id}: theoretical premium`);
      assert.equal(view.container.all(has('data-example')).length, 0, 'a chosen strategy is not an example');
    }
  } finally { await view.unmount(); }
});

test('the expiry select is always on the page and no menu covers it: «Aggiungi call/put/azioni» are plain buttons', async () => {
  languages.impostaLinguaCorrente('it');
  const view = await mount({ legs: [ob.optionLeg('call', 'buy', EXPIRY, 100)] });
  try {
    const expiry = view.one(has('data-expiry'));
    assert.equal(expiry.localName, 'select');
    assert.ok(!expiry.closest?.('.ob-ticket') && view.container.all(el => el.className === 'ob-picker')[0].contains?.(expiry) !== false, 'the expiry lives in the picker, not in a popup');
    assert.deepEqual(expiry.byTag('option').map(op => op.getAttribute('value')), [EXPIRY, EXPIRY2]);
    assert.equal(view.container.all(attr('role', 'menu')).length, 0, 'no popup menu');
    const adds = view.container.all(has('data-add-leg'));
    assert.deepEqual(adds.map(b => [b.localName, b.getAttribute('data-add-leg'), b.textContent]), [['button', 'call', 'Aggiungi call'], ['button', 'put', 'Aggiungi put'], ['button', 'stock', 'Aggiungi azioni']]);
    await view.fire(adds[1], 'click');
    assert.equal(view.container.all(has('data-leg')).length, 2, 'the put is added at once');
    assert.equal(view.container.all(has('data-expiry')).length, 1, 'and the expiry select is still there');
    assert.equal(view.container.all(has('data-example')).length, 0, 'legs given by the user: no example over them');
  } finally { await view.unmount(); }
});

test('the example is applied once: an edit makes it the user\'s ticket, a cleared ticket stays empty', async () => {
  languages.impostaLinguaCorrente('it');
  const view = await mount({});
  try {
    assert.equal(view.container.all(has('data-example')).length, 1);
    await view.fire(view.container.all(el => el.localName === 'input' && el.className.includes('is-qty'))[0], 'input', '2');
    assert.equal(view.container.all(has('data-example')).length, 0, 'edited: no longer an example');
    await view.fire(view.one(attr('aria-label', 'Svuota')), 'click');
    assert.equal(view.container.all(has('data-leg')).length, 0, 'cleared');
    assert.equal(view.container.all(has('data-example')).length, 0, 'and the example does not come back');
  } finally { await view.unmount(); }
});

test('chart markers: strikes on the axis, breakevens with the % from spot, ±1σ/±2σ bands declared, extremes annotated', async () => {
  languages.impostaLinguaCorrente('it');
  // extremes inside the drawn range (the stand-in curve is 10 × (price − 100)); outside it they live in the key figures
  const view = await mount({ legs: [ob.optionLeg('call', 'buy', EXPIRY, 100), ob.optionLeg('call', 'sell', EXPIRY, 105)], maxProfit: 40, maxLoss: 20 });
  try {
    assert.deepEqual(view.container.all(has('data-strike-mark')).map(el => el.getAttribute('data-strike-mark')), ['100', '105']);
    assert.ok(view.container.all(has('data-strike-mark')).some(el => el.textContent === 'K100'));
    const be = view.one(attr('data-mark', 'be'));
    assert.ok(be.textContent.includes('101,10') && be.textContent.includes('(+1,1%)'), be.textContent);
    assert.equal(view.container.all(attr('data-mark', 'spot')).length, 1);
    assert.deepEqual(view.container.all(has('data-band')).map(el => el.getAttribute('data-band')).sort(), ['1', '2'], 'both bands drawn');
    assert.ok(view.text(has('data-band-legend')).includes('IV ATM 25,0%'), 'σ declared: ' + view.text(has('data-band-legend')));
    assert.ok(view.text(attr('data-extreme', 'max-profit')).includes('+40,00'), view.text(attr('data-extreme', 'max-profit')));
    assert.match(view.text(attr('data-extreme', 'max-loss')), /[-−]20,00/);
    assert.equal(view.one(has('data-loss-area')).getAttribute('data-loss-area'), 'bounded');
    assert.ok(view.container.all(has('data-y-tick')).some(el => el.textContent === '0'), 'zero on the y axis');
  } finally { await view.unmount(); }
  const naked = await mount({ legs: [ob.optionLeg('call', 'sell', EXPIRY, 100)], unlimited: true });
  try { assert.equal(naked.one(has('data-loss-area')).getAttribute('data-loss-area'), 'unlimited', 'the unbounded loss has its own tint'); }
  finally { await naked.unmount(); }
});

test('crosshair from the keyboard: exact engine values and the probability of ending above; + zooms, «Adatta» restores', async () => {
  languages.impostaLinguaCorrente('it');
  // clock fixed exactly 10 days before the 16:00 New York close of EXPIRY, so the oracle is a literal
  const view = await mount({ legs: [ob.optionLeg('call', 'buy', EXPIRY, 100)], now: Date.parse('2035-01-09T21:00:00Z') });
  try {
    const svg = view.one(attr('role', 'img'));
    await React.act(async () => { dispatch(view.container, svg, 'keydown', { key: 'ArrowRight' }); }); await view.settle();
    const tip = view.one(has('data-tip'));
    const price = Number(tip.byTag('b')[0].textContent.replace(/\./g, '').replace(',', '.'));
    assert.ok(price > 100 && price < 102, 'one step right of spot: ' + price);
    // engine stand-in: curve point at price p has expiry (p − 100) × 10
    assert.ok(view.text(has('data-tip-expiry')).startsWith('+' + ((price - 100) * 10).toFixed(2).replace('.', ',')), 'expiry value of the engine row: ' + view.text(has('data-tip-expiry')));
    assert.equal(price, 101);
    // independent oracle: scipy.stats.norm.cdf(d2), S 100, X 101, σ .25 (ATM IV), T 10/365, r .04, q 0 → 0.40723345812292056
    assert.equal(view.text(has('data-tip-above')), '40,7%');
    assert.ok(view.text(has('data-tip-basis')).includes('IV ATM 25,0%') && view.text(has('data-tip-basis')).includes('drift r−q'), view.text(has('data-tip-basis')));
    // the scenario IV shock moves σ: +5 pt → σ .30 → scipy 0.41951181597645293
    await view.fire(view.one(attr('data-slider', 'iv')), 'input', '5');
    await React.act(async () => { dispatch(view.container, svg, 'keydown', { key: 'ArrowRight' }); }); await view.settle();
    await React.act(async () => { dispatch(view.container, svg, 'keydown', { key: 'ArrowLeft' }); }); await view.settle();
    assert.equal(view.text(has('data-tip-above')), '42,0%', 'σ includes the IV shock');
    assert.ok(view.text(has('data-band-legend')).includes('shock +5,0'), view.text(has('data-band-legend')));
    assert.equal(view.container.all(has('data-fit')).length, 0, 'no «Adatta» before a zoom');
    await React.act(async () => { dispatch(view.container, svg, 'keydown', { key: '+' }); }); await view.settle();
    assert.ok(svg.hasAttribute('data-zoomed'), 'zoomed in');
    await view.fire(view.one(has('data-fit')), 'click');
    assert.ok(!svg.hasAttribute('data-zoomed'), '«Adatta» restores the first window');
  } finally { await view.unmount(); }
});

// ── v2 (review 9fd3c56) ─────────────────────────────────────────────────────────────────────────

const openDrawer = async view => {
  const drawer = view.one(has('data-details'));
  drawer.open = true; drawer.setAttribute('open', '');
  await React.act(async () => { for (const l of drawer.listeners.filter(x => x.type === 'toggle')) l.listener({ type: 'toggle', target: drawer, currentTarget: drawer,
    bubbles: false, preventDefault() {}, stopPropagation() {}, isTrusted: true, timeStamp: Date.now() }); });
  await view.settle();
  return drawer;
};

test('mixed ticket (one quoted leg, one theoretical): every figure says «su prezzi teorici», chart legend and P/L map too', async () => {
  languages.impostaLinguaCorrente('it');
  const view = await mount({ legs: [ob.optionLeg('call', 'buy', EXPIRY, 100), ob.optionLeg('call', 'sell', EXPIRY, 110)] });
  try {
    assert.equal(view.container.all(has('data-no-quotes')).length, 0, 'not every contract is unquoted: no global badge');
    for (const k of ['entry', 'max-profit', 'max-loss', 'breakevens']) assert.ok(view.text(attr('data-fig', k)).includes('su prezzi teorici'), k + ': ' + view.text(attr('data-fig', k)));
    assert.ok(view.text(has('data-chart-theory')).includes('prezzi teorici'));
    const drawer = await openDrawer(view);
    assert.equal(drawer.all(has('data-heat-theory')).length, 1, 'the P/L map says it');
    const src = view.one(attr('data-leg-detail', 2)).textContent;
    assert.ok(src.includes('teorico'), 'the drawer names the theoretical source: ' + src);
    assert.ok(view.one(attr('data-leg-detail', 1)).textContent.includes('medio'));
  } finally { await view.unmount(); }
  const quoted = await mount({ legs: [ob.optionLeg('call', 'buy', EXPIRY, 100)] });
  try {
    assert.ok(!quoted.text(attr('data-fig', 'entry')).includes('teorici'));
    assert.equal(quoted.container.all(has('data-chart-theory')).length, 0);
  } finally { await quoted.unmount(); }
});

test('the dividend yield reaches the theoretical price (q > 0), literal oracle', async () => {
  languages.impostaLinguaCorrente('it');
  const view = await mount({ legs: [ob.optionLeg('call', 'buy', EXPIRY, 100)], noQuotes: true, now: Date.parse('2035-01-09T21:00:00Z') });
  try {
    await view.fire(view.one(el => el.localName === 'button' && el.className.includes('ob-link')), 'click');
    await view.fire(view.one(el => el.className === 'ob-inputs').byTag('input')[2], 'input', '2');
    // scipy: S 100, K 100, T 10/365, σ .25, r .04, q .02 → 1.6768803236045002
    // tolerance = the erf approximation (parity with the engine measured at 1,8e-5 at most); q = 0 would give 1,70
    assert.ok(Math.abs(view.bodies.at(-1).legs[0].premium - 1.6768803236045002) < 2e-5, String(view.bodies.at(-1).legs[0].premium));
    assert.equal(view.bodies.at(-1).dividend_yield, .02);
  } finally { await view.unmount(); }
});

test('an unlimited loss has no «perdita max» line on the chart', async () => {
  languages.impostaLinguaCorrente('it');
  const view = await mount({ legs: [ob.optionLeg('call', 'sell', EXPIRY, 100)], unlimited: true, maxProfit: 40 });
  try {
    assert.equal(view.container.all(attr('data-extreme', 'max-loss')).length, 0);
    assert.equal(view.container.all(attr('data-extreme', 'max-profit')).length, 1, 'the bounded side is still annotated');
  } finally { await view.unmount(); }
});

test('a leg added before the chain arrives is never replaced by the example', async () => {
  languages.impostaLinguaCorrente('it');
  const view = await mount({ deferExpiry: EXPIRY });
  try {
    await view.fire(view.one(attr('data-add-leg', 'stock')), 'click');
    assert.equal(view.container.all(has('data-leg')).length, 1);
    await React.act(async () => { view.deferred.resolve(); }); await view.settle();
    assert.equal(view.container.all(has('data-leg')).length, 1, 'still the user leg only');
    assert.equal(view.container.all(has('data-example')).length, 0);
    assert.ok(view.text(attr('data-leg', 1)).includes('Azioni'));
  } finally { await view.unmount(); }
});

test('the example rebuilt on another expiry keeps its badge; a click on a strategy makes it the user choice', async () => {
  languages.impostaLinguaCorrente('it');
  const view = await mount({});
  try {
    await view.fire(view.one(has('data-expiry')), 'change', EXPIRY2);
    assert.ok(view.bodies.at(-1).legs[0].days > 20, 'rebuilt on the far expiry');
    assert.equal(view.container.all(has('data-example')).length, 1, 'still declared an example');
    await view.fire(preset(view, 'bull_call'), 'click');
    assert.equal(view.container.all(has('data-example')).length, 0, 'chosen: no longer an example');
  } finally { await view.unmount(); }
});
