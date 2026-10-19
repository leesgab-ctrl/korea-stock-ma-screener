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
const now = new Date('2026-10-16T20:30+09:00');
let matches = context.recentImagePatternMatches(search, candidates, confirmed, now);
assert.deepEqual(Array.from(matches, item => item.id), ['latest']);
assert.equal(JSON.stringify(search), original, 'Display filtering must not delete stored history or change alert state');
assert.equal(context.recentImagePatternMatches(search, [], confirmed, now).length, 0);
assert.equal(context.recentImagePatternMatches({...search, status: 'error'}, candidates, confirmed, now).length, 0);
assert.equal(context.recentImagePatternMatches(search, candidates, new Set(), now).length, 0);
assert.deepEqual(Array.from(context.recentImagePatternMatches(search, candidates, confirmed,
  new Date('2026-10-15T10:00+09:00')), item => item.id), ['prior', 'utc']);
// Keep yesterday/today color snapshots unchanged; filter only capture history.
assert.deepEqual(Array.from(context.watchQuoteDates(candidates)), ['2026-10-15', '2026-10-16']);
// Stale Friday charts must not make Friday's signals reappear at Monday's opening.
assert.equal(context.recentImagePatternMatches(search, candidates, confirmed,
  new Date('2026-10-19T09:00+09:00')).length, 0);
assert.equal(context.recentImagePatternMatches(search, candidates, confirmed,
  new Date('2026-10-17T12:00+09:00')).length, 0);
assert.equal(context.recentImagePatternMatches(search, candidates, confirmed,
  new Date('2026-10-16T15:00:00Z')).length, 0, 'KST midnight must clear yesterday');
candidates[0].displayCharts.intraday.series.push({t: '2026-10-19T09:00+09:00', complete: false});
matches = context.recentImagePatternMatches(search, candidates, confirmed, new Date('2026-10-19T09:30+09:00'));
assert.deepEqual(Array.from(matches, item => item.id), ['future']);

const container = {dataset: {day: '2026-10-16'}};
context.state = {view: 'imageSearch', payload: {}};
context.document = {visibilityState: 'visible', querySelector: selector => selector === '#imageSearchMatches' ? container : null};
let renders = 0;
context.imagePatternDay = () => '2026-10-19';
context.renderImageSearch = () => { renders++; container.dataset.day = '2026-10-19'; };
vm.runInContext(source.slice(source.indexOf('function refreshImagePatternDay('),
  source.indexOf('document.addEventListener("visibilitychange"')), context);
context.refreshImagePatternDay();
context.refreshImagePatternDay();
assert.equal(renders, 1, 'An open page clears the prior date once without repeatedly rebuilding charts');
context.state.view = 'capture';
container.dataset.day = '2026-10-16';
context.refreshImagePatternDay();
assert.equal(renders, 1, 'The study board must not be rebuilt');
console.log('Image recency passed: today only, KST dates, stale opening data, future signals, day rollover and history preservation.');
