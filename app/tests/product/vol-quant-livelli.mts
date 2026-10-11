// vol-quant v2.1 (11/10/2026, Opus 5.5): gerarchia DICHIARATA del forward (quote → chiusure → proxy
// S·e^{rT}), smile dalle IV del fornitore senza quote, arbitraggi eseguibili «non verificabili» senza quote.
// FIXTURE SINTETICHE calibrate sulla FORMA del payload reale di un piano senza quote (bid e ask null su ogni
// contratto, solo IV del fornitore, OI e volume presenti): nessun ticker, nessun valore reale.
// ORACOLO INDIPENDENTE: i prezzi sono costruiti perche' la parita' C − P = e^{−rT}(F − K) valga ESATTAMENTE
// col forward scelto qui; il proxy si confronta con S·e^{rT} calcolato nel test.
import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';

const target = process.env.VOL_QUANT_LIB ? pathToFileURL(resolve(process.env.VOL_QUANT_LIB)).href : new URL('../../src/lib/vol-quant.ts', import.meta.url).href;
const vq: typeof import('../../src/lib/vol-quant.ts') = await import(target);

const near = (got: number | null | undefined, want: number, tol: number, what = '') => {
  assert.ok(typeof got === 'number' && Number.isFinite(got), `${what}: got ${got}`);
  assert.ok(Math.abs((got as number) - want) <= tol, `${what}: got ${got}, want ${want} (tol ${tol})`);
};

const S = 97.3, F_TRUE = 98.1, T = 0.3, R = 0.041, D = Math.exp(-R * T);
const STRIKES = [80, 85, 90, 95, 100, 105, 110, 115, 120];
const ivOf = (K: number) => 0.31 + 0.6 * Math.log(K / F_TRUE) ** 2 - 0.08 * Math.log(K / F_TRUE);
/** put = valore positivo qualunque; call dalla parita' col forward vero: C − P = D(F − K) esatta */
const putOf = (K: number) => 2.2 + 0.05 * Math.max(K - F_TRUE, 0) * 20;
const callOf = (K: number) => putOf(K) + D * (F_TRUE - K);
type Kind = 'quotes' | 'close' | 'none';
function chain(kind: Kind, opts: { volume?: number; stamp?: string | null } = {}) {
  return STRIKES.flatMap(K => (['call', 'put'] as const).map(type => {
    const px = type === 'call' ? callOf(K) : putOf(K);
    return { type, strike: K, iv: ivOf(K), oi: 900, volume: opts.volume ?? 14, multiplier: 100, adjusted: false,
      // forma reale senza quote: bid/ask null su OGNI contratto
      bid: kind === 'quotes' ? px - 0.02 : null, ask: kind === 'quotes' ? px + 0.02 : null,
      close: kind === 'close' ? px : null, close_timestamp: kind === 'close' ? (opts.stamp === undefined ? '2031-03-07T20:59:58Z' : opts.stamp) : null };
  }));
}
const input = (kind: Kind, extra: Partial<import('../../src/lib/vol-quant.ts').ForwardInput> = {}, opts = {}) =>
  ({ expiry: '2031-06-20', T, r: R, spot: S, contracts: chain(kind, opts), ...extra });
const EU = { exercise: 'european' as const };

test('level 1: parity on bid/ask, unchanged and declared', () => {
  const f = vq.forwardLevels([input('quotes')], EU)['2031-06-20'];
  assert.equal(f.level, 'parity_quotes'); assert.deepEqual(f.tried, []);
  near(f.forward, F_TRUE, 1e-9, 'F from mids (symmetric spread)');
  near(f.halfWidth, (0.04 + 0.04) / 2 / D, 1e-12, 'half width from the spread');
});

