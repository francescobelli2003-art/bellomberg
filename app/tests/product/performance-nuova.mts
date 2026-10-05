import assert from 'node:assert/strict';
import test from 'node:test';
import {
  PERIODI, periodoAttribuzione, indiceBase, statistichePeriodo, rendimentoAllaData,
  sottAcqua, mediaMobile, rendimentiMensili, pnlGiornalieri, pnlPeriodo, pnlTotale, istogramma, sintesiDistribuzione, RichiesteUltime,
} from '../../src/pages/performance/calcoli.ts';

const close = (a: number | null, b: number, eps = 1e-9) => a != null && Math.abs(a - b) < eps;
// calendario di borsa lun-ven dall'8 maggio 2026 (inizio reale del portafoglio)
const sedute = (() => { const out: string[] = []; for (let d = new Date(Date.UTC(2026, 4, 8)); d <= new Date(Date.UTC(2026, 9, 2)); d.setUTCDate(d.getUTCDate() + 1)) { const w = d.getUTCDay(); if (w && w < 6) out.push(d.toISOString().slice(0, 10)); } return out; })();

test('periodi: selettore e mappa verso l\'attribuzione', () => {
  assert.deepEqual([...PERIODI], ['1S', '1M', '3M', 'YTD', 'Tutto']);
  assert.deepEqual(PERIODI.map(periodoAttribuzione), ['1W', '30D', '3M', 'YTD', 'INCEPTION']);
});

test('indiceBase: ultima seduta prima del primo giorno del periodo, stessi confini dell\'attribuzione', () => {
  const fine = sedute.length - 1; // 2026-10-02, venerdì
  assert.equal(sedute[indiceBase(sedute, '1S')], '2026-09-25'); // finestra 26/09..02/10
  assert.equal(sedute[indiceBase(sedute, '1M')], '2026-09-02'); // finestra 03/09..02/10
  assert.equal(sedute[indiceBase(sedute, '3M')], '2026-07-03'); // finestra 05/07..02/10
  assert.equal(indiceBase(sedute, 'YTD'), 0); // storia più corta dell'anno
  assert.equal(indiceBase(sedute, 'Tutto'), 0);
  assert.ok(indiceBase(sedute, '1S') < fine);
  assert.equal(indiceBase([], '3M'), 0);
});

test('statistichePeriodo: rendimento, max drawdown e Sharpe solo con almeno 20 rendimenti', () => {
  const indice = [100, 110, 99, 104.5];
  const s = statistichePeriodo(indice, 0, 0);
  assert.ok(close(s.rendimentoPct, 4.5));
  assert.ok(close(s.maxDdPct, -10));
  assert.equal(s.sharpe, null);
  assert.equal(s.nRendimenti, 3);
  const lungo = Array.from({ length: 41 }, (_, i) => 100 * Math.pow(1.001, i) * (i % 2 ? 1.002 : 1));
  const l = statistichePeriodo(lungo, 0, 0.032, sedute.slice(0, 41));
  assert.ok(l.sharpe != null && Number.isFinite(l.sharpe));
  assert.deepEqual(statistichePeriodo([100], 0, 0), { rendimentoPct: null, maxDdPct: null, ddOggiPct: null, sharpe: null, nRendimenti: 0 });
  assert.equal(statistichePeriodo([0, 10], 0, 0).rendimentoPct, null);
});

test('rendimentoAllaData: base all\'ultima data <= da; null se il benchmark parte dopo', () => {
  const d = ['2026-05-08', '2026-05-11', '2026-05-12'];
  assert.ok(close(rendimentoAllaData(d, [100, 102, 105], '2026-05-11'), (105 / 102 - 1) * 100));
  assert.ok(close(rendimentoAllaData(d, [100, 102, 105], '2026-05-10'), 5));
  assert.equal(rendimentoAllaData(d, [100, 102, 105], '2026-05-01'), null);
  assert.equal(rendimentoAllaData([], [], '2026-05-01'), null);
});

test('sottAcqua e mediaMobile', () => {
  assert.deepEqual(sottAcqua([100, 120, 90, 120, 130]).map(v => Math.round(v * 100) / 100), [0, 0, -25, 0, 0]);
  assert.deepEqual(mediaMobile([1, 2, 3, 4], 2), [null, 1.5, 2.5, 3.5]);
});

