const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'monitor.js'), 'utf8');
const context = vm.createContext({});
vm.runInContext(source.slice(source.indexOf('function chartSeriesWithMa120('), source.indexOf('function drawChart(')), context);
const bars = Array.from({length: 125}, (_, index) => ({
  t: new Date(Date.UTC(2026, 9, 1, 0, index * 30)).toISOString(), c: 100 + index,
  m3: 100, m10: 101, m20: 102, m40: 103, m60: 104,
}));
const chart = {history: bars, series: bars.slice(118)};
const original = JSON.stringify(chart);
const result = context.chartSeriesWithMa120(chart);
assert.equal(result[0].m120, null);
assert.equal(result[1].m120, 159.5);
assert.equal(result.at(-1).m120, 164.5);
assert.equal(result.at(-1).m3, 100);
assert.equal(JSON.stringify(chart), original, 'Display fallback must not mutate analysis data');
assert.equal(context.chartSeriesWithMa120({series:bars.slice(-119)}).at(-1).m120, null);
assert.equal(context.chartSeriesWithMa120({history:bars,series:[{...bars.at(-1),m120:123.45}]}).at(-1).m120, 123.45);
assert.equal(context.chartSeriesWithMa120({history:bars,series:[{...bars.at(-1),m120:null}]}).at(-1).m120, null);
const invalid = bars.map(row => ({...row}));
invalid[60].c = null;
assert.equal(context.chartSeriesWithMa120({series:invalid}).at(-1).m120, null);
assert.equal(context.chartSeriesWithMa120().length, 0);
console.log('Display-only MA120 history, warmup, cache, and immutability checks passed.');
