const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require('playwright');
const {FakeGithub} = require('./test_study_images.cjs');

const root = path.resolve(__dirname, '..');
const output = path.join(root, 'outputs', 'study-board-ui');
const image = process.env.STUDY_TEST_IMAGE ? fs.readFileSync(process.env.STUDY_TEST_IMAGE)
  : Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a/wAAAABJRU5ErkJggg==', 'base64');
const github = new FakeGithub([], {missingBranch: true});

(async () => {
  fs.mkdirSync(output, {recursive: true});
  const server = http.createServer((request, response) => {
    const relative = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
    const file = path.resolve(root, `.${relative === '/' ? '/monitor.html' : relative}`);
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
      response.writeHead(404); response.end(); return;
    }
    const type = {'.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json'}[path.extname(file)] || 'application/octet-stream';
    response.writeHead(200, {'Content-Type': `${type}; charset=utf-8`});
    fs.createReadStream(file).pipe(response);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const url = `http://127.0.0.1:${server.address().port}/monitor.html`;
  let browser;
  try {
    browser = await chromium.launch({channel: 'msedge', headless: true});
    const route = async context => {
      await context.route('https://api.github.com/repos/leesgab-ctrl/korea-stock-ma-screener/**', async route => {
        const request = route.request();
        if (request.method() !== 'GET' && request.headers().authorization !== 'Bearer test-only-token') {
          return route.fulfill({status: 403, contentType: 'application/json',
            body: JSON.stringify({message: 'Resource not accessible by personal access token'})});
        }
        const result = await github.fetch(request.url(), {method: request.method(), body: request.postData(),
          headers: Object.fromEntries(Object.entries(request.headers()).map(([key, value]) => [key === 'accept' ? 'Accept' : key, value]))});
        await route.fulfill({status: result.status, contentType: 'application/json', body: JSON.stringify(await result.json())});
      });
      await context.route('https://raw.githubusercontent.com/**', route => route.fulfill({contentType: 'image/png', body: image}));
      await context.route('https://stock.naver.com/domestic/stock/*/discussion', route => route.fulfill({contentType: 'text/html', body: '<title>Stock discussion</title>'}));
    };
    const context = await browser.newContext({viewport: {width: 1280, height: 900}});
    await route(context);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(url);
    await page.waitForFunction(() => document.querySelector('#activeCount').textContent !== '-');
    assert.equal(await page.locator('.main-tabs [data-view="positions"]').count(), 0);
    assert.equal(await page.locator('.main-tabs [data-view="capture"]').count(), 1);
    assert.deepEqual(await page.locator('.main-tabs .view-tab').evaluateAll(nodes => nodes.map(node => node.dataset.view)),
      ['imageSearch', 'ak', 'ag', 'capture', 'operations']);
    assert.equal(await page.locator('#imageSearchPanel').isVisible(), true);
    assert.equal(await page.locator('#imageSearchRising [data-list="image-search-rising"]').count(), 1);
    assert.match(await page.locator('#imageSearchStatus').innerText(), /검색조건 설정 대기/);
    const payload = JSON.parse(fs.readFileSync(path.join(root, 'data/candidate-monitor.json'), 'utf8'));
    for (const family of ['ak', 'ag']) {
      await page.locator(`[data-view="${family}"]`).click();
      const expected = payload.candidates.filter(item => family === 'ak' ? item.candidateTier === 'expanded_ak'
        : !item.candidateTier || item.candidateTier === 'core').sort((a, b) =>
        (b.registeredAt || b.dailySignalDate || '').localeCompare(a.registeredAt || a.dailySignalDate || '')
        || a.name.localeCompare(b.name, 'ko'));
      assert.deepEqual(await page.locator('#candidateList > .candidate').evaluateAll(nodes => nodes.map(node => node.dataset.code)), expected.map(item => item.code));
      const links = await page.locator('#candidateList > .candidate').evaluateAll(nodes => nodes.map(node => {
        const link = node.querySelector('.candidate-board-link');
        return {code: node.dataset.code, text: link.textContent, href: link.href, target: link.target, rel: link.rel, label: link.getAttribute('aria-label')};
      }));
      assert.ok(links.every(link => link.href === `https://stock.naver.com/domestic/stock/${link.code}/discussion`
        && link.text === '종목게시판' && link.target === '_blank' && link.rel.includes('noopener') && link.rel.includes('noreferrer') && link.label.includes('새 탭')));
      assert.equal(await page.locator('.candidate-select a').count(), 0, 'Board links must not be nested inside graph buttons');
      assert.match(await page.locator('#candidateMeta').innerText(), /최근 등록순.*10거래일/);
      assert.equal(await page.locator('#viewTitle').innerText(), family === 'ak' ? '대상종목AK' : '대상종목AG');
      for (const width of [1280, 390, 320]) {
        await page.setViewportSize({width, height: 900});
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
        assert.ok(await page.locator('#candidateList > .candidate').evaluateAll(nodes => nodes.every(node => {
          const link = node.querySelector('.candidate-board-link').getBoundingClientRect();
          const tier = node.querySelector('.candidate-tier').getBoundingClientRect();
          const register = node.querySelector('.candidate-register').getBoundingClientRect();
          const exclude = node.querySelector('.candidate-exclude').getBoundingClientRect();
          const bounds = node.getBoundingClientRect();
          const overlaps = rect => link.left < rect.right && link.right > rect.left && link.top < rect.bottom && link.bottom > rect.top;
          return link.left >= tier.right + 6 && Math.abs((link.top + link.bottom - tier.top - tier.bottom) / 2) <= 1
            && !overlaps(register) && !overlaps(exclude) && link.left >= bounds.left && link.right <= bounds.right + 1;
        })), `Board links must sit beside the tier badge without overlapping controls at ${width}`);
        await page.screenshot({path: path.join(output, `family-${family}-${width}.png`)});
      }
      if (links.length) {
        const originalUrl = page.url();
        const popupReady = page.waitForEvent('popup');
        await page.locator('#candidateList .candidate-board-link').first().click();
        const popup = await popupReady;
        await popup.waitForLoadState('domcontentloaded');
        assert.equal(popup.url(), links[0].href);
        assert.equal(await page.locator('#positionDialog').isVisible(), false);
        assert.equal(page.url(), originalUrl, 'Board clicks must not select a graph');
        await popup.close();
        await page.locator('#candidateList .candidate-select').first().click({position: {x: 18, y: 18}});
        assert.equal(new URL(page.url()).searchParams.get('stock'), links[0].code, 'Graph selection stays available');
      }
    }
    await page.setViewportSize({width: 1280, height: 900});
    assert.match(await page.locator('.main-tabs [data-view="capture"]').innerText(), /^이미지공부/);
    assert.equal(await page.locator('#capturePanel').getAttribute('aria-label'), '이미지공부');
    assert.equal(await page.locator('#capturePanel h2').innerText(), '이미지공부');
    await page.locator('[data-view="operations"]').click();
    await page.locator('#positionsButton').click();
    assert.equal(await page.locator('.positions-panel').isVisible(), true);
    await page.locator('[data-monitor-view="core"]').click();
    assert.equal(await page.locator('#corePanel').isVisible(), true);
    assert.equal(await page.locator('.main-tabs [data-view="operations"]').getAttribute('aria-selected'), 'true');
    await page.locator('[data-monitor-view="target"]').click();
    assert.equal(await page.locator('.workspace').isVisible(), true);
    await page.locator('[data-view="operations"]').click();
    assert.equal(await page.locator('#operationsPanel > .summary').isVisible(), true);
    await page.locator('#watchQuotesButton').click();
    assert.equal(await page.locator('#operationContent').isVisible(), true);
    assert.equal(await page.locator('.workspace').isVisible(), false);
    assert.equal(await page.locator('.main-tabs [data-view="operations"]').getAttribute('aria-selected'), 'true');
    assert.ok(await page.locator('.position-item').count() > 0);
    await page.screenshot({path: path.join(output, 'holdings-desktop.png')});
    await page.locator('#watchQuotesButton').click();
    assert.equal(await page.locator('.positions-panel').isVisible(), false);
    assert.equal(await page.locator('#operationContent').isVisible(), true);
    await page.locator('#positionsButton').click();
    assert.equal(await page.locator('.positions-panel').isVisible(), true);
    await page.locator('[data-view="capture"]').click();
    assert.equal(await page.locator('.positions-panel').isVisible(), false);
    await page.locator('#captureFile').setInputFiles({name: 'study-example.png', mimeType: 'image/png', buffer: image});
    await page.locator('#captureDraftImage').waitFor({state: 'visible'});
    await page.locator('#captureTitle').fill('상승 전 공부 이미지');
    await page.locator('#captureStock').fill('연습종목 000001');
    await page.locator('#captureDate').fill('2026-10-15');
    await page.locator('#captureNote').fill('하락 뒤 바닥에서 단기선이 모이는 모습. 자동 분석이나 문자 연결 없음.');
    await page.locator('#captureSave').click();
    await page.waitForFunction(() => document.querySelector('#captureStatus').textContent.includes('GitHub 연결이 필요'));
    assert.equal(await page.locator('#captureTitle').inputValue(), '상승 전 공부 이미지', 'Failed saves must preserve the draft');
    await page.locator('#captureToken').fill('test-old-token');
    await page.locator('#captureConnect').click();
    await page.locator('#captureSave').click();
    await page.waitForFunction(() => document.querySelector('#captureStatus').textContent.includes('토큰에 이미지 저장 권한이 없습니다'));
    assert.equal(await page.locator('#captureTitle').inputValue(), '상승 전 공부 이미지');
    assert.equal(await page.locator('#captureDraftImage').isVisible(), true, 'Permission errors preserve the selected image');
    await page.locator('#captureToken').fill('test-only-token');
    // Register must use the token currently in the input, not the older saved token.
    await page.locator('#captureSave').click();
    await page.waitForFunction(() => document.querySelector('#captureCount').textContent === '1');
    assert.equal(await page.evaluate(() => localStorage.getItem('koreaStockMonitor.githubToken') === 'test-only-token'), true);
    assert.equal(await page.locator('.capture-item .capture-confirmation input').isChecked(), false);
    await page.locator('.capture-item .capture-confirmation input').check();
    await page.waitForFunction(() => document.querySelector('#captureStatus').textContent.includes('포착조건으로 확정'));
    await page.locator('[data-view="imageSearch"]').click();
    assert.equal(await page.locator('#imageSearchConditions .image-search-condition').count(), 1);
    assert.match(await page.locator('#imageSearchConditions').innerText(), /검색조건 설정 대기/);
    assert.equal(await page.locator('#imageSearchRising h2').innerText(), '오늘상승종목');
    for (const width of [1280, 390, 320]) {
      await page.setViewportSize({width, height: 900});
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await page.screenshot({path: path.join(output, `image-search-${width}.png`), fullPage: true});
    }
    await page.locator('[data-view="capture"]').click();
    await page.locator('.capture-item .capture-confirmation input').uncheck();
    await page.waitForFunction(() => document.querySelector('#captureStatus').textContent.includes('확정을 해제'));
    assert.equal(await page.locator('.capture-item .capture-confirmation input').isChecked(), false);
    await page.locator('.capture-item .capture-confirmation input').check();
    await page.waitForFunction(() => document.querySelector('#captureStatus').textContent.includes('포착조건으로 확정'));
    assert.equal(await page.locator('#captureTitle').inputValue(), '');
    await page.locator('#captureConnection').evaluate(node => node.open = false);
    await page.locator('.capture-image-button').click();
    await page.waitForFunction(() => document.querySelector('#capturePreviewImage').naturalWidth > 0);
    await page.screenshot({path: path.join(output, 'preview-desktop.png')});
    await page.locator('#capturePreviewClose').click();
    await page.locator('.capture-item-actions button', {hasText: '수정'}).click();
    assert.equal(await page.locator('#captureConfirmed').isChecked(), true);
    await page.locator('#captureTitle').fill('<img onerror=alert(1)> 공부 메모');
    await page.locator('#captureNote').fill('수정한 공부 메모');
    await page.locator('#captureSave').click();
    await page.waitForFunction(() => document.querySelector('.capture-item h3')?.textContent.startsWith('<img'));
    assert.equal(await page.locator('.capture-item h3 img').count(), 0);
    await page.locator('.capture-item-actions button', {hasText: '휴지통'}).click();
    await page.waitForFunction(() => document.querySelector('#captureCount').textContent === '0');
    await page.locator('#captureTrash').click();
    await page.locator('.capture-item-actions button', {hasText: '복원'}).click();
    await page.waitForFunction(() => document.querySelector('#captureCount').textContent === '1');
    await page.locator('#captureActive').click();
    await page.locator('#captureFile').setInputFiles({name: 'not-an-image.txt', mimeType: 'text/plain', buffer: Buffer.from('not an image')});
    await page.waitForFunction(() => document.querySelector('#captureStatus').textContent.includes('PNG, JPG'));
    assert.equal(await page.locator('#captureSave').isEnabled(), false);

    await page.evaluate(base64 => {
      const bytes = Uint8Array.from(atob(base64), char => char.charCodeAt(0));
      const transfer = new DataTransfer();
      transfer.items.add(new File([bytes], 'clipboard.png', {type: 'image/png'}));
      document.dispatchEvent(new ClipboardEvent('paste', {clipboardData: transfer, bubbles: true, cancelable: true}));
    }, image.toString('base64'));
    await page.locator('#captureDraftImage').waitFor({state: 'visible'});
    await page.locator('#captureTitle').fill('붙여넣기 공부 이미지');
    await page.locator('#captureConfirmed').check();
    await page.locator('#captureSave').click();
    await page.waitForFunction(() => document.querySelector('#captureCount').textContent === '2');
    assert.equal(await page.locator('.capture-item .capture-confirmation input:checked').count(), 2);
    for (const width of [1280, 390, 320]) {
      await page.setViewportSize({width, height: 900});
      await page.locator('#capturePanel').scrollIntoViewIfNeeded();
      await page.waitForFunction(() => [...document.querySelectorAll('.capture-image-button img')].every(img => img.complete && img.naturalWidth > 0));
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), `No horizontal overflow at ${width}`);
      await page.screenshot({path: path.join(output, `board-${width}.png`), fullPage: true});
    }
    await page.reload();
    await page.locator('[data-view="capture"]').click();
    await page.waitForFunction(() => document.querySelector('#captureCount').textContent === '2');

    const phone = await browser.newContext({viewport: {width: 390, height: 844}});
    await route(phone);
    const phonePage = await phone.newPage();
    await phonePage.goto(url);
    await phonePage.locator('[data-view="capture"]').click();
    await phonePage.waitForFunction(() => document.querySelector('#captureCount').textContent === '2');
    assert.equal(await phonePage.locator('.capture-item .capture-confirmation input:checked').count(), 2);
    assert.equal(await phonePage.evaluate(() => localStorage.getItem('koreaStockMonitor.githubToken')), null);
    await phonePage.screenshot({path: path.join(output, 'phone-public-read.png'), fullPage: true});
    assert.deepEqual(errors, []);
    console.log('Study board UI passed: holdings move, image upload/paste, public cross-device read, editing, trash/restore, validation, XSS text and responsive layouts.');
    console.log(`Screenshots: ${output}`);
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
