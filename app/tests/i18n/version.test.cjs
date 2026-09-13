const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser, apiFinta } = require('./_carica.cjs');

const app = path.resolve(__dirname, '../..');
const version = JSON.parse(fs.readFileSync(path.join(app, 'package.json'), 'utf8')).version;

test('login displays the actual frontend package version in both languages', () => {
  ambienteBrowser();
  const load = creaCaricatore({ stub: {
    react: { ...React, useState: initial => React.useState(initial === 'check' ? 'login' : initial) },
    '@/lib/api': apiFinta(),
  } });
  const Login = load('components/LoginGate.tsx').default;
  const language = load('i18n/lingua.ts');
  for (const selected of ['it', 'en']) {
    language.impostaLinguaCorrente(selected);
    const markup = renderToStaticMarkup(React.createElement(Login, {}, 'Synthetic child'));
    assert.ok(markup.includes('V' + version + ' OBSIDIAN'), 'login must identify the actual frontend version');
  }
});

test('Python API, package metadata and desktop lockfile declare the same version', () => {
  const root = path.dirname(app);
  const lock = JSON.parse(fs.readFileSync(path.join(app, 'package-lock.json'), 'utf8'));
  const python = fs.readFileSync(path.join(root, 'src/bellomberg/agents/bellomberg.py'), 'utf8');
  const project = fs.readFileSync(path.join(root, 'pyproject.toml'), 'utf8');
  assert.equal(lock.version, version);
  assert.equal(lock.packages[''].version, version);
  assert.equal(python.match(/^VERSION\s*=\s*"([^"]+)"/m)?.[1], version);
  assert.equal(project.match(/^version\s*=\s*"([^"]+)"/m)?.[1], version);
});
