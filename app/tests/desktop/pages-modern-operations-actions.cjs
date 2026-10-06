// Synthetic operational flows for Trade, Movements, Mandato, Journal and Settings.
// Every write is explicitly configured through the isolated fixture server.
const assert = require('node:assert/strict');
const { fixtureData } = require('./pages-modern-fixtures.cjs');

const STAMP = '2026-09-30T12:00:00Z';
const clone = value => JSON.parse(JSON.stringify(value));

async function capture(q, name, viewports, options = {}) {
  assert.equal(typeof q.capture, 'function', 'shared action runner must provide q.capture(name, options)');
  return q.capture(name, { ...options, ...(viewports ? { viewports } : {}) });
}

async function revealAndInspectControl(q, selector) {
  return q.js(async sel => {
    const target = document.querySelector(sel);
    if (!target) return { found: false, selector: sel };
    target.scrollIntoView({ behavior: 'instant', block: 'center', inline: 'nearest' });
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const rect = target.getBoundingClientRect();
    const clipped = { left: 0, top: 0, right: innerWidth, bottom: innerHeight };
    for (let parent = target.parentElement; parent && parent !== document.documentElement; parent = parent.parentElement) {
      const style = getComputedStyle(parent), box = parent.getBoundingClientRect();
      if (/^(auto|scroll|hidden|clip)$/.test(style.overflowX)) {
        clipped.left = Math.max(clipped.left, box.left + parent.clientLeft);
        clipped.right = Math.min(clipped.right, box.left + parent.clientLeft + parent.clientWidth);
      }
      if (/^(auto|scroll|hidden|clip)$/.test(style.overflowY)) {
        clipped.top = Math.max(clipped.top, box.top + parent.clientTop);
        clipped.bottom = Math.min(clipped.bottom, box.top + parent.clientTop + parent.clientHeight);
      }
    }
    const style = getComputedStyle(target);
    const visibleAncestors = [...function* () {
      for (let node = target; node && node !== document.documentElement; node = node.parentElement) yield node;
    }()].every(node => {
      const current = getComputedStyle(node);
      return current.display !== 'none' && current.visibility !== 'hidden' && current.visibility !== 'collapse'
        && current.pointerEvents !== 'none' && Number(current.opacity) > .01;
    });
    const fullyVisible = rect.width > 0 && rect.height > 0 && rect.left >= clipped.left && rect.top >= clipped.top
      && rect.right <= clipped.right && rect.bottom <= clipped.bottom;
    const x = Math.max(0, Math.min(innerWidth - 1, (Math.max(rect.left, clipped.left) + Math.min(rect.right, clipped.right)) / 2));
    const y = Math.max(0, Math.min(innerHeight - 1, (Math.max(rect.top, clipped.top) + Math.min(rect.bottom, clipped.bottom)) / 2));
    const hit = fullyVisible ? document.elementFromPoint(x, y) : null;
    return { found: true, selector: sel, text: (target.innerText || target.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 180),
      tag: target.tagName, disabled: !!target.disabled || target.getAttribute('aria-disabled') === 'true',
      visibleAncestors, fullyVisible, hitTestable: !!hit && (hit === target || target.contains(hit)),
      pointerEvents: style.pointerEvents, viewport: { width: innerWidth, height: innerHeight },
      rect: { left: Math.round(rect.left), top: Math.round(rect.top), right: Math.round(rect.right), bottom: Math.round(rect.bottom) },
      hit: hit ? { tag: hit.tagName, text: (hit.innerText || hit.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 120) } : null };
  }, selector);
}

async function captureCompactJournalAction(q, name, selector, routePrefixes, baselineRequests) {
  let before, after;
  await q.withViewport(900, 700, async () => {
    before = await revealAndInspectControl(q, selector);
    assert.ok(before.found && before.visibleAncestors && before.fullyVisible && before.hitTestable
      && before.pointerEvents !== 'none' && !before.disabled,
    `${name} control is not fully visible and hit-testable after scrolling at 900x700: ${JSON.stringify(before)}`);
    await capture(q, `operations-modern/${name}-compact-actions`, [[900, 700]], { scrollSelector: selector });
    after = await revealAndInspectControl(q, selector);
    assert.deepEqual({ text: after.text, disabled: after.disabled, hitTestable: after.hitTestable,
      fullyVisible: after.fullyVisible, viewport: after.viewport },
    { text: before.text, disabled: before.disabled, hitTestable: before.hitTestable,
      fullyVisible: before.fullyVisible, viewport: before.viewport },
    `${name} control remains visible and reachable after its compact screenshot`);
    assert.deepEqual(await scopedRequestSignature(q, routePrefixes), baselineRequests,
      `${name} screenshot issues no additional page reads or writes`);
  });
  return { viewport: '900x700', before, after, clicked: false, fixtureOnly: true };
}

async function captureCompactPending(q, name, { fieldSelector, buttonSelector, contextSelector, expectedText, contextBusyAttr }) {
  let before, after;
  await q.withViewport(900, 700, async () => {
    const inspect = async reveal => q.js(async ({ fieldSelector: field, buttonSelector: button, contextSelector: context, revealTarget }) => {
      const anchor = field ? document.querySelector(field) : null;
      const target = button ? document.querySelector(button) : anchor?.closest('form')?.querySelector('button[type="submit"]') || anchor;
      if (!target) return { found: false, field, button };
      if (revealTarget) target.scrollIntoView({ block: 'center', inline: 'nearest' });
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      const rect = target.getBoundingClientRect();
      const visibleRect = { left: 0, top: 0, right: innerWidth, bottom: innerHeight };
      for (let parent = target.parentElement; parent && parent !== document.documentElement; parent = parent.parentElement) {
        const style = getComputedStyle(parent), box = parent.getBoundingClientRect();
        const clipX = /^(auto|scroll|hidden|clip)$/.test(style.overflowX);
        const clipY = /^(auto|scroll|hidden|clip)$/.test(style.overflowY);
        if (clipX) { visibleRect.left = Math.max(visibleRect.left, box.left + parent.clientLeft); visibleRect.right = Math.min(visibleRect.right, box.left + parent.clientLeft + parent.clientWidth); }
        if (clipY) { visibleRect.top = Math.max(visibleRect.top, box.top + parent.clientTop); visibleRect.bottom = Math.min(visibleRect.bottom, box.top + parent.clientTop + parent.clientHeight); }
      }
      const fullyVisible = rect.width > 0 && rect.height > 0 && rect.left >= 0 && rect.top >= 0
        && rect.right <= innerWidth && rect.bottom <= innerHeight
        && visibleRect.left <= rect.left + 1 && visibleRect.top <= rect.top + 1
        && visibleRect.right >= rect.right - 1 && visibleRect.bottom >= rect.bottom - 1;
      const x = Math.max(0, Math.min(innerWidth - 1, (Math.max(rect.left, visibleRect.left) + Math.min(rect.right, visibleRect.right)) / 2));
      const y = Math.max(0, Math.min(innerHeight - 1, (Math.max(rect.top, visibleRect.top) + Math.min(rect.bottom, visibleRect.bottom)) / 2));
      const hit = fullyVisible ? document.elementFromPoint(x, y) : null;
      const style = getComputedStyle(target), contextNode = context ? document.querySelector(context) : null;
      const ancestorsVisible = [...function* () { for (let node = target; node && node !== document.documentElement; node = node.parentElement) yield node; }()]
        .every(node => { const s = getComputedStyle(node); return s.display !== 'none' && s.visibility !== 'hidden' && s.visibility !== 'collapse' && Number(s.opacity) > .01; });
      return { found: true, text: (target.innerText || target.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 180),
        disabled: !!target.disabled || target.getAttribute('aria-disabled') === 'true',
        visible: style.display !== 'none' && style.visibility !== 'hidden' && ancestorsVisible,
        fullyVisible, hitTestable: !!hit && (hit === target || target.contains(hit) || hit.contains(target)),
        rect: { left: Math.round(rect.left), top: Math.round(rect.top), right: Math.round(rect.right), bottom: Math.round(rect.bottom) },
        viewport: { width: innerWidth, height: innerHeight }, contextFound: !!contextNode,
        contextBusy: contextNode && contextNode.getAttribute('aria-busy'), contextDisabled: contextNode && !!contextNode.disabled };
    }, { fieldSelector, buttonSelector, contextSelector, revealTarget: reveal });

    before = await inspect(true);
    assert.ok(before.found && before.visible && before.fullyVisible && before.hitTestable,
      `${name} control is not fully visible/hit-testable after scrolling: ${JSON.stringify(before)}`);
    assert.ok(before.disabled, `${name} target control is not disabled while the synthetic write is pending: ${JSON.stringify(before)}`);
    if (expectedText) assert.match(before.text, new RegExp(expectedText, 'i'), `${name} does not show its pending label`);
    if (contextBusyAttr) {
      assert.equal(before.contextFound, true, `${name} busy-state context is missing`);
      assert.equal(before.contextBusy, contextBusyAttr, `${name} context aria-busy changed: ${JSON.stringify(before)}`);
    }
    await capture(q, name, [[900, 700]]);
    after = await inspect(false);
    assert.deepEqual({ text: after.text, disabled: after.disabled, contextBusy: after.contextBusy },
      { text: before.text, disabled: before.disabled, contextBusy: before.contextBusy },
      `${name} state changed during its compact capture`);
  });
  return { viewport: '900x700', before, after, fixtureOnly: true };
}

async function clickText(q, scope, pattern, index = 0) {
  const key = 'bb-ops-' + Math.random().toString(36).slice(2);
  const found = await q.js((selector, source, ordinal, marker) => {
    const root = document.querySelector(selector);
    if (!root) return null;
    const re = new RegExp(source, 'i');
    const candidates = [...root.querySelectorAll('button,a,[role="button"]')].filter(el => {
      const rect = el.getBoundingClientRect(), style = getComputedStyle(el);
      const text = (el.innerText || el.getAttribute('aria-label') || el.title || '').trim();
      return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden' && re.test(text);
    });
    const target = candidates[ordinal];
    if (!target) return { count: candidates.length, candidates: candidates.map(el => (el.innerText || el.getAttribute('aria-label') || '').trim().slice(0, 100)) };
    target.setAttribute('data-ops-click', marker);
    return { text: (target.innerText || target.getAttribute('aria-label') || '').trim(), count: candidates.length };
  }, scope, pattern, index, key);
  assert.ok(found && found.text, 'no visible action matching /' + pattern + '/ in ' + scope + ': ' + JSON.stringify(found));
  return q.click('[data-ops-click="' + key + '"]');
}

async function text(q, selector) {
  return q.js(sel => {
    const el = document.querySelector(sel);
    return el ? (el.innerText || el.textContent || '').trim() : null;
  }, selector);
}

async function reloadCurrentApp(q, label, expectedRoute) {
  await q.js(() => {
    window.__bbOpsReloadBeforeNavigation = true;
    setTimeout(() => window.location.reload(), 30);
    return true;
  });
  // Allow the old document to finish its unload before polling the new
  // renderer context. The isolated fixture server and overrides live in the
  // harness process, so they remain stable across this app-only reload.
  await q.pause(450);
  await q.waitFor((route) => !window.__bbOpsReloadBeforeNavigation
      && location.hash === route
      && document.readyState === 'complete'
      && !!document.querySelector('[data-testid="appearance-menu"]')
      && !!document.querySelector('main .relative'),
    label, 20000, expectedRoute);
}

async function closeSettingsLayers(q) {
  for (let attempt = 0; attempt < 3; attempt++) {
    const open = await q.js(() => !!document.querySelector('.f11-ask[role="dialog"][aria-modal="true"]')
      || !!document.querySelector('.f11v[role="dialog"][aria-modal="true"]'));
    if (!open) return;
    await q.key('Escape');
    await q.waitFor(() => !document.querySelector('.f11-ask[role="dialog"][aria-modal="true"]')
      || !document.querySelector('.f11v[role="dialog"][aria-modal="true"]'), 'Settings layer closes on Escape', 1000);
  }
}

async function routeWrites(q, method, route) {
  return (await q.snapshot()).requests.filter(item => item.method === method && item.route === route);
}

async function visualControllerSnapshot(q, { controllerSelector, stateSelectors = [], evidenceSelector, rowSelector }) {
  return q.js(({ controllerSelector, stateSelectors, evidenceSelector, rowSelector }) => {
    const controller = document.querySelector(controllerSelector);
    if (!controller) return null;
    window.__bbOpsVisualNodeIds ||= new WeakMap();
    window.__bbOpsVisualNextNodeId ||= 0;
    if (!window.__bbOpsVisualNodeIds.has(controller)) {
      window.__bbOpsVisualNodeIds.set(controller, ++window.__bbOpsVisualNextNodeId);
    }
    const controls = stateSelectors.map(selector => {
      const elements = [...document.querySelectorAll(selector)];
      return { selector, elements: elements.map(element => ({
        id: element.id || null,
        text: (element.innerText || element.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 240),
        value: 'value' in element ? element.value : null,
        checked: 'checked' in element ? !!element.checked : null,
        pressed: element.getAttribute('aria-pressed'),
        selected: element.getAttribute('aria-selected'),
        disabled: !!element.disabled,
      })) };
    });
    const evidence = evidenceSelector ? document.querySelector(evidenceSelector) : null;
    return { controllerId: window.__bbOpsVisualNodeIds.get(controller), controls,
      evidenceText: evidence ? (evidence.innerText || evidence.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 6000) : null,
      rowCount: rowSelector ? document.querySelectorAll(rowSelector).length : null };
  }, { controllerSelector, stateSelectors, evidenceSelector, rowSelector });
}

async function scopedRequestSignature(q, routePrefixes) {
  const snapshot = await q.snapshot();
  return (snapshot.requests || []).filter(item => routePrefixes.some(prefix => item.route === prefix || item.route.startsWith(prefix + '/')))
    .map(({ method, route, query }) => ({ method, route, query: query || {} }));
}

