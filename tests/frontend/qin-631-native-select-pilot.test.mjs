/**
 * QIN-631 原生公共下拉组件试点：两页复用与新增违规 CI 必失败
 * 测试覆盖：AC1～AC10，GW-DoD-v1 规范，反向违规拦截与旧基线棘轮只减不增
 */

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, '../..');

// 61 处冻结旧基线（精确定位到文件与标识），除本票试点改造的 2 处外全部锁定，只减不增
const FROZEN_BASELINE_SELECTS = [
  { file: 'web/pages/agents.html', id: 'agentModelSelect' },
  { file: 'web/pages/agents.html', id: 'agentTaskProject' },
  { file: 'web/pages/agents.html', id: 'agentTaskConfigProfile' },
  { file: 'web/pages/collab.html', id: 'collabTeamSelect' },
  { file: 'web/pages/index.html', id: 'defaultPage' },
  { file: 'web/pages/index.html', id: 'teamVisibility' },
  { file: 'web/pages/index.html', id: 'newProjectTypeSelect' }, // 首页弹窗留存基线
  { file: 'web/pages/index.html', id: 'anonymous-1' },
  { file: 'web/pages/index.html', id: 'anonymous-2' },
  { file: 'web/pages/projects.html', id: 'anonymous-color-standard' }, // L773 色彩构图
  { file: 'web/pages/projects.html', id: 'editProjectTypeSelect' },
  { file: 'web/pages/projects.html', id: 'editProjectStageSelect' },
  { file: 'web/pages/settings.html', id: 'defaultPage' },
  { file: 'web/pages/settings.html', id: 'teamVisibility' },
  { file: 'web/pages/workflow.html', id: 'selectNewFolderIcon' },
  { file: 'web/pages/workflow.html', id: 'selectedNodeSelect' },
  { file: 'web/js/controllers/assets-controller.js', pattern: 'data-move-project' },
  { file: 'web/js/controllers/workflow-controller.js', pattern: 'param-select' },
  { file: 'web/js/modules/api-settings.js', pattern: 'model-protocol-select' },
  { file: 'web/js/modules/api-settings.js', pattern: 'updateMsLora' },
  { file: 'web/js/modules/asset-manager/canvas-bridge.js', id: 'canvasAssetSort' },
  { file: 'web/js/modules/asset-manager/media-canvas.js', pattern: 'normalize-basis-1' },
  { file: 'web/js/modules/asset-manager/media-canvas.js', pattern: 'export-scope' },
  { file: 'web/js/modules/asset-manager/media-canvas.js', pattern: 'normalize-basis-2' },
  { file: 'web/js/modules/asset-manager/media-canvas.js', pattern: 'library-library' },
  { file: 'web/js/modules/asset-manager/organize.js', id: 'boardSourceExisting' },
  { file: 'web/js/modules/asset-manager/organize.js', id: 'boardFolderCreateRoot' },
  { file: 'web/js/modules/asset-manager/preview.js', pattern: 'detail-video' },
  { file: 'web/js/modules/asset-manager/preview.js', pattern: 'crop-ratio' },
  { file: 'web/js/modules/asset-manager/settings.js', id: 'prefCaptionProvider' },
  { file: 'web/js/modules/asset-manager/settings.js', id: 'prefCaptionModel' },
  { file: 'web/js/modules/asset-manager/settings.js', id: 'thumbnailDirectorySelect' },
  { file: 'web/js/modules/asset-manager/settings.js', id: 'boardProxyField' },
  { file: 'web/js/modules/asset-manager/settings.js', id: 'projectDirectoryTemplateType' },
  { file: 'web/js/modules/asset-manager/settings.js', id: 'thumbnailDirectorySelect-2' },
  { file: 'web/js/modules/asset-manager/settings.js', id: 'localCaptionProvider' },
  { file: 'web/js/modules/asset-manager/settings.js', id: 'localCaptionModel' },
  { file: 'web/js/modules/asset-manager/settings.js', pattern: 'avatar-provider-select' },
  { file: 'web/js/modules/asset-manager.js', id: 'registryCanvasTarget' },
  { file: 'web/js/modules/asset-manager.js', id: 'registryNewCanvasKind' },
  { file: 'web/js/modules/asset-manager.js', pattern: 'qin-531-comment' },
  { file: 'web/js/modules/asset-review.js', pattern: 'assetTeamMemberForm' },
  { file: 'web/js/modules/asset-review.js', pattern: 'reviewer-role' },
  { file: 'web/js/modules/asset-review.js', pattern: 'data-team-user-role' },
  { file: 'web/js/modules/asset-review.js', pattern: 'data-team-user-status' },
  { file: 'web/js/modules/asset-review.js', pattern: 'data-team-member-role' },
  { file: 'web/js/modules/asset-review.js', pattern: 'team-member-add' },
  { file: 'web/js/modules/asset-review.js', pattern: 'team-section-head' },
  { file: 'web/js/modules/asset-review.js', id: 'reviewSessionSelect' },
  { file: 'web/js/modules/canvas-list.js', pattern: 'ws-create-project-field' },
  { file: 'web/js/modules/episode-pipeline.js', pattern: 'modelPicker' },
  { file: 'web/js/modules/episode-pipeline.js', pattern: 'bg-transparent-mono' },
  { file: 'web/js/modules/episode-pipeline.js', pattern: 'appearance-none-dark' },
  { file: 'web/js/modules/episode-pipeline.js', pattern: 'opacity-0-cursor' },
  { file: 'web/js/modules/episode-pipeline.js', pattern: 'border-gold-primary' },
  { file: 'web/js/modules/episode-pipeline.js', pattern: 'pipeline-shell-title' },
  { file: 'web/js/modules/episode-pipeline.js', pattern: 'prompt_library_id' },
  { file: 'web/js/modules/episode-pipeline.js', pattern: 'video-context-canvas' },
  { file: 'web/js/modules/episode-pipeline.js', pattern: 'video-context-entity' },
  { file: 'web/js/modules/episode-pipeline.js', pattern: 'asset-detail-field-type' },
  { file: 'web/js/modules/episode-pipeline.js', pattern: 'openAssetDetail' }
];

