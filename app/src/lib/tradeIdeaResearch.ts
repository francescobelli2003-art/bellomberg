import { API_BASE, clearSessionAndReload, requestHeaders } from './api';
import { tradeIdeaRequest, TradeIdeaApiError, tradeIdeaApiError } from './tradeIdeas';
import { t } from '../i18n/t';

export type ResearchObject = Record<string, unknown>;
export interface ResearchEvent {
  id: number; kind: string; at: string; run_id: string; generation_id: string; data: ResearchObject;
  artifact?: {name: string; kind: string; sha256: string; download_url: string};
}
export interface ResearchInput {
  driver: string; scenario: string; value: number | number[]; unit: string; period: string; rationale: string; source_id: string;
}
export interface ResearchCost {
  budget_limit_usd: string; charged_usd: string; reserved_usd: string; unknown_requests: number;
  remaining_known_usd: string | null; requests: number; overrun: boolean;
  phases: Array<{phase: string; charged_usd: string; reserved_usd: string; unknown_requests: number; requests: number; cache_read_tokens: number | null; cache_write_tokens: number | null}>;
  unresolved: Array<{request_id: string; role: string; status: string; reason?: string}>;
  deterministic_actions: Record<string, number>;
}
export interface ResearchState {
  run_id: string; ticker: string;
  model: {status: string; reason?: string; generation_id?: string; snapshot_id?: string; method?: string; currency?: string;
    price?: number; valuation_date?: string; values?: Record<string, number | null>; editable?: ResearchInput[];
    kind?: 'intrinsic' | 'exposure'; intrinsic_value_applicable?: boolean};
  versions: Array<{generation_id: string; label: string; valuation_date: string; usable: boolean}>;
  history: ResearchEvent[];
  pending_actions?: Array<{request_id:string;generation_id:string;kind:string;at:string;status:string;reason?:string}>;
  objections: Array<{id: number; run_id: string; objection: string; status: string; at: string; responses: Array<{id: number; text: string; status: string}>}>;
  cost: ResearchCost; conclusions_status: string;
}
export interface ResearchExportPreview {
  status: string; reasons: string[]; included_sections: string[];
  exclusions: Array<{category: string; label: string; count: number}>;
  limitation: string;
}
const root = (run: string) => `/runs/${encodeURIComponent(run)}/workspace`;
export const TradeIdeaResearch = {
  view: async (run: string, generation?: string): Promise<ResearchState> => {
    const value = await tradeIdeaRequest<ResearchState>(root(run) + (generation ? `?generation_id=${encodeURIComponent(generation)}` : ''));
    if (!value || value.run_id !== run || !value.model || !Array.isArray(value.history) || !Array.isArray(value.versions) || !value.cost) {
      throw new TradeIdeaApiError(t('tradeIdeaResearch.responseIncomplete'), 502);
    }
    return value;
  },
  act: (run: string, generation: string, kind: string, data: ResearchObject, requestId: string) =>
    tradeIdeaRequest<ResearchEvent>(root(run) + '/actions', {kind, data, generation_id: generation, request_id: requestId}),
  recover: (run:string, requestId:string) => tradeIdeaRequest<ResearchEvent>(root(run)+'/actions/'+encodeURIComponent(requestId)+'/recover',{}),
  preview: (run: string) => tradeIdeaRequest<ResearchExportPreview>(root(run) + '/export-preview'),
};
export async function researchArtifactBlob(event: ResearchEvent): Promise<Blob> {
  if (!event.artifact) throw new Error(t('tradeIdeaResearch.artifactUnavailable'));
  const response = await fetch(API_BASE + event.artifact.download_url, {headers: requestHeaders(), cache: 'no-store'});
  if (response.status === 401 || response.status === 403) clearSessionAndReload();
  if (!response.ok) {
    throw tradeIdeaApiError(await response.json().catch(() => null), response.status);
  }
  return response.blob();
}
