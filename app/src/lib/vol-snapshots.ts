/* ============================================================================
   Istantanee salvate della superficie (Vol Deck «quant», 10/10/2026 — Opus 5.5).
   CACHE LOCALE dichiarata del confronto «ΔIV vs <data>»: la pagina conserva qui, per sottostante, le
   superfici che ha mostrato (solo i campi che servono a ricostruire il modello). Nessun calcolo: il
   confronto lo fa `surfaceDiff` di lib/vol-quant. Regole:
   - schema versionato DENTRO i dati ({ v: 2, tickers }): un archivio d'altra forma o una voce non valida
     si scarta e si conta (`discarded`), mai un crash della pagina (review 10/10);
   - il precedente e' l'istantanea salvata PIU' RECENTE con orario STRETTAMENTE anteriore a quella mostrata
     e di un ALTRO download (lo stesso download riletto non e' un «precedente»);
   - stesso contenuto (impronta uguale) = «istantanea identica», non un ΔIV tutto a zero;
   - limite globale MAX_TOTAL: si eliminano prima i sottostanti visti da piu' tempo; `clearSnapshots`
     svuota l'archivio (privacy);
   - storage assente o pieno: si lavora in memoria e lo si dichiara (`persisted:false`).
   ========================================================================== */
export interface SavedSnapshot {
  ticker: string; at: string; downloadId: string | null; kind: string | null;
  /** stessi nomi del payload del builder: la copia si rilegge con le funzioni di lib/vol-quant */
  snapshot_at: string; spot_est: number | null; moneyness_grid: number[];
  slices: { expiry: string; days: number; t_years: number | null; atm_iv: number | null; iv_grid: (number | null)[] }[];
  /** forward implicito per scadenza, se la pagina l'ha calcolato */
  forwards?: Record<string, number>;
  /** stessi campi di qualifica del payload, letti da moneynessSurfaceFromBuilder */
  spot_qualified?: boolean | null; spot_fallback?: boolean | null; iv_grid_qualified?: boolean | null;
  /** impronta del contenuto (griglia, spot, IV): uguale = istantanea identica */
  hash?: string;
}

export const SNAPSHOT_KEY = 'bellomberg.vol.snapshots.v2';
export const SCHEMA_VERSION = 2;
export const MAX_PER_TICKER = 8;
export const MAX_TOTAL = 40;
type Store = Record<string, SavedSnapshot[]>;
const memory: { store: Store | null } = { store: null };
const status = { discarded: 0, persisted: true };

const finiteNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
/** Istante di un testo ISO: senza fuso esplicito NON si interpreta (un orario locale non e' un istante). */
const instant = (v: unknown): number | null => {
  if (typeof v !== 'string' || !/(Z|[+-]\d{2}:?\d{2})$/.test(v.trim())) return null;
  const t = Date.parse(v);
  return Number.isNaN(t) ? null : t;
};
const atOf = (surface: any): string | null => {
  const v = surface?.snapshot_at ?? surface?._timestamp;
  return instant(v) != null ? v : null;
};

/** Impronta FNV-1a del contenuto confrontabile (griglia, spot, scadenze, IV di griglia). */
export function contentHash(snap: Pick<SavedSnapshot, 'moneyness_grid' | 'spot_est' | 'slices'>): string {
  const text = JSON.stringify([snap.moneyness_grid, snap.spot_est, snap.slices.map(s => [s.expiry, s.iv_grid])]);
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
  return h.toString(16).padStart(8, '0');
}

/** Una voce letta dall'archivio e' valida solo con tutti i campi del tipo, coi tipi giusti. */
function validSnapshot(x: any): x is SavedSnapshot {
  return !!x && typeof x === 'object' && typeof x.ticker === 'string' && x.ticker !== '' && instant(x.at) != null
    && (x.downloadId === null || typeof x.downloadId === 'string') && typeof x.snapshot_at === 'string'
    && (x.spot_est === null || finiteNum(x.spot_est)) && Array.isArray(x.moneyness_grid) && x.moneyness_grid.every(finiteNum)
    && Array.isArray(x.slices) && x.slices.every((s: any) => s && typeof s.expiry === 'string' && finiteNum(s.days)
      && Array.isArray(s.iv_grid) && s.iv_grid.every((v: unknown) => v === null || finiteNum(v)))
    && (x.forwards === undefined || (x.forwards && typeof x.forwards === 'object' && Object.values(x.forwards).every(finiteNum)));
}

/** Versione compatta e serializzabile di un payload di superficie; null se manca ticker o orario con fuso. */
export function compactSnapshot(surface: any, ticker: string, forwards?: Record<string, number | null> | null): SavedSnapshot | null {
  const at = atOf(surface);
  const t = (typeof surface?.ticker === 'string' && surface.ticker) || ticker;
  if (!at || !t || !Array.isArray(surface?.moneyness_grid) || !Array.isArray(surface?.slices)) return null;
  const snap: SavedSnapshot = { ticker: t, at, downloadId: typeof surface.download_id === 'string' ? surface.download_id : null,
    kind: typeof surface.snapshot_kind === 'string' ? surface.snapshot_kind : null, snapshot_at: at,
    spot_est: finiteNum(surface.spot_est) ? surface.spot_est : null,
    moneyness_grid: surface.moneyness_grid.filter(finiteNum),
    slices: surface.slices.filter((s: any) => s && typeof s.expiry === 'string' && finiteNum(s.days)).map((s: any) => ({
      expiry: s.expiry, days: s.days, t_years: finiteNum(s.t_years) ? s.t_years : null, atm_iv: finiteNum(s.atm_iv) ? s.atm_iv : null,
      iv_grid: Array.isArray(s.iv_grid) ? s.iv_grid.map((v: unknown) => finiteNum(v) ? v : null) : [] })),
    ...(typeof surface.spot_qualified === 'boolean' ? { spot_qualified: surface.spot_qualified } : {}),
    ...(typeof surface.spot_fallback === 'boolean' ? { spot_fallback: surface.spot_fallback } : {}),
    ...(typeof surface.iv_grid_qualified === 'boolean' ? { iv_grid_qualified: surface.iv_grid_qualified } : {}),
    ...(forwards ? { forwards: Object.fromEntries(Object.entries(forwards).filter((e): e is [string, number] => finiteNum(e[1]) && e[1] > 0)) } : {}) };
  snap.hash = contentHash(snap);
  return snap;
}

