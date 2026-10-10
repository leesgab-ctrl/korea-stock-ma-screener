const fs = require('fs');
const vm = require('vm');
const path = require('path');
const crypto = require('crypto');
const reclassify = process.argv.includes('--reclassify');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'monitor.js'), 'utf8');
const rules = source.slice(source.indexOf('function chartPhases('), source.indexOf('function drawChart('))
  + source.slice(source.indexOf('function chartGroup('), source.indexOf('const formatter ='));
const context = {};
vm.createContext(context);
vm.runInContext(rules, context);
const version = crypto.createHash('sha256').update(rules.replace(/\r\n/g, '\n')).digest('hex').slice(0, 12);
const payload = JSON.parse(fs.readFileSync(path.join(root, 'data/candidate-monitor.json')));
const marketPath = path.join(root, 'data/stock-data.json');
const market = fs.existsSync(marketPath) ? JSON.parse(fs.readFileSync(marketPath)) : { dates: [] };
const output = path.join(root, 'data/price-analysis.json');
const saved = fs.existsSync(output) ? JSON.parse(fs.readFileSync(output)) : { records: [] };
const records = new Map(saved.records.map(row => [row.id + '|' + row.date, row]));
if (reclassify) {
  for (const row of records.values()) {
    row.color = null;
    row.compact = null;
    row.ruleVersion = version;
    row.source = 'reclassification_missing';
  }
}
const now = new Date();
const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul' }).format(now);
const items = [...(payload.history || []), ...(payload.candidates || [])];
const merge = (chart, key) => [...new Map([...(chart?.history || []), ...(chart?.series || [])].map(row => [row[key], row])).values()]
  .filter(row => row.complete !== false).sort((a, b) => a[key].localeCompare(b[key]));
const dates = [...new Set([...(market.dates || []), ...items.flatMap(item => merge(item.displayCharts?.daily, 'd').map(row => row.d))])]
  .filter(date => now >= new Date(date + 'T20:00:00+09:00')).sort();
for (const item of items) {
  if (!item.registeredAt) continue;
  const registered = item.registeredAt.slice(0, 10);
  const id = item.id || item.code + '|' + item.registeredAt;
  const series = merge(item.displayCharts?.intraday, 't');
  const daily = merge(item.displayCharts?.daily, 'd');
  const window = dates.filter(date => date > registered).slice(0, 10);
  const end = item.verificationEndDate || window.at(-1);
  const excluded = item.archiveReason === 'manual_excluded' ? item.archivedAt?.slice(0, 10) : null;
  for (const date of dates.filter(date => date >= registered && date <= end && (!excluded || date <= excluded))) {
    const key = id + '|' + date;
    const volume = daily.find(row => row.d === date)?.v;
    if (Number.isFinite(volume) && volume >= 0 && records.has(key)) records.get(key).volume = volume;
    const prefix = series.filter(row => row.t.slice(0, 10) <= date);
    const last = prefix.at(-1);
    const spread = last?.t.slice(0, 10) === date ? context.maximumMaSpread(last) : null;
    const compact = spread === null ? null : spread <= 0.8;
    if (records.has(key)) records.get(key).compact = compact;
    if (records.get(key)?.color) continue;
    const close = daily.find(row => row.d === date && row.c > 0);
    const priorDate = dates[dates.indexOf(date) - 1];
    const prior = daily.find(row => row.d === priorDate && row.c > 0);
    const valid = last?.t.slice(0, 10) === date && Date.parse(last.t) >= Date.parse(item.registeredAt)
      && [last.m3, last.m10, last.m20, last.m40, last.m60].every(value => value > 0) && close && prior;
    if (!valid && records.has(key)) continue;
    // Truncate before evaluating: later purple confirmations must not recolor earlier snapshots.
    const color = valid ? context.phaseBackground(context.chartPhases(prefix).at(-1)) : null;
    const type = valid ? ({reference: '상승', target: '조정'}[context.chartGroup({registeredAt: item.registeredAt,
      displayCharts: {intraday: {dataStatus: 'ok', series: prefix}}})] || '대기') : '대기';
    const existing = records.get(key);
    records.set(key, { id, code: item.code, name: item.name, registeredAt: item.registeredAt, date,
      type,
      color, compact, volume: Number.isFinite(volume) && volume >= 0 ? volume : null,
      close: reclassify && existing ? existing.close : close?.c ?? null,
      change: reclassify && existing ? existing.change : close && prior ? (close.c / prior.c - 1) * 100 : null,
      ruleVersion: version, source: date === today ? 'close' : 'recalculated', capturedAt: now.toISOString() });
  }
}
const result = { dates, records: [...records.values()].sort((a, b) => a.date.localeCompare(b.date) || a.id.localeCompare(b.id)) };
fs.writeFileSync(output, JSON.stringify(result, null, 2) + '\n');
console.log(`Saved ${result.records.length} daily monitoring records`);
const referencePath = path.join(root, 'data/reference-history.json');
const priorReferences = fs.existsSync(referencePath) ? JSON.parse(fs.readFileSync(referencePath)).records || [] : [];
const references = new Map(priorReferences.map(row => [row.id, row]));
for (const item of items) {
  if (!item.registeredAt) continue;
  const id = item.id || item.code + '|' + item.registeredAt;
  const rows = merge(item.displayCharts?.intraday, 't').filter(row => Date.parse(row.t) >= Date.parse(item.registeredAt));
  const index = rows.findIndex((row, i) => {
    const recent = rows.slice(Math.max(0, i - 3), i + 1);
    return recent.length === 4 && recent.every(bar => Number.isFinite(bar.m20) && Number.isFinite(bar.m40) && bar.m20 > bar.m40)
      && recent.slice(1).every((bar, j) => bar.m40 > recent[j].m40) && row.c > 0;
  });
  if (index < 0) continue;
  const first = rows[index];
  if (!references.has(id) || first.t < references.get(id).registeredAt) {
    references.set(id, {id, code: item.code, name: item.name, registeredAt: first.t,
      registrationPrice: first.c, originalRegisteredAt: item.registeredAt, source: 'reconstructed_completed_bars'});
  }
}
fs.writeFileSync(referencePath, JSON.stringify({generatedAt: now.toISOString(), ruleVersion: version,
  records: [...references.values()].sort((a, b) => b.registeredAt.localeCompare(a.registeredAt))}, null, 2) + '\n');
console.log(`Saved ${references.size} reference-type entry records`);