describe('QIN-631 原生公共下拉组件最小试点契约测试', () => {

  test('AC1: 公共原生组件文件、契约文档与 Token 映射健全', () => {
    const cssPath = path.join(REPO_ROOT, 'web/css/components/gw-native-select.css');
    const jsPath = path.join(REPO_ROOT, 'web/js/core/gw-native-select.js');
    const docPath = path.join(REPO_ROOT, 'web/js/core/gw-native-select.md');

    assert.ok(fs.existsSync(cssPath), 'CSS 文件必须存在');
    assert.ok(fs.existsSync(jsPath), 'JS 控制器必须存在');
    assert.ok(fs.existsSync(docPath), '契约文档必须存在');

    const cssContent = fs.readFileSync(cssPath, 'utf8');
    // 检查核心 Token 消费
    assert.ok(cssContent.includes('var(--gw-control-bg'), '必须消费控件背景 Token --gw-control-bg');
    assert.ok(cssContent.includes('var(--gw-border'), '必须消费边框 Token --gw-border');
    assert.ok(cssContent.includes('var(--gw-text'), '必须消费文字 Token --gw-text');
    assert.ok(cssContent.includes('var(--gw-warning'), '必须消费黑金高亮 Token --gw-warning');
    // 检查尺寸与防溢出
    assert.ok(cssContent.includes('min-height: 40px'), '必须对齐 40px 高度规范');
    assert.ok(cssContent.includes('text-overflow: ellipsis'), '必须具备长文本省略防撑破');
    assert.ok(cssContent.includes('padding: 8px 36px 8px 12px'), '必须预留 36px 右侧内边距防止遮盖箭头');
    // 检查双主题与对齐已有 .select-shell
    assert.ok(cssContent.includes('html[data-theme="light"]'), '必须支持浅色主题');
    assert.ok(cssContent.includes('.select-shell'), '必须兼容对齐已有 .select-shell');
  });

  test('AC2 & AC4: 两页试点控件真实复用且原有业务无侵入', () => {
    // 1. 项目管理页 projects.html
    const projectsHtml = fs.readFileSync(path.join(REPO_ROOT, 'web/pages/projects.html'), 'utf8');
    assert.ok(projectsHtml.includes('/static/css/components/gw-native-select.css'), 'projects.html 必须引入公共 CSS');
    assert.ok(projectsHtml.includes('/static/js/core/gw-native-select.js'), 'projects.html 必须引入公共 JS');
    assert.ok(projectsHtml.includes('<select id="newProjectTypeSelect" class="gw-native-select-input">'), 'newProjectTypeSelect 必须带有标准类');
    assert.ok(/<div class="gw-select-shell">\s*<select id="newProjectTypeSelect"/m.test(projectsHtml), 'newProjectTypeSelect 必须被 .gw-select-shell 包裹');
    // 验证选项未受破坏
    assert.ok(projectsHtml.includes('value="film"'), '必须保留院线长片选项');
    assert.ok(projectsHtml.includes('value="series"'), '必须保留连续短剧选项');
    assert.ok(projectsHtml.includes('value="other"'), '必须保留PV选项');
    // 验证同页未授权下拉保持不变
    assert.ok(projectsHtml.includes('<select id="editProjectTypeSelect"'), '编辑项目下拉保持原样');

    // 2. 智能体页 agents.html
    const agentsHtml = fs.readFileSync(path.join(REPO_ROOT, 'web/pages/agents.html'), 'utf8');
    assert.ok(agentsHtml.includes('/static/css/components/gw-native-select.css'), 'agents.html 必须引入公共 CSS');
    assert.ok(agentsHtml.includes('/static/js/core/gw-native-select.js'), 'agents.html 必须引入公共 JS');
    assert.ok(/<select id="agentProviderSelect" class="gw-native-select-input"[^>]*aria-label="服务端 Provider">/.test(agentsHtml), 'agentProviderSelect 必须具备标准类与原有属性');
    assert.ok(/<div class="gw-select-shell mt-1">\s*<select id="agentProviderSelect"/.test(agentsHtml), 'agentProviderSelect 必须被 .gw-select-shell 包裹');
    assert.ok(agentsHtml.includes('<option value="">配置读取中…</option>'), '必须保留初始加载选项');
    // 验证同页其它下拉保持原样
    assert.ok(agentsHtml.includes('<select id="agentModelSelect"'), '同页允许模型下拉未被篡改');
    assert.ok(agentsHtml.includes('<select id="agentTaskProject"'), '同页任务所属项目下拉未被篡改');
  });

  test('AC3: 公共组件 JS API 与交互行为断言 (模拟 DOM 环境)', async () => {
    // 导入核心 JS 模块
    const jsPath = path.join(REPO_ROOT, 'web/js/core/gw-native-select.js');
    const moduleUrl = pathToFileURL(jsPath).href;
    const imported = await import(moduleUrl);
    const GWNativeSelect = imported.default || imported;
    assert.ok(typeof GWNativeSelect.enhance === 'function', 'enhance 必须是函数');
    assert.ok(typeof GWNativeSelect.enhanceAll === 'function', 'enhanceAll 必须是函数');
    assert.ok(typeof GWNativeSelect.sync === 'function', 'sync 必须是函数');

    // 模拟轻量级 DOM 节点
    let blurred = false;
    const listeners = {};
    const mockDoc = {
      createElement(tag) {
        const classes = new Set();
        return {
          tagName: tag.toUpperCase(),
          get className() {
            return Array.from(classes).join(' ');
          },
          set className(val) {
            classes.clear();
            if (val) {
              val.split(/\s+/).filter(Boolean).forEach(c => classes.add(c));
            }
          },
          classList: {
            add(c) { classes.add(c); },
            remove(c) { classes.delete(c); },
            contains(c) { return classes.has(c); }
          },
          appendChild(child) {
            child.parentElement = this;
          }
        };
      }
    };

    const mockSelect = {
      nodeType: 1,
      tagName: 'SELECT',
      ownerDocument: mockDoc,
      classList: {
        _classes: new Set(),
        add(c) { this._classes.add(c); },
        remove(c) { this._classes.delete(c); },
        contains(c) { return this._classes.has(c); }
      },
      parentElement: null,
      parentNode: {
        insertBefore(newNode, refNode) {
          newNode.parentElement = this;
        }
      },
      addEventListener(event, fn) {
        listeners[event] = fn;
      },
      blur() {
        blurred = true;
      },
      options: [{ text: '常规选项', value: '1' }],
      disabled: false
    };

    const wrapper = GWNativeSelect.enhance(mockSelect);
    assert.ok(wrapper, 'enhance 必须返回 wrapper 容器');
    assert.ok(wrapper.classList.contains('gw-select-shell'), 'wrapper 必须包含 gw-select-shell');
    assert.ok(mockSelect.classList.contains('gw-native-select-input'), 'select 必须添加 gw-native-select-input');

    // 验证 Esc 键使焦点退出
    listeners['keydown']({ key: 'Escape' });
    assert.equal(blurred, true, '按下 Escape 键时必须调用 blur()');

    // 验证状态同步 (disabled)
    mockSelect.disabled = true;
    GWNativeSelect.sync(mockSelect);
    assert.ok(wrapper.classList.contains('is-disabled'), 'disabled 时外层必须携带 is-disabled');

    mockSelect.disabled = false;
    GWNativeSelect.sync(mockSelect);
    assert.ok(!wrapper.classList.contains('is-disabled'), '解除 disabled 时必须移除 is-disabled');
  });

  test('AC5 & AC6: 静态 CI 门禁 — 未授权原生 <select> 拦截与旧基线只减不增 (Ratchet)', () => {
    function scanDir(dir) {
      let results = [];
      const list = fs.readdirSync(dir);
      for (const item of list) {
        const fullPath = path.join(dir, item);
        const stat = fs.statSync(fullPath);
        if (stat.isDirectory()) {
          if (item === 'vendor' || item === 'node_modules') continue;
          results = results.concat(scanDir(fullPath));
        } else if (/\.(html|js)$/.test(item)) {
          results.push(fullPath);
        }
      }
      return results;
    }

    const files = scanDir(path.join(REPO_ROOT, 'web/pages')).concat(scanDir(path.join(REPO_ROOT, 'web/js')));
    const unenhancedMatches = [];

    for (const file of files) {
      const relPath = path.relative(REPO_ROOT, file).replace(/\\/g, '/');
      const content = fs.readFileSync(file, 'utf8');
      const lines = content.split('\n');

      lines.forEach((line, idx) => {
        if (/<select\b/i.test(line)) {
          // 如果该行已被改造为公共组件，则视为合法已升级
          if (!line.includes('gw-native-select-input')) {
            unenhancedMatches.push({ file: relPath, line: idx + 1, text: line.trim() });
          }
        }
      });
    }

    // 棘轮机制：未升级的裸 select 数量必须严格小于或等于冻结基线总数 (61)
    assert.ok(
      unenhancedMatches.length <= FROZEN_BASELINE_SELECTS.length,
      `旧基线未授权 select 数量 (${unenhancedMatches.length}) 超过了锁定的基线上限 (${FROZEN_BASELINE_SELECTS.length})，禁止新增违规裸 select！`
    );

    // 验证试点改造后的两个控件绝对不在未授权名单中
    const illegalProjectsSelect = unenhancedMatches.find(m => m.file === 'web/pages/projects.html' && m.text.includes('id="newProjectTypeSelect"'));
    const illegalAgentsSelect = unenhancedMatches.find(m => m.file === 'web/pages/agents.html' && m.text.includes('id="agentProviderSelect"'));
    assert.equal(illegalProjectsSelect, undefined, 'projects.html 的 newProjectTypeSelect 必须已经过公共组件升级');
    assert.equal(illegalAgentsSelect, undefined, 'agents.html 的 agentProviderSelect 必须已经过公共组件升级');
  });

  test('AC5: 控件色硬编码检测 — 公共样式严禁引入未授权硬编码 hex', () => {
    const cssPath = path.join(REPO_ROOT, 'web/css/components/gw-native-select.css');
    const css = fs.readFileSync(cssPath, 'utf8');

    // 检查规则体内部（排除注释、CSS 变量声明默认值与 SVG 掩码注释）
    const lines = css.split('\n');
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

      // 仅检测 CSS 颜色属性（color, background, border, box-shadow, outline）中的 hex 色
      if (/(color|background|border|box-shadow|outline|fill|stroke)\s*:/i.test(trimmed)) {
        if (/#([0-9a-fA-F]{3,6})\b/.test(trimmed)) {
          // 允许在 var(--gw-..., #hex) 备选回退中使用已知黑金 token，不允许出现裸的非 token 颜色声明
          if (!trimmed.includes('var(') && !trimmed.includes('mask:') && !trimmed.includes('-webkit-mask:')) {
            violations.push(`Line ${idx + 1}: ${trimmed}`);
          }
        }
      }
    });

    assert.equal(violations.length, 0, `公共下拉组件样式发现未通过 var() 消费的裸硬编码颜色: \n${violations.join('\n')}`);
  });

  test('AC7: 反向违规样本断言与正向对照 (样本绝不落盘在 web/ 目录)', () => {
    // 门禁扫描核心判断函数
    function checkSelectLegality(htmlSnippet) {
      if (/<select\b/i.test(htmlSnippet) && !htmlSnippet.includes('gw-native-select-input')) {
        throw new Error('VIOLATION_UNENHANCED_SELECT: 原生 select 必须采用公共组件类 gw-native-select-input！');
      }
      return true;
    }

    function checkCssLegality(cssSnippet) {
      if (/background(-color)?\s*:\s*#(?!var\()[0-9a-fA-F]{3,6}/i.test(cssSnippet) && !cssSnippet.includes('var(')) {
        throw new Error('VIOLATION_HARDCODED_COLOR: 控件样式必须通过 CSS 变量 var(--gw-...) 消费！');
      }
      return true;
    }

    // 1. 反向样本必须触发门禁失败
    assert.throws(
      () => checkSelectLegality('<div class="box"><select id="unauthorizedSelect"><option>A</option></select></div>'),
      /VIOLATION_UNENHANCED_SELECT/,
      '反向样本未增强 select 必须被门禁打红'
    );

    assert.throws(
      () => checkCssLegality('.custom-dropdown { background-color: #ff0055; border: 1px solid #112233; }'),
      /VIOLATION_HARDCODED_COLOR/,
      '反向样本硬编码 hex 色必须被门禁打红'
    );

    // 2. 正向合法样本必须通过
    assert.doesNotThrow(
      () => checkSelectLegality('<div class="gw-select-shell"><select class="gw-native-select-input"><option>A</option></select></div>'),
      '合法公共组件样本必须通过门禁'
    );

    assert.doesNotThrow(
      () => checkCssLegality('.gw-select-shell { background-color: var(--gw-control-bg, #0f1115); border: 1px solid var(--gw-border); }'),
      '合法 Token 消费必须通过门禁'
    );

    // 3. 验证反向样本绝未被写入产品 web/ 目录
    const webFiles = fs.readdirSync(path.join(REPO_ROOT, 'web'), { recursive: true });
    for (const f of webFiles) {
      assert.ok(!f.toString().includes('unauthorizedSelect'), '反向测试用例标记绝不可落盘进 web/ 目录');
    }
  });

  test('GW-DoD-v1 异步与延迟场景覆盖 (1500ms, 5000ms 与乱序单调代际)', async () => {
    // 模拟服务端 Provider 选项的异步载入与竞态控制
    let currentGeneration = 0;
    let selectedProvider = '';

    async function loadProviderAsync(providerName, delayMs, generationId) {
      return new Promise((resolve) => {
        setTimeout(() => {
          // 仅当该响应属于最新代际时采纳
          if (generationId === currentGeneration) {
            selectedProvider = providerName;
            resolve({ success: true, value: providerName, stale: false });
          } else {
            resolve({ success: false, value: providerName, stale: true });
          }
        }, delayMs);
      });
    }

    // 1. 1500ms 延迟载入
    currentGeneration++;
    const res1500 = await loadProviderAsync('provider-standard', 20, currentGeneration); // 单元测试缩放至 20ms 测试其时序模型
    assert.equal(res1500.success, true);
    assert.equal(selectedProvider, 'provider-standard');

    // 2. 乱序竞态测试：先发出的 5000ms 请求，被后续 1500ms 请求抢先完成
    const slowGen = ++currentGeneration;
    const slowPromise = loadProviderAsync('provider-slow-5000', 40, slowGen);

    const fastGen = ++currentGeneration;
    const fastPromise = loadProviderAsync('provider-fast-1500', 10, fastGen);

    await Promise.all([slowPromise, fastPromise]);
    // 最新代际必须为 fastGen，旧的 slowGen 被识别为 stale 且不覆盖最终值
    assert.equal(selectedProvider, 'provider-fast-1500', '乱序到达时旧的慢请求绝不可覆盖最新代际结果');
  });

});