test('rendimentiMensili: TWR fine mese su fine mese precedente, YTD sulla stessa base', () => {
  const m = rendimentiMensili(['2026-05-08', '2026-05-29', '2026-06-30', '2026-07-01'], [100, 110, 121, 108.9]);
  assert.ok(m);
  assert.equal(m!.ultimoMese, '2026-07');
  assert.ok(close(m!.anni[0].celle[4], 10));
  assert.ok(close(m!.anni[0].celle[5], 10));
  assert.ok(close(m!.anni[0].celle[6], -10, 1e-9));
  assert.ok(close(m!.anni[0].ytd, 8.9, 1e-9));
  assert.equal(m!.anni[0].celle[0], null);
});

test('pnlGiornalieri: il versamento non è un guadagno (V_t − V_{t−1} − F_t)', () => {
  const p = pnlGiornalieri(['a', 'b', 'c'], [1000, 1600, 1550], [0, 500, 0], [100, 110, 110 * 1550 / 1600]).sedute;
  assert.deepEqual(p.map(x => ({ data: x.data, eur: Math.round(x.eur * 1e6) / 1e6 })), [{ data: 'b', eur: 100 }, { data: 'c', eur: -50 }]);
  assert.deepEqual(pnlGiornalieri(['a', 'b'], [1000, Number.NaN], [0, 0], [100, 101]).sedute, []);
});

test('pnlGiornalieri: un flusso mancante esclude il giorno e lo dichiara, mai 0 (il versamento non diventa P&L)', () => {
  // giorno b: versamento di 500 con flusso illeggibile; con «?? 0» risultava una seduta da +545 €
  const r = pnlGiornalieri(['a', 'b', 'c'], [1000, 1600, 1550], [0, null as unknown as number, 0], [100, 110, 110 * 1550 / 1600]);
  assert.deepEqual(r.sedute.map(x => x.data), ['c']);
  assert.deepEqual(r.senzaFlusso, ['b']);
  assert.equal(r.disallineata, false);
  // serie dei flussi più corta delle date (o assente): nessun giorno è attribuibile
  const corta = pnlGiornalieri(['a', 'b', 'c'], [1000, 1600, 1550], [0, 500], [100, 110, 108]);
  assert.equal(corta.disallineata, true); assert.deepEqual(corta.sedute, []);
  assert.equal(pnlGiornalieri(['a', 'b'], [1000, 1010], undefined, [100, 101]).disallineata, true);
});

test('istogramma: classi contigue che contano ogni seduta una volta', () => {
  const v = [-238, -170, -20, 0, 15, 254];
  const { classi, passo } = istogramma(v, 25);
  assert.equal(passo, 25);
  assert.equal(classi.reduce((s, c) => s + c.n, 0), v.length);
  for (let i = 1; i < classi.length; i++) assert.equal(classi[i].da, classi[i - 1].a);
  assert.ok(classi[0].da <= -238 && classi[classi.length - 1].a > 254);
  assert.ok(istogramma(v).passo > 0);
  assert.deepEqual(istogramma([]).classi, []);
});

test('sintesiDistribuzione: sedute oltre il VaR, attese al 5%, peggiore e migliore', () => {
  const s = sintesiDistribuzione([{ data: 'a', eur: -200 }, { data: 'b', eur: -100 }, { data: 'c', eur: 50 }], 166);
  assert.equal(s.sedute, 3); assert.equal(s.oltreVar, 1); assert.equal(s.attese, 0);
  assert.deepEqual(s.peggiore, { data: 'a', eur: -200 }); assert.deepEqual(s.migliore, { data: 'c', eur: 50 });
  assert.equal(sintesiDistribuzione([], null).peggiore, null);
});

test('sintesiDistribuzione: senza VaR il conteggio oltre il VaR è n.d. (null), non 0', () => {
  const s = sintesiDistribuzione([{ data: 'a', eur: -200 }, { data: 'b', eur: 50 }], null);
  assert.equal(s.oltreVar, null);
  assert.equal(s.sedute, 2);
});


test('pnlGiornalieri: il passaggio ricostruita → ufficiale non è una seduta in utile (rendimento dall\'indice)', () => {
  // la serie ricostruita esclude la cassa, lo snapshot ufficiale la include: il backend mette r = 0 sul salto
  const p = pnlGiornalieri(['a', 'b', 'c', 'd'], [1000, 1010, 1500, 1515], [0, 0, 0, 0], [200, 202, 202, 204.02]).sedute;
  assert.deepEqual(p.map(x => Math.round(x.eur * 1e6) / 1e6), [10, 0, 15]);
});

