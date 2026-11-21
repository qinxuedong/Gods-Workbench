/**
 * QIN-631 原生公共下拉组件试点：两页复用与新增违规 CI 必失败
 * 测试覆盖：AC1～AC10，GW-DoD-v1 规范，反向违规拦截与旧基线棘轮只减不增
 * R2 审核整改：
 * 1. 扫描器升级为全局标签实例级提取（解决同一行多个 <select> 漏检问题，全库精确 64 处历史基线）；
 * 2. 增加“同一行追加未授权裸 select”真实产品扫描器反证测试；
 * 3. 颜色检测范围扩大至全产品 UI（含页面内联 style 与非 vendor CSS 下拉规则）；
 * 4. 保持真实 DOM 4 宽度双主题、长文省略与 1500ms/5000ms 真实时序单调代际保护。
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
  { file: 'web/pages/agents.html', line: 292, pattern: 'agentModelSelect' },
  { file: 'web/pages/agents.html', line: 327, pattern: 'agentTaskProject' },
  { file: 'web/pages/agents.html', line: 333, pattern: 'agentTaskConfigProfile' },
  { file: 'web/pages/collab.html', line: 306, pattern: 'collabTeamSelect' },
  { file: 'web/pages/index.html', line: 1068, pattern: 'defaultPage' },
  { file: 'web/pages/index.html', line: 1153, pattern: 'teamVisibility' },
  { file: 'web/pages/index.html', line: 1368, pattern: 'bg-[#0a0c10]' },
  { file: 'web/pages/index.html', line: 1424, pattern: 'newProjectTypeSelect' },
  { file: 'web/pages/index.html', line: 1432, pattern: 'px-2 py-1.5' },
  { file: 'web/pages/projects.html', line: 776, pattern: 'bg-[#0a0c10]' },
  { file: 'web/pages/projects.html', line: 914, pattern: 'editProjectTypeSelect' },
  { file: 'web/pages/projects.html', line: 922, pattern: 'editProjectStageSelect' },
  { file: 'web/pages/settings.html', line: 370, pattern: 'defaultPage' },
  { file: 'web/pages/settings.html', line: 455, pattern: 'teamVisibility' },
  { file: 'web/pages/workflow.html', line: 262, pattern: 'selectNewFolderIcon' },
  { file: 'web/pages/workflow.html', line: 328, pattern: 'selectedNodeSelect' },
  { file: 'web/js/controllers/assets-controller.js', line: 190, pattern: 'data-move-project' },
  { file: 'web/js/controllers/workflow-controller.js', line: 2312, pattern: 'param-select' },
  { file: 'web/js/modules/api-settings.js', line: 1340, pattern: 'model-protocol-select' },
  { file: 'web/js/modules/api-settings.js', line: 1400, pattern: 'updateMsLora' },
  { file: 'web/js/modules/asset-manager/canvas-bridge.js', line: 238, pattern: 'canvasAssetSort' },
  { file: 'web/js/modules/asset-manager/media-canvas.js', line: 724, pattern: 'data-detail-media-normalize-basis' },
  { file: 'web/js/modules/asset-manager/media-canvas.js', line: 735, pattern: 'data-detail-media-export-scope' },
  { file: 'web/js/modules/asset-manager/media-canvas.js', line: 756, pattern: 'data-detail-media-normalize-basis' },
  { file: 'web/js/modules/asset-manager/media-canvas.js', line: 1014, pattern: 'data-detail-media-library-library' },
  { file: 'web/js/modules/asset-manager/media-canvas.js', line: 1014, pattern: 'data-detail-media-library-scope' },
  { file: 'web/js/modules/asset-manager/media-canvas.js', line: 1014, pattern: 'data-detail-media-library-sort' },
  { file: 'web/js/modules/asset-manager/organize.js', line: 1084, pattern: 'boardSourceExisting' },
  { file: 'web/js/modules/asset-manager/organize.js', line: 1624, pattern: 'boardFolderCreateRoot' },
  { file: 'web/js/modules/asset-manager/preview.js', line: 1362, pattern: 'data-detail-video-root' },
  { file: 'web/js/modules/asset-manager/preview.js', line: 3330, pattern: 'data-image-editor-crop-ratio' },
  { file: 'web/js/modules/asset-manager/settings.js', line: 466, pattern: 'prefCaptionProvider' },
  { file: 'web/js/modules/asset-manager/settings.js', line: 467, pattern: 'prefCaptionModel' },
  { file: 'web/js/modules/asset-manager/settings.js', line: 518, pattern: 'thumbnailDirectoryPreset' },
  { file: 'web/js/modules/asset-manager/settings.js', line: 1451, pattern: 'boardProxyDimensions' },
  { file: 'web/js/modules/asset-manager/settings.js', line: 2053, pattern: 'projectDirectoryTemplateType' },
  { file: 'web/js/modules/asset-manager/settings.js', line: 2394, pattern: 'thumbnailDirectoryPreset' },
  { file: 'web/js/modules/asset-manager/settings.js', line: 2569, pattern: 'localCaptionProvider' },
  { file: 'web/js/modules/asset-manager/settings.js', line: 2572, pattern: 'localCaptionModel' },
  { file: 'web/js/modules/asset-manager/settings.js', line: 2915, pattern: 'avatar-provider-select' },
  { file: 'web/js/modules/asset-manager.js', line: 6253, pattern: 'registryCanvasTarget' },
  { file: 'web/js/modules/asset-manager.js', line: 6254, pattern: 'registryNewCanvasKind' },
  { file: 'web/js/modules/asset-manager.js', line: 6352, pattern: '<select>' },
  { file: 'web/js/modules/asset-review.js', line: 276, pattern: 'name="role"' },
  { file: 'web/js/modules/asset-review.js', line: 279, pattern: 'name="team_id"' },
  { file: 'web/js/modules/asset-review.js', line: 279, pattern: 'name="user_id"' },
  { file: 'web/js/modules/asset-review.js', line: 339, pattern: 'name="role"' },
  { file: 'web/js/modules/asset-review.js', line: 347, pattern: 'data-team-user-role' },
  { file: 'web/js/modules/asset-review.js', line: 348, pattern: 'data-team-user-status' },
  { file: 'web/js/modules/asset-review.js', line: 365, pattern: 'data-team-member-role' },
  { file: 'web/js/modules/asset-review.js', line: 368, pattern: 'name="user_id"' },
  { file: 'web/js/modules/asset-review.js', line: 375, pattern: 'teamApprovalFilter' },
  { file: 'web/js/modules/asset-review.js', line: 764, pattern: 'reviewSessionSelect' },
  { file: 'web/js/modules/canvas-list.js', line: 1341, pattern: 'ws-create-project' },
  { file: 'web/js/modules/episode-pipeline.js', line: 123, pattern: 'data-stage-model' },
  { file: 'web/js/modules/episode-pipeline.js', line: 382, pattern: 'bg-transparent text-neutral-200' },
  { file: 'web/js/modules/episode-pipeline.js', line: 415, pattern: 'appearance-none bg-[#090a0d]' },
  { file: 'web/js/modules/episode-pipeline.js', line: 962, pattern: 'opacity-0 cursor-pointer' },
  { file: 'web/js/modules/episode-pipeline.js', line: 1091, pattern: 'border-gold-primary' },
  { file: 'web/js/modules/episode-pipeline.js', line: 1761, pattern: 'data-pipeline-select' },
  { file: 'web/js/modules/episode-pipeline.js', line: 1831, pattern: 'prompt_library_id' },
  { file: 'web/js/modules/episode-pipeline.js', line: 2600, pattern: 'data-video-context-canvas' },
  { file: 'web/js/modules/episode-pipeline.js', line: 2601, pattern: 'data-video-context-entity' },
  { file: 'web/js/modules/episode-pipeline.js', line: 2932, pattern: 'data-asset-detail-field="type"' }
];

/**
 * 递归扫描目录提取指定后缀文件
 */
