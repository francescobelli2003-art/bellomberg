// R01 (note Ricerca non leggibili) e R14 (storage Trade Idea non pronto, qualifica fonti not_run):
// la UI deve dichiarare il buco, mai trasformarlo in «zero note», «ferma» o «fonti non qualificate».
// Funzioni e componenti VERI, dizionari VERI; si stubba solo la rete.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');
ambienteBrowser();

const headers = { 'X-BB-Language': 'it' };
const load = creaCaricatore({ stub: {
  './api': { API_BASE: 'http://synthetic.invalid', requestHeaders: () => headers,
    clearSessionAndReload: () => { throw new Error('unexpected session clear'); } },
  '@/lib/api': { Bellomberg: {}, API_BASE: 'http://synthetic.invalid', requestHeaders: () => headers,
    clearSessionAndReload: () => { throw new Error('unexpected session clear'); } },
} });
const language = load('i18n/lingua.ts');
const logica = load('pages/decisioni/logica.ts');
const { TradeIdeas, TradeIdeaApiError, tradeIdeaArtifactBlob, storageOf, readTradeIdeaStorage } = load('lib/tradeIdeas.ts');

const DAY = 86400_000;
const ago = d => new Date(Date.now() - d * DAY).toISOString().slice(0, 19);
const research = extra => ({ id: 7, memo_id: 1, timestamp: ago(20), action: 'RESEARCH', ticker: 'SYNTH.X',
  eur_amount: null, timing: 't', confidence: 'c', status: 'PENDING', pm_feedback: null, outcome_pct: null, ...extra });

// ---------- R01: logica.ts ----------

test('R01: unreadable notes are unknown, never zero, never «idle», never counted as to-do', () => {
  const rotta = research({ notes: [], notes_status: 'unavailable', notes_error: 'decision_notes_unavailable' });
  assert.equal(logica.statoNote(rotta), 'unavailable');
  assert.equal(logica.ricercaFerma(rotta), false, 'unknown history must not become an «idle for N days» claim');
  assert.equal(logica.attivitaIgnota(rotta), true);
  // stesso caso con le note LEGGIBILI e vuote: lo zero è vero e la ricerca è davvero ferma
  const vuota = research({ notes: [], notes_status: 'available', notes_error: null });
  assert.equal(logica.statoNote(vuota), 'available');
  assert.equal(logica.ricercaFerma(vuota), true);
  assert.equal(logica.attivitaIgnota(vuota), false);
  // contatore «da fare»: solo la ferma vera
  const g = logica.raggruppa([rotta, { ...vuota, id: 8 }]);
  assert.deepEqual(g.ricerche.filter(x => logica.ricercaFerma(x)).map(x => x.id), [8]);
});

test('R01: operative rows have no notes contract (n/a), incoherent or unmarked empty payloads are not a verified zero', () => {
  assert.equal(logica.statoNote({ ...research({}), action: 'BUY' }), 'n/a');
  assert.equal(logica.attivitaIgnota({ ...research({}), action: 'BUY' }), false);
  assert.equal(logica.statoNote(research({ notes_status: 'available' })), 'unavailable', 'available without an array');
  assert.equal(logica.statoNote(research({ notes: [], notes_error: 'decision_notes_unavailable' })), 'unavailable');
  assert.equal(logica.statoNote(research({ notes: [] })), 'unavailable', 'no marker: an empty list is not certified');
  const legacy = research({ notes: [{ id: 1, autore: 'PM', testo: 'x', timestamp: ago(1) }] });
  assert.equal(logica.statoNote(legacy), 'available', 'notes actually delivered prove the read');
  assert.equal(logica.ricercaFerma(legacy), false);
});

// ---------- R14: parsing del 503 ----------

async function withFetch(reply, run) {
  const before = global.fetch;
  global.fetch = async (url, options) => reply(url, options);
  try { await run(); } finally { global.fetch = before; }
}
const storage = status => ({ status, error_code: 'trade_idea_' + status,
  update_required: status === 'schema_absent' || status === 'schema_partial',
  action: { db_missing: 'check_database_path', schema_partial: 'run_explicit_migration', schema_absent: 'run_explicit_migration',
    schema_incompatible: 'inspect_schema', db_unreadable: 'check_database_access' }[status],
  documentation: 'docs/TRADE_IDEA.md#aggiornamento-storage' });
