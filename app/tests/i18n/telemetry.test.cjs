const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { MemoryRouter } = require('react-router-dom');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');

const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const catalogue = count => ({ agents: Array.from({ length: count }, (_, i) => ({ id: `desk_${i}` })),
  engines: { committee_r1_r2: 'synthetic-engine' } });
const active = () => ({ running: true, specialist_status: {
  desk_0: 'done', desk_1: 'done', desk_2: 'done', desk_3: 'done', desk_4: 'running', desk_5: 'running',
} });

// Exercise the actual Layout and its effects with independently released API replies.
// No clock timer is fired and all API calls are confined to these fake services.
function retained() {
  ambienteBrowser();
  const oldWindow = global.window;
  global.window = { addEventListener() {}, removeEventListener() {} };
  const list = deferred(), live = [deferred()], calls = { list: 0, live: 0 };
  const state = [], effects = [], deps = [], cleanups = [], timers = new Map();
  let si = 0, ei = 0, timerId = 0;
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
  const load = creaCaricatore({ stub: { react: hooks,
    './SettingsPanel': { default: () => null, __esModule: true },
    '@/lib/api': { Bellomberg: {
      agentsList: () => { calls.list++; return list.promise; },
      agentsLive: () => {
        const at = calls.live++;
        assert.ok(live[at], 'unexpected extra live request');
        return live[at].promise;
      },
      health: async () => ({}), fx: async () => ({ rates: {} }), scheduledTasks: async () => ({ tasks: [] }),
    } },
  } });
  const language = load('i18n/lingua.ts'), Layout = load('components/Layout.tsx').default;
  const render = (lang = 'en') => {
    si = ei = 0; effects.length = 0; language.impostaLinguaCorrente(lang);
    const previousError = console.error;
    let html;
    console.error = (...args) => {
      if (!String(args[0]).includes('useLayoutEffect does nothing on the server')) previousError(...args);
    };
    try { html = renderToStaticMarkup(React.createElement(MemoryRouter, {}, React.createElement(Layout))); }
    finally { console.error = previousError; }
    return html.match(/<footer\b[^>]*>([\s\S]*?)<\/footer>/)[1].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
  };
  const withTimers = fn => {
    const oldSet = global.setInterval, oldClear = global.clearInterval;
    global.setInterval = (callback, ms) => { const id = ++timerId; timers.set(id, { callback, ms }); return id; };
    global.clearInterval = id => timers.delete(id);
    try { return fn(); } finally { global.setInterval = oldSet; global.clearInterval = oldClear; }
  };
  const flush = () => new Promise(resolve => setImmediate(resolve));
  render();
  withTimers(() => { for (const effect of effects) { const cleanup = effect(); if (cleanup) cleanups.push(cleanup); } });
  return { list, live, calls, render, flush,
    poll: () => { for (const timer of timers.values()) if (timer.ms === 15000) timer.callback(); },
    close: () => { withTimers(() => cleanups.splice(0).forEach(fn => fn())); global.window = oldWindow; },
  };
}

for (const order of ['catalogue-first', 'live-first']) {
  test(`the run denominator stays with the observed run for ${order}`, async () => {
    const ui = retained();
    try {
      if (order === 'catalogue-first') {
        ui.list.resolve(catalogue(7)); await ui.flush();
        assert.doesNotMatch(ui.render(), /7 READY/, 'a catalogue alone does not prove idle');
        ui.live[0].resolve(active());
      } else {
        ui.live[0].resolve(active()); await ui.flush();
        assert.match(ui.render(), /4\/6 RUN/);
        ui.list.resolve(catalogue(7));
      }
      await ui.flush();
      assert.match(ui.render(), /4\/6 RUN/); assert.doesNotMatch(ui.render(), /4\/7 RUN/);
      assert.match(ui.render(), /SYNTHETIC ENGINE/);
      assert.deepEqual(ui.calls, { list: 1, live: 1 });
    } finally { ui.close(); }
  });
}

test('a late catalogue failure cannot erase or replace an observed live population', async () => {
  const ui = retained();
  try {
    ui.live[0].resolve(active()); await ui.flush();
    ui.list.reject(new Error('Synthetic catalogue unavailable')); await ui.flush();
    assert.match(ui.render(), /4\/6 RUN/); assert.match(ui.render(), /RUN IN PROGRESS/);
    assert.match(ui.render(), /Engine OFFLINE/, 'the failed engine source is still disclosed');
  } finally { ui.close(); }
});

test('missing or malformed live maps stay unknown and cannot borrow the catalogue size', async () => {
  for (const status of [undefined, null, [], ['done'], 'done', 9, { desk: null }, { desk: 'unexpected' }, { '': 'done' }]) {
    const ui = retained();
    try {
      ui.list.resolve(catalogue(7)); ui.live[0].resolve({ running: true, specialist_status: status }); await ui.flush();
      assert.match(ui.render(), /N\/A\/N\/A RUN/); assert.doesNotMatch(ui.render(), /0\/7|0\/0|null/);
      assert.match(ui.render('it'), /N\.D\.\/N\.D\. RUN/);
    } finally { ui.close(); }
  }
});

test('an explicitly empty live map is measured zero; idle totals use only the catalogue', async () => {
  for (const count of [0, 7]) {
    const ui = retained();
    try {
      ui.list.resolve(catalogue(count)); ui.live[0].resolve({ running: true, specialist_status: {} }); await ui.flush();
      assert.match(ui.render(), /0\/0 RUN/);
      const idle = deferred(); ui.live.push(idle); ui.poll(); idle.resolve({ running: false }); await ui.flush();
      assert.match(ui.render(), new RegExp(`${count} READY`)); assert.doesNotMatch(ui.render(), /RUN IN PROGRESS/);
    } finally { ui.close(); }
  }
});

test('unreadable or absent live state does not claim idle even when the catalogue succeeds', async () => {
  for (const response of [null, {}, { running: 'false' }, { running: false, heartbeat: 'illeggibile' }]) {
    const ui = retained();
    try {
      ui.live[0].resolve(response); await ui.flush(); ui.list.resolve(catalogue(7)); await ui.flush();
      assert.doesNotMatch(ui.render(), /READY|RUN IN PROGRESS/);
      assert.match(ui.render(), /Agents N\/A/);
    } finally { ui.close(); }
  }
});

test('a failed live refresh clears unverified counters and a later catalogue cannot restore them', async () => {
  const ui = retained();
  try {
    ui.live[0].resolve(active()); await ui.flush();
    const failure = deferred(); ui.live.push(failure); ui.poll(); failure.reject(new Error('Synthetic live source unavailable')); await ui.flush();
    ui.list.resolve(catalogue(7)); await ui.flush();
    assert.match(ui.render(), /Agents N\/A/); assert.doesNotMatch(ui.render(), /4\/6|READY|RUN IN PROGRESS/);
    assert.match(ui.render(), /OFFLINE/);
  } finally { ui.close(); }
});

test('only one telemetry poll can be outstanding and late replies after unmount do not change the view', async () => {
  const ui = retained();
  try {
    ui.list.resolve(catalogue(7)); await ui.flush();
    ui.poll(); ui.poll();
    assert.equal(ui.calls.live, 1);
    const before = ui.render(); ui.close(); ui.live[0].resolve(active()); await ui.flush();
    assert.equal(ui.render(), before);
  } finally { ui.close(); }
});
