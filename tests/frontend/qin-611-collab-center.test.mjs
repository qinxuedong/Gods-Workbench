import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const repoRoot = process.cwd();
const read = (relativePath) =>
  readFileSync(path.join(repoRoot, relativePath), "utf8");

const collabHtml = read("web/pages/collab.html");
const collabController = read("web/js/controllers/collab-controller.js");
const taskCenterHtml = read("web/embeds/task-center.html");
const taskCenterModule = read("web/js/modules/task-center.js");
const pageHtml = ["index.html", "projects.html", "production.html"].map((name) =>
  read(`web/pages/${name}`),
);

test("AC1/AC2: 协作页只有「中心」任务日志入口且不保留排他锁看板", () => {
  assert.match(collabHtml, /<h3[^>]*>中心<\/h3>/);
  assert.match(collabHtml, /id="collabCenterFrame"/);
  assert.doesNotMatch(collabHtml, /工作台入口|工程资源排他锁看板|collabLockBoard/);
  assert.doesNotMatch(collabHtml, /data-collab-view|collabTaskFeed/);
});

test("AC3/AC4/AC6: 中心嵌入现有任务中心并保留真实任务深链", () => {
  assert.match(collabHtml, /\/static\/embeds\/task-center\.html\?embedded=1&amp;view=tasks/);
  assert.match(collabController, /frame\.src = '\/static\/embeds\/task-center\.html\?' \+ params\.toString\(\)/);
  assert.match(collabController, /'project_id', 'entity_id', 'asset_id', 'canvas_id', 'job_id', 'event_id'/);
  assert.match(taskCenterHtml, /\/static\/js\/modules\/task-center\.js/);
  assert.match(taskCenterModule, /\/api\/observability\/tasks/);
  assert.match(taskCenterModule, /\/api\/observability\/events/);
  assert.match(taskCenterModule, /state\.view = 'tasks'/);
  assert.match(taskCenterModule, /state\.view = 'logs'/);
});

test("AC5: 右侧日志来自统一运行事件，不复用左侧认证审计接口", () => {
  assert.match(collabController, /return '\/api\/observability\/events\?' \+ params/);
  assert.match(collabController, /if \(key === 'audit'\) \{/);
  assert.match(collabController, /params\.set\('source', 'audit_logs'\)/);
  assert.match(taskCenterHtml, /仅显示统一投影返回的安全事件摘要/);
});

test("AC7: 首页、项目中心、制片页入口导航到协作中心且不再维护任务抽屉", () => {
  for (const html of pageHtml) {
    assert.doesNotMatch(html, /taskCenterDrawer|workbenchTaskCenterDrawerList/);
  }
  assert.match(pageHtml[0], /\/static\/pages\/collab\.html\?view=tasks/);
  assert.match(pageHtml[1], /\/static\/pages\/collab\.html\?view=tasks/);
});
