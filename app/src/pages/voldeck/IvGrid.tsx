import { useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { t as tr } from '@/i18n/t';
import { finite, ivText, numText, priceText, type SurfaceModel } from '@/lib/vol-deck';
import { colorRange, inkOn, scaleColor } from './Surface3D';
import type { AxisMode } from './SliceCharts';

/* ============================================================================
   Griglia numerica della superficie (Vol Deck v3, 10/10/2026 — Opus 5.5): scadenze × K/S (o strike
   equivalente) con l'IV di griglia del builder. Stessa scala colore e stessi estremi (p1/p99) del 3D;
   il testo e' nero o bianco secondo il contrasto del fondo. Un buco resta un buco: cella tratteggiata
   «n.d.», mai riempita. Clic su cella = sceglie scadenza e K/S (3D e fette si allineano); clic
   sull'intestazione di riga o colonna = sceglie solo quella. Al passaggio: croce di riga e colonna e
   lettura esatta sotto la tabella.
   v2 (review 10/10): tastiera a tabindex mobile (una sola cella nel Tab, frecce per muoversi, Invio o
   Spazio per scegliere; il focus da' la stessa lettura esatta dell'hover); ⚠ parziale con testo accessibile.
   ========================================================================== */
export default function IvGrid({ model, axis, expiry, column, onPick, onExpiry, onColumn }: {
  model: SurfaceModel; axis: AxisMode; expiry: string | null; column: number | null;
  onPick: (expiry: string, column: number) => void; onExpiry: (expiry: string) => void; onColumn: (column: number) => void;
}) {
  const [hover, setHover] = useState<{ r: number; c: number } | null>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const cells = useRef(new Map<string, HTMLTableCellElement>());
  const na = tr('voldeck.ui_n_a_15');
  const range = colorRange(model);
  const strikeAxis = axis === 'strike' && model.spot != null;
  // K/S coi decimali che servono (0,825 resta 0,825: mai arrotondato a un valore che la griglia non ha)
  const head = (i: number) => strikeAxis ? priceText(model.strikes[i], na)
    : numText(model.grid[i], Math.abs(model.grid[i] * 100 - Math.round(model.grid[i] * 100)) > 1e-9 ? 3 : 2, na);
  const short = (e: string) => `${e.slice(8, 10)}/${e.slice(5, 7)}/${e.slice(2, 4)}`;
  const at = hover && model.rows[hover.r] ? { row: model.rows[hover.r], c: hover.c } : null;
  // la cella nel Tab: quella scelta (o la prima)
  const fr = Math.max(0, model.rows.findIndex(r => r.expiry === expiry)), fc = column != null && column >= 0 && column < model.grid.length ? column : 0;
  const onKey = (j: number, i: number) => (e: KeyboardEvent<HTMLTableCellElement>) => {
    const step: Record<string, [number, number]> = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] };
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onPick(model.rows[j].expiry, i); return; }
    const d = step[e.key]; if (!d) return;
    e.preventDefault();
    const nj = Math.max(0, Math.min(model.rows.length - 1, j + d[0])), ni = Math.max(0, Math.min(model.grid.length - 1, i + d[1]));
    cells.current.get(nj + ':' + ni)?.focus();
  };
  const cell = (j: number, i: number) => ({ tabIndex: j === fr && i === fc ? 0 : -1, 'data-r': j, 'data-c': i,
    ref: (el: HTMLTableCellElement | null) => { if (el) cells.current.set(j + ':' + i, el); else cells.current.delete(j + ':' + i); },
    onFocus: () => setHover({ r: j, c: i }), onKeyDown: onKey(j, i) });
  const fill = (v: number) => {
    const t = range ? (v * 100 - range.cmin) / ((range.cmax - range.cmin) || 1) : 0.5;
    const rgb = scaleColor(t);
    return { background: `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`, color: inkOn(rgb) };
  };
  return <div className="vdn-ivgrid-wrap" ref={wrap} data-vol-grid onPointerLeave={() => setHover(null)}>
    <div className="vdn-ivgrid-scroll">
      <table className="vdn-ivgrid num" aria-label={tr('voldeck.n_grid_aria')}>
        <thead><tr>
          <th className="l" scope="col">{tr('voldeck.ui_expiry_177')}</th>
          {model.grid.map((m, i) => <th key={i} scope="col" className={(i === column ? 'is-sel' : '') + (hover?.c === i ? ' is-cross' : '') + (Math.abs(m - 1) < 1e-9 ? ' is-atm' : '')}>
            <button type="button" aria-pressed={i === column} onClick={() => onColumn(i)}>{head(i)}</button></th>)}
        </tr></thead>
        <tbody>{model.rows.map((r, j) => <tr key={r.expiry} className={r.expiry === expiry ? 'is-sel' : undefined}>
          <th scope="row" className={'l' + (hover?.r === j ? ' is-cross' : '')}>
            <button type="button" aria-pressed={r.expiry === expiry} onClick={() => onExpiry(r.expiry)}>
              <b>{short(r.expiry)}</b><span>{r.days}{tr('voldeck.short_days')}</span>
              {r.partial && <em className="vdn-warn-text" title={tr('voldeck.n_partial_badge')}><span aria-hidden="true">⚠</span><span className="vdn-sr">{tr('voldeck.n_partial_sr')}</span></em>}
            </button>
          </th>
          {r.iv.map((v, i) => {
            const cls = [(r.expiry === expiry && i === column) ? 'is-pick' : '', hover && (hover.r === j || hover.c === i) ? 'is-cross' : '',
              hover && hover.r === j && hover.c === i ? 'is-hover' : ''].filter(Boolean).join(' ');
            const title = `${r.expiry} · K/S ${numText(model.grid[i], 3, na)} · ${tr('voldeck.n_iv_grid')} ${v == null ? tr('voldeck.n_hole_word') : ivText(v, na, 2)}`;
            return v == null || !finite(v)
              ? <td key={i} className={'is-hole ' + cls} title={title} data-vol-grid-hole {...cell(j, i)}
                  onPointerEnter={() => setHover({ r: j, c: i })} onClick={() => onPick(r.expiry, i)}>{na}</td>
              : <td key={i} className={cls} style={fill(v)} title={title} {...cell(j, i)}
                  onPointerEnter={() => setHover({ r: j, c: i })} onClick={() => onPick(r.expiry, i)}>{numText(v * 100, 1, na)}</td>;
          })}
        </tr>)}</tbody>
      </table>
    </div>
    <p className="vdn-ivgrid-read" aria-live="polite" data-vol-grid-read>
      {at ? <>
        <b>{at.row.expiry}</b> · {at.row.days}{tr('voldeck.short_days')} · K/S <b>{numText(model.grid[at.c], 3, na)}</b>
        {' '}· {tr('voldeck.n_strike_eq_short')} {priceText(model.strikes[at.c], na)} · {tr('voldeck.n_iv_grid')}{' '}
        <b className={at.row.iv[at.c] == null ? 'vdn-warn-text' : undefined}>{at.row.iv[at.c] == null ? tr('voldeck.n_hole_word') : ivText(at.row.iv[at.c], na, 2)}</b>
      </> : <span>{tr('voldeck.n_grid_hint')}</span>}
    </p>
  </div>;
}
