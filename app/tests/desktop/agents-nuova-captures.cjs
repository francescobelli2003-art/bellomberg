// Offline Electron captures of the Nuova "Agenti in diretta" page at 1920, 2560, 3440 and 5120 px,
// light and dark: at rest, running (round 1), synthesis, completed, API errors with partial cost,
// stale heartbeat and backend unreachable. Every /agents read is a synthetic fixture built here;
// no run is started, no model, backend or market API is contacted.
// BB_AGENTS_CAPTURE=dir sets the output folder; BB_AGENTS_SIZES=1920x1080,2560x1440 narrows the sizes;
// BB_AGENTS_STATES=corso,errore narrows the states.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const root = path.resolve(__dirname, '../..');

const ROSTER = [
  ['capo', 'Capo', 'Analista senior PM', '#ff9500'], ['options', 'Options Flow', 'Flussi opzioni', '#a78bfa'],
  ['quant', 'Quant', 'Analista quantitativo', '#00e5ff'], ['fundamentals', 'Fundamentals', 'Analista fondamentale', '#00ff95'],
  ['eventdesk', 'Event Desk', 'Eventi (notizie + geopolitica)', '#f472b6'], ['macro', 'Macro', 'Stratega macroeconomico', '#ffc760'],
  ['crypto', 'Crypto', 'Specialista cripto', '#fbbf24'],
].map(([id, name, role, color]) => ({ id, name, role, color, model: 'fixture-model' }));
const DESKS = ['options', 'quant', 'fundamentals', 'eventdesk', 'macro', 'crypto'];
const TOOLS = {
  options: [['get_vol_surface', "{'ticker': 'SPY'}"], ['get_options_chain', "{'ticker': 'ZZOPT'}"], ['get_put_call_ratio', "{'ticker': 'QQQ'}"]],
  quant: [['get_portfolio_risk', '{}'], ['quant_compute', "{'expr': 'corr_90d'}"], ['get_price_history', "{'ticker': 'ACME'}"]],
  fundamentals: [['get_fundamentals', "{'ticker': 'ZZTEST'}"], ['get_consensus_estimates', "{'ticker': 'ZZCONS'}"], ['get_insider_trades', "{'ticker': 'ACME.MI'}"]],
  eventdesk: [['search_news', "{'query': 'BCE tassi ottobre'}"], ['get_polymarket_events', "{'query': 'midterm'}"], ['get_earnings_calendar', '{}']],
  macro: [['get_fx_rates', "{'market': 'EUR/USD'}"], ['get_yield_curve', "{'market': 'US'}"], ['get_macro_calendar', '{}']],
  crypto: [['get_onchain_flows', "{'ticker': 'BTC-USD'}"], ['get_price_history', "{'ticker': 'ETH-USD'}"]],
};
const clock = ms => new Date(ms).toTimeString().slice(0, 8);

