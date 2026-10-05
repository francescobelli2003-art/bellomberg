// All-pages Classic/Modern Electron render audit. Screenshots prove that the
// app actually rendered through its production bundle; they do not imply a
// human visual review. All HTTP is served by the sibling ephemeral fixture.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { startFixture } = require('./pages-modern-fixtures.cjs');

const root = path.resolve(__dirname, '../..');
const MARK = 'PAGES_MODERN_RESULT ';
const HANDSHAKE = 'PAGES_MODERN_FIXTURE ';
const PAGES = [
  { id: 'dashboard', route: '/dashboard' },
  { id: 'performance', route: '/performance' },
  { id: 'watchlist', route: '/watchlist' },
  { id: 'market', route: '/market' },
  { id: 'news', route: '/news' },
  { id: 'fundamentals', route: '/fundamentals' },
  { id: 'factors', route: '/factors' },
  { id: 'backtest', route: '/backtest' },
  { id: 'vol', route: '/vol' },
  { id: 'edge', route: '/edge' },
  { id: 'agents', route: '/agents' },
  { id: 'agent-progress', route: '/agent-progress' },
  { id: 'memos', route: '/memos' },
  { id: 'decisions', route: '/decisions' },
  { id: 'trades', route: '/trades' },
  { id: 'movements', route: '/movements' },
  { id: 'mandato', route: '/mandato' },
];
const FINAL_VIEWPORTS = [[1920, 1080], [2560, 1440], [3440, 1440], [5120, 1440], [1440, 1000], [1280, 900], [900, 700]];

// Optional dark-mode QA overrides (unset = previous behaviour):
// BB_PAGES_THEME=dark (Nuova Scuro), BB_PAGES_MODES=modern,classic, BB_PAGES_ONLY=page,ids,
// BB_PAGES_VIEWPORTS=1920x1080,2560x1440, BB_PAGES_DIST=dist-dir, BB_PAGES_SETTINGS=0.
function qaOverrides() {
  const list = name => (process.env[name] || '').split(',').map(item => item.trim()).filter(Boolean);
  const modes = list('BB_PAGES_MODES');
  const only = list('BB_PAGES_ONLY');
  const viewports = list('BB_PAGES_VIEWPORTS').map(item => item.split('x').map(Number));
  for (const id of only) assert.ok(PAGES.some(page => page.id === id), `unknown page ${id}`);
  for (const [w, h] of viewports) assert.ok(w > 0 && h > 0, 'BB_PAGES_VIEWPORTS uses WIDTHxHEIGHT');
  return { theme: process.env.BB_PAGES_THEME === 'dark' ? 'dark' : 'light',
    modes: modes.length ? modes : null, only: only.length ? only : null,
    viewports: viewports.length ? viewports : null, distDir: process.env.BB_PAGES_DIST || 'dist',
    settings: process.env.BB_PAGES_SETTINGS !== '0' };
}

