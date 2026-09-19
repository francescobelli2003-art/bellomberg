// 13/09 (Claude Opus 5): il valore mancante stampato da lib/format parla la lingua della UI.
// Prima di questa prova fmtEUR/fmtPct/fmtNum restituivano un 'n/a' fisso anche in italiano
// (revisione C5 del 13/09: CF-VaR 99% in Monte Carlo, poi coperto da un involucro nella pagina).
// Frasi attese congelate qui, non lette dai cataloghi sotto prova.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');

ambienteBrowser();
const carica = creaCaricatore();
const lingua = carica('i18n/lingua.ts');
const formato = carica('lib/format.ts');

const ATTESO = { it: 'n.d.', en: 'n/a' };
const BUCHI = [null, undefined, NaN, Infinity, -Infinity];

test('fmtEUR, fmtPct e fmtNum dichiarano il buco con il segnaposto della lingua corrente', () => {
  for (const selected of ['it', 'en', 'it']) {
    lingua.impostaLinguaCorrente(selected);
    for (const v of BUCHI) {
      assert.equal(formato.fmtEUR(v), ATTESO[selected], `${selected} fmtEUR(${v})`);
      assert.equal(formato.fmtEUR(v, true, 0), ATTESO[selected], `${selected} fmtEUR(${v}, sign, 0)`);
      assert.equal(formato.fmtPct(v), ATTESO[selected], `${selected} fmtPct(${v})`);
      assert.equal(formato.fmtNum(v, 0), ATTESO[selected], `${selected} fmtNum(${v}, 0)`);
    }
  }
});

test('la lingua passata dal chiamante vince su quella corrente anche per il buco', () => {
  lingua.impostaLinguaCorrente('en');
  assert.equal(formato.fmtEUR(null, false, 2, 'it'), 'n.d.');
  assert.equal(formato.fmtPct(NaN, true, 2, 'it'), 'n.d.');
  assert.equal(formato.fmtNum(undefined, 2, 'it'), 'n.d.');
  lingua.impostaLinguaCorrente('it');
  assert.equal(formato.fmtEUR(null, false, 2, 'en'), 'n/a');
  assert.equal(formato.fmtPct(NaN, true, 2, 'en'), 'n/a');
  assert.equal(formato.fmtNum(undefined, 2, 'en'), 'n/a');
});

test('i numeri veri restano numeri: lo zero non diventa un buco', () => {
  lingua.impostaLinguaCorrente('it');
  assert.equal(formato.fmtEUR(0), '0,00\u00a0€', 'separatore NBSP di Intl it-IT');
  assert.equal(formato.fmtPct(0), '0,00%');
  assert.equal(formato.fmtNum(0, 0), '0');
  lingua.impostaLinguaCorrente('en');
  assert.equal(formato.fmtEUR(0), '€0.00');
  assert.equal(formato.fmtPct(0), '0.00%');
  assert.equal(formato.fmtNum(0, 0), '0');
});

test('un consumatore vero di lib/format (curva.eur0) riceve il segnaposto della lingua', () => {
  const curva = carica('lib/curva.ts');
  lingua.impostaLinguaCorrente('it');
  assert.equal(curva.eur0(null), 'n.d.');
  assert.equal(curva.eur0(1234), '1.234\u00a0€');
  lingua.impostaLinguaCorrente('en');
  assert.equal(curva.eur0(null), 'n/a');
  assert.equal(curva.eur0(1234), '€1,234');
  lingua.impostaLinguaCorrente('it');
});
