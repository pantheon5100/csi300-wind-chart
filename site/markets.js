(() => {
  'use strict';

  const KEYS = ['sse', 'gold', 'hstech'];
  const START = '2014-12-31';
  const LAUNCH = '2020-07-27';
  const DAY = 86400000;
  const SERIES = {
    sse: { name: '上证指数', code: '000001.SH', unit: '点', type: 'price_index', color: '#236da8' },
    gold: { name: '伦敦金现', code: 'XAU', unit: '美元/金衡盎司', type: 'spot_bid', color: '#a66d08' },
    hstech: { name: '恒生科技', code: 'HSTECH', unit: '点', type: 'price_index', color: '#7752ab' },
  };

  function dayStamp(value) {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) throw new TypeError('日期格式无效');
    const stamp = Date.parse(`${value}T00:00:00Z`);
    if (!Number.isFinite(stamp) || new Date(stamp).toISOString().slice(0, 10) !== value) throw new TypeError('日期无效');
    return stamp;
  }

  function safeSourceUrl(value) {
    const url = new URL(value);
    if (!['https:', 'http:'].includes(url.protocol)) throw new TypeError('数据来源链接无效');
    return url.href;
  }

  function validateSnapshot(data) {
    if (data?.schema_version !== 1 || data.dataset_id !== 'sse-gold-hstech' || data.start_date !== START) {
      throw new TypeError('三资产数据格式或历史起点不符');
    }
    if (!Number.isFinite(Date.parse(data.generated_at))) throw new TypeError('数据生成时间无效');
    const today = new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10);
    for (const key of KEYS) {
      const item = data.series?.[key], definition = SERIES[key];
      if (!item || item.code !== definition.code || item.unit !== definition.unit || item.type !== definition.type) {
        throw new TypeError(`${definition.name}的品种或单位不符`);
      }
      if (!Array.isArray(item.data) || item.data.length < 2) throw new TypeError(`${definition.name}缺少历史数据`);
      let previous = '';
      for (const row of item.data) {
        if (!Array.isArray(row) || row.length !== 2) throw new TypeError(`${definition.name}的数据列不符`);
        dayStamp(row[0]);
        if (row[0] <= previous || row[0] < START || row[0] > today || !Number.isFinite(row[1]) || row[1] <= 0) {
          throw new TypeError(`${definition.name}存在无效或重复的日线`);
        }
        previous = row[0];
      }
      if (item.start_date !== item.data[0][0] || item.start_date !== START || item.as_of !== previous) {
        throw new TypeError(`${definition.name}的历史起止日期不符`);
      }
      if (!item.source || !['live', 'cached'].includes(item.source.mode)
        || !Number.isFinite(Date.parse(item.source.checked_at))) throw new TypeError(`${definition.name}缺少来源状态`);
      safeSourceUrl(item.source.url);
      if (item.source.data_url != null) safeSourceUrl(item.source.data_url);
      if (key === 'hstech' && (item.launch_date !== LAUNCH || item.backtest_before !== LAUNCH)) {
        throw new TypeError('恒生科技的推出日期或回溯标记不符');
      }
    }
    const lastDates = KEYS.map(key => data.series[key].as_of).sort();
    if (data.as_of !== lastDates.at(-1)) throw new TypeError('最新交易日期与数据不符');
    const otherDates = KEYS.slice(1).map(key => new Set(data.series[key].data.map(row => row[0])));
    const common = data.series.sse.data.filter(row => otherDates.every(set => set.has(row[0]))).at(-1)?.[0];
    if (!common || data.common_as_of !== common) throw new TypeError('最近共同交易日与数据不符');
    if (!Number.isFinite(Date.parse(data.update_status?.checked_at))
      || !['fresh', 'unchanged', 'cached'].includes(data.update_status?.outcome)) throw new TypeError('服务器更新状态无效');
    return data;
  }

  function unionDates(data) {
    return [...new Set(KEYS.flatMap(key => data.series[key].data.map(row => row[0])))].sort();
  }

  function rollbackReason(candidate, current) {
    if (!current) return '';
    for (const key of KEYS) {
      const next = candidate.series[key], old = current.series[key];
      if (next.as_of < old.as_of) return `${SERIES[key].name}只到 ${next.as_of}`;
      const dates = new Set(next.data.map(row => row[0]));
      if (old.data.some(row => !dates.has(row[0]))) return `${SERIES[key].name}缺少已发布的历史交易日`;
    }
    if (Date.parse(candidate.generated_at) < Date.parse(current.generated_at)) return '文件生成时间早于当前版本';
    return '';
  }

  // A single numeric scale is used for every raw value and for both labeled axes.
  function sharedDomain(values) {
    const maximum = Math.max(...values);
    const rough = maximum / 5;
    const power = 10 ** Math.floor(Math.log10(rough));
    const magnitude = rough / power;
    const step = (magnitude <= 1 ? 1 : magnitude <= 2 ? 2 : magnitude <= 2.5 ? 2.5 : magnitude <= 5 ? 5 : 10) * power;
    const high = Math.ceil(maximum / step) * step;
    const ticks = [];
    for (let value = 0; value <= high + step / 100; value += step) ticks.push(Number(value.toPrecision(12)));
    return { low: 0, high, ticks };
  }

  function nearestIndex(stamps, target, start = 0, end = stamps.length - 1) {
    let low = start, high = end;
    while (low < high) {
      const mid = Math.floor((low + high) / 2);
      if (stamps[mid] < target) low = mid + 1;
      else high = mid;
    }
    return low > start && target - stamps[low - 1] <= stamps[low] - target ? low - 1 : low;
  }

  function requestUrl(base, nonce) {
    const url = new URL('./markets-data.json', base);
    if (!['https:', 'http:'].includes(url.protocol)) throw new TypeError('数据地址必须使用 HTTP 或 HTTPS');
    url.searchParams.set('refresh', String(nonce));
    return url.href;
  }

  const model = { validateSnapshot, unionDates, rollbackReason, sharedDomain, nearestIndex, requestUrl };
  if (typeof module !== 'undefined' && module.exports) { module.exports = model; return; }
  if (typeof document === 'undefined' || !document.getElementById('markets')) return;
  window.MarketsChartModel = model;

  const $ = id => document.getElementById(`markets-${id}`);
  const controls = window.ChartControls;
  const chart = $('chart'), tooltip = $('tooltip'), wrap = $('wrap');
  const numbers = new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const goldNumbers = new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 3, maximumFractionDigits: 3 });
  const tickNumbers = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 });
  const timeFormat = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  });
  let data = null, dates = [], stamps = [], lookups = {}, range = null;
  let geometry = null, selected = null, anchor = null, pinned = false;
  let inFlight = false, requestCount = 0, lastCheck = 0, refreshTimer = null, resizeFrame = null;
  const AUTO_REFRESH_MS = 24 * 3600000;

  function element(tag, attributes = {}, text) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
    if (text != null) node.textContent = text;
    return node;
  }

  function textNode(parent, text, attributes = {}) {
    parent.append(element('text', attributes, text));
  }

  function status(message, error = false) {
    $('status').textContent = message;
    $('status').classList.toggle('error', error);
  }

  function formatTime(value) {
    return timeFormat.format(new Date(value));
  }

  function metadata() {
    $('period').textContent = `全部历史：${data.start_date} — ${data.as_of}；最近共同记录日：${data.common_as_of}。`;
    const cacheNotes = [], cachedErrors = [];
    for (const key of KEYS) {
      const item = data.series[key], source = item.source;
      $(`${key}-value`).textContent = (key === 'gold' ? goldNumbers : numbers).format(item.data.at(-1)[1]);
      $(`${key}-date`).textContent = item.as_of;
      $(`${key}-date`).setAttribute('datetime', item.as_of);
      $(`${key}-source`).href = safeSourceUrl(source.url);
      $(`${key}-source`).textContent = source.name || SERIES[key].name;
      $(`${key}-feed`).hidden = !source.data_url;
      if (source.data_url) $(`${key}-feed`).href = safeSourceUrl(source.data_url);
      $(`${key}-source-date`).textContent = `截至 ${item.as_of} · ${item.data.length.toLocaleString('zh-CN')} 条日线${source.mode === 'cached' ? ' · 保留已发布数据' : ''}`;
      if (source.mode === 'cached') {
        cacheNotes.push(`${SERIES[key].name}保留截至 ${item.as_of} 的已发布数据：${source.error || '此次来源未返回可用的新数据'}`);
        if (typeof source.error === 'string' && source.error) cachedErrors.push(source.error);
      }
    }
    if (Array.isArray(data.update_status.warnings)) {
      for (const warning of data.update_status.warnings) {
        if (typeof warning === 'string' && warning && !cacheNotes.includes(warning)
          && !cachedErrors.some(error => warning.includes(error))) cacheNotes.push(warning);
      }
    }
    $('source-status').hidden = cacheNotes.length === 0;
    $('source-status').textContent = cacheNotes.join('；');
    $('source-status').classList.toggle('markets-cached', cacheNotes.length > 0);
    $('server-check').textContent = formatTime(data.update_status.checked_at);
    $('latest').hidden = false;
    $('display').hidden = false;
  }

  function updateButtons() {
    if (!range) return;
    const count = range.end - range.start + 1, total = dates.length;
    $('zoom-in').disabled = count <= Math.min(5, total);
    $('zoom-out').disabled = count === total;
    $('earlier').disabled = range.start === 0;
    $('later').disabled = range.end === total - 1;
    $('all').disabled = count === total;
    $('view-range').textContent = `当前显示：${dates[range.start]} — ${dates[range.end]}（${count.toLocaleString('zh-CN')} 个有行情的日期）`;
  }

  function draw() {
    if (!data || !range) return;
    const width = Math.max(280, wrap.clientWidth), height = width < 600 ? 360 : 420;
    const frame = { left: width < 600 ? 51 : 66, right: width - (width < 600 ? 55 : 70), top: 44, bottom: height - 38 };
    const first = dates[range.start], last = dates[range.end];
    const visible = Object.fromEntries(KEYS.map(key => [key, data.series[key].data.filter(row => row[0] >= first && row[0] <= last)]));
    const domain = sharedDomain(KEYS.flatMap(key => visible[key].map(row => row[1])));
    const from = stamps[range.start], to = stamps[range.end];
    const x = stamp => frame.left + (stamp - from) / (to - from || DAY) * (frame.right - frame.left);
    const y = value => frame.bottom - (value - domain.low) / (domain.high - domain.low) * (frame.bottom - frame.top);
    chart.replaceChildren();
    chart.setAttribute('viewBox', `0 0 ${width} ${height}`);
    chart.setAttribute('data-y-min', domain.low);
    chart.setAttribute('data-y-max', domain.high);
    chart.setAttribute('data-view-start', first);
    chart.setAttribute('data-view-end', last);
    chart.append(element('title', { id: 'markets-chart-title' }, '上证指数、伦敦金现与恒生科技：原始绝对值'));
    chart.append(element('desc', { id: 'markets-chart-description' }, `${first} 至 ${last}。上证指数与恒生科技为点数，伦敦金现为美元/金衡盎司。左右两轴为同一个数值刻度，未转换百分比。恒生科技推出前以虚线表示回溯数据。`));
    const defs = element('defs'), clip = element('clipPath', { id: 'markets-plot-clip' });
    clip.append(element('rect', { x: frame.left, y: frame.top, width: frame.right - frame.left, height: frame.bottom - frame.top }));
    defs.append(clip); chart.append(defs);

    const launchX = x(dayStamp(LAUNCH));
    if (first < LAUNCH) {
      chart.append(element('rect', { x: frame.left, y: frame.top, width: Math.max(0, Math.min(frame.right, launchX) - frame.left), height: frame.bottom - frame.top, class: 'markets-backtest-band' }));
      if (Math.min(frame.right, launchX) - frame.left > 75) {
        textNode(chart, '恒科回溯期', { x: frame.left + 7, y: frame.top + 19, class: 'markets-backtest-label' });
      }
    }
    for (const tick of domain.ticks) {
      const position = y(tick);
      chart.append(element('line', { x1: frame.left, x2: frame.right, y1: position, y2: position, class: 'grid' }));
      textNode(chart, tickNumbers.format(tick), { x: frame.left - 8, y: position + 4, 'text-anchor': 'end', class: 'markets-axis-left markets-y-tick', 'data-axis': 'left', 'data-value': tick });
      textNode(chart, tickNumbers.format(tick), { x: frame.right + 8, y: position + 4, 'text-anchor': 'start', class: 'markets-axis-right markets-y-tick', 'data-axis': 'right', 'data-value': tick });
    }
    textNode(chart, '股指（点）', { x: 0, y: 19, class: 'markets-axis-left' });
    textNode(chart, '黄金（美元/金衡盎司）', { x: width, y: 19, 'text-anchor': 'end', class: 'markets-axis-right' });
    chart.append(element('line', { x1: frame.left, x2: frame.right, y1: frame.bottom, y2: frame.bottom, class: 'axis-line' }));
    if (launchX >= frame.left && launchX <= frame.right) chart.append(element('line', { x1: launchX, x2: launchX, y1: frame.top, y2: frame.bottom, class: 'markets-launch-line', 'data-launch-date': LAUNCH }));

    textNode(chart, first, { x: frame.left, y: height - 12, 'text-anchor': 'start', class: 'endpoint-date' });
    textNode(chart, last, { x: frame.right, y: height - 12, 'text-anchor': 'end', class: 'endpoint-date' });
    const middleCount = width >= 750 ? 4 : width >= 550 ? 2 : 0;
    for (let i = 1; i <= middleCount; i++) {
      const target = from + (to - from) * i / (middleCount + 1);
      const index = nearestIndex(stamps, target, range.start, range.end), date = dates[index];
      const position = x(stamps[index]);
      if (position > frame.left + 90 && position < frame.right - 90) {
        textNode(chart, to - from > 3 * 365 * DAY ? date.slice(0, 4) : date.slice(0, 7), { x: position, y: height - 12, 'text-anchor': 'middle' });
      }
    }

    const lines = element('g', { 'clip-path': 'url(#markets-plot-clip)' });
    function pathFor(key, points, phase = 'live') {
      if (!points.length) return;
      const d = points.map((row, index) => `${index ? 'L' : 'M'}${x(dayStamp(row[0])).toFixed(2)},${y(row[1]).toFixed(2)}`).join(' ');
      lines.append(element('path', {
        d, stroke: SERIES[key].color, class: `markets-series${phase === 'backtest' ? ' markets-backtest-path' : ''}`,
        'data-series': key, 'data-phase': phase, 'data-points': points.length,
      }));
    }
    for (const key of KEYS) {
      if (key !== 'hstech') pathFor(key, visible[key]);
      else {
        const backtest = visible[key].filter(row => row[0] < LAUNCH), live = visible[key].filter(row => row[0] >= LAUNCH);
        // The boundary segment connects two real observations; it creates no daily records.
        pathFor(key, backtest.length && live.length ? [...backtest, live[0]] : backtest, 'backtest');
        pathFor(key, live);
      }
      const endpoint = visible[key].at(-1);
      if (endpoint) lines.append(element('circle', { cx: x(dayStamp(endpoint[0])), cy: y(endpoint[1]), r: 2.2, fill: SERIES[key].color, 'data-endpoint-series': key }));
    }
    chart.append(lines);

    const hover = element('g', { visibility: 'hidden', 'aria-hidden': 'true' });
    const guide = element('line', { x1: frame.left, x2: frame.left, y1: frame.top, y2: frame.bottom, class: 'hover-guide' });
    hover.append(guide);
    const dots = {};
    for (const key of KEYS) {
      dots[key] = element('circle', { r: 4, fill: SERIES[key].color, class: 'hover-dot', 'data-hover-series': key });
      hover.append(dots[key]);
    }
    chart.append(hover);
    const hit = element('rect', { x: frame.left, y: frame.top, width: frame.right - frame.left, height: frame.bottom - frame.top, class: 'hit-area' });
    hit.addEventListener('pointermove', event => { if (!pinned) inspect(pointerIndex(event), false); });
    hit.addEventListener('pointerleave', () => { if (!pinned) hideInspection(); });
    hit.addEventListener('click', event => { chart.focus?.({ preventScroll: true }); pinned = true; anchor = pointerIndex(event); inspect(anchor, true); });
    chart.append(hit);
    geometry = { width, height, frame, x, y, hover, guide, dots };
    updateButtons();
    if (selected != null && selected >= range.start && selected <= range.end && pinned) inspect(selected, false);
    else hideInspection();
  }

  function pointerIndex(event) {
    const bounds = chart.getBoundingClientRect();
    const position = (event.clientX - bounds.left) * geometry.width / bounds.width;
    const proportion = Math.max(0, Math.min(1, (position - geometry.frame.left) / (geometry.frame.right - geometry.frame.left)));
    return nearestIndex(stamps, stamps[range.start] + proportion * (stamps[range.end] - stamps[range.start]), range.start, range.end);
  }

  function hideInspection() {
    if (geometry) geometry.hover.setAttribute('visibility', 'hidden');
    tooltip.hidden = true;
    if (!pinned) selected = null;
  }

  function inspect(index, announce) {
    if (!geometry || !data) return;
    selected = Math.max(range.start, Math.min(range.end, index));
    const date = dates[selected], position = geometry.x(stamps[selected]);
    geometry.guide.setAttribute('x1', position); geometry.guide.setAttribute('x2', position);
    geometry.hover.setAttribute('visibility', 'visible');
    tooltip.replaceChildren();
    const title = document.createElement('p'); title.className = 'tooltip-date'; title.textContent = date; tooltip.append(title);
    const announced = [date];
    for (const key of KEYS) {
      const value = lookups[key].get(date), present = value != null;
      const row = document.createElement('div'); row.className = `tooltip-row markets-${key}`;
      const label = document.createElement('span'); label.textContent = `${SERIES[key].name}${key === 'hstech' && date < LAUNCH ? '（回溯）' : ''}`;
      const amount = document.createElement('strong');
      amount.textContent = present ? `${(key === 'gold' ? goldNumbers : numbers).format(value)} ${SERIES[key].unit}` : '休市 / 无记录';
      row.append(label, amount); tooltip.append(row);
      geometry.dots[key].setAttribute('visibility', present ? 'visible' : 'hidden');
      if (present) { geometry.dots[key].setAttribute('cx', position); geometry.dots[key].setAttribute('cy', geometry.y(value)); }
      announced.push(`${label.textContent}：${amount.textContent}`);
    }
    tooltip.hidden = false;
    const availableWidth = wrap.clientWidth;
    const left = Math.max(4, Math.min(availableWidth - tooltip.offsetWidth - 4, position + (position < availableWidth / 2 ? 12 : -tooltip.offsetWidth - 12)));
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${Math.max(6, geometry.frame.top + 7)}px`;
    if (announce) $('announcement').textContent = announced.join('；');
  }

  function changeRange(next, latest = false) {
    range = next; selected = null; anchor = null; pinned = false;
    draw();
    if (latest) {
      pinned = true; anchor = range.end; inspect(range.end, true);
      $('announcement').textContent = `已定位最新有记录的日期。${$('announcement').textContent}`;
    }
  }

  const actions = {
    'zoom-in': () => changeRange(controls.zoomRange(range, dates.length, 0.5, anchor ?? range.end)),
    'zoom-out': () => changeRange(controls.zoomRange(range, dates.length, 2, anchor ?? range.end)),
    earlier: () => changeRange(controls.panRange(range, dates.length, -1)),
    later: () => changeRange(controls.panRange(range, dates.length, 1)),
    all: () => changeRange({ start: 0, end: dates.length - 1 }),
    year: () => {
      const cutoff = new Date(stamps.at(-1)); cutoff.setUTCFullYear(cutoff.getUTCFullYear() - 1);
      const start = dates.findIndex(date => dayStamp(date) >= cutoff.getTime());
      changeRange({ start: Math.max(0, start), end: dates.length - 1 });
    },
    'latest-button': () => changeRange(controls.latestRange(range, dates.length), true),
  };
  for (const [id, action] of Object.entries(actions)) $(id).addEventListener('click', () => { if (data) action(); });
  chart.addEventListener('keydown', event => {
    if (!data || !['ArrowLeft', 'ArrowRight', 'Home', 'End', 'Escape'].includes(event.key)) return;
    event.preventDefault();
    if (event.key === 'Escape') { pinned = false; anchor = null; hideInspection(); return; }
    let next = selected ?? range.end;
    if (event.key === 'Home') next = range.start;
    if (event.key === 'End') next = range.end;
    if (event.key === 'ArrowLeft') next--;
    if (event.key === 'ArrowRight') next++;
    pinned = true; inspect(next, true); anchor = selected;
  });
  document.addEventListener('pointerdown', event => {
    if (!wrap.contains(event.target)) { pinned = false; hideInspection(); }
  });

  function scheduleRefresh() {
    clearTimeout(refreshTimer);
    if (document.visibilityState !== 'visible') return;
    refreshTimer = setTimeout(() => refresh(false), Math.max(1000, AUTO_REFRESH_MS - (Date.now() - lastCheck)));
  }

  async function refresh(manual) {
    if (inFlight) return;
    inFlight = true;
    $('refresh-button').disabled = true; $('refresh-button').textContent = '正在刷新…';
    status(data ? '正在检查最新已发布数据…' : '正在读取长期历史数据…');
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 25000);
    try {
      const response = await fetch(requestUrl(document.baseURI, `${Date.now()}-${++requestCount}`), { cache: 'no-store', signal: controller.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const candidate = validateSnapshot(await response.json());
      const rollback = rollbackReason(candidate, data);
      if (rollback) throw new Error(`收到较旧或不完整的数据（${rollback}）`);
      const previous = data, oldDates = dates, oldRange = range;
      const changed = !previous || KEYS.some(key => JSON.stringify(candidate.series[key].data) !== JSON.stringify(previous.series[key].data));
      data = candidate; dates = unionDates(data); stamps = dates.map(dayStamp);
      lookups = Object.fromEntries(KEYS.map(key => [key, new Map(data.series[key].data)]));
      if (!oldRange) range = { start: 0, end: dates.length - 1 };
      else if (oldRange.start === 0 && oldRange.end === oldDates.length - 1) range = { start: 0, end: dates.length - 1 };
      else if (manual || oldRange.end === oldDates.length - 1) {
        const count = oldRange.end - oldRange.start + 1;
        range = { start: Math.max(0, dates.length - count), end: dates.length - 1 };
      } else {
        // Retain calendar boundaries if a later file adds a previously missing date.
        range = { start: dates.indexOf(oldDates[oldRange.start]), end: dates.indexOf(oldDates[oldRange.end]) };
      }
      selected = null; anchor = null; pinned = false;
      metadata(); draw();
      const cached = KEYS.filter(key => data.series[key].source.mode === 'cached');
      status(`${previous ? changed ? '已刷新' : '已检查，暂无更新的已发布行情' : '已载入'}；各来源最新日期见上方，最近共同记录日 ${data.common_as_of}。${cached.length ? ` ${cached.map(key => SERIES[key].name).join('、')}使用保留数据，原因见下方。` : ' 非交易日没有新日线时，保留最近交易日数据。'}`, cached.length > 0);
    } catch (error) {
      const reason = error.name === 'AbortError' ? '请求超时' : error.message;
      status(`${reason}。${data ? `已保留当前图表及截至 ${data.as_of} 的已发布记录，可再次刷新。` : '尚未载入本图数据，请重试。'}`, true);
    } finally {
      clearTimeout(timeout); inFlight = false; lastCheck = Date.now();
      $('browser-check').textContent = formatTime(lastCheck);
      $('refresh-button').disabled = false; $('refresh-button').textContent = '刷新本图数据';
      scheduleRefresh();
    }
  }

  $('refresh-button').addEventListener('click', () => refresh(true));
  // The original button refreshes both independent datasets, without coupling their failures.
  document.getElementById('refresh-button')?.addEventListener('click', () => refresh(true));
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && Date.now() - lastCheck >= AUTO_REFRESH_MS) refresh(false);
    else scheduleRefresh();
  });
  window.addEventListener('pageshow', event => { if (event.persisted) refresh(false); });
  const resize = () => {
    if (resizeFrame != null) cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(() => { resizeFrame = null; draw(); });
  };
  if (typeof ResizeObserver !== 'undefined') new ResizeObserver(resize).observe(wrap);
  else window.addEventListener('resize', resize);
  refresh(false);
})();
