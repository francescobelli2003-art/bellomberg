import { API_BASE, getSessionToken } from './api';

export type JournalKind = 'thesis' | 'macro';
export type JournalStatus = 'active' | 'archived' | 'all';
export interface JournalDraft { kind: JournalKind; ticker: string | null; title: string; body: string }
export interface JournalEntry extends JournalDraft {
  id: number; origin: 'user'; created_at: string; updated_at: string;
  version: number; archived_at: string | null;
}
export interface JournalSummary extends Omit<JournalEntry, 'body'> { excerpt: string }
export interface JournalRevision extends JournalDraft {
  entry_id: number; version: number; origin: 'user'; created_at: string; saved_at: string;
  archived_at: string | null; action: 'create' | 'update' | 'archive' | 'restore';
}
export interface JournalPageResult<T> { items: T[]; total: number; limit: number; offset: number }
export class JournalError extends Error {
  constructor(message: string, public status: number, public code = '', public currentVersion?: number) { super(message); }
}

async function request<T>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<T> {
  const timeout = new AbortController();
  const abort = () => timeout.abort();
  if (signal?.aborted) abort();
  signal?.addEventListener('abort', abort, { once: true });
  const timer = window.setTimeout(abort, 30000);
  try {
    const token = getSessionToken();
    const response = await fetch(`${API_BASE}/journal${path}`, {
      method, headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
        ...(token ? { 'X-BB-Token': token } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body), signal: timeout.signal, cache: 'no-store',
    });
    const data = await response.json();
    if (!response.ok) {
      const detail = data?.detail;
      const message = response.status === 401 || response.status === 403
        ? 'Sessione scaduta. Conserva la bozza prima di accedere di nuovo.'
        : typeof detail === 'string' ? detail : detail?.message || `Diario non disponibile (HTTP ${response.status}).`;
      throw new JournalError(message, response.status, detail?.code, detail?.current_version);
    }
    return data as T;
  } catch (error) {
    if (error instanceof JournalError || signal?.aborted) throw error;
    if (timeout.signal.aborted) throw new JournalError('Richiesta scaduta. La bozza è conservata; verifica le note salvate prima di riprovare.', 0, 'timeout');
    throw new JournalError('Collegamento al Diario non riuscito. La bozza resta nell’editor.', 0, 'network');
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener('abort', abort);
  }
}

export const Journal = {
  list: (filters: { status: JournalStatus; kind?: JournalKind; query: string; offset: number }, signal?: AbortSignal) => {
    const params = new URLSearchParams({ status: filters.status, query: filters.query, offset: String(filters.offset), limit: '30' });
    if (filters.kind) params.set('kind', filters.kind);
    return request<JournalPageResult<JournalSummary>>(`?${params}`, 'GET', undefined, signal);
  },
  get: (id: number) => request<JournalEntry>(`/${id}`),
  create: (body: JournalDraft) => request<JournalEntry>('', 'POST', body),
  update: (id: number, expected_version: number, body: JournalDraft) => request<JournalEntry>(`/${id}`, 'PUT', { ...body, expected_version }),
  archive: (id: number, expected_version: number, archived: boolean) => request<JournalEntry>(`/${id}/archive`, 'POST', { expected_version, archived }),
  history: (id: number, offset = 0, signal?: AbortSignal) => request<JournalPageResult<JournalRevision>>(`/${id}/versions?limit=20&offset=${offset}`, 'GET', undefined, signal),
};

export function journalDraft(entry?: JournalDraft | null): JournalDraft {
  return entry ? { kind: entry.kind, ticker: entry.ticker, title: entry.title, body: entry.body }
    : { kind: 'thesis', ticker: null, title: '', body: '' };
}

export function journalChanged(draft: JournalDraft, saved?: JournalDraft | null): boolean {
  const base = journalDraft(saved);
  return draft.kind !== base.kind || draft.ticker !== base.ticker || draft.title !== base.title || draft.body !== base.body;
}
