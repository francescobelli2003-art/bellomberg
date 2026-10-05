const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');

ambienteBrowser();

function page(relative, seed = {}) {
  let state = 0;
  const hooks = { ...React, useEffect() {}, useRef: current => ({ current }),
    useState(initial) { const i = state++; return [i in seed ? seed[i] : typeof initial === 'function' ? initial() : initial, () => {}]; } };
  const carica = creaCaricatore({ stub: {
    react: hooks, 'react-router-dom': { useNavigate: () => () => {} },
    '@/lib/api': { Bellomberg: {} },
  } });
  const lingua = carica('i18n/lingua.ts');
  const Page = carica(relative).default;
  return (selected, props) => {
    state = 0;
    lingua.impostaLinguaCorrente(selected);
    return renderToStaticMarkup(React.createElement(Page, props));
  };
}

// Preferiti Nuova (05/10/2026): stati della pagina nell'ordine di WatchlistPage
// (0 favs, 1 quotes, 2 quoteErr, 3 loading, 4 err, 5 notes, ...).
test('Watchlist switches labels and numeric locale while preserving notes and ticker', () => {
  const original = 'Nota originale: non tradurre queste parole';
  const render = page('pages/WatchlistPage.tsx', {
    0: [{ ticker: 'SYNTH.X', name: 'Synthetic issuer', note: original }],
    1: { 'SYNTH.X': { ticker: 'SYNTH.X', name: 'Synthetic issuer', price: 12345.67, prev_close: 9000, currency: 'EUR' } },
    5: { 'SYNTH.X': original },
  });
  const it = render('it'), en = render('en'), again = render('it');
  assert.match(it, /seguito dal Consigliere/);
  assert.match(en, /followed by the Committee/);
  assert.match(it, /12\.345,67/);
  assert.match(en, /12,345\.67/);
  for (const html of [it, en, again]) {
    assert.match(html, /SYNTH\.X/);
    assert.ok(html.includes(original));
    assert.doesNotMatch(html, /⟦/);
  }
  assert.equal(again, it);
});

// 13/09 (Claude Opus 5): il conteggio dei preferiti in errore era il letterale italiano 'N.D.' anche in EN.
test('a failed watchlist read declares the favorite count unavailable in the page language', () => {
  const render = page('pages/WatchlistPage.tsx', { 4: 'Original favorites error' });
  const it = render('it'), en = render('en');
  assert.match(it, /Preferiti n\.d\./); assert.match(en, /Favorites N\/A/);
  assert.doesNotMatch(en, /n\.d\./i);
  assert.doesNotMatch(it, /0 titoli/); assert.doesNotMatch(en, /No favorites/);
  for (const html of [it, en]) assert.ok(html.includes('Original favorites error'));
});

test('Market overview resolves country labels on each language render', () => {
  const render = page('pages/MarketPage.tsx');
  const it = render('it'), en = render('en');
  assert.match(it, /Azioni per paese/);
  assert.match(en, /Equities by country/);
  assert.match(it, /Germania/);
  assert.match(en, /Germany/);
  assert.doesNotMatch(en, /Azioni per paese|Germania|⟦ui\./);
});

test('Chart controls translate labels without changing provider range and interval IDs', () => {
  const render = page('components/TvChartPanel.tsx');
  const it = render('it', { ticker: 'SYNTH.X' }), en = render('en', { ticker: 'SYNTH.X' });
  assert.match(it, /CANDELE/);
  assert.match(en, /CANDLES/);
  assert.match(it, />1G</);
  assert.match(en, />1D</);
  assert.match(en, /loading SYNTH\.X/);
  assert.doesNotMatch(en, /⟦ui\./);
});
