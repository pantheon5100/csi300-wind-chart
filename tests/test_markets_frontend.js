'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const model = require('../site/markets.js');

const site = path.join(__dirname, '../site');
const html = fs.readFileSync(path.join(site, 'index.html'), 'utf8');
const scripts = ['chart-controls.js', 'markets.js'].map(file => fs.readFileSync(path.join(site, file), 'utf8'));
const fixedNow = Date.parse('2026-11-28T04:00:00Z');

function snapshot(asOf = '2026-11-27') {
  const dates = ['2014-12-31', '2015-01-02', '2020-07-24', '2020-07-27', '2025-11-27',
    '2026-11-02', '2026-11-03', '2026-11-04', '2026-11-05', '2026-11-06',
    '2026-11-09', '2026-11-10', '2026-11-11', '2026-11-12', '2026-11-13',
    '2026-11-16', '2026-11-17', '2026-11-18', '2026-11-19', '2026-11-20',
    '2026-11-23', '2026-11-24', '2026-11-25', '2026-11-26', '2026-11-27'].filter(date => date <= asOf);
  const definitions = {
    sse: ['000001.SH', '点', 'price_index', '中证指数官网', 3234.68, 20],
    gold: ['XAU', '美元/金衡盎司', 'spot_bid', 'Dukascopy XAU/USD 现货买价（日线，UTC）', 1194.123, 90],
    hstech: ['HSTECH', '点', 'price_index', '东方财富', 3000, 100],
  };
  const series = Object.fromEntries(Object.entries(definitions).map(([key, [code, unit, type, name, base, step]]) => {
    const points = dates.map((date, i) => [date, date === '2026-11-24' ? 4000 : Number((base + i * step).toFixed(3))]);
    // A real source calendar can have a Sunday UTC gold bar and no stock bar.
    if (key === 'gold' && asOf >= '2026-11-22') points.push(['2026-11-22', 2900.125]);
    points.sort((a, b) => a[0].localeCompare(b[0]));
    return [key, {
      code, name, unit, type, start_date: '2014-12-31', as_of: asOf,
      ...(key === 'hstech' ? { launch_date: '2020-07-27', backtest_before: '2020-07-27' } : {}),
      source: { provider_id: `test_${key}`, name, url: `https://example.com/${key}`, ...(key === 'sse' ? {} : { data_url: `https://example.com/feed/${key}?history=all` }), mode: 'live', checked_at: '2026-11-28T00:00:00Z' },
      data: points,
    }];
  }));
  return {
    schema_version: 1, dataset_id: 'sse-gold-hstech', start_date: '2014-12-31',
    as_of: asOf, common_as_of: asOf, generated_at: '2026-11-28T00:00:00Z', series,
    update_status: { checked_at: '2026-11-28T00:00:00Z', outcome: 'fresh', message: 'Updated.', warnings: [] },
  };
}

function recalculateDates(data) {
  for (const item of Object.values(data.series)) item.as_of = item.data.at(-1)[0];
  data.as_of = Object.values(data.series).map(item => item.as_of).sort().at(-1);
  const lookup = ['gold', 'hstech'].map(key => new Set(data.series[key].data.map(row => row[0])));
  data.common_as_of = data.series.sse.data.filter(row => lookup.every(set => set.has(row[0]))).at(-1)[0];
  return data;
}

