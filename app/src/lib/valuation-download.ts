import { API_BASE, requestHeaders } from '@/lib/api';
import type { ValuationModel } from './api';
import { t } from '../i18n/t';

export interface PersonalValuation {
  id: string; ticker: string; source_generation: string; label: string;
  created_at: string; status: 'pending' | 'ready' | 'blocked';
  available: boolean; modified: boolean | null; error?: string | null;
}

async function modelRequest(ticker: string, suffix: string, body?: object, fetcher = fetch) {
  const response = await fetcher(API_BASE + `/valuation/models/${encodeURIComponent(ticker)}/${suffix}`, {
    method: body ? 'POST' : 'GET', headers: { ...requestHeaders(), ...(body ? { 'Content-Type': 'application/json' } : {}) },
    cache: 'no-store', ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const value = await response.json().catch(() => null);
  if (!response.ok || value === null) throw new Error(value?.detail?.message ||
    (typeof value?.detail === 'string' ? value.detail : t('fundamentals.actionFailed', { status: response.status })));
  return value;
}

export const requestModelRefresh = (ticker: string, requestId: string, fetcher = fetch) =>
  modelRequest(ticker, 'refresh', { request_id: requestId }, fetcher);

export const lockModel = (ticker: string, generation: string, locked: boolean, fetcher = fetch) =>
  modelRequest(ticker, 'lock', { generation_id: generation, locked }, fetcher);

export const createPersonalVariant = (ticker: string, generation: string, label: string, requestId: string, fetcher = fetch): Promise<PersonalValuation> =>
  modelRequest(ticker, 'variants', { generation_id: generation, label, request_id: requestId }, fetcher);

export async function personalVariants(ticker: string, fetcher = fetch): Promise<PersonalValuation[]> {
  return (await modelRequest(ticker, 'variants', undefined, fetcher)).variants;
}

export async function personalWorkbook(variant: PersonalValuation, fetcher = fetch): Promise<Blob> {
  if (!variant.available) throw new Error(t('fundamentals.currentUnavailable'));
  const response = await fetcher(API_BASE + `/valuation/models/${encodeURIComponent(variant.ticker)}/variants/${encodeURIComponent(variant.id)}/workbook`,
    { headers: requestHeaders(), cache: 'no-store' });
  if (!response.ok) throw new Error(t('fundamentals.downloadFailed', { status: response.status }));
  if (response.headers.get('X-Valuation-Generation') !== variant.source_generation) throw new Error(t('fundamentals.downloadMismatch'));
  return response.blob();
}

export async function historicalWorkbook(model: ValuationModel, fetcher = fetch): Promise<Blob> {
  const response = await fetcher(API_BASE + `/fundamentals/models/${encodeURIComponent(model.file)}/download`,
    { headers: requestHeaders(), cache: 'no-store' });
  if (!response.ok) throw new Error(t('fundamentals.downloadFailed', { status: response.status }));
  if (response.headers.get('X-Valuation-Copy') !== 'historical') throw new Error(t('fundamentals.downloadMismatch'));
  return response.blob();
}

/** Authenticated read of the exact current generation. Never fall back to an old filename. */
export async function currentWorkbook(model: ValuationModel, fetcher = fetch): Promise<Blob> {
  const path = `/valuation/models/${encodeURIComponent(model.ticker)}/generations/${encodeURIComponent(model.generation_id || '')}/workbook`;
  if (!model.current_generation || model.current_download !== path) throw new Error(t('fundamentals.currentUnavailable'));
  const response = await fetcher(API_BASE + path, { headers: requestHeaders(), cache: 'no-store' });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(error?.detail?.message || (typeof error?.detail === 'string' ? error.detail :
      t('fundamentals.downloadFailed', { status: response.status })));
  }
  if (response.headers.get('X-Valuation-Generation') !== model.generation_id) {
    throw new Error(t('fundamentals.downloadMismatch'));
  }
  return response.blob();
}
