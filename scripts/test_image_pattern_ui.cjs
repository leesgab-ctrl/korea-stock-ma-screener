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
const records = [...new Map(rules.flatMap((rule, i) => rule.imageIds.map(id => [id, {...record(id),
  title: ['대성산업 참고', '대한제강 참고', '79397 밀집 참고', 'MA120 회복 참고', 'MA20 피라미드 참고'][i] || rule.name,
  stock: i === 0 ? '대성산업' : i === 1 ? '대한제강' : '',
  conditionConfirmed: true, conditionConfirmedAt: '2026-10-01T08:00:00+09:00'}]))).values()];
const github = new FakeGithub(records);
const python = process.env.PYTHON_EXE || 'python';
const payload = JSON.parse(execFileSync(python, ['-c',
  "import sys,json,datetime as d;sys.path.insert(0,'scripts');from image_pattern_search import process;p=json.load(open('data/candidate-monitor.json',encoding='utf-8'));b=json.load(sys.stdin);process(p,d.datetime.fromisoformat('2026-10-15T23:59:00+09:00'),no_notify=True,board=b);print(json.dumps(p,ensure_ascii=False))"],
  {cwd: root, input: JSON.stringify({version: 1, records}), encoding: 'utf8', env: {...process.env, PYTHONUTF8: '1'}, maxBuffer: 40 * 1024 * 1024}));
const image = process.env.STUDY_TEST_IMAGE ? fs.readFileSync(process.env.STUDY_TEST_IMAGE)
  : Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a/wAAAABJRU5ErkJggg==', 'base64');
// A deterministic UI contract fixture avoids depending on a live stock matching the new rule.
const marketDays = [...new Set(payload.candidates.flatMap(item => (item.displayCharts?.intraday?.series || []).map(row => row.t.slice(0, 10))))].sort();
const latestDay = marketDays.at(-1);
const priorDay = marketDays.at(-2);
const oldDay = marketDays.at(-3);
const baseDaesung = payload.imageSearch.matches.find(event => event.code === '128820' && event.ruleId === rules[0].id);
const baseDaehan = payload.imageSearch.matches.find(event => event.code === '084010' && event.ruleId === rules[1].id);
const baseInno = payload.imageSearch.matches.find(event => event.code === '274400');
payload.imageSearch.matches.push({...baseDaesung, stage: 'primary', time: `${latestDay}T10:00+09:00`, completedAt: `${latestDay}T10:30+09:00`});
payload.imageSearch.matches.push({...baseDaesung, time: `${latestDay}T11:00+09:00`, completedAt: `${latestDay}T11:30+09:00`});
payload.imageSearch.matches.push({...baseDaesung, ruleId: rules[1].id, ruleName: rules[1].name, sourceImageIds: rules[1].imageIds,
  time: `${latestDay}T11:00+09:00`, completedAt: `${latestDay}T11:30+09:00`});
payload.imageSearch.matches.push({...baseDaehan, time: `${priorDay}T12:00+09:00`, completedAt: `${priorDay}T12:30+09:00`});
payload.imageSearch.matches.push({...baseInno, time: `${latestDay}T14:00+09:00`, completedAt: `${latestDay}T14:30+09:00`});
payload.candidates.push({...payload.candidates.find(item => item.code === '128820'), code: '999999', name: 'Old signal only'});
payload.imageSearch.matches.push({...baseDaesung, code: '999999', name: 'Old signal only', time: `${oldDay}T10:00+09:00`, completedAt: `${oldDay}T10:30+09:00`});
const clusterRule = rules.find(rule => rule.type === 'rising_cluster_ma3');
payload.imageSearch.matches.push({...baseDaesung, time: `${latestDay}T12:00+09:00`, completedAt: `${latestDay}T12:30+09:00`,
  ruleId: clusterRule.id, ruleName: clusterRule.name, sourceImageIds: clusterRule.imageIds,
  signalType: 'ma3Rise', clusterLines: [3, 10, 20, 40, 60], changePct: 2.9, fastRecovery: false, stage: 'confirmation', stageLabel: '5선 밀집 · MA3 2봉 연속 상승 · 2차'});
