// Review of Andrea's PR #14 (Shell Nuova), Opus 5.5.
// 1) The Decisions badge in the sidebar: a failed read, or a reply without the list, must be a
//    declared gap (N.D.), not the same empty space as "no decisions to make" (rule 14/07).
//    Exercised through the real Layout, so the wiring of the labels is tested too.
// 2) The Dashboard run notice that the PR moves into the top bar: it must stay visible at
//    narrow widths and shrink instead of pushing search and system status out of the window.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { MemoryRouter } = require('react-router-dom');
const { creaCaricatore, ambienteBrowser, apiFinta, SRC } = require('./_carica.cjs');

function shellWithDecisions(decisions, { closedGroups = [], path: startPath = '/dashboard', filing = null } = {}) {
  ambienteBrowser();
  if (closedGroups.length) localStorage.setItem('bb.nav.gruppiChiusi', JSON.stringify(closedGroups));
  const oldWindow = global.window;
  // listeners captured so the tests can fire focus / visibilitychange / resize by hand
  const listeners = { window: {}, document: {} };
  const target = where => ({
    addEventListener(type, fn) { (listeners[where][type] ||= []).push(fn); },
    removeEventListener(type, fn) { listeners[where][type] = (listeners[where][type] || []).filter(f => f !== fn); },
  });
  global.window = { ...target('window'), dispatchEvent() {} };
  Object.assign(global.document, target('document'), { hidden: false });
  const intervals = [];
  const state = [], effects = [], deps = [], cleanups = [];
  let si = 0, ei = 0;
  const hooks = { ...React,
    useState(initial) {
      const at = si++;
      if (!(at in state)) state[at] = typeof initial === 'function' ? initial() : initial;
      return [state[at], value => { state[at] = typeof value === 'function' ? value(state[at]) : value; }];
    },
    useEffect(fn, values) {
      const at = ei++, before = deps[at];
      if (!before || !values || values.some((v, i) => !Object.is(v, before[i]))) effects.push(fn);
      deps[at] = values;
    },
  };
  const api = apiFinta();
  const Bellomberg = new Proxy({}, { get: (_t, name) => (name === 'decisions' ? decisions
    : name === 'filingNovita' && filing ? filing : api.Bellomberg[name]) });
  const load = creaCaricatore({ stub: { react: hooks, '@/lib/api': { ...api, Bellomberg },
    './SettingsPanel': { default: () => null, __esModule: true } } });
  const language = load('i18n/lingua.ts'), Layout = load('components/Layout.tsx').default;
  const render = lang => {
    si = ei = 0; effects.length = 0; language.impostaLinguaCorrente(lang);
    const previousError = console.error;
    console.error = (...args) => {
      if (!String(args[0]).includes('useLayoutEffect does nothing on the server')) previousError(...args);
    };
    try { return renderToStaticMarkup(React.createElement(MemoryRouter, { initialEntries: [startPath] }, React.createElement(Layout))); }
    finally { console.error = previousError; }
  };
  const oldSet = global.setInterval, oldClear = global.clearInterval;
  global.setInterval = (fn, ms) => { intervals.push({ fn, ms }); return intervals.length; };
  global.clearInterval = () => {};
  return {
    render, listeners, intervals,
    fire(where, type) { for (const fn of listeners[where][type] || []) fn(); },
    runEffects() { for (const effect of effects.splice(0)) { const c = effect(); if (c) cleanups.push(c); } },
    flush: () => new Promise(resolve => setImmediate(resolve)),
    close() { cleanups.splice(0).forEach(fn => fn()); global.setInterval = oldSet; global.clearInterval = oldClear; global.window = oldWindow; },
  };
}

