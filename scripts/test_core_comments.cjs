const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'monitor.js'), 'utf8');
const notice = {message: 'Current color comment', color: '#cfb8e8', level: 1, time: '2026-10-12T09:30'};
const context = vm.createContext({candidateColorNotice: () => notice, statusLabels: {watching: 'Watching'}});
vm.runInContext(source.slice(source.indexOf('function coreStockComment('), source.indexOf('function compareCandidateSpread(')), context);
const at = time => new Date(`2026-10-12T${time}:00+09:00`);
const row = (t, m3, m10, extra = {}) => ({t: `2026-10-12T${t}`, m3, m10, ...extra});
const item = rows => ({status: 'watching', displayCharts: {intraday: {series: rows}}});
const up = [row('09:00', 101, 100), row('09:30', 103, 101)];
const down = [row('09:00', 100, 101), row('09:30', 98, 100)];
const comment = (rows, time = '10:00') => context.coreStockComment(item(rows), at(time));
assert.equal(comment(up).direction, 'up');
assert.match(comment(up).message, /3ma가 하락/);
assert.equal(comment(down).direction, 'down');
assert.match(comment(down).message, /기다려 주시기 바랍니다/);
for (const rows of [
  [row('09:00', 103, 100), row('09:30', 104, 102)], // Rising convergence.
  [row('09:00', 100, 103), row('09:30', 99, 101)], // Falling convergence.
  [row('09:00', 101, 100), row('09:30', 102, 99)], // Mixed directions.
  [row('09:00', 101, 100), row('09:30', 102, 101)], // Parallel.
  [row('09:00', 101, 100), row('09:30', 102, 100)], // One horizontal.
  [row('09:00', 101, 100), row('09:30', 101, 100)],
  [row('09:00', 101, 100), row('09:30', null, 100)],
  [row('09:00', 101, 100)], [],
]) {
  assert.equal(comment(rows).direction, '');
  assert.equal(comment(rows).message, '주가추세를 지켜봐야 하는시점인것 같습니다.');
}
assert.equal(comment([...up, row('10:00', 90, 100, {complete: false})]).direction, 'up',
  'Incomplete native candles do not change the comment');
assert.equal(comment([...up, row('11:30', 90, 100)]).direction, 'up',
  'Future candles are not used');
assert.equal(comment(up.map(r => ({...r, t: r.t.replace('10-12', '10-08')}))).direction, '',
  'Previous-session candles cannot produce a morning buy-timing comment');
assert.equal(comment(up, '09:00').direction, '', 'Two completed today candles are required');
assert.equal(comment(up, '11:00').direction, 'up');
for (const time of ['08:59', '11:01', '17:00']) {
  assert.equal(comment(up, time).message, notice.message);
  assert.equal(comment(up, time).direction, '');
}
assert.equal(comment(up).color, notice.color);
assert.equal(comment(up).level, notice.level);
assert.equal(comment(up).time, up[1].t);
context.candidateColorNotice = () => null;
assert.equal(comment(up, '17:00').message, 'Watching');
assert.equal(comment(up).direction, 'up');
console.log('Core morning MA3/MA10 comment tests passed.');
