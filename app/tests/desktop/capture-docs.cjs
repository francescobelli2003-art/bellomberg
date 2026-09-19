// Standalone, offline guards. Add --integration for the real hidden renderer/build.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const capture = require('../../tools/capture-docs.cjs');
const root = path.resolve(__dirname, '../../..');
// Longer than the sum of the subprocess budgets the tool declares, so a slow run fails
// with the tool's own receipt instead of a generic node:test timeout.
const INTEGRATION_TIMEOUT_MS = Object.values(capture.BUDGET_MS ?? {}).reduce((n, ms) => n + ms, 0) + 60000;

test('network permits only exact synthetic origin and inventoried local files', () => {
  const file = path.join(os.tmpdir(), 'synthetic-capture-index.html');
  const origin = 'http://127.0.0.1:49199';
  const allowed = new Set([file]);
  assert.equal(capture.allowedRequest(origin + '/portfolio', origin, allowed), true);
  for (const url of ['http://127.0.0.1:8765/health', 'http://localhost:49199/',
    origin + '.evil.invalid/', 'https://example.invalid/', 'ws://127.0.0.1:49199/',
    'file:///C:/outside-documentation/private.sqlite', 'data:text/html,private', 'javascript:1']) {
    assert.equal(capture.allowedRequest(url, origin, allowed), false, url);
  }
  assert.equal(capture.allowedRequest(require('node:url').pathToFileURL(file).href, origin, allowed), true);
  assert.throws(() => capture.validateOrigin('http://127.0.0.1:8765'), /origin/);
  assert.throws(() => capture.validateOrigin('http://127.0.0.1:5173'), /origin/);
  assert.throws(() => capture.validateOrigin('http://127.0.0.1:8000'), /origin/);
  assert.throws(() => capture.validateOrigin('http://user@127.0.0.1:49199'), /origin/);
});

test('child environment has only controlled startup variables and temporary profiles', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'bb-capture-unit-'));
  const env = capture.childEnvironment(tmp, { PATH: 'tool-path', SYSTEMROOT: 'windows',
    OPENAI_API_KEY: 'not-a-real-secret', BELLOMBERG_BACKEND_DIR: 'PM', NODE_OPTIONS: '--require pm',
    HTTP_PROXY: 'pm', ELECTRON_RUN_AS_NODE: '1', APPDATA: 'PM' });
  assert.equal(env.PATH, 'tool-path');
  for (const key of ['OPENAI_API_KEY', 'BELLOMBERG_BACKEND_DIR', 'NODE_OPTIONS', 'HTTP_PROXY', 'ELECTRON_RUN_AS_NODE']) assert.equal(env[key], undefined);
  for (const key of ['APPDATA', 'LOCALAPPDATA', 'HOME', 'USERPROFILE', 'BELLOMBERG_DATA_DIR']) assert.ok(env[key].startsWith(tmp + path.sep));
  assert.throws(() => capture.childEnvironment(root, {}), /temporary/);
});

test('font cache binds exact approved URLs and bytes, refusing drift and escape', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'bb-font-unit-'));
  const bytes = Buffer.from('synthetic font bytes');
  const hash = require('node:crypto').createHash('sha256').update(bytes).digest('hex');
  const asset = { url: 'https://fonts.gstatic.com/s/inter/v99/Demo.woff2',
    filename: 'font.woff2', sha256: hash, content_type: 'font/woff2' };
  fs.writeFileSync(path.join(tmp, asset.filename), bytes);
  const fixture = { fonts: { assets: [asset] } };
  assert.equal(capture.loadFonts(fixture, tmp).get(asset.url).bytes.equals(bytes), true);
  assert.throws(() => capture.loadFonts({ fonts: { assets: [{ ...asset, filename: '../escape' }] } }, tmp), /font/i);
  assert.throws(() => capture.loadFonts({ fonts: { assets: [{ ...asset, url: 'https://example.invalid/font' }] } }, tmp), /font/i);
  fs.writeFileSync(path.join(tmp, asset.filename), 'changed');
  assert.throws(() => capture.loadFonts(fixture, tmp), /font/i);
});

test('font preparation is explicit and verifies content before accepting it', async () => {
  const bytes = Buffer.from('synthetic font bytes');
  const hash = require('node:crypto').createHash('sha256').update(bytes).digest('hex');
  const fixture = { fonts: { assets: [{ url: 'https://fonts.gstatic.com/s/inter/v99/demo.woff2', filename: 'font.woff2', sha256: hash, content_type: 'font/woff2' }] } };
  const calls = [];
  const dir = await capture.prepareFonts(fixture, async (url, options) => {
    calls.push({ url, options }); return new Response(bytes);
  });
  assert.equal(capture.loadFonts(fixture, dir).size, 1);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].options.redirect, 'error');
  await assert.rejects(capture.prepareFonts(fixture, async () => new Response('wrong bytes')), /font/i);
});

test('source hash normalizes CRLF and binds filenames; binary bytes stay exact', () => {
  const a = { 'app/src/a.ts': Buffer.from('a\r\nb\r\n'), 'docs/ignored.png': Buffer.from('a') };
  const b = { 'app/src/a.ts': Buffer.from('a\nb\n') };
  assert.equal(capture.sourceDigest(a), capture.sourceDigest(b));
  assert.notEqual(capture.sourceDigest(a), capture.sourceDigest({ 'app/src/b.ts': b['app/src/a.ts'] }));
  assert.notEqual(capture.sourceDigest({ 'app/src/a.ts': Buffer.from([0, 13, 10]) }),
    capture.sourceDigest({ 'app/src/a.ts': Buffer.from([0, 10]) }));
});

test('source recipe matches the public Python verifier, including Unicode names', () => {
  const tree = { 'app/src/é.ts': Buffer.from('a\r\nb'), 'app/package.json': Buffer.from('{}'),
    'app/src/😀.ts': Buffer.from('supplementary'), 'app/src/\ue000.ts': Buffer.from('bmp'),
    'app/tools/capture-docs.cjs': Buffer.from('ignored'), 'app/resources/raw.bin': Buffer.from([0, 13, 10]) };
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'bb-capture-unit-'));
  const result = spawnSync(capture.resolvePython(process.env.BB_DOCS_PYTHON), ['-I', '-S', '-c', 'import importlib.util,json,sys; s=importlib.util.spec_from_file_location("v",sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); t=json.load(sys.stdin); print(m.build_source_digest({k:bytes(v) for k,v in t.items()}))',
    path.join(root, 'docs/guide/verify_screenshots.py')], { encoding: 'utf8',
    input: JSON.stringify(Object.fromEntries(Object.entries(tree).map(([k, v]) => [k, [...v]])))
      .replace(/[\u007f-\uffff]/g, c => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0')),
    env: capture.childEnvironment(tmp) });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), capture.sourceDigest(tree));
});

test('Python auto-install aliases fail before any subprocess', () => {
  for (const value of [undefined, 'python', 'py', 'C:/Users/demo/AppData/Local/Microsoft/WindowsApps/python.exe']) {
    assert.throws(() => capture.resolvePython(value), /absolute|alias/);
  }
});

test('readiness needs settled fonts/images, visible watermark, route, language, drawn charts and the scene viewport', () => {
  const good = { route: '/trades', language: 'en', ready: true, fonts: true, images: true,
    watermark: true, content: true, busy: false, error: false, charts: true, viewport: { width: 1440, height: 900 } };
  const scene = { route: '/trades', language: 'en', viewport: { width: 1440, height: 900 } };
  assert.equal(capture.readyState(good, scene), true);
  for (const key of ['ready', 'fonts', 'images', 'watermark', 'content', 'charts']) assert.equal(capture.readyState({ ...good, [key]: false }, scene), false, key);
  for (const change of [{ busy: true }, { error: true }, { route: '/dashboard' }, { language: 'it' }, { charts: undefined },
    { viewport: { width: 1440, height: 1000 } }, { viewport: undefined }]) assert.equal(capture.readyState({ ...good, ...change }, scene), false, JSON.stringify(change));
});

