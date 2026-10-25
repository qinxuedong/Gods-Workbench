# CI 跳过用例与未覆盖能力清单

> 本文记录一次可复现的测试环境快照。`pytest` 的 skip 数量会随操作系统、可选依赖、服务配置和显式运行开关变化；不把旧报告中的 84 项当作本次结果。

## 实测基线

- 日期：2026-10-25
- 代码 SHA：`597e165342ef3fbfe370617e19e0303cdb54e59d`（干净 `origin/master` 基线）
- 环境：Windows 11 Pro 10.0.26300、Python 3.11.9、pytest 9.1.1
- 命令：设置 `GW_RUNNINGHUB_API_KEY=dummy-for-tests` 后运行 `python -m pytest -rs -q -p no:cacheprovider`
- 结果：`3265 passed, 67 skipped, 24 warnings`，退出码 0，耗时 1616.47 秒；pytest 报告的 skip 位置计数合计 67。
- 完整基线输出：`.local/qa/QIN-376/master-current-pytest-20261025.log`（私有证据，不提交）
- 同一 SHA 的 warnings 基线为 24 条；QIN-376 候选版本按用户要求必须做到新增告警为 0。

本次有 67 个 skip，与工单来源中记录的 84 个不同，差 17 个。以下按干净 master 的完整 `pytest -rs` 输出逐项归类；参数化的多个 case 以 pytest 报告中的计数列出。

## Skip 清单

