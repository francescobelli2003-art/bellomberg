const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
function load(file) {
  const exports = {};
  const scope = { exports, module: { exports }, require(name) {
    const target = name.startsWith('@/')
      ? path.resolve(__dirname, '../../src', name.slice(2).replace(/\.js$/, '') + '.ts')
      : path.resolve(path.dirname(file), name.replace(/\.js$/, '') + '.ts');
    return load(target);
  }};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, scope);
  return scope.module.exports;
}
const helpers = () => load(path.resolve(__dirname, '../../src/lib/trade-entry.ts'));
const decision = { id: 31, ticker: 'SYNTH', action: 'ADD', status: 'PARTIAL' };
const body = { ticker: 'SYNTH', action: 'BUY', quantita: 2, prezzo: 30, valuta: 'USD',
               data: '2026-08-10', linked_decision_id: 31, senza_decisione: false };
const response = () => ({ ok: true, preview_id: 'synthetic-preview', expires_in_seconds: 120,
  cash_delta_eur: -48, cash_disponibile_eur: 952, data: '2026-08-10T12:00:00',
  ora_convenzionale: true, link_origin: 'explicit', decisione: { id: 31, status: 'PARTIAL', nota: null },
  fx: { tasso: 0.8, fonte: 'storico', nota: null }, ricalcolo: null, cassa_nota: 'saldo corrente' });

test('a historical date is preserved; invalid and future dates are rejected', () => {
  const { dataTrade } = helpers();
  assert.equal(dataTrade('2026-08-10', '', '2026-09-12'), '2026-08-10');
  assert.equal(dataTrade('2026-08-10', '09:15', '2026-09-12'), '2026-08-10T09:15:00');
  assert.equal(dataTrade('', '', '2026-09-12'), undefined);
  for (const [day, time] of [['2026-09-13', ''], ['2026-02-30', ''], ['1999-01-01', ''],
                            ['', '09:15'], ['2026-08-10', '25:00']]) {
    assert.throws(() => dataTrade(day, time, '2026-09-12'));
  }
});

test('manual no-decision differs from unknown; partial executions can link explicitly', () => {
  const { legameTrade } = helpers();
  assert.equal(legameTrade('none', [], 'SYNTH', 'BUY').senza_decisione, true);
  assert.equal(legameTrade('unknown', [], 'SYNTH', 'BUY').senza_decisione, false);
  assert.equal(legameTrade('31', [decision], 'SYNTH', 'BUY').linked_decision_id, 31);
  for (const change of [{ ticker: 'OTHER' }, { action: 'SELL' }, { status: 'SKIPPED' },
                        { status: 'EXPIRED' }, { veto: 1 }]) {
    assert.throws(() => legameTrade('31', [{ ...decision, ...change }], 'SYNTH', 'BUY'));
  }
  assert.throws(() => legameTrade('31', [], 'SYNTH', 'BUY'));
});

test('without a decision in the route the link starts undeclared; ?decision=<id> keeps the id', () => {
  const { legameIniziale } = helpers();
  assert.equal(legameIniziale(null), 'unknown');
  assert.equal(legameIniziale(''), 'unknown');
  assert.equal(legameIniziale('31'), '31');
  const src = fs.readFileSync(path.resolve(__dirname, '../../src/pages/TradeEntryPage.tsx'), 'utf8');
  assert.ok(src.includes('useState(() => legameIniziale(decisionFromRoute))'), 'the page starts from legameIniziale');
  assert.ok(!src.includes("useState(decisionFromRoute || 'none')"), 'no silent «no decision» default');
});

test('preview and confirmation carry the chosen link: undeclared, manual no-decision, explicit id', () => {
  const { legameIniziale, legameTrade, congelaAnteprima } = helpers();
  const base = { ticker: 'SYNTH', action: 'BUY', quantita: 2, prezzo: 30, valuta: 'USD', data: '2026-08-10' };
  const cases = [
    { selection: legameIniziale(null), origin: 'unknown', decisione: null,
      check: b => { assert.equal(b.senza_decisione, false); assert.equal('linked_decision_id' in b, false); } },
    { selection: 'none', origin: 'none', decisione: null,
      check: b => { assert.equal(b.senza_decisione, true); assert.equal('linked_decision_id' in b, false); } },
    { selection: legameIniziale('31'), origin: 'explicit', decisione: { id: 31, status: 'PARTIAL', nota: null },
      check: b => { assert.equal(b.senza_decisione, false); assert.equal(b.linked_decision_id, 31); } },
  ];
  for (const c of cases) {
    const request = { ...base, ...legameTrade(c.selection, [decision], 'SYNTH', 'BUY') };
    c.check(request);
    const frozen = congelaAnteprima(request, { ...response(), link_origin: c.origin, decisione: c.decisione });
    c.check(frozen.body);
    for (const other of ['unknown', 'none', 'explicit'].filter(o => o !== c.origin)) {
      assert.throws(() => congelaAnteprima(request, { ...response(), link_origin: other, decisione: c.decisione }),
        `${c.origin} body must not confirm a ${other} preview`);
    }
  }
});