const json503 = body => new Response(JSON.stringify(body), { status: 503, headers: { 'Content-Type': 'application/json' } });

test('R14: a 503 keeps detail, error_code and the storage diagnosis instead of the message only', async () => {
  for (const status of ['db_missing', 'schema_partial', 'schema_incompatible', 'db_unreadable']) {
    const body = { detail: 'Trade Idea storage non pronto: synthetic', error_code: 'trade_idea_' + status, storage: storage(status) };
    for (const call of [() => TradeIdeas.list({ limit: 5 }), () => TradeIdeas.active(), () => tradeIdeaArtifactBlob('r', 'a')]) {
      await withFetch(() => json503(body), async () => {
        const error = await call().then(() => null, e => e);
        assert.ok(error instanceof TradeIdeaApiError);
        assert.equal(error.status, 503);
        assert.equal(error.message, body.detail);
        assert.equal(error.errorCode, 'trade_idea_' + status);
        assert.deepEqual(error.storage, storage(status));
        assert.deepEqual(storageOf(error), storage(status));
      });
    }
  }
});

test('R14: a malformed storage object is not trusted; other errors carry no storage', async () => {
  await withFetch(() => json503({ detail: 'x', error_code: 7, storage: { status: 'schema_partial' } }), async () => {
    const error = await TradeIdeas.active().then(() => null, e => e);
    assert.equal(error.storage, null);
    assert.equal(error.errorCode, undefined);
  });
  await withFetch(() => new Response('not json', { status: 500 }), async () => {
    const error = await TradeIdeas.active().then(() => null, e => e);
    assert.equal(error.message, 'HTTP 500');
    assert.equal(storageOf(error), null);
  });
  assert.equal(storageOf(new Error('plain')), null);
});

// ---------- R14: resa ----------

const Notice = load('components/TradeIdeaStorageNotice.tsx').default;
const Readiness = load('components/TradeIdeaSourceReadiness.tsx').default;
const html = (C, props) => renderToStaticMarkup(React.createElement(C, props));

test('R14: storage notice proposes the migration only for schema_absent/schema_partial, in both languages', () => {
  for (const lang of ['it', 'en']) {
    language.impostaLinguaCorrente(lang);
    const migrate = lang === 'it' ? /migrazione esplicita/ : /explicit migration/;
    const noMigrate = lang === 'it' ? /Non migrare|prima di qualsiasi migrazione/ : /Do not migrate|before any migration/;
    for (const status of ['db_missing', 'schema_incompatible', 'db_unreadable']) {
      const out = html(Notice, { storage: storage(status), lead: 'LEAD' });
      assert.doesNotMatch(out, migrate, status + ' must not invite a migration');
      assert.match(out, new RegExp('trade_idea_' + status));
      assert.match(out, /LEAD/);
    }
    assert.match(html(Notice, { storage: storage('schema_partial'), lead: 'L' }), migrate);
    assert.match(html(Notice, { storage: storage('schema_absent'), lead: 'L' }), migrate);
    assert.match(html(Notice, { storage: storage('db_missing'), lead: 'L' }), noMigrate);
    assert.match(html(Notice, { storage: storage('schema_incompatible'), lead: 'L' }), noMigrate);
    assert.match(html(Notice, { storage: storage('db_unreadable'), lead: 'L' }), lang === 'it' ? /blocchi/ : /locks/);
    // azione sconosciuta: niente migrazione, guida citata
    const odd = html(Notice, { storage: { ...storage('db_missing'), action: 'future_action', status: 'future' }, lead: 'L' });
    assert.doesNotMatch(odd, migrate);
    assert.match(odd, /future/);
  }
});

