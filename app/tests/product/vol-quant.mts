// vol-quant (10/10/2026, Opus 5.5): libreria pura della superficie «da desk».
// ORACOLO INDIPENDENTE: i letterali vengono da tests/product/vol-quant-oracle.py (scipy norm,
// brentq, numpy.interp), mai dal codice sotto test. Gli input sintetici (SVI/SSVI, catene
// Black-76 con forward noto, strutture con evento noto) sono costruiti qui.
// Banco di mutazioni: tests/product/vol-quant-mutanti.mjs (VOL_QUANT_LIB punta a una copia).
import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';
import { readFileSync } from 'node:fs';

const target = process.env.VOL_QUANT_LIB ? pathToFileURL(resolve(process.env.VOL_QUANT_LIB)).href : new URL('../../src/lib/vol-quant.ts', import.meta.url).href;
const vq: typeof import('../../src/lib/vol-quant.ts') = await import(target);

const near = (got: number | null | undefined, want: number, tol: number, what = '') => {
  assert.ok(typeof got === 'number' && Number.isFinite(got), `${what}: got ${got}`);
  assert.ok(Math.abs((got as number) - want) <= tol, `${what}: got ${got}, want ${want} (tol ${tol})`);
};

test('normCdf matches scipy to double precision, tails included', () => {
  const xs = [-8.5, -3.2, -0.7, 0.0, 1.3, 6.0];
  const want = [9.47953482220325e-18, 0.0006871379379158471, 0.24196365222307303, 0.5, 0.9031995154143897, 0.9999999990134123];
  xs.forEach((x, i) => near(vq.normCdf(x), want[i], Math.max(1e-14, want[i] * 1e-10), `N(${x})`));
  // Hart 5666 misurato contro scipy: relativo 1e-15 a |x| = 2, 2e-14 a 3,2, 5e-11 a 5, 5e-10 a 6
  // (assoluto sempre < 1e-15). Tolleranze RELATIVE alla precisione vera: un coefficiente troncato si vede.
  const tails: [number, number, number][] = [[-2, 0.0227501319481792, 1e-14], [-3.2, 0.0006871379379158471, 1e-13],
    [-5, 2.8665157187919333e-07, 1e-10], [-6, 9.865876450376948e-10, 1e-9]];
  for (const [x, w, rel] of tails) near(vq.normCdf(x), w, w * rel, `tail N(${x})`);
  near(vq.normCdf(6) + vq.normCdf(-6), 1, 1e-15, 'symmetry');
});

test('bsDelta (spot, r, q) and deltaFromForward (undiscounted) on known cases', () => {
  near(vq.bsDelta('call', 100, 110, 0.5, 0.3, 0.04, 0.01), 0.39065302763589743, 1e-12, 'call');
  near(vq.bsDelta('put', 100, 110, 0.5, 0.3, 0.04, 0.01), -0.6043594515567849, 1e-12, 'put');
  near(vq.bsDelta('call', 50, 30, 2.0, 0.6, 0.0, 0.03), 0.7819984378007622, 1e-12, 'call q');
  near(vq.bsDelta('put', 250, 200, 0.05, 0.15, 0.05, 0.0), -7.695621917491735e-12, 1e-15, 'deep OTM put');
  near(vq.deltaFromForward('call', 100, 100, 1.0, 0.2), 0.539827837277029, 1e-12, 'ATMF call = N(σ√T/2)');
  near(vq.deltaFromForward('put', 100, 90, 0.25, 0.35), -0.24523543646080648, 1e-12, 'fwd put');
  near(vq.deltaFromForward('call', 4000, 4400, 0.1, 0.12), 0.006339329191634516, 1e-12, 'fwd call');
  near(vq.forwardPrice('call', 100, 95, 0.4, 0.25), 8.963458448269066, 1e-10, 'B76 call');
  near(vq.forwardPrice('put', 100, 95, 0.4, 0.25), 3.9634584482690656, 1e-10, 'B76 put');
  for (const bad of [[0, 100, 1, .2], [100, 100, 0, .2], [100, 100, 1, 0], [100, NaN, 1, .2]] as const)
    assert.equal(vq.deltaFromForward('call', ...bad), null, `invalid ${bad}`);
  assert.equal(vq.bsDelta('call', 100, 100, 1, .2, NaN, 0), null, 'a missing r is not 0');
  assert.equal(vq.bsDelta('call', 100, 100, 1, .2, 0.03, null as unknown as number), null, 'a missing q is not 0');
});

const T_SVI = 0.3;
const sviW = (k: number, a = 0.02, b = 0.12, rho = -0.5, m = 0.02, sig = 0.15) => a + b * (rho * (k - m) + Math.sqrt((k - m) ** 2 + sig * sig));
const sviSigma = (k: number) => Math.sqrt(sviW(k) / T_SVI);

test('strikeForDelta solves K with a strike-dependent smile, round trip strike ↔ delta', () => {
  const F = 150, range = { kMin: -1, kMax: 1 };
  const cases: [number, 'call' | 'put', number, number, number][] = [
    [0.25, 'call', 0.14585056456247583, 173.55349122782025, 0.34614634750981577],
    [0.10, 'call', 0.2774502471010806, 197.96406834026268, 0.3665527676488584],
    [-0.25, 'put', -0.13019638210029777, 131.68845084401252, 0.42616231678000344],
    [-0.10, 'put', -0.3355808625041363, 107.23840409025547, 0.5405114751376541],
    [0.5, 'call', 0.019029309171033085, 152.88172806366268, 0.35617700815777176],
  ];
  for (const [d, type, k, K, iv] of cases) {
    const r = vq.strikeForDelta(F, T_SVI, sviSigma, d, type, range);
    assert.equal(r.reason, null);
    near(r.k, k, 1e-10, `k ${type}${d}`); near(r.strike, K, 1e-7, `K ${type}${d}`); near(r.iv, iv, 1e-10, `iv ${type}${d}`);
    near(vq.deltaFromForward(type, F, r.strike as number, T_SVI, sviSigma(r.k as number)), d, 1e-12, 'round trip');
  }
  const narrow = vq.strikeForDelta(F, T_SVI, sviSigma, 0.10, 'call', { kMin: -0.1, kMax: 0.2 });
  assert.equal(narrow.reason, 'delta_out_of_range'); assert.equal(narrow.strike, null, 'never extrapolated');
  assert.equal(vq.strikeForDelta(F, T_SVI, sviSigma, 0.25, 'put', range).reason, 'invalid_input', 'a put target must be negative');
  assert.equal(vq.strikeForDelta(F, T_SVI, sviSigma, 1.2, 'call', range).reason, 'invalid_input');
  // smile assurdo: Δcall non monotono (0,99 → 0,43 → 0,99): due strike per Δ = 0,6
  const wild = (k: number) => 0.1 + 20 * k * k;
  assert.equal(vq.strikeForDelta(100, 1, wild, 0.6, 'call', { kMin: -0.5, kMax: 0.5 }).reason, 'delta_ambiguous');
  assert.equal(vq.strikeForDelta(100, 1, () => null, 0.25, 'call', range).reason, 'outside_smile');
});

// smile campionato (F = 200, T = 0,5), nodi k = −0,60 … 0,40 passo 0,05
const F1 = 200, T1 = 0.5;
const KS = Array.from({ length: 21 }, (_, i) => Math.round((-0.6 + 0.05 * i) * 1e10) / 1e10);
const sampled = (): import('../../src/lib/vol-quant.ts').SmileSurface => ({ asOf: '2035-01-02T15:00:00Z', spot: 199, slices: [
  { expiry: '2035-07-03', T: T1, days: 182, source: 'grid', points: KS.map(k => ({ strike: F1 * Math.exp(k), iv: Math.sqrt(sviW(k) / T1) })) }] });

test('deltaGrid: 10ΔP 25ΔP ATMF 25ΔC 10ΔC on the total-variance-linear smile (scipy brentq + numpy.interp)', () => {
  const g = vq.deltaGrid(sampled(), { '2035-07-03': { forward: F1 } });
  assert.equal(g.deltaConvention, 'forward-undiscounted'); assert.equal(g.atmConvention, 'ATMF (K = F)');
  const row = g.rows[0];
  assert.equal(row.source, 'grid'); assert.equal(row.reason, null);
  const want: Record<string, [number, number]> = {
    '10P': [-0.3356104012161254, 0.41872077760282017], '25P': [-0.13029226104783265, 0.3304114052250797],
    'ATMF': [0, 0.2805683344232056], '25C': [0.14594192207946752, 0.26827332045444924], '10C': [0.27759171612036776, 0.284065517612307] };
  for (const [key, [k, iv]] of Object.entries(want)) {
    const c = row.cells[key as 'ATMF'];
    assert.equal(c.reason, null, key);
    near(c.k, k, 1e-9, `k ${key}`); near(c.iv, iv, 1e-10, `iv ${key}`); near(c.strike, F1 * Math.exp(k), 1e-6, `K ${key}`);
  }
  near(row.cells.ATMF.delta, vq.normCdf(0.2805683344232056 * Math.sqrt(T1) / 2), 1e-12, 'ATMF delta is N(σ√T/2), declared');
  near(row.cells['25P'].delta, -0.25, 1e-10); near(row.cells['10C'].delta, 0.10, 1e-10);

  const skew = vq.skewMetrics(g)[0];
  near(skew.rr25, -0.062138084770630486, 1e-10, 'rr25'); near(skew.bf25, 0.01877402841655884, 1e-10, 'bf25');
  near(skew.rr10, -0.1346552599905132, 1e-10, 'rr10'); near(skew.bf10, 0.07082481318435796, 1e-10, 'bf10');
  near(skew.skewNorm25, -0.22147219463798154, 1e-10, 'RR25/ATM signed (soglie_score convention)');
});

