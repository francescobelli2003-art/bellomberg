const { test } = require('node:test');
const assert = require('node:assert/strict');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');
ambienteBrowser();
const load = creaCaricatore({ stub: { '@/lib/api': {
  API_BASE: 'http://synthetic.invalid', requestHeaders: () => ({ 'X-BB-Token': 'synthetic-session' }),
} } });
const { currentWorkbook, historicalWorkbook, requestModelRefresh, lockModel, createPersonalVariant, personalVariants, personalWorkbook } = load('lib/valuation-download.ts');
const model = { ticker: 'SYNTH.A', generation_id: 'synthetic-generation', current_generation: true,
  current_download: '/valuation/models/SYNTH.A/generations/synthetic-generation/workbook' };

test('current workbook sends the session header and verifies the returned generation', async () => {
  const bytes = new Uint8Array([80, 75, 3, 4]);
  const blob = await currentWorkbook(model, async (url, options) => {
    assert.equal(url, 'http://synthetic.invalid' + model.current_download);
    assert.equal(options.headers['X-BB-Token'], 'synthetic-session');
    assert.equal(options.cache, 'no-store');
    return new Response(bytes, { headers: { 'X-Valuation-Generation': model.generation_id } });
  });
  assert.deepEqual(new Uint8Array(await blob.arrayBuffer()), bytes);
});

test('superseded or damaged workbook errors never fall back to a filename download', async () => {
  for (const status of [401, 409, 503]) {
    let calls = 0;
    await assert.rejects(currentWorkbook(model, async () => {
      calls++;
      return new Response(JSON.stringify({ detail: { message: 'synthetic refusal' } }), { status });
    }), /synthetic refusal/);
    assert.equal(calls, 1);
  }
  await assert.rejects(currentWorkbook(model, async () => new Response('other generation', {
    headers: { 'X-Valuation-Generation': 'different' },
  })));
});

test('unpublished models and foreign download paths cannot receive the session token', async () => {
  for (const patch of [{ current_generation: false }, { current_download: 'https://external.invalid' }]) {
    await assert.rejects(currentWorkbook({ ...model, ...patch }, async () => assert.fail('unexpected request')));
  }
});

test('explicit model actions send session and exact generation without AI or path fields', async () => {
  const calls = [];
  const fetcher = async (url, options) => {
    calls.push({ url, ...options });
    return new Response(JSON.stringify({ id: 17, status: 'queued' }));
  };
  await requestModelRefresh('SYNTH.A', 'stable-request', fetcher);
  await requestModelRefresh('SYNTH.A', 'stable-request', fetcher);
  await lockModel('SYNTH.A', 'generation', true, fetcher);
  await createPersonalVariant('SYNTH.A', 'generation', 'Personal thesis', 'copy-request', fetcher);
  assert.deepEqual(JSON.parse(calls[0].body), { request_id: 'stable-request' });
  assert.equal(calls[0].body, calls[1].body);
  assert.deepEqual(JSON.parse(calls[2].body), { generation_id: 'generation', locked: true });
  assert.deepEqual(JSON.parse(calls[3].body), { generation_id: 'generation', label: 'Personal thesis', request_id: 'copy-request' });
  for (const call of calls) {
    assert.equal(call.method, 'POST');
    assert.equal(call.headers['X-BB-Token'], 'synthetic-session');
    assert.equal(call.cache, 'no-store');
    assert.ok(call.url.startsWith('http://synthetic.invalid/valuation/models/SYNTH.A/'));
  }
});

test('action refusal is surfaced with no automatic second request', async () => {
  let calls = 0;
  await assert.rejects(requestModelRefresh('SYNTH.A', 'request', async () => {
    calls++;
    return new Response(JSON.stringify({ detail: { message: 'local authorization absent' } }), { status: 403 });
  }), /local authorization absent/);
  assert.equal(calls, 1);
});

test('personal copy read is session protected and verifies its original generation', async () => {
  const variant = { id: 'copy', ticker: 'SYNTH.A', source_generation: 'original', available: true };
  const rows = await personalVariants('SYNTH.A', async (_url, options) => {
    assert.equal(options.method, 'GET');
    return new Response(JSON.stringify({ variants: [variant] }));
  });
  const blob = await personalWorkbook(rows[0], async (url, options) => {
    assert.equal(url, 'http://synthetic.invalid/valuation/models/SYNTH.A/variants/copy/workbook');
    assert.equal(options.headers['X-BB-Token'], 'synthetic-session');
    return new Response('personal edit', { headers: { 'X-Valuation-Generation': 'original' } });
  });
  assert.equal(await blob.text(), 'personal edit');
  await assert.rejects(personalWorkbook(variant, async () => new Response('mismatch')));
});

test('historical downloads require the session and explicit dated-copy response', async () => {
  const file = { file: 'VAL_OLD.xlsx' };
  const blob = await historicalWorkbook(file, async (url, options) => {
    assert.equal(url, 'http://synthetic.invalid/fundamentals/models/VAL_OLD.xlsx/download');
    assert.equal(options.headers['X-BB-Token'], 'synthetic-session');
    return new Response('dated copy', { headers: { 'X-Valuation-Copy': 'historical' } });
  });
  assert.equal(await blob.text(), 'dated copy');
  await assert.rejects(historicalWorkbook(file, async () => new Response('unlabelled')));
});