async function captureModernRetention(q, name, page, selectors, routePrefixes, expectations = {}) {
  const entryMode = await q.js(() => document.querySelector('[data-theme-choice="dark"]')?.getAttribute('aria-pressed') === 'true'
    ? 'modern' : 'classic');
  const requestsAtEntry = await scopedRequestSignature(q, routePrefixes);
  const entryState = await visualControllerSnapshot(q, selectors);
  if (entryMode !== 'classic') await q.toggle('classic');
  const before = await visualControllerSnapshot(q, selectors);
  assert.ok(before, `${name} controller is mounted before the mode switch`);
  assert.deepEqual(before, entryState, `${name} state survives setup in Classic`);
  assert.deepEqual(await scopedRequestSignature(q, routePrefixes), requestsAtEntry,
    `${name} setup in Classic issues no additional page reads or writes`);
  if (expectations.before) expectations.before(before);
  const requestsBefore = requestsAtEntry;

  await q.toggle('modern');
  assert.ok(await q.js(pageId => !![...document.querySelectorAll('.bb-page-modern')].find(node => node.dataset.page === pageId), page),
    `${name} is rendered in the modern page presentation`);
  const switched = await visualControllerSnapshot(q, selectors);
  assert.deepEqual(switched, before, `${name} controller identity, selections, values and rendered evidence survive the modern switch`);
  assert.deepEqual(await scopedRequestSignature(q, routePrefixes), requestsBefore,
    `${name} mode switch issues no additional page reads or writes`);

  const scrollSelector = expectations.scrollSelector || null;
  await capture(q, `operations-modern/${name}`, undefined, scrollSelector ? { scrollSelector } : {});
  let compactActionEvidence = null;
  if (expectations.compactActionSelector) {
    const compactRequestBaseline = await scopedRequestSignature(q, routePrefixes);
    compactActionEvidence = await captureCompactJournalAction(q, name, expectations.compactActionSelector,
      routePrefixes, compactRequestBaseline);
  }
  const afterCapture = await visualControllerSnapshot(q, selectors);
  assert.deepEqual(afterCapture, before, `${name} remains on the same controller/state through seven Modern viewport captures`);
  assert.deepEqual(await scopedRequestSignature(q, routePrefixes), requestsBefore,
    `${name} seven-viewport Modern captures issue no additional page reads or writes`);

  await q.toggle('classic');
  const restored = await visualControllerSnapshot(q, selectors);
  assert.deepEqual(restored, before, `${name} controller and selection also survive return to Classic`);
  assert.deepEqual(await scopedRequestSignature(q, routePrefixes), requestsBefore,
    `${name} return switch issues no additional page reads or writes`);
  return { controllerId: before.controllerId, scopedRequestCount: requestsBefore.length,
    modernSevenViewportCapture: true, scrollSelector, compactActionEvidence, returnedToClassic: true };
}

async function executeFixtureScenario(q, page, scenario, run) {
  await q.fixture({ clearAll: true });
  const result = await q.executeScenario(page, scenario, async () => {
    try { return await run(); }
    finally { await q.fixture({ clearAll: true }); }
  });
  if (result.status === 'failed') {
    // Failed assertions must not leak a modal, selected route, or fixture
    // override into the next independently reported scenario.
    await q.fixture({ clearAll: true });
    try { await closeSettingsLayers(q); } catch { /* continue best-effort recovery */ }
    try {
      const modeControlPresent = await q.js(() => !!document.querySelector('[data-theme-choice="light"]'));
      if (modeControlPresent) await q.toggle('classic');
      await q.visit('/dashboard');
      result.cleanupRecovery = 'classic dashboard restored';
    } catch (error) {
      result.cleanupRecovery = `dashboard recovery failed: ${String(error?.message || error)}`;
    }
    await q.fixture({ clearAll: true });
  }
  return result;
}

const taskDisplayNames = () => fixtureData.tasks.map(task => String(task.TaskName || '').replace('Bellomberg-', ''));

async function submitTrade(q) {
  const present = await q.js(() => {
    const button = document.querySelector('#f7-tk')?.closest('form')?.querySelector('button[type="submit"]');
    if (!button) return false;
    button.setAttribute('data-ops-trade-submit', 'true');
    return true;
  });
  assert.ok(present, 'Trade form submit control is missing');
  return q.click('[data-ops-trade-submit="true"]');
}

async function submitCash(q) {
  const present = await q.js(() => {
    const button = document.querySelector('#f7-mv-imp')?.closest('form')?.querySelector('button[type="submit"]');
    if (!button) return false;
    button.setAttribute('data-ops-cash-submit', 'true');
    return true;
  });
  assert.ok(present, 'Cash movement submit control is missing');
  return q.click('[data-ops-cash-submit="true"]');
}

function tradePreview(previewId) {
  return { ok: true, preview_id: previewId || 'qa-trade-preview-1', expires_in_seconds: 30,
    cash_delta_eur: -80, cash_disponibile_eur: 12265.67,
    data: '2026-09-29T10:30:00', ora_convenzionale: false, link_origin: 'none',
    fx: { tasso: 1, fonte: 'identity', nota: null, data: null },
    performance_note: 'Synthetic fixture preview only.' };
}

function openingBody() {
  return { ticker: 'SYNQA', nome: 'Synthetic QA Holding', quantita: 2, prezzo_medio: 40,
    valuta: 'EUR', as_of: '2026-09-29', provenienza: 'Synthetic acquisition register',
    nota: 'Synthetic fixture only.' };
}

function openingResponse(preview) {
  const body = openingBody();
  const opening = { ...body, precisione_data: 'day' };
  if (!preview) Object.assign(opening, { id: 82, created_at: STAMP });
  return { ok: true, ...(preview ? { preview_id: 'qa-opening-preview-1', expires_in_seconds: 30 } : {}),
    opening, position: { ticker: body.ticker, nome: body.nome, quantita: body.quantita,
      prezzo_medio: body.prezzo_medio, valuta: body.valuta, data_apertura: null },
    cash_delta_eur: 0, cash_disponibile_eur: 12345.67,
    performance_note: 'Synthetic fixture only; no acquisition or cash movement.' };
}

function journalEntry(overrides = {}) {
  return { id: 910, origin: 'user', kind: 'thesis', ticker: 'SYN1',
    title: 'Synthetic thesis: durable margins',
    body: '## Fixture-only thesis\n\nSynthetic evidence.\n\nNo external or portfolio data is represented.',
    created_at: '2026-09-27T08:15:00Z', updated_at: STAMP, version: 4, archived_at: null, ...overrides };
}

const JOURNAL_VISUAL_SELECTORS = {
  controllerSelector: '#panel-diario .journal-page',
  stateSelectors: ['#tab-diario', '#panel-diario .journal-search input', '#panel-diario .journal-title-input',
    '#panel-diario .journal-body-label textarea', '#panel-diario .journal-editor-top',
    '#panel-diario .journal-warning', '#panel-diario .journal-history', '#panel-diario .journal-pagination'],
  evidenceSelector: '#panel-diario .journal-page',
  rowSelector: '#panel-diario .journal-library .journal-note',
};

async function captureJournalModern(q, name, before, options = {}) {
  return captureModernRetention(q, name, 'mandato', JOURNAL_VISUAL_SELECTORS, ['/journal'], { before, ...options });
}

async function run(q) {
  // This integration fixture never tests desktop notifications. Turning off
  // the opt-in poll avoids an unrelated timer GET during screenshot resizes.
  await q.js(() => localStorage.setItem('bellomberg_alerts_enabled', 'false'));
  await tradeActions(q);
  await tradeReadStates(q);
  await cashActions(q);
  await openingActions(q);
  await openingReadStates(q);
  await movementsActions(q);
  await movementsReadStates(q);
  await mandateActions(q);
  await mandateReadStates(q);
  await journalActions(q);
  await journalListStates(q);
  await journalPromoteThesis(q);
  await settingsActions(q);
  await settingsReadStates(q);
}

async function tradeActions(q) {
  await executeFixtureScenario(q, 'trades', 'validated-preview-cancel-confirm-once-and-unknown-write', async () => {
    await q.toggle('classic');
    await q.visit('/trades');
    await q.waitFor(() => !!document.querySelector('#f7-tk') && !!document.querySelector('#f7-qt'), 'Trade operation form');
    await q.toggle('modern');
    // The Nuova page drops the cash rail with ticks: the order size is compared with the
    // median of past orders in a small histogram next to the before → after tiles.
    const sizeReference = await q.js(() => {
      const box = document.querySelector('.bb-page-modern[data-page="trades"] .f7c .te-size');
      return box ? { text: box.innerText.trim(), bars: box.querySelectorAll('.te-hist span').length } : null;
    });
    assert.ok(sizeReference && sizeReference.bars > 0 && /median|mediana/i.test(sizeReference.text),
      `order size panel compares with the median of past orders: ${JSON.stringify(sizeReference)}`);
    await capture(q, 'operations/trades-cash-reference-labels');
    await q.toggle('classic');
    await q.setValue('#f7-tk', 'SYNQA');
    await q.setValue('#f7-qt', '0');
    await q.setValue('#f7-pz', '40');
    await submitTrade(q);
    await q.waitFor(() => !!document.querySelector('main [data-page="trades"] [data-avviso]'), 'client-side trade validation');
    assert.equal((await routeWrites(q, 'POST', '/trade/preview')).length, 0, 'invalid quantities must not reach preview');

    await q.fixture({ setWrite: { '/trade/preview': tradePreview('qa-trade-preview-cancel') } });
    await q.setValue('#f7-qt', '2');
    await submitTrade(q);
    await q.waitFor(() => !!document.querySelector('.cfm-modal[role="alertdialog"]'), 'trade confirmation dialog');
    await q.pause(35);
    const focusDefaultCancel = await q.js(() => document.activeElement?.matches('.cfm-modal .cfm-btn.no') || false);
    assert.ok(focusDefaultCancel, 'trade confirmation defaults focus to Cancel');
    await q.js(() => { const dialog = document.querySelector('.cfm-modal[role="alertdialog"]'); if (dialog) dialog.dataset.opsStableId = 'trade-cancel-dialog'; });
    await q.toggle('modern');
    assert.equal(await q.js(() => document.querySelector('.cfm-modal[role="alertdialog"]')?.dataset.opsStableId || null),
      'trade-cancel-dialog', 'trade confirmation remains the same pending dialog after mode switch');
    await q.waitFor(() => document.activeElement?.matches('.cfm-modal .cfm-btn.no'),
      'trade dialog cancel focus restored after mode switch');
    await capture(q, 'operations/trades-confirm-cancel-modern');
    await q.waitFor(() => document.activeElement?.matches('.cfm-modal .cfm-btn.no'),
      'trade dialog retains focus after viewport captures');
    const previewCountBeforeCompactCancel = (await routeWrites(q, 'POST', '/trade/preview')).length;
    const tradeCountBeforeCompactCancel = (await routeWrites(q, 'POST', '/trade')).length;
    const compactCancel = await q.withViewport(900, 700, async () => {
      const scroll = await q.js(async () => {
        const dialog = document.querySelector('.cfm-modal[role="alertdialog"]');
        if (!dialog) return null;
        dialog.scrollTop = dialog.scrollHeight;
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        return { scrollTop: dialog.scrollTop, maxScrollTop: dialog.scrollHeight - dialog.clientHeight };
      });
      assert.ok(scroll && scroll.maxScrollTop > 0 && scroll.scrollTop >= scroll.maxScrollTop - 2,
        'compact trade confirmation scrolls to its action footer');
      const controls = await q.js(() => {
        const dialog = document.querySelector('.cfm-modal[role="alertdialog"]');
        const measure = selector => {
          const element = dialog?.querySelector(selector);
          if (!element) return { exists: false };
          const rect = element.getBoundingClientRect(), box = dialog.getBoundingClientRect();
          const x = rect.left + rect.width / 2, y = rect.top + rect.height / 2;
          const hit = document.elementFromPoint(x, y);
          return { exists: true, enabled: !element.disabled,
            fullyVisible: rect.left >= 0 && rect.right <= innerWidth && rect.top >= Math.max(0, box.top + dialog.clientTop)
              && rect.bottom <= Math.min(innerHeight, box.top + dialog.clientTop + dialog.clientHeight),
            hitTest: !!hit && (hit === element || element.contains(hit)) };
        };
        return { cancel: measure('.cfm-btn.no'), record: measure('.cfm-btn.go'), viewport: [innerWidth, innerHeight] };
      });
      assert.deepEqual(controls.viewport, [900, 700]);
      assert.ok(controls.cancel.exists && controls.cancel.enabled && controls.cancel.fullyVisible && controls.cancel.hitTest,
        'compact trade Cancel is visible, enabled and receives the native hit test: ' + JSON.stringify(controls.cancel));
      assert.ok(controls.record.exists && controls.record.enabled && controls.record.fullyVisible && controls.record.hitTest,
        'compact trade Record is visible, enabled and receives the native hit test: ' + JSON.stringify(controls.record));
      await capture(q, 'operations/trades-confirm-cancel-modern-compact-scrolled', [[900, 700]]);
      await q.click('.cfm-modal .cfm-btn.no');
      await q.waitFor(() => !document.querySelector('.cfm-modal[role="alertdialog"]'), 'compact trade Cancel closes the dialog');
      return controls;
    });
    assert.equal((await routeWrites(q, 'POST', '/trade/preview')).length, previewCountBeforeCompactCancel,
      'compact dialog review adds no preview POST');
    assert.equal((await routeWrites(q, 'POST', '/trade')).length, tradeCountBeforeCompactCancel,
      'compact Cancel closes the frozen preview without a trade write');
    await q.toggle('classic');

    await q.fixture({ setWrite: {
      '/trade/preview': tradePreview('qa-trade-preview-confirmed'),
      '/trade': { body: { ok: true, trade_id: 599, cash_disponibile_eur: 12265.67,
        cash_delta_eur: -80, data: '2026-09-29T10:30:00', ora_convenzionale: false,
        link_origin: 'none', fx: { tasso: 1, fonte: 'identity' }, performance_note: 'Synthetic fixture commit only.' }, delayMs: 20000 },
    } });
    await submitTrade(q);
    await q.waitFor(() => !!document.querySelector('.cfm-modal[role="alertdialog"]'), 'second trade confirmation');
    await q.click('.cfm-modal .cfm-btn.go');
    await q.waitFor(() => document.querySelector('#f7-tk')?.closest('form')?.querySelector('button[type="submit"]')?.disabled,
      'trade write busy state');
    const started = await routeWrites(q, 'POST', '/trade');
    assert.equal(started.length, 1, 'trade confirmation sends one write');
    const busyBeforeSwitch = await q.js(() => ({ ticker: document.querySelector('#f7-tk')?.value || null,
      quantity: document.querySelector('#f7-qt')?.value || null, price: document.querySelector('#f7-pz')?.value || null,
      submitDisabled: !!document.querySelector('#f7-tk')?.closest('form')?.querySelector('button[type="submit"]')?.disabled }));
    assert.deepEqual(busyBeforeSwitch, { ticker: 'SYNQA', quantity: '2', price: '40', submitDisabled: true });
    await q.toggle('modern');
    const switchedBusy = await q.js(() => ({ ticker: document.querySelector('#f7-tk')?.value || null,
      quantity: document.querySelector('#f7-qt')?.value || null, price: document.querySelector('#f7-pz')?.value || null,
      submitDisabled: !!document.querySelector('#f7-tk')?.closest('form')?.querySelector('button[type="submit"]')?.disabled }));
    const controllerSurvived = JSON.stringify(switchedBusy) === JSON.stringify(busyBeforeSwitch);
    assert.deepEqual(switchedBusy, busyBeforeSwitch, 'trade draft and busy state survive the modern presentation switch');
    await capture(q, 'operations/trades-confirmed-pending');
    const pendingControlVisual = await captureCompactPending(q, 'operations/trades-confirmed-pending-control', {
      fieldSelector: '#f7-tk', expectedText: 'writing|scrittura' });
    assert.deepEqual(await q.js(() => ({ ticker: document.querySelector('#f7-tk')?.value || null,
      quantity: document.querySelector('#f7-qt')?.value || null, price: document.querySelector('#f7-pz')?.value || null,
      submitDisabled: !!document.querySelector('#f7-tk')?.closest('form')?.querySelector('button[type="submit"]')?.disabled })),
    busyBeforeSwitch, 'Trade remains pending throughout all seven viewport captures');
    await q.toggle('classic');
    await q.waitFor(() => /Trade\s+#599/.test(document.querySelector('main [data-page="trades"]')?.innerText || ''),
      'trade result received after mode round-trip', 22000);
    const confirmed = await routeWrites(q, 'POST', '/trade');
    const previewRequest = (await routeWrites(q, 'POST', '/trade/preview')).at(-1);
    assert.equal(confirmed.length, 1, 'mode switches did not resubmit the trade');
    assert.deepEqual({ ticker: confirmed[0].input.ticker, quantity: confirmed[0].input.quantita,
      preview_id: confirmed[0].input.preview_id }, { ticker: 'SYNQA', quantity: 2, preview_id: 'qa-trade-preview-confirmed' });
    assert.equal(previewRequest.input.senza_decisione, true, 'fixture uses the explicit no-decision path');
    await capture(q, 'operations/trades-commit-result');

    await q.setValue('#f7-tk', 'SYNQX');
    await q.setValue('#f7-qt', '1');
    await q.setValue('#f7-pz', '22');
    await q.fixture({ setWrite: { '/trade/preview': tradePreview('qa-trade-preview-uncertain'),
      '/trade': { status: 503, body: { detail: 'Synthetic uncertain commit response.' } } } });
    await submitTrade(q);
    await q.waitFor(() => !!document.querySelector('.cfm-modal[role="alertdialog"]'), 'uncertain trade confirmation');
    await q.click('.cfm-modal .cfm-btn.go');
    await q.waitFor(() => /Synthetic uncertain commit response/.test(document.querySelector('main [data-page="trades"]')?.innerText || ''),
      'uncertain trade outcome disclosed', 5000);
    await q.pause(200);
    const allTradeWrites = await routeWrites(q, 'POST', '/trade');
    assert.equal(allTradeWrites.length, 2, 'uncertain server response never causes an automatic second write');
    assert.match(await text(q, 'main [data-page="trades"]'), /unknown|uncertain|incerto|non so/i,
      'UI must describe the commit as uncertain');
    await capture(q, 'operations/trades-uncertain-result', [[1920, 1080]]);
    return { assertionResults: { invalidTradeRejectedBeforePreview: true, cancelDoesNotWrite: true,
      confirmWritesOnce: confirmed.length === 1, pendingControllerSurvivesPresentationSwitch: controllerSurvived,
      pendingSubmitVisibleAndDisabledAtCompactViewport: pendingControlVisual.before.fullyVisible && pendingControlVisual.before.disabled,
      orderSizeComparesWithMedian: sizeReference.bars > 0,
      uncertainCommitDoesNotRetry: allTradeWrites.length === 2, responseContractEchoed: previewRequest.input.senza_decisione === true },
      writes: allTradeWrites.map(item => ({ method: item.method, route: item.route, input: item.input })),
      sizeReference, pendingControlVisual, compactCancel, defaultCancelFocused: focusDefaultCancel,
      fixtureVsReal: { allMutationsSynthetic: true, backendStarted: false, llmStarted: false } };
  });
}

