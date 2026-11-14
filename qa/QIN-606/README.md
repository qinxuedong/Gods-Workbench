# QIN-606 浏览器证据（只读分支）

- 对象：GitHub #720 / QIN-606，PR #722
- 口径：隔离测试实例（run.py --test + 临时数据目录）+ Chrome（ego-browser CDP 视口模拟），直接打开资产库嵌入页 `/static/asset-manager.html?embedded=1&vault=1`（口径同 QIN-602 证据分支 qin-602-qa），四宽按嵌入页视口量测
- 账户：隔离库内 setup 注册的测试账户，无真实数据
- `ac-evidence.json` 首字段 `head` = `0a282a68…`（PR head，修复后）；`baseline-evidence.json` 首字段 `head` = `cbff29ad…`（master，修复前基线）
- 空库（全部计数 0）与 QIN-602 证据的空库口径一致；重叠/间距定义：gap = em.x − small.right（正值=间距，负值=重叠）

## 组合与结果（gap = em.x − small.right）

| 视口 | 主题 | 修复前 gap（基线 cbff29ad） | 修复后 gap（head 0a282a68） | 判定 |
|---|---|---|---|---|
| 1440 | dark/light | **−2.27（重叠 2.27px）** | **+5** | AC1 达成 |
| 1920 | dark/light | 45.5 | 45.5 | AC2 达成（零回归） |
| 724 / 900 | dark/light | （QIN-602 span 2 已修复） | +100.66 / +8.66 | AC3 达成（无溢出、13 卡、无重叠） |
| 全部 | dark/light | — | scrollOverflow=false | AC3 无横向溢出 |

- AC4：1440 `emTruncated=true`，徽标就地截断为「统一资产…」（约 4 字 + 省略号，与设计表 v2 估算一致）；1920 完整显示 66px。
- 每组 dark/light 数值完全一致（布局与主题无关，主题仅配色）。

## 文件

- `ac-evidence.json`：修复后 8 组量测（含 small/em 矩形、gap/overlap、emTruncated、emMaxWidth/emOverflow/emEllipsis 生效样式、卡数、横向溢出）
- `baseline-evidence.json`：修复前基线 4 组（master cbff29ad）
- `qin606-<宽>-<主题>.png`：修复后截图 8 张；`baseline-<宽>-<主题>.png`：修复前截图 4 张
- 二进制证据只放本只读分支，不进入主干；验收脚本与运行时数据留在本地私有目录，不入库

## 返工重取记录（第 1 轮独立审查 FIX_REQUIRED 闭环）

- 首轮 `ac-evidence.json` 的 head 字段误记为 start commit `e31d1691…`（量测时实现改动仍在工作区、未提交），README 却写 `0a282a68…`，JSON 与 README 不一致——审查指出后**在干净提交 head `0a282a6816d734ea55902d15b42accb69611679d` 上全量重取 8 组**（`git status --porcelain` 为空，JSON 新增 `headDirtyWorktree:false` 字段证明测量由该提交运行）。
- 重取数值与首轮逐位一致：gap 724=+100.66 / 900=+8.66 / **1440=+5**（截断生效「统一资产…」）/ **1920=+45.5**（零回归），`scrollOverflow=false`、13 卡、dark/light 逐位一致，`passed:true`。
- 修复前基线 `baseline-evidence.json`（head `cbff29ad…`）本就在干净 master 上量得，无需重取。
- 8 张修复后截图同步替换为本轮重取版本；baseline 4 张不变。
