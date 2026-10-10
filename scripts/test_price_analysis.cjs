const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const context = {};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../price-analysis.js'), 'utf8'), context);
const payload = { dates: ['2026-10-01', '2026-10-02', '2026-10-06'], records: [
  {id: 'a', date: '2026-10-01', color: '#aaa', change: 0, ruleVersion: '1'},
  {id: 'a', date: '2026-10-02', color: '#bbb', change: 5, ruleVersion: '1'},
  {id: 'b', date: '2026-10-01', color: '#aaa', change: 0, ruleVersion: '1'},
  {id: 'b', date: '2026-10-02', color: '#aaa', change: -1, ruleVersion: '1'},
  {id: 'a', date: '2026-10-06', color: '#ccc', change: 99, ruleVersion: '1'},
] };
const result = context.aggregatePriceAnalysis(payload, 5, '2026-10-06');
assert.equal(result.total, 4);
assert.equal(result.groups.length, 2);
assert.equal(result.groups[0].average, 5);
assert.equal(result.groups[0].weight, 25);
assert.equal(result.unchanged, 1);
assert.equal(result.missing, 2);
payload.records[1].ruleVersion = '2';
assert.equal(context.aggregatePriceAnalysis(payload, 5, '2026-10-06').groups.length, 1);
assert.equal(context.aggregatePriceAnalysis(payload, 1, '2026-10-06').total, 2);
payload.records[1].ruleVersion = '1';
payload.records[1].change = (120 / 100 - 1) * 100;
assert.equal(context.aggregatePriceAnalysis(payload, 5, '2026-10-06').groups[0].largeGains, 1);
payload.records[1].change = 19.99;
assert.equal(context.aggregatePriceAnalysis(payload, 5, '2026-10-06').groups[0].largeGains, 0);
const volumes = {dates: ['2026-10-01', '2026-10-02', '2026-10-06'], records: [
  {id: 'v', date: '2026-10-01', color: '#aaa', change: 0, volume: 100, ruleVersion: '1'},
  {id: 'v', date: '2026-10-02', color: '#aaa', change: 1, volume: 120, ruleVersion: '1'},
  {id: 'v', date: '2026-10-06', color: '#bbb', change: 2, volume: 9999, ruleVersion: '1'},
]};
assert.equal(context.aggregatePriceAnalysis(volumes, 1, '2026-10-07').groups[0].volumeAverage, 120);
volumes.records[0].volume = 0;
assert.equal(context.aggregatePriceAnalysis(volumes, 1, '2026-10-07').groups[0].volumeAverage, null);
assert.equal(context.aggregatePriceAnalysis(volumes, 1, '2026-10-07').groups[0].rows.length, 1);
const pairs = [
  {from: 'a', to: 'x', 5: {average: 10, rows: [{change: 10}]}},
  {from: 'a', to: 'y', 5: {average: 0, rows: Array.from({length: 9}, () => ({change: 0}))}},
  {from: 'b', to: 'z', 5: {average: 2, rows: [{change: 2}]}},
];
assert.equal(context.sortPriceAnalysisPairs(pairs, 5)[0].from, 'a');
const ordered = context.sortPriceAnalysisPairs(pairs, 5, true);
assert.equal(ordered[0].from, 'b');
assert.equal(ordered[1].to, 'x');
assert.equal(ordered[2].to, 'y');
console.log('Price analysis tests passed');
