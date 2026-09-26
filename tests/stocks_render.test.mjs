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

const content = new FakeElement('content');
const elements = new Map([['.content', content]]);
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
const context = {
  document,
  window,
  localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
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
const harness = source.replace(/^load\(\);$/m, 'if (!window.__DASHBOARD_TEST__) load();') + `
this.__stocksRenderer = {
  render() { renderStocksPage(); },
  setState(audit, options, ticker) {
    stockAudit = audit;
    stockReviewOptions = options;
    stockSelectedReviewId = options.default_selection.id;
    stockSelectedTicker = ticker;
  },
};`;
vm.runInContext(harness, context);

const options = {
  candidates: [{
    id: 'packet-281',
    label: 'AAPL, AMD, NVDA, MSFT · 2026-09-04',
    run_id: 'packet-281',
    from_date: '2026-09-04',
    to_date: '2026-09-04',
    tickers: ['AAPL', 'AMD', 'NVDA', 'MSFT'],
  }],
  default_selection: {
    id: 'packet-281',
    run_id: 'packet-281',
    from_date: '2026-09-04',
    to_date: '2026-09-04',
    tickers: ['AAPL', 'AMD', 'NVDA', 'MSFT'],
  },
};
const injectedAudit = process.env.STOCK_AUDIT_RESPONSE
  ? JSON.parse(process.env.STOCK_AUDIT_RESPONSE)
  : null;

function review(ticker, articleCount) {
  return {
    ticker,
    run_id: 'packet-281',
    query: { from_date: '2026-09-04', to_date: '2026-09-04', ticker },
    producer_correlation: {
      run_id: 'packet-281',
      from_date: '2026-09-04',
      to_date: '2026-09-04',
      ticker,
    },
    status: 'reviewable_with_warnings',
    smoke_status: 'pass',
    cached_fallback_used: false,
    review_counts: {
      input_article_count: articleCount + 1,
      sentence_row_count: articleCount * 3,
      reviewed_article_count: articleCount,
      relevance_row_count: articleCount * 2,
      accepted_decision_count: articleCount,
      borderline_decision_count: 0,
      rejected_decision_count: articleCount,
    },
    review_count_reasons: {},
    states: {
      ticker_association: { status: 'available', ticker_isolation_pass: true },
      relevance: { status: 'available', reason: `${ticker} relevance evidence preserved` },
      finbert: { status: 'available', row_count: articleCount * 3 },
      topic: { status: 'available', reason: `${ticker} topic evidence preserved` },
    },
    decision_reasons: {
      ticker_association: [],
      relevance: [`${ticker} relevance evidence preserved`],
      finbert: [],
      topic: [`${ticker} topic evidence preserved`],
    },
  };
}

test('packet-backed endpoint response renders the selected ticker review contract', () => {
  const audit = injectedAudit || {
    ok: true,
    status: 'pass',
    run_id: 'packet-281',
    query: {
      from_date: '2026-09-04',
      to_date: '2026-09-04',
      tickers: ['AAPL', 'AMD'],
    },
    payload: {
      tickers: {
        AAPL: { controls: { ticker: 'AAPL' } },
        AMD: { controls: { ticker: 'AMD' } },
      },
      errors: {},
    },
    reviews: [review('AAPL', 3), review('AMD', 5)],
    review_counts: {
      input_article_count: 10,
      sentence_row_count: 24,
      reviewed_article_count: 8,
      relevance_row_count: 16,
      accepted_decision_count: 8,
      borderline_decision_count: 0,
      rejected_decision_count: 8,
    },
    ticker_errors: {},
    review_options: options,
  };
  const selectedOptions = audit.review_options || options;
  const expectedReviewedArticles = audit.reviews.find((item) => item.ticker === 'AMD')
    .review_counts.reviewed_article_count;

  context.__stocksRenderer.setState(audit, selectedOptions, 'AMD');
  context.__stocksRenderer.render();

  assert.match(content.innerHTML, /AMD evidence review/);
  assert.match(content.innerHTML, new RegExp(`Reviewed articles</span><strong>${expectedReviewedArticles}</strong>`));
  if (!injectedAudit) {
    assert.doesNotMatch(content.innerHTML, /Reviewed articles<\/span><strong>8<\/strong>/);
    assert.match(content.innerHTML, /AMD relevance evidence preserved/);
  }
  assert.match(content.innerHTML, new RegExp(audit.run_id));
  assert.match(content.innerHTML, new RegExp(`${audit.query.from_date} → ${audit.query.to_date}`));
  assert.match(content.innerHTML, /Packet ticker payload<\/span><strong>loaded<\/strong>/);
});

test('controlled ticker errors remain visible with correlation metadata', () => {
  const failedReview = review('AMD', 0);
  failedReview.status = 'unavailable';
  failedReview.producer_correlation = {
    run_id: null,
    from_date: null,
    to_date: null,
    ticker: null,
  };
  const audit = {
    ok: false,
    status: 'fail',
    error: 'SemanticEvidenceUnavailable',
    reason: 'Evidence is unavailable for requested ticker(s): AMD.',
    run_id: 'packet-281',
    query: {
      from_date: '2026-09-04',
      to_date: '2026-09-04',
      tickers: ['AMD'],
    },
    payload: { tickers: {}, errors: { AMD: {
      error: 'PacketContractError',
      reason: 'Requested ticker evidence is missing from the selected packet.',
    } } },
    reviews: [failedReview],
    review_counts: failedReview.review_counts,
    ticker_errors: { AMD: {
      error: 'PacketContractError',
      reason: 'Requested ticker evidence is missing from the selected packet.',
    } },
    review_options: options,
  };

  context.__stocksRenderer.setState(audit, options, 'AMD');
  context.__stocksRenderer.render();

  assert.match(content.innerHTML, /AMD evidence review/);
  assert.match(content.innerHTML, /Not reviewable/);
  assert.match(content.innerHTML, /PacketContractError/);
  assert.match(content.innerHTML, /Requested ticker evidence is missing from the selected packet\./);
  assert.match(content.innerHTML, /packet-281/);
  assert.match(content.innerHTML, /2026-09-04 → 2026-09-04/);
});
