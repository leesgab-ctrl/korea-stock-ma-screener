const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'monitor.js'), 'utf8');
const helpers = source.slice(source.indexOf('function dailyQuoteUnit('), source.indexOf('function drawDailyChart('));
const context = vm.createContext({});
vm.runInContext(helpers, context);
assert.equal(context.dailyPriceLabel(40150), '40,150');
assert.equal(context.dailyPriceLabel(500000), '500,000');
assert.equal(context.dailyPriceLabel(999), '999');

for (const [price, expected] of [[1999, 1], [2000, 5], [5000, 10], [20000, 50], [50000, 100], [200000, 500], [500000, 1000]]) {
  assert.equal(context.dailyQuoteUnit(price), expected);
}
for (const boundary of [2000, 5000, 20000, 50000, 200000, 500000]) {
  for (const price of [boundary - 0.1, boundary, boundary + 0.1]) {
    for (const rounding of [Math.floor, Math.round, Math.ceil]) {
      const rounded = context.dailyQuotePrice(price, rounding);
      assert.equal(rounded % context.dailyQuoteUnit(rounded), 0);
      if (rounding === Math.floor) assert.ok(rounded <= price);
      if (rounding === Math.ceil) assert.ok(rounded >= price);
    }
  }
}
for (const [min, max] of [[1, 2], [4995, 5010], [36000, 40950], [199900, 201000], [500000, 900000]]) {
  const ticks = Array.from(context.dailyPriceTicks(min, max));
  assert.equal(new Set(ticks).size, ticks.length);
  assert.equal(ticks[0], max);
  assert.equal(ticks.at(-1), min);
  ticks.forEach((price, index) => {
    assert.match(String(price), /^\d+$/);
    assert.equal(price % context.dailyQuoteUnit(price), 0);
    if (index) assert.ok(price < ticks[index - 1]);
  });
}
console.log('Daily quote-aligned price tick tests passed.');