async function tradeReadStates(q) {
  await executeFixtureScenario(q, 'trades', 'positions-loading-empty-and-refresh-error-never-keep-stale-values', async () => {
    const emptyPortfolio = clone(fixtureData.portfolio);
    emptyPortfolio.positions = [];
    emptyPortfolio.n_positions = 0;
    emptyPortfolio.totale_valore_mercato_eur = 0;
    emptyPortfolio.totale_pl_eur = 0;
    await q.toggle('classic');
    await q.visit('/dashboard');
    await q.fixture({ clearAll: true, setRead: {
      '/portfolio': { body: emptyPortfolio, delayMs: 8000 },
      '/trades': { count: 0, trades: [] },
      '/cash/movements': { count: 0, movements: [] },
      '/decisions': { decisions: [] },
      '/fx': clone(fixtureData.fx),
    } });
    await reloadCurrentApp(q, 'fresh app renderer for first Trade portfolio read', '#/dashboard');
    await q.visit('/trades');
    await q.waitFor(() => !!document.querySelector('main [data-page="trades"] #f7-tk')
      && /caric|load/i.test(document.querySelector('main [data-page="trades"] .te-book')?.innerText || ''),
    'Trade first-read loading state');
    await q.toggle('modern');
    const loadingVisible = await q.js(() => /caric|load/i.test(document.querySelector('main [data-page="trades"] .te-book')?.innerText || ''));
    assert.ok(loadingVisible, 'modern Trade says the position book is loading before the portfolio response');
    await capture(q, 'operations/trades-read-loading-modern', [[1920, 1080]]);
    assert.ok(await q.js(() => /caric|load/i.test(document.querySelector('main [data-page="trades"] .te-book')?.innerText || '')),
      'Trade portfolio remains loading after its presentation switch and capture');
    await q.waitFor(() => !/caric|load/i.test(document.querySelector('main [data-page="trades"] .te-book')?.innerText || ''),
      'Trade delayed portfolio response', 25000);
    const emptyPositions = await q.js(() => ({ selectedRows: document.querySelectorAll('main [data-page="trades"] .te-book .te-row').length,
      firstRow: document.querySelector('main [data-page="trades"] .te-book')?.innerText || '' }));
    assert.equal(emptyPositions.selectedRows, 0, 'an empty portfolio does not keep synthetic position rows visible');
    assert.ok(emptyPositions.firstRow.length > 0, 'the empty book is explained in its table');
    await q.click('#f7-mode-cash');
    const emptyCashRegister = await text(q, 'main [data-page="trades"] .te-register');
    await q.click('#f7-mode-trade');
    assert.ok(/no cash|nessun|moviment/i.test(emptyCashRegister), 'an empty cash archive is distinguished from unread data');

    await q.fixture({ setRead: { '/portfolio': { status: 503, body: { detail: 'Synthetic portfolio read unavailable.' } } } });
    await q.click('main [data-page="trades"] .te-today button[aria-busy]');
    await q.waitFor(() => /Synthetic portfolio read unavailable/.test(
      document.querySelector('main [data-page="trades"] .te-book')?.innerText || ''),
    'Trade refresh error replaces the old position book');
    const failedRefresh = await q.js(() => ({ tickerButtons: document.querySelectorAll('main [data-page="trades"] .te-book .te-row').length,
      text: document.querySelector('main [data-page="trades"] .te-book')?.innerText || '' }));
    assert.equal(failedRefresh.tickerButtons, 0, 'Trade drops old position values after refresh failure instead of presenting them as current');
    assert.match(failedRefresh.text, /Synthetic portfolio read unavailable/);
    const reads = (await q.snapshot()).requests.filter(item => item.method === 'GET' && ['/portfolio', '/trades', '/cash/movements'].includes(item.route));
    assert.ok(reads.some(item => item.route === '/portfolio') && reads.some(item => item.route === '/trades')
      && reads.some(item => item.route === '/cash/movements'), 'Trade read-state checks exercise the intended synthetic endpoints');
    await capture(q, 'operations/trades-read-error-modern', [[1920, 1080]]);
    await q.fixture({ clearAll: true });
    await q.toggle('classic');
    return { assertionResults: { portfolioLoadingIsExplicit: loadingVisible, emptyPositionBookIsExplicit: emptyPositions.selectedRows === 0,
      emptyCashRegisterIsExplicit: /no cash|nessun|moviment/i.test(emptyCashRegister), failedRefreshDiscardsOldPositions: failedRefresh.tickerButtons === 0 },
      loading: 'delayed synthetic GET /portfolio returned no private or persisted data', emptyPositions, emptyCashRegister,
      failedRefresh, fixtureVsReal: { allReadsSynthetic: true, writes: 0, backendStarted: false, llmStarted: false } };
  });
}

async function cashActions(q) {
  await executeFixtureScenario(q, 'trades', 'cash-threshold-confirmation-cancellation-and-inflight-switch', async () => {
    await q.visit('/trades');
    await q.click('#f7-mode-cash');
    await q.waitFor(() => !!document.querySelector('#f7-mv-imp'), 'cash register form');
    await q.setValue('#f7-mv-imp', 'invalid amount');
    await submitCash(q);
    await q.waitFor(() => !!document.querySelector('.te-cashform [data-avviso]'), 'invalid cash amount explanation');
    assert.equal((await routeWrites(q, 'POST', '/cash/movement')).length, 0, 'invalid amount stays client-side');

    await q.fixture({ setWrite: { '/cash/movement': { status: 422,
      body: { code: 'cash_threshold', detail: 'GUARDIA IMPORTO: confirm with conferma=true' } } } });
    await q.setValue('#f7-mv-imp', '1250');
    await q.setValue('#f7-mv-dt', '2026-09-29');
    await q.setValue('#f7-mv-nt', 'Synthetic QA deposit');
    await submitCash(q);
    await q.waitFor(() => !!document.querySelector('.te-cash-confirm .te-confirm-go'), 'backend cash threshold remedy');
    await q.js(() => { const dialog = document.querySelector('.te-cash-confirm'); if (dialog) dialog.dataset.opsStableId = 'cash-threshold-dialog'; });
    await q.toggle('modern');
    assert.equal(await q.js(() => document.querySelector('.te-cash-confirm')?.dataset.opsStableId || null),
      'cash-threshold-dialog', 'cash threshold confirmation remains pending across mode switch');
    await capture(q, 'operations/cash-threshold-review-modern');
    await q.toggle('classic');
    const rejected = (await routeWrites(q, 'POST', '/cash/movement'))[0];
    assert.equal(rejected.input.importo_eur, 1250);
    assert.equal(rejected.input.nota, 'Synthetic QA deposit');

    await q.fixture({ setWrite: { '/cash/movement': { body: { ok: true, movement_id: 601,
      tipo: 'DEPOSIT', importo_eur: 1250, cash_disponibile_eur: 13695.67, cash_note: null }, delayMs: 20000 } } });
    await q.click('.te-cash-confirm .te-confirm-go');
    await q.waitFor(() => document.querySelector('#f7-mv-imp')?.closest('form')?.querySelector('button[type="submit"]')?.disabled,
      'cash write busy state');
    const started = await routeWrites(q, 'POST', '/cash/movement');
    assert.equal(started.length, 2, 'intentional confirmation resends exactly once');
    assert.equal(started[1].input.conferma, true, 'remedy sends the frozen body with conferma=true');
    const busyBeforeSwitch = await q.js(() => ({ amount: document.querySelector('#f7-mv-imp')?.value || null,
      date: document.querySelector('#f7-mv-dt')?.value || null, note: document.querySelector('#f7-mv-nt')?.value || null,
      submitDisabled: !!document.querySelector('#f7-mv-imp')?.closest('form')?.querySelector('button[type="submit"]')?.disabled }));
    assert.deepEqual(busyBeforeSwitch, { amount: '1250', date: '2026-09-29', note: 'Synthetic QA deposit', submitDisabled: true });
    await q.toggle('modern');
    const switchedBusy = await q.js(() => ({ amount: document.querySelector('#f7-mv-imp')?.value || null,
      date: document.querySelector('#f7-mv-dt')?.value || null, note: document.querySelector('#f7-mv-nt')?.value || null,
      submitDisabled: !!document.querySelector('#f7-mv-imp')?.closest('form')?.querySelector('button[type="submit"]')?.disabled }));
    const controllerSurvived = JSON.stringify(switchedBusy) === JSON.stringify(busyBeforeSwitch);
    assert.deepEqual(switchedBusy, busyBeforeSwitch, 'cash draft and in-flight disabled state survive the modern presentation switch');
    await capture(q, 'operations/cash-write-pending');
    const pendingControlVisual = await captureCompactPending(q, 'operations/cash-write-pending-control', {
      fieldSelector: '#f7-mv-imp', expectedText: 'sending|invio' });
    assert.deepEqual(await q.js(() => ({ amount: document.querySelector('#f7-mv-imp')?.value || null,
      date: document.querySelector('#f7-mv-dt')?.value || null, note: document.querySelector('#f7-mv-nt')?.value || null,
      submitDisabled: !!document.querySelector('#f7-mv-imp')?.closest('form')?.querySelector('button[type="submit"]')?.disabled })),
    busyBeforeSwitch, 'cash action stays pending throughout all seven viewport captures');
    await q.toggle('classic');
    await q.waitFor(() => /Movement 601 recorded|Movimento 601 a registro/.test(document.querySelector('.lb-annuncio')?.innerText || ''),
      'cash write result after mode switch', 22000);
    assert.ok(controllerSurvived, 'cash controller and form persist during delayed write');
    assert.equal((await routeWrites(q, 'POST', '/cash/movement')).length, 2, 'mode changes do not repeat cash confirmation');
    await capture(q, 'operations/cash-write-result');

    await q.fixture({ setWrite: { '/cash/movement': { status: 422,
      body: { code: 'cash_threshold', detail: 'GUARDIA IMPORTO: confirm with conferma=true' } } } });
    await q.setValue('#f7-mv-imp', '1300');
    await q.setValue('#f7-mv-nt', 'Synthetic cancelled cash request');
    await submitCash(q);
    await q.waitFor(() => !!document.querySelector('.te-cash-confirm .te-confirm-cancel'), 'cash guard cancellation control');
    await q.click('.te-cash-confirm .te-confirm-cancel');
    await q.pause(100);
    const writes = await routeWrites(q, 'POST', '/cash/movement');
    assert.equal(writes.length, 3, 'cancelling a second 422 does not issue a confirmed write');
    assert.equal(writes[2].input.conferma, undefined);
    return { assertionResults: { invalidAmountClientRejected: true, thresholdResponseOffersRemedy: true,
      explicitConfirmationUsesFrozenPayload: writes[1].input.conferma === true,
      inFlightControllerSurvivesModeSwitch: controllerSurvived, modeSwitchDoesNotRepeatWrite: writes.length === 3,
      pendingSubmitVisibleAndDisabledAtCompactViewport: pendingControlVisual.before.fullyVisible && pendingControlVisual.before.disabled,
      cancelLeavesOnly422Request: writes.length === 3 },
      writes: writes.map(item => ({ method: item.method, route: item.route, input: item.input })),
      pendingControlVisual, fixtureVsReal: { allMutationsSynthetic: true } };
  });
}

