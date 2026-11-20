(() => {
  'use strict';

  function checkTotal(total) {
    if (!Number.isSafeInteger(total) || total < 1) throw new TypeError('Total must be a positive integer');
  }

  function checkRange(range, total) {
    checkTotal(total);
    if (!range || !Number.isSafeInteger(range.start) || !Number.isSafeInteger(range.end)
      || range.start < 0 || range.end < range.start || range.end >= total) {
      throw new TypeError('Range must contain valid inclusive indices');
    }
  }

  function fitRange(start, count, total) {
    const size = Math.min(count, total);
    const first = Math.max(0, Math.min(start, total - size));
    return { start: first, end: first + size - 1 };
  }

  function zoomRange(range, total, factor, anchorIndex = range?.end) {
    checkRange(range, total);
    if (!Number.isFinite(factor) || factor <= 0) throw new TypeError('Zoom factor must be positive');
    if (!Number.isSafeInteger(anchorIndex) || anchorIndex < range.start || anchorIndex > range.end) {
      throw new TypeError('Zoom anchor must be inside the current range');
    }
    const count = range.end - range.start + 1;
    const nextCount = Math.max(Math.min(5, total), Math.min(total, Math.round(count * factor)));
    const position = count > 1 ? (anchorIndex - range.start) / (count - 1) : 1;
    return fitRange(Math.round(anchorIndex - position * (nextCount - 1)), nextCount, total);
  }

  function panRange(range, total, direction) {
    checkRange(range, total);
    if (direction !== -1 && direction !== 1) throw new TypeError('Pan direction must be -1 or 1');
    const count = range.end - range.start + 1;
    return fitRange(range.start + direction * Math.max(1, Math.round(count / 2)), count, total);
  }

  function latestRange(range, total) {
    checkRange(range, total);
    const count = range.end - range.start + 1;
    return { start: total - count, end: total - 1 };
  }

  function reconcileRange(range, oldTotal, newTotal) {
    checkRange(range, oldTotal);
    checkTotal(newTotal);
    if (range.start === 0 && range.end === oldTotal - 1) return { start: 0, end: newTotal - 1 };
    const count = range.end - range.start + 1;
    return fitRange(range.end === oldTotal - 1 ? newTotal - count : range.start, count, newTotal);
  }

  function snapshotDate(snapshot) {
    const date = snapshot?.as_of;
    if (typeof date !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(date)) throw new TypeError('Snapshot requires an ISO date');
    const stamp = Date.parse(`${date}T00:00:00Z`);
    if (!Number.isFinite(stamp) || new Date(stamp).toISOString().slice(0, 10) !== date) throw new TypeError('Snapshot date is invalid');
    return date;
  }

  function isOlderSnapshot(candidate, current) {
    const date = snapshotDate(candidate);
    return current == null ? false : date < snapshotDate(current);
  }

  function dataRequestUrl(baseUrl, nonce) {
    if (!(typeof nonce === 'string' && nonce.length > 0) && !Number.isFinite(nonce)) throw new TypeError('Refresh nonce is required');
    const url = new URL('./data.json', baseUrl);
    if (url.protocol !== 'https:' && url.protocol !== 'http:') throw new TypeError('Data URL must use HTTP or HTTPS');
    url.searchParams.set('refresh', String(nonce));
    return url.href;
  }

  const api = { zoomRange, panRange, latestRange, reconcileRange, isOlderSnapshot, dataRequestUrl };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else window.ChartControls = api;
})();
