import { eLingua, impostaLinguaCorrente, salvaLingua, type Lingua } from './lingua.js';

export type PreferenzaLingua = {
  language: Lingua; selected: boolean; source: 'preferences' | 'compatibility_default';
};
export type SalvaPreferenza = { language: Lingua; repair_fingerprint?: string };
export type ClientePreferenze = {
  preferences: () => Promise<unknown>;
  savePreferences?: (body: SalvaPreferenza) => Promise<unknown>;
};
export type PreferenzaVerificata = PreferenzaLingua & { cacheSaved: boolean; backupCreated?: boolean };

export class ErrorePreferenza extends Error {
  constructor(message: string, readonly fingerprint?: string, readonly code?: string, readonly status?: number) {
    super(message);
  }
}

function risposta(value: unknown): PreferenzaLingua {
  const r = value as Partial<PreferenzaLingua> | null;
  if (!r || !eLingua(r.language) || typeof r.selected !== 'boolean'
      || (r.selected ? r.source !== 'preferences' : (r.source !== 'compatibility_default' || r.language !== 'it'))) {
    throw new ErrorePreferenza('Invalid preference response / Risposta preferenze non valida');
  }
  return { language: r.language, selected: r.selected, source: r.selected ? 'preferences' : 'compatibility_default' };
}

function errore(value: unknown): ErrorePreferenza {
  if (value instanceof ErrorePreferenza) return value;
  const e = value as { response?: { status?: number; data?: { detail?: unknown } }; message?: string };
  const detail = e?.response?.data?.detail;
  const d = detail && typeof detail === 'object' ? detail as Record<string, unknown> : undefined;
  const message = typeof d?.message === 'string' ? d.message : typeof detail === 'string' ? detail
    : e?.message || 'Request failed / Richiesta non riuscita';
  return new ErrorePreferenza(message,
    typeof d?.fingerprint === 'string' && /^[a-f0-9]{64}$/.test(d.fingerprint) ? d.fingerprint : undefined,
    typeof d?.code === 'string' ? d.code : undefined, e?.response?.status);
}

function applica(r: PreferenzaLingua): PreferenzaVerificata {
  if (!r.selected) return { ...r, cacheSaved: false };
  const cacheSaved = salvaLingua(r.language);
  impostaLinguaCorrente(r.language);
  return { ...r, cacheSaved };
}

export async function caricaPreferenza(client: ClientePreferenze): Promise<PreferenzaVerificata> {
  try { return applica(risposta(await client.preferences())); }
  catch (e) { throw errore(e); }
}

/** Change the live UI only after independent readback. A failed response can mean
 * the profile was written: callers offer a fresh read, never an automatic retry. */
export async function scegliLingua(language: Lingua, client: ClientePreferenze, repairFingerprint?: string): Promise<PreferenzaVerificata> {
  try {
    if (!eLingua(language)) throw new ErrorePreferenza('Unsupported language / Lingua non supportata');
    if (!client.savePreferences) throw new ErrorePreferenza('Preference writer unavailable / Salvataggio non disponibile');
    const body: SalvaPreferenza = { language, ...(repairFingerprint ? { repair_fingerprint: repairFingerprint } : {}) };
    const raw = await client.savePreferences(body);
    const written = risposta(raw);
    const read = risposta(await client.preferences());
    if (!written.selected || !read.selected || written.language !== language || read.language !== language) {
      throw new ErrorePreferenza('Saved preference changed: reload / Preferenza salvata cambiata: rileggi');
    }
    if (repairFingerprint && (raw as { backup_created?: boolean }).backup_created !== true) {
      throw new ErrorePreferenza('Repair backup not confirmed / Backup del ripristino non confermato');
    }
    return { ...applica(read), backupCreated: (raw as { backup_created?: boolean }).backup_created === true };
  } catch (e) { throw errore(e); }
}
