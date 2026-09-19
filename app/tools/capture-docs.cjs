// Public documentation capture: the actual built renderer, an invented fixture,
// and one declared watermark. No application main, Python backend or providers.
// --check (default) builds and checks every fixture scene in each fixture language (Italian and
// English unless the fixture declares "languages"), without saving PNGs. Fixture v2: response keys
// carry method and exact sorted query, scenes carry ordered steps and an optional viewport.
// --candidate writes review candidates ONLY to a newly created OS TEMP directory, and refuses
// to start while a capture input differs from HEAD (every receipt states the tree).
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
const NAVIGATION = 'app/src/lib/navigation.ts';
const WATERMARK = 'DEMO · SYNTHETIC DATA';
const LANGUAGES = Object.freeze(['it', 'en']);
const DEFAULT_VIEWPORT = Object.freeze({ width: 1440, height: 1000 });
const MAX_STEPS = 16;
// After every step the page must stay unchanged (no request, busy flag or chart activity) this long.
const STEP_QUIET_MS = 500;
const STEP_TIMEOUT_MS = 15000;
const MAX_BODY_BYTES = 1024 * 1024;
// Frame-freshness sentinels: near-identical watermark yellows (the declared one is #ffdf70),
// green d0-ef by blue 68-77 except the declared colour itself (index 0xf8: green 0xdf, blue 0x70).
// One colour per capture attempt of a run, never reused. 13/09: 256 colours allowed 25 scenes in one
// language, fewer than the handbook scenes being prepared.
const SENTINEL_LIMIT = 511;
const CAPTURE_ATTEMPTS = 10;
function sentinelColour(n) {
  assert.ok(Number.isInteger(n) && n >= 0 && n < SENTINEL_LIMIT, 'Freshness sentinel colours exhausted');
  const index = n >= 0xf8 ? n + 1 : n;
  return '#ff' + (0xd0 + (index >> 4)).toString(16) + (0x68 + (index & 15)).toString(16);
}
let sentinelsUsed = 0;
const nextSentinel = () => sentinelColour(sentinelsUsed++);
// Declared subprocess budgets of one run, in ms (tree: three git reads).
const BUDGET_MS = Object.freeze({ tree: 60000, tsc: 180000, vite: 180000, renderer: 420000, verifier: 15000 });
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
// Scene selectors (steps and the final readiness selector): compounds of a tag, #id, .class,
// [name] or [name="value"], joined only by single spaces (descendants). No child/sibling
// combinators, lists, universal selector or pseudo-classes; names are lower case.
// The selector does not make a click safe ('.primary' can be a save button). Write safety comes
// from the synthetic service: it stores nothing, answers only declared responses (a write only when
// its method and path are declared) and preflights for them, and any other request gets 409, which
// fails the capture as an unexpected request.
const SELECTOR_NAME = '[a-z][a-z0-9_-]*';
const SELECTOR_PART = '(?:#' + SELECTOR_NAME + '|\\.' + SELECTOR_NAME + '|\\[' + SELECTOR_NAME + '(?:="[^"\\\\\\[\\]\\n]*")?\\])';
const SELECTOR_COMPOUND = '(?:[a-z][a-z0-9]*' + SELECTOR_PART + '*|' + SELECTOR_PART + '+)';
const SCENE_SELECTOR = new RegExp('^' + SELECTOR_COMPOUND + '(?: ' + SELECTOR_COMPOUND + ')*$');
const validSelector = value => typeof value === 'string' && value.length <= 200 && SCENE_SELECTOR.test(value);
const STEP_KINDS = Object.freeze(['click', 'fill', 'select', 'wait', 'scroll']);
const SCENE_FIELDS = Object.freeze(['id', 'route', 'selector', 'required', 'steps', 'clicks', 'fields', 'viewport', 'note']);
const FIELD_ID = /^[A-Za-z][A-Za-z0-9_-]*$/;
// The legacy form ("clicks", at most three, then "fields") becomes ordered steps; "field" is the
// legacy value step that accepts inputs and selects alike and cannot be written in "steps".
function sceneSteps(scene) {
  if (scene.steps !== undefined) {
    assert.ok(scene.clicks === undefined && scene.fields === undefined, 'Scene mixes steps with legacy clicks or fields: ' + scene.id);
    assert.ok(Array.isArray(scene.steps), 'Invalid documentation steps: ' + scene.id);
    return scene.steps;
  }
  const clicks = scene.clicks ?? [];
  assert.ok(Array.isArray(clicks) && clicks.length <= 3, 'Unsafe documentation click: ' + scene.id);
  return [...clicks.map(click => ({ click })), ...(scene.fields === undefined ? [] : [{ field: scene.fields }])];
}
function validateStep(step, sceneId, legacy) {
  const kinds = step !== null && typeof step === 'object' && !Array.isArray(step) ? Object.keys(step) : [];
  assert.ok(kinds.length === 1 && (STEP_KINDS.includes(kinds[0]) || legacy && kinds[0] === 'field'), 'Invalid documentation step: ' + JSON.stringify({ scene: sceneId, step }));
  const [kind] = kinds, value = step[kind];
  if (['click', 'wait', 'scroll'].includes(kind)) {
    assert.ok(validSelector(value), 'Unsafe documentation ' + kind + ' selector: ' + JSON.stringify({ scene: sceneId, selector: value }));
  } else {
    assert.ok(value !== null && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length > 0
      && Object.entries(value).every(([id, text]) => FIELD_ID.test(id) && typeof text === 'string' && text.length <= 500),
    'Invalid documentation ' + kind + ' step: ' + JSON.stringify({ scene: sceneId, value }));
  }
  return step;
}
// Response keys: "<METHOD> <pathname>" or "<METHOD> <pathname>?<query>", where the query is the one
// the app sends with parameters sorted by name and encoded as URLSearchParams; "/path" is "GET /path".
const RESPONSE_KEY = /^(GET|POST|PUT|DELETE) (\/[^\s?#]*)(?:\?([^\s#]*))?$/;
function canonicalQuery(search) {
  const params = new URLSearchParams(search); params.sort();
  return params.toString();
}
function responseKey(key) {
  const full = typeof key === 'string' && key.startsWith('/') ? 'GET ' + key : key;
  const match = typeof full === 'string' ? RESPONSE_KEY.exec(full) : null;
  assert.ok(match && new URL(match[2], 'http://127.0.0.1').pathname === match[2] && match[3] !== '', 'Invalid synthetic response key: ' + JSON.stringify(key));
  if (match[3] !== undefined) assert.equal(canonicalQuery(match[3]), match[3], 'Synthetic response query must be sorted by name and URLSearchParams-encoded: ' + JSON.stringify(key));
  return full;
}
function requestKey(method, target) {
  const url = new URL(target, 'http://127.0.0.1'), query = canonicalQuery(url.search);
  return method + ' ' + url.pathname + (query ? '?' + query : '');
}
const responseIndexes = new WeakMap();
function responseIndex(fixture) {
  assert.ok(fixture.responses !== null && typeof fixture.responses === 'object' && !Array.isArray(fixture.responses), 'Synthetic responses required');
  if (!responseIndexes.has(fixture.responses)) {
    const index = new Map();
    for (const [key, body] of Object.entries(fixture.responses)) {
      const full = responseKey(key);
      assert.ok(!index.has(full), 'Duplicate synthetic response key: ' + full);
      assert.notEqual(full.split('?')[0], 'GET /preferences', 'GET /preferences is answered from the capture language; do not declare it');
      index.set(full, body);
    }
    responseIndexes.set(fixture.responses, index);
  }
  return responseIndexes.get(fixture.responses);
}
// Method, path and query first, then method and path; GET /preferences always follows the language.
function matchResponse(fixture, method, target) {
  const url = new URL(target, 'http://127.0.0.1'), index = responseIndex(fixture);
  if (method === 'GET' && url.pathname === '/preferences') return 'GET /preferences';
  const exact = requestKey(method, target), bare = method + ' ' + url.pathname;
  return index.has(exact) ? exact : index.has(bare) ? bare : null;
}
const responseBody = (fixture, key) => responseIndex(fixture).get(responseKey(key));
function fixtureLanguages(fixture) {
  if (fixture.languages === undefined) return [...LANGUAGES];
  assert.ok(Array.isArray(fixture.languages) && fixture.languages.length > 0 && new Set(fixture.languages).size === fixture.languages.length
    && fixture.languages.every(language => LANGUAGES.includes(language)), 'Invalid fixture languages: ' + JSON.stringify(fixture.languages));
  return [...fixture.languages];
}
function fixtureClock(fixture) {
  if (fixture.clock === undefined) return null;
  const clock = fixture.clock;
  assert.ok(clock && typeof clock === 'object' && !Array.isArray(clock)
    && Object.keys(clock).sort().join() === 'now,timezone'
    && typeof clock.now === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(clock.now)
    && Number.isFinite(Date.parse(clock.now)) && new Date(clock.now).toISOString() === clock.now,
  'Invalid documentation clock: use a canonical UTC ISO instant');
  assert.ok(typeof clock.timezone === 'string' && clock.timezone.length > 0, 'Invalid documentation timezone');
  try { new Intl.DateTimeFormat('en', { timeZone: clock.timezone }).format(0); }
  catch { throw new Error('Invalid documentation timezone'); }
  return { now: clock.now, timezone: clock.timezone };
}
// Evaluated in the main world before each document's scripts. Performance clocks,
// timers and the Electron runner's Date remain real, so readiness budgets still work.
function pageFixedClock(epoch) {
  const NativeDate = globalThis.Date;
  function DocumentationDate(...args) {
    if (!new.target) return new NativeDate(epoch).toString();
    return Reflect.construct(NativeDate, args.length ? args : [epoch],
      new.target === DocumentationDate ? NativeDate : new.target);
  }
  Object.setPrototypeOf(DocumentationDate, NativeDate);
  DocumentationDate.prototype = NativeDate.prototype;
  Object.defineProperty(DocumentationDate, 'now', { value: () => epoch });
  globalThis.Date = DocumentationDate;
}
async function installRendererClock(contents, clock) {
  if (!clock) return;
  const checked = fixtureClock({ clock });
  // CDP emulation waits for a renderer; an un-navigated hidden window has none.
  // This empty local document starts it before installing the per-document hook.
  await contents.loadURL('about:blank');
  contents.debugger.attach('1.3');
  await contents.debugger.sendCommand('Emulation.setTimezoneOverride', { timezoneId: checked.timezone });
  await contents.debugger.sendCommand('Page.enable');
  await contents.debugger.sendCommand('Page.addScriptToEvaluateOnNewDocument', {
    source: '(' + pageFixedClock.toString() + ')(' + Date.parse(checked.now) + ');',
  });
}
// Every capture of a run, in order: each language, then each scene.
const captureMatrix = fixture => fixtureLanguages(fixture).flatMap(language => fixture.scenes.map(scene => ({ language, scene })));
// Page routes a scene may open: the destinations registered in app/src/lib/navigation.ts, read from
// the source the way docs/guide/verify_docs.py reads it. A row that does not parse stops the capture.
function navigationDestinations(root) {
  const source = fs.readFileSync(path.join(root, NAVIGATION), 'utf8');
  const block = /const destinations = \[\r?\n([\s\S]*?)\r?\n\];/.exec(source);
  assert.ok(block, 'Navigation registry not found: ' + NAVIGATION);
  const rows = block[1].split(/\r?\n/).filter(line => line.trim() && !line.trim().startsWith('//'));
  const destinations = rows.map(line => /^\s*\['[^']+',\s*'(\/[^']*)',/.exec(line)?.[1]);
  assert.ok(destinations.length > 0 && destinations.every(Boolean), 'Unreadable navigation registry row in ' + NAVIGATION);
  return destinations;
}
function validateScene(fixture, scene, destinations) {
  assert.ok(Array.isArray(destinations) && destinations.length > 0, 'Navigation destinations required');
  assert.ok(scene !== null && typeof scene === 'object' && Object.keys(scene).every(key => SCENE_FIELDS.includes(key)), 'Unknown documentation scene field: ' + JSON.stringify(scene && scene.id));
  assert.ok(/^[a-z][a-z0-9-]*$/.test(scene.id) && validSelector(scene.selector) && Array.isArray(scene.required), 'Invalid documentation scene: ' + JSON.stringify(scene.id));
  assert.ok(destinations.includes(scene.route), 'Scene route is not a navigation destination: ' + JSON.stringify({ scene: scene.id, route: scene.route }));
  assert.ok(scene.note === undefined || typeof scene.note === 'string' && scene.note.trim() && scene.note.length <= 1000, 'Invalid documentation scene note: ' + scene.id);
  const index = responseIndex(fixture), required = scene.required.map(responseKey);
  assert.ok(required.every(key => key === 'GET /preferences' || index.has(key)), 'Required route without synthetic response: ' + scene.id);
  assert.equal(new Set(required).size, required.length, 'Duplicate required response: ' + scene.id);
  const steps = sceneSteps(scene), legacy = scene.steps === undefined;
  assert.ok(steps.length <= MAX_STEPS, 'Too many documentation steps: ' + scene.id);
  for (const step of steps) validateStep(step, scene.id, legacy);
  if (scene.viewport !== undefined) {
    const v = scene.viewport;
    assert.ok(v !== null && typeof v === 'object' && Object.keys(v).sort().join() === 'height,width'
      && Number.isInteger(v.width) && v.width >= 800 && v.width <= 2560 && Number.isInteger(v.height) && v.height >= 600 && v.height <= 1600,
    'Invalid documentation viewport: ' + scene.id);
  }
  return scene;
}
// NativeImage.toBitmap() is BGRA; the probe is in CSS pixels, mapped by the bitmap/CSS width ratio.
function frameShowsSentinel(bitmap, width, probe, colour) {
  const ratio = width / probe.width, at = (Math.round(probe.y * ratio) * width + Math.round(probe.x * ratio)) * 4;
  return at >= 0 && at + 3 < bitmap.length
    && [bitmap[at + 2], bitmap[at + 1], bitmap[at]].join() === [1, 3, 5].map(i => parseInt(colour.slice(i, i + 2), 16)).join();
}
function nativeFrameShowsSentinel(frame, probe, colour) {
  const factor = frame.getScaleFactors()[0] ?? 1;
  return frameShowsSentinel(frame.toBitmap({ scaleFactor: factor }), frame.getSize(factor).width, probe, colour);
}
// A hidden window can return an older frame than the DOM measured (13/09: loading-state PNGs for
// trade-entry-en and agent-progress-it with a ready DOM). Before each attempt paint(colour) gives the
// watermark a sentinel never used before in this run, so neither an earlier attempt's frame nor one
// left from the previous scene can match; a frame without that exact pixel is refused and repainted.
async function captureFreshFrame({ paint, repaint, capture, shows, limit = CAPTURE_ATTEMPTS, context = {} }) {
  let image = null, attempts = 0;
  const sentinels = [];
  while (!image && attempts < limit) {
    const colour = nextSentinel(); sentinels.push(colour);
    const probe = await paint(colour);
    if (attempts++) await repaint();
    const frame = await capture();
    if (shows(frame, probe, colour)) image = frame;
  }
  if (!image) throw new Error('Stale frame: no capture showed the freshness sentinel: ' + JSON.stringify({ ...context, attempts, sentinels }));
  return { image, attempts, sentinels };
}
// The renderer's only capture path: the real pixel check decides, and the PNG saved is the frame it
// accepted. A second capturePage or toPNG elsewhere in this file would bypass it (source guard in
// app/tests/desktop/capture-docs.cjs).
async function sceneFrame({ w, js, context, wait = pause }) {
  const result = await captureFreshFrame({ shows: nativeFrameShowsSentinel, context,
    paint: colour => js(async colour => {
      const mark = document.getElementById('documentation-watermark'); mark.style.background = colour;
      await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
      const box = mark.getBoundingClientRect();
      return { x: box.left + 6, y: box.top + 4, width: innerWidth };
    }, colour),
    repaint: async () => { w.webContents.invalidate(); await wait(200); },
    capture: () => w.capturePage(undefined, { stayHidden: true }) });
  return { ...result, png: result.image.toPNG() };
}
// Response keys matched during one scene (requests that matched nothing appear as their own request
// key) that are neither app-shell reads nor declared responses of that scene.
function sceneRoutesOutside(shell, scene, keys) {
  const allowed = new Set([...shell, ...scene.required].map(responseKey));
  return keys.filter(key => !allowed.has(key)).sort();
}
function parseFixtureJson(text) {
  const value = JSON.parse(text, (key, item) => {
    assert.ok(typeof item !== 'number' || Number.isFinite(item), 'Non-finite fixture number'); return item;
  });
  // JSON.parse validates grammar but discards duplicate keys. Check them before use,
  // including escaped spellings, without interpreting any reference as a file path.
  const tokens = text.match(/"(?:\\.|[^"\\])*"|[{}\[\],:]|[^{}\[\],:\s]+/g) || [];
  let index = 0;
  function scan() {
    const token = tokens[index++];
    if (token === '{') {
      const keys = new Set();
      while (tokens[index] !== '}') {
        const key = JSON.parse(tokens[index++]);
        assert.ok(!keys.has(key), 'Duplicate fixture JSON key'); keys.add(key); index++; scan();
        if (tokens[index] === ',') index++;
      }
      index++;
    } else if (token === '[') {
      while (tokens[index] !== ']') { scan(); if (tokens[index] === ',') index++; }
      index++;
    }
  }
  scan(); return value;
}
function decodeFixture(encoded) {
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const keys = (value, expected) => object(value) && Object.keys(value).length === expected.length && expected.every(k => Object.hasOwn(value, k));
  assert.ok(object(encoded), 'Fixture object required');
  let definitions = {}, document = encoded;
  if (Object.hasOwn(encoded, 'schema')) {
    assert.ok(keys(encoded, ['schema', 'definitions', 'document']) && encoded.schema === 'documentation-local-refs/1', 'Unsupported fixture encoding');
    definitions = encoded.definitions; document = encoded.document;
    assert.ok(object(definitions) && Object.keys(definitions).every(k => /^[a-f0-9]{12}$/.test(k)), 'Invalid local definitions');
  }
  const active = new Set(), cache = new Map();
  function reference(key) {
    assert.ok(typeof key === 'string' && Object.hasOwn(definitions, key) && !active.has(key), 'Missing or cyclic local reference');
    if (!cache.has(key)) { active.add(key); cache.set(key, walk(definitions[key])); active.delete(key); }
    return structuredClone(cache.get(key));
  }
  function walk(node) {
    if (Array.isArray(node)) return node.map(walk);
    if (!object(node)) return node;
    if (Object.hasOwn(node, '$demo_ref')) {
      assert.ok(keys(node, ['$demo_ref']), 'Ambiguous local reference'); return reference(node.$demo_ref);
    }
    if (Object.hasOwn(node, '$demo_table')) {
      const table = node.$demo_table;
      assert.ok(keys(node, ['$demo_table']) && keys(table, ['columns', 'rows']), 'Invalid table marker');
      const { columns, rows } = table;
      assert.ok(Array.isArray(columns) && columns.length && columns.every(k => typeof k === 'string' && !k.startsWith('$demo_'))
        && new Set(columns).size === columns.length && Array.isArray(rows)
        && rows.every(row => Array.isArray(row) && row.length === columns.length), 'Ambiguous table columns or row width');
      return rows.map(row => Object.fromEntries(columns.map((key, i) => [key, walk(row[i])])));
    }
    assert.ok(!Object.keys(node).some(k => k.startsWith('$demo_')), 'Unknown fixture marker');
    return Object.fromEntries(Object.entries(node).map(([key, value]) => [key, walk(value)]));
  }
  for (const key of Object.keys(definitions)) reference(key);
  return walk(document);
}
function loadFixture(root) {
  const f = decodeFixture(parseFixtureJson(fs.readFileSync(path.join(root, FIXTURE), 'utf8')));
  assert.equal(f.id, 'documentation-demo');
  assert.equal(f.version, 1, 'Fixture version must stay 1: docs/guide/verify_screenshots.py accepts only fixture version 1 in the manifest');
  fixtureClock(f);
  const languages = fixtureLanguages(f), destinations = navigationDestinations(root), index = responseIndex(f);
  assert.ok(Array.isArray(f.scenes) && f.scenes.length > 0, 'Documentation scenes required');
  assert.equal(new Set(f.scenes.map(s => s.id)).size, f.scenes.length, 'Duplicate documentation scene id');
  assert.ok(Array.isArray(f.shell) && f.shell.map(responseKey).every(key => key === 'GET /preferences' || key.startsWith('GET ') && index.has(key)), 'Shell route without synthetic response');
  for (const scene of f.scenes) validateScene(f, scene, destinations);
  assert.ok(f.scenes.length * languages.length * CAPTURE_ATTEMPTS <= SENTINEL_LIMIT, 'Too many scene captures for unused freshness sentinels');
  const portfolio = index.get('GET /portfolio');
  assert.ok(!portfolio || portfolio.positions.every(p => p.ticker.startsWith('DEMO.')), 'Synthetic portfolio positions must use DEMO tickers');
  return f;
}
// Project only the backend's explicitly authored variants, as its API boundary does.
// The decoded snapshot and archived/source prose remain untouched.
function presentationResponse(payload, language) {
  assert.ok(language === 'it' || language === 'en', 'Unsupported documentation language');
  const copy = structuredClone(payload);
  function visit(node) {
    if (node === null || typeof node !== 'object') return;
    if (Object.hasOwn(node, '_presentation_v1')) {
      const meta = node._presentation_v1, seen = new Set();
      assert.ok(meta && meta.version === 1 && Array.isArray(meta.texts), 'Invalid presentation metadata');
      for (const item of meta.texts) {
        assert.ok(item && Array.isArray(item.path) && item.path.length && typeof item.it === 'string'
          && typeof item.en === 'string', 'Missing presentation variant or path');
        const identity = JSON.stringify(item.path);
        assert.ok(!seen.has(identity), 'Duplicate presentation path'); seen.add(identity);
        let parent = node;
        for (const [index, key] of item.path.entries()) {
          assert.ok((typeof key === 'string' || Number.isInteger(key) && key >= 0)
            && !['_presentation_v1', '__proto__', 'prototype', 'constructor'].includes(key)
            && parent !== null && typeof parent === 'object' && Object.hasOwn(parent, key), 'Invalid presentation path');
          if (index === item.path.length - 1) {
            assert.ok(typeof parent[key] === 'string' && [item.it, item.en].includes(parent[key]), 'Presentation text drift');
            parent[key] = item[language];
          } else parent = parent[key];
        }
      }
    }
    for (const [key, value] of Object.entries(node)) if (key !== '_presentation_v1') visit(value);
  }
  visit(copy);
  return copy;
}
// OPTIONS answers only the preflight of a declared method and path (Access-Control-Request-Method,
// GET when absent). The synthetic service stores nothing.
function fixtureResponse(fixture, method, target, language, preflightMethod) {
  const refused = { status: 409, body: { code: 'SYNTHETIC_ENDPOINT_REFUSED', detail: 'Unlisted documentation request' }, key: null };
  if (language !== 'it' && language !== 'en') return { status: 400, body: { code: 'unsupported_language', detail: "Unsupported language: expected 'it' or 'en'" }, key: null };
  if (method === 'OPTIONS') {
    const requested = String(preflightMethod || 'GET').toUpperCase();
    const key = ['GET', 'POST', 'PUT', 'DELETE'].includes(requested) ? matchResponse(fixture, requested, target) : null;
    return key ? { status: 200, body: {}, key, preflight: requested } : refused;
  }
  const key = ['GET', 'POST', 'PUT', 'DELETE'].includes(method) ? matchResponse(fixture, method, target) : null;
  if (key === 'GET /preferences') return { status: 200, body: { language, selected: true, source: 'preferences' }, key };
  if (!key) return refused;
  try { return { status: 200, body: presentationResponse(responseIndex(fixture).get(key), language), key }; }
  catch { return { status: 500, body: { code: 'SYNTHETIC_PRESENTATION_INVALID', detail: 'Invalid authored presentation: documentation response refused' }, key }; }
}
function receivedBody(bytes, contentType) {
  const receipt = { bytes: bytes.length, sha256: sha(bytes) };
  let text;
  try { text = new TextDecoder('utf-8', { fatal: true }).decode(bytes); } catch { return receipt; }
  if (/json/i.test(String(contentType))) { try { return { ...receipt, json: JSON.parse(text) }; } catch {} }
  return { ...receipt, text };
}
// The isolated local API of a capture run. Every request is recorded with its matched response key
// and, when it carries one, its body; anything but 200 is also recorded as unexpected.
function syntheticService(fixture) {
  const requests = [], unexpected = [];
  const server = http.createServer((req, res) => {
    const chunks = [];
    let size = 0;
    req.on('data', chunk => { size += chunk.length; if (size <= MAX_BODY_BYTES) chunks.push(chunk); });
    req.on('end', () => {
      const url = new URL(req.url, 'http://127.0.0.1');
      const language = req.headers['x-bb-language'] ?? 'it';
      const result = size > MAX_BODY_BYTES
        ? { status: 413, body: { code: 'SYNTHETIC_BODY_REFUSED', detail: 'Documentation request body too large' }, key: null }
        : fixtureResponse(fixture, req.method, req.url, language, req.headers['access-control-request-method']);
      const query = canonicalQuery(url.search);
      const item = { method: req.method, route: url.pathname, query, key: result.key, language, status: result.status };
      if (req.method === 'OPTIONS') item.preflight = String(req.headers['access-control-request-method'] || '');
      if (size) item.body = size > MAX_BODY_BYTES ? { bytes: size, refused: true } : receivedBody(Buffer.concat(chunks), req.headers['content-type']);
      requests.push(item); if (result.status !== 200) unexpected.push(item);
      res.writeHead(result.status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store',
        'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'Content-Type,X-BB-Token,X-BB-Language',
        'Access-Control-Allow-Methods': (result.preflight || 'GET') + ',OPTIONS' });
      res.end(JSON.stringify(result.body));
    });
  });
  return { server, requests, unexpected };
}
function readyState(state, scene) {
  return state.route === scene.route && state.language === scene.language && state.ready && state.fonts
    && state.images && state.watermark && state.content && !state.busy && !state.error && state.charts === true
    && (!scene.viewport || state.viewport?.width === scene.viewport.width && state.viewport?.height === scene.viewport.height);
}
// ---- Page functions: serialized into the renderer page by pageScript, so they must not use closures.
// Wraps Plotly.newPlot/react (also when Plotly loads later) to count calls and settled promises,
// and counts plotly_afterplot of every plotted element. The real pinned renderer still draws.
function pageObservePlots() {
  if (window.__bbDocsPlots) return window.__bbDocsPlots.preloaded;
  const state = window.__bbDocsPlots = { installed: true, preloaded: !!window.Plotly, calls: 0, settled: 0, afterplot: 0, errors: [] };
  const wrap = plotly => {
    if (!plotly || typeof plotly !== 'object' && typeof plotly !== 'function' || plotly.__bbDocsObserved) return plotly;
    for (const name of ['newPlot', 'react']) {
      const original = plotly[name];
      if (typeof original !== 'function') continue;
      plotly[name] = function (gd, ...rest) {
        state.calls++;
        const done = error => { state.settled++; if (error) state.errors.push(String(error)); };
        let result;
        try { result = original.call(this, gd, ...rest); } catch (error) { done(error); throw error; }
        return Promise.resolve(result).then(value => {
          done();
          const element = typeof gd === 'string' ? document.getElementById(gd) : gd;
          if (element && typeof element.on === 'function' && !element.__bbDocsAfterplot) {
            element.__bbDocsAfterplot = true; element.on('plotly_afterplot', () => { state.afterplot++; });
          }
          return value;
        }, error => { done(error); throw error; });
      };
    }
    Object.defineProperty(plotly, '__bbDocsObserved', { value: true });
    return plotly;
  };
  let current = wrap(window.Plotly);
  Object.defineProperty(window, 'Plotly', { configurable: true, enumerable: true, get: () => current, set: value => { current = wrap(value); } });
  return state.preloaded;
}
// Readiness of the current page. charts: every newPlot/react promise settled without error, and every
// visible Plotly element is plotted, shows no "no WebGL" panel and keeps its WebGL contexts.
function pageState(selector, watermark) {
  const mark = document.getElementById('documentation-watermark'), box = mark?.getBoundingClientRect();
  const plots = window.__bbDocsPlots || { installed: false, preloaded: false, calls: 0, settled: 0, afterplot: 0, errors: [] };
  const shown = element => element.getClientRects().length > 0 && element.getBoundingClientRect().width > 0 && element.getBoundingClientRect().height > 0;
  const graphs = [...document.querySelectorAll('.js-plotly-plot')].filter(shown);
  const idle = plots.calls === plots.settled;
  let noWebgl = 0, lost = 0, canvases = 0, unplotted = 0;
  for (const gd of graphs) {
    if (gd.querySelector('.no-webgl')) { noWebgl++; continue; }
    if (!gd._fullLayout) unplotted++;
    // Only after Plotly settled: getContext on a canvas without a context would create one.
    if (idle) for (const canvas of gd.querySelectorAll('.gl-container canvas')) {
      canvases++;
      const context = canvas.getContext('webgl') || canvas.getContext('webgl2');
      if (!context || context.isContextLost()) lost++;
    }
  }
  const busy = !!document.querySelector('[aria-busy="true"]');
  const charts = plots.installed && idle && plots.errors.length === 0 && noWebgl === 0 && lost === 0 && unplotted === 0 && !(plots.preloaded && graphs.length);
  return { route: location.hash.slice(1), language: document.documentElement.lang,
    ready: document.readyState === 'complete', fonts: document.fonts.status === 'loaded',
    images: [...document.images].every(i => i.complete && i.naturalWidth > 0),
    watermark: watermark == null || mark?.textContent === watermark && !!box && box.top >= 0 && box.bottom <= innerHeight
      && box.left >= 0 && box.right <= innerWidth && getComputedStyle(mark).visibility === 'visible',
    content: selector == null || !!document.querySelector(selector), busy, error: !!document.querySelector('[role="alert"]'),
    viewport: { width: innerWidth, height: innerHeight }, charts,
    plots: { calls: plots.calls, settled: plots.settled, afterplot: plots.afterplot, graphs: graphs.length, gl_canvases: canvases,
      no_webgl: noWebgl, lost_contexts: lost, unplotted, preloaded: plots.preloaded, errors: plots.errors.slice(0, 3) },
    signature: JSON.stringify([plots.calls, plots.settled, plots.afterplot, busy, graphs.length]) };
}
// One attempt at one step: { done: false, reason } while its target is missing, hidden, disabled or
// busy (or a select lacks the option); fill and select check every field before writing any value.
function pageStep(step) {
  const kind = Object.keys(step)[0], value = step[kind];
  if (kind === 'click' || kind === 'wait' || kind === 'scroll') {
    const element = document.querySelector(value);
    if (!element || element.getClientRects().length === 0) return { done: false, reason: 'missing or hidden: ' + value };
    if (kind === 'wait') return { done: true };
    if (kind === 'scroll') { element.scrollIntoView({ block: 'start', inline: 'nearest' }); return { done: true }; }
    if (element.disabled || element.closest('[aria-busy="true"]')) return { done: false, reason: 'disabled or busy: ' + value };
    if (typeof element.click === 'function') element.click();
    else element.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
    return { done: true };
  }
  const entries = Object.entries(value), elements = entries.map(([id]) => document.getElementById(id));
  for (const [i, [id, text]] of entries.entries()) {
    const element = elements[i];
    if (!element || element.disabled) return { done: false, reason: 'missing or disabled field: ' + id };
    if (!['INPUT', 'TEXTAREA', 'SELECT'].includes(element.tagName)) throw new Error('Not a form field: ' + id);
    const select = element.tagName === 'SELECT';
    if (kind === 'fill' && select || kind === 'select' && !select) throw new Error('Step ' + kind + ' does not match field ' + id + ' (' + element.tagName + ')');
    if (select && ![...element.options].some(option => option.value === text)) return { done: false, reason: 'option not available: ' + id + '=' + text };
  }
  const values = {};
  for (const [i, [id, text]] of entries.entries()) {
    const element = elements[i], select = element.tagName === 'SELECT';
    const prototype = select ? HTMLSelectElement.prototype : element.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, text);
    element.dispatchEvent(new Event(select ? 'change' : 'input', { bubbles: true }));
    values[id] = element.value;
  }
  return { done: true, values };
}
function pageScript(webContents) {
  return async (fn, ...args) => {
    const result = await webContents.executeJavaScript(`(async()=>{try{return {ok:true,value:await (${fn.toString()})(...${JSON.stringify(args)})}}catch(error){return {ok:false,error:String(error)}}})()`);
    if (!result.ok) throw new Error('Renderer script: ' + result.error);
    return result.value;
  };
}
// Waits until no request is pending, nothing is busy, charts are ready and none of these changed for
// quietMs. network: { pending(), lastRequest() } of the capture session. No animation-frame wait: 13/09,
// after the offline test page's first click the hidden window ran requestAnimationFrame only every
// 1.4-2 s until a capturePage (cause not isolated); paint freshness is checked by sceneFrame instead.
async function settleScene({ js, network, context = {}, quietMs = STEP_QUIET_MS, timeoutMs = STEP_TIMEOUT_MS, clock = Date.now, wait = pause }) {
  const until = clock() + timeoutMs;
  let state, signature, changedAt = clock();
  for (;;) {
    state = await js(pageState, null, null);
    if (state.signature !== signature) { signature = state.signature; changedAt = clock(); }
    const quiet = clock() - Math.max(network.lastRequest(), changedAt);
    if (network.pending() === 0 && !state.busy && state.charts && quiet >= quietMs) break;
    if (clock() >= until) throw new Error('Page did not settle: ' + JSON.stringify({ ...context, pending: network.pending(), busy: state.busy, charts: state.charts, plots: state.plots, quiet }));
    await wait(60);
  }
  return state;
}
// Steps in fixture order. The loaded page settles first (legacy "fields" were written only after the
// page's reads); then each step waits for its target, acts once, and the page settles before the next.
async function runSteps({ js, steps, network, context = {}, timeoutMs = STEP_TIMEOUT_MS, clock = Date.now, wait = pause, settle = settleScene }) {
  const executed = [];
  if (steps.length) await settle({ js, network, context: { ...context, index: -1, kind: 'loaded page' }, clock, wait });
  for (const [index, step] of steps.entries()) {
    const kind = Object.keys(step)[0], started = clock(), until = started + timeoutMs;
    let result = await js(pageStep, step);
    while (!result.done && clock() < until) { await wait(60); result = await js(pageStep, step); }
    assert.ok(result.done, 'Documentation step not ready: ' + JSON.stringify({ ...context, index, step, reason: result.reason }));
    await settle({ js, network, context: { ...context, index, kind }, clock, wait });
    executed.push({ index, kind, target: step[kind], ...(result.values ? { values: result.values } : {}), ms: clock() - started });
  }
  return executed;
}
// Electron settings shared by the capture renderer and its offline tests. Hardware acceleration stays
// off: 13/09 a hidden window drew a Plotly 3D surface through the software WebGL adapter.
function configureElectron(app, temporary) {
  for (const name of ['userData', 'sessionData', 'crashDumps', 'logs']) {
    const target = path.join(temporary, name); fs.mkdirSync(target, { recursive: true }); app.setPath(name, target);
  }
  app.disableHardwareAcceleration();
  app.commandLine.appendSwitch('disable-background-networking');
  app.commandLine.appendSwitch('disable-component-update');
  app.commandLine.appendSwitch('host-resolver-rules', 'MAP * ~NOTFOUND, EXCLUDE 127.0.0.1');
}
function hiddenWindowOptions({ session, preload, additionalArguments, viewport = DEFAULT_VIEWPORT } = {}) {
  const webPreferences = { sandbox: true, contextIsolation: true, nodeIntegration: false, backgroundThrottling: false };
  if (session) webPreferences.session = session;
  if (preload) webPreferences.preload = preload;
  if (additionalArguments) webPreferences.additionalArguments = additionalArguments;
  return { show: false, width: viewport.width, height: viewport.height, useContentSize: true, webPreferences };
}
async function command(binary, args, options, timeout) {
  assert.ok(Number.isInteger(timeout) && timeout > 0, 'Subprocess budget required');
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
  const result = spawnSync(python, ['-I', '-S', '-c', code, path.join(root, 'docs/guide/verify_screenshots.py'), root, temporary], { encoding: 'utf8', env, timeout: BUDGET_MS.verifier });
  assert.equal(result.status, 0, 'Public PNG/manifest verifier: ' + result.stderr);
  return Number(result.stdout.trim());
}
// Candidates must be reproducible from a commit. Capture inputs (the source inventory, fixture and
// this script) that differ from HEAD, staged or not, are listed; so is every inventoried file git does
// not track, ignored ones included, because the digest reads the disk, not the index.
function inventoryNames(root) {
  const singles = fs.readdirSync(path.join(root, 'app')).map(p => 'app/' + p).filter(sourcePath)
    .concat([FIXTURE, SCRIPT].filter(name => fs.existsSync(path.join(root, name))));
  return Object.keys(readInventory(root, ['app/src', 'app/electron', 'app/public', 'app/resources'], singles));
}
function treeState(root, env) {
  const git = args => {
    const result = spawnSync('git', ['--no-optional-locks', '-C', root, ...args], { encoding: 'utf8', env, windowsHide: true, timeout: BUDGET_MS.tree / 3 });
    assert.equal(result.status, 0, 'Git tree state unavailable: ' + (result.error ? String(result.error) : String(result.stderr).trim()));
    return result.stdout;
  };
  const inputs = stdout => stdout.split('\0').filter(name => name && (sourcePath(name) || name === FIXTURE || name === SCRIPT)).sort();
  const modified = inputs(git(['diff', '--name-only', '-z', 'HEAD', '--', 'app']));
  const tracked = new Set(git(['ls-files', '--full-name', '-z', '--', 'app']).split('\0').filter(Boolean));
  const untracked = inventoryNames(root).filter(name => !tracked.has(name)).sort();
  return { head: git(['rev-parse', 'HEAD']).trim(), modified, untracked, clean: modified.length === 0 && untracked.length === 0 };
}
function assertCleanTree(tree) {
  assert.ok(tree.clean, 'Candidates need a clean tree: commit or remove these capture inputs first, '
    + 'otherwise the manifest binds PNGs to sources no commit contains: ' + JSON.stringify({ modified: tree.modified, untracked: tree.untracked }));
}
async function run({ candidate = false, python: pythonPath = process.env.BB_DOCS_PYTHON,
  fontCache = process.env.BB_DOCS_FONT_CACHE, root: rootOption = ROOT } = {}) {
  const python = resolvePython(pythonPath);
  const root = fs.realpathSync(rootOption);
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'bellomberg-docs-'));
  const env = childEnvironment(temporary);
  // Measured before fixture, fonts or build: a candidate on uncommitted inputs stops here.
  const tree = treeState(root, env);
  if (candidate) assertCleanTree(tree);
  const fixture = loadFixture(root);
  const fonts = loadFonts(fixture, fontCache);
  const fontDirectory = path.join(temporary, 'pinned-fonts'); fs.mkdirSync(fontDirectory);
  for (const asset of fonts.values()) fs.writeFileSync(path.join(fontDirectory, asset.filename), asset.bytes, { flag: 'wx' });
  const beforeBuild = sourceDigest(sources(root));
  const log = [];
  log.push(await command(process.execPath, [path.join(root, 'app/node_modules/typescript/bin/tsc')], { cwd: path.join(root, 'app'), env }, BUDGET_MS.tsc));
  log.push(await command(process.execPath, [path.join(root, 'app/node_modules/vite/bin/vite.js'), 'build'], { cwd: path.join(root, 'app'), env }, BUDGET_MS.vite));
  assert.equal(sourceDigest(sources(root)), beforeBuild, 'Source drift during build');
  fs.writeFileSync(path.join(temporary, 'build.log'), log.join('\n'));
  const before = fingerprint(root);
  const { server, requests, unexpected } = syntheticService(fixture);
  let receipt;
  try {
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
    const origin = validateOrigin('http://127.0.0.1:' + server.address().port);
    const config = { root, temporary, origin, candidate, languages: fixtureLanguages(fixture), scenes: fixture.scenes, shell: fixture.shell,
      clock: fixtureClock(fixture), before, fontDirectory };
    const output = await command(require(path.join(root, 'app/node_modules/electron')), [__filename, '--documentation-renderer', JSON.stringify(config)], { cwd: path.join(root, 'app'), env }, BUDGET_MS.renderer);
    fs.writeFileSync(path.join(temporary, 'renderer.log'), output);
    const line = output.split(/\r?\n/).find(l => l.startsWith(RESULT));
    assert.ok(line, 'Renderer receipt missing');
    receipt = JSON.parse(line.slice(RESULT.length));
    assert.ok(receipt.ok); assert.equal(unexpected.length, 0, 'Unexpected synthetic endpoint requested');
    assertUnchanged(before, fingerprint(root));
    for (const scene of fixture.scenes) for (const key of scene.required.map(responseKey)) {
      assert.ok(requests.some(r => r.method !== 'OPTIONS' && r.key === key && r.status === 200), 'Missing measured response: ' + key);
    }
    if (candidate) {
      const manifest = { schema_version: 1, fixture: { id: fixture.id, version: fixture.version, path: FIXTURE, sha256: before.fixture },
        capture: { path: SCRIPT, sha256: before.capture }, app_version: before.app_version,
        build: { source_sha256: before.source, bundle_sha256: before.bundle }, images: receipt.images };
      fs.writeFileSync(path.join(temporary, MANIFEST), JSON.stringify(manifest, null, 2) + '\n');
      assert.equal(verifyCandidates(root, temporary, env, python), receipt.images.length);
      assertUnchanged(before, fingerprint(root));
      const after = treeState(root, env);
      assert.ok(after.clean && after.head === tree.head, 'Capture inputs changed during the candidate run: '
        + JSON.stringify({ head: [tree.head, after.head], modified: after.modified, untracked: after.untracked }));
    }
    receipt = { ...receipt, temporary, origin, tree, inputs: before, clock: fixtureClock(fixture), build_source_matched: true,
      bundle_recipe: 'Sorted [app/dist/** or app/dist-electron/preload.mjs, SHA256 exact bytes], compact ASCII JSON SHA256',
      requests, unexpected_requests: unexpected, images_saved: candidate ? receipt.images.length : 0,
      watermark_injected_by_capture: true, approval: 'NOT APPROVED: candidate pixels require visual review and private pins' };
    fs.writeFileSync(path.join(temporary, 'capture-receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
    return receipt;
  } catch (error) {
    const captureDir = path.join(temporary, 'docs/assets/screenshots');
    const failure = { ok: false, error: String(error), temporary, tree, inputs: before, requests,
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
  configureElectron(app, config.temporary);
  const blocked = [], probes = [], scenes = [], images = [], errors = [];
  const local = new Set(Object.keys(readInventory(config.root, ['app/dist']))
    .map(name => path.resolve(config.root, name)));
  const pending = new Set(), routeCounts = {};
  let lastRequest = Date.now(), w, js, probeMode = false;
  const network = { pending: () => pending.size, lastRequest: () => lastRequest };
  const finish = value => { console.log(RESULT + JSON.stringify(value)); app.exit(value.ok ? 0 : 1); };
  try {
    await app.whenReady();
    const isolated = session.fromPartition('documentation-' + crypto.randomUUID());
    const fixture = loadFixture(config.root);
    assert.deepEqual([fixtureLanguages(fixture), fixture.scenes, fixture.shell], [config.languages, config.scenes, config.shell], 'Renderer fixture differs from the checked fixture');
    assert.deepEqual(fixtureClock(fixture), config.clock, 'Renderer clock differs from the checked fixture');
    const fonts = loadFonts(fixture, config.fontDirectory), servedFonts = [];
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
        // Counted by matched response key; a preflight is answered by the service, its request is counted.
        if (details.url.startsWith(config.origin + '/') && details.method !== 'OPTIONS') {
          const key = matchResponse(fixture, details.method, details.url) ?? requestKey(details.method, details.url);
          routeCounts[key] = (routeCounts[key] || 0) + 1;
        }
      }
      callback({ cancel: !allow });
    });
    const complete = details => { pending.delete(details.id); lastRequest = Date.now(); };
    isolated.webRequest.onCompleted(complete); isolated.webRequest.onErrorOccurred(complete);
    w = new BrowserWindow(hiddenWindowOptions({ session: isolated, preload: path.join(config.root, 'app/dist-electron/preload.mjs'),
      additionalArguments: ['--bellomberg-launch-id=documentation-demo', '--bellomberg-api-port=' + new URL(config.origin).port] }));
    await installRendererClock(w.webContents, config.clock);
    js = pageScript(w.webContents);
    w.webContents.setWindowOpenHandler(() => { errors.push('Window opening refused'); return { action: 'deny' }; });
    w.webContents.on('will-navigate', (event, url) => { if (!allowedRequest(url, config.origin, local)) { event.preventDefault(); errors.push('Navigation refused'); } });
    w.webContents.on('preload-error', () => errors.push('Preload error'));
    w.webContents.on('render-process-gone', () => errors.push('Renderer process gone'));
    const index = path.join(config.root, 'app/dist/index.html');
    await w.loadFile(index, { hash: '/trades' });
    for (const { language, scene } of captureMatrix(fixture)) {
      const viewport = scene.viewport ?? DEFAULT_VIEWPORT, context = { scene: scene.id, language };
      w.setContentSize(viewport.width, viewport.height);
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
      const observedClock = config.clock ? await js(() => ({ now: new Date().toISOString(),
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone })) : null;
      assert.deepEqual(observedClock, config.clock, 'New document did not receive the declared clock');
      await js(watermark => {
        const mark = document.createElement('div'); mark.id = 'documentation-watermark'; mark.textContent = watermark;
        // This is the only injected visual element. Existing app UI remains intact.
        Object.assign(mark.style, { position: 'fixed', top: '7px', left: '50%', transform: 'translateX(-50%)',
          padding: '6px 14px', background: '#ffdf70', color: '#151515', border: '2px solid #151515',
          font: 'bold 14px sans-serif', zIndex: '2147483647', pointerEvents: 'none', whiteSpace: 'nowrap' });
        document.body.append(mark);
      }, WATERMARK);
      // Before any step: Plotly loads lazily, so its calls are observed from the first one.
      assert.equal(await js(pageObservePlots), false, 'Plotly was loaded before the capture observer: ' + JSON.stringify(context));
      // Documented in-page actions (a tab, a list entry, a field, a disclosure), in order; the page settles after each.
      const executedSteps = await runSteps({ js, steps: sceneSteps(scene), network, context });
      const until = Date.now() + 10000;
      let state, signature, changedAt = Date.now();
      do {
        state = await js(pageState, scene.selector, WATERMARK);
        if (state.signature !== signature) { signature = state.signature; changedAt = Date.now(); }
        if (readyState(state, { ...scene, language, viewport }) && pending.size === 0 && Date.now() - Math.max(lastRequest, changedAt) > 700) break;
        await pause(60);
      } while (Date.now() < until);
      assert.ok(readyState(state, { ...scene, language, viewport }) && pending.size === 0, 'Readiness failed: ' + JSON.stringify({ ...context, state, pending: pending.size }));
      // Wait for actual startup count-up/transition and paint, without altering UI CSS or data.
      await pause(900); await js(async () => {
        // Load both declared app families even when this scene uses only mono.
        // This preloads actual pinned fonts; it changes no element's styling.
        await Promise.all([document.fonts.load('400 12px Inter'), document.fonts.load('400 12px "JetBrains Mono"')]);
        await document.fonts.ready;
        await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
      });
      // Charts drawn after the fonts, still ready and unchanged before the frame is taken.
      const charts = (await settleScene({ js, network, context })).plots;
      const loadedFamilies = await js(() => [...document.fonts].filter(face => face.status === 'loaded').map(face => face.family.replace(/["']/g, '')));
      for (const family of ['Inter', 'JetBrains Mono']) assert.ok(loadedFamilies.includes(family), 'Actual font not loaded: ' + family + '; loaded=' + JSON.stringify(loadedFamilies) + '; served=' + JSON.stringify(servedFonts));
      assert.deepEqual(blocked, []); assert.deepEqual(errors, []);
      for (const key of scene.required.map(responseKey)) assert.ok((routeCounts[key] || 0) > (countsBefore[key] || 0), 'Required scene request not observed: ' + key);
      const outside = sceneRoutesOutside(fixture.shell, scene, Object.keys(routeCounts).filter(key => routeCounts[key] > (countsBefore[key] || 0)));
      assert.deepEqual(outside, [], 'Scene requested undeclared routes: ' + JSON.stringify({ scene: scene.id, outside }));
      assert.equal(await js(() => window.bellomberg.apiUrl), config.origin);
      const prefs = w.webContents.getLastWebPreferences(); assert.ok(prefs.sandbox && prefs.contextIsolation && !prefs.nodeIntegration);
      const dom = await js(() => document.body.innerText + '\n\nDOCUMENTATION FORM VALUES\n' + [...document.querySelectorAll('input,select,textarea')]
        .filter(el => el.getClientRects().length).map(el => el.id + ': ' + el.value).join('\n'));
      assert.ok(dom.includes(WATERMARK));
      // Pixels newer than the DOM checks above: see sceneFrame.
      const { png, attempts, sentinels } = await sceneFrame({ w, js, context });
      if (config.candidate) {
        const stem = 'docs/assets/screenshots/' + scene.id + '-' + language;
        fs.mkdirSync(path.dirname(path.join(config.temporary, stem)), { recursive: true });
        fs.writeFileSync(path.join(config.temporary, stem + '.png'), png);
        fs.writeFileSync(path.join(config.temporary, stem + '.dom.txt'), dom + '\n');
        images.push({ path: stem + '.png', sha256: sha(png), dom_path: stem + '.dom.txt', dom_sha256: sha(Buffer.from(dom + '\n')),
          width: png.readUInt32BE(16), height: png.readUInt32BE(20), language, route: scene.route });
      }
      scenes.push({ id: scene.id, language, route: scene.route, viewport, clock: observedClock, note: scene.note ?? null, steps: executedSteps, charts,
        capture_attempts: attempts, sentinels, ready: true, watermark_visible: state.watermark, dom_sha256: sha(dom), pending_requests: pending.size,
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

module.exports = { allowedRequest, validateOrigin, childEnvironment, resolvePython, sourceDigest, readyState, loadFixture, decodeFixture, parseFixtureJson, validateScene, sceneRoutesOutside, frameShowsSentinel,
  nativeFrameShowsSentinel, captureFreshFrame, sentinelColour, SENTINEL_LIMIT, CAPTURE_ATTEMPTS, BUDGET_MS, treeState, assertCleanTree, verifyCandidates, readInventory, loadFonts, prepareFonts,
  fixtureResponse, assertUnchanged, fingerprint, sceneFrame, inventoryNames, run,
  // Fixture v2 and the renderer pieces the offline tests drive through a real hidden window.
  responseKey, requestKey, canonicalQuery, matchResponse, responseBody, fixtureLanguages, fixtureClock, pageFixedClock, installRendererClock, captureMatrix, navigationDestinations,
  sceneSteps, validateStep, validSelector, syntheticService, pageObservePlots, pageState, pageStep, pageScript,
  settleScene, runSteps, configureElectron, hiddenWindowOptions, DEFAULT_VIEWPORT, MAX_STEPS, STEP_QUIET_MS, WATERMARK };
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
      temporary: r.temporary, scenes: r.scenes.length, images_saved: r.images_saved, tree_clean: r.tree.clean, approval: r.approval })))
      .catch(error => { console.error(String(error)); process.exitCode = 1; });
  } else { console.error('Usage: node app/tools/capture-docs.cjs --prepare-fonts OR [--check|--candidate] --python /absolute/python --font-cache /absolute/cache'); process.exitCode = 2; }
}