test('blocked proposal is not linkable; different execution ticker needs recorded identity proof', () => {
  const { assessmentAllowsExecution, decisioneCompatibile, legameTrade } = helpers();
  const blocked = { ...decision, assessment_status: 'BLOCKED' };
  const unavailable = { ...decision, assessment_status: 'CHECK_UNAVAILABLE' };
  const override = { ...decision, assessment_status: 'OVERRIDE_PENDING' };
  assert.equal(decisioneCompatibile(blocked, 'SYNTH', 'BUY'), false);
  assert.equal(decisioneCompatibile(unavailable, 'SYNTH', 'BUY'), false);
  assert.equal(decisioneCompatibile(override, 'SYNTH', 'BUY'), false);
  assert.equal(assessmentAllowsExecution(blocked), false);
  assert.equal(assessmentAllowsExecution(unavailable), false);
  assert.equal(assessmentAllowsExecution(override), false);
  assert.throws(() => legameTrade('31', [decision], 'BROKER.DE', 'BUY'));
  assert.equal(legameTrade('31', [decision], 'BROKER.DE', 'BUY', true).linked_decision_id, 31);
  assert.throws(() => legameTrade('31', [blocked], 'BROKER.DE', 'BUY', true));
});

test('unreadable Trade Idea provenance suspends the link of a decision without trade_idea (10/10)', () => {
  const { collegamentoSospeso, decisioneCompatibile, legameTrade } = helpers();
  const storage = { status: 'lookup_failed', error_code: 'trade_idea_lookup_failed', update_required: false, action: 'check_database_access' };
  const plain = { ...decision, trade_idea: null };
  assert.equal(decisioneCompatibile(plain, 'SYNTH', 'BUY'), true, 'provenance read (no storage): linkable as before');
  assert.equal(decisioneCompatibile(plain, 'SYNTH', 'BUY', false, storage), false);
  assert.equal(decisioneCompatibile(plain, 'BROKER.DE', 'BUY', true, storage), false, 'the alias path is suspended too');
  assert.equal(collegamentoSospeso(plain, storage), true);
  assert.equal(collegamentoSospeso(plain, null), false);
  // una trade_idea presente e' stata letta: si valuta come sempre
  const read = { ...decision, trade_idea: { technical_status: 'completed', destination_kind: 'dcn', artifacts_ready: true } };
  assert.equal(collegamentoSospeso(read, storage), false);
  assert.equal(decisioneCompatibile(read, 'SYNTH', 'BUY', false, storage), true);
  assert.throws(() => legameTrade('31', [plain], 'SYNTH', 'BUY', false, storage),
    e => /collegamento sospeso|linking suspended/.test(e.message));
  assert.equal(legameTrade('31', [plain], 'SYNTH', 'BUY').linked_decision_id, 31);
  assert.equal(JSON.stringify(legameTrade('unknown', [plain], 'SYNTH', 'BUY', false, storage)), JSON.stringify({ senza_decisione: false }));
});

test('confirmation freezes the server historical FX and exact linked request', () => {
  const { congelaAnteprima } = helpers();
  const input = { ...body };
  const result = congelaAnteprima(input, response());
  input.data = '2026-09-12'; input.linked_decision_id = 32;
  assert.equal(result.body.data, '2026-08-10');
  assert.equal(result.body.linked_decision_id, 31);
  assert.equal(result.body.preview_id, 'synthetic-preview');
  assert.equal(result.preview.fx.tasso, 0.8);
  assert.equal(result.preview.cash_disponibile_eur, 952);
  assert.equal(Object.isFrozen(result.body), true);
});

