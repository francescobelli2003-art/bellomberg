// Deterministic, ephemeral HTTP fixture for the all-pages Electron harness.
// Every response is synthetic; unsupported reads and every write are recorded.
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');

// Public repository example supplies the full mandate field schema; values
// returned below are cloned and explicitly synthetic fixture values.
const mandatoExample = require('../../../src/bellomberg/resources/examples/mandato_pm.example.json');
const clone = value => JSON.parse(JSON.stringify(value));

const stamp = '2026-09-29T12:00:00Z';
const positions = Array.from({ length: 8 }, (_, i) => {
  const n = i + 1, ticker = ['SYN1', 'SYN2', 'SYN3', 'SYN4', 'SYN5', 'SYN6', 'SYN7', 'SYN8'][i];
  return { ticker, nome: `Synthetic holding ${n}`, quantita: 10 + n, prezzo_medio: 96 + n,
    prezzo_live: 101 + n, valuta: 'EUR', valore_mercato: (10 + n) * (101 + n),
    pl_eur: n * 21 - 50, pl_pct: n - 3, peso_pct: 20 - n * 1.5,
    prev_close: 100 + n, prev_close_ts: stamp, prev_close_source: 'fixture', fx_to_eur: 1 };
});
const dates = Array.from({ length: 22 }, (_, i) => `2026-09-${String(i + 1).padStart(2, '0')}`);
const bars = Array.from({ length: 120 }, (_, i) => ({ t: Math.floor(Date.UTC(2026, 5, 1 + i) / 1000),
  o: 98 + i * .2, h: 100 + i * .2, l: 97 + i * .2, c: 99 + i * .2, v: 1000 + i * 10 }));
const syntheticNow = () => new Date().toISOString();
const newsAt = hoursAgo => new Date(Date.now() - hoursAgo * 3600_000).toISOString();
const wireItems = [
  { id: 9901, title: 'Synthetic chip demand holds through quarter-end', snippet: 'Fixture-only report: a representative synthetic supplier describes stable lead times and planned capacity.', source: 'Fixture Wire', url: null, published_at: newsAt(2), pulled_at: newsAt(1.8), ticker_mentioned: 'SYN1', theme: 'technology', provider: 'fixture-wire', sentiment: 'positive', sentiment_score: .64, relevance: 9.4, summary_status: 'available', summary_language: 'en', summary_note: null, headline_it: 'Domanda sintetica stabile', why_matters: 'Synthetic fixture context only.', title_original: 'Synthetic chip demand holds through quarter-end', snippet_original: 'Fixture-only source excerpt.' },
  { id: 9902, title: 'Synthetic regulator proposes reporting timetable', snippet: 'An invented calendar item used to exercise the article list and filters.', source: 'Fixture Journal', url: null, published_at: newsAt(7), pulled_at: newsAt(6.8), ticker_mentioned: 'SYN2', theme: 'regulation', provider: 'fixture-journal', sentiment: 'neutral', sentiment_score: 0, relevance: 8.3, summary_status: 'available', summary_language: 'en', summary_note: null, headline_it: 'Calendario regolatorio sintetico', why_matters: 'Synthetic fixture context only.', title_original: 'Synthetic regulator proposes reporting timetable', snippet_original: 'Fixture-only source excerpt.' },
  { id: 9903, title: 'Synthetic energy costs ease in scenario data', snippet: 'A fictional market note gives the populated news desk a second theme and provider.', source: 'Fixture Research', url: null, published_at: newsAt(16), pulled_at: newsAt(15.8), ticker_mentioned: 'SYN3', theme: 'commodities', provider: 'fixture-research', sentiment: 'negative', sentiment_score: -.42, relevance: 7.1, summary_status: 'available', summary_language: 'en', summary_note: null, headline_it: 'Costi sintetici in calo', why_matters: 'Synthetic fixture context only.', title_original: 'Synthetic energy costs ease in scenario data', snippet_original: 'Fixture-only source excerpt.' },
];
const deskStamp = syntheticNow();
const briefing = { briefing_md: '## Synthetic briefing\n\nThree fictional items cover technology, regulation, and commodities. All names, figures, and summaries are test data.\n\n### Watch\n- SYN1: capacity commentary (fixture).\n- SYN2: reporting timetable (fixture).', language: 'en', generated_at: deskStamp, age_minutes: 4, slot_label: 'Synthetic close', news_count: 3, stale: false };
const usableDecision = { method_id: 'operating_fcff', decision_status: 'resolved', support_status: 'integrated', requirements_status: 'complete', missing_fields: [], rationale: 'Synthetic fixture valuation contract.' };
const usableQuality = { status: 'DOCUMENTATA', issues: [] };
const modelDetail = { engine: 'operating_v3', method: 'operating_fcff', payload_currency: 'EUR', valuation_date: '2026-09-29', valuation_basis: 'Synthetic scenario assumptions for UI verification.', valuation_usability: { usable: true, reasons: [], missing_fields: [] }, valuation_decision: clone(usableDecision), analytical_quality: clone(usableQuality), sanity: { severity: 'OK', headline: 'Synthetic ranges are internally consistent.' }, fair_value_weighted: 128.4, fair_value_blend: 126.8, fair_value_base: 129, fair_value_bull: 162, fair_value_bear: 88, fair_value_final: 128.4, holding_irr: { irr: .14, years: 3, by_scenario: { bear: -.03, base: .14, bull: .27 } }, peers_used: ['SYN2', 'SYN3'], peer_note: 'Synthetic peer set.', wacc_used: .091, _timestamp: deskStamp };
const fundamentalsModels = [
  { file: 'SYN1_SYNTHETIC.xlsx', dir: 'synthetic-fixture', engine: 'operating_v3', ticker: 'SYN1', matched: true, identity_status: 'canonical', snapshot_id: 'fixture-snapshot-syn1', generation_id: 'fixture-generation-syn1-r4', current_generation: true, current_download: '/valuation/models/SYN1/generations/fixture-generation-syn1-r4/workbook', historical_download: false, automation: { status: 'ready', locked: false, current: { generation_id: 'fixture-generation-syn1-r4', revision: 4, as_of: deskStamp, published_at: deskStamp }, approval: { publication_origin: 'synthetic_fixture', active: true }, latest_publication_attempt: null, latest_prepare_job: { status: 'succeeded', reason: 'Synthetic fixture only', updated_at: deskStamp }, latest_price_job: null }, valuation_usability: { usable: true, reasons: [], missing_fields: [] }, valuation_decision: clone(usableDecision), analytical_quality: clone(usableQuality), canonical: true, generated_at: deskStamp, flagged: false, fair_value: 128.4, price_at_thesis: 105, price_model_as_of: deskStamp, upside_pct: 22.3, upside_today_pct: 22.3, market_quote: { status_at_read: 'ok', price: 105, currency: 'EUR', as_of: deskStamp }, thesis_date: deskStamp.slice(0, 10), variant_view: 'Synthetic base case: measured growth, gradual margin expansion, and no external-company assumptions.', sanity_severity: 'OK', sanity_headline: 'Synthetic ranges are internally consistent.', detail: clone(modelDetail), memo_id: 300 },
  { file: 'SYN1_SYNTHETIC_r3.xlsx', dir: 'synthetic-fixture/archive', engine: 'operating_v3', ticker: 'SYN1', matched: true, identity_status: 'canonical', snapshot_id: 'fixture-snapshot-syn1-r3', generation_id: 'fixture-generation-syn1-r3', current_generation: false, current_download: '', historical_download: true, automation: { status: 'unavailable', locked: null, current: null }, valuation_usability: { usable: false, reasons: ['Archived synthetic revision.'], missing_fields: [] }, valuation_decision: clone(usableDecision), analytical_quality: clone(usableQuality), canonical: true, generated_at: newsAt(36), flagged: true, fair_value: 118, price_at_thesis: 102, upside_pct: 15.7, sanity_severity: 'WARN', detail: { ...clone(modelDetail), valuation_usability: { usable: false, reasons: ['Archived synthetic revision.'], missing_fields: [] }, sanity: { severity: 'WARN', headline: 'Archived synthetic revision.' }, fair_value_weighted: 118, fair_value_base: 118 } },
  { file: 'SYN2_SYNTHETIC_legacy.xlsx', dir: 'synthetic-fixture/archive', engine: 'operating_v3', ticker: 'SYN2', matched: false, identity_status: 'canonical', snapshot_id: 'fixture-snapshot-syn2', generation_id: 'fixture-generation-syn2-r2', current_generation: false, current_download: '', historical_download: true, automation: { status: 'unavailable', locked: null, current: null }, valuation_usability: { usable: true, reasons: [], missing_fields: [] }, valuation_decision: clone(usableDecision), analytical_quality: clone(usableQuality), canonical: true, generated_at: newsAt(48), flagged: false, fair_value: 74.2, price_at_thesis: 71, upside_pct: 4.5, sanity_severity: 'WARN', detail: { ...clone(modelDetail), sanity: { severity: 'WARN', headline: 'Synthetic archived scenario for history comparison.' }, fair_value_weighted: 74.2, fair_value_base: 74.2 } },
];
const personalVariants = [{ id: 'fixture-variant-syn1-1', ticker: 'SYN1', source_generation: 'fixture-generation-syn1-r4', label: 'Synthetic downside sensitivity', created_at: newsAt(2), status: 'ready', available: true, modified: true }];
const filingCitationBefore = { sezione: 'Synthetic segment note', testo: 'Fixture-only prior period statement.', url: 'https://example.invalid/synthetic-prior', sha256: 'synthetic-hash-before', pagine_fisiche: [8], inizio: 120, fine: 181, src: 'fixture' };
const filingCitationAfter = { sezione: 'Synthetic segment note', testo: 'Fixture-only current period statement with a changed outlook.', url: 'https://example.invalid/synthetic-current', sha256: 'synthetic-hash-after', pagine_fisiche: [9], inizio: 205, fine: 291, src: 'fixture' };
const filingRuns = [
  { id: 990, ticker: 'SYN1', status: 'completed', started_at: newsAt(12), finished_at: newsAt(11.9), trigger: 'fixture', profile_version: 2, reason: null },
  { id: 989, ticker: 'SYN1', status: 'completed', started_at: newsAt(300), finished_at: newsAt(299.9), trigger: 'fixture', profile_version: 1, reason: null },
];
const filingResult = { stato: 'confronto_disponibile', motivi: ['Synthetic fixture; not a real filing review.'], candidati: [{ fonte: 'Synthetic report', url: 'https://example.invalid/synthetic-current', stato: 'available', motivi: [], sha256: 'synthetic-hash-after', metadati: { periodo_inizio: '2026-01-01', periodo_fine: '2026-06-30', tipo: 'fixture' } }], copertura: { stato: 'sufficiente', limiti: ['No external document retrieved.'], candidati_osservati: 2, max_documenti: 4, documenti_tentati: 2 }, freschezza: { stato: 'synthetic_fixture', checked_at: deskStamp, ultimo_periodo: '2026-06-30', next_report_date: '2026-10-31', next_report_source: 'https://example.invalid/calendar', verificato_il: deskStamp, motivi: [] }, fonti: [{ nome: 'Synthetic archive', stato: 'available', motivi: [] }], coppia: { ambito: 'synthetic six-month comparison', prima: { url: filingCitationBefore.url, sha256: filingCitationBefore.sha256, metadati: { periodo_inizio: '2025-07-01', periodo_fine: '2025-12-31' } }, dopo: { url: filingCitationAfter.url, sha256: filingCitationAfter.sha256, metadati: { periodo_inizio: '2026-01-01', periodo_fine: '2026-06-30' } } }, confronto_corrente: { stato: 'computed', motivi: ['Deterministic synthetic text comparison.'], limiti: ['Fixture content only.'], sezioni_confrontate: ['Synthetic segment note', 'Synthetic outlook'], similarita_sezioni: { 'Synthetic segment note': { jaccard: .72, coseno: .81, metodo: 'synthetic fixture' } }, misure: { segmenti_prima: 3, segmenti_dopo: 4, cambiamenti: 1 }, cambiamenti: [{ tipo: 'outlook_changed', prima: clone(filingCitationBefore), dopo: clone(filingCitationAfter) }], segmenti_non_confrontabili: [], ambito: 'synthetic six-month comparison' }, confronto_storico: null, ultimo_non_verificato: false };
// Filing page (phase E): one synthetic title per state the page must handle. Names, CIKs and
// documents are invented; no real issuer, filing, model call or SEC/ESEF lookup is represented.
const filingTitle = (ticker, nome, extra) => ({ ticker, nome, gruppo: 3, stato_riga: `${ticker} · no filing profile (synthetic fixture)`,
  fonte: null, ultimo_confronto: null, run_id: null, novita: false, profilo: false, escluso: false, documento: null,
  cambiamenti: 0, run_attivo: null, ultimo_errore: null, attivazione: null, proposta_ai: null, nel_contesto: true, prossimo_at: null, ...extra });
