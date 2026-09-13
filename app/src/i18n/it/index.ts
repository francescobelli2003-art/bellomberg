import { ui } from './ui.js';
import { communications } from './communications.js';
import { activity } from './activity.js';
import { settings } from './settings.js';
import { newsdesk } from './newsdesk.js';
import { fundamentals } from './fundamentals.js';
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

export const it = {
  ui,
  communications,
  activity,
  settings,
  newsdesk,
  fundamentals,
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
    italiano: 'ITALIANO', inglese: 'INGLESE', titolo: 'Scegli la lingua',
    descrizione: 'Interfaccia e nuovi contenuti seguiranno la lingua scelta. Puoi cambiarla nelle impostazioni. I documenti esistenti restano nella lingua originale.',
    salva: 'SALVA LINGUA', salvataggio: 'SALVATAGGIO…', caricamento: 'Caricamento della preferenza…',
    errore: 'Preferenza non disponibile: {errore}', riprova: 'RIPROVA',
    cache_non_salvata: 'La lingua è salvata nel profilo. La memoria locale del browser non è disponibile: prima dell’accesso la schermata sarà bilingue.',
    non_verificata: 'Preferenza non verificata', ripara: 'RIPRISTINA E SALVA', rileggi: 'RILEGGI PROFILO',
    ripara_nota: 'La scelta ripristina il file illeggibile e ne conserva una copia verificata.',
    rileggi_nota: 'Rileggi il profilo prima di riprovare: il salvataggio potrebbe essere riuscito.',
    salvata: 'Lingua salvata e verificata.', backup_salvato: 'Lingua salvata. La copia del file precedente è stata verificata.',
  },
  layout: { impostazioni_guasti: '{tasto} · IMPOSTAZIONI · {n} lavori pianificati in errore' },
  numeri: {
    grafia_richiesta: 'Usa il formato di questo campo, per esempio {esempio}',
    migliaia_richieste: 'Separatori delle migliaia errati: scrivi {esempio} oppure togli i separatori delle migliaia',
    solo_cifre: 'usa solo cifre, virgola o punto',
    piu_di_un_decimale: 'più di un separatore decimale',
    ambiguo: '{t} è ambiguo: scrivi {senza} oppure {decimale}',
    grafia_estranea: '{t} usa la grafia inglese: in italiano scrivi ad esempio 1.234,56',
    migliaia_posizione: 'separatori in posizione non da migliaia: scrivi 1.234.567 oppure togli i punti',
    non_numero: 'non è un numero', maggiore_zero: 'deve essere maggiore di zero',
    manca_dopo_segno: 'manca il numero dopo il segno', obbligatorio: 'campo obbligatorio',
    intero: 'serve un numero intero', intervallo: 'deve essere fra {min} e {max}',
  },
};
