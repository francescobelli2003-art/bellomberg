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
    const typeName = typeof value.type === 'function' ? value.type.displayName || value.type.name || '' : '';
    // These are pure presentation callbacks. Walking them exposes the actual
    // JSX controls below ModernPage and local recovery wrappers without
    // invoking page/controller components or their effects.
    const presentation = typeof value.props?.render === 'function'
      && /ModernPage|Deferred|Boundary/.test(typeName) ? value.props.render() : null;
    // The Settings section views (components/impostazioni/Viste.tsx) are hook-free
    // presentation functions: expanding them exposes their buttons the same way.
    const view = typeof value.type === 'function' && /^Sezione/.test(typeName) ? value.type(value.props) : null;
    return [...(predicate(value) ? [value] : []), ...nodes(value.props?.children, predicate), ...nodes(presentation, predicate),
      ...nodes(view, predicate)];
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
  const button = h.elements(node => node.type === 'button' && node.props['data-azione'] === 'backup')[0];
  await button.props.onClick(); const it = h.render();
  assert.match(it, /Backup creato/); assert.match(it, /synthetic\/path\/new\.zip/);
  const count = h.calls.length; h.language.impostaLinguaCorrente('en');
  const en = h.render(); assert.match(en, /Backup created/); assert.match(en, /2\.5.*synthetic\/path\/new\.zip/);
  assert.equal(h.calls.length, count); assert.equal(h.calls.filter(c => c.method === 'dbBackupCreate').length, 1);
});

test('Settings errors stay explicit in both languages and preserve the original backend detail', async () => {
  const h = harness('SettingsPanel', { fx: new Error('Original FX failure'), dbBackupCreate: new Error('Original backup failure') });
  h.render(); const it = await h.settle(); assert.match(it, /Dati non caricati/);
  await h.elements(node => node.type === 'button' && node.props['data-azione'] === 'backup')[0].props.onClick();
  h.language.impostaLinguaCorrente('en'); const en = h.render();
  assert.match(en, /Data not loaded/); assert.match(en, /Backup not created/); assert.match(en, /Original backup failure/);
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
  // The read clears its loading flag in `.finally`: settle twice so the error, not the spinner, is painted.
  h.render(); await h.settle(); const html = await h.settle();
  assert.match(html, /Original scheduler diagnostic/); assert.match(html, /Dati non caricati/);
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
  const trigger = h.elements(n => n.type === 'button' && n.props['aria-label'] === 'Elimina synthetic_backup.zip')[0];
  trigger.props.onClick({ currentTarget: { focus() {} } });
  assert.match(h.render(), /Eliminare il backup\?/);
  const count = h.calls.length; h.language.impostaLinguaCorrente('en');
  const en = h.render(); assert.match(en, /Delete the backup\?/); assert.match(en, /2\.50 MB/); assert.match(en, /synthetic_backup\.zip/);
  assert.equal(h.calls.length, count);
  h.elements(n => n.type === 'button' && n.props.children === 'Cancel')[0].props.onClick();
  assert.doesNotMatch(h.render(), /Delete the backup\?/); assert.equal(h.calls.length, count);
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
  assert.match(it, /<b>Lettura dei motori in errore<\/b>/); assert.match(en, /<b>Engine read failed<\/b>/);
  for (const html of [it, en]) {
    assert.match(html, /Original engine diagnostic/); assert.doesNotMatch(html, />engines_error</);
    // una chiave d'errore sconosciuta resta visibile col suo nome: mai sparita in silenzio
    assert.match(html, /<b>future_error<\/b>/); assert.match(html, /Original future diagnostic/);
  }
});

