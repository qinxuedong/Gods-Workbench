# UI 治理交接文档（2026-10-12）

## 交接结论

本交接记录覆盖 UI 治理 B0a～B6 以及用户最后确认的按钮纠正，基线绑定到提交前的 `master` HEAD：

- 基线 HEAD：`cbd7c62c873774b5036d0942fab755eb09b6c89e`
- 任务范围：`B0A_TO_B6_UI_GOVERNANCE`
- 权威设计源：`.local/docs/design/按钮设计标准/`
- 当前状态：本地技术验证完成，等待人工视觉确认
- 人工视觉确认：`NOT_PERFORMED`
- 真实宿主、2077、生产数据、供应商执行：`OUT_OF_SCOPE`

本文件只记录交接事实。`.local/agent_tasks/active/ui-governance-20261012/` 中的报告、日志、哈希和脚本是本地证据，不随本文件进入远端仓库。

## 已完成范围

### 1. 按钮规则已按真源收口

- 拟物发光胶囊只允许固定顶栏的 10 个导航按钮使用。
- 顶栏以外的操作入口统一使用普通按钮样式；不再因为页面位置或历史模板残留使用高亮胶囊。
- 金色主按钮的 `hover`、`pressed` 保持金色材质；按压只提供位移和内凹阴影反馈，不切换为黑色。
- 工作流页面中用户指定的两个入口使用普通暗色按钮，保留原有行为、ID、文案和 `title`。

精确落点：

- `web/pages/workflow.html:208`：`btnOpenContractModal`，最终 class 为 `hw-btn`。
- `web/pages/workflow.html:446`：`btnParseLink`，最终 class 为 `hw-btn`。
- 两个按钮均不包含 `hw-btn-gold` 或 `primary-ignite`。

### 2. 其他治理范围

本轮 B0a～B6 还包含设计系统文档、硬件按钮基础样式、页面级主题收口、嵌入页和主页面入口治理、焦点与键盘可达性、排版门禁、动态浮窗边界及本地验证夹具。产品源码改动已绑定到 `.local/agent_tasks/active/ui-governance-20261012/source-manifest.json` 的 66 个路径，后端 `gw/` 未改动。

## 验证证据

以下结果在上述基线和冻结源码上完成，并已写入本地验证报告：

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 前端 Node 门禁 | 125 passed，0 failed | `.local/agent_tasks/active/ui-governance-20261012/final-node.log` |
| 完整 Python 测试 | 1551 passed，13 skipped，7 warnings | `.local/agent_tasks/active/ui-governance-20261012/final-pytest.log` |
| 工作流普通按钮 reconciliation | 24/24 通过 | `.local/agent_tasks/active/ui-governance-20261012/ORB-ORDINARY-BUTTONS-RECONCILIATION.json` |
| 浏览器最终报告 | `PASSED_FROZEN_BROWSER_VERIFICATION` | `.local/agent_tasks/active/ui-governance-20261012/ORB-FINAL-BROWSER-REPORT.md` |
| Standards 独立审核 | `PASSED_LOCAL_ONLY` | `.local/agent_tasks/active/ui-governance-20261012/FINAL-REVIEW-STANDARDS.md` |
| Spec 独立审核 | `PASSED` | `.local/agent_tasks/active/ui-governance-20261012/FINAL-REVIEW-SPEC.md` |
| JavaScript 语法 | 通过 | `.local/agent_tasks/active/ui-governance-20261012/validation.json` |
| `git diff --check` | 退出码 0 | `.local/agent_tasks/active/ui-governance-20261012/final-diff-check.log` |
| 交付门禁 | 退出码 0；无漂移、无额外路径、无暂存文件 | `.local/agent_tasks/active/ui-governance-20261012/delivery-check.json` |

验证命令记录：

```powershell
node --test tests\frontend\agent-tasks.test.mjs tests\frontend\asset-share-media.test.mjs tests\frontend\canvas-workspace.test.mjs tests\frontend\typography-governance.test.mjs tests\frontend\ui-governance.test.mjs tests\frontend\workflow-assets.test.mjs tests\frontend\workflow-auth-behavior.test.mjs tests\frontend\workflow-remediation.test.mjs tests\frontend\workflow-workbench.test.mjs
python -m pytest -v
git diff --check
python .local\agent_tasks\active\ui-governance-20261012\check_delivery.py
```

`check_delivery.py` 的通过结果对应本交接文件加入前的冻结树；本文件属于交接产物，不纳入 66 个产品源码路径的冻结清单。

## 交接边界与风险

- 当前目录是共享脏工作树。不得对其他未提交文件执行 `reset`、`clean`、`stash`、整体格式化或覆盖式复制。
- 本次交接提交只包含本文件；已有 UI 治理产品改动、测试改动和新增样式文件不在本次提交中。
- 不得把本地审核结果表述为远端 CI、宿主验收或发布验收。
- 浏览器与夹具证据只能证明本地冻结条件下的静态/交互行为，不能替代真实 2077 服务、生产数据、供应商接口或用户视觉验收。
- `.local/`、`.agents/` 和用户提供的参考图片均保持本地，不提交二进制或私有资料。

## 后续人工验收

由用户在最后阶段按 `.local/agent_tasks/active/ui-governance-20261012/HUMAN-ACCEPTANCE.md` 检查：

1. 固定顶栏仅出现 10 个拟物发光胶囊，其他区域不出现同类高亮胶囊。
2. 金色主按钮在默认、悬浮、按压、释放状态都保持金色材质，按压不会变黑。
3. `btnOpenContractModal` 与 `btnParseLink` 显示普通按钮，不显示高亮材质。
4. 重点页面在实际显示器、缩放比例和用户关注的视觉细节下无明显错位或误导。

用户完成后只更新本地人工清单中的状态与证据，不回写为已经完成的宿主或供应商验收。

## 版本控制动作

- 本地提交目标：只提交 `docs/handoffs/UI-GOVERNANCE-HANDOFF-20261012.md`。
- 不推送远端。
- 提交后以 `git status --short --branch` 和 `git show --stat --oneline HEAD` 复核，确认其他共享改动仍保持原状。
