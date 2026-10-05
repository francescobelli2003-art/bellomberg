// Authenticated operations run only against the fail-closed synthetic server.
const assert = require('node:assert/strict');
const { reads, writes, model, now } = require('./pages-modern-action-data.cjs');
const clone = x => JSON.parse(JSON.stringify(x));
const delayed = body => ({ body, delayMs: 20000 });
const writesFor = async (q, route, method = 'POST') => (await q.snapshot()).requests.filter(r => r.route === route && r.method === method);
async function button(q, scope, label, name) {
  const found = await q.js((s, word, attr) => {
    const normalize = value => value.trim().replace(/^[^\p{Letter}\p{Number}]+/u, '').trim();
    const b = [...document.querySelectorAll(s + ' button')].find(el => normalize(el.textContent) === normalize(word));
    if (!b) return false; b.setAttribute(attr, 'true'); return true;
  }, scope, label, 'data-research-' + name);
  assert.ok(found, `Missing operational control ${label}`);
  return `[data-research-${name}="true"]`;
}
async function fresh(q, page) {
  await q.toggle('classic'); await q.visit('/dashboard');
  await q.waitFor(() => !!document.querySelector('main .bbn-dashboard'), 'previous research controller unmounted'); await q.pause(400);
  await q.visit('/' + page); await q.waitFor(p => !!document.querySelector(`main [data-page="${p}"]`), page + ' controller', 10000, page);
}
async function pending(q, selector, name, validate) {
  const before = await q.pageState();
  for (const mode of ['modern', 'classic']) {
    const counts = await q.counts(); await q.toggle(mode);
    assert.ok(await q.js(s => document.querySelector(s)?.disabled, selector), name + ' remains disabled during held write');
    await validate();
    assert.deepEqual(await q.counts(), counts, name + ' toggle sends no requests');
    await q.capture('research-' + name + '-pending-' + mode);
    assert.ok(await q.js(s => document.querySelector(s)?.disabled, selector), name + ' remains pending after all seven captures');
    await validate();
  }
  assert.equal((await q.pageState()).id, before.id, name + ' controller remains mounted');
}
async function reset(q, routes) {
  const own = routes.filter(r => Object.hasOwn(reads, r));
  await q.fixture({ clearReads: routes.filter(r => !own.includes(r)), setRead: Object.fromEntries(own.map(r => [r, reads[r]])), setWrite: writes });
}
async function runOperations(q) {
  await q.pause(5500); // Existing shell's first alert timeout settles before assertions.
  await q.executeScenario('watchlist', 'note-save-error-remove-and-keyboard-navigation', async () => {
    const route = '/favorites/SYN1/note';
    try {
      await fresh(q, 'watchlist');
      // Preferiti Nuova (05/10/2026): elenco + dettaglio; la nota è nel dettaglio del titolo scelto
      await q.waitFor(() => !!document.querySelector('[data-page="watchlist"] .pf-row[data-ticker="SYN1"]'), 'favorite list loaded');
      await q.click('[data-page="watchlist"] .pf-row[data-ticker="SYN1"]');
      await q.waitFor(() => !!document.querySelector('[data-page="watchlist"] [data-save-note="SYN1"]'), 'favorite note loaded');
      const textarea = '[data-page="watchlist"] .pf-nota textarea';
      const submit = '[data-page="watchlist"] [data-save-note="SYN1"]';
      const draft = 'Synthetic long PM draft: preserved across presentation changes.';
      await q.typeText(textarea, draft);
      await q.fixture({ setWrite: { [route]: delayed({ ok: true }) } });
      const initial = (await writesFor(q, route)).length;
      await q.click(submit); await q.waitFor(s => document.querySelector(s)?.disabled, 'note save busy', 5000, submit);
      await pending(q, submit, 'favorite-note', async () => assert.equal(await q.js(s => document.querySelector(s)?.value, textarea), draft));
      assert.equal((await writesFor(q, route)).length, initial + 1);
      assert.equal((await writesFor(q, route)).at(-1).query.note, draft);
      await q.waitFor(() => !!document.querySelector('[data-page="watchlist"] .pf-nota .pf-stato.is-ok'), 'note save completes', 30000);
      assert.match(await q.js(() => document.querySelector('[data-page="watchlist"] .pf-nota .pf-stato')?.textContent), /saved/i);
      await q.fixture({ setWrite: { [route]: { status: 503, body: { detail: 'Synthetic note persistence failure' } } } });
      await q.typeText(textarea, 'Synthetic retry draft'); await q.click(submit);
      await q.waitFor(() => /Synthetic note persistence failure/.test(document.querySelector('[data-page="watchlist"] .pf-nota [role="alert"]')?.textContent || ''), 'note error explicit');
      assert.equal(await q.js(s => document.querySelector(s)?.value, textarea), 'Synthetic retry draft');
      await q.toggle('modern');
      await q.capture('research-favorite-note-error');
      await q.fixture({ setWrite: { '/favorites/SYN1': { ok: true } } });
      await q.click('[data-page="watchlist"] [data-remove="SYN1"]');
      await q.waitFor(() => !document.querySelector('[data-page="watchlist"] .pf-row[data-ticker="SYN1"]'), 'favorite optimistic removal');
      assert.equal((await writesFor(q, '/favorites/SYN1', 'DELETE')).length, 1);
      const target = '[data-page="watchlist"] [data-open-market]';
      const next = await q.js(s => document.querySelector(s)?.dataset.openMarket || null, target);
      assert.ok(next && next !== 'SYN1', 'detail moved to another favorite after removal');
      await q.js(s => document.querySelector(s).focus(), target); await q.key('Enter');
      await q.waitFor(() => location.hash.startsWith('#/market'), 'favorite keyboard opens market');
      // Market consumes the one-shot session key on mount; assert the selected UI.
      await q.waitFor(t => document.querySelector('[data-page="market"] .mk-dtitle span')?.textContent.split(' · ')[0] === t, 'favorite ticker selected in market', 10000, next);
      assert.equal(await q.js(() => sessionStorage.getItem('bb:mktTicker')), null);
      return { assertionResults: { oneNoteWriteAcrossToggles: true, queryPayloadPreserved: true, errorRetainsDraft: true, deleteOnce: true, keyboardTickerNavigation: true }, allWritesSynthetic: true };
    } finally { await reset(q, []); }
  });

  await q.executeScenario('market', 'favorite-rollback', async () => {
    try {
      await q.js(() => sessionStorage.setItem('bb:mktTicker', 'SYN1'));
      await fresh(q, 'market'); await q.waitFor(() => !!document.querySelector('[data-page="market"] [data-azione="preferito"]:not(:disabled)'), 'security controls loaded');
      const favorite = await q.js(() => { const b = document.querySelector('[data-page="market"] [data-azione="preferito"]'); if (!b) return false; b.dataset.researchFavorite = 'true'; return b.textContent.trim(); });
      assert.ok(favorite);
      const priorDelete = (await writesFor(q, '/favorites/SYN1', 'DELETE')).length;
      const priorAdd = (await writesFor(q, '/favorites')).length;
      await q.fixture({ setWrite: { '/favorites/SYN1': { status: 503, body: { detail: 'Synthetic favorite delete failure' } } } });
      await q.click('[data-research-favorite]');
      await q.waitFor(() => /Synthetic favorite delete failure/.test(document.querySelector('[data-page="market"]')?.innerText || ''), 'favorite write error');
      assert.equal(await q.js(() => document.querySelector('[data-research-favorite]')?.textContent.trim()), favorite);
      await q.fixture({ setWrite: { '/favorites/SYN1': { ok: true }, '/favorites': { ok: true } } });
      await q.click('[data-research-favorite]'); await q.pause(180);
      await q.click('[data-research-favorite]'); await q.pause(180);
      assert.equal((await writesFor(q, '/favorites/SYN1', 'DELETE')).length, priorDelete + 2);
      assert.equal((await writesFor(q, '/favorites')).length, priorAdd + 1);
      await q.toggle('modern');
      await q.capture('research-market-favorite');
      return { assertionResults: { failedWriteRollsBackFavorite: true, removeAndAddOnce: true }, allWritesSynthetic: true };
    } finally { await reset(q, []); }
  });

  await q.executeScenario('fundamentals', 'refresh-lock-personal-copy-and-authenticated-download', async () => {
    const base = '/valuation/models/SYN1', registry = '/fundamentals/models';
    const workbook = model.current_download;
    const copy = { id: 'qa-root-personal', ticker: 'SYN1', source_generation: model.generation_id, label: 'Synthetic personal scenario', created_at: now, status: 'ready', available: true, modified: false };
    const routes = [registry, base + '/variants', workbook, base + '/variants/' + copy.id + '/workbook'];
    try {
      await q.fixture({ setRead: { [registry]: { count: 1, models: [clone(model)], nota: 'Synthetic operation fixture only.' }, [base + '/variants']: { variants: [] } } });
      await fresh(q, 'fundamentals');
      await q.waitFor(() => !!document.querySelector('[data-page="fundamentals"] input[aria-label="Personal variant name"]'), 'model operations loaded');
      const draft = 'Synthetic personal scenario';
      await q.typeText('[aria-label="Personal variant name"]', draft);
      const actions = [
        ['REFRESH MODEL', '/refresh', { status: 'queued', reason: 'Synthetic only' }],
        ['LOCK VERSION', '/lock', { locked: true }],
        ['CREATE VARIANT', '/variants', copy],
      ];
      const evidence = [];
      for (const [label, suffix, response] of actions) {
        const selector = await button(q, '[data-page="fundamentals"]', label, suffix.slice(1));
        if (suffix === '/variants') await q.typeText('[aria-label="Personal variant name"]', draft);
        if (suffix === '/variants') await q.fixture({ setRead: { [base + suffix]: { variants: [copy] } } });
        await q.fixture({ setWrite: { [base + suffix]: delayed(response) } });
        const before = (await writesFor(q, base + suffix)).length;
        await q.click(selector); await q.waitFor(s => document.querySelector(s)?.disabled, label + ' busy', 5000, selector);
        await pending(q, selector, 'model-' + suffix.slice(1), async () => assert.equal(await q.js(() => document.querySelector('[aria-label="Personal variant name"]')?.value), draft));
        assert.equal((await writesFor(q, base + suffix)).length, before + 1);
        const request = (await writesFor(q, base + suffix)).at(-1);
        if (suffix !== '/refresh') assert.equal(request.input.generation_id, model.generation_id);
        if (suffix === '/variants') assert.equal(request.input.label, draft);
        if (suffix !== '/lock') assert.ok(request.input.request_id);
        if (suffix === '/lock') assert.equal(request.input.locked, true);
        await q.waitFor(() => [...document.querySelectorAll('[data-page="fundamentals"] button')]
          .some(button => button.textContent.trim() === 'REFRESH MODEL' && !button.disabled), label + ' complete', 30000);
        await q.waitFor(() => !/Loading models/.test(document.querySelector('[data-page="fundamentals"]')?.innerText || ''), 'registry refresh finished');
        await q.pause(180);
        // Revision legitimately reloads variants and clears its completed draft.
        await q.typeText('[aria-label="Personal variant name"]', draft);
        evidence.push({ kind: suffix, input: request.input, once: true });
      }
      const download = await button(q, '[data-page="fundamentals"]', 'OPEN EXCEL', 'download');
      await q.fixture({ setRead: { [workbook]: { body: 'Synthetic workbook bytes', headers: { 'X-Valuation-Generation': 'wrong-generation' } } } });
      await q.click(download);
      await q.waitFor(() => /received generation does not match/.test(document.querySelector('[data-page="fundamentals"]')?.innerText || ''), 'mismatched workbook rejected');
      const priorDownloads = q.report.downloads.length;
      await q.fixture({ setRead: { [workbook]: { body: 'Synthetic workbook bytes', headers: { 'X-Valuation-Generation': model.generation_id } },
        [base + '/variants/' + copy.id + '/workbook']: { body: 'Synthetic personal workbook bytes', headers: { 'X-Valuation-Generation': model.generation_id } } } });
      await q.click(download);
      for (let n = 0; n < 60 && q.report.downloads.length === priorDownloads; n++) await q.pause(100);
      assert.equal(q.report.downloads.length, priorDownloads + 1); assert.equal(q.report.downloads.at(-1).state, 'completed');
      assert.ok(q.report.downloads.at(-1).bytes > 0);
      const personal = await button(q, '[data-page="fundamentals"]', 'OPEN VARIANT', 'personal-download'); await q.click(personal);
      for (let n = 0; n < 60 && q.report.downloads.length === priorDownloads + 1; n++) await q.pause(100);
      assert.equal(q.report.downloads.length, priorDownloads + 2); assert.equal(q.report.downloads.at(-1).state, 'completed');
      await q.toggle('modern');
      await q.capture('research-model-actions-personal-copy');
      return { assertionResults: { refreshLockVariantEachOnce: true, busyDraftAndControllerAcrossModes: true, generationMismatchRejected: true, twoDownloadsCompleteInIsolatedTemp: true }, evidence, allWritesSynthetic: true };
    } finally { await reset(q, routes); }
  });

  await runFiling(q);
}

