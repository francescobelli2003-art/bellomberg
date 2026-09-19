// Execute real TypeScript functions with synthetic promises/streams. No API or Electron process.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const { EventEmitter } = require('node:events');
const root = path.resolve(__dirname, '../..');
let checks = 0;
function check(condition, message) { assert.ok(condition, message); checks++; }
function loadModule(file) {
  const source = fs.readFileSync(path.join(root, file), 'utf8');
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const scope = { exports: {}, URL, console }; vm.runInNewContext(js, scope); return scope.exports;
}
const security = loadModule('electron/security.ts');
check(security.externalWebUrl('https://example.org/a') === 'https://example.org/a', 'HTTPS link allowed');
check(security.externalWebUrl('http://127.0.0.1:8765/memos/1/pdf') !== null, 'local API download allowed');
for (const url of ['file:///C:/test.txt', 'javascript:alert(1)', 'ms-settings:privacy', 'data:text/plain,a', '//example.org', 'https://user:pass@example.org']) {
  check(security.externalWebUrl(url) === null, 'reject OS scheme/credentials: ' + url);
}
check(security.isAppDocument('file:///app/index.html#/chat', 'file:///app/index.html'), 'hash navigation allowed');
check(!security.isAppDocument('file:///app/other.html', 'file:///app/index.html'), 'other local file rejected');
check(!security.isAppDocument('https://example.org/', 'file:///app/index.html'), 'remote navigation rejected');
check(!security.isAppDocument('http://localhost:5174/', 'http://localhost:5173/'), 'different dev origin rejected');
check(security.apiPort(undefined) === 8765 && security.apiPort('19876') === 19876, 'explicit API port');
for (const port of ['0', '1e4', 'NaN', '65536']) { assert.throws(() => security.apiPort(port)); checks++; }
const { portfolioValues } = require('../i18n/_carica.cjs').creaCaricatore()('lib/portfolio-values.ts');
check(portfolioValues({ cash_source: null, cash_disponibile_eur: 0, nav_total_eur: 1200 }).nav === null, 'missing cash source invalidates NAV');
check(portfolioValues({ cash_source: 'sqlite:cash_state', cash_disponibile_eur: 0, nav_total_eur: 1200 }).cash === 0, 'measured zero preserved');
check(portfolioValues({ cash_source: 'sqlite:cash_state', cash_disponibile_eur: null, nav_total_eur: 1200 }).nav === null, 'null is not zero');
check(portfolioValues({ cash_source: null, cash_source_note: 'synthetic failure' }).note === 'synthetic failure', 'source error kept');

