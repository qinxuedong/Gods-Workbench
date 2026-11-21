# QIN-626 安装端验收

本目录提供可复跑的 Windows 安装端验收入口，覆盖：首装、本地优先启动、启动失败的可读原因与重试、深浅主题与 724/900/1440/1920 布局、远端连接失败关闭与重试、桌面冷/热启动、单实例锁与端口冲突、退出清理、连续覆盖升级、失败回退到紧邻的上一成功代、卸载保留数据。

脚本只使用真实的 NSIS 安装器、安装后的桌面程序、随包 Python 后端和系统端口/进程；不 mock 安装行为。安装壳的 UI 行为通过 WebView2 远程调试端口驱动真实页面（读取真实 DOM 与计算样式），远端场景使用真实局域网地址上的第二套内置后端和一个故意缺失缓存头/版本来源标记的本地假服务。每个场景把实际退出码、升级日志状态、代标记、PID、监听端口、HTTP 头、页面文案、主题 token、布局测量和桌面诊断结果写入证据文件。

## 前置条件

- Windows 10/11，Windows PowerShell 5.1 或更高版本。
- 当前登录会话能看到桌面窗口（用于检查主窗口和失败弹窗；不要求管理员权限）。
- `2077` 端口空闲，且没有其他 `gods-workbench-desktop.exe` 实例。
- 机器具有一个可绑定的私有 IPv4 局域网地址（`10/8`、`172.16/12`、`192.168/16`）；远端场景会在该地址的临时端口上启动随包后端和假服务。地址在证据中替换为 `<LAN_IP>`。
- 远端真实后端以 `--host <LAN_IP>` 启动，并设置 `GW_ALLOWED_HOSTS=<LAN_IP>`，以满足工作台 Host 白名单；假服务不经过该中间件。
- WebView2 运行时可用；脚本通过 `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` 为验收实例开启临时 CDP 端口，只连接 `127.0.0.1`。
- 安装器构建自同一提交；构建时写入的 `product_version` 与 `git_commit` 必须和验收参数一致。
- 若机器上已有真实 `%LOCALAPPDATA%\GodsWorkbench`，必须用 `-LocalAppDataOverride` 指向一个空的隔离目录；脚本不会删除真实数据根。
- 若 `%LOCALAPPDATA%\ai.godsworkbench.desktop` 已有升级状态，首次运行需显式使用 `-Reset`，且该状态必须带有本脚本写入的验收标记；脚本拒绝清理无标记状态。

## 构建

先在仓库根目录构建载荷，再进入 `clients/desktop` 构建安装器（占位符请替换为实际路径与版本）：

```powershell
python clients/desktop/packaging/build_release.py `
  --version "<X.Y.Z>" `
  --build-id "<build-id>" `
  --output "<构建输出目录>" `
  --runtime-archive "<官方 CPython embeddable 压缩包>" `
  --wheelhouse "<已下载的 wheel 目录>" `
  --git-commit "<提交 SHA>"

Set-Location clients/desktop
cargo tauri build --config "<构建输出目录的绝对路径>\tauri.release.conf.json" --bundles nsis --ci -- --locked
```

安装器默认位于 `clients/desktop/target/release/bundle/nsis/`。用 `Get-FileHash` 记录 SHA-256，并将同一 SHA、版本和提交传给验收脚本。

## 运行

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  clients/desktop/packaging/acceptance/run_install_acceptance.ps1 `
  -Installer "<安装器路径>" `
  -ExpectedSha256 "<64 位 SHA-256>" `
  -ExpectedVersion "<构建的 product_version>" `
  -ExpectedCommit "<构建使用的提交 SHA>" `
  -LocalAppDataOverride "<隔离的空 LOCALAPPDATA 目录>" `
  -OutputDir "<证据输出目录>" `
  -Reset
```

可选项：

- `-InstallDir "<无空格目录>"`：默认在 `%PUBLIC%` 下创建唯一目录；NSIS `/D` 不支持带空格的路径。
- `-KeepInstalled`：卸载验证后重新安装，便于人工检查。
- `-KeepState`：保留隔离数据根、升级备份和客户端配置；下次运行需 `-Reset`。默认在写完证据后清理本次验收创建的隔离数据根、远端独立数据目录（`gw-qin626-accept-remote-*`）、升级日志/备份和客户端配置，真实 `%LOCALAPPDATA%\GodsWorkbench` 数据根始终不动。

脚本退出码：`0` 表示全部检查通过；`1` 表示存在检查失败或未处理异常。无论结果如何，都会写 `<OutputDir>\acceptance.json` 与 `<OutputDir>\acceptance.md`（路径已替换为占位符）。

## 场景与证据