test('deltaGrid n/a: forward missing, smile short, 10Δ beyond the covered strikes, ATM outside', () => {
  const noF = vq.deltaGrid(sampled(), {});
  assert.equal(noF.rows[0].reason, 'forward_missing');
  assert.ok(Object.values(noF.rows[0].cells).every(c => c.iv === null && c.reason === 'forward_missing'), 'never spot·e^{rT}');
  assert.equal(vq.deltaGrid(sampled(), { '2035-07-03': { forward: null, reason: 'rate_missing' } }).rows[0].reason, 'rate_missing',
    'v2: the TRUE reason travels (FRED down), not a generic forward_missing');
  const s = sampled(); s.slices[0].points = s.slices[0].points.slice(9, 11);
  assert.equal(vq.deltaGrid(s, { '2035-07-03': F1 }).rows[0].reason, 'smile_too_short');
  const narrow = sampled(); narrow.slices[0].points = narrow.slices[0].points.filter((_, i) => KS[i] >= -0.2 && KS[i] <= 0.2);
  const row = vq.deltaGrid(narrow, { '2035-07-03': F1 }).rows[0];
  assert.equal(row.cells['10P'].iv, null); assert.equal(row.cells['10P'].reason, 'delta_out_of_range');
  assert.equal(row.cells['10C'].reason, 'delta_out_of_range'); assert.notEqual(row.cells['25P'].iv, null);
  const sk = vq.skewMetrics({ ...vq.deltaGrid(narrow, { '2035-07-03': F1 }) })[0];
  assert.equal(sk.rr10, null); assert.equal(sk.reasons.rr10, 'delta_out_of_range'); assert.notEqual(sk.rr25, null);
  const right = sampled(); right.slices[0].points = right.slices[0].points.filter((_, i) => KS[i] >= 0.05);
  const r2 = vq.deltaGrid(right, { '2035-07-03': F1 }).rows[0];
  assert.equal(r2.cells.ATMF.reason, 'outside_smile'); assert.equal(vq.skewMetrics({ ...vq.deltaGrid(right, { '2035-07-03': F1 }) })[0].skewNorm25, null);
  assert.equal(vq.deltaGrid({ ...sampled(), slices: [{ ...sampled().slices[0], T: null }] }, { '2035-07-03': F1 }).rows[0].reason, 'time_missing');
});

test('surfaceFromBuilder: K = (K/S)·spot, holes stay out, no spot = no strikes', () => {
  const payload = { spot_est: 100, snapshot_at: '2035-01-02T15:00:00Z', moneyness_grid: [0.9, 1, 1.1, 1.2],
    slices: [{ expiry: '2035-02-01', days: 30, t_years: 0.08, iv_grid: [0.3, 0.25, null, 0] }] };
  const s = vq.surfaceFromBuilder(payload);
  assert.equal(s.asOf, '2035-01-02T15:00:00Z');
  assert.deepEqual(s.slices[0].points, [{ strike: 90, iv: 0.3 }, { strike: 100, iv: 0.25 }]);
  assert.equal(s.slices[0].source, 'grid');
  assert.deepEqual(vq.surfaceFromBuilder({ ...payload, spot_est: null }).slices[0].points, []);
});

// catena Black-76 scontata con forward noto 104,3 (r 4,5%, T 0,75): mid esatti, spread ±0,05
const PAR = [[90, 17.59133447468612, 3.765906033268073], [95, 14.470198847760487, 5.478836294950139], [100, 11.75173079283226, 7.594434128629629],
  [105, 9.429081802427094, 10.10585102683217], [110, 7.480073398836018, 12.990908511848815], [115, 5.871667065576698, 16.21656806719721]];
const q = (type: 'call' | 'put', strike: number, mid: number, over = {}) => ({ type, strike, bid: mid - 0.05, ask: mid + 0.05, oi: 10, volume: 0, adjusted: false, multiplier: 100, ...over });
const EU = { exercise: 'european' as const };
const parChain = () => PAR.flatMap(([K, c, p]) => [q('call', K, c), q('put', K, p)]);

test('impliedForward: F = K + e^{rT}(C − P), median of the nearest-ATM pairs, quality declared', () => {
  const res = vq.impliedForward([{ expiry: '2035-10-01', T: 0.75, r: 0.045, contracts: parChain(), spot: 100 }], EU)['2035-10-01'];
  assert.equal(res.reason, null);
  near(res.forward, 104.3, 1e-9, 'known forward');
  assert.deepEqual(res.pairs.map(p => p.strike), [95, 100, 105, 110], 'the 4 strikes with the smallest |C − P|');
  near(res.dispersion, 0, 1e-9); near(res.halfWidth, Math.exp(0.045 * 0.75) * 0.1, 1e-12, 'e^{rT}·(spreadC + spreadP)/2');
  near(res.impliedCarry, Math.log(1.043) / 0.75, 1e-9); near(res.impliedDividendYield, 0.045 - Math.log(1.043) / 0.75, 1e-9);
  const noisy = parChain(); noisy[4] = q('call', 100, PAR[2][1] + 0.6);
  const robust = vq.impliedForward([{ expiry: 'x', T: 0.75, r: 0.045, contracts: noisy }], EU).x;
  near(robust.forward, 104.3, 1e-9, 'one bad pair does not move the median');
  near(robust.dispersion, Math.exp(0.045 * 0.75) * 0.6, 1e-9, 'and shows up in the dispersion');
  assert.equal(robust.impliedCarry, null, 'no spot: no carry, never a guess');
});

test('impliedForward n/a: no r, no T, too few pairs, no valid quotes — never spot·e^{rT}', () => {
  const one = (over: Partial<import('../../src/lib/vol-quant.ts').ForwardInput>) => vq.impliedForward([{ expiry: 'e', T: 0.75, r: 0.045, contracts: parChain(), spot: 100, ...over }], EU).e;
  assert.deepEqual([one({ r: null }).forward, one({ r: null }).reason], [null, 'rate_missing']);
  assert.equal(one({ T: null }).reason, 'time_missing'); assert.equal(one({ T: 0 }).reason, 'time_missing');
  const single = one({ contracts: [q('call', 100, 11.75), q('put', 100, 7.59), q('call', 105, 9.4)] });
  assert.deepEqual([single.forward, single.reason, single.validPairs], [null, 'too_few_pairs', 1]);
  const bad = parChain().map((c, i) => i % 4 === 0 ? { ...c, oi: 0, volume: 0 } : i % 4 === 1 ? { ...c, bid: 0 } : i % 4 === 2 ? { ...c, adjusted: true } : { ...c, ask: c.bid - 0.01 });
  assert.equal(one({ contracts: bad }).reason, 'no_valid_pairs', 'illiquid, zero bid, adjusted and crossed quotes are out');
  const wide = parChain().map(c => c.type === 'put' ? { ...c, bid: 0.5, ask: 30 } : c);
  assert.equal(one({ contracts: wide }).reason, 'no_valid_pairs', 'spread beyond maxRelSpread is out');
  assert.equal(one({ contracts: parChain().map(c => ({ ...c, multiplier: 10 })) }).reason, 'no_valid_pairs', 'non-standard multiplier is out');
});

test('forwardVol: total-variance forward, calendar inversion flagged with its size', () => {
  const [p] = vq.forwardVol([{ expiry: 'b', T: 0.5, iv: 0.27 }, { expiry: 'a', T: 0.25, iv: 0.25 }]);
  assert.deepEqual([p.from, p.to], ['a', 'b'], 'sorted by T');
  near(p.forwardVol, 0.28861739379323625, 1e-12, 'scipy');
  const [inv] = vq.forwardVol([{ expiry: 'a', T: 0.25, iv: 0.30 }, { expiry: 'b', T: 0.5, iv: 0.20 }]);
  assert.equal(inv.forwardVol, null); assert.equal(inv.calendarArbitrage, true); assert.equal(inv.reason, 'calendar_arbitrage');
  near(inv.varianceDrop, 0.0225 - 0.02, 1e-15); near(inv.forwardVariance, (0.02 - 0.0225) / 0.25, 1e-15); assert.equal(inv.beyondNoise, true);
  const [tiny] = vq.forwardVol([{ expiry: 'a', T: 0.25, iv: 0.2502 }, { expiry: 'b', T: 0.26, iv: 0.2450 }]);
  assert.equal(tiny.calendarArbitrage, true); assert.equal(tiny.beyondNoise, false, 'inside ±0.5 vol points of noise');
  const holes = vq.forwardVol([{ expiry: 'a', T: 0.25, iv: 0.3 }, { expiry: 'b', T: 0.5, iv: null }, { expiry: 'c', T: 1, iv: 0.3 }]);
  assert.deepEqual(holes.map(h => h.reason), ['iv_missing', 'iv_missing'], 'a hole breaks the pair, it is not skipped');
  assert.equal(vq.forwardVol([{ expiry: 'a', T: 0.25, iv: 0.3 }]).length, 0, 'one expiry: no pair');
  assert.equal(vq.forwardVol([{ expiry: 'a', T: 0.25, iv: 0.3 }, { expiry: 'b', T: 0.25, iv: 0.3 }])[0].reason, 'same_time');
  const byDelta = vq.forwardVolByDelta({ ...vq.deltaGrid(ssvi(), ssviF()) });
  assert.ok(byDelta.ATMF.length === 4 && byDelta.ATMF.every(x => x.forwardVol !== null && !x.calendarArbitrage));
});

