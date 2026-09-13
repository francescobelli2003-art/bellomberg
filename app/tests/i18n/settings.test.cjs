const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');

function harness(page, overrides = {}) {
  ambienteBrowser();
  const listeners = new Map();
  globalThis.window = { addEventListener: (name, fn) => listeners.set(name, fn),
    removeEventListener() {}, dispatchEvent(event) { listeners.get(event.type)?.(event); } };
  globalThis.sessionStorage = globalThis.localStorage;
  const slots = [], effects = [], calls = [];
  let cursor = 0, tree;
  const memo = (fn, deps) => {
    const index = cursor++, old = slots[index];
    if (!old || !deps || deps.some((value, i) => !Object.is(value, old.deps[i]))) {
      slots[index] = { deps, value: fn() };
    }
    return slots[index].value;
  };
  const hooks = { ...React,
    useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial;
      return [slots[index], value => { slots[index] = typeof value === 'function' ? value(slots[index]) : value; }]; },
    useRef(initial) { return memo(() => ({ current: initial }), []); },
    useMemo: memo, useCallback: (fn, deps) => memo(() => fn, deps),
    useEffect(fn, deps) { memo(() => { effects.push(fn); return true; }, deps); },
    useSyncExternalStore(_subscribe, snapshot) { return snapshot(); },
  };
  const fixtures = {
    scheduledTasks: { tasks: [{ TaskName: 'Bellomberg-SYNTH', State: 'Ready', LastTaskResult: 0,
      LastRunTime: '09/10/2026 23:00:00', NextRunTime: '09/11/2026 23:00:00' }] },
    health: { status: 'ok', version: 'synthetic-version' }, fx: { rates: { USD: 0.875 } },
    agentsList: { engines: { briefing: 'SYNTHETIC_MODEL_ID' } },
    dbBackupsList: { count: 1, backups: [{ filename: 'synthetic_backup.zip', path: 'synthetic/path/synthetic_backup.zip',
      size_mb: 2.5, created: '2026-09-10T23:00:00' }] },
    dbBackupCreate: { ok: true, size_mb: 2.5, files_count: 1, backup_path: 'synthetic/path/new.zip', db_quick_check: {} },
    portfolio: { positions: [{ ticker: 'SYNTH' }] }, ...overrides,
  };
  const api = { Bellomberg: new Proxy({}, { get(_target, method) { return async (...args) => {
    calls.push({ method, args });
    if (!(method in fixtures)) throw new Error('Unexpected synthetic API call: ' + String(method));
    const value = fixtures[method]; if (value instanceof Error) throw value;
    if (typeof value === 'function') return value(...args);
    return value;
  }; } }), API_BASE: 'http://synthetic.invalid' };
  const navigate = () => {};
  const load = creaCaricatore({ stub: { react: hooks, '@/lib/api': api, '../lib/api': api,
    'react-router-dom': { useNavigate: () => navigate },
    './SceltaLingua': { __esModule: true, default: () => React.createElement('div', { 'data-preference-owned-by-root': true }) },
    './RunConfirmDialog': { __esModule: true, default: () => null },
  } });
  const language = load('i18n/lingua.ts'), Component = load('components/' + page + '.tsx').default;
  const props = { open: true, onClose() {} };
  const render = () => {
    cursor = 0; tree = Component(props);
    const html = renderToStaticMarkup(tree);
    while (effects.length) effects.shift()();
    return html;
  };
  const settle = async () => { await Promise.resolve(); await Promise.resolve(); return render(); };
  const nodes = (value, predicate) => {
    if (!value || typeof value !== 'object') return [];
    if (Array.isArray(value)) return value.flatMap(item => nodes(item, predicate));
    return [...(predicate(value) ? [value] : []), ...nodes(value.props?.children, predicate)];
  };
  return { calls, language, render, settle, listeners,
    elements: predicate => nodes(tree, predicate) };
}