// A throwaway repository root holding only what loadFixture reads: the fixture and the navigation registry.
function fixtureRepo(fixture) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'bb-capture-unit-'));
  for (const [name, bytes] of [['app/tests/fixtures/documentation-demo.json', JSON.stringify(fixture)],
    ['app/src/lib/navigation.ts', fs.readFileSync(path.join(root, 'app/src/lib/navigation.ts'))]]) {
    fs.mkdirSync(path.dirname(path.join(tmp, name)), { recursive: true }); fs.writeFileSync(path.join(tmp, name), bytes);
  }
  return tmp;
}
test('local fixture references preserve the full ordered snapshot and independent responses', () => {
  const fixture = capture.loadFixture(root);
  assert.equal(require('node:crypto').createHash('sha256').update(JSON.stringify(fixture)).digest('hex'), '194884bd238a8e7a9d85362c79dc9f7d4d954a339e6ca3e60eb6990a96ef2c31');
  const value = capture.decodeFixture({ schema: 'documentation-local-refs/1', definitions: {
    aaaaaaaaaaaa: { name: 'Readable text' } }, document: [{ $demo_ref: 'aaaaaaaaaaaa' }, { $demo_ref: 'aaaaaaaaaaaa' }] });
  value[0].name = 'changed'; assert.equal(value[1].name, 'Readable text');
  assert.deepEqual(capture.decodeFixture({ schema: 'documentation-local-refs/1', definitions: {}, document: {
    $demo_table: { columns: ['name', 'value'], rows: [['readable', 2], ['second', 3]] } } }),
  [{ name: 'readable', value: 2 }, { name: 'second', value: 3 }]);
});
test('fixture reader rejects invalid refs, cycles, ambiguous markers, columns and duplicate JSON keys', () => {
  assert.equal(typeof capture.decodeFixture, 'function');
  assert.equal(typeof capture.parseFixtureJson, 'function');
  const ref = { $demo_ref: 'aaaaaaaaaaaa' };
  const envelope = (document, definitions = {}) => ({ schema: 'documentation-local-refs/1', document, definitions });
  for (const value of [envelope(ref), envelope({ ...ref, extra: 1 }), envelope(ref, { aaaaaaaaaaaa: ref }),
    envelope(ref, { aaaaaaaaaaaa: { $demo_ref: 'bbbbbbbbbbbb' }, bbbbbbbbbbbb: ref }),
    envelope({}, { aaaaaaaaaaaa: ref }),
    envelope({ $demo_table: { columns: ['a', 'a'], rows: [[1, 2]] } }),
    envelope({ $demo_table: { columns: ['a'], rows: [[1, 2]] } }),
    envelope({ $demo_table: { columns: ['$demo_ref'], rows: [['aaaaaaaaaaaa']] } }),
    envelope({ $demo_table: { columns: ['a'], rows: [[1]], extra: 1 } }),
    { schema: 'documentation-local-refs/2', document: {}, definitions: {} }]) {
    assert.throws(() => capture.decodeFixture(value), /reference|table|encoding|definitions|marker/);
  }
  for (const text of ['{"a":1,"a":2}', '{"a":1,"\\u0061":2}', '{"a":NaN}', '{"a":1e999}']) {
    assert.throws(() => capture.parseFixtureJson(text));
  }
});
const CHAIN = '/options/download/synthetic-1/chain';
const inlineFixture = (scenes, extra = {}) => ({ id: 'documentation-demo', version: 1, shell: ['/health', '/preferences'], scenes,
  responses: { '/health': { status: 'ok' }, 'GET /journal': { items: [] }, 'POST /options/strategy/simulate': { pnl: [] },
    ['GET ' + CHAIN + '?expiry=2026-10-16&limit=250&offset=0']: { page: 1 }, ['GET ' + CHAIN]: { page: 'any' } }, ...extra });
const legacyScene = { id: 'journal', route: '/mandato', selector: '.journal-history li', required: ['/journal'], clicks: ['#tab-diario', '.journal-notes button'] };
const DEMO_CLOCK = { now: '2026-09-13T08:11:40.000Z', timezone: 'Europe/Rome' };

test('documentation clock requires an explicit canonical instant and a valid timezone', () => {
  assert.deepEqual(capture.fixtureClock({ clock: DEMO_CLOCK }), DEMO_CLOCK);
  assert.equal(capture.fixtureClock({}), null); // Legacy fixtures have no injected clock.
  for (const clock of [null, 'today', { now: '2026-09-13T10:11:40', timezone: 'Europe/Rome' },
    { ...DEMO_CLOCK, now: '2026-02-30T00:00:00.000Z' }, { ...DEMO_CLOCK, timezone: 'invalid/zone' },
    { ...DEMO_CLOCK, extra: 1 }]) assert.throws(() => capture.fixtureClock({ clock }), /clock|timezone/i);
});

test('fixed document Date preserves explicit constructors, Date calls, parse and UTC while the runner clock advances', async () => {
  const vm = require('node:vm'), context = vm.createContext({}), hostStart = Date.now();
  vm.runInContext('(' + capture.pageFixedClock.toString() + ')(' + Date.parse(DEMO_CLOCK.now) + ')', context);
  const read = () => JSON.parse(vm.runInContext('JSON.stringify([Date.now(),new Date().toISOString(),new Date(0).getTime(),Date.parse("2000-01-01T00:00:00Z"),Date.UTC(2000,0,1),typeof Date(),new Date() instanceof Date])', context));
  const before = read();
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.deepEqual(read(), before);
  assert.deepEqual(before, [Date.parse(DEMO_CLOCK.now), DEMO_CLOCK.now, 0, 946684800000, 946684800000, 'string', true]);
  assert.ok(Date.now() > hostStart, 'The runner must keep its real clock');
  const freshDocument = vm.createContext({});
  assert.ok(vm.runInContext('Date.now()', freshDocument) > before[0]);
});
const workingScene = { id: 'vol-deck-lab', route: '/vol', selector: '#vd-lab-title', required: ['POST /options/strategy/simulate', 'GET ' + CHAIN + '?expiry=2026-10-16&limit=250&offset=0'],
  steps: [{ click: '[data-vol-workspace="chain"]' }, { fill: { 'vd-ticker': 'DEMO' } }, { select: { 'vd-chain-expiry': '2026-10-16' } },
    { wait: '.vd-chain tbody tr' }, { click: 'button[aria-label="Add call 100"]' }, { scroll: '#vd-lab-title' }],
  viewport: { width: 1440, height: 900 }, note: 'Laboratory after one chain page: the scenario the strategy returns.' };

