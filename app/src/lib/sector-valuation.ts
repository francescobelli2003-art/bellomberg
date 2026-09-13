import { t as tr } from '@/i18n/t';
import type { ValuationModel } from './api';

/** Presentation guard for stale UI/API caches. Economic validation stays in Python. */
export function valuationIsUsable(model: ValuationModel): boolean {
  const decision = model.valuation_decision ?? model.detail?.valuation_decision;
  const sanity = model.sanity_severity ?? model.detail?.sanity?.severity;
  const quality = model.analytical_quality ?? model.detail?.analytical_quality;
  return model.valuation_usability?.usable === true && !model.flagged
    && model.detail?.valuation_usability?.usable !== false
    && (!model.detail?.sanity?.severity || ['OK', 'WARN'].includes(model.detail.sanity.severity))
    && model.identity_status === 'canonical'
    && !!model.snapshot_id && !!model.generation_id
    && decision?.decision_status === 'resolved' && decision.support_status === 'integrated'
    && decision.requirements_status === 'complete' && !(decision.missing_fields?.length)
    && quality?.status === 'DOCUMENTATA' && !(quality.issues?.length)
    && (sanity === 'OK' || sanity === 'WARN');
}

export function valuationBadge(model: ValuationModel): string {
  const sanity = model.sanity_severity ?? model.detail?.sanity?.severity;
  if (model.flagged || sanity === 'BLOCK' || model.detail?.sanity?.severity === 'BLOCK') return 'BLOCK';
  if (!valuationIsUsable(model)) return 'n.d.';
  return sanity === 'WARN' ? 'WARN' : 'OK';
}

/** Do not let an old page cache revive secondary FV, NAV targets, SOTP or IRR. */
export function prepareValuationModel(model: ValuationModel): ValuationModel {
  const copy = structuredClone(model);
  if (valuationIsUsable(copy)) return copy;
  const derived = /^(fair_value|upside)|^(target_price|price_target|nav_target|fv_ps|sotp_ev_total|sotp_delta_pct|methods_delta|methods_divergence|irr|irr_exit_peer|exit_ptbv_just|exit_ptbv_peer|exit_multiple)$/;
  function obscure(value: unknown, context = ''): void {
    if (!value || typeof value !== 'object') return;
    for (const [key, item] of Object.entries(value)) {
      if (derived.test(key) || context === 'by_scenario') {
        (value as Record<string, unknown>)[key] = null;
      } else if (key !== 'valuation_decision' && key !== 'analytical_quality') {
        obscure(item, key);
      }
    }
  }
  obscure(copy);
  copy.fair_value = null;
  copy.upside_pct = null;
  copy.valuation_usability = { usable: false,
    reasons: copy.valuation_usability?.reasons?.length ? copy.valuation_usability.reasons
      : [tr('fundamentals.f139')],
    missing_fields: copy.valuation_usability?.missing_fields ?? [] };
  return copy;
}
