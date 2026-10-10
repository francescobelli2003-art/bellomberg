// Option builder (09/10/2026, Opus 5.5): chain → leg logic. Pricing is the backend's; here we
// prove which contract a leg reads, which price/IV it carries and that a hole stays a hole.
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  buildPreset, daysToClose, editLeg, engineLegs, engineResultProblem, expiryCloseUtc, fieldNumber, horizonDays, invertLeg,
  nearestPoint, optionLeg, premiums, quoteLeg, returnOnRisk, stockLeg, strikesOf, type ChainContract,
} from '../../src/lib/option-builder.ts';

const read = (s: string) => { const v = Number(s.replace(',', '.')); return s.trim() && Number.isFinite(v) && v > 0 ? v : null; };

function row(type: 'call' | 'put', strike: number, expiry: string, over: Partial<ChainContract> = {}): ChainContract {
  const bid = 10 / strike, ask = bid + .2;
  return { contract: `O:SYN${expiry}${type[0]}${strike}`, type, strike, expiry, iv: type === 'call' ? .25 : .27,
    bid, ask, mid: (bid + ask) / 2, multiplier: 100, adjusted: false, quote_timestamp: null, quote_timeframe: 'DELAYED',
    quality: [], _source: 'synthetic', ...over };
}
const NEAR = '2035-01-19', FAR = '2035-02-16';
const strikes = [85, 90, 95, 100, 105, 110, 115];
const chain = (e: string) => strikes.flatMap(k => [row('call', k, e), row('put', k, e)]);
const chains = new Map([[NEAR, chain(NEAR)], [FAR, chain(FAR)]]);
const NOW = Date.UTC(2035, 0, 9, 15, 0, 0);

test('expiry close is 16:00 New York: EST in winter, EDT in summer', () => {
  assert.equal(new Date(expiryCloseUtc('2026-12-18')).toISOString(), '2026-12-18T21:00:00.000Z');
  assert.equal(new Date(expiryCloseUtc('2026-07-17')).toISOString(), '2026-07-17T20:00:00.000Z');
  // 0DTE at 10:00 New York (15:00Z in January): six hours left, a quarter of a day, not 0.
  assert.equal(daysToClose('2035-01-09', NOW), .25);
});

test('a leg reads its own contract: bid/ask/mid/IV follow side, type and strike (audit H1, M1)', () => {
  const leg = optionLeg('call', 'buy', NEAR, 100);
  const buy = quoteLeg(leg, chains, 'natural', 100, NOW, read);
  const c100 = chains.get(NEAR)!.find(r => r.type === 'call' && r.strike === 100)!;
  assert.equal(buy.price, c100.ask); assert.equal(buy.priceSource, 'ask'); assert.equal(buy.iv, .25);
  const sold = quoteLeg(invertLeg(leg), chains, 'natural', 100, NOW, read);
  assert.equal(sold.price, c100.bid, 'selling reads the bid, not the old ask');
  const put = quoteLeg(editLeg(leg, { type: 'put' }), chains, 'mid', 100, NOW, read);
  assert.equal(put.iv, .27, 'the put carries the put IV');
  const k105 = quoteLeg(editLeg(leg, { strike: 105 }), chains, 'mid', 100, NOW, read);
  assert.equal(k105.mid, chains.get(NEAR)!.find(r => r.type === 'call' && r.strike === 105)!.mid);
});

test('manual overrides belong to one contract and drop when it changes', () => {
  const leg = { ...optionLeg('call', 'buy', NEAR, 100), iv: '30', price: '4,5' };
  assert.equal(quoteLeg(leg, chains, 'mid', 100, NOW, read).price, 4.5);
  assert.equal(quoteLeg(leg, chains, 'mid', 100, NOW, read).iv, .3);
  const moved = editLeg(leg, { strike: 105 });
  assert.equal(moved.iv, ''); assert.equal(moved.price, '');
  assert.equal(invertLeg(leg).price, '', 'the side flips: a manual buy price is not a sell price');
  assert.equal(invertLeg(leg).iv, '30', 'same contract: the IV assumption stays');
});

