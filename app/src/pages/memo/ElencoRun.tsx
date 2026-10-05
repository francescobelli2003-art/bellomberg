import { Archive } from 'lucide-react';
import type { Decision, Memo } from '@/lib/api';
import { fmtEUR } from '@/lib/format';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import { ORD, conta, eseguite } from './logica';
import { parole } from './parole';

export interface Campione { rows: number; excluded: number | null }

/** Barra impilata degli esiti di un memo: prima il fatto, in fondo l'evaporato. */
export function BarraEsiti({ ds, grande = false }: { ds: Decision[]; grande?: boolean }) {
  const w = parole(), c = conta(ds);
  const titolo = ORD.filter(s => c[s]).map(s => `${w.legenda[s]} ${c[s]}`).join(' · ');
  return (
    <span className={'mm-stk' + (grande ? ' is-lg' : '')} title={titolo} role="img" aria-label={titolo}>
      {ORD.map(s => c[s] ? <i key={s} className={`mm-e-${s}`} style={{ flex: c[s] }} /> : null)}
    </span>
  );
}

/** Colonna sinistra: le run del Comitato raggruppate per mese. Presentazionale: lo stato sta nella pagina. */
export default function ElencoRun({ memos, loading, errore, selId, onSelect, decPerMemo, decErr, decLoading, campione, righeDbErr }: {
  memos: Memo[]; loading: boolean; errore: boolean; selId: number | null; onSelect: (id: number) => void;
  decPerMemo: Map<number, Decision[]>; decErr: string | null; decLoading: boolean;
  campione: Campione | null; righeDbErr: string | null;
}) {
  const w = parole(), loc = localeDi(linguaCorrente());
  const gruppi: { mese: string; voci: Memo[] }[] = [];
  memos.forEach(m => {
    const d = new Date(m.timestamp);
    const mese = Number.isFinite(d.getTime()) ? d.toLocaleDateString(loc, { month: 'long', year: 'numeric' }) : w.nd;
    const g = gruppi[gruppi.length - 1];
    if (g && g.mese === mese) g.voci.push(m); else gruppi.push({ mese, voci: [m] });
  });

  const piede = righeDbErr !== null ? w.sampleError(righeDbErr || w.unknown)
    : campione === null ? w.sampleLoading
    : campione.excluded === null ? w.sampleUndeclared(campione.rows)
    : w.sampleCount(campione.excluded, campione.rows);

  return (
    <section className="bbn-card mm-runs" aria-labelledby="mm-runs-t">
      <header className="mm-card-head">
        <span className="mm-ci" aria-hidden="true"><Archive size={16} /></span>
        <h2 id="mm-runs-t">{w.runs}</h2>
        {!errore && !loading && <span className="mm-card-note">{w.runsNote(memos.length)}</span>}
      </header>
      <div className="mm-list" data-qa="memo-runs">
        {errore ? <p className="mm-empty">{w.noMemoErr}</p>
          : loading ? <p className="mm-empty" role="status">{w.loading}</p>
          : memos.length === 0 ? <p className="mm-empty" data-qa="memo-runs-empty">{w.empty}</p>
          : gruppi.map(g => (
            <div key={g.mese} role="group" aria-label={g.mese}>
              <div className="mm-month"><span>{g.mese}</span><span>{g.voci.length}</span></div>
              {g.voci.map(m => {
                const d = new Date(m.timestamp), ok = Number.isFinite(d.getTime());
                const ds = decPerMemo.get(m.id) || [];
                const riga = decErr !== null ? w.decisionsNd : decLoading ? '…' : w.runLine(w.decisions(ds.length), eseguite(conta(ds)));
                const nav = typeof m.portfolio_nav_eur === 'number' && Number.isFinite(m.portfolio_nav_eur) ? m.portfolio_nav_eur : null;
                return (
                  <button key={m.id} type="button" className={'mm-run' + (m.id === selId ? ' is-on' : '')}
                    aria-current={m.id === selId ? 'true' : undefined} aria-label={w.runOpen(m.id)}
                    data-memo-id={m.id} onClick={() => onSelect(m.id)}>
                    <span className="mm-dd" aria-hidden="true">
                      <small>{ok ? d.toLocaleDateString(loc, { weekday: 'short' }).replace('.', '') : ''}</small>
                      <b>{ok ? d.getDate() : '—'}</b>
                    </span>
                    <span className="mm-run-tx">
                      <b>Memo {m.id}{m.kind === 'trade_idea' && m.label ? <span className="mm-tag">{m.label}</span> : null}</b>
                      <span>{riga}</span>
                      {ds.length > 0 && <BarraEsiti ds={ds} />}
                    </span>
                    <span className="mm-run-r" title={w.securitiesBasis}>
                      {nav !== null ? <><b className="num">{fmtEUR(nav, false, 0)}</b><small>{w.invested}</small></>
                        : <small>{w.investedNd}</small>}
                    </span>
                  </button>
                );
              })}
            </div>
          ))}
      </div>
      {!errore && <p className="mm-foot" data-qa="memo-sample">{piede}</p>}
    </section>
  );
}