const filingTitles = [
  filingTitle('SYN1', 'Nova Systems', { stato: 'novita', gruppo_ui: 'novita', gruppo: 0, documento: 'SEC 10-Q', fonte: 'SEC',
    stato_riga: 'SYN1 · SEC 10-Q · 1 change since the last committee run (synthetic fixture)', ultimo_confronto: newsAt(11.9),
    run_id: 990, novita: true, profilo: true, cambiamenti: 1, prossimo_at: newsAt(-120) }),
  filingTitle('SYN2', 'Kore Industrial', { stato: 'da_confermare', gruppo_ui: 'da_sistemare',
    attivazione: { esito: 'da_confermare', motivo: 'Two synthetic SEC issuers share this name.', candidati: 2, at: newsAt(30) } }),
  filingTitle('SYN3', 'Acme Retail', { stato: 'senza_fonte', gruppo_ui: 'senza_fonte',
    attivazione: { esito: 'senza_fonte', motivo: 'No synthetic SEC or ESEF issuer found.', candidati: 0, at: newsAt(30) } }),
  filingTitle('SYN4', 'Orsa Energia', { stato: 'proposta_ai', gruppo_ui: 'da_sistemare',
    proposta_ai: { sha256: 'synthetic-ai-sha-syn4', url: 'https://example.invalid/orsa-half-year-2026.pdf', at: newsAt(20), salvabile: true, verificate: 2 } }),
];
const filingOverview = (ambito = 'portafoglio', extra = {}) => ({ ambito,
  controllo_giornaliero: { attivo: true, forzato_spento_da_env: false, prossimo_at: newsAt(-12), ultimo_fine_at: newsAt(11.9) },
  titoli: clone(filingTitles).map(t => ({ ...t, nel_contesto: ambito === 'portafoglio' })),
  copertura: { totale: 4, con_confronto: 1, aggiornati: 1, non_aggiornati: 0, senza_confronto: 0, senza_profilo: 3, esclusi: 0 },
  contesto: { caratteri: 412, budget: 6000, omessi_totali: 0 },
  aggiornamento: { status: 'idle', trigger: 'fixture', started_at: newsAt(12), finished_at: newsAt(11.9), error: null }, ...extra });