test('statistichePeriodo: Sharpe come il backend ((CAGR − rf) / σ annua, span ≥ 20 giorni) e drawdown di oggi nel periodo', () => {
  const date: string[] = []; for (let d = new Date(Date.UTC(2026, 4, 8)); date.length < 41; d.setUTCDate(d.getUTCDate() + 1)) { const w = d.getUTCDay(); if (w && w < 6) date.push(d.toISOString().slice(0, 10)); }
  const indice = date.map((_, i) => 100 * Math.pow(1.001, i) * (i % 2 ? 1.002 : 1));
  const r = indice.slice(1).map((v, i) => v / indice[i] - 1);
  const m = r.reduce((a, b) => a + b, 0) / r.length;
  const vol = Math.sqrt(r.reduce((a, b) => a + (b - m) ** 2, 0) / (r.length - 1)) * Math.sqrt(252);
  const span = (Date.parse(date[40]) - Date.parse(date[0])) / 864e5;
  const cagr = Math.pow(indice[40] / indice[0], 365.25 / span) - 1;
  assert.ok(close(statistichePeriodo(indice, 0, 0.032, date).sharpe, (cagr - 0.032) / vol, 1e-9));
  assert.equal(statistichePeriodo(indice.slice(0, 15), 0, 0.032, date.slice(0, 15)).sharpe, null);
  assert.ok(close(statistichePeriodo([100, 110, 99, 104.5], 0, 0, ['a', 'b', 'c', 'd']).ddOggiPct, (104.5 / 110 - 1) * 100));
});

test('rendimentiMensili: un benchmark che parte dopo l\'inizio richiesto non inventa il primo mese né l\'anno', () => {
  const m = rendimentiMensili(['2026-05-20', '2026-05-29', '2026-06-30'], [100, 102, 103], '2026-05-08');
  assert.equal(m!.anni[0].celle[4], null);
  assert.ok(close(m!.anni[0].celle[5], (103 / 102 - 1) * 100));
  assert.equal(m!.anni[0].ytd, null);
  assert.ok(close(rendimentiMensili(['2026-05-08', '2026-05-29'], [100, 102], '2026-05-08')!.anni[0].celle[4], 2));
});

test('RichiesteUltime: una richiesta per volta per scenario, vince solo l\'ultima', () => {
  const q = new RichiesteUltime<string>();
  const a = q.apri('none');
  assert.equal(q.inVolo('none'), true);
  const b = q.apri('none', true); // forzata: sostituisce la precedente
  assert.equal(q.chiudi('none', a), false, 'risposta vecchia scartata');
  assert.equal(q.chiudi('none', b), true);
  assert.equal(q.inVolo('none'), false);
  assert.equal(q.apri('gfc') > 0, true);
  assert.equal(q.apri('gfc'), null, 'non forzata mentre è in volo: nessun doppione');
});

// 05/10/2026: interruttore % / € sul riepilogo. Gli euro misurano la stessa finestra della percentuale.
test('pnlPeriodo: somma il P&L delle sedute dopo la base, al netto dei versamenti', () => {
  const date = ['a', 'b', 'c', 'd'], valori = [1000, 1600, 1550, 1600], flussi = [0, 500, 0, 0];
  const indice = [100, 110, 110 * 1550 / 1600, 110 * 1600 / 1600];
  assert.ok(close(pnlPeriodo(date, valori, flussi, indice, 0).eur, 100 - 50 + 50, 1e-6));
  assert.ok(close(pnlPeriodo(date, valori, flussi, indice, 1).eur, 0, 1e-6)); // solo c e d: −50 + 50
  assert.ok(close(pnlPeriodo(date, valori, flussi, indice, 2).eur, 50, 1e-6));
  assert.equal(pnlPeriodo(date, valori, flussi, indice, 3).eur, null, 'nessuna seduta dopo la base: n.d., non 0');
  assert.deepEqual(pnlPeriodo(date, valori, [0, 500], indice, 0), { eur: null, esclusi: 0 }, 'flussi disallineati');
});

test('pnlPeriodo: le sedute senza flusso sono escluse e contate, solo dentro la finestra', () => {
  const r = pnlPeriodo(['a', 'b', 'c'], [1000, 1600, 1550], [0, null as unknown as number, 0], [100, 110, 110 * 1550 / 1600], 0);
  assert.ok(close(r.eur, -50, 1e-6)); assert.equal(r.esclusi, 1);
  assert.equal(pnlPeriodo(['a', 'b', 'c'], [1000, 1600, 1550], [0, null as unknown as number, 0], [100, 110, 110 * 1550 / 1600], 1).esclusi, 0);
});

test('pnlTotale: non realizzato + realizzato + dividendi, n.d. se una voce manca', () => {
  assert.ok(close(pnlTotale(-40.5, 210.25, 12.75), 182.5, 1e-9));
  assert.equal(pnlTotale(null, 210.25, 12.75), null);
  assert.equal(pnlTotale(-40.5, 210.25, Number.NaN), null);
});