async function openingActions(q) {
  await executeFixtureScenario(q, 'trades', 'opening-preview-cancel-and-confirm-once-while-pending', async () => {
    const unrelatedWriteCountBefore = (await q.snapshot()).writes
      .filter(item => ['/trade', '/trade/preview', '/cash/movement'].includes(item.route)).length;
    await q.visit('/trades');
    await q.click('#f7-mode-opening');
    await q.waitFor(() => !!document.querySelector('[data-opening-entry] #f7-op-ticker'), 'opening-balance controller');
    const fields = [
      ['#f7-op-ticker', 'SYNQA'], ['#f7-op-name', 'Synthetic QA Holding'],
      ['#f7-op-qty', '2'], ['#f7-op-cost', '40'], ['#f7-op-currency', 'EUR'],
      ['#f7-op-day', '2026-09-29'], ['#f7-op-precision', 'day'],
      ['#f7-op-source', 'Synthetic acquisition register'], ['#f7-op-note', 'Synthetic fixture only.'],
    ];
    for (const [selector, value] of fields) await q.setValue(selector, value);
    await q.fixture({ setWrite: { '/positions/opening/preview': openingResponse(true) } });
    await clickText(q, '[data-opening-entry] form', 'preview|anteprima|check|verifica');
    await q.waitFor(() => !!document.querySelector('.cfm-modal[role="alertdialog"]'), 'opening position preview confirmation');
    const previewBody = (await routeWrites(q, 'POST', '/positions/opening/preview'))[0]?.input;
    assert.deepEqual(previewBody, openingBody(), 'preview sends the visible synthetic opening balance');
    await q.js(() => { const dialog = document.querySelector('.cfm-modal[role="alertdialog"]'); if (dialog) dialog.dataset.opsStableId = 'opening-preview-dialog'; });
    await q.toggle('modern');
    assert.equal(await q.js(() => document.querySelector('.cfm-modal[role="alertdialog"]')?.dataset.opsStableId || null),
      'opening-preview-dialog', 'opening confirmation stays mounted after mode switch');
    await q.waitFor(() => document.activeElement?.matches('.cfm-modal .cfm-btn.no'),
      'opening dialog cancel focus restored after mode switch');
    await capture(q, 'operations/opening-preview-confirmation-modern');
    await q.waitFor(() => document.activeElement?.matches('.cfm-modal .cfm-btn.no'),
      'opening dialog retains focus after viewport captures');
    await q.key('Escape');
    await q.waitFor(() => !document.querySelector('.cfm-modal[role="alertdialog"]'), 'opening preview Escape cancel');
    assert.equal((await routeWrites(q, 'POST', '/positions/opening')).length, 0, 'Escape cancels without committing');
    await q.toggle('classic');

    await q.fixture({ setWrite: { '/positions/opening/preview': openingResponse(true),
      '/positions/opening': { body: openingResponse(false), delayMs: 20000 } } });
    await clickText(q, '[data-opening-entry] form', 'preview|anteprima|check|verifica');
    await q.waitFor(() => !!document.querySelector('.cfm-modal[role="alertdialog"]'), 'opening second confirmation');
    await q.toggle('modern');
    await capture(q, 'operations/opening-pending-confirmation');
    await q.click('.cfm-modal .cfm-btn.go');
    await q.waitFor(() => document.querySelector('[data-opening-entry] fieldset')?.disabled,
      'opening write busy state');
    const writes = await routeWrites(q, 'POST', '/positions/opening');
    assert.equal(writes.length, 1, 'opening confirmation sends exactly one write');
    assert.deepEqual(writes[0].input, { ...openingBody(), preview_id: 'qa-opening-preview-1' });
    const busyBeforeSwitch = await q.js(() => ({ ticker: document.querySelector('#f7-op-ticker')?.value || null,
      quantity: document.querySelector('#f7-op-qty')?.value || null, cost: document.querySelector('#f7-op-cost')?.value || null,
      disabled: !!document.querySelector('[data-opening-entry] fieldset')?.disabled }));
    assert.deepEqual(busyBeforeSwitch, { ticker: 'SYNQA', quantity: '2', cost: '40', disabled: true });
    const switchedBusy = await q.js(() => ({ ticker: document.querySelector('#f7-op-ticker')?.value || null,
      quantity: document.querySelector('#f7-op-qty')?.value || null, cost: document.querySelector('#f7-op-cost')?.value || null,
      disabled: !!document.querySelector('[data-opening-entry] fieldset')?.disabled }));
    const controllerSurvived = JSON.stringify(switchedBusy) === JSON.stringify(busyBeforeSwitch);
    assert.deepEqual(switchedBusy, busyBeforeSwitch, 'opening draft and write busy state survive the modern presentation switch');
    await capture(q, 'operations/opening-write-pending');
    const pendingControlVisual = await captureCompactPending(q, 'operations/opening-write-pending-control', {
      fieldSelector: '#f7-op-ticker', expectedText: 'writing|scrittura|recording|registrazione' });
    assert.deepEqual(await q.js(() => ({ ticker: document.querySelector('#f7-op-ticker')?.value || null,
      quantity: document.querySelector('#f7-op-qty')?.value || null, cost: document.querySelector('#f7-op-cost')?.value || null,
      disabled: !!document.querySelector('[data-opening-entry] fieldset')?.disabled })),
    busyBeforeSwitch, 'opening form remains locked throughout all seven viewport captures');
    await q.toggle('classic');
    await q.waitFor(() => document.querySelector('[data-opening-receipt]')?.innerText.includes('#82') || false,
      'opening receipt after mode switch', 22000);
    assert.ok(controllerSurvived, 'opening controller survives its pending write across presentation changes');
    assert.equal((await routeWrites(q, 'POST', '/positions/opening')).length, 1, 'mode change did not resend opening write');
    const unrelatedWritesAfter = (await q.snapshot()).writes
      .filter(item => ['/trade', '/trade/preview', '/cash/movement'].includes(item.route)).length;
    assert.equal(unrelatedWritesAfter, unrelatedWriteCountBefore, 'opening balance does not use trade/cash API paths');
    await capture(q, 'operations/opening-receipt');
    return { assertionResults: { previewInputMatchesVisibleDraft: true, escapeCancelsWithoutCommit: true,
      delayedCommitSurvivesModeSwitch: controllerSurvived, openingWrittenOnce: true,
      noTradeOrCashEndpointUsed: unrelatedWritesAfter === unrelatedWriteCountBefore,
      pendingSubmitVisibleAndDisabledAtCompactViewport: pendingControlVisual.before.fullyVisible && pendingControlVisual.before.disabled },
      preview: previewBody, write: writes[0], receipt: await text(q, '[data-opening-receipt]'),
      pendingControlVisual, fixtureVsReal: { allMutationsSynthetic: true, backendStarted: false, llmStarted: false } };
  });
}

async function openingReadStates(q) {
  await executeFixtureScenario(q, 'trades', 'opening-register-loading-empty-and-error-are-distinct', async () => {
    await q.toggle('classic');
    await q.visit('/dashboard');
    await q.fixture({ clearAll: true, setRead: {
      '/positions/opening': { body: { openings: clone(fixtureData.openingRows) }, delayMs: 8000 },
    } });
    await reloadCurrentApp(q, 'fresh app renderer for first opening register read', '#/dashboard');
    await q.visit('/trades');
    await q.click('#f7-mode-opening');
    await q.waitFor(() => !!document.querySelector('[data-opening-entry] .te-reg[aria-busy="true"]'),
      'opening register loading');
    await q.toggle('modern');
    const loading = await q.js(() => ({ busy: document.querySelector('[data-opening-entry] .te-reg')?.getAttribute('aria-busy'),
      copy: document.querySelector('[data-opening-entry] .te-reg')?.innerText || '' }));
    assert.equal(loading.busy, 'true');
    assert.match(loading.copy, /loading|caric|reading|lettura/i);
    await capture(q, 'operations/opening-register-loading-modern', [[1920, 1080]]);
    assert.equal(await q.js(() => document.querySelector('[data-opening-entry] .te-reg')?.getAttribute('aria-busy')),
      'true', 'Opening register remains busy after its presentation switch and capture');
    await q.waitFor(() => document.querySelector('[data-opening-entry] .te-reg')?.getAttribute('aria-busy') === 'false',
      'opening register response', 25000);

    await q.fixture({ setRead: { '/positions/opening': { openings: [] } } });
    await q.click('[data-opening-entry] [data-qa="opening-refresh"]');
    await q.waitFor(() => /no documented|nessuna posizione iniziale/i.test(document.querySelector('[data-opening-entry] .te-reg')?.innerText || ''),
      'opening register empty state');
    const empty = await text(q, '[data-opening-entry] .te-reg');
    assert.match(empty, /no documented|nessuna posizione iniziale/i, 'an empty opening register is identified explicitly');
    await capture(q, 'operations/opening-register-empty-modern', [[1920, 1080]]);

    await q.fixture({ setRead: { '/positions/opening': { status: 503, body: { detail: 'Synthetic opening register unavailable.' } } } });
    await q.click('[data-opening-entry] [data-qa="opening-refresh"]');
    await q.waitFor(() => /Synthetic opening register unavailable/.test(
      document.querySelector('[data-opening-entry] .te-reg')?.innerText || ''), 'opening register read error');
    const failed = await q.js(() => ({ text: document.querySelector('[data-opening-entry] .te-reg')?.innerText || '',
      oldRows: document.querySelectorAll('[data-opening-entry] .te-reg .te-row').length }));
    assert.match(failed.text, /Synthetic opening register unavailable/);
    assert.equal(failed.oldRows, 0, 'failed opening refresh does not leave old rows presented as current');
    await capture(q, 'operations/opening-register-error-modern', [[1920, 1080]]);
    await q.fixture({ clearAll: true });
    await q.toggle('classic');
    return { assertionResults: { loadingExplicit: loading.busy === 'true', emptyExplicit: /no documented|nessuna posizione iniziale/i.test(empty),
      readErrorExplicit: /Synthetic opening register unavailable/.test(failed.text), staleRowsCleared: failed.oldRows === 0 },
      loading, empty, failed, fixtureVsReal: { readsSynthetic: true, writes: 0, backendStarted: false, llmStarted: false } };
  });
}

// Movimenti in stile Nuova (05/10/2026): una pagina sola, Registro | Dettaglio, con Attività per mese e
// Realizzato. Le vecchie viste Scie e Diario vivono nel dettaglio (Storia del titolo, commento completo).
const MV = 'main [data-page="movements"] .bbn-movimenti';
async function movementsActions(q) {
  await executeFixtureScenario(q, 'movements', 'register-filters-month-and-detail-use-fixture-only', async () => {
    await q.toggle('classic');
    await q.visit('/movements');
    await q.waitFor(() => !!document.querySelector('main [data-page="movements"] .bbn-movimenti [data-mov-row]'), 'Movements page');
    await q.waitFor(() => !document.querySelector('main [data-page="movements"] [data-mov-azione="aggiorna"]')?.disabled,
      'trade and cash archive reads complete');
    const before = await q.counts();
    const initialRows = await q.js(() => document.querySelectorAll('main [data-page="movements"] [data-mov-row]').length);
    assert.ok(initialRows >= 5, 'expected mixed synthetic register rows, found ' + initialRows);
    await capture(q, 'operations/movements-register');

    await q.click(MV + ' [data-mov-filtro="CASSA"]');
    const cashRows = await q.js(() => [...document.querySelectorAll('main [data-page="movements"] [data-mov-row]')].map(row => row.dataset.specie));
    assert.ok(cashRows.length > 0 && cashRows.every(kind => kind === 'cassa'),
      'Cash filter keeps only cash rows: ' + JSON.stringify(cashRows));
    const modernCaptures = [];
    modernCaptures.push(await captureModernRetention(q, 'movements-register-cash-selected', 'movements', {
      controllerSelector: MV,
      stateSelectors: [MV + ' .mv-fbar button[aria-pressed="true"]', MV + ' [data-mov-dettaglio] h2'],
      evidenceSelector: MV + ' .mv-list',
      rowSelector: MV + ' [data-mov-row]',
    }, ['/trades', '/cash/movements'], {
      before: state => {
        assert.equal(state.rowCount, cashRows.length, 'Modern cash-filtered register keeps the selected synthetic rows');
        assert.ok(state.controls[0].elements.some(item => /CASH|CASSA/i.test(item.text) && item.pressed === 'true'),
          'cash filter remains selected before its Modern capture');
      },
    }));

    await q.click(MV + ' [data-mov-filtro="TUTTI"]');
    await q.click(MV + ' [data-mov-row][data-specie="titolo"]');
    await q.waitFor(() => !!document.querySelector('main [data-page="movements"] [data-mov-dettaglio^="t:"]'), 'security detail');
    const detail = await q.js(() => {
      const ticker = document.querySelector('main [data-page="movements"] [data-mov-dettaglio] h2')?.firstChild?.textContent?.trim() || '';
      const history = [...document.querySelectorAll('main [data-page="movements"] [data-mov-storia]')].length;
      return { ticker, history };
    });
    assert.ok(detail.ticker && detail.history >= 1, 'security detail shows its history: ' + JSON.stringify(detail));
    await capture(q, 'operations/movements-detail');

    const month = await q.js(() => document.querySelector('main [data-page="movements"] [data-mov-mese]:not(:disabled)')?.dataset.movMese || null);
    assert.ok(month, 'activity chart has a selectable month');
    await q.click(`${MV} [data-mov-mese="${month}"]`);
    const heads = await q.js(() => [...document.querySelectorAll('main [data-page="movements"] [data-mov-mhead]')].map(h => h.dataset.movMhead));
    assert.deepEqual(heads, [month], 'month selection keeps only that month in the register');
    await q.click(MV + ' [data-mov-togli="mese"]');
    await q.waitFor(() => !document.querySelector('main [data-page="movements"] [data-mov-togli="mese"]'), 'month filter cleared');
    const after = await q.counts();
    const readDeltas = Object.fromEntries(Object.entries(after)
      .filter(([key]) => /^GET \/(trades|cash\/movements)/.test(key))
      .map(([key, value]) => [key, value - (before[key] || 0)]).filter(([, value]) => value));
    assert.deepEqual(readDeltas, {}, 'derived filter, month and detail changes must not issue repeated reads');
    return { assertionResults: { mixedRegisterLoaded: initialRows >= 5,
      cashFilterOnlyShowsCash: cashRows.length > 0 && cashRows.every(kind => kind === 'cassa'),
      detailShowsSecurityHistory: detail.history >= 1, monthFilterKeepsOneMonth: heads.length === 1,
      derivedControlsDoNotRefetch: Object.keys(readDeltas).length === 0,
      modernCaptureRetainsControllerAndIssuesNoReads: modernCaptures.length === 1 },
      initialRows, cashRows, detail, month, readDeltas, modernCaptures,
      fixtureVsReal: { dataSource: 'synthetic fixtures only', writes: 0 } };
  });
}

