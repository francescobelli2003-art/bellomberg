// R14 seguito (Opus 5.5, 10/10/2026): CABLAGGIO di TradeIdeaPage sullo storage non pronto.
// r01-r14-stati prova il parsing del 503 e il riquadro da soli; qui gira la PAGINA VERA con
// readTradeIdeaStorage/storageOf veri, dizionari veri, fetch finta per rotta e intervalli
// catturati (il polling si fa scattare a mano). Si stubbano solo rete, router, preferiti e
// ConfirmDialog (portal): il suo onConfirm si legge dalle props catturate e si chiama.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const jsxRuntime = require('react/jsx-runtime');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');
ambienteBrowser();

// Hook trattenuti per istanza di pagina: un solo caricatore (trascompila una volta), stato in `cur`.
let cur = null;
const remember = (fn, d) => {
  const at = cur.mi++, before = cur.memos[at];
  if (!before || !d || d.some((v, i) => !Object.is(v, before.d[i]))) cur.memos[at] = { d, value: fn() };
  return cur.memos[at].value;
};
const capture = fn => (type, props, key) => { if (cur && props) cur.elements.push({ type, props }); return fn(type, props, key); };
const load = creaCaricatore({ stub: {
  react: { ...React, useMemo: remember, useCallback: (fn, d) => remember(() => fn, d),
    useRef(initial) { const at = cur.ri++; if (!cur.refs[at]) cur.refs[at] = { current: initial }; return cur.refs[at]; },
    useState(initial) {
      const at = cur.si++, values = cur.values;
      if (!(at in values)) values[at] = typeof initial === 'function' ? initial() : initial;
      return [values[at], v => { values[at] = typeof v === 'function' ? v(values[at]) : v; }];
    },
    useEffect(fn, d) { const at = cur.ei++, before = cur.deps[at]; if (!before || !d || d.some((v, i) => !Object.is(v, before[i]))) cur.effects.push(fn); cur.deps[at] = d; },
  },
  'react/jsx-runtime': { ...jsxRuntime, jsx: capture(jsxRuntime.jsx), jsxs: capture(jsxRuntime.jsxs) },
  'react-router-dom': { useNavigate: () => () => {}, useLocation: () => ({ state: null }),
    useSearchParams: () => [new URLSearchParams(cur.search), () => {}], Link: props => React.createElement('a', null, props.children) },
  '@/components/ConfirmDialog': () => null,
  '@/lib/api': { Bellomberg: { favorites: async () => ({ favorites: [] }) } },
  './api': { API_BASE: 'http://synthetic.invalid', requestHeaders: () => ({}), clearSessionAndReload: () => { throw new Error('unexpected session clear'); } },
} });
const language = load('i18n/lingua.ts');
const Page = load('pages/TradeIdeaPage.tsx').default;
const { researchArtifactBlob } = load('lib/tradeIdeaResearch.ts');
const { TradeIdeaApiError } = load('lib/tradeIdeas.ts');
global.requestAnimationFrame = () => 0;

function page(routes, search = '') {
  const state = { si: 0, mi: 0, ei: 0, ri: 0, values: {}, memos: [], effects: [], deps: [], refs: [], elements: [], search,
    calls: [], intervals: [] };
  global.setInterval = fn => { if (cur === state) state.intervals.push(fn); return state.intervals.length; };
  global.clearInterval = () => {};
  global.fetch = async (url, options = {}) => {
    const call = (options.method || 'GET') + ' ' + String(url).replace('http://synthetic.invalid/trade-ideas', '').replace('http://synthetic.invalid', '');
    state.calls.push(call);
    for (const [re, reply] of routes) if (re.test(call)) return reply(call);
    return new Response(JSON.stringify({ detail: 'unrouted ' + call }), { status: 404 });
  };
  cur = state;
  const render = (lang = 'it') => {
    cur = state; state.si = state.mi = state.ei = state.ri = 0; state.effects.length = 0; state.elements.length = 0;
    language.impostaLinguaCorrente(lang);
    return renderToStaticMarkup(React.createElement(Page));
  };
  const flush = async () => { for (let i = 0; i < 6; i++) await new Promise(r => setImmediate(r)); };
  const settle = async (lang = 'it') => { for (let i = 0; i < 4; i++) { render(lang); for (const fn of [...state.effects]) fn(); await flush(); } return render(lang); };
  const tick = async () => { for (const fn of [...state.intervals]) fn(); await flush(); };
  const find = pred => { const hit = state.elements.find(e => pred(e.props, e.type)); assert.ok(hit, 'element not rendered'); return hit.props; };
  const act = async (fn, lang = 'it') => { await fn(); await flush(); return settle(lang); };
  return { render, settle, tick, find, act, calls: state.calls };
}

