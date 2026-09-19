// Run from app/: npm run build:bundles && node tests/desktop/trade.cjs
// Built React form + production preload, hidden Electron, isolated userData.
// Only the ephemeral synthetic HTTP server is reachable; no application main,
// Python backend, real portfolio, provider API or PM desktop process is used.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { spawn } = require('node:child_process');
const appRoot = path.resolve(__dirname, '../..');
const RESULT = 'BB_TRADE_DESKTOP_RESULT ';
const DAY = '2024-02-01';

function preview(body, serial, mismatch = false) {
  const historical = Boolean(body.data);
  const rate = body.valuta === 'EUR' ? 1 : historical ? 0.8 : 0.95;
  const delta = -Math.round(body.quantita * body.prezzo * rate * 100) / 100;
  return {
    ok: true, preview_id: `synthetic-preview-${serial}`, expires_in_seconds: 120,
    cash_delta_eur: delta, cash_disponibile_eur: 1000 + delta,
    data: body.data ? body.data.includes('T') ? body.data : body.data + 'T12:00:00'
      : '2024-02-20T09:42:11',
    ora_convenzionale: Boolean(body.data && !body.data.includes('T')),
    link_origin: body.linked_decision_id != null ? 'explicit' : body.senza_decisione ? 'none' : 'unknown',
    decisione: body.linked_decision_id == null ? null : {
      id: mismatch ? 99 : body.linked_decision_id, status: 'PARTIAL',
      nota: 'Esecuzione parziale: lo stato della decisione non viene cambiato.',
    },
    fx: { tasso: rate, fonte: body.valuta === 'EUR' ? 'identity' : historical ? 'storico' : 'corrente',
      data: body.data?.slice(0, 10) || '2024-02-20', nota: null },
    ricalcolo: historical ? {
      valuta: body.valuta,
      prima: { quantita: 20, prezzo_medio: 20, realized: 5, data_apertura: '2024-02-10' },
      dopo: { quantita: 22, prezzo_medio: 20.91, realized: 4, data_apertura: DAY },
      trade_successivi: [7, 8],
      note: ['Realizzato in valuta locale; snapshot NAV precedenti non riscritti.'],
    } : null,
    cassa_nota: 'Il delta riguarda la cassa corrente; non ricostruisce la cassa storica.',
    cash_note: null, guardia_note: null,
  };
}