async function movementsReadStates(q) {
  await executeFixtureScenario(q, 'movements', 'register-loading-empty-and-stale-refresh-error', async () => {
    await q.toggle('classic');
    await q.visit('/dashboard');
    await q.waitFor(() => !!document.querySelector('main .bbn-dashboard'), 'Dashboard mounted before Movements fixtures');
    await q.fixture({ clearAll: true, setRead: {
      '/trades': { body: { count: fixtureData.trades.length, trades: clone(fixtureData.trades) }, delayMs: 8000 },
      '/cash/movements': { body: { count: 2, movements: [
        { id: 811, type: 'DEPOSIT', amount_eur: 500, date: '2026-09-26', note: 'Synthetic movements state-check deposit' },
        { id: 812, type: 'WITHDRAWAL', amount_eur: 125, date: '2026-09-28', note: 'Synthetic movements state-check withdrawal' },
      ] }, delayMs: 8000 },
    } });
    await q.visit('/movements');
    await q.waitFor(() => !!document.querySelector('main [data-page="movements"] [data-mov-stato="caricamento"]'), 'Movements initial loading');
    await q.toggle('modern');
    const loading = await text(q, MV + ' [data-mov-stato="caricamento"]');
    assert.ok(loading, 'Movements reports that both archives are still loading');
    await capture(q, 'operations/movements-read-loading-modern', [[1920, 1080]]);
    assert.ok(await q.js(() => !!document.querySelector('.bb-page-modern[data-page="movements"] [data-mov-stato="caricamento"]')),
      'Movements remains in its first-read state after the mode switch and capture');
    await q.waitFor(() => !document.querySelector('main [data-page="movements"] [data-mov-stato="caricamento"]'), 'Movements archive reads complete', 25000);

    await q.fixture({ setRead: { '/trades': { count: 0, trades: [] }, '/cash/movements': { count: 0, movements: [] } } });
    await q.click(MV + ' [data-mov-azione="aggiorna"]');
    await q.waitFor(() => !!document.querySelector('main [data-page="movements"] [data-mov-stato="vuoto"] b'), 'Movements empty archives');
    const emptyText = await text(q, MV + ' [data-mov-stato="vuoto"]');
    assert.ok(emptyText && emptyText.length > 30, 'successful empty reads have a distinct empty-state explanation');
    await capture(q, 'operations/movements-read-empty-modern', [[1920, 1080]]);

    await q.fixture({ clearReads: ['/trades', '/cash/movements'] });
    await q.click(MV + ' [data-mov-azione="aggiorna"]');
    await q.waitFor(() => document.querySelectorAll('main [data-page="movements"] [data-mov-row]').length >= 4,
      'Movements populated synthetic register restored');
    const rowsBeforeError = await q.js(() => document.querySelectorAll('main [data-page="movements"] [data-mov-row]').length);
    assert.ok(rowsBeforeError >= 4);
    await q.fixture({ setRead: { '/trades': { status: 503, body: { detail: 'Synthetic trade archive unavailable during refresh.' } } } });
    await q.click(MV + ' [data-mov-azione="aggiorna"]');
    await q.waitFor(() => !!document.querySelector('main [data-page="movements"] [data-mov-stato="vecchio"][role="alert"]'),
      'Movements failed refresh announces stale securities');
    const stale = await q.js(() => ({ rows: document.querySelectorAll('main [data-page="movements"] [data-mov-row]').length,
      alert: document.querySelector('main [data-page="movements"] [data-mov-stato="vecchio"][role="alert"]')?.innerText || '' }));
    assert.ok(stale.rows >= rowsBeforeError, 'Movements keeps its last successful archive rows during a failed refresh');
    assert.match(stale.alert, /Synthetic trade archive unavailable during refresh/);
    assert.ok(/stale|old|non aggiorn|vecchi|precedent/i.test(stale.alert), 'Movements labels the retained rows as stale');
    await capture(q, 'operations/movements-read-stale-modern', [[1920, 1080]]);
    await q.fixture({ clearAll: true });
    await q.toggle('classic');
    return { assertionResults: { loadingExplicit: !!loading, emptyExplicit: !!emptyText,
      failedRefreshAnnouncesStaleData: stale.rows >= rowsBeforeError && /Synthetic trade archive unavailable/.test(stale.alert),
      retainedRowsAreDisclosedAsStale: /stale|old|non aggiorn|vecchi|precedent/i.test(stale.alert) },
      loading, emptyText, rowsBeforeError, stale,
      fixtureVsReal: { allReadsSynthetic: true, writes: 0, backendStarted: false, llmStarted: false } };
  });
}

async function mandateActions(q) {
  await executeFixtureScenario(q, 'mandato', 'seven-section-validation-preview-save-and-pending-switch', async () => {
    await q.toggle('classic');
    await q.visit('/mandato');
    await q.waitFor(() => !!document.querySelector('#panel-mandato .mandato-page'), 'Mandato form data ready');
    const previewWritesBeforeScenario = (await routeWrites(q, 'POST', '/mandato/anteprima')).length;
    const saveWritesBeforeScenario = (await routeWrites(q, 'PUT', '/mandato')).length;
    const sections = ['profilo', 'rischio', 'sizing', 'cassa', 'disciplina', 'opzioni', 'note'];
    for (const section of sections) {
      await q.click('#panel-mandato .mandato-index a[href="#mandato-' + section + '"]');
      await q.waitFor(id => !!document.querySelector('#panel-mandato #mandato-' + id)
        && !document.querySelector('#panel-mandato #mandato-' + id).hidden,
      'Mandato section ' + section, 4000, section);
      const fieldCount = await q.js(id => document.querySelectorAll('#panel-mandato #mandato-' + id + ' input,#panel-mandato #mandato-' + id + ' select,#panel-mandato #mandato-' + id + ' textarea').length, section);
      assert.ok(fieldCount > 0, section + ' section has no usable controls');
    }
    await q.click('#panel-mandato .mandato-index a[href="#mandato-rischio"]');
    const originalVar = await q.js(() => document.querySelector('#panel-mandato #m-var99_1g_pct')?.value ?? null);
    assert.notEqual(originalVar, null, 'fixture contains required daily VaR field');
    await q.setValue('#panel-mandato #m-var99_1g_pct', 'not-a-number');
    const previewCountBeforeInvalid = (await routeWrites(q, 'POST', '/mandato/anteprima')).length;
    await q.click('#panel-mandato button[data-action="preview"]');
    await q.waitFor(() => !!document.querySelector('#panel-mandato .field-error[role="alert"]'), 'Mandato local numeric validation');
    assert.equal((await routeWrites(q, 'POST', '/mandato/anteprima')).length, previewCountBeforeInvalid,
      'invalid mandate numbers are rejected before preview');
    await q.setValue('#panel-mandato #m-var99_1g_pct', originalVar);

    await q.click('#panel-mandato .mandato-index a[href="#mandato-note"]');
    const updatedMandate = clone(fixtureData.mandato);
    updatedMandate.impronta = 'synthetic-fixture-mandate-v2';
    updatedMandate.valori.note.note_per_il_comitato = 'Synthetic committee note saved by isolated desktop QA.';
    await q.setValue('#panel-mandato #m-note_per_il_comitato', updatedMandate.valori.note.note_per_il_comitato);
    await q.fixture({ setRead: { '/mandato': updatedMandate }, setWrite: {
      '/mandato/anteprima': { body: { testo: 'Synthetic preview: seven mandate sections validated.',
        impronta: updatedMandate.impronta, origine: 'synthetic_fixture', output_language: 'en' }, delayMs: 20000 },
      '/mandato': { body: { ok: true, synthetic: true }, delayMs: 20000 },
    } });
    await q.setValue('#panel-mandato #m-esclusioni', 'Synthetic excluded investment group.');
    await q.toggle('modern');
    const compactReachability = await q.withViewport(900, 700, async () => {
      const lastQuestionClick = await q.click('#panel-mandato #m-esclusioni');
      await capture(q, 'operations/mandato-last-question-reachable-900x700', [[900, 700]]);
      const previewClick = await q.click('#panel-mandato button[data-action="preview"]');
      await q.waitFor(() => document.querySelector('#panel-mandato button[data-action="preview"]')?.disabled,
        'Mandato compact preview busy state');
      const surface = await q.js(() => {
        const targets = ['#panel-mandato #m-esclusioni', '#panel-mandato button[data-action="preview"]'];
        return targets.map(selector => {
          const element = document.querySelector(selector);
          if (!element) return { selector, exists: false };
          const rect = element.getBoundingClientRect();
          const style = getComputedStyle(element);
          const x = rect.left + rect.width / 2, y = rect.top + rect.height / 2;
          const hit = document.elementFromPoint(x, y);
          return { selector, exists: true, disabled: !!element.disabled, rect: { left: rect.left, top: rect.top,
            right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height },
            inViewport: rect.left >= 0 && rect.right <= innerWidth && rect.top >= 0 && rect.bottom <= innerHeight,
            visible: style.display !== 'none' && style.visibility !== 'hidden', hitTest: !!hit && (hit === element || element.contains(hit) || hit.closest(selector) === element) };
        });
      });
      assert.ok(surface.every(item => item.exists && item.visible && item.inViewport && item.hitTest),
        'Mandato final textarea and preview action remain visible and hit-testable at 900x700: ' + JSON.stringify(surface));
      assert.equal(surface[1].disabled, true, 'preview action is genuinely busy while its synthetic request is pending');
      await capture(q, 'operations/mandato-preview-compact-pending-900x700', [[900, 700]]);
      return { lastQuestionClick, previewClick, surface };
    });
    const previewBusyDraft = await q.js(() => ({ note: document.querySelector('#panel-mandato #m-note_per_il_comitato')?.value || null,
      exclusions: document.querySelector('#panel-mandato #m-esclusioni')?.value || null,
      previewDisabled: !!document.querySelector('#panel-mandato button[data-action="preview"]')?.disabled }));
    assert.deepEqual(previewBusyDraft, { note: updatedMandate.valori.note.note_per_il_comitato,
      exclusions: 'Synthetic excluded investment group.', previewDisabled: true });
    await capture(q, 'operations/mandato-preview-pending');
    assert.deepEqual(await q.js(() => ({ note: document.querySelector('#panel-mandato #m-note_per_il_comitato')?.value || null,
      exclusions: document.querySelector('#panel-mandato #m-esclusioni')?.value || null,
      previewDisabled: !!document.querySelector('#panel-mandato button[data-action="preview"]')?.disabled })),
    previewBusyDraft, 'Mandato preview remains in flight throughout all seven viewport captures');
    await q.toggle('classic');
    const draftWhilePreview = await q.js(() => ({ note: document.querySelector('#panel-mandato #m-note_per_il_comitato')?.value || null,
      exclusions: document.querySelector('#panel-mandato #m-esclusioni')?.value || null,
      previewDisabled: !!document.querySelector('#panel-mandato button[data-action="preview"]')?.disabled }));
    assert.deepEqual(draftWhilePreview, previewBusyDraft, 'Mandato controller retains draft and busy state during the pending preview');
    await q.toggle('modern');
    await q.waitFor(() => /Synthetic preview: seven mandate sections/.test(document.querySelector('#panel-mandato .mandato-preview')?.innerText || ''),
      'Mandato preview returned after mode switch', 22000);
    await capture(q, 'operations/mandato-preview');
    const allPreviews = await routeWrites(q, 'POST', '/mandato/anteprima');
    const previews = allPreviews.slice(previewWritesBeforeScenario);
    assert.equal(previews.length, 1, 'the seven-section scenario adds exactly one mandate preview request');
    assert.equal(previews.at(-1)?.input?.note?.note_per_il_comitato,
      updatedMandate.valori.note.note_per_il_comitato, 'the last scenario preview carries the edited committee note');

    await q.click('#panel-mandato button[data-action="save"]');
    await q.waitFor(() => !!document.querySelector('#panel-mandato .mandato-form[aria-busy="true"]'), 'Mandato save busy state');
    const savesStarted = (await routeWrites(q, 'PUT', '/mandato')).slice(saveWritesBeforeScenario);
    assert.equal(savesStarted.length, 1, 'valid mandate save starts once');
    const savingDraft = await q.js(() => ({ note: document.querySelector('#panel-mandato #m-note_per_il_comitato')?.value || null,
      disabled: !!document.querySelector('#panel-mandato .mandato-form')?.disabled,
      busy: document.querySelector('#panel-mandato .mandato-form')?.getAttribute('aria-busy') }));
    assert.deepEqual(savingDraft, { note: updatedMandate.valori.note.note_per_il_comitato, disabled: true, busy: 'true' });
    await q.toggle('classic');
    const savingDraftInClassic = await q.js(() => ({ note: document.querySelector('#panel-mandato #m-note_per_il_comitato')?.value || null,
      disabled: !!document.querySelector('#panel-mandato .mandato-form')?.disabled,
      busy: document.querySelector('#panel-mandato .mandato-form')?.getAttribute('aria-busy') }));
    assert.deepEqual(savingDraftInClassic, savingDraft, 'Mandato save remains busy in Classic while the fixture write is pending');
    await q.toggle('modern');
    await capture(q, 'operations/mandato-save-pending');
    const savePendingControlVisual = await captureCompactPending(q, 'operations/mandato-save-pending-control', {
      buttonSelector: '#panel-mandato button[data-action="save"]', contextSelector: '#panel-mandato .mandato-form',
      contextBusyAttr: 'true', expectedText: 'SAVING|SALVATAGGIO' });
    const switchedSavingDraft = await q.js(() => ({ note: document.querySelector('#panel-mandato #m-note_per_il_comitato')?.value || null,
      disabled: !!document.querySelector('#panel-mandato .mandato-form')?.disabled,
      busy: document.querySelector('#panel-mandato .mandato-form')?.getAttribute('aria-busy') }));
    const saveBusy = JSON.stringify(switchedSavingDraft) === JSON.stringify(savingDraft);
    assert.deepEqual(switchedSavingDraft, savingDraft, 'Mandato draft and saving lock survive the seven viewport captures and modern switch');
    await q.toggle('classic');
    await q.waitFor(() => /saved|salvato/i.test(document.querySelector('#panel-mandato .preview-message')?.innerText || ''),
      'Mandato save completion after mode switch', 22000);
    const saves = (await routeWrites(q, 'PUT', '/mandato')).slice(saveWritesBeforeScenario);
    assert.equal(saves.length, 1, 'mode switch does not repeat mandate save');
    await capture(q, 'operations/mandato-saved');
    return { assertionResults: { allSevenSectionsReachable: true, invalidNumberDoesNotPreview: true,
      draftSurvivesDelayedPreview: draftWhilePreview.note === updatedMandate.valori.note.note_per_il_comitato,
      previewCarriesEditedValues: true, saveSentOnce: saves.length === 1,
      inFlightSaveBusyStateObserved: saveBusy,
      compactPendingSaveControlVisibleAndDisabled: savePendingControlVisual.before.fullyVisible && savePendingControlVisual.before.disabled
        && savePendingControlVisual.before.contextBusy === 'true',
      compactTailQuestionAndPreviewActuallyHitTested: compactReachability.surface.every(item => item.hitTest && item.inViewport) },
      compactReachability, savePendingControlVisual,
      sections, invalidValue: 'not-a-number', previewInput: previews[0].input, saveInput: saves[0].input,
      fixtureVsReal: { allReadsAndWritesSynthetic: true, backendStarted: false, llmStarted: false } };
  });

  await executeFixtureScenario(q, 'mandato', 'stale-preview-fingerprint-is-disclosed-without-retry', async () => {
    // Recovery cases and earlier assertions may leave the renderer on the
    // dashboard. Start this scenario from a fresh Mandato mount and explicit
    // synthetic read so it does not depend on the prior scenario's outcome.
    await q.visit('/dashboard');
    await q.fixture({ setRead: { '/mandato': clone(fixtureData.mandato) } });
    await q.visit('/mandato');
    await q.waitFor(() => !!document.querySelector('#panel-mandato .mandato-page')
      && !!document.querySelector('#panel-mandato #m-note_per_il_comitato'), 'Mandato form data ready for stale-preview scenario');
    await q.click('#panel-mandato .mandato-index a[href="#mandato-note"]');
    await q.setValue('#panel-mandato #m-note_per_il_comitato', 'Synthetic stale preview must not be called current.');
    const updatedState = clone(fixtureData.mandato);
    updatedState.impronta = 'synthetic-fixture-mandate-v2';
    await q.fixture({ setRead: { '/mandato': updatedState }, setWrite: {
      '/mandato/anteprima': { testo: 'Synthetic stale preview.', impronta: 'synthetic-stale-v3',
        origine: 'synthetic_fixture', output_language: 'en' },
      '/mandato': { ok: true, synthetic: true },
    } });
    await q.click('#panel-mandato button[data-action="preview"]');
    await q.waitFor(() => /Synthetic stale preview/.test(document.querySelector('#panel-mandato .mandato-preview')?.innerText || ''),
      'stale synthetic preview ready');
    const putsBefore = (await routeWrites(q, 'PUT', '/mandato')).length;
    await q.click('#panel-mandato button[data-action="save"]');
    await q.waitFor(() => !!document.querySelector('#panel-mandato .preview-message')
      && document.querySelector('#panel-mandato .preview-message').innerText.length > 0, 'version mismatch message');
    const staleMessage = await text(q, '#panel-mandato .preview-message');
    const putsAfter = (await routeWrites(q, 'PUT', '/mandato')).length;
    assert.match(staleMessage, /mismatch|changed|different|cambi|impronta|fingerprint/i, 'mismatched save fingerprint is explained');
    assert.equal(putsAfter, putsBefore + 1, 'there is one attempted fixture save and no automatic retry');
    await capture(q, 'operations/mandato-stale-version', [[1920, 1080]]);
    return { assertionResults: { staleFingerprintDisplayed: true, noAutomaticRetry: putsAfter === putsBefore + 1 },
      staleMessage, putsBefore, putsAfter, fixtureVsReal: { staleResponseIsSynthetic: true, mutationApplied: false } };
  });
}

