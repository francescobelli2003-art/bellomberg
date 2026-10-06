// Hidden Electron smoke for the Dashboard (hero, positions, headline-matched
// news, allocation/treemap, risk vs SPY, important events), its layout at
// standard and ultrawide sizes in Light and Dark, and the shell navigation. Every API response is synthetic and served by this file;
// no Python backend is started and operational POSTs are rejected/recorded.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { spawn } = require('node:child_process');

const root = path.resolve(__dirname, '../..');
const MARK = 'INTERFACE_MODES_RESULT ';
const HANDSHAKE = 'INTERFACE_MODES_FIXTURE ';
const TRACE = 'INTERFACE_MODES_TRACE ';
// Page destinations in src/lib/navigation.ts (CONFIG excluded): 19 since fbdfc40 added Filings (F20).
const SIDEBAR_PAGES = 19;
const LOGO_PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=';

function fixtureData() {
  // Unequal weights (84% invested, SYN15 the largest) make the donut and the
  // treemap areas measurable; values, weights and totals agree with each other.
  const nav = 146913.56;
  const weights = [0.5, 0.8, 1, 1.2, 1.5, 2, 3, 4, 5, 6, 7, 8, 10, 14, 20];
  const positions = Array.from({ length: 15 }, (_, i) => {
    const n = i + 1, ticker = `SYN${String(n).padStart(2, '0')}`;
    const prezzoLive = 100 + n, valore = Math.round(nav * weights[i]) / 100;
    return {
      ticker, nome: `Synthetic holding ${n}`, quantita: Math.round(valore / prezzoLive * 1000) / 1000, prezzo_medio: prezzoLive - 5,
      prezzo_live: prezzoLive, valuta: 'EUR', valore_mercato: valore,
      pl_eur: n * 10 - 80, pl_pct: n - 8, peso_pct: weights[i],
      prev_close: prezzoLive - 1, prev_close_ts: '2026-09-25T16:30:00Z',
      prev_close_source: 'position_prices', fx_to_eur: 1,
      ...(n === 1 ? { tesi: 'Synthetic thesis for the detail panel.\n\nSecond paragraph, fixture only.' } : {}),
    };
  });
  const invested = Math.round(positions.reduce((sum, p) => sum + p.valore_mercato, 0) * 100) / 100;
  const dates = ['2026-09-26', '2026-09-28', '2026-09-29'];
  const bars = Array.from({ length: 16 }, (_, i) => ({
    t: Math.floor(Date.UTC(2026, 8, 1 + i) / 1000), o: 100 + i, h: 102 + i,
    l: 99 + i, c: 101 + i, v: 1000 + i * 10,
  }));
  return {
    portfolio: {
      source: 'Synthetic fixture', n_positions: positions.length, positions,
      totale_valore_mercato_eur: invested, totale_pl_eur: 1234.56,
      cash_disponibile_eur: Math.round((nav - invested) * 100) / 100, cash_source: 'sqlite:cash_state', nav_total_eur: nav,
      timestamp: '2026-09-29T12:00:00Z', as_of: '2026-09-29T12:00:00Z', stale_positions: ['SYN15'],
    },
    // Decision timestamps are relative to the run, like the calendar below: the Decisions
    // page auto-archives a PENDING non-research proposal older than 7 days (logica.ts
    // isArchived), so fixed dates emptied «To decide» once the newest HOLD turned 8 days old.
    // 20 h apart keeps the oldest at ~5.8 days, inside the window and below GIORNI_FERMA.
    decisions: Array.from({ length: 8 }, (_, i) => ({
      id: 700 + i, memo_id: null, timestamp: new Date(Date.now() - i * 20 * 3600_000).toISOString().replace(/\.\d{3}Z$/, 'Z'),
      action: i % 2 ? 'HOLD' : 'RESEARCH', ticker: `SYN${String(i + 1).padStart(2, '0')}`,
      eur_amount: 1000 + i, timing: 'synthetic fixture', confidence: 'MEDIUM', status: 'PENDING',
      pm_feedback: null, outcome_pct: null,
    })),
    risk: {
      timestamp: '2026-09-29T12:00:00Z', nav_eur: 146913.56,
      portfolio: { vol_annual_pct: 12.3, sharpe: 1.57, var_95_1d_pct: 1.23, var_99_1d_pct: 2.34,
        var_95_1d_eur: 1807, var_99_1d_eur: 3438, beta_vs_spy: 1.11, max_dd_1y_pct: -8.7 },
      per_asset: {}, correlation: { tickers: [], matrix: [] }, alerts: [], n_assets_analyzed: 15,
      skipped_tickers: [], lookback_days: 252,
      benchmark: { ticker: 'SPY', vol_annual_pct: 15.1, sharpe: 1.2, var_95_1d_pct: -1.5, var_99_1d_pct: -2.6,
        beta_vs_spy: 1, max_dd_1y_pct: -11.2, n_obs: 252 },
    },
    benchmark: { ticker: 'SPY', currency: 'EUR', total_return: true, dates, index: [100, 101, 101.5] },
    navHistory: {
      dates, nav_eur: [100000, 101000, 102000], cost_basis_eur: [99000, 99500, 100000],
      pnl_eur: [1000, 1500, 2000], cash_eur: 23456.78, nav_total_eur: [120000, 130000, 146913.56],
      first_trade_date: dates[0], tickers: positions.map(p => p.ticker), n_days: dates.length,
      final_total_return_eur: 2000, final_total_return_pct: 2,
    },
    twr: {
      dates, twr_index: [100, 102, 104], regimes: ['reconstructed', 'official', 'official'],
      values_eur: [120000, 130000, 146913.56], flows_eur: [0, 0, 10000],
      metrics: { twr_total_pct: 4, twr_annualized_pct: 12, max_drawdown_pct: -8.7,
        current_drawdown_pct: -2, vol_annual_pct: 12.3, sharpe: 1.57, risk_free_used: 0.02,
        irr_annual_pct: 10, irr_basis: 'synthetic fixture' },
      reconciliation: { nav_live_eur: 146913.56, last_snapshot_date: '2026-09-28',
        last_snapshot_nav_eur: 146000, delta_pct: 0.63, note: 'Synthetic fixture', breach: false, tolerance_pct: 1 },
    },
    bars,
    news: [
      { id: 1, title: 'SYN03 beats quarterly estimates and lifts its outlook', source: 'Synthetic wire', url: null,
        published_at: '2026-09-29T09:00:00Z', ticker_mentioned: 'SYN03', sentiment: 'positive', snippet: 'Synthetic fixture headline.' },
      { id: 2, title: 'SYN07 faces a regulatory review in Europe', source: 'Synthetic wire', url: null,
        published_at: '2026-09-29T08:00:00Z', ticker_mentioned: 'SYN07', sentiment: 'negative', snippet: null },
      { id: 3, title: 'SYN01 opens a new plant', source: 'Synthetic wire', url: null,
        published_at: '2026-09-28T15:00:00Z', ticker_mentioned: null, sentiment: 'neutral', snippet: null },
      // Tagged with a holding by the feed, but the headline names none: it must be left out.
      { id: 4, title: 'Unrelated chipmaker rallies on memory prices', source: 'Synthetic wire', url: null,
        published_at: '2026-09-29T10:00:00Z', ticker_mentioned: 'SYN02', sentiment: 'positive', snippet: null },
    ],
    varContribution: { portfolio_var_95_1d_pct: 1.23, n_obs: 252, items: Array.from({ length: 15 }, (_, i) => ({
      ticker: `SYN${String(i + 1).padStart(2, '0')}`, weight_pct: 6, standalone_var_pct: 1.5,
      marginal_var: 0.01, component_var_pct: 0.08, contribution_pct_of_total_var: i === 0 ? 31 : i === 1 ? 22 : 47 / 13 })) },
    concentration: { by_region: { n_buckets: 2, hhi: 0.5, weights_pct: { 'North America': 64.5, Europe: 35.5 } } },
    agents: { agents: [{ id: 'synthetic', name: 'Synthetic committee member', role: 'Local fixture', color: '#1455ff', model: 'synthetic-model' }],
      engines: { committee_r1_r2: 'synthetic-engine' } },
    live: { running: false, heartbeat: 'ok', specialist_status: { synthetic: 'done' }, usage_total: { cost_eur: null } },
    overview: {
      indici: [{ ticker: 'SPY', name: 'S&P 500', price: 500, change_pct: 0.5 },
        { ticker: 'QQQ', name: 'Nasdaq', price: 400, change_pct: 0.7 }, { ticker: '^VIX', name: 'VIX', price: 17, change_pct: -0.2 }],
      obbligazioni: [{ ticker: '^TNX', name: '10Y', price: 4, change_pct: 0.1 }],
      commodities: [{ ticker: 'GC=F', name: 'Oro', price: 2600, change_pct: 0.2 }, { ticker: 'CL=F', name: 'WTI', price: 70, change_pct: -0.1 }],
      valute: [{ ticker: 'EURUSD=X', name: 'EUR/USD', price: 1.1, change_pct: 0.1 }, { ticker: 'BTC-USD', name: 'Bitcoin', price: 60000, change_pct: 0.4 }],
    },
  };
}