// SSVI (Gatheral-Jacquier) power-law: ρ = −0,6, η = 1, γ = 0,5 ⇒ η(1+|ρ|) = 1,6 ≤ 2 e θ crescente: nessun arbitraggio
const TS = [0.05, 0.1, 0.25, 0.5, 1.0], EXP = ['2035-01-20', '2035-02-07', '2035-04-03', '2035-07-03', '2036-01-02'];
const theta = (t: number) => (0.25 + 0.05 * Math.exp(-t)) ** 2 * t;
const ssviW = (k: number, t: number) => { const th = theta(t), phi = 1 / Math.sqrt(th), rho = -0.6;
  return th / 2 * (1 + rho * phi * k + Math.sqrt((phi * k + rho) ** 2 + 1 - rho * rho)); };
const fwd = (t: number) => 100 * Math.exp(0.03 * t);
const ssviF = () => Object.fromEntries(TS.map((t, i) => [EXP[i], fwd(t)]));
const ssvi = (): import('../../src/lib/vol-quant.ts').SmileSurface => ({ asOf: null, spot: 100, slices: TS.map((t, i) => ({ expiry: EXP[i], T: t, source: 'observed' as const,
  points: Array.from({ length: 17 }, (_, j) => -0.5 + 0.05 * j).map(k => ({ strike: fwd(t) * Math.exp(k), iv: Math.sqrt(ssviW(k, t) / t) })) })) });

test('arbitrageChecks: arbitrage-free SSVI gives 0 flags, even with zero noise tolerance', () => {
  const rep = vq.arbitrageChecks(ssvi(), ssviF());
  assert.deepEqual(rep.flags, []); assert.equal(rep.clean, true);
  assert.equal(rep.checked.calendarPairs, 4); assert.equal(rep.checked.butterflyTriplets, 5 * 15);
  assert.deepEqual(vq.arbitrageChecks(ssvi(), ssviF(), { ivNoise: 0 }).flags, [], 'strict tolerance');
});

test('arbitrageChecks: inverted calendar and concave smile are flagged, with size, expiries, strikes', () => {
  const flat = (expiry: string, T: number, iv: number, ks = [-0.2, -0.1, 0, 0.1, 0.2]) =>
    ({ expiry, T, source: 'observed' as const, points: ks.map(k => ({ strike: 100 * Math.exp(k), iv })) });
  const cal = vq.arbitrageChecks({ asOf: null, spot: 100, slices: [flat('A', 0.25, 0.30), flat('B', 0.5, 0.20)] }, { A: 100, B: 100 });
  const c = cal.flags.filter(f => f.kind === 'calendar');
  assert.equal(c.length, 1, 'one flag per contiguous k-run');
  assert.deepEqual(c[0].expiries, ['A', 'B']); assert.equal(c[0].origin, 'nodes');
  near(c[0].magnitude, 0.0225 - 0.02, 1e-12, 'w1 − w2'); near(c[0].volPoints, Math.sqrt(0.0225 / 0.5) - 0.2, 1e-12);
  assert.ok(c[0].k[0] < 0 && c[0].k[1] > 0 && Math.abs(c[0].k[0] + c[0].k[1]) < 1e-12, 'run around ATMF; the wings are inside their wider noise');
  assert.equal(c[0].strikes.length, 4);
  const strictCal = vq.arbitrageChecks({ asOf: null, spot: 100, slices: [flat('A', 0.25, 0.30), flat('B', 0.5, 0.20)] }, { A: 100, B: 100 }, { ivNoise: 0 }).flags.filter(f => f.kind === 'calendar');
  near(strictCal[0].k[0], -0.2, 1e-12); near(strictCal[0].k[1], 0.2, 1e-12);
  const noise = vq.arbitrageChecks({ asOf: null, spot: 100, slices: [flat('A', 0.25, 0.2502), flat('B', 0.26, 0.2450)] }, { A: 100, B: 100 });
  assert.deepEqual(noise.flags, [], 'an inversion inside the bid/ask noise is not flagged');

  // smile a gobba sull'ATM (T = 0,1): Durrleman g < 0 per |k| < 0,0325 (oracolo scipy): butterfly ai nodi −0,025, 0, +0,025
  const hump = (k: number) => 0.20 + 0.25 * Math.exp(-((k / 0.06) ** 2));
  const ks = Array.from({ length: 17 }, (_, i) => -0.2 + 0.025 * i);
  const humped = { expiry: 'C', T: 0.1, source: 'observed' as const, points: ks.map(k => ({ strike: 100 * Math.exp(k), iv: hump(k) })) };
  const bf = vq.arbitrageChecks({ asOf: null, spot: 100, slices: [humped] }, { C: 100 });
  const b = bf.flags.filter(f => f.kind === 'butterfly');
  // con il rumore per nodo ε·(1 + |k|/(σ√T)) resta solo la tripletta centrale (python con la stessa regola)
  assert.deepEqual(b.map(f => Math.round(f.k[1] * 1000) / 1000), [0], 'the triplet centred where the density is most negative');
  near(b[0].magnitude, 0.004058821828615243, 1e-12, 'B = λ1c1 − c2 + λ3c3 at k = 0 (scipy)');
  const bStrict = vq.arbitrageChecks({ asOf: null, spot: 100, slices: [humped] }, { C: 100 }, { ivNoise: 0 }).flags.filter(f => f.kind === 'butterfly');
  near(bStrict[0].magnitude, 0.0014275468133141019, 1e-12); near(bStrict[2].magnitude, 0.0014636853329474363, 1e-12);
  assert.deepEqual(bStrict.map(f => Math.round(f.k[1] * 1000) / 1000), [-0.025, 0, 0.025], 'zero tolerance: all three negative triplets (scipy)');
  assert.ok(b.every(f => f.magnitude > f.tolerance && f.strikes.length === 3 && f.origin === 'nodes' && f.source === 'observed'));
  // concavo ma senza arbitraggio: σ = 0,30 − 1,5k² ha g(k) ≥ 0,55 (scipy) — la concavità da sola non è un flag
  const mild = { expiry: 'M', T: 0.5, source: 'observed' as const, points: Array.from({ length: 13 }, (_, i) => -0.3 + 0.05 * i).map(k => ({ strike: 100 * Math.exp(k), iv: 0.30 - 1.5 * k * k })) };
  assert.deepEqual(vq.arbitrageChecks({ asOf: null, spot: 100, slices: [mild] }, { M: 100 }).flags, []);
  assert.equal(bf.checked.calendarPairs, 0, 'one expiry: no calendar pair');

  // artefatto: al nodo k = 0 di A la scadenza B è sotto solo per interpolazione (il nodo k = −0,2 di B è sopra)
  const A = { expiry: 'A', T: 0.40, source: 'observed' as const, points: [-0.2, -0.1, 0, 0.1, 0.2].map(k => ({ strike: 100 * Math.exp(k), iv: 0.3 })) };
  const B = { expiry: 'B', T: 0.41, source: 'observed' as const, points: [{ strike: 100 * Math.exp(-0.2), iv: 0.40 }, { strike: 100 * Math.exp(0.2), iv: 0.18 }, { strike: 100 * Math.exp(0.3), iv: 0.18 }] };
  const art = vq.arbitrageChecks({ asOf: null, spot: 100, slices: [A, B] }, { A: 100, B: 100 }).flags.filter(f => f.kind === 'calendar');
  assert.ok(art.length >= 1); assert.ok(art.some(f => f.origin === 'nodes'), 'at k = 0.2 both nodes agree: data');
  const B2 = { ...B, points: [{ strike: 100 * Math.exp(-0.2), iv: 0.40 }, { strike: 100 * Math.exp(0.25), iv: 0.10 }, { strike: 100 * Math.exp(0.3), iv: 0.10 }] };
  const only = vq.arbitrageChecks({ asOf: null, spot: 100, slices: [A, B2] }, { A: 100, B: 100 }).flags.filter(f => f.kind === 'calendar');
  assert.ok(only.length >= 1 && only.every(f => f.origin === 'interpolation'), 'violation only between B nodes: interpolation artifact');

  const skip = vq.arbitrageChecks(ssvi(), { [EXP[0]]: fwd(TS[0]) });
  assert.equal(skip.skipped.length, 4); assert.ok(skip.skipped.every(s => s.reason === 'forward_missing'));
});

test('arbitrageChecks: negative density of the interpolant itself is an interpolation flag (Durrleman, scipy)', () => {
  const T = 0.25, pts = [[-0.1, 0.2], [0, 0.01], [0.1, 0.012]].map(([k, w]) => ({ strike: 100 * Math.exp(k), iv: Math.sqrt(w / T) }));
  const rep = vq.arbitrageChecks({ asOf: null, spot: 100, slices: [{ expiry: 'D', T, source: 'grid', points: pts }] }, { D: 100 });
  const d = rep.flags.filter(f => f.kind === 'density');
  assert.equal(d.length, 1, 'only the steep piece');
  assert.deepEqual([d[0].origin, d[0].source, d[0].unit], ['interpolation', 'grid', 'g(k)']);
  near(d[0].magnitude, 18.66269097222222, 1e-9, 'min g over the 4 interior points');
});