test('fixture scenes open only navigation destinations and a request is answered only when declared', () => {
  const fixture = capture.loadFixture(root);
  // Independent reading of the registry, with the row pattern docs/guide/verify_docs.py applies.
  const registry = fs.readFileSync(path.join(root, 'app/src/lib/navigation.ts'), 'utf8');
  const destinations = [...registry.matchAll(/^\s*\['[^']+',\s*'([^']+)',/gm)].map(m => m[1]);
  assert.equal(destinations.length, 19);
  assert.deepEqual(capture.navigationDestinations(root), destinations);
  assert.ok(fixture.scenes.every(s => destinations.includes(s.route)), JSON.stringify(fixture.scenes.map(s => s.route)));
  assert.equal(new Set(fixture.scenes.map(s => s.id)).size, fixture.scenes.length);
  assert.equal(capture.fixtureResponse(fixture, 'GET', '/preferences', 'en').body.language, 'en');
  const declared = new Set(Object.keys(fixture.responses).map(capture.responseKey));
  for (const [method, route] of [['POST', '/prices/update'], ['GET', '/unexpected'], ['PUT', '/preferences'], ['POST', '/trade'], ['PUT', '/mandato'],
    ['POST', '/mandato/anteprima'], ['POST', '/journal'], ['PUT', '/journal/1'], ['POST', '/consigliere/run'], ['DELETE', '/favorites/DEMO.A']]) {
    assert.equal(capture.fixtureResponse(fixture, method, route, 'en').status, declared.has(method + ' ' + route) ? 200 : 409, method + ' ' + route);
  }
  assert.equal(capture.fixtureResponse(fixture, 'OPTIONS', '/unexpected', 'en', 'GET').status, 409);
});

test('scene selectors, steps and viewport follow the v2 grammar; legacy clicks and fields stay valid', () => {
  const nav = capture.navigationDestinations(root), fixture = inlineFixture([legacyScene, workingScene]);
  assert.deepEqual(capture.sceneSteps(capture.validateScene(fixture, legacyScene, nav)), [{ click: '#tab-diario' }, { click: '.journal-notes button' }]);
  assert.deepEqual(capture.sceneSteps({ ...legacyScene, fields: { 'f7-tk': 'DEMO.A' } }).at(-1), { field: { 'f7-tk': 'DEMO.A' } });
  assert.equal(capture.validateScene(fixture, workingScene, nav), workingScene);
  for (const selector of ['#tab-diario', '.journal-notes button', 'button[data-action="save"]', 'li[data-selected]', '.vsxgrid .p3 .p3h', '#m-orizzonte_anni',
    'button[aria-label="Dettaglio call strike 90"]']) assert.equal(capture.validSelector(selector), true, selector);
  // Pseudo-classes after a tag, an id, a class or an attribute (13/09: after #id and .class they were not tried).
  for (const selector of ['.journal-notes > button', '#Tab', '[data-action=preview]', 'a:hover', 'li:nth-child(2)', '#tab-diario:focus',
    '.journal-notes:hover button', '[data-x]:not(.y)', '.a::before', '*', 'div + p', 'div ~ p', '#a,#b', '',
    '[data-x="a]"]', '.a  .b', ' .a', 42, 'x'.repeat(201)]) {
    assert.equal(capture.validSelector(selector), false, String(selector));
    for (const kind of ['click', 'wait', 'scroll']) {
      assert.throws(() => capture.validateScene(fixture, { ...workingScene, steps: [{ [kind]: selector }] }, nav), new RegExp(kind + ' selector'), kind + ' ' + selector);
    }
    assert.throws(() => capture.validateScene(fixture, { ...workingScene, selector }, nav), /Invalid documentation scene/, String(selector));
  }
  for (const clicks of [['#tab-diario', '#a', '#b', '#c'], ['.journal-notes > button'], ['#Tab'], [42]]) {
    assert.throws(() => capture.validateScene(fixture, { ...legacyScene, clicks }, nav), /click/, JSON.stringify(clicks));
  }
  const refusedSteps = [[Array(capture.MAX_STEPS + 1).fill({ wait: '#a' }), /Too many/], [[{ hover: '#a' }], /Invalid documentation step/],
    [[{ click: '#a', wait: '#b' }], /Invalid documentation step/], [[{ field: { a: '1' } }], /Invalid documentation step/], [['#a'], /Invalid documentation step/],
    [[{ fill: { a: 1 } }], /fill step/], [[{ fill: {} }], /fill step/], [[{ select: { 'bad id': 'x' } }], /select step/], [[{ fill: ['a'] }], /fill step/]];
  for (const [steps, error] of refusedSteps) assert.throws(() => capture.validateScene(fixture, { ...workingScene, steps }, nav), error, JSON.stringify(steps).slice(0, 80));
  assert.ok(capture.validateScene(fixture, { ...workingScene, steps: Array(capture.MAX_STEPS).fill({ wait: '#a' }) }, nav));
  assert.throws(() => capture.validateScene(fixture, { ...workingScene, clicks: ['#a'] }, nav), /mixes steps/);
  for (const viewport of [{ width: 100, height: 900 }, { width: 1440 }, { width: 1440.5, height: 900 }, { width: 1440, height: 900, scale: 2 }, null, '1440x900']) {
    assert.throws(() => capture.validateScene(fixture, { ...workingScene, viewport }, nav), /viewport/, JSON.stringify(viewport));
  }
  assert.throws(() => capture.validateScene(fixture, { ...workingScene, step: [] }, nav), /Unknown documentation scene field/);
  assert.throws(() => capture.validateScene(fixture, { ...workingScene, note: '' }, nav), /note/);
  assert.throws(() => capture.validateScene(fixture, { ...workingScene, required: ['GET /journal/2'] }, nav), /synthetic response/);
  assert.throws(() => capture.validateScene(fixture, { ...workingScene, required: ['GET ' + CHAIN + '?offset=0&limit=250&expiry=2026-10-16'] }, nav), /sorted/);
  assert.throws(() => capture.validateScene(fixture, { ...workingScene, required: ['/journal', 'GET /journal'] }, nav), /Duplicate required/);
  // Scene reads: shell and declared keys only, compared in their method form.
  assert.deepEqual(capture.sceneRoutesOutside(fixture.shell, workingScene, ['GET /health', 'GET /preferences', 'POST /options/strategy/simulate']), []);
  assert.deepEqual(capture.sceneRoutesOutside(fixture.shell, legacyScene, ['GET /journal', 'POST /options/strategy/simulate', 'GET /x?a=1']), ['GET /x?a=1', 'POST /options/strategy/simulate']);
  // The same refusals when the capture loads a fixture from disk.
  assert.throws(() => capture.loadFixture(fixtureRepo(inlineFixture([{ ...legacyScene, clicks: ['[data-action=save]'] }]))), /click/);
  assert.throws(() => capture.loadFixture(fixtureRepo(inlineFixture([{ ...workingScene, steps: [{ scroll: 'a:hover' }] }]))), /scroll selector/);
  assert.equal(capture.loadFixture(fixtureRepo(inlineFixture([legacyScene, workingScene]))).scenes.length, 2);
});

test('synthetic API selects only authored presentation paths and never mutates its snapshot', () => {
  const payload = { rows: [{ label: 'Analista', price: 50 }], archived: 'Analista',
    nested: { label: 'Scaduto', _presentation_v1: { version: 1, texts: [{ path: ['label'], it: 'Scaduto', en: 'Stale' }] } },
    _presentation_v1: { version: 1, texts: [{ path: ['rows', 0, 'label'], it: 'Analista', en: 'Analyst' }] } };
  const fixture = inlineFixture([], { responses: { 'GET /example': payload } }), before = structuredClone(payload);
  const en = capture.fixtureResponse(fixture, 'GET', '/example', 'en');
  assert.equal(en.status, 200); assert.equal(en.body.rows[0].label, 'Analyst');
  assert.equal(en.body.nested.label, 'Stale'); assert.equal(en.body.archived, 'Analista');
  assert.equal(en.body.rows[0].price, 50); assert.deepEqual(en.body._presentation_v1, payload._presentation_v1);
  en.body.rows[0].price = 10; en.body._presentation_v1.texts[0].en = 'changed';
  assert.deepEqual(payload, before);
  assert.deepEqual(capture.fixtureResponse(fixture, 'GET', '/example', 'it').body, before);
  for (const language of ['fr', '', null, undefined]) assert.equal(capture.fixtureResponse(fixture, 'GET', '/example', language).status, 400);
  const invalid = [
    p => delete p._presentation_v1.texts[0].en,
    p => p._presentation_v1.texts[0].path = ['rows', 0, 'missing'],
    p => p._presentation_v1.texts[0].path = ['__proto__', 'label'],
    p => p._presentation_v1.texts[0].path = ['_presentation_v1', 'texts', 0, 'it'],
    p => p._presentation_v1.texts[0].path = ['rows', -1, 'label'],
    p => p._presentation_v1.texts.push(structuredClone(p._presentation_v1.texts[0])),
    p => p.rows[0].label = 42,
    p => p.rows[0].label = 'User-authored replacement',
  ];
  for (const damage of invalid) {
    const bad = structuredClone(payload); damage(bad);
    const result = capture.fixtureResponse(inlineFixture([], { responses: { 'GET /example': bad } }), 'GET', '/example', 'en');
    assert.equal(result.status, 500); assert.equal(result.body.code, 'SYNTHETIC_PRESENTATION_INVALID');
  }
  const all = capture.loadFixture(root), original = structuredClone(all);
  for (const key of Object.keys(all.responses)) {
    const split = key.indexOf(' ');
    for (const language of ['it', 'en']) assert.equal(capture.fixtureResponse(all, key.slice(0, split), key.slice(split + 1), language).status, 200, key);
  }
  assert.deepEqual(all, original);
  assert.equal(capture.fixtureResponse(all, 'GET', '/agents/list', 'en').body.agents[3].role, 'Quant');
});

test('a scene whose route is not a navigation destination is refused', () => {
  const nav = capture.navigationDestinations(root), fixture = inlineFixture([]);
  for (const route of ['/secret', '/vol/', '/memos/12', '#/vol', 'vol', undefined]) {
    assert.throws(() => capture.validateScene(fixture, { ...workingScene, route }, nav), /not a navigation destination/, String(route));
  }
  assert.throws(() => capture.validateScene(fixture, workingScene), /Navigation destinations required/);
  assert.throws(() => capture.loadFixture(fixtureRepo(inlineFixture([{ ...legacyScene, route: '/secret' }]))), /not a navigation destination/);
  // The registry is read from the checked-out source: an unreadable row stops the capture instead of shrinking the list.
  const repo = fixtureRepo(inlineFixture([legacyScene]));
  const registry = path.join(repo, 'app/src/lib/navigation.ts');
  fs.writeFileSync(registry, fs.readFileSync(registry, 'utf8').replace("['vol', '/vol',", "['vol', `/vol`,"));
  assert.throws(() => capture.loadFixture(repo), /Unreadable navigation registry row/);
  fs.writeFileSync(registry, 'export const NAVIGATION = [];\n');
  assert.throws(() => capture.loadFixture(repo), /Navigation registry not found/);
});

test('response keys carry method and the exact sorted query; lookup tries method, path and query, then method and path', () => {
  assert.equal(capture.responseKey('/portfolio'), 'GET /portfolio');
  assert.equal(capture.responseKey('POST /options/strategy/simulate'), 'POST /options/strategy/simulate');
  assert.equal(capture.requestKey('GET', CHAIN + '?offset=0&limit=250&expiry=2026-10-16'), 'GET ' + CHAIN + '?expiry=2026-10-16&limit=250&offset=0');
  assert.equal(capture.requestKey('GET', 'http://127.0.0.1:49199/options/vol_surface/DEMO?include_context=false&expiries=2026-10-16,2026-11-20'),
    'GET /options/vol_surface/DEMO?expiries=2026-10-16%2C2026-11-20&include_context=false');
  assert.equal(capture.requestKey('GET', '/x?b=2&a=1&a=0'), 'GET /x?a=1&a=0&b=2');
  for (const key of ['PATCH /x', 'get /x', 'GET x', 'GET /x?', 'GET /x?b=1&a=2', 'GET /x?expiries=a,b', 'GET /x#top', 'GET /a/../b', 'GET  /x', 'POST', 42, null]) {
    assert.throws(() => capture.responseKey(key), /response key|sorted/, String(key));
  }
  const fixture = inlineFixture([]);
  assert.deepEqual(capture.fixtureResponse(fixture, 'GET', CHAIN + '?offset=0&limit=250&expiry=2026-10-16', 'en'),
    { status: 200, body: { page: 1 }, key: 'GET ' + CHAIN + '?expiry=2026-10-16&limit=250&offset=0' });
  assert.deepEqual(capture.fixtureResponse(fixture, 'GET', CHAIN + '?expiry=2026-10-16&limit=250&offset=250', 'en'), { status: 200, body: { page: 'any' }, key: 'GET ' + CHAIN });
  assert.equal(capture.matchResponse(inlineFixture([], { responses: { ['GET ' + CHAIN + '?offset=0']: 1 } }), 'GET', CHAIN + '?offset=250'), null);
  for (const [method, target] of [['POST', CHAIN], ['GET', '/options/strategy/simulate'], ['PUT', '/options/strategy/simulate'], ['DELETE', '/journal'], ['PATCH', '/journal'], ['HEAD', '/health']]) {
    assert.equal(capture.fixtureResponse(fixture, method, target, 'en').status, 409, method + ' ' + target);
  }
  assert.deepEqual(capture.fixtureResponse(fixture, 'GET', '/preferences?x=1', 'it').body, { language: 'it', selected: true, source: 'preferences' });
  assert.throws(() => capture.fixtureResponse(inlineFixture([], { responses: { '/x': 1, 'GET /x': 2 } }), 'GET', '/x', 'en'), /Duplicate synthetic response key/);
  assert.throws(() => capture.fixtureResponse(inlineFixture([], { responses: { 'GET /preferences': {} } }), 'GET', '/x', 'en'), /preferences/);
  assert.deepEqual(capture.responseBody(capture.loadFixture(root), '/health'), capture.responseBody(capture.loadFixture(root), 'GET /health'));
});

test('languages: the fixture decides which languages are captured, Italian and English when absent', () => {
  assert.deepEqual(capture.fixtureLanguages({}), ['it', 'en']);
  assert.deepEqual(capture.fixtureLanguages({ languages: ['en'] }), ['en']);
  for (const languages of [[], ['fr'], ['en', 'en'], 'en', null]) assert.throws(() => capture.fixtureLanguages({ languages }), /languages/, JSON.stringify(languages));
  const scenes = [{ id: 'a' }, { id: 'b' }];
  assert.deepEqual(capture.captureMatrix({ languages: ['en'], scenes }).map(c => c.language + ':' + c.scene.id), ['en:a', 'en:b']);
  assert.deepEqual(capture.captureMatrix({ scenes }).map(c => c.language + ':' + c.scene.id), ['it:a', 'it:b', 'en:a', 'en:b']);
  const english = capture.loadFixture(fixtureRepo(inlineFixture([legacyScene, workingScene], { languages: ['en'] })));
  assert.deepEqual(capture.captureMatrix(english).map(c => c.language), ['en', 'en']);
  assert.throws(() => capture.loadFixture(fixtureRepo(inlineFixture([legacyScene], { languages: ['it', 'de'] }))), /languages/);
  // Wiring: the renderer walks captureMatrix of the fixture it loaded, never a literal language list, and run() hands it the languages.
  const source = fs.readFileSync(path.join(root, 'app/tools/capture-docs.cjs'), 'utf8');
  const rendererSource = source.slice(source.indexOf('async function renderer('), source.indexOf('module.exports'));
  assert.equal(rendererSource.split('of captureMatrix(fixture))').length - 1, 1);
  assert.doesNotMatch(rendererSource, /for \(const language of/);
  assert.match(capture.run.toString(), /languages: fixtureLanguages\(fixture\)/);
  // More captures than unused sentinel colours are refused when the fixture loads, before any build.
  const many = n => inlineFixture(Array.from({ length: n }, (_, i) => ({ ...legacyScene, id: 'scene-' + i })), { languages: ['en'] });
  const most = Math.floor(capture.SENTINEL_LIMIT / capture.CAPTURE_ATTEMPTS);
  assert.equal(capture.loadFixture(fixtureRepo(many(most))).scenes.length, most);
  assert.throws(() => capture.loadFixture(fixtureRepo(many(most + 1))), /sentinel/);
  assert.throws(() => capture.loadFixture(fixtureRepo({ ...many(Math.ceil(most / 2) + 1), languages: ['it', 'en'] })), /sentinel/);
});

// Minimal HTTP client: node:http sends preflight headers verbatim.
function request(base, method, target, { body, headers = {} } = {}) {
  return new Promise((resolve, reject) => {
    const data = body === undefined ? null : Buffer.from(typeof body === 'string' ? body : JSON.stringify(body));
    const req = require('node:http').request(base + target, { method, headers: { 'X-BB-Language': 'en', ...(data ? { 'Content-Type': 'application/json', 'Content-Length': data.length } : {}), ...headers } }, res => {
      const chunks = []; res.on('data', c => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, body: JSON.parse(Buffer.concat(chunks).toString('utf8')), allow: res.headers['access-control-allow-methods'] }));
    });
    req.on('error', reject); if (data) req.write(data); req.end();
  });
}

test('synthetic service: an undeclared POST stays 409, a declared POST returns its fixed body and the received body is recorded', async () => {
  const { server, requests, unexpected } = capture.syntheticService(inlineFixture([]));
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = 'http://127.0.0.1:' + server.address().port;
  try {
    const legs = { legs: [{ side: 'sell', strike: 110 }], iv_shift: 0.055 };
    assert.deepEqual(await request(base, 'POST', '/options/strategy/simulate', { body: legs }), { status: 200, body: { pnl: [] }, allow: 'GET,OPTIONS' });
    // Nothing is stored: another body gets the same declared answer.
    assert.deepEqual((await request(base, 'POST', '/options/strategy/simulate', { body: { legs: [] } })).body, { pnl: [] });
    assert.equal((await request(base, 'POST', '/trade', { body: { ticker: 'DEMO.A', quantita: 2 } })).status, 409);
    assert.equal((await request(base, 'GET', '/options/strategy/simulate')).status, 409);
    assert.equal((await request(base, 'DELETE', '/journal')).status, 409);
    assert.deepEqual(await request(base, 'OPTIONS', '/options/strategy/simulate', { headers: { 'Access-Control-Request-Method': 'POST' } }), { status: 200, body: {}, allow: 'POST,OPTIONS' });
    assert.equal((await request(base, 'OPTIONS', '/options/strategy/simulate', { headers: { 'Access-Control-Request-Method': 'PUT' } })).status, 409);
    assert.equal((await request(base, 'OPTIONS', '/trade', { headers: { 'Access-Control-Request-Method': 'POST' } })).status, 409);
    assert.deepEqual((await request(base, 'GET', CHAIN + '?offset=0&limit=250&expiry=2026-10-16')).body, { page: 1 });
    assert.deepEqual((await request(base, 'GET', CHAIN + '?offset=250&limit=250&expiry=2026-10-16')).body, { page: 'any' });
    assert.equal((await request(base, 'GET', '/preferences')).body.language, 'en');
    assert.equal((await request(base, 'POST', '/options/strategy/simulate', { body: 'x'.repeat(1024 * 1024 + 1) })).status, 413);
    const posts = requests.filter(r => r.method === 'POST');
    assert.deepEqual(posts.slice(0, 3).map(r => [r.route, r.key, r.status, r.body.json]), [
      ['/options/strategy/simulate', 'POST /options/strategy/simulate', 200, legs],
      ['/options/strategy/simulate', 'POST /options/strategy/simulate', 200, { legs: [] }],
      ['/trade', null, 409, { ticker: 'DEMO.A', quantita: 2 }]]);
    assert.ok(posts.slice(0, 3).every(r => r.body.bytes > 0 && /^[0-9a-f]{64}$/.test(r.body.sha256)));
    assert.deepEqual(posts[3].body, { bytes: 1024 * 1024 + 1, refused: true });
    const chain = requests.find(r => r.route === CHAIN);
    assert.deepEqual([chain.query, chain.key], ['expiry=2026-10-16&limit=250&offset=0', 'GET ' + CHAIN + '?expiry=2026-10-16&limit=250&offset=0']);
    assert.deepEqual(unexpected.map(r => [r.method, r.route, r.preflight ?? '', r.status]), [['POST', '/trade', '', 409], ['GET', '/options/strategy/simulate', '', 409],
      ['DELETE', '/journal', '', 409], ['OPTIONS', '/options/strategy/simulate', 'PUT', 409], ['OPTIONS', '/trade', 'POST', 409], ['POST', '/options/strategy/simulate', '', 413]]);
  } finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
  // Wiring: the capture run serves this service and checks required keys against its records.
  assert.match(capture.run.toString(), /\{ server, requests, unexpected \} = syntheticService\(fixture\)/);
  assert.match(capture.run.toString(), /r\.key === key && r\.status === 200/);
});

test('steps run one at a time in fixture order and the page settles after each one before the next starts', async () => {
  let now = 0;
  const clock = () => now, wait = async ms => { now += ms; }, events = [];
  const readyAt = { '#late': 300, '#never': Infinity };
  const js = async (fn, step) => {
    assert.equal(fn, capture.pageStep);
    const kind = Object.keys(step)[0], target = JSON.stringify(step[kind]);
    if ((readyAt[step[kind]] ?? 0) > now) { events.push('retry ' + target); return { done: false, reason: 'missing or hidden' }; }
    events.push('act ' + kind + ' ' + target); return { done: true };
  };
  const settle = async ({ context }) => { events.push('settle ' + context.index); now += 10; };
  const steps = [{ click: '#a' }, { wait: '#late' }, { fill: { name: 'DEMO' } }, { select: { choice: 'two' } }, { scroll: '#end' }];
  const executed = await capture.runSteps({ js, steps, network: {}, clock, wait, settle, context: { scene: 'synthetic' } });
  assert.deepEqual(executed.map(s => [s.index, s.kind]), [[0, 'click'], [1, 'wait'], [2, 'fill'], [3, 'select'], [4, 'scroll']]);
  assert.deepEqual(events.filter(e => !e.startsWith('retry')), ['settle -1', 'act click "#a"', 'settle 0', 'act wait "#late"', 'settle 1',
    'act fill {"name":"DEMO"}', 'settle 2', 'act select {"choice":"two"}', 'settle 3', 'act scroll "#end"', 'settle 4']);
  assert.ok(events.indexOf('retry "#late"') > events.indexOf('settle 0'));
  // A target that never becomes ready fails with scene, index and reason; later steps never run.
  events.length = 0;
  await assert.rejects(capture.runSteps({ js, steps: [{ click: '#a' }, { click: '#never' }, { click: '#b' }], network: {}, clock, wait, settle, timeoutMs: 1000, context: { scene: 'synthetic' } }),
    /step not ready.*"scene":"synthetic","index":1.*missing or hidden/);
  assert.ok(!events.includes('act click "#b"') && !events.includes('settle 1'));
  // A scene without steps goes straight to readiness.
  events.length = 0;
  assert.deepEqual(await capture.runSteps({ js, steps: [], network: {}, clock, wait, settle }), []);
  assert.deepEqual(events, []);
});

test('settling waits for no pending request, no busy element, ready charts and a quiet period that restarts on change', async () => {
  // Each scenario keeps exactly one condition unmet until `until`; ignoring that condition ends near 500 ms.
  const scenarios = {
    'pending request': { pending: t => t < 1500 ? 1 : 0, until: 1500 },
    'busy element': { busy: t => t < 1500, until: 1500 },
    'charts not ready': { charts: t => t >= 1500, until: 1500 },
    // Charts become ready at 1200, but their state changed at 1000: quiet counts from the change.
    'chart state changed': { charts: t => t >= 1200, signature: t => t < 1000 ? 'drawing' : 'drawn', until: 1500 },
    'late request': { lastRequest: t => Math.min(t, 1300), until: 1800 },
  };
  for (const [name, s] of Object.entries(scenarios)) {
    let now = 0;
    const calls = [];
    const js = async (fn, ...args) => {
      calls.push(fn === capture.pageState && args.length === 2 && args.every(a => a === null) ? 'state' : 'other');
      return { busy: s.busy?.(now) ?? false, charts: s.charts?.(now) ?? true, signature: s.signature?.(now) ?? 'same', plots: {} };
    };
    const network = { pending: () => s.pending?.(now) ?? 0, lastRequest: () => s.lastRequest?.(now) ?? 0 };
    const state = await capture.settleScene({ js, network, clock: () => now, wait: async ms => { now += ms; }, quietMs: 500 });
    assert.ok(now >= s.until && now < s.until + 700, name + ' ended at ' + now);
    assert.deepEqual([state.charts, calls.includes('other')], [true, false], name);
  }
  let now = 0;
  await assert.rejects(capture.settleScene({ js: async fn => fn === capture.pageState ? { busy: false, charts: false, signature: 'x', plots: { no_webgl: 1 } } : undefined,
    network: { pending: () => 0, lastRequest: () => 0 }, clock: () => now, wait: async ms => { now += ms; }, timeoutMs: 2000, context: { scene: 'synthetic' } }),
  /did not settle.*"scene":"synthetic".*"no_webgl":1/);
});

// Offline: a real hidden Electron window with the capture's own GPU settings, page functions, steps,
// settling and frame capture, on local pages. The only resource loaded is the pinned Plotly bundle file.
test('in a hidden window steps run in order, settling waits for a slow chart, and a Plotly 3D surface reaches the captured pixels', { timeout: 120000 }, t => {
  const tmp = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'bb-capture-window-')));
  const plotly = require('node:url').pathToFileURL(path.join(root, 'app/public/vendor/plotly-2.32.0.min.js')).href;
  fs.writeFileSync(path.join(tmp, 'steps.html'), `<!doctype html><html lang="en"><head><meta charset="utf-8">
<style>body{margin:0;font:14px sans-serif;background:#fff}#spacer{height:3000px}</style></head><body>
<button id="open" aria-label="Open panel" disabled>Open</button><div id="panel"></div><div id="spacer"></div>
<section data-part="end"><h2 class="end-title">End</h2></section><div style="height:2000px"></div>
<script>
window.__initialNow = Date.now();
window.__log = [];
const log = entry => window.__log.push(entry);
// Enabled only after the pre-step settle period: the click step must wait for it.
setTimeout(() => { document.getElementById('open').disabled = false; }, 1200);
document.getElementById('open').addEventListener('click', () => {
  log('click:open');
  setTimeout(() => {
    const panel = document.getElementById('panel');
    panel.innerHTML = '<input id="name"><select id="choice"></select><span class="status"></span>';
    const choice = document.getElementById('choice');
    document.getElementById('name').addEventListener('input', event => {
      log('input:name=' + event.target.value);
      // Options arrive later than the settle quiet period: select must wait for them.
      setTimeout(() => { choice.innerHTML = '<option value="one">one</option><option value="two">two</option>'; }, 800);
    });
    choice.addEventListener('change', event => {
      log('change:choice=' + event.target.value);
      // A chart library that appears only now and settles 3 s later, with no request in between: without the chart
      // condition two settle periods (about 1.1 s) would end before it.
      window.Plotly = { newPlot: () => new Promise(resolve => setTimeout(() => { log('plot:settled'); resolve(); }, 3000)) };
      window.Plotly.newPlot(panel);
      setTimeout(() => panel.querySelector('.status').setAttribute('data-ready', 'yes'), 100);
    });
  }, 200);
});
addEventListener('scroll', () => { if (!window.__scrolled) { window.__scrolled = true; log('scroll'); } });
</script></body></html>`);
  fs.writeFileSync(path.join(tmp, 'surface.html'), `<!doctype html><html lang="en"><head><meta charset="utf-8">
<style>body{margin:0;background:#fff;font:14px sans-serif}#plot{position:absolute;left:80px;top:120px;width:720px;height:480px}</style></head><body>
<button data-vol-workspace="tools" style="position:absolute;left:880px;top:120px">Tools</button><div id="plot"></div>
<script>
window.__initialNow = Date.now();
document.querySelector('[data-vol-workspace="tools"]').addEventListener('click', () => {
  const script = document.createElement('script');
  script.src = ${JSON.stringify(plotly)};
  script.onload = () => {
    const z = Array.from({ length: 6 }, (_, i) => Array.from({ length: 17 }, (_, j) => 20 + 30 * Math.pow((j - 8) / 8, 2) + i * 2));
    window.Plotly.newPlot(document.getElementById('plot'), [{ type: 'surface', z, showscale: false, colorscale: [[0, '#8a6d1f'], [1, '#d4af37']] }],
      { paper_bgcolor: '#fff', margin: { l: 0, r: 0, t: 0, b: 0 }, scene: { xaxis: { visible: false }, yaxis: { visible: false }, zaxis: { visible: false }, bgcolor: '#fff' } },
      { displayModeBar: false });
  };
  document.head.append(script);
});
</script></body></html>`);
  fs.writeFileSync(path.join(tmp, 'main.cjs'), `
const capture = require(${JSON.stringify(path.join(root, 'app/tools/capture-docs.cjs'))});
const { app, BrowserWindow, session } = require('electron');
const path = require('node:path');
const tmp = ${JSON.stringify(tmp)};
capture.configureElectron(app, path.join(tmp, 'profile'));
const out = value => { console.log('BB_WINDOW_RESULT ' + JSON.stringify(value)); app.exit(0); };
// Surface-coloured pixels (red minus blue >= 24) inside the plot rectangle of an accepted frame.
function warmPixels(image, rect) {
  const factor = image.getScaleFactors()[0] ?? 1, size = image.getSize(factor), bitmap = image.toBitmap({ scaleFactor: factor });
  const ratio = size.width / rect.innerWidth;
  let warm = 0;
  for (let y = Math.round(rect.top * ratio); y < Math.round(rect.bottom * ratio); y++) for (let x = Math.round(rect.left * ratio); x < Math.round(rect.right * ratio); x++) {
    const i = (y * size.width + x) * 4; if (bitmap[i + 2] - bitmap[i] >= 24) warm++;
  }
  return warm;
}
(async () => {
  try {
    await app.whenReady();
    const pending = new Set(); let lastRequest = Date.now();
    const web = session.defaultSession.webRequest;
    web.onBeforeRequest((details, callback) => { pending.add(details.id); lastRequest = Date.now(); callback({}); });
    const complete = details => { pending.delete(details.id); lastRequest = Date.now(); };
    web.onCompleted(complete); web.onErrorOccurred(complete);
    const network = { pending: () => pending.size, lastRequest: () => lastRequest };
    const w = new BrowserWindow(capture.hiddenWindowOptions({ viewport: { width: 1280, height: 800 } }));
    await capture.installRendererClock(w.webContents, ${JSON.stringify(DEMO_CLOCK)});
    const js = capture.pageScript(w.webContents);
    const result = {};
    await w.loadFile(path.join(tmp, 'steps.html'));
    result.firstClock = await js(() => [new Date().toISOString(), new Date(0).getTime(), new Date('2026-09-13T10:11:40').toISOString(), Intl.DateTimeFormat().resolvedOptions().timeZone, window.__initialNow]);
    // 13/09: created with useContentSize 1280x800, the hidden page measured 1282x802; the renderer sets the size per scene.
    result.creationViewport = await js(() => [innerWidth, innerHeight]);
    w.setContentSize(1280, 800);
    result.firstViewport = await js(() => [innerWidth, innerHeight]);
    await js(capture.pageObservePlots);
    result.steps = await capture.runSteps({ js, network, context: { scene: 'steps' }, steps: [
      { click: 'button[aria-label="Open panel"]' }, { fill: { name: 'DEMO' } }, { select: { choice: 'two' } },
      { wait: '#panel .status[data-ready="yes"]' }, { scroll: 'section[data-part="end"] .end-title' }] });
    result.log = await js(() => window.__log);
    result.values = await js(() => [document.getElementById('name').value, document.getElementById('choice').value]);
    result.endTop = await js(() => document.querySelector('.end-title').getBoundingClientRect().top);
    result.stepsPlots = (await js(capture.pageState, null, null)).plots;
    w.setContentSize(1440, 900);
    await w.loadFile(path.join(tmp, 'surface.html'));
    result.secondClock = await js(() => [new Date().toISOString(), new Date(0).getTime(), new Date('2026-09-13T10:11:40').toISOString(), Intl.DateTimeFormat().resolvedOptions().timeZone, window.__initialNow]);
    await js(watermark => {
      const mark = document.createElement('div'); mark.id = 'documentation-watermark'; mark.textContent = watermark;
      Object.assign(mark.style, { position: 'fixed', top: '7px', left: '50%', transform: 'translateX(-50%)', padding: '6px 14px', background: '#ffdf70',
        color: '#151515', border: '2px solid #151515', font: 'bold 14px sans-serif', zIndex: '2147483647', pointerEvents: 'none', whiteSpace: 'nowrap' });
      document.body.append(mark);
    }, capture.WATERMARK);
    result.preloaded = await js(capture.pageObservePlots);
    const rect = () => js(() => { const r = document.getElementById('plot').getBoundingClientRect(); return { left: r.left, top: r.top, right: r.right, bottom: r.bottom, innerWidth }; });
    result.beforeState = await js(capture.pageState, null, capture.WATERMARK);
    result.warmBefore = warmPixels((await capture.sceneFrame({ w, js, context: { scene: 'surface-before' } })).image, await rect());
    result.surfaceSteps = await capture.runSteps({ js, network, context: { scene: 'surface' }, steps: [{ click: '[data-vol-workspace="tools"]' }] });
    const state = await js(capture.pageState, null, capture.WATERMARK);
    result.state = { charts: state.charts, plots: state.plots, viewport: state.viewport, watermark: state.watermark };
    const frame = await capture.sceneFrame({ w, js, context: { scene: 'surface' } });
    result.warmAfter = warmPixels(frame.image, await rect());
    result.attempts = frame.attempts;
    // A visible Plotly element showing Plotly's no-WebGL panel is never ready.
    result.noWebglState = (await js(() => {
      const gd = document.createElement('div'); gd.className = 'js-plotly-plot'; gd.style.cssText = 'position:absolute;left:0;top:700px;width:40px;height:40px';
      const panel = document.createElement('div'); panel.className = 'no-webgl'; gd.append(panel); document.body.append(gd);
    }), await js(capture.pageState, null, null));
    result.webglRenderer = await js(() => { const c = document.querySelector('.gl-container canvas'); const gl = c && (c.getContext('webgl') || c.getContext('webgl2'));
      const ext = gl && gl.getExtension('WEBGL_debug_renderer_info'); return ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null; });
    out({ ok: true, ...result });
  } catch (error) { out({ ok: false, error: String(error && error.stack || error) }); }
})();
`);
  const env = capture.childEnvironment(fs.mkdtempSync(path.join(os.tmpdir(), 'bb-capture-unit-')));
  const run = spawnSync(require(path.join(root, 'app/node_modules/electron')), [path.join(tmp, 'main.cjs')], { encoding: 'utf8', env, timeout: 100000, windowsHide: true });
  const line = String(run.stdout).split(/\r?\n/).find(l => l.startsWith('BB_WINDOW_RESULT '));
  assert.ok(line, 'No window result: ' + JSON.stringify({ status: run.status, signal: run.signal, error: String(run.error), stderr: String(run.stderr).slice(-3000) }));
  const r = JSON.parse(line.slice('BB_WINDOW_RESULT '.length));
  assert.equal(r.ok, true, r.error);
  assert.deepEqual(r.firstClock, [DEMO_CLOCK.now, 0, DEMO_CLOCK.now, DEMO_CLOCK.timezone, Date.parse(DEMO_CLOCK.now)]);
  assert.deepEqual(r.secondClock, r.firstClock, 'Every new document must get the same clock before application scripts');
  t.diagnostic(JSON.stringify({ warm_before: r.warmBefore, warm_after: r.warmAfter, webgl: r.webglRenderer, attempts: r.attempts,
    creation_viewport: r.creationViewport, steps_ms: r.steps.map(s => s.ms) }));
  // Steps: in order, each after the previous one settled (the chart settled before the scroll step).
  assert.deepEqual(r.log, ['click:open', 'input:name=DEMO', 'change:choice=two', 'plot:settled', 'scroll']);
  assert.deepEqual(r.steps.map(s => s.kind), ['click', 'fill', 'select', 'wait', 'scroll']);
  assert.deepEqual(r.values, ['DEMO', 'two']);
  assert.ok(Math.abs(r.endTop) <= 1, 'scroll target top ' + r.endTop);
  assert.deepEqual([r.stepsPlots.calls, r.stepsPlots.settled, r.stepsPlots.errors], [1, 1, []]);
  // Viewport: after setContentSize, as the renderer does before each scene, the page measures exactly that size.
  assert.deepEqual(r.firstViewport, [1280, 800], 'created as ' + JSON.stringify(r.creationViewport));
  assert.deepEqual(r.state.viewport, { width: 1440, height: 900 });
  // Plotly loads after the observer; the 3D surface is plotted with a live WebGL context and reaches the frame.
  assert.equal(r.preloaded, false);
  assert.deepEqual([r.beforeState.charts, r.beforeState.plots.graphs], [true, 0]);
  assert.equal(r.state.charts, true, JSON.stringify(r.state.plots));
  assert.deepEqual([r.state.plots.calls, r.state.plots.settled, r.state.plots.graphs, r.state.plots.no_webgl, r.state.plots.lost_contexts], [1, 1, 1, 0, 0]);
  assert.ok(r.state.plots.gl_canvases >= 1 && r.state.watermark, JSON.stringify(r.state));
  assert.deepEqual([r.noWebglState.charts, r.noWebglState.plots.no_webgl, r.noWebglState.plots.graphs], [false, 1, 2]);
  assert.ok(r.warmBefore < 200, 'surface pixels before plotting: ' + r.warmBefore);
  assert.ok(r.warmAfter > 20000, 'surface pixels in the captured frame: ' + r.warmAfter + ' (' + r.webglRenderer + ')');
});

