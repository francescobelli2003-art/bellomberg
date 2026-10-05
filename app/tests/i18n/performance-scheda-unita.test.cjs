// 05/10/2026: riepilogo Performance con interruttore % / € e riga «P&L totale» in Altre metriche.
// Dati sintetici: gli euro seguono la stessa finestra della percentuale, il versamento non è un guadagno.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');

ambienteBrowser();
globalThis.window = globalThis.window || { setTimeout: () => 0, clearTimeout() {}, dispatchEvent() {}, addEventListener() {}, removeEventListener() {} };
const load = creaCaricatore({ stub: { '@/lib/api': { Bellomberg: {} } } });
load('i18n/lingua.ts').impostaLinguaCorrente('it');
const VistaScheda = load('pages/performance/VistaScheda.tsx').default;

const date = ['2026-09-01', '2026-09-02', '2026-09-03', '2026-09-04'];
const twr = { dates: date, twr_index: [100, 110, 110 * 1550 / 1600, 110], values_eur: [1000, 1600, 1550, 1600], flows_eur: [0, 500, 0, 0],
  regimes: date.map(() => 'official'), metrics: { risk_free_used: 0.032, twr_total_pct: 10 } };
const props = {
  periodo: 'Tutto', onPeriodo() {}, twr: { stato: 'ok', dati: twr }, spy: { stato: 'attesa' }, avanzate: { stato: 'attesa' },
  contabilita: { stato: 'ok', dati: { valoreMercato: 5000, costo: 4900, nonRealizzato: -40.5, realizzato: 210.25, dividendi: 12.75, cassa: 500 } },
  nav: { valore: 5500, posizioni: 3 }, attribuzione: { stato: 'attesa' }, nomi: {},
};
const testo = html => html.match(/data-qa="perf-return"[^>]*>(.*?)<\/div>/)[1].replace(/<[^>]+>/g, '');

test('Performance riepilogo: % di default con gli euro del periodo sotto; € li inverte', () => {
  try { localStorage.removeItem('bb.performance.unit'); } catch { /* */ }
  const pctHtml = renderToStaticMarkup(React.createElement(VistaScheda, props));
  assert.match(pctHtml, /<button[^>]*aria-pressed="true"[^>]*>%</);
  assert.equal(testo(pctHtml), '+10,00%+100,00 €');
  localStorage.setItem('bb.performance.unit', 'eur');
  const eurHtml = renderToStaticMarkup(React.createElement(VistaScheda, props));
  assert.match(eurHtml, /<button[^>]*aria-pressed="true"[^>]*>€</);
  assert.equal(testo(eurHtml), '+100,00 €+10,00%');
  localStorage.removeItem('bb.performance.unit');
});

test('Altre metriche: P&L totale = non realizzato + realizzato + dividendi, «—» se una voce manca', () => {
  const html = renderToStaticMarkup(React.createElement(VistaScheda, props));
  assert.match(html, /P&amp;L totale<\/span><b><span class="up-t">\+182,50\s€/);
  const senza = { ...props, contabilita: { stato: 'ok', dati: { ...props.contabilita.dati, dividendi: null } } };
  assert.match(renderToStaticMarkup(React.createElement(VistaScheda, senza)), /P&amp;L totale<\/span><b><span>—/);
});
