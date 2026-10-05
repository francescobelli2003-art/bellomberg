// Committee, agent-run, progress, memo and decision interactions against the
// isolated synthetic fixture only. No live agent or LLM is started.
const assert = require('node:assert/strict');

const stamp = '2026-09-29T12:00:00Z';

async function markButton(q, { root = 'main', pattern, includes, nth = 0 }) {
  const attr = 'data-qa-committee-button';
  const found = await q.js((rootSel, regexText, needle, index, marker) => {
    document.querySelectorAll(`[${marker}]`).forEach(el => el.removeAttribute(marker));
    let regex = null; try { regex = regexText ? new RegExp(regexText, 'i') : null; } catch {}
    const candidates = [...document.querySelectorAll(`${rootSel} button`)].filter(el => {
      const rect = el.getBoundingClientRect(), style = getComputedStyle(el);
      const text = (el.innerText || el.getAttribute('aria-label') || '').trim();
      return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden'
        && !el.disabled && (!regex || regex.test(text)) && (!needle || text.toLowerCase().includes(needle.toLowerCase()));
    });
    const button = candidates[index];
    if (!button) return { count: candidates.length, texts: candidates.map(el => (el.innerText || '').trim()).slice(0, 30) };
    button.setAttribute(marker, 'target'); return { selector: `[${marker}="target"]`, text: (button.innerText || '').trim() };
  }, root, pattern || '', includes || '', nth, attr);
  assert.ok(found.selector, `Could not find committee button: ${JSON.stringify(found)}`);
  return found.selector;
}

async function markInput(q, selector, nth = 0) {
  const attr = 'data-qa-committee-input';
  const found = await q.js((sel, index, marker) => {
    document.querySelectorAll(`[${marker}]`).forEach(el => el.removeAttribute(marker));
    const candidates = [...document.querySelectorAll(sel)].filter(el => {
      const rect = el.getBoundingClientRect(), style = getComputedStyle(el);
      return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
    });
    const input = candidates[index];
    if (!input) return { count: candidates.length, fields: candidates.map(el => ({ placeholder: el.placeholder, aria: el.getAttribute('aria-label') })) };
    input.setAttribute(marker, 'target'); return { selector: `[${marker}="target"]`, placeholder: input.placeholder };
  }, selector, nth, attr);
  assert.ok(found.selector, `Could not find committee input ${selector}: ${JSON.stringify(found)}`);
  return found.selector;
}

async function waitForCount(q, key, target, { exact = false, timeout = 7000 } = {}) {
  const until = Date.now() + timeout;
  while (Date.now() < until) {
    const count = (await q.counts())[key] || 0;
    if (exact ? count === target : count >= target) return count;
    await q.pause(40);
  }
  throw new Error(`Timed out waiting for ${key} count ${exact ? '===' : '>='} ${target}.`);
}

async function verifyAgentsScrollReachability(q) {
  const evidence = [];
  for (const [width, height] of [[1920,1080],[2560,1440],[3440,1440],[5120,1440],[1440,1000],[1280,900],[900,700]]) {
    await q.withViewport(width, height, async () => {
      // 02/10/2026: desk cards replace the orbital plates; every card head, the Capo, the run box
      // and the visible details panel must be reachable without header/footer clipping.
      const targets = await q.js(() => {
        const root = document.querySelector('main [data-page="agents"]');
        root.querySelectorAll('[data-qa-reach-card],[data-qa-last-plate],[data-qa-reach-panel]').forEach(el =>
          ['data-qa-reach-card', 'data-qa-last-plate', 'data-qa-reach-panel'].forEach(name => el.removeAttribute(name)));
        const visible = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.display !== 'none'; };
        const heads = [...root.querySelectorAll('.ag-desk .ag-desk-h')].filter(visible);
        heads.forEach((el, i) => el.setAttribute('data-qa-reach-card', String(i)));
        const capo = root.querySelector('.ag-capo .ag-capo-top'); capo.setAttribute('data-qa-last-plate', 'true');
        const panel = [...root.querySelectorAll('.ag-panel > .bbn-card-head, .ag-calls-wide > .bbn-card-head')].find(visible);
        panel.setAttribute('data-qa-reach-panel', 'true');
        return heads.map((_, i) => `[data-qa-reach-card="${i}"]`).concat('[data-qa-last-plate="true"]', '[data-qa-reach-panel="true"]',
          'main [data-page="agents"] .ag-run h1');
      });
      const checks = [];
      for (const selector of targets) {
        await q.scrollTo(selector);
        const check = await q.js(sel => {
          const el = document.querySelector(sel), rect = el.getBoundingClientRect();
          const footer = document.querySelector('footer')?.getBoundingClientRect();
          const clip = { left:0, top:0, right:innerWidth, bottom:footer ? Math.min(innerHeight,footer.top) : innerHeight };
          for (let node = el.parentElement; node && node !== document.documentElement; node = node.parentElement) {
            const style = getComputedStyle(node), box = node.getBoundingClientRect();
            // client* are in the node's own CSS px; under CSS zoom (the Agents page at ≥ 2200 px) the
            // box is in zoomed px, so the client sizes are scaled by the node's own zoom factor
            const kx = node.offsetWidth ? box.width / node.offsetWidth : 1, ky = node.offsetHeight ? box.height / node.offsetHeight : 1;
            if (/^(auto|scroll|hidden|clip)$/.test(style.overflowY)) { clip.top=Math.max(clip.top,box.top+node.clientTop*ky); clip.bottom=Math.min(clip.bottom,box.top+(node.clientTop+node.clientHeight)*ky); }
            if (/^(auto|scroll|hidden|clip)$/.test(style.overflowX)) { clip.left=Math.max(clip.left,box.left+node.clientLeft*kx); clip.right=Math.min(clip.right,box.left+(node.clientLeft+node.clientWidth)*kx); }
          }
          const visible = rect.width>0 && rect.height>0 && rect.left>=clip.left-1 && rect.top>=clip.top-1 && rect.right<=clip.right+1 && rect.bottom<=clip.bottom+1;
          return { selector:sel, visible, clip, rect:{left:rect.left,top:rect.top,right:rect.right,bottom:rect.bottom}, text:(el.textContent||'').trim().slice(0,100) };
        }, selector);
        assert.ok(check.visible, `Agent target cannot be revealed without header/footer clipping at ${width}x${height}: ${JSON.stringify(check)}`);
        checks.push(check);
      }
      await q.capture('agents-multi-desk-modern-top', { viewports:[[width,height]], scrollSelector:'main [data-page="agents"] .ag-run' });
      await q.capture('agents-multi-desk-modern-bottom', { viewports:[[width,height]], scrollSelector:'[data-qa-last-plate="true"]' });
      evidence.push({ viewport:`${width}x${height}`, checks });
    });
  }
  return evidence;
}

async function waitForStableCount(q, key, minimum, { timeout = 6000, polls = 4 } = {}) {
  const until = Date.now() + timeout;
  let previous = -1, stable = 0;
  while (Date.now() < until) {
    const count = (await q.counts())[key] || 0;
    if (count >= minimum && count === previous) stable++; else stable = 0;
    if (stable >= polls) return count;
    previous = count;
    await q.pause(100);
  }
  throw new Error(`Timed out waiting for ${key} to reach ${minimum} and settle.`);
}

async function tickFirstInterval(q, delay) {
  const runtime = await q.runtimeSnapshot();
  const interval = runtime?.intervals?.find(item => item.delay === delay);
  assert.ok(interval, `expected a ${delay}ms controlled interval: ${JSON.stringify(runtime?.intervals)}`);
  await q.tick([interval.id]);
  return interval;
}