test('frame freshness needs the exact sentinel pixel of a BGRA bitmap scaled from CSS pixels', () => {
  const width = 10, bitmap = Buffer.alloc(width * 4 * 4);
  const put = (x, y, [r, g, b]) => { const i = (y * width + x) * 4; bitmap[i] = b; bitmap[i + 1] = g; bitmap[i + 2] = r; bitmap[i + 3] = 255; };
  const probe = { x: 4, y: 1.6, width: 8 }; // ratio 1.25 -> pixel (5, 2)
  put(5, 2, [0xff, 0xe1, 0x70]);
  assert.equal(capture.frameShowsSentinel(bitmap, width, probe, '#ffe170'), true);
  assert.equal(capture.frameShowsSentinel(bitmap, width, probe, '#ffe070'), false);
  assert.equal(capture.frameShowsSentinel(bitmap, width, { ...probe, width: 10 }, '#ffe170'), false);
  assert.equal(capture.frameShowsSentinel(bitmap, width, { x: 99, y: 99, width: 8 }, '#ffe170'), false);
  put(5, 2, [0x70, 0xe1, 0xff]);
  assert.equal(capture.frameShowsSentinel(bitmap, width, probe, '#ffe170'), false);
});

test('every capture attempt of a run gets its own sentinel, a near copy of the declared watermark yellow', () => {
  const colours = Array.from({ length: capture.SENTINEL_LIMIT }, (_, n) => capture.sentinelColour(n));
  assert.equal(new Set(colours).size, colours.length);
  const channel = (c, i) => parseInt(c.slice(i, i + 2), 16);
  assert.ok(colours.every(c => /^#[0-9a-f]{6}$/.test(c) && c !== '#ffdf70' && channel(c, 1) === 0xff
    && Math.abs(channel(c, 3) - 0xdf) <= 16 && Math.abs(channel(c, 5) - 0x70) <= 8), JSON.stringify(colours));
  // Every scene in every fixture language, each allowed CAPTURE_ATTEMPTS frames, never runs out of unused colours.
  const fixture = capture.loadFixture(root);
  assert.ok(capture.SENTINEL_LIMIT >= capture.captureMatrix(fixture).length * capture.CAPTURE_ATTEMPTS);
  assert.throws(() => capture.sentinelColour(capture.SENTINEL_LIMIT), /exhausted/);
});