// Calendar dates are relative to the run day: the Dashboard only lists events
// from today on, so fixed dates would make the test expire.
function calendarFixture() {
  const day = offset => { const d = new Date(); d.setDate(d.getDate() + offset); return d.toISOString().slice(0, 10); };
  return { count: 9, items: [
    { date: day(1), time: '14:30', title: 'US: Nonfarm payrolls', importance: 5, country: 'US' },
    { date: day(2), time: '14:30', title: 'US: CPI', importance: 5, country: 'US' },
    { date: day(2), time: '14:30', title: 'US: CPI', importance: 5, country: 'US' },
    { date: day(3), time: '16:00', title: 'ISM manufacturing', importance: 4, country: 'US', date_estimated: true },
    { date: day(4), time: '14:30', title: 'Weekly claims', importance: 3, country: 'US' },
    { date: day(6), time: '14:15', title: 'ECB rate decision', importance: 5, country: 'EU' },
    // portfolio earnings: listed apart from the macro rows, next one per holding
    { date: day(21), time: 'TBD', type: 'Earnings', title: 'SYN03 Earnings Q3 2026', importance: 4, country: 'EU', ticker: 'SYN03', source: 'finnhub' },
    { date: day(41), time: 'TBD', type: 'Earnings', title: 'SYN05 Earnings', importance: 4, country: 'EU', ticker: 'SYN05', source: 'yfinance', date_estimated: true },
    { date: day(25), time: 'TBD', type: 'Earnings', title: 'GONE Earnings', importance: 4, country: 'US', ticker: 'GONE', source: 'finnhub' },
  ] };
}

function startFixture() {
  const data = fixtureData();
  const requests = [];
  let fixturePositionLimit = 15;
  let fixtureLanguage = 'en';
  let badRiskOnce = false;
  const instrumentation = { chartModuleIntercepts: 0, chartModuleRewriteError: null };
  const server = http.createServer(async (req, res) => {
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type,X-BB-Token,X-BB-Language');
    res.setHeader('Access-Control-Allow-Methods', 'GET,POST,PUT,OPTIONS');
    res.setHeader('Content-Type', 'application/json');
    if (req.method === 'OPTIONS') { res.end('{}'); return; }
    let raw = ''; for await (const chunk of req) raw += chunk;
    const input = raw ? JSON.parse(raw) : null;
    const url = new URL(req.url, 'http://127.0.0.1');
    const route = url.pathname;
    if (req.method === 'GET' && route === '/') {
      res.setHeader('Content-Type', 'text/html; charset=utf-8');
      res.end(fs.readFileSync(path.join(root, 'dist/index.html')));
      return;
    }
    if (req.method === 'GET' && route.startsWith('/assets/')) {
      const assetRoot = path.resolve(root, 'dist/assets');
      const assetPath = path.resolve(root, 'dist', decodeURIComponent(route.slice(1)));
      if (!assetPath.startsWith(assetRoot + path.sep)) {
        res.statusCode = 403; res.end('Forbidden'); return;
      }
      let asset = fs.readFileSync(assetPath);
      if (/^lightweight-charts\.production-.*\.js$/.test(path.basename(assetPath))) {
        let source = asset.toString('utf8');
        const exportedFactory = source.match(/export\{[^}]*?([A-Za-z_$][\w$]*)\s+as createChart(?:[,}])/);
        if (!exportedFactory) instrumentation.chartModuleRewriteError = 'createChart export alias not found';
        else {
          const factory = exportedFactory[1];
          const wrapper = `const __bbTestCreateChart=(...args)=>{const chart=${factory}(...args);const id=window.__bbTestCharts.length;chart.__bbTestLayoutFontSize=args[1]?.layout?.fontSize??null;const timeScale=chart.timeScale();const setRange=timeScale.setVisibleLogicalRange.bind(timeScale);timeScale.setVisibleLogicalRange=range=>{window.__bbTestChartEvents.push({id,kind:'set-range',range});return setRange(range)};chart.timeScale=()=>timeScale;const apply=chart.applyOptions.bind(chart);chart.applyOptions=options=>{window.__bbTestChartEvents.push({id,kind:'resize',range:timeScale.getVisibleLogicalRange(),width:options.width,height:options.height});return apply(options)};const remove=chart.remove.bind(chart);chart.remove=()=>{window.__bbTestChartEvents.push({id,kind:'remove'});chart.__bbTestRemoved=true;return remove()};chart.__bbTestId=id;chart.__bbTestRemoved=false;window.__bbTestCharts.push(chart);window.__bbTestChartEvents.push({id,kind:'created',layoutFontSize:chart.__bbTestLayoutFontSize});return chart};`;
          source = source.replace('export{', `${wrapper}export{`)
            .replace(`${factory} as createChart`, '__bbTestCreateChart as createChart');
          asset = Buffer.from(source);
          instrumentation.chartModuleIntercepts++;
        }
      }
      const extension = path.extname(assetPath);
      res.setHeader('Content-Type', extension === '.js' ? 'text/javascript; charset=utf-8'
        : extension === '.css' ? 'text/css; charset=utf-8'
          : extension === '.svg' ? 'image/svg+xml' : extension === '.woff2' ? 'font/woff2' : 'application/octet-stream');
      res.setHeader('Cache-Control', 'no-store');
      res.end(asset);
      return;
    }
    if (route === '/__fixture') {
      if (input?.badRiskOnce) badRiskOnce = true;
      if (Number.isInteger(input?.positionLimit) && input.positionLimit >= 0 && input.positionLimit <= 15) {
        fixturePositionLimit = input.positionLimit;
      }
      if (input?.language === 'en' || input?.language === 'it') fixtureLanguage = input.language;
      res.end(JSON.stringify({ requests: requests.map(r => ({ ...r })), badRiskOnce }));
      return;
    }
    requests.push({ method: req.method, route, query: Object.fromEntries(url.searchParams), input,
      language: req.headers['x-bb-language'] });
    let body = {};
    if (route === '/health') body = { status: 'ok', brand: 'Synthetic', version: 'dual-mode-test' };
    else if (route === '/auth/status') body = { configured: true, default_pin: false };
    else if (route === '/mandato') body = { dichiarato: true, causa: null, dettaglio: null, campi: {}, campi_mancanti: [], valori: {}, origine: 'esempio', impronta: null, dichiarato_il: null, errori: [] };
    else if (route === '/preferences') {
      if (req.method === 'PUT' && (input?.language === 'en' || input?.language === 'it')) fixtureLanguage = input.language;
      body = { language: fixtureLanguage, selected: true, source: 'preferences' };
    }
    else if (route === '/fx') body = { rates: { EUR: 1, USD: 0.9, GBP: 1.2 } };
    else if (route === '/portfolio') body = { ...data.portfolio,
      n_positions: fixturePositionLimit, positions: data.portfolio.positions.slice(0, fixturePositionLimit) };
    else if (route === '/decisions') body = { decisions: data.decisions };
    else if (/^\/decisions\/\d+\/events$/.test(route)) body = { events: [] };
    else if (route === '/portfolio/risk') {
      if (badRiskOnce) { badRiskOnce = false; body = { ...data.risk, alerts: [null] }; }
      else body = data.risk;
    }
    else if (route === '/portfolio/analytics/nav_history') body = data.navHistory;
    else if (route === '/portfolio/analytics/twr') body = data.twr;
    else if (route === '/portfolio/analytics/benchmark') body = data.benchmark;
    else if (route === '/news/feed') body = { count: data.news.length, items: data.news, timestamp: '2026-09-29T12:00:00Z' };
    else if (route === '/news/economic-calendar') body = calendarFixture();
    // one company logo (a 1×1 PNG) for SYN01; the others fall back to brand or initials
    else if (route === '/market/logos') body = { logos: Object.fromEntries((url.searchParams.get('tickers') || '').split(',')
      .filter(Boolean).map(t => [t, t === 'SYN01' ? LOGO_PNG : null])), motivi: {} };
    else if (route === '/portfolio/analytics/var_contribution') body = data.varContribution;
    else if (route === '/portfolio/analytics/concentration') body = data.concentration;
    else if (route === '/agents/list') body = data.agents;
    else if (route === '/agents/live') body = data.live;
    else if (route === '/portfolio/gap_days') body = { error: 'Synthetic gap-days unavailable', days: {}, unpriced: [] };
    else if (route === '/market/overview') body = data.overview;
    else if (route === '/market/quote') body = { ticker: url.searchParams.get('ticker'), name: 'Synthetic quote', price: 100, prev_close: 99 };
    else if (route === '/market/ohlc') body = { ticker: url.searchParams.get('ticker'), period: url.searchParams.get('period'), interval: url.searchParams.get('interval'), bars: data.bars };
    else if (route === '/tasks/scheduled') body = { tasks: [] };
    else if (route === '/db/backups') body = { backups: [], count: 0 };
    else if (route === '/prices/update' && req.method === 'POST') body = { ok: true, source: 'synthetic fixture' };
    else if (/^\/consigliere(?:\/|$)/.test(route) || route === '/agents/live/reset') { res.statusCode = 403; body = { detail: 'Blocked by interface test: no runs allowed' }; }
    else { res.statusCode = 404; body = { detail: `Unexpected synthetic endpoint: ${route}` }; }
    res.end(JSON.stringify(body));
  });
  return { server, requests, instrumentation };
}