test('level 2: parity on closing prices with the session day, no spread claimed', () => {
  const f = vq.forwardLevels([input('close')], EU)['2031-06-20'];
  assert.equal(f.level, 'parity_close');
  assert.deepEqual(f.tried.map(t => [t.level, t.reason]), [['parity_quotes', 'no_valid_pairs']], 'level 1 tried first and why it failed');
  near(f.forward, F_TRUE, 1e-9, 'F from the closes');
  assert.equal(f.halfWidth, null, 'closes carry no spread: half width unknown, never 0');
  assert.ok(f.pairs.length >= 2 && f.pairs.every(p => p.halfWidth === null));
  assert.deepEqual(f.closeDays, ['2031-03-07'], 'session day in New York (20:59 UTC is still the 7th)');
  assert.equal(f.closeUndated, 0);
  const undated = vq.forwardLevels([input('close', {}, { stamp: null })], EU)['2031-06-20'];
  assert.equal(undated.level, 'parity_close'); assert.deepEqual(undated.closeDays, []);
  assert.equal(undated.closeUndated, undated.pairs.length * 2, 'every used leg without a bar time is counted');
  // dopo la mezzanotte UTC la seduta di New York e' ancora il giorno prima
  assert.equal(vq.nyDay('2031-03-08T01:30:00Z'), '2031-03-07');
  assert.equal(vq.nyDay('not a date'), null);
});

test('level 2 needs trades: a close without volume is an old price and does not count', () => {
  const f = vq.forwardLevels([input('close', {}, { volume: 0 })], EU)['2031-06-20'];
  assert.equal(f.level, 'proxy_carry', 'no traded close: down to the proxy');
  assert.deepEqual(f.tried.map(t => t.level), ['parity_quotes', 'parity_close']);
});

test('level 3: labelled proxy F = S·e^{rT} with q = 0 when the plan carries neither quotes nor closes', () => {
  const f = vq.forwardLevels([input('none')])['2031-06-20'];
  assert.equal(f.level, 'proxy_carry');
  near(f.forward, S * Math.exp(R * T), 1e-12, 'S·e^{rT}');
  assert.equal(f.reason, null);
  assert.match(String(f.detail), /q = 0/); assert.match(String(f.detail), /dividends ignored/); assert.match(String(f.detail), /quotes missing/);
  assert.deepEqual(f.tried.map(t => [t.level, t.reason]), [['parity_quotes', 'no_valid_pairs'], ['parity_close', 'no_valid_pairs']]);
  assert.equal(f.pairs.length, 0); assert.equal(f.dispersion, null); assert.equal(f.impliedDividendYield, null, 'q is an assumption, not a measure');
});

test('the hierarchy stops on missing time, rate or spot, and the proxy can be switched off', () => {
  const noRate = vq.forwardLevels([input('none', { r: null })])['2031-06-20'];
  assert.equal(noRate.level, null); assert.equal(noRate.forward, null); assert.equal(noRate.reason, 'rate_missing');
  const noTime = vq.forwardLevels([input('none', { T: null })])['2031-06-20'];
  assert.equal(noTime.level, null); assert.equal(noTime.reason, 'time_missing');
  const noSpot = vq.forwardLevels([input('none', { spot: null })], EU)['2031-06-20'];
  assert.equal(noSpot.level, null); assert.equal(noSpot.reason, 'spot_missing', 'the proxy needs the spot');
  const off = vq.forwardLevels([input('none')], { proxy: false })['2031-06-20'];
  assert.equal(off.level, null); assert.equal(off.forward, null); assert.equal(off.reason, 'no_valid_pairs');
  const dup = vq.forwardLevels([input('none'), input('none')])['2031-06-20'];
  assert.equal(dup.level, null); assert.equal(dup.reason, 'duplicate_input');
});