test('capture loop keeps only a frame showing this attempt sentinel, also right after a previous scene', async () => {
  const width = 8, probe = { x: 3, y: 2, width: 8 }, painted = [];
  let repaints = 0;
  // NativeImage stand-in whose BGRA bitmap shows `colour` at the probe pixel.
  const frame = colour => {
    const bitmap = Buffer.alloc(width * 4 * 4), at = (2 * width + 3) * 4;
    [bitmap[at + 2], bitmap[at + 1], bitmap[at]] = [1, 3, 5].map(i => parseInt(colour.slice(i, i + 2), 16));
    return { colour, getScaleFactors: () => [1], getSize: () => ({ width, height: 4 }), toBitmap: () => bitmap };
  };
  // `frames` are what the hidden window hands back first; afterwards it paints the latest sentinel.
  const loop = frames => capture.captureFreshFrame({ shows: capture.nativeFrameShowsSentinel, context: { scene: 'synthetic' },
    paint: async colour => { painted.push(colour); return probe; },
    repaint: async () => { repaints++; },
    capture: async () => frames.length ? frames.shift() : frame(painted.at(-1)) });
  const first = await loop([]);
  assert.deepEqual([first.attempts, first.image.colour, repaints], [1, painted[0], 0]);
  // Next scene: the window first returns the frame the previous scene saved, then a fresh one.
  const second = await loop([first.image]);
  assert.equal(second.attempts, 2);
  assert.notEqual(second.image, first.image);
  assert.equal(second.image.colour, painted.at(-1));
  assert.deepEqual(second.sentinels, painted.slice(1));
  assert.equal(repaints, 1);
  // Only old frames: explicit failure after the limit, never a returned stale frame.
  const stale = first.image, before = painted.length;
  await assert.rejects(loop(Array(capture.CAPTURE_ATTEMPTS).fill(stale)), new RegExp('Stale frame.*"attempts":' + capture.CAPTURE_ATTEMPTS + '\\b'));
  assert.equal(painted.length - before, capture.CAPTURE_ATTEMPTS);
  assert.equal(new Set(painted).size, painted.length);
});

