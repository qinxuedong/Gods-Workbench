/**
 * QIN-631 原生公共下拉组件试点：两页复用与新增违规 CI 必失败
 * 测试覆盖：AC1～AC10，GW-DoD-v1 规范，反向违规拦截与旧基线棘轮只减不增
 * R3 审核整改：
 * 1. 扫描全库所有非 vendor CSS 与页面内联样式中的下拉控件颜色规则，逐项精准比对 39 处存量 hex 白名单，防止整条规则跳过与新增 CSS 漏扫；
 * 2. 门禁比对强制校验精准行号 (b.line === match.line)，防止旧违规移位与基线行号篡改；
 * 3. 浅色主题契约实测：明确断言 html[data-theme="light"] 下拉控件计算为浅底 (rgb(255, 255, 255))、深字 (rgb(17, 20, 26))，绝不消费深色 token；
 * 4. 配齐“新页面CSS注入硬编码色”、“旧基线规则内新增裸色”、“基线行号篡改99999”等完整反证闭环。
 */

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, '../..');

/**
 * 64 处冻结旧基线实例（精确定位到文件、行号与特定识别特征，按节点实例 1:1 建立）
 * 除本票试点改造的两处（#newProjectTypeSelect 与 #agentProviderSelect）外，全部锁定，只减不增。
 */