test('smileFromChain: OTM at the forward junction, builder exclusions, no forward = empty', () => {
  const c = (type: 'call' | 'put', strike: number, iv: number | null, over = {}) => ({ type, strike, iv, bid: 1, ask: 1.1, oi: 5, volume: 0, adjusted: false, multiplier: 100, ...over });
  const chain = [c('put', 95, 0.3), c('call', 95, 0.29), c('put', 102, 0.27), c('call', 102, 0.26), c('call', 105, 0.25), c('put', 105, 0.255),
    c('call', 110, 0.24, { oi: 0 }), c('call', 115, 0.23, { adjusted: true }), c('call', 120, null), c('put', 90, 0.33, { bid: 0 })];
  const s = vq.smileFromChain('e', 0.2, chain, 103);
  assert.equal(s.source, 'observed');
  assert.deepEqual(s.points.map(p => ({ strike: p.strike, iv: p.iv })), [{ strike: 95, iv: 0.3 }, { strike: 102, iv: 0.27 }, { strike: 105, iv: 0.25 }], 'put below F (102 < 103 is a put), call above; illiquid/adjusted/no-IV/zero-bid out');
  assert.deepEqual(vq.smileFromChain('e', 0.2, chain, null).points, []);
});

// struttura con evento noto: base 30%, mossa utili 6% (v_e = 0,0036), utili il 2035-01-20
const EV = 0.0036, AS_OF = '2035-01-02';
const evTerm = (base: (t0: number, t1: number) => number) => {
  const pts = [['2035-01-17', 0.02], ['2035-01-24', 0.04], ['2035-02-21', 0.12], ['2035-03-21', 0.2]] as const;
  let w = 0, prevT = 0;
  return pts.map(([expiry, T]) => { w += base(prevT, T) * (T - prevT) + (expiry > '2035-01-20' && prevT < 0.03 ? EV : 0); prevT = T; return { expiry, T, iv: Math.sqrt(w / T) }; });
};

test('eventVol recovers a known earnings move; sloped base with pre+post', () => {
  const r = vq.eventVol(evTerm(() => 0.09), '2035-01-20', AS_OF);
  assert.equal(r.reason, null); assert.deepEqual([r.before, r.after, r.baseMethod], ['2035-01-17', '2035-01-24', 'pre+post']);
  near(r.eventVariance, EV, 1e-12); near(r.eventMove, 0.06, 1e-10); near(r.eventVolAnnualized, Math.sqrt(252 * EV), 1e-9);
  near((r.eventVolAnnualized as number) * Math.sqrt(1 / 252), 0.06, 1e-10, 'σ_event·√(1/252) = the event-day move');
  near(r.baseVol, 0.3, 1e-10);
  // base 0,08 prima, 0,09 nell'intervallo dell'evento, 0,10 dopo: la media pre+post è esatta
  const slope = vq.eventVol(evTerm((t0) => t0 < 0.015 ? 0.08 : t0 < 0.03 ? 0.09 : 0.10), '2035-01-20', AS_OF);
  near(slope.eventVariance, EV, 1e-12, 'sloped base'); near(slope.baseVol, 0.3, 1e-10);
  const pre = vq.eventVol(evTerm((t0) => t0 < 0.015 ? 0.08 : t0 < 0.03 ? 0.09 : 0.10), '2035-01-20', AS_OF, { base: 'pre' });
  assert.equal(pre.baseMethod, 'pre'); near(pre.eventVariance, EV + 0.01 * 0.02, 1e-12, 'pre-only base misses the slope');
  const first = vq.eventVol(evTerm(() => 0.09), '2035-01-10', AS_OF);
  assert.deepEqual([first.before, first.after, first.baseMethod], [null, '2035-01-17', 'post']);
});

test('eventVol n/a: ambiguous day, no expiry after, single expiry, no premium, no base', () => {
  const t = evTerm(() => 0.09);
  assert.equal(vq.eventVol(t, '2035-01-24', AS_OF).reason, 'event_timing_ambiguous');
  assert.equal(vq.eventVol(t, '2035-04-01', AS_OF).reason, 'no_expiry_after_event');
  assert.equal(vq.eventVol([t[0]], '2035-01-20', AS_OF).reason, 'single_expiry');
  assert.equal(vq.eventVol(t, null, AS_OF).reason, 'invalid_input');
  const flatTerm = [0.02, 0.04, 0.12].map((T, i) => ({ expiry: ['2035-01-17', '2035-01-24', '2035-02-21'][i], T, iv: 0.3 }));
  const none = vq.eventVol(flatTerm, '2035-01-20', AS_OF);
  assert.equal(none.reason, 'no_event_premium'); assert.equal(none.eventMove, null);
  assert.equal(vq.eventVol(flatTerm.slice(0, 2), '2035-01-10', AS_OF, { base: 'pre' }).reason, 'no_base_vol');
});

test('surfaceDiff: constant tenor × K/S, both dates carried, holes stay holes', () => {
  const cur = { asOf: '2035-01-10', spot: 101, moneyness: [0.9, 1, 1.1], slices: [{ expiry: 'c1', T: 20 / 365, iv: [0.35, 0.30, null] }, { expiry: 'c2', T: 50 / 365, iv: [0.32, 0.28, 0.27] }] };
  const prev = { asOf: '2035-01-03', spot: 99, moneyness: [0.95, 1, 1.1], slices: [{ expiry: 'p1', T: 25 / 365, iv: [0.3, 0.26, 0.25] }, { expiry: 'p2', T: 80 / 365, iv: [0.29, 0.27, 0.26] }] };
  const d = vq.surfaceDiff(cur, prev, { tenorsDays: [45, 50, 10, 60] });
  assert.deepEqual([d.currentAsOf, d.previousAsOf, d.currentSpot, d.previousSpot], ['2035-01-10', '2035-01-03', 101, 99]);
  assert.deepEqual(d.moneyness, [1, 1.1], 'only the K/S values both grids carry');
  near(d.rows[0].diff[0], 0.015022681933961779, 1e-12, 'numpy.interp in total variance');
  near(d.rows[1].current[0], 0.28, 1e-15, 'exact expiry'); assert.equal(d.rows[0].diff[1], null, 'a hole on one side');
  assert.equal(d.rows[2].reasons[0], 'outside_term'); assert.equal(d.rows[3].reasons[0], 'outside_term', 'never extrapolated');
  const fromBuilder = vq.moneynessSurfaceFromBuilder({ snapshot_at: 's', spot_est: 100, moneyness_grid: [1], slices: [{ expiry: 'e', t_years: 0.1, iv_grid: [0] }] });
  assert.deepEqual(fromBuilder.slices[0].iv, [null], 'a 0 in the grid is a hole');
});

test('expectedMoveCone: σ√T, F·σ√T and the LOGNORMAL band F·e^{±σ√T} (never negative), no forward = price n/a', () => {
  const [row] = vq.expectedMoveCone({ e: 100 }, [{ expiry: 'e', T: 0.25, iv: 0.2 }]);
  near(row.pct, 0.1, 1e-15); near(row.move, 10, 1e-12);
  near(row.low, 100 * Math.exp(-0.1), 1e-12); near(row.high, 100 * Math.exp(0.1), 1e-12); assert.equal(row.forwardSource, 'per-expiry');
  const [wide] = vq.expectedMoveCone(100, [{ expiry: 'e', T: 1, iv: 1.3 }]);
  near(wide.low, 27.25317930340126, 1e-10, 'scipy/math: the linear band would give −30'); near(wide.high, 366.92966676192447, 1e-9);
  near(wide.downPct, 0.7274682069659875, 1e-14); near(wide.upPct, 2.6692966676192444, 1e-13);
  assert.equal(wide.forwardSource, 'single-value', 'one number for every expiry is declared, it may be the spot');
  const rows = vq.expectedMoveCone({ a: { forward: 50 }, b: { forward: null, reason: 'rate_missing' } }, [{ expiry: 'b', T: 1, iv: 0.3 }, { expiry: 'a', T: 0.5, iv: 0.2 }, { expiry: 'c', T: 2, iv: null }]);
  assert.deepEqual(rows.map(r => r.expiry), ['a', 'b', 'c']);
  near(rows[0].move, 50 * 0.2 * Math.sqrt(0.5), 1e-12);
  assert.deepEqual([rows[1].move, rows[1].low, rows[1].reason], [null, null, 'rate_missing']); near(rows[1].pct, 0.3, 1e-15);
  assert.equal(rows[2].reason, 'iv_missing');
});

test('reason codes carry both languages', () => {
  assert.match(vq.reasonText('rate_missing', 'it'), /tasso/); assert.match(vq.reasonText('rate_missing', 'en'), /rate/);
  assert.equal(vq.reasonText(null), '');
});

/* ------------------------------------------------------------------ v2 (revisioni avversariali) */
type Surf = import('../../src/lib/vol-quant.ts').SmileSurface;
const flatSlice = (expiry: string, T: number, ivOf: (k: number) => number, ks: number[], source: 'observed' | 'grid' = 'observed') =>
  ({ expiry, T, source, points: ks.map(k => ({ strike: 100 * Math.exp(k), iv: ivOf(k) })) });

test('v2 rate: DGS3MO bond-equivalent percent → continuous decimal; a percent r is refused', () => {
  near(vq.rateFromDgs3mo(4.37), 0.04322941994481601, 1e-15, '2·ln(1 + y/2), python');
  near(vq.continuousFromBondEquivalent(0.0437), 0.04322941994481601, 1e-15);
  assert.equal(vq.rateFromDgs3mo(null), null);
  const f = vq.impliedForward([{ expiry: 'e', T: 0.75, r: 4.5, contracts: parChain() }], EU).e;
  assert.deepEqual([f.forward, f.reason], [null, 'invalid_input'], 'r = 4.5 is a percent: never F = 221');
  assert.notEqual(vq.impliedForward([{ expiry: 'e', T: 0.75, r: -0.004, contracts: parChain() }], EU).e.forward, null, 'small negative rates are legal');
});

