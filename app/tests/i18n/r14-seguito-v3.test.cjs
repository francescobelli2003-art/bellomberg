// R14 seguito v3 (Opus 5.5, 10/10/2026): contratto backend 3583a78.
//  B2  GET /decisions -> `trade_idea_storage` (provenienza Trade Idea non leggibile, anche `lookup_failed`)
//  B4  POST /consigliere/run -> `trade_idea_active_check` (controllo «Trade Idea attiva» non eseguito)
// Funzioni e pagine VERE, dizionari VERI; si stubbano rete, router e il dialogo di conferma (se ne
// leggono le props e si chiama onConfirm, come farebbe il PM).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const JSX = require('react/jsx-runtime');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');
ambienteBrowser();
globalThis.window = globalThis.window || {};
globalThis.window.confirm = () => true;

const storage = status => ({ status, error_code: 'trade_idea_' + status,
  update_required: status === 'schema_absent' || status === 'schema_partial',
  action: { db_missing: 'check_database_path', schema_partial: 'run_explicit_migration', schema_absent: 'run_explicit_migration',
    schema_incompatible: 'inspect_schema', db_unreadable: 'check_database_access' }[status],
  documentation: 'docs/TRADE_IDEA.md#aggiornamento-storage' });
// forma ESATTA del backend (bellomberg_api.py, ramo except Exception di GET /decisions)
const LOOKUP_FAILED = { status: 'lookup_failed', error_code: 'trade_idea_lookup_failed', update_required: false,
  action: 'check_database_access', documentation: 'docs/TRADE_IDEA.md#aggiornamento-storage' };
const MIGRATE = { it: /migrazione esplicita/, en: /explicit migration/ };
const ACCESS = { it: /blocchi di altri programmi/, en: /locks held by other programs/ };

// ---------- lookup_failed: guasto d'accesso, mai migrazione ----------

const base = creaCaricatore({ stub: {
  './api': { API_BASE: 'http://synthetic.invalid', requestHeaders: () => ({}), clearSessionAndReload: () => { throw new Error('unexpected'); } },
} });
const baseLanguage = base('i18n/lingua.ts');
const { readTradeIdeaStorage } = base('lib/tradeIdeas.ts');
const Notice = base('components/TradeIdeaStorageNotice.tsx').default;
const { storageActionText } = base('components/TradeIdeaStorageNotice.tsx');
const { t: tr } = base('i18n/t.ts');

test('lookup_failed is read as a diagnosis and rendered as an access fault, never as a migration (IT/EN)', () => {
  assert.deepEqual(readTradeIdeaStorage(LOOKUP_FAILED), LOOKUP_FAILED, 'the backend shape is a valid diagnosis');
  for (const lang of ['it', 'en']) {
    baseLanguage.impostaLinguaCorrente(lang);
    const out = renderToStaticMarkup(React.createElement(Notice, { storage: LOOKUP_FAILED, lead: 'LEAD' }));
    assert.match(out, ACCESS[lang]);
    assert.match(out, /trade_idea_lookup_failed/);
    assert.match(out, /data-ti-storage="lookup_failed"/);
    assert.doesNotMatch(out, MIGRATE[lang]);
    // anche se un backend futuro accompagnasse lookup_failed con azione e flag di migrazione: accesso, mai migrazione
    const forged = { ...LOOKUP_FAILED, action: 'run_explicit_migration', update_required: true };
    assert.doesNotMatch(storageActionText(tr, forged), MIGRATE[lang]);
    assert.match(storageActionText(tr, forged), ACCESS[lang]);
    // l'ordinario resta: schema_partial propone la migrazione
    assert.match(storageActionText(tr, storage('schema_partial')), MIGRATE[lang]);
  }
});

// ---------- Decisioni: provenienza Trade Idea non leggibile ----------