test('Settings renders real IT/EN labels, original records and locale numbers without repeating reads', async () => {
  const h = harness('SettingsPanel'); h.language.impostaLinguaCorrente('it'); h.render();
  const it = await h.settle(); assert.match(it, /Impostazioni/); assert.match(it, /2,50 MB/);
  assert.match(it, /synthetic_backup\.zip/);
  const count = h.calls.length; assert.equal(count, 5);
  h.language.impostaLinguaCorrente('en'); const en = h.render();
  assert.match(en, /Settings/); assert.match(en, /2\.50 MB/); assert.match(en, /SYNTHETIC_MODEL_ID/);
  assert.doesNotMatch(en, /La macchina|Il pettine delle notti|nessun backup/);
  assert.equal(h.calls.length, count);
});

test('an existing backup result follows language changes while its original path and measured values remain', async () => {
  const h = harness('SettingsPanel'); h.render(); await h.settle();
  const button = h.elements(node => node.type === 'button' && node.props.className === 'btn am')[0];
  await button.props.onClick(); const it = h.render();
  assert.match(it, /Backup creato/); assert.match(it, /synthetic\/path\/new\.zip/);
  const count = h.calls.length; h.language.impostaLinguaCorrente('en');
  const en = h.render(); assert.match(en, /Backup created/); assert.match(en, /2\.5.*synthetic\/path\/new\.zip/);
  assert.equal(h.calls.length, count); assert.equal(h.calls.filter(c => c.method === 'dbBackupCreate').length, 1);
});

test('Settings errors stay explicit in both languages and preserve the original backend detail', async () => {
  const h = harness('SettingsPanel', { fx: new Error('Original FX failure'), dbBackupCreate: new Error('Original backup failure') });
  h.render(); const it = await h.settle(); assert.match(it, /DATI NON CARICATI/);
  await h.elements(node => node.type === 'button' && node.props.className === 'btn am')[0].props.onClick();
  h.language.impostaLinguaCorrente('en'); const en = h.render();
  assert.match(en, /DATA NOT LOADED/); assert.match(en, /Backup NOT created/); assert.match(en, /Original backup failure/);
});

test('palette localizes navigation and actions while preserving query and avoiding extra portfolio reads', async () => {
  const h = harness('CommandPalette'); h.render(); h.listeners.get('bb:palette')(); h.render(); await h.settle();
  const input = h.elements(node => node.type === 'input')[0]; input.props.onChange({ target: { value: 'SYNTH' } });
  const it = h.render(); assert.match(it, /POSIZIONE SYNTH/);
  const count = h.calls.length; h.language.impostaLinguaCorrente('en'); const en = h.render();
  assert.match(en, /POSITION SYNTH/); assert.match(en, /value="SYNTH"/); assert.doesNotMatch(en, /feed filtrato sul nome/);
  assert.equal(h.calls.length, count); assert.equal(h.calls.filter(c => c.method === 'portfolio').length, 1);
  h.elements(node => node.type === 'input')[0].props.onChange({ target: { value: '' } });
  const all = h.render(); assert.match(all, /SETTINGS/); assert.match(all, /Research/); assert.match(all, /REFRESH PRICES/);
});

test('a file on the same date does not establish which job created it or what failed afterwards', async () => {
  const h = harness('SettingsPanel', { scheduledTasks: { tasks: [{ TaskName: 'Bellomberg-NewsFeed', State: 'Ready', LastTaskResult: 7,
    LastRunTime: '09/10/2026 23:00:00' }] } });
  h.render(); const html = await h.settle();
  assert.doesNotMatch(html, /fallisce quello che viene dopo/);
  assert.match(html, /synthetic_backup\.zip/); assert.match(html, /NewsFeed/);
  assert.match(html, /causa non dimostrata/i);
});

test('a scheduler error in an HTTP-success payload is declared with its original diagnostic', async () => {
  const h = harness('SettingsPanel', { scheduledTasks: { tasks: [], error: 'Original scheduler diagnostic' } });
  h.render(); const html = await h.settle();
  assert.match(html, /Original scheduler diagnostic/); assert.match(html, /DATI NON CARICATI/);
  assert.doesNotMatch(html, /Nessun lavoro schedulato/);
});

test('Settings does not invent the cause of a failed job or a backup directory absent from the payload', async () => {
  const h = harness('SettingsPanel', { scheduledTasks: { tasks: [{ TaskName: 'Bellomberg-SYNTH', State: 'Ready', LastTaskResult: 7 }] },
    dbBackupsList: { count: 0, backups: [] } });
  h.render(); const html = await h.settle();
  assert.doesNotMatch(html, /run_db_backup\.bat|data\/backups/);
  assert.match(html, /SYNTH/); assert.match(html, /7/);
  assert.match(html, /causa non dichiarata/i); assert.match(html, /percorso non dichiarato/i);
});

