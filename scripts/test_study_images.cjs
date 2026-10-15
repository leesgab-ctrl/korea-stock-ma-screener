const assert = require('node:assert/strict');
const {StudyImageStore, applyChange, validateIndex, BRANCH} = require('../rising-patterns.js');
const clone = value => JSON.parse(JSON.stringify(value));
const record = (id = 'pattern-0001', revision = 'revision-1') => ({id, revision, title: 'Study image', stock: '000001',
  chartDate: '2026-10-15', note: 'Before the rise', imagePath: `data/study-images/${id}.png`, mime: 'image/png',
  width: 230, height: 120, bytes: 200, createdAt: '2026-10-15T10:00:00+09:00', updatedAt: '2026-10-15T10:00:00+09:00'});

class FakeGithub {
  constructor(records = [], {missingBranch = false} = {}) {
    this.seq = 1;
    this.main = this.sha();
    this.head = missingBranch ? null : this.main;
    this.snapshots = new Map([[this.main, {version: 1, records: clone(records)}]]);
    this.trees = new Map();
    this.parents = new Map();
    this.calls = [];
    this.injectConflict = null;
  }
  sha() { return (this.seq++).toString(16).padStart(40, '0'); }
  response(status, value) { return {ok: status >= 200 && status < 300, status, json: async () => clone(value)}; }
  async fetch(url, options = {}) {
    const parsed = new URL(url);
    const endpoint = parsed.pathname.split('/repos/leesgab-ctrl/korea-stock-ma-screener/')[1];
    const method = options.method || 'GET';
    const body = options.body ? JSON.parse(options.body) : null;
    this.calls.push({endpoint, method, body, headers: options.headers});
    if (method === 'GET' && endpoint.startsWith('git/ref/heads/')) {
      const head = endpoint === 'git/ref/heads/main' ? this.main : this.head;
      return this.response(head ? 200 : 404, head ? {object: {sha: head}} : {});
    }
    if (method === 'POST' && endpoint === 'git/refs') {
      assert.equal(body.ref, `refs/heads/${BRANCH}`);
      this.head = body.sha;
      return this.response(201, {object: {sha: this.head}});
    }
    if (method === 'GET' && endpoint.startsWith('contents/')) {
      assert.equal(options.headers.Accept, 'application/vnd.github.raw+json');
      return this.response(200, this.snapshots.get(parsed.searchParams.get('ref')));
    }
    if (method === 'POST' && endpoint === 'git/blobs') return this.response(201, {sha: this.sha()});
    if (method === 'GET' && endpoint.startsWith('git/commits/')) return this.response(200, {tree: {sha: `tree-${endpoint.split('/').at(-1)}`}});
    if (method === 'POST' && endpoint === 'git/trees') {
      assert.ok(body.base_tree, 'A base tree is required to preserve all other files');
      const sha = this.sha();
      this.trees.set(sha, JSON.parse(body.tree.find(item => item.path === 'data/study-images.json').content));
      return this.response(201, {sha});
    }
    if (method === 'POST' && endpoint === 'git/commits') {
      const sha = this.sha();
      this.snapshots.set(sha, clone(this.trees.get(body.tree)));
      this.parents.set(sha, body.parents[0]);
      return this.response(201, {sha});
    }
    if (method === 'PATCH' && endpoint.startsWith('git/refs/heads/')) {
      assert.equal(endpoint, `git/refs/heads/${BRANCH}`, 'Never update the monitoring branch');
      assert.equal(body.force, false);
      if (this.injectConflict) {
        const callback = this.injectConflict;
        this.injectConflict = null;
        const newer = clone(this.snapshots.get(this.head));
        callback(newer);
        this.head = this.sha();
        this.snapshots.set(this.head, newer);
        return this.response(422, {});
      }
      if (this.parents.get(body.sha) !== this.head) return this.response(422, {});
      this.head = body.sha;
      if (this.loseWriteResponse) { this.loseWriteResponse = false; throw new Error('Connection lost after saving'); }
      return this.response(200, {object: {sha: this.head}});
    }
    throw new Error(`Unexpected request ${method} ${endpoint}`);
  }
  store(token = () => 'test-only-token') { return new StudyImageStore({fetcher: this.fetch.bind(this), token}); }
}

