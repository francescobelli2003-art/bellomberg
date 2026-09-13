// Standalone, offline guards. Add --integration for the real hidden renderer/build.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const capture = require('../../tools/capture-docs.cjs');
const root = path.resolve(__dirname, '../../..');

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

test('readiness needs settled fonts/images, visible watermark, route and correct language', () => {
  const good = { route: '/trades', language: 'en', ready: true, fonts: true, images: true,
    watermark: true, content: true, busy: false, error: false };
  assert.equal(capture.readyState(good, { route: '/trades', language: 'en' }), true);
  for (const key of ['ready', 'fonts', 'images', 'watermark', 'content']) assert.equal(capture.readyState({ ...good, [key]: false }, good), false, key);
  for (const change of [{ busy: true }, { error: true }, { route: '/dashboard' }, { language: 'it' }]) assert.equal(capture.readyState({ ...good, ...change }, good), false);
});

test('fixture has finite synthetic values, exhaustive read-only routes and no arbitrary route', () => {
  const fixture = capture.loadFixture(root);
  assert.equal(fixture.id, 'documentation-demo');
  assert.deepEqual(fixture.scenes.map(s => s.route), ['/dashboard', '/trades']);
  assert.equal(capture.fixtureResponse(fixture, 'GET', '/portfolio', 'en').status, 200);
  assert.equal(capture.fixtureResponse(fixture, 'GET', '/preferences', 'en').body.language, 'en');
  for (const [method, route] of [['POST', '/prices/update'], ['GET', '/unexpected'], ['OPTIONS', '/unexpected'], ['PUT', '/preferences'], ['POST', '/trade']]) {
    assert.equal(capture.fixtureResponse(fixture, method, route, 'en').status, 409);
  }
});

test('synthetic holdings and cash reconcile to documented invented trades and deposit', () => {
  const f = capture.loadFixture(root).responses;
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

if (process.argv.includes('--integration')) test('real built renderer is isolated and ready in both languages', { timeout: 300000 }, async () => {
  const receipt = await capture.run({ candidate: false });
  assert.equal(receipt.ok, true);
  assert.equal(receipt.images_saved, 0);
  assert.equal(receipt.scenes.length, 4);
  assert.ok(receipt.scenes.every(s => s.ready && s.watermark_visible));
  assert.equal(receipt.unexpected_requests.length, 0);
  assert.equal(receipt.blocked.length, 0);
  assert.ok(receipt.requests.length > 20);
  assert.ok(receipt.requests.every(r => ['GET', 'OPTIONS'].includes(r.method)));
  assert.ok(receipt.network_guard_probes.every(p => p.blocked));
});
