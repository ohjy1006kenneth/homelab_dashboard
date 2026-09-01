import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../frontend/app.js', import.meta.url), 'utf8');

class FakeElement {
  constructor(id = '') {
    this.id = id;
    this.innerHTML = '';
    this.hidden = false;
    this.dataset = {};
    this.classList = { toggle() {}, add() {}, remove() {} };
  }
  addEventListener() {}
  removeEventListener() {}
  querySelector() { return null; }
  querySelectorAll() { return []; }
  closest() { return null; }
  getAttribute() { return ''; }
  setAttribute() {}
  getBoundingClientRect() { return { top: 0, width: 1, height: 1 }; }
}

const grid = new FakeElement('overview-app-grid');
const available = new FakeElement('available-app-list');
const elements = new Map([
  ['#overview-app-grid', grid],
  ['#available-app-list', available],
]);
const document = {
  documentElement: new FakeElement('documentElement'),
  body: new FakeElement('body'),
  querySelector(selector) { return elements.get(selector) || new FakeElement(selector); },
  querySelectorAll() { return []; },
  addEventListener() {},
};
const window = {
  location: { hash: '', origin: 'http://localhost' },
  innerHeight: 900,
  addEventListener() {},
  matchMedia() { return { matches: false }; },
  getComputedStyle() { return { display: 'block', visibility: 'visible', opacity: '1' }; },
  open() {},
};
const localStorage = {
  getItem() { return null; },
  setItem() {},
  removeItem() {},
};
const context = {
  document,
  window,
  localStorage,
  console,
  URL,
  URLSearchParams,
  FormData: class {},
  EventSource: class { close() {} },
  fetch: async () => { throw new Error('fetch should not run in this harness'); },
  requestAnimationFrame: (callback) => callback(),
  cancelAnimationFrame() {},
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
  alert() {},
  CSS: { escape: (value) => value },
};
vm.createContext(context);
// Boot is intentionally skipped, but all production declarations and handlers are evaluated.
const harness = source.replace(/^load\(\);$/m, 'if (!window.__DASHBOARD_TEST__) load();') + `
this.__dashboardLauncher = {
  draw() { drawOverviewApps(); },
  setData(nextApps, nextPinnedIds) { apps = nextApps; overviewPinnedAppIds = nextPinnedIds; },
  agentRefreshRoute,
  stockReviewOptionsRoute,
};`;
vm.runInContext(harness, context);

const apps = [
  { id: 'valid-a', name: 'Valid A', description: 'A', status: 'running', health: { ok: true }, web_ui_port: 1001, open_url: 'http://valid-a' },
  { id: 'valid-b', name: 'Valid B', description: 'B', status: 'running', health: { ok: true }, web_ui_port: 1002, open_url: 'http://valid-b' },
  { id: 'valid-c', name: 'Valid C', description: 'C', status: 'running', health: { ok: true }, web_ui_port: 1003, open_url: 'http://valid-c' },
];

function render(pinnedIds) {
  grid.innerHTML = '';
  available.innerHTML = '';
  context.__dashboardLauncher.setData(apps, pinnedIds);
  context.__dashboardLauncher.draw();
  return grid.innerHTML;
}

test('stale-only pinned IDs render fallback cards through the production launcher path', () => {
  const html = render(['deleted-a', 'deleted-b']);
  assert.match(html, /Valid A/);
  assert.match(html, /Valid B/);
  assert.match(html, /Valid C/);
  assert.doesNotMatch(html, /No apps available\./);
});

test('mixed valid and stale IDs render valid pinned order without fallback apps', () => {
  const html = render(['deleted-a', 'valid-b', 'deleted-b', 'valid-a']);
  assert.ok(html.indexOf('Valid B') < html.indexOf('Valid A'));
  assert.doesNotMatch(html, /Valid C/);
  assert.doesNotMatch(html, /No apps available\./);
});

test('bootstrap agent refresh uses the supported agent-list endpoint', () => {
  assert.equal(context.__dashboardLauncher.agentRefreshRoute(), '/api/agents');
  assert.doesNotMatch(source, /api\/agents\/mission-control/);
});

test('stock review options are lazy and route-scoped', () => {
  assert.equal(context.__dashboardLauncher.stockReviewOptionsRoute('overview'), null);
  assert.equal(context.__dashboardLauncher.stockReviewOptionsRoute('apps'), null);
  assert.equal(context.__dashboardLauncher.stockReviewOptionsRoute('stocks'), '/api/stocks/review-options');
});
