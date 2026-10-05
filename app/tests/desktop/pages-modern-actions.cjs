// Functional retention/action audit for the synthetic, isolated desktop build.
// This harness never starts the real backend, real agent runners, or an LLM.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { execFileSync } = require('node:child_process');
const crypto = require('node:crypto');
const { startFixture, fixtureData } = require('./pages-modern-fixtures.cjs');
const actionData = require('./pages-modern-action-data.cjs');
const { installRuntimeInstrumentation } = require('./pages-modern-instrumentation.cjs');

const root = path.resolve(__dirname, '../..');
const MARK = 'PAGES_MODERN_ACTIONS_RESULT ';
const HANDSHAKE = 'PAGES_MODERN_ACTIONS_FIXTURE ';
const PAGES = [
  ['dashboard', '/dashboard'], ['performance', '/performance'], ['watchlist', '/watchlist'],
  ['market', '/market'], ['news', '/news'], ['fundamentals', '/fundamentals'],
  ['factors', '/factors'], ['backtest', '/backtest'], ['vol', '/vol'], ['edge', '/edge'],
  ['agents', '/agents'], ['agent-progress', '/agent-progress'], ['memos', '/memos'],
  ['decisions', '/decisions'], ['trades', '/trades'], ['movements', '/movements'], ['mandato', '/mandato'],
];
const SPECIALISTS = [
  'pages-modern-risk-actions.cjs',
  'pages-modern-committee-actions.cjs',
  'pages-modern-operations-actions.cjs',
  'pages-modern-research-states.cjs',
  'pages-modern-research-actions.cjs',
];
const ELECTRON_CSP_WARNING = `%cElectron Security Warning (Insecure Content-Security-Policy) font-weight: bold; This renderer process has either no Content Security
  Policy set or a policy with "unsafe-eval" enabled. This exposes users of
  this app to unnecessary security risks.

For more information and help, consult
https://electronjs.org/docs/tutorial/security.
This warning will not show up
once the app is packaged.`;
const CANVAS2D_READBACK_ADVISORY = 'Canvas2D: Multiple readback operations using getImageData are faster with the willReadFrequently attribute set to true. See: https://html.spec.whatwg.org/multipage/canvas.html#concept-canvas-will-read-frequently';
const EXPECTED_PRESENTER_FAULT_MESSAGES = new Set([
  'Error: qa-controlled-presenter-fault',
  '[NewInterfaceBoundary] caught: Error: qa-controlled-presenter-fault [object Object]',
]);
function hasVerifiedNativeVolMesh(scenario) {
  if (scenario?.page !== 'vol' || scenario?.status !== 'passed'
    || scenario?.scenario !== 'populated-chain-details-strategy-payoff-and-native-plotly-camera-use-fixtures'
    || scenario?.assertionResults?.livePlotlySurfaceMeshAndWebGLVerified !== true
    || scenario?.assertionResults?.nativeCameraUpdatedAndPreserved !== true) return false;
  const plot = scenario.nativePlotlyCamera?.afterSevenViewportCapture;
  const canvas = plot?.selectedCanvas;
  return Number(plot?.meshGrid?.rows) > 1 && Number(plot?.meshGrid?.columns) > 1
    && !!canvas?.contextVersion && canvas.contextLost === false
    && ['webgl', 'webgl2', 'experimental-webgl'].includes(canvas.contextType);
}
function auditVolCanvasReadbackAdvisory(consoleErrors, { graphicsMode, scenarios = [] } = {}) {
  const nativeVolMeshVerified = graphicsMode === 'native' && scenarios.some(hasVerifiedNativeVolMesh);
  const expectedCanvasReadbackAdvisories = nativeVolMeshVerified
    ? consoleErrors.filter(item => item.page === '/vol' && item.level === 2 && item.message === CANVAS2D_READBACK_ADVISORY)
    : [];
  return { nativeVolMeshVerified, expectedCanvasReadbackAdvisories };
}
const GROUP_MODULES = {
  core: 'core',
  research: 'research',
  'journal-config': 'journal-config',
  recovery: 'recovery',
  risk: SPECIALISTS[0],
  committee: SPECIALISTS[1],
  operations: SPECIALISTS[2],
  states: SPECIALISTS[3],
  'research-extra': SPECIALISTS[4],
};
const ALL_GROUPS = Object.keys(GROUP_MODULES);
const ROUTE_PAGE = { backtest: 'montecarlo' };
const REQUIRED_SCENARIOS_BY_GROUP = {
  core: PAGES.map(([page]) => `${page}:state-and-controller-survive-mode-switches`),
  research: [
    'performance:range-control-is-retained', 'watchlist:note-draft-is-retained-without-writing',
    'market:search-detail-tabs-and-controller-retain-state', 'news:wire-desk-and-filter-state-retain',
    'fundamentals:selected-model-and-variant-draft-retain',
  ],
  'journal-config': [
    'journal:nested-journal-draft-and-tab-retain',
    'settings:config-modal-focus-escape-cancel-and-mode-choice-reachable',
    'journal:create-update-history-archive-restore-and-version-conflict',
    'settings:preferences-tasks-backup-create-delete-and-mode-retention',
  ],
  recovery: [
    'performance:presenter-fault-recovery', 'trades:trade-local-presenter-fault-retains-draft',
    'mandato:mandato-local-presenter-fault-retains-preview', 'journal:journal-local-presenter-fault-retains-draft',
    'vol:vol-local-presenter-fault-retains-catalog-request',
    'filing:local-presenter-fault-retains-profile-save',
    'memos:memo-local-presenter-fault-retains-search-request',
  ],
  risk: [
    'factors:populated-recalc-preserves-controller-across-mode-switch',
    'backtest:what-if-validation-and-single-inflight-v3-run-survive-mode-switch',
    'vol:populated-chain-details-strategy-payoff-and-native-plotly-camera-use-fixtures',
    'vol:download-pause-and-resume-remain-in-flight-across-mode-switch',
    'edge:threshold-force-refresh-and-cache-age-are-distinct-fixture-actions',
    'factors:partial-coverage-and-reported-factor-error-remain-distinguishable',
    'backtest:empty-invalid-what-if-is-blocked-and-fixture-error-is-retained',
    'edge:scanner-network-error-is-declared-and-retry-is-available',
    'vol:empty-expiry-catalogue-declares-zero-availability-and-disables-download',
    'vol:empty-observed-chain-and-filter-result-are-declared',
    'backtest:portfolio-read-error-is-not-presented-as-an-empty-simulation-book',
  ],
  committee: [
    'agents:multi-desk-cards-stay-readable-across-viewports-and-mode-switch',
    'agent-progress:tabs-series-selection-and-keyboard-reading-survive-mode-switch',
    'memos:semantic-search-keyboard-overlay-detail-and-local-links-are-fixture-only',
    'decisions:research-note-close-reopen-and-trade-link-use-explicit-fixture-writes',
    'agents:unreadable-heartbeat-and-live-read-error-are-distinct-visible-states',
    'agent-progress:empty-roster-read-is-explicit-and-remains-empty-in-modern',
    'agent-progress:progress-endpoint-read-error-is-not-empty-history',
    'memos:empty-memo-index-is-distinct-from-the-read-error',
    'memos:memo-index-503-is-reported-with-retry-not-rendered-as-empty',
    'memos:memo-with-absent-body-declares-missing-markdown-not-read-error',
    'decisions:empty-decision-board-and-decisions-read-error-remain-distinguishable',
  ],
  operations: [
    'trades:validated-preview-cancel-confirm-once-and-unknown-write',
    'trades:positions-loading-empty-and-refresh-error-never-keep-stale-values',
    'trades:cash-threshold-confirmation-cancellation-and-inflight-switch',
    'trades:opening-preview-cancel-and-confirm-once-while-pending',
    'trades:opening-register-loading-empty-and-error-are-distinct',
    'movements:register-filters-lanes-and-three-views-use-fixture-only',
    'movements:register-loading-empty-and-stale-refresh-error',
    'mandato:seven-section-validation-preview-save-and-pending-switch',
    'mandato:stale-preview-fingerprint-is-disclosed-without-retry',
    'mandato:read-loading-error-and-explicit-retry',
    'journal:create-update-history-archive-restore-and-version-conflict',
    'journal:journal-loading-empty-search-and-pagination',
    'settings:preferences-tasks-backup-create-delete-and-mode-retention',
    'settings:modern-settings-per-resource-loading-and-read-errors',
  ],
  states: [
    'watchlist:read-loading-empty-error-zero-and-absence-are-distinct',
    'fundamentals:registry-loading-empty-and-read-error-have-distinct-messages',
    'news:wire-loading-empty-error-and-stale-provider-remain-distinct',
    'performance:nav-loading-empty-and-domain-error-do-not-invent-accounting-zero',
    'market:debounced-search-loading-empty-and-source-error-retain-query',
  ],
  'research-extra': [
    'watchlist:note-save-error-remove-and-keyboard-navigation', 'market:favorite-rollback',
    'fundamentals:refresh-lock-personal-copy-and-authenticated-download',
    'filing:validation-profile-save-and-manual-check',
    'filing:bulk-activation-and-link-confirm',
    'filing:ai-proposal-button-only',
    'filing:pdf-found-one-click',
    'filing:summary-links-to-page',
    'fundamentals:bank-regulated-assets-nav-and-archived-workbooks',
    'news:provider-refresh-held-operation-and-result-uses-one-post',
  ],
};
const ACTIONS_OUT = path.resolve(process.env.BB_PAGES_ACTIONS_CAPTURE_DIR || process.env.PAGES_MODERN_ACTIONS_OUTPUT_DIR
  || path.resolve(root, '..', 'outputs/pages-modern/actions'));

function installActionChartProbe() {
  window.__bbQaCharts ||= [];
}

function parseRequestedGroups(value = 'all') {
  const requested = String(value).split(',').map(item => item.trim()).filter(Boolean);
  if (requested.includes('all')) return [...ALL_GROUPS];
  const invalid = requested.filter(item => !Object.hasOwn(GROUP_MODULES, item));
  assert.deepEqual(invalid, [], `unknown debug group(s): ${invalid.join(', ')}`);
  assert.ok(requested.length > 0, 'select at least one group or use "all"');
  return [...new Set(requested)];
}

function filesUnder(dir) {
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    const candidate = path.join(dir, entry.name);
    return entry.isDirectory() ? filesUnder(candidate) : [candidate];
  });
}

