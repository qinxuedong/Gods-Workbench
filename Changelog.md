# 更新日志 (Changelog)

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 格式，版本演进遵循语义化版本规范。

---

## [0.0.1-alpha] - 2026-10-07

### 核心里程碑：首个正式开源 Alpha 版本发布 (GW2026-v0.0.1-alpha)

本版本标志着神工坊（Gods' Workbench）完成核心资产与智能画布基础治理，正式采用 Apache-2.0 许可证对外开源发布，解除基线冻结状态。

#### 开源合规与许可治理 (Open Source & Licensing)
- **正式开源授权**：主代码正式采用 **Apache License 2.0** 协议，在仓库根目录建立完整 [`LICENSE`](LICENSE) 文件；
- **第三方通知与分层声明**：在根目录建立 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)，分层声明 Python 后端依赖、前端厂商库（`web/vendor/`）以及提示词注册表（`web/prompt-registry/`）的开源许可证与版权信息；
- **思源黑体字节级哈希核验**：对 `web/vendor/fonts/` 中的 3 个思源黑体 OTF 文件（Bold、Medium、Normal）完成与 Adobe 官方发布包（v2.005R）的逐字节 SHA-256 核验，完全一致；
- **前端依赖版权补全**：在 `web/vendor/LICENSES.md` 补全 `tailwindcss-cdn.js` 及其传递依赖（`caniuse-lite` CC-BY-4.0 等）的版权声明，将发布状态更新为 `RELEASED`；
- **解除冻结基线**：在 `gw/core/platform.py` 与元数据夹具中将 `release_authorized` 正式标记为 `True`，全面解除 `NOT AUTHORIZED FOR PUBLIC DISTRIBUTION` 状态。

#### 架构与工程规范 (Architecture & Hygiene)
- **作业宪章同步**：更新 `AGENTS.md` 6项核心规范，确立已发布状态与三态运行隔离准则；
- **多层级本地文档中心**：重构 `.local/docs/` 本地知识体系（含 `user_doc/`、`notes/`、`archive/`），编写文档中心索引 `README.md`，实现内部文档与外部代码仓库的安全隔离；
- **调试探针与远端轻量化**：撤回历史临时探针，规范本地探针专属目录 `.local/probes/`，固化忽略规则；
- **测试门禁绿灯保障**：通过洁净室准入测试、代码卫生门禁、跨域契约测试与平台运行时测试全量回归（400+ 测试用例 100% 绿灯）。

#### 用户界面与工作流 (UI & Workflows)
- **黑金硬件设计系统**：深化黑金硬件风格视觉一致性，对齐设置视图、目录挂载弹窗交互与页面边距；
- **工作流领域治理**：完成工作流模块核心契约、执行引擎与前端控制器的工程闭环。

---

## [0.0.0-dev] - 2026-09-29

### 初始基线建立 (Initial Baseline)

- **核心资产系统**：建立基于 FastAPI 与统一数据语义的本地优先资产管理中心；
- **智能画布核心**：集成分镜拓扑编排、节点工作流与稳定任务调度；
- **数据不变量与契约**：确立核心实体 ID 规范（`project_id`、`canvas_id`、`asset_id` 等），引入 CAS 乐观并发控制；
- **三态运行隔离**：确立测试态（`tmp_path`）、开发沙盒态（`.local/runtime-dev/`）与生产态（`%LOCALAPPDATA%\GodsWorkbench\`）三态存储与 Cookie 隔离架构；
- **洁净室准入门禁**：确立严苛的代码防污染门禁、二进制白名单与黄金测试夹具。
