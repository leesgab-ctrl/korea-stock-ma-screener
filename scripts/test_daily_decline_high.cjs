const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'monitor.js'), 'utf8');
const context = vm.createContext({});
vm.runInContext(source.slice(source.indexOf('function dailyDeclineHigh('), source.indexOf('function drawDailyChart(')), context);
const rows = Array.from({ length: 75 }, (_, index) => ({
  d: `day-${index}`, m60: index < 65 ? 6000 - index * 20 : 4700 + (index - 65) * 5, c: 3000 + index,
}));
rows[14].c = 9000; // Outside the final 60-session window.
rows[15].c = 4300;
rows[20].h = 8000; // Intraday highs do not determine a closing-price level.
rows[70].c = 10000; // An ascending MA60 session must not set the level.
const find = context.dailyDeclineHigh;
assert.equal(find(rows).index, 15);
assert.equal(find(rows).price, 4300);
assert.equal(find(rows, 64), null, 'Continued steady decline is not a finishing pattern');
const slowing = rows.slice(0, 65).map(row => ({ ...row }));
for (let index = 60; index <= 64; index += 1) slowing[index].m60 = slowing[59].m60 - (index - 59) * 5;
assert.equal(find(slowing).index, 14);
assert.equal(find(rows, 69).price, 9000, 'Past-day lookup has its own 60-session window');
rows[74].c = 20000;
assert.equal(find(rows, 69).price, 9000, 'Future highs never affect a past-day lookup');
const rising = rows.map((row, index) => ({ ...row, m60: 1000 + index }));
assert.equal(find(rising), null, 'No declining MA60 sessions means no reference line');
const flat = rows.map(row => ({ ...row, m60: 1000 }));
assert.equal(find(flat), null);
const recovered = rows.map(row => ({ ...row }));
recovered[74].m60 = 7000;
assert.equal(find(recovered), null, 'A recovered, overall rising MA60 is not a decline-finishing pattern');
const missing = rows.map(row => ({ ...row, m60: null }));
assert.equal(find(missing), null);
assert.equal(find([]), null);
assert.equal(find([{ m60: 1000, c: 500 }]), null);
rows[15].c = 0;
rows[20].c = 4300;
rows[21].c = 4300;
assert.equal(find(rows).index, 20, 'Equal closing highs use the first qualifying date');
context.dailyPriceLabel = price => String(price);
const calls = [];
const ctx = Object.fromEntries(['save', 'restore', 'beginPath', 'stroke', 'fillRect', 'fillText'].map(name => [name, (...args) => calls.push([name, ...args])]));
ctx.measureText = label => ({ width: label.length * 5 });
ctx.setLineDash = dash => calls.push(['dash', ...dash]);
ctx.moveTo = (x, y) => calls.push(['start', x, y]);
ctx.lineTo = (x, y) => calls.push(['end', x, y]);
const draw = context.drawDailyDeclineHigh;
draw(ctx, { index: 15, date: 'day-15', price: 4300 }, 10, 60, index => 40 + index * 4, price => price / 100,
  { left: 40, right: 4, top: 28 }, 390, 170, true);
assert.deepEqual(calls.find(row => row[0] === 'dash'), ['dash', 6, 4]);
assert.deepEqual(calls.find(row => row[0] === 'start'), ['start', 60, 43]);
assert.deepEqual(calls.find(row => row[0] === 'end'), ['end', 276, 43]);
assert.ok(calls.find(row => row[0] === 'fillText')[3] >= 62, 'Reference label avoids the registration label row');
calls.length = 0;
draw(ctx, { index: 15, price: 4300 }, 20, 10, index => 40 + index * 4, price => price / 100,
  { left: 40, right: 4, top: 28 }, 390, 170, true);
assert.equal(calls.find(row => row[0] === 'start')[1], 40, 'Offscreen reference starts at the visible plot edge');
console.log('Daily MA60 decline-high reference tests passed.');
