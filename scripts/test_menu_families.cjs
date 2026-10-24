const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'monitor.js'), 'utf8');
const state = {view: 'ak', keyword: ''};
const context = vm.createContext({state, chartGroup: () => 'target', newRegistrationLabel: () => ''});
vm.runInContext(source.slice(source.indexOf('function matchesFamilyView('), source.indexOf('function renderImageSearch('))
  + source.slice(source.indexOf('function filteredCandidates('), source.indexOf('function candidateMaximumSpread(')), context);
const candidates = [
  {code: '000001', name: 'Old AK', candidateTier: 'expanded_ak', registeredAt: '2026-10-01T16:40:00+09:00'},
  {code: '000002', name: 'New AK', candidateTier: 'expanded_ak', registeredAt: '2026-10-14T16:40:00+09:00'},
  {code: '000003', name: 'Core AG', candidateTier: 'core', registeredAt: '2026-10-13T16:40:00+09:00', status: 'insufficient'},
  {code: '000004', name: 'Legacy AG', dailySignalDate: '2026-10-12'},
  {code: '000005', name: 'Retired expanded AG', candidateTier: 'expanded'},
  {code: '000003', name: 'Separate AH', candidateTier: 'ah', dailySignalDate: '2026-10-22'},
  {code: '000006', name: 'New AH', candidateTier: 'ah', dailySignalDate: '2026-10-23'},
];
assert.deepEqual(Array.from(context.filteredCandidates(candidates).sort(context.compareRegistrationNewest), item => item.code), ['000002', '000001']);
state.view = 'ag';
assert.deepEqual(Array.from(context.filteredCandidates(candidates).sort(context.compareRegistrationNewest), item => item.code), ['000003', '000004']);
state.keyword = '000004';
assert.deepEqual(Array.from(context.filteredCandidates(candidates), item => item.code), ['000004']);
assert.equal(context.matchesFamilyView(candidates[4], 'ag'), false);
assert.equal(context.matchesFamilyView(candidates[0], 'ag'), false);
assert.equal(context.matchesFamilyView(candidates[2], 'ak'), false);
state.view = 'ah';
state.keyword = '';
assert.deepEqual(Array.from(context.filteredCandidates(candidates).sort(context.compareRegistrationNewest), item => item.code), ['000006', '000003']);
assert.equal(context.matchesFamilyView(candidates[2], 'ah'), false);
assert.equal(context.matchesFamilyView(candidates[5], 'ag'), false);
state.payload = {candidates: [candidates[2]], analysisCandidates: [candidates[6]]};
state.ahPayload = {candidates: [candidates[5], candidates[6]]};
const shared = context.sharedMonitorCandidates();
assert.equal(shared.length, 2);
assert.deepEqual(Array.from(shared.find(item => item.code === '000003').screeningFamilies), ['A-G', 'A-H']);
assert.equal(candidates[2].screeningFamilies, undefined);
state.payload.manualExclusions = [{code: '000006'}];
assert.equal(context.sharedMonitorCandidates().length, 1);
const ahEvent = {code: '000003', signalType: 'ahMa120Ma180', source: 'ah', completedAt: '2026-10-23T14:30:00+09:00', approximate: true};
state.ahPayload.candidates[0].reviewMatches = [ahEvent];
assert.equal(context.recentImagePatternMatches({status: 'ok', matches: []}, shared, new Set(), new Date('2026-10-23T16:00:00+09:00')).length, 1);
assert.equal(context.recentImagePatternMatches({status: 'error'}, shared, new Set(), new Date('2026-10-23T16:00:00+09:00')).length, 1);
assert.equal(context.recentImagePatternMatches({status: 'ok'}, shared, new Set(), new Date('2026-10-24T09:00:00+09:00')).length, 0);
assert.equal(context.recentImagePatternMatches({status: 'ok'}, shared, new Set(), new Date('2026-10-23T14:00:00+09:00')).length, 0);
assert.equal(context.recentImagePatternMatches({status: 'ok'}, [], new Set(), new Date('2026-10-23T16:00:00+09:00')).length, 0);
assert.match(context.imagePatternLabel(ahEvent), /AH.*근사/);
const replays = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'data/image-search-replays.json'), 'utf8'));
assert.equal(replays.version, 1);
assert.equal(replays.matches.length, 5);
assert.equal(new Set(replays.matches.map(event => `${event.code}|${event.id}`)).size, 5);
for (const event of replays.matches) {
  assert.equal(event.replayed, true);
  assert.equal(event.displayOnly, true);
  assert.ok(Math.abs((event.price / event.previousClose - 1) * 100 - event.changePct) < 0.000001);
  assert.equal(Date.parse(event.completedAt) - Date.parse(event.time), 30 * 60 * 1000);
}
state.ahPayload = {candidates: []};
state.payload = {asOf: '2026-10-23'};
state.imageReplayPayload = replays;
const replayCandidates = [{code: '365330'}, {code: '225190'}];
const confirmed = new Set(replays.matches.flatMap(event => event.sourceImageIds));
const replayResults = context.recentImagePatternMatches({status: 'ok'}, replayCandidates, confirmed, new Date('2026-10-24T22:00:00+09:00'));
assert.equal(replayResults.length, 5);
assert.deepEqual([...new Set(Array.from(replayResults, event => event.code))].sort(), ['225190', '365330']);
assert.equal(context.recentImagePatternMatches({status: 'ok'}, replayCandidates, new Set(), new Date('2026-10-24T22:00:00+09:00')).length, 5);
assert.equal(context.recentImagePatternMatches({status: 'ok'}, replayCandidates.slice(1), confirmed, new Date('2026-10-24T22:00:00+09:00')).length, 2);
const actual = {...replays.matches[0], replayed: false, displayOnly: false};
const actualResults = context.recentImagePatternMatches({status: 'ok', matches: [actual]}, replayCandidates, confirmed, new Date('2026-10-23T22:00:00+09:00'));
assert.equal(actualResults.length, 5);
assert.equal(actualResults.find(event => event.id === actual.id).replayed, false);
state.payload.asOf = '2026-10-26';
assert.equal(context.recentImagePatternMatches({status: 'ok'}, replayCandidates, confirmed, new Date('2026-10-26T09:00:00+09:00')).length, 0);
state.payload.asOf = '2026-10-23';
const newQuote = [{code: '365330', displayCharts: {quote: {time: '2026-10-26T09:00+09:00'}}}, {code: '225190'}];
assert.equal(context.recentImagePatternMatches({status: 'ok'}, newQuote, confirmed, new Date('2026-10-26T09:01:00+09:00')).length, 0);
console.log('Family menus passed: isolated AK/AG, registration descending, legacy AG, missing charts, retired expanded exclusion and keyword filter.');
