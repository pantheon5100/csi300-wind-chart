'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const site = path.join(__dirname, '../site');
const html = fs.readFileSync(path.join(site, 'index.html'), 'utf8');
const scripts = ['chart-controls.js', 'app.js'].map(file => fs.readFileSync(path.join(site, file), 'utf8'));
const fixedNow = Date.parse('2026-11-24T08:00:00Z');

function snapshot(asOf = '2026-11-19') {
  const dates = ['2004-12-31', '2026-11-02', '2026-11-03', '2026-11-04', '2026-11-05',
    '2026-11-06', '2026-11-09', '2026-11-10', '2026-11-11', '2026-11-12', '2026-11-13',
    '2026-11-16', '2026-11-17', '2026-11-18', '2026-11-19', '2026-11-20', '2026-11-23'];
  const data = dates.filter(date => date <= asOf).map((date, index) => {
    const stock = 1000 + index * 10, wind = 989.3 + index * 4;
    return [date, stock, wind, (stock / 1000 - 1) * 100, (wind / 989.3 - 1) * 100];
  });
  return {
    schema_version: 1,
    columns: ['date', 'stock_level', 'wind_level', 'stock_return_pct', 'wind_return_pct'],
    start_date: '2004-12-31', as_of: asOf, generated_at: '2026-11-20T00:00:00Z',
    baseline: { H00300: 1000, '885003.WI': 989.3, wind_approximate: true },
    sources: {
      H00300: { as_of: asOf, url: 'https://example.com/stock', name: 'Test stock source' },
      '885003.WI': { as_of: asOf, url: 'https://example.com/fund', name: 'Test fund source' },
    },
    data,
  };
}

