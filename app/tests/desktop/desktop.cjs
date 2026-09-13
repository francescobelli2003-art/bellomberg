// Hidden Electron renderer/preload smoke test with an ephemeral synthetic HTTP service.
// It never imports the application's main process or starts a Python backend.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { spawn } = require('node:child_process');
const appRoot = path.resolve(__dirname, '../..');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'bellomberg-desktop-test-'));
const requests = [];
const requestDetails = [];
let language = null;
const server = http.createServer(async (req, res) => {
  requests.push(req.url);
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type,X-BB-Token,X-BB-Language');
  res.setHeader('Access-Control-Allow-Methods', 'GET,PUT,POST,OPTIONS');
  res.setHeader('Content-Type', 'application/json');
  if (req.method === 'OPTIONS') { res.end('{}'); return; }
  const route = new URL(req.url, 'http://127.0.0.1').pathname;
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const input = chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) : null;
  requestDetails.push({ method: req.method, route, language: req.headers['x-bb-language'], input });
  let body = {};
  if (route === '/health') body = { status: 'ok', brand: 'Synthetic', version: 'test' };
  else if (route === '/preferences') {
    if (req.method === 'PUT') {
      assert.ok(['it', 'en'].includes(input.language));
      assert.equal(req.headers['x-bb-token'], 'synthetic-token');
      language = input.language;
    }
    body = { language: language || 'it', selected: language !== null, source: language ? 'preferences' : 'compatibility_default' };
  }
  else if (route === '/auth/status') body = { configured: true, default_pin: false };
  else if (route === '/mandato') body = { dichiarato: true, causa: null };
  else if (route === '/agents/list') body = { agents: [{ id: 'quant', name: 'Synthetic Quant', role: 'test', color: '#FFA51E', model: 'synthetic' }], engines: {} };
  else if (/^\/chat\/[^/]+\/sessions$/.test(route)) body = { sessions: [] };
  else if (route === '/fx') body = { rates: {} };
  else if (route === '/system/tasks') body = { tasks: [] };
  else if (route.startsWith('/news/')) body = { items: [] };
  else { res.statusCode = 503; body = { detail: 'synthetic unavailable endpoint' }; }
  res.end(JSON.stringify(body));
});

