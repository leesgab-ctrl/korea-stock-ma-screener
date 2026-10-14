const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'monitor.js'), 'utf8');
const context = vm.createContext({});
vm.runInContext(source.slice(source.indexOf('function chartPhases('), source.indexOf('function drawChart('))
  + source.slice(source.indexOf('function candidateColorNotice('), source.indexOf('function compareCandidateSpread(')), context);
const values = [[103,104,102],[96,98,97],[99,98,99],[101,99,100],[110,101,99],[111,102,101],[112,103,99],[113,104,102]];
const rows = values.map(([m3,m20,m10],index)=>({t:`2026-10-15T${String(9+Math.floor(index/2)).padStart(2,'0')}:${index%2?'30':'00'}:00+09:00`,
  m3,m20,m10,m40:100,m60:110,complete:true}));
const original = JSON.stringify(rows);
const result = context.chartPhases(rows);
assert.deepEqual(Array.from(result,r=>r.phase),['before','pullback','fast','fast','fast','fast','confirmed','confirmed']);
assert.equal(result[4].recoveryReached60,false);
assert.equal(result[5].recoveryReached60,true);
assert.deepEqual(Array.from(result, (r,i)=>r.recoveryMa10Breakout?i:null).filter(i=>i!==null),[5]);
for(const row of result.slice(2,6))assert.equal(context.phaseDisplay(row).color,'#badb68');
assert.equal(context.phaseDisplay({...result[3],referencePullback:true,ma60Observation:true}).color,'#badb68','Recovery interval overrides other shading');
assert.equal(context.phaseDisplay(result[6]).phase,'confirmed');
assert.equal(JSON.stringify(rows),original);
const forming=rows.slice(0,6).map(r=>({...r}));forming.at(-1).complete=false;
assert.equal(context.chartPhases(forming).at(-1).recoveryReached60,false);
assert.equal(context.chartPhases(forming).at(-1).recoveryMa10Breakout,false);
const notice = context.candidateColorNotice({displayCharts:{intraday:{series:rows.slice(0,4)}}});
assert.equal(notice.color,'#badb68');assert.match(notice.message,/MA3가 MA20을 상향 돌파/);
assert.equal(context.recoveryStages(rows.slice(2)).some(r=>r.recoveryMa10Breakout),false);
console.log('Inclusive dark-lime recovery interval, independent MA10 alert, priority, and completed-bar tests passed.');
