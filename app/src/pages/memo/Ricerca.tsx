import type { RefObject } from 'react';
import { Search } from 'lucide-react';
import type { Memo, MemoSearchHit } from '@/lib/api';
import { fmtNum } from '@/lib/format';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import { parole } from './parole';

/* Ricerca semantica nell'archivio: campo nell'intestazione, risultati in un pannello sotto.
   `distance` e' una distanza COSENO e si scrive com'e': tradurla in «rilevanza 87%» sarebbe
   un numero inventato. Lo stato vive nella pagina; qui solo la vista. */
export default function Ricerca({ aperta, q, hits, ms, errS, cercando, sel, memos, boxRef, inputRef,
  onApri, onQ, onTasto, onScegli, onOpen }: {
  aperta: boolean; q: string; hits: MemoSearchHit[] | null; ms: number | null; errS: string | null; cercando: boolean;
  sel: number; memos: Memo[]; boxRef: RefObject<HTMLDivElement>; inputRef: RefObject<HTMLInputElement>;
  onApri: () => void; onQ: (q: string) => void; onTasto: (e: React.KeyboardEvent<HTMLInputElement>) => void;
  onScegli: (i: number) => void; onOpen: (memoId: number) => void;
}) {
  const w = parole(), loc = localeDi(linguaCorrente());
  const byId = new Map(memos.map(m => [m.id, m]));
  const lista = hits || [];
  const dMin = lista.length ? Math.min(...lista.map(h => h.distance)) : 0;
  const dMax = lista.length ? Math.max(...lista.map(h => h.distance)) : 1;
  const nMemo = new Set(lista.map(h => h.memo_id)).size;
  const idLista = 'mm-search-list';

  return (
    <div className="mm-search" ref={boxRef}>
      <label className={'mm-field' + (aperta ? ' is-on' : '')}>
        <Search size={14} aria-hidden="true" />
        <span className="mm-sr">{w.searchLabel}</span>
        <input ref={inputRef} type="search" value={q} placeholder={w.searchPh} autoComplete="off" data-qa="memo-search"
          role="combobox" aria-expanded={aperta} aria-controls={idLista} aria-autocomplete="list"
          aria-activedescendant={aperta && lista.length ? `mm-hit-${sel}` : undefined}
          onFocus={onApri} onClick={onApri} onChange={e => onQ(e.target.value)} onKeyDown={onTasto} />
        <kbd>{w.searchKbd}</kbd>
      </label>
      {aperta && (
        <div className="mm-drop" data-qa="memo-search-panel">
          <div className="mm-drop-h" aria-live="polite">
            {cercando ? <span>{w.searching}</span>
              : errS !== null ? <span className="is-bad">{w.searchErr(errS || w.unknown)}</span>
              : hits === null ? <span>{w.searchHint}</span>
              : hits.length === 0 ? <span>{w.searchNone(q.trim())}</span>
              : <><span>{w.searchHits(hits.length, nMemo, ms ?? 0)}</span><span className="bbn-grow" /><span title={w.searchOrderHint}>{w.searchOrder}</span></>}
          </div>
          {lista.length > 0 && errS === null && (
            <div className="mm-drop-list" id={idLista} role="listbox" aria-label={w.searchLabel}>
              {lista.map((h, i) => {
                const m = byId.get(h.memo_id), d = m ? new Date(m.timestamp) : null;
                const vicino = 100 - (dMax === dMin ? 0 : (h.distance - dMin) / (dMax - dMin)) * 70;
                return (
                  <div key={h.chunk_id} id={`mm-hit-${i}`} role="option" aria-selected={i === sel}
                    className={'mm-hit' + (i === sel ? ' is-on' : '')} data-hit-memo={h.memo_id}
                    onMouseEnter={() => onScegli(i)} onClick={() => onOpen(h.memo_id)}>
                    <span className="mm-hit-l">
                      <b>Memo {h.memo_id}</b>
                      <span>{d && Number.isFinite(d.getTime()) ? d.toLocaleDateString(loc, { day: 'numeric', month: 'short' }) : w.noText}</span>
                      <span title={w.distHint} className="num">{w.dist(fmtNum(h.distance, 4))}</span>
                      <span className="mm-vic" aria-hidden="true"><i style={{ width: `${Math.round(vicino)}%` }} /></span>
                    </span>
                    <p>{(h.content || '').replace(/\s+/g, ' ').trim()}</p>
                  </div>
                );
              })}
            </div>
          )}
          <div className="mm-drop-f">
            <span><b>{w.keyArrows}</b> {w.keyPick}</span>
            <span><b>{w.keyEnter}</b> {w.keyOpen}</span>
            <span><b>{w.keyEsc}</b> {w.keyClose}</span>
            <span className="mm-drop-note">{w.searchFoot}</span>
          </div>
        </div>
      )}
    </div>
  );
}
