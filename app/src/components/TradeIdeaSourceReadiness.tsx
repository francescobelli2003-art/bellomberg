import { useT } from '@/i18n/provider';
import type { TradeIdeaPreflight } from '@/lib/tradeIdeas';

export const researchOnly = (value?: {analysis_mode?: string} | null) => value?.analysis_mode === 'fundamentals_research_v1';

/** Preflight copy follows the accepted contract; legacy runs keep their terms. */
export default function TradeIdeaSourceReadiness({preflight}: {preflight: TradeIdeaPreflight | null}) {
  const t = useT(), qualification = preflight?.source_qualification;
  const research = researchOnly(preflight) || researchOnly(qualification);
  // R14: not_run = verifica mai eseguita (un controllo precedente ha bloccato), failed = eseguita e non riuscita.
  // Nessuna delle due si presenta come descrizione delle fonti o come «fonti non qualificate».
  const execution = qualification?.execution_status;
  return <div className="ti-catalog">
    <h3>{t('tradeidea.sourceQualification')}</h3>
    <p>{execution === 'not_run' ? t('tradeidea.sourceCheckNotRun')
      : execution === 'failed' ? t('tradeidea.sourceCheckFailed')
      : research ? t('tradeidea.companyResearchSources')
      : qualification?.status === 'research_required' ? t('tradeidea.sourceResearchRequired')
      : qualification?.status === 'preparation_required' ? t('tradeidea.historyPreparationRequired')
      : qualification?.status === 'qualified' ? t('tradeidea.sourcesQualified') : t('tradeidea.sourcesBlocked')}</p>
    {!research && <small>{qualification?.method_id}</small>}
    {qualification?.reasons?.map((reason, index) => <p key={index}>{reason}</p>)}
    {research ? <><h3>{t('tradeidea.companyResearchPackage')}</h3><p>{t('tradeidea.companyResearchGrant')}</p></>
      : <><h3>{t('tradeidea.modelPreparation')}</h3><p>{preflight?.preparation?.required ? t('tradeidea.preparationRequired') : t('tradeidea.preparationReady')}</p>
        <p className="ti-field-note">{t('tradeidea.preparationGrant')}</p></>}
  </div>;
}
