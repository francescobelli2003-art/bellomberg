/* Contatore della voce Decisioni nel menu (05/10/2026): decisioni ancora da prendere
   (stato PENDING, la stessa lettura del pulsante «Vedi decisioni» della Dashboard).
   Si rilegge ogni 2 minuti e quando si cambia pagina; una lettura fallita toglie il
   contatore, mai uno zero inventato. Componente a parte come BadgeFiling. */
import { useEffect, useState } from 'react';
import { Bellomberg } from '@/lib/api';

const OGNI_MS = 2 * 60 * 1000;

export default function BadgeDecisioni({ percorso, etichetta }: { percorso: string; etichetta: (n: number) => string }) {
  const [n, setN] = useState<number | null>(null);
  useEffect(() => {
    let vivo = true;
    const leggi = () => {
      Promise.resolve().then(() => Bellomberg.decisions('PENDING', -1))
        .then(r => { if (vivo) setN(Array.isArray(r?.decisions) ? r.decisions.length : null); })
        .catch(() => { if (vivo) setN(null); });
    };
    leggi();
    const id = setInterval(leggi, OGNI_MS);
    return () => { vivo = false; clearInterval(id); };
  }, [percorso]);
  return n != null && n > 0 ? <b className="bb-nav-count" data-decisions-badge={n} aria-label={etichetta(n)}>{n}</b> : null;
}