test('the renderer saves the PNG of the frame the real pixel check accepted, through one capture path', async () => {
  const width = 8, probe = { x: 3, y: 2, width: 8 }, painted = [], calls = [];
  let invalidations = 0;
  const frame = colour => {
    const bitmap = Buffer.alloc(width * 4 * 4), at = (2 * width + 3) * 4;
    [bitmap[at + 2], bitmap[at + 1], bitmap[at]] = [1, 3, 5].map(i => parseInt(colour.slice(i, i + 2), 16));
    return { getScaleFactors: () => [1], getSize: () => ({ width, height: 4 }), toBitmap: () => bitmap, toPNG: () => Buffer.from('png:' + colour) };
  };
  const stale = frame('#000000');
  const frames = [stale];
  // Window stand-in: first hands back an old frame, then the frame with the latest painted sentinel.
  const w = { webContents: { invalidate() { invalidations++; } },
    capturePage: async (...args) => { calls.push(args); return frames.length ? frames.shift() : frame(painted.at(-1)); } };
  const js = async (_pageFunction, colour) => { painted.push(colour); return probe; };
  const shot = await capture.sceneFrame({ w, js, context: { scene: 'synthetic' }, wait: async () => {} });
  assert.equal(shot.attempts, 2);
  assert.equal(String(shot.png), 'png:' + painted.at(-1));
  assert.notEqual(String(shot.png), String(stale.toPNG()));
  assert.equal(invalidations, 1);
  assert.deepEqual(calls, [[undefined, { stayHidden: true }], [undefined, { stayHidden: true }]]);
  // One capture path in the tool: no second capturePage or toPNG can save an unchecked frame.
  const source = fs.readFileSync(path.join(root, 'app/tools/capture-docs.cjs'), 'utf8');
  assert.equal(source.split('capturePage(').length - 1, 1, 'capturePage outside sceneFrame');
  assert.equal(source.split('.toPNG()').length - 1, 1, 'toPNG outside sceneFrame');
  assert.equal(source.split('await sceneFrame(').length - 1, 1, 'the renderer captures through sceneFrame');
});

