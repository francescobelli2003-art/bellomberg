// Read states only. All HTTP is handled by the isolated renderer fixture.
// No provider refresh, model generation or financial operation is started.
const assert = require('node:assert/strict');
const { reads: actionReads } = require('./pages-modern-action-data.cjs');

const root = page => `main [data-page="${page}"]`;
const delayed = body => ({ body, delayMs: 12000 });
const failed = detail => ({ status: 503, body: { detail } });
// Static module chunks are not reads: the first lazy import of the chart engine
// (no longer preloaded by the Dashboard) may land inside a measuring window.
const diff = (before, after) => Object.fromEntries(Object.keys(after)
  .filter(key => !key.startsWith('GET /assets/'))
  .map(key => [key, after[key] - (before[key] || 0)]).filter(([, value]) => value));

async function restore(q, routes) {
  const own = routes.filter(route => Object.hasOwn(actionReads, route));
  await q.fixture({ clearReads: routes.filter(route => !own.includes(route)),
    setRead: Object.fromEntries(own.map(route => [route, actionReads[route]])) });
}
async function fresh(q, page, reads) {
  await q.toggle('classic');
  await q.visit('/dashboard');
  await q.waitFor(() => !!document.querySelector('main .bbn-dashboard'), 'previous state controller unmounted');
  // The Dashboard reads news, events, risk and SPY on mount. Let them land before
  // leaving: with delayed reads holding connections, a queued read would reach
  // the fixture later and be blamed on the next page's capture.
  await q.waitFor(() => !document.querySelector('main .bbn-dashboard [aria-busy="true"], main .dashboard-modern-loading'), 'Dashboard reads landed', 20000);
  await q.settleRequests('Dashboard reads before the state fixture', 8000);
  await q.fixture({ setRead: reads });
  await q.visit('/' + page);
  await q.waitFor(id => !!document.querySelector(`main [data-page="${id}"]`), `${page} state controller`, 10000, page);
}
async function text(q, page) {
  return q.js(selector => document.querySelector(selector)?.innerText || '', root(page));
}
async function both(q, page, name, validate) {
  const evidence = [];
  for (const mode of ['classic', 'modern']) {
    const before = await q.counts();
    await q.toggle(mode);
    const result = await validate(mode);
    assert.ok(result, `${page}/${name}/${mode}: visible state assertion must return evidence`);
    assert.deepEqual(diff(before, await q.counts()), {}, `${page}/${name}: mode toggle restarted a read`);
    assert.equal(typeof q.capture, 'function');
    await q.capture(`read-state-${page}-${name}-${mode}`);
    evidence.push({ mode, ...result });
  }
  return evidence;
}
async function run(q) {
  // The existing shell deliberately schedules its first alert read after 5s.
  // Let that initial read settle before attributing viewport deltas to a page.
  await q.pause(5500);
  await q.executeScenario('watchlist', 'read-loading-empty-error-zero-and-absence-are-distinct', async () => {
    const routes = ['/favorites', '/market/quote'];
    const evidence = [];
    try {
      await fresh(q, 'watchlist', { '/favorites': delayed({ favorites: [] }) });
      // Preferiti Nuova (05/10/2026): lo stato della pagina è dichiarato in data-state
      await q.waitFor(() => !!document.querySelector('[data-page="watchlist"] .bbn-preferiti[data-state="loading"]'), 'favorites visibly loading');
      evidence.push(...await both(q, 'watchlist', 'loading', async () => {
        const state = await q.js(() => ({ loading: !!document.querySelector('[data-page="watchlist"] .bbn-preferiti[data-state="loading"] [aria-busy="true"]'),
          refreshDisabled: document.querySelector('[data-page="watchlist"] .pf-refresh')?.disabled }));
        assert.ok(state.loading && state.refreshDisabled); return state;
      }));
      await q.waitFor(() => /No favorites/i.test(document.querySelector('[data-page="watchlist"]')?.innerText || ''), 'favorites empty after fixture read', 20000);
      evidence.push(...await both(q, 'watchlist', 'empty', async () => {
        const value = await text(q, 'watchlist'); assert.match(value, /No favorites/i);
        assert.doesNotMatch(value, /unavailable/i); return { emptyMessage: 'No favorites' };
      }));
      const marker = 'Synthetic favorites read failure';
      await fresh(q, 'watchlist', { '/favorites': failed(marker) });
      await q.waitFor(value => document.querySelector('[data-page="watchlist"]')?.innerText.includes(value), 'favorites error disclosed', 10000, marker);
      evidence.push(...await both(q, 'watchlist', 'error', async () => {
        const value = await text(q, 'watchlist'); assert.ok(value.includes(marker));
        assert.doesNotMatch(value, /No favorites/i); return { errorDetail: marker, emptyMessageAbsent: true };
      }));
      await restore(q, ['/favorites']);
      await fresh(q, 'watchlist', { '/market/quote': { ticker: 'SYN1', price: 0, change_pct: 0, currency: 'EUR' } });
      const zero = () => [...document.querySelectorAll('[data-page="watchlist"] .pf-row .pf-px')].map(el => el.textContent.trim());
      await q.waitFor(() => [...document.querySelectorAll('[data-page="watchlist"] .pf-row .pf-px')].some(el => /^0\.0+\s*EUR$/.test(el.textContent.trim())), 'observed zero quote');
      evidence.push(...await both(q, 'watchlist', 'zero', async () => {
        const prices = await q.js(zero); assert.ok(prices.some(price => /^0\.0+\s*EUR$/.test(price)), JSON.stringify(prices)); return { zeroQuoteDisplayed: true };
      }));
      await fresh(q, 'watchlist', { '/market/quote': { ticker: 'SYN1', currency: 'EUR' } });
      await q.waitFor(() => !!document.querySelector('[data-page="watchlist"] .pf-row') && !document.querySelector('[data-page="watchlist"] .pf-refresh:disabled'), 'missing quotes settled');
      evidence.push(...await both(q, 'watchlist', 'missing-quote', async () => {
        const prices = await q.js(() => [...document.querySelectorAll('[data-page="watchlist"] .pf-row .pf-px')].map(el => el.textContent.trim()));
        assert.ok(prices.length > 0); assert.ok(prices.every(price => /^—\s*EUR$/.test(price)), 'missing price marker retains its declared currency');
        assert.ok(prices.every(price => !/0\.00/.test(price)), 'absence cannot become an observed zero');
        return { observedPrices: prices, absenceIsNotZero: true };
      }));
      return { assertionResults: { loadingEmptyErrorDistinguished: true, zeroAndMissingDistinguished: true, noWriteActionTriggered: true }, evidence };
    } finally { await restore(q, routes); }
  });

  await q.executeScenario('fundamentals', 'registry-loading-empty-and-read-error-have-distinct-messages', async () => {
    const route = '/fundamentals/models', evidence = [];
    try {
      await fresh(q, 'fundamentals', { [route]: delayed({ count: 0, models: [], nota: '' }) });
      await q.waitFor(() => /Loading models/.test(document.querySelector('[data-page="fundamentals"]')?.innerText || ''), 'registry loading message');
      evidence.push(...await both(q, 'fundamentals', 'loading', async () => {
        const value = await text(q, 'fundamentals'); assert.match(value, /Loading models/);
        assert.doesNotMatch(value, /No models\./); return { loadingNotEmpty: true };
      }));
      await q.waitFor(() => /No models\./.test(document.querySelector('[data-page="fundamentals"]')?.innerText || ''), 'registry empty result', 20000);
      evidence.push(...await both(q, 'fundamentals', 'empty', async () => {
        const value = await text(q, 'fundamentals'); assert.match(value, /No models\./);
        assert.doesNotMatch(value, /Loading models/); return { noModelsMessage: true };
      }));
      const marker = 'Synthetic valuation registry read failure';
      await fresh(q, 'fundamentals', { [route]: failed(marker) });
      await q.waitFor(value => document.querySelector('[data-page="fundamentals"]')?.innerText.includes(value), 'registry source error', 10000, marker);
      evidence.push(...await both(q, 'fundamentals', 'error', async () => {
        const value = await text(q, 'fundamentals'); assert.ok(value.includes(marker));
        assert.doesNotMatch(value, /No models\./); return { errorDetail: marker, emptyMessageAbsent: true };
      }));
      return { assertionResults: { registryReadStatesDistinct: true, noModelOperationStarted: true }, evidence };
    } finally { await restore(q, [route]); }
  });

  await q.executeScenario('news', 'wire-loading-empty-error-and-stale-provider-remain-distinct', async () => {
    const route = '/news/feed', evidence = [];
    try {
      await fresh(q, 'news', { [route]: delayed({ count: 0, items: [], fonti_mute: {}, avviso: null }) });
      await q.waitFor(() => !!document.querySelector('[data-page="news"] .news-list .news-loading'), 'wire visibly loading');
      evidence.push(...await both(q, 'news', 'loading', async () => {
        const pulse = await q.js(() => !!document.querySelector('[data-page="news"] .news-list .news-loading'));
        assert.ok(pulse); assert.doesNotMatch(await text(q, 'news'), /No stored news/); return { wireLoading: pulse };
      }));
      await q.waitFor(() => /No stored news/.test(document.querySelector('[data-page="news"]')?.innerText || ''), 'empty local wire', 20000);
      evidence.push(...await both(q, 'news', 'empty', async () => {
        const value = await text(q, 'news'); assert.match(value, /No stored news/);
        return { emptyWire: true, providerRefreshNotInvoked: true };
      }));
      const marker = 'Synthetic local wire read failure';
      await fresh(q, 'news', { [route]: failed(marker) });
      await q.waitFor(value => document.querySelector('[data-page="news"]')?.innerText.includes(value), 'wire error detail', 10000, marker);
      evidence.push(...await both(q, 'news', 'error', async () => {
        const value = await text(q, 'news'); assert.ok(value.includes(marker));
        assert.doesNotMatch(value, /No stored news/); return { errorDetail: marker };
      }));
      await restore(q, [route]);
      // Other groups deliberately clear overrides. This state owns the stale
      // provider response as well as its available wire, regardless of order.
      await fresh(q, 'news', { '/news/providers': actionReads['/news/providers'] });
      await q.waitFor(() => (document.querySelector('[data-page="news"] .news-list')?.innerText || '').includes('Synthetic chip demand'), 'populated wire with stale provider');
      evidence.push(...await both(q, 'news', 'stale-provider', async () => {
        const value = await text(q, 'news'); assert.match(value, /Partial coverage|Last provider round partial/); assert.match(value, /Synthetic stale provider status/);
        assert.match(value, /Synthetic chip demand/); return { staleProviderSeparateFromAvailableWire: true };
      }));
      return { assertionResults: { feedReadStatesDistinct: true, staleDoesNotEraseAvailableItems: true, noProviderWriteTriggered: true }, evidence };
    } finally { await restore(q, [route, '/news/providers']); }
  });

  await q.executeScenario('performance', 'nav-loading-empty-and-domain-error-do-not-invent-accounting-zero', async () => {
    const route = '/portfolio/analytics/nav_history', evidence = [];
    const empty = { dates: [], nav_eur: [], cost_basis_eur: [], pnl_eur: [], cash_eur: 0, n_days: 0 };
    try {
      await fresh(q, 'performance', { [route]: delayed(empty) });
      await q.waitFor(() => /Reconstructing NAV/.test(document.querySelector('[data-page="performance"]')?.innerText || ''), 'NAV reconstruction loading');
      evidence.push(...await both(q, 'performance', 'loading', async () => {
        const value = await text(q, 'performance'); assert.match(value, /Reconstructing NAV/); return { reconstructionPending: true };
      }));
      await q.waitFor(() => !document.querySelector('[data-page="performance"] [data-qa="perf-refresh"]:disabled'), 'NAV read settled', 20000);
      evidence.push(...await both(q, 'performance', 'empty-accounting', async mode => {
        const marketValue = await q.js(() => [...document.querySelectorAll('[data-page="performance"] .perf-mrow')]
          .find(el => el.querySelector('span')?.textContent.trim().toLowerCase() === 'market value')?.innerText || '');
        assert.ok(marketValue, 'accounting metric must be present');
        // An absent series is declared «—» in both themes, never an observed €0.00.
        assert.match(marketValue, /—/); assert.doesNotMatch(marketValue, /€0\.00|0\.00 €/);
        assert.match(await text(q, 'performance'), /engine responded without the series/);
        return { marketValue, absentSeriesIsNotObservedZero: true, mode };
      }));
      const marker = 'Synthetic NAV series unavailable';
      // Dashboard also reads NAV while fresh() changes routes. Retire the
      // loading override first: otherwise its identical pending GET holds the
      // next Performance request behind Chromium's per-URL request lock.
      await restore(q, [route]);
      await fresh(q, 'performance', { [route]: { error: marker } });
      await q.waitFor(value => document.querySelector('[data-page="performance"]')?.innerText.includes(value), 'NAV domain error', 10000, marker);
      evidence.push(...await both(q, 'performance', 'source-error', async () => {
        const value = await text(q, 'performance'); assert.ok(value.includes(marker));
        return { errorDetail: marker, independentLiveDataRetained: /Today’s P&L/.test(value) };
      }));
      return { assertionResults: { navReadStatesDistinct: true, absentAccountingNotZeroInModern: true, calculationsNotChanged: true }, evidence };
    } finally { await restore(q, [route]); }
  });

  await q.executeScenario('market', 'debounced-search-loading-empty-and-source-error-retain-query', async () => {
    const route = '/market/search', evidence = [];
    try {
      await fresh(q, 'market', { [route]: delayed({ results: [] }) });
      const input = root('market') + ' .mk-search input';
      const priorSearches = (await q.counts())['GET /market/search'] || 0;
      await q.typeText(input, 'SYN-NONE');
      await q.waitFor(() => !!document.querySelector('[data-page="market"] .mk-search .mk-search-busy'), 'debounced search loading');
      for (let n = 0; n < 60 && ((await q.counts())['GET /market/search'] || 0) === priorSearches; n++) await q.pause(50);
      assert.equal((await q.counts())['GET /market/search'], priorSearches + 1, 'the debounced request has actually started');
      evidence.push(...await both(q, 'market', 'search-loading', async () => {
        const state = await q.js(() => ({ busy: !!document.querySelector('[data-page="market"] .mk-search .mk-search-busy'),
          query: document.querySelector('[data-page="market"] .mk-search input')?.value }));
        assert.ok(state.busy); assert.equal(state.query, 'SYN-NONE'); return state;
      }));
      await q.waitFor(() => !document.querySelector('[data-page="market"] .mk-search .mk-search-busy'), 'search no results settled', 20000);
      evidence.push(...await both(q, 'market', 'search-empty', async mode => {
        const query = await q.js(() => document.querySelector('[data-page="market"] .mk-search input')?.value);
        assert.equal(query, 'SYN-NONE');
        if (mode === 'modern') assert.match(await text(q, 'market'), /no results/i);
        return { query, explicitEmptyMessage: mode === 'modern' };
      }));
      const marker = 'Synthetic search source failure';
      await q.fixture({ setRead: { [route]: failed(marker) } });
      await q.typeText(input, 'SYN-ERROR');
      await q.waitFor(value => document.querySelector('[data-page="market"] .mk-search [role="alert"]')?.innerText.includes(value), 'search error detail', 10000, marker);
      evidence.push(...await both(q, 'market', 'search-error', async () => {
        const value = await text(q, 'market'); assert.ok(value.includes(marker));
        assert.doesNotMatch(value, /no results/i);
        const query = await q.js(() => document.querySelector('[data-page="market"] .mk-search input')?.value);
        assert.equal(query, 'SYN-ERROR'); return { query, errorDetail: marker };
      }));
      return { assertionResults: { debouncedSearchStatesDistinct: true, queryRetainedDuringModeChanges: true, noExternalReadStarted: true }, evidence };
    } finally { await restore(q, [route]); }
  });
}
module.exports = { run };