test('a missing quote, IV or multiplier is n.d. with its reason, never 0', () => {
  const holes = new Map([[NEAR, [row('call', 100, NEAR, { bid: null, ask: null, mid: null }), row('put', 100, NEAR, { iv: null }),
    row('call', 105, NEAR, { multiplier: null }), row('put', 95, NEAR, { bid: 3, ask: 2, mid: null })]]]);
  const q = (l: ReturnType<typeof optionLeg>) => quoteLeg(l, holes, 'mid', 100, NOW, read);
  assert.deepEqual(q(optionLeg('call', 'buy', NEAR, 100)).reasons, ['no_quote']);
  assert.equal(q(optionLeg('call', 'buy', NEAR, 100)).price, null);
  assert.deepEqual(q(optionLeg('put', 'buy', NEAR, 100)).reasons, ['no_iv']);
  assert.deepEqual(q(optionLeg('call', 'buy', NEAR, 105)).reasons, ['no_multiplier']);
  assert.deepEqual(q(optionLeg('put', 'buy', NEAR, 95)).reasons, ['crossed_quote']);
  assert.deepEqual(q(optionLeg('put', 'buy', NEAR, 110)).reasons, ['no_contract']);
  assert.deepEqual(quoteLeg(optionLeg('call', 'buy', '2035-01-08', 100), holes, 'mid', 100, NOW, read).reasons, ['expired', 'no_contract']);
  const legs = [optionLeg('call', 'buy', NEAR, 100), optionLeg('put', 'buy', NEAR, 100)];
  const quotes = legs.map(q);
  const out = engineLegs(legs, quotes);
  assert.equal(out.ok, false);
  assert.deepEqual(!out.ok && out.missing.map(m => m.index), [0, 1]);
  assert.equal(premiums(legs, quotes).mid, null, 'one missing price makes the net premium n.d.');
  // a manual price fills the hole and is declared as manual
  const filled = quoteLeg({ ...legs[0], price: '2' }, holes, 'mid', 100, NOW, read);
  assert.equal(filled.ok, true); assert.equal(filled.priceSource, 'manual');
});

test('net premium at mid vs natural: the spread is the cost of crossing it', () => {
  const legs = [optionLeg('call', 'buy', NEAR, 100), optionLeg('call', 'sell', NEAR, 110)];
  const quotes = legs.map(l => quoteLeg(l, chains, 'mid', 100, NOW, read));
  const p = premiums(legs, quotes);
  const c = (k: number) => chains.get(NEAR)!.find(r => r.type === 'call' && r.strike === k)!;
  assert.ok(Math.abs(p.mid! - 100 * (c(100).mid! - c(110).mid!)) < 1e-9);
  assert.ok(Math.abs(p.natural! - 100 * (c(100).ask! - c(110).bid!)) < 1e-9);
  assert.ok(Math.abs(p.spread! - 100 * (.1 + .1)) < 1e-9, 'half spread on each of the two legs');
});

test('engine legs carry fractional days computed now, IV in decimals and shares without strike', () => {
  const legs = [stockLeg('buy', 100), optionLeg('call', 'sell', NEAR, 105)];
  const quotes = legs.map(l => quoteLeg(l, chains, 'mid', 101.5, NOW, read));
  const out = engineLegs(legs, quotes);
  assert.ok(out.ok);
  if (!out.ok) return;
  assert.deepEqual(out.legs[0], { type: 'stock', side: 'buy', quantity: 100, premium: 101.5 });
  assert.equal(out.legs[1].days, daysToClose(NEAR, NOW));
  assert.equal(out.legs[1].iv, .25);
  assert.equal(horizonDays(quotes), daysToClose(NEAR, NOW));
});