const alertsSource = fs.readFileSync(path.join(root, 'src/hooks/useNewsAlerts.ts'), 'utf8');
const alertsTree = ts.createSourceFile('useNewsAlerts.ts', alertsSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
const batchNode = alertsTree.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'selectNotificationBatch');
check(!!batchNode, 'notification batch function located');
const batchScope = { exports: {} };
vm.runInNewContext(ts.transpileModule(batchNode.getText(alertsTree) + '\nexports.fn=selectNotificationBatch;', {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, batchScope);
const batch = batchScope.exports.fn([{ id: 1 }, { id: 2 }, { id: 3 }, { id: 4 }, { id: 5 }], new Set([2]), 3);
check(Array.from(batch, x => x.id).join(',') === '1,3,4', 'only displayed alerts enter the notification batch');

const source = fs.readFileSync(path.join(root, 'src/pages/Chat.tsx'), 'utf8');
const tree = ts.createSourceFile('Chat.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const functions = {};
function visit(node) {
  if (ts.isVariableDeclaration(node) && ['sendMessage', 'stopStream'].includes(node.name.getText(tree))) functions[node.name.getText(tree)] = node.initializer.getText(tree);
  if (ts.isCallExpression(node) && node.expression.getText(tree) === 'useEffect' && node.arguments[1]?.getText(tree) === '[activeSession]') functions.history = node.arguments[0].getText(tree);
  ts.forEachChild(node, visit);
}
visit(tree);
for (const name of ['sendMessage', 'stopStream', 'history']) check(!!functions[name], 'real function located: ' + name);
function bind(name, scope) {
  const js = ts.transpileModule('globalThis.run = ' + functions[name], { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  const context = vm.createContext(scope); vm.runInContext(js, context); return context.run;
}
const deferred = () => { let resolve; const promise = new Promise(r => resolve = r); return { promise, resolve }; };
const settle = () => new Promise(resolve => setImmediate(resolve));
const { creaCaricatore, ambienteBrowser } = require('../i18n/_carica.cjs');
ambienteBrowser();
globalThis.window = { bellomberg: { apiUrl: 'http://synthetic.invalid' }, location: { href: 'http://synthetic.invalid/' } };
const caricaChat = creaCaricatore();
const language = caricaChat('i18n/lingua.ts');
const { t: tr } = caricaChat('i18n/t.ts');
const { requestHeaders } = caricaChat('lib/api.ts');
const { leggiDetail } = caricaChat('lib/quota.ts');
function chatScope() {
  const state = { messages: [], streaming: false, sessionsError: null };
  const scope = {
    input: 'synthetic question', selectedAgent: { id: 'quant' }, streaming: false, messagesLoading: false, ritirato: null, activeSession: 1,
    setInput() {}, setPin() {}, setHot() {}, setActiveSession() {}, setMsgsErr() {}, setSessions() {}, setMessagesLoading() {},
    skipFetchRef: { current: null }, sendingRef: { current: false }, requestRef: { current: 0 }, abortRef: { current: null },
    setStreaming(value) { state.streaming = value; },
    setMessages(value) { state.messages = typeof value === 'function' ? value(state.messages) : value; },
    AbortController, TextDecoder, performance, console, getSessionToken() { return null; }, clearSessionAndReload() {},
    tr, requestHeaders, linguaCorrente: language.linguaCorrente,
    leggiDetail, setSessionsErr(value) { state.sessionsError = value; },
    titoloDaMessaggio: text => text,
    Bellomberg: { chatStreamUrl() { return 'synthetic://not-a-network-request'; }, chatListSessions() { return Promise.resolve({ sessions: [] }); } },
  };
  return { state, scope };
}
function response(text) {
  let reads = 0;
  return { ok: true, status: 200, body: { getReader() { return {
    read() { return Promise.resolve(reads++ === 0 ? { done: false, value: new TextEncoder().encode(text) } : { done: true }); },
    cancel() { return Promise.resolve(); },
  }; } } };
}
async function main() {
  const mainSource = fs.readFileSync(path.join(root, 'electron/main.ts'), 'utf8');
  const mainTree = ts.createSourceFile('main.ts', mainSource, ts.ScriptTarget.Latest, true);
  const backendFunctions = {};
  for (const node of mainTree.statements) {
    if (ts.isFunctionDeclaration(node) && ['startPythonBackend', 'stopOwnedBackend'].includes(node.name?.text)) {
      backendFunctions[node.name.text] = node.getText(mainTree);
    }
  }
  function backendScope({ alreadyUp = false, fail = false } = {}) {
    const errors = []; let killed = 0; let spawned = 0; let pings = 0;
    const context = vm.createContext({
      PROJECT_ROOT: '/synthetic/backend', API_PORT: 8765, quitting: false, pythonBackend: null, backendOwned: false,
      console: { log() {}, error() {} }, process: { platform: 'win32', env: { BELLOMBERG_PYTHON: 'synthetic-python' } },
      path, fs: { existsSync() { return true; } }, backendError(message) { errors.push(message); },
      pingBackend: async () => alreadyUp || pings++ > 0 && !fail,
      setTimeout(callback) { callback(); },
      spawn(executable, args, options) {
        check(executable === 'synthetic-python' && options.windowsHide === true && options.shell === undefined, 'configured Python starts without shell or console');
        spawned++; const child = new EventEmitter(); child.stdout = new EventEmitter(); child.stderr = new EventEmitter(); child.exitCode = null;
        child.kill = () => { killed++; child.exitCode = 0; child.emit('exit', 0); };
        if (fail) queueMicrotask(() => child.emit('error', new Error('synthetic ENOENT')));
        return child;
      },
    });
    for (const text of Object.values(backendFunctions)) vm.runInContext(ts.transpileModule(text, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText, context);
    return { context, errors, counts: () => ({ killed, spawned }) };
  }
  const reused = backendScope({ alreadyUp: true }); await reused.context.startPythonBackend(); reused.context.stopOwnedBackend();
  check(reused.counts().spawned === 0 && reused.counts().killed === 0, 'external backend neither spawned nor stopped');
  const owned = backendScope(); await owned.context.startPythonBackend(); owned.context.stopOwnedBackend(); owned.context.stopOwnedBackend();
  check(owned.counts().spawned === 1 && owned.counts().killed === 1 && owned.errors.length === 0, 'owned backend cleaned exactly once without false failure');
  const failed = backendScope({ fail: true }); await failed.context.startPythonBackend();
  check(failed.errors.length === 1 && failed.errors[0].includes('ENOENT') && failed.context.pythonBackend === null, 'asynchronous spawn failure declared and ownership cleared');

  // Backend dialogs (P4, 13/09): Electron starts before the backend that stores the language choice,
  // so every dialog is bilingual in one window: Italian paragraph, blank line, English paragraph,
  // then the technical details (paths, exit codes, errors) exactly once under bilingual labels.
  const dialogProblems = [];
  const expectDialog = (condition, message) => { if (condition) checks++; else dialogProblems.push(message); };
  const IT_WORDS = new Set(['il', 'la', 'le', 'non', 'di', 'del', 'della', 'dell', 'alla', 'e', '\u00e8', 'oppure', 'poi', 'dopo', 'solo',
    'avvio', 'avvia', 'controlla', 'imposta', 'correggi', 'riavvia', 'consulta', 'installa', 'trovato', 'fallito', 'terminato']);
  const EN_WORDS = new Set(['the', 'and', 'is', 'was', 'not', 'of', 'to', 'or', 'then', 'after', 'only', 'check', 'set', 'fix',
    'restart', 'see', 'install', 'start', 'does', 'failed', 'found', 'exited', 'ready']);
  const dialogWords = (text, set) => String(text ?? '').toLowerCase().split(/[^\p{L}]+/u).filter(word => set.has(word)).length;
  const italian = text => dialogWords(text, IT_WORDS) >= 2 && dialogWords(text, EN_WORDS) === 0;
  const english = text => dialogWords(text, EN_WORDS) >= 2 && dialogWords(text, IT_WORDS) === 0;
  const bilingualLabel = line => { const m = /^([^/:\n]+) \/ ([^/:\n]+): /.exec(line); return !!m && m[1].trim() !== m[2].trim(); };

  const errorFunction = mainTree.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'backendError');
  check(!!errorFunction, 'real function located: backendError');
  const shown = [];
  const errorContext = vm.createContext({ quitting: false, console: { error() {} }, dialog: { showErrorBox(title, body) { shown.push({ title, body }); } } });
  vm.runInContext(ts.transpileModule(errorFunction.getText(mainTree), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText, errorContext);
  const composed = 'Frase sintetica.\n\nSynthetic sentence.\n\nEtichetta / Label: valore';
  errorContext.backendError(composed);
  const [titleIt, titleEn, ...titleRest] = String(shown[0]?.title).split(' / ');
  expectDialog(shown.length === 1 && shown[0].body === composed, 'backend dialog shows the composed message unchanged');
  expectDialog(/^Bellomberg .*backend non disponibile$/.test(titleIt) && titleEn === 'backend unavailable' && titleRest.length === 0,
    'backend dialog title is Italian, then English: ' + JSON.stringify(shown[0]?.title));

  const dialogCalls = [];
  (function findDialogCalls(node) {
    if (ts.isCallExpression(node) && node.expression.getText(mainTree) === 'backendError') dialogCalls.push(node);
    ts.forEachChild(node, findDialogCalls);
  })(mainTree);
  const textualCalls = (mainSource.match(/\bbackendError\s*\(/g) || []).length - 1; // minus the declaration
  check(dialogCalls.length > 0 && dialogCalls.length === textualCalls, 'every backendError call located: ' + dialogCalls.length + ' of ' + textualCalls);
  for (const call of dialogCalls) {
    const where = 'main.ts:' + (mainTree.getLineAndCharacterOfPosition(call.getStart(mainTree)).line + 1) + ' backendError';
    const composed = call.arguments.length === 1 && ts.isCallExpression(call.arguments[0]) && call.arguments[0].expression.getText(mainTree) === 'bilingue';
    expectDialog(composed, where + ' must pass bilingue(it, en, ...details), got: ' + call.arguments.map(a => a.getText(mainTree)).join(', ').slice(0, 120));
    if (!composed) continue;
    const [it, en, ...details] = call.arguments[0].arguments;
    expectDialog(!!it && ts.isStringLiteralLike(it) && italian(it.text), where + ': first paragraph is a fixed Italian sentence: ' + it?.getText(mainTree));
    expectDialog(!!en && ts.isStringLiteralLike(en) && english(en.text), where + ': second paragraph is a fixed English sentence: ' + en?.getText(mainTree));
    for (const detail of details) {
      let head = detail;
      while (ts.isBinaryExpression(head) && head.operatorToken.kind === ts.SyntaxKind.PlusToken) head = head.left;
      expectDialog(head !== detail && ts.isStringLiteralLike(head) && bilingualLabel(head.text), where + ': detail has a bilingual label and its value once: ' + detail.getText(mainTree));
    }
  }

  const paragraphs = message => String(message ?? '').split('\n\n');
  const occurrences = (text, needle) => String(text ?? '').split(needle).length - 1;
  function expectLayout(name, scope, needles, hasDetails = true) {
    const message = scope.errors[0];
    const [it, en, details, ...rest] = paragraphs(message);
    expectDialog(scope.errors.length === 1 && italian(it) && english(en) && rest.length === 0 && (hasDetails ? !!details : details === undefined),
      name + ': one dialog, Italian paragraph, blank line, English paragraph' + (hasDetails ? ', details' : '') + ': ' + JSON.stringify(scope.errors));
    for (const line of hasDetails ? String(details ?? '').split('\n') : []) expectDialog(bilingualLabel(line), name + ': detail line has a bilingual label: ' + JSON.stringify(line));
    for (const needle of needles) expectDialog(occurrences(message, needle) === 1 && String(details ?? '').includes(needle), name + ': shown once, among the details: ' + needle);
  }
  const noRoot = backendScope(); noRoot.context.PROJECT_ROOT = null;
  await noRoot.context.startPythonBackend();
  expectLayout('installer without backend', noRoot, [], false);
  const noScript = backendScope(); noScript.context.fs = { existsSync() { return false; } };
  await noScript.context.startPythonBackend();
  expectLayout('backend folder without bellomberg_api.py', noScript, [path.join('/synthetic/backend', 'bellomberg_api.py')]);
  const exited = backendScope(); const triedVenv = '/synthetic/backend/.venv/Scripts/python.exe';
  Object.assign(exited.context, {
    process: { platform: 'win32', env: {} }, pingBackend: async () => false,
    defaultPython: () => ({ python: 'synthetic-python', source: 'PATH', tried: [triedVenv] }),
    spawn() {
      const child = new EventEmitter(); child.stdout = new EventEmitter(); child.stderr = new EventEmitter(); child.exitCode = null;
      queueMicrotask(() => { child.stderr.emit('data', Buffer.from('Traceback\nSyntheticError: missing module\n')); child.exitCode = 77; child.emit('exit', 77); });
      return child;
    },
  });
  await exited.context.startPythonBackend();
  expectLayout('backend exited', exited, ['77', triedVenv, 'SyntheticError: missing module']);
  expectDialog(/ambiente virtuale non trovato \/ virtual environment not found: /.test(exited.errors[0] ?? ''), 'tried virtual environment named in both languages: ' + JSON.stringify(exited.errors));
  // The exit event carries either a numeric status or a signal; missing status is not zero.
  for (const [code, signal, expectedCode, expectedSignal] of [
    [null, 'SIGTERM', 'non disponibile / unavailable', 'SIGTERM'],
    [null, 'SIGKILL', 'non disponibile / unavailable', 'SIGKILL'],
    [0, null, '0', 'nessuno ricevuto / none received'],
    [null, null, 'non disponibile / unavailable', 'nessuno ricevuto / none received'],
  ]) {
    const ended = backendScope();
    await ended.context.startPythonBackend();
    ended.context.pythonBackend.emit('exit', code, signal);
    const description = 'backend exit ' + String(code) + '/' + String(signal);
    expectLayout(description, ended, signal ? [signal] : []);
    const body = ended.errors[0] ?? '';
    expectDialog(ended.context.pythonBackend === null && ended.context.backendOwned === false,
      description + ': exit clears ownership');
    expectDialog(body.includes('Codice di uscita / Exit code: ' + expectedCode),
      description + ': observed numeric status or explicit unavailable status');
    expectDialog(body.includes('Segnale / Signal: ' + expectedSignal),
      description + ': observed signal is distinct from the numeric status');
    expectDialog(!/\b(?:null|undefined)\b/.test(body), description + ': no raw null/undefined in the dialog');
  }
  expectLayout('Python failed to start', failed, ['synthetic ENOENT', 'synthetic-python']);
  const badPython = backendScope();
  Object.assign(badPython.context, { process: { platform: 'win32', env: { BELLOMBERG_PYTHON: '/synthetic/missing/python' } }, fs: { existsSync() { return false; } } });
  await badPython.context.startPythonBackend();
  expectLayout('BELLOMBERG_PYTHON path missing', badPython, ['/synthetic/missing/python']);
  const slow = backendScope(); let syntheticClock = 0;
  Object.assign(slow.context, { pingBackend: async () => false, Date: { now: () => (syntheticClock += 20000) } });
  await slow.context.startPythonBackend();
  expectLayout('backend not ready in time', slow, [], false);
  const thrown = backendScope(); thrown.context.pingBackend = async () => { throw new Error('synthetic ping failure'); };
  await thrown.context.startPythonBackend();
  expectLayout('unexpected startup failure', thrown, ['synthetic ping failure']);
  check(dialogProblems.length === 0, 'bilingual backend dialogs:\n  ' + dialogProblems.join('\n  '));

  const waits = new Map(); const history = chatScope();
  history.scope.Bellomberg.chatGetSession = id => { const wait = deferred(); waits.set(id, wait); return wait.promise; };
  const cleanup = bind('history', history.scope)();
  cleanup(); history.scope.activeSession = 2; bind('history', history.scope)();
  waits.get(2).resolve({ messages: [{ role: 'assistant', content: 'SESSION-B' }] }); await settle();
  waits.get(1).resolve({ messages: [{ role: 'assistant', content: 'SESSION-A' }] }); await settle();
  check(history.state.messages[0].content === 'SESSION-B', 'late history A cannot replace selected B');

  const stream = chatScope(); const wire = deferred(); stream.scope.fetch = () => wire.promise;
  const pending = bind('sendMessage', stream.scope)();
  bind('stopStream', stream.scope)();
  stream.state.messages = [{ role: 'assistant', content: 'SESSION-B' }];
  stream.scope.sendingRef.current = true; stream.state.streaming = true;
  const nextAbort = new AbortController(); stream.scope.abortRef.current = nextAbort;
  wire.resolve(response('event: delta\ndata: {"text":"FROM-A"}\n\n')); await pending;
  check(stream.state.messages[0].content === 'SESSION-B', 'late stream A cannot patch B');
  check(stream.state.streaming && stream.scope.sendingRef.current && stream.scope.abortRef.current === nextAbort, 'old finally cannot release next send');

  const double = chatScope(); double.scope.activeSession = null;
  const created = []; double.scope.Bellomberg.chatCreateSession = () => { const wait = deferred(); created.push(wait); return wait.promise; };
  const send = bind('sendMessage', double.scope); const first = send(); const second = send();
  check(created.length === 1, 'double send before create resolves makes one session');
  bind('stopStream', double.scope)(); created[0].resolve({ session_id: 3 }); await Promise.all([first, second]);
  check(!double.state.streaming && !double.scope.sendingRef.current, 'cancel during create releases send');

  const eof = chatScope(); eof.scope.fetch = () => Promise.resolve(response('event: delta\ndata: {"text":"partial"}\n\n'));
  await bind('sendMessage', eof.scope)();
  check(eof.state.messages[1].ok === false && !eof.state.messages[1].streaming && eof.state.messages[1].errore.includes('incompleta'), 'EOF without done declared incomplete');
  const crlf = chatScope(); crlf.scope.fetch = () => Promise.resolve(response('event: delta\r\ndata: {"text":"complete"}\r\n\r\nevent: done\r\ndata: {"ok":true}\r\n\r\n'));
  await bind('sendMessage', crlf.scope)();
  check(crlf.state.messages[1].content === 'complete' && crlf.state.messages[1].ok === true, 'CRLF SSE parsed with final confirmation');

  const frozen = chatScope(); frozen.scope.activeSession = null;
  const creating = deferred(); let sentHeaders;
  frozen.scope.Bellomberg.chatCreateSession = () => creating.promise;
  frozen.scope.fetch = (_url, options) => { sentHeaders = options.headers; return Promise.resolve(response('event: meta\ndata: {"output_language":"it"}\n\nevent: done\ndata: {"ok":true}\n\n')); };
  language.impostaLinguaCorrente('it');
  localStorage.setItem('bellomberg_token_v1', 'synthetic-before-await');
  const sending = bind('sendMessage', frozen.scope)();
  language.impostaLinguaCorrente('en'); localStorage.setItem('bellomberg_token_v1', 'synthetic-after-await');
  creating.resolve({ session_id: 9 }); await sending;
  check(sentHeaders['X-BB-Language'] === 'it', 'chat stream language captured before session creation await');
  check(sentHeaders['X-BB-Token'] === 'synthetic-before-await', 'authentication snapshot belongs to the same send');
  check(frozen.state.messages[1].output_language === 'it', 'actual stream language read back from meta');
  check(frozen.state.messages[0].output_language == null, 'user prose is not attested from the interface language');
  const langHistory = chatScope();
  langHistory.scope.Bellomberg.chatGetSession = () => Promise.resolve({ messages: [
    { role: 'assistant', content: 'ORIGINAL LEGACY', output_language: null },
    { role: 'assistant', content: 'ORIGINAL ENGLISH', output_language: 'en' },
  ] });
  bind('history', langHistory.scope)(); await settle();
  check(langHistory.state.messages[0].content === 'ORIGINAL LEGACY' && langHistory.state.messages[0].output_language == null, 'legacy text and unknown language preserved');
  check(langHistory.state.messages[1].content === 'ORIGINAL ENGLISH' && langHistory.state.messages[1].output_language === 'en', 'historical output language preserved without translating text');
  language.impostaLinguaCorrente('it');
  const archiveFailure = chatScope();
  archiveFailure.scope.Bellomberg.chatListSessions = () => Promise.reject({ message: 'synthetic archive failure' });
  archiveFailure.scope.fetch = () => Promise.resolve(response('event: done\ndata: {"ok":true}\n\n'));
  await bind('sendMessage', archiveFailure.scope)(); await settle();
  check(archiveFailure.state.sessionsError?.detail?.includes('synthetic archive failure'), 'archive refresh failure remains visible after a completed reply');
  console.log('release contracts: ' + checks + ' checks passed; no network, Electron, or private data');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