// the badge and the screen-reader sentence that follows it
const badge = html => (html.match(/<b [^>]*data-decisions-badge="[^"]*"[^>]*>[^<]*<\/b>(<small class="sr-only">[^<]*<\/small>)?/) || [''])[0];
// the menu link of a destination, as rendered by NavLink in a MemoryRouter
const link = (html, to) => (html.match(new RegExp(`<a [^>]*href="${to}"[^>]*>.*?</a>`)) || [''])[0];
// accessible name computed from content, as a screen reader does here: aria-hidden subtrees
// out, every other text in; the link must not carry an aria-label that would replace it all
const accessibleName = a => a
  .replace(/<(\w+)[^>]*aria-hidden="true"[^>]*>.*?<\/\1>/g, ' ')
  .replace(/<svg.*?<\/svg>/g, ' ').replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/\s+/g, ' ').trim();

for (const [name, decisions] of [
  ['a failed read', () => Promise.reject(new Error('synthetic decisions failure'))],
  ['a reply without the decisions list', () => Promise.resolve({ error: 'synthetic' })],
]) test(`sidebar Decisions badge declares ${name} as N.D. in both languages`, async () => {
  const shell = shellWithDecisions(decisions);
  try {
    shell.render('en');
    shell.runEffects();
    await shell.flush(); await shell.flush();
    for (const [lang, nd, label] of [
      ['en', 'N/A', 'Decisions to make: count unavailable'],
      ['it', 'N.D.', 'Decisioni da prendere: conteggio non leggibile'],
    ]) {
      const b = badge(shell.render(lang));
      assert.match(b, /data-decisions-badge="nd"/, `${lang}: the gap is declared, not hidden: ${b}`);
      assert.ok(b.includes(`<small class="sr-only">, ${label}</small>`), `${lang}: ${b}`);
      assert.ok(b.includes(`>${nd}</b>`), `${lang}: ${b}`);
    }
  } finally { shell.close(); }
});

test('sidebar Decisions badge shows the pending count, and nothing when there are none', async () => {
  for (const [list, expected] of [[[{ id: 1 }, { id: 2 }, { id: 3 }], '3'], [[], null]]) {
    const shell = shellWithDecisions(() => Promise.resolve({ decisions: list }));
    try {
      shell.render('en');
      shell.runEffects();
      await shell.flush(); await shell.flush();
      const b = badge(shell.render('it'));
      if (expected == null) assert.equal(b, '', 'zero pending decisions: no badge');
      else {
        assert.match(b, new RegExp(`data-decisions-badge="${expected}"`));
        assert.ok(b.includes(`<small class="sr-only">, ${expected} decisioni da prendere</small>`), b);
      }
    } finally { shell.close(); }
  }
});

test('the run notice brought into the top bar stays visible and shrinks at narrow widths', () => {
  const css = fs.readFileSync(path.join(SRC, 'components/shell-modern.css'), 'utf8');
  const hides = [...css.matchAll(/([^{}]*\.bb-barra-ospite > header > :not\(([^)]*)\)[^{}]*)\{([^}]*)\}/g)]
    .filter(m => /display:\s*none/.test(m[3]));
  assert.ok(hides.length > 0, 'the narrow-width rule that hides the page context is still the one under test');
  for (const m of hides) assert.match(m[2], /\.bbn-run/, `narrow rule must spare the run notice: ${m[1].trim()}`);
  const shrink = css.match(/\.bb-barra-ospite > header > \.bbn-run\s*\{([^}]*)\}/);
  assert.ok(shrink, 'the run notice has its own rule in the top bar');
  assert.match(shrink[1], /flex:\s*0 1 auto/);
  assert.match(shrink[1], /min-width:\s*0/);
  const dashboard = fs.readFileSync(path.join(SRC, 'pages/Dashboard.tsx'), 'utf8');
  assert.match(dashboard, /className=\{'bbn-run'[^>]*title=\{runText\}/, 'a shortened notice keeps its full text in the tooltip');
});

