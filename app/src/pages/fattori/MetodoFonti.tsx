import { useEffect, useRef } from 'react';
import { X } from 'lucide-react';
import type { Parole } from './parole';

export interface SezioneMetodo { titolo: string; avviso?: boolean; testo?: string[]; voci?: Array<[string, string]> }

/** Pannello laterale «Metodo e fonti»: definizioni verbatim, qualità delle stime, allineamento
 *  delle serie e costo del calcolo. Esc o il velo lo chiudono; il focus torna a chi l'ha aperto. */
export default function MetodoFonti({ aperto, onChiudi, sezioni, w }: {
  aperto: boolean;
  onChiudi: () => void;
  sezioni: SezioneMetodo[];
  w: Parole;
}) {
  const chiudi = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!aperto) return;
    const prima = document.activeElement as HTMLElement | null;
    chiudi.current?.focus();
    return () => { try { prima?.focus(); } catch { /* elemento smontato */ } };
  }, [aperto]);
  if (!aperto) return null;
  return (
    <div className="bbn-drawer-root fat-method" role="dialog" aria-modal="true" aria-label={w.method}
      onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); onChiudi(); } }}>
      <div className="bbn-scrim" onClick={onChiudi} aria-hidden="true" />
      <div className="bbn-drawer">
        <div className="bbn-drawer-head">
          <div className="bbn-drawer-title"><b>{w.method}</b></div>
          <button ref={chiudi} type="button" className="bbn-icon-btn" onClick={onChiudi} aria-label={w.close}><X size={16} /></button>
        </div>
        {sezioni.map(s => (
          <section key={s.titolo} className={s.avviso ? 'is-warn' : undefined}>
            <h3>{s.titolo}</h3>
            {s.testo?.map((t, i) => <p key={i}>{t}</p>)}
            {s.voci && s.voci.length > 0 && <dl>{s.voci.map(([k, v], i) => <div key={k + i}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>}
          </section>
        ))}
      </div>
    </div>
  );
}
