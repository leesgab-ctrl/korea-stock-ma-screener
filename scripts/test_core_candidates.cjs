const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'monitor.js'), 'utf8');
const context = vm.createContext({});
vm.runInContext(source.slice(source.indexOf('function chartPhases('), source.indexOf('function drawChart('))
  + source.slice(source.indexOf('function candidateColorNotice('), source.indexOf('function compareCandidateSpread('))
  + source.slice(source.indexOf('function watchQuoteDates('), source.indexOf('function renderCandidates(')), context);
const rows = [
  {t: '2026-10-08T09:00', m3: 100.5, m10: 100.6, m20: 100.7, m40: 100, m60: 100},
  {t: '2026-10-08T09:30', m3: 100.5, m10: 100.6, m20: 100.7, m40: 100.2, m60: 100},
];
assert.equal(context.isCoreCandidate({displayCharts: {intraday: {series: rows}}}), true);
assert.equal(context.isCoreCandidate({displayCharts: {intraday: {series: [...rows, {...rows[1], complete: false, m3: 1000}]}}}), true,
  'Incomplete candles cannot remove a qualifying core candidate');
assert.equal(context.isCoreCandidate({}), false);
const priorRows = [...rows, {...rows[1], t: '2026-10-09T09:00', m3: 120, m10: 115, m20: 110, m40: 105, m60: 100}];
assert.equal(context.isCoreCandidate({displayCharts: {intraday: {series: priorRows}}}), true,
  'A qualifying prior-day color includes a stock even when today no longer qualifies');
assert.equal(context.isCoreCandidate({displayCharts: {intraday: {series: priorRows}}}, ['2026-10-09', '2026-10-10']), false,
  'An older qualifying color outside prior/today cannot include a stock');
const snapshots = context.watchColorSnapshots({displayCharts: {intraday: {series: priorRows}}});
assert.equal(snapshots[0].phase.t, rows[1].t);
assert.equal(snapshots[0].display.color, context.phaseDisplay(context.chartPhases(rows).at(-1)).color);
assert.equal(context.watchColorSnapshots({displayCharts: {intraday: {series: [...rows, {...rows[1], t: '2026-10-09T09:00', complete: false}]}}})[1].phase, null,
  'An incomplete today candle cannot masquerade as the final completed today color');
context.watchColorSnapshots = item => [{display: item.notice}];
for (const [phase, level, compact, expected] of [
  ['reference', 1, false, true], ['reference', 3, true, true],
  ['observation', 1, false, true], ['observation', 2, true, true],
  ['before', 2, true, true], ['before', 1, false, false],
  ['reference', 0, false, false], ['observation', 0, false, false],
  ['confirmed', 3, true, false], ['fast', 3, true, false], ['pullback', 2, false, false],
]) assert.equal(context.isCoreCandidate({notice: {phase, level, compact}}), expected, `${phase}, level ${level}, X ${compact}`);
console.log('Core candidate color/X filtering tests passed.');
