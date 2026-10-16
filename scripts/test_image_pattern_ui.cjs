const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');
const {FakeGithub, record} = require('./test_study_images.cjs');
const root = path.resolve(__dirname, '..');
const output = path.join(root, 'outputs', 'image-pattern-ui');
const rules = JSON.parse(fs.readFileSync(path.join(root, 'data/image-search-rules.json'), 'utf8')).rules;
const records = rules.map((rule, i) => ({...record(rule.imageIds[0]), title: ['대성산업 참고', '대한제강 참고', '79397 밀집 참고'][i], stock: ['대성산업', '대한제강', ''][i],
  conditionConfirmed: true, conditionConfirmedAt: '2026-10-01T08:00:00+09:00'}));
const github = new FakeGithub(records);
const python = process.env.PYTHON_EXE || 'python';
const payload = JSON.parse(execFileSync(python, ['-c',
  "import sys,json,datetime as d;sys.path.insert(0,'scripts');from image_pattern_search import process;p=json.load(open('data/candidate-monitor.json',encoding='utf-8'));b=json.load(sys.stdin);process(p,d.datetime.fromisoformat('2026-10-15T23:59:00+09:00'),no_notify=True,board=b);print(json.dumps(p,ensure_ascii=False))"],
  {cwd: root, input: JSON.stringify({version: 1, records}), encoding: 'utf8', env: {...process.env, PYTHONUTF8: '1'}, maxBuffer: 40 * 1024 * 1024}));
const image = process.env.STUDY_TEST_IMAGE ? fs.readFileSync(process.env.STUDY_TEST_IMAGE)
  : Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a/wAAAABJRU5ErkJggg==', 'base64');
// A deterministic UI contract fixture avoids depending on a live stock matching the new rule.
const clusterRule = rules.find(rule => rule.type === 'rising_cluster_ma3');
payload.imageSearch.matches.push({...payload.imageSearch.matches.find(event => event.code === '128820'),
  ruleId: clusterRule.id, ruleName: clusterRule.name, sourceImageIds: clusterRule.imageIds,
  signalType: 'ma3Rise', stage: 'confirmation', stageLabel: 'MA3 2봉 연속 상승 · 2차'});

(async () => {
  fs.mkdirSync(output, {recursive: true});
  const server = http.createServer((request, response) => {
    const relative = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
    const file = path.resolve(root, `.${relative === '/' ? '/monitor.html' : relative}`);
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) { response.writeHead(404); response.end(); return; }
    const type = {'.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json'}[path.extname(file)] || 'application/octet-stream';
    response.writeHead(200, {'Content-Type': `${type}; charset=utf-8`});
    if (relative === '/data/candidate-monitor.json') response.end(JSON.stringify(payload));
    else fs.createReadStream(file).pipe(response);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  try {
    const context = await browser.newContext({viewport: {width: 1280, height: 1000}});
    await context.addInitScript(() => localStorage.setItem('koreaStockMonitor.githubToken', 'test-only-token'));
    await context.route('https://api.github.com/repos/leesgab-ctrl/korea-stock-ma-screener/**', async route => {
      const request = route.request();
      const result = await github.fetch(request.url(), {method: request.method(), body: request.postData(),
        headers: Object.fromEntries(Object.entries(request.headers()).map(([key, value]) => [key === 'accept' ? 'Accept' : key, value]))});
      await route.fulfill({status: result.status, contentType: 'application/json', body: JSON.stringify(await result.json())});
    });
    await context.route('https://raw.githubusercontent.com/**', route => route.fulfill({contentType: 'image/png', body: image}));
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}/monitor.html`);
    await page.waitForFunction(() => document.querySelector('#imageSearchStatus').textContent.startsWith('3개 독립 조건'));
    for (const code of ['128820', '084010']) assert.equal(await page.locator(`#imageSearchMatches details[data-code="${code}"]`).count(), 1);
    const comments = await page.locator('#imageSearchMatches .image-pattern-comment').allTextContents();
    assert.ok(comments.some(text => text.includes('대성산업형')));
    assert.ok(comments.some(text => text.includes('대한제강형')));
    assert.ok(comments.some(text => text.includes('MA3 2봉 연속 상승 · 2차')));
    assert.match(await page.locator('#imageSearchConditions').innerText(), /1% 미만/);
    for (const width of [1280, 390, 320]) {
      await page.setViewportSize({width, height: 1000});
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      assert.ok(await page.locator('.image-pattern-comment').evaluateAll(nodes => nodes.every(node => node.scrollWidth <= node.clientWidth + 1 && node.clientWidth > node.parentElement.clientWidth * .8)), 'Signal comments must span the full row and wrap');
      await page.screenshot({path: path.join(output, `matches-${width}.png`), fullPage: true});
    }
    await page.locator('#imageSearchMatches details[data-code="128820"] summary').click();
    await page.locator('#imageSearchMatches details[data-code="128820"] iframe').waitFor();
    await page.locator('[data-view="capture"]').click();
    assert.match(await page.locator('.capture-item').filter({hasText: '대성산업 참고'}).innerText(), /검색·알림 사용 중/);
    await page.locator(`.capture-item[data-id="${rules[0].imageIds[0]}"] .capture-confirmation input`).uncheck();
    await page.waitForFunction(() => document.querySelector('#captureStatus').textContent.includes('확정을 해제'));
    await page.locator('[data-view="imageSearch"]').click();
    assert.match(await page.locator('#imageSearchStatus').innerText(), /^2개 독립 조건/);
    assert.ok((await page.locator('#imageSearchMatches .image-pattern-comment').allTextContents()).every(text => !text.includes('대성산업형')));
    await page.locator('[data-view="capture"]').click();
    assert.match(await page.locator(`.capture-item[data-id="${clusterRule.imageIds[0]}"]`).innerText(), /검색·알림 사용 중/);
    await page.locator(`.capture-item[data-id="${clusterRule.imageIds[0]}"] .capture-confirmation input`).uncheck();
    await page.waitForFunction(() => document.querySelector('#captureStatus').textContent.includes('확정을 해제'));
    await page.locator('[data-view="imageSearch"]').click();
    assert.match(await page.locator('#imageSearchStatus').innerText(), /^1개 독립 조건/);
    assert.ok((await page.locator('#imageSearchMatches .image-pattern-comment').allTextContents()).every(text => !text.includes('MA3 2봉 연속 상승')));
    assert.equal(await page.locator('#imageSearchRising h2').innerText(), '오늘상승종목');
    assert.deepEqual(errors, []);
    console.log('Image pattern UI passed: independent rules, actual examples, merged stock rows, signal times, chart expansion, shared switch and desktop/mobile layouts.');
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