async function freshDashboard(q) {
  await q.toggle('classic');
  await q.fixture({ clearReads: ['/portfolio'] });
  await q.visit('/dashboard');
}

const progressPoint = (runId, memoId, hitRate, computedAt) => ({ run_id: runId, memo_id: memoId,
  completed_at: computedAt, computed_at: computedAt, n: 12, hits: Math.round(12 * hitRate / 100), hit_rate_pct: hitRate,
  avg_edge_pct: hitRate > 60 ? 2.4 : -1.2, ci95: { low_pct: Math.max(0, hitRate - 20), high_pct: Math.min(100, hitRate + 18) },
  quality: ['ok'], comparison_key: 'synthetic-cohort', window_days: 28, maturation_days: 7, source: 'synthetic fixture',
  n_fetch_fail: 0, n_unmeasurable: 0, n_directional_candidates: 12,
  operational: { status: 'complete', models: ['fixture-only'], usage: { cost_eur: 0, partial: false, duration_s: 2, api_calls: 1, status: 'synthetic', tokens_status: 'complete' } } });

const memo = { id: 813, output_language: 'en', timestamp: stamp, title: 'Synthetic investment memo', pdf_path: '/fixture/synthetic-memo.pdf',
  appendix_path: '/fixture/synthetic-appendix.pdf', pdf_available: true, appendix_available: true, has_content: true,
  portfolio_nav_eur: 76564.57, dcf_files: '["SYNQ_DCF.xlsx"]', notes: 'Synthetic note only.', capo_tokens_in: 120, capo_tokens_out: 220,
  full_markdown: `# Synthetic investment memo\n\n## Thesis\n\nFixture-only thesis for SYNQ.\n\n## ACTION TABLE\n| Action | Ticker | EUR | Timing | Confidence |\n|---|---|---:|---|---|\n| BUY | SYNQ | 1000 | staged | HIGH |\n\n## Risk\n\nSynthetic risks only.\n\n## Sources\n\n[src: fixture-research] Synthetic source.` };
const priorMemo = { ...memo, id: 812, timestamp: '2026-09-28T12:00:00Z', title: 'Synthetic earlier investment memo',
  pdf_path: '/fixture/synthetic-earlier-memo.pdf', appendix_path: '/fixture/synthetic-earlier-appendix.pdf',
  full_markdown: memo.full_markdown.replaceAll('Synthetic investment memo', 'Synthetic earlier investment memo') };

function multiDeskOrbitFixture() {
  const definitions = [
    { id: 'orbit_01', marker: 'ORBIT-01', name: 'Northstar Strategy Research ORBIT-01', role: 'Synthetic long-horizon research desk', color: '#28A745' },
    { id: 'orbit_02', marker: 'ORBIT-02', name: 'Meridian Market Structure ORBIT-02', role: 'Synthetic market-structure analysis desk', color: '#E0B400' },
    { id: 'orbit_03', marker: 'ORBIT-03', name: 'Cobalt Credit Observatory ORBIT-03', role: 'Synthetic credit and balance-sheet desk', color: '#26A6D1' },
    { id: 'orbit_04', marker: 'ORBIT-04', name: 'Juniper Supply Chain Research ORBIT-04', role: 'Synthetic supply-chain research desk', color: '#D14A78' },
    { id: 'orbit_05', marker: 'ORBIT-05', name: 'Atlas Competitive Intelligence ORBIT-05', role: 'Synthetic competitive intelligence desk', color: '#8957D8' },
    { id: 'orbit_06', marker: 'ORBIT-06', name: 'Saffron Valuation & Scenarios ORBIT-06', role: 'Synthetic valuation and scenario desk', color: '#C56A21' },
  ];
  const startMs = Date.now() - 54 * 60 * 1000;
  const start = new Date(startMs);
  const atClock = seconds => new Date(startMs + seconds * 1000).toTimeString().slice(0, 8);
  const firstOffsets = [90, 210, 360, 510, 690, 870];
  const endOffsets = [330, 660, 1020, 1410, 1920, 2460];
  const toolLog = [];
  const usage = {};
  const specialistStatus = {};
  definitions.forEach((agent, index) => {
    const ticker = `SYN${index + 1}`;
    const context = `Fixture-only long-form context for ${agent.name}: synthetic sector drivers, balance-sheet checks, `
      + 'supplier observations, valuation sensitivities, and scenario notes; no live data or provider call.';
    toolLog.push(
      { specialist: agent.id, round: 1, tool: `fixture_market_structure_snapshot_${String(index + 1).padStart(2, '0')}`,
        input: `{"ticker": "${ticker}", "context": "${context}"}`, time: atClock(firstOffsets[index]) },
      { specialist: agent.id, round: 2, tool: `fixture_long_form_research_read_${String(index + 1).padStart(2, '0')}`,
        input: `{"ticker": "${ticker}", "context": "${context}"}`, time: atClock(endOffsets[index]) },
    );
    usage[agent.id] = { in: 1200 + index * 137, out: 340 + index * 41, cache_read: 80 + index * 9,
      cache_write: 12 + index, cost_eur: 0.08 + index * 0.013, duration_s: 130 + index * 37,
      api_calls: 2, status: 'ok', partial: false, tokens_status: 'completo' };
    specialistStatus[agent.id] = index === definitions.length - 1 ? 'running' : 'done';
  });
  const roster = definitions.map(({ id, name, role, color }) => ({ id, name, role, color, model: 'synthetic-fixture-only' }));
  const live = { running: true, start_time: start.toISOString(), updated_at: new Date(startMs + 870 * 1000).toISOString(),
    message: 'Synthetic multi-desk run; fixture only.', current_round: 2, current_specialist: definitions[5].id,
    specialist_status: specialistStatus, tool_log: toolLog, tool_log_tappato: false, n_tool_calls: toolLog.length,
    usage_by_specialist: usage,
    usage_total: { cost_eur: Object.values(usage).reduce((sum, item) => sum + item.cost_eur, 0), in: 8555, out: 2855,
      cache_read: 645, cache_write: 87, partial: false, fx_source: 'fallback', error_agents: [] } };
  return { roster, live, expectedMarkers: definitions.map(agent => agent.marker) };
}

async function dismissPortal(q, selector) {
  if (!await q.js(sel => !!document.querySelector(sel), selector)) return;
  await q.key('Escape');
  await q.waitFor(sel => !document.querySelector(sel), `dismiss ${selector}`, 2500, selector);
}

const decision = (id, action, status, archived = false) => ({ id, memo_id: 813, timestamp: stamp, action, ticker: 'SYNQ',
  eur_amount: action === 'BUY' ? 1000 : null, timing: 'Synthetic schedule', confidence: 'HIGH', rationale: `Synthetic rationale for ${id}.`,
  status, pm_feedback: null, outcome_pct: null, outcome_eur: null, outcome_notes: null,
  assessment_status: action === 'BUY' ? 'OPERATIVE' : undefined, assessment_reason: action === 'BUY' ? 'Synthetic fixture assessed.' : undefined,
  archived, archive_override: null, notes: [], veto: null, esecuzione: null });