async function runner() {
  for (const file of ['dist/index.html', 'dist-electron/preload.mjs']) {
    assert.ok(fs.existsSync(path.join(appRoot, file)), `${file} missing: run npm run build:bundles first`);
  }
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'bellomberg-trade-desktop-'));
  const requests = [];
  let mode = 'success';
  let current = [];
  let latestPreview;
  let currentFx = 0.95;
  let serial = 0;
  let language = 'it';
  let openings = [];
  const openingResponse = body => ({ ok: true,
    opening: { ...body, precisione_data: body.as_of.includes('T') ? 'second' : 'day' },
    position: { ticker: body.ticker, nome: body.nome ?? null, quantita: body.quantita,
      prezzo_medio: body.prezzo_medio, valuta: body.valuta, data_apertura: null },
    cash_delta_eur: 0, cash_disponibile_eur: null,
    performance_note: language === 'it' ? 'Copertura sintetica: precedente non documentato.' : 'Synthetic coverage: earlier history undocumented.' });
  const server = http.createServer(async (req, res) => {
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type,X-BB-Token,X-BB-Language');
    res.setHeader('Access-Control-Allow-Methods', 'GET,POST,PUT,OPTIONS');
    res.setHeader('Content-Type', 'application/json');
    if (req.method === 'OPTIONS') { res.end('{}'); return; }
    let raw = '';
    for await (const chunk of req) raw += chunk;
    let body;
    try { body = raw ? JSON.parse(raw) : null; }
    catch { res.statusCode = 400; res.end('{"detail":"invalid fixture JSON"}'); return; }
    const route = new URL(req.url, 'http://127.0.0.1').pathname;
    if (route === '/__fixture') {
      if (body?.mode) { mode = body.mode; current = []; latestPreview = null; currentFx = 0.95; openings = []; }
      if (body?.fx) currentFx = body.fx;
      if (body?.language) language = body.language;
      res.end(JSON.stringify({ requests: current, latestPreview }));
      return;
    }
    const recorded = { method: req.method, route, body, raw, mode, language: req.headers['x-bb-language'] };
    requests.push(recorded); current.push(recorded);
    let response;
    if (route === '/health') response = { status: 'ok', brand: 'Synthetic', version: 'test' };
    else if (route === '/auth/status') response = { configured: true, default_pin: false };
    else if (route === '/mandato') response = { dichiarato: true, causa: null };
    else if (route === '/preferences') {
      if (req.method === 'PUT') language = body.language;
      response = { language, selected: true, source: 'preferences' };
    }
    else if (route === '/system/tasks') response = { tasks: [] };
    else if (route === '/fx') response = { rates: { EUR: 1, USD: currentFx } };
    else if (route === '/portfolio') response = {
      source: 'synthetic-memory', n_positions: 1, cash_disponibile_eur: 1000,
      cash_source: mode === 'untrusted-cash' ? 'portfolio.json' : 'sqlite:cash_state',
      cash_source_note: mode === 'untrusted-cash' ? 'SYNTHETIC_CASH_SOURCE_UNAVAILABLE' : null,
      nav_total_eur: 1570,
      totale_valore_mercato_eur: 570, totale_pl_eur: 190, timestamp: '2024-02-20T09:00:00',
      positions: [{ ticker: 'SYNTH', nome: 'Synthetic position', quantita: 20,
        prezzo_medio: 20, prezzo_live: 30, valuta: 'USD', valore_mercato: 570,
        pl_eur: 190, pl_pct: 50, peso_pct: 36.3, fx_to_eur: currentFx }],
    };
    else if (route === '/trades') response = { trades: [] };
    else if (route === '/cash/movements') response = { movements: [] };
    else if (route === '/decisions') response = { decisions: [{
      id: 31, memo_id: null, timestamp: '2024-01-31T10:00:00', action: 'ADD',
      ticker: 'SYNTH', eur_amount: 400, timing: 'synthetic', confidence: 'HIGH',
      status: 'PARTIAL', pm_feedback: null, outcome_pct: null, veto: 0,
      esecuzione: { trade_ids: [3], eur: 80, pct: 20, inferito: false,
        data: '2024-01-31T11:00:00', trades: [{ id: 3, data: '2024-01-31T11:00:00',
          quantita: 4, prezzo: 25, valuta: 'USD', linked_decision_id: 31 }] },
    }] };
    else if (req.method === 'GET' && route === '/positions/opening') response = { openings };
    else if (req.method === 'GET' && route.startsWith('/positions/opening/')) {
      const opening = openings.find(row => row.ticker === decodeURIComponent(route.split('/').pop()));
      if (!opening) { res.statusCode = 404; response = { detail: 'SYNTHETIC_OPENING_ABSENT' }; }
      else response = { opening };
    }
    else if (req.method === 'POST' && route === '/positions/opening/preview') {
      if (mode === 'opening-preview409') { res.statusCode = 409; response = { detail: 'SYNTHETIC_OPENING_EXISTS' }; }
      else {
        latestPreview = { ...openingResponse(body), preview_id: `opening-${++serial}`, expires_in_seconds: 120 };
        if (mode === 'opening-mismatch') latestPreview.cash_delta_eur = 10;
        response = latestPreview;
      }
    }
    else if (req.method === 'POST' && route === '/positions/opening') {
      if (mode === 'opening-post409') { res.statusCode = 409; response = { detail: 'SYNTHETIC_OPENING_DRIFT' }; }
      else {
        assert.deepEqual(body, { ...current.find(row => row.route === '/positions/opening/preview').body, preview_id: latestPreview.preview_id });
        const result = openingResponse(body);
        result.opening.id = 71; result.opening.created_at = '2026-09-12T10:00:00+00:00';
        delete result.opening.preview_id; openings = [result.opening];
        if (mode === 'opening-post500') { res.statusCode = 500; response = { detail: 'SYNTHETIC_OPENING_COMMITTED_UNCERTAIN' }; }
        else if (mode === 'opening-malformed-receipt') response = { ...result, cash_delta_eur: 1 };
        else response = result;
      }
    }
    else if (req.method === 'POST' && route === '/trade/preview') {
      if (mode === 'preview409') { res.statusCode = 409; response = { detail: 'SYNTHETIC_PREVIEW_CONFLICT' }; }
      else { latestPreview = preview(body, ++serial, mode === 'mismatch-response'); response = latestPreview; }
    } else if (req.method === 'POST' && route === '/trade') {
      if (mode === 'post500') { res.statusCode = 500; response = { detail: 'SYNTHETIC_COMMIT_UNCERTAIN' }; }
      else if (mode === 'post409') { res.statusCode = 409; response = { detail: 'SYNTHETIC_EXPIRED_PREVIEW' }; }
      else if (mode === 'network-before-headers') { req.socket.destroy(); return; }
      else if (mode === 'network-drop') {
        // A response interrupted after headers reaches Axios as a network error,
        // without Chromium's own pre-response connection-retry behaviour.
        res.setHeader('Content-Length', '10000');
        res.write('{"ok":', () => res.destroy());
        return;
      }
      else response = { ...latestPreview, trade_id: 901 };
    } else { res.statusCode = 503; response = { detail: `unimplemented synthetic endpoint ${route}` }; }
    recorded.status = res.statusCode;
    res.end(JSON.stringify(response));
  });
  let output = '';
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    assert.ok(server.address().port >= 8766, 'synthetic listener must not use PM ports');
    const config = { temporary, origin: `http://127.0.0.1:${server.address().port}` };
    const env = { ...process.env };
    for (const key of ['ELECTRON_RUN_AS_NODE', 'BELLOMBERG_BACKEND_DIR', 'BELLOMBERG_PYTHON']) delete env[key];
    const child = spawn(require('electron'), [__filename, '--renderer-contract', JSON.stringify(config)], {
      env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
    });
    child.stdout.on('data', chunk => { output += chunk; });
    child.stderr.on('data', chunk => { output += chunk; });
    const timer = setTimeout(() => child.kill(), 55000);
    let code;
    try { code = await new Promise((resolve, reject) => {
      child.once('error', reject); child.once('close', resolve);
    }); } finally { clearTimeout(timer); }
    fs.writeFileSync(path.join(temporary, 'output.log'), output);
    fs.writeFileSync(path.join(temporary, 'requests.json'), JSON.stringify(requests, null, 2));
    const line = output.split(/\r?\n/).find(item => item.startsWith(RESULT));
    assert.ok(line, `Electron produced no result; inspect ${path.join(temporary, 'output.log')}`);
    const result = JSON.parse(line.slice(RESULT.length));
    assert.equal(code, 0, JSON.stringify(result));
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.ok(requests.some(item => item.route === '/portfolio'), 'real renderer read synthetic portfolio');
    assert.ok(requests.every(item => item.method === 'GET'
      || item.method === 'PUT' && item.route === '/preferences'
      || item.method === 'POST' && ['/trade', '/trade/preview', '/positions/opening', '/positions/opening/preview'].includes(item.route)),
    'no unexpected application mutations');
    const counts = { preview: requests.filter(item => item.route === '/trade/preview').length,
      trade: requests.filter(item => item.route === '/trade').length };
    console.log(`trade desktop: ${result.scenarios.length} scenarios passed; ${JSON.stringify(counts)}`);
    console.log(result.scenarios.join('\n'));
    console.log('Trade desktop evidence: ' + temporary);
  } finally {
    if (output) fs.writeFileSync(path.join(temporary, 'output.log'), output);
    fs.writeFileSync(path.join(temporary, 'requests.json'), JSON.stringify(requests, null, 2));
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
}