test('v2 strikeForDelta: two roots 9e-6 apart inside one scan step are found (scipy scan: 3 roots)', () => {
  const sig = (k: number) => 0.25 + 0.05 * Math.exp(-(((k - 0.1) / 0.002) ** 2));
  const r = vq.strikeForDelta(100, 0.25, sig, 0.2771573719829023, 'call', { kMin: -1, kMax: 1 });
  assert.equal(r.reason, 'delta_ambiguous'); assert.match(r.detail as string, /^3 roots/);
  assert.equal(vq.strikeForDelta(100, 0.25, sig, 0.25, 'xyz' as 'call', { kMin: -1, kMax: 1 }).reason, 'invalid_input', 'unknown type is not a put');
  // σ con una V stretta (0,0004) attorno al NODO k0 = 0,1003, fra due punti della scansione: senza
  // valutare il nodo la V sparisce (scipy, scansione densa: radici 0,10016 / 0,10044 / 0,10447)
  const k0 = 0.1003, vSig = (k: number) => 0.25 - 0.03 * Math.max(0, 1 - Math.abs(k - k0) / 0.0002);
  assert.equal(vq.strikeForDelta(100, 0.25, vSig, 0.2196803372156837, 'call', { kMin: -1, kMax: 1, nodes: [k0] }).reason, 'delta_ambiguous', 'nodes are scanned');
});

test('v2 density: a concave kink of piecewise-linear w is a NEGATIVE point mass (finite differences, python)', () => {
  const T = 0.5, ks = [-0.2, -0.1, 0, 0.1, 0.2];
  const surf = (w0: number): Surf => ({ asOf: null, spot: 100, slices: [{ expiry: 'X', T, source: 'observed',
    points: ks.map((k, i) => ({ strike: 100 * Math.exp(k), iv: Math.sqrt([0.035, 0.026, w0, 0.026, 0.035][i] / T) })) }] });
  const strict = vq.arbitrageChecks(surf(0.028), { X: 100 }, { ivNoise: 0 }).flags.filter(f => f.unit === 'probability');
  assert.equal(strict.length, 1); assert.deepEqual([strict[0].kind, strict[0].origin], ['density', 'interpolation']);
  near(strict[0].magnitude, 0.04751607440844907, 1e-6, 'mass −4,75% (one-sided differences of the interpolant price)');
  near(strict[0].k[1], 0, 1e-15);
  assert.equal(vq.arbitrageChecks(surf(0.028), { X: 100 }).flags.filter(f => f.unit === 'probability').length, 0,
    'inside ±0,5 vol points the kink can vanish (python: favourable Δw′ = +0,0065)');
  const big = vq.arbitrageChecks(surf(0.032), { X: 100 }).flags.filter(f => f.unit === 'probability');
  assert.equal(big.length, 1); near(big[0].magnitude, 0.1332750854121656, 1e-6);
  assert.ok(big[0].magnitude > big[0].tolerance && big[0].tolerance > 0);
  assert.deepEqual(vq.arbitrageChecks(ssvi(), ssviF(), { ivNoise: 0 }).flags, [], 'convex SSVI w: no kink mass');
});

test('v2 junction: a put/call IV jump at K = F is flagged as junction, not as market data (python B)', () => {
  const c = (type: 'call' | 'put', strike: number, iv: number) => ({ type, strike, iv, bid: 2, ask: 2.2, oi: 5, volume: 0, adjusted: false, multiplier: 100 });
  const chain = [90, 95, 100, 105, 110].flatMap(K => [c('put', K, K < 100 ? 0.32 : 0.31), c('call', K, K < 100 ? 0.23 : 0.22)]);
  const slice = vq.smileFromChain('J', 0.25, chain, 100);
  assert.deepEqual(slice.points.map(p => p.iv), [0.32, 0.32, 0.22, 0.22, 0.22], 'K = F is a call (K ≥ F)');
  const rep = vq.arbitrageChecks({ asOf: null, spot: 100, slices: [slice] }, { J: 100 });
  const b = rep.flags.filter(f => f.kind === 'butterfly');
  assert.deepEqual(b.map(f => f.strikes), [[90, 95, 100]]);
  assert.equal(b[0].origin, 'junction'); assert.equal(b[0].test, 'model', 'mixed put/call without discount: model test'); near(b[0].magnitude, 0.006792037788277982, 1e-12);
});

test('v2 smileFromChain: same quote filters as the forward, IV range, counts by reason', () => {
  const c = (type: 'call' | 'put', strike: number, iv: number | null, over = {}) => ({ type, strike, iv, bid: 1, ask: 1.1, oi: 5, volume: 0, adjusted: false, multiplier: 100, ...over });
  const s = vq.smileFromChain('e', 0.2, [c('put', 90, 9.0), c('put', 95, 0.3, { ask: 3 }), c('put', 97, 0.3, { bid: null, ask: null }),
    c('call', 105, 0.25), c('call', 110, 0.24, { ask: 0.9 }), null as never, c('call', 115, 0.23)], 100);
  assert.deepEqual(s.points.map(p => ({ strike: p.strike, iv: p.iv })), [{ strike: 105, iv: 0.25 }, { strike: 115, iv: 0.23 }]);
  assert.deepEqual([s.points[0].type, s.points[0].bid, s.points[0].ask], ['call', 1, 1.1], 'quotes travel for executable checks');
  assert.deepEqual(s.excluded, { iv_out_of_range: 1, wide_spread: 1, no_bid_ask: 1, crossed: 1 });
  assert.equal(vq.smileFromChain('e', 0.2, [], null).reason, 'forward_missing');
});

test('v2 arbitrageChecks: nothing checked is NOT clean; true reason; same T; one expiry = calendar unchecked', () => {
  const s: Surf = { asOf: null, spot: 100, slices: [flatSlice('A', 0.25, () => 0.4, [-0.1, 0, 0.1]), flatSlice('B', 0.5, () => 0.2, [-0.1, 0, 0.1])] };
  const down = vq.impliedForward([{ expiry: 'A', T: 0.25, r: null, contracts: [] }, { expiry: 'B', T: 0.5, r: null, contracts: [] }]);
  const rep = vq.arbitrageChecks(s, down);
  assert.equal(rep.clean, null, 'FRED down: unchecked, never clean'); assert.equal(rep.cleanReason, 'rate_missing');
  assert.deepEqual(rep.skipped.map(x => x.reason), ['rate_missing', 'rate_missing']);
  assert.equal(vq.arbitrageChecks({ asOf: null, spot: 1, slices: [] }, {}).clean, null);
  assert.equal(vq.arbitrageChecks(null as never, {}).clean, null);
  const one = vq.arbitrageChecks({ asOf: null, spot: 100, slices: [s.slices[0]] }, { A: 100 });
  assert.deepEqual([one.clean, one.calendarChecked], [true, false]);
  const same = vq.arbitrageChecks({ asOf: null, spot: 100, slices: [flatSlice('A', 0.25, () => 0.4, [-0.1, 0, 0.1]),
    flatSlice('A2', 0.25, () => 0.4, [-0.1, 0, 0.1]), flatSlice('C', 0.5, () => 0.2, [-0.1, 0, 0.1])] }, { A: 100, A2: 100, C: 100 });
  assert.ok(same.skipped.some(x => x.reason === 'same_time'));
  assert.deepEqual(same.flags.filter(f => f.kind === 'calendar').map(f => f.expiries.join('>')).sort(), ['A2>C', 'A>C'], 'the expiry after a same-T pair is still compared');
});

test('v2 calendar: non-consecutive pair checked where no intermediate expiry covers k; tolerance in total variance', () => {
  const wide = [-0.3, -0.2, -0.1, 0, 0.1, 0.2, 0.3];
  const s: Surf = { asOf: null, spot: 100, slices: [flatSlice('S1', 0.25, () => 0.30, wide), flatSlice('S2', 0.5, () => 0.30, [-0.1, 0, 0.1]),
    flatSlice('S3', 0.75, k => k <= -0.2 + 1e-9 ? 0.15 : 0.30, wide)] };
  const cal = vq.arbitrageChecks(s, { S1: 100, S2: 100, S3: 100 }).flags.filter(f => f.kind === 'calendar');
  assert.equal(cal.length, 1); assert.deepEqual(cal[0].expiries, ['S1', 'S3']); assert.equal(cal[0].origin, 'nodes');
  near(cal[0].magnitude, 0.30 ** 2 * 0.25 - 0.15 ** 2 * 0.75, 1e-15, 'w1 − w3 on the wing S2 does not cover');
  near(cal[0].k[0], -0.3, 1e-12); assert.ok(cal[0].k[1] < -0.1);
  near(cal[0].tolerance, (0.09 - 0.295 ** 2) * 0.25 + (0.155 ** 2 - 0.15 ** 2) * 0.75, 1e-15, 'same unit as magnitude (calendar ε constant)');
  const strict = vq.arbitrageChecks(s, { S1: 100, S2: 100, S3: 100 }, { ivNoise: 0 }).flags.filter(f => f.kind === 'calendar');
  near(strict[0].k[0], -0.3, 1e-12);
  assert.equal(cal[0].ivNoise, 0.005);
  assert.equal(vq.arbitrageChecks(s, { S1: 100, S2: 100, S3: 100 }).checked.calendarPairs, 3);
});

