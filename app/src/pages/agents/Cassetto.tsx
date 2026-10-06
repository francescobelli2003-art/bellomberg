import { useEffect, type ReactNode } from 'react';
import { X } from 'lucide-react';
import { usaFocusPannello } from '@/lib/usaFocusPannello';

/* Cassetto laterale (redesign 05/10/2026): report di un desk e dettagli della run.
   Resta montato anche chiuso (visibility:hidden, fuori schermo): i test SSR leggono i
   pannelli e i desktop non devono aspettare un montaggio. Esc e il velo lo chiudono; aperto,
   il focus entra, TAB gira dentro e alla chiusura torna a chi l'ha aperto (usaFocusPannello). */
export default function Cassetto({ aperto, onChiudi, etichetta, chiudi, testa, schede, children, classe = '' }: {
  aperto: boolean; onChiudi: () => void; etichetta: string; chiudi: string;
  testa: ReactNode; schede?: ReactNode; children: ReactNode; classe?: string;
}) {
  const fuoco = usaFocusPannello<HTMLElement>(aperto, onChiudi);
  useEffect(() => {
    if (!aperto) return;
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onChiudi(); };
    window.addEventListener('keydown', esc);
    return () => window.removeEventListener('keydown', esc);
  }, [aperto, onChiudi]);
  return (
    <div className={'ag-cassetto' + (aperto ? ' is-aperto' : '') + (classe ? ' ' + classe : '')} data-aperto={aperto ? '1' : '0'}>
      <div className="ag-velo" onClick={onChiudi} aria-hidden="true" />
      <aside ref={fuoco.ref} className="ag-cass" role="dialog" aria-modal={aperto} aria-label={etichetta} aria-hidden={!aperto}
        onKeyDown={aperto ? fuoco.onKeyDown : undefined}>
        <header className="ag-cass-h">{testa}
          <button type="button" className="ag-cass-x" onClick={onChiudi} aria-label={chiudi} title={chiudi}><X size={18} /></button>
        </header>
        {schede && <div className="ag-cass-tabs">{schede}</div>}
        <div className="ag-cass-b">{children}</div>
      </aside>
    </div>
  );
}
