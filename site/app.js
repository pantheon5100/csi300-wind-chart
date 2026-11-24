(() => {
  'use strict';

  // A raw-level crossing is confirmed at the first close on the opposite side.
  // Zero touches and zero plateaus do not count until the nonzero sign reverses.
  function calculateCrossings(data, epsilon = 1e-9) {
    if (!Array.isArray(data) || !Number.isFinite(epsilon) || epsilon < 0) throw new TypeError('Invalid crossing input');
    const events = [];
    let previousDate = '';
    let previousSign = 0;
    let previousSideIndex = -1;
    for (let index = 0; index < data.length; index += 1) {
      const row = data[index];
      if (!Array.isArray(row) || !Number.isFinite(dayValue(row[0])) || row[0] <= previousDate || !Number.isFinite(row[1]) || !Number.isFinite(row[2])) throw new TypeError('Crossings require ordered dates and finite index levels');
      previousDate = row[0];
      const difference = row[1] - row[2];
      const sign = difference > epsilon ? 1 : difference < -epsilon ? -1 : 0;
      if (sign === 0) continue;
      if (previousSign && sign !== previousSign) {
        const prior = events.at(-1);
        events.push({
          number: events.length + 1,
          date: row[0],
          index,
          direction: sign > 0 ? 'stock_above' : 'wind_above',
          difference,
          previousSideDate: data[previousSideIndex][0],
          equalityObservations: index - previousSideIndex - 1,
          calendarDays: prior ? Math.round((dayValue(row[0]) - dayValue(prior.date)) / 86400000) : null,
          dataIntervals: prior ? index - prior.index : null,
        });
      }
      previousSign = sign;
      previousSideIndex = index;
    }
    const latestEvent = events.at(-1);
    const lastRow = data.at(-1);
    return {
      events,
      ongoing: latestEvent ? {
        fromDate: latestEvent.date,
        toDate: lastRow[0],
        calendarDays: Math.round((dayValue(lastRow[0]) - dayValue(latestEvent.date)) / 86400000),
        dataIntervals: data.length - 1 - latestEvent.index,
      } : null,
    };
  }

  // Keep the calculation independently testable without a DOM or browser.
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { calculateCrossings };
    return;
  }

  const NS = 'http://www.w3.org/2000/svg';
  const CHECK_INTERVAL = 24 * 60 * 60 * 1000;
  const FIXED_START = '2004-12-31';
  const EXPECTED_COLUMNS = ['date', 'stock_level', 'wind_level', 'stock_return_pct', 'wind_return_pct'];
  const COLORS = { stock: '#2674b8', wind: '#d5643f', gap: '#725c97' };
  const { zoomRange, panRange, latestRange, reconcileRange, isOlderSnapshot, dataRequestUrl } = window.ChartControls;
  const $ = id => document.getElementById(id);
  const clockFormat = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
  });
  const levelFormat = new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const axisFormat = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 });
  let payload = null;
  let rows = [];
  let stamps = [];
  let crossingInfo = { events: [], ongoing: null };
  let fingerprint = '';
  let pending = false;
  let lastCheck = 0;
  let refreshTimer = 0;
  let resizeFrame = 0;
  let viewRange = null;
  let inspectionAnchorIndex = null;
  let requestSequence = 0;

  function signed(value) { return `${value > 0 ? '+' : ''}${Math.abs(value) < 0.05 ? '0.0' : value.toFixed(1)}`; }
  function percentage(value, approximate = false) { return `${approximate ? '约 ' : ''}${signed(value)}%`; }
  function level(value, approximate = false) { return `${approximate ? '约 ' : ''}${levelFormat.format(value)} 点`; }
  function gap(value, approximate = false) {
    const rounded = Math.abs(value) < 0.005 ? 0 : value;
    return `${approximate ? '约 ' : ''}${rounded > 0 ? '+' : ''}${levelFormat.format(rounded)} 点`;
  }
  function setText(id, value) { $(id).textContent = value; }
  function crossingDirection(event) { return event.direction === 'stock_above' ? '沪深300点位转为较高' : '万得点位转为较高'; }

  const charts = [
    {
      key: 'returns', title: '累计收益率', unit: '累计收益率（%）', tick: value => `${axisFormat.format(value)}%`,
      series: [
        { key: 'stock', name: '沪深300全收益', value: row => row[3], format: row => percentage(row[3]) },
        { key: 'wind', name: '万得偏债混合', value: row => row[4], format: row => percentage(row[4], true) },
      ],
    },
    {
      key: 'levels', title: '原始指数点位', unit: '指数点位（点）', tick: value => axisFormat.format(value),
      series: [
        { key: 'stock', name: '沪深300全收益', value: row => row[1], format: row => level(row[1]) },
        { key: 'wind', name: '万得偏债混合', value: row => row[2], format: row => level(row[2], row[0] === FIXED_START) },
      ],
    },
    {
      key: 'gap', title: '原始指数点位差（偏离）', unit: '指数点位差（点）', tick: value => axisFormat.format(value),
      series: [{ key: 'gap', name: '沪深300点位 − 万得点位', value: row => row[1] - row[2], format: row => gap(row[1] - row[2], row[0] === FIXED_START) }],
    },
  ].map(chart => ({ ...chart, svg: $(`${chart.key}-chart`), wrap: $(`${chart.key}-wrap`), tooltip: $(`${chart.key}-tooltip`), selectedIndex: 0, pinned: false, geometry: null }));

  function dayValue(value) {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return NaN;
    const stamp = Date.parse(`${value}T00:00:00Z`);
    return Number.isFinite(stamp) && new Date(stamp).toISOString().slice(0, 10) === value ? stamp : NaN;
  }
  function currentChinaDay() {
    const parts = new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
    const fields = Object.fromEntries(parts.map(part => [part.type, part.value]));
    return `${fields.year}-${fields.month}-${fields.day}`;
  }
  function validateData(candidate) {
    if (!candidate || candidate.schema_version !== 1 || JSON.stringify(candidate.columns) !== JSON.stringify(EXPECTED_COLUMNS)) throw new Error('数据格式与当前图表不兼容');
    if (candidate.start_date !== FIXED_START || candidate.baseline?.H00300 !== 1000 || !Number.isFinite(candidate.baseline?.['885003.WI']) || Math.abs(candidate.baseline['885003.WI'] - 989.3) > 0.000001 || candidate.baseline?.wind_approximate !== true) throw new Error('数据基准发生变化');
    if (!Number.isFinite(Date.parse(candidate.generated_at))) throw new Error('数据文件缺少有效生成时间');
    if (!Array.isArray(candidate.data) || candidate.data.length < 2) throw new Error('数据文件中没有足够的历史记录');
    let previous = '';
    for (const row of candidate.data) {
      if (!Array.isArray(row) || row.length !== 5 || !Number.isFinite(dayValue(row[0])) || row[0] <= previous || !row.slice(1).every(Number.isFinite) || row[1] <= 0 || row[2] <= 0) throw new Error('历史记录包含无效日期或数值');
      previous = row[0];
    }
    if (candidate.data[0][0] !== FIXED_START || candidate.as_of !== candidate.data.at(-1)[0] || dayValue(candidate.as_of) > dayValue(currentChinaDay())) throw new Error('数据区间不一致');
    if (candidate.data[0][1] !== 1000 || Math.abs(candidate.data[0][2] - 989.3) > 0.000001 || Math.abs(candidate.data[0][3]) > 0.000001 || Math.abs(candidate.data[0][4]) > 0.000001) throw new Error('基准日点位或收益不一致');
    for (const code of ['H00300', '885003.WI']) {
      const source = candidate.sources?.[code];
      if (!source || !Number.isFinite(dayValue(source.as_of))) throw new Error('缺少有效来源日期');
      const url = new URL(source.url);
      if (!['https:', 'http:'].includes(url.protocol)) throw new Error('无效的来源链接');
    }
    return candidate;
  }
  function status(message, error = false) {
    setText('update-status', message);
    $('update-status').classList.toggle('error', error);
  }
  function updateStaleness() {
    if (!payload) return;
    const days = Math.floor((dayValue(currentChinaDay()) - dayValue(payload.as_of)) / 86400000);
    $('stale-badge').hidden = days <= 5;
    setText('stale-badge', days > 5 ? `数据距今 ${days} 天` : '');
  }
  function sourceCheckReason(check) {
    const message = typeof check.message === 'string' ? check.message.trim() : '';
    if (check.status === 'older') return Number.isFinite(dayValue(check.as_of)) ? `仅至 ${check.as_of}，日期较旧` : '数据日期较旧';
    if (message.includes('global source declares an H20269 fallback')) return '全局说明提及 H20269 降级，但未明确这条万得序列的来源，暂不采用';
    const mismatch = message.match(/source disagrees with verified history on (\d{4}-\d{2}-\d{2})/);
    if (mismatch) return `与已发布点位不一致（${mismatch[1]}）`;
    if (check.status === 'rejected') return message ? `未采用：${message.slice(0, 160)}` : '未通过数据校验';
    if (check.status === 'failed') return '请求失败';
    return message.slice(0, 160) || '尚未取得可用数据';
  }
  function renderSourceHealth() {
    const update = payload.update_status;
    const sourceStatus = $('source-status');
    const hasUpdate = update && typeof update.message === 'string' && update.message.trim();
    sourceStatus.hidden = !hasUpdate;
    sourceStatus.classList.toggle('source-cached', update?.outcome === 'cached');
    if (hasUpdate) {
      const details = [];
      let message = update.message;
      if (update.outcome === 'cached') {
        message = `部分来源本次未取得可用数据，保留截至 ${payload.as_of} 的已发布行情。`;
        for (const [code, name] of [['H00300', '沪深300'], ['885003.WI', '万得']]) {
          const source = payload.sources[code];
          if (source.mode !== 'cached' || !Array.isArray(source.checks)) continue;
          for (const check of source.checks) {
            if (check && check.status !== 'available') details.push(`${name}${check.role === 'fallback' ? '备用来源' : '主来源'}：${sourceCheckReason(check)}`);
          }
        }
      } else if (payload.sources['885003.WI'].mode === 'fallback') {
        details.push(`万得当前采用第三方备用来源：${payload.sources['885003.WI'].name}`);
      }
      const checkedAt = Number.isFinite(Date.parse(update.checked_at)) ? `服务器最近检查：${clockFormat.format(new Date(update.checked_at))}（北京时间）。` : '';
      sourceStatus.textContent = `${message}${details.length ? ` ${details.join('；')}。` : ''}${checkedAt ? ` ${checkedAt}` : ''}`;
    } else sourceStatus.textContent = '';

    const options = $('source-options');
    options.replaceChildren();
    const statuses = { available: '可用', older: '日期较旧', failed: '请求失败', rejected: '未通过校验', unverified: '未核验' };
    const precisionNotes = [];
    for (const code of ['H00300', '885003.WI']) {
      const source = payload.sources[code];
      if (typeof source.precision === 'string' && source.precision.trim()) precisionNotes.push(`${code}：${source.precision}`);
      if (typeof source.historical_source_note === 'string' && source.historical_source_note.trim()) precisionNotes.push(source.historical_source_note);
      if (!Array.isArray(source.checks)) continue;
      for (const check of source.checks) {
        if (!check || typeof check.name !== 'string') continue;
        const item = document.createElement('li');
        item.append(document.createTextNode(`${code} · ${check.role === 'fallback' ? '备用' : '主来源'}：`));
        let url;
        try { url = new URL(check.url); } catch { url = null; }
        if (url && ['http:', 'https:'].includes(url.protocol)) {
          const link = document.createElement('a');
          link.href = url.href;
          link.target = '_blank';
          link.rel = 'noopener noreferrer';
          link.textContent = check.name;
          item.append(link);
        } else item.append(document.createTextNode(check.name));
        const asOf = Number.isFinite(dayValue(check.as_of)) ? `，截至 ${check.as_of}` : '';
        const selected = source.mode !== 'cached' && check.provider_id && check.provider_id === source.provider_id ? '，本次使用' : '';
        item.append(document.createTextNode(` · ${statuses[check.status] || '状态未知'}${asOf}${selected}`));
        if (typeof check.message === 'string' && check.message.trim()) {
          const note = document.createElement('span');
          note.className = 'source-check-note';
          note.textContent = `。${check.message}`;
          item.append(note);
        }
        options.append(item);
      }
    }
    $('source-options-details').hidden = !options.childElementCount;
    if (Array.isArray(update?.warnings)) for (const warning of update.warnings) {
      if (typeof warning === 'string' && warning.trim()) precisionNotes.push(warning);
    }
    const notes = [...new Set(precisionNotes)];
    $('source-precision-note').hidden = !notes.length;
    setText('source-precision-note', notes.join('；'));
  }
  function renderDataRevisions() {
    const details = $('data-revisions-details');
    const content = $('data-revisions-content');
    content.replaceChildren();
    let count = 0;
    for (const revision of Array.isArray(payload.data_revisions) ? payload.data_revisions : []) {
      if (!revision || !['H00300', '885003.WI'].includes(revision.index_code)
        || !Number.isFinite(dayValue(revision.reviewed_at)) || !Array.isArray(revision.changes)) continue;
      const changes = revision.changes.filter(change => change && Number.isFinite(dayValue(change.date))
        && change.date <= payload.as_of && Number.isFinite(change.old) && change.old > 0 && Number.isFinite(change.new) && change.new > 0);
      if (!changes.length) continue;
      count += changes.length;
      const title = document.createElement('p');
      title.textContent = `${revision.index_code} · 复核日期 ${revision.reviewed_at}`;
      content.append(title);
      if (typeof revision.reason === 'string' && revision.reason.trim()) {
        const reason = document.createElement('p');
        reason.textContent = revision.reason;
        content.append(reason);
      }
      const list = document.createElement('ul');
      for (const change of changes) {
        const item = document.createElement('li');
        item.textContent = `${change.date}：${levelFormat.format(change.old)} → ${levelFormat.format(change.new)} 点`;
        list.append(item);
      }
      content.append(list);
      const sources = document.createElement('p');
      sources.append(document.createTextNode('复核来源：'));
      let links = 0;
      for (const source of Array.isArray(revision.sources) ? revision.sources : []) {
        if (!source || typeof source.name !== 'string') continue;
        let url;
        try { url = new URL(source.url); } catch { continue; }
        if (!['http:', 'https:'].includes(url.protocol)) continue;
        if (links++) sources.append(document.createTextNode(' · '));
        const link = document.createElement('a');
        link.href = url.href;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = source.name;
        sources.append(link);
      }
      if (links) content.append(sources);
    }
    details.hidden = count === 0;
    setText('data-revisions-summary', `历史数据修订记录（${count} 条点位）`);
  }
  function renderMetadata() {
    const latest = rows.at(-1);
    const latestDifference = latest[1] - latest[2];
    setText('as-of', payload.as_of);
    setText('period', `${payload.start_date} — ${payload.as_of} · 共同日期的日度数据 · 累计收益起点 0%`);
    setText('current-deviation-value', gap(latestDifference));
    setText('current-deviation-direction', Math.abs(latestDifference) < 0.005 ? '两指数点位相同' : latestDifference > 0 ? '沪深300全收益点位较高' : '万得偏债混合点位较高');
    setText('current-deviation-date', payload.as_of);
    $('current-deviation-date').dateTime = payload.as_of;
    $('current-deviation').hidden = false;
    setText('wind-baseline', payload.baseline['885003.WI'].toFixed(2));
    setText('generated-at', clockFormat.format(new Date(payload.generated_at)));
    setText('point-count', `共 ${rows.length.toLocaleString('zh-CN')} 个日期。`);
    for (const [key, code] of [['stock', 'H00300'], ['wind', '885003.WI']]) {
      const source = payload.sources[code];
      $(`${key}-source`).href = source.url;
      $(`${key}-source`).title = source.name || '';
      if (source.name) setText(`${key}-source`, source.name);
      const mode = { primary: '主来源', fallback: '备用来源', cached: '保留上次有效数据' }[source.mode];
      setText(`${key}-source-date`, `截至 ${source.as_of}${mode ? ` · ${mode}` : ''}`);
    }
    renderSourceHealth();
    renderDataRevisions();
    updateStaleness();
  }
  function scheduleNextCheck() {
    clearTimeout(refreshTimer);
    const delay = Math.max(0, CHECK_INTERVAL - (Date.now() - lastCheck));
    refreshTimer = setTimeout(() => {
      if (document.visibilityState === 'visible') refreshData();
      // A hidden page waits until it becomes visible; no background polling.
    }, delay);
  }
  async function refreshData({ manual = false } = {}) {
    if (pending) return;
    pending = true;
    $('refresh-button').disabled = true;
    setText('refresh-button', '正在刷新…');
    status(payload ? '正在刷新已发布数据，当前曲线保留。' : '正在读取数据…');
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 25000);
    try {
      const url = dataRequestUrl(document.baseURI, `${Date.now()}-${++requestSequence}`);
      const response = await fetch(url, { cache: 'no-store', signal: controller.signal });
      if (!response.ok) throw new Error(`数据请求失败（HTTP ${response.status}）`);
      const candidate = validateData(await response.json());
      if (isOlderSnapshot(candidate, payload)) throw new Error(`收到较旧数据（${candidate.as_of}），未覆盖当前曲线`);
      const nextFingerprint = JSON.stringify(candidate.data);
      const changed = nextFingerprint !== fingerprint;
      const oldAsOf = payload?.as_of;
      const oldTotal = rows.length;
      const selectedDates = charts.map(chart => rows[chart.selectedIndex]?.[0]);
      const nextRange = oldTotal && viewRange ? reconcileRange(viewRange, oldTotal, candidate.data.length) : { start: 0, end: candidate.data.length - 1 };
      const nextStamps = candidate.data.map(row => dayValue(row[0]));
      const nextCrossings = calculateCrossings(candidate.data);
      payload = candidate;
      rows = candidate.data;
      viewRange = nextRange;
      if (manual) {
        viewRange = latestRange(viewRange, rows.length);
        inspectionAnchorIndex = null;
        charts.forEach(chart => { chart.pinned = false; });
      }
      stamps = nextStamps;
      crossingInfo = nextCrossings;
      fingerprint = nextFingerprint;
      renderMetadata();
      renderViewControls();
      if (changed || manual) {
        $('loading').hidden = true;
        $('charts').hidden = false;
        charts.forEach((chart, index) => {
          chart.selectedIndex = chart.pinned && selectedDates[index] ? nearestIndex(dayValue(selectedDates[index])) : viewRange.end;
          chart.selectedIndex = Math.max(viewRange.start, Math.min(viewRange.end, chart.selectedIndex));
          drawChart(chart);
        });
        if (changed) setText('last-update', clockFormat.format(new Date()));
      }
      status(!oldAsOf ? `已载入，数据截至 ${payload.as_of}。` : changed ? `已刷新，数据截至 ${payload.as_of}。` : `已刷新，数据截至 ${payload.as_of}；暂无更晚的已发布记录。`);
    } catch (error) {
      const message = error.name === 'AbortError' ? '请求超时' : error instanceof SyntaxError ? '数据文件暂时无法读取' : error.message;
      status(payload ? `刷新未完成：${message}。保留截至 ${payload.as_of} 的曲线，可手动重试。` : `载入失败：${message}。请点击“刷新最新数据”重试。`, true);
      if (!payload) setText('loading', '暂时无法载入数据。');
    } finally {
      clearTimeout(timeout);
      lastCheck = Date.now();
      setText('last-check', clockFormat.format(new Date(lastCheck)));
      pending = false;
      $('refresh-button').disabled = false;
      setText('refresh-button', '刷新最新数据');
      updateStaleness();
      scheduleNextCheck();
    }
  }

  function renderViewControls() {
    if (!viewRange || !rows.length) return;
    const count = viewRange.end - viewRange.start + 1;
    const ending = rows[viewRange.end];
    for (const chart of charts) {
      setText(`${chart.key}-end-date`, ending[0]);
      $(`${chart.key}-end-date`).dateTime = ending[0];
    }
    setText('return-stock', percentage(ending[3]));
    setText('return-wind', percentage(ending[4], true));
    setText('level-stock', level(ending[1]));
    setText('level-wind', level(ending[2]));
    setText('gap-total', gap(ending[1] - ending[2]));
    setText('view-range', `显示 ${rows[viewRange.start][0]} — ${rows[viewRange.end][0]} · ${count.toLocaleString('zh-CN')} 个共同日期`);
    $('zoom-in').disabled = count <= Math.min(5, rows.length);
    $('zoom-out').disabled = count >= rows.length;
    $('zoom-reset').disabled = count >= rows.length;
    $('pan-earlier').disabled = viewRange.start === 0;
    $('pan-later').disabled = viewRange.end === rows.length - 1;
  }
  function changeView(nextRange, showLatest = false) {
    if (!rows.length) return;
    viewRange = nextRange;
    for (const chart of charts) {
      chart.selectedIndex = showLatest ? rows.length - 1 : Math.max(viewRange.start, Math.min(viewRange.end, chart.selectedIndex));
      chart.pinned = showLatest;
    }
    if (showLatest) inspectionAnchorIndex = rows.length - 1;
    renderViewControls();
    charts.forEach(drawChart);
    if (showLatest) {
      const latest = rows.at(-1);
      setText('chart-announcement', `已定位最新数据 ${latest[0]}。沪深300全收益 ${level(latest[1])}，万得偏债混合 ${level(latest[2])}，偏离 ${gap(latest[1] - latest[2])}。`);
    }
  }

  function append(parent, tag, attributes = {}, text) {
    const element = document.createElementNS(NS, tag);
    for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
    if (text !== undefined) element.textContent = text;
    parent.append(element);
    return element;
  }
  function nearestIndex(stamp) {
    let lo = 0, hi = stamps.length - 1;
    while (lo < hi) {
      const mid = Math.floor((lo + hi) / 2);
      if (stamps[mid] < stamp) lo = mid + 1;
      else hi = mid;
    }
    return lo > 0 && stamp - stamps[lo - 1] <= stamps[lo] - stamp ? lo - 1 : lo;
  }
  function tickStep(range) {
    const raw = Math.max(range, 1) / 5;
    const magnitude = 10 ** Math.floor(Math.log10(raw));
    const error = raw / magnitude;
    return magnitude * (error >= Math.sqrt(50) ? 10 : error >= Math.sqrt(10) ? 5 : error >= Math.sqrt(2) ? 2 : 1);
  }
  function drawChart(chart) {
    if (!rows.length) return;
    const width = Math.max(240, chart.wrap.clientWidth);
    const compact = width < 520;
    const height = compact ? 265 : 310;
    const frame = { left: compact ? 48 : 58, right: width - 12, top: 25, bottom: height - 35 };
    const visibleRows = rows.slice(viewRange.start, viewRange.end + 1);
    const allHistory = visibleRows.length === rows.length;
    let min = allHistory ? 0 : Infinity, max = allHistory ? 0 : -Infinity;
    for (const row of visibleRows) {
      for (const series of chart.series) { const value = series.value(row); min = Math.min(min, value); max = Math.max(max, value); }
    }
    if (min === max) { const padding = Math.max(Math.abs(min) * 0.01, 1); min -= padding; max += padding; }
    const step = tickStep(max - min);
    const yMin = Math.floor(min / step) * step;
    const yMax = Math.ceil(max / step) * step;
    const t0 = stamps[viewRange.start], t1 = stamps[viewRange.end];
    const x = stamp => frame.left + (stamp - t0) / (t1 - t0) * (frame.right - frame.left);
    const y = value => frame.bottom - (value - yMin) / (yMax - yMin) * (frame.bottom - frame.top);
    chart.geometry = { width, height, frame, x, y, t0, t1 };
    chart.svg.replaceChildren();
    chart.svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    chart.svg.setAttribute('width', String(width));
    chart.svg.setAttribute('height', String(height));
    append(chart.svg, 'title', { id: `${chart.key}-title` }, chart.title);
    append(chart.svg, 'desc', { id: `${chart.key}-description` }, `显示${visibleRows[0][0]}至${visibleRows.at(-1)[0]}，${visibleRows.length}个共同日期。完整数据截至${payload.as_of}。最新：${chart.series.map(series => `${series.name}${series.format(rows.at(-1))}`).join('，')}。累计收益基准保持2004-12-31不变。使用真实记录之间的直线连接。${chart.key === 'gap' ? `完整历史有 ${crossingInfo.events.length} 次交叉确认日，可在图中查看日期与间隔。` : ''}`);
    append(chart.svg, 'text', { x: frame.left, y: 12 }, chart.unit);
    const grid = append(chart.svg, 'g', { 'aria-hidden': 'true' });
    for (let tick = yMin; tick <= yMax + step / 100; tick += step) {
      const value = Math.abs(tick) < step / 100 ? 0 : tick;
      append(grid, 'line', { x1: frame.left, x2: frame.right, y1: y(value), y2: y(value), class: value === 0 ? 'zero-line' : 'grid' });
      append(grid, 'text', { x: frame.left - 8, y: y(value) + 3.5, 'text-anchor': 'end' }, chart.tick(value));
    }
    append(grid, 'line', { x1: frame.left, x2: frame.right, y1: frame.bottom, y2: frame.bottom, class: 'axis-line' });
    const tickCount = Math.max(1, Math.floor((frame.right - frame.left) / (compact ? 95 : 130)));
    const yearsVisible = (t1 - t0) / (365.25 * 86400000);
    const tickIndices = new Set();
    const tickLabels = new Set();
    for (let n = 1; n < tickCount; n += 1) {
      const index = nearestIndex(t0 + (t1 - t0) * n / tickCount);
      if (index <= viewRange.start || index >= viewRange.end || tickIndices.has(index)) continue;
      const px = x(stamps[index]);
      if (px < frame.left + 85 || px > frame.right - 85) continue;
      tickIndices.add(index);
      const date = rows[index][0];
      const label = yearsVisible > 5 ? date.slice(0, 4) : yearsVisible > 1 ? date.slice(0, 7) : date.slice(5);
      if (tickLabels.has(label)) continue;
      tickLabels.add(label);
      append(grid, 'text', { x: px, y: frame.bottom + 23, 'text-anchor': 'middle' }, label);
    }
    append(grid, 'text', { x: frame.left, y: frame.bottom + 23, 'text-anchor': 'start', class: 'endpoint-date' }, visibleRows[0][0]);
    append(grid, 'text', { x: frame.right, y: frame.bottom + 23, 'text-anchor': 'end', class: 'endpoint-date' }, visibleRows.at(-1)[0]);
    const defs = append(chart.svg, 'defs');
    const clip = append(defs, 'clipPath', { id: `${chart.key}-clip` });
    append(clip, 'rect', { x: frame.left - 2, y: frame.top - 2, width: frame.right - frame.left + 4, height: frame.bottom - frame.top + 4 });
    const paths = append(chart.svg, 'g', { 'clip-path': `url(#${chart.key}-clip)`, 'aria-hidden': 'true' });
    for (const series of chart.series) {
      const path = visibleRows.map((row, index) => `${index ? 'L' : 'M'}${x(stamps[viewRange.start + index]).toFixed(2)},${y(series.value(row)).toFixed(2)}`).join('');
      append(paths, 'path', { d: path, class: `series-path ${series.key}-path`, stroke: COLORS[series.key] });
      append(paths, 'circle', { cx: x(t1), cy: y(series.value(visibleRows.at(-1))), r: 2.6, fill: COLORS[series.key] });
    }
    if (chart.key === 'gap') {
      const markers = append(chart.svg, 'g', { class: 'crossing-markers', 'clip-path': `url(#${chart.key}-clip)`, 'aria-hidden': 'true' });
      for (const event of crossingInfo.events) {
        if (event.index < viewRange.start || event.index > viewRange.end) continue;
        const marker = append(markers, 'circle', {
          cx: x(stamps[event.index]), cy: y(event.difference), r: 3.3,
          class: 'crossing-marker', fill: COLORS[event.direction === 'stock_above' ? 'stock' : 'wind'],
          'data-crossing-number': event.number,
        });
        append(marker, 'title', {}, `第 ${event.number} 次：${event.date}，${crossingDirection(event)}`);
      }
    }
    const hover = append(chart.svg, 'g', { id: `${chart.key}-hover`, display: 'none', 'pointer-events': 'none', 'aria-hidden': 'true' });
    append(hover, 'line', { id: `${chart.key}-guide`, class: 'hover-guide', y1: frame.top, y2: frame.bottom });
    for (const series of chart.series) append(hover, 'circle', { id: `${chart.key}-${series.key}-dot`, class: 'hover-dot', r: 3.8, fill: COLORS[series.key] });
    append(chart.svg, 'rect', { x: frame.left, y: frame.top, width: frame.right - frame.left, height: frame.bottom - frame.top, class: 'hit-area', 'aria-hidden': 'true' });
    if (chart.pinned) showPoint(chart, chart.selectedIndex);
    else hidePoint(chart);
  }
  function hidePoint(chart) {
    chart.tooltip.hidden = true;
    $(`${chart.key}-hover`)?.setAttribute('display', 'none');
  }
  function showPoint(chart, index, pointerY, announce = false) {
    if (!rows.length || !chart.geometry) return;
    chart.selectedIndex = Math.max(viewRange.start, Math.min(viewRange.end, index));
    const row = rows[chart.selectedIndex];
    const { x, y, frame, width, height } = chart.geometry;
    const px = x(stamps[chart.selectedIndex]);
    $(`${chart.key}-hover`).setAttribute('display', 'inline');
    $(`${chart.key}-guide`).setAttribute('x1', String(px));
    $(`${chart.key}-guide`).setAttribute('x2', String(px));
    chart.tooltip.replaceChildren();
    const date = document.createElement('p');
    date.className = 'tooltip-date';
    date.textContent = row[0];
    chart.tooltip.append(date);
    for (const series of chart.series) {
      const marker = $(`${chart.key}-${series.key}-dot`);
      marker.setAttribute('cx', String(px));
      marker.setAttribute('cy', String(y(series.value(row))));
      const line = document.createElement('div');
      line.className = `tooltip-row ${series.key}`;
      const label = document.createElement('span');
      label.textContent = series.name;
      const value = document.createElement('strong');
      value.textContent = series.format(row);
      line.append(label, value);
      chart.tooltip.append(line);
    }
    const crossing = chart.key === 'gap' ? crossingInfo.events.find(event => event.index === chart.selectedIndex) : null;
    if (crossing) {
      const note = document.createElement('p');
      note.className = 'tooltip-note';
      note.textContent = `交叉 #${crossing.number} · ${crossingDirection(crossing)}`;
      chart.tooltip.append(note);
      const interval = document.createElement('p');
      interval.className = 'tooltip-note';
      interval.textContent = crossing.calendarDays === null ? '首次交叉' : `距上次 ${crossing.calendarDays} 日历天／${crossing.dataIntervals} 共同数据日`;
      chart.tooltip.append(interval);
    }
    chart.tooltip.hidden = false;
    const scale = chart.wrap.clientWidth / width;
    const tooltipWidth = chart.tooltip.offsetWidth;
    const tooltipHeight = chart.tooltip.offsetHeight;
    const pixelX = px * scale;
    const desiredLeft = pixelX + tooltipWidth + 16 < chart.wrap.clientWidth ? pixelX + 10 : pixelX - tooltipWidth - 10;
    const left = Math.max(3, Math.min(desiredLeft, chart.wrap.clientWidth - tooltipWidth - 3));
    const defaultY = Math.min(...chart.series.map(series => y(series.value(row))));
    const desiredTop = (pointerY ?? defaultY) * scale - tooltipHeight - 12;
    const top = Math.max(frame.top * scale, Math.min(desiredTop, height * scale - tooltipHeight - 8));
    chart.tooltip.style.left = `${left}px`;
    chart.tooltip.style.top = `${top}px`;
    if (announce) setText('chart-announcement', `${chart.title}，${row[0]}，${chart.series.map(series => `${series.name}${series.format(row)}`).join('，')}${crossing ? `，第 ${crossing.number} 次交叉，${crossingDirection(crossing)}` : ''}`);
  }
  function handlePointer(chart, event) {
    if (!rows.length || !chart.geometry) return;
    const box = chart.svg.getBoundingClientRect();
    const px = (event.clientX - box.left) * chart.geometry.width / box.width;
    const py = (event.clientY - box.top) * chart.geometry.height / box.height;
    const { frame, t0, t1 } = chart.geometry;
    if (px < frame.left || px > frame.right || py < frame.top || py > frame.bottom) {
      if (!chart.pinned) hidePoint(chart);
      return;
    }
    const stamp = t0 + (px - frame.left) / (frame.right - frame.left) * (t1 - t0);
    showPoint(chart, nearestIndex(stamp), py);
  }

  for (const chart of charts) {
    chart.svg.addEventListener('pointermove', event => handlePointer(chart, event));
    chart.svg.addEventListener('pointerdown', event => { chart.pinned = true; handlePointer(chart, event); inspectionAnchorIndex = chart.selectedIndex; });
    chart.svg.addEventListener('pointerleave', () => { if (!chart.pinned) hidePoint(chart); });
    chart.svg.addEventListener('focus', () => { chart.pinned = true; showPoint(chart, chart.selectedIndex, undefined, true); });
    chart.svg.addEventListener('blur', () => { chart.pinned = false; hidePoint(chart); });
    chart.svg.addEventListener('keydown', event => {
      if (!rows.length) return;
      let index = chart.selectedIndex;
      if (event.key === 'ArrowLeft') index -= 1;
      else if (event.key === 'ArrowRight') index += 1;
      else if (event.key === 'Home') index = viewRange.start;
      else if (event.key === 'End') index = viewRange.end;
      else if (event.key === 'Escape') { chart.pinned = false; hidePoint(chart); return; }
      else return;
      event.preventDefault();
      chart.pinned = true;
      showPoint(chart, index, undefined, true);
      inspectionAnchorIndex = chart.selectedIndex;
    });
  }
  document.addEventListener('pointerdown', event => {
    for (const chart of charts) if (!chart.wrap.contains(event.target)) { chart.pinned = false; hidePoint(chart); }
  });
  $('refresh-button').addEventListener('click', () => refreshData({ manual: true }));
  for (const [id, factor] of [['zoom-in', 0.5], ['zoom-out', 2]]) {
    $(id).addEventListener('click', () => {
      if (!viewRange) return;
      const anchor = inspectionAnchorIndex !== null && inspectionAnchorIndex >= viewRange.start && inspectionAnchorIndex <= viewRange.end ? inspectionAnchorIndex : viewRange.end;
      changeView(zoomRange(viewRange, rows.length, factor, anchor));
    });
  }
  for (const [id, direction] of [['pan-earlier', -1], ['pan-later', 1]]) {
    $(id).addEventListener('click', () => {
      if (!viewRange) return;
      inspectionAnchorIndex = null;
      changeView(panRange(viewRange, rows.length, direction));
    });
  }
  $('zoom-reset').addEventListener('click', () => { inspectionAnchorIndex = null; changeView({ start: 0, end: rows.length - 1 }); });
  $('show-latest').addEventListener('click', () => { if (viewRange) changeView(latestRange(viewRange, rows.length), true); });
  const observer = new ResizeObserver(() => {
    cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(() => charts.forEach(drawChart));
  });
  charts.forEach(chart => observer.observe(chart.wrap));
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible') return;
    updateStaleness();
    if (!pending && Date.now() - lastCheck >= CHECK_INTERVAL) refreshData();
    else scheduleNextCheck();
  });
  window.addEventListener('pageshow', event => { if (event.persisted) refreshData(); });
  refreshData();
})();