async function renderer(config) {
  const { app, BrowserWindow } = require('electron');
  app.setPath('userData', path.join(config.temporary, 'userdata'));
  app.disableHardwareAcceleration();
  process.env.BELLOMBERG_LAUNCH_ID = 'synthetic-trade-launch';
  process.env.BELLOMBERG_DESKTOP_API_URL = config.origin;
  const scenarios = [];
  const preloadErrors = [];
  const blocked = [];
  let w;
  const finish = result => { console.log(RESULT + JSON.stringify(result)); app.exit(result.ok ? 0 : 1); };
  const control = body => new Promise((resolve, reject) => {
    const req = http.request(config.origin + '/__fixture', { method: 'POST',
      headers: { 'Content-Type': 'application/json' } }, res => {
      let raw = ''; res.on('data', chunk => { raw += chunk; });
      res.on('end', () => { try { resolve(JSON.parse(raw)); } catch (error) { reject(error); } });
    });
    req.on('error', reject); req.end(JSON.stringify(body || {}));
  });
  const js = (fn, ...args) => w.webContents.executeJavaScript(`(${fn.toString()})(...${JSON.stringify(args)})`);
  const waitFor = async (fn, label, timeout = 7000) => {
    const until = Date.now() + timeout;
    while (Date.now() < until) {
      const value = await js(fn);
      if (value) return value;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    throw new Error(`Timed out: ${label}; ${await js(() => document.body.innerText.slice(-2200))}`);
  };
  const field = async (id, value) => {
    await js((id, value) => {
      const node = document.getElementById(id);
      if (!node) throw new Error('Missing field ' + id);
      const proto = node.tagName === 'SELECT' ? HTMLSelectElement.prototype
        : node.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, 'value').set.call(node, value);
      node.dispatchEvent(new Event(node.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }));
    }, id, value);
    assert.equal(await js(id => document.getElementById(id)?.value, id), value, id);
  };
  const submit = async () => {
    await js(() => document.querySelector('#f7-tk').closest('form').querySelector('[type="submit"]').click());
  };
  const dialog = () => waitFor(() => document.querySelector('[role="alertdialog"]')?.innerText,
    'confirmation dialog');
  const row = label => js(label => Array.from(document.querySelectorAll('.cfm-row'))
    .find(node => node.querySelector('.k')?.textContent === label)?.querySelector('.v')?.textContent, label);
  const confirm = async () => { await js(() => document.querySelector('.cfm-actions .go').click()); };
  const cancel = async () => { await js(() => document.querySelector('.cfm-actions .no').click()); };
  const counts = async (previews, trades) => {
    const state = await control();
    assert.equal(state.requests.filter(item => item.route === '/trade/preview').length, previews, 'preview count');
    assert.equal(state.requests.filter(item => item.route === '/trade').length, trades, 'trade count');
    return state;
  };
  const open = async (mode = 'success', query = '') => {
    await control({ mode });
    await w.loadFile(path.join(appRoot, 'dist/index.html'), { hash: '/trades' + query });
    // loadFile can be a same-document hash navigation: remount for each case,
    // including the initial synthetic session seeded after the login screen.
    await new Promise(resolve => { w.webContents.once('did-finish-load', resolve); w.webContents.reload(); });
    await waitFor(() => document.querySelector('#f7-decisione option[value="31"]')
      || document.querySelector('#f7-decisione') && document.querySelector('[aria-label="Usa SYNTH nel tagliando"]'), 'loaded form');
    await waitFor(() => !document.querySelector('.f7c [aria-busy="true"]'), 'completed form reads');
  };
  const fill = async (day = DAY, selection = 'none') => {
    await field('f7-tk', 'SYNTH');
    await field('f7-qt', '2');
    await field('f7-pz', '30');
    await field('f7-vl', 'USD');
    await field('f7-data', day);
    await field('f7-decisione', selection);
  };
  try {
    await app.whenReady();
    w = new BrowserWindow({ show: false, width: 1400, height: 1000, webPreferences: {
      preload: path.join(appRoot, 'dist-electron/preload.mjs'), contextIsolation: true,
      nodeIntegration: false, sandbox: true, backgroundThrottling: false,
      additionalArguments: ['--bellomberg-launch-id=synthetic-trade-launch',
        '--bellomberg-api-port=' + new URL(config.origin).port],
    } });
    w.webContents.on('preload-error', (_event, _path, error) => preloadErrors.push(String(error)));
    w.webContents.session.webRequest.onBeforeRequest({ urls: ['http://*/*', 'https://*/*', 'ws://*/*', 'wss://*/*'] },
      (details, callback) => {
        const cancel = !details.url.startsWith(config.origin + '/');
        if (cancel) blocked.push(details.url);
        callback({ cancel });
      });
    await w.loadFile(path.join(appRoot, 'dist/index.html'), { hash: '/trades' });
    await js(() => {
      localStorage.setItem('bellomberg_token_v1', 'synthetic-token');
      localStorage.setItem('bellomberg_unlocked_v1', JSON.stringify({ ts: Date.now() }));
      localStorage.setItem('bellomberg_last_launch_id', 'synthetic-trade-launch');
    });

    await open('untrusted-cash'); await fill();
    const unverified = await js(() => document.body.innerText);
    assert.match(unverified, /Cassa non disponibile/);
    assert.match(unverified, /SYNTHETIC_CASH_SOURCE_UNAVAILABLE/);
    assert.match(unverified, /NAV non disponibile/);
    assert.equal(await js(() => document.querySelector('.f7c .binario') === null), true);
    await counts(0, 0);
    scenarios.push('unverified numeric cash produces no cash bar, coverage or NAV forecast; source reason retained');

    await open('success', '?decision=31');
    assert.equal(await js(() => document.querySelector('#f7-tk').value), 'SYNTH');
    assert.equal(await js(() => document.querySelector('[role="radio"][aria-checked="true"]').textContent), 'ADD');
    assert.equal(await js(() => document.querySelector('#f7-decisione').value), '31');
    assert.match(await js(() => document.querySelector('#f7-decisione option:checked').textContent), /PARTIAL.*eseguito/);
    await fill(DAY, '31');
    await field('f7-nt', 'Nota sintetica: già eseguito');
    await field('f7-rz', 'Esecuzione parziale\nSeconda riga');
    await js(() => {
      const button = document.querySelector('#f7-tk').closest('form').querySelector('[type="submit"]');
      button.click(); button.click();
    });
    const text = await dialog();
    assert.match(text, /ora convenzionale, non misurata/);
    assert.match(text, /120 secondi/);
    assert.match(await row('Cambio applicato'), /1 USD = 0[,.]8 EUR.*storico.*2024-02-01/);
    assert.match(await row('Decisione'), /#31.*PARTIAL/);
    assert.match(await row('Realizzato'), /5[,.]00.*4[,.]00 USD/);
    assert.doesNotMatch(await row('Realizzato'), /EUR/);
    assert.match(await row('Apertura posizione'), /2024-02-10.*2024-02-01/);
    assert.match(await row('Operazioni successive'), /#7, #8/);
    assert.match(await row('Variazione cassa'), /48[,.]00/);
    assert.match(await row('Cassa dopo'), /952[,.]00/);
    const before = await counts(1, 0);
    const request = before.requests.find(item => item.route === '/trade/preview').body;
    assert.deepEqual(request, { ticker: 'SYNTH', action: 'ADD', quantita: 2, prezzo: 30,
      valuta: 'USD', note: 'Nota sintetica: già eseguito', pm_rationale: 'Esecuzione parziale\nSeconda riga',
      data: DAY, senza_decisione: false, linked_decision_id: 31 });
    // A later quote and even live form edits cannot change the confirmed body.
    await control({ fx: 1.25 });
    await js(() => document.querySelector('.f7c button[aria-busy]').click());
    await waitFor(() => document.querySelector('#f7-vl').parentElement.textContent.includes('1,250000'), 'changed current FX');
    await field('f7-qt', '9');
    await field('f7-data', '2024-02-05');
    await field('f7-nt', 'Non confermata');
    assert.match(await row('Cambio applicato'), /0[,.]8 EUR.*storico/);
    assert.equal(await row('Quantità'), '2');
    await confirm();
    await waitFor(() => /Trade #901 registrato/.test(document.body.innerText), 'successful confirmed trade');
    const after = await counts(1, 1);
    assert.deepEqual(after.requests.find(item => item.route === '/trade').body,
      { ...request, preview_id: before.latestPreview.preview_id });
    scenarios.push('query decision + partial + historical conventional date + local replay + frozen exact confirmation');

    await open();
    assert.equal(await js(() => document.querySelector('#f7-decisione').value), 'none');
    await fill('', 'none');
    await submit(); await dialog();
    assert.match(await row('Decisione'), /manuale, senza decisione/);
    assert.doesNotMatch(await row('Data operazione'), /convenzionale/);
    const dateless = await counts(1, 0);
    const body = dateless.requests.find(item => item.route === '/trade/preview').body;
    assert.ok(!Object.hasOwn(body, 'data'));
    assert.ok(!Object.hasOwn(body, 'linked_decision_id'));
    assert.equal(body.senza_decisione, true);
    await confirm();
    await waitFor(() => /Trade #901 registrato/.test(document.body.innerText), 'dateless trade');
    const committed = await counts(1, 1);
    assert.deepEqual(committed.requests.find(item => item.route === '/trade').body,
      { ...body, preview_id: dateless.latestPreview.preview_id });
    scenarios.push('manual default + absent operation date stays omitted in preview and confirmed request');

    await open(); await fill(DAY, 'unknown'); await field('f7-ora', '12:00:00');
    await submit(); await dialog();
    assert.match(await row('Decisione'), /Legame non dichiarato/);
    assert.match(await row('Data operazione'), /2024-02-01 12:00:00/);
    assert.doesNotMatch(await row('Data operazione'), /convenzionale/);
    const measured = await counts(1, 0);
    assert.equal(measured.requests.find(item => item.route === '/trade/preview').body.data, DAY + 'T12:00:00');
    assert.equal(measured.requests.find(item => item.route === '/trade/preview').body.senza_decisione, false);
    await cancel(); await counts(1, 0);
    scenarios.push('measured noon differs from conventional noon; unknown link and cancel do not write');

    await open('preview409'); await fill(); await submit();
    await waitFor(() => document.body.innerText.includes('SYNTHETIC_PREVIEW_CONFLICT'), 'preview conflict');
    assert.equal(await js(() => document.querySelector('[role="alertdialog"]') === null), true);
    await counts(1, 0);
    scenarios.push('preview 409 blocks confirmation and sends no trade');

    await open('mismatch-response', '?decision=31'); await fill(DAY, '31'); await submit();
    await waitFor(() => document.body.innerText.includes('Anteprima incompleta o incoerente'), 'mismatched preview');
    assert.equal(await js(() => document.querySelector('[role="alertdialog"]') === null), true);
    await counts(1, 0);
    scenarios.push('mismatched decision in server preview cannot become a confirmed trade');

    await open('success', '?decision=31'); await fill(DAY, '31'); await field('f7-tk', 'OTHER');
    await submit();
    await waitFor(() => document.body.innerText.includes('Decisione non disponibile o incompatibile'), 'incompatible selection');
    assert.equal(await js(() => document.querySelector('#f7-decisione').value), '31');
    await counts(0, 0);
    scenarios.push('changing query-selected ticker preserves explicit selection and blocks incompatible request');

    for (const mode of ['post409', 'post500', 'network-drop']) {
      await open(mode); await fill(); await submit(); await dialog(); await counts(1, 0);
      await confirm();
      if (mode === 'post409') {
        await waitFor(() => document.body.innerText.includes('SYNTHETIC_EXPIRED_PREVIEW'), 'expired preview');
        assert.match(await js(() => document.body.innerText), /Il trade NON è stato scritto/);
      } else {
        await waitFor(() => document.body.innerText.includes('Esito sconosciuto.'), mode);
        const text = await js(() => document.body.innerText);
        assert.match(text, /potrebbe essere stato scritto/);
        assert.doesNotMatch(text, /Il trade NON è stato scritto/);
      }
      await counts(1, 1);
      scenarios.push(mode + ': one confirmed request; refusal or uncertainty shown without automatic retry');
    }
    await open('network-before-headers'); await fill(); await submit(); await dialog();
    const interrupted = await counts(1, 0);
    await confirm();
    await waitFor(() => document.body.innerText.includes('Esito sconosciuto.'), 'connection lost before response');
    const attempts = (await control()).requests.filter(item => item.route === '/trade');
    assert.ok(attempts.length >= 1, 'at least one transport attempt was observed');
    const original = interrupted.requests.find(item => item.route === '/trade/preview').body;
    for (const attempt of attempts) {
      assert.deepEqual(attempt.body, { ...original, preview_id: interrupted.latestPreview.preview_id },
        'transport retries keep the same one-use token');
      assert.equal(attempt.raw, attempts[0].raw, 'transport retries are byte-identical');
    }
    scenarios.push(`connection lost before headers: ${attempts.length} measured transport attempts, identical one-use token, uncertain outcome`);

    // Opening balances use an independent read/preview/commit channel, including direct navigation.
    let shellFxReads = 0;
    const openBalance = async (mode = 'opening-success', language = 'it') => {
      await control({ mode, language });
      await w.loadFile(path.join(appRoot, 'dist/index.html'), { hash: '/trades?mode=opening' });
      await new Promise(resolve => { w.webContents.once('did-finish-load', resolve); w.webContents.reload(); });
      await waitFor(() => document.getElementById('f7-op-ticker'), 'opening form');
      await waitFor(() => document.documentElement.lang === (document.querySelector('#f7-mode-opening')?.textContent === 'Opening position' ? 'en' : 'it'), 'opening language');
      await waitFor(() => document.querySelector('[data-opening-entry] [aria-busy="false"]'), 'opening register');
      shellFxReads = (await control()).requests.filter(r => r.route === '/fx').length;
    };
    const fillBalance = async (language = 'it') => {
      for (const [id, value] of [['ticker', 'SYNTHOPEN'], ['qty', language === 'it' ? '2,5' : '2.5'],
        ['cost', '0'], ['currency', 'USD'], ['day', DAY], ['source', 'Original broker statement'],
        ['name', 'Original holding name'], ['note', 'Nota originale\nUnchanged original note']]) await field('f7-op-' + id, value);
    };
    const submitBalance = () => js(() => document.querySelector('#f7-op-ticker').closest('form').querySelector('[type="submit"]').click());
    const openingCounts = async (previews, writes) => {
      const state = await control();
      assert.equal(state.requests.filter(r => r.route === '/positions/opening/preview').length, previews);
      assert.equal(state.requests.filter(r => r.route === '/positions/opening' && r.method === 'POST').length, writes);
      assert.ok(state.requests.every(r => !['/trade', '/trade/preview', '/cash/movement', '/trades'].includes(r.route)), 'opening flow makes no trade or cash calls');
      assert.equal(state.requests.filter(r => r.route === '/fx').length, shellFxReads, 'opening actions add no FX reads beyond the measured shell startup');
      return state;
    };
    for (const language of ['it', 'en']) {
      await openBalance('opening-success', language); await fillBalance(language);
      await submitBalance(); const previewText = await dialog();
      assert.match(previewText, language === 'it' ? /non acquisto|Non crea acquisti/ : /Creates no purchases/);
      assert.equal(await row(language === 'it' ? 'Costo medio in valuta' : 'Average cost in currency'), '0 USD');
      assert.equal(await row(language === 'it' ? 'Cassa disponibile' : 'Available cash'), language === 'it' ? 'n.d.' : 'n/a');
      assert.equal(await row(language === 'it' ? 'Saldo noto al' : 'Balance known as of'), DAY);
      await waitFor(() => Number(getComputedStyle(document.querySelector('[role="alertdialog"]')).opacity) >= 0.999, 'confirmation animation complete');
      await js(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      fs.writeFileSync(path.join(config.temporary, `opening-${language}-preview.png`), (await w.capturePage(undefined, { stayHidden: true })).toPNG());
      const before = await openingCounts(1, 0);
      const source = before.requests.find(r => r.route === '/positions/opening/preview').body;
      assert.equal(source.quantita, 2.5); assert.equal(source.prezzo_medio, 0); assert.equal(source.as_of, DAY);
      assert.equal(source.nota, 'Nota originale\nUnchanged original note');
      assert.equal(await js(() => document.getElementById('f7-op-qty').matches(':disabled')), true);
      await cancel(); await openingCounts(1, 0);
      await submitBalance(); await dialog();
      await js(() => { const button = document.querySelector('.cfm-actions .go'); button.click(); button.click(); });
      await waitFor(() => document.querySelector('[data-opening-receipt]')?.innerText.includes('#71'), 'opening receipt');
      const after = await openingCounts(2, 1);
      const written = after.requests.find(r => r.route === '/positions/opening' && r.method === 'POST');
      assert.deepEqual(written.body, { ...source, preview_id: after.latestPreview.preview_id });
      assert.equal(written.language, language);
      await js(() => [...document.querySelectorAll('[data-opening-entry] button')].find(b => /RILEGGI IL TICKER|READ BACK THE TICKER/.test(b.textContent)).click());
      await waitFor(() => /Saldo riletto dal registro|Balance read back from the register/.test(document.body.innerText), 'opening readback');
      assert.match(await js(() => document.querySelector('[data-opening-receipt]').innerText), /Original broker statement/);
      await js(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      fs.writeFileSync(path.join(config.temporary, `opening-${language}-receipt.png`), (await w.capturePage(undefined, { stayHidden: true })).toPNG());
      scenarios.push(`opening ${language}: zero cost + foreign currency + source preserved + day-only + cancel + frozen one-use commit + receipt and readback; no FX/trades/cash`);
    }
    for (const mode of ['opening-preview409', 'opening-mismatch', 'opening-post409', 'opening-post500', 'opening-malformed-receipt']) {
      await openBalance(mode); await fillBalance(); await submitBalance();
      if (mode === 'opening-preview409' || mode === 'opening-mismatch') {
        await waitFor(() => /SYNTHETIC_OPENING_EXISTS|Anteprima saldo incompleta/.test(document.body.innerText), mode);
        assert.equal(await js(() => document.querySelector('[role="alertdialog"]')), null);
        await openingCounts(1, 0);
      } else {
        await dialog(); await confirm();
        await waitFor(() => /Saldo non registrato|Esito incerto/.test(document.body.innerText), mode);
        const page = await js(() => document.querySelector('[data-opening-entry]').innerText);
        if (mode === 'opening-post409') assert.match(page, /Saldo non registrato/);
        else {
          assert.match(page, /Esito incerto/); assert.doesNotMatch(page, /Saldo non registrato/);
          assert.equal(await js(() => document.getElementById('f7-op-ticker').closest('form').querySelector('[type="submit"]').disabled), true);
          await submitBalance();
        }
        await openingCounts(1, 1);
      }
      scenarios.push(mode + ': fail-closed preview or declared write refusal/uncertainty; no automatic resend');
    }

    await control({ language: 'it' });
    await open('success'); await fill();
    await field('f7-qt', '2,5'); await field('f7-pz', '30,50');
    await field('f7-rz', 'Bozza invariata / unchanged draft');
    await js(() => document.getElementById('f7-mode-opening').click());
    await waitFor(() => document.getElementById('f7-op-ticker'), 'opening first visit');
    await fillBalance();
    await js(() => document.getElementById('f7-mode-trade').click());
    assert.equal(await js(() => document.getElementById('f7-qt').value), '2,5');
    assert.equal(await js(() => document.getElementById('f7-rz').value), 'Bozza invariata / unchanged draft');
    await js(() => document.getElementById('f7-mode-opening').click());
    assert.equal(await js(() => document.getElementById('f7-op-note').value), 'Nota originale\nUnchanged original note');
    await js(() => document.getElementById('f7-mode-trade').click());
    scenarios.push('switching trade/opening tabs preserves both unsaved raw drafts');

    const setLanguage = async language => {
      await js(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'F19', bubbles: true })));
      await waitFor(() => document.querySelector('.f11v input[name="language"]:not(:disabled)'), 'language preference settings');
      await js(language => document.querySelector(`.f11v input[name="language"][value="${language}"]`).click(), language);
      await js(() => document.getElementById('language-title').closest('section').querySelector('button').click());
      await waitFor(() => {
        const section = document.getElementById('language-title')?.closest('section');
        return section?.querySelector('input[name="language"]:checked')?.value === document.documentElement.lang
          && !section.querySelector('button')?.disabled;
      }, 'saved and read back language');
      assert.equal(await js(() => document.documentElement.lang), language);
      await js(() => document.querySelector('.f11v .phead button').click());
    };
    await setLanguage('en');
    assert.equal(await js(() => document.getElementById('f7-qt').value), '2,5');
    assert.equal(await js(() => document.getElementById('f7-pz').value), '30,50');
    assert.equal(await js(() => document.getElementById('f7-rz').value), 'Bozza invariata / unchanged draft');
    assert.match(await js(() => document.querySelector('#f7-tk').closest('.rq').innerText), /TRADE TICKET/);
    // 13/09: a field keeps the grammar it was written in until it is cleared (useNumericDraft and
    // tests/i18n/trade-draft.test.cjs, both b81e113). Clearing first makes the new value English.
    await field('f7-qt', ''); await field('f7-qt', '1,234'); await submit();
    await waitFor(() => document.querySelector('#f7-tk').closest('.rq').innerText.includes('ambiguous'), 'English numeric ambiguity');
    assert.equal((await control()).requests.filter(r => r.route === '/trade/preview').length, 0);
    await setLanguage('it');
    assert.equal(await js(() => document.getElementById('f7-qt').value), '1,234');
    assert.match(await js(() => document.querySelector('#f7-tk').closest('.rq').innerText), /IL TAGLIANDO/);
    await setLanguage('en');
    assert.match(await js(() => document.querySelector('#f7-tk').closest('.rq').innerText), /ambiguous/);
    await field('f7-qt', '2.5'); await field('f7-pz', '30.50'); await submit(); await dialog();
    const enRequest = (await control()).requests.find(r => r.route === '/trade/preview');
    assert.equal(enRequest.body.quantita, 2.5); assert.equal(enRequest.body.prezzo, 30.5);
    assert.equal(enRequest.language, 'en');
    assert.match(await row('Applied FX rate'), /historical/);
    assert.equal(await row('Quantity'), '2.5'); await cancel();
    scenarios.push('F19 IT→EN→IT→EN preserves trade draft, reactive numeric errors and historical preview amounts/decision rules');

    await js(() => document.getElementById('f7-mode-opening').click());
    assert.equal(await js(() => document.getElementById('f7-op-qty').value), '2,5');
    assert.match(await js(() => document.querySelector('[data-opening-entry]').innerText), /DOCUMENTED OPENING POSITION/);
    await field('f7-op-precision', 'second');
    await field('f7-op-time', '09:15:42'); await submitBalance(); await dialog();
    assert.equal(await row('Balance known as of'), DAY + ' 09:15:42');
    assert.equal(await row('Documented precision'), 'Day and measured time');
    const balanceRequests = (await control()).requests.filter(r => r.route === '/positions/opening/preview');
    assert.equal(balanceRequests.at(-1).body.as_of, DAY + 'T09:15:42');
    assert.equal(balanceRequests.at(-1).body.prezzo_medio, 0);
    await cancel();
    await setLanguage('it');
    assert.equal(await js(() => document.getElementById('f7-op-time').value), '09:15:42');
    assert.equal(await js(() => document.getElementById('f7-op-source').value), 'Original broker statement');
    assert.match(await js(() => document.querySelector('[data-opening-entry]').innerText), /POSIZIONE INIZIALE DOCUMENTATA/);
    await setLanguage('en');
    const preferenceWrites = (await control()).requests.filter(r => r.method === 'PUT' && r.route === '/preferences');
    assert.equal(preferenceWrites.length, 5);
    assert.ok(preferenceWrites.every(r => ['it', 'en'].includes(r.body.language)));
    assert.equal((await control()).requests.filter(r => r.route === '/trade' || r.route === '/cash/movement'
      || r.route === '/positions/opening' && r.method === 'POST').length, 0);
    await new Promise(resolve => { w.webContents.once('did-finish-load', resolve); w.webContents.reload(); });
    await waitFor(() => document.querySelector('#f7-mode-opening')?.textContent === 'Opening position', 'language persists after reload');
    assert.equal(await js(() => document.documentElement.lang), 'en');
    scenarios.push('F19 language change preserves opening draft/measured time and source; preference readback persists on reload; no economic writes');
    assert.deepEqual(preloadErrors, []);
    assert.equal(await js(() => window.bellomberg.apiUrl), config.origin);
    const prefs = w.webContents.getLastWebPreferences();
    assert.ok(prefs.sandbox && prefs.contextIsolation && !prefs.nodeIntegration);
    finish({ ok: true, scenarios, blocked });
  } catch (error) {
    const page = w && !w.isDestroyed() ? await js(() => document.body.innerText.slice(-3000)).catch(String) : null;
    finish({ ok: false, error: String(error), stack: error.stack, scenarios, preloadErrors, blocked, page });
  }
}

if (process.argv.includes('--renderer-contract')) {
  renderer(JSON.parse(process.argv[process.argv.indexOf('--renderer-contract') + 1]));
} else {
  runner().catch(error => { console.error(error); process.exitCode = 1; });
}
