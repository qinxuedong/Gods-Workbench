# P8-A1 前后端 API 缺口基线更新

## 2026-11-02：QIN-514 / GitHub #537

QIN-514 在资产设置中接入旧 JPEG 无损转码工具。前端调用与后端实现均新增以下路径，已加入 `tests/contracts/projects/test_phase8_frontend_backend_api_gap.py` 的 `KNOWN_IMPLEMENTED`：

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/assets/transcode-jpeg-lossless/scan` | 扫描当前主体可见的待转码 JPEG 与工具状态 |
| POST | `/api/assets/transcode-jpeg-lossless` | 创建异步无损转码任务 |
| GET | `/api/assets/transcode-jpeg-lossless/jobs/{p}` | 按稳定 job ID 轮询任务 |
| POST | `/api/assets/transcode-jpeg-lossless/jobs/{p}/cancel` | 请求在当前文件完成后安全取消 |

这些路径是本票有意新增的已实现接口，不属于未实现路径集合。`KNOWN_UNIMPLEMENTED` 与无静态前端调用的契约基线不变。

### 验证

- 首次 CI 在前端路径全集与已实现路径集合两项断言中发现上述新增路径未登记。
- 本地单独复跑确认是基线未更新，不是运行时或时序偶发。
- 基线更新后的窄测：`python -m pytest tests/contracts/projects/test_phase8_frontend_backend_api_gap.py -v`，7 passed。
- 完整 CI 将随 QIN-514 更新提交重新运行；本报告记录基线变更，不提前宣称全量门禁通过。

## 2026-11-05：QIN-553 / GitHub #618

QIN-553 新增预置 LUT 包的幂等登记能力。后端实现与前端 LUT 库入口各新增以下路径，已同步加入 `tests/contracts/projects/test_phase8_frontend_backend_api_gap.py` 的 `KNOWN_IMPLEMENTED` 与 `KNOWN_BACKEND_PATHS`：

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/asset-registry/builtin-luts/ensure` | 首次进入 LUT 库时幂等登记内置 LUT 资产（重复调用不重复登记，支持隐藏条目恢复显示） |

该路径是本票有意新增的已实现接口，不属于未实现路径集合。其余基线不变。

### 验证

- 本地首跑守卫在两个集合断言中发现上述新增路径未登记；登记后 `python -m pytest tests/contracts/projects/test_phase8_frontend_backend_api_gap.py -v`，7 passed。
- 完整 CI 将随 QIN-553 提交重新运行；本报告记录基线变更，不提前宣称全量门禁通过。

## 2026-11-20：QIN-630 / GitHub #765

QIN-630 为受信本机启动新增以下已实现接口；该路径已登记在 `tests/contracts/projects/test_phase8_frontend_backend_api_gap.py` 的 `KNOWN_IMPLEMENTED` 与 `KNOWN_BACKEND_PATHS`：

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/asset-auth/local/trusted-bootstrap` | 使用有效一次性本机票据原子建立或复用本机所有者并兑换本机会话；成功返回 200 与 HttpOnly 会话 Cookie。 |

既有 `GET /api/asset-auth/status` 在本地账户模式的响应增加只读 `trusted_local` 摘要，含 `enabled`、`established`、`needs_manual_claim` 布尔值与 `mode`；不泄露设备密钥、票据、路径或账户名。此为既有路径的响应增量，不新增路径基线。

### 验证

- 补充接口事实后运行 `python -m pytest tests/contracts/projects/test_phase8_frontend_backend_api_gap.py -q`，7 passed。冻结的 `KNOWN_*` 集合、断言与未实现路径集合未改。

## 2026-11-26：QIN-656 / GitHub #818

QIN-656 新增镜头视觉向量设置、状态与异步补跑接口。经 PM 批准，仅将以下三个已实现路径登记到 `tests/contracts/projects/test_phase8_frontend_backend_api_gap.py` 的 `KNOWN_IMPLEMENTED` 与 `KNOWN_BACKEND_PATHS`：

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/asset-registry/shot-vectors/jobs` | 为资源根异步补跑镜头视觉向量任务 |
| POST | `/api/asset-registry/shot-vectors/settings` | 保存镜头视觉向量开关 |
| GET | `/api/asset-registry/shot-vectors/status` | 读取本地镜头视觉向量依赖与状态 |

除此三项外，不修改或放宽其他端点、断言或基线规则；`KNOWN_UNIMPLEMENTED` 保持不变。

### 验证

- `python -m pytest tests/contracts/projects/test_phase8_frontend_backend_api_gap.py -q`：7 passed。
- `python -m pytest tests/contracts/assets/test_mvp1_t1_shot_index.py tests/contracts/assets/test_mvp1_t6_shot_search.py -q`：35 passed。
- `node --test tests/frontend/qin-631-native-select-pilot.test.mjs tests/frontend/mvp1-t6-omnibar-asset.test.mjs`：27 passed。
- `ruff check tests/contracts/projects/test_phase8_frontend_backend_api_gap.py`、`node --check web/js/modules/asset-manager.js` 与 `git diff --check`：通过。
- GitHub CI 将随 QIN-656 新提交重新运行；本报告不提前宣称全量门禁通过。

## 2026-11-27：QIN-658 / GitHub #820

QIN-658 增加本机 ASR 状态、设置与资产任务队列入口。经 PM 在 issue #820 确认，仅将以下三个已实现路径登记到 `tests/contracts/projects/test_phase8_frontend_backend_api_gap.py` 的 `KNOWN_IMPLEMENTED` 与 `KNOWN_BACKEND_PATHS`：

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/asset-registry/asr/jobs` | 对资源根或单视频提交异步台词转写任务 |
| POST | `/api/asset-registry/asr/settings` | 保存可选 ASR 引擎、模型与开关 |
| GET | `/api/asset-registry/asr/status` | 读取本机可选依赖、模型与 ffmpeg 就绪状态 |

除此三项外，不修改其他端点、断言或基线规则；`KNOWN_UNIMPLEMENTED` 保持不变。

### 验证

- `python -m pytest tests/contracts/assets/ -k "asr or transcript or search" -q`：41 passed。
- `python -m pytest tests/contracts/projects/test_phase8_frontend_backend_api_gap.py -q`：7 passed。
- `node --test tests/frontend/*.test.mjs`：1,206 passed；`python -m pytest tests/hygiene -q`：113 passed；`ruff check` 覆盖本票改动 Python 文件：通过。
- GitHub CI 与 UI 浏览器截图验收仍待完成；不将本地结果记作 CI 或浏览器通过。
