// t(): interpolazione, chiave mancante DICHIARATA (marcatore visibile + console.error),
// lingua corrente, persistenza, <html lang>, formato per lingua.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');

const { memoria } = ambienteBrowser();
const carica = creaCaricatore();
const lingua = carica('i18n/lingua.ts');
const t = carica('i18n/t.ts');
const formato = carica('lib/format.ts');

function conConsoleError(fn) {
  const raccolti = [];
  const orig = console.error;
  console.error = (...a) => raccolti.push(a.map(String).join(' '));
  try { fn(); } finally { console.error = orig; }
  return raccolti;
}

test('traduci sceglie il dizionario della lingua e interpola {nome}', () => {
  assert.equal(t.traduci('it', 'lingua.italiano'), 'ITALIANO');
  assert.equal(t.traduci('en', 'lingua.inglese'), 'ENGLISH');
  const it = t.traduci('it', 'layout.impostazioni_guasti', { tasto: 'F19', n: 2 });
  const en = t.traduci('en', 'layout.impostazioni_guasti', { tasto: 'F19', n: 2 });
  assert.match(it, /F19/); assert.match(it, /2 lavor/);
  assert.match(en, /F19/); assert.match(en, /2 scheduled/);
  assert.notEqual(it, en);
});

test('chiave mancante a runtime: marcatore visibile e console.error, mai un testo di ripiego', () => {
  const errori = conConsoleError(() => {
    assert.equal(t.traduci('en', 'layout.chiave_che_non_esiste'), '⟦layout.chiave_che_non_esiste⟧');
    assert.equal(t.traduci('en', 'spazio_inesistente.x'), '⟦spazio_inesistente.x⟧');
    assert.equal(t.tDinamica('it', 'nav.' + 'pagina_finta'), '⟦nav.pagina_finta⟧');
  });
  assert.equal(errori.length, 3);
  assert.match(errori[0], /layout\.chiave_che_non_exists|layout\.chiave_che_non_esiste/);
});

test('parametro mancante nell\'interpolazione: il segnaposto resta visibile e viene dichiarato', () => {
  const errori = conConsoleError(() => {
    assert.equal(t.traduci('it', 'layout.impostazioni_guasti', { tasto: 'F19' }).includes('{n}'), true);
  });
  assert.equal(errori.length, 1);
});

test('t() senza lingua usa la lingua corrente; impostaLinguaCorrente aggiorna <html lang>', () => {
  lingua.impostaLinguaCorrente('en');
  assert.equal(t.t('lingua.italiano'), 'ITALIAN');
  assert.equal(globalThis.document.documentElement.lang, 'en');
  lingua.impostaLinguaCorrente('it');
  assert.equal(t.t('lingua.italiano'), 'ITALIANO');
  assert.equal(globalThis.document.documentElement.lang, 'it');
});

test('persistenza in localStorage sotto bellomberg.lingua; valore estraneo = nessuna scelta', () => {
  memoria.clear();
  assert.equal(lingua.leggiLinguaSalvata(), null);
  assert.equal(lingua.salvaLingua('en'), true);
  assert.equal(memoria.get('bellomberg.lingua'), 'en');
  assert.equal(lingua.leggiLinguaSalvata(), 'en');
  memoria.set('bellomberg.lingua', 'fr');
  const errori = conConsoleError(() => assert.equal(lingua.leggiLinguaSalvata(), null));
  assert.equal(errori.length, 1, 'il valore non riconosciuto viene dichiarato');
  memoria.clear();
});

test('formato per lingua: valuta e numeri seguono it-IT / en-GB, la firma storica resta', () => {
  lingua.impostaLinguaCorrente('it');
  assert.equal(formato.fmtEUR(1234.5), '1.234,50 €');
  assert.equal(formato.fmtNum(1234.5), '1.234,50');
  assert.equal(formato.fmtNum(1000, 0), '1.000', 'grouping is explicit even for four digits');
  lingua.impostaLinguaCorrente('en');
  assert.equal(formato.fmtEUR(1234.5), '€1,234.50');
  assert.equal(formato.fmtNum(1234.5), '1,234.50');
  assert.equal(formato.fmtNum(1000, 0), '1,000');
  assert.equal(formato.fmtEUR(1234.5, true, 0, 'it'), '+1.235 €');
  assert.equal(formato.fmtEUR(null), 'n/a');
  assert.equal(formato.fmtNum(null), 'n/a');
  assert.equal(formato.fmtPct(1.5), '+1.50%');
  assert.equal(formato.fmtPct(1.5, true, 2, 'it'), '+1,50%', 'anche le percentuali seguono la lingua');
  assert.equal(formato.localeDi('it'), 'it-IT');
  assert.equal(formato.localeDi('en'), 'en-GB');
  const d = new Date(2026, 8, 12, 14, 5, 9);
  assert.equal(formato.fmtDataBreve(d, 'en'), '12 SEP 2026');
  assert.equal(formato.fmtDataBreve(d, 'it'), '12 SET 2026');
  assert.equal(formato.fmtOra(d, 'en'), '14:05:09');
  assert.equal(formato.fmtOra(d, 'it'), '14:05:09');
  lingua.impostaLinguaCorrente('it');
});

test('sottoscrizioni: cambio lingua notificato senza remount, nessun evento per la stessa lingua', () => {
  lingua.impostaLinguaCorrente('it');
  const viste = [];
  const stop = lingua.sottoscriviLingua(() => viste.push(lingua.linguaCorrente()));
  lingua.impostaLinguaCorrente('en');
  lingua.impostaLinguaCorrente('en');
  lingua.impostaLinguaCorrente('it');
  stop();
  lingua.impostaLinguaCorrente('en');
  assert.deepEqual(viste, ['en', 'it']);
  assert.throws(() => lingua.impostaLinguaCorrente('fr'), /Unsupported language/);
  assert.equal(lingua.linguaCorrente(), 'en');
  lingua.impostaLinguaCorrente('it');
});