const ma120Rule = rules.find(rule => rule.type === 'ma3_ma120_recovery');
payload.imageSearch.matches.push({...baseDaesung, time: `${latestDay}T12:00+09:00`, completedAt: `${latestDay}T12:30+09:00`,
  ruleId: ma120Rule.id, ruleName: ma120Rule.name, sourceImageIds: ma120Rule.imageIds,
  signalType: 'ma3Ma120', stage: 'confirmation', stageLabel: '다음 봉 MA3 ≥ MA120 유지 · 2차 확인'});
const nearRule = rules.find(rule => rule.type === 'ma3_ma60_near_rebound');
for (const [stage, clock] of [['watch', '13:30'], ['primary', '14:00'], ['confirmation', '14:30']]) {
  payload.imageSearch.matches.push({...baseDaesung, time: `${latestDay}T${clock}+09:00`, completedAt: `${latestDay}T${clock}+09:00`,
    ruleId: nearRule.id, ruleName: nearRule.name, sourceImageIds: nearRule.imageIds, signalType: 'ma3Ma60Near', stage,
    gapPercent: stage === 'confirmation' ? 1 : -1, maxGapPercent: 2});
}
const innoCandidate = payload.candidates.find(item => item.code === '274400');
const innoChartSets = [innoCandidate.displayCharts, payload.holdingCharts?.['274400']?.displayCharts].filter(Boolean);
const priorQuoteTimes = [innoCandidate.lastPriceTime, innoCandidate.intraday?.quoteTime,
  ...innoChartSets.flatMap(charts => [charts.quote?.time, charts.intraday?.fetchedAt, charts.intraday?.series?.at(-1)?.t])]
  .map(value => Date.parse(value)).filter(Number.isFinite);