/** A synthetic heartbeat for one state, anchored to the current clock. */
function heartbeat(kind) {
  const now = Date.now();
  const closed = kind === 'riposo' || kind === 'completata';
  const runSec = { corso: 760, errore: 1458, fermo: 1570, sintesi: 1862, riposo: 2106, completata: 2106 }[kind] || 760;
  const start = closed ? now - 3 * 3600e3 : now - runSec * 1000;
  const round = { corso: 1, fermo: 1, errore: 2, sintesi: 2 }[kind] ?? 2;
  const r2 = ['options', 'fundamentals', 'eventdesk'];
  const R = [[0, 372], [372, 945], [945, 1730]];
  const log = [];
  for (let r = 0; r <= round; r++) {
    const [a, b] = R[r];
    const end = !closed && r === round ? Math.min(b, runSec - 3) : b;
    DESKS.forEach((d, di) => {
      if (r === 2 && !r2.includes(d)) return;
      const n = r === 0 ? 5 : 6;
      for (let i = 0; i < n; i++) {
        let t = a + 20 + di * 9 + i * ((end - a - 60) / n);
        /* the last call of a desk still at work lands a few seconds ago (or 42 s ago: it is thinking) */
        const fresh = { macro: 3, options: 8, crypto: 15, fundamentals: 42, eventdesk: 5 }[d];
        if (!closed && r === round && i === n - 1 && fresh != null) t = runSec - fresh;
        if (!closed && r === round && (d === 'quant' || (d === 'eventdesk' && kind === 'corso'))) t = Math.min(t, runSec - 150);
        const [tool, input] = TOOLS[d][(i + r) % TOOLS[d].length];
        log.push({ time: clock(start + t * 1000), specialist: d, round: r, tool, input, _t: t });
      }
    });
  }
  log.sort((x, y) => x._t - y._t);
  const tool_log = log.map(({ _t, ...e }) => e);
  const usage = (d, extra = {}) => ({ status: 'ok', cost_eur: { options: .02, quant: .03, fundamentals: .19, eventdesk: .03, macro: .03, crypto: .01, capo: .24, _red_team: .03, _reflection: .01, _action_table: .01 }[d],
    duration_s: { options: 287, quant: 461, fundamentals: 746, eventdesk: 336, macro: 423, crypto: 118, capo: 261, _red_team: 72, _reflection: 48, _action_table: 31 }[d],
    api_calls: 6, in: 34000, out: 16000, cache_read: 310000, cache_write: 105000, ...extra });
  const status = {}, by = {};
  for (const d of DESKS) {
    if (closed || kind === 'sintesi') status[d] = 'done';
    else if (kind === 'errore') status[d] = r2.includes(d) ? 'running' : 'done';
    else status[d] = (d === 'quant' || d === 'eventdesk') ? 'done' : 'running';
    by[d] = usage(d);
  }
  if (kind === 'errore' || closed) by.fundamentals = usage('fundamentals', { status: 'api_error' });
  if (kind === 'errore') { by.crypto = usage('crypto', { status: 'model_unknown', cost_eur: null }); status.eventdesk = 'running'; status.options = 'done'; }
  const body = {
    language: 'it', running: !closed, start_time: new Date(start).toISOString(), current_round: round,
    specialist_status: status, tool_log: closed ? tool_log : tool_log.slice(-50), tool_log_tappato: !closed, n_tool_calls: closed ? tool_log.length : 214 + round * 90,
    r2_specialists: r2, updated_at: new Date(now - 4000).toISOString(), usage_by_specialist: by,
    usage_total: { cost_eur: closed ? .55 : kind === 'errore' ? .44 : kind === 'sintesi' ? .52 : .31, partial: kind === 'errore' || (!closed && kind !== 'sintesi'),
      in: 239200, out: 114800, cache_read: 2225000, cache_write: 755300, fx_source: kind === 'errore' ? 'fallback' : 'live',
      error_agents: kind === 'errore' || closed ? ['fundamentals'] : [], unpriced_agents: kind === 'errore' ? ['crypto'] : [] },
  };
  /* report sintetici (05/10/2026, redesign tavolo + corsie): i round chiusi, piu' i desk che
     hanno gia' consegnato quello in corso; testi inventati, nessun dato reale */
  const testo = (d, r) => `## Tesi ${d} R${r}\nLa posizione sintetica ZZTEST resta coerente con il mandato: il desk ${d} non vede motivi per cambiarla nel round ${r}. Seconda frase di contesto sul titolo ACME.\n\n- Punto sintetico uno\n- Punto sintetico due`;
  const reports = {};
  for (const d of DESKS) for (let r = 0; r <= round; r++) {
    if (r === 2 && !r2.includes(d)) continue;
    if (!closed && kind !== 'sintesi' && r === round && status[d] !== 'done') continue;
    (reports[d] ||= {})[String(r)] = testo(d, r);
  }
  body.reports_by_specialist = reports;
  body.expected_reports = 15;
  if (kind === 'sintesi') {
    body.specialist_status.capo = 'running'; body.updated_at = new Date(now - 130000).toISOString();
    body.usage_by_specialist._red_team = usage('_red_team');
  }
  if (kind === 'fermo') { body.stale_warning = true; body.stale_seconds = 720; body.updated_at = new Date(now - 720000).toISOString(); }
  if (closed) {
    body.completed_at = new Date(start + runSec * 1000).toISOString(); body.memo_id = 48;
    body.specialist_status.capo = 'done';
    for (const s of ['capo', '_red_team', '_reflection', '_action_table']) body.usage_by_specialist[s] = usage(s);
  }
  return body;
}