// Filing page (phase E). AI only from explicit buttons: loading, polling, automatic selection
// and bulk activation never POST ai-proposal or refresh, and never GET ai-estimate.
const FP = 'main [data-page="filing"]';
const FORBIDDEN_FILING = /^\/filings\/[^/]+\/(?:ai-proposal|refresh|ai-estimate)$/;
// `since`: index in the fixture request log where the scenario started (earlier scenarios may press «Verifica ora»)
const requestMark = async q => (await q.snapshot()).requests.length;
const filingAiOrCheck = async (q, since) => (await q.snapshot()).requests.slice(since).filter(r => FORBIDDEN_FILING.test(r.route)
  && (r.method === 'POST' || (r.method === 'GET' && r.route.endsWith('/ai-estimate'))));
const readsOf = async (q, route, since = 0) => (await q.snapshot()).requests.slice(since).filter(r => r.method === 'GET' && r.route === route);
async function openFilingTitle(q, ticker) {
  await q.waitFor((root, t) => !!document.querySelector(`${root} [data-filing-ticker="${t}"]`), 'filing row ' + ticker, 10000, FP, ticker);
  await q.click(`${FP} [data-filing-ticker="${ticker}"]`);
  await q.waitFor((root, t) => !!document.querySelector(`${root} [data-filing-dettaglio="${t}"]`)
    && document.querySelector(`${root} [data-filing-ticker="${t}"]`)?.getAttribute('aria-pressed') === 'true', 'filing detail ' + ticker, 10000, FP, ticker);
}
const notice = (q, tone, pattern, label, timeout = 10000) => q.waitFor((root, t, source) =>
  new RegExp(source).test(document.querySelector(`${root} [data-filing-avviso="${t}"]`)?.textContent || ''), label, timeout, FP, tone, pattern.source);

