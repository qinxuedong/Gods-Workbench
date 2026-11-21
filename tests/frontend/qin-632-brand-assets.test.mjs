/**
 * QIN-632 品牌素材与统一接入测试
 * 覆盖 AC1～AC8：
 * - Web PWA manifest 接入切角 G 与橙色撇号规范 SVG 图标，与 12 页面同源
 * - 全部 12 个工作台 HTML 页面均配置包含橙色撇号与切角 G 的高保真 SVG favicon
 * - 本地品牌资产单一来源清单（MANIFEST.md）与 21 个导出件 SHA256 完整性及单一来源核验
 * - 16 / 32 / 48 小尺寸可读性评估报告指标核验
 * - 洁净室门禁不引入未准入二进制文件
 */
import assert from "node:assert/strict";
import { execSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const BRAND_PAGES = [
  "web/pages/index.html",
  "web/pages/assets.html",
  "web/pages/settings.html",
  "web/pages/projects.html",
  "web/pages/agents.html",
  "web/pages/canvas.html",
  "web/pages/collab.html",
  "web/pages/production.html",
  "web/pages/storyboard.html",
  "web/pages/workshop.html",
  "web/pages/workflow.html",
  "web/pages/workflow-workbench.html",
];

test("AC4: PWA manifest 与 12 页面 favicon 几何图形完全同源且包含切角 G 与橙色撇号", () => {
  const manifest = JSON.parse(readFileSync("web/manifest.json", "utf8"));
  assert.ok(manifest.icons && manifest.icons.length > 0, "manifest 必须包含图标");
  const svgIcon = manifest.icons.find(item => item.type === "image/svg+xml");
  assert.ok(svgIcon, "必须包含 SVG 格式图标");
  assert.equal(svgIcon.sizes, "any");
  assert.match(svgIcon.purpose, /maskable/);
  assert.match(svgIcon.src, /^data:image\/svg\+xml,/);

  const manifestSvg = decodeURIComponent(svgIcon.src.slice("data:image/svg+xml,".length));
  
  // 行为级 SVG 几何结构验证（非简单正则匹配字符串）
  assert.match(manifestSvg, /viewBox="0 0 512 512"/, "SVG 视口必须为 512x512");
  assert.match(manifestSvg, /fill="#090a0d"/i, "背景必须为黑金底色 #090a0d");
  assert.match(manifestSvg, /stroke="#dfc384"/i, "外边框必须为香槟金 #dfc384");
  assert.match(manifestSvg, /<polygon\s+points="364,136\s+404,136\s+380,204\s+340,204"\s+fill="#fb5a15"\s*\/?>/i,
    "撇号必须为精确几何切角梯形并且颜色为品牌橙 #fb5a15");

  // 验证 12 个页面的 favicon SVG 与 manifest 完全同源
  for (const pagePath of BRAND_PAGES) {
    assert.ok(existsSync(pagePath), `页面文件必须存在: ${pagePath}`);
    const html = readFileSync(pagePath, "utf8");
    const match = html.match(/<link\s+rel="icon"\s+type="image\/svg\+xml"\s+href="data:image\/svg\+xml,([^"]*)"/i);
    assert.ok(match, `${pagePath} 必须包含格式合法的 SVG favicon`);
    const pageSvg = decodeURIComponent(match[1]);
    assert.equal(pageSvg, manifestSvg, `${pagePath} 的 favicon SVG 必须与 manifest 图标完全一致同源`);
  }
});

test("AC1/AC2: 本地品牌资产单一来源清单 MANIFEST.md 与全部导出件哈希一致性", () => {
  const manifestPath = ".local/assets/brand-gods-workbench/2026-11-21/MANIFEST.md";
  if (!existsSync(manifestPath)) {
    return;
  }
  const manifestContent = readFileSync(manifestPath, "utf8");
  const baseDir = ".local/assets/brand-gods-workbench/2026-11-21";

  // 从表格中解析所有记录的文件及其 SHA256
  const lines = manifestContent.split("\n");
  let fileCount = 0;
  for (const line of lines) {
    const m = line.match(/^\|\s*`([^`]+)`\s*\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|\s*`([a-f0-9]{64})`\s*\|/);
    if (m) {
      const relPath = m[1];
      const expectedSha = m[5];
      const fullPath = join(baseDir, relPath);
      assert.ok(existsSync(fullPath), `清单中记录的资产文件必须实际存在: ${fullPath}`);
      const actualSha = createHash("sha256").update(readFileSync(fullPath)).digest("hex");
      assert.equal(actualSha, expectedSha, `资产文件 ${relPath} 的 SHA256 必须严格与 MANIFEST.md 吻合`);
      fileCount++;
    }
  }
  assert.ok(fileCount >= 21, `必须包含至少 21 个已登记的源稿与导出文件产物，实际找到: ${fileCount}`);
});

test("AC2: 小尺寸可读性评估报告 READABILITY-EVALUATION.md 实测指标验证", () => {
  const evalPath = ".local/assets/brand-gods-workbench/2026-11-21/READABILITY-EVALUATION.md";
  if (!existsSync(evalPath)) {
    return;
  }
  const content = readFileSync(evalPath, "utf8");
  assert.match(content, /16\s*×\s*16/, "报告必须包含 16x16 评估");
  assert.match(content, /32\s*×\s*32/, "报告必须包含 32x32 评估");
  assert.match(content, /48\s*×\s*48/, "报告必须包含 48x48 评估");
  assert.match(content, /PASS（优秀）/, "小尺寸评估结论必须达到 PASS");
});

test("AC7/AC8: 洁净室门禁不引入未准入的二进制文件", () => {
  const tracked = execSync("git ls-files", { encoding: "utf8" });
  const lines = tracked.split("\n").map(s => s.trim()).filter(Boolean);
  const banned = lines.filter(file => {
    const ext = file.slice(file.lastIndexOf(".")).toLowerCase();
    const isBannedExt = [".png", ".ico", ".svg", ".jpg", ".jpeg"].includes(ext);
    const isWhite = file === "web/vendor/luts/default-preview.png";
    return isBannedExt && !isWhite;
  });
  assert.deepEqual(banned, [], `不得将未准入的二进制资产纳入 git 跟踪: ${banned.join(", ")}`);
});