// Second review round of PR #14 (Opus 5.5).
// 3) The menu link's accessible name includes the counter: no aria-label on the link (it would
//    replace the content, counter included), the visible number is aria-hidden and a sentence for
//    screen readers sits next to it, singular and plural in both languages.
test('the Decisions menu link is named with its counter, singular and plural, in both languages', async () => {
  for (const [list, names] of [
    [[{ id: 1 }, { id: 2 }, { id: 3 }], { it: 'Decisioni , 3 decisioni da prendere F', en: 'Decisions , 3 decisions to make F' }],
    [[{ id: 1 }], { it: 'Decisioni , 1 decisione da prendere F', en: 'Decisions , 1 decision to make F' }],
  ]) {
    const shell = shellWithDecisions(() => Promise.resolve({ decisions: list }));
    try {
      shell.render('en');
      shell.runEffects();
      await shell.flush(); await shell.flush();
      for (const lang of ['it', 'en']) {
        const html = shell.render(lang);
        assert.doesNotMatch(html, /<a [^>]*class="bb-modern-nav-link[^"]*"[^>]*aria-label=|<a [^>]*aria-label=[^>]*class="bb-modern-nav-link/,
          `${lang}: no menu link replaces its content with an aria-label`);
        const a = link(html, '/decisions');
        assert.ok(a, `${lang}: the Decisions link is rendered`);
        assert.ok(accessibleName(a).startsWith(names[lang]), `${lang}: ${accessibleName(a)} in ${a}`);
        assert.match(a, /<b [^>]*aria-hidden="true"[^>]*>\d+<\/b>/, `${lang}: the bare number is not read twice: ${a}`);
      }
    } finally { shell.close(); }
  }
});

test('the N.D. counter is part of the Decisions link name too', async () => {
  const shell = shellWithDecisions(() => Promise.reject(new Error('synthetic decisions failure')));
  try {
    shell.render('it');
    shell.runEffects();
    await shell.flush(); await shell.flush();
    const name = accessibleName(link(shell.render('it'), '/decisions'));
    assert.ok(name.startsWith('Decisioni , Decisioni da prendere: conteggio non leggibile'), name);
  } finally { shell.close(); }
});

