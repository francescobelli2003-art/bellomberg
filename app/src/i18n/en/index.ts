import { ui } from './ui.js';
import { communications } from './communications.js';
import { activity } from './activity.js';
import { settings } from './settings.js';
import { newsdesk } from './newsdesk.js';
import { fundamentals } from './fundamentals.js';
import { filings } from './filings.js';
import { factors } from './factors.js';
import { montecarlo } from './montecarlo.js';
import { edge } from './edge.js';
import { memoarchive } from './memoarchive.js';
import { decisiondesk } from './decisiondesk.js';
import { movements } from './movements.js';
import { mandate } from './mandate.js';
import { journal } from './journal.js';
import { voldeck } from './voldeck.js';
import { progress } from './progress.js';
import { shell, nav, login } from './shell.js';
import { trade } from './trade.js';
import { dashboard } from './dashboard.js';

export const en = {
  ui,
  communications,
  activity,
  settings,
  newsdesk,
  fundamentals,
  filings,
  factors,
  montecarlo,
  edge,
  memoarchive,
  decisiondesk,
  movements,
  mandate,
  journal,
  voldeck,
  progress,
  shell, nav, login,
  trade,
  dashboard,
  lingua: {
    italiano: 'ITALIAN', inglese: 'ENGLISH', titolo: 'Choose your language',
    descrizione: 'The interface and new content will follow your choice. You can change it in settings. Existing documents stay in their original language.',
    salva: 'SAVE LANGUAGE', salvataggio: 'SAVING…', caricamento: 'Loading your preference…',
    errore: 'Preference unavailable: {errore}', riprova: 'RETRY',
    cache_non_salvata: 'Your language is saved in the profile. Browser storage is unavailable: the sign-in screen will be bilingual.',
    non_verificata: 'Preference not verified', ripara: 'REPAIR AND SAVE', rileggi: 'RELOAD PROFILE',
    ripara_nota: 'Saving repairs the unreadable file and preserves a verified backup.',
    rileggi_nota: 'Reload the profile before trying again: the save may have succeeded.',
    salvata: 'Language saved and verified.', backup_salvato: 'Language saved. A backup of the previous file was verified.',
  },
  layout: { impostazioni_guasti: '{tasto} · SETTINGS · {n} scheduled jobs failed' },
  numeri: {
    grafia_richiesta: 'Use the format of this field, for example {esempio}',
    migliaia_richieste: 'Invalid thousands separators: write {esempio} or remove the thousands separators',
    solo_cifre: 'use digits, a comma or a decimal point only',
    piu_di_un_decimale: 'more than one decimal separator',
    ambiguo: '{t} is ambiguous: write {senza} or {decimale}',
    grafia_estranea: '{t} uses Italian notation: in English write for example 1,234.56',
    migliaia_posizione: 'invalid thousands separators: write 1,234,567 or remove the commas',
    non_numero: 'not a number', maggiore_zero: 'must be greater than zero',
    manca_dopo_segno: 'number missing after the sign', obbligatorio: 'required field',
    intero: 'an integer is required', intervallo: 'must be between {min} and {max}',
  },
};