test('delete confirmation follows language changes, preserves the original filename and does not delete on translation or cancel', async () => {
  const h = harness('SettingsPanel'); h.render(); await h.settle();
  h.elements(n => n.type === 'button' && n.props['aria-label'] === 'Elimina synthetic_backup.zip')[0].props.onClick();
  assert.match(h.render(), /Eliminazione definitiva/);
  const count = h.calls.length; h.language.impostaLinguaCorrente('en');
  const en = h.render(); assert.match(en, /Permanent deletion/); assert.match(en, /2\.50 MB/); assert.match(en, /synthetic_backup\.zip/);
  assert.equal(h.calls.length, count);
  h.elements(n => n.type === 'button' && n.props.children === 'CANCEL')[0].props.onClick();
  assert.doesNotMatch(h.render(), /Permanent deletion/); assert.equal(h.calls.length, count);
});

test('palette pending and completed action messages translate without executing the action again', async () => {
  let finish; const pending = new Promise(resolve => { finish = resolve; });
  const h = harness('CommandPalette', { updatePrices: () => pending });
  h.render(); h.listeners.get('bb:palette')(); h.render(); await h.settle();
  const item = h.elements(n => n.props?.role === 'option' && Array.isArray(n.props.children) && n.props.children.some(child => child?.props?.children === 'REFRESH PREZZI'))[0];
  const action = item.props.onClick(); assert.match(h.render(), /AGGIORNAMENTO PREZZI/);
  const count = h.calls.length; h.language.impostaLinguaCorrente('en');
  assert.match(h.render(), /UPDATING PRICES/); assert.equal(h.calls.length, count);
  finish({ ok: true }); await action; assert.match(h.render(), /PRICES UPDATED/);
  h.language.impostaLinguaCorrente('it'); assert.match(h.render(), /PREZZI AGGIORNATI/);
  assert.equal(h.calls.filter(c => c.method === 'updatePrices').length, 1);
});

test('engine role labels are translated while model strings and unknown source keys remain unchanged', async () => {
  const h = harness('SettingsPanel', { agentsList: { engines: { action_extractor: 'SYNTHETIC_MODEL_ID', committee_macro: 'SYNTHETIC_MACRO', future_role: 'SYNTHETIC_FUTURE' } } });
  h.render(); const it = await h.settle(); assert.match(it, /Estrazione azioni/);
  const count = h.calls.length; h.language.impostaLinguaCorrente('en'); const en = h.render();
  assert.match(en, /Action extraction/); assert.match(en, /SYNTHETIC_MODEL_ID/); assert.match(en, /future role/);
  assert.match(en, /SYNTHETIC_MACRO/); assert.equal(h.calls.length, count);
});

test('translated committee action still opens the confirmation before any paid run', async () => {
  const h = harness('CommandPalette'); h.language.impostaLinguaCorrente('en');
  h.render(); h.listeners.get('bb:palette')(); h.render(); await h.settle();
  const count = h.calls.length;
  h.elements(n => n.props?.role === 'option' && Array.isArray(n.props.children) && n.props.children.some(child => child?.props?.children === 'LAUNCH COMMITTEE'))[0].props.onClick();
  h.render(); const confirmation = h.elements(n => n.props && 'onConfirm' in n.props)[0];
  assert.equal(confirmation.props.open, true); assert.equal(h.calls.length, count);
  h.language.impostaLinguaCorrente('it'); h.render(); assert.equal(h.calls.length, count);
  confirmation.props.onCancel(); h.render(); assert.equal(h.calls.length, count);
});

