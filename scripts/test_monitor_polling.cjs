const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'monitor.js'), 'utf8');
let now = Date.parse('2026-10-15T12:00:00+09:00');
let timers = new Map();
let timerId = 0;
let renders = 0;
let exclusionRenders = 0;
let fetchFails = false;
let server = {
  generatedAt: '2026-10-15T11:55:00+09:00',
  summary: {},
  candidates: [{ code: '000001', status: 'watching', lastPrice: 100 }, { code: '000002', status: 'watching', lastPrice: 200 }],
  manualExclusions: [],
  paperTrading: { positions: [{ id: 'paper', status: 'open' }] },
};
let positions = { positions: [{ id: 'holding', status: 'open' }] };
let ah = {candidates: []};
const state = { pendingExclusions: new Map([['000001', { action: 'exclude', at: now }]]),
  payload: null, positions: null, selectedCode: '000002', exclusionMessage: '' };
const elements = { refreshButton: { disabled: false }, runStatus: {}, updatedAt: {}, pushState: {}, candidateList: {} };
const context = vm.createContext({ state, elements, Date: class extends Date { static now() { return now; } },
  inlineChartMode: false, historyChartMode: false, restoreOperationPending: false,
  PENDING_EXCLUSION_KEY: 'pending', sessionStorage: { setItem() {} }, exclusionPollTimer: undefined,
  setTimeout: (callback, delay) => { timers.set(++timerId, { callback, delay }); return timerId; },
  clearTimeout: id => timers.delete(id),
  fetch: async url => {
    if (fetchFails) throw new Error('Network unavailable');
    return { ok: true, json: async () => structuredClone(url.startsWith('data/positions') ? positions : url.startsWith('data/ah-monitor') ? ah : server) };
  },
  document: { querySelector: () => ({ classList: { contains: () => true } }) },
  formatDateTime: value => value,
  currentCandidates: () => (state.view === 'ah' ? state.ahPayload : state.payload)?.candidates || [],
  render: () => { renders++; }, renderManualExclusions: () => { exclusionRenders++; },
});
const section = (start, end) => source.slice(source.indexOf(start), source.indexOf(end));
vm.runInContext(section('async function loadData(', 'function render()')
  + section('function applyPendingExclusions()', 'function renderManualExclusions()'), context);

async function poll() {
  assert.equal(timers.size, 1);
  const [id, scheduled] = timers.entries().next().value;
  timers.delete(id);
  assert.equal(scheduled.delay, 10000);
  await scheduled.callback();
}

(async () => {
  await context.loadData();
  assert.equal(renders, 1);
  assert.equal(state.payload.candidates.length, 1);
  for (let i = 0; i < 5; i++) { now += 10000; await poll(); }
  assert.equal(renders, 1, 'Repeated identical polls must not rebuild charts');
  assert.equal(exclusionRenders, 1, 'Unchanged exclusions must not rebuild their chart list');
  assert.equal(state.positions.positions.length, 2, 'Paper positions must not accumulate on polls');
  assert.match(elements.runStatus.textContent, /서버 반영 확인 중/);
  assert.equal(elements.refreshButton.disabled, false);

  fetchFails = true;
  await poll();
  assert.equal(state.loadFailed, true);
  fetchFails = false;
  await poll();
  assert.equal(renders, 2, 'Identical data must restore charts after a network failure');
  assert.equal(state.loadFailed, false);

  server.candidates[1].lastPrice = 210;
  await poll();
  assert.equal(renders, 3, 'A changed quote must update the display');
  positions.positions.push({ id: 'new-holding', status: 'open' });
  await poll();
  assert.equal(renders, 4, 'Changed holdings must update the display');
  await context.loadData();
  assert.equal(renders, 5, 'Manual refresh must still redraw');

  server.manualExclusions = [{ code: '000001' }];
  server.candidates.shift();
  await poll();
  assert.equal(state.pendingExclusions.size, 0, 'Confirmed requests stop polling');
  assert.equal(renders, 6, 'Server confirmation must update exclusions');
  assert.equal(timers.size, 0, 'No new timer after confirmation');

  timers.clear();
  state.pendingExclusions.set('000002', { action: 'exclude', at: now });
  await context.loadData();
  assert.equal(state.payload.candidates.length, 0);
  now += 600001;
  await poll();
  assert.equal(state.pendingExclusions.size, 0, 'Timed-out requests must stop polling');
  assert.equal(state.payload.candidates.length, 1, 'Timed-out requests restore the server view');
  assert.equal(timers.size, 0);
  assert.match(state.exclusionMessage, /지연/);
  const previousRenders = renders;
  ah.candidates.push({code: '000003', candidateTier: 'ah'});
  await context.loadData({pollOnly: true});
  assert.equal(renders, previousRenders + 1, 'Changed AH data must update the display');
  await context.loadData({pollOnly: true});
  assert.equal(renders, previousRenders + 1, 'Identical AH data must keep chart frames');
  console.log('Monitor polling tests passed: unchanged polls, quotes, holdings, manual refresh, confirmation and timeout.');
})().catch(error => { console.error(error); process.exitCode = 1; });