const recent = new Date(Date.now() - 2 * 86400_000).toISOString().slice(0, 19);
const decision = extra => ({ id: 123, memo_id: 1, timestamp: recent, action: 'BUY', ticker: 'SYNTH.X',
  eur_amount: 0, timing: 't', confidence: '7', rationale: 'r', status: 'PENDING', pm_feedback: null,
  outcome_pct: null, outcome_eur: null, outcome_notes: null, archived: false, trade_idea: null, ...extra });

function decisioni(response) {
  let si = 0, mi = 0, ei = 0;
  const values = {}, memos = [], effects = [], deps = [];
  const api = { decisions: async () => JSON.parse(JSON.stringify(response)),
    decisionEvents: async () => ({ events: [] }), marketLogos: async () => ({ logos: {}, motivi: {} }) };
  const remember = (fn, d) => { const at = mi++, before = memos[at]; if (!before || !d || d.some((v, i) => !Object.is(v, before.d[i]))) memos[at] = { d, value: fn() }; return memos[at].value; };
  const load = creaCaricatore({ stub: {
    react: { ...React, useMemo: remember, useCallback: (fn, d) => remember(() => fn, d),
      useState(initial) { const at = si++; if (!(at in values)) values[at] = typeof initial === 'function' ? initial() : initial; return [values[at], v => { values[at] = typeof v === 'function' ? v(values[at]) : v; }]; },
      useEffect(fn, d) { const at = ei++, before = deps[at]; if (!before || !d || d.some((v, i) => !Object.is(v, before[i]))) effects.push(fn); deps[at] = d; },
    },
    '@/lib/api': { Bellomberg: api },
    'react-router-dom': { useNavigate: () => () => {} },
  } });
  const language = load('i18n/lingua.ts'), Page = load('pages/Decisions.tsx').default;
  const render = lang => { si = mi = ei = 0; effects.length = 0; language.impostaLinguaCorrente(lang); return renderToStaticMarkup(React.createElement(Page)); };
  const runEffects = async () => { for (const fn of effects.splice(0)) fn(); await new Promise(r => setImmediate(r)); };
  return async lang => { render(lang); await runEffects(); render(lang); await runEffects(); return render(lang); };
}

test('Decisions: trade_idea_storage lookup_failed -> one compact notice, access remedy, no migration, no «no Trade Idea» (IT/EN)', async () => {
  const ready = decisioni({ decisions: [decision(), decision({ id: 124, ticker: 'SYNTH.Y' })], watch_provenance_error: null,
    trade_idea_storage: LOOKUP_FAILED });
  for (const [lang, lead, none] of [['it', 'Provenienza Trade Idea non leggibile:', /nessuna Trade Idea/gi],
    ['en', 'Trade Idea provenance unreadable:', /no Trade Idea/gi]]) {
    const out = await ready(lang);
    assert.equal((out.match(/data-dc-ti-storage="lookup_failed"/g) || []).length, 1, lang + ': one notice for the page, not one per row');
    assert.ok(out.includes(lead), lang);
    assert.match(out, ACCESS[lang]);
    assert.match(out, /trade_idea_lookup_failed/);
    assert.doesNotMatch(out, MIGRATE[lang], lang + ': lookup_failed never proposes a migration');
    assert.equal((out.match(none) || []).length, 1, lang + ': «no Trade Idea» appears only inside the notice that denies it, never on a row');
    // le righe restano servite
    assert.match(out, /SYNTH\.X/); assert.match(out, /SYNTH\.Y/);
  }
});