async function runFiling(q) {
  // scenari lanciati da soli (BB_PAGES_ACTIONS_ONLY_SCENARIO): le risposte finte alle scritture servono da subito,
  // non solo dopo il reset del primo scenario (altrimenti 403 → sessione chiusa → login)
  await reset(q, []);
  await q.executeScenario('filing', 'validation-profile-save-and-manual-check', async () => {
    const profileRoute = '/filings/SYN1/profile', refreshRoute = '/filings/SYN1/refresh';
    const textarea = `${FP} [data-filing-input="profilo"]`, interval = `${FP} [data-filing-input="intervallo"]`;
    const save = `${FP} [data-filing-azione="salva-profilo"]`, check = `${FP} [data-filing-azione="verifica"]`;
    try {
      await fresh(q, 'filing');
      await openFilingTitle(q, 'SYN1');
      await q.click(`${FP} [data-filing-azione="profilo"]`);
      await q.waitFor(s => !!document.querySelector(s), 'filing profile editor', 5000, textarea);
      await q.typeText(textarea, '[]');
      const before = (await writesFor(q, profileRoute, 'PUT')).length;
      await q.click(save);
      await notice(q, 'bad', /Invalid JSON/, 'invalid profile JSON');
      assert.equal((await writesFor(q, profileRoute, 'PUT')).length, before);
      const json = JSON.stringify({ identity: { ticker: 'SYN1', fixture: true }, source: { url: 'https://example.invalid' }, sections: ['Synthetic note'] });
      await q.typeText(textarea, json); await q.typeText(interval, '0'); await q.click(save);
      await notice(q, 'bad', /Invalid interval/, 'invalid interval');
      assert.equal((await writesFor(q, profileRoute, 'PUT')).length, before);
      await q.typeText(interval, '24');
      await q.fixture({ setWrite: { [profileRoute]: delayed({ ok: true }) } }); await q.click(save);
      await q.waitFor(s => document.querySelector(s)?.disabled, 'filing save busy', 5000, save);
      await pending(q, save, 'filing-profile', async () => {
        assert.equal(await q.js(s => document.querySelector(s)?.value, textarea), json);
        assert.equal(await q.js(s => document.querySelector(s)?.value, interval), '24');
      });
      assert.equal((await writesFor(q, profileRoute, 'PUT')).length, before + 1);
      assert.deepEqual((await writesFor(q, profileRoute, 'PUT')).at(-1).input, { profile: JSON.parse(json), enabled: false, interval_hours: 24, qualitative_enabled: false });
      await notice(q, 'ok', /Profile saved/, 'filing profile saved', 30000);
      assert.equal(await q.js(s => document.querySelector(s)?.value, textarea), json, 'saved draft stays in the editor');
      assert.equal((await writesFor(q, refreshRoute)).length, 0, 'saving a profile never starts a check');
      await q.fixture({ setWrite: { [refreshRoute]: delayed({ run_id: 990, status: 'queued' }) } }); await q.click(check);
      await q.waitFor(s => document.querySelector(s)?.disabled, 'filing check busy', 5000, check);
      await pending(q, check, 'filing-check', async () =>
        assert.equal(await q.js(root => document.querySelector(root + ' [data-filing-dettaglio]')?.getAttribute('data-filing-dettaglio'), FP), 'SYN1'));
      assert.equal((await writesFor(q, refreshRoute)).length, 1);
      await notice(q, 'ok', /Check started/, 'filing check queued', 30000);
      assert.equal((await writesFor(q, refreshRoute)).length, 1, 'one click, one check');
      assert.equal((await writesFor(q, '/filings/SYN1/ai-proposal')).length, 0);
      await q.toggle('modern');
      await q.capture('research-filing-validation-and-result');
      return { assertionResults: { invalidJsonAndIntervalNoWrite: true, profilePayloadPreservedAndOnce: true, manualCheckOnce: true, noAiCall: true },
        allWritesSynthetic: true, actualNetworkOrLLMStarted: false };
    } finally { await reset(q, []); }
  });

  await q.executeScenario('filing', 'bulk-activation-and-link-confirm', async () => {
    const bulkRoute = '/filings/activate-missing', activateRoute = '/filings/SYN2/activate';
    const mark = await requestMark(q);
    try {
      await fresh(q, 'filing');
      await q.waitFor(root => document.querySelectorAll(root + ' button.fl-row[data-filing-ticker]').length === 4, 'four filing rows', 10000, FP);
      const groups = await q.js(root => Object.fromEntries([...document.querySelectorAll(root + ' [data-filing-gruppo]')]
        .map(g => [g.getAttribute('data-filing-gruppo'), [...g.querySelectorAll('[data-filing-ticker]')].map(r => `${r.getAttribute('data-filing-ticker')}:${r.getAttribute('data-filing-stato')}`)])), FP);
      assert.deepEqual(groups, { novita: ['SYN1:novita'], da_sistemare: ['SYN2:da_confermare', 'SYN4:proposta_ai'], senza_fonte: ['SYN3:senza_fonte'] });
      // automatic selection (first title) never searches candidates
      assert.equal((await readsOf(q, '/filings/SYN2/proposal', mark)).length, 0, 'no candidate search before the user opens a title');
      const before = (await writesFor(q, bulkRoute)).length;
      await q.click(`${FP} [data-filing-azione="attiva-mancanti"]`);
      await q.waitFor(root => !!document.querySelector(root + ' [data-filing-esito]'), 'bulk activation result', 10000, FP);
      assert.equal((await writesFor(q, bulkRoute)).length, before + 1, 'one click, one bulk activation');
      const esito = await q.js(root => document.querySelector(root + ' [data-filing-esito]')?.textContent || '', FP);
      assert.match(esito, /1 activated · 1 to confirm · 1 without a source/);
      await openFilingTitle(q, 'SYN2');
      await q.waitFor(root => document.querySelectorAll(root + ' [data-filing-centro="collegamento"] [data-filing-candidato]').length === 2,
        'two SEC candidates', 10000, FP);
      assert.equal((await readsOf(q, '/filings/SYN2/proposal', mark)).length, 1, 'opening the title searches candidates once');
      const second = `${FP} [data-filing-candidato="0009990022"] input[type="radio"]`;
      await q.click(second);
      await q.waitFor(s => document.querySelector(s)?.checked, 'second candidate chosen', 5000, second);
      await q.click(`${FP} [data-filing-azione="conferma"]`);
      await notice(q, 'ok', /Profile activated/, 'link confirmed');
      const posts = (await q.snapshot()).requests.slice(mark).filter(r => r.method === 'POST' && r.route === activateRoute);
      assert.equal(posts.length, 1, 'one confirmation, one activation');
      assert.deepEqual(posts[0].input, { cik: '0009990022' });
      assert.deepEqual(await filingAiOrCheck(q, mark), [], 'bulk activation and linking start no AI call and no check');
      await q.capture('research-filing-bulk-and-link', { viewports: ['1920x1080', '1280x900'] });
      return { assertionResults: { bulkActivationOnce: true, resultShown: true, candidateSearchOnlyOnOpen: true, chosenCikSent: true, noAiCall: true } };
    } finally { await reset(q, []); }
  });

  await q.executeScenario('filing', 'ai-proposal-button-only', async () => {
    const pdf = 'https://example.invalid/acme-half-year-2026.pdf';
    const { fixtureData } = require('./pages-modern-fixtures.cjs');
    const mark = await requestMark(q);
    try {
      // a running manager makes the page poll: loading, auto selection and polling stay free of AI
      await q.fixture({ setRead: { '/filings': fixtureData.filingOverview('portafoglio', { aggiornamento: { status: 'running', trigger: 'fixture', started_at: now, finished_at: null, error: null } }) } });
      await fresh(q, 'filing');
      await q.waitFor(root => !!document.querySelector(root + ' [data-filing-dettaglio="SYN1"]'), 'automatic selection of the first title', 10000, FP);
      const overviewBefore = (await readsOf(q, '/filings')).length;
      await q.pause(6500);
      const overviewAfter = (await readsOf(q, '/filings')).length;
      assert.ok(overviewAfter > overviewBefore, `the page polls while the manager runs (${overviewBefore} -> ${overviewAfter})`);
      assert.deepEqual(await filingAiOrCheck(q, mark), [], 'load, automatic selection and polling start no AI call, estimate or check');
      await q.fixture({ clearReads: ['/filings'] });
      // stored proposal: read for free, never re-proposed
      await openFilingTitle(q, 'SYN4');
      await q.waitFor(root => !!document.querySelector(root + ' [data-filing-centro="proposta-ai"] [data-filing-ai="esito"] [data-filing-azione="ai-salva"]'), 'stored AI proposal', 10000, FP);
      assert.ok((await readsOf(q, '/filings/SYN4/ai-proposal', mark)).length >= 1);
      assert.deepEqual(await filingAiOrCheck(q, mark), [], 'opening a stored proposal sends no AI call');
      // title without a source: URL, free estimate, one proposal, save
      await openFilingTitle(q, 'SYN3');
      await q.waitFor(root => !!document.querySelector(root + ' [data-filing-centro="senza-fonte"] [data-filing-azione="sito-ir"]'), 'no-source card', 10000, FP);
      await q.click(`${FP} [data-filing-azione="sito-ir"]`);
      await q.waitFor(root => !!document.querySelector(root + ' [data-filing-centro="proposta-ai"] [data-filing-input="ai-url"]'), 'AI proposal form', 5000, FP);
      await q.typeText(`${FP} [data-filing-input="ai-url"]`, pdf);
      assert.deepEqual(await filingAiOrCheck(q, mark), [], 'typing a URL sends nothing');
      await q.click(`${FP} [data-filing-azione="ai-stima"]`);
      await q.waitFor(root => !!document.querySelector(root + ' [data-filing-ai="stima"]') && !!document.querySelector(root + ' [data-filing-azione="ai-proponi"]'), 'free estimate', 10000, FP);
      const estimates = await readsOf(q, '/filings/SYN3/ai-estimate', mark);
      assert.equal(estimates.length, 1); assert.equal(estimates[0].query.url, pdf);
      assert.equal((await writesFor(q, '/filings/SYN3/ai-proposal')).length, 0, 'the estimate is not the AI call');
      assert.match(await q.js(root => document.querySelector(root + ' [data-filing-azione="ai-proponi"]')?.textContent || '', FP), /0[.,]0412/);
      await q.click(`${FP} [data-filing-azione="ai-proponi"]`);
      await q.waitFor(root => !!document.querySelector(root + ' [data-filing-ai="esito"] [data-filing-azione="ai-salva"]'), 'AI proposal result', 10000, FP);
      const proposals = await writesFor(q, '/filings/SYN3/ai-proposal');
      assert.equal(proposals.length, 1, 'exactly one AI call, from the button');
      assert.deepEqual(proposals[0].input, { url: pdf });
      assert.equal(await q.js(root => document.querySelectorAll(root + ' [data-filing-sezione]').length, FP), 2);
      await q.click(`${FP} [data-filing-azione="ai-salva"]`);
      await notice(q, 'ok', /Profile saved/, 'AI profile saved');
      const accepts = await writesFor(q, '/filings/SYN3/ai-proposal/accept');
      assert.equal(accepts.length, 1);
      assert.deepEqual(accepts[0].input, { sha256: 'synthetic-ai-sha-syn3', ir_urls: [pdf] });
      assert.equal((await writesFor(q, '/filings/SYN3/ai-proposal')).length, 1, 'saving does not call the model again');
      assert.equal((await q.snapshot()).requests.slice(mark).filter(r => r.method === 'POST' && /\/(?:ai-proposal|refresh)$/.test(r.route)).length, 1,
        'the only AI call or check in this scenario is the one button press');
      await q.capture('research-filing-ai-proposal-saved', { viewports: ['1920x1080'] });
      return { assertionResults: { noAiOnLoadPollOrAutoSelect: true, storedProposalReadFree: true, estimateFree: true, oneAiCallFromButton: true, acceptOnce: true },
        polls: overviewAfter - overviewBefore };
    } finally { await reset(q, ['/filings']); }
  });

  await q.executeScenario('filing', 'pdf-found-one-click', async () => {
    // Fase F: PDF IR trovati sul sito (sintetici): stima gratis compilata da un clic, proposta AI solo dal suo pulsante.
    const { fixtureData } = require('./pages-modern-fixtures.cjs');
    const ultimo = 'https://example.invalid/acme-annual-report-2025.pdf', prima = 'https://example.invalid/acme-annual-report-2024.pdf';
    const pdfIr = { tipo: 'annuale', candidati: 2, at: now, sito: 'https://www.acme.example/',
      ultimo: { url: ultimo, testo: 'Annual report 2025', tipo: 'annuale', periodo: '2025-12-31' },
      precedente: { url: prima, testo: 'Annual report 2024', tipo: 'annuale', periodo: '2024-12-31' } };
    const conPdf = fixtureData.filingOverview('portafoglio');
    conPdf.titoli = conPdf.titoli.map(t => t.ticker === 'SYN3' ? { ...t, pdf_ir: pdfIr } : t);
    const mark = await requestMark(q);
    const scritte = async route => (await q.snapshot()).requests.slice(mark).filter(r => r.route === route && r.method === 'POST');
    try {
      await q.fixture({ setRead: { '/filings': conPdf } });
      await fresh(q, 'filing');
      await openFilingTitle(q, 'SYN3');
      await q.waitFor(root => !!document.querySelector(root + ' [data-filing-pdf-trovati] [data-filing-azione="pdf-stima"]'), 'found PDFs card', 10000, FP);
      assert.equal(await q.js(root => document.querySelectorAll(root + ' [data-filing-pdf]').length, FP), 2, 'latest and year-before PDFs listed');
      assert.deepEqual(await filingAiOrCheck(q, mark), [], 'opening a title with found PDFs estimates nothing and calls no AI');
      assert.equal((await scritte('/filings/SYN3/search-pdf')).length, 0, 'no site search on open');
      await q.click(`${FP} [data-filing-azione="pdf-stima"]`);
      await q.waitFor(root => !!document.querySelector(root + ' [data-filing-ai="stima"]') && !!document.querySelector(root + ' [data-filing-azione="ai-proponi"]'), 'free estimate', 10000, FP);
      const estimates = await readsOf(q, '/filings/SYN3/ai-estimate', mark);
      assert.equal(estimates.length, 1); assert.equal(estimates[0].query.url, ultimo);
      const campi = await q.js(root => [document.querySelector(root + ' [data-filing-input="ai-url"]')?.value, document.querySelector(root + ' [data-filing-input="ai-altri"]')?.value], FP);
      assert.deepEqual(campi, [ultimo, prima], 'both found PDFs prefilled');
      assert.equal((await scritte('/filings/SYN3/ai-proposal')).length, 0, 'the estimate is not the AI call');
      await q.click(`${FP} [data-filing-azione="ai-proponi"]`);
      await q.waitFor(root => !!document.querySelector(root + ' [data-filing-ai="esito"]'), 'AI proposal result', 10000, FP);
      const proposals = await scritte('/filings/SYN3/ai-proposal');
      assert.equal(proposals.length, 1, 'exactly one AI call, from the button');
      assert.deepEqual(proposals[0].input, { url: ultimo, altri_url: [prima] });
      await q.capture('research-filing-pdf-found', { viewports: ['1920x1080'] });
      // senza PDF trovati: la ricerca sul sito parte solo dal pulsante, una volta
      await q.fixture({ setRead: { '/filings': fixtureData.filingOverview('portafoglio') } });
      await fresh(q, 'filing');
      await openFilingTitle(q, 'SYN3');
      await q.waitFor(root => !!document.querySelector(root + ' [data-filing-azione="cerca-pdf"]'), 'site search button', 10000, FP);
      await q.click(`${FP} [data-filing-azione="cerca-pdf"]`);
      await q.waitFor(root => !!document.querySelector(root + ' [data-filing-pdf-nessuno]'), 'nothing found declared', 10000, FP);
      assert.equal((await scritte('/filings/SYN3/search-pdf')).length, 1, 'one site search, from the button');
      assert.equal((await scritte('/filings/SYN3/ai-proposal')).length, 1, 'the search starts no AI call');
      return { assertionResults: { noEstimateOrAiOnOpen: true, estimatePrefilled: true, oneAiCallFromButton: true, searchOnlyFromButton: true } };
    } finally { await reset(q, ['/filings']); }
  });

  await q.executeScenario('filing', 'summary-links-to-page', async () => {
    const summaryReads = async () => (await q.snapshot()).requests.filter(r => r.method === 'GET' && r.route.startsWith('/filings')).length;
    const filingReadsSince = async n => (await q.snapshot()).requests.filter(r => r.method === 'GET' && r.route.startsWith('/filings')).slice(n).map(r => r.route);
    const followSummary = async (page, scope, start) => {
      const summary = `${scope} [data-filing-riepilogo="SYN1"]`;
      await q.waitFor(s => document.querySelector(s)?.getAttribute('data-stato') === 'ok', page + ' filing summary', 15000, summary);
      await q.settleRequests(page + ' summary reads', 6000);
      const reads = await filingReadsSince(start);
      assert.ok(reads.includes('/filings/SYN1') && reads.includes('/filings/runs/990')
        && reads.every(r => /^\/filings\/(?:SYN1|runs\/990|novita)$/.test(r)), `${page} summary reads only the title and its last run: ${JSON.stringify(reads)}`);
      const view = await q.js(s => ({ text: document.querySelector(s)?.innerText || '', href: document.querySelector(s + ' a[data-filing-apri="SYN1"]')?.getAttribute('href') }), summary);
      assert.equal(view.href, '#/filing?t=SYN1');
      assert.match(view.text, /1 change/);
      assert.equal(await q.js(() => document.querySelectorAll('[data-testid="filing-diff-panel"], .filing-diff-surface').length), 0, 'the old filing panel is gone');
      await q.click(`${summary} a[data-filing-apri="SYN1"]`);
      await q.waitFor(root => /^#\/filing\?t=SYN1/.test(location.hash) && !!document.querySelector(root + ' [data-filing-dettaglio="SYN1"]')
        && document.querySelector(root + ' [data-filing-ticker="SYN1"]')?.getAttribute('aria-pressed') === 'true', page + ' link opens the Filing page on SYN1', 10000, FP);
      return { ...view, reads };
    };
    const mark = await requestMark(q);
    try {
      const badge = await q.waitFor(() => {
        const b = document.querySelector('span.bb-nav-badge[data-filing-badge]');
        return b && { n: b.getAttribute('data-filing-badge'), text: b.textContent.trim(), href: b.closest('a')?.getAttribute('href') || null };
      }, 'Filing menu badge', 10000);
      assert.deepEqual(badge, { n: '1', text: '1', href: '#/filing' });
      await q.js(() => sessionStorage.setItem('bb:mktTicker', 'SYN1')); await fresh(q, 'market');
      await q.waitFor(() => !!document.querySelector('[data-page="market"] .mk-c-news .bbn-seg button:nth-child(2)'), 'news and filings tabs loaded');
      let start = await summaryReads();
      await q.click('[data-page="market"] .mk-c-news .bbn-seg button:nth-child(2)');
      const market = await followSummary('market', '[data-page="market"] .mk-filing', start);
      start = await summaryReads();
      await fresh(q, 'fundamentals');
      await q.waitFor(() => !!document.querySelector('[data-page="fundamentals"] [data-filing-riepilogo]'), 'Fundamentals filing summary', 15000);
      const ticker = await q.js(() => document.querySelector('[data-page="fundamentals"] [data-filing-riepilogo]')?.getAttribute('data-filing-riepilogo'));
      assert.equal(ticker, 'SYN1', 'Fundamentals selects the synthetic SYN1 model');
      const fundamentals = await followSummary('fundamentals', '[data-page="fundamentals"]', start);
      assert.deepEqual(await filingAiOrCheck(q, mark), [], 'summaries and links never start an AI call or a check');
      await q.capture('research-filing-from-summary', { viewports: ['1920x1080'] });
      return { assertionResults: { badgeFromNovita: true, marketSummaryLinks: true, fundamentalsSummaryLinks: true, summaryReadOnly: true, noAiCall: true },
        badge, market, fundamentals };
    } finally { await q.js(() => sessionStorage.removeItem('bb:mktTicker')); await reset(q, []); }
  });
}

