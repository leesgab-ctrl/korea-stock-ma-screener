const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const history = Array.from({length: 61}, (_, i) => ({
  id: `qa-${i}`, code: i ? String(i).padStart(6, '0') : '083500',
  name: i ? `QA${String(i).padStart(2, '0')}` : '에프엔에스테크', candidateTier: 'expanded_ak',
  registeredAt: new Date(Date.UTC(2026, 9, 1, 0, i)).toISOString(), registrationPrice: 100,
  archivedAt: '2026-10-16', archiveReason: 'window_completed',
  displayCharts: {daily: {history: [{d: '2026-10-02', c: 101}, {d: '2026-10-16', c: 102}]}}
}));
const analysisHistory = [
  {...history[0]},
  {...history[0], id: 'qa-ah', code: '317850', name: '대모', candidateTier: 'ah', archiveReason: undefined,
    registeredAt: '2026-10-24T00:15:57+09:00', archivedAt: '2026-10-30T09:02:11+09:00'},
  {...history[0], id: 'qa-first-rise', code: '999001', name: '첫날상승종료', candidateTier: 'first_rise', archiveReason: undefined},
  {...history[0], id: 'qa-active', code: '999002', name: '관리중종목', archiveReason: undefined},
  {code: '999003', name: '종료미확인', registeredAt: '2026-10-23'},
];
const server = http.createServer((request, response) => {
  const file = path.resolve(root, '.' + new URL(request.url, 'http://localhost').pathname);
  if (!file.startsWith(root + path.sep)) return response.writeHead(403).end();
  fs.readFile(file, (error, content) => {
    if (error) return response.writeHead(404).end();
    response.setHeader('Content-Type', {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css'}[path.extname(file)] || 'application/octet-stream');
    response.end(content);
  });
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  try {
    for (const width of [1280, 390, 320]) {
      const page = await browser.newPage({viewport: {width, height: 900}});
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/data/candidate-monitor.json?*', route => route.fulfill({json: {
        generatedAt: '2026-10-30T14:00+09:00', candidates: [{code: '999002'}], history, analysisHistory}}));
      await page.route('**/data/reference-history.json?*', route => route.fulfill({json: {records: history.map(item => ({
        id: item.id, code: item.code, registeredAt: item.registeredAt, registrationPrice: 100, firstEntryVerified: false}))}}));
      await page.goto(`http://127.0.0.1:${server.address().port}/history.html`);
      await page.waitForFunction(() => document.querySelectorAll('#historyList tbody tr').length === 50);
      const input = page.getByRole('searchbox', {name: '종목검색'});
      await input.fill('에프엔');
      assert.equal(await page.locator('#historyList tbody tr').count(), 1);
      assert.match(await page.locator('#historyList').textContent(), /에프엔에스테크/);
      await input.fill('083500');
      assert.equal(await page.locator('#historyList tbody tr').count(), 1);
      await input.fill('없는종목');
      assert.match(await page.locator('#historyList').textContent(), /검색 결과가 없습니다/);
      await input.fill('');
      assert.equal(await page.locator('#historyList tbody tr').count(), 50);
      await page.getByRole('button', {name: '다음 페이지', exact: true}).click();
      assert.equal(await page.locator('#historyList tbody tr').count(), 13);
      await input.fill('qa60');
      assert.equal(await page.locator('#historyList tbody tr').count(), 1);
      await input.fill('대모');
      assert.equal(await page.locator('#historyList tbody tr').count(), 1);
      assert.match(await page.locator('#historyList').textContent(), /317850.*A-H/);
      await input.fill('317850');
      assert.equal(await page.locator('#historyList tbody tr').count(), 1);
      await page.getByRole('button', {name: '대모', exact: true}).click();
      assert.match(await page.locator('#historyList iframe').getAttribute('src'), /historyChart=1&stock=317850/);
      await input.fill('첫날상승종료');
      assert.equal(await page.locator('#historyList tbody tr').count(), 1);
      await input.fill('관리중종목');
      assert.match(await page.locator('#historyList').textContent(), /검색 결과가 없습니다/);
      await input.fill('종료미확인');
      assert.match(await page.locator('#historyList').textContent(), /검색 결과가 없습니다/);
      await input.fill('에프엔');
      const box = await input.boundingBox();
      assert.ok(box.x >= 0 && box.x + box.width <= width + 1);
      await page.screenshot({path: path.join(root, `outputs/history-search-${width}.png`)});
      await page.goto(`http://127.0.0.1:${server.address().port}/history.html?type=reference`);
      await page.waitForFunction(() => document.querySelectorAll('#historyList tbody tr').length === 50);
      await page.getByRole('searchbox', {name: '종목검색'}).fill('083500');
      assert.equal(await page.locator('#historyList tbody tr').count(), 1);
      assert.deepEqual(errors, []);
      console.log(JSON.stringify({width, nameAndCodeSearch: true, ahArchiveSearch: true, firstRiseArchiveSearch: true, paginationReset: true, referenceSearch: true, errors}));
      await page.close();
    }
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => {console.error(error); server.close(); process.exitCode = 1;});
