/* Barra in alto della shell (05/10/2026): la pagina può portarci la propria riga titolo
   (contesto e azioni) con <InBarra>. La shell resta l'unica a scrivere il titolo: l'h1 della
   pagina viaggia con la riga ma è nascosto dal CSS della shell.
   Senza shell (test SSR, pagina montata da sola) la riga resta dove la pagina la mette. */
import { createContext, useContext, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

const SlotBarra = createContext<HTMLElement | null>(null);
export const FornitoreBarra = SlotBarra.Provider;

/** `classi`: classi di stile per la riga nella barra. Mai la classe radice della pagina: test e
    codice cercano la radice con querySelector e troverebbero prima la barra. */
export function InBarra({ classi = '', children }: { classi?: string; children: ReactNode }) {
  const slot = useContext(SlotBarra);
  if (!slot) return <>{children}</>;
  return createPortal(<div className={('bb-barra-ospite ' + classi).trim()}>{children}</div>, slot);
}
