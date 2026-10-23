const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const output = path.join(root, 'outputs', 'candidate-scan-ui');
const payload = JSON.parse(fs.readFileSync(path.join(root, 'data/candidate-monitor.json'), 'utf8'));
const baseline = structuredClone(payload.candidates);
let request, posts = 0, phase = 'idle', denied = false, postLost = false;
const report = {startedAt: '2026-10-23T11:00:00+09:00', completedAt: '2026-10-23T11:03:00+09:00',
  asOf: '2026-10-23', currentDayData: true, checkedStocks: 2450, runId: '12345', newRegistrations: []};

(async () => {
  fs.mkdirSync(output, {recursive: true});
  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    const file = path.resolve(root, `.${pathname === '/' ? '/monitor.html' : pathname}`);
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404); res.end(); return; }
    res.setHeader('Content-Type', ({'.html':'text/html', '.js':'application/javascript', '.css':'text/css', '.json':'application/json'})[path.extname(file)] || 'application/octet-stream');
    if (pathname === '/data/candidate-monitor.json') res.end(JSON.stringify(payload));
    else fs.createReadStream(file).pipe(res);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  try {
    const context = await browser.newContext({viewport: {width: 1280, height: 1000}});
    await context.addInitScript(() => localStorage.setItem('koreaStockMonitor.githubToken', 'test-only-token'));
    await context.route('https://api.github.com/**', async route => {
      const req = route.request();
      if (!req.url().includes('/actions/workflows/update-pages.yml/')) return route.fulfill({status: 404, body:'{}'});
      if (denied) return route.fulfill({status: 403, body:'{}'});
      if (req.method() === 'POST') {
        posts++;
        request = JSON.parse(req.postData());
        phase = 'queued';
        if (postLost) return route.abort();
        return route.fulfill({status: 204});
      }
      const runs = phase === 'idle' ? [] : [{id: 12345, event: 'workflow_dispatch',
        display_title: `Candidate scan ${request?.inputs.request_id || 'other-request'}`,
        status: phase === 'success' || phase === 'failure' ? 'completed' : phase,
        conclusion: phase === 'success' ? 'success' : phase === 'failure' ? 'failure' : null,
        updated_at: '2026-10-23T02:04:00Z'}];
      return route.fulfill({contentType:'application/json', body:JSON.stringify({workflow_runs:runs})});
    });
    const page = await context.newPage();
    await page.clock.install({time: new Date('2026-10-23T11:00:00+09:00')});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const url = `http://127.0.0.1:${server.address().port}/monitor.html?view=operations`;
    const s = name => page.locator(`[data-scan="${name}"]`);
    await page.goto(url);
    await page.locator('#candidateScanButton').click();
    assert.match(await s('counts').innerText(), /AG \d+ · AK \d+/);
    await s('submit').click();
    await page.waitForFunction(() => document.querySelector('[data-scan="status"]').textContent.includes('실행 요청 접수'));
    assert.equal(posts, 1);
    assert.equal(request.ref, 'main');
    assert.ok(request.inputs.request_id);
    assert.equal(await s('submit').isDisabled(), true);
    assert.doesNotMatch(await s('status').innerText(), /등록 완료/);
    // Reload must resume this exact request, not dispatch it again.
    await page.reload();
    await s('check').waitFor();
    await s('check').click();
    assert.equal(posts, 1);
    phase = 'in_progress';
    await s('check').click();
    await page.waitForFunction(() => document.querySelector('[data-scan="status"]').textContent.includes('진행 중'));
    phase = 'success';
    payload.candidateScan = structuredClone(report);
    await s('check').click();
    await page.waitForFunction(() => document.querySelector('[data-scan="status"]').textContent.includes('신규 0종목'));
    assert.equal(await s('submit').isEnabled(), true);
    assert.equal(await s('added').innerText(), '0종목');
    assert.deepEqual(payload.candidates, baseline, 'Refreshing a report must not change registrations');
    assert.equal(await page.locator('#operationContent').isVisible(), true);
    payload.candidateScan.newRegistrations = [baseline[0]];
    await page.locator('#refreshButton').click();
    await page.waitForFunction(() => document.querySelector('[data-scan="added"]').textContent === '1종목');
    payload.candidateScan.newRegistrations = [];
    await page.locator('#refreshButton').click();
    await page.waitForFunction(() => document.querySelector('[data-scan="added"]').textContent === '0종목');
    for (const width of [1280, 390, 320]) {
      await page.setViewportSize({width, height: 1000});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `No overflow at ${width}`);
      assert.equal(await page.locator('#candidateScanButton').evaluate(button => button.scrollWidth <= button.clientWidth), true, `Menu label fits at ${width}`);
      await page.screenshot({path: path.join(output, `${width}.png`), fullPage: true});
    }
    // Existing cloud run is reused, even if it came from the schedule or another device.
    phase = 'queued';
    await s('submit').click();
    await page.waitForFunction(() => document.querySelector('[data-scan="status"]').textContent.includes('이미'));
    assert.equal(posts, 1);
    phase = 'failure';
    await s('check').click();
    await page.waitForFunction(() => document.querySelector('[data-scan="status"]').textContent.includes('완료 실패'));
    assert.equal(await s('submit').isEnabled(), true);
    denied = true;
    await s('submit').click();
    await page.waitForFunction(() => document.querySelector('[data-scan="status"]').textContent.includes('권한'));
    assert.equal(posts, 1);
    assert.equal(await s('submit').isEnabled(), true);
    // A network failure after dispatch is ambiguous: retain the id and check, never repeat POST.
    denied = false;
    phase = 'idle';
    postLost = true;
    await s('submit').click();
    await page.waitForFunction(() => document.querySelector('[data-scan="status"]').textContent.includes('접수 여부'));
    assert.equal(posts, 2);
    assert.equal(await s('submit').isDisabled(), true);
    postLost = false;
    await s('check').click();
    assert.equal(posts, 2);
    assert.equal(errors.length, 0, errors.join('\n'));
    assert.doesNotMatch(await s('status').innerText(), /test-only-token/);
    console.log('Candidate scan UI passed: dispatch, exact-run resume, completion, zero additions, duplicate prevention, permissions, network ambiguity, desktop/mobile.');
    await context.close();
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
