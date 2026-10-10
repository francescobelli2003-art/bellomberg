// Option builder (09/10/2026, Opus 5.5) — pure logic between the Polygon chain and the backend
// engine. NO pricing here: payoff, P/L, greeks, breakevens, extremes and probability of profit
// come from POST /options/strategy/simulate (bellomberg/portfolio/options_strategy.py), the single
// source of truth. This module only decides WHICH contract a leg is, WHAT price/IV it carries and
// whether the leg is computable; a missing datum is a declared reason, never a 0.
//
// Audit 09/10 (AUDIT-OPTION-BUILDER.md) covered here:
//   H1  a leg is a reference (type, strike, expiry) into the loaded chain: bid/ask/mid/IV are
//       re-read on every change of side, type, strike or expiry; manual overrides are dropped
//       when the contract changes (and a manual price when the side flips).
//   M1  every leg reads its OWN contract's IV (no inheritance across legs).
//   M2  days to expiry are fractional, to 16:00 America/New_York of the expiry date, computed
//       at request time; an expired contract is declared, never clamped to 0.
//   L2  days are recomputed from the stored date at every request (no frozen day counts).
//   L3  numbers shown in fields are rounded (no float noise).
//   L5  quantities, strikes, prices and IV are read with leggiNumero (> 0) by the caller.

export type Side = 'buy' | 'sell';
export type OptionType = 'call' | 'put';
export type PriceMode = 'mid' | 'natural';
/** How a leg that outlives the first expiry is valued along the way (engine `vol_model`). */
export type VolModel = 'forward' | 'constant';

/** The subset of the backend chain row the builder reads. */
export interface ChainContract {
  contract: string | null; type: OptionType; strike: number | null; expiry: string;
  iv: number | null; bid: number | null; ask: number | null; mid: number | null;
  multiplier: number | null; adjusted?: boolean; quote_timestamp: string | null;
  quote_timeframe: string | null; quality: string[]; _source: string;
}

export interface BuilderLeg {
  id: string; kind: 'option' | 'stock'; side: Side;
  /** contracts for options, shares for stock (text as typed) */
  qty: string;
  type?: OptionType; expiry?: string; strike?: number;
  /** manual overrides (text as typed, declared in the UI); empty = from the chain */
  iv?: string; price?: string;
}

export type Chains = ReadonlyMap<string, readonly ChainContract[]>;

let seq = 0;
export const legId = () => `ob-${Date.now().toString(36)}-${++seq}`;

export function optionLeg(type: OptionType, side: Side, expiry: string, strike: number, qty = 1): BuilderLeg {
  return { id: legId(), kind: 'option', side, qty: String(qty), type, expiry, strike };
}
export function stockLeg(side: Side, shares: number): BuilderLeg {
  return { id: legId(), kind: 'stock', side, qty: String(shares) };
}

/** Changes that identify another contract drop what belonged to the previous one (H1). */
export function editLeg(leg: BuilderLeg, change: Partial<Pick<BuilderLeg, 'side' | 'type' | 'expiry' | 'strike' | 'qty' | 'iv' | 'price'>>): BuilderLeg {
  const next = { ...leg, ...change };
  const contractChanged = ('type' in change && change.type !== leg.type) || ('expiry' in change && change.expiry !== leg.expiry)
    || ('strike' in change && change.strike !== leg.strike);
  if (contractChanged) { next.iv = ''; next.price = ''; }
  if ('side' in change && change.side !== leg.side && !('price' in change)) next.price = '';
  return next;
}
export const invertLeg = (leg: BuilderLeg) => editLeg(leg, { side: leg.side === 'buy' ? 'sell' : 'buy' });

// ── time to expiry ──────────────────────────────────────────────────────────

const NY = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hourCycle: 'h23',
  year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });

function nyOffsetMinutes(utcMs: number): number {
  const parts = NY.formatToParts(new Date(utcMs));
  const get = (type: string) => Number(parts.find(p => p.type === type)?.value);
  const wall = Date.UTC(get('year'), get('month') - 1, get('day'), get('hour') % 24, get('minute'), get('second'));
  return Math.round((wall - utcMs) / 60000);
}