test('tree state lists uncommitted capture inputs and a candidate refuses them before building', async () => {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'bb-capture-tree-')));
  const env = capture.childEnvironment(fs.mkdtempSync(path.join(os.tmpdir(), 'bb-capture-unit-')));
  // A throwaway repository in TEMP, never the working tree under test.
  const git = (...args) => {
    const result = spawnSync('git', ['-C', repo, '-c', 'user.name=Demo', '-c', 'user.email=demo@example.invalid', '-c', 'core.autocrlf=false', ...args], { encoding: 'utf8', env });
    assert.equal(result.status, 0, result.stderr); return result.stdout;
  };
  const write = (name, text) => { fs.mkdirSync(path.dirname(path.join(repo, name)), { recursive: true }); fs.writeFileSync(path.join(repo, name), text); };
  git('init', '-q');
  write('app/src/demo.ts', 'export const a = 1;\n'); write('app/tests/demo.cjs', '1;\n'); write('.gitignore', 'app/src/ignored.ts\n');
  git('add', '.'); git('commit', '-q', '-m', 'synthetic');
  assert.deepEqual(capture.treeState(repo, env), { head: git('rev-parse', 'HEAD').trim(), modified: [], untracked: [], clean: true });
  write('app/src/demo.ts', 'export const a = 2;\n'); write('app/src/new.ts', 'export const b = 1;\n');
  write('app/src/ignored.ts', '1;\n'); write('app/tests/demo.cjs', '2;\n'); // an ignored input and a non-input
  let tree = capture.treeState(repo, env);
  // The ignored file is in the source digest (read from disk), so it makes the tree unclean too.
  assert.deepEqual([tree.modified, tree.untracked, tree.clean], [['app/src/demo.ts'], ['app/src/ignored.ts', 'app/src/new.ts'], false]);
  git('add', 'app/src/demo.ts'); // a staged change still differs from HEAD
  tree = capture.treeState(repo, env);
  assert.deepEqual([tree.modified, tree.clean], [['app/src/demo.ts'], false]);
  // The interpreter path is only validated: the refusal comes before Python, fixture, fonts or build.
  await assert.rejects(capture.run({ candidate: true, python: process.execPath, root: repo }),
    /clean tree[\s\S]*app\/src\/demo\.ts[\s\S]*app\/src\/new\.ts/);
  await assert.rejects(capture.run({ candidate: false, python: process.execPath, root: repo }), error => !/clean tree/.test(String(error)));
});

