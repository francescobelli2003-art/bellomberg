import { t as tr } from '@/i18n/t';
import { daysToExpiry, finite, ivText, numText, priceText, type SurfaceModel } from '@/lib/vol-deck';
import type { OverlayCtx } from './Surface3D';
import type { VolQuant } from './quant';
import { flagLine, fwdQuality, naWhy, whyText } from './quantText';

/* ============================================================================
   Sovrapposizioni del Vol Deck «quant» (10/10/2026, Opus 5.5): tracce Plotly per il 3D e
   posizioni per le fette. Ogni numero viene da VolQuant (lib/vol-quant): qui solo coordinate.
   - Forward (ATMF): per scadenza il forward implicito dalla parita' (non lo spot), alla IV ATMF
     della griglia delta; senza chain/tasso la scadenza non ha punto e il motivo e' contato.
   - ±1σ: il cono F·σ_ATM·√T sul pavimento; fuori dall'asse mostrato il tratto si spezza.
   - Utili: la data del payload sull'asse delle scadenze, con vol d'evento e mossa del giorno.
   - Arbitraggi: un cerchio sopra ogni cella toccata da un flag.
   ========================================================================== */
export interface OverlayToggles { forward: boolean; cone: boolean; earnings: boolean; arb: boolean }
export const NO_OVERLAYS: OverlayToggles = { forward: false, cone: false, earnings: false, arb: false };
export interface OverlayColors { forward: string; cone: string; earnings: string; arb: string; arbInd?: string; muted: string }

/** Posizione x di un prezzo sull'asse mostrato (K/S o strike); null fuori dall'asse (mai allargato). */
export const priceX = (model: SurfaceModel, price: number | null, strikeAxis: boolean, xs: number[]): number | null => {
  if (!finite(price) || model.spot == null) return null;
  const x = strikeAxis ? price : price / model.spot;
  return x >= xs[0] - 1e-9 && x <= xs[xs.length - 1] + 1e-9 ? x : null;
};