// 4) Below 900 px the menu is icons only and the group buttons are hidden: the items of closed
//    groups must stay in the DOM and the narrow CSS must show them; labels and screen-reader
//    sentences are hidden from sight, never with display:none (that would empty the link name).
test('items of closed groups stay reachable in the icon-only menu below 900 px', () => {
  const shell = shellWithDecisions(() => new Promise(() => {}), { closedGroups: ['Comitato'] });
  try {
    const html = shell.render('it');
    const a = link(html, '/decisions');
    assert.match(a, /class="bb-modern-nav-link is-collapsed"/, `closed group: the item is collapsed, not removed: ${a}`);
    assert.match(html, /<div class="bb-nav-group is-closed" role="group" aria-label="Comitato">/);
  } finally { shell.close(); }
  const css = fs.readFileSync(path.join(SRC, 'components/shell-modern.css'), 'utf8');
  assert.match(css, /\n\.bb-modern-nav-link\.is-collapsed\s*\{\s*display:\s*none;\s*\}/, 'wide menu: collapsed items hidden');
  const narrow = css.match(/@media \(max-width: 900px\) \{([\s\S]*?)\n\}/);
  assert.ok(narrow, 'the 900 px rule is still the one under test');
  assert.match(narrow[1], /\.bb-modern-nav-link\.is-collapsed\s*\{\s*display:\s*flex;\s*\}/, 'narrow menu: collapsed items shown');
  assert.match(narrow[1], /\.bb-modern-nav-link > :not\(svg, span:first-of-type, \.sr-only, \.bb-nav-count, \.bb-nav-badge\)[^{]*\{\s*display:\s*none/,
    'narrow menu: the label, the screen-reader sentences and the counters are not display:none');
  assert.match(narrow[1], /\.bb-modern-nav-link > span:first-of-type\s*\{[^}]*position:\s*absolute[^}]*clip:/,
    'narrow menu: the label is visually hidden but still names the link');
});

// 5) The Decisions counter refreshes on its own: back to the window (focus / visibilitychange,
//    one read even when both arrive together) and every few minutes while visible. Never on
//    resize: the desktop captures count requests across window sizes.
test('the Decisions counter refreshes on focus and visibility, not on resize, without doubled reads', async () => {
  let reads = 0, n = 2;
  const shell = shellWithDecisions(() => { reads++; return Promise.resolve({ decisions: Array.from({ length: n }, (_, i) => ({ id: i })) }); });
  // the wall clock and the monotonic clock move together here; the next test splits them
  const realNow = Date.now, realPerf = performance.now;
  let now = realNow();
  Date.now = () => now;
  performance.now = () => now;
  try {
    shell.render('en');
    shell.runEffects();
    await shell.flush(); await shell.flush();
    assert.equal(reads, 1, 'one read on mount');
    const every = shell.intervals.map(i => i.ms).filter(ms => ms >= 3 * 60 * 1000 && ms <= 10 * 60 * 1000);
    assert.ok(every.length >= 1, `a periodic read every few minutes: ${JSON.stringify(shell.intervals.map(i => i.ms))}`);
    shell.fire('window', 'focus');
    assert.equal(reads, 1, 'a focus right after a read does not read again');
    now += 60 * 1000;
    shell.fire('window', 'resize');
    assert.equal(reads, 1, 'resizing the window never reads the decisions');
    n = 5;
    shell.fire('window', 'focus');
    shell.fire('document', 'visibilitychange');
    await shell.flush(); await shell.flush();
    assert.equal(reads, 2, 'focus + visibilitychange together make one read');
    assert.match(badge(shell.render('en')), /data-decisions-badge="5"/, 'the counter shows the new count without changing page');
    now += 60 * 1000;
    global.document.hidden = true;
    shell.fire('document', 'visibilitychange');
    for (const i of shell.intervals.filter(i => i.ms >= 3 * 60 * 1000)) i.fn();
    await shell.flush();
    assert.equal(reads, 2, 'a hidden window is not polled');
    global.document.hidden = false;
    shell.fire('document', 'visibilitychange');
    await shell.flush(); await shell.flush();
    assert.equal(reads, 3, 'coming back to a hidden window reads again');
  } finally { Date.now = realNow; performance.now = realPerf; shell.close(); }
});

// Review of PR #25 (Opus 5.5): the 15 s pause between two reads is a duration, so it is
// measured on the monotonic clock. On the wall clock a time sync that moves the clock back
// an hour would silence focus refreshes for that hour; one that moves it forward would let a
// doubled focus + visibilitychange through.
test('the pause between two Decisions reads ignores wall-clock jumps', async () => {
  let reads = 0;
  const shell = shellWithDecisions(() => { reads++; return Promise.resolve({ decisions: [{ id: 1 }] }); });
  const realNow = Date.now, realPerf = performance.now;
  let wall = realNow(), mono = 1000;
  Date.now = () => wall;
  performance.now = () => mono;
  try {
    shell.render('en');
    shell.runEffects();
    await shell.flush(); await shell.flush();
    assert.equal(reads, 1, 'one read on mount');
    wall -= 60 * 60 * 1000; mono += 60 * 1000;
    shell.fire('window', 'focus');
    await shell.flush(); await shell.flush();
    assert.equal(reads, 2, 'a minute later the focus reads again, even if the wall clock went back an hour');
    wall += 2 * 60 * 60 * 1000; mono += 1000;
    shell.fire('window', 'focus');
    await shell.flush(); await shell.flush();
    assert.equal(reads, 2, 'a second later no new read, even if the wall clock jumped forward');
  } finally { Date.now = realNow; performance.now = realPerf; global.document.hidden = false; shell.close(); }
});

// Third review round of PR #14 (Opus 5.5).
// 6) Below 900 px the counters are the only sign left on the icon-only menu: the Decisions counter
//    (N.D. included) and the Filing badge sit on the icon corner instead of disappearing.
test('below 900 px the Decisions counter, N.D. included, and the Filing badge sit on the icon', () => {
  const css = fs.readFileSync(path.join(SRC, 'components/shell-modern.css'), 'utf8');
  const narrow = css.match(/@media \(max-width: 900px\) \{([\s\S]*?)\n\}/);
  assert.ok(narrow, 'the 900 px rule is still the one under test');
  const rules = [...narrow[1].replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^}]*)\}/g)].map(m => ({ sel: m[1].trim(), body: m[2] }));
  // no narrow rule hides a counter: the catch-all spares them and nothing else names them with display:none
  for (const r of rules.filter(r => /display:\s*none/.test(r.body))) {
    for (const part of r.sel.split(/,(?![^(]*\))/).map(x => x.trim()).filter(x => x.startsWith('.bb-modern-nav-link'))) {
      assert.doesNotMatch(part.replace(/:not\([^)]*\)/g, ''), /bb-nav-count|bb-nav-badge|is-unknown/, `a counter is hidden at narrow width: ${part}`);
      if (/:not\(/.test(part)) assert.match(part, /\.bb-nav-count/, `catch-all spares the Decisions counter: ${part}`);
      if (/:not\(/.test(part)) assert.match(part, /\.bb-nav-badge/, `catch-all spares the Filing badge: ${part}`);
    }
  }
  const corner = rules.find(r => /\.bb-modern-nav-link > \.bb-nav-count\b/.test(r.sel) && /position:\s*absolute/.test(r.body));
  assert.ok(corner, 'the counter is positioned on the icon corner');
  assert.match(corner.sel, /span\.bb-nav-badge/, 'the Filing badge shares the corner rule');
  assert.match(corner.body, /top:\s*\d+px/); assert.match(corner.body, /right:\s*\d+px/);
  assert.ok(rules.some(r => r.sel === '.bb-modern-nav-link' && /position:\s*relative/.test(r.body)),
    'the link is the positioning box of its counter');
  assert.ok(rules.some(r => /\.bb-nav-count\.is-unknown/.test(r.sel) && /--bbn-warn/.test(r.body)), 'N.D. keeps its warning tint on the icon');
});

