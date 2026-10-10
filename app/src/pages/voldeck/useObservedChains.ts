import { useEffect, useRef, useState } from 'react';
import { fetchWholeChain, volRequest, type ChainPage, type WholeChain } from '@/lib/vol-deck';

/* ============================================================================
   Quote MISURATE del Vol Deck (v2, 10/10/2026 — Opus 5.5): la chain gia' scaricata nel job, letta
   dalla memoria del backend (nessuna richiesta al fornitore), pagina dopo pagina fino in fondo.
   MEDIA-5 audit: la v1 calcolava «da caricare» da una fotografia dello stato e, se le scadenze
   cambiavano mentre una richiesta era in volo, la cancellava e ne cancellava lo stato «loading»
   senza rilanciarla: la scadenza restava «in caricamento» per sempre. Qui una richiesta in volo vive
   finche' non risponde (il registro `inflight` e' la verita'), si annulla solo se cambia il job o la
   pagina si smonta, e una chiave «loading» senza richiesta viva viene rilanciata.
   ========================================================================== */
export type ChainState = { state: 'loading' } | ({ state: 'ok' } & WholeChain) | { state: 'error'; error: string };

export function useObservedChains(downloadId: string | null, expiries: string[], enabled: boolean) {
  const [chains, setChains] = useState<Record<string, ChainState>>({});
  const inflight = useRef(new Map<string, AbortController>());
  const key = expiries.join(',');

  // nuovo job (o nessun job): tutto cio' che era in volo appartiene a un'altra istantanea
  useEffect(() => {
    setChains({});
    const running = inflight.current;
    return () => { running.forEach(c => c.abort()); running.clear(); };
  }, [downloadId]);

  useEffect(() => {
    if (!downloadId || !enabled) return;
    for (const expiry of key ? key.split(',') : []) {
      const id = downloadId + '|' + expiry;
      const current = chains[id];
      if (inflight.current.has(id) || (current && current.state !== 'loading')) continue;
      const controller = new AbortController();
      inflight.current.set(id, controller);
      setChains(c => ({ ...c, [id]: { state: 'loading' } }));
      const settle = (next: ChainState) => {
        if (controller.signal.aborted || inflight.current.get(id) !== controller) return;
        inflight.current.delete(id);
        setChains(c => ({ ...c, [id]: next }));
      };
      fetchWholeChain(path => volRequest<ChainPage>(path, undefined, controller.signal), downloadId, expiry)
        .then(whole => settle({ state: 'ok', ...whole }))
        .catch(err => settle({ state: 'error', error: err instanceof Error ? err.message : String(err) }));
    }
  }, [downloadId, enabled, key, chains]);

  /** Azzera le quote (es. il contesto e' arrivato da un'istantanea diversa da quella del job). */
  const reset = () => { inflight.current.forEach(c => c.abort()); inflight.current.clear(); setChains({}); };
  const of = (expiry: string | null): ChainState | undefined => (downloadId && expiry ? chains[downloadId + '|' + expiry] : undefined);
  return { chains, of, reset };
}
