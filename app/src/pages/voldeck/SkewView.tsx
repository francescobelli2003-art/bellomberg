import type { ReactNode } from 'react';
import Card from '@/components/nuova/Card';
import { t as tr } from '@/i18n/t';
import { finite, ivText, numText, priceText, type SurfaceModel } from '@/lib/vol-deck';
import IvGrid from './IvGrid';
import { SmileChart } from './SliceCharts';
import type { VolQuant } from './quant';
import { forwardBasisLine, fwdLevelTag, fwdQuality, naWhy, onProxy, proxyHint, ptText, ratioText } from './quantText';

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
    // 11/10 v2: una cella che poggia sul forward proxy lo dice nella lettura (griglia delta e Superficie in asse delta)
    const px = r && c?.iv != null && onProxy(q, r.expiry) ? ' · ' + tr('voldeck.q_proxy_mark') + ': ' + proxyHint(q, r.expiry) : '';
    return `${q.deltaHeads[col]} · K ${priceText(c?.strike, na)}${c?.reason ? ' · ' + naWhy(c.reason) : ''}${px}`;
  };
}

export function OvdvTable({ q, model, expiry, onExpiry }: { q: VolQuant; model: SurfaceModel; expiry: string | null; onExpiry: (e: string) => void }) {
  const na = tr('voldeck.ui_n_a_15');
  // 11/10 v2 (review): ATMF, RR, BF e skew normalizzato che poggiano sul forward PROXY portano il segno «proxy»
  const mark = (px: string | null) => px ? <small className="vdn-proxy-mark" data-vol-proxy-mark title={px}> {tr('voldeck.q_proxy_mark')}</small> : null;
  const cell = (v: number | null, why: Parameters<typeof naWhy>[0], text: (v: number) => string, tone?: boolean, px: string | null = null): ReactNode =>
    <td className={!finite(v) ? 'is-na' : tone ? (v < 0 ? 'is-bad' : 'is-good') : undefined} title={!finite(v) ? naWhy(why) : px ?? undefined}>{finite(v) ? text(v) : na}{finite(v) && mark(px)}</td>;
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
      // etichetta del livello per riga solo se i livelli sono MISTI: con uno solo lo dice la riga sopra la tabella
      const mixed = Object.keys(q.forwardLevels).length > 1;
      const atmWhy = q.delta.rows.find(x => x.expiry === row.expiry)?.cells.ATMF?.reason ?? (f.F == null ? f.why : null);
      const px = onProxy(q, row.expiry) ? proxyHint(q, row.expiry) : null;
      return <tr key={row.expiry} className={row.expiry === expiry ? 'is-sel' : undefined} data-vol-skew-row={row.expiry}
        onClick={() => onExpiry(row.expiry)}>
        <td className="l">{row.expiry}{row.partial ? <small className="vdn-warn-text"> ⚠</small> : null}</td><td>{row.days}</td>
        <td className={f.F == null ? 'is-na' : undefined} title={f.F == null ? naWhy(f.why) : fq ?? undefined} data-vol-skew-fwd>{f.F == null ? na : priceText(f.F, na)}
          {mixed && f.F != null && f.level && <small data-vol-skew-fwd-level={f.level}> {fwdLevelTag(f.level)}</small>}
          {f.F != null && finite(res?.dispersionRel) && <small> ±{numText((res!.dispersionRel as number) * 100, 3)}%</small>}</td>
        <td className={finite(s?.atmf) ? 'is-acc' : 'is-na'} title={!finite(s?.atmf) ? naWhy(atmWhy) : px ?? undefined} data-vol-skew-atm>{finite(s?.atmf) ? ivText(s!.atmf, na, 1) : na}{finite(s?.atmf) && mark(px)}</td>
        {cell(s?.rr25 ?? null, s?.reasons.rr25 ?? atmWhy, v => ptText(v), true, px)}
        {cell(s?.bf25 ?? null, s?.reasons.bf25 ?? atmWhy, v => ptText(v), false, px)}
        {cell(s?.rr10 ?? null, s?.reasons.rr10 ?? atmWhy, v => ptText(v), true, px)}
        {cell(s?.bf10 ?? null, s?.reasons.bf10 ?? atmWhy, v => ptText(v), false, px)}
        {cell(s?.skewNorm25 ?? null, s?.reasons.skewNorm25 ?? atmWhy, v => ratioText(v), true, px)}
      </tr>;
    })}</tbody>
  </table></div>;
}

export default function SkewView({ q, model, expiry, column, onExpiry, onColumn, onPick, onExpiryStep, rateLine: _rateLine, smileHeight = 300 }: {
  q: VolQuant; model: SurfaceModel; expiry: string | null; column: number | null;
  onExpiry: (e: string) => void; onColumn: (c: number) => void; onPick: (e: string, c: number) => void; onExpiryStep: (s: -1 | 1) => void;
  rateLine: string; smileHeight?: number;
}) {
  const dm = q.deltaModel;
  const row = dm.rows.find(r => r.expiry === expiry) || null;
  const col = column != null && column >= 0 && column < dm.grid.length ? column : q.deltaHeads.indexOf('ATMF');
  // 11/10: smile e tabella OVDV affiancati in alto (l'elemento della pagina), griglia delta sotto
  return <div className="vdn-view" data-vol-view-panel="skew">
    <p className="vdn-legend vdn-fwd-basis" data-vol-fwd-basis>{forwardBasisLine(q, model.rows.map(r => r.expiry))}
      {q.ivOnlyExpiries.length > 0 && <> {tr('voldeck.q_iv_only', { n: q.ivOnlyExpiries.length })}</>}</p>
    <div className="vdn-split-2">
      <Card className="vdn-wide-card" titolo={tr('voldeck.q_skew_title')} conteggio={tr('voldeck.q_skew_count', { n: q.skew.filter(s => finite(s.rr25)).length, t: model.rows.length })}>
        <OvdvTable q={q} model={model} expiry={expiry} onExpiry={onExpiry} />
      </Card>
      <Card className="vdn-slice-card" titolo={tr('voldeck.q_delta_smile_title')} conteggio={row ? `${row.expiry} · ${row.days}${tr('voldeck.short_days')}` : undefined}>
        <SmileChart model={dm} expiry={expiry} column={col} onColumn={onColumn} onExpiryStep={onExpiryStep} quotes={null} axis="delta" heads={q.deltaHeads} height={smileHeight} />
        {row && row.iv.every(v => v == null) && <p className="vdn-legend vdn-warn-text">{naWhy(q.forwardOf(row.expiry).why ?? q.delta.rows.find(r => r.expiry === row.expiry)?.reason)}</p>}
      </Card>
    </div>
    <Card className="vdn-wide-card" titolo={tr('voldeck.q_delta_grid_title')} conteggio={tr('voldeck.n_tile_holes_sub', { f: dm.filledCells, c: dm.cells })}>
      <IvGrid model={dm} axis="delta" expiry={expiry} column={col} onPick={onPick} onExpiry={onExpiry} onColumn={onColumn}
        extras={{ heads: q.deltaHeads, cellLabel: deltaCellLabel(q, model) }} />
    </Card>
  </div>;
}

