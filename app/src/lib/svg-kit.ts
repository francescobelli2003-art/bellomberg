import * as React from 'react';

/* ============================================================
   SVG KIT — le primitive degli strumenti-firma (Opus 5, 26/07)

   Estratte da `components/Quadrante.tsx` (F4) all'apertura dell'audit
   frontend: stavano dentro lo strumento di UNA pagina, e ogni pagina che
   ne avesse avuto bisogno se le sarebbe ricopiate — che e' esattamente
   il debito B-UI15 (i formattatori duplicati) di nuovo, in un altro
   vestito. Qui non c'e' niente di F4: sono geometria e misura del testo.

   DISCIPLINA (lezioni gia' pagate, vedi lib/useBox.ts e le pagine v3):
   - l'SVG disegna 1:1 sul box misurato in pixel VERI (`useBox`): mai
     `preserveAspectRatio="none"` con del testo dentro, o le scritte si
     stirano (difetto che fece bocciare il primo giro di F5);
   - la scatola di un'etichetta si CALCOLA sul testo che contiene, prima
     di disegnarla: il letter-spacing compreso. E' cosi' che si ottiene
     zero testo fuori dalla propria piastra — per costruzione, non a
     occhio (il collaudo `qa_app.py` misura proprio questo).
   ============================================================ */

export const TAU = Math.PI * 2;

/** L'unica famiglia monospaziata dell'app: se cambia, cambia anche
 *  l'avanzamento assunto da `textWidth` qui sotto. Non e' decorazione. */
export const MONO = "'JetBrains Mono',monospace";

/* ── geometria polare ───────────────────────────────────────────────────
   Convenzione: t=0 a MEZZOGIORNO, senso ORARIO, `scale` = valore di t che
   compie un giro intero. Vale per un quadrante orario come per qualunque
   grandezza ciclica. */

export const angleAt = (t: number, scale: number) => (t / scale) * TAU;

export const pointAt = (cx: number, cy: number, r: number, t: number, scale: number): [number, number] =>
  [cx + Math.sin(angleAt(t, scale)) * r, cy - Math.cos(angleAt(t, scale)) * r];

export function arcPath(cx: number, cy: number, r: number, t0: number, t1: number, scale: number) {
  const [x0, y0] = pointAt(cx, cy, r, t0, scale);
  const [x1, y1] = pointAt(cx, cy, r, t1, scale);
  const large = (t1 - t0) / scale > 0.5 ? 1 : 0;
  return `M${x0.toFixed(1)} ${y0.toFixed(1)} A${r.toFixed(1)} ${r.toFixed(1)} 0 ${large} 1 ${x1.toFixed(1)} ${y1.toFixed(1)}`;
}

/* ── misura del testo e piastre ─────────────────────────────────────── */

/** JetBrains Mono avanza 0,6 em esatti. Il letter-spacing si somma DOPO ogni
   carattere: senza contarlo la piastra risulta piu' stretta dell'inchiostro
   (misurato su F4: fino a +8px fuori dal bordo). Meglio mezzo pixel di aria. */
export const textWidth = (s: string, size: number, ls = 0.4) => s.length * (size * 0.6 + ls);

export interface LabLine { t: string; col: string; size?: number; ls?: number; op?: number }

/** la scatola di un blocco di etichetta, calcolata sul testo che contiene.
 *  `lh` e' l'interlinea: attenzione, non e' il corpo. Su F4 il collaudo trovo'
 *  il 16% di sovrapposizione fra due righe DENTRO la loro stessa piastra
 *  perche' l'interlinea (9,5) era piu' stretta dell'ingombro d'inchiostro di
 *  una riga da 9px — ascendente piu' discendente. */
export function plateBox(lines: LabLine[], lh = 11.5) {
  const w = Math.max(...lines.map(l => textWidth(l.t, l.size ?? 9, l.ls ?? 0.4)));
  return { w, h: lines.length * lh };
}

/** risolve le collisioni verticali fra etichette dentro la fascia min..max.
 *  Due passate: la prima spinge verso il basso a partire da `min`, la seconda
 *  ricompatta verso l'alto da `max`, cosi' il blocco resta centrato sulla
 *  fascia invece di accumularsi tutto in cima quando lo spazio non basta. */
export function layoutLabels<T extends { y: number; h: number }>(items: T[], min: number, max: number, gap = 5): (T & { ly: number })[] {
  const a = items.map((it, i) => ({ ...it, i, ly: it.y })).sort((p, q) => p.y - q.y);
  let cur = min;
  for (const it of a) { const top = Math.max(it.ly - it.h / 2, cur); it.ly = top + it.h / 2; cur = top + it.h + gap; }
  let bot = max;
  for (let k = a.length - 1; k >= 0; k--) { const b = Math.min(a[k].ly + a[k].h / 2, bot); a[k].ly = b - a[k].h / 2; bot = b - a[k].h - gap; }
  const out = new Array(items.length) as (T & { ly: number })[];
  for (const it of a) out[it.i] = it as unknown as T & { ly: number };
  return out;
}

/* ------------------------------------------------------------------
   IL TESTO DENTRO UN SVG SCALATO (Opus 5, 27/07)

   Un <svg> con `viewBox` fisso reso a larghezza variabile scala ANCHE il
   testo, e il testo cosi' esce dalla scala tipografica della pagina. Non e'
   un'ipotesi: misurato sull'app viva, alle due inquadrature del 49" del PM.

     F8  radar   viewBox 300  reso 244px  ->  un 7px arriva all'occhio  5,69px
                              reso 200px  (Windows 150%)                4,67px
     F12 smile   viewBox 1400 reso 1302px ->  un 10,5px arriva          9,77px
                              reso 3009px (Windows 150%)               22,57px

   Cioe' lo stesso difetto va nelle DUE direzioni: il radar sparisce, il
   grafico di F12 diventa il doppio di tutto il resto della pagina. (L'audit
   aveva letto solo il primo verso — «ogni pixel di colonna in meno
   rimpicciolisce il testo» — ed e' vero a meta'.)

   `getComputedStyle().fontSize` non se ne accorge: restituisce il corpo
   DICHIARATO. Per questo il controllo "minuteria sotto 7px" del cancello non
   li vedeva: guardava il numero scritto, non quello che arriva all'occhio.

   Rimedio: si moltiplica il corpo per questo fattore, e il testo arriva
   all'occhio della misura dichiarata qualunque sia la larghezza. Il disegno
   non si tocca.
   ------------------------------------------------------------------ */
export function useScalaTesto(ref: React.RefObject<SVGSVGElement | null>, viewBoxW: number) {
  const [k, setK] = React.useState(1);
  React.useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const misura = () => {
      const w = el.getBoundingClientRect().width;
      setK(w > 0 && viewBoxW > 0 ? viewBoxW / w : 1);
    };
    misura();
    const ro = new ResizeObserver(misura);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref, viewBoxW]);
  return k;
}
