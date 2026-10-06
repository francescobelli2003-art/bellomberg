/* Badge della voce Filing nel menu (fase E, 03/10/2026): titoli del portafoglio con un confronto
   più recente dell'ultima run del Comitato. Lettura leggera (GET /filings/novita, nessuna rete
   esterna), all'avvio, ogni 10 minuti e dopo le azioni della pagina Filing. Componente a parte:
   i suoi hook non toccano l'ordine di quelli di Layout.
   Review PR #14: il numero visibile è aria-hidden e la frase per i lettori di schermo è un
   testo fratello, così entra nel nome accessibile del link (v. BadgeDecisioni).
   Terza tornata: il numero letto si riferisce anche a Layout (`segnala`) per l'intestazione del
   gruppo chiuso; la lettura resta solo qui. */
import { useEffect, useState } from 'react';
import { Bellomberg } from '@/lib/api';

export const FILING_EVENTO = 'bb:filing-changed';
const OGNI_MS = 10 * 60 * 1000;

export default function BadgeFiling({ etichetta, segnala }: {
  etichetta: (n: number) => string;
  // solo cose stabili (un setState): l'effetto la cattura una volta, al montaggio
  segnala: (n: number) => void;
}) {
  const [n, setN] = useState(0);
  useEffect(() => {
    let vivo = true;
    // Promise.resolve: anche un client senza il metodo (stub, backend vecchio) resta un badge assente
    const leggi = () => { Promise.resolve().then(() => Bellomberg.filingNovita()).then(r => { if (vivo) { setN(r.n); segnala(r.n); } }).catch(() => { /* badge assente, mai un errore nel menu */ }); };
    leggi();
    const id = setInterval(leggi, OGNI_MS);
    window.addEventListener(FILING_EVENTO, leggi);
    return () => { vivo = false; clearInterval(id); window.removeEventListener(FILING_EVENTO, leggi); };
  }, []);
  return n > 0 ? <>
    <span className="bb-nav-badge" data-filing-badge={n} aria-hidden="true">{n}</span>
    <small className="sr-only">{`, ${etichetta(n)}`}</small>
  </> : null;
}