function runner() {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'bellomberg-interface-modes-'));
  const captureDirectory = process.env.BELLOMBERG_INTERFACE_CAPTURE_DIR
    ? path.resolve(process.env.BELLOMBERG_INTERFACE_CAPTURE_DIR)
    : temporary;
  fs.mkdirSync(captureDirectory, { recursive: true });
  const { server, requests, instrumentation } = startFixture();
  return new Promise((resolve, reject) => {
    server.listen(0, '127.0.0.1', async () => {
      const origin = `http://127.0.0.1:${server.address().port}`;
      const fixture = path.join(temporary, 'renderer-entry.cjs');
      const handshakePath = path.join(temporary, 'renderer-handshake.json');
      const tracePath = path.join(temporary, 'renderer-trace.log');
      fs.writeFileSync(fixture, `
        const fs = require('node:fs');
        const { app } = require('electron');
        const config = ${JSON.stringify({ temporary, captureDirectory, origin, handshakePath, tracePath })};
        const trace = (stage, extra = {}) => { const item = { stage, pid: process.pid, ready: app.isReady(), argv: process.argv, main: require.main && require.main.filename, ...extra }; fs.appendFileSync(config.tracePath, JSON.stringify(item) + '\\n'); console.log(${JSON.stringify(TRACE)} + JSON.stringify(item)); };
        app.setPath('userData', require('node:path').join(config.temporary, 'userdata'));
        app.disableHardwareAcceleration();
        fs.writeFileSync(config.handshakePath, JSON.stringify({ pid: process.pid, entry: __filename, userData: app.getPath('userData'), origin: config.origin }));
        console.log(${JSON.stringify(HANDSHAKE)} + JSON.stringify({ pid: process.pid, entry: __filename, userData: app.getPath('userData'), origin: config.origin }));
        app.on('before-quit', () => trace('before-quit', { windows: require('electron').BrowserWindow.getAllWindows().length }));
        app.on('window-all-closed', () => trace('window-all-closed'));
        trace('entry');
        app.whenReady().then(() => {
          trace('ready-before-require');
          const test = require(${JSON.stringify(__filename)});
          trace('require-after', { rendererType: typeof test.renderer });
          const running = test.renderer(config);
          trace('renderer-called', { windows: require('electron').BrowserWindow.getAllWindows().length });
          return running;
        }).then(() => trace('renderer-fulfilled'))
          .catch(error => { console.error('INTERFACE_MODES_FIXTURE_ERROR ' + (error?.stack || error)); app.exit(1); });
      `, { mode: 0o600 });
      let output = '';
      let child;
      try {
        const env = { ...process.env };
        delete env.ELECTRON_RUN_AS_NODE;
        delete env.BELLOMBERG_BACKEND_DIR;
        delete env.BELLOMBERG_PYTHON;
        child = spawn(require('electron'), [fixture], {
          cwd: temporary, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
        });
        child.stdout.on('data', chunk => { output += chunk; });
        child.stderr.on('data', chunk => { output += chunk; });
        const timer = setTimeout(() => child.kill(), 300000);
        let code, signal;
        try {
          ({ code, signal } = await new Promise((done, fail) => {
            child.once('error', fail);
            child.once('close', (exitCode, closeSignal) => done({ code: exitCode, signal: closeSignal }));
          }));
        } finally { clearTimeout(timer); }
        fs.writeFileSync(path.join(temporary, 'output.log'), output);
        fs.writeFileSync(path.join(temporary, 'requests.json'), JSON.stringify(requests, null, 2));
        const handshakeLine = output.split(/\r?\n/).find(item => item.startsWith(HANDSHAKE));
        assert.ok(handshakeLine && fs.existsSync(handshakePath), `Temporary Electron entry did not start. child=${child.pid} evidence=${temporary}\n${output.slice(-4000)}`);
        const handshake = JSON.parse(fs.readFileSync(handshakePath, 'utf8'));
        assert.equal(handshake.pid, child.pid, 'the renderer entry ran in the Electron child started by this test');
        assert.equal(fs.realpathSync(handshake.entry), fs.realpathSync(fixture), 'Electron executed the isolated fixture outside the application package');
        assert.equal(handshake.userData, path.join(temporary, 'userdata'), 'Electron uses the test-only user data directory');
        assert.equal(handshake.origin, origin, 'renderer uses this run’s ephemeral HTTP fixture');
        assert.ok(instrumentation.chartModuleIntercepts >= 1, 'fixture instrumented the lightweight-charts module for each renderer document');
        assert.equal(instrumentation.chartModuleRewriteError, null, instrumentation.chartModuleRewriteError || 'chart instrumentation export rewrite succeeded');
        const line = output.split(/\r?\n/).find(item => item.startsWith(MARK));
        assert.ok(line, `Electron smoke returned no result (exit ${code}, signal ${signal}). Evidence: ${temporary}\n${output.slice(-6000)}`);
        const result = JSON.parse(line.slice(MARK.length));
        assert.equal(code, 0, JSON.stringify({ ...result, signal, temporary }));
        assert.equal(result.ok, true, JSON.stringify({ ...result, temporary }));
        assert.ok(requests.some(r => r.route === '/portfolio/analytics/twr'), 'real renderer read fixture TWR data');
        assert.ok(requests.some(r => r.route === '/market/ohlc'), 'real renderer read fixture candle data');
        assert.equal(requests.filter(r => r.method !== 'GET' && (/^\/consigliere(?:\/|$)/.test(r.route) || r.route === '/agents/live/reset')).length, 0, 'run endpoint was never called');
        const writes = requests.filter(r => !['GET', 'OPTIONS'].includes(r.method));
        assert.ok(writes.every(r => (r.method === 'POST' && r.route === '/prices/update')
          || (r.method === 'PUT' && r.route === '/preferences')),
        'only the synthetic price refresh and fixture-backed language preference may write');
        assert.equal(requests.filter(r => r.method === 'POST' && r.route !== '/prices/update').length, 0,
          'operational POSTs were never sent');
        const summary = {
          ok: result.ok,
          capturedAt: new Date().toISOString(),
          scenarios: result.scenarios,
          requestCount: requests.length,
          chartInstrumentation: instrumentation,
          chartRangeEvidence: result.chartRangeEvidence,
          runDialogBoundsEvidence: result.runDialogBoundsEvidence,
          tenHoldingTreemap: result.tenHoldingHeat && { width: result.tenHoldingHeat.map.width,
            height: result.tenHoldingHeat.map.height, tiles: result.tenHoldingHeat.tiles.length },
          forwardedOperationalPosts: requests.filter(r => r.method === 'POST' && r.route !== '/prices/update').length,
          captures: result.captures,
          captureDirectory,
          viewportSummary: result.viewportReports.map(view => ({
            theme: view.theme,
            viewport: view.viewport,
            contentWidth: view.contentWidth,
            shell: { left: view.main.left, right: view.main.right, width: view.main.width, height: view.main.height },
            dashboard: { left: view.dashboard.left, right: view.dashboard.right, width: view.dashboard.width, height: view.dashboard.height },
            page: { clientHeight: view.page.clientHeight, scrollHeight: view.page.scrollHeight },
            panels: Object.fromEntries(Object.entries(view.panels).map(([name, panel]) => [name,
              { left: panel.left, right: panel.right, top: panel.top, bottom: panel.bottom, width: panel.width, height: panel.height }])),
            typography: view.typography,
            treemap: view.treemap ? { width: view.treemap.map.width, height: view.treemap.map.height, tiles: view.treemap.tiles.length } : null,
            events: view.events,
            horizontalOverflow: view.overflow,
          })),
          security: result.security,
        };
        fs.writeFileSync(path.join(captureDirectory, 'electron.log'), output, { mode: 0o600 });
        fs.writeFileSync(path.join(captureDirectory, 'viewport-measurements.json'),
          JSON.stringify(result.viewportReports, null, 2), { mode: 0o600 });
        fs.writeFileSync(path.join(captureDirectory, 'summary.json'), JSON.stringify(summary, null, 2), { mode: 0o600 });
        console.log(JSON.stringify({ temporary, ...summary }));
        resolve();
      } catch (error) {
        fs.writeFileSync(path.join(temporary, 'output.log'), output);
        fs.writeFileSync(path.join(temporary, 'requests.json'), JSON.stringify(requests, null, 2));
        reject(error);
      } finally {
        // Only stop the exact Electron child this runner spawned. Never discover
        // or terminate application/helper processes by global PID or name.
        if (child && child.exitCode === null && child.signalCode === null) child.kill();
        server.closeAllConnections();
        server.close();
      }
    });
  });
}