function run() {
  const phase = process.env.BB_PAGES_PHASE === 'final' ? 'final' : 'baseline';
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'bellomberg-pages-modern-'));
  const captureDirectory = process.env.BB_PAGES_CAPTURE_DIR
    ? path.resolve(process.env.BB_PAGES_CAPTURE_DIR)
    : path.join(root, '..', 'outputs', 'pages-modern', phase);
  fs.mkdirSync(captureDirectory, { recursive: true });
  const resultPath = path.join(captureDirectory, 'renderer-result.json');
  try { fs.unlinkSync(resultPath); } catch {}
  const qa = qaOverrides();
  const { server, requests, unexpectedGets, writes } = startFixture(root, { distDir: qa.distDir });
  return new Promise((resolve, reject) => {
    server.listen(0, '127.0.0.1', async () => {
      const origin = `http://127.0.0.1:${server.address().port}`;
      const entry = path.join(temporary, 'electron-entry.cjs');
      const handshakePath = path.join(temporary, 'handshake.json');
      const config = { phase, temporary, captureDirectory, rendererResultPath: resultPath,
        richDetails: phase === 'final' || process.env.BB_PAGES_DETAILS === '1', origin, handshakePath, qa };
      fs.writeFileSync(entry, `
        const fs = require('node:fs');
        const { app } = require('electron');
        const config = ${JSON.stringify(config)};
        app.setPath('userData', require('node:path').join(config.temporary, 'userdata'));
        app.disableHardwareAcceleration();
        const handshake = { pid: process.pid, entry: __filename, userData: app.getPath('userData'), origin: config.origin };
        fs.writeFileSync(config.handshakePath, JSON.stringify(handshake), { mode: 0o600 });
        console.log(${JSON.stringify(HANDSHAKE)} + JSON.stringify(handshake));
        app.whenReady().then(() => require(${JSON.stringify(__filename)}).renderer(config))
          .catch(error => { console.error('PAGES_MODERN_FIXTURE_ERROR ' + (error?.stack || error)); app.exit(1); });
      `, { mode: 0o600 });
      let child, output = '';
      try {
        const env = { ...process.env };
        delete env.ELECTRON_RUN_AS_NODE;
        delete env.BELLOMBERG_BACKEND_DIR;
        delete env.BELLOMBERG_PYTHON;
        delete env.BELLOMBERG_DESKTOP_API_URL;
        child = spawn(require('electron'), [entry], { cwd: temporary, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
        child.stdout.on('data', chunk => { output += chunk; });
        child.stderr.on('data', chunk => { output += chunk; });
        const timer = setTimeout(() => child.kill(), phase === 'baseline' ? 240000 : 900000);
        let code, signal;
        try {
          ({ code, signal } = await new Promise((done, fail) => {
            child.once('error', fail);
            child.once('close', (exitCode, closeSignal) => done({ code: exitCode, signal: closeSignal }));
          }));
        } finally { clearTimeout(timer); }
        const handshakeLine = output.split(/\r?\n/).find(line => line.startsWith(HANDSHAKE));
        assert.ok(handshakeLine && fs.existsSync(handshakePath), `Isolated Electron entry did not start. ${temporary}\n${output.slice(-5000)}`);
        const handshake = JSON.parse(fs.readFileSync(handshakePath, 'utf8'));
        assert.equal(handshake.pid, child.pid, 'the screenshot runner owns the Electron process');
        assert.equal(fs.realpathSync(handshake.entry), fs.realpathSync(entry), 'Electron ran the temporary harness entry');
        assert.equal(handshake.userData, path.join(temporary, 'userdata'), 'Electron uses a temporary profile');
        assert.equal(handshake.origin, origin, 'the only allowed app origin is this run’s fixture');
        const resultLine = output.split(/\r?\n/).find(line => line.startsWith(MARK));
        assert.ok(resultLine, `Electron produced no page-render result (exit ${code}, signal ${signal}). Evidence: ${temporary}\n${output.slice(-8000)}`);
        const marker = JSON.parse(resultLine.slice(MARK.length));
        assert.equal(path.resolve(marker.resultPath), resultPath, 'renderer result was written to the configured capture directory');
        assert.ok(fs.existsSync(resultPath), `Renderer evidence file missing at ${resultPath}`);
        const result = JSON.parse(fs.readFileSync(resultPath, 'utf8'));
        assert.equal(code, 0, JSON.stringify({ ...result, signal, temporary }));
        assert.equal(result.ok, true, JSON.stringify({ ...result, temporary }));
        const summary = { ...result, capturedAt: new Date().toISOString(), artifactDirectory: captureDirectory,
          phase, baselineVisualReview: 'not performed by this harness',
          network: { allowedOrigin: origin, blockedExternalRequests: result.blockedExternalRequests,
            unexpectedGets: [...new Set(unexpectedGets)], writes: writes.map(item => ({ ...item })),
            realBackendStarted: false, realLlmStarted: false, } };
        fs.writeFileSync(path.join(captureDirectory, 'summary.json'), JSON.stringify(summary, null, 2), { mode: 0o600 });
        fs.writeFileSync(path.join(captureDirectory, 'requests.json'), JSON.stringify(requests, null, 2), { mode: 0o600 });
        fs.writeFileSync(path.join(captureDirectory, 'electron.log'), output, { mode: 0o600 });
        console.log(JSON.stringify({ ok: true, phase, rendered: result.pages.filter(page => page.rendered).length,
          expectedPages: PAGES.length, screenshots: result.captures.length, captureDirectory, unexpectedGets: [...new Set(unexpectedGets)],
          writes: writes.map(item => `${item.method} ${item.route}`), blockedExternalRequests: result.externalRequestBlocks.length }));
        for (const stale of ['failure.log', 'failure-requests.json']) {
          const candidate = path.join(captureDirectory, stale); if (fs.existsSync(candidate)) fs.unlinkSync(candidate);
        }
        resolve();
      } catch (error) {
        fs.writeFileSync(path.join(captureDirectory, 'failure.log'), output, { mode: 0o600 });
        fs.writeFileSync(path.join(captureDirectory, 'failure-requests.json'), JSON.stringify({ requests, unexpectedGets, writes }, null, 2), { mode: 0o600 });
        reject(error);
      } finally {
        // Only stop the exact Electron child launched above.
        if (child && child.exitCode === null && child.signalCode === null) child.kill();
        server.closeAllConnections?.(); server.close();
      }
    });
  });
}

async function renderer(config) {
  const { app, BrowserWindow } = require('electron');
  process.env.BELLOMBERG_LAUNCH_ID = 'synthetic-pages-modern';
  process.env.BELLOMBERG_DESKTOP_API_URL = config.origin;
  const scenarios = [], pages = [], captures = [], blockedExternalRequests = [], consoleErrors = [], windowTrace = [];
  let window, activeRoute = 'startup';
  const qa = config.qa || { theme: 'light', modes: null, only: null, viewports: null, settings: true };
  const ROUTED_PAGES = qa.only ? PAGES.filter(page => qa.only.includes(page.id)) : PAGES;
  const modeLabel = mode => (mode === 'modern' && qa.theme === 'dark' ? 'dark' : mode);
  const js = async (fn, ...args) => window.webContents.executeJavaScript(`(${fn.toString()})(...${JSON.stringify(args)})`);
  const pause = (ms = 180) => new Promise(resolve => setTimeout(resolve, ms));
  const waitFor = async (predicate, label, timeout = 15000, ...args) => {
    const until = Date.now() + timeout;
    while (Date.now() < until) {
      if (await js(predicate, ...args)) return;
      await pause(50);
    }
    const text = await js(() => document.body?.innerText?.slice(0, 2400) || 'document body is empty');
    const viewport = await js(() => ({ width: innerWidth, height: innerHeight, dpr: devicePixelRatio }));
    throw new Error(`Timed out waiting for ${label}. Current route=${await js(() => location.hash)} viewport=${JSON.stringify(viewport)} args=${JSON.stringify(args)} body=${text}`);
  };
  const settle = async () => {
    const animationSettle = await js(async () => {
      const fontsReady = await Promise.race([
        document.fonts.ready.then(() => true, () => false),
        new Promise(resolve => setTimeout(() => resolve(false), 4000)),
      ]);
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      // Wait for finite entrance/transparency transitions so evidence does not
      // capture semi-transparent panels. Infinite loaders/spinners stay live.
      const finiteRunning = document.getAnimations({ subtree: true }).filter(animation => {
        const timing = animation.effect?.getComputedTiming();
        return !!timing && Number.isFinite(timing.endTime) && timing.iterations !== Infinity
          && (animation.playState === 'running' || animation.playState === 'pending');
      });
      await Promise.race([
        Promise.all(finiteRunning.map(animation => animation.finished.catch(() => {}))),
        new Promise(resolve => setTimeout(resolve, 2000)),
      ]);
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      return { fontsReady, finiteAnimations: finiteRunning.length, pendingFiniteAnimations: document.getAnimations({ subtree: true })
        .filter(animation => {
          const timing = animation.effect?.getComputedTiming();
          return !!timing && Number.isFinite(timing.endTime) && timing.iterations !== Infinity
            && (animation.playState === 'running' || animation.playState === 'pending');
        }).length };
    });
    await pause(100);
    return animationSettle;
  };
  const fixtureState = async () => (await (await fetch(config.origin + '/__fixture')).json());
  const countsByRoute = async () => {
    const { requests } = await fixtureState();
    return requests.reduce((out, req) => { out[req.route] = (out[req.route] || 0) + 1; return out; }, {});
  };
  const capture = async (name) => {
    const animationSettle = await settle();
    const info = await js(() => ({ width: innerWidth, height: innerHeight, route: location.hash,
      theme: document.documentElement.getAttribute('data-bb-theme') || 'light',
      mode: (document.documentElement.getAttribute('data-bb-theme') === 'dark' ? 'modern' : 'classic'),
      pageRoot: (() => { const node = document.querySelector('main .bb-page[data-page]') || document.querySelector('main .relative')?.firstElementChild; return node ? { tag: node.tagName, page: node.getAttribute?.('data-page') || null, className: String(node.className || '') } : null; })(),
      bodyText: document.body.innerText.slice(0, 260), loadingMarkers: [...document.querySelectorAll('[aria-busy="true"]')].length,
      alerts: document.querySelectorAll('[role="alert"]').length,
      runtimeBoundary: !!document.querySelector('main .bb-interface-recovery, main [data-testid="page-runtime-error"], main [data-error-boundary="runtime"]') || /RUNTIME ERROR \/\//i.test(document.querySelector('main')?.innerText || '') }));
    const image = await window.capturePage(undefined, { stayHidden: true });
    fs.writeFileSync(path.join(config.captureDirectory, name), image.toPNG());
    captures.push({ name, ...info, ...animationSettle, bytes: image.toPNG().byteLength,
      rendered: !!info.route && !!info.pageRoot && !info.runtimeBoundary,
      visualReview: 'not performed' });
    return captures.at(-1);
  };
  const revealTarget = async (selector, needle, label) => {
    await settle();
    const scrollResult = await js((sel, text, labelText) => {
      const candidates = [...document.querySelectorAll(sel)];
      const el = text == null ? candidates[0] : candidates.filter(node => (node.innerText || node.textContent || '').includes(text))
        .sort((a, b) => (a.innerText || a.textContent || '').length - (b.innerText || b.textContent || '').length)[0];
      if (!el) return { selector: sel, found: false, text, label: labelText };
      el.scrollIntoView({ block: 'center', inline: 'nearest' });
      const scrollers = [];
      for (let parent = el.parentElement; parent && parent !== document.documentElement; parent = parent.parentElement) {
        const style = getComputedStyle(parent);
        if ((parent.scrollHeight > parent.clientHeight + 2 && /^(auto|scroll|hidden)$/.test(style.overflowY)) ||
            (parent.scrollWidth > parent.clientWidth + 2 && /^(auto|scroll|hidden)$/.test(style.overflowX))) scrollers.push(parent);
      }
      return { selector: sel, found: true, text, label: labelText,
        scrollTrace: scrollers.map(parent => ({ tag: parent.tagName, className: String(parent.className || '').slice(0, 100),
          scrollTop: Math.round(parent.scrollTop), scrollLeft: Math.round(parent.scrollLeft) })) };
    }, selector, needle, label);
    if (!scrollResult.found) {
      assert.fail(`${label} was not found for reveal: ${JSON.stringify(scrollResult)}`);
    }
    await settle();
    const evidence = await js((sel, text, labelText, scrollTrace) => {
      const candidates = [...document.querySelectorAll(sel)];
      const el = text == null ? candidates[0] : candidates.filter(node => (node.innerText || node.textContent || '').includes(text))
        .sort((a, b) => (a.innerText || a.textContent || '').length - (b.innerText || b.textContent || '').length)[0];
      if (!el) return { selector: sel, found: false, text, label: labelText, scrollTrace };
      const r = el.getBoundingClientRect();
      const intersection = { left: 0, top: 0, right: innerWidth, bottom: innerHeight };
      const clippingAncestors = [];
      for (let parent = el.parentElement; parent && parent !== document.documentElement; parent = parent.parentElement) {
        const style = getComputedStyle(parent);
        const box = parent.getBoundingClientRect();
        const clipX = /^(auto|scroll|hidden|clip)$/.test(style.overflowX);
        const clipY = /^(auto|scroll|hidden|clip)$/.test(style.overflowY);
        if (!clipX && !clipY) continue;
        const clip = { left: box.left + parent.clientLeft, top: box.top + parent.clientTop,
          right: box.left + parent.clientLeft + parent.clientWidth,
          bottom: box.top + parent.clientTop + parent.clientHeight };
        if (clipX) { intersection.left = Math.max(intersection.left, clip.left); intersection.right = Math.min(intersection.right, clip.right); }
        if (clipY) { intersection.top = Math.max(intersection.top, clip.top); intersection.bottom = Math.min(intersection.bottom, clip.bottom); }
        clippingAncestors.push({ tag: parent.tagName, className: String(parent.className || '').slice(0, 100),
          clip: Object.fromEntries(Object.entries(clip).map(([key, value]) => [key, Math.round(value)])), overflowX: style.overflowX, overflowY: style.overflowY });
      }
      const visibleRect = { left: Math.max(r.left, intersection.left), top: Math.max(r.top, intersection.top),
        right: Math.min(r.right, intersection.right), bottom: Math.min(r.bottom, intersection.bottom) };
      const visibleWidth = Math.max(0, visibleRect.right - visibleRect.left), visibleHeight = Math.max(0, visibleRect.bottom - visibleRect.top);
      const hitPoint = { x: Math.round(visibleRect.left + visibleWidth / 2), y: Math.round(visibleRect.top + visibleHeight / 2) };
      const hit = visibleWidth > 0 && visibleHeight > 0 ? document.elementFromPoint(hitPoint.x, hitPoint.y) : null;
      const hitTestable = !!hit && (el.contains(hit) || hit.contains(el));
      const ancestorVisibility = [];
      for (let node = el; node && node !== document.documentElement; node = node.parentElement) {
        const style = getComputedStyle(node);
        ancestorVisibility.push({ tag: node.tagName, className: String(node.className || '').slice(0, 80), display: style.display,
          visibility: style.visibility, opacity: Number(style.opacity), pointerEvents: style.pointerEvents });
      }
      const visible = visibleWidth > 0 && visibleHeight > 0 && ancestorVisibility.every(item => item.display !== 'none' &&
        item.visibility !== 'hidden' && item.visibility !== 'collapse' && item.opacity > 0.01);
      const fullyVisible = visible && r.top >= 0 && r.bottom <= innerHeight && r.right > 0 && r.left < innerWidth &&
        visibleRect.left <= r.left + 1 && visibleRect.top <= r.top + 1 && visibleRect.right >= r.right - 1 && visibleRect.bottom >= r.bottom - 1;
      return { selector: sel, found: true, text, label: labelText, rect: { top: Math.round(r.top), left: Math.round(r.left), right: Math.round(r.right), bottom: Math.round(r.bottom), width: Math.round(r.width), height: Math.round(r.height) },
        viewport: { width: innerWidth, height: innerHeight }, intersectsViewport: visibleWidth > 0 && visibleHeight > 0,
        visibleRect: Object.fromEntries(Object.entries(visibleRect).map(([key, value]) => [key, Math.round(value)])),
        visible, fullyVisible, hitTestable, hitPoint,
        hitElement: hit ? { tag: hit.tagName, className: String(hit.className || '').slice(0, 100), text: (hit.innerText || hit.textContent || '').trim().slice(0, 100) } : null,
        clippingAncestors, ancestorVisibility, scrollTrace, visibleText: (el.innerText || el.textContent || '').slice(0, 180) };
    }, selector, needle, label, scrollResult.scrollTrace);
    assert.ok(evidence.found && evidence.fullyVisible && evidence.hitTestable,
      `${label} was clipped, occluded, moving, or not hit-testable after scrolling in the rendered viewport: ${JSON.stringify(evidence)}`);
    return evidence;
  };
  const reveal = (selector, label) => revealTarget(selector, null, label);
  const revealText = (selector, text, label) => revealTarget(selector, text, label);
  const resetPageScroll = () => js(() => {
    const nodes = [document.scrollingElement, document.body, ...document.querySelectorAll('main, main *')];
    let reset = 0;
    for (const node of nodes) {
      if (!node || (!node.scrollTop && !node.scrollLeft)) continue;
      node.scrollTop = 0;
      node.scrollLeft = 0;
      reset += 1;
    }
    window.scrollTo(0, 0);
    return reset;
  });
  const visit = async (route) => {
    activeRoute = route;
    if (route === '/market' && config.richDetails) await js(() => sessionStorage.setItem('bb:mktTicker', 'SYN1'));
    await js(r => { location.hash = '#' + r; }, route);
    await waitFor(r => location.hash === '#' + r && !!document.querySelector('main .relative') && !!document.querySelector('[data-testid="appearance-menu"]'), `Classic page ${route}`, 15000, route);
    await pause(350);
    const scrollResetCount = await resetPageScroll();
    await settle();
    return { route, scrollResetCount };
  };
  // Classica was removed (2026-10-02): the audit's two presentations are now the
  // two themes — 'classic' renders Light and 'modern' renders Dark.
  const switchMode = async (mode) => {
    const theme = mode === 'modern' || mode === 'dark' ? 'dark' : 'light';
    await js(t => { const toggle = document.querySelector('.bb-interface-menu-toggle');
      if (toggle?.getAttribute('aria-expanded') === 'false') toggle.click();
      document.querySelector(`[data-theme-choice="${t}"]`)?.click(); }, theme);
    await waitFor(t => document.querySelector(`[data-theme-choice="${t}"]`)?.getAttribute('aria-pressed') === 'true', `theme ${theme}`, 15000, theme);
    await settle();
  };
  const readRetentionState = async () => js(() => {
    window.__pagesQaIds ||= new WeakMap(); window.__pagesQaNextId ||= 0;
    const id = node => { if (!node) return null; if (!window.__pagesQaIds.has(node)) window.__pagesQaIds.set(node, ++window.__pagesQaNextId); return window.__pagesQaIds.get(node); };
    const root = document.querySelector('main .relative')?.firstElementChild;
    return { route: location.hash, rootId: id(root), mode: document.querySelector('[data-theme-choice="dark"]')?.getAttribute('aria-pressed'),
      fields: [...document.querySelectorAll('main input, main textarea, main select')].slice(0, 40).map(el => ({ name: el.getAttribute('aria-label') || el.name || el.placeholder || el.id, value: el.value })),
      selectedTabs: [...document.querySelectorAll('main [role="tab"][aria-selected="true"]')].map(el => el.id || el.textContent.trim()),
      selectedButtons: [...document.querySelectorAll('main button[aria-pressed="true"], main button[aria-checked="true"]')].map(el => el.dataset.modeChoice || el.textContent.trim()).slice(0, 12),
      charts: document.querySelectorAll('main canvas, main svg[class*="chart"], main .chart-container').length };
  });

  try {
    const width = config.phase === 'baseline' ? 1920 : FINAL_VIEWPORTS[0][0];
    const height = config.phase === 'baseline' ? 1080 : FINAL_VIEWPORTS[0][1];
    window = new BrowserWindow({ show: false, width, height, useContentSize: true, backgroundColor: '#080b13',
      webPreferences: { preload: path.join(root, 'dist-electron/preload.mjs'), contextIsolation: true, nodeIntegration: false,
        sandbox: true, backgroundThrottling: false,
        additionalArguments: ['--bellomberg-launch-id=synthetic-pages-modern', '--bellomberg-api-port=' + new URL(config.origin).port] } });
    // Keep an explicit root reference for isolated native QA probes that attach
    // to this harness process; remove it again before the owned window closes.
    globalThis.__bbQaCaptureWindow = window;
    window.on('close', () => windowTrace.push({ event: 'close', route: activeRoute, at: new Date().toISOString() }));
    window.on('closed', () => windowTrace.push({ event: 'closed', route: activeRoute, at: new Date().toISOString() }));
    window.webContents.on('destroyed', () => windowTrace.push({ event: 'webContents-destroyed', route: activeRoute, at: new Date().toISOString() }));
    window.webContents.on('unresponsive', () => windowTrace.push({ event: 'unresponsive', route: activeRoute, at: new Date().toISOString() }));
    window.webContents.on('console-message', (_event, level, message) => { if (level >= 2) consoleErrors.push({ route: activeRoute, message: String(message) }); });
    window.webContents.on('will-navigate', (_event, url) => { if (!url.startsWith(config.origin + '/')) blockedExternalRequests.push(url); });
    window.webContents.on('render-process-gone', (_event, details) => {
      consoleErrors.push({ route: activeRoute, renderProcessGone: details });
      windowTrace.push({ event: 'render-process-gone', route: activeRoute, details, at: new Date().toISOString() });
    });
    window.webContents.session.webRequest.onBeforeRequest({ urls: ['http://*/*', 'https://*/*', 'ws://*/*', 'wss://*/*'] }, (details, callback) => {
      const allowed = details.url.startsWith(config.origin + '/');
      if (!allowed) blockedExternalRequests.push(details.url);
      callback({ cancel: !allowed });
    });
    await window.loadURL(config.origin + '/#/dashboard');
    await pause(300);
    await js(() => {
      localStorage.setItem('bellomberg_token_v1', 'synthetic-fixture-token');
      localStorage.setItem('bellomberg_unlocked_v1', JSON.stringify({ ts: Date.now() }));
      localStorage.setItem('bellomberg_last_launch_id', 'synthetic-pages-modern');
      localStorage.setItem('bellomberg.lingua', 'en');
      localStorage.removeItem('bellomberg.interface-theme.v1');
    });
    await js(theme => { if (theme === 'dark') localStorage.setItem('bellomberg.interface-theme.v1', 'dark');
      else localStorage.removeItem('bellomberg.interface-theme.v1'); }, qa.theme);
    await new Promise(resolve => { window.webContents.once('did-finish-load', resolve); window.webContents.reload(); });
    await waitFor(() => !!document.querySelector('[data-testid="appearance-menu"]') && !!document.querySelector('main .relative'), 'application shell');
    await switchMode('classic');
    scenarios.push('production bundle loaded in Electron from the fixture origin with a temporary profile and synthetic access token');

    const viewports = qa.viewports || (config.phase === 'baseline' ? [[1920, 1080]] : FINAL_VIEWPORTS);
    const modes = qa.modes || (config.phase === 'baseline' ? ['classic'] : ['classic', 'modern']);
    const expectedPageStates = ROUTED_PAGES.length * modes.length * viewports.length;
    for (const [viewWidth, viewHeight] of viewports) {
      window.setContentSize(viewWidth, viewHeight);
      await waitFor((w, h) => innerWidth === w && innerHeight === h, `content viewport ${viewWidth}x${viewHeight}`, 3000, viewWidth, viewHeight);
      for (const mode of modes) {
        await switchMode(mode);
        for (const page of ROUTED_PAGES) {
          await visit(page.route);
          await switchMode(mode);
          const safeId = page.id.replace(/[^a-z0-9-]/gi, '-');
          const name = `${safeId}-${modeLabel(mode)}-${viewWidth}x${viewHeight}.png`;
          const captureInfo = await capture(name);
          const renderEvidence = await js(route => ({ route: location.hash, root: (() => { const node = document.querySelector('main .bb-page[data-page]') || document.querySelector('main .relative')?.firstElementChild; return node ? { tag: node.tagName, page: node.getAttribute?.('data-page') || null, className: String(node.className || '') } : null; })(),
            text: document.querySelector('main .relative')?.innerText?.slice(0, 500) || '', busy: [...document.querySelectorAll('main [aria-busy="true"]')].length,
            errorBoundary: !!document.querySelector('main .bb-interface-recovery, main [data-testid="page-runtime-error"], main [data-error-boundary="runtime"]') || /RUNTIME ERROR \/\//i.test(document.querySelector('main')?.innerText || ''),
            errors: [...document.querySelectorAll('main [role="alert"]')].map(el => el.innerText.slice(0, 300)) }), page.route);
          pages.push({ id: page.id, route: page.route, mode, viewport: { width: viewWidth, height: viewHeight },
            rendered: captureInfo.rendered && renderEvidence.route === '#' + page.route && !!renderEvidence.root && !renderEvidence.errorBoundary,
            visualReview: 'not performed', screenshot: name, evidence: renderEvidence });
          if (page.id === 'mandato') {
            const sectionLinks = await js(() => [...document.querySelectorAll('.mandato-index a[href^="#mandato-"]')]
              .map(el => ({ href: el.getAttribute('href'), label: el.innerText.trim() })));
            for (const [index, section] of sectionLinks.entries()) {
              await js(href => document.querySelector(`.mandato-index a[href="${href}"]`)?.click(), section.href);
              await waitFor(href => document.querySelector(`${href}:not([hidden])`), `Mandato section ${section.label}`, 5000, section.href);
              const key = section.href.replace('#mandato-', '');
            await capture(`mandato-section-${key}-${modeLabel(mode)}-${viewWidth}x${viewHeight}.png`);
          }
            await js(() => document.querySelector('#tab-diario')?.click());
            await waitFor(() => document.querySelector('#tab-diario')?.getAttribute('aria-selected') === 'true' && !!document.querySelector('#panel-diario .journal-library-head'), 'Mandato Journal tab');
            await waitFor(() => document.querySelectorAll('#panel-diario .journal-note').length > 0, 'fixture journal rows');
            await resetPageScroll();
            await settle();
            await capture(`mandato-journal-${modeLabel(mode)}-${viewWidth}x${viewHeight}.png`);
            await js(() => document.querySelector('#panel-diario .journal-note')?.click());
            await waitFor(() => !!document.querySelector('#panel-diario .journal-version') &&
              !!document.querySelector('#panel-diario .journal-title-input')?.value, 'fixture journal detail and history');
            await capture(`mandato-journal-detail-${modeLabel(mode)}-${viewWidth}x${viewHeight}.png`);
            await js(() => document.querySelector('#tab-mandato')?.click());
            await waitFor(() => document.querySelector('#tab-mandato')?.getAttribute('aria-selected') === 'true', 'Mandato tab restore');
          }
          if (page.id === 'trades') {
            await js(() => document.querySelector('#f7-mode-opening')?.click());
            await waitFor(() => document.querySelector('#f7-mode-opening')?.getAttribute('aria-pressed') === 'true', 'Trade Entry opening mode');
            await waitFor(() => document.querySelector('main')?.innerText.includes('SYN4'), 'synthetic opening record');
            await capture(`trades-opening-${modeLabel(mode)}-${viewWidth}x${viewHeight}.png`);
            await js(() => document.querySelector('#f7-mode-trade')?.click());
            await waitFor(() => document.querySelector('#f7-mode-trade')?.getAttribute('aria-pressed') === 'true', 'Trade Entry mode restore');
          }
        }
      }
    }
    scenarios.push(`rendered ${pages.filter(page => page.rendered).length}/${pages.length} requested route-mode-viewport states from production dist`);

    // Rich read-only states make the legacy baseline useful for page owners
    // before styling starts. Every value in these views comes from fixtures.
    const richReports = [];
    if (config.richDetails) {
      const richViewports = qa.viewports || (config.phase === 'baseline' ? [[1920, 1080]] : FINAL_VIEWPORTS);
      for (const [viewWidth, viewHeight] of richViewports) {
        window.setContentSize(viewWidth, viewHeight);
        await waitFor((w, h) => innerWidth === w && innerHeight === h, `rich detail viewport ${viewWidth}x${viewHeight}`, 3000, viewWidth, viewHeight);
        for (const mode of modes) {
          await switchMode(mode);
          const suffix = `${modeLabel(mode)}-${viewWidth}x${viewHeight}`;

          await visit('/market');
          // scheda Nuova: Bilanci, Azionisti e (seconda scheda del box Notizie) Filing
          await waitFor(() => document.querySelector('.market-page')?.innerText.includes('SYN1') && !!document.querySelector('.market-page .mk-c-fin'), 'Market SYN1 quote');
          await waitFor(() => document.querySelector('.market-page .mk-tbl tbody tr'), 'Market financial statements');
          const financialTabs = [
            { key: 'income', pattern: /income statement/i },
            { key: 'balance', pattern: /balance sheet/i },
            { key: 'cashflow', pattern: /cash flow/i },
          ];
          for (const tab of financialTabs) {
            await js(source => {
              const pattern = new RegExp(source, 'i');
              const button = [...document.querySelectorAll('.market-page .mk-c-fin .bbn-seg button')].find(el => pattern.test(el.title));
              button?.click(); return !!button;
            }, tab.pattern.source);
            await waitFor(source => [...document.querySelectorAll('.market-page .mk-c-fin .bbn-seg button.is-on')].some(el => new RegExp(source, 'i').test(el.title)),
              `Market ${tab.key} selected`, 5000, tab.pattern.source);
            const sectionEvidence = await reveal('.market-page .mk-c-fin', `Market ${tab.key} financial section`);
            await capture(`market-financials-${tab.key}-${suffix}.png`);
            richReports.push({ page: 'market', state: `financials-${tab.key}`, mode, viewport: { width: viewWidth, height: viewHeight }, rendered: true, visibleInViewport: true, element: sectionEvidence, visualReview: 'not performed' });
          }
          const holdersEvidence = await reveal('.market-page .mk-c-own', 'Market holders panel');
          await capture(`market-holders-${suffix}.png`);
          richReports.push({ page: 'market', state: 'holders', mode, viewport: { width: viewWidth, height: viewHeight }, rendered: true, visibleInViewport: true, element: holdersEvidence, visualReview: 'not performed' });
          await js(() => document.querySelector('.market-page .mk-c-news .bbn-seg button:nth-child(2)')?.click());
          // fase E: one-line filing summary linking to the Filing page (the full panel moved there)
          await waitFor(() => document.querySelector('.market-page [data-filing-riepilogo="SYN1"]')?.getAttribute('data-stato') === 'ok', 'Market filings summary');
          const marketFilingEvidence = await reveal('.market-page [data-filing-riepilogo="SYN1"]', 'Market filing summary');
          await waitFor(() => document.querySelector('.market-page [data-filing-riepilogo="SYN1"] a[data-filing-apri="SYN1"]')?.getAttribute('href') === '#/filing?t=SYN1', 'Market filing summary link');
          const marketFilingContentEvidence = await revealText('.market-page [data-filing-riepilogo="SYN1"] *', '1 change', 'Market filing summary content');
          await capture(`market-filings-${suffix}.png`);
          richReports.push({ page: 'market', state: 'filing-summary-and-link', mode, viewport: { width: viewWidth, height: viewHeight }, rendered: true, visibleInViewport: true, element: marketFilingEvidence, content: marketFilingContentEvidence, visualReview: 'not performed' });

          await visit('/fundamentals');
          await waitFor(() => document.querySelectorAll('.fundamentals-page tbody tr').length >= 2 && document.querySelector('.fundamentals-page [data-filing-riepilogo]'), 'Fundamentals selected synthetic model and filing summary');
          const fundamentalsDetailEvidence = await reveal('[data-testid="sector-valuation-status"]', 'Fundamentals selected model detail');
          await pause(250);
          await capture(`fundamentals-model-detail-${suffix}.png`);
          richReports.push({ page: 'fundamentals', state: 'selected-model-revision-and-detail', mode, viewport: { width: viewWidth, height: viewHeight }, rendered: true, visibleInViewport: true, element: fundamentalsDetailEvidence, visualReview: 'not performed' });
          const archiveLabel = await js(() => [...document.querySelectorAll('.fundamentals-page button')].find(el => /previous files no longer active|file precedenti non piu' attivi/i.test(el.innerText))?.innerText.trim() || null);
          assert.ok(archiveLabel, 'Fundamentals archive/revision control is missing');
          const archiveAnchor = await revealText('.fundamentals-page button', archiveLabel, 'Fundamentals archive/revision control');
          await js(() => [...document.querySelectorAll('.fundamentals-page button')].find(el => /previous files no longer active|file precedenti non piu' attivi/i.test(el.innerText))?.click());
          await pause(150);
          const archiveRevisionEvidence = await revealText('.fundamentals-page *', 'SYN1_SYNTHETIC_r3.xlsx', 'Fundamentals archived synthetic revision');
          await capture(`fundamentals-archive-${suffix}.png`);
          richReports.push({ page: 'fundamentals', state: 'archived-revisions', mode, viewport: { width: viewWidth, height: viewHeight }, rendered: true, visibleInViewport: true, element: archiveRevisionEvidence, control: archiveAnchor, visualReview: 'not performed' });
          await waitFor(() => document.querySelector('.fundamentals-page [data-filing-riepilogo="SYN1"]')?.getAttribute('data-stato') === 'ok', 'Fundamentals filing summary');
          const fundamentalsFilingEvidence = await reveal('.fundamentals-page [data-filing-riepilogo="SYN1"]', 'Fundamentals filing summary');
          const fundamentalsFilingContentEvidence = await revealText('.fundamentals-page [data-filing-riepilogo="SYN1"] *', '1 change', 'Fundamentals filing summary content');
          await capture(`fundamentals-filings-${suffix}.png`);
          richReports.push({ page: 'fundamentals', state: 'filing-summary-and-link', mode, viewport: { width: viewWidth, height: viewHeight }, rendered: true, visibleInViewport: true, element: fundamentalsFilingEvidence, content: fundamentalsFilingContentEvidence, visualReview: 'not performed' });

          await visit('/news');
          await waitFor(() => document.querySelector('.bbn-notizie')?.innerText.includes('Synthetic chip demand'), 'News wire synthetic articles');
          const newsReadingEvidence = await revealText('.news-list .news-row', 'Synthetic chip demand', 'News wire populated article row');
          await capture(`news-wire-reading-${suffix}.png`);
          richReports.push({ page: 'news', state: 'wire-articles-reading-position', mode, viewport: { width: viewWidth, height: viewHeight }, rendered: true, visibleInViewport: true, element: newsReadingEvidence, visualReview: 'not performed' });
          await js(() => [...document.querySelectorAll('.bbn-notizie [data-qa="news-view"] button')].find(el => el.innerText.trim() === 'Agenda')?.click());
          await waitFor(() => !!document.querySelector('.news-md') && document.querySelector('.bbn-notizie')?.innerText.includes('Synthetic issuer investor briefing'), 'News desk briefing and event fixtures');
          const newsDeskEvidence = await revealText('.news-agenda *', 'Synthetic issuer investor briefing', 'News desk corporate event');
          await capture(`news-desk-${suffix}.png`);
          richReports.push({ page: 'news', state: 'desk-briefing-macro-events-calendar', mode, viewport: { width: viewWidth, height: viewHeight }, rendered: true, visibleInViewport: true, element: newsDeskEvidence, visualReview: 'not performed' });
        }
      }
    }
    scenarios.push(`rich baseline fixtures produced ${richReports.length} additional read-only Market, Fundamentals, News and Filing states`);

    // Settings is a global overlay rather than a route. Capture every section
    // of its index; no action buttons run.
    const settingsReports = [];
    const settingsModes = qa.modes || (config.phase === 'baseline' ? ['classic'] : ['classic', 'modern']);
    const settingsViewports = !qa.settings ? [] : qa.viewports || (config.phase === 'baseline' ? [[1920, 1080]] : FINAL_VIEWPORTS);
    for (const [viewWidth, viewHeight] of settingsViewports) {
      window.setContentSize(viewWidth, viewHeight);
      await waitFor((w, h) => innerWidth === w && innerHeight === h, `Settings content viewport ${viewWidth}x${viewHeight}`, 3000, viewWidth, viewHeight);
      for (const mode of settingsModes) {
        await switchMode(mode);
        await visit('/dashboard');
        await js(() => document.querySelector('[aria-label="Settings"]')?.click() || window.dispatchEvent(new Event('bb:settings')));
        await waitFor(() => !!document.querySelector('.f11v[role="dialog"]'), `SettingsPanel ${mode}`);
        await pause(450);
        // One section at a time (Nuova, 05/10): open each index entry and capture it.
        const sectionInfo = await js(() => ({ sections: [...document.querySelectorAll('.f11v [data-sezione]')].map((el, index) => ({ index,
          id: el.getAttribute('data-sezione'), title: el.querySelector('.t')?.innerText?.trim() || `settings-${index}` })) }));
        for (const section of sectionInfo.sections) {
          await js(id => document.querySelector(`.f11v [data-sezione="${id}"]`)?.click(), section.id);
          await pause(250);
          const name = `settings-${section.index}-${section.title.toLowerCase().replace(/[^a-z0-9]+/g, '-')}-${modeLabel(mode)}-${viewWidth}x${viewHeight}.png`;
          await capture(name);
          settingsReports.push({ mode, viewport: { width: viewWidth, height: viewHeight }, section: section.title, index: section.index, screenshot: name, rendered: sectionInfo.sections.length > 0, visualReview: 'not performed' });
        }
        await js(() => document.querySelector('.f11v [data-sezione]')?.click());
        await capture(`settings-top-${modeLabel(mode)}-${viewWidth}x${viewHeight}.png`);
        await js(() => document.querySelector('.f11v .phead .x')?.click());
      }
    }
    scenarios.push(`SettingsPanel produced ${settingsReports.length} section captures across ${settingsModes.join(' and ')} mode at ${settingsViewports.length} viewport sizes`);

    // Probe mounted page identity, tab/form values, chart counts and backend
    // request deltas through an actual mode round-trip on selected controls.
    const retention = [];
    if (config.phase === 'final') {
      for (const route of ['/performance', '/market', '/backtest', '/trades', '/mandato']) {
        await switchMode('classic'); await visit(route);
        if (route === '/mandato') await js(() => document.querySelector('#tab-diario')?.click());
        if (route === '/market') {
          const button = await js(() => [...document.querySelectorAll('main button')].find(el => /financial|income|news|holders/i.test(el.innerText))?.click() || false);
          void button;
        }
        if (route === '/trades') await js(() => { const field = document.querySelector('#f7-tk, main input[type="text"]'); if (!field) return; const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set; setter.call(field, 'SYNQA'); field.dispatchEvent(new Event('input', { bubbles: true })); });
        const before = await readRetentionState(), beforeCounts = await countsByRoute();
        await switchMode('modern'); const modern = await readRetentionState(); await switchMode('classic'); const after = await readRetentionState();
        const afterCounts = await countsByRoute();
        retention.push({ route, before, modern, after, requestDeltas: Object.fromEntries(new Set([...Object.keys(beforeCounts), ...Object.keys(afterCounts)]).values()
          .filter(key => afterCounts[key] !== beforeCounts[key]).map(key => [key, (afterCounts[key] || 0) - (beforeCounts[key] || 0)])),
          rootStable: before.rootId === modern.rootId && modern.rootId === after.rootId,
          routeStable: before.route === modern.route && modern.route === after.route });
      }
      scenarios.push('selected route mount identity, form/tab state and fixture request deltas sampled over Classic↔Modern↔Classic');
    }

    const state = await fixtureState();
    const output = { ok: true, phase: config.phase, renderedPageStates: pages.filter(page => page.rendered).length,
      expectedPageStates, pages, richStates: richReports, settings: settingsReports, captures, scenarios,
      retention, requestCount: state.requests.length, fixtureReads: state.requests.filter(item => item.method === 'GET' && !item.route.startsWith('/assets/') && item.route !== '/').length,
      fixtureWrites: state.writes, unexpectedFixtureGets: state.unexpectedGets,
      externalRequestBlocks: [...new Set(blockedExternalRequests)], consoleErrors,
      notes: { screenshotMeans: 'Electron capturePage produced an image for the recorded route and viewport.',
        visualReview: 'No screenshot was visually reviewed by this harness.', pageCount: PAGES.length,
        chatExcluded: 'Chat has its dedicated app/tests/desktop/chat-modes.cjs harness.', allDataSynthetic: true } };
    fs.writeFileSync(path.join(config.captureDirectory, 'partial-evidence.json'), JSON.stringify(output, null, 2), { mode: 0o600 });
    fs.writeFileSync(config.rendererResultPath, JSON.stringify(output), { mode: 0o600 });
    console.log(MARK + JSON.stringify({ resultPath: config.rendererResultPath, ok: output.ok,
      renderedPageStates: output.renderedPageStates, expectedPageStates: output.expectedPageStates,
      captures: output.captures.length, unexpectedFixtureGets: output.unexpectedFixtureGets.length }));
    assert.equal(output.renderedPageStates, output.expectedPageStates, 'every requested route/mode/viewport combination rendered a page root');
    assert.deepEqual(output.unexpectedFixtureGets, [], 'all renderer GETs matched an explicit synthetic fixture');
  } catch (error) {
    const diagnostic = { at: new Date().toISOString(), phase: config.phase, route: activeRoute,
      error: String(error?.stack || error), windowTrace, consoleErrors,
      renderedPageStates: pages.filter(page => page.rendered).length,
      expectedPageStates: PAGES.length * (config.phase === 'baseline' ? 1 : 2) * (config.phase === 'baseline' ? 1 : FINAL_VIEWPORTS.length),
      pageCount: pages.length, captureCount: captures.length, pages, captures, externalRequestBlocks: blockedExternalRequests };
    fs.writeFileSync(path.join(config.captureDirectory, 'renderer-error.json'), JSON.stringify(diagnostic, null, 2), { mode: 0o600 });
    console.error('PAGES_MODERN_RENDERER_ERROR ' + JSON.stringify(diagnostic));
    throw error;
  } finally {
    delete globalThis.__bbQaCaptureWindow;
    if (window && !window.isDestroyed()) window.destroy();
  }
}

if (require.main === module) run().catch(error => { console.error(error?.stack || error); process.exitCode = 1; });
module.exports = { renderer, PAGES };