test('v2 surfaceDiff: a hole breaks the interpolation, an inversion is n/a, a missing previous is declared', () => {
  const mk = (ivs: (number | null)[], Ts = [30, 60, 90]) => ({ asOf: 'a', spot: 100, moneyness: [1], slices: Ts.map((d, i) => ({ expiry: 'e' + i, T: d / 365, iv: [ivs[i]] })) });
  assert.equal(vq.surfaceDiff(mk([0.3, null, 0.3]), mk([0.3, 0.3, 0.3]), { tenorsDays: [45] }).rows[0].reasons[0], 'iv_missing', 'never interpolates 30→90 over the hole');
  assert.equal(vq.surfaceDiff(mk([0.5, 0.3, 0.3]), mk([0.3, 0.3, 0.3]), { tenorsDays: [45] }).rows[0].reasons[0], 'calendar_arbitrage');
  const noPrev = vq.surfaceDiff(mk([0.3, 0.3, 0.3]), null, { tenorsDays: [45] });
  assert.equal(noPrev.reason, 'previous_missing'); assert.deepEqual(noPrev.rows[0].reasons, ['previous_missing']);
  const q = vq.surfaceDiff({ ...mk([0.3, 0.3, 0.3]), qualified: false }, mk([0.3, 0.3, 0.3]), { tenorsDays: [45] });
  assert.equal(q.currentQualified, false);
});

test('v2 builder payload: error, spot proxy and unqualified grid are declared', () => {
  const payload = { spot_est: 100, snapshot_at: 's', moneyness_grid: [0.9, 1, 1.1], slices: [{ expiry: 'e', t_years: 0.1, iv_grid: [0.3, 0.25, 0.26] }] };
  const err = vq.surfaceFromBuilder({ ...payload, error: 'boom' });
  assert.deepEqual([err.slices.length, err.reason, err.detail], [0, 'surface_error', 'boom']);
  const proxy = vq.surfaceFromBuilder({ ...payload, spot_proxy: 'proxy_strike_delta50' });
  assert.equal(vq.deltaGrid(proxy, { e: 100 }).rows[0].reason, 'spot_proxy', 'not smile_too_short');
  const unq = vq.surfaceFromBuilder({ ...payload, iv_grid_qualified: false, spot_fallback: true });
  const row = vq.deltaGrid(unq, { e: 100 }).rows[0];
  assert.equal(row.qualified, false); assert.notEqual(row.cells.ATMF.iv, null, 'strikes are the builder ones: values kept, label shown');
  assert.equal(vq.moneynessSurfaceFromBuilder({ ...payload, spot_proxy: 'x' }).reason, 'spot_proxy');
  assert.equal(vq.moneynessSurfaceFromBuilder({ ...payload, iv_grid_qualified: false }).qualified, false);
});

test('v2 eventVol: snapshot date required, past earnings refused, inversion is calendar, dates validated', () => {
  const t = evTerm(() => 0.09);
  assert.equal(vq.eventVol(t, '2035-01-20', null).reason, 'as_of_missing');
  assert.equal(vq.eventVol(t, '2035-01-20', '2035-01-25').reason, 'event_past', 'a stale next_earnings never becomes a premium');
  assert.equal(vq.eventVol(t, '2035-01-20', '2035-01-20').reason, 'event_timing_ambiguous');
  near(vq.eventVol(t, '2035-01-20T21:00:00Z', '2035-01-02T15:00:00Z').eventVariance, EV, 1e-12, 'timestamps truncated to the day');
  assert.equal(vq.eventVol(t, '2035-13-45', AS_OF).reason, 'invalid_input');
  const inv = [{ expiry: '2035-01-10', T: 10 / 365, iv: 0.50 }, { expiry: '2035-01-24', T: 24 / 365, iv: 0.30 }, { expiry: '2035-02-21', T: 52 / 365, iv: 0.30 }];
  assert.equal(vq.eventVol(inv, '2035-01-20', AS_OF).reason, 'calendar_arbitrage', 'w falls across the event');
  const sameT = [{ expiry: '2035-01-17', T: 0.02, iv: 0.3 }, { expiry: '2035-01-24', T: 0.02, iv: 0.4 }, { expiry: '2035-02-21', T: 0.12, iv: 0.3 }];
  assert.equal(vq.eventVol(sameT, '2035-01-20', AS_OF).reason, 'same_time');
});

test('v2 robustness: null elements inside arrays never throw', () => {
  assert.doesNotThrow(() => {
    vq.impliedForward([null as never, { expiry: 'e', T: 0.75, r: 0.045, contracts: [null as never, ...parChain()] }]);
    vq.deltaGrid({ asOf: null, spot: 1, slices: [null as never] }, {});
    vq.arbitrageChecks({ asOf: null, spot: 1, slices: [null as never] }, {});
    vq.skewMetrics({ rows: [{ expiry: 'x' } as never] } as never);
    vq.expectedMoveCone(100, [null as never]);
    vq.eventVol([null as never, { expiry: '2035-01-10', T: 0.1, iv: 0.3 }], '2035-01-20', AS_OF);
    vq.forwardVol([null as never, { expiry: 'a', T: 0.1, iv: 0.3 }]);
    vq.surfaceFromBuilder({ spot_est: 100, moneyness_grid: [1], slices: [null] });
  });
});

/* ------------------------------------------------------------------ v2 seconda revisione */
const AM: { r: number; q: number; T: number; S: number; F: number; contracts: never[] }[] =
  JSON.parse(readFileSync(new URL('./vol-quant-american.json', import.meta.url), 'utf8'));

test('v2 american: de-americanized parity recovers the true forward within 5 bp (python CRR 2000 steps, 12 cases)', () => {
  assert.equal(AM.length, 12);
  for (const c of AM) {
    const f = vq.impliedForward([{ expiry: 'e', T: c.T, r: c.r, contracts: c.contracts, spot: c.S }]).e;
    assert.equal(f.reason, null); assert.equal(f.exercise, 'american');
    assert.ok(Math.abs(f.forward as number / c.F - 1) < 0.0005, `r ${c.r} q ${c.q} T ${c.T}: F ${f.forward} vs ${c.F}`);
    near(f.impliedDividendYield, c.q, 1e-4, `q implied r ${c.r} q ${c.q} T ${c.T}`);
    assert.ok((f.deamericanizationResidual as number) < 1e-6 * c.F * 10);
    assert.ok(f.pairs.every(p => p.callEep >= 0 && p.putEep >= 0));
  }
  // la parità europea sui prezzi americani è distorta: −0,57% a 1 anno (q = 0, r = 4,5%)
  const one = AM.find(c => c.q === 0 && c.T === 1) as typeof AM[number];
  const raw = vq.impliedForward([{ expiry: 'e', T: 1, r: one.r, contracts: one.contracts, spot: one.S }], { exercise: 'european' }).e;
  assert.ok(raw.forward as number / one.F - 1 < -0.005, 'the bias the correction removes');
  const am = vq.impliedForward([{ expiry: 'e', T: 1, r: one.r, contracts: one.contracts, spot: one.S }]).e;
  near(am.rawForward, raw.forward as number, 1e-12, 'rawForward = the uncorrected parity');
  assert.equal(vq.impliedForward([{ expiry: 'e', T: 1, r: one.r, contracts: one.contracts }]).e.reason, 'spot_missing');
  const noIv = one.contracts.map(c => ({ ...(c as object), iv: null }));
  const n = vq.impliedForward([{ expiry: 'e', T: 1, r: one.r, contracts: noIv as never, spot: one.S }]).e;
  assert.equal(n.reason, 'no_valid_pairs'); assert.match(n.detail as string, /without provider IV/);
});

test('v2 crrPrice: European tree converges to Black-Scholes, American put ≥ European', () => {
  // BS put S = K = 100, T = 1, σ = 0,3, r = 4,5%, q = 0 (scipy): 9.591016310550259
  near(vq.crrPrice('put', 100, 100, 1, 0.3, 0.045, 0, false, 2000) as number, 9.591016310550259, 2e-3, 'BS put (scipy) within the tree discretisation');
  assert.ok((vq.crrPrice('put', 100, 100, 1, 0.3, 0.045, 0, true) as number) > (vq.crrPrice('put', 100, 100, 1, 0.3, 0.045, 0, false) as number));
  near(vq.crrPrice('call', 100, 100, 1, 0.3, 0.045, 0, true) as number, vq.crrPrice('call', 100, 100, 1, 0.3, 0.045, 0, false) as number, 1e-12, 'no dividend: never exercise a call early');
  assert.equal(vq.crrPrice('put', 100, 100, 0, 0.3, 0.045, 0), null);
});

test('v2 executable butterfly and vertical on bid/ask (hand arithmetic)', () => {
  const pt = (K: number, bid: number, ask: number, type: 'call' | 'put' = 'call') => ({ strike: K, iv: 0.3, type, bid, ask });
  const slice = { expiry: 'X', T: 0.25, source: 'observed' as const, points: [pt(90, 12, 12.2), pt(95, 8.6, 8.8), pt(100, 4.5, 4.6), pt(105, 4.7, 4.8)] };
  const rep = vq.arbitrageChecks({ asOf: null, spot: 100, slices: [slice] }, { X: 100 });
  const bf = rep.flags.filter(f => f.kind === 'butterfly');
  // (90, 95, 100): ½·12,2 − 8,6 + ½·4,6 = −0,2 → comprabile a credito 0,2
  assert.deepEqual(bf.map(f => f.strikes), [[90, 95, 100]], '(95, 100, 105): ½·8,8 − 4,5 + ½·4,8 = 2,3 ≥ 0, clean');
  assert.ok(bf.every(f => f.test === 'executable' && f.unit === 'price' && f.tolerance === 0));
  near(bf[0].magnitude, 0.2, 1e-12);
  const v = rep.flags.filter(f => f.kind === 'vertical');
  // call 100/105: ask 4,6 < bid 4,7 → lo spread si compra a credito 0,1
  assert.deepEqual(v.map(f => f.strikes), [[100, 105]]); near(v[0].magnitude, 0.1, 1e-12); assert.equal(v[0].test, 'executable');
  assert.equal(rep.checked.executableTriplets, 2);
});

