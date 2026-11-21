# 原生公共下拉组件契约规范 (Native Enhanced Select Component Contract)

> **工单**：QIN-631 / GitHub #771
> **适用范围**：神工坊产品 UI 原生控件层（Native Component Layer）
> **设计系统**：黑金硬件设计系统（Black-Gold Hardware Design System）

---

## 1. 规范概述

为消除全站各页面中各自为政、内联硬编码样式的原生 `<select>` 控件，同时恪守轻量化、KISS 原则与**零外部第三方 UI 库依赖**（绝不引入 React/Vue 组件库），神工坊制定本原生公共下拉组件契约规范。

本组件采用**增强语义外壳（Enhanced Semantic Shell）**模式：
- 保留底层标准 HTML5 `<select>` 元素的所有原生语义、表单提交行为与辅助功能（A11y）；
- 通过外部包裹容器 `.gw-select-shell` 与统一类 `.gw-native-select-input`，结合 `hardware-tokens.css` 实现全站一致的硬件触感视觉、统一指示箭头、聚焦环以及双主题自适应；
- 兼容既有 DOM 获取方式（`document.getElementById`、`page.select_option()` 等），零侵入业务 Controller。

---

## 2. 结构与类名契约

### 2.1 HTML 结构标准
标准结构必须包含外层容器 `.gw-select-shell` 与内部具备 `.gw-native-select-input` 类的 `<select>`：

```html
<div class="gw-select-shell">
  <select id="exampleSelect" class="gw-native-select-input" aria-label="示例选择">
    <option value="opt1">选项一</option>
    <option value="opt2">选项二</option>
  </select>
</div>
```

若控件位于特定网格或需要外层间距，可在 `.gw-select-shell` 上追加工具类（如 `w-full`、`mt-1` 等）。

### 2.2 核心类名定义
| 类名 | 挂载目标 | 说明 |
|---|---|---|
| `.gw-select-shell` | 外层 `<div>` 容器 | 提供定位上下文（`relative`）、统一内嵌 SVG 箭头遮罩指示器及整体尺寸约束 |
| `.gw-native-select-input` | 内部 `<select>` 元素 | 重置原生外观（`appearance: none`）、绑定黑金硬件 Token 边框与背景、设置内边距与文字截断 |
| `.is-loading` | 外层容器 | 标记选项正在异步载入中 |
| `.is-disabled` | 外层容器 | 视觉禁用态（配合内部 select 的 `disabled` 属性） |

---

## 3. 设计 Token 映射契约

所有样式必须完全消费 `web/css/base/hardware-tokens.css` 与 `web/css/base/theme.css` 定义的 CSS 自定义属性，严禁在组件样式或调用方出现未授权的硬编码 hex 颜色。

| 视觉属性 | 深色主题 (Dark / Default) | 浅色主题 (Light) | 规范 Token |
|---|---|---|---|
| **控件背景** | `#0f1115` | `#ffffff` | `var(--gw-control-bg)` |
| **控件边框** | `#343841` | `#d1d5db` | `var(--gw-border)` |
| **主要文字** | `#e5e7eb` | `#1f2937` | `var(--gw-text)` |
| **弱化/箭头** | `#aeb6c3` | `#6b7280` | `var(--gw-text-muted)` |
| **聚焦高亮** | `#dfc384`（香槟钛金） | `#dfc384` | `var(--gw-warning)` |
| **最小高度** | `40px` | `40px` | 遵循项目 `min-height: 40px` 标准 |
| **字号与行高** | `var(--gw-type-body-md)` | `var(--gw-type-body-md)` | `var(--gw-leading-body-md)` |

---

## 4. 交互与无障碍（A11y）契约

1. **指示箭头**：
   - 统一由 `.gw-select-shell::after` 渲染内联矢量 SVG 箭头；
   - 内部 `<select>` 的右侧内边距保持 ≥ `36px`，确保文字在任何语言与长度下均不与指示箭头重叠；
   - 箭头元素设置 `pointer-events: none`，点击箭头区域正常穿透并唤起下拉选择菜单。
2. **长文本截断**：
   - 选项文字超长时，在未展开态强制应用 `text-overflow: ellipsis; overflow: hidden; white-space: nowrap;`，防止撑破栅格布局或弹窗容器。
3. **键盘与焦点响应**：
   - 当用户通过 `Tab` 键聚焦时，容器获得 `:focus-within` 伪类，显示香槟钛金（`--gw-warning`）高亮边框与外发光环；
   - 支持键盘 `Escape` 快速失焦并安全返还焦点：优先返还至 `options.triggerElement`、声明式属性 `data-gw-trigger`（或 `data-trigger`）指定的选择器目标；在未显式声明 trigger 时，通过 W3C 标准 `focusin` 的 `relatedTarget` 或移入前交互焦点机制动态返还至前置活动控件；
   - 当不存在外部 trigger 且无有效前置焦点时，严禁盲目失焦将焦点抛弃至 `document.body`，保持当前焦点以确保无障碍（A11y）上下文不丢失；
   - 纯原生 `<select>` 具备操作系统原生键盘上下键选择与快速检索选项能力。
4. **响应式与多端尺寸**：
   - 在 724px、900px、1440px、1920px 四档标准断点下自适应父级宽度，窄屏下提供最小可点按热区（高度 ≥ 40px）。

---

## 5. JavaScript API（可选辅助能力）

对于由脚本动态生成或需要状态管理的场景，提供 `window.GWNativeSelect` 命名空间：

```javascript
// 增强某个已有的原生 select
const shell = window.GWNativeSelect.enhance(selectEl);

// 批量增强页面或容器内带 [data-gw-select] 的元素
window.GWNativeSelect.enhanceAll(document.getElementById('myDialog'));

// 当异步动态替换了 options 后同步状态
window.GWNativeSelect.sync(selectEl);
```

---

## 6. CI 质量门禁与反向约束

为确保全站样式统一且不再滋生新的私有下拉实现，CI 门禁 `tests/frontend/qin-631-native-select-pilot.test.mjs` 强制执行以下规则：
1. **新增未授权原生 `<select>` 必失败**：凡在产品 UI 范围内新增未经 `.gw-select-shell` / `.gw-native-select-input` 包装且未在旧基线中登记的原生 `<select>`，测试自动报错打红；
2. **控件色硬编码必失败**：凡在组件样式及相关结构中引入硬编码 hex 色（未使用 `var(--gw-...)`），测试自动报错打红；
3. **旧基线只减不增（Ratchet Mechanism）**：历史存量旧下拉条目受严格锁定，随着后续波次迁移，基线数量只减不增。
