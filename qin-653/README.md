# QIN-653 四宽双主题详情面板取证（只读）

本分支只存放 issue #814 / QIN-653 对审核官 2026-11-24 第 1 轮 `FIX_REQUIRED` 所要求的补证原件，**不参与任何构建**，可随时删除。

- **固定 head**：`9cd74574dd10064802da8d6933e7bb43a8d0b94f`（PR #815 / 分支 `qin-653`）；发评前后 head 未变、无 rebase。
- **取数环境**：worktree `gw-qin-653` 的 `web/` 经本地只读静态代理提供（前端代码逐字节来自上述 head），API 与登录态转发至既有本地实例；未改数据、未改代码。
- **采集脚本**：`ego-matrix.mjs`，Chrome DevTools Protocol `Emulation.setDeviceMetricsOverride` 设 724/900/1440/1920 四档宽度（高 900、DSF 1、非移动），每档 `Page.reload` 后由 iframe 内真实 keydown 打开详情面板，再改宽度让 CSS 媒体查询自然重算。
- **主题**：顶层与 iframe 同时设 `studio_theme`/`canvas_theme`/`theme` 为 `dark` 或 `light`，逐格回读 `data-theme` 与 `body` 计算背景色核对生效。

## 原件

- `shots/matrix-{dark,light}-{724,900,1440,1920}.png` — 8 张整页截图（含 iframe 内详情面板）。
- `matrix-dark.json` / `matrix-light.json` — 逐格几何与计算样式读数（侧栏/画布视口/工具条/分页按钮位置、背景色、溢出差、类型文字数、底部按钮数、快捷键提示数、激活态渐变）。
- `matrix-summary.json` — 8 格硬判据汇总，`all_pass: true`。

## 审核判据对照

| 项 | 判据 | 结果 |
| --- | --- | --- |
| 打开 | 四档双主题详情面板真实打开、`viewer=true` | 8/8 |
| 主题 | 顶层与 iframe `data-theme` 均为目标主题，背景色 dark `rgb(9,10,13)` / light `rgb(241,242,244)` | 8/8 |
| 不超出 | iframe `scrollWidth - clientWidth == 0` | 8/8 |
| 结构 | 类型 `<span>` 0 个、底部 `.asset-detail-actions` 0 个、快捷键 `.asset-detail-shortcuts` 0 个 | 8/8 |
| 宽档对齐 | 1440 / 1920：侧栏顶 == 画布可视区顶 == 92（`max-width:960px` 外，边距绑定工具栏高度） | 4/4 |
| 窄档堆叠 | 724 / 900：侧栏位于画布下方，分页按钮 `tabs.top - sidebar.top <= 2`（走 `@media max-width:960px` 上下堆叠） | 4/4 |
| 分页激活态 | `linear-gradient(rgba(201,168,88,.2), rgba(201,168,88,.07))` 金色渐变，非白底反色 | 8/8 |

`matrix-summary.json` 的 `grid[].passed` 逐格为 `true`（8/8），任一格失败则整份汇总 `all_pass=false`。