test('v2 executable across the junction with the discount: puts become calls by parity', () => {
  // put 95 e call 100/105 con D = e^{−0,05·0,25}: C(95) = P + D·(F − 95). F si elide nel butterfly.
  const D = Math.exp(-0.05 * 0.25), F = 100;
  const c = (type: 'call' | 'put', strike: number, bid: number, ask: number) => ({ type, strike, iv: 0.3, bid, ask, oi: 5, volume: 0, adjusted: false, multiplier: 100 });
  const chain = [c('put', 90, 1.0, 1.05), c('put', 95, 2.2, 2.3), c('call', 100, 4.8, 4.9), c('call', 105, 2.0, 2.05)];
  const slice = vq.smileFromChain('J', 0.25, chain, F, { r: 0.05 });
  near(slice.discount, D, 1e-15);
  const rep = vq.arbitrageChecks({ asOf: null, spot: 100, slices: [slice] }, { J: F });
  assert.equal(rep.checked.executableTriplets, 2);
  // (95, 100, 105): ½·(2,3 + D·5) − 4,8 + ½·2,05 = −0,156
  const cost = 0.5 * (2.3 + D * 5) - 4.8 + 0.5 * 2.05;
  const b = rep.flags.filter(f => f.kind === 'butterfly' && f.strikes[0] === 95);
  if (cost < 0) { assert.equal(b.length, 1); near(b[0].magnitude, -cost, 1e-12); assert.equal(b[0].origin, 'junction'); }
  else assert.equal(b.length, 0);
  assert.ok(cost < 0, 'the case is built to be an arbitrage');
  const noD = vq.arbitrageChecks({ asOf: null, spot: 100, slices: [vq.smileFromChain('J', 0.25, chain, F)] }, { J: F });
  assert.equal(noD.checked.executableTriplets, 0, 'without r the mixed triplets fall back to the model test');
});

test('v2 validation: ivNoise, duplicate expiries, only puts', () => {
  const s: Surf = { asOf: null, spot: 100, slices: [flatSlice('A', 0.25, () => 0.3, [-0.1, 0, 0.1]), flatSlice('B', 0.5, () => 0.2, [-0.1, 0, 0.1])] };
  for (const bad of [NaN, -0.01, Infinity]) {
    const r = vq.arbitrageChecks(s, { A: 100, B: 100 }, { ivNoise: bad });
    assert.deepEqual([r.clean, r.cleanReason, r.flags.length], [null, 'invalid_input', 0], `ivNoise ${bad}: never a false clean`);
  }
  assert.equal(vq.forwardVol([{ expiry: 'a', T: 0.25, iv: 0.3 }, { expiry: 'b', T: 0.5, iv: 0.2 }], { ivNoise: NaN })[0].reason, 'invalid_input');
  const dup = vq.impliedForward([{ expiry: 'E', T: 0.75, r: 0.045, contracts: parChain() }, { expiry: 'E', T: 0.75, r: 0.045, contracts: parChain() }], EU);
  assert.deepEqual([dup.E.forward, dup.E.reason], [null, 'duplicate_input'], 'two inputs for one expiry: neither wins silently');
  assert.equal(vq.impliedForward([{ expiry: 'E', T: 0.75, r: 0.045, contracts: parChain().filter(c => c.type === 'put') }], EU).E.reason, 'no_valid_pairs');
  const echo = vq.impliedForward([{ expiry: 'E', T: NaN, r: 'x' as never, contracts: [] }]).E;
  assert.deepEqual([echo.T, echo.r], [null, null], 'n/a rows copy only valid values');
  const [cone] = vq.expectedMoveCone(100, [{ expiry: 'e', T: NaN, iv: -0.2 }]);
  assert.deepEqual([cone.T, cone.iv, cone.reason], [null, null, 'time_missing']);
  assert.equal(vq.expectedMoveCone(100, [{ expiry: 'e', T: 1, iv: 9 }])[0].reason, 'iv_out_of_range');
  const g = vq.deltaGrid({ asOf: null, spot: 100, slices: [{ ...flatSlice('A', 0.25, () => 0.3, [-0.1, -0.05, 0, 0.05, 0.1]),
    points: [...flatSlice('A', 0.25, () => 0.3, [-0.1, -0.05, 0, 0.05, 0.1]).points, { strike: 100, iv: 0.5 }, { strike: 101, iv: 7 }] }] }, { A: 100 }).rows[0];
  assert.deepEqual([g.duplicates, g.outOfRange], [1, 1], 'duplicates averaged and out-of-range IVs dropped, both counted');
});

test('v2 surfaceFromChains: observed surface is the default input of the delta grid', () => {
  const c = (type: 'call' | 'put', strike: number, iv: number) => ({ type, strike, iv, bid: 1, ask: 1.05, oi: 5, volume: 0, adjusted: false, multiplier: 100 });
  const ks = [80, 85, 90, 95, 100, 105, 110, 115, 120];
  const chain = ks.flatMap(K => [c('put', K, 0.3 - 0.002 * (K - 100)), c('call', K, 0.3 - 0.002 * (K - 100))]);
  const surf = vq.surfaceFromChains([{ expiry: 'A', T: 0.25, contracts: chain }, { expiry: 'B', T: 0.5, contracts: chain }],
    { A: 100, B: { forward: null, reason: 'rate_missing' } }, { asOf: '2035-01-02', spot: 100 });
  assert.equal(surf.slices[0].source, 'observed'); assert.equal(surf.slices[0].points.length, 9);
  const g = vq.deltaGrid(surf, { A: 100, B: { forward: null, reason: 'rate_missing' } });
  assert.equal(g.rows[0].cells.ATMF.reason, null); near(g.rows[0].cells.ATMF.iv, 0.3, 1e-12);
  assert.equal(g.rows[1].reason, 'rate_missing');
  assert.equal(vq.LABELS.ATMF, 'ATMF (K = F)'); assert.match(vq.LABELS.BF, /non strangle/);
  assert.ok('atmf' in vq.skewMetrics(g)[0], 'skew field is atmf, never «atm»');
});

test('v2 surfaceDiff on K/F: an unchanged K/F smile gives 0 although the spot moved; slide and flags exposed', () => {
  // w/T = 0,0625 − 0,05·ln(K/F): lineare in ln K, quindi l'interpolazione è esatta
  const sig = (x: number) => Math.sqrt(0.0625 - 0.05 * Math.log(x));
  const m = [0.8, 0.85, 0.9, 0.95, 1, 1.05, 1.1, 1.15, 1.2];
  const snap = (S: number, F: (T: number) => number, asOf: string) => ({ asOf, spot: S, moneyness: m, spotQualified: true, spotFallback: false,
    slices: [30, 60].map(d => ({ expiry: 'e' + d, T: d / 365, iv: m.map(mm => sig(mm * S / F(d / 365))) })) });
  // carry diverso fra le due date (+4% e −6%): solo l'asse K/F lo assorbe
  const cg = (S: number) => S === 105 ? 0.04 : -0.06;
  const cur = snap(105, T => 105 * Math.exp(cg(105) * T), '2035-01-10'), prev = snap(100, T => 100 * Math.exp(cg(100) * T), '2035-01-03');
  const fw = (S: number) => ({ e30: S * Math.exp(cg(S) * 30 / 365), e60: S * Math.exp(cg(S) * 60 / 365) });
  const d = vq.surfaceDiff(cur, prev, { tenorsDays: [45], currentForwards: fw(105), previousForwards: fw(100) });
  assert.equal(d.axis, 'K/F'); near(d.dLnSpot, Math.log(1.05), 1e-15);
  const inner = d.rows[0].diff.filter(x => x !== null) as number[];
  assert.ok(inner.length >= 5); assert.ok(inner.every(x => Math.abs(x) < 1e-12), 'sticky-K/F smile: zero diff on the K/F axis');
  const ks = vq.surfaceDiff(cur, prev, { tenorsDays: [45] });
  assert.equal(ks.axis, 'K/S'); assert.equal(ks.currentSpotQualified, true); assert.equal(ks.previousSpotFallback, false);
  const i = 4, p = ks.rows[0].previous, x = m.map(Math.log);
  near(ks.rows[0].skewSlide[i], (p[i + 1] as number - (p[i - 1] as number)) / (x[i + 1] - x[i - 1]) * Math.log(1.05), 1e-15, 'central slope × Δln S');
  near(ks.rows[0].diffExSlide[i], (ks.rows[0].diff[i] as number) - (ks.rows[0].skewSlide[i] as number), 1e-15);
  const missingF = vq.surfaceDiff(cur, prev, { tenorsDays: [45], currentForwards: { e30: 105 }, previousForwards: fw(100) });
  assert.ok(missingF.rows[0].reasons.every(r => r === 'forward_missing'), 'a slice without forward is a hole with its reason');
  const dupGrid = vq.surfaceDiff({ ...cur, moneyness: [1, 1, ...m.slice(2)] }, prev, { tenorsDays: [45] });
  assert.deepEqual(dupGrid.duplicateMoneyness, [1]); assert.ok(!dupGrid.moneyness.includes(1), 'a duplicated K/S column is dropped, never one at random');
});