const fixtureTime = new Date(Math.max(...priorQuoteTimes) + (9 * 60 + 31) * 60000).toISOString().slice(0, 19) + '+09:00';
// Keep the quote fixture newer than refreshed market data used for chart rendering.
for (const charts of innoChartSets) charts.quote = {
  price: 2365, previousClose: 2210, time: fixtureTime, source: 'naver_quote', dataStatus: 'ok',
};

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
    await page.waitForFunction(() => document.querySelector('#imageSearchMatches h2')?.textContent === '이미지조건 포착이력');
    await page.locator('#imageSearchMatches details[data-code="128820"]').waitFor();
    for (const code of ['128820', '084010']) assert.equal(await page.locator(`#imageSearchMatches details[data-code="${code}"]`).count(), 1);
    assert.equal(await page.locator('#imageSearchMatches details[data-code="999999"]').count(), 0, 'A stock with only old signals must be hidden');
    assert.match(await page.locator('#imageSearchMatches .watch-quote-time').innerText(), /최근 2거래일 포착순/);
    const comments = await page.locator('#imageSearchMatches .image-pattern-comment').allTextContents();
    assert.ok(comments.some(text => text.includes('[하락중 MA10이 MA40 돌파]')));
    assert.ok(comments.some(text => text.includes('[5선 밀집후 MA3 2봉 상승]')));
    assert.ok(comments.some(text => text.includes('+2.9%')));
    assert.ok(comments.some(text => text.includes('[120MA아래로 3MA하락후 상승]')));
    assert.ok(comments.some(text => text.includes('[피라미드형3MA반등]')));
    assert.ok(comments.some(text => text.includes('[MA60 부근 MA3 돌파 확인]')));
    assert.ok(comments.some(text => text.includes('[피라미드형 상승가능성 검토]')));
    assert.ok(comments.every(text => !text.includes('피라미드후')));
    assert.equal(await page.locator('.image-pattern-strength').count(), 0);
    assert.ok(comments.every(text => !/대성산업형|대한제강형|빠른 회복 참고|포착 종가/.test(text)));
    assert.ok(await page.locator('.image-pattern-name').evaluateAll(nodes => nodes.every(node => getComputedStyle(node).color === 'rgb(49, 103, 173)')));
    assert.ok(await page.locator('.image-pattern-time').evaluateAll(nodes => nodes.every(node => /\d{2}\.\d{2}\. \d{2}:\d{2}/.test(node.textContent))));
    const displayedTimes = await page.locator('.image-pattern-time').allTextContents();
    const allowedPrefixes = [latestDay, priorDay].map(day => day.slice(5).replace('-', '.') + '.');
    assert.ok(displayedTimes.every(text => allowedPrefixes.some(prefix => text.trim().startsWith(prefix))), 'Old descriptions must be hidden even on stocks with recent signals');
    assert.equal(await page.locator('#imageSearchConditions').count(), 0);
    assert.equal(await page.locator('#imageSearchPanel > :not([hidden])').first().getAttribute('id'), 'imageSearchMatches');
    for (const section of ['#imageSearchMatches', '#imageSearchRising']) {
      assert.equal(await page.locator(`${section} .watch-quote-header > :nth-child(2)`).textContent(), '대상종목');
      const familyLabels = await page.locator(`${section} .watch-quote-row`).evaluateAll(rows => rows.map(row => ({
        code: row.dataset.code, family: row.querySelector('summary > :nth-child(2)').textContent
      })));
      assert.ok(familyLabels.length > 0);
      for (const {code, family} of familyLabels) {
        assert.equal(family, payload.candidates.find(item => item.code === code).candidateTier === 'expanded_ak' ? 'AK' : 'AG');
      }
      assert.equal(await page.locator(`${section} .watch-quote-timestamp`).count(), 0, 'Price observation time must not be repeated below every price');
    }
    await page.waitForFunction(() => [...document.querySelectorAll('.image-pattern-thumbnail img')].every(img => img.complete && img.naturalWidth > 0));
    const sourceRow = page.locator('#imageSearchMatches details[data-code="128820"]');
    const sourceLine = sourceRow.locator('.image-pattern-entry').filter({hasText: `${latestDay.slice(5).replace('-', '.')}. 11:30`});
    assert.equal(await sourceLine.count(), 1, 'Matching rule events must remain merged into one description');
    assert.deepEqual(await sourceLine.locator('.image-pattern-thumbnail').evaluateAll(nodes => nodes.map(node => node.dataset.imageId)), [rules[0].imageIds[0]], 'Merged descriptions show only one representative icon');
    const icon = sourceLine.locator('.image-pattern-thumbnail').first();
    const thumbnailSrc = await icon.locator('img').getAttribute('src');
    const imageId = await icon.getAttribute('data-image-id');
    const referenceImage = records.find(item => item.id === imageId);
    const originalUrl = page.url();
    await icon.click();
    assert.equal(await page.locator('#capturePreviewDialog').isVisible(), true);
    assert.ok((await page.locator('#capturePreviewImage').getAttribute('src')).endsWith(referenceImage.imagePath));
    assert.ok(thumbnailSrc.endsWith(rules[0].stageThumbnails.confirmation.path), 'MA10 confirmation uses the newly supplied crossover crop');
    assert.ok(await sourceLine.locator('.image-pattern-thumbnail img').evaluateAll((nodes, path) => nodes.every(node => node.src.endsWith(path)), rules[1].thumbnail.path), 'MA10 confirmation keeps the supplied crop');
    const primaryIcons = page.locator('.image-pattern-entry').filter({hasText: '[하락중 MA3가 MA40 돌파]'}).locator('.image-pattern-thumbnail img');
    assert.ok(await primaryIcons.count() > 0);
    assert.ok(await primaryIcons.evaluateAll((nodes, path) => nodes.every(node => node.src.endsWith(path)), rules[0].thumbnail.path), 'MA3 primary keeps its previously approved crop');
    assert.equal(await sourceRow.getAttribute('open'), null, 'Thumbnail clicks must not expand stock charts');
    assert.equal(await sourceRow.locator('iframe').count(), 0);
    assert.equal(page.url(), originalUrl, 'Preview must stay in Rising Search');
    await page.locator('#capturePreviewClose').click();
    await icon.press('Enter');
    assert.equal(await page.locator('#capturePreviewDialog').isVisible(), true);
    await page.locator('#capturePreviewClose').click();
    const inno = page.locator('#imageSearchMatches details[data-code="274400"]');
    assert.match(await inno.locator('summary .watch-quote-price').nth(0).innerText(), /2,365원/);
    assert.equal(await inno.locator('summary .watch-quote-price').nth(1).innerText(), '+7.0%');
    assert.equal(await inno.locator('summary .watch-quote-price').nth(1).getAttribute('data-direction'), 'up');
    await inno.locator('summary').click();
    await page.waitForFunction(() => {
      const frame = document.querySelector('#imageSearchMatches details[data-code="274400"] iframe');
      return frame?.contentDocument?.querySelector('#dailyChart')?.width > 0
        && frame.contentDocument.querySelector('#maChart')?.width > 0;
    });
    for (const width of [1280, 390, 320]) {
      await page.setViewportSize({width, height: 1000});
      assert.ok(await page.locator('.image-pattern-entry').evaluateAll(nodes => nodes.every(node => node.querySelectorAll('.image-pattern-thumbnail').length <= 1)), 'Each description has at most one icon across viewports');
      assert.ok(await page.locator('.image-pattern-thumbnail').evaluateAll(nodes => nodes.every(node => {
        const box = node.getBoundingClientRect();
        return box.height === 16 && box.width === 24;
      })), 'All reference icons must be exactly 16px high and 24px wide across viewports');
      await page.waitForFunction(() => {
        const frame = document.querySelector('#imageSearchMatches details[data-code="274400"] iframe');
        return ['maChart', 'dailyChart'].every(id => {
          const canvas = frame.contentDocument.getElementById(id);
          return Math.abs(canvas.width - canvas.clientWidth * frame.contentWindow.devicePixelRatio) <= 1;
        });
      });
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      assert.ok(await page.locator('.image-pattern-comment').evaluateAll(nodes => nodes.every(node => node.scrollWidth <= node.clientWidth + 1 && node.clientWidth > node.parentElement.clientWidth * .8)), 'Signal comments must span the full row and wrap');
      const chartWidths = await inno.locator('iframe').evaluate(frame => {
        const doc = frame.contentDocument;
        return {parent: frame.parentElement.clientWidth, frame: frame.getBoundingClientRect().width,
          viewport: frame.contentWindow.innerWidth, document: doc.documentElement.scrollWidth,
          canvases: ['maChart', 'dailyChart'].map(id => ({id, right: doc.getElementById(id).getBoundingClientRect().right,
            width: doc.getElementById(id).getBoundingClientRect().width}))};
      });
      assert.ok(chartWidths.frame <= chartWidths.parent + 1 && chartWidths.document <= chartWidths.viewport + 1
        && chartWidths.canvases.every(canvas => canvas.right <= chartWidths.viewport + 1), `Expanded charts must fit at ${width}: ${JSON.stringify(chartWidths)}`);
      assert.ok(await inno.locator('iframe').evaluate(frame => ['maChart', 'dailyChart'].every(id => {
        const canvas = frame.contentDocument.getElementById(id);
        const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
        let colored = 0;
        for (let i = 0; i < pixels.length; i += 16) {
          if (pixels[i + 3] && Math.max(pixels[i], pixels[i+1], pixels[i+2]) - Math.min(pixels[i], pixels[i+1], pixels[i+2]) > 30) colored++;
        }
        return colored > 30;
      })), 'Both expanded charts must contain rendered candles and moving-average colors');
      assert.ok(await inno.locator('iframe').evaluate(frame => [...frame.contentDocument.querySelectorAll('.phase-legend span')]
        .every(el => el.getBoundingClientRect().right <= frame.contentWindow.innerWidth)), 'Every color legend label must stay visible');
      assert.equal(await inno.locator('.watch-quote-timestamp').count(), 0, 'Remove repeated 20:xx time below prices');
      assert.match(await inno.locator('summary .watch-quote-price').first().getAttribute('title'), /수집 기준/, 'Keep the price timestamp in its tooltip');
      assert.ok(await page.locator('.family-quotes .watch-quote-header > :nth-child(2) > span').evaluateAll(nodes => nodes.every(node => node.scrollWidth <= node.clientWidth + 1)), 'The target-family heading must fit on mobile');
      await inno.screenshot({path: path.join(output, `expanded-274400-${width}.png`)});
      await page.screenshot({path: path.join(output, `matches-${width}.png`), fullPage: true});
    }
    await page.locator('#imageSearchMatches details[data-code="128820"] summary').click();
    await page.locator('#imageSearchMatches details[data-code="128820"] iframe').waitFor();
    await page.locator('[data-view="capture"]').click();
    assert.match(await page.locator('.capture-item').filter({hasText: '대성산업 참고'}).innerText(), /검색·알림 사용 중/);
    await page.locator(`.capture-item[data-id="${rules[0].imageIds[0]}"] .capture-confirmation input`).uncheck();
    await page.waitForFunction(() => document.querySelector('#captureStatus').textContent.includes('확정을 해제'));
    await page.locator('[data-view="imageSearch"]').click();
    assert.equal(await page.locator('#imageSearchStatus').isVisible(), false);
    assert.ok((await page.locator('#imageSearchMatches .image-pattern-entry').evaluateAll(nodes => nodes.map(node => node.title))).every(text => !text.startsWith(rules[0].name)));
    await page.locator('[data-view="capture"]').click();
    assert.match(await page.locator(`.capture-item[data-id="${clusterRule.imageIds[0]}"]`).innerText(), /검색·알림 사용 중/);
    await page.locator(`.capture-item[data-id="${clusterRule.imageIds[0]}"] .capture-confirmation input`).uncheck();
    await page.waitForFunction(() => document.querySelector('#captureStatus').textContent.includes('확정을 해제'));
    await page.locator('[data-view="imageSearch"]').click();
    assert.equal(await page.locator('#imageSearchStatus').isVisible(), false);
    assert.ok((await page.locator('#imageSearchMatches .image-pattern-comment').allTextContents()).every(text => !text.includes('5선 밀집후 MA3 2봉 상승')));
    await page.locator('[data-view="capture"]').click();
    const retainedImage = page.locator('.capture-item[data-id="4d923542-862d-43e6-bedc-4fd4cf4c8d0f"]');
    assert.equal(await retainedImage.count(), 1, 'Daesung study image must remain stored');
    const card = page.locator(`.capture-item[data-id="${nearRule.imageIds[0]}"]`);
    assert.match(await card.innerText(), /검색·알림 사용 중/);
    await card.locator('.capture-confirmation input').uncheck();
    await page.waitForFunction(() => document.querySelector('#captureStatus').textContent.includes('확정을 해제'));
    await page.locator('[data-view="imageSearch"]').click();
    assert.ok((await page.locator('#imageSearchMatches .image-pattern-comment').allTextContents()).every(text => !/MA60 부근|피라미드형3MA반등|피라미드형 상승가능성 검토/.test(text)));
    assert.equal(await page.locator('#imageSearchRising h2').innerText(), '오늘상승종목');
    assert.deepEqual(errors, []);
    console.log('Image pattern UI passed: independent rules, actual examples, merged stock rows, signal times, chart expansion, shared switch and desktop/mobile layouts.');
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