test('presets: one click, distinct ordered strikes, nearest to spot', () => {
  const strikesOf = (id: Parameters<typeof buildPreset>[0]) => {
    const out = buildPreset(id, 101, NEAR, chains, [FAR]);
    assert.ok(out.ok, id); return out.ok ? out.legs.map(l => [l.kind === 'stock' ? 'S' : l.type, l.side, l.strike ?? null, l.expiry ?? null]) : [];
  };
  assert.deepEqual(strikesOf('long_call'), [['call', 'buy', 100, NEAR]]);
  assert.deepEqual(strikesOf('iron_condor'), [['put', 'buy', 90, NEAR], ['put', 'sell', 95, NEAR], ['call', 'sell', 105, NEAR], ['call', 'buy', 110, NEAR]]);
  assert.deepEqual(strikesOf('iron_butterfly'), [['put', 'buy', 95, NEAR], ['put', 'sell', 100, NEAR], ['call', 'sell', 100, NEAR], ['call', 'buy', 105, NEAR]]);
  assert.deepEqual(strikesOf('covered_call'), [['S', 'buy', null, null], ['call', 'sell', 105, NEAR]]);
  assert.deepEqual(strikesOf('cash_secured_put'), [['put', 'sell', 95, NEAR]]);
  assert.deepEqual(strikesOf('bear_put'), [['put', 'buy', 100, NEAR], ['put', 'sell', 95, NEAR]]);
  assert.deepEqual(strikesOf('calendar'), [['call', 'sell', 100, NEAR], ['call', 'buy', 100, FAR]]);
  const covered = buildPreset('covered_call', 101, NEAR, chains, []);
  assert.ok(covered.ok && covered.legs[0].qty === '100', 'shares = one contract multiplier');
  assert.deepEqual(buildPreset('calendar', 101, NEAR, new Map([[NEAR, chain(NEAR)]]), [FAR]), { ok: false, reason: 'needs_far_expiry', expiry: FAR });
  assert.deepEqual(buildPreset('straddle', null, NEAR, chains, []), { ok: false, reason: 'no_spot' });
});

test('helpers: nearest exact curve point, return on risk only when both ends are finite, field numbers without float noise', () => {
  assert.equal(nearestPoint([{ price: 90 }, { price: 95 }, { price: 100 }], 96.4), 1);
  assert.equal(returnOnRisk({ max_profit: 700, max_loss: 300, unlimited_profit: false, unlimited_loss: false }), 7 / 3);
  assert.equal(returnOnRisk({ max_profit: null, max_loss: 300, unlimited_profit: true, unlimited_loss: false }), null);
  assert.equal(fieldNumber(.29 * 100, true), '29');
  assert.equal(fieldNumber(28.999999999999996, false), '29');
  assert.equal(fieldNumber(158.5, true), '158,5');
});

// ── review 10/10 (Opus 5.5): surviving mutants F1, F2, F4; L5; M3 response shape ─────────────

test('F1: a share leg enters the net premium at its price (spot or manual), never at a null mid', () => {
  const legs = [stockLeg('buy', 100), optionLeg('call', 'sell', NEAR, 105)];
  const quotes = legs.map(l => quoteLeg(l, chains, 'mid', 101.5, NOW, read));
  assert.equal(quotes[0].mid, null, 'shares have no bid/ask/mid');
  const c105 = chains.get(NEAR)!.find(r => r.type === 'call' && r.strike === 105)!;
  const p = premiums(legs, quotes);
  assert.ok(p.mid != null && Math.abs(p.mid - (100 * 101.5 - 100 * c105.mid!)) < 1e-9, String(p.mid));
  assert.ok(p.natural != null && Math.abs(p.natural - (100 * 101.5 - 100 * c105.bid!)) < 1e-9, String(p.natural));
  const manual = [{ ...legs[0], price: '99' }, legs[1]];
  const pm = premiums(manual, manual.map(l => quoteLeg(l, chains, 'mid', 101.5, NOW, read)));
  assert.ok(pm.mid != null && Math.abs(pm.mid - (100 * 99 - 100 * c105.mid!)) < 1e-9);
});

test('F2 + L5: an adjusted contract is never used and the leg says "adjusted", not "no contract"', () => {
  const adj = new Map([[NEAR, [row('call', 100, NEAR, { adjusted: true }), row('call', 105, NEAR), row('put', 100, NEAR)]]]);
  const q = quoteLeg(optionLeg('call', 'buy', NEAR, 100), adj, 'mid', 100, NOW, read);
  assert.equal(q.contract, null); assert.equal(q.price, null);
  assert.deepEqual(q.reasons, ['adjusted']);
  assert.deepEqual(quoteLeg(optionLeg('call', 'buy', NEAR, 110), adj, 'mid', 100, NOW, read).reasons, ['no_contract']);
  assert.deepEqual(strikesOf(adj.get(NEAR), 'call'), [105], 'the adjusted strike is not offered');
  const preset = buildPreset('long_call', 100, NEAR, adj, []);
  assert.ok(preset.ok && preset.legs[0].strike === 105, 'presets skip adjusted contracts');
  // a standard twin next to an adjusted one is used (the adjusted row is ignored)
  const twin = new Map([[NEAR, [row('call', 100, NEAR, { adjusted: true, mid: 99 }), row('call', 100, NEAR)]]]);
  const t = quoteLeg(optionLeg('call', 'buy', NEAR, 100), twin, 'mid', 100, NOW, read);
  assert.equal(t.ok, true); assert.notEqual(t.price, 99);
});