// Fixed IDs are taken from the production HTML; methods model only actual DOM use.
async function boot(responses, width = 900) {
  const nodes = new Map(), requests = [], queue = [...responses], timers = new Map();
  const documentListeners = new Map(), windowListeners = new Map();
  class Element {
    constructor(tag = 'div') {
      this.tagName = tag; this.children = []; this.attributes = {}; this.listeners = new Map();
      this.style = {}; this.hidden = false; this.disabled = false; this.clientWidth = width;
      this.offsetWidth = 310; this.offsetHeight = 140; this._text = '';
      const classes = new Set();
      this.classList = {
        toggle(name, enabled) { enabled ? classes.add(name) : classes.delete(name); },
        contains(name) { return classes.has(name); },
      };
    }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
    setAttribute(name, value) {
      this.attributes[name] = String(value);
      if (name === 'id') { this.id = String(value); nodes.set(this.id, this); }
      if (name === 'class') this.className = String(value);
    }
    getAttribute(name) { return this.attributes[name] ?? null; }
    append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
    replaceChildren(...children) { this._text = ''; this.children = []; this.append(...children); }
    contains(target) { return target === this || this.children.some(child => child.contains(target)); }
    addEventListener(type, listener) {
      if (!this.listeners.has(type)) this.listeners.set(type, []);
      this.listeners.get(type).push(listener);
    }
    getBoundingClientRect() { return { left: 0, top: 0, width: this.clientWidth, height: width < 600 ? 360 : 420 }; }
  }
  for (const match of html.matchAll(/<([a-z][\w-]*)\b[^>]*\bid="([^"]+)"[^>]*>/gi)) {
    const item = new Element(match[1]); item.setAttribute('id', match[2]);
    item.hidden = /\shidden(?:\s|>)/.test(match[0]); item.disabled = /\sdisabled(?:\s|>)/.test(match[0]);
  }
  const addListener = map => (name, listener) => {
    if (!map.has(name)) map.set(name, []);
    map.get(name).push(listener);
  };
  const document = {
    baseURI: 'https://pantheon5100.github.io/csi300-wind-chart/', visibilityState: 'visible',
    getElementById: id => nodes.get(id) ?? null,
    createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag),
    addEventListener: addListener(documentListeners),
  };
  const window = { addEventListener: addListener(windowListeners) };
  let now = fixedNow, timer = 0;
  class FixedDate extends Date {
    constructor(...args) { super(...(args.length ? args : [now])); }
    static now() { return now; }
  }
  const context = vm.createContext({
    document, window, Date: FixedDate, Intl, URL, AbortController,
    setTimeout(callback, delay) { const id = ++timer; timers.set(id, { callback, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    requestAnimationFrame: () => 1, cancelAnimationFrame() {},
    ResizeObserver: class { observe() {} },
    async fetch(url, options) {
      requests.push({ url, options });
      if (!queue.length) throw new Error('Unexpected extra fetch');
      const response = queue.shift();
      if (response instanceof Error) throw response;
      return { ok: true, status: 200, async json() { return structuredClone(response); } };
    },
  });
  for (const script of scripts) vm.runInContext(script, context);
  const settle = () => new Promise(resolve => setImmediate(resolve));
  await settle();
  return {
    requests, nodes, document, timers, windowListeners, documentListeners,
    get: id => nodes.get(id), advance(ms) { now += ms; },
    async dispatch(target, name, extra = {}) {
      if (typeof target === 'string') target = nodes.get(target);
      assert.ok(target, `Missing event target: ${target}`);
      for (const listener of target.listeners.get(name) || []) await listener({ target, preventDefault() {}, ...extra });
      await settle();
    },
    async click(id) { return this.dispatch(id, 'click'); },
    async windowEvent(name, event = {}) {
      for (const listener of windowListeners.get(name) || []) await listener(event);
      await settle();
    },
    async visibility(state) {
      document.visibilityState = state;
      for (const listener of documentListeners.get('visibilitychange') || []) await listener({});
      await settle();
    },
  };
}

function descendants(element) { return element.children.flatMap(child => [child, ...descendants(child)]); }
function chartNodes(app) { return descendants(app.get('markets-chart')); }
function paths(app) { return chartNodes(app).filter(node => node.tagName === 'path'); }
function pathData(app) { return paths(app).map(node => node.getAttribute('d')); }
function endpointDates(app) { return chartNodes(app).filter(node => node.className === 'endpoint-date').map(node => node.textContent); }

test('actual independent frontend loads all three long histories and keeps full precision and source dates', async () => {
  const data = snapshot(), app = await boot([data]);
  assert.match(html, /<\/div>\s*<section id="markets"/);
  assert.equal(app.get('markets-display').hidden, false);
  assert.equal(app.get('markets-latest').hidden, false);
  assert.deepEqual(endpointDates(app), ['2014-12-31', '2026-11-27']);
  assert.deepEqual([...new Set(paths(app).map(node => node.getAttribute('data-series')))], ['sse', 'gold', 'hstech']);
  assert.ok(pathData(app).every(value => !/NaN|Infinity/.test(value)));
  for (const key of ['sse', 'gold', 'hstech']) {
    assert.equal(app.get(`markets-${key}-date`).textContent, '2026-11-27');
    assert.equal(app.get(`markets-${key}-source`).href, `https://example.com/${key}`);
    assert.match(app.get(`markets-${key}-source-date`).textContent, /截至 2026-11-27/);
  }
  assert.equal(app.get('markets-gold-value').textContent, '3,354.123');
  for (const key of ['gold', 'hstech']) {
    assert.equal(app.get(`markets-${key}-feed`).hidden, false);
    assert.equal(app.get(`markets-${key}-feed`).href, `https://example.com/feed/${key}?history=all`);
  }
  assert.equal(app.get('markets-sse-feed').hidden, true);
  assert.match(app.get('markets-status').textContent, /已载入.*2026-11-27/);
  assert.equal(app.get('markets-refresh-button').disabled, false);
  assert.equal(app.requests.length, 1);
  assert.equal(app.requests[0].options.cache, 'no-store');
  assert.match(app.requests[0].url, /\/markets-data\.json\?refresh=/);
  assert.equal([...app.timers.values()].filter(timer => timer.delay === 24 * 3600000).length, 1);
});

test('absolute raw values share identical left/right ticks and the same y transform', async () => {
  const app = await boot([snapshot()]);
  const ticks = chartNodes(app).filter(node => node.getAttribute('data-value') != null);
  const tickValues = axis => ticks.filter(node => node.getAttribute('data-axis') === axis).map(node => [node.getAttribute('data-value'), node.getAttribute('y'), node.textContent]);
  assert.deepEqual(tickValues('left'), tickValues('right'));
  assert.equal(app.get('markets-chart').getAttribute('data-y-min'), '0');
  assert.equal(app.get('markets-chart').getAttribute('data-y-max'), '6000');
  // All three fixtures equal 4000 on Nov 24. Walk from latest through exact dates.
  await app.dispatch('markets-chart', 'keydown', { key: 'End' });
  for (let i = 0; i < 3; i++) await app.dispatch('markets-chart', 'keydown', { key: 'ArrowLeft' });
  assert.equal(app.get('markets-tooltip').children[0].textContent, '2026-11-24');
  const dots = chartNodes(app).filter(node => node.getAttribute('data-hover-series'));
  assert.equal(new Set(dots.map(node => node.getAttribute('cy'))).size, 1);
  const expectedY = 382 - 4000 / 6000 * (382 - 44);
  assert.equal(Number(dots[0].getAttribute('cy')), expectedY);
  assert.match(app.get('markets-tooltip').textContent, /4,000\.000 美元\/金衡盎司/);
  assert.match(app.get('markets-tooltip').textContent, /4,000\.00 点/);
});

test('HSTECH history before launch is explicitly dashed, while current data is solid', async () => {
  const app = await boot([snapshot()]);
  const hstech = paths(app).filter(node => node.getAttribute('data-series') === 'hstech');
  const backtest = hstech.find(node => node.getAttribute('data-phase') === 'backtest');
  const live = hstech.find(node => node.getAttribute('data-phase') === 'live');
  assert.match(backtest.className, /markets-backtest-path/);
  assert.doesNotMatch(live.className, /markets-backtest-path/);
  assert.equal(backtest.getAttribute('data-points'), '4'); // Three prelaunch observations + real boundary point.
  assert.ok(chartNodes(app).some(node => node.getAttribute('data-launch-date') === '2020-07-27'));
  await app.dispatch('markets-chart', 'keydown', { key: 'Home' });
  assert.match(app.get('markets-tooltip').textContent, /2014-12-31.*恒生科技（回溯）.*3,000\.00 点/);
  assert.match(html, /2020-07-27.*2014-12-31—2020-07-24.*回溯数据/);
});

test('gold Sunday records stay on their own calendar and stock tooltips never forward fill', async () => {
  const data = snapshot(), app = await boot([data]);
  assert.equal(paths(app).find(node => node.getAttribute('data-series') === 'sse').getAttribute('data-points'), String(data.series.sse.data.length));
  assert.equal(paths(app).find(node => node.getAttribute('data-series') === 'gold').getAttribute('data-points'), String(data.series.gold.data.length));
  await app.dispatch('markets-chart', 'keydown', { key: 'End' });
  for (let i = 0; i < 5; i++) await app.dispatch('markets-chart', 'keydown', { key: 'ArrowLeft' });
  const tooltip = app.get('markets-tooltip');
  assert.equal(tooltip.children[0].textContent, '2026-11-22');
  assert.match(tooltip.children[1].textContent, /上证指数休市 \/ 无记录/);
  assert.match(tooltip.children[2].textContent, /2,900\.125 美元\/金衡盎司/);
  assert.match(tooltip.children[3].textContent, /恒生科技休市 \/ 无记录/);
  for (const key of ['sse', 'hstech']) {
    const dot = chartNodes(app).find(node => node.getAttribute('data-hover-series') === key);
    assert.equal(dot.getAttribute('visibility'), 'hidden');
  }
});

test('zoom, pan, latest, year, and all controls preserve latest cards and expose exact viewport dates', async () => {
  const app = await boot([snapshot()]);
  const initialValue = app.get('markets-sse-value').textContent;
  await app.click('markets-zoom-in');
  assert.equal(endpointDates(app).at(-1), '2026-11-27');
  assert.notEqual(endpointDates(app)[0], '2014-12-31');
  await app.click('markets-earlier');
  assert.notEqual(endpointDates(app).at(-1), '2026-11-27');
  assert.equal(app.get('markets-sse-value').textContent, initialValue);
  await app.click('markets-latest-button');
  assert.equal(endpointDates(app).at(-1), '2026-11-27');
  assert.equal(app.get('markets-tooltip').children[0].textContent, '2026-11-27');
  assert.match(app.get('markets-announcement').textContent, /已定位最新/);
  await app.click('markets-year');
  assert.deepEqual(endpointDates(app), ['2025-11-27', '2026-11-27']);
  await app.click('markets-all');
  assert.deepEqual(endpointDates(app), ['2014-12-31', '2026-11-27']);
  assert.match(app.get('markets-view-range').textContent, /2014-12-31 — 2026-11-27/);
  assert.equal(app.get('markets-zoom-out').disabled, true);
});

test('both manual buttons request new URLs, refresh latest data, and retain a narrowed window', async () => {
  const app = await boot([snapshot('2026-11-23'), snapshot(), snapshot()]);
  await app.click('markets-zoom-in');
  await app.click('markets-earlier');
  await app.click('markets-refresh-button');
  assert.equal(endpointDates(app).at(-1), '2026-11-27');
  assert.notEqual(endpointDates(app)[0], '2014-12-31');
  assert.equal(app.get('markets-sse-date').textContent, '2026-11-27');
  await app.click('refresh-button');
  assert.equal(app.requests.length, 3);
  assert.equal(new Set(app.requests.map(request => request.url)).size, 3);
  assert.match(app.get('markets-status').textContent, /暂无更新/);
});

test('a rollback in only one series is rejected even when the maximum dataset date is unchanged', async () => {
  const current = snapshot(), stale = snapshot();
  stale.series.hstech.data.pop(); recalculateDates(stale);
  assert.equal(stale.as_of, current.as_of);
  const app = await boot([current, stale]);
  const before = pathData(app);
  await app.click('markets-refresh-button');
  assert.deepEqual(pathData(app), before);
  assert.equal(app.get('markets-hstech-date').textContent, '2026-11-27');
  assert.match(app.get('markets-status').textContent, /较旧或不完整.*恒生科技只到 2026-11-26.*保留当前图表/);
  assert.equal(app.get('markets-status').classList.contains('error'), true);
});

test('cached individual series retain their own dates and show source reasons separately from browser checks', async () => {
  const partial = snapshot();
  partial.series.gold.data.pop();
  partial.series.gold.source.mode = 'cached'; partial.series.gold.source.error = 'Provider unavailable';
  partial.update_status.warnings = ['伦敦金现 沿用截至 2026-11-26 的已发布数据：Provider unavailable', 'Additional data notice'];
  partial.update_status.outcome = 'cached'; recalculateDates(partial);
  const app = await boot([partial, partial]);
  assert.equal(app.get('markets-sse-date').textContent, '2026-11-27');
  assert.equal(app.get('markets-gold-date').textContent, '2026-11-26');
  assert.match(app.get('markets-period').textContent, /共同.*2026-11-26/);
  assert.match(app.get('markets-source-status').textContent, /伦敦金现保留截至 2026-11-26.*Provider unavailable/);
  assert.equal(app.get('markets-source-status').textContent.split('Provider unavailable').length - 1, 1);
  assert.match(app.get('markets-source-status').textContent, /Additional data notice/);
  assert.equal(app.get('markets-source-status').hidden, false);
  assert.match(app.get('markets-server-check').textContent, /2026\/11\/28 08:00:00/);
  assert.match(app.get('markets-browser-check').textContent, /2026\/11\/28 12:00:00/);
  await app.click('markets-refresh-button');
  assert.match(app.get('markets-server-check').textContent, /08:00:00/);
  assert.match(app.get('markets-status').textContent, /伦敦金现使用保留数据/);
});

test('network or validation failures preserve the chart and allow an immediate retry', async () => {
  const invalid = snapshot(); invalid.series.gold.type = 'futures';
  const app = await boot([snapshot(), new Error('Network unavailable'), invalid, snapshot()]);
  const before = pathData(app);
  for (const failure of [/Network unavailable/, /伦敦金现的品种或单位不符/]) {
    await app.click('markets-refresh-button');
    assert.deepEqual(pathData(app), before);
    assert.equal(app.get('markets-display').hidden, false);
    assert.equal(app.get('markets-refresh-button').disabled, false);
    assert.match(app.get('markets-status').textContent, failure);
  }
  await app.click('markets-refresh-button');
  assert.equal(app.get('markets-status').classList.contains('error'), false);
});

test('visibility and back-forward restoration check published data without a dependency on the original dataset', async () => {
  const app = await boot([snapshot(), snapshot(), snapshot()]);
  await app.visibility('hidden');
  assert.equal(app.timers.size, 0);
  app.advance(24 * 3600000);
  await app.visibility('visible');
  assert.equal(app.requests.length, 2);
  await app.windowEvent('pageshow', { persisted: true });
  assert.equal(app.requests.length, 3);
});

test('narrow screens keep a valid shared scale and fully labeled endpoint dates', async () => {
  const app = await boot([snapshot()], 362);
  assert.equal(app.get('markets-chart').getAttribute('viewBox'), '0 0 362 360');
  assert.deepEqual(endpointDates(app), ['2014-12-31', '2026-11-27']);
  assert.ok(pathData(app).every(value => !/NaN|Infinity/.test(value)));
});

test('data checks reject substituted instruments, malformed dates, unsafe sources and dropped historical days', () => {
  const wrong = snapshot(); wrong.series.gold.code = 'GC';
  assert.throws(() => model.validateSnapshot(wrong), /品种或单位/);
  const date = snapshot(); date.series.sse.data[1][0] = '2015-02-30';
  assert.throws(() => model.validateSnapshot(date), /日期无效/);
  const url = snapshot(); url.series.sse.source.url = 'javascript:alert(1)';
  assert.throws(() => model.validateSnapshot(url), /来源链接无效/);
  const feed = snapshot(); feed.series.gold.source.data_url = 'javascript:alert(1)';
  assert.throws(() => model.validateSnapshot(feed), /来源链接无效/);
  const partial = snapshot(); partial.series.gold.data.splice(3, 1);
  assert.match(model.rollbackReason(partial, snapshot()), /缺少已发布的历史交易日/);
  const oldGenerated = snapshot(); oldGenerated.generated_at = '2026-11-27T00:00:00Z';
  assert.match(model.rollbackReason(oldGenerated, snapshot()), /生成时间/);
});
