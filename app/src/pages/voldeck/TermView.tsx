import Card from '@/components/nuova/Card';
import { t as tr } from '@/i18n/t';
import { finite, ivText, numText, priceText, type SurfaceModel } from '@/lib/vol-deck';
import { TermChart } from './SliceCharts';
import type { VolQuant } from './quant';
import { fwdVolLine, termMarks } from './overlays';
import { naWhy, whyText } from './quantText';

/* ============================================================================
   Sotto-pagina «Termine» del Vol Deck (10/10/2026, Opus 5.5). Una domanda: «come si distribuisce la
   varianza nel tempo, e quanto pesano gli utili?». In una schermata: striscia degli utili (vol d'evento
   e mossa del giorno), grafico della struttura a termine con la vol forward, e UNA tabella per scadenza
   (forward, vol forward dalla scadenza precedente, ±1σ lognormale sul forward). Numeri: lib/vol-quant
   (eventVol, forwardVol, expectedMoveCone). Il proiettore centrato sullo spot e' stato tolto (PM 10/10).
   ========================================================================== */
/** Stesse righe del modello K/S, ma con l'ATM = ATMF della libreria (un solo ATM in pagina). */
export function termModelOf(model: SurfaceModel, q: VolQuant): SurfaceModel {
  return { ...model, rows: model.rows.map(r => ({ ...r, atm: q.atmf[r.expiry] ?? null, atmUnqualified: null })) };
}

export default function TermView({ q, model, column, expiry, onExpiry, earnings, chartHeight = 230 }: {
  q: VolQuant; model: SurfaceModel; column: number | null; expiry: string | null; onExpiry: (e: string) => void;
  earnings: string | null; chartHeight?: number;
}) {
  const na = tr('voldeck.ui_n_a_15');
  const marks = termMarks(model, q, earnings, { fwd: true, earnings: true });
  const e = q.event;
  const d = (x: string) => model.rows.find(r => r.expiry === x)?.days;
  const termModel = termModelOf(model, q);
  return <div className="vdn-view" data-vol-view-panel="termine">
    {/* utili: una striscia, coi numeri della libreria o il motivo n.d. */}
    <div className="vdn-eventbar" data-vol-event>
      {!earnings ? <span className="vdn-warn-text" data-vol-event-na>{tr('voldeck.q_earn_none')}</span> : <>
        <b>{tr('voldeck.q_event_title')} · {earnings}</b>
        <span>{tr('voldeck.q_event_interval')} <b>{e?.before || tr('voldeck.q_event_today')} → {e?.after || na}</b></span>
        <span>{tr('voldeck.q_event_vol')} <b className={e?.eventVolAnnualized == null ? 'vdn-warn-text' : undefined} data-vol-event-vol>
          {e?.eventVolAnnualized != null ? ivText(e.eventVolAnnualized, na, 1) : naWhy(e?.reason)}</b></span>
        <span>{tr('voldeck.q_event_move')} <b className={e?.eventMove == null ? 'vdn-warn-text' : undefined} data-vol-event-move>
          {e?.eventMove != null ? '±' + numText(e.eventMove * 100, 2) + '%' : naWhy(e?.reason)}</b></span>
        <span>{tr('voldeck.q_event_base')} <b>{finite(e?.baseVol) ? ivText(e!.baseVol, na, 2) + ' · ' + tr(`voldeck.q_base_${e!.baseMethod === 'pre+post' ? 'both' : e!.baseMethod || 'pre'}`) : na}</b></span>
      </>}
    </div>
    <div className="vdn-slices">
      <Card className="vdn-slice-card" titolo={tr('voldeck.n_term_title')} conteggio={tr('voldeck.q_term_count')}>
        <TermChart model={termModel} column={column} expiry={expiry} onExpiry={onExpiry} marks={marks} height={chartHeight} />
      </Card>
      <Card className="vdn-slice-card" titolo={tr('voldeck.q_term_table_title')} conteggio={tr('voldeck.q_cone_count')}>
        <div className="vdn-table-wrap"><table className="vdn-table is-compact num" data-vol-fwdvol-table data-vol-cone-table>
          <thead><tr><th className="l">{tr('voldeck.ui_expiry_177')}</th><th>F</th><th>{tr('voldeck.q_col_fwdvol')}</th><th>±1σ</th><th>{tr('voldeck.q_col_range')}</th></tr></thead>
          <tbody>{q.cone.map(r => {
            const p = q.fwdVol.find(x => x.to === r.expiry);
            return <tr key={r.expiry} data-vol-cone-row={r.expiry} data-vol-fwdvol-row={p ? r.expiry : undefined} className={r.expiry === expiry ? 'is-sel' : undefined}
              onClick={() => onExpiry(r.expiry)}>
              <td className="l">{r.expiry} <small>{d(r.expiry)}{tr('voldeck.short_days')}</small></td>
              <td className={r.forward == null ? 'is-na' : undefined} title={r.forward == null ? naWhy(q.forwardOf(r.expiry).why) : undefined}>{priceText(r.forward, na)}</td>
              <td className={!p ? 'is-na' : p.forwardVol == null ? (p.calendarArbitrage ? 'is-ind' : 'is-na') : 'is-acc'} title={p ? fwdVolLine(model, p) : undefined}>
                {!p ? '—' : p.forwardVol != null ? ivText(p.forwardVol, na, 2) : p.calendarArbitrage ? tr('voldeck.q_status_cal_short') : whyText(p.reason)}
                {p && <small> {d(p.from) ?? '?'}{tr('voldeck.short_days')} → {d(p.to) ?? '?'}{tr('voldeck.short_days')}</small>}</td>
              <td className={r.downPct == null ? 'is-na' : 'is-acc'} data-vol-cone-pct>{r.downPct == null || r.upPct == null ? naWhy(r.reason) : `−${numText(r.downPct * 100, 2)}% / +${numText(r.upPct * 100, 2)}%`}</td>
              <td className={r.low == null ? 'is-na' : undefined} title={r.low == null ? naWhy(r.reason) : undefined}>{r.low == null ? na : `${priceText(r.low, na)} – ${priceText(r.high, na)}`}</td>
            </tr>;
          })}</tbody>
        </table></div>
      </Card>
    </div>
  </div>;
}