async function mandateReadStates(q) {
  await executeFixtureScenario(q, 'mandato', 'read-loading-error-and-explicit-retry', async () => {
    await q.toggle('classic');
    await q.visit('/dashboard');
    await q.waitFor(() => !!document.querySelector('main .bbn-dashboard'), 'Dashboard mounted before Mandato fixtures');
    await q.fixture({ clearAll: true, setRead: { '/mandato': { body: clone(fixtureData.mandato), delayMs: 8000 } } });
    await reloadCurrentApp(q, 'fresh app renderer for first Mandato read', '#/dashboard');
    await q.visit('/mandato');
    await q.waitFor(() => !!document.querySelector('#tab-mandato'), 'Mandato route mounted for delayed read');
    await q.waitFor(() => !!document.querySelector('#panel-mandato .mandato-loading[role="status"][aria-busy="true"]'),
      'Mandato first-read loading');
    await q.toggle('modern');
    const loading = await text(q, '#panel-mandato .mandato-loading');
    assert.ok(loading, 'Mandato presents a loading state rather than an empty or broken form');
    await capture(q, 'operations/mandato-read-loading-modern', [[1920, 1080]]);
    assert.ok(await q.js(() => !!document.querySelector('#panel-mandato .mandato-loading[aria-busy="true"]')),
      'Mandato remains in its first-read state after the mode switch and capture');
    await q.waitFor(() => !!document.querySelector('#panel-mandato .mandato-page'), 'Mandato synthetic data loaded', 25000);
    const fieldCount = await q.js(() => document.querySelectorAll('#panel-mandato .mandato-page input,#panel-mandato .mandato-page select,#panel-mandato .mandato-page textarea').length);
    assert.ok(fieldCount > 0, 'successful fixture read mounts the full mandate form');

    await q.fixture({ setRead: { '/mandato': { status: 503, body: { detail: 'Synthetic mandate read unavailable.' } } } });
    await q.visit('/dashboard');
    await q.waitFor(() => !!document.querySelector('main .bbn-dashboard'), 'Dashboard mounted before Mandato error fixture');
    await q.visit('/mandato');
    await q.waitFor(() => !!document.querySelector('#panel-mandato .mandato-fault[role="alert"]'), 'Mandato read failure declared');
    const fault = await text(q, '#panel-mandato .mandato-fault');
    assert.match(fault, /Synthetic mandate read unavailable/);
    assert.ok(await q.js(() => !!document.querySelector('#panel-mandato .mandato-fault button')),
      'Mandato read failure offers an explicit retry action');
    await capture(q, 'operations/mandato-read-error-modern', [[1920, 1080]]);
    await q.fixture({ clearReads: ['/mandato'] });
    await q.click('#panel-mandato .mandato-fault button');
    await q.waitFor(() => !!document.querySelector('#panel-mandato .mandato-page'), 'Mandato retry recovers using the fixture');
    const recoveredFieldCount = await q.js(() => document.querySelectorAll('#panel-mandato .mandato-page input,#panel-mandato .mandato-page select,#panel-mandato .mandato-page textarea').length);
    assert.equal(recoveredFieldCount, fieldCount, 'retry restores the same schema-backed form without losing sections');
    await q.fixture({ clearAll: true });
    await q.toggle('classic');
    return { assertionResults: { loadingExplicit: !!loading, failureExplicitAndRetryable: /Synthetic mandate read unavailable/.test(fault),
      retryRestoresSchema: recoveredFieldCount === fieldCount }, loading, fieldCount, fault, recoveredFieldCount,
      fixtureVsReal: { allReadsSynthetic: true, writes: 0, backendStarted: false, llmStarted: false } };
  });
}

async function openJournal(q) {
  await q.toggle('classic');
  await q.visit('/mandato');
  await q.waitFor(() => !!document.querySelector('#tab-diario'), 'Mandato tabs mounted before opening Journal');
  await q.click('#tab-diario');
  await q.waitFor(() => document.querySelector('#tab-diario')?.getAttribute('aria-selected') === 'true'
    && !!document.querySelector('#panel-diario .journal-page'), 'Journal inside Mandato controller');
  await q.waitFor(() => !!document.querySelector('#panel-diario .journal-editor'), 'Journal editor');
  // The core retention case intentionally leaves a synthetic draft in session memory.
  const canDiscard = await q.js(() => [...document.querySelectorAll('#panel-diario .journal-editor-actions button')]
    .some(button => !button.classList.contains('journal-primary') && !button.disabled));
  if (canDiscard) await clickText(q, '#panel-diario .journal-editor-actions', 'discard|scarta');
}