/** Giorni di calendario agli utili (null se la data manca o non e' leggibile). */
export function earningsDays(date: string | null | undefined, asOf: string | null = null): number | null {
  if (typeof date !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return null;
  // giorni dalla data (New York) dell'istantanea, non dall'orologio di chi guarda (review 10/10); senza data: oggi
  const day = typeof asOf === 'string' ? asOf.slice(0, 10) : null;
  if (day && /^\d{4}-\d{2}-\d{2}$/.test(day)) return Math.round((Date.parse(date + 'T00:00:00Z') - Date.parse(day + 'T00:00:00Z')) / 86400000);
  const d = daysToExpiry(date);
  return Number.isFinite(d) ? d : null;
}

/** Testo della vol d'evento (vol annua + mossa del giorno) o il suo motivo n.d. */
export function eventLine(q: VolQuant, date: string | null): string {
  if (!date || !q.event) return tr('voldeck.q_earn_none');
  const e = q.event;
  return e.eventMove != null
    ? tr('voldeck.q_earn_line', { d: date, v: ivText(e.eventVolAnnualized, '', 1), m: numText(e.eventMove * 100, 2), a: e.before || '—', b: e.after || '—' })
    : tr('voldeck.q_earn_na', { d: date, why: whyText(e.reason) });
}

export function overlayTraces(model: SurfaceModel, q: VolQuant, on: OverlayToggles, c: OverlayColors, date: string | null, delta: boolean, asOf: string | null = null) {
  return (ctx: OverlayCtx): any[] => {
    const out: any[] = [];
    const ys = ctx.ys, lift = (ctx.zTop - ctx.floor) * 0.02;
    if (on.forward) {
      const x: (number | null)[] = [], y: (number | null)[] = [], z: (number | null)[] = [], text: string[] = [];
      model.rows.forEach((row, j) => {
        const { F } = q.forwardOf(row.expiry);
        const cell = q.delta.rows.find(r => r.expiry === row.expiry)?.cells.ATMF;
        // asse delta: la colonna ATM (ATMF) e' gia' x = 2; altrimenti F sull'asse mostrato
        const px = delta ? (ctx.xs[q.delta.columns.indexOf('ATMF')] ?? null) : priceX(model, F, ctx.strikeAxis, ctx.xs);
        if (F == null || px == null || !finite(cell?.iv)) { x.push(null); y.push(null); z.push(null); text.push(''); return; }
        const res = q.forwards[row.expiry];
        x.push(px); y.push(ys[j]); z.push((cell!.iv as number) * 100 + lift);
        text.push(`<b>${row.expiry}</b> · ${row.days}${tr('voldeck.short_days')}<br>${tr('voldeck.q_fwd_hover', {
          f: priceText(F, ''), m: model.spot != null ? numText(F / model.spot, 4) : tr('voldeck.ui_n_a_15'), v: ivText(cell!.iv, '', 2),
          q: res ? fwdQuality(res) : tr('voldeck.ui_n_a_15') })}`);
      });
      if (x.some(v => v != null)) out.push({ type: 'scatter3d', mode: 'lines+markers', meta: 'ov-forward', name: tr('voldeck.q_ov_forward'),
        x, y, z, text, hoverinfo: 'text', connectgaps: false, line: { color: c.forward, width: 6 }, marker: { size: 3.5, color: c.forward } });
    }
    if (on.cone && !delta) {
      for (const side of ['low', 'high'] as const) {
        const x: (number | null)[] = [], y: (number | null)[] = [], z: (number | null)[] = [], text: string[] = [];
        model.rows.forEach((row, j) => {
          const r = q.cone.find(k => k.expiry === row.expiry);
          const px = r ? priceX(model, r[side], ctx.strikeAxis, ctx.xs) : null;
          if (!r || px == null) { x.push(null); y.push(null); z.push(null); text.push(''); return; }
          x.push(px); y.push(ys[j]); z.push(ctx.floor);
          text.push(`<b>${row.expiry}</b><br>${tr('voldeck.q_cone_hover', { dn: numText((r.downPct as number) * 100, 2), up: numText((r.upPct as number) * 100, 2), lo: priceText(r.low, ''), hi: priceText(r.high, ''), f: priceText(r.forward, '') })}`);
        });
        if (x.some(v => v != null)) out.push({ type: 'scatter3d', mode: 'lines', meta: 'ov-cone-' + side, name: tr('voldeck.q_ov_cone'),
          x, y, z, text, hoverinfo: 'text', connectgaps: false, line: { color: c.cone, width: 5, dash: 'dash' } });
      }
    }
    if (on.earnings) {
      const d = earningsDays(date, asOf);
      const first = model.rows[0]?.days, last = model.rows[model.rows.length - 1]?.days;
      if (d != null && first != null && last != null && d >= 0 && d <= last) {
        const ye = Math.sqrt(d), label = eventLine(q, date);
        out.push({ type: 'scatter3d', mode: 'lines+markers', meta: 'ov-earnings', name: tr('voldeck.q_ov_earnings'),
          x: ctx.xs, y: ctx.xs.map(() => ye), z: ctx.xs.map(() => ctx.floor), text: ctx.xs.map(() => label), hoverinfo: 'text',
          line: { color: c.earnings, width: 5 }, marker: { size: 2, color: c.earnings } });
      }
    }
    if (on.arb && !delta && q.flags.length) {
      // due livelli: anello grande e forte = arbitraggio eseguibile; anello piccolo e tenue = indicativo
      for (const level of ['executable', 'indicative'] as const) {
        const x: number[] = [], y: number[] = [], z: number[] = [], text: string[] = [], cd: [string, number][] = [];
        model.rows.forEach((row, j) => row.iv.forEach((v, i) => {
          const key = row.expiry + '|' + i, hits = q.cellFlags.get(key);
          if (!hits || !finite(v) || q.cellLevel.get(key) !== level) return;
          x.push(ctx.xs[i]); y.push(ys[j]); z.push(v * 100 + lift); cd.push([row.expiry, i]);
          text.push(`<b>${row.expiry}</b> · K/S ${numText(model.grid[i], 3)}<br>` + hits.map(h => flagLine(q.flags[h])).join('<br>'));
        }));
        const color = level === 'executable' ? c.arb : (c.arbInd || c.muted);
        if (x.length) out.push({ type: 'scatter3d', mode: 'markers', meta: level === 'executable' ? 'ov-arb-exec' : 'ov-arb-ind',
          name: tr(`voldeck.q_level_${level}`), x, y, z, text, customdata: cd, hoverinfo: 'text',
          marker: level === 'executable' ? { symbol: 'circle-open', size: 11, color, line: { color, width: 4 } }
            : { symbol: 'circle-open', size: 7, color, opacity: 0.55, line: { color, width: 1.5 } } });
      }
    }
    return out;
  };
}

/** Riepilogo per la legenda: quante scadenze hanno il forward, e perche' le altre no. */
export function forwardCoverage(model: SurfaceModel, q: VolQuant): { ok: number; total: number; why: string | null } {
  const res = model.rows.map(row => q.forwardOf(row.expiry));
  const missing = res.filter(r => r.F == null);
  return { ok: res.length - missing.length, total: res.length, why: missing.length ? naWhy(missing[0].why) : null };
}

/** Riga di lettura di una coppia di vol forward (valore, arbitraggio di calendario o motivo n.d.). */
export function fwdVolLine(model: SurfaceModel, p: import('@/lib/vol-quant').ForwardVolPair): string {
  const d = (e: string) => model.rows.find(r => r.expiry === e)?.days;
  const span = `${d(p.from) ?? '?'}${tr('voldeck.short_days')}→${d(p.to) ?? '?'}${tr('voldeck.short_days')}`;
  if (p.forwardVol != null) return tr('voldeck.q_fwdvol_line', { s: span, v: ivText(p.forwardVol, '', 2) });
  if (p.calendarArbitrage) return tr(p.beyondNoise ? 'voldeck.q_fwdvol_arb' : 'voldeck.q_fwdvol_arb_noise', { s: span, w: numText((p.varianceDrop ?? 0) * 1e4, 2) });
  return tr('voldeck.q_fwdvol_na', { s: span, why: whyText(p.reason) });
}

/** Sovrapposizioni della fetta «term» (TermChart): vol forward e utili, gia' in giorni del modello. */
export function termMarks(model: SurfaceModel, q: VolQuant, date: string | null, on: { fwd: boolean; earnings: boolean }, asOf: string | null = null): import('./SliceCharts').TermMarks {
  const d = (e: string) => model.rows.find(r => r.expiry === e)?.days;
  const ed = earningsDays(date, asOf ?? q.event?.asOf ?? null);
  return {
    fwd: on.fwd ? q.fwdVol.flatMap(p => d(p.from) != null && d(p.to) != null
      ? [{ from: p.from, to: p.to, d1: d(p.from) as number, d2: d(p.to) as number, v: p.forwardVol, arb: p.calendarArbitrage, text: fwdVolLine(model, p) }] : []) : [],
    earnings: on.earnings && ed != null ? { days: ed, text: eventLine(q, date) } : null,
  };
}
