const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const payload = JSON.parse(fs.readFileSync(path.join(root, 'data/candidate-monitor.json'), 'utf8'));
const item = payload.candidates.find(item => item.code === '059100');
assert.ok(item);
const bars = item.displayCharts.intraday.series.filter(row => row.complete !== false && Number.isFinite(row.m3));
const row = bars.at(-5), other = bars.at(-3);
assert.ok(row && other);
const receipt = {id: 'actual-test-receipt', code: item.code, name: item.name, barTime: row.t,
  sentAt: new Date(Date.parse(row.t) + 35 * 60000).toISOString(), label: 'MA3가 MA120 돌파', stage: 'primary', kind: 'image'};
const second = {...receipt, id:'second-condition', label:'MA10이 MA40 돌파', stage:'confirmation'};
payload.notificationHistory = [receipt, second, {...receipt, id:'another-bar', barTime: other.t,
  sentAt:new Date(Date.parse(other.t) + 35 * 60000).toISOString()}];
payload.history = [item];
payload.imageSearch = {events:[{code:item.code, time:bars.at(-1).t, stage:'primary', ruleName:'Detected but never delivered'}]};
const sandbox = {window:{}, document:{addEventListener(){}}};
vm.runInNewContext(fs.readFileSync(path.join(root, 'notification-markers.js'), 'utf8'), sandbox);
const collected = sandbox.window.NotificationMarkers.forCode(payload, {notificationHistory:[receipt]}, item.code);
assert.equal(collected.length, 3, 'Duplicate source receipts are merged; detected events do not become deliveries');
assert.equal(sandbox.window.NotificationMarkers.forCode(payload, null, '999999').length, 0);
assert.equal(sandbox.window.NotificationMarkers.forCode({notificationHistory:[{...receipt, sentAt:'invalid'}]}, null, item.code).length, 0);

const server = http.createServer((req, res) => {
  const pathname = new URL(req.url, 'http://localhost').pathname;
  const file = path.resolve(root, `.${pathname === '/' ? '/monitor.html' : pathname}`);
  if (!file.startsWith(root + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404); res.end(); return; }
  res.setHeader('Content-Type', ({'.html':'text/html','.js':'application/javascript','.css':'text/css','.json':'application/json'})[path.extname(file)] || 'application/octet-stream');
  if (pathname === '/data/candidate-monitor.json') res.end(JSON.stringify(payload));
  else if (pathname === '/data/positions.json') res.end(JSON.stringify({positions:[], notificationHistory:[receipt]}));
  else fs.createReadStream(file).pipe(res);
});
server.listen(0, '127.0.0.1', () => {
  console.log(JSON.stringify({url:`http://127.0.0.1:${server.address().port}/monitor.html?historyChart=1&stock=${item.code}`,
    barTime:row.t, secondBarTime:other.t, expectedMarkers:2}));
  if (!process.argv.includes('--serve')) server.close();
});