1. **首装与本地优先启动**：安装器退出码、`journal.txt` 为 `verified`、桌面主窗口可见、`/healthz` 与 `/openapi.json`（`<product_version>+gw.<build-id>`）版本、`server.json` 已写入、后端私有 Python 进程、2077 监听者、实例锁 PID。验收前不写任何服务器偏好；首启必须由安装壳自动启动内置后端，并在 CDP 中观察到页面从安装壳进入 `http://127.0.0.1:2077/static/pages/index.html`，`/healthz` 与 `/openapi.json` 均带 `no-store`/`no-cache`/`Expires: 0`。
2. **热启动**：再次启动桌面，第二个进程退出码 `0`，桌面/后端/监听者仍各一个，锁 PID 不变。
3. **冷启动与退出清理**：退出后桌面和后端进程消失、锁文件删除、2077 释放。
4. **陈旧锁恢复**：写入离线 PID 的遗留锁，桌面应成为新主实例并替换锁。
5. **并发陈旧锁恢复**：两个进程同时启动，只有一个主实例；另一个以 `0` 退出，后端与端口不重复。
6. **活实例无响应**：锁指向一个只接受连接但不应答的本机进程；桌面必须显示明确错误弹窗并以 `1` 退出，保留原锁，不启动后端。
7. **本机启动失败与重试（真实页面）**：用外部进程占用 2077 后启动桌面；在 CDP 中必须看到 `本机端口被占用`、包含 `2077` 的原因、下一步动作和可见的「重试本机启动」按钮，且停留在首页。随后读取真实计算样式验证深色 token（`#08090c`/`#111216`/`#e8e8ea`/`#dfc384`）与浅色 token（`rgb(241, 242, 245)`/`rgb(255, 255, 255)`/`rgb(17, 20, 26)`），并验证主题选择写入 `localStorage`。用 CDP 设备度量依次测量 724/900/1440/1920：724 为单列、其余为三列，卡片宽度约 696/720/760/800，1920 字号为 15px，其余为 14px，四个宽度均无横向溢出；每个宽度及深/浅主题、失败态和远端入口另存 CDP 页面截图，不依赖窗口是否置顶。释放 2077 后点击「重试本机启动」，必须真实进入本机工作台并重新通过健康/版本/能力/`no-store` 校验。
8. **远端失败关闭与重试（真实页面）**：继续在失败首页打开「连接远端工作台」，先连接一个故意不返回 `no-store` 头、随后返回缺少 `+gw.` 版本标记的局域网假服务；页面必须分别显示 `远端服务版本无法可信验证`、包含 `no-store`/`+gw.` 的原因和可见的「重试连接」按钮，且不导航。假服务停止后，在同一地址端口启动随包真实后端（独立 `LOCALAPPDATA`），点击「重试连接」必须进入 `http://<LAN_IP>:<port>/static/pages/index.html`，目标地址不得携带 `gw_trusted` 或查询串，`server.json` 写入该远端地址，且不出现本机 2077 后端。
9. **连续覆盖升级**：G0 → 安装器 → 备份 G0、安装 G1；G1 → 安装器 → 备份 G1、安装 G2。每代用 `backend/run.py` 中的 `QIN626_ACCEPTANCE_GENERATION` 标记核验。
10. **失败升级回退**：为安装器进程设置无效的 `GW_BUNDLE_MANIFEST`，安装后自检失败；安装器退出码必须为 `2`，日志为 `rolled_back`，程序与备份都必须回到紧邻的 G2，`server.json`/`client.json` 仍在，随后桌面启动时 `/openapi.json` 版本一致。
11. **卸载保留数据**：静默卸载后程序目录删除；隔离数据根的 sentinel、Roaming `server.json`/`client.json` 和升级备份仍存在，端口和进程清理。证据写入后才清理本次验收状态。

`acceptance.json` 主要字段：

- `package`：安装器文件名、实际 SHA-256、期望 SHA/版本/提交。
- `installer`：每次安装的退出码、安装前后日志状态、备份代标记、安装代标记。
- `lifecycle`：各场景的桌面/后端 PID、监听 PID、锁 PID、健康状态、诊断错误、退出码。
- `ui`：首启/重试的导航地址、本机/远端失败文案、主题 token 与 `localStorage`、四个宽度的真实布局测量、远端目标地址与票据缺失检查、失败/主题/远端截图文件名；`*.page.png` 为 CDP 页面截图，`*.png` 为整屏截图。
- `remote_backend`：远端真实后端基地址、`/openapi.json` 版本与 `Cache-Control`（地址替换为 `<LAN_IP>`，独立数据目录替换为 `<REMOTE_LOCALAPPDATA>`）。
- `journal_final`、`backup_generation`、`installed_generation`、`data_sentinel`、`server_config_present`、`client_settings_present`。
- `self_check_tail`：失败自检日志尾部（本地路径已替换）。
- `checks`：逐项 `PASS/FAIL` 与失败详情。

## 与 QIN-630 的边界

本入口记录 `trusted.available` 与 `trusted.auto_login`，但不把受信一次性票据可用作为 QIN-626 的通过条件。QIN-630 后端合入并联合验收前，桌面不得宣称免登录完成；密码登录路径仍可验收。若受信票据可用，证据中会显示对应状态。
