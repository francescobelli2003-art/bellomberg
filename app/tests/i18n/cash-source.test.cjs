const { test } = require('node:test');
const assert = require('node:assert/strict');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');
ambienteBrowser();
const load = creaCaricatore();
const { simula, converti, cambioPer } = load('lib/cassa.ts');
const { portfolioValues } = load('lib/portfolio-values.ts');
const order = { ticker: 'SYNTH.X', action: 'BUY', quantita: 2, prezzo: 30, valuta: 'EUR' };
const conversion = converti(order, cambioPer('EUR', null, []));
const snapshot = { positions: [], cash_disponibile_eur: 100, nav_total_eur: 150,
  totale_valore_mercato_eur: 50, cash_source: 'sqlite:cash_state' };

test('an unverified cash source cannot cover an order or produce cash and NAV forecasts', () => {
  for (const source of [undefined, null, '', 'portfolio.json', 'synthetic-memory']) {
    const data = { ...snapshot, cash_source: source };
    const result = simula(order, conversion, undefined, data);
    assert.equal(result.cassaPrima, portfolioValues(data).cash, String(source));
    assert.equal(result.navPrima, portfolioValues(data).nav, String(source));
    for (const key of ['cassaDopo', 'copre', 'mancano', 'navDopo']) assert.equal(result[key], null, key);
    assert.equal(result.cassaNegativa, false);
  }
});

test('verified positive, zero and negative balances remain measurements', () => {
  for (const cash of [100, 0, -10]) {
    const result = simula(order, conversion, undefined, { ...snapshot, cash_disponibile_eur: cash });
    assert.equal(result.cassaPrima, cash);
    assert.equal(result.cassaDopo, cash - 60);
    assert.equal(result.copre, cash >= 60);
    assert.equal(result.mancano, cash >= 60 ? null : 60 - cash);
    assert.equal(result.cassaNegativa, cash < 0);
    assert.equal(result.navDopo, 150);
  }
});

test('missing and nonfinite totals do not enter forecasts, including an empty order', () => {
  for (const value of [null, undefined, NaN, Infinity, -Infinity]) {
    for (const attempted of [order, { ...order, quantita: 0 }]) {
      const data = { ...snapshot, cash_disponibile_eur: value, nav_total_eur: value,
        totale_valore_mercato_eur: value };
      const result = simula(attempted, conversion, undefined, data);
      for (const key of ['cassaPrima', 'cassaDopo', 'navPrima', 'navDopo', 'pesoPrima', 'pesoDopo']) {
        assert.equal(result[key], null, key + ': ' + value);
      }
    }
  }
  assert.equal(simula(order, conversion, undefined, { ...snapshot, nav_total_eur: Infinity }).navPrima, null);
  const result = simula(order, conversion, undefined, { ...snapshot, totale_valore_mercato_eur: Infinity });
  assert.equal(result.pesoDopo, null);
  assert.equal(result.pesoMuto, 'book-assente');
});
