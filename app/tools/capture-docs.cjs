// Public documentation capture: the actual built renderer, an invented fixture,
// and one declared watermark. No application main, Python backend or providers.
// --check (default) builds and checks four scenes without saving PNGs.
// --candidate writes review candidates ONLY to a newly created OS TEMP directory.
// Neither mode publishes files or approves pixels. Supply --python /absolute/python:
// an installed interpreter, never an auto-installing WindowsApps alias/launcher.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const { fileURLToPath } = require('node:url');
const { spawn, spawnSync } = require('node:child_process');
const ROOT = path.resolve(__dirname, '../..');
const FIXTURE = 'app/tests/fixtures/documentation-demo.json';
const SCRIPT = 'app/tools/capture-docs.cjs';
const MANIFEST = 'docs/assets/screenshots/manifest.json';
const WATERMARK = 'DEMO · SYNTHETIC DATA';
const RESULT = 'BB_DOCS_RESULT ';
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const canonical = bytes => {
  try { new TextDecoder('utf-8', { fatal: true }).decode(bytes); }
  catch { return bytes; }
  return bytes.includes(0) ? bytes : Buffer.from(bytes.toString('utf8').replace(/\r\n/g, '\n'));
};
// Python ensure_ascii=True compatibility, including supplementary Unicode names.
const asciiJson = value => JSON.stringify(value).replace(/[\u007f-\uffff]/g,
  c => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'));
const digestRows = (tree, normalize = false) => sha(asciiJson(Object.keys(tree).sort((a, b) => Buffer.compare(Buffer.from(a), Buffer.from(b)))
  .map(name => [name, sha(normalize ? canonical(tree[name]) : tree[name])])));
const sourcePath = name => ['app/src/', 'app/electron/', 'app/public/', 'app/resources/'].some(p => name.startsWith(p))
  || /^app\/[^/]+$/.test(name) && (['.json', '.js', '.cjs', '.mjs', '.ts', '.html', '.css'].includes(path.extname(name))
    || ['.npmrc', '.nvmrc', '.browserslistrc'].includes(path.basename(name)));
const sourceDigest = tree => digestRows(Object.fromEntries(Object.entries(tree).filter(([name]) => sourcePath(name))), true);

function inside(child, parent) {
  const relative = path.relative(parent, child);
  return relative !== '' && !relative.startsWith('..' + path.sep) && relative !== '..' && !path.isAbsolute(relative);
}
function childEnvironment(temporary, inherited = process.env) {
  assert.ok(inside(fs.realpathSync(temporary), fs.realpathSync(os.tmpdir())), 'Profile must be a new temporary directory');
  const env = {};
  for (const [key, value] of Object.entries(inherited)) if (['PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE'].includes(key.toUpperCase())) env[key] = value;
  for (const key of ['TEMP', 'TMP', 'APPDATA', 'LOCALAPPDATA', 'HOME', 'USERPROFILE', 'BELLOMBERG_DATA_DIR']) {
    env[key] = path.join(temporary, key.toLowerCase()); fs.mkdirSync(env[key], { recursive: true });
  }
  env.PYTHONIOENCODING = 'utf-8'; env.PYTHONDONTWRITEBYTECODE = '1';
  return env;
}
function resolvePython(value) {
  assert.ok(typeof value === 'string' && path.isAbsolute(value), 'Supply an absolute installed Python path with --python (or BB_DOCS_PYTHON)');
  assert.ok(!/windowsapps/i.test(value) && !/^py(?:thon)?(?:3)?-?manager(?:\.exe)?$/i.test(path.basename(value))
    && !/^py(?:\.exe)?$/i.test(path.basename(value)), 'Python alias or auto-install launcher refused');
  const resolved = fs.realpathSync(value);
  assert.ok(fs.statSync(resolved).isFile() && !/windowsapps/i.test(resolved), 'Installed Python executable required');
  return resolved;
}
function validateOrigin(origin) {
  const url = new URL(origin);
  assert.ok(url.origin === origin && url.hostname === '127.0.0.1' && url.protocol === 'http:'
    && !url.username && !url.password && Number(url.port) >= 8766
    && !['8765', '5173'].includes(url.port), 'Unsafe synthetic origin');
  return origin;
}
function allowedRequest(value, origin, localFiles) {
  try {
    const url = new URL(value);
    if (url.protocol === 'http:') return url.origin === origin && !url.username && !url.password;
    return url.protocol === 'file:' && localFiles.has(path.resolve(fileURLToPath(url)));
  } catch { return false; }
}
const FONT_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36';
function fontAssets(fixture) {
  const assets = fixture.fonts?.assets;
  assert.ok(Array.isArray(assets) && assets.length > 0 && assets.length < 100, 'Pinned font assets required');
  const urls = new Set(), names = new Set();
  for (const asset of assets) {
    const url = new URL(asset.url);
    assert.ok(url.protocol === 'https:' && !url.username && !url.password && !url.hash && !url.port
      && (url.hostname === 'fonts.gstatic.com' && /^\/s\/[A-Za-z0-9/_.-]+\.woff2$/.test(url.pathname) && !url.search
        || url.hostname === 'fonts.googleapis.com' && url.pathname === '/css2' && asset.content_type === 'text/css'), 'Unsafe font URL');
    assert.ok(/^[a-z0-9-]+\.(?:css|woff2)$/.test(asset.filename) && /^[a-f0-9]{64}$/.test(asset.sha256)
      && ['text/css', 'font/woff2'].includes(asset.content_type) && !urls.has(asset.url) && !names.has(asset.filename), 'Invalid font pin');
    urls.add(asset.url); names.add(asset.filename);
  }
  return assets;
}
function loadFonts(fixture, directory) {
  assert.ok(typeof directory === 'string' && path.isAbsolute(directory), 'Supply absolute --font-cache directory');
  const resolved = fs.realpathSync(directory);
  assert.ok(!fs.lstatSync(directory).isSymbolicLink(), 'Linked font cache refused');
  const result = new Map();
  for (const asset of fontAssets(fixture)) {
    const file = path.join(resolved, asset.filename);
    assert.ok(!fs.lstatSync(file).isSymbolicLink() && inside(fs.realpathSync(file), resolved), 'Linked font refused');
    const bytes = fs.readFileSync(file);
    assert.ok(bytes.length > 0 && bytes.length <= 2 * 1024 * 1024 && sha(bytes) === asset.sha256, 'Font bytes differ from pin');
    result.set(asset.url, { ...asset, bytes });
  }
  // The unchanged upstream CSS can reference only inventoried font resources.
  for (const asset of result.values()) if (asset.content_type === 'text/css') {
    const css = asset.bytes.toString('utf8');
    assert.ok(!/@import/i.test(css), 'Font CSS imports refused');
    for (const match of css.matchAll(/url\(([^)]+)\)/g)) assert.ok(result.has(match[1].replace(/^["']|["']$/g, '')), 'Unpinned font CSS resource');
  }
  return result;
}
async function prepareFonts(fixture, transport = fetch) {
  const assets = fontAssets(fixture);
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'bellomberg-doc-fonts-'));
  for (const asset of assets) {
    const response = await transport(asset.url, { redirect: 'error', headers: { 'User-Agent': FONT_UA }, signal: AbortSignal.timeout(20000) });
    assert.ok(response.ok, 'Font download failed: HTTP ' + response.status);
    const bytes = Buffer.from(await response.arrayBuffer());
    assert.ok(bytes.length <= 2 * 1024 * 1024 && sha(bytes) === asset.sha256, 'Font download differs from pin');
    fs.writeFileSync(path.join(directory, asset.filename), bytes, { flag: 'wx' });
  }
  loadFonts(fixture, directory);
  return directory;
}
function readInventory(root, folders, singles = []) {
  const tree = {};
  function visit(name) {
    const absolute = path.join(root, name);
    assert.ok(!fs.lstatSync(absolute).isSymbolicLink() && inside(fs.realpathSync(absolute), root), 'Linked source refused');
    if (fs.statSync(absolute).isDirectory()) {
      for (const child of fs.readdirSync(absolute)) visit(name + '/' + child);
    } else tree[name] = fs.readFileSync(absolute);
  }
  for (const name of folders) if (fs.existsSync(path.join(root, name))) visit(name);
  for (const name of singles) visit(name);
  return tree;
}
function sources(root) {
  return readInventory(root, ['app/src', 'app/electron', 'app/public', 'app/resources'],
    fs.readdirSync(path.join(root, 'app')).map(p => 'app/' + p).filter(sourcePath).concat(FIXTURE, SCRIPT));
}
function fingerprint(root) {
  const tree = sources(root);
  const bundle = readInventory(root, ['app/dist'], ['app/dist-electron/preload.mjs']);
  assert.ok(bundle['app/dist/index.html']?.length && Object.keys(bundle).some(p => p.startsWith('app/dist/assets/') && p.endsWith('.js')), 'Built renderer missing');
  return { source: sourceDigest(tree), bundle: digestRows(bundle), fixture: sha(canonical(tree[FIXTURE])),
    capture: sha(canonical(tree[SCRIPT])), app_version: JSON.parse(tree['app/package.json']).version };
}
function assertUnchanged(before, after) {
  assert.deepEqual(after, before, 'Capture input drift: discard candidates and rebuild');
}
function loadFixture(root) {
  const f = JSON.parse(fs.readFileSync(path.join(root, FIXTURE), 'utf8'));
  assert.equal(f.id, 'documentation-demo'); assert.equal(f.version, 1);
  assert.deepEqual(f.scenes.map(s => s.route), ['/dashboard', '/trades']);
  assert.ok(f.scenes.every(s => /^[a-z-]+$/.test(s.id) && Array.isArray(s.required)));
  assert.ok(f.responses['/portfolio'].positions.every(p => p.ticker.startsWith('DEMO.')));
  return f;
}
function fixtureResponse(fixture, method, route, language) {
  if (method === 'OPTIONS' && (route === '/preferences' || Object.hasOwn(fixture.responses, route))) return { status: 200, body: {} };
  if (method === 'GET' && route === '/preferences') return { status: 200, body: { language, selected: true, source: 'preferences' } };
  if (method === 'GET' && Object.hasOwn(fixture.responses, route)) return { status: 200, body: fixture.responses[route] };
  return { status: 409, body: { code: 'SYNTHETIC_ENDPOINT_REFUSED', detail: 'Unlisted documentation request' } };
}
function readyState(state, scene) {
  return state.route === scene.route && state.language === scene.language && state.ready && state.fonts
    && state.images && state.watermark && state.content && !state.busy && !state.error;
}
async function command(binary, args, options, timeout = 180000) {
  return new Promise((resolve, reject) => {
    const child = spawn(binary, args, { ...options, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    let output = '';
    const timer = setTimeout(() => { child.kill(); reject(new Error('Documentation subprocess timed out: ' + path.basename(binary) + ' ' + path.basename(args[0]) + '\n' + output.slice(-2000))); }, timeout);
    for (const pipe of [child.stdout, child.stderr]) pipe.on('data', b => { output += b; });
    child.on('error', error => { clearTimeout(timer); reject(error); });
    child.on('close', (code, signal) => { clearTimeout(timer); code === 0 ? resolve(output) : reject(new Error('Documentation subprocess failed (' + path.basename(binary) + ', exit=' + code + ', signal=' + signal + '): ' + output.slice(-6000))); });
  });
}
function verifyCandidates(root, temporary, env, python) {
  // Only public, narrowly inventoried inputs. No Bellomberg Python imports.
  const code = 'import importlib.util,sys,pathlib; s=importlib.util.spec_from_file_location("screenshots",sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); t=m.load_tree(pathlib.Path(sys.argv[2])); t={k:v for k,v in t.items() if not k.startswith(m.SCREENSHOT_DIR)}; out=pathlib.Path(sys.argv[3]); t.update({p.relative_to(out).as_posix():p.read_bytes() for p in (out/m.SCREENSHOT_DIR).iterdir() if p.is_file()}); r=m.verify_tree(t); print(len(r))';
  const result = spawnSync(python, ['-I', '-S', '-c', code, path.join(root, 'docs/guide/verify_screenshots.py'), root, temporary], { encoding: 'utf8', env, timeout: 15000 });
  assert.equal(result.status, 0, 'Public PNG/manifest verifier: ' + result.stderr);
  return Number(result.stdout.trim());
}
async function run({ candidate = false, python: pythonPath = process.env.BB_DOCS_PYTHON,
  fontCache = process.env.BB_DOCS_FONT_CACHE } = {}) {
  const python = resolvePython(pythonPath);
  const root = fs.realpathSync(ROOT);
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'bellomberg-docs-'));
  const env = childEnvironment(temporary);
  const fixture = loadFixture(root);
  const fonts = loadFonts(fixture, fontCache);
  const fontDirectory = path.join(temporary, 'pinned-fonts'); fs.mkdirSync(fontDirectory);
  for (const asset of fonts.values()) fs.writeFileSync(path.join(fontDirectory, asset.filename), asset.bytes, { flag: 'wx' });
  const beforeBuild = sourceDigest(sources(root));
  const log = [];
  log.push(await command(process.execPath, [path.join(root, 'app/node_modules/typescript/bin/tsc')], { cwd: path.join(root, 'app'), env }));
  log.push(await command(process.execPath, [path.join(root, 'app/node_modules/vite/bin/vite.js'), 'build'], { cwd: path.join(root, 'app'), env }));
  assert.equal(sourceDigest(sources(root)), beforeBuild, 'Source drift during build');
  fs.writeFileSync(path.join(temporary, 'build.log'), log.join('\n'));
  const before = fingerprint(root);
  const requests = [], unexpected = [];
  const server = http.createServer((req, res) => {
    const route = new URL(req.url, 'http://127.0.0.1').pathname;
    const language = req.headers['x-bb-language'] === 'en' ? 'en' : 'it';
    const result = fixtureResponse(fixture, req.method, route, language);
    const item = { method: req.method, route, language, status: result.status };
    requests.push(item); if (result.status !== 200) unexpected.push(item);
    res.writeHead(result.status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store',
      'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'Content-Type,X-BB-Token,X-BB-Language',
      'Access-Control-Allow-Methods': 'GET,OPTIONS' });
    res.end(JSON.stringify(result.body));
  });
  let receipt;
  try {
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
    const origin = validateOrigin('http://127.0.0.1:' + server.address().port);
    const config = { root, temporary, origin, candidate, scenes: fixture.scenes, before, fontDirectory };
    const output = await command(require(path.join(root, 'app/node_modules/electron')), [__filename, '--documentation-renderer', JSON.stringify(config)], { cwd: path.join(root, 'app'), env }, 60000);
    fs.writeFileSync(path.join(temporary, 'renderer.log'), output);
    const line = output.split(/\r?\n/).find(l => l.startsWith(RESULT));
    assert.ok(line, 'Renderer receipt missing');
    receipt = JSON.parse(line.slice(RESULT.length));
    assert.ok(receipt.ok); assert.equal(unexpected.length, 0, 'Unexpected synthetic endpoint requested');
    assertUnchanged(before, fingerprint(root));
    for (const scene of fixture.scenes) for (const route of scene.required) assert.ok(requests.some(r => r.route === route && r.status === 200), 'Missing measured response: ' + route);
    if (candidate) {
      const manifest = { schema_version: 1, fixture: { id: fixture.id, version: fixture.version, path: FIXTURE, sha256: before.fixture },
        capture: { path: SCRIPT, sha256: before.capture }, app_version: before.app_version,
        build: { source_sha256: before.source, bundle_sha256: before.bundle }, images: receipt.images };
      fs.writeFileSync(path.join(temporary, MANIFEST), JSON.stringify(manifest, null, 2) + '\n');
      assert.equal(verifyCandidates(root, temporary, env, python), receipt.images.length);
      assertUnchanged(before, fingerprint(root));
    }
    receipt = { ...receipt, temporary, origin, inputs: before, build_source_matched: true,
      bundle_recipe: 'Sorted [app/dist/** or app/dist-electron/preload.mjs, SHA256 exact bytes], compact ASCII JSON SHA256',
      requests, unexpected_requests: unexpected, images_saved: candidate ? receipt.images.length : 0,
      watermark_injected_by_capture: true, approval: 'NOT APPROVED: candidate pixels require visual review and private pins' };
    fs.writeFileSync(path.join(temporary, 'capture-receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
    return receipt;
  } catch (error) {
    const captureDir = path.join(temporary, 'docs/assets/screenshots');
    const failure = { ok: false, error: String(error), temporary, inputs: before, requests,
      unexpected_requests: unexpected, images_saved: fs.existsSync(captureDir)
        ? fs.readdirSync(captureDir).filter(name => name.endsWith('.png')).length : 0,
      approval: 'FAILED: no candidate approved; partial temporary files, if any, must not be published' };
    fs.writeFileSync(path.join(temporary, 'capture-receipt.json'), JSON.stringify(failure, null, 2) + '\n');
    throw new Error(String(error) + '\nFailure receipt: ' + path.join(temporary, 'capture-receipt.json'));
  } finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
}

async function renderer(config) {
  const { app, BrowserWindow, session } = require('electron');
  assert.ok(inside(fs.realpathSync(os.tmpdir()), fs.realpathSync(config.temporary)), 'Renderer TEMP must be inside its isolated profile');
  validateOrigin(config.origin);
  for (const name of ['userData', 'sessionData', 'crashDumps', 'logs']) {
    const target = path.join(config.temporary, name); fs.mkdirSync(target, { recursive: true }); app.setPath(name, target);
  }
  app.disableHardwareAcceleration();
  app.commandLine.appendSwitch('disable-background-networking');
  app.commandLine.appendSwitch('disable-component-update');
  app.commandLine.appendSwitch('host-resolver-rules', 'MAP * ~NOTFOUND, EXCLUDE 127.0.0.1');
  const blocked = [], probes = [], scenes = [], images = [], errors = [];
  const local = new Set(Object.keys(readInventory(config.root, ['app/dist']))
    .map(name => path.resolve(config.root, name)));
  const pending = new Set(), routeCounts = {};
  let lastRequest = Date.now(), w, probeMode = false;
  const js = async (fn, ...args) => {
    const result = await w.webContents.executeJavaScript(`(async()=>{try{return {ok:true,value:await (${fn.toString()})(...${JSON.stringify(args)})}}catch(error){return {ok:false,error:String(error)}}})()`);
    if (!result.ok) throw new Error('Renderer script: ' + result.error);
    return result.value;
  };
  const finish = value => { console.log(RESULT + JSON.stringify(value)); app.exit(value.ok ? 0 : 1); };
  try {
    await app.whenReady();
    const isolated = session.fromPartition('documentation-' + crypto.randomUUID());
    const fonts = loadFonts(loadFixture(config.root), config.fontDirectory), servedFonts = [];
    isolated.protocol.handle('https', request => {
      const asset = fonts.get(request.url);
      if (!asset || request.method !== 'GET') {
        (probeMode ? probes : blocked).push({ url: request.url, blocked: true });
        return new Response('Documentation request refused', { status: 403 });
      }
      servedFonts.push({ url: request.url, sha256: sha(asset.bytes) });
      return new Response(asset.bytes, { headers: { 'Content-Type': asset.content_type, 'Access-Control-Allow-Origin': '*', 'Cache-Control': 'no-store' } });
    });
    isolated.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
    isolated.setPermissionCheckHandler(() => false);
    isolated.on('will-download', event => { event.preventDefault(); errors.push('Download refused'); });
    isolated.webRequest.onBeforeRequest({ urls: ['<all_urls>'] }, (details, callback) => {
      const allow = allowedRequest(details.url, config.origin, local) || details.method === 'GET' && fonts.has(details.url);
      if (!allow) (probeMode ? probes : blocked).push({ url: details.url, blocked: true });
      else {
        pending.add(details.id); lastRequest = Date.now();
        if (details.url.startsWith(config.origin + '/')) {
          const route = new URL(details.url).pathname; routeCounts[route] = (routeCounts[route] || 0) + 1;
        }
      }
      callback({ cancel: !allow });
    });
    const complete = details => { pending.delete(details.id); lastRequest = Date.now(); };
    isolated.webRequest.onCompleted(complete); isolated.webRequest.onErrorOccurred(complete);
    w = new BrowserWindow({ show: false, width: 1440, height: 1000, useContentSize: true, webPreferences: {
      session: isolated, preload: path.join(config.root, 'app/dist-electron/preload.mjs'), sandbox: true,
      contextIsolation: true, nodeIntegration: false, backgroundThrottling: false,
      additionalArguments: ['--bellomberg-launch-id=documentation-demo', '--bellomberg-api-port=' + new URL(config.origin).port] } });
    w.webContents.setWindowOpenHandler(() => { errors.push('Window opening refused'); return { action: 'deny' }; });
    w.webContents.on('will-navigate', (event, url) => { if (!allowedRequest(url, config.origin, local)) { event.preventDefault(); errors.push('Navigation refused'); } });
    w.webContents.on('preload-error', () => errors.push('Preload error'));
    w.webContents.on('render-process-gone', () => errors.push('Renderer process gone'));
    const index = path.join(config.root, 'app/dist/index.html');
    await w.loadFile(index, { hash: '/trades' });
    for (const language of ['it', 'en']) for (const scene of config.scenes) {
      const countsBefore = { ...routeCounts };
      await js(language => {
        localStorage.clear(); sessionStorage.clear();
        localStorage.setItem('bellomberg_token_v1', 'synthetic-documentation-token');
        localStorage.setItem('bellomberg_unlocked_v1', JSON.stringify({ ts: Date.now() }));
        localStorage.setItem('bellomberg_last_launch_id', 'documentation-demo');
        localStorage.setItem('bellomberg.lingua', language);
      }, language);
      await w.loadFile(index, { hash: scene.route });
      await new Promise(resolve => { w.webContents.once('did-finish-load', resolve); w.webContents.reload(); });
      await js(watermark => {
        const mark = document.createElement('div'); mark.id = 'documentation-watermark'; mark.textContent = watermark;
        // This is the only injected visual element. Existing app UI remains intact.
        Object.assign(mark.style, { position: 'fixed', top: '7px', left: '50%', transform: 'translateX(-50%)',
          padding: '6px 14px', background: '#ffdf70', color: '#151515', border: '2px solid #151515',
          font: 'bold 14px sans-serif', zIndex: '2147483647', pointerEvents: 'none', whiteSpace: 'nowrap' });
        document.body.append(mark);
      }, WATERMARK);
      const until = Date.now() + 10000;
      let state;
      do {
        state = await js((selector, watermark) => {
          const mark = document.getElementById('documentation-watermark'); const box = mark?.getBoundingClientRect();
          return { route: location.hash.slice(1), language: document.documentElement.lang,
            ready: document.readyState === 'complete', fonts: document.fonts.status === 'loaded',
            images: [...document.images].every(i => i.complete && i.naturalWidth > 0),
            watermark: mark?.textContent === watermark && !!box && box.top >= 0 && box.bottom <= innerHeight
              && box.left >= 0 && box.right <= innerWidth && getComputedStyle(mark).visibility === 'visible',
            content: !!document.querySelector(selector), busy: !!document.querySelector('[aria-busy="true"]'),
            error: !!document.querySelector('[role="alert"]') };
        }, scene.selector, WATERMARK);
        if (readyState(state, { ...scene, language }) && pending.size === 0 && Date.now() - lastRequest > 700) break;
        await pause(60);
      } while (Date.now() < until);
      assert.ok(readyState(state, { ...scene, language }) && pending.size === 0, 'Readiness failed: ' + JSON.stringify({ scene: scene.id, state, pending: pending.size }));
      if (scene.fields) for (const [id, value] of Object.entries(scene.fields)) await js((id, value) => {
        const element = document.getElementById(id); if (!element) throw new Error('Missing documented form field: ' + id + '; synthetic UI: ' + document.body.innerText.slice(-2500));
        Object.getOwnPropertyDescriptor(element.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value').set.call(element, value);
        element.dispatchEvent(new Event(element.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }));
      }, id, value);
      // Wait for actual startup count-up/transition and paint, without altering UI CSS or data.
      await pause(900); await js(async () => {
        // Load both declared app families even when this scene uses only mono.
        // This preloads actual pinned fonts; it changes no element's styling.
        await Promise.all([document.fonts.load('400 12px Inter'), document.fonts.load('400 12px "JetBrains Mono"')]);
        await document.fonts.ready;
        await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
      });
      const loadedFamilies = await js(() => [...document.fonts].filter(face => face.status === 'loaded').map(face => face.family.replace(/["']/g, '')));
      for (const family of ['Inter', 'JetBrains Mono']) assert.ok(loadedFamilies.includes(family), 'Actual font not loaded: ' + family + '; loaded=' + JSON.stringify(loadedFamilies) + '; served=' + JSON.stringify(servedFonts));
      assert.deepEqual(blocked, []); assert.deepEqual(errors, []);
      for (const route of scene.required) assert.ok((routeCounts[route] || 0) > (countsBefore[route] || 0), 'Required scene request not observed: ' + route);
      assert.equal(await js(() => window.bellomberg.apiUrl), config.origin);
      const prefs = w.webContents.getLastWebPreferences(); assert.ok(prefs.sandbox && prefs.contextIsolation && !prefs.nodeIntegration);
      const dom = await js(() => document.body.innerText + '\n\nDOCUMENTATION FORM VALUES\n' + [...document.querySelectorAll('input,select,textarea')]
        .filter(el => el.getClientRects().length).map(el => el.id + ': ' + el.value).join('\n'));
      assert.ok(dom.includes(WATERMARK));
      if (config.candidate) {
        const png = (await w.capturePage(undefined, { stayHidden: true })).toPNG();
        const stem = 'docs/assets/screenshots/' + scene.id + '-' + language;
        fs.mkdirSync(path.dirname(path.join(config.temporary, stem)), { recursive: true });
        fs.writeFileSync(path.join(config.temporary, stem + '.png'), png);
        fs.writeFileSync(path.join(config.temporary, stem + '.dom.txt'), dom + '\n');
        images.push({ path: stem + '.png', sha256: sha(png), dom_path: stem + '.dom.txt', dom_sha256: sha(Buffer.from(dom + '\n')),
          width: png.readUInt32BE(16), height: png.readUInt32BE(20), language, route: scene.route });
      }
      scenes.push({ id: scene.id, language, route: scene.route, ready: true, watermark_visible: state.watermark, dom_sha256: sha(dom), pending_requests: pending.size,
        requests: Object.fromEntries(Object.entries(routeCounts).map(([route, n]) => [route, n - (countsBefore[route] || 0)]).filter(([, n]) => n > 0)) });
    }
    // Exercise the real session gate after captures. These probes must be cancelled
    // before contact; no request is made to a production port or external host.
    probeMode = true;
    for (const url of ['http://127.0.0.1:8765/__documentation_tripwire', 'http://127.0.0.1:5173/__documentation_tripwire', 'https://example.invalid/__documentation_tripwire']) {
      try { await isolated.fetch(url); } catch {}
      assert.ok(probes.some(p => p.url === url && p.blocked), 'Network gate probe not measured');
    }
    assert.ok(servedFonts.some(font => font.url.startsWith('https://fonts.gstatic.com/')), 'Pinned font serving not observed');
    finish({ ok: true, scenes, images, blocked, font_cache_served: servedFonts, network_guard_probes: probes });
  } catch (error) { finish({ ok: false, error: String(error), scenes, blocked, errors }); }
}

module.exports = { allowedRequest, validateOrigin, childEnvironment, resolvePython, sourceDigest, readyState, loadFixture, readInventory, loadFonts, prepareFonts,
  fixtureResponse, assertUnchanged, fingerprint, run };
if (process.versions.electron && process.type === 'browser' && process.argv[2] === '--documentation-renderer') {
  renderer(JSON.parse(process.argv[3])).catch(error => {
    console.log(RESULT + JSON.stringify({ ok: false, error: String(error) })); require('electron').app.exit(1);
  });
} else if (require.main === module) {
  if (process.argv[2] === '--documentation-renderer') { console.error('Renderer entry requires Electron'); process.exitCode = 2; }
  else if (process.argv[2] === '--prepare-fonts' && process.argv.length === 3) {
    prepareFonts(loadFixture(ROOT)).then(directory => console.log(JSON.stringify({ font_cache: directory, network: 'Explicit pinned public font downloads; capture itself uses the cache' })))
      .catch(error => { console.error(String(error)); process.exitCode = 1; });
  } else if (!process.argv[2] || ['--check', '--candidate'].includes(process.argv[2])) {
    const options = { candidate: process.argv[2] === '--candidate' };
    for (let i = 3; i < process.argv.length; i += 2) {
      if (!['--python', '--font-cache'].includes(process.argv[i]) || !process.argv[i + 1]) throw new Error('Invalid capture arguments');
      options[process.argv[i] === '--python' ? 'python' : 'fontCache'] = process.argv[i + 1];
    }
    run(options).then(r => console.log(JSON.stringify({ ok: r.ok,
      temporary: r.temporary, scenes: r.scenes.length, images_saved: r.images_saved, approval: r.approval })))
      .catch(error => { console.error(String(error)); process.exitCode = 1; });
  } else { console.error('Usage: node app/tools/capture-docs.cjs --prepare-fonts OR [--check|--candidate] --python /absolute/python --font-cache /absolute/cache'); process.exitCode = 2; }
}