async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  const fixture = path.join(temporary, 'smoke.cjs');
  fs.writeFileSync(fixture, `
    const {app,BrowserWindow}=require('electron');
    const path=require('node:path');
    app.setPath('userData',path.join(${JSON.stringify(temporary)},'userdata'));
    app.disableHardwareAcceleration();
    process.env.BELLOMBERG_LAUNCH_ID='synthetic-launch';
    process.env.BELLOMBERG_DESKTOP_API_URL=${JSON.stringify(origin)};
    let failed=false;
    const finish=(value)=>{console.log('BB_DESKTOP_RESULT '+JSON.stringify(value));app.exit(value.ok?0:1)};
    app.whenReady().then(async()=>{
      const w=new BrowserWindow({show:false,webPreferences:{preload:${JSON.stringify(path.join(appRoot, 'dist-electron/preload.mjs'))},contextIsolation:true,nodeIntegration:false,sandbox:true,additionalArguments:['--bellomberg-launch-id=synthetic-launch','--bellomberg-api-port=${server.address().port}']}});
      w.webContents.on('preload-error',(_e,_p,error)=>{failed=true;console.error(error)});
      const evaluate = source => w.webContents.executeJavaScript(source);
      const waitFor = async (source, label) => {
        for (let i=0;i<100;i++) { if (await evaluate(source)) return; await new Promise(r=>setTimeout(r,100)); }
        throw new Error(label + ': ' + await evaluate('document.body.innerText.slice(0,900)'));
      };
      const reload = async () => { await new Promise(resolve=>{w.webContents.once('did-finish-load',resolve);w.webContents.reload()}); };
      w.webContents.session.webRequest.onBeforeRequest({urls:['http://*/*','https://*/*']},(details,callback)=>callback({cancel:!details.url.startsWith(${JSON.stringify(origin + '/')})}));
      await w.loadFile(${JSON.stringify(path.join(appRoot, 'dist/index.html'))},{hash:'/chat'});
      await new Promise(r=>setTimeout(r,500));
      console.log('BB_PRELOAD '+JSON.stringify(await w.webContents.executeJavaScript('({bridge:window.bellomberg,storage:localStorage.getItem("bellomberg_unlocked_v1")})')));
      await evaluate('localStorage.setItem("bellomberg_token_v1","synthetic-token");localStorage.setItem("bellomberg_unlocked_v1",JSON.stringify({ts:Date.now()}));localStorage.setItem("bellomberg_last_launch_id","synthetic-launch");localStorage.setItem("bellomberg.lingua","en");');
      await reload();
      await waitFor('document.body.innerText.includes("Choose your language") && document.querySelector("fieldset")?.disabled === false', 'first explicit choice');
      if (await evaluate('!!document.querySelector("input[name=language]:checked")')) throw new Error('browser cache bypassed the explicit first choice');
      await evaluate('document.querySelector("input[name=language][value=en]").click()');
      await evaluate('Array.from(document.querySelectorAll("button")).find(b=>b.textContent.includes("SAVE LANGUAGE")).click()');
      await waitFor('document.documentElement.lang === "en" && !!document.querySelector("textarea")', 'English first launch');
      const draft = 'SYNTHETIC UNSENT DRAFT 731';
      await evaluate('(()=>{const e=document.querySelector("textarea");Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,"value").set.call(e,'+JSON.stringify(draft)+');e.dispatchEvent(new Event("input",{bubbles:true}));})()');
      await evaluate('window.dispatchEvent(new Event("bb:settings"))');
      await waitFor('!!document.querySelector("input[name=language][value=en]:checked") && document.querySelector("fieldset")?.disabled === false', 'saved preference in settings');
      await evaluate('document.querySelector("input[name=language][value=it]").click()');
      await evaluate('Array.from(document.querySelectorAll("button")).find(b=>b.textContent.includes("SAVE LANGUAGE")).click()');
      await waitFor('document.documentElement.lang === "it" && localStorage.getItem("bellomberg.lingua") === "it"', 'Italian preference verified');
      if (await evaluate('document.querySelector("textarea")?.value') !== draft) throw new Error('language switch discarded the unsent chat draft');
      await evaluate('window.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true}))');
      await reload();
      await waitFor('document.documentElement.lang === "it" && !!document.querySelector("textarea")', 'persisted language on reload');
      for(let i=0;i<80;i++){
        const view=await w.webContents.executeJavaScript('({bridge:window.bellomberg,chat:/desk conversazionale/i.test(document.body.innerText),agent:document.body.innerText.includes("SYNTHETIC QUANT"),csp:!!document.querySelector("meta[http-equiv=Content-Security-Policy]")})');
        if(view.chat&&view.agent){const p=w.webContents.getLastWebPreferences();finish({ok:!failed&&view.bridge?.apiUrl===${JSON.stringify(origin)}&&view.bridge?.launchId==='synthetic-launch'&&view.csp&&p.sandbox&&p.contextIsolation&&!p.nodeIntegration,view,security:{sandbox:p.sandbox,contextIsolation:p.contextIsolation,nodeIntegration:p.nodeIntegration}});return;}
        await new Promise(r=>setTimeout(r,100));
      }
      finish({ok:false,reason:'renderer never reached synthetic chat',view:await w.webContents.executeJavaScript('({bridge:window.bellomberg,storage:localStorage.getItem("bellomberg_unlocked_v1"),launch:localStorage.getItem("bellomberg_last_launch_id")})'),text:await w.webContents.executeJavaScript('document.body.innerText.slice(0,600)')});
    }).catch(error=>finish({ok:false,error:String(error)}));
  `);
  const env = { ...process.env };
  delete env.ELECTRON_RUN_AS_NODE;
  delete env.BELLOMBERG_BACKEND_DIR;
  delete env.BELLOMBERG_PYTHON;
  let output = '';
  const child = spawn(require('electron'), [fixture], { env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', d => output += d);
  child.stderr.on('data', d => output += d);
  const timer = setTimeout(() => child.kill(), 45000);
  const code = await new Promise((resolve, reject) => { child.once('error', reject); child.once('close', resolve); });
  clearTimeout(timer);
  fs.writeFileSync(path.join(temporary, 'output.log'), output);
  const line = output.split(/\r?\n/).find(x => x.startsWith('BB_DESKTOP_RESULT '));
  if (!line) throw new Error('Electron smoke did not produce a result; log: ' + path.join(temporary, 'output.log'));
  const result = JSON.parse(line.slice('BB_DESKTOP_RESULT '.length));
  assert.equal(code, 0, JSON.stringify(result));
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.ok(requests.includes('/mandato'), 'renderer used the synthetic API');
  const writes = requestDetails.filter(r => !['GET', 'OPTIONS'].includes(r.method));
  assert.deepEqual(writes.map(r => [r.method, r.route, r.input.language]), [['PUT', '/preferences', 'en'], ['PUT', '/preferences', 'it']]);
  assert.ok(requestDetails.some(r => r.route === '/agents/list' && r.language === 'en'), 'new reads use the selected English language');
  assert.equal(language, 'it');
  console.log('desktop smoke: hidden renderer, sandbox/preload/CSP, explicit first language, IT/EN persistence and preserved unsent draft; no paid API or portfolio writes');
  console.log('Desktop evidence: ' + temporary);
}
main().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => server.close());