async function run() {
  const { server } = require('./chat-modes.cjs').startFixture();
  const original = server.listeners('request')[0];
  const fx = { kind: 'riposo' };
  server.removeAllListeners('request');
  server.on('request', async (req, res) => {
    const route = new URL(req.url, 'http://localhost').pathname;
    const send = (body, status = 200) => { res.statusCode = status; res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(body)); };
    if (route === '/__agents') { let raw = ''; for await (const c of req) raw += c; fx.kind = JSON.parse(raw || '{}').kind || fx.kind; return send({ ok: true }); }
    if (req.method === 'GET' && route === '/preferences') return send({ language: 'it', selected: true, source: 'preferences' });
    if (req.method === 'GET' && route === '/agents/list') return send({ agents: ROSTER, engines: { committee_r0: 'claude-sonnet-fixture', committee_r1_r2: 'claude-opus-fixture', capo: 'claude-opus-fixture' } });
    /* copertura filing sintetica: senza, la riga Filing della run non ha dati da leggere */
    if (req.method === 'GET' && route === '/filings') return send({ titoli: [],
      copertura: { totale: 12, con_confronto: 7, aggiornati: 5, non_aggiornati: 2, senza_confronto: 0, senza_profilo: 5, esclusi: 0 },
      contesto: { caratteri: 9000, budget: 14000, omessi_totali: 0 }, aggiornamento: null });
    if (req.method === 'GET' && route === '/agents/live') {
      if (fx.kind === 'giu') return send({ detail: 'connect ECONNREFUSED 127.0.0.1:8000' }, 503);
      return send(heartbeat(fx.kind));
    }
    if (req.method !== 'GET' && /^\/(consigliere|agents\/live\/reset)/.test(route)) return send({ detail: 'writes are not allowed in captures' }, 403);
    return original(req, res);
  });
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'bb-agents-nuova-'));
  const directory = path.resolve(process.env.BB_AGENTS_CAPTURE || path.join(root, '../outputs/agents-captures'));
  fs.mkdirSync(directory, { recursive: true });
  const sizes = (process.env.BB_AGENTS_SIZES || '1920x1080,2560x1440,3440x1440,5120x1440').split(',').map(s => s.split('x').map(Number));
  const states = (process.env.BB_AGENTS_STATES || 'riposo,corso,sintesi,errore,fermo,completata,giu').split(',');
  const config = { origin: `http://127.0.0.1:${server.address().port}`, temporary, directory, sizes, states };
  const entry = path.join(temporary, 'entry.cjs');
  fs.writeFileSync(entry, `require('electron').app.setPath('userData',${JSON.stringify(path.join(temporary, 'userdata'))});require('electron').app.whenReady().then(()=>require(${JSON.stringify(__filename)}).renderer(${JSON.stringify(config)})).catch(e=>{console.error(e);require('electron').app.exit(1)})`);
  const env = { ...process.env }; delete env.ELECTRON_RUN_AS_NODE;
  const child = spawn(require('electron'), [entry], { env, stdio: ['ignore', 'pipe', 'pipe'] });
  let log = ''; child.stdout.on('data', b => log += b); child.stderr.on('data', b => log += b);
  const timer = setTimeout(() => child.kill(), 900000);
  try {
    const code = await new Promise(r => child.once('close', r));
    fs.writeFileSync(path.join(directory, 'electron.log'), log);
    console.log(log.slice(-3000)); assert.equal(code, 0);
  } finally { clearTimeout(timer); server.closeAllConnections(); server.close(); }
}

