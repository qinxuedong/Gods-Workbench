import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const repoRoot = process.cwd();
const read = (relativePath) =>
  readFileSync(path.join(repoRoot, relativePath), "utf8");

const collabHtml = read("web/pages/collab.html");
const collabController = read("web/js/controllers/collab-controller.js");

const panelSpan = (id) => {
  const marker = `id="${id}"`;
  const at = collabHtml.indexOf(marker);
  assert.ok(at >= 0, `collab.html 缺少 #${id}`);
  const start = collabHtml.lastIndexOf("<span", at);
  const end = collabHtml.indexOf("</span>", at);
  assert.ok(start >= 0 && end > start, `#${id} 标签未闭合`);
  return collabHtml.slice(start, end);
};

test("AC1 #collabTimelineSource 不再重复「变更审计」字样", () => {
  const source = panelSpan("collabTimelineSource");
  assert.doesNotMatch(source, /变更审计/);
  // 头部仍如实标注真实数据源，便于排查。
  assert.match(source, /数据源 · \/api\/observability\/events/);
});

test("AC1 面板中 #collabTimelineSource 与 #collabAuditScope 只保留一个「变更审计」标识", () => {
  const source = panelSpan("collabTimelineSource");
  const scope = panelSpan("collabAuditScope");
  const withLabel = [source, scope].filter((span) => span.includes("变更审计"));
  assert.equal(withLabel.length, 1, "两个锚点必须且只有一个保留「变更审计」");
  assert.ok(withLabel[0].includes("collabAuditScope"));
});

test("AC2 范围切换文案保留唯一「变更审计」标识且随当前工程/全局切换", () => {
  assert.match(
    collabController,
    /el\('collabAuditScope'\)\.textContent = \(projectId \? '当前工程' : '全局'\) \+ '变更审计 · 最近 24 小时';/,
  );
  // 时间线加载与状态渲染链路保持不变。
  assert.match(collabController, /el\('collabTimeline'\)\.innerHTML/);
  assert.match(collabController, /el\('collabTimelineState'\)\.textContent/);
  assert.match(collabController, /updateMore\('collabAuditMore', page\)/);
});
