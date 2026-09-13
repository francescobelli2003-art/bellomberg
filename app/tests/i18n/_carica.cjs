// Caricatore dei moduli TypeScript dell'app per i test node di app/tests/i18n.
// Trascompila con il compilatore gia' in node_modules (come contratti.cjs) ma
// RISOLVE gli import: '@/x' -> src/x, './x.js' -> ./x.ts (stile NodeNext usato
// dentro src/i18n), '.css' -> modulo vuoto, pacchetti npm -> require vero.
// Cosi' i test eseguono le funzioni VERE con i dizionari VERI, senza stub
// nascosti: si stubba solo cio' che il chiamante dichiara (es. '@/lib/api').
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

const SRC = path.resolve(__dirname, '../../src');

function creaCaricatore({ stub = {} } = {}) {
  const cache = new Map();
  function risolvi(spec, daDir) {
    if (Object.prototype.hasOwnProperty.call(stub, spec)) return { stub: stub[spec] };
    if (/\.css$/.test(spec)) return { stub: {} };
    let base;
    if (spec.startsWith('@/')) base = path.join(SRC, spec.slice(2));
    else if (spec.startsWith('.')) base = path.resolve(daDir, spec);
    else return { esterno: spec };
    const candidati = [];
    for (const b of [base, base.replace(/\.js$/, '')]) {
      for (const ext of ['', '.ts', '.tsx', '/index.ts', '/index.tsx']) candidati.push(b + ext);
    }
    for (const c of candidati) {
      if (fs.existsSync(c) && fs.statSync(c).isFile()) return { file: c };
    }
    throw new Error('modulo non risolto: ' + spec + ' (da ' + daDir + ')');
  }
  function carica(file) {
    const abs = path.isAbsolute(file) ? file : path.join(SRC, file);
    if (cache.has(abs)) return cache.get(abs).exports;
    const sorgente = fs.readFileSync(abs, 'utf8');
    if (abs.endsWith('.json')) {
      const modulo = { exports: JSON.parse(sorgente) };
      cache.set(abs, modulo);
      return modulo.exports;
    }
    const js = ts.transpileModule(sorgente, {
      fileName: abs,
      compilerOptions: {
        module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
      },
    }).outputText;
    const modulo = { exports: {} };
    cache.set(abs, modulo);
    const richiedi = (spec) => {
      const r = risolvi(spec, path.dirname(abs));
      if ('stub' in r) return r.stub;
      if (r.esterno) return require(r.esterno);
      return carica(r.file);
    };
    new Function('exports', 'require', 'module', '__filename', '__dirname', js)(
      modulo.exports, richiedi, modulo, abs, path.dirname(abs));
    return modulo.exports;
  }
  return carica;
}

/** localStorage e document finti, per provare persistenza e <html lang> senza browser. */
function ambienteBrowser() {
  const memoria = new Map();
  globalThis.localStorage = {
    getItem: k => (memoria.has(k) ? memoria.get(k) : null),
    setItem: (k, v) => { memoria.set(k, String(v)); },
    removeItem: k => { memoria.delete(k); },
    clear: () => memoria.clear(),
  };
  globalThis.document = { documentElement: { lang: '' } };
  return { memoria };
}

/** Stub dichiarato di '@/lib/api': nessuna rete; ogni metodo resta in attesa. */
function apiFinta(risposte = {}) {
  const mai = () => new Promise(() => {});
  const Bellomberg = new Proxy({}, {
    get: (_t, nome) => (nome in risposte ? () => Promise.resolve(risposte[nome]) : mai),
  });
  return { Bellomberg, API_BASE: 'http://synthetic.invalid', TOKEN_STORAGE_KEY: 'bellomberg_token_v1' };
}

module.exports = { creaCaricatore, ambienteBrowser, apiFinta, SRC };