/** UTC instant of 16:00 New York time on the expiry date (US equity options close). */
export function expiryCloseUtc(expiry: string): number {
  const [y, m, d] = expiry.split('-').map(Number);
  const nominal = Date.UTC(y, m - 1, d, 16, 0, 0);
  return nominal - nyOffsetMinutes(nominal + 5 * 3600000) * 60000;
}

/** Fractional calendar days (ACT/365 numerator) from `now` to the 16:00 ET close. */
export function daysToClose(expiry: string, now: number): number {
  return (expiryCloseUtc(expiry) - now) / 86400000;
}

// ── chain reading ───────────────────────────────────────────────────────────

const usable = (row: ChainContract) => row.strike != null && !row.adjusted && (row.type === 'call' || row.type === 'put');

export function findContract(chains: Chains, leg: BuilderLeg): ChainContract | null {
  if (leg.kind !== 'option' || !leg.expiry || leg.strike == null) return null;
  return chains.get(leg.expiry)?.find(row => row.type === leg.type && row.strike === leg.strike && usable(row)) ?? null;
}

/** True when the leg's (type, strike, expiry) exists in the chain ONLY as an adjusted contract
 *  (corporate action: non-standard deliverable). It is excluded, and said so (review L5). */
export function onlyAdjusted(chains: Chains, leg: BuilderLeg): boolean {
  if (leg.kind !== 'option' || !leg.expiry || leg.strike == null) return false;
  const rows = chains.get(leg.expiry)?.filter(row => row.type === leg.type && row.strike === leg.strike) ?? [];
  return rows.length > 0 && rows.every(row => row.adjusted);
}

export function strikesOf(chain: readonly ChainContract[] | undefined, type?: OptionType): number[] {
  const set = new Set<number>();
  for (const row of chain || []) if (usable(row) && (!type || row.type === type)) set.add(row.strike as number);
  return [...set].sort((a, b) => a - b);
}

/** Strikes listed for BOTH call and put: presets use them so paired legs share a strike. */
export function pairedStrikes(chain: readonly ChainContract[] | undefined): number[] {
  const calls = new Set(strikesOf(chain, 'call'));
  return strikesOf(chain, 'put').filter(k => calls.has(k));
}

export function nearestStrike(strikes: readonly number[], target: number): number | null {
  let best: number | null = null;
  for (const k of strikes) if (best == null || Math.abs(k - target) < Math.abs(best - target)) best = k;
  return best;
}

/** First strike strictly beyond `from` in the given direction, nearest to `target` if possible. */
function strikeBeyond(strikes: readonly number[], from: number, target: number, dir: 1 | -1): number | null {
  const side = strikes.filter(k => dir > 0 ? k > from : k < from);
  if (!side.length) return null;
  const near = nearestStrike(side, target) as number;
  return near;
}

// ── quote of a leg ──────────────────────────────────────────────────────────

export type NdReason = 'no_contract' | 'no_quote' | 'crossed_quote' | 'no_iv' | 'no_multiplier' | 'expired'
  | 'no_spot' | 'bad_qty' | 'bad_price' | 'bad_iv' | 'no_expiry' | 'adjusted';

export interface LegQuote {
  ok: boolean; reasons: NdReason[];
  contract: ChainContract | null;
  bid: number | null; ask: number | null; mid: number | null;
  /** price per underlying unit actually used (null = n.d.) and where it comes from */
  price: number | null; priceSource: 'mid' | 'ask' | 'bid' | 'manual' | 'spot' | null;
  /** natural price (buy at ask, sell at bid) and its distance from mid, per unit */
  natural: number | null;
  iv: number | null; ivSource: 'chain' | 'manual' | null;
  multiplier: number | null; days: number | null; qty: number | null;
}

/** Parser injected by the caller (leggiNumero of lib/cassa: > 0, comma-aware, ambiguity refused). */
export type Reader = (text: string) => number | null;

