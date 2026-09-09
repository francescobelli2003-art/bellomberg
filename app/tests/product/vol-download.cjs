const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const scope = { exports: {}, setTimeout, clearTimeout, DOMException, require: () => ({}) };
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.resolve(__dirname, '../../src/lib/vol-deck.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, scope);
const { watchDownload } = scope.exports;
const state = (name, count = 0) => ({ id: 'job-a', ticker: 'DEMO', state: name, n_contracts: count });

test('download polling reports progress and stops at a recoverable failure without losing rows', async () => {
  const replies = [state('running', 250), state('error', 500)];
  const received = [];
  const result = await watchDownload('job-a', 'DEMO', async () => replies.shift(), s => received.push(s), new AbortController().signal, 0);
  assert.equal(result.state, 'error');
  assert.deepEqual(received.map(x => x.n_contracts), [250, 500]);
  assert.equal(replies.length, 0);
});
test('download polling rejects results from another ticker or job', async () => {
  for (const patch of [{ ticker: 'ELSE' }, { id: 'job-b' }]) {
    let rendered = false;
    await assert.rejects(watchDownload('job-a', 'DEMO', async () => ({ ...state('complete'), ...patch }), () => { rendered = true; }, new AbortController().signal, 0), /identità/);
    assert.equal(rendered, false);
  }
});
test('cancel during an in-flight status read cannot paint the old ticker', async () => {
  const controller = new AbortController();
  let release, rendered = false;
  const pending = watchDownload('job-a', 'DEMO', () => new Promise(resolve => { release = resolve; }), () => { rendered = true; }, controller.signal, 0);
  controller.abort(); release(state('complete'));
  await assert.rejects(pending, error => error.name === 'AbortError');
  assert.equal(rendered, false);
});
test('pause ends polling, resume can continue the same preserved job to completion', async () => {
  const controller = new AbortController();
  const paused = await watchDownload('job-a', 'DEMO', async () => state('paused', 750), () => {}, controller.signal, 0);
  assert.equal(paused.n_contracts, 750);
  const complete = await watchDownload('job-a', 'DEMO', async () => state('complete', 1020), () => {}, controller.signal, 0);
  assert.equal(complete.n_contracts, 1020);
});
test('expiry horizon includes same-day contracts and every date through the chosen boundary', () => {
  const dates = Array.from({length: 15}, (_, i) => `2035-05-${String(i + 1).padStart(2, '0')}`);
  assert.deepEqual(Array.from(scope.exports.expiriesThrough(dates, '2035-05-12')), dates.slice(0, 12));
  assert.deepEqual(Array.from(scope.exports.expiriesThrough(dates, '')), dates);
  assert.equal(scope.exports.expiriesThrough(dates, '2035-04-30').length, 0);
});
test('month shortcuts use calendar months, including month-end clamping', () => {
  assert.equal(scope.exports.horizonDate(1, new Date('2035-01-31T12:00:00Z')), '2035-02-28');
  assert.equal(scope.exports.horizonDate(12, new Date('2035-05-04T12:00:00Z')), '2036-05-04');
});
test('days-to-expiry uses the local calendar day shared with the local backend at midnight', () => {
  const nearMidnight = { getFullYear: () => 2035, getMonth: () => 8, getDate: () => 10,
                        getUTCFullYear: () => 2035, getUTCMonth: () => 8, getUTCDate: () => 9 };
  assert.equal(scope.exports.daysToExpiry('2035-09-10', nearMidnight), 0);
});
