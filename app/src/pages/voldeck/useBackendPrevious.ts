import { useEffect, useState } from 'react';
import { volRequest } from '@/lib/vol-deck';
import { compactSnapshot, snapshotTime, type SavedSnapshot } from '@/lib/vol-snapshots';
import { contentHashOf, listBackendSnapshots, loadBackendSnapshot, previousBackendSnapshot, surfaceOfRecord } from '@/lib/vol-snapshots-api';

/* ============================================================================
   Precedente dall'ARCHIVIO DEL BACKEND (10/10/2026, Opus 5.5 — RICHIESTE_UI.md R2): fonte preferita del
   confronto «ΔIV vs <data>». Elenco del sottostante → il piu' recente STRETTAMENTE anteriore all'istantanea
   mostrata (mai la stessa) → istantanea completa → stessa forma compatta della cache locale.
   Stati: 'ok' (snapshot null = l'archivio non ha un precedente: n.d., la cache locale NON lo sostituisce),
   'error' (archivio non raggiungibile: la pagina usa la cache locale e lo DICHIARA), 'loading', 'off'.
   ========================================================================== */
export type ArchiveState =
  | { state: 'off' } | { state: 'loading' }
  | { state: 'ok'; snapshot: SavedSnapshot | null; id: string | null; identicalAt?: string | null; retention?: { days: number | null; max_per_ticker: number | null } | null }
  | { state: 'error'; error: string };

export function useBackendPrevious(surface: any, ticker: string, enabled: boolean): ArchiveState & { snapshot?: SavedSnapshot | null; error?: string; identicalAt?: string | null; retention?: { days: number | null; max_per_ticker: number | null } | null } {
  const [st, setSt] = useState<ArchiveState>({ state: 'off' });
  const at = surface ? snapshotTime(surface) : null;
  const t = (typeof surface?.ticker === 'string' && surface.ticker) || ticker;
  // id dell'ARCHIVIO dell'istantanea mostrata (job.archive.id; per un duplicato e' l'id della vecchia): mai l'uuid del download
  const currentId = typeof surface?.archive?.id === 'string' ? surface.archive.id : typeof surface?.archive_id === 'string' ? surface.archive_id : null;
  useEffect(() => {
    if (!enabled || !surface || !t || !at) { setSt({ state: 'off' }); return; }
    const controller = new AbortController();
    setSt({ state: 'loading' });
    (async () => {
      const list = await listBackendSnapshots(t, volRequest, controller.signal);
      const meta = previousBackendSnapshot(list.snapshots, at, currentId);
      // stesso contenuto gia' archiviato prima (es. weekend, download deduplicato): «istantanea identica del <data>»,
      // mai un ΔIV tutto a zero ne' un confronto saltato in silenzio verso un'istantanea piu' vecchia
      const hash = contentHashOf(currentId), now = Date.parse(at);
      const same = hash ? list.snapshots.filter(x => contentHashOf(x.id) === hash && Date.parse(x.snapshot_at) < now)
        .sort((x, y) => Date.parse(y.snapshot_at) - Date.parse(x.snapshot_at))[0] : undefined;
      const retention = list.retention ? { days: list.retention.days, max_per_ticker: list.retention.max_per_ticker } : null;
      if (same && (!meta || Date.parse(same.snapshot_at) > Date.parse(meta.snapshot_at))) return { state: 'ok', snapshot: null, id: same.id, identicalAt: same.snapshot_at, retention } as const;
      if (!meta) return { state: 'ok', snapshot: null, id: null, retention } as const;
      const record = await loadBackendSnapshot(meta.id, volRequest, controller.signal);
      const snap = compactSnapshot(surfaceOfRecord(record), t);
      if (snap && record.content_hash) snap.hash = snap.hash ?? record.content_hash;
      return { state: 'ok', snapshot: snap, id: meta.id, retention } as const;
    })().then(next => { if (!controller.signal.aborted) setSt(next); })
      .catch(e => { if (!controller.signal.aborted) setSt({ state: 'error', error: e instanceof Error ? e.message : String(e) }); });
    return () => controller.abort();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, surface, t, at, currentId]);
  return st as ArchiveState & { snapshot?: SavedSnapshot | null; error?: string; identicalAt?: string | null; retention?: { days: number | null; max_per_ticker: number | null } | null };
}
