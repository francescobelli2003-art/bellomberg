// G9c seguito (Opus 5.5, 04/10/2026): testi visibili fissi trovati dalla revisione REV_G9c (R2).
// Ogni caso esegue il codice vero nelle due lingue; dove il render non arriva (tabella catena
// opzioni) si legge il sorgente del solo nodo interessato.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { MemoryRouter } = require('react-router-dom');
const { creaCaricatore, ambienteBrowser, apiFinta } = require('./_carica.cjs');

ambienteBrowser();

test('Trade Idea request errors (no JSON, incomplete research, missing artifact) follow the selected language', async () => {
  const load = creaCaricatore({ stub: { './api': { API_BASE: 'http://synthetic.invalid', requestHeaders: () => ({}),
    clearSessionAndReload: () => { throw new Error('unexpected session clear'); } } } });
  const language = load('i18n/lingua.ts');
  const { TradeIdeas } = load('lib/tradeIdeas.ts');
  const { TradeIdeaResearch, researchArtifactBlob } = load('lib/tradeIdeaResearch.ts');
  const previous = global.fetch;
  try {
    const messages = {};
    for (const locale of ['it', 'en']) {
      language.impostaLinguaCorrente(locale);
      global.fetch = async () => ({ ok: true, status: 200, json: async () => { throw new SyntaxError('synthetic'); } });
      const noJson = await TradeIdeas.list().then(() => null, e => e.message);
      global.fetch = async () => ({ ok: true, status: 200, json: async () => ({ run_id: 'other' }) });
      const incomplete = await TradeIdeaResearch.view('synthetic-run').then(() => null, e => e.message);
      const artifact = await researchArtifactBlob({ id: 1 }).then(() => null, e => e.message);
      messages[locale] = [noJson, incomplete, artifact];
    }
    for (const text of [...messages.it, ...messages.en]) { assert.ok(text, 'an error is declared'); assert.doesNotMatch(text, /⟦|\{[a-z]+\}/); }
    assert.match(messages.it[0], /^HTTP 200: /); assert.match(messages.en[0], /^HTTP 200: /);
    assert.match(messages.it[0], /JSON/); assert.match(messages.en[0], /JSON/);
    assert.doesNotMatch(messages.en[0], /risposta|assente/);
    assert.doesNotMatch(messages.it[1] + messages.it[2], /Research response|incomplete|Artifact unavailable/);
    for (let i = 0; i < 3; i++) assert.notEqual(messages.it[i], messages.en[i], `case ${i} is the same text in both languages`);
  } finally { global.fetch = previous; }
});

test('Trade Idea page section marks follow the selected language', () => {
  const load = creaCaricatore({ stub: { '@/lib/api': apiFinta(),
    '@/lib/tradeIdeas': { TradeIdeas: new Proxy({}, { get: () => () => new Promise(() => {}) }), TradeIdeaApiError: Error,
      readTradeIdeaStorage: () => null, storageOf: () => null },
    '@/lib/tradeIdeaResearch': { TradeIdeaResearch: {} } } });
  const language = load('i18n/lingua.ts'), Page = load('pages/TradeIdeaPage.tsx').default;
  const render = locale => { language.impostaLinguaCorrente(locale);
    return renderToStaticMarkup(React.createElement(MemoryRouter, { initialEntries: ['/trade-idea'] }, React.createElement(Page))); };
  const it = render('it'), en = render('en');
  for (const mark of ['RICERCA / COMITATO', '01 / DATI', '02 / VERIFICHE', '03 / ARCHIVIO']) assert.ok(it.includes(mark), 'it: ' + mark);
  for (const mark of ['RESEARCH / COMMITTEE', '01 / INPUT', '02 / PREFLIGHT', '03 / ARCHIVE']) assert.ok(en.includes(mark), 'en: ' + mark);
  assert.doesNotMatch(it, /RESEARCH \/ COMMITTEE|01 \/ INPUT|02 \/ PREFLIGHT|03 \/ ARCHIVE/);
});

test('option chain download time is labelled through the catalog', () => {
  // la tabella della catena si monta solo dopo una lettura: si controlla il nodo nel sorgente
  // e le frasi del catalogo, che devono esistere e differire nelle due lingue.
  const source = fs.readFileSync(path.resolve(__dirname, '../../src/components/VolWorkbench.tsx'), 'utf8');
  const node = source.split('\n').find(line => line.includes('chain._timestamp'));
  assert.ok(node, 'download time node found');
  assert.doesNotMatch(node, />Download /);
  assert.match(node, /tr\('voldeck\.chainDownloadedAt', \{ time: /);
  const load = creaCaricatore();
  const { traduci } = load('i18n/t.ts');
  const it = traduci('it', 'voldeck.chainDownloadedAt', { time: '10:00' }), en = traduci('en', 'voldeck.chainDownloadedAt', { time: '10:00' });
  assert.match(it, /10:00/); assert.match(en, /10:00/); assert.notEqual(it, en);
});