| 数量 | 测试与能力 | 本次跳过原因 | 是否应纳入常规 CI 与建议 |
| ---: | --- | --- | --- |
| 1 | `tests/contracts/observability/test_phase12_probe.py`：Phase 12 本机探针执行 | 模块级开关 `GW_RUN_LOCAL_PHASE12_PROBE_TESTS=1` 未启用；探针脚本位于本地私有目录。 | 否。保持默认关闭；仅在准备好的本机环境显式运行。CI 已运行该模块中其余 12 项探针契约测试。 |
| 1 | `tests/contracts/assets/test_phase12_output_boundary.py::test_output_symlink_cannot_reach_internal_state`：符号链接输出边界 | 当前 Windows 账户创建目录符号链接被系统拒绝（WinError 1314）。 | 应有等价覆盖。Linux CI 可覆盖符号链接语义；Windows 已有 Junction 边界测试，本次正常运行。无需提升开发机权限。 |
| 3 | `tests/contracts/auth/test_phase9g_real_op_interop.py`：真实第三方 OIDC OP 登录、PKCE 篡改与 nonce 校验 | 未安装 `oidc-provider` 包；`GW_OIDC_PROVIDER_MODULE_DIR` 未配置。 | 不应阻塞常规 CI。若需要真实互操作验收，建立独立的依赖锁定集成任务；常规 CI 保持模拟/契约覆盖和默认关闭门禁。 |
| 4 | `tests/contracts/auth/test_phase9i_real_idp_wiring.py`：真实 IdP discovery、endpoint 信任、PKCE 与运行时接线 | 未配置 `GW_REAL_IDP_ISSUER` 和 `GW_REAL_IDP_ENDPOINT_HOSTS`。 | 不应在普通 PR CI 注入真实 IdP。使用获准的临时 IdP 环境另行验收；常规 CI 保留配置校验与 fail-closed 契约。 |
| 45 | `tests/contracts/projects/test_c5_permission_matrix.py`：生成的 `canvas_list_all` 权限矩阵（`test_cell_canvas_list_all__{role}__{state}`） | 产品决策为 Permanently N/A：`GET /api/canvases` 必须提供 `project_id`，没有不带项目范围的全局画布列表端点。45 个矩阵 cell 在同一 skip 位置汇总。 | 否。这不是环境缺口或待补功能；不要把 45 项写成未测能力。后续若调整测试，改为覆盖已支持的项目范围列表，再移除永久 N/A cell。 |
| 2 | `tests/contracts/settings/test_provider_persistence.py::test_linked_state_or_temp_file_is_rejected_before_access`：配置文件及临时文件符号链接保护 | 当前平台/账户拒绝创建目录符号链接。 | 应有等价覆盖。由支持符号链接的 Linux CI 验证；Windows 权限不足时保留明确 skip。 |
| 1 | `tests/contracts/test_comfy_lifecycle.py::test_os_pidfd_identity_change_never_signals`：Linux pidfd 进程身份保护 | 测试明确仅验证 Linux `pidfd`；本次运行在 Windows。 | 是，应在 Linux CI lane 执行；Windows 不重复模拟 Linux 系统调用。 |
| 1 | `tests/e2e/test_c8_single_shot_review_delivery_browser.py::test_c8_real_single_shot_review_delivery_browser`：真实单镜头生成、审阅与交付 | `GW_C8_REAL_E2E=1` 未启用；真实 RunningHub 工作流需要人工配置。 | 否，不放入常规 PR CI。保留安全门禁测试；在获准的 RunningHub 环境另行运行。 |
| 1 | `tests/e2e/test_c9_r2_closure_browser.py::test_c9_real_single_shot_review_delivery_browser`：真实闭环与审阅交付 | `GW_C9_REAL_E2E=1` 未启用；真实 RunningHub 工作流需要人工配置。 | 否，不放入常规 PR CI。保留安全门禁测试；在获准的 RunningHub 环境另行运行。 |
| 1 | `tests/e2e/test_c10_asset_scale_browser.py::test_c10_real_asset_browser_measurement`：真实资产规模浏览器测量 | `GW_C10_REAL_E2E=1` 未启用。 | 否，不放入常规 PR CI。保留确定性 fixture 与契约覆盖；真实规模测量按 C10 环境单独执行。 |
| 1 | `tests/e2e/test_c11_r3_stability_browser.py::test_c11_r3_stability_real_browser_observation`：长时间路由往返与真实任务轮询稳定性 | `GW_C11_REAL_E2E=1` 未启用。 | 否，不作为每次 PR 的门禁；在稳定性测试环境显式运行并保留运行证据。 |
| 1 | `tests/e2e/test_c12_timeline_rough_cut.py::test_c12_timeline_rough_cut_real_browser_refresh_and_restart`：真实浏览器时间线刷新与重启回读 | `GW_C12_REAL_E2E=1` 未启用。 | 否，不放入常规 PR CI。继续运行本机 fixture/契约测试；真实浏览器验收单独执行。 |
| 1 | `tests/e2e/test_c13_animatic_radar_browser.py::test_c13_animatic_radar_real_browser_evidence`：真实动画样片与雷达浏览器证据 | `GW_C13_REAL_E2E=1` 未启用。 | 否，不放入常规 PR CI。真实浏览器证据由专用环境单独收集。 |
| 1 | `tests/e2e/test_c14_i2v_generation_browser.py::test_c14_i2v_ten_shot_generation_and_multitake_browser_closeout`：I2V 十镜头生成与多 Take 浏览器验收 | `GW_C14_REAL_E2E=1` 未启用。 | 否，不放入常规 PR CI；真实生成涉及供应商与外部资源，按专用环境验收。 |
| 1 | `tests/e2e/test_c15_full_pipeline_browser.py::test_c15_local_master_render_plays_in_chrome`：本地 FFmpeg 母带与 Chrome 解码 | `GW_C15_LOCAL_RENDER_E2E=1` 未启用；该任务需要本地 FFmpeg 与 Chrome。 | 不应成为普通 PR CI 默认任务。若 CI 需要渲染覆盖，应建立具备固定 FFmpeg/Chrome 版本的专用 lane。 |
| 1 | `tests/e2e/test_c16_multi_topology_browser.py::test_c16_real_nas_read_only_preflight`：飞牛 NAS 只读预检 | `GW_C16_REAL_E2E=1` 未启用；该测试要求完成部署并由秦先生明确通知后再运行。 | 否，绝不自动放进常规 CI。仅在 NAS 部署完成并履行测试前报告后执行；本次没有访问 `192.168.0.199`。 |
| 1 | `tests/runtime/test_t5_runtime_paths.py::test_existing_symlink_in_any_root_is_rejected`：运行时目录根拒绝符号链接 | 当前 Windows 账户未获准创建目录符号链接。 | 应由支持符号链接的平台 CI 覆盖；Windows Junction 行为单独测试，不把权限性 skip 当成通过。 |

## 结论与补齐建议

- 67 个 skip 中，45 个是产品明确不支持的全局画布列表权限矩阵 cell；其余 22 个由本机探针门禁、Windows 符号链接权限、Linux 专属 pidfd、缺失 OIDC 包/IdP 配置或真实浏览器/服务环境门禁导致。
- 常规 CI 应覆盖确定性的权限契约、模拟身份提供方、路径保护和本地 fixture；为符号链接及 pidfd 保留合适的 Linux runner。Windows Junction 用例在本次运行中执行，没有列入 skip。
- OIDC 真实 OP、真实 IdP、RunningHub、长稳浏览器、FFmpeg/Chrome 与 NAS 验收需要单独准备环境，不应通过默认打开环境变量来“消除 skip”。C16 还必须先取得部署完成通知。
- 建议把永久 N/A 的 45 个矩阵 cell 从“环境跳过”报表中单独计类或从生成矩阵移除；否则会把不支持的 API 形状误报为 CI 未覆盖能力。