async function renderer(config) {
const { app, BrowserWindow } = require('electron');
  process.env.BELLOMBERG_LAUNCH_ID = 'synthetic-interface-mode';
  process.env.BELLOMBERG_DESKTOP_API_URL = config.origin;
  let window;
  const scenarios = [];
  const captures = [];
  const preloadErrors = [], consoleErrors = [];
  const runtimeErrors = [];
  let activeStage = 'startup';
  const trace = (stage, extra = {}) => fs.appendFileSync(config.tracePath, JSON.stringify({ stage, pid: process.pid, ready: app.isReady(), ...extra }) + '\n');
  const js = (fn, ...args) => window.webContents.executeJavaScript(`(${fn.toString()})(...${JSON.stringify(args)})`);
  const readChartRange = () => js(() => {
    const chart = [...(window.__bbTestCharts || [])].filter(item => !item.__bbTestRemoved).at(-1);
    const range = chart?.timeScale().getVisibleLogicalRange();
    return chart && range ? { chartId: chart.__bbTestId, from: range.from, to: range.to,
      activeCount: window.__bbTestCharts.filter(item => !item.__bbTestRemoved).length } : null;
  });
  const assertSameChartRange = (expected, actual, label) => {
    assert.ok(expected && actual && Math.abs(expected.from - actual.from) < 0.05
      && Math.abs(expected.to - actual.to) < 0.05,
    `${label}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  };
  const wait = async (fn, label, timeout = 10000, ...args) => {
    const end = Date.now() + timeout;
    while (Date.now() < end) {
      if (await js(fn, ...args)) return;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    throw new Error(`${label}: ${await js(() => document.body.innerText.slice(0, 1800))}`);
  };
  const click = selector => js(s => {
    const element = document.querySelector(s);
    if (!element) throw new Error('Missing selector: ' + s);
    element.click();
  }, selector);
  const keyboard = (key, options = {}) => js((k, o) => window.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, ...o })), key, options);
  const reportCounts = async routes => {
    const response = await fetch(config.origin + '/__fixture');
    const payload = await response.json();
    return Object.fromEntries(routes.map(route => [route, payload.requests.filter(r => r.route === route).length]));
  };
  const fixtureControl = async body => {
    const response = await fetch(config.origin + '/__fixture', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
    return response.json();
  };
  const waitRequestCount = async (route, minimum) => {
    const end = Date.now() + 10000;
    while (Date.now() < end) {
      const current = await reportCounts([route]);
      if (current[route] > minimum) return current[route];
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    throw new Error(`Timed out waiting for ${route} request count to exceed ${minimum}`);
  };
  const settle = async () => {
    await js(async () => { await document.fonts.ready; await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); });
    await new Promise(resolve => setTimeout(resolve, 150));
  };
  const capture = async name => {
    await settle();
    fs.writeFileSync(path.join(config.captureDirectory, name), (await window.capturePage(undefined, { stayHidden: true })).toPNG());
  };
  const readIntervals = async () => JSON.parse(await js(() => JSON.stringify([...window.__bbIntervals.entries()])));
  const intervalDiff = (before, after) => {
    const beforeMap = new Map(before), afterMap = new Map(after);
    return {
      added: [...afterMap].filter(([id]) => !beforeMap.has(id)).map(([, ms]) => ms).sort((a, b) => a - b),
      removed: [...beforeMap].filter(([id]) => !afterMap.has(id)).map(([, ms]) => ms).sort((a, b) => a - b),
      retained: [...beforeMap].filter(([id, ms]) => afterMap.get(id) === ms).length,
    };
  };
  const waitForRoute = route => wait(expected => location.hash === '#' + expected, 'route ' + route, 10000, route);
  const readTreemap = () => js(() => {
    const map = document.querySelector('[data-testid="dashboard-allocation"] .bbn-treemap');
    if (!map) return null;
    const box = el => { const r = el.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height }; };
    return { map: box(map), key: box(document.querySelector('[data-testid="dashboard-allocation"] .bbn-heat-key')), tiles: [...map.querySelectorAll('.bbn-tile')].map(tile => ({ ...box(tile),
      label: tile.getAttribute('aria-label') || '', text: tile.innerText.trim(), button: tile.tagName === 'BUTTON',
      labelFits: [...tile.children].every(child => child.scrollWidth <= tile.clientWidth + 1 && child.getBoundingClientRect().bottom <= tile.getBoundingClientRect().bottom + 1) })) };
  });
  const assertTreemap = (t, count, context) => {
    assert.ok(t && t.tiles.length === count, `${context}: ${count} treemap tiles: ${JSON.stringify(t && t.tiles.length)}`);
    assert.ok(Math.abs(t.map.width - t.map.height) <= 2, `${context}: the treemap is a square: ${JSON.stringify(t.map)}`);
    assert.ok(t.key && (t.key.top >= t.map.bottom - 1 || t.key.left >= t.map.right - 1),
      `${context}: the colour key sits beside or below the treemap, never under it: ${JSON.stringify({ map: t.map, key: t.key })}`);
    const area = t.tiles.reduce((sum, tile) => sum + tile.width * tile.height, 0);
    assert.ok(Math.abs(area - t.map.width * t.map.height) <= t.map.width * t.map.height * 0.02, `${context}: tiles fill the square: ${area} vs ${t.map.width * t.map.height}`);
    for (const tile of t.tiles) {
      assert.ok(tile.left >= t.map.left - 1 && tile.right <= t.map.right + 1 && tile.top >= t.map.top - 1 && tile.bottom <= t.map.bottom + 1,
        `${context}: tile inside the square: ${JSON.stringify(tile)}`);
      assert.match(tile.label, /% of the portfolio|% del portafoglio/, `${context}: every tile names its share of the portfolio: ${tile.label}`);
      assert.doesNotMatch(tile.text, /[+-]\d/, `${context}: the visible label is the weight, not the P&L: ${tile.text}`);
      if (tile.text) assert.ok(tile.labelFits, `${context}: visible labels fit their tile: ${JSON.stringify(tile)}`);
    }
    for (let i = 0; i < t.tiles.length; i++) for (let j = i + 1; j < t.tiles.length; j++) {
      const a = t.tiles[i], b = t.tiles[j];
      const ox = Math.min(a.right, b.right) - Math.max(a.left, b.left), oy = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      assert.ok(ox <= 0.5 || oy <= 0.5, `${context}: tiles never overlap: ${JSON.stringify({ a, b })}`);
    }
    const holdings = t.tiles.filter(tile => tile.button);
    const big = holdings.reduce((best, tile) => tile.width * tile.height > best.width * best.height ? tile : best, holdings[0]);
    assert.match(big.label, /SYN15|Synthetic holding 15/, `${context}: the largest holding has the largest tile: ${big.label}`);
  };
  const measureLayout = () => js(() => {
    const box = el => { if (!el) return null; const r = el.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height,
      clientHeight: el.clientHeight, scrollHeight: el.scrollHeight, clientWidth: el.clientWidth, scrollWidth: el.scrollWidth }; };
    const q = s => document.querySelector(s);
    const main = q('.bb-modern-main'), dashboard = q('.bbn-dashboard');
    let page = dashboard?.parentElement;
    while (page && page !== main && !['auto', 'scroll'].includes(getComputedStyle(page).overflowY)) page = page.parentElement;
    const font = el => el ? parseFloat(getComputedStyle(el).fontSize) : null;
    const eventsList = q('[data-testid="dashboard-events"] .bbn-event-list');
    const visibleEvents = [...document.querySelectorAll('[data-testid="dashboard-events"] .bbn-event:not([hidden])')];
    const sidebar = q('.bb-modern-sidebar');
    const navLinks = [...document.querySelectorAll('.bb-modern-nav-link')].map(link => {
      const label = link.querySelector('span'), icon = link.querySelector('svg');
      return { text: label?.textContent.trim() || '', fontSize: parseFloat(getComputedStyle(link).fontSize), labelWidth: label?.clientWidth || 0,
        labelScrollWidth: label?.scrollWidth || 0, iconWidth: icon?.getBoundingClientRect().width || 0, height: link.getBoundingClientRect().height };
    });
    return {
      viewport: { width: innerWidth, height: innerHeight },
      main: box(main), dashboard: box(dashboard), page: box(page), contentWidth: page ? page.clientWidth - 32 : 0,
      overflow: { main: main.scrollWidth - main.clientWidth, dashboard: dashboard.scrollWidth - dashboard.clientWidth, page: page.scrollWidth - page.clientWidth },
      panels: {
        hero: box(q('.bbn-hero')), chart: box(q('.bbn-chart')), alloc: box(q('[data-testid="dashboard-allocation"]')),
        positions: box(q('[data-testid="dashboard-positions"]')), news: box(q('[data-testid="dashboard-news"]')),
        side: box(q('.bbn-side')), risk: box(q('[data-testid="dashboard-risk"]')), events: box(q('[data-testid="dashboard-events"]')),
      },
      riskTableBottom: q('[data-testid="dashboard-risk"] .bbn-risk-table')?.getBoundingClientRect().bottom ?? null,
      events: { visible: visibleEvents.length, total: document.querySelectorAll('[data-testid="dashboard-events"] .bbn-event').length,
        listBottom: eventsList?.getBoundingClientRect().bottom ?? 0, lastBottom: visibleEvents.at(-1)?.getBoundingClientRect().bottom ?? 0,
        rowStep: visibleEvents.length > 1 ? visibleEvents[1].getBoundingClientRect().top - visibleEvents[0].getBoundingClientRect().top : 48,
        earnings: !!document.querySelector('[data-testid="dashboard-earnings"]') },
      positionsScroll: box(q('[data-testid="dashboard-positions"] .bbn-scroll')),
      typography: { hero: font(q('.bbn-hero-value')), cardTitles: [...document.querySelectorAll('.bbn-card-head h2')].map(font),
        rowName: font(q('.bbn-row-name b')), rowTicker: font(q('.bbn-row-name span')), body: font(dashboard),
        chartAxis: font(q('.bbn-chart-axis')), riskTable: font(q('.bbn-risk-table td')) },
      sidebar: sidebar ? { ...box(sidebar), navLinks } : null,
    };
  });
  const assertLayout = (d, width, height) => {
    const tag = `${d.theme} ${width}×${height}`;
    assert.deepEqual(d.viewport, { width, height }, `${tag}: requested viewport`);
    assert.ok(d.overflow.main <= 1 && d.overflow.dashboard <= 1 && d.overflow.page <= 1, `${tag}: no horizontal overflow: ${JSON.stringify(d.overflow)}`);
    for (const [name, panel] of Object.entries(d.panels)) {
      assert.ok(panel && panel.left >= d.main.left - 1 && panel.right <= d.main.right + 1, `${tag}: ${name} inside the main area: ${JSON.stringify(panel)}`);
    }
    assert.ok(d.typography.hero >= 44 && d.typography.hero <= 56, `${tag}: net worth 44–56px: ${d.typography.hero}`);
    assert.ok(d.typography.cardTitles.length >= 5 && d.typography.cardTitles.every(size => size === 16), `${tag}: card titles 16px: ${d.typography.cardTitles}`);
    assert.ok(d.typography.body === 14 && d.typography.rowName >= 14 && d.typography.rowTicker >= 12 && d.typography.chartAxis >= 12 && d.typography.riskTable >= 12,
      `${tag}: readable type scale: ${JSON.stringify(d.typography)}`);
    const p = d.panels, near = (a, b, tol = 2) => Math.abs(a - b) <= tol;
    if (d.contentWidth >= 3600) {
      assert.ok([p.positions, p.news, p.alloc].every(panel => near(panel.top, p.hero.top)), `${tag}: 32:9 puts every block in one row: ${JSON.stringify(p)}`);
      assert.ok(near(p.hero.bottom, p.positions.bottom) && near(p.positions.bottom, p.news.bottom) && near(p.news.bottom, p.side.bottom),
        `${tag}: 32:9 columns share the full height: ${JSON.stringify(p)}`);
      assert.ok(p.hero.left < p.positions.left && p.positions.left < p.news.left && p.news.left < p.alloc.left, `${tag}: 32:9 column order`);
    } else if (d.contentWidth >= 1180) {
      assert.ok(near(p.hero.top, p.alloc.top) && near(p.hero.height, p.alloc.height), `${tag}: Allocation is as tall as the chart: ${JSON.stringify({ hero: p.hero, alloc: p.alloc })}`);
      assert.ok(near(p.positions.top, p.news.top) && near(p.news.top, p.side.top), `${tag}: positions, news and risk start together: ${JSON.stringify(p)}`);
      assert.ok(near(p.positions.height, p.news.height), `${tag}: positions and news have the same height: ${JSON.stringify({ positions: p.positions, news: p.news })}`);
      assert.ok(near(p.risk.top, p.side.top) && near(p.events.bottom, p.news.bottom), `${tag}: risk + events are as tall as the news: ${JSON.stringify({ risk: p.risk, events: p.events, news: p.news })}`);
      assert.ok(near(p.alloc.right, p.side.right), `${tag}: the right column is aligned`);
    } else {
      assert.ok(p.alloc.top >= p.hero.bottom - 1 && p.positions.top >= p.alloc.bottom - 1 && p.news.top >= p.positions.bottom - 1,
        `${tag}: narrow windows stack the blocks: ${JSON.stringify(p)}`);
    }
    if (height >= 1300 && d.contentWidth >= 1180) {
      assert.ok(d.page.scrollHeight <= d.page.clientHeight + 1, `${tag}: the Dashboard fits the screen without page scrolling: ${JSON.stringify(d.page)}`);
    }
    assert.ok(d.riskTableBottom != null && d.riskTableBottom <= p.risk.bottom + 1, `${tag}: the risk table is not clipped: ${JSON.stringify({ table: d.riskTableBottom, risk: p.risk })}`);
    assert.ok(d.events.visible >= 3, `${tag}: at least three events are visible: ${JSON.stringify(d.events)}`);
    assert.ok(d.events.lastBottom <= d.events.listBottom + 1, `${tag}: no event is cut: ${JSON.stringify(d.events)}`);
    assert.ok(d.events.visible === d.events.total || d.events.listBottom - d.events.lastBottom < d.events.rowStep,
      `${tag}: the events card shows every row that fits, not a fixed minimum: ${JSON.stringify(d.events)}`);
    if (width >= 901 && d.sidebar) {
      // 05/10/2026 shell Nuova: 264px, perché i tasti F restano visibili accanto a etichette a 14px non troncate.
      assert.ok(d.sidebar.width >= 256 && d.sidebar.width <= 272, `${tag}: sidebar 256–272px: ${d.sidebar.width}`);
      assert.equal(d.sidebar.navLinks.length, SIDEBAR_PAGES, `${tag}: all ${SIDEBAR_PAGES} sidebar links`);
      assert.ok(d.sidebar.navLinks.every(link => link.text && link.fontSize >= 14 && link.labelScrollWidth <= link.labelWidth + 1),
        `${tag}: sidebar labels readable without truncation: ${JSON.stringify(d.sidebar.navLinks.filter(l => l.labelScrollWidth > l.labelWidth + 1))}`);
    }
  };

  try {
    // The external fixture calls renderer only after app.whenReady(). Avoid a
    // second await so Electron creates its hidden window before it can auto-quit.
    trace('renderer-before-window');
    // useContentSize: width/height and every setContentSize below are the renderer viewport. Without it, on
    // Windows at 125% scaling with the default menu bar, setContentSize(1920, 1080) gave innerHeight 1082
    // (measured 05/10: content 1081, inner 1082 at every size); same option as pages-modern and dark-mode-switch.
    window = new BrowserWindow({ show: false, width: 1440, height: 1100, useContentSize: true,
      webPreferences: { preload: path.join(root, 'dist-electron/preload.mjs'), contextIsolation: true,
        nodeIntegration: false, sandbox: true, backgroundThrottling: false,
        additionalArguments: ['--bellomberg-launch-id=synthetic-interface-mode', '--bellomberg-api-port=' + new URL(config.origin).port] } });
    trace('renderer-window-created', { windows: BrowserWindow.getAllWindows().length });
    window.webContents.on('render-process-gone', (_event, details) => trace('render-process-gone', details));
    window.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => trace('did-fail-load', { errorCode, errorDescription, validatedURL }));
    window.webContents.on('preload-error', (_event, _path, error) => preloadErrors.push(String(error)));
    window.webContents.on('console-message', (_event, level, message) => {
      if (level >= 2) { consoleErrors.push(String(message)); runtimeErrors.push({ stage: activeStage, message: String(message) }); }
    });
    window.webContents.session.webRequest.onBeforeRequest({ urls: ['http://*/*', 'https://*/*', 'ws://*/*', 'wss://*/*'] }, (details, callback) => {
      callback({ cancel: !details.url.startsWith(config.origin + '/') });
    });
    // Serve the production bundle through the isolated fixture origin so the
    // lightweight-charts module can be instrumented in-memory for range proof.
    // No built asset or application source is modified by this test.
    await window.loadURL(config.origin + '/#/dashboard');
    trace('dashboard-file-loaded');
    await new Promise(resolve => setTimeout(resolve, 350));
    await js(() => {
      localStorage.setItem('bellomberg_token_v1', 'synthetic-token');
      localStorage.setItem('bellomberg_unlocked_v1', JSON.stringify({ ts: Date.now() }));
      localStorage.setItem('bellomberg_last_launch_id', 'synthetic-interface-mode');
      localStorage.setItem('bellomberg.lingua', 'en');
      localStorage.removeItem('bellomberg.interface-theme.v1');
    });
    // Track active timers, including cleanup, so private panel unmounts are
    // distinct from shared Dashboard/Layout polling that stays mounted.
    window.webContents.debugger.attach('1.3');
    trace('debugger-attached');
    await window.webContents.debugger.sendCommand('Page.enable');
    trace('debugger-page-enabled');
    await window.webContents.debugger.sendCommand('Page.addScriptToEvaluateOnNewDocument', { source: `
      (()=>{
        const nativeSet=window.setInterval.bind(window), nativeClear=window.clearInterval.bind(window);
        window.__bbIntervals=new Map(); window.__bbIntervalEvents=[];
        window.__bbTestCharts=[]; window.__bbTestChartEvents=[];
        window.setInterval=(fn,ms,...args)=>{
          const delay=Number(ms)||0, id=nativeSet(fn,ms,...args);
          window.__bbIntervals.set(id,delay); window.__bbIntervalEvents.push({kind:'set',id,delay}); return id;
        };
        window.clearInterval=id=>{
          const delay=window.__bbIntervals.get(id);
          window.__bbIntervals.delete(id); window.__bbIntervalEvents.push({kind:'clear',id,delay:delay??null});
          return nativeClear(id);
        };
      })();
    ` });
    trace('interval-hook-installed');
    await new Promise(resolve => { window.webContents.once('did-finish-load', resolve); window.webContents.reload(); });
    trace('dashboard-reloaded');
    await wait(() => !!document.querySelector('[data-testid="appearance-menu"]') && !!document.querySelector('.bbn-dashboard'), 'Dashboard');
    assert.equal(await js(() => localStorage.getItem('bellomberg.interface-mode.v1')), null, 'no Classica/Nuova preference is written any more');
    assert.equal(await js(() => document.querySelector('[data-theme-choice="light"]')?.getAttribute('aria-pressed')), 'true');
    assert.equal(await js(() => document.querySelectorAll('[data-mode-choice]').length), 0, 'the Classica/Nuova switch is gone');
    scenarios.push('a first launch opens the Dashboard in Light with only the Appearance (Light/Dark) menu');

    const watched = ['/portfolio', '/decisions', '/portfolio/risk', '/portfolio/analytics/twr', '/portfolio/analytics/nav_history',
      '/portfolio/analytics/benchmark', '/news/feed', '/news/economic-calendar', '/portfolio/analytics/var_contribution',
      '/portfolio/analytics/concentration', '/market/ohlc', '/market/logos'];
    await wait(() => document.querySelectorAll('[data-testid="dashboard-positions"] .bbn-row').length === 15
      && !!document.querySelector('[data-logo="remoto"]')
      && document.querySelectorAll('[data-testid="dashboard-risk"] .bbn-risk-table tbody tr').length === 6
      && document.querySelectorAll('[data-testid="dashboard-news"] .bbn-news-row').length > 0
      && document.querySelectorAll('[data-testid="dashboard-events"] .bbn-event').length > 0
      && !!document.querySelector('.bbn-chart-line'), 'Dashboard cards with fixture data', 15000);
    const chartMotion = await js(() => ({ dash: getComputedStyle(document.querySelector('.bbn-chart-line')).strokeDasharray,
      reveal: getComputedStyle(document.querySelector('.bbn-chart-svg')).animationName }));
    assert.deepEqual(chartMotion, { dash: 'none', reveal: 'bbn-draw' },
      'the line is revealed by a clip, never by a dash pattern (dashes break under non-scaling-stroke)');
    // The hero opens on NAV (unit value); Value shows the euro net worth.
    assert.equal(await js(() => document.querySelector('.bbn-hero-vista button[aria-pressed="true"]')?.textContent.trim()), 'NAV', 'NAV is the default hero view');
    await click('.bbn-hero-vista button:nth-child(2)');
    // The figure counts up once on first paint (750 ms): read it when it lands.
    await wait(() => /146,913\.56/.test(document.querySelector('.bbn-hero-value')?.textContent || ''), 'net worth count-up settles', 5000);
    await settle();
    const snapshot = await js(() => ({
      tickers: [...document.querySelectorAll('[data-testid="dashboard-positions"] .bbn-row')].map(row => row.dataset.ticker).sort(),
      hero: document.querySelector('.bbn-hero-value')?.textContent.trim() || '',
      pill: document.querySelector('.bbn-hero .bbn-pill')?.textContent.trim() || '',
      risk: document.querySelector('[data-testid="dashboard-risk"]')?.innerText || '',
      riskLevel: document.querySelector('[data-testid="dashboard-risk"] .bbn-level')?.textContent.trim() || '',
      news: [...document.querySelectorAll('[data-testid="dashboard-news"] .bbn-news-row b')].map(b => b.textContent.trim()),
      events: [...document.querySelectorAll('[data-testid="dashboard-events"] .bbn-event:not([hidden]) b')].map(b => b.textContent.trim()),
      estimated: document.querySelectorAll('[data-testid="dashboard-events"] .bbn-event em').length,
      eventsSwitch: [...document.querySelectorAll('[data-testid="dashboard-events"] .bbn-seg button')].map(b => b.textContent.trim()),
      dayPnl: { text: document.querySelector('[data-testid="dashboard-day-pnl"]')?.innerText.replace(/\s+/g, ' ').trim() || '',
        tone: document.querySelector('[data-testid="dashboard-day-pnl"] .bbn-pill')?.dataset.tone || '',
        nextToTitle: document.querySelector('.bbn-toolbar h1')?.nextElementSibling?.dataset?.testid === 'dashboard-day-pnl' },
      remoteLogo: !!document.querySelector('[data-testid="dashboard-positions"] .bbn-row[data-ticker="SYN01"] [data-logo="remoto"] img'),
      chips: [...document.querySelectorAll('[data-testid="dashboard-allocation"] .bbn-chip')].map(c => c.textContent.trim()),
      decisionsCount: document.querySelector('[data-action="view-decisions"] .bbn-count')?.textContent.trim() || '',
      fxStrip: !!document.querySelector('.bb-modern-fx'),
      statusDots: document.querySelectorAll('.bb-modern-statuses .bb-modern-status').length,
      fontFamily: getComputedStyle(document.querySelector('.bbn-dashboard')).fontFamily,
    }));
    assert.equal(snapshot.tickers.length, 15, 'the position list contains every fixture position');
    assert.ok(snapshot.tickers.includes('SYN15'));
    assert.match(snapshot.hero, /146,913\.56/, `hero shows the net worth: ${snapshot.hero}`);
    assert.match(snapshot.pill, /month/, `hero pill reports the selected period (1M by default): ${snapshot.pill}`);
    for (const marker of ['12.30%', '1.23%', '2.34%', '-8.70%', '1.57', 'Beta to SPY', '15.10%']) assert.ok(snapshot.risk.includes(marker), `risk contains ${marker}: ${snapshot.risk}`);
    assert.equal(snapshot.riskLevel, 'Low risk', '12.3% vs SPY 15.1% = 0.81×, under the declared 0.85× threshold');
    assert.ok(snapshot.news.some(title => /SYN03/.test(title)), `news matched to a holding by its headline: ${JSON.stringify(snapshot.news)}`);
    assert.ok(!snapshot.news.some(title => /Unrelated/.test(title)), 'a headline that names no holding is left out');
    assert.ok(snapshot.events.length >= 3, `at least three important events are visible: ${JSON.stringify(snapshot.events)}`);
    assert.ok(!snapshot.events.some(title => /Weekly claims/.test(title)), 'importance-3 events are left out');
    assert.equal(new Set(snapshot.events).size, snapshot.events.length, 'each event appears once');
    assert.ok(snapshot.estimated >= 1, 'an estimated date is declared');
    assert.ok(!snapshot.events.some(title => /Earnings/.test(title)), 'earnings do not take the macro rows');
    assert.deepEqual(snapshot.eventsSwitch, ['Calendar', 'Earnings2'], 'the events card switches Calendar | Earnings (with the count)');
    await click('[data-testid="dashboard-events"] .bbn-seg button:nth-child(2)');
    await wait(() => document.querySelectorAll('[data-testid="dashboard-events"] .bbn-earning').length > 0, 'earnings view');
    const earnings = await js(() => [...document.querySelectorAll('[data-testid="dashboard-events"] .bbn-earning')].map(li => ({ ticker: li.dataset.ticker,
      name: li.querySelector('.bbn-event-body b')?.textContent.trim(), estimated: !!li.querySelector('em'), logo: !!li.querySelector('.bbn-ico') })));
    assert.deepEqual(earnings, [{ ticker: 'SYN03', name: 'Synthetic holding 3', estimated: false, logo: true },
      { ticker: 'SYN05', name: 'Synthetic holding 5', estimated: true, logo: true }], 'portfolio earnings, by date, estimated flagged, sold holdings left out');
    assert.equal(await js(() => document.querySelectorAll('[data-testid="dashboard-events"] .bbn-event:not(.bbn-earning)').length), 0,
      'the earnings view lists only earnings');
    window.setContentSize(2560, 1440); await new Promise(resolve => setTimeout(resolve, 400));
    await capture('dashboard-light-2560x1440-earnings.png'); captures.push('dashboard-light-2560x1440-earnings.png');
    window.setContentSize(1440, 1100); await new Promise(resolve => setTimeout(resolve, 400));
    await click('[data-testid="dashboard-events"] .bbn-seg button:nth-child(1)');
    await wait(() => !document.querySelector('[data-testid="dashboard-events"] .bbn-earning'), 'calendar view again');
    assert.ok(snapshot.remoteLogo, 'the company logo from /market/logos replaces the initials');
    // Σ quantity × (price − previous close) over the fixture = €1,105.37, on €122,302 invested at the
    // previous close = 0.90%; the previous close is 25/09 and the snapshot 29/09, so the box names the window.
    assert.ok(snapshot.dayPnl.nextToTitle, 'the daily P&L box sits right after the Dashboard title');
    assert.match(snapshot.dayPnl.text, /^P&L 25\/09→29\/09 ▲ \+€1,105\.37 \+0\.90%$/, `daily P&L in € and %: ${snapshot.dayPnl.text}`);
    assert.equal(snapshot.dayPnl.tone, 'su', 'a gain is green');
    // (Σw)²/Σw² over the fixture weights: 84² / 904.58 = 7.8.
    assert.ok(snapshot.chips.includes('Diversified like 8 holdings'), `effective number of holdings: ${JSON.stringify(snapshot.chips)}`);
    assert.equal(snapshot.decisionsCount, '8', 'View decisions shows the pending count');
    assert.equal(snapshot.fxStrip, false, 'the FX strip lives on the Markets page only');
    assert.equal(snapshot.statusDots, 1, 'one status dot replaces the API/PX labels');
    assert.match(snapshot.fontFamily, /Manrope/, 'the Dashboard uses the bundled geometric font');
    scenarios.push('hero, positions, news (headline-matched), allocation, risk (vs SPY) and important events render from fixture data');

    // Detailed view: the full register with all nine measures.
    await click('[data-testid="dashboard-positions"] .bbn-switch');
    await wait(() => document.querySelectorAll('[data-testid="dashboard-positions"] .bbn-table tbody tr').length === 15, 'detailed register');
    const columns = await js(() => [...document.querySelectorAll('[data-testid="dashboard-positions"] .bbn-table thead th')].map(th => th.textContent.trim()));
    assert.deepEqual(columns, ['Holding', 'Qty', 'Price', 'Today %', 'Today €', 'Value', 'P&L €', 'P&L %', 'Weight']);
    await click('[data-testid="dashboard-positions"] .bbn-switch');
    await wait(() => document.querySelectorAll('[data-testid="dashboard-positions"] .bbn-row').length === 15, 'compact list again');
    // Today / Total switches the pill of every row.
    const pillsToday = await js(() => [...document.querySelectorAll('[data-testid="dashboard-positions"] .bbn-row .bbn-pill')].map(p => p.textContent.trim()));
    await click('[data-testid="dashboard-positions"] .bbn-seg button:nth-child(2)');
    await wait(prev => JSON.stringify([...document.querySelectorAll('[data-testid="dashboard-positions"] .bbn-row .bbn-pill')].map(p => p.textContent.trim())) !== prev,
      'Total changes the row pills', 5000, JSON.stringify(pillsToday));
    await click('[data-testid="dashboard-positions"] .bbn-seg button:nth-child(1)');
    scenarios.push('detailed view shows the nine-column register; Today/Total switches the row pills');

    // Hero: periods and SPY.
    // 1D: the fixture's previous close is 25/09 and the snapshot 29/09, so the pill
    // declares the multi-session window instead of saying "today".
    for (const [index, pattern] of [[0, /25\/09→29\/09/], [1, /week/], [4, /inception/], [2, /month/]]) {
      await click(`.bbn-hero-row .bbn-seg button:nth-child(${index + 1})`);
      await wait(re => new RegExp(re).test(document.querySelector('.bbn-hero .bbn-pill')?.textContent || ''), `hero period ${pattern}`, 5000, pattern.source);
    }
    // A period change remounts the chart SVG and replays the 700 ms reveal: capture it mid-way.
    await click('.bbn-hero-row .bbn-seg button:nth-child(4)');
    await capture('dashboard-light-chart-mid-reveal.png'); captures.push('dashboard-light-chart-mid-reveal.png');
    await click('.bbn-hero-row .bbn-seg button:nth-child(3)');
    assert.equal(await js(() => !!document.querySelector('.bbn-chart-spy')), false, 'SPY is off by default');
    await click('.bbn-hero .bbn-toggle-chip');
    await wait(() => !!document.querySelector('.bbn-chart-spy') && document.querySelector('.bbn-hero .bbn-toggle-chip')?.getAttribute('aria-pressed') === 'true', 'SPY line on');
    await click('.bbn-hero .bbn-toggle-chip');
    scenarios.push('hero periods 1D/1W/All/1M update the pill and SPY draws on demand');

    // Allocation: holdings donut, regions, square treemap.
    await click('[data-testid="dashboard-allocation"] .bbn-seg button:nth-child(2)');
    await wait(() => document.querySelectorAll('[data-testid="dashboard-allocation"] .bbn-regions li').length === 2, 'regions view');
    await click('[data-testid="dashboard-allocation"] .bbn-seg button:nth-child(3)');
    await wait(() => document.querySelectorAll('[data-testid="dashboard-allocation"] .bbn-tile').length === 16, 'treemap with 15 holdings plus cash');
    await settle();
    const treemap = await readTreemap();
    assertTreemap(treemap, 16, '1440 treemap');
    scenarios.push('the heatmap is a square treemap: 15 holdings + cash fill it, area ∝ share of net worth, labels show the weight');

    // Stock detail: candles live in the side panel, with real chart gestures.
    const ohlcBefore = (await reportCounts(['/market/ohlc']))['/market/ohlc'];
    await js(() => document.querySelector('[data-testid="dashboard-positions"] .bbn-row[data-ticker="SYN01"]').focus());
    await click('[data-testid="dashboard-positions"] .bbn-row[data-ticker="SYN01"]');
    await wait(() => !!document.querySelector('[data-testid="stock-detail"] canvas'), 'stock detail with candle chart', 10000);
    await waitRequestCount('/market/ohlc', ohlcBefore);
    const detail = await js(() => ({ title: document.querySelector('[data-testid="stock-detail"] .bbn-drawer-title')?.innerText || '',
      stats: document.querySelectorAll('[data-testid="stock-detail"] .bbn-stats div').length,
      thesis: document.querySelector('[data-testid="stock-detail-thesis"] .bbn-tesi-testo')?.innerText || '',
      focusInside: document.querySelector('[data-testid="stock-detail"]').contains(document.activeElement) }));
    assert.match(detail.title, /Synthetic holding 1[\s\S]*SYN01/);
    assert.equal(detail.stats, 4);
    assert.match(detail.thesis, /^Synthetic thesis for the detail panel\.\n\nSecond paragraph/, 'the panel shows the position thesis the advisor reads, line breaks kept');
    assert.ok(detail.focusInside, 'focus moves into the detail panel');
    const chartRequest = (await fixtureControl()).requests.filter(r => r.route === '/market/ohlc').at(-1);
    assert.equal(chartRequest?.query.ticker, 'SYN01', 'the panel fetched the selected ticker’s candles');
    const chartBounds = await js(() => {
      const r = document.querySelector('[data-testid="stock-detail"] canvas').getBoundingClientRect();
      return { x: r.left, y: r.top, width: r.width, height: r.height };
    });
    assert.ok(chartBounds.width > 200 && chartBounds.height > 120, `candle chart has a usable viewport: ${JSON.stringify(chartBounds)}`);
    const beforeZoomRange = await readChartRange();
    const chartX = Math.round(chartBounds.x + chartBounds.width / 2), chartY = Math.round(chartBounds.y + chartBounds.height / 2);
    await window.webContents.debugger.sendCommand('Input.dispatchMouseEvent', { type: 'mouseWheel', x: chartX, y: chartY, deltaX: 0, deltaY: -120 });
    await wait((from, to) => {
      const chart = [...(window.__bbTestCharts || [])].filter(item => !item.__bbTestRemoved).at(-1);
      const range = chart?.timeScale().getVisibleLogicalRange();
      return range && (Math.abs(range.from - from) > 0.05 || Math.abs(range.to - to) > 0.05);
    }, 'wheel zoom changes the visible range', 5000, beforeZoomRange.from, beforeZoomRange.to);
    const zoomedRange = await readChartRange();
    window.setContentSize(1920, 1080);
    await new Promise(resolve => setTimeout(resolve, 400)); await settle();
    const resizedRange = await readChartRange();
    assert.equal(resizedRange?.chartId, zoomedRange.chartId, 'resizing keeps the same live chart');
    assertSameChartRange(zoomedRange, resizedRange, 'resizing keeps the zoomed dates');
    const chartRangeEvidence = { beforeZoomRange, zoomedRange, resizedRange };
    await js(() => document.querySelector('[data-testid="stock-detail"]').dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    await wait(() => !document.querySelector('[data-testid="stock-detail"]')
      && document.activeElement?.dataset?.ticker === 'SYN01', 'Escape closes the detail and returns focus to the row');
    window.setContentSize(1440, 1100);
    await new Promise(resolve => setTimeout(resolve, 300));
    scenarios.push('a position opens its detail panel: candles for that ticker, wheel zoom, resize keeps the range, Escape returns focus');

    // Theme: Light ⇄ Dark only repaints — no reads, no timers, no remount.
    const rootBefore = await js(() => { window.__bbRoot = document.querySelector('.bbn-dashboard'); return true; });
    assert.ok(rootBefore);
    const countsBefore = await reportCounts(watched), intervalsBefore = await readIntervals();
    for (const theme of ['dark', 'light', 'dark']) {
      await click('.bb-interface-menu-toggle');
      await click(`[data-theme-choice="${theme}"]`);
      await wait(t => (document.documentElement.getAttribute('data-bb-theme') || 'light') === t, `theme ${theme}`, 5000, theme);
      await settle();
    }
    const countsAfter = await reportCounts(watched), intervalsAfter = await readIntervals();
    assert.deepEqual(countsAfter, countsBefore, 'a theme switch reads nothing');
    assert.deepEqual(intervalDiff(intervalsBefore, intervalsAfter).added, [], 'a theme switch adds no timer');
    assert.deepEqual(intervalDiff(intervalsBefore, intervalsAfter).removed, [], 'a theme switch removes no timer');
    assert.ok(await js(() => window.__bbRoot === document.querySelector('.bbn-dashboard')), 'the Dashboard is not remounted');
    assert.equal(await js(() => getComputedStyle(document.querySelector('.bbn-dashboard').parentElement.parentElement).backgroundColor), 'rgb(10, 10, 11)',
      'Dark paints the near-black neutral background');
    await new Promise(resolve => { window.webContents.once('did-finish-load', resolve); window.webContents.reload(); });
    await wait(() => document.documentElement.getAttribute('data-bb-theme') === 'dark' && !!document.querySelector('.bbn-dashboard'), 'Dark persists after reload');
    await wait(() => document.querySelectorAll('[data-testid="dashboard-positions"] .bbn-row').length === 15, 'positions after reload');
    assert.equal(await js(() => document.querySelectorAll('[data-testid="dashboard-allocation"] .bbn-tile').length), 16, 'the heatmap view preference survives the reload');
    scenarios.push('Light/Dark repaint in place (no reads, timers or remount) and Dark persists after reload');

    const viewportCases = [
      { width: 1920, height: 1080, capture: true },
      { width: 2560, height: 1440, capture: true },
      { width: 2560, height: 1080, capture: false },
      { width: 3440, height: 1440, capture: true },
      { width: 5120, height: 1440, capture: true },
      { width: 1280, height: 800, capture: false },
      { width: 1024, height: 768, capture: false },
    ];
    const viewportReports = [];
    for (const theme of ['dark', 'light']) {
      if ((await js(() => document.documentElement.getAttribute('data-bb-theme') || 'light')) !== theme) {
        await click('.bb-interface-menu-toggle');
        await click(`[data-theme-choice="${theme}"]`);
        await wait(t => (document.documentElement.getAttribute('data-bb-theme') || 'light') === t, `theme ${theme}`, 5000, theme);
        await click('.bb-interface-menu-toggle');
      }
      for (const viewport of viewportCases) {
        const { width, height } = viewport;
        window.setContentSize(width, height);
        await new Promise(resolve => setTimeout(resolve, 350));
        await settle();
        await new Promise(resolve => setTimeout(resolve, 250));
        await js(() => { for (const el of document.querySelectorAll('main *')) if (el.scrollTop) el.scrollTop = 0; });
        for (const view of ['titoli', 'heatmap']) {
          await js(v => { const seg = document.querySelector('[data-testid="dashboard-allocation"] .bbn-seg');
            seg?.querySelectorAll('button')[v === 'heatmap' ? 2 : 0].click(); }, view);
          await settle();
          if (viewport.capture) {
            const name = `dashboard-${theme}-${width}x${height}${view === 'heatmap' ? '-heatmap' : ''}.png`;
            await capture(name); captures.push(name);
          }
        }
        // the earnings view uses the whole card at every size: all rows visible, none cut
        await js(() => document.querySelector('[data-testid="dashboard-events"] .bbn-seg button:nth-child(2)').click());
        await settle();
        const earningsView = await js(() => {
          const list = document.querySelector('[data-testid="dashboard-events"] .bbn-event-list');
          const rows = [...document.querySelectorAll('[data-testid="dashboard-events"] .bbn-earning:not([hidden])')];
          return { visible: rows.length, listBottom: list?.getBoundingClientRect().bottom ?? 0, lastBottom: rows.at(-1)?.getBoundingClientRect().bottom ?? 0 };
        });
        assert.ok(earningsView.visible === 2 && earningsView.lastBottom <= earningsView.listBottom + 1,
          `${theme} ${width}×${height}: both earnings visible and not cut: ${JSON.stringify(earningsView)}`);
        await js(() => document.querySelector('[data-testid="dashboard-events"] .bbn-seg button:nth-child(1)').click());
        await settle();
        const dimensions = await measureLayout();
        dimensions.theme = theme;
        dimensions.treemap = await readTreemap();
        assertLayout(dimensions, width, height);
        if (dimensions.treemap) assertTreemap(dimensions.treemap, 16, `${theme} ${width}×${height}`);
        viewportReports.push(dimensions);
      }
    }
    window.setContentSize(1440, 1100);
    await click('.bb-interface-menu-toggle');
    await click('[data-theme-choice="light"]');
    await click('.bb-interface-menu-toggle');
    scenarios.push('Dashboard measured and captured at 1920×1080, 2560×1440, 3440×1440 and 5120×1440 in Dark and Light; 2560×1080, 1280 and 1024 measured');

    const routePairs = await js(() => [...document.querySelectorAll('.bb-modern-nav-scroll a[href^="#/"]')].map(a => [a.getAttribute('href'), a.textContent.trim()]));
    assert.equal(routePairs.length, SIDEBAR_PAGES, `Modern sidebar exposes all ${SIDEBAR_PAGES} page destinations`);
    for (const [href] of routePairs) {
      activeStage = `navigation ${href}`;
      await click(`a[href="${href}"]`);
      await waitForRoute(href.slice(1));
      await wait(expected => !!document.querySelector(`.bb-modern-nav-scroll a[href="${expected}"][aria-current="page"]`), 'active destination ' + href, 5000, href);
      await settle();
      if (href === '#/decisions') {
        const decisionPage = await js(() => ({ heading: document.querySelector('main h1')?.textContent.trim() || '',
          hasFixtureDecision: /SYN0\d/.test(document.querySelector('main [data-page="decisions"]')?.innerText || ''),
          modernDashboardVisible: !!document.querySelector('.bbn-dashboard') }));
        assert.match(decisionPage.heading, /^(Decisions|Decisioni)$/,
          `Decisions remains a real navigable route after removing its dashboard panel: ${JSON.stringify(decisionPage)}`);
        assert.ok(decisionPage.hasFixtureDecision && !decisionPage.modernDashboardVisible,
          `Decisions page renders its fixture data independently of the dashboard: ${JSON.stringify(decisionPage)}`);
      }
    }
    const inspectSidebarLabels = () => js(() => ({
      labels: [...document.querySelectorAll('.bb-modern-nav-link span')].map(label => ({
        text: label.textContent.trim(), width: label.clientWidth, scrollWidth: label.scrollWidth,
        fontSize: parseFloat(getComputedStyle(label.parentElement).fontSize),
      })),
      config: { text: document.querySelector('.bb-modern-config span')?.textContent.trim() || '',
        visible: (() => { const e = document.querySelector('.bb-modern-config').getBoundingClientRect();
          const s = document.querySelector('.bb-modern-sidebar').getBoundingClientRect();
          return e.top >= s.top && e.bottom <= s.bottom; })() },
    }));
    const englishSidebarLabels = await inspectSidebarLabels();
    assert.equal(englishSidebarLabels.labels.length, SIDEBAR_PAGES);
    assert.ok(englishSidebarLabels.labels.every(label => label.text && label.fontSize >= 14
      && label.width > 0 && label.scrollWidth <= label.width + 1),
    `English sidebar labels remain fully readable: ${JSON.stringify(englishSidebarLabels)}`);
    assert.deepEqual(englishSidebarLabels.config, { text: 'Settings', visible: true });
    await click('.bb-modern-config');
    await wait(() => {
      const input = document.querySelector('.f11v input[name="language"][value="it"]');
      const save = document.querySelector('.f11v button.btn-amber');
      return input && !input.disabled && save && !save.disabled;
    }, 'ready language selector inside CONFIG');
    await click('.f11v input[name="language"][value="it"]');
    await wait(() => document.querySelector('.f11v input[name="language"][value="it"]')?.checked === true,
      'Italian language choice is selected');
    await click('.f11v button.btn-amber');
    await wait(() => document.querySelector('.bb-modern-nav-link span')?.textContent.trim() === 'Centro di comando',
      'Italian sidebar labels after verified language preference write');
    const italianSidebarLabels = await inspectSidebarLabels();
    assert.equal(italianSidebarLabels.labels.length, SIDEBAR_PAGES);
    assert.ok(italianSidebarLabels.labels.every(label => label.text && label.fontSize >= 14
      && label.width > 0 && label.scrollWidth <= label.width + 1),
    `Italian sidebar labels remain fully readable: ${JSON.stringify(italianSidebarLabels)}`);
    assert.deepEqual(italianSidebarLabels.config, { text: 'Impostazioni', visible: true });
    await wait(() => document.querySelector('.f11v input[name="language"][value="en"]')?.disabled === false,
      'language selector is ready to restore English');
    await click('.f11v input[name="language"][value="en"]');
    await wait(() => document.querySelector('.f11v input[name="language"][value="en"]')?.checked === true,
      'English language choice is selected');
    await click('.f11v button.btn-amber');
    await wait(() => document.querySelector('.bb-modern-nav-link span')?.textContent.trim() === 'Command Center',
      'English sidebar labels restored');
    await click('.f11v button.x');
    await wait(() => !document.querySelector('.f11v[role="dialog"]'), 'close CONFIG after locale checks');
    scenarios.push('sidebar labels remain untruncated in English and Italian; CONFIG stays visible while nav scrolls');
    activeStage = 'F1';
    await keyboard('F1');
    await waitForRoute('/dashboard');
    assert.ok(await js(() => !!document.querySelector('.bb-modern-nav-scroll a[href="#/dashboard"][aria-current="page"]')));
    activeStage = 'F19';
    await keyboard('F19');
    await wait(() => !!document.querySelector('.f11v[role="dialog"]'), 'F19 opens CONFIG');
    await keyboard('F19');
    await wait(() => !document.querySelector('.f11v[role="dialog"]'), 'F19 closes CONFIG');
    activeStage = 'Ctrl+K';
    await keyboard('k', { ctrlKey: true });
    await wait(() => !!document.querySelector('[role="combobox"]'), 'Ctrl+K opens command palette');
    await keyboard('Escape');
    await wait(() => !document.querySelector('[role="combobox"]'), 'Escape closes command palette');
    scenarios.push(`${SIDEBAR_PAGES} sidebar routes track active page; F1/F19 and Ctrl+K remain available`);


    const runDialogBoundsEvidence = { notTested: 'user-request' };
    activeStage = 'synthetic-price-refresh';
    // F1 remounts the Dashboard: wait until the portfolio has loaded and the button exists.
    await wait(() => !!document.querySelector('[data-action="refresh-prices"]:not(:disabled)'), 'Dashboard ready for price refresh');
    const refreshBefore = await reportCounts(['/prices/update', '/portfolio']);
    await click('[data-action="refresh-prices"]');
    await waitRequestCount('/prices/update', refreshBefore['/prices/update']);
    await waitRequestCount('/portfolio', refreshBefore['/portfolio']);
    scenarios.push('Refresh prices uses the isolated synthetic service; the adviser run is excluded by user request');

    activeStage = 'malformed-risk-alert';
    const riskReadsBefore = (await reportCounts(['/portfolio/risk']))['/portfolio/risk'];
    await fixtureControl({ badRiskOnce: true });
    await click('[data-testid="dashboard-risk"] .bbn-icon-btn');
    await waitRequestCount('/portfolio/risk', riskReadsBefore);
    await settle();
    assert.ok(await js(() => !document.querySelector('.bb-interface-recovery')
      && document.querySelectorAll('[data-testid="dashboard-risk"] .bbn-risk-table tbody tr').length === 6),
    'a malformed risk alert from the backend is ignored: the Dashboard and its risk metrics stay up');
    scenarios.push('a malformed risk alert is discarded instead of taking the Dashboard down');

    activeStage = 'position-limits';
    await fixtureControl({ positionLimit: 0 });
    await new Promise(resolve => { window.webContents.once('did-finish-load', resolve); window.webContents.reload(); });
    await wait(() => !!document.querySelector('[data-testid="dashboard-positions"] .bbn-empty')
      && !document.querySelector('[data-testid="dashboard-allocation"] .bbn-treemap'), 'empty portfolio states');
    await fixtureControl({ positionLimit: 10 });
    await new Promise(resolve => { window.webContents.once('did-finish-load', resolve); window.webContents.reload(); });
    await wait(() => document.querySelectorAll('[data-testid="dashboard-positions"] .bbn-row').length === 10, 'ten holdings');
    window.setContentSize(3440, 1440);
    await new Promise(resolve => setTimeout(resolve, 500));
    await js(() => document.querySelector('[data-testid="dashboard-allocation"] .bbn-seg').querySelectorAll('button')[2].click());
    await settle();
    const tenHoldingHeat = await readTreemap();
    const tenChips = await js(() => [...document.querySelectorAll('[data-testid="dashboard-allocation"] .bbn-chip')].map(c => c.textContent.trim()));
    const tenEffective = Number((tenChips.find(c => /Diversified like/.test(c)) || '').match(/\d+/)?.[0]);
    assert.ok(tenEffective >= 1 && tenEffective <= 10, `ten holdings are never "like more than ten": ${JSON.stringify(tenChips)}`);
    assert.ok(tenHoldingHeat && tenHoldingHeat.tiles.length === 11, 'ten holdings plus cash');
    for (const tile of tenHoldingHeat.tiles) {
      assert.ok(tile.left >= tenHoldingHeat.map.left - 1 && tile.right <= tenHoldingHeat.map.right + 1
        && tile.top >= tenHoldingHeat.map.top - 1 && tile.bottom <= tenHoldingHeat.map.bottom + 1, 'ten-holding tiles stay inside the square');
    }
    await capture('dashboard-light-3440x1440-10holdings-heatmap.png');
    captures.push('dashboard-light-3440x1440-10holdings-heatmap.png');
    scenarios.push('empty and ten-holding portfolios render their own states; the treemap stays square with every holding inside');

    assert.deepEqual(preloadErrors, [], `no preload errors: ${JSON.stringify(preloadErrors)}`);
    assert.deepEqual(runtimeErrors, [], `no renderer warnings or errors: ${JSON.stringify(runtimeErrors)}`);
    const prefs = window.webContents.getLastWebPreferences();
    assert.ok(prefs.sandbox && prefs.contextIsolation && !prefs.nodeIntegration);
    console.log(MARK + JSON.stringify({ ok: true, scenarios, viewportReports, captures, chartRangeEvidence, runDialogBoundsEvidence, tenHoldingHeat, consoleErrors, runtimeErrors,
      captureDirectory: config.captureDirectory,
      security: { sandbox: prefs.sandbox, contextIsolation: prefs.contextIsolation, nodeIntegration: prefs.nodeIntegration } }));
    app.exit(0);
  } catch (error) {
    trace('renderer-caught-error', { error: String(error), stack: error?.stack });
    console.log(MARK + JSON.stringify({ ok: false, error: String(error), scenarios, preloadErrors, consoleErrors, runtimeErrors,
      page: window ? await js(() => document.body.innerText.slice(-2800)).catch(String) : null }));
    app.exit(1);
  }
}

if (require.main === module) runner().catch(error => { console.error(error); process.exitCode = 1; });
module.exports = { renderer };
