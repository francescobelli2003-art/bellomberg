import { useSearchParams } from 'react-router-dom';

/* ============================================================================
   Sotto-pagine degli Strumenti del Vol Deck (10/10/2026, Opus 5.5 — richiesta PM «suddividi in sotto
   pagine, fai una cosa pulita»). Una domanda per sotto-pagina, una sola in vista; lo stato vive nell'URL
   (`#/vol?vista=…`): il tasto indietro torna alla sotto-pagina di prima e la si puo' linkare.
   ========================================================================== */
export const SUBPAGES = ['superficie', 'griglia', 'skew', 'termine', 'coerenza', 'variazioni', 'contesto', 'realizzata'] as const;
export type Subpage = typeof SUBPAGES[number];
export const DEFAULT_SUBPAGE: Subpage = 'superficie';
export const VIEW_PARAM = 'vista';

/** Valore dell'URL → sotto-pagina; qualunque altro valore (o nessuno) = la Superficie. */
export function parseSubpage(value: string | null | undefined): Subpage {
  return (SUBPAGES as readonly string[]).includes(value || '') ? value as Subpage : DEFAULT_SUBPAGE;
}

/** Nuovi parametri con la sotto-pagina scelta (gli altri parametri restano). */
export function withSubpage(params: URLSearchParams, page: Subpage): URLSearchParams {
  const next = new URLSearchParams(params);
  next.set(VIEW_PARAM, page);
  return next;
}

/** Area di lavoro (Acquisizione · Strumenti · Chain · Laboratorio) nell'URL (`area=…`), cosi' Indietro dal
 *  Laboratorio torna agli Strumenti e non cambia una sotto-pagina che non si vede. */
export const AREAS = ['acquisition', 'tools', 'chain', 'laboratory'] as const;
export type Area = typeof AREAS[number];
export const AREA_PARAM = 'area';
export function parseArea(value: string | null | undefined): Area {
  return (AREAS as readonly string[]).includes(value || '') ? value as Area : 'acquisition';
}
export function useWorkspace(): [Area, (area: Area) => void] {
  const [params, setParams] = useSearchParams();
  const area = parseArea(params.get(AREA_PARAM));
  const go = (next: Area) => { if (next !== area) setParams(p => { const n = new URLSearchParams(p); n.set(AREA_PARAM, next); return n; }); };
  return [area, go];
}

/** Sotto-pagina dall'URL; cambiarla aggiunge una voce alla cronologia (indietro = sotto-pagina di prima). */
export function useSubpage(): [Subpage, (page: Subpage) => void] {
  const [params, setParams] = useSearchParams();
  const page = parseSubpage(params.get(VIEW_PARAM));
  const go = (next: Subpage) => { if (next !== page) setParams(p => withSubpage(p, next)); };
  return [page, go];
}
