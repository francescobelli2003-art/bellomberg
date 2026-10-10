import { useT } from '@/i18n/provider';
import type { TradeIdeaStorage } from '@/lib/tradeIdeas';
import { externalWebUrl } from '../../electron/security';

type Translation = ReturnType<typeof useT>;

/** R14: rimedio scelto da `storage.action`. La migrazione si propone SOLO se action === 'run_explicit_migration'
 *  E update_required === true (schema_absent/schema_partial); una delle due senza l'altra è incoerente e si
 *  tratta come stato sconosciuto; un'azione sconosciuta non la propone mai. Nessuna migrazione dalla UI.
 *  Anche la riga compatta della pagina Decisioni (provenienza Trade Idea non leggibile) usa questo testo. */
export function storageActionText(t: Translation, storage: TradeIdeaStorage): string {
  // R14 seguito (B2): lookup_failed = lettura non classificata dalla diagnosi → guasto d'accesso, qualunque
  // azione o flag accompagni lo stato (mai una migrazione su un guasto non diagnosticato).
  if (storage.status === 'lookup_failed') return t('tradeidea.storageActionAccess');
  switch (storage.action) {
    case 'run_explicit_migration': if (storage.update_required === true) return t('tradeidea.storageActionMigration'); break;
    case 'check_database_path': return t('tradeidea.storageActionPath');
    case 'inspect_schema': return t('tradeidea.storageActionSchema');
    case 'check_database_access': return t('tradeidea.storageActionAccess');
    case 'retry': if (storage.status === 'db_busy') return t('tradeidea.storageActionRetry'); break;
  }
  return t('tradeidea.storageActionUnknown', { status: storage.status || storage.action || '?' });
}

/** 10/10 (Opus 5.5): tono del riquadro. AVVISO (giallo, `ti-notice` base della pagina) per gli stati che
 *  non sono guasti: la migrazione esplicita dopo un update del codice (stessa coerenza di storageActionText:
 *  action run_explicit_migration CON update_required true) e il DB occupato da un'altra connessione (db_busy,
 *  action retry: si riprova). Tutto il resto (db_missing, db_unreadable, schema_incompatible, lookup_failed,
 *  combinazioni incoerenti o sconosciute) e' un GUASTO: rosso. */
export function storageTone(storage: TradeIdeaStorage): 'warn' | 'error' {
  if (storage.status === 'lookup_failed') return 'error';
  if (storage.action === 'run_explicit_migration' && storage.update_required === true) return 'warn';
  if (storage.action === 'retry' && storage.status === 'db_busy') return 'warn';
  return 'error';
}

/** Riquadro dello storage non pronto: guasti nello stile degli avvisi d'errore della pagina (ti-notice
 *  ti-error), stati da aggiornare/riprovare nel tono d'avviso (ti-notice). La guida è un link solo se è un
 *  URL web; un percorso del repository si mostra, non si incolla all'API. */
export default function TradeIdeaStorageNotice({ storage, lead }: { storage: TradeIdeaStorage; lead: string }) {
  const t = useT();
  const doc = storage.documentation || '';
  const web = doc ? externalWebUrl(doc) : null;
  const tono = storageTone(storage);
  return <div className={tono === 'error' ? 'ti-notice ti-error' : 'ti-notice'} role={tono === 'error' ? 'alert' : 'status'}
    data-ti-storage={storage.status} data-ti-storage-tone={tono}>
    <p><strong>{lead}</strong></p>
    <p>{storageActionText(t, storage)}</p>
    <p>{t('tradeidea.storageCode', { code: storage.error_code })}</p>
    {web ? <p><a href={web} target="_blank" rel="noreferrer">{t('tradeidea.storageGuide')}</a></p>
      : doc ? <p>{t('tradeidea.storageGuidePath', { path: doc })}</p> : null}
  </div>;
}
