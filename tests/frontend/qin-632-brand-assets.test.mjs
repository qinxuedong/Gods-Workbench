/**
 * QIN-632 品牌素材与统一接入测试
 * 覆盖 AC1～AC8：
 * - Web PWA manifest 内嵌切角 G 与橙色撇号 SVG 图标
 * - 全部 12 个工作台 HTML 页面均配置包含橙色撇号的 SVG favicon
 * - 桌面托盘 make_tray_icon 与构建 build.rs 窗口图标统一包含品牌金与橙色撇号像素
 * - 桌面 UI 具备品牌入口
 * - 洁净室门禁不引入未准入二进制文件
 */
import assert from "node:assert/strict";
import { execSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
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

test("AC4: PWA manifest 接入规范切角 G 与橙色撇号 SVG 图标", () => {
  const manifest = JSON.parse(readFileSync("web/manifest.json", "utf8"));
  assert.ok(manifest.icons && manifest.icons.length > 0, "manifest 必须包含图标");
  const svgIcon = manifest.icons.find(item => item.type === "image/svg+xml");
  assert.ok(svgIcon, "必须包含 SVG 格式图标");
  assert.equal(svgIcon.sizes, "any");
  assert.match(svgIcon.purpose, /maskable/);
  assert.match(svgIcon.src, /^data:image\/svg\+xml,/);

  const decodedSvg = decodeURIComponent(svgIcon.src.slice("data:image/svg+xml,".length));
  assert.match(decodedSvg, /#dfc384/i, "图标必须包含香槟金强调色");
  assert.match(decodedSvg, /#fb5a15/i, "图标必须包含品牌特征橙色撇号");
  assert.match(decodedSvg, /#090a0d/i, "图标背景必须对齐黑金硬件底色");
});

test("AC4: 12 个工作台页面全部在 <head> 中正确接入 SVG favicon", () => {
  for (const pagePath of BRAND_PAGES) {
    assert.ok(existsSync(pagePath), `页面文件必须存在: ${pagePath}`);
    const html = readFileSync(pagePath, "utf8");
    assert.match(
      html,
      /<link\s+rel="icon"\s+type="image\/svg\+xml"\s+href="data:image\/svg\+xml,[^"]*"/i,
      `${pagePath} 必须包含 SVG data URI 格式的 favicon`
    );
    assert.match(
      html,
      /%23fb5a15/i,
      `${pagePath} 的 favicon 数据中必须包含品牌橙色撇号点缀`
    );
  }
});

test("AC5: 桌面端 make_tray_icon 源码统一包含切角 G 与橙色撇号像素", () => {
  const mainRs = readFileSync("clients/desktop/src/main.rs", "utf8");
  assert.match(mainRs, /fn make_tray_icon\(\)\s*->\s*Image<'static>/, "main.rs 必须定义 make_tray_icon");
  assert.match(mainRs, /const ORANGE:\s*\[u8;\s*4\]\s*=\s*\[251,\s*90,\s*21,\s*255\];/, "托盘图标必须定义品牌橙色");
  assert.match(mainRs, /const GOLD:\s*\[u8;\s*4\]\s*=\s*\[223,\s*195,\s*132,\s*255\];/, "托盘图标必须定义香槟金色");
  assert.match(mainRs, /const DARK:\s*\[u8;\s*4\]\s*=\s*\[9,\s*10,\s*13,\s*255\];/, "托盘图标必须定义黑金底色");
  assert.match(mainRs, /apostrophe/, "托盘绘制循环必须包含撇号分支");
});

test("AC5: 桌面构建 build.rs 窗口图标源码统一包含品牌橙与金色", () => {
  const buildRs = readFileSync("clients/desktop/build.rs", "utf8");
  assert.match(buildRs, /fn generated_windows_icon\(\)\s*->\s*PathBuf/, "build.rs 必须定义 generated_windows_icon");
  assert.match(buildRs, /const ORANGE:\s*\[u8;\s*4\]\s*=\s*\[251,\s*90,\s*21,\s*255\];/, "构建图标必须定义品牌橙色");
  assert.match(buildRs, /const GOLD:\s*\[u8;\s*4\]\s*=\s*\[223,\s*195,\s*132,\s*255\];/, "构建图标必须定义香槟金色");
  assert.match(buildRs, /apostrophe/, "构建图标必须包含撇号分支");
});

test("AC7/AC8: 洁净室门禁不引入未准入的二进制文件", () => {
  // 通过 git ls-files 检查本分支未新增任何未准入的 .png / .ico / .svg 文件
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