function read(): Store {
  if (memory.store) return memory.store;
  status.discarded = 0;
  try {
    const raw = typeof localStorage !== 'undefined' ? localStorage.getItem(SNAPSHOT_KEY) : null;
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || parsed.v !== SCHEMA_VERSION || !parsed.tickers || typeof parsed.tickers !== 'object') {
      status.discarded = 1; return {};
    }
    const store: Store = {};
    for (const [t, list] of Object.entries(parsed.tickers)) {
      if (!Array.isArray(list)) { status.discarded++; continue; }
      const ok = list.filter(validSnapshot);
      status.discarded += list.length - ok.length;
      if (ok.length) store[t] = ok;
    }
    return store;
  } catch { status.discarded = Math.max(1, status.discarded); return {}; }
}
function write(store: Store): boolean {
  try { localStorage.setItem(SNAPSHOT_KEY, JSON.stringify({ v: SCHEMA_VERSION, tickers: store })); memory.store = null; status.persisted = true; return true; }
  catch { memory.store = store; status.persisted = false; return false; }
}
/** Limite globale: si eliminano prima i sottostanti la cui istantanea piu' recente e' la piu' vecchia. */
function trim(store: Store): number {
  let evicted = 0;
  const count = () => Object.values(store).reduce((a, l) => a + l.length, 0);
  while (count() > MAX_TOTAL) {
    const oldest = Object.entries(store).sort((a, b) => (instant(a[1][a[1].length - 1]?.at) ?? 0) - (instant(b[1][b[1].length - 1]?.at) ?? 0))[0];
    if (!oldest) break;
    evicted += oldest[1].length; delete store[oldest[0]];
  }
  return evicted;
}

/** Salva l'istantanea mostrata (una sola per orario+download). Ritorna se e' rimasta su disco. */
export function saveSnapshot(surface: any, ticker: string, forwards?: Record<string, number | null> | null): { saved: boolean; persisted: boolean } {
  const snap = compactSnapshot(surface, ticker, forwards);
  if (!snap) return { saved: false, persisted: false };
  const store = read();
  const same = (s: SavedSnapshot) => s.at === snap.at && s.downloadId === snap.downloadId;
  // un nuovo salvataggio senza forward non cancella quelli gia' salvati per la stessa istantanea
  const old = (store[snap.ticker] || []).find(same);
  if (!snap.forwards && old?.forwards) snap.forwards = old.forwards;
  const list = (store[snap.ticker] || []).filter(s => !same(s));
  list.push(snap);
  list.sort((a, b) => (instant(a.at) as number) - (instant(b.at) as number));
  store[snap.ticker] = list.slice(-MAX_PER_TICKER);
  trim(store);
  return { saved: true, persisted: write(store) };
}

/** L'istantanea salvata piu' recente STRETTAMENTE anteriore a `currentAt`, di un ALTRO download, per lo stesso sottostante. */
export function previousSnapshot(ticker: string, currentAt: string | null, currentDownloadId: string | null = null): SavedSnapshot | null {
  const now = instant(currentAt);
  if (!ticker || now == null) return null;
  const list = (read()[ticker] || []).filter(s => (instant(s.at) as number) < now && !(currentDownloadId && s.downloadId === currentDownloadId));
  return list.length ? list.reduce((a, b) => ((instant(b.at) as number) > (instant(a.at) as number) ? b : a)) : null;
}

/** Stesso contenuto (impronta) fra l'istantanea mostrata e una salvata. */
export function identicalContent(surface: any, saved: SavedSnapshot | null): boolean {
  if (!saved) return false;
  const cur = compactSnapshot(surface, saved.ticker);
  return !!cur && (saved.hash ?? contentHash(saved)) === cur.hash;
}

/** Svuota l'archivio locale (pulsante «Cancella archivio ΔIV»). */
export function clearSnapshots(): boolean {
  memory.store = null; status.discarded = 0;
  try { localStorage.removeItem(SNAPSHOT_KEY); return true; } catch { return false; }
}

/** Stato dell'archivio da dichiarare in pagina. */
export function snapshotStoreStatus(): { persisted: boolean; discarded: number; total: number; max: number } {
  const store = read();
  return { persisted: status.persisted && !memory.store, discarded: status.discarded, total: Object.values(store).reduce((a, l) => a + l.length, 0), max: MAX_TOTAL };
}

export const snapshotTime = atOf;
/** Solo per i test: azzera la copia in memoria. */
export function __resetSnapshotMemory() { memory.store = null; status.discarded = 0; status.persisted = true; }
