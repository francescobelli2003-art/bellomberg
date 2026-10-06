// 04/10/2026 (G9b): vista Rischio renderizzata lato server con dati sintetici (ZZTEST/ACME).
// Prova il CABLAGGIO, non solo i calcoli: tabella Liquidabilità ripristinata, sedute senza flusso
// dichiarate, conteggio oltre il VaR non inventato quando il VaR manca.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');

ambienteBrowser();
globalThis.window = globalThis.window || { setTimeout: () => 0, clearTimeout() {}, dispatchEvent() {}, addEventListener() {}, removeEventListener() {} };
const load = creaCaricatore({ stub: { '@/lib/api': { Bellomberg: {} } } });
const language = load('i18n/lingua.ts');
const VistaRischio = load('pages/performance/VistaRischio.tsx').default;
const { pnlGiornalieri } = load('pages/performance/calcoli.ts');

const attesa = { stato: 'attesa' };
const scenari = { none: attesa, shock_3sigma: attesa, gfc_2008: attesa, covid_2020: attesa };
const liquidita = { stato: 'ok', dati: {
  items: [
    { ticker: 'ZZTEST', position_eur: 12000, avg_daily_volume_eur: 1500, days_to_liquidate: 40, score: 'red' },
    { ticker: 'ACME.MI', position_eur: 3000, avg_daily_volume_eur: 900000, days_to_liquidate: 0.02, score: 'green' },
    { ticker: 'NOVA.DE', position_eur: 2000, days_to_liquidate: null, score: 'unknown', reason: 'storico yfinance assente' },
  ],
  n_green: 1, n_yellow: 0, n_red: 1, threshold_green_days: 1, threshold_yellow_days: 5,
  assumption_pct_of_volume: 0.2, note: 'nota sintetica del servizio',
} };
// rischio senza VaR: il conteggio delle sedute oltre il VaR non è misurabile
const rischio = { stato: 'ok', dati: { lookback_days: 252, portfolio: { var_95_1d_eur: null, var_99_1d_eur: null, vol_annual_pct: 20 } } };
const date = ['2026-01-02', '2026-01-05', '2026-01-06', '2026-01-07', '2026-01-08'];
const pnl = { stato: 'ok', dati: pnlGiornalieri(date, [1000, 1010, 1520, 1500, 1510], [0, 0, null, 0, 0], [100, 101, 101.66, 99.66, 100.33]) };

function render(lang, props = {}) {
  language.impostaLinguaCorrente(lang);
  return renderToStaticMarkup(React.createElement(VistaRischio, {
    rischio, pnl, contributi: attesa, concentrazione: attesa, drawdown: attesa, liquidita, scenari,
    onRiprovaScenario() {}, nomi: {}, ...props,
  }));
}

test('Rischio: tabella Liquidabilità con giorni, semaforo, motivo delle righe non misurate e nota del servizio', () => {
  const html = render('it');
  assert.match(html, /data-qa="perf-liquidity"/);
  assert.match(html, /Liquidabilità/);
  assert.match(html, /ZZTEST/); assert.match(html, /40,00 g/); assert.match(html, /rosso/);
  assert.match(html, /NOVA\.DE/); assert.match(html, /non misurata/); assert.match(html, /storico yfinance assente/);
  assert.match(html, /Nota del servizio: nota sintetica del servizio/);
  const en = render('en');
  assert.match(en, /Liquidity/); assert.match(en, /Days to liquidate/); assert.match(en, /not measured/);
  language.impostaLinguaCorrente('it');
});

test('Rischio: liquidità in errore = errore dichiarato col testo della fonte, non tabella vuota', () => {
  const html = render('it', { liquidita: { stato: 'errore', testo: 'yfinance non disponibile' } });
  assert.match(html, /perf-liquidity/);
  assert.match(html, /yfinance non disponibile/);
});