test('a failed or incomplete preview cannot become a confirmation with guessed numbers', () => {
  const { congelaAnteprima } = helpers();
  for (const changes of [{ ok: false }, { preview_id: '' }, { cash_delta_eur: NaN },
      { cash_disponibile_eur: null }, { fx: { tasso: 1, fonte: 'unknown' } },
      { decisione: { id: 32, status: 'PARTIAL' } }, { ora_convenzionale: null }]) {
    assert.throws(() => congelaAnteprima(body, { ...response(), ...changes }));
  }
});

test('movement clocks distinguish conventional, measured noon and unknown legacy time', () => {
  const m = load(path.resolve(__dirname, '../../src/lib/movimenti.ts'));
  const base = { ticker: 'SYNTH', action: 'BUY', quantita: 1, prezzo: 20,
    valuta: 'EUR', data: '2026-08-10T12:00:00' };
  const rows = [{ ...base, ora_convenzionale: 1 }, { ...base, ora_convenzionale: 0 }, base];
  assert.equal(m.contaOreSegnaposto(rows), 1);
  assert.match(m.oraTrade(rows[0]), /convenzionale/);
  assert.equal(m.oraTrade(rows[1]), '12:00:00');
  assert.match(m.oraTrade(rows[2]), /non documentata/);
  assert.match(m.legameMovimento({ ...base, link_origin: 'none' }), /senza decisione/);
  assert.match(m.legameMovimento({ ...base, link_origin: 'explicit', linked_decision_id: 31 }), /#31/);
  assert.match(m.legameMovimento(base), /non dichiarato/);
  assert.match(m.legameMovimento({ ...base, linked_decision_id: 31 }), /#31.*non documentata/);
});

test('a server 5xx or timeout cannot certify that the trade was not committed', () => {
  const { scritturaRifiutata } = load(path.resolve(__dirname, '../../src/lib/cassa.ts'));
  for (const status of [400, 401, 403, 409, 422]) assert.equal(scritturaRifiutata({ response: { status } }), true);
  for (const status of [500, 502, 503, 504]) assert.equal(scritturaRifiutata({ response: { status } }), false);
  assert.equal(scritturaRifiutata({ message: 'timeout' }), false);
});

test('stable cash guard codes work in English without translating or parsing the reason', () => {
  const { leggiRifiuto } = load(path.resolve(__dirname, '../../src/lib/cassa.ts'));
  for (const [code, expected] of [['cash_duplicate', 'duplicato'], ['cash_threshold', 'soglia']]) {
    const value = leggiRifiuto({ response: { status: 422, data: { code, detail: 'Original English guard detail' } } });
    assert.equal(value.quale, expected); assert.equal(value.rimediabile, true);
    assert.equal(value.nonScritto, true); assert.equal(value.motivo, 'Original English guard detail');
  }
  const unrelated = leggiRifiuto({ response: { status: 422, data: { code: 'unknown', detail: 'conferma=true' } } });
  assert.equal(unrelated.rimediabile, false, 'an unknown explicit code must not be reclassified from prose');
  for (const status of [401, 403, 409]) {
    assert.equal(leggiRifiuto({ response: { status, data: { code: 'cash_duplicate', detail: 'Rejected' } } }).rimediabile, false);
  }
});

test('both interface languages accept the same machine FX identifiers and preserve confirmed data', () => {
  const { creaCaricatore, ambienteBrowser } = require('../i18n/_carica.cjs');
  ambienteBrowser();
  const read = creaCaricatore();
  const language = read('i18n/lingua.ts');
  const trade = read('lib/trade-entry.ts');
  for (const choice of ['it', 'en']) {
    language.impostaLinguaCorrente(choice);
    for (const source of ['identity', 'storico', 'corrente']) {
      const reply = response(); reply.fx.fonte = source;
      const confirmed = trade.congelaAnteprima(body, reply);
      assert.equal(confirmed.preview.fx.fonte, source);
      assert.equal(confirmed.preview.fx.tasso, 0.8);
      assert.equal(confirmed.body.linked_decision_id, 31);
    }
  }
});

test('an existing local validation error can be rendered in the new language without rerunning a request', () => {
  const { creaCaricatore, ambienteBrowser } = require('../i18n/_carica.cjs');
  ambienteBrowser(); const read = creaCaricatore();
  const language = read('i18n/lingua.ts'), trade = read('lib/trade-entry.ts');
  language.impostaLinguaCorrente('it');
  let error; try { trade.legameTrade('31', [], 'SYNTH', 'BUY'); } catch (e) { error = e; }
  assert.match(error.renderMessage(), /Decisione non disponibile/);
  language.impostaLinguaCorrente('en');
  assert.match(error.renderMessage(), /Decision unavailable/);
});

const openingHelpers = () => load(path.resolve(__dirname, '../../src/lib/position-opening.ts'));
const openingDraft = { ticker: ' synth ', quantita: '2,5', prezzo_medio: '0', valuta: 'USD',
  giorno: '2026-08-10', ora: '', precisione: 'day', provenienza: '  Synthetic statement  ',
  nome: 'Original name', nota: 'Nota originale / original note' };
const openingBody = { ticker: 'SYNTH', quantita: 2.5, prezzo_medio: 0, valuta: 'USD',
  as_of: '2026-08-10', provenienza: 'Synthetic statement', nome: 'Original name', nota: openingDraft.nota };
const openingPreview = () => ({ ok: true, preview_id: 'opening-token', expires_in_seconds: 120,
  opening: { ...openingBody, precisione_data: 'day' },
  position: { ticker: 'SYNTH', nome: 'Original name', quantita: 2.5, prezzo_medio: 0,
    valuta: 'USD', data_apertura: null },
  cash_delta_eur: 0, cash_disponibile_eur: null, performance_note: 'Synthetic coverage note' });

test('opening balance uses locale numbers, permits zero cost and preserves source text without trade fields', () => {
  const { preparaPosizioneIniziale } = openingHelpers();
  for (const [language, quantity] of [['it', '2,5'], ['en', '2.5']]) {
    const value = preparaPosizioneIniziale({ ...openingDraft, quantita: quantity }, language, '2026-09-12');
    assert.deepEqual(JSON.parse(JSON.stringify(value)), openingBody);
    for (const key of ['action', 'data', 'cash_delta_eur', 'fx', 'precisione_data', 'linked_decision_id']) {
      assert.equal(key in value, false, key);
    }
  }
});

test('opening requires documented date precision, positive quantity, nonnegative cost and source', () => {
  const { preparaPosizioneIniziale } = openingHelpers();
  for (const patch of [{ giorno: '' }, { giorno: '2026-02-30' }, { giorno: '2026-09-13' },
    { precisione: 'second', ora: '' }, { precisione: 'second', ora: '25:15' },
    { precisione: 'guessed' }, { quantita: '0' }, { quantita: '-1' }, { prezzo_medio: '-1' },
    { prezzo_medio: '' }, { prezzo_medio: '1.234' }, { provenienza: '   ' }, { ticker: '' }]) {
    assert.throws(() => preparaPosizioneIniziale({ ...openingDraft, ...patch }, 'it', '2026-09-12'), JSON.stringify(patch));
  }
  const day = preparaPosizioneIniziale({ ...openingDraft, ora: '12:12' }, 'it', '2026-09-12');
  assert.equal(day.as_of, '2026-08-10', 'a date-only source must never acquire an invented hour');
  const second = preparaPosizioneIniziale({ ...openingDraft, precisione: 'second', ora: '09:15' }, 'it', '2026-09-12');
  assert.equal(second.as_of, '2026-08-10T09:15:00');
});

test('opening confirmation freezes a coherent cash-neutral response and exact request', () => {
  const { congelaPosizioneIniziale } = openingHelpers();
  const source = openingPreview(), request = { ...openingBody };
  const frozen = congelaPosizioneIniziale(request, source);
  source.opening.quantita = 100; source.position.quantita = 100; request.nota = 'changed';
  assert.equal(frozen.preview.opening.quantita, 2.5);
  assert.equal(frozen.preview.position.quantita, 2.5);
  assert.equal(frozen.body.nota, openingDraft.nota);
  assert.equal(frozen.body.preview_id, 'opening-token');
  assert.equal(Object.isFrozen(frozen.body), true);
  assert.equal(Object.isFrozen(frozen.preview.opening), true);
});

test('opening preview rejects changed amounts, provenance, date, currency, nonzero cash or guessed acquisition', () => {
  const { congelaPosizioneIniziale } = openingHelpers();
  for (const alter of [p => p.cash_delta_eur = 1, p => delete p.cash_disponibile_eur,
    p => p.cash_disponibile_eur = NaN, p => p.preview_id = '', p => p.expires_in_seconds = 0,
    p => p.opening.quantita = 10, p => p.opening.prezzo_medio = 1, p => p.opening.valuta = 'EUR',
    p => p.opening.provenienza = 'different', p => p.opening.as_of += 'T12:00:00',
    p => p.opening.precisione_data = 'second', p => p.position.data_apertura = '2026-08-10',
    p => p.position.quantita = 10, p => p.opening.nota = 'translated', p => delete p.performance_note]) {
    const response = openingPreview(); alter(response);
    assert.throws(() => congelaPosizioneIniziale(openingBody, response));
  }
});

test('opening receipt and readback require identity, creation time, exact baseline values and no guessed acquisition', () => {
  const { leggiRicevutaPosizione, leggiPosizioniIniziali } = openingHelpers();
  const response = openingPreview(); delete response.preview_id; delete response.expires_in_seconds;
  response.opening.id = 71; response.opening.created_at = '2026-09-12T10:00:00+00:00';
  assert.equal(leggiRicevutaPosizione(openingBody, response).opening.id, 71);
  assert.equal(leggiPosizioniIniziali({ openings: [response.opening] })[0].id, 71);
  assert.throws(() => leggiPosizioniIniziali({ openings: [{ ...response.opening, id: null }] }));
  assert.throws(() => leggiRicevutaPosizione(openingBody, { ...response, opening: { ...response.opening, id: 0 } }));
  assert.throws(() => leggiRicevutaPosizione(openingBody, { ...response, opening: { ...response.opening, ticker: 'OTHER' } }));
});

test('opening readback rejects impossible calendar dates and preview cannot change the original name', () => {
  const { leggiPosizioniIniziali, congelaPosizioneIniziale } = openingHelpers();
  const record = { ...openingPreview().opening, id: 71, created_at: '2026-09-12T10:00:00+00:00' };
  assert.throws(() => leggiPosizioniIniziali({ openings: [{ ...record, as_of: '2026-02-30' }] }));
  const changed = openingPreview(); changed.opening.nome = 'Wrong name';
  assert.throws(() => congelaPosizioneIniziale(openingBody, changed));
});

// ── G9a: anteprima sola lettura, legame confermato insieme al trade ──────────
const ISIN_SINT = 'XS0000000000';
const identityBody = { isin: ISIN_SINT, source: 'prospetto sintetico', verified_at: '2026-09-01T08:00:00.000Z', reason: 'stessa azione' };
const identityView = { ...identityBody, proposed_ticker: 'SYNTH', execution_ticker: 'BROKER.DE' };
const aliasBody = { ...body, ticker: 'BROKER.DE', instrument_identity: identityBody };
const divBody = { ...body, linked_decision_id: undefined, senza_decisione: true,
  manual_divergence: { decision_id: 41, reason: 'eseguito comunque' } };
const divView = { decision_id: 41, reason: 'eseguito comunque', ticker_proposto: 'SYNTH', ticker_eseguito: 'SYNTH' };

test('a confirmation carries only the ISIN verification and divergence the backend validated', () => {
  const { congelaAnteprima } = helpers();
  assert.equal(congelaAnteprima(aliasBody, { ...response(), instrument_identity: identityView }).body.instrument_identity.isin, ISIN_SINT);
  assert.throws(() => congelaAnteprima(aliasBody, response()), 'preview silent on the verification');
  assert.throws(() => congelaAnteprima(aliasBody, { ...response(), instrument_identity: { ...identityView, isin: 'XS0000000018' } }));
  assert.throws(() => congelaAnteprima(body, { ...response(), instrument_identity: identityView }), 'verification nobody asked for');
  const div = { ...response(), link_origin: 'none', decisione: null };
  assert.equal(congelaAnteprima(divBody, { ...div, manual_divergence: divView }).body.manual_divergence.decision_id, 41);
  assert.throws(() => congelaAnteprima(divBody, div), 'preview silent on the divergence');
  assert.throws(() => congelaAnteprima(divBody, { ...div, manual_divergence: { ...divView, reason: 'altro' } }));
  assert.throws(() => congelaAnteprima(divBody, { ...div, manual_divergence: { ...divView, decision_id: 42 } }));
});

test('ISIN verification body: unreadable date is a declared error, not a RangeError', () => {
  const { corpoIdentita, FrontendTradeError } = helpers();
  const ok = corpoIdentita(' xs0000000000 ', ' fonte ', '2026-09-01T10:00', ' motivo ');
  assert.equal(ok.isin, ISIN_SINT); assert.equal(ok.source, 'fonte'); assert.equal(ok.reason, 'motivo');
  assert.match(ok.verified_at, /^2026-09-01T\d{2}:00:00\.000Z$/);
  for (const args of [['', 'f', '2026-09-01T10:00', 'm'], [ISIN_SINT, 'f', 'non-una-data', 'm'], [ISIN_SINT, 'f', '2026-13-45T99:99', 'm']]) {
    let err = null; try { corpoIdentita(...args); } catch (e) { err = e; }
    assert.ok(err instanceof FrontendTradeError, String(err));
  }
});

test('the confirm dialog shows the ticker alias and the manual divergence that will be written', () => {
  const { righeLegame } = helpers();
  const rows = righeLegame({ ...response(), instrument_identity: identityView, manual_divergence: { ...divView, ticker_eseguito: 'BROKER.DE' } });
  const text = rows.map(r => r.k + ' ' + r.v).join('\n');
  for (const piece of ['SYNTH', 'BROKER.DE', ISIN_SINT, 'prospetto sintetico', '#41', 'eseguito comunque']) assert.ok(text.includes(piece), piece);
  assert.equal(righeLegame(response()).length, 0);
});

test('deposited/withdrawn totals: missing amount and unknown type are n/a with a reason, a full window is a minimum', () => {
  const { totaliMovimenti } = helpers();
  const ok = totaliMovimenti([{ type: 'DEPOSIT', amount_eur: 100 }, { type: 'WITHDRAWAL', amount_eur: 30 }, { type: 'DEPOSIT', amount_eur: 5 }], 200);
  assert.deepEqual([ok.versati, ok.prelevati, ok.parziale, ok.motivoVersati, ok.motivoPrelevati], [105, 30, false, null, null]);
  const missing = totaliMovimenti([{ type: 'DEPOSIT', amount_eur: null }, { type: 'WITHDRAWAL', amount_eur: 30 }], 200);
  assert.equal(missing.versati, null); assert.ok(missing.motivoVersati); assert.equal(missing.prelevati, 30);
  const unknown = totaliMovimenti([{ type: 'DEPOSIT', amount_eur: 10 }, { type: 'FEE', amount_eur: 3 }], 200);
  assert.equal(unknown.versati, null); assert.equal(unknown.prelevati, null);
  assert.ok(unknown.motivoPrelevati.includes('FEE'));
  assert.equal(totaliMovimenti([{ type: 'DEPOSIT', amount_eur: 1 }, { type: 'DEPOSIT', amount_eur: 1 }], 2).parziale, true);
});

test('Trade Entry page: preview never writes, commit reads the frozen body, menu compares the typed ticker', () => {
  const src = fs.readFileSync(path.resolve(__dirname, '../../src/pages/TradeEntryPage.tsx'), 'utf8');
  for (const gone of ['verifyInstrumentIdentity', 'recordManualTradeDivergence', 'decisioneCompatibile(d, d.ticker',
                      'Trade <b>#', '· BLOCKED', "'DIV'"]) {
    assert.ok(!src.includes(gone), gone);
  }
  const commit = src.slice(src.indexOf('const commit = async'), src.indexOf('const pendingRows'));
  assert.ok(commit.includes('p.body.manual_divergence') && !/\bmanualDivergence(Decision|Reason)\b/.test(commit), 'commit reads live form state');
  assert.ok(src.slice(src.indexOf('const pendingRows'), src.indexOf('if (v.ricalcolo)')).includes('righeLegame(v)'));
  assert.ok(src.includes('decisioneCompatibile(d, tickerUp, action, false, tiStorage)'));
  assert.ok(src.includes('decisioneCompatibile(d, tickerUp, action, true, tiStorage)'));
  assert.ok(src.includes('legameTrade(selectedDecision, decisions, tickerUp, action, !!identita, tiStorage)'));
  const api = fs.readFileSync(path.resolve(__dirname, '../../src/lib/api.ts'), 'utf8');
  assert.ok(!api.includes("'/instrument-identities/verify'"));
});

test('taglieStoriche: an order with unreadable quantity or price is excluded and counted, never a size of 0 (G9b review)', () => {
  const { taglieStoriche } = load(path.resolve(__dirname, '../../src/lib/cassa.ts'));
  const r = taglieStoriche([
    { ticker: 'ZZTEST', action: 'BUY', data: '2026-09-01', quantita: 10, prezzo: 5, valuta: 'EUR' },
    { ticker: 'ACME.MI', action: 'BUY', data: '2026-09-02', quantita: null, prezzo: 7, valuta: 'EUR' },
    { ticker: 'ZZTEST', action: 'SELL', data: '2026-09-03', quantita: 2, prezzo: 30, valuta: 'EUR' },
  ], null, []);
  assert.deepEqual(r.misurate.map(t => t.eur), [50, 60]);
  assert.equal(r.mediana, 55);
  assert.equal(r.senzaImporto, 1);
  assert.equal(r.senzaCambio, 0);
});

test('Trade Entry page declares the orders excluded from the sizes for a missing amount (G9a+G9b integration)', () => {
  const src = fs.readFileSync(path.resolve(__dirname, '../../src/pages/TradeEntryPage.tsx'), 'utf8');
  assert.ok(/taglie\.senzaImporto > 0 && <> \{tr\('trade\.orders_no_amount', \{ n: String\(taglie\.senzaImporto\) \}\)\}/.test(src), 'senzaImporto not shown');
  assert.ok(src.includes('taglie.senzaCambio > 0 || taglie.senzaImporto > 0 || tradesErr'), 'note hidden when only senzaImporto > 0');
  const cat = l => fs.readFileSync(path.resolve(__dirname, `../../src/i18n/${l}/trade.ts`), 'utf8');
  assert.match(cat('it'), /"orders_no_amount": "\{n\} movimenti senza importo non conteggiati"/);
  assert.match(cat('en'), /"orders_no_amount": "\{n\} movements without an amount not counted"/);
});

test('manual divergence is offered for every non-operative status the backend accepts, each named in it/en (G6 in Trade Entry)', () => {
  const { creaCaricatore, ambienteBrowser } = require('../i18n/_carica.cjs');
  ambienteBrowser(); const read = creaCaricatore();
  const language = read('i18n/lingua.ts'), trade = read('lib/trade-entry.ts');
  const stati = ['BLOCKED', 'OVERRIDE_PENDING', 'CHECK_UNAVAILABLE'];
  for (const s of stati) assert.equal(trade.statoDivergenza({ assessment_status: s }), s, s);
  for (const s of ['OPERATIVE', null, undefined, '']) assert.equal(trade.statoDivergenza({ assessment_status: s }), null, String(s));
  const attesi = { it: ['BLOCCATA', 'DEROGA IN ATTESA', 'NON VERIFICABILE'], en: ['BLOCKED', 'OVERRIDE PENDING', 'NOT VERIFIABLE'] };
  for (const lingua of ['it', 'en']) {
    language.impostaLinguaCorrente(lingua);
    assert.deepEqual(stati.map(s => trade.etichettaStatoDivergenza(s)), attesi[lingua]);
    const spiegazioni = stati.map(s => trade.spiegazioneDivergenza(s, 'ZZTEST'));
    assert.equal(new Set(spiegazioni).size, 3, 'each case explains itself');
    for (const t of spiegazioni) assert.ok(t.includes('ZZTEST') && !/[⟦{]/.test(t), t);
    const righe = trade.righeLegame({ ...response(), manual_divergence: { decision_id: 41, reason: 'r', ticker_proposto: 'ZZTEST',
      ticker_eseguito: 'ZZTEST', assessment_status: 'CHECK_UNAVAILABLE' } });
    assert.ok(righe.some(r => r.v.includes(attesi[lingua][2])), 'confirm dialog names the status');
  }
  language.impostaLinguaCorrente('it');
  // la pagina e il pulsante della pagina Decisioni passano dall'helper, non dal letterale BLOCKED
  // (05/10/2026: la pagina Decisioni Nuova tiene gruppi e pulsanti in pages/decisioni/)
  for (const file of ['TradeEntryPage.tsx', 'Decisions.tsx', 'decisioni/logica.ts', 'decisioni/Vista.tsx']) {
    const src = fs.readFileSync(path.resolve(__dirname, '../../src/pages', file), 'utf8');
    assert.ok(!src.includes("=== 'BLOCKED'") && !src.includes("!== 'BLOCKED'"), file);
    if (file !== 'Decisions.tsx') assert.ok(/statoDivergenza\(\w+\) !== null/.test(src), file);
  }
});