test('Decisions: a storage diagnosis keeps its own remedy; a readable provenance shows no notice', async () => {
  const partial = await decisioni({ decisions: [decision()], trade_idea_storage: storage('schema_partial') })('it');
  assert.match(partial, /data-dc-ti-storage="schema_partial"/);
  assert.match(partial, MIGRATE.it, 'schema_partial + update_required: the explicit migration is the remedy');
  const missing = await decisioni({ decisions: [decision()], trade_idea_storage: storage('db_missing') })('en');
  assert.match(missing, /data-dc-ti-storage="db_missing"/);
  assert.doesNotMatch(missing, MIGRATE.en);
  const malformed = await decisioni({ decisions: [decision()], trade_idea_storage: { status: 7 } })('it');
  assert.match(malformed, /data-dc-ti-storage="unknown"/, 'a malformed diagnosis still declares the provenance unreadable');
  assert.doesNotMatch(malformed, MIGRATE.it);
  for (const response of [{ decisions: [decision()], trade_idea_storage: null }, { decisions: [decision()] }]) {
    const out = await decisioni(response)('it');
    assert.doesNotMatch(out, /data-dc-ti-storage|Provenienza Trade Idea non leggibile/);
  }
});

// ---------- 10/10 (Opus 5.5): collegamento sospeso e tono del riquadro ----------

test('Decisions: unreadable provenance disables «link trade» with the reason; readable provenance keeps it (IT/EN)', async () => {
  const motivo = { it: 'Provenienza Trade Idea non leggibile: collegamento sospeso finché non si legge.',
    en: 'Trade Idea provenance unreadable: linking suspended until it can be read.' };
  for (const lang of ['it', 'en']) {
    const out = await decisioni({ decisions: [decision()], trade_idea_storage: LOOKUP_FAILED })(lang);
    const btn = out.match(/<button[^>]*data-dc-azione="collega"[^>]*>/);
    assert.ok(btn, lang + ': the button stays visible');
    assert.match(btn[0], /disabled=""/, lang + ': but disabled');
    assert.ok(out.includes(motivo[lang]), lang + ': with the reason');
    assert.match(out, /data-dc-collega-sospeso="123"/);
    const ok = await decisioni({ decisions: [decision()], trade_idea_storage: null })(lang);
    const okBtn = ok.match(/<button[^>]*data-dc-azione="collega"[^>]*>/);
    assert.ok(okBtn && !/disabled/.test(okBtn[0]), lang + ': readable provenance links');
    assert.ok(!ok.includes(motivo[lang]));
  }
  // una proposta non collegabile per altri motivi (veto) non mostra il motivo della provenienza
  const veto = await decisioni({ decisions: [decision({ veto: { reason: 'r' } })], trade_idea_storage: LOOKUP_FAILED })('it');
  assert.doesNotMatch(veto, /data-dc-collega-sospeso/);
});

test('storage notice: explicit migration and db_busy are a WARNING (yellow), real faults stay red (IT/EN)', () => {
  const tono = s => {
    const out = renderToStaticMarkup(React.createElement(Notice, { storage: s, lead: 'LEAD' }));
    const cls = out.match(/^<div class="([^"]*)" role="([^"]*)"/);
    assert.ok(cls, out.slice(0, 120));
    return { cls: cls[1], role: cls[2], out };
  };
  const BUSY = { status: 'db_busy', error_code: 'trade_idea_db_busy', update_required: false, action: 'retry',
    documentation: 'docs/TRADE_IDEA.md#aggiornamento-storage' };
  const RETRY = { it: /riprova tra poco/, en: /try again shortly/ };
  for (const lang of ['it', 'en']) {
    baseLanguage.impostaLinguaCorrente(lang);
    for (const s of [storage('schema_partial'), storage('schema_absent')]) {
      const r = tono(s);
      assert.equal(r.cls, 'ti-notice', lang + ' ' + s.status + ': warning tone, not ti-error');
      assert.equal(r.role, 'status');
      assert.match(r.out, MIGRATE[lang]);
    }
    const busy = tono(BUSY);
    assert.equal(busy.cls, 'ti-notice', lang + ': db_busy is a retry warning');
    assert.match(busy.out, RETRY[lang]);
    assert.match(busy.out, /data-ti-storage-tone="warn"/);
    for (const s of [storage('db_missing'), storage('db_unreadable'), storage('schema_incompatible'), LOOKUP_FAILED,
      // incoerenti: migrazione senza flag, lookup_failed travestito, retry su uno stato che non e' db_busy
      { ...storage('schema_partial'), update_required: false },
      { ...LOOKUP_FAILED, action: 'run_explicit_migration', update_required: true },
      { ...BUSY, status: 'db_unreadable' }]) {
      const r = tono(s);
      assert.equal(r.cls, 'ti-notice ti-error', lang + ' ' + JSON.stringify(s) + ': a fault is red');
      assert.equal(r.role, 'alert');
    }
  }
});