test('Rischio: seduta senza flusso esclusa e dichiarata; senza VaR nessun «0 sedute oltre il VaR»', () => {
  const html = render('it');
  assert.match(html, /1 sedute escluse: flusso di cassa del giorno mancante/);
  assert.doesNotMatch(html, /0 hanno superato il VaR/);
  assert.match(html, /non è misurabile \(VaR n\.d\.\)/);
  const en = render('en');
  assert.match(en, /1 sessions excluded/);
  assert.doesNotMatch(en, /0 exceeded the 95% VaR/);
  language.impostaLinguaCorrente('it');
});

// Revisione G9b R5: volume non misurato = n.d. (non «0 €»), contatori assenti = n.d. (non «undefined»).
test('R5: liquidity volume zero on an unmeasured row is n/a, missing counters are n/a', () => {
  const html = render('it', { liquidita: { stato: 'ok', dati: {
    items: [{ ticker: 'ZZTEST', position_eur: 1000, avg_daily_volume_eur: 0, days_to_liquidate: null, score: 'unknown' },
      { ticker: 'ACME.MI', position_eur: 500, avg_daily_volume_eur: 0, days_to_liquidate: 0, score: 'green' }],
    threshold_green_days: 1, threshold_yellow_days: 5, assumption_pct_of_volume: 0.2, note: '' } } });
  const riga = html.slice(html.indexOf('ZZTEST'), html.indexOf('ACME.MI'));
  assert.doesNotMatch(riga, />0 €</);
  assert.match(riga, /n\.d\./);
  assert.match(riga, /volume medio nullo o assente/);
  const acme = html.slice(html.indexOf('ACME.MI'));
  assert.match(acme, />0 €</, 'a 0 on a measured row stays the datum it is');
  assert.doesNotMatch(html, /undefined/);
  assert.match(html, /n\.d\. verde/);
});

// Review del maintainer (06/10): l'esito del replay ha il SEGNO (window_loss_*: negativo =
// perdita). Rosso solo la perdita, verde il guadagno, nessun colore se manca. Numeri inventati interi.
test('Rischio: replay colorato e firmato secondo il segno, etichetta coerente', () => {
  const replay = meta => ({ stato: 'ok', dati: { stress_meta: { applied: 'gfc_2008', replaced_days: 21, ...meta } } });
  const scheda = (html, id) => { const from = html.indexOf(`data-scenario="${id}"`); assert.ok(from > 0, id);
    const to = html.indexOf('data-qa="perf-scenario"', from + 1); return html.slice(from, to > 0 ? to : undefined); };
  const html = render('it', { scenari: { ...scenari,
    gfc_2008: replay({ window_loss_pct: -40, window_loss_eur: -400 }),
    covid_2020: replay({ window_loss_pct: 12, window_loss_eur: 150 }) } });
  const perdita = scheda(html, 'gfc_2008'), guadagno = scheda(html, 'covid_2020');
  assert.match(perdita, /data-replay-sign="loss"/); assert.match(perdita, /Perdita se si ripetesse/);
  assert.match(perdita, /<b class="down-t">−400 €<\/b><small>−40,00%<\/small>/);
  assert.match(guadagno, /data-replay-sign="gain"/); assert.match(guadagno, /Guadagno se si ripetesse/);
  assert.match(guadagno, /<b class="up-t">\+150 €<\/b><small>\+12,00%<\/small>/);
  assert.doesNotMatch(guadagno, /down-t/);
  // esito assente: nessun colore, segnaposto della pagina, etichetta neutra
  const vuoto = scheda(render('it', { scenari: { ...scenari, gfc_2008: replay({}) } }), 'gfc_2008');
  assert.match(vuoto, /data-replay-sign="na"/); assert.match(vuoto, /Esito se si ripetesse/);
  assert.match(vuoto, /<b>—<\/b><small>—<\/small>/); assert.doesNotMatch(vuoto, /down-t|up-t/);
  const en = scheda(render('en', { scenari: { ...scenari, covid_2020: replay({ window_loss_pct: 12, window_loss_eur: 150 }) } }), 'covid_2020');
  assert.match(en, /Gain if it happened again/);
  language.impostaLinguaCorrente('it');
});