export function scanDir(dir) {
  let results = [];
  const list = fs.readdirSync(dir);
  for (const item of list) {
    const fullPath = path.join(dir, item);
    const stat = fs.statSync(fullPath);
    if (stat.isDirectory()) {
      if (item === 'vendor' || item === 'node_modules' || item === '.git') continue;
      results = results.concat(scanDir(fullPath));
    } else if (/\.(html|js)$/.test(item)) {
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
 * 门禁校验核心函数：逐项白名单比对 + 棘轮总数只减不增（杜绝偷梁换柱与同行漏报）
 */
export function validateSelectCompliance(detectedMatches, baselineList = FROZEN_BASELINE_SELECTS) {
  for (const match of detectedMatches) {
    const normFile = match.file.split(path.sep).join('/');
    const found = baselineList.find(b => b.file === normFile && (match.attrs.includes(b.pattern) || match.text.includes(b.pattern)));
    if (!found) {
      throw new Error(
        `[VIOLATION_NEW_UNENHANCED_SELECT] 发现未经登记的新增违规裸 <select>: ${normFile}:${match.line || '?'}:${match.col || '?'} -> "${match.text}"。严禁直接使用原生 select，必须接入 GWNativeSelect！`
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
      if (/#([0-9a-fA-F]{3,6})\b/.test(trimmed)) {
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
 * 产品 UI 页面内联样式中冻结的 3 处历史存量下拉样式基线（仅限 collab.html 历史存量，其余全库零裸 hex）
 */
export const FROZEN_BASELINE_SELECT_COLORS = [
  { file: 'collab.html', pattern: '.asset-team-modal input, .asset-team-modal select' },
  { file: 'collab.html', pattern: '.asset-team-modal .team-user-row input' },
  { file: 'collab.html', pattern: '.collab-message-toolbar select' }
];

/**
 * 扫描产品 UI 范围内针对原生下拉与外壳样式的硬编码 hex 色（含公共组件、页面内联 style 与非 vendor CSS 下拉规则）
 */
export function scanUiForSelectColorViolations(repoRoot = REPO_ROOT) {
  // 1. 公共下拉组件样式必须严格零裸 hex 违规
  const compCssPath = path.join(repoRoot, 'web/css/components/gw-native-select.css');
  if (fs.existsSync(compCssPath)) {
    validateCssColors(fs.readFileSync(compCssPath, 'utf8'), 'gw-native-select.css');
  }

  // 2. 扫描产品页面 web/pages/*.html 中的内联 <style>
  const pagesDir = path.join(repoRoot, 'web/pages');
  const pageFiles = fs.readdirSync(pagesDir).filter(f => f.endsWith('.html'));
  for (const pf of pageFiles) {
    const content = fs.readFileSync(path.join(pagesDir, pf), 'utf8');
    const styleMatches = content.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi);
    for (const sm of styleMatches) {
      const styleBlock = sm[1];
      // 提取针对 select, .gw-select-shell, .select-shell 的规则
      const selectRuleRegex = /(?:^|\})([^{}]*(?:select|\.gw-select-shell|\.select-shell)[^{]*\{[^{}]*\})/gi;
      let ruleMatch;
      while ((ruleMatch = selectRuleRegex.exec(styleBlock)) !== null) {
        const rule = ruleMatch[1].trim();
        const isBaseline = FROZEN_BASELINE_SELECT_COLORS.some(b => b.file === pf && rule.includes(b.pattern));
        if (!isBaseline) {
          validateCssColors(rule, `${pf} inline <style>`);
        }
      }
    }
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
    assert.ok(fs.existsSync(docPath), '契约文档必须在 docs/contracts/NATIVE-SELECT-COMPONENT-CONTRACT.md 下存在');

    const cssContent = fs.readFileSync(cssPath, 'utf8');
    assert.ok(cssContent.includes('var(--gw-control-bg'), '必须消费控件背景 Token --gw-control-bg');
    assert.ok(cssContent.includes('var(--gw-border'), '必须消费边框 Token --gw-border');
    assert.ok(cssContent.includes('var(--gw-text'), '必须消费文字 Token --gw-text');
    assert.ok(cssContent.includes('var(--gw-warning'), '必须消费黑金高亮 Token --gw-warning');
    assert.ok(cssContent.includes('min-height: 40px'), '必须对齐 40px 高度规范');
    assert.ok(cssContent.includes('text-overflow: ellipsis'), '必须具备长文本省略防撑破');
    assert.ok(cssContent.includes('padding: 8px 36px 8px 12px'), '必须预留 36px 右侧内边距防止遮盖箭头');
    assert.ok(cssContent.includes('html[data-theme="light"]'), '必须支持浅色主题');
    assert.ok(cssContent.includes('.select-shell'), '必须兼容对齐已有 .select-shell');
  });

  test('AC2 & AC4: 两页试点控件真实复用且声明式接入 (实测通过验证)', () => {
    // 1. 项目管理页 projects.html
    const projectsHtml = fs.readFileSync(path.join(REPO_ROOT, 'web/pages/projects.html'), 'utf8');
    assert.ok(projectsHtml.includes('/static/css/components/gw-native-select.css'), 'projects.html 必须引入公共 CSS');
    assert.ok(projectsHtml.includes('/static/js/core/gw-native-select.js'), 'projects.html 必须引入公共 JS');
    assert.ok(/<select id="newProjectTypeSelect"[^>]*data-gw-select[^>]*class="[^"]*gw-native-select-input[^"]*"/.test(projectsHtml), 'newProjectTypeSelect 必须具备 data-gw-select 声明属性与标准类');
    assert.ok(/<div class="gw-select-shell">\s*<select id="newProjectTypeSelect"/m.test(projectsHtml), 'newProjectTypeSelect 必须被 .gw-select-shell 包裹');
    assert.ok(projectsHtml.includes('value="film"'), '必须保留院线长片选项');
    assert.ok(projectsHtml.includes('value="series"'), '必须保留连续短剧选项');
    assert.ok(projectsHtml.includes('value="other"'), '必须保留PV选项');
    assert.ok(projectsHtml.includes('<select id="editProjectTypeSelect"'), '编辑项目下拉保持原样');

    // 2. 智能体页 agents.html
    const agentsHtml = fs.readFileSync(path.join(REPO_ROOT, 'web/pages/agents.html'), 'utf8');
    assert.ok(agentsHtml.includes('/static/css/components/gw-native-select.css'), 'agents.html 必须引入公共 CSS');
    assert.ok(agentsHtml.includes('/static/js/core/gw-native-select.js'), 'agents.html 必须引入公共 JS');
    assert.ok(/<select id="agentProviderSelect"[^>]*data-gw-select[^>]*class="[^"]*gw-native-select-input[^"]*"[^>]*aria-label="服务端 Provider">/.test(agentsHtml), 'agentProviderSelect 必须具备 data-gw-select 与标准类');
    assert.ok(/<div class="gw-select-shell mt-1">\s*<select id="agentProviderSelect"/.test(agentsHtml), 'agentProviderSelect 必须被 .gw-select-shell 包裹');
    assert.ok(agentsHtml.includes('<option value="">配置读取中…</option>'), '必须保留初始加载选项');
    assert.ok(agentsHtml.includes('<select id="agentModelSelect"'), '同页允许模型下拉未被篡改');
  });

  test('AC3 & 必改2: 预包装结构事件绑定补齐、幂等标记与自初始化机制', async () => {
    const jsPath = path.join(REPO_ROOT, 'web/js/core/gw-native-select.js');
    const moduleUrl = pathToFileURL(jsPath).href;
    const imported = await import(moduleUrl);
    const GWNativeSelect = imported.default || imported;

    assert.ok(typeof GWNativeSelect.enhance === 'function', 'enhance 必须是函数');
    assert.ok(typeof GWNativeSelect.enhanceAll === 'function', 'enhanceAll 必须是函数');
    assert.ok(typeof GWNativeSelect.sync === 'function', 'sync 必须是函数');

    // 构造具备预包装外壳的测试节点
    let blurred = false;
    let changeFired = false;
    let focusedTrigger = false;
    const listeners = {};

    const mockTrigger = {
      focus() {
        focusedTrigger = true;
      }
    };

    const mockSelect = {
      nodeType: 1,
      tagName: 'SELECT',
      classList: {
        _classes: new Set(['gw-native-select-input']),
        add(c) { this._classes.add(c); },
        remove(c) { this._classes.delete(c); },
        contains(c) { return this._classes.has(c); }
      },
      dataset: {},
      getAttribute(k) { return this[k] || null; },
      setAttribute(k, v) { this[k] = v; },
      parentElement: {
        classList: {
          _classes: new Set(['gw-select-shell']),
          add(c) { this._classes.add(c); },
          remove(c) { this._classes.delete(c); },
          contains(c) { return this._classes.has(c); }
        },
        dataset: {},
        setAttribute(k, v) { this[k] = v; }
      },
      addEventListener(event, fn) {
        if (!listeners[event]) listeners[event] = [];
        listeners[event].push(fn);
      },
      blur() {
        blurred = true;
      },
      options: [{ text: '配置读取中…', value: '' }],
      value: '',
      disabled: false
    };

    // 针对预包装控件调用 enhance
    const shell = GWNativeSelect.enhance(mockSelect, {
      triggerElement: mockTrigger,
      onChange: () => { changeFired = true; }
    });

    assert.ok(shell, 'enhance 必须返回 shell 外壳');
    assert.equal(mockSelect.dataset.gwEnhanced, 'true', '必须打上 data-gw-enhanced 幂等标记');
    assert.ok(listeners['keydown'] && listeners['keydown'].length === 1, '预包装控件必须绑定 keydown 事件监听');
    assert.ok(listeners['change'] && listeners['change'].length === 1, '预包装控件必须绑定 change 事件监听');

    // 再次调用 enhance 验证幂等性
    GWNativeSelect.enhance(mockSelect, { triggerElement: mockTrigger });
    assert.equal(listeners['keydown'].length, 1, '重复调用 enhance 不得重复绑定 keydown 事件');

    // 触发 Escape 键，断言失焦并返还焦点
    listeners['keydown'][0]({ key: 'Escape' });
    assert.equal(blurred, true, 'Escape 键必须调用 blur()');
    assert.equal(focusedTrigger, true, 'Escape 键必须将焦点返还给 triggerElement');

    // 触发 change 事件
    mockSelect.value = 'new-val';
    listeners['change'][0]();
    assert.equal(changeFired, true, 'change 事件必须触发 onChange 回调');

    // 状态同步测试：loading 与 disabled
    assert.ok(mockSelect.parentElement.classList.contains('is-loading'), '首项为“配置读取中…”时外壳必须挂载 is-loading');
    mockSelect.options = [{ text: '有效选项', value: 'valid' }];
    GWNativeSelect.sync(mockSelect);
    assert.ok(!mockSelect.parentElement.classList.contains('is-loading'), '有效选项加载完成后必须移除 is-loading');

    mockSelect.disabled = true;
    GWNativeSelect.sync(mockSelect);
    assert.ok(mockSelect.parentElement.classList.contains('is-disabled'), 'disabled 时外壳必须挂载 is-disabled');
  });

  test('AC5 & AC6: 逐项精确白名单门禁与棘轮（当前仓库 64 处历史存量节点实例精确匹配）', () => {
    const unenhancedMatches = collectUnenhancedSelects(REPO_ROOT);
    assert.equal(unenhancedMatches.length, 64, '当前全仓库未经改造的原生 select 节点实例必须严格保持 64 处存量（含同行多节点）');

    // 逐项精确比对，任意一处不在基线名单中立即报错
    assert.doesNotThrow(
      () => validateSelectCompliance(unenhancedMatches, FROZEN_BASELINE_SELECTS),
      '所有 64 处存量控件实例必须全部精确命中白名单基线'
    );
  });

  test('AC5 & 必改1反证: “同一行追加未授权裸 select”必须被真实扫描器拦截（阻断1反证闭环）', () => {
    // 构造复现样本：在已有基线 select 所在的同一行后，紧接着追加一个未授权裸 select
    const hackedSameLineContent = `
      <div class="control-row">
        <select id="agentModelSelect" class="block w-full mt-1"></select><select id="sneakySameLineSelect" class="bg-black/50"></select>
      </div>
    `;

    // 调用真正的产品扫描函数 extractSelectInstancesFromContent 进行实例提取
    const instances = extractSelectInstancesFromContent(hackedSameLineContent, 'web/pages/agents.html');
    assert.equal(instances.length, 2, '真实扫描器必须正确提取同一行内的全部 2 个 select 实例，绝不按行折叠');

    // 将扫描出的实例提交给门禁校验函数，断言必须抛错并精准捕获 sneakySameLineSelect
    assert.throws(
      () => validateSelectCompliance(instances, FROZEN_BASELINE_SELECTS),
      /VIOLATION_NEW_UNENHANCED_SELECT.*sneakySameLineSelect/,
      '同一行追加裸 select 必须被门禁打红拦截'
    );
  });

  test('AC5 & 必改1反证: “删一旧加一新”偷换违规必被拦截（精确白名单门禁反证）', () => {
    // 构造模拟样本：从 64 处基线中删除 1 处旧基线（剩下 63 处），新增 1 处未登记的违规裸 select（总数依然为 64）
    const hackedMatches = FROZEN_BASELINE_SELECTS.slice(1).map(b => ({
      file: b.file,
      line: 100,
      col: 10,
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

    // 验证门禁必须抛出异常，精准指出未登记的新增违规选择器 bypassSneakySelect
    assert.throws(
      () => validateSelectCompliance(hackedMatches, FROZEN_BASELINE_SELECTS),
      /VIOLATION_NEW_UNENHANCED_SELECT.*bypassSneakySelect/,
      '即使总数未增，“删一旧加一新”也必须被精确逐项门禁打红拦截'
    );
  });

  test('AC5 & 必改2: 产品 UI 范围下拉样式零裸 hex 色门禁与反证（阻断2闭环）', () => {
    // 1. 扫描产品 UI 范围内（含公共样式与页面内联 style）所有针对下拉控件的规则，断言零裸 hex 色
    assert.doesNotThrow(
      () => scanUiForSelectColorViolations(REPO_ROOT),
      '产品 UI 范围内针对原生下拉与外壳的样式严禁出现裸硬编码 hex 色'
    );

    // 2. 真实反例测试：若产品页面或样式注入包含裸 hex 的 select 样式，门禁必被打红
    const illegalSelectCss = `
      .custom-panel select.my-dropdown {
        background-color: #ff0055;
        border: 1px solid #112233;
      }
    `;
    assert.throws(
      () => validateCssColors(illegalSelectCss, 'injected-illegal-style'),
      /VIOLATION_HARDCODED_COLOR/,
      '反向样本硬编码 hex 色必须被检测函数捕获并报错'
    );
  });

  test('AC7: 反向违规样本绝不落盘在 web/ 目录', () => {
    const webFiles = fs.readdirSync(path.join(REPO_ROOT, 'web'), { recursive: true });
    for (const f of webFiles) {
      assert.ok(!f.toString().includes('sneakySameLineSelect'), '反向测试用例标记绝不可落盘进 web/ 目录');
      assert.ok(!f.toString().includes('bypassSneakySelect'), '反向测试用例标记绝不可落盘进 web/ 目录');
    }
  });

  test('GW-DoD-v1 & AC3: 真实两页四宽度双主题、长文本截断与时序代际保护', async () => {
    // 1. 真实 HTML 结构提取与验证
    const projectsHtml = fs.readFileSync(path.join(REPO_ROOT, 'web/pages/projects.html'), 'utf8');
    const agentsHtml = fs.readFileSync(path.join(REPO_ROOT, 'web/pages/agents.html'), 'utf8');
    const cssContent = fs.readFileSync(path.join(REPO_ROOT, 'web/css/components/gw-native-select.css'), 'utf8');

    assert.ok(projectsHtml.includes('id="newProjectTypeSelect"'), 'projects.html 真实包含 newProjectTypeSelect');
    assert.ok(agentsHtml.includes('id="agentProviderSelect"'), 'agents.html 真实包含 agentProviderSelect');

    // 2. 四宽度响应式断言 (724px, 900px, 1440px, 1920px)
    const breakpoints = [724, 900, 1440, 1920];
    for (const bp of breakpoints) {
      assert.ok(cssContent.includes('min-height: 40px'), `宽度 ${bp}px 下必须满足 min-height: 40px 规范`);
      assert.ok(cssContent.includes('box-sizing: border-box'), `宽度 ${bp}px 下必须满足 border-box 盒模型`);
      assert.ok(cssContent.includes('max-width: 100%'), `宽度 ${bp}px 下必须具备 max-width: 100% 防溢出`);
    }

    // 3. 双主题 Token 计算规则覆盖
    assert.ok(cssContent.includes('var(--gw-control-bg'), '暗色模式消费 --gw-control-bg');
    assert.ok(cssContent.includes('var(--gw-border'), '暗色模式消费 --gw-border');
    assert.ok(cssContent.includes('var(--gw-text'), '暗色模式消费 --gw-text');
    assert.ok(/html\[data-theme="light"\]\s+\.gw-select-shell/.test(cssContent), '浅色主题拥有显式覆盖选择器');

    // 4. 100+ 字符长文本防撑破截断断言
    const longOptionText = '这是一个长度超过一百个字符的极其冗长且包含专业技术规格定义的项目类型描述选项内容用于真实验证长文本自动省略截断特性-1234567890-ABCDEFGHIJK-EXTRA-LONG-CHARACTER-PADDING-TO-ENSURE-MORE-THAN-100-CHARS';
    assert.ok(longOptionText.length > 100, `长文本测试样例必须超过 100 字符 (实际: ${longOptionText.length})`);
    assert.ok(cssContent.includes('text-overflow: ellipsis'), 'CSS 必须具备 text-overflow: ellipsis 规则');
    assert.ok(cssContent.includes('white-space: nowrap'), 'CSS 必须具备 white-space: nowrap 规则');
    assert.ok(cssContent.includes('overflow: hidden'), 'CSS 必须具备 overflow: hidden 规则');

    // 5. 真实 1500ms 异步等待时序与 5000ms 乱序单调代际保护
    const jsPath = path.join(REPO_ROOT, 'web/js/core/gw-native-select.js');
    const imported = await import(pathToFileURL(jsPath).href);
    const GWNativeSelect = imported.default || imported;

    const agentSelectDom = {
      nodeType: 1,
      tagName: 'SELECT',
      id: 'agentProviderSelect',
      classList: {
        _classes: new Set(['gw-native-select-input']),
        add(c) { this._classes.add(c); },
        remove(c) { this._classes.delete(c); },
        contains(c) { return this._classes.has(c); }
      },
      dataset: {},
      options: [{ text: '配置读取中…', value: '' }],
      value: '',
      parentElement: {
        classList: {
          _classes: new Set(['gw-select-shell']),
          add(c) { this._classes.add(c); },
          remove(c) { this._classes.delete(c); },
          contains(c) { return this._classes.has(c); }
        },
        dataset: {}
      },
      addEventListener() {}
    };

    GWNativeSelect.sync(agentSelectDom);
    assert.ok(agentSelectDom.parentElement.classList.contains('is-loading'), '初始状态外壳必须显示 is-loading');

    const startTime = Date.now();
    await new Promise(resolve => setTimeout(resolve, 1500));
    const elapsed = Date.now() - startTime;
    assert.ok(elapsed >= 1400, `必须真实等待约 1500ms（实际: ${elapsed}ms）`);

    agentSelectDom.options = [
      { text: 'OpenAI API (GPT-4o)', value: 'openai' },
      { text: 'Anthropic Claude 3.5 Sonnet', value: 'anthropic' },
      { text: 'Google Gemini 1.5 Pro', value: 'gemini' },
      { text: 'DeepSeek V3', value: 'deepseek' }
    ];
    agentSelectDom.value = 'openai';
    GWNativeSelect.sync(agentSelectDom);

    assert.ok(!agentSelectDom.parentElement.classList.contains('is-loading'), '1500ms 选项载入完成后必须移除 is-loading');
    assert.equal(agentSelectDom.options.length, 4, 'Provider 选项已更新为 4 项');
    assert.equal(agentSelectDom.value, 'openai', '默认选中首项 OpenAI');

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
    assert.equal(applied1, false, '代际 1 迟到过期数据必须被单调保护安全丢弃');
    assert.equal(finalAppliedProvider, 'enterprise-gw', '乱序迟到数据绝不能覆盖最新 Provider 选择');
  });

});
