/* ============================================================================
   Istantanee della superficie conservate dal BACKEND (Vol Deck, 10/10/2026 — Opus 5.5).
   Ogni download completato lascia su file un'istantanea (`GET /options/snapshots?ticker=`,
   `GET /options/snapshots/{id}`): il confronto «ΔIV rispetto al download precedente» funziona
   da qualunque computer e dopo i riavvii. Questo modulo e' solo il client: nessun calcolo di
   volatilita' (quello resta in lib/vol-quant) e nessun import, cosi' la richiesta HTTP la passa
   la pagina (`volRequest` di lib/vol-deck: stessa sessione e stessa lingua degli altri endpoint
   opzioni) e i test girano senza rete.
   Regole:
   - il precedente e' l'istantanea con `snapshot_at` STRETTAMENTE anteriore a quella mostrata, e
     mai la stessa istantanea (id uguale); senza orario leggibile non si confronta (mai un'ora inventata);
   - una voce malformata non sparisce: finisce in `errors` accanto a quelle dichiarate dal backend;
   - nessun ripiego silenzioso: se il backend non risponde l'errore sale al chiamante, che decide
     se usare la cache locale DICHIARANDOLO (v. RICHIESTE_UI.md).
   ========================================================================== */

export type VolRequest = <T>(path: string, body?: unknown, signal?: AbortSignal) => Promise<T>;

/** Una riga dell'elenco: stessi nomi del backend (options_snapshots._meta). */
export interface BackendSnapshotMeta {
  id: string; ticker: string; snapshot_at: string; saved_at: string | null;
  spot: number | null; spot_source: string | null; spot_qualified: boolean | null; iv_grid_qualified: boolean | null;
  expiries: string[]; surface_expiries: string[]; n_expiries: number; n_contracts: number; n_slices: number;
  surface_error: string | null; data_delay: string | null; download_complete: boolean | null;
  source: string | null; size_bytes: number; index: string | null;
}

export interface BackendSnapshotList {
  ticker: string | null; snapshots: BackendSnapshotMeta[];
  /** voci illeggibili: dichiarate dal backend + scartate qui perche' malformate */
  errors: { id: string | null; error: string }[];
  retention: { days: number | null; max_per_ticker: number | null; error: string | null } | null;
}

export interface BackendChainContract {
  contract: string | null; expiry: string | null; type: string | null; strike: number | null;
  bid: number | null; ask: number | null; iv: number | null; oi: number | null; volume: number | null;
  adjusted: boolean | null; multiplier: number | null; exercise_style: string | null;
  quote_timestamp: string | null; quote_timeframe: string | null;
}

export interface BackendSnapshotRecord {
  id: string; ticker: string; snapshot_at: string; saved_at: string | null; content_hash: string | null;
  provenance: Record<string, unknown>; summary: Record<string, unknown>;
  chain_status: Record<string, { complete: boolean; error: string | null }>;
  /** payload del builder (slices con iv_grid e t_years, moneyness_grid, spot_est, flag di qualita') */
  surface: any;
  chains: Record<string, BackendChainContract[]>;
  size_bytes: number | null;
}

const ID_RE = /^[A-Z0-9][A-Z0-9.\-^]{0,24}~\d{8}T\d{6}Z_[0-9a-f]{16}$/;
const finiteNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const str = (v: unknown): string | null => (typeof v === 'string' ? v : null);
const bool = (v: unknown): boolean | null => (typeof v === 'boolean' ? v : null);
const strList = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []);
const timeOf = (v: unknown): number | null => {
  if (typeof v !== 'string' || !v.trim()) return null;
  const t = Date.parse(v);
  return Number.isNaN(t) ? null : t;
};

export const isSnapshotId = (id: unknown): id is string => typeof id === 'string' && ID_RE.test(id);

/** Una riga del backend, o null se manca cio' che serve a confrontare (id valido, ticker, orario). */
export function parseSnapshotMeta(raw: any): BackendSnapshotMeta | null {
  if (!raw || typeof raw !== 'object' || !isSnapshotId(raw.id) || typeof raw.ticker !== 'string'
    || timeOf(raw.snapshot_at) === null || raw.id.split('~')[0] !== raw.ticker) return null;
  return {
    id: raw.id, ticker: raw.ticker, snapshot_at: raw.snapshot_at, saved_at: str(raw.saved_at),
    spot: finiteNum(raw.spot) ? raw.spot : null, spot_source: str(raw.spot_source),
    spot_qualified: bool(raw.spot_qualified), iv_grid_qualified: bool(raw.iv_grid_qualified),
    expiries: strList(raw.expiries), surface_expiries: strList(raw.surface_expiries),
    n_expiries: finiteNum(raw.n_expiries) ? raw.n_expiries : strList(raw.expiries).length,
    n_contracts: finiteNum(raw.n_contracts) ? raw.n_contracts : 0, n_slices: finiteNum(raw.n_slices) ? raw.n_slices : 0,
    surface_error: str(raw.surface_error), data_delay: str(raw.data_delay), download_complete: bool(raw.download_complete),
    source: str(raw.source), size_bytes: finiteNum(raw.size_bytes) ? raw.size_bytes : 0, index: str(raw.index),
  };
}