async function journalActions(q) {
  await executeFixtureScenario(q, 'journal', 'create-update-history-archive-restore-and-version-conflict', async () => {
    await openJournal(q);
    const modernCaptures = [];
    await q.waitFor(() => !!document.querySelector('#panel-diario .journal-notes'), 'Journal library');
    await clickText(q, '#panel-diario .journal-library-head', 'new note|nuova nota');
    await q.waitFor(() => !!document.querySelector('#panel-diario .journal-title-input'), 'new journal editor');
    await q.setValue('#panel-diario .journal-title-input', 'Synthetic QA journal entry');
    const createdBody = 'Fixture-only entry created in the isolated desktop harness.';
    await q.setValue('#panel-diario .journal-body-label textarea', createdBody);
    const created = { id: 919, origin: 'user', kind: 'thesis', ticker: null, title: 'Synthetic QA journal entry',
      body: createdBody, created_at: STAMP, updated_at: STAMP, version: 1, archived_at: null };
    // Wrap the journal entry in the response descriptor: an entry itself has
    // a `body` field, which readConfig reserves for raw response bodies.
    await q.fixture({ setWrite: { '/journal': { body: created } } });
    await clickText(q, '#panel-diario .journal-editor-actions', 'save note|salva nota');
    await q.waitFor(() => /(?:Note|Nota)\s+919.*Versione?\s+1/i.test(document.querySelector('#panel-diario .journal-editor-top')?.innerText || ''),
      'created journal entry');
    const createRequest = (await routeWrites(q, 'POST', '/journal'))[0];
    assert.deepEqual(createRequest.input, { kind: 'thesis', ticker: null, title: created.title, body: created.body },
      'create sends only the user-authored journal draft');
    await capture(q, 'operations/journal-created-editor');
    modernCaptures.push(await captureJournalModern(q, 'journal-created-editor', state => {
      assert.equal(state.controls[2].elements[0]?.value, created.title, 'new note title remains in the Modern editor');
      assert.equal(state.controls[3].elements[0]?.value, created.body, 'new note body remains in the Modern editor');
      assert.ok(state.controls[0].elements[0]?.selected === 'true', 'Journal tab remains selected for the Modern create capture');
    }, { scrollSelector: '#panel-diario .journal-title-input',
      compactActionSelector: '#panel-diario .journal-editor-actions > button:last-child' }));

    await clickText(q, '#panel-diario .journal-notes', 'Synthetic thesis: durable margins');
    await q.waitFor(() => document.querySelector('#panel-diario .journal-editor-top')?.innerText.includes('910'),
      'selected Journal entry 910');
    const update = journalEntry({ title: 'Synthetic thesis: durable margins - QA revision',
      body: '## Fixture-only thesis\n\nUpdated synthetic evidence.\n\nNo real-company information.', version: 4 });
    await q.setValue('#panel-diario .journal-title-input', update.title);
    await q.setValue('#panel-diario .journal-body-label textarea', update.body);
    await q.fixture({ setWrite: { '/journal/910': { body: update, delayMs: 20000 } } });
    await clickText(q, '#panel-diario .journal-editor-actions', 'save new version|salva nuova versione');
    await q.waitFor(() => document.querySelector('#panel-diario .journal-editor-actions .journal-primary')?.disabled,
      'Journal update busy state');
    const updatesStarted = await routeWrites(q, 'PUT', '/journal/910');
    assert.equal(updatesStarted.length, 1, 'Journal update starts exactly once');
    assert.equal(updatesStarted[0].input.expected_version, 3, 'optimistic write carries the loaded revision');
    const pendingEditor = await q.js(() => ({ title: document.querySelector('#panel-diario .journal-title-input')?.value || null,
      disabled: !!document.querySelector('#panel-diario .journal-editor fieldset')?.disabled,
      saveDisabled: !!document.querySelector('#panel-diario .journal-editor-actions .journal-primary')?.disabled }));
    assert.deepEqual(pendingEditor, { title: update.title, disabled: true, saveDisabled: true });
    await q.toggle('modern');
    const switchedEditor = await q.js(() => ({ title: document.querySelector('#panel-diario .journal-title-input')?.value || null,
      disabled: !!document.querySelector('#panel-diario .journal-editor fieldset')?.disabled,
      saveDisabled: !!document.querySelector('#panel-diario .journal-editor-actions .journal-primary')?.disabled }));
    const titleWhilePending = switchedEditor.title;
    assert.deepEqual(switchedEditor, pendingEditor, 'Journal editor draft and busy state survive the modern presentation switch');
    await capture(q, 'operations/journal-update-pending');
    const pendingControlVisual = await captureCompactPending(q, 'operations/journal-update-pending-control', {
      buttonSelector: '#panel-diario .journal-editor-actions .journal-primary', expectedText: 'please wait|attendi' });
    assert.deepEqual(await q.js(() => ({ title: document.querySelector('#panel-diario .journal-title-input')?.value || null,
      disabled: !!document.querySelector('#panel-diario .journal-editor fieldset')?.disabled,
      saveDisabled: !!document.querySelector('#panel-diario .journal-editor-actions .journal-primary')?.disabled })),
    pendingEditor, 'Journal update remains pending throughout all seven viewport captures');
    await q.toggle('classic');
    await q.waitFor(() => /\b(?:v|version\s*)4\b/i.test(document.querySelector('#panel-diario .journal-editor-top')?.innerText || ''),
      'Journal saved revision after mode switch', 22000);
    assert.equal(titleWhilePending, update.title, 'Journal editor draft stays mounted during the delayed write');
    await capture(q, 'operations/journal-updated-history');
    const versionCount = await q.js(() => document.querySelectorAll('#panel-diario .journal-history li').length);
    assert.ok(versionCount >= 1, 'revision history is rendered for the selected entry');
    modernCaptures.push(await captureJournalModern(q, 'journal-updated-history', state => {
      assert.equal(state.controls[2].elements[0]?.value, update.title, 'updated title remains in the Modern editor');
      assert.match(state.controls[4].elements[0]?.text || '', /910.*(?:v|version\s*)4/i,
        'updated revision identity is visible in the Modern editor');
      assert.ok(state.rowCount >= 1, 'updated Journal entry remains in the Modern library');
    }));

    const archived = journalEntry({ ...update, version: 5, archived_at: STAMP });
    await q.fixture({ setWrite: { '/journal/910/archive': { body: archived } } });
    await clickText(q, '#panel-diario .journal-editor-actions', 'archive|archivia');
    await q.waitFor(() => /archiv/i.test(document.querySelector('#panel-diario .journal-warning')?.innerText || ''),
      'archived Journal state');
    const archiveRequest = (await routeWrites(q, 'POST', '/journal/910/archive'))[0];
    assert.deepEqual(archiveRequest.input, { expected_version: 4, archived: true });
    await capture(q, 'operations/journal-archived');
    modernCaptures.push(await captureJournalModern(q, 'journal-archived', state => {
      assert.match(state.controls[5].elements[0]?.text || '', /archiv/i, 'archive state is visible in the Modern editor');
    }));

    const restored = journalEntry({ ...update, version: 6, archived_at: null });
    await q.fixture({ setWrite: { '/journal/910/archive': { body: restored } } });
    await clickText(q, '#panel-diario .journal-editor-actions', 'restore|ripristina');
    await q.waitFor(() => !/archived/i.test(document.querySelector('#panel-diario .journal-editor-top')?.innerText || ''),
      'Journal restored state');
    const archiveWrites = await routeWrites(q, 'POST', '/journal/910/archive');
    assert.equal(archiveWrites.length, 2, 'archive and restore each issue one fixture request');
    assert.deepEqual(archiveWrites[1].input, { expected_version: 5, archived: false });
    await capture(q, 'operations/journal-restored');
    modernCaptures.push(await captureJournalModern(q, 'journal-restored', state => {
      assert.match(state.controls[4].elements[0]?.text || '', /910.*(?:v|version\s*)6/i,
        'restored revision is visible in the Modern editor');
      assert.ok(!/archiv/i.test(state.controls[4].elements[0]?.text || ''), 'restored editor is no longer archived');
    }));

    const remote = journalEntry({ title: 'Synthetic remote version from fixture', version: 7 });
    await q.fixture({ setRead: { '/journal/910': { body: remote } }, setWrite: { '/journal/910': { status: 409,
      body: { detail: { code: 'version_conflict', current_version: 7, message: 'Synthetic version conflict.' } } } } });
    await q.setValue('#panel-diario .journal-title-input', 'Synthetic local draft after remote update');
    await clickText(q, '#panel-diario .journal-editor-actions', 'save new version|salva nuova versione');
    await q.waitFor(() => !!document.querySelector('#panel-diario .journal-warning button'), 'Journal optimistic conflict');
    const conflictWrite = (await routeWrites(q, 'PUT', '/journal/910')).at(-1);
    assert.equal(conflictWrite.input.expected_version, 6, 'conflict request uses the version loaded by the editor');
    await capture(q, 'operations/journal-conflict');
    modernCaptures.push(await captureJournalModern(q, 'journal-conflict', state => {
      assert.match(state.controls[5].elements[0]?.text || '', /compare|confronta/i,
        'version conflict and compare action remain visible in Modern');
      assert.match(state.controls[2].elements[0]?.value || '', /Synthetic local draft/i,
        'local conflicting draft remains intact in Modern');
    }));
    await clickText(q, '#panel-diario .journal-warning', 'compare|confronta');
    await q.waitFor(() => document.querySelector('#panel-diario .journal-conflict')?.innerText.includes('Synthetic remote version from fixture'),
      'remote Journal revision is readable');
    const conflictText = await text(q, '#panel-diario .journal-conflict');
    assert.match(conflictText, /Synthetic remote version from fixture/);
    modernCaptures.push(await captureJournalModern(q, 'journal-conflict-remote-compare', state => {
      assert.match(state.evidenceText || '', /Synthetic remote version from fixture/,
        'remote version comparison is visible in the seven-viewport Modern capture');
    }, { scrollSelector: '#panel-diario .journal-conflict h3',
      compactActionSelector: '#panel-diario .journal-conflict button:last-child' }));
    return { assertionResults: { createPayloadExact: true, updateUsesExpectedVersion: true,
      historyRendersRevision: versionCount >= 1, archiveAndRestoreUseMonotonicVersion: archiveWrites.length === 2,
      staleWriteShowsConflict: true, remoteCompareReadable: true,
      pendingEditorSurvivesModeSwitch: titleWhilePending === update.title,
      pendingSaveControlVisibleAndDisabledAtCompactViewport: pendingControlVisual.before.fullyVisible && pendingControlVisual.before.disabled,
      createUpdateArchiveRestoreAndConflictHaveModernSevenViewportEvidence: modernCaptures.length === 6 },
      create: createRequest.input, update: updatesStarted[0].input, archive: archiveRequest.input,
      archiveRestore: archiveWrites.map(item => item.input), conflict: conflictWrite.input, conflictText, pendingControlVisual, modernCaptures,
      fixtureVsReal: { allJournalWritesSynthetic: true, externalLLM: false, backendStarted: false } };
  });
}

async function journalPromoteThesis(q) {
  await executeFixtureScenario(q, 'journal', 'promote-thesis-note-to-position-thesis-through-the-backend-guard', async () => {
    // own list and note: the previous scenario leaves the library on its pagination fixture
    const note = journalEntry();
    const { body: noteBody, ...summary } = note;
    await q.toggle('classic');
    await q.visit('/dashboard');
    await q.fixture({ clearAll: true, setRead: {
      '/journal': { body: { items: [{ ...summary, excerpt: 'Synthetic evidence.' }], total: 1, limit: 30, offset: 0 } },
      '/journal/910': { body: note },
      '/journal/910/history': { body: { items: [], total: 0, limit: 30, offset: 0 } } } });
    await reloadCurrentApp(q, 'fresh app renderer before Journal thesis promotion', '#/dashboard');
    await openJournal(q);
    await q.waitFor(() => /durable margins/i.test(document.querySelector('#panel-diario .journal-notes')?.innerText || ''), 'Journal library rows');
    await clickText(q, '#panel-diario .journal-notes', 'Synthetic thesis: durable margins');
    await q.waitFor(() => !!document.querySelector('#panel-diario .jr-promote'), 'promotion band for a thesis note on a held ticker');
    assert.match(await text(q, '#panel-diario .jr-promote'), /SYN1/, 'the band names the position whose thesis the advisor reads');
    const body = await q.js(() => document.querySelector('#panel-diario .journal-body-label textarea')?.value || '');

    await q.fixture({ setWrite: { '/positions/SYN1/tesi': { status: 422,
      body: { detail: 'Synthetic thesis guard: the new text is shorter than half.', code: 'thesis_shortening_confirmation' } } } });
    await clickText(q, '#panel-diario .jr-promote', 'use as position thesis|usa come tesi della posizione');
    await q.waitFor(() => !!document.querySelector('#panel-diario .jr-promote .journal-warning'), 'promotion confirmation step');
    assert.equal((await routeWrites(q, 'PUT', '/positions/SYN1/tesi')).length, 0, 'opening the confirmation writes nothing');
    await clickText(q, '#panel-diario .jr-promote .journal-warning', '^(set as thesis|imposta come tesi|replace|sostituisci)$');
    await q.waitFor(() => /Synthetic thesis guard/.test(document.querySelector('#panel-diario .jr-promote .journal-warning')?.innerText || ''),
      'backend guard shown verbatim');
    const first = await routeWrites(q, 'PUT', '/positions/SYN1/tesi');
    assert.equal(first.length, 1, 'the first confirmation sends exactly one write');
    assert.deepEqual(first[0].input, { tesi: body, conferma: false, autore: 'diario' }, 'the note body is sent without overriding the guard');

    await q.fixture({ setWrite: { '/positions/SYN1/tesi': { body: { ok: true, ticker: 'SYN1', scritture: 1, versione_storico: 1 } } } });
    await clickText(q, '#panel-diario .jr-promote .journal-warning', 'replace anyway|sostituisci comunque');
    await q.waitFor(() => !!document.querySelector('#panel-diario .jr-promote.is-same'), 'note recognised as the position thesis');
    const writes = await routeWrites(q, 'PUT', '/positions/SYN1/tesi');
    assert.equal(writes.length, 2, 'the override sends one more write');
    assert.equal(writes[1].input.conferma, true, 'only the second, explicit yes overrides the guard');
    assert.match(await text(q, '#panel-diario .journal-message'), /SYN1/, 'the outcome names the updated position');
    await capture(q, 'operations/journal-thesis-promoted');
  });
}

async function journalListStates(q) {
  await executeFixtureScenario(q, 'journal', 'journal-loading-empty-search-and-pagination', async () => {
    await q.toggle('classic');
    await q.visit('/dashboard');
    await q.fixture({ clearAll: true, setRead: { '/journal': {
      body: { items: [], total: 0, limit: 30, offset: 0 }, delayMs: 20000,
    } } });
    await reloadCurrentApp(q, 'fresh app renderer before Journal first list read', '#/dashboard');
    await openJournal(q);
    await q.waitFor(() => !!document.querySelector('#panel-diario .journal-library [role="status"]'), 'Journal list loading');
    await q.toggle('modern');
    const loading = await text(q, '#panel-diario .journal-library [role="status"]');
    assert.ok(loading, 'Journal announces its pending list read');
    const modernCaptures = [];
    modernCaptures.push(await captureJournalModern(q, 'journal-list-loading-modern', state => {
      assert.match(state.evidenceText || '', /loading|caric|reading|lettura/i, 'Modern Journal keeps its pending first-read message');
      assert.equal(state.rowCount, 0, 'pending first-read state does not imply populated rows');
    }));
    assert.ok(await q.js(() => !!document.querySelector('#panel-diario .journal-library [role="status"]')),
      'Journal retains its first-read status after the mode switch and capture');
    await q.waitFor(() => !!document.querySelector('#panel-diario .journal-library .journal-empty'), 'Journal empty library', 25000);
    const empty = await text(q, '#panel-diario .journal-library .journal-empty');
    assert.match(empty, /no notes|start your first note|nessun|nota/i, 'an empty Journal library has its own explanation');
    modernCaptures.push(await captureJournalModern(q, 'journal-list-empty-modern', state => {
      assert.match(state.evidenceText || '', /no notes|start your first note|nessun|nota/i, 'Modern Journal explains a genuinely empty library');
      assert.equal(state.rowCount, 0, 'empty library has no rendered note rows');
    }));

    await q.fixture({ clearReads: ['/journal'] });
    await q.setValue('#panel-diario .journal-search input', 'durable');
    await q.waitFor(() => document.querySelectorAll('#panel-diario .journal-library .journal-note').length === 1
      && /durable margins/i.test(document.querySelector('#panel-diario .journal-library .journal-notes')?.innerText || ''),
    'Journal search returns the matching synthetic note', 6000);
    const searchReads = (await q.snapshot()).requests.filter(item => item.method === 'GET' && item.route === '/journal');
    assert.ok(searchReads.some(item => item.query.query === 'durable'), 'search sends the settled query to the list endpoint');
    const foundTitle = await text(q, '#panel-diario .journal-library .journal-notes');
    modernCaptures.push(await captureJournalModern(q, 'journal-search-result-modern', state => {
      assert.equal(state.controls[1].elements[0]?.value, 'durable', 'Modern Journal retains the search query');
      assert.equal(state.rowCount, 1, 'Modern Journal renders the matching fixture row');
      assert.match(state.evidenceText || '', /durable margins/i, 'Modern search result is readable');
    }));

    await q.setValue('#panel-diario .journal-search input', 'synthetic-no-match-qa');
    await q.waitFor(() => !!document.querySelector('#panel-diario .journal-library .journal-empty'), 'Journal filtered empty state', 6000);
    const filteredEmpty = await text(q, '#panel-diario .journal-library .journal-empty');
    const filteredReads = (await q.snapshot()).requests.filter(item => item.method === 'GET' && item.route === '/journal');
    assert.ok(filteredReads.some(item => item.query.query === 'synthetic-no-match-qa'), 'filtered empty state follows the actual search request');
    assert.match(filteredEmpty, /match|find|corrispond|nessun|not found/i, 'filtered empty results are explained separately from an unfiltered empty library');
    modernCaptures.push(await captureJournalModern(q, 'journal-filtered-empty-modern', state => {
      assert.equal(state.controls[1].elements[0]?.value, 'synthetic-no-match-qa', 'Modern filtered-empty state retains the search query');
      assert.equal(state.rowCount, 0, 'filtered empty search contains no rows');
      assert.match(state.evidenceText || '', /match|find|corrispond|nessun|not found/i, 'Modern filtered-empty state explains the query result');
    }));

    const page = Array.from({ length: 30 }, (_, i) => ({ id: 2200 + i, origin: 'user', kind: 'thesis', ticker: 'SYNQA',
      title: `Synthetic pagination page row ${i + 1}`, body: 'Fixture-only pagination evidence.',
      excerpt: 'Synthetic fixture row.', created_at: STAMP, updated_at: STAMP, version: 1, archived_at: null }));
    await q.fixture({ setRead: { '/journal': { items: page, total: 60, limit: 30, offset: 0 } } });
    await q.setValue('#panel-diario .journal-search input', '');
    await q.waitFor(() => document.querySelectorAll('#panel-diario .journal-library .journal-note').length === 30,
      'Journal synthetic pagination fixture loaded', 6000);
    await q.click('#panel-diario .journal-pagination button:last-child');
    await q.waitFor(() => /31.*60|31\D+60/.test(document.querySelector('#panel-diario .journal-pagination')?.innerText || ''),
      'Journal page two range', 6000);
    const pageTwo = await text(q, '#panel-diario .journal-pagination');
    const pagingReads = (await q.snapshot()).requests.filter(item => item.method === 'GET' && item.route === '/journal');
    assert.ok(pagingReads.some(item => item.query.offset === '30' && item.query.query === ''),
      'next-page control requests the next synthetic offset');
    assert.match(pageTwo, /31.*60|31\D+60/, 'the range label advances to the second page');
    modernCaptures.push(await captureJournalModern(q, 'journal-page-two-modern', state => {
      assert.equal(state.controls[1].elements[0]?.value, '', 'Modern pagination keeps the cleared search query');
      assert.equal(state.rowCount, 30, 'Modern second page renders the requested fixture page');
      assert.match(state.controls[7].elements[0]?.text || '', /31.*60|31\D+60/,
        'Modern pagination displays the second page range');
    }));
    await q.fixture({ clearAll: true });
    await q.toggle('classic');
    return { assertionResults: { loadingExplicit: !!loading, emptyExplicit: /no notes|start your first note|nessun|nota/i.test(empty),
      searchUsesQueryAndFindsFixture: searchReads.some(item => item.query.query === 'durable') && /durable margins/i.test(foundTitle),
      filteredEmptyDistinct: /match|find|corrispond|nessun|not found/i.test(filteredEmpty),
      pagingRequestsOffsetAndUpdatesRange: pagingReads.some(item => item.query.offset === '30' && item.query.query === '') && /31.*60|31\D+60/.test(pageTwo),
      loadingEmptySearchAndPaginationHaveModernSevenViewportEvidence: modernCaptures.length === 5 },
      loading, empty, foundTitle, filteredEmpty, pageTwo,
      modernCaptures,
      pagingNote: 'The fixture reuses a deterministic 30-row synthetic page body for all offsets; this case verifies query offset and UI range, not uniqueness of page-two records.',
      fixtureVsReal: { allReadsSynthetic: true, writes: 0, backendStarted: false, llmStarted: false } };
  });
}