test('below 900 px a failed read still marks the icon with N.D. and keeps the link name', async () => {
  const shell = shellWithDecisions(() => Promise.reject(new Error('synthetic decisions failure')), { closedGroups: ['Comitato'] });
  try {
    shell.render('it');
    shell.runEffects();
    await shell.flush(); await shell.flush();
    const a = link(shell.render('it'), '/decisions');
    assert.match(a, /class="bb-modern-nav-link is-collapsed"/, 'the collapsed item is the one shown as an icon');
    assert.match(a, /<b class="bb-nav-count is-unknown"[^>]*aria-hidden="true"[^>]*>N\.D\.<\/b>/, a);
    assert.ok(accessibleName(a).startsWith('Decisioni , Decisioni da prendere: conteggio non leggibile'), accessibleName(a));
  } finally { shell.close(); }
});

// 7) Above 900 px a closed group shows what its hidden items hold: the count (or N.D.) on the
//    group header and the same sentence in the name of the toggle button. The counters are read
//    once by the badges and handed up: closing a group or resizing never reads again.
const groupButton = (html, label) =>
  (html.match(new RegExp(`<button [^>]*class="bb-modern-nav-group"[^>]*>(?:(?!</button>).)*?${label}(?:(?!</button>).)*?</button>`)) || [''])[0];

test('a closed group shows the pending decisions on its header, with an accessible sentence', async () => {
  for (const [list, it, en] of [
    [[{ id: 1 }, { id: 2 }, { id: 3 }], 'Comitato , 3 decisioni da prendere', 'Committee , 3 decisions to make'],
    [[{ id: 1 }], 'Comitato , 1 decisione da prendere', 'Committee , 1 decision to make'],
  ]) {
    const shell = shellWithDecisions(() => Promise.resolve({ decisions: list }), { closedGroups: ['Comitato'] });
    try {
      shell.render('en');
      shell.runEffects();
      await shell.flush(); await shell.flush();
      for (const [lang, label, name] of [['it', 'Comitato', it], ['en', 'Committee', en]]) {
        const b = groupButton(shell.render(lang), label);
        assert.ok(b, `${lang}: group button rendered`);
        assert.match(b, new RegExp(`<b class="bb-nav-count bb-nav-group-count" data-group-badge="decisions"[^>]*aria-hidden="true">${list.length}</b>`), b);
        assert.equal(accessibleName(b), name, `${lang}: ${b}`);
      }
    } finally { shell.close(); }
  }
});

