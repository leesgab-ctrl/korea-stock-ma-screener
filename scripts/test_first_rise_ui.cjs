const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require('playwright');

const root = path.resolve(__dirname, '..');
const first = JSON.parse(fs.readFileSync(path.join(root, 'data/first-rise-monitor.json'), 'utf8'));
const legacy = JSON.parse(fs.readFileSync(path.join(root, 'data/candidate-monitor.json'), 'utf8'));
const server = http.createServer((request, response) => {
  const file = path.resolve(root, '.' + decodeURIComponent(new URL(request.url, 'http://localhost').pathname));
  if (!file.startsWith(root + path.sep)) return response.writeHead(403).end();
  fs.readFile(file, (error, content) => {
    if (error) return response.writeHead(404).end();
    response.setHeader('Content-Type', {'.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.html': 'text/html'}[path.extname(file)] || 'application/octet-stream');
    response.end(content);
  });
});

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = process.env.REVIEW_URL || `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  try {
    for (const width of [1280, 390, 320]) {
      const page = await browser.newPage({viewport: {width, height: 1000}});
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(base + '/monitor.html?view=firstRise');
      await page.waitForFunction(n => document.querySelectorAll('#candidateList > .candidate').length === n, first.candidates.length);
      assert.equal(await page.locator('[data-view=firstRise]').getAttribute('aria-selected'), 'true');
      assert.match(await page.locator('#candidateMeta').textContent(), /10거래일/);
      assert.match(await page.locator('#candidateList .candidate-tier').first().textContent(), /첫날상승.*첫날 종가/);
      assert.match(await page.locator('#candidateMeta').textContent(), /종가 \+5%.*장중 신규 선정 없음/);
      assert.equal(await page.evaluate(() => sharedMonitorCandidates().filter(item => item.candidateTier === 'first_rise').every(confirmedFirstRise)), true);
      const frame = page.frameLocator('.inline-stock-chart').first();
      await frame.locator('#maChart').waitFor({state: 'visible'});
      const pixels = await frame.locator('#maChart').evaluate(canvas => {
        const rgba = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
        let colored = 0;
        for (let i = 0; i < rgba.length; i += 4) if (rgba[i+3] && Math.max(...rgba.slice(i, i+3)) - Math.min(...rgba.slice(i, i+3)) > 40) colored++;
        return colored;
      });
      assert.ok(pixels > 200);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await page.screenshot({path: path.join(root, `outputs/first-rise-${width}.png`)});
      await page.reload();
      await page.waitForFunction(n => document.querySelectorAll('#candidateList > .candidate').length === n, first.candidates.length);
      assert.match(page.url(), /view=firstRise/);
      await page.locator('[data-view=operations]').click();
      await page.locator('#criteriaButton').click();
      assert.equal(await page.locator('#firstRiseCriteria li').count(), 8);
      assert.match(await page.locator('#firstRiseCriteria').textContent(), /4%.*미래 마감 거래량/s);
      await page.locator('#firstRiseCriteria').scrollIntoViewIfNeeded();
      await page.screenshot({path: path.join(root, `outputs/first-rise-criteria-${width}.png`)});
      await page.locator('#criteriaDialogClose').click();
      await page.locator('#candidateScanButton').click();
      assert.match(await page.locator('[data-scan=counts]').textContent(), /첫날상승/);
      assert.match(await page.locator('[data-scan=submit]').textContent(), /첫날상승/);
      await page.locator('[data-view=imageSearch]').click();
      await page.evaluate(() => {
        window.RisingPatterns = {confirmed: [{id: 'qa-image'}], open() {}, setRuleLinks() {}};
        const item = state.firstRisePayload.candidates[0];
        state.payload.imageSearch = {status: 'ok', matches: [{code: item.code, sourceImageIds: ['qa-image'],
          id: 'qa-first-rise', completedAt: new Date(Date.now()-60000).toISOString(), ruleName: 'QA',
          signalType: 'ma3Ma120', stage: 'primary', price: 100, changePct: 1}]};
        renderImageSearch();
      });
      const code = first.candidates[0].code;
      const row = page.locator(`#imageSearchMatches .watch-quote-row[data-code="${code}"]`);
      assert.equal(await row.count(), 1);
      assert.match(await row.locator('.watch-quote-group').first().textContent(), /첫날상승/);
      await page.locator('[data-view=ag]').click();
      await page.waitForFunction(n => document.querySelectorAll('#candidateList > .candidate').length === n,
        legacy.candidates.filter(x => !x.candidateTier || x.candidateTier === 'core').length);
      assert.equal(await page.locator('#candidateList > .candidate').count(), legacy.candidates.filter(x => !x.candidateTier || x.candidateTier === 'core').length);
      assert.deepEqual(errors, []);
      console.log(JSON.stringify({width, candidates: first.candidates.length, coloredPixels: pixels, errors}));
      await page.close();
    }
    const page = await browser.newPage();
    await page.route('**/data/first-rise-monitor.json?*', route => route.fulfill({status: 503, body: ''}));
    await page.goto(base + '/monitor.html?view=ag');
    await page.locator('#candidateList > .candidate').first().waitFor();
    await page.locator('[data-view=firstRise]').click();
    assert.match(await page.locator('#runStatus').textContent(), /첫날상승 데이터를 불러오지 못했습니다/);
    console.log('First-rise data failures do not break AG.');
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); server.close(); process.exitCode = 1; });
