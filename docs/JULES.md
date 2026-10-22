# docs/JULES.md — Gods-Workbench（神工坊）给 Jules 的工作约定

> 本文件写给云端编码代理（Google Jules）。任务上下文只来自 GitHub Issue 里的「任务包」（task pack）；本文件是通用规则，与任务包冲突时以**更严格的那一条**为准。

## 1. 项目简介
以资产为中心的本地优先创作与生产工作台（asset-centric production workbench）。
- `gw/`：Python 3.11 后端（FastAPI / uvicorn / pydantic），入口 `run.py`
- `web/`：Web 前端（静态资源 + JS）
- `tests/`：pytest 测试，子目录 `api/ canvas/ contracts/ media/ runtime/ hygiene/ e2e/ frontend/ deployment/ benchmarks/ fixtures/`
- `docs/contracts/`：接口目录与契约（改接口前先读对应 catalog）
- `clients/desktop/`：Tauri 桌面端（Rust，Windows CI 构建；Jules 一般不碰）
- `deploy/`：Docker / NAS 部署文件（**禁止改动或执行部署**，除非任务包明确授权修改文件）

## 2. 环境准备（与 `.github/workflows/ci.yml` 一致）
```bash
python -m pip install --upgrade pip
python -m pip install --require-hashes -r requirements.lock.hashes
python -m playwright install --with-deps chromium
```
不要修改 `requirements.lock` / `requirements.lock.hashes`（CI 校验两锁一致），除非任务包授权。

## 3. 测试命令
先跑与改动相关的定向测试，通过后再跑全量：
```bash
# 测试中需要配置闸门值，用任意占位值即可，绝不填真实 key
export GW_RUNNINGHUB_API_KEY=dummy-for-tests

# 1) 定向
python -m pytest tests/<子目录>/<test_file>.py -v -p no:cacheprovider
python -m pytest tests/<...> -k "<关键字>" -v

# 2) 全量（CI 原命令）
python -m pytest -v --no-header -p no:cacheprovider

# 3) 前端契约回归（CI 原命令，需 Node）
node --test tests/frontend/agent-tasks.test.mjs tests/frontend/c16-t3-pwa-installable.test.mjs
```
Lint：仓库有 `ruff.toml`（py311，规则 E4/E7/E9/F/I/UP/B/ASYNC/RET/SIM/S），但 **CI 当前不跑 ruff**。对你改动的文件执行：
```bash
python -m pip install ruff   # 不在锁文件里，仅本地使用
ruff check <changed files>
```
不要对全仓库做 `ruff --fix` 或格式化。

CI 另有二进制白名单扫描：**不得提交** 图片/字体/音视频/压缩包/可执行/PDF 等二进制文件。

## 4. 硬性规则
1. 从**最新 `master`** 建分支；分支名 `jules/qin-<编号>-<slug>`，如 `jules/qin-212-asset-search-filter`。
2. **禁止**直接 push 到 `master`；**禁止 rebase**（需要同步时 `git merge origin/master`）；**禁止 force-push**；**禁止合并任何 PR**。
3. 只修改任务包中**授权的文件**。如需改动其他文件：停止，在 Issue/PR 上评论说明原因与文件清单，等待授权。
4. **测试先行**：先写/改失败的测试，再写实现，最后保证定向 + 全量测试通过。
5. **不得泄露或硬编码任何密钥**。RunningHub key 只能通过环境变量 `GW_RUNNINGHUB_API_KEY` 读取；测试中用占位值或 mock，不调用真实供应商。
6. **不部署、不碰生产**：不执行 `deploy/` 下脚本、docker compose、任何远程环境操作。
7. 不读写 `.local/`（它不在仓库中）；所需上下文全部来自 Issue 任务包。
8. 不依赖 Windows / NAS / 真实硬件验收。此类验收项（AC）标注为「**待本地实机验收**」，**绝不声称已完成**。
9. 不改 CI workflow（`.github/workflows/`），除非任务包明确授权。

## 5. 任务来源与状态
- 任务由 PM 通过 Jules CLI 派发（任务包写明 GitHub Issue、Linear 票号、授权文件和 AC），或 Issue 带 `jules` 标签触发。**没有派发、也没有该标签的 Issue 一律不处理。**
- 状态以 GitHub 为准，**标签由 PM/审查者维护，Jules 不改标签**：

| 状态 | GitHub 表达 | 谁来维护 |
|---|---|---|
| 已派给 Jules | Issue 标签 `jules` 或 CLI 派发 | PM |
| 执行中 | Jules 在 Issue 评论 `状态：执行中（执行人: jules）` | Jules |
| 等待审查 | PR 已开（Draft 视为未完成） | Jules |
| 需要返工 | PR review 评论（Request changes） | 审查者 / PM |
| 已验收 | 审查通过，等待人工合并 | 审查者 / PM |
| 已完成 | PR 由人合并，Issue 关闭 | 人工 |

- Jules 在 Issue 或 PR 评论里写明状态，并注明 `执行人: jules`。

## 6. PR 约定
- 标题以 Linear 工单号开头：`QIN-xxx: <简述>`
- 正文必须包含：
  - 关联 GitHub Issue（`Refs #n`；仅在任务包明确要求时用 `Closes #n`）和 Linear 工单链接
  - 改动文件清单（应全部在授权范围内）
  - 测试结果：执行过的定向与全量命令及通过/失败数
  - 范围外发现（out-of-scope findings）：只记录，不顺手修
  - 「待本地实机验收」的 AC 列表（如有）
  - `执行人: jules`
- 每条 Issue / PR 评论都要带 `执行人: jules`。

PR 正文模板：
```markdown
QIN-xxx | Issue: #n | Linear: <link>

## 改动文件
- path/a.py
## 测试
- `python -m pytest tests/... -v` → N passed
- `python -m pytest -v --no-header -p no:cacheprovider` → N passed
## 待本地实机验收
- （无 / 列出）
## 范围外发现
- （无 / 列出）

执行人: jules
```

## 7. 返工
- 针对 PR review 意见，在**同一分支追加新提交**，不开新 PR，不 rebase / force-push。
- 逐条回复 review 意见（已修改 / 不修改的理由），附测试结果，并带 `执行人: jules`。

## 8. 并行任务
- 每个任务只改自己授权的文件。
- 如发现与另一个打开中的 jules PR 改动同一文件或存在冲突：停止，在本 Issue/PR 评论说明重叠的 PR 与文件，等待 PM 决定。

## 9. 完成前自检
- [ ] 分支名符合 `jules/qin-<num>-<slug>`，基于最新 master
- [ ] 只改了授权文件，无二进制、无密钥
- [ ] 定向 + 全量 pytest 通过；前端改动跑过 node 契约测试
- [ ] PR 标题 / 正文 / `执行人: jules` 齐全；状态评论已写
