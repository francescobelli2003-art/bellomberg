// Grammatica dei numeri scritti a mano, PER LINGUA. Le trappole vengono dal
// rapporto di ricognizione del 12/09 (prova_leggiNumero.cjs): con la grammatica
// italiana '1,234.56' valeva 1.23456 senza errore. Qui ogni forma ambigua o
// straniera deve produrre un ERRORE DICHIARATO, mai un numero mille volte diverso.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');

ambienteBrowser();
const carica = creaCaricatore();
const cassa = carica('lib/cassa.ts');
const mandato = carica('lib/mandato.ts');
const lingua = carica('i18n/lingua.ts');

const ok = v => ({ ok: true, valore: v });
const ko = () => 'errore';
const esito = r => (r == null ? null : r.ok ? r.valore : 'errore');

// [input, atteso IT, atteso EN] — 'errore' = rifiuto dichiarato (ok:false con motivo)
const TABELLA = [
  ['158,50',     158.5,    158.5],     // virgola: decimale in IT; in EN non puo' essere migliaia (2 cifre)
  ['1.234,56',   1234.56,  'errore'],  // grafia italiana
  ['1,234.56',   'errore', 1234.56],   // grafia inglese: la trappola misurata (era 1.23456)
  ['10,000.25',  'errore', 10000.25],  // idem (era 10.00025)
  ['1,000',      1.0,      'errore'],  // IT: uno virgola zero; EN: ambiguo (era 1 anche in EN)
  ['1.234',      'errore', 1.234],     // IT: ambiguo (regola storica); EN: decimale
  ['1,234',      1.234,    'errore'],  // specchio della riga sopra
  ['0.125',      0.125,    0.125],     // parte intera 0: mai migliaia
  ['0,125',      0.125,    0.125],
  ['3.850.000',  3850000,  'errore'],  // migliaia italiane
  ['3,850,000',  'errore', 3850000],   // migliaia inglesi
  ['1234.5',     1234.5,   1234.5],    // punto con 1 decimale: non e' mai migliaia
  ['1234,5',     1234.5,   1234.5],
  ['12.5',       12.5,     12.5],
  ['1.2.3,4',    'errore', 'errore'],  // punti fuori posizione (prima: 123.4 zitto in IT)
  ['1..2',       'errore', 'errore'],
  ['1,,2',       'errore', 'errore'],
  ['abc',        'errore', 'errore'],
  ['1234',       1234,     1234],
  ['',           null,     null],      // vuoto: non e' un errore, e' assenza
];

test('leggiNumero legge ogni forma secondo la grammatica della lingua indicata', () => {
  for (const [input, it, en] of TABELLA) {
    assert.equal(esito(cassa.leggiNumero(input, 'it')), it, `IT ${JSON.stringify(input)}`);
    assert.equal(esito(cassa.leggiNumero(input, 'en')), en, `EN ${JSON.stringify(input)}`);
  }
});

test('la stessa stringa non vale mai due numeri diversi a seconda della lingua', () => {
  // proprieta' di sicurezza: se entrambe le lingue accettano, il numero e' lo stesso
  for (const [input, it, en] of TABELLA) {
    if (typeof it === 'number' && typeof en === 'number') assert.equal(it, en, input);
  }
  for (const intero of ['0', '1', '12', '123', '1234']) {
    for (const decimali of ['0', '1', '25', '125', '1234']) {
      for (const separatore of [',', '.', ',0.', '.0,']) {
        const input = intero + separatore + decimali;
        const a = cassa.leggiNumero(input, 'it'), b = cassa.leggiNumero(input, 'en');
        if (a?.ok && b?.ok) assert.equal(a.valore, b.valore, input);
      }
    }
  }
});

test('il rifiuto dice come scrivere il numero, nella lingua della UI', () => {
  const it = cassa.leggiNumero('1.234', 'it');
  assert.equal(it.ok, false);
  assert.match(it.motivo, /1\.234 è ambiguo: scrivi 1234 oppure 1,234/);
  const en = cassa.leggiNumero('1,234', 'en');
  assert.equal(en.ok, false);
  assert.match(en.motivo, /1,234 is ambiguous: write 1234 or 1\.234/);
  const straniera = cassa.leggiNumero('1,234.56', 'it');
  assert.match(straniera.motivo, /1\.234,56/, 'suggerisce la grafia italiana');
  const stranieraEn = cassa.leggiNumero('1.234,56', 'en');
  assert.match(stranieraEn.motivo, /1,234\.56/, 'suggests the English spelling');
  assert.match(cassa.leggiNumero('0', 'en').motivo, /greater than zero/);
  assert.match(cassa.leggiNumero('0', 'it').motivo, /maggiore di zero/);
  assert.match(cassa.leggiNumero('x', 'en').motivo, /digits/);
});

