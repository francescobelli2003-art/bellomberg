import type { ReactNode } from 'react';
import Card from '@/components/nuova/Card';
import { t as tr } from '@/i18n/t';
import { finite, ivText, numText, priceText, type SurfaceModel } from '@/lib/vol-deck';
import IvGrid from './IvGrid';
import { SmileChart } from './SliceCharts';
import type { VolQuant } from './quant';
import { fwdQuality, naWhy, ptText, ratioText } from './quantText';

/* ============================================================================
   Vista «Skew» del Vol Deck (10/10/2026, Opus 5.5). Una domanda: «quanto e' storta la curva,
   scadenza per scadenza?». Tabella stile OVDV (ATM, RR25, BF25, RR10, BF10, skew normalizzato),
   griglia IV ai delta standard e smile della scadenza scelta in delta. Tutti i numeri:
   lib/vol-quant (deltaGrid + skewMetrics) sul forward implicito dalla parita'.
   ========================================================================== */
export function deltaCellLabel(q: VolQuant, model: SurfaceModel) {
  return (row: number, col: number) => {
    const r = q.delta.rows.find(x => x.expiry === model.rows[row]?.expiry);
    const c = r?.cells[q.delta.columns[col]];
    const na = tr('voldeck.ui_n_a_15');
    return `${q.deltaHeads[col]} · K ${priceText(c?.strike, na)}${c?.reason ? ' · ' + naWhy(c.reason) : ''}`;
  };
}

export function OvdvTable({ q, model, expiry, onExpiry }: { q: VolQuant; model: SurfaceModel; expiry: string | null; onExpiry: (e: string) => void }) {
  const na = tr('voldeck.ui_n_a_15');
  const cell = (v: number | null, why: Parameters<typeof naWhy>[0], text: (v: number) => string, tone?: boolean): ReactNode =>
    <td className={!finite(v) ? 'is-na' : tone ? (v < 0 ? 'is-bad' : 'is-good') : undefined} title={!finite(v) ? naWhy(why) : undefined}>{finite(v) ? text(v) : na}</td>;
  return <div className="vdn-table-wrap"><table className="vdn-table is-compact num" data-vol-skew-table>
    <thead><tr>
      <th className="l">{tr('voldeck.ui_expiry_177')}</th><th>{tr('voldeck.ui_days_97')}</th><th>F</th><th>ATMF</th>
      <th>RR 25Δ</th><th>smile BF 25Δ</th><th>RR 10Δ</th><th>smile BF 10Δ</th><th title={tr('voldeck.q_skewnorm_hint')}>{tr('voldeck.q_skewnorm')}</th>
    </tr></thead>
    <tbody>{model.rows.map(row => {
      const s = q.skew.find(x => x.expiry === row.expiry);
      const f = q.forwardOf(row.expiry), res = q.forwards[row.expiry];
      // qualita' del forward: coppie usate e dispersione dei candidati (v2 della libreria: residuo de-americanizzato)
      const fq = res && res.forward != null ? fwdQuality(res) : null;
      const atmWhy = q.delta.rows.find(x => x.expiry === row.expiry)?.cells.ATMF?.reason ?? (f.F == null ? f.why : null);
      return <tr key={row.expiry} className={row.expiry === expiry ? 'is-sel' : undefined} data-vol-skew-row={row.expiry}
        onClick={() => onExpiry(row.expiry)}>
        <td className="l">{row.expiry}{row.partial ? <small className="vdn-warn-text"> ⚠</small> : null}</td><td>{row.days}</td>
        <td className={f.F == null ? 'is-na' : undefined} title={f.F == null ? naWhy(f.why) : fq ?? undefined} data-vol-skew-fwd>{f.F == null ? na : priceText(f.F, na)}
          {f.F != null && finite(res?.dispersionRel) && <small> ±{numText((res!.dispersionRel as number) * 100, 3)}%</small>}</td>
        <td className={finite(s?.atmf) ? 'is-acc' : 'is-na'} title={!finite(s?.atmf) ? naWhy(atmWhy) : undefined} data-vol-skew-atm>{finite(s?.atmf) ? ivText(s!.atmf, na, 1) : na}</td>
        {cell(s?.rr25 ?? null, s?.reasons.rr25 ?? atmWhy, v => ptText(v), true)}
        {cell(s?.bf25 ?? null, s?.reasons.bf25 ?? atmWhy, v => ptText(v))}
        {cell(s?.rr10 ?? null, s?.reasons.rr10 ?? atmWhy, v => ptText(v), true)}
        {cell(s?.bf10 ?? null, s?.reasons.bf10 ?? atmWhy, v => ptText(v))}
        {cell(s?.skewNorm25 ?? null, s?.reasons.skewNorm25 ?? atmWhy, v => ratioText(v), true)}
      </tr>;
    })}</tbody>
  </table></div>;
}

export default function SkewView({ q, model, expiry, column, onExpiry, onColumn, onPick, onExpiryStep, rateLine, smileHeight = 140 }: {
  q: VolQuant; model: SurfaceModel; expiry: string | null; column: number | null;
  onExpiry: (e: string) => void; onColumn: (c: number) => void; onPick: (e: string, c: number) => void; onExpiryStep: (s: -1 | 1) => void;
  rateLine: string; smileHeight?: number;
}) {
  const dm = q.deltaModel;
  const row = dm.rows.find(r => r.expiry === expiry) || null;
  const col = column != null && column >= 0 && column < dm.grid.length ? column : q.deltaHeads.indexOf('ATMF');
  return <div className="vdn-view" data-vol-view-panel="skew">
    <div className="vdn-split-2">
    <Card className="vdn-wide-card" titolo={tr('voldeck.q_skew_title')} conteggio={tr('voldeck.q_skew_count', { n: q.skew.filter(s => finite(s.rr25)).length, t: model.rows.length })}>
      <OvdvTable q={q} model={model} expiry={expiry} onExpiry={onExpiry} />
    </Card>
      <Card className="vdn-slice-card" titolo={tr('voldeck.q_delta_grid_title')} conteggio={tr('voldeck.n_tile_holes_sub', { f: dm.filledCells, c: dm.cells })}>
        <IvGrid model={dm} axis="delta" expiry={expiry} column={col} onPick={onPick} onExpiry={onExpiry} onColumn={onColumn}
          extras={{ heads: q.deltaHeads, cellLabel: deltaCellLabel(q, model) }} />
      </Card>
    </div>
      <Card className="vdn-slice-card" titolo={tr('voldeck.q_delta_smile_title')} conteggio={row ? `${row.expiry} · ${row.days}${tr('voldeck.short_days')}` : undefined}>
        <SmileChart model={dm} expiry={expiry} column={col} onColumn={onColumn} onExpiryStep={onExpiryStep} quotes={null} axis="delta" heads={q.deltaHeads} height={smileHeight} />
        {row && row.iv.every(v => v == null) && <p className="vdn-legend vdn-warn-text">{naWhy(q.forwardOf(row.expiry).why ?? q.delta.rows.find(r => r.expiry === row.expiry)?.reason)}</p>}
      </Card>
  </div>;
}