export function quoteLeg(leg: BuilderLeg, chains: Chains, mode: PriceMode, spot: number | null, now: number, read: Reader): LegQuote {
  const reasons: NdReason[] = [];
  const qtyRaw = read(leg.qty);
  const qty = qtyRaw != null && Number.isInteger(qtyRaw) ? qtyRaw : null;
  if (qty == null) reasons.push('bad_qty');
  const manualPrice = leg.price?.trim() ? read(leg.price) : null;
  if (leg.price?.trim() && manualPrice == null) reasons.push('bad_price');
  if (leg.kind === 'stock') {
    const price = manualPrice ?? spot;
    if (price == null) reasons.push('no_spot');
    return { ok: !reasons.length, reasons, contract: null, bid: null, ask: null, mid: null,
      price, priceSource: manualPrice != null ? 'manual' : spot != null ? 'spot' : null, natural: price,
      iv: null, ivSource: null, multiplier: 1, days: null, qty };
  }
  if (!leg.expiry) reasons.push('no_expiry');
  const contract = findContract(chains, leg);
  const days = leg.expiry ? daysToClose(leg.expiry, now) : null;
  if (days != null && days <= 0) reasons.push('expired');
  if (!contract) reasons.push(onlyAdjusted(chains, leg) ? 'adjusted' : 'no_contract');
  const bid = contract?.bid ?? null, ask = contract?.ask ?? null;
  const crossed = bid != null && ask != null && ask < bid;
  const mid = contract?.mid != null && !crossed ? contract.mid : bid != null && ask != null && !crossed && ask > 0 ? (bid + ask) / 2 : null;
  const natural = leg.side === 'buy' ? (ask != null && ask > 0 && !crossed ? ask : null) : (bid != null && !crossed && ask != null && ask > 0 ? bid : null);
  let price: number | null = null; let priceSource: LegQuote['priceSource'] = null;
  if (manualPrice != null) { price = manualPrice; priceSource = 'manual'; }
  else if (mode === 'mid') { price = mid; priceSource = mid == null ? null : 'mid'; }
  else { price = natural; priceSource = natural == null ? null : leg.side === 'buy' ? 'ask' : 'bid'; }
  if (contract && price == null && !leg.price?.trim()) reasons.push(crossed ? 'crossed_quote' : 'no_quote');
  const manualIv = leg.iv?.trim() ? read(leg.iv) : null;
  if (leg.iv?.trim() && (manualIv == null || manualIv > 500)) reasons.push('bad_iv');
  const iv = manualIv != null && manualIv <= 500 ? manualIv / 100 : contract?.iv ?? null;
  if (contract && iv == null && !leg.iv?.trim()) reasons.push('no_iv');
  const multiplier = contract?.multiplier ?? null;
  if (contract && multiplier == null) reasons.push('no_multiplier');
  return { ok: !reasons.length, reasons, contract, bid, ask, mid, price, priceSource, natural,
    iv, ivSource: manualIv != null ? 'manual' : contract?.iv != null ? 'chain' : null, multiplier, days, qty };
}

const sign = (side: Side) => side === 'buy' ? 1 : -1;

/** Net premium at mid and at natural, and the spread cost between them (cash, × qty × multiplier).
 *  Any leg without its price makes the corresponding total n.d. (null). Positive = paid. */
export function premiums(legs: readonly BuilderLeg[], quotes: readonly LegQuote[]) {
  let mid: number | null = 0, natural: number | null = 0;
  legs.forEach((leg, i) => {
    const q = quotes[i];
    const units = q.qty == null || q.multiplier == null ? null : sign(leg.side) * q.qty * q.multiplier;
    const m = leg.kind === 'stock' ? q.price : q.mid, n = leg.kind === 'stock' ? q.price : q.natural;
    mid = mid == null || units == null || m == null ? null : mid + units * m;
    natural = natural == null || units == null || n == null ? null : natural + units * n;
  });
  return { mid, natural, spread: mid == null || natural == null ? null : (natural as number) - (mid as number) };
}

// ── request for the engine ──────────────────────────────────────────────────

export interface EngineLeg { type: OptionType | 'stock'; side: Side; quantity: number; strike?: number; days?: number;
  iv?: number; premium: number; multiplier?: number }