test('F4: selling at the natural price needs a live ask too (a one-sided bid is not a market)', () => {
  const oneSided = new Map([[NEAR, [row('call', 100, NEAR, { bid: 1, ask: null, mid: null }), row('put', 100, NEAR, { bid: 0, ask: 0, mid: null })]]]);
  for (const type of ['call', 'put'] as const) {
    const sell = quoteLeg(optionLeg(type, 'sell', NEAR, 100), oneSided, 'natural', 100, NOW, read);
    assert.equal(sell.natural, null, type); assert.equal(sell.price, null, type);
    assert.deepEqual(sell.reasons, ['no_quote'], type);
  }
  const two = quoteLeg(optionLeg('call', 'sell', NEAR, 100), new Map([[NEAR, [row('call', 100, NEAR, { bid: 1, ask: 1.2, mid: 1.1 })]]]), 'natural', 100, NOW, read);
  assert.equal(two.price, 1); assert.equal(two.priceSource, 'bid');
});

function engineResult(): Record<string, unknown> {
  const point = (price: number, pnl: number) => ({ price, pnl, delta: .5, gamma: .01, vega: 10, theta: -2, rho: 1 });
  return { currency: 'USD', model: 'BSM', greek_units: {}, entry_cost: 300, net_premium: 300, fees: 0, entry_kind: 'debit',
    same_expiry: true, expiry_days: 10.5, breakevens: [103], max_profit: null, max_loss: 300, unlimited_profit: true, unlimited_loss: false,
    today: point(100, 0), scenario: point(100, 0), curve: [{ price: 90, expiry: -300, today: -250, scenario: -250 }, { price: 110, expiry: 700, today: 650, scenario: 650 }],
    heatmap: [{ elapsed_days: 0, cells: [point(90, -250), point(110, 650)] }], limits: [] };
}

test('M3: the engine response is checked before the page reads it; the first bad field is named', () => {
  assert.equal(engineResultProblem(engineResult()), null);
  assert.equal(engineResultProblem(null), 'response: object');
  assert.equal(engineResultProblem([]), 'response: object');
  assert.equal(engineResultProblem({ pnl: [] }), 'entry_cost');
  const damage = (f: (r: Record<string, any>) => void) => { const r = engineResult() as Record<string, any>; f(r); return engineResultProblem(r); };
  assert.equal(damage(r => { delete r.curve; }), 'curve');
  assert.equal(damage(r => { r.curve = [r.curve[0]]; }), 'curve', 'one point is not a curve');
  assert.equal(damage(r => { r.curve[1].today = null; }), 'curve[1]');
  assert.equal(damage(r => { r.curve[0].expiry = null; }), null, 'expiry may be n.d.');
  assert.equal(damage(r => { r.today.pnl = 'x'; }), 'today: price/pnl');
  assert.equal(damage(r => { r.scenario.theta = undefined; }), 'scenario.theta');
  assert.equal(damage(r => { r.scenario.theta = null; }), null, 'a greek may be n.d. (kink at expiry)');
  assert.equal(damage(r => { delete r.heatmap[0].cells[1].pnl; }), 'heatmap[0].cells[1]: price/pnl');
  assert.equal(damage(r => { r.entry_kind = 'free'; }), 'entry_kind');
  assert.equal(damage(r => { r.max_loss = Number.NaN; }), 'max_loss');
  assert.equal(damage(r => { r.breakevens = ['103']; }), 'breakevens');
  assert.equal(damage(r => { r.unlimited_loss = 0; }), 'unlimited_loss');
  assert.equal(damage(r => { delete r.limits; }), 'limits');
});