async function run(q) {
  assert.equal(typeof q.capture, 'function', 'specialist states require visual evidence capture');
  await q.executeScenario('agents', 'multi-desk-cards-stay-readable-across-viewports-and-mode-switch', async () => {
    // 02/10/2026: the orbital dial became one card per desk plus a details panel. The guarantees stay:
    // six long desk names readable at seven viewports, nothing overlapping, the page's own choice (now
    // the details tab, before the dial pin) survives Classic/Modern and scrolling, no extra reads or writes.
    const fixture = multiDeskOrbitFixture();
    await freshDashboard(q);
    await q.fixture({ setRead: {
      '/agents/list': { body: { agents: fixture.roster, engines: { committee_r1_r2: 'synthetic-fixture-only', capo: 'synthetic-fixture-only' } } },
      '/agents/live': { body: fixture.live },
    } });
    const requestsBefore = await q.snapshot();
    const beforeVisit = await q.counts();
    await q.visit('/agents');
    await waitForCount(q, 'GET /agents/list', (beforeVisit['GET /agents/list'] || 0) + 1, { exact: true });
    await waitForCount(q, 'GET /agents/live', (beforeVisit['GET /agents/live'] || 0) + 1, { exact: true });
    const cardsSelector = 'main [data-page="agents"] .ag-desk';
    await q.waitFor(sel => document.querySelectorAll(sel).length === 6, 'six synthetic desk cards', 7000, cardsSelector);
    const beforeSwitch = await q.pageState();
    const initialCards = await q.js(sel => {
      const names = [...document.querySelectorAll(sel)].map(card => (card.querySelector('.nm b')?.textContent || '').trim());
      return { cardCount: names.length, text: names.join(' '), labels: names };
    }, cardsSelector);
    for (const marker of fixture.expectedMarkers) {
      assert.ok(initialCards.text.includes(marker), `desk card ${marker} is missing: ${initialCards.text}`);
    }

    await q.toggle('modern');
    assert.equal((await q.pageState()).id, beforeSwitch.id, 'Modern toggle remounted the six-desk page controller');
    const tabSelector = 'main [data-page="agents"] .ag-panel .bbn-seg button';
    const readState = async () => q.js((sel, cards) => ({
      tab: [...document.querySelectorAll(sel)].find(b => b.getAttribute('aria-pressed') === 'true')?.innerText.trim() || '',
      markers: [...document.querySelectorAll(cards)].map(card => (card.querySelector('.nm b')?.textContent || '').trim()).join(' '),
    }), tabSelector, cardsSelector);

    const captures = await q.capture('agents-multi-desk-modern', {
      verifyAgentsDial: true, requiredDeskMarkers: fixture.expectedMarkers,
    });
    assert.equal(captures.length, 7, 'multi-desk evidence must include all seven required viewports');
    const layoutEvidence = captures.map(capture => ({ viewport: capture.viewport, cards: capture.agentsDial || null }));
    for (const item of layoutEvidence) {
      assert.ok(item.cards, `desk card guard evidence missing at ${item.viewport}`);
      assert.equal(item.cards.cardCount, fixture.expectedMarkers.length, `six desk cards are not visible at ${item.viewport}: ${JSON.stringify(item.cards)}`);
      assert.deepEqual(item.cards.missingDeskMarkers, [], `a synthetic desk name is missing or clipped at ${item.viewport}: ${JSON.stringify(item.cards)}`);
      assert.equal(item.cards.platePlateOverlapCount, 0, `page sections overlap at ${item.viewport}: ${JSON.stringify(item.cards)}`);
    }

    const beforeTab = await readState();
    await q.click(`${tabSelector}:nth-of-type(3)`);
    await q.waitFor(sel => document.querySelectorAll(sel)[2]?.getAttribute('aria-pressed') === 'true', 'details tab selected', 3000, tabSelector);
    const chosen = await readState();
    assert.notEqual(chosen.tab, beforeTab.tab, 'a trusted click on a details tab should select it');
    const beforeModeCounts = await q.counts();
    await q.toggle('classic');
    assert.equal((await q.pageState()).id, beforeSwitch.id, 'six-desk controller remounted on Classic');
    const classic = await readState();
    assert.equal(classic.tab, chosen.tab, 'the selected details tab must persist in Classic');
    await q.toggle('modern');
    const modern = await readState();
    assert.equal(modern.tab, chosen.tab, 'the selected details tab must persist when returning to Modern');
    const scrollReachability = await verifyAgentsScrollReachability(q);
    const afterScroll = await readState();
    assert.equal(afterScroll.tab, modern.tab, 'scrolling preserves the details tab');
    for (const marker of fixture.expectedMarkers) assert.ok(afterScroll.markers.includes(marker), 'scrolling retains desk ' + marker);
    const afterModeCounts = await q.counts();
    assert.equal(afterModeCounts['GET /agents/list'], beforeModeCounts['GET /agents/list'],
      'mode switches must not reload the roster');
    assert.equal(afterModeCounts['GET /agents/live'], beforeModeCounts['GET /agents/live'],
      'mode switches must not poll the live run');
    const requestsAfter = await q.snapshot();
    const writes = requestsAfter.requests.slice(requestsBefore.requests.length)
      .filter(item => ['POST', 'PUT', 'PATCH', 'DELETE'].includes(item.method));
    assert.equal(writes.length, 0, 'the multi-desk scenario must not start agents, LLMs, or mutations');
    return { assertionResults: { sixLongDeskNamesRendered: fixture.expectedMarkers.every(marker => initialCards.text.includes(marker)),
      sixDeskCardsRendered: layoutEvidence.length === 7 && layoutEvidence.every(item => item.cards.cardCount === 6
        && item.cards.missingDeskMarkers.length === 0), sevenViewportOverlapGuardPassed: layoutEvidence.length === 7
        && layoutEvidence.every(item => item.cards.platePlateOverlapCount === 0),
      trustedClickSelectsDetailsTab: chosen.tab !== beforeTab.tab, detailsTabSurvivesClassicAndModern: classic.tab === chosen.tab && modern.tab === chosen.tab,
      stablePageAndNoExtraReads: (await q.pageState()).id === beforeSwitch.id
        && afterModeCounts['GET /agents/list'] === beforeModeCounts['GET /agents/list']
        && afterModeCounts['GET /agents/live'] === beforeModeCounts['GET /agents/live'],
      noAgentLlmOrMutationWrites: writes.length === 0 }, expectedMarkers: fixture.expectedMarkers,
      initialCards, tabBefore: beforeTab.tab, tabAfter: chosen.tab,
      layoutEvidence, scrollReachability, reads: { list: afterModeCounts['GET /agents/list'], live: afterModeCounts['GET /agents/live'] }, writes };
  });

  await q.executeScenario('agent-progress', 'tabs-series-selection-and-keyboard-reading-survive-mode-switch', async () => {
    const first = progressPoint('qa-run-1', 301, 58.3, '2026-09-20T12:00:00Z');
    const second = progressPoint('qa-run-2', 302, 75, '2026-09-29T12:00:00Z');
    const payload = { source: 'synthetic fixture', paid_analysis: false, history: { state: 'available', count: 2, available: true,
      first_captured_at: first.computed_at, note: 'Two synthetic observations only.' }, trend: { available: true, reason: null },
      agents: [{ id: 'capo', label: 'Synthetic Lead', role: 'Portfolio lead', attribution: 'collective', latest: second,
        current: null, delta: { available: true, hit_rate_pp: 16.7, reason: 'synthetic comparison' }, series: [first, second] }],
      runs: [{ run_id: 'qa-run-1', memo_id: 301, started_at: first.completed_at, completed_at: first.completed_at, captured_at: first.completed_at,
        score_error: null, review_status: 'unmarked', scorecard: { degraded: false, by_action: { BUY: { n: 12, hits: 7, hit_rate_pct: 58.3, avg_edge_pct: 1.2 } },
          by_confidence: { ALTA: { n: 8, hits: 6, hit_rate_pct: 75, avg_edge_pct: 2.4 } },
          by_confidence_scartate: { n: 1, motivo: 'Synthetic missing confidence.', etichette: { UNKNOWN: 1 } },
          details: [{ id: 1, ticker: 'SYNQ', action: 'BUY', date: first.completed_at, confidence: 'HIGH', confidence_bucket: 'ALTA',
            horizon_used: '4w', direction: 'up', ret_1w_pct: 1.2, ret_4w_pct: 3.4, edge_pct: 1.1, hit: true, specialists: ['fixture'], status: 'complete' }] },
        reflection: { text: 'Synthetic reflection.', status: 'available', kind: 'fixture', implementation_verified: false, performance_proven: false } }],
      current_scorecard: { available: true, stato: 'ready', error: null, computed_at: second.computed_at },
      method: { score: 'synthetic', attribution: 'synthetic', horizon: 'fixture', comparison: 'fixture', learning: 'fixture', timing: 'fixture' } };
    await q.fixture({ setRead: { '/agents/progress': { body: payload, delayMs: 1200 } } });
    await q.toggle('classic');
    const beforeVisit = await q.counts();
    const progressReadsBeforeVisit = beforeVisit['GET /agents/progress'] || 0;
    await q.visit('/agent-progress');
    await q.waitFor(() => !!document.querySelector('main [data-page="agent-progress"] .ap-loading'), 'agent progress delayed load');
    await waitForCount(q, 'GET /agents/progress', progressReadsBeforeVisit + 1, { exact: true });
    const before = await q.pageState(); await q.toggle('modern');
    assert.equal((await q.pageState()).id, before.id, 'progress controller remounted during delayed read');
    assert.ok(await q.js(() => !!document.querySelector('main [data-page="agent-progress"] .ap-loading')), 'progress loading state should persist after mode switch');
    await q.waitFor(() => document.querySelectorAll('main [data-page="agent-progress"] circle[role="button"]').length === 2, 'synthetic series points', 7000);
    const actionTab = 'main [data-page="agent-progress"] #ap-tab-action';
    await q.click(actionTab);
    assert.equal(await q.js(sel => document.querySelector(sel)?.getAttribute('aria-selected'), actionTab), 'true');
    await q.key('ArrowRight');
    assert.equal(await q.js(() => document.querySelector('main [data-page="agent-progress"] #ap-tab-confidence')?.getAttribute('aria-selected')), 'true',
      'right-arrow tab navigation should move to confidence evidence');
    await q.key('Enter');
    await q.waitFor(() => !!document.querySelector('main [data-page="agent-progress"] #ap-panel-confidence table'), 'confidence evidence table');
    await q.click('main [data-page="agent-progress"] #ap-tab-measurements');
    await q.waitFor(() => !!document.querySelector('main [data-page="agent-progress"] #ap-panel-measurements .ap-trend-panel summary'), 'series panel in measurements tab');
    await q.click('main [data-page="agent-progress"] #ap-panel-measurements .ap-trend-panel summary');
    await q.waitFor(() => {
      const point = document.querySelector('main [data-page="agent-progress"] circle[role="button"]');
      const rect = point?.getBoundingClientRect(); return !!rect && rect.width > 0 && rect.height > 0;
    }, 'visible series points after opening trend chart');
    const points = 'main [data-page="agent-progress"] circle[role="button"]';
    await q.js(sel => document.querySelector(sel)?.scrollIntoView({ behavior: 'instant', block: 'center' }), points + ':last-of-type');
    await q.click(points + ':last-of-type');
    await q.js(sel => document.querySelector(sel)?.focus(), points + ':last-of-type');
    await q.key('ArrowLeft');
    const activeRun = await q.js(() => ({ reading: document.querySelector('main [data-page="agent-progress"] .ap-chart-reading')?.innerText || '',
      selected: document.querySelectorAll('main [data-page="agent-progress"] circle.ap-selected').length }));
    assert.ok(activeRun.reading.includes('#301') || activeRun.reading.includes('58.3'), `keyboard point reading not updated: ${activeRun.reading}`);
    await q.capture('agent-progress-confidence-chart-modern');
    await q.toggle('classic');
    const count = (await q.counts())['GET /agents/progress'] || 0;
    assert.equal(count - progressReadsBeforeVisit, 1, 'mode switch or evidence interactions must not refetch agent progress');
    return { assertionResults: { delayedProgressStatePreserved: true, tabClickWorks: true, arrowTabNavigationWorks: true,
      confidenceBreakdownRendered: true, chartPointKeyboardSelectionWorks: true, oneProgressRead: count - progressReadsBeforeVisit === 1,
      noPaidAnalysisOrAgentRun: true }, reading: activeRun.reading, seriesPoints: 2 };
  });

  await q.executeScenario('memos', 'semantic-search-keyboard-overlay-detail-and-local-links-are-fixture-only', async () => {
    try {
    const all = [memo, priorMemo];
    const search = { results: [
      { chunk_id: 'fixture-813-1', memo_id: 813, content: 'Synthetic thesis: fixture-only assumptions for SYNQ.', distance: 0.271 },
      { chunk_id: 'fixture-812-1', memo_id: 812, content: 'Earlier synthetic thesis: fixture-only assumptions for SYNQ.', distance: 0.1842 },
    ] };
    await q.fixture({ setRead: {
      '/memos': { body: { memos: all }, delayMs: 50 }, '/memos/813': { body: memo, delayMs: 1200 },
      '/memos/812': { body: priorMemo, delayMs: 1200 },
      '/memos/search/alpha': { body: search, delayMs: 1600 },
      '/decisions': { body: { decisions: [decision(880, 'RESEARCH', 'PENDING')] } },
    } });
    await q.toggle('classic'); await q.visit('/memos');
    await q.waitFor(() => !!document.querySelector('main [data-page="memos"] .qcall'), 'memo semantic search launcher', 10000);
    const before = await q.pageState(); await q.toggle('modern');
    assert.equal((await q.pageState()).id, before.id, 'memo archive controller remounted on mode switch');
    await q.click('main [data-page="memos"] .qcall');
    await q.waitFor(() => !!document.querySelector('body .f9b-modal input'), 'memo search overlay');
    const dialog = await q.js(() => {
      const modal = document.querySelector('body .f9b-modal');
      const close = modal?.querySelector('button.f9b-close');
      const rect = close?.getBoundingClientRect();
      return { role: modal?.getAttribute('role'), modal: modal?.getAttribute('aria-modal'), labelledBy: modal?.getAttribute('aria-labelledby'),
        title: modal?.querySelector('#f9b-search-title')?.textContent?.trim() || '',
        closeButton: !!close, closeWidth: rect?.width || 0, closeHeight: rect?.height || 0 };
    });
    assert.equal(dialog.role, 'dialog'); assert.equal(dialog.modal, 'true');
    assert.equal(dialog.labelledBy, 'f9b-search-title'); assert.ok(dialog.title);
    assert.ok(dialog.closeButton, 'semantic search close affordance must be a native button');
    assert.ok(dialog.closeWidth >= 32 && dialog.closeHeight >= 32,
      `modern close target should be at least 32px: ${JSON.stringify(dialog)}`);
    await q.capture('memo-search-close-accessible-modern');
    await q.key('Escape');
    await q.waitFor(() => !document.querySelector('body .f9b-modal'), 'Escape closes semantic search');
    assert.equal(await q.js(() => document.activeElement === document.querySelector('main [data-page="memos"] .qcall')),
      true, 'Escape should restore focus to the search launcher');

    await q.click('main [data-page="memos"] .qcall');
    await q.waitFor(() => !!document.querySelector('body .f9b-modal input'), 'semantic search reopens for close-button keyboard test');
    await q.key('Tab', ['Shift']);
    assert.equal(await q.js(() => document.activeElement === document.querySelector('body .f9b-modal .f9b-close')),
      true, 'the native close button should be keyboard reachable from the search input');
    await q.key('Enter');
    await q.waitFor(() => !document.querySelector('body .f9b-modal'), 'Enter activates the semantic search close button');
    assert.equal(await q.js(() => document.activeElement === document.querySelector('main [data-page="memos"] .qcall')),
      true, 'close-button activation should restore focus to the search launcher');

    await q.click('main [data-page="memos"] .qcall');
    await q.waitFor(() => !!document.querySelector('body .f9b-modal input'), 'semantic search opens for fixture query');
    const searchInput = await markInput(q, 'body .f9b-modal .mq input');
    await q.typeText(searchInput, 'alpha');
    const beforeSearch = await q.counts();
    await q.key('Enter');
    await waitForCount(q, 'GET /memos/search/alpha', (beforeSearch['GET /memos/search/alpha'] || 0) + 1);
    assert.equal(await q.js(() => document.querySelectorAll('body .f9b-modal .mhit').length), 0,
      'the delayed search must still be unresolved when presentation changes');
    const closeGeometry = () => q.js(() => {
      const close = document.querySelector('body .f9b-modal button.f9b-close');
      const rect = close?.getBoundingClientRect(); return { button: !!close, width: rect?.width || 0, height: rect?.height || 0 };
    });
    const darkDialog = await closeGeometry();
    await q.toggle('classic');
    assert.ok(await q.js(() => !!document.querySelector('body .f9b-modal')), 'memo search overlay should remain open after presentation switch');
    // Light and Dark share one dialog: the close target keeps its size (the 13px Classic glyph is gone).
    const lightDialog = await closeGeometry();
    assert.ok(lightDialog.button && lightDialog.width >= 24 && lightDialog.height >= 24
      && lightDialog.width === darkDialog.width && lightDialog.height === darkDialog.height,
      `memo search close target is the same usable size in Light and Dark: ${JSON.stringify({ darkDialog, lightDialog })}`);
    await q.waitFor(() => !!document.querySelector('body .f9b-modal .mhit'), 'synthetic semantic search result', 8000);
    const afterSearch = await q.snapshot();
    const searchReads = afterSearch.requests.filter(item => item.method === 'GET' && item.route === '/memos/search/alpha');
    assert.equal(searchReads.length, 1, 'mode switch must not repeat the semantic search');
    await q.capture('memo-semantic-search-result-classic');
    await q.key('ArrowDown');
    const selected = await q.js(() => document.querySelector('body .f9b-modal .mhit.on')?.innerText || '');
    assert.ok(selected.includes('MEMO 812') && selected.includes('SYNQ'), 'arrow-key selection should retain the second synthetic hit');
    await q.click(searchInput);
    await q.key('Enter');
    await q.waitFor(() => !document.querySelector('body .f9b-modal')
      && (document.querySelector('main [data-page="memos"]')?.innerText || '').toUpperCase().includes('SYNTHETIC EARLIER INVESTMENT MEMO')
      && (document.querySelector('main [data-page="memos"] .doc')?.innerText || '').includes('Fixture-only thesis for SYNQ.'),
      'search result opens memo detail', 9000);
    const links = await q.js(() => [...document.querySelectorAll('main [data-page="memos"] .apri a')].map(a => ({ href: a.href, target: a.target })));
    assert.ok(links.some(link => link.href.endsWith('/memos/812/pdf') && link.target === '_blank'));
    assert.ok(links.some(link => link.href.endsWith('/memos/812/appendix') && link.target === '_blank'));
    const snap = await q.snapshot();
    assert.equal(snap.requests.filter(item => item.method === 'GET' && item.route === '/memos/search/alpha').length, 1);
    assert.equal(snap.requests.filter(item => item.method === 'GET' && item.route === '/memos/812').length, 1,
      'opening a different search hit should fetch that memo detail once');
    return { assertionResults: { searchModalOpens: true, dialogHasAccessibleName: true, modernCloseTargetMeetsMinimum: true,
      escapeClosesAndRestoresFocus: true, keyboardCloseButtonClosesAndRestoresFocus: true, classicCloseGeometryUnchanged: true,
      delayedQueryRemainsOpenAcrossModeSwitch: true, oneSemanticSearchRequest: true, keyboardSelectionOpensMemo: true,
      detailRendered: true, localPdfAndAppendixLinksVisible: true },
      downloads: 'PDF/appendix links verified against fixture-local URLs; not clicked because new-window/download effects are denied by the isolated harness.',
      fixture: 'synthetic memo, search hits, and decision joins only' };
    } finally {
      await dismissPortal(q, 'body .f9b-modal');
      await dismissPortal(q, 'body .cfm-modal');
    }
  });

  await q.executeScenario('decisions', 'research-note-close-reopen-and-trade-link-use-explicit-fixture-writes', async () => {
    await dismissPortal(q, 'body .f9b-modal');
    await dismissPortal(q, 'body .cfm-modal');
    const initial = { decisions: [decision(880, 'RESEARCH', 'PENDING'), decision(881, 'RESEARCH', 'EXPIRED', true), decision(882, 'BUY', 'PENDING')] };
    await q.fixture({ setRead: { '/decisions': { body: initial, delayMs: 1200 }, '/trades': { body: { count: 0, trades: [] } } }, setWrite: {
      '/decisions/880/note': { body: { id: 900, autore: 'PM', testo: 'Synthetic committee note.', timestamp: stamp }, delayMs: 1400 },
      '/decisions/880/update': { body: { ok: true }, delayMs: 1200 },
      '/decisions/881/update': { body: { ok: true } },
    } });
    // Pagina Nuova (05/10/2026): viste a segmenti, elenco + dettaglio; selettori data-dc-*.
    const D = 'main [data-page="decisions"]';
    await q.toggle('classic'); await q.visit('/decisions');
    await q.waitFor(() => (document.querySelector('main [data-page="decisions"]')?.innerText || '').includes('SYNQ'), 'decision board delayed read', 7000);
    const before = await q.pageState(); await q.toggle('modern');
    assert.equal((await q.pageState()).id, before.id, 'decision controller remounted during load');
    await q.click(await markButton(q, { root: `${D} .dc-top`, pattern: 'in ricerca|in research' }));
    await q.waitFor(() => !!document.querySelector('main [data-page="decisions"] [data-dc-conv="880"]'), 'research view opens the pending research', 5000);
    await q.capture('decisions-research-pending-modern');
    const noteInput = await markInput(q, `${D} [data-dc-campo="nota"]`);
    await q.setValue(noteInput, 'Synthetic committee note.');
    const beforeNote = await q.counts(); await q.click(`${D} [data-dc-azione="invia"]`);
    await waitForCount(q, 'POST /decisions/880/note', (beforeNote['POST /decisions/880/note'] || 0) + 1);
    await q.toggle('classic');
    assert.equal(await q.js(() => !!document.querySelector('main [data-page="decisions"] [data-dc-conv="880"]')), true,
      'research detail should remain mounted while note saves');
    assert.ok(await q.js(() => !!document.querySelector('main [data-page="decisions"] [data-dc-azione="invia"]')?.disabled),
      'committee note busy state should survive the presentation switch');
    await q.waitFor(() => document.querySelector('main [data-page="decisions"] [data-dc-campo="nota"]')?.value === '',
      'saved committee note clears its controlled draft', 8000);
    const notePost = (await q.snapshot()).requests.filter(item => item.method === 'POST' && item.route === '/decisions/880/note');
    assert.equal(notePost.length, 1); assert.equal(notePost[0].input.testo, 'Synthetic committee note.');

    // la pagina resta occupata finché non ha riletto le decisioni dopo la nota: poi la chiusura si abilita
    await q.waitFor(() => document.querySelector('main [data-page="decisions"] [data-dc-azione="ricerca-chiudi"]')?.disabled === false,
      'note write and reload settle', 9000);
    const beforeClose = await q.counts(); await q.click(`${D} [data-dc-azione="ricerca-chiudi"]`);
    await waitForCount(q, 'POST /decisions/880/update', (beforeClose['POST /decisions/880/update'] || 0) + 1);
    await q.toggle('modern');
    assert.ok(await q.js(() => !!document.querySelector('main [data-page="decisions"] [data-dc-azione="ricerca-chiudi"]')?.disabled),
      'research close remains pending across a presentation switch');
    const closePost = (await q.snapshot()).requests.filter(item => item.method === 'POST' && item.route === '/decisions/880/update').at(-1);
    assert.equal(closePost.input.status, 'EXPIRED', 'close research must send terminal status');
    await q.waitFor(() => document.querySelector('main [data-page="decisions"] [data-dc-azione="ricerca-chiudi"]')?.disabled === false,
      'research close write settles', 9000);
    await waitForStableCount(q, 'GET /decisions', (beforeClose['GET /decisions'] || 0) + 1);

    await q.click(await markButton(q, { root: `${D} .dc-top`, pattern: 'archivio|archive' }));
    await q.click(await markButton(q, { root: `${D} .dc-list .dc-head-row`, pattern: 'ricerche|research' }));
    await q.waitFor(() => !!document.querySelector('main [data-page="decisions"] [data-dc-dettaglio="881"]'), 'archived research detail');
    await q.waitFor(() => document.querySelector('main [data-page="decisions"] [data-dc-azione="ricerca-ripristina"]')?.disabled === false,
      'archived research restore action enabled', 7000);
    const beforeReopen = await q.counts(); await q.click(`${D} [data-dc-azione="ricerca-ripristina"]`);
    await waitForCount(q, 'POST /decisions/881/update', (beforeReopen['POST /decisions/881/update'] || 0) + 1);
    const reopenPost = (await q.snapshot()).requests.filter(item => item.method === 'POST' && item.route === '/decisions/881/update').at(-1);
    assert.equal(reopenPost.input.status, 'PENDING');
    await waitForStableCount(q, 'GET /decisions', (beforeReopen['GET /decisions'] || 0) + 1);

    await q.click(await markButton(q, { root: `${D} .dc-top`, pattern: 'da decidere|to decide' }));
    await q.waitFor(() => !!document.querySelector('main [data-page="decisions"] [data-dc-sel="882"]'), 'operational BUY row');
    await q.click(`${D} [data-dc-sel="882"]`);
    await q.waitFor(() => !!document.querySelector('main [data-page="decisions"] [data-dc-dettaglio="882"]'), 'operational detail');
    const detailStyle = [];
    for (const [width, height] of [[1920, 1080], [2560, 1440], [3440, 1440], [5120, 1440],
      [1440, 1000], [1280, 900], [900, 700]]) {
      const style = await q.withViewport(width, height, async () => {
        await q.toggle('modern');
        const result = await q.js(() => {
          const card = document.querySelector('main [data-page="decisions"] .dc-det');
          const rationale = card?.querySelector('.dc-prose');
          const background = card ? getComputedStyle(card).backgroundColor : '';
          const foreground = rationale ? getComputedStyle(rationale).color : '';
          const channels = value => (value.match(/[\d.]+/g) || []).slice(0, 3).map(Number).map(channel => {
            const normalized = channel / 255;
            return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
          });
          const luminance = value => { const [red, green, blue] = channels(value); return 0.2126 * red + 0.7152 * green + 0.0722 * blue; };
          const bgLuminance = luminance(background), fgLuminance = luminance(foreground);
          const contrast = (Math.max(bgLuminance, fgLuminance) + 0.05) / (Math.min(bgLuminance, fgLuminance) + 0.05);
          return { exists: !!card && !!rationale, background, foreground, contrast, overflowX: document.documentElement.scrollWidth > innerWidth,
            rationale: rationale?.textContent?.trim() || '' };
        });
        assert.ok(result.exists, `operational detail missing at ${width}x${height}`);
        assert.ok(result.contrast >= 4.5, `detail text contrast below 4.5:1 at ${width}x${height}: ${JSON.stringify(result)}`);
        assert.equal(result.overflowX, false, `horizontal page overflow at ${width}x${height}`);
        await q.capture('decisions-operational-detail-modern', { viewports: [[width, height]] });
        return result;
      });
      detailStyle.push({ viewport: `${width}x${height}`, ...style });
    }
    await q.click(`${D} [data-dc-azione="collega"]`);
    await q.waitFor(expected => location.hash === expected, 'trade-entry navigation from linked decision', 5000, '#/trades?decision=882');
    const tradeHash = await q.js(() => location.hash);
    const snapshot = await q.snapshot();
    const writes = snapshot.requests.filter(item => item.method === 'POST' && /^\/decisions\/(880\/(note|update)|881\/update)$/.test(item.route));
    assert.equal(writes.length, 3, 'the explicit note, close, and reopen actions should each write once');
    return { assertionResults: { viewSegmentsWorkInModern: true, notePayloadExactAndOnce: true,
      researchCloseSendsExpired: closePost.input.status === 'EXPIRED', archivedResearchCanReopen: reopenPost.input.status === 'PENDING',
      operationalDecisionOpensTradeEntry: tradeHash === '#/trades?decision=882',
      operationalDetailReadableAtSevenViewports: detailStyle.length === 7,
      noTradeOrderSubmitted: true },
      detailStyle, writeRoutes: writes.map(item => item.route), tradeHash,
      safety: 'GET /decisions remained fixture-only because the real route may auto-expire stale decisions.' };
  });

  await q.executeScenario('agents', 'unreadable-heartbeat-and-live-read-error-are-distinct-visible-states', async () => {
    const unreadable = { running: false, heartbeat: 'illeggibile', message: 'Synthetic heartbeat file unreadable.', tool_log: [] };
    const staleRun = { running: true, start_time: stamp, updated_at: stamp, stale_warning: true, stale_seconds: 660,
      message: 'Synthetic stale live run.', current_round: 1, current_specialist: 'screener',
      specialist_status: { screener: 'running' }, tool_log: [{ time: '14:00:01', round: 1, specialist: 'screener', tool: 'fixture.read', input: 'SYNQ' }],
      usage_total: { cost_eur: 0, in: 0, out: 0, cache_read: 0, cache_write: 0, partial: false, fx_source: 'fallback', error_agents: [] },
      usage_by_specialist: {}, n_tool_calls: 1 };
    await freshDashboard(q);
    await q.fixture({ setRead: {
      '/agents/list': { body: { agents: [], engines: {} } },
      '/agents/live': { body: staleRun },
    } });
    const beforeRead = await q.counts();
    await q.visit('/agents');
    await q.waitFor(() => !!document.querySelector('main [data-page="agents"] .ag-run'), 'agent live read state page');
    await waitForCount(q, 'GET /agents/live', (beforeRead['GET /agents/live'] || 0) + 1);
    await q.waitFor(() => !!document.querySelector('main [data-page="agents"] button.ag-stop'), 'fixture stale run state');
    const before = await q.pageState(); await q.toggle('modern');
    const modern = await q.pageState();
    assert.equal(modern.id, before.id, 'live heartbeat display mode change remounted its poll controller');
    assert.ok(await q.js(() => !!document.querySelector('main [data-page="agents"] .ag-ban.is-bad[data-avviso="fermo"]')),
      'stale running heartbeat should be separately declared');
    await q.fixture({ setRead: { '/agents/live': { body: unreadable } } });
    const beforeUnreadable = await q.counts();
    const unreadablePoll = await tickFirstInterval(q, 1500);
    await waitForCount(q, 'GET /agents/live', (beforeUnreadable['GET /agents/live'] || 0) + 1);
    await q.waitFor(() => [...document.querySelectorAll('main [data-page="agents"] .ag-ban')]
      .some(node => node.innerText.includes('Synthetic heartbeat file unreadable.')), 'unreadable heartbeat declared');
    const heartbeat = await q.js(() => [...document.querySelectorAll('main [data-page="agents"] .ag-ban')]
      .map(node => node.innerText).find(text => text.includes('Synthetic heartbeat file unreadable.')) || '');
    assert.ok(heartbeat.includes('Synthetic heartbeat file unreadable.'), 'unreadable state should retain the backend message');
    await q.capture('agents-heartbeat-unreadable-modern');

    await q.fixture({ setRead: { '/agents/live': { body: { detail: 'Synthetic live endpoint unavailable.' }, status: 503 } } });
    const beforeError = await q.counts();
    const interval = await tickFirstInterval(q, 1500);
    await waitForCount(q, 'GET /agents/live', (beforeError['GET /agents/live'] || 0) + 1);
    await q.waitFor(() => [...document.querySelectorAll('main [data-page="agents"] .ag-ban[data-avviso="backend"]')]
      .some(node => node.innerText.includes('Synthetic live endpoint unavailable.')), 'live HTTP error after controlled poll');
    const liveError = await q.js(() => [...document.querySelectorAll('main [data-page="agents"] .ag-ban[data-avviso="backend"]')]
      .map(node => node.innerText).find(text => text.includes('Synthetic live endpoint unavailable.')) || '');
    assert.ok(liveError.includes('Synthetic live endpoint unavailable.'));
    // a failed read is never shown as «no run»: the run box declares the state unavailable
    assert.equal(await q.js(() => document.querySelector('main [data-page="agents"] .bbn-agents')?.dataset.vista), 'cieco',
      'live HTTP error must not render the idle run box');
    assert.equal(unreadablePoll.delay, 1500); assert.equal(interval.delay, 1500);
    await q.capture('agents-live-read-error-modern');
    return { assertionResults: { unreadableHeartbeatRetainsDeclaredState: heartbeat.includes('Synthetic heartbeat file unreadable.'),
      liveHttpErrorIsNotReportedAsIdle: liveError.includes('Synthetic live endpoint unavailable.'),
      modernSwitchPreservesPollController: modern.id === before.id, staleRunShownBeforeUnreadablePoll: true,
      unreadablePollWasExplicitlyTicked: true, errorPollWasExplicitlyTicked: true,
      noRealAgentOrLlmStarted: true }, intervals: [{ delay: unreadablePoll.delay }, { delay: interval.delay }],
      fixture: 'all /agents reads were served by explicit fixtures; running state is synthetic and no run was started' };
  });

  await q.executeScenario('agent-progress', 'empty-roster-read-is-explicit-and-remains-empty-in-modern', async () => {
    const empty = { source: 'synthetic fixture', paid_analysis: false,
      history: { state: 'empty', count: 0, available: false, first_captured_at: null, note: 'Synthetic fixture contains no completed runs.' },
      trend: { available: false, reason: 'No observations in this fixture.' }, agents: [], runs: [],
      current_scorecard: { available: false, stato: 'empty', error: null, computed_at: null },
      method: { score: 'synthetic', attribution: 'synthetic', horizon: 'synthetic', comparison: 'synthetic', learning: 'synthetic', timing: 'synthetic' } };
    await freshDashboard(q);
    const beforeReads = await q.counts();
    await q.fixture({ setRead: { '/agents/progress': { body: empty } } });
    await q.visit('/agent-progress');
    await waitForCount(q, 'GET /agents/progress', (beforeReads['GET /agents/progress'] || 0) + 1);
    await q.waitFor(() => !!document.querySelector('main [data-page="agent-progress"] p[role="status"]'), 'empty agent progress state');
    const before = await q.pageState(); await q.toggle('modern');
    const modern = await q.pageState();
    const message = await q.js(() => document.querySelector('main [data-page="agent-progress"] p[role="status"]')?.innerText || '');
    assert.ok(message.length > 0, 'empty agent roster must be explicitly described');
    assert.equal(modern.id, before.id, 'empty progress response caused a mode-specific remount');
    assert.equal((await q.counts())['GET /agents/progress'] - (beforeReads['GET /agents/progress'] || 0), 1,
      'presentation change must not refetch empty progress');
    await q.capture('agent-progress-empty-modern');
    return { assertionResults: { emptyRosterIsExplicit: message.length > 0, oneFixtureRead: true,
      modernSwitchKeepsController: modern.id === before.id, noPaidAnalysisOrAgentRun: true }, message, fixture: 'empty progress payload only' };
  });

  await q.executeScenario('agent-progress', 'progress-endpoint-read-error-is-not-empty-history', async () => {
    await freshDashboard(q);
    const beforeReads = await q.counts();
    await q.fixture({ setRead: { '/agents/progress': { body: { detail: 'Synthetic progress store unavailable.' }, status: 503, delayMs: 900 } } });
    await q.visit('/agent-progress');
    await q.waitFor(() => !!document.querySelector('main [data-page="agent-progress"] .ap-loading'), 'progress error read in-flight');
    const before = await q.pageState(); await q.toggle('modern');
    assert.equal((await q.pageState()).id, before.id, 'progress controller remounted during delayed read error');
    await q.waitFor(() => !!document.querySelector('main [data-page="agent-progress"] .ap-error'), 'progress error declared');
    const error = await q.js(() => document.querySelector('main [data-page="agent-progress"] .ap-error')?.innerText || '');
    assert.ok(error.includes('Synthetic progress store unavailable.'), `progress error detail not displayed: ${error}`);
    assert.equal((await q.counts())['GET /agents/progress'] - (beforeReads['GET /agents/progress'] || 0), 1);
    await q.capture('agent-progress-read-error-modern');
    return { assertionResults: { delayedReadErrorIsDeclared: true, emptyHistoryNotInferred: true,
      controllerStableAcrossModeSwitch: true, oneFixtureRead: true }, error, fixture: 'synthetic HTTP 503; paid-analysis flag is never true' };
  });

  await q.executeScenario('memos', 'empty-memo-index-is-distinct-from-the-read-error', async () => {
    await freshDashboard(q);
    const beforeReads = await q.counts();
    await q.fixture({ setRead: { '/memos': { body: { memos: [] } }, '/decisions': { body: { decisions: [] } } } });
    await q.visit('/memos');
    await waitForCount(q, 'GET /memos', (beforeReads['GET /memos'] || 0) + 2);
    await q.waitFor(() => {
      const text = document.querySelector('main [data-page="memos"] .f9b .scroll p')?.innerText || '';
      return !!text && !/loading|reading|…|\.\.\./i.test(text);
    }, 'empty memo-index declaration');
    const before = await q.pageState(); await q.toggle('modern');
    const modern = await q.pageState();
    const view = await q.js(() => ({ cards: document.querySelectorAll('main [data-page="memos"] .f9b .mr').length,
      emptyText: document.querySelector('main [data-page="memos"] .f9b .scroll p')?.innerText || '',
      loading: !!document.querySelector('main [data-page="memos"] .f9b .scroll [role="status"]') }));
    assert.equal(view.cards, 0); assert.ok(view.emptyText.length > 0); assert.equal(modern.id, before.id);
    await q.capture('memos-index-empty-modern');
    return { assertionResults: { emptyMemoIndexHasExplicitMessage: view.emptyText.length > 0, noMemoCards: view.cards === 0,
      modeSwitchKeepsArchiveController: modern.id === before.id, fixtureDecisionsRead: true }, view, fixture: 'both memo list reads and decisions were fixture-backed' };
  });

  await q.executeScenario('memos', 'memo-index-503-is-reported-with-retry-not-rendered-as-empty', async () => {
    await freshDashboard(q);
    const beforeReads = await q.counts();
    await q.fixture({ setRead: { '/memos': { body: { detail: 'Synthetic memo index unavailable.' }, status: 503 },
      '/decisions': { body: { decisions: [] } } } });
    await q.visit('/memos');
    await waitForCount(q, 'GET /memos', (beforeReads['GET /memos'] || 0) + 2);
    await q.waitFor(() => !!document.querySelector('main [data-page="memos"] .f9b .p3 button'), 'memo error retry action');
    await q.toggle('modern');
    const state = await q.js(() => ({ message: document.querySelector('main [data-page="memos"] .f9b .p-3')?.innerText || '',
      retry: [...document.querySelectorAll('main [data-page="memos"] .f9b button')].some(button => /retry|riprova/i.test(button.innerText)) }));
    assert.ok(state.message.includes('Synthetic memo index unavailable.'), `memo error should retain endpoint detail: ${state.message}`);
    assert.equal(state.retry, true); assert.equal(await q.js(() => document.querySelectorAll('main [data-page="memos"] .f9b .mr').length), 0);
    await q.capture('memos-index-read-error-modern');
    return { assertionResults: { indexFailureIsDeclared: true, notFalseEmpty: true, retryAvailable: state.retry }, state,
      fixture: 'memo index 503 and empty decisions were explicit fixture responses' };
  });

  await q.executeScenario('memos', 'memo-with-absent-body-declares-missing-markdown-not-read-error', async () => {
    await freshDashboard(q);
    const beforeReads = await q.counts();
    const noBody = { ...memo, id: 814, title: 'Synthetic memo without body', has_content: false,
      pdf_available: false, appendix_available: false, full_markdown: null, pdf_path: null, appendix_path: null };
    await q.fixture({ setRead: { '/memos': { body: { memos: [noBody] } }, '/memos/814': { body: noBody },
      '/decisions': { body: { decisions: [] } } } });
    await q.visit('/memos');
    await waitForCount(q, 'GET /memos', (beforeReads['GET /memos'] || 0) + 2);
    await q.waitFor(() => (document.querySelector('main [data-page="memos"] .f9b .cMid')?.innerText || '').includes('full_markdown'),
      'absent memo body explanation');
    const before = await q.pageState(); await q.toggle('modern');
    const modern = await q.pageState();
    const bodyState = await q.js(() => document.querySelector('main [data-page="memos"] .f9b .cMid')?.innerText || '');
    const bodyAlert = await q.js(() => !!document.querySelector('main [data-page="memos"] .f9b .cMid [role="alert"]'));
    assert.ok(bodyState.includes('full_markdown'), `absent memo body needs a specific explanation: ${bodyState}`);
    assert.equal(modern.id, before.id); assert.equal(bodyAlert, false, 'metadata-only memo should not be mislabeled as a failed read');
    assert.equal((await q.counts())['GET /memos/814'] - (beforeReads['GET /memos/814'] || 0), 1);
    await q.capture('memos-body-absent-modern');
    return { assertionResults: { absentMarkdownExplained: true, bodyWasReadOnceFromFixture: true,
      notRenderedAsReadError: !bodyAlert, controllerStable: true }, bodyState,
      fixture: 'the memo entry is explicitly metadata-only; no PDF is opened or downloaded' };
  });

  await q.executeScenario('decisions', 'empty-decision-board-and-decisions-read-error-remain-distinguishable', async () => {
    await freshDashboard(q);
    const beforeEmpty = await q.counts();
    await q.fixture({ setRead: { '/decisions': { body: { decisions: [] } } } });
    await q.visit('/decisions');
    await waitForCount(q, 'GET /decisions', (beforeEmpty['GET /decisions'] || 0) + 1);
    await q.waitFor(() => !!document.querySelector('main [data-page="decisions"] .dc-list .dc-empty'), 'decision board empty state');
    const before = await q.pageState(); await q.toggle('modern');
    const modern = await q.pageState();
    const empty = await q.js(() => ({ loading: !!document.querySelector('main [data-page="decisions"] .dc-skel'),
      rows: document.querySelectorAll('main [data-page="decisions"] [data-dc-sel]').length,
      counts: [...document.querySelectorAll('main [data-page="decisions"] [data-dc-conta]')].map(el => el.textContent.trim()),
      emptyPanes: [...document.querySelectorAll('main [data-page="decisions"] .dc-empty')].map(el => el.innerText).filter(Boolean) }));
    assert.equal(empty.loading, false); assert.equal(empty.rows, 0);
    assert.deepEqual(empty.counts, ['0', '0', '0', '0'], `all four views declare zero: ${JSON.stringify(empty)}`);
    assert.ok(empty.emptyPanes.length >= 1, `the empty queue should be declared: ${JSON.stringify(empty)}`);
    assert.equal(modern.id, before.id);
    assert.equal((await q.counts())['GET /decisions'] - (beforeEmpty['GET /decisions'] || 0), 1);
    await q.capture('decisions-board-empty-modern');

    await freshDashboard(q);
    const beforeError = await q.counts();
    await q.fixture({ setRead: { '/decisions': { body: { detail: 'Synthetic decisions store unavailable.' }, status: 503 } } });
    await q.visit('/decisions');
    await waitForCount(q, 'GET /decisions', (beforeError['GET /decisions'] || 0) + 1);
    await q.waitFor(() => !!document.querySelector('main [data-page="decisions"] [data-dc-errore="lettura"]'), 'decisions error banner');
    await q.toggle('modern');
    const error = await q.js(() => document.querySelector('main [data-page="decisions"] [data-dc-errore="lettura"]')?.innerText || '');
    assert.ok(await q.js(() => [...document.querySelectorAll('main [data-page="decisions"] [data-dc-conta]')].every(el => /^n[./]/.test(el.textContent.trim()))),
      'a failed read never shows zero counters');
    assert.ok(error.includes('Synthetic decisions store unavailable.'), `decision API error not shown: ${error}`);
    assert.equal((await q.counts())['GET /decisions'] - (beforeError['GET /decisions'] || 0), 1,
      'exactly one fixture read for the failed board load');
    await q.capture('decisions-board-read-error-modern');
    return { assertionResults: { emptyQueuesExplicitlyDeclared: empty.emptyPanes.length >= 1,
      apiReadFailureIsNotReportedAsEmpty: error.includes('Synthetic decisions store unavailable.'),
      oneFixtureReadPerLoad: true, noLiveDecisionReadOrAutoExpiry: true }, empty, error,
      safety: 'both GET /decisions responses were served by fixtures; the real auto-expiring route was not touched' };
  });
}

module.exports = { run };
