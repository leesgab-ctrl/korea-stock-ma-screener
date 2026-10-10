const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'history.js'), 'utf8');
const context = {};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function historyEvaluation('), source.indexOf('function renderSummary(')), context);
const daily = Array.from({length: 11}, (_, i) => ({d: `2026-10-${String(i + 1).padStart(2, '0')}`, c: 100 + i, complete: true}));
const item = {registeredAt: '2026-10-01T12:00:00+09:00', registrationPrice: 100,
  verificationEndDate: '2026-10-11', displayCharts: {daily: {history: daily.slice(0, 6), series: daily.slice(5)}}};
const result = context.historyEvaluation(item);
assert.equal(result.registered, '2026-10-01');
assert.ok(Math.abs(result.fiveReturn - 5) < 1e-9);
assert.ok(Math.abs(result.peakReturn - 10) < 1e-9);
item.verificationEndDate = '2026-10-04';
assert.equal(context.historyEvaluation(item).fiveReturn, null);
assert.equal(context.historyEvaluation(item).peakReturn, null);
const payload = JSON.parse(fs.readFileSync(path.join(root, 'data/candidate-monitor.json')));
const originals = new Map([...payload.history, ...payload.candidates].map(row => [row.id || row.code + '|' + row.registeredAt, row]));
const entries = JSON.parse(fs.readFileSync(path.join(root, 'data/reference-history.json'))).records;
assert.equal(new Set(entries.map(row => row.id)).size, entries.length);
for (const entry of entries) {
  const original = originals.get(entry.id);
  if (!original) continue;
  assert.ok(Date.parse(entry.registeredAt) >= Date.parse(original.registeredAt));
  assert.ok(entry.registrationPrice > 0);
  const chart = original.displayCharts.intraday;
  const rows = [...new Map([...(chart.history || []), ...(chart.series || [])].map(row => [row.t, row])).values()]
    .filter(row => row.complete !== false && Date.parse(row.t) >= Date.parse(original.registeredAt)).sort((a, b) => a.t.localeCompare(b.t));
  const index = rows.findIndex(row => row.t === entry.registeredAt);
  assert.ok(index >= 3);
  const recent = rows.slice(index - 3, index + 1);
  assert.ok(recent.every(row => row.m20 > row.m40));
  assert.ok(recent.slice(1).every((row, i) => row.m40 > recent[i].m40));
  assert.equal(rows[index].c, entry.registrationPrice);
}
console.log(`Reference history tests passed (${entries.length} entries)`);