const storage = status => ({ status, error_code: 'trade_idea_' + status,
  update_required: status === 'schema_absent' || status === 'schema_partial',
  action: { db_missing: 'check_database_path', schema_partial: 'run_explicit_migration', schema_absent: 'run_explicit_migration',
    schema_incompatible: 'inspect_schema', db_unreadable: 'check_database_access' }[status],
  documentation: 'docs/TRADE_IDEA.md#aggiornamento-storage' });
const json = (status, body) => () => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const RAW = 'Trade Idea storage non pronto: synthetic';
const s503 = st => json(503, { detail: RAW + ' ' + st, error_code: 'trade_idea_' + st, storage: storage(st) });
const html503 = () => new Response('<html>Bad gateway</html>', { status: 503, headers: { 'Content-Type': 'text/html' } });
const box = (html, status) => (html.match(new RegExp(`data-ti-storage="${status}"`, 'g')) || []).length;
const launchDisabled = html => /<div class="ti-launch">.*?<button class="ti-button ti-primary" disabled=""/s.test(html);
const RUN = { id: 'RUN1', ticker: 'SYN', status: 'running' };
const detailOf = () => json(200, { run: RUN, events: [], artifacts: [] });

test('initial 503 storage on /active and /runs: one notice per surface with its own remedy, IT and EN, no raw detail', async () => {
  const p = page([[/^GET \/active/, s503('db_missing')], [/^GET \/runs/, s503('schema_partial')]]);
  for (const [lang, unavailable, history, migrate] of [
    ['it', 'Stato run attiva non verificabile', 'Storico non disponibile: Archivio Trade Idea non pronto', /migrazione esplicita/],
    ['en', 'Active run status unavailable', 'History unavailable', /explicit migration/]]) {
    const out = await p.settle(lang);
    assert.equal(box(out, 'db_missing'), 1, lang + ': /active 503 must render its storage notice');
    assert.equal(box(out, 'schema_partial'), 1, lang + ': /runs 503 must render its storage notice, not the raw text');
    assert.ok(out.includes(unavailable), lang);
    assert.ok(out.includes(history), lang);
    assert.match(out, migrate, 'schema_partial (run_explicit_migration + update_required) proposes the migration');
    assert.match(out, /trade_idea_db_missing/);
    assert.doesNotMatch(out, new RegExp(RAW), lang + ': the raw backend detail is replaced by the notice');
    assert.doesNotMatch(out, lang === 'it' ? /È già in corso una Trade Idea/ : /already running/);
  }
});

test('503 without JSON (HTML proxy page) on /active: generic unavailable notice, never a storage notice nor «no active run»', async () => {
  const p = page([[/^GET \/active/, html503], [/^GET \/runs/, json(200, { runs: [], total: 0 })]]);
  const out = await p.settle('it');
  assert.match(out, /Stato run attiva non verificabile: HTTP 503/);
  assert.doesNotMatch(out, /data-ti-storage/);
});

