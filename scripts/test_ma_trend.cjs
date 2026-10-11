const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'monitor.js'), 'utf8');
const lines = [];
const context = vm.createContext({
  window: {devicePixelRatio: 1}, formatter: new Intl.NumberFormat('en-US'),
  formatDateTime: value => value,
  watchQuoteDates: items => [...new Set(items.flatMap(item => (item.displayCharts?.intraday?.series || []).map(row => row.t.slice(0, 10))))].sort().slice(-2),
  drawLine: (ctx, rows, field, color, width, x, y) => lines.push({field, color, points: rows.map((row, i) => [x(i), y(row[field])])}),
});
vm.runInContext(source.slice(source.indexOf('function maTrendData('), source.indexOf('function renderWatchQuotes(')), context);
const item = {displayCharts: {
  intraday: {series: [
    {t: '2026-10-07T19:30', m3: 90, m10: 95},
    {t: '2026-10-08T09:00', m3: 100, m10: 101},
    {t: '2026-10-08T09:30', m3: 102, m10: 103},
    {t: '2026-10-08T10:00', m3: 1000, m10: 1100, complete: false},
  ]},
  daily: {history: [
    {d: '2026-10-06', c: 95}, {d: '2026-10-07', c: 99}, {d: '2026-10-08', c: 200},
  ]},
}};
const data = context.maTrendData(item);
assert.equal(data.rows.length, 2);
assert.ok(data.rows.every(row => row.t.startsWith('2026-10-08')));
assert.equal(data.previousClose, 99);
assert.equal(context.maTrendData(item, '2026-10-09').rows.length, 0, 'Missing today must not show yesterday MA lines');
const fallback = {intraday: {quoteTime: '2026-10-08T15:30', quotePreviousClose: 98}};
assert.equal(context.maTrendData(fallback, '2026-10-08').previousClose, 98);
assert.equal(context.maTrendData(fallback, '2026-10-09').previousClose, null, 'Stale quote baseline must not be reused');
assert.equal(context.maTrendData({displayCharts: {daily: {series: [{d: '2026-10-07', c: 0}]}}}, '2026-10-08').previousClose, null);
const strokes = [];
let start, end;
const ctx = {scale() {}, clearRect() {}, beginPath() {}, moveTo(x, y) {start = [x, y];}, lineTo(x, y) {end = [x, y];}, stroke() {strokes.push({color: this.strokeStyle, width: this.lineWidth, start, end});}};
const canvas = {getContext: () => ctx, setAttribute() {}};
context.drawMaTrend(canvas, item, '2026-10-08');
assert.equal(strokes.length, 2);
assert.equal(strokes[0].color, '#c5cbca');
assert.equal(strokes[0].width, 1);
assert.deepEqual(strokes[0].start, [0, 18]);
assert.deepEqual(strokes[0].end, [96, 18]);
const twoBarOrigin = 93 - 2 * 90 / 22;
assert.deepEqual(strokes[1].start, [twoBarOrigin, 0]);
assert.deepEqual(strokes[1].end, [twoBarOrigin, 36]);
assert.equal(lines.length, 2);
assert.equal(lines[0].field, 'm3');
assert.equal(lines[1].field, 'm10');
assert.ok(lines[0].points[0][0] > twoBarOrigin);
assert.equal(lines[0].points[1][0], 93);
assert.equal(lines[1].points[1][1], 3, 'MA lines and prior close use the same price scale');
assert.ok(canvas.title.includes('99'));
lines.length = 0;
strokes.length = 0;
context.drawMaTrend(canvas, {displayCharts: {intraday: {series: item.displayCharts.intraday.series.slice(0, 2)}, daily: item.displayCharts.daily}}, '2026-10-08');
const oneBarOrigin = strokes[1].start[0];
assert.ok(oneBarOrigin > twoBarOrigin, 'The reference moves left as bars accumulate');
assert.equal(lines[0].points[0][0], 93, 'The first completed bar is visible at the right edge');
assert.equal(strokes.length, 4, 'The first bar has two short MA line marks');
strokes.length = 0;
context.drawMaTrend(canvas, {intraday: {quoteTime: '2026-10-08T09:00', quotePreviousClose: 99}}, '2026-10-08');
assert.deepEqual(strokes[1].start, [93, 0], 'Before any completed bars, the cross starts at the right');
assert.equal(strokes[0].start[1], 18, 'With only prior close available, its horizontal reference is centered');
console.log('Today-only MA trend, scrolling cross reference, and previous closing tests passed.');
