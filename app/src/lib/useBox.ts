import { useLayoutEffect, useRef, useState } from 'react';

/**
 * Misura un box in PIXEL REALI (ResizeObserver) per gli SVG degli strumenti.
 *
 * Regola PM 26/07 (lezione F5): l'SVG disegna 1:1 sul box misurato, quindi
 * niente `preserveAspectRatio="none"` e nessuna scritta stirata. Il box va
 * misurato FUORI dal flusso (`position:absolute;inset:0` sul figlio svg),
 * altrimenti il ResizeObserver va in retroazione e la pagina cresce da sola.
 *
 * Estratto da MonteCarloPage (F5) il 26/07 per riuso in F3 — copia unica,
 * nella scia del debito B-UI15 (formattatori duplicati).
 */
export function useBox<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [box, setBox] = useState({ w: 0, h: 0 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setBox({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, box] as const;
}
