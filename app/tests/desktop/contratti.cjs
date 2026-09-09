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
const { portfolioValues } = loadModule('src/lib/portfolio-values.ts');
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
function chatScope() {
  const state = { messages: [], streaming: false };
  const scope = {
    input: 'synthetic question', selectedAgent: { id: 'quant' }, streaming: false, messagesLoading: false, ritirato: null, activeSession: 1,
    setInput() {}, setPin() {}, setHot() {}, setActiveSession() {}, setMsgsErr() {}, setSessions() {}, setMessagesLoading() {},
    skipFetchRef: { current: null }, sendingRef: { current: false }, requestRef: { current: 0 }, abortRef: { current: null },
    setStreaming(value) { state.streaming = value; },
    setMessages(value) { state.messages = typeof value === 'function' ? value(state.messages) : value; },
    AbortController, TextDecoder, performance, console, getSessionToken() { return null; }, clearSessionAndReload() {},
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
  console.log('release contracts: ' + checks + ' checks passed; no network, Electron, or private data');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