// Only DOM operations actually used by the app are modeled. Elements with fixed
// IDs come from the real HTML, so a missing integration element fails this test.
async function boot(responses) {
  const nodes = new Map(), requests = [], queue = [...responses];
  class Element {
    constructor(tag = 'div') {
      this.tagName = tag;
      this.children = [];
      this.attributes = {};
      this.listeners = new Map();
      this.style = {};
      this.hidden = false;
      this.disabled = false;
      this.clientWidth = 900;
      this.offsetWidth = 225;
      this.offsetHeight = 85;
      this._text = '';
      const classes = new Set();
      this.classList = {
        toggle(name, enabled) { enabled ? classes.add(name) : classes.delete(name); },
        contains(name) { return classes.has(name); },
      };
    }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
    get childElementCount() { return this.children.length; }
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
    getBoundingClientRect() { return { left: 0, top: 0, width: this.clientWidth, height: 310 }; }
  }
  for (const match of html.matchAll(/<([a-z][\w-]*)\b[^>]*\bid="([^"]+)"[^>]*>/gi)) {
    const element = new Element(match[1]);
    element.setAttribute('id', match[2]);
    element.hidden = /\shidden(?:\s|>)/.test(match[0]);
    element.disabled = /\sdisabled(?:\s|>)/.test(match[0]);
  }
  const document = {
    baseURI: 'https://pantheon5100.github.io/csi300-wind-chart/', visibilityState: 'visible',
    getElementById: id => nodes.get(id) ?? null,
    createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag),
    createTextNode(text) { const node = new Element('#text'); node.textContent = text; return node; },
    addEventListener() {},
  };
  const window = { addEventListener() {} };
  class FixedDate extends Date {
    constructor(...args) { super(...(args.length ? args : [fixedNow])); }
    static now() { return fixedNow; }
  }
  let timer = 0;
  const context = vm.createContext({
    document, window, Date: FixedDate, Intl, URL, AbortController,
    setTimeout: () => ++timer, clearTimeout() {},
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
  await new Promise(resolve => setImmediate(resolve));
  return {
    nodes, requests,
    get: id => nodes.get(id),
    async click(id) {
      const target = nodes.get(id);
      assert.ok(target, `Missing button: ${id}`);
      for (const listener of target.listeners.get('click') || []) await listener({ target });
    },
  };
}

function descendants(element) {
  return element.children.flatMap(child => [child, ...descendants(child)]);
}
function paths(app, chart = 'returns') {
  return descendants(app.get(`${chart}-chart`)).filter(node => node.tagName === 'path').map(node => node.getAttribute('d'));
}
function endpointDates(app, chart = 'returns') {
  return descendants(app.get(`${chart}-chart`)).filter(node => node.className === 'endpoint-date').map(node => node.textContent);
}

test('actual frontend boots from empty state and draws all three charts through the latest date', async () => {
  const app = await boot([snapshot()]);
  assert.equal(app.get('as-of').textContent, '2026-11-19');
  assert.equal(app.get('charts').hidden, false);
  assert.equal(app.get('loading').hidden, true);
  assert.equal(app.get('refresh-button').disabled, false);
  assert.equal(app.get('data-revisions-details').hidden, true);
  assert.match(app.get('update-status').textContent, /已载入.*2026-11-19/);
  for (const chart of ['returns', 'levels', 'gap']) {
    assert.deepEqual(endpointDates(app, chart), ['2004-12-31', '2026-11-19']);
    assert.ok(paths(app, chart).length > 0);
    assert.ok(paths(app, chart).every(value => !/NaN|Infinity/.test(value)));
  }
  assert.equal(app.requests.length, 1);
  assert.equal(app.requests[0].options.cache, 'no-store');
});

test('manual refresh uses a new URL even within the same millisecond and reports an unchanged date', async () => {
  const app = await boot([snapshot(), snapshot()]);
  const before = paths(app);
  await app.click('refresh-button');
  assert.equal(app.requests.length, 2);
  assert.notEqual(app.requests[0].url, app.requests[1].url);
  assert.match(app.requests[1].url, /\/csi300-wind-chart\/data\.json\?refresh=/);
  assert.match(app.get('update-status').textContent, /2026-11-19.*暂无更晚/);
  assert.deepEqual(paths(app), before);
  assert.equal(app.get('refresh-button').disabled, false);
});

test('manual refresh advances an older displayed dataset to the newly published final record', async () => {
  const app = await boot([snapshot('2026-11-16'), snapshot()]);
  assert.equal(app.get('as-of').textContent, '2026-11-16');
  await app.click('refresh-button');
  assert.equal(app.get('as-of').textContent, '2026-11-19');
  assert.equal(app.get('current-deviation-date').textContent, '2026-11-19');
  assert.equal(endpointDates(app).at(-1), '2026-11-19');
  assert.match(app.get('update-status').textContent, /已刷新.*2026-11-19/);
});

test('an older response cannot roll back the visible chart, header, or latest indicator', async () => {
  const app = await boot([snapshot(), snapshot('2026-11-16')]);
  const before = paths(app);
  const indicator = app.get('current-deviation-value').textContent;
  await app.click('refresh-button');
  assert.equal(app.get('as-of').textContent, '2026-11-19');
  assert.equal(app.get('current-deviation-value').textContent, indicator);
  assert.deepEqual(paths(app), before);
  assert.equal(endpointDates(app).at(-1), '2026-11-19');
  assert.match(app.get('update-status').textContent, /较旧数据.*2026-11-16.*保留截至 2026-11-19/);
  assert.equal(app.get('update-status').classList.contains('error'), true);
  assert.equal(app.get('refresh-button').disabled, false);
});

test('a network failure preserves the chart and leaves manual retry usable', async () => {
  const app = await boot([snapshot(), new Error('Network unavailable'), snapshot()]);
  const before = paths(app);
  await app.click('refresh-button');
  assert.equal(app.get('as-of').textContent, '2026-11-19');
  assert.equal(app.get('charts').hidden, false);
  assert.deepEqual(paths(app), before);
  assert.match(app.get('update-status').textContent, /Network unavailable.*保留截至 2026-11-19/);
  assert.equal(app.get('refresh-button').disabled, false);
  await app.click('refresh-button');
  assert.equal(app.get('update-status').classList.contains('error'), false);
  assert.match(app.get('update-status').textContent, /2026-11-19.*暂无更晚/);
});

test('zoom and pan change the visible dates and summaries; latest selects the exact final record', async () => {
  const app = await boot([snapshot()]);
  await app.click('zoom-in');
  assert.deepEqual(endpointDates(app), ['2026-11-10', '2026-11-19']);
  await app.click('pan-earlier');
  for (const chart of ['returns', 'levels', 'gap']) {
    assert.deepEqual(endpointDates(app, chart), ['2026-11-04', '2026-11-13']);
    assert.equal(app.get(`${chart}-end-date`).textContent, '2026-11-13');
  }
  assert.equal(app.get('level-stock').textContent, '1,100.00 点');
  assert.equal(app.get('return-stock').textContent, '+10.0%');
  assert.equal(app.get('as-of').textContent, '2026-11-19');
  await app.click('show-latest');
  assert.equal(app.get('level-stock').textContent, '1,140.00 点');
  assert.match(app.get('chart-announcement').textContent, /已定位最新数据 2026-11-19/);
  for (const chart of ['returns', 'levels', 'gap']) {
    assert.equal(endpointDates(app, chart).at(-1), '2026-11-19');
    assert.equal(app.get(`${chart}-tooltip`).hidden, false);
    assert.equal(app.get(`${chart}-tooltip`).children[0].textContent, '2026-11-19');
  }
  await app.click('zoom-reset');
  assert.deepEqual(endpointDates(app), ['2004-12-31', '2026-11-19']);
  assert.equal(app.get('zoom-out').disabled, true);
});

test('cached data explains the conflicting date and old backup without confusing source checks with browser refreshes', async () => {
  const data = snapshot();
  data.update_status = { outcome: 'cached', checked_at: '2026-11-20T04:07:07Z', message: 'Retained existing data.' };
  data.sources['885003.WI'].mode = 'cached';
  data.sources['885003.WI'].checks = [
    { name: 'Primary mirror', role: 'primary', status: 'rejected', as_of: '2026-11-19', message: '885003.WI: source disagrees with verified history on 2026-11-18' },
    { name: 'FundDB', role: 'fallback', status: 'older', as_of: '2026-11-13' },
  ];
  const app = await boot([data, data]);
  const before = app.get('source-status').textContent;
  assert.equal(app.get('source-status').hidden, false);
  assert.equal(app.get('source-status').classList.contains('source-cached'), true);
  assert.match(before, /保留截至 2026-11-19 的已发布行情/);
  assert.match(before, /与已发布点位不一致（2026-11-18）/);
  assert.match(before, /备用来源：仅至 2026-11-13/);
  assert.match(before, /服务器最近检查：.*2026\/11\/20 12:07:07/);
  assert.doesNotMatch(before, /官方验证|verified official/);
  await app.click('refresh-button');
  assert.equal(app.get('source-status').textContent, before);
  assert.match(app.get('last-check').textContent, /2026\/11\/24/);
});

test('the chosen FundDB fallback is linked and labeled separately from the rejected primary', async () => {
  const data = snapshot();
  const primaryUrl = 'https://ashare.laoqianriritan.com/data/fund_index.json';
  const fallbackUrl = 'https://ai2.jiucaishuo.com/api/index-valuation/history?code=885003.WI&metric=close&years2=10';
  data.update_status = { outcome: 'updated', checked_at: '2026-11-24T04:00:00Z', message: '来源检查完成。' };
  Object.assign(data.sources['885003.WI'], {
    mode: 'fallback', provider_id: 'funddb', name: '韭圈儿 / FundDB', url: fallbackUrl,
    checks: [
      { name: '老钱日日谈', role: 'primary', provider_id: 'laoqian', status: 'rejected', url: primaryUrl, message: 'Wind: global source declares an H20269 fallback without explicit per-series 885003.WI provenance' },
      { name: '韭圈儿 / FundDB', role: 'fallback', provider_id: 'funddb', status: 'available', url: fallbackUrl, as_of: '2026-11-19' },
    ],
  });
  const app = await boot([data]);
  assert.equal(app.get('wind-source').href, fallbackUrl);
  assert.equal(app.get('wind-source').textContent, '韭圈儿 / FundDB');
  assert.match(app.get('wind-source-date').textContent, /备用来源/);
  assert.match(app.get('source-status').textContent, /第三方备用来源：韭圈儿 \/ FundDB/);
  const entries = app.get('source-options').children;
  assert.match(entries[0].textContent, /主来源.*未通过校验/);
  assert.doesNotMatch(entries[0].textContent, /本次使用/);
  assert.match(entries[1].textContent, /备用.*本次使用/);
  assert.deepEqual(descendants(app.get('source-options')).filter(node => node.tagName === 'a').map(node => node.href), [primaryUrl, fallbackUrl]);
  assert.match(html, /非 Wind 官方直连/);
});

test('ambiguous fallback provenance is described without declaring that the Wind series was definitely replaced', async () => {
  const data = snapshot();
  data.update_status = { outcome: 'cached', checked_at: '2026-11-24T04:00:00Z', message: '保留现有数据。' };
  Object.assign(data.sources['885003.WI'], {
    mode: 'cached', checks: [{ name: 'Primary', role: 'primary', status: 'rejected', message: 'Wind: global source declares an H20269 fallback without explicit per-series 885003.WI provenance; the global note does not establish that this Wind-labelled series was substituted' }],
  });
  const app = await boot([data]);
  assert.match(app.get('source-status').textContent, /全局说明提及 H20269.*未明确这条万得序列的来源/);
  assert.doesNotMatch(app.get('source-status').textContent, /已被.*替代|已替换|global source declares/);
});

test('reviewed corrections appear in a collapsed audit note with exact values and usable source links', async () => {
  const data = snapshot('2026-11-23');
  const changes = [
    { date: '2026-11-18', old: 4973.26, new: 4968.32 },
    { date: '2026-11-19', old: 4976.58, new: 4963.24 },
    { date: '2026-11-20', old: 4979.57, new: 4940.44 },
  ];
  for (const change of changes) {
    const row = data.data.find(row => row[0] === change.date);
    row[2] = change.new;
    row[4] = (change.new / data.baseline['885003.WI'] - 1) * 100;
  }
  data.data_revisions = [{
    id: 'wind-885003-20261124', index_code: '885003.WI', reviewed_at: '2026-11-24', applied_at: '2026-11-24T04:00:00Z',
    reason: '两家公开来源对三条点位一致，已复核修订；未取得 Wind 官方直连确认。', changes,
    sources: [
      { name: 'FundDB', url: 'https://ai2.jiucaishuo.com/api/index-valuation/history?code=885003.WI&metric=close&years2=10' },
      { name: '公开镜像', url: 'https://ashare.laoqianriritan.com/data/fund_index.json' },
      { name: 'Invalid link', url: 'javascript:alert(1)' },
    ],
  }];
  const app = await boot([data]);
  assert.equal(app.get('data-revisions-details').hidden, false);
  assert.equal(app.get('data-revisions-details').getAttribute('open'), null);
  assert.match(app.get('data-revisions-summary').textContent, /3 条点位/);
  const note = app.get('data-revisions-content').textContent;
  assert.match(note, /复核日期 2026-11-24/);
  assert.match(note, /2026-11-18：4,973.26 → 4,968.32 点/);
  assert.match(note, /2026-11-20：4,979.57 → 4,940.44 点/);
  assert.match(note, /未取得 Wind 官方直连确认/);
  const links = descendants(app.get('data-revisions-content')).filter(node => node.tagName === 'a');
  assert.equal(links.length, 2);
  assert.ok(links.every(link => link.href.startsWith('https://')));
  assert.equal(app.get('as-of').textContent, '2026-11-23');
});
