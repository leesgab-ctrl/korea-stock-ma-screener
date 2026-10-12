const assert = require('node:assert/strict');
const {coreOpeningChecks} = require('./core_morning_signals.cjs');
const today = '2026-10-12';
const row = (t, m3, extra = {}) => ({t, m3, m10:100.6, m20:100.7, m40:100.2, m60:100, complete:true, ...extra});
const base = [
  row('2026-10-07T15:00:00',100.5,{m40:100}),
  row('2026-10-08T19:00:00',100.5,{m40:100.1}),
  row('2026-10-08T19:30:00',100.5),
];
const candidate = (first=101, second=102, extra=[]) => ({code:'145720',name:'Dentium',registeredAt:'2026-10-07T16:40:00+09:00',
  displayCharts:{intraday:{series:[...base,row(`${today}T09:00:00`,first),row(`${today}T09:30:00`,second),...extra]}}});
const check = item => coreOpeningChecks({candidates:[item]},today)[0];
assert.equal(check(candidate()).matched,true);
assert.equal(check(candidate()).previousMa3,100.5);
assert.equal(check(candidate()).row.t,`${today}T09:30:00`);
assert.equal(check(candidate(100.5,102)).matched,false,'A flat first comparison does not count');
assert.equal(check(candidate(100,102)).matched,false,'A falling first comparison does not count');
assert.equal(check(candidate(102,101)).matched,false,'A falling second comparison does not count');
assert.equal(check(candidate(101,101)).matched,false,'A flat second comparison does not count');
assert.equal(check(candidate(101,null)).ready,false);
const unconfirmed=candidate();delete unconfirmed.displayCharts.intraday.series.at(-1).complete;
assert.equal(check(unconfirmed).ready,false,'Opening bars require explicit completion confirmation');
for (const stamp of ['09:00','09:30']) {
  const item=candidate();
  item.displayCharts.intraday.series.find(r=>r.t.slice(11,16)===stamp).complete=false;
  assert.equal(check(item).ready,false,'Forming bars cannot count');
}
for (const stamp of ['09:00','09:30']) {
  const item=candidate();
  item.displayCharts.intraday.series=item.displayCharts.intraday.series.filter(r=>r.t.slice(11,16)!==stamp);
  assert.equal(check(item).ready,false,'Missing opening bars cannot be replaced by later bars');
}
assert.equal(check(candidate(100,100,[row(`${today}T10:00:00`,103),row(`${today}T10:30:00`,106)])).matched,false,
  'A later two-rise sequence cannot rescue a failed opening');
assert.equal(check(candidate(101,102,[row(`${today}T10:00:00`,50)])).matched,true,
  'Later price movement cannot change the fixed opening result');
const wide = m3 => ({m3,m10:130,m20:120,m40:110,m60:100});
const todayOnly = candidate();
todayOnly.displayCharts.intraday.series=base.map(r=>({...r,...wide(100)})).concat([
  row(`${today}T09:00:00`,100.5),row(`${today}T09:30:00`,100.8,{m40:100.3}),
]);
assert.equal(coreOpeningChecks({candidates:[todayOnly]},today).length,0,'Today-only core colors are not yesterday core stocks');
const gray = candidate();
gray.displayCharts.intraday.series=base.map(r=>({...r,...wide(100)}));
gray.displayCharts.intraday.series[0]={...gray.displayCharts.intraday.series[0],m3:100,m10:100,m20:100,m40:100,m60:100};
gray.displayCharts.intraday.series.push(row(`${today}T09:00:00`,101),row(`${today}T09:30:00`,102));
assert.equal(check(gray).matched,true,'Use the actual previous core menu: its prior-session gray X remains eligible');
const newItem=candidate();newItem.registeredAt=`${today}T09:00:00+09:00`;
assert.equal(coreOpeningChecks({candidates:[newItem]},today).length,0,'Newly registered today stocks are excluded');
assert.equal(coreOpeningChecks({candidates:[]},today).length,0);
console.log('Core-only fixed 09:30/10:00 MA3 opening checks passed.');
