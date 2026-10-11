import { t as tr } from '@/i18n/t';
import { ivText, numText, priceText, type SurfaceModel, type SurfaceRow } from '@/lib/vol-deck';

/* Lettura laterale del punto scelto (v2 10/10, Opus 5.5 — estratta da VolSurfacePage per provarla).
   La IV di griglia e l'ATM del builder sono DUE righe: un buco di griglia resta «buco», mai l'ATM al
   suo posto; un ATM non qualificato (spot proxy) si mostra solo come tale, accanto al n.d.
   v3 «quant»: sull'asse delta la colonna si chiama col suo delta e lo strike e' quello della libreria. */
export default function PointReadout({ model, row, column, delta }: { model: SurfaceModel; row: SurfaceRow; column: number | null;
  delta?: { head: string; strike: number | null } | null }) {
  const na = tr('voldeck.ui_n_a_15');
  const cell = column != null ? row.iv[column] : null;
  const strike = delta ? delta.strike : column != null ? model.strikes[column] : null;
  return <dl className="vdn-readout" aria-live="polite" data-vol-readout>
    <div><dt>{tr('voldeck.ui_expiry_177')}</dt><dd>{row.expiry} · {row.days}{tr('voldeck.short_days')}</dd></div>
    {delta ? <div><dt>Delta</dt><dd>{delta.head}</dd></div>
      : <div><dt>K/S</dt><dd>{column != null ? numText(model.grid[column], 3, na) : na}</dd></div>}
    <div><dt>{tr(delta ? 'voldeck.q_strike_delta' : 'voldeck.n_strike_eq')}</dt><dd>{priceText(strike, na)}</dd></div>
    <div><dt>{tr('voldeck.n_iv_grid')}</dt><dd data-vol-readout-iv className={column != null && cell == null ? 'vdn-warn-text' : 'is-big'}>
      {column != null && cell == null ? tr('voldeck.n_hole_word') : ivText(cell, na, 2)}</dd></div>
    <div><dt>{tr('voldeck.n_term_atm')}</dt><dd data-vol-readout-atm>{ivText(row.atm, na, 2)}
      {row.atm == null && row.atmUnqualified != null && <small className="vdn-warn-text"> · {tr('voldeck.n_unqualified', { v: ivText(row.atmUnqualified, na, 2) })}</small>}</dd></div>
  </dl>;
}
