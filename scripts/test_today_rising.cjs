const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'monitor.js'), 'utf8');
const context = vm.createContext({state: {payload: {candidates: []}}});
vm.runInContext(source.slice(source.indexOf('function displayQuote('), source.indexOf('function quoteText('))
  + source.slice(source.indexOf('function isTodayRising('), source.indexOf('function renderCandidates(')), context);
const date = '2026-10-12';
const item = price => ({code: '123456', intraday: {quotePrice: price, quoteTime: `${date}T15:30+09:00`, quotePreviousClose: 100}});
assert.equal(context.isTodayRising(item(105), date), true);
assert.equal(context.isTodayRising(item(100), date), false);
assert.equal(context.isTodayRising(item(95), date), false);
assert.equal(context.isTodayRising(item(105), '2026-10-13'), false, 'Stale positive quotes cannot appear in today rising');
assert.equal(context.isTodayRising({}, date), false);
assert.equal(context.isTodayRising({code: '123456', intraday: {quotePrice: 105, quoteTime: `${date}T15:30+09:00`}}, date), false,
  'Missing previous closing prices must not invent a positive return');
const native = item(105);
native.displayCharts = {intraday: {series: [{t: `${date}T16:00+09:00`, c: 99}]}, daily: {series: [{d: '2026-10-08', c: 100}]}};
assert.equal(context.isTodayRising(native, date), false, 'The latest native chart quote takes precedence over an older positive quote');
native.displayCharts.intraday.series[0].c = 101;
assert.equal(context.isTodayRising(native, date), true);
console.log('Today rising uses positive current-session returns and rejects stale/missing quotes.');