test('detail 503 storage: the detail surface renders the notice with its lead', async () => {
  const p = page([[/^GET \/active/, json(200, { run_id: null })], [/^GET \/runs\/RUN1$/, s503('db_unreadable')],
    [/^GET \/runs/, json(200, { runs: [RUN], total: 1 })]], 'run=RUN1');
  const it = await p.settle('it');
  assert.equal(box(it, 'db_unreadable'), 1);
  assert.match(it, /Dettaglio non disponibile: Archivio Trade Idea non pronto/);
  assert.match(it, /blocchi di altri programmi/);
  assert.doesNotMatch(it, /migrazione esplicita/);
  const en = p.render('en');
  assert.match(en, /locks held by other programs/);
  assert.doesNotMatch(en, new RegExp(RAW));
});

test('H4 polling: /active 200 RUN1 then 503 -> last known run, never «already running» in the present, launch stays blocked', async () => {
  let n = 0;
  const p = page([[/^GET \/active/, c => (n++ === 0 ? json(200, { run_id: 'RUN1' }) : s503('db_unreadable'))(c)],
    [/^GET \/runs\/RUN1$/, detailOf()], [/^GET \/runs/, json(200, { runs: [RUN], total: 1 })]], 'run=RUN1');
  const before = await p.settle('it');
  assert.match(before, /È già in corso una Trade Idea/, 'verified active run is stated in the present');
  assert.doesNotMatch(before, /Stato run attiva non verificabile/);
  await p.tick();
  for (const [lang, lastKnown, present, unavailable] of [
    ['it', 'Ultima run attiva nota: RUN1', /È già in corso una Trade Idea|Apri run attiva/, 'Stato run attiva non verificabile'],
    ['en', 'Last known active run: RUN1', /already running|Open active run/, 'Active run status unavailable']]) {
    const out = await p.settle(lang);
    assert.ok(out.includes(unavailable), lang + ': polling 503 on /active must be declared');
    assert.equal(box(out, 'db_unreadable'), 1, lang + ': the polling 503 keeps its storage diagnosis');
    assert.ok(out.includes(lastKnown), lang);
    assert.doesNotMatch(out, present, lang + ': an unverifiable active run is not «running» now');
    assert.ok(launchDisabled(out), lang + ': launch stays blocked while the last known run is unverified');
  }
  assert.ok(p.calls.filter(c => c === 'GET /active').length >= 2, 'the polling really re-read /active');
});

test('preflight 200 ok:false with storage renders the notice next to the reasons; preflight 503 storage uses the notice too', async () => {
  const pf = { ok: false, ticker: 'SYN', identity: { ticker: 'SYN' }, reasons: ['synthetic reason: storage not ready'],
    storage: storage('schema_partial'), models: [{ role: 'committee', model: 'm' }],
    source_qualification: { status: 'blocked', execution_status: 'not_run', reasons: [], fingerprint: 'f' } };
  let reply = json(200, pf);
  const p = page([[/^GET \/active/, json(200, { run_id: null })], [/^GET \/runs/, json(200, { runs: [], total: 0 })],
    [/^POST \/preflight/, c => reply(c)]], 'ticker=SYN');
  await p.settle('it');
  const verify = async () => {
    p.find((props, type) => type === 'input' && props.id === 'ti-budget').onChange({ target: { value: '5' } });
    await p.settle('it');
    return p.act(() => p.find(props => typeof props.className === 'string' && props.className.includes('ti-verify')).onClick());
  };
  const it = await verify();
  assert.ok(p.calls.includes('POST /preflight'), 'the verify button really ran the preflight');
  assert.equal(box(it, 'schema_partial'), 1, 'preflight.storage must render the notice');
  assert.match(it, /migrazione esplicita/);
  assert.match(it, /synthetic reason: storage not ready/);
  assert.ok(launchDisabled(it));
  assert.match(p.render('en'), /explicit migration/);
  reply = s503('db_missing');
  const failed = await verify();
  assert.equal(box(failed, 'db_missing'), 1);
  assert.match(failed, /Preflight non disponibile: Archivio Trade Idea non pronto/);
  assert.doesNotMatch(failed, /migrazione esplicita/);
  assert.doesNotMatch(failed, new RegExp(RAW));
});