/** Legs for POST /options/strategy/simulate, or the list of n.d. legs (index → reasons). */
export function engineLegs(legs: readonly BuilderLeg[], quotes: readonly LegQuote[]):
  { ok: true; legs: EngineLeg[] } | { ok: false; missing: { index: number; reasons: NdReason[] }[] } {
  const missing = quotes.map((q, index) => ({ index, reasons: q.reasons })).filter(x => x.reasons.length);
  if (missing.length) return { ok: false, missing };
  return { ok: true, legs: legs.map((leg, i) => {
    const q = quotes[i];
    if (leg.kind === 'stock') return { type: 'stock' as const, side: leg.side, quantity: q.qty as number, premium: q.price as number };
    return { type: leg.type as OptionType, side: leg.side, quantity: q.qty as number, strike: leg.strike as number,
      days: q.days as number, iv: q.iv as number, premium: q.price as number, multiplier: q.multiplier as number };
  }) };
}

/** First option expiry in fractional days (the horizon of the scenario slider), or null. */
export function horizonDays(quotes: readonly LegQuote[]): number | null {
  const days = quotes.map(q => q.days).filter((d): d is number => d != null && d > 0);
  return days.length ? Math.min(...days) : null;
}

// ── ready-made strategies ───────────────────────────────────────────────────

export type PresetId = 'long_call' | 'long_put' | 'covered_call' | 'cash_secured_put' | 'bull_call' | 'bear_call'
  | 'bull_put' | 'bear_put' | 'straddle' | 'strangle' | 'iron_condor' | 'iron_butterfly' | 'calendar';

export const PRESET_GROUPS: { id: 'directional' | 'vertical' | 'volatility'; presets: PresetId[] }[] = [
  { id: 'directional', presets: ['long_call', 'long_put', 'covered_call', 'cash_secured_put'] },
  { id: 'vertical', presets: ['bull_call', 'bear_call', 'bull_put', 'bear_put'] },
  { id: 'volatility', presets: ['straddle', 'strangle', 'iron_condor', 'iron_butterfly', 'calendar'] },
];

export type PresetOutcome = { ok: true; legs: BuilderLeg[] }
  | { ok: false; reason: 'no_spot' | 'no_chain' | 'few_strikes' | 'needs_far_expiry'; expiry?: string };

/**
 * Builds a preset on the loaded chain: strikes nearest to spot·(1 ± width), always distinct and
 * correctly ordered. `farExpiries` = expiries after `expiry` (the calendar uses the first one
 * whose chain is loaded; otherwise the caller is told which expiry to download).
 */