async function renderer(config) {
  const { app, BrowserWindow } = require('electron');
  process.env.BELLOMBERG_LAUNCH_ID = 'synthetic-chat-modes';
  process.env.BELLOMBERG_DESKTOP_API_URL = config.origin;
  const win = new BrowserWindow({ show: false, width: 1920, height: 1080, useContentSize: true, enableLargerThanScreen: true,
    webPreferences: { preload: path.join(root, 'dist-electron/preload.mjs'), contextIsolation: true, sandbox: true, backgroundThrottling: false,
      additionalArguments: ['--bellomberg-launch-id=synthetic-chat-modes', '--bellomberg-api-port=' + new URL(config.origin).port] } });
  win.webContents.session.webRequest.onBeforeRequest({ urls: ['http://*/*', 'https://*/*'] }, (d, cb) => cb({ cancel: !d.url.startsWith(config.origin + '/') }));
  const js = (fn, ...args) => win.webContents.executeJavaScript(`(${fn.toString()})(...${JSON.stringify(args)})`);
  const pause = ms => new Promise(r => setTimeout(r, ms));
  const wait = async (fn, label, ...args) => { for (let i = 0; i < 300; i++) { if (await js(fn, ...args)) return; await pause(50); } throw new Error('Timed out: ' + label); };
  const scenario = kind => fetch(config.origin + '/__agents', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind }) });
  const vista = v => wait(v => document.querySelector('.bbn-agents')?.dataset.vista === v, 'vista ' + v, v);
  const shot = async name => { await pause(400);
    const overflow = await js(() => document.documentElement.scrollWidth > innerWidth);
    assert.equal(overflow, false, 'horizontal overflow in ' + name);
    fs.writeFileSync(path.join(config.directory, name + '.png'), (await win.webContents.capturePage()).toPNG()); };
  const reload = () => new Promise(r => { win.webContents.once('did-finish-load', r); win.webContents.reload(); });
  try {
    await win.loadURL(config.origin + '/#/agents');
    await js(() => {
      localStorage.setItem('bellomberg_token_v1', 'synthetic-chat-fixture-token');
      localStorage.setItem('bellomberg_unlocked_v1', JSON.stringify({ ts: Date.now() }));
      localStorage.setItem('bellomberg_last_launch_id', 'synthetic-chat-modes');
      localStorage.setItem('bellomberg.lingua', 'it');
      localStorage.removeItem('bellomberg_active_run');
    });
    for (const theme of ['light', 'dark']) {
      await js(t => localStorage.setItem('bellomberg.interface-theme.v1', t), theme);
      for (const [w, h] of config.sizes) {
        win.setContentSize(w, h);
        const tag = `${theme}-${w}x${h}`;
        await scenario('riposo'); await reload();
        await wait(() => document.querySelectorAll('.bbn-agents .ag-desk').length === 6, 'desks');
        await vista('riposo');
        if (config.states.includes('riposo')) await shot(`${tag}-1-riposo`);
        const steps = [['corso', 'viva', '2-corso'], ['sintesi', 'viva', '3-sintesi'], ['errore', 'viva', '4-errore'], ['fermo', 'viva', '5-fermo'],
          ['completata', 'finita', '6-completata'], ['giu', 'cieco', '7-giu']];
        for (const [kind, v, name] of steps) {
          if (kind === 'completata' && !config.states.includes('completata')) continue;
          await scenario(kind); await vista(v);
          await pause(1700);   // a second poll, so the clocks have ticked on the new state
          if (config.states.includes(kind)) await shot(`${tag}-${name}`);
        }
      }
    }
    console.log('AGENTS_NUOVA_CAPTURES_OK ' + fs.readdirSync(config.directory).filter(f => f.endsWith('.png')).length);
  } catch (e) { console.error(e); try { fs.writeFileSync(path.join(config.directory, 'failure.png'), (await win.webContents.capturePage()).toPNG()); } catch {} win.destroy(); app.exit(1); return; }
  win.destroy(); app.exit(0);
}
if (require.main === module) run().catch(e => { console.error(e); process.exitCode = 1; });
module.exports = { renderer, heartbeat };
