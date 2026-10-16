const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'monitor.js'), 'utf8');
const context = vm.createContext({});
for (const [start, end] of [['function recentImagePatternMatches(', 'function renderImageSearch('], ['function watchQuoteDates(', 'function watchColorSnapshots(']]) {
  vm.runInContext(source.slice(source.indexOf(start), source.indexOf(end)), context);
}
const candidates = [{displayCharts: {intraday: {series: ['2026-10-14', '2026-10-15', '2026-10-16'].map(day => ({t: day + 'T09:00+09:00'}))}}}];
const event = (id, completedAt, image = 'confirmed') => ({id, completedAt, sourceImageIds: [image]});
const search = {status: 'ok', matches: [event('old', '2026-10-14T15:30+09:00'), event('prior', '2026-10-15T09:30+09:00'),
  event('latest', '2026-10-16T20:00+09:00'), event('utc', '2026-10-14T15:30:00Z'), event('future', '2026-10-19T09:30+09:00'),
  event('unconfirmed', '2026-10-16T10:00+09:00', 'disabled'), event('bad', 'invalid')]};
const confirmed = new Set(['confirmed']);
const original = JSON.stringify(search);
let matches = context.recentImagePatternMatches(search, candidates, confirmed);
assert.deepEqual(Array.from(matches, item => item.id), ['prior', 'latest', 'utc']);
assert.equal(JSON.stringify(search), original, 'Display filtering must not delete stored history or change alert state');
assert.equal(context.recentImagePatternMatches(search, [], confirmed).length, 0);
assert.equal(context.recentImagePatternMatches({...search, status: 'error'}, candidates, confirmed).length, 0);
assert.equal(context.recentImagePatternMatches(search, candidates, new Set()).length, 0);
// A holiday or weekend contributes no chart session; Thursday and Friday remain the latest two dates.
assert.deepEqual(Array.from(context.watchQuoteDates(candidates)), ['2026-10-15', '2026-10-16']);
// As soon as Monday's opening bar exists, only Friday and Monday remain eligible.
candidates[0].displayCharts.intraday.series.push({t: '2026-10-19T09:00+09:00', complete: false});
matches = context.recentImagePatternMatches(search, candidates, confirmed);
assert.deepEqual(Array.from(matches, item => item.id), ['latest', 'future']);
console.log('Image recency passed: latest two market sessions, KST completion date, weekend/holiday gaps, current session, confirmation switches and history preservation.');