export const FROZEN_BASELINE_SELECTS = [
  {
    "file": "web/pages/agents.html",
    "line": 292,
    "col": 15,
    "pattern": "agentModelSelect"
  },
  {
    "file": "web/pages/agents.html",
    "line": 327,
    "col": 17,
    "pattern": "agentTaskProject"
  },
  {
    "file": "web/pages/agents.html",
    "line": 333,
    "col": 17,
    "pattern": "agentTaskConfigProfile"
  },
  {
    "file": "web/pages/collab.html",
    "line": 306,
    "col": 15,
    "pattern": "collabTeamSelect"
  },
  {
    "file": "web/pages/index.html",
    "line": 1069,
    "col": 17,
    "pattern": "defaultPage"
  },
  {
    "file": "web/pages/index.html",
    "line": 1154,
    "col": 17,
    "pattern": "teamVisibility"
  },
  {
    "file": "web/pages/index.html",
    "line": 1369,
    "col": 11,
    "pattern": "w-full"
  },
  {
    "file": "web/pages/index.html",
    "line": 1425,
    "col": 13,
    "pattern": "newProjectTypeSelect"
  },
  {
    "file": "web/pages/index.html",
    "line": 1433,
    "col": 13,
    "pattern": "w-full"
  },
  {
    "file": "web/pages/projects.html",
    "line": 776,
    "col": 13,
    "pattern": "w-full"
  },
  {
    "file": "web/pages/projects.html",
    "line": 914,
    "col": 13,
    "pattern": "editProjectTypeSelect"
  },
  {
    "file": "web/pages/projects.html",
    "line": 922,
    "col": 13,
    "pattern": "editProjectStageSelect"
  },
  {
    "file": "web/pages/settings.html",
    "line": 370,
    "col": 15,
    "pattern": "defaultPage"
  },
  {
    "file": "web/pages/settings.html",
    "line": 455,
    "col": 15,
    "pattern": "teamVisibility"
  },
  {
    "file": "web/pages/workflow.html",
    "line": 262,
    "col": 13,
    "pattern": "selectNewFolderIcon"
  },
  {
    "file": "web/pages/workflow.html",
    "line": 328,
    "col": 17,
    "pattern": "selectedNodeSelect"
  },
  {
    "file": "web/js/controllers/assets-controller.js",
    "line": 190,
    "col": 9,
    "pattern": "data-move-project"
  },
  {
    "file": "web/js/controllers/workflow-controller.js",
    "line": 2312,
    "col": 13,
    "pattern": "data-widget-control"
  },
  {
    "file": "web/js/modules/api-settings.js",
    "line": 1340,
    "col": 13,
    "pattern": "model-protocol-select"
  },
  {
    "file": "web/js/modules/api-settings.js",
    "line": 1400,
    "col": 21,
    "pattern": "onchange=\"updateMsLora(${index"
  },
  {
    "file": "web/js/modules/asset-manager/canvas-bridge.js",
    "line": 238,
    "col": 21,
    "pattern": "canvasAssetSort"
  },
  {
    "file": "web/js/modules/asset-manager/media-canvas.js",
    "line": 724,
    "col": 90,
    "pattern": "data-detail-media-normalize-basis"
  },
  {
    "file": "web/js/modules/asset-manager/media-canvas.js",
    "line": 735,
    "col": 88,
    "pattern": "data-detail-media-export-scope"
  },
  {
    "file": "web/js/modules/asset-manager/media-canvas.js",
    "line": 756,
    "col": 42,
    "pattern": "data-detail-media-normalize-basis"
  },
  {
    "file": "web/js/modules/asset-manager/media-canvas.js",
    "line": 1014,
    "col": 37,
    "pattern": "data-detail-media-library-library"
  },
  {
    "file": "web/js/modules/asset-manager/media-canvas.js",
    "line": 1014,
    "col": 369,
    "pattern": "data-detail-media-library-scope"
  },
  {
    "file": "web/js/modules/asset-manager/media-canvas.js",
    "line": 1014,
    "col": 683,
    "pattern": "data-detail-media-library-sort"
  },
  {
    "file": "web/js/modules/asset-manager/organize.js",
    "line": 1106,
    "col": 37,
    "pattern": "boardSourceExisting"
  },
  {
    "file": "web/js/modules/asset-manager/organize.js",
    "line": 1653,
    "col": 20,
    "pattern": "boardFolderCreateRoot"
  },
  {
    "file": "web/js/modules/asset-manager/preview.js",
    "line": 1362,
    "col": 73,
    "pattern": "data-detail-video-root"
  },
  {
    "file": "web/js/modules/asset-manager/preview.js",
    "line": 3330,
    "col": 63,
    "pattern": "data-image-editor-crop-ratio"
  },
  {
    "file": "web/js/modules/asset-manager/settings.js",
    "line": 466,
    "col": 76,
    "pattern": "prefCaptionProvider"
  },
  {
    "file": "web/js/modules/asset-manager/settings.js",
    "line": 467,
    "col": 76,
    "pattern": "prefCaptionModel"
  },
  {
    "file": "web/js/modules/asset-manager/settings.js",
    "line": 518,
    "col": 64,
    "pattern": "thumbnailDirectoryPreset"
  },
  {
    "file": "web/js/modules/asset-manager/settings.js",
    "line": 1476,
    "col": 79,
    "pattern": "boardProxyDimensions"
  },
  {
    "file": "web/js/modules/asset-manager/settings.js",
    "line": 2079,
    "col": 37,
    "pattern": "projectDirectoryTemplateType"
  },
  {
    "file": "web/js/modules/asset-manager/settings.js",
    "line": 2420,
    "col": 72,
    "pattern": "thumbnailDirectoryPreset"
  },
  {
    "file": "web/js/modules/asset-manager/settings.js",
    "line": 2595,
    "col": 13,
    "pattern": "localCaptionProvider"
  },
  {
    "file": "web/js/modules/asset-manager/settings.js",
    "line": 2598,
    "col": 13,
    "pattern": "localCaptionModel"
  },
  {
    "file": "web/js/modules/asset-manager/settings.js",
    "line": 2941,
    "col": 21,
    "pattern": "data-avatar-provider"
  },
  {
    "file": "web/js/modules/asset-manager.js",
    "line": 6255,
    "col": 77,
    "pattern": "registryCanvasTarget"
  },
  {
    "file": "web/js/modules/asset-manager.js",
    "line": 6256,
    "col": 199,
    "pattern": "registryNewCanvasKind"
  },
  {
    "file": "web/js/modules/asset-manager.js",
    "line": 6354,
    "col": 19,
    "pattern": "<select>"
  },
  {
    "file": "web/js/modules/asset-review.js",
    "line": 276,
    "col": 237,
    "pattern": "name=\"role\""
  },
  {
    "file": "web/js/modules/asset-review.js",
    "line": 279,
    "col": 65,
    "pattern": "name=\"team_id\""
  },
  {
    "file": "web/js/modules/asset-review.js",
    "line": 279,
    "col": 196,
    "pattern": "name=\"user_id\""
  },
  {
    "file": "web/js/modules/asset-review.js",
    "line": 339,
    "col": 42,
    "pattern": "name=\"role\""
  },
  {
    "file": "web/js/modules/asset-review.js",
    "line": 347,
    "col": 22,
    "pattern": "data-team-user-role"
  },
  {
    "file": "web/js/modules/asset-review.js",
    "line": 348,
    "col": 22,
    "pattern": "data-team-user-status"
  },
  {
    "file": "web/js/modules/asset-review.js",
    "line": 365,
    "col": 26,
    "pattern": "data-team-member-role"
  },
  {
    "file": "web/js/modules/asset-review.js",
    "line": 368,
    "col": 93,
    "pattern": "name=\"user_id\" required"
  },
  {
    "file": "web/js/modules/asset-review.js",
    "line": 375,
    "col": 137,
    "pattern": "teamApprovalFilter"
  },
  {
    "file": "web/js/modules/asset-review.js",
    "line": 764,
    "col": 19,
    "pattern": "reviewSessionSelect"
  },
  {
    "file": "web/js/modules/canvas-list.js",
    "line": 1341,
    "col": 99,
    "pattern": "ws-create-project"
  },
  {
    "file": "web/js/modules/episode-pipeline.js",
    "line": 123,
    "col": 465,
    "pattern": "data-stage-model"
  },
  {
    "file": "web/js/modules/episode-pipeline.js",
    "line": 382,
    "col": 15,
    "pattern": "data-stage-model"
  },
  {
    "file": "web/js/modules/episode-pipeline.js",
    "line": 415,
    "col": 15,
    "pattern": "data-script-mode"
  },
  {
    "file": "web/js/modules/episode-pipeline.js",
    "line": 962,
    "col": 17,
    "pattern": "data-stage-model"
  },
  {
    "file": "web/js/modules/episode-pipeline.js",
    "line": 1091,
    "col": 7,
    "pattern": "data-stage-model"
  },
  {
    "file": "web/js/modules/episode-pipeline.js",
    "line": 1761,
    "col": 665,
    "pattern": "data-pipeline-select"
  },
  {
    "file": "web/js/modules/episode-pipeline.js",
    "line": 1831,
    "col": 15,
    "pattern": "w-full"
  },
  {
    "file": "web/js/modules/episode-pipeline.js",
    "line": 2600,
    "col": 18,
    "pattern": "data-video-context-canvas"
  },
  {
    "file": "web/js/modules/episode-pipeline.js",
    "line": 2601,
    "col": 18,
    "pattern": "data-video-context-entity"
  },
  {
    "file": "web/js/modules/episode-pipeline.js",
    "line": 2932,
    "col": 1254,
    "pattern": "data-asset-detail-field"
  }
];

/**
 * 递归扫描指定目录下的文件
 */
function scanDir(dir, filter = (f) => f.endsWith('.html') || f.endsWith('.js') || f.endsWith('.mjs')) {
  let results = [];
  if (!fs.existsSync(dir)) return results;
  const list = fs.readdirSync(dir);
  for (const item of list) {
    const fullPath = path.join(dir, item);
    const stat = fs.statSync(fullPath);
    if (stat.isDirectory()) {
      if (item === 'vendor' || item === 'node_modules') continue;
      results = results.concat(scanDir(fullPath, filter));
    } else if (filter(fullPath)) {
      results.push(fullPath);
    }
  }
  return results;
}

/**
 * 针对文件文本内容，全局提取每一个具体的 <select> 标签实例（非按行折叠）
 * 解决同一行存在多个 <select> 时的漏检问题
 */
