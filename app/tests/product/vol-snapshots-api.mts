// Istantanee della superficie dal backend (10/10/2026, Opus 5.5): client puro, richiesta iniettata.
// Oracolo scritto qui (id, orari, percorsi); ticker sintetici ZZ*, mai valori del book.
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  isSnapshotId, listBackendSnapshots, loadBackendSnapshot, parseSnapshotList, previousBackendSnapshot, surfaceOfRecord,
  type VolRequest,
} from '../../src/lib/vol-snapshots-api.ts';

const meta = (ticker: string, stamp: string, hash: string, at: string, extra: Record<string, unknown> = {}) => ({
  id: `${ticker}~${stamp}_${hash}`, ticker, snapshot_at: at, saved_at: at, spot: 113, spot_source: 'Polygon underlying snapshot',
  spot_qualified: true, iv_grid_qualified: true, expiries: ['2031-04-17'], surface_expiries: ['2031-04-17'],
  n_expiries: 1, n_contracts: 37, n_slices: 1, surface_error: null, data_delay: 'DELAYED', download_complete: true,
  source: 'Polygon option-chain snapshot', size_bytes: 4813, index: 'meta', ...extra,
});
const A = meta('ZZVA', '20310304T161700Z', '00000000000000a1', '2031-03-04T16:17:00+00:00');
const B = meta('ZZVA', '20310311T151900Z', '00000000000000b2', '2031-03-11T15:19:00+00:00');
const C = meta('ZZVA', '20310313T194100Z', '00000000000000c3', '2031-03-13T19:41:00+00:00');

function fakeRequest(responses: Record<string, unknown>, seen: string[]): VolRequest {
  return (async (path: string) => {
    seen.push(path);
    if (!(path in responses)) throw new Error(`unexpected ${path}`);
    return responses[path];
  }) as VolRequest;
}

test('the list asks the right path, keeps newest first and declares malformed entries', async () => {
  const seen: string[] = [];
  const bad = { ...B, id: '../x' };
  const req = fakeRequest({ '/options/snapshots?ticker=ZZVA': {
    ticker: 'ZZVA', snapshots: [A, C, bad, B], errors: [{ id: 'ZZVA~x', error: 'illeggibile (format)' }],
    retention: { days: 90, max_per_ticker: 120, error: null } } }, seen);
  const out = await listBackendSnapshots(' zzva ', req);
  assert.deepEqual(seen, ['/options/snapshots?ticker=ZZVA']);
  assert.deepEqual(out.snapshots.map(s => s.id), [C.id, B.id, A.id]);
  assert.equal(out.errors.length, 2, 'backend error + client-rejected entry, never silently dropped');
  assert.equal(out.errors[1].id, '../x');
  assert.deepEqual(out.retention, { days: 90, max_per_ticker: 120, error: null });
});

test('a row of another ticker never enters the comparison', async () => {
  const other = meta('ZZVB', '20310312T151900Z', '00000000000000d4', '2031-03-12T15:19:00+00:00');
  const out = await listBackendSnapshots('ZZVA', fakeRequest({ '/options/snapshots?ticker=ZZVA': { snapshots: [A, other] } }, []));
  assert.deepEqual(out.snapshots.map(s => s.ticker), ['ZZVA']);
  assert.match(out.errors[0].error, /other ticker/);
});

test('previous = latest strictly earlier, never the same snapshot, none without a time', () => {
  const list = parseSnapshotList({ snapshots: [A, B, C] }).snapshots;
  assert.equal(previousBackendSnapshot(list, C.snapshot_at)?.id, B.id);
  assert.equal(previousBackendSnapshot(list, B.snapshot_at)?.id, A.id, 'equal time is not "previous"');
  assert.equal(previousBackendSnapshot(list, A.snapshot_at), null);
  assert.equal(previousBackendSnapshot(list, '2031-03-20T00:00:00Z', C.id)?.id, B.id, 'the shown snapshot is excluded by id');
  assert.equal(previousBackendSnapshot(list, null), null);
  assert.equal(previousBackendSnapshot(list, 'ieri'), null);
});

test('load validates the id before asking and the payload after', async () => {
  const seen: string[] = [];
  const rec = { id: B.id, ticker: 'ZZVA', snapshot_at: B.snapshot_at, saved_at: B.saved_at, content_hash: '00000000000000b2',
    provenance: { data_delay: 'DELAYED' }, summary: {}, chain_status: {},
    surface: { slices: [{ expiry: '2031-04-17', days: 37, t_years: .1183, iv_grid: [.31, .27, null] }], moneyness_grid: [.9, 1, 1.1], spot_est: 113 },
    chains: { '2031-04-17': [{ contract: 'O:ZZVA', strike: 109, type: 'call' }] }, size_bytes: 4813 };
  const req = fakeRequest({ [`/options/snapshots/${encodeURIComponent(B.id)}`]: rec,
    [`/options/snapshots/${encodeURIComponent(C.id)}`]: { ...rec, id: A.id } }, seen);
  const out = await loadBackendSnapshot(B.id, req);
  assert.equal(out.surface.slices[0].t_years, .1183);
  const s = surfaceOfRecord(out);
  assert.equal(s.snapshot_at, B.snapshot_at); assert.equal(s.archive_id, B.id); assert.equal(s.spot_est, 113);
  await assert.rejects(loadBackendSnapshot('../../consigliere.db', req), /Invalid snapshot id/);
  await assert.rejects(loadBackendSnapshot(C.id, req), /Unreadable snapshot/, 'a different id in the answer is refused');
  assert.equal(seen.length, 2, 'the invalid id never reached the network');
  assert.ok(isSnapshotId(A.id)); assert.ok(!isSnapshotId('ZZVA~20310304T161700Z_zz'));
});

test('backend failure is not swallowed', async () => {
  const req = (async () => { throw new Error('HTTP 500'); }) as VolRequest;
  await assert.rejects(listBackendSnapshots('ZZVA', req), /HTTP 500/);
  await assert.rejects(listBackendSnapshots('ZZVA', fakeRequest({ '/options/snapshots?ticker=ZZVA': { nope: 1 } }, [])), /Unreadable snapshot list/);
});

test('a deduplicated download never compares with its own content (archive.id of the job)', () => {
  // giovedi' (a1) e venerdi' (b2) archiviati; sabato lo stesso contenuto di venerdi':
  // il backend risponde archive {status:'duplicate', id: venerdi'} e snapshot_at = sabato.
  const thu = meta('ZZVA', '20311009T200000Z', '00000000000000a1', '2031-10-09T20:00:00+00:00');
  const fri = meta('ZZVA', '20311010T203000Z', '00000000000000b2', '2031-10-10T20:30:00+00:00');
  const list = parseSnapshotList({ snapshots: [thu, fri] }).snapshots;
  const sat = '2031-10-11T15:00:00+00:00';
  assert.equal(previousBackendSnapshot(list, sat, fri.id)?.id, thu.id);
  // stesso hash con un altro orario (id diverso): escluso lo stesso, per contenuto
  assert.equal(previousBackendSnapshot(list, sat, 'ZZVA~20311011T150000Z_00000000000000b2')?.id, thu.id);
  // un id che non e' d'archivio (uuid del download) non esclude nulla: il chiamante deve passare archive.id
  assert.equal(previousBackendSnapshot(list, sat, 'a3f9c0de')?.id, fri.id);
});
