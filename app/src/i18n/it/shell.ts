export const shell = {
  cancel: 'ANNULLA', retry: 'RIPROVA', unknown: 'Errore non dichiarato', component: 'Componente',
  confirm_keys: 'ESC ANNULLA · TAB CAMBIA PULSANTE · INVIO ATTIVA QUELLO IN FOCUS',
  runtime_error: 'ERRORE DI ESECUZIONE', runtime_note: 'Un componente ha generato un’eccezione. Puoi riprovare senza riavviare l’intera app.',
  error_trace: 'Traccia dell’errore', component_trace: 'Traccia dei componenti', app: 'Applicazione',
  mandate_loading: 'VERIFICA MANDATO…', mandate_error: 'MANDATO NON LEGGIBILE',
  mandate_note: 'Il file potrebbe essere in uso o danneggiato. La bozza della sessione resta intatta.',
  palette: 'Palette comandi (Ctrl+K)', search: 'CERCA TICKER · PAGINE · AZIONI',
  settings: 'Impostazioni', settings_unknown: '{key} · IMPOSTAZIONI · stato dei lavori automatici non leggibile',
  settings_failed: '{key} · IMPOSTAZIONI · {n} lavori automatici non riusciti', modules: 'Moduli Bellomberg',
  fx_error: 'FX NON DISPONIBILE · errore del servizio', fx_waiting: 'Attesa dei cambi…',
  fx_stale: 'Ultimo aggiornamento riuscito: i cambi mostrati sono STALE',
  engine: 'Motore', agents: 'Agenti', session: 'SESSIONE', live_run: 'RUN IN CORSO',
  ready: 'PRONTI', unavailable: 'N.D.', engine_restart: 'N.D. · RIAVVIO SERVIZIO NECESSARIO',
  workspace: 'Area di lavoro · F1–F19 · CTRL+K',
};

export const nav = {
  dashboard: 'Centro di comando', performance: 'Performance', watchlist: 'Preferiti', market: 'Mercati globali',
  news: 'Notizie', fundamentals: 'Fondamentali', factors: 'Fattori di rischio', montecarlo: 'Monte Carlo',
  vol: 'Opzioni e volatilità', edge: 'Ricerca opportunità', chat: 'Chat agenti', agents: 'Agenti in diretta',
  progress: 'Progressi agenti', memos: 'Archivio memo', decisions: 'Decisioni', trades: 'Inserimento operazioni',
  movements: 'Movimenti', mandato: 'Mandato e Diario', settings: 'Impostazioni',
  portfolio_group: 'Portafoglio', research_group: 'Ricerca', risk_group: 'Rischio', committee_group: 'Comitato',
  operations_group: 'Operazioni', mandate_group: 'Mandato', system_group: 'Sistema',
};

export const login = {
  storage_unavailable: 'Memoria del browser non disponibile. Accesso valido per questa sessione; dopo il ricaricamento potrebbe essere richiesto di nuovo il PIN.',
  module_memory: 'MEMORIA SQLITE', module_desks: 'DESK E AGENTI · CAPO', module_news: 'NOTIZIE / FRED',
  module_quant: 'QUANT GARCH / MC', module_valuation: 'VALUTAZIONI DCF',
  denied: 'PIN ERRATO · ACCESSO NEGATO', attempts: 'TROPPI TENTATIVI · RIPROVA FRA QUALCHE MINUTO',
  connection: 'ERRORE DI CONNESSIONE AL SERVIZIO', enter_four: 'DIGITA LE QUATTRO CIFRE DEL PIN',
  accepted: 'PIN ACCETTATO · CANALE APERTO', checking: 'VERIFICA PIN IN CORSO', enter: 'DIGITA IL PIN',
  subtitle: 'TERMINALE DI RICERCA PRIVATO', authentication: 'AUTENTICAZIONE PIN',
  granted: '◈ ACCESSO AUTORIZZATO', authenticating: '◌ AUTENTICAZIONE…', authorize: '◌ AUTORIZZA ACCESSO',
  operator: 'OPERATORE · ACCESSO RISERVATO',
  default_pin: 'PIN PREDEFINITO ATTIVO · IMPOSTA BELLOMBERG_PIN NEL FILE .ENV (PREDEFINITO: 1234)',
  authorization_granted: 'AUTORIZZAZIONE CONCESSA', authorization_waiting: 'IN ATTESA DI AUTORIZZAZIONE',
  local_backend: 'SERVIZIO LOCALE', session: 'ORBITA STABILE · DURATA SESSIONE 12H', secure_boot: 'AVVIO SICURO',
  handshake: 'VERIFICA DEL CANALE', operator_pin: 'PIN OPERATORE', verified: 'VERIFICATO',
  impact: 'IMPATTO 2026-OB · 4.2 KT', confirmed: 'CONFERMATO', backend_session: 'SESSIONE DEL SERVIZIO',
  authorized: 'AUTORIZZATA', start: 'AVVIO TERMINALE', status_unknown: 'STATO DEL PIN NON VERIFICATO',
};