// Test-only fault injection in the served production asset; no product source
// seam is added. The compiled callback anchor must occur once in the dist tree.
function instrumentPresentationFault(distRoot) {
  // Select the render callback immediately before ModernPage's destructured
  // `page`/`presentationBoundary` component signature. Other deferred local
  // presenters (Filing, Journal, Mandato, chart sections) are intentionally
  // excluded so recovery tests fault the intended owner boundary only.
  const anchor = /function\s+([\w$]+)\s*\(\s*\{\s*render\s*:\s*([\w$]+)\s*\}\s*\)\s*\{\s*return\s+\2\s*\(\s*\)\s*;?\s*\}(?=function\s+[\w$]+\s*\(\s*\{[^}]*\bpage\s*:\s*[\w$]+\s*,\s*className\s*:\s*[\w$]+\s*,\s*presentationBoundary\s*:\s*[\w$]+\s*=\s*!0)/g;
  const callbackAnchor = /function\s+([\w$]+)\s*\(\s*\{\s*render\s*:\s*([\w$]+)\s*\}\s*\)\s*\{\s*return\s+\2\s*\(\s*\)\s*;?\s*\}/g;
  const chartFactoryAnchor = /function\s+([\w$]+)\(e,t\)\{return\s+([\w$]+)\(e,new\s+([\w$]+),\3\.yf\(t\)\)\}/g;
  const candidates = filesUnder(path.join(distRoot, 'assets')).filter(file => file.endsWith('.js'));
  const matches = [], callbackCounts = [];
  for (const file of candidates) {
    const source = fs.readFileSync(file, 'utf8');
    const count = [...source.matchAll(new RegExp(anchor.source, 'g'))].length;
    if (count) matches.push({ file, count,
      context: source.slice(source.search(anchor), source.search(anchor) + 420) });
    const callbacks = [...source.matchAll(new RegExp(callbackAnchor.source, 'g'))];
    if (callbacks.length) callbackCounts.push({ file, count: callbacks.length, names: callbacks.map(match => match[1]) });
  }
  const total = matches.reduce((sum, match) => sum + match.count, 0);
  assert.equal(total, 1, `expected exactly one compiled ModernPage render callback before its page/presentationBoundary signature; found ${total}: ${JSON.stringify(matches)}`);
  const targets = new Map(callbackCounts.map(item => [item.file, item.count]));
  const deferredAnchorCount = callbackCounts.reduce((sum, item) => sum + item.count, 0);
  assert.ok(deferredAnchorCount >= 1, 'expected at least one compiled render callback for presenter fault injection');
  const chartFactories = [];
  for (const file of candidates.filter(file => /^lightweight-charts\.production-.*\.js$/.test(path.basename(file)))) {
    const source = fs.readFileSync(file, 'utf8');
    const factories = [...source.matchAll(new RegExp(chartFactoryAnchor.source, 'g'))];
    chartFactories.push(...factories.map(match => ({ file, match: match[0], chart: match[1], factory: match[2], options: match[3] })));
  }
  assert.equal(chartFactories.length, 1, `expected exactly one compiled lightweight-charts createChart factory for logical-range instrumentation; found ${chartFactories.length}`);
  const chartFactory = chartFactories[0];
  const original = fs.readFileSync;
  const served = new Set(), chartServed = new Set();
  fs.readFileSync = function patchedReadFileSync(file, options) {
    const resolved = typeof file === 'string' ? path.resolve(file) : '';
    const contents = original.call(fs, file, options);
    if (!targets.has(resolved) && resolved !== chartFactory.file) return contents;
    const source = Buffer.isBuffer(contents) ? contents.toString('utf8') : String(contents);
    let transformed = source;
    if (targets.has(resolved)) {
      let replaced = 0;
      transformed = transformed.replace(callbackAnchor, (_match, name, renderArg) => {
        replaced++;
        return `function ${name}({render:${renderArg}}){const __bbPageModernNode=${renderArg}();if(window.__bbPageModernFaultOnce&&localStorage.getItem('bellomberg.interface-theme.v1')==='dark'&&window.__bbPageModernFaultMatches&&window.__bbPageModernFaultMatches(__bbPageModernNode,window.__bbPageModernFaultTarget)){window.__bbPageModernFaultAttempts=(window.__bbPageModernFaultAttempts||0)+1;if(!(window.__bbPageModernFaultTrips||0)){window.__bbPageModernFaultTrips=1;window.__bbPageModernFaultLastTarget=window.__bbPageModernFaultTarget}throw new Error("qa-controlled-presenter-fault")}return __bbPageModernNode}`;
      });
      assert.equal(replaced, targets.get(resolved), `presenter callback instrumentation anchor drifted in ${resolved}`);
      served.add(resolved);
    }
    if (resolved === chartFactory.file) {
      let replaced = 0;
      transformed = transformed.replace(chartFactoryAnchor, (_match, name, factory, options) => {
        replaced++;
        return `function ${name}(e,t){const __bbQaOptions=new ${options};const __bbQaResolvedOptions=${options}.yf(t);const __bbQaChart=${factory}(e,__bbQaOptions,__bbQaResolvedOptions);window.__bbQaCharts||(window.__bbQaCharts=[]);const id=window.__bbQaCharts.length;__bbQaChart.__bbQaId=id;__bbQaChart.__bbQaHost=e;__bbQaChart.__bbQaRemoved=false;__bbQaChart.__bbQaInitOptions=__bbQaResolvedOptions;const remove=__bbQaChart.remove.bind(__bbQaChart);__bbQaChart.remove=()=>{__bbQaChart.__bbQaRemoved=true;return remove()};window.__bbQaCharts.push(__bbQaChart);return __bbQaChart}`;
      });
      assert.equal(replaced, 1, `lightweight-charts createChart factory instrumentation anchor drifted in ${resolved}`);
      chartServed.add(resolved);
    }
    return Buffer.isBuffer(contents) ? Buffer.from(transformed) : transformed;
  };
  return { matches, anchorCount: total, deferredAnchorCount,
    deferredAnchors: callbackCounts.map(item => ({ file: path.relative(distRoot, item.file), count: item.count, names: item.names })),
    target: matches.map(item => path.relative(distRoot, item.file)),
    chartFactory: { file: path.relative(distRoot, chartFactory.file), source: chartFactory.match },
    wasServed: () => served.size === targets.size && chartServed.has(chartFactory.file),
    restore: () => { fs.readFileSync = original; } };
}

function run(options = {}) {
  const groups = parseRequestedGroups(options.groups || process.env.PAGES_MODERN_ACTIONS_GROUPS || 'all');
  const onlyScenario = options.onlyScenario || process.env.BB_PAGES_ACTIONS_ONLY_SCENARIO || null;
  assert.ok(!onlyScenario || /^[a-z0-9-]+:.+$/.test(onlyScenario), `invalid exact diagnostic scenario filter: ${onlyScenario}`);
  const graphicsMode = options.graphicsMode || process.env.BB_PAGES_ACTIONS_GL || 'hardware-disabled';
  assert.ok(['hardware-disabled', 'swiftshader', 'native'].includes(graphicsMode), `unsupported fixture graphics mode: ${graphicsMode}`);
  const distRoot = path.resolve(root, 'dist');
  assert.ok(fs.existsSync(path.join(distRoot, 'index.html')), 'coordinated production dist is required; this runner does not build it');
  const indexHtml = fs.readFileSync(path.join(distRoot, 'index.html'), 'utf8');
  const builtAssets = [...new Set([...indexHtml.matchAll(/(?:src|href)=["']([^"']+\/(?:assets|vendor)\/[^"']+)["']/g)]
    .map(match => match[1].replace(/^\//, '').split(/[?#]/)[0]))];
  const assetHashes = new Map(builtAssets.map(relative => {
    const absolute = path.resolve(distRoot, relative);
    assert.ok(absolute.startsWith(distRoot + path.sep) && fs.existsSync(absolute), `dist asset missing: ${relative}`);
    return [relative, crypto.createHash('sha256').update(fs.readFileSync(absolute)).digest('hex')];
  }));
  // lightweight-charts is a separately emitted vendor module, not a direct
  // index.html script; include its hash because chart-range assertions observe it.
  for (const absolute of filesUnder(path.join(distRoot, 'assets'))
    .filter(file => /^lightweight-charts\.production-.*\.js$/.test(path.basename(file)))) {
    const relative = path.relative(distRoot, absolute);
    if (!assetHashes.has(relative)) assetHashes.set(relative, crypto.createHash('sha256').update(fs.readFileSync(absolute)).digest('hex'));
  }
  const buildIdentity = { gitHead: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim(),
    indexHtmlSha256: crypto.createHash('sha256').update(indexHtml).digest('hex'),
    assets: [...assetHashes].map(([relative, sha256]) => ({ path: relative, sha256 })) };
  const instrumentation = groups.includes('recovery') || groups.includes('research')
    ? instrumentPresentationFault(distRoot)
    : { matches: [], anchorCount: 0, deferredAnchorCount: 0, deferredAnchors: [], target: [],
      wasServed: () => false, restore: () => {} };
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'bellomberg-pages-actions-'));
  const outputDirectory = path.resolve(options.outputDirectory || ACTIONS_OUT);
  fs.mkdirSync(outputDirectory, { recursive: true });
  const { server, requests, unexpectedGets, writes } = startFixture(root, { reads: actionData.reads, writes: actionData.writes });
  return new Promise((resolve, reject) => {
    server.listen(0, '127.0.0.1', async () => {
      const origin = `http://127.0.0.1:${server.address().port}`;
      const entry = path.join(temporary, 'electron-actions-entry.cjs');
      const handshakePath = path.join(temporary, 'handshake.json');
      // BB_PAGES_ACTIONS_THEME=dark runs every Nuova step with the Dark theme preference saved.
      const theme = process.env.BB_PAGES_ACTIONS_THEME === 'dark' ? 'dark' : 'light';
      const config = { temporary, origin, handshakePath, progressPath: path.join(temporary, 'progress.log'), outputDirectory, groups, graphicsMode, onlyScenario, buildIdentity, theme,
        diagnosticFilter: process.env.BB_PAGES_ACTIONS_RECOVERY_PERFORMANCE_ONLY === '1' ? 'recovery-performance-only' : null,
        modernPageAnchorCount: instrumentation.anchorCount, deferredPresenterAnchors: instrumentation.deferredAnchors,
        instrumentedChartFactory: instrumentation.chartFactory || null };
      fs.writeFileSync(entry, `
        const fs = require('node:fs');
        const path = require('node:path');
        const { app } = require('electron');
        const config = ${JSON.stringify(config)};
        app.setPath('userData', path.join(config.temporary, 'userdata'));
        if (config.graphicsMode === 'hardware-disabled') app.disableHardwareAcceleration();
        const handshake = { pid: process.pid, entry: __filename, userData: app.getPath('userData'), origin: config.origin };
        fs.writeFileSync(config.handshakePath, JSON.stringify(handshake), { mode: 0o600 });
        console.log(${JSON.stringify(HANDSHAKE)} + JSON.stringify(handshake));
        app.whenReady().then(() => require(${JSON.stringify(__filename)}).renderer(config))
          .catch(error => { console.error('PAGES_MODERN_ACTIONS_FIXTURE_ERROR ' + (error?.stack || error)); app.exit(1); });
      `, { mode: 0o600 });
      let child;
      let output = '';
      try {
        const env = { ...process.env };
        delete env.ELECTRON_RUN_AS_NODE;
        delete env.BELLOMBERG_BACKEND_DIR;
        delete env.BELLOMBERG_PYTHON;
        delete env.BELLOMBERG_DESKTOP_API_URL;
        const electronArgs = graphicsMode === 'swiftshader'
          ? ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', entry]
          : [entry];
        child = spawn(require('electron'), electronArgs, { cwd: temporary, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
        child.stdout.on('data', chunk => { output += chunk; });
        child.stderr.on('data', chunk => { output += chunk; });
        const timer = setTimeout(() => child.kill(), 900000);
        let code, signal;
        try {
          ({ code, signal } = await new Promise((done, fail) => {
            child.once('error', fail);
            child.once('close', (exitCode, closeSignal) => done({ code: exitCode, signal: closeSignal }));
          }));
        } finally { clearTimeout(timer); }
        const handshakeLine = output.split(/\r?\n/).find(line => line.startsWith(HANDSHAKE));
        assert.ok(handshakeLine && fs.existsSync(handshakePath), `isolated Electron entry failed to start (${temporary})\n${output.slice(-5000)}`);
        const handshake = JSON.parse(fs.readFileSync(handshakePath, 'utf8'));
        assert.equal(handshake.pid, child.pid);
        assert.equal(fs.realpathSync(handshake.entry), fs.realpathSync(entry));
        assert.equal(handshake.userData, path.join(temporary, 'userdata'));
        assert.equal(handshake.origin, origin);
        const resultLine = output.split(/\r?\n/).find(line => line.startsWith(MARK));
        assert.ok(resultLine, `Electron produced no functional result (exit ${code}, signal ${signal})\n${temporary}\n${output.slice(-10000)}`);
        const compactResult = JSON.parse(resultLine.slice(MARK.length));
        const resultPath = path.join(outputDirectory, 'summary.json');
        assert.ok(fs.existsSync(resultPath), `Electron exited without the full renderer report (${temporary})`);
        const result = JSON.parse(fs.readFileSync(resultPath, 'utf8'));
        assert.ok(Array.isArray(result.pages), 'renderer report must contain per-page results');
        assert.equal(result.scenarios.length, compactResult.scenarios, 'compact marker scenario count differs from full report');
        const network = { allowedOrigin: origin, blockedExternalRequests: result.blockedExternalRequests,
          externalWindowRequests: result.externalWindowRequests, unexpectedGets: [...new Set(unexpectedGets)],
          writes: writes.map(item => ({ ...item })), realBackendStarted: false, realLlmStarted: false };
        const summary = { ...result, capturedAt: new Date().toISOString(), artifactDirectory: outputDirectory,
          network, faultInstrumentation: { modernPageAnchorCount: instrumentation.anchorCount,
            deferredAnchorCount: instrumentation.deferredAnchorCount, deferredAnchors: instrumentation.deferredAnchors,
            targetAssets: instrumentation.target, chartFactory: instrumentation.chartFactory || null,
            assetServed: instrumentation.wasServed() } };
        fs.writeFileSync(path.join(outputDirectory, 'summary.json'), JSON.stringify(summary, null, 2), { mode: 0o600 });
        fs.writeFileSync(path.join(outputDirectory, 'requests.json'), JSON.stringify(requests, null, 2), { mode: 0o600 });
        fs.writeFileSync(path.join(outputDirectory, 'electron.log'), output, { mode: 0o600 });
        for (const page of summary.pages || []) {
          fs.writeFileSync(path.join(outputDirectory, `${page.id}.json`), JSON.stringify({ ...page,
            fixtureVsReal: { allHTTPFromFixture: network.unexpectedGets.length === 0 && network.blockedExternalRequests.length === 0,
              realBackendStarted: false, realLlmStarted: false } }, null, 2), { mode: 0o600 });
        }
        console.log(JSON.stringify({ ok: summary.ok, acceptanceReady: summary.acceptanceReady, groups: summary.groups,
          scenarios: summary.scenarios.length, failed: summary.failedScenarios.length,
          pages: summary.pages.length, missingSpecialists: summary.missingSpecialistModules, reports: outputDirectory,
          fixtureRequests: requests.length, writes: writes.length, externalBlocked: network.blockedExternalRequests.length }));
        if (code !== 0 || !summary.ok) reject(new Error(`functional checks did not pass (exit ${code})`));
        else resolve();
      } catch (error) {
        fs.writeFileSync(path.join(outputDirectory, 'failure.log'), output, { mode: 0o600 });
        if (fs.existsSync(path.join(temporary, 'progress.log')))
          fs.copyFileSync(path.join(temporary, 'progress.log'), path.join(outputDirectory, 'progress.log'));
        fs.writeFileSync(path.join(outputDirectory, 'failure-requests.json'), JSON.stringify({ requests, unexpectedGets, writes }, null, 2), { mode: 0o600 });
        reject(error);
      } finally {
        if (child && child.exitCode === null && child.signalCode === null) child.kill();
        server.closeAllConnections?.();
        server.close();
        instrumentation.restore();
      }
    });
  });
}

async function renderer(config) {
  const { app, BrowserWindow } = require('electron');
  process.env.BELLOMBERG_LAUNCH_ID = 'synthetic-pages-modern-actions';
  process.env.BELLOMBERG_DESKTOP_API_URL = config.origin;
  const report = { ok: false, pages: [], scenarios: [], failedScenarios: [], missingSpecialistModules: [],
    groups: config.groups || [...ALL_GROUPS], acceptanceReady: false, partialRun: false, captures: [],
    advisorRunNotTested: 'user-request',
    advisorRunNotTestedDetail: 'Run, cancel, and reset actions for the Consigliere were omitted at the user\'s request; no advisor write was authorized.',
    onlyScenario: config.onlyScenario || null,
    diagnosticFilter: config.diagnosticFilter || null,
    graphicsMode: config.graphicsMode || 'hardware-disabled',
    buildIdentity: config.buildIdentity || null,
    blockedExternalRequests: [], externalWindowRequests: [], downloads: [], consoleErrors: [], fixtureVsReal: {
      allHTTPFromFixture: true, realBackendStarted: false, realLlmStarted: false } };
  let window;
  let activePage = 'startup';
  const networkByRequest = new Map();
  const progress = (event, details = {}) => {
    const line = `${new Date().toISOString()} ${event}${Object.keys(details).length ? ` ${JSON.stringify(details)}` : ''}\n`;
    try { fs.appendFileSync(config.progressPath, line, { mode: 0o600 }); } catch { /* diagnostic only */ }
    console.log('PAGES_MODERN_ACTIONS_PROGRESS ' + line.trim());
  };
  const withTimeout = (promise, label, ms = 10000) => {
    let timer;
    return Promise.race([
      Promise.resolve(promise),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms); }),
    ]).finally(() => clearTimeout(timer));
  };
  const cdp = (method, params = {}) => withTimeout(window.webContents.debugger.sendCommand(method, params), `CDP ${method}`);
  const js = async (fn, ...args) => {
    try {
      return await withTimeout(window.webContents.executeJavaScript(`(${fn.toString()})(...${JSON.stringify(args)})`),
        'renderer executeJavaScript', 8000);
    } catch (error) {
      const source = String(fn).replace(/\s+/g, ' ').slice(0, 280);
      throw new Error(`renderer executeJavaScript failed for ${source}: ${error?.stack || error}`);
    }
  };
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  const waitFor = async (fn, label, timeout = 15000, ...args) => {
    const until = Date.now() + timeout;
    while (Date.now() < until) {
      const value = await js(fn, ...args);
      if (value) return value;
      await pause(40);
    }
    throw new Error(`timeout waiting for ${label} on ${await js(() => location.hash)}; DOM=${await js(() => document.querySelector('main')?.innerText?.slice(0, 1800) || '')}`);
  };
  const snapshot = async () => (await (await fetch(config.origin + '/__fixture')).json());
  const counts = async () => {
    const state = await snapshot();
    return state.requests.reduce((out, item) => {
      const key = `${item.method} ${item.route}`;
      out[key] = (out[key] || 0) + 1;
      return out;
    }, {});
  };
  const settleRequests = async (label = 'fixture request set', timeout = 5000) => {
    const until = Date.now() + timeout;
    let previous = null, steady = 0;
    while (Date.now() < until) {
      const current = await counts();
      if (JSON.stringify(current) === JSON.stringify(previous)) steady++;
      else steady = 0;
      if (steady >= 3) return current;
      previous = current;
      await pause(120);
    }
    throw new Error(`timed out waiting for ${label} to settle`);
  };
  const runtimeSnapshot = async () => js(() => window.__bbPagesRuntime?.snapshot() || null);
  const tickIntervals = async ids => js(async selected => window.__bbPagesRuntime?.tick(selected), ids);
  const debuggerAttached = () => window.webContents.debugger.isAttached();
  const attachDebugger = () => { if (!debuggerAttached()) window.webContents.debugger.attach('1.3'); };
  const nativeClick = async selector => {
    await js(sel => document.querySelector(sel)?.scrollIntoView({ behavior: 'instant', block: 'center', inline: 'nearest' }), selector);
    await pause(25);
    const target = await js(sel => {
      const el = document.querySelector(sel);
      if (!el) return { ok: false, reason: 'selector-missing', selector: sel };
      const rect = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      const x = rect.left + rect.width / 2, y = rect.top + rect.height / 2;
      const hit = document.elementFromPoint(x, y);
      const hitTest = !!hit && (hit === el || el.contains(hit));
      return { ok: rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none' && style.pointerEvents !== 'none' && hitTest,
        x, y, hitTest, disabled: !!el.disabled, tag: el.tagName, text: (el.innerText || el.getAttribute('aria-label') || '').trim().slice(0, 100) };
    }, selector);
    assert.ok(target.ok, `unreachable control ${selector}: ${JSON.stringify(target)}`);
    assert.equal(target.disabled, false, `control disabled: ${selector}`);
    attachDebugger();
    await window.webContents.debugger.sendCommand('Input.dispatchMouseEvent', { type: 'mousePressed', x: target.x, y: target.y, button: 'left', clickCount: 1 });
    await window.webContents.debugger.sendCommand('Input.dispatchMouseEvent', { type: 'mouseReleased', x: target.x, y: target.y, button: 'left', clickCount: 1 });
    await pause(50);
    return { selector, ...target, dispatched: 'CDP trusted mouse input' };
  };
  const chartGesture = async (selector, dx, dy, wheelDelta = null) => {
    await js(sel => document.querySelector(sel)?.scrollIntoView({ behavior: 'instant', block: 'center', inline: 'nearest' }), selector);
    await pause(30);
    const target = await js(sel => {
      const el = document.querySelector(sel);
      if (!el) return { ok: false, reason: 'selector-missing', selector: sel };
      const canvas = [...el.querySelectorAll('canvas')].filter(item => item.width > 80 && item.height > 60)
        .sort((a, b) => b.width * b.height - a.width * a.height)[0];
      const surface = canvas || el;
      const r = surface.getBoundingClientRect(), x = r.left + r.width * .55, y = r.top + r.height * .52;
      const hit = document.elementFromPoint(x, y);
      return { ok: r.width > 80 && r.height > 60 && !!hit && el.contains(hit), x, y,
        width: r.width, height: r.height, hit: hit?.tagName || null, target: canvas ? 'largest chart canvas' : 'selected chart panel' };
    }, selector);
    assert.ok(target.ok, `unreachable chart surface ${selector}: ${JSON.stringify(target)}`);
    attachDebugger();
    if (wheelDelta != null) {
      await cdp('Input.dispatchMouseEvent', { type: 'mouseMoved', x: target.x, y: target.y, modifiers: 0 });
      await js(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      await cdp('Input.dispatchMouseEvent', { type: 'mouseWheel', x: target.x, y: target.y,
        deltaX: Number(dx) || 0, deltaY: Number(wheelDelta), modifiers: 0 });
      await pause(80);
      return { selector, width: target.width, height: target.height, deltaX: Number(dx) || 0,
        deltaY: Number(wheelDelta), dispatched: 'CDP trusted mouse wheel' };
    }
    await cdp('Input.dispatchMouseEvent', { type: 'mouseMoved', x: target.x, y: target.y, modifiers: 0 });
    await pause(60);
    const startX = target.x - 20, startY = target.y;
    const endX = target.x + Number(dx), endY = target.y + Number(dy);
    await cdp('Input.dispatchMouseEvent', { type: 'mousePressed', x: startX, y: startY,
      button: 'left', buttons: 1, clickCount: 1 });
    await pause(50);
    // lightweight-charts first records the drag origin, then initializes its
    // scroll timer on the next move. Further held moves are needed to produce
    // a time-scale pan, so send several trusted segments and allow animation
    // frames between them instead of releasing after a single move.
    for (let segment = 1; segment <= 6; segment++) {
      const progress = segment / 6;
      await cdp('Input.dispatchMouseEvent', { type: 'mouseMoved',
        x: startX + (endX - startX) * progress, y: startY + (endY - startY) * progress,
        button: 'left', buttons: 1, modifiers: 0 });
      await js(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      await pause(20);
    }
    await cdp('Input.dispatchMouseEvent', { type: 'mouseReleased', x: endX,
      y: endY, button: 'left', buttons: 0, clickCount: 1, modifiers: 0 });
    await pause(80);
    return { selector, width: target.width, height: target.height, deltaX: Number(dx),
      deltaY: Number(dy), dispatched: 'CDP trusted mouse drag' };
  };
  const key = async (keyName, modifiers = []) => {
    const bits = { Alt: 1, Control: 2, Meta: 4, Shift: 8 };
    const modifier = modifiers.reduce((mask, item) => mask | (bits[item] || 0), 0);
    const keyCodes = { Escape: 27, Tab: 9, Enter: 13, ArrowDown: 40, ArrowUp: 38, ArrowLeft: 37, ArrowRight: 39, Space: 32 };
    attachDebugger();
    const before = await js(() => ({ hasFocus: document.hasFocus(), active: document.activeElement?.tagName || null,
      id: document.activeElement?.id || '', text: document.activeElement?.textContent?.trim().slice(0, 60) || '' }));
    // CDP dispatches the key into this webContents, but the page should still
    // own native document focus so Tab/Enter/Space exercise browser defaults.
    window.webContents.focus();
    await pause(20);
    const after = await js(() => ({ hasFocus: document.hasFocus(), active: document.activeElement?.tagName || null,
      id: document.activeElement?.id || '', text: document.activeElement?.textContent?.trim().slice(0, 60) || '' }));
    assert.ok(after.hasFocus, `keyboard input requires focused renderer document (before=${JSON.stringify(before)}, after=${JSON.stringify(after)})`);
    const code = keyName === 'Escape' ? 'Escape' : keyName === 'Tab' ? 'Tab' : keyName === 'Enter' ? 'Enter' : keyName;
    const virtualCode = keyCodes[keyName] || 0;
    await window.webContents.debugger.sendCommand('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: keyName, code,
      modifiers: modifier, windowsVirtualKeyCode: virtualCode, nativeVirtualKeyCode: virtualCode });
    const text = keyName === 'Enter' ? '\r' : keyName === 'Space' ? ' ' : '';
    if (text) await window.webContents.debugger.sendCommand('Input.dispatchKeyEvent', { type: 'char', key: keyName, code,
      text, unmodifiedText: text, modifiers: modifier, windowsVirtualKeyCode: virtualCode, nativeVirtualKeyCode: virtualCode });
    await window.webContents.debugger.sendCommand('Input.dispatchKeyEvent', { type: 'keyUp', key: keyName, code, modifiers: modifier, windowsVirtualKeyCode: keyCodes[keyName] || 0 });
    await pause(45);
    return { key: keyName, modifiers, documentFocused: after.hasFocus,
      activeBefore: before, activeAfterFocus: after, sequence: text ? ['rawKeyDown', 'char', 'keyUp'] : ['rawKeyDown', 'keyUp'] };
  };
  const setValue = async (selector, value) => {
    const result = await js((sel, next) => {
      const el = document.querySelector(sel);
      if (!el) return { ok: false, reason: 'selector-missing' };
      const prototype = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
      const descriptor = Object.getOwnPropertyDescriptor(prototype, 'value');
      descriptor.set.call(el, next);
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      return { ok: true, value: el.value, tag: el.tagName };
    }, selector, String(value));
    assert.ok(result.ok, `could not set ${selector}`);
    await waitFor((sel, expected) => document.querySelector(sel)?.value === expected, `controlled value ${selector}`, 2500, selector, String(value));
    return result;
  };
  const typeText = async (selector, value, expected = String(value)) => {
    await nativeClick(selector);
    const prepared = await js(sel => {
      const el = document.querySelector(sel);
      if (!el || !('select' in el)) return false;
      el.focus(); el.select();
      return document.activeElement === el;
    }, selector);
    assert.ok(prepared, `could not focus/select controlled text input ${selector}`);
    attachDebugger();
    await window.webContents.debugger.sendCommand('Input.insertText', { text: String(value) });
    const upperExpected = String(expected);
    await waitFor((sel, target) => document.querySelector(sel)?.value === target,
      `trusted text input ${selector}`, 2500, selector, upperExpected);
    return { selector, value: upperExpected, dispatched: 'CDP Input.insertText after trusted focus and select' };
  };
  const fixture = async payload => {
    const forbiddenWrites = Object.keys(payload?.setWrite || {}).filter(route =>
      /^\/consigliere(?:\/|$)/.test(route) || route === '/agents/live/reset');
    assert.deepEqual(forbiddenWrites, [], 'user-requested advisor run/cancel/reset fixture writes are prohibited');
    const response = await fetch(config.origin + '/__fixture', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(payload) });
    assert.equal(response.status, 200);
    return response.json();
  };
  const settleFiniteAnimations = async () => js(async () => {
    const pending = () => document.getAnimations({ subtree: true }).filter(animation => {
      const timing = animation.effect?.getComputedTiming();
      return animation.playState === 'running' && timing && timing.duration !== Infinity
        && timing.iterations !== Infinity && Number.isFinite(Number(timing.endTime));
    });
    const initial = pending();
    if (!initial.length) return { runningAtStart: 0, runningAtEnd: 0, timedOut: false };
    let timer, timedOut = false;
    await Promise.race([
      Promise.all(initial.map(animation => animation.finished.catch(() => undefined))),
      new Promise(resolve => { timer = setTimeout(() => { timedOut = true; resolve(); }, 2500); }),
    ]);
    clearTimeout(timer);
    return { runningAtStart: initial.length, runningAtEnd: pending().length, timedOut };
  });
  const capture = async (name, options = {}) => {
    const defaultViewports = ['1920x1080', '2560x1440', '3440x1440', '5120x1440', '1440x1000', '1280x900', '900x700'];
    const allowed = new Set([...defaultViewports, '768x1024', '430x932', '375x812']);
    const rawViewports = options.viewports || defaultViewports;
    assert.ok(Array.isArray(rawViewports) && rawViewports.length > 0, 'capture requires at least one viewport');
    const viewports = rawViewports.map(item => Array.isArray(item) ? `${Number(item[0])}x${Number(item[1])}` : String(item));
    assert.deepEqual(viewports.filter(item => !allowed.has(item)), [], 'capture requested an unsupported viewport');
    const safeName = String(name).replace(/[^a-zA-Z0-9._-]+/g, '-').replace(/^-|-$/g, '').slice(0, 80);
    assert.ok(safeName, 'capture name must be non-empty');
    const originalBounds = window.getBounds();
    const originalViewport = await js(() => ({ width: innerWidth, height: innerHeight }));
    const beforeCounts = await counts();
    const captures = [];
    const svgBoundsFailures = [];
    const volAxisTitleFailures = [];
    const agentsDialFailures = [];
    try {
      for (const viewport of viewports) {
        const [width, height] = viewport.split('x').map(Number);
        const captureScrollSelector = options.scrollSelector || (options.verifyAgentsDial
          ? 'main [data-page="agents"] .ag-tavolo' : null);
        // This BrowserWindow uses useContentSize:true, so set the renderer's
        // requested viewport directly instead of treating it as outer bounds.
        window.setContentSize(width, height);
        await waitFor((w, h) => innerWidth === w && innerHeight === h, `capture viewport ${viewport}`, 5000, width, height);
        const animationSettle = await settleFiniteAnimations();
        await pause(80);
        const captureTarget = captureScrollSelector ? await js(sel => {
          const el = document.querySelector(sel);
          if (!el) return { selector: sel, found: false, visible: false };
          el.scrollIntoView({ behavior: 'instant', block: 'center', inline: 'nearest' });
          const rect = el.getBoundingClientRect(), style = getComputedStyle(el);
          const visible = rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden'
            && rect.bottom > 0 && rect.right > 0 && rect.top < innerHeight && rect.left < innerWidth;
          return { selector: sel, found: true, visible, rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
            viewport: { width: innerWidth, height: innerHeight } };
        }, captureScrollSelector) : null;
        if (captureScrollSelector) assert.ok(captureTarget?.visible,
          `capture ${safeName} target is not visible at ${viewport}: ${JSON.stringify(captureTarget)}`);
        if (captureTarget) await pause(80);
        const svgTextBounds = options.svgTextBoundsSelectors?.length ? await js((selectors, requiredTexts) => {
          const entries = new Map();
          for (const selector of selectors) {
            for (const svg of document.querySelectorAll(selector)) {
              if (svg.tagName.toLowerCase() !== 'svg') continue;
              const entry = entries.get(svg) || { selectors: [], svg, outside: [], checked: 0 };
              entry.selectors.push(selector); entries.set(svg, entry);
            }
          }
          const summaries = [...entries.values()].map(entry => {
            const svg = entry.svg, view = svg.viewBox?.baseVal;
            const screen = svg.getBoundingClientRect();
            const viewBox = view && view.width > 0 && view.height > 0
              ? { x: view.x, y: view.y, width: view.width, height: view.height }
              : { x: 0, y: 0, width: screen.width, height: screen.height };
            const inverse = svg.getScreenCTM()?.inverse();
            if (!inverse || !viewBox.width || !viewBox.height) {
              entry.outside.push({ error: 'SVG has no measurable screen transform or viewport', viewBox });
              return { selectors: entry.selectors, className: String(svg.getAttribute('class') || ''), viewBox,
                checked: 0, texts: [], outside: entry.outside };
            }
            const texts = [...svg.querySelectorAll('text')].filter(text => {
              const style = getComputedStyle(text);
              return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || 1) > 0
                && (text.textContent || '').trim();
            });
            const outside = [], measuredTexts = [];
            for (const text of texts) {
              let bounds;
              try { bounds = text.getBBox(); } catch { continue; }
              const matrix = inverse.multiply(text.getScreenCTM());
              const corners = [[bounds.x, bounds.y], [bounds.x + bounds.width, bounds.y],
                [bounds.x, bounds.y + bounds.height], [bounds.x + bounds.width, bounds.y + bounds.height]]
                .map(([x, y]) => ({ x: matrix.a * x + matrix.c * y + matrix.e, y: matrix.b * x + matrix.d * y + matrix.f }));
              const rect = { left: Math.min(...corners.map(point => point.x)), top: Math.min(...corners.map(point => point.y)),
                right: Math.max(...corners.map(point => point.x)), bottom: Math.max(...corners.map(point => point.y)) };
              entry.checked++;
              measuredTexts.push({ text: (text.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 100), rect });
              if (rect.left < viewBox.x - 1 || rect.top < viewBox.y - 1
                || rect.right > viewBox.x + viewBox.width + 1 || rect.bottom > viewBox.y + viewBox.height + 1) {
                outside.push({ text: (text.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 100), rect });
              }
            }
            entry.outside.push(...outside);
            return { selectors: entry.selectors, className: String(svg.getAttribute('class') || ''), viewBox,
              checked: entry.checked, texts: measuredTexts, outside };
          });
          const required = (requiredTexts || []).map(requirement => {
            const host = document.querySelector(requirement.selector);
            const normalized = String(requirement.text || '').trim().replace(/\s+/g, ' ').toLowerCase();
            const textSelector = host?.tagName.toLowerCase() === 'svg' ? 'text' : 'svg text';
            const node = host && [...host.querySelectorAll(textSelector)].find(text =>
              (text.textContent || '').trim().replace(/\s+/g, ' ').toLowerCase().includes(normalized));
            return { selector: requirement.selector, text: requirement.text, found: !!node,
              renderedText: node ? (node.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 100) : null };
          });
          return { ok: summaries.length > 0 && summaries.every(summary => summary.outside.length === 0)
              && summaries.some(summary => summary.checked > 0) && required.every(item => item.found),
            selectorCount: selectors.length, summaries, required };
        }, options.svgTextBoundsSelectors, options.requiredSvgTexts || []) : null;
        if (options.svgTextBoundsSelectors?.length && !svgTextBounds?.ok) {
          svgBoundsFailures.push({ viewport, evidence: svgTextBounds });
          if (!options.captureBeforeSvgBoundsFailure) assert.ok(false,
            `capture ${safeName} has SVG text outside its viewBox or required labels missing at ${viewport}: ${JSON.stringify(svgTextBounds)}`);
        }
        const volWorkbenchContrast = options.verifyVolWorkbenchContrast ? await js(requireChainContrast => {
          const page = document.querySelector('main [data-page="vol"]');
          const parse = color => {
            const values = String(color || '').match(/[\d.]+/g)?.map(Number) || [];
            if (values.length < 3) return null;
            return values.slice(0, 3).map(value => Math.max(0, Math.min(255, value)));
          };
          const luminance = color => {
            const channels = parse(color); if (!channels) return null;
            const linear = channels.map(value => { const s = value / 255; return s <= .04045 ? s / 12.92 : ((s + .055) / 1.055) ** 2.4; });
            return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2];
          };
          // Dark Nuova: chain/inspector surfaces must be dark instead of light.
          const darkTheme = document.documentElement.getAttribute('data-bb-theme') === 'dark';
          const surfaceOk = color => darkTheme ? luminance(color) < .2 : luminance(color) > .8;
          const contrast = (foreground, background) => {
            const a = luminance(foreground), b = luminance(background);
            return a == null || b == null ? null : (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
          };
          const visible = element => {
            const style = getComputedStyle(element), rect = element.getBoundingClientRect();
            return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
          };
          const chain = page?.querySelector('.vd-chain:not([hidden])');
          const table = chain?.querySelector('table');
          const headerCells = [...(table?.querySelectorAll('thead th') || [])].filter(visible);
          const strikeCells = [...(table?.querySelectorAll('tbody tr:first-child > th[scope="row"]') || [])].filter(visible);
          const cellResult = elements => elements.map(element => {
            const style = getComputedStyle(element), textElement = element.querySelector('button') || element;
            const foreground = getComputedStyle(textElement).color, background = style.backgroundColor;
            return { text: (element.innerText || element.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 80),
              foreground, background, ratio: contrast(foreground, background) };
          });
          const headers = cellResult(headerCells), strikes = cellResult(strikeCells);
          const inspector = page?.querySelector('.vd-contract-inspector');
          const inspectorStyle = inspector ? getComputedStyle(inspector) : null;
          const inspectorColor = inspector ? getComputedStyle(inspector.querySelector('h3') || inspector).color : null;
          const inspectorRatio = inspectorStyle ? contrast(inspectorColor, inspectorStyle.backgroundColor) : null;
          const workbench = page?.querySelector('.vol-workbench');
          const legHeads = [...(workbench?.querySelectorAll('.vd-leg-head') || [])].filter(visible).map(element => {
            const style = getComputedStyle(element);
            return { text: (element.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 60),
              color: style.color, background: style.backgroundColor, ratio: contrast(style.color, style.backgroundColor) };
          });
          // Light keeps the historical white reference; the dark Nuova uses the
          // chart's first opaque ancestor background.
          const chartSurface = element => {
            if (!darkTheme) return 'rgb(255, 255, 255)';
            for (let node = element; node; node = node.parentElement) {
              const bg = getComputedStyle(node).backgroundColor;
              const alpha = (String(bg).match(/[\d.]+/g) || [])[3];
              if (bg && bg !== 'transparent' && (alpha === undefined || Number(alpha) > .9)) return bg;
            }
            return 'rgb(14, 21, 34)';
          };
          const chartText = [...(workbench?.querySelectorAll('.vd-chart svg text') || [])].filter(visible).map(element => ({
            text: (element.textContent || '').trim().slice(0, 60), color: getComputedStyle(element).fill,
            ratio: contrast(getComputedStyle(element).fill, chartSurface(element)),
          }));
          const readout = workbench?.querySelector('.vd-chart-reading');
          const readoutStyle = readout ? getComputedStyle(readout) : null;
          const readoutRatio = readoutStyle ? contrast(getComputedStyle(readout.querySelector('b') || readout).color,
            readoutStyle.backgroundColor) : null;
          const heatmapHeaders = [...(workbench?.querySelectorAll('.vd-heatmap thead th') || [])].filter(visible).map(element => {
            const style = getComputedStyle(element);
            return { text: (element.innerText || '').trim(), color: style.color, background: style.backgroundColor,
              ratio: contrast(style.color, style.backgroundColor) };
          });
          const legTextOk = legHeads.length === 0 || legHeads.every(item => item.ratio != null && item.ratio >= 4.5);
          const chartTextOk = chartText.length === 0 || chartText.every(item => item.ratio != null && item.ratio >= 4.5);
          const readoutOk = !readout || (readoutRatio != null && readoutRatio >= 4.5);
          const inspectorOk = !inspector || (inspectorRatio != null && inspectorRatio >= 4.5
            && surfaceOk(inspectorStyle.backgroundColor));
          const chainEvidencePresent = !!chain && headers.length > 0 && strikes.length > 0;
          const ok = (!requireChainContrast || chainEvidencePresent)
            && (headers.length === 0 || headers.every(item => item.ratio != null && item.ratio >= 4.5
            && surfaceOk(item.background)))
            && (strikes.length === 0 || strikes.every(item => item.ratio != null && item.ratio >= 4.5
              && surfaceOk(item.background)))
            && (heatmapHeaders.length === 0 || heatmapHeaders.every(item => item.ratio != null && item.ratio >= 4.5
              && surfaceOk(item.background)))
            && legTextOk && chartTextOk && readoutOk && inspectorOk;
          return { ok, headers, strikes, heatmapHeaders, legHeads, chartText, readoutRatio, inspectorRatio,
            checks: { headers: headers.length > 0, strikes: strikes.length > 0, heatmapHeaders: heatmapHeaders.length > 0,
              chainEvidencePresent, legHeads: legHeads.length > 0, chartText: chartText.length > 0, readout: !!readout, inspector: !!inspector } };
        }, options.verifyVolChainContrast === true) : null;
        if (options.verifyVolWorkbenchContrast) assert.ok(volWorkbenchContrast?.ok,
          `capture ${safeName} has low contrast or dark residual Vol UI at ${viewport}: ${JSON.stringify(volWorkbenchContrast)}`);
        // 05/10/2026: the desks sit around the committee table. Same guarantees on the new surface:
        // every synthetic desk is named in full, no label or node overlaps another, text stays readable (≥ 11px, ≥ 4.5:1).
        const agentsDial = options.verifyAgentsDial ? await js(requiredDeskMarkers => {
          const root = document.querySelector('main [data-page="agents"] .bbn-agents');
          if (!root) return { ok: false, error: 'agents page not found' };
          const visible = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; };
          const cards = [...root.querySelectorAll('.ag-desk .ag-lbl')].filter(visible);
          const sections = [root.querySelector('.ag-run'), root.querySelector('.ag-tavolo'), root.querySelector('.ag-corsie'), root.querySelector('.ag-capo-c'),
            ...root.querySelectorAll('.ag-side > .bbn-card'), ...root.querySelectorAll('.ag-desk .ag-nodo'), ...cards].filter(el => el && visible(el));
          const box = el => { const r = el.getBoundingClientRect(); return { x: r.left, y: r.top, right: r.right, bottom: r.bottom }; };
          const overlapArea = (a, b) => Math.max(0, Math.min(a.right, b.right) - Math.max(a.x, b.x)) * Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.y, b.y));
          const overlaps = [];
          for (let i = 0; i < sections.length; i++) for (let j = i + 1; j < sections.length; j++) {
            if (sections[i].contains(sections[j]) || sections[j].contains(sections[i])) continue;
            const area = overlapArea(box(sections[i]), box(sections[j]));
            if (area > 2) overlaps.push({ a: sections[i].className, b: sections[j].className, area });
          }
          const rgba = c => { const v = String(c).match(/[\d.]+/g)?.map(Number) || [0, 0, 0]; return [v[0], v[1], v[2], v.length > 3 ? v[3] : 1]; };
          const over = (top, under) => top.slice(0, 3).map((ch, i) => ch * top[3] + under[i] * (1 - top[3]));
          const lum = rgb => rgb.map(ch => { const c = ch / 255; return c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4; })
            .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
          const ground = el => { const stack = []; for (let n = el; n; n = n.parentElement) { const bg = rgba(getComputedStyle(n).backgroundColor); if (bg[3] > 0) stack.push(bg); if (bg[3] >= 1) break; }
            return stack.reverse().reduce((under, top) => over(top, under), [255, 255, 255]); };
          const texts = cards.flatMap(card => [...card.querySelectorAll('.nm b, .bbn-pill, .ag-doing')]).filter(visible).map(el => {
            const st = getComputedStyle(el), fg = rgba(st.color), bg = ground(el), color = over(fg, bg);
            const L1 = lum(color), L2 = lum(bg);
            return { text: (el.textContent || '').trim().slice(0, 60), size: Number.parseFloat(st.fontSize),
              contrast: (Math.max(L1, L2) + .05) / (Math.min(L1, L2) + .05) };
          });
          const names = cards.map(card => { const el = card.querySelector('.nm b');
            return { text: (el?.textContent || '').trim(), clipped: !el || el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1 }; });
          const missingDeskMarkers = (requiredDeskMarkers || []).filter(marker => !names.some(name => name.text.includes(marker) && !name.clipped));
          const outside = sections.filter(el => { const r = el.getBoundingClientRect(); return r.left < -1 || r.right > innerWidth + 1; }).length;
          const evidence = { cardCount: cards.length, sectionCount: sections.length, plateCount: cards.length,
            platePlateOverlapCount: overlaps.length, textPlateOverlapCount: 0, plateHudOverlapCount: 0, overlaps,
            names, missingDeskMarkers, clippedNames: names.filter(name => name.clipped).length, outside,
            minTextSize: texts.length ? Math.min(...texts.map(t => t.size)) : null,
            minTextContrast: texts.length ? Math.min(...texts.map(t => t.contrast)) : null,
            lowContrast: texts.filter(t => t.contrast < 4.5).slice(0, 8) };
          evidence.ok = cards.length > 0 && overlaps.length === 0 && missingDeskMarkers.length === 0 && evidence.clippedNames === 0
            && outside === 0 && evidence.minTextSize >= 11 && evidence.minTextContrast >= 4.5;
          return evidence;
        }, options.requiredDeskMarkers || []) : null;
        if (options.verifyAgentsDial && !agentsDial?.ok) agentsDialFailures.push({ viewport, evidence: agentsDial });
        const volAxisTitle = options.verifyVolAxisTitle ? await js(() => {
          const graph = document.querySelector('main [data-page="vol"] [data-vol-3d]');
          const title = document.querySelector('main [data-page="vol"] [data-vol-axis-title="expiry"]');
          const card = graph?.closest('.p3');
          const rect = element => {
            if (!element) return null;
            const box = element.getBoundingClientRect();
            return { x: box.x, y: box.y, left: box.left, top: box.top, right: box.right, bottom: box.bottom,
              width: box.width, height: box.height };
          };
          const graphRect = rect(graph), titleRect = rect(title), cardRect = rect(card);
          const style = title ? getComputedStyle(title) : null;
          const parse = color => String(color || '').match(/[\d.]+/g)?.map(Number).slice(0, 3) || [];
          const luminance = color => {
            const values = parse(color); if (values.length < 3) return null;
            const channels = values.map(value => { const srgb = value / 255; return srgb <= .04045 ? srgb / 12.92 : ((srgb + .055) / 1.055) ** 2.4; });
            return .2126 * channels[0] + .7152 * channels[1] + .0722 * channels[2];
          };
          const fg = luminance(style?.color), bg = luminance(card ? getComputedStyle(card).backgroundColor : null);
          const contrast = fg == null || bg == null ? null : (Math.max(fg, bg) + .05) / (Math.min(fg, bg) + .05);
          const text = (title?.textContent || '').trim().replace(/\s+/g, ' ');
          const ariaDescribedBy = graph?.getAttribute('aria-describedby')?.split(/\s+/).includes(title?.id) || false;
          const visible = !!title && !!titleRect && titleRect.width > 0 && titleRect.height > 0
            && style?.display !== 'none' && style?.visibility !== 'hidden' && Number(style?.opacity || 1) > 0;
          const withinViewport = !!titleRect && titleRect.left >= 0 && titleRect.top >= 0
            && titleRect.right <= innerWidth && titleRect.bottom <= innerHeight;
          const withinCard = !!titleRect && !!cardRect && titleRect.left >= cardRect.left - 1
            && titleRect.right <= cardRect.right + 1 && titleRect.bottom <= cardRect.bottom + 1;
          const belowCanvas = !!graphRect && !!titleRect && titleRect.top >= graphRect.bottom - 1;
          const recognizedLabel = /(?:days\s+to\s+expiry|giorni\s+a\s+scadenza)/i.test(text);
          const fontSize = Number.parseFloat(style?.fontSize || '0');
          const evidence = { text, visible, withinViewport, withinCard, belowCanvas, recognizedLabel, fontSize, color: style?.color || null,
            background: card ? getComputedStyle(card).backgroundColor : null, contrast, ariaLabel: graph?.getAttribute('aria-label') || null,
            ariaDescribedBy, graphRect, titleRect, cardRect, viewport: { width: innerWidth, height: innerHeight } };
          evidence.ok = visible && withinViewport && withinCard && belowCanvas && recognizedLabel && fontSize >= 12
            && contrast != null && contrast >= 4.5 && !!evidence.ariaLabel && ariaDescribedBy;
          return evidence;
        }) : null;
        if (options.verifyVolAxisTitle && !volAxisTitle?.ok) volAxisTitleFailures.push({ viewport, evidence: volAxisTitle });
        const metadata = await js(() => {
          const root = document.querySelector('main [data-page]');
          const visible = el => {
            const rect = el.getBoundingClientRect(), style = getComputedStyle(el);
            return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
          };
          const dialogs = [...new Set(document.querySelectorAll('[role="dialog"],dialog[open],.f11v,.f11-ask'))]
            .filter(visible).map(el => ({ tag: el.tagName.toLowerCase(), id: el.id || '', className: String(el.className || ''),
              role: el.getAttribute('role'), label: el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || '',
              text: (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 240) }));
          const settingsDialogs = dialogs.filter(dialog => /(?:^|\s)f11v(?:\s|$)|(?:^|\s)f11-ask(?:\s|$)/.test(dialog.className));
          const doc = document.documentElement, body = document.body;
          const overflowNodes = [...document.querySelectorAll('main *')].map(el => {
            const r = el.getBoundingClientRect(), s = getComputedStyle(el);
            return { tag: el.tagName.toLowerCase(), id: el.id || '', className: String(el.className || '').slice(0, 80),
              text: (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 70), scrollWidth: el.scrollWidth, clientWidth: el.clientWidth,
              right: Math.round(r.right), overflowX: s.overflowX };
          }).filter(el => el.scrollWidth > el.clientWidth + 2 && ['auto', 'scroll', 'hidden', 'clip'].includes(el.overflowX))
            .sort((a, b) => (b.scrollWidth - b.clientWidth) - (a.scrollWidth - a.clientWidth)).slice(0, 8);
          return { viewport: { innerWidth, innerHeight, devicePixelRatio }, route: location.hash,
            mode: document.querySelector('[data-theme-choice="dark"]')?.getAttribute('aria-pressed') === 'true' ? 'modern' : 'classic',
            rootPage: root?.dataset.page || null, rootClass: String(root?.className || ''),
            activeDialogs: dialogs, settingsDialogs,
            visibleSurface: dialogs.length ? { type: 'dialog', labels: dialogs.map(dialog => dialog.label || dialog.text.slice(0, 80)) }
              : { type: 'page', page: root?.dataset.page || null },
            focus: document.activeElement ? { tag: document.activeElement.tagName, id: document.activeElement.id || '',
              text: (document.activeElement.innerText || document.activeElement.getAttribute('aria-label') || '').trim().slice(0, 100) } : null,
            documentOverflow: { scrollWidth: Math.max(doc.scrollWidth, body.scrollWidth), clientWidth: doc.clientWidth,
              scrollHeight: Math.max(doc.scrollHeight, body.scrollHeight), clientHeight: doc.clientHeight }, overflowNodes };
        });
        assert.ok(!metadata.settingsDialogs.length || /settings|config|backup/i.test(safeName),
          `capture ${safeName} refused because a Settings/backup modal obscures the page: ${JSON.stringify(metadata.settingsDialogs)}`);
        const image = await window.webContents.capturePage();
        const filename = `${safeName}-${viewport}.png`;
        const destination = path.join(config.outputDirectory, 'captures', filename);
        fs.mkdirSync(path.dirname(destination), { recursive: true });
        fs.writeFileSync(destination, image.toPNG(), { mode: 0o600 });
        captures.push({ name: safeName, viewport, path: destination, animationSettle, captureTarget,
          svgTextBounds, volAxisTitle, volWorkbenchContrast, agentsDial, ...metadata });
      }
      if (svgBoundsFailures.length && options.captureBeforeSvgBoundsFailure) {
        const diagnosticPath = path.join(config.outputDirectory, 'diagnostics', `${safeName}-svg-bounds.json`);
        fs.mkdirSync(path.dirname(diagnosticPath), { recursive: true });
        fs.writeFileSync(diagnosticPath, JSON.stringify({ name: safeName, failures: svgBoundsFailures,
          captures: captures.map(({ path: capturePath, viewport, svgTextBounds, ...metadata }) => ({
            path: capturePath, viewport, svgTextBounds, ...metadata,
          })) }, null, 2), { mode: 0o600 });
        if (!options.deferSvgBoundsFailure) assert.ok(false,
          `capture ${safeName} collected diagnostic evidence but failed SVG bounds/title checks: `
          + JSON.stringify(svgBoundsFailures.map(item => ({ viewport: item.viewport, evidence: item.evidence }))));
      }
      if (volAxisTitleFailures.length) {
        const diagnosticPath = path.join(config.outputDirectory, 'diagnostics', `${safeName}-vol-axis-title.json`);
        fs.mkdirSync(path.dirname(diagnosticPath), { recursive: true });
        fs.writeFileSync(diagnosticPath, JSON.stringify({ name: safeName, failures: volAxisTitleFailures,
          captures: captures.map(({ path: capturePath, viewport, volAxisTitle }) => ({ path: capturePath, viewport, volAxisTitle })) }, null, 2), { mode: 0o600 });
        if (!options.deferVolAxisTitleFailure) assert.ok(false,
          `capture ${safeName} has a missing, clipped, or low-contrast HTML Vol axis title: ${JSON.stringify(volAxisTitleFailures)}`);
      }
      if (agentsDialFailures.length) {
        const diagnosticPath = path.join(config.outputDirectory, 'diagnostics', `${safeName}-agents-dial.json`);
        fs.mkdirSync(path.dirname(diagnosticPath), { recursive: true });
        fs.writeFileSync(diagnosticPath, JSON.stringify({ name: safeName, failures: agentsDialFailures,
          captures: captures.map(({ path: capturePath, viewport, agentsDial }) => ({ path: capturePath, viewport, agentsDial })) }, null, 2), { mode: 0o600 });
        if (!options.deferAgentsDialFailure) assert.ok(false,
          `capture ${safeName} agent desk cards overlap, clip a desk name, or fail their readability limits: ${JSON.stringify(agentsDialFailures)}`);
      }
    } finally {
      window.setContentSize(originalViewport.width, originalViewport.height);
      await waitFor((w, h) => innerWidth === w && innerHeight === h, 'capture viewport restore', 5000, originalViewport.width, originalViewport.height);
      window.setPosition(originalBounds.x, originalBounds.y);
      await pause(100);
    }
    const requestDeltas = deltaCounts(beforeCounts, await counts());
    assert.deepEqual(requestDeltas, {}, `viewport capture/resize caused fixture requests: ${JSON.stringify(requestDeltas)}`);
    report.captures.push(...captures.map(({ name: captureName, path: capturePath, viewport, ...metadata }) => ({
      name: captureName, path: capturePath, viewport, ...metadata, resizeRequestDeltas: requestDeltas,
    })));
    return captures;
  };
  const withViewport = async (width, height, callback) => {
    assert.equal(typeof callback, 'function', 'withViewport requires a callback');
    const originalBounds = window.getBounds();
    const originalViewport = await js(() => ({ width: innerWidth, height: innerHeight }));
    try {
      window.setContentSize(Number(width), Number(height));
      await waitFor((w, h) => innerWidth === w && innerHeight === h, `viewport ${width}x${height}`, 5000,
        Number(width), Number(height));
      await pause(100);
      return await callback();
    } finally {
      window.setContentSize(originalViewport.width, originalViewport.height);
      await waitFor((w, h) => innerWidth === w && innerHeight === h, 'original viewport restore', 5000,
        originalViewport.width, originalViewport.height);
      window.setPosition(originalBounds.x, originalBounds.y);
      await pause(50);
    }
  };
  const pageState = async () => js(() => {
    try {
    window.__bbQaIds ||= new WeakMap(); window.__bbQaNextId ||= 0;
    const id = node => { if (!node) return null; if (!window.__bbQaIds.has(node)) window.__bbQaIds.set(node, ++window.__bbQaNextId); return window.__bbQaIds.get(node); };
    const root = document.querySelector('main [data-page]') || document.querySelector('main .relative')?.firstElementChild;
    const visible = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; };
    const controls = [...(root?.querySelectorAll('input,textarea,select') || [])].filter(visible).map(el => ({ id: el.id || '', name: el.getAttribute('aria-label') || el.name || el.placeholder || '', value: el.type === 'checkbox' || el.type === 'radio' ? el.checked : el.value }));
    // A highlighted option of an open combobox list is transient focus state, not a choice.
    const choices = [...(root?.querySelectorAll('[aria-selected="true"]:not([role="option"]),[aria-pressed="true"],[aria-checked="true"],button.on,.mr.on,.mhit.on,.tb.on,.vst .on,[role="tab"].on') || [])]
      .filter(el => visible(el) && !(root?.dataset.page === 'factors' && el.matches('[role="radio"],input[type="radio"]')))
      .map(el => ({ id: el.id || '', text: (el.innerText || el.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ').slice(0, 100) }));
    const addChoice = (key, el) => { if (el && visible(el)) choices.push({ id: key, text: (el.innerText || el.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ').slice(0, 100) }); };
    if (root?.dataset.page === 'factors') {
      const selected = root.querySelector('[data-strato="calibro"] [role="radio"][aria-checked="true"]');
      if (selected) choices.push({ id: 'factors-gauge-radio', text: (selected.innerText || selected.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ').slice(0, 100) });
    }
    if (root?.dataset.page === 'edge') {
      const threshold = [...root.querySelectorAll('[data-zona="comandi"] button')].find(el => /^≥\s*\d+/.test(el.innerText.trim()) && el.className.includes('bg-gold'));
      addChoice('edge-strength-threshold', threshold);
    }
    if (root?.dataset.page === 'decisions') {
      const filter = [...root.querySelectorAll('button')].find(el => ['ALL', 'PENDING', 'EXECUTED', 'PARTIAL', 'SKIPPED', 'EXPIRED'].includes(el.innerText.trim().toUpperCase()) && el.className.includes('bg-[#1c1305]'));
      addChoice('decisions-filter', filter);
    }
    return { id: id(root), page: root?.dataset.page || null, className: String(root?.className || ''), controls, choices };
    } catch (error) {
      return { pageStateError: String(error?.stack || error), page: location.hash };
    }
  });
  const record = (page, scenario, evidence = {}, scenarioStatus = 'passed') => {
    assert.equal(Object.hasOwn(evidence, 'status'), false,
      `${page}/${scenario}: status is reserved for the assertion runner; expose application/workflow status as domainStatus`);
    assert.ok(scenarioStatus === 'passed' || scenarioStatus === 'failed', `invalid harness status: ${scenarioStatus}`);
    const item = { assertionResults: evidence.assertionResults || {}, ...evidence, page, scenario, status: scenarioStatus };
    report.scenarios.push(item);
    if (item.status !== 'passed') report.failedScenarios.push(item);
    try {
      const summary = { page: item.page, scenario: item.scenario, status: item.status,
        error: item.error ? String(item.error).slice(0, 1800) : null,
        assertionResults: item.assertionResults || {} };
      fs.appendFileSync(path.join(config.outputDirectory, 'scenario-results.jsonl'), `${JSON.stringify(summary)}\n`, { mode: 0o600 });
      fs.writeFileSync(path.join(config.outputDirectory, 'summary.partial.json'), JSON.stringify({
        acceptanceReady: false, partialRun: true, groups: report.groups,
        scenarios: report.scenarios.length, passed: report.scenarios.filter(s => s.status === 'passed').length,
        failed: report.failedScenarios.length, latest: summary,
      }, null, 2) + '\n', { mode: 0o600 });
    } catch { /* partial progress output must not change scenario behavior */ }
    return item;
  };
  const executeScenario = async (page, scenario, run) => {
    if (config.onlyScenario && `${page}:${scenario}` !== config.onlyScenario)
      return { page, scenario, status: 'skipped', skippedByFilter: true };
    try { return record(page, scenario, await run(), 'passed'); }
    catch (error) {
      const domSnapshot = await js(() => ({ hash: location.hash, title: document.title,
        mainText: (document.querySelector('main')?.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 700),
        pageRoots: [...document.querySelectorAll('main [data-page]')].map(el => ({ page: el.getAttribute('data-page'), className: String(el.className || ''), text: (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 250) })),
        activeElement: document.activeElement ? { tag: document.activeElement.tagName, id: document.activeElement.id || '',
          ariaLabel: document.activeElement.getAttribute('aria-label') || '', text: (document.activeElement.innerText || document.activeElement.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 120) } : null,
        activeDialogs: [...new Set(document.querySelectorAll('[role="dialog"],dialog[open],.f11v,.f11-ask'))].filter(el => {
          const rect = el.getBoundingClientRect(), style = getComputedStyle(el);
          return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
        }).map(el => ({ tag: el.tagName.toLowerCase(), className: String(el.className || ''),
          role: el.getAttribute('role'), label: el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || '',
          text: (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 220) })),
        alerts: [...document.querySelectorAll('main [role="alert"],main .bb-interface-recovery')].map(el => (el.innerText || '').trim().slice(0, 300)),
        errorTraces: [...document.querySelectorAll('main details')].map(el => { el.open = true; return (el.innerText || '').trim().slice(0, 3500); }),
        mode: document.querySelector('[data-theme-choice="dark"]')?.getAttribute('aria-pressed'),
        apiResourceTiming: performance.getEntriesByType('resource').filter(entry => {
          try { return new URL(entry.name).origin === location.origin && !entry.name.includes('/__fixture'); } catch { return false; }
        }).slice(-50).map(entry => { const url = new URL(entry.name); return { url: url.pathname + url.search,
          startTime: entry.startTime, duration: entry.duration, responseEnd: entry.responseEnd,
          transferSize: entry.transferSize, initiatorType: entry.initiatorType }; })
      })).catch(diagnosticError => ({ unavailable: String(diagnosticError?.message || diagnosticError) }));
      const fixtureAtFailure = await snapshot().catch(diagnosticError => ({ unavailable: String(diagnosticError?.message || diagnosticError) }));
      const networkAtFailure = [...networkByRequest.values()].filter(item => item.url.startsWith(new URL(config.origin).origin))
        .slice(-100).map(item => ({ ...item }));
      const faultInstrumentation = await js(() => ({ target: window.__bbPageModernFaultTarget || null,
        trips: window.__bbPageModernFaultTrips || 0, attempts: window.__bbPageModernFaultAttempts || 0,
        armed: !!window.__bbPageModernFaultOnce, lastTarget: window.__bbPageModernFaultLastTarget || null }))
        .catch(() => null);
      const settingsCleanup = page === 'settings' ? q.lastSettingsCleanup || null : null;
      return record(page, scenario, { assertionResults: { [scenario]: false }, error: String(error?.stack || error), domSnapshot,
        faultInstrumentation, fixtureAtFailure, networkAtFailure,
        ...(settingsCleanup ? { settingsCleanup } : {}) }, 'failed');
    }
  };
  const q = { js, visit: async route => {
    activePage = route;
    const routePart = String(route).replace(/^\/+|\/+$/g, '').split('/')[0];
    const expectedPage = ROUTE_PAGE[routePart] || routePart;
    const readySelector = expectedPage === 'dashboard' ? 'main .bbn-dashboard' : `main [data-page="${expectedPage}"]`;
    await js(value => { location.hash = `#${value}`; }, route);
    await waitFor((value, page) => location.hash === '#' + value
      && !!document.querySelector(page)
      && !!document.querySelector('[data-testid="appearance-menu"]'), `route page root ${route}`, 20000, route, readySelector);
    await pause(320);
  }, waitFor, click: nativeClick, setValue, typeText, settleRequests, toggle: async mode => {
    // The Classica/Nuova switch was removed (2026-10-02). Scenarios still flip the
    // presentation round-trip, now through the only switch left: 'classic' stands
    // for Light and 'modern' for Dark (the presenter fault is armed in Dark and the
    // boundary recovers by returning to Light).
    const theme = { classic: 'light', modern: 'dark', light: 'light', dark: 'dark' }[mode];
    assert.ok(theme, `unknown presentation ${mode}`);
    if (await js(() => document.querySelector('.bb-interface-menu-toggle')?.getAttribute('aria-expanded') === 'false')) {
      await nativeClick('.bb-interface-menu-toggle');
    }
    const evidence = await nativeClick(`[data-theme-choice="${theme}"]`);
    await waitFor(value => document.querySelector(`[data-theme-choice="${value}"]`)?.getAttribute('aria-pressed') === 'true', `interface theme ${theme}`, 5000, theme);
    // Close the menu without moving focus (a programmatic click does not focus),
    // so it never covers the page and a dialog keeps the focus it restored.
    await js(() => { const toggle = document.querySelector('.bb-interface-menu-toggle');
      if (toggle?.getAttribute('aria-expanded') === 'true') toggle.click(); });
    return evidence;
  }, key, pause, fixture, snapshot, counts, runtimeSnapshot, tick: tickIntervals, pageState, capture, withViewport,
  lastSettingsCleanup: null,
  cleanupSettingsDialogs: async () => {
    const read = () => js(() => ({ confirmation: !!document.querySelector('.f11-ask[role="dialog"],.f11-ask'),
      settings: !!document.querySelector('.f11v[role="dialog"],.f11v') }));
    const before = await read(), dispatched = [];
    for (let attempt = 0; attempt < 3; attempt++) {
      const state = await read();
      if (!state.confirmation && !state.settings) break;
      try { dispatched.push({ attempt: attempt + 1, key: 'Escape', sent: await key('Escape') }); }
      catch (error) { dispatched.push({ attempt: attempt + 1, key: 'Escape', error: String(error?.message || error) }); }
      await pause(80);
    }
    return { before, after: await read(), dispatched };
  },
  drag: (selector, dx, dy = 0) => chartGesture(selector, dx, dy),
  wheel: (selector, deltaY, deltaX = 0) => chartGesture(selector, deltaX, 0, deltaY),
  armPresenterFault: async target => js(next => {
    window.__bbPageModernFaultTarget = next;
    window.__bbPageModernFaultTrips = 0;
    window.__bbPageModernFaultAttempts = 0;
    window.__bbPageModernFaultOnce = true;
    window.__bbPageModernFaultLastTarget = null;
    window.__bbPageModernFaultMatches = (node, wanted) => {
      // expandComponent: the presenter returns a hook-free view component (Filing: <VistaFiling d a />)
      // whose root element carries the class; call that one component once to read its root, never deeper.
      let expansions = wanted.expandComponent ? 1 : 0;
      const visit = value => {
        if (!value) return false;
        if (Array.isArray(value)) return value.some(visit);
        if (typeof value !== 'object') return false;
        const props = value.props || {};
        const classes = typeof props.className === 'string' ? props.className.split(/\s+/) : [];
        if (wanted.className && classes.includes(wanted.className)) return true;
        if (wanted.testId && props['data-testid'] === wanted.testId) return true;
        if (wanted.id && props.id === wanted.id) return true;
        if (expansions > 0 && typeof value.type === 'function' && !(value.type.prototype && value.type.prototype.isReactComponent)) {
          expansions--;
          let rendered = null;
          try { rendered = value.type(props); } catch { rendered = null; }
          if (visit(rendered)) return true;
        }
        return visit(props.children);
      };
      return visit(node);
    };
    return { target: next, armed: true };
  }, target), faultTrips: async () => js(() => ({ trips: window.__bbPageModernFaultTrips || 0,
    attempts: window.__bbPageModernFaultAttempts || 0,
    lastTarget: window.__bbPageModernFaultLastTarget || null, armed: !!window.__bbPageModernFaultOnce })),
  disarmPresenterFault: async () => js(() => { window.__bbPageModernFaultOnce = false; return { armed: false,
    trips: window.__bbPageModernFaultTrips || 0, attempts: window.__bbPageModernFaultAttempts || 0 }; }),
  scrollTo: async selector => {
    const result = await js(sel => {
      const el = document.querySelector(sel); if (!el) return { ok: false, selector: sel };
      el.scrollIntoView({ behavior: 'instant', block: 'center', inline: 'nearest' });
      const rect = el.getBoundingClientRect();
      return { ok: rect.width > 0 && rect.height > 0, selector: sel, x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height) };
    }, selector);
    assert.ok(result.ok, `could not scroll control into view: ${selector}`);
    await pause(60);
    return result;
  }, canvasState: async selector => js(sel => {
    const canvases = [...document.querySelectorAll(sel)].filter(canvas => canvas.width > 80 && canvas.height > 60)
      .sort((a, b) => b.width * b.height - a.width * a.height);
    if (!canvases.length) return { count: 0, digest: null, selector: sel };
    const canvas = canvases[0], encoded = canvas.toDataURL('image/png');
    let hash = 2166136261;
    for (let i = 0; i < encoded.length; i++) { hash ^= encoded.charCodeAt(i); hash = Math.imul(hash, 16777619); }
    return { count: canvases.length, width: canvas.width, height: canvas.height,
      digest: (hash >>> 0).toString(16), encodedLength: encoded.length, selector: sel };
  }, selector), chartState: async selector => js(sel => {
    const panel = document.querySelector(sel);
    if (!panel) return { selector: sel, chartCount: 0, chartId: null, range: null };
    const charts = (window.__bbQaCharts || []).filter(chart => !chart.__bbQaRemoved
      && chart.__bbQaHost instanceof Node && panel.contains(chart.__bbQaHost));
    const chart = charts.at(-1);
    const range = chart?.timeScale()?.getVisibleLogicalRange() || null;
    const canvas = [...panel.querySelectorAll('canvas')].filter(item => item.width > 80 && item.height > 60)
      .sort((a, b) => b.width * b.height - a.width * a.height)[0];
    const rect = canvas?.getBoundingClientRect();
    return { selector: sel, chartCount: charts.length, chartId: chart?.__bbQaId ?? null,
      range: range && Number.isFinite(range.from) && Number.isFinite(range.to) ? { from: range.from, to: range.to } : null,
      canvas: rect && { x: rect.x, y: rect.y, width: rect.width, height: rect.height } };
  }, selector), chartHostFor: async panelSelector => js(sel => {
    const panel = document.querySelector(sel);
    if (!panel) return { selector: sel, chartId: null, canvas: null };
    const charts = (window.__bbQaCharts || []).filter(chart => !chart.__bbQaRemoved
      && chart.__bbQaHost instanceof Node && panel.contains(chart.__bbQaHost));
    const ranked = charts.map(chart => {
      const canvases = [...chart.__bbQaHost.querySelectorAll('canvas')];
      const canvas = canvases.sort((a, b) => b.width * b.height - a.width * a.height)[0];
      return { chart, canvas, area: canvas ? canvas.width * canvas.height : 0 };
    }).sort((a, b) => b.area - a.area);
    const selected = ranked[0];
    if (!selected || !selected.area) return { selector: sel, chartId: null, canvas: null };
    selected.chart.__bbQaHost.setAttribute('data-qa-chart-host', String(selected.chart.__bbQaId));
    return { chartId: selected.chart.__bbQaId,
      selector: `main [data-page="performance"] [data-qa-chart-host="${selected.chart.__bbQaId}"]`,
      canvas: { width: selected.canvas.width, height: selected.canvas.height } };
  }, panelSelector), chartDiagnostics: async selector => js(sel => {
    const host = document.querySelector(sel);
    const chart = (window.__bbQaCharts || []).filter(item => !item.__bbQaRemoved
      && item.__bbQaHost instanceof Node && host?.contains(item.__bbQaHost)).at(-1);
    if (!chart) return { selector: sel, chartId: null };
    let options = null, timeOptions = null;
    try { options = typeof chart.options === 'function' ? chart.options() : chart.__bbQaInitOptions || null; }
    catch { options = chart.__bbQaInitOptions || null; }
    try { timeOptions = typeof chart.timeScale()?.options === 'function' ? chart.timeScale().options() : null; } catch {}
    const range = chart.timeScale()?.getVisibleLogicalRange() || null;
    return { selector: sel, chartId: chart.__bbQaId,
      range: range && Number.isFinite(range.from) && Number.isFinite(range.to) ? { from: range.from, to: range.to } : null,
      handleScroll: options?.handleScroll ?? null, handleScale: options?.handleScale ?? null,
      initialHandleScroll: chart.__bbQaInitOptions?.handleScroll ?? null, initialHandleScale: chart.__bbQaInitOptions?.handleScale ?? null,
      barSpacing: timeOptions?.barSpacing ?? null, rightOffset: timeOptions?.rightOffset ?? null };
  }, selector), chartInventory: async stage => js(label => {
    const entries = window.__bbQaCharts || [];
    const panelFor = chart => {
      let node = chart.__bbQaHost instanceof Node ? chart.__bbQaHost : null;
      while (node) {
        if (node.matches?.('.p3') && /TWR INDEX/i.test(node.querySelector('.p3h')?.textContent || '')) return node;
        node = node.parentElement;
      }
      return null;
    };
    const charts = entries.filter(chart => panelFor(chart)).map(chart => {
      const panel = panelFor(chart), host = chart.__bbQaHost;
      const canvas = [...(host?.querySelectorAll?.('canvas') || [])]
        .sort((a, b) => b.width * b.height - a.width * a.height)[0];
      let range = null, options = null, timeOptions = null;
      try { range = chart.timeScale()?.getVisibleLogicalRange() || null; } catch {}
      try { options = typeof chart.options === 'function' ? chart.options() : chart.__bbQaInitOptions || null; } catch {}
      try { timeOptions = typeof chart.timeScale()?.options === 'function' ? chart.timeScale().options() : null; } catch {}
      const rect = canvas?.getBoundingClientRect();
      return { id: chart.__bbQaId ?? null, removed: !!chart.__bbQaRemoved, connected: !!host?.isConnected,
        hostClass: String(host?.className || ''), panelConnected: !!panel?.isConnected,
        range: range && Number.isFinite(range.from) && Number.isFinite(range.to) ? { from: range.from, to: range.to } : null,
        canvas: canvas ? { width: canvas.width, height: canvas.height,
          rect: rect && { x: rect.x, y: rect.y, width: rect.width, height: rect.height } } : null,
        handleScroll: options?.handleScroll ?? null, handleScale: options?.handleScale ?? null,
        barSpacing: timeOptions?.barSpacing ?? null, rightOffset: timeOptions?.rightOffset ?? null,
        panelText: (panel?.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 500) };
    });
    return { stage: label, route: location.hash,
      mode: document.querySelector('[data-theme-choice="dark"]')?.getAttribute('aria-pressed') === 'true' ? 'modern' : 'classic',
      pageClass: String(document.querySelector('main [data-page="performance"]')?.className || ''), charts };
  }, stage), startChartInputProbe: async selector => js(sel => {
    const host = document.querySelector(sel);
    if (!host) return { ok: false, reason: 'chart-host-missing' };
    window.__bbQaChartInputProbes ||= {};
    const id = `chart-input-${Date.now()}-${Object.keys(window.__bbQaChartInputProbes).length + 1}`;
    const probe = { id, host, events: [], listeners: {} };
    for (const type of ['mousedown', 'mousemove', 'mouseup', 'pointerdown', 'pointermove', 'pointerup', 'wheel']) {
      const listener = event => {
        const path = typeof event.composedPath === 'function' ? event.composedPath() : [];
        if (event.target !== host && !host.contains(event.target) && !path.includes(host)) return;
        probe.events.push({ type, isTrusted: event.isTrusted, button: event.button ?? null, buttons: event.buttons ?? null,
          clientX: event.clientX ?? null, clientY: event.clientY ?? null, deltaX: event.deltaX ?? null, deltaY: event.deltaY ?? null,
          target: event.target?.tagName || null, targetClass: typeof event.target?.className === 'string' ? event.target.className.slice(0, 100) : null,
          path: path.slice(0, 6).map(node => node?.tagName || node?.nodeName || null) });
      };
      probe.listeners[type] = listener;
      document.addEventListener(type, listener, true);
    }
    window.__bbQaChartInputProbes[id] = probe;
    return { ok: true, id, listenerTypes: Object.keys(probe.listeners) };
  }, selector), readChartInputProbe: async id => js(key => {
    const probe = window.__bbQaChartInputProbes?.[key];
    return probe ? { id: probe.id, events: probe.events.slice() } : null;
  }, id), stopChartInputProbe: async id => js(key => {
    const probes = window.__bbQaChartInputProbes || {}, probe = probes[key];
    if (!probe) return { stopped: false, events: [] };
    for (const [type, listener] of Object.entries(probe.listeners)) document.removeEventListener(type, listener, true);
    delete probes[key];
    return { stopped: true, events: probe.events.slice() };
  }, id), record, executeScenario, progress, assert, report };

  try {
    const width = 1920, height = 1080;
    window = new BrowserWindow({ show: false, width, height, useContentSize: true, backgroundColor: '#fff',
      webPreferences: { preload: path.join(root, 'dist-electron/preload.mjs'), contextIsolation: true, nodeIntegration: false,
        sandbox: true, backgroundThrottling: false,
        additionalArguments: ['--bellomberg-launch-id=synthetic-pages-modern-actions', '--bellomberg-api-port=' + new URL(config.origin).port] } });
    globalThis.__bbQaWindow = window;
    window.on('close', event => progress('browser-window-close', { defaultPrevented: event.defaultPrevented }));
    window.on('closed', () => progress('browser-window-closed'));
    window.on('unresponsive', () => progress('browser-window-unresponsive'));
    window.on('responsive', () => progress('browser-window-responsive'));
    app.on('before-quit', event => progress('app-before-quit', { defaultPrevented: event.defaultPrevented }));
    app.on('will-quit', event => progress('app-will-quit', { defaultPrevented: event.defaultPrevented }));
    app.on('quit', (_event, exitCode) => progress('app-quit', { exitCode }));
    window.webContents.on('console-message', (_event, level, message) => { if (level >= 2) report.consoleErrors.push({ page: activePage, level, message: String(message) }); });
    window.webContents.debugger.on('message', (_event, method, params) => {
      if (method === 'Network.requestWillBeSent') {
        networkByRequest.set(params.requestId, { requestId: params.requestId, url: params.request.url,
          method: params.request.method, type: params.type || null, startedAt: params.timestamp,
          startedWallTime: params.wallTime, status: null, finishedAt: null, failed: null });
      } else if (method === 'Network.responseReceived') {
        const item = networkByRequest.get(params.requestId);
        if (item) item.response = { status: params.response.status, mimeType: params.response.mimeType,
          fromDiskCache: !!params.response.fromDiskCache, responseAt: params.timestamp };
      } else if (method === 'Network.loadingFinished') {
        const item = networkByRequest.get(params.requestId);
        if (item) { item.finishedAt = params.timestamp; item.encodedDataLength = params.encodedDataLength; }
      } else if (method === 'Network.loadingFailed') {
        const item = networkByRequest.get(params.requestId);
        if (item) item.failed = { at: params.timestamp, errorText: params.errorText, canceled: !!params.canceled };
      }
    });
    window.webContents.on('destroyed', () => progress('webcontents-destroyed', { page: activePage }));
    window.webContents.on('unresponsive', () => progress('webcontents-unresponsive', { page: activePage }));
    window.webContents.on('responsive', () => progress('webcontents-responsive', { page: activePage }));
    window.webContents.on('render-process-gone', (_event, details) => {
      report.consoleErrors.push({ page: activePage, renderProcessGone: details });
      progress('render-process-gone', { page: activePage, details });
    });
    window.webContents.setWindowOpenHandler(details => { report.externalWindowRequests.push({ url: details.url, action: 'denied' }); return { action: 'deny' }; });
    window.webContents.session.on('will-download', (_event, item) => {
      const name = `${Date.now()}-${path.basename(item.getFilename()).replace(/[^a-zA-Z0-9._-]/g, '_')}`;
      const destination = path.join(config.temporary, 'downloads', name);
      fs.mkdirSync(path.dirname(destination), { recursive: true });
      item.setSavePath(destination);
      item.once('done', (_event, state) => report.downloads.push({ name, destination, state, bytes: item.getReceivedBytes() }));
    });
    window.webContents.on('will-navigate', (_event, url) => {
      if (url !== 'about:blank' && !url.startsWith(config.origin + '/')) report.blockedExternalRequests.push(url);
    });
    window.webContents.session.webRequest.onBeforeRequest({ urls: ['http://*/*', 'https://*/*', 'ws://*/*', 'wss://*/*'] }, (details, callback) => {
      const allowed = details.url.startsWith(config.origin + '/');
      if (!allowed) report.blockedExternalRequests.push(details.url);
      callback({ cancel: !allowed });
    });
    // Hidden BrowserWindows may not have a renderer target until their first
    // navigation. Load an inert blank document first, then install CDP hooks,
    // then navigate to the app so instrumentation observes its first render.
    progress('about-blank-load-start');
    await withTimeout(window.loadURL('about:blank'), 'initial about:blank load', 15000);
    progress('about-blank-load-complete', { url: window.webContents.getURL() });
    progress('debugger-attach-start');
    window.webContents.debugger.attach('1.3');
    progress('debugger-attach-complete');
    await cdp('Page.enable');
    progress('cdp-page-enable-complete');
    await cdp('Network.enable');
    progress('cdp-network-enable-complete');
    await cdp('Page.addScriptToEvaluateOnNewDocument', {
      source: `(${installRuntimeInstrumentation.toString()})();(${installActionChartProbe.toString()})()`,
    });
    progress('runtime-instrumentation-installed');
    await withTimeout(window.loadURL(config.origin + '/#/dashboard'), 'initial app load', 30000);
    progress('initial-app-load-complete', { url: window.webContents.getURL() });
    await pause(220);
    await js(() => {
      localStorage.setItem('bellomberg_token_v1', 'synthetic-fixture-token');
      localStorage.setItem('bellomberg_unlocked_v1', JSON.stringify({ ts: Date.now() }));
      localStorage.setItem('bellomberg_last_launch_id', 'synthetic-pages-modern-actions');
      localStorage.setItem('bellomberg.lingua', 'en');
      // No group tests desktop notifications: the alert poll starts on a native
      // 5 s timeout after every load and could land inside a capture/resize window.
      localStorage.setItem('bellomberg_alerts_enabled', 'false');
    });
    await js(theme => { if (theme === 'dark') localStorage.setItem('bellomberg.interface-theme.v1', 'dark');
      else localStorage.removeItem('bellomberg.interface-theme.v1'); }, config.theme || 'light');
    await new Promise(resolve => { window.webContents.once('did-finish-load', resolve); window.webContents.reload(); });
    await waitFor(() => !!document.querySelector('[data-testid="appearance-menu"]') && !!document.querySelector('main .relative'), 'authenticated app shell');
    await q.toggle('classic');
    record('environment', 'isolated-electron-fixture', { assertionResults: { temporaryProfile: true, localOrigin: true, syntheticToken: true }, userData: app.getPath('userData'), origin: config.origin });

    const selectedGroups = new Set(report.groups);
    report.partialRun = !!config.onlyScenario || report.groups.length !== ALL_GROUPS.length
      || ALL_GROUPS.some(group => !selectedGroups.has(group));
    if (selectedGroups.has('core')) await runCore(q);
    if (selectedGroups.has('research')) await runResearch(q);
    if (selectedGroups.has('journal-config')) await runJournalAndConfig(q);
    if (selectedGroups.has('recovery')) await runPresentationRecovery(q);

    const moduleExports = [];
    const selectedSpecialistFiles = SPECIALISTS.filter(file => {
      const key = Object.entries(GROUP_MODULES).find(([, value]) => value === file)?.[0];
      return key && selectedGroups.has(key);
    });
    for (const file of selectedSpecialistFiles) {
      const modulePath = path.join(__dirname, file);
      if (!fs.existsSync(modulePath)) { report.missingSpecialistModules.push(file); continue; }
      const specialist = require(modulePath);
      assert.equal(typeof specialist.run, 'function', `${file} must export async function run(q)`);
      moduleExports.push(file);
      await specialist.run(q);
    }

    const fixtureState = await snapshot();
    const externalCount = report.blockedExternalRequests.length;
    const allFixtureReads = fixtureState.unexpectedGets.length === 0;
    const allFixtureWrites = (fixtureState.unexpectedWrites || []).length === 0;
    const forbiddenAdvisorWrites = fixtureState.requests.filter(item => item.method !== 'GET'
      && (/^\/consigliere(?:\/|$)/.test(item.route) || item.route === '/agents/live/reset'));
    const actualScenarioKeys = new Set(report.scenarios.map(item => `${item.page}:${item.scenario}`));
    const groupCoverage = Object.fromEntries(report.groups.map(group => {
      const required = REQUIRED_SCENARIOS_BY_GROUP[group] || [];
      const missing = required.filter(scenario => !actualScenarioKeys.has(scenario));
      return [group, { required, missing,
        excludedByUser: group === 'committee' ? ['agents:run-confirm-cancel-default-focus-run-and-stop-use-fixture-only-writes'] : [],
        ok: missing.length === 0 }];
    }));
    const missingRequiredScenarios = Object.entries(groupCoverage).flatMap(([group, coverage]) =>
      coverage.missing.map(scenario => ({ group, scenario })));
    report.pages = [...new Set(PAGES.map(([id]) => id).concat('journal', 'settings', 'filing'))].map(id => ({ id,
      scenarios: report.scenarios.filter(item => item.page === id),
      assertionResults: Object.fromEntries(report.scenarios.filter(item => item.page === id).flatMap(item => Object.entries(item.assertionResults || {}))),
      fixtureRequestCount: fixtureState.requests.filter(item => item.route && pageRouteMatches(id, item.route)).length,
      writeCount: fixtureState.writes.filter(item => pageRouteMatches(id, item.route)).length,
      fixtureVsReal: { allHTTPFromFixture: allFixtureReads && allFixtureWrites && externalCount === 0, realBackendStarted: false, realLlmStarted: false } }));
    report.requestCount = fixtureState.requests.length;
    report.fixtureReads = fixtureState.requests.filter(item => item.method === 'GET').length;
    report.fixtureWrites = fixtureState.writes;
    report.unexpectedFixtureGets = fixtureState.unexpectedGets;
    report.unexpectedFixtureWrites = fixtureState.unexpectedWrites || [];
    report.forbiddenAdvisorWrites = forbiddenAdvisorWrites;
    report.groupCoverage = groupCoverage;
    report.missingRequiredScenarios = missingRequiredScenarios;
    report.specialistModules = moduleExports;
    report.faultInstrumentation = { modernPageAnchorCount: config.modernPageAnchorCount,
      deferredAnchorCount: config.deferredPresenterAnchors?.reduce((sum, item) => sum + item.count, 0) || 0,
      assetServed: selectedGroups.has('recovery') };
    report.acceptanceReady = false;
    const recoveryScenarios = ['presenter-fault-recovery', 'trade-local-presenter-fault-retains-draft',
      'mandato-local-presenter-fault-retains-preview', 'journal-local-presenter-fault-retains-draft',
      'vol-local-presenter-fault-retains-catalog-request', 'local-presenter-fault-retains-profile-save',
      'memo-local-presenter-fault-retains-search-request'];
    const requiredRecovery = !selectedGroups.has('recovery') || recoveryScenarios.every(name =>
      report.scenarios.some(s => s.scenario === name && s.status === 'passed'));
    const expectedFaultConsoleErrors = selectedGroups.has('recovery') && requiredRecovery
      ? report.consoleErrors.filter(item => EXPECTED_PRESENTER_FAULT_MESSAGES.has(String(item.message || ''))) : [];
    const canvasReadbackAudit = auditVolCanvasReadbackAdvisory(report.consoleErrors,
      { graphicsMode: report.graphicsMode, scenarios: report.scenarios });
    const expectedElectronWarnings = report.consoleErrors.filter(item => item.level === 2 && item.message === ELECTRON_CSP_WARNING);
    const unexpectedConsoleErrors = report.consoleErrors.filter(item => !expectedFaultConsoleErrors.includes(item)
      && !expectedElectronWarnings.includes(item)
      && !canvasReadbackAudit.expectedCanvasReadbackAdvisories.includes(item));
    report.consoleErrorAudit = { ok: unexpectedConsoleErrors.length === 0,
      expectedElectronWarnings, expectedFaultMessages: expectedFaultConsoleErrors,
      expectedCanvasReadbackAdvisories: canvasReadbackAudit.expectedCanvasReadbackAdvisories,
      nativeVolMeshVerified: canvasReadbackAudit.nativeVolMeshVerified,
      unexpectedErrors: unexpectedConsoleErrors,
      rule: 'Only the exact known Electron unsafe-eval startup warning, the exact QA-injected presenter fault (when all required recovery scenarios pass), and the exact Canvas2D readback performance advisory on /vol after a verified native Plotly mesh pass are allowed. Other console errors and renderer exits fail.' };
    report.ok = report.failedScenarios.length === 0 && report.missingSpecialistModules.length === 0
      && report.unexpectedFixtureGets.length === 0 && report.unexpectedFixtureWrites.length === 0
      && forbiddenAdvisorWrites.length === 0 && missingRequiredScenarios.length === 0 && externalCount === 0 && requiredRecovery
      && report.consoleErrorAudit.ok;
    report.acceptanceReady = report.ok && ALL_GROUPS.every(group => selectedGroups.has(group));
    for (const page of report.pages) fs.writeFileSync(path.join(config.outputDirectory, `${page.id}.json`), JSON.stringify(page, null, 2), { mode: 0o600 });
    fs.writeFileSync(path.join(config.outputDirectory, 'summary.json'), JSON.stringify(report, null, 2), { mode: 0o600 });
    console.log(MARK + JSON.stringify({ ok: report.ok, scenarios: report.scenarios.length, failedScenarios: report.failedScenarios.length,
      acceptanceReady: report.acceptanceReady, groups: report.groups, pages: report.pages.length,
      missingSpecialists: report.missingSpecialistModules, requestCount: report.requestCount,
      writes: report.fixtureWrites.length, advisorRunNotTested: report.advisorRunNotTested,
      forbiddenAdvisorWrites: report.forbiddenAdvisorWrites.length,
      externalBlocked: report.blockedExternalRequests.length, externalWindowDenied: report.externalWindowRequests.length }));
    if (!report.ok) app.exit(1); else app.exit(0);
  } catch (error) {
    report.ok = false;
    report.runnerError = String(error?.stack || error);
    report.failedScenarios.push({ page: 'runner', scenario: 'uncaught-error', status: 'failed', error: report.runnerError });
    fs.writeFileSync(path.join(config.outputDirectory, 'summary.json'), JSON.stringify(report, null, 2), { mode: 0o600 });
    console.error(error?.stack || error);
    app.exit(1);
  } finally {
    if (window && !window.isDestroyed()) window.destroy();
  }
}

const selectors = {
  dashboard: ['main [data-page="dashboard"]'], performance: ['main [data-page="performance"] [data-qa="perf-view"] button:nth-of-type(2)', 'main [data-page="performance"] [data-qa="perf-refresh"]'],
  watchlist: ['main [data-page="watchlist"] textarea'], market: ['main [data-page="market"] .market-page input'],
  news: ['main [data-page="news"] button.tb:nth-of-type(2)', 'main [data-page="news"] .tb'],
  fundamentals: ['main [data-page="fundamentals"] tbody tr', 'main [data-page="fundamentals"] input[aria-label]'],
  factors: ['main [data-page="factors"] [data-strato="calibro"] [role="radio"]'], backtest: ['main [data-page="montecarlo"] select'],
  vol: ['main [data-page="vol"] #va-ticker'], edge: ['main [data-page="edge"] [data-zona="comandi"] button:nth-of-type(2)'],
  agents: ['main [data-page="agents"] .ag-dettagli .bbn-seg button:nth-of-type(3)'], 'agent-progress': ['main [data-page="agent-progress"] #ap-tab-action'],
  memos: ['main [data-page="memos"] .mr'], decisions: ['main [data-page="decisions"] button'],
  trades: ['main [data-page="trades"] #f7-tk'], movements: ['main [data-page="movements"] .vst button:nth-of-type(2)'],
  mandato: ['main [data-page="mandato"] #tab-diario'],
};

async function runCore(q) {
  for (const [id, route] of PAGES) await q.executeScenario(id, 'state-and-controller-survive-mode-switches', async () => {
    await q.toggle('classic');
    await q.visit(route);
    const initialState = await q.pageState();
    assert.ok(!initialState.pageStateError, `${id}: page-state probe failed: ${initialState.pageStateError}`);
    await preparePageState(q, id);
    await q.pause(300);
    const beforeState = await q.pageState();
    assert.ok(!beforeState.pageStateError, `${id}: page-state probe failed after setup: ${beforeState.pageStateError}`);
    assert.ok(beforeState.id != null, `${id}: page root missing`);
    assert.ok(beforeState.controls.length || beforeState.choices.length, `${id}: no controllable local state was found`);
    const stateModified = JSON.stringify(initialState.controls) !== JSON.stringify(beforeState.controls)
      || JSON.stringify(initialState.choices) !== JSON.stringify(beforeState.choices);
    assert.ok(stateModified, `${id}: the setup did not change page-owned UI state before toggling`);
    if (id === 'dashboard') await q.pause(1800); // late dashboard effects/read pairs are fixture requests, not mode-toggle work
    await q.settleRequests(`${id} initial page requests`);
    const beforeCounts = await q.counts();
    const beforeRuntime = await q.runtimeSnapshot();
    const modernClick = await q.toggle('modern');
    await q.pause(220);
    const modernState = await q.pageState();
    const modernRuntime = await q.runtimeSnapshot();
    const afterModernCounts = await q.counts();
    const classicClick = await q.toggle('classic');
    await q.pause(220);
    const classicState = await q.pageState();
    const classicRuntime = await q.runtimeSnapshot();
    const afterClassicCounts = await q.counts();
    const requestDeltasByLeg = { classicToModern: deltaCounts(beforeCounts, afterModernCounts), modernToClassic: deltaCounts(afterModernCounts, afterClassicCounts) };
    const requestDeltas = deltaCounts(beforeCounts, afterClassicCounts);
    // A theme switch changes colours only: no page, the Dashboard included,
    // may issue a read on either leg.
    const knownDashboardReferenceReads = { classicToModern: {}, modernToClassic: {} };
    const sameCounts = (actual, expected) => JSON.stringify(Object.entries(actual).sort(([a], [b]) => a.localeCompare(b)))
      === JSON.stringify(Object.entries(expected).sort(([a], [b]) => a.localeCompare(b)));
    const requestDeltaMatchesKnownBaseline = id === 'dashboard'
      ? sameCounts(requestDeltasByLeg.classicToModern, knownDashboardReferenceReads.classicToModern)
        && sameCounts(requestDeltasByLeg.modernToClassic, knownDashboardReferenceReads.modernToClassic)
      : Object.keys(requestDeltas).length === 0;
    const sameState = (a, b) => JSON.stringify(a.controls) === JSON.stringify(b.controls) && JSON.stringify(a.choices) === JSON.stringify(b.choices);
    const controlStateStable = sameState(beforeState, modernState) && sameState(beforeState, classicState);
    const rootStable = beforeState.id === modernState.id && modernState.id === classicState.id;
    const runtimeBefore = normalizeRuntime(beforeRuntime), runtimeModern = normalizeRuntime(modernRuntime), runtimeClassic = normalizeRuntime(classicRuntime);
    const runtimeComparison = compareRuntimeAcrossModes(beforeRuntime, modernRuntime, classicRuntime, { referencePage: id });
    const runtimeStable = runtimeComparison.ok;
    const lifecycleEvents = [...modernRuntime.events.slice(beforeRuntime.events.length), ...classicRuntime.events.slice(modernRuntime.events.length)]
      .filter(event => ['interval-added', 'interval-removed', 'listener-added', 'listener-removed'].includes(event.kind));
    assert.equal(modernState.page, beforeState.page, `${id}: page wrapper identity disappeared in Modern mode`);
    assert.equal(classicState.page, beforeState.page, `${id}: page wrapper identity changed after return to Classic`);
    assert.ok(rootStable, `${id}: page wrapper DOM node remounted during the mode switch`);
    assert.ok(controlStateStable, `${id}: changed UI state did not persist across mode switches: ${JSON.stringify({ beforeState, modernState, classicState })}`);
    assert.ok(requestDeltaMatchesKnownBaseline, `${id}: mode change caused requests beyond its allowed baseline: ${JSON.stringify({ total: requestDeltas, legs: requestDeltasByLeg, knownDashboardReferenceReads: id === 'dashboard' ? knownDashboardReferenceReads : undefined })}`);
    assert.ok(runtimeStable, `${id}: interval or global listener lifecycle changed beyond balanced, chart-origin rebinds: ${JSON.stringify({ comparison: runtimeComparison, lifecycleEvents })}`);
    const pageReport = { assertionResults: { wrapperIdentityStable: rootStable, changedControlStatePreserved: controlStateStable,
      stateWasModifiedBeforeToggle: stateModified,
      lifecycleReferenceAccountedFor: requestDeltaMatchesKnownBaseline,
      requestDeltaMatchesKnownBaseline,
      knownDashboardReferenceReads: id === 'dashboard' ? knownDashboardReferenceReads : null,
      knownDashboardReferenceIntervalRebind: id === 'dashboard' ? runtimeComparison.dashboardReferenceTimerRebind : null,
      noUnexpectedIntervalOrGlobalListenerChurn: runtimeStable,
      classicAndModernModeChoicesHitTested: modernClick.hitTest && classicClick.hitTest },
      action: 'page-specific local control changed before round-trip; mode selector activated by coordinate mouse input after hit-test',
      initial: summarizeState(initialState), before: summarizeState(beforeState), modern: summarizeState(modernState), returnedClassic: summarizeState(classicState), requestDeltas, requestDeltasByLeg,
      runtime: { clock: beforeRuntime.clock, before: runtimeBefore, modern: runtimeModern, classic: runtimeClassic, lifecycleEvents, comparison: runtimeComparison },
      clickEvidence: { modern: modernClick, classic: classicClick }, fixtureVsReal: { backend: 'synthetic fixture', backendProcessStarted: false, llmStarted: false } };
    assert.ok(pageReport.assertionResults.classicAndModernModeChoicesHitTested);
    return pageReport;
  });

  await q.executeScenario('agents', 'one-explicit-controlled-poll-tick', async () => {
    await q.toggle('classic'); await q.visit('/agents');
    const runtime = await q.runtimeSnapshot();
    const pollingIntervals = runtime.intervals.filter(interval => interval.delay === 1500);
    assert.equal(pollingIntervals.length, 1, `expected one Agents polling interval, saw ${JSON.stringify(pollingIntervals)}`);
    const interval = pollingIntervals[0], before = await q.counts();
    await q.tick([interval.id]);
    const after = await q.counts(), afterRuntime = await q.runtimeSnapshot();
    const requestDelta = (after['GET /agents/live'] || 0) - (before['GET /agents/live'] || 0);
    const ticks = afterRuntime.intervals.find(item => item.id === interval.id)?.ticks;
    assert.equal(requestDelta, 1, 'one manually controlled poll interval tick must cause exactly one fixture read');
    assert.equal(ticks, interval.ticks + 1, 'the selected interval callback must run exactly once');
    return { assertionResults: { controlledIntervalClock: runtime.clock.startsWith('fixture-controlled'), oneTickOneRequest: requestDelta === 1, callbackCountedOnce: true },
      interval: { id: interval.id, delay: interval.delay, beforeTicks: interval.ticks, afterTicks: ticks }, requestDelta: { 'GET /agents/live': requestDelta }, clock: runtime.clock };
  });
}

async function preparePageState(q, id) {
  if (id === 'factors') {
    const radio = await q.js(() => {
      const root = document.querySelector('main [data-page="factors"]');
      const radios = [...(root?.querySelectorAll('[data-strato="calibro"] [role="radio"]') || [])];
      const selected = radios.findIndex(el => el.getAttribute('aria-checked') === 'true');
      if (selected < 0 || radios.length < 2) return { count: radios.length, selected };
      const direction = selected === radios.length - 1 ? 'ArrowLeft' : 'ArrowRight';
      radios[selected].focus();
      return { count: radios.length, selected, direction, focused: document.activeElement === radios[selected] };
    });
    assert.ok(radio.count > 1 && radio.selected >= 0 && radio.focused,
      `Factors gauge roving-focus control is not available: ${JSON.stringify(radio)}`);
    await q.key(radio.direction);
    await q.waitFor((previous, count) => {
      const current = [...document.querySelectorAll('main [data-page="factors"] [role="radio"]')]
        .findIndex(el => el.getAttribute('aria-checked') === 'true');
      return current >= 0 && current !== previous && count > 1;
    }, 'Factors gauge keyboard selection', 2500, radio.selected, radio.count);
    return;
  }
  if (id === 'edge') {
    const target = await q.js(() => {
      const buttons = [...document.querySelectorAll('main [data-page="edge"] [data-zona="comandi"] button')]
        .filter(el => /^≥\s*\d+/.test(el.innerText.trim()));
      const candidate = buttons.find(el => !el.classList.contains('bg-gold'));
      if (!candidate) return { count: buttons.length, active: buttons.find(el => el.classList.contains('bg-gold'))?.innerText.trim() || null };
      candidate.setAttribute('data-qa-action-choice', 'edge-threshold');
      return { count: buttons.length, target: candidate.innerText.trim(), selector: '[data-qa-action-choice="edge-threshold"]' };
    });
    assert.ok(target.selector, `Edge Scanner has no alternate strength filter: ${JSON.stringify(target)}`);
    await q.click(`main [data-page="edge"] ${target.selector}`);
    await q.waitFor(() => document.querySelector('main [data-page="edge"] [data-qa-action-choice="edge-threshold"]')?.classList.contains('bg-gold'), 'Edge Scanner selected threshold');
    return;
  }
  if (id === 'mandato') {
    const link = selectors[id][0];
    await q.click(link);
    await q.waitFor(() => document.querySelector('#tab-diario')?.getAttribute('aria-selected') === 'true' && !!document.querySelector('#panel-diario .journal-page'), 'Mandato Journal panel');
    const control = await q.js(() => [...document.querySelectorAll('#panel-diario input,#panel-diario textarea')].find(el => el.offsetWidth && el.offsetHeight)?.tagName || null);
    if (control) await q.setValue('#panel-diario .journal-title-input', 'Synthetic retained journal title');
    else await q.click('#panel-diario .journal-primary');
    return;
  }
  if (id === 'dashboard') {
    // Page-owned state: the hero period (1M by default) moves to 1W.
    const selector = 'main .bbn-hero .bbn-seg button:nth-child(2)';
    await q.waitFor(() => !!document.querySelector('main .bbn-hero .bbn-seg button'), 'Dashboard hero periods');
    await q.click(selector);
    await q.waitFor(() => document.querySelector('main .bbn-hero .bbn-seg button:nth-child(2)')?.getAttribute('aria-pressed') === 'true', 'Dashboard hero period 1W');
    return;
  }
  if (id === 'fundamentals') {
    const row = await q.js(() => !!document.querySelector('main [data-page="fundamentals"] tbody tr'));
    if (row) {
      await q.click('main [data-page="fundamentals"] tbody tr:first-child');
      await q.waitFor(() => !!document.querySelector('main [data-page="fundamentals"] [data-testid="sector-valuation-status"]'), 'Fundamentals selected model detail');
      const variant = await q.js(() => document.querySelector('main [data-page="fundamentals"] input[aria-label]')?.getAttribute('aria-label') || null);
      if (variant) {
        const input = `main [data-page="fundamentals"] input[aria-label="${cssEscape(variant)}"]`;
        await q.setValue(input, 'Synthetic retained variant label');
      }
      return;
    }
  }
  if (id === 'memos') {
    const row = await q.js(() => !!document.querySelector('main [data-page="memos"] .mr'));
    const candidate = await q.js(() => [...document.querySelectorAll('main [data-page="memos"] .mr')].find(el => !el.classList.contains('on'))?.getAttribute('data-memo-id') || null);
    if (row) {
      const selector = await q.js(() => {
        const item = [...document.querySelectorAll('main [data-page="memos"] .mr')].find(el => !el.classList.contains('on'));
        if (!item) return null;
        item.setAttribute('data-qa-action-choice', 'memo'); return '[data-qa-action-choice="memo"]';
      });
      assert.ok(selector, `memo fixture has no unselected row (candidate ${candidate})`);
      await q.click(`main [data-page="memos"] ${selector}`);
      await q.waitFor(() => document.querySelectorAll('main [data-page="memos"] .mr.on').length === 1, 'memo selection'); return;
    }
  }
  if (id === 'decisions') {
    const selector = await q.js(() => {
      const root = document.querySelector('main [data-page="decisions"]');
      const button = [...(root?.querySelectorAll('button') || [])].find(el => /^(PENDING|IN ATTESA)$/i.test(el.innerText.trim()));
      if (!button) return null;
      button.setAttribute('data-qa-action-choice', 'decision-filter'); return '[data-qa-action-choice="decision-filter"]';
    });
    assert.ok(selector, 'Decisions page did not render its Pending status filter');
    await q.click(`main [data-page="decisions"] ${selector}`);
    await q.waitFor(() => {
      const button = document.querySelector('main [data-page="decisions"] [data-qa-action-choice="decision-filter"]');
      return button?.className.includes('bg-[#1c1305]');
    }, 'Pending decision filter');
    return;
  }
  if (id === 'agents') {
    // the page's own choice is the tab of the «Run details» drawer (05/10/2026); Tools is never the default tab
    const selector = 'main [data-page="agents"] .ag-dettagli .bbn-seg button:nth-of-type(3)';
    await q.click('main [data-page="agents"] button[data-dettagli="1"]');
    await q.waitFor(() => document.querySelector('main [data-page="agents"] .ag-dettagli')?.dataset.aperto === '1', 'Agents details drawer', 5000);
    await q.pause(450);
    await q.click(selector);
    await q.waitFor(sel => document.querySelector(sel)?.getAttribute('aria-pressed') === 'true',
      'Agents details tab', 5000, selector);
    await q.click('main [data-page="agents"] .ag-dettagli button.ag-cass-x');
    await q.waitFor(() => document.querySelector('main [data-page="agents"] .ag-dettagli')?.dataset.aperto === '0', 'Agents details drawer closed', 5000);
    return;
  }
  if (id === 'news') {
    const controls = await q.js(() => [...document.querySelectorAll('main [data-page="news"] button')].map(el => (el.innerText || '').trim()).filter(Boolean).slice(0, 15));
    if (controls.some(text => /^DESK$/i.test(text))) { await q.click('main [data-page="news"] button.tb:nth-of-type(2)'); await q.waitFor(() => [...document.querySelectorAll('main [data-page="news"] button.tb')].some(el => /^DESK$/i.test(el.innerText.trim()) && el.classList.contains('on')), 'news desk selection'); return; }
  }
  const targetSelectors = selectors[id] || [];
  let chosen = null;
  for (const candidate of targetSelectors) {
    chosen = await q.js(sel => {
      const el = document.querySelector(sel);
      if (!el) return null;
      const rect = el.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && getComputedStyle(el).display !== 'none' ? { selector: sel, tag: el.tagName, type: el.type || '', value: el.value, text: (el.innerText || '').trim().slice(0, 100) } : null;
    }, candidate);
    if (chosen) break;
  }
  assert.ok(chosen, `${id}: no safe local state control found in selectors ${JSON.stringify(targetSelectors)}`);
  if (chosen.tag === 'INPUT' || chosen.tag === 'TEXTAREA') {
    const value = chosen.type === 'checkbox' ? undefined : chosen.type === 'number' || chosen.type === 'range' ? '12'
      : chosen.type === 'date' ? '2026-09-29' : chosen.type === 'time' ? '12:34' : `QA retained ${id}`;
    if (chosen.type === 'checkbox') await q.js(sel => { const el = document.querySelector(sel); el.click(); }, chosen.selector);
    else if (id === 'vol') await q.setValue(chosen.selector, 'SYNQA');
    else if (id === 'trades') await q.typeText(chosen.selector, 'SYN1', 'SYN1');
    else if (id === 'market') {
      await q.setValue(chosen.selector, 'SYN1');
      await q.waitFor(() => !!document.querySelector('main [data-page="market"] .market-page .mk-menu button.mk-hit'), 'Market search suggestion', 5000);
      await q.click('main [data-page="market"] .market-page .mk-menu button.mk-hit');
    } else await q.setValue(chosen.selector, value);
    return;
  }
  if (chosen.tag === 'SELECT') {
    const next = await q.js(sel => { const el = document.querySelector(sel); return el?.options?.[1]?.value ?? null; }, chosen.selector);
    assert.ok(next != null, `${id}: select has no alternative state`);
    await q.setValue(chosen.selector, next);
    return;
  }
  await q.click(chosen.selector);
  await q.pause(200);
}

async function runResearch(q) {
  await q.executeScenario('performance', 'range-control-is-retained', async () => {
    await q.visit('/performance');
    const R = 'main [data-page="performance"]';
    await q.waitFor(() => document.querySelectorAll('main [data-page="performance"] [data-qa="perf-range"] button').length === 5, 'Performance period selector');
    await q.waitFor(() => !!document.querySelector('main [data-page="performance"] .perf-line'), 'Performance TWR line');
    // A Nuova chart is plain SVG: the period is proved by the drawn series, not by a chart engine range.
    const read = () => q.js(() => {
      const root = document.querySelector('main [data-page="performance"]');
      const buttons = [...root.querySelectorAll('[data-qa="perf-range"] button')];
      const d = root.querySelector('.perf-line')?.getAttribute('d') || '';
      return { labels: buttons.map(b => b.innerText.trim()), active: buttons.find(b => b.getAttribute('aria-pressed') === 'true')?.innerText.trim() || null,
        heroLabel: root.querySelector('.perf-hero-label')?.textContent.trim() || null, points: (d.match(/[ML]/g) || []).length };
    });
    const initial = await read();
    assert.equal(initial.labels.length, 5, `Performance period labels: ${JSON.stringify(initial)}`);
    const target = initial.labels.find(label => /^1(S|W)$/.test(label));
    assert.ok(target && initial.active && target !== initial.active, `weekly period must be selectable: ${JSON.stringify(initial)}`);
    await q.js(label => { [...document.querySelectorAll('main [data-page="performance"] [data-qa="perf-range"] button')]
      .find(b => b.innerText.trim() === label)?.setAttribute('data-qa-performance-range', 'true'); }, target);
    await q.click(`${R} [data-qa-performance-range="true"]`);
    await q.waitFor(label => [...document.querySelectorAll('main [data-page="performance"] [data-qa="perf-range"] button')]
      .find(b => b.innerText.trim() === label)?.getAttribute('aria-pressed') === 'true', 'weekly period selected', 5000, target);
    const selected = await read();
    assert.equal(selected.active, target);
    assert.notEqual(selected.heroLabel, initial.heroLabel, 'hero label follows the period');
    assert.ok(selected.points > 1 && selected.points < initial.points, `weekly period draws fewer points: ${JSON.stringify({ initial, selected })}`);
    await q.settleRequests('Performance period-dependent reads', 6000);
    const before = await q.pageState(), counts = await q.counts();
    await q.toggle('modern'); const modern = await q.pageState();
    await q.capture('performance-weekly-twr-range');
    await q.toggle('classic');
    await q.pause(350);
    const after = await q.pageState(), requestDeltas = deltaCounts(counts, await q.counts());
    const retained = await read();
    assert.equal(before.id, after.id); assert.deepEqual(before.choices, after.choices); assert.deepEqual(modern.choices, before.choices); assert.deepEqual(requestDeltas, {});
    assert.deepEqual(retained, selected, 'period, headline and drawn series survive both theme switches');
    return { assertionResults: { rangeChangedDrawnSeries: selected.points < initial.points, rangeLabelAndHeadlineAgree: true, rangeStateRetained: true,
      wrapperStable: before.id === after.id, noRepeatRequests: Object.keys(requestDeltas).length === 0 },
      initial, selected, retained, modern, requestDeltas };
  });
  await q.executeScenario('watchlist', 'note-draft-is-retained-without-writing', async () => {
    await q.visit('/watchlist');
    await q.waitFor(() => document.querySelectorAll('main [data-page="watchlist"] textarea').length > 0, 'Watchlist note editor');
    const selector = 'main [data-page="watchlist"] tbody textarea';
    const beforeCounts = await q.counts();
    await q.setValue(selector, 'Synthetic note retained across presentation mode.');
    const before = await q.pageState(); await q.toggle('modern'); await q.toggle('classic');
    const after = await q.pageState(), requestDeltas = deltaCounts(beforeCounts, await q.counts());
    assert.equal(before.id, after.id); assert.deepEqual(before.controls, after.controls); assert.deepEqual(requestDeltas, {});
    assert.ok((after.controls || []).some(control => control.value === 'Synthetic note retained across presentation mode.'));
    return { assertionResults: { noteDraftRetained: true, noMutationSent: true, wrapperStable: true, noExtraRequests: true }, requestDeltas, writesObserved: 0 };
  });
  await q.executeScenario('market', 'search-detail-tabs-and-controller-retain-state', async () => {
    await q.visit('/market');
    const search = 'main [data-page="market"] .market-page input';
    await q.setValue(search, 'SYN1');
    await q.waitFor(() => !!document.querySelector('main [data-page="market"] .market-page .mk-menu button.mk-hit'), 'Market synthetic search hit', 6000);
    const hit = await q.js(() => document.querySelector('main [data-page="market"] .market-page .mk-menu button.mk-hit')?.innerText || null);
    assert.ok(hit, 'Market fixture search result absent');
    await q.click('main [data-page="market"] .market-page .mk-menu button.mk-hit');
    await q.waitFor(() => document.querySelector('main [data-page="market"] .market-page')?.innerText.includes('SYN1'), 'Market detail controller');
    await q.pause(400);
    // scheda Nuova: il segmento «Balance» del box Bilanci, con il nome completo nel title
    const financialTab = await q.js(() => [...document.querySelectorAll('main [data-page="market"] .mk-c-fin .bbn-seg button')].find(el => /balance sheet/i.test(el.title))?.innerText || null);
    if (financialTab) {
      await q.js(text => [...document.querySelectorAll('main [data-page="market"] .mk-c-fin .bbn-seg button')].find(el => el.innerText.trim() === text)?.setAttribute('data-qa-market-tab', 'true'), financialTab);
      await q.click('main [data-page="market"] button[data-qa-market-tab="true"]');
    }
    const financialEvidence = await q.js(() => {
      const root = document.querySelector('main [data-page="market"]');
      const selectedTab = [...root.querySelectorAll('.mk-c-fin .bbn-seg button')].find(el => el.classList.contains('is-on'))?.innerText.trim() || null;
      const rows = [...root.querySelectorAll('table tbody tr')].map(row => row.innerText.trim());
      return { selectedTab, firstRows: rows.slice(0, 3), hasTotalAssets: rows.some(row => row.includes('Total Assets')) };
    });
    assert.ok(financialEvidence.selectedTab && financialEvidence.hasTotalAssets,
      `Market financial tab did not select populated balance-sheet data: ${JSON.stringify(financialEvidence)}`);
    // grafico Nuova: periodo e candela sono due menu (8 periodi), come nel dettaglio della Dashboard
    const rangeSelect = 'main [data-page="market"] .mk-c-chart .tbar .tsel:first-child select';
    const chartRange = await q.js(sel => {
      const select = document.querySelector(sel);
      const options = [...(select?.options || [])];
      const active = options.find(option => option.selected);
      const target = options.find(option => option !== active && !option.disabled);
      return { active: active?.text.trim() || null, target: target?.text.trim() || null, targetValue: target?.value ?? null,
        available: options.length, toolbar: !!select };
    }, rangeSelect);
    assert.ok(chartRange.toolbar && chartRange.available === 8 && chartRange.target, `Market chart range controls unavailable: ${JSON.stringify(chartRange)}`);
    const chartReadsBefore = (await q.counts())['GET /market/ohlc'] || 0;
    await q.setValue(rangeSelect, chartRange.targetValue);
    await q.waitFor((sel, target) => document.querySelector(sel)?.selectedOptions?.[0]?.text.trim() === target,
    'Market chart range updated', 5000, rangeSelect, chartRange.target);
    let chartReadsAfter = chartReadsBefore;
    const readDeadline = Date.now() + 5000;
    while (Date.now() < readDeadline && chartReadsAfter <= chartReadsBefore) {
      chartReadsAfter = (await q.counts())['GET /market/ohlc'] || 0;
      if (chartReadsAfter <= chartReadsBefore) await q.pause(40);
    }
    assert.ok(chartReadsAfter > chartReadsBefore, 'Market chart range must cause one explicit OHLC data read');
    const markedPanel = await q.js(() => {
      const panel = document.querySelector('main [data-page="market"] .mk-c-chart');
      if (panel) panel.setAttribute('data-qa-market-chart', 'true');
      return !!panel;
    });
    assert.ok(markedPanel, 'Market price-action panel is present');
    const panelSelector = 'main [data-page="market"] [data-qa-market-chart="true"]';
    const canvasSelector = panelSelector + ' canvas';
    const indicators = {};
    for (const label of ['SMA 20/50', 'VWAP', 'RSI 14', 'LOG']) {
      const selector = await q.js(text => {
        const panel = document.querySelector('main [data-page="market"] [data-qa-market-chart="true"]');
        const button = [...(panel?.querySelectorAll('.tbar button') || [])].find(item => item.textContent.trim() === text);
        if (!button) return null;
        const key = text.replace(/\W+/g, '-').toLowerCase();
        button.setAttribute('data-qa-market-tool', key);
        return `main [data-page="market"] [data-qa-market-chart="true"] [data-qa-market-tool="${key}"]`;
      }, label);
      assert.ok(selector, `Market chart control ${label} is present`);
      const initial = await q.js(sel => {
        const el = document.querySelector(sel);
        return { off: el?.classList.contains('off') || false, on: el?.classList.contains('on') || false };
      }, selector);
      indicators[label] = initial;
      const needsToggle = label === 'SMA 20/50' ? !initial.off : label === 'LOG' ? !initial.on : initial.off;
      if (needsToggle) await q.click(selector);
    }
    await q.waitFor(() => {
      const panel = document.querySelector('main [data-page="market"] [data-qa-market-chart="true"]');
      const legend = [...(panel?.querySelectorAll('div.absolute') || [])].map(item => item.innerText).join(' ');
      const button = text => [...(panel?.querySelectorAll('.tbar button') || [])].find(item => item.textContent.trim() === text);
      return legend.includes('VWAP') && legend.includes('RSI14') && !legend.includes('SMA20')
        && button('SMA 20/50')?.classList.contains('off') && !button('VWAP')?.classList.contains('off')
        && !button('RSI 14')?.classList.contains('off') && button('LOG')?.classList.contains('on');
    }, 'Market indicators and logarithmic scale applied to chart', 6000);
    await q.pause(300);
    const chartBeforeGestures = await q.chartState(panelSelector);
    assert.ok(chartBeforeGestures.chartCount > 0 && chartBeforeGestures.range && chartBeforeGestures.canvas?.width > 80,
      `Market price-action must expose a live native logical range: ${JSON.stringify(chartBeforeGestures)}`);
    const wheelEvidence = await q.wheel(panelSelector, -240);
    await q.waitFor((selector, from, to) => {
      const panel = document.querySelector(selector);
      const chart = (window.__bbQaCharts || []).filter(item => !item.__bbQaRemoved && item.__bbQaHost instanceof Node && panel?.contains(item.__bbQaHost)).at(-1);
      const range = chart?.timeScale()?.getVisibleLogicalRange();
      return range && (Math.abs(range.from - from) > .05 || Math.abs(range.to - to) > .05);
    }, 'native Market wheel zoom changes visible logical range', 5000, panelSelector, chartBeforeGestures.range.from, chartBeforeGestures.range.to);
    const chartAfterZoom = await q.chartState(panelSelector);
    const dragEvidence = await q.drag(panelSelector, -110, 0);
    await q.waitFor((selector, from, to) => {
      const panel = document.querySelector(selector);
      const chart = (window.__bbQaCharts || []).filter(item => !item.__bbQaRemoved && item.__bbQaHost instanceof Node && panel?.contains(item.__bbQaHost)).at(-1);
      const range = chart?.timeScale()?.getVisibleLogicalRange();
      return range && (Math.abs(range.from - from) > .05 || Math.abs(range.to - to) > .05);
    }, 'native Market drag changes visible logical range', 5000, panelSelector, chartAfterZoom.range.from, chartAfterZoom.range.to);
    const chartAfterPan = await q.chartState(panelSelector);
    const selectedChartRange = await q.js(sel => document.querySelector(sel)?.selectedOptions?.[0]?.text.trim() || null, rangeSelect);
    assert.equal(selectedChartRange, chartRange.target);
    const before = await q.pageState(), counts = await q.counts();
    await q.toggle('modern'); const modern = await q.pageState();
    await q.capture('market-balance-sheet-chart-range');
    await q.toggle('classic');
    await q.pause(350);
    const chartAfterModeSwitch = await q.chartState(panelSelector);
    const after = await q.pageState(), requestDeltas = deltaCounts(counts, await q.counts());
    assert.equal(before.id, after.id); assert.deepEqual(before.controls, after.controls); assert.deepEqual(before.choices, after.choices); assert.deepEqual(modern.choices, before.choices); assert.deepEqual(requestDeltas, {});
    const indicatorAfter = await q.js(() => {
      const panel = document.querySelector('main [data-page="market"] [data-qa-market-chart="true"]');
      const button = text => [...(panel?.querySelectorAll('.tbar button') || [])].find(item => item.textContent.trim() === text);
      const legend = [...(panel?.querySelectorAll('div.absolute') || [])].map(item => item.innerText).join(' ');
      return { smaOff: button('SMA 20/50')?.classList.contains('off'), vwapOn: !button('VWAP')?.classList.contains('off'),
        rsiOn: !button('RSI 14')?.classList.contains('off'), logOn: button('LOG')?.classList.contains('on'), legend };
    });
    assert.ok(indicatorAfter.smaOff && indicatorAfter.vwapOn && indicatorAfter.rsiOn && indicatorAfter.logOn
      && indicatorAfter.legend.includes('VWAP') && indicatorAfter.legend.includes('RSI14'),
    `Market indicators must remain selected across presentation modes: ${JSON.stringify(indicatorAfter)}`);
    assert.ok(chartAfterModeSwitch.range && Math.abs(chartAfterModeSwitch.range.from - chartAfterPan.range.from) < .05
      && Math.abs(chartAfterModeSwitch.range.to - chartAfterPan.range.to) < .05,
    `Market pan/zoom logical range should be preserved across Modern and Classic: before=${JSON.stringify(chartAfterPan)} after=${JSON.stringify(chartAfterModeSwitch)}`);
    const ohlcAfterModes = (await q.counts())['GET /market/ohlc'] || 0;
    assert.equal(ohlcAfterModes, chartReadsAfter, 'gestures and mode switches must not refetch unchanged OHLC data');
    return { assertionResults: { searchHitReached: true, detailMounted: true, financialTabUsesBalanceData: financialEvidence.hasTotalAssets,
      chartRangeCausedOneDataRead: true, nativeWheelAndDragChangedVisibleLogicalRange: true,
      indicatorsAndLogScaleToggled: true, chartLogicalRangeAndControlsRetained: true,
      selectedControlRetained: true, noModeRequests: Object.keys(requestDeltas).length === 0 },
      selectedHit: hit, financialEvidence, chartRange: { ...chartRange, selected: selectedChartRange }, indicators,
      chartGestures: { wheel: wheelEvidence, drag: dragEvidence },
      chartLogicalRange: { before: chartBeforeGestures, afterZoom: chartAfterZoom, afterPan: chartAfterPan, afterModeSwitch: chartAfterModeSwitch },
      indicatorAfter, requestDeltas };
  });
  await q.executeScenario('news', 'wire-desk-and-filter-state-retain', async () => {
    // 02/10/2026: pagina Notizie in stile Nuova — viste Feed | Agenda, filtri in una barra sopra l'elenco.
    const view = label => `main [data-page="news"] [data-qa="news-view"] button[data-qa-view="${label}"]`;
    const markViews = () => q.js(() => [...document.querySelectorAll('main [data-page="news"] [data-qa="news-view"] button')]
      .forEach(el => el.setAttribute('data-qa-view', el.innerText.trim())));
    await q.visit('/news');
    await q.waitFor(() => !!document.querySelector('main [data-page="news"] [data-qa="news-view"] button'), 'News controls');
    await q.waitFor(() => document.querySelectorAll('main [data-page="news"] .news-row').length >= 4, 'News synthetic active and archived headlines');
    const wireCounts = await q.js(() => ({ total: document.querySelectorAll('main [data-page="news"] .news-row').length,
      feed: document.querySelector('main [data-page="news"] .news-fcount')?.innerText || '' }));
    assert.equal(wireCounts.total, 4, `News should render its four synthetic items: ${JSON.stringify(wireCounts)}`);
    await q.js(() => [...document.querySelectorAll('main [data-page="news"] .news-filters [aria-label="Period"] button')]
      .find(el => el.innerText.trim() === '24 hours')?.setAttribute('data-qa-news-period', 'true'));
    assert.ok(await q.js(() => !!document.querySelector('main [data-page="news"] [data-qa-news-period="true"]')), 'News 24h filter unavailable');
    await q.click('main [data-page="news"] [data-qa-news-period="true"]');
    await q.waitFor(() => document.querySelectorAll('main [data-page="news"] .news-row').length === 3,
      '24-hour date filter excludes only archived synthetic article', 5000);
    const filteredBefore = await q.js(() => ({ rows: document.querySelectorAll('main [data-page="news"] .news-row').length,
      activeFilters: document.querySelector('main [data-page="news"] .news-fcount')?.innerText || '',
      periodSelected: document.querySelector('main [data-page="news"] [data-qa-news-period="true"]')?.getAttribute('aria-pressed') === 'true',
      providerStatus: [...document.querySelectorAll('main [data-page="news"] .news-note')].map(el => el.innerText)
        .find(value => value?.includes('Synthetic stale provider status.')) || '' }));
    assert.equal(filteredBefore.rows, 3);
    assert.ok(filteredBefore.periodSelected, 'News date filter should expose its selected state');
    assert.ok(filteredBefore.providerStatus, 'News stale/degraded provider notice should retain explanatory details');
    await markViews();
    assert.ok(await q.js(s => !!document.querySelector(s), view('Agenda')), 'News Agenda view is missing');
    await q.click(view('Agenda'));
    await q.waitFor(s => document.querySelector(s)?.getAttribute('aria-pressed') === 'true', 'News Agenda active', 5000, view('Agenda'));
    await q.waitFor(() => (document.querySelector('main [data-page="news"] .news-agenda')?.textContent || '').toLowerCase().includes('synthetic briefing'),
      'News current briefing response rendered', 10000);
    const deskContent = await q.js(() => ({ briefing: (document.querySelector('main [data-page="news"] .news-agenda')?.textContent || '').toLowerCase().includes('synthetic briefing') }));
    assert.ok(deskContent.briefing, 'News Agenda briefing response did not render');
    const before = await q.pageState(), counts = await q.counts();
    await q.toggle('modern'); const modern = await q.pageState();
    await q.capture('news-desk-after-24h-wire-filter');
    await q.toggle('classic');
    const after = await q.pageState(), requestDeltas = deltaCounts(counts, await q.counts());
    assert.equal(before.id, after.id); assert.deepEqual(before.choices, after.choices); assert.deepEqual(modern.choices, before.choices); assert.deepEqual(requestDeltas, {});
    await markViews();
    const deskRetained = await q.js(s => document.querySelector(s)?.getAttribute('aria-pressed') === 'true', view('Agenda'));
    assert.ok(deskRetained, 'News Agenda view selection should survive presentation changes');

    // The feed filter bar is unmounted while Agenda is selected. Re-enter Feed after
    // mode switching, then inspect its own date-filter segment.
    await q.click(view('Feed'));
    await q.waitFor(s => document.querySelector(s)?.getAttribute('aria-pressed') === 'true'
      && !!document.querySelector('main [data-page="news"] .news-list'), 'News Feed view restored after mode changes', 5000, view('Feed'));
    const retained = await q.js(deskSelectedBeforeWireReturn => {
      const period = [...document.querySelectorAll('main [data-page="news"] .news-filters [aria-label="Period"] button')]
        .find(el => /^24 hours$/i.test(el.innerText.trim()));
      if (period) period.setAttribute('data-qa-news-period-after-mode', 'true');
      return { deskSelectedBeforeWireReturn,
        wireSelected: document.querySelector('main [data-page="news"] [data-qa="news-view"] button[aria-pressed="true"]')?.innerText.trim() === 'Feed',
        periodButton: period?.innerText.trim() || null,
        periodSelected: period?.getAttribute('aria-pressed') === 'true',
        filteredWireRows: document.querySelectorAll('main [data-page="news"] .news-list .news-row').length };
    }, deskRetained);
    assert.ok(retained.periodButton && retained.periodSelected && retained.filteredWireRows === 3,
      `News Feed date-filter selection and results should survive presentation changes: ${JSON.stringify(retained)}`);
    await q.capture('news-wire-after-24h-filter-mode-switch');
    return { assertionResults: { syntheticItemsPopulated: wireCounts.total === 4, dateFilterActuallyExcludesArchived: filteredBefore.rows === 3,
      staleProviderEvidenceAvailable: !!filteredBefore.providerStatus,
      deskViewRetained: retained.deskSelectedBeforeWireReturn,
      wireFilterRetainedAfterReenteringWire: retained.wireSelected && retained.periodSelected && retained.filteredWireRows === 3,
      noRefreshOnModeSwitch: Object.keys(requestDeltas).length === 0, wrapperStable: before.id === after.id },
      wireCounts, filteredBefore, deskContent, retained, feedCount: wireCounts.total, requestDeltas };
  });
  await q.executeScenario('fundamentals', 'selected-model-and-variant-draft-retain', async () => {
    await q.visit('/fundamentals');
    await q.waitFor(() => !!document.querySelector('main [data-page="fundamentals"] tbody tr'), 'Fundamentals synthetic model row', 8000);
    const rowCount = await q.js(() => document.querySelectorAll('main [data-page="fundamentals"] tbody tr').length);
    await q.click('main [data-page="fundamentals"] tbody tr:first-child');
    await q.waitFor(() => !!document.querySelector('main [data-page="fundamentals"] [data-testid="sector-valuation-status"]'), 'Fundamentals selected model detail');
    const variant = await q.js(() => document.querySelector('main [data-page="fundamentals"] input[aria-label]')?.getAttribute('aria-label') || null);
    assert.ok(variant, 'Fundamentals variant draft input unavailable');
    await q.setValue(`main [data-page="fundamentals"] input[aria-label="${cssEscape(variant)}"]`, 'QA retained variant draft');
    const before = await q.pageState(), counts = await q.counts();
    const writesBefore = (await q.snapshot()).writes.length;
    await q.toggle('modern'); await q.toggle('classic');
    const after = await q.pageState(), requestDeltas = deltaCounts(counts, await q.counts());
    assert.equal(before.id, after.id); assert.deepEqual(before.controls, after.controls); assert.deepEqual(requestDeltas, {});
    assert.ok(after.controls.some(control => control.value === 'QA retained variant draft'));
    const writesAfter = (await q.snapshot()).writes.length, writeDelta = writesAfter - writesBefore;
    assert.equal(writeDelta, 0, 'Fundamentals presentation mode changes must not dispatch a mutation');
    return { assertionResults: { usableAndBlockedRowsRendered: rowCount >= 2, detailStateRetained: true, variantDraftRetained: true, noMutationSent: writeDelta === 0, noModeRequests: true }, rowCount, requestDeltas, writesBefore, writesAfter, writeDelta };
  });
}

async function runJournalAndConfig(q) {
  await q.executeScenario('journal', 'nested-journal-draft-and-tab-retain', async () => {
    await q.visit('/mandato');
    await q.click('#tab-diario');
    await q.waitFor(() => document.querySelector('#tab-diario')?.getAttribute('aria-selected') === 'true' && !!document.querySelector('#panel-diario .journal-page'), 'Journal mounted in Mandato');
    await q.waitFor(() => !!document.querySelector('#panel-diario .journal-title-input'), 'Journal draft title field');
    await q.click('#panel-diario .journal-primary');
    await q.setValue('#panel-diario .journal-title-input', 'Synthetic retained new journal draft');
    await q.setValue('#panel-diario textarea[aria-label]', 'Synthetic body retained across mode change.');
    const before = await q.pageState(), counts = await q.counts();
    await q.toggle('modern'); await q.toggle('classic');
    const after = await q.pageState(), requestDeltas = deltaCounts(counts, await q.counts());
    assert.equal(before.id, after.id); assert.ok(after.controls.some(item => item.value === 'Synthetic retained new journal draft'));
    assert.ok(after.controls.some(item => item.value === 'Synthetic body retained across mode change.'));
    assert.deepEqual(requestDeltas, {});
    const journalWrites = (await q.snapshot()).writes.filter(item => item.route.startsWith('/journal'));
    assert.equal(journalWrites.length, 0);
    return { assertionResults: { nestedDraftRetained: true, journalMutationNotSent: true, noModeRequests: true, wrapperStable: true }, requestDeltas, journalWrites };
  });

  await q.executeScenario('settings', 'config-modal-focus-escape-cancel-and-mode-choice-reachable', async () => {
    q.lastSettingsCleanup = null;
    try {
    await q.visit('/dashboard');
    const openButton = await q.js(() => !!document.querySelector('[aria-label="Settings"]'));
    if (openButton) await q.click('[aria-label="Settings"]');
    else await q.js(() => window.dispatchEvent(new Event('bb:settings')));
    await q.waitFor(() => !!document.querySelector('.f11v[role="dialog"][aria-modal="true"]'), 'Settings dialog');
    const openEvidence = await q.js(() => ({ dialog: !!document.querySelector('.f11v[role="dialog"][aria-modal="true"]'), opener: document.querySelector('[aria-label="Settings"]')?.outerHTML.slice(0, 180), focus: document.activeElement?.outerHTML?.slice(0, 180) || '' }));
    const backupButton = await q.js(() => [...document.querySelectorAll('.f11v button')].find(el => /delete/i.test(`${el.getAttribute('aria-label') || ''} ${el.title || ''}`))?.getAttribute('aria-label') || null);
    let confirmEvidence = { confirmationAvailable: false, escapeCancel: null, focusRestore: null, mutationCount: 0 };
    if (backupButton) {
      const escaped = cssEscape(backupButton);
      const selector = `.f11v button[aria-label="${escaped}"]`;
      const writesBefore = (await q.snapshot()).writes.filter(item => item.method === 'DELETE').length;
      await q.click(selector);
      await q.waitFor(() => !!document.querySelector('.f11-ask[role="dialog"][aria-modal="true"]'), 'Settings destructive confirmation');
      const dialogFocus = await q.js(() => ({ activeText: (document.activeElement?.innerText || document.activeElement?.getAttribute('aria-label') || '').trim().slice(0, 100), cancelAutoFocused: document.activeElement?.textContent?.trim().toLowerCase() === 'cancel' || document.activeElement?.textContent?.trim().toLowerCase() === 'annulla' }));
      await q.key('Escape');
      await q.waitFor(() => !document.querySelector('.f11-ask[role="dialog"]'), 'confirmation Escape cancellation');
      const readFocusAfterEscape = () => q.js(sel => {
        const active = document.activeElement, trigger = document.querySelector(sel);
        return { active: active ? { tag: active.tagName, id: active.id || '', ariaLabel: active.getAttribute('aria-label') || '',
          text: (active.innerText || active.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 120) } : null,
          trigger: trigger ? { tag: trigger.tagName, id: trigger.id || '', ariaLabel: trigger.getAttribute('aria-label') || '',
            text: (trigger.innerText || trigger.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 120) } : null,
          settingsDialogOpen: !!document.querySelector('.f11v[role="dialog"]') };
      }, selector);
      const focusImmediatelyAfterEscape = await readFocusAfterEscape();
      let restored = false, focusRestoreError = null;
      try {
        await q.waitFor(sel => document.activeElement === document.querySelector(sel),
          'Settings confirmation restores trigger focus', 2000, selector);
        restored = true;
      } catch (error) { focusRestoreError = String(error?.message || error); }
      const focusAfterEscape = await readFocusAfterEscape();
      const writesAfter = (await q.snapshot()).writes.filter(item => item.method === 'DELETE').length;
      confirmEvidence = { confirmationAvailable: true, dialogFocus, focusImmediatelyAfterEscape, focusAfterEscape,
        focusRestoreError, escapeCancel: true, focusRestore: restored, mutationCount: writesAfter - writesBefore };
      assert.equal(writesAfter, writesBefore, 'Escape should cancel without a delete request');
      assert.ok(restored, `focus should return to destructive action trigger after Escape: ${JSON.stringify(confirmEvidence)}`);
    }
    await q.key('Escape');
    await q.waitFor(() => !document.querySelector('.f11v[role="dialog"]'), 'Settings Escape close');
    const settingsFocusRestored = await q.js(() => document.activeElement === document.querySelector('[aria-label="Settings"]'));
    assert.ok(openEvidence.dialog);
    assert.equal(settingsFocusRestored, true, 'Settings close should restore focus to opener');
    assert.equal(confirmEvidence.confirmationAvailable, true, 'Settings confirmation control was not available in this fixture');
    assert.ok(confirmEvidence.escapeCancel && confirmEvidence.focusRestore && confirmEvidence.mutationCount === 0);
    return { assertionResults: { dialogOpens: true, focusRestoredOnClose: settingsFocusRestored, confirmationEscapeCancelsWithoutMutation: true,
      confirmationFocusRestored: confirmEvidence.focusRestore, confirmTriggerReachable: true }, openEvidence, confirmEvidence,
      keyboard: 'CDP Input.dispatchKeyEvent, so browser Tab/Escape defaults and focus movement are exercised' };
    } finally {
      try { q.lastSettingsCleanup = await q.cleanupSettingsDialogs(); }
      catch (cleanupError) { q.lastSettingsCleanup = { error: String(cleanupError?.stack || cleanupError) }; }
    }
  });
}

async function runPresentationRecovery(q) {
  // The desktop shell schedules one startup notification read on a native
  // timeout. Let it settle before recovery scenarios begin so a late, unrelated
  // shell request cannot be mistaken for a presenter-triggered read.
  await q.pause(5500);
  await q.settleRequests('startup shell notification request', 6000);

  q.progress('recovery-stage-start', { page: 'performance', scenario: 'presenter-fault-recovery' });
  const performanceResult = await q.executeScenario('performance', 'presenter-fault-recovery', async () => {
    await q.toggle('classic'); await q.visit('/performance');
    const R = 'main [data-page="performance"]';
    await q.waitFor(() => document.querySelectorAll('main [data-page="performance"] [data-qa="perf-range"] button').length === 5, 'Performance period selector');
    await q.waitFor(() => { const card = document.querySelector('main [data-page="performance"] [data-qa="perf-attribution"]'); return !!card && !card.querySelector('.perf-state:not(.is-error)'); }, 'Performance attribution initial request settles', 15000);
    const before = await q.pageState();
    const attributionCount = period => q.js(async value => (await fetch('/__fixture').then(response => response.json())).requests
      .filter(item => item.method === 'GET' && item.route === '/portfolio/attribution' && item.query.period === value).length, period);
    const before30d = await attributionCount('30D');
    const control = await q.js(() => {
      const button = [...document.querySelectorAll('main [data-page="performance"] [data-qa="perf-range"] button')].find(el => el.innerText.trim() === '1M');
      if (button) button.setAttribute('data-qa-recovery-range', 'true');
      return button?.innerText.trim() || null;
    });
    assert.equal(control, '1M', 'Performance recovery test selects the real monthly period');
    await q.click(`${R} [data-qa-recovery-range="true"]`);
    await q.waitFor(() => document.querySelector('main [data-page="performance"] [data-qa-recovery-range="true"]')?.getAttribute('aria-pressed') === 'true', 'monthly period selected before recovery fault', 5000);
    // The global period drives attribution: 1M maps to the backend's 30D window, exactly one new read.
    await q.waitFor(async () => {
      const fixture = await fetch('/__fixture').then(response => response.json());
      return fixture.requests.some(item => item.method === 'GET' && item.route === '/portfolio/attribution' && item.query.period === '30D');
    }, '30D attribution request reaches synthetic fixture', 10000);
    await q.settleRequests('Performance 30D attribution response', 6000);
    await q.waitFor(() => { const card = document.querySelector('main [data-page="performance"] [data-qa="perf-attribution"]'); return !!card && !card.querySelector('.perf-state:not(.is-error)'); }, 'attribution settles after the period change', 10000);
    assert.equal(await attributionCount('30D'), before30d + 1, 'selecting 1M issues one 30D attribution request');
    const readView = () => q.js(() => {
      const root = document.querySelector('main [data-page="performance"]');
      return { active: [...root.querySelectorAll('[data-qa="perf-range"] button')].find(b => b.getAttribute('aria-pressed') === 'true')?.innerText.trim() || null,
        heroLabel: root.querySelector('.perf-hero-label')?.textContent.trim() || null,
        line: root.querySelector('.perf-line')?.getAttribute('d') || null,
        attributionRows: root.querySelectorAll('[data-qa="perf-attribution"] .perf-arow:not(.perf-arow-head)').length };
    });
    const viewBefore = await readView();
    assert.ok(viewBefore.line, `Performance chart is drawn before the fault: ${JSON.stringify(viewBefore)}`);
    const armedState = await q.pageState(), beforeCounts = await q.counts();
    await q.toggle('modern');
    await q.armPresenterFault({ className: 'performance-page' });
    await q.toggle('classic');
    await q.toggle('modern');
    await q.waitFor(() => !!document.querySelector('.bb-interface-recovery [data-theme-recover="light"]'), 'faulted presentation recovery boundary', 7000);
    const faultEvidence = await q.faultTrips();
    assert.equal(faultEvidence.trips, 1, 'served presenter instrumentation must trigger exactly once for the selected virtual root');
    assert.ok(faultEvidence.attempts >= 1, 'fault instrumentation must remain latched through React render retries');
    await q.disarmPresenterFault();
    const recoveryClick = await q.click('.bb-interface-recovery [data-theme-recover="light"]');
    await q.waitFor(() => document.querySelector('[data-theme-choice="light"]')?.getAttribute('aria-pressed') === 'true', 'global Classic recovery');
    await q.waitFor(() => !!document.querySelector('main [data-page="performance"] .perf-line'), 'recovered Performance chart', 10000);
    const after = await q.pageState(), deltas = deltaCounts(beforeCounts, await q.counts());
    assert.equal(after.id, armedState.id, 'page/controller wrapper should survive presenter recovery');
    assert.deepEqual(after.controls, armedState.controls, 'page-owned controls should survive presenter recovery');
    assert.deepEqual(after.choices, armedState.choices, 'page-owned selection should survive presenter recovery');
    assert.deepEqual(deltas, {}, 'presenter recovery must not refetch data');
    assert.ok(recoveryClick.hitTest);
    const viewAfter = await readView();
    assert.deepEqual(viewAfter, viewBefore, 'period, headline, drawn series and attribution survive the presenter fault');
    return { assertionResults: { faultInjectedIntoServedBundleOnce: faultEvidence.trips === 1, fallbackVisible: true, recoveryHitTested: recoveryClick.hitTest,
      pageControllerAndControlStatePreserved: after.id === armedState.id && JSON.stringify(after.controls) === JSON.stringify(armedState.controls),
      periodDrivesAttribution: true, selectedPeriodAndSeriesPreserved: JSON.stringify(viewAfter) === JSON.stringify(viewBefore),
      noRecoveryRequests: Object.keys(deltas).length === 0 },
      faultTrips: faultEvidence.trips, faultTarget: faultEvidence.lastTarget, viewBefore, viewAfter,
      before: summarizeState(before), armedState: summarizeState(armedState), recovered: summarizeState(after), requestDeltas: deltas,
      faultMessage: 'qa-controlled-presenter-fault', instrumentationNote: 'test-only vnode-targeted transform of compiled deferred render callbacks in the served production JS asset' };
  });
  q.progress('recovery-stage-result', { page: 'performance', status: performanceResult?.status || 'unknown' });

  const recoverLocally = async spec => {
    if (process.env.BB_PAGES_ACTIONS_RECOVERY_PERFORMANCE_ONLY === '1' && spec.page !== 'performance') {
      q.progress('recovery-diagnostic-skip', { page: spec.page, scenario: spec.scenario,
        filter: 'recovery-performance-only', acceptanceReady: false });
      return null;
    }
    q.progress('recovery-stage-start', { page: spec.page, scenario: spec.scenario });
    const result = await q.executeScenario(spec.page, spec.scenario, async () => {
    const restoreWrites = spec.writeRoute && Object.hasOwn(actionData.writes, spec.writeRoute)
      ? { [spec.writeRoute]: actionData.writes[spec.writeRoute] } : {};
    try {
      await q.toggle('classic');
      await q.visit(spec.route);
      await q.waitFor(selector => !!document.querySelector(selector), `${spec.page} local recovery view`, 15000, spec.readySelector);
      await q.settleRequests(`${spec.page} pre-fault reads`, 6000);
      await spec.prepare();

      const beforeValue = await spec.readValue();
      const pageBefore = await q.pageState();
      const countsBefore = await q.counts();
      const runtimeBefore = normalizeRuntime(await q.runtimeSnapshot());
      if (spec.request) {
        const requestKey = `${spec.request.method} ${spec.request.route}`;
        assert.equal(countsBefore[requestKey], spec.priorRequests || 1, `${spec.page} expected one controlled pending request before the presentation fault`);
      }

      await q.toggle('modern');
      await q.armPresenterFault(spec.target);
      await q.toggle('classic');
      await q.toggle('modern');
      await q.waitFor(() => !!document.querySelector('.bb-interface-recovery [data-theme-recover="light"]'),
        `${spec.page} local presentation fault boundary`, 7000);
      const fault = await q.faultTrips();
      assert.equal(fault.trips, 1, `${spec.page} local presenter fault should trip once`);
      assert.ok(fault.attempts >= 1, `${spec.page} local fault must remain latched through React render retries`);
      assert.deepEqual(fault.lastTarget, spec.target, `${spec.page} fault must target its own presentation surface`);
      await q.disarmPresenterFault();
      const faultPage = await q.pageState();
      assert.equal(faultPage.id, pageBefore.id, `${spec.page} route controller stays mounted under the local boundary`);
      await q.settleRequests(`${spec.page} shell and page reads before capture`, 6000);
      await q.capture(`recovery-${spec.page}-local-fault`, { viewports: ['1920x1080'] });

      const recoveryClick = await q.click('.bb-interface-recovery [data-theme-recover="light"]');
      await q.waitFor(() => document.querySelector('[data-theme-choice="light"]')?.getAttribute('aria-pressed') === 'true',
        `${spec.page} local fallback returns to Classic`, 5000);
      await q.waitFor(selector => !!document.querySelector(selector), `${spec.page} local presenter restored`, 7000, spec.readySelector);
      const afterClassic = await spec.readValue();
      assert.deepEqual(afterClassic, beforeValue, `${spec.page} draft or pending operation must survive local fault recovery`);
      const pageAfterClassic = await q.pageState();
      assert.equal(pageAfterClassic.id, pageBefore.id, `${spec.page} page/controller wrapper identity is preserved`);
      const classicDeltas = deltaCounts(countsBefore, await q.counts());
      assert.deepEqual(classicDeltas, {}, `${spec.page} mode switch, fault, and recovery add no API requests`);
      assert.deepEqual(normalizeRuntime(await q.runtimeSnapshot()), runtimeBefore,
        `${spec.page} local presentation fault must preserve timers and global listeners`);
      assert.ok(recoveryClick.hitTest, `${spec.page} recovery action must be reached by trusted input`);

      await q.toggle('modern');
      const afterModern = await spec.readValue();
      assert.deepEqual(afterModern, beforeValue, `${spec.page} state survives re-entering Modern after local recovery`);
      const modernDeltas = deltaCounts(countsBefore, await q.counts());
      assert.deepEqual(modernDeltas, {}, `${spec.page} recovery mode toggle adds no API requests`);
      await q.capture(`recovery-${spec.page}-local-restored-modern`, { viewports: ['1920x1080'] });
      await q.toggle('classic');
      assert.deepEqual(await spec.readValue(), beforeValue, `${spec.page} state remains after the final Classic switch`);
      assert.deepEqual(deltaCounts(countsBefore, await q.counts()), {}, `${spec.page} presentation toggles remain request-free`);

      let completion = null;
      if (spec.waitCompleted) completion = await spec.waitCompleted();
      if (spec.request) {
        const requests = (await q.snapshot()).requests.filter(item => item.method === spec.request.method && item.route === spec.request.route);
        assert.equal(requests.length, spec.priorRequests || 1, `${spec.page} held operation must be dispatched exactly once`);
      }
      return { assertionResults: { localPresenterFaultInjectedOnce: true, correctSurfaceWasFaulted: true,
        controllerIdentityPreserved: true, stateOrInflightOperationRetained: true, allPresentationTogglesRequestFree: true,
        timersAndGlobalListenersStable: true, recoveryReachedByTrustedInput: true,
        heldOperationCompletedAfterRecovery: !spec.waitCompleted || !!completion },
        target: spec.target, request: spec.request || null, beforeValue, afterClassic, afterModern,
        requestDeltas: { classicRecovery: classicDeltas, modernRecovery: modernDeltas }, completion,
        faultTrips: fault.trips, recoveryHitTest: recoveryClick.hitTest,
        clock: 'fixture-controlled intervals; no interval tick was injected during this scenario' };
    } finally {
      const setWrite = Object.keys(restoreWrites).length ? restoreWrites : undefined;
      await q.fixture({ ...(spec.readRoutes ? { clearReads: spec.readRoutes } : {}), ...(setWrite ? { setWrite } : {}) });
    }
    });
    q.progress('recovery-stage-result', { page: spec.page, scenario: spec.scenario, status: result?.status || 'unknown' });
    return result;
  };

  await recoverLocally({ page: 'trades', scenario: 'trade-local-presenter-fault-retains-draft', route: '/trades',
    target: { className: 'f7c' }, readySelector: 'main [data-page="trades"] #f7-nt',
    prepare: async () => {
      const note = 'Synthetic local-boundary recovery trade draft.';
      await q.typeText('main [data-page="trades"] #f7-nt', note);
    }, readValue: async () => q.js(() => ({ note: document.querySelector('main [data-page="trades"] #f7-nt')?.value || '',
      ticker: document.querySelector('main [data-page="trades"] #f7-tk')?.value || '' })) });

  await recoverLocally({ page: 'mandato', scenario: 'mandato-local-presenter-fault-retains-preview', route: '/mandato',
    target: { className: 'mandato-form' }, readySelector: '#panel-mandato #m-broker',
    request: { method: 'POST', route: '/mandato/anteprima' }, writeRoute: '/mandato/anteprima',
    prepare: async () => {
      await q.typeText('#panel-mandato #m-broker', 'Synthetic recovery preview broker');
      await q.fixture({ setWrite: { '/mandato/anteprima': { body: { testo: 'Synthetic mandate preview returned after local recovery.',
        impronta: 'qa-local-boundary-preview', origine: 'synthetic_fixture', output_language: 'en' }, delayMs: 20000 } } });
      await q.click('#panel-mandato button[data-action="preview"]');
      await q.waitFor(() => document.querySelector('#panel-mandato button[data-action="preview"]')?.disabled,
        'Mandato preview pending before local recovery', 5000);
      await q.waitFor(async () => {
        const state = await fetch('/__fixture').then(response => response.json());
        return state.requests.some(item => item.method === 'POST' && item.route === '/mandato/anteprima');
      }, 'Mandato preview reaches synthetic fixture', 5000);
    },
    readValue: async () => q.js(() => ({ broker: document.querySelector('#panel-mandato #m-broker')?.value || '',
      busy: !!document.querySelector('#panel-mandato button[data-action="preview"]')?.disabled,
      local: document.querySelector('#panel-mandato .preview-message')?.textContent || '' })),
    waitCompleted: async () => {
      try {
        await q.waitFor(() => /Synthetic mandate preview returned after local recovery/.test(document.querySelector('#panel-mandato .mandato-preview')?.innerText || ''),
          'Mandato preview returns after local recovery', 30000);
      } catch (error) {
        const visible = await q.js(() => ({ preview: document.querySelector('#panel-mandato .mandato-preview')?.innerText || null,
          message: document.querySelector('#panel-mandato .preview-message')?.innerText || null,
          busy: !!document.querySelector('#panel-mandato button[data-action="preview"]')?.disabled,
          broker: document.querySelector('#panel-mandato #m-broker')?.value || null }));
        const fixture = await q.snapshot();
        const request = fixture.requests.filter(item => item.method === 'POST' && item.route === '/mandato/anteprima');
        throw new Error(`${error?.message || error}; visible=${JSON.stringify(visible)} fixtureRequests=${JSON.stringify(request)}`);
      }
      return { previewRendered: true, writes: (await q.snapshot()).requests.filter(item => item.method === 'POST' && item.route === '/mandato/anteprima').length };
    } });

  await recoverLocally({ page: 'journal', scenario: 'journal-local-presenter-fault-retains-draft', route: '/mandato',
    target: { className: 'journal-page' }, readySelector: '#tab-diario',
    prepare: async () => {
      await q.click('#tab-diario');
      await q.waitFor(() => !!document.querySelector('#panel-diario .journal-page'), 'Journal nested route appears');
      await q.click('#panel-diario .journal-library-head .journal-primary');
      await q.waitFor(() => !!document.querySelector('#panel-diario .journal-title-input'), 'Journal draft editor opens');
      await q.typeText('#panel-diario .journal-title-input', 'Synthetic retained journal title');
      await q.typeText('#panel-diario .journal-body-label textarea', 'Synthetic journal body retained across local fault recovery.');
    },
    readValue: async () => q.js(() => ({ title: document.querySelector('#panel-diario .journal-title-input')?.value || '',
      body: document.querySelector('#panel-diario .journal-body-label textarea')?.value || '',
      saveDisabled: !!document.querySelector('#panel-diario .journal-editor-actions .journal-primary')?.disabled })) });

  const volCatalogRoute = '/options/expiry_catalog/SYNV';
  await recoverLocally({ page: 'vol', scenario: 'vol-local-presenter-fault-retains-catalog-request', route: '/vol',
    target: { className: 'vol-workbench' }, readySelector: 'main [data-page="vol"] #va-ticker',
    request: { method: 'GET', route: volCatalogRoute }, readRoutes: [volCatalogRoute],
    prepare: async () => {
      await q.fixture({ setRead: { [volCatalogRoute]: { body: { ticker: 'SYNV', expirations: ['2026-12-18'], complete: true,
        next_after: null, requests_used: 1, error: null, _timestamp: '2026-09-30T12:00:00Z' }, delayMs: 20000 } } });
      await q.typeText('main [data-page="vol"] #va-ticker', 'SYNV');
      await q.click('main [data-page="vol"] form.va-ticker button[type="submit"]');
      await q.waitFor(() => !!document.querySelector('main [data-page="vol"] .vd-loading'), 'Vol catalog loading before local recovery');
      await q.waitFor(async () => {
        const state = await fetch('/__fixture').then(response => response.json());
        return state.requests.some(item => item.method === 'GET' && item.route === '/options/expiry_catalog/SYNV');
      }, 'Vol catalog reaches synthetic fixture', 5000);
    },
    readValue: async () => q.js(() => ({ ticker: document.querySelector('main [data-page="vol"] #va-ticker')?.value || '',
      loading: !!document.querySelector('main [data-page="vol"] .vd-loading'),
      catalog: document.querySelector('main [data-page="vol"] .vd-catalog-summary')?.textContent?.trim() || '' })),
    waitCompleted: async () => {
      await q.waitFor(() => !!document.querySelector('main [data-page="vol"] .vd-catalog-summary'), 'Vol catalog completes after local recovery', 30000);
      const catalog = await q.js(() => document.querySelector('main [data-page="vol"] .vd-catalog-summary')?.textContent?.trim() || '');
      const expiryEvidence = await q.js(() => ({
        optionLabel: document.querySelector('main [data-page="vol"] select option[value="2026-12-18"]')?.textContent?.trim() || null,
      }));
      assert.ok(expiryEvidence.optionLabel, `synthetic expiry was not rendered in the catalog selector: ${catalog}`);
      assert.match(expiryEvidence.optionLabel, /2026/, `catalog option must show the synthetic year: ${expiryEvidence.optionLabel}`);
      return { catalogText: catalog, expiryEvidence, requests: (await q.snapshot()).requests.filter(item => item.method === 'GET' && item.route === '/options/expiry_catalog/SYNV').length };
    } });

  // Filing page (phase E): the unsaved profile JSON and the held PUT live in FilingPage, the
  // presenter (VistaFiling, root .bbn-filing) is faulted and restored without losing either.
  const filingSaveRoute = '/filings/SYN1/profile';
  const FP = 'main [data-page="filing"]';
  const filingSave = `${FP} [data-filing-azione="salva-profilo"]`, filingProfile = `${FP} [data-filing-input="profilo"]`;
  await recoverLocally({ page: 'filing', scenario: 'local-presenter-fault-retains-profile-save', route: '/filing',
    target: { className: 'bbn-filing', expandComponent: true }, readySelector: `${FP} .bbn-filing [data-filing-ticker="SYN1"]`,
    request: { method: 'PUT', route: filingSaveRoute }, writeRoute: filingSaveRoute,
    prepare: async () => {
      await q.click(`${FP} [data-filing-ticker="SYN1"]`);
      await q.waitFor(sel => !!document.querySelector(sel), 'Filing detail of SYN1', 10000, `${FP} [data-filing-dettaglio="SYN1"] [data-filing-azione="profilo"]`);
      await q.settleRequests('Filing detail reads', 6000);
      await q.click(`${FP} [data-filing-azione="profilo"]`);
      await q.waitFor(sel => !!document.querySelector(sel), 'Filing profile editor opens', 5000, filingProfile);
      const draft = JSON.stringify({ identity: { ticker: 'SYN1', fixture: true }, source: { name: 'Synthetic archive', url: 'https://example.invalid' },
        sections: ['Synthetic recovery profile draft'] }, null, 2);
      await q.typeText(filingProfile, draft);
      await q.fixture({ setWrite: { [filingSaveRoute]: { body: { ticker: 'SYN1', enabled: false,
        interval_hours: 168, qualitative_enabled: false, synthetic: true }, delayMs: 20000 } } });
      await q.click(filingSave);
      await q.waitFor(selector => document.querySelector(selector)?.disabled, 'Filing profile save pending before local recovery', 5000, filingSave);
      await q.waitFor(async () => {
        const state = await fetch('/__fixture').then(response => response.json());
        return state.requests.some(item => item.method === 'PUT' && item.route === '/filings/SYN1/profile');
      }, 'Filing profile reaches synthetic fixture', 5000);
    },
    readValue: async () => q.js((profile, save, root) => ({ profile: document.querySelector(profile)?.value || '',
      saving: !!document.querySelector(save)?.disabled,
      ticker: document.querySelector(root + ' [data-filing-dettaglio]')?.getAttribute('data-filing-dettaglio') || '' }), filingProfile, filingSave, FP),
    waitCompleted: async () => {
      await q.waitFor(sel => { const button = document.querySelector(sel); return !!button && !button.disabled; },
        'Filing profile save completes after local recovery', 30000, filingSave);
      await q.waitFor(root => /Profile saved/.test(document.querySelector(root + ' [data-filing-avviso="ok"]')?.textContent || ''),
        'Filing profile saved notice', 10000, FP);
      const writes = (await q.snapshot()).requests.filter(item => item.method === 'PUT' && item.route === filingSaveRoute);
      assert.equal(writes.length, 1, 'Filing profile save reaches fixture exactly once');
      const forbidden = (await q.snapshot()).requests.filter(item => item.method === 'POST' && /^\/filings\/[^/]+\/(?:ai-proposal|refresh)$/.test(item.route));
      assert.deepEqual(forbidden, [], 'profile save and recovery never start an AI proposal or a check');
      return { profileStillVisible: !!(await q.js(sel => document.querySelector(sel)?.value.includes('Synthetic recovery profile draft'), filingProfile)),
        writes: writes.length, savedPayload: writes[0].input };
    } });

  const memoQuery = 'fixture recovery query';
  const memoRoute = '/memos/search/fixture%20recovery%20query';
  await recoverLocally({ page: 'memos', scenario: 'memo-local-presenter-fault-retains-search-request', route: '/memos',
    target: { className: 'f9b-scrim' }, readySelector: 'main [data-page="memos"] .qcall',
    request: { method: 'GET', route: memoRoute }, readRoutes: [memoRoute],
    prepare: async () => {
      await q.click('main [data-page="memos"] .qcall');
      await q.waitFor(() => !!document.querySelector('main [data-page="memos"] .f9b-scrim .mq input'), 'Memo semantic search opens');
      await q.fixture({ setRead: { [memoRoute]: { body: { query: memoQuery, results: [] }, delayMs: 12000 } } });
      const selector = 'main [data-page="memos"] .f9b-scrim .mq input';
      await q.typeText(selector, memoQuery);
      const keyEvidence = await q.key('Enter');
      assert.ok(keyEvidence.documentFocused, 'Memo search Enter needs real document focus');
      await q.waitFor(() => /searching/i.test(document.querySelector('main [data-page="memos"] .f9b-scrim .mq .ms')?.textContent || ''),
        'Memo semantic search enters pending state');
      await q.waitFor(async () => {
        const state = await fetch('/__fixture').then(response => response.json());
        return state.requests.some(item => item.method === 'GET' && item.route === '/memos/search/fixture%20recovery%20query');
      }, 'Memo search reaches synthetic fixture', 5000);
    },
    readValue: async () => q.js(() => ({ query: document.querySelector('main [data-page="memos"] .f9b-scrim .mq input')?.value || '',
      status: document.querySelector('main [data-page="memos"] .f9b-scrim .mq .ms')?.textContent?.trim() || '',
      modalOpen: !!document.querySelector('main [data-page="memos"] .f9b-scrim') })),
    waitCompleted: async () => {
      await q.waitFor(() => {
        const status = document.querySelector('main [data-page="memos"] .f9b-scrim .mq .ms')?.textContent || '';
        return !!status && !/searching/i.test(status);
      }, 'Memo search result arrives after local recovery', 16000);
      const result = await q.js(() => ({ status: document.querySelector('main [data-page="memos"] .f9b-scrim .mq .ms')?.textContent?.trim() || '',
        results: document.querySelectorAll('main [data-page="memos"] .f9b-scrim .masse').length }));
      assert.equal(result.results, 0, 'synthetic empty search result renders as empty, not as loading');
      return result;
    } });
}

function summarizeState(state) { return { id: state.id, page: state.page, controls: state.controls, choices: state.choices }; }
function normalizeRuntime(snapshot) {
  if (!snapshot) return null;
  return { intervals: snapshot.intervals.map(({ id, delay, ticks, callbackId }) => ({ id, delay, ticks, callbackId }))
      .sort((a, b) => a.id - b.id),
    listeners: snapshot.listeners.map(({ target, type, callbackId, capture, once }) => ({ target, type, callbackId, capture, once }))
      .sort((a, b) => `${a.target}:${a.type}:${a.callbackId}:${a.capture}`.localeCompare(`${b.target}:${b.type}:${b.callbackId}:${b.capture}`)) };
}
function compareRuntimeAcrossModes(before, modern, classic, { referencePage } = {}) {
  const chartTypes = new Set(['document:mousedown', 'document:touchstart', 'window:resize']);
  const snapshots = [before, modern, classic];
  const intervals = snapshots.map(item => item.intervals.map(({ id, delay, ticks, callbackId }) => ({ id, delay, ticks, callbackId }))
    .sort((a, b) => a.id - b.id));
  const isChartListener = item => chartTypes.has(`${item.target}:${item.type}`);
  const nonChart = snapshots.map(item => item.listeners.filter(item => !isChartListener(item))
    .map(({ target, type, callbackId, capture, once, origin }) => ({ target, type, callbackId, capture, once, origin }))
    .sort((a, b) => `${a.target}:${a.type}:${a.callbackId}:${a.capture}`.localeCompare(`${b.target}:${b.type}:${b.callbackId}:${b.capture}`)));
  const chartCounts = snapshots.map(item => Object.fromEntries(Object.entries(item.listenerCounts || {})
    .filter(([key]) => chartTypes.has(key)).sort(([a], [b]) => a.localeCompare(b))));
  const chartOrigin = origin => {
    const locations = [...String(origin || '').matchAll(/(?:https?:\/\/[^\s)]+)?\/assets\/([^\s):]+\.js):(\d+):(\d+)/g)]
      .map(match => `${match[1]}:${match[2]}:${match[3]}`);
    return { locations, sourceSite: locations.at(-1) || null };
  };
  const isVerifiedChartOrigin = event => {
    const origin = String(event.origin || '');
    if (['mousedown', 'touchstart'].includes(event.type) && event.target === 'document') {
      // These registrations originate inside the shipped lightweight-charts
      // package's Pn.fp constructor, whose cleanup is exercised by chart theme
      // rebinding. Do not allow arbitrary app-bundle listeners by type alone.
      return /\/lightweight-charts\.production-[^:]+\.js:\d+:\d+/.test(origin) && /Pn\.fp/.test(origin);
    }
    if (event.type === 'resize' && event.target === 'window') {
      // Source-attribution guard for TerminalChart.tsx: the registered resize
      // callback owns the ResizeObserver/DPR rebinding and saves/restores its
      // visible logical range. Locate the exact stack frame in the current
      // served bundle and verify both the listener pair and chart-range code
      // around it. A generic `index-*.js` match is deliberately insufficient.
      const location = [...origin.matchAll(/\/assets\/(index-[^/\s):]+\.js):(\d+):(\d+)/g)].at(-1);
      if (!location) return false;
      const [, filename, lineText, columnText] = location;
      const asset = path.join(root, 'dist/assets', filename);
      if (!fs.existsSync(asset)) return false;
      const lines = fs.readFileSync(asset, 'utf8').split('\n');
      const lineNumber = Number(lineText), column = Number(columnText);
      const line = lines[lineNumber - 1] || '';
      const callOffset = line.indexOf('window.addEventListener(`resize`,');
      if (callOffset < 0 || Math.abs(callOffset + 1 - column) > 120) return false;
      const listener = line.slice(callOffset).match(/^window\.addEventListener\(`resize`,([\w$]+)\)/)?.[1];
      if (!listener) return false;
      const around = line.slice(Math.max(0, callOffset - 2200), Math.min(line.length, callOffset + 1800));
      return around.includes('timeScale().getVisibleLogicalRange()')
        && around.includes('window.removeEventListener(`resize`,' + listener + ')');
    }
    return false;
  };
  const deltas = [];
  const phaseEvents = [];
  for (const [phase, left, right] of [['classic-to-modern', before, modern], ['modern-to-classic', modern, classic]]) {
    const start = phase === 'classic-to-modern' ? before.events.length : modern.events.length;
    const end = phase === 'classic-to-modern' ? modern.events.length : classic.events.length;
    const events = right.events.slice(start, end).filter(event => event.kind === 'listener-added' || event.kind === 'listener-removed');
    const unexpected = events.filter(event => !chartTypes.has(`${event.target}:${event.type}`));
    const unclassified = events.filter(event => !chartOrigin(event.origin).sourceSite || !isVerifiedChartOrigin(event));
    const balance = {};
    for (const event of events) {
      const evidence = chartOrigin(event.origin);
      const key = `${event.target}:${event.type}:${evidence.sourceSite || 'unknown'}`;
      balance[key] = (balance[key] || 0) + (event.kind === 'listener-added' ? 1 : -1);
    }
    const imbalanced = Object.entries(balance).filter(([, count]) => count !== 0);
    deltas.push({ phase, events, unexpected, unclassified, balance, imbalanced });
    phaseEvents.push(events);
  }
  // No page owns presentation-only timers any more (the Classic-only Dashboard
  // panels were removed): every interval keeps its identity across the switch.
  const intervalsStable = JSON.stringify(intervals[0]) === JSON.stringify(intervals[1]) && JSON.stringify(intervals[0]) === JSON.stringify(intervals[2]);
  const nonChartStable = JSON.stringify(nonChart[0]) === JSON.stringify(nonChart[1]) && JSON.stringify(nonChart[0]) === JSON.stringify(nonChart[2]);
  const chartCountsStable = JSON.stringify(chartCounts[0]) === JSON.stringify(chartCounts[1]) && JSON.stringify(chartCounts[0]) === JSON.stringify(chartCounts[2]);
  const chartChurnBalanced = deltas.every(item => item.unexpected.length === 0 && item.unclassified.length === 0 && item.imbalanced.length === 0);
  return { ok: intervalsStable && nonChartStable && chartCountsStable && chartChurnBalanced,
    intervalsStable, nonChartStable, chartCountsStable, chartChurnBalanced,
    chartClassification: 'Only document mousedown/touchstart registrations with a lightweight-charts Pn.fp source stack and window resize registrations whose exact compiled stack site resolves to TerminalChart logical-range resize code are permitted. Churn must balance per source site; all other listeners and fixture-controlled intervals retain exact identity.',
    chartCounts, phases: deltas.map(({ phase, unexpected, unclassified, balance, imbalanced, events }) => ({
      phase, eventCount: events.length, unexpected, unclassified, balance, imbalanced,
      eventEvidence: events.map(({ kind, target, type, callbackId, origin, createdAt }) => ({ kind, target, type, callbackId, createdAt,
        origin: chartOrigin(origin), chartOriginVerified: isVerifiedChartOrigin({ target, type, origin }) })),
    })) };
}
function deltaCounts(before, after) {
  const changes = {};
  for (const key of new Set([...Object.keys(before), ...Object.keys(after)])) {
    const delta = (after[key] || 0) - (before[key] || 0);
    if (delta) changes[key] = delta;
  }
  return changes;
}
function cssEscape(value) { return String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"'); }
function pageRouteMatches(id, route) {
  const prefixes = { settings: ['/tasks/', '/db/', '/health', '/fx', '/agents/list'], journal: ['/journal'],
    performance: ['/portfolio/analytics', '/portfolio/risk', '/portfolio'], watchlist: ['/favorites', '/market/quote'],
    market: ['/market/', FILING_SUMMARY_READ], news: ['/news/'], fundamentals: ['/fundamentals/', '/valuation/', FILING_SUMMARY_READ],
    filing: ['/filings'],
    factors: ['/portfolio/factors', '/portfolio/metrics', '/portfolio/risk'], backtest: ['/portfolio/montecarlo'],
    vol: ['/options/'], edge: ['/signals/'], agents: ['/agents/', '/consigliere/'], 'agent-progress': ['/agents/progress'],
    memos: ['/memos'], decisions: ['/decisions'], trades: ['/trade', '/positions/', '/portfolio/validate_ticker'],
    movements: ['/trades', '/cash/movements'], mandato: ['/mandato'], dashboard: ['/portfolio', '/prices/'] }[id] || [];
  return prefixes.some(prefix => prefix instanceof RegExp ? prefix.test(route) : route.startsWith(prefix));
}
// Market and Fundamentals show only the one-line filing summary: GET /filings/{t} and /filings/runs/{id}.
const FILING_SUMMARY_READ = /^\/filings\/(?:runs\/\d+|(?!novita$|runs$)[^/]+)$/;

if (require.main === module) run().catch(error => { console.error(error?.stack || error); process.exitCode = 1; });
module.exports = { run, renderer, runCore, runResearch, runJournalAndConfig, runPresentationRecovery, instrumentPresentationFault,
  auditVolCanvasReadbackAdvisory, CANVAS2D_READBACK_ADVISORY, PAGES, SPECIALISTS };
