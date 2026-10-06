import { X } from 'lucide-react';
import type { Parole } from './parole';
import { usaFocusPannello } from '@/lib/usaFocusPannello';

export interface SezioneMetodo { titolo: string; avviso?: boolean; testo?: string[]; voci?: Array<[string, string]> }

/** Pannello laterale «Metodo e fonti»: definizioni verbatim, qualità delle stime, allineamento
 *  delle serie e costo del calcolo. Esc o il velo lo chiudono; il focus entra nel pannello, TAB
 *  gira solo dentro e alla chiusura torna a chi l'ha aperto (usaFocusPannello). */
export default function MetodoFonti({ aperto, onChiudi, sezioni, w }: {
  aperto: boolean;
  onChiudi: () => void;
  sezioni: SezioneMetodo[];
  w: Parole;
}) {
  const fuoco = usaFocusPannello<HTMLDivElement>(aperto, onChiudi);
  if (!aperto) return null;
  return (
    <div ref={fuoco.ref} className="bbn-drawer-root fat-method" role="dialog" aria-modal="true" aria-label={w.method}
      onKeyDown={fuoco.onKeyDown}>
      <div className="bbn-scrim" onClick={onChiudi} aria-hidden="true" />
      <div className="bbn-drawer">
        <div className="bbn-drawer-head">
          <div className="bbn-drawer-title"><b>{w.method}</b></div>
          <button type="button" className="bbn-icon-btn" onClick={onChiudi} aria-label={w.close}><X size={16} /></button>
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