const filingPreview = ticker => {
  const testo = ticker === 'SYN1' ? 'SYN1 · SEC 10-Q · ~ Synthetic segment note «Fixture-only current period statement with a changed outlook.» [C1-dopo]'
    : `${ticker} · no filing profile (synthetic fixture)`;
  return { ticker, testo, caratteri: testo.length, omessi: 0, in_evidenza: ticker === 'SYN1' ? ['C1'] : [],
    contesto_totale: { caratteri: 412, budget: 6000 }, nota: 'Synthetic context preview only.' };
};
const filingProposal = ticker => ticker === 'SYN2'
  ? { ticker, nome: 'Kore Industrial', profilo_attivo: false, escluso: false, preferita: 'sec',
    sec: { stato: 'ambiguo', motivo: 'Two synthetic issuers match the name.', candidati: [
      { cik: '0009990021', ticker: 'SYN2', nome: 'Kore Industrial Inc', origine: 'nome' },
      { cik: '0009990022', ticker: 'SYN2', nome: 'Kore Industrial Holdings', origine: 'nome_simile' }] } }
  : { ticker, nome: filingTitles.find(t => t.ticker === ticker)?.nome || null, profilo_attivo: false, escluso: false, preferita: null,
    sec: { stato: 'nessuno', candidati: [], motivo: 'No synthetic SEC issuer.' },
    esef: { stato: 'nessuno', candidati: [], motivo: 'No synthetic ESEF issuer (LEI 9999000000000000SYN0 range only).' } };
const filingAiSections = [
  { nome: 'Principal risks', inizio: 'Principal risks', fine: 'Outlook', caratteri: 4200, pagine: [14, 15, 16], anteprima: 'Synthetic risk text.' },
  { nome: 'Outlook', inizio: 'Outlook', fine: 'Condensed statements', caratteri: 1800, pagine: [17], anteprima: 'Synthetic outlook text.' },
];
const filingAiSaved = ticker => ({ stato: 'done', sha256: `synthetic-ai-sha-${ticker.toLowerCase()}`, url: 'https://example.invalid/orsa-half-year-2026.pdf',
  tipo: 'semestrale', lingua: 'en', periodo: { inizio: '2026-01-01', fine: '2026-06-30' }, verificate: clone(filingAiSections), scartate: [],
  salvabile: true, motivi: [], avvisi: [], altri: [], modello: 'fixture-model', costo_eur: 0.0038, cached: true,
  at: newsAt(20), da_riverificare: false, accettata: false });
const filingAiEstimate = (ticker, url) => ({ stato: 'ok', ticker, url, sha256: `synthetic-ai-sha-${ticker.toLowerCase()}`, pagine: 38,
  caratteri_input: 94000, righe: 2100, pagine_indice: [2, 3], troncato: 0, lingua_rilevata: 'en', cache: false, modello: 'fixture-model',
  token_input_stimati: 37600, token_output_max: 4000, costo_max_eur: 0.0412, costo_max_usd: 0.0448, tariffe_origine: 'listino', motivo: null });
const portfolio = { source: 'Synthetic fixture', n_positions: positions.length, positions,
  totale_valore_mercato_eur: 64218.9, totale_pl_eur: 2180.42, cash_disponibile_eur: 12345.67,
  // Exact public availability sentinel consumed by portfolioValues. Still a
  // synthetic in-process response; no SQLite file or backend is read.
  cash_source: 'sqlite:cash_state', cash_source_note: 'Synthetic fixture response only',
  nav_total_eur: 76564.57, timestamp: stamp, as_of: stamp, stale_positions: ['SYN8'] };
const decisions = Array.from({ length: 8 }, (_, i) => ({ id: 700 + i, memo_id: 300 + i,
  timestamp: `2026-09-${String(29 - i).padStart(2, '0')}T10:00:00Z`, action: i % 2 ? 'HOLD' : 'RESEARCH',
  ticker: positions[i % positions.length].ticker, eur_amount: 1000 + i * 250, timing: 'synthetic fixture',
  confidence: i % 2 ? 'MEDIUM' : 'HIGH', status: i < 5 ? 'PENDING' : 'EXECUTED', pm_feedback: null,
  outcome_pct: i - 2, rationale: `Synthetic rationale ${i + 1}`, archive_override: false,
  // R01: le RESEARCH dichiarano le note lette (lista vuota vera), altrimenti la UI mostra «note non disponibili»
  ...(i % 2 ? {} : { notes: [], notes_status: 'available', notes_error: null }) }));
const navHistory = { dates, nav_eur: dates.map((_, i) => 60000 + i * 100), cost_basis_eur: dates.map((_, i) => 58000 + i * 80),
  pnl_eur: dates.map((_, i) => 2000 + i * 20), cash_eur: 12345.67, nav_total_eur: dates.map((_, i) => 72000 + i * 110),
  first_trade_date: dates[0], tickers: positions.map(p => p.ticker), n_days: dates.length,
  final_total_return_eur: 2180.42, final_total_return_pct: 3.5 };
const twr = { dates, twr_index: dates.map((_, i) => 100 + i * .16), regimes: dates.map(() => 'official'),
  values_eur: navHistory.nav_total_eur, flows_eur: dates.map(() => 0), metrics: { twr_total_pct: 3.4,
    twr_annualized_pct: 12.1, max_drawdown_pct: -4.8, current_drawdown_pct: -1.2, vol_annual_pct: 12.3,
    sharpe: 1.57, risk_free_used: .02, irr_annual_pct: 10.4, irr_basis: 'synthetic fixture' },
  reconciliation: { nav_live_eur: 76564.57, last_snapshot_date: dates.at(-1), last_snapshot_nav_eur: 76000,
    delta_pct: .74, note: 'Synthetic fixture', breach: false, tolerance_pct: 1 } };
const risk = { timestamp: stamp, nav_eur: portfolio.nav_total_eur,
  portfolio: { vol_annual_pct: 12.3, sharpe: 1.57, var_95_1d_pct: 1.23, var_99_1d_pct: 2.34,
    var_95_1d_eur: 941, var_99_1d_eur: 1792, beta_vs_spy: 1.11, max_dd_1y_pct: -8.7 },
  per_asset: {}, correlation: { tickers: positions.map(p => p.ticker), matrix: [] }, alerts: [],
  n_assets_analyzed: positions.length, skipped_tickers: [], lookback_days: 252 };
const agents = { agents: [
  { id: 'capo', name: 'Synthetic Lead', role: 'Portfolio lead', color: '#1455ff', model: 'fixture-model' },
  { id: 'macro', name: 'Synthetic Macro', role: 'Macro analyst', color: '#008f63', model: 'fixture-model' },
  { id: 'quant', name: 'Synthetic Quant', role: 'Quant analyst', color: '#8b5cf6', model: 'fixture-model' },
], engines: { committee_r1_r2: 'synthetic-engine', chat: 'fixture-model' } };
const live = { running: false, heartbeat: 'ok', specialist_status: { capo: 'done', macro: 'done', quant: 'done' }, usage_total: { cost_eur: null } };

