const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const JSX = require('react/jsx-runtime');
const { renderToStaticMarkup } = require('react-dom/server');
const fs = require('node:fs');
const path = require('node:path');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');
ambienteBrowser();

const full = { ticker: 'SYNTH.X', status: 'ready', reason: null,
  profile: { ticker: 'SYNTH.X', profile: { ticker: 'SYNTH.X' }, version: 1, enabled: false, interval_hours: 24, qualitative_enabled: false },
  runs: [{ id: 7, ticker: 'SYNTH.X', status: 'completed', started_at: '2026-09-20T12:00:00Z', trigger: 'manual' }], active_run: null };
const citation = { sezione: 'Risk', testo: '<script>alert(1)</script> Original quoted risk',
  url: 'https://example.org/report.pdf', sha256: 'abc123', pagine_fisiche: [4], inizio: 22, fine: 61 };
const detail = { ...full.runs[0], result: {
  stato: 'parziale', motivi: ['Source gap'], copertura: { stato: 'limitata', limiti: ['Repository incomplete'], candidati_osservati: 3, documenti_tentati: 2, max_documenti: 20 },
  freschezza: { stato: 'stale', ultimo_periodo: '2025-12-31', motivi: ['Expected new report missing'] },
  ultimo_non_verificato: true, coppia: { ambito: 'storico', prima: { url: 'https://example.org/old', sha256: 'old' }, dopo: { url: 'https://example.org/new', sha256: 'new' } },
  confronto_storico: { stato: 'ok', cambiamenti: [{ tipo: 'modificato', prima: citation, dopo: { ...citation, url: 'javascript:alert(1)' } }], misure: { cambiamenti: 1 }, sezioni_confrontate: ['Risk'], similarita_sezioni: { Risk: { jaccard: 0.5, coseno: 0.6 } } },
}, judgment: { status: 'ok', findings: [{ category: 'Risk', assessment: 'Original analyst judgment', citations: ['C1-dopo'] }] }, index: { status: 'ok' } };

function panel(api) {
  let si = 0, ei = 0, ri = 0;
  const states = [], refs = [], deps = [], cleanup = [], pending = [], nodes = [];
  const jsx = name => (type, props, ...args) => { nodes.push({ type, props }); return JSX[name](type, props, ...args); };
  const load = creaCaricatore({ stub: {
    react: { ...React,
      useRef(initial) { return refs[ri++] ||= { current: initial }; },
      useState(initial) { const at = si++; if (!(at in states)) states[at] = typeof initial === 'function' ? initial() : initial;
        return [states[at], value => { states[at] = typeof value === 'function' ? value(states[at]) : value; }]; },
      useEffect(fn, d) { const at = ei++, before = deps[at];
        if (!before || !d || d.some((v, i) => !Object.is(v, before[i]))) pending.push(() => { cleanup[at]?.(); cleanup[at] = fn(); });
        deps[at] = d; },
    },
    'react/jsx-runtime': { ...JSX, jsx: jsx('jsx'), jsxs: jsx('jsxs') },
    '@/lib/api': { Bellomberg: api },
  } });
  const language = load('i18n/lingua.ts');
  const Component = load('components/FilingDiffPanel.tsx').default;
  const render = (ticker = 'SYNTH.X', lang = 'it') => { si = ei = ri = 0; nodes.length = 0; language.impostaLinguaCorrente(lang);
    return renderToStaticMarkup(React.createElement(Component, { ticker })); };
  const settle = async () => { for (const fn of pending.splice(0)) fn(); await new Promise(resolve => setImmediate(resolve)); };
  const ready = async (ticker = 'SYNTH.X', lang = 'it') => { render(ticker, lang); await settle(); render(ticker, lang); await settle(); return render(ticker, lang); };
  const click = async (label) => { const node = nodes.find(n => n.type === 'button' && text(n.props.children).includes(label));
    assert.ok(node, `button ${label}`); await node.props.onClick(); };
  return { render, settle, ready, click, nodes };
}
const text = value => Array.isArray(value) ? value.map(text).join(' ') : value && typeof value === 'object' ? text(value.props?.children) : String(value ?? '');

test('bilingual loading, missing profile, stale historical diff, safe document links and distinct AI judgment', async () => {
  const ui = panel({ filingList: async () => full, filingRun: async () => detail });
  assert.match(ui.render('SYNTH.X', 'it'), /Caricamento pubblicazioni/);
  const it = await ui.ready('SYNTH.X', 'it');
  assert.match(it, /Confronto storico/); assert.match(it, /stale/); assert.match(it, /Repository incomplete/);
  assert.match(it, /Original analyst judgment/); assert.match(it, /Confronto testuale deterministico/);
  assert.match(it, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(it, /href="javascript:/);
  assert.match(it, /https:\/\/example.org\/report.pdf/); assert.match(it, /abc123/); assert.match(it, /Pagine fisiche: 4/);
  assert.match(it, /C1-dopo/); assert.doesNotMatch(it, /Citazione non presente/);
  const en = ui.render('SYNTH.X', 'en'); assert.match(en, /Historical comparison/); assert.match(en, /AI judgment/);
  const noProfile = panel({ filingList: async () => ({ ...full, profile: null, runs: [] }) });
  assert.match(await noProfile.ready(), /Nessun profilo documentale/);
});

test('renders the actual synthetic pipeline output with its document citation IDs', async () => {
  const fixturePath = path.resolve(__dirname, '../fixtures/filing-pipeline.json');
  const fixture = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));
  const ui = panel({ filingList: async () => fixture.listing, filingRun: async () => fixture.detail });
  const html = await ui.ready('ACME.TEST', 'en');
  assert.equal(fixture.detail.result.confronto_corrente.stato, 'ok');
  assert.match(html, /Latest verified period comparison/);
  assert.match(html, /cdn.acme.example\/annual-2025.html/);
  assert.match(html, /cdn.acme.example\/annual-2024.html/);
  assert.match(html, /Demand is declining/);
  assert.match(html, /Freshness: n.d./);
  assert.deepEqual(fixture.citation_ids, ['C1-dopo', 'C1-prima']);
});

test('an old ticker response cannot replace the selected ticker', async () => {
  let release;
  const ui = panel({ filingList: ticker => ticker === 'OLD' ? new Promise(resolve => { release = resolve; }) : Promise.resolve({ ...full, ticker: 'NEW', profile: { ...full.profile, ticker: 'NEW', profile: { ticker: 'NEW' } }, runs: [] }) });
  ui.render('OLD'); await ui.settle(); ui.render('NEW'); await ui.settle();
  release({ ...full, ticker: 'OLD' }); await new Promise(resolve => setImmediate(resolve));
  const html = ui.render('NEW'); assert.match(html, /NEW/); assert.doesNotMatch(html, /SYNTH.X/);
});

test('manual refresh and profile save errors are visible; rendering does not trigger writes', async () => {
  let writes = 0;
  const ui = panel({ filingList: async () => full, filingRun: async () => detail,
    filingRefresh: async () => { writes++; throw new Error('Refresh failed'); },
    filingSaveProfile: async () => { writes++; throw new Error('Save failed'); } });
  await ui.ready(); assert.equal(writes, 0);
  await ui.click('Verifica ora'); assert.match(ui.render(), /Refresh failed/);
  await ui.click('Salva profilo'); assert.match(ui.render(), /Save failed/);
  assert.equal(writes, 2);
});