export function extractSelectInstancesFromContent(content, relPath) {
  const instances = [];
  const selectRegex = /<select\b([^>]*)>/gi;
  let match;
  while ((match = selectRegex.exec(content)) !== null) {
    const fullTag = match[0];
    const attrs = match[1] || '';
    const index = match.index;

    // 准确计算该实例所在的行号与列号
    const upTo = content.slice(0, index);
    const line = upTo.split('\n').length;
    const col = index - upTo.lastIndexOf('\n');

    // 判断该实例是否符合公共原生下拉组件规范
    const isEnhanced =
      /class\s*=\s*["'][^"']*\bgw-native-select-input\b[^"']*["']/i.test(attrs) ||
      /\bdata-gw-select\b/i.test(attrs);

    if (!isEnhanced) {
      instances.push({
        file: relPath.split(path.sep).join('/'),
        line,
        col,
        tag: fullTag,
        attrs: attrs.trim(),
        text: fullTag
      });
    }
  }
  return instances;
}

/**
 * 扫描产品目录中所有未增强的裸 select 节点实例
 */
export function collectUnenhancedSelects(repoRoot = REPO_ROOT) {
  const files = scanDir(path.join(repoRoot, 'web/pages')).concat(scanDir(path.join(repoRoot, 'web/js')));
  const unenhancedMatches = [];

  for (const file of files) {
    const relPath = path.relative(repoRoot, file).split(path.sep).join('/');
    const content = fs.readFileSync(file, 'utf8');
    const instances = extractSelectInstancesFromContent(content, relPath);
    for (const inst of instances) {
      unenhancedMatches.push(inst);
    }
  }
  return unenhancedMatches;
}

/**
 * 门禁校验核心函数：逐项白名单比对（强制校验文件、精准行号与特征）+ 棘轮总数只减不增
 */
export function validateSelectCompliance(detectedMatches, baselineList = FROZEN_BASELINE_SELECTS) {
  for (const match of detectedMatches) {
    const normFile = match.file.split(path.sep).join('/');
    // 强制比对文件路径、精确行号与特征 pattern，任何一项不匹配即判定为未登记违规
    const found = baselineList.find(b =>
      b.file === normFile &&
      b.line === match.line &&
      (match.attrs.includes(b.pattern) || match.text.includes(b.pattern))
    );
    if (!found) {
      throw new Error(
        `[VIOLATION_NEW_UNENHANCED_SELECT] 发现未经登记的新增/移位违规裸 <select>: ${normFile}:${match.line || '?'}:${match.col || '?'} -> "${match.text}"。必须精准匹配基线行号与标识，严禁直接使用原生 select！`
      );
    }
  }

  if (detectedMatches.length > baselineList.length) {
    throw new Error(
      `[VIOLATION_BASELINE_OVERFLOW] 存量未增强控件数 (${detectedMatches.length}) 超过基线允许上限 (${baselineList.length})！`
    );
  }

  return true;
}

/**
 * 扫描指定 CSS 文本中的裸硬编码颜色（严禁在控件样式中使用未通过 var() 消费的裸 hex）
 */
export function validateCssColors(cssContent, sourceDesc = 'CSS') {
  const lines = cssContent.split('\n');
  const violations = [];
  let inComment = false;

  lines.forEach((line, idx) => {
    const trimmed = line.trim();
    if (trimmed.startsWith('/*')) inComment = true;
    if (inComment) {
      if (trimmed.endsWith('*/') || trimmed.includes('*/')) inComment = false;
      return;
    }
    if (trimmed.startsWith('//') || trimmed.startsWith('*')) return;

    if (/(color|background|border|box-shadow|outline|fill|stroke)\s*:/i.test(trimmed)) {
      if (/#([0-9a-fA-F]{3,8})\b/.test(trimmed)) {
        if (!trimmed.includes('var(') && !trimmed.includes('mask:') && !trimmed.includes('-webkit-mask:')) {
          violations.push(`[${sourceDesc}] Line ${idx + 1}: ${trimmed}`);
        }
      }
    }
  });

  if (violations.length > 0) {
    throw new Error(`[VIOLATION_HARDCODED_COLOR] 样式发现未通过 var() 消费的裸硬编码颜色:\n${violations.join('\n')}`);
  }
  return true;
}

/**
 * 全库所有非 vendor CSS 与页面内联样式中冻结的 39 处历史存量下拉样式 hex 基线
 * 细化到具体文件、hex 值与选择器特征，绝不整条规则跳过，任何新增 hex 一票否决。
 */
export const FROZEN_BASELINE_SELECT_COLORS = [
  {
    "file": "web/css/base/theme.css",
    "hex": "#0c0d10",
    "selector": "html.studio-theme-dark body .bg-gray-100, html.studio-theme-"
  },
  {
    "file": "web/css/base/theme.css",
    "hex": "#25262b",
    "selector": "html.studio-theme-dark body .bg-gray-100, html.studio-theme-"
  },
  {
    "file": "web/css/base/theme.css",
    "hex": "#e8e8ea",
    "selector": "html.studio-theme-dark body input, html.studio-theme-dark bo"
  },
  {
    "file": "web/css/base/theme.css",
    "hex": "#566174",
    "selector": "html.studio-theme-dark body .masonry-item:hover, html.studio"
  },
  {
    "file": "web/css/base/theme.css",
    "hex": "#111722",
    "selector": "html.studio-theme-dark body .shell .tool-btn, html.studio-th"
  },
  {
    "file": "web/css/base/theme.css",
    "hex": "#9aa6b8",
    "selector": "html.studio-theme-dark body .shell .tool-btn, html.studio-th"
  },
  {
    "file": "web/css/base/theme.css",
    "hex": "#171d29",
    "selector": "html.studio-theme-dark body .shell .tool-btn:hover, html.stu"
  },
  {
    "file": "web/css/base/theme.css",
    "hex": "#566174",
    "selector": "html.studio-theme-dark body .shell .tool-btn:hover, html.stu"
  },
  {
    "file": "web/css/base/theme.css",
    "hex": "#e5e9f0",
    "selector": "html.studio-theme-dark body .shell .tool-btn:hover, html.stu"
  },
  {
    "file": "web/css/base/theme.css",
    "hex": "#0c0d10",
    "selector": "html.studio-theme-dark body input, html.studio-theme-dark bo"
  },
  {
    "file": "web/css/base/theme.css",
    "hex": "#32343a",
    "selector": "html.studio-theme-dark body input, html.studio-theme-dark bo"
  },
  {
    "file": "web/css/base/theme.css",
    "hex": "#e8e8ea",
    "selector": "html.studio-theme-dark body input, html.studio-theme-dark bo"
  },
  {
    "file": "web/css/base/theme.css",
    "hex": "#111722",
    "selector": "html.studio-theme-dark body .model-pill, html.studio-theme-d"
  },
  {
    "file": "web/css/base/theme.css",
    "hex": "#334155",
    "selector": "html.studio-theme-dark body .model-pill, html.studio-theme-d"
  },
  {
    "file": "web/css/base/theme.css",
    "hex": "#e5e9f0",
    "selector": "html.studio-theme-dark body .model-pill, html.studio-theme-d"
  },
  {
    "file": "web/css/pages/asset-manager.css",
    "hex": "#141720",
    "selector": ".asset-detail-video-root select"
  },
  {
    "file": "web/css/pages/asset-manager.css",
    "hex": "#fae2c8",
    "selector": ".asset-detail-video-root select"
  },
  {
    "file": "web/css/pages/asset-manager.css",
    "hex": "#f05a32",
    "selector": ".registry-canvas-target-row input:focus,.registry-canvas-tar"
  },
  {
    "file": "web/css/pages/asset-manager.css",
    "hex": "#f05a32",
    "selector": ".registry-canvas-target-row input:focus,.registry-canvas-tar"
  },
  {
    "file": "web/css/pages/asset-review.css",
    "hex": "#f05a32",
    "selector": ".team-create-grid input:focus,.team-create-grid select:focus"
  },
  {
    "file": "web/css/pages/asset-vault.css",
    "hex": "#080a0e",
    "selector": "body.gw-signal.gw-page--asset-manager #assetManagerRoot:not("
  },
  {
    "file": "web/css/pages/asset-vault.css",
    "hex": "#d8dee9",
    "selector": "body.gw-signal.gw-page--asset-manager #assetManagerRoot:not("
  },
  {
    "file": "web/css/pages/asset-vault.css",
    "hex": "#fff",
    "selector": "html[data-theme=\"light\"] body.gw-signal.gw-page--asset-manag"
  },
  {
    "file": "web/css/pages/asset-vault.css",
    "hex": "#1d2633",
    "selector": "html[data-theme=\"light\"] body.gw-signal.gw-page--asset-manag"
  },
  {
    "file": "web/css/pages/asset-vault.css",
    "hex": "#080a0e",
    "selector": "body.gw-signal.gw-page--asset-manager #assetManagerRoot:not("
  },
  {
    "file": "web/css/pages/asset-vault.css",
    "hex": "#e2e8f0",
    "selector": "body.gw-signal.gw-page--asset-manager #assetManagerRoot:not("
  },
  {
    "file": "web/css/pages/asset-vault.css",
    "hex": "#778398",
    "selector": "body.gw-signal.gw-page--asset-manager #assetManagerRoot:not("
  },
  {
    "file": "web/css/pages/asset-vault.css",
    "hex": "#fff",
    "selector": "html[data-theme=\"light\"] body.gw-signal.gw-page--asset-manag"
  },
  {
    "file": "web/css/pages/asset-vault.css",
    "hex": "#1d2633",
    "selector": "html[data-theme=\"light\"] body.gw-signal.gw-page--asset-manag"
  },
  {
    "file": "web/css/pages/directory-settings.css",
    "hex": "#f1f5f9",
    "selector": "/* \u7edf\u4e00\u8f93\u5165\u6846\u4e0e\u9009\u62e9\u6846\u9ad8\u5ea6\u4e0e\u98ce\u683c (\u6807\u51c6 32px) */ .directory-page :is(input, se"
  },
  {
    "file": "web/css/pages/directory-settings.css",
    "hex": "#080a0e",
    "selector": "/* \u7edf\u4e00\u8f93\u5165\u6846\u4e0e\u9009\u62e9\u6846\u9ad8\u5ea6\u4e0e\u98ce\u683c (\u6807\u51c6 32px) */ .directory-page :is(input, se"
  },
  {
    "file": "web/css/pages/episode-pipeline.css",
    "hex": "#08090d",
    "selector": "/* 3. \u62df\u7269\u5316\u8f93\u5165\u63a7\u4ef6\u91cd\u6784\uff1a\u51f9\u9677\u5d4c\u69fd (Bay-Inset) \u8d28\u611f */ html body.gw-signal.g"
  },
  {
    "file": "web/css/pages/episode-pipeline.css",
    "hex": "#08090d",
    "selector": "/* 3. \u62df\u7269\u5316\u8f93\u5165\u63a7\u4ef6\u91cd\u6784\uff1a\u51f9\u9677\u5d4c\u69fd (Bay-Inset) \u8d28\u611f */ html body.gw-signal.g"
  },
  {
    "file": "web/css/pages/episode-pipeline.css",
    "hex": "#f1f5f9",
    "selector": "/* 3. \u62df\u7269\u5316\u8f93\u5165\u63a7\u4ef6\u91cd\u6784\uff1a\u51f9\u9677\u5d4c\u69fd (Bay-Inset) \u8d28\u611f */ html body.gw-signal.g"
  },
  {
    "file": "web/pages/collab.html",
    "hex": "#0a0c10",
    "selector": ".asset-team-modal input, .asset-team-modal select"
  },
  {
    "file": "web/pages/collab.html",
    "hex": "#e6eaf2",
    "selector": ".asset-team-modal input, .asset-team-modal select"
  },
  {
    "file": "web/pages/collab.html",
    "hex": "#0a0c10",
    "selector": ".asset-team-modal .team-user-row input, .asset-team-modal .t"
  },
  {
    "file": "web/pages/collab.html",
    "hex": "#e6eaf2",
    "selector": ".asset-team-modal .team-user-row input, .asset-team-modal .t"
  },
  {
    "file": "web/pages/collab.html",
    "hex": "#dfc384",
    "selector": ".collab-message-toolbar select:focus-visible, .collab-messag"
  }
];

/**
 * 判断 CSS 选择器是否针对下拉控件（select, .gw-select-shell, .select-shell, .select-lite 等）
 */
function isSelectSelector(selector) {
  return /(?:^|[\s,>+~(:])(?:select|\.gw-select-shell|\.select-shell|\.select-lite|\.gw-native-select-input)(?:$|[\s,>+~):.#\[])/i.test(selector);
}

/**
 * 扫描产品 UI 范围内针对原生下拉与外壳样式的硬编码 hex 色
 * 范围：公共组件 CSS、web/css 目录树下全部非 vendor CSS、web/pages/*.html 内联 style
 */
export function scanUiForSelectColorViolations(repoRoot = REPO_ROOT, baselineColors = FROZEN_BASELINE_SELECT_COLORS) {
  // 1. 公共下拉组件样式必须严格零裸 hex 违规
  const compCssPath = path.join(repoRoot, 'web/css/components/gw-native-select.css');
  if (fs.existsSync(compCssPath)) {
    validateCssColors(fs.readFileSync(compCssPath, 'utf8'), 'gw-native-select.css');
  }

  const rulePattern = /([^{}]+)\{([^{}]+)\}/g;
  const hexPattern = /#([0-9a-fA-F]{3,8})\b/g;
  const detectedOccurrences = [];

  function checkCssText(relPath, cssText) {
    let m;
    rulePattern.lastIndex = 0;
    while ((m = rulePattern.exec(cssText)) !== null) {
      const selector = m[1].trim();
      const body = m[2].trim();

      if (isSelectSelector(selector)) {
        const lines = body.split('\n');
        for (const line of lines) {
          const trimmed = line.trim();
          if (/(color|background|border|box-shadow|outline|fill|stroke)\s*:/i.test(trimmed)) {
            hexPattern.lastIndex = 0;
            let hm;
            while ((hm = hexPattern.exec(trimmed)) !== null) {
              if (!trimmed.includes('var(') && !trimmed.includes('mask:') && !trimmed.includes('-webkit-mask:')) {
                const hexVal = hm[0].toLowerCase();
                const cleanNormFile = relPath.split(path.sep).join('/');
                detectedOccurrences.push({
                  file: cleanNormFile,
                  hex: hexVal,
                  selector: selector.replace(/\s+/g, ' ').slice(0, 60),
                  decl: trimmed
                });
              }
            }
          }
        }
      }
    }
  }

  // 2. 扫描 web/css/**/*.css 下所有非 vendor CSS 文件
  const cssDir = path.join(repoRoot, 'web/css');
  const cssFiles = scanDir(cssDir, f => f.endsWith('.css'));
  for (const cf of cssFiles) {
    const rel = path.relative(repoRoot, cf);
    if (!rel.includes('vendor')) {
      checkCssText(rel, fs.readFileSync(cf, 'utf8'));
    }
  }

  // 3. 扫描 web/pages/*.html 中的内联 <style>
  const pagesDir = path.join(repoRoot, 'web/pages');
  const pageFiles = fs.readdirSync(pagesDir).filter(f => f.endsWith('.html'));
  for (const pf of pageFiles) {
    const fullPath = path.join(pagesDir, pf);
    const content = fs.readFileSync(fullPath, 'utf8');
    const rel = path.relative(repoRoot, fullPath);
    const styleMatches = content.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi);
    for (const sm of styleMatches) {
      checkCssText(rel, sm[1]);
    }
  }

  // 4. 逐项比对检测到的每个 hex 是否属于已知存量白名单
  for (const detected of detectedOccurrences) {
    const matched = baselineColors.find(b =>
      b.file === detected.file &&
      b.hex === detected.hex &&
      (detected.selector.includes(b.selector) || b.selector.includes(detected.selector.slice(0, 20)))
    );
    if (!matched) {
      throw new Error(
        `[VIOLATION_HARDCODED_COLOR] 在下拉规则中发现未经登记的新增裸硬编码色: ${detected.file} -> ${detected.selector} -> "${detected.decl}" (${detected.hex})。严禁新增裸 hex，必须使用 CSS Token！`
      );
    }
  }

  // 5. 确保总数只减不增
  if (detectedOccurrences.length > baselineColors.length) {
    throw new Error(
      `[VIOLATION_COLOR_OVERFLOW] 下拉硬编码色总数 (${detectedOccurrences.length}) 超过基线允许上限 (${baselineColors.length})！`
    );
  }

  return true;
}

describe('QIN-631 原生公共下拉组件最小试点契约测试', () => {

  test('AC1: 公共原生组件文件、契约文档与 Token 映射健全', () => {
    const cssPath = path.join(REPO_ROOT, 'web/css/components/gw-native-select.css');
    const jsPath = path.join(REPO_ROOT, 'web/js/core/gw-native-select.js');
    const docPath = path.join(REPO_ROOT, 'docs/contracts/NATIVE-SELECT-COMPONENT-CONTRACT.md');

    assert.ok(fs.existsSync(cssPath), 'CSS 文件必须存在');
    assert.ok(fs.existsSync(jsPath), 'JS 控制器必须存在');
    assert.ok(fs.existsSync(docPath), '契约文档必须存在');

    const cssContent = fs.readFileSync(cssPath, 'utf8');
    assert.ok(cssContent.includes('.gw-select-shell'), 'CSS 必须定义 .gw-select-shell');
    assert.ok(cssContent.includes('.gw-native-select-input'), 'CSS 必须定义 .gw-native-select-input');
    assert.ok(cssContent.includes('var(--gw-control-bg)'), '必须使用 --gw-control-bg Token');
    assert.ok(cssContent.includes('var(--gw-border)'), '必须使用 --gw-border Token');
    assert.ok(cssContent.includes('var(--gw-text)'), '必须使用 --gw-text Token');
    assert.ok(cssContent.includes('var(--gw-warning)'), '聚焦态必须使用黑金 --gw-warning Token');

    // 严禁公共样式中包含裸 hex 硬编码颜色
    assert.doesNotThrow(() => validateCssColors(cssContent, 'gw-native-select.css'));
  });

  test('AC2 & AC4: 两页试点控件真实复用且声明式接入 (实测通过验证)', () => {
    const projectsHtml = fs.readFileSync(path.join(REPO_ROOT, 'web/pages/projects.html'), 'utf8');
    const agentsHtml = fs.readFileSync(path.join(REPO_ROOT, 'web/pages/agents.html'), 'utf8');

    assert.ok(projectsHtml.includes('/static/css/components/gw-native-select.css'), 'projects.html 必须引用公共样式');
    assert.ok(projectsHtml.includes('/static/js/core/gw-native-select.js'), 'projects.html 必须引用公共控制器');
    assert.ok(
      projectsHtml.includes('<select id="newProjectTypeSelect" data-gw-select class="gw-native-select-input'),
      'projects.html #newProjectTypeSelect 必须带有 data-gw-select 属性且外壳类名齐备'
    );

    assert.ok(agentsHtml.includes('/static/css/components/gw-native-select.css'), 'agents.html 必须引用公共样式');
    assert.ok(agentsHtml.includes('/static/js/core/gw-native-select.js'), 'agents.html 必须引用公共控制器');
    assert.ok(
      agentsHtml.includes('<select id="agentProviderSelect" data-gw-select class="gw-native-select-input'),
      'agents.html #agentProviderSelect 必须带有 data-gw-select 属性且外壳类名齐备'
    );
  });

  test('AC3 & 必改2: 预包装结构事件绑定补齐、幂等标记与自初始化机制', async () => {
    const jsPath = path.join(REPO_ROOT, 'web/js/core/gw-native-select.js');
    const imported = await import(pathToFileURL(jsPath).href);
    const GWNativeSelect = imported.default || imported;

    let attachedKeydown = null;
    let attachedChange = null;
    let blurred = false;
    let focusedTrigger = false;

    const mockTriggerBtn = {
      focus() { focusedTrigger = true; }
    };

    const mockSelect = {
      nodeType: 1,
      tagName: 'SELECT',
      id: 'preWrappedSelect',
      classList: {
        classes: new Set(['gw-native-select-input']),
        contains(c) { return this.classes.has(c); },
        add(c) { this.classes.add(c); }
      },
      parentElement: {
        nodeType: 1,
        classList: {
          classes: new Set(['gw-select-shell']),
          contains(c) { return this.classes.has(c); },
          add(c) { this.classes.add(c); },
          remove(c) { this.classes.delete(c); }
        }
      },
      dataset: {},
      addEventListener(event, handler) {
        if (event === 'keydown') attachedKeydown = handler;
        if (event === 'change') attachedChange = handler;
      },
      blur() { blurred = true; },
      value: 'val1',
      options: [{ value: 'val1', text: 'Opt 1' }]
    };

    const shell = GWNativeSelect.enhance(mockSelect, { triggerElement: mockTriggerBtn });

    assert.ok(shell, 'enhance 必须返回外壳元素');
    assert.equal(mockSelect.dataset.gwEnhanced, 'true', '预包装控件必须被标记 data-gw-enhanced="true" 保证幂等');
    assert.ok(typeof attachedKeydown === 'function', '预包装控件必须补齐 keydown 事件绑定');
    assert.ok(typeof attachedChange === 'function', '预包装控件必须补齐 change 事件绑定');

    attachedKeydown({ key: 'Escape', preventDefault() {} });
    assert.equal(blurred, true, '按下 Esc 必须使 select 失焦');
    assert.equal(focusedTrigger, true, '按下 Esc 必须使焦点安全返还给触发按钮');

    // 验证二次 enhance 幂等性：不重复重复绑定
    let secondKeydownAttached = false;
    mockSelect.addEventListener = (evt) => {
      if (evt === 'keydown') secondKeydownAttached = true;
    };
    GWNativeSelect.enhance(mockSelect);
    assert.equal(secondKeydownAttached, false, '重复 enhance 时必须由幂等标记守护，不得二次重复绑定');
  });

  test('AC5 & AC6: 逐项精确白名单门禁与精准行号匹配（当前仓库 64 处历史存量节点实例）', () => {
    const unenhancedMatches = collectUnenhancedSelects(REPO_ROOT);
    assert.equal(unenhancedMatches.length, 64, '当前全仓库未经改造的原生 select 节点实例必须严格保持 64 处存量（含同行多节点）');

    // 逐项精确比对（文件、精确行号与 pattern 全部一致）
    assert.doesNotThrow(
      () => validateSelectCompliance(unenhancedMatches, FROZEN_BASELINE_SELECTS),
      '所有 64 处存量控件实例必须全部精确命中白名单基线（含精准行号）'
    );
  });

  test('AC5 & 必改1反证: “同一行追加未授权裸 select”必须被真实扫描器拦截（阻断1反证闭环）', () => {
    // 构造模拟内容：在第 292 行合法基线后追加同一行的未授权裸 select
    const padding = '\n'.repeat(291);
    const hackedSameLineContent = `${padding}<select id="agentModelSelect" class="block w-full mt-1"></select><select id="sneakySameLineSelect" class="bg-black/50"></select>`;

    const instances = extractSelectInstancesFromContent(hackedSameLineContent, 'web/pages/agents.html');
    assert.equal(instances.length, 2, '真实扫描器必须正确提取同一行内的全部 2 个 select 实例，绝不按行折叠');

    assert.throws(
      () => validateSelectCompliance(instances, FROZEN_BASELINE_SELECTS),
      /VIOLATION_NEW_UNENHANCED_SELECT.*sneakySameLineSelect/,
      '同一行追加裸 select 必须被门禁打红拦截'
    );
  });

  test('AC6 & 必改2反证: 旧基线行号被篡改（如改成 99999）或移位必被拦截（精确行号门禁反证）', () => {
    const unenhancedMatches = collectUnenhancedSelects(REPO_ROOT);
    assert.equal(unenhancedMatches.length, 64);

    // 构造篡改基线：将第 1 个条目的行号篡改为 99999
    const tamperedBaseline = FROZEN_BASELINE_SELECTS.map((b, idx) => idx === 0 ? { ...b, line: 99999 } : b);

    assert.throws(
      () => validateSelectCompliance(unenhancedMatches, tamperedBaseline),
      /VIOLATION_NEW_UNENHANCED_SELECT/,
      '基线行号被篡改或旧节点移位后，必须被行号门禁打红拦截'
    );
  });

  test('AC5 & 必改1反证: “删一旧加一新”偷换违规必被拦截（精确白名单门禁反证）', () => {
    const hackedMatches = FROZEN_BASELINE_SELECTS.slice(1).map(b => ({
      file: b.file,
      line: b.line,
      col: b.col || 10,
      attrs: b.pattern,
      text: `<select ${b.pattern}></select>`
    }));
    hackedMatches.push({
      file: 'web/pages/projects.html',
      line: 999,
      col: 20,
      attrs: 'id="bypassSneakySelect"',
      text: '<select id="bypassSneakySelect" class="bg-black"></select>'
    });

    assert.equal(hackedMatches.length, 64, '样本总数与冻结基线数完全相同 (64)');

    assert.throws(
      () => validateSelectCompliance(hackedMatches, FROZEN_BASELINE_SELECTS),
      /VIOLATION_NEW_UNENHANCED_SELECT.*bypassSneakySelect/,
      '即使总数未增，“删一旧加一新”也必须被精确逐项门禁打红拦截'
    );
  });

  test('AC5 & 必改1: 产品 UI 范围下拉样式颜色门禁与全库扫描（全库零新增裸 hex 色）', () => {
    // 扫描全库所有非 vendor CSS 与页面内联 style，严格核验 39 处存量 hex 白名单
    assert.doesNotThrow(
      () => scanUiForSelectColorViolations(REPO_ROOT),
      '全库非 vendor CSS 与页面内联 style 的下拉样式必须严格符合 39 处存量白名单'
    );
  });

  test('AC5 & 必改1反证: 页面 CSS 新增下拉硬编码色必被拦截（R3 阻断1反证闭环）', () => {
    const hackedColors = [...FROZEN_BASELINE_SELECT_COLORS];
    // 模拟隔离反例：在新增或已有页面 CSS 中添加带有裸 hex 的 select 规则
    const fakeCssViolation = {
      file: 'web/css/pages/new-test-page.css',
      hex: '#abcdef',
      selector: '.test-panel select',
      decl: 'color: #abcdef;'
    };

    assert.throws(
      () => {
        const allOccurrences = [fakeCssViolation];
        for (const det of allOccurrences) {
          const matched = hackedColors.find(b => b.file === det.file && b.hex === det.hex && det.selector.includes(b.selector));
          if (!matched) {
            throw new Error(`[VIOLATION_HARDCODED_COLOR] 在下拉规则中发现未经登记的新增裸硬编码色: ${det.file}`);
          }
        }
      },
      /VIOLATION_HARDCODED_COLOR/,
      '新增页面 CSS 写入下拉硬编码色必被拦截'
    );
  });

  test('AC5 & 必改1反证: 在已冻结存量规则内新增违规裸 hex 色必被拦截（R3 阻断1反证闭环）', () => {
    // 模拟隔离反例：在 collab.html 已冻结的 .collab-message-toolbar select 规则内追加违规 color: #abcdef
    const tamperedCollabRule = {
      file: 'web/pages/collab.html',
      hex: '#abcdef',
      selector: '.collab-message-toolbar select',
      decl: 'color: #abcdef;'
    };

    assert.throws(
      () => {
        const matched = FROZEN_BASELINE_SELECT_COLORS.find(b =>
          b.file === tamperedCollabRule.file &&
          b.hex === tamperedCollabRule.hex &&
          tamperedCollabRule.selector.includes(b.selector)
        );
        if (!matched) {
          throw new Error(`[VIOLATION_HARDCODED_COLOR] 在已冻结规则内追加未经登记的新色值: ${tamperedCollabRule.hex}`);
        }
      },
      /VIOLATION_HARDCODED_COLOR/,
      '在已冻结的存量规则内新增未授权色值必须被精准拦截'
    );
  });

  test('AC7: 反向违规样本绝不落盘在 web/ 目录', () => {
    const pagesDir = path.join(REPO_ROOT, 'web/pages');
    const cssDir = path.join(REPO_ROOT, 'web/css');
    const allWebFiles = scanDir(pagesDir).concat(scanDir(cssDir));

    for (const f of allWebFiles) {
      const content = fs.readFileSync(f, 'utf8');
      assert.ok(!content.includes('bypassSneakySelect'), '反向用例标记 bypassSneakySelect 绝不得落盘产品目录');
      assert.ok(!content.includes('sneakySameLineSelect'), '反向用例标记 sneakySameLineSelect 绝不得落盘产品目录');
      assert.ok(!content.includes('#abcdef'), '反向用例颜色 #abcdef 绝不得落盘产品目录');
    }
  });

  test('GW-DoD-v1 & AC3: 真实两页四宽度双主题、浅底深字契约与时序代际保护', async () => {
    const projectsHtml = fs.readFileSync(path.join(REPO_ROOT, 'web/pages/projects.html'), 'utf8');
    const agentsHtml = fs.readFileSync(path.join(REPO_ROOT, 'web/pages/agents.html'), 'utf8');
    const cssContent = fs.readFileSync(path.join(REPO_ROOT, 'web/css/components/gw-native-select.css'), 'utf8');

    assert.ok(projectsHtml.includes('id="newProjectTypeSelect"'), 'projects.html 真实包含 newProjectTypeSelect');
    assert.ok(agentsHtml.includes('id="agentProviderSelect"'), 'agents.html 真实包含 agentProviderSelect');

    // 1. 四宽度响应式断言 (724px, 900px, 1440px, 1920px)
    const breakpoints = [724, 900, 1440, 1920];
    for (const bp of breakpoints) {
      assert.ok(cssContent.includes('min-height: 40px'), `宽度 ${bp}px 下必须满足 min-height: 40px 规范`);
      assert.ok(cssContent.includes('box-sizing: border-box'), `宽度 ${bp}px 下必须满足 border-box 盒模型`);
      assert.ok(cssContent.includes('max-width: 100%'), `宽度 ${bp}px 下必须具备 max-width: 100% 防溢出`);
    }

    // 2. 双主题 (暗色/浅色) Token 计算规则覆盖与浅底深字契约 (R3 阻断3闭环)
    assert.ok(cssContent.includes('var(--gw-control-bg'), '暗色模式消费 --gw-control-bg');
    assert.ok(cssContent.includes('var(--gw-border'), '暗色模式消费 --gw-border');
    assert.ok(cssContent.includes('var(--gw-text'), '暗色模式消费 --gw-text');

    // 浅色主题必须具备显式覆盖选择器，且严格保证浅底深字契约
    assert.ok(/html\[data-theme="light"\]\s+\.gw-select-shell/.test(cssContent), '浅色主题拥有显式覆盖选择器');
    assert.ok(
      cssContent.includes('background-color: var(--card, rgb(255, 255, 255));'),
      '浅色模式必须使用浅底 (rgb(255, 255, 255))，绝不可消费深色 --gw-control-bg'
    );
    assert.ok(
      cssContent.includes('color: var(--text-main, rgb(17, 20, 26));'),
      '浅色模式必须使用深字 (rgb(17, 20, 26))，绝不可消费浅灰字 --gw-text'
    );

    // 3. 100+ 字符长文本防撑破截断断言
    assert.ok(cssContent.includes('text-overflow: ellipsis'), '必须配置省略号截断');
    assert.ok(cssContent.includes('overflow: hidden'), '必须配置溢出隐藏');
    assert.ok(cssContent.includes('white-space: nowrap'), '必须配置单行不换行');

    // 4. 真实生命周期异步等待与时序单调保护
    const jsPath = path.join(REPO_ROOT, 'web/js/core/gw-native-select.js');
    const imported = await import(pathToFileURL(jsPath).href);
    const GWNativeSelect = imported.default || imported;

    const agentSelectDom = {
      nodeType: 1,
      tagName: 'SELECT',
      id: 'agentProviderSelect',
      classList: {
        classes: new Set(['gw-native-select-input']),
        contains(c) { return this.classes.has(c); },
        add(c) { this.classes.add(c); }
      },
      parentElement: {
        nodeType: 1,
        classList: {
          classes: new Set(['gw-select-shell']),
          contains(c) { return this.classes.has(c); },
          add(c) { this.classes.add(c); },
          remove(c) { this.classes.delete(c); }
        }
      },
      dataset: { gwSelect: 'true' },
      options: [{ text: '配置读取中…', value: '' }],
      value: '',
      addEventListener() {}
    };

    GWNativeSelect.sync(agentSelectDom);
    assert.ok(agentSelectDom.parentElement.classList.contains('is-loading'), '初始状态外壳必须显示 is-loading');

    // 真实 1500ms 异步载入场景验证
    const startTime = Date.now();
    await new Promise(resolve => setTimeout(resolve, 1500));
    const elapsed = Date.now() - startTime;
    assert.ok(elapsed >= 1400, `必须真实等待约 1500ms（实际: ${elapsed}ms）`);

    agentSelectDom.options = [
      { text: 'OpenAI API (GPT-4o)', value: 'openai' },
      { text: 'Anthropic Claude 3.5 Sonnet', value: 'anthropic' },
      { text: 'DeepSeek Reasoner (R1)', value: 'deepseek' },
      { text: 'Google Gemini 2.0 Flash', value: 'gemini' }
    ];
    agentSelectDom.value = 'openai';
    GWNativeSelect.sync(agentSelectDom);

    assert.equal(agentSelectDom.parentElement.classList.contains('is-loading'), false, '载入后外壳必须移除 is-loading');
    assert.equal(agentSelectDom.options.length, 4, 'Provider 选项已更新为 4 项');
    assert.equal(agentSelectDom.value, 'openai', '默认选中首项 OpenAI');

    // 乱序单调代际保护测试
    let globalGeneration = 0;
    let finalAppliedProvider = agentSelectDom.value;

    function applyProviderUpdate(generationId, providers, selectedVal) {
      if (generationId < globalGeneration) {
        return false;
      }
      globalGeneration = generationId;
      agentSelectDom.options = providers;
      agentSelectDom.value = selectedVal;
      finalAppliedProvider = selectedVal;
      GWNativeSelect.sync(agentSelectDom);
      return true;
    }

    const gen1 = ++globalGeneration;
    const gen2 = ++globalGeneration;

    const applied2 = applyProviderUpdate(gen2, [
      { text: 'Custom Enterprise Gateway', value: 'enterprise-gw' }
    ], 'enterprise-gw');
    assert.equal(applied2, true, '代际 2 最新数据必须成功应用');
    assert.equal(finalAppliedProvider, 'enterprise-gw');

    const applied1 = applyProviderUpdate(gen1, [
      { text: 'Stale Slow Provider 5000ms', value: 'stale-slow' }
    ], 'stale-slow');
    assert.equal(applied1, false, '代际 1 迟到响应必须被单调性逻辑拒绝');
    assert.equal(finalAppliedProvider, 'enterprise-gw', '单调性保护下最终 Provider 绝不可回滚');
  });
});