const overview = { indici: [{ ticker: 'SPY', name: 'S&P 500', price: 500, change_pct: .5 },
  { ticker: 'QQQ', name: 'Nasdaq 100', price: 400, change_pct: .7 }, { ticker: '^VIX', name: 'VIX', price: 17, change_pct: -.2 }],
  obbligazioni: [{ ticker: '^TNX', name: '10Y Treasury', price: 4, change_pct: .1 }],
  commodities: [{ ticker: 'GC=F', name: 'Gold', price: 2600, change_pct: .2 }],
  valute: [{ ticker: 'EURUSD=X', name: 'EUR/USD', price: 1.1, change_pct: .1 }, { ticker: 'BTC-USD', name: 'Bitcoin', price: 60000, change_pct: .4 }] };
const fx = { rates: { EUR: 1, USD: .92, GBP: 1.18, CHF: 1.04 } };
const memos = Array.from({ length: 5 }, (_, i) => ({ id: 300 + i, timestamp: `2026-09-${29 - i}T08:00:00Z`,
  created_at: `2026-09-${29 - i}T08:00:00Z`, title: `Synthetic committee memo ${i + 1}`,
  ticker: positions[i].ticker, summary: 'Synthetic fixture memo, no real portfolio or model data.',
  content: `## Synthetic memo ${i + 1}\n\nFixture-only analysis for ${positions[i].ticker}.`,
  action: 'HOLD', status: 'complete', output_language: 'en', raw_markdown: null }));
const tasks = [
  { TaskName: 'Bellomberg-NightlyBackup', State: 'Ready', LastTaskResult: 0,
    LastRunTime: '09/28/2026 23:00:01', NextRunTime: '09/29/2026 23:00:01' },
  { TaskName: 'Bellomberg-NewsRefresh', State: 'Ready', LastTaskResult: 0,
    LastRunTime: '09/29/2026 08:00:01', NextRunTime: '09/29/2026 12:00:01' },
  { TaskName: 'Bellomberg-DisabledFixture', State: 'Disabled', LastTaskResult: 15,
    LastRunTime: '09/27/2026 23:00:01', NextRunTime: '09/29/2026 23:00:01' },
];
const backups = Array.from({ length: 8 }, (_, i) => ({ filename: i === 0 ? 'bellomberg_backup_2026-09-28.zip' : `fixture_manual_${i}.zip`,
  path: `fixture/backups/${i === 0 ? 'bellomberg_backup_2026-09-28.zip' : `fixture_manual_${i}.zip`}`,
  size_mb: 12.4 + i * .7, created: `2026-09-${String(29 - i).padStart(2, '0')}T23:00:00Z` }));
