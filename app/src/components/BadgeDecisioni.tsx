/* Contatore della voce Decisioni nel menu (05/10/2026): decisioni ancora da prendere
   (stato PENDING, la stessa lettura del pulsante «Vedi decisioni» della Dashboard).
   Una lettura fallita o una risposta senza l'elenco NON spegne il contatore (sarebbe uguale
   a «nessuna decisione», fallback muto vietato dalla regola 14/07): mostra N.D. dichiarato.
   Review PR #14 (Opus 5.5). Componente a parte come BadgeFiling.

   Quando si rilegge (review PR #14, seconda tornata): al cambio pagina, quando la finestra
   torna in primo piano (focus / visibilitychange) e ogni 5 minuti a finestra visibile.
   MAI al ridimensionamento: i test desktop contano le richieste durante le catture a più
   larghezze e un GET /decisions lì è un errore. Per lo stesso motivo il giro periodico è
   lungo (una cattura dura meno) e salta le finestre nascoste: al ritorno ci pensa
   visibilitychange. Focus e visibilitychange arrivano spesso insieme: una lettura in volo
   o una appena fatta (< 15 s) non ne fa partire un'altra.

   Il contatore visibile è aria-hidden; la frase completa sta in un testo per lettori di
   schermo FRATELLO del badge, così entra nel nome accessibile del link (un aria-label sul
   link lo sostituirebbe e il contatore sparirebbe; uno sul <b> non è affidabile).

   Review PR #14, terza tornata: ogni lettura arrivata (numero o N.D.) si riferisce anche a Layout
   con `segnala`, che la mostra sull'intestazione del gruppo quando il gruppo è chiuso. Il badge
   resta l'unico a leggere: nessuna richiesta in più, e nessuna al ridimensionamento. */
import { useEffect, useState } from 'react';
import { Bellomberg } from '@/lib/api';

const OGNI_MS = 5 * 60 * 1000;
const PAUSA_MIN_MS = 15 * 1000;

export type Lettura = { stato: 'attesa' } | { stato: 'ok'; n: number } | { stato: 'ignota' };

export default function BadgeDecisioni({ percorso, etichetta, ignota, nd, segnala }: {
  percorso: string; etichetta: (n: number) => string; ignota: string; nd: string;
  // deve usare solo cose stabili (un setState): l'effetto la cattura e si rifà solo al cambio pagina
  segnala: (lettura: Lettura) => void;
}) {
  const [lettura, setLettura] = useState<Lettura>({ stato: 'attesa' });
  useEffect(() => {
    let vivo = true, inVolo = false, ultima = -Infinity;
    const nascosta = () => typeof document !== 'undefined' && Boolean(document.hidden);
    const leggi = () => {
      if (inVolo) return;
      inVolo = true; ultima = performance.now();
      Promise.resolve().then(() => Bellomberg.decisions('PENDING', -1))
        .then(r => Array.isArray(r?.decisions) ? { stato: 'ok', n: r.decisions.length } as const : { stato: 'ignota' } as const,
          () => ({ stato: 'ignota' }) as const)
        .then(esito => { if (vivo) { setLettura(esito); segnala(esito); } })
        .finally(() => { inVolo = false; });
    };
    // ritorno in primo piano: rilegge solo se visibile e se l'ultima lettura non è di un attimo fa
    // la pausa è una durata: orologio monotonico, un salto dell'ora di sistema non la allunga né la azzera
    const alRitorno = () => { if (!nascosta() && performance.now() - ultima >= PAUSA_MIN_MS) leggi(); };
    const periodica = () => { if (!nascosta()) leggi(); };
    leggi();
    const id = setInterval(periodica, OGNI_MS);
    const finestra = typeof window !== 'undefined' && typeof window.addEventListener === 'function' ? window : null;
    const doc = typeof document !== 'undefined' && typeof document.addEventListener === 'function' ? document : null;
    finestra?.addEventListener('focus', alRitorno);
    doc?.addEventListener('visibilitychange', alRitorno);
    return () => {
      vivo = false; clearInterval(id);
      finestra?.removeEventListener('focus', alRitorno);
      doc?.removeEventListener('visibilitychange', alRitorno);
    };
  }, [percorso]);
  if (lettura.stato === 'ignota') {
    return <>
      <b className="bb-nav-count is-unknown" data-decisions-badge="nd" title={ignota} aria-hidden="true">{nd}</b>
      <small className="sr-only">{`, ${ignota}`}</small>
    </>;
  }
  return lettura.stato === 'ok' && lettura.n > 0
    ? <>
      <b className="bb-nav-count" data-decisions-badge={lettura.n} aria-hidden="true">{lettura.n}</b>
      <small className="sr-only">{`, ${etichetta(lettura.n)}`}</small>
    </> : null;
}
