export const shell = {
  cancel: 'CANCEL', retry: 'RETRY', unknown: 'Undeclared error', component: 'Component',
  confirm_keys: 'ESC CANCELS · TAB CHANGES BUTTON · ENTER ACTIVATES THE FOCUSED BUTTON',
  runtime_error: 'RUNTIME ERROR', runtime_note: 'A component raised an exception. You can retry without restarting the entire app.',
  error_trace: 'Error trace', component_trace: 'Component trace', app: 'Application',
  mandate_loading: 'CHECKING MANDATE…', mandate_error: 'MANDATE UNREADABLE',
  mandate_note: 'The file may be in use or damaged. Your session draft is preserved.',
  palette: 'Command palette (Ctrl+K)', search: 'SEARCH TICKERS · PAGES · ACTIONS',
  settings: 'Settings', settings_unknown: '{key} · SETTINGS · scheduled job status unavailable',
  settings_failed: '{key} · SETTINGS · {n} scheduled jobs failed', modules: 'Bellomberg modules',
  fx_error: 'FX UNAVAILABLE · service error', fx_waiting: 'Waiting for exchange rates…',
  fx_stale: 'Last successful update: the displayed exchange rates are STALE',
  engine: 'Engine', agents: 'Agents', session: 'SESSION', live_run: 'RUN IN PROGRESS',
  ready: 'READY', unavailable: 'N/A', engine_restart: 'N/A · SERVICE RESTART REQUIRED',
  workspace: 'Workspace · F1–F19 · CTRL+K',
};

export const nav = {
  dashboard: 'Command Center', performance: 'Performance', watchlist: 'Watchlist', market: 'Global Markets',
  news: 'News Desk', fundamentals: 'Fundamentals', factors: 'Factor Lab', montecarlo: 'Monte Carlo',
  vol: 'Vol Deck', edge: 'Edge Scanner', chat: 'Agent Chat', agents: 'Agents Live',
  progress: 'Agent Progress', memos: 'Memo Archive', decisions: 'Decisions', trades: 'Trade Entry',
  movements: 'Movements', mandato: 'Mandate and Journal', settings: 'Settings',
  portfolio_group: 'Portfolio', research_group: 'Research', risk_group: 'Risk', committee_group: 'Committee',
  operations_group: 'Operations', mandate_group: 'Mandate', system_group: 'System',
};

export const login = {
  storage_unavailable: 'Browser storage unavailable. Access is valid for this session; reloading may require your PIN again.',
  module_memory: 'SQLITE MEMORY', module_desks: 'DESKS AND AGENTS · CHIEF', module_news: 'NEWS / FRED',
  module_quant: 'QUANT GARCH / MC', module_valuation: 'DCF VALUATIONS',
  denied: 'INCORRECT PIN · ACCESS DENIED', attempts: 'TOO MANY ATTEMPTS · RETRY IN A FEW MINUTES',
  connection: 'SERVICE CONNECTION ERROR', enter_four: 'ENTER YOUR FOUR-DIGIT PIN',
  accepted: 'PIN ACCEPTED · CHANNEL OPEN', checking: 'CHECKING PIN', enter: 'ENTER PIN',
  subtitle: 'PRIVATE RESEARCH TERMINAL', authentication: 'PIN AUTHENTICATION',
  granted: '◈ ACCESS GRANTED', authenticating: '◌ AUTHENTICATING…', authorize: '◌ AUTHORIZE ACCESS',
  operator: 'OPERATOR · RESTRICTED ACCESS',
  default_pin: 'DEFAULT PIN ACTIVE · SET BELLOMBERG_PIN IN THE .ENV FILE (DEFAULT: 1234)',
  authorization_granted: 'AUTHORIZATION GRANTED', authorization_waiting: 'AWAITING AUTHORIZATION',
  local_backend: 'LOCAL SERVICE', session: 'STABLE ORBIT · SESSION TTL 12H', secure_boot: 'SECURE BOOT',
  handshake: 'CHANNEL VERIFICATION', operator_pin: 'OPERATOR PIN', verified: 'VERIFIED',
  impact: 'IMPACT 2026-OB · 4.2 KT', confirmed: 'CONFIRMED', backend_session: 'SERVICE SESSION',
  authorized: 'AUTHORIZED', start: 'STARTING TERMINAL', status_unknown: 'PIN STATUS NOT VERIFIED',
};