async function runRich(q) {
  await q.pause(5500);
  await q.executeScenario('fundamentals', 'bank-regulated-assets-nav-and-archived-workbooks', async () => {
    await reset(q, ['/fundamentals/models']); await fresh(q, 'fundamentals');
    await q.waitFor(() => [...document.querySelectorAll('[data-page="fundamentals"] .research-ticker-action')].some(el => el.textContent.includes('SYNBANK')), 'sector models loaded');
    const evidence = [];
    for (const [ticker, expected] of [['SYNBANK', /121\.4/], ['SYNRAB', /RAB/], ['SYNMNAV', /NAV/]]) {
      const target = await q.js(tk => {
        const b = [...document.querySelectorAll('[data-page="fundamentals"] .research-ticker-action')].find(el => el.textContent.trim().startsWith(tk));
        if (!b) return false; b.dataset.researchSector = tk; return true;
      }, ticker);
      assert.ok(target); await q.click(`[data-research-sector="${ticker}"]`);
      await q.waitFor(tk => document.querySelector('[data-page="fundamentals"] [data-testid="sector-valuation-status"]')?.previousElementSibling?.textContent.includes(tk), 'selected ' + ticker, 10000, ticker);
      await q.pause(180);
      for (const mode of ['classic', 'modern']) {
        const before = await q.counts(); await q.toggle(mode);
        const text = await q.js(() => document.querySelector('[data-page="fundamentals"] [data-testid="sector-valuation-status"]')?.parentElement?.innerText || '');
        assert.match(text, expected); assert.deepEqual(await q.counts(), before);
        if (ticker === 'SYNBANK') { assert.match(text, /114\.20/); assert.match(text, /127\.80/); assert.match(text, /76 bps/); }
        if (ticker === 'SYNRAB') { assert.match(text, /8,200/); assert.match(text, /98\.30/); assert.match(text, /103\.10/); }
        if (ticker === 'SYNMNAV') { assert.match(text, /26\.78/); assert.match(text, /USD fair value/); assert.match(text, /NAV 25\.50/); }
        await q.capture('research-sector-' + ticker + '-' + mode);
      }
      evidence.push({ ticker, selectionRetained: true, sectorPayloadVisible: true });
    }
    // Archived rows retain their historical provenance and authenticated copy header.
    const historical = 'SYN1_SYNTHETIC_r3.xlsx';
    const archive = await q.js(() => {
      const b = [...document.querySelectorAll('[data-page="fundamentals"] button')].find(el => /ARCHIV|OLDER|previous files/i.test(el.textContent));
      if (!b) return false; b.dataset.researchArchive = 'true'; return b.textContent;
    });
    assert.ok(archive, 'archived revision disclosure must exist'); await q.click('[data-research-archive]');
    const historicalPath = '/fundamentals/models/' + historical + '/download';
    try {
      await q.fixture({ setRead: { [historicalPath]: { body: 'Synthetic archived workbook bytes', headers: { 'X-Valuation-Copy': 'historical' } } } });
      const selector = await button(q, '[data-page="fundamentals"]', historical, 'historical');
      const prior = q.report.downloads.length; await q.click(selector);
      for (let n = 0; n < 60 && q.report.downloads.length === prior; n++) await q.pause(100);
      assert.equal(q.report.downloads.length, prior + 1); assert.equal(q.report.downloads.at(-1).state, 'completed');
      for (const mode of ['classic', 'modern']) {
        const before = await q.counts(); await q.toggle(mode); assert.deepEqual(await q.counts(), before);
        await q.capture('research-sector-historical-revisions-' + mode);
      }
    } finally { await reset(q, [historicalPath]); }
    return { assertionResults: { sectorSpecificPayloadsRetained: true, archivedWorkbookDownloadComplete: true }, evidence, allReadsSynthetic: true };
  });
  await q.executeScenario('news', 'provider-refresh-held-operation-and-result-uses-one-post', async () => {
    const route = '/news/feed/refresh';
    const job = { id: 'qa-root-news-refresh', status: 'running', trigger: 'synthetic_fixture', started_at: now, result: null, error: null };
    try {
      await fresh(q, 'news'); await q.waitFor(() => !!document.querySelector('[data-page="news"] .news-list'), 'wire loaded');
      const selector = await button(q, '[data-page="news"]', 'Query providers', 'news-refresh');
      await q.fixture({ setWrite: { [route]: delayed({ accepted: true, job }) },
        setRead: { '/news/providers': { ...clone(reads['/news/providers']), refresh_job: { ...job, status: 'success', result: { saved: 2, skipped_duplicates: 1 } } } } });
      const before = (await writesFor(q, route)).length; await q.click(selector);
      await q.waitFor(s => document.querySelector(s)?.disabled, 'provider refresh held POST busy', 5000, selector);
      await pending(q, selector, 'news-provider-refresh', async () => assert.ok(await q.js(() => !!document.querySelector('[data-page="news"] .news-list'))));
      assert.equal((await writesFor(q, route)).length, before + 1);
      assert.equal((await writesFor(q, route)).at(-1).query.classify, 'true');
      await q.waitFor(s => !!document.querySelector(s) && !document.querySelector(s).disabled, 'refresh success after polling', 30000, selector);
      assert.equal((await writesFor(q, route)).length, before + 1);
      for (const mode of ['classic', 'modern']) {
        const before = await q.counts(); await q.toggle(mode); assert.deepEqual(await q.counts(), before);
        assert.ok(await q.js(s => !!document.querySelector(s) && !document.querySelector(s).disabled, selector));
        await q.capture('research-news-provider-refresh-complete-' + mode);
      }
      return { assertionResults: { heldOperationRetainsController: true, singlePostAcrossModeChanges: true, sharedJobStatusPollingCompletes: true }, actualProviderOrLLMRequest: false };
    } finally { await reset(q, ['/news/providers']); }
  });
}
async function run(q) {
  if (process.env.BB_RESEARCH_ACTIONS_SCOPE !== 'rich') await runOperations(q);
  if (process.env.BB_RESEARCH_ACTIONS_SCOPE !== 'operations') await runRich(q);
}
module.exports = { run };