test('v2 reviewer survivors: liquidity filter, duplicates by OI, even median, F ≤ 0, averaged duplicates', () => {
  const dead = parChain().map(c => ({ ...c, oi: 0, volume: 0 }));
  assert.equal(vq.impliedForward([{ expiry: 'e', T: 0.75, r: 0.045, contracts: dead }], EU).e.reason, 'no_valid_pairs', 'OI = volume = 0 is out');
  assert.equal(vq.smileFromChain('e', 0.75, dead.map(c => ({ ...c, iv: 0.3 })), 104.3).points.length, 0);
  // duplicato del contratto call 100: quello con OI maggiore è il prezzo vero, l'altro è sbagliato di 1
  const dup = [...parChain(), { ...q('call', 100, PAR[2][1] + 1), oi: 3 }];
  near(vq.impliedForward([{ expiry: 'e', T: 0.75, r: 0.045, contracts: dup }], EU).e.forward, 104.3, 1e-9, 'the larger OI wins');
  const dupS = vq.smileFromChain('e', 0.75, [q('call', 110, 1), { ...q('call', 110, 1), oi: 99, iv: 0.4 }, q('call', 115, 1)].map(c => ({ iv: 0.3, ...c })), 104.3);
  assert.equal(dupS.points.find(p => p.strike === 110)?.iv, 0.4);
  // mediana pari = media dei due centrali: 2 coppie, una spostata di +0,6 sul mid della call
  const two = [q('call', 100, PAR[2][1] + 0.6), q('put', 100, PAR[2][2]), q('call', 105, PAR[3][1]), q('put', 105, PAR[3][2])];
  near(vq.impliedForward([{ expiry: 'e', T: 0.75, r: 0.045, contracts: two }], EU).e.forward, 104.3 + Math.exp(0.045 * 0.75) * 0.3, 1e-9);
  for (const bad of [0, -5]) assert.equal(vq.deltaGrid(sampled(), { '2035-07-03': bad }).rows[0].reason, 'forward_missing', `F = ${bad}`);
  for (const bad of [0, -5]) assert.deepEqual(vq.expectedMoveCone({ e: { forward: bad } }, [{ expiry: 'e', T: 1, iv: 0.2 }]).map(r => [r.forward, r.move, r.reason]), [[null, null, 'forward_missing']]);
  const tie = vq.smileFromChain('e', 0.75, [{ ...q('call', 110, 1), iv: 0.3, ask: 1.2 }, { ...q('call', 110, 1), iv: 0.35 }, { ...q('call', 115, 1), iv: 0.3 }], 104.3);
  assert.equal(tie.points.find(p => p.strike === 110)?.iv, 0.35, 'same OI: the tighter spread wins');
  const g = vq.deltaGrid({ asOf: null, spot: 100, slices: [{ expiry: 'A', T: 0.25, source: 'observed',
    points: [90, 95, 100, 105, 110].map(K => ({ strike: K, iv: 0.3 })).concat([{ strike: 100, iv: 0.5 }]) }] }, { A: 100 }).rows[0];
  near(g.cells.ATMF.iv, 0.4, 1e-12, 'duplicated strike: the declared average, not the max');
});

test('v2 reviewer survivors: rows and smiles sorted by T whatever the input order; model vertical (python)', () => {
  const s = ssvi(); s.slices.reverse();
  assert.deepEqual(vq.deltaGrid(s, ssviF()).rows.map(r => r.expiry), EXP);
  const cal = vq.arbitrageChecks({ asOf: null, spot: 100, slices: [flatSlice('B', 0.5, () => 0.20, [-0.1, 0, 0.1]), flatSlice('A', 0.25, () => 0.30, [-0.1, 0, 0.1])] }, { A: 100, B: 100 });
  assert.deepEqual(cal.flags.filter(f => f.kind === 'calendar').map(f => f.expiries), [['A', 'B']]);
  const vs = { expiry: 'V', T: 0.25, source: 'observed' as const, points: [[-0.1, 0.25], [-0.05, 1.5], [0, 0.1]].map(([k, iv]) => ({ strike: 100 * Math.exp(k), iv })) };
  const all = vq.arbitrageChecks({ asOf: null, spot: 100, slices: [vs] }, { V: 100 }).flags.filter(f => f.kind === 'vertical');
  assert.equal(all.length, 2, 'the rising piece (call price up with K) and the falling one (slope < −1)');
  const v = all.filter(f => Math.abs(f.k[0] + 0.05) < 1e-12);
  assert.equal(v.length, 1); assert.equal(v[0].test, 'model'); assert.equal(v[0].unit, 'slope');
  near(v[0].magnitude, 4.951442286559459, 1e-9, 'slope below −1 by this much (python)'); near(v[0].k[0], -0.05, 1e-12);
});

/* ------------------------------------------------------------------ v3 (ri-verifica della v2) */
test('v3 density: a noisy segment below the noise threshold gives no density flag (python: raw g −1,17, favourable +0,53)', () => {
  const T = 0.02, slice = flatSlice('N', T, k => (Math.abs(k) < 1e-12 ? 0.18 : 0.2), [-0.002, 0, 0.002]);
  assert.equal(vq.arbitrageChecks({ asOf: null, spot: 100, slices: [slice] }, { N: 100 }).flags.filter(f => f.kind === 'density').length, 0);
  const strict = vq.arbitrageChecks({ asOf: null, spot: 100, slices: [slice] }, { N: 100 }, { ivNoise: 0 }).flags.filter(f => f.unit === 'g(k)');
  assert.ok(strict.some(f => Math.abs(f.magnitude - 1.1732080429868308) < 1e-9), 'with zero noise the raw Durrleman g of the right segment (python)');
});

test('v3 american: each leg uses ITS OWN provider IV in the tree (a wrong call IV is harmless at q = 0, a wrong put IV is not)', () => {
  for (const c of AM.filter(x => x.q === 0 && x.T >= 1)) {
    const cs = c.contracts.map(x => ((x as { type: string }).type === 'call' ? { ...(x as object), iv: 0.20 } : x));
    const f = vq.impliedForward([{ expiry: 'e', T: c.T, r: c.r, contracts: cs as never, spot: c.S }]).e;
    // misurato: IV della call sbagliata 0,05 bp; la stessa IV sulla put (gambe scambiate) 8 bp a 1 anno, 26 bp a 2
    assert.ok(Math.abs(f.forward as number / c.F - 1) < 0.00005, `T ${c.T}: ${f.forward} vs ${c.F}`);
  }
});

test('v3 indicative: where bid/ask exist the executable test rules, model and density flags are only indicative', () => {
  const hump = (k: number) => 0.20 + 0.25 * Math.exp(-((k / 0.06) ** 2));
  const ks = Array.from({ length: 17 }, (_, i) => -0.2 + 0.025 * i);
  const pts = ks.map(k => { const C = vq.forwardPrice('call', 100, 100 * Math.exp(k), 0.1, hump(k)) as number;
    return { strike: 100 * Math.exp(k), iv: hump(k), type: 'call' as const, bid: 0.7 * C, ask: 1.3 * C }; });
  const quoted = vq.arbitrageChecks({ asOf: null, spot: 100, slices: [{ expiry: 'H', T: 0.1, source: 'observed', points: pts }] }, { H: 100 });
  assert.equal(quoted.flags.filter(f => f.test === 'executable').length, 0, 'the ±30% quotes leave no executable arbitrage');
  assert.ok(quoted.flags.length > 0 && quoted.flags.every(f => f.indicative), 'the density of the interpolant is still seen, as indicative');
  assert.equal(quoted.clean, true, 'indicative flags do not make the surface dirty');
  const bare = vq.arbitrageChecks({ asOf: null, spot: 100, slices: [{ expiry: 'H', T: 0.1, source: 'observed', points: pts.map(p => ({ strike: p.strike, iv: p.iv })) }] }, { H: 100 });
  assert.equal(bare.clean, false); assert.ok(bare.flags.every(f => !f.indicative));
});

test('v3 time range: 0 < T ≤ 30 years, an absurd T is invalid_input', () => {
  assert.equal(vq.MAX_T_YEARS, 30);
  assert.equal(vq.impliedForward([{ expiry: 'e', T: 31, r: 0.04, contracts: parChain() }], EU).e.reason, 'invalid_input');
  assert.equal(vq.impliedForward([{ expiry: 'e', T: 0, r: 0.04, contracts: parChain() }], EU).e.reason, 'time_missing');
  assert.equal(vq.deltaGrid({ ...sampled(), slices: [{ ...sampled().slices[0], T: 1e300 }] }, { '2035-07-03': F1 }).rows[0].reason, 'invalid_input');
  assert.equal(vq.expectedMoveCone(100, [{ expiry: 'e', T: 1e300, iv: 0.06 }])[0].reason, 'invalid_input');
  assert.equal(vq.forwardVol([{ expiry: 'a', T: 0.5, iv: 0.3 }, { expiry: 'b', T: 40, iv: 0.3 }])[0].reason, 'invalid_input');
  assert.equal(vq.deltaFromForward('call', 100, 100, 31, 0.2), null);
  assert.equal(vq.strikeForDelta(100, 31, () => 0.2, 0.25, 'call', { kMin: -1, kMax: 1 }).reason, 'invalid_input');
});
