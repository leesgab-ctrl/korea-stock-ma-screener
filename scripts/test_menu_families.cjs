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
console.log('Family menus passed: isolated AK/AG, registration descending, legacy AG, missing charts, retired expanded exclusion and keyword filter.');
