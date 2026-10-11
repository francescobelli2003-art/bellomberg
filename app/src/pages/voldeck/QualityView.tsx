import Card from '@/components/nuova/Card';
import { t as tr } from '@/i18n/t';
import { finite, numText, priceText, type SurfaceModel } from '@/lib/vol-deck';
import IvGrid from './IvGrid';
import type { VolQuant } from './quant';
import { flagLine, kindText, levelText, magnitudeText, originText, ratioOfFlag, sourceText, whyText } from './quantText';

/* ============================================================================
   Vista «Qualita'» del Vol Deck (10/10/2026, Opus 5.5). Due domande: «la superficie e' priva di
   arbitraggi?» e «cosa e' cambiato dall'ultimo download?». Numeri: lib/vol-quant
   (arbitrageChecks sulla griglia del builder e sulle quote osservate, surfaceDiff).
   ========================================================================== */
/** Da dove viene il precedente del ΔIV: archivio del backend (preferito), cache locale (backend non raggiungibile), nessuno. */
export type PreviousSource = 'backend' | 'cache' | 'none' | 'loading';
export interface DiffInfo {
  nowText: string; prevText: string | null; persisted: boolean;
  source?: PreviousSource; identical?: boolean; archiveError?: string | null;
  store?: { persisted: boolean; discarded: number; total: number; max: number }; onClear?: () => void;
  /** retention dichiarata dall'archivio del backend (giorni, istantanee per sottostante) */
  retention?: { days: number | null; max_per_ticker: number | null } | null;
}

/** Striscia del confronto: date, asse, fonte del precedente, archivio locale (limite, voci scartate) e pulsante di pulizia. */
export function DiffBar({ info, axis }: { info: DiffInfo; axis: 'K/F' | 'K/S' | null }) {
  const src = info.source ?? 'none';
  const none = !info.prevText;
  return <div className={'vdn-diffbar' + (none ? ' is-na' : '')} data-vol-diff-dates data-vol-diff-source={src}>
    <span>{info.identical && info.prevText ? tr('voldeck.q_diff_identical', { d: info.prevText })
      : none ? tr('voldeck.q_diff_none') : tr('voldeck.q_diff_dates', { a: info.nowText, b: info.prevText as string }) + (axis ? ' · ' + tr(axis === 'K/F' ? 'voldeck.q_diff_axis_kf' : 'voldeck.q_diff_axis_ks') : '')}</span>
    <span className="vdn-diffbar-src">{tr(`voldeck.q_prev_src_${src}`, { e: info.archiveError || '' })}
      {src === 'backend' && info.retention ? ' · ' + tr('voldeck.q_archive_retention', { d: info.retention.days ?? '—', n: info.retention.max_per_ticker ?? '—' }) : ''}</span>
    {info.store && <span className="vdn-diffbar-src" data-vol-cache-status>{tr('voldeck.q_cache_status', { n: info.store.total, m: info.store.max })}
      {info.store.discarded ? ' · ' + tr('voldeck.q_cache_discarded', { n: info.store.discarded }) : ''}{!info.persisted ? ' · ' + tr('voldeck.q_diff_memory') : ''}</span>}
    {info.onClear && <button type="button" className="bbn-link" data-vol-cache-clear onClick={info.onClear}>{tr('voldeck.q_cache_clear')}</button>}
  </div>;
}

/** Riquadro compatto per la griglia della vista Superficie: eseguibili e indicativi contati a parte. */
export function QualitySummary({ q, onOpen }: { q: VolQuant; onOpen: () => void }) {
  const worst = q.flags[0];
  return <button type="button" className={'vdn-quality-chip' + (q.nExecutable ? ' is-bad' : q.nIndicative ? ' is-ind' : '')} onClick={onOpen}
    data-vol-quality-chip={q.flags.length} data-vol-quality-exec={q.nExecutable} data-vol-quality-ind={q.nIndicative} title={worst ? flagLine(worst) : undefined}>
    {q.flags.length ? tr('voldeck.q_quality_chip', { e: q.nExecutable, i: q.nIndicative }) : q.arb.clean === null ? tr('voldeck.q_quality_unchecked') : tr('voldeck.q_quality_clean')}
  </button>;
}