const journalEntries = Array.from({ length: 4 }, (_, i) => {
  const id = 910 + i, updated = `2026-09-${String(29 - i).padStart(2, '0')}T08:15:00Z`;
  const entry = { id, origin: 'user', kind: i === 1 ? 'macro' : 'thesis', ticker: i === 1 ? null : positions[i].ticker,
    title: i === 0 ? 'Synthetic thesis: durable margins' : i === 1 ? 'Synthetic macro: rates normalization' : `Synthetic research note ${i + 1}`,
    body: `## Fixture-only thesis\n\n**Idea**\nSynthetic evidence for ${positions[i].ticker}.\n\n**Risks**\nNo external or portfolio data is represented.\n\n**What would change the view**\nA future synthetic fixture revision.`,
    created_at: `2026-09-${String(27 - i).padStart(2, '0')}T08:15:00Z`, updated_at: updated,
    version: i === 0 ? 3 : 1, archived_at: i === 3 ? `2026-09-${String(29 - i).padStart(2, '0')}T09:00:00Z` : null };
  return { ...entry, excerpt: entry.body.replace(/[#*\n]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 150) };
});
const journalVersions = [
  { entry_id: 910, version: 1, origin: 'user', kind: 'thesis', ticker: 'SYN1', title: 'Synthetic thesis: initial view',
    body: '## Fixture-only thesis\n\nInitial synthetic view.\n\nEvidence and risks are synthetic.',
    created_at: '2026-09-27T08:15:00Z', saved_at: '2026-09-27T08:15:00Z', archived_at: null, action: 'create' },
  { entry_id: 910, version: 2, origin: 'user', kind: 'thesis', ticker: 'SYN1', title: 'Synthetic thesis: margin review',
    body: '## Fixture-only thesis\n\nUpdated synthetic evidence.\n\nRisks remain fixture-only.',
    created_at: '2026-09-27T08:15:00Z', saved_at: '2026-09-28T08:15:00Z', archived_at: null, action: 'update' },
  { entry_id: 910, version: 3, origin: 'user', kind: 'thesis', ticker: 'SYN1', title: 'Synthetic thesis: durable margins',
    body: journalEntries[0].body, created_at: '2026-09-27T08:15:00Z', saved_at: '2026-09-29T08:15:00Z', archived_at: null, action: 'update' },
];
const trades = [
  { id: 501, ticker: 'SYN1', action: 'BUY', data: '2026-09-12T10:30:00', quantita: 4, prezzo: 91.25, valuta: 'EUR',
    created_at: '2026-09-12T10:30:05Z', note: 'Synthetic starter position', pm_rationale: 'Fixture-only reason',
    linked_decision_id: 700, link_origin: 'explicit', ora_convenzionale: false, realized_eur: null, realized_local: null },
  { id: 502, ticker: 'SYN2', action: 'BUY', data: '2026-09-15T12:00:00', quantita: 10, prezzo: 48.60, valuta: 'USD',
    created_at: '2026-09-15T12:01:00Z', note: 'Synthetic USD position', pm_rationale: 'Fixture-only reason',
    linked_decision_id: null, link_origin: 'none', ora_convenzionale: true, realized_eur: null, realized_local: null },
  { id: 503, ticker: 'SYN1', action: 'ADD', data: '2026-09-20T14:15:00', quantita: 2, prezzo: 94.10, valuta: 'EUR',
    created_at: '2026-09-20T14:15:05Z', note: 'Synthetic follow-up', pm_rationale: 'Fixture-only reason',
    linked_decision_id: 701, link_origin: 'explicit', ora_convenzionale: false, realized_eur: null, realized_local: null },
  { id: 504, ticker: 'SYN3', action: 'TRIM', data: '2026-09-24T11:45:00', quantita: 1, prezzo: 112.75, valuta: 'EUR',
    created_at: '2026-09-24T11:45:05Z', note: 'Synthetic partial close', pm_rationale: null,
    linked_decision_id: null, link_origin: 'none', ora_convenzionale: false, realized_eur: 125, realized_local: 125 },
];
const openingRows = [{ id: 81, ticker: 'SYN4', nome: 'Synthetic opening holding', quantita: 3,
  prezzo_medio: 74.2, valuta: 'EUR', as_of: '2026-09-01', precisione_data: 'day',
  provenienza: 'Fixture opening balance', nota: 'Synthetic starting balance only', created_at: '2026-09-29T08:00:00Z' }];
const mandateValues = clone(mandatoExample._esempio);
mandateValues.profilo.broker = 'Fixture broker';
mandateValues.profilo.residenza_fiscale = 'Synthetic jurisdiction';
mandateValues.note.note_per_il_comitato = 'Synthetic fixture values for UI verification only.';
mandateValues.note.aree_gradite = ['Synthetic technology', 'Synthetic healthcare'];
mandateValues.note.esclusioni = ['Synthetic excluded issuer'];
const mandato = { dichiarato: true, causa: null, dettaglio: null, campi_mancanti: [], valori: mandateValues,
  origine: 'personalizzato', impronta: 'synthetic-fixture-mandate-v1', dichiarato_il: stamp,
  campi: clone(mandatoExample._campi), errori: [], esempio: clone(mandatoExample._esempio) };

const dataFor = (method, route, url) => {
  if (route === '/__fixture') return { ok: true };
  if (route === '/health') return { status: 'ok', version: 'synthetic-fixture', brand: 'Bellomberg fixture' };
  if (route === '/auth/status') return { configured: true, default_pin: false };
  if (route === '/preferences') return { language: 'en', selected: true, source: 'preferences' };
  if (route === '/mandato') return clone(mandato);
  if (route === '/fx') return fx;
  if (route === '/portfolio') return portfolio;
  if (route === '/decisions') return { decisions };
  if (route === '/portfolio/risk') return risk;
  if (route === '/portfolio/analytics/nav_history') return navHistory;
  if (route === '/portfolio/analytics/twr') return twr;
  if (route === '/portfolio/gap_days') return { error: 'Synthetic fixture gap-days unavailable', days: {}, unpriced: [] };
  if (route === '/portfolio/metrics/advanced') return { benchmark: 'SPY', metrics: { cagr_pct: 8.4, volatility_pct: 12.3, sharpe: 1.57, beta: 1.11, alpha_pct: 2.1 }, timestamp: stamp };
  if (route === '/portfolio/analytics/drawdowns') return { drawdowns: [{ start: dates[10], trough: dates[13], end: dates[16], depth_pct: -4.8, recovery_days: 3 }], max_drawdown_pct: -4.8, current_drawdown_pct: -1.2, timestamp: stamp };
  // Performance Rischio reads TWR drawdown episodes from the tearsheet (portfolio_tearsheet._drawdown_episodes shape).
  if (route === '/portfolio/tearsheet') return { drawdowns: { top: [{ start_date: dates[10], trough_date: dates[13], depth_pct: -4.8, days_to_trough: 3,
    recovery_date: dates[16], days_total: 6, open: false }], current: null, n_episodes_total: 1 }, timestamp: stamp };
  if (route === '/portfolio/analytics/liquidity') return { items: positions.map(p => ({ ticker: p.ticker, adv_eur: 1250000, position_eur: p.valore_mercato, days_to_liquidate: .4, participation_pct: .7, quality: 'fixture' })), timestamp: stamp };
  if (route === '/portfolio/analytics/concentration') return { by_ticker: positions.map(p => ({ ticker: p.ticker, weight_pct: p.peso_pct })), by_sector: [{ sector: 'Synthetic technology', weight_pct: 37.5 }], hhi: .13, timestamp: stamp };
  if (route === '/portfolio/analytics/var_contribution') return { ok: true, items: positions.slice(0, 5).map(p => ({ ticker: p.ticker, weight_pct: p.peso_pct, component_var_pct: .2, component_var_eur: 120, contribution_pct_of_total_var: 15, marginal_var_pct_per_1pct_weight: .1 })), portfolio_var_pct_daily: 1.23, portfolio_var_eur_daily: 941, portfolio_vol_annual_pct: 12.3, confidence_level: .95, lookback_days: 252, n_assets: positions.length, methodology: 'synthetic fixture' };
  if (route === '/portfolio/analytics/benchmark') return { ticker: url.searchParams.get('ticker') || 'SPY', dates, values: dates.map((_, i) => 100 + i * .12), source: 'synthetic fixture' };
  if (route === '/portfolio/attribution') return { period: url.searchParams.get('period') || 'YTD', items: positions.map(p => ({ ticker: p.ticker, contribution_pct: p.pl_pct, weight_pct: p.peso_pct })), total_return_pct: 3.5, timestamp: stamp };
  if (route === '/portfolio/factors') return { status: 'unavailable', reason: 'Synthetic factor data not supplied', items: [], period: url.searchParams.get('period') || '1y' };
  if (route === '/portfolio/metrics/beta_reconcile') return { betas: {}, definitions: {}, sources_failed: { fixture: 'No real provider called' }, threshold: .2, max_spread: 0, verdict: 'incomplete', beta_consensus: null };
  if (route === '/portfolio/montecarlo' || route === '/portfolio/montecarlo/v3') {
    const days = Array.from({ length: 13 }, (_, i) => Math.round(i * 21));
    const fan_bands = { days, p5: [], p10: [], p25: [], p50: [], p75: [], p90: [], p95: [] };
    for (let i = 0; i < days.length; i++) {
      const mid = portfolio.nav_total_eur * (1 + i * .003);
      for (const [key, spread] of [['p5', .25], ['p10', .18], ['p25', .1], ['p50', 0], ['p75', .11], ['p90', .2], ['p95', .28]]) fan_bands[key].push(mid * (1 + spread * (key === 'p5' || key === 'p10' || key === 'p25' ? -i / 12 : i / 12)));
    }
    return { timestamp: stamp, version: 'synthetic-fixture-v1', method: 'parametric_t', method_description: 'Synthetic Student t fixture',
      drift_mode: 'shrinkage', stress_scenario: 'none', stress_requested: 'none', stress_fallback: false, calibration_note: 'Synthetic only',
      returns_basis: 'synthetic_fixture', lookback_years: 3, lookback_days_calibration: 756, n_sims: 1000, horizon_days: 252,
      horizon_years: 1, n_assets: positions.length, tickers_analyzed: positions.map(p => p.ticker), removed_tickers: [], added_tickers: [],
      weights: Object.fromEntries(positions.map(p => [p.ticker, p.peso_pct / 100])), base_nav_eur: portfolio.nav_total_eur,
      percentiles_ratio: { p1: .72, p5: .84, p10: .89, p25: .96, p50: 1.04, p75: 1.11, p90: 1.17, p95: 1.22, p99: 1.31 },
      percentiles_eur: { p1: 55126, p5: 64314, p10: 68143, p25: 73502, p50: 79627, p75: 85067, p90: 89580, p95: 93408, p99: 100300 },
      expected_return_pct: 4.1, median_return_pct: 4, stdev_pct: 12.8, sharpe_simulated: .32,
      prob_negative_pct: 28, prob_loss_10pct: 20, prob_loss_20pct: 5, prob_gain_10pct: 29, prob_gain_20pct: 8,
      var_95_pct: 17, var_99_pct: 28, es_95_pct: 21, es_99_pct: 33, es_95_eur: 16079, es_99_eur: 25266,
      max_drawdown_p5_pct: -25, max_drawdown_median_pct: -10, max_drawdown_p95_pct: -3,
      sample_paths: Array.from({ length: 14 }, (_, pathIndex) => days.map((_, i) => portfolio.nav_total_eur * (1 + i * .003 + Math.sin((i + pathIndex) * .52) * .018))),
      sample_paths_days: days, sample_paths_n: 14, fan_bands,
      terminal_hist: { counts: [3, 8, 19, 39, 72, 116, 180, 213, 177, 103, 49, 15, 6], edges_eur: Array.from({ length: 14 }, (_, i) => 52000 + i * 4000) },
      modifications_applied: [], skipped_modifications: [] };
  }
  if (route === '/portfolio/validate_ticker') return { symbol: url.searchParams.get('symbol') || 'SYN1', valid: true, name: 'Synthetic instrument', currency: 'EUR' };
  if (route === '/agents/list') return agents;
  if (route === '/agents/live') return live;
  if (route === '/agents/progress') return { source: 'synthetic fixture', paid_analysis: false,
    history: { state: 'available', count: 3, available: true, first_captured_at: stamp, note: 'Fixture only' }, trend: { available: true, reason: null },
    agents: [{ id: 'capo', label: 'Synthetic Lead', role: 'Portfolio lead', attribution: 'collective', delta: { available: true, hit_rate_pp: 2.5, reason: 'fixture' },
      latest: { run_id: 'fixture-1', memo_id: 300, completed_at: stamp, computed_at: stamp, n: 12, hits: 8, hit_rate_pct: 66.7, avg_edge_pct: 2.2, ci95: { low_pct: 40, high_pct: 88 }, quality: ['ok'], comparison_key: 'fixture', window_days: 28, maturation_days: 7, source: 'fixture', n_fetch_fail: 0, n_unmeasurable: 0, n_directional_candidates: 12, operational: { status: 'complete', models: ['fixture-model'], usage: { cost_eur: 0, partial: false, duration_s: 2, api_calls: 1, status: 'synthetic', tokens_status: 'complete' } }, delta: { available: true, hit_rate_pp: 2.5, reason: 'fixture' } }, series: [] }],
    runs: [], current_scorecard: { available: true, stato: 'ready', error: null, computed_at: stamp },
    method: { score: 'synthetic', attribution: 'synthetic', horizon: 'fixture', comparison: 'fixture', learning: 'fixture', timing: 'fixture' } };
  if (route === '/market/overview') return overview;
  if (route === '/market/movers') return { azioni: [
    { ticker: 'SYN1', name: 'Synthetic holding 1', country: 'US', price: 105, change_pct: 2.4 },
    { ticker: 'SYN2', name: 'Synthetic holding 2', country: 'IT', price: 48, change_pct: -1.8 },
    { ticker: 'SYN3', name: 'Synthetic holding 3', country: 'DE', price: 72, change_pct: 0.6 }] };
  if (route === '/market/ohlc') return { ticker: url.searchParams.get('ticker'), period: url.searchParams.get('period'), interval: url.searchParams.get('interval'), bars };
  if (route === '/market/quote') return { ticker: url.searchParams.get('ticker') || 'SYN1', name: 'Synthetic holding', exchange: 'FIX', price: 105, prev_close: 104, change_pct: .96, currency: 'EUR', sector: 'Synthetic technology', industry: 'Fixture', market_cap: 4_200_000_000, pe: 18.4, fwd_pe: 16.2, eps: 5.7, div_yield: .018, beta: 1.08, high_52w: 128, low_52w: 82, volume: 1_450_000, avg_volume: 1_220_000, target_mean: 121, recommendation: 'synthetic_hold', short_pct_float: .021, summary: 'Synthetic company profile used only to verify layout and data states.', ev: 4_480_000_000, ev_ebitda: 10.5, ev_sales: 2.8, peg: 1.6, pb: 3.2, fcf: 235_000_000 };
  if (route === '/market/search') return { results: [{ ticker: 'SYN1', name: 'Synthetic holding 1', exchange: 'FIX', currency: 'EUR', type: 'Equity' }] };
  if (route === '/market/news') return { ticker: url.searchParams.get('ticker') || 'SYN1', items: wireItems.slice(0, 2).map(item => ({ title: item.title, link: item.url, publisher: item.source, published: item.published_at })) };
  if (route === '/market/financials') return { ticker: url.searchParams.get('ticker') || 'SYN1', statements: {
    income: { years: ['2025', '2024', '2023'], rows: { Revenue: [1_520_000_000, 1_390_000_000, 1_250_000_000], 'Gross Profit': [684_000_000, 612_000_000, 525_000_000], 'Operating Income': [242_000_000, 211_000_000, 176_000_000], 'Net Income': [181_000_000, 158_000_000, 129_000_000], 'Diluted EPS': [5.7, 4.9, 4.1] } },
    balance: { years: ['2025', '2024', '2023'], rows: { 'Total Assets': [2_140_000_000, 1_980_000_000, 1_810_000_000], 'Total Debt': [310_000_000, 335_000_000, 360_000_000], 'Cash And Cash Equivalents': [420_000_000, 385_000_000, 344_000_000] } },
    cashflow: { years: ['2025', '2024', '2023'], rows: { 'Operating Cash Flow': [284_000_000, 252_000_000, 216_000_000], 'Capital Expenditure': [-49_000_000, -44_000_000, -39_000_000], 'Free Cash Flow': [235_000_000, 208_000_000, 177_000_000] } },
  } };
  if (route === '/market/holders') return { ticker: url.searchParams.get('ticker') || 'SYN1', major: [
    { label: 'insidersPercentHeld', value: .084 }, { label: 'institutionsPercentHeld', value: .647 }, { label: 'institutionsFloatPercentHeld', value: .713 }, { label: 'institutionsCount', value: 312 },
  ], institutional: [{ holder: 'Synthetic Index Partners', shares: 1_240_000, pctheld: .032, value: 130_200_000, date_reported: '2026-06-30' }, { holder: 'Fixture Capital Group', shares: 780_000, pctheld: .020, value: 81_900_000, date_reported: '2026-06-30' }] };
  if (route === '/filings') return filingOverview(url.searchParams.get('ambito') === 'preferiti' ? 'preferiti' : 'portafoglio');
  if (route === '/filings/novita') return { n: 0, tickers: [] };
  const filingSub = route.match(/^\/filings\/([^/]+)\/(context-preview|proposal|ai-estimate|ai-proposal)$/);
  if (filingSub && filingSub[1] !== 'runs') {
    const ticker = decodeURIComponent(filingSub[1]);
    if (filingSub[2] === 'context-preview') return filingPreview(ticker);
    if (filingSub[2] === 'proposal') return filingProposal(ticker);
    if (filingSub[2] === 'ai-estimate') return filingAiEstimate(ticker, url.searchParams.get('url') || '');
    return filingAiSaved(ticker);
  }
  const filingTicker = route.match(/^\/filings\/([^/]+)$/);
  if (filingTicker) {
    const ticker = decodeURIComponent(filingTicker[1]);
    if (ticker === 'SYN1') return { ticker, status: 'available', reason: 'Synthetic fixture archive only.', profile: { ticker, profile: { identity: { ticker: 'SYN1', fixture: true }, source: { name: 'Synthetic archive', url: 'https://example.invalid' }, sections: ['Synthetic segment note', 'Synthetic outlook'] }, version: 2, enabled: false, interval_hours: 168, qualitative_enabled: false, next_due_at: newsAt(-120) }, runs: clone(filingRuns), active_run: null, ultimo_completo: clone(filingRuns[0]) };
    return { ticker, status: 'unavailable', reason: 'No synthetic filing fixture for this ticker.', profile: null, runs: [], active_run: null };
  }
  const filingRunMatch = route.match(/^\/filings\/runs\/(\d+)$/);
  if (filingRunMatch) {
    const id = Number(filingRunMatch[1]), run = filingRuns.find(item => item.id === id);
    if (!run) return { detail: 'Synthetic fixture run not found.' };
    return { ...clone(run), result: clone(filingResult), judgment: { status: 'synthetic_fixture', findings: [{ category: 'outlook', assessment: 'The fixture current text includes a changed forward-looking statement.', citations: ['C1-prima', 'C1-dopo'] }], reason: 'No LLM or real filing was used.', model: 'fixture-only', usage: { cost_eur: 0 }, coverage: { shown: 1, total: 1 }, citations_available: [] }, index: { status: 'available', reason: 'Synthetic citation index only.' } };
  }
  if (route === '/favorites') return { favorites: positions.slice(0, 3).map((p, i) => ({ ticker: p.ticker, name: p.nome, sector: 'Synthetic technology', industry: 'Fixture', note: i === 0 ? 'Track synthetic thesis' : '' })) };
  if (route === '/fundamentals/models') return { count: fundamentalsModels.length, models: clone(fundamentalsModels), nota: 'Synthetic fixture: no real issuer data, model run, or workbook.' };
  if (route === '/valuation/models/SYN1/variants') return { variants: clone(personalVariants) };
  if (route === '/news/feed') return { count: wireItems.length, items: clone(wireItems), timestamp: syntheticNow(), fonti_mute: null, avviso: null };
  if (route === '/news/providers') return { providers_contingentati: [], timestamp: syntheticNow(), ultimo_giro: { stato: 'ok', timestamp: newsAt(4 / 60), age_minutes: 4, fetched: 12, classified: 12, classification_attempted: 12, classification_failed: 0, saved: 8, skipped_duplicates: 4, providers_blocked: {} }, refresh_job: null, fonti_mute: null, avviso: null, nota: 'Synthetic provider status only.' };
  if (route === '/news/macro') return { count: 2, items: [
    { title: 'Synthetic central bank path unchanged', snippet: 'Fixture-only macro note.', url: null, provider: 'fixture-macro', source: 'Fixture Macro', published_at: newsAt(3), topic_id: 'rates', topic_label: 'Rates', topic_category: 'rates', topic_importance: 4, tickers_affected: ['SYN1', 'SYN2'] },
    { title: 'Synthetic inflation expectations ease', snippet: 'Fictional data for the desk view.', url: null, provider: 'fixture-macro', source: 'Fixture Macro', published_at: newsAt(9), topic_id: 'inflation', topic_label: 'Inflation', topic_category: 'inflation', topic_importance: 3, tickers_affected: ['SYN3'] },
  ], timestamp: syntheticNow(), categories_requested: null, fonti_mute: null, avviso: null };
  if (route === '/news/corporate-events') return { count: 1, items: [{ title: 'Synthetic issuer investor briefing', title_origin: 'Synthetic issuer investor briefing', snippet: 'Fixture-only event with no real company or schedule.', snippet_origin: 'Synthetic source excerpt.', presentation_languages: ['en'], url: null, provider: 'fixture-calendar', published_at: newsAt(5), ticker_mentioned: 'SYN1', event_type: 'investor_meeting', source_type: 'fixture', topic_importance: 4, metadata: { fixture: true } }], timestamp: syntheticNow(), categories_requested: null, fonti_mute: null, avviso: null };
  if (route === '/news/top-global') return { count: 2, items: [
    { title: 'Synthetic global markets open mixed', snippet: 'Fixture-only global headline.', url: null, provider: 'fixture-global', source: 'Fixture Global', published_at: newsAt(1) },
    { title: 'Synthetic commodity outlook revised', snippet: 'Invented global desk item.', url: null, provider: 'fixture-global', source: 'Fixture Global', published_at: newsAt(6) },
  ], timestamp: syntheticNow(), fonti_mute: null, avviso: null };
  if (route === '/news/economic-calendar') return { count: 2, items: [
    { date: newsAt(-18).slice(0, 10), time: '12:30', type: 'indicator', title: 'Synthetic CPI release', importance: 3, country: 'US', previous: '2.4', estimate: '2.3', actual: '2.2', unit: '%' },
    { date: newsAt(-36).slice(0, 10), time: '09:00', type: 'meeting', title: 'Synthetic central bank minutes', importance: 2, country: 'EU', previous: null, estimate: null, actual: null, unit: null },
  ], timestamp: syntheticNow(), categories_requested: null, fonti_mute: null, avviso: null };
  if (route === '/news/briefing/current') return clone(briefing);
  if (route === '/news/briefing/refresh') return { error: 'Synthetic fixture refresh disabled.' };
  if (route === '/news/topics') return { categories: {}, topics: [] };
  if (route === '/news/alerts/unnotified') return { count: 0, items: [] };
  if (route === '/market/logos') return { logos: Object.fromEntries((url.searchParams.get('tickers') || '').split(',').filter(Boolean).map(t => [t, null])), motivi: {} };
  if (route === '/tasks/scheduled') return { tasks };
  if (route === '/db/backups') return { backups, count: backups.length };
  if (route === '/memos' || route.startsWith('/memos/search/')) return { memos: route.includes('search') ? [] : memos };
  if (/^\/memos\/\d+$/.test(route)) return memos.find(m => m.id === Number(route.split('/').at(-1))) || memos[0];
  if (route === '/trades') return { count: trades.length, trades: clone(trades) };
  if (route === '/cash/movements') return { count: 2, movements: [
    { id: 1, date: '2026-09-20', type: 'DEPOSIT', amount_eur: 5000, note: 'Fixture funding', created_at: stamp },
    { id: 2, date: '2026-09-25', type: 'WITHDRAWAL', amount_eur: 500, note: 'Fixture withdrawal', created_at: stamp }] };
  if (route === '/positions/opening') return { openings: clone(openingRows) };
  if (route === '/signals/edge_scan') return { signals: [
    { ticker: 'SYN1', category: 'momentum', name: 'Synthetic trend signal', value: 1.42, context: 'Synthetic fixture only', direction: 'bullish', strength: 78, reading: 'Positive synthetic momentum', source: 'fixture' },
    { ticker: 'SYN2', category: 'risk', name: 'Synthetic concentration flag', value: 1.1, context: 'Synthetic fixture only', direction: 'caution', strength: 61, reading: 'Fixture risk marker', source: 'fixture' }],
    n_signals_total: 2, n_signals_strong: 2, by_category: { momentum: 1, risk: 1 }, generated: stamp,
    cache: { attiva: true, servita_da_cache: false, ttl_s: 3600, ttl_motivo: null },
    copertura: { posizioni_totali: positions.length, posizioni_scansionate: positions.length, scansione_piena: positions.map(p => p.ticker),
      scansione_degradata: {}, solo_prezzo: [], nessuna_misura: {}, non_scansionate: [], fattoriali_su: positions.length, fattoriali_ko: null, nota: 'Synthetic fixture coverage.' } };
  if (route === '/journal') {
    const status = url.searchParams.get('status') || 'active', kind = url.searchParams.get('kind') || '';
    const query = (url.searchParams.get('query') || '').toLowerCase();
    const filtered = journalEntries.filter(entry => (status === 'all' || (status === 'archived' ? !!entry.archived_at : !entry.archived_at))
      && (!kind || entry.kind === kind) && (!query || `${entry.title} ${entry.ticker || ''} ${entry.excerpt}`.toLowerCase().includes(query)));
    const offset = Number(url.searchParams.get('offset') || 0), limit = Number(url.searchParams.get('limit') || 30);
    return { items: filtered.slice(offset, offset + limit), total: filtered.length, limit, offset };
  }
  const journalVersionsMatch = route.match(/^\/journal\/(\d+)\/versions$/);
  if (journalVersionsMatch) return { items: Number(journalVersionsMatch[1]) === 910 ? journalVersions : [],
    total: Number(journalVersionsMatch[1]) === 910 ? journalVersions.length : 0,
    limit: Number(url.searchParams.get('limit') || 20), offset: Number(url.searchParams.get('offset') || 0) };
  const journalEntryMatch = route.match(/^\/journal\/(\d+)$/);
  if (journalEntryMatch) {
    const entry = journalEntries.find(item => item.id === Number(journalEntryMatch[1]));
    if (!entry) return { detail: 'Synthetic journal entry not found' };
    const { excerpt: _excerpt, ...detail } = entry;
    return detail;
  }
  if (route === '/market/overview') return overview;
  return undefined;
};

function startFixture(root, options = {}) {
  const requests = [];
  const unexpectedGets = [];
  const unexpectedWrites = [];
  const writes = [];
  const reads = new Map(Object.entries(options.reads || {}));
  const writeHandlers = new Map(Object.entries(options.writes || {}));
  const readConfig = value => value && typeof value === 'object' && Object.hasOwn(value, 'body')
    ? { body: value.body, status: value.status || 200, delayMs: Math.max(0, Number(value.delayMs) || 0),
      headers: value.headers || {}, contentType: value.contentType || null }
    : { body: value, status: 200, delayMs: 0, headers: {}, contentType: null };
  const stateSnapshot = () => ({ requests: requests.map(item => ({ ...item })), unexpectedGets: [...unexpectedGets],
    unexpectedWrites: unexpectedWrites.map(item => ({ ...item })),
    writes: writes.map(item => ({ ...item })), readOverrides: [...reads.keys()], writeOverrides: [...writeHandlers.keys()] });
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    const route = url.pathname;
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type,X-BB-Token,X-BB-Language');
    res.setHeader('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS');
    res.setHeader('Access-Control-Expose-Headers', 'X-Valuation-Generation,X-Valuation-Copy,Content-Disposition');
    if (req.method === 'OPTIONS') { res.statusCode = 204; res.end(); return; }
    let raw = '';
    for await (const chunk of req) raw += chunk;
    let input = null;
    try { input = raw ? JSON.parse(raw) : null; } catch { input = { invalid_json: true }; }
    if (route === '/__fixture' && req.method === 'POST') {
      if (input?.clearAll) { reads.clear(); writeHandlers.clear(); }
      if (Array.isArray(input?.clearReads)) for (const key of input.clearReads) reads.delete(String(key));
      if (input?.setRead && typeof input.setRead === 'object') for (const [key, value] of Object.entries(input.setRead)) reads.set(key, value);
      if (input?.setWrite && typeof input.setWrite === 'object') for (const [key, value] of Object.entries(input.setWrite)) writeHandlers.set(key, value);
      res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(stateSnapshot())); return;
    }
    if (route !== '/__fixture') requests.push({ method: req.method, route, query: Object.fromEntries(url.searchParams), input,
      language: req.headers['x-bb-language'] || null });
    if (req.method !== 'GET') {
      writes.push({ method: req.method, route });
      res.setHeader('Content-Type', 'application/json');
      if (route === '/prices/update') { res.end(JSON.stringify({ ok: true, source: 'synthetic fixture' })); return; }
      if (route === '/news/alerts/mark-notified') { res.end(JSON.stringify({ updated: 0 })); return; }
      if (writeHandlers.has(route)) {
        const configured = writeHandlers.get(route);
        const reply = typeof configured === 'function' ? await configured({ method: req.method, route, query: url.searchParams, input }) : configured;
        const normalized = readConfig(reply);
        if (normalized.delayMs) await new Promise(resolve => setTimeout(resolve, normalized.delayMs));
        for (const [name, value] of Object.entries(normalized.headers)) res.setHeader(name, value);
        if (normalized.contentType) res.setHeader('Content-Type', normalized.contentType);
        res.statusCode = normalized.status;
        res.end(typeof normalized.body === 'string' || Buffer.isBuffer(normalized.body)
          ? normalized.body : JSON.stringify(normalized.body)); return;
      }
      unexpectedWrites.push({ method: req.method, route, status: 403 });
      res.statusCode = 403;
      res.end(JSON.stringify({ detail: 'Blocked synthetic fixture write; no mutation was applied.' }));
      return;
    }
    if (route === '/__fixture') {
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify(stateSnapshot())); return;
    }
    if (route === '/' && root) {
      let html = fs.readFileSync(path.join(root, options.distDir || 'dist', 'index.html'), 'utf8');
      html = html.replace(/<link rel="preconnect" href="https:\/\/fonts\.googleapis\.com">\s*/g, '')
        .replace(/<link rel="preconnect" href="https:\/\/fonts\.gstatic\.com"[^>]*>\s*/g, '')
        .replace(/<link href="https:\/\/fonts\.googleapis\.com\/css[^\"]*" rel="stylesheet">\s*/g, '');
      res.setHeader('Content-Type', 'text/html; charset=utf-8'); res.end(html); return;
    }
    if ((route.startsWith('/assets/') || route.startsWith('/vendor/')) && root) {
      const distRoot = path.resolve(root, options.distDir || 'dist');
      const assetPath = path.resolve(distRoot, decodeURIComponent(route.slice(1)));
      if (!assetPath.startsWith(distRoot + path.sep)) { res.statusCode = 403; res.end('Forbidden'); return; }
      let asset;
      try { asset = fs.readFileSync(assetPath); } catch { res.statusCode = 404; res.end('Not found'); return; }
      const ext = path.extname(assetPath);
      res.setHeader('Content-Type', ext === '.js' ? 'text/javascript; charset=utf-8' : ext === '.css' ? 'text/css; charset=utf-8'
        : ext === '.svg' ? 'image/svg+xml' : ext === '.woff2' ? 'font/woff2' : 'application/octet-stream');
      res.setHeader('Cache-Control', 'no-store'); res.end(asset); return;
    }
    const configuredRead = reads.has(route) ? readConfig(reads.get(route)) : null;
    if (configuredRead?.delayMs) await new Promise(resolve => setTimeout(resolve, configuredRead.delayMs));
    const body = configuredRead ? configuredRead.body : dataFor(req.method, route, url);
    if (body === undefined) {
      unexpectedGets.push(route);
      res.statusCode = 404;
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify({ detail: `No synthetic fixture for GET ${route}` })); return;
    }
    res.statusCode = configuredRead?.status || 200;
    res.setHeader('Content-Type', configuredRead?.contentType || 'application/json; charset=utf-8');
    for (const [name, value] of Object.entries(configuredRead?.headers || {})) res.setHeader(name, value);
    res.setHeader('Cache-Control', 'no-store');
    res.end(typeof body === 'string' || Buffer.isBuffer(body) ? body : JSON.stringify(body));
  });
  return { server, requests, unexpectedGets, unexpectedWrites, writes, reads, writeHandlers, stateSnapshot };
}

module.exports = { startFixture, fixtureData: { positions, portfolio, decisions, navHistory, twr, risk, agents, live,
  overview, fx, memos, tasks, backups, trades, openingRows, mandato, journalEntries, journalVersions,
  bars, wireItems, briefing, fundamentalsModels, personalVariants, filingRuns, filingResult,
  filingOverview, filingAiSections } };
