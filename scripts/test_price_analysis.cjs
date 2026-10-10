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
assert.equal(result.groups.length, 1);
assert.equal(result.groups[0].average, 5);
assert.equal(result.groups[0].weight, 25);
assert.equal(result.unchanged, 1);
assert.equal(result.missing, 2);
payload.records[1].ruleVersion = '2';
assert.equal(context.aggregatePriceAnalysis(payload, 5, '2026-10-06').groups.length, 0);
assert.equal(context.aggregatePriceAnalysis(payload, 1, '2026-10-06').total, 2);
console.log('Price analysis tests passed');