test('without quotes the observed smile uses the provider IVs, declared, and the delta grid is filled', () => {
  const fw = vq.forwardLevels([input('none')]);
  const chains = [{ expiry: '2031-06-20', T, contracts: chain('none') as import('../../src/lib/vol-quant.ts').ParityContract[] }];
  const strict = vq.surfaceFromChains(chains, fw, { spot: S, r: R });
  assert.equal(strict.slices[0].points.length, 0, 'default: quotes required, nothing silently replaced');
  assert.equal(vq.deltaGrid(strict, fw).rows[0].reason, 'smile_too_short');
  const loose = vq.surfaceFromChains(chains, fw, { spot: S, r: R, ivFallback: true });
  const sl = loose.slices[0];
  assert.equal(sl.basis, 'provider_iv');
  assert.ok(sl.points.length >= 7 && sl.points.every(p => p.bid === null && p.ask === null), 'points keep the missing quotes as null');
  const F = S * Math.exp(R * T);
  assert.ok(sl.points.every(p => p.type === (p.strike < F ? 'put' : 'call')), 'OTM around the proxy forward');
  const grid = vq.deltaGrid(loose, fw);
  assert.equal(grid.rows[0].basis, 'provider_iv');
  near(grid.rows[0].cells.ATMF.iv, (() => {      // oracolo: w lineare fra i due strike che racchiudono F
    const a = 95, b = 100, ka = Math.log(a / F), kb = Math.log(b / F);
    const wa = ivOf(a) ** 2 * T, wb = ivOf(b) ** 2 * T, w = wa + (wb - wa) * (0 - ka) / (kb - ka);
    return Math.sqrt(w / T);
  })(), 1e-12, 'ATMF on the provider IVs at K = F');
  const skew = vq.skewMetrics(grid)[0];
  assert.ok(skew.rr25 !== null && skew.rr25 < 0, 'put skew of the fixture: RR25 negative');
});

test('executable arbitrage is never «clean» without quotes: not verifiable', () => {
  const fw = vq.forwardLevels([input('none')]);
  const chains = [{ expiry: '2031-06-20', T, contracts: chain('none') as import('../../src/lib/vol-quant.ts').ParityContract[] }];
  const arb = vq.arbitrageChecks(vq.surfaceFromChains(chains, fw, { spot: S, r: R, ivFallback: true }), fw);
  assert.equal(arb.executableTests, 0);
  assert.equal(arb.executableStatus, 'quotes_missing');
  assert.equal(vq.reasonText('quotes_missing', 'it'), 'non verificabili: quote assenti');
  const fq = vq.forwardLevels([input('quotes')], EU);
  const quoted = vq.arbitrageChecks(vq.surfaceFromChains([{ ...chains[0], contracts: chain('quotes') as any }], fq, { spot: S, r: R }), fq);
  assert.ok(quoted.executableTests > 0); assert.equal(quoted.executableStatus, 'checked');
  const empty = vq.arbitrageChecks({ asOf: null, spot: null, slices: [] }, {});
  assert.equal(empty.executableStatus, null, 'nothing checked: no status');
});

// ---------------------------------------------------------------- v2 (review su chain vere, 11/10)
// Il carry di riferimento e' S·e^{rT} (q = 0); le chiusure qui sotto sono costruite con un forward SCELTO dal test.
const chainF = (Fx: number, legDay: (K: number, type: string) => string | null, volume = 14) => STRIKES.flatMap(K => (['call', 'put'] as const).map(type => {
  const put = 2.2 + Math.max(K - Fx, 0), px = type === 'call' ? put + D * (Fx - K) : put;
  const day = legDay(K, type);
  return { type, strike: K, iv: ivOf(K), oi: 900, volume, multiplier: 100, adjusted: false, bid: null, ask: null,
    close: px, close_timestamp: day ? `${day}T20:59:58Z` : null };
}));
const inF = (contracts: any[], extra: any = {}) => ({ expiry: '2031-06-20', T, r: R, spot: S, contracts, ...extra });
const CARRY = S * Math.exp(R * T);

test('v2 D1: only closes of the chain\'s last session; other sessions and undated legs are excluded and counted', () => {
  // le gambe a 95 e 100 hanno una chiusura VECCHIA con un forward diverso: non devono entrare
  const fresh = chainF(CARRY * 0.99, () => '2031-03-07');
  const stale = chainF(CARRY * 0.98, () => '2031-02-14').filter(c => c.strike === 95 || c.strike === 100);
  const mixedChain = [...fresh.filter(c => !(c.strike === 95 || c.strike === 100)), ...stale,
    { ...fresh[0], strike: 125, close_timestamp: null }];
  const f = vq.forwardLevels([inF(mixedChain)], EU)['2031-06-20'];
  assert.equal(f.level, 'parity_close'); assert.equal(f.closeSession, '2031-03-07');
  near(f.forward, CARRY * 0.99, 1e-9, 'F from the same-session closes only');
  assert.deepEqual(f.closeDays, ['2031-03-07']);
  assert.equal(f.closeExcluded.otherSession, 4, '2 strikes × 2 legs of an older session');
  assert.equal(f.closeExcluded.undated, 1);
  assert.deepEqual(f.warnings, []);
  // sessione di riferimento passata dal chiamante
  const forced = vq.forwardLevels([inF(mixedChain)], { ...EU, closeSession: '2031-02-14' })['2031-06-20'];
  assert.equal(forced.level, 'parity_close'); near(forced.forward, CARRY * 0.98, 1e-9, 'the chosen session decides');
  assert.deepEqual(forced.warnings, [], 'two same-session pairs: enough');
});

