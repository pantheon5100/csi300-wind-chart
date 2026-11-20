'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { calculateCrossings } = require('../site/app.js');

const points = pairs => pairs.map(([date, difference]) => [date, 1000 + difference, 1000, difference / 10, 0]);

test('initial equality and first departure do not count as crossings', () => {
  const result = calculateCrossings(points([
    ['2024-01-01', 0], ['2024-01-02', 0], ['2024-01-03', -2],
  ]));
  assert.deepEqual(result, { events: [], ongoing: null });
});

test('confirms first opposite close, measures calendar and observation intervals separately', () => {
  const result = calculateCrossings(points([
    ['2024-01-01', 0], ['2024-01-02', -2], ['2024-01-05', 5],
    ['2024-01-08', 2], ['2024-01-09', -1], ['2024-01-12', -3],
  ]));
  assert.equal(result.events.length, 2);
  assert.deepEqual(result.events[0], {
    number: 1, date: '2024-01-05', index: 2, direction: 'stock_above', difference: 5,
    previousSideDate: '2024-01-02', equalityObservations: 0, calendarDays: null, dataIntervals: null,
  });
  assert.equal(result.events[1].date, '2024-01-09');
  assert.equal(result.events[1].direction, 'wind_above');
  assert.equal(result.events[1].calendarDays, 4);
  assert.equal(result.events[1].dataIntervals, 2);
  assert.deepEqual(result.ongoing, {
    fromDate: '2024-01-09', toDate: '2024-01-12', calendarDays: 3, dataIntervals: 1,
  });
});

test('zero plateau followed by the opposite sign creates one event at confirmation', () => {
  const result = calculateCrossings(points([
    ['2024-01-01', -1], ['2024-01-02', 0], ['2024-01-03', 0], ['2024-01-04', 2],
  ]));
  assert.equal(result.events.length, 1);
  assert.equal(result.events[0].date, '2024-01-04');
  assert.equal(result.events[0].previousSideDate, '2024-01-01');
  assert.equal(result.events[0].equalityObservations, 2);
  assert.equal(result.events[0].difference, 2);
});

test('zero touch followed by the same sign and a final zero do not count', () => {
  const result = calculateCrossings(points([
    ['2024-01-01', -1], ['2024-01-02', 0], ['2024-01-03', -2], ['2024-01-04', 0],
  ]));
  assert.equal(result.events.length, 0);
  assert.equal(result.ongoing, null);
});

test('floating point zero noise does not create a spurious sign reversal', () => {
  const result = calculateCrossings(points([
    ['2024-01-01', -1], ['2024-01-02', 5e-10], ['2024-01-03', -2],
  ]));
  assert.equal(result.events.length, 0);
});

test('raw index crossings differ from cumulative-return crossings when the bases differ', () => {
  const data = [
    ['2004-12-31', 1000.00, 989.30, 0, 0],
    ['2005-01-04', 982.79, 984.85, -1.721, -0.449813],
    ['2005-01-05', 992.56, 985.90, -0.744, -0.343677],
  ];
  // Raw levels start unequal and reverse twice. The normalized-return gap
  // starts at zero and remains negative thereafter, so it never reverses.
  assert.deepEqual(data.map(row => Math.sign(row[3] - row[4])), [0, -1, -1]);
  const result = calculateCrossings(data);
  assert.deepEqual(result.events.map(event => [event.date, event.direction]), [
    ['2005-01-04', 'wind_above'],
    ['2005-01-05', 'stock_above'],
  ]);
  assert.ok(Math.abs(result.events[0].difference - (-2.06)) < 1e-9);
  assert.ok(Math.abs(result.events[1].difference - 6.66) < 1e-9);
  assert.equal(result.events[0].previousSideDate, '2004-12-31');
});

test('rejects ambiguous dates, unordered dates, and nonfinite index levels', () => {
  assert.throws(() => calculateCrossings(points([['2024-02-30', -1]])), TypeError);
  assert.throws(() => calculateCrossings(points([['2024-01-02', -1], ['2024-01-01', 2]])), TypeError);
  assert.throws(() => calculateCrossings(points([['2024-01-01', NaN]])), TypeError);
  assert.throws(() => calculateCrossings(points([['2024-01-01', Infinity]])), TypeError);
  assert.throws(() => calculateCrossings([['2024-01-01', 1000, Infinity, 0, 0]]), TypeError);
});