test('senza lingua esplicita vale la lingua corrente della UI', () => {
  lingua.impostaLinguaCorrente('en');
  assert.equal(esito(cassa.leggiNumero('1,234.56')), 1234.56);
  lingua.impostaLinguaCorrente('it');
  assert.equal(esito(cassa.leggiNumero('1,234.56')), 'errore');
  assert.equal(esito(cassa.leggiNumero('1.234,56')), 1234.56);
});

test('la lingua del messaggio cambia senza reinterpretare la grammatica della bozza', () => {
  for (const [input] of TABELLA) {
    for (const grammar of ['it', 'en']) {
      const other = grammar === 'it' ? 'en' : 'it';
      assert.equal(esito(cassa.leggiNumero(input, grammar, other)), esito(cassa.leggiNumero(input, grammar)));
    }
  }
  assert.match(cassa.leggiNumero('1.234', 'it', 'en').motivo, /is ambiguous: write 1234 or 1,234/);
  assert.match(cassa.leggiNumero('1,234', 'en', 'it').motivo, /è ambiguo: scrivi 1234 oppure 1\.234/);
  assert.match(cassa.leggiNumero('1,234.56', 'it', 'en').motivo, /Use.*1\.234,56/);
  assert.match(cassa.leggiNumero('1.234,56', 'en', 'it').motivo, /Usa.*1,234\.56/);
  assert.match(cassa.leggiNumero('1.2.3,4', 'it', 'en').motivo, /1\.234\.567/);
  assert.match(cassa.leggiNumero('0', 'it', 'en').motivo, /greater than zero/);
  assert.match(cassa.leggiNumero('x', 'it', 'en').motivo, /digits/);
  assert.match(cassa.leggiNumeroConSegno('-', 'it', 'en').motivo, /sign/);
  assert.deepEqual(cassa.leggiNumeroConSegno('-1.234,56', 'it', 'en'), ok(-1234.56));
});

test('leggiNumeroConSegno: segno, zero e vuoto per lingua', () => {
  assert.deepEqual(cassa.leggiNumeroConSegno('-1,234.5', 'en'), ok(-1234.5));
  assert.deepEqual(cassa.leggiNumeroConSegno('-1.234,5', 'it'), ok(-1234.5));
  assert.deepEqual(cassa.leggiNumeroConSegno('0', 'en'), ok(0));
  assert.equal(cassa.leggiNumeroConSegno('', 'en'), null);
  assert.match(cassa.leggiNumeroConSegno('-', 'en').motivo, /sign/);
  assert.match(cassa.leggiNumeroConSegno('-', 'it').motivo, /segno/);
});

test('prezzoDaBook scrive nella grafia della lingua e leggiNumero lo rilegge (nessun rifiuto del prezzo precompilato)', () => {
  const pos = { prezzo_live: 12.345 };
  assert.equal(cassa.prezzoDaBook(pos, 'it'), '12,345');
  assert.equal(cassa.prezzoDaBook(pos, 'en'), '12.345');
  for (const l of ['it', 'en']) {
    for (const v of [12.345, 1234.5, 0.125, 158.5, 3.1]) {
      const scritto = cassa.prezzoDaBook({ prezzo_live: v }, l);
      assert.deepEqual(cassa.leggiNumero(scritto, l), ok(v), `${l} ${scritto}`);
    }
  }
  assert.equal(cassa.prezzoDaBook({ prezzo_live: null }, 'en'), '');
});

test('leggiNumeroMandato usa la stessa grammatica e traduce i motivi propri', () => {
  const pct = { tipo: 'pct', obbligatorio: true, intervallo: [0, 10] };
  assert.deepEqual(mandato.leggiNumeroMandato('0,5', pct, 'it'), ok(0.5));
  assert.deepEqual(mandato.leggiNumeroMandato('0.5', pct, 'en'), ok(0.5));
  assert.deepEqual(mandato.leggiNumeroMandato('0,5', pct, 'en'), ok(0.5));
  assert.equal(mandato.leggiNumeroMandato('1.234', pct, 'it').ok, false);
  assert.equal(mandato.leggiNumeroMandato('1,234', pct, 'en').ok, false);
  assert.match(mandato.leggiNumeroMandato('11', pct, 'en').motivo, /between 0 and 10/);
  assert.match(mandato.leggiNumeroMandato('11', pct, 'it').motivo, /fra 0 e 10/);
  assert.match(mandato.leggiNumeroMandato('1.2', { ...pct, tipo: 'int' }, 'en').motivo, /integer/);
  assert.match(mandato.leggiNumeroMandato('1,2', { ...pct, tipo: 'int' }, 'it').motivo, /intero/);
  assert.match(mandato.leggiNumeroMandato('', pct, 'en').motivo, /required/);
  assert.deepEqual(mandato.leggiNumeroMandato('', { ...pct, obbligatorio: false }, 'en'), { ok: true, valore: null });
});