test('integration waits longer than every subprocess budget the capture declares', () => {
  assert.deepEqual(Object.keys(capture.BUDGET_MS), ['tree', 'tsc', 'vite', 'renderer', 'verifier']);
  assert.ok(Object.values(capture.BUDGET_MS).every(ms => Number.isInteger(ms) && ms > 0));
  assert.ok(INTEGRATION_TIMEOUT_MS > Object.values(capture.BUDGET_MS).reduce((n, ms) => n + ms, 0));
  // No wait of ten seconds or more hides outside BUDGET_MS in the orchestration.
  for (const fn of [capture.run, capture.treeState, capture.verifyCandidates]) assert.equal(fn.toString().match(/\b\d{5,}\b/g), null, fn.name);
});

test('mandate scene: the unit next to a field wraps between words, never inside one', () => {
  // With overflow-wrap:anywhere the flex row shrinks the unit below its longest word ("ann|i", "year|s").
  const css = fs.readFileSync(path.join(root, 'app/src/pages/mandato.css'), 'utf8');
  const wraps = [...css.matchAll(/([^{}]+)\{([^}]*)\}/g)]
    .filter(([, selectors]) => selectors.split(',').map(s => s.trim()).includes('.mandato-input span'))
    .flatMap(([, , body]) => [...body.matchAll(/overflow-wrap\s*:\s*([a-z-]+)/g)].map(m => m[1]));
  assert.ok(wraps.length > 0 && wraps.at(-1) !== 'anywhere', JSON.stringify(wraps));
});

test('desktop synthetic services answer the scheduled-task route the Layout polls', () => {
  const read = name => fs.readFileSync(path.join(root, name), 'utf8');
  assert.match(read('app/src/components/Layout.tsx'), /Bellomberg\.scheduledTasks\(\)/);
  const route = read('app/src/lib/api.ts').match(/scheduledTasks: \(\) => api\.get<[^>]*>\('([^']+)'\)/)?.[1];
  assert.ok(route && capture.loadFixture(root).shell.map(capture.responseKey).includes('GET ' + route), String(route));
  for (const name of ['app/tests/desktop/f13.cjs', 'app/tests/desktop/f18.cjs']) {
    assert.ok(read(name).includes("route==='" + route + "'"), name + ' does not serve ' + route);
    assert.ok(!read(name).includes("'/system/tasks'"), name + ' serves a route no page calls');
  }
});

test('new synthetic payloads use DEMO identifiers, coherent journal rows and valid presentation metadata', () => {
  const fixture = capture.loadFixture(root), f = key => capture.responseBody(fixture, key);
  const progress = f('/agents/progress'), journal = f('/journal'), entry = f('/journal/1'), versions = f('/journal/1/versions');
  const tickers = [...progress.runs.flatMap(r => r.scorecard.details.map(d => d.ticker)), ...journal.items.map(i => i.ticker),
    entry.ticker, ...versions.items.map(v => v.ticker)].filter(t => t !== null);
  assert.ok(tickers.length > 0 && tickers.every(t => t.startsWith('DEMO.')), JSON.stringify(tickers));
  assert.equal(journal.items[0].id, entry.id); assert.equal(entry.version, versions.items[0].version);
  assert.equal(journal.total, journal.items.length); assert.equal(versions.total, versions.items.length);
  assert.equal(journal.items[0].excerpt, entry.body.slice(0, 180)); assert.equal(versions.items[0].body, entry.body);
  // Same invariants as app/src/lib/api-presentation.ts: one bad entry makes the page fail closed.
  for (const payload of [progress, f('/mandato')]) {
    assert.equal(payload._presentation_v1.version, 1);
    const seen = new Set();
    for (const item of payload._presentation_v1.texts) {
      const value = item.path.reduce((node, key) => {
        assert.ok(node !== null && typeof node === 'object' && Object.hasOwn(node, key), JSON.stringify(item.path));
        return node[key];
      }, payload);
      assert.ok(typeof item.it === 'string' && typeof item.en === 'string' && value === item.it, JSON.stringify(item.path));
      assert.ok(!seen.has(JSON.stringify(item.path)), JSON.stringify(item.path)); seen.add(JSON.stringify(item.path));
    }
  }
});

test('synthetic mandate is the public example profile with the public schema', () => {
  const m = capture.responseBody(capture.loadFixture(root), '/mandato');
  const example = JSON.parse(fs.readFileSync(path.join(root, 'src/bellomberg/resources/examples/mandato_pm.example.json'), 'utf8'));
  assert.equal(m.dichiarato, true); assert.equal(m.origine, 'esempio'); assert.deepEqual(m.errori, []);
  assert.deepEqual(m.campi, example._campi);
  for (const block of Object.keys(example._esempio)) {
    assert.deepEqual(m.valori[block], example._esempio[block], block);
    assert.deepEqual(m.esempio[block], example._esempio[block], block);
  }
});

test('synthetic holdings and cash reconcile to documented invented trades and deposit', () => {
  const fixture = capture.loadFixture(root), body = key => capture.responseBody(fixture, key);
  const f = { '/trades': body('/trades'), '/cash/movements': body('/cash/movements'), '/portfolio': body('/portfolio') };
  const trades = f['/trades'].trades, cash = f['/cash/movements'].movements;
  assert.equal(trades.length, f['/trades'].count);
  for (const p of f['/portfolio'].positions) {
    const rows = trades.filter(t => t.ticker === p.ticker);
    assert.equal(rows.reduce((n, t) => n + t.quantita, 0), p.quantita);
    assert.equal(rows.reduce((n, t) => n + t.quantita * t.prezzo, 0), p.costo_eur);
  }
  const balance = cash.reduce((n, c) => n + c.amount_eur, 0) - trades.reduce((n, t) => n + t.quantita * t.prezzo, 0);
  assert.equal(balance, f['/portfolio'].cash_disponibile_eur);
  assert.equal(balance + f['/portfolio'].totale_valore_mercato_eur, f['/portfolio'].nav_total_eur);
});

test('source inventory never follows a junction to an uninventoried directory', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'bb-capture-unit-'));
  const allowed = path.join(tmp, 'allowed'), outside = path.join(tmp, 'outside');
  fs.mkdirSync(allowed); fs.mkdirSync(outside); fs.writeFileSync(path.join(outside, 'private.txt'), 'SYNTHETIC tripwire');
  fs.symlinkSync(outside, path.join(allowed, 'linked'), process.platform === 'win32' ? 'junction' : 'dir');
  assert.throws(() => capture.readInventory(tmp, ['allowed']), /Linked source/);
});

test('drift guard refuses changed source, bundle or fixture before any candidate publication', () => {
  const before = { source: 'a', bundle: 'b', fixture: 'c', capture: 'd', app_version: '1' };
  capture.assertUnchanged(before, { ...before });
  for (const key of Object.keys(before)) assert.throws(() => capture.assertUnchanged(before, { ...before, [key]: 'changed' }), /drift/);
});

if (process.argv.includes('--integration')) test('real built renderer is isolated and ready in every fixture language', { timeout: INTEGRATION_TIMEOUT_MS }, async () => {
  const fixture = capture.loadFixture(root);
  const receipt = await capture.run({ candidate: false });
  assert.equal(receipt.ok, true);
  assert.equal(receipt.images_saved, 0);
  assert.deepEqual(receipt.scenes.map(s => s.language + ':' + s.id), capture.captureMatrix(fixture).map(c => c.language + ':' + c.scene.id));
  assert.ok(receipt.scenes.every(s => s.ready && s.watermark_visible && s.capture_attempts >= 1 && s.capture_attempts <= capture.CAPTURE_ATTEMPTS
    && s.sentinels.length === s.capture_attempts && s.steps.length === capture.sceneSteps(fixture.scenes.find(f => f.id === s.id)).length
    && s.charts.calls === s.charts.settled && s.charts.no_webgl === 0 && s.charts.lost_contexts === 0));
  const sentinels = receipt.scenes.flatMap(s => s.sentinels);
  assert.equal(new Set(sentinels).size, sentinels.length, 'A sentinel colour was reused across captures');
  assert.ok(Array.isArray(receipt.tree.modified) && Array.isArray(receipt.tree.untracked) && /^[0-9a-f]{40}$/.test(receipt.tree.head));
  assert.equal(receipt.unexpected_requests.length, 0);
  assert.equal(receipt.blocked.length, 0);
  assert.ok(receipt.requests.length > 20);
  // A write reached the service only as a declared response; its received body is in the receipt.
  const declared = new Set(Object.keys(fixture.responses).map(capture.responseKey));
  assert.ok(receipt.requests.every(r => ['GET', 'OPTIONS'].includes(r.method) || declared.has(r.key) && r.status === 200));
  assert.ok(receipt.network_guard_probes.every(p => p.blocked));
});
