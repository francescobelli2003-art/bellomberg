import { useEffect, useRef, useState } from 'react';
import type { MutableRefObject } from 'react';
import { Check, Loader2, Plus } from 'lucide-react';
import { Bellomberg, type MktSearchHit } from '@/lib/api';
import { leggiDetail } from '@/lib/quota';
import IconaTitolo from '@/components/nuova/IconaTitolo';
import type { Parole } from './parole';

const erroreFonte = (e: any) => leggiDetail(e?.response?.data?.detail) || leggiDetail(e?.message) || '—';
const campoAttivo = (el: Element | null) => !!el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || (el as HTMLElement).isContentEditable);

/** Aggiunta di un preferito dalla testata: ricerca globale dei titoli (Yahoo, via /market/search)
 *  con 300 ms di attesa dopo l'ultimo tasto. Frecce per scorrere, Invio aggiunge, Esc chiude,
 *  «/» porta il fuoco qui. Un titolo già seguito resta in elenco ma non si può aggiungere due volte. */
export default function AggiungiTitolo({ w, inputRef, preferiti, aggiungo, onAggiungi }: {
  w: Parole;
  inputRef: MutableRefObject<HTMLInputElement | null>;
  preferiti: Set<string>;
  aggiungo: string | null;
  onAggiungi: (h: MktSearchHit) => void;
}) {
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<MktSearchHit[]>([]);
  const [aperto, setAperto] = useState(false);
  const [cerco, setCerco] = useState(false);
  const [errore, setErrore] = useState<string | null>(null);
  const [attivo, setAttivo] = useState(0);
  const [cercato, setCercato] = useState(false);
  const boxRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!q.trim()) { setHits([]); setErrore(null); setCerco(false); setCercato(false); return; }
    let vivo = true;
    setCerco(true); setErrore(null);
    const t = setTimeout(() => {
      Bellomberg.mktSearch(q.trim())
        .then(r => { if (vivo) { setHits(r.results || []); setAttivo(0); setAperto(true); setCercato(true); } })
        .catch(e => { if (vivo) { setHits([]); setErrore(erroreFonte(e)); setCercato(false); } })
        .finally(() => { if (vivo) setCerco(false); });
    }, 300);
    return () => { vivo = false; clearTimeout(t); };
  }, [q]);

  // un clic fuori chiude il menu
  useEffect(() => {
    if (!aperto || typeof document === 'undefined' || typeof document.addEventListener !== 'function') return;
    const fuori = (e: MouseEvent) => { if (!boxRef.current?.contains(e.target as Node)) setAperto(false); };
    document.addEventListener('mousedown', fuori);
    return () => document.removeEventListener('mousedown', fuori);
  }, [aperto]);

  // «/» da qualsiasi punto della pagina (non mentre si scrive in un altro campo)
  useEffect(() => {
    if (typeof document === 'undefined' || typeof document.addEventListener !== 'function') return;
    const tasto = (e: KeyboardEvent) => {
      if (e.key !== '/' || e.metaKey || e.ctrlKey || e.altKey || campoAttivo(document.activeElement)) return;
      e.preventDefault(); inputRef.current?.focus();
    };
    document.addEventListener('keydown', tasto);
    return () => document.removeEventListener('keydown', tasto);
  }, [inputRef]);

  const scegli = (h: MktSearchHit) => {
    if (preferiti.has(h.symbol.toUpperCase()) || aggiungo) return;
    onAggiungi(h);
    setQ(''); setHits([]); setAperto(false); setCercato(false);
  };
  const mostra = aperto && !!q.trim() && !cerco && (errore !== null || cercato);

  return (
    <div className="pf-add" ref={boxRef}>
      <label className="pf-field pf-addf">
        {aggiungo || cerco ? <Loader2 size={14} className="pf-spin" aria-hidden="true" /> : <Plus size={14} aria-hidden="true" />}
        <input ref={inputRef} value={q} aria-label={w.addLabel} placeholder={aggiungo ? w.adding : w.addHint} autoComplete="off"
          role="combobox" aria-expanded={mostra} aria-controls="pf-add-menu" disabled={!!aggiungo}
          onMouseDown={() => setAperto(true)}
          onChange={e => { setQ(e.target.value); setAperto(true); }}
          onKeyDown={e => {
            if (e.key === 'ArrowDown') { e.preventDefault(); setAperto(true); setAttivo(i => Math.min(i + 1, Math.max(hits.length - 1, 0))); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); setAttivo(i => Math.max(i - 1, 0)); }
            else if (e.key === 'Enter' && hits[attivo]) { e.preventDefault(); scegli(hits[attivo]); }
            else if (e.key === 'Escape') { setAperto(false); setQ(''); }
          }} />
        <kbd>/</kbd>
      </label>
      {mostra && (
        <div className="pf-pop" id="pf-add-menu" role="listbox" aria-label={w.addResults}>
          {errore !== null ? <div className="pf-pop-msg" role="alert">{w.addFailed(errore)}</div>
            : hits.length === 0 ? <div className="pf-pop-msg" role="status">{w.addNone(q.trim())}</div>
            : <>
                <h6>{w.addResults}</h6>
                {hits.map((h, i) => {
                  const gia = preferiti.has(h.symbol.toUpperCase());
                  return (
                    <button key={h.symbol + h.exchange} type="button" role="option" aria-selected={i === attivo} aria-disabled={gia}
                      className={'pf-hit' + (i === attivo ? ' is-act' : '')} data-ticker={h.symbol}
                      onMouseEnter={() => setAttivo(i)} onMouseDown={e => e.preventDefault()} onClick={() => scegli(h)}>
                      <IconaTitolo ticker={h.symbol} nome={h.name} dimensione="sm" />
                      <span className="pf-hit-name"><b>{h.symbol}{h.name ? ' · ' + h.name : ''}</b><small>{[h.exchange, h.type].filter(Boolean).join(' · ')}</small></span>
                      {gia ? <span className="pf-hit-act is-gia"><Check size={13} aria-hidden="true" />{w.addAlready}</span>
                        : <span className="pf-hit-act"><Plus size={13} aria-hidden="true" />{w.addAction}</span>}
                    </button>
                  );
                })}
              </>}
        </div>
      )}
    </div>
  );
}