async function run() {
  const first = record();
  assert.equal(applyChange({version: 1, records: []}, {record: first, expectedRevision: null}).records.length, 1);
  assert.throws(() => applyChange({version: 1, records: [first]}, {record: first, expectedRevision: null}), /다른 기기/);
  assert.throws(() => applyChange({version: 1, records: [first]}, {record: first, expectedRevision: 'old'}), /다른 기기/);
  assert.throws(() => validateIndex({version: 1, records: [{...first, imagePath: 'https://other.invalid/image.png'}]}));
  assert.throws(() => validateIndex({version: 1, records: [{...first, imagePath: 'data/study-images/../../secret.png'}]}));
  assert.throws(() => validateIndex({version: 1, records: [{...first, mime: 'image/svg+xml'}]}));
  assert.throws(() => validateIndex({version: 1, records: [first, first]}));
  assert.throws(() => validateIndex({version: 1, records: [{...first, createdAt: 'invalid'}]}));
  const fake = new FakeGithub([], {missingBranch: true});
  const originalMain = fake.main;
  const store = fake.store();
  const saved = await store.save({record: first, expectedRevision: null}, {base64: 'iVBORw0KGgo='});
  assert.equal(saved.index.records[0].title, first.title);
  assert.equal(fake.main, originalMain);
  assert.equal(fake.calls.filter(call => call.endpoint === 'git/blobs').length, 1);
  assert.ok(store.imageUrl(first, saved.revision).includes(saved.revision));
  assert.throws(() => store.imageUrl(first, 'main'));
  const read = await new StudyImageStore({fetcher: fake.fetch.bind(fake)}).read();
  assert.equal(read.index.records.length, 1, 'Public reading does not need a token');
  const callsBefore = fake.calls.length;
  await assert.rejects(() => fake.store(() => '').save({record: first}), /연결/);
  assert.equal(fake.calls.length, callsBefore, 'Missing credentials must not transmit an image');

  const edited = {...first, title: '<img onerror=alert(1)>', revision: 'revision-2'};
  const updated = await store.save({record: edited, expectedRevision: first.revision});
  assert.equal(updated.index.records[0].title, edited.title, 'Text must remain data, not HTML');
  assert.equal(fake.calls.filter(call => call.endpoint === 'git/blobs').length, 1, 'Metadata edit must not reupload the image');
  const trashed = {...edited, deletedAt: '2026-10-15T11:00:00+09:00', revision: 'revision-3'};
  await store.save({record: trashed, expectedRevision: edited.revision});
  const restored = {...trashed, deletedAt: null, revision: 'revision-4'};
  const restoredResult = await store.save({record: restored, expectedRevision: trashed.revision});
  assert.equal(restoredResult.index.records[0].deletedAt, null);
  assert.equal(restoredResult.index.records[0].imagePath, first.imagePath);

  const concurrent = new FakeGithub([first]);
  concurrent.injectConflict = index => index.records.push(record('pattern-other', 'other-1'));
  const concurrentResult = await concurrent.store().save({record: record('pattern-new'), expectedRevision: null}, {base64: 'iVBORw0KGgo='});
  assert.equal(concurrentResult.index.records.length, 3, 'Concurrent additions must both survive a retry');
  assert.equal(concurrent.calls.filter(call => call.endpoint === 'git/blobs').length, 1, 'A retry must reuse the uploaded blob');
  assert.equal(concurrent.main, originalMain);

  const conflict = new FakeGithub([first]);
  conflict.injectConflict = index => { index.records[0].revision = 'edited-elsewhere'; };
  await assert.rejects(() => conflict.store().save({record: edited, expectedRevision: first.revision}), /다른 기기/);
  assert.equal(conflict.snapshots.get(conflict.head).records[0].revision, 'edited-elsewhere');

  const uncertain = new FakeGithub([]);
  uncertain.loseWriteResponse = true;
  const confirmed = await uncertain.store().save({record: first, expectedRevision: null}, {base64: 'iVBORw0KGgo='});
  assert.equal(confirmed.index.records.length, 1, 'Confirm an uncertain successful write without duplicating the post');

  const denied = new StudyImageStore({token: () => 'test-only-token', fetcher: async () => ({ok: false, status: 403})});
  await assert.rejects(() => denied.save({record: first}), /권한/);
  console.log('Study image board tests passed: public reads, isolated atomic saves, image reuse, concurrent edits, validation and trash/restore.');
}
module.exports = {FakeGithub, record};
if (require.main === module) run().catch(error => { console.error(error); process.exitCode = 1; });
