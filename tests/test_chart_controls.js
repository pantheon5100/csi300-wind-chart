'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {
  zoomRange, panRange, latestRange, reconcileRange, isOlderSnapshot, dataRequestUrl,
} = require('../site/chart-controls.js');

test('zooming in follows the latest date and can return to the full range', () => {
  const full = { start: 0, end: 99 };
  const zoomed = zoomRange(full, 100, 0.5);
  assert.deepEqual(zoomed, { start: 50, end: 99 });
  assert.deepEqual(zoomRange(zoomed, 100, 2), full);
  assert.deepEqual(full, { start: 0, end: 99 });
});

test('zoom preserves a chosen anchor position when the boundaries allow it', () => {
  const range = { start: 100, end: 200 };
  const zoomed = zoomRange(range, 500, 0.5, 150);
  assert.deepEqual(zoomed, { start: 125, end: 175 });
  assert.equal((150 - zoomed.start) / (zoomed.end - zoomed.start), 0.5);
  const evenRange = { start: 100, end: 199 };
  assert.deepEqual(zoomRange(zoomRange(evenRange, 500, 0.5, 140), 500, 2, 140), evenRange);
});

test('zoom respects the minimum window and both ends of the history', () => {
  assert.deepEqual(zoomRange({ start: 94, end: 99 }, 100, 0.01), { start: 95, end: 99 });
  assert.deepEqual(zoomRange({ start: 95, end: 99 }, 100, 0.5), { start: 95, end: 99 });
  assert.deepEqual(zoomRange({ start: 0, end: 9 }, 100, 2, 0), { start: 0, end: 19 });
  assert.deepEqual(zoomRange({ start: 25, end: 74 }, 100, 100), { start: 0, end: 99 });
  assert.deepEqual(zoomRange({ start: 0, end: 2 }, 3, 0.5), { start: 0, end: 2 });
  assert.deepEqual(zoomRange({ start: 0, end: 0 }, 1, 2), { start: 0, end: 0 });
});

test('pan moves by half a window without changing its width or leaving the data', () => {
  assert.deepEqual(panRange({ start: 40, end: 59 }, 100, 1), { start: 50, end: 69 });
  assert.deepEqual(panRange({ start: 40, end: 59 }, 100, -1), { start: 30, end: 49 });
  assert.deepEqual(panRange({ start: 5, end: 24 }, 100, -1), { start: 0, end: 19 });
  assert.deepEqual(panRange({ start: 75, end: 94 }, 100, 1), { start: 80, end: 99 });
  assert.deepEqual(panRange({ start: 0, end: 99 }, 100, 1), { start: 0, end: 99 });
});

test('latest preserves the selected window size and lands on the exact last record', () => {
  assert.deepEqual(latestRange({ start: 20, end: 39 }, 100), { start: 80, end: 99 });
  assert.deepEqual(latestRange({ start: 0, end: 99 }, 100), { start: 0, end: 99 });
});

test('new data extends the full view and moves a recent window while preserving its size', () => {
  assert.deepEqual(reconcileRange({ start: 0, end: 99 }, 100, 103), { start: 0, end: 102 });
  assert.deepEqual(reconcileRange({ start: 80, end: 99 }, 100, 103), { start: 83, end: 102 });
});

test('refresh preserves a historical view and handles unchanged or shorter histories', () => {
  const history = { start: 30, end: 49 };
  assert.deepEqual(reconcileRange(history, 100, 103), history);
  assert.deepEqual(reconcileRange(history, 100, 100), history);
  assert.deepEqual(reconcileRange({ start: 80, end: 99 }, 100, 90), { start: 70, end: 89 });
  assert.deepEqual(reconcileRange(history, 100, 10), { start: 0, end: 9 });
});

test('rollback detection distinguishes older dates from unchanged and newer observations', () => {
  const current = { as_of: '2026-11-19' };
  assert.equal(isOlderSnapshot({ as_of: '2026-11-16' }, current), true);
  assert.equal(isOlderSnapshot({ as_of: '2026-11-19' }, current), false);
  assert.equal(isOlderSnapshot({ as_of: '2026-11-20' }, current), false);
  assert.equal(isOlderSnapshot(current, null), false);
  assert.throws(() => isOlderSnapshot({ as_of: '2026-02-30' }, current), TypeError);
  assert.throws(() => isOlderSnapshot({ as_of: '11/19/2026' }, current), TypeError);
});

test('refresh URLs remain inside the Pages project and change for each new nonce', () => {
  const base = 'https://pantheon5100.github.io/csi300-wind-chart/';
  const first = dataRequestUrl(base, 1234);
  assert.equal(first, base + 'data.json?refresh=1234');
  assert.notEqual(dataRequestUrl(base, 1235), first);
  assert.equal(dataRequestUrl(base + 'index.html?previous=1#charts', 'manual 2'), base + 'data.json?refresh=manual+2');
  assert.equal(dataRequestUrl('http://localhost:8000/index.html', 1), 'http://localhost:8000/data.json?refresh=1');
});

test('invalid controls are rejected instead of creating unusable chart bounds', () => {
  assert.throws(() => zoomRange({ start: 0, end: 9 }, 10, 0), TypeError);
  assert.throws(() => zoomRange({ start: 0, end: 9 }, 10, Infinity), TypeError);
  assert.throws(() => zoomRange({ start: 0, end: 9 }, 10, 0.5, 10), TypeError);
  assert.throws(() => panRange({ start: 0, end: 9 }, 10, 0), TypeError);
  assert.throws(() => latestRange({ start: 0, end: 10 }, 10), TypeError);
  assert.throws(() => reconcileRange({ start: 0, end: 9 }, 10, 0), TypeError);
  assert.throws(() => dataRequestUrl('https://example.com/', undefined), TypeError);
  assert.throws(() => dataRequestUrl('file:///tmp/index.html', 1), TypeError);
});
