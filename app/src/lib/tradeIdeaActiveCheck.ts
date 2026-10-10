/* R14 seguito (B4, Opus 5.5, 10/10/2026): POST /consigliere/run dichiara `trade_idea_active_check`
   (anche in GET /consigliere/status/{task_id}). Con lo store Trade Idea non pronto il backend NON blocca
   la weekly ma salta il controllo «Trade Idea attiva»: la UI lo dichiara accanto all'avvio, mai muta.
   Nessun import di runtime: lo usano Dashboard, Agenti e la palette comandi. */
import type { Chiave, Parametri } from '@/i18n/t';
import type { TradeIdeaStorage } from './tradeIdeas';

export interface TradeIdeaActiveCheck {
  status: 'completed' | 'not_run' | string;
  reason: string | null;
  storage: TradeIdeaStorage | null;
}

type Traduci = (chiave: Chiave, parametri?: Parametri) => string;

/** Il controllo da dichiarare, o null. `completed` = eseguito; campo assente = backend anteriore al
 *  contratto (nessuna affermazione); ogni altro stato (not_run o sconosciuto) = controllo non eseguito. */
export function controlloAttivaSaltato(risposta: unknown): TradeIdeaActiveCheck | null {
  const raw = risposta && typeof risposta === 'object' ? (risposta as Record<string, unknown>).trade_idea_active_check : null;
  if (!raw || typeof raw !== 'object') return null;
  const c = raw as Record<string, unknown>;
  if (c.status === 'completed') return null;
  const storage = c.storage && typeof c.storage === 'object' ? c.storage as TradeIdeaStorage : null;
  return { status: typeof c.status === 'string' ? c.status : '?', reason: typeof c.reason === 'string' ? c.reason : null, storage };
}

/** «Controllo Trade Idea attiva non eseguito: <motivo>», motivo nella lingua corrente; il motivo
 *  non riconosciuto si mostra com'è, quello assente si dichiara assente. */
export function testoControlloAttiva(t: Traduci, check: TradeIdeaActiveCheck): string {
  const reason = check.reason || '';
  const code = check.storage?.error_code || check.storage?.status || t('tradeidea.activeCheckCodeMissing');
  const motivo = reason === 'trade_idea_storage_unavailable' ? t('tradeidea.activeCheckReasonStorage', { code })
    : reason.startsWith('trade_idea_store_error:') ? t('tradeidea.activeCheckReasonStore', { kind: reason.slice('trade_idea_store_error:'.length) || '?' })
    : reason || t('tradeidea.activeCheckReasonMissing');
  return t('tradeidea.activeCheckNotRun', { reason: motivo });
}