export default function QualityView({ q, model, expiry, column, onPick, onExpiry, onColumn, diff, onShowDiff, part }: {
  q: VolQuant; model: SurfaceModel; expiry: string | null; column: number | null;
  onPick: (e: string, c: number) => void; onExpiry: (e: string) => void; onColumn: (c: number) => void;
  diff: DiffInfo; onShowDiff: () => void;
  /** sotto-pagina: 'flags' = Coerenza (arbitraggi), 'diff' = Variazioni (ΔIV); assente = entrambe */
  part?: 'flags' | 'diff';
}) {
  const na = tr('voldeck.ui_n_a_15');
  const grid = q.flags.filter(f => f.set === 'grid').length, obs = q.flags.filter(f => f.set === 'observed').length;
  const worst = q.flags[0] || null;
  const skipped = [...q.arb.skipped, ...(q.arbObserved?.skipped || [])];
  const cells = q.diffCells?.flat().filter(finite) || [];
  const up = cells.length ? Math.max(...cells) : null, down = cells.length ? Math.min(...cells) : null;
  const flagMap = new Map<string, string[]>([...q.cellFlags].map(([k, ids]) => [k, ids.map(i => flagLine(q.flags[i]))]));
  return <div className="vdn-view" data-vol-view-panel="quality">
    {part !== 'diff' && <Card className="vdn-wide-card" titolo={tr('voldeck.q_quality_title')} conteggio={tr('voldeck.q_quality_count', { g: q.arb.checked.calendarPairs, b: q.arb.checked.butterflyTriplets })} data-vol-quality>
      <div className="vdn-tiles is-four vdn-inner-tiles">
        <div className="vdn-tile"><span>{tr('voldeck.q_flags_exec')}</span><b className={q.nExecutable ? 'is-bad' : 'is-good'} data-vol-flags-exec>{q.nExecutable}</b>
          <small>{tr('voldeck.q_flags_exec_sub')}</small></div>
        <div className="vdn-tile"><span>{tr('voldeck.q_flags_ind')}</span><b className="is-muted" data-vol-flags-ind>{q.nIndicative}</b>
          <small>{tr('voldeck.q_flags_ind_sub', { t: q.arb.tolerances.ivNoise == null ? na : numText(q.arb.tolerances.ivNoise * 100, 1) })}</small></div>
        <div className="vdn-tile" title={worst ? flagLine(worst) : undefined}><span>{tr('voldeck.q_worst')}</span>
          <b className={worst ? (worst.level === 'executable' ? 'is-bad' : 'is-muted') : undefined} data-vol-worst>{worst ? kindText(worst.flag.kind) : '—'}</b>
          <small>{worst ? `${levelText(worst.level)} · ${worst.flag.expiries.join(' → ')} · ${ratioOfFlag(worst)}` : q.arb.clean === null ? tr('voldeck.q_quality_unchecked') : tr('voldeck.q_quality_clean')}</small></div>
        <div className="vdn-tile"><span>{tr('voldeck.q_checked')}</span><b>{q.arb.checked.expiries.length}/{model.rows.length}</b>
          <small>{tr('voldeck.q_flags_split', { g: grid, o: obs })} · {q.arbObserved ? tr('voldeck.q_checked_obs', { n: q.arbObserved.checked.expiries.length }) : tr('voldeck.q_checked_obs_none')}</small></div>
      </div>
      {q.flags.length > 0 && <div className="vdn-table-wrap vdn-scroll-box"><table className="vdn-table num" data-vol-flags-table>
        <thead><tr><th className="l">{tr('voldeck.q_col_level')}</th><th className="l">{tr('voldeck.q_col_kind')}</th><th className="l">{tr('voldeck.ui_expiry_177')}</th><th>Strike</th>
          <th className="l">{tr('voldeck.q_col_magnitude')}</th><th>{tr('voldeck.q_col_ratio')}</th><th className="l">{tr('voldeck.q_col_origin')}</th><th className="l">{tr('voldeck.q_col_source')}</th></tr></thead>
        <tbody>{q.flags.map(f => {
          const ks = f.flag.kind === 'calendar' ? f.flag.strikes.slice(0, 2) : f.flag.strikes;
          return <tr key={f.id} data-vol-flag-row={f.flag.kind} data-vol-flag-level={f.level} className={f.level === 'executable' ? 'is-exec' : 'is-ind'}
            onClick={() => f.cells[0] && onPick(f.cells[0].expiry, f.cells[0].col)} title={flagLine(f)}>
            <td className="l"><span className={'fat-pill ' + (f.level === 'executable' ? 'is-bad' : 'is-ind')}>{levelText(f.level)}</span></td>
            <td className="l">{kindText(f.flag.kind)}</td><td className="l">{f.flag.expiries.join(' → ')}</td>
            <td>{ks.length ? `${priceText(Math.min(...ks), na)}–${priceText(Math.max(...ks), na)}` : na}</td>
            <td className="l">{magnitudeText(f.flag, f.level)}</td><td>{ratioOfFlag(f)}</td>
            <td className="l">{originText(f.flag.origin)}</td><td className="l">{sourceText(f.set, f.flag.source)}</td>
          </tr>;
        })}</tbody>
      </table></div>}
      {skipped.length > 0 && <p className="vdn-legend">{tr('voldeck.q_skipped', { n: skipped.length })} {skipped.slice(0, 4).map(s => `${s.expiry}: ${whyText(s.reason)}`).join(' · ')}</p>}
    </Card>}
    {part !== 'flags' && <Card className="vdn-wide-card" titolo={tr('voldeck.q_diff_title')} data-vol-diff-card
      azioni={q.diff ? <button type="button" className="bbn-link" onClick={onShowDiff}>{tr('voldeck.q_diff_on_surface')}</button> : undefined}>
      <DiffBar info={diff} axis={q.diff?.axis ?? null} />
      {diff.identical && diff.prevText ? <p className="vdn-legend" data-vol-diff-identical>{tr('voldeck.q_diff_identical', { d: diff.prevText })}</p>
        : !q.diff ? <p className="vdn-legend vdn-warn-text" data-vol-diff-na>{tr('voldeck.q_diff_none')}</p> : <>
        <div className="vdn-chips vdn-diff-chips">
          <span className="fat-chip">{tr('voldeck.q_diff_up')} <b data-vol-diff-up>{up == null ? na : '+' + numText(up * 100, 2) + ' pt'}</b></span>
          <span className="fat-chip">{tr('voldeck.q_diff_down')} <b data-vol-diff-down>{down == null ? na : (down < 0 ? '−' : '+') + numText(Math.abs(down) * 100, 2) + ' pt'}</b></span>
          <span className="fat-chip">{tr('voldeck.q_diff_cells', { n: cells.length, c: model.cells })}</span>
        </div>
        <IvGrid model={model} axis="moneyness" expiry={expiry} column={column} onPick={onPick} onExpiry={onExpiry} onColumn={onColumn}
          extras={{ diff: { cells: q.diffCells || [], range: q.diffRange, line: diffLineOf(q) }, flags: flagMap, flagLevel: q.cellLevel }} />
      </>}
    </Card>}
  </div>;
}

/** Riga «ΔIV vs data» di una cella (valore col segno o motivo n.d.), condivisa da 3D e griglie. */
export function diffLineOf(q: VolQuant) {
  return (row: number, col: number): string => {
    const v = q.diffCells?.[row]?.[col];
    if (!q.diff) return tr('voldeck.q_diff_none');
    const sg = (x: number) => (x > 0 ? '+' : x < 0 ? '−' : '') + numText(Math.abs(x) * 100, 2);
    const slide = q.diffSlide?.[row]?.[col], rest = q.diffExSlide?.[row]?.[col];
    if (finite(v) && finite(slide) && finite(rest)) return tr('voldeck.q_diff_cell_slide', { v: sg(v), s: sg(slide), x: sg(rest) });
    return finite(v) ? tr('voldeck.q_diff_cell', { v: sg(v) })
      : tr('voldeck.q_diff_cell_na', { why: whyText(q.diffReasons?.[row]?.[col] ?? 'not_comparable') });
  };
}