async function settingsActions(q) {
  await executeFixtureScenario(q, 'settings', 'preferences-tasks-backup-create-delete-and-mode-retention', async () => {
    const expectedTaskNames = taskDisplayNames();
    await q.toggle('classic');
    await q.visit('/dashboard');
    await q.fixture({ setRead: { '/preferences': { language: 'en', selected: true, source: 'preferences' } } });
    await clickText(q, 'nav', 'CONFIG');
    await q.waitFor(() => !!document.querySelector('.f11v[role="dialog"][aria-modal="true"]'), 'Settings overlay');
    await q.waitFor(() => !!document.querySelector('.f11v input[name="language"]:not(:disabled)'), 'Settings language data');
    // One section at a time (Nuova, 05/10): job rows live under «Automatic jobs».
    await q.click('.f11v [data-sezione="lavori"]');
    await q.waitFor(names => names.every(name => (document.querySelector('.f11v')?.innerText || '').includes(name)),
      'synthetic scheduled task rows', 10000, expectedTaskNames);
    const hasTasks = await q.js(names => {
      const value = document.querySelector('.f11v')?.innerText || '';
      return names.every(name => value.includes(name));
    }, expectedTaskNames);
    assert.ok(hasTasks, 'Settings shows the synthetic task states');
    await capture(q, 'operations/settings-dialog-classic');
    const dialogId = await q.js(() => {
      window.__bbOpsIds ||= new WeakMap(); window.__bbOpsNextId ||= 0;
      const el = document.querySelector('.f11v[role="dialog"]');
      if (!el) return null;
      if (!window.__bbOpsIds.has(el)) window.__bbOpsIds.set(el, ++window.__bbOpsNextId);
      return window.__bbOpsIds.get(el);
    });

    await q.fixture({ setRead: { '/preferences': { language: 'it', selected: true, source: 'preferences' } },
      setWrite: { '/preferences': { language: 'it', selected: true, source: 'preferences' } } });
    await q.click('.f11v [data-sezione="generale"]');
    await q.click('.f11v input[name="language"][value="it"]');
    await q.click('.f11v .btn-amber');
    await q.waitFor(() => document.documentElement.lang === 'it', 'Italian preference independently written and read back', 5000);
    const italianWrites = await routeWrites(q, 'PUT', '/preferences');
    assert.equal(italianWrites.length, 1);
    assert.deepEqual(italianWrites[0].input, { language: 'it' });
    await q.toggle('modern');
    const dialogIdAfterSwitch = await q.js(() => {
      const el = document.querySelector('.f11v[role="dialog"]');
      return el && window.__bbOpsIds?.get(el) || null;
    });
    assert.equal(dialogIdAfterSwitch, dialogId, 'settings stays mounted across the global presentation switch');
    await capture(q, 'operations/settings-dialog-modern');

    // Restore the runner-wide language before remaining action modules run.
    await q.fixture({ setRead: { '/preferences': { language: 'en', selected: true, source: 'preferences' } },
      setWrite: { '/preferences': { language: 'en', selected: true, source: 'preferences' } } });
    await q.click('.f11v input[name="language"][value="en"]');
    await q.click('.f11v .btn-amber');
    await q.waitFor(() => document.documentElement.lang === 'en', 'English preference restored by readback', 5000);

    const backupName = 'bellomberg_backup_2026-09-30_qa.zip';
    const initialBackups = clone(fixtureData.backups);
    const afterCreate = [{ filename: backupName, path: 'fixture/backups/' + backupName,
      size_mb: 0.1, created: STAMP }, ...initialBackups];
    await q.fixture({ setRead: { '/db/backups': { backups: afterCreate, count: afterCreate.length } },
      setWrite: { '/db/backup': { ok: true, backup_path: 'fixture/backups/' + backupName, size_mb: 0.1,
        files_count: 2, files: ['fixture/db.sqlite', 'fixture/preferences.json'],
        db_quick_check: { integrity: 'ok', journal: 'ok' }, timestamp: STAMP } } });
    await q.click('.f11v [data-sezione="backup"]');
    await q.click('.f11v button.btn.am');
    await q.waitFor(name => (document.querySelector('.f11v .esito')?.innerText || '').includes(name),
      'synthetic backup result', 5000, backupName);
    const createWrites = await routeWrites(q, 'POST', '/db/backup');
    assert.equal(createWrites.length, 1, 'backup action sends one explicit fixture request');
    await capture(q, 'operations/settings-backup-created');

    const deleteName = 'bellomberg_backup_2026-09-28.zip';
    const afterDelete = afterCreate.filter(item => item.filename !== deleteName);
    const deleteRoute = '/db/backups/' + deleteName;
    await q.fixture({ setRead: { '/db/backups': { backups: afterDelete, count: afterDelete.length } },
      setWrite: { [deleteRoute]: { ok: true, deleted: deleteName } } });
    const trigger = await q.js(name => {
      const row = [...document.querySelectorAll('.f11v .bl')].find(item => item.innerText.includes(name));
      const button = row?.querySelector('button[aria-label]');
      if (!button) return null;
      button.setAttribute('data-ops-delete-backup', 'true');
      return { label: button.getAttribute('aria-label'), row: row.innerText.slice(0, 150) };
    }, deleteName);
    assert.ok(trigger, 'synthetic backup row is visible for deletion');
    await q.click('.f11v button[data-ops-delete-backup="true"]');
    await q.waitFor(() => !!document.querySelector('.f11-ask[role="dialog"][aria-modal="true"]'),
      'permanent backup deletion confirmation');
    assert.equal((await routeWrites(q, 'DELETE', deleteRoute)).length, 0, 'opening delete confirmation has no side effect');
    await capture(q, 'operations/settings-delete-backup-confirmation');
    await clickText(q, '.f11-ask .bf', 'delete permanently|elimina definitivamente');
    await q.waitFor(name => (document.querySelector('.f11v .esito')?.innerText || '').includes(name),
      'backup deletion result', 5000, deleteName);
    const deleteWrites = await routeWrites(q, 'DELETE', deleteRoute);
    assert.equal(deleteWrites.length, 1, 'confirmed deletion calls the configured fixture route once');
    assert.equal(deleteWrites[0].input, null, 'backup deletion has no fabricated request body');
    await capture(q, 'operations/settings-backup-deleted');

    const preferences = await routeWrites(q, 'PUT', '/preferences');
    assert.equal(preferences.length, 2, 'Italian language and English restoration each write once');
    assert.deepEqual(preferences.map(item => item.input.language), ['it', 'en']);
    await q.key('Escape');
    await q.waitFor(() => !document.querySelector('.f11v[role="dialog"]'), 'Settings closes on Escape');
    return { assertionResults: { allSyntheticTasksRendered: hasTasks, languageWriteReadbackVerified: true,
      settingsModalRetainedOnModeSwitch: dialogIdAfterSwitch === dialogId, backupCreatedOnce: createWrites.length === 1,
      deletionWaitsForConfirmation: deleteWrites.length === 1, deleteHasNoBody: deleteWrites[0].input === null,
      defaultLanguageRestored: await q.js(() => document.documentElement.lang === 'en') },
      taskNames: expectedTaskNames,
      preferenceWrites: preferences.map(item => item.input), createdBackup: createWrites[0], deletedBackup: deleteWrites[0],
      fixtureVsReal: { everyWriteExplicitFixture: true, backendStarted: false, llmStarted: false } };
  });
  await closeSettingsLayers(q);
}

async function settingsReadStates(q) {
  await executeFixtureScenario(q, 'settings', 'modern-settings-per-resource-loading-and-read-errors', async () => {
    const delayed = value => ({ body: clone(value), delayMs: 8000 });
    await q.fixture({ clearAll: true, setRead: {
      '/tasks/scheduled': delayed({ tasks: clone(fixtureData.tasks) }),
      '/db/backups': delayed({ backups: clone(fixtureData.backups), count: fixtureData.backups.length }),
      '/health': delayed({ status: 'ok', version: 'synthetic-fixture' }),
      '/fx': delayed(clone(fixtureData.fx)),
      '/agents/list': delayed(clone(fixtureData.agents)),
    } });
    await reloadCurrentApp(q, 'fresh app renderer for first Settings read', '#/dashboard');
    await q.toggle('modern');
    await clickText(q, 'nav', 'CONFIG');
    await q.waitFor(() => !!document.querySelector('.f11v[role="dialog"][aria-modal="true"]'), 'Settings loading overlay');
    await q.pause(120);
    const loading = await q.js(() => ({ statusCount: document.querySelectorAll('.f11v [role="status"][aria-busy="true"]').length,
      text: document.querySelector('.f11v')?.innerText || '', dialog: !!document.querySelector('.f11v[role="dialog"]') }));
    assert.ok(loading.dialog && loading.statusCount >= 4,
      'modern Settings labels task, backup, FX, and engine reads while pending: ' + JSON.stringify(loading));
    assert.match(loading.text, /loading/i, 'modern Settings displays an accessible loading label');
    await capture(q, 'operations/settings-read-loading-modern', [[1920, 1080]]);
    const busyAfterCapture = await q.js(() => document.querySelectorAll('.f11v [role="status"][aria-busy="true"]').length);
    assert.ok(busyAfterCapture >= 4, 'Settings resources remain busy after their presentation switch and capture');
    const expectedTaskNames = taskDisplayNames();
    await q.waitFor(() => document.querySelectorAll('.f11v [aria-busy="true"]').length === 0, 'Settings synthetic reads settle', 25000);
    await q.click('.f11v [data-sezione="lavori"]');
    await q.waitFor((names) => document.querySelectorAll('.f11v [aria-busy="true"]').length === 0
      && names.every(name => (document.querySelector('.f11v')?.innerText || '').includes(name)),
    'Settings synthetic reads complete', 25000, expectedTaskNames);
    await q.key('Escape');
    await q.waitFor(() => !document.querySelector('.f11v[role="dialog"]'), 'Settings loading overlay closed');

    const fail = endpoint => ({ status: 503, body: { detail: `Synthetic Settings ${endpoint} read failed.` } });
    await q.fixture({ setRead: {
      '/tasks/scheduled': fail('tasks'), '/db/backups': fail('backups'), '/health': fail('health'),
      '/fx': fail('FX'), '/agents/list': fail('engines'),
    } });
    await clickText(q, 'nav', 'CONFIG');
    await q.waitFor(() => !!document.querySelector('.f11v .buco'), 'Settings missing-data report');
    await q.waitFor(() => document.querySelectorAll('.f11v [aria-busy="true"]').length === 0, 'Settings read errors settled');
    const errorState = await q.js(() => ({ text: document.querySelector('.f11v')?.innerText || '',
      missing: document.querySelector('.f11v .buco')?.innerText || '', loading: document.querySelectorAll('.f11v [aria-busy="true"]').length,
      diagnostics: [...document.querySelectorAll('.f11v .nota')].map(node => node.innerText) }));
    const expectedResourceLabels = ['scheduled jobs', 'backup list', 'backend status', 'exchange rates', 'engines'];
    for (const expected of expectedResourceLabels) assert.ok(errorState.missing.includes(expected),
      `Settings names the missing ${expected} resource`);
    assert.ok(/NOT readable/i.test(errorState.text), 'Settings declares the scheduled-task and backup read failures');
    assert.ok(/exchange rates NOT readable/i.test(errorState.text), 'Settings declares the FX read failure');
    const failingReads = (await q.snapshot()).requests.filter(item => item.method === 'GET'
      && ['/tasks/scheduled', '/db/backups', '/health', '/fx', '/agents/list'].includes(item.route));
    for (const endpoint of ['/tasks/scheduled', '/db/backups', '/health', '/fx', '/agents/list']) {
      assert.ok(failingReads.some(item => item.route === endpoint), `fixture exercised failed read ${endpoint}`);
    }
    assert.equal(errorState.loading, 0, 'failed Settings reads leave loading state and expose read errors');
    assert.match(errorState.text, /NOT readable|absent/i, 'Settings renders explanatory read-failure content');
    await capture(q, 'operations/settings-read-errors-modern', [[1920, 1080]]);
    await q.fixture({ clearAll: true });
    await q.key('Escape');
    await q.waitFor(() => !document.querySelector('.f11v[role="dialog"]'), 'Settings error overlay closed');
    await q.toggle('classic');
    return { assertionResults: { loadingVisiblePerResource: loading.statusCount >= 4,
      allFiveReadFailuresAreNamed: expectedResourceLabels.every(key => errorState.missing.includes(key)),
      errorsReplaceLoaders: errorState.loading === 0 }, loading, missing: errorState.missing, diagnostics: errorState.diagnostics,
      fixtureVsReal: { allReadsSynthetic: true, writes: 0, backendStarted: false, llmStarted: false } };
  });
  await closeSettingsLayers(q);
}

module.exports = { run };