// 13/09 (Claude Opus 5): il nome nudo «install_all_schedulers.ps1» non si lanciava piu' dalla radice
// (spostato in tools/ops/windows il 09/09) e taceva che lo script registra Bellomberg-Consigliere
// ACCESO e azzera il trigger del PriceUpdater (MASTER_TODO T8). Frasi attese congelate qui.
test('with no scheduled jobs the hint gives the runnable installer command and warns what it re-enables, in both languages', async () => {
  const h = harness('SettingsPanel', { scheduledTasks: { tasks: [] } });
  const command = 'powershell -ExecutionPolicy Bypass -File .\\tools\\ops\\windows\\install_all_schedulers.ps1';
  h.render(); const it = await h.settle();
  const count = h.calls.length; h.language.impostaLinguaCorrente('en'); const en = h.render();
  for (const html of [it, en]) {
    assert.ok(html.includes('<b>' + command + '</b>'), 'the command from the script header');
    assert.doesNotMatch(html, /<b>install_all_schedulers\.ps1<\/b>/);
  }
  assert.ok(it.includes('Nessun lavoro schedulato. Dalla cartella reale del repo, non dalla junction, esegui'));
  assert.ok(it.includes('in un PowerShell aperto come amministratore.'));
  assert.ok(it.includes('ATTENZIONE: lo script registra anche Bellomberg-Consigliere ACCESO (il comitato a pagamento, lunedì e giovedì) e azzera il trigger di Bellomberg-PriceUpdater. Dopo il lancio verifica qui lo stato di Bellomberg-Consigliere.'));
  assert.ok(en.includes('No scheduled jobs. From the real repository folder, not the junction, run'));
  assert.ok(en.includes('in PowerShell opened as administrator.'));
  assert.ok(en.includes('WARNING: the script also registers Bellomberg-Consigliere as ENABLED (the paid committee, Mondays and Thursdays) and resets the Bellomberg-PriceUpdater trigger. After running it, check the Bellomberg-Consigliere status here.'));
  assert.doesNotMatch(en, /Nessun lavoro|ATTENZIONE/); assert.equal(h.calls.length, count);
});

test('an engine read failure is declared with a translated label and its original diagnostic, not the raw JSON key', async () => {
  const h = harness('SettingsPanel', { agentsList: { engines: { engines_error: 'Original engine diagnostic', future_error: 'Original future diagnostic' } } });
  h.render(); const it = await h.settle();
  h.language.impostaLinguaCorrente('en'); const en = h.render();
  assert.match(it, /<b class="ko">Lettura dei motori in errore<\/b>/); assert.match(en, /<b class="ko">Engine read failed<\/b>/);
  for (const html of [it, en]) {
    assert.match(html, /Original engine diagnostic/); assert.doesNotMatch(html, />engines_error</);
    // una chiave d'errore sconosciuta resta visibile col suo nome: mai sparita in silenzio
    assert.match(html, /<b class="ko">future_error<\/b>/); assert.match(html, /Original future diagnostic/);
  }
});

test('Settings and palette leave only exact technical units, filenames and licence text outside the catalog', () => {
  const fs = require('node:fs'), path = require('node:path'), ts = require('typescript');
  const allowed = new Set(['powershell -ExecutionPolicy Bypass -File .\\tools\\ops\\windows\\install_all_schedulers.ps1', 'MB', 'quick_check', 'TradingView Lightweight Charts', '· Apache-2.0', 'EUR', 'engines', 'MB ·']);
  const residues = [];
  for (const filename of ['SettingsPanel.tsx', 'CommandPalette.tsx']) {
    const source = fs.readFileSync(path.join(__dirname, '../../src/components', filename), 'utf8');
    const tree = ts.createSourceFile(filename, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    function walk(node) {
      if (ts.isJsxText(node)) {
        const value = node.getText(tree).replace(/\s+/g, ' ').trim();
        if (/[A-Za-zÀ-ÿ]/.test(value) && !allowed.has(value)) residues.push(filename + ': ' + value);
      }
      ts.forEachChild(node, walk);
    }
    walk(tree);
  }
  assert.deepEqual(residues, []);
});

test('four-digit backup sizes keep locale grouping explicit in both languages', async () => {
  const h = harness('SettingsPanel', { dbBackupsList: { count: 1, backups: [{ filename: 'synthetic_large.zip', path: 'synthetic/path/synthetic_large.zip', size_mb: 1000, created: '2026-09-10T23:00:00' }] } });
  h.render(); assert.match(await h.settle(), /1\.000,00 MB/);
  h.language.impostaLinguaCorrente('en'); assert.match(h.render(), /1,000\.00 MB/);
});