export function buildPreset(id: PresetId, spot: number | null, expiry: string, chains: Chains, farExpiries: readonly string[], width = .05): PresetOutcome {
  if (spot == null || !(spot > 0)) return { ok: false, reason: 'no_spot' };
  const chain = chains.get(expiry);
  if (!chain?.length) return { ok: false, reason: 'no_chain' };
  const both = pairedStrikes(chain);
  const calls = strikesOf(chain, 'call'), puts = strikesOf(chain, 'put');
  const atm = nearestStrike(both.length ? both : calls.length ? calls : puts, spot);
  if (atm == null) return { ok: false, reason: 'few_strikes' };
  const up = (from: number, list: number[], w = width) => strikeBeyond(list, from, spot * (1 + w), 1);
  const down = (from: number, list: number[], w = width) => strikeBeyond(list, from, spot * (1 - w), -1);
  const o = optionLeg;
  const need = (...ks: (number | null)[]) => ks.every(k => k != null);
  const mult = chain.find(r => r.multiplier != null)?.multiplier ?? null;
  switch (id) {
    case 'long_call': return calls.includes(atm) ? { ok: true, legs: [o('call', 'buy', expiry, atm)] } : { ok: false, reason: 'few_strikes' };
    case 'long_put': return puts.includes(atm) ? { ok: true, legs: [o('put', 'buy', expiry, atm)] } : { ok: false, reason: 'few_strikes' };
    case 'covered_call': {
      const k = up(spot, calls); if (k == null || mult == null) return { ok: false, reason: 'few_strikes' };
      return { ok: true, legs: [stockLeg('buy', mult), o('call', 'sell', expiry, k)] };
    }
    case 'cash_secured_put': {
      const k = down(spot, puts); if (k == null) return { ok: false, reason: 'few_strikes' };
      return { ok: true, legs: [o('put', 'sell', expiry, k)] };
    }
    case 'bull_call': case 'bear_call': {
      const hi = up(atm, calls); if (!calls.includes(atm) || hi == null) return { ok: false, reason: 'few_strikes' };
      const bull = id === 'bull_call';
      return { ok: true, legs: [o('call', bull ? 'buy' : 'sell', expiry, atm), o('call', bull ? 'sell' : 'buy', expiry, hi)] };
    }
    case 'bull_put': case 'bear_put': {
      const lo = down(atm, puts); if (!puts.includes(atm) || lo == null) return { ok: false, reason: 'few_strikes' };
      const bull = id === 'bull_put';
      return { ok: true, legs: [o('put', bull ? 'sell' : 'buy', expiry, atm), o('put', bull ? 'buy' : 'sell', expiry, lo)] };
    }
    case 'straddle': return both.includes(atm) ? { ok: true, legs: [o('call', 'buy', expiry, atm), o('put', 'buy', expiry, atm)] } : { ok: false, reason: 'few_strikes' };
    case 'strangle': {
      const c = up(spot, calls), p = down(spot, puts);
      return need(c, p) ? { ok: true, legs: [o('put', 'buy', expiry, p as number), o('call', 'buy', expiry, c as number)] } : { ok: false, reason: 'few_strikes' };
    }
    case 'iron_condor': {
      const sp = down(spot, puts), sc = up(spot, calls);
      const lp = sp == null ? null : down(sp, puts, 2 * width), lc = sc == null ? null : up(sc, calls, 2 * width);
      if (!need(sp, sc, lp, lc)) return { ok: false, reason: 'few_strikes' };
      return { ok: true, legs: [o('put', 'buy', expiry, lp as number), o('put', 'sell', expiry, sp as number),
        o('call', 'sell', expiry, sc as number), o('call', 'buy', expiry, lc as number)] };
    }
    case 'iron_butterfly': {
      const lp = down(atm, puts), lc = up(atm, calls);
      if (!both.includes(atm) || !need(lp, lc)) return { ok: false, reason: 'few_strikes' };
      return { ok: true, legs: [o('put', 'buy', expiry, lp as number), o('put', 'sell', expiry, atm),
        o('call', 'sell', expiry, atm), o('call', 'buy', expiry, lc as number)] };
    }
    case 'calendar': {
      if (!calls.includes(atm)) return { ok: false, reason: 'few_strikes' };
      const far = farExpiries.find(e => e > expiry && strikesOf(chains.get(e), 'call').length);
      if (!far) return { ok: false, reason: 'needs_far_expiry', expiry: farExpiries.find(e => e > expiry) };
      const k = nearestStrike(strikesOf(chains.get(far), 'call'), atm) as number;
      return { ok: true, legs: [o('call', 'sell', expiry, k), o('call', 'buy', far, k)] };
    }
  }
}

// ── engine response (the fields the builder reads) ──────────────────────────

export interface EnginePoint { price: number; pnl: number; delta: number | null; gamma: number | null; vega: number | null; theta: number | null; rho: number | null }
export interface EngineResult {
  currency: string; model: string; greek_units: Record<string, string>;
  vol_model?: VolModel; vol_model_basis?: string;
  forward_vols?: { index: number; near_iv: number; leg_iv: number; forward_iv: number | null }[];
  /** structural, or european_dividend = unbounded only for a European live call with q > 0 */
  unlimited_loss_reason?: 'structural' | 'european_dividend' | null;
  tail_reference?: { price: number; pnl: number } | null;
  entry_cost: number; net_premium: number; fees: number; entry_kind: 'debit' | 'credit' | 'even';
  same_expiry: boolean; expiry_days: number; expiry_basis?: 'exact' | 'model_first_expiry'; tail_limit?: number | null;
  breakevens: number[]; breakeven_intervals?: { from: number; to: number | null }[];
  max_profit: number | null; max_loss: number | null; unlimited_profit: boolean; unlimited_loss: boolean;
  probability_of_profit?: { value: number; sigma: number; horizon_days: number; basis: string } | null;
  legs?: { index: number; type: string; side: Side; value: number; pnl_today: number; delta: number | null; gamma: number | null; vega: number | null; theta: number | null; rho: number | null }[];
  today: EnginePoint; scenario: EnginePoint;
  curve: { price: number; expiry: number | null; today: number; scenario: number }[];
  heatmap: { elapsed_days: number; cells: EnginePoint[] }[];
  limits: string[];
}