test('v2 D1: fewer than two same-session pairs = all traded closes with «closes not from the same session»; dispersion > 1% warned', () => {
  const day = (K: number) => (K === 100 ? '2031-03-07' : '2031-03-06');
  const f = vq.forwardLevels([inF(chainF(CARRY * 0.995, day))], EU)['2031-06-20'];
  assert.equal(f.level, 'parity_close'); assert.ok(f.warnings.includes('close_mixed_sessions'));
  assert.deepEqual(f.closeDays, ['2031-03-06', '2031-03-07'], 'both sessions declared');
  // candidati che si disperdono oltre l'1%: due strike con un forward diverso del 3%
  const disp = chainF(CARRY * 0.995, () => '2031-03-07').map(c => c.strike >= 105 && c.type === 'call' ? { ...c, close: (c.close as number) + D * CARRY * 0.03 } : c);
  const g = vq.forwardLevels([inF(disp)], { ...EU, maxPairs: 9 })['2031-06-20'];
  assert.equal(g.level, 'parity_close'); assert.ok((g.dispersionRel as number) > 0.01); assert.ok(g.warnings.includes('close_dispersion_high'));
});

test('v2 D3: the close-parity forward must sit in the carry band, asymmetric for dividends', () => {
  const lo = -(0.005 + 0.12 * T), hi = 0.005 + 0.02 * T;
  const at = (x: number) => vq.forwardLevels([inF(chainF(CARRY * Math.exp(x), () => '2031-03-07'))], EU)['2031-06-20'];
  assert.equal(at(lo + 0.001).level, 'parity_close', 'just inside below (dividends)');
  assert.equal(at(hi - 0.001).level, 'parity_close', 'just inside above');
  for (const x of [lo - 0.002, hi + 0.002]) {
    const f = at(x);
    assert.equal(f.level, 'proxy_carry', `outside the band (${x}) the level is dropped`);
    const t2 = f.tried.find(t => t.level === 'parity_close')!;
    assert.equal(t2.reason, 'implausible_vs_carry');
    near(t2.value, CARRY * Math.exp(x), 1e-9, 'the discarded value is declared');
  }
  // senza spot (europee) la banda non si misura: livello tenuto con l'avviso
  const noSpot = vq.forwardLevels([inF(chainF(CARRY * 1.2, () => '2031-03-07'), { spot: null })], EU)['2031-06-20'];
  assert.equal(noSpot.level, 'parity_close'); assert.ok(noSpot.warnings.includes('carry_unchecked'));
});

test('v2 D2: a declared dividend yield gives level 3b, q = 0 only as the last step; an unusable yield is said', () => {
  const div = { q: 0.065, source: 'synthetic yield', asOf: '2031-03-07' };
  const f = vq.forwardLevels([input('none')], { dividend: div })['2031-06-20'];
  assert.equal(f.level, 'proxy_carry_q'); near(f.forward, S * Math.exp((R - 0.065) * T), 1e-12, 'S·e^{(r−q)T}');
  assert.deepEqual(f.dividend, div); assert.ok(vq.isProxyLevel(f.level));
  assert.match(String(f.detail), /synthetic yield/);
  const bad = vq.forwardLevels([input('none')], { dividend: { q: 6.5, source: 'percent by mistake' } })['2031-06-20'];
  assert.equal(bad.level, 'proxy_carry'); assert.match(String(bad.detail), /dividend yield not usable: 6\.5/);
  // quote o chiusure presenti: il dividendo non serve
  assert.equal(vq.forwardLevels([input('quotes')], { ...EU, dividend: div })['2031-06-20'].level, 'parity_quotes');
  assert.equal(vq.isProxyLevel('parity_close'), false);
});
