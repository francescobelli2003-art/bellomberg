/* ============================================================
   SIGILLI DEI DESK (Opus 5, 26/07)

   Estratto da `components/Quadrante.tsx` all'apertura dell'audit frontend:
   il sigillo non e' un dettaglio del quadrante di F4, e' l'IDENTITA' di un
   agente del comitato — e la stessa identita' compare (o dovrebbe comparire)
   in ogni pagina che parla di desk.

   PERCHE' ESISTE: nel payload `macro` (#ffc760) e `crypto` (#fbbf24) sono due
   ambre quasi identiche. Su un 49" a distanza di scrivania non si distinguono,
   e chi non vede bene i colori non li distingue affatto. Ogni desk ha quindi
   anche una FORMA: il colore diventa ridondante, non portante.

   I tracciati sono centrati sull'origine e disegnati su una scatola nominale
   di 13px: il componente li scala e compensa lo spessore del tratto, cosi' un
   sigillo da 9px e uno da 13px hanno la stessa mano.
   ============================================================ */

const SIGIL: Record<string, string> = {
  fundamentals: 'M-5-5h10v10h-10z M-2-2h4v4h-4z',
  macro: 'M0-6 6 0 0 6 -6 0z M0-2.6 2.6 0 0 2.6 -2.6 0z',
  crypto: 'M0-6 5.2-3v6L0 6 -5.2 3v-6z',
  eventdesk: 'M0-6.2V6.2 M-5.4-3.1 5.4 3.1 M-5.4 3.1 5.4-3.1',
  quant: 'M-5.4-5.4h10.8v10.8h-10.8z M0-5.4V5.4',
  options: 'M0-6A6 6 0 010 6z M0-6A6 6 0 000 6',
  capo: 'M0-6.4 5.6 5.2H-5.6z M0-1.6 2.4 3.1h-4.8z',
};

/** ripiego DICHIARATO: un id senza sigillo proprio (le fasi della pipeline,
 *  un desk nuovo) prende il rombo neutro. Non e' un default zitto — e'
 *  visibilmente "non uno dei sette". */
const STAGE_SIGIL = 'M-3.4 0 0-3.4 3.4 0 0 3.4z';

/** true se l'id ha un sigillo PROPRIO: per una legenda che deve dire quali
 *  simboli sta spiegando e quali no. */
export const hasSigil = (id: string) => id in SIGIL;

export function Sigil({ id, color, size = 13, x = 0, y = 0 }: {
  id: string; color: string; size?: number; x?: number; y?: number;
}) {
  const d = SIGIL[id] || STAGE_SIGIL;
  const k = size / 13;
  return (
    <g transform={`translate(${x},${y}) scale(${k.toFixed(3)})`} fill="none" stroke={color}
       strokeWidth={(1.35 / k).toFixed(2)} strokeLinejoin="round">
      {d.split(' M').map((p, i) => <path key={i} d={i ? 'M' + p : p} />)}
    </g>
  );
}

export default Sigil;