test('R14: migration only when action is run_explicit_migration AND update_required is true; never for an unknown action', () => {
  // contratto: update_required booleano obbligatorio, altrimenti la diagnosi non è creduta
  const { update_required, ...noFlag } = storage('schema_partial');
  assert.equal(readTradeIdeaStorage(noFlag), null, 'missing update_required: not a diagnosis');
  assert.equal(readTradeIdeaStorage({ ...storage('schema_partial'), update_required: 'true' }), null, 'string flag: not a diagnosis');
  for (const lang of ['it', 'en']) {
    language.impostaLinguaCorrente(lang);
    const migrate = lang === 'it' ? /migrazione esplicita/ : /explicit migration/;
    const unknown = lang === 'it' ? /non riconosciuto/ : /Unrecognised/;
    // azione di migrazione ma update_required falso: incoerente, nessuna migrazione
    const flagOff = html(Notice, { storage: { ...storage('schema_partial'), update_required: false }, lead: 'L' });
    assert.doesNotMatch(flagOff, migrate);
    assert.match(flagOff, unknown);
    // update_required vero ma azione sconosciuta: mai migrazione
    const odd = html(Notice, { storage: { ...storage('schema_partial'), status: 'future', action: 'future_action', update_required: true }, lead: 'L' });
    assert.doesNotMatch(odd, migrate);
    assert.match(odd, /future/);
    // update_required vero con un'azione nota diversa: il suo rimedio, non la migrazione
    assert.doesNotMatch(html(Notice, { storage: { ...storage('db_missing'), update_required: true }, lead: 'L' }), migrate);
    assert.match(html(Notice, { storage: storage('schema_absent'), lead: 'L' }), migrate);
  }
});

test('R14: the guide is a real link only for a web URL; a repository path is shown, not glued to the API', () => {
  language.impostaLinguaCorrente('it');
  const path = html(Notice, { storage: storage('schema_partial'), lead: 'L' });
  assert.match(path, /docs\/TRADE_IDEA\.md#aggiornamento-storage/);
  assert.doesNotMatch(path, /<a /);
  assert.doesNotMatch(path, /synthetic\.invalid/);
  const web = html(Notice, { storage: { ...storage('schema_partial'), documentation: 'https://example.org/guide#x' }, lead: 'L' });
  assert.match(web, /<a [^>]*href="https:\/\/example\.org\/guide#x"/);
  const evil = html(Notice, { storage: { ...storage('schema_partial'), documentation: 'javascript:alert(1)' }, lead: 'L' });
  assert.doesNotMatch(evil, /<a /);
});

test('R14: source readiness separates not_run, failed and completed', () => {
  const pf = (execution_status, extra = {}) => ({ ok: false, analysis_mode: 'fundamentals_research_v1',
    source_qualification: { status: 'blocked', execution_status, reasons: extra.reasons || [], analysis_mode: 'fundamentals_research_v1' },
    preparation: { required: false, paid: false, status: 'ready' } });
  for (const lang of ['it', 'en']) {
    language.impostaLinguaCorrente(lang);
    const notRun = html(Readiness, { preflight: pf('not_run') });
    assert.match(notRun, lang === 'it' ? /Verifica fonti non eseguita/ : /Source check not run/);
    assert.doesNotMatch(notRun, lang === 'it' ? /non riuscita|Fonti non ancora qualificate|Gli agenti leggono/ : /failed|Sources not yet qualified|Agents read/);
    const legacyNotRun = html(Readiness, { preflight: { ...pf('not_run'), analysis_mode: undefined,
      source_qualification: { status: 'blocked', execution_status: 'not_run', reasons: [] } } });
    assert.doesNotMatch(legacyNotRun, lang === 'it' ? /Fonti non ancora qualificate/ : /Sources not yet qualified/);
    const failed = html(Readiness, { preflight: pf('failed', { reasons: ['synthetic qualifier error'] }) });
    assert.match(failed, lang === 'it' ? /Verifica fonti non riuscita/ : /Source check failed/);
    assert.match(failed, /synthetic qualifier error/);
    const done = html(Readiness, { preflight: { ...pf('completed'), ok: true,
      source_qualification: { status: 'research_required', execution_status: 'completed', reasons: [], analysis_mode: 'fundamentals_research_v1' } } });
    assert.doesNotMatch(done, lang === 'it' ? /non eseguita|non riuscita/ : /not run|failed/);
    assert.match(done, lang === 'it' ? /Gli agenti leggono/ : /Agents read/);
  }
});
