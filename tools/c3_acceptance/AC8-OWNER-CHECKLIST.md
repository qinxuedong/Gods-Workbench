# C3-AC8 真实 RunningHub 验收清单（交秦先生本人执行）

依据：CUT-20261013-C3 修订「秦先生决定 2026-10-13 10:38」——AC8 由秦先生本人在本机出证，团队一律不碰 key。

## 1. 绑定版本

- 判定对象 SHA：`<合并后在本清单第 2 节命令所在 HEAD 上执行 git rev-parse HEAD 填入>`（先合并 C3 分支再验收；不要在旧 HEAD 上出证）
- 本机仓库：`/workspace/Gods-Workbench`（或当前共用副本），Python：`/workspace/.gw-venv/bin/python`

## 2. 配置 key（只进本机环境变量，不写进任何文件）

```bash
export GW_RUNNINGHUB_API_KEY='<你的 RunningHub 付费 apiKey>'   # 只在本机终端会话里设置
```

- key 不写进仓库文件、日志、结果 JSON 或 PR；脚本只读取环境变量，值不会被打印或落盘。
- 结果 JSON 里只会出现变量名 `GW_RUNNINGHUB_API_KEY`，不会出现值。

## 3. 运行验收命令

```bash
cd <本机仓库根目录>
/workspace/.gw-venv/bin/python tools/c3_acceptance/ac8_runninghub_real.py \
  --out .local/qa/C3-evidence/AC8/ac8-result.json \
  --workflow-id <RunningHub 工作流数字ID> \
  --i-accept-cost
```

- `--workflow-id`：在 RunningHub 打开要验收的工作流，地址栏 `/workflow/<数字ID>` 里的数字（10-25 位）。
- `--i-accept-cost`：确认本次真实任务会消耗付费额度；不加会被拒绝（退出码 2，BLOCKED）。
- 未设置 key 也会被拒绝（BLOCKED）；脚本不会联网、不会启动应用。
- 可选：`--timeout-seconds`（默认 1800）、`--poll-interval`（默认 10）、`--domain`（默认 www.runninghub.cn）。
- 退出码：0 = PASS，1 = FAILED，2 = BLOCKED。

## 3b. Windows PowerShell 版本

本机为 Windows 时，用下面的 PowerShell 命令替代第 2、3 节的 bash 命令（key 配置 + 运行验收）；第 4、5、6 节的判定标准、证据归档与预算提示不变。

### 配置 key（只进本机会话环境变量，不写进任何文件）

```powershell
$env:GW_RUNNINGHUB_API_KEY = '<你的 RunningHub 付费 apiKey>'   # 只在本机 PowerShell 会话里设置
```

- key 只对当前 PowerShell 会话生效，不写进仓库文件、日志、结果 JSON 或 PR；脚本只读取环境变量，值不会被打印或落盘（同第 2 节）。
- 验收结束后清掉会话里的 key：`Remove-Item Env:GW_RUNNINGHUB_API_KEY`。

### 激活本地虚拟环境（或直接调用其 python）

```powershell
.\.venv\Scripts\Activate.ps1   # 激活本机仓库根目录下的 .venv
```

- 若不想激活（或 `Activate.ps1` 受执行策略限制），可直接调用 `.\.venv\Scripts\python.exe`，效果相同；下面的运行命令即采用此写法。

### 运行验收命令

```powershell
cd <本机仓库根目录>
.\.venv\Scripts\python.exe tools\c3_acceptance\ac8_runninghub_real.py `
  --out .local\qa\C3-evidence\AC8\ac8-result.json `
  --workflow-id <RunningHub 工作流数字ID> `
  --i-accept-cost
```

- 多行时用 PowerShell 反引号 `` ` `` 续行（反引号之后必须直接换行，不能有空格）；也可写成单行：
  `.\.venv\Scripts\python.exe tools\c3_acceptance\ac8_runninghub_real.py --out .local\qa\C3-evidence\AC8\ac8-result.json --workflow-id <数字ID> --i-accept-cost`
- Windows 路径分隔符：证据目录写作 `.local\qa\C3-evidence\AC8\`（与第 5 节 `.local/qa/C3-evidence/AC8/` 等价）。
- 退出码用 `$LASTEXITCODE` 查看（运行后直接输入 `$LASTEXITCODE`）：0 = PASS，1 = FAILED，2 = BLOCKED（含义同第 3 节）。
- `--workflow-id`、`--i-accept-cost` 及可选参数（`--timeout-seconds`、`--poll-interval`、`--domain`）含义同第 3 节。

## 4. 判定 PASS 的标准

1. 命令退出码 0，终端输出 `PASS: 真实任务 <job_id> 完成；统一资产 <asset_id> 已登记且可回跳。`
2. `.local/qa/C3-evidence/AC8/ac8-result.json` 中 `"result": "PASS"`，且 `asset_verification` 三项均成立：
   - `task_id_matches: true`（资产 task_id = job_id）
   - `workflow_id_matches: true`（metadata.workflow_id 指向导入的工作流）
   - `content_http_status: 200`（资产内容可读回）
3. 截图（AC8 规定证据类型）：运行结束后在本机浏览器打开神工坊资产库页面，能看到这条任务产出的资产（或统一资产注册表条目），截图保存到证据目录。

## 5. 证据归档

全部放进 `.local/qa/C3-evidence/AC8/`：

- `ac8-result.json`（脚本自动写出，含 SHA 请在第 1 节手工补记）
- 资产库/统一资产页面的截图（PNG，文件名建议 `asset-library.png`）
- 如实登记：AC8 在运行成功前一律 BLOCKED，不以替身测试（pytest 全绿）代替真实验收。

## 6. 预算提示

- 本次验收只跑一条真实 RH 任务（一次上传 + 一次建任务 + 轮询查询 + 一次内容回读），消耗额度以该工作流单次运行为准；脚本不会重试、不会重复提交（幂等键 + collect 门禁）。
- 任务轮询超时不会伪造终态，也不会再扣费；超时后**不要重跑本脚本**（重跑会提交一条新的付费任务），应改为：到 RunningHub 控制台查看该任务的状态与产出，或从 `ac8-result.json` 记录 `job_id`，并将该任务按 FAILED/超时上报团队。
- 预算上限由秦先生自行设定（人工门 G2）；超出预算即停，不接受「多跑几次更稳」。
