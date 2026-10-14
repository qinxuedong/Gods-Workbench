// C4-AC5b 未归属收件箱：运行真实控制器脚本，替换 DOM 与网络，不连接任何服务。
import {readFileSync} from 'node:fs';
import {createContext, runInContext} from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';

const source = readFileSync(new URL('../../web/js/controllers/assets-controller.js', import.meta.url), 'utf8');
const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});

function harness(plan, elements = {}) {
  const requests = [];
  const context = createContext({
    window: {addEventListener() {}, location: {search: ''}},
    document: {getElementById: id => elements[id] || null, body: {appendChild() {}}, createElement: () => ({})},
    MutationObserver: class { observe() {} disconnect() {} }, CSS: {escape: v => v}, console,
    fetch: async (url, options = {}) => {
      requests.push({url, ...options});
      const step = plan.shift();
      assert.ok(step, `意外请求 ${url}`);
      if (step.url) assert.equal(url, step.url);
      if (step.expected !== undefined) { const body = JSON.parse(options.body); assert.equal(body.expected_asset_version, step.expected); assert.equal('expected_version' in body, false); }
      return response(step.data, step.status || 200);
    },
  });
  context.window.window = context.window;
  runInContext(source, context);
  const inbox = context.window.GWUnassignedInbox;
  return {inbox, requests, plan};
}
const INBOX = '/api/assets/unassigned?limit=500';
const MOVE = '/api/assets/a1/move-to-project';
const asset = (id, meta = {}, v = 1) => ({asset_id: id, name: id, metadata: meta, asset_version: v});
const inbox = (revision, assets) => ({assets, items: assets, total: assets.length, revision});

test('C4-AC5b 来源未知只取后端 source_unknown，不按字段缺失推断', () => {
  const {inbox: box} = harness([]);
  assert.equal(box.sourceUnknown({...asset('x'), source_unknown: true, origin_label: '来源未知'}), true);
  // 字段全缺但后端说 false：不标记（旧推断会误标）。
  assert.equal(box.sourceUnknown({...asset('x'), source_unknown: false, origin_label: null}), false);
  assert.equal(box.sourceUnknown(asset('x')), false);
});

test('C4-AC5b 移入项目发送该条目 asset_version 作为 expected_asset_version（不用库级 revision）', async () => {
  const h = harness([{url: INBOX, data: inbox(70, [asset('a0', {}, 9), asset('a1', {}, 3)])}, {url: MOVE, expected: 3, data: {moved: true}}, {url: INBOX, data: inbox(71, [asset('a0', {}, 9)])}]);
  await h.inbox.load();
  assert.equal(await h.inbox.moveToProject('a1', 'prj-1'), 'moved');
  assert.deepEqual(JSON.parse(h.requests[1].body), {project_id: 'prj-1', expected_asset_version: 3});
  assert.equal(h.inbox.snapshot.total, 1);
});

test('C4-AC5b 409 资产版本冲突刷新该条目后按最新 asset_version 重试一次', async () => {
  const conflict = {detail: {code: 'VERSION_CONFLICT', message: '资产版本冲突', asset_id: 'a1', expected_version: 1, current_version: 2}};
  const h = harness([{url: INBOX, data: inbox(1, [asset('a1', {}, 1)])}, {url: MOVE, expected: 1, status: 409, data: conflict},
    {url: INBOX, data: inbox(9, [asset('a1', {}, 2)])}, {url: MOVE, expected: 2, data: {moved: true}}, {url: INBOX, data: inbox(10, [])}]);
  await h.inbox.load();
  assert.equal(await h.inbox.moveToProject('a1', 'prj-1'), 'moved');
  assert.equal(h.plan.length, 0);
});

test('C4-AC5b 重试仍冲突只重试一次并提示', async () => {
  const conflict = {detail: {code: 'VERSION_CONFLICT', message: 'c', asset_id: 'a1'}};
  const h = harness([{url: INBOX, data: inbox(1, [asset('a1', {}, 1)])}, {url: MOVE, expected: 1, status: 409, data: conflict},
    {url: INBOX, data: inbox(2, [asset('a1', {}, 2)])}, {url: MOVE, expected: 2, status: 409, data: conflict}, {url: INBOX, data: inbox(3, [asset('a1', {}, 3)])}]);
  await h.inbox.load();
  assert.equal(await h.inbox.moveToProject('a1', 'prj-1'), 'conflict');
  assert.equal(h.requests.filter(r => r.url === MOVE).length, 2);
});

test('C4-AC5b 已被归属（409 ASSET_ALREADY_ASSIGNED）不重试，刷新列表', async () => {
  const h = harness([{url: INBOX, data: inbox(1, [asset('a1')])}, {url: MOVE, status: 409, data: {detail: {code: 'ASSET_ALREADY_ASSIGNED', message: 'x'}}},
    {url: INBOX, data: inbox(2, [])}]);
  await h.inbox.load();
  assert.equal(await h.inbox.moveToProject('a1', 'prj-1'), 'already_assigned');
  assert.equal(h.requests.filter(r => r.url === MOVE).length, 1);
});

test('C4-AC5b 匿名 401 不显示入口，所有者显示数量', async () => {
  const count = {textContent: ''};
  const button = {style: {display: 'none'}, querySelector: () => count};
  const anon = harness([{url: INBOX, status: 401, data: {detail: {code: 'UNAUTHENTICATED', message: 'x'}}}], {btnUnassignedInbox: button});
  // 构造时 rebind 已触发一次 refreshEntry；等待其完成。
  await new Promise(r => setImmediate(r));
  assert.equal(button.style.display, 'none');
  const owner = harness([{url: INBOX, data: inbox(1, [asset('a1'), asset('a2')])}], {btnUnassignedInbox: button});
  await new Promise(r => setImmediate(r));
  assert.equal(button.style.display, '');
  assert.equal(count.textContent, '2');
  assert.equal(anon.plan.length + owner.plan.length, 0);
});
