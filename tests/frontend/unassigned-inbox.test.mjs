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
      if (step.expected !== undefined) assert.equal(JSON.parse(options.body).expected_version, step.expected);
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
const asset = (id, meta = {}) => ({asset_id: id, name: id, metadata: meta});
const inbox = (revision, assets) => ({assets, items: assets, total: assets.length, revision});

test('C4-AC5b 来源未知标记只给无工作流/任务/地址来源的资产', () => {
  const {inbox: box} = harness([]);
  assert.equal(box.sourceUnknown(asset('x', {workflow_owner: 'o'})), true);
  assert.equal(box.sourceUnknown(asset('x', {workflow_id: 'w'})), false);
  assert.equal(box.sourceUnknown({...asset('x'), task_id: 't'}), false);
  assert.equal(box.sourceUnknown({...asset('x'), url: '/static/pages/workflow.html?id=w'}), false);
});

test('C4-AC5b 移入项目使用收件箱 revision 作为 expected_version', async () => {
  const h = harness([{url: INBOX, data: inbox(7, [asset('a1')])}, {url: MOVE, expected: 7, data: {moved: true}}, {url: INBOX, data: inbox(8, [])}]);
  await h.inbox.load();
  assert.equal(await h.inbox.moveToProject('a1', 'prj-1'), 'moved');
  assert.deepEqual(JSON.parse(h.requests[1].body), {project_id: 'prj-1', expected_version: 7});
  assert.equal(h.inbox.snapshot.total, 0);
});

test('C4-AC5b 409 版本冲突刷新后按最新 revision 重试一次', async () => {
  const conflict = {detail: {code: 'VERSION_CONFLICT', message: '注册表版本冲突'}};
  const h = harness([{url: INBOX, data: inbox(1, [asset('a1')])}, {url: MOVE, expected: 1, status: 409, data: conflict},
    {url: INBOX, data: inbox(5, [asset('a1')])}, {url: MOVE, expected: 5, data: {moved: true}}, {url: INBOX, data: inbox(6, [])}]);
  await h.inbox.load();
  assert.equal(await h.inbox.moveToProject('a1', 'prj-1'), 'moved');
  assert.equal(h.plan.length, 0);
});

test('C4-AC5b 重试仍冲突只重试一次并提示', async () => {
  const conflict = {detail: {code: 'VERSION_CONFLICT', message: 'c'}};
  const h = harness([{url: INBOX, data: inbox(1, [asset('a1')])}, {url: MOVE, status: 409, data: conflict},
    {url: INBOX, data: inbox(2, [asset('a1')])}, {url: MOVE, expected: 2, status: 409, data: conflict}, {url: INBOX, data: inbox(3, [asset('a1')])}]);
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
