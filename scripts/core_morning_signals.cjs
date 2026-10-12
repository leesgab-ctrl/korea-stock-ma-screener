const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'monitor.js'), 'utf8');
const context = vm.createContext({});
vm.runInContext(source.slice(source.indexOf('function chartPhases('), source.indexOf('function drawChart('))
  + source.slice(source.indexOf('function watchQuoteDates('), source.indexOf('function isTodayRising(')), context);

function coreOpeningChecks(payload, today) {
  const candidates = payload.candidates || [];
  const priorDates = [...new Set(candidates.flatMap(item => (item.displayCharts?.intraday?.series || [])
    .filter(row => row.complete !== false && row.t?.slice(0, 10) < today).map(row => row.t.slice(0, 10))))].sort().slice(-2);
  const previousDate = priorDates.at(-1);
  if (!previousDate) return [];
  return candidates.filter(item => item.registeredAt?.slice(0, 10) <= previousDate
    && context.isCoreCandidate(item, priorDates)).map(item => {
    const rows = (item.displayCharts?.intraday?.series || []).filter(row => row.complete !== false)
      .slice().sort((a, b) => a.t.localeCompare(b.t));
    const previous = rows.filter(row => row.t.slice(0, 10) === previousDate).at(-1);
    // Native candle timestamps mark the start; these close at 09:30 and 10:00.
    const first = rows.find(row => row.complete === true && row.t.slice(0, 10) === today && row.t.slice(11, 16) === '09:00');
    const second = rows.find(row => row.complete === true && row.t.slice(0, 10) === today && row.t.slice(11, 16) === '09:30');
    const ready = [previous?.m3, first?.m3, second?.m3].every(value => Number.isFinite(value) && value > 0);
    const epsilon = ready ? Math.max(previous.m3, first.m3, second.m3) * 1e-12 : 0;
    return {code: item.code, previousDate, ready,
      matched: ready && first.m3 > previous.m3 + epsilon && second.m3 > first.m3 + epsilon,
      previousMa3: previous?.m3 ?? null, firstMa3: first?.m3 ?? null, secondMa3: second?.m3 ?? null,
      row: second || null};
  });
}

module.exports = {coreOpeningChecks};
if (require.main === module) {
  const today = process.argv[2];
  if (!/^\d{4}-\d{2}-\d{2}$/.test(today || '')) throw new Error('A Korea trading date is required');
  process.stdout.write(JSON.stringify(coreOpeningChecks(JSON.parse(fs.readFileSync(0, 'utf8')), today)));
}