// ---------- avvio weekly: trade_idea_active_check ----------

const { controlloAttivaSaltato, testoControlloAttiva } = base('lib/tradeIdeaActiveCheck.ts');
const NOT_RUN = { task_id: 'run_SYNTH', status: 'running', message: 'm',
  trade_idea_active_check: { status: 'not_run', reason: 'trade_idea_storage_unavailable', storage: storage('db_unreadable') } };
const COMPLETED = { task_id: 'run_SYNTH', status: 'running', trade_idea_active_check: { status: 'completed', reason: null, storage: null } };

test('active check helper: not_run (and unknown statuses) are declared with their reason, completed and legacy are not', () => {
  assert.equal(controlloAttivaSaltato(COMPLETED), null);
  assert.equal(controlloAttivaSaltato({ task_id: 'x' }), null, 'older backend without the field: no claim');
  assert.equal(controlloAttivaSaltato(null), null);
  assert.deepEqual(controlloAttivaSaltato(NOT_RUN), NOT_RUN.trade_idea_active_check);
  assert.equal(controlloAttivaSaltato({ trade_idea_active_check: { status: 'future', reason: null } }).status, 'future');
  for (const [lang, head, store, missing] of [
    ['it', 'Controllo Trade Idea attiva non eseguito: archivio Trade Idea non pronto (codice trade_idea_db_unreadable)', 'errore dell’archivio Trade Idea (RuntimeError)', 'motivo non dichiarato'],
    ['en', 'Active Trade Idea check not run: Trade Idea storage not ready (code trade_idea_db_unreadable)', 'Trade Idea storage error (RuntimeError)', 'reason not declared']]) {
    baseLanguage.impostaLinguaCorrente(lang);
    assert.equal(testoControlloAttiva(tr, controlloAttivaSaltato(NOT_RUN)), head);
    assert.ok(testoControlloAttiva(tr, { status: 'not_run', reason: 'trade_idea_store_error:RuntimeError', storage: null }).includes(store));
    assert.ok(testoControlloAttiva(tr, { status: 'not_run', reason: null, storage: null }).includes(missing));
    assert.ok(testoControlloAttiva(tr, { status: 'not_run', reason: 'synthetic_future_reason', storage: null }).includes('synthetic_future_reason'));
  }
});