test('stop, email retry and launch 503 storage show the notice, not the raw detail', async () => {
  const pfOk = { ok: true, ticker: 'SYN', identity: { ticker: 'SYN' }, reasons: [], models: [{ role: 'committee', model: 'm' }],
    source_qualification: { status: 'qualified', reasons: [], fingerprint: 'f' } };
  const p = page([[/^GET \/active/, json(200, { run_id: null })],
    [/^POST \/runs\/RUN1\/stop$/, s503('db_unreadable')], [/^POST \/runs\/RUN1\/email\/retry$/, s503('schema_incompatible')],
    // dettaglio con email fallita: il bottone di reinvio esiste
    [/^GET \/runs\/RUN1$/, json(200, { run: RUN, events: [], artifacts: [], email: { status: 'failed', error: 'synthetic smtp' } })],
    [/^POST \/runs$/, s503('db_missing')],
    [/^GET \/runs/, json(200, { runs: [RUN], total: 1 })], [/^POST \/preflight/, json(200, pfOk)]], 'run=RUN1&ticker=SYN');
  await p.settle('it');
  let out = await p.act(() => p.find((props, type) => type !== 'button' && props.tone === 'crimson').onConfirm());
  assert.ok(p.calls.includes('POST /runs/RUN1/stop'));
  assert.equal(box(out, 'db_unreadable'), 1, 'stop 503 storage');
  assert.match(out, /Stop non confermato: Archivio Trade Idea non pronto/);
  out = await p.act(() => p.find((props, type) => type === 'button' && props.className === 'ti-button ti-primary' && typeof props.onClick === 'function'
    && props.children === 'Ritenta invio email').onClick());
  assert.ok(p.calls.includes('POST /runs/RUN1/email/retry'));
  assert.equal(box(out, 'schema_incompatible'), 1, 'email retry 503 storage');
  assert.match(out, /Reinvio non confermato: Archivio Trade Idea non pronto/);
  p.find((props, type) => type === 'input' && props.id === 'ti-budget').onChange({ target: { value: '5' } });
  await p.settle('it');
  await p.act(() => p.find(props => typeof props.className === 'string' && props.className.includes('ti-verify')).onClick());
  out = await p.act(() => p.find(props => props.title === 'Conferma avvio Trade Idea' && typeof props.onConfirm === 'function').onConfirm());
  assert.ok(p.calls.includes('POST /runs'), 'the launch really posted');
  assert.equal(box(out, 'db_missing'), 1, 'launch 503 storage');
  assert.match(out, /Avvio non confermato: Archivio Trade Idea non pronto/);
  assert.doesNotMatch(out, new RegExp(RAW));
});

test('researchArtifactBlob keeps the storage diagnosis of a 503', async () => {
  const before = global.fetch;
  global.fetch = async () => s503('schema_absent')();
  try {
    const error = await researchArtifactBlob({ artifact: { download_url: '/trade-ideas/research/x/artifact' } }).then(() => null, e => e);
    assert.ok(error instanceof TradeIdeaApiError);
    assert.equal(error.status, 503);
    assert.equal(error.errorCode, 'trade_idea_schema_absent');
    assert.deepEqual(error.storage, storage('schema_absent'));
  } finally { global.fetch = before; }
});

// ---------- R14 seguito v3 (Opus 5.5, 10/10/2026) ----------

const PF_OK = { ok: true, ticker: 'SYN', identity: { ticker: 'SYN' }, reasons: [], models: [{ role: 'committee', model: 'm' }],
  source_qualification: { status: 'qualified', reasons: [], fingerprint: 'f' } };
const verifyReady = async p => {
  await p.settle('it');
  p.find((props, type) => type === 'input' && props.id === 'ti-budget').onChange({ target: { value: '5' } });
  await p.settle('it');
  return p.act(() => p.find(props => typeof props.className === 'string' && props.className.includes('ti-verify')).onClick());
};