test('Settings and palette leave only exact technical units, filenames and licence text outside the catalog', () => {
  const fs = require('node:fs'), path = require('node:path'), ts = require('typescript');
  const allowed = new Set(['powershell -ExecutionPolicy Bypass -File .\\tools\\ops\\windows\\install_all_schedulers.ps1', 'MB', 'quick_check', 'TradingView Lightweight Charts', '· Apache-2.0', 'EUR', 'engines', 'MB ·', '1']);
  const residues = [];
  for (const filename of ['SettingsPanel.tsx', 'CommandPalette.tsx', 'impostazioni/Viste.tsx']) {
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

// 05/10 (Impostazioni Nuova): su macOS il backend lancia `powershell`, che non esiste. E' un sistema
// non supportato, non un dato mancante: niente banner rosso, stato neutro con la diagnostica originale.
test('a missing powershell on macOS is a neutral unsupported state, not a missing-data error', async () => {
  const diagnostic = "[Errno 2] No such file or directory: 'powershell'";
  const h = harness('SettingsPanel', { scheduledTasks: { tasks: [], error: diagnostic } });
  h.render(); await h.settle(); const it = await h.settle();
  assert.doesNotMatch(it, /Dati non caricati/); assert.match(it, /Non disponibili su macOS/);
  assert.ok(it.includes('No such file or directory'), 'the original diagnostic stays readable');
  h.language.impostaLinguaCorrente('en'); const en = h.render();
  assert.match(en, /Not available on macOS/); assert.doesNotMatch(en, /Data not loaded/);
});

// 05/10 (review PR #9): senza `count` dal backend il numero di file NON si ricava dalla lunghezza
// dell'elenco consegnato (troncato a 50) e la completezza non si dichiara «Completo»: si dice che manca.
test('a backup list without a declared count shows the gap, never the delivered length or «complete»', async () => {
  const h = harness('SettingsPanel', { dbBackupsList: { backups: [{ filename: 'synthetic_backup.zip', path: 'synthetic/path/synthetic_backup.zip',
    size_mb: 2.5, created: '2026-09-10T23:00:00' }] } });
  h.render(); const it = await h.settle();
  assert.doesNotMatch(it, /\b1 file\b/); assert.match(it, /\? file/);
  assert.doesNotMatch(it, />Completo</); assert.match(it, /non dichiara quanti file/);
  h.language.impostaLinguaCorrente('en'); const en = h.render();
  assert.doesNotMatch(en, /\b1 files?\b/); assert.doesNotMatch(en, />Complete</); assert.match(en, /does not declare how many files/);
});

// 05/10 (review PR #9): un lavoro senza LastTaskResult non scrive «undefined» nella frase.
test('a job without a last result never prints «undefined»', async () => {
  const h = harness('SettingsPanel', { scheduledTasks: { tasks: [
    { TaskName: 'Bellomberg-SYNTHOFF', State: 'Disabled', NextRunTime: '09/11/2026 23:00:00' },
    { TaskName: 'Bellomberg-SYNTHNEW', State: 'Ready', LastRunTime: '09/10/2026 23:00:00', NextRunTime: '09/11/2026 23:00:00' }] } });
  h.render(); await h.settle(); const it = await h.settle();
  assert.match(it, /SYNTHOFF/); assert.doesNotMatch(it, /undefined/);
  h.language.impostaLinguaCorrente('en'); assert.doesNotMatch(h.render(), /undefined/);
});

// 06/10 (review PR #9): il fumetto del grafico e i conteggi dei file hanno singolare e plurale
// veri, in entrambe le lingue: niente «1 presi a mano», «backup(s)», «1 files» o «1 days».
test('one backup taken by hand is singular in the tooltip, the aria label and the file counts, in both languages', async () => {
  const h = harness('SettingsPanel'); h.language.impostaLinguaCorrente('it'); h.render();
  const it = await h.settle();
  assert.match(it, /1 preso a mano/); assert.doesNotMatch(it, /1 presi a mano/);
  assert.match(it, /MB in 1 backup/); assert.match(it, />1 file</); assert.doesNotMatch(it, /\b1 giorni\b/);
  h.language.impostaLinguaCorrente('en'); const en = h.render();
  assert.match(en, /1 made manually/); assert.match(en, /MB across 1 backup\b/);
  assert.doesNotMatch(en, /backup\(s\)/); assert.doesNotMatch(en, /\b1 files\b/); assert.match(en, />1 file</);
  assert.doesNotMatch(en, /\b1 days\b/); assert.match(en, /1 day covered/);
});

test('two backups taken by hand on the same night keep the plural in both languages', async () => {
  const backups = [1, 2].map(i => ({ filename: 'synthetic_' + i + '.zip', path: 'synthetic/path/synthetic_' + i + '.zip',
    size_mb: 2, created: '2026-09-10T2' + i + ':00:00' }));
  const h = harness('SettingsPanel', { dbBackupsList: { count: 2, backups } });
  h.language.impostaLinguaCorrente('it'); h.render(); const it = await h.settle();
  assert.match(it, /2 presi a mano/); assert.match(it, /MB in 2 backup/); assert.match(it, />2 file</);
  h.language.impostaLinguaCorrente('en'); const en = h.render();
  assert.match(en, /2 made manually/); assert.match(en, /MB across 2 backups/); assert.match(en, />2 files</);
});

// 06/10 (review PR #9): sotto i 10 MB l'asse non arrotonda all'intero (2,5 era «3», e col fondo
// scala a 1 cima e meta' erano entrambe «1»): una cifra decimale sotto 10, due sotto 1.
test('the MB axis keeps decimals below 10 MB and never prints two identical labels', async () => {
  const assi = html => [...html.matchAll(/class="bbn-imp-gl"[^>]*>([^<]*)</g)].map(m => m[1]);
  const conMb = mb => harness('SettingsPanel', { dbBackupsList: { count: 1, backups: [{ filename: 'synthetic_backup.zip',
    path: 'synthetic/path/synthetic_backup.zip', size_mb: mb, created: '2026-09-10T23:00:00' }] } });
  let h = conMb(3); h.language.impostaLinguaCorrente('it'); h.render();
  assert.deepEqual(assi(await h.settle()), ['3,0 MB', '1,5']);
  h.language.impostaLinguaCorrente('en'); assert.deepEqual(assi(h.render()), ['3.0 MB', '1.5']);
  h = conMb(1); h.language.impostaLinguaCorrente('it'); h.render();
  assert.deepEqual(assi(await h.settle()), ['1,0 MB', '0,50']);
  h = conMb(40); h.language.impostaLinguaCorrente('it'); h.render();
  assert.deepEqual(assi(await h.settle()), ['40 MB', '20']);
  h = conMb(16); h.render();
  assert.deepEqual(assi(await h.settle()), ['16 MB', '8,0']);

  const { etichetteMb } = creaCaricatore()('components/impostazioni/logica.ts');
  const fisso = (v, cifre) => v.toFixed(cifre);
  assert.deepEqual(etichetteMb(2.5, fisso), ['2.5', '1.3']);
  assert.deepEqual(etichetteMb(9, fisso), ['9.0', '4.5']);
  // un formattatore che arrotonda tutto uguale non produce due etichette identiche
  const grezzo = (v, cifre) => (cifre < 3 ? '1' : v.toFixed(cifre));
  const [alto, mezzo] = etichetteMb(1, grezzo); assert.notEqual(alto, mezzo);
});

// 06/10 (review PR #9): la lingua salvata nel profilo senza memoria locale non e' un successo pieno:
// l'avviso ha tono e icona d'avviso, non la spunta verde.
test('the unsaved local cache notice is a warning, not the green check', async () => {
  const slots = []; let cursor = 0; const effects = [];
  const memo = (fn, deps) => { const i = cursor++, old = slots[i];
    if (!old || !deps || deps.some((v, k) => !Object.is(v, old.deps[k]))) slots[i] = { deps, value: fn() }; return slots[i].value; };
  const hooks = { ...React,
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = initial; return [slots[i], v => { slots[i] = typeof v === 'function' ? v(slots[i]) : v; }]; },
    useRef(initial) { return memo(() => ({ current: initial }), []); },
    useCallback: (fn, deps) => memo(() => fn, deps), useMemo: memo,
    useEffect(fn, deps) { memo(() => { effects.push(fn); return true; }, deps); },
    useSyncExternalStore(_s, snapshot) { return snapshot(); },
  };
  ambienteBrowser();
  const preferenze = { caricaPreferenza: async () => ({ selected: true, language: 'it', cacheSaved: false }),
    scegliLingua: async () => { throw new Error('unexpected save'); }, ErrorePreferenza: Error };
  const load = creaCaricatore({ stub: { react: hooks, '@/lib/api': { Bellomberg: {} }, '@/i18n/preferenze': preferenze } });
  const language = load('i18n/lingua.ts'); const SceltaLingua = load('components/SceltaLingua.tsx').default;
  const render = () => { cursor = 0; const html = renderToStaticMarkup(SceltaLingua({ variant: 'nuova' })); while (effects.length) effects.shift()(); return html; };
  language.impostaLinguaCorrente('it'); render(); await Promise.resolve(); await Promise.resolve();
  const it = render();
  const nota = it.match(/<span role="status" class="bbn-imp-card-note[^"]*">.*?<\/span>/)[0];
  assert.match(nota, /is-warn/); assert.match(nota, /triangle-alert/); assert.doesNotMatch(nota, /lucide-check\b/);
  assert.match(nota, /memoria locale del browser non è disponibile/);
  language.impostaLinguaCorrente('en'); assert.match(render(), /Browser storage is unavailable/);
});
