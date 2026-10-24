const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require('playwright');

const root = path.resolve(__dirname, '..');
const ah = JSON.parse(fs.readFileSync(path.join(root, 'data/ah-monitor.json'), 'utf8'));
const legacy = JSON.parse(fs.readFileSync(path.join(root, 'data/candidate-monitor.json'), 'utf8'));
const server = http.createServer((request, response) => {
  const file = path.resolve(root, '.' + decodeURIComponent(new URL(request.url, 'http://localhost').pathname));
  if (!file.startsWith(root + path.sep)) { response.writeHead(403).end(); return; }
  fs.readFile(file, (error, content) => {
    if (error) { response.writeHead(404).end(); return; }
    const type = {'.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.html': 'text/html'}[path.extname(file)];
    response.setHeader('Content-Type', type || 'application/octet-stream');
    response.end(content);
  });
});

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  try {
    for (const width of [1280, 390]) {
      const page = await browser.newPage({viewport: {width, height: 1000}});
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(base + '/monitor.html?view=ah');
      await page.waitForFunction(count => document.querySelectorAll('#candidateList > .candidate').length === count, ah.candidates.length);
      assert.equal(await page.locator('[data-view=ah]').getAttribute('aria-selected'), 'true');
      assert.equal(await page.locator('#candidateList .candidate-tier').first().textContent().then(text => text.startsWith('A-H')), true);
      assert.match(await page.locator('#candidateMeta').textContent(), /5거래일/);
      await page.reload();
      await page.waitForFunction(count => document.querySelectorAll('#candidateList > .candidate').length === count, ah.candidates.length);
      assert.match(page.url(), /view=ah/);
      const frame = page.frameLocator('.inline-stock-chart').first();
      await frame.locator('#maChart').waitFor({state: 'visible'});
      const pixels = await frame.locator('#maChart').evaluate(canvas => {
        const rgba = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
        let colored = 0, ma180 = 0;
        for (let i = 0; i < rgba.length; i += 4) {
          if (rgba[i+3] > 0 && Math.max(rgba[i], rgba[i+1], rgba[i+2]) - Math.min(rgba[i], rgba[i+1], rgba[i+2]) > 40) colored++;
          if (Math.abs(rgba[i]-181) < 3 && Math.abs(rgba[i+1]-43) < 3 && Math.abs(rgba[i+2]-116) < 3) ma180++;
        }
        return {colored, ma180};
      });
      assert.ok(pixels.colored > 200, JSON.stringify(pixels));
      assert.ok(pixels.ma180 > 20, JSON.stringify(pixels));
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await page.screenshot({path: path.join(root, `outputs/ah-menu-${width}.png`)});
      await page.locator('[data-view=ag]').click();
      const agCount = legacy.candidates.filter(item => !item.candidateTier || item.candidateTier === 'core').length;
      assert.equal(await page.locator('#candidateList > .candidate').count(), agCount);
      await page.locator('[data-view=operations]').click();
      await page.locator('#criteriaButton').click();
      assert.equal(await page.locator('#ahCriteria tr').count(), 6);
      await page.locator('#ahCriteria').scrollIntoViewIfNeeded();
      await page.screenshot({path: path.join(root, `outputs/ah-criteria-${width}.png`)});
      await page.locator('#criteriaDialogClose').click();
      await page.locator('[data-view=imageSearch]').click();
      const risingAh = ah.candidates.find(item => item.displayCharts?.quote?.change > 0
        && !legacy.candidates.some(existing => existing.code === item.code));
      if (risingAh) {
        const row = page.locator(`#imageSearchRising .watch-quote-row[data-code="${risingAh.code}"]`);
        assert.equal(await row.count(), 1);
        assert.match(await row.locator('.watch-quote-group').first().textContent(), /AH/);
      }
      const overlap = ah.candidates.find(item => legacy.candidates.some(existing => existing.code === item.code));
      const onlyAh = ah.candidates.find(item => !legacy.candidates.some(existing => existing.code === item.code));
      if (overlap && onlyAh) {
        await page.evaluate(codes => {
          window.RisingPatterns = {confirmed: [{id: 'qa-image'}], open() {}, setRuleLinks() {}};
          state.payload.imageSearch = {status: 'ok', matches: codes.map(code => ({code, sourceImageIds: ['qa-image'],
            completedAt: new Date(Date.now()-60000).toISOString(), ruleName: 'QA condition', price: 100,
            changePct: 1, signalType: 'ma3Ma120', stage: 'primary'}))};
          renderImageSearch();
        }, [onlyAh.code, overlap.code]);
        assert.equal(await page.locator(`#imageSearchMatches .watch-quote-row[data-code="${onlyAh.code}"]`).count(), 1);
        const duplicate = page.locator(`#imageSearchMatches .watch-quote-row[data-code="${overlap.code}"]`);
        assert.equal(await duplicate.count(), 1);
        assert.match(await duplicate.locator('.watch-quote-group').first().textContent(), /AG.*AH|AK.*AH/);
      }
      await page.goto(base + '/monitor.html?stock=365330');
      await page.waitForFunction(() => document.querySelector('[data-view=ah]').getAttribute('aria-selected') === 'true');
      assert.match(page.url(), /view=ah/);
      assert.deepEqual(errors, []);
      console.log(JSON.stringify({width, ah: ah.candidates.length, ag: agCount, pixels, errors}));
      await page.close();
    }
    const page = await browser.newPage();
    await page.route('**/data/ah-monitor.json?*', route => route.fulfill({status: 503, body: ''}));
    await page.goto(base + '/monitor.html?view=ag');
    await page.locator('#candidateList > .candidate').first().waitFor();
    await page.locator('[data-view=ah]').click();
    assert.match(await page.locator('#runStatus').textContent(), /AH 데이터를 불러오지 못했습니다/);
    console.log('AH data failure is isolated from AG.');
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); server.close(); process.exitCode = 1; });
