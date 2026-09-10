const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const root = path.resolve(__dirname, '../..');

function load(relative, overrides = {}) {
  const scope = { exports: {}, structuredClone, require: name => overrides[name] ?? require(name) };
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(root, relative), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX },
  }).outputText, scope);
  return scope.exports;
}
const presentation = load('src/lib/sector-valuation.ts');

function fixture() {
  return { file: 'VAL_SYNTH.X.xlsx', dir: 'report', engine: 'operating', ticker: 'SYNTH.X',
    canonical: true, identity_status: 'canonical', matched: false, flagged: false,
    snapshot_id: 'a'.repeat(64), generation_id: 'c1a47890-8b24-4ec8-86ae-d29ecdc23062',
    generated_at: '2026-09-10', fair_value: 1234.56, upside_pct: 14.7,
    sanity_severity: 'OK', analytical_quality: { status: 'DOCUMENTATA', issues: [] },
    valuation_usability: { usable: true, reasons: [], missing_fields: [] },
    valuation_decision: { method_id: 'operating_fcff', decision_status: 'resolved',
      support_status: 'integrated', requirements_status: 'complete', missing_fields: [],
      rationale: 'Synthetic method rationale.' },
    detail: { engine: 'operating', fair_value_weighted: 1234.56, sanity: { severity: 'OK' } } };
}

function render(model) {
  let state = 0;
  const hooks = { ...React, useEffect() {}, useMemo: fn => fn(),
    useState: initial => [state++ === 0 ? [model] : initial, () => {}] };
  const Page = load('src/pages/FundamentalsPage.tsx', {
    react: hooks, '@/lib/api': { Bellomberg: {}, API_BASE: 'http://synthetic.invalid' },
    '@/lib/sector-valuation': presentation,
  }).default;
  return renderToStaticMarkup(React.createElement(Page));
}

test('F17 renders an external canonical ticker, documented method and usable value', () => {
  const html = render(fixture());
  assert.match(html, /SYNTH\.X/);
  assert.match(html, /operating_fcff/);
  assert.match(html, /Synthetic method rationale/);
  assert.match(html, /1234\.56/);
  assert.match(html, />OK</);
  assert.match(html, /non nel book/);
});

test('unknown sanity and legacy page caches never render an OK or a fair value', () => {
  for (const changes of [{ sanity_severity: 'UNKNOWN' }, { valuation_usability: undefined },
      { valuation_decision: undefined }, { identity_status: 'legacy_unverified' }]) {
    const model = { ...fixture(), ...changes };
    const before = structuredClone(model);
    const html = render(model);
    assert.doesNotMatch(html, />OK</);
    assert.doesNotMatch(html, /1234\.56/);
    assert.deepEqual(model, before);
  }
});

test('blocked NAV and SOTP secondary values cannot revive through detail or UI cache', () => {
  const model = fixture();
  model.sanity_severity = 'BLOCK';
  model.detail = { engine: 'mnav', profile_key: 'cef_nav', fair_value_nav: 1901,
    fair_value_sotp: 2701, sotp_n_segments: 2, nav_per_share: 50, nav_target: 4,
    holding_irr: { irr: 1.99, by_scenario: { base: 2.99 } } };
  const normalized = presentation.prepareValuationModel(model);
  assert.equal(normalized.detail.fair_value_nav, null);
  assert.equal(normalized.detail.fair_value_sotp, null);
  assert.equal(normalized.detail.holding_irr.by_scenario.base, null);
  assert.equal(normalized.detail.nav_per_share, 50);
  const html = render(model);
  assert.match(html, />BLOCK</);
  assert.doesNotMatch(html, /1901|2701|1234\.56/);
});

test('incomplete research renders acquisition gaps and no fictitious Excel download', () => {
  const model = fixture();
  model.file = '';
  model.valuation_usability = { usable: false, reasons: ['Capital data absent'], missing_fields: ['capital_bridge'] };
  model.valuation_decision.support_status = 'calculator_only';
  model.acquisition_tasks = [{ field: 'capital_bridge', status: 'missing', reason: 'Issuer filing required' }];
  const html = render(model);
  assert.match(html, /capital_bridge/);
  assert.match(html, /Issuer filing required/);
  assert.match(html, /Capital data absent/);
  assert.doesNotMatch(html, /APRI EXCEL|1234\.56|>OK</);
});

test('managed care renders the real Python pipeline output with cutoff and capital gaps',
  { skip: !process.env.SECTOR_VALUATION_FIXTURE }, () => {
    const models = JSON.parse(fs.readFileSync(process.env.SECTOR_VALUATION_FIXTURE, 'utf8'));
    const complete = render(models.complete);
    assert.match(complete, /managed_care_distributable_equity/);
    assert.match(complete, /Cutoff flussi e prezzo: 2026-06-30/);
    assert.match(complete, /non FV storico o upside al prezzo corrente/);
    assert.match(complete, /APRI EXCEL/);
    assert.match(complete, />OK</);
    const incomplete = render(models.incomplete);
    assert.match(incomplete, /permitted_distribution/);
    assert.match(incomplete, /FV n.d./);
    assert.doesNotMatch(incomplete, />OK</);
    assert.equal(models.incomplete.detail.managed_care.scenarios.base.fair_value_per_share, null);
  });

test('documented sector valuation renders real complete and missing pipeline results',
  { skip: !process.env.DOCUMENTED_VALUATION_FIXTURE }, () => {
    const models = JSON.parse(fs.readFileSync(process.env.DOCUMENTED_VALUATION_FIXTURE, 'utf8'));
    const complete = render(models.complete);
    assert.ok(complete.includes(models.method));
    assert.match(complete, /APRI EXCEL/);
    assert.match(complete, />OK|>WARN/);
    assert.ok(complete.includes(models.complete.detail.valuation_date));
    const incomplete = render(models.incomplete);
    assert.ok(incomplete.includes(models.missing));
    assert.match(incomplete, /FV n.d./);
    assert.doesNotMatch(incomplete, />OK</);
    assert.equal(models.incomplete.fair_value, null);
  });