export function parseSnapshotList(payload: any): BackendSnapshotList {
  if (!payload || typeof payload !== 'object' || !Array.isArray(payload.snapshots)) {
    throw new Error('Unreadable snapshot list from the backend / Elenco istantanee illeggibile dal backend');
  }
  const errors: BackendSnapshotList['errors'] = Array.isArray(payload.errors)
    ? payload.errors.map((e: any) => ({ id: str(e?.id), error: str(e?.error) ?? 'unknown' })) : [];
  const snapshots: BackendSnapshotMeta[] = [];
  for (const raw of payload.snapshots) {
    const meta = parseSnapshotMeta(raw);
    if (meta) snapshots.push(meta);
    else errors.push({ id: str(raw?.id), error: 'malformed entry (client) / voce malformata (client)' });
  }
  snapshots.sort((a, b) => (timeOf(b.snapshot_at)! - timeOf(a.snapshot_at)!) || (a.id < b.id ? 1 : -1));
  const r = payload.retention;
  return {
    ticker: str(payload.ticker), snapshots, errors,
    retention: r && typeof r === 'object'
      ? { days: finiteNum(r.days) ? r.days : null, max_per_ticker: finiteNum(r.max_per_ticker) ? r.max_per_ticker : null, error: str(r.error) }
      : null,
  };
}

/** Elenco delle istantanee del sottostante (piu' recente prima). Gli errori HTTP salgono al chiamante. */
export async function listBackendSnapshots(ticker: string, request: VolRequest, signal?: AbortSignal): Promise<BackendSnapshotList> {
  const t = (ticker || '').trim().toUpperCase();
  if (!t) throw new Error('Ticker required / Ticker obbligatorio');
  const list = parseSnapshotList(await request<any>(`/options/snapshots?ticker=${encodeURIComponent(t)}`, undefined, signal));
  // difesa: una riga di un altro sottostante non entra mai nel confronto
  const foreign = list.snapshots.filter(s => s.ticker !== t);
  return foreign.length
    ? { ...list, snapshots: list.snapshots.filter(s => s.ticker === t),
        errors: [...list.errors, ...foreign.map(s => ({ id: s.id, error: 'other ticker (client) / altro sottostante (client)' }))] }
    : list;
}

/** Istantanea completa (superficie + chain minime + provenienza). */
export async function loadBackendSnapshot(id: string, request: VolRequest, signal?: AbortSignal): Promise<BackendSnapshotRecord> {
  if (!isSnapshotId(id)) throw new Error('Invalid snapshot id / Id istantanea non valido');
  const raw = await request<any>(`/options/snapshots/${encodeURIComponent(id)}`, undefined, signal);
  if (!raw || raw.id !== id || !raw.surface || typeof raw.surface !== 'object' || !Array.isArray(raw.surface.slices)
    || timeOf(raw.snapshot_at) === null) {
    throw new Error('Unreadable snapshot from the backend / Istantanea illeggibile dal backend');
  }
  return {
    id: raw.id, ticker: String(raw.ticker), snapshot_at: raw.snapshot_at, saved_at: str(raw.saved_at),
    content_hash: str(raw.content_hash), provenance: raw.provenance && typeof raw.provenance === 'object' ? raw.provenance : {},
    summary: raw.summary && typeof raw.summary === 'object' ? raw.summary : {},
    chain_status: raw.chain_status && typeof raw.chain_status === 'object' ? raw.chain_status : {},
    surface: raw.surface, chains: raw.chains && typeof raw.chains === 'object' ? raw.chains : {},
    size_bytes: finiteNum(raw.size_bytes) ? raw.size_bytes : null,
  };
}

/** Hash di contenuto di un id d'archivio (`TICKER~stamp_<hash16>`), o null. */
export const contentHashOf = (id: unknown): string | null => (isSnapshotId(id) ? id.slice(-16) : null);

/**
 * Il precedente: la piu' recente con orario STRETTAMENTE anteriore a `currentAt` e con CONTENUTO
 * diverso da quello mostrato. `currentId` e' `archive.id` dello stato/superficie del job
 * (`/options/download/{id}/status` o `/surface`), NON l'id del download: su un download
 * deduplicato (`archive.status: "duplicate"`) e' l'id dell'istantanea gia' archiviata con lo
 * stesso contenuto, che e' piu' vecchia di `snapshot_at` del job e non va confrontata con se'
 * stessa. Si esclude per hash di contenuto (suffisso dell'id), non solo per id.
 */
export function previousBackendSnapshot(list: BackendSnapshotMeta[], currentAt: string | null,
  currentId?: string | null): BackendSnapshotMeta | null {
  const now = timeOf(currentAt);
  if (now === null) return null;
  const sameContent = contentHashOf(currentId);
  let best: BackendSnapshotMeta | null = null;
  for (const s of list) {
    const t = timeOf(s.snapshot_at);
    // stesso id => stesso hash: basta il confronto per contenuto (un id non d'archivio non esclude nulla)
    if (t === null || t >= now || (sameContent !== null && contentHashOf(s.id) === sameContent)) continue;
    if (!best || t > timeOf(best.snapshot_at)!) best = s;
  }
  return best;
}

/** Il payload di superficie dell'istantanea, con l'orario dell'istantanea: va a compactSnapshot / lib/vol-quant. */
export function surfaceOfRecord(record: BackendSnapshotRecord): any {
  return { ...record.surface, snapshot_at: record.snapshot_at, snapshot_kind: 'archived_snapshot', archive_id: record.id };
}
