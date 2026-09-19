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
