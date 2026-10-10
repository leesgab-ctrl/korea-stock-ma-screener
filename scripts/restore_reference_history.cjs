const fs = require('node:fs');
const path = require('node:path');
const cp = require('node:child_process');
const root = path.resolve(__dirname, '..');
const git = process.env.GIT_EXECUTABLE || 'git';
const run = args => cp.execFileSync(git, args, {cwd: root, maxBuffer: 100 * 1024 * 1024, encoding: 'utf8'});
const payload = JSON.parse(fs.readFileSync(path.join(root, 'data/candidate-monitor.json')));
const items = [...(payload.history || []), ...(payload.candidates || [])];
const idFor = row => row.id || row.code + '|' + row.registeredAt;
const histories = new Map(items.map(row => [idFor(row), new Map()]));
const add = snapshot => {
  for (const item of [...(snapshot.history || []), ...(snapshot.candidates || [])]) {
    const rows = histories.get(idFor(item));
    if (!rows) continue;
    const chart = item.displayCharts?.intraday;
    for (const row of [...(item.registrationDayBars || []), ...(chart?.history || []), ...(chart?.series || [])]) {
      if (row.complete === false || !row.t) continue;
      rows.set(row.t, row);
    }
  }
};
const commits = run(['log', '--format=%H', '--reverse', 'HEAD', '--', 'data/candidate-monitor.json']).trim().split(/\s+/).filter(Boolean);
for (const sha of commits) {
  add(JSON.parse(run(['show', `${sha}:data/candidate-monitor.json`])));
}
add(payload);
const records = [];
for (const item of items) {
  const id = idFor(item);
  const rows = [...histories.get(id).values()].filter(row => Date.parse(row.t) >= Date.parse(item.registeredAt))
    .sort((a, b) => a.t.localeCompare(b.t));
  const index = rows.findIndex((row, i) => {
    const recent = rows.slice(Math.max(0, i - 3), i + 1);
    return recent.length === 4 && recent.every(bar => Number.isFinite(bar.m20) && Number.isFinite(bar.m40) && bar.m20 > bar.m40)
      && recent.slice(1).every((bar, j) => bar.m40 > recent[j].m40) && row.c > 0;
  });
  if (index < 0) continue;
  const first = rows[index];
  records.push({id, code: item.code, name: item.name, registeredAt: first.t, registrationPrice: first.c,
    originalRegisteredAt: item.registeredAt, source: 'historical_saved_bars',
    availableFrom: rows[0].t, firstEntryVerified: rows[0].t.slice(0, 10) === item.registeredAt.slice(0, 10)
      && Number.isFinite(rows[0].m20) && Number.isFinite(rows[0].m40), recoveredSnapshots: commits.length});
}
fs.writeFileSync(path.join(root, 'data/reference-history.json'), JSON.stringify({generatedAt: new Date().toISOString(),
  records: records.sort((a, b) => b.registeredAt.localeCompare(a.registeredAt))}, null, 2) + '\n');
console.log(`Recovered ${records.length} entries from ${commits.length} historical snapshots`);
for (const row of records) {
  const item = items.find(item => idFor(item) === row.id);
  if (item.archiveReason === 'window_completed') console.log(row.name, row.availableFrom, row.registeredAt, row.registrationPrice);
}