// Pagina con stato trattenuto per indice e dialogo di conferma catturato.
function pagina(relative, reply, seed = {}, props = null) {
  let si = 0, mi = 0;
  const memos = [], dialogs = [], calls = [];
  const remember = (fn, d) => { const at = mi++, before = memos[at]; if (!before || !d || before.d.length !== d.length || d.some((v, i) => !Object.is(v, before.d[i]))) memos[at] = { d, value: fn() }; return memos[at].value; };
  const api = { API_BASE: 'http://synthetic.invalid', Bellomberg: new Proxy({}, { get: (_t, name) => (...args) => {
    calls.push(name);
    return name === 'runConsigliere' ? Promise.resolve(JSON.parse(JSON.stringify(reply))) : new Promise(() => {});
  } }) };
  const Dialog = { __esModule: true, default: p => { dialogs.push(p); return null; } };
  const load = creaCaricatore({ stub: {
    react: { ...React, useLayoutEffect() {}, useEffect() {}, useMemo: remember, useCallback: (fn, d) => remember(() => fn, d),
      useState(initial) { const at = si++; if (!(at in seed)) seed[at] = typeof initial === 'function' ? initial() : initial; return [seed[at], v => { seed[at] = typeof v === 'function' ? v(seed[at]) : v; }]; } },
    'react/jsx-runtime': JSX,
    'react-router-dom': { useNavigate: () => () => {} },
    '@/lib/api': api, '../lib/api': api,
    '@/components/RunConfirmDialog': Dialog, './RunConfirmDialog': Dialog,
    '@/lib/useBox': { useBox: () => [{ current: null }, { w: 1000, h: 650 }] },
    '@/components/TerminalChart': { __esModule: true, default: () => null },
  } });
  const language = load('i18n/lingua.ts'), Page = load(relative).default;
  const render = lang => { si = mi = 0; dialogs.length = 0; language.impostaLinguaCorrente(lang); return renderToStaticMarkup(React.createElement(Page, props)); };
  const confirm = async lang => {
    render(lang);
    const dialog = dialogs.find(p => typeof p.onConfirm === 'function');
    assert.ok(dialog, 'run confirmation dialog rendered');
    dialog.onConfirm();
    for (let i = 0; i < 4; i++) await new Promise(r => setImmediate(r));
    return render(lang);
  };
  return { render, confirm, calls };
}
const PORTFOLIO = { cash_source: 'sqlite:cash_state', cash_disponibile_eur: 100, nav_total_eur: 1100,
  totale_valore_mercato_eur: 1000, totale_pl_eur: 0, positions: [], n_positions: 0 };

test('Dashboard weekly start: active check not run is declared next to the start chip, not when completed (IT/EN)', async () => {
  const p = pagina('pages/Dashboard.tsx', NOT_RUN, { 0: PORTFOLIO, 4: false });
  const it = await p.confirm('it');
  assert.ok(p.calls.includes('runConsigliere'), 'the confirm really started the run');
  assert.match(it, /data-run-ti-check="not_run"/);
  assert.ok(it.includes('Controllo Trade Idea attiva non eseguito: archivio Trade Idea non pronto (codice trade_idea_db_unreadable)'));
  const en = p.render('en');
  assert.ok(en.includes('Active Trade Idea check not run: Trade Idea storage not ready (code trade_idea_db_unreadable)'), 'follows the language');
  const ok = pagina('pages/Dashboard.tsx', COMPLETED, { 0: PORTFOLIO, 4: false });
  assert.doesNotMatch(await ok.confirm('it'), /data-run-ti-check|Controllo Trade Idea attiva/);
});

test('Agents weekly start: active check not run becomes a warning banner (IT/EN)', async () => {
  const p = pagina('pages/AgentsLive.tsx', NOT_RUN);
  const it = await p.confirm('it');
  assert.ok(p.calls.includes('runConsigliere'));
  assert.match(it, /data-avviso="ti-check"/);
  assert.match(it, /data-ag-ti-check="not_run"/);
  assert.ok(it.includes('Controllo Trade Idea attiva non eseguito: archivio Trade Idea non pronto'));
  assert.ok(p.render('en').includes('Active Trade Idea check not run: Trade Idea storage not ready'));
  const ok = pagina('pages/AgentsLive.tsx', COMPLETED);
  assert.doesNotMatch(await ok.confirm('it'), /data-avviso="ti-check"|Controllo Trade Idea attiva/);
});

test('Command palette weekly start: active check not run stays on screen with its reason', async () => {
  const p = pagina('components/CommandPalette.tsx', NOT_RUN, { 0: true });
  const out = await p.confirm('it');
  assert.ok(p.calls.includes('runConsigliere'));
  assert.ok(out.includes('Controllo Trade Idea attiva non eseguito: archivio Trade Idea non pronto (codice trade_idea_db_unreadable)'), out.slice(0, 400));
  const ok = pagina('components/CommandPalette.tsx', COMPLETED, { 0: true });
  assert.doesNotMatch(await ok.confirm('it'), /Controllo Trade Idea attiva/);
});