test('safety: /active unreadable and NO known run -> launch blocked with a clear reason and «Retry check»; a good re-read unblocks', async () => {
  for (const failing of [s503('db_unreadable'), html503]) {
    let activeOk = false;
    const p = page([[/^GET \/active/, c => (activeOk ? json(200, { run_id: null }) : failing)(c)],
      [/^GET \/runs/, json(200, { runs: [], total: 0 })], [/^POST \/preflight/, json(200, PF_OK)], [/^POST \/runs$/, json(202, { run_id: 'NEW' })]], 'ticker=SYN');
    const blocked = await verifyReady(p);
    assert.ok(p.calls.includes('POST /preflight'), 'fixture: a valid preflight is current');
    assert.ok(launchDisabled(blocked), 'an unverifiable active-run status must block a paid launch');
    assert.match(blocked, /data-ti-launch-blocked="active-unverified"/);
    assert.match(blocked, /Avvio bloccato: lo stato della run attiva non è verificabile/);
    assert.match(blocked, /Riprova verifica/);
    assert.doesNotMatch(blocked, /Ultima run attiva nota/, 'no run was ever known: nothing is invented');
    const en = p.render('en');
    assert.match(en, /Launch blocked: the active run status cannot be verified/);
    assert.match(en, /Retry check/);
    // il bottone di conferma, se arrivasse lo stesso, non avvia: start() rispetta canLaunch
    p.render('it');
    await p.act(() => p.find(props => props.title === 'Conferma avvio Trade Idea' && typeof props.onConfirm === 'function').onConfirm());
    assert.ok(!p.calls.includes('POST /runs'), 'no paid run is posted while the active status is unverified');
    // «Riprova verifica» rilegge davvero /active; letto (nessuna run attiva) l'avvio si sblocca
    const reads = p.calls.filter(c => c === 'GET /active').length;
    activeOk = true;
    const after = await p.act(() => p.find((props, type) => type === 'button' && typeof props.onClick === 'function'
      && [].concat(props.children).some(child => child === 'Riprova verifica')).onClick());
    assert.equal(p.calls.filter(c => c === 'GET /active').length, reads + 1, 'retry re-reads /active');
    assert.ok(!launchDisabled(after), 'a verified «no active run» unblocks the launch');
    assert.doesNotMatch(after, /data-ti-launch-blocked/);
  }
});

test('saved run: execution_status unknown_legacy is «historical data not recorded», never completed nor failed; completed stays completed', async () => {
  const detailWith = execution_status => json(200, { run: { ...RUN, status: 'completed',
    source_qualification: { status: 'qualified', execution_status } }, events: [], artifacts: [] });
  let current = detailWith('unknown_legacy');
  const p = page([[/^GET \/active/, json(200, { run_id: null })], [/^GET \/runs\/RUN1$/, c => current(c)],
    [/^GET \/runs/, json(200, { runs: [RUN], total: 1 })]], 'run=RUN1');
  const it = await p.settle('it');
  assert.match(it, /data-ti-source-check="unknown_legacy"/);
  assert.match(it, /<span>Verifica fonti<\/span><strong>dato storico non registrato<\/strong>/);
  const en = p.render('en');
  assert.match(en, /<span>Source check<\/span><strong>historical data not recorded<\/strong>/);
  for (const out of [it, en]) {
    const cell = out.match(/data-ti-source-check="unknown_legacy">(.*?)<\/div>/)[1];
    assert.doesNotMatch(cell, /completata|eseguita|non riuscita|fallita|completed|executed|failed/i);
  }
  current = detailWith('completed');
  await p.tick();
  const done = await p.settle('it');
  assert.match(done, /<span>Verifica fonti<\/span><strong>completata<\/strong>/);
  assert.doesNotMatch(done, /dato storico non registrato/);
});