const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const isNumOrNull = (v: unknown) => v === null || isNum(v);
const POINT_KEYS = ['delta', 'gamma', 'vega', 'theta', 'rho'] as const;
function pointProblem(p: unknown, where: string): string | null {
  if (!p || typeof p !== 'object') return `${where}: object`;
  const o = p as Record<string, unknown>;
  if (!isNum(o.price) || !isNum(o.pnl)) return `${where}: price/pnl`;
  for (const k of POINT_KEYS) if (!isNumOrNull(o[k])) return `${where}.${k}`;
  return null;
}

/**
 * Shape check of POST /options/strategy/simulate (review M3): the builder reads `curve[0]`,
 * `heatmap[i].cells`, `today.pnl`... A response without them is declared as an engine error with
 * the first missing field, instead of crashing the page. Returns null when the shape is usable.
 */
export function engineResultProblem(value: unknown): string | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return 'response: object';
  const r = value as Record<string, unknown>;
  for (const k of ['entry_cost', 'net_premium', 'fees', 'expiry_days'] as const) if (!isNum(r[k])) return k;
  if (!['debit', 'credit', 'even'].includes(r.entry_kind as string)) return 'entry_kind';
  for (const k of ['same_expiry', 'unlimited_profit', 'unlimited_loss'] as const) if (typeof r[k] !== 'boolean') return k;
  for (const k of ['max_profit', 'max_loss'] as const) if (!isNumOrNull(r[k])) return k;
  if (!Array.isArray(r.breakevens) || !r.breakevens.every(isNum)) return 'breakevens';
  if (!Array.isArray(r.curve) || r.curve.length < 2) return 'curve';
  for (const [i, row] of (r.curve as unknown[]).entries()) {
    const o = row as Record<string, unknown> | null;
    if (!o || !isNum(o.price) || !isNumOrNull(o.expiry) || !isNum(o.today) || !isNum(o.scenario)) return `curve[${i}]`;
  }
  for (const k of ['today', 'scenario'] as const) { const bad = pointProblem(r[k], k); if (bad) return bad; }
  if (!Array.isArray(r.heatmap)) return 'heatmap';
  for (const [i, row] of (r.heatmap as unknown[]).entries()) {
    const o = row as Record<string, unknown> | null;
    if (!o || !isNum(o.elapsed_days) || !Array.isArray(o.cells)) return `heatmap[${i}]`;
    for (const [j, c] of (o.cells as unknown[]).entries()) { const bad = pointProblem(c, `heatmap[${i}].cells[${j}]`); if (bad) return bad; }
  }
  if (!Array.isArray(r.limits)) return 'limits';
  if (r.legs !== undefined && !Array.isArray(r.legs)) return 'legs';
  return null;
}

export interface RateQuote { value: number | null; percent: number | null; date: string | null; source: string; status: 'solid' | 'stale' | 'error'; error: string | null }

/** Index of the curve point nearest to `price` (the reading is the engine's exact value there). */
export function nearestPoint(curve: readonly { price: number }[], price: number): number {
  let best = 0;
  for (let i = 1; i < curve.length; i++) if (Math.abs(curve[i].price - price) < Math.abs(curve[best].price - price)) best = i;
  return best;
}

/** Return on risk: max profit / max loss, only when both are finite and the loss is positive. */
export function returnOnRisk(r: Pick<EngineResult, 'max_profit' | 'max_loss' | 'unlimited_profit' | 'unlimited_loss'>): number | null {
  if (r.unlimited_profit || r.unlimited_loss || r.max_profit == null || r.max_loss == null || !(r.max_loss > 0)) return null;
  return r.max_profit / r.max_loss;
}

/** Number shown in a field: at most 4 decimals, no binary noise (L3). Separator by language. */
export function fieldNumber(value: number | null | undefined, comma: boolean, digits = 4): string {
  if (value == null || !Number.isFinite(value)) return '';
  const text = String(Number(value.toFixed(digits)));
  return comma ? text.replace('.', ',') : text;
}