test('a closed group declares an unreadable decisions count as N.D. on its header', async () => {
  const shell = shellWithDecisions(() => Promise.resolve({ error: 'synthetic' }), { closedGroups: ['Comitato'] });
  try {
    shell.render('it');
    shell.runEffects();
    await shell.flush(); await shell.flush();
    for (const [lang, label, nd, name] of [
      ['it', 'Comitato', 'N.D.', 'Comitato , Decisioni da prendere: conteggio non leggibile'],
      ['en', 'Committee', 'N/A', 'Committee , Decisions to make: count unavailable'],
    ]) {
      const b = groupButton(shell.render(lang), label);
      assert.match(b, new RegExp(`<b class="bb-nav-count bb-nav-group-count is-unknown" data-group-badge="decisions"[^>]*>${nd}</b>`), b);
      assert.equal(accessibleName(b), name);
    }
  } finally { shell.close(); }
});

test('no group marker when the group is open, when nothing is pending, or when the item is the open page', async () => {
  const cases = [
    [{ closedGroups: [] }, [{ id: 1 }], 'open group: the counter is on the item'],
    [{ closedGroups: ['Comitato'] }, [], 'nothing pending: nothing on the header'],
    [{ closedGroups: ['Comitato'], path: '/decisions' }, [{ id: 1 }], 'the open page stays visible with its own counter'],
  ];
  for (const [options, list, why] of cases) {
    const shell = shellWithDecisions(() => Promise.resolve({ decisions: list }), options);
    try {
      shell.render('it');
      shell.runEffects();
      await shell.flush(); await shell.flush();
      const b = groupButton(shell.render('it'), 'Comitato');
      assert.doesNotMatch(b, /data-group-badge|sr-only/, `${why}: ${b}`);
      assert.equal(accessibleName(b), 'Comitato', why);
    } finally { shell.close(); }
  }
});

test('a closed Research group shows the Filing news on its header', async () => {
  const shell = shellWithDecisions(() => new Promise(() => {}), { closedGroups: ['Ricerca'], filing: () => Promise.resolve({ n: 2 }) });
  try {
    shell.render('it');
    shell.runEffects();
    await shell.flush(); await shell.flush();
    for (const [lang, label, name] of [
      ['it', 'Ricerca', 'Ricerca , 2 titoli con novità nei filing'],
      ['en', 'Research', 'Research , 2 holdings with new filing changes'],
    ]) {
      const b = groupButton(shell.render(lang), label);
      assert.match(b, /<b class="bb-nav-count bb-nav-group-count is-filing" data-group-badge="filing"[^>]*>2<\/b>/, b);
      assert.equal(accessibleName(b), name);
    }
  } finally { shell.close(); }
});

test('the group marker reuses the badges reads: no extra request, none on resize or re-render', async () => {
  let decisionReads = 0, filingReads = 0;
  const shell = shellWithDecisions(() => { decisionReads++; return Promise.resolve({ decisions: [{ id: 1 }, { id: 2 }] }); },
    { closedGroups: ['Comitato', 'Ricerca'], filing: () => { filingReads++; return Promise.resolve({ n: 1 }); } });
  try {
    shell.render('it');
    shell.runEffects();
    await shell.flush(); await shell.flush();
    assert.equal(decisionReads, 1, 'one decisions read on mount, shared by the item and the group header');
    assert.equal(filingReads, 1, 'one filing read on mount, shared by the item and the group header');
    for (let i = 0; i < 3; i++) {
      shell.fire('window', 'resize');
      const html = shell.render(i % 2 ? 'en' : 'it');
      shell.runEffects();
      await shell.flush();
      assert.match(html, /data-group-badge="decisions"[^>]*>2</);
      assert.match(html, /data-group-badge="filing"[^>]*>1</);
    }
    assert.equal(decisionReads, 1, 'resizing and re-rendering the header never read the decisions again');
    assert.equal(filingReads, 1, 'resizing and re-rendering the header never read the filings again');
  } finally { shell.close(); }
});
