const { test } = require('node:test');
const assert = require('node:assert/strict');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');
ambienteBrowser();
const load = creaCaricatore();
const fixture = () => ({ value: 1234.5, original: 'Testo originale', dates: ['2001-01-01'], series: [100, 101], notes: ['Serie incompleta'],
  _presentation_v1: { version: 1, texts: [{ path: ['notes', 0], it: 'Serie incompleta', en: 'Incomplete series' }] } });

test('API cached output switches language locally without mutating original text, values or raw state', () => {
  const { localizePayload } = load('lib/api-presentation.ts');
  const raw = fixture(), before = structuredClone(raw);
  const en = localizePayload(raw, 'en'), it = localizePayload(en, 'it');
  assert.deepEqual(en.notes, ['Incomplete series']);
  assert.deepEqual(it, raw); assert.deepEqual(raw, before);
  assert.equal(en.value, 1234.5); assert.equal(en.original, 'Testo originale');
  assert.equal(en.dates, raw.dates); assert.equal(en.series, raw.series);
  const historical = { notes: ['Appunto personale'] };
  assert.equal(localizePayload(historical, 'en'), historical);
});

test('invalid presentation metadata cannot overwrite financial fields or modify object prototypes', () => {
  const { localizePayload } = load('lib/api-presentation.ts');
  for (const path of [['value'], ['missing'], ['__proto__', 'polluted'], ['notes', 2], ['_presentation_v1', 'version']]) {
    const raw = fixture(); raw._presentation_v1.texts[0].path = path;
    assert.throws(() => localizePayload(raw, 'en'), /presentation|presentazione/i);
    assert.equal(raw.value, 1234.5);
  }
  assert.equal({}.polluted, undefined);
  const raw = fixture(); raw.notes = ['Changed since metadata was built'];
  assert.throws(() => localizePayload(raw, 'en'), /presentation|presentazione/i);
  const duplicate = fixture(); duplicate._presentation_v1.texts.push(duplicate._presentation_v1.texts[0]);
  assert.throws(() => localizePayload(duplicate, 'en'), /presentation|presentazione/i);
});
