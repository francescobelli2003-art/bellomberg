const { test } = require('node:test');
const assert = require('node:assert/strict');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');

function setup() {
  const { memoria } = ambienteBrowser();
  const load = creaCaricatore();
  const language = load('i18n/lingua.ts');
  language.impostaLinguaCorrente('it');
  return { memoria, language, preferences: load('i18n/preferenze.ts') };
}
const saved = language => ({ language, selected: true, source: 'preferences' });

test('profile is authoritative; a local cache never satisfies the first explicit choice', async () => {
  const { preferences, language, memoria } = setup();
  memoria.set('bellomberg.lingua', 'en');
  const result = await preferences.caricaPreferenza({ preferences: async () => ({ language: 'it', selected: false, source: 'compatibility_default' }) });
  assert.equal(result.selected, false);
  assert.equal(language.linguaCorrente(), 'it');
  assert.equal(memoria.get('bellomberg.lingua'), 'en', 'reading does not fabricate a saved choice');
  await preferences.caricaPreferenza({ preferences: async () => saved('en') });
  assert.equal(language.linguaCorrente(), 'en');
  assert.equal(memoria.get('bellomberg.lingua'), 'en');
});

test('language changes only after PUT and independent GET confirm the same stored value', async () => {
  const { preferences, language } = setup();
  const calls = [];
  const client = {
    savePreferences: async body => { calls.push(['put', body]); assert.equal(language.linguaCorrente(), 'it'); return saved('en'); },
    preferences: async () => { calls.push(['get']); assert.equal(language.linguaCorrente(), 'it'); return saved('en'); },
  };
  const result = await preferences.scegliLingua('en', client);
  assert.deepEqual(calls, [['put', { language: 'en' }], ['get']]);
  assert.equal(result.cacheSaved, true);
  assert.equal(language.linguaCorrente(), 'en');
  assert.equal(document.documentElement.lang, 'en');
});

test('failed save or stale readback leaves the active language and cached choice unchanged', async () => {
  for (const failure of ['put', 'get', 'drift', 'unselected', 'malformed']) {
    const { preferences, language, memoria } = setup();
    memoria.set('bellomberg.lingua', 'it');
    const client = {
      savePreferences: async () => { if (failure === 'put') throw new Error('disk full'); return saved('en'); },
      preferences: async () => {
        if (failure === 'get') throw new Error('network down');
        if (failure === 'drift') return saved('it');
        if (failure === 'unselected') return { language: 'it', selected: false, source: 'compatibility_default' };
        return { language: 'en', selected: 'yes', source: 'preferences' };
      },
    };
    await assert.rejects(preferences.scegliLingua('en', client));
    assert.equal(language.linguaCorrente(), 'it', failure);
    assert.equal(memoria.get('bellomberg.lingua'), 'it', failure);
  }
});

test('invalid preference payloads are declared; no malformed language becomes a default', async () => {
  for (const payload of [null, {}, saved('fr'), { ...saved('en'), source: 'guessed' }, { language: 'en', selected: false, source: 'preferences' }]) {
    const { preferences } = setup();
    await assert.rejects(preferences.caricaPreferenza({ preferences: async () => payload }), /Invalid preference response/);
  }
  const { preferences } = setup();
  await assert.rejects(preferences.scegliLingua('fr', {}), /Unsupported language/);
});

test('corrupt preference fingerprint is preserved for explicit repair, never automatically submitted', async () => {
  const { preferences } = setup();
  const original = { response: { status: 503, data: { detail: { code: 'language_preference_unavailable', message: 'Saved preferences unreadable', fingerprint: 'a'.repeat(64) } } } };
  await assert.rejects(preferences.caricaPreferenza({ preferences: async () => { throw original; } }), error => {
    assert.equal(error.message, 'Saved preferences unreadable');
    assert.equal(error.fingerprint, 'a'.repeat(64));
    return true;
  });
  let body;
  const client = { savePreferences: async b => { body = b; return { ...saved('en'), backup_created: true }; }, preferences: async () => saved('en') };
  const result = await preferences.scegliLingua('en', client, 'a'.repeat(64));
  assert.deepEqual(body, { language: 'en', repair_fingerprint: 'a'.repeat(64) });
  assert.equal(result.backupCreated, true);
});

test('browser cache failure is returned visibly while the verified profile remains authoritative', async () => {
  const { preferences, language } = setup();
  localStorage.setItem = () => { throw new Error('storage denied'); };
  const result = await preferences.scegliLingua('en', { savePreferences: async () => saved('en'), preferences: async () => saved('en') });
  assert.equal(result.cacheSaved, false);
  assert.equal(language.linguaCorrente(), 'en');
});
